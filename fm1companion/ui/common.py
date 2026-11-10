# SPDX-License-Identifier: GPL-3.0-only
"""Shared UI building blocks: background jobs, formatting, icons."""
from __future__ import annotations

import datetime as dt
import threading
import traceback
from typing import Callable

from pathlib import Path

from PyQt6.QtCore import QByteArray, QEvent, QObject, QPoint, QRect, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QFont, QIcon, QIconEngine, QPainter, QPixmap
from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel, QLayout, QMessageBox,
                             QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout,
                             QWidget)

from ..i18n import _

ICON_DIR = Path(__file__).resolve().parents[1] / "icons"
_svg_cache: dict[str, str] = {}
_icon_cache: dict[tuple, QIcon] = {}
_HAVE_SVG: bool | None = None


def _svg_ok() -> bool:
    global _HAVE_SVG
    if _HAVE_SVG is None:
        try:
            from PyQt6.QtSvg import QSvgRenderer  # noqa: F401
            _HAVE_SVG = True
        except ImportError:                    # python3-pyqt6.qtsvg missing -> desktop icons
            _HAVE_SVG = False
    return _HAVE_SVG


def _svg_text(lname: str) -> str:
    if lname not in _svg_cache:
        f = ICON_DIR / "lucide" / f"{lname}.svg"
        try:
            _svg_cache[lname] = f.read_text("utf-8")
        except OSError:
            _svg_cache[lname] = ""
    return _svg_cache[lname]


class _TintEngine(QIconEngine):
    """draws a Lucide SVG in the current design's color (follows light/dark switches)."""

    def __init__(self, lname: str, tint: str = ""):
        super().__init__()
        self.lname, self.tint = lname, tint
        self._pm: dict[tuple, QPixmap] = {}

    def _color(self, mode) -> str:
        from . import theme
        t = theme.current()
        if mode == QIcon.Mode.Disabled:
            return theme.mix(t.text, t.bg, 0.55)
        if self.tint == "accent":
            return t.accent_fg
        if self.tint in ("danger", "warning", "success", "info"):
            return getattr(t, self.tint)
        return t.text

    def pixmap(self, size, mode, state):
        from PyQt6.QtSvg import QSvgRenderer
        col = self._color(mode)
        key = (size.width(), size.height(), col)
        pm = self._pm.get(key)
        if pm is None:
            pm = QPixmap(size)
            pm.fill(Qt.GlobalColor.transparent)
            svg = _svg_text(self.lname).replace("currentColor", col)
            r = QSvgRenderer(QByteArray(svg.encode()))
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            side = min(size.width(), size.height())
            r.render(p, QRectF((size.width() - side) / 2, (size.height() - side) / 2, side, side))
            p.end()
            if len(self._pm) > 24:
                self._pm.clear()
            self._pm[key] = pm
            _remember_pixmap(pm, self.lname, self.tint)
        return pm

    def scaledPixmap(self, size, mode, state, scale):
        pm = self.pixmap(size, mode, state)
        if scale and scale > 0 and pm.devicePixelRatio() != scale:
            pm.setDevicePixelRatio(scale)
            _remember_pixmap(pm, self.lname, self.tint)
        return pm

    def paint(self, painter, rect, mode, state):
        dpr = painter.device().devicePixelRatioF() if painter.device() else 1.0
        pm = self.pixmap(QSize(max(1, round(rect.width() * dpr)), max(1, round(rect.height() * dpr))), mode, state)
        painter.drawPixmap(QRectF(rect), pm, QRectF(pm.rect()))

    def clone(self):
        return _TintEngine(self.lname, self.tint)

    def availableSizes(self, mode=QIcon.Mode.Normal, state=QIcon.State.Off):
        return [QSize(n, n) for n in (16, 22, 24, 32, 48, 64)]

    def iconName(self):
        return self.lname


_PIX_REGISTRY: dict[int, tuple[str, str]] = {}


def _remember_pixmap(pm: QPixmap, lname: str, tint: str):
    """remembers which icon a pixmap shows – so that label icons (QLabel.setPixmap)
    can be recolored on a light/dark switch."""
    if len(_PIX_REGISTRY) > 4000:
        _PIX_REGISTRY.clear()
    _PIX_REGISTRY[pm.cacheKey()] = (lname, tint)


def refresh_icon_labels(app=None):
    """after a design switch: draw all QLabels with a built-in icon in the new text color
    (QLabel only stores a finished image – without this, e.g. dark icons would stay on a dark background)."""
    from PyQt6.QtWidgets import QApplication
    app = app or QApplication.instance()
    if app is None or not _svg_ok():
        return 0
    n = 0
    for w in app.allWidgets():
        if not isinstance(w, QLabel):
            continue
        pm = w.pixmap()
        if pm is None or pm.isNull():
            continue
        hit = _PIX_REGISTRY.get(pm.cacheKey())
        if not hit:
            continue
        eng = _TintEngine(*hit)
        dpr = pm.devicePixelRatio() or 1.0
        new = eng.pixmap(pm.size(), QIcon.Mode.Normal, QIcon.State.Off)
        new.setDevicePixelRatio(dpr)
        _remember_pixmap(new, *hit)
        w.setPixmap(new)
        n += 1
    return n


