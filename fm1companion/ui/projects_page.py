# SPDX-License-Identifier: GPL-3.0-only
"""Projects: the 32 slots on the device and the project library on the computer; for SLOOP 3 its one project
(read only: fetch it, view it) and .s3p files in the library, the web editor's own format."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QUrl, Qt
from PyQt6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PyQt6.QtWidgets import (QFileDialog, QGroupBox, QInputDialog, QLabel, QLineEdit, QProgressBar,
                             QPushButton, QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import backup, projects
from .common import (FlowLayout, confirm, device_tag, flow_row, fmt_date, fmt_size, fs_error_text, heading, icon,
                     same_device, show_error)
from .playback_bar import DeviceBadge, device_name
from .responsive import adaptive_splitter, wrap_height
from ..i18n import _, ngettext, pgettext
import time


class ProjectsPage(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.slots: list[projects.SlotInfo] = []
        lay = QVBoxLayout(self)
        lay.addWidget(heading(_("Projects"), _("Fetch projects from the active FM-1 to the computer, view and listen to "
                                               "them, name them, collect them and play them back into any slot – also "
                                               "onto another FM-1 with the same firmware.")))
        self.badge = DeviceBadge(ctx, _("On the left you see the slots of"))
        lay.addWidget(self.badge)
        split = QSplitter()
        lay.addWidget(split, 1)

        # Device
        g = QGroupBox(_("On the FM-1"))
        self.dev_box = g
        gl = QVBoxLayout(g)
        self.dev_tree = QTreeWidget()
        self.dev_tree.setHeaderLabels([pgettext("project slot", "Slot"), _("Used"), _("Size")])
        self.dev_tree.setRootIsDecorated(False)
        self.dev_tree.itemDoubleClicked.connect(lambda *_: self._view_slot())
        self.dev_tree.setToolTip(_("Double-click: view and listen"))
        gl.addWidget(self.dev_tree, 1)
        self.dev_info = QLabel(_("Not read yet."))
        self.dev_info.setWordWrap(True)
        gl.addWidget(self.dev_info)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setMaximumHeight(6)
        self.progress.hide()
        gl.addWidget(self.progress)
        b_read = QPushButton(icon("view-refresh"), _("Re-read"))
        b_read.setToolTip(_("The slots are read automatically when the page opens – here again when needed (F5)"))
        b_read.clicked.connect(self.read_slots)
        QShortcut(QKeySequence("F5"), self, activated=self.read_slots)
        self.b_view_slot = QPushButton(icon("media-playback-start", "document-preview"), _("View && listen"))
        self.b_view_slot.setToolTip(_("Read the chosen slot from the FM-1 (read only) and open it in “View & Export” – "
                                      "play it there on the FM-1 with its sounds or export it as MIDI"))
        self.b_view_slot.clicked.connect(self._view_slot)
        b_exp = QPushButton(icon("go-next", "document-export"), _("Fetch to the computer"))
        b_exp.setToolTip(_("Copy the chosen slot as a project file into the library (nothing changes on the FM-1)"))
        b_exp.clicked.connect(self._export)
        b_all = QPushButton(icon("document-save-all"), _("Fetch all"))
        b_all.setToolTip(_("Copy all used slots into the library"))
        b_all.clicked.connect(self._export_all)
        b_erase = QPushButton(icon("edit-clear", "edit-delete"), _("Clear slot …"))
        b_erase.setToolTip(_("Deletes the slot on the FM-1 – the app puts a copy into the library first"))
        b_erase.clicked.connect(self._erase)
        # grouped: view | back up (read only) | changes the FM-1 – short title at the front of the row (no extra rows)
        gl.addWidget(flow_row(_section(_("View:")), self.b_view_slot, b_read))
        gl.addWidget(flow_row(_section(_("Back up:")), b_exp, b_all))
        gl.addWidget(flow_row(_section(_("Changes the FM-1:")), b_erase))
        split.addWidget(g)

        # Library
        lg = QGroupBox(_("Library on the computer"))
        ll = QVBoxLayout(lg)
        self.search = QLineEdit()
        self.search.setPlaceholderText(_("Search (name, note, firmware) …"))
        self.search.textChanged.connect(self.refresh_library)
        ll.addWidget(self.search)
        self.lib = QTreeWidget()
        self.lib.setHeaderLabels([_("Name"), "Firmware", _("Device"), _("Date"), _("Size"), pgettext("library", "Note"),
                                  _("Tempo"), _("Length")])
        self.lib.setRootIsDecorated(False)
        self.lib.setSortingEnabled(True)
        ll.addWidget(self.lib, 1)
        self.lib_empty = QLabel(_("No projects in the library yet – how it works: choose a used slot on the left and "
                                  "“Fetch to the computer”, or “Fetch all”. Project files (.fm1proj, SLOOP 3: .s3p) "
                                  "can also be added with “Add file”."))
        self.lib_empty.setWordWrap(True)
        self.lib_empty.setProperty("role", "muted")
        wrap_height(self.lib_empty)
        ll.addWidget(self.lib_empty)
        self.lib.itemDoubleClicked.connect(lambda *_: self._view_lib())
        self.lib.setToolTip(_("Double-click: view and listen"))
        b_view = QPushButton(icon("media-playback-start", "document-preview"), _("View && listen"))
        b_view.setToolTip(_("Open the chosen project in “View & Export” – tracks, patterns, song; play on the FM-1 or "
                            "on the computer; MIDI export"))
        b_view.clicked.connect(self._view_lib)
        self.b_imp = QPushButton(icon("go-previous", "document-import"), _("To the FM-1 into the chosen slot …"))
        self.b_imp.setToolTip(_("Write the chosen project into the slot chosen on the left on the active FM-1 (with "
                                "confirmation; the device checks it first)"))
        self.b_imp.clicked.connect(self._import)
        b_ren = QPushButton(icon("edit-rename"), _("Rename"))
        b_ren.clicked.connect(self._rename)
        b_note = QPushButton(icon("document-edit"), pgettext("library", "Note"))
        b_note.clicked.connect(self._note)
        b_del = QPushButton(icon("edit-delete"), _("Delete"))
        b_del.clicked.connect(self._delete)
        b_add = QPushButton(icon("document-open"), _("Add file …"))
        b_add.setToolTip(_("Add project files (.fm1proj; SLOOP 3: .s3p from its web editor, or the project in an "
                           ".s3b or SLOOP 3 backup) to the library"))
        b_add.clicked.connect(self._add_file)
        b_save = QPushButton(icon("document-save-as", "document-save"), _("Save as file …"))
        b_save.setToolTip(_("Save a copy of the chosen project somewhere else – a SLOOP 3 project as the .s3p file "
                            "its web editor opens"))
        b_save.clicked.connect(self._save_copy)
        b_bk = QPushButton(icon("document-preview", "backup"), _("From backups …"))
        b_bk.setToolTip(_("View projects in older backups and fetch them one by one (Backups › Browse & Compare)"))
        b_bk.clicked.connect(self._from_backups)
        b_open = QPushButton(icon("folder-open"), _("Folder"))
        b_open.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(ctx.settings.projects_dir))))
        ll.addWidget(flow_row(_section(_("View:")), b_view, self.b_imp))
        ll.addWidget(flow_row(_section(_("Organise:")), b_ren, b_note, b_del))
        ll.addWidget(flow_row(_section(_("Add:")), b_add, b_bk, b_save, b_open))
        split.addWidget(lg)
        split.setChildrenCollapsible(False)
        g.setMinimumWidth(280)
        lg.setMinimumWidth(320)
        split.setStretchFactor(1, 2)
        adaptive_splitter(split)                 # narrow: slots above the library
        self.active = False
        self.reading = False
        self._read_for = None                        # device_tag for which the slots were read
        ctx.changed.connect(self._dev_changed)
        self._titles()
        self.refresh_library()

    def _titles(self):
        name = device_name(self.ctx)
        self.dev_box.setTitle(_("On the FM-1 “{name}”").format(name=name) if name else _("On the FM-1 (none connected)"))
        self.b_imp.setText(_("To “{name}” into the chosen slot …").format(name=name) if name
                           else _("To the FM-1 into the chosen slot …"))
        # a firmware without the editor protocol takes no projects over USB: the button says so instead of
        # looking usable (it was enabled for an FM-1 on the original firmware)
        why = self._no_projects_reason() or (S3_NOT_YET if self._s3() else "")
        if not hasattr(self, "_imp_tip"):
            self._imp_tip = self.b_imp.toolTip()
        self.b_imp.setEnabled(not why)
        self.b_imp.setToolTip(why or self._imp_tip)

    def _no_projects_reason(self) -> str:
        """an FM-1 is connected, but its firmware offers no projects over USB ("" otherwise)"""
        c = self.ctx
        if c.port is None or c.connected or not getattr(c, "midi_connected", False) or self._s3():
            return ""
        if getattr(c, "protocol", "") == "update":
            return _("The FM-1 is waiting in update mode – finish the installation first (page “Recovery”).")
        from .device import firmware_name
        return _("{firmware} has no editor protocol – the app can neither read its projects nor write projects to "
                 "this FM-1.").format(firmware=firmware_name(getattr(c, "firmware_key", "")) or _("The running firmware"))

    def _s3(self) -> bool:
        """the FM-1 runs SLOOP 3: one project, its own protocol (read here with READ, as the backup does)"""
        return getattr(self.ctx, "sloop3", None) is not None and bool(getattr(self.ctx, "midi_connected", False))

    def _dev_changed(self):
        self._titles()
        if self._s3():
            self._s3_rows()
            return
        if self._read_for is not None and same_device(self._read_for, device_tag(self.ctx)) and self.slots:
            return                                   # same device, just new info
        self.dev_tree.clear()
        self.slots = []
        self._read_for = None
        self.dev_info.setText(_("Not read yet.") if self.ctx.connected
                              else self._no_projects_reason() or _("No device is connected."))
        if self.active and self.ctx.connected:
            self.read_slots(auto=True)

    def activate(self):
        """Page (tab) opened: read the slots automatically – like on the presets page."""
        self.active = True
        if self._s3():
            self._s3_rows()
            return
        if self.ctx.connected and not self.slots and not self.reading:
            self.read_slots(auto=True)

    def deactivate(self):
        self.active = False

    # ------------------------------------------------------------ Device
    def read_slots(self, auto: bool = False):
        if self._s3():
            self._s3_reread(auto)
            return
        if auto:
            if not self.ctx.connected or self.reading:
                return
        elif not self.ctx.require(self):
            return
        if self.reading:
            return

        def work(w, link):
            w.progress.emit(0, 0)
            return projects.list_slots(link)

        def fail(msg, tb):
            self.reading = False
            self.progress.hide()
            self.dev_info.setText(_("Reading the slots failed: {error}").format(error=msg))
        self.reading = True
        self.progress.show()
        self.dev_info.setText(_("Reading the project slots …"))
        tag = device_tag(self.ctx)
        self.ctx.job(self, _("Read slots"), work, dialog=False,
                     on_done=lambda res: self._got_slots(res, tag), on_fail=fail)

    def _got_slots(self, res, tag=None):
        self.reading = False
        self.progress.hide()
        self._read_for = tag or device_tag(self.ctx)
        self.slots, live = res
        keep = self._slot()                          # keep the selection across re-reading
        self.dev_tree.clear()
        sloop = self._sections()
        self.dev_tree.setHeaderLabels(["Section" if sloop else pgettext("project slot", "Slot"), _("Used"), _("Size")])
        for s in self.slots:
            it = QTreeWidgetItem([f"{s.slot:2d}" if not sloop else _("{name} (slot {n})").format(
                name=self._slot_name(s.slot), n=s.slot), "●" if s.used else "", fmt_size(s.size) if s.used else _("empty")])
            it.setData(0, Qt.ItemDataRole.UserRole, s.slot)
            self.dev_tree.addTopLevelItem(it)
            if s.slot == keep:
                self.dev_tree.setCurrentItem(it)
        used = sum(1 for s in self.slots if s.used)
        if not self.slots:
            self.dev_info.setText(_("This firmware reports no project slots that can be read one by one. Backups "
                                    "work anyway (page “Backups”)."))
        elif sloop:
            self.dev_info.setText(ngettext(
                "{used} of {n} section used, running project {size}. SLOOP projects can be fetched, collected, viewed "
                "(SLOOP 2.6 and later) and played back.",
                "{used} of {n} sections used, running project {size}. SLOOP projects can be fetched, collected, viewed "
                "(SLOOP 2.6 and later) and played back.", len(self.slots)).format(
                used=used, n=len(self.slots), size=fmt_size(live)))
        else:
            self.dev_info.setText(ngettext(
                "{used} of {n} slot used, running music {size}. You give names in the library (the protocol "
                "provides none).",
                "{used} of {n} slots used, running music {size}. You give names in the library (the protocol "
                "provides none).", len(self.slots)).format(used=used, n=len(self.slots), size=fmt_size(live)))
        if self.slots:
            self.dev_info.setText(self.dev_info.text() + " " + _("Read from “{device}” at {time}.").format(
                device=device_name(self.ctx) or 'FM-1', time=time.strftime('%H:%M')))
        key = (getattr(self.ctx, "firmware_key", "") or "").lower()
        viewable = not sloop or key == "sloop"            # (SLOOP's forks keep projects of their own kind)
        self.b_view_slot.setEnabled(viewable)
        self.b_view_slot.setToolTip(_("Read the chosen slot from the FM-1 (read only) and open it in “View & Export”")
                                    if viewable else _("The project view does not read this firmware's projects."))

    def _view_slot(self):
        """Read a slot and show it in the view (there: play on the FM-1, MIDI export)."""
        slot = self._slot()
        if self._s3() and slot is not None:
            if slot == 1 and not self._s3_saved():
                show_error(self, _("Empty"), _("No project is saved on the FM-1 yet."))
                return
            self._go_view(read_slot=slot)               # (the project view reads SLOOP 3 itself: 0 now, 1 saved)
            return
        if slot is None:
            show_error(self, _("No slot chosen"), _("Please choose a used slot on the left."))
            return
        info = next((s for s in self.slots if s.slot == slot), None)
        if info is not None and not info.used:
            show_error(self, _("Empty"), _("{word} {name} is empty – there is nothing to see.").format(
                word=self._slot_word(), name=self._slot_name(slot)))
            return
        self._go_view(read_slot=slot)

    def _view_lib(self):
        it = self.lib.currentItem()
        if not it:
            show_error(self, _("No project chosen"), _("Please choose a project from the library on the right."))
            return
        self._go_view(path=it.data(0, Qt.ItemDataRole.UserRole))

    def _go_view(self, **kw):
        win = getattr(self.ctx, "window", None)
        if win is not None and hasattr(win, "go") and win.page("project_view") is not None:
            win.go("project_view", **kw)
            return
        from .pages.project_view import ProjectView           # without a main window (tests): own view
        self._pv = ProjectView(self.ctx)
        self._pv.open_with(**kw)
        self._pv.show()

    def _slot(self) -> int | None:
        it = self.dev_tree.currentItem()
        return it.data(0, Qt.ItemDataRole.UserRole) if it else None

    def _export(self):
        slot = self._slot()
        if self._s3():
            if slot is None:
                show_error(self, _("No project chosen"), _("Please choose on the left which project to fetch."))
                return
            self._s3_fetch([slot])
            return
        if slot is None or not self.ctx.require(self):
            return
        info = next((s for s in self.slots if s.slot == slot), None)
        word, nm = self._slot_word(), self._slot_name(slot)
        if info and not info.used:
            show_error(self, _("Empty"), _("{word} {name} is empty – there is nothing to fetch.").format(word=word, name=nm))
            return
        default = f"{self.ctx.profile.name if self.ctx.profile else 'FM-1'} {word} {nm}"
        name, ok = QInputDialog.getText(self, _("Fetch project"), _("Name for the project from {word} {name}:").format(
            word=word, name=nm), text=default)
        if not ok:
            return
        self._export_slots([(slot, name.strip() or default)])

    def _export_all(self):
        if self._s3():
            self._s3_fetch(([1] if self._s3_saved() else []) + [0])
            return
        if not self.ctx.require(self):
            return
        used = [s for s in self.slots if s.used]
        if not used:
            show_error(self, _("Nothing to fetch"), _("No project slot is used on the FM-1 – or the slots have not been "
                                                      "read yet (“Read again”, F5)."))
            return
        prefix = self.ctx.profile.name if self.ctx.profile else "FM-1"
        self._export_slots([(s.slot, f"{prefix} {self._slot_word()} {self._slot_name(s.slot)}") for s in used])

    def _export_slots(self, items):
        fw = self.ctx.info.version if self.ctx.info else ""
        dev = self.ctx.profile.name if self.ctx.profile else ""
        folder = self.ctx.settings.projects_dir
        got: list = []
        stopped = [False]

        def work(w, link):
            man = backup.list_objects(link)              # one inventory for all (every LIST stops playback)

            def stop(_n=0):                              # between chunks: every read is one request and answer –
                if getattr(w, "cancelled", False):       # stopping in between leaves the FM-1 as it was
                    stopped[0] = True
                    raise backup.BackupError(_("cancelled"))
            for i, (slot, name) in enumerate(items):
                stop()
                w.log.emit(f"{self._slot_word()} {self._slot_name(slot)} → {name}")
                got.append(projects.export_slot(link, slot, folder, name, fw, dev, manifest=man, progress=stop))
                w.progress.emit(i + 1, len(items))
            return list(got)

        def failed(m):
            if stopped[0]:
                return ngettext("Cancelled – {done} of {n} project was fetched and is in the library; the one being "
                                "read was not saved. Nothing was changed on the FM-1.",
                                "Cancelled – {done} of {n} projects were fetched and are in the library; the one "
                                "being read was not saved. Nothing was changed on the FM-1.", len(items)).format(
                    done=len(got), n=len(items))
            return _("Not all projects fetched: {error} – {done} of {n} are in the library, "
                     "nothing was changed on the FM-1.").format(error=m, done=len(got), n=len(items))
        self.ctx.job(self, _("Fetch projects"), work, cancellable=True,
                     done_text=lambda out: ngettext(
                         "{done} of {n} project fetched into the library ({folder}). Nothing was changed on the FM-1.",
                         "{done} of {n} projects fetched into the library ({folder}). Nothing was changed on the FM-1.",
                         len(items)).format(done=len(out), n=len(items), folder=folder),
                     fail_text=failed)
        self.refresh_library()

    # ------------------------------------------------------ SLOOP 3: one project, read only
    def _s3_saved(self) -> bool:
        info = getattr(self, "_s3_info", None) or getattr(self.ctx, "sloop3", None)
        return bool(info is not None and info.projects_used & 1)

    def _s3_rows(self):
        """the two ways SLOOP 3's project can be read: as saved on the FM-1, and as it plays now"""
        keep = self._slot()
        self.slots = []
        self._read_for = None
        self.dev_tree.clear()
        self.dev_tree.setHeaderLabels([_("Project"), _("Used"), _("Size")])
        for slot, title, used in ((1, _("Project saved on the FM-1"), self._s3_saved()),
                                  (0, _("Project as it plays now (unsaved changes too)"), True)):
            it = QTreeWidgetItem([title, "●" if used else "", "" if used else _("empty")])
            it.setData(0, Qt.ItemDataRole.UserRole, slot)
            self.dev_tree.addTopLevelItem(it)
            if slot == keep:
                self.dev_tree.setCurrentItem(it)
        if self.dev_tree.currentItem() is None:
            self.dev_tree.setCurrentItem(self.dev_tree.topLevelItem(0 if self._s3_saved() else 1))
        self.dev_tree.resizeColumnToContents(0)
        label = self.ctx.sloop3.label if getattr(self.ctx, "sloop3", None) is not None else "SLOOP 3"
        self.dev_info.setText(_("{firmware} keeps one project on the FM-1. The app reads it the way the backup does "
                                "(read only): view and listen, or fetch it into the library as a .s3p file its web "
                                "editor opens. Writing a project to the FM-1 is not in the app yet.").format(
            firmware=label))
        self.b_view_slot.setEnabled(True)
        self.b_view_slot.setToolTip(_("Read the chosen project from the FM-1 (read only) and open it in “View & "
                                      "Export”"))

    def _s3_reread(self, auto: bool = False):
        """whether a project is saved: SLOOP 3's INFO again (read only)"""
        if auto:
            self._s3_rows()
            return
        from .. import sloop3 as S3

        def work(w, link):
            with S3.Client(link) as c:
                return c.info()

        def done(info):
            self._s3_info = info
            self._s3_rows()
        self.ctx.job(self, _("Read slots"), work, dialog=False, on_done=done,
                     on_fail=lambda m, tb: self.dev_info.setText(_("Reading the slots failed: {error}").format(error=m)))

    def _s3_fetch(self, slots: list[int]):
        """SLOOP 3's project into the library as .s3p (READ kind 0 / 2, read only)"""
        from .. import sloop3 as S3
        dev = self.ctx.profile.name if self.ctx.profile else "FM-1"
        if 1 in slots and not self._s3_saved() and slots == [1]:
            show_error(self, _("Empty"), _("No project is saved on the FM-1 yet."))
            return
        names = {1: _("{device} SLOOP 3 project").format(device=dev),
                 0: _("{device} SLOOP 3 as it played").format(device=dev)}
        if len(slots) == 1:
            name, ok = QInputDialog.getText(self, _("Fetch project"), _("Name for the project:"), text=names[slots[0]])
            if not ok:
                return
            names[slots[0]] = name.strip() or names[slots[0]]
        folder = self.ctx.settings.projects_dir

        def work(w, link):
            out = []
            for slot in slots:
                w.log.emit(names[slot])
                data = projects.fetch_sloop3(link, S3.KIND_PROJECT if slot == 1 else S3.KIND_NOW)
                out.append(projects.save_s3p(folder, names[slot], data))
            return out
        self.ctx.job(self, _("Fetch projects"), work,
                     done_text=lambda out: ngettext(
                         "{done} of {n} project fetched into the library ({folder}). Nothing was changed on the FM-1.",
                         "{done} of {n} projects fetched into the library ({folder}). Nothing was changed on the FM-1.",
                         len(slots)).format(done=len(out), n=len(slots), folder=folder),
                     fail_text=lambda m: _("Not read: {error}. Nothing was changed on the FM-1.").format(error=m))
        self.refresh_library()

    def _save_copy(self):
        """a library project as a file somewhere else (an .s3p as it is; a .fm1proj as it is)"""
        path = self._lib_path()
        if path is None:
            show_error(self, _("No project chosen"), _("Please choose a project from the library on the right."))
            return
        filt = _("SLOOP 3 project (*.s3p)") if path.suffix == projects.S3P_SUFFIX else _("FM-1 project (*.fm1proj)")
        dest, _f = QFileDialog.getSaveFileName(self, _("Save as file …"), str(Path.home() / path.name), filt)
        if not dest:
            return
        dest = Path(dest)
        if dest.suffix.lower() != path.suffix:
            dest = dest.with_name(dest.name + path.suffix)
        try:
            projects._write_bytes(dest, path.read_bytes())
        except OSError as e:
            show_error(self, _("Not saved"), fs_error_text(e, _("“{name}” not saved").format(name=dest.name)))
            return
        self.ctx.message.emit(_("Saved: {file}").format(file=dest))

    def _sections(self) -> bool:
        """SLOOP and every firmware with its backup protocol (KARMA, ORBIT, Clipper …): the four slots are the
        song's sections A–D"""
        key = (getattr(self.ctx, "firmware_key", "") or "").lower()
        ver = self.ctx.info.version if getattr(self.ctx, "info", None) else ""
        return backup.guess_protocol(key, ver) == backup.SLOOP

    def _slot_word(self) -> str:
        key = (getattr(self.ctx, "firmware_key", "") or "").lower()
        # (JIANT FM: its slots 1–4 are the current song's sections too – numbered, not A–D)
        return "Section" if self._sections() or key.startswith("jiant") else pgettext("project slot", "Slot")

    def _slot_name(self, slot: int) -> str:
        if self._sections() and 1 <= slot <= 4:
            return "ABCD"[slot - 1]
        return str(slot)

    def _erase(self):
        slot = self._slot()
        if self._s3():
            show_error(self, _("Clear slot …"), _("The app changes nothing on an FM-1 with SLOOP 3 here – it only reads "
                                                 "its project."))
            return
        if slot is None or not self.ctx.require(self):
            return
        info = next((s for s in self.slots if s.slot == slot), None)
        word, nm = self._slot_word(), self._slot_name(slot)
        if info is not None and not info.used:
            show_error(self, _("Already empty"), _("{word} {name} is already empty.").format(word=word, name=nm))
            return
        dev = self.ctx.profile.name if self.ctx.profile else "FM-1"
        size = f" ({fmt_size(info.size)})" if info is not None else ""
        if not confirm(self, _("Clear {word}").format(word=word),
                       _("Clear {word} <b>{name}</b> on <b>{device}</b>{size}?<br><br>First the app puts a copy of the "
                         "project into the library (“… before clearing”) – so you can play it back at any time. "
                         "Everything else on the FM-1 stays unchanged.").format(word=word, name=nm, device=dev, size=size),
                       _("Clear"), danger=True):
            return
        fw = self.ctx.info.version if self.ctx.info else ""
        folder = self.ctx.settings.projects_dir
        saved: list = []

        def work(w, link):
            man = backup.list_objects(link)
            cur = next((o for o in man if o.id == backup.project_id(slot)), None)
            if cur is not None and cur.size:
                w.log.emit(_("Saving the project into the library …"))
                saved.append(projects.export_slot(link, slot, folder,
                                                  _("{device} {word} {name} (before clearing)").format(
                                                      device=dev, word=word, name=nm), fw, dev, manifest=man,
                                                  notes=_("Copy the app made before clearing.")))
                w.log.emit(_("Backed up: {name}").format(name=saved[0].name))
            w.log.emit(_("Clearing {word} {name} …").format(word=word, name=nm))
            projects.erase_slot(link, slot)
            return projects.list_slots(link)
        d = self.ctx.job(self, _("Clear {word} {name}").format(word=word, name=nm), work,
                         done_text=lambda _r: _("{word} {name} on {device} is now empty.").format(
                             word=word, name=nm, device=dev) +
                         (" " + _("The project is in the library as “{name}” – from there you can play it back.").format(
                             name=saved[0].stem) if saved else ""),
                         fail_text=lambda m: _("Not cleared: {error}").format(error=m) +
                         (" " + _("(The copy “{name}” is in the library anyway.)").format(name=saved[0].stem) if saved else ""),
                         cancel_tip=_("Clearing is a single step and cannot be cancelled."))
        if d.ok:
            self._got_slots(d.result_value)
        self.refresh_library()

    # ------------------------------------------------------ Library
    def refresh_library(self):
        self.lib.clear()
        q = self.search.text().lower().split()
        try:
            lib = list(projects.library(self.ctx.settings.projects_dir))
        except OSError as e:
            self.dev_info.setText(fs_error_text(e, _("Project library not readable")))
            lib = []
        for f in projects.s3p_library(self.ctx.settings.projects_dir):
            hay = f.name.lower() + " sloop 3"
            if any(w not in hay for w in q):
                continue
            it = QTreeWidgetItem([f.name, "SLOOP 3", "", fmt_date(_iso(f.mtime)), fmt_size(f.size),
                                  f.problem and _("not readable"), "" if f.bpm is None else f"{f.bpm} BPM",
                                  "" if not f.bars else _bars(f.bars)])
            it.setData(0, Qt.ItemDataRole.UserRole, str(f.path))
            it.setToolTip(0, _("{path}\nSLOOP 3 project (.s3p), format {format} – opens in its web editor as it "
                               "is").format(path=f.path, format=f.version) + (f"\n{f.problem}" if f.problem else ""))
            self.lib.addTopLevelItem(it)
        for pf in lib:
            m = pf.meta
            hay = " ".join(str(m.get(k, "")) for k in ("name", "notes", "firmware", "device", "source")).lower()
            if any(w not in hay for w in q):
                continue
            tempo, length = _melodee_facts(pf)
            it = QTreeWidgetItem([pf.name, m.get("firmware", ""), self._origin(m.get("device", "")),
                                  fmt_date(m.get("created", "")),
                                  fmt_size(m.get("object", {}).get("size", 0)), m.get("notes", ""), tempo, length])
            it.setData(0, Qt.ItemDataRole.UserRole, str(pf.path))
            it.setToolTip(0, _("{path}\nOrigin: {source}\nFormat: {format}").format(
                path=pf.path, source=m.get('source', ''), format=m.get('kind', '')))
            self.lib.addTopLevelItem(it)
        for c in range(8):
            if c != 5:
                self.lib.resizeColumnToContents(c)
        if hasattr(self, "lib_empty"):
            self.lib_empty.setVisible(self.lib.topLevelItemCount() == 0)

    def _lib_path(self) -> Path | None:
        it = self.lib.currentItem()
        return Path(it.data(0, Qt.ItemDataRole.UserRole)) if it else None

    def _pf(self) -> projects.ProjectFile | None:
        it = self.lib.currentItem()
        if not it:
            return None
        if Path(it.data(0, Qt.ItemDataRole.UserRole)).suffix == projects.S3P_SUFFIX:
            return None                                 # (callers that handle .s3p ask _lib_path first)
        try:
            return projects.load_project(Path(it.data(0, Qt.ItemDataRole.UserRole)))
        except Exception as e:  # noqa: BLE001
            show_error(self, _("Project file damaged"), str(e))
            return None

    def _origin(self, device) -> str:
        """the device a library project came from; a name no profile has (renamed or deleted since) is marked"""
        device = str(device or "")
        try:
            known = {p.name for p in self.ctx.store.profiles.values()}
        except AttributeError:
            return device
        if not device or device in known:
            return device
        return _("{name} (no profile with this name)").format(name=device)

    def _import(self):
        path = self._lib_path()
        if self._s3() or (path is not None and path.suffix == projects.S3P_SUFFIX):
            show_error(self, _("Not yet"), S3_NOT_YET)
            return
        pf = self._pf()
        slot = self._slot()
        if not pf or not self.ctx.require(self):
            return
        fw_dev = self.ctx.info.version if self.ctx.info else ""
        src_fam = projects.family(pf.meta.get("firmware", "")) or projects.family_of_data(pf.data)
        dst_fam = projects.family(fw_dev) or projects.family(getattr(self.ctx, "firmware_key", "") or "")
        name_of = {"melodee": "Melodee", "sloop": "SLOOP", "skiff": "Skiff"}
        foreign = projects.foreign_reason(pf.meta.get("firmware", ""), fw_dev)
        if foreign:                                  # (Tessera, DUELL, JIANT …: projects of their own kind)
            show_error(self, _("Other firmware"), f"<b>{pf.name}</b>: {foreign}")
            return
        if src_fam and dst_fam and src_fam != dst_fam:
            show_error(self, _("Other firmware"),
                       _("<b>{name}</b> comes from <b>{source}</b>, the FM-1 runs <b>{target}</b>. The project formats "
                         "do not match – nothing is written.").format(
                           name=pf.name, source=name_of.get(src_fam, src_fam), target=name_of.get(dst_fam, dst_fam)))
            return
        if not self.slots and self.ctx.connected:           # read the occupancy first so the confirmation is accurate
            def read(w, link):
                return projects.list_slots(link)
            d0 = self.ctx.job(self, _("Read slots"), read, close_on_success=True)
            if not d0.ok:
                return
            self._got_slots(d0.result_value)
        n_slots = len(self.slots) or (4 if dst_fam == "sloop" else 32)
        word = self._slot_word()
        if slot is None:
            slot, ok = QInputDialog.getInt(self, _("Choose {word}").format(word=word),
                                           _("Into which project slot (1–{n})?").format(n=n_slots), 1, 1, n_slots)
            if not ok:
                return
        if slot > n_slots:
            show_error(self, _("No such slot"), ngettext("This firmware only has {n} project slot.",
                                                         "This firmware only has {n} project slots.",
                                                         n_slots).format(n=n_slots))
            return
        nm = self._slot_name(slot)
        occupied = next((s for s in self.slots if s.slot == slot and s.used), None)
        warn = ""
        if pf.meta.get("firmware") and fw_dev and pf.meta["firmware"].strip() != fw_dev.strip():
            warn = "<br><br>" + _("The project comes from <b>{source}</b>, the FM-1 runs <b>{target}</b> (same "
                                  "firmware, different version – the FM-1 checks whether it reads the format).").format(
                source=pf.meta['firmware'], target=fw_dev)
        if self.ctx.profile:
            head = _("Play <b>{name}</b> into {word} <b>{slot}</b> on <b>{device}</b>?").format(
                name=pf.name, word=word, slot=nm, device=self.ctx.profile.name)
        else:
            head = _("Play <b>{name}</b> into {word} <b>{slot}</b> on <b>the FM-1</b>?").format(
                name=pf.name, word=word, slot=nm)
        text = (head + "<br><br>" +
                (_("<b>{word} {slot} is in use ({size}) and will be overwritten.</b> Fetch it to the computer first "
                   "if you want to keep it.").format(word=word, slot=nm, size=fmt_size(occupied.size)) if occupied else
                 _("{word} {slot} is empty.").format(word=word, slot=nm)) + warn + "<br><br>" +
                _("The device checks the project first – if it does not fit, nothing is written. Everything else on "
                  "the FM-1 stays unchanged."))
        if not confirm(self, _("Play in project"), text, _("Play in"), danger=bool(occupied)):
            return

        def work(w, link):
            projects.import_to_slot(link, pf, slot, device_firmware=fw_dev)
            w.log.emit(_("{word} {slot}: {name} written. Load it on the FM-1 under SAVE > PROJECT.").format(
                word=word, slot=nm, name=pf.name))
            return projects.list_slots(link)
        d = self.ctx.job(self, _("Play in project"), work,
                         done_text=_("“{name}” is now in {word} {slot}. Load it on the FM-1 under SAVE > PROJECT.").format(
                             name=pf.name, word=word, slot=nm),
                         fail_text=lambda m: _("Not played in: {error}").format(error=m) +
                         ("" if _("nothing was written") in str(m).lower() else " – " + _("the device wrote nothing.")),
                         cancel_tip=_("A single project is written in one go – please wait a moment."))
        if d.ok:
            self._got_slots(d.result_value)

    def _rename(self):
        path = self._lib_path()
        if path is not None and path.suffix == projects.S3P_SUFFIX:
            name, ok = QInputDialog.getText(self, _("Rename"), _("Name:"), text=path.stem)
            new = path.with_name(projects._safe(name.strip()) + projects.S3P_SUFFIX) if ok and name.strip() else None
            if new is None or new == path:
                return
            if new.exists():
                show_error(self, _("Not renamed"), _("A file named {name} is already there.").format(name=new.name))
                return
            try:
                path.rename(new)
                self.ctx.message.emit(f"{_('Renamed')}: {new.stem}")
            except OSError as e:
                show_error(self, _("Not saved"), fs_error_text(e, _("{name} cannot be changed").format(name=path.name)))
            self.refresh_library()
            return
        pf = self._pf()
        if not pf:
            return
        name, ok = QInputDialog.getText(self, _("Rename"), _("Name:"), text=pf.name)
        if ok and name.strip():
            self._update(pf, _("Renamed"), name=name.strip())

    def _note(self):
        path = self._lib_path()
        if path is not None and path.suffix == projects.S3P_SUFFIX:
            show_error(self, pgettext("library", "Note"), _("A SLOOP 3 project file (.s3p) has no room for a note – it "
                                                            "is kept exactly as its web editor saves it. Its name is "
                                                            "the file's name."))
            return
        pf = self._pf()
        if not pf:
            return
        txt, ok = QInputDialog.getMultiLineText(self, pgettext("library", "Note"), _("Note:"), pf.meta.get("notes", ""))
        if ok:
            self._update(pf, _("Note saved"), notes=txt)

    def _update(self, pf, what: str, **changes):
        try:
            projects.update_meta(pf, **changes)
            self.ctx.message.emit(f"{what}: {pf.name}")
        except OSError as e:
            show_error(self, _("Not saved"), fs_error_text(e, _("{name} cannot be changed").format(name=pf.path.name)))
        self.refresh_library()

    def _delete(self):
        path = self._lib_path()
        if path is not None and path.suffix == projects.S3P_SUFFIX:
            if confirm(self, _("Delete"), _("Delete <b>{name}</b> from the library? The file {file} is removed for "
                                            "good – nothing changes on the FM-1.").format(name=path.stem, file=path.name),
                       _("Delete"), danger=True):
                try:
                    path.unlink(missing_ok=True)
                    self.ctx.message.emit(_("Deleted from the library: {name}").format(name=path.stem))
                except OSError as e:
                    show_error(self, _("Not deleted"), fs_error_text(e, _("{name} not deleted").format(name=path.name)))
                self.refresh_library()
            return
        pf = self._pf()
        if pf and confirm(self, _("Delete"), _("Delete <b>{name}</b> from the library? The file {file} is removed for "
                                               "good – nothing changes on the FM-1.").format(name=pf.name, file=pf.path.name),
                          _("Delete"), danger=True):
            try:
                pf.path.unlink(missing_ok=True)
                self.ctx.message.emit(_("Deleted from the library: {name}").format(name=pf.name))
            except OSError as e:
                show_error(self, _("Not deleted"), fs_error_text(e, _("{name} not deleted").format(name=pf.path.name)))
            self.refresh_library()

    def _from_backups(self):
        win = getattr(self.ctx, "window", None)
        if win is not None and win.page("backup_browser") is not None:
            win.go("backup_browser")
        else:
            self._add_file()

    def _add_file(self):
        files, _filter = QFileDialog.getOpenFileNames(self, _("Project or backup files"), str(Path.home()),
                                                      _("Projects and backups (*.fm1proj *.s3p *.s3b *.json)"))
        n = 0
        for f in files:
            p = Path(f)
            try:
                from .. import sloop3proj as S3P
                if p.suffix.lower() in (projects.S3P_SUFFIX, ".s3b") or S3P.is_sloop3_backup(p):
                    found = S3P.projects_in(p)
                    if not found:
                        raise S3P.S3FormatError(_("no SLOOP 3 project in it"))
                    for title, data in found:
                        projects.save_s3p(self.ctx.settings.projects_dir,
                                          p.stem if p.suffix.lower() == projects.S3P_SUFFIX else f"{p.stem} – {title}",
                                          data)
                        n += 1
                    continue
                if p.suffix == projects.SUFFIX:
                    pf = projects.load_project(p)
                    projects.save_project(self.ctx.settings.projects_dir, pf.name, pf.data, pf.meta.get("firmware", ""),
                                          pf.meta.get("device", ""), pf.meta.get("notes", ""), pf.meta.get("source", ""))
                    n += 1
                else:
                    a = backup.load_file(p)
                    for slot, data in projects.projects_from_backup(a):
                        projects.save_project(self.ctx.settings.projects_dir,
                                              _("{name} slot {n}").format(name=p.stem, n=slot), data,
                                              a.firmware, source=_("{file}, slot {n}").format(file=p.name, n=slot))
                        n += 1
            except OSError as e:
                show_error(self, _("Not saved"), fs_error_text(e, _("{name} not added to the library").format(name=p.name)))
                break
            except Exception as e:  # noqa: BLE001
                show_error(self, _("File skipped"), f"{p.name}: {e}")
        if n:
            self.ctx.message.emit(ngettext("{n} project added to the library.", "{n} projects added to the library.",
                                           n).format(n=n))
        self.refresh_library()


