# SPDX-License-Identifier: GPL-3.0-only
"""Backups: anlegen, ansehen, zurückspielen, aufräumen – pro Gerät. Dazu automatische Backups."""
from __future__ import annotations

import datetime as dt
import json
import shutil
import time
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, QUrl, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices
from PyQt6.QtWidgets import (QCheckBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel,
                             QLineEdit, QPushButton, QScrollArea, QSizePolicy, QSpinBox, QTreeWidget, QTreeWidgetItem,
                             QVBoxLayout, QWidget)

from .. import autobackup as AB, backup, config, projects
from ..i18n import N_, _, ngettext, pgettext
from .common import confirm, fmt_date, fmt_size, fw_label, heading, icon, show_error
from .responsive import reflow

SORT = Qt.ItemDataRole.UserRole + 7


class _Row(QTreeWidgetItem):
    """Zeile der Backup-Liste: Datum und Größe sortieren nach Wert, nicht nach Text."""

    def __lt__(self, other):
        col = self.treeWidget().sortColumn() if self.treeWidget() else 0
        a, b = self.data(col, SORT), other.data(col, SORT)
        if a is not None and b is not None:
            return a < b
        return super().__lt__(other)

backup_filename = AB.backup_filename          # (firmware_page importiert es von hier)
# backup kinds (the keys are stored in the backup files) -> display text, translated where used: _(KIND_TEXT[k])
KIND_TEXT = {"manuell": N_("manual"), "auto": N_("automatic"), "vor-Update": N_("before firmware change"),
             "sicherheit": N_("safety")}


def no_backup_reason(ctx) -> str:
    """warum gerade kein Backup geht ("" = es geht)"""
    if ctx.connected:
        return ""
    if ctx.port is None:
        return _("No FM-1 connected. Please connect an FM-1 via USB.")
    if getattr(ctx, "protocol", "") == "update":
        return _("The FM-1 is in update mode – finish the installation first (page “Rescue”), then back up.")
    if getattr(ctx, "midi_connected", False):
        from .device import firmware_name
        fw = firmware_name(ctx.firmware_key) or _("The running firmware")
        return _("{firmware} has no editor protocol – the app cannot make a backup of it. Backups are available "
                 "for Melodee, Skiff and SLOOP. Please back up important projects with the firmware's own tool."
                 ).format(firmware=fw)
    return _("The FM-1 does not respond. Check the cable and reconnect.")


def make_backup(ctx, parent, note: str = "", kind: str = "") -> Path | None:
    from .device import FolderError
    why = no_backup_reason(ctx)
    if why:
        show_error(parent, _("Backup not possible"), why)
        return None
    prof = ctx.profile
    try:
        folder = ctx.device_folder(ctx.settings.backups_dir)
    except OSError as e:
        show_error(parent, _("Backup not possible"), str(e) if isinstance(e, FolderError) else
                   _("The backup folder cannot be created: {error}").format(error=e.strerror or e))
        return None
    version = ctx.info.version if ctx.info else ""
    fw = ctx.firmware_key
    akind = {"auto": "sicherheit", "": "manuell"}.get(kind, kind)

    def work(w, link):
        w.log.emit(_("Reading the inventory …"))
        try:
            path = AB.capture_to_file(link, folder, prof, fw, version, note, akind,
                                      progress=lambda a, b: w.progress.emit(a, b),
                                      abort=lambda: bool(getattr(w, "cancelled", False)))
        except backup.BackupError as e:
            if getattr(e, "cancelled", False):
                raise backup.BackupError(_("Cancelled – no file was saved, nothing was changed on the FM-1.")) from e
            raise
        except OSError as e:
            from .. import firmware as _fw
            w.log.emit(_("Technical details: ") + _fw.explain(e))
            raise RuntimeError(_("The backup was read but not saved: {error}").format(error=_fw.friendly_error(e))) from e
        w.log.emit(_("Saved: {size} – firmware {firmware} {version}").format(
            size=fmt_size(path.stat().st_size), firmware=fw or '?', version=version))
        w.log.emit(_("File: {path}").format(path=path))
        try:
            prune(folder, ctx.settings.keep_backups)
        except OSError as e:
            w.log.emit(_("Old automatic backups could not be deleted: {error}").format(error=e.strerror or e))
        _mirror_quiet(ctx, path, w.log.emit)
        return path

    def said(path) -> str:
        m = AB.read_meta(path)
        size = fmt_size(Path(path).stat().st_size)
        if m:
            return _("Backup saved: {name} ({parts} parts, {projects} projects, {size}). Nothing was changed on "
                     "the FM-1.").format(name=Path(path).name, parts=m.objects, projects=m.projects, size=size)
        return _("Backup saved: {name} ({size}). Nothing was changed on the FM-1.").format(name=Path(path).name,
                                                                                          size=size)
    from .feedback import open_folder_action
    d = ctx.job(parent, _("Backup"), work, done=said, actions=[open_folder_action(lambda p: Path(p).parent)],
                cancellable=(kind != "auto"))
    return d.result_value if d.ok else None


