# SPDX-License-Identifier: GPL-3.0-only
"""Zusammengelegte Seiten: mehrere bisher eigenständige Seiten als Reiter EINER Seite.

Die Navigation bleibt kurz (Ziel: ~13 Seiten), die alten Seiten-Schlüssel funktionieren weiter –
``window.go("backup_browser", path=...)`` landet auf „Backups › Durchsuchen & Vergleichen“.

Jede Unterseite behält ihren Code; ihr eigener Seitenkopf wird ausgeblendet und sein Untertitel wandert in
den gemeinsamen Kopf, sobald der Reiter aktiv ist. activate/deactivate/shutdown/open_with und das Signal
``navigate`` werden an die Unterseiten durchgereicht; unbekannte Attribute (``page.tree``, ``page.refresh()``)
landen bei der Hauptunterseite (erster Reiter), damit bestehender Code und Tests weiter funktionieren.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QLabel, QTabWidget, QVBoxLayout, QWidget

from .common import heading


class TabbedPage(QWidget):
    navigate = pyqtSignal(str)

    def __init__(self, title: str, subtitle: str, tabs: list[tuple[str, str, QWidget]], parent=None):
        """tabs: [(Reiter-Schlüssel, Reiter-Beschriftung, Unterseite), ...] – der erste Reiter ist der Haupt-Reiter."""
        super().__init__(parent)
        self.setObjectName("TabbedPage")
        self._keys: list[str] = []
        self._subs: list[QWidget] = []
        self._subtitles: dict[int, str] = {}
        self._active = False
        self._current = -1
        self._default_subtitle = subtitle
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.head = heading(title, subtitle or " ")
        self.sub_label = self.head.findChildren(QLabel)[-1]
        lay.addWidget(self.head)
        # heißt bewusst page_tabs: viele Unterseiten haben selbst ein Attribut „tabs“, das über __getattr__
        # weiter erreichbar bleibt (z. B. page("compose").tabs = die inneren Reiter von Komponieren)
        self.page_tabs = QTabWidget()
        self.page_tabs.setObjectName("PageTabs")
        self.page_tabs.setDocumentMode(True)
        lay.addWidget(self.page_tabs, 1)
        for key, label, page in tabs:
            self.add_tab(key, label, page)
        self.page_tabs.currentChanged.connect(self._tab_changed)
        self._current = self.page_tabs.currentIndex()
        self._show_subtitle(self._current)

    # ------------------------------------------------------------------ Aufbau
    def add_tab(self, key: str, label: str, page: QWidget):
        head = page.findChild(QWidget, "PageHeading")
        if head is not None:
            labels = head.findChildren(QLabel)
            self._subtitles[len(self._keys)] = labels[-1].text() if len(labels) > 1 else ""
            head.hide()
        lay = page.layout()
        if lay is not None:
            m = lay.contentsMargins()
            lay.setContentsMargins(0, max(m.top(), 6), 0, 0)
        self._keys.append(key)
        self._subs.append(page)
        self.page_tabs.addTab(page, label.replace("&", "&&"))      # „&“ im Titel, kein Tastenkürzel
        sig = getattr(page, "navigate", None)
        if sig is not None and hasattr(sig, "connect"):
            try:
                sig.connect(self.navigate)
            except (TypeError, AttributeError):
                pass

    def __getattr__(self, name: str):
        """unbekannte Attribute kommen von der Hauptunterseite (erster Reiter)."""
        if name.startswith("_") or name in ("page_tabs", "head", "sub_label"):
            raise AttributeError(name)
        subs = self.__dict__.get("_subs") or []
        if subs:
            return getattr(subs[0], name)
        raise AttributeError(name)

    # ------------------------------------------------------------------ Reiter
    @property
    def keys(self) -> list[str]:
        return list(self._keys)

    def sub(self, key: str) -> QWidget | None:
        return self._subs[self._keys.index(key)] if key in self._keys else None

    def tab_key(self) -> str:
        i = self.page_tabs.currentIndex()
        return self._keys[i] if 0 <= i < len(self._keys) else ""

    def current(self) -> QWidget | None:
        i = self.page_tabs.currentIndex()
        return self._subs[i] if 0 <= i < len(self._subs) else None

    def set_tab(self, key_or_index) -> bool:
        i = key_or_index if isinstance(key_or_index, int) else (self._keys.index(key_or_index)
                                                                  if key_or_index in self._keys else -1)
        if 0 <= i < self.page_tabs.count():
            self.page_tabs.setCurrentIndex(i)
            return True
        return False

    def _show_subtitle(self, i: int):
        text = self._subtitles.get(i) or self._default_subtitle or " "
        self.sub_label.setText(text)

    def _tab_changed(self, i: int):
        prev = self._current
        self._current = i
        self._show_subtitle(i)
        if not self._active:
            return
        if 0 <= prev < len(self._subs) and prev != i and hasattr(self._subs[prev], "deactivate"):
            self._subs[prev].deactivate()
        if 0 <= i < len(self._subs) and hasattr(self._subs[i], "activate"):
            self._subs[i].activate()

    # ------------------------------------------------------------------ Seiten-Haken
    def activate(self):
        self._active = True
        cur = self.current()
        if cur is not None and hasattr(cur, "activate"):
            cur.activate()

    def deactivate(self):
        self._active = False
        cur = self.current()
        if cur is not None and hasattr(cur, "deactivate"):
            cur.deactivate()

    def open_with(self, tab: str | None = None, **kwargs):
        if tab:
            self.set_tab(tab)
        cur = self.current()
        if kwargs and cur is not None and hasattr(cur, "open_with"):
            cur.open_with(**kwargs)

    def shutdown(self):
        for p in self._subs:
            if hasattr(p, "shutdown"):
                try:
                    p.shutdown()
                except Exception:              # noqa: BLE001 – Beenden darf nicht hängen bleiben
                    pass
            elif hasattr(p, "deactivate"):
                try:
                    p.deactivate()
                except Exception:              # noqa: BLE001
                    pass

    def keyPressEvent(self, e):                 # Strg+Tab wechselt den Reiter (wie in Browsern)
        if e.key() == Qt.Key.Key_Tab and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            n = self.page_tabs.count()
            if n:
                self.page_tabs.setCurrentIndex((self.page_tabs.currentIndex() + 1) % n)
            return
        super().keyPressEvent(e)


# ====================================================================== Reiter in eine bestehende Seite einhängen
class TabAdopter:
    """Hängt weitere Seiten als Reiter in das vorhandene QTabWidget einer Seite (``main.tabs``) – z. B. den
    Klangerzeuger als sechsten Reiter der MIDI-Seite. Die Hauptseite behält ihren Kopf; die Unterseite zeigt
    ihren Titel als Abschnitt. activate/deactivate/shutdown/open_with/set_tab der Hauptseite werden so ergänzt,
    dass die Unterseiten dieselben Haken bekommen wie eigenständige Seiten."""

    def __init__(self, main: QWidget, parts: list[tuple[str, str, QWidget]]):
        self.main = main
        self.tabs: QTabWidget = main.tabs
        self.keys: list[str] = []
        self.subs: list[QWidget] = []
        self.active = False
        self._cur_sub: QWidget | None = None
        self._orig = {n: getattr(main, n, None) for n in ("activate", "deactivate", "shutdown", "open_with")}
        for key, label, page in parts:
            head = page.findChild(QWidget, "PageHeading")
            if head is not None:
                labels = head.findChildren(QLabel)
                if labels:
                    labels[0].setProperty("role", "cardTitle")
            lay = page.layout()
            if lay is not None:
                m = lay.contentsMargins()
                lay.setContentsMargins(max(m.left(), 8), max(m.top(), 8), max(m.right(), 8), max(m.bottom(), 4))
            self.keys.append(key)
            self.subs.append(page)
            self.tabs.addTab(page, label.replace("&", "&&"))
            sig = getattr(page, "navigate", None)
            msig = getattr(main, "navigate", None)
            if sig is not None and msig is not None and hasattr(sig, "connect"):
                try:
                    sig.connect(msig)
                except (TypeError, AttributeError):
                    pass
        self.tabs.currentChanged.connect(self._tab_changed)
        main.activate = self.activate
        main.deactivate = self.deactivate
        main.shutdown = self.shutdown
        main.open_with = self.open_with
        main.set_tab = self.set_tab
        main.adopted = self

    def sub(self, key: str) -> QWidget | None:
        return self.subs[self.keys.index(key)] if key in self.keys else None

    def set_tab(self, key_or_index) -> bool:
        if isinstance(key_or_index, int):
            if 0 <= key_or_index < self.tabs.count():
                self.tabs.setCurrentIndex(key_or_index)
                return True
            return False
        page = self.sub(key_or_index)
        if page is not None:
            self.tabs.setCurrentWidget(page)
            return True
        orig = self._orig.get("open_with")
        if orig is not None:                       # Reiter der Hauptseite (z. B. "monitor")
            orig(tab=key_or_index)
            return True
        return False

    def _tab_changed(self, i: int):
        w = self.tabs.widget(i)
        new = w if w in self.subs else None
        if not self.active:
            self._cur_sub = new
            return
        if self._cur_sub is not None and self._cur_sub is not new and hasattr(self._cur_sub, "deactivate"):
            self._cur_sub.deactivate()
        self._cur_sub = new
        if new is not None and hasattr(new, "activate"):
            new.activate()

    def activate(self):
        self.active = True
        if self._orig["activate"]:
            self._orig["activate"]()
        w = self.tabs.currentWidget()
        self._cur_sub = w if w in self.subs else None
        if self._cur_sub is not None and hasattr(self._cur_sub, "activate"):
            self._cur_sub.activate()

    def deactivate(self):
        self.active = False
        if self._orig["deactivate"]:
            self._orig["deactivate"]()
        if self._cur_sub is not None and hasattr(self._cur_sub, "deactivate"):
            self._cur_sub.deactivate()

    def shutdown(self):
        if self._orig["shutdown"]:
            try:
                self._orig["shutdown"]()
            except Exception:                  # noqa: BLE001
                pass
        for p in self.subs:
            fn = getattr(p, "shutdown", None) or getattr(p, "deactivate", None)
            if fn:
                try:
                    fn()
                except Exception:              # noqa: BLE001
                    pass

    def open_with(self, tab: str | None = None, **kwargs):
        if tab and tab in self.keys:
            self.set_tab(tab)
            page = self.sub(tab)
            if kwargs and hasattr(page, "open_with"):
                page.open_with(**kwargs)
            return
        w = self.tabs.currentWidget()
        if not tab and kwargs and w in self.subs and hasattr(w, "open_with"):
            w.open_with(**kwargs)
            return
        if self._orig["open_with"]:
            self._orig["open_with"](**({"tab": tab} if tab else {}), **kwargs)