def bundled_icon(name: str) -> QIcon:
    """Icon from the built-in set (freedesktop name or Lucide name), otherwise empty."""
    from ..icons.names import lucide_name
    lname, tint = lucide_name(name)
    if not lname and _svg_text(name):
        lname = name
    if not lname or not _svg_ok() or not _svg_text(lname):
        return QIcon()
    return QIcon(_TintEngine(lname, tint))


def icon_mode() -> str:
    """'app' (built-in icons first) or 'system' (desktop theme first)."""
    try:
        from .. import config
        return config.module("rahmen", {}).get("icons", "app")
    except Exception:  # noqa: BLE001
        return "app"


def icon_cache_clear():
    _icon_cache.clear()


def icon(*names: str) -> QIcon:
    """first available icon. Default: uniform built-in set (Lucide, in the design's text
    color); the setting 'Icons: system theme' prefers Breeze/Papirus. Unknown names
    fall back to the desktop theme."""
    key = (icon_mode(),) + names
    if key in _icon_cache:
        return _icon_cache[key]
    order = ("theme", "app") if key[0] == "system" else ("app", "theme")
    res = QIcon()
    for how in order:
        for n in names:
            ic = bundled_icon(n) if how == "app" else QIcon.fromTheme(n)
            if not ic.isNull():
                res = ic
                break
        if not res.isNull():
            break
    _icon_cache[key] = res
    return res


def fmt_size(n: float | None) -> str:
    if n is None:
        return "–"
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(n) < 1024 or unit == "TiB":
            from ..i18n import decimal
            return f"{n:.0f} {unit}" if unit == "B" else f"{decimal(n, 1)} {unit}"   # de: 430,8 KiB, en: 430.8 KiB
        n /= 1024
    return f"{n:.1f}"


