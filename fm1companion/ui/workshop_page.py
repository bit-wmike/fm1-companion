# SPDX-License-Identifier: GPL-3.0-only
"""Workshop: build on the computer, play on the FM-1 – MIDI file → pattern(s), also as a drum grid."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
                             QLabel, QListWidget, QListWidgetItem, QPushButton, QScrollArea, QSpinBox, QSplitter,
                             QVBoxLayout, QWidget)

from .. import midi_import as MI, projectfmt as PF
from .common import DeviceUndo, confirm, device_tag, fmt_size, fs_error_text, heading, icon, run_job, show_error
from .musik_widgets import StepRoll
from ..i18n import _, ngettext, pgettext


class PianoRoll(StepRoll):
    """(old name – the workshop now shows the shared piano roll with drum grid)"""


class WorkshopPage(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.tracks: list[MI.MidiTrackInfo] = []
        self.chunks: list[list] = []
        self.steps = []
        self.path: str = ""
        self.undo = DeviceUndo()        # {"track", "patterns": [PatternSnapshot], "song": SongSnapshot|None} per device
        self.report = MI.LossReport()
        lay = QVBoxLayout(self)
        lay.addWidget(heading(_("From MIDI file: MIDI → pattern"),
                              _("Write melodies, bass lines and beats on the computer (DAW, MuseScore, LMMS …), save "
                                "them as a MIDI file and play them here into patterns of an FM-1 track. Up to 64 steps "
                                "per pattern, up to 4 notes per step, held notes become TIE, drums become the drum "
                                "grid. Longer pieces are spread over several patterns.")))
        top = QHBoxLayout()
        self.b_open = b_open = QPushButton(icon("document-open"), _("Open MIDI file …"))
        b_open.setProperty("variant", "primary")
        b_open.setMinimumHeight(36)
        b_open.setToolTip(_("Choose a .mid file – or simply drag it onto this page"))
        b_open.clicked.connect(lambda: self._open())
        self.file_label = QLabel(_("No file yet – choose a .mid or drag it here."))
        self.file_label.setProperty("role", "muted")
        self.file_label.setWordWrap(True)
        top.addWidget(b_open)
        top.addWidget(self.file_label, 1)
        self.b_opts = QPushButton(icon("configure", "preferences-system"), "")
        self.b_opts.setCheckable(True)
        self.b_opts.setChecked(True)
        self.b_opts.setToolTip(_("Show or hide track choice and transfer – more room for the preview"))
        top.addWidget(self.b_opts)
        self.setAcceptDrops(True)
        lay.addLayout(top)
        mid_w = QWidget()                      # top: tracks + transfer, bottom: preview – adjustable in the splitter
        mid = QHBoxLayout(mid_w)
        mid.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.setToolTip(_("Track or channel of the MIDI file that is transferred"))
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setWordWrap(True)
        self.list.currentRowChanged.connect(lambda *_: self._track_changed())
        self._list_empty()
        mid.addWidget(self.list, 1)
        opt = QGroupBox(_("Transfer"))
        f = QFormLayout(opt)
        self.res = QComboBox()
        for txt, v in ((_("1/16 (default)"), 4), ("1/8", 2), ("1/32", 8), ("1/4", 1), (_("1/8 triplets"), 3)):
            self.res.addItem(txt, v)
        self.start = QDoubleSpinBox()
        self.start.setRange(0, 9999)
        self.start.setSuffix(_(" bars"))
        self.start.setDecimals(2)
        self.length = QSpinBox()
        self.length.setRange(1, 64)
        self.length.setValue(16)
        self.length.setSuffix(_(" steps"))
        self.transpose = QSpinBox()
        self.transpose.setRange(-48, 48)
        self.transpose.setSuffix(_(" semitones"))
        self.vel = QComboBox()
        self.vel.addItem(_("Velocity from the file"), "original")
        self.vel.addItem(_("fixed velocity (100)"), "fest")
        self.quant = QSpinBox()
        self.quant.setRange(0, 100)
        self.quant.setValue(100)
        self.quant.setSuffix(" %")
        self.quant.setToolTip(_("How strongly notes are pulled to the grid before rounding (100 % = hard)"))
        self.drums = QCheckBox(_("as drum grid (GM drums → lanes)"))
        self.drums.setToolTip(_("KICK 35/36, SNARE 38/40, CLAP 39, HAT 42/44/46, TOM 41–50, RIM 37, BELL/cymbals 49–59"))
        self.npat = QSpinBox()
        self.npat.setRange(1, 16)
        self.npat.setSuffix(ngettext(" pattern", " patterns", 1))
        self.npat.valueChanged.connect(self._npat_suffix)
        self.npat.setToolTip(_("Spread longer files over several consecutive patterns"))
        self.song = QCheckBox(_("Create a song chain for it"))
        self.song.setToolTip(_("Creates a song on the FM-1 that plays the patterns one after another. The old song "
                               "chain is read first and can be brought back with “Undo”."))
        self.swing = QSpinBox()
        self.swing.setRange(-1, 100)
        self.swing.setValue(-1)
        self.swing.setSpecialValueText(_("unchanged"))
        self.swing.setToolTip(_("Set the pattern's SWING on the FM-1"))
        self.track = QComboBox()
        for i in range(PF.NTRK):
            self.track.addItem(_("Track {n}").format(n=i + 1) + (" (MIDI)" if i >= PF.NPART else ""), i)
        self.track.currentIndexChanged.connect(self._fill_patterns)
        self.pattern = QComboBox()
        self._fill_patterns()
        for wdg in (self.res, self.vel):
            wdg.activated.connect(self._preview)
        for wdg in (self.start, self.length, self.transpose, self.quant, self.npat):
            wdg.valueChanged.connect(self._preview)
        self.drums.toggled.connect(self._preview)
        f.addRow(_("Grid:"), self.res)
        f.addRow(_("From:"), self.start)
        f.addRow(_("Length:"), self.length)
        f.addRow(_("Split into:"), self.npat)
        f.addRow(_("Transpose:"), self.transpose)
        f.addRow(_("Velocity:"), self.vel)
        f.addRow(_("Quantise:"), self.quant)
        f.addRow(self.drums)
        f.addRow(QLabel(_("<b>Target on the FM-1</b>")))
        f.addRow(_("Track:"), self.track)
        f.addRow(_("from pattern:"), self.pattern)
        f.addRow(_("Swing:"), self.swing)
        f.addRow(self.song)
        self.b_send = QPushButton(icon("go-up", "document-send"), _("Play onto the FM-1"))
        self.b_send.clicked.connect(self._send)
        self.b_undo = QPushButton(icon("edit-undo"), _("Undo"))
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
        lead = QWidget()                       # “Preview pattern: [n]” at the front of the listen row (saves a row)
        prow = QHBoxLayout(lead)
        prow.setContentsMargins(0, 0, 0, 0)
        prow.addWidget(QLabel(_("Preview pattern:")))
        self.show_chunk = QSpinBox()
        self.show_chunk.setRange(1, 1)
        self.show_chunk.valueChanged.connect(self._show_chunk)
        prow.addWidget(self.show_chunk)
        from .playback_bar import PlaybackBar
        self.bpm = 120.0
        self.play_bar = PlaybackBar(ctx, self._play_source, self, loop=True, play_text=_("Listen"), compact=True,
                                    lead=lead)
        self.play_bar.b_play.setToolTip(_("Listen to the shown pattern – on the FM-1 with the sound of the chosen "
                                          "track, without an FM-1 as an approximation on the computer"))
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
        # the preview gets the larger share (at 1280×720 otherwise only ~140 px for the piano roll); the options
        # scroll by themselves and can be collapsed entirely
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
        lay.addLayout(row)                     # main button always visible – even with collapsed options
        note = QLabel(_("Note: the patterns are overwritten and selected on the FM-1; the app reads them first for "
                        "“Undo”. Afterwards save on the FM-1 (SAVE)."))
        note.setWordWrap(True)
        lay.addWidget(note)
        ctx.changed.connect(self._dev_changed)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._size_split()

    def _size_split(self):
        """Initial options/preview split as long as the user hasn't moved the divider: preview
        as tall as the piano roll needs, at most 60 % and at least 55 % (if the roll needs it)"""
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
        it = QListWidgetItem(_("No MIDI file yet – how it works: choose “Open MIDI file …” above. Then its tracks "
                               "are listed here; the chosen track is transferred."))
        it.setFlags(Qt.ItemFlag.NoItemFlags)
        self.list.setWordWrap(True)
        self.list.addItem(it)

    def _fill_patterns(self, *_args):
        k = self.track.currentData() or 0
        n = PF.NPAT if k < PF.NPART else PF.NPAT_MIDI
        cur = self.pattern.currentIndex()
        self.pattern.clear()
        self.pattern.addItem(_("current pattern"), None)
        for i in range(n):
            self.pattern.addItem(f"Pattern {i + 1}", i)
        self.pattern.setCurrentIndex(max(0, min(cur, n)))

    def _open(self, path: str | None = None):
        if not path:
            path, _filter = QFileDialog.getOpenFileName(self, _("MIDI file"), str(Path.home()), "MIDI (*.mid *.midi)")
            if not path:
                return
        try:
            size = Path(path).stat().st_size
        except OSError as e:
            show_error(self, _("MIDI file not readable"), fs_error_text(e, _("Not opened")))
            return
        if size > 50 << 20:
            show_error(self, _("File too large"), _("{name} is {size} – a MIDI file with notes is rarely larger than a "
                                                    "few megabytes. Please choose another one.").format(
                name=Path(path).name, size=fmt_size(size)))
            return
        self.b_open.setEnabled(False)
        self.file_label.setText(_("reading {name} …").format(name=Path(path).name))

        def work(w):
            return MI.read_midi(Path(path), progress=lambda a, t: w.progress.emit(a, t))

        def done(res):
            self.b_open.setEnabled(True)
            self._loaded(path, *res)

        def fail(msg, tb):
            self.b_open.setEnabled(True)
            self.file_label.setText(_("No file yet – choose a .mid or drag it here."))
            show_error(self, _("MIDI file not readable"), f"{Path(path).name}: {msg}")
        run_job(self, work, on_done=done, on_fail=fail,
                on_progress=lambda a, t: self.file_label.setText(_("reading {name} … track {n} of {total}").format(
                    name=Path(path).name, n=a + 1, total=t)))

    def _play_source(self):
        """the shown pattern, played on the selected target track (tempo of the MIDI file)."""
        from .. import midiexport as ME
        from .playback_bar import Source
        if not self.chunks:
            return None
        c = self.chunks[min(len(self.chunks), self.show_chunk.value()) - 1]
        pat = PF.Pattern(0, steps=list(c), length=self.length.value())
        return Source(ME.pattern_events(pat, 0, 0), self.bpm, _("From MIDI file"), None,
                      track_override=self.track.currentData() or 0, loop_quarters=ME.pattern_quarters(pat))

    def deactivate(self):
        self.play_bar.stop(quiet=True)

    def shutdown(self):
        self.play_bar.shutdown()

    def _loaded(self, path: str, tracks: list, bpm: float):
        self.tracks = tracks
        self.bpm = float(bpm or 120)
        self.path = str(path)
        self.file_label.setText(ngettext("{name} · {bpm} BPM · {n} track with notes", "{name} · {bpm} BPM · {n} tracks with notes",
                                         len(self.tracks)).format(name=Path(path).name, bpm=f"{bpm:.0f}", n=len(self.tracks)))
        self.list.clear()
        for t in self.tracks:
            ch = " · " + _("channel {n}").format(n=t.channel + 1) if t.channel is not None else ""
            drum = " · " + _("drums") if t.channel == 9 else ""
            self.list.addItem(QListWidgetItem(f"{t.name}{ch}{drum} – {t.summary}"))
        self.b_send.setProperty("variant", "primary")
        self.b_open.setProperty("variant", "")
        for b in (self.b_send, self.b_open):
            b.style().unpolish(b)
            b.style().polish(b)
        if self.tracks:
            self.list.setCurrentRow(0)
        else:
            it = QListWidgetItem(_("This file contains no notes – how it works: export a track with notes as MIDI "
                                   "in the DAW (not only automation or tempo)."))
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
            self._npat_suffix(self.npat.value())         # (otherwise “16 Pattern” with blocked signals)
        self._preview()

    def _opts(self) -> MI.ImportOptions:
        return MI.ImportOptions(steps_per_beat=self.res.currentData(), start_beat=self.start.value() * 4,
                                length=self.length.value(), transpose=self.transpose.value(),
                                velocity=self.vel.currentData(), drums=self.drums.isChecked(),
                                quantize=self.quant.value(), patterns=self.npat.value())

    def _preview(self, *_args):
        i = self.list.currentRow()
        if not 0 <= i < len(self.tracks):
            self.chunks, self.steps = [], []
            self.roll.set_steps([], self.length.value())
            self.loss.setText("")
            return
        self.chunks, self.report = MI.import_patterns(self.tracks[i].notes, self._opts())
        self.steps = self.chunks[0]
        self.show_chunk.setRange(1, len(self.chunks))
        self.loss.setText(_("<b>What is transferred:</b> {report}").format(report=self.report.text()))
        self._show_chunk()

    def _show_chunk(self, *_args):
        if self.chunks:
            c = self.chunks[min(len(self.chunks), self.show_chunk.value()) - 1]
            self.roll.set_steps(c, self.length.value())
            self.roll.resize(self.roll.sizeHint())
            notes = [n for st in c[:self.length.value()] for n in st.notes if st.time == PF.ST_NOTE]
            if notes:                                    # bring the notes into view (otherwise possibly below the edge)
                mid_y = 16 + (self.roll.hi - (max(notes) + min(notes)) // 2) * 12
                self.roll_scroll.ensureVisible(0, mid_y, 0, self.roll_scroll.viewport().height() // 2)

    def _npat_suffix(self, v: int):
        self.npat.setSuffix(ngettext(" pattern", " patterns", v))

    def _dev_changed(self):
        self.b_undo.setEnabled(bool(self.undo))
        self.b_undo.setToolTip("" if not self.undo or self.undo.matches(self.ctx) else self.undo.refusal())

    def _send(self):
        if not self.chunks:
            if not self.path:
                show_error(self, _("No MIDI file yet"), _("No MIDI file is loaded yet – how it works: choose “Open MIDI "
                                                          "file …” above, click a track on the left, then play it onto "
                                                          "the FM-1 here."))
            else:
                show_error(self, _("No track chosen"), _("Choose a track of the MIDI file on the left that contains notes."))
            return
        if not self.ctx.require(self):
            return
        tr, first, length = self.track.currentData(), self.pattern.currentData(), self.length.value()
        npat = PF.NPAT if tr < PF.NPART else PF.NPAT_MIDI
        chunks = self.chunks
        if first is not None and first + len(chunks) > npat:
            show_error(self, _("Too many patterns"), ngettext(
                "From pattern {first} only {n} pattern fits on track {track}. Please choose an earlier pattern or split "
                "into fewer.",
                "From pattern {first} only {n} patterns fit on track {track}. Please choose an earlier pattern or split "
                "into fewer.", npat - first).format(first=first + 1, n=npat - first, track=tr + 1))
            return
        if first is None and len(chunks) > 1:
            first = 0
        targets = [None] if first is None else list(range(first, first + len(chunks)))
        what = _("the current pattern") if first is None else \
            ", ".join(f"Pattern {b + 1}" for b in targets)
        song = self.song.isChecked() and first is not None and tr < PF.NPART
        if song:
            text = _("On the FM-1, <b>track {track}: {what}</b> will be overwritten with {n} steps each and the song "
                     "chain replaced by a chain of these patterns.<br>First the app reads the patterns and the old song "
                     "chain (undo possible; if the song chain cannot be read, it stays unchanged). Please stop the "
                     "FM-1 first.")
        else:
            text = _("On the FM-1, <b>track {track}: {what}</b> will be overwritten with {n} steps each.<br>First the "
                     "app reads the patterns (undo possible). Please stop the FM-1 first.")
        if not confirm(self, _("Write patterns"), text.format(track=tr + 1, what=what, n=length),
                       _("Write"), danger=True):
            return
        swing = None if self.swing.value() < 0 else self.swing.value()
        entry = {"track": tr, "patterns": [], "song": None}
        tag = device_tag(self.ctx)     # grows step by step (also on cancel)

        def work(w, link):
            total = len(targets) * 128
            for j, (b, steps) in enumerate(zip(targets, chunks)):
                w.log.emit(_("Pattern {pattern}: reading …").format(pattern=_("(current)") if b is None else b + 1))
                snap = MI.read_device_pattern(link, tr, b, progress=lambda a, t: w.progress.emit(j * 128 + a // 2, total))
                entry["patterns"].append(snap)          # remember before writing: even half-written ones can be restored
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
                    w.log.emit(_("The old song chain could not be read – so it stays unchanged. The patterns are "
                                 "written."))
                else:
                    entry["song"] = old
                    try:
                        rc = MI.write_song(link, rows)
                        w.log.emit(_("Song chain created.") if rc == 0 else _("Song chain refused (code {code}).").format(code=rc))
                        if rc != 0:
                            entry["song"] = None
                    except Exception:  # noqa: BLE001
                        w.log.emit(_("This firmware does not accept a song chain – the patterns are written anyway."))
            w.log.emit(_("Done. Listen on the FM-1 and store it with SAVE."))
            return True

        def summary(_res):
            n = len(entry["patterns"])
            txt = ngettext("{n} pattern written to track {track} ({what})", "{n} patterns written to track {track} ({what})",
                           n).format(n=n, track=tr + 1, what=what)
            if song:
                txt += _("; song chain replaced") if entry["song"] is not None else _("; song chain unchanged (not readable)")
            return txt + _(". Old state saved (“Undo”). Only permanent with SAVE on the FM-1.")

        def failed(m):
            n = len(entry["patterns"])
            return (_("Cancelled: {error}.").format(error=m) + " " + (ngettext(
                "{n} pattern was read before – “Undo” brings back the old state.",
                "{n} patterns were read before – “Undo” brings back the old state.", n).format(n=n) if n
                else _("Nothing has been changed on the FM-1.")))
        self.ctx.job(self, "MIDI → Pattern", work, done_text=summary, fail_text=failed)
        if entry["patterns"]:
            n = len(entry["patterns"])
            self.undo.push(self.ctx, entry, ngettext("{n} pattern", "{n} patterns", n).format(n=n)
                           + (" + " + _("song chain") if entry["song"] else ""), tag)
        self._dev_changed()

    def _undo(self):
        if not self.undo or not self.ctx.require(self) or not self.undo.check(self.ctx, self):
            return
        entry = self.undo.peek()
        snaps, song = entry["patterns"], entry["song"]
        n, track = len(snaps), entry['track'] + 1
        if song:
            question = ngettext("Set {n} pattern on track {track} and the song chain back to the state before writing?",
                                "Set {n} patterns on track {track} and the song chain back to the state before writing?", n)
        else:
            question = ngettext("Set {n} pattern on track {track} back to the state before writing?",
                                "Set {n} patterns on track {track} back to the state before writing?", n)
        if not confirm(self, _("Undo"), question.format(n=n, track=track), pgettext("undo", "Restore")):
            return
        done_steps = []

        def work(w, link):
            for s in reversed(snaps):
                MI.restore_snapshot(link, s)
                done_steps.append(s)
            if song is not None:
                rc = MI.restore_song(link, song)
                if rc:
                    raise MI.ProtocolError(_("The FM-1 refused the old song chain (code {code}).").format(code=rc))
                done_steps.append(song)
            return True

        if song:
            done = ngettext("{n} pattern on track {track} and the song chain have the old state again.",
                            "{n} patterns on track {track} and the song chain have the old state again.", n)
        else:
            done = ngettext("{n} pattern on track {track} has the old state again.",
                            "{n} patterns on track {track} have the old state again.", n)
        d = self.ctx.job(self, _("Undo"), work, done_text=done.format(n=n, track=track),
                         fail_text=lambda m: _("Not completely brought back: {error}. The rest can still be "
                                               "undone.").format(error=m))
        if getattr(d, "ok", False):
            self.undo.pop()
        elif done_steps:              # partially restored: the rest remains undoable
            entry["patterns"] = [s for s in snaps if not any(s is x for x in done_steps)]
            if song is not None and any(song is x for x in done_steps):
                entry["song"] = None
            if not entry["patterns"] and entry["song"] is None:
                self.undo.pop()
        self._dev_changed()
