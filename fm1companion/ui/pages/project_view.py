# SPDX-License-Identifier: GPL-3.0-only
"""Projekt-Ansicht: Melodee-Projekte aus Backups, Projektdateien oder direkt vom FM-1 ansehen und exportieren."""
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
from ...i18n import _

SETTINGS = {"lane_notes": ",".join(str(n) for n in PF.LANE_GM), "drum_channel": 0, "mode": "song", "loops": 4,
            "last_dir": ""}


def _settings():
    return config.module("musik", dict(SETTINGS))


def bank_text(v: int) -> str:
    return "Pause" if v == PF.CHAIN_REST else "halten" if v == PF.CHAIN_HOLD else f"P{v + 1}"


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
        lay.addWidget(heading("Projekt-Ansicht", "Melodee-Projekte ansehen, anhören (auf dem FM-1 mit seinen eigenen "
                              "Klängen oder im Rechner) und als MIDI-Datei für Audacity, LMMS, Ardour oder MuseScore "
                              "exportieren."))
        self.live_tag = None                       # (Profil-ID, Port, Name) wenn die laufende Musik eines Geräts offen ist
        self.live_slot = None
        open_card = Card("1 · Projekt öffnen")
        top = FlowLayout()
        top.addWidget(button("Backup öffnen …", ("document-open",), slot=self.open_backup,
                             tooltip="Ein Backup aus ~/FM-1/Backups öffnen (alle Projekte darin) – Strg+O"))
        top.addWidget(button("Projektdatei öffnen …", ("document-open-folder", "folder"), slot=self.open_project,
                             tooltip="Eine .fm1proj-Datei aus der Projekt-Bibliothek öffnen"))
        sep = QLabel("  oder live vom FM-1:")
        sep.setProperty("role", "muted")
        top.addWidget(sep)
        self.slot = QSpinBox()
        self.slot.setRange(0, 32)
        self.slot.setSpecialValueText("laufende Musik")
        self.slot.setPrefix("Projekt ")
        self.slot.setToolTip("Was vom aktiven FM-1 gelesen wird: die laufende Musik oder ein gespeicherter Projektplatz")
        top.addWidget(self.slot)
        self.b_read = button("Vom FM-1 lesen", ("go-down", "document-import"), "primary", self.read_device,
                             tooltip="Liest das Projekt vom aktiven FM-1 (oben in der Leiste) über die Backup-Befehle "
                                     "– nur lesen, am FM-1 wird nichts geändert")
        top.addWidget(self.b_read)
        self.choice = QComboBox()
        self.choice.setMinimumWidth(220)
        self.choice.setToolTip("Projekt in der geöffneten Datei")
        self.choice.currentIndexChanged.connect(self._choose)
        self.choice.hide()                         # erst sichtbar, wenn eine Datei mit Projekten offen ist
        top.addWidget(self.choice)
        open_card.lay.addLayout(top)
        self.badge = DeviceBadge(ctx, "Live-Lesen und Abspielen gehen an")
        open_card.lay.addWidget(self.badge)
        lay.addWidget(open_card)
        self.stack = QStackedWidget()
        self.empty = empty_state("view-media-playlist", "Noch kein Projekt geöffnet",
                                 "So geht's: ein Backup öffnen (Geräte-Backups liegen in ~/FM-1/Backups), eine "
                                 "Projektdatei aus der Bibliothek wählen oder die laufende Musik direkt vom FM-1 lesen.",
                                 "Backup öffnen …", self.open_backup)
        self.stack.addWidget(self.empty)
        self.stack.addWidget(self._build_main())
        lay.addWidget(self.stack, 1)
        if getattr(ctx, "changed", None) is not None:
            ctx.changed.connect(self._update_song_row)
        QShortcut(QKeySequence("Ctrl+O"), self, activated=self.open_backup)
        QShortcut(QKeySequence("Ctrl+E"), self, activated=self.export_midi)

    # ---------------------------------------------------------------------------------------- Aufbau
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
        self.tracks.setHorizontalHeaderLabels(["Spur", "Engine", "Preset", "Lautst.", "Pan", "Stumm", "Pattern",
                                               "MIDI-Kanal"])
        self.tracks.verticalHeader().hide()
        self.tracks.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tracks.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.tracks.horizontalHeader().setStretchLastSection(True)
        tabs.addTab(self.tracks, "Spuren")
        self.grid = QTableWidget(0, PF.NPAT)
        self.grid.setHorizontalHeaderLabels([str(i + 1) for i in range(PF.NPAT)])
        self.grid.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.grid.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.grid.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.grid.cellClicked.connect(self._cell)
        self.grid.setToolTip("Patterns je Spur: gefüllt = belegt, fett = aktiv. Klick zeigt die Noten.")
        tabs.addTab(self.grid, "Pattern-Übersicht")
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
        bar.addWidget(QLabel("Zoom:"))
        self.zoom = QSlider(Qt.Orientation.Horizontal)
        self.zoom.setRange(8, 48)
        self.zoom.setValue(20)
        self.zoom.setMaximumWidth(160)
        self.zoom.setToolTip("Breite eines Steps")
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
        v.insertWidget(1, self._build_play())           # Anhören gleich unter der Projektinfo – sichtbar ohne Rollen
        v.addWidget(split, 1)
        v.addWidget(self._build_export())
        return w

    def _build_play(self) -> QWidget:
        card = Card("2 · Anhören")
        s = _settings()
        self.mode = QComboBox()
        self.mode.addItem("Song (Abschnitte nacheinander)", "song")
        self.mode.addItem("aktive Patterns in Schleife", "active")
        self.mode.addItem("nur das gezeigte Pattern", "pattern")
        self.mode.setCurrentIndex(max(0, self.mode.findData(s.get("mode", "song"))))
        self.mode.setToolTip("Was gespielt und exportiert wird")
        self.loops = QSpinBox()
        self.loops.setRange(1, 64)
        self.loops.setValue(int(s.get("loops", 4)))
        self.loops.setSuffix(" Durchläufe")
        self.loops.setToolTip("Wie oft die aktiven Patterns laufen (Ablauf „aktive Patterns“)")
        self.muted = QCheckBox("stumme Spuren mitspielen")
        self.muted.setChecked(True)
        self.muted.setToolTip("Spuren, die im Projekt stumm geschaltet sind, trotzdem spielen und exportieren")
        sel = FlowLayout()
        sel.addWidget(QLabel("Auswahl:"))
        sel.addWidget(self.mode)
        sel.addWidget(self.loops)
        sel.addWidget(self.muted)
        card.lay.addLayout(sel)
        self.player_bar = PlaybackBar(self.ctx, self.play_source, self, play_text="Abspielen",
                                      shortcut_hint="Leertaste")
        card.lay.addWidget(self.player_bar)
        self.b_song = button("Song am FM-1 selbst starten", ("media-playback-start", "media-seek-forward"), "",
                             self.device_song, "Das FM-1 spielt seine laufende Musik selbst ab (sein Song, exakt "
                             "sein Klang). Nur für die gerade gelesene laufende Musik des aktiven FM-1; nichts wird "
                             "gespeichert.")
        self.b_song_stop = button("FM-1 stoppen", ("media-playback-stop",), "", self.device_stop,
                                  "Den Transport des FM-1 anhalten (wie die STOP-Taste)")
        self.song_from = QSpinBox(minimum=1, maximum=1, value=1)
        self.song_from.setPrefix("ab Abschnitt ")
        self.song_from.setToolTip("Abschnitt des Songs, mit dem das FM-1 beginnt (wie PLAY am SONG-Cursor). Ab Melodee "
                                  "1.6.1 mit CLK INT bekommen mitlaufende Geräte dann Song Position + Continue.")
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
            loop = float(int(loop + 3.999) // 4 * 4) or None   # auf ganze Takte
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
        """laufende Musik: das FM-1 spielt seinen eigenen Song (BANK_SONG start) – kein Flash-Zugriff."""
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
            where = f" ab Abschnitt {row + 1}" if row else ""
            sync = P.song_sync_note(version, clk, row) if rc == 0 else ""
            self.player_bar.status.setText(
                f"„{name}“ spielt seinen Song selbst{where} ({rows} Abschnitte) – exakt sein Klang. „FM-1 stoppen“ "
                "hält an." + (" " + sync if sync else "")
                if rc == 0 else f"„{name}“ hat den Start abgelehnt (Code {rc}) – ist ein Song angelegt? Am FM-1 SONG "
                                "prüfen oder „Abspielen“ nutzen.")
        self.ctx.job(self, "Song starten", work, dialog=False, on_done=done,
                     on_fail=lambda m, tb: self.player_bar.status.setText(f"Start nicht möglich: {m}"))

    def device_stop(self):
        if not self.ctx.require(self):
            return
        from ... import playback as P
        self.ctx.job(self, "FM-1 stoppen", lambda w, link: P.transport_stop(link), dialog=False,
                     on_done=lambda ok: self.player_bar.status.setText("FM-1 gestoppt." if ok else
                                                                        "Das FM-1 hat nicht geantwortet."),
                     on_fail=lambda m, tb: self.player_bar.status.setText(f"Nicht gestoppt: {m}"))

    def deactivate(self):
        self.player_bar.stop(quiet=True)

    def shutdown(self):
        self.player_bar.shutdown()

    def _build_export(self) -> QWidget:
        card = Card("3 · Exportieren")
        g = QGridLayout()
        s = _settings()
        self.drumch = QComboBox()
        self.drumch.addItem("Drums auf dem Kanal der Spur", 0)
        self.drumch.addItem("Drums auf Kanal 10 (GM)", 1)
        self.drumch.setCurrentIndex(int(s.get("drum_channel", 0)))
        self.lanes = QLineEdit(s.get("lane_notes", SETTINGS["lane_notes"]))
        self.lanes.setToolTip("GM-Noten der 8 Drum-Lanes: KICK, SNARE, CLAP, HAT CL, HAT OP, TOM, RIM, BELL")
        g.addWidget(label("Exportiert die Auswahl aus „Anhören“.", "muted"), 0, 0, 1, 2)
        g.addWidget(self.drumch, 0, 3)
        g.addWidget(QLabel("Lane-Noten:"), 1, 0)
        g.addWidget(self.lanes, 1, 1)
        row = QHBoxLayout()
        self.b_midi = button("MIDI-Datei …", ("audio-midi", "document-save"), "", self.export_midi,
                             "Standard-MIDI-Datei (Typ 1): eine Spur je FM-1-Spur, Kanal wie am FM-1 (Strg+E)")
        row.addWidget(self.b_midi)
        row.addWidget(button("CSV …", ("text-csv", "document-save"), slot=self.export_csv,
                             tooltip="Alle Noten als Tabelle (Tabellenkalkulation)"))
        row.addWidget(button("JSON …", ("document-save",), slot=self.export_json,
                             tooltip="Das ganze Projekt als lesbare JSON-Datei"))
        row.addWidget(button("Bild …", ("image-x-generic", "document-save"), slot=self.export_png,
                             tooltip="Die Piano-Roll als PNG-Bild"))
        row.addWidget(button("Export-Ordner öffnen", ("folder-open",), slot=self.open_folder,
                             tooltip="Den Ordner ~/FM-1/MIDI-Export im Dateimanager öffnen (von dort in Audacity "
                                     "oder eine DAW ziehen)"))
        row.addStretch(1)
        g.addLayout(row, 2, 0, 1, 4)
        reflow(row)                                      # Export-Knöpfe brechen bei schmalem Fenster um
        card.lay.addLayout(g)
        return card

    # ---------------------------------------------------------------------------------------- Laden
    def open_backup(self, path: str | None = None):
        if not path:
            start = str(config.load().backups_dir) if hasattr(config.load(), "backups_dir") else str(Path.home())
            path, _ = QFileDialog.getOpenFileName(self, "Backup öffnen", start, "FM-1-Backup (*.json)")
            if not path:
                return
        self._load_path(Path(path))

    def open_project(self, path: str | None = None):
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Projektdatei öffnen", str(start_dir("Projekte")),
                                                  "FM-1-Projekt (*.fm1proj)")
            if not path:
                return
        self._load_path(Path(path))

    def _load_path(self, path: Path):
        try:
            items = PF.load_any(path)
        except Exception as e:  # noqa: BLE001
            show_error(self, "Datei nicht lesbar", f"{path.name} ließ sich nicht öffnen:\n{e}")
            return
        if not items:
            show_error(self, "Keine Projekte", f"In {path.name} ist kein Projekt gespeichert.")
            return
        self.source = path.stem
        self.source_note = f"Datei {path.name}"
        self.live_tag = self.live_slot = None
        self.set_items(items)

    def open_with(self, path=None, data: bytes | None = None, title: str = "", firmware: str = "",
                  read_slot: int | None = None, **_):
        """von anderen Seiten: Datei/Daten zeigen – oder mit read_slot einen Platz des aktiven FM-1 lesen."""
        if read_slot is not None:
            self.slot.setValue(max(0, min(self.slot.maximum(), int(read_slot))))
            self.read_device()
        elif data is not None:
            self.source = title or "Projekt"
            self.source_note = title or "Projekt"
            self.live_tag = self.live_slot = None
            self.set_items([(title or "Projekt", PF.decode(data, firmware))])
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
                show_error(self, "SLOOP hat nur 4 Plätze", "SLOOP speichert Projekte als Sections A–D (Plätze 1–4). "
                                                           "Bitte 0 (laufendes Projekt) oder 1–4 wählen.")
                return
            show_error(self, "SLOOP-Projekte", "Die Projekt-Ansicht kennt nur das Melodee-Format. SLOOP-Projekte sind "
                                               "anders aufgebaut – zum Ansehen nimm den SLOOP-Web-Editor (Seite "
                                               "„Editor“). Sichern geht wie gewohnt über Backups.")
            return
        if key and key not in ("melodee", "skiff", "felucca"):
            from ..device import firmware_name
            show_error(self, "Andere Firmware", f"{firmware_name(key) or 'Diese Firmware'} hat keine Projekte, die die "
                                                "App lesen kann. Die Projekt-Ansicht zeigt Melodee-Projekte.")
            return

        def work(w, link):
            man = {o.id: o for o in backup.list_objects(link)}
            o = man.get(oid)
            if not o or not o.size:
                raise backup.BackupError(f"Projektplatz {slot} ist leer – nichts zu lesen." if slot else
                                         "Keine laufende Musik gefunden.")
            w.log.emit(f"Lese {o.size} Byte …")
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
                what = "Laufende Musik" if not slot else f"Projekt {slot}"
                title = f"{what} von „{dev}“"
                self.source = title
                self.source_note = (f"live gelesen von <b>„{dev}“</b>" + (f" ({fwl})" if fwl else "") +
                                    f" um {time.strftime('%H:%M')} – {what}")
                self.live_tag, self.live_slot = tag, slot
                self.set_items([(title, PF.decode(data, fw))])
        self.ctx.job(self, f"Projekt von „{dev}“ lesen", work, on_done=done,
                     done_text=lambda d: ("Laufende Musik" if not slot else f"Projekt {slot}") + f" von „{dev}“ gelesen "
                     f"({len(d)} Byte) und geöffnet – nur angesehen, am FM-1 wurde nichts geändert.")

    def set_items(self, items):
        self.items = items
        self.choice.blockSignals(True)
        self.choice.clear()
        for title, prj in items:
            self.choice.addItem(title + ("" if prj.ok else " (nicht lesbar)"))
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

    # ---------------------------------------------------------------------------------------- Anzeige
    def show_project(self):
        prj = self.prj
        if not prj.ok:
            if prj.format.startswith("SLOOP"):
                self.info.setText(f"<b>{prj.format}</b> – {'; '.join(prj.warnings)}")
            else:
                self.info.setText(f"<b>Dieses Projekt ließ sich nicht lesen.</b> Format: {prj.format}. "
                                  f"{'; '.join(prj.warnings)}<br>Was geht: das Projekt bleibt im Backup erhalten und "
                                  "lässt sich wie gewohnt zurückspielen – nur die Ansicht fehlt.")
            self.tracks.setRowCount(0)
            self.grid.setRowCount(0)
            self.song.setRowCount(0)
            self.roll.set_steps([], 16)
            self.roll_title.setText("")
            return
        warn = f"<br><i>Hinweis: {'; '.join(prj.warnings)}</i>" if prj.warnings else ""
        name = f"<b>{prj.name}</b> · " if prj.name else ""
        src = f"<br>Quelle: {self.source_note}" if getattr(self, "source_note", "") else ""
        self.info.setText(f"{name}{prj.bpm} BPM · Swing {prj.swing} · {len(prj.used_tracks)} Spuren · "
                          f"Format {prj.format}{warn}{self.extras_note(prj)}{src}")
        self._update_song_row()
        used = prj.used_tracks
        self.tracks.setRowCount(len(used))
        for r, t in enumerate(used):
            preset = t.preset_name or (f"Preset {t.preset + 1}" if t.preset < 0xFE else "")
            if t.user_slot:
                preset += f" (Bibliothek U{t.user_slot:03d})"
            vals = [t.name, t.engine_name, preset, str(t.level), str(t.pan), "ja" if t.mute else "",
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
                        it.setToolTip(f"{t.name}, Pattern {b + 1}: {p.note_count} Noten/Schläge, {p.length} Steps, "
                                      f"{PF.enum_name(PF.P_SDIV, p.div)}" + (" – aktiv" if b == t.active else "")
                                      + (f" – eigenes Preset: {prj.sound_name(sid)}" if sid else ""))
                    elif b == t.active:
                        it.setText("●")
                        it.setToolTip("aktiv, leer")
                    it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                else:
                    it.setFlags(Qt.ItemFlag.NoItemFlags)
                    it.setToolTip("Diese Spur hat nur 8 Patterns")
                self.grid.setItem(r, b, it)
        # Song
        n = len(used)
        punches = any(row.punches for row in prj.song)
        self.song.clearSpans()
        self.song.setColumnCount(2 + n + (1 if punches else 0))
        self.song.setHorizontalHeaderLabels(["Abschnitt", "Wdh."] + [t.name for t in used]
                                            + (["Punch-FX"] if punches else []))
        self.song.setRowCount(len(prj.song))
        for r, row in enumerate(prj.song):
            self.song.setItem(r, 0, QTableWidgetItem(str(r + 1)))
            self.song.setItem(r, 1, QTableWidgetItem(f"{row.repeat}×"))
            for c, t in enumerate(used):
                v = row.banks[t.index] if t.index < len(row.banks) else 0
                it = QTableWidgetItem(bank_text(v))
                sid, how = prj.resolved_sound(r, t.index) if v != PF.CHAIN_REST else (0, "")
                if sid:                                 # Melodee 1.6: Song-Block- bzw. Pattern-Preset (nur Anzeige)
                    it.setText(f"{bank_text(v)} · {prj.sound_name(sid)}")
                    it.setToolTip(("Song-Block-Preset" if how == "block" else "Pattern-Preset") +
                                  f": {prj.sound_name(sid)} ({prj.sounds[sid - 1].engine_name}) – klingt exakt nur im "
                                  "Studio bzw. am FM-1; MIDI-Export und Vorhören nehmen den Klang der Spur.")
                self.song.setItem(r, 2 + c, it)
            if punches:
                it = QTableWidgetItem(", ".join(x.text for x in row.punches))
                it.setToolTip("Zeitgesteuerte Punch-Effekte dieses Abschnitts (Steps START–END, bei jeder Wiederholung)"
                              " – wirken nur am FM-1 und im Studio, nicht im MIDI-Export.")
                self.song.setItem(r, 2 + n, it)
        if not prj.song:
            self.song.setRowCount(1)
            self.song.setItem(0, 0, QTableWidgetItem("Kein Song angelegt – exportiert werden dann die aktiven Patterns."))
            self.song.setSpan(0, 0, 1, self.song.columnCount())
        self.mode.model().item(0).setEnabled(bool(prj.song))
        if not prj.song and self.mode.currentData() == "song":
            self.mode.setCurrentIndex(1)
        # erstes belegtes Pattern zeigen
        first = next(((t.index, t.active) for t in used if t.active < len(t.patterns) and t.patterns[t.active].used),
                     next(((t.index, b) for t in used for b, p in enumerate(t.patterns) if p.used), (0, 0)))
        self.show_pattern(*first)

    @staticmethod
    def extras_note(prj) -> str:
        """Melodee 1.6: Punch-Effekte und Song-/Pattern-Presets werden angezeigt, aber nur im Studio exakt gespielt"""
        n_p = sum(len(r.punches) for r in prj.song)
        if not (n_p or prj.sounds):
            return ""
        parts = ([f"{n_p} Punch-Effekte"] if n_p else []) + \
                ([f"{len(prj.sounds)} Song-/Pattern-Presets ({', '.join(x.name for x in prj.sounds[:4])}"
                  + (" …" if len(prj.sounds) > 4 else "") + ")"] if prj.sounds else [])
        return ("<br><i>" + " und ".join(parts) + " (Melodee 1.6) stehen in der Song-Tabelle. Vorhören und MIDI-Export "
                "spielen sie nicht – exakt klingen sie nur im Studio (oder am FM-1).</i>")

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
        extra = f", {len(p.recorded)} aufgenommene Noten" if p.recorded else ""
        state = "" if p.used else " – leer"
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
        base = (self.prj.name or self.source or "FM-1 Projekt").replace("/", "_")
        if self.mode.currentData() == "pattern":
            base += f" Spur {self.cur[0] + 1} P{self.cur[1] + 1}"
        path = start_dir("MIDI-Export") / f"{base}{suffix}"
        if not ask:
            return path
        p, _ = QFileDialog.getSaveFileName(self, title, str(path), filt)
        return Path(p) if p else None

    def _ready(self) -> bool:
        if not self.prj or not self.prj.ok:
            show_error(self, "Nichts zu exportieren", "Bitte zuerst ein lesbares Projekt öffnen.")
            return False
        return True

    def export_midi(self, path: Path | None = None) -> Path | None:
        if not self._ready():
            return None
        path = path or self._target(".mid", "MIDI-Datei speichern", "MIDI (*.mid)")
        if not path:
            return None
        o = self.options()
        pat = self.cur if o.mode == "pattern" else None
        if not ME.events(self.prj, o, pat):
            show_error(self, "Keine Noten", "In der gewählten Auswahl gibt es keine Noten.")
            return None
        if not self._write(path, lambda: ME.save_midi(self.prj, path, o, pat)):
            return None
        self._done(path)
        return path

    def _write(self, path: Path, fn) -> bool:
        """Datei schreiben; Fehler (z. B. schreibgeschützter Ordner) als deutsche Meldung statt Absturz."""
        try:
            fn()
            return True
        except OSError as e:
            show_error(self, "Nicht gespeichert", fs_error_text(e, f"„{path.name}“ nicht gespeichert"))
            return False

    def export_csv(self, path: Path | None = None):
        if not self._ready():
            return None
        path = path or self._target(".csv", "CSV speichern", "CSV (*.csv)")
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
        path = path or self._target(".json", "JSON speichern", "JSON (*.json)")
        if path:
            if not self._write(path, lambda: path.write_text(ME.to_json(self.prj), "utf-8")):
                return None
            self._done(path)
        return path

    def export_png(self, path: Path | None = None):
        if not self._ready():
            return None
        path = path or self._target(".png", "Bild speichern", "PNG (*.png)")
        if path:
            self.roll.resize(self.roll.sizeHint())
            if not self.roll.grab().save(str(path), "PNG"):
                show_error(self, "Nicht gespeichert", f"„{path.name}“ ließ sich nicht schreiben – Ordner schreibgeschützt?")
                return None
            self._done(path)
        return path

    def _done(self, path: Path):
        try:
            from ..common import notify
            notify(f"Gespeichert: {path.name}", "success", ("Ordner öffnen", self.open_folder))
        except Exception:  # noqa: BLE001
            pass
        msg = getattr(self.ctx, "message", None)
        if msg is not None:
            msg.emit(f"Gespeichert: {path}")

    def open_folder(self):
        folder, err = safe_folder("MIDI-Export")
        if err:
            show_error(self, "Ordner öffnen", err)
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))


def _make(ctx, window):
    return ProjectView(ctx, window)


PAGE = PageSpec(key="project_view", title=_("Project View"), group="musik", factory=_make,
                icons=("view-media-playlist", "audio-midi", "document-preview"), order=20,
                description=_("view Melodee projects and export them as MIDI"),
                keywords=("projekt", "midi", "export", "piano roll", "song", "pattern", "daw", "audacity",
                          "project", "view"))