def fmt_secs(s: float | None) -> str:
    if s is None:
        return "–"
    s = int(s)
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60}:{s % 60:02d} min"
    d, rest = divmod(s, 86400)
    h, rest = divmod(rest, 3600)
    if d:
        return _("{days} d {hours} h {minutes} min").format(days=d, hours=h, minutes=rest // 60)
    return f"{h} h {rest // 60} min"


def fmt_date(iso: str) -> str:
    try:
        t = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if t.tzinfo:
            t = t.astimezone()
        return t.strftime("%d.%m.%Y %H:%M")
    except (ValueError, AttributeError):
        return iso or "–"


class Worker(QObject):
    log = pyqtSignal(str)
    progress = pyqtSignal(int, int)
    done = pyqtSignal(object)
    failed = pyqtSignal(str, str)

    def __init__(self, fn: Callable, *args, **kwargs):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.cancelled = False

    def run(self):
        try:
            res = self.fn(self, *self.args, **self.kwargs)
            self.done.emit(res)
        except Exception as e:      # noqa: BLE001 – show everything
            msg = fs_error_text(e) if isinstance(e, OSError) else (str(e) or type(e).__name__)
            try:
                self.failed.emit(msg, traceback.format_exc())
            except RuntimeError:     # dialog already closed
                pass


_running: set = set()


def _text_of(spec, value) -> str:
    """done_text/fail_text: str, callable(value) -> str|None or None."""
    if spec is None:
        return ""
    try:
        t = spec(value) if callable(spec) else str(spec)
    except Exception:  # noqa: BLE001 – a broken summary must not disturb the flow
        return ""
    return t or ""


def run_job(parent: QWidget | None, fn: Callable, *args, on_done=None, on_fail=None, on_log=None,
            on_progress=None, done_text=None, fail_text=None, notify_fail: bool = False, **kwargs) -> Worker:
    """fn(worker, *args) runs in a background thread; result/error arrive in the GUI thread.

    Feedback without a dialog: ``done_text`` (str or callable(result) -> str) shows a toast on success
    (“Done”); ``fail_text`` (str or callable(error text) -> str) or ``notify_fail=True`` shows an
    error toast – in addition to on_fail, if given.

    Deliberately a Python daemon thread instead of QThread: if a job (e.g. network) is still hanging when
    the app exits, it simply ends with the process – a QThread would then make Qt abort
    with 'Destroyed while thread is still running'."""
    w = Worker(fn, *args, **kwargs)          # lives in the GUI thread: signals arrive there
    if on_log:
        w.log.connect(on_log)
    if on_progress:
        w.progress.connect(on_progress)

    def ok(res):
        _running.discard(w)
        if on_done:
            on_done(res)
        t = _text_of(done_text, res)
        if t:
            notify(t, "success")

    def bad(msg, tb):
        _running.discard(w)
        t = _text_of(fail_text, msg)
        if t or notify_fail:
            notify(t or _("Failed: {error}").format(error=msg), "error")
        if on_fail:
            on_fail(msg, tb)
        elif not (t or notify_fail):
            show_error(parent, _("Error"), msg, tb)

    w.done.connect(ok)
    w.failed.connect(bad)
    _running.add(w)
    threading.Thread(target=w.run, daemon=True, name="fm1c-job").start()
    return w


def show_error(parent, title: str, msg: str, details: str = ""):
    box = QMessageBox(QMessageBox.Icon.Warning, title, msg, QMessageBox.StandardButton.Ok, parent)
    if details:
        box.setDetailedText(details)
    german_buttons(box)
    box.exec()


def show_done(parent, title: str, msg: str):
    """short success message as a dialog (for actions whose result one needs to see)."""
    box = QMessageBox(QMessageBox.Icon.Information, title, msg, QMessageBox.StandardButton.Ok, parent)
    german_buttons(box)
    box.button(QMessageBox.StandardButton.Ok).setText(_("Close"))
    box.exec()


class _GuiCaller(QObject):
    call = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.call.connect(self._run, Qt.ConnectionType.BlockingQueuedConnection)

    def _run(self, box):
        try:
            box.append(box[0]())
        except Exception as e:  # noqa: BLE001 – pass on to the calling thread
            box.append(e)


_gui_caller: _GuiCaller | None = None


def call_in_gui(fn: Callable):
    """Run fn() in the GUI thread and wait for the result (e.g. a prompt from a background job)."""
    global _gui_caller
    from PyQt6.QtCore import QThread
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None or QThread.currentThread() is app.thread():
        return fn()
    if _gui_caller is None or _gui_caller.thread() is not app.thread():
        raise RuntimeError("call_in_gui: zuerst einmal im GUI-Thread aufrufen (init_gui_caller)")
    box = [fn]
    _gui_caller.call.emit(box)
    res = box[1] if len(box) > 1 else None
    if isinstance(res, Exception):
        raise res
    return res


def init_gui_caller():
    """create in the GUI thread (before the first call_in_gui from a background thread)."""
    global _gui_caller
    if _gui_caller is None:
        _gui_caller = _GuiCaller()


def confirm(parent, title: str, text: str, ok: str | None = None, danger: bool = False) -> bool:
    ok = ok or _("Proceed")
    box = QMessageBox(QMessageBox.Icon.Warning if danger else QMessageBox.Icon.Question, title, text,
                      parent=parent)
    yes = box.addButton(ok, QMessageBox.ButtonRole.AcceptRole)
    no = box.addButton(_("Cancel"), QMessageBox.ButtonRole.RejectRole)
    if danger:                                 # destructive action: red button, cancel is the default
        yes.setProperty("variant", "danger")
        box.setDefaultButton(no)
    else:
        box.setDefaultButton(yes)
    box.exec()
    return box.clickedButton() is yes


class JobDialog(QDialog):
    """Progress + log for longer operations – with clear feedback at the end.

    JobDialog(parent, title, fn, *args, on_done=None, close_on_success=False,
              done_text=None, fail_text=None, actions=(), cancellable=False, cancel_tip="", **kwargs)

    * fn(worker, *args, **kwargs) runs in the background (worker.log / worker.progress / worker.cancelled).
    * While running: heading “<title> – running …”, button “Cancel” (sets worker.cancelled; only active with
      cancellable=True, otherwise grayed out with the reason cancel_tip).
    * Success: green checkmark, “Done”, below it one line of result. Source of the line (first non-empty):
      done_text (str or callable(result) -> str), return value of on_done(result) if str, last
      log line. Plus “Close” and optional action buttons actions=[(label, fn(result)), …].
    * Error: red icon, “Failed”, readable explanation (fail_text: str or callable(message) -> str,
      otherwise the message itself), log expandable under “Details”, “Close”.
    * Result: .ok, .result_value, .summary (success or error line), .error (error message)."""

    def __init__(self, parent, title: str, fn: Callable, *args, on_done=None, close_on_success=False,
                 done_text=None, fail_text=None, actions=(), cancellable: bool = False, cancel_tip: str = "",
                 **kwargs):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(560, 360)
        self.title = title
        self.result_value = None
        self.ok = False
        self.summary = ""
        self.error = ""
        self._done_text, self._fail_text, self._actions = done_text, fail_text, list(actions or ())
        self._last_log = ""
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        top.setSpacing(10)
        self.state_icon = QLabel()
        self.state_icon.setFixedSize(28, 28)
        self.state_icon.hide()
        top.addWidget(self.state_icon, 0, Qt.AlignmentFlag.AlignVCenter)
        self.head = QLabel(_("{title} – running …").format(title=title))
        f = self.head.font()
        f.setPointSizeF(f.pointSizeF() * 1.2)
        f.setBold(True)
        self.head.setFont(f)
        self.head.setWordWrap(True)
        top.addWidget(self.head, 1)
        lay.addLayout(top)
        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)
        self.result_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.result_label.hide()
        lay.addWidget(self.result_label)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        lay.addWidget(self.bar)
        self.details_btn = QPushButton(_("Show details"))
        self.details_btn.setProperty("variant", "flat")
        self.details_btn.setCheckable(True)
        self.details_btn.toggled.connect(self._details)
        self.details_btn.hide()
        lay.addWidget(self.details_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self.logv = QPlainTextEdit()
        self.logv.setReadOnly(True)
        self.logv.setFont(QFont("monospace"))
        lay.addWidget(self.logv, 1)
        lay.addStretch(0)                      # takes up the space when the log is collapsed
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        german_buttons(self.buttons)
        self.buttons.rejected.connect(self.reject)
        self.buttons.button(QDialogButtonBox.StandardButton.Close).setEnabled(False)
        self.cancel_button = self.buttons.addButton(_("Cancel"), QDialogButtonBox.ButtonRole.ActionRole)
        self.cancel_button.setEnabled(bool(cancellable))
        self.cancel_button.setToolTip(_("Cancels at the next safe point.") if cancellable else
                                      (cancel_tip or _("This operation cannot be cancelled safely – "
                                                       "please wait until it is finished.")))
        self.cancel_button.clicked.connect(self._cancel)
        if not cancellable and type(self) is not JobDialog:
            self.cancel_button.hide()          # subclasses with their own cancel (e.g. installation)
        lay.addWidget(self.buttons)
        self._on_done = on_done
        self._close_on_success = close_on_success
        self.worker = run_job(self, fn, *args, on_done=self._done, on_fail=self._fail, on_log=self.add,
                              on_progress=self._prog, **kwargs)

    def add(self, line: str):
        self.logv.appendPlainText(line)
        if line.strip():
            self._last_log = line.strip()

    def _prog(self, a: int, b: int):
        if b <= 0:
            self.bar.setRange(0, 0)
        else:
            self.bar.setRange(0, b)
            self.bar.setValue(min(a, b))

    def _cancel(self):
        self.worker.cancelled = True
        self.cancel_button.setEnabled(False)
        self.add(_("Cancellation requested – takes effect at the next safe point."))

    def _details(self, on: bool):
        self.logv.setVisible(on)
        self.details_btn.setText(_("Hide details") if on else _("Show details"))

    def _state(self, ok: bool, head: str, text: str):
        from . import theme
        name = "emblem-ok" if ok else "dialog-error"
        from ..icons.names import lucide_name
        lname = lucide_name(name)[0]
        if lname and _svg_ok() and _svg_text(lname):
            self.state_icon.setPixmap(QIcon(_TintEngine(lname, "success" if ok else "danger")).pixmap(28, 28))
        else:
            self.state_icon.setPixmap(icon(name).pixmap(28, 28))
        self.state_icon.show()
        col = theme.current().success if ok else theme.current().danger
        self.head.setText(head)
        self.head.setStyleSheet(f"color: {col};")
        self.result_label.setText(text)
        self.result_label.setVisible(bool(text))
        self.summary = text
        self.cancel_button.hide()
        self.bar.hide()
        self.details_btn.show()
        self.details_btn.setChecked(False)
        self._details(False)
        close = self.buttons.button(QDialogButtonBox.StandardButton.Close)
        close.setEnabled(True)
        close.setText(_("Close"))
        close.setDefault(True)
        close.setFocus()

    def _done(self, res):
        self.ok = True
        self.result_value = res
        self.bar.setRange(0, 1)
        self.bar.setValue(1)
        ret = self._on_done(res) if self._on_done else None
        text = _text_of(self._done_text, res) or (ret if isinstance(ret, str) else "") or self._last_log \
            or _("{title}: done.").format(title=self.title)
        self._state(True, _("Finished"), text)
        for label, fn in self._actions:
            b = self.buttons.addButton(label, QDialogButtonBox.ButtonRole.ActionRole)
            b.clicked.connect(lambda _c=False, f=fn: (f(self.result_value), self.accept()))
        if self._close_on_success:
            self.accept()

    def _fail(self, msg, tb):
        self.error = msg
        self.bar.setRange(0, 1)
        self.bar.setValue(0)
        self.add("")
        self.add(_("ERROR: {error}").format(error=msg))
        if tb:
            self.add("")
            self.add(tb.rstrip())
        text = _text_of(self._fail_text, msg) or msg
        self._state(False, _("Failed"), text)

    def reject(self):
        if self.buttons.button(QDialogButtonBox.StandardButton.Close).isEnabled():
            super().reject()


class Card(QFrame):
    """Box with a heading (drawn by the design as a rounded card).
    ``Card(title)`` as before; optionally ``icon=`` (icon name) and ``subtitle=``."""

    def __init__(self, title: str, parent=None, *, icon: str | None = None, subtitle: str = ""):
        super().__init__(parent)
        self.setObjectName("Card")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        # height for width: wrapping texts and button rows (responsive.reflow/flow_row) need more height in a narrow
        # window than the sizeHint says – with “Maximum” they were cut off
        sp = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        sp.setHeightForWidth(True)
        self.setSizePolicy(sp)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(16, 14, 16, 14)
        self.lay.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(8)
        if icon:
            ic = QLabel()
            ic.setPixmap(globals()["icon"](icon).pixmap(18, 18))
            head.addWidget(ic)
        t = QLabel(title)
        t.setProperty("role", "cardTitle")
        self.title_label = t
        head.addWidget(t)
        head.addStretch(1)
        self.head = head
        self.lay.addLayout(head)
        if subtitle:
            st = QLabel(subtitle)
            st.setWordWrap(True)
            st.setProperty("role", "cardSubtitle")
            self.lay.addWidget(st)


def heading(text: str, sub: str = "") -> QWidget:
    """Page header: large title, below it a sentence in secondary text color (links allowed)."""
    w = QWidget()
    w.setObjectName("PageHeading")
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 8)
    lay.setSpacing(2)
    t = QLabel(text)
    t.setProperty("role", "pageTitle")
    lay.addWidget(t)
    if sub:
        s = QLabel(sub)
        s.setWordWrap(True)
        s.setTextFormat(Qt.TextFormat.RichText)
        s.setOpenExternalLinks(True)
        s.setProperty("role", "pageSubtitle")
        lay.addWidget(s)
    return w


