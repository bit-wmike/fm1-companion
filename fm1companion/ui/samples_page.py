# SPDX-License-Identifier: GPL-3.0-only
"""Samples: library, preview, kit builder and slots on the FM-1."""
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
from ..i18n import _, decimal, ngettext, pgettext
from .audio import OutputButton, Player, Waveform
from .common import (DeviceUndo, FlowLayout, confirm, device_tag, notify, fmt_secs, fmt_size, fs_error_text, german_buttons, heading, icon,
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
        self.slot_undo = DeviceUndo()          # (slot, old content as backup object) per device
        self._slots_busy = False
        self._search_timer = QTimer(self, singleShot=True, interval=250)
        self._search_timer.timeout.connect(self.refresh_table)

        lay = QVBoxLayout(self)
        lay.addWidget(heading(_("Samples & Kits"), _("Your samples from anywhere on the computer: audition, trim and "
                                                     "build them into kits, slice loops, record yourself, download free packs. "
                                                     "Upload: Skiff (3 slots), SLOOP (4) and FM1 Open (8 slots, together "
                                                     "~19.5 s). Melodee from 1.5 has no sample slots.")))
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        lay.addWidget(self.tabs, 1)
        split = QSplitter()
        self.tabs.addTab(split, icon("folder-sound", "audio-x-generic"), _("Library && Kits"))

        # ---------------- left: filters
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit()
        self.search.setPlaceholderText(_("Search: kick, po-33, vocal …"))
        self.search.textChanged.connect(lambda: self._search_timer.start())
        ll.addWidget(self.search)
        self.chk_fav = QCheckBox(_("favourites only"))
        self.chk_fav.toggled.connect(self.refresh_table)
        self.max_len = QDoubleSpinBox()
        self.max_len.setRange(0, 600)
        self.max_len.setSuffix(" s")
        self.max_len.setSpecialValueText(_("any length"))
        self.max_len.setToolTip(_("only sounds up to this length"))
        self.max_len.valueChanged.connect(lambda: self._search_timer.start())
        ll.addLayout(_row(self.chk_fav, self.max_len))
        tabs = QTabWidget()
        self.folders = QTreeWidget()
        self.folders.setHeaderHidden(True)
        self.folders.currentItemChanged.connect(lambda *_args: self.refresh_table())
        tabs.addTab(self.folders, _("Folders"))
        self.tags = QListWidget()
        self.tags.currentItemChanged.connect(lambda *_args: self.refresh_table())
        tabs.addTab(self.tags, _("Labels"))
        ll.addWidget(tabs, 1)
        b_addroot = QPushButton(icon("folder-new", "list-add"), _("Add folder …"))
        b_addroot.clicked.connect(self._add_root)
        b_scan = QPushButton(icon("view-refresh"), _("Rescan"))
        b_scan.clicked.connect(self.scan)
        ll.addLayout(_row(b_addroot, b_scan))
        b_dupes = QPushButton(icon("edit-copy"), _("Find duplicates …"))
        b_dupes.setToolTip(_("Look for files with the same content in the library"))
        b_dupes.clicked.connect(self.find_duplicates)
        ll.addWidget(b_dupes)
        hint = QLabel(_("Tip: just drag files or folders here."))
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

        # ---------------- middle: table + waveform
        mid = QWidget()
        ml = QVBoxLayout(mid)
        ml.setContentsMargins(0, 0, 0, 0)
        self.table = QTreeWidget()
        self.table.setHeaderLabels(["★", _("Name"), _("Length"), _("Format"), _("Labels"), _("Folder")])
        self.table.setRootIsDecorated(False)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setSortingEnabled(True)
        self.table.currentItemChanged.connect(self._sample_selected)
        self.table.itemDoubleClicked.connect(lambda *_args: self._add_to_kit())
        ml.addWidget(self.table, 3)
        self.wave = Waveform()
        ml.addWidget(self.wave, 1)
        b_play = QPushButton(icon("media-playback-start"), _("Listen"))
        b_play.clicked.connect(self._play_selected)
        b_stop = QPushButton(icon("media-playback-stop"), _("Stop"))
        b_stop.clicked.connect(self.player.stop)
        self.chk_auto = QCheckBox(_("play on selection"))
        self.chk_auto.setChecked(True)
        b_fav = QPushButton(icon("starred-symbolic", "emblem-favorite", "rating"), _("Favourite"))
        b_fav.clicked.connect(self._toggle_fav)
        b_tags = QPushButton(icon("tag", "bookmark-new"), _("Labels …"))
        b_tags.clicked.connect(self._edit_tags)
        b_show = QPushButton(icon("folder-open"), _("Show in folder"))
        b_show.clicked.connect(self._show_in_folder)
        b_slice = QPushButton(icon("edit-cut"), _("Slice …"))
        b_slice.setToolTip(_("Cut a loop into slices (transients, equal length, BPM grid) – Ctrl+L"))
        b_slice.clicked.connect(lambda: self.open_slicer())
        for seq, parent, fn in ((QKeySequence("Ctrl+L"), self, lambda: self.open_slicer()),
                                (QKeySequence(Qt.Key.Key_Space), self.table, self._play_selected)):
            sc = QShortcut(seq, parent, fn)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        b_tokit = QPushButton(icon("list-add"), _("To kit →"))
        b_tokit.clicked.connect(self._add_to_kit)
        self.out_btn = OutputButton(self)
        ml.addLayout(_row(b_play, b_stop, self.chk_auto, self.out_btn, None, b_fav, b_tags, b_show, b_slice, b_tokit))
        split.addWidget(mid)

        # ---------------- right: kit + slots
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        kg = QGroupBox(_("Kit builder"))
        kl = QVBoxLayout(kg)
        self.kit_combo = QComboBox()
        self.kit_combo.activated.connect(self._kit_chosen)
        b_kit_new = QPushButton(icon("document-new"), _("New"))
        b_kit_new.setToolTip(_("New kit"))
        b_kit_new.clicked.connect(self._kit_new)
        b_kit_save = QPushButton(icon("document-save"), _("Save"))
        b_kit_save.setToolTip(_("Save kit"))
        b_kit_save.clicked.connect(self._kit_save)
        b_kit_del = QPushButton(icon("edit-delete"), _("Delete"))
        b_kit_del.setToolTip(_("Delete kit"))
        b_kit_del.clicked.connect(self._kit_delete)
        kl.addWidget(self.kit_combo)
        kl.addLayout(_row(b_kit_new, b_kit_save, b_kit_del))
        f = QFormLayout()
        self.kit_name = QLineEdit()
        self.kit_name.setMaxLength(8)
        self.kit_name.setToolTip(_("The FM-1 shows at most 8 characters"))
        self.kit_name.textEdited.connect(self._kit_meta)
        self.kit_mode = QComboBox()
        self.kit_mode.addItem(_("Drum kit: one sound per key"), "drum")
        self.kit_mode.addItem(_("Instrument: spread over the keyboard"), "keys")
        self.kit_mode.activated.connect(self._kit_meta)
        self.kit_base = QSpinBox()
        self.kit_base.setRange(0, 112)
        self.kit_base.valueChanged.connect(self._kit_meta)
        f.addRow(_("Name:"), self.kit_name)
        f.addRow(_("Kind:"), self.kit_mode)
        f.addRow(_("First key:"), self.kit_base)
        kl.addLayout(f)
        self.zones = QTreeWidget()
        self.zones.setHeaderLabels(["#", pgettext("kit zone", "Sound"), pgettext("keyboard", "Key"), _("Length")])
        self.zones.setRootIsDecorated(False)
        self.zones.currentItemChanged.connect(self._zone_selected)
        self.zones.setMinimumHeight(170)
        kl.addWidget(self.zones, 1)
        b_up = QPushButton(icon("go-up"), "▲")
        b_up.clicked.connect(lambda: self._move(-1))
        b_down = QPushButton(icon("go-down"), "▼")
        b_down.clicked.connect(lambda: self._move(1))
        b_rm = QPushButton(icon("list-remove"), _("Remove"))
        b_rm.clicked.connect(self._remove_zone)
        b_hear = QPushButton(icon("media-playback-start"), _("Listen"))
        b_hear.setToolTip(_("as it sounds on the FM-1 (22 kHz, edited)"))
        b_hear.clicked.connect(self._hear_zone)
        kl.addLayout(_row(b_up, b_down, b_rm, None, b_hear))
        # zone editing
        zf = QFormLayout()
        self.z_root = QSpinBox()
        self.z_root.setRange(0, 127)
        self.z_root.valueChanged.connect(self._zone_edit)
        self.z_norm = QCheckBox(_("Normalize"))
        self.z_trim = QCheckBox(_("Trim silence"))
        self.z_rev = QCheckBox(_("Reverse"))
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
        zf.addRow(_("Root note:"), self.z_root)
        zf.addRow(_row(self.z_norm, self.z_trim, self.z_rev))
        zf.addRow(_("Volume:"), self.z_gain)
        zf.addRow(_("Fade out:"), self.z_fade)
        kl.addLayout(zf)
        self.zone_wave = Waveform()
        self.zone_wave.markers_changed.connect(self._markers)
        kl.addWidget(self.zone_wave)
        self.budget = QProgressBar()
        self.budget.setFormat(_("%v ms of %m ms"))
        kl.addWidget(self.budget)
        b_fit = QPushButton(icon("transform-scale", "zoom-fit-best"), _("Fit to slot"))
        b_fit.clicked.connect(self._fit)
        b_wav = QPushButton(icon("document-export"), _("Export as WAVs"))
        b_wav.clicked.connect(self._export_wavs)
        kl.addLayout(_row(b_fit, b_wav))
        rl.addWidget(kg, 3)

        # ---------------- “On the FM-1” tab: sample slots (Skiff/SLOOP) and FM1 Open bank – at the top, visible even at 720p
        self.dev_tab = QWidget()
        dl = QVBoxLayout(self.dev_tab)
        dl.setContentsMargins(0, 8, 0, 0)
        self.kit_hint = QLabel("")
        self.kit_hint.setWordWrap(True)
        self.kit_hint.setProperty("role", "muted")
        dl.addWidget(self.kit_hint)
        sg = QGroupBox(_("Sample slots on the FM-1 (Skiff, SLOOP)"))
        sl = QVBoxLayout(sg)
        self.slot_list = QListWidget()
        sl.addWidget(self.slot_list)
        self.slot_info = QLabel(_("Not read yet."))
        self.slot_info.setWordWrap(True)
        sl.addWidget(self.slot_info)
        self.b_sread = b_sread = QPushButton(icon("view-refresh"), _("Read"))
        b_sread.setToolTip(_("Read slots from the FM-1 (happens automatically when this tab is opened)"))
        b_sread.clicked.connect(self.read_slots)
        self.b_upload = QPushButton(icon("go-up", "document-send"), _("Load kit into slot"))
        self.b_upload.setProperty("variant", "primary")
        self.b_upload.setToolTip(_("Write the kit from the kit builder (tab “Library & Kits”) into the chosen slot"))
        self.b_upload.clicked.connect(self._upload)
        self.b_serase = b_serase = QPushButton(icon("edit-clear"), _("Clear slot"))
        b_serase.setToolTip(_("Clear the chosen slot on the FM-1 (its content is read first for “Undo”)"))
        b_serase.clicked.connect(self._erase_slot)
        self.b_sundo = QPushButton(icon("edit-undo"), _("Undo"))
        self.b_sundo.setToolTip(_("Restore the slot content from before the last overwrite/clear"))
        self.b_sundo.setEnabled(False)
        self.b_sundo.clicked.connect(self._slot_undo)
        sl.addLayout(_row(b_sread, self.b_upload, b_serase, self.b_sundo))
        dl.addWidget(sg, 1)
        self._build_open(dl)
        dscroll = QScrollArea()
        dscroll.setWidgetResizable(True)
        dscroll.setFrameShape(QScrollArea.Shape.NoFrame)
        dscroll.setWidget(self.dev_tab)
        self.dev_tab_index = self.tabs.addTab(dscroll, icon("audio-card", "media-flash"), _("On the FM-1"))
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
        # narrow (e.g. half a 1080p screen): filters above the list – two columns instead of three
        self._lib_split, self._lib_cols, self._lib_inner = split, (left, mid, rscroll), None
        on_narrow(split, lambda: sum(max(w.minimumWidth(), w.minimumSizeHint().width()) for w in (left, mid, rscroll))
                  + 2 * split.handleWidth() + 48, self._lib_narrow)

        # ---------------- further tabs
        from .sample_recorder import SampleRecorder
        self.recorder = SampleRecorder(ctx, self.lib, self.player)
        self.recorder.saved.connect(self._recorded)
        rsc = QScrollArea()
        rsc.setWidgetResizable(True)
        rsc.setFrameShape(QScrollArea.Shape.NoFrame)
        rsc.setWidget(self.recorder)
        self.rec_tab = rsc
        self.tabs.addTab(rsc, icon("media-record", "audio-input-microphone"), _("Record"))

        from .samplepacks_tab import PacksTab
        self.packs = PacksTab(ctx, self.lib)
        self.packs.downloaded.connect(lambda files: self._recorded(files[0]) if files else None)
        self.tabs.addTab(self.packs, icon("folder-download", "download", "folder"), _("Sample packs"))

        ctx.changed.connect(self._dev_changed)
        self.b_serase.setEnabled(False)
        self._open_felucca_note()
        self._fill_kits()
        self._show_kit()
        self.refresh_filters()
        self.refresh_table()
        QTimer.singleShot(1500, self.scan)

    # ============================================================ Library
    def _add_root(self):
        d = QFileDialog.getExistingDirectory(self, _("Add sample folder"), str(Path.home()))
        if not d:
            return
        s = self.ctx.settings
        if d not in s.sample_roots:
            s.sample_roots.append(d)
            config.save(s)
        self.scan()

    def scan(self):
        roots = self.ctx.settings.all_sample_roots()
        self.lib_info.setText(_("reading samples …"))

        def work(w):
            return self.lib.scan(roots, progress=lambda n, name: w.log.emit(_("{n} files … {name}").format(n=n, name=name)))
        run_job(self, work, on_done=self._scanned, on_log=lambda t: self.lib_info.setText(t),
                on_fail=lambda m, tb: self.lib_info.setText(_("Error while reading: {error}").format(error=m)))

    def _scanned(self, stats):
        st = self.lib.stats()
        self.lib_info.setText(_("{n} samples, {length}, {size} · new {new}, changed {changed}, removed {removed}").format(
            n=st['count'], length=fmt_secs(st['seconds']), size=fmt_size(st['bytes']),
            new=stats['neu'], changed=stats['geändert'], removed=stats['entfernt']))  # i18n: ignore (key)
        self.refresh_filters()
        self.refresh_table()
        self._ffmpeg_note()

    def refresh_filters(self):
        cur = self.folders.currentItem().data(0, Qt.ItemDataRole.UserRole) if self.folders.currentItem() else ""
        self.folders.blockSignals(True)
        self.folders.clear()
        all_it = QTreeWidgetItem([_("All samples")])
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
        it = QListWidgetItem(_("all"))
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
        """Notice when MP3/FLAC & co. can't be read without ffmpeg."""
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
            chans = {1: _("mono"), 2: _("stereo")}.get(r["channels"], _("{n} channels").format(n=r['channels']))
            fmt = (_("{khz} kHz · {bits} bit · {channels}").format(khz=decimal(r['rate'] / 1000, 1), bits=r['bits'], channels=chans)
                   if r["rate"] else r["ext"])
            dur = decimal(r['duration'], 2) + " s" if r["duration"] else "?"
            it = QTreeWidgetItem(["★" if r["favorite"] else "", r["name"], dur,
                                  fmt, r["tags"], Path(r["folder"]).name])
            if not r["duration"] and not r["rate"] and str(r["ext"]).lower().lstrip(".") in NEEDS_FFMPEG \
                    and not S.have_ffmpeg():
                it.setToolTip(2, FFMPEG_HINT)
                it.setText(3, _("{ext} – for MP3/FLAC: ffmpeg is missing").format(ext=r['ext']))
            it.setData(0, Qt.ItemDataRole.UserRole, r["path"])
            it.setData(2, Qt.ItemDataRole.UserRole, r["duration"] or 0)
            it.setToolTip(1, r["path"] + (f"\n{r['notes']}" if r["notes"] else ""))
            if r["duration"] and r["duration"] > S.SLOT_SECONDS:
                it.setToolTip(2, _("longer than a whole slot – must be shortened"))
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
                on_fail=lambda m, tb: self.wave.clear(_("not readable: {error}").format(error=m)))
        if self.chk_auto.isChecked():
            self.player.play(Path(path), status=self.lib_info.setText)

    def _play_selected(self):
        it = self.table.currentItem()
        if it:
            self.player.play(Path(it.data(0, Qt.ItemDataRole.UserRole)), status=self.lib_info.setText)

    def deactivate(self):
        """Leaving the page: stop the preview (the recording in the “Record” tab keeps running)."""
        self.player.stop()

    # ------------------------------------------------------------ Drag & drop
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and any(u.isLocalFile() for u in e.mimeData().urls()):
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.add_paths([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])

    def add_paths(self, paths: list[str]) -> int:
        """Folders become library folders, single files end up in Samples › Imported (copy).
        -> number of folders + files taken over (files are copied in the background)."""
        import shutil
        s = self.ctx.settings
        n = 0
        dest = s.samples_dir / "Importiert"  # i18n: ignore (folder name)
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
            notify(ngettext("{n} file skipped – not an audio file: {names}", "{n} files skipped – not audio files: {names}",
                            len(skipped)).format(n=len(skipped), names=", ".join(skipped[:5])), "warning")
        if not files:
            if n:
                self.scan()
                notify(ngettext("{n} folder added to the library – being read.", "{n} folders added to the library – being read.", n).format(n=n),
                       "success")
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
            text = ngettext("{n} file copied to Samples › Importiert", "{n} files copied to Samples › Importiert",
                            len(done)).format(n=len(done))
            if n:
                text = _("{copied}, {folders} added").format(
                    copied=text, folders=ngettext("{n} folder", "{n} folders", n).format(n=n))
            notify(text + ".", "success" if not errs else "warning")
            if errs:
                show_error(self, _("Not all files taken over"), "\n".join(errs))
        self.lib_info.setText(ngettext("Copying {n} file …", "Copying {n} files …", len(files)).format(n=len(files)))
        run_job(self, work, on_done=finish, on_fail=lambda m, tb: show_error(self, _("Take over files"), m),
                on_progress=lambda a, b: self.lib_info.setText(_("Copying … {a} of {b}").format(a=a, b=b)))
        return n + len(files)

    def find_duplicates(self):
        def done(groups):
            self.dupes = groups
            from PyQt6.QtWidgets import QDialog, QDialogButtonBox
            d = QDialog(self)
            d.setWindowTitle(_("Duplicate samples"))
            d.resize(720, 420)
            v = QVBoxLayout(d)
            v.addWidget(QLabel(ngettext("{n} group with the same content.", "{n} groups with the same content.", len(groups)).format(
                n=len(groups)) if groups else _("No duplicate files found.")))
            t = QTreeWidget()
            t.setHeaderLabels([_("File"), _("Folder")])
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
            v.addWidget(QLabel(_("Double-click: listen. Deleting works in the file manager (“Show in folder”).")))
            bb = german_buttons(QDialogButtonBox(QDialogButtonBox.StandardButton.Close))
            bb.rejected.connect(d.reject)
            v.addWidget(bb)
            d.exec()
        run_job(self, lambda w: self.lib.duplicates(), on_done=done)

    def _recorded(self, path: str):
        self.refresh_filters()
        self.refresh_table()
        st = self.lib.stats()
        self.lib_info.setText(_("{n} samples · new: {name}").format(n=st['count'], name=Path(path).name))

    def shutdown(self):
        self.player.stop()
        self.recorder.shutdown()

    def open_slicer(self, path: str | None = None):
        """Slicer for the selected (or given) file; optionally builds a kit directly."""
        from .slicer_dialog import SlicerDialog
        if not path:
            it = self.table.currentItem()
            if not it:
                show_error(self, _("Slice"), _("First choose a sound (e.g. a drum loop) in the list."))
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
                self, _("New kit"), _("The current kit has unsaved changes. Discard them?"), _("Discard")):
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
        txt, ok = QInputDialog.getText(self, _("Labels"), _("Separated by commas (e.g. kick, po-33, dirty):"), text=cur)
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
        self.kit_combo.addItem(_("(unsaved kit)"), "")
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
        if self.kit_dirty and not confirm(self, _("Change kit"), _("Discard unsaved changes?"), _("Discard")):
            self._fill_kits()
            return
        try:
            self.kit = S.Kit.load(Path(path))
        except OSError as e:
            show_error(self, _("Open kit"), fs_error_text(e, _("Kit not readable")))
            self._fill_kits()
            return
        except Exception:  # noqa: BLE001 – broken JSON or similar
            show_error(self, _("Kit damaged"), _("“{name}” is not a valid kit file (damaged or from "
                                                 "another app). The current kit stays open.").format(name=Path(path).stem))
            self._fill_kits()
            return
        self.kit_dirty = False
        self._show_kit()

    def _kit_new(self):
        if self.kit_dirty and not confirm(self, _("New kit"), _("Discard unsaved changes?"), _("Discard")):
            return
        self.kit = S.Kit(name="KIT 1")
        self.kit_dirty = False
        self._fill_kits()
        self._show_kit()

    def _kit_save(self):
        new_path = None
        if not self.kit.path:
            name, ok = QInputDialog.getText(self, _("Save kit"), _("Name of the kit file:"), text=self.kit.name.title())
            if not ok or not name.strip():
                return
            stem = S.safe_kit_name(name)
            if not stem:
                show_error(self, _("Save kit"), _("This name does not work as a file name – please use letters or digits. "
                                                  "Nothing saved."))
                return
            folder, err = safe_folder("Kits")
            if err:
                show_error(self, _("Save kit"), err)
                return
            new_path = folder / f"{stem}{S.KIT_SUFFIX}"
            if new_path.exists() and not confirm(
                    self, _("Overwrite kit"), _("There is already a kit “{name}”. Replace it?\n\nThe old kit file will be "
                                                "overwritten (the samples in it stay on the disk).").format(name=stem),
                    _("Overwrite"), danger=True):
                return
            self.kit.path = str(new_path)
        try:
            self.kit.save()
        except OSError as e:
            if new_path is not None:
                self.kit.path = ""
            show_error(self, _("Save kit"), fs_error_text(e, _("Kit not saved")))
            return
        self.kit_dirty = False
        self._fill_kits()
        notify(_("Kit “{name}” saved ({n} sounds) – only on the computer.").format(name=Path(self.kit.path).stem, n=len(self.kit.zones)),
               "success")

    def _kit_delete(self):
        if not self.kit.path:
            show_error(self, _("Delete kit"), _("This kit is not saved yet – there is no file to delete. "
                                                "“New” discards it."))
            return
        if confirm(self, _("Delete kit"), _("Delete kit “{name}” ({n} sounds)?\n\n"
                                            "Only the kit file is deleted – the samples stay, and nothing changes on the FM-1.").format(
                       name=Path(self.kit.path).stem, n=len(self.kit.zones)),
                   _("Delete"), danger=True):
            try:
                Path(self.kit.path).unlink(missing_ok=True)
            except OSError as e:
                show_error(self, _("Delete kit"), fs_error_text(e, _("Not deleted")))
                return
            notify(_("Kit “{name}” deleted (only the file; samples and FM-1 unchanged).").format(name=Path(self.kit.path).stem), "success")
            self.kit = S.Kit(name="KIT 1")
            self.kit_dirty = False
            self._fill_kits()
            self._show_kit()

    def _kit_meta(self, *_args):
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
            key = S.note_name(z.root) if self.kit.mode == "drum" else _("Root {note}").format(note=S.note_name(z.root))
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
            self.budget.setFormat(_("0 s of {cap} s – one slot holds all sounds together").format(cap=decimal(S.SLOT_SECONDS, 1)))
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
            used, cap = decimal(ms / 1000, 2), decimal(S.SLOT_SECONDS, 1)
            self.budget.setFormat(_("TOO LONG: {used} s of {cap} s").format(used=used, cap=cap) if over
                                  else _("{used} s of {cap} s").format(used=used, cap=cap))
            self.budget.setStyleSheet("QProgressBar::chunk{background:#da4453}" if over else "")
            for i, b in enumerate(per):
                it = self.zones.topLevelItem(i)
                if it:
                    it.setText(3, decimal(b * 2 / S.SLOT_RATE, 2) + " s")
        run_job(self, work, on_done=done, on_fail=lambda m, tb: self.budget.setFormat(_("Error: {error}").format(error=m)))

    def _add_to_kit(self):
        paths = self._selected_paths()
        if not paths:
            return
        room = 16 - len(self.kit.zones)
        if room <= 0:
            show_error(self, _("Kit full"), _("A slot holds at most 16 sounds."))
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
        if self.kit.mode == "keys":            # detect the root note – reading audio doesn't belong in the GUI thread
            def work(w):
                out = {}
                for z in new_zones:
                    try:
                        sr, x = S.read_mono(Path(z.path))
                        out[z.path] = S.detect_root(sr, x, Path(z.path).stem)
                    except Exception:  # noqa: BLE001 – then it stays C4
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
            show_error(self, _("Kit full"), _("Only {room} of {n} sounds still fitted in – a slot has at most "
                                              "16 sounds. Create a second kit for the rest.").format(room=room, n=len(paths)))

    def _zone(self) -> S.Zone | None:
        it = self.zones.currentItem()
        i = self.zones.indexOfTopLevelItem(it) if it else -1
        return self.kit.zones[i] if 0 <= i < len(self.kit.zones) else None

    def _zone_selected(self, *_args):
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
            self.zone_wave.clear(_("no zone chosen"))
        for wdg in widgets:
            wdg.blockSignals(False)

    def _zone_edit(self, *_args):
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
                on_fail=lambda m, tb: show_error(self, _("Listen"), m))

    def _fit(self):
        if not self.kit.zones:
            return
        run_job(self, lambda w: S.fit_to_slot(self.kit), on_done=lambda _res: (self._fill_zones(keep=True), self._zone_selected()),
                done_text=_("Kit shortened to slot length (only in the kit builder, files unchanged)."))
        self.kit_dirty = True

    def _export_wavs(self):
        if not self.kit.zones:
            return
        d = QFileDialog.getExistingDirectory(self, _("Export to where?"), str(safe_folder("Kits")[0]))
        if d:
            run_job(self, lambda w: S.export_wavs(self.kit, Path(d) / self.kit.name),
                    done_text=lambda files: ngettext("{n} WAV file exported to {folder}", "{n} WAV files exported to {folder}",
                                                     len(files)).format(n=len(files), folder=Path(d) / self.kit.name),
                    fail_text=lambda m: _("WAV export failed: {error}").format(error=m))

    # ================================================================ Slots
    def _dev_changed(self):
        if hasattr(self, "open_list"):
            self.open_entries, self.open_bank = None, b""
            self.open_list.clear()
            self.open_budget.setFormat(_("not read yet"))
            self.open_budget.setValue(0)
            self._open_enable()
        self.slot_list.clear()
        self.slot_list.setVisible(True)
        self.slots = []
        self._slots_busy = False
        self._slots_reading = None
        self._slots_gen = getattr(self, "_slots_gen", 0) + 1   # discard results of older reads
        self.slot_info.setText(self._no_device_text())
        self.b_upload.setEnabled(self.ctx.connected)
        self.b_upload.setToolTip(self._upload_tip())
        self.b_sread.setEnabled(self.ctx.link is not None)
        self.b_serase.setEnabled(False)                # only after reading, with the selected slot
        self._open_felucca_note()
        self._slot_undo_enable()
        if self.ctx.connected and self.tabs.currentIndex() == self.dev_tab_index:
            self._tab_changed(self.dev_tab_index)

    def _no_device_text(self) -> str:
        if self.ctx.connected:
            return _("Not read yet.")
        if getattr(self.ctx, "midi_connected", False) or self.ctx.link is not None:
            return _("This FM-1 does not speak the Felucca protocol – the app cannot read Skiff/SLOOP sample slots "
                     "here. If FM1 Open runs on it, use the box below (“Read & back up”).")
        return _("No FM-1 connected – here's how: plug in the FM-1 via USB; with Skiff or SLOOP the slots appear here.")

    def _upload_tip(self) -> str:
        if not self.ctx.connected:
            return _("First connect an FM-1 with Skiff or SLOOP – that firmware has the sample slots")
        if not self.slots:
            return _("First “Read” – then choose a slot")
        return _("Write the kit from the kit builder (tab “Library & Kits”) into the chosen slot")

    def _slot_undo_enable(self):
        self.b_sundo.setEnabled(bool(self.slot_undo))
        self.b_sundo.setToolTip(_("Restore the slot content from before the last overwrite/clear")
                                if not self.slot_undo or self.slot_undo.matches(self.ctx) else self.slot_undo.refusal())

    def read_slots(self):
        if not self.ctx.connected and getattr(self.ctx, "midi_connected", self.ctx.link is not None) \
                and self.ctx.link is not None:
            self.slot_info.setText(_("No Felucca device – see FM1 Open below."))
            self.open_read()
            return
        if not self.ctx.require(self):
            self._slots_busy = False
            return
        # device + connection generation: even reconnecting the same FM-1 invalidates old results
        key = (self.ctx.port.key if self.ctx.port else "", getattr(self, "_slots_gen", 0))
        if getattr(self, "_slots_reading", None) == key:
            return                                     # already running for this device (e.g. tab change + button)
        self._slots_reading = key
        self.slot_info.setText(_("Reading the sample slots …"))

        def fail(msg, tb):
            if self._slots_reading == key:
                self._slots_reading = None
            self._slots_busy = False
            if self._current_key() == key:
                self.slot_info.setText(_("Reading slots failed: {error}").format(error=msg))
        self.ctx.job(self, _("Read sample slots"), lambda w, link: S.slot_info(link), dialog=False,
                     on_done=lambda res: self._got_slots(res, key), on_fail=fail)

    def _current_key(self) -> tuple:
        return (self.ctx.port.key if self.ctx.port else "", getattr(self, "_slots_gen", 0))

    def _lib_narrow(self, narrow: bool):
        """Library: three columns (filters | list | kit) or two (filters above list | kit)."""
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
        """“On the FM-1” tab opened: read slots automatically (once per device), update the kit hint."""
        if i != self.dev_tab_index:
            return
        self._kit_hint()
        if self.ctx.connected and not self.slots and not getattr(self, "_slots_busy", False):
            self._slots_busy = True
            self.read_slots()

    def _kit_hint(self):
        k = getattr(self, "kit", None)
        name = (k.name if k is not None and getattr(k, "name", "") else "") or _("(unsaved kit)")
        n = len(k.zones) if k is not None else 0
        self.kit_hint.setText(_("Ready for upload: <b>{name}</b> with {n} sounds from the kit builder "
                                "(tab “Library & Kits”).").format(name=name, n=n) if n else
                              _("No kit built yet – in the tab “Library & Kits” mark sounds and “To kit”."))

    def _got_slots(self, res, key=None):
        if key is not None:
            if self._slots_reading == key:
                self._slots_reading = None
            if key != self._current_key():
                return                                 # result of a device that has been disconnected/changed in the meantime
        self._slots_busy = False
        kib, slots = res
        keep = self.slot_list.currentRow()
        self.slots = slots
        self.slot_list.clear()
        if not slots:
            fw = self.ctx.title() if hasattr(self.ctx, "title") else _("This firmware")
            self.slot_info.setText(_("<b>{firmware} has no sample slots</b> (Melodee from 1.5 plays only synth and "
                                     "drum engines). You can still build kits, save them and export them as WAVs – "
                                     "uploading works on an FM-1 with Skiff or SLOOP.").format(firmware=fw))
            self.slot_list.setVisible(False)          # no empty list that looks like “something is missing here”
            self.b_upload.setEnabled(False)
            self.b_upload.setToolTip(_("This firmware has no sample slots (Melodee from 1.5) – uploading is not possible here"))
            self.b_serase.setEnabled(False)
            self.b_serase.setToolTip(_("This firmware has no sample slots"))
            self.b_sread.setEnabled(False)
            self.b_sread.setToolTip(_("This firmware has no sample slots – after a device change the app reads again"))
            return
        self.b_sread.setEnabled(True)
        self.b_sread.setToolTip(_("Read slots from the FM-1 (happens automatically when this tab is opened)"))
        self.slot_list.setVisible(True)
        self.b_serase.setEnabled(True)
        self.b_upload.setEnabled(True)
        self.b_upload.setToolTip(self._upload_tip())
        for s in slots:
            it = QListWidgetItem(_("USR{n}: empty").format(n=s.index + 1) if s.empty else
                                 _("USR{n}: {name} – {zones} sounds, {kib} KiB").format(n=s.index + 1, name=s.name, zones=s.zones, kib=s.kib))
            it.setData(Qt.ItemDataRole.UserRole, s.index)
            self.slot_list.addItem(it)
        self.slot_info.setText(_("{n} slots of {kib} KiB each.").format(n=len(slots), kib=kib))
        self.slot_list.setCurrentRow(keep if 0 <= keep < len(slots) else 0)    # selection stays when re-reading

    def _upload(self):
        it = self.slot_list.currentItem()
        if not it or not self.ctx.require(self):
            show_error(self, _("Choose slot"), _("First “Read”, then choose a slot."))
            return
        if not self.kit.zones:
            show_error(self, _("Empty kit"), _("Add sounds to the kit first."))
            return
        if getattr(self, "last_budget", None) is not None and self.last_budget > S.SLOT_BYTES:
            show_error(self, _("Kit too long"), _("{reason}\n\nNothing was changed on the FM-1.").format(reason=S.budget_text(self.last_budget)))
            return
        slot = it.data(Qt.ItemDataRole.UserRole)
        cur = self.slots[slot]
        if not confirm(self, _("Load kit into slot"),
                       (_("USR{n} contains “{name}” ({zones} sounds) and will be replaced by the kit "
                          "“{kit}”.").format(n=slot + 1, name=cur.name, zones=cur.zones, kit=self.kit.name) if not cur.empty else
                        _("The kit “{kit}” goes into the empty slot USR{n}.").format(kit=self.kit.name, n=slot + 1))
                       + _("<br><br>First the app reads the old slot content so you can bring it back with “Undo” "
                           "(as long as the app is running)."), _("Overwrite") if not cur.empty else _("Load"),
                       danger=not cur.empty):
            return
        kit = self.kit
        fw = self.ctx.firmware_key
        kept: list = []
        tag = device_tag(self.ctx)

        def work(w, link):
            w.log.emit(_("Saving USR{n} for “Undo” …").format(n=slot + 1))
            kept.append(S.read_slot(link, slot, fw))
            w.log.emit(_("Building the slot (22 050 Hz, IMA-ADPCM) …"))
            hdr, data = S.build_slot(kit)
            w.log.emit(_("{bytes} bytes, {n} sounds – transferring …").format(bytes=len(data), n=hdr[6]))
            S.upload(link, slot, hdr, data, progress=lambda a, b: w.progress.emit(a, b))
            w.log.emit(f"USR{slot + 1} = {kit.name}")
            return S.slot_info(link)
        d = self.ctx.job(self, _("Load kit into USR{n}").format(n=slot + 1), work,
                         done_text=_("Kit “{kit}” ({zones} sounds) is now in USR{n}. The old "
                                     "content is saved – “Undo” brings it back.").format(kit=kit.name, zones=len(kit.zones), n=slot + 1),
                         fail_text=lambda m: (_("Not uploaded: {error} – the old slot content is saved (“Undo”).") if kept else
                                              _("Not uploaded: {error} – nothing was changed on the FM-1.")).format(error=m))
        if kept:                               # also after a cancel midway
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
        what = (_("“{name}” ({zones} sounds)").format(name=cur.name, zones=cur.zones) if cur and not cur.empty
                else _("the (already empty) slot"))
        if not confirm(self, _("Clear slot"), _("Clear USR{n} – this deletes {what} on the FM-1.<br><br>"
                                              "First the app reads the content so you can bring it back with “Undo” "
                                              "(as long as the app is running).").format(n=slot + 1, what=what), _("Clear"), danger=True):
            return
        fw = self.ctx.firmware_key
        kept: list = []
        tag = device_tag(self.ctx)

        def work(w, link):
            w.log.emit(_("Saving USR{n} for “Undo” …").format(n=slot + 1))
            kept.append(S.read_slot(link, slot, fw))
            S.erase(link, slot)
            return S.slot_info(link)
        d = self.ctx.job(self, _("Clear slot"), work,
                         done_text=_("USR{n} is empty. The old content is saved – “Undo” brings it back.").format(n=slot + 1))
        if kept:
            self.slot_undo.push(self.ctx, (slot, kept[0]), f"USR{slot + 1}", tag)
        self._slot_undo_enable()
        if d.ok:
            self._got_slots(d.result_value)

    def _slot_undo(self):
        if not self.slot_undo or not self.ctx.require(self) or not self.slot_undo.check(self.ctx, self):
            return
        slot, data = self.slot_undo.peek()
        if not confirm(self, _("Undo"), (_("Set USR{n} back to the state before the last change (empty)?") if not data
                                        else _("Set USR{n} back to the state before the last change?")).format(n=slot + 1),
                       pgettext("undo", "Restore")):
            return

        def work(w, link):
            S.restore_slot(link, slot, data, progress=lambda a, b: w.progress.emit(a, b))
            return S.slot_info(link)
        d = self.ctx.job(self, _("Restore USR{n}").format(n=slot + 1), work,
                         done_text=(_("USR{n} has the old content again (empty).") if not data
                                    else _("USR{n} has the old content again.")).format(n=slot + 1))
        if d.ok:
            self.slot_undo.pop()
            self._got_slots(d.result_value)
        self._slot_undo_enable()


