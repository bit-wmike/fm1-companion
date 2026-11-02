# SPDX-License-Identifier: GPL-3.0-only
"""Befehlssuche (Strg+K): unscharfe Suche über Seiten, Menü-Aktionen, Hilfe-Themen und eigene Befehle."""
from __future__ import annotations

import re

from PyQt6.QtCore import QEvent, QRectF, QSize, Qt
from PyQt6.QtGui import QAction, QColor, QFont, QIcon, QKeySequence, QPainter
from PyQt6.QtWidgets import (QFrame, QLineEdit, QListWidget, QListWidgetItem, QStyle, QStyledItemDelegate,
                             QVBoxLayout, QWidget)

from . import theme
from .common import icon


def fuzzy_score(query: str, text: str) -> float:
    """0 = kein Treffer. Belohnt Teilwort am Anfang, Wortanfänge und zusammenhängende Buchstaben."""
    q, t = query.lower().strip(), text.lower()
    if not q:
        return 1.0
    if t.startswith(q):
        return 1000 - len(t) * 0.1
    i = t.find(q)
    if i >= 0:
        return (800 if i == 0 or not t[i - 1].isalnum() else 600) - i - len(t) * 0.1
    # Teilfolge
    score, pos, run = 0.0, -1, 0
    for ch in q:
        if ch == " ":
            continue
        nxt = t.find(ch, pos + 1)
        if nxt < 0:
            return 0.0
        run = run + 1 if nxt == pos + 1 else 0
        score += 10 + run * 6 + (8 if nxt == 0 or not t[nxt - 1].isalnum() else 0) - min(nxt - pos, 10) * 0.5
        pos = nxt
    return max(1.0, score)


class Entry:
    def __init__(self, title, subtitle, callback, icon_: QIcon | None = None, keywords="", shortcut="", kind=""):
        self.title, self.subtitle, self.callback = title, subtitle, callback
        self.icon, self.keywords, self.shortcut, self.kind = icon_ or QIcon(), keywords, shortcut, kind

    def score(self, q: str) -> float:
        best = max(fuzzy_score(q, self.title) * 1.2, fuzzy_score(q, self.keywords) * 0.8,
                   fuzzy_score(q, self.subtitle) * 0.6)
        if not best and " " in q.strip():   # alle Wörter müssen irgendwo vorkommen
            hay = f"{self.title} {self.keywords} {self.subtitle}".lower()
            if all(w in hay for w in q.lower().split()):
                best = 50
        return best


def collect(window) -> list[Entry]:
    out: list[Entry] = []
    from .pages import GROUP_TITLES
    for key, spec in window.specs.items():
        out.append(Entry(spec.title, (GROUP_TITLES.get(spec.group, spec.group) + " · " + (spec.description or "Seite")),
                         lambda k=key: window.go(k), icon(*spec.icons) if spec.icons else QIcon(),
                         " ".join(spec.keywords) + " " + key, kind="Seite"))
    # Reiter zusammengelegter Seiten (FM-1-Zustand, Projekt-Ansicht, Aus MIDI-Datei, Backup-Browser, FM-Voices,
    # Simulator, Klangerzeuger …) bleiben einzeln auffindbar
    try:
        from .main_window import ALIASES, COMPOSITES, FLAT_COMPOSITES
        tab_titles = {sub: title for comp in (COMPOSITES, FLAT_COMPOSITES) for tabs in comp.values()
                      for _tab, title, sub in tabs}
        for sub_key, (main_key, _tab) in ALIASES.items():
            spec = getattr(window, "absorbed", {}).get(sub_key)
            main = window.specs.get(main_key)
            title = tab_titles.get(sub_key, sub_key)
            desc = spec.description if spec else ""
            where = (main.title if main else main_key) + " › " + title
            kws = " ".join(spec.keywords) if spec else ""
            out.append(Entry(title, where + (" · " + desc if desc else ""), lambda k=sub_key: window.go(k),
                             icon(*spec.icons) if spec and spec.icons else (icon(*main.icons) if main and main.icons else QIcon()),
                             kws + " " + sub_key + " " + (spec.title if spec else ""), kind="Seite"))
    except Exception:  # noqa: BLE001 – Befehlssuche darf nie an den Reitern scheitern
        pass
    pages = {spec.title.lower() for spec in window.specs.values()}

    def walk(menu, path):
        for a in menu.actions():
            if a.menu():
                walk(a.menu(), path + [a.menu().title().replace("&", "")])
            elif not a.isSeparator() and a.isEnabled() and a.text():
                t = a.text().replace("&", "").replace("…", "").strip()
                if t.lower() in pages:            # Menüpunkt öffnet nur dieselbe Seite -> nicht doppelt zeigen
                    continue
                out.append(Entry(t, " › ".join(path),
                                 a.trigger, a.icon(), a.statusTip() or a.toolTip(),
                                 a.shortcut().toString(QKeySequence.SequenceFormat.NativeText), "Aktion"))
    for a in window.menuBar().actions():
        if a.menu():
            walk(a.menu(), [a.menu().title().replace("&", "")])
    for c in getattr(window, "commands", []):
        out.append(Entry(c["title"], c.get("subtitle", ""), c["callback"], icon(c["icon"]) if c.get("icon") else QIcon(),
                         " ".join(c.get("keywords", ())), kind="Befehl"))
    taken = {e.title.lower() for e in out}
    if window.page("help") is not None:
        try:
            from .pages.help import topics
            for key, title, md in topics():
                words = " ".join(re.findall(r"\*\*(.+?)\*\*", md))[:300]
                if title.lower() in taken:          # gleichnamige Seite/Aktion: Hilfe-Thema kenntlich machen
                    title = f"Hilfe: {title}"
                out.append(Entry(title, "Hilfe", lambda k=key: window.go("help", topic=k), icon("help-contents"),
                                 words, kind="Hilfe"))
        except Exception:  # noqa: BLE001
            pass
    seen, uniq = set(), []
    for e in out:                                 # gleicher Titel + gleiche Art nur einmal
        k = (e.kind, e.title.lower())
        if k in seen:
            continue
        seen.add(k)
        uniq.append(e)
    return uniq