def set_variant(widget: QWidget, variant: str) -> QWidget:
    """Button variant: "primary" (main action), "danger" (delete/overwrite), "flat", "secondary"/""."""
    widget.setProperty("variant", variant)
    st = widget.style()
    st.unpolish(widget)
    st.polish(widget)
    return widget


def button(text: str, icon_name: str | tuple = (), variant: str = "", slot=None, tooltip: str = "") -> QPushButton:
    names = (icon_name,) if isinstance(icon_name, str) else tuple(icon_name)
    b = QPushButton(icon(*names) if names else QIcon(), text)
    if variant:
        b.setProperty("variant", variant)
    if slot:
        b.clicked.connect(slot)
    if tooltip:
        b.setToolTip(tooltip)
    return b


def label(text: str, role: str = "", wrap: bool = True) -> QLabel:
    """QLabel with a design role: "muted", "small", "section", "badge", "cardTitle" …"""
    lab = QLabel(text)
    lab.setWordWrap(wrap)
    if role:
        lab.setProperty("role", role)
    return lab


def badge(text: str, level: str = "") -> QLabel:
    """small pill, level = "" | "success" | "warning" | "error"."""
    b = QLabel(text)
    b.setProperty("role", "badge")
    if level:
        b.setProperty("level", level)
    b.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Maximum)
    return b


