# SPDX-License-Identifier: GPL-3.0-only
"""Reiter „Aufnehmen“ der Sample-Seite: aus jeder Quelle (FM-1 über USB, Line-in/USB-Mikrofon,
Ausgang des Rechners) einen Sound aufnehmen – mit Vorlauf, Auto-Start bei Signal, Stille
abschneiden, Normalisieren – und direkt in die Bibliothek legen."""
from __future__ import annotations

import re
import tempfile
import time
from pathlib import Path

import numpy as np
from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                             QLineEdit, QProgressBar, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from .. import recorder
from ..i18n import _, decimal
from .audio import LiveWave, Player, Waveform
from .common import confirm, fmt_secs, fs_error_text, icon, run_job, show_error
from .. import samples as S


def _unique(folder: Path, name: str) -> Path:
    safe = re.sub(r"[^\w\-. ]+", "_", name).strip() or _("Recording")
    p = folder / f"{safe}.wav"
    k = 2
    while p.exists():
        p = folder / f"{safe} ({k}).wav"
        k += 1
    return p


class SampleRecorder(QWidget):
    saved = pyqtSignal(str)

    def __init__(self, ctx, lib: S.Library, player: Player | None = None, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.lib = lib
        self.player = player or Player(self)
        self.sources: list[recorder.CaptureDevice] = []
        self.rec: recorder.Recording | None = None
        self.tail: recorder.TailReader | None = None
        self.ring: list[np.ndarray] = []         # Vorlauf (ohne laufende Aufnahme)
        self.take: list[np.ndarray] | None = None  # laufende Aufnahme
        self.result: np.ndarray | None = None
        self.rate = 44100
        self.started = 0.0
        self.tmpdir = Path(tempfile.mkdtemp(prefix="fm1c-samplerec-"))

        lay = QVBoxLayout(self)
        g = QGroupBox(_("Source"))
        f = QFormLayout(g)
        self.source = QComboBox()
        self.source.setToolTip(_("FM-1 via USB, an input of the computer (line-in, USB microphone) or the "
                                 "output of the computer (everything that is playing right now)"))
        b_find = QPushButton(icon("view-refresh"), _("Search"))
        b_find.clicked.connect(self.find_sources)
        row = QHBoxLayout()
        row.addWidget(self.source, 1)
        row.addWidget(b_find)
        f.addRow(_("Input:"), row)
        self.mono = QCheckBox(_("save as mono"))
        self.mono.setChecked(True)
        self.mono.setToolTip(_("The FM-1 plays samples in mono anyway"))
        f.addRow("", self.mono)
        lay.addWidget(g)

        g2 = QGroupBox(_("Record"))
        f2 = QFormLayout(g2)
        self.meters = [QProgressBar(), QProgressBar()]
        mrow = QVBoxLayout()
        for i, m in enumerate(self.meters):
            m.setRange(0, 100)
            m.setTextVisible(True)
            m.setFormat("L" if i == 0 else "R")
            m.setMaximumHeight(12)
            mrow.addWidget(m)
        f2.addRow(_("Level:"), mrow)
        self.live = LiveWave()
        f2.addRow(self.live)
        self.preroll = QDoubleSpinBox()
        self.preroll.setRange(0, 5)
        self.preroll.setSingleStep(0.1)
        self.preroll.setValue(0.5)
        self.preroll.setSuffix(" s")
        self.preroll.setToolTip(_("This much before pressing “Record” is included (while “Ready” is running)"))
        f2.addRow(_("Pre-roll:"), self.preroll)
        trow = QHBoxLayout()
        self.auto = QCheckBox(_("starts on signal above"))
        self.auto.setToolTip(_("Get ready, then recording starts by itself on the first sound"))
        self.thr = QSpinBox()
        self.thr.setRange(-60, -3)
        self.thr.setValue(-30)
        self.thr.setSuffix(" dB")
        trow.addWidget(self.auto)
        trow.addWidget(self.thr)
        trow.addStretch(1)
        f2.addRow(_("Auto start:"), trow)
        brow = QHBoxLayout()
        self.b_arm = QPushButton(icon("audio-input-microphone"), _("Ready (monitor)"))
        self.b_arm.setToolTip(_("Open the input and show the level without saving"))
        self.b_arm.clicked.connect(self.toggle_monitor)
        self.b_rec = QPushButton(icon("media-record"), _("Record"))
        self.b_rec.setProperty("variant", "primary")
        self.b_rec.clicked.connect(self.toggle_record)
        self.time = QLabel(decimal(0, 1) + " s")
        brow.addWidget(self.b_arm)
        brow.addWidget(self.b_rec)
        brow.addWidget(self.time)
        brow.addStretch(1)
        f2.addRow(brow)
        self.state = QLabel("")
        self.state.setProperty("role", "muted")
        self.state.setWordWrap(True)
        f2.addRow(self.state)
        lay.addWidget(g2)

        g3 = QGroupBox(_("Result"))
        f3 = QFormLayout(g3)
        self.wave = Waveform()
        self.wave.text = _("Nothing recorded yet – press “Record”, play, “Stop”.")
        f3.addRow(self.wave)
        orow = QHBoxLayout()
        self.trim = QCheckBox(_("Trim silence"))
        self.trim.setChecked(True)
        self.norm = QCheckBox(_("Normalize"))
        self.norm.setChecked(True)
        for c in (self.trim, self.norm):
            c.toggled.connect(self._reprocess)
            orow.addWidget(c)
        orow.addStretch(1)
        f3.addRow(orow)
        self.name = QLineEdit()
        self.name.setPlaceholderText(_("e.g. Snare kitchen pot"))
        f3.addRow(_("Name:"), self.name)
        self.tags = QLineEdit()
        self.tags.setPlaceholderText(_("Tags, separated by commas (e.g. snare, own)"))
        f3.addRow(_("Tags:"), self.tags)
        srow = QHBoxLayout()
        self.b_play = QPushButton(icon("media-playback-start"), _("Listen"))
        self.b_play.clicked.connect(self.play)
        self.b_save = QPushButton(icon("document-save"), _("Into the library"))
        self.b_save.setProperty("variant", "primary")
        self.b_save.setToolTip(_("Saves to ~/FM-1/Samples/Aufnahmen and adds it to the library"))
        self.b_save.clicked.connect(self.save)
        self.b_drop = QPushButton(icon("edit-delete"), _("Discard"))
        self.b_drop.clicked.connect(self.discard)
        for b in (self.b_play, self.b_save, self.b_drop):
            srow.addWidget(b)
        srow.addStretch(1)
        f3.addRow(srow)
        lay.addWidget(g3)
        lay.addStretch(1)

        self.timer = QTimer(self, interval=60)
        self.timer.timeout.connect(self._tick)
        self._raw: np.ndarray | None = None
        self._set_result_enabled(False)
        self.find_sources()

    # ------------------------------------------------------------------ Quellen
    def find_sources(self):
        """Quellen im Hintergrund suchen (pw-dump darf den GUI-Thread nicht aufhalten)."""
        if self.rec and self.rec.running or getattr(self, "_finding", False):
            return
        self._finding = True
        self.state.setText(_("Looking for inputs …"))
        port = self.ctx.port

        def done(sources):
            self._finding = False
            self._found(sources)

        def fail(m, tb):
            self._finding = False
            self._found([])
            self.state.setText(_("Could not look for inputs: {error}").format(error=m))
        run_job(self, lambda w: recorder.list_sources(port), on_done=done, on_fail=fail)

    def _found(self, sources):
        self.sources = sources
        self.source.clear()
        for d in self.sources:
            self.source.addItem(icon("audio-input-microphone" if d.kind != "monitor" else "audio-speakers"),
                                d.title, d.kind)
        ok = bool(self.sources)
        self.b_rec.setEnabled(ok)
        self.b_arm.setEnabled(ok)
        from .. import melodee16                     # Melodee: Aufnahme vor/nach MASTER (Tooltip am Pegel)
        info = getattr(self.ctx, "info", None)
        hint = melodee16.usb_record_hint(info.version if info else "")
        for m in self.meters:
            m.setToolTip(_("{hint} (applies to the source “FM-1 via USB”)").format(hint=hint) if hint else "")
        self.state.setText("" if ok else _("No recording input found. Connect the FM-1 via USB, plug in a USB microphone "
                                           "or use PipeWire (then the output of the computer works too)."))

    def select_kind(self, kind: str) -> bool:
        i = self.source.findData(kind)
        if i >= 0:
            self.source.setCurrentIndex(i)
        return i >= 0

    # ------------------------------------------------------------------ Ablauf
    @property
    def monitoring(self) -> bool:
        return bool(self.rec and self.rec.running)

    @property
    def recording(self) -> bool:
        return self.take is not None

    def start_monitor(self) -> bool:
        """Eingang öffnen (blockierend – für Tests und start_monitor_async)."""
        i = self.source.currentIndex()
        if not 0 <= i < len(self.sources):
            return False
        dev = self.sources[i]
        ch = 1 if 1 in dev.channels and dev.kind == "input" and 2 not in dev.channels else 2
        self.rate = 48000 if 48000 in dev.rates and 44100 not in dev.rates else 44100
        try:
            self.rec = recorder.Recording(dev, self.tmpdir, ch, self.rate, "monitor")
            self.rec.start()                   # wartet 0,6 s je Aufnahmeweg
        except Exception as e:  # noqa: BLE001
            self.rec = None
            show_error(self, _("Cannot open the input"), str(e))
            return False
        self._monitor_started(dev, ch)
        return True

    def _monitor_started(self, dev, ch: int):
        self.tail = recorder.TailReader(self.rec.path, ch)
        self.ring = []
        self.live.reset(self.rate)
        self.b_arm.setText(_("Ready – stop"))
        self.state.setText(_("Monitoring: {source} · starts on the first sound").format(source=dev.title) if self.auto.isChecked()
                           else _("Monitoring: {source}").format(source=dev.title))
        self.timer.start()

    def start_monitor_async(self, then=None):
        """wie start_monitor(), aber ohne den GUI-Thread aufzuhalten; then() läuft nach dem Öffnen."""
        i = self.source.currentIndex()
        if not 0 <= i < len(self.sources) or getattr(self, "_opening", False):
            return
        dev = self.sources[i]
        ch = 1 if 1 in dev.channels and dev.kind == "input" and 2 not in dev.channels else 2
        self.rate = 48000 if 48000 in dev.rates and 44100 not in dev.rates else 44100
        try:
            rec = recorder.Recording(dev, self.tmpdir, ch, self.rate, "monitor")
        except OSError as e:
            show_error(self, _("Cannot open the input"), str(e))
            return
        self._opening = True
        self.b_arm.setEnabled(False)
        self.b_rec.setEnabled(False)
        self.state.setText(_("Opening {source} …").format(source=dev.title))

        def done(_rec):
            self._opening = False
            self.b_arm.setEnabled(True)
            self.b_rec.setEnabled(True)
            self.rec = rec
            self._monitor_started(dev, ch)
            if then:
                then()

        def fail(m, tb):
            self._opening = False
            self.b_arm.setEnabled(True)
            self.b_rec.setEnabled(True)
            self.state.setText("")
            show_error(self, _("Cannot open the input"), m)
        run_job(self, lambda w: rec.start(), on_done=done, on_fail=fail)

    def toggle_monitor(self):
        if self.monitoring and not self.recording:
            self._stop_capture()
            self.state.setText("")
        elif not self.monitoring:
            self.start_monitor_async()

    def toggle_record(self):
        if self.recording:
            self.stop()
        elif self.monitoring:
            self.record()
        else:
            self.start_monitor_async(then=self.record)

    def record(self) -> bool:
        if not self.monitoring and not self.start_monitor():
            return False
        pre = int(self.preroll.value() * self.rate)
        ring = np.concatenate(self.ring) if self.ring else np.zeros((0, self.rec.channels))
        self.take = [ring[-pre:] if pre else ring[:0]]
        self.ring = []
        self.started = time.time()
        self.live.mark()
        self.b_rec.setText(_("Stop"))
        self.b_rec.setIcon(icon("media-playback-stop"))
        self.state.setText(_("Recording …"))
        return True

    def stop(self):
        if not self.recording:
            return
        self._tick()
        take = np.concatenate(self.take) if self.take else np.zeros((0, 2))
        self.take = None
        self._stop_capture()
        self.b_rec.setText(_("Record"))
        self.b_rec.setIcon(icon("media-record"))
        self._raw = take
        self._reprocess()
        if not self.name.text().strip():
            self.name.setText(time.strftime(_("Recording %Y-%m-%d %H-%M-%S")))

    def _stop_capture(self):
        self.timer.stop()
        if self.rec:
            try:
                self.rec.stop()
            except Exception:  # noqa: BLE001
                pass
            for p in self.tmpdir.glob("*.wav"):
                p.unlink(missing_ok=True)
        self.rec = None
        self.b_arm.setText(_("Ready (monitor)"))
        for m in self.meters:
            m.setValue(0)

    def _tick(self):
        if not self.tail:
            return
        if self.rec and not self.rec.running and self.monitoring is False and self.take is None:
            self.timer.stop()
        blk = self.tail.read()
        if len(blk):
            pk = np.abs(blk).max(axis=0)
            for i, m in enumerate(self.meters):
                v = float(pk[min(i, len(pk) - 1)])
                m.setValue(int(100 * max(0.0, 1 + 20 * np.log10(max(v, 1e-6)) / 60)))
            self.live.push(blk)
            if self.take is not None:
                self.take.append(blk)
            else:
                self.ring.append(blk)
                keep = int(5 * self.rate)
                total = sum(len(b) for b in self.ring)
                while self.ring and total - len(self.ring[0]) >= keep:
                    total -= len(self.ring.pop(0))
                if self.auto.isChecked() and float(pk.max()) > 10 ** (self.thr.value() / 20):
                    self.record()
        if self.take is not None:
            secs = sum(len(b) for b in self.take) / self.rate
            self.time.setText(decimal(secs, 1) + " s")
            if secs > 120:
                self.stop()
                self.state.setText(_("Stopped automatically after 2 minutes."))
        if self.rec and not self.rec.running and self.take is not None:
            self.stop()

    # ------------------------------------------------------------------ Ergebnis
    def _reprocess(self, *_args):
        if self._raw is None:
            return
        y = recorder.process_take(self._raw, self.rate, self.trim.isChecked(), self.norm.isChecked())
        if self.mono.isChecked() and y.ndim == 2:
            y = y.mean(axis=1)
        self.result = y
        if len(y):
            mono = y if y.ndim == 1 else y.mean(axis=1)
            self.wave.set_data(S.peaks(mono.tolist(), 800), len(y) / self.rate)
            length = fmt_secs(len(y) / self.rate) if len(y) > 60 * self.rate else f"{len(y) / self.rate:.2f} s"
            self.state.setText(_("Recorded: {length} – listen, name it, put it into the library.").format(length=length))
        else:
            self.wave.clear(_("Only silence recorded – check the level."))
        self._set_result_enabled(bool(len(y)))

    def _set_result_enabled(self, on: bool):
        for b in (self.b_play, self.b_save, self.b_drop):
            b.setEnabled(on)

    def play(self):
        if self.result is None or not len(self.result):
            return
        mono = self.result if self.result.ndim == 1 else self.result.mean(axis=1)
        self.player.play_samples((np.clip(mono, -1, 1) * 32767).astype(np.int16).tolist(), self.rate, status=self.state.setText)

    def save(self) -> Path | None:
        if self.result is None or not len(self.result):
            return None
        folder = self.ctx.settings.samples_dir / "Aufnahmen"  # i18n: ignore (Ordnername)
        try:
            path = _unique(folder, self.name.text().strip() or _("Recording"))
            recorder.write_wav_array(path, self.result, self.rate)
            self.lib.add_file(path, root=self.ctx.settings.samples_dir, tags="aufnahme," + self.tags.text())
        except OSError as e:
            show_error(self, _("Into the library"), fs_error_text(e, _("Recording not saved")))
            return None
        self.state.setText(_("Saved: {name} – now in the library (folder Aufnahmen).").format(name=path.name))
        self.result, self._raw = None, None
        self.name.clear()
        self._set_result_enabled(False)
        self.saved.emit(str(path))
        return path

    def discard(self):
        if self.result is not None and len(self.result) and not confirm(
                self, _("Discard"), _("Discard the recording? It is not saved yet and cannot be "
                                      "recovered."), _("Discard"), danger=True):
            return
        self.result, self._raw = None, None
        self.wave.clear(_("Discarded – nothing saved."))
        self.state.setText(_("Recording discarded."))
        self._set_result_enabled(False)

    def shutdown(self):
        import shutil
        self.take = None
        self._stop_capture()
        shutil.rmtree(self.tmpdir, ignore_errors=True)
