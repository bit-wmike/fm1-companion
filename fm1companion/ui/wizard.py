# SPDX-License-Identifier: GPL-3.0-only
"""Einrichtungs-Assistent (erster Start, jederzeit über Hilfe erneut) und „Was ist neu in 2.0“."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (QButtonGroup, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                             QLineEdit, QListWidget, QPushButton, QRadioButton, QScrollArea, QVBoxLayout, QWidget,
                             QWizard, QWizardPage)

from .. import SUITE_NAME, __version__, config, diag
from . import theme
from .common import heading, icon


def _p(text: str, role: str = "") -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setTextFormat(Qt.TextFormat.RichText)
    lab.setOpenExternalLinks(True)
    if role:
        lab.setProperty("role", role)
    return lab


class _Welcome(QWizardPage):
    def __init__(self, wz):
        super().__init__()
        self.setTitle(f"Willkommen in der {SUITE_NAME}")
        self.setSubTitle("In einer Minute ist alles eingerichtet. Du kannst den Assistenten später über "
                         "Hilfe › Einrichtungs-Assistent wieder öffnen.")
        lay = QVBoxLayout(self)
        lay.addWidget(_p("Die Suite kümmert sich um alles rund um dein M-VAVE FM-1: Firmware wechseln, Backups, "
                         "Klänge und Presets, Samples, MIDI, Simulator und Aufnahme."))
        row = QHBoxLayout()
        row.addWidget(QLabel("Aussehen:"))
        self.theme = QComboBox()
        for k, t in theme.MODES:
            self.theme.addItem(t, k)
        cur = wz.ctx.settings.theme
        self.theme.setCurrentIndex(max(0, self.theme.findData("auto" if cur == "system" and not wz.upgrade else cur)))
        self.theme.currentIndexChanged.connect(self._preview)
        row.addWidget(self.theme, 1)
        lay.addLayout(row)
        lay.addStretch(1)
        self.wz = wz

    def _preview(self):
        from PyQt6.QtWidgets import QApplication
        theme.apply(QApplication.instance(), self.theme.currentData())
        self.wz._previewed = True


class _Folder(QWizardPage):
    def __init__(self, wz):
        super().__init__()
        self.setTitle("Hauptordner")
        self.setSubTitle("Hier liegen deine Backups, Projekte, Kits, Aufnahmen und Firmware-Downloads.")
        lay = QVBoxLayout(self)
        h = QHBoxLayout()
        self.edit = QLineEdit(wz.ctx.settings.user_root)
        b = QPushButton(icon("folder-open"), "Wählen …")
        b.clicked.connect(self._pick)
        h.addWidget(self.edit, 1)
        h.addWidget(b)
        lay.addLayout(h)
        lay.addWidget(_p("Tipp: Lege den Ordner dorthin, wo du ihn auch sicherst (z. B. auf das NAS-Laufwerk). "
                         "Die App löscht ihn nie – auch nicht beim Deinstallieren.", "muted"))
        lay.addStretch(1)
        # NICHT als Pflichtfeld "root*": Qt zählt ein Pflichtfeld erst als ausgefüllt, wenn es sich vom
        # Anfangswert unterscheidet – mit vorausgefülltem ~/FM-1 blieb „Weiter“ grau, der Assistent hing fest.
        self.registerField("root", self.edit)
        self.edit.textChanged.connect(self.completeChanged)

    def isComplete(self) -> bool:
        return bool(self.edit.text().strip())

    def _pick(self):
        d = QFileDialog.getExistingDirectory(self, "Hauptordner wählen", self.edit.text())
        if d:
            self.edit.setText(d)

    def validatePage(self) -> bool:
        """Ordner muss sich anlegen und beschreiben lassen – sonst bleibt der Assistent hier und sagt warum."""
        import os
        root = Path(self.edit.text().strip() or "~").expanduser()
        try:
            root.mkdir(parents=True, exist_ok=True)
            probe = root / ".fm1c-schreibtest"
            probe.write_text("ok")
            probe.unlink()
        except OSError as e:
            from .common import show_error
            from .device import folder_error_text
            show_error(self, "Hauptordner nicht beschreibbar", folder_error_text(root, e))
            return False
        self.edit.setText(str(root))
        return True


class _Samples(QWizardPage):
    def __init__(self, wz):
        super().__init__()
        self.setTitle("Sample-Ordner")
        self.setSubTitle("Wo liegen deine Samples (z. B. Pocket-Operator-Klänge)? Optional – geht auch später.")
        lay = QVBoxLayout(self)
        self.list = QListWidget()
        for r in wz.ctx.settings.sample_roots:
            self.list.addItem(r)
        lay.addWidget(self.list, 1)
        h = QHBoxLayout()
        add = QPushButton(icon("list-add"), "Ordner hinzufügen …")
        add.clicked.connect(self._add)
        rm = QPushButton(icon("list-remove"), "Entfernen")
        rm.clicked.connect(lambda: self.list.takeItem(self.list.currentRow()))
        h.addWidget(add)
        h.addWidget(rm)
        h.addStretch(1)
        lay.addLayout(h)

    def _add(self):
        d = QFileDialog.getExistingDirectory(self, "Sample-Ordner")
        if d:
            self.list.addItem(d)

    def roots(self) -> list[str]:
        return [self.list.item(i).text() for i in range(self.list.count())]


class _Deps(QWizardPage):
    def __init__(self, wz):
        super().__init__()
        self.setTitle("Abhängigkeiten prüfen")
        self.setSubTitle("Welche Systempakete die Suite braucht und ob sie da sind.")
        lay = QVBoxLayout(self)
        self.text = _p("")
        self.text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.text)
        self.cmd_label = QLabel("")                # der apt-Befehl: umbrechend, kopierbar, Monospace
        self.cmd_label.setWordWrap(True)
        self.cmd_label.setTextFormat(Qt.TextFormat.PlainText)
        self.cmd_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.cmd_label.setStyleSheet("QLabel { font-family: monospace; padding: 6px; border: 1px solid palette(mid); "
                                     "border-radius: 6px; }")
        self.cmd_label.hide()
        lay.addWidget(self.cmd_label)
        h = QHBoxLayout()
        self.copy = QPushButton(icon("edit-copy"), "Befehl kopieren")
        self.copy.setToolTip("Den apt-Befehl in die Zwischenablage legen – dann im Terminal einfügen")
        self.copy.clicked.connect(self._copy)
        again = QPushButton(icon("view-refresh"), "Erneut prüfen")
        again.clicked.connect(self.initializePage)
        h.addWidget(self.copy)
        h.addWidget(again)
        h.addStretch(1)
        lay.addLayout(h)
        lay.addStretch(1)
        self.cmd = ""

    def initializePage(self):
        states = diag.check_deps()
        rows = "".join(f"<tr><td>{'✔' if s.ok else ('✖' if s.required else '–')}</td><td>{s.package}</td>"
                       f"<td>{s.why}{'' if s.ok else (' – <b>fehlt</b>' if s.required else ' – optional')}</td></tr>"
                       for s in states)
        self.cmd = diag.apt_command(states)
        tail = "<p>Fehlendes nachinstallieren (im Terminal):</p>" if self.cmd else "<p><b>Alles da.</b></p>"
        self.text.setText(f"<table cellpadding='2'>{rows}</table>{tail}")
        self.cmd_label.setText(self.cmd)
        self.cmd_label.setVisible(bool(self.cmd))
        self.copy.setEnabled(bool(self.cmd))

    def _copy(self):
        QGuiApplication.clipboard().setText(self.cmd)
        self.copy.setText("Kopiert ✔")
        QTimer.singleShot(2000, lambda: self.copy.setText("Befehl kopieren"))


class _Device(QWizardPage):
    def __init__(self, wz):
        super().__init__()
        self.wz = wz
        self.setTitle("FM-1 anschließen & benennen")
        self.setSubTitle("Schalte dein FM-1 ein und verbinde es per USB-Datenkabel. Es erscheint hier von selbst.")
        lay = QVBoxLayout(self)
        self.state = _p("Suche …")
        lay.addWidget(self.state)
        h = QHBoxLayout()
        h.addWidget(QLabel("Name:"))
        self.name = QLineEdit()
        self.name.setPlaceholderText("z. B. Studio-FM-1")
        h.addWidget(self.name, 1)
        lay.addLayout(h)
        lay.addWidget(_p("Ein zweites FM-1? Einfach umstecken – jedes Gerät bekommt sein eigenes Profil, eigene Farbe "
                         "und eigene Backups. Dieser Schritt ist optional.", "muted"))
        lay.addStretch(1)
        wz.ctx.changed.connect(self._update)
        wz.ctx.ports_changed.connect(self._update)
        self._named_for = None

    def initializePage(self):
        self._update()

    def _update(self):
        c = self.wz.ctx
        proto = getattr(c, "protocol", "felucca" if c.connected else "")
        if c.profile and (c.connected or proto == "generic"):
            if c.info:
                fw = c.info.version
            else:
                from .device import firmware_name
                fw = (firmware_name(c.firmware_key) or "Firmware ohne Editor-Protokoll") + \
                     (f" ({c.identity})" if c.identity else "")
            self.state.setText(f"<b style='color:{c.profile.color}'>●</b> <b>Gefunden:</b> {c.port.label} – {fw}")
            if self._named_for != c.profile.id:
                self._named_for = c.profile.id
                self.name.setText(c.profile.name)
            self.name.setEnabled(True)
        elif c.port and proto == "update":
            self.state.setText("Ein FM-1 wartet im <b>Update-Modus</b>. Nach dem Assistenten geht es auf der Seite "
                               "„Rettung“ weiter – dort wird die unterbrochene Installation abgeschlossen.")
            self.name.setEnabled(c.profile is not None)
        elif c.port:
            self.state.setText("FM-1 gefunden, es antwortet aber (noch) nicht – einen Moment. Antwortet es gar nicht: "
                               "Datenkabel prüfen, andere MIDI-Programme schließen.")
            self.name.setEnabled(c.profile is not None)
        else:
            self.state.setText("Noch kein FM-1 verbunden. Du kannst diesen Schritt auch überspringen.")
            self.name.setEnabled(False)

    def apply(self):
        c = self.wz.ctx
        n = self.name.text().strip()
        if c.profile and n and n != c.profile.name:
            try:
                c.rename_profile(n)
            except OSError as e:               # Backup-Ordner nicht verschiebbar: Name bleibt, Hinweis statt Absturz
                from .common import show_error
                show_error(self, "Umbenennen nicht möglich", str(e))


class _AutoBackup(QWizardPage):
    """Hinweis-Schritt: Was die App beim ersten Verbinden von selbst tut."""

    def __init__(self, wz):
        super().__init__()
        self.setTitle("Automatische Sicherung")
        self.setSubTitle("Beim ersten Verbinden macht die App automatisch ein Backup.")
        lay = QVBoxLayout(self)
        lay.addWidget(_p("Sobald ein FM-1 mit Melodee, Skiff oder SLOOP verbunden ist, sichert die App einmal täglich "
                         "beim Anstecken den kompletten Stand (laufende Musik, alle Projekte, User-Presets, "
                         "Einstellungen) nach <b>FM-1/Backups/&lt;Gerätename&gt;</b>. Das dauert ein paar Sekunden; "
                         "das FM-1 stoppt dafür kurz die Wiedergabe.<br><br>"
                         "Vor jeder Firmware-Installation und vor jedem Zurückspielen entsteht zusätzlich ein "
                         "Sicherheits-Backup. Automatische Backups werden nach Regeln aufgeräumt (die letzten, "
                         "das neueste je Monat und je Firmware bleiben); deine eigenen Backups löscht die App nie.<br><br>"
                         "Einstellen lässt sich das unter <b>Backups</b> und in den Einstellungen."))
        lay.addStretch(1)


class _Channel(QWizardPage):
    def __init__(self, wz):
        super().__init__()
        self.setTitle("Update-Kanal")
        self.setSubTitle("Welche Firmware-Versionen soll die App anbieten?")
        lay = QVBoxLayout(self)
        self.grp = QButtonGroup(self)
        self.stable = QRadioButton("Stabile Versionen (empfohlen)")
        self.rc = QRadioButton("Auch Release Candidates")
        for b in (self.stable, self.rc):
            self.grp.addButton(b)
            lay.addWidget(b)
        (self.rc if wz.ctx.settings.channel == "rc" else self.stable).setChecked(True)
        lay.addWidget(_p("Release Candidates (RC) sind Vorabversionen mit den neuesten Funktionen – meist gut getestet, "
                         "aber noch nicht final. Du kannst das jederzeit in den Einstellungen ändern.", "muted"))
        lay.addWidget(_p("Vor jedem Update legt die App automatisch ein Backup an.", "muted"))
        lay.addStretch(1)


class _Done(QWizardPage):
    def __init__(self, wz):
        super().__init__()
        self.setTitle("Fertig!")
        self.setSubTitle("Viel Spaß mit deinem FM-1.")
        lay = QVBoxLayout(self)
        lay.addWidget(_p("<ul><li><b>Strg+K</b> – alles finden</li><li><b>Strg+B</b> – Backup</li>"
                         "<li><b>F1</b> – Hilfe</li></ul>"
                         "Unten rechts siehst du kompakt, wie es deinem Rechner geht – ein Klick öffnet den "
                         "Systemcheck."))
        self.tour = QPushButton(icon("help-hint"), "Was ist neu in 2.0?")
        self.tour.clicked.connect(lambda: QTimer.singleShot(0, wz.window.open_whats_new) if wz.window else None)
        lay.addWidget(self.tour, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addStretch(1)


class FirstRunWizard(QWizard):
    def __init__(self, ctx, window=None, upgrade: bool = False):
        super().__init__(window)
        self.ctx, self.window, self.upgrade = ctx, window, upgrade
        self.setWindowTitle("Einrichtung")
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        from .main_window import app_icon
        self.setPixmap(QWizard.WizardPixmap.LogoPixmap, app_icon().pixmap(48, 48))
        self.setOption(QWizard.WizardOption.NoBackButtonOnStartPage, True)
        for b, t in ((QWizard.WizardButton.NextButton, "Weiter >"), (QWizard.WizardButton.BackButton, "< Zurück"),
                     (QWizard.WizardButton.FinishButton, "Fertig"), (QWizard.WizardButton.CancelButton, "Abbrechen")):
            self.setButtonText(b, t)
        self.resize(680, 500)
        self.p_welcome, self.p_folder, self.p_samples = _Welcome(self), _Folder(self), _Samples(self)
        self.p_deps, self.p_device, self.p_channel, self.p_done = _Deps(self), _Device(self), _Channel(self), _Done(self)
        self.p_autobackup = _AutoBackup(self)
        for p in (self.p_welcome, self.p_folder, self.p_samples, self.p_deps, self.p_device, self.p_autobackup,
                  self.p_channel, self.p_done):
            self.addPage(p)
        self._mode_before = ctx.settings.theme

    def accept(self):
        s = self.ctx.settings
        s.theme = self.p_welcome.theme.currentData()
        s.user_root = self.p_folder.edit.text().strip() or s.user_root
        s.sample_roots = self.p_samples.roots()
        s.channel = "rc" if self.p_channel.rc.isChecked() else "stable"
        self.p_device.apply()
        pr = theme.prefs()
        pr["wizard_done"] = True
        config.save(s)
        config.ensure_dirs()
        super().accept()

    def reject(self):
        # nur zurückstellen, wenn die Vorschau das Design wirklich geändert hat (ein unnötiges Neu-Stylen der ganzen
        # App beim Abbrechen stürzte beim zweiten Öffnen offscreen ab)
        if getattr(self, "_previewed", False):
            from PyQt6.QtWidgets import QApplication
            theme.apply(QApplication.instance(), self._mode_before)
            self._previewed = False
        super().reject()


# ---------------------------------------------------------------- Was ist neu ---
# (Titel, Text, Symbol, mögliche Seiten-Schlüssel/Modulnamen)
NEWS = [
    ("Studio", "Der digitale Zwilling deines FM-1: dieselbe Firmware im Rechner – spielen, als WAV (auch "
     "Einzelspuren) für Logic/Audacity ausgeben und zurück aufs FM-1 schreiben.", "audio-lines", ("studio",)),
    ("Firmware+", "Alle Firmwares an einem Ort: Katalog mit allem, was es fürs FM-1 gibt, dazu Installieren, "
     "Versionen wechseln und zurück zum Original.", "compass", ("discover", "catalog")),
    ("Rettung", "Schritt für Schritt aus dem Update-Modus zurück – auch wenn das Display dunkel bleibt.", "life-buoy",
     ("recovery",)),
    ("Simulator", "FM-1-Firmwares direkt im Rechner spielen, ohne Gerät.", "monitor-play", ("simulator",)),
    ("Presets", "Alle User-Presets durchsuchen, sichern, tauschen und zurückspielen.", "library", ("presets",)),
    ("FM-Voices", "DX7-Klänge importieren und als FM-Voices aufs FM-1 bringen.", "audio-lines", ("voices",)),
    ("MIDI", "Ports verbinden, mitlesen, Clock senden.", "piano", ("midi",)),
    ("Klangerzeuger", "Die MIDI-Spuren 5–8 hörbar machen – mit FluidSynth im Rechner.", "speaker", ("soundmodule",)),
    ("Projekte › Ansicht & Export", "Projekte als Spuren und Patterns ansehen und als MIDI exportieren.", "list-music",
     ("project_view", "projectview")),
    ("Komponieren", "Ideen, Akkorde und Patterns erzeugen und aufs Gerät spielen.", "pen-tool", ("compose",)),
    ("Backups › Durchsuchen", "In Backups hineinschauen, vergleichen, Teile herausholen.", "archive",
     ("backup_browser", "backupbrowser")),
    ("Geräte", "Alle deine FM-1 mit Profil, Farbe und Verlauf.", "keyboard-music", ("devices",)),
    ("Hilfe-Center", "Anleitungen, FAQ und Diagnose – auch offline. F1.", "book-open", ("help",)),
    ("Befehlssuche", "Strg+K: jede Seite, jede Aktion, jedes Hilfe-Thema per Tastatur.", "command", ("@palette",)),
    ("Systemcheck", "Findet Wackelkontakte, Unterspannung und USB-Audio-Fehler im Kernel-Protokoll.", "activity",
     ("@syscheck",)),
    ("Neues Design", "Hell, dunkel oder automatisch – mit Akzentfarbe und einheitlichen Symbolen.", "palette",
     ("@settings",)),
]


def find_page(window, candidates) -> str | None:
    """Seiten-Schlüssel finden: direkt oder über den Modulnamen der Seite (ui/pages/<name>.py)."""
    for c in candidates:
        if window.page(c) is not None:
            return c
    for key, spec in getattr(window, "specs", {}).items():
        mod = getattr(spec.factory, "__module__", "") or ""
        if any(mod.endswith("." + c) for c in candidates):
            return key
    return None


class WhatsNewDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle("Was ist neu in 2.0")
        self.resize(860, 620)
        lay = QVBoxLayout(self)
        lay.addWidget(heading(f"Neu in der {SUITE_NAME} {__version__}",
                              "Viel ist viel, mehr ist besser: alles fürs FM-1 jetzt in einer App. Klick auf eine Karte "
                              "öffnet den Bereich."))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        grid = QGridLayout(body)
        grid.setSpacing(12)
        scroll.setWidget(body)
        lay.addWidget(scroll, 1)
        self.cards: dict[str, QPushButton | None] = {}
        n = 0
        for title, text, ic, cands in NEWS:
            target = self._target(cands)
            card = QFrame()
            card.setObjectName("Tile")
            v = QVBoxLayout(card)
            h = QHBoxLayout()
            il = QLabel()
            il.setPixmap(icon(ic).pixmap(22, 22))
            h.addWidget(il)
            t = QLabel(title)
            t.setProperty("role", "cardTitle")
            h.addWidget(t, 1)
            v.addLayout(h)
            v.addWidget(_p(text, "muted"))
            if target:
                b = QPushButton("Öffnen")
                b.setProperty("variant", "flat")
                b.clicked.connect(lambda _=False, tg=target: self._open(tg))
                v.addWidget(b, 0, Qt.AlignmentFlag.AlignLeft)
                self.cards[title] = b
            else:
                v.addWidget(_p("in dieser Ausgabe nicht enthalten", "small"))
                self.cards[title] = None
            grid.addWidget(card, n // 3, n % 3)
            n += 1
        row = QHBoxLayout()
        row.addStretch(1)
        ok = QPushButton("Los geht's")
        ok.setProperty("variant", "primary")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        row.addWidget(ok)
        lay.addLayout(row)

    def _target(self, cands):
        if cands and cands[0].startswith("@"):
            return cands[0]
        return find_page(self.window, cands)

    def _open(self, target: str):
        self.accept()
        w = self.window
        if target == "@palette":
            w.open_palette()
        elif target == "@syscheck":
            w.open_syscheck()
        elif target == "@settings":
            w._settings()
        else:
            w.go(target)


__all__ = ["FirstRunWizard", "WhatsNewDialog", "find_page", "Path"]