def empty_state(icon_name: str, title: str, text: str = "", action_text: str = "", action=None) -> QWidget:
    """empty state: large icon, title, explanation ("here's how …"), optionally a button.
    Title and text sit in a centred column of at most 560 px without layout alignment, so that word-wrapped lines
    get their full height (a layout with alignment sizes labels by sizeHint and cuts longer translations)."""
    w = QWidget()
    outer = QVBoxLayout(w)
    outer.setContentsMargins(24, 24, 24, 24)
    outer.addStretch(1)
    row = QHBoxLayout()
    row.addStretch(1)
    col = QWidget()
    col.setMaximumWidth(560)
    col.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    lay = QVBoxLayout(col)
    lay.setContentsMargins(0, 0, 0, 0)
    ic = QLabel()
    ic.setPixmap(icon(icon_name, "dialog-information").pixmap(48, 48))
    ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
    ic.setEnabled(False)
    lay.addWidget(ic)
    t = QLabel(title)
    t.setProperty("role", "emptyTitle")
    t.setAlignment(Qt.AlignmentFlag.AlignCenter)
    t.setWordWrap(True)
    lay.addWidget(t)
    if text:
        s = QLabel(text)
        s.setProperty("role", "muted")
        s.setAlignment(Qt.AlignmentFlag.AlignCenter)
        s.setWordWrap(True)
        lay.addWidget(s)
    if action_text and action:
        b = button(action_text, variant="primary", slot=action)
        lay.addWidget(b, 0, Qt.AlignmentFlag.AlignHCenter)
    row.addWidget(col, 20)
    row.addStretch(1)
    outer.addLayout(row)
    outer.addStretch(1)
    return w


