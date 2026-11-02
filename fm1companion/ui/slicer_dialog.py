# SPDX-License-Identifier: GPL-3.0-only
"""Dialog „Loop zerschneiden“: Schnittmarken finden/setzen, Stücke vorhören, als WAVs in die
Bibliothek legen oder direkt ein Kit bauen."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel,
                             QPushButton, QSlider, QSpinBox, QStackedWidget, QVBoxLayout, QWidget)

from .. import samples as S, slicer
from .audio import Player, SliceView
from .common import fs_error_text, icon, plural, run_job, show_error

MODES = [("onsets", "Transienten (Schläge erkennen)"), ("equal", "Gleich lange Stücke"), ("grid", "BPM-Raster")]


class SlicerDialog(QDialog):
    def __init__(self, path: str, parent=None, out_dir: Path | None = None, player: Player | None = None):
        super().__init__(parent)
        self.path = str(path)
        self.out_dir = out_dir
        self.setWindowTitle(f"Zerschneiden – {Path(path).name}")
        self.resize(980, 620)
        self.player = player or Player(self)
        self.sr = 0
        self.x = np.zeros(0)
        self.kit: S.Kit | None = None
        self.exported: list[Path] = []
        self._seq = 0                          # laufende Nummer der Berechnung (Schieberegler → Hintergrund)
        self._timer = QTimer(self, singleShot=True, interval=150)
        self._timer.timeout.connect(self._compute_now)

        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.mode = QComboBox()
        for k, t in MODES:
            self.mode.addItem(t, k)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        top.addWidget(QLabel("Schneiden nach:"))
        top.addWidget(self.mode)
        self.params = QStackedWidget()
        # Transienten
        p1 = QWidget()
        f1 = QHBoxLayout(p1)
        f1.setContentsMargins(0, 0, 0, 0)
        self.sens = QSlider(Qt.Orientation.Horizontal)
        self.sens.setRange(0, 100)
        self.sens.setValue(50)
        self.sens.setToolTip("Empfindlichkeit: weiter rechts findet auch leise Schläge")
        self.gap = QSpinBox()
        self.gap.setRange(10, 1000)
        self.gap.setValue(60)
        self.gap.setSuffix(" ms")
        self.gap.setToolTip("Mindestabstand zwischen zwei Schnitten")
        f1.addWidget(QLabel("Empfindlichkeit:"))
        f1.addWidget(self.sens, 1)
        f1.addWidget(QLabel("Abstand min.:"))
        f1.addWidget(self.gap)
        # gleich lang
        p2 = QWidget()
        f2 = QHBoxLayout(p2)
        f2.setContentsMargins(0, 0, 0, 0)
        self.count = QSpinBox()
        self.count.setRange(2, 64)
        self.count.setValue(8)
        f2.addWidget(QLabel("Anzahl Stücke:"))
        f2.addWidget(self.count)
        f2.addStretch(1)
        # Raster
        p3 = QWidget()
        f3 = QHBoxLayout(p3)
        f3.setContentsMargins(0, 0, 0, 0)
        self.bpm = QDoubleSpinBox()
        self.bpm.setRange(30, 300)
        self.bpm.setDecimals(2)
        self.bpm.setValue(120)
        self.bpm.setSuffix(" BPM")
        self.div = QComboBox()
        for d, t in ((4, "Viertel"), (8, "Achtel"), (16, "Sechzehntel")):
            self.div.addItem(t, d)
        self.div.setCurrentIndex(1)
        b_guess = QPushButton(icon("view-refresh"), "aus Länge")
        b_guess.setToolTip("Tempo aus der Loop-Länge schätzen (ganze Takte)")
        b_guess.clicked.connect(lambda: self.bpm.setValue(slicer.guess_bpm(self.duration)))
        f3.addWidget(QLabel("Tempo:"))
        f3.addWidget(self.bpm)
        f3.addWidget(b_guess)
        f3.addWidget(QLabel("Raster:"))
        f3.addWidget(self.div)
        f3.addStretch(1)
        for p in (p1, p2, p3):
            self.params.addWidget(p)
        top.addWidget(self.params, 1)
        self.zero = QCheckBox("auf Nulldurchgang")
        self.zero.setChecked(True)
        self.zero.setToolTip("Schnitte auf den nächsten Nulldurchgang legen – vermeidet Knackser")
        top.addWidget(self.zero)
        lay.addLayout(top)
        for w in (self.sens, self.gap, self.count, self.bpm, self.div, self.zero):
            sig = getattr(w, "valueChanged", None) or getattr(w, "currentIndexChanged", None) or w.toggled
            sig.connect(self.compute)

        self.view = SliceView()
        self.view.slice_clicked.connect(self.play_slice)
        self.view.points_changed.connect(self._update_info)
        lay.addWidget(self.view, 1)
        self.info = QLabel("Lädt …")
        self.info.setWordWrap(True)
        self.info.setProperty("role", "muted")
        lay.addWidget(self.info)
        hint = QLabel("Klick: Stück anhören · Doppelklick: Schnitt setzen · Ziehen: verschieben · Rechtsklick oder Entf: "
                      "Schnitt entfernen · Leertaste: aktuelles Stück · ←/→: vorheriges/nächstes · 1–9: Stück direkt")
        hint.setWordWrap(True)
        hint.setProperty("role", "small")
        lay.addWidget(hint)

        bottom = QHBoxLayout()
        f = QFormLayout()
        self.base = QSpinBox()
        self.base.setRange(0, 112)
        self.base.setValue(36)
        self.base.setToolTip("Taste des ersten Stücks im Kit (36 = C2, wie Kick bei General MIDI)")
        self.base.valueChanged.connect(lambda v: self.base.setSuffix(f"  ({S.note_name(v)})"))
        self.base.setSuffix(f"  ({S.note_name(36)})")
        self.kitname = QComboBox()
        self.kitname.setEditable(True)
        self.kitname.setEditText(Path(path).stem.upper()[:8])
        self.kitname.lineEdit().setMaxLength(8)
        f.addRow("Kit-Name:", self.kitname)
        f.addRow("Erste Taste:", self.base)
        bottom.addLayout(f)
        bottom.addStretch(1)
        self.b_wav = QPushButton(icon("document-export", "document-save"), "Als WAVs in die Bibliothek")
        self.b_wav.setToolTip("Jedes Stück als eigene WAV-Datei speichern (Samples › Slices)")
        self.b_wav.clicked.connect(self.export)
        self.b_kit = QPushButton(icon("list-add"), "Kit bauen")
        self.b_kit.setProperty("variant", "primary")
        self.b_kit.setToolTip("Kit mit einem Stück pro Taste (höchstens 16, passend für einen Slot gekürzt)")
        self.b_kit.clicked.connect(self.build_kit)
        b_close = QPushButton("Schließen")
        b_close.clicked.connect(self.reject)
        for b in (self.b_wav, self.b_kit, b_close):
            bottom.addWidget(b)
        lay.addLayout(bottom)

        QShortcut(QKeySequence(Qt.Key.Key_Space), self, lambda: self.play_slice(max(0, self.view.current)))
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, lambda: self._step(1))
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, lambda: self._step(-1))
        for n in range(1, 10):
            QShortcut(QKeySequence(str(n)), self, lambda n=n: self.play_slice(n - 1))
        QShortcut(QKeySequence(Qt.Key.Key_Delete), self, self.remove_current)
        self._load()

    def remove_current(self):
        """Entf: den Schnitt am Anfang des gewählten Stücks entfernen (der erste bei 0 s bleibt)."""
        i = self.view.current
        pts = sorted(self.view.points)
        if 0 <= i < len(pts) and pts[i] > 0:
            self.view.points.remove(pts[i])
            self.view.current = max(0, i - 1)
            self.view.update()
            self._update_info()

    @property
    def duration(self) -> float:
        return len(self.x) / self.sr if self.sr else 0.0

    def _load(self):
        def work(w):
            sr, x = S.read_mono(Path(self.path))
            a = np.asarray(x, dtype=np.float64)
            return sr, a, S.peaks(x, 1200)

        def done(r):
            self.sr, self.x, pk = r
            self.view.set_data(pk, self.duration)
            self.bpm.blockSignals(True)
            self.bpm.setValue(slicer.guess_bpm(self.duration))
            self.bpm.blockSignals(False)
            self.compute()
        run_job(self, work, on_done=done, on_fail=lambda m, tb: self.info.setText(f"Datei nicht lesbar: {m}"))

    def load_array(self, x, sr: int):
        """für Tests und Aufnahmen: Audio direkt übergeben."""
        self.sr, self.x = sr, np.asarray(x, dtype=np.float64)
        self.view.set_data(S.peaks(self.x.tolist(), 1200), self.duration)
        self._compute_now()

    def _mode_changed(self, i):
        self.params.setCurrentIndex(i)
        self.compute()

    def compute(self, *_):
        """Schnitte neu berechnen – entprellt und im Hintergrund (die Transienten-Suche über eine lange Datei
        dauert auf dem Pi Sekunden; der Regler bleibt flüssig)."""
        if not self.sr:
            return
        self._timer.start()

    def _compute_now(self):
        if not self.sr:
            return
        self._seq += 1
        seq = self._seq
        m = self.mode.currentData()
        x, sr, dur = self.x, self.sr, self.duration
        sens, gap, cnt, bpm, div, zero = (self.sens.value() / 100, self.gap.value() / 1000, self.count.value(),
                                          self.bpm.value(), self.div.currentData(), self.zero.isChecked())
        self.info.setText("Suche Schnittpunkte …")

        def work(w):
            found = None
            if m == "onsets":
                pts = slicer.detect_onsets(x, sr, sens, gap)
                found = len(pts)
                if pts and pts[0] > 0.03 and np.max(np.abs(x[:int(pts[0] * sr)]), initial=0) > 0.1 * np.max(np.abs(x)):
                    pts = [0.0] + pts
                if not pts:
                    pts = [0.0]
            elif m == "equal":
                pts = slicer.equal_points(dur, cnt)
            else:
                pts = slicer.grid_points(dur, bpm, div)
            if zero:
                pts = [slicer.snap_zero(x, sr, t) if t > 0 else 0.0 for t in pts]
            return pts, found

        def done(res):
            if seq != self._seq:
                return
            pts, found = res
            self.found = found
            self.view.set_points(pts)
            self._update_info()
        run_job(self, work, on_done=done, on_fail=lambda msg, tb: self.info.setText(f"Berechnung fehlgeschlagen: {msg}"))

    def ranges(self):
        return slicer.ranges(self.view.points, self.duration)

    def _update_info(self):
        rs = self.ranges()
        n = len(rs)
        txt = plural(n, "Stück", "Stücke")
        if n:
            lens = [b - a for a, b in rs]
            txt += f", {min(lens) * 1000:.0f}–{max(lens) * 1000:.0f} ms"
        if self.mode.currentData() == "onsets" and getattr(self, "found", None) == 0:
            txt += (" · Keine Schläge erkannt (gleichmäßiger Klang wie ein Pad?) – Empfindlichkeit nach rechts, "
                    "oder „Gleich lange Stücke“/„BPM-Raster“ wählen, oder per Doppelklick selbst schneiden.")
        elif self.mode.currentData() == "onsets" and n > 64:
            txt += " · Sehr viele Treffer – Empfindlichkeit nach links oder Mindestabstand erhöhen."
        if n > 16:
            txt += " · Ein Kit fasst 16 Sounds – nur die ersten 16 kommen hinein (die übrigen als WAVs speichern)."
        self.info.setText(txt)
        self.b_kit.setEnabled(n > 0)
        self.b_wav.setEnabled(n > 0)

    def _step(self, d):
        n = len(self.view.points)
        if n:
            self.play_slice((self.view.current + d) % n)

    def play_slice(self, i: int):
        rs = self.ranges()
        if not 0 <= i < len(rs):
            return
        self.view.current = i
        self.view.update()
        a, b = rs[i]
        y = np.clip(self.x[int(a * self.sr):int(b * self.sr)], -1, 1)
        self.player.play_samples((y * 32767).astype(np.int16).tolist(), self.sr, status=self.info.setText)

    def export(self):
        rs = self.ranges()
        if not rs:
            return
        base = Path(self.path).stem
        folder = (self.out_dir or Path.home() / "FM-1" / "Samples" / "Slices") / base
        try:
            self.exported = slicer.export_slices(self.x, self.sr, rs, folder, base)
        except OSError as e:
            show_error(self, "Speichern fehlgeschlagen", fs_error_text(e, "Stücke nicht gespeichert"))
            return
        self.info.setText(f"Fertig: {plural(len(self.exported), 'WAV-Datei', 'WAV-Dateien')} gespeichert in {folder} – "
                          "die Bibliothek liest sie beim Schließen ein.")

    def build_kit(self):
        rs = self.ranges()
        if not rs:
            return
        name = self.kitname.currentText().strip() or "SLICES"
        base = self.base.value()
        self.b_kit.setEnabled(False)
        self.info.setText("Baue das Kit (liest die Stücke und prüft, ob alles in einen Slot passt) …")

        def done(kit):
            self.kit = kit
            self.accept()

        def fail(m, tb):
            self.b_kit.setEnabled(True)
            self._update_info()
            show_error(self, "Kit nicht möglich", m)
        run_job(self, lambda w: slicer.kit_from_slices(self.path, rs, name, base), on_done=done, on_fail=fail)
