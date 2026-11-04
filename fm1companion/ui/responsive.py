# SPDX-License-Identifier: GPL-3.0-only
"""Responsive building blocks: pages adapt to the available width instead of forcing the window wide.

The app should also be usable on the left half of a 1080p screen (content ≈ 940 px wide). Instead of squeezing every page
individually, there are a few central helpers here:

* :class:`WidthWatcher` – reports ``narrow`` (True/False) when the *available* width of a widget falls below or
  rises above a limit again. With hysteresis and flicker protection; “available” means: a widget that is larger
  than the scroll area it sits in only counts as wide as it is visible.
* :func:`adaptive_splitter` – a two-column layout (list | details) switches to stacked below the limit
  and back to side by side above it. Without a value, the limit is computed from the columns' minimum
  widths (so it adapts to font and zoom).
* :func:`reflow` – an existing button row (QHBoxLayout) becomes a wrapping
  :class:`~.common.FlowLayout` in place; sub-layouts (e.g. “Target:” + selection) stay together.
* :func:`current_only` – a QStackedWidget/QTabWidget only requests as much minimum size as the *visible*
  page (otherwise Qt takes the maximum of all pages – one wide tab forced the whole page wide).
* :func:`form_wrap` – form: when narrow, label above the field.
"""
from __future__ import annotations

import time
from typing import Callable

from PyQt6.QtCore import QEvent, QObject, QTimer, Qt, pyqtSignal
from PyQt6.QtWidgets import (QAbstractScrollArea, QBoxLayout, QFormLayout, QGridLayout, QLayout, QScrollArea,
                             QSizePolicy, QSplitter, QStackedWidget, QTabWidget, QWidget)

from .common import FlowLayout

_KEEP: set = set()       # keep helpers alive (otherwise Python cleans up wrappers of Qt-owned widgets)


def _keep(obj: QObject):
    """Keep the wrapper alive. Deliberately without cleanup in the destroyed signal: releasing a wrapper in the middle
    of tearing down its Qt object is risky; the helpers live as long as the window anyway."""
    _KEEP.add(obj)


HYSTERESIS = 32          # px: only this much wider than the limit does it switch back to “wide”
FLICKER_LIMIT = 6        # more switches within FLICKER_WINDOW seconds: freeze the state (never a flicker loop)
FLICKER_WINDOW = 1.0


def available_width(widget: QWidget) -> int:
    """visible width: the widget's width minus what sticks out over the edge in surrounding scroll areas."""
    w = widget.width()
    p = widget.parentWidget()
    while p is not None:
        if isinstance(p, QScrollArea) and p.widget() is not None:
            over = p.widget().width() - p.viewport().width()
            if over > 0:
                w -= over
        p = p.parentWidget()
    return max(0, w)


def _scroll_viewports(widget: QWidget) -> list[QWidget]:
    out = []
    p = widget.parentWidget()
    while p is not None:
        if isinstance(p, QAbstractScrollArea):
            out.append(p.viewport())
        p = p.parentWidget()
    return out