class Disclosure(QWidget):
    """expandable section: header row with an arrow (“Details”), below it an initially hidden widget.
    Disclosure("Details", inner_widget) or Disclosure("Background", html_text)."""

    def __init__(self, title: str = "Details", content=None, parent=None, *, open_: bool = False):
        super().__init__(parent)
        from PyQt6.QtWidgets import QToolButton
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.button = QToolButton()
        self.button.setObjectName("DisclosureButton")
        self.button.setText(title)
        self.button.setCheckable(True)
        self.button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.button.setArrowType(Qt.ArrowType.RightArrow)
        self.button.setToolTip(_("Show/hide background and details"))
        self.button.setCursor(Qt.CursorShape.PointingHandCursor)
        lay.addWidget(self.button, 0, Qt.AlignmentFlag.AlignLeft)
        if isinstance(content, str):
            lab = QLabel(content)
            lab.setWordWrap(True)
            lab.setTextFormat(Qt.TextFormat.RichText)
            lab.setOpenExternalLinks(True)
            lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
            content = lab
        self.content = content if content is not None else QWidget()
        self.content.setObjectName("DisclosureContent")
        lay.addWidget(self.content)
        self.content.setVisible(False)
        self.button.toggled.connect(self._toggle)
        if open_:
            self.button.setChecked(True)

    def _toggle(self, on: bool):
        self.content.setVisible(on)
        self.button.setArrowType(Qt.ArrowType.DownArrow if on else Qt.ArrowType.RightArrow)

    def is_open(self) -> bool:
        return self.button.isChecked()

    def set_open(self, on: bool):
        self.button.setChecked(on)


def notify(text: str, level: str = "info", action=None):
    """Show a notice (toast + history), from any thread: notify("Backup done", "success",
    action=("Open folder", fn))."""
    from .notify import notify as _n
    _n(text, level, action)


def hbox(*widgets, stretch_last=False) -> QHBoxLayout:
    h = QHBoxLayout()
    for w in widgets:
        if w is None:
            h.addStretch(1)
        elif isinstance(w, QWidget):
            h.addWidget(w)
        else:
            h.addLayout(w)
    if stretch_last:
        h.addStretch(1)
    return h


# ====================================================================== German for Qt standard buttons
GERMAN_BUTTONS = {
    "Ok": "OK", "Cancel": "Abbrechen", "Close": "Schließen", "Yes": "Ja", "No": "Nein", "Apply": "Übernehmen",  # i18n: ignore
    "Save": "Speichern", "Discard": "Verwerfen", "Open": "Öffnen", "Reset": "Zurücksetzen", "Help": "Hilfe",  # i18n: ignore
    "Abort": "Abbrechen", "Retry": "Wiederholen", "Ignore": "Ignorieren", "YesToAll": "Ja, alle",  # i18n: ignore
    "NoToAll": "Nein, keine", "RestoreDefaults": "Standardwerte", "SaveAll": "Alle speichern",  # i18n: ignore
}
_ENGLISH = {"OK": "OK", "&OK": "OK", "Cancel": "Abbrechen", "&Cancel": "Abbrechen", "Close": "Schließen",  # i18n: ignore
            "&Close": "Schließen", "Yes": "Ja", "&Yes": "Ja", "No": "Nein", "&No": "Nein", "Apply": "Übernehmen",  # i18n: ignore
            "&Apply": "Übernehmen", "Save": "Speichern", "&Save": "Speichern", "Discard": "Verwerfen",  # i18n: ignore
            "Open": "Öffnen", "&Open": "Öffnen", "Reset": "Zurücksetzen", "Help": "Hilfe", "Abort": "Abbrechen",  # i18n: ignore
            "Retry": "Wiederholen", "Ignore": "Ignorieren", "Restore Defaults": "Standardwerte",  # i18n: ignore
            "Show Details...": "Details anzeigen …", "Hide Details...": "Details ausblenden …"}  # i18n: ignore


def german_buttons(box):
    """Label the standard buttons of a QDialogButtonBox / QMessageBox in German (if Qt has not loaded a German
    translation). Returns box.
    Only for the German UI – in any other language Qt's own (or translated) labels stay."""
    from .. import i18n
    if i18n.current_language() != "de":
        return box
    try:
        for b in box.buttons():
            t = b.text()
            if t in _ENGLISH:
                b.setText(_ENGLISH[t])
                continue
            sb = box.standardButton(b)
            name = getattr(sb, "name", "")
            if name in GERMAN_BUTTONS:
                b.setText(GERMAN_BUTTONS[name])
    except (AttributeError, RuntimeError, TypeError):
        pass
    return box


class _ButtonGermanizer(QObject):
    """Fallback without qtbase_de.qm: labels the standard buttons of every dialog when it is shown."""

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Type.Show and isinstance(obj, QDialog):
            for bb in obj.findChildren(QDialogButtonBox):
                german_buttons(bb)
            if isinstance(obj, QMessageBox):
                german_buttons(obj)
        return False


_translators: list = []


