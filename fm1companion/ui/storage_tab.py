# SPDX-License-Identifier: GPL-3.0-only
"""Settings › Storage: what the app keeps on the computer, “Delete …” per category and the automatic cleanup."""
from __future__ import annotations

import time
from pathlib import Path

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (QCheckBox, QDialog, QFormLayout, QGroupBox, QHBoxLayout, QInputDialog, QListWidget,
                             QListWidgetItem, QPushButton, QSpinBox, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                             QWidget)

from .. import storage
from ..i18n import N_, _, ngettext
from .common import confirm, icon, label, run_job, show_error
from .notify import notify

KIND_TEXT = {"data": N_("your data"), "keep": N_("kept")}
DATA_ACTION = {"studio": N_("Delete a state …"), "rescuedumps": N_("Show and delete dumps …"),
               "webdata": N_("Clear web editor data …")}


class StorageTab(QWidget):
    def __init__(self, ctx=None, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.cats: list[storage.Category] = []
        self.scanning = False
        lay = QVBoxLayout(self)
        lay.addWidget(label(_("What the app keeps on this computer. “Delete …” only removes data the app made "
                              "itself and can fetch or create again – never your backups, projects, samples or "
                              "recordings, and nothing an interrupted installation still needs."), "muted"))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([_("Category"), _("Size"), _("Can be deleted")])
        self.tree.setRootIsDecorated(False)
        self.tree.currentItemChanged.connect(lambda *_a: self._selected())
        lay.addWidget(self.tree, 1)
        self.detail = label("", "small")
        lay.addWidget(self.detail)
        row = QHBoxLayout()
        self.b_open = QPushButton(icon("folder-open"), _("Open folder"))
        self.b_open.clicked.connect(self.open_folder)
        self.b_delete = QPushButton(icon("edit-delete"), _("Delete …"))
        self.b_delete.clicked.connect(self.delete_current)
        self.b_rescan = QPushButton(icon("view-refresh"), _("Measure again"))
        self.b_rescan.clicked.connect(self.refresh)
        for b in (self.b_open, self.b_delete, self.b_rescan):
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        self.total = label("", "")
        lay.addWidget(self.total)

        cfg = storage.settings()
        box = QGroupBox(_("Automatic cleanup"))
        f = QFormLayout(box)
        self.auto = QCheckBox(_("clean up automatically (at most once a day, a minute after the start)"))
        self.auto.setChecked(bool(cfg.get("auto", True)))
        f.addRow(self.auto)

        def spin(key, lo, hi, suffix, off_text):
            s = QSpinBox()
            s.setRange(lo, hi)
            s.setSuffix(suffix)
            s.setSpecialValueText(off_text)
            s.setValue(int(cfg.get(key, storage.DEFAULTS[key])))
            return s
        never = _("never")
        keep_all = _("keep all")
        self.log_days = spin("log_days", 0, 3650, _(" days"), never)
        self.cache_days = spin("cache_days", 0, 3650, _(" days"), never)
        self.fw_keep = spin("firmware_keep", 0, 50, "", keep_all)
        self.pk_keep = spin("packages_keep", 0, 50, "", keep_all)
        self.ed_keep = spin("editor_keep", 0, 50, "", keep_all)
        f.addRow(_("Delete older log parts after:"), self.log_days)
        f.addRow(_("Delete catalog images, sample pack downloads and temporary files after:"), self.cache_days)
        f.addRow(_("Downloaded versions to keep per firmware:"), self.fw_keep)
        self.fw_keep.setToolTip(_("The newest versions stay, as well as the versions on your devices and the "
                                  "package of an interrupted installation."))
        f.addRow(_("Copies of installed packages to keep:"), self.pk_keep)
        f.addRow(_("Editor versions to keep per firmware:"), self.ed_keep)
        self.b_now = QPushButton(icon("edit-clear-all", "edit-clear"), _("Clean up now"))
        self.b_now.setToolTip(_("Applies the rules above right away."))
        self.b_now.clicked.connect(self.clean_now)
        f.addRow("", self.b_now)
        lay.addWidget(box)
        self._selected()

    # ------------------------------------------------------------ measuring
    def refresh(self):
        if self.scanning:
            return
        self.scanning = True
        self.b_rescan.setEnabled(False)
        self.total.setText(_("Measuring …"))
        run_job(self, lambda w: storage.scan(), on_done=self._show,
                on_fail=lambda m, tb: (setattr(self, "scanning", False), self.b_rescan.setEnabled(True),
                                       self.total.setText(_("Measuring failed: {error}").format(error=m))))

    def _show(self, cats):
        self.scanning = False
        self.b_rescan.setEnabled(True)
        cur = self._cur()
        self.cats = cats
        self.tree.clear()
        for c in cats:
            if c.deletable:
                can = storage.human(c.cleanable) if c.cleanable else "–"
            else:
                can = _(KIND_TEXT[c.kind])
            it = QTreeWidgetItem([_(c.title), storage.human(c.size), can])
            it.setData(0, Qt.ItemDataRole.UserRole, c.key)
            it.setToolTip(0, _(c.detail))
            for col in (1, 2):
                it.setTextAlignment(col, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.tree.addTopLevelItem(it)
            if cur is not None and c.key == cur.key:
                self.tree.setCurrentItem(it)
        if self.tree.currentItem() is None and self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
        self.tree.resizeColumnToContents(0)
        total, cleanable = storage.app_total(cats)
        own = next((c.size for c in cats if c.key == "userdata"), 0)
        self.total.setText(_("App data: <b>{size}</b>, of it <b>{free}</b> can be deleted. Your own files in the "
                             "main folder: {own}.").format(size=storage.human(total), free=storage.human(cleanable),
                                                          own=storage.human(own)))
        self._selected()

    def _cur(self) -> storage.Category | None:
        it = self.tree.currentItem()
        key = it.data(0, Qt.ItemDataRole.UserRole) if it else None
        return next((c for c in self.cats if c.key == key), None)

    def select(self, key: str) -> bool:
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            if it.data(0, Qt.ItemDataRole.UserRole) == key:
                self.tree.setCurrentItem(it)
                return True
        return False

    def _selected(self):
        c = self._cur()
        text = _(c.detail) if c else ""
        if c is not None and c.key == "webdata" and storage.webdata_clear_pending():
            text += " " + _("Clearing is pending – it happens when the app starts next time.")
        self.detail.setText(text)
        self.b_open.setEnabled(bool(c and c.folder is not None))
        if c is not None and c.kind == "data":
            self.b_delete.setText(_(DATA_ACTION.get(c.key, N_("Delete …"))))
            self.b_delete.setEnabled(c.size > 0)
        else:
            self.b_delete.setText(_("Delete …"))
            self.b_delete.setEnabled(bool(c and c.deletable and c.cleanable > 0))

    # ------------------------------------------------------------ actions
    def open_folder(self):
        c = self._cur()
        if c is None or c.folder is None:
            return
        d = c.folder
        while not d.exists() and d.parent != d:     # not created yet: open the nearest existing folder
            d = d.parent
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(d)))

    def delete_current(self) -> bool:
        c = self._cur()
        if c is None:
            return False
        if c.key == "rescuedumps":
            return self.rescue_dialog()
        if c.key == "webdata":
            return self.clear_webdata()
        if c.kind == "data":
            return self.delete_studio_state()
        if not c.deletable or not c.cleanable:
            return False
        if storage.install_running():
            show_error(self, _("Not now"), _("An installation is running – nothing is deleted until it has finished."))
            return False
        if not confirm(self, _("Delete {what}").format(what=_(c.title)),
                       _("Delete <b>{what}</b> ({size})?<br><br>{detail}").format(
                           what=_(c.title), size=storage.human(c.cleanable), detail=_(c.detail)),
                       _("Delete"), danger=True):
            return False
        if c.key == "webengine" and self._clear_web_cache():
            return True
        title = _(c.title)
        run_job(self, lambda w: storage.clear(c.key), on_done=lambda r: self._cleared(r, title),
                on_fail=lambda m, tb: show_error(self, _("Not deleted"), m))
        return True

    def _clear_web_cache(self) -> bool:
        """the built-in browser is running: let it empty its cache itself (deleting under it breaks pages)"""
        from . import web
        prof = getattr(web, "_profile", None)
        if prof is None:
            return False
        prof.clearHttpCache()
        notify(_("Web cache is being emptied."), "success")
        self.refresh()
        return True

    def _cleared(self, res: storage.Result, title: str):
        text = _("{what}: {size} freed.").format(what=title, size=storage.human(res.freed))
        if res.errors:
            text += " " + ngettext("{n} item could not be deleted: {first}",
                                   "{n} items could not be deleted: {first}", len(res.errors)).format(
                n=len(res.errors), first=res.errors[0])
        notify(text, "warning" if res.errors else "success")
        self.last_result = res
        self.refresh()

    def delete_studio_state(self) -> bool:
        states = storage.studio_states()
        if not states:
            return False
        known = set()
        store = getattr(self.ctx, "store", None)
        for p in (store.profiles.values() if store is not None else []):
            known.add(p.folder_name)
        names = []
        for folder, _path, size, mtime in states:
            gone = "" if folder in known else " " + _("(device forgotten)")
            names.append(_("{name} – {size}, {date}").format(name=folder, size=storage.human(size),
                                                              date=time.strftime(_("%Y-%m-%d %H:%M"),
                                                                                 time.localtime(mtime))) + gone)
        choice, ok = QInputDialog.getItem(self, _("Delete a Studio state"), _("Studio state:"), names, 0, False)
        if not ok or choice not in names:
            return False
        folder = states[names.index(choice)][0]
        service = getattr(getattr(self.ctx, "window", None), "studio", None)
        if storage.studio_state_in_use(folder, service):
            show_error(self, _("Not now"), _("The Studio is working on the state of <b>{name}</b> right now – it "
                                             "would be saved again at once. Open another state in the Studio first "
                                             "(or quit the app and delete it at the next start).").format(name=folder))
            return False
        if not confirm(self, _("Delete a Studio state"),
                       _("Delete the Studio state of <b>{name}</b>? Your work in the Studio for this device is "
                         "lost; backups and the FM-1 itself are not affected.").format(name=folder),
                       _("Delete"), danger=True):
            return False
        try:
            freed = storage.delete_studio_state(folder)
        except OSError as e:
            show_error(self, _("Not deleted"), str(e))
            return False
        notify(_("Studio state of {name} deleted ({size}).").format(name=folder, size=storage.human(freed)),
               "success")
        self.refresh()
        return True

    # ------------------------------------------------------------ web editor data
    def clear_webdata(self) -> bool:
        if storage.install_running():
            show_error(self, _("Not now"), _("An installation is running – nothing is deleted until it has finished."))
            return False
        if not confirm(self, _("Clear web editor data"),
                       _("Clear the saved data of the built-in browser (cookies, local storage, databases of the web "
                         "pages)?<br><br>Web editors and the simulator may keep <b>your settings</b> there (e.g. "
                         "options of an editor, remembered choices) – they are lost and the pages start as on the "
                         "first visit. Not affected: your backups, projects and samples, the FM-1 itself, and the "
                         "app's own settings."), _("Clear"), danger=True):
            return False
        from . import web
        try:
            now = web.clear_site_data()
        except OSError as e:
            show_error(self, _("Not deleted"), str(e))
            return False
        notify(_("Web editor data cleared.") if now else
               _("Web editor data: cookies cleared now, the rest when the app starts next time (the built-in "
                 "browser has it open)."), "success")
        self.refresh()
        return True

    # ------------------------------------------------------------ rescue folder
    def rescue_dialog(self) -> bool:
        d = RescueDumpsDialog(self)
        d.exec()
        self.refresh()
        return d.deleted > 0

    def values(self) -> dict:
        return {"auto": self.auto.isChecked(), "log_days": self.log_days.value(),
                "cache_days": self.cache_days.value(), "firmware_keep": self.fw_keep.value(),
                "packages_keep": self.pk_keep.value(), "editor_keep": self.ed_keep.value()}

    def apply(self):
        storage.settings().update(self.values())

    def load_values(self):
        """show the saved cleanup settings again (after “Reset settings of …”)"""
        cfg = {**storage.DEFAULTS, **storage.settings()}
        self.auto.setChecked(bool(cfg["auto"]))
        for w, key in ((self.log_days, "log_days"), (self.cache_days, "cache_days"), (self.fw_keep, "firmware_keep"),
                       (self.pk_keep, "packages_keep"), (self.ed_keep, "editor_keep")):
            w.setValue(int(cfg[key]))

    def clean_now(self) -> bool:
        if storage.install_running():
            show_error(self, _("Not now"), _("An installation is running – nothing is deleted until it has finished."))
            return False
        vals = self.values()
        self.b_now.setEnabled(False)

        def done(res):
            self.b_now.setEnabled(True)
            self._cleared(res, _("Cleanup"))
        run_job(self, lambda w: storage.auto_clean(vals), on_done=done,
                on_fail=lambda m, tb: (self.b_now.setEnabled(True), show_error(self, _("Not deleted"), m)))
        return True



