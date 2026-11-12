# SPDX-License-Identifier: GPL-3.0-only
"""Devices: all known FM-1s, compare them and copy content from one FM-1 to another."""
from __future__ import annotations

import time
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QGridLayout,
                             QHBoxLayout, QInputDialog, QLabel, QPushButton, QScrollArea, QSizePolicy, QTreeWidget,
                             QTreeWidgetItem, QVBoxLayout, QWidget)

from ... import autobackup as AB, backup_inspect as BI, firmware
from ..device import firmware_name
from ..common import (Card, FlowLayout, confirm, fmt_date, fmt_size, fw_label, german_buttons, heading, icon, safe_folder,
                      show_error)
from . import PageSpec
from .backup_browser import selective_restore
from ...i18n import _, ngettext, pgettext


def profile_folder(ctx, prof) -> Path:
    return safe_folder("Backups")[0] / prof.folder_name


def _fw_text(spec, p) -> str:
    """one firmware label instead of “Melodee v1.6.0-rc.7 Melodee 1.6.0-rc.7”: name + version, plus the reported
    identifier only if it says something new."""
    name = spec.name if spec else (p.firmware or _("unknown"))
    ver = (p.firmware_version or "").strip()
    reported = fw_label(p.version_string or "").strip()
    text = f"<b>{name}</b>" + (f" {ver}" if ver else "")
    plain = f"{name} {ver.lstrip('vV')}".strip().lower()
    if reported and reported.lower() != plain and reported.lower() not in plain:
        text += f" <span style='color:gray'>({reported})</span>"
    return text


def newest_report(ctx, prof) -> BI.Report | None:
    for m in AB.list_backups(profile_folder(ctx, prof)):
        try:
            return BI.inspect(m.path)
        except Exception:                          # noqa: BLE001 – skip broken file
            continue
    return None


def firmware_history(ctx, prof) -> list[tuple[str, str]]:
    """(date, firmware) in chronological order – from the backups, at each change."""
    out = []
    for m in sorted(AB.list_backups(profile_folder(ctx, prof)), key=lambda m: m.when):
        fw = m.firmware or m.fw_key
        if fw and (not out or out[-1][1] != fw):
            out.append((m.when.astimezone().strftime("%d.%m.%Y"), fw))
    return out


class PartsDialog(QDialog):
    """What should be copied?"""

    def __init__(self, parent, rep: BI.Report, allowed: set[int]):
        super().__init__(parent)
        self.setWindowTitle(_("What to copy?"))
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(_("From the state of {date} ({firmware}):").format(date=fmt_date(rep.created),
                                                                                firmware=rep.firmware)))
        proj = [o for o in rep.used_projects if o.id in allowed]
        banks = [o for o in rep.objects if o.group == BI.G_PRESETS and o.id in allowed]
        self.groups = {
            "projects": (QCheckBox(ngettext("Projects ({n} used slot; the slots on the target are replaced)",
                                            "Projects ({n} used slots; the slots on the target are replaced)",
                                            len(proj)).format(n=len(proj))),
                         [o.id for o in proj], True),
            "presets": (QCheckBox(_("all user presets ({used} used, {banks} banks)").format(
                used=len(rep.used_presets), banks=len(banks))),
                        [o.id for o in banks], True),
            "settings": (QCheckBox(_("Settings (colour, start project, calibration …)")),
                         [1] if 1 in allowed else [], False),
            "live": (QCheckBox(_("running music")), [0] if 0 in allowed else [], False)}
        for cb, ids, on in self.groups.values():
            cb.setChecked(on and bool(ids))
            cb.setEnabled(bool(ids))
            lay.addWidget(cb)
        hint = QLabel(_("The target is backed up first, and you see exactly what will be overwritten."))
        hint.setWordWrap(True)
        lay.addWidget(hint)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        german_buttons(bb)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText(_("Next …"))
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def ids(self) -> list[int]:
        return [i for cb, ids, _on in self.groups.values() if cb.isChecked() for i in ids]


