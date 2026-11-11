# SPDX-License-Identifier: GPL-3.0-only
"""Settings › Storage: what the app keeps on the computer, “Delete …” per category and the automatic cleanup."""
from __future__ import annotations

import time

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (QCheckBox, QFormLayout, QGroupBox, QHBoxLayout, QInputDialog, QPushButton, QSpinBox,
                             QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import storage
from ..i18n import N_, _, ngettext
from .common import confirm, icon, label, run_job, show_error
from .notify import notify

KIND_TEXT = {"data": N_("your data"), "keep": N_("kept")}


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
        self.detail.setText(_(c.detail) if c else "")
        self.b_open.setEnabled(bool(c and c.folder is not None))
        if c is not None and c.kind == "data":
            self.b_delete.setText(_("Delete a state …"))
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

    def values(self) -> dict:
        return {"auto": self.auto.isChecked(), "log_days": self.log_days.value(),
                "cache_days": self.cache_days.value(), "firmware_keep": self.fw_keep.value(),
                "packages_keep": self.pk_keep.value(), "editor_keep": self.ed_keep.value()}

    def apply(self):
        storage.settings().update(self.values())

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