class WidthWatcher(QObject):
    """calls ``callback(narrow)`` as soon as the available width of ``widget`` falls below ``breakpoint``
    (narrow=True) or reaches ``breakpoint + hysteresis`` again (narrow=False). ``breakpoint`` may be a
    function (asked anew on every check). The first call comes as soon as the widget has a size.
    """
    changed = pyqtSignal(bool)

    def __init__(self, widget: QWidget, breakpoint: int | Callable[[], int], callback=None,
                 hysteresis: int = HYSTERESIS):
        super().__init__(widget)
        self.widget = widget
        self._bp = breakpoint
        self.hysteresis = hysteresis
        self.narrow: bool | None = None
        self.switches: list[float] = []          # times of the switches (flicker protection, tests)
        self.frozen = False
        self._bp_narrow = 0                      # limit at the last switch to narrow
        self._pending = False
        self._watched: set[int] = set()
        if callback is not None:
            self.changed.connect(callback)
        widget.installEventFilter(self)
        _keep(self)

    @property
    def breakpoint(self) -> int:
        return int(self._bp() if callable(self._bp) else self._bp)

    def eventFilter(self, obj, ev):
        t = ev.type()
        if t == QEvent.Type.LayoutRequest and not self.widget.isVisible():
            return False                         # hidden page: nothing to do (saves work in the GUI thread)
        if t in (QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.ParentChange, QEvent.Type.LayoutRequest):
            self.schedule()
        return False

    def schedule(self):
        """Check batched after the current layout pass (never rebuild in the middle of a resizeEvent)."""
        if not self._pending:
            self._pending = True
            QTimer.singleShot(0, self.check)

    def _watch_viewports(self):
        for vp in _scroll_viewports(self.widget):
            if id(vp) not in self._watched:
                self._watched.add(id(vp))
                vp.installEventFilter(self)

    def check(self) -> bool | None:
        self._pending = False
        try:
            w = self.widget
            if w is None or not w.isVisible() or w.width() <= 1:
                return self.narrow
        except RuntimeError:                 # widget already deleted
            return self.narrow
        self._watch_viewports()
        avail = available_width(w)
        bp = self.breakpoint
        if self.narrow is None:
            want = avail < bp
        elif self.narrow:
            # back to wide only from the limit that applied when switching to narrow: computed stacked
            # it is often smaller (different minimum widths) – otherwise the layout oscillates between them
            want = avail < max(bp, self._bp_narrow) + self.hysteresis
        else:
            want = avail < bp
        if want != self.narrow:
            now = time.monotonic()
            self.switches = [s for s in self.switches if now - s < FLICKER_WINDOW]
            if self.narrow is not None and len(self.switches) >= FLICKER_LIMIT:
                self.frozen = True               # rather keep a state than flicker
                return self.narrow
            self.switches.append(now)
            self._bp_narrow = bp if want else 0
            self.narrow = want
            self.changed.emit(want)
        return self.narrow


def _min_w(w: QWidget) -> int:
    return max(w.minimumSizeHint().width(), w.minimumWidth())


