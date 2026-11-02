# SPDX-License-Identifier: GPL-3.0-only
"""Werkstatt: am Rechner bauen, auf dem FM-1 spielen – MIDI-Datei → Pattern(s), auch als Drum-Grid."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
                             QLabel, QListWidget, QListWidgetItem, QPushButton, QScrollArea, QSpinBox, QSplitter,
                             QVBoxLayout, QWidget)

from .. import midi_import as MI, projectfmt as PF
from .common import DeviceUndo, confirm, device_tag, fmt_size, fs_error_text, heading, icon, plural, run_job, show_error
from .musik_widgets import StepRoll


class PianoRoll(StepRoll):
    """(alter Name – die Werkstatt zeigt jetzt die gemeinsame Piano-Roll mit Drum-Grid)"""


class WorkshopPage(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.tracks: list[MI.MidiTrackInfo] = []
        self.chunks: list[list] = []
        self.steps = []
        self.path: str = ""
        self.undo = DeviceUndo()        # {"track", "patterns": [PatternSnapshot], "song": SongSnapshot|None} je Gerät
        self.report = MI.LossReport()
        lay = QVBoxLayout(self)
        lay.addWidget(heading("Aus MIDI-Datei: MIDI → Pattern",
                              "Melodien, Basslinien und Beats am Rechner schreiben (DAW, MuseScore, LMMS …), als "
                              "MIDI-Datei speichern und hier in Patterns einer FM-1-Spur spielen. Bis 64 Steps je "
                              "Pattern, bis 4 Noten pro Step, gehaltene Noten werden zu TIE, Schlagzeug wird zum "
                              "Drum-Grid. Längere Stücke verteilen sich auf mehrere Patterns."))
        top = QHBoxLayout()
        self.b_open = b_open = QPushButton(icon("document-open"), "MIDI-Datei öffnen …")
        b_open.setProperty("variant", "primary")
        b_open.setMinimumHeight(36)
        b_open.setToolTip("Eine .mid-Datei wählen – oder sie einfach auf diese Seite ziehen")
        b_open.clicked.connect(lambda: self._open())
        self.file_label = QLabel("Noch keine Datei – .mid wählen oder hierher ziehen.")
        self.file_label.setProperty("role", "muted")
        self.file_label.setWordWrap(True)
        top.addWidget(b_open)
        top.addWidget(self.file_label, 1)
        self.b_opts = QPushButton(icon("configure", "preferences-system"), "")
        self.b_opts.setCheckable(True)
        self.b_opts.setChecked(True)
        self.b_opts.setToolTip("Spurwahl und Übertragung ein- oder ausklappen – mehr Platz für die Vorschau")
        top.addWidget(self.b_opts)
        self.setAcceptDrops(True)
        lay.addLayout(top)
        mid_w = QWidget()                      # oben: Spuren + Übertragung, unten: Vorschau – im Splitter verschiebbar
        mid = QHBoxLayout(mid_w)
        mid.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.setToolTip("Spur bzw. Kanal der MIDI-Datei, die übertragen wird")
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setWordWrap(True)
        self.list.currentRowChanged.connect(lambda *_: self._track_changed())
        self._list_empty()
        mid.addWidget(self.list, 1)
        opt = QGroupBox("Übertragung")
        f = QFormLayout(opt)
        self.res = QComboBox()
        for txt, v in (("1/16 (Standard)", 4), ("1/8", 2), ("1/32", 8), ("1/4", 1), ("1/8 Triolen", 3)):
            self.res.addItem(txt, v)
        self.start = QDoubleSpinBox()
        self.start.setRange(0, 9999)
        self.start.setSuffix(" Takte")
        self.start.setDecimals(2)
        self.length = QSpinBox()
        self.length.setRange(1, 64)
        self.length.setValue(16)
        self.length.setSuffix(" Steps")
        self.transpose = QSpinBox()
        self.transpose.setRange(-48, 48)
        self.transpose.setSuffix(" Halbtöne")
        self.vel = QComboBox()
        self.vel.addItem("Anschlag aus der Datei", "original")
        self.vel.addItem("fester Anschlag (100)", "fest")
        self.quant = QSpinBox()
        self.quant.setRange(0, 100)
        self.quant.setValue(100)
        self.quant.setSuffix(" %")
        self.quant.setToolTip("Wie stark auf das Raster gezogen wird, bevor gerundet wird (100 % = hart)")
        self.drums = QCheckBox("als Drum-Grid (GM-Schlagzeug → Lanes)")
        self.drums.setToolTip("KICK 35/36, SNARE 38/40, CLAP 39, HAT 42/44/46, TOM 41–50, RIM 37, BELL/Becken 49–59")
        self.npat = QSpinBox()
        self.npat.setRange(1, 16)
        self.npat.setSuffix(" Pattern")
        self.npat.valueChanged.connect(self._npat_suffix)
        self.npat.setToolTip("Längere Dateien auf mehrere aufeinanderfolgende Patterns verteilen")
        self.song = QCheckBox("Song-Kette dafür anlegen")
        self.song.setToolTip("Legt am FM-1 einen Song an, der die Patterns nacheinander spielt. Die alte Song-Kette "
                             "wird vorher gelesen und lässt sich mit „Rückgängig“ zurückholen.")
        self.swing = QSpinBox()
        self.swing.setRange(-1, 100)
        self.swing.setValue(-1)
        self.swing.setSpecialValueText("unverändert")
        self.swing.setToolTip("SWING des Patterns am FM-1 setzen")
        self.track = QComboBox()
        for i in range(PF.NTRK):
            self.track.addItem(f"Spur {i + 1}" + (" (MIDI)" if i >= PF.NPART else ""), i)
        self.track.currentIndexChanged.connect(self._fill_patterns)
        self.pattern = QComboBox()
        self._fill_patterns()
        for wdg in (self.res, self.vel):
            wdg.activated.connect(self._preview)
        for wdg in (self.start, self.length, self.transpose, self.quant, self.npat):
            wdg.valueChanged.connect(self._preview)
        self.drums.toggled.connect(self._preview)
        f.addRow("Raster:", self.res)
        f.addRow("Ab:", self.start)
        f.addRow("Länge:", self.length)
        f.addRow("Aufteilen auf:", self.npat)
        f.addRow("Transponieren:", self.transpose)
        f.addRow("Anschlag:", self.vel)
        f.addRow("Quantisieren:", self.quant)
        f.addRow(self.drums)
        f.addRow(QLabel("<b>Ziel auf dem FM-1</b>"))
        f.addRow("Spur:", self.track)
        f.addRow("ab Pattern:", self.pattern)
        f.addRow("Swing:", self.swing)
        f.addRow(self.song)
        self.b_send = QPushButton(icon("go-up", "document-send"), "Auf das FM-1 spielen")
        self.b_send.clicked.connect(self._send)
        self.b_undo = QPushButton(icon("edit-undo"), "Rückgängig")
        self.b_undo.setEnabled(False)
        self.b_undo.clicked.connect(self._undo)
        row = QHBoxLayout()
        row.addWidget(self.b_send, 1)
        row.addWidget(self.b_undo)
        osc = QScrollArea()
        osc.setWidgetResizable(True)
        osc.setFrameShape(QScrollArea.Shape.NoFrame)
        osc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        osc.setWidget(opt)
        osc.setMinimumWidth(opt.minimumSizeHint().width() + 18)
        osc.setMinimumHeight(120)
        mid.addWidget(osc)
        prev_w = QWidget()
        pl = QVBoxLayout(prev_w)
        pl.setContentsMargins(0, 0, 0, 0)
        self.loss = QLabel("")
        self.loss.setWordWrap(True)
        pl.addWidget(self.loss)
        lead = QWidget()                       # „Vorschau Pattern: [n]“ vorne in der Anhören-Reihe (spart eine Zeile)
        prow = QHBoxLayout(lead)
        prow.setContentsMargins(0, 0, 0, 0)
        prow.addWidget(QLabel("Vorschau Pattern:"))
        self.show_chunk = QSpinBox()
        self.show_chunk.setRange(1, 1)
        self.show_chunk.valueChanged.connect(self._show_chunk)
        prow.addWidget(self.show_chunk)
        from .playback_bar import PlaybackBar
        self.bpm = 120.0
        self.play_bar = PlaybackBar(ctx, self._play_source, self, loop=True, play_text="Anhören", compact=True,
                                    lead=lead)
        self.play_bar.b_play.setToolTip("Das gezeigte Pattern anhören – auf dem FM-1 mit dem Klang der gewählten "
                                        "Spur, ohne FM-1 als Annäherung im Rechner")
        pl.addWidget(self.play_bar)
        self.roll = PianoRoll()
        sc = QScrollArea()
        sc.setWidget(self.roll)
        sc.setMinimumHeight(110)
        self.roll_scroll = sc
        pl.addWidget(sc, 1)
        vs = QSplitter(Qt.Orientation.Vertical)
        vs.setChildrenCollapsible(False)
        vs.addWidget(mid_w)
        vs.addWidget(prev_w)
        # die Vorschau bekommt den größeren Teil (bei 1280×720 sonst nur ~140 px für die Piano-Roll); die Optionen
        # rollen selbst und lassen sich ganz einklappen
        vs.setStretchFactor(0, 2)
        vs.setStretchFactor(1, 3)
        self._split_sized = False
        vs.splitterMoved.connect(lambda *_: setattr(self, "_split_sized", True))
        self.preview_split = vs
        self.opts_box = mid_w
        self.b_opts.toggled.connect(mid_w.setVisible)
        lay.addWidget(vs, 1)
        row.setStretchFactor(self.b_send, 0)
        row.insertStretch(0, 1)
        lay.addLayout(row)                     # Hauptknopf immer sichtbar – auch bei eingeklappten Optionen
        note = QLabel("Hinweis: Die Patterns werden überschrieben und auf dem FM-1 ausgewählt; vorher liest die App sie "
                      "für „Rückgängig“. Danach am FM-1 speichern (SAVE).")
        note.setWordWrap(True)
        lay.addWidget(note)
        ctx.changed.connect(self._dev_changed)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._size_split()

    def _size_split(self):
        """Startaufteilung Optionen/Vorschau, solange der Nutzer den Teiler nicht selbst verschoben hat: Vorschau
        so hoch wie die Piano-Roll braucht, höchstens 60 % und mindestens 55 % (wenn die Rolle das braucht)"""
        vs = self.preview_split
        if self._split_sized or not self.opts_box.isVisible():
            return
        total = sum(vs.sizes())
        if total <= 0:
            return
        need = self.roll.sizeHint().height() + self.loss.sizeHint().height() + 40
        pv = max(min(need, int(total * 0.6)), min(int(total * 0.55), need), 160)
        if total - pv < 120:
            return
        vs.blockSignals(True)
        vs.setSizes([total - pv, pv])
        vs.blockSignals(False)

    def dragEnterEvent(self, e):
        urls = e.mimeData().urls() if e.mimeData().hasUrls() else []
        if any(u.toLocalFile().lower().endswith((".mid", ".midi")) for u in urls):
            e.acceptProposedAction()

    def dropEvent(self, e):
        for u in e.mimeData().urls():
            f = u.toLocalFile()
            if f.lower().endswith((".mid", ".midi")):
                self._open(f)
                e.acceptProposedAction()
                return

    def _list_empty(self):
        it = QListWidgetItem("Noch keine MIDI-Datei – so geht's: oben „MIDI-Datei öffnen …“ wählen. Danach stehen "
                             "hier ihre Spuren; die gewählte Spur wird übertragen.")
        it.setFlags(Qt.ItemFlag.NoItemFlags)
        self.list.setWordWrap(True)
        self.list.addItem(it)

    def _fill_patterns(self, *_):
        k = self.track.currentData() or 0
        n = PF.NPAT if k < PF.NPART else PF.NPAT_MIDI
        cur = self.pattern.currentIndex()
        self.pattern.clear()
        self.pattern.addItem("aktuelles Pattern", None)
        for i in range(n):
            self.pattern.addItem(f"Pattern {i + 1}", i)
        self.pattern.setCurrentIndex(max(0, min(cur, n)))

    def _open(self, path: str | None = None):
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "MIDI-Datei", str(Path.home()), "MIDI (*.mid *.midi)")
            if not path:
                return
        try:
            size = Path(path).stat().st_size
        except OSError as e:
            show_error(self, "MIDI-Datei nicht lesbar", fs_error_text(e, "Nicht geöffnet"))
            return
        if size > 50 << 20:
            show_error(self, "Datei zu groß", f"{Path(path).name} ist {fmt_size(size)} groß – eine MIDI-Datei mit "
                                              "Noten ist selten größer als ein paar Megabyte. Bitte eine andere wählen.")
            return
        self.b_open.setEnabled(False)
        self.file_label.setText(f"lese {Path(path).name} …")

        def work(w):
            return MI.read_midi(Path(path), progress=lambda a, t: w.progress.emit(a, t))

        def done(res):
            self.b_open.setEnabled(True)
            self._loaded(path, *res)

        def fail(msg, tb):
            self.b_open.setEnabled(True)
            self.file_label.setText("Noch keine Datei – .mid wählen oder hierher ziehen.")
            show_error(self, "MIDI-Datei nicht lesbar", f"{Path(path).name}: {msg}")
        run_job(self, work, on_done=done, on_fail=fail,
                on_progress=lambda a, t: self.file_label.setText(f"lese {Path(path).name} … Spur {a + 1} von {t}"))

    def _play_source(self):
        """das gezeigte Pattern, gespielt auf der gewählten Zielspur (Tempo der MIDI-Datei)."""
        from .. import midiexport as ME
        from .playback_bar import Source
        if not self.chunks:
            return None
        c = self.chunks[min(len(self.chunks), self.show_chunk.value()) - 1]
        pat = PF.Pattern(0, steps=list(c), length=self.length.value())
        return Source(ME.pattern_events(pat, 0, 0), self.bpm, "Aus MIDI-Datei", None,
                      track_override=self.track.currentData() or 0, loop_quarters=ME.pattern_quarters(pat))

    def deactivate(self):
        self.play_bar.stop(quiet=True)

    def shutdown(self):
        self.play_bar.shutdown()

    def _loaded(self, path: str, tracks: list, bpm: float):
        self.tracks = tracks
        self.bpm = float(bpm or 120)
        self.path = str(path)
        self.file_label.setText(f"{Path(path).name} · {bpm:.0f} BPM · {plural(len(self.tracks), 'Spur', 'Spuren')} mit Noten")
        self.list.clear()
        for t in self.tracks:
            ch = f" · Kanal {t.channel + 1}" if t.channel is not None else ""
            drum = " · Schlagzeug" if t.channel == 9 else ""
            self.list.addItem(QListWidgetItem(f"{t.name}{ch}{drum} – {t.summary}"))
        self.b_send.setProperty("variant", "primary")
        self.b_open.setProperty("variant", "")
        for b in (self.b_send, self.b_open):
            b.style().unpolish(b)
            b.style().polish(b)
        if self.tracks:
            self.list.setCurrentRow(0)
        else:
            it = QListWidgetItem("Diese Datei enthält keine Noten – so geht's: in der DAW eine Spur mit Noten "
                                 "als MIDI exportieren (nicht nur Automation oder Tempo).")
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(it)
            self._preview()

    def _track_changed(self):
        i = self.list.currentRow()
        if 0 <= i < len(self.tracks):
            self.drums.blockSignals(True)
            self.drums.setChecked(self.tracks[i].channel == 9)
            self.drums.blockSignals(False)
            need = MI.patterns_needed(self.tracks[i].notes, self._opts())
            self.npat.blockSignals(True)
            self.npat.setValue(min(16, need))
            self.npat.blockSignals(False)
            self._npat_suffix(self.npat.value())         # (bei gesperrten Signalen sonst „16 Pattern“)
        self._preview()

    def _opts(self) -> MI.ImportOptions:
        return MI.ImportOptions(steps_per_beat=self.res.currentData(), start_beat=self.start.value() * 4,
                                length=self.length.value(), transpose=self.transpose.value(),
                                velocity=self.vel.currentData(), drums=self.drums.isChecked(),
                                quantize=self.quant.value(), patterns=self.npat.value())

    def _preview(self, *_):
        i = self.list.currentRow()
        if not 0 <= i < len(self.tracks):
            self.chunks, self.steps = [], []
            self.roll.set_steps([], self.length.value())
            self.loss.setText("")
            return
        self.chunks, self.report = MI.import_patterns(self.tracks[i].notes, self._opts())
        self.steps = self.chunks[0]
        self.show_chunk.setRange(1, len(self.chunks))
        self.loss.setText("<b>Was übertragen wird:</b> " + self.report.text())
        self._show_chunk()

    def _show_chunk(self, *_):
        if self.chunks:
            c = self.chunks[min(len(self.chunks), self.show_chunk.value()) - 1]
            self.roll.set_steps(c, self.length.value())
            self.roll.resize(self.roll.sizeHint())
            notes = [n for st in c[:self.length.value()] for n in st.notes if st.time == PF.ST_NOTE]
            if notes:                                    # die Noten ins Bild holen (sonst evtl. unter dem Rand)
                mid_y = 16 + (self.roll.hi - (max(notes) + min(notes)) // 2) * 12
                self.roll_scroll.ensureVisible(0, mid_y, 0, self.roll_scroll.viewport().height() // 2)

    def _npat_suffix(self, v: int):
        self.npat.setSuffix(" Pattern" if v == 1 else " Patterns")

    def _dev_changed(self):
        self.b_undo.setEnabled(bool(self.undo))
        self.b_undo.setToolTip("" if not self.undo or self.undo.matches(self.ctx) else self.undo.refusal())

    def _send(self):
        if not self.chunks:
            if not self.path:
                show_error(self, "Noch keine MIDI-Datei", "Es ist noch keine MIDI-Datei geladen – so geht's: oben "
                           "„MIDI-Datei öffnen …“ wählen, links eine Spur anklicken, dann hier auf das FM-1 spielen.")
            else:
                show_error(self, "Keine Spur gewählt", "Wähle links eine Spur der MIDI-Datei, die Noten enthält.")
            return
        if not self.ctx.require(self):
            return
        tr, first, length = self.track.currentData(), self.pattern.currentData(), self.length.value()
        npat = PF.NPAT if tr < PF.NPART else PF.NPAT_MIDI
        chunks = self.chunks
        if first is not None and first + len(chunks) > npat:
            show_error(self, "Zu viele Patterns", f"Ab Pattern {first + 1} passen nur {plural(npat - first, 'Pattern', 'Patterns')} "
                       f"auf Spur {tr + 1}. Bitte ein früheres Pattern wählen oder weniger aufteilen.")
            return
        if first is None and len(chunks) > 1:
            first = 0
        targets = [None] if first is None else list(range(first, first + len(chunks)))
        what = "das aktuelle Pattern" if first is None else \
            ", ".join(f"Pattern {b + 1}" for b in targets)
        song = self.song.isChecked() and first is not None and tr < PF.NPART
        if not confirm(self, "Patterns schreiben",
                       f"Auf dem FM-1 werden <b>Spur {tr + 1}: {what}</b> mit je {length} Steps überschrieben"
                       + (" und die Song-Kette durch eine Kette dieser Patterns ersetzt" if song else "") +
                       ".<br>Vorher liest die App " + ("die Patterns und die alte Song-Kette" if song else
                                                       "die Patterns") +
                       " (Rückgängig möglich" + ("; lässt sich die Song-Kette nicht lesen, bleibt sie unverändert"
                                                 if song else "") + "). Am FM-1 bitte vorher stoppen.",
                       "Schreiben", danger=True):
            return
        swing = None if self.swing.value() < 0 else self.swing.value()
        entry = {"track": tr, "patterns": [], "song": None}
        tag = device_tag(self.ctx)     # wächst Schritt für Schritt (auch bei Abbruch)

        def work(w, link):
            total = len(targets) * 128
            for j, (b, steps) in enumerate(zip(targets, chunks)):
                w.log.emit(f"Pattern {'(aktuell)' if b is None else b + 1}: lesen …")
                snap = MI.read_device_pattern(link, tr, b, progress=lambda a, t: w.progress.emit(j * 128 + a // 2, total))
                entry["patterns"].append(snap)          # vor dem Schreiben merken: auch halb geschriebene zurückholbar
                note = MI.write_device_pattern(link, tr, b, steps, length, swing=swing,
                                               progress=lambda a, t: w.progress.emit(j * 128 + 64 + a, total))
                if note:
                    w.log.emit(note)
            if song:
                rows = [([b if k == tr else 0 for k in range(4)], 1) for b in targets]
                try:
                    old = MI.read_song(link)
                except Exception:  # noqa: BLE001
                    old = None
                if old is None:
                    w.log.emit("Die alte Song-Kette ließ sich nicht lesen – sie bleibt deshalb unverändert. "
                               "Die Patterns sind geschrieben.")
                else:
                    entry["song"] = old
                    try:
                        rc = MI.write_song(link, rows)
                        w.log.emit("Song-Kette angelegt." if rc == 0 else f"Song-Kette abgelehnt (Code {rc}).")
                        if rc != 0:
                            entry["song"] = None
                    except Exception:  # noqa: BLE001
                        w.log.emit("Diese Firmware nimmt keine Song-Kette an – die Patterns sind trotzdem geschrieben.")
            w.log.emit("Fertig. Am FM-1 anhören und mit SAVE speichern.")
            return True

        def summary(_):
            n = len(entry["patterns"])
            txt = f"{plural(n, 'Pattern', 'Patterns')} auf Spur {tr + 1} geschrieben ({what})"
            if song:
                txt += "; Song-Kette ersetzt" if entry["song"] is not None else "; Song-Kette unverändert (nicht lesbar)"
            return txt + ". Alter Stand gesichert („Rückgängig“). Dauerhaft erst mit SAVE am FM-1."

        def failed(m):
            n = len(entry["patterns"])
            return (f"Abgebrochen: {m}. " + (f"{plural(n, 'Pattern', 'Patterns')} wurden vorher gelesen – „Rückgängig“ "
                                             "holt den alten Stand zurück." if n else "Am FM-1 wurde nichts geändert."))
        self.ctx.job(self, "MIDI → Pattern", work, done_text=summary, fail_text=failed)
        if entry["patterns"]:
            n = len(entry["patterns"])
            self.undo.push(self.ctx, entry, plural(n, "Pattern", "Patterns") + (" + Song-Kette" if entry["song"] else ""),
                           tag)
        self._dev_changed()

    def _undo(self):
        if not self.undo or not self.ctx.require(self) or not self.undo.check(self.ctx, self):
            return
        entry = self.undo.peek()
        snaps, song = entry["patterns"], entry["song"]
        if not confirm(self, "Rückgängig", f"{plural(len(snaps), 'Pattern', 'Patterns')} auf Spur {entry['track'] + 1}"
                       + (" und die Song-Kette" if song else "") + " wieder auf den Stand vor dem Schreiben setzen?",
                       "Wiederherstellen"):
            return
        done_steps = []

        def work(w, link):
            for s in reversed(snaps):
                MI.restore_snapshot(link, s)
                done_steps.append(s)
            if song is not None:
                rc = MI.restore_song(link, song)
                if rc:
                    raise MI.ProtocolError(f"Das FM-1 hat die alte Song-Kette abgelehnt (Code {rc}).")
                done_steps.append(song)
            return True

        d = self.ctx.job(self, "Rückgängig", work,
                         done_text=f"{plural(len(snaps), 'Pattern', 'Patterns')} auf Spur {entry['track'] + 1}"
                         + (" und die Song-Kette" if song else "") + " haben wieder den alten Stand.",
                         fail_text=lambda m: f"Nicht vollständig zurückgeholt: {m}. Der Rest bleibt rückgängig-bar.")
        if getattr(d, "ok", False):
            self.undo.pop()
        elif done_steps:              # teilweise zurückgeholt: Rest bleibt rückgängig-bar
            entry["patterns"] = [s for s in snaps if not any(s is x for x in done_steps)]
            if song is not None and any(song is x for x in done_steps):
                entry["song"] = None
            if not entry["patterns"] and entry["song"] is None:
                self.undo.pop()
        self._dev_changed()