# ================================================================ FM1 Open
NEEDS_FFMPEG = {"mp3", "flac", "ogg", "opus", "m4a"}
FFMPEG_HINT = _("Length “?”: for MP3/FLAC: sudo apt install ffmpeg")
OPEN_TITLE = _("FM1 Open: 8 slots, together ~19.5 s")


def _write_bytes_atomic(path: Path, data: bytes):
    """Write the backup file all or nothing (hidden .tmp file, then rename)."""
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
    """FM1 Open is a separate firmware: if the FM-1 speaks Felucca (Melodee/SLOOP/Skiff), it can't be FM1 Open –
    then the box is locked and says so, instead of reporting “no response” after seconds when reading."""
    felucca = bool(self.ctx.connected)
    self.b_open_read.setEnabled(self.ctx.link is not None and not felucca)
    self.b_open_restore.setEnabled(not felucca)
    self.open_list.setVisible(not felucca)          # no empty list for a device that isn't FM1 Open at all
    self.open_budget.setVisible(not felucca)
    if felucca:
        self.open_info.setText(_("Only for an FM-1 with the M-VAVE firmware FM1 Open – this one runs {firmware}.").format(firmware=self.ctx.title()))
    elif self.open_entries is None:
        self.open_info.setText(OPEN_HINT)


