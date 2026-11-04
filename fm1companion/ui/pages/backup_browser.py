# SPDX-License-Identifier: GPL-3.0-only
"""Backup browser: look inside backups, compare, restore and export individual parts."""
from __future__ import annotations

import base64
import json
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                             QListWidget, QListWidgetItem, QPushButton, QSplitter, QTabWidget, QTextBrowser,
                             QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ... import autobackup as AB, backup, backup_inspect as BI, projects
from ..common import confirm, flow_row, fmt_date, fmt_size, fw_label, heading, icon, safe_folder, show_error
from ..responsive import adaptive_splitter, reflow
from . import PageSpec
from ...i18n import N_, _, ngettext, pgettext

GROUP_ICONS = {BI.G_MUSIC: ("media-playback-start",), BI.G_PROJECTS: ("folder-music", "folder"),
               BI.G_PRESETS: ("audio-x-generic", "preferences-desktop-sound"),
               BI.G_SETTINGS: ("configure", "preferences-system"), BI.G_SAMPLES: ("audio-x-wav", "audio-x-generic"),
               BI.G_OTHER: ("application-octet-stream", "unknown")}
GROUP_ORDER = [BI.G_MUSIC, BI.G_PROJECTS, BI.G_PRESETS, BI.G_SETTINGS, BI.G_SAMPLES, BI.G_OTHER]
LIVE = "__live__"


def selective_restore(ctx, parent, rep: BI.Report, ids, source_label: str) -> bool:
    """Read a safety backup → list exactly what will be overwritten → confirm → write."""
    if not ctx.require(parent):
        return False
    if not rep.restorable or rep.archive is None:
        show_error(parent, _("Not possible"), _("The app cannot restore this backup format."))
        return False
    if rep.firmware_key and ctx.firmware_key and rep.firmware_key != ctx.firmware_key:
        show_error(parent, _("Different firmware"), _("The backup comes from <b>{source}</b>, the FM-1 runs "
                                                      "<b>{target}</b>. Parts of it do not fit – nothing is "
                                                      "written.").format(source=BI.fw_name(rep.firmware_key),
                                                                         target=BI.fw_name(ctx.firmware_key)))
        return False
    prof = ctx.profile
    try:
        folder = ctx.device_folder(ctx.settings.backups_dir)
    except OSError as e:
        show_error(parent, _("No safety backup possible"), f"{e}<br><br>" + _("Without a safety backup nothing is "
                                                                                 "restored."))
        return False
    fw, version = ctx.firmware_key, ctx.info.version if ctx.info else ""

    def read_now(w, link):
        w.log.emit(_("Safety backup: reading the current state of the device …"))
        p = AB.capture_to_file(link, folder, prof, fw, version,
                               _("automatic, before restoring parts from {source}").format(source=source_label), "sicherheit",
                               progress=lambda a, b: w.progress.emit(a, b))
        w.log.emit(_("Saved: {name}").format(name=p.name))
        return p
    d = ctx.job(parent, _("Safety backup"), read_now,
                done_text=lambda p: _("Current state saved: {name}. Nothing has been restored yet – "
                                      "next you will see exactly what will be replaced.").format(name=p.name),
                fail_text=lambda m: _("Safety backup failed: {error}. Nothing is written.").format(error=m))
    if not d.ok:
        show_error(parent, _("No safety backup"), _("The safety backup failed. "
                                                    "Without a safety backup nothing is written."))
        return False
    safety = d.result_value
    cur = BI.inspect(safety)
    man = [backup.ObjInfo(o.id, o.size, o.crc) for o in cur.objects]
    plan = BI.plan_restore(rep, ids, man, cur)
    if not plan.ok:
        show_error(parent, _("Restore not possible"), "<br>".join(plan.errors))
        return False
    name = prof.name if prof else ""
    if not confirm(parent, _("Restore selected"),
                   plan.html(name) + "<br><br>" + _("Safety backup of the current state: <i>{name}</i>").format(
                       name=safety.name),
                   _("Restore"), danger=True):
        return False
    arch = rep.archive
    titles = {it.id: it.title for it in plan.items}
    state = {"error": None}

    def work(w, link):
        w.log.emit(_("Checking every object with the device before anything is replaced …"))
        try:
            done = backup.restore_selected(link, arch, plan.ids(), progress=lambda a, b: w.progress.emit(a, b),
                                           expect=plan.expect, clear_extension=plan.clear_extension,
                                           cancelled=lambda: bool(getattr(w, "cancelled", False)))
        except Exception as e:
            state["error"] = e
            for i in getattr(e, "written", None) or []:
                w.log.emit(_("✓ {part} written").format(part=titles.get(i) or backup.object_title(i, arch.sloop)))
            raise
        for it in plan.items:
            w.log.emit(f"✓ {it.title}")
        w.log.emit(_("Done."))
        return done

    def failed(m):
        e = state["error"]
        where = backup.written_text(e, plan.ids(), arch.sloop, titles) if e is not None else ""
        cancelled = bool(getattr(e, "cancelled", False) or getattr(e, "code", "") == "cancel")
        head = _("Restore cancelled") if cancelled else _("Restore failed: {error}").format(error=m)
        return f"{head}<br>{where}<br>" + _("You have the state before in the safety backup {name}.").format(
            name=safety.name)
    n = len(plan.items)
    parts = ", ".join(it.title for it in plan.items)
    if name:
        done_text = ngettext("{n} part restored to {device}: {parts}. The previous state is in the safety backup {name}.",
                             "{n} parts restored to {device}: {parts}. The previous state is in the safety backup "
                             "{name}.", n).format(n=n, device=name, parts=parts, name=safety.name)
    else:
        done_text = ngettext("{n} part restored to the FM-1: {parts}. The previous state is in the safety backup {name}.",
                             "{n} parts restored to the FM-1: {parts}. The previous state is in the safety backup "
                             "{name}.", n).format(n=n, parts=parts, name=safety.name)
    d2 = ctx.job(parent, _("Restore selected"), work, cancellable=True, done_text=done_text, fail_text=failed)
    if d2.ok:
        ctx.message.emit(ngettext("{n} part restored to {device}.", "{n} parts restored to {device}.", n).format(
            n=n, device=name) if name else
            ngettext("{n} part restored to the FM-1.", "{n} parts restored to the FM-1.", n).format(n=n))
    return d2.ok


