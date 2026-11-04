# SPDX-License-Identifier: GPL-3.0-only
"""Hilfe-Center: Anleitungen (Markdown aus data/help), Suche, Links in die App und Diagnose."""
from __future__ import annotations

import html
import re
from pathlib import Path

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QFont, QTextBlockFormat, QTextCharFormat, QTextCursor, QTextDocument
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit,
                             QPushButton, QSplitter, QTabWidget, QTextBrowser, QVBoxLayout, QWidget)

from . import PageSpec, load_errors
from ... import applog, diag
from .. import theme
from ..common import heading, icon, run_job
from ..responsive import reflow
from ...i18n import _

HELP_ROOT = Path(__file__).resolve().parents[2] / "data" / "help"   # one folder per language: help/<lang>/
HELP_FALLBACK = "de"                     # the complete set of pages; other languages may have fewer
HELP_DIR = HELP_ROOT / HELP_FALLBACK     # (kept for code that expects the old name)
ACTIONS = {"syscheck": "open_syscheck", "shortcuts": "show_shortcuts", "wizard": "open_wizard",
           "whatsnew": "open_whats_new", "report": "save_report", "palette": "open_palette", "about": "_about"}


def help_files(lang: str | None = None) -> list[Path]:
    """help pages for a language: help/<lang>/*.md, missing pages taken from help/de/ (same file name)."""
    from ... import i18n
    lang = lang or i18n.current_language()
    pages: dict[str, Path] = {f.name: f for f in (HELP_ROOT / HELP_FALLBACK).glob("*.md")}
    if lang != HELP_FALLBACK and (HELP_ROOT / lang).is_dir():
        pages.update({f.name: f for f in (HELP_ROOT / lang).glob("*.md")})
    return [pages[k] for k in sorted(pages)]


def topics(lang: str | None = None) -> list[tuple[str, str, str]]:
    """(key, title, Markdown) of all help pages in the UI language (fallback: German)"""
    out = []
    for f in help_files(lang):
        text = f.read_text("utf-8")
        m = re.match(r"#\s+(.+)", text)
        out.append((f.stem, m.group(1).strip() if m else f.stem, text))
    return out


def style_document(doc: QTextDocument):
    """Überschriften, Code und Tabellen im Design der App einfärben."""
    t = theme.current()
    cur = QTextCursor(doc)
    block = doc.begin()
    while block.isValid():
        lvl = block.blockFormat().headingLevel()
        if lvl:
            cur.setPosition(block.position())
            cur.setPosition(block.position() + block.length() - 1, QTextCursor.MoveMode.KeepAnchor)
            cf = QTextCharFormat()
            cf.setForeground(theme.color("text") if lvl > 1 else theme.color("accent_fg"))
            cur.mergeCharFormat(cf)
            bf = QTextBlockFormat()
            bf.setTopMargin(14 if lvl > 1 else 4)
            bf.setBottomMargin(6)
            cur.mergeBlockFormat(bf)
        block = block.next()
    del t


class HelpPage(QWidget):
    def __init__(self, ctx, window):
        super().__init__()
        self.ctx, self.window = ctx, window
        lay = QVBoxLayout(self)
        lay.addWidget(heading("Hilfe", "Anleitungen, Antworten und Diagnose – auch offline."))
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs, 1)

        # ---- Anleitungen
        g = QWidget()
        gl = QHBoxLayout(g)
        left = QVBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("In der Hilfe suchen …")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        left.addWidget(self.search)
        self.list = QListWidget()
        self.list.setMaximumWidth(260)
        self.list.currentItemChanged.connect(lambda it, _p: self._show(it))
        left.addWidget(self.list, 1)
        self.hits = QLabel("")
        self.hits.setProperty("role", "small")
        left.addWidget(self.hits)
        gl.addLayout(left)
        self.browser = QTextBrowser()
        self.browser.setOpenLinks(False)
        self.browser.anchorClicked.connect(self.link)
        gl.addWidget(self.browser, 1)
        self.tabs.addTab(g, icon("help-contents"), "Anleitungen")
        self.topics = topics()
        for key, title, _md in self.topics:
            it = QListWidgetItem(title)
            it.setData(Qt.ItemDataRole.UserRole, key)
            self.list.addItem(it)

        # ---- Diagnose
        self.diag = DiagnoseView(window)
        self.tabs.addTab(self.diag, icon("hwinfo"), "Diagnose")
        self.list.setCurrentRow(0)

    # ------------------------------------------------------------
    def open_with(self, topic: str = "", **kw):
        if topic == "diagnose":
            self.tabs.setCurrentWidget(self.diag)
            self.diag.refresh()
        elif topic:
            self.show_topic(topic)

    def activate(self):
        if self.tabs.currentWidget() is self.diag:
            self.diag.refresh()

    def show_topic(self, key: str) -> bool:
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.ItemDataRole.UserRole) == key:
                self.tabs.setCurrentIndex(0)
                self.list.item(i).setHidden(False)
                self.list.setCurrentRow(i)
                return True
        return False

    def _show(self, it):
        if it is None:
            return
        key = it.data(Qt.ItemDataRole.UserRole)
        text = next((md for k, _t, md in self.topics if k == key), "")
        self.browser.document().setMarkdown(text)
        style_document(self.browser.document())
        # immer oben anfangen: sonst steht die Schreibmarke am Dokumentende und die Ansicht springt beim
        # ersten Anzeigen nach unten (Überschrift und erster Absatz waren abgeschnitten)
        self.browser.moveCursor(QTextCursor.MoveOperation.Start)
        self.browser.verticalScrollBar().setValue(0)
        q = self.search.text().strip()
        if q:
            self.browser.moveCursor(QTextCursor.MoveOperation.Start)
            self.browser.find(q)

    def _filter(self, q: str):
        q = q.strip().lower()
        n = 0
        for i in range(self.list.count()):
            it = self.list.item(i)
            md = self.topics[i][2].lower()
            hit = not q or q in md
            it.setHidden(not hit)
            cnt = md.count(q) if q else 0
            it.setText(self.topics[i][1] + (f"  ({cnt})" if q and cnt else ""))
            n += hit
        self.hits.setText(f"{n} Treffer" if q else "")
        cur = self.list.currentItem()
        if q and (cur is None or cur.isHidden()):
            first = next((self.list.item(i) for i in range(self.list.count()) if not self.list.item(i).isHidden()), None)
            if first:
                self.list.setCurrentItem(first)
        elif cur:
            self._show(cur)

    def link(self, url: QUrl):
        s = url.toString()
        if s.startswith("app://page/"):
            key = s[len("app://page/"):].strip("/")
            if self.window.page(key) is not None:
                self.window.go(key)
            else:
                self.window.notify(f"Die Seite „{key}“ gibt es in dieser Version nicht.", "info")
        elif s.startswith("app://action/"):
            fn = getattr(self.window, ACTIONS.get(s[len("app://action/"):].strip("/"), ""), None)
            if fn:
                fn()
        elif s.startswith("help:"):
            self.show_topic(s[5:])
        elif url.scheme() in ("http", "https", "mailto"):
            QDesktopServices.openUrl(url)
        elif s.endswith(".md"):
            self.show_topic(Path(s).stem)


