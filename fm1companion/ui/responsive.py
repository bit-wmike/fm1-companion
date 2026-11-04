# SPDX-License-Identifier: GPL-3.0-only
"""Responsive Bausteine: Seiten passen sich an die verfügbare Breite an, statt das Fenster breit zu zwingen.

Die App soll auch auf der linken Hälfte eines 1080p-Bildschirms gut nutzbar sein (Inhalt ≈ 940 px breit). Statt jede Seite
einzeln zu quetschen, gibt es hier wenige zentrale Helfer:

* :class:`WidthWatcher` – meldet ``narrow`` (True/False), wenn die *verfügbare* Breite eines Widgets eine Grenze
  unter- bzw. wieder überschreitet. Mit Hysterese und Flacker-Schutz; „verfügbar“ heißt: ein Widget, das größer
  ist als der Rollbereich, in dem es liegt, zählt nur so breit, wie man es sieht.
* :func:`adaptive_splitter` – zweispaltiges Layout (Liste | Details) wechselt unterhalb der Grenze auf
  übereinander und oberhalb wieder nebeneinander. Ohne Angabe wird die Grenze aus den Mindestbreiten der
  Spalten berechnet (passt sich also an Schrift und Zoom an).
* :func:`reflow` – eine bestehende Knopfreihe (QHBoxLayout) wird an Ort und Stelle zur umbrechenden
  :class:`~.common.FlowLayout`; Unter-Layouts (z. B. „Ziel:“ + Auswahl) bleiben dabei zusammen.
* :func:`current_only` – ein QStackedWidget/QTabWidget fordert nur so viel Mindestgröße wie die *sichtbare*
  Seite (Qt nimmt sonst das Maximum aller Seiten – ein breiter Reiter zwang die ganze Seite breit).
* :func:`form_wrap` – Formular: bei Schmalheit Beschriftung über dem Feld.
"""
from __future__ import annotations

import time
from typing import Callable

from PyQt6.QtCore import QEvent, QObject, QTimer, Qt, pyqtSignal
from PyQt6.QtWidgets import (QAbstractScrollArea, QBoxLayout, QFormLayout, QGridLayout, QLayout, QScrollArea,
                             QSizePolicy, QSplitter, QStackedWidget, QTabWidget, QWidget)

from .common import FlowLayout

_KEEP: set = set()       # Helfer fest halten (Python räumt Hüllen von Qt-eigenen Widgets sonst weg)


def _keep(obj: QObject):
    """Hülle fest halten. Bewusst ohne Aufräumen im destroyed-Signal: eine Hülle mitten im Abbau ihres
    Qt-Objekts freizugeben ist riskant; die Helfer leben ohnehin so lange wie das Fenster."""
    _KEEP.add(obj)


HYSTERESIS = 32          # px: erst so viel breiter als die Grenze geht es zurück auf „breit“
FLICKER_LIMIT = 6        # mehr Wechsel in FLICKER_WINDOW Sekunden: Zustand einfrieren (nie eine Flacker-Schleife)
FLICKER_WINDOW = 1.0


def available_width(widget: QWidget) -> int:
    """sichtbare Breite: Breite des Widgets minus das, was in umgebenden Rollbereichen über den Rand ragt."""
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
    """ruft ``callback(narrow)`` auf, sobald die verfügbare Breite von ``widget`` unter ``breakpoint`` fällt
    (narrow=True) bzw. wieder ``breakpoint + hysteresis`` erreicht (narrow=False). ``breakpoint`` darf eine
    Funktion sein (wird bei jeder Prüfung neu gefragt). Der erste Aufruf kommt, sobald das Widget eine Größe hat.
    """
    changed = pyqtSignal(bool)

    def __init__(self, widget: QWidget, breakpoint: int | Callable[[], int], callback=None,
                 hysteresis: int = HYSTERESIS):
        super().__init__(widget)
        self.widget = widget
        self._bp = breakpoint
        self.hysteresis = hysteresis
        self.narrow: bool | None = None
        self.switches: list[float] = []          # Zeitpunkte der Wechsel (Flacker-Schutz, Tests)
        self.frozen = False
        self._bp_narrow = 0                      # Grenze beim letzten Wechsel auf schmal
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
            return False                         # verborgene Seite: nichts zu tun (spart Arbeit im GUI-Thread)
        if t in (QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.ParentChange, QEvent.Type.LayoutRequest):
            self.schedule()
        return False

    def schedule(self):
        """Prüfung gebündelt nach dem laufenden Layout-Durchgang (nie mitten in einem resizeEvent umbauen)."""
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
        except RuntimeError:                 # Widget schon gelöscht
            return self.narrow
        self._watch_viewports()
        avail = available_width(w)
        bp = self.breakpoint
        if self.narrow is None:
            want = avail < bp
        elif self.narrow:
            # zurück auf breit erst ab der Grenze, die beim Wechsel auf schmal galt: übereinander gerechnet
            # ist sie oft kleiner (andere Mindestbreiten) – sonst pendelt das Layout dazwischen hin und her
            want = avail < max(bp, self._bp_narrow) + self.hysteresis
        else:
            want = avail < bp
        if want != self.narrow:
            now = time.monotonic()
            self.switches = [s for s in self.switches if now - s < FLICKER_WINDOW]
            if self.narrow is not None and len(self.switches) >= FLICKER_LIMIT:
                self.frozen = True               # lieber einen Zustand behalten als flackern
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
    """Splitter nebeneinander (breit) bzw. übereinander (schmal). Ohne ``breakpoint``: Summe der
    Mindestbreiten der Spalten + ``slack``. Mit ``breakpoint``: das Größere von beidem. Die zuletzt
    gewählten Größen je Ausrichtung bleiben erhalten. ``on_change(narrow)`` wird zusätzlich aufgerufen.
    ``ratios``: Aufteilung beim ersten Wechsel auf übereinander (z. B. (2, 3)), sonst gleichmäßig."""
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
            else:                                # erstes Mal: nach Stretch-Faktoren bzw. gleichmäßig verteilen
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
    """Kurzform: ``on_narrow(self, 820, lambda n: ...)``."""
    return WidthWatcher(widget, breakpoint, callback, hysteresis)