def _mirror_quiet(ctx, path: Path, log=None):
    target = AB.settings().get("mirror")
    if not target:
        return
    try:
        AB.mirror_file(path, ctx.settings.backups_dir, Path(target))
        AB.STATUS.mirror = _("Mirror up to date ({time})").format(time=time.strftime('%H:%M'))
        if log:
            log(_("Mirrored and verified: {target}").format(target=target))
    except OSError as e:
        AB.STATUS.mirror = _("Mirror not possible: {error}").format(error=e)
        if log:
            log(AB.STATUS.mirror)


def prune(folder: Path, keep: int):
    """alte automatische Backups löschen: die letzten `keep`, eins pro Monat, das neueste je Firmware und
    alle angehefteten bleiben; manuelle Backups löscht die App nie."""
    if keep <= 0:
        return
    AB.prune(folder, keep)


# ------------------------------------------------------- Automatik ---

class AutoBackupService(QObject):
    """automatische Backups beim Verbinden und (optional) regelmäßig – nie während anderer Zugriffe."""
    updated = pyqtSignal()

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.running = False
        self._seen = ""
        self._retries = 0
        self.connect_timer = QTimer(self)
        self.connect_timer.setSingleShot(True)
        self.connect_timer.timeout.connect(lambda: self.run(_("on connect")))
        self.tick = QTimer(self)
        self.tick.timeout.connect(self._periodic)
        self.tick.start(60_000)
        ctx.changed.connect(self._changed)

    def _changed(self):
        c = self.ctx
        key = f"{c.port.key if c.port else ''}|{c.profile.id if c.profile else ''}" if c.connected else ""
        if getattr(c, "is_virtual", False):
            key = ""                           # der Studio-Zwilling: keine automatischen Backups (sein Stand liegt im Studio)
        if key and key != self._seen:
            self._retries = 0
            if AB.settings()["on_connect"]:
                self.connect_timer.start(int(AB.settings()["delay_s"] * 1000))
        if not key:
            self.connect_timer.stop()
        self._seen = key

    def _periodic(self):
        s = AB.settings()
        if s["periodic"] and self.ctx.connected and self.ctx.profile:
            try:
                folder = self.ctx.device_folder(self.ctx.settings.backups_dir)
            except OSError as e:
                AB.STATUS.add(_("Automatic backup not possible: {error}").format(error=e))
                return
            if AB.needs_backup(folder, s["hours"] / 24):
                self.run(_("periodic"), force=True)

    def busy_reason(self) -> str:
        c = self.ctx
        if not c.connected or not c.profile:
            return _("no device")
        if getattr(c, "is_virtual", False):
            return _("Studio twin (not a real FM-1)")
        if c.hold:
            return _("firmware installation in progress")
        if self.running:
            return _("already running")
        if c.link._exclusive_depth:
            return _("device in use right now")
        win = getattr(c, "window", None)
        if win is not None:
            try:
                cur = win.stack.currentWidget()
                key = next((k for k, p in win.pages.items() if p is cur), "")
                if key in ("editor", "recorder"):
                    return _("editor/recording open")
                rec = getattr(getattr(win, "recorder", None), "rec", None)
                if rec is not None and rec.running:
                    return _("recording in progress")
            except Exception:                      # noqa: BLE001
                pass
        return ""

    def run(self, reason: str = "", force: bool = False) -> bool:
        reason = reason or _("on connect")
        c = self.ctx
        if not c.connected or not c.profile or getattr(c, "is_virtual", False):
            return False
        try:
            folder = c.device_folder(c.settings.backups_dir)
        except OSError as e:
            AB.STATUS.add(_("Automatic backup not possible: {error}").format(error=e))
            c.message.emit(_("Automatic backup not possible: {error}").format(error=e))
            self.updated.emit()
            return False
        s = AB.settings()
        if not force and not AB.needs_backup(folder, s["days"]):
            AB.STATUS.add(ngettext("{device}: the last backup is up to date (younger than {n} day).",
                                   "{device}: the last backup is up to date (younger than {n} days).",
                                   int(s['days'])).format(device=c.profile.name, n=s['days']))
            self.updated.emit()
            return False
        why = self.busy_reason()
        if why:
            if self._retries < 30:
                self._retries += 1
                QTimer.singleShot(60_000, lambda: self.run(reason, force))
            AB.STATUS.add(_("Automatic backup is waiting ({reason}).").format(reason=why))
            self.updated.emit()
            return False
        self.running = True
        prof, fw, version = c.profile, c.firmware_key, c.info.version if c.info else ""
        link = c.link
        keep = c.settings.keep_backups
        root = c.settings.backups_dir

        def abort():
            return link._exclusive_depth > 1 or c.hold or link.closed

        def work(w, link):
            p = AB.capture_to_file(link, folder, prof, fw, version, _("automatic, {reason}").format(reason=reason), "auto",
                                   abort=abort)
            prune(folder, keep)
            target = s.get("mirror")
            if target:
                try:
                    AB.mirror_file(p, root, Path(target))
                    AB.STATUS.mirror = _("Mirror up to date ({time})").format(time=time.strftime('%H:%M'))
                except OSError as e:
                    AB.STATUS.mirror = _("Mirror not possible: {error}").format(error=e)
            return p

        def done(p):
            self.running = False
            AB.STATUS.last_path, AB.STATUS.last_time = p, time.time()
            AB.STATUS.add(_("Automatic backup of {device} ({reason}) saved.").format(device=prof.name, reason=reason))
            c.message.emit(_("Automatic backup of {device} saved.").format(device=prof.name))
            self.updated.emit()

        def fail(msg, tb):
            self.running = False
            AB.STATUS.add(_("Automatic backup not made: {error}").format(error=msg))
            c.message.emit(_("Automatic backup not made: {error}").format(error=msg))
            self.updated.emit()

        AB.STATUS.add(_("Automatic backup of {device} running …").format(device=prof.name))
        self.updated.emit()
        c.job(c.window, _("Automatic backup"), work, dialog=False, on_done=done, on_fail=fail)
        return True


