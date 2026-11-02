# SPDX-License-Identifier: GPL-3.0-only
"""Einstellungen."""
from __future__ import annotations

from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
                             QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QSpinBox,
                             QTabWidget, QVBoxLayout, QWidget)
from PyQt6.QtCore import Qt

from .. import config, firmware
from .common import confirm, german_buttons, icon, show_error


class SettingsDialog(QDialog):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        s = ctx.settings
        self.setWindowTitle("Einstellungen")
        self.setMinimumSize(620, 460)
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        lay.addWidget(tabs)

        # Allgemein
        g = QWidget()
        f = QFormLayout(g)
        self.channel = QComboBox()
        self.channel.addItem("stabile Versionen (empfohlen)", "stable")
        self.channel.addItem("auch Release Candidates (Vorabversionen, neueste Funktionen)", "rc")
        self.channel.setToolTip("Stabil: nur fertige Versionen. Release Candidates (RC): Vorabversionen mit den "
                                "neuesten Funktionen – gut getestet, aber noch nicht final.")
        self.channel.setCurrentIndex(max(0, self.channel.findData(s.channel)))
        self.check = QCheckBox("beim Anstecken eines FM-1 nach Firmware-Updates suchen (fragt GitHub)")
        self.check.setToolTip("Holt die Versionsliste der Firmware des FM-1 von GitHub. Ausgeschaltet geht die App "
                              "dafür nur auf deinen Klick ins Netz.")
        self.check.setChecked(s.check_on_start)
        self.autobk = QCheckBox("vor jedem Update automatisch sichern")
        self.autobk.setChecked(s.auto_backup_before_update)
        self.keep = QSpinBox()
        self.keep.setRange(0, 500)
        self.keep.setSpecialValueText("alle behalten")
        self.keep.setValue(s.keep_backups)
        self.keep.setToolTip("Wie viele automatische Backups pro Gerät behalten werden. Manuelle Backups bleiben immer.")
        f.addRow("Update-Kanal:", self.channel)
        f.addRow("", self.check)
        f.addRow("", self.autobk)
        f.addRow("Automatische Backups behalten:", self.keep)
        tabs.addTab(g, "Allgemein")

        # Aussehen
        from . import theme as th
        pr = th.prefs()
        ap = QWidget()
        af0 = QFormLayout(ap)
        self.theme = QComboBox()
        for k, t in th.MODES:
            self.theme.addItem(t, k)
        self.theme.setCurrentIndex(max(0, self.theme.findData(s.theme)))
        self.accent = QComboBox()
        for name, col in th.ACCENTS:
            self.accent.addItem(self._swatch(col), name, col)
        if self.accent.findData(pr.get("accent")) < 0:
            self.accent.addItem(self._swatch(pr.get("accent")), "Eigene Farbe", pr.get("accent"))
        self.accent.addItem(icon("color-management"), "Eigene Farbe wählen …", "")
        self.accent.setCurrentIndex(max(0, self.accent.findData(pr.get("accent"))))
        self.accent.activated.connect(self._accent_pick)
        self.icons = QComboBox()
        self.icons.addItem("App-Design (einheitlich)", "app")
        self.icons.addItem("System-Thema (Breeze/Papirus)", "system")
        self.icons.setCurrentIndex(max(0, self.icons.findData(pr.get("icons", "app"))))
        self.zoom = QSpinBox()
        self.zoom.setRange(80, 160)
        self.zoom.setSingleStep(10)
        self.zoom.setSuffix(" %")
        self.zoom.setValue(round(float(pr.get("zoom", 1.0)) * 100))
        self.notify = QComboBox()
        for k, t in (("background", "wenn das Fenster im Hintergrund ist"), ("always", "immer"), ("never", "nie")):
            self.notify.addItem(t, k)
        self.notify.setCurrentIndex(max(0, self.notify.findData(pr.get("desktop_notify", "background"))))
        self.splash = QCheckBox("Startbildschirm anzeigen")
        self.splash.setChecked(bool(pr.get("splash", True)))
        af0.addRow("Design:", self.theme)
        af0.addRow("Akzentfarbe:", self.accent)
        af0.addRow("Symbole:", self.icons)
        af0.addRow("Größe der Oberfläche:", self.zoom)
        af0.addRow("Desktop-Benachrichtigungen:", self.notify)
        af0.addRow("", self.splash)
        wiz = QPushButton(icon("tools-wizard"), "Einrichtungs-Assistent erneut starten …")
        wiz.clicked.connect(self._wizard)
        af0.addRow("", wiz)
        tabs.addTab(ap, "Aussehen")

        # Ordner
        o = QWidget()
        ol = QVBoxLayout(o)
        of = QFormLayout()
        self.root = QLineEdit(s.user_root)
        b = QPushButton(icon("folder-open"), "")
        b.clicked.connect(lambda: self._pick(self.root))
        h = QHBoxLayout()
        h.addWidget(self.root)
        h.addWidget(b)
        of.addRow("Hauptordner:", h)
        ol.addLayout(of)
        hint = QLabel("Darin liegen Backups, Projekte, Kits, Aufnahmen, Firmware und der Sample-Ordner „Samples“.")
        hint.setWordWrap(True)
        ol.addWidget(hint)
        ol.addWidget(QLabel("Weitere Sample-Ordner (z. B. deine Pocket-Operator-Samples):"))
        self.roots = QListWidget()
        for r in s.sample_roots:
            self.roots.addItem(r)
        ol.addWidget(self.roots, 1)
        b_add = QPushButton(icon("list-add"), "Hinzufügen …")
        b_add.clicked.connect(self._add_root)
        b_rm = QPushButton(icon("list-remove"), "Entfernen")
        b_rm.clicked.connect(lambda: self.roots.takeItem(self.roots.currentRow()))
        h2 = QHBoxLayout()
        h2.addWidget(b_add)
        h2.addWidget(b_rm)
        h2.addStretch(1)
        ol.addLayout(h2)
        self.norm = QCheckBox("neue Kit-Sounds normalisieren")
        self.norm.setChecked(s.sample_default_normalize)
        self.trim = QCheckBox("Stille am Anfang/Ende abschneiden")
        self.trim.setChecked(s.sample_default_trim)
        ol.addWidget(self.norm)
        ol.addWidget(self.trim)
        tabs.addTab(o, "Ordner && Samples")

        # Aufnahme
        a = QWidget()
        af = QFormLayout(a)
        self.aud = QLineEdit(s.audacity_cmd)
        self.open_aud = QCheckBox("nach der Aufnahme in Audacity öffnen")
        self.open_aud.setChecked(s.record_open_audacity)
        af.addRow("Audacity-Befehl:", self.aud)
        af.addRow("", self.open_aud)
        tabs.addTab(a, "Aufnahme")

        # Geräte
        d = QWidget()
        dl = QVBoxLayout(d)
        intro = QLabel("Bekannte FM-1. Die Firmware-Version trägt die App beim Installieren selbst ein; "
                       "für vorher installierte Firmware kannst du sie hier setzen.")
        intro.setWordWrap(True)                 # vorher bei 620 px Dialogbreite abgeschnitten
        dl.addWidget(intro)
        self.devs = QListWidget()
        dl.addWidget(self.devs, 1)
        b_ren = QPushButton(icon("edit-rename"), "Umbenennen")
        b_ren.clicked.connect(self._dev_rename)
        b_ver = QPushButton(icon("document-edit"), "Firmware-Version setzen")
        b_ver.clicked.connect(self._dev_version)
        b_del = QPushButton(icon("edit-delete"), "Vergessen")
        b_del.clicked.connect(self._dev_delete)
        h3 = QHBoxLayout()
        for bb in (b_ren, b_ver, b_del):
            h3.addWidget(bb)
        h3.addStretch(1)
        dl.addLayout(h3)
        tabs.addTab(d, "Geräte")
        self._fill_devs()

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        german_buttons(bb)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    @staticmethod
    def _swatch(col: str):
        from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap
        pm = QPixmap(16, 16)
        pm.fill(QColor(0, 0, 0, 0))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(col))
        p.setPen(QColor(0, 0, 0, 60))
        p.drawEllipse(1, 1, 14, 14)
        p.end()
        return QIcon(pm)

    def _accent_pick(self, i):
        if self.accent.itemData(i):
            return
        from PyQt6.QtGui import QColor
        from PyQt6.QtWidgets import QColorDialog
        col = QColorDialog.getColor(QColor(self.accent.itemData(0) or "#ff7a1a"), self, "Akzentfarbe")
        if col.isValid():
            self.accent.insertItem(i, self._swatch(col.name()), "Eigene Farbe", col.name())
            self.accent.setCurrentIndex(i)
        else:                                   # abgebrochen: bisherige Akzentfarbe behalten
            from . import theme as th
            self.accent.setCurrentIndex(max(0, self.accent.findData(th.prefs().get("accent"))))

    def _wizard(self):
        w = self.parent()
        self.reject()
        if w is not None and hasattr(w, "open_wizard"):
            w.open_wizard()

    def _pick(self, edit):
        dd = QFileDialog.getExistingDirectory(self, "Ordner wählen", edit.text())
        if dd:
            edit.setText(dd)

    def _add_root(self):
        dd = QFileDialog.getExistingDirectory(self, "Sample-Ordner")
        if dd:
            self.roots.addItem(dd)

    def _fill_devs(self):
        self.devs.clear()
        for p in sorted(self.ctx.store.profiles.values(), key=lambda x: x.name):
            fw = firmware.spec(p.firmware)
            what = f"{fw.name if fw else p.firmware} {p.firmware_version}".strip() or "Firmware unbekannt"
            it = QListWidgetItem(f"{p.name} – {what}" + (f"  · Seriennr. {p.serial}" if p.serial else ""))
            it.setData(Qt.ItemDataRole.UserRole, p.id)
            self.devs.addItem(it)

    def _cur(self):
        it = self.devs.currentItem()
        return self.ctx.store.profiles.get(it.data(Qt.ItemDataRole.UserRole)) if it else None

    def _dev_rename(self):
        p = self._cur()
        if p:
            name, ok = QInputDialog.getText(self, "Umbenennen", "Name:", text=p.name)
            if ok and name.strip():
                p.name = name.strip()
                self.ctx.store.save()
                self._fill_devs()

    def _dev_version(self):
        p = self._cur()
        if not p:
            return
        keys = [s.key for s in firmware.catalog()]
        key, ok = QInputDialog.getItem(self, "Firmware", "Firmware:", keys, max(0, keys.index(p.firmware)) if p.firmware in keys else 0, False)
        if not ok:
            return
        tag, ok = QInputDialog.getText(self, "Version", "Version (wie im Release, z. B. v1.6.0-rc.7):", text=p.firmware_version)
        if ok:
            p.firmware, p.firmware_version = key, tag.strip()
            self.ctx.store.save()
            self._fill_devs()
            self.ctx.changed.emit()

    def _dev_delete(self):
        p = self._cur()
        if p and confirm(self, "Gerät vergessen", f"{p.name} vergessen? Backups und Projekte bleiben erhalten.", "Vergessen"):
            self.ctx.store.delete(p.id)
            self._fill_devs()

    def _ok(self):
        s = self.ctx.settings
        s.channel = self.channel.currentData()
        s.check_on_start = self.check.isChecked()
        s.auto_backup_before_update = self.autobk.isChecked()
        s.keep_backups = self.keep.value()
        s.theme = self.theme.currentData()
        from . import theme as th
        pr = th.prefs()
        pr["accent"] = self.accent.currentData() or pr.get("accent")
        pr["icons"] = self.icons.currentData()
        pr["zoom"] = self.zoom.value() / 100
        pr["desktop_notify"] = self.notify.currentData()
        pr["splash"] = self.splash.isChecked()
        s.user_root = self.root.text().strip() or s.user_root
        s.sample_roots = [self.roots.item(i).text() for i in range(self.roots.count())]
        s.sample_default_normalize = self.norm.isChecked()
        s.sample_default_trim = self.trim.isChecked()
        s.audacity_cmd = self.aud.text().strip() or "audacity"
        s.record_open_audacity = self.open_aud.isChecked()
        if not config.save(s):              # nicht beschreibbar: sagen, dass es nur bis zum Beenden gilt
            show_error(self, "Einstellungen nicht gespeichert", config.save_error)
        config.ensure_dirs()
        self.accept()
