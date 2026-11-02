# SPDX-License-Identifier: GPL-3.0-only
"""Über-Dialog (Suite-Branding, Mitwirkende, Lizenzen, System) und Tastenkürzel-Übersicht."""
from __future__ import annotations

import html
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QTabWidget, QTextBrowser,
                             QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import SUITE_NAME, __version__, diag
from .common import heading

ROOT = Path(__file__).resolve().parents[2]


def _browser(html_text: str) -> QTextBrowser:
    b = QTextBrowser()
    b.setOpenExternalLinks(True)
    b.setHtml(html_text)
    return b


PRIVACY = ("Ohne Telemetrie, ohne Konten. Automatisch fragt die App nur zwei Dinge ab (abschaltbar): beim Anstecken "
           "eines FM-1 die Versionsliste seiner Firmware auf GitHub und beim Öffnen des Katalogs den Katalog von "
           "awesome-fm-1 samt Vorschaubildern. Alles andere nur auf deinen Klick. Fehlerberichte werden nur lokal "
           "gespeichert.")


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Über {SUITE_NAME}")
        self.resize(680, 560)
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        logo = QLabel()
        from .main_window import app_icon
        logo.setPixmap(app_icon().pixmap(84, 84))
        top.addWidget(logo, 0, Qt.AlignmentFlag.AlignTop)
        tv = QVBoxLayout()
        t = QLabel(f"{SUITE_NAME} {__version__.rsplit('.', 1)[0]}")
        t.setProperty("role", "pageTitle")
        tv.addWidget(t)
        s = QLabel(f"Version {__version__} · Alles rund ums M-VAVE FM-1 in einer App: Firmware, Editor, Backups, "
                   "Klänge, MIDI, Simulator, Samples und Aufnahme.")
        s.setWordWrap(True)
        s.setProperty("role", "muted")
        tv.addWidget(s)
        tv.addStretch(1)
        top.addLayout(tv, 1)
        lay.addLayout(top)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs, 1)
        self.tabs.addTab(_browser(
            "<p>Freie Software unter der <b>GNU General Public License 3.0</b> (GPL-3.0-only).</p>"
            f"<p>{html.escape(PRIVACY)}</p>"
            "<p>Inoffizielles Community-Projekt, nicht mit M-VAVE verbunden. Keine Gewährleistung (GPL-3.0, "
            "Abschnitte 15 und 16). „FM-1“ und „M-VAVE“ sind Namen ihrer Inhaber; sie dienen hier nur "
            "zur Beschreibung, mit welchem Gerät die App arbeitet.</p>"
            f"<p>Installation: {html.escape(diag.install_kind())}</p>"), "Über")
        rows = []
        for e in diag.credits():
            name = html.escape(e["name"])
            if e.get("url"):
                name = f"<a href='{html.escape(e['url'])}'>{name}</a>"
            rows.append(f"<tr><td><b>{name}</b><br><small>{html.escape(e.get('kind', ''))}</small></td>"
                        f"<td>{html.escape(e.get('authors', ''))}<br><small>{html.escape(e.get('use', ''))}</small></td>"
                        f"<td>{html.escape(e.get('license', ''))}</td></tr>")
        self.credits = _browser("<p>Die Suite steht auf den Schultern dieser Projekte – danke!</p>"
                                "<table cellpadding='6' width='100%'><tr><th align='left'>Projekt</th>"
                                "<th align='left'>Von / wofür</th><th align='left'>Lizenz</th></tr>"
                                + "".join(rows) + "</table>")
        self.tabs.addTab(self.credits, "Mitwirkende && Lizenzen")
        gpl = ""
        for f in (ROOT / "LICENSE", Path(__file__).resolve().parents[1] / "LICENSE", Path("/usr/share/common-licenses/GPL-3")):
            if f.exists():
                gpl = f.read_text("utf-8", errors="replace")
                break
        lic = QTextBrowser()
        lic.setPlainText(gpl or "GNU General Public License 3.0 – https://www.gnu.org/licenses/gpl-3.0.html")
        self.tabs.addTab(lic, "GPL-3.0")
        sysrows = "".join(f"<tr><td>{html.escape(k)}:&nbsp;</td><td>{html.escape(v)}</td></tr>"
                          for k, v in diag.versions().items())
        self.tabs.addTab(_browser(f"<table cellpadding='3'>{sysrows}</table>"), "System")
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        bb.button(QDialogButtonBox.StandardButton.Close).setText("Schließen")
        lay.addWidget(bb)


def _plain(text: str) -> str:
    """Mnemonik-& entfernen, echte && als & behalten („Samples && Kits“ -> „Samples & Kits“)."""
    return text.replace("&&", "\x00").replace("&", "").replace("\x00", "&")


def collect_shortcuts(window) -> list[tuple[str, str, str]]:
    """(Bereich, Aktion, Kürzel) aus Menüs und Fensteraktionen."""
    out, seen = [], set()

    def walk(menu, path):
        for a in menu.actions():
            if a.menu():
                walk(a.menu(), path + [_plain(a.menu().title())])
            elif not a.isSeparator() and not a.shortcut().isEmpty():
                key = a.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
                out.append((" › ".join(path), _plain(a.text()), key))
                seen.add(id(a))
    for a in window.menuBar().actions():
        if a.menu():
            walk(a.menu(), [_plain(a.menu().title())])
    for a in window.findChildren(QAction):
        if id(a) not in seen and not a.shortcut().isEmpty() and a.parent() is window and not a.menu():
            out.append(("Seiten", _plain(a.text()), a.shortcut().toString(QKeySequence.SequenceFormat.NativeText)))
            seen.add(id(a))
    return out


class ShortcutsDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.setWindowTitle("Tastenkürzel")
        self.resize(560, 520)
        lay = QVBoxLayout(self)
        lay.addWidget(heading("Tastenkürzel", "Tipp: Mit Strg+K findest du jede Seite und jede Aktion per Suche."))
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filtern …")
        self.filter.setClearButtonEnabled(True)
        lay.addWidget(self.filter)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Aktion", "Kürzel"])
        self.tree.setRootIsDecorated(False)
        self.tree.setColumnWidth(0, 360)
        lay.addWidget(self.tree, 1)
        for area, text, key in collect_shortcuts(window):
            it = QTreeWidgetItem([f"{text}   ({area})", key])
            self.tree.addTopLevelItem(it)
        self.filter.textChanged.connect(self._filter)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.button(QDialogButtonBox.StandardButton.Close).setText("Schließen")
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _filter(self, t):
        t = t.lower()
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            it.setHidden(bool(t) and t not in (it.text(0) + it.text(1)).lower())


__all__ = ["AboutDialog", "ShortcutsDialog", "collect_shortcuts", "QWidget"]