# ------------------------------------------------------------ Seite ---

class BackupsPage(QWidget):
    navigate = pyqtSignal(str)

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.auto = AutoBackupService(ctx, self)
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)   # Reihen brechen um
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        lay = QVBoxLayout(body)
        lay.addWidget(heading(_("Backups"), _("Complete backups (running music, all projects, user presets, "
                                         "settings). Same format as the web editor – files are interchangeable. "
                                         "Look inside, compare and restore parts: in the tab <b>Browse & Compare</b>.")))
        top = QHBoxLayout()
        self.chk_all = QCheckBox(_("Show backups of all devices"))
        self.chk_all.toggled.connect(self.refresh)
        top.addWidget(self.chk_all)
        self.chk_pinned = QCheckBox(_("pinned only"))
        self.chk_pinned.toggled.connect(self.refresh)
        top.addWidget(self.chk_pinned)
        top.addStretch(1)
        lay.addLayout(top)
        reflow(top)                                      # schmal: Häkchen untereinander statt abgeschnitten
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([_("Date"), _("Device"), _("Firmware"), _("Kind"), _("Content"), _("Size"),
                                     _("Note / Labels")])
        self.tree.setRootIsDecorated(False)
        self.tree.setSortingEnabled(True)
        self.tree.setMinimumHeight(260)
        self.tree.itemDoubleClicked.connect(lambda *_a: self._browse())
        lay.addWidget(self.tree, 1)
        self.info = QLabel()
        self.info.setWordWrap(True)
        lay.addWidget(self.info)

        def btn(ic, txt, fn, tip=""):
            b = QPushButton(icon(*ic), txt)
            b.clicked.connect(fn)
            if tip:
                b.setToolTip(tip)
            return b
        row = QHBoxLayout()
        for b in (btn(("document-save", "backup"), _("Back up now"), self._new, _("Ctrl+B")),
                  btn(("document-preview", "system-search"), _("View content …"), self._browse,
                      _("Opens the backup in the tab “Browse & Compare”: view projects, presets, settings, "
                      "compare with another state, restore single parts (also double-click)")),
                  btn(("document-revert", "edit-undo"), _("Restore everything …"), self._restore),
                  btn(("folder-music",), _("Extract projects"), self._extract)):
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        reflow(row)                                      # Knopfreihen brechen bei halber Bildschirmbreite um
        row2 = QHBoxLayout()
        for b in (btn(("document-edit",), pgettext("backup", "Note"), self._note),
                  btn(("tag", "bookmark-new"), _("Labels"), self._labels, _("e.g. “before firmware change”, “live set October”")),
                  btn(("pin", "window-pin", "emblem-favorite"), _("Pin"), self._pin,
                      _("Pinned backups are never deleted automatically")),
                  btn(("document-import", "document-open"), _("Add file …"), self._import),
                  btn(("edit-delete",), _("Delete"), self._delete)):
            row2.addWidget(b)
        row2.addStretch(1)
        row2.addWidget(btn(("folder-open",), _("Open folder"), self._open_folder))
        lay.addLayout(row2)
        reflow(row2)
        lay.addWidget(self._auto_box())
        ctx.changed.connect(self.refresh)
        self.auto.updated.connect(self._auto_status)
        self.auto.updated.connect(self.refresh)
        self.refresh()

    # ------------------------------------------------------- Automatik
    def _auto_box(self) -> QWidget:
        s = AB.settings()
        g = QGroupBox(_("Automatic backups"))
        f = QFormLayout(g)
        f.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)   # schmal: Feld unter das lange Häkchen
        self.a_connect = QCheckBox(_("on connect, if the last backup is older than"))
        self.a_connect.setChecked(s["on_connect"])
        self.a_days = QSpinBox()
        self.a_days.setRange(1, 60)
        self.a_days.setSuffix(_(" day(s)"))
        self.a_days.setValue(int(s["days"]))
        self.a_days.setMaximumWidth(130)
        f.addRow(self.a_connect, self.a_days)
        self.a_periodic = QCheckBox(_("periodically while connected, every"))
        self.a_periodic.setChecked(s["periodic"])
        self.a_periodic.setToolTip(_("Note: a backup briefly stops playback on the FM-1. "
                                   "Never runs while the editor or a recording is open."))
        self.a_hours = QSpinBox()
        self.a_hours.setRange(1, 72)
        self.a_hours.setSuffix(_(" hours"))
        self.a_hours.setValue(int(s["hours"]))
        self.a_hours.setMaximumWidth(130)
        f.addRow(self.a_periodic, self.a_hours)
        self.a_keep = QSpinBox()
        self.a_keep.setRange(1, 500)
        self.a_keep.setValue(int(self.ctx.settings.keep_backups or 20))
        self.a_monthly = QCheckBox(_("also keep the newest of each month"))
        self.a_monthly.setChecked(s["keep_monthly"])
        f.addRow(_("Keep automatic ones:"), self._h(self.a_keep, self.a_monthly))
        self.a_mirror = QLineEdit(s["mirror"])
        self.a_mirror.setPlaceholderText(_("optional: e.g. /mnt/nas/FM-1-Backups – every backup is copied and verified"))
        b_pick = QPushButton(icon("folder-open"), "…")
        b_pick.clicked.connect(self._pick_mirror)
        b_now = QPushButton(icon("folder-sync", "view-refresh"), _("Mirror now"))
        b_now.clicked.connect(self._mirror_now)
        f.addRow(_("Mirror folder:"), self._h(self.a_mirror, b_pick, b_now))
        for wdg in (self.a_connect, self.a_periodic, self.a_monthly):
            wdg.toggled.connect(self._save_auto)
        for wdg in (self.a_days, self.a_hours, self.a_keep):
            wdg.valueChanged.connect(self._save_auto)
        self.a_mirror.editingFinished.connect(self._save_auto)
        self.a_status = QLabel()
        self.a_status.setWordWrap(True)
        self.a_status.setMinimumWidth(0)
        self.a_status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        f.addRow(_("Status:"), self.a_status)
        hint = QLabel(_("A backup briefly stops playback on the FM-1. Automatic backups therefore only run when "
                      "nothing else is happening with the device – never during an installation, in the editor or "
                      "during a recording. Before every firmware installation the app backs up by default, labelled "
                      "with the old firmware (no backup only after explicit confirmation in the installation plan)."))
        hint.setWordWrap(True)
        hint.setMinimumWidth(0)
        hint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        hint.setStyleSheet("color: palette(mid);")
        f.addRow(hint)
        self._auto_status()
        return g

    @staticmethod
    def _h(*ws):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        for x in ws:
            h.addWidget(x)
        h.addStretch(0)
        return w

    def _save_auto(self, *_args):
        s = AB.settings()
        s.update(on_connect=self.a_connect.isChecked(), days=self.a_days.value(), periodic=self.a_periodic.isChecked(),
                 hours=self.a_hours.value(), keep_monthly=self.a_monthly.isChecked(),
                 mirror=self.a_mirror.text().strip())
        self.ctx.settings.keep_backups = self.a_keep.value()
        config.save(self.ctx.settings)

    def _pick_mirror(self):
        d = QFileDialog.getExistingDirectory(self, _("Choose mirror folder"), self.a_mirror.text() or str(Path.home()))
        if d:
            self.a_mirror.setText(d)
            self._save_auto()

    def _mirror_now(self):
        self._save_auto()
        target = AB.settings()["mirror"]
        if not target:
            show_error(self, _("No mirror folder"), _("Please enter a mirror folder first (e.g. a NAS drive)."))
            return
        from .common import JobDialog

        def work(w):
            ok, bad, msgs = AB.mirror_all(self.ctx.settings.backups_dir, Path(target))
            for m in msgs:
                w.log.emit(m)
            w.log.emit(_("{ok} backups verified in the mirror, {bad} errors.").format(ok=ok, bad=bad))
            AB.STATUS.mirror = _("Mirror: {ok} verified, {bad} errors ({time})").format(ok=ok, bad=bad,
                                                                                    time=time.strftime('%H:%M'))
            return ok, bad
        from . import feedback
        feedback.run(self, _("Mirror backups"), work,
                     done=lambda r: (_("Mirror updated: {ok} backups copied and verified, {bad} ERRORS (see log). "
                                       "Target: {target}") if r[1] else
                                     _("Mirror updated: {ok} backups copied and verified, no errors. Target: {target}")
                                     ).format(ok=r[0], bad=r[1], target=target))
        self._auto_status()

    def _auto_status(self):
        st = AB.STATUS
        lines = [st.text]
        if st.mirror:
            lines.append(st.mirror)
        self.a_status.setText("<br>".join(lines))
        self.a_status.setToolTip("\n".join(st.history) or _("nothing yet"))

    # ------------------------------------------------------------ Liste
    def _folder(self) -> Path:
        if self.chk_all.isChecked() or not self.ctx.profile:
            return self.ctx.settings.backups_dir
        return self.ctx.device_folder(self.ctx.settings.backups_dir)

    def refresh(self):
        self.tree.setSortingEnabled(False)
        self.tree.clear()
        try:
            folder = self._folder()
        except OSError as e:
            self.info.setText(_("<b>Backup folder not reachable.</b> {error}").format(error=e))
            return
        total = 0
        metas = AB.list_backups(folder, include_damaged=True)
        for m in metas:
            if self.chk_pinned.isChecked() and not m.pinned:
                continue
            if m.damaged:
                it = _Row([dt.datetime.fromtimestamp(m.mtime).strftime("%d.%m.%Y %H:%M") if m.mtime
                           else "?", m.device or m.path.parent.name, "–", _("damaged"),
                           _("file not readable"), fmt_size(m.size), m.path.name])
                it.setData(0, Qt.ItemDataRole.UserRole, str(m.path))
                it.setData(0, SORT, float(m.mtime or 0))
                it.setData(5, SORT, float(m.size))
                it.setData(1, Qt.ItemDataRole.UserRole, "damaged")
                it.setIcon(0, icon("dialog-warning", "emblem-important"))
                for c in range(7):
                    it.setToolTip(c, _("{path}\nDamaged: {problem}. Restoring is not possible – delete it or "
                                       "recover it from the mirror folder.").format(path=m.path, problem=m.damaged))
                    it.setForeground(c, QColor("#da4453"))
                self.tree.addTopLevelItem(it)
                total += m.size
                continue
            note = " · ".join(([m.note] if m.note else []) + [f"#{x}" for x in m.labels])
            it = _Row([m.when.astimezone().strftime("%d.%m.%Y %H:%M"), m.device or m.path.parent.name,
                       fw_label(m.firmware), _(KIND_TEXT[m.kind]) if m.kind in KIND_TEXT else m.kind,
                       AB.content_text(m.projects, m.presets, m.objects), fmt_size(m.size), note])
            it.setData(0, Qt.ItemDataRole.UserRole, str(m.path))
            it.setData(0, SORT, m.when.timestamp())
            it.setData(5, SORT, float(m.size))
            it.setToolTip(0, str(m.path))
            it.setToolTip(4, _("Parts = separately storable building blocks of the backup (running music, settings, "
                             "every project slot, every preset bank, every sample slot)"))
            if note:
                it.setToolTip(6, note)
            if m.pinned:
                it.setIcon(0, icon("pin", "window-pin", "emblem-favorite"))
                it.setToolTip(6, _("pinned – never deleted automatically"))
            if m.fmt != backup.FORMAT:
                it.setToolTip(4, _("SLOOP backup (format of the SLOOP web editor) – interchangeable in both directions"))
            self.tree.addTopLevelItem(it)
            total += m.size
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(0, Qt.SortOrder.DescendingOrder)
        for c in range(6):
            self.tree.resizeColumnToContents(c)
        n = self.tree.topLevelItemCount()
        bad = sum(1 for m in metas if m.damaged)
        why = no_backup_reason(self.ctx) if self.ctx.port is not None else ""
        if n:
            self.info.setText(ngettext("{n} backup, {size} in {folder}", "{n} backups, {size} in {folder}", n).format(
                n=n, size=fmt_size(total), folder=folder) +
                (" – <b style='color:#da4453'>" + _("{n} damaged").format(n=bad) + "</b>" if bad else "") +
                (f"<br><small>{why}</small>" if why else ""))
        elif why:
            self.info.setText(_("<b>No backups of this device yet.</b> {reason}").format(reason=why))
        else:
            self.info.setText(_("No backups yet – here's how: connect the FM-1 and click “Back up now” (Ctrl+B). "
                              "Automatic backups are set up below."))

    def _open_folder(self):
        try:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._folder())))
        except OSError as e:
            show_error(self, _("Folder not reachable"), str(e))

    def _sel(self) -> Path | None:
        it = self.tree.currentItem()
        return Path(it.data(0, Qt.ItemDataRole.UserRole)) if it else None

    def _sel_ok(self) -> Path | None:
        """ausgewähltes Backup, wenn es lesbar ist (sonst Hinweis)"""
        it = self.tree.currentItem()
        if it is not None and it.data(1, Qt.ItemDataRole.UserRole) == "damaged":
            show_error(self, _("Backup damaged"), _("{name} cannot be read. Restoring and opening are not possible – deleting is.").format(
                name=Path(it.data(0, Qt.ItemDataRole.UserRole)).name))
            return None
        return self._sel()

    def _new(self):
        note, ok = QInputDialog.getText(self, _("Backup"), _("Note (optional):"))
        if ok:
            make_backup(self.ctx, self, note.strip())
            self.refresh()

    def _browse(self):
        p = self._sel_ok()
        win = getattr(self.ctx, "window", None)
        if p and win is not None and win.page("backup_browser") is not None:
            win.go("backup_browser", path=str(p))
        elif p:
            self._details()

    def _details(self):
        p = self._sel()
        if not p:
            return
        from .. import backup_inspect as BI
        try:
            r = BI.inspect(p)
        except Exception as e:  # noqa: BLE001
            show_error(self, _("Backup damaged"), str(e))
            return
        lines = [f"<b>{p.name}</b>", _("Firmware: {firmware}").format(firmware=r.firmware),
                 _("Created: {date}").format(date=fmt_date(r.created)), r.summary(), ""]
        lines += [f"{o.title}: {fmt_size(o.size)}" for o in r.objects if o.used]
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox(QMessageBox.Icon.Information, _("Backup"), "<br>".join(lines), parent=self).exec()

    def _restore(self):
        p = self._sel_ok()
        if not p:
            return
        why = no_backup_reason(self.ctx)
        if why:
            show_error(self, _("Restore not possible"), why)
            return
        try:
            a = backup.load_file(p)
        except Exception as e:  # noqa: BLE001
            show_error(self, _("Backup damaged"), str(e))
            return
        comp = a.companion
        # dieselbe Prüfung wie auf der Geräte-Seite: nur innerhalb derselben Firmware-Familie
        from .. import backup_inspect as BI
        try:
            rep = BI.inspect(p)
            compat = BI.compatibility(rep, self.ctx.firmware_key, self.ctx.info.version if self.ctx.info else "", None)
        except Exception as e:  # noqa: BLE001
            show_error(self, _("Backup damaged"), str(e))
            return
        if not compat.ok:
            show_error(self, _("Restore not possible"),
                       f"<b>{p.name}</b><br><br>{compat.reason}" +
                       ("<br><br>" + _("What is possible:") + "<ul>" + "".join(f"<li>{x}</li>" for x in compat.possible) + "</ul>"
                        if compat.possible else ""))
            return
        warn = ""
        if comp.get("device_id") and self.ctx.profile and comp["device_id"] != self.ctx.profile.id:
            warn += "<br><br>" + _("The backup comes from another device ({device}). That is allowed.").format(
                device=comp.get('device'))
        if self.ctx.profile:
            everything = _("Everything on <b>{device}</b> will be replaced by the state of {date}: running music, all "
                           "projects, presets and settings.").format(device=self.ctx.profile.name,
                                                                     date=fmt_date(a.created))
        else:
            everything = _("Everything on <b>the FM-1</b> will be replaced by the state of {date}: running music, all "
                           "projects, presets and settings.").format(date=fmt_date(a.created))
        if not confirm(self, _("Restore backup"),
                       everything + "<br><br>" +
                       _("A safety backup of the current state is made automatically first.") + "<br>" +
                       _("Restore only parts? Use the tab “Browse & Compare”.") + warn +
                       ("<br><br>" + _("<b>SLOOP:</b> Please stop the song on the FM-1 first (STOP) – otherwise SLOOP "
                                       "refuses the restore.") if a.sloop else ""),
                       _("Restore"), danger=True):
            return
        safety = make_backup(self.ctx, self, _("automatic, before restoring {name}").format(name=p.name), "auto")
        if safety is None and not confirm(self, _("No safety backup"),
                                          _("The safety backup failed. Restore anyway?"),
                                          _("Restore anyway"), danger=True):
            return

        fw_key = self.ctx.firmware_key or ""
        state = {"error": None}
        planned = [o["id"] for o in a.objects]

        def work(w, link):
            w.log.emit(_("Checking every object with the device before anything is replaced …") if not a.sloop else
                       _("Checking the backup and writing it in SLOOP's order: banks, projects, settings, "
                         "samples …"))
            try:
                backup.restore(link, a, progress=lambda x, y: w.progress.emit(x, y), firmware_key=fw_key)
            except Exception as e:
                state["error"] = e
                for i in getattr(e, "written", None) or []:
                    w.log.emit(_("✓ {part} written").format(part=backup.object_title(i, a.sloop)))
                raise
            w.log.emit(_("Done. The FM-1 now plays the state from the backup."))
            return len([o for o in a.objects if o.get("size")]) if not a.sloop else len(a.objects)
        when = fmt_date(a.created)
        after = " " + (_("The previous state is in the safety backup {name}.").format(name=safety.name) if safety
                       else _("There is NO safety backup of the previous state."))

        def failed(m):
            e = state["error"]
            where = backup.written_text(e, planned, a.sloop) if e is not None else ""
            return _("Restore failed: {error}").format(error=m) + f"<br>{where}{after}"
        self.ctx.job(self, _("Restore backup"), work,
                     cancel_tip=_("A complete restore cannot be cancelled safely – please wait. "
                                  "Single parts are restored in the tab “Browse & Compare”."),
                     done=lambda n: _("Restore complete: {n} parts of the state of {date} written – running "
                                      "music, projects, presets and settings.").format(n=n, date=when) + after,
                     fail_text=failed)
        self.refresh()

    def _extract(self):
        p = self._sel_ok()
        if not p:
            return
        try:
            a = backup.load_file(p)
        except Exception as e:  # noqa: BLE001
            show_error(self, _("Backup damaged"), str(e))
            return
        objs = projects.projects_from_backup(a)
        if not objs:
            show_error(self, _("No projects"), _("This backup contains no saved projects."))
            return
        dest = self.ctx.settings.projects_dir
        dev = (a.companion or {}).get("device", "")
        what = _("Section") if a.sloop else _("Slot")
        try:
            for slot, data in objs:
                name = _("{device} {what} {slot}").format(
                    device=dev or 'FM-1', what=what, slot='ABCD'[slot - 1] if a.sloop and 1 <= slot <= 4 else slot)
                projects.save_project(dest, name, data, a.firmware, dev,
                                      source=_("Backup {name}, {what} {slot}").format(name=p.name, what=what, slot=slot))
        except OSError as e:
            from .common import fs_error_text
            show_error(self, _("Not saved"), fs_error_text(e, _("projects not written to the library")))
            return
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox(QMessageBox.Icon.Information, _("Projects extracted"),
                    ngettext("{n} project is now in the project library ({folder}). There you can rename and "
                             "load it. Nothing was changed on the FM-1.",
                             "{n} projects are now in the project library ({folder}). There you can rename and "
                             "load them. Nothing was changed on the FM-1.", len(objs)).format(n=len(objs), folder=dest),
                    parent=self).exec()
        win = getattr(self.ctx, "window", None)
        if win is not None and hasattr(win, "projects"):
            try:
                win.projects.refresh_library()
            except Exception:  # noqa: BLE001
                pass

    def _note(self):
        p = self._sel_ok()
        if not p:
            return
        m = AB.read_meta(p)
        txt, ok = QInputDialog.getText(self, pgettext("backup", "Note"), _("Note for the backup:"), text=m.note if m else "")
        if ok:
            self._set_meta(p, note=txt)

    def _labels(self):
        p = self._sel_ok()
        if not p:
            return
        m = AB.read_meta(p)
        txt, ok = QInputDialog.getText(self, _("Labels"), _("Labels, separated by commas\n"
                                                            "(e.g. before firmware change, live set October):"),
                                       text=", ".join(m.labels) if m else "")
        if ok:
            self._set_meta(p, labels=[x.strip() for x in txt.split(",") if x.strip()])

    def _pin(self):
        p = self._sel_ok()
        if not p:
            return
        m = AB.read_meta(p)
        self._set_meta(p, pinned=not (m and m.pinned))

    def _set_meta(self, p: Path, **kw):
        try:
            AB.set_meta(p, **kw)
            what = {"note": _("note saved"), "labels": _("labels saved"),
                    "pinned": _("pinned – never deleted automatically") if kw.get("pinned") else _("no longer pinned")}
            self.ctx.message.emit(f"{p.name}: " + ", ".join(what[k] for k in kw if k in what))
        except (OSError, ValueError, backup.BackupError) as e:
            from .common import fs_error_text
            show_error(self, _("Not saved"), fs_error_text(e, _("{name} cannot be changed").format(name=p.name))
                       if isinstance(e, OSError) else f"{p.name}: {e}")
        self.refresh()

    def _import(self):
        files, _filter = QFileDialog.getOpenFileNames(self, _("Add backup files"), str(Path.home() / "Downloads"),
                                                      _("Backups (*.json)"))
        from .common import fs_error_text
        for f in files:
            try:
                from .. import backup_inspect as BI
                BI.inspect(Path(f))
            except Exception as e:  # noqa: BLE001
                show_error(self, _("Not a valid file"), f"{Path(f).name}: {e}")
                continue
            try:
                folder = self._folder()
                dest = folder / Path(f).name
                if dest.exists():
                    if dest.read_bytes() == Path(f).read_bytes():
                        self.ctx.message.emit(_("{name} is already in the backup folder (identical) – nothing copied.").format(
                            name=Path(f).name))
                        continue
                    n = 2
                    while dest.exists():
                        dest = folder / f"{Path(f).stem} ({n}){Path(f).suffix}"
                        n += 1
                shutil.copy2(f, dest)
                self.ctx.message.emit(_("Backup added: {name} (copy, the original stays)").format(name=dest.name))
            except OSError as e:
                show_error(self, _("Not copied"), fs_error_text(e, _("{name} not copied").format(name=Path(f).name)))
                break
        self.refresh()

    def _delete(self):
        p = self._sel()
        if not p:
            return
        m = AB.read_meta(p)
        extra = "<br><br>" + _("<b>This backup is pinned.</b>") if m and m.pinned else ""
        if confirm(self, _("Delete"), _("Delete {name} permanently?").format(name=p.name) + extra, _("Delete"), danger=True):
            try:
                p.unlink(missing_ok=True)
                self.ctx.message.emit(_("Deleted: {name}").format(name=p.name))
            except OSError as e:
                show_error(self, _("Not deleted"), f"{p.name}: {e.strerror or e}")
            self.refresh()
