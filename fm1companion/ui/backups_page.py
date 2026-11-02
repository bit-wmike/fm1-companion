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
KIND_TEXT = {"manuell": "manuell", "auto": "automatisch", "vor-Update": "vor Firmware-Wechsel",
             "sicherheit": "Sicherheit"}


def no_backup_reason(ctx) -> str:
    """warum gerade kein Backup geht ("" = es geht)"""
    if ctx.connected:
        return ""
    if ctx.port is None:
        return "Kein FM-1 verbunden. Bitte ein FM-1 per USB anschließen."
    if getattr(ctx, "protocol", "") == "update":
        return ("Das FM-1 ist im Update-Modus – erst die Installation abschließen (Seite „Rettung“), dann sichern.")
    if getattr(ctx, "midi_connected", False):
        from .device import firmware_name
        fw = firmware_name(ctx.firmware_key) or "Die laufende Firmware"
        return (f"{fw} hat kein Editor-Protokoll – davon kann die App kein Backup machen. Backups gibt es bei "
                "Melodee, Skiff und SLOOP. Wichtige Projekte bitte mit dem Werkzeug der Firmware sichern.")
    return "Das FM-1 antwortet nicht. Kabel prüfen und neu verbinden."


def make_backup(ctx, parent, note: str = "", kind: str = "") -> Path | None:
    why = no_backup_reason(ctx)
    if why:
        show_error(parent, "Backup nicht möglich", why)
        return None
    prof = ctx.profile
    try:
        folder = ctx.device_folder(ctx.settings.backups_dir)
    except OSError as e:
        show_error(parent, "Backup nicht möglich", str(e) if str(e).startswith("Der Ordner") else
                   f"Der Backup-Ordner lässt sich nicht anlegen: {e.strerror or e}")
        return None
    version = ctx.info.version if ctx.info else ""
    fw = ctx.firmware_key
    akind = {"auto": "sicherheit", "": "manuell"}.get(kind, kind)

    def work(w, link):
        w.log.emit("Lese das Inventar …")
        try:
            path = AB.capture_to_file(link, folder, prof, fw, version, note, akind,
                                      progress=lambda a, b: w.progress.emit(a, b),
                                      abort=lambda: bool(getattr(w, "cancelled", False)))
        except backup.BackupError as e:
            if "abgebrochen" in str(e):
                raise backup.BackupError("Abgebrochen – es wurde keine Datei gespeichert, am FM-1 wurde nichts "
                                         "verändert.") from e
            raise
        except OSError as e:
            from .. import firmware as _fw
            w.log.emit("Technische Details: " + _fw.explain(e))
            raise RuntimeError(f"Das Backup wurde gelesen, aber nicht gespeichert: {_fw.friendly_error(e)}") from e
        w.log.emit(f"Gesichert: {fmt_size(path.stat().st_size)} – Firmware {fw or '?'} {version}")
        w.log.emit(f"Datei: {path}")
        try:
            prune(folder, ctx.settings.keep_backups)
        except OSError as e:
            w.log.emit(f"Alte automatische Backups konnten nicht gelöscht werden: {e.strerror or e}")
        _mirror_quiet(ctx, path, w.log.emit)
        return path

    def said(path) -> str:
        m = AB.read_meta(path)
        parts = f"{m.objects} Teile, {m.projects} Projekte, " if m else ""
        return (f"Backup gespeichert: {Path(path).name} ({parts}{fmt_size(Path(path).stat().st_size)}). "
                "Am FM-1 wurde nichts verändert.")
    from .feedback import open_folder_action
    d = ctx.job(parent, "Backup", work, done=said, actions=[open_folder_action(lambda p: Path(p).parent)],
                cancellable=(kind != "auto"))
    return d.result_value if d.ok else None