def adaptive_splitter(splitter: QSplitter, breakpoint: int | None = None, *, hysteresis: int = HYSTERESIS,
                      slack: int = 48, on_change=None, ratios: tuple | None = None) -> WidthWatcher:
    """Splitter side by side (wide) or stacked (narrow). Without ``breakpoint``: sum of the
    columns' minimum widths + ``slack``. With ``breakpoint``: the larger of both. The last
    chosen sizes per orientation are kept. ``on_change(narrow)`` is called additionally.
    ``ratios``: split on the first switch to stacked (e.g. (2, 3)), otherwise even."""
    saved: dict[Qt.Orientation, list[int]] = {}
    splitter.setChildrenCollapsible(False)

    def need() -> int:
        kids = [splitter.widget(i) for i in range(splitter.count())]
        kids = [k for k in kids if k is not None and not k.isHidden()]
        auto = sum(_min_w(k) for k in kids) + splitter.handleWidth() * max(0, len(kids) - 1) + slack
        return max(auto, breakpoint or 0)

    def apply(narrow: bool):
        want = Qt.Orientation.Vertical if narrow else Qt.Orientation.Horizontal
        if splitter.orientation() != want:
            saved[splitter.orientation()] = splitter.sizes()
            splitter.setOrientation(want)
            if want in saved:
                splitter.setSizes(saved[want])
            else:                                # first time: distribute by stretch factors or evenly
                n = splitter.count()
                total = splitter.height() if narrow else splitter.width()
                r = list(ratios) if narrow and ratios and len(ratios) == n else [1] * n
                if n and total > 0:
                    splitter.setSizes([total * x // sum(r) for x in r])
        if on_change is not None:
            on_change(narrow)

    watcher = WidthWatcher(splitter, need, apply, hysteresis)
    splitter.width_watcher = watcher
    return watcher


def on_narrow(widget: QWidget, breakpoint: int | Callable[[], int], callback, hysteresis: int = HYSTERESIS) \
        -> WidthWatcher:
    """Short form: ``on_narrow(self, 820, lambda n: ...)``."""
    return WidthWatcher(widget, breakpoint, callback, hysteresis)


# --------------------------------------------------------------------------- Wrap button rows
def _holder(sub: QLayout) -> QWidget:
    sub.setParent(None)
    h = QWidget()
    sub.setContentsMargins(0, 0, 0, 0)
    h.setLayout(sub)
    return h


def reflow(box: QBoxLayout, hspacing: int | None = None, vspacing: int | None = None) -> QWidget:
    """replaces a horizontal row (QHBoxLayout) with a wrapping FlowLayout at the same place.
    Widgets move along, sub-layouts stay together as a group, stretches/spacings are dropped.
    Returns the new container widget (``.flow`` is the layout)."""
    hs = box.spacing() if hspacing is None or hspacing < 0 else hspacing
    hs = hs if hs >= 0 else 6
    cont = QWidget()
    flow = FlowLayout(cont, hs, vspacing if vspacing is not None else max(4, hs))
    items = []
    while box.count():
        it = box.takeAt(0)
        if it is None:
            break
        if it.widget() is not None:
            items.append(it.widget())
        elif it.layout() is not None:
            items.append(_holder(it.layout()))
    parent = box.parent()
    if isinstance(parent, QLayout):
        idx = next((i for i in range(parent.count()) if parent.itemAt(i).layout() is box), -1)
        if isinstance(parent, QBoxLayout):
            stretch = parent.stretch(idx) if idx >= 0 else 0
            parent.takeAt(idx)
            parent.insertWidget(idx if idx >= 0 else parent.count(), cont, stretch)
        elif isinstance(parent, QGridLayout) and idx >= 0:
            r, c, rs, cs = parent.getItemPosition(idx)
            parent.takeAt(idx)
            parent.addWidget(cont, r, c, rs, cs)
        elif isinstance(parent, QFormLayout) and idx >= 0:
            row, role = parent.getItemPosition(idx)
            parent.takeAt(idx)
            parent.setWidget(row, role, cont)
        else:
            raise ValueError("reflow: parent layout not supported")
    elif isinstance(parent, QWidget):
        # the row was a widget's layout: detach the old layout, insert the container
        QWidget().setLayout(box)
        from PyQt6.QtWidgets import QVBoxLayout
        v = QVBoxLayout(parent)
        v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(cont)
    box.setParent(None)
    for w in items:
        flow.addWidget(w)
    sp = cont.sizePolicy()
    sp.setHeightForWidth(True)
    sp.setHorizontalPolicy(QSizePolicy.Policy.Preferred)
    cont.setSizePolicy(sp)
    cont.flow = flow
    return cont


# --------------------------------------------------------------------------- only the visible page counts
def _sync_current(sw: QStackedWidget):
    cur = sw.currentWidget()
    for i in range(sw.count()):
        w = sw.widget(i)
        if w is None:
            continue
        orig = w.property("_resp_policy")
        if orig is None:
            orig = w.sizePolicy()
            w.setProperty("_resp_policy", orig)
        if w is cur:
            w.setSizePolicy(orig)
        else:
            w.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
    sw.updateGeometry()
    p = sw.parentWidget()
    if isinstance(p, QTabWidget):
        p.updateGeometry()


def current_only(stack) -> None:
    """Minimum size of a QStackedWidget/QTabWidget = that of the visible page (instead of the maximum of all pages).
    Hidden pages get the size policy “Ignored”; their own policy comes back when shown."""
    sw = stack
    if isinstance(stack, QTabWidget):
        sw = stack.findChild(QStackedWidget, "qt_tabwidget_stackedwidget")
    if not isinstance(sw, QStackedWidget) or sw.property("_resp_current_only"):
        return
    sw.setProperty("_resp_current_only", True)
    _CurrentOnly(sw)
    _sync_current(sw)


class _CurrentOnly(QObject):
    """attached to the stack (lives as long as it does). A lambda connection would be lost as soon as Python
    cleans up the wrapper of a stack created by Qt (tab widget internal)."""

    def __init__(self, sw: QStackedWidget):
        super().__init__(sw)
        self.sw = sw
        sw.currentChanged.connect(self._changed)
        _keep(self)

    def _changed(self, _i: int):
        _sync_current(self.sw)


def current_only_all(root: QWidget) -> int:
    """current_only for all stacks/tabs below root (e.g. the whole main window)."""
    n = 0
    for sw in root.findChildren(QStackedWidget):
        if not sw.property("_resp_current_only"):
            current_only(sw)
            n += 1
    return n


# --------------------------------------------------------------------------- Forms
def form_wrap(form: QFormLayout, host: QWidget, breakpoint: int) -> WidthWatcher:
    """Form: with a narrow ``host`` put the label above the field, otherwise next to it."""
    def apply(narrow: bool):
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows if narrow
                              else QFormLayout.RowWrapPolicy.DontWrapRows)
        host.updateGeometry()
    return WidthWatcher(host, breakpoint, apply)


# --------------------------------------------------------------------------- Page area with scroll bars
class PageHost(QWidget):
    """Carrier for the page stack in the main window's scroll area. Always fills the visible area and
    only scrolls when the visible page doesn't get its *minimum* size. (A QStackedWidget directly in the
    QScrollArea would get its *preferred* height because of wrapping rows – then almost every page would scroll.)"""

    def __init__(self, inner: QWidget, parent=None):
        super().__init__(parent)
        self.inner = inner
        self._last = None
        self._pending = False
        self.syncs = 0                           # how often the scroll area had to recompute (test)
        inner.setParent(self)
        # filter as a child of the stack (not the carrier itself): when tearing down the window Qt would otherwise call a
        # filter whose object is just being destroyed
        self._watch = _LayoutWatch(inner, self._schedule)

    def minimumSizeHint(self):
        return self.inner.minimumSizeHint().expandedTo(self.inner.minimumSize())

    def sizeHint(self):
        return self.minimumSizeHint()

    def resizeEvent(self, e):
        self.inner.setGeometry(self.rect())
        super().resizeEvent(e)

    def _schedule(self):
        if not self._pending:
            self._pending = True                 # batched: one check per pass, not per text change
            QTimer.singleShot(0, self._sync)

    def _sync(self):
        """only let the scroll area recompute if the page's minimum size has really changed
        (the MIDI monitor changes texts many times per second – that must not trigger a window recomputation)."""
        self._pending = False
        try:
            m = self.minimumSizeHint()
        except RuntimeError:
            return
        if m == self._last:
            return
        self._last = m
        self.syncs += 1
        self.updateGeometry()
        sa = self.parentWidget().parentWidget() if self.parentWidget() is not None else None
        if isinstance(sa, QScrollArea):
            from PyQt6.QtWidgets import QApplication
            QApplication.postEvent(sa, QEvent(QEvent.Type.LayoutRequest))


class _FitHeight(QObject):
    def __init__(self, widget: QWidget):
        super().__init__(widget)
        self.widget = widget
        self._last = -1
        widget.installEventFilter(self)
        _keep(self)

    def eventFilter(self, obj, ev):
        if ev.type() in (QEvent.Type.Resize, QEvent.Type.LayoutRequest, QEvent.Type.Show):
            lay = self.widget.layout()
            w = self.widget.width()
            if lay is not None and lay.hasHeightForWidth() and w > 0:
                h = lay.totalHeightForWidth(w)
                if h != self._last and h > 0:
                    self._last = h
                    self.widget.setMinimumHeight(h)
        return False


def fit_height(widget: QWidget) -> QWidget:
    """Minimum height follows wrapping rows (FlowLayout, word wrap) – for widgets in a vertical
    QSplitter or scroll area that otherwise don't know “height for width” (buttons would be cut off)."""
    widget._fit_height = _FitHeight(widget)
    return widget


class _WrapHeight(QObject):
    def __init__(self, label):
        super().__init__(label)
        self.label = label
        self._last = -1
        label.installEventFilter(self)
        _keep(self)

    def update(self):
        if not self.label.wordWrap():
            return
        w = self.label.width()
        h = self.label.heightForWidth(w) if w > 0 else -1
        if h > 0 and h != self._last:
            self._last = h
            self.label.setMinimumHeight(h)

    def eventFilter(self, obj, ev):
        if ev.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self.update()
        return False


def wrap_height(label):
    """a word-wrapped QLabel keeps the height of all its lines when the page gets narrow (the page scrolls
    instead of cutting the text – longer translations need more lines)"""
    f = label._wrap_height = _WrapHeight(label)
    set_text = label.setText

    def _set_text(text):
        set_text(text)
        f.update()
    label.setText = _set_text
    return label


class _LayoutWatch(QObject):
    """reports a widget's LayoutRequest events; lives as its child."""

    def __init__(self, widget: QWidget, callback):
        super().__init__(widget)
        self._cb = callback
        widget.installEventFilter(self)

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Type.LayoutRequest:
            self._cb()
        return False