class DevicesPage(QWidget):
    navigate = pyqtSignal(str)

    def __init__(self, ctx, window=None):
        super().__init__()
        self.ctx = ctx
        self.window_ = window
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        lay = QVBoxLayout(body)
        lay.addWidget(heading(_("Devices"), _("All FM-1 the app knows – with firmware history and backups. "
                                               "Compare two devices and copy projects/presets from one to the other.")))
        self.cards = QGridLayout()
        lay.addLayout(self.cards)

        # Compare / copy
        tool = Card(_("Compare and copy"))
        r = QHBoxLayout()
        self.a = QComboBox()
        self.b = QComboBox()
        r.addWidget(QLabel(_("Device A:")))
        r.addWidget(self.a, 1)
        r.addWidget(QLabel(_("Device B:")))
        r.addWidget(self.b, 1)
        b_cmp = QPushButton(icon("view-split-left-right", "document-compare"), _("Compare"))
        b_cmp.clicked.connect(lambda: self.compare(self.a.currentData(), self.b.currentData()))
        r.addWidget(b_cmp)
        self.b_cmp = b_cmp
        for combo in (self.a, self.b):
            combo.currentIndexChanged.connect(self._cmp_enable)
        self.a.currentIndexChanged.connect(lambda *_a: self._copy_enable())
        tool.lay.addLayout(r)
        self.cmp_tree = QTreeWidget()
        self.cmp_tree.setHeaderLabels(["", _("Device A"), _("Device B")])
        self.cmp_tree.setMinimumHeight(220)
        tool.lay.addWidget(self.cmp_tree)
        r2 = QHBoxLayout()
        self.b_copy = QPushButton(icon("edit-copy", "go-next"), _("Copy from A to the connected FM-1 …"))
        self.b_copy.setToolTip(_("Source: the newest backup of A (back it up fresh first if A is connected)"))
        self.b_copy.clicked.connect(lambda: self.copy(self.a.currentData()))
        r2.addWidget(self.b_copy)
        self.copy_info = QLabel()
        self.copy_info.setWordWrap(True)
        r2.addWidget(self.copy_info, 1)
        tool.lay.addLayout(r2)
        lay.addWidget(tool)
        lay.addStretch(1)
        ctx.changed.connect(self.refresh)
        ctx.ports_changed.connect(self.refresh)
        self.refresh()

    def activate(self):
        self.refresh()

    # ------------------------------------------------------------ Cards
    def refresh(self):
        while self.cards.count():
            w = self.cards.takeAt(0).widget()
            if w:
                w.hide()                       # gone immediately (deleteLater only comes later)
                w.setParent(None)
                w.deleteLater()
        c = self.ctx
        connected = {pr.id for pr in (c.profile_for_port(x) for x in c.ports) if pr}
        profs = sorted(c.store.profiles.values(), key=lambda p: p.name.lower())
        if not profs:
            self.cards.addWidget(QLabel(_("No devices yet – here's how: connect an FM-1 via USB. "
                                          "The app then creates a profile.")), 0, 0)
        for i, p in enumerate(profs):
            self.cards.addWidget(self._card(p, p.id in connected), i // 2, i % 2)
        for combo in (self.a, self.b):
            cur = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            for p in profs:
                combo.addItem(f"{p.name} ({firmware_name(p.firmware) or '?'})", p.id)
            i = combo.findData(cur)
            combo.setCurrentIndex(i if i >= 0 else (1 if combo is self.b and len(profs) > 1 else 0))
            combo.blockSignals(False)
        if self.a.currentData() and self.a.currentData() == self.b.currentData() and len(profs) > 1:
            self.b.blockSignals(True)               # don't suggest the same device twice
            self.b.setCurrentIndex(next(i for i in range(self.b.count()) if self.b.itemData(i) != self.a.currentData()))
            self.b.blockSignals(False)
        if hasattr(self, "b_cmp"):
            self._cmp_enable()
        tgt = c.profile
        self.copy_info.setText(_("The target is always the connected device: <b>{device}</b> ({firmware})").format(
            device=tgt.name, firmware=firmware_name(c.firmware_key)) if tgt and c.connected
                               else _("To copy, connect the target FM-1 (firmware with editor protocol)."))
        self._copy_enable()
        if self.cmp_tree.topLevelItemCount() == 0 or getattr(self, "_cmp_placeholder", False):
            self.cmp_tree.clear()
            self._cmp_placeholder = True
            self.cmp_tree.addTopLevelItem(QTreeWidgetItem([
                _("No comparison yet.") + " " + (
                    _("Choose two different devices above and press “Compare” – the newest backup of each is "
                      "compared.") if len(profs) > 1 else
                    _("This needs a second known FM-1 – simply connect it, the app creates a profile."))]))
            self.cmp_tree.topLevelItem(0).setFirstColumnSpanned(True)

    def _copy_enable(self, *_args):
        c = self.ctx
        tgt = c.profile
        src = self.a.currentData()
        if not (tgt and c.connected):
            ok, tip = False, _("Only possible when an FM-1 with editor protocol (Melodee, Skiff, SLOOP) is connected.")
        elif src == tgt.id:
            ok, tip = False, _("Device A is the connected FM-1 itself – choose another device as the source. For older "
                                "states of the same device: Backups › Browse & Compare.")
        else:
            ok, tip = True, _("Source: the newest backup of A – target: the connected FM-1. The target is backed up first.")
        self.b_copy.setEnabled(ok)
        self.b_copy.setToolTip(tip)

    def _card(self, p, attached: bool) -> QWidget:
        c = self.ctx
        active = c.profile is not None and c.profile.id == p.id
        card = Card(p.name)
        metas = AB.list_backups(profile_folder(c, p))
        spec = firmware.spec(p.firmware)
        state = _("active") if active else (_("plugged in") if attached else _("not plugged in"))
        hist = firmware_history(c, p)
        lines = [f"<b style='color:{p.color}'>●</b> {state}",
                 _("Firmware: {firmware}").format(firmware=_fw_text(spec, p)),
                 _("History: {history}").format(history=" → ".join(f"{fw} ({d})" for d, fw in hist[-4:]) if hist else "–"),
                 _("Last seen: {date}").format(
                     date=time.strftime('%d.%m.%Y %H:%M', time.localtime(p.last_seen)) if p.last_seen else '–'),
                 (_("Backups: {n} ({size}), newest {date}").format(
                     n=len(metas), size=fmt_size(sum(m.size for m in metas)),
                     date=metas[0].when.astimezone().strftime('%d.%m.%Y %H:%M')) if metas else
                  _("Backups: {n} ({size})").format(n=len(metas), size=fmt_size(0))),
                 _("Serial number: {serial} · USB port: {usb}").format(serial=p.serial or _("none"), usb=p.usb_path or '–')]
        if p.notes:
            lines.append(f"<i>{p.notes}</i>")
        lab = QLabel("<br>".join(lines))
        lab.setWordWrap(True)
        lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        card.lay.addWidget(lab)
        row = FlowLayout()
        for key, ic, txt, fn in (("rename", ("edit-rename",), _("Rename"), lambda _c=False, x=p: self.rename(x.id)),
                                 ("color", ("color-management", "preferences-desktop-color"), _("Colour"),
                                  lambda _c=False, x=p: self._color(x)),
                                 ("note", ("document-edit",), pgettext("device", "Note"), lambda _c=False, x=p: self._notes(x)),
                                 ("backups", ("document-preview", "backup"), _("Backups"),
                                  lambda _c=False, x=p: self._backups(x)),
                                 ("merge", ("merge", "edit-copy"), _("Merge …"), lambda _c=False, x=p: self._merge(x)),
                                 ("forget", ("edit-delete",), _("Forget"), lambda _c=False, x=p: self.forget(x.id))):
            b = QPushButton(icon(*ic), txt)
            b.clicked.connect(fn)
            if key == "forget":
                b.setEnabled(not active)
                b.setToolTip(_("The active device cannot be forgotten – choose another one at the top left first.")
                             if active else _("Remove the profile (the backups stay in the folder)"))
            elif key == "merge":
                b.setToolTip(_("If this profile is really the same FM-1 as another one: merge the backups, "
                               "forget this profile."))
            elif key == "backups":
                b.setToolTip(_("Browse and compare the backups of this device."))
            row.addWidget(b)
        card.lay.addLayout(row)
        card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        card.setMinimumWidth(240)
        card.setStyleSheet(f"Card {{ border-left: 4px solid {p.color}; }}")
        return card

    # ------------------------------------------------------------ Profiles
    def rename(self, pid: str, name: str | None = None):
        p = self.ctx.store.profiles.get(pid)
        if not p:
            return
        if name is None:
            name, ok = QInputDialog.getText(self, _("Rename device"), _("Name:"), text=p.name)
            if not ok:
                return
        name = name.strip()
        if not name or name == p.name:
            return
        try:                                       # backups move along (shared logic with the overview)
            self.ctx.rename_profile(name, profile=p)
        except OSError as e:
            from ..common import show_error
            show_error(self, _("Rename not possible"), str(e))

    def _color(self, p):
        col = QColorDialog.getColor(QColor(p.color), self, _("Colour for {device}").format(device=p.name))
        if col.isValid():
            p.color = col.name()
            self.ctx.store.save()
            self.ctx.changed.emit()

    def _notes(self, p):
        txt, ok = QInputDialog.getMultiLineText(self, pgettext("device", "Note"), _("Note on {device}:").format(device=p.name),
                                                  p.notes)
        if ok:
            p.notes = txt
            self.ctx.store.save()
            self.refresh()

    def _backups(self, p):
        win = self.window_
        if win is not None and win.page("backup_browser") is not None:
            win.go("backup_browser")
            page = win.page("backup_browser")
            i = page.dev.findData(str(profile_folder(self.ctx, p)))
            if i >= 0:
                page.dev.setCurrentIndex(i)

    def forget(self, pid: str, ask: bool = True) -> bool:
        p = self.ctx.store.profiles.get(pid)
        if not p or (self.ctx.profile and self.ctx.profile.id == pid):
            return False
        attached = any(pr is not None and pr.id == pid for pr in (self.ctx.profile_for_port(x) for x in self.ctx.ports))
        if ask and not confirm(self, _("Forget device"), _("Remove the profile <b>{device}</b> (name, colour, note, "
                                                           "firmware history)? The backups stay in {folder}.").format(
                                   device=p.name, folder=profile_folder(self.ctx, p))
                               + ("<br><br>" + _("The device is connected right now – as soon as it becomes active, "
                                                 "the app creates a new profile for it.") if attached else ""),
                               _("Forget"), danger=True):
            return False
        self.ctx.store.delete(pid)
        self.refresh()
        return True

    def _merge(self, p):
        others = [x for x in self.ctx.store.profiles.values() if x.id != p.id]
        if not others:
            show_error(self, _("Merge"), _("There is no second profile."))
            return
        names = [x.name for x in others]
        item, ok = QInputDialog.getItem(self, _("Merge"), _("“{device}” is the same device as:").format(device=p.name), names, 0, False)
        if ok:
            self.merge(p.id, others[names.index(item)].id)

    def merge(self, src_id: str, dst_id: str) -> bool:
        """Merge two profiles of the same device: backups from src to dst, forget src."""
        st = self.ctx.store
        src, dst = st.profiles.get(src_id), st.profiles.get(dst_id)
        if not src or not dst or (self.ctx.profile and self.ctx.profile.id == src_id):
            show_error(self, _("Merge"), _("The active device cannot be merged into another one – "
                                                 "merge the other way round."))
            return False
        # every file moves (never overwritten: a second file of the same name gets the old device's name appended),
        # sub-folders and other files (.x0b, notes …) included – the same code as renaming a device
        from ...devices import move_profile_folder
        try:
            move_profile_folder(profile_folder(self.ctx, src).parent, src.folder_name, dst.folder_name)
        except OSError as e:
            show_error(self, _("Merge"), _("The backups of “{device}” could not be moved completely: {error}. "
                                           "Nothing was deleted; both profiles stay.").format(
                                               device=src.name, error=e.strerror or e))
            self.refresh()
            return False
        dst.serial = dst.serial or src.serial
        dst.notes = "\n".join(x for x in (dst.notes, src.notes) if x)
        dst.last_seen = max(dst.last_seen, src.last_seen)
        st.delete(src_id)
        self.ctx.changed.emit()
        self.refresh()
        return True

    # ----------------------------------------------------------- Comparison
    def _cmp_enable(self, *_args):
        a, b = self.a.currentData(), self.b.currentData()
        same = bool(a) and a == b
        self.b_cmp.setEnabled(bool(a) and bool(b) and not same)
        self.b_cmp.setToolTip(_("Device A and B are the same – please choose two different devices.") if same
                              else _("Compare the newest backups of A and B side by side"))

    def compare(self, a_id: str, b_id: str) -> BI.DeviceComparison | None:
        st = self.ctx.store
        pa, pb = st.profiles.get(a_id), st.profiles.get(b_id)
        if not pa or not pb:
            return None
        if a_id == b_id:
            show_error(self, _("Compare"), _("Device A and device B are the same FM-1 – choose two different devices."))
            return None
        ra, rb = newest_report(self.ctx, pa), newest_report(self.ctx, pb)
        res = BI.compare_devices(ra, rb, pa.version_string or pa.firmware, pb.version_string or pb.firmware)
        self.cmp_tree.clear()
        self._cmp_placeholder = False
        self.cmp_tree.setHeaderLabels(["", pa.name, pb.name])
        state_of = _("State of")
        for k, x, y in res.rows:
            if k == state_of:
                x, y = fmt_date(x) if ra else x, fmt_date(y) if rb else y
            it = QTreeWidgetItem([k, x, y])
            if x != y and k != state_of:
                it.setIcon(0, icon("emblem-important", "dialog-information"))
            self.cmp_tree.addTopLevelItem(it)
        for title, names in ((_("Presets only on {device}").format(device=pa.name), res.only_a),
                             (_("Presets only on {device}").format(device=pb.name), res.only_b),
                             (_("Presets on both"), res.both)):
            g = QTreeWidgetItem([f"{title} ({len(names)})"])
            for n in names:
                g.addChild(QTreeWidgetItem(["", n if names is not res.only_b else "", n if names is not res.only_a else ""]))
            self.cmp_tree.addTopLevelItem(g)
        if not ra or not rb:
            self.cmp_tree.addTopLevelItem(QTreeWidgetItem([_("Note"), _("The comparison uses the newest backup of each – "
                                                             "for a device without a backup: connect it and press Ctrl+B.")]))
        self.cmp_tree.resizeColumnToContents(0)
        return res

    # ------------------------------------------------------------- Copy
    def copy(self, src_id: str, ids: list[int] | None = None, source: Path | None = None) -> tuple[BI.Compat | None, bool]:
        """Play content from device src (newest backup or `source`) onto the connected device."""
        c = self.ctx
        if not c.require(self):
            return None, False
        src = c.store.profiles.get(src_id)
        if not src:
            return None, False
        if c.profile and c.profile.id == src_id:
            show_error(self, _("Same device"), _("Source and target are the same FM-1. For older states of the same device "
                                                 "use Backups › Browse & Compare."))
            return None, False
        try:
            rep = BI.inspect(source) if source else newest_report(c, src)
        except Exception as e:  # noqa: BLE001
            show_error(self, _("Backup not readable"), str(e))
            return None, False
        if rep is None:
            show_error(self, _("No backup"), _("There is no backup of <b>{device}</b> yet. Connect it and make "
                                              "one (Ctrl+B) – that is the source for the copy.").format(device=src.name))
            return None, False

        def inv(w, link):
            from ... import backup
            return [o.id for o in backup.list_objects(link)]
        d = c.job(self, _("Check target"), inv)
        if not d.ok:
            return None, False
        comp = BI.compatibility(rep, c.firmware_key, c.info.version if c.info else "", d.result_value)
        if not comp.ok:
            show_error(self, _("Copy not possible"),
                       f"<b>{src.name} → {c.profile.name}</b><br><br>{comp.reason}" +
                       ("<br><br>" + _("What is possible:") + "<ul>" + "".join(f"<li>{x}</li>" for x in comp.possible) + "</ul>"
                        if comp.possible else ""))
            self.copy_info.setText(_("Not compatible: {reason}").format(reason=comp.reason))
            return comp, False
        if ids is None:
            dlg = PartsDialog(self, rep, comp.allowed_ids)
            if not dlg.exec():
                return comp, False
            ids = dlg.ids()
        ids = [i for i in ids if i in comp.allowed_ids]
        if not ids:
            show_error(self, _("Nothing selected"), _("Nothing was selected for copying."))
            return comp, False
        ok = selective_restore(c, self, rep, ids, f"{src.name} ({fmt_date(rep.created)})")
        self.copy_info.setText(ngettext("Copied from {device}: {n} part.", "Copied from {device}: {n} parts.", len(ids)).format(
            device=src.name, n=len(ids)) if ok else comp.reason)
        self.refresh()
        return comp, ok


def _make(ctx, window):
    return DevicesPage(ctx, window)


PAGE = PageSpec(key="devices", title=_("Devices"), group="geraet", factory=_make,
                icons=("computer", "audio-card", "drive-removable-media"), order=50,
                description=_("all FM-1: profiles, firmware history, compare, copy from device to device"),
                keywords=("profil", "zweites fm-1", "kopieren", "vergleich", "seriennummer",
                          "profile", "second fm-1", "copy", "compare", "serial number", "devices"))