OPEN_HINT = _("For an FM-1 with FM1 Open: first “Read” – the app backs up the whole bank before it writes anything "
              "(the device always rewrites all 8 slots).")


def _build_open(self, rl):
    from .. import fm1open as F
    self.F = F
    self.open_entries = None
    self.open_bank = b""
    self.open_cap = F.CAPACITY
    g = QGroupBox(OPEN_TITLE)
    g.setToolTip(_("Official M-VAVE firmware FM1 Open (V14): 8 sample pads share one bank"))
    lay = QVBoxLayout(g)
    self.open_list = QListWidget()
    self.open_list.setMinimumHeight(150)
    lay.addWidget(self.open_list)
    self.open_budget = QProgressBar()
    self.open_budget.setRange(0, int(F.capacity_seconds() * 1000))
    self.open_budget.setFormat(_("not read yet"))
    self.open_budget.setValue(0)
    lay.addWidget(self.open_budget)
    self.open_info = QLabel(OPEN_HINT)
    self.open_info.setWordWrap(True)
    self.open_info.setProperty("role", "muted")
    lay.addWidget(self.open_info)
    self.b_open_read = b_read = QPushButton(icon("view-refresh"), _("Read && back up"))
    b_read.setToolTip(_("Read the bank from the FM-1 and save it as .fm1s backup (Backups › Device)"))
    b_read.clicked.connect(self.open_read)
    self.b_open_zone = QPushButton(icon("go-up"), _("Zone → slot"))
    self.b_open_zone.setToolTip(_("Chosen kit zone into the chosen slot (32 kHz, SEA5)"))
    self.b_open_zone.clicked.connect(lambda: self.open_upload("zone"))
    self.b_open_kit = QPushButton(icon("go-up"), _("Kit → slots 1–8"))
    self.b_open_kit.setToolTip(_("The first 8 sounds of the kit into slot 1–8; other slots stay"))
    self.b_open_kit.clicked.connect(lambda: self.open_upload("kit"))
    self.b_open_clear = QPushButton(icon("edit-clear"), _("Clear slot"))
    self.b_open_clear.clicked.connect(lambda: self.open_upload("clear"))
    self.b_open_export = QPushButton(icon("document-export"), _("Bank file …"))
    self.b_open_export.setToolTip(_("Save the bank as .fm1s – can also be loaded in the M-VAVE Workbench"))
    self.b_open_export.clicked.connect(self.open_export)
    self.b_open_restore = QPushButton(icon("document-revert", "document-open"), _("Restore backup …"))
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
        it = QListWidgetItem(_("{pad}: empty").format(pad=head) if e.empty else
                             _("{pad}: {name} – {secs} s").format(pad=head, name=e.name or _("(unnamed)"), secs=decimal(e.seconds, 2)))
        it.setData(Qt.ItemDataRole.UserRole, i)
        self.open_list.addItem(it)
    used = F.used_seconds(entries)
    cap = F.capacity_seconds(self.open_cap)
    self.open_budget.setRange(0, int(cap * 1000))
    self.open_budget.setValue(min(int(used * 1000), self.open_budget.maximum()))
    self.open_budget.setFormat(_("{used} s of {cap} s used").format(used=decimal(used, 2), cap=decimal(cap, 2)))
    if self.open_list.currentRow() < 0:
        self.open_list.setCurrentRow(0)


