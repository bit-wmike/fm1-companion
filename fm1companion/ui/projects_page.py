# SPDX-License-Identifier: GPL-3.0-only
"""Projekte: die 32 Plätze auf dem Gerät und die Projekt-Bibliothek auf dem Rechner."""
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

        # Gerät
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
        # gruppiert: ansehen | sichern (nur lesen) | ändert das FM-1 – Kurztitel vorn in der Reihe (keine Extrazeilen)
        gl.addWidget(flow_row(_section(_("View:")), self.b_view_slot, b_read))
        gl.addWidget(flow_row(_section(_("Back up:")), b_exp, b_all))
        gl.addWidget(flow_row(_section(_("Changes the FM-1:")), b_erase))
        split.addWidget(g)

        # Bibliothek
        lg = QGroupBox(_("Library on the computer"))
        ll = QVBoxLayout(lg)
        self.search = QLineEdit()
        self.search.setPlaceholderText(_("Search (name, note, firmware) …"))
        self.search.textChanged.connect(self.refresh_library)
        ll.addWidget(self.search)
        self.lib = QTreeWidget()
        self.lib.setHeaderLabels([_("Name"), "Firmware", _("Device"), _("Date"), _("Size"), pgettext("library", "Note")])
        self.lib.setRootIsDecorated(False)
        self.lib.setSortingEnabled(True)
        ll.addWidget(self.lib, 1)
        self.lib_empty = QLabel(_("No projects in the library yet – how it works: choose a used slot on the left and "
                                  "“Fetch to the computer”, or “Fetch all”. Project files (.fm1proj) can also be added "
                                  "with “Add file”."))
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
        b_add.clicked.connect(self._add_file)
        b_bk = QPushButton(icon("document-preview", "backup"), _("From backups …"))
        b_bk.setToolTip(_("View projects in older backups and fetch them one by one (Backups › Browse & Compare)"))
        b_bk.clicked.connect(self._from_backups)
        b_open = QPushButton(icon("folder-open"), _("Folder"))
        b_open.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(ctx.settings.projects_dir))))
        ll.addWidget(flow_row(_section(_("View:")), b_view, self.b_imp))
        ll.addWidget(flow_row(_section(_("Organise:")), b_ren, b_note, b_del))
        ll.addWidget(flow_row(_section(_("Add:")), b_add, b_bk, b_open))
        split.addWidget(lg)
        split.setChildrenCollapsible(False)
        g.setMinimumWidth(280)
        lg.setMinimumWidth(320)
        split.setStretchFactor(1, 2)
        adaptive_splitter(split)                 # schmal: Plätze über der Bibliothek
        self.active = False
        self.reading = False
        self._read_for = None                        # device_tag, für das die Plätze gelesen wurden
        ctx.changed.connect(self._dev_changed)
        self._titles()
        self.refresh_library()

    def _titles(self):
        name = device_name(self.ctx)
        self.dev_box.setTitle(_("On the FM-1 “{name}”").format(name=name) if name else _("On the FM-1 (none connected)"))
        self.b_imp.setText(_("To “{name}” into the chosen slot …").format(name=name) if name
                           else _("To the FM-1 into the chosen slot …"))

    def _dev_changed(self):
        self._titles()
        if self._read_for is not None and same_device(self._read_for, device_tag(self.ctx)) and self.slots:
            return                                   # gleiches Gerät, nur neue Infos
        self.dev_tree.clear()
        self.slots = []
        self._read_for = None
        self.dev_info.setText(_("Not read yet.") if self.ctx.connected else _("No device is connected."))
        if self.active and self.ctx.connected:
            self.read_slots(auto=True)

    def activate(self):
        """Seite (Reiter) geöffnet: Plätze automatisch lesen – wie bei den Presets."""
        self.active = True
        if self.ctx.connected and not self.slots and not self.reading:
            self.read_slots(auto=True)

    def deactivate(self):
        self.active = False

    # ------------------------------------------------------------ Gerät
    def read_slots(self, auto: bool = False):
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
        keep = self._slot()                          # Auswahl über das Neu-Lesen hinweg behalten
        self.dev_tree.clear()
        sloop = getattr(self.ctx, "firmware_key", "") == "sloop"
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
                "{used} of {n} section used, running project {size}. SLOOP projects can be fetched, collected and "
                "played back – viewing only works in the SLOOP web editor.",
                "{used} of {n} sections used, running project {size}. SLOOP projects can be fetched, collected and "
                "played back – viewing only works in the SLOOP web editor.", len(self.slots)).format(
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
        self.b_view_slot.setEnabled(not sloop)
        self.b_view_slot.setToolTip(_("SLOOP projects are shown by the SLOOP web editor (page “Editor”).") if sloop else
                                    _("Read the chosen slot from the FM-1 (read only) and open it in “View & Export”"))

    def _view_slot(self):
        """Platz lesen und in der Ansicht zeigen (dort: abspielen auf dem FM-1, MIDI-Export)."""
        slot = self._slot()
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
        from .pages.project_view import ProjectView           # ohne Hauptfenster (Tests): eigene Ansicht
        self._pv = ProjectView(self.ctx)
        self._pv.open_with(**kw)
        self._pv.show()

    def _slot(self) -> int | None:
        it = self.dev_tree.currentItem()
        return it.data(0, Qt.ItemDataRole.UserRole) if it else None

    def _export(self):
        slot = self._slot()
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

        def work(w, link):
            man = backup.list_objects(link)              # ein Inventar für alle (jedes LIST stoppt die Wiedergabe)
            for i, (slot, name) in enumerate(items):
                w.log.emit(f"{self._slot_word()} {self._slot_name(slot)} → {name}")
                got.append(projects.export_slot(link, slot, folder, name, fw, dev, manifest=man))
                w.progress.emit(i + 1, len(items))
            return list(got)
        self.ctx.job(self, _("Fetch projects"), work, cancellable=False,
                     done_text=lambda out: ngettext(
                         "{done} of {n} project fetched into the library ({folder}). Nothing was changed on the FM-1.",
                         "{done} of {n} projects fetched into the library ({folder}). Nothing was changed on the FM-1.",
                         len(items)).format(done=len(out), n=len(items), folder=folder),
                     fail_text=lambda m: _("Not all projects fetched: {error} – {done} of {n} are in the library, "
                                           "nothing was changed on the FM-1.").format(error=m, done=len(got), n=len(items)))
        self.refresh_library()

    def _slot_word(self) -> str:
        return "Section" if getattr(self.ctx, "firmware_key", "") == "sloop" else pgettext("project slot", "Slot")

    def _slot_name(self, slot: int) -> str:
        if getattr(self.ctx, "firmware_key", "") == "sloop" and 1 <= slot <= 4:
            return "ABCD"[slot - 1]
        return str(slot)

    def _erase(self):
        slot = self._slot()
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

    # ------------------------------------------------------ Bibliothek
    def refresh_library(self):
        self.lib.clear()
        q = self.search.text().lower().split()
        try:
            lib = list(projects.library(self.ctx.settings.projects_dir))
        except OSError as e:
            self.dev_info.setText(fs_error_text(e, _("Project library not readable")))
            lib = []
        for pf in lib:
            m = pf.meta
            hay = " ".join(str(m.get(k, "")) for k in ("name", "notes", "firmware", "device", "source")).lower()
            if any(w not in hay for w in q):
                continue
            it = QTreeWidgetItem([pf.name, m.get("firmware", ""), m.get("device", ""), fmt_date(m.get("created", "")),
                                  fmt_size(m.get("object", {}).get("size", 0)), m.get("notes", "")])
            it.setData(0, Qt.ItemDataRole.UserRole, str(pf.path))
            it.setToolTip(0, _("{path}\nOrigin: {source}\nFormat: {format}").format(
                path=pf.path, source=m.get('source', ''), format=m.get('kind', '')))
            self.lib.addTopLevelItem(it)
        for c in range(5):
            self.lib.resizeColumnToContents(c)
        if hasattr(self, "lib_empty"):
            self.lib_empty.setVisible(self.lib.topLevelItemCount() == 0)

    def _pf(self) -> projects.ProjectFile | None:
        it = self.lib.currentItem()
        if not it:
            return None
        try:
            return projects.load_project(Path(it.data(0, Qt.ItemDataRole.UserRole)))
        except Exception as e:  # noqa: BLE001
            show_error(self, _("Project file damaged"), str(e))
            return None

    def _import(self):
        pf = self._pf()
        slot = self._slot()
        if not pf or not self.ctx.require(self):
            return
        fw_dev = self.ctx.info.version if self.ctx.info else ""
        src_fam = projects.family(pf.meta.get("firmware", "")) or projects.family_of_data(pf.data)
        dst_fam = projects.family(fw_dev) or projects.family(getattr(self.ctx, "firmware_key", "") or "")
        name_of = {"melodee": "Melodee", "sloop": "SLOOP", "skiff": "Skiff"}
        if src_fam and dst_fam and src_fam != dst_fam:
            show_error(self, _("Other firmware"),
                       _("<b>{name}</b> comes from <b>{source}</b>, the FM-1 runs <b>{target}</b>. The project formats "
                         "do not match – nothing is written.").format(
                           name=pf.name, source=name_of.get(src_fam, src_fam), target=name_of.get(dst_fam, dst_fam)))
            return
        if not self.slots and self.ctx.connected:           # Belegung erst lesen, damit die Bestätigung stimmt
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
        pf = self._pf()
        if not pf:
            return
        name, ok = QInputDialog.getText(self, _("Rename"), _("Name:"), text=pf.name)
        if ok and name.strip():
            self._update(pf, _("Renamed"), name=name.strip())

    def _note(self):
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
                                                      _("Projects and backups (*.fm1proj *.json)"))
        n = 0
        for f in files:
            p = Path(f)
            try:
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


def _section(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setProperty("role", "section")
    return lab


def _row(*ws):
    """Knopfreihe, die bei schmalem Fenster umbricht (None wird ignoriert)."""
    h = FlowLayout()
    for w in ws:
        if w is not None:
            h.addWidget(w)
    return h
