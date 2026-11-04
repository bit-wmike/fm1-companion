# SPDX-License-Identifier: GPL-3.0-only
"""Merged pages: several previously standalone pages as tabs of ONE page.

Navigation stays short (goal: ~13 pages), the old page keys keep working –
``window.go("backup_browser", path=...)`` lands on “Backups › Browse & compare”.

Each subpage keeps its code; its own page header is hidden and its subtitle moves into
the shared header as soon as the tab is active. activate/deactivate/shutdown/open_with and the signal
``navigate`` are passed through to the subpages; unknown attributes (``page.tree``, ``page.refresh()``)
go to the main subpage (first tab), so existing code and tests keep working.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QLabel, QTabWidget, QVBoxLayout, QWidget

from .common import heading


class TabbedPage(QWidget):
    navigate = pyqtSignal(str)

    def __init__(self, title: str, subtitle: str, tabs: list[tuple[str, str, QWidget]], parent=None):
        """tabs: [(tab key, tab label, subpage), ...] – the first tab is the main tab."""
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
        # deliberately called page_tabs: many subpages have their own attribute “tabs”, which stays reachable
        # via __getattr__ (e.g. page("compose").tabs = the inner tabs of Compose)
        self.page_tabs = QTabWidget()
        self.page_tabs.setObjectName("PageTabs")
        self.page_tabs.setDocumentMode(True)
        lay.addWidget(self.page_tabs, 1)
        for key, label, page in tabs:
            self.add_tab(key, label, page)
        self.page_tabs.currentChanged.connect(self._tab_changed)
        self._current = self.page_tabs.currentIndex()
        self._show_subtitle(self._current)

    # ------------------------------------------------------------------ Layout
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
        self.page_tabs.addTab(page, label.replace("&", "&&"))      # “&” in the title, not a shortcut
        sig = getattr(page, "navigate", None)
        if sig is not None and hasattr(sig, "connect"):
            try:
                sig.connect(self.navigate)
            except (TypeError, AttributeError):
                pass

    def __getattr__(self, name: str):
        """unknown attributes come from the main subpage (first tab)."""
        if name.startswith("_") or name in ("page_tabs", "head", "sub_label"):
            raise AttributeError(name)
        subs = self.__dict__.get("_subs") or []
        if subs:
            return getattr(subs[0], name)
        raise AttributeError(name)

    # ------------------------------------------------------------------ Tabs
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

    # ------------------------------------------------------------------ Page hooks
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
                except Exception:              # noqa: BLE001 – exit must not get stuck
                    pass
            elif hasattr(p, "deactivate"):
                try:
                    p.deactivate()
                except Exception:              # noqa: BLE001
                    pass

    def keyPressEvent(self, e):                 # Ctrl+Tab switches the tab (like in browsers)
        if e.key() == Qt.Key.Key_Tab and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            n = self.page_tabs.count()
            if n:
                self.page_tabs.setCurrentIndex((self.page_tabs.currentIndex() + 1) % n)
            return
        super().keyPressEvent(e)


# ====================================================================== Embed tabs into an existing page
class TabAdopter:
    """Embeds further pages as tabs into a page's existing QTabWidget (``main.tabs``) – e.g. the
    sound module as the sixth tab of the MIDI page. The main page keeps its header; the subpage shows
    its title as a section. activate/deactivate/shutdown/open_with/set_tab of the main page are extended
    so that the subpages get the same hooks as standalone pages."""

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
        if orig is not None:                       # tab of the main page (e.g. "monitor")
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