def _open_backup_dir(self):
    d = self.ctx.device_folder(self.ctx.settings.backups_dir) if hasattr(self.ctx, "device_folder") else \
        self.ctx.settings.backups_dir
    return Path(d)


def open_read(self):
    if self.ctx.link is None:
        show_error(self, _("No device"), _("Please connect an FM-1 with FM1 Open via USB."))
        return
    F = self.F
    folder = self._open_backup_dir()

    def work(w, link):
        with F.Client(link) as c:
            w.log.emit(_("Asking the FM-1 for FM1 Open …"))
            info = c.probe()
            st, size = c.sample_status()
            if st == 0:
                return info, None, None
            w.log.emit(_("Reading the sample bank ({kib} KiB) …").format(kib=size // 1024))
            bank = c.read_bank(lambda a, b: w.progress.emit(a, b))
        F.read_bank(bank)                 # checks the bank before it is backed up
        p = folder / time.strftime("FM1-Open-Samples %Y-%m-%d %H-%M-%S.fm1s")
        _write_bytes_atomic(p, bank)
        w.log.emit(_("Backed up: {path}").format(path=p))
        return info, bank, p

    def done(res):
        info, bank, p = res
        self.open_cap = info["capacity"]
        if bank is None:
            self.open_entries, self.open_bank = F.empty_bank(), b""
            self.open_info.setText(_("FM1 Open detected, but without a valid sample bank. You can write a new bank."))
        else:
            self.open_entries, self.open_bank = F.read_bank(bank), bank
            self.open_info.setText(_("FM1 Open detected. Backup: {name}").format(name=p.name))
        self._open_show(self.open_entries)
        self._open_enable()
    d = self.ctx.job(self, _("FM1 Open: read sample bank"), work, on_done=done,
                     done_text=lambda r: (_("Sample bank read and saved as backup: {name}. Nothing was changed on the FM-1.").format(
                                              name=r[2].name) if r[1] is not None
                                          else _("FM1 Open detected – no sample bank on the device yet. Nothing was changed on the FM-1.")))
    if not d.ok:
        self.open_info.setText(_("No FM1 Open found (or no answer). Is FM1 Open V13/V14 running on the FM-1?"))


def open_export(self):
    if self.open_entries is None:
        return
    p, _filter = QFileDialog.getSaveFileName(self, _("Save sample bank"), str(self._open_backup_dir() / "FM1-samples.fm1s"),
                                             _("FM1 Open sample bank (*.fm1s)"))
    if p:
        try:
            _write_bytes_atomic(Path(p), self.open_bank or self.F.build_bank(self.open_entries))
        except (OSError, self.F.FM1OpenError) as e:
            show_error(self, _("Save bank file"), fs_error_text(e, _("Not saved")) if isinstance(e, OSError) else str(e))
            return
        self.open_info.setText(_("Bank file saved: {path} – nothing was changed on the FM-1.").format(path=p))


def open_upload(self, what: str, restore_path: str | None = None):
    """what: "zone" | "kit" | "clear" | "restore". Writes the whole bank – with confirmation and backup."""
    F = self.F
    if self.ctx.link is None:
        show_error(self, _("No device"), _("Please connect an FM-1 with FM1 Open via USB."))
        return False
    if what != "restore" and self.open_entries is None:
        show_error(self, _("Read first"), _("Read the sample bank first (“Read & back up”) – that way a backup stays."))
        return False
    slot = max(0, self.open_list.currentRow())
    kit = self.kit
    zones = []
    if what == "zone":
        z = self._zone()
        if not z:
            show_error(self, _("Choose zone"), _("Choose the sound in the kit builder that should go into the slot."))
            return False
        zones = [(slot, z)]
    elif what == "kit":
        if not kit.zones:
            show_error(self, _("Empty kit"), _("Add sounds to the kit first."))
            return False
        zones = list(enumerate(kit.zones[:F.SLOTS]))
    elif what == "restore":
        if not restore_path:
            restore_path, _filter = QFileDialog.getOpenFileName(self, _("Restore backup"), str(self._open_backup_dir()),
                                                                _("FM1 Open sample bank (*.fm1s)"))
            if not restore_path:
                return False
        try:
            restore = F.read_bank(Path(restore_path).read_bytes())
        except Exception as e:  # noqa: BLE001
            show_error(self, _("File invalid"), str(e))
            return False
    old = self.open_entries or F.empty_bank()
    if what == "restore":
        text = _("The complete sample bank (all 8 slots) will be replaced by {name}.").format(name=Path(restore_path).name)
    elif what == "clear":
        text = _("Slot {n} ({name}) will be cleared; the other 7 slots stay.").format(n=slot + 1, name=old[slot].name or _("empty"))
    else:
        lst = ", ".join(_("Slot {n} ({old}) ← {new}").format(n=i + 1, old=old[i].name or _("empty"), new=z.name) for i, z in zones)
        text = _("{list}. The other slots stay as they were just read.").format(list=lst)
    if not confirm(self, _("FM1 Open: write sample bank"),
                   text + _("\n\nThe FM-1 rewrites the whole bank for this (playback pauses briefly). "
                            "First the app backs up the current bank as a .fm1s file."), _("Write"), danger=True):
        return False
    folder = self._open_backup_dir()
    cached = self.open_bank

    def work(w, link):
        with F.Client(link) as c:
            info = c.probe()
            w.log.emit(_("Checking whether the bank on the FM-1 still matches what was read …"))
            if c.unchanged(cached):
                current = cached
            else:
                if what != "restore":
                    raise F.FM1OpenError(_("The bank on the FM-1 has changed since reading – please first "
                                           "“Read & back up”, then write again. Nothing was changed."))
                st, _size = c.sample_status()
                current = c.read_bank(lambda a, b: w.progress.emit(a, b)) if st == 2 else b""
            if current:
                bp = folder / time.strftime("FM1-Open-Samples %Y-%m-%d %H-%M-%S vor-Schreiben.fm1s")
                _write_bytes_atomic(bp, current)
                w.log.emit(_("Backup: {name}").format(name=bp.name))
            entries = list(restore) if what == "restore" else (F.read_bank(current) if current else F.empty_bank())
            if what == "clear":
                entries[slot] = F.SampleEntry()
            for i, z in zones:
                w.log.emit(_("Encoding {name} for slot {n} (32 kHz, SEA5) …").format(name=z.name, n=i + 1))
                pcm = F.render_zone(z)
                room = F.free_frames(entries, i, info["capacity"])
                if len(pcm) > room:
                    w.log.emit(_("  too long – shortened to {secs} s").format(secs=f"{room / F.RATE:.2f}"))
                    pcm = pcm[:room]
                if not pcm:
                    raise F.FM1OpenError(_("The sample bank is full (together ~19.5 s) – clear other slots first."))
                entries[i] = F.make_entry(pcm, z.name)
            bank = F.build_bank(entries, info["capacity"])
            w.log.emit(_("Writing {kib} KiB and reading back to check …").format(kib=len(bank) // 1024))
            c.write_bank(bank, lambda a, b: w.progress.emit(a, b))
            w.log.emit(_("Done – the bank on the FM-1 matches byte for byte."))
            return entries, bank

    def done(res):
        self.open_entries, self.open_bank = res
        self._open_show(self.open_entries)
        self._open_enable()
        self.open_info.setText(_("Written and checked. Play the SAMPLER track again on the FM-1."))
    d = self.ctx.job(self, _("FM1 Open: write sample bank"), work, on_done=done,
                     done_text=_("Sample bank written and checked byte for byte. The previous state is a "
                                 ".fm1s backup in the backup folder of the device."),
                     fail_text=lambda m: _("Not written: {error}").format(error=m))
    return d.ok


for _f in (_build_open, _open_enable, _open_show, _open_backup_dir, _open_felucca_note, open_read, open_export,
           open_upload):
    setattr(SamplesPage, _f.__name__, _f)


def _row(*ws):
    """Button row that wraps when the window is narrow (None = gap, is ignored)."""
    h = FlowLayout()
    for w in ws:
        if isinstance(w, QWidget):
            h.addWidget(w)
        elif w is not None:
            h.addItem(w)
    return h
