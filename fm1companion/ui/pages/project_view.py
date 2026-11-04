# SPDX-License-Identifier: GPL-3.0-only
"""Project view: view and export Melodee projects from backups, project files or directly from the FM-1."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QUrl
import time
from PyQt6.QtGui import QBrush, QColor, QDesktopServices, QFont, QKeySequence, QShortcut
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QGridLayout, QHBoxLayout,
                             QHeaderView, QLabel, QLineEdit, QScrollArea, QSlider, QSpinBox, QSplitter,
                             QStackedWidget, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from ... import config, midiexport as ME, projectfmt as PF
from ..common import (Card, FlowLayout, button, confirm, empty_state, flow_row, fs_error_text, heading, label, safe_folder,  # noqa: F401,E501
                      show_error, start_dir)
from ..musik_widgets import StepRoll
from ..playback_bar import DeviceBadge, PlaybackBar, Source, device_name
from ..responsive import reflow
from . import PageSpec
from ...i18n import _, ngettext, pgettext

SETTINGS = {"lane_notes": ",".join(str(n) for n in PF.LANE_GM), "drum_channel": 0, "mode": "song", "loops": 4,
            "last_dir": ""}


def _settings():
    return config.module("musik", dict(SETTINGS))


def bank_text(v: int) -> str:
    return _("Rest") if v == PF.CHAIN_REST else _("hold") if v == PF.CHAIN_HOLD else f"P{v + 1}"


class ProjectView(QWidget):
    def __init__(self, ctx, window=None):
        super().__init__()
        self.ctx, self.window = ctx, window
        self.items: list[tuple[str, PF.Project]] = []
        self.prj: PF.Project | None = None
        self.cur = (0, 0)
        self.source = ""
        self.source_note = ""
        lay = QVBoxLayout(self)
        lay.addWidget(heading(_("Project View"), _("View and listen to Melodee projects (on the FM-1 with its own "
                                                   "sounds or on the computer) and export them as a MIDI file for "
                                                   "Audacity, LMMS, Ardour or MuseScore.")))
        self.live_tag = None                       # (profile ID, port, name) when a device's running music is open
        self.live_slot = None
        open_card = Card(_("1 · Open project"))
        top = FlowLayout()
        top.addWidget(button(_("Open backup …"), ("document-open",), slot=self.open_backup,
                             tooltip=_("Open a backup from ~/FM-1/Backups (all projects in it) – Ctrl+O")))
        top.addWidget(button(_("Open project file …"), ("document-open-folder", "folder"), slot=self.open_project,
                             tooltip=_("Open a .fm1proj file from the project library")))
        sep = QLabel("  " + _("or live from the FM-1:"))
        sep.setProperty("role", "muted")
        top.addWidget(sep)
        self.slot = QSpinBox()
        self.slot.setRange(0, 32)
        self.slot.setSpecialValueText(_("running music"))
        self.slot.setPrefix(_("Project "))
        self.slot.setToolTip(_("What is read from the active FM-1: the running music or a saved project slot"))
        top.addWidget(self.slot)
        self.b_read = button(_("Read from FM-1"), ("go-down", "document-import"), "primary", self.read_device,
                             tooltip=_("Reads the project from the active FM-1 (at the top of the bar) with the backup "
                                       "commands – read only, nothing is changed on the FM-1"))
        top.addWidget(self.b_read)
        self.choice = QComboBox()
        self.choice.setMinimumWidth(220)
        self.choice.setToolTip(_("Project in the opened file"))
        self.choice.currentIndexChanged.connect(self._choose)
        self.choice.hide()                         # only visible once a file with projects is open
        top.addWidget(self.choice)
        open_card.lay.addLayout(top)
        self.badge = DeviceBadge(ctx, _("Live reading and playback go to"))
        open_card.lay.addWidget(self.badge)
        lay.addWidget(open_card)
        self.stack = QStackedWidget()
        self.empty = empty_state("view-media-playlist", _("No project opened yet"),
                                 _("How it works: open a backup (device backups are in ~/FM-1/Backups), choose a "
                                   "project file from the library or read the running music directly from the FM-1."),
                                 _("Open backup …"), self.open_backup)
        self.stack.addWidget(self.empty)
        self.stack.addWidget(self._build_main())
        lay.addWidget(self.stack, 1)
        if getattr(ctx, "changed", None) is not None:
            ctx.changed.connect(self._update_song_row)
        QShortcut(QKeySequence("Ctrl+O"), self, activated=self.open_backup)
        QShortcut(QKeySequence("Ctrl+E"), self, activated=self.export_midi)

    # ---------------------------------------------------------------------------------------- Layout
    def _build_main(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        self.info = label("")
        self.info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(self.info)
        split = QSplitter(Qt.Orientation.Vertical)
        tabs = QTabWidget()
        self.tracks = QTableWidget(0, 8)
        self.tracks.setHorizontalHeaderLabels([_("Track"), "Engine", "Preset", _("Vol."), "Pan", _("Mute"), "Pattern",
                                               _("MIDI channel")])
        self.tracks.verticalHeader().hide()
        self.tracks.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tracks.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.tracks.horizontalHeader().setStretchLastSection(True)
        tabs.addTab(self.tracks, _("Tracks"))
        self.grid = QTableWidget(0, PF.NPAT)
        self.grid.setHorizontalHeaderLabels([str(i + 1) for i in range(PF.NPAT)])
        self.grid.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.grid.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.grid.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.grid.cellClicked.connect(self._cell)
        self.grid.setToolTip(_("Patterns per track: filled = in use, bold = active. A click shows the notes."))
        tabs.addTab(self.grid, _("Pattern overview"))
        self.song = QTableWidget(0, 2)
        self.song.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.song.verticalHeader().hide()
        tabs.addTab(self.song, "Song")
        tabs.setCurrentIndex(1)
        split.addWidget(tabs)
        rollbox = QWidget()
        rv = QVBoxLayout(rollbox)
        rv.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        self.roll_title = QLabel("")
        f = QFont(self.roll_title.font())
        f.setBold(True)
        self.roll_title.setFont(f)
        bar.addWidget(self.roll_title, 1)
        bar.addWidget(QLabel(_("Zoom:")))
        self.zoom = QSlider(Qt.Orientation.Horizontal)
        self.zoom.setRange(8, 48)
        self.zoom.setValue(20)
        self.zoom.setMaximumWidth(160)
        self.zoom.setToolTip(_("Width of one step"))
        self.zoom.valueChanged.connect(lambda v: (self.roll.set_zoom(v), self.roll.resize(self.roll.sizeHint())))
        bar.addWidget(self.zoom)
        rv.addLayout(bar)
        self.roll = StepRoll()
        sc = QScrollArea()
        sc.setWidget(self.roll)
        sc.setWidgetResizable(False)
        sc.setMinimumHeight(130)
        self.roll_scroll = sc
        rv.addWidget(sc, 1)
        split.addWidget(rollbox)
        split.setStretchFactor(1, 2)
        v.insertWidget(1, self._build_play())           # listening right below the project info – visible without scrolling
        v.addWidget(split, 1)
        v.addWidget(self._build_export())
        return w

    def _build_play(self) -> QWidget:
        card = Card(_("2 · Listen"))
        s = _settings()
        self.mode = QComboBox()
        self.mode.addItem(_("Song (sections one after another)"), "song")
        self.mode.addItem(_("active patterns in a loop"), "active")
        self.mode.addItem(_("only the shown pattern"), "pattern")
        self.mode.setCurrentIndex(max(0, self.mode.findData(s.get("mode", "song"))))
        self.mode.setToolTip(_("What is played and exported"))
        self.loops = QSpinBox()
        self.loops.setRange(1, 64)
        self.loops.setValue(int(s.get("loops", 4)))
        self.loops.setSuffix(_(" passes"))
        self.loops.setToolTip(_("How often the active patterns run (sequence “active patterns”)"))
        self.muted = QCheckBox(_("play muted tracks too"))
        self.muted.setChecked(True)
        self.muted.setToolTip(_("Play and export tracks that are muted in the project anyway"))
        sel = FlowLayout()
        sel.addWidget(QLabel(_("Selection:")))
        sel.addWidget(self.mode)
        sel.addWidget(self.loops)
        sel.addWidget(self.muted)
        card.lay.addLayout(sel)
        self.player_bar = PlaybackBar(self.ctx, self.play_source, self, play_text=_("Play"),
                                      shortcut_hint=_("Space"))
        card.lay.addWidget(self.player_bar)
        self.b_song = button(_("Start the song on the FM-1 itself"), ("media-playback-start", "media-seek-forward"), "",
                             self.device_song, _("The FM-1 plays its running music itself (its song, exactly its "
                                                 "sound). Only for the running music of the active FM-1 just read; "
                                                 "nothing is saved."))
        self.b_song_stop = button(_("Stop FM-1"), ("media-playback-stop",), "", self.device_stop,
                                  _("Stop the transport of the FM-1 (like the STOP button)"))
        self.song_from = QSpinBox(minimum=1, maximum=1, value=1)
        self.song_from.setPrefix(_("from section "))
        self.song_from.setToolTip(_("Section of the song the FM-1 starts with (like PLAY at the SONG cursor). From "
                                    "Melodee 1.6.1 with CLK INT, following devices then get Song Position + Continue."))
        self.song_row = flow_row(self.b_song, self.song_from, self.b_song_stop)
        self.song_row.hide()
        card.lay.addWidget(self.song_row)
        QShortcut(QKeySequence("Space"), self, activated=self._toggle_play)
        return card

    def _toggle_play(self):
        if self.player_bar.playing:
            self.player_bar.stop()
        else:
            self.player_bar.play()

    def play_source(self) -> Source | None:
        if not self.prj or not self.prj.ok:
            return None
        o = self.options()
        pat = self.cur if o.mode == "pattern" else None
        evs = ME.events(self.prj, o, pat)
        loop = None
        if o.mode == "pattern":
            t = self.prj.tracks[self.cur[0]]
            loop = ME.pattern_quarters(t.patterns[self.cur[1]], self.prj.swing)
        elif evs:
            loop = max(e.start + e.length for e in evs)
            loop = float(int(loop + 3.999) // 4 * 4) or None   # to whole bars
        return Source(evs, self.prj.bpm or 120, self.prj.name or self.source, self.prj, loop_quarters=loop,
                      live_tag=self.live_tag)

    def _update_song_row(self):
        from ..common import device_tag, same_device
        live = bool(self.live_tag and self.live_slot == 0 and self.prj and self.prj.ok and self.prj.song and
                    getattr(self.ctx, "connected", False) and same_device(self.live_tag, device_tag(self.ctx)))
        self.song_row.setVisible(live)
        if live:
            self.song_from.setMaximum(max(1, len(self.prj.song)))

    def device_song(self):
        """running music: the FM-1 plays its own song (BANK_SONG start) – no flash access."""
        if not self.ctx.require(self):
            return
        from ... import playback as P
        self.player_bar.stop(quiet=True)
        name = device_name(self.ctx)
        row = max(0, self.song_from.value() - 1)
        version = self.ctx.info.version if getattr(self.ctx, "info", None) else ""

        def work(w, link):
            rc, rows = P.song_start(link, row)
            return rc, rows, (P.read_clock(link) if rc == 0 and P.sends_clock(version) else None)

        def done(res):
            rc, rows, clk = res
            sync = P.song_sync_note(version, clk, row) if rc == 0 else ""
            if rc != 0:
                text = _("“{name}” refused the start (code {code}) – is a song set up? Check SONG on the FM-1 or use "
                         "“Play”.").format(name=name, code=rc)
            elif row:
                text = ngettext("“{name}” plays its song itself from section {section} ({n} section) – exactly its "
                                "sound. “Stop FM-1” stops it.",
                                "“{name}” plays its song itself from section {section} ({n} sections) – exactly its "
                                "sound. “Stop FM-1” stops it.", rows).format(name=name, section=row + 1, n=rows)
            else:
                text = ngettext("“{name}” plays its song itself ({n} section) – exactly its sound. “Stop FM-1” stops it.",
                                "“{name}” plays its song itself ({n} sections) – exactly its sound. “Stop FM-1” stops it.",
                                rows).format(name=name, n=rows)
            self.player_bar.status.setText(text + (" " + sync if sync else ""))
        self.ctx.job(self, _("Start song"), work, dialog=False, on_done=done,
                     on_fail=lambda m, tb: self.player_bar.status.setText(_("Start not possible: {error}").format(error=m)))

    def device_stop(self):
        if not self.ctx.require(self):
            return
        from ... import playback as P
        self.ctx.job(self, _("Stop FM-1"), lambda w, link: P.transport_stop(link), dialog=False,
                     on_done=lambda ok: self.player_bar.status.setText(_("FM-1 stopped.") if ok else
                                                                        _("The FM-1 did not answer.")),
                     on_fail=lambda m, tb: self.player_bar.status.setText(_("Not stopped: {error}").format(error=m)))

    def deactivate(self):
        self.player_bar.stop(quiet=True)

    def shutdown(self):
        self.player_bar.shutdown()

    def _build_export(self) -> QWidget:
        card = Card(_("3 · Export"))
        g = QGridLayout()
        s = _settings()
        self.drumch = QComboBox()
        self.drumch.addItem(_("Drums on the track's channel"), 0)
        self.drumch.addItem(_("Drums on channel 10 (GM)"), 1)
        self.drumch.setCurrentIndex(int(s.get("drum_channel", 0)))
        self.lanes = QLineEdit(s.get("lane_notes", SETTINGS["lane_notes"]))
        self.lanes.setToolTip(_("GM notes of the 8 drum lanes: KICK, SNARE, CLAP, HAT CL, HAT OP, TOM, RIM, BELL"))
        g.addWidget(label(_("Exports the selection from “Listen”."), "muted"), 0, 0, 1, 2)
        g.addWidget(self.drumch, 0, 3)
        g.addWidget(QLabel(_("Lane notes:")), 1, 0)
        g.addWidget(self.lanes, 1, 1)
        row = QHBoxLayout()
        self.b_midi = button(_("MIDI file …"), ("audio-midi", "document-save"), "", self.export_midi,
                             _("Standard MIDI File (type 1): one track per FM-1 track, channel as on the FM-1 (Ctrl+E)"))
        row.addWidget(self.b_midi)
        row.addWidget(button("CSV …", ("text-csv", "document-save"), slot=self.export_csv,
                             tooltip=_("All notes as a table (spreadsheet)")))
        row.addWidget(button("JSON …", ("document-save",), slot=self.export_json,
                             tooltip=_("The whole project as a readable JSON file")))
        row.addWidget(button(_("Image …"), ("image-x-generic", "document-save"), slot=self.export_png,
                             tooltip=_("The piano roll as a PNG image")))
        row.addWidget(button(_("Open export folder"), ("folder-open",), slot=self.open_folder,
                             tooltip=_("Open the folder ~/FM-1/MIDI-Export in the file manager (drag from there "
                                       "into Audacity or a DAW)")))
        row.addStretch(1)
        g.addLayout(row, 2, 0, 1, 4)
        reflow(row)                                      # export buttons wrap when the window is narrow
        card.lay.addLayout(g)
        return card

    # ---------------------------------------------------------------------------------------- Load
    def open_backup(self, path: str | None = None):
        if not path:
            start = str(config.load().backups_dir) if hasattr(config.load(), "backups_dir") else str(Path.home())
            path, _filter = QFileDialog.getOpenFileName(self, _("Open backup"), start, _("FM-1 backup (*.json)"))
            if not path:
                return
        self._load_path(Path(path))

    def open_project(self, path: str | None = None):
        if not path:
            path, _filter = QFileDialog.getOpenFileName(self, _("Open project file"), str(start_dir("Projekte")),  # i18n: ignore
                                                        _("FM-1 project (*.fm1proj)"))
            if not path:
                return
        self._load_path(Path(path))

    def _load_path(self, path: Path):
        try:
            items = PF.load_any(path)
        except Exception as e:  # noqa: BLE001
            show_error(self, _("File not readable"), _("{name} could not be opened:\n{error}").format(name=path.name, error=e))
            return
        if not items:
            show_error(self, _("No projects"), _("There is no project saved in {name}.").format(name=path.name))
            return
        self.source = path.stem
        self.source_note = _("File {name}").format(name=path.name)
        self.live_tag = self.live_slot = None
        self.set_items(items)

    def open_with(self, path=None, data: bytes | None = None, title: str = "", firmware: str = "",
                  read_slot: int | None = None, **_kw):
        """from other pages: show a file/data – or read a slot of the active FM-1 with read_slot."""
        if read_slot is not None:
            self.slot.setValue(max(0, min(self.slot.maximum(), int(read_slot))))
            self.read_device()
        elif data is not None:
            self.source = title or _("Project")
            self.source_note = title or _("Project")
            self.live_tag = self.live_slot = None
            self.set_items([(title or _("Project"), PF.decode(data, firmware))])
        elif path:
            self._load_path(Path(path))

    def read_device(self):
        if not self.ctx.require(self):
            return
        from ... import backup
        slot = self.slot.value()
        oid = backup.project_id(slot)
        fw = getattr(self.ctx, "firmware_tag", "") or ""
        key = getattr(self.ctx, "firmware_key", "") or ""
        if key == "sloop":
            if slot > 4:
                show_error(self, _("SLOOP only has 4 slots"), _("SLOOP saves projects as sections A–D (slots 1–4). "
                                                                "Please choose 0 (running project) or 1–4."))
                return
            show_error(self, _("SLOOP projects"), _("The project view only knows the Melodee format. SLOOP projects are "
                                                    "built differently – to view them use the SLOOP web editor (page "
                                                    "“Editor”). Backing up works as usual via backups."))
            return
        if key and key not in ("melodee", "skiff", "felucca"):
            from ..device import firmware_name
            show_error(self, _("Other firmware"), _("{firmware} has no projects the app can read. The project view shows "
                                                    "Melodee projects.").format(
                firmware=firmware_name(key) or _("This firmware")))
            return

        def work(w, link):
            man = {o.id: o for o in backup.list_objects(link)}
            o = man.get(oid)
            if not o or not o.size:
                raise backup.BackupError(_("Project slot {slot} is empty – nothing to read.").format(slot=slot) if slot
                                         else _("No running music found."))
            w.log.emit(_("Reading {n} bytes …").format(n=o.size))
            done = [0]

            def prog(n):
                done[0] += n
                w.progress.emit(done[0], o.size)
            return backup.read_object(link, o, prog)

        from ..common import device_tag
        tag = device_tag(self.ctx)
        dev = device_name(self.ctx) or "FM-1"
        fwl = self.ctx.info.version if getattr(self.ctx, "info", None) else fw

        def done(data):
            if data:
                what = _("Running music") if not slot else _("Project {n}").format(n=slot)
                title = _("{what} from “{device}”").format(what=what, device=dev)
                self.source = title
                self.source_note = (_("read live from <b>“{device}”</b>").format(device=dev) + (f" ({fwl})" if fwl else "")
                                    + " " + _("at {time} – {what}").format(time=time.strftime('%H:%M'), what=what))
                self.live_tag, self.live_slot = tag, slot
                self.set_items([(title, PF.decode(data, fw))])
        self.ctx.job(self, _("Read project from “{device}”").format(device=dev), work, on_done=done,
                     done_text=lambda d: _("{what} read from “{device}” ({n} bytes) and opened – only viewed, nothing "
                                           "was changed on the FM-1.").format(
                         what=_("Running music") if not slot else _("Project {n}").format(n=slot), device=dev, n=len(d)))

    def set_items(self, items):
        self.items = items
        self.choice.blockSignals(True)
        self.choice.clear()
        for title, prj in items:
            self.choice.addItem(title + ("" if prj.ok else " " + _("(not readable)")))
        self.choice.blockSignals(False)
        self.choice.setVisible(bool(items))
        first = next((i for i, (t, p) in enumerate(items) if p.ok), 0)
        self.choice.setCurrentIndex(first)
        self._choose(first)

    def _choose(self, i: int):
        if not 0 <= i < len(self.items):
            return
        self.prj = self.items[i][1]
        self.stack.setCurrentIndex(1)
        self.show_project()

    # ---------------------------------------------------------------------------------------- Display
    def show_project(self):
        prj = self.prj
        if not prj.ok:
            if prj.format.startswith("SLOOP"):
                self.info.setText(f"<b>{prj.format}</b> – {'; '.join(prj.warnings)}")
            else:
                self.info.setText(_("<b>This project could not be read.</b> Format: {format}. {warnings}<br>What "
                                    "works: the project stays in the backup and can be restored as usual – only the "
                                    "view is missing.").format(format=prj.format, warnings='; '.join(prj.warnings)))
            self.tracks.setRowCount(0)
            self.grid.setRowCount(0)
            self.song.setRowCount(0)
            self.roll.set_steps([], 16)
            self.roll_title.setText("")
            return
        warn = ("<br><i>" + _("Note: {warnings}").format(warnings='; '.join(prj.warnings)) + "</i>") if prj.warnings else ""
        name = f"<b>{prj.name}</b> · " if prj.name else ""
        src = ("<br>" + _("Source: {source}").format(source=self.source_note)) if getattr(self, "source_note", "") else ""
        self.info.setText(name + ngettext("{bpm} BPM · Swing {swing} · {n} track · Format {format}",
                                          "{bpm} BPM · Swing {swing} · {n} tracks · Format {format}",
                                          len(prj.used_tracks)).format(bpm=prj.bpm, swing=prj.swing,
                                                                        n=len(prj.used_tracks), format=prj.format)
                          + f"{warn}{self.extras_note(prj)}{src}")
        self._update_song_row()
        used = prj.used_tracks
        self.tracks.setRowCount(len(used))
        for r, t in enumerate(used):
            preset = t.preset_name or (f"Preset {t.preset + 1}" if t.preset < 0xFE else "")
            if t.user_slot:
                preset += " " + _("(library U{slot:03d})").format(slot=t.user_slot)
            vals = [t.name, t.engine_name, preset, str(t.level), str(t.pan), _("yes") if t.mute else "",
                    f"P{t.active + 1}", str(t.midi_channel + 1)]
            for c, v in enumerate(vals):
                self.tracks.setItem(r, c, QTableWidgetItem(v))
        self.grid.setRowCount(len(used))
        self.grid.setVerticalHeaderLabels([t.name for t in used])
        hl = self.palette().color(self.palette().ColorRole.Highlight)
        for r, t in enumerate(used):
            for b in range(PF.NPAT):
                it = QTableWidgetItem("")
                if b < len(t.patterns):
                    p = t.patterns[b]
                    if p.used:
                        c = QColor(hl)
                        c.setAlpha(200 if b == t.active else 110)
                        it.setBackground(QBrush(c))
                        it.setText(("●" if b == t.active else "") + ("D" if p.has_drums else "♪"))
                        sid = prj.pattern_sounds.get((t.index, b), 0)
                        it.setToolTip(_("{track}, pattern {pattern}: {notes} notes/hits, {length} steps, {div}").format(
                            track=t.name, pattern=b + 1, notes=p.note_count, length=p.length,
                            div=PF.enum_name(PF.P_SDIV, p.div)) + (" – " + _("active") if b == t.active else "")
                                      + (" – " + _("own preset: {name}").format(name=prj.sound_name(sid)) if sid else ""))
                    elif b == t.active:
                        it.setText("●")
                        it.setToolTip(_("active, empty"))
                    it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                else:
                    it.setFlags(Qt.ItemFlag.NoItemFlags)
                    it.setToolTip(_("This track only has 8 patterns"))
                self.grid.setItem(r, b, it)
        # Song
        n = len(used)
        punches = any(row.punches for row in prj.song)
        self.song.clearSpans()
        self.song.setColumnCount(2 + n + (1 if punches else 0))
        self.song.setHorizontalHeaderLabels([pgettext("song", "Section"), _("Rep.")] + [t.name for t in used]
                                            + (["Punch-FX"] if punches else []))
        self.song.setRowCount(len(prj.song))
        for r, row in enumerate(prj.song):
            self.song.setItem(r, 0, QTableWidgetItem(str(r + 1)))
            self.song.setItem(r, 1, QTableWidgetItem(f"{row.repeat}×"))
            for c, t in enumerate(used):
                v = row.banks[t.index] if t.index < len(row.banks) else 0
                it = QTableWidgetItem(bank_text(v))
                sid, how = prj.resolved_sound(r, t.index) if v != PF.CHAIN_REST else (0, "")
                if sid:                                 # Melodee 1.6: song block or pattern preset (display only)
                    it.setText(f"{bank_text(v)} · {prj.sound_name(sid)}")
                    it.setToolTip(_("{kind}: {name} ({engine}) – sounds exact only in the studio or on the FM-1; "
                                    "MIDI export and preview use the track's sound.").format(
                        kind=_("Song block preset") if how == "block" else _("Pattern preset"),
                        name=prj.sound_name(sid), engine=prj.sounds[sid - 1].engine_name))
                self.song.setItem(r, 2 + c, it)
            if punches:
                it = QTableWidgetItem(", ".join(x.text for x in row.punches))
                it.setToolTip(_("Timed punch effects of this section (steps START–END, on every repeat) – only work "
                                "on the FM-1 and in the studio, not in the MIDI export."))
                self.song.setItem(r, 2 + n, it)
        if not prj.song:
            self.song.setRowCount(1)
            self.song.setItem(0, 0, QTableWidgetItem(_("No song set up – the active patterns are exported instead.")))
            self.song.setSpan(0, 0, 1, self.song.columnCount())
        self.mode.model().item(0).setEnabled(bool(prj.song))
        if not prj.song and self.mode.currentData() == "song":
            self.mode.setCurrentIndex(1)
        # show the first occupied pattern
        first = next(((t.index, t.active) for t in used if t.active < len(t.patterns) and t.patterns[t.active].used),
                     next(((t.index, b) for t in used for b, p in enumerate(t.patterns) if p.used), (0, 0)))
        self.show_pattern(*first)

    @staticmethod
    def extras_note(prj) -> str:
        """Melodee 1.6: punch effects and song/pattern presets are displayed, but only played exactly in the studio"""
        n_p = sum(len(r.punches) for r in prj.song)
        if not (n_p or prj.sounds):
            return ""
        parts = ([ngettext("{n} punch effect", "{n} punch effects", n_p).format(n=n_p)] if n_p else []) + \
                ([ngettext("{n} song/pattern preset ({names})", "{n} song/pattern presets ({names})",
                           len(prj.sounds)).format(n=len(prj.sounds), names=', '.join(x.name for x in prj.sounds[:4])
                                                   + (" …" if len(prj.sounds) > 4 else ""))] if prj.sounds else [])
        return ("<br><i>" + _("{items} (Melodee 1.6) are in the song table. Preview and MIDI export do not play them – "
                              "they only sound exact in the studio (or on the FM-1).").format(
            items=_(" and ").join(parts)) + "</i>")

    def _cell(self, r: int, c: int):
        used = self.prj.used_tracks if self.prj else []
        if 0 <= r < len(used) and c < len(used[r].patterns):
            self.show_pattern(used[r].index, c)

    def show_pattern(self, k: int, b: int):
        if not self.prj or not self.prj.tracks:
            return
        self.cur = (k, b)
        t = self.prj.tracks[k]
        p = t.patterns[b]
        self.roll.set_steps(p.steps, p.length)
        self.roll.set_zoom(self.zoom.value())
        self.roll.resize(self.roll.sizeHint())
        extra = ngettext(", {n} recorded note", ", {n} recorded notes", len(p.recorded)).format(
            n=len(p.recorded)) if p.recorded else ""
        state = "" if p.used else " – " + _("empty")
        self.roll_title.setText(f"{t.name} · Pattern {b + 1}{state} · {p.length} Steps · "
                                f"{PF.enum_name(PF.P_SDIV, p.div)} · Swing {p.swing}{extra}")

    # ---------------------------------------------------------------------------------------- Export
    def options(self) -> ME.Options:
        s = _settings()
        try:
            lanes = tuple(int(x) for x in self.lanes.text().replace(";", ",").split(","))
            if len(lanes) != 8 or not all(0 <= x <= 127 for x in lanes):
                raise ValueError
        except ValueError:
            lanes = tuple(PF.LANE_GM)
            self.lanes.setText(",".join(map(str, lanes)))
        s.update(lane_notes=",".join(map(str, lanes)), drum_channel=self.drumch.currentIndex(),
                 mode=self.mode.currentData(), loops=self.loops.value())
        try:
            config.save()
        except Exception:  # noqa: BLE001
            pass
        return ME.Options(mode=self.mode.currentData(), loops=self.loops.value(), lane_notes=lanes,
                          drum_channel=9 if self.drumch.currentIndex() else None, include_muted=self.muted.isChecked())

    def _target(self, suffix: str, title: str, filt: str, ask: bool = True) -> Path | None:
        base = (self.prj.name or self.source or "FM-1 Projekt").replace("/", "_")  # i18n: ignore – file name
        if self.mode.currentData() == "pattern":
            base += f" Spur {self.cur[0] + 1} P{self.cur[1] + 1}"  # i18n: ignore – file name
        path = start_dir("MIDI-Export") / f"{base}{suffix}"
        if not ask:
            return path
        p, _filter = QFileDialog.getSaveFileName(self, title, str(path), filt)
        return Path(p) if p else None

    def _ready(self) -> bool:
        if not self.prj or not self.prj.ok:
            show_error(self, _("Nothing to export"), _("Please open a readable project first."))
            return False
        return True

    def export_midi(self, path: Path | None = None) -> Path | None:
        if not self._ready():
            return None
        path = path or self._target(".mid", _("Save MIDI file"), "MIDI (*.mid)")
        if not path:
            return None
        o = self.options()
        pat = self.cur if o.mode == "pattern" else None
        if not ME.events(self.prj, o, pat):
            show_error(self, _("No notes"), _("There are no notes in the chosen selection."))
            return None
        if not self._write(path, lambda: ME.save_midi(self.prj, path, o, pat)):
            return None
        self._done(path)
        return path

    def _write(self, path: Path, fn) -> bool:
        """Write the file; errors (e.g. read-only folder) as a readable message instead of a crash."""
        try:
            fn()
            return True
        except OSError as e:
            show_error(self, _("Not saved"), fs_error_text(e, _("“{name}” not saved").format(name=path.name)))
            return False

    def export_csv(self, path: Path | None = None):
        if not self._ready():
            return None
        path = path or self._target(".csv", _("Save CSV"), "CSV (*.csv)")
        if path:
            o = self.options()
            if not self._write(path, lambda: path.write_text(ME.to_csv(self.prj, o, self.cur if o.mode == "pattern"
                                                                         else None), "utf-8")):
                return None
            self._done(path)
        return path

    def export_json(self, path: Path | None = None):
        if not self._ready():
            return None
        path = path or self._target(".json", _("Save JSON"), "JSON (*.json)")
        if path:
            if not self._write(path, lambda: path.write_text(ME.to_json(self.prj), "utf-8")):
                return None
            self._done(path)
        return path

    def export_png(self, path: Path | None = None):
        if not self._ready():
            return None
        path = path or self._target(".png", _("Save image"), "PNG (*.png)")
        if path:
            self.roll.resize(self.roll.sizeHint())
            if not self.roll.grab().save(str(path), "PNG"):
                show_error(self, _("Not saved"), _("“{name}” could not be written – folder write-protected?").format(name=path.name))
                return None
            self._done(path)
        return path

    def _done(self, path: Path):
        try:
            from ..common import notify
            notify(_("Saved: {file}").format(file=path.name), "success", (_("Open folder"), self.open_folder))
        except Exception:  # noqa: BLE001
            pass
        msg = getattr(self.ctx, "message", None)
        if msg is not None:
            msg.emit(_("Saved: {file}").format(file=path))

    def open_folder(self):
        folder, err = safe_folder("MIDI-Export")
        if err:
            show_error(self, _("Open folder"), err)
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))


def _make(ctx, window):
    return ProjectView(ctx, window)


PAGE = PageSpec(key="project_view", title=_("Project View"), group="musik", factory=_make,
                icons=("view-media-playlist", "audio-midi", "document-preview"), order=20,
                description=_("view Melodee projects and export them as MIDI"),
                keywords=("projekt", "midi", "export", "piano roll", "song", "pattern", "daw", "audacity",
                          "project", "view"))
