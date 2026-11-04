# SPDX-License-Identifier: GPL-3.0-only
"""Geräte: alle bekannten FM-1, vergleichen und Inhalte von einem FM-1 aufs andere kopieren."""
from __future__ import annotations

import shutil
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
from ...i18n import _


def profile_folder(ctx, prof) -> Path:
    return safe_folder("Backups")[0] / prof.folder_name


def _fw_text(spec, p) -> str:
    """eine Firmware-Angabe statt „Melodee v1.6.0-rc.7 Melodee 1.6.0-rc.7“: Name + Version, dazu die gemeldete
    Kennung nur, wenn sie etwas Neues sagt."""
    name = spec.name if spec else (p.firmware or "unbekannt")
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
        except Exception:                          # noqa: BLE001 – kaputte Datei überspringen
            continue
    return None


def firmware_history(ctx, prof) -> list[tuple[str, str]]:
    """(Datum, Firmware) in zeitlicher Folge – aus den Backups, jeweils beim Wechsel."""
    out = []
    for m in sorted(AB.list_backups(profile_folder(ctx, prof)), key=lambda m: m.when):
        fw = m.firmware or m.fw_key
        if fw and (not out or out[-1][1] != fw):
            out.append((m.when.astimezone().strftime("%d.%m.%Y"), fw))
    return out