def install_translations(app) -> bool:
    """Qt's own texts (OK/Cancel/Close, file dialogs, context menus) in the language of the app (i18n).
    Loads qtbase_<lang>.qm from Qt's translations folder (Debian: package qt6-translations-l10n); for German,
    if the file is missing, an event filter labels the standard buttons itself. English needs nothing.
    -> True if a Qt translation was loaded."""
    from PyQt6.QtCore import QLibraryInfo, QLocale, QTranslator
    from .. import i18n
    lang = i18n.current_language()
    QLocale.setDefault(qt_locale(lang))
    if lang == i18n.SOURCE_LANGUAGE:
        return False
    paths = []
    try:
        paths.append(QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath))
    except Exception:  # noqa: BLE001
        pass
    paths += ["/usr/share/qt6/translations", "/usr/share/qt5/translations"]
    loaded = False
    for name in (f"qtbase_{lang}", f"qt_{lang}"):
        for p in paths:
            tr = QTranslator(app)
            if p and tr.load(name, p):
                app.installTranslator(tr)
                _translators.append(tr)
                loaded = True
                break
        if loaded:
            break
    if lang == "de":
        g = _ButtonGermanizer(app)
        app.installEventFilter(g)
        _translators.append(g)
    return loaded


def qt_locale(lang: str | None = None):
    """QLocale for number formats (decimal comma in spin boxes) matching the UI language."""
    from PyQt6.QtCore import QLocale
    from .. import i18n
    lang = lang or i18n.current_language()
    if lang == "de":
        return QLocale(QLocale.Language.German, QLocale.Country.Germany)
    if lang == "en":
        return QLocale(QLocale.Language.English, QLocale.Country.UnitedStates)
    return QLocale(lang)


# ====================================================================== Undo per device
def device_tag(ctx) -> tuple[str, str, str]:
    """(profile ID, port key, display name) of the currently connected FM-1."""
    prof = getattr(ctx, "profile", None)
    port = getattr(ctx, "port", None)
    name = prof.name if prof else (port.label if port else _("another FM-1"))
    return (prof.id if prof else "", port.key if port else "", name)


def same_device(a: tuple, b: tuple) -> bool:
    if a[0] or b[0]:
        return a[0] == b[0]
    return bool(a[1]) and a[1] == b[1]


class DeviceUndo:
    """Undo stack whose entries know which FM-1 they belong to. Undo is refused
    if a different device is currently connected – otherwise the old content of device A would end up on device B."""

    def __init__(self):
        self.items: list[tuple[tuple, object, str]] = []   # (device_tag, data, description)

    def push(self, ctx, data, what: str = "", tag: tuple | None = None):
        """tag: device_tag(ctx) from BEFORE the job (in case the device has changed in the meantime)."""
        self.items.append((tag or device_tag(ctx), data, what))

    def __len__(self):
        return len(self.items)

    def __bool__(self):
        return bool(self.items)

    def __getitem__(self, i):
        """Data of the i-th entry (like the former list: undo[-1])."""
        return self.items[i][1]

    def peek(self):
        return self.items[-1][1] if self.items else None

    def what(self) -> str:
        return self.items[-1][2] if self.items else ""

    def owner(self) -> str:
        return self.items[-1][0][2] if self.items else ""

    def pop(self):
        return self.items.pop()[1] if self.items else None

    def clear(self):
        self.items.clear()

    def matches(self, ctx) -> bool:
        return bool(self.items) and same_device(self.items[-1][0], device_tag(ctx))

    def refusal(self) -> str:
        return _("This undo belongs to {device} – please connect that device again. (Otherwise the old content "
                 "would end up on the wrong FM-1.)").format(device=self.owner())

    def check(self, ctx, parent=None) -> bool:
        """True if the top entry belongs to the connected device; otherwise a notice and False."""
        if not self.items:
            return False
        if self.matches(ctx):
            return True
        if parent is not None:
            show_error(parent, _("Undo not possible"), self.refusal())
        return False


# ====================================================================== File system
def fs_error_text(e: BaseException, what: str = "") -> str:
    """understandable message for OSError & co. (instead of "[Errno 13] …")."""
    import errno as _e
    path = getattr(e, "filename", None) or ""
    code = getattr(e, "errno", None)

    def msg(with_path: str, without: str) -> str:
        return with_path.format(path=path) if path else without
    if code in (_e.EACCES, _e.EPERM, _e.EROFS):
        base = msg(_("No write permission ({path}) – please check the folder's permissions or choose a different main "
                     "folder in the settings."),
                   _("No write permission – please check the folder's permissions or choose a different main folder in "
                     "the settings."))
    elif code == _e.ENOSPC:
        base = msg(_("The storage device is full ({path})."), _("The storage device is full."))
    elif code == _e.ENOENT:
        base = msg(_("File or folder not found ({path})."), _("File or folder not found."))
    elif code == _e.EISDIR:
        base = msg(_("This is a folder, not a file ({path})."), _("This is a folder, not a file."))
    elif code == _e.ENOTDIR:
        base = msg(_("Part of the path is not a folder ({path})."), _("Part of the path is not a folder."))
    elif isinstance(e, OSError):
        err = e.strerror or e
        base = (_("File error ({path}): {error}").format(path=path, error=err) if path else
                _("File error: {error}").format(error=err))
    else:
        base = str(e) or type(e).__name__
    return _("{what}: {error}").format(what=what, error=base) if what else base


