# SPDX-License-Identifier: GPL-3.0-only
"""Notices: small toasts at the top right of the window (below the toolbar), a history and optional desktop notifications.

For pages:
    from fm1companion.ui.notify import notify
    notify("Backup done", "success", action=("Open folder", lambda: ...))
or ``window.notify(text, level, action)``. Works from any thread (is handed over to the GUI
thread). level: "info" | "success" | "warning" | "error".
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import time
from dataclasses import dataclass, field

from PyQt6.QtCore import QEvent, QObject, QPoint, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                             QPushButton, QToolButton, QVBoxLayout, QWidget)
from ..i18n import _

log = logging.getLogger("fm1companion.notify")
LEVEL_ICON = {"info": "dialog-information", "success": "emblem-ok", "warning": "dialog-warning",
              "error": "dialog-error"}
LEVEL_TITLE = {"info": _("Note"), "success": _("Done"), "warning": _("Attention"), "error": _("Error")}


@dataclass
class Note:
    text: str
    level: str = "info"
    action: tuple | None = None          # (label, function)
    time: float = field(default_factory=time.time)
    read: bool = False


def _norm_action(action):
    if action is None:
        return None
    if callable(action):
        return (_("Open"), action)
    if isinstance(action, (tuple, list)) and len(action) == 2 and callable(action[1]):
        return (str(action[0]), action[1])
    return None


class _Bus(QObject):
    posted = pyqtSignal(str, str, object)


_bus: _Bus | None = None
_center: "NotificationCenter | None" = None


def notify(text: str, level: str = "info", action=None):
    """module-wide helper – safe from background threads."""
    level = level if level in LEVEL_ICON else "info"
    if _bus is not None:
        _bus.posted.emit(text, level, action)       # NotificationCenter.post writes to the log
    else:
        _log_note(text, level)


def _log_note(text: str, level: str):
    """every notice ends up in the log (and thus in the error report) – with its level."""
    getattr(log, {"error": "error", "warning": "warning"}.get(level, "info"))("[%s] %s", LEVEL_TITLE.get(level, level), text)


LANE_BELOW_H = 900       # window height below which notices get their own row
LANE_BELOW_W = 1150


class Toast(QFrame):
    closed = pyqtSignal(object)

    def __init__(self, note: Note, parent):
        super().__init__(parent)
        from .common import icon
        self.note = note
        self.setObjectName("Toast")
        self.setProperty("level", note.level)
        self.setFixedWidth(360)
        h = QHBoxLayout(self)
        h.setContentsMargins(12, 10, 8, 10)
        h.setSpacing(10)
        ic = QLabel()
        ic.setPixmap(icon(LEVEL_ICON[note.level]).pixmap(20, 20))
        h.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
        v = QVBoxLayout()
        v.setSpacing(4)
        t = QLabel(note.text)
        t.setWordWrap(True)
        t.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(t)
        self.action_button = None
        if note.action:
            b = QPushButton(note.action[0])
            b.setProperty("variant", "flat")
            b.clicked.connect(self._act)
            v.addWidget(b, 0, Qt.AlignmentFlag.AlignLeft)
            self.action_button = b
        h.addLayout(v, 1)
        x = QToolButton()
        x.setIcon(icon("window-close"))
        x.setToolTip(_("Close"))
        x.setAutoRaise(True)
        x.clicked.connect(self.dismiss)
        h.addWidget(x, 0, Qt.AlignmentFlag.AlignTop)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.dismiss)
        # fixed lifetime – even under the mouse pointer (a toast should never permanently cover buttons);
        # everything stays readable in the history (bell)
        self.lifetime = 12000 if note.level == "error" else 8000 if note.level == "warning" else 6000
        self.timer.start(self.lifetime)

    def _act(self):
        try:
            self.note.action[1]()
        finally:
            self.dismiss()

    def dismiss(self):
        self.timer.stop()
        self.hide()
        self.closed.emit(self)
        self.deleteLater()


class NotificationCenter(QObject):
    """belongs to the main window: toast stack, history, desktop notification."""
    changed = pyqtSignal()
    MAX_TOASTS = 4
    MAX_LANE = 2              # at most two in the notice row (everything stays in the history)

    def __init__(self, window: QWidget):
        super().__init__(window)
        global _bus, _center
        self.window = window
        self.history: list[Note] = []
        self.toasts: list[Toast] = []
        self.tray = None
        if _bus is None:
            _bus = _Bus()
        _bus.posted.connect(self.post)
        _center = self
        window.installEventFilter(self)

    # ------------------------------------------------------------------
    def post(self, text: str, level: str = "info", action=None, quiet: bool = False):
        """quiet=True: only show briefly, doesn't count as unread on the bell (e.g. “Active: FM-1 B”)."""
        note = Note(text, level if level in LEVEL_ICON else "info", _norm_action(action), read=quiet)
        _log_note(note.text, note.level)
        self.history.insert(0, note)
        del self.history[200:]
        t = Toast(note, self.window)
        t.closed.connect(self._closed)
        self.toasts.append(t)
        while len(self.toasts) > (self.MAX_LANE if self.lane_mode() else self.MAX_TOASTS):
            self.toasts[0].dismiss()
        t.show()
        t.raise_()
        self._layout()
        self._desktop(note)
        self.changed.emit()
        return note

    def unread(self) -> int:
        return sum(1 for n in self.history if not n.read)

    def mark_read(self):
        for n in self.history:
            n.read = True
        self.changed.emit()

    def clear(self):
        self.history.clear()
        self.changed.emit()

    def _closed(self, t):
        if t in self.toasts:
            self.toasts.remove(t)
        self._layout()

    def _top(self) -> int:
        """Top edge of the page area (below the menu and toolbar)."""
        w = self.window
        cw = w.centralWidget() if hasattr(w, "centralWidget") else None
        if cw is not None and cw.isVisible():
            return cw.geometry().top()
        return 0

    def lane_mode(self) -> bool:
        """small window (laptop 1280×720, half screen): notices get their own row above the
        page and push it down briefly instead of covering buttons. Large window: floating at the top right."""
        w = self.window
        lane = getattr(w, "toast_lane", None)
        return lane is not None and (w.height() < LANE_BELOW_H or w.width() < LANE_BELOW_W)

    def _layout(self):
        """stack at the top right (newest on top) – floating or, with a small window, in the notice row."""
        w = self.window
        lane = getattr(w, "toast_lane", None)
        if self.lane_mode():
            lay = lane.layout()
            # side by side, as many as fit in the width (newest on the right) – pushes the page only once
            fit = max(1, min(self.MAX_LANE, (max(lane.width(), w.width() - 120) + 8) // 368))
            while len(self.toasts) > fit:
                self.toasts[0].dismiss()
            for t in list(self.toasts):
                if t.parentWidget() is lane:
                    lay.removeWidget(t)
            for t in self.toasts:
                if t.parentWidget() is not lane:
                    t.setParent(lane)
                lay.addWidget(t, 0, Qt.AlignmentFlag.AlignTop)
                t.show()
                t.setMinimumHeight(t.heightForWidth(t.width()) if t.hasHeightForWidth() else t.sizeHint().height())
            lane.setVisible(bool(self.toasts))
            return
        if lane is not None:
            for t in list(self.toasts):
                if t.parentWidget() is lane:
                    lane.layout().removeWidget(t)
                    t.setParent(w)
                    t.show()
            lane.hide()
        y = self._top() + 12
        for t in reversed(self.toasts):
            t.adjustSize()
            t.move(QPoint(max(0, w.width() - t.width() - 24), y))
            t.raise_()
            y += t.height() + 8

    def eventFilter(self, obj, ev):
        if obj is self.window and ev.type() == QEvent.Type.Resize:
            self._layout()
        return False

    # ------------------------------------------------------------------
    def _desktop(self, note: Note):
        try:
            from .theme import prefs
            pref = prefs().get("desktop_notify", "background")
        except Exception:  # noqa: BLE001
            pref = "background"
        # “in the background” = NO window of the app is active. Previously only the main window counted: with an
        # open wizard/dialog, notices like “FM-1 A connected” additionally ended up as desktop notifications.
        if pref == "never" or (pref == "background" and (self.window.isActiveWindow()
                                                          or QApplication.activeWindow() is not None)):
            return
        if QApplication.platformName() in ("offscreen", "minimal"):
            return
        from .. import APP_NAME
        title = f"{APP_NAME}: {LEVEL_TITLE[note.level]}"
        if shutil.which("notify-send"):
            urg = {"error": "critical", "warning": "normal"}.get(note.level, "low")
            try:
                subprocess.Popen(["notify-send", "-a", APP_NAME, "-i", "fm1-companion", "-u", urg, title, note.text],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
            except OSError:
                pass
        from PyQt6.QtWidgets import QSystemTrayIcon
        if QSystemTrayIcon.isSystemTrayAvailable():
            if self.tray is None:
                self.tray = QSystemTrayIcon(self.window.windowIcon(), self)
                self.tray.setToolTip(APP_NAME)
                self.tray.activated.connect(self._to_front)       # clicking the icon brings the window to the front
                self.tray.messageClicked.connect(self._to_front)
                self._tray_hide = QTimer(self)
                self._tray_hide.setSingleShot(True)
                self._tray_hide.timeout.connect(self.tray.hide)   # only there for the message, no dead permanent icon
            self.tray.show()
            self.tray.showMessage(title, note.text)
            self._tray_hide.start(20000)

    def _to_front(self, *_):
        w = self.window
        if w.isMinimized():
            w.showNormal()
        w.show()
        w.raise_()
        w.activateWindow()
        if self.tray is not None:
            self.tray.hide()


def center() -> NotificationCenter | None:
    return _center


class HistoryDialog(QDialog):
    """History of all notices of this session."""

    def __init__(self, nc: NotificationCenter, parent=None):
        super().__init__(parent)
        from .common import fmt_date, heading, icon
        self.setWindowTitle(_("Notifications"))
        self.resize(520, 440)
        lay = QVBoxLayout(self)
        lay.addWidget(heading(_("Notifications"), _("All notices since the app was started.")))
        self.list = QListWidget()
        lay.addWidget(self.list, 1)
        for n in nc.history:
            it = QListWidgetItem(icon(LEVEL_ICON[n.level]),
                                 f"{n.text}\n{fmt_date(time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(n.time)))}")
            it.setData(Qt.ItemDataRole.UserRole, n)
            self.list.addItem(it)
        if not nc.history:
            self.list.addItem(_("No notifications yet."))
        self.list.itemDoubleClicked.connect(self._act)
        row = QHBoxLayout()
        clr = QPushButton(icon("edit-clear-all"), "Verlauf leeren")
        clr.clicked.connect(lambda: (nc.clear(), self.list.clear()))
        close = QPushButton(_("Close"))
        close.setDefault(True)
        close.clicked.connect(self.accept)
        row.addWidget(clr)
        row.addStretch(1)
        row.addWidget(close)
        lay.addLayout(row)
        nc.mark_read()

    def _act(self, it):
        n = it.data(Qt.ItemDataRole.UserRole)
        if isinstance(n, Note) and n.action:
            n.action[1]()