def _mirror_quiet(ctx, path: Path, log=None):
    target = AB.settings().get("mirror")
    if not target:
        return
    try:
        AB.mirror_file(path, ctx.settings.backups_dir, Path(target))
        AB.STATUS.mirror = f"Spiegel aktuell ({time.strftime('%H:%M')})"
        if log:
            log(f"Gespiegelt und geprüft: {target}")
    except OSError as e:
        AB.STATUS.mirror = f"Spiegel nicht möglich: {e}"
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
        self.connect_timer.timeout.connect(lambda: self.run("beim Verbinden"))
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
                AB.STATUS.add(f"Automatisches Backup nicht möglich: {e}")
                return
            if AB.needs_backup(folder, s["hours"] / 24):
                self.run("regelmäßig", force=True)

    def busy_reason(self) -> str:
        c = self.ctx
        if not c.connected or not c.profile:
            return "kein Gerät"
        if getattr(c, "is_virtual", False):
            return "Studio-Zwilling (kein echtes FM-1)"
        if c.hold:
            return "Firmware-Installation läuft"
        if self.running:
            return "läuft schon"
        if c.link._exclusive_depth:
            return "Gerät gerade in Benutzung"
        win = getattr(c, "window", None)
        if win is not None:
            try:
                cur = win.stack.currentWidget()
                key = next((k for k, p in win.pages.items() if p is cur), "")
                if key in ("editor", "recorder"):
                    return "Editor/Aufnahme offen"
                rec = getattr(getattr(win, "recorder", None), "rec", None)
                if rec is not None and rec.running:
                    return "Aufnahme läuft"
            except Exception:                      # noqa: BLE001
                pass
        return ""

    def run(self, reason: str = "beim Verbinden", force: bool = False) -> bool:
        c = self.ctx
        if not c.connected or not c.profile or getattr(c, "is_virtual", False):
            return False
        try:
            folder = c.device_folder(c.settings.backups_dir)
        except OSError as e:
            AB.STATUS.add(f"Automatisches Backup nicht möglich: {e}")
            c.message.emit(f"Automatisches Backup nicht möglich: {e}")
            self.updated.emit()
            return False
        s = AB.settings()
        if not force and not AB.needs_backup(folder, s["days"]):
            AB.STATUS.add(f"{c.profile.name}: letztes Backup ist aktuell (jünger als {s['days']} Tag(e)).")
            self.updated.emit()
            return False
        why = self.busy_reason()
        if why:
            if self._retries < 30:
                self._retries += 1
                QTimer.singleShot(60_000, lambda: self.run(reason, force))
            AB.STATUS.add(f"Automatisches Backup wartet ({why}).")
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
            p = AB.capture_to_file(link, folder, prof, fw, version, f"automatisch {reason}", "auto", abort=abort)
            prune(folder, keep)
            target = s.get("mirror")
            if target:
                try:
                    AB.mirror_file(p, root, Path(target))
                    AB.STATUS.mirror = f"Spiegel aktuell ({time.strftime('%H:%M')})"
                except OSError as e:
                    AB.STATUS.mirror = f"Spiegel nicht möglich: {e}"
            return p

        def done(p):
            self.running = False
            AB.STATUS.last_path, AB.STATUS.last_time = p, time.time()
            AB.STATUS.add(f"Automatisches Backup von {prof.name} ({reason}) gespeichert.")
            c.message.emit(f"Automatisches Backup von {prof.name} gespeichert.")
            self.updated.emit()

        def fail(msg, tb):
            self.running = False
            AB.STATUS.add(f"Automatisches Backup nicht gemacht: {msg}")
            c.message.emit(f"Automatisches Backup nicht gemacht: {msg}")
            self.updated.emit()

        AB.STATUS.add(f"Automatisches Backup von {prof.name} läuft …")
        self.updated.emit()
        c.job(c.window, "Automatisches Backup", work, dialog=False, on_done=done, on_fail=fail)
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
        lay.addWidget(heading("Backups", "Komplette Sicherungen (laufende Musik, alle Projekte, User-Presets, "
                                         "Einstellungen). Gleiches Format wie der Web-Editor – Dateien sind austauschbar. "
                                         "Hineinschauen, vergleichen und Teile zurückspielen: im Reiter <b>Durchsuchen & Vergleichen</b>."))
        top = QHBoxLayout()
        self.chk_all = QCheckBox("Backups aller Geräte zeigen")
        self.chk_all.toggled.connect(self.refresh)
        top.addWidget(self.chk_all)
        self.chk_pinned = QCheckBox("nur angeheftete")
        self.chk_pinned.toggled.connect(self.refresh)
        top.addWidget(self.chk_pinned)
        top.addStretch(1)
        lay.addLayout(top)
        reflow(top)                                      # schmal: Häkchen untereinander statt abgeschnitten
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Datum", "Gerät", "Firmware", "Art", "Inhalt", "Größe", "Notiz / Schlagwörter"])
        self.tree.setRootIsDecorated(False)
        self.tree.setSortingEnabled(True)
        self.tree.setMinimumHeight(260)
        self.tree.itemDoubleClicked.connect(lambda *_: self._browse())
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
        for b in (btn(("document-save", "backup"), "Backup jetzt", self._new, "Strg+B"),
                  btn(("document-preview", "system-search"), "Inhalt ansehen …", self._browse,
                      "Öffnet das Backup im Reiter „Durchsuchen & Vergleichen“: Projekte, Presets, Einstellungen "
                      "ansehen, mit einem anderen Stand vergleichen, einzelne Teile zurückspielen (auch Doppelklick)"),
                  btn(("document-revert", "edit-undo"), "Alles zurückspielen …", self._restore),
                  btn(("folder-music",), "Projekte daraus entnehmen", self._extract)):
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        reflow(row)                                      # Knopfreihen brechen bei halber Bildschirmbreite um
        row2 = QHBoxLayout()
        for b in (btn(("document-edit",), "Notiz", self._note),
                  btn(("tag", "bookmark-new"), "Schlagwörter", self._labels, "z. B. „vor Firmware-Wechsel“, „Live-Set Oktober“"),
                  btn(("pin", "window-pin", "emblem-favorite"), "Anheften", self._pin,
                      "Angeheftete Backups werden nie automatisch gelöscht"),
                  btn(("document-import", "document-open"), "Datei hinzufügen …", self._import),
                  btn(("edit-delete",), "Löschen", self._delete)):
            row2.addWidget(b)
        row2.addStretch(1)
        row2.addWidget(btn(("folder-open",), "Ordner öffnen", self._open_folder))
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
        g = QGroupBox("Automatische Backups")
        f = QFormLayout(g)
        f.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)   # schmal: Feld unter das lange Häkchen
        self.a_connect = QCheckBox("beim Verbinden, wenn das letzte Backup älter ist als")
        self.a_connect.setChecked(s["on_connect"])
        self.a_days = QSpinBox()
        self.a_days.setRange(1, 60)
        self.a_days.setSuffix(" Tag(e)")
        self.a_days.setValue(int(s["days"]))
        self.a_days.setMaximumWidth(130)
        f.addRow(self.a_connect, self.a_days)
        self.a_periodic = QCheckBox("regelmäßig, solange verbunden, alle")
        self.a_periodic.setChecked(s["periodic"])
        self.a_periodic.setToolTip("Achtung: ein Backup stoppt kurz die Wiedergabe am FM-1. "
                                   "Läuft nie, während der Editor oder die Aufnahme offen ist.")
        self.a_hours = QSpinBox()
        self.a_hours.setRange(1, 72)
        self.a_hours.setSuffix(" Stunden")
        self.a_hours.setValue(int(s["hours"]))
        self.a_hours.setMaximumWidth(130)
        f.addRow(self.a_periodic, self.a_hours)
        self.a_keep = QSpinBox()
        self.a_keep.setRange(1, 500)
        self.a_keep.setValue(int(self.ctx.settings.keep_backups or 20))
        self.a_monthly = QCheckBox("zusätzlich je Monat das neueste behalten")
        self.a_monthly.setChecked(s["keep_monthly"])
        f.addRow("Automatische behalten:", self._h(self.a_keep, self.a_monthly))
        self.a_mirror = QLineEdit(s["mirror"])
        self.a_mirror.setPlaceholderText("optional: z. B. /mnt/nas/FM-1-Backups – jede Sicherung wird kopiert und geprüft")
        b_pick = QPushButton(icon("folder-open"), "…")
        b_pick.clicked.connect(self._pick_mirror)
        b_now = QPushButton(icon("folder-sync", "view-refresh"), "Jetzt spiegeln")
        b_now.clicked.connect(self._mirror_now)
        f.addRow("Spiegel-Ordner:", self._h(self.a_mirror, b_pick, b_now))
        for wdg in (self.a_connect, self.a_periodic, self.a_monthly):
            wdg.toggled.connect(self._save_auto)
        for wdg in (self.a_days, self.a_hours, self.a_keep):
            wdg.valueChanged.connect(self._save_auto)
        self.a_mirror.editingFinished.connect(self._save_auto)
        self.a_status = QLabel()
        self.a_status.setWordWrap(True)
        self.a_status.setMinimumWidth(0)
        self.a_status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        f.addRow("Status:", self.a_status)
        hint = QLabel("Ein Backup stoppt am FM-1 kurz die Wiedergabe. Automatisch gesichert wird deshalb nur, wenn "
                      "sonst nichts mit dem Gerät passiert – nie während einer Installation, im Editor oder bei einer "
                      "Aufnahme. Vor jeder Firmware-Installation sichert die App standardmäßig, beschriftet mit der alten "
                      "Firmware (ohne Backup nur nach ausdrücklicher Bestätigung im Installationsplan).")
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

    def _save_auto(self, *_):
        s = AB.settings()
        s.update(on_connect=self.a_connect.isChecked(), days=self.a_days.value(), periodic=self.a_periodic.isChecked(),
                 hours=self.a_hours.value(), keep_monthly=self.a_monthly.isChecked(),
                 mirror=self.a_mirror.text().strip())
        self.ctx.settings.keep_backups = self.a_keep.value()
        config.save(self.ctx.settings)

    def _pick_mirror(self):
        d = QFileDialog.getExistingDirectory(self, "Spiegel-Ordner wählen", self.a_mirror.text() or str(Path.home()))
        if d:
            self.a_mirror.setText(d)
            self._save_auto()

    def _mirror_now(self):
        self._save_auto()
        target = AB.settings()["mirror"]
        if not target:
            show_error(self, "Kein Spiegel-Ordner", "Bitte erst einen Spiegel-Ordner eintragen (z. B. ein NAS-Laufwerk).")
            return
        from .common import JobDialog

        def work(w):
            ok, bad, msgs = AB.mirror_all(self.ctx.settings.backups_dir, Path(target))
            for m in msgs:
                w.log.emit(m)
            w.log.emit(f"{ok} Backups im Spiegel geprüft, {bad} Fehler.")
            AB.STATUS.mirror = f"Spiegel: {ok} geprüft, {bad} Fehler ({time.strftime('%H:%M')})"
            return ok, bad
        from . import feedback
        feedback.run(self, "Backups spiegeln", work,
                     done=lambda r: f"Spiegel aktualisiert: {r[0]} Backups kopiert und geprüft"
                                    + (f", {r[1]} FEHLER (siehe Protokoll)." if r[1] else ", keine Fehler.")
                                    + f" Ziel: {target}")
        self._auto_status()

    def _auto_status(self):
        st = AB.STATUS
        lines = [st.text]
        if st.mirror:
            lines.append(st.mirror)
        self.a_status.setText("<br>".join(lines))
        self.a_status.setToolTip("\n".join(st.history) or "noch nichts")

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
            self.info.setText(f"<b>Backup-Ordner nicht erreichbar.</b> {e}")
            return
        total = 0
        metas = AB.list_backups(folder, include_damaged=True)
        for m in metas:
            if self.chk_pinned.isChecked() and not m.pinned:
                continue
            if m.damaged:
                it = _Row([dt.datetime.fromtimestamp(m.mtime).strftime("%d.%m.%Y %H:%M") if m.mtime
                           else "?", m.device or m.path.parent.name, "–", "beschädigt",
                           "Datei nicht lesbar", fmt_size(m.size), m.path.name])
                it.setData(0, Qt.ItemDataRole.UserRole, str(m.path))
                it.setData(0, SORT, float(m.mtime or 0))
                it.setData(5, SORT, float(m.size))
                it.setData(1, Qt.ItemDataRole.UserRole, "damaged")
                it.setIcon(0, icon("dialog-warning", "emblem-important"))
                for c in range(7):
                    it.setToolTip(c, f"{m.path}\nBeschädigt: {m.damaged}. Zurückspielen nicht möglich – löschen oder "
                                     "aus dem Spiegel-Ordner wiederherstellen.")
                    it.setForeground(c, QColor("#da4453"))
                self.tree.addTopLevelItem(it)
                total += m.size
                continue
            note = " · ".join(([m.note] if m.note else []) + [f"#{x}" for x in m.labels])
            it = _Row([m.when.astimezone().strftime("%d.%m.%Y %H:%M"), m.device or m.path.parent.name,
                       fw_label(m.firmware), KIND_TEXT.get(m.kind, m.kind),
                       AB.content_text(m.projects, m.presets, m.objects), fmt_size(m.size), note])
            it.setData(0, Qt.ItemDataRole.UserRole, str(m.path))
            it.setData(0, SORT, m.when.timestamp())
            it.setData(5, SORT, float(m.size))
            it.setToolTip(0, str(m.path))
            it.setToolTip(4, "Teile = einzeln sicherbare Bausteine des Backups (laufende Musik, Einstellungen, "
                             "jeder Projektplatz, jede Preset-Bank, jeder Sample-Slot)")
            if note:
                it.setToolTip(6, note)
            if m.pinned:
                it.setIcon(0, icon("pin", "window-pin", "emblem-favorite"))
                it.setToolTip(6, "angeheftet – wird nie automatisch gelöscht")
            if m.fmt != backup.FORMAT:
                it.setToolTip(4, "SLOOP-Backup (Format des SLOOP-Web-Editors) – in beide Richtungen austauschbar")
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
            self.info.setText(f"{n} Backup{'s' if n != 1 else ''}, {fmt_size(total)} in {folder}" +
                              (f" – <b style='color:#da4453'>{bad} beschädigt</b>" if bad else "") +
                              (f"<br><small>{why}</small>" if why else ""))
        elif why:
            self.info.setText(f"<b>Noch keine Backups von diesem Gerät.</b> {why}")
        else:
            self.info.setText("Noch keine Backups – so geht's: FM-1 anschließen und „Backup jetzt“ (Strg+B). "
                              "Automatische Backups stellst du unten ein.")

    def _open_folder(self):
        try:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._folder())))
        except OSError as e:
            show_error(self, "Ordner nicht erreichbar", str(e))

    def _sel(self) -> Path | None:
        it = self.tree.currentItem()
        return Path(it.data(0, Qt.ItemDataRole.UserRole)) if it else None

    def _sel_ok(self) -> Path | None:
        """ausgewähltes Backup, wenn es lesbar ist (sonst Hinweis)"""
        it = self.tree.currentItem()
        if it is not None and it.data(1, Qt.ItemDataRole.UserRole) == "damaged":
            show_error(self, "Backup beschädigt", f"{Path(it.data(0, Qt.ItemDataRole.UserRole)).name} lässt sich nicht "
                       "lesen. Zurückspielen und Öffnen sind nicht möglich – Löschen geht.")
            return None
        return self._sel()

    def _new(self):
        note, ok = QInputDialog.getText(self, "Backup", "Notiz (optional):")
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
            show_error(self, "Backup beschädigt", str(e))
            return
        lines = [f"<b>{p.name}</b>", f"Firmware: {r.firmware}", f"Erstellt: {fmt_date(r.created)}", r.summary(), ""]
        lines += [f"{o.title}: {fmt_size(o.size)}" for o in r.objects if o.used]
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox(QMessageBox.Icon.Information, "Backup", "<br>".join(lines), parent=self).exec()

    def _restore(self):
        p = self._sel_ok()
        if not p:
            return
        why = no_backup_reason(self.ctx)
        if why:
            show_error(self, "Zurückspielen nicht möglich", why)
            return
        try:
            a = backup.load_file(p)
        except Exception as e:  # noqa: BLE001
            show_error(self, "Backup beschädigt", str(e))
            return
        comp = a.companion
        # dieselbe Prüfung wie auf der Geräte-Seite: nur innerhalb derselben Firmware-Familie
        from .. import backup_inspect as BI
        try:
            rep = BI.inspect(p)
            compat = BI.compatibility(rep, self.ctx.firmware_key, self.ctx.info.version if self.ctx.info else "", None)
        except Exception as e:  # noqa: BLE001
            show_error(self, "Backup beschädigt", str(e))
            return
        if not compat.ok:
            show_error(self, "Zurückspielen nicht möglich",
                       f"<b>{p.name}</b><br><br>{compat.reason}" +
                       ("<br><br>Was geht:<ul>" + "".join(f"<li>{x}</li>" for x in compat.possible) + "</ul>"
                        if compat.possible else ""))
            return
        warn = ""
        if comp.get("device_id") and self.ctx.profile and comp["device_id"] != self.ctx.profile.id:
            warn += f"<br><br>Das Backup stammt von einem anderen Gerät ({comp.get('device')}). Das ist erlaubt."
        if not confirm(self, "Backup zurückspielen",
                       f"Alles auf <b>{self.ctx.profile.name if self.ctx.profile else 'dem FM-1'}</b> wird durch den Stand "
                       f"vom {fmt_date(a.created)} ersetzt: laufende Musik, alle Projekte, Presets und Einstellungen.<br><br>"
                       "Vorher wird automatisch ein Sicherheits-Backup des jetzigen Stands angelegt.<br>"
                       "Nur Teile zurückspielen? Dafür gibt es den Reiter „Durchsuchen & Vergleichen“." + warn +
                       ("<br><br><b>SLOOP:</b> Bitte vorher den Song am FM-1 stoppen (STOP) – sonst lehnt SLOOP das "
                        "Zurückspielen ab." if a.sloop else ""),
                       "Zurückspielen", danger=True):
            return
        safety = make_backup(self.ctx, self, f"automatisch vor dem Zurückspielen von {p.name}", "auto")
        if safety is None and not confirm(self, "Kein Sicherheits-Backup",
                                          "Das Sicherheits-Backup hat nicht geklappt. Trotzdem zurückspielen?",
                                          "Trotzdem", danger=True):
            return

        fw_key = self.ctx.firmware_key or ""
        state = {"error": None}
        planned = [o["id"] for o in a.objects]

        def work(w, link):
            w.log.emit("Prüfe jedes Objekt beim Gerät, bevor etwas ersetzt wird …" if not a.sloop else
                       "Prüfe das Backup und schreibe es in SLOOPs Reihenfolge: Banken, Projekte, Einstellungen, "
                       "Samples …")
            try:
                backup.restore(link, a, progress=lambda x, y: w.progress.emit(x, y), firmware_key=fw_key)
            except Exception as e:
                state["error"] = e
                for i in getattr(e, "written", None) or []:
                    w.log.emit(f"✓ {backup.object_title(i, a.sloop)} geschrieben")
                raise
            w.log.emit("Fertig. Das FM-1 spielt jetzt den Stand aus dem Backup.")
            return len([o for o in a.objects if o.get("size")]) if not a.sloop else len(a.objects)
        when = fmt_date(a.created)
        after = (f" Der vorherige Stand liegt im Sicherheits-Backup {safety.name}." if safety
                 else " Es gibt KEIN Sicherheits-Backup des vorherigen Stands.")

        def failed(m):
            e = state["error"]
            where = backup.written_text(e, planned, a.sloop) if e is not None else ""
            return f"Zurückspielen fehlgeschlagen: {m}<br>{where}{after}"
        self.ctx.job(self, "Backup zurückspielen", work,
                     cancel_tip="Ein komplettes Zurückspielen lässt sich nicht sicher abbrechen – bitte warten. "
                                "Einzelne Teile spielst du im Reiter „Durchsuchen & Vergleichen“ zurück.",
                     done=lambda n: f"Rücksicherung abgeschlossen: {n} Teile vom Stand {when} geschrieben – laufende "
                                    "Musik, Projekte, Presets und Einstellungen." + after,
                     fail_text=failed)
        self.refresh()

    def _extract(self):
        p = self._sel_ok()
        if not p:
            return
        try:
            a = backup.load_file(p)
        except Exception as e:  # noqa: BLE001
            show_error(self, "Backup beschädigt", str(e))
            return
        objs = projects.projects_from_backup(a)
        if not objs:
            show_error(self, "Keine Projekte", "Dieses Backup enthält keine gespeicherten Projekte.")
            return
        dest = self.ctx.settings.projects_dir
        dev = (a.companion or {}).get("device", "")
        what = "Section" if a.sloop else "Platz"
        try:
            for slot, data in objs:
                name = f"{dev or 'FM-1'} {what} {'ABCD'[slot - 1] if a.sloop and 1 <= slot <= 4 else slot}"
                projects.save_project(dest, name, data, a.firmware, dev, source=f"Backup {p.name}, {what} {slot}")
        except OSError as e:
            from .common import fs_error_text
            show_error(self, "Nicht gespeichert", fs_error_text(e, "Projekte nicht in die Bibliothek geschrieben"))
            return
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox(QMessageBox.Icon.Information, "Projekte entnommen",
                    f"{len(objs)} Projekt(e) liegen jetzt in der Projekt-Bibliothek ({dest}). Dort kannst du sie "
                    "umbenennen und einspielen. Am FM-1 wurde nichts verändert.", parent=self).exec()
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
        txt, ok = QInputDialog.getText(self, "Notiz", "Notiz zum Backup:", text=m.note if m else "")
        if ok:
            self._set_meta(p, note=txt)

    def _labels(self):
        p = self._sel_ok()
        if not p:
            return
        m = AB.read_meta(p)
        txt, ok = QInputDialog.getText(self, "Schlagwörter", "Schlagwörter, durch Komma getrennt\n"
                                                          "(z. B. vor Firmware-Wechsel, Live-Set Oktober):",
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
            what = {"note": "Notiz gespeichert", "labels": "Schlagwörter gespeichert",
                    "pinned": "angeheftet – wird nie automatisch gelöscht" if kw.get("pinned") else "nicht mehr angeheftet"}
            self.ctx.message.emit(f"{p.name}: " + ", ".join(what[k] for k in kw if k in what))
        except (OSError, ValueError, backup.BackupError) as e:
            from .common import fs_error_text
            show_error(self, "Nicht gespeichert", fs_error_text(e, f"{p.name} lässt sich nicht ändern")
                       if isinstance(e, OSError) else f"{p.name}: {e}")
        self.refresh()

    def _import(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Backup-Dateien hinzufügen", str(Path.home() / "Downloads"),
                                                "Backups (*.json)")
        from .common import fs_error_text
        for f in files:
            try:
                from .. import backup_inspect as BI
                BI.inspect(Path(f))
            except Exception as e:  # noqa: BLE001
                show_error(self, "Keine gültige Datei", f"{Path(f).name}: {e}")
                continue
            try:
                folder = self._folder()
                dest = folder / Path(f).name
                if dest.exists():
                    if dest.read_bytes() == Path(f).read_bytes():
                        self.ctx.message.emit(f"{Path(f).name} ist schon im Backup-Ordner (identisch) – nichts kopiert.")
                        continue
                    n = 2
                    while dest.exists():
                        dest = folder / f"{Path(f).stem} ({n}){Path(f).suffix}"
                        n += 1
                shutil.copy2(f, dest)
                self.ctx.message.emit(f"Backup hinzugefügt: {dest.name} (Kopie, das Original bleibt)")
            except OSError as e:
                show_error(self, "Nicht kopiert", fs_error_text(e, f"{Path(f).name} nicht kopiert"))
                break
        self.refresh()

    def _delete(self):
        p = self._sel()
        if not p:
            return
        m = AB.read_meta(p)
        extra = "<br><br><b>Dieses Backup ist angeheftet.</b>" if m and m.pinned else ""
        if confirm(self, "Löschen", f"{p.name} endgültig löschen?{extra}", "Löschen", danger=True):
            try:
                p.unlink(missing_ok=True)
                self.ctx.message.emit(f"Gelöscht: {p.name}")
            except OSError as e:
                show_error(self, "Nicht gelöscht", f"{p.name}: {e.strerror or e}")
            self.refresh()
