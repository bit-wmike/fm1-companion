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
from ..i18n import _

ROOT = Path(__file__).resolve().parents[2]


def _browser(html_text: str) -> QTextBrowser:
    b = QTextBrowser()
    b.setOpenExternalLinks(True)
    b.setHtml(html_text)
    return b


PRIVACY = _("No telemetry, no accounts. The app only asks for two things automatically (can be switched off): when "
             "an FM-1 is plugged in, the version list of its firmware on GitHub, and when the catalog is opened, the "
             "catalog of awesome-fm-1 with preview images. Everything else only when you click. Bug reports are only "
             "saved locally.")


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("About {name}").format(name=SUITE_NAME))
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
        s = QLabel(_("Version {version} · Everything around the M-VAVE FM-1 in one app: firmware, editor, backups, "
                     "sounds, MIDI, simulator, samples and recording.").format(version=__version__))
        s.setWordWrap(True)
        s.setProperty("role", "muted")
        tv.addWidget(s)
        tv.addStretch(1)
        top.addLayout(tv, 1)
        lay.addLayout(top)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs, 1)
        self.tabs.addTab(_browser(
            _("<p>Free software under the <b>GNU General Public License 3.0</b> (GPL-3.0-only).</p>")
            + f"<p>{html.escape(PRIVACY)}</p>"
            + _("<p>Unofficial community project, not affiliated with M-VAVE. No warranty (GPL-3.0, sections 15 and "
                "16). “FM-1” and “M-VAVE” are names of their owners; they are only used here to describe which "
                "device the app works with.</p>")
            + _("<p>Installation: {kind}</p>").format(kind=html.escape(diag.install_kind()))), _("About"))
        rows = []
        for e in diag.credits():
            name = html.escape(e["name"])
            if e.get("url"):
                name = f"<a href='{html.escape(e['url'])}'>{name}</a>"
            rows.append(f"<tr><td><b>{name}</b><br><small>{html.escape(e.get('kind', ''))}</small></td>"
                        f"<td>{html.escape(e.get('authors', ''))}<br><small>{html.escape(e.get('use', ''))}</small></td>"
                        f"<td>{html.escape(e.get('license', ''))}</td></tr>")
        self.credits = _browser(_("<p>The suite stands on the shoulders of these projects – thank you!</p>")
                                + "<table cellpadding='6' width='100%'><tr><th align='left'>" + _("Project")
                                + "</th><th align='left'>" + _("By / used for") + "</th><th align='left'>"
                                + _("License") + "</th></tr>" + "".join(rows) + "</table>")
        self.tabs.addTab(self.credits, _("Credits && Licenses"))
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
        self.tabs.addTab(_browser(f"<table cellpadding='3'>{sysrows}</table>"), _("System"))
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        bb.button(QDialogButtonBox.StandardButton.Close).setText(_("Close"))
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
            out.append((_("Pages"), _plain(a.text()), a.shortcut().toString(QKeySequence.SequenceFormat.NativeText)))
            seen.add(id(a))
    return out


class ShortcutsDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.setWindowTitle(_("Keyboard shortcuts"))
        self.resize(560, 520)
        lay = QVBoxLayout(self)
        lay.addWidget(heading(_("Keyboard shortcuts"), _("Tip: with Ctrl+K you can find every page and every action by search.")))
        self.filter = QLineEdit()
        self.filter.setPlaceholderText(_("Filter …"))
        self.filter.setClearButtonEnabled(True)
        lay.addWidget(self.filter)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([_("Action"), _("Shortcut")])
        self.tree.setRootIsDecorated(False)
        self.tree.setColumnWidth(0, 360)
        lay.addWidget(self.tree, 1)
        for area, text, key in collect_shortcuts(window):
            it = QTreeWidgetItem([f"{text}   ({area})", key])
            self.tree.addTopLevelItem(it)
        self.filter.textChanged.connect(self._filter)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.button(QDialogButtonBox.StandardButton.Close).setText(_("Close"))
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _filter(self, t):
        t = t.lower()
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            it.setHidden(bool(t) and t not in (it.text(0) + it.text(1)).lower())


__all__ = ["AboutDialog", "ShortcutsDialog", "collect_shortcuts", "QWidget"]