class DiagnoseView(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        b = QPushButton(icon("view-refresh"), "Aktualisieren")
        b.clicked.connect(self.refresh)
        b2 = QPushButton(icon("utilities-system-monitor"), "Systemcheck …")
        b2.clicked.connect(lambda: window.open_syscheck())
        b3 = QPushButton(icon("tools-report-bug"), "Fehlerbericht speichern …")
        b3.clicked.connect(lambda: window.save_report())
        b4 = QPushButton(icon("folder-open"), "Protokoll-Ordner")
        b4.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(applog.log_dir()))))
        for x in (b, b2, b3, b4):
            row.addWidget(x)
        row.addStretch(1)
        lay.addLayout(row)
        reflow(row)                                      # Knöpfe brechen bei schmalem Fenster um
        split = QSplitter(Qt.Orientation.Vertical)
        self.info = QTextBrowser()
        self.info.setOpenExternalLinks(True)
        split.addWidget(self.info)
        self.logv = QPlainTextEdit()
        self.logv.setReadOnly(True)
        self.logv.setFont(QFont("monospace"))
        self.logv.setPlaceholderText("Noch keine Einträge im Protokoll.")
        split.addWidget(self.logv)
        split.setSizes([400, 220])
        lay.addWidget(split, 1)
        self._ports = None

    def refresh(self):
        self.info.setHtml(self.html())
        self.logv.setPlainText(applog.tail(300))
        self.logv.moveCursor(QTextCursor.MoveOperation.End)
        run_job(self, lambda w: diag.midi_ports(), on_done=self._got_ports, on_fail=lambda m, tb: None)

    def _got_ports(self, res):
        self._ports = res
        self.info.setHtml(self.html())

    def html(self) -> str:
        e = html.escape
        parts = ["<h3>Seiten, die nicht geladen werden konnten</h3>"]
        if load_errors:
            for mod, tb in load_errors:
                last = tb.strip().splitlines()[-1] if tb.strip() else ""
                parts.append(f"<p><b>{e(mod)}</b>: {e(last)}</p><pre>{e(tb[-1500:])}</pre>")
        else:
            parts.append("<p>Keine – alle Seiten wurden geladen.</p>")
        parts.append("<h3>Versionen</h3><table cellpadding='2'>")
        parts += [f"<tr><td>{e(k)}:&nbsp;</td><td>{e(v)}</td></tr>" for k, v in diag.versions().items()]
        parts.append("</table><h3>Abhängigkeiten</h3><table cellpadding='2'>")
        states = diag.check_deps()
        for s in states:
            mark = "✔" if s.ok else ("✖" if s.required else "–")
            parts.append(f"<tr><td>{mark}</td><td>{e(s.package)}</td><td>{e(s.why)}"
                         f"{'' if s.ok else (' – <b>fehlt</b>' if s.required else ' – optional, fehlt')}</td></tr>")
        parts.append("</table>")
        cmd = diag.apt_command(states)
        if cmd:
            parts.append(f"<p>Nachinstallieren:<br><code>{e(cmd)}</code></p>")
        parts.append("<h3>MIDI-Anschlüsse</h3>")
        if self._ports is None:
            parts.append("<p>werden gesucht …</p>")
        else:
            ins, outs, err = self._ports
            parts.append(f"<p>MIDI nicht nutzbar: {e(err)}</p>" if err else
                         f"<p>Eingänge: {e(', '.join(ins) or 'keine')}<br>Ausgänge: {e(', '.join(outs) or 'keine')}</p>")
        parts.append(f"<h3>Protokoll</h3><p>{e(str(applog.log_file()))}</p>")
        return "".join(parts)


def _make(ctx, window):
    return HelpPage(ctx, window)


PAGE = PageSpec(key="help", title=_("Help"), group="hilfe", factory=_make, icons=("help-contents", "help-browser"),
                order=10, description=_("Guides, questions & answers, diagnostics"),
                keywords=("hilfe", "anleitung", "faq", "diagnose", "handbuch", "update-modus", "rettung",
                          "help", "guide", "manual", "diagnostics", "update mode", "recovery"))
