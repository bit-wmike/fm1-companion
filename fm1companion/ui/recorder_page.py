# SPDX-License-Identifier: GPL-3.0-only
"""Aufnahme: FM-1 (oder jede andere Quelle) aufnehmen – auf Wunsch taktgenau zur MIDI-Clock des FM-1 –,
Spuren trennen, kurz schneiden, exportieren, als Sample übernehmen, in Audacity öffnen."""
from __future__ import annotations

import shutil
import time
from pathlib import Path

from PyQt6.QtCore import QTimer, QUrl, Qt, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                             QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QProgressBar, QPushButton,
                             QScrollArea, QSpinBox, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import config, recorder, samples as S
from .audio import LiveWave, Player, Waveform
from .common import (FlowLayout, JobDialog, confirm, fs_error_text, notify, fmt_secs, fmt_size, heading, icon,
                     run_job, safe_folder, show_error)
from ..i18n import _, decimal, ngettext

REALTIME = (0xF8, 0xFA, 0xFB, 0xFC)


class RecorderPage(QWidget):
    clock_in = pyqtSignal(int, float)          # aus dem MIDI-Thread: (Statusbyte, Zeit)
    spp_in = pyqtSignal(int)

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.opts = config.module("recorder", {"sync": False, "bars": 4, "sync_mode": "start", "latency_ms": 0.0,
                                               "split": "pairs", "export": "flac", "source": "fm1"})
        self.dev: recorder.CaptureDevice | None = None
        self.sources: list[recorder.CaptureDevice] = []
        self.rec: recorder.Recording | None = None
        self.tail: recorder.TailReader | None = None
        self.est: recorder.StartEstimator | None = None
        self.sync = recorder.BarSync()
        self._link = None
        self.player = Player(self)
        self.clock_in.connect(self._clock)
        self.spp_in.connect(self.sync.feed_spp)

        outer = QVBoxLayout(self)
        outer.addWidget(heading(_("Recording"), _("Records the FM-1 via USB audio (or any other source). With Melodee "
                                                  "you can choose stereo or four separate tracks on the device – the "
                                                  "app splits them. Bar-accurate: start with the FM-1 and stop after "
                                                  "N bars.")))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 0, 0, 0)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        g = QGroupBox(_("Record"))
        f = QFormLayout(g)
        srow = QHBoxLayout()
        self.source = QComboBox()
        self.source.activated.connect(self._source_chosen)
        self.b_find = QPushButton(icon("view-refresh"), _("Search"))
        self.b_find.setToolTip(_("Search for audio devices again"))
        self.b_find.clicked.connect(self.find)
        srow.addWidget(self.source, 1)
        srow.addWidget(self.b_find)
        f.addRow(_("Source:"), srow)
        self.dev_label = QLabel("–")
        self.dev_label.setWordWrap(True)
        self.dev_label.setProperty("role", "muted")
        f.addRow("", self.dev_label)
        self.mode = QComboBox()
        self.mode.addItem(_("Automatic (what the source offers)"), "auto")
        self.mode.addItem(_("Stereo (sum)"), "stereo")
        self.mode.addItem(_("All tracks separate"), "tracks")
        self.mode.setCurrentIndex(max(0, self.mode.findData(ctx.settings.record_mode)))
        self.mode.activated.connect(self._mode)
        f.addRow(_("Mode:"), self.mode)
        self.split = QComboBox()
        self.split.addItem(_("Split multichannel into stereo pairs"), "pairs")
        self.split.addItem(_("Split multichannel into single channels (mono)"), "mono")
        self.split.setCurrentIndex(max(0, self.split.findData(self.opts["split"])))
        self.split.activated.connect(lambda: self._opt("split", self.split.currentData()))
        f.addRow(_("Tracks:"), self.split)
        self.rate = QComboBox()
        f.addRow(_("Sample rate:"), self.rate)
        self.name = QLineEdit()
        self.name.setPlaceholderText(_("e.g. song idea 3 (optional)"))
        f.addRow(_("Name:"), self.name)

        # Takt-Sync
        sy = QHBoxLayout()
        self.cb_sync = QCheckBox(_("bar-accurate to the MIDI clock"))
        self.cb_sync.setToolTip(_("The recording starts exactly at the start (or next bar) of the FM-1 and ends after "
                                  "the chosen number of bars. The FM-1 must send MIDI clock (CLK OUT)."))
        self.cb_sync.setChecked(bool(self.opts["sync"]))
        self.cb_sync.toggled.connect(lambda v: (self._opt("sync", v), self._sync_ui()))
        self.sync_mode = QComboBox()
        self.sync_mode.addItem(_("wait for start"), "start")
        self.sync_mode.addItem(_("from the next bar"), "bar")
        self.sync_mode.setCurrentIndex(max(0, self.sync_mode.findData(self.opts["sync_mode"])))
        self.sync_mode.activated.connect(lambda: self._opt("sync_mode", self.sync_mode.currentData()))
        self.bars = QSpinBox()
        self.bars.setRange(0, 256)
        self.bars.setSpecialValueText(_("until stop"))
        self.bars.setSuffix(_(" bars"))
        self.bars.setValue(int(self.opts["bars"]))
        self.bars.valueChanged.connect(lambda v: self._opt("bars", v))
        self.latency = QDoubleSpinBox()
        self.latency.setRange(-500, 500)
        self.latency.setSuffix(" ms")
        self.latency.setValue(float(self.opts["latency_ms"]))
        self.latency.setToolTip(_("Compensation if the start sits a tick too early/late (positive = cut later)"))
        self.latency.valueChanged.connect(lambda v: self._opt("latency_ms", v))
        sy = FlowLayout()
        for wdg in (self.cb_sync, self.sync_mode, self.bars, QLabel(_("Compensation:")), self.latency):
            sy.addWidget(wdg)
        f.addRow(_("Bar:"), sy)
        self.sync_label = QLabel("")
        self.sync_label.setProperty("role", "muted")
        f.addRow("", self.sync_label)

        self.time = QLabel("0:00")
        fnt = self.time.font()
        fnt.setPointSizeF(fnt.pointSizeF() * 2)
        fnt.setBold(True)
        self.time.setFont(fnt)
        f.addRow(_("Duration:"), self.time)
        self.meters: list[QProgressBar] = []
        self.meter_box = QVBoxLayout()
        f.addRow(_("Level:"), self.meter_box)
        self.level_hint = QLabel("")                    # Melodee: Aufnahme vor/nach MASTER (melodee16)
        self.level_hint.setWordWrap(True)
        self.level_hint.setProperty("role", "muted")
        self.level_hint.hide()
        f.addRow("", self.level_hint)
        self.live = LiveWave(8.0)
        f.addRow(self.live)
        self.b_rec = QPushButton(icon("media-record"), _("Start recording"))
        self.b_rec.setProperty("variant", "primary")
        self.b_rec.setToolTip(_("Start/stop recording (Ctrl+R)"))
        self.b_rec.clicked.connect(self._toggle)
        sc = QShortcut(QKeySequence("Ctrl+R"), self, self._toggle)
        sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        row = QHBoxLayout()
        row.addWidget(self.b_rec)
        row.addStretch(1)
        f.addRow(row)
        lay.addWidget(g)

        lg = QGroupBox(_("Recordings"))
        ll = QVBoxLayout(lg)
        self.list = QTreeWidget()
        self.list.setHeaderLabels([_("File"), _("Duration"), _("Size")])
        self.list.setRootIsDecorated(False)
        self.list.setMinimumHeight(180)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.itemDoubleClicked.connect(lambda *_: self._play())
        sc = QShortcut(QKeySequence(Qt.Key.Key_Space), self.list, self._play)
        sc.setContext(Qt.ShortcutContext.WidgetShortcut)
        ll.addWidget(self.list, 1)
        self.empty = QLabel(_("No recordings yet – how it works: choose a source, “Start recording”, play on the FM-1, "
                              "“Stop recording”. The file lands in {folder}.").format(folder="~/FM-1/Aufnahmen"))
        self.empty.setWordWrap(True)
        self.empty.setProperty("role", "muted")
        ll.addWidget(self.empty)
        b_play = QPushButton(icon("media-playback-start"), _("Listen"))
        b_play.clicked.connect(self._play)
        b_stop = QPushButton(icon("media-playback-stop"), _("Stop"))
        b_stop.clicked.connect(self.player.stop)
        b_edit = QPushButton(icon("edit-cut"), _("Cut …"))
        b_edit.setToolTip(_("Set start/end, fade in/out, normalise – as a new file"))
        b_edit.clicked.connect(self._edit)
        self.export_fmt = QComboBox()
        for k, (t, _args) in recorder.EXPORT_FORMATS.items():
            self.export_fmt.addItem(_(t), k)
        self.export_fmt.setCurrentIndex(max(0, self.export_fmt.findData(self.opts["export"])))
        b_exp = QPushButton(icon("document-export"), _("Export"))
        b_exp.clicked.connect(self._export)
        b_sample = QPushButton(icon("list-add"), _("Use as sample"))
        b_sample.setToolTip(_("Copy to {folder} – shows up in the sample library").format(folder="~/FM-1/Samples/Aufnahmen"))
        b_sample.clicked.connect(self._as_sample)
        b_aud = QPushButton(icon("audacity", "audio-x-generic"), _("Open in Audacity"))
        b_aud.clicked.connect(self._audacity)
        self.b_del = b_del = QPushButton(icon("edit-delete"), _("Delete"))
        b_del.setProperty("variant", "danger")
        b_del.setEnabled(False)
        b_del.setToolTip(_("Delete the marked recordings (only possible with a selection)"))
        b_del.clicked.connect(self._delete)
        self.sel_buttons = (b_play, b_edit, b_exp, b_sample, b_aud, b_del)
        self.list.itemSelectionChanged.connect(self._sel_changed)
        b_open = QPushButton(icon("folder-open"), _("Folder"))
        b_open.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(safe_folder("Aufnahmen")[0]))))
        r2 = FlowLayout()                      # bricht bei schmalem Fenster um
        for b in (b_play, b_stop, b_edit, self.export_fmt, b_exp, b_sample, b_aud, b_del, b_open):
            r2.addWidget(b)
        ll.addLayout(r2)
        lay.addWidget(lg, 1)

        self.timer = QTimer(self, interval=80)
        self.timer.timeout.connect(self._tick)
        ctx.changed.connect(self.find)
        ctx.changed.connect(self._relisten)
        self.find()
        self.refresh_list()
        self._sync_ui()

    # ------------------------------------------------------------ Einstellungen
    def _opt(self, k, v):
        self.opts[k] = v
        config.save()

    def _sync_ui(self):
        on = self.cb_sync.isChecked()
        for wdg in (self.sync_mode, self.bars, self.latency):
            wdg.setEnabled(on)
        if on:
            self._relisten()
        self._show_sync()

    # ------------------------------------------------------------ MIDI-Clock
    def _listener(self, data, direction):
        if direction != "in" or not data:
            return
        st = data[0]
        if st in REALTIME:
            self.clock_in.emit(st, time.monotonic())
        elif st == 0xF2 and len(data) >= 3:
            self.spp_in.emit(data[1] | data[2] << 7)

    def _relisten(self):
        link = self.ctx.link
        if self._link is not None and self._link is not link:
            self._link.remove_listener(self._listener)
            self._link = None
        if link is not None and (self.cb_sync.isChecked() or self.isVisible()):
            link.add_listener(self._listener)
            self._link = link

    def activate(self):
        self._relisten()

    def deactivate(self):
        self.player.stop()
        if self._link is not None and not (self.rec and self.rec.running):
            self._link.remove_listener(self._listener)
            self._link = None

    def shutdown(self):
        self.deactivate()
        if self.rec and self.rec.running:          # beim Beenden: nur sauber stoppen (Datei bleibt roh erhalten)
            self.timer.stop()
            try:
                self.rec.stop()
            except Exception:  # noqa: BLE001
                pass
            self.rec = None

    def _clock(self, st: int, t: float):
        self.sync.feed(st, t)
        if st == 0xF8 and self.est is not None and self.tail is not None:
            self.est.observe(time.monotonic(), self.tail.frames)
        if st != 0xF8 or self.sync.tick % 24 == 0:
            self._show_sync()

    def _show_sync(self):
        if not self.cb_sync.isChecked():
            self.sync_label.setText("")
            return
        b = self.sync
        bar, beat = b.position
        tempo = f"{b.bpm:.1f} BPM" if b.bpm else _("no clock")
        where = _("bar {bar}.{beat}").format(bar=bar, beat=beat) if b.running and bar else (
            _("stopped") if b.tick >= 0 else _("waiting for MIDI clock"))
        st = {"aus": "", "wartet": " · " + _("recording waits for the cue …"), "nimmt auf": " · " + _("running"),  # i18n: ignore
              "fertig": " · " + _("done")}[b.state]  # i18n: ignore – state keys
        self.sync_label.setText(f"{tempo} · {where}{st}")

    # ------------------------------------------------------------ Quellen
    def find(self):
        """Quellen suchen – im Hintergrund (pw-dump kann Sekunden brauchen; der GUI-Thread wartet nicht)."""
        if self.rec and self.rec.running:
            return
        if getattr(self, "_finding", False):
            self._find_again = True
            return
        self._finding = True
        self.b_find.setEnabled(False)
        self.dev_label.setText(_("Searching for audio devices …"))
        port = self.ctx.port

        def done(sources):
            self._finding = False
            self.b_find.setEnabled(True)
            if getattr(self, "_find_again", False):
                self._find_again = False
                self.find()
                return
            self._found(sources)

        def fail(m, tb):
            self._finding = False
            self.b_find.setEnabled(True)
            self._found([])
            self.dev_label.setText(_("Audio devices could not be searched: {error}").format(error=m))
        run_job(self, lambda w: recorder.list_sources(port), on_done=done, on_fail=fail)

    def _found(self, sources):
        self.sources = sources
        self.source.clear()
        for d in self.sources:
            self.source.addItem(d.title, d.kind)
        want = self.opts.get("source", "fm1")
        idx = next((i for i, d in enumerate(self.sources) if d.kind == want), 0)
        if self.sources:
            self.source.setCurrentIndex(idx)
        self._source_chosen()

    def _source_chosen(self, *_args):
        i = self.source.currentIndex()
        self.dev = self.sources[i] if 0 <= i < len(self.sources) else None
        self.rate.clear()
        if not self.dev:
            self.dev_label.setText(_("No recording input found. Is the FM-1 connected via USB? (After a firmware "
                                     "update unplug it once and plug it in again.)"))
            self.level_hint.hide()
            self.b_rec.setEnabled(False)
            return
        self.opts["source"] = self.dev.kind
        self.b_rec.setEnabled(True)
        for r in self.dev.rates:
            self.rate.addItem(f"{r / 1000:g}".replace(".", decimal(0.1, 1)[1]) + " kHz", r)
        self.rate.setCurrentIndex(max(0, self.rate.findData(44100)))
        way = "Demo" if self.dev.kind == "demo" else ("PipeWire" if self.dev.pw_node else "ALSA")
        self.dev_label.setText(_("{device} · channels: {channels} · {way}").format(
            device=self.dev.title, channels=', '.join(map(str, self.dev.channels)), way=way))
        self._level_hint()

    def _level_hint(self):
        from .. import melodee16
        info = getattr(self.ctx, "info", None)
        txt = melodee16.usb_record_hint(info.version if info else "") if self.dev and self.dev.kind == "fm1" else ""
        self.level_hint.setText(txt)
        self.level_hint.setVisible(bool(txt))

    def _mode(self):
        self.ctx.settings.record_mode = self.mode.currentData()
        config.save(self.ctx.settings)

    def _channels(self) -> int:
        m = self.mode.currentData()
        ch = self.dev.channels if self.dev else [2]
        if m == "stereo":
            return 2 if 2 in ch else min(ch)
        return max(ch)

    # ------------------------------------------------------------ Aufnehmen
    def _toggle(self):
        if self.rec and self.rec.running:
            self._finish()
            return
        if not self.dev or getattr(self, "_starting", False):
            return
        ch = self._channels()
        try:
            rec = recorder.Recording(self.dev, self.ctx.settings.recordings_dir, ch,
                                     self.rate.currentData() or 44100, self.name.text().strip())
        except OSError as e:
            show_error(self, _("Recording not possible"), fs_error_text(e, _("Recording folder not writable")))
            return
        self._starting = True
        self.b_rec.setEnabled(False)
        self.b_rec.setText(_("Opening the input …"))

        def done(_res):
            self._starting = False
            self.b_rec.setEnabled(True)
            self.rec = rec
            self._started(ch)

        def fail(m, tb):
            self._starting = False
            self.b_rec.setEnabled(True)
            self.b_rec.setText(_("Start recording"))
            self.rec = None
            show_error(self, _("Recording not possible"), m)
        run_job(self, lambda w: rec.start(), on_done=done, on_fail=fail)   # start() wartet 0,6 s je Weg

    def _started(self, ch: int):
        self.tail = recorder.TailReader(self.rec.path, ch)
        self.est = recorder.StartEstimator(self.rec.rate)
        self.live.reset(self.rec.rate)
        if self.cb_sync.isChecked():
            self._relisten()
            self.sync.arm(self.bars.value(), self.sync_mode.currentData())
            if self.sync.mode == "start" and self.sync_mode.currentData() == "bar":
                self.sync_label.setText(_("Bar unknown (start not seen) – waiting for the FM-1 to start …"))
        else:
            self.sync.disarm()
        while self.meters:
            m = self.meters.pop()
            self.meter_box.removeWidget(m)
            m.deleteLater()
        for c in range(ch):
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setTextVisible(True)
            bar.setFormat(_("Channel {n}").format(n=c + 1))
            bar.setMaximumHeight(14)
            self.meter_box.addWidget(bar)
            self.meters.append(bar)
        self.b_rec.setText(_("Stop recording"))
        self.b_rec.setIcon(icon("media-playback-stop"))
        self.timer.start()

    def _finish(self):
        """Aufnahme beenden; bei Takt-Sync auf den Takt-Bereich zuschneiden; Spuren trennen – die
        Nachbearbeitung (Datei lesen/schreiben, Spuren trennen) läuft im Hintergrund mit Fortschrittsdialog."""
        rec = self.rec
        if rec is None or getattr(self, "_finishing", False):
            return
        self._finishing = True
        self.timer.stop()
        self._tick(read_only=True)
        sync = self.sync if self.sync.armed else None
        t0 = self.est.t0 if self.est else None
        now = time.monotonic()
        start_t = sync.start_time if sync else None
        end_t = sync.end_time if sync and sync.end_time is not None else now
        latency, split = self.latency.value(), self.split.currentData()
        self.b_rec.setEnabled(False)
        self.b_rec.setText(_("Stopping the recording …"))
        if sync and start_t is None:
            self.sync_label.setText(_("No MIDI start received – the whole recording is kept."))

        def work(w):
            w.log.emit(_("Stopping the recording program …"))
            raw = rec.stop() if rec.channels <= 2 else _stop_raw(rec)
            path = rec.path
            if sync and start_t is not None and t0 is not None:
                w.log.emit(_("Trimming to the bar range …"))
                a, b = recorder.take_window(start_t, end_t, t0, rec.rate, latency)
                sr, arr = recorder.read_wav_array(path)
                recorder.write_wav_array(path, arr[a:min(b, len(arr))], sr)
            if rec.channels > 2:
                w.log.emit(_("Splitting {n} channels …").format(n=rec.channels))
                return recorder.split_tracks(path, split)
            return raw

        def after(files, err=""):
            self._finishing = False
            self.rec = None
            self.sync.disarm()
            self.b_rec.setEnabled(True)
            self.b_rec.setText(_("Start recording"))
            self.b_rec.setIcon(icon("media-record"))
            self.refresh_list()
            self.last_files = files
            if err:
                show_error(self, _("Post-process recording"), err + "\n\n" + _("The raw recording is in the recording "
                                                                                 "folder: {name}").format(name=rec.path.name))
                return
            if self.ctx.settings.record_open_audacity and files and not getattr(self, "no_audacity", False):
                if not recorder.open_in_audacity(files, self.ctx.settings.audacity_cmd):
                    show_error(self, _("Audacity not found"), _("Audacity is not installed – on the Pi: sudo apt install "
                                                                "audacity (or as a Flatpak). The recording is ready in the "
                                                                "recording folder anyway; “Open in Audacity” works any "
                                                                "time later."))
        long = rec.elapsed > 20 or rec.channels > 2
        if long:                                     # länger: Dialog mit Fortschritt, sonst still im Hintergrund
            d = JobDialog(self, _("Finish recording"), work, close_on_success=True,
                          done_text=lambda files: ngettext("{n} file saved.", "{n} files saved.", len(files)).format(
                              n=len(files)))
            d.exec()
            after(d.result_value or [], "" if d.ok else d.error)
        else:
            run_job(self, work, on_done=lambda files: after(files), on_fail=lambda m, tb: after([], m))

    def _tick(self, read_only=False):
        if not self.rec:
            return
        if self.tail:
            blk = self.tail.read()
            if self.est is not None:
                self.est.observe(time.monotonic(), self.tail.frames)
            if len(blk):
                import numpy as np
                pk = np.abs(blk).max(axis=0)
                for bar, v in zip(self.meters, pk):
                    bar.setValue(int(100 * max(0.0, 1 + 20 * np.log10(max(float(v), 1e-6)) / 60)))
                self.live.push(blk)
        if read_only:
            return
        if not self.rec.running:
            self.timer.stop()
            show_error(self, _("Recording ended"), _("The recording ended unexpectedly (USB disconnected?)."))
            self._finish()
            return
        el = self.rec.elapsed
        try:
            size = self.rec.path.stat().st_size
        except OSError:
            size = 0
        self.time.setText((fmt_secs(el) if el >= 60 else f"0:{int(el):02d}")
                          + (f"   <span style='font-size:60%'>{fmt_size(size)}</span>" if size > 1 << 20 else ""))
        s = self.sync
        if s.armed and s.end_time is not None and time.monotonic() > s.end_time + 0.35:
            self._finish()
        elif s.armed and s.start_time is None and el > 15 and not s.times:
            self.sync_label.setText(_("No MIDI clock from the FM-1 for {seconds} s – does it send clock (CLK OUT on) and is "
                                      "playback running? The recording runs anyway; “Stop recording” keeps everything.").format(
                seconds=int(el)))

    # ------------------------------------------------------------ Liste
    def refresh_list(self):
        self.list.clear()
        d = self.ctx.settings.recordings_dir
        try:
            files = sorted([p for p in d.iterdir() if p.suffix.lower() in (".wav", ".flac", ".mp3")],
                           key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError as e:
            files = []
            self.empty.setText(fs_error_text(e, _("Recording folder not readable")))
        later = []                                   # FLAC/MP3: Länge per ffprobe – das dauert, also im Hintergrund
        for p in files:
            try:
                size = p.stat().st_size
                wav = p.suffix.lower() == ".wav"
                dur = S.probe(p).duration if wav else None      # WAV: nur den Kopf lesen, Millisekunden
            except OSError:
                continue
            it = QTreeWidgetItem([p.name, _dur_text(dur) if dur is not None else "…", fmt_size(size)])
            it.setData(0, Qt.ItemDataRole.UserRole, str(p))
            self.list.addTopLevelItem(it)
            if dur is None:
                later.append((it, p))
        self.list.resizeColumnToContents(0)
        if later:
            gen = self._list_gen = getattr(self, "_list_gen", 0) + 1

            def work(w):
                out = []
                for _it, p in later:
                    try:
                        out.append(S.probe(p).duration)
                    except Exception:  # noqa: BLE001 – dann bleibt „?“
                        out.append(None)
                return out

            def done(durs):
                if gen != self._list_gen:            # Liste inzwischen neu aufgebaut
                    return
                for (it, _p), dur in zip(later, durs):
                    it.setText(1, _dur_text(dur) if dur else "?")
            run_job(self, work, on_done=done, on_fail=lambda m, tb: None)
        self.empty.setVisible(not files)
        self._sel_changed()

    def _files(self) -> list[Path]:
        return [Path(it.data(0, Qt.ItemDataRole.UserRole)) for it in self.list.selectedItems()]

    def _play(self):
        f = self._files()
        if f:
            self.player.play(f[0], status=self.dev_label.setText)

    def _audacity(self):
        f = self._files()
        if f and not recorder.open_in_audacity(f, self.ctx.settings.audacity_cmd):
            show_error(self, _("Audacity not found"), _("Audacity is not installed. On the Pi: sudo apt install audacity "
                                                        "– or adjust the command in the settings (e.g. for a Flatpak)."))

    def _sel_changed(self):
        has = bool(self.list.selectedItems())
        for b in self.sel_buttons:
            b.setEnabled(has)

    def _delete(self):
        f = self._files()
        names = "\n".join(p.name for p in f[:10]) + ("\n" + _("… and {n} more").format(n=len(f) - 10) if len(f) > 10 else "")
        if f and confirm(self, _("Delete"), ngettext("Delete {n} recording for good?", "Delete {n} recordings for good?",
                                                     len(f)).format(n=len(f)) + f"\n\n{names}",
                         _("Delete"), danger=True):
            try:
                for p in f:
                    p.unlink(missing_ok=True)
            except OSError as e:
                show_error(self, _("Delete"), fs_error_text(e, _("Not all recordings deleted")))
            else:
                notify(ngettext("{n} recording deleted.", "{n} recordings deleted.", len(f)).format(n=len(f)), "success")
            self.refresh_list()

    def _export(self):
        f = self._files()
        fmt = self.export_fmt.currentData()
        self._opt("export", fmt)
        if not f:
            return
        wavs = [p for p in f if p.suffix.lower() == ".wav"]

        def work(w):
            return [recorder.export_audio(p, fmt, p.with_name(p.stem + ("-export" if fmt == "wav" else "") + "." + fmt))
                    for p in wavs]
        run_job(self, work, on_done=lambda out: (self.refresh_list(), self.dev_label.setText(
            ngettext("{n} file exported as {format}.", "{n} files exported as {format}.", len(out)).format(
                n=len(out), format=fmt.upper()))),
            done_text=lambda out: ngettext(
                "{done} of {n} recording exported as {format} (next to the original).",
                "{done} of {n} recordings exported as {format} (next to the original).", len(wavs)).format(
                done=len(out), n=len(wavs), format=fmt.upper())
            + (" " + _("{n} skipped (not WAV).").format(n=len(f) - len(wavs)) if len(f) > len(wavs) else ""),
            on_fail=lambda m, tb: show_error(self, _("Export failed"), m))
        self.export_job = True

    def _as_sample(self) -> list[Path]:
        out = []
        try:
            folder = self.ctx.settings.samples_dir / "Aufnahmen"
            folder.mkdir(parents=True, exist_ok=True)
            lib = S.Library()
            for p in self._files():
                dst = folder / p.name
                k = 2
                while dst.exists():
                    dst = folder / f"{p.stem} ({k}){p.suffix}"
                    k += 1
                shutil.copyfile(p, dst)
                lib.add_file(dst, root=self.ctx.settings.samples_dir, tags="aufnahme")
                out.append(dst)
        except OSError as e:
            show_error(self, _("Use as sample"), fs_error_text(e, _("{n} taken over, then aborted").format(n=len(out))))
        if out:
            self.dev_label.setText(ngettext("{n} file taken over into the sample library (Samples › Recordings).",
                                            "{n} files taken over into the sample library (Samples › Recordings).",
                                            len(out)).format(n=len(out)))
            notify(ngettext("{n} recording taken over as a sample (Samples › Recordings).",
                            "{n} recordings taken over as samples (Samples › Recordings).", len(out)).format(n=len(out)),
                   "success")
        return out

    def _edit(self):
        f = [p for p in self._files() if p.suffix.lower() == ".wav"]
        if not f:
            return None
        d = QuickEditDialog(f[0], self, self.player)
        if d.exec() and d.saved:
            self.refresh_list()
        return d


def _dur_text(dur: float) -> str:
    return fmt_secs(dur) if dur >= 1 else decimal(dur, 2) + " s"


def _stop_raw(rec: recorder.Recording) -> list[Path]:
    """wie Recording.stop(), aber ohne automatisches Aufteilen (das macht die Seite nach dem Zuschnitt)."""
    import signal
    import subprocess
    if rec.proc and rec.proc.poll() is None:
        rec.proc.send_signal(signal.SIGINT)
        try:
            rec.proc.wait(5)
        except subprocess.TimeoutExpired:
            rec.proc.terminate()
            rec.proc.wait(3)
    rec.proc = None
    recorder._fix_header(rec.path)
    return [rec.path]


class QuickEditDialog(QDialog):
    """Schnell-Editor: Anfang/Ende ziehen, ein-/ausblenden, normalisieren -> neue Datei."""

    def __init__(self, path: Path, parent=None, player: Player | None = None):
        super().__init__(parent)
        self.path = Path(path)
        self.player = player or Player(self)
        self.saved: Path | None = None
        self.setWindowTitle(_("Cut – {name}").format(name=self.path.name))
        self.resize(760, 360)
        lay = QVBoxLayout(self)
        self.wave = Waveform()
        self.wave.setMinimumHeight(150)
        lay.addWidget(self.wave, 1)
        f = QFormLayout()
        self.fin = QDoubleSpinBox()
        self.fin.setRange(0, 10000)
        self.fin.setSuffix(" ms")
        self.fout = QDoubleSpinBox()
        self.fout.setRange(0, 10000)
        self.fout.setValue(10)
        self.fout.setSuffix(" ms")
        self.norm = QCheckBox(_("Normalise (peak to −0.3 dB)"))
        self.norm.setChecked(True)
        f.addRow(_("Fade in:"), self.fin)
        f.addRow(_("Fade out:"), self.fout)
        f.addRow("", self.norm)
        lay.addLayout(f)
        bb = QDialogButtonBox()
        self.b_play = bb.addButton(_("Listen to selection"), QDialogButtonBox.ButtonRole.ActionRole)
        self.b_play.clicked.connect(self.play)
        self.b_ok = bb.addButton(_("Save as new file"), QDialogButtonBox.ButtonRole.AcceptRole)
        self.b_ok.clicked.connect(self.save)
        bb.addButton(_("Cancel"), QDialogButtonBox.ButtonRole.RejectRole).clicked.connect(self.reject)
        lay.addWidget(bb)
        self.sr, self.a = 0, None
        self.b_play.setEnabled(False)
        self.b_ok.setEnabled(False)
        self.wave.clear(_("Loading the recording …"))

        path = self.path

        def load(w):
            # alles Rechnen hier im Hintergrund: eine 10-Minuten-Aufnahme sind ~26 Mio. Werte
            sr, a = recorder.read_wav_array(path)
            mono = a.mean(axis=1)
            return sr, a, S.peaks(mono, 900), len(mono) / sr

        def done(res):
            self.sr, self.a, pk, dur = res
            self.wave.set_data(pk, dur, 0, 0, editable=True)
            self.b_play.setEnabled(True)
            self.b_ok.setEnabled(True)
        run_job(self, load, on_done=done, on_fail=lambda m, tb: self.wave.clear(_("File not readable: {error}").format(error=m)))

    def play(self):
        """Auswahl anhören – über eine kurze WAV-Datei (numpy schreibt sie in Millisekunden, keine Python-Liste)."""
        if self.a is None:
            return
        a, b = int(self.wave.start * self.sr), int(self.wave.end * self.sr)
        y = recorder.fade(self.a[a:b].mean(axis=1), self.sr, self.fin.value(), self.fout.value())
        import os
        import tempfile
        try:
            fd, name = tempfile.mkstemp(prefix="fm1c-quickedit-", suffix=".wav")   # sicherer, zufälliger Name
            os.close(fd)
            tmp = Path(name)
            prev = getattr(self, "_tmp_wav", None)
            self._tmp_wav = tmp
            recorder.write_wav_array(tmp, y, self.sr)
            if prev is None:                           # beim Beenden wegräumen
                import atexit
                atexit.register(lambda: getattr(self, "_tmp_wav", Path("/nonexistent")).unlink(missing_ok=True))
            elif prev != tmp:
                prev.unlink(missing_ok=True)
        except OSError as e:
            show_error(self, _("Listen"), fs_error_text(e, _("Preview not possible")))
            return
        self.player.play(tmp)

    def save(self) -> Path | None:
        """Auswahl als neue Datei speichern – im Hintergrund (liest die ganze Aufnahme noch einmal); der Dialog
        schließt sich, wenn die Datei fertig ist."""
        if self.a is None or getattr(self, "_saving", False):
            return None
        dst = self.path.with_name(f"{self.path.stem} (geschnitten).wav")  # i18n: ignore – file name
        k = 2
        while dst.exists():
            dst = self.path.with_name(f"{self.path.stem} (geschnitten {k}).wav")  # i18n: ignore – file name
            k += 1
        args = (self.path, dst, self.wave.start, self.wave.end, self.norm.isChecked(), self.fin.value(), self.fout.value())
        self._saving = True
        self.b_ok.setEnabled(False)
        self.b_ok.setText(_("Saving …"))

        def done(saved):
            self._saving = False
            self.saved = saved
            notify(_("Cut and saved: {name} (the original stays unchanged).").format(name=dst.name), "success")
            self.accept()

        def fail(m, tb):
            self._saving = False
            self.b_ok.setEnabled(True)
            self.b_ok.setText(_("Save as new file"))
            show_error(self, _("Cut"), _("Not saved: {error}\n\nThe original stays unchanged.").format(error=m))
        run_job(self, lambda w: recorder.cut_wav(*args), on_done=done, on_fail=fail)
        return dst