class PartsDialog(QDialog):
    """Was soll kopiert werden?"""

    def __init__(self, parent, rep: BI.Report, allowed: set[int]):
        super().__init__(parent)
        self.setWindowTitle("Was kopieren?")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"Aus dem Stand vom {fmt_date(rep.created)} ({rep.firmware}):"))
        proj = [o for o in rep.used_projects if o.id in allowed]
        banks = [o for o in rep.objects if o.group == "Presets" and o.id in allowed]
        self.groups = {
            "projects": (QCheckBox(f"Projekte ({len(proj)} belegte Plätze; die Plätze am Ziel werden ersetzt)"),
                         [o.id for o in proj], True),
            "presets": (QCheckBox(f"alle User-Presets ({len(rep.used_presets)} belegt, {len(banks)} Banken)"),
                        [o.id for o in banks], True),
            "settings": (QCheckBox("Einstellungen (Farbe, Start-Projekt, Kalibrierung …)"),
                         [1] if 1 in allowed else [], False),
            "live": (QCheckBox("laufende Musik"), [0] if 0 in allowed else [], False)}
        for cb, ids, on in self.groups.values():
            cb.setChecked(on and bool(ids))
            cb.setEnabled(bool(ids))
            lay.addWidget(cb)
        hint = QLabel("Vorher wird das Ziel gesichert, und du siehst genau, was überschrieben wird.")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        german_buttons(bb)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("Weiter …")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def ids(self) -> list[int]:
        return [i for cb, ids, _ in self.groups.values() if cb.isChecked() for i in ids]


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
        lay.addWidget(heading("Geräte", "Alle FM-1, die die App kennt – mit Firmware-Verlauf und Backups. "
                                        "Zwei Geräte vergleichen und Projekte/Presets von einem aufs andere kopieren."))
        self.cards = QGridLayout()
        lay.addLayout(self.cards)

        # Vergleichen / Kopieren
        tool = Card("Vergleichen und kopieren")
        r = QHBoxLayout()
        self.a = QComboBox()
        self.b = QComboBox()
        r.addWidget(QLabel("Gerät A:"))
        r.addWidget(self.a, 1)
        r.addWidget(QLabel("Gerät B:"))
        r.addWidget(self.b, 1)
        b_cmp = QPushButton(icon("view-split-left-right", "document-compare"), "Vergleichen")
        b_cmp.clicked.connect(lambda: self.compare(self.a.currentData(), self.b.currentData()))
        r.addWidget(b_cmp)
        self.b_cmp = b_cmp
        for combo in (self.a, self.b):
            combo.currentIndexChanged.connect(self._cmp_enable)
        self.a.currentIndexChanged.connect(lambda *_: self._copy_enable())
        tool.lay.addLayout(r)
        self.cmp_tree = QTreeWidget()
        self.cmp_tree.setHeaderLabels(["", "Gerät A", "Gerät B"])
        self.cmp_tree.setMinimumHeight(220)
        tool.lay.addWidget(self.cmp_tree)
        r2 = QHBoxLayout()
        self.b_copy = QPushButton(icon("edit-copy", "go-next"), "Von A aufs verbundene FM-1 kopieren …")
        self.b_copy.setToolTip("Quelle: das neueste Backup von A (vorher frisch sichern, wenn A angeschlossen ist)")
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

    # ------------------------------------------------------------ Karten
    def refresh(self):
        while self.cards.count():
            w = self.cards.takeAt(0).widget()
            if w:
                w.hide()                       # sofort weg (deleteLater kommt erst später)
                w.setParent(None)
                w.deleteLater()
        c = self.ctx
        connected = {pr.id for pr in (c.profile_for_port(x) for x in c.ports) if pr}
        profs = sorted(c.store.profiles.values(), key=lambda p: p.name.lower())
        if not profs:
            self.cards.addWidget(QLabel("Noch keine Geräte – so geht's: ein FM-1 per USB anschließen. "
                                        "Die App legt dann ein Profil an."), 0, 0)
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
            self.b.blockSignals(True)               # nicht zweimal dasselbe Gerät vorschlagen
            self.b.setCurrentIndex(next(i for i in range(self.b.count()) if self.b.itemData(i) != self.a.currentData()))
            self.b.blockSignals(False)
        if hasattr(self, "b_cmp"):
            self._cmp_enable()
        tgt = c.profile
        self.copy_info.setText(f"Ziel ist immer das verbundene Gerät: <b>{tgt.name}</b> ({firmware_name(c.firmware_key)})" if tgt and c.connected
                               else "Zum Kopieren das Ziel-FM-1 anschließen (Firmware mit Editor-Protokoll).")
        self._copy_enable()
        if self.cmp_tree.topLevelItemCount() == 0 or getattr(self, "_cmp_placeholder", False):
            self.cmp_tree.clear()
            self._cmp_placeholder = True
            self.cmp_tree.addTopLevelItem(QTreeWidgetItem([
                "Noch kein Vergleich. " + ("Zwei verschiedene Geräte oben wählen und „Vergleichen“ drücken – verglichen "
                                           "wird jeweils das neueste Backup." if len(profs) > 1 else
                                           "Dafür braucht es ein zweites bekanntes FM-1 – einfach anschließen, die App "
                                           "legt ein Profil an.")]))
            self.cmp_tree.topLevelItem(0).setFirstColumnSpanned(True)

    def _copy_enable(self, *_):
        c = self.ctx
        tgt = c.profile
        src = self.a.currentData()
        if not (tgt and c.connected):
            ok, tip = False, "Erst möglich, wenn ein FM-1 mit Editor-Protokoll (Melodee, Skiff, SLOOP) verbunden ist."
        elif src == tgt.id:
            ok, tip = False, ("Gerät A ist das verbundene FM-1 selbst – als Quelle ein anderes Gerät wählen. Für ältere "
                              "Stände desselben Geräts: Backups › Durchsuchen & Vergleichen.")
        else:
            ok, tip = True, "Quelle: das neueste Backup von A – Ziel: das verbundene FM-1. Vorher wird das Ziel gesichert."
        self.b_copy.setEnabled(ok)
        self.b_copy.setToolTip(tip)

    def _card(self, p, attached: bool) -> QWidget:
        c = self.ctx
        active = c.profile is not None and c.profile.id == p.id
        card = Card(p.name)
        metas = AB.list_backups(profile_folder(c, p))
        spec = firmware.spec(p.firmware)
        state = "aktiv" if active else ("angeschlossen" if attached else "nicht angeschlossen")
        hist = firmware_history(c, p)
        lines = [f"<b style='color:{p.color}'>●</b> {state}",
                 "Firmware: " + _fw_text(spec, p),
                 "Verlauf: " + (" → ".join(f"{fw} ({d})" for d, fw in hist[-4:]) if hist else "–"),
                 f"Zuletzt gesehen: {time.strftime('%d.%m.%Y %H:%M', time.localtime(p.last_seen)) if p.last_seen else '–'}",
                 f"Backups: {len(metas)} ({fmt_size(sum(m.size for m in metas))})"
                 + (f", neuestes {metas[0].when.astimezone().strftime('%d.%m.%Y %H:%M')}" if metas else ""),
                 f"Seriennummer: {p.serial or 'keine'} · USB-Anschluss: {p.usb_path or '–'}"]
        if p.notes:
            lines.append(f"<i>{p.notes}</i>")
        lab = QLabel("<br>".join(lines))
        lab.setWordWrap(True)
        lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        card.lay.addWidget(lab)
        row = FlowLayout()
        for ic, txt, fn in ((("edit-rename",), "Umbenennen", lambda _=False, x=p: self.rename(x.id)),
                            (("color-management", "preferences-desktop-color"), "Farbe", lambda _=False, x=p: self._color(x)),
                            (("document-edit",), "Notiz", lambda _=False, x=p: self._notes(x)),
                            (("document-preview", "backup"), "Backups", lambda _=False, x=p: self._backups(x)),
                            (("merge", "edit-copy"), "Zusammenführen …", lambda _=False, x=p: self._merge(x)),
                            (("edit-delete",), "Vergessen", lambda _=False, x=p: self.forget(x.id))):
            b = QPushButton(icon(*ic), txt)
            b.clicked.connect(fn)
            if txt == "Vergessen":
                b.setEnabled(not active)
                b.setToolTip("Das aktive Gerät lässt sich nicht vergessen – erst oben links ein anderes wählen."
                             if active else "Profil entfernen (die Backups bleiben im Ordner)")
            elif txt == "Zusammenführen …":
                b.setToolTip("Wenn dieses Profil in Wirklichkeit dasselbe FM-1 wie ein anderes ist: Backups "
                             "zusammenlegen, dieses Profil vergessen.")
            elif txt == "Backups":
                b.setToolTip("Die Backups dieses Geräts durchsuchen und vergleichen.")
            row.addWidget(b)
        card.lay.addLayout(row)
        card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        card.setMinimumWidth(240)
        card.setStyleSheet(f"Card {{ border-left: 4px solid {p.color}; }}")
        return card

    # ------------------------------------------------------------ Profile
    def rename(self, pid: str, name: str | None = None):
        p = self.ctx.store.profiles.get(pid)
        if not p:
            return
        if name is None:
            name, ok = QInputDialog.getText(self, "Gerät umbenennen", "Name:", text=p.name)
            if not ok:
                return
        name = name.strip()
        if not name or name == p.name:
            return
        try:                                       # Backups wandern mit (gemeinsame Logik mit der Übersicht)
            self.ctx.rename_profile(name, profile=p)
        except OSError as e:
            from ..common import show_error
            show_error(self, "Umbenennen nicht möglich", str(e))

    def _color(self, p):
        col = QColorDialog.getColor(QColor(p.color), self, f"Farbe für {p.name}")
        if col.isValid():
            p.color = col.name()
            self.ctx.store.save()
            self.ctx.changed.emit()

    def _notes(self, p):
        txt, ok = QInputDialog.getMultiLineText(self, "Notiz", f"Notiz zu {p.name}:", p.notes)
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
        if ask and not confirm(self, "Gerät vergessen", f"Profil <b>{p.name}</b> entfernen (Name, Farbe, Notiz, "
                                                        f"Firmware-Verlauf)? Die Backups bleiben in "
                                                        f"{profile_folder(self.ctx, p)}."
                                                        + ("<br><br>Das Gerät ist gerade angeschlossen – sobald es "
                                                           "aktiv wird, legt die App ein neues Profil dafür an."
                                                           if attached else ""), "Vergessen", danger=True):
            return False
        self.ctx.store.delete(pid)
        self.refresh()
        return True

    def _merge(self, p):
        others = [x for x in self.ctx.store.profiles.values() if x.id != p.id]
        if not others:
            show_error(self, "Zusammenführen", "Es gibt kein zweites Profil.")
            return
        names = [x.name for x in others]
        item, ok = QInputDialog.getItem(self, "Zusammenführen", f"„{p.name}“ ist dasselbe Gerät wie:", names, 0, False)
        if ok:
            self.merge(p.id, others[names.index(item)].id)

    def merge(self, src_id: str, dst_id: str) -> bool:
        """zwei Profile desselben Geräts zusammenlegen: Backups von src nach dst, src vergessen."""
        st = self.ctx.store
        src, dst = st.profiles.get(src_id), st.profiles.get(dst_id)
        if not src or not dst or (self.ctx.profile and self.ctx.profile.id == src_id):
            show_error(self, "Zusammenführen", "Das aktive Gerät kann nicht in ein anderes aufgehen – "
                                               "führe umgekehrt zusammen.")
            return False
        sf, df = profile_folder(self.ctx, src), profile_folder(self.ctx, dst)
        if sf.exists():
            df.mkdir(parents=True, exist_ok=True)
            for f in sf.rglob("*.json"):
                target = df / f.name
                if not target.exists():
                    shutil.move(str(f), target)
            shutil.rmtree(sf, ignore_errors=True)
        dst.serial = dst.serial or src.serial
        dst.notes = "\n".join(x for x in (dst.notes, src.notes) if x)
        dst.last_seen = max(dst.last_seen, src.last_seen)
        st.delete(src_id)
        self.ctx.changed.emit()
        self.refresh()
        return True

    # ----------------------------------------------------------- Vergleich
    def _cmp_enable(self, *_):
        a, b = self.a.currentData(), self.b.currentData()
        same = bool(a) and a == b
        self.b_cmp.setEnabled(bool(a) and bool(b) and not same)
        self.b_cmp.setToolTip("Gerät A und B sind dasselbe – bitte zwei verschiedene Geräte wählen." if same
                              else "Neueste Backups von A und B nebeneinander vergleichen")

    def compare(self, a_id: str, b_id: str) -> BI.DeviceComparison | None:
        st = self.ctx.store
        pa, pb = st.profiles.get(a_id), st.profiles.get(b_id)
        if not pa or not pb:
            return None
        if a_id == b_id:
            show_error(self, "Vergleichen", "Gerät A und Gerät B sind dasselbe FM-1 – wähle zwei verschiedene Geräte.")
            return None
        ra, rb = newest_report(self.ctx, pa), newest_report(self.ctx, pb)
        res = BI.compare_devices(ra, rb, pa.version_string or pa.firmware, pb.version_string or pb.firmware)
        self.cmp_tree.clear()
        self._cmp_placeholder = False
        self.cmp_tree.setHeaderLabels(["", pa.name, pb.name])
        for k, x, y in res.rows:
            if k == "Stand vom":
                x, y = fmt_date(x) if ra else x, fmt_date(y) if rb else y
            it = QTreeWidgetItem([k, x, y])
            if x != y and k != "Stand vom":
                it.setIcon(0, icon("emblem-important", "dialog-information"))
            self.cmp_tree.addTopLevelItem(it)
        for title, names in ((f"Presets nur auf {pa.name}", res.only_a), (f"Presets nur auf {pb.name}", res.only_b),
                             ("Presets auf beiden", res.both)):
            g = QTreeWidgetItem([f"{title} ({len(names)})"])
            for n in names:
                g.addChild(QTreeWidgetItem(["", n if names is not res.only_b else "", n if names is not res.only_a else ""]))
            self.cmp_tree.addTopLevelItem(g)
        if not ra or not rb:
            self.cmp_tree.addTopLevelItem(QTreeWidgetItem(["Hinweis", "Vergleich nutzt jeweils das neueste Backup – "
                                                           "für ein Gerät ohne Backup: anschließen und Strg+B."]))
        self.cmp_tree.resizeColumnToContents(0)
        return res

    # ------------------------------------------------------------- Kopie
    def copy(self, src_id: str, ids: list[int] | None = None, source: Path | None = None) -> tuple[BI.Compat | None, bool]:
        """Inhalte von Gerät src (neuestes Backup oder `source`) aufs verbundene Gerät spielen."""
        c = self.ctx
        if not c.require(self):
            return None, False
        src = c.store.profiles.get(src_id)
        if not src:
            return None, False
        if c.profile and c.profile.id == src_id:
            show_error(self, "Gleiches Gerät", "Quelle und Ziel sind dasselbe FM-1. Für ältere Stände desselben Geräts "
                                               "nimm Backups › Durchsuchen & Vergleichen.")
            return None, False
        try:
            rep = BI.inspect(source) if source else newest_report(c, src)
        except Exception as e:  # noqa: BLE001
            show_error(self, "Backup nicht lesbar", str(e))
            return None, False
        if rep is None:
            show_error(self, "Kein Backup", f"Von <b>{src.name}</b> gibt es noch kein Backup. Schließe es an und mach "
                                            "eins (Strg+B) – das ist die Quelle für die Kopie.")
            return None, False

        def inv(w, link):
            from ... import backup
            return [o.id for o in backup.list_objects(link)]
        d = c.job(self, "Ziel prüfen", inv)
        if not d.ok:
            return None, False
        comp = BI.compatibility(rep, c.firmware_key, c.info.version if c.info else "", d.result_value)
        if not comp.ok:
            show_error(self, "Kopieren nicht möglich",
                       f"<b>{src.name} → {c.profile.name}</b><br><br>{comp.reason}" +
                       ("<br><br>Was geht:<ul>" + "".join(f"<li>{x}</li>" for x in comp.possible) + "</ul>"
                        if comp.possible else ""))
            self.copy_info.setText("Nicht kompatibel: " + comp.reason)
            return comp, False
        if ids is None:
            dlg = PartsDialog(self, rep, comp.allowed_ids)
            if not dlg.exec():
                return comp, False
            ids = dlg.ids()
        ids = [i for i in ids if i in comp.allowed_ids]
        if not ids:
            show_error(self, "Nichts ausgewählt", "Es wurde nichts zum Kopieren ausgewählt.")
            return comp, False
        ok = selective_restore(c, self, rep, ids, f"{src.name} ({fmt_date(rep.created)})")
        self.copy_info.setText(f"Kopiert von {src.name}: {len(ids)} Teile." if ok else comp.reason)
        self.refresh()
        return comp, ok


def _make(ctx, window):
    return DevicesPage(ctx, window)


PAGE = PageSpec(key="devices", title=_("Devices"), group="geraet", factory=_make,
                icons=("computer", "audio-card", "drive-removable-media"), order=50,
                description=_("all FM-1: profiles, firmware history, compare, copy from device to device"),
                keywords=("profil", "zweites fm-1", "kopieren", "vergleich", "seriennummer",
                          "profile", "second fm-1", "copy", "compare", "serial number", "devices"))
