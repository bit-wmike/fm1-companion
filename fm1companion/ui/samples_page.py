# SPDX-License-Identifier: GPL-3.0-only
"""Samples: Bibliothek, Vorhören, Kit-Baukasten und Slots auf dem FM-1."""
from __future__ import annotations

import os
import time
from pathlib import Path

from PyQt6.QtCore import QUrl, Qt, QTimer
from PyQt6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
                             QGroupBox, QInputDialog, QLabel, QLineEdit, QListWidget,
                             QListWidgetItem, QProgressBar, QPushButton, QScrollArea, QSpinBox, QSplitter, QTabWidget,
                             QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import config, samples as S
from .audio import OutputButton, Player, Waveform
from .common import (DeviceUndo, FlowLayout, confirm, device_tag, notify, fmt_secs, fmt_size, fs_error_text, german_buttons, heading, icon, plural,
                     run_job, safe_folder, show_error)
from .responsive import on_narrow


class SamplesPage(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.lib = S.Library()
        self.player = Player(self)
        self.kit: S.Kit = S.Kit(name="KIT 1")
        self.kit_dirty = False
        self.slots: list = []
        self.slot_undo = DeviceUndo()          # (Slot, alter Inhalt als Backup-Objekt) je Gerät
        self._slots_busy = False
        self._search_timer = QTimer(self, singleShot=True, interval=250)
        self._search_timer.timeout.connect(self.refresh_table)

        lay = QVBoxLayout(self)
        lay.addWidget(heading("Samples & Kits", "Deine Samples von überall auf dem Rechner, vorhören, zuschneiden und "
                                                "zu Kits bauen, Loops zerschneiden, selbst aufnehmen, freie Pakete laden. "
                                                "Hochladen: Skiff (3 Slots), SLOOP (4) und FM1 Open (8 Slots, gemeinsam "
                                                "~19,5 s). Melodee ab 1.5 hat keine Sample-Slots."))
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        lay.addWidget(self.tabs, 1)
        split = QSplitter()
        self.tabs.addTab(split, icon("folder-sound", "audio-x-generic"), "Bibliothek && Kits")

        # ---------------- links: Filter
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Suchen: kick, po-33, vocal …")
        self.search.textChanged.connect(lambda: self._search_timer.start())
        ll.addWidget(self.search)
        self.chk_fav = QCheckBox("nur Favoriten")
        self.chk_fav.toggled.connect(self.refresh_table)
        self.max_len = QDoubleSpinBox()
        self.max_len.setRange(0, 600)
        self.max_len.setSuffix(" s")
        self.max_len.setSpecialValueText("beliebig lang")
        self.max_len.setToolTip("nur Sounds bis zu dieser Länge")
        self.max_len.valueChanged.connect(lambda: self._search_timer.start())
        ll.addLayout(_row(self.chk_fav, self.max_len))
        tabs = QTabWidget()
        self.folders = QTreeWidget()
        self.folders.setHeaderHidden(True)
        self.folders.currentItemChanged.connect(lambda *_: self.refresh_table())
        tabs.addTab(self.folders, "Ordner")
        self.tags = QListWidget()
        self.tags.currentItemChanged.connect(lambda *_: self.refresh_table())
        tabs.addTab(self.tags, "Schlagwörter")
        ll.addWidget(tabs, 1)
        b_addroot = QPushButton(icon("folder-new", "list-add"), "Ordner hinzufügen …")
        b_addroot.clicked.connect(self._add_root)
        b_scan = QPushButton(icon("view-refresh"), "Neu einlesen")
        b_scan.clicked.connect(self.scan)
        ll.addLayout(_row(b_addroot, b_scan))
        b_dupes = QPushButton(icon("edit-copy"), "Doppelte finden …")
        b_dupes.setToolTip("Dateien mit gleichem Inhalt in der Bibliothek suchen")
        b_dupes.clicked.connect(self.find_duplicates)
        ll.addWidget(b_dupes)
        hint = QLabel("Tipp: Dateien oder Ordner einfach hierher ziehen.")
        hint.setProperty("role", "small")
        hint.setWordWrap(True)
        ll.addWidget(hint)
        self.setAcceptDrops(True)
        self.lib_info = QLabel()
        self.lib_info.setWordWrap(True)
        ll.addWidget(self.lib_info)
        self.ffmpeg_hint = QLabel(FFMPEG_HINT)
        self.ffmpeg_hint.setWordWrap(True)
        self.ffmpeg_hint.setProperty("role", "small")
        self.ffmpeg_hint.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.ffmpeg_hint.hide()
        ll.addWidget(self.ffmpeg_hint)
        split.addWidget(left)

        # ---------------- Mitte: Tabelle + Wellenform
        mid = QWidget()
        ml = QVBoxLayout(mid)
        ml.setContentsMargins(0, 0, 0, 0)
        self.table = QTreeWidget()
        self.table.setHeaderLabels(["★", "Name", "Länge", "Format", "Schlagwörter", "Ordner"])
        self.table.setRootIsDecorated(False)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setSortingEnabled(True)
        self.table.currentItemChanged.connect(self._sample_selected)
        self.table.itemDoubleClicked.connect(lambda *_: self._add_to_kit())
        ml.addWidget(self.table, 3)
        self.wave = Waveform()
        ml.addWidget(self.wave, 1)
        b_play = QPushButton(icon("media-playback-start"), "Anhören")
        b_play.clicked.connect(self._play_selected)
        b_stop = QPushButton(icon("media-playback-stop"), "Stopp")
        b_stop.clicked.connect(self.player.stop)
        self.chk_auto = QCheckBox("beim Auswählen abspielen")
        self.chk_auto.setChecked(True)
        b_fav = QPushButton(icon("starred-symbolic", "emblem-favorite", "rating"), "Favorit")
        b_fav.clicked.connect(self._toggle_fav)
        b_tags = QPushButton(icon("tag", "bookmark-new"), "Schlagwörter …")
        b_tags.clicked.connect(self._edit_tags)
        b_show = QPushButton(icon("folder-open"), "Im Ordner zeigen")
        b_show.clicked.connect(self._show_in_folder)
        b_slice = QPushButton(icon("edit-cut"), "Zerschneiden …")
        b_slice.setToolTip("Loop in Stücke schneiden (Transienten, gleich lang, BPM-Raster) – Strg+L")
        b_slice.clicked.connect(lambda: self.open_slicer())
        for seq, parent, fn in ((QKeySequence("Ctrl+L"), self, lambda: self.open_slicer()),
                                (QKeySequence(Qt.Key.Key_Space), self.table, self._play_selected)):
            sc = QShortcut(seq, parent, fn)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        b_tokit = QPushButton(icon("list-add"), "Ins Kit →")
        b_tokit.clicked.connect(self._add_to_kit)
        self.out_btn = OutputButton(self)
        ml.addLayout(_row(b_play, b_stop, self.chk_auto, self.out_btn, None, b_fav, b_tags, b_show, b_slice, b_tokit))
        split.addWidget(mid)

        # ---------------- rechts: Kit + Slots
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        kg = QGroupBox("Kit-Baukasten")
        kl = QVBoxLayout(kg)
        self.kit_combo = QComboBox()
        self.kit_combo.activated.connect(self._kit_chosen)
        b_kit_new = QPushButton(icon("document-new"), "Neu")
        b_kit_new.setToolTip("Neues Kit")
        b_kit_new.clicked.connect(self._kit_new)
        b_kit_save = QPushButton(icon("document-save"), "Speichern")
        b_kit_save.setToolTip("Kit speichern")
        b_kit_save.clicked.connect(self._kit_save)
        b_kit_del = QPushButton(icon("edit-delete"), "Löschen")
        b_kit_del.setToolTip("Kit löschen")
        b_kit_del.clicked.connect(self._kit_delete)
        kl.addWidget(self.kit_combo)
        kl.addLayout(_row(b_kit_new, b_kit_save, b_kit_del))
        f = QFormLayout()
        self.kit_name = QLineEdit()
        self.kit_name.setMaxLength(8)
        self.kit_name.setToolTip("Das FM-1 zeigt maximal 8 Zeichen")
        self.kit_name.textEdited.connect(self._kit_meta)
        self.kit_mode = QComboBox()
        self.kit_mode.addItem("Drum-Kit: jede Taste ein Sound", "drum")
        self.kit_mode.addItem("Instrument: über die Tastatur verteilt", "keys")
        self.kit_mode.activated.connect(self._kit_meta)
        self.kit_base = QSpinBox()
        self.kit_base.setRange(0, 112)
        self.kit_base.valueChanged.connect(self._kit_meta)
        f.addRow("Name:", self.kit_name)
        f.addRow("Art:", self.kit_mode)
        f.addRow("Erste Taste:", self.kit_base)
        kl.addLayout(f)
        self.zones = QTreeWidget()
        self.zones.setHeaderLabels(["#", "Sound", "Taste", "Länge"])
        self.zones.setRootIsDecorated(False)
        self.zones.currentItemChanged.connect(self._zone_selected)
        self.zones.setMinimumHeight(170)
        kl.addWidget(self.zones, 1)
        b_up = QPushButton(icon("go-up"), "▲")
        b_up.clicked.connect(lambda: self._move(-1))
        b_down = QPushButton(icon("go-down"), "▼")
        b_down.clicked.connect(lambda: self._move(1))
        b_rm = QPushButton(icon("list-remove"), "Entfernen")
        b_rm.clicked.connect(self._remove_zone)
        b_hear = QPushButton(icon("media-playback-start"), "Anhören")
        b_hear.setToolTip("so wie es auf dem FM-1 klingt (22 kHz, bearbeitet)")
        b_hear.clicked.connect(self._hear_zone)
        kl.addLayout(_row(b_up, b_down, b_rm, None, b_hear))
        # Zonen-Bearbeitung
        zf = QFormLayout()
        self.z_root = QSpinBox()
        self.z_root.setRange(0, 127)
        self.z_root.valueChanged.connect(self._zone_edit)
        self.z_norm = QCheckBox("Normalisieren")
        self.z_trim = QCheckBox("Stille abschneiden")
        self.z_rev = QCheckBox("Rückwärts")
        for c in (self.z_norm, self.z_trim, self.z_rev):
            c.toggled.connect(self._zone_edit)
        self.z_gain = QDoubleSpinBox()
        self.z_gain.setRange(-24, 12)
        self.z_gain.setSuffix(" dB")
        self.z_gain.valueChanged.connect(self._zone_edit)
        self.z_fade = QDoubleSpinBox()
        self.z_fade.setRange(0, 2000)
        self.z_fade.setSuffix(" ms")
        self.z_fade.valueChanged.connect(self._zone_edit)
        zf.addRow("Grundton:", self.z_root)
        zf.addRow(_row(self.z_norm, self.z_trim, self.z_rev))
        zf.addRow("Lautstärke:", self.z_gain)
        zf.addRow("Ausblenden:", self.z_fade)
        kl.addLayout(zf)
        self.zone_wave = Waveform()
        self.zone_wave.markers_changed.connect(self._markers)
        kl.addWidget(self.zone_wave)
        self.budget = QProgressBar()
        self.budget.setFormat("%v ms von %m ms")
        kl.addWidget(self.budget)
        b_fit = QPushButton(icon("transform-scale", "zoom-fit-best"), "Passend kürzen")
        b_fit.clicked.connect(self._fit)
        b_wav = QPushButton(icon("document-export"), "Als WAVs exportieren")
        b_wav.clicked.connect(self._export_wavs)
        kl.addLayout(_row(b_fit, b_wav))
        rl.addWidget(kg, 3)

        # ---------------- Reiter „Auf dem FM-1“: Sample-Slots (Skiff/SLOOP) und FM1-Open-Bank – oben, auch bei 720p sichtbar
        self.dev_tab = QWidget()
        dl = QVBoxLayout(self.dev_tab)
        dl.setContentsMargins(0, 8, 0, 0)
        self.kit_hint = QLabel("")
        self.kit_hint.setWordWrap(True)
        self.kit_hint.setProperty("role", "muted")
        dl.addWidget(self.kit_hint)
        sg = QGroupBox("Sample-Slots auf dem FM-1 (Skiff, SLOOP)")
        sl = QVBoxLayout(sg)
        self.slot_list = QListWidget()
        sl.addWidget(self.slot_list)
        self.slot_info = QLabel("Noch nicht gelesen.")
        self.slot_info.setWordWrap(True)
        sl.addWidget(self.slot_info)
        self.b_sread = b_sread = QPushButton(icon("view-refresh"), "Lesen")
        b_sread.setToolTip("Slots vom FM-1 lesen (passiert beim Öffnen dieses Reiters automatisch)")
        b_sread.clicked.connect(self.read_slots)
        self.b_upload = QPushButton(icon("go-up", "document-send"), "Kit in Slot laden")
        self.b_upload.setProperty("variant", "primary")
        self.b_upload.setToolTip("Das Kit aus dem Kit-Baukasten (Reiter „Bibliothek & Kits“) in den gewählten Slot schreiben")
        self.b_upload.clicked.connect(self._upload)
        self.b_serase = b_serase = QPushButton(icon("edit-clear"), "Slot leeren")
        b_serase.setToolTip("Den gewählten Slot am FM-1 leeren (vorher wird sein Inhalt für „Rückgängig“ gelesen)")
        b_serase.clicked.connect(self._erase_slot)
        self.b_sundo = QPushButton(icon("edit-undo"), "Rückgängig")
        self.b_sundo.setToolTip("Den Slot-Inhalt von vor dem letzten Überschreiben/Leeren zurückspielen")
        self.b_sundo.setEnabled(False)
        self.b_sundo.clicked.connect(self._slot_undo)
        sl.addLayout(_row(b_sread, self.b_upload, b_serase, self.b_sundo))
        dl.addWidget(sg, 1)
        self._build_open(dl)
        dscroll = QScrollArea()
        dscroll.setWidgetResizable(True)
        dscroll.setFrameShape(QScrollArea.Shape.NoFrame)
        dscroll.setWidget(self.dev_tab)
        self.dev_tab_index = self.tabs.addTab(dscroll, icon("audio-card", "media-flash"), "Auf dem FM-1")
        self.tabs.currentChanged.connect(self._tab_changed)
        rscroll = QScrollArea()
        rscroll.setWidgetResizable(True)
        rscroll.setFrameShape(QScrollArea.Shape.NoFrame)
        rscroll.setWidget(right)
        rscroll.setMinimumWidth(340)
        rscroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        right.setMinimumWidth(300)
        left.setMinimumWidth(170)
        mid.setMinimumWidth(300)
        split.addWidget(rscroll)
        split.setChildrenCollapsible(False)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 3)
        split.setStretchFactor(2, 2)
        split.setSizes([200, 460, 380])
        # schmal (z. B. halber 1080p-Bildschirm): Filter über die Liste – zwei statt drei Spalten
        self._lib_split, self._lib_cols, self._lib_inner = split, (left, mid, rscroll), None
        on_narrow(split, lambda: sum(max(w.minimumWidth(), w.minimumSizeHint().width()) for w in (left, mid, rscroll))
                  + 2 * split.handleWidth() + 48, self._lib_narrow)

        # ---------------- weitere Reiter
        from .sample_recorder import SampleRecorder
        self.recorder = SampleRecorder(ctx, self.lib, self.player)
        self.recorder.saved.connect(self._recorded)
        rsc = QScrollArea()
        rsc.setWidgetResizable(True)
        rsc.setFrameShape(QScrollArea.Shape.NoFrame)
        rsc.setWidget(self.recorder)
        self.rec_tab = rsc
        self.tabs.addTab(rsc, icon("media-record", "audio-input-microphone"), "Aufnehmen")

        from .samplepacks_tab import PacksTab
        self.packs = PacksTab(ctx, self.lib)
        self.packs.downloaded.connect(lambda files: self._recorded(files[0]) if files else None)
        self.tabs.addTab(self.packs, icon("folder-download", "download", "folder"), "Sample-Pakete")

        ctx.changed.connect(self._dev_changed)
        self.b_serase.setEnabled(False)
        self._open_felucca_note()
        self._fill_kits()
        self._show_kit()
        self.refresh_filters()
        self.refresh_table()
        QTimer.singleShot(1500, self.scan)

    # ============================================================ Bibliothek
    def _add_root(self):
        d = QFileDialog.getExistingDirectory(self, "Sample-Ordner hinzufügen", str(Path.home()))
        if not d:
            return
        s = self.ctx.settings
        if d not in s.sample_roots:
            s.sample_roots.append(d)
            config.save(s)
        self.scan()

    def scan(self):
        roots = self.ctx.settings.all_sample_roots()
        self.lib_info.setText("lese Samples ein …")

        def work(w):
            return self.lib.scan(roots, progress=lambda n, name: w.log.emit(f"{n} Dateien … {name}"))
        run_job(self, work, on_done=self._scanned, on_log=lambda t: self.lib_info.setText(t),
                on_fail=lambda m, tb: self.lib_info.setText(f"Fehler beim Einlesen: {m}"))

    def _scanned(self, stats):
        st = self.lib.stats()
        self.lib_info.setText(f"{st['count']} Samples, {fmt_secs(st['seconds'])}, {fmt_size(st['bytes'])} · "
                              f"neu {stats['neu']}, geändert {stats['geändert']}, entfernt {stats['entfernt']}")
        self.refresh_filters()
        self.refresh_table()
        self._ffmpeg_note()

    def refresh_filters(self):
        cur = self.folders.currentItem().data(0, Qt.ItemDataRole.UserRole) if self.folders.currentItem() else ""
        self.folders.blockSignals(True)
        self.folders.clear()
        all_it = QTreeWidgetItem(["Alle Samples"])
        all_it.setData(0, Qt.ItemDataRole.UserRole, "")
        self.folders.addTopLevelItem(all_it)
        roots = [str(r) for r in self.ctx.settings.all_sample_roots()]
        nodes: dict[str, QTreeWidgetItem] = {}
        for r in roots:
            it = QTreeWidgetItem([Path(r).name or r])
            it.setToolTip(0, r)
            it.setData(0, Qt.ItemDataRole.UserRole, r)
            it.setIcon(0, icon("folder-sound", "folder-music", "folder"))
            self.folders.addTopLevelItem(it)
            nodes[r] = it
        for folder, count in self.lib.folders():
            root = next((r for r in roots if folder == r or folder.startswith(r.rstrip("/") + "/")), None)
            if not root:
                continue
            parent = nodes[root]
            rel = Path(folder).relative_to(root) if folder != root else Path()
            acc = root
            for part in rel.parts:
                acc = os.path.join(acc, part)
                if acc not in nodes:
                    it = QTreeWidgetItem([part])
                    it.setData(0, Qt.ItemDataRole.UserRole, acc)
                    it.setIcon(0, icon("folder"))
                    parent.addChild(it)
                    nodes[acc] = it
                parent = nodes[acc]
            parent.setText(0, f"{parent.text(0).split('  (')[0]}  ({count})")
        for it in nodes.values():
            if it.data(0, Qt.ItemDataRole.UserRole) == cur:
                self.folders.setCurrentItem(it)
        if not self.folders.currentItem():
            self.folders.setCurrentItem(all_it)
        self.folders.blockSignals(False)
        self.tags.blockSignals(True)
        cur_tag = self.tags.currentItem().data(Qt.ItemDataRole.UserRole) if self.tags.currentItem() else ""
        self.tags.clear()
        it = QListWidgetItem("alle")
        it.setData(Qt.ItemDataRole.UserRole, "")
        self.tags.addItem(it)
        for tag, n in self.lib.tags():
            it = QListWidgetItem(f"{tag}  ({n})")
            it.setData(Qt.ItemDataRole.UserRole, tag)
            self.tags.addItem(it)
            if tag == cur_tag:
                self.tags.setCurrentItem(it)
        self.tags.blockSignals(False)

    def _ffmpeg_note(self):
        """Hinweis, wenn MP3/FLAC & Co. ohne ffmpeg nicht gelesen werden können."""
        if S.have_ffmpeg():
            self.ffmpeg_hint.hide()
            return
        rows = self.lib.query("", "", "", False, 0)
        self.ffmpeg_hint.setVisible(any(str(r["ext"]).lower().lstrip(".") in NEEDS_FFMPEG for r in rows))

    def refresh_table(self):
        folder = self.folders.currentItem().data(0, Qt.ItemDataRole.UserRole) if self.folders.currentItem() else ""
        tag = self.tags.currentItem().data(Qt.ItemDataRole.UserRole) if self.tags.currentItem() else ""
        rows = self.lib.query(self.search.text(), folder or "", tag or "", self.chk_fav.isChecked(), self.max_len.value())
        self.table.setSortingEnabled(False)
        self.table.clear()
        for r in rows:
            chans = {1: "mono", 2: "stereo"}.get(r["channels"], f"{r['channels']} Kanäle")
            fmt = (f"{r['rate'] / 1000:.1f}".replace(".", ",") + f" kHz · {r['bits']} Bit · {chans}") if r["rate"] else r["ext"]
            dur = f"{r['duration']:.2f} s".replace(".", ",") if r["duration"] else "?"
            it = QTreeWidgetItem(["★" if r["favorite"] else "", r["name"], dur,
                                  fmt, r["tags"], Path(r["folder"]).name])
            if not r["duration"] and not r["rate"] and str(r["ext"]).lower().lstrip(".") in NEEDS_FFMPEG \
                    and not S.have_ffmpeg():
                it.setToolTip(2, FFMPEG_HINT)
                it.setText(3, f"{r['ext']} – für MP3/FLAC: ffmpeg fehlt")
            it.setData(0, Qt.ItemDataRole.UserRole, r["path"])
            it.setData(2, Qt.ItemDataRole.UserRole, r["duration"] or 0)
            it.setToolTip(1, r["path"] + (f"\n{r['notes']}" if r["notes"] else ""))
            if r["duration"] and r["duration"] > S.SLOT_SECONDS:
                it.setToolTip(2, "länger als ein ganzer Slot – muss gekürzt werden")
            self.table.addTopLevelItem(it)
        self.table.setSortingEnabled(True)
        for c in (0, 2, 3):
            self.table.resizeColumnToContents(c)
        self.table.setColumnWidth(1, max(180, self.table.columnWidth(1)))

    def _selected_paths(self) -> list[str]:
        return [it.data(0, Qt.ItemDataRole.UserRole) for it in self.table.selectedItems()]

    def _sample_selected(self, cur, _prev):
        if not cur:
            self.wave.clear()
            return
        path = cur.data(0, Qt.ItemDataRole.UserRole)

        def work(w):
            sr, x = S.read_mono(Path(path))
            return S.peaks(x, 800), len(x) / sr if sr else 0
        run_job(self, work, on_done=lambda r: self.wave.set_data(*r),
                on_fail=lambda m, tb: self.wave.clear(f"nicht lesbar: {m}"))
        if self.chk_auto.isChecked():
            self.player.play(Path(path), status=self.lib_info.setText)

    def _play_selected(self):
        it = self.table.currentItem()
        if it:
            self.player.play(Path(it.data(0, Qt.ItemDataRole.UserRole)), status=self.lib_info.setText)

    def deactivate(self):
        """Seite verlassen: Vorhören anhalten (die Aufnahme im Reiter „Aufnehmen“ läuft weiter)."""
        self.player.stop()

    # ------------------------------------------------------------ Ziehen & Ablegen
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and any(u.isLocalFile() for u in e.mimeData().urls()):
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.add_paths([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])

    def add_paths(self, paths: list[str]) -> int:
        """Ordner werden Bibliotheks-Ordner, einzelne Dateien landen in Samples › Importiert (Kopie).
        -> Zahl der übernommenen Ordner + Dateien (Dateien werden im Hintergrund kopiert)."""
        import shutil
        s = self.ctx.settings
        n = 0
        dest = s.samples_dir / "Importiert"
        files, skipped = [], []
        for p in map(Path, paths):
            if p.is_dir():
                if str(p) not in s.sample_roots:
                    s.sample_roots.append(str(p))
                    n += 1
            elif p.suffix.lower() in S.AUDIO_EXT:
                files.append(p)
            else:
                skipped.append(p.name)
        if n:
            config.save(s)
        if skipped:
            notify(f"{len(skipped)} Datei(en) übersprungen – keine Audiodatei: " + ", ".join(skipped[:5]), "warning")
        if not files:
            if n:
                self.scan()
                notify(f"{plural(n, 'Ordner', 'Ordner')} zur Bibliothek hinzugefügt – wird eingelesen.", "success")
            return n

        def work(w):
            done, errs = [], []
            dest.mkdir(parents=True, exist_ok=True)
            for i, p in enumerate(files):
                t = dest / p.name
                try:
                    if not t.exists():
                        shutil.copyfile(p, t)
                    self.lib.add_file(t, root=s.samples_dir, tags="importiert")
                    done.append(t)
                except OSError as e:
                    errs.append(f"{p.name}: {fs_error_text(e)}")
                w.progress.emit(i + 1, len(files))
            return done, errs

        def finish(res):
            done, errs = res
            self.scan()
            text = f"{plural(len(done), 'Datei', 'Dateien')} nach Samples › Importiert kopiert"
            text += f", {plural(n, 'Ordner', 'Ordner')} hinzugefügt" if n else ""
            notify(text + ".", "success" if not errs else "warning")
            if errs:
                show_error(self, "Nicht alle Dateien übernommen", "\n".join(errs))
        self.lib_info.setText(f"Kopiere {plural(len(files), 'Datei', 'Dateien')} …")
        run_job(self, work, on_done=finish, on_fail=lambda m, tb: show_error(self, "Dateien übernehmen", m),
                on_progress=lambda a, b: self.lib_info.setText(f"Kopiere … {a} von {b}"))
        return n + len(files)

    def find_duplicates(self):
        def done(groups):
            self.dupes = groups
            from PyQt6.QtWidgets import QDialog, QDialogButtonBox
            d = QDialog(self)
            d.setWindowTitle("Doppelte Samples")
            d.resize(720, 420)
            v = QVBoxLayout(d)
            v.addWidget(QLabel(f"{len(groups)} Gruppen mit gleichem Inhalt." if groups else
                               "Keine doppelten Dateien gefunden."))
            t = QTreeWidget()
            t.setHeaderLabels(["Datei", "Ordner"])
            for g in groups:
                top = QTreeWidgetItem([f"{len(g)}× {Path(g[0]).name}", ""])
                for f in g:
                    c = QTreeWidgetItem([Path(f).name, str(Path(f).parent)])
                    c.setData(0, Qt.ItemDataRole.UserRole, f)
                    top.addChild(c)
                t.addTopLevelItem(top)
            t.expandAll()
            t.itemDoubleClicked.connect(lambda it, _c: it.data(0, Qt.ItemDataRole.UserRole) and
                                        self.player.play(Path(it.data(0, Qt.ItemDataRole.UserRole)), status=self.lib_info.setText))
            v.addWidget(t, 1)
            v.addWidget(QLabel("Doppelklick: anhören. Löschen geht im Dateimanager („Im Ordner zeigen“)."))
            bb = german_buttons(QDialogButtonBox(QDialogButtonBox.StandardButton.Close))
            bb.rejected.connect(d.reject)
            v.addWidget(bb)
            d.exec()
        run_job(self, lambda w: self.lib.duplicates(), on_done=done)

    def _recorded(self, path: str):
        self.refresh_filters()
        self.refresh_table()
        st = self.lib.stats()
        self.lib_info.setText(f"{st['count']} Samples · neu: {Path(path).name}")

    def shutdown(self):
        self.player.stop()
        self.recorder.shutdown()

    def open_slicer(self, path: str | None = None):
        """Slicer für die gewählte (oder angegebene) Datei; baut auf Wunsch direkt ein Kit."""
        from .slicer_dialog import SlicerDialog
        if not path:
            it = self.table.currentItem()
            if not it:
                show_error(self, "Zerschneiden", "Wähle zuerst einen Sound (z. B. einen Drum-Loop) in der Liste.")
                return None
            path = it.data(0, Qt.ItemDataRole.UserRole)
        d = SlicerDialog(path, self, out_dir=self.ctx.settings.samples_dir / "Slices", player=self.player)
        d.exec()
        self.slicer_dialog = d
        if d.exported:
            self.scan()
        if d.kit is not None:
            self.take_kit(d.kit)
        return d

    def take_kit(self, kit: S.Kit):
        if self.kit_dirty and self.kit.zones and not confirm(
                self, "Neues Kit", "Das aktuelle Kit hat ungespeicherte Änderungen. Verwerfen?", "Verwerfen"):
            return False
        self.kit = kit
        self.kit_dirty = True
        self.tabs.setCurrentIndex(0)
        self._fill_kits()
        self._show_kit()
        return True

    def _toggle_fav(self):
        for it in self.table.selectedItems():
            fav = 0 if it.text(0) else 1
            self.lib.set(it.data(0, Qt.ItemDataRole.UserRole), favorite=fav)
            it.setText(0, "★" if fav else "")

    def _edit_tags(self):
        items = self.table.selectedItems()
        if not items:
            return
        cur = items[0].text(4)
        txt, ok = QInputDialog.getText(self, "Schlagwörter", "Durch Komma getrennt (z. B. kick, po-33, dreckig):", text=cur)
        if not ok:
            return
        tags = ",".join(t.strip().lower() for t in txt.split(",") if t.strip())
        for it in items:
            self.lib.set(it.data(0, Qt.ItemDataRole.UserRole), tags=tags)
            it.setText(4, tags)
        self.refresh_filters()

    def _show_in_folder(self):
        it = self.table.currentItem()
        if it:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(it.data(0, Qt.ItemDataRole.UserRole)).parent)))

    # ================================================================ Kits
    def _fill_kits(self):
        self.kit_combo.blockSignals(True)
        self.kit_combo.clear()
        self.kit_combo.addItem("(ungespeichertes Kit)", "")
        try:
            kits = sorted(safe_folder("Kits")[0].glob(f"*{S.KIT_SUFFIX}"))
        except OSError:
            kits = []
        for p in kits:
            self.kit_combo.addItem(p.stem, str(p))
        idx = self.kit_combo.findData(self.kit.path)
        self.kit_combo.setCurrentIndex(max(0, idx))
        self.kit_combo.blockSignals(False)

    def _kit_chosen(self, _i):
        path = self.kit_combo.currentData()
        if not path:
            return
        if self.kit_dirty and not confirm(self, "Kit wechseln", "Ungespeicherte Änderungen verwerfen?", "Verwerfen"):
            self._fill_kits()
            return
        try:
            self.kit = S.Kit.load(Path(path))
        except OSError as e:
            show_error(self, "Kit öffnen", fs_error_text(e, "Kit nicht lesbar"))
            self._fill_kits()
            return
        except Exception:  # noqa: BLE001 – kaputtes JSON o. Ä.
            show_error(self, "Kit beschädigt", f"„{Path(path).stem}“ ist keine gültige Kit-Datei (beschädigt oder von "
                       "einer anderen App). Das aktuelle Kit bleibt offen.")
            self._fill_kits()
            return
        self.kit_dirty = False
        self._show_kit()

    def _kit_new(self):
        if self.kit_dirty and not confirm(self, "Neues Kit", "Ungespeicherte Änderungen verwerfen?", "Verwerfen"):
            return
        self.kit = S.Kit(name="KIT 1")
        self.kit_dirty = False
        self._fill_kits()
        self._show_kit()

    def _kit_save(self):
        new_path = None
        if not self.kit.path:
            name, ok = QInputDialog.getText(self, "Kit speichern", "Name der Kit-Datei:", text=self.kit.name.title())
            if not ok or not name.strip():
                return
            stem = S.safe_kit_name(name)
            if not stem:
                show_error(self, "Kit speichern", "Dieser Name taugt nicht als Dateiname – bitte Buchstaben oder Ziffern "
                           "verwenden. Nichts gespeichert.")
                return
            folder, err = safe_folder("Kits")
            if err:
                show_error(self, "Kit speichern", err)
                return
            new_path = folder / f"{stem}{S.KIT_SUFFIX}"
            if new_path.exists() and not confirm(
                    self, "Kit überschreiben", f"Es gibt schon ein Kit „{stem}“. Ersetzen?\n\nDie alte Kit-Datei wird "
                    "überschrieben (die Samples darin bleiben auf der Platte).", "Überschreiben", danger=True):
                return
            self.kit.path = str(new_path)
        try:
            self.kit.save()
        except OSError as e:
            if new_path is not None:
                self.kit.path = ""
            show_error(self, "Kit speichern", fs_error_text(e, "Kit nicht gespeichert"))
            return
        self.kit_dirty = False
        self._fill_kits()
        notify(f"Kit „{Path(self.kit.path).stem}“ gespeichert ({len(self.kit.zones)} Sounds) – nur auf dem Rechner.",
               "success")

    def _kit_delete(self):
        if not self.kit.path:
            show_error(self, "Kit löschen", "Dieses Kit ist noch nicht gespeichert – es gibt keine Datei zu löschen. "
                       "„Neu“ verwirft es.")
            return
        if confirm(self, "Kit löschen", f"Kit „{Path(self.kit.path).stem}“ löschen ({len(self.kit.zones)} Sounds)?\n\n"
                   "Nur die Kit-Datei wird gelöscht – die Samples bleiben, und am FM-1 ändert sich nichts.",
                   "Löschen", danger=True):
            try:
                Path(self.kit.path).unlink(missing_ok=True)
            except OSError as e:
                show_error(self, "Kit löschen", fs_error_text(e, "Nicht gelöscht"))
                return
            notify(f"Kit „{Path(self.kit.path).stem}“ gelöscht (nur die Datei; Samples und FM-1 unverändert).", "success")
            self.kit = S.Kit(name="KIT 1")
            self.kit_dirty = False
            self._fill_kits()
            self._show_kit()

    def _kit_meta(self, *_):
        self.kit.name = self.kit_name.text().upper() or "KIT"
        self.kit.mode = self.kit_mode.currentData()
        self.kit.base_note = self.kit_base.value()
        self.kit.assign_keys() if self.kit.mode == "drum" else None
        self.kit_dirty = True
        self._fill_zones(keep=True)

    def _show_kit(self):
        k = self.kit
        self.kit_name.setText(k.name)
        self.kit_mode.setCurrentIndex(max(0, self.kit_mode.findData(k.mode)))
        self.kit_base.blockSignals(True)
        self.kit_base.setValue(k.base_note)
        self.kit_base.blockSignals(False)
        self._fill_zones()

    def _fill_zones(self, keep=False):
        row = self.zones.indexOfTopLevelItem(self.zones.currentItem()) if keep and self.zones.currentItem() else 0
        self.zones.clear()
        for i, z in enumerate(self.kit.zones):
            key = S.note_name(z.root) if self.kit.mode == "drum" else f"Grundton {S.note_name(z.root)}"
            self.zones.addTopLevelItem(QTreeWidgetItem([str(i + 1), z.name, key, "…"]))
        if self.kit.zones:
            self.zones.setCurrentItem(self.zones.topLevelItem(min(row, len(self.kit.zones) - 1)))
        self._update_budget()

    def _update_budget(self):
        kit = self.kit
        self.last_budget = None
        if not kit.zones:
            self.budget.setRange(0, int(S.SLOT_SECONDS * 1000))
            self.budget.setValue(0)
            self.budget.setFormat(f"0 s von {S.SLOT_SECONDS:.1f} s – ein Slot fasst alle Sounds zusammen".replace(".", ","))
            return

        def work(w):
            return S.kit_budget(kit)

        def done(res):
            total, per = res
            self.last_budget = total
            ms = int(total * 2 / S.SLOT_RATE * 1000)
            self.budget.setRange(0, int(S.SLOT_SECONDS * 1000))
            self.budget.setValue(min(ms, self.budget.maximum()))
            over = total > S.SLOT_BYTES
            self.budget.setFormat((("ZU LANG: " if over else "") + f"{ms / 1000:.2f} s von {S.SLOT_SECONDS:.1f} s").replace(".", ","))
            self.budget.setStyleSheet("QProgressBar::chunk{background:#da4453}" if over else "")
            for i, b in enumerate(per):
                it = self.zones.topLevelItem(i)
                if it:
                    it.setText(3, f"{b * 2 / S.SLOT_RATE:.2f} s".replace(".", ","))
        run_job(self, work, on_done=done, on_fail=lambda m, tb: self.budget.setFormat(f"Fehler: {m}"))

    def _add_to_kit(self):
        paths = self._selected_paths()
        if not paths:
            return
        room = 16 - len(self.kit.zones)
        if room <= 0:
            show_error(self, "Kit voll", "Ein Slot fasst höchstens 16 Sounds.")
            return
        st = self.ctx.settings
        new_zones = [S.Zone(path=p, edit=S.Edit(normalize=st.sample_default_normalize, trim_silence=st.sample_default_trim))
                     for p in paths[:room]]
        self.kit.zones += new_zones
        if self.kit.mode == "drum":
            self.kit.assign_keys()
        self.lib.mark_used(paths[:room])
        self.kit_dirty = True
        self._fill_zones()
        if self.kit.mode == "keys":            # Grundton erkennen – Audio lesen gehört nicht in den GUI-Thread
            def work(w):
                out = {}
                for z in new_zones:
                    try:
                        sr, x = S.read_mono(Path(z.path))
                        out[z.path] = S.detect_root(sr, x, Path(z.path).stem)
                    except Exception:  # noqa: BLE001 – dann bleibt C4
                        pass
                return out

            def done(roots):
                for z in new_zones:
                    if z.path in roots and z in self.kit.zones:
                        z.root = roots[z.path]
                self._fill_zones(keep=True)
                self._zone_selected()
            run_job(self, work, on_done=done, on_fail=lambda m, tb: None)
        if len(paths) > room:
            show_error(self, "Kit voll", f"Nur {room} von {len(paths)} Sounds passten noch hinein – ein Slot hat höchstens "
                       "16 Sounds. Für die übrigen ein zweites Kit anlegen.")

    def _zone(self) -> S.Zone | None:
        it = self.zones.currentItem()
        i = self.zones.indexOfTopLevelItem(it) if it else -1
        return self.kit.zones[i] if 0 <= i < len(self.kit.zones) else None

    def _zone_selected(self, *_):
        z = self._zone()
        widgets = (self.z_root, self.z_norm, self.z_trim, self.z_rev, self.z_gain, self.z_fade)
        for wdg in widgets:
            wdg.blockSignals(True)
        if z:
            self.z_root.setValue(z.root)
            self.z_root.setEnabled(self.kit.mode == "keys")
            self.z_norm.setChecked(z.edit.normalize)
            self.z_trim.setChecked(z.edit.trim_silence)
            self.z_rev.setChecked(z.edit.reverse)
            self.z_gain.setValue(z.edit.gain_db)
            self.z_fade.setValue(z.edit.fade_out_ms)
            path = z.path

            def work(w):
                sr, x = S.read_mono(Path(path))
                return S.peaks(x, 600), len(x) / sr
            run_job(self, work, on_done=lambda r, z=z: self.zone_wave.set_data(r[0], r[1], z.edit.start,
                                                                               z.edit.end or r[1], editable=True),
                    on_fail=lambda m, tb: self.zone_wave.clear(m))
        else:
            self.zone_wave.clear("keine Zone gewählt")
        for wdg in widgets:
            wdg.blockSignals(False)

    def _zone_edit(self, *_):
        z = self._zone()
        if not z:
            return
        z.root = self.z_root.value()
        z.edit.normalize = self.z_norm.isChecked()
        z.edit.trim_silence = self.z_trim.isChecked()
        z.edit.reverse = self.z_rev.isChecked()
        z.edit.gain_db = self.z_gain.value()
        z.edit.fade_out_ms = self.z_fade.value()
        self.kit_dirty = True
        self._fill_zones(keep=True)

    def _markers(self, a, b):
        z = self._zone()
        if z:
            z.edit.start, z.edit.end = round(a, 4), round(b, 4)
            z.edit.trim_silence = False
            self.z_trim.blockSignals(True)
            self.z_trim.setChecked(False)
            self.z_trim.blockSignals(False)
            self.kit_dirty = True
            self._update_budget()

    def _move(self, d):
        it = self.zones.currentItem()
        i = self.zones.indexOfTopLevelItem(it) if it else -1
        j = i + d
        if 0 <= i < len(self.kit.zones) and 0 <= j < len(self.kit.zones):
            zs = self.kit.zones
            zs[i], zs[j] = zs[j], zs[i]
            if self.kit.mode == "drum":
                self.kit.assign_keys()
            self.kit_dirty = True
            self._fill_zones()
            self.zones.setCurrentItem(self.zones.topLevelItem(j))

    def _remove_zone(self):
        it = self.zones.currentItem()
        i = self.zones.indexOfTopLevelItem(it) if it else -1
        if 0 <= i < len(self.kit.zones):
            del self.kit.zones[i]
            if self.kit.mode == "drum":
                self.kit.assign_keys()
            self.kit_dirty = True
            self._fill_zones()

    def _hear_zone(self):
        z = self._zone()
        if not z:
            return
        run_job(self, lambda w: S.render_zone(z), on_done=lambda s: self.player.play_samples(s, S.SLOT_RATE, status=self.lib_info.setText),
                on_fail=lambda m, tb: show_error(self, "Anhören", m))

    def _fit(self):
        if not self.kit.zones:
            return
        run_job(self, lambda w: S.fit_to_slot(self.kit), on_done=lambda _: (self._fill_zones(keep=True), self._zone_selected()),
                done_text="Kit auf Slot-Länge gekürzt (nur im Kit-Baukasten, Dateien unverändert).")
        self.kit_dirty = True

    def _export_wavs(self):
        if not self.kit.zones:
            return
        d = QFileDialog.getExistingDirectory(self, "Wohin exportieren?", str(safe_folder("Kits")[0]))
        if d:
            run_job(self, lambda w: S.export_wavs(self.kit, Path(d) / self.kit.name),
                    done_text=lambda files: f"{len(files)} WAV-Dateien exportiert nach {Path(d) / self.kit.name}",
                    fail_text=lambda m: f"WAV-Export fehlgeschlagen: {m}")

    # ================================================================ Slots
    def _dev_changed(self):
        if hasattr(self, "open_list"):
            self.open_entries, self.open_bank = None, b""
            self.open_list.clear()
            self.open_budget.setFormat("noch nicht gelesen")
            self.open_budget.setValue(0)
            self._open_enable()
        self.slot_list.clear()
        self.slot_list.setVisible(True)
        self.slots = []
        self._slots_busy = False
        self._slots_reading = None
        self._slots_gen = getattr(self, "_slots_gen", 0) + 1   # Ergebnisse älterer Lesevorgänge verwerfen
        self.slot_info.setText(self._no_device_text())
        self.b_upload.setEnabled(self.ctx.connected)
        self.b_upload.setToolTip(self._upload_tip())
        self.b_sread.setEnabled(self.ctx.link is not None)
        self.b_serase.setEnabled(False)                # erst nach dem Lesen, mit gewähltem Slot
        self._open_felucca_note()
        self._slot_undo_enable()
        if self.ctx.connected and self.tabs.currentIndex() == self.dev_tab_index:
            self._tab_changed(self.dev_tab_index)

    def _no_device_text(self) -> str:
        if self.ctx.connected:
            return "Noch nicht gelesen."
        if getattr(self.ctx, "midi_connected", False) or self.ctx.link is not None:
            return ("Dieses FM-1 spricht nicht das Felucca-Protokoll – Sample-Slots von Skiff/SLOOP kann die App hier "
                    "nicht lesen. Läuft FM1 Open darauf, nutze den Kasten darunter („Lesen & sichern“).")
        return "Kein FM-1 verbunden – so geht's: FM-1 per USB anstecken; mit Skiff oder SLOOP erscheinen hier die Slots."

    def _upload_tip(self) -> str:
        if not self.ctx.connected:
            return "Erst ein FM-1 mit Skiff oder SLOOP verbinden – diese Firmware hat die Sample-Slots"
        if not self.slots:
            return "Erst „Lesen“ – dann einen Slot wählen"
        return "Das Kit aus dem Kit-Baukasten (Reiter „Bibliothek & Kits“) in den gewählten Slot schreiben"

    def _slot_undo_enable(self):
        self.b_sundo.setEnabled(bool(self.slot_undo))
        self.b_sundo.setToolTip("Den Slot-Inhalt von vor dem letzten Überschreiben/Leeren zurückspielen"
                                if not self.slot_undo or self.slot_undo.matches(self.ctx) else self.slot_undo.refusal())

    def read_slots(self):
        if not self.ctx.connected and getattr(self.ctx, "midi_connected", self.ctx.link is not None) \
                and self.ctx.link is not None:
            self.slot_info.setText("Kein Felucca-Gerät – siehe FM1 Open darunter.")
            self.open_read()
            return
        if not self.ctx.require(self):
            self._slots_busy = False
            return
        # Gerät + Verbindungs-Generation: auch ein Neu-Verbinden desselben FM-1 macht alte Ergebnisse ungültig
        key = (self.ctx.port.key if self.ctx.port else "", getattr(self, "_slots_gen", 0))
        if getattr(self, "_slots_reading", None) == key:
            return                                     # läuft schon für dieses Gerät (z. B. Reiterwechsel + Knopf)
        self._slots_reading = key
        self.slot_info.setText("Lese die Sample-Slots …")

        def fail(msg, tb):
            if self._slots_reading == key:
                self._slots_reading = None
            self._slots_busy = False
            if self._current_key() == key:
                self.slot_info.setText(f"Slots lesen fehlgeschlagen: {msg}")
        self.ctx.job(self, "Slots lesen", lambda w, link: S.slot_info(link), dialog=False,
                     on_done=lambda res: self._got_slots(res, key), on_fail=fail)

    def _current_key(self) -> tuple:
        return (self.ctx.port.key if self.ctx.port else "", getattr(self, "_slots_gen", 0))

    def _lib_narrow(self, narrow: bool):
        """Bibliothek: drei Spalten (Filter | Liste | Kit) oder zwei (Filter über Liste | Kit)."""
        split = self._lib_split
        left, mid, kit = self._lib_cols
        total = max(split.width(), 600)
        if narrow:
            if self._lib_inner is None:
                self._lib_inner = QSplitter(Qt.Orientation.Vertical)
                self._lib_inner.setChildrenCollapsible(False)
            inner = self._lib_inner
            split.insertWidget(0, inner)
            inner.addWidget(left)
            inner.addWidget(mid)
            inner.setStretchFactor(0, 2)
            inner.setStretchFactor(1, 3)
            inner.show()
            sizes = {inner: total * 3 // 5, kit: total * 2 // 5}
        else:
            split.insertWidget(0, left)
            split.insertWidget(1, mid)
            if self._lib_inner is not None:
                self._lib_inner.hide()
            sizes = {left: total * 2 // 10, mid: total * 45 // 100, kit: total * 35 // 100}
        split.setSizes([sizes.get(split.widget(i), 0) for i in range(split.count())])

    def _tab_changed(self, i: int):
        """Reiter „Auf dem FM-1“ geöffnet: Slots automatisch lesen (einmal je Gerät), Kit-Hinweis aktualisieren."""
        if i != self.dev_tab_index:
            return
        self._kit_hint()
        if self.ctx.connected and not self.slots and not getattr(self, "_slots_busy", False):
            self._slots_busy = True
            self.read_slots()

    def _kit_hint(self):
        k = getattr(self, "kit", None)
        name = (k.name if k is not None and getattr(k, "name", "") else "") or "(ungespeichertes Kit)"
        n = len(k.zones) if k is not None else 0
        self.kit_hint.setText(f"Zum Hochladen bereit: <b>{name}</b> mit {n} Sounds aus dem Kit-Baukasten "
                              "(Reiter „Bibliothek & Kits“)." if n else
                              "Noch kein Kit gebaut – im Reiter „Bibliothek & Kits“ Sounds markieren und „Ins Kit“.")

    def _got_slots(self, res, key=None):
        if key is not None:
            if self._slots_reading == key:
                self._slots_reading = None
            if key != self._current_key():
                return                                 # Ergebnis eines inzwischen getrennten/gewechselten Geräts
        self._slots_busy = False
        kib, slots = res
        keep = self.slot_list.currentRow()
        self.slots = slots
        self.slot_list.clear()
        if not slots:
            fw = self.ctx.title() if hasattr(self.ctx, "title") else "Diese Firmware"
            self.slot_info.setText(f"<b>{fw} hat keine Sample-Slots</b> (Melodee ab 1.5 spielt nur Synth- und "
                                   "Drum-Engines). Kits kannst du trotzdem bauen, speichern und als WAVs exportieren – "
                                   "hochladen geht auf ein FM-1 mit Skiff oder SLOOP.")
            self.slot_list.setVisible(False)          # keine leere Liste, die nach „hier fehlt was“ aussieht
            self.b_upload.setEnabled(False)
            self.b_upload.setToolTip("Diese Firmware hat keine Sample-Slots (Melodee ab 1.5) – Hochladen ist hier nicht möglich")
            self.b_serase.setEnabled(False)
            self.b_serase.setToolTip("Diese Firmware hat keine Sample-Slots")
            self.b_sread.setEnabled(False)
            self.b_sread.setToolTip("Diese Firmware hat keine Sample-Slots – nach einem Gerätewechsel liest die App neu")
            return
        self.b_sread.setEnabled(True)
        self.b_sread.setToolTip("Slots vom FM-1 lesen (passiert beim Öffnen dieses Reiters automatisch)")
        self.slot_list.setVisible(True)
        self.b_serase.setEnabled(True)
        self.b_upload.setEnabled(True)
        self.b_upload.setToolTip(self._upload_tip())
        for s in slots:
            it = QListWidgetItem(f"USR{s.index + 1}: " + ("leer" if s.empty else f"{s.name} – {s.zones} Sounds, {s.kib} KiB"))
            it.setData(Qt.ItemDataRole.UserRole, s.index)
            self.slot_list.addItem(it)
        self.slot_info.setText(f"{len(slots)} Slots zu je {kib} KiB.")
        self.slot_list.setCurrentRow(keep if 0 <= keep < len(slots) else 0)    # Auswahl bleibt beim Neu-Lesen

    def _upload(self):
        it = self.slot_list.currentItem()
        if not it or not self.ctx.require(self):
            show_error(self, "Slot wählen", "Erst „Lesen“, dann einen Slot auswählen.")
            return
        if not self.kit.zones:
            show_error(self, "Leeres Kit", "Füge dem Kit zuerst Sounds hinzu.")
            return
        if getattr(self, "last_budget", None) is not None and self.last_budget > S.SLOT_BYTES:
            show_error(self, "Kit zu lang", S.budget_text(self.last_budget) + "\n\nAm FM-1 wurde nichts geändert.")
            return
        slot = it.data(Qt.ItemDataRole.UserRole)
        cur = self.slots[slot]
        if not confirm(self, "Kit in Slot laden",
                       (f"USR{slot + 1} enthält „{cur.name}“ ({cur.zones} Sounds) und wird durch das Kit "
                        f"„{self.kit.name}“ ersetzt." if not cur.empty else
                        f"Das Kit „{self.kit.name}“ kommt in den leeren Slot USR{slot + 1}.")
                       + "<br><br>Vorher liest die App den alten Slot-Inhalt, damit du ihn mit „Rückgängig“ "
                         "zurückholen kannst (solange die App läuft).", "Überschreiben" if not cur.empty else "Laden",
                       danger=not cur.empty):
            return
        kit = self.kit
        fw = self.ctx.firmware_key
        kept: list = []
        tag = device_tag(self.ctx)

        def work(w, link):
            w.log.emit(f"Sichere USR{slot + 1} für „Rückgängig“ …")
            kept.append(S.read_slot(link, slot, fw))
            w.log.emit("Baue den Slot (22 050 Hz, IMA-ADPCM) …")
            hdr, data = S.build_slot(kit)
            w.log.emit(f"{len(data)} Bytes, {hdr[6]} Sounds – übertrage …")
            S.upload(link, slot, hdr, data, progress=lambda a, b: w.progress.emit(a, b))
            w.log.emit(f"USR{slot + 1} = {kit.name}")
            return S.slot_info(link)
        d = self.ctx.job(self, f"Kit in USR{slot + 1} laden", work,
                         done_text=f"Kit „{kit.name}“ ({len(kit.zones)} Sounds) liegt jetzt in USR{slot + 1}. Der alte "
                         "Inhalt ist gesichert – „Rückgängig“ holt ihn zurück.",
                         fail_text=lambda m: f"Nicht hochgeladen: {m}" + (" – der alte Slot-Inhalt ist gesichert "
                                                                          "(„Rückgängig“)." if kept else
                                                                          " – am FM-1 wurde nichts geändert."))
        if kept:                               # auch nach einem Abbruch mittendrin
            self.slot_undo.push(self.ctx, (slot, kept[0]), f"USR{slot + 1}", tag)
        self._slot_undo_enable()
        if d.ok:
            self._got_slots(d.result_value)

    def _erase_slot(self):
        it = self.slot_list.currentItem()
        if not it or not self.ctx.require(self):
            return
        slot = it.data(Qt.ItemDataRole.UserRole)
        cur = self.slots[slot] if 0 <= slot < len(self.slots) else None
        what = f"„{cur.name}“ ({cur.zones} Sounds)" if cur and not cur.empty else "den (schon leeren) Slot"
        if not confirm(self, "Slot leeren", f"USR{slot + 1} leeren – das löscht {what} auf dem FM-1.<br><br>"
                       "Vorher liest die App den Inhalt, damit du ihn mit „Rückgängig“ zurückholen kannst "
                       "(solange die App läuft).", "Leeren", danger=True):
            return
        fw = self.ctx.firmware_key
        kept: list = []
        tag = device_tag(self.ctx)

        def work(w, link):
            w.log.emit(f"Sichere USR{slot + 1} für „Rückgängig“ …")
            kept.append(S.read_slot(link, slot, fw))
            S.erase(link, slot)
            return S.slot_info(link)
        d = self.ctx.job(self, "Slot leeren", work,
                         done_text=f"USR{slot + 1} ist leer. Der alte Inhalt ist gesichert – „Rückgängig“ holt ihn zurück.")
        if kept:
            self.slot_undo.push(self.ctx, (slot, kept[0]), f"USR{slot + 1}", tag)
        self._slot_undo_enable()
        if d.ok:
            self._got_slots(d.result_value)

    def _slot_undo(self):
        if not self.slot_undo or not self.ctx.require(self) or not self.slot_undo.check(self.ctx, self):
            return
        slot, data = self.slot_undo.peek()
        if not confirm(self, "Rückgängig", f"USR{slot + 1} wieder auf den Stand vor der letzten Änderung setzen"
                       + (" (leer)" if not data else "") + "?", "Wiederherstellen"):
            return

        def work(w, link):
            S.restore_slot(link, slot, data, progress=lambda a, b: w.progress.emit(a, b))
            return S.slot_info(link)
        d = self.ctx.job(self, f"USR{slot + 1} wiederherstellen", work,
                         done_text=f"USR{slot + 1} hat wieder den alten Inhalt" + (" (leer)." if not data else "."))
        if d.ok:
            self.slot_undo.pop()
            self._got_slots(d.result_value)
        self._slot_undo_enable()


# ================================================================ FM1 Open
NEEDS_FFMPEG = {"mp3", "flac", "ogg", "opus", "m4a"}
FFMPEG_HINT = "Länge „?“: für MP3/FLAC: sudo apt install ffmpeg"
OPEN_TITLE = "FM1 Open: 8 Slots, gemeinsam ~19,5 s"


def _write_bytes_atomic(path: Path, data: bytes):
    """Backup-Datei ganz oder gar nicht schreiben (versteckte .tmp-Datei, dann umbenennen)."""
    import os
    import tempfile
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _open_felucca_note(self):
    """FM1 Open ist eine eigene Firmware: Spricht das FM-1 Felucca (Melodee/SLOOP/Skiff), kann es kein FM1 Open sein –
    dann ist der Kasten gesperrt und sagt das, statt beim Lesen nach Sekunden „keine Antwort“ zu melden."""
    felucca = bool(self.ctx.connected)
    self.b_open_read.setEnabled(self.ctx.link is not None and not felucca)
    self.b_open_restore.setEnabled(not felucca)
    self.open_list.setVisible(not felucca)          # keine leere Liste für ein Gerät, das gar kein FM1 Open ist
    self.open_budget.setVisible(not felucca)
    if felucca:
        self.open_info.setText(f"Nur für ein FM-1 mit der M-VAVE-Firmware FM1 Open – dieses läuft {self.ctx.title()}.")
    elif self.open_entries is None:
        self.open_info.setText(OPEN_HINT)


OPEN_HINT = ("Für ein FM-1 mit FM1 Open: erst „Lesen“ – die App sichert die ganze Bank, bevor sie etwas schreibt "
             "(das Gerät schreibt immer alle 8 Slots neu).")


def _build_open(self, rl):
    from .. import fm1open as F
    self.F = F
    self.open_entries = None
    self.open_bank = b""
    self.open_cap = F.CAPACITY
    g = QGroupBox(OPEN_TITLE)
    g.setToolTip("Offizielle M-VAVE-Firmware FM1 Open (V14): 8 Sample-Pads teilen sich eine Bank")
    lay = QVBoxLayout(g)
    self.open_list = QListWidget()
    self.open_list.setMinimumHeight(150)
    lay.addWidget(self.open_list)
    self.open_budget = QProgressBar()
    self.open_budget.setRange(0, int(F.capacity_seconds() * 1000))
    self.open_budget.setFormat("noch nicht gelesen")
    self.open_budget.setValue(0)
    lay.addWidget(self.open_budget)
    self.open_info = QLabel(OPEN_HINT)
    self.open_info.setWordWrap(True)
    self.open_info.setProperty("role", "muted")
    lay.addWidget(self.open_info)
    self.b_open_read = b_read = QPushButton(icon("view-refresh"), "Lesen && sichern")
    b_read.setToolTip("Bank vom FM-1 lesen und als .fm1s-Backup speichern (Backups › Gerät)")
    b_read.clicked.connect(self.open_read)
    self.b_open_zone = QPushButton(icon("go-up"), "Zone → Slot")
    self.b_open_zone.setToolTip("Gewählte Kit-Zone in den gewählten Slot (32 kHz, SEA5)")
    self.b_open_zone.clicked.connect(lambda: self.open_upload("zone"))
    self.b_open_kit = QPushButton(icon("go-up"), "Kit → Slots 1–8")
    self.b_open_kit.setToolTip("Die ersten 8 Sounds des Kits in Slot 1–8; übrige Slots bleiben")
    self.b_open_kit.clicked.connect(lambda: self.open_upload("kit"))
    self.b_open_clear = QPushButton(icon("edit-clear"), "Slot leeren")
    self.b_open_clear.clicked.connect(lambda: self.open_upload("clear"))
    self.b_open_export = QPushButton(icon("document-export"), "Bank-Datei …")
    self.b_open_export.setToolTip("Bank als .fm1s speichern – lässt sich auch in der Workbench von M-VAVE laden")
    self.b_open_export.clicked.connect(self.open_export)
    self.b_open_restore = QPushButton(icon("document-revert", "document-open"), "Backup zurückspielen …")
    self.b_open_restore.clicked.connect(lambda: self.open_upload("restore"))
    lay.addLayout(_row(b_read, self.b_open_zone, self.b_open_kit))
    lay.addLayout(_row(self.b_open_clear, self.b_open_export, self.b_open_restore))
    rl.addWidget(g, 1)
    self._open_enable()


def _open_enable(self):
    on = self.open_entries is not None
    for b in (self.b_open_zone, self.b_open_kit, self.b_open_clear, self.b_open_export):
        b.setEnabled(on)


def _open_show(self, entries, planned=None):
    F = self.F
    self.open_list.clear()
    for i, e in enumerate(entries):
        head = f"{i + 1} · {F.PAD_ROLES[i]} ({S.note_name(F.PAD_NOTES[i])})"
        it = QListWidgetItem(f"{head}: " + ("leer" if e.empty else f"{e.name or '(ohne Name)'} – " + f"{e.seconds:.2f} s".replace(".", ",")))
        it.setData(Qt.ItemDataRole.UserRole, i)
        self.open_list.addItem(it)
    used = F.used_seconds(entries)
    cap = F.capacity_seconds(self.open_cap)
    self.open_budget.setRange(0, int(cap * 1000))
    self.open_budget.setValue(min(int(used * 1000), self.open_budget.maximum()))
    self.open_budget.setFormat(f"{used:.2f} s von {cap:.2f} s belegt".replace(".", ","))
    if self.open_list.currentRow() < 0:
        self.open_list.setCurrentRow(0)


def _open_backup_dir(self):
    d = self.ctx.device_folder(self.ctx.settings.backups_dir) if hasattr(self.ctx, "device_folder") else \
        self.ctx.settings.backups_dir
    return Path(d)


def open_read(self):
    if self.ctx.link is None:
        show_error(self, "Kein Gerät", "Bitte ein FM-1 mit FM1 Open per USB verbinden.")
        return
    F = self.F
    folder = self._open_backup_dir()

    def work(w, link):
        with F.Client(link) as c:
            w.log.emit("Frage das FM-1 nach FM1 Open …")
            info = c.probe()
            st, size = c.sample_status()
            if st == 0:
                return info, None, None
            w.log.emit(f"Lese die Sample-Bank ({size // 1024} KiB) …")
            bank = c.read_bank(lambda a, b: w.progress.emit(a, b))
        F.read_bank(bank)                 # prüft die Bank, bevor sie gesichert wird
        p = folder / time.strftime("FM1-Open-Samples %Y-%m-%d %H-%M-%S.fm1s")
        _write_bytes_atomic(p, bank)
        w.log.emit(f"Gesichert: {p}")
        return info, bank, p

    def done(res):
        info, bank, p = res
        self.open_cap = info["capacity"]
        if bank is None:
            self.open_entries, self.open_bank = F.empty_bank(), b""
            self.open_info.setText("FM1 Open erkannt, aber ohne gültige Sample-Bank. Du kannst eine neue Bank schreiben.")
        else:
            self.open_entries, self.open_bank = F.read_bank(bank), bank
            self.open_info.setText(f"FM1 Open erkannt. Backup: {p.name}")
        self._open_show(self.open_entries)
        self._open_enable()
    d = self.ctx.job(self, "FM1 Open: Sample-Bank lesen", work, on_done=done,
                     done_text=lambda r: ("Sample-Bank gelesen und als Backup gesichert: " + r[2].name if r[1] is not None
                                          else "FM1 Open erkannt – noch keine Sample-Bank auf dem Gerät.")
                     + " Am FM-1 wurde nichts geändert.")
    if not d.ok:
        self.open_info.setText("Kein FM1 Open gefunden (oder keine Antwort). Läuft FM1 Open V13/V14 auf dem FM-1?")


def open_export(self):
    if self.open_entries is None:
        return
    p, _ = QFileDialog.getSaveFileName(self, "Sample-Bank speichern", str(self._open_backup_dir() / "FM1-samples.fm1s"),
                                       "FM1-Open-Sample-Bank (*.fm1s)")
    if p:
        try:
            _write_bytes_atomic(Path(p), self.open_bank or self.F.build_bank(self.open_entries))
        except (OSError, self.F.FM1OpenError) as e:
            show_error(self, "Bank-Datei speichern", fs_error_text(e, "Nicht gespeichert") if isinstance(e, OSError) else str(e))
            return
        self.open_info.setText(f"Bank-Datei gespeichert: {p} – am FM-1 wurde nichts geändert.")


def open_upload(self, what: str, restore_path: str | None = None):
    """what: "zone" | "kit" | "clear" | "restore". Schreibt die ganze Bank – mit Rückfrage und Backup."""
    F = self.F
    if self.ctx.link is None:
        show_error(self, "Kein Gerät", "Bitte ein FM-1 mit FM1 Open per USB verbinden.")
        return False
    if what != "restore" and self.open_entries is None:
        show_error(self, "Erst lesen", "Lies die Sample-Bank zuerst („Lesen & sichern“) – so bleibt eine Sicherung.")
        return False
    slot = max(0, self.open_list.currentRow())
    kit = self.kit
    zones = []
    if what == "zone":
        z = self._zone()
        if not z:
            show_error(self, "Zone wählen", "Wähle im Kit-Baukasten den Sound, der in den Slot soll.")
            return False
        zones = [(slot, z)]
    elif what == "kit":
        if not kit.zones:
            show_error(self, "Leeres Kit", "Füge dem Kit zuerst Sounds hinzu.")
            return False
        zones = list(enumerate(kit.zones[:F.SLOTS]))
    elif what == "restore":
        if not restore_path:
            restore_path, _ = QFileDialog.getOpenFileName(self, "Backup zurückspielen", str(self._open_backup_dir()),
                                                          "FM1-Open-Sample-Bank (*.fm1s)")
            if not restore_path:
                return False
        try:
            restore = F.read_bank(Path(restore_path).read_bytes())
        except Exception as e:  # noqa: BLE001
            show_error(self, "Datei ungültig", str(e))
            return False
    old = self.open_entries or F.empty_bank()
    if what == "restore":
        text = f"Die komplette Sample-Bank (alle 8 Slots) wird durch {Path(restore_path).name} ersetzt."
    elif what == "clear":
        text = f"Slot {slot + 1} ({old[slot].name or 'leer'}) wird geleert; die anderen 7 Slots bleiben."
    else:
        lst = ", ".join(f"Slot {i + 1} ({old[i].name or 'leer'}) ← {z.name}" for i, z in zones)
        text = f"{lst}. Die übrigen Slots bleiben, wie sie gerade gelesen wurden."
    if not confirm(self, "FM1 Open: Sample-Bank schreiben",
                   text + "\n\nDas FM-1 schreibt dafür die ganze Bank neu (Wiedergabe pausiert kurz). "
                   "Vorher sichert die App die aktuelle Bank als .fm1s-Datei.", "Schreiben", danger=True):
        return False
    folder = self._open_backup_dir()
    cached = self.open_bank

    def work(w, link):
        with F.Client(link) as c:
            info = c.probe()
            w.log.emit("Prüfe, ob die Bank auf dem FM-1 noch dem gelesenen Stand entspricht …")
            if c.unchanged(cached):
                current = cached
            else:
                if what != "restore":
                    raise F.FM1OpenError("Die Bank auf dem FM-1 hat sich seit dem Lesen geändert – bitte erst "
                                         "„Lesen & sichern“, dann noch einmal schreiben. Es wurde nichts verändert.")
                st, _ = c.sample_status()
                current = c.read_bank(lambda a, b: w.progress.emit(a, b)) if st == 2 else b""
            if current:
                bp = folder / time.strftime("FM1-Open-Samples %Y-%m-%d %H-%M-%S vor-Schreiben.fm1s")
                _write_bytes_atomic(bp, current)
                w.log.emit(f"Backup: {bp.name}")
            entries = list(restore) if what == "restore" else (F.read_bank(current) if current else F.empty_bank())
            if what == "clear":
                entries[slot] = F.SampleEntry()
            for i, z in zones:
                w.log.emit(f"Kodiere {z.name} für Slot {i + 1} (32 kHz, SEA5) …")
                pcm = F.render_zone(z)
                room = F.free_frames(entries, i, info["capacity"])
                if len(pcm) > room:
                    w.log.emit(f"  zu lang – auf {room / F.RATE:.2f} s gekürzt")
                    pcm = pcm[:room]
                if not pcm:
                    raise F.FM1OpenError("Die Sample-Bank ist voll (gemeinsam ~19,5 s) – leere zuerst andere Slots.")
                entries[i] = F.make_entry(pcm, z.name)
            bank = F.build_bank(entries, info["capacity"])
            w.log.emit(f"Schreibe {len(bank) // 1024} KiB und lese zur Kontrolle zurück …")
            c.write_bank(bank, lambda a, b: w.progress.emit(a, b))
            w.log.emit("Fertig – die Bank auf dem FM-1 stimmt Byte für Byte.")
            return entries, bank

    def done(res):
        self.open_entries, self.open_bank = res
        self._open_show(self.open_entries)
        self._open_enable()
        self.open_info.setText("Geschrieben und geprüft. Am FM-1 die SAMPLER-Spur neu anschlagen.")
    d = self.ctx.job(self, "FM1 Open: Sample-Bank schreiben", work, on_done=done,
                     done_text="Sample-Bank geschrieben und Byte für Byte geprüft. Der vorherige Stand liegt als "
                     ".fm1s-Backup im Backup-Ordner des Geräts.",
                     fail_text=lambda m: f"Nicht geschrieben: {m}")
    return d.ok


for _f in (_build_open, _open_enable, _open_show, _open_backup_dir, _open_felucca_note, open_read, open_export,
           open_upload):
    setattr(SamplesPage, _f.__name__, _f)


def _row(*ws):
    """Knopfreihe, die bei schmalem Fenster umbricht (None = Lücke, wird ignoriert)."""
    h = FlowLayout()
    for w in ws:
        if isinstance(w, QWidget):
            h.addWidget(w)
        elif w is not None:
            h.addItem(w)
    return h
