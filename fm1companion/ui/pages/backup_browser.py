# SPDX-License-Identifier: GPL-3.0-only
"""Backup-Browser: in Backups hineinschauen, vergleichen, einzelne Teile zurückspielen und exportieren."""
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

GROUP_ICONS = {"Musik": ("media-playback-start",), "Projekte": ("folder-music", "folder"),
               "Presets": ("audio-x-generic", "preferences-desktop-sound"), "Einstellungen": ("configure", "preferences-system"),
               "Samples": ("audio-x-wav", "audio-x-generic"), "Sonstiges": ("application-octet-stream", "unknown")}
GROUP_ORDER = ["Musik", "Projekte", "Presets", "Einstellungen", "Samples", "Sonstiges"]
LIVE = "__live__"


def selective_restore(ctx, parent, rep: BI.Report, ids, source_label: str) -> bool:
    """Sicherheits-Backup lesen → genau auflisten, was überschrieben wird → bestätigen → schreiben."""
    if not ctx.require(parent):
        return False
    if not rep.restorable or rep.archive is None:
        show_error(parent, "Nicht möglich", "Dieses Backup-Format kann die App nicht zurückspielen.")
        return False
    if rep.firmware_key and ctx.firmware_key and rep.firmware_key != ctx.firmware_key:
        show_error(parent, "Andere Firmware", f"Das Backup stammt von <b>{BI.fw_name(rep.firmware_key)}</b>, auf dem "
                                              f"FM-1 läuft <b>{BI.fw_name(ctx.firmware_key)}</b>. Teile daraus passen "
                                              "nicht – es wird nichts geschrieben.")
        return False
    prof = ctx.profile
    try:
        folder = ctx.device_folder(ctx.settings.backups_dir)
    except OSError as e:
        show_error(parent, "Kein Sicherheits-Backup möglich", f"{e}<br><br>Ohne Sicherheits-Backup wird nichts "
                                                               "zurückgespielt.")
        return False
    fw, version = ctx.firmware_key, ctx.info.version if ctx.info else ""

    def read_now(w, link):
        w.log.emit("Sicherheits-Backup: lese den jetzigen Stand des Geräts …")
        p = AB.capture_to_file(link, folder, prof, fw, version,
                               f"automatisch vor dem Teil-Zurückspielen aus {source_label}", "sicherheit",
                               progress=lambda a, b: w.progress.emit(a, b))
        w.log.emit(f"Gesichert: {p.name}")
        return p
    d = ctx.job(parent, "Sicherheits-Backup", read_now,
                done_text=lambda p: f"Jetziger Stand gesichert: {p.name}. Noch wurde nichts zurückgespielt – "
                "als Nächstes siehst du genau, was ersetzt wird.",
                fail_text=lambda m: f"Sicherheits-Backup fehlgeschlagen: {m}. Es wird nichts geschrieben.")
    if not d.ok:
        show_error(parent, "Kein Sicherheits-Backup", "Das Sicherheits-Backup hat nicht geklappt. "
                                                      "Ohne Sicherheits-Backup wird nichts geschrieben.")
        return False
    safety = d.result_value
    cur = BI.inspect(safety)
    man = [backup.ObjInfo(o.id, o.size, o.crc) for o in cur.objects]
    plan = BI.plan_restore(rep, ids, man, cur)
    if not plan.ok:
        show_error(parent, "Zurückspielen nicht möglich", "<br>".join(plan.errors))
        return False
    name = prof.name if prof else "dem FM-1"
    if not confirm(parent, "Ausgewählte zurückspielen",
                   plan.html(name) + f"<br><br>Sicherheits-Backup des jetzigen Stands: <i>{safety.name}</i>",
                   "Zurückspielen", danger=True):
        return False
    arch = rep.archive
    titles = {it.id: it.title for it in plan.items}
    state = {"error": None}

    def work(w, link):
        w.log.emit("Prüfe jedes Objekt beim Gerät, bevor etwas ersetzt wird …")
        try:
            done = backup.restore_selected(link, arch, plan.ids(), progress=lambda a, b: w.progress.emit(a, b),
                                           expect=plan.expect, clear_extension=plan.clear_extension,
                                           cancelled=lambda: bool(getattr(w, "cancelled", False)))
        except Exception as e:
            state["error"] = e
            for i in getattr(e, "written", None) or []:
                w.log.emit(f"✓ {titles.get(i) or backup.object_title(i, arch.sloop)} geschrieben")
            raise
        for it in plan.items:
            w.log.emit(f"✓ {it.title}")
        w.log.emit("Fertig.")
        return done

    def failed(m):
        e = state["error"]
        where = backup.written_text(e, plan.ids(), arch.sloop, titles) if e is not None else ""
        head = "Zurückspielen abgebrochen" if "abgebrochen" in str(m) else f"Zurückspielen fehlgeschlagen: {m}"
        return f"{head}<br>{where}<br>Den Stand davor hast du im Sicherheits-Backup {safety.name}."
    d2 = ctx.job(parent, "Ausgewählte zurückspielen", work, cancellable=True,
                 done_text=f"{len(plan.items)} Teil(e) auf {name} zurückgespielt: "
                 + ", ".join(it.title for it in plan.items) + f". Der vorherige Stand liegt im "
                 f"Sicherheits-Backup {safety.name}.",
                 fail_text=failed)
    if d2.ok:
        ctx.message.emit(f"{len(plan.items)} Teile auf {name} zurückgespielt.")
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
        lay.addWidget(heading("Backup-Browser", "In jedes Backup hineinschauen: Projekte, alle 384 Preset-Plätze, "
                                                "Einstellungen. Zwei Stände vergleichen und nur Teile zurückspielen."))
        split = QSplitter()
        lay.addWidget(split, 1)

        # links: Gerät + Zeitleiste
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.dev = QComboBox()
        self.dev.setToolTip("Backups welches Geräts?")
        self.dev.currentIndexChanged.connect(self._fill_timeline)
        ll.addWidget(self.dev)
        self.timeline = QListWidget()
        self.timeline.setMinimumHeight(64)
        self.timeline.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)   # lange Zeilen: … statt Rollbalken
        self.timeline.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.timeline.currentRowChanged.connect(self._pick)
        ll.addWidget(self.timeline, 1)
        row = QHBoxLayout()
        for ic, txt, fn, tip in ((("document-edit",), "Notiz", self._note, ""),
                                 (("tag", "bookmark-new"), "Schlagwörter", self._labels, "z. B. „vor Firmware-Wechsel“"),
                                 (("pin", "window-pin"), "Anheften", self._pin, "nie automatisch löschen")):
            b = QPushButton(icon(*ic), txt)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            row.addWidget(b)
        ll.addLayout(row)
        reflow(row)                                      # Knöpfe brechen bei schmaler Spalte um
        row = QHBoxLayout()
        b = QPushButton(icon("archive-insert", "package-x-generic"), "Als Zip …")
        b.setToolTip("Backups dieses Geräts (oder alle) als Zip mit Inhaltsverzeichnis exportieren")
        b.clicked.connect(self._zip_export)
        row.addWidget(b)
        b = QPushButton(icon("archive-extract", "document-open"), "Zip importieren …")
        b.clicked.connect(self._zip_import)
        row.addWidget(b)
        ll.addLayout(row)
        reflow(row)
        split.addWidget(left)

        # rechts: Reiter
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.head = QLabel("Kein Backup gewählt.")
        self.head.setWordWrap(True)
        rl.addWidget(self.head)
        self.tabs = QTabWidget()
        self.tabs.setMinimumHeight(130)                  # schmal/übereinander: Inhaltsbaum nie zusammengedrückt
        rl.addWidget(self.tabs, 1)
        # Inhalt
        self.content = QTreeWidget()
        self.content.setHeaderLabels(["Inhalt", "Größe", "Format", "Details"])
        self.content.itemChanged.connect(self._check_changed)
        self.content.currentItemChanged.connect(lambda *_: self._update_view_button())
        self.content.itemDoubleClicked.connect(lambda *_: self.view_project())
        self.tabs.addTab(self.content, icon("view-list-tree", "view-list-details"), "Inhalt")
        # Presets
        pw = QWidget()
        pl = QVBoxLayout(pw)
        pr = QHBoxLayout()
        self.p_search = QLineEdit()
        self.p_search.setPlaceholderText("Presets suchen (Name, Engine, Platz) …")
        self.p_search.textChanged.connect(self._fill_presets)
        self.p_used = QCheckBox("nur belegte")
        self.p_used.setChecked(True)
        self.p_used.toggled.connect(self._fill_presets)
        pr.addWidget(self.p_search, 1)
        pr.addWidget(self.p_used)
        pl.addLayout(pr)
        self.ptable = QTreeWidget()
        self.ptable.setHeaderLabels(["Platz", "Name", "Engine", "★", "Bank (Objekt)"])
        self.ptable.setRootIsDecorated(False)
        self.ptable.setSortingEnabled(True)
        self.ptable.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        pl.addWidget(self.ptable, 1)
        self.p_info = QLabel()
        pl.addWidget(self.p_info)
        self.tabs.addTab(pw, icon("audio-x-generic"), "Presets")
        # Vergleich
        cw = QWidget()
        cl = QVBoxLayout(cw)
        cr = QHBoxLayout()
        cr.addWidget(QLabel("Vergleichen mit:"))
        self.cmp_with = QComboBox()
        cr.addWidget(self.cmp_with, 1)
        b = QPushButton(icon("document-compare", "kompare", "view-split-left-right"), "Vergleichen")
        b.clicked.connect(self.compare)
        cr.addWidget(b)
        self.cmp_only = QCheckBox("nur Änderungen")
        self.cmp_only.setChecked(True)
        self.cmp_only.toggled.connect(self._fill_compare)
        cr.addWidget(self.cmp_only)
        cl.addLayout(cr)
        self.cmp_sum = QLabel("Wähle einen zweiten Stand – ein anderes Backup oder den Live-Stand des Geräts.")
        self.cmp_sum.setWordWrap(True)
        cl.addWidget(self.cmp_sum)
        self.cmp_tree = QTreeWidget()
        self.cmp_tree.setHeaderLabels(["Inhalt", "dieses Backup", "Vergleich", "Änderung"])
        cl.addWidget(self.cmp_tree, 1)
        b = QPushButton(icon("edit-select-all"), "Geänderte Teile zum Zurückspielen auswählen")
        b.clicked.connect(self._select_changed)
        cl.addWidget(b)
        self.tabs.addTab(cw, icon("document-compare", "view-split-left-right"), "Vergleich")
        # Info
        self.infov = QTextBrowser()
        self.tabs.addTab(self.infov, icon("documentinfo", "help-about"), "Info")

        self.b_restore = QPushButton(icon("document-revert", "edit-undo"), "Ausgewählte zurückspielen …")
        self.b_restore.setToolTip("Nur die angehakten Teile aufs verbundene FM-1 – mit Sicherheits-Backup und genauer Liste")
        self.b_restore.clicked.connect(self.restore_selected)
        self.b_export = QPushButton(icon("document-export", "document-save-as"), "Ausgewählte exportieren …")
        self.b_export.setToolTip("Angehakte Teile als Dateien in einen Ordner: Projekte als .fm1proj (auch in die "
                                 "Projekt-Bibliothek), markierte Presets als .json, alles andere roh (.bin)")
        self.b_export.clicked.connect(self._export)
        self.b_view = QPushButton(icon("view-preview", "document-preview", "document-open"), "Projekt ansehen")
        self.b_view.setToolTip("Das markierte Projekt unter Projekte › Ansicht & Export öffnen: Spuren, Pattern, Song, "
                               "MIDI-Export (auch per Doppelklick)")
        self.b_view.clicked.connect(self.view_project)
        self.b_view.setEnabled(False)
        self.sel_label = QLabel()
        self.sel_label.setWordWrap(True)
        rl.addWidget(flow_row(self.b_restore, self.b_export, self.b_view))
        rl.addWidget(self.sel_label)
        split.addWidget(right)
        split.setStretchFactor(1, 3)
        split.setSizes([300, 900])
        adaptive_splitter(split, 640, ratios=(1, 3))     # schmal: Zeitleiste über den Reitern
        ctx.changed.connect(self._fill_devices)
        self._fill_devices()

    # ----------------------------------------------------------- Seite
    def activate(self):
        self._fill_devices()
        if not self.rep and self.metas:
            self.open_path(self.metas[0].path)

    def open_with(self, path: str = "", **_):
        if path:
            self.open_path(Path(path))

    def _fill_devices(self):
        cur = self.dev.currentData()
        self.dev.blockSignals(True)
        self.dev.clear()
        self.dev.addItem(icon("folder"), "alle Geräte", "")
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
            extra = " · ".join(([AB_KIND.get(m.kind, m.kind)] if m.kind != "manuell" else []) + [f"#{x}" for x in m.labels])
            it = QListWidgetItem(icon("pin", "window-pin") if m.pinned else icon("document-save", "backup"),
                                 (f"{day}  " if day != last_day else "            ") + label + (f"\n            {extra}" if extra else "")
                                 + (f"\n            „{m.note}“" if m.note else ""))
            last_day = day
            it.setData(Qt.ItemDataRole.UserRole, str(m.path))
            it.setToolTip(f"{m.path}\n{AB.content_text(m.projects, m.presets, m.objects)}, {fmt_size(m.size)}")
            self.timeline.addItem(it)
        if not self.metas:
            it = QListWidgetItem("Noch keine Backups.\nSo geht's: FM-1 anschließen,\ndann Strg+B (Backup jetzt).")
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            it.setToolTip("Noch keine Backups – so geht's: FM-1 anschließen, dann Strg+B (Backup jetzt).")
            self.timeline.addItem(it)
        self.timeline.blockSignals(False)
        self.cmp_with.clear()
        if self.ctx.connected:
            self.cmp_with.addItem(icon("audio-midi"), "Live-Stand des verbundenen FM-1", LIVE)
        for m in self.metas:
            self.cmp_with.addItem(f"{fmt_date(m.created)} · {m.device} · {fw_label(m.firmware)}", str(m.path))
        if keep:
            for i in range(self.timeline.count()):
                if self.timeline.item(i).data(Qt.ItemDataRole.UserRole) == str(keep):
                    self.timeline.blockSignals(True)
                    self.timeline.setCurrentRow(i)
                    self.timeline.blockSignals(False)
        elif self.metas and self.isVisible():
            self.open_path(self.metas[0].path)          # neuestes Backup gleich zeigen

    def _pick(self, row):
        it = self.timeline.item(row) if row >= 0 else None
        p = it.data(Qt.ItemDataRole.UserRole) if it else None
        if p:
            self.open_path(Path(p))

    # ----------------------------------------------------------- Laden
    def open_path(self, path: Path):
        try:
            self.rep = BI.inspect(path)
        except Exception as e:  # noqa: BLE001
            show_error(self, "Backup nicht lesbar", f"{path.name}: {e}")
            return
        self.other = None
        self.cmp_tree.clear()
        self.cmp_sum.setText("Wähle einen zweiten Stand – ein anderes Backup oder den Live-Stand des Geräts.")
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
                          + ("" if r.restorable else " · <i>nur ansehen</i>"))
        self.content.blockSignals(True)
        self.content.clear()
        groups = {}
        for g in GROUP_ORDER:
            objs = [o for o in r.objects if o.group == g]
            if not objs:
                continue
            gi = QTreeWidgetItem([f"{g} ({sum(1 for o in objs if o.used)} von {len(objs)} belegt)"])
            gi.setIcon(0, icon(*GROUP_ICONS[g]))
            gi.setFlags(gi.flags() | Qt.ItemFlag.ItemIsAutoTristate | Qt.ItemFlag.ItemIsUserCheckable)
            gi.setCheckState(0, Qt.CheckState.Unchecked)
            self.content.addTopLevelItem(gi)
            groups[g] = gi
            for o in objs:
                it = QTreeWidgetItem([o.title, fmt_size(o.size) if o.used else "leer", o.format,
                                      (o.detail or "") + ("  ⚠ " + "; ".join(o.warnings) if o.warnings else "")])
                it.setData(0, Qt.ItemDataRole.UserRole, o.id)
                it.setToolTip(0, f"{o.title} – technisch: Objekt {o.id}, CRC {o.crc:08X}")
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
            gi.setExpanded(g in ("Musik", "Projekte", "Einstellungen"))
        self.content.blockSignals(False)
        for col in range(3):
            self.content.resizeColumnToContents(col)
        self._fill_presets()
        lines = [f"<h3>{c.get('device') or 'Backup'}</h3><table>"]
        for k, v in (("Datei", str(r.path or "–")), ("Format", r.format), ("Firmware (Gerät meldete)", fw_label(r.firmware)),
                     ("Firmware-Kennung", r.firmware_key or "–"), ("Firmware-Version", c.get("firmware_version", "–")),
                     ("Erstellt", fmt_date(r.created)), ("Art", AB_KIND.get(c.get("kind", ""), c.get("kind", "–"))),
                     ("Notiz", c.get("note", "")), ("Schlagwörter", ", ".join(c.get("labels") or [])),
                     ("Angeheftet", "ja" if c.get("pinned") else "nein"), ("Seriennummer", c.get("serial", "–")),
                     ("Vom Web-Editor lesbar", "ja" if r.known else "nein (anderes Inventar)")):
            lines.append(f"<tr><td><b>{k}</b></td><td>{v}</td></tr>")
        lines.append("</table>")
        if r.settings:
            lines.append("<h4>Einstellungen</h4><table>" + "".join(
                f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in r.settings.items()) + "</table>")
        if r.warnings:
            lines.append("<p>" + "<br>".join(r.warnings) + "</p>")
        self.infov.setHtml("".join(lines))
        self._check_changed()
        self._update_view_button()

    # ------------------------------------------------- Projekt ansehen
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
                it.setToolTip(1, "älteres Datensatz-Format (am Gerät: OLD FORMAT)")
            self.ptable.addTopLevelItem(it)
            n += 1
        self.ptable.setSortingEnabled(True)
        self.ptable.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        used = len(self.rep.used_presets)
        self.p_info.setText(f"{used} von {len(allp)} Plätzen belegt, {sum(1 for p in allp if p.favorite)} Favoriten · "
                            f"{n} angezeigt" if allp else
                            "Keine Preset-Banken erkannt (andere Firmware oder älteres Format).")

    # ------------------------------------------------------ Auswahl
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

    def _check_changed(self, *_):
        ids = self.selected_ids()
        self.b_restore.setEnabled(bool(ids) and bool(self.rep and self.rep.restorable))
        self.sel_label.setText((f"{len(ids)} Teil ausgewählt" if len(ids) == 1 else f"{len(ids)} Teile ausgewählt") if ids else "Teile im Reiter „Inhalt“ anhaken.")

    # ---------------------------------------------------- Zurückspielen
    def restore_selected(self) -> bool:
        if not self.rep:
            return False
        ids = self.selected_ids()
        if not ids:
            show_error(self, "Nichts ausgewählt", "Bitte im Reiter „Inhalt“ die Teile anhaken, die zurück sollen.")
            return False
        comp = self.rep.companion
        label = self.rep.path.name if self.rep.path else (comp.get("device") or "Backup")
        ok = selective_restore(self.ctx, self, self.rep, ids, label)
        self._fill_timeline()
        return ok

    # ------------------------------------------------------- Vergleich
    def compare(self):
        if not self.rep:
            return
        key = self.cmp_with.currentData()
        if key == LIVE:
            if not self.ctx.require(self):
                return
            version = self.ctx.info.version if self.ctx.info else ""

            def work(w, link):
                w.log.emit("Lese den Live-Stand (nur in den Speicher, keine Datei) …")
                return backup.capture_live(link, version, progress=lambda a, b: w.progress.emit(a, b),
                                           companion={"device": self.ctx.profile.name if self.ctx.profile else "",
                                                      "firmware": self.ctx.firmware_key})
            d = self.ctx.job(self, "Live-Stand lesen", work, close_on_success=True,
                             done_text="Live-Stand gelesen (nur im Speicher, keine Datei, am FM-1 nichts geändert).")
            if not d.ok:
                return
            self.compare_with(BI.inspect(d.result_value), "Live")
        elif key:
            try:
                self.compare_with(BI.inspect(Path(key)), Path(key).name)
            except Exception as e:  # noqa: BLE001
                show_error(self, "Backup nicht lesbar", str(e))

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
        # Richtung: vom Backup (alt) zum Vergleich
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
            it = QTreeWidgetItem([title, fmt_size(a.size) if a and a.used else ("leer" if a else "–"),
                                  fmt_size(b.size) if b and b.used else ("leer" if b else "–"),
                                  ch.kind if ch else "gleich"])
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
            show_error(self, "Noch kein Vergleich", "Erst oben einen zweiten Stand wählen und „Vergleichen“ drücken.")
            return
        if d.identical:
            show_error(self, "Keine Unterschiede", "Beide Stände sind gleich – es gibt nichts auszuwählen.")
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
            show_error(self, "Nichts ausgewählt", "Teile im Reiter „Inhalt“ anhaken oder Presets in der Liste markieren.")
            return
        dest = QFileDialog.getExistingDirectory(self, "Exportieren nach", str(self.ctx.settings.root))
        if not dest:
            return
        try:
            n, names = export_parts(self.rep, ids, labels, Path(dest), self.ctx.settings.projects_dir)
        except OSError as e:
            from ..common import fs_error_text
            show_error(self, "Nicht exportiert", fs_error_text(e, "Export nicht möglich"))
            return
        if not n:
            show_error(self, "Nichts exportiert", "Die angehakten Teile sind leer – es gibt nichts zu exportieren.")
            return
        has_proj = any(x.endswith(projects.SUFFIX) for x in names)
        self.ctx.message.emit(f"{n} Datei(en) exportiert nach {dest}")
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox(QMessageBox.Icon.Information, "Exportiert",
                    f"{n} Datei(en) liegen in {dest}:<br>" + "<br>".join(names[:12])
                    + (f"<br>… und {len(names) - 12} weitere" if len(names) > 12 else "")
                    + ("<br><br>Die Projekte liegen zusätzlich in der Projekt-Bibliothek (Projekte › Plätze & Bibliothek)."
                       if has_proj else ""), parent=self).exec()

    # ------------------------------------------------------ Metadaten
    def _cur_path(self) -> Path | None:
        return self.rep.path if self.rep and self.rep.path else None

    def _note(self):
        p = self._cur_path()
        if not p:
            return
        txt, ok = QInputDialog.getText(self, "Notiz", "Notiz zum Backup:", text=self.rep.companion.get("note", ""))
        if ok:
            self._set_meta(p, note=txt)

    def _labels(self):
        p = self._cur_path()
        if not p:
            return
        txt, ok = QInputDialog.getText(self, "Schlagwörter", "Schlagwörter, durch Komma getrennt\n"
                                                          "(z. B. vor Firmware-Wechsel, Live-Set Oktober):",
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
            what = {"note": "Notiz gespeichert", "labels": "Schlagwörter gespeichert",
                    "pinned": "angeheftet – wird nie automatisch gelöscht" if kw.get("pinned") else "nicht mehr angeheftet"}
            self.ctx.message.emit(f"{p.name}: " + ", ".join(what[k] for k in kw if k in what))
        except (OSError, ValueError, backup.BackupError) as e:
            from ..common import fs_error_text
            show_error(self, "Nicht gespeichert", fs_error_text(e, f"{p.name} lässt sich nicht ändern")
                       if isinstance(e, OSError) else f"{p.name}: {e}")
        self._reload(p)

    def _reload(self, p):
        self.open_path(p)
        self._fill_timeline()

    def _zip_export(self):
        paths = [m.path for m in self.metas]
        if not paths:
            show_error(self, "Keine Backups", "Hier gibt es noch keine Backups zum Exportieren.")
            return
        dest, _ = QFileDialog.getSaveFileName(self, "Backups als Zip", str(Path.home() / "FM-1-Backups.zip"), "Zip (*.zip)")
        if not dest:
            return
        root = self.ctx.settings.backups_dir

        def work(w):
            w.log.emit(f"Packe {len(paths)} Backups …")
            return AB.export_zip(paths, Path(dest), root)
        from .. import feedback
        feedback.run(self, "Backups als Zip", work,
                     done=lambda n: f"{n} Backups mit Inhaltsverzeichnis und Prüfsummen in {Path(dest).name} gepackt. "
                                    "Die Originale bleiben, wo sie sind.")

    def _zip_import(self):
        src, _ = QFileDialog.getOpenFileName(self, "Backup-Zip importieren", str(Path.home()), "Zip (*.zip)")
        if not src:
            return
        try:
            got, skipped = AB.import_zip(Path(src), self.ctx.settings.backups_dir)
        except OSError as e:
            from ..common import fs_error_text
            show_error(self, "Zip nicht übernommen", fs_error_text(e, "Import nicht möglich"))
            return
        except Exception as e:  # noqa: BLE001
            show_error(self, "Zip nicht lesbar", f"{str(e) or type(e).__name__}<br><br>Es wurde nichts übernommen.")
            return
        from PyQt6.QtWidgets import QMessageBox
        text = (f"{len(got)} Backup(s) in den Backup-Ordner übernommen." if got else
                "Kein Backup übernommen.")
        if skipped:
            text += f"<br><br>{len(skipped)} übersprungen:<br>" + "<br>".join(skipped[:15]) + \
                    (f"<br>… und {len(skipped) - 15} weitere" if len(skipped) > 15 else "")
        QMessageBox(QMessageBox.Icon.Information if got else QMessageBox.Icon.Warning, "Zip importiert", text,
                    parent=self).exec()
        self._fill_devices()


AB_KIND = {"manuell": "manuell", "auto": "automatisch", "vor-Update": "vor Firmware-Wechsel", "sicherheit": "Sicherheit"}


def export_parts(rep: BI.Report, ids, preset_labels, dest: Path, projects_dir: Path | None = None) -> tuple[int, list[str]]:
    """Objekte roh (.bin), Projekte als .fm1proj (auch in die Bibliothek), Presets als .json.
    -> (Anzahl, Dateinamen im Zielordner). OSError, wenn der Ordner nicht beschreibbar ist."""
    dest.mkdir(parents=True, exist_ok=True)
    dev = rep.companion.get("device", "") or "FM-1"
    names: list[str] = []
    for oid in ids:
        o = rep.obj(oid)
        if not o or not o.used:
            continue
        if o.slot or o.id == 0:
            src = f"Backup {rep.path.name if rep.path else ''}, " + (f"Platz {o.slot}" if o.slot else "laufende Musik")
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


PAGE = PageSpec(key="backup_browser", title="Backup-Browser", group="daten", factory=_make,
                icons=("document-preview", "system-search", "backup"), order=20,
                description="in Backups hineinschauen, vergleichen, einzelne Teile zurückspielen",
                keywords=("backup", "diff", "vergleich", "presets", "wiederherstellen", "zip"))