class BackupBrowserPage(QWidget):
    navigate = pyqtSignal(str)

    def __init__(self, ctx, window=None):
        super().__init__()
        self.ctx = ctx
        self.window_ = window
        self.rep: BI.Report | None = None
        self.other: BI.Report | None = None
        self.metas: list[AB.Meta] = []
        lay = QVBoxLayout(self)
        lay.addWidget(heading(_("Backup Browser"), _("Look inside every backup: projects, all 384 preset slots, "
                                                       "settings. Compare two states and restore only parts.")))
        split = QSplitter()
        lay.addWidget(split, 1)

        # left: device + timeline
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.dev = QComboBox()
        self.dev.setToolTip(_("Backups of which device?"))
        self.dev.currentIndexChanged.connect(self._fill_timeline)
        ll.addWidget(self.dev)
        self.timeline = QListWidget()
        self.timeline.setMinimumHeight(64)
        self.timeline.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)   # long lines: … instead of scroll bar
        self.timeline.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.timeline.currentRowChanged.connect(self._pick)
        ll.addWidget(self.timeline, 1)
        row = QHBoxLayout()
        for ic, txt, fn, tip in ((("document-edit",), pgettext("backup", "Note"), self._note, ""),
                                 (("tag", "bookmark-new"), _("Labels"), self._labels, _("e.g. “before firmware change”")),
                                 (("pin", "window-pin"), _("Pin"), self._pin, _("never delete automatically"))):
            b = QPushButton(icon(*ic), txt)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            row.addWidget(b)
        ll.addLayout(row)
        reflow(row)                                      # buttons wrap when the column is narrow
        row = QHBoxLayout()
        b = QPushButton(icon("archive-insert", "package-x-generic"), _("As zip …"))
        b.setToolTip(_("Export the backups of this device (or all) as a zip with a table of contents"))
        b.clicked.connect(self._zip_export)
        row.addWidget(b)
        b = QPushButton(icon("archive-extract", "document-open"), _("Import zip …"))
        b.clicked.connect(self._zip_import)
        row.addWidget(b)
        ll.addLayout(row)
        reflow(row)
        split.addWidget(left)

        # right: tabs
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.head = QLabel(_("No backup selected."))
        self.head.setWordWrap(True)
        rl.addWidget(self.head)
        self.tabs = QTabWidget()
        self.tabs.setMinimumHeight(130)                  # narrow/stacked: content tree never squeezed
        rl.addWidget(self.tabs, 1)
        # Content
        self.content = QTreeWidget()
        self.content.setHeaderLabels([_("Content"), _("Size"), _("Format"), _("Details")])
        self.content.itemChanged.connect(self._check_changed)
        self.content.currentItemChanged.connect(lambda *_a: self._update_view_button())
        self.content.itemDoubleClicked.connect(lambda *_a: self.view_project())
        self.tabs.addTab(self.content, icon("view-list-tree", "view-list-details"), _("Content"))
        # Presets
        pw = QWidget()
        pl = QVBoxLayout(pw)
        pr = QHBoxLayout()
        self.p_search = QLineEdit()
        self.p_search.setPlaceholderText(_("Search presets (name, engine, slot) …"))
        self.p_search.textChanged.connect(self._fill_presets)
        self.p_used = QCheckBox(_("used only"))
        self.p_used.setChecked(True)
        self.p_used.toggled.connect(self._fill_presets)
        pr.addWidget(self.p_search, 1)
        pr.addWidget(self.p_used)
        pl.addLayout(pr)
        self.ptable = QTreeWidget()
        self.ptable.setHeaderLabels([_("Slot"), _("Name"), _("Engine"), "★", _("Bank (object)")])
        self.ptable.setRootIsDecorated(False)
        self.ptable.setSortingEnabled(True)
        self.ptable.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        pl.addWidget(self.ptable, 1)
        self.p_info = QLabel()
        pl.addWidget(self.p_info)
        self.tabs.addTab(pw, icon("audio-x-generic"), _("Presets"))
        # Comparison
        cw = QWidget()
        cl = QVBoxLayout(cw)
        cr = QHBoxLayout()
        cr.addWidget(QLabel(_("Compare with:")))
        self.cmp_with = QComboBox()
        cr.addWidget(self.cmp_with, 1)
        b = QPushButton(icon("document-compare", "kompare", "view-split-left-right"), _("Compare"))
        b.clicked.connect(self.compare)
        cr.addWidget(b)
        self.cmp_only = QCheckBox(_("changes only"))
        self.cmp_only.setChecked(True)
        self.cmp_only.toggled.connect(self._fill_compare)
        cr.addWidget(self.cmp_only)
        cl.addLayout(cr)
        self.cmp_sum = QLabel(_("Choose a second state – another backup or the live state of the device."))
        self.cmp_sum.setWordWrap(True)
        cl.addWidget(self.cmp_sum)
        self.cmp_tree = QTreeWidget()
        self.cmp_tree.setHeaderLabels([_("Content"), _("this backup"), _("Comparison"), _("Change")])
        cl.addWidget(self.cmp_tree, 1)
        b = QPushButton(icon("edit-select-all"), _("Select changed parts for restoring"))
        b.clicked.connect(self._select_changed)
        cl.addWidget(b)
        self.tabs.addTab(cw, icon("document-compare", "view-split-left-right"), _("Comparison"))
        # Info
        self.infov = QTextBrowser()
        self.tabs.addTab(self.infov, icon("documentinfo", "help-about"), _("Info"))

        self.b_restore = QPushButton(icon("document-revert", "edit-undo"), _("Restore selected …"))
        self.b_restore.setToolTip(_("Only the checked parts to the connected FM-1 – with a safety backup and an exact list"))
        self.b_restore.clicked.connect(self.restore_selected)
        self.b_export = QPushButton(icon("document-export", "document-save-as"), _("Export selected …"))
        self.b_export.setToolTip(_("Checked parts as files into a folder: projects as .fm1proj (also into the "
                                   "project library), selected presets as .json, everything else raw (.bin)"))
        self.b_export.clicked.connect(self._export)
        self.b_view = QPushButton(icon("view-preview", "document-preview", "document-open"), _("View project"))
        self.b_view.setToolTip(_("Open the selected project in Projects › View & Export: tracks, patterns, song, "
                                 "MIDI export (also by double-click)"))
        self.b_view.clicked.connect(self.view_project)
        self.b_view.setEnabled(False)
        self.sel_label = QLabel()
        self.sel_label.setWordWrap(True)
        rl.addWidget(flow_row(self.b_restore, self.b_export, self.b_view))
        rl.addWidget(self.sel_label)
        split.addWidget(right)
        split.setStretchFactor(1, 3)
        split.setSizes([300, 900])
        adaptive_splitter(split, 640, ratios=(1, 3))     # narrow: timeline above the tabs
        ctx.changed.connect(self._fill_devices)
        self._fill_devices()

    # ----------------------------------------------------------- Page
    def activate(self):
        self._fill_devices()
        if not self.rep and self.metas:
            self.open_path(self.metas[0].path)

    def open_with(self, path: str = "", **_kw):
        if path:
            self.open_path(Path(path))

    def _fill_devices(self):
        cur = self.dev.currentData()
        self.dev.blockSignals(True)
        self.dev.clear()
        self.dev.addItem(icon("folder"), _("all devices"), "")
        root, _err = safe_folder("Backups")
        try:
            dirs = sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []
        except OSError:
            dirs = []
        for d in dirs:
            prof = next((p for p in self.ctx.store.profiles.values() if p.folder_name == d.name), None)
            self.dev.addItem(icon("audio-midi", "media-flash"), prof.name if prof else d.name, str(d))
        want = cur if cur is not None else (str(root / self.ctx.profile.folder_name) if self.ctx.profile else "")
        i = self.dev.findData(want)
        self.dev.setCurrentIndex(max(0, i))
        self.dev.blockSignals(False)
        self._fill_timeline()

    def _fill_timeline(self):
        folder = self.dev.currentData() or str(safe_folder("Backups")[0])
        keep = self.rep.path if self.rep else None
        try:
            self.metas = AB.list_backups(Path(folder))
        except OSError:
            self.metas = []
        self.timeline.blockSignals(True)
        self.timeline.clear()
        last_day = None
        for m in self.metas:
            day = m.when.astimezone().strftime("%d.%m.%Y")
            label = f"{m.when.astimezone().strftime('%H:%M')}  {m.device or m.path.parent.name} · {fw_label(m.firmware) or m.fw_key}"
            extra = " · ".join(([kind_text(m.kind)] if m.kind != "manuell" else []) + [f"#{x}" for x in m.labels])
            it = QListWidgetItem(icon("pin", "window-pin") if m.pinned else icon("document-save", "backup"),
                                 (f"{day}  " if day != last_day else "            ") + label + (f"\n            {extra}" if extra else "")
                                 + ("\n            " + _("“{note}”").format(note=m.note) if m.note else ""))
            last_day = day
            it.setData(Qt.ItemDataRole.UserRole, str(m.path))
            it.setToolTip(f"{m.path}\n{AB.content_text(m.projects, m.presets, m.objects)}, {fmt_size(m.size)}")
            self.timeline.addItem(it)
        if not self.metas:
            it = QListWidgetItem(_("No backups yet.\nHere's how: connect the FM-1,\nthen Ctrl+B (Back up now)."))
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            it.setToolTip(_("No backups yet – here's how: connect the FM-1, then Ctrl+B (Back up now)."))
            self.timeline.addItem(it)
        self.timeline.blockSignals(False)
        self.cmp_with.clear()
        if self.ctx.connected:
            self.cmp_with.addItem(icon("audio-midi"), _("Live state of the connected FM-1"), LIVE)
        for m in self.metas:
            self.cmp_with.addItem(f"{fmt_date(m.created)} · {m.device} · {fw_label(m.firmware)}", str(m.path))
        if keep:
            for i in range(self.timeline.count()):
                if self.timeline.item(i).data(Qt.ItemDataRole.UserRole) == str(keep):
                    self.timeline.blockSignals(True)
                    self.timeline.setCurrentRow(i)
                    self.timeline.blockSignals(False)
        elif self.metas and self.isVisible():
            self.open_path(self.metas[0].path)          # show the newest backup right away

    def _pick(self, row):
        it = self.timeline.item(row) if row >= 0 else None
        p = it.data(Qt.ItemDataRole.UserRole) if it else None
        if p:
            self.open_path(Path(p))

    # ----------------------------------------------------------- Load
    def open_path(self, path: Path):
        try:
            self.rep = BI.inspect(path)
        except Exception as e:  # noqa: BLE001
            show_error(self, _("Backup not readable"), f"{path.name}: {e}")
            return
        self.other = None
        self.cmp_tree.clear()
        self.cmp_sum.setText(_("Choose a second state – another backup or the live state of the device."))
        self._show()
        for i in range(self.timeline.count()):
            if self.timeline.item(i).data(Qt.ItemDataRole.UserRole) == str(path):
                self.timeline.blockSignals(True)
                self.timeline.setCurrentRow(i)
                self.timeline.blockSignals(False)

    def _show(self):
        r = self.rep
        c = r.companion
        self.head.setText(f"<b>{c.get('device') or (r.path.parent.name if r.path else 'Backup')}</b> · "
                          f"{fw_label(r.firmware)} · {fmt_date(r.created)} · {r.summary()}"
                          + ("" if r.restorable else " · <i>" + _("view only") + "</i>"))
        self.content.blockSignals(True)
        self.content.clear()
        groups = {}
        for g in GROUP_ORDER:
            objs = [o for o in r.objects if o.group == g]
            if not objs:
                continue
            gi = QTreeWidgetItem([_("{group} ({used} of {total} used)").format(
                group=BI.group_text(g), used=sum(1 for o in objs if o.used), total=len(objs))])
            gi.setIcon(0, icon(*GROUP_ICONS[g]))
            gi.setFlags(gi.flags() | Qt.ItemFlag.ItemIsAutoTristate | Qt.ItemFlag.ItemIsUserCheckable)
            gi.setCheckState(0, Qt.CheckState.Unchecked)
            self.content.addTopLevelItem(gi)
            groups[g] = gi
            for o in objs:
                it = QTreeWidgetItem([o.title, fmt_size(o.size) if o.used else _("empty"), o.format,
                                      (o.detail or "") + ("  ⚠ " + "; ".join(o.warnings) if o.warnings else "")])
                it.setData(0, Qt.ItemDataRole.UserRole, o.id)
                it.setToolTip(0, _("{part} – technically: object {id}, CRC {crc:08X}").format(part=o.title, id=o.id, crc=o.crc))
                if it.text(3):
                    it.setToolTip(3, it.text(3))
                if o.id in BI.skip_ids(r) or not r.restorable:
                    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
                else:
                    it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    it.setCheckState(0, Qt.CheckState.Unchecked)
                if not o.used:
                    it.setForeground(0, self.palette().placeholderText())
                gi.addChild(it)
            gi.setExpanded(g in (BI.G_MUSIC, BI.G_PROJECTS, BI.G_SETTINGS))
        self.content.blockSignals(False)
        for col in range(3):
            self.content.resizeColumnToContents(col)
        # group rows carry check box + icon; measured before the style sheet is applied they would be elided
        self.content.setColumnWidth(0, self.content.columnWidth(0) + 32)
        self._fill_presets()
        lines = [f"<h3>{c.get('device') or 'Backup'}</h3><table>"]
        kind = c.get("kind", "")
        for k, v in ((_("File"), str(r.path or "–")), (_("Format"), r.format),
                     (_("Firmware (reported by the device)"), fw_label(r.firmware)),
                     (_("Firmware key"), r.firmware_key or "–"), (_("Firmware version"), c.get("firmware_version", "–")),
                     (_("Created"), fmt_date(r.created)), (_("Kind"), kind_text(kind) if kind else "–"),
                     (pgettext("backup", "Note"), c.get("note", "")), (_("Labels"), ", ".join(c.get("labels") or [])),
                     (_("Pinned"), _("yes") if c.get("pinned") else _("no")), (_("Serial number"), c.get("serial", "–")),
                     (_("Readable by the web editor"), _("yes") if r.known else _("no (different inventory)"))):
            lines.append(f"<tr><td><b>{k}</b></td><td>{v}</td></tr>")
        lines.append("</table>")
        if r.settings:
            lines.append("<h4>" + _("Settings") + "</h4><table>" + "".join(
                f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in r.settings.items()) + "</table>")
        if r.warnings:
            lines.append("<p>" + "<br>".join(r.warnings) + "</p>")
        self.infov.setHtml("".join(lines))
        self._check_changed()
        self._update_view_button()

    # ------------------------------------------------- View project
    def _cur_project(self):
        it = self.content.currentItem()
        oid = it.data(0, Qt.ItemDataRole.UserRole) if it else None
        o = self.rep.obj(oid) if self.rep and oid is not None else None
        return o if o is not None and o.used and (o.id == 0 or o.slot) else None

    def _update_view_button(self):
        win = self.window_
        ok = win is not None and win.page("project_view") is not None
        self.b_view.setVisible(ok)
        self.b_view.setEnabled(ok and self._cur_project() is not None)

    def view_project(self) -> bool:
        o = self._cur_project()
        win = self.window_
        if o is None or win is None or win.page("project_view") is None:
            return False
        dev = (self.rep.companion or {}).get("device") or (self.rep.path.parent.name if self.rep.path else "Backup")
        win.go("project_view", data=o.data, title=f"{dev} · {o.title} ({fmt_date(self.rep.created)})",
               firmware=self.rep.firmware)
        return True

    def _fill_presets(self):
        self.ptable.setSortingEnabled(False)
        self.ptable.clear()
        if not self.rep:
            return
        q = self.p_search.text().lower().split()
        allp = self.rep.presets
        n = 0
        for p in allp:
            if self.p_used.isChecked() and not p.used:
                continue
            hay = f"{p.label} {p.name} {p.engine_name}".lower()
            if any(w not in hay for w in q):
                continue
            o = self.rep.obj(p.oid)
            it = QTreeWidgetItem([p.label, p.name if p.used else "–", p.engine_name if p.used else "",
                                  "★" if p.favorite else "", f"{o.title if o else ''} ({p.oid})"])
            it.setData(0, Qt.ItemDataRole.UserRole, p.label)
            if p.used and not p.known_format:
                it.setToolTip(1, _("older record format (on the device: OLD FORMAT)"))
            self.ptable.addTopLevelItem(it)
            n += 1
        self.ptable.setSortingEnabled(True)
        self.ptable.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        used = len(self.rep.used_presets)
        self.p_info.setText(_("{used} of {total} slots used, {favorites} favourites · {shown} shown").format(
            used=used, total=len(allp), favorites=sum(1 for p in allp if p.favorite), shown=n) if allp else
            _("No preset banks recognised (different firmware or older format)."))

    # ------------------------------------------------------ Selection
    def selected_ids(self) -> list[int]:
        out = []
        for i in range(self.content.topLevelItemCount()):
            g = self.content.topLevelItem(i)
            for j in range(g.childCount()):
                it = g.child(j)
                if it.flags() & Qt.ItemFlag.ItemIsUserCheckable and it.checkState(0) == Qt.CheckState.Checked:
                    out.append(it.data(0, Qt.ItemDataRole.UserRole))
        return out

    def select_ids(self, ids):
        ids = set(ids)
        for i in range(self.content.topLevelItemCount()):
            g = self.content.topLevelItem(i)
            for j in range(g.childCount()):
                it = g.child(j)
                if it.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                    it.setCheckState(0, Qt.CheckState.Checked if it.data(0, Qt.ItemDataRole.UserRole) in ids
                                     else Qt.CheckState.Unchecked)
        self._check_changed()

    def _check_changed(self, *_args):
        ids = self.selected_ids()
        self.b_restore.setEnabled(bool(ids) and bool(self.rep and self.rep.restorable))
        self.sel_label.setText(ngettext("{n} part selected", "{n} parts selected", len(ids)).format(n=len(ids)) if ids
                               else _("Check parts in the tab “Content”."))

    # ---------------------------------------------------- Restore
    def restore_selected(self) -> bool:
        if not self.rep:
            return False
        ids = self.selected_ids()
        if not ids:
            show_error(self, _("Nothing selected"), _("Please check the parts to restore in the tab “Content”."))
            return False
        comp = self.rep.companion
        label = self.rep.path.name if self.rep.path else (comp.get("device") or "Backup")
        ok = selective_restore(self.ctx, self, self.rep, ids, label)
        self._fill_timeline()
        return ok

    # ------------------------------------------------------- Comparison
    def compare(self):
        if not self.rep:
            return
        key = self.cmp_with.currentData()
        if key == LIVE:
            if not self.ctx.require(self):
                return
            version = self.ctx.info.version if self.ctx.info else ""

            def work(w, link):
                w.log.emit(_("Reading the live state (into memory only, no file) …"))
                return backup.capture_live(link, version, progress=lambda a, b: w.progress.emit(a, b),
                                           companion={"device": self.ctx.profile.name if self.ctx.profile else "",
                                                      "firmware": self.ctx.firmware_key})
            d = self.ctx.job(self, _("Read live state"), work, close_on_success=True,
                             done_text=_("Live state read (in memory only, no file, nothing changed on the FM-1)."))
            if not d.ok:
                return
            self.compare_with(BI.inspect(d.result_value), _("Live"))
        elif key:
            try:
                self.compare_with(BI.inspect(Path(key)), Path(key).name)
            except Exception as e:  # noqa: BLE001
                show_error(self, _("Backup not readable"), str(e))

    def compare_with(self, other: BI.Report, label: str = ""):
        self.other = other
        self.cmp_label = label
        self._fill_compare()

    def _fill_compare(self):
        self.cmp_tree.clear()
        self.last_diff = None
        if not self.rep or not self.other:
            return
        why = BI.comparable(self.rep, self.other)
        if why:
            self.cmp_sum.setText(f"<b>{why}</b>")
            return
        # direction: from the backup (old) to the comparison
        d = BI.diff(self.rep, self.other)
        self.last_diff = d
        self.cmp_sum.setText(f"<b>{d.summary()}</b>")
        changed = {c.id: c for c in d.objects}
        ids = [o.id for o in self.rep.objects] + [o.id for o in self.other.objects if not self.rep.obj(o.id)]
        for oid in ids:
            a, b = self.rep.obj(oid), self.other.obj(oid)
            ch = changed.get(oid)
            if self.cmp_only.isChecked() and not ch:
                continue
            title = (a or b).title
            it = QTreeWidgetItem([title, fmt_size(a.size) if a and a.used else (_("empty") if a else "–"),
                                  fmt_size(b.size) if b and b.used else (_("empty") if b else "–"),
                                  BI.kind_text(ch.kind if ch else BI.K_SAME)])
            it.setData(0, Qt.ItemDataRole.UserRole, oid)
            if ch:
                it.setIcon(3, icon("emblem-important", "dialog-warning"))
            for pc in [p for p in d.presets if a and any(x.label == p.label for x in a.presets)]:
                it.addChild(QTreeWidgetItem([pc.label, pc.old.name if pc.old else "–", pc.new.name if pc.new else "–",
                                             pc.text()]))
            self.cmp_tree.addTopLevelItem(it)
        self.cmp_tree.resizeColumnToContents(0)

    def _select_changed(self):
        d = getattr(self, "last_diff", None)
        if d is None:
            show_error(self, _("No comparison yet"), _("First choose a second state above and press “Compare”."))
            return
        if d.identical:
            show_error(self, _("No differences"), _("Both states are the same – there is nothing to select."))
            return
        self.select_ids([i for i in d.changed_ids() if self.rep.obj(i)])
        self.tabs.setCurrentIndex(0)

    # ---------------------------------------------------------- Export
    def _export(self):
        if not self.rep:
            return
        ids = self.selected_ids()
        labels = {it.data(0, Qt.ItemDataRole.UserRole) for it in self.ptable.selectedItems()}
        if not ids and not labels:
            show_error(self, _("Nothing selected"), _("Check parts in the tab “Content” or select presets in the list."))
            return
        dest = QFileDialog.getExistingDirectory(self, _("Export to"), str(self.ctx.settings.root))
        if not dest:
            return
        try:
            n, names = export_parts(self.rep, ids, labels, Path(dest), self.ctx.settings.projects_dir)
        except OSError as e:
            from ..common import fs_error_text
            show_error(self, _("Not exported"), fs_error_text(e, _("export not possible")))
            return
        if not n:
            show_error(self, _("Nothing exported"), _("The checked parts are empty – there is nothing to export."))
            return
        has_proj = any(x.endswith(projects.SUFFIX) for x in names)
        self.ctx.message.emit(ngettext("{n} file exported to {folder}", "{n} files exported to {folder}", n).format(
            n=n, folder=dest))
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox(QMessageBox.Icon.Information, _("Exported"),
                    ngettext("{n} file is in {folder}:", "{n} files are in {folder}:", n).format(n=n, folder=dest)
                    + "<br>" + "<br>".join(names[:12])
                    + ("<br>… " + _("and {n} more").format(n=len(names) - 12) if len(names) > 12 else "")
                    + ("<br><br>" + _("The projects are also in the project library (Projects › Slots & Library).")
                       if has_proj else ""), parent=self).exec()

    # ------------------------------------------------------ Metadata
    def _cur_path(self) -> Path | None:
        return self.rep.path if self.rep and self.rep.path else None

    def _note(self):
        p = self._cur_path()
        if not p:
            return
        txt, ok = QInputDialog.getText(self, pgettext("backup", "Note"), _("Note for the backup:"), text=self.rep.companion.get("note", ""))
        if ok:
            self._set_meta(p, note=txt)

    def _labels(self):
        p = self._cur_path()
        if not p:
            return
        txt, ok = QInputDialog.getText(self, _("Labels"), _("Labels, separated by commas\n"
                                                            "(e.g. before firmware change, live set October):"),
                                       text=", ".join(self.rep.companion.get("labels") or []))
        if ok:
            self._set_meta(p, labels=[x.strip() for x in txt.split(",") if x.strip()])

    def _pin(self):
        p = self._cur_path()
        if p:
            self._set_meta(p, pinned=not self.rep.companion.get("pinned"))

    def _set_meta(self, p: Path, **kw):
        try:
            AB.set_meta(p, **kw)
            what = {"note": _("note saved"), "labels": _("labels saved"),
                    "pinned": _("pinned – never deleted automatically") if kw.get("pinned") else _("no longer pinned")}
            self.ctx.message.emit(f"{p.name}: " + ", ".join(what[k] for k in kw if k in what))
        except (OSError, ValueError, backup.BackupError) as e:
            from ..common import fs_error_text
            show_error(self, _("Not saved"), fs_error_text(e, _("{name} cannot be changed").format(name=p.name))
                       if isinstance(e, OSError) else f"{p.name}: {e}")
        self._reload(p)

    def _reload(self, p):
        self.open_path(p)
        self._fill_timeline()

    def _zip_export(self):
        paths = [m.path for m in self.metas]
        if not paths:
            show_error(self, _("No backups"), _("There are no backups to export here yet."))
            return
        dest, _filter = QFileDialog.getSaveFileName(self, _("Backups as zip"), str(Path.home() / "FM-1-Backups.zip"),
                                                    _("Zip (*.zip)"))
        if not dest:
            return
        root = self.ctx.settings.backups_dir

        def work(w):
            w.log.emit(_("Packing {n} backups …").format(n=len(paths)))
            return AB.export_zip(paths, Path(dest), root)
        from .. import feedback
        feedback.run(self, _("Backups as zip"), work,
                     done=lambda n: _("{n} backups packed with table of contents and checksums into {name}. "
                                      "The originals stay where they are.").format(n=n, name=Path(dest).name))

    def _zip_import(self):
        src, _filter = QFileDialog.getOpenFileName(self, _("Import backup zip"), str(Path.home()), _("Zip (*.zip)"))
        if not src:
            return
        try:
            got, skipped = AB.import_zip(Path(src), self.ctx.settings.backups_dir)
        except OSError as e:
            from ..common import fs_error_text
            show_error(self, _("Zip not taken over"), fs_error_text(e, _("import not possible")))
            return
        except Exception as e:  # noqa: BLE001
            show_error(self, _("Zip not readable"), f"{str(e) or type(e).__name__}<br><br>" + _("Nothing was taken over."))
            return
        from PyQt6.QtWidgets import QMessageBox
        text = (ngettext("{n} backup taken over into the backup folder.", "{n} backups taken over into the backup folder.",
                         len(got)).format(n=len(got)) if got else _("No backup taken over."))
        if skipped:
            text += "<br><br>" + _("{n} skipped:").format(n=len(skipped)) + "<br>" + "<br>".join(skipped[:15]) + \
                    ("<br>… " + _("and {n} more").format(n=len(skipped) - 15) if len(skipped) > 15 else "")
        QMessageBox(QMessageBox.Icon.Information if got else QMessageBox.Icon.Warning, _("Zip imported"), text,
                    parent=self).exec()
        self._fill_devices()