def rescue_item_text(it: storage.RescueItem) -> str:
    what = {"session": _("rescue run"), "dump": _("flash dump"), "logs": _("tool logs")}.get(it.kind, it.kind)
    tags = []
    if it.full:
        tags.append(_("full backup"))
    if it.simulated:
        tags.append(_("simulation/example"))
    if it.newest:
        tags.append(_("NEWEST of this device"))
    return _("{name} – {what}, {size}, {date}").format(
        name=it.name, what=what, size=storage.human(it.size),
        date=time.strftime(_("%Y-%m-%d %H:%M"), time.localtime(it.mtime))) + (
        " · " + ", ".join(tags) if tags else "")


class RescueDumpsDialog(QDialog):
    """The rescue folder's dumps and tool logs: list, open the folder, delete ONE at a time (always asked)."""

    def __init__(self, parent=None, base=None):
        super().__init__(parent)
        self.base = base
        self.deleted = 0
        self.setWindowTitle(_("Rescue dumps and tool logs"))
        self.resize(720, 420)
        lay = QVBoxLayout(self)
        lay.addWidget(label(_("Full flash backups are your safety net: with one, a damaged FM-1 can be brought back "
                              "exactly as it was. Delete only what you no longer need – one at a time. The newest "
                              "full backup of each device is marked and needs an extra confirmation."), "muted"))
        self.list = QListWidget()
        lay.addWidget(self.list, 1)
        self.info = label("", "small")
        lay.addWidget(self.info)
        row = QHBoxLayout()
        self.b_open = QPushButton(icon("folder-open"), _("Open folder"))
        self.b_open.clicked.connect(self._open)
        self.b_del = QPushButton(icon("edit-delete"), _("Delete …"))
        self.b_del.setProperty("variant", "danger")
        self.b_del.clicked.connect(self.delete_current)
        close = QPushButton(_("Close"))
        close.clicked.connect(self.accept)
        for b in (self.b_open, self.b_del):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(close)
        lay.addLayout(row)
        self.list.currentRowChanged.connect(lambda *_a: self.b_del.setEnabled(self.list.currentRow() >= 0))
        self.fill()

    def fill(self):
        self.items = storage.rescue_items(self.base)
        self.list.clear()
        for it in self.items:
            li = QListWidgetItem(icon("security-high" if it.newest else ("media-flash" if it.full else
                                                                          "text-x-generic"), "document"),
                                 rescue_item_text(it))
            self.list.addItem(li)
        total = sum(i.size for i in self.items)
        self.info.setText(ngettext("{n} item, {size} together.", "{n} items, {size} together.",
                                   len(self.items)).format(n=len(self.items), size=storage.human(total))
                          if self.items else _("The rescue folder holds no dumps or tool logs."))
        if self.items:
            self.list.setCurrentRow(0)
        self.b_del.setEnabled(bool(self.items))

    def _open(self):
        d = Path(self.base) if self.base else storage.rescue_dir()
        while not d.exists() and d.parent != d:
            d = d.parent
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(d)))

    def delete_current(self) -> bool:
        row = self.list.currentRow()
        if not 0 <= row < len(self.items):
            return False
        it = self.items[row]
        text = _("Delete <b>{name}</b> ({size}) for good?").format(name=it.name, size=storage.human(it.size))
        if it.full:
            text += "<br><br>" + _("It holds a complete backup of an FM-1's flash. Without it, that state of the "
                                   "device cannot be restored any more.")
        if not confirm(self, _("Delete rescue data"), text, _("Delete"), danger=True):
            return False
        if it.newest and not confirm(
                self, _("Delete the newest full backup?"),
                _("<b>{name}</b> is the newest (or the only) full flash backup of this device. If the FM-1 ever "
                  "needs a rescue, this is the file that brings it back.<br><br>Make a new full backup on the "
                  "Rescue page first, or keep a copy elsewhere. Really delete it?").format(name=it.name),
                _("Delete anyway"), danger=True):
            return False
        try:
            freed = storage.delete_rescue_item(it.path, self.base)
        except storage.Busy as e:
            show_error(self, _("Not now"), str(e))
            return False
        except OSError as e:
            show_error(self, _("Not deleted"), e.strerror or str(e))
            return False
        self.deleted += 1
        notify(_("{name} deleted ({size}).").format(name=it.name, size=storage.human(freed)), "success")
        self.fill()
        return True
