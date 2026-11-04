# SPDX-License-Identifier: GPL-3.0-only
"""Presets: Melodee's 384 user slots (shared library, command 80) and the custom preset library."""
from __future__ import annotations

import json
import time
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                             QMenu, QPushButton, QSpinBox, QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ... import presetlib as pl
from ..common import (DeviceUndo, confirm, notify, device_tag, flow_row, fs_error_text, heading, icon,
                      fw_label, safe_folder, same_device, show_error)
from . import PageSpec
from ...i18n import _, ngettext, pgettext

HINT = _("Writing, renaming and deleting save to the flash of the FM-1 and <b>stop playback for this</b>. "
         "Favourites you change during playback are saved by the FM-1 only after STOP.")


def _tree(headers):
    t = QTreeWidget()
    t.setHeaderLabels(headers)
    t.setRootIsDecorated(False)
    t.setSortingEnabled(True)
    t.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
    t.setAlternatingRowColors(True)
    return t


class PresetsPage(QWidget):
    def __init__(self, ctx, window=None):
        super().__init__()
        self.ctx, self.window = ctx, window
        self.caps: pl.Caps | None = None
        self.slots: list[dict] = []
        self.cache: dict[int, pl.Preset] = {}          # slots read
        self.undo = DeviceUndo()                      # (slot, old preset) per device
        self._dev_tag = None
        self.fs_error = ""
        self.lib_items: list[pl.Preset] = []
        self.busy = False
        self.active = False
        lay = QVBoxLayout(self)
        lay.addWidget(heading(_("Presets"), _("The 384 user slots of your FM-1 (Melodee from 1.5) and your own "
                                              "preset library in the folder FM-1/Presets – back up, compare, "
                                              "restore, listen.")))
        self.state = QLabel("")
        self.state.setWordWrap(True)
        self._note = ""                               # result of the last action, stays after re-reading
        self._worker = None
        self.b_cancel = QPushButton(icon("process-stop", "dialog-cancel"), _("Cancel"))
        self.b_cancel.setToolTip(_("Stop the running task after the current slot – what is done stays"))
        self.b_cancel.clicked.connect(self.cancel_job)
        self.b_cancel.hide()
        srow = QHBoxLayout()
        srow.addWidget(self.state, 1)
        srow.addWidget(self.b_cancel, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(srow)
        hint = QLabel(HINT)
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid);")
        lay.addWidget(hint)
        # Filter
        fl = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setMinimumWidth(160)
        self.search.setPlaceholderText(_("Search: name, engine, slot, tag …  (Ctrl+F)"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.fill)
        fl.addWidget(self.search, 1)
        self.engine = QComboBox()
        self.engine.addItem(_("all engines"), None)
        self.engine.currentIndexChanged.connect(self.fill)
        self.engine.setToolTip(_("filter by engine"))
        fl.addWidget(self.engine)
        self.only_used = QCheckBox(_("used only"))
        self.only_used.setChecked(True)
        self.only_used.toggled.connect(self.fill)
        fl.addWidget(self.only_used)
        self.only_fav = QCheckBox(_("favourites only ★"))
        self.only_fav.toggled.connect(self.fill)
        fl.addWidget(self.only_fav)
        lay.addLayout(fl)
        QShortcut(QKeySequence("Ctrl+F"), self, activated=self.search.setFocus)

        sp = QSplitter(Qt.Orientation.Horizontal)
        lay.addWidget(sp, 1)
        # ---- Device
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(QLabel(_("<b>On the FM-1</b>")))
        self.dev = _tree([_("Slot"), _("Engine"), _("Name"), "★", _("Kind"), _("Library")])
        self.dev.itemSelectionChanged.connect(self._sel_changed)
        ll.addWidget(self.dev, 1)
        self.empty = QLabel(_("No FM-1 with Melodee connected yet – here's how: plug in the FM-1 via USB, "
                              "Melodee 1.5 or newer must be running."))
        self.empty.setWordWrap(True)
        ll.addWidget(self.empty)
        r1 = self._flow(ll)
        self.b_read = self._btn(_("Read in"), "view-refresh", self.read_list, _("Read the list of slots again (F5)"), r1)
        self.b_read.setProperty("variant", "primary")
        QShortcut(QKeySequence("F5"), self, activated=self.read_list)
        self.b_all = self._btn(_("Back up all"), "document-save-all", self.save_all,
                               _("fetch all used slots completely into the library"), r1)
        self.b_all.setProperty("variant", "primary")
        self.b_fetch = self._btn(_("To library"), "go-next", self.fetch_selected,
                                 _("fetch the marked slots into the library"), r1)
        # listen / save on a track
        r2 = self._flow(ll)
        tl = QLabel(_("Track:"))
        tl.setToolTip(_("Track on the FM-1 for listening / saving"))
        r2.addWidget(tl)
        self.track = QSpinBox()
        self.track.setRange(1, 4)
        self.track.setToolTip(_("Track on the FM-1 for listening / saving"))
        r2.addWidget(self.track)
        self.b_load = self._btn(_("Listen"), "media-playback-start", self.load_track,
                                _("Load the preset onto the track (changes only the sound of the track, saves nothing)"), r2)
        self.b_store = self._btn(_("Save current sound as preset …"), "document-save", self.store_track,
                                 _("save the current sound of the track into this slot"), r2)
        # less frequently used actions: “More” menu (and context menu of the list)
        self.more = QMenu(self)
        self.b_ren = self._menu_action(self.more, _("Rename …"), "edit-rename", self.rename, _("Rename the preset on the FM-1 (F2)"))
        QShortcut(QKeySequence("F2"), self.dev, activated=self.rename)
        self.b_fav = self._menu_action(self.more, _("Favourite ★ on/off"), "starred", self.toggle_fav, _("Favourite on/off"))
        self.b_move = self._menu_action(self.more, _("Move/swap …"), "transform-move", self.move_slot,
                                        _("Move a slot into another slot or swap two slots"))
        self.more.addSeparator()
        self.b_del = self._menu_action(self.more, _("Clear slot …"), "edit-delete", self.erase, _("Clear the slot on the FM-1"))
        self.b_undo = self._menu_action(self.more, _("Undo"), "edit-undo", self.do_undo,
                                        _("take back the last change on the FM-1"))
        self.b_more = QPushButton(icon("overflow-menu", "application-menu"), _("More ▾"))
        self.b_more.setToolTip(_("Rename, favourite, move, clear, undo"))
        self.b_more.setMenu(self.more)
        r2.addWidget(self.b_more)
        self.dev.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.dev.customContextMenuRequested.connect(lambda pos: self.more.exec(self.dev.viewport().mapToGlobal(pos)))
        sp.addWidget(left)
        # ---- Library
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(QLabel(_("<b>Library</b> (all devices)")))
        self.lib = _tree([_("Name"), _("Engine"), _("Device"), _("Slot"), _("Labels"), _("Kind")])
        self.lib.itemSelectionChanged.connect(self._sel_changed)
        rl.addWidget(self.lib, 1)
        self.lib_empty = QLabel(_("No presets in the library yet – here's how: “Back up all” fetches all "
                                  "used slots from the FM-1, “Import” reads .fm1preset, set, editor and .syx files."))
        self.lib_empty.setWordWrap(True)
        rl.addWidget(self.lib_empty)
        q1 = self._flow(rl)
        self.b_put = self._btn(_("Write to slot …"), "go-previous", self.write_selected,
                               _("Write the library preset into the marked slot on the FM-1"), q1)
        self.b_cmp = self._btn(_("Compare"), "kompare", self.compare, _("Compare the slot on the FM-1 with the library preset"), q1)
        self.b_tag = self._btn(_("Labels …"), "tag", self.edit_tags, _("Set tags (separated by commas)"), q1)
        self.b_lren = self._btn(_("Rename …"), "edit-rename", self.lib_rename,
                                _("Change the name of the library preset (only the file, nothing on the FM-1)"), q1)
        q2 = self._flow(rl)
        self._btn(_("Import …"), "document-import", self.import_files, _("Fetch files into the library"), q2)
        self.b_exp = self._btn(_("Export …"), "document-export", lambda: self.export(),   # (clicked passes False as target)
                               _("marked presets as a set (.zip), single (.fm1preset) or for the web editor (.json)"), q2)
        self.b_ldel = self._btn(_("Remove"), "edit-delete", self.lib_delete, _("delete from the library"), q2)
        self._btn(_("Open folder"), "folder-open", self.open_folder, _("Library folder in the file manager"), q2)
        sp.addWidget(right)
        sp.setChildrenCollapsible(False)
        left.setMinimumWidth(300)
        right.setMinimumWidth(260)
        sp.setSizes([600, 500])
        ctx.changed.connect(self._dev_changed)
        self.refresh_library()
        self._dev_changed()

    # ------------------------------------------------------------ Basics
    @staticmethod
    def _flow(parent_layout):
        w = flow_row()
        parent_layout.addWidget(w)
        return w.layout()

    def _btn(self, text, ic, fn, tip, lay):
        b = QPushButton(icon(ic), text)
        b.setToolTip(tip)
        b.clicked.connect(fn)
        lay.addWidget(b)
        return b

    @staticmethod
    def _menu_action(menu, text, ic, fn, tip):
        a = menu.addAction(icon(ic), text)
        a.setToolTip(tip)
        a.setStatusTip(tip)
        a.triggered.connect(fn)
        return a

    def backup_all(self):
        """Overview quick access: read the slots (if needed) and back up all occupied ones."""
        if not self.ctx.connected:
            self.ctx.require(self)
            return
        if self.slots and not self.busy:
            self.save_all()
            return
        self._save_after_read = True
        if not self.busy:
            self.read_list()

    @property
    def root(self) -> Path:
        p, self.fs_error = safe_folder("Presets")
        return p

    def library(self) -> pl.Library:
        return pl.Library(self.root)

    def device_name(self) -> str:
        return self.ctx.profile.folder_name if self.ctx.profile else _("Unknown device")

    def activate(self):
        self.active = True
        if self.ctx.connected and self.caps is None and not self.busy:
            self.read_list()

    def deactivate(self):
        self.active = False

    def _dev_changed(self):
        tag = device_tag(self.ctx)
        if self._dev_tag is not None and same_device(tag, self._dev_tag) and self.slots:
            self._enable()                     # same device, just new info
            return
        self._dev_tag = tag
        self.caps, self.slots, self.cache = None, [], {}
        self._note = ""
        self.state.setText(self._status_text())
        self.fill()
        if self.ctx.connected and self.active:
            if self.busy:
                self._reread = True            # a job for the old device is still running: re-read afterwards
            else:
                self.read_list()

    def _after_job(self):
        if getattr(self, "_reread", False):
            self._reread = False
            if self.ctx.connected and self.active and not self.busy:
                self.read_list()

    def _status_text(self) -> str:
        """Status line without a read list (after a device change)."""
        c = self.ctx
        if c.connected:
            return _("{firmware}: slots not read yet – “Read in” (F5).").format(firmware=fw_label(c.title()))
        if getattr(c, "midi_connected", False):
            return _("{firmware}: This firmware has no preset library via USB (MIDI only). "
                     "You can still search and export your library on the right.").format(firmware=fw_label(c.title()))
        return _("No FM-1 connected – the library on the right can be used anyway.")

    def _job(self, title, fn, done, dialog=False, partial=None, cancellable=False, unit=None):
        """fn(worker, bank) exclusively on the device. partial(): called on errors so that steps already
        done remain undoable. cancellable: offer “Cancel” (fn checks worker.cancelled between
        slots). Progress (worker.progress) appears in the status line."""
        if not self.ctx.require(self):
            return
        if self.busy:
            return
        self.busy = True
        self._enable()

        def work(w, link):
            if self.caps is None or not self.caps.shared:
                self.caps = pl.probe(link, self.ctx.info)
            if not self.caps.shared:
                raise pl.PresetError(pl.refusal_text(self.caps))
            return fn(w, pl.SharedBank(link, self.caps))

        tag = device_tag(self.ctx)
        self._job_tag = tag
        t0 = time.monotonic()

        def finish():
            self.busy = False
            self._worker = None
            self.b_cancel.hide()

        def ok(res):
            finish()
            if same_device(tag, device_tag(self.ctx)):
                done(res)
            elif partial:                      # device changed: only keep the undo for the old device
                partial()
            self._enable()
            self._after_job()

        def fail(msg, tb):
            cancelled = self._worker is not None and self._worker.cancelled
            finish()
            if partial:
                partial()
            self._enable()
            QTimer.singleShot(0, self._after_job)
            if cancelled:
                text = _("{title}: cancelled – nothing more was changed on the FM-1.").format(title=title)
                if partial and self.undo:
                    text += _(" “Undo” takes back what was already changed.")
                self.state.setText(text)
                return
            if self.caps and not self.caps.shared:       # not an error but a property of the firmware
                self.state.setText(_("{firmware}: no user presets via USB – "
                                     "you can still use the library on the right. Nothing was changed on the FM-1.").format(
                    firmware=fw_label(self.ctx.title())))
                self.empty.setText(pl.refusal_text(self.caps))
                return
            text = _("<b>{title} failed:</b> {error}").format(title=title, error=msg)
            if partial and self.undo:
                text += _(" Slots already changed can be taken back with “Undo”.")
            self.state.setText(text)
            show_error(self, title, msg, tb)

        def progress(a, b):
            if b and self.busy:
                el = time.monotonic() - t0
                rest = _(", about {n} s left").format(n=int(el / a * (b - a)) + 1) if a and el > 1.5 and a < b else ""
                self.state.setText(_("{title} … {a} of {b} {unit}{rest}").format(title=title, a=a, b=b, unit=unit or _("slots"),
                                                                                  rest=rest))
        self.state.setText(f"{title} …")
        self.b_cancel.setVisible(bool(cancellable))
        self._worker = self.ctx.job(self, title, work, dialog=False, on_done=ok, on_fail=fail, on_progress=progress)

    def cancel_job(self):
        """“Cancel”: the running job stops after the current slot (what's done stays)."""
        if self._worker is not None:
            self._worker.cancelled = True
            self.b_cancel.setEnabled(False)
            self.state.setText(_("Stopping after the current slot …"))
            QTimer.singleShot(0, lambda: self.b_cancel.setEnabled(True))

    def _set_note(self, text: str):
        """Remember the result of an action; read_list() shows it before the slot summary."""
        self._note = text
        self.state.setText(text)

    # ------------------------------------------------------------ List
    def read_list(self):
        def work(w, bank):
            return bank.list_all(progress=lambda a, b: w.progress.emit(a, b), cancel=lambda: w.cancelled)

        def done(slots):
            self.slots = slots
            self.cache = {k: v for k, v in self.cache.items() if k < len(slots)}
            used = sum(s["used"] for s in slots)
            fav = sum(s["favorite"] for s in slots)
            note, self._note = self._note, ""
            fw = fw_label(self.ctx.title())
            summary = (_("{firmware}: {used} of {total} slots used, {fav} favourites.").format(
                           firmware=fw, used=used, total=len(slots), fav=fav) if fav
                       else _("{firmware}: {used} of {total} slots used.").format(firmware=fw, used=used, total=len(slots)))
            self.state.setText((note + " — " if note else "") + summary)
            self._engines()
            self.fill()
            if getattr(self, "_save_after_read", False):
                self._save_after_read = False
                QTimer.singleShot(0, self.save_all)
        self._job(_("Read preset list"), work, done, cancellable=True)

    def _engines(self):
        cur = self.engine.currentData()
        self.engine.blockSignals(True)
        self.engine.clear()
        self.engine.addItem(_("all engines"), None)
        engs = sorted({s["engine"] for s in self.slots if s["used"]} | {p.engine for p in self.lib_items})
        for e in engs:
            self.engine.addItem(self._ename(e), e)
        i = self.engine.findData(cur)
        self.engine.setCurrentIndex(max(0, i))
        self.engine.blockSignals(False)

    def _ename(self, e):
        return self.caps.engine_name(e) if self.caps else pl.engine_display_name(e)

    def _match(self, text_parts, engine, fav):
        q = self.search.text().strip().lower()
        e = self.engine.currentData()
        if e is not None and engine != e:
            return False
        if self.only_fav.isChecked() and not fav:
            return False
        return not q or any(q in t.lower() for t in text_parts)

    def fill(self):
        ok = self.ctx.connected
        self.empty.setVisible(not self.slots)
        if ok and self.caps is not None and not self.caps.shared:
            self.empty.setText(pl.refusal_text(self.caps))
        elif not ok and getattr(self.ctx, "midi_connected", False):
            self.empty.setText(_("This firmware has no preset library the app can read via USB "
                                 "(Melodee from 1.5 only). Here you save presets directly on the FM-1."))
        elif not ok:
            self.empty.setText(_("No FM-1 with Melodee connected yet – here's how: plug in the FM-1 via USB, "
                                 "Melodee 1.5 or newer must be running."))
        hashes = {p.content_hash() for p in self.lib_items}
        self.dev.setSortingEnabled(False)
        self.dev.clear()
        for s in self.slots:
            if self.only_used.isChecked() and not s["used"]:
                continue
            label = f"U{s['slot'] + 1:03d}"
            eng = self._ename(s["engine"]) if s["used"] else ""
            p = self.cache.get(s["slot"])
            if not self._match([label, s["name"], eng], s["engine"] if s["used"] else -1, s["favorite"]):
                continue
            inlib = "" if not p else ("✓" if p.content_hash() in hashes else "–")
            it = QTreeWidgetItem([label, eng, s["name"] if s["used"] else _("(empty)"), "★" if s["favorite"] else "",
                                  p.kind_text if p else "", inlib])
            it.setData(0, Qt.ItemDataRole.UserRole, s["slot"])
            self.dev.addTopLevelItem(it)
        self.dev.setSortingEnabled(True)
        self.dev.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.lib.setSortingEnabled(False)
        self.lib.clear()
        for i, p in enumerate(self.lib_items):
            eng = p.engine_name or pl.engine_display_name(p.engine)
            if not self._match([p.name, eng, p.device, p.label, *p.tags], p.engine, p.favorite):
                continue
            it = QTreeWidgetItem([p.name, eng, p.device, p.label, ", ".join(p.tags), p.kind_text])
            it.setData(0, Qt.ItemDataRole.UserRole, i)
            it.setToolTip(0, str(p.path or ""))
            self.lib.addTopLevelItem(it)
        self.lib.setSortingEnabled(True)
        self.lib_empty.setVisible(not self.lib_items)
        for t in (self.dev, self.lib):
            for c in range(t.columnCount()):
                t.resizeColumnToContents(c)
        self._enable()

    def _enable(self):
        ok = self.ctx.connected and not self.busy and bool(self.slots)
        ds, ls = self.sel_slots(), self.sel_lib()
        used = [s for s in ds if self.slots[s]["used"]]
        for b, cond in ((self.b_read, self.ctx.connected and not self.busy), (self.b_all, ok),
                        (self.b_fetch, ok and used), (self.b_load, ok and len(used) == 1),
                        (self.b_store, ok and len(ds) == 1), (self.b_ren, ok and len(used) == 1),
                        (self.b_fav, ok and len(used) == 1 and bool(self.caps and self.caps.favorites)),
                        (self.b_move, ok and len(used) == 1), (self.b_del, ok and bool(used)),
                        (self.b_undo, ok and self.undo.matches(self.ctx)), (self.b_put, ok and len(ls) >= 1 and len(ds) == 1),
                        (self.b_cmp, ok and len(ls) == 1 and len(used) == 1), (self.b_tag, bool(ls)),
                        (self.b_lren, len(ls) == 1), (self.b_exp, bool(ls)), (self.b_ldel, bool(ls))):
            b.setEnabled(bool(cond))
        self.b_undo.setText(_("Undo: {what}").format(what=self.undo.what()) if self.undo else _("Undo"))
        self.b_undo.setToolTip(_("take back the last change on the FM-1") if not self.undo or self.undo.matches(self.ctx)
                               else self.undo.refusal())

    def _sel_changed(self):
        self._enable()

    def sel_slots(self) -> list[int]:
        return sorted(it.data(0, Qt.ItemDataRole.UserRole) for it in self.dev.selectedItems())

    def sel_lib(self) -> list[pl.Preset]:
        return [self.lib_items[it.data(0, Qt.ItemDataRole.UserRole)] for it in self.lib.selectedItems()]

    def select_slot(self, slot: int):
        for i in range(self.dev.topLevelItemCount()):
            it = self.dev.topLevelItem(i)
            if it.data(0, Qt.ItemDataRole.UserRole) == slot:
                self.dev.setCurrentItem(it)
                return True
        return False

    def refresh_library(self):
        try:
            self.lib_items = self.library().all()
        except OSError as e:
            self.lib_items = []
            self.fs_error = fs_error_text(e, _("Library not readable"))
        if self.fs_error:
            self.lib_empty.setText(self.fs_error)
        self._engines()
        self.fill()

    def _slot_text(self, slot: int) -> str:
        s = self.slots[slot]
        label = f"U{slot + 1:03d}"
        return (_("{slot} “{name}” ({engine})").format(slot=label, name=s['name'], engine=self._ename(s['engine'])) if s["used"]
                else _("{slot} (empty)").format(slot=label))

    # ------------------------------------------------------------ Back up
    def _fetch(self, slots: list[int], title: str):
        if not slots:
            self.state.setText(_("{title}: No used slot is chosen – nothing to back up.").format(title=title)
                               if self.slots else _("{title}: First “Read in”, then back up.").format(title=title))
            return
        dev = self.device_name()
        favs = {k: self.slots[k]["favorite"] for k in slots}   # read in the GUI thread, not in the background

        def work(w, bank):
            lib = self.library()
            known = lib.hashes()
            new = 0
            got = {}
            for i, k in enumerate(slots):
                if w.cancelled:
                    break
                p = bank.get(k)
                p.device = dev
                p.favorite = favs[k]
                got[k] = p
                if p.used:
                    try:
                        _path, n = lib.save(p, dev, known)
                    except OSError as e:
                        raise pl.PresetError(fs_error_text(e, _("Preset not saved in the library"))) from e
                    new += n
                w.progress.emit(i + 1, len(slots))
            return got, new

        def done(res):
            got, new = res
            self.cache.update(got)
            stopped = _(" (cancelled)") if len(got) < len(slots) else ""
            self.state.setText(_("{got} of {total} presets read{stopped}, {new} new in the library "
                                 "(folder Presets/{device}); {old} were already there. Nothing was changed on the FM-1.").format(
                got=len(got), total=len(slots), stopped=stopped, new=new, device=dev, old=len(got) - new))
            self.refresh_library()
        self._job(title, work, done, cancellable=True, unit=_("presets"))

    def save_all(self):
        self._fetch([s["slot"] for s in self.slots if s["used"]], _("Back up all presets"))

    def fetch_selected(self):
        self._fetch([k for k in self.sel_slots() if self.slots[k]["used"]], _("Fetch presets"))

    # ------------------------------------------------------------ Write
    def _after_write(self, slot, old, what):
        self.undo.push(self.ctx, (slot, old), what)
        self.read_list()

    def _keep(self, olds: list, what: str):
        """Create undo entries for steps already done (also after an error midway)."""
        for k, old in olds:
            self.cache.pop(k, None)
            self.undo.push(self.ctx, (k, old), what.format(slot=f"U{k + 1:03d}"), getattr(self, "_job_tag", None))
        olds.clear()

    def write_selected(self):
        ds, ls = self.sel_slots(), self.sel_lib()
        if len(ds) != 1 or not ls:
            return
        start = ds[0]
        targets = list(range(start, min(len(self.slots), start + len(ls))))
        if len(targets) < len(ls):
            show_error(self, _("Not enough slots"), _("{n} presets no longer fit from {slot} on.").format(n=len(ls), slot=f"U{start + 1:03d}"))
            return
        occupied = [k for k in targets if self.slots[k]["used"]]
        text = (_("Write “{name}” into {slot}?").format(name=ls[0].name, slot=self._slot_text(start)) if len(ls) == 1 else
                _("Write {n} presets into {first}–{last}?").format(n=len(ls), first=f"U{start + 1:03d}", last=f"U{targets[-1] + 1:03d}"))
        if occupied:
            text += _("\n\nWill be overwritten:\n{slots}").format(slots="\n".join(self._slot_text(k) for k in occupied[:12]))
            if len(occupied) > 12:
                text += _("\n… and {n} more").format(n=len(occupied) - 12)
        text += _("\n\nThe old content is read first – you can take it back with “Undo”. "
                  "The FM-1 stops playback for this.")
        if not confirm(self, _("Write into the FM-1"), text, _("Write"), danger=bool(occupied)):
            return

        olds: list = []                     # grows with each written slot

        def work(w, bank):
            for i, (k, p) in enumerate(zip(targets, ls)):
                if w.cancelled:
                    break
                old = bank.get(k)
                olds.append((k, old))          # remember before writing (half written = also restore)
                bank.put(k, p)
                w.progress.emit(i + 1, len(ls))
            return len(olds)

        def done(n):
            self._keep(olds, _("{slot} written"))
            if n == 0:                         # cancelled before the first slot
                self._set_note(_("Writing cancelled – nothing was changed on the FM-1."))
                self.read_list()
                return
            stopped = _(" ({n} not written after cancelling)").format(n=len(ls) - n) if n < len(ls) else ""
            where = (_("“{name}” → {slot}").format(name=ls[0].name, slot=f"U{start + 1:03d}") if n == 1 else
                     f"U{start + 1:03d}–U{start + n:03d}")
            self._set_note(ngettext("{n} preset written{stopped}: {where}. “Undo” restores the old content.",
                                    "{n} presets written{stopped}: {where}. “Undo” restores the old content.", n).format(
                n=n, stopped=stopped, where=where))
            self.read_list()

        self._job(_("Write presets"), work, done, partial=lambda: self._keep(olds, _("{slot} written")),
                  cancellable=len(ls) > 1, unit=_("presets"))

    def do_undo(self):
        if not self.undo or not self.undo.check(self.ctx, self):
            return
        slot, old = self.undo.peek()
        what = self.undo.what()
        label = f"U{slot + 1:03d}"
        question = (_("Take back {what}? {slot} gets “{name}” again.").format(what=what, slot=label, name=old.name) if old.used
                    else _("Take back {what}? {slot} becomes empty again.").format(what=what, slot=label))
        if not confirm(self, _("Undo"), question, _("Revert")):
            return

        def work(w, bank):
            bank.restore(slot, old)

        def done(_res):
            self.undo.pop()
            self.cache.pop(slot, None)
            self._set_note(_("Taken back: {what} – {slot} is “{name}” again.").format(what=what, slot=label, name=old.name)
                           if old.used else _("Taken back: {what} – {slot} is empty again.").format(what=what, slot=label))
            self.read_list()
        self._job(_("Undo"), work, done)

    def ask_name(self, title: str, prompt: str, current: str, limit: int) -> str | None:
        """Ask for a name and adapt it to the FM-1's limits BEFORE sending (ASCII, limit characters) –
        with explanation and confirmation if the name changes as a result. None = cancelled."""
        name, ok = QInputDialog.getText(self, title, prompt, text=current)
        if not ok:
            return None
        fitted, why = pl.fit_name(name, limit)
        if not fitted:
            show_error(self, title, _("The name must not be empty – {reason}").format(reason=why) if why
                       else _("The name must not be empty."))
            return None
        if why and not confirm(self, title, _("{reason}\n\nTake it like this?").format(reason=why), _("Accept")):
            return None
        return fitted

    def rename(self):
        ds = [k for k in self.sel_slots() if self.slots[k]["used"]]
        if len(ds) != 1:
            return
        k = ds[0]
        limit = pl.name_limit(self.slots[k]["engine"])
        old_name = self.slots[k]["name"]
        name = self.ask_name(_("Rename"), _("New name for {slot}\n(at most {n} characters, "
                                            "letters/digits/simple characters – the app transliterates umlauts):").format(
            slot=self._slot_text(k), n=limit), old_name, limit)
        if not name or name == old_name:
            return

        olds: list = []

        def work(w, bank):
            olds.append((k, bank.get(k)))
            bank.rename(k, name)

        def done(_res):
            self._keep(olds, _("{slot} renamed"))
            self._set_note(_("{slot} renamed: “{old}” → “{new}”.").format(slot=f"U{k + 1:03d}", old=old_name, new=name))
            self.read_list()
        self._job(_("Rename"), work, done, partial=lambda: self._keep(olds, _("{slot} renamed")))

    def erase(self):
        ds = [k for k in self.sel_slots() if self.slots[k]["used"]]
        if not ds:
            return
        more = _("\n… and {n} more").format(n=len(ds) - 12) if len(ds) > 12 else ""
        if not confirm(self, _("Delete"), ngettext("Clear {n} slot on the FM-1?\n\n{slots}{more}\n\nThe content is read first (undo possible).",
                                                   "Clear {n} slots on the FM-1?\n\n{slots}{more}\n\nThe content is read first (undo possible).",
                                                   len(ds)).format(n=len(ds), slots="\n".join(self._slot_text(k) for k in ds[:12]), more=more),
                       _("Delete"), danger=True):
            return

        olds: list = []
        names = [self.slots[k]["name"] for k in ds]

        def work(w, bank):
            for k in ds:
                if w.cancelled:
                    break
                olds.append((k, bank.get(k)))
                bank.erase(k)
                w.progress.emit(len(olds), len(ds))
            return len(olds)

        def done(n):
            self._keep(olds, _("{slot} deleted"))
            if n == 0:
                self._set_note(_("Clearing cancelled – nothing was changed on the FM-1."))
                self.read_list()
                return
            detail = _(": {slot} “{name}”").format(slot=f"U{ds[0] + 1:03d}", name=names[0]) if n == 1 else ""
            stopped = _(" ({n} not any more after cancelling)").format(n=len(ds) - n) if n < len(ds) else ""
            self._set_note(ngettext("{n} slot cleared{detail}{stopped}. “Undo” brings the content back.",
                                    "{n} slots cleared{detail}{stopped}. “Undo” brings the content back.", n).format(
                n=n, detail=detail, stopped=stopped))
            self.read_list()
        self._job(_("Delete"), work, done, partial=lambda: self._keep(olds, _("{slot} deleted")), cancellable=len(ds) > 1)

    def toggle_fav(self):
        ds = [k for k in self.sel_slots() if self.slots[k]["used"]]
        if len(ds) != 1:
            return
        k = ds[0]
        on = not self.slots[k]["favorite"]

        def done(rc):
            self.slots[k]["favorite"] = on
            label, name = f"U{k + 1:03d}", self.slots[k]['name']
            what = (_("{slot} “{name}” is now a favourite ★").format(slot=label, name=name) if on
                    else _("{slot} “{name}” is no longer a favourite").format(slot=label, name=name))
            self.state.setText(what + {0: _(" – saved."), 3: _(" – but only until switching off (no flash)."),
                                       4: _(" – the FM-1 saves it after STOP.")}.get(rc, "."))
            self.fill()
            self.select_slot(k)
        self._job(_("Favourite"), lambda w, bank: bank.set_favorite(k, on), done)

    def move_slot(self):
        ds = [k for k in self.sel_slots() if self.slots[k]["used"]]
        if len(ds) != 1:
            return
        a = ds[0]
        b, ok = QInputDialog.getInt(self, _("Move/swap"), _("Target slot for {slot} (1–384):\n"
                                                           "If the target is used, the two slots are swapped.").format(
            slot=self._slot_text(a)), a + 2, 1, len(self.slots))
        b -= 1
        if not ok or b == a:
            return
        swap = self.slots[b]["used"]
        if not confirm(self, _("Move/swap"), (_("Swap {a} and {b}?\n\nBoth old contents are read first (undo possible).").format(
                           a=self._slot_text(a), b=self._slot_text(b)) if swap
                       else _("Move {a} to {slot}?\n\nBoth old contents are read first (undo possible).").format(
                           a=self._slot_text(a), slot=f"U{b + 1:03d}")), _("Execute")):
            return

        olds: list = []

        def work(w, bank):
            pa, pb = bank.get(a), bank.get(b)
            olds.extend([(a, pa), (b, pb)])
            bank.put(b, pa, defaults=False)
            if pb.used:
                bank.put(a, pb, defaults=False)
            else:
                bank.erase(a)
            return True

        ta, tb = self._slot_text(a), self._slot_text(b)

        def done(_res):
            self._keep(olds, _("{slot} changed"))
            self._set_note(_("Swapped: {a} ↔ {b}.").format(a=ta, b=tb) if swap else
                           _("Moved: {a} → {target} ({source} is empty now).").format(a=ta, target=f"U{b + 1:03d}", source=f"U{a + 1:03d}"))
            self.read_list()
        self._job(_("Move"), work, done, partial=lambda: self._keep(olds, _("{slot} changed")))

    def load_track(self):
        ds = [k for k in self.sel_slots() if self.slots[k]["used"]]
        if len(ds) != 1:
            return
        k, tr = ds[0], self.track.value()
        self._job(_("Listen"), lambda w, bank: bank.load(k, tr - 1),
                  lambda _res: self.state.setText(_("{slot} is now on track {track} – play a few notes "
                                                    "on the FM-1. Nothing was saved.").format(slot=self._slot_text(k), track=tr)))

    def store_track(self):
        ds = self.sel_slots()
        if len(ds) != 1:
            return
        k, tr = ds[0], self.track.value()
        name, ok = QInputDialog.getText(self, _("Save track"), _("Name (empty = automatic), sound of track {track} "
                                                                 "to {slot}:").format(track=tr, slot=f"U{k + 1:03d}"), text="")
        if not ok:
            return
        name, why = pl.fit_name(name, 20)
        if why and not confirm(self, _("Save track"), _("{reason}\n\nTake it like this?").format(reason=why), _("Accept")):
            return
        if self.slots[k]["used"] and not confirm(self, _("Overwrite"), _("{slot} will be overwritten. "
                                                                         "The old content is read first (undo possible).").format(
                                                     slot=self._slot_text(k)),
                                                 _("Overwrite"), danger=True):
            return

        olds: list = []

        def done(_res):
            self._keep(olds, _("{slot} saved"))
            label = f"U{k + 1:03d}"
            self._set_note(_("Sound of track {track} saved in {slot} as “{name}”. “Undo” restores the old content.").format(
                track=tr, slot=label, name=name) if name else
                _("Sound of track {track} saved in {slot}. “Undo” restores the old content.").format(track=tr, slot=label))
            self.read_list()

        def work(w, bank):
            olds.append((k, bank.get(k)))
            bank.store(k, tr - 1, name)
        self._job(_("Save track"), work, done, partial=lambda: self._keep(olds, _("{slot} saved")))

    # ------------------------------------------------------------ Comparison
    def compare(self):
        ds, ls = [k for k in self.sel_slots() if self.slots[k]["used"]], self.sel_lib()
        if len(ds) != 1 or len(ls) != 1:
            return
        k, lp = ds[0], ls[0]

        def done(dp):
            self.cache[k] = dp
            lines = pl.diff(dp, lp)
            self.compare_result = lines
            from PyQt6.QtWidgets import QMessageBox
            box = QMessageBox(QMessageBox.Icon.Information, _("Comparison"),
                              _("{slot} (FM-1) ↔ “{name}” (library):\n\n{lines}").format(
                                  slot=f"U{k + 1:03d}", name=lp.name, lines="\n".join(lines)), parent=self)
            box.exec()
            self.fill()
        self._job(_("Compare"), lambda w, bank: bank.get(k), done)

    # ------------------------------------------------------------ Library
    def edit_tags(self):
        ls = self.sel_lib()
        if not ls:
            return
        cur = ", ".join(sorted({t for p in ls for t in p.tags}))
        text, ok = QInputDialog.getText(self, _("Labels"), _("Tags (separated by commas):"), text=cur)
        if not ok:
            return
        tags = [t.strip() for t in text.split(",") if t.strip()]
        lib = self.library()
        try:
            for p in ls:
                p.tags = tags
                lib.update(p)
        except OSError as e:
            show_error(self, _("Labels"), fs_error_text(e, _("Tags not saved")))
        else:
            self.state.setText(ngettext("Tags saved for {n} preset (library only, nothing changed on the FM-1).",
                                        "Tags saved for {n} presets (library only, nothing changed on the FM-1).", len(ls)).format(n=len(ls)))
        self.refresh_library()

    def import_files(self, files=None):
        if not files:
            files, _filter = QFileDialog.getOpenFileNames(self, _("Import presets"), str(Path.home()),
                                                          _("Presets (*.fm1preset *.zip *.json *.syx);;All files (*)"))
        if not files:
            return
        n, errs = 0, []
        lib = self.library()
        known = len(lib.hashes())
        for f in files:
            try:
                n += len(lib.import_file(Path(f)))
            except OSError as e:
                errs.append(f"{Path(f).name}: {fs_error_text(e)}")
            except pl.PresetError as e:
                errs.append(f"{Path(f).name}: {e}")
            except Exception as e:                 # noqa: BLE001 – unexpected, but no crash
                errs.append(_("{name}: file not readable ({error})").format(name=Path(f).name, error=type(e).__name__))
        new = len(lib.hashes()) - known
        text = (ngettext("{n} preset imported, {new} of them new in the library (folder Presets/Importiert); {old} existed already.",
                         "{n} presets imported, {new} of them new in the library (folder Presets/Importiert); {old} existed already.",
                         n).format(n=n, new=new, old=n - new) if n else _("Nothing imported."))
        if errs:
            text += _(" Not readable: {list}").format(list="; ".join(errs))
            show_error(self, _("Import"), ngettext("{n} file could not be read:\n\n{list}\n\nThe library was not changed by this; readable files are taken over.",
                                                   "{n} files could not be read:\n\n{list}\n\nThe library was not changed by this; readable files are taken over.",
                                                   len(errs)).format(n=len(errs), list="\n".join(errs)))
        self.state.setText(text)
        self.refresh_library()

    def lib_rename(self):
        ls = self.sel_lib()
        if len(ls) != 1:
            return
        p = ls[0]
        name = self.ask_name(_("Rename"), _("New name for “{name}” in the library\n(at most 20 characters; "
                                            "only the file changes, nothing on the FM-1):").format(name=p.name), p.name, 20)
        if not name or name == p.name:
            return
        old = p.name
        try:
            f = self.library().rename(p, name)
        except (OSError, pl.PresetError) as e:
            show_error(self, _("Rename"), fs_error_text(e, _("Not renamed")) if isinstance(e, OSError) else str(e))
            return
        self.state.setText(_("Renamed in the library: “{old}” → “{new}” ({file}). Nothing was changed on the FM-1.").format(
            old=old, new=name, file=f.name))
        self.refresh_library()

    def export(self, target=None):
        ls = self.sel_lib()
        if not ls:
            return
        if target is None:
            target, flt = QFileDialog.getSaveFileName(
                self, _("Export"), str(Path.home() / ("Presets.zip" if len(ls) > 1 else pl.safe_file(ls[0].name) + ".fm1preset")),
                _("Set (*.zip);;Single (*.fm1preset);;Melodee web editor (*.json)"))
        if not target:
            return
        t = Path(target)
        try:
            if t.suffix == ".json":
                t.write_text(json.dumps(pl.to_felucca(ls, self.caps.engines if self.caps else []), indent=1), "utf-8")
            elif t.suffix == ".fm1preset" and len(ls) == 1:
                t.write_text(json.dumps(ls[0].to_json(), indent=1, ensure_ascii=False), "utf-8")
            else:
                t = t.with_suffix(".zip")      # several presets only fit in one sentence
                pl.export_zip(ls, t)
        except OSError as e:
            show_error(self, pgettext("error title", "Export"), fs_error_text(e, _("Not exported")))
            return
        self.state.setText(ngettext("{n} preset exported: {file} – only as a file, nothing changed on the FM-1.",
                                    "{n} presets exported: {file} – only as a file, nothing changed on the FM-1.", len(ls)).format(
            n=len(ls), file=t.name))
        notify(ngettext("{n} preset exported: {file}", "{n} presets exported: {file}", len(ls)).format(n=len(ls), file=t.name), "success")

    def lib_delete(self):
        ls = self.sel_lib()
        names = "\n".join(_("“{name}” ({device}{slot})").format(name=p.name, device=p.device or _("without device"),
                                                                  slot=', ' + p.label if p.slot is not None else '') for p in ls[:12])
        more = _("\n… and {n} more").format(n=len(ls) - 12) if len(ls) > 12 else ""
        if ls and confirm(self, _("Remove"), ngettext("Delete {n} preset from the library?\n\n{names}{more}\n\nThe files are deleted; the FM-1 stays unchanged.",
                                                      "Delete {n} presets from the library?\n\n{names}{more}\n\nThe files are deleted; the FM-1 stays unchanged.",
                                                      len(ls)).format(n=len(ls), names=names, more=more), _("Delete"), danger=True):
            lib = self.library()
            try:
                for p in ls:
                    lib.delete(p)
            except OSError as e:
                show_error(self, _("Remove"), fs_error_text(e, _("Not deleted")))
            else:
                self.state.setText(ngettext("{n} preset removed from the library – the FM-1 stays unchanged.",
                                            "{n} presets removed from the library – the FM-1 stays unchanged.", len(ls)).format(n=len(ls)))
            self.refresh_library()

    def open_folder(self):
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.root)))


def _make(ctx, window):
    return PresetsPage(ctx, window)


PAGE = PageSpec(key="presets", title=_("Presets"), group="klang", factory=_make,
                icons=("bookmarks", "folder-favorites", "document-open-recent"), order=10,
                description=_("back up, compare, restore and listen to the FM-1 user presets"),
                keywords=("user", "preset", "bibliothek", "library", "favoriten", "melodee", "favorites", "compare",
                          "restore"), needs_device=True)