S3_NOT_YET = _("Not yet: the app does not write projects to an FM-1 with SLOOP 3. Put a .s3p file onto it with the "
               "Projects tab of its web editor (page “Editor”).")


def _iso(ts: float) -> str:
    import datetime as dt
    return dt.datetime.fromtimestamp(ts).isoformat(timespec="seconds")


def _bars(bars: float) -> str:
    n = int(bars) if bars == int(bars) else bars
    return ngettext("{n} bar", "{n} bars", int(bars) if bars == int(bars) else 2).format(n=n)


def _melodee_facts(pf) -> tuple[str, str]:
    """tempo and length of a library project, for the list (decoded on the computer; "" if it cannot be)"""
    try:
        from .. import midiexport as ME, projectfmt as PF
        prj = PF.decode(pf.data, pf.meta.get("firmware", ""))
        if not prj.ok:
            return "", ""
        if prj.song:
            q = sum(ME.section_quarters(prj, r) for r in prj.song)
        else:
            q = max((ME.pattern_quarters(t.patterns[t.active], prj.swing) for t in prj.used_tracks
                     if t.active < len(t.patterns) and t.patterns[t.active].used), default=0.0)
        return f"{prj.bpm} BPM", _bars(round(q / 4, 2)) if q else ""
    except Exception:  # noqa: BLE001 – the list shows what it can
        return "", ""


def _section(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setProperty("role", "section")
    return lab


def _row(*ws):
    """Button row that wraps when the window is narrow (None is ignored)."""
    h = FlowLayout()
    for w in ws:
        if w is not None:
            h.addWidget(w)
    return h