# backup kinds (keys stored in the backup files) -> display text
AB_KIND = {"manuell": N_("manual"), "auto": N_("automatic"), "vor-Update": N_("before firmware change"),
           "sicherheit": N_("safety")}


def kind_text(kind: str) -> str:
    return _(AB_KIND[kind]) if kind in AB_KIND else kind


def export_parts(rep: BI.Report, ids, preset_labels, dest: Path, projects_dir: Path | None = None) -> tuple[int, list[str]]:
    """Objects raw (.bin), projects as .fm1proj (also into the library), presets as .json.
    -> (count, file names in the target folder). OSError if the folder is not writable."""
    dest.mkdir(parents=True, exist_ok=True)
    dev = rep.companion.get("device", "") or "FM-1"
    names: list[str] = []
    for oid in ids:
        o = rep.obj(oid)
        if not o or not o.used:
            continue
        if o.slot or o.id == 0:
            src = f"Backup {rep.path.name if rep.path else ''}, " + (_("slot {n}").format(n=o.slot) if o.slot
                                                                     else _("running music"))
            for folder in filter(None, (dest, projects_dir)):
                p = projects.save_project(folder, f"{dev} {o.title}", o.data, rep.firmware, dev, source=src)
                if folder == dest:
                    names.append(p.name)
        else:
            safe = o.title.replace("/", "-").replace(" ", "_")
            p = dest / f"{dev}_{oid:02d}_{safe}.bin"
            p.write_bytes(o.data)
            names.append(p.name)
    sel = [p for p in rep.presets if p.used and p.label in set(preset_labels)]
    if sel:
        out = {"format": "fm1companion-presets", "version": 1, "firmware": rep.firmware, "device": dev,
               "presets": [{"slot": p.label, "name": p.name, "engine": p.engine, "engine_name": p.engine_name,
                            "favorite": p.favorite, "record_version": p.version,
                            "record": base64.b64encode(p.raw).decode()} for p in sel]}
        p = dest / f"{dev}_Presets_{len(sel)}.json"
        p.write_text(json.dumps(out, indent=1, ensure_ascii=False), "utf-8")
        names.append(p.name)
    return len(names), names


def _make(ctx, window):
    return BackupBrowserPage(ctx, window)


PAGE = PageSpec(key="backup_browser", title=_("Backup Browser"), group="daten", factory=_make,
                icons=("document-preview", "system-search", "backup"), order=20,
                description=_("look inside backups, compare them, restore single parts"),
                keywords=("backup", "diff", "vergleich", "presets", "wiederherstellen", "zip",
                          "compare", "restore", "browse", "inspect"))