class _Delegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(option.rect.width(), option.fontMetrics.height() * 2 + 14)

    def paint(self, p: QPainter, option, index):
        t = theme.current()
        e: Entry = index.data(Qt.ItemDataRole.UserRole)
        r = option.rect
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if option.state & QStyle.StateFlag.State_Selected:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(t.accent_soft))
            p.drawRoundedRect(QRectF(r.adjusted(4, 1, -4, -1)), 8, 8)
        if not e.icon.isNull():
            e.icon.paint(p, r.left() + 14, r.top() + (r.height() - 20) // 2, 20, 20)
        fm = option.fontMetrics
        x = r.left() + 46
        right = r.right() - 12
        f = QFont(option.font)
        if e.shortcut or e.kind:
            small = QFont(option.font)
            small.setPointSizeF(max(7.0, small.pointSizeF() * 0.86))
            p.setFont(small)
            p.setPen(QColor(t.muted))
            label = e.shortcut or e.kind
            w = p.fontMetrics().horizontalAdvance(label)
            p.drawText(right - w, r.top(), w, r.height(), Qt.AlignmentFlag.AlignVCenter, label)
            right -= w + 12
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(t.text))
        p.drawText(x, r.top() + 6, right - x, fm.height(), Qt.AlignmentFlag.AlignLeft,
                   fm.elidedText(e.title, Qt.TextElideMode.ElideRight, right - x))
        p.setFont(option.font)
        p.setPen(QColor(t.muted))
        p.drawText(x, r.top() + 8 + fm.height(), right - x, fm.height(), Qt.AlignmentFlag.AlignLeft,
                   fm.elidedText(e.subtitle, Qt.TextElideMode.ElideRight, right - x))
        p.restore()


class CommandPalette(QFrame):
    """schwebt oben mittig über dem Hauptfenster; Esc schließt, Enter führt aus."""
    MAX = 50

    def __init__(self, window: QWidget):
        super().__init__(window)
        self.window = window
        self.setObjectName("Palette")
        self.entries = collect(window)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)
        self.input = QLineEdit()
        self.input.setObjectName("PaletteInput")
        self.input.setPlaceholderText("Seite, Aktion oder Hilfe suchen …")
        self.input.textChanged.connect(self.update_results)
        self.input.installEventFilter(self)
        lay.addWidget(self.input)
        self.list = QListWidget()
        self.list.setObjectName("PaletteList")
        self.list.setItemDelegate(_Delegate(self.list))
        # der Delegat kürzt lange Beschreibungen selbst („…“) – eine waagerechte Leiste war nur Störung
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.itemActivated.connect(lambda it: self.execute(it))
        self.list.itemClicked.connect(lambda it: self.execute(it))
        lay.addWidget(self.list, 1)
        self.update_results("")

    def popup(self):
        w = self.window
        width = min(640, max(420, w.width() - 80))
        self.setGeometry((w.width() - width) // 2, 64, width, min(460, w.height() - 120))
        self.show()
        self.raise_()
        self.input.setFocus()

    def results(self) -> list[Entry]:
        return [self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())]

    def update_results(self, q: str):
        scored = [(e.score(q), i, e) for i, e in enumerate(self.entries)]
        scored = [x for x in scored if x[0] > 0]
        if q.strip():
            scored.sort(key=lambda x: (-x[0], x[1]))
        self.list.clear()
        for _s, _i, e in scored[:self.MAX]:
            it = QListWidgetItem(e.title)
            it.setData(Qt.ItemDataRole.UserRole, e)
            self.list.addItem(it)
        if self.list.count():
            self.list.setCurrentRow(0)

    def execute(self, it=None):
        it = it or self.list.currentItem()
        if it is None:
            return False
        e: Entry = it.data(Qt.ItemDataRole.UserRole)
        self.close()
        self.deleteLater()
        e.callback()
        return True

    def eventFilter(self, obj, ev):
        if obj is self.input and ev.type() == QEvent.Type.KeyPress:
            k = ev.key()
            if k in (Qt.Key.Key_Down, Qt.Key.Key_Up, Qt.Key.Key_PageDown, Qt.Key.Key_PageUp):
                row = self.list.currentRow() + {Qt.Key.Key_Down: 1, Qt.Key.Key_Up: -1,
                                                Qt.Key.Key_PageDown: 8, Qt.Key.Key_PageUp: -8}[k]
                self.list.setCurrentRow(max(0, min(self.list.count() - 1, row)))
                return True
            if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.execute()
                return True
            if k == Qt.Key.Key_Escape:
                self.close()
                self.deleteLater()
                return True
        if obj is self.input and ev.type() == QEvent.Type.FocusOut:
            pass
        return False


__all__ = ["CommandPalette", "fuzzy_score", "collect", "QAction"]