# --------------------------------------------------------------------------- Knopfreihen umbrechen
def _holder(sub: QLayout) -> QWidget:
    sub.setParent(None)
    h = QWidget()
    sub.setContentsMargins(0, 0, 0, 0)
    h.setLayout(sub)
    return h


def reflow(box: QBoxLayout, hspacing: int | None = None, vspacing: int | None = None) -> QWidget:
    """ersetzt eine waagrechte Reihe (QHBoxLayout) durch ein umbrechendes FlowLayout an derselben Stelle.
    Widgets wandern mit, Unter-Layouts bleiben als Gruppe zusammen, Dehnungen/Abstände fallen weg.
    Gibt das neue Container-Widget zurück (``.flow`` ist das Layout)."""
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
        # Reihe war das Layout eines Widgets: altes Layout ablösen, Container einsetzen
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


# --------------------------------------------------------------------------- nur die sichtbare Seite zählt
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
    """Mindestgröße eines QStackedWidget/QTabWidget = die der sichtbaren Seite (statt Maximum aller Seiten).
    Verborgene Seiten bekommen die Größenregel „Ignored“; die eigene Regel kommt beim Anzeigen zurück."""
    sw = stack
    if isinstance(stack, QTabWidget):
        sw = stack.findChild(QStackedWidget, "qt_tabwidget_stackedwidget")
    if not isinstance(sw, QStackedWidget) or sw.property("_resp_current_only"):
        return
    sw.setProperty("_resp_current_only", True)
    _CurrentOnly(sw)
    _sync_current(sw)


class _CurrentOnly(QObject):
    """hängt am Stapel (lebt so lange wie er). Eine Lambda-Verbindung ginge verloren, sobald Python die Hülle
    eines von Qt angelegten Stapels (Reiter-Widget intern) wegräumt."""

    def __init__(self, sw: QStackedWidget):
        super().__init__(sw)
        self.sw = sw
        sw.currentChanged.connect(self._changed)
        _keep(self)

    def _changed(self, _i: int):
        _sync_current(self.sw)


def current_only_all(root: QWidget) -> int:
    """current_only für alle Stapel/Reiter unterhalb von root (z. B. das ganze Hauptfenster)."""
    n = 0
    for sw in root.findChildren(QStackedWidget):
        if not sw.property("_resp_current_only"):
            current_only(sw)
            n += 1
    return n


# --------------------------------------------------------------------------- Formulare
def form_wrap(form: QFormLayout, host: QWidget, breakpoint: int) -> WidthWatcher:
    """Formular: bei schmalem ``host`` die Beschriftung über das Feld setzen, sonst daneben."""
    def apply(narrow: bool):
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows if narrow
                              else QFormLayout.RowWrapPolicy.DontWrapRows)
        host.updateGeometry()
    return WidthWatcher(host, breakpoint, apply)


# --------------------------------------------------------------------------- Seitenbereich mit Rollleisten
class PageHost(QWidget):
    """Träger für den Seitenstapel im Rollbereich des Hauptfensters. Füllt immer den sichtbaren Bereich und
    rollt erst, wenn die sichtbare Seite ihre *Mindest*größe nicht bekommt. (Ein QStackedWidget direkt im
    QScrollArea bekäme wegen umbrechender Zeilen seine *bevorzugte* Höhe – dann rollte fast jede Seite.)"""

    def __init__(self, inner: QWidget, parent=None):
        super().__init__(parent)
        self.inner = inner
        self._last = None
        self._pending = False
        self.syncs = 0                           # wie oft der Rollbereich neu rechnen musste (Test)
        inner.setParent(self)
        # Filter als Kind des Stapels (nicht der Träger selbst): beim Abbau des Fensters ruft Qt sonst einen
        # Filter auf, dessen Objekt gerade zerstört wird
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
            self._pending = True                 # gebündelt: eine Prüfung je Durchgang, nicht je Textänderung
            QTimer.singleShot(0, self._sync)

    def _sync(self):
        """nur wenn sich die Mindestgröße der Seite wirklich geändert hat, den Rollbereich neu rechnen lassen
        (der MIDI-Monitor ändert viele Male pro Sekunde Texte – das darf keine Fenster-Neuberechnung auslösen)."""
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
    """Mindesthöhe folgt umbrechenden Zeilen (FlowLayout, Zeilenumbruch) – für Widgets in einem senkrechten
    QSplitter oder Rollbereich, die „Höhe für Breite“ sonst nicht kennen (Knöpfe würden abgeschnitten)."""
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
    """meldet LayoutRequest-Ereignisse eines Widgets; lebt als dessen Kind."""

    def __init__(self, widget: QWidget, callback):
        super().__init__(widget)
        self._cb = callback
        widget.installEventFilter(self)

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Type.LayoutRequest:
            self._cb()
        return False
