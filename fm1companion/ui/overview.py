# SPDX-License-Identifier: GPL-3.0-only
"""Übersicht: das aktive Gerät, Update-Status, letzte Sicherung, Schnellaktionen."""
from __future__ import annotations

import html
import json
import re

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QColorDialog, QFormLayout, QGridLayout, QHBoxLayout, QInputDialog, QLabel,
                             QPlainTextEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from .. import firmware
from .common import Card, fmt_date, fmt_size, heading, icon


class OverviewPage(QWidget):
    navigate = pyqtSignal(str)
    backup_now = pyqtSignal()
    check_updates = pyqtSignal()

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.update_info: dict = {}
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        lay = QVBoxLayout(body)
        lay.addWidget(heading("Übersicht", "Dein FM-1 auf einen Blick. Mehrere Geräte? Oben links umschalten."))

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        lay.addLayout(grid)

        # Gerät (links, über zwei Zeilen)
        self.dev_card = Card("Gerät", icon="audio-card")
        self.dev_form = QFormLayout()
        self.dev_form.setHorizontalSpacing(12)
        self.dev_card.lay.addLayout(self.dev_form)
        self.lbl = {}
        for k in ("Name", "Firmware", "Version", "Verbindung", "Seriennummer", "USB"):
            self.lbl[k] = QLabel("–")
            self.lbl[k].setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.lbl[k].setWordWrap(True)
            self.dev_form.addRow(k + ":", self.lbl[k])
        row = QHBoxLayout()
        self.b_rename = QPushButton(icon("edit-rename"), "Umbenennen")
        self.b_color = QPushButton(icon("color-management", "preferences-desktop-color"), "Farbe")
        self.b_rename.clicked.connect(self._rename)
        self.b_color.clicked.connect(self._color)
        row.addWidget(self.b_rename)
        row.addWidget(self.b_color)
        row.addStretch(1)
        self.dev_card.lay.addLayout(row)
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText("Notizen zu diesem Gerät (z. B. „das mit dem Kratzer, für Live-Sets“)")
        self.notes.setMaximumHeight(56)
        self._notes_pid = ""
        self._notes_timer = QTimer(self, interval=700, singleShot=True)   # nicht bei jedem Tastendruck speichern
        self._notes_timer.timeout.connect(self._notes)
        self.notes.textChanged.connect(self._notes_timer.start)
        self.dev_card.lay.addWidget(self.notes)
        grid.addWidget(self.dev_card, 0, 0, Qt.AlignmentFlag.AlignTop)
        right = QVBoxLayout()                       # Sicherung und Firmware dicht untereinander
        right.setSpacing(10)
        grid.addLayout(right, 0, 1)

        # Sicherung (rechts oben)
        self.bk_card = Card("Sicherung", icon="backup")
        self.bk_text = QLabel("–")
        self.bk_text.setWordWrap(True)
        self.bk_card.lay.addWidget(self.bk_text)
        r3 = QHBoxLayout()
        b3 = QPushButton(icon("document-save", "backup"), "Backup jetzt")
        b3.setProperty("variant", "primary")
        b3.clicked.connect(self.backup_now.emit)
        self.b_backup = b3
        b4 = QPushButton(icon("folder-open"), "Alle Backups")
        b4.clicked.connect(lambda: self.navigate.emit("backups"))
        r3.addWidget(b3)
        r3.addWidget(b4)
        r3.addStretch(1)
        self.bk_card.lay.addLayout(r3)
        right.addWidget(self.bk_card)

        # Firmware / Updates (rechts darunter)
        self.up_card = Card("Firmware & Updates", icon="system-software-update")
        self.up_text = QLabel("Noch nicht geprüft.")
        self.up_text.setWordWrap(True)
        self.up_card.lay.addWidget(self.up_text)
        r2 = QHBoxLayout()
        b = QPushButton(icon("view-refresh"), "Jetzt prüfen")
        b.clicked.connect(self.check_updates.emit)
        b2 = QPushButton(icon("system-software-update", "update-none"), "Zu den Firmwares")
        b2.clicked.connect(lambda: self.navigate.emit("firmware"))
        r2.addWidget(b)
        r2.addWidget(b2)
        r2.addStretch(1)
        self.up_card.lay.addLayout(r2)
        right.addWidget(self.up_card)
        right.addStretch(1)

        # Schnellzugriff: vier echte Aktionen
        q = Card("Schnellzugriff", icon="zap")
        qg = QGridLayout()
        qg.setSpacing(8)
        self.quick_buttons = {}
        for i, (txt, ic, slot, tip) in enumerate([
                ("Backup jetzt", "document-save", self.backup_now.emit, "Komplettes Backup des aktiven FM-1 anlegen (Strg+B)"),
                ("Editor öffnen", "audio-midi", lambda: self.navigate.emit("editor"), "Der Web-Editor der installierten Firmware"),
                ("Firmware prüfen", "view-refresh", self.check_updates.emit, "Nach neuen Versionen für dieses Gerät suchen"),
                ("Presets sichern", "bookmarks", self._save_presets, "Alle User-Presets des FM-1 einlesen und sichern")]):
            bb = QPushButton(icon(ic, "applications-multimedia"), txt)
            bb.setToolTip(tip)
            bb.clicked.connect(lambda _=False, f=slot: f())
            qg.addWidget(bb, i // 2, i % 2)
            self.quick_buttons[txt] = bb
        q.lay.addLayout(qg)
        grid.addWidget(q, 1, 0, Qt.AlignmentFlag.AlignTop)

        # Weitere Geräte
        self.others = Card("Bekannte Geräte", icon="computer")
        self.others_text = QLabel("–")
        self.others_text.setWordWrap(True)
        self.others.lay.addWidget(self.others_text)
        grid.addWidget(self.others, 1, 1, Qt.AlignmentFlag.AlignTop)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        lay.addStretch(1)

        ctx.changed.connect(self.refresh)
        ctx.ports_changed.connect(self.refresh)
        self.refresh()

    def attach(self, window):
        """vom Hauptfenster nach dem Laden aller Seiten: Katalog-Neuigkeiten (falls vorhanden)."""
        from .wizard import find_page
        self.window = window
        news = getattr(window.page(find_page(window, ("discover", "catalog")) or ""), "news_items", None)
        if callable(news):
            try:
                items = list(news())[:3]
            except Exception:  # noqa: BLE001
                items = []
            if items:
                nc = Card("Neu im Katalog", icon="newspaper")
                for it in items:
                    lab = QLabel(f"<b>{it.get('title', '')}</b> – {it.get('text', '')}")
                    lab.setWordWrap(True)
                    nc.lay.addWidget(lab)
                lay = self.findChild(QScrollArea).widget().layout()
                lay.insertWidget(lay.count() - 1, nc)

    def _save_presets(self):
        """Schnellzugriff „Presets sichern“: zur Presets-Seite und dort alle sichern (liest vorher ein)."""
        win = getattr(self, "window", None)
        page = win.page("presets") if win is not None else None
        self.navigate.emit("presets")
        fn = getattr(page, "backup_all", None) or getattr(page, "_backup_all", None) or getattr(page, "save_all", None)
        if callable(fn):
            fn()

    def refresh(self):
        c = self.ctx
        p = c.profile
        port = c.port
        if self._notes_timer.isActive():            # getippte Notiz noch dem bisherigen Gerät zuordnen
            self._notes_timer.stop()
            self._notes()
        self._notes_pid = p.id if p else ""
        self.lbl["Name"].setText(f"<b style='color:{p.color}'>●</b> {html.escape(p.name)}" if p else "–")
        from .device import firmware_name
        if port and port.update_mode:
            fw_text = "Update-Modus"
        elif getattr(c, "firmware_ambiguous", False):
            fw_text = "unbekannt – bitte wählen"
        else:
            fw_text = firmware_name(c.firmware_key) or ("unbekannt" if port else "–")
        self.lbl["Firmware"].setText(fw_text)
        from .common import fw_label
        if c.info:
            ver = fw_label(c.info.version)
        elif port and port.update_mode:
            ver = c.identity or "–"
        elif port and getattr(c, "identity", ""):
            ver = f"Kennung {c.identity}" + (f"  ({p.firmware_version})" if p and p.firmware_version else "")
        else:
            ver = (p.firmware_version if p and p.firmware_version else "–") if port else "–"
        if c.info and p and p.firmware_version and p.firmware_version.lstrip("vV") not in ver:
            ver += f"  ({p.firmware_version})"
        self.lbl["Version"].setText(ver)
        if port and hasattr(c, "status_text"):
            state = c.status_text()
            if c.connected or getattr(c, "midi_connected", False):
                state += f" · über {port.out_name}"
        else:
            state = "kein FM-1 angeschlossen"
        self.lbl["Verbindung"].setText(state)
        self.lbl["Seriennummer"].setText((port.serial or "keine (Gerät wird per Profil-Auswahl erkannt)") if port else "–")
        self.lbl["USB"].setText(f"{port.usb_id} · Port {port.usb_path}" if port and port.usb_id else "–")
        for b in (self.b_rename, self.b_color, self.notes):
            b.setEnabled(p is not None)
        can_backup = bool(c.connected)
        why = ("Komplettes Backup des aktiven FM-1 anlegen (Strg+B)" if can_backup else
               ("Backup geht nur bei Firmwares mit Editor-Protokoll (Melodee, Skiff, SLOOP)." if port else
                "Erst ein FM-1 anschließen."))
        for b in (self.b_backup, self.quick_buttons.get("Backup jetzt")):
            if b is not None:
                b.setEnabled(can_backup)
                b.setToolTip(why)
        self.notes.blockSignals(True)
        self.notes.setPlainText(p.notes if p else "")
        self.notes.blockSignals(False)
        self._backup_text()
        self._update_text()
        lines = []
        connected = {pr.id for pr in (c.profile_for_port(x) for x in c.ports) if pr}
        for prof in sorted(c.store.profiles.values(), key=lambda x: x.name):
            fw = firmware.spec(prof.firmware) if prof.firmware else None
            state = "aktiv" if p and prof.id == p.id else ("angeschlossen" if prof.id in connected else "nicht angeschlossen")
            ver = re.sub(r"^[vV](?=\d)", "", prof.firmware_version or "")   # wie oben: „1.6.0-rc.7“, nicht „v1.6.0-rc.7“
            what = f"{fw.name if fw else prof.firmware} {ver}".strip() or "Firmware unbekannt"
            lines.append(f"<b style='color:{prof.color}'>●</b> <b>{html.escape(prof.name)}</b> – "
                         f"{html.escape(what)} · {state}")
        self.others_text.setText("<br>".join(lines) or "Noch keine Geräte. Schließe ein FM-1 an.")

    def _backup_text(self):
        c = self.ctx
        if not c.profile:
            self.bk_text.setText("–")
            return
        try:
            folder = c.device_folder(c.settings.backups_dir)
            from .. import autobackup as AB
            files = [m.path for m in AB.list_backups(folder, recursive=False)]   # nur lesbare, neueste zuerst
        except OSError as e:
            self.bk_text.setText(f"<b>Backup-Ordner nicht erreichbar.</b> {getattr(e, 'strerror', '') or e}")
            return
        if not files:
            self.bk_text.setText("Noch kein Backup von diesem Gerät. <b>Mach am besten jetzt eins.</b>")
            return
        f = files[0]
        try:
            meta = json.loads(f.read_text("utf-8"))
            when = fmt_date(meta.get("created", ""))
            fw = meta.get("firmware", "")
        except (OSError, ValueError):
            when, fw = "?", ""
        self.bk_text.setText(f"Letztes Backup: <b>{when}</b> ({fw}), {len(files)} insgesamt, "
                             f"{fmt_size(sum(x.stat().st_size for x in files))}.")

    def set_update_info(self, info: dict):
        self.update_info = info
        self._update_text()

    def _update_text(self):
        i = self.update_info
        c = self.ctx
        if not c.profile:
            self.up_text.setText("Kein Gerät angeschlossen.")
            return
        if c.port and c.port.update_mode:
            self.up_text.setText("Das FM-1 wartet im Update-Modus – erst die Installation abschließen (Seite „Rettung“).")
            return
        if i and i.get("firmware") and i.get("firmware") != c.firmware_key:
            i = {}                                   # Ergebnis eines anderen Geräts gilt hier nicht
        if not c.firmware_key and not i:
            self.up_text.setText("Firmware unbekannt – bitte wählen (Hinweis oben) oder „Zu den Firmwares“ (Firmware+)."
                                 if getattr(c, "firmware_ambiguous", False) else
                                 "Firmware unbekannt – ein Versionscheck ist so nicht möglich.")
            return
        if not i:
            self.up_text.setText("Noch nicht geprüft.")
            return
        if i.get("error"):
            self.up_text.setText(f"Prüfen nicht möglich: {i['error']}")
            return
        cur, new = i.get("current"), i.get("latest")
        chan = "inkl. Release Candidates" if c.settings.channel == "rc" else "nur stabile Versionen"
        if new and cur and new != cur:
            self.up_text.setText(f"<b>Neue Version verfügbar: {new}</b><br>Installiert: {cur} ({chan})")
        elif new and not cur:
            self.up_text.setText(f"Neueste Version: {new} ({chan}). Welche Version installiert ist, weiß die App "
                                 "nicht – sie merkt es sich ab der ersten Installation über die App.")
        elif cur:
            self.up_text.setText(f"Aktuell: {cur} ({chan})")
        else:
            self.up_text.setText("Keine Versionen gefunden – bist du online?")

    def _rename(self):
        p = self.ctx.profile
        name, ok = QInputDialog.getText(self, "Gerät umbenennen", "Name:", text=p.name)
        if ok and name.strip():
            try:                                    # Backups wandern mit (wie auf der Geräte-Seite)
                self.ctx.rename_profile(name.strip())
            except OSError as e:
                from .common import show_error
                show_error(self, "Umbenennen nicht möglich", str(e))

    def _color(self):
        p = self.ctx.profile
        col = QColorDialog.getColor(QColor(p.color), self, "Farbe für dieses Gerät")
        if col.isValid():
            p.color = col.name()
            self.ctx.store.save()
            self.ctx.changed.emit()

    def _notes(self):
        pid = getattr(self, "_notes_pid", "")
        p = self.ctx.store.profiles.get(pid) if pid else None
        if p and p.notes != self.notes.toPlainText():
            p.notes = self.notes.toPlainText()
            self.ctx.store.save()