def safe_folder(name: str) -> tuple[Path, str]:
    """Create a visible subfolder (~/FM-1/<name>) without crashing. -> (path, error text or "")."""
    from .. import config
    try:
        return config.folder(name), ""
    except OSError as e:
        try:
            root = Path(config.load().root)
        except Exception:  # noqa: BLE001
            root = Path.home() / "FM-1"
        return root / name, fs_error_text(e, _("Folder “{name}” cannot be created").format(name=name))


# ====================================================================== Layout helpers
class FlowLayout(QLayout):
    """Button row that wraps to the next line when space is short. addStretch() is ignored
    (compatibility with QHBoxLayout code)."""

    def __init__(self, parent=None, hspacing: int = 6, vspacing: int = 6):
        super().__init__(parent)
        self._items: list = []
        self._h, self._v = hspacing, vspacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def addStretch(self, *_a):
        pass

    def addSpacing(self, *_a):
        pass

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do(rect, False)

    def _visible(self):
        return [it for it in self._items if not (it.widget() and it.widget().isHidden())]

    def sizeHint(self):
        m = self.contentsMargins()
        items = self._visible()
        w = sum(it.sizeHint().width() for it in items) + self._h * max(0, len(items) - 1)
        h = max((it.sizeHint().height() for it in items), default=0)
        return QSize(w + m.left() + m.right(), h + m.top() + m.bottom())

    def minimumSize(self):
        m = self.contentsMargins()
        s = QSize()
        for it in self._visible():
            s = s.expandedTo(it.minimumSize())
        return QSize(s.width() + m.left() + m.right(), s.height() + m.top() + m.bottom())

    def _do(self, rect, test):
        """Fill rows; items of a row are vertically centered (checkboxes next to buttons)."""
        m = self.contentsMargins()
        r = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        lines: list[list] = [[]]
        x = r.x()
        for it in self._visible():
            sz = it.sizeHint()
            sz = QSize(min(sz.width(), max(r.width(), it.minimumSize().width())), sz.height())
            if lines[-1] and x + sz.width() > r.right() + 1:
                lines.append([])
                x = r.x()
            lines[-1].append((it, sz, x))
            x += sz.width() + self._h
        y = r.y()
        for row in lines:
            if not row:
                continue
            hgt = max(sz.height() for _it, sz, _x in row)
            if not test:
                for it, sz, xx in row:
                    it.setGeometry(QRect(QPoint(xx, y + (hgt - sz.height()) // 2), sz))
            y += hgt + self._v
        line_end = y - self._v if any(lines) else y
        return line_end - rect.y() + m.bottom()


def flow_row(*widgets, hspacing: int = 6) -> QWidget:
    """Widget with a wrapping button row (None entries are ignored)."""
    w = QWidget()
    fl = FlowLayout(w, hspacing)
    for x in widgets:
        if isinstance(x, QWidget):
            fl.addWidget(x)
    sp = w.sizePolicy()
    sp.setHeightForWidth(True)
    w.setSizePolicy(sp)
    return w


def make_scrollable(page: QWidget, horizontal: bool = False) -> QScrollArea:
    """puts the existing content of a page into a scroll area (the page can then become
    arbitrarily small). Call at the end of the constructor, after the layout is filled."""
    lay = page.layout()
    inner = QWidget()
    inner.setObjectName("PageScrollInner")
    if lay is not None:
        inner.setLayout(lay)              # takes over layout and child widgets
    sc = QScrollArea()
    sc.setObjectName("PageScroll")
    sc.setWidgetResizable(True)
    sc.setFrameShape(QFrame.Shape.NoFrame)
    if not horizontal:
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    sc.setWidget(inner)
    outer = QVBoxLayout(page)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.addWidget(sc)
    page.scroll_area = sc
    return sc


def plural(n: int, one: str, many: str) -> str:
    """plural(1, "Pattern", "Patterns") -> "1 Pattern"."""
    return f"{n} {one if n == 1 else many}"


def start_dir(name: str) -> Path:
    """Start folder for file dialogs: ~/FM-1/<name> if it can be created, otherwise the home folder."""
    p, err = safe_folder(name)
    return p if not err and p.is_dir() else Path.home()


def fw_label(version: str) -> str:
    """Firmware identifier for display: SLOOP reports itself (like all Felucca derivatives) as “FELUCCA SLOOP 2.8.3” –
    “SLOOP 2.8.3” is displayed. Stored data stays unchanged."""
    import re as _re
    v = (version or "").strip()
    return _re.sub(r"\bFELUCCA\s+(?=(?:SLOOP|KARMA)\b)", "", v, flags=_re.IGNORECASE)
