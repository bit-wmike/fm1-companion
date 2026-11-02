# SPDX-License-Identifier: GPL-3.0-only
"""Gemeinsame Bausteine der Oberfläche: Hintergrund-Jobs, Formatierung, Icons."""
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
        except ImportError:                    # python3-pyqt6.qtsvg fehlt -> Desktop-Symbole
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
    """zeichnet ein Lucide-SVG in der Farbe des aktuellen Designs (folgt Wechseln hell/dunkel)."""

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
    """merkt sich, welches Symbol ein Pixmap zeigt – damit Beschriftungs-Symbole (QLabel.setPixmap)
    beim Wechsel hell/dunkel neu eingefärbt werden können."""
    if len(_PIX_REGISTRY) > 4000:
        _PIX_REGISTRY.clear()
    _PIX_REGISTRY[pm.cacheKey()] = (lname, tint)


def refresh_icon_labels(app=None):
    """nach einem Design-Wechsel: alle QLabel mit eingebautem Symbol in der neuen Textfarbe zeichnen
    (QLabel speichert nur ein fertiges Bild – ohne das blieben z. B. dunkle Symbole auf dunklem Grund)."""
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
    """Symbol aus dem eingebauten Satz (freedesktop-Name oder Lucide-Name), sonst leer."""
    from ..icons.names import lucide_name
    lname, tint = lucide_name(name)
    if not lname and _svg_text(name):
        lname = name
    if not lname or not _svg_ok() or not _svg_text(lname):
        return QIcon()
    return QIcon(_TintEngine(lname, tint))


def icon_mode() -> str:
    """'app' (eingebaute Symbole zuerst) oder 'system' (Desktop-Thema zuerst)."""
    try:
        from .. import config
        return config.module("rahmen", {}).get("icons", "app")
    except Exception:  # noqa: BLE001
        return "app"


def icon_cache_clear():
    _icon_cache.clear()


def icon(*names: str) -> QIcon:
    """erstes vorhandene Symbol. Standard: einheitlicher eingebauter Satz (Lucide, in der Textfarbe
    des Designs); Einstellung 'Symbole: System-Thema' bevorzugt Breeze/Papirus. Unbekannte Namen
    fallen auf das Desktop-Thema zurück."""
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
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}".replace(".", ",")   # deutsch: 430,8 KiB
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
    return (f"{d} T " if d else "") + f"{h} h {rest // 60} min"


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
        except Exception as e:      # noqa: BLE001 – alles anzeigen
            msg = fs_error_text(e) if isinstance(e, OSError) else (str(e) or type(e).__name__)
            try:
                self.failed.emit(msg, traceback.format_exc())
            except RuntimeError:     # Dialog schon geschlossen
                pass


_running: set = set()


def _text_of(spec, value) -> str:
    """done_text/fail_text: str, callable(value) -> str|None oder None."""
    if spec is None:
        return ""
    try:
        t = spec(value) if callable(spec) else str(spec)
    except Exception:  # noqa: BLE001 – eine kaputte Zusammenfassung darf den Ablauf nicht stören
        return ""
    return t or ""


def run_job(parent: QWidget | None, fn: Callable, *args, on_done=None, on_fail=None, on_log=None,
            on_progress=None, done_text=None, fail_text=None, notify_fail: bool = False, **kwargs) -> Worker:
    """fn(worker, *args) läuft in einem Hintergrund-Thread; Ergebnis/Fehler kommen im GUI-Thread an.

    Rückmeldung ohne Dialog: ``done_text`` (str oder callable(Ergebnis) -> str) zeigt bei Erfolg einen Toast
    („Erledigt“); ``fail_text`` (str oder callable(Fehlertext) -> str) bzw. ``notify_fail=True`` zeigt einen
    Fehler-Toast – zusätzlich zu on_fail, falls angegeben.

    Bewusst ein Python-Daemon-Thread statt QThread: hängt ein Job (z. B. Netzwerk) beim
    Beenden der App noch, endet er einfach mit dem Prozess – ein QThread würde Qt dann
    mit 'Destroyed while thread is still running' abbrechen lassen."""
    w = Worker(fn, *args, **kwargs)          # lebt im GUI-Thread: Signale kommen dort an
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
            notify(t or f"Fehlgeschlagen: {msg}", "error")
        if on_fail:
            on_fail(msg, tb)
        elif not (t or notify_fail):
            show_error(parent, "Fehler", msg, tb)

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
    """kurze Erfolgsmeldung als Dialog (für Aktionen, deren Ergebnis man sehen muss)."""
    box = QMessageBox(QMessageBox.Icon.Information, title, msg, QMessageBox.StandardButton.Ok, parent)
    german_buttons(box)
    box.button(QMessageBox.StandardButton.Ok).setText("Schließen")
    box.exec()


class _GuiCaller(QObject):
    call = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.call.connect(self._run, Qt.ConnectionType.BlockingQueuedConnection)

    def _run(self, box):
        try:
            box.append(box[0]())
        except Exception as e:  # noqa: BLE001 – an den aufrufenden Thread weitergeben
            box.append(e)


_gui_caller: _GuiCaller | None = None


def call_in_gui(fn: Callable):
    """fn() im GUI-Thread ausführen und auf das Ergebnis warten (z. B. eine Rückfrage aus einem Hintergrund-Job)."""
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
    """im GUI-Thread anlegen (vor dem ersten call_in_gui aus einem Hintergrund-Thread)."""
    global _gui_caller
    if _gui_caller is None:
        _gui_caller = _GuiCaller()


def confirm(parent, title: str, text: str, ok: str = "Fortfahren", danger: bool = False) -> bool:
    box = QMessageBox(QMessageBox.Icon.Warning if danger else QMessageBox.Icon.Question, title, text,
                      parent=parent)
    yes = box.addButton(ok, QMessageBox.ButtonRole.AcceptRole)
    no = box.addButton("Abbrechen", QMessageBox.ButtonRole.RejectRole)
    if danger:                                 # zerstörende Aktion: roter Knopf, Abbrechen ist Standard
        yes.setProperty("variant", "danger")
        box.setDefaultButton(no)
    else:
        box.setDefaultButton(yes)
    box.exec()
    return box.clickedButton() is yes


class JobDialog(QDialog):
    """Fortschritt + Protokoll für längere Vorgänge – mit klarer Rückmeldung am Ende.

    JobDialog(parent, title, fn, *args, on_done=None, close_on_success=False,
              done_text=None, fail_text=None, actions=(), cancellable=False, cancel_tip="", **kwargs)

    * fn(worker, *args, **kwargs) läuft im Hintergrund (worker.log / worker.progress / worker.cancelled).
    * Während des Laufs: Überschrift „<Titel> – läuft …“, Knopf „Abbrechen“ (setzt worker.cancelled; nur mit
      cancellable=True aktiv, sonst grau mit Begründung cancel_tip).
    * Erfolg: grünes Häkchen, „Fertig“, darunter eine Zeile Ergebnis. Quelle der Zeile (erste nicht leere):
      done_text (str oder callable(Ergebnis) -> str), Rückgabewert von on_done(Ergebnis) falls str, letzte
      Protokollzeile. Dazu „Schließen“ und optionale Aktionsknöpfe actions=[(Beschriftung, fn(Ergebnis)), …].
    * Fehler: rotes Symbol, „Fehlgeschlagen“, deutsche Erklärung (fail_text: str oder callable(Meldung) -> str,
      sonst die Meldung selbst), Protokoll unter „Details“ aufklappbar, „Schließen“.
    * Ergebnis: .ok, .result_value, .summary (Erfolgs- bzw. Fehlerzeile), .error (Fehlermeldung)."""

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
        self.head = QLabel(f"{title} – läuft …")
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
        self.details_btn = QPushButton("Details anzeigen")
        self.details_btn.setProperty("variant", "flat")
        self.details_btn.setCheckable(True)
        self.details_btn.toggled.connect(self._details)
        self.details_btn.hide()
        lay.addWidget(self.details_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self.logv = QPlainTextEdit()
        self.logv.setReadOnly(True)
        self.logv.setFont(QFont("monospace"))
        lay.addWidget(self.logv, 1)
        lay.addStretch(0)                      # nimmt den Platz, wenn das Protokoll eingeklappt ist
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        german_buttons(self.buttons)
        self.buttons.rejected.connect(self.reject)
        self.buttons.button(QDialogButtonBox.StandardButton.Close).setEnabled(False)
        self.cancel_button = self.buttons.addButton("Abbrechen", QDialogButtonBox.ButtonRole.ActionRole)
        self.cancel_button.setEnabled(bool(cancellable))
        self.cancel_button.setToolTip("Bricht am nächsten sicheren Punkt ab." if cancellable else
                                      (cancel_tip or "Dieser Vorgang lässt sich nicht sicher abbrechen – "
                                       "bitte warten, bis er fertig ist."))
        self.cancel_button.clicked.connect(self._cancel)
        if not cancellable and type(self) is not JobDialog:
            self.cancel_button.hide()          # Unterklassen mit eigenem Abbrechen (z. B. Installation)
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
        self.add("Abbruch angefordert – greift am nächsten sicheren Punkt.")

    def _details(self, on: bool):
        self.logv.setVisible(on)
        self.details_btn.setText("Details ausblenden" if on else "Details anzeigen")

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
        close.setText("Schließen")
        close.setDefault(True)
        close.setFocus()

    def _done(self, res):
        self.ok = True
        self.result_value = res
        self.bar.setRange(0, 1)
        self.bar.setValue(1)
        ret = self._on_done(res) if self._on_done else None
        text = _text_of(self._done_text, res) or (ret if isinstance(ret, str) else "") or self._last_log \
            or f"{self.title}: erledigt."
        self._state(True, "Fertig", text)
        for label, fn in self._actions:
            b = self.buttons.addButton(label, QDialogButtonBox.ButtonRole.ActionRole)
            b.clicked.connect(lambda _=False, f=fn: (f(self.result_value), self.accept()))
        if self._close_on_success:
            self.accept()

    def _fail(self, msg, tb):
        self.error = msg
        self.bar.setRange(0, 1)
        self.bar.setValue(0)
        self.add("")
        self.add("FEHLER: " + msg)
        if tb:
            self.add("")
            self.add(tb.rstrip())
        text = _text_of(self._fail_text, msg) or msg
        self._state(False, "Fehlgeschlagen", text)

    def reject(self):
        if self.buttons.button(QDialogButtonBox.StandardButton.Close).isEnabled():
            super().reject()


class Card(QFrame):
    """Kasten mit Überschrift (vom Design als abgerundete Karte gezeichnet).
    ``Card(title)`` wie bisher; optional ``icon=`` (Symbolname) und ``subtitle=``."""

    def __init__(self, title: str, parent=None, *, icon: str | None = None, subtitle: str = ""):
        super().__init__(parent)
        self.setObjectName("Card")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        # Höhe nach Breite: umbrechende Texte und Knopfreihen (responsive.reflow/flow_row) brauchen im schmalen Fenster
        # mehr Höhe, als der sizeHint sagt – mit „Maximum“ wurden sie abgeschnitten
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
    """Seitenkopf: großer Titel, darunter ein Satz in Nebentext-Farbe (Links erlaubt)."""
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
    """Knopf-Variante: "primary" (Hauptaktion), "danger" (löschen/überschreiben), "flat", "secondary"/""."""
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
    """QLabel mit Design-Rolle: "muted", "small", "section", "badge", "cardTitle" …"""
    lab = QLabel(text)
    lab.setWordWrap(wrap)
    if role:
        lab.setProperty("role", role)
    return lab


def badge(text: str, level: str = "") -> QLabel:
    """kleine Pille, level = "" | "success" | "warning" | "error"."""
    b = QLabel(text)
    b.setProperty("role", "badge")
    if level:
        b.setProperty("level", level)
    b.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Maximum)
    return b


def empty_state(icon_name: str, title: str, text: str = "", action_text: str = "", action=None) -> QWidget:
    """leerer Zustand: großes Symbol, Titel, Erklärung ("so geht's …"), optional ein Knopf."""
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(24, 32, 24, 32)
    lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
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
        s.setMaximumWidth(520)
        lay.addWidget(s, 0, Qt.AlignmentFlag.AlignHCenter)
    if action_text and action:
        b = button(action_text, variant="primary", slot=action)
        lay.addWidget(b, 0, Qt.AlignmentFlag.AlignHCenter)
    return w


class Disclosure(QWidget):
    """aufklappbarer Bereich: Kopfzeile mit Pfeil („Details“), darunter ein zunächst verstecktes Widget.
    Disclosure("Details", inner_widget) oder Disclosure("Hintergrund", html_text)."""

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
        self.button.setToolTip("Hintergründe und Einzelheiten ein-/ausblenden")
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
    """Hinweis anzeigen (Toast + Verlauf), aus jedem Thread: notify("Backup fertig", "success",
    action=("Ordner öffnen", fn))."""
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


# ====================================================================== Deutsch für Qt-Standardknöpfe
GERMAN_BUTTONS = {
    "Ok": "OK", "Cancel": "Abbrechen", "Close": "Schließen", "Yes": "Ja", "No": "Nein", "Apply": "Übernehmen",
    "Save": "Speichern", "Discard": "Verwerfen", "Open": "Öffnen", "Reset": "Zurücksetzen", "Help": "Hilfe",
    "Abort": "Abbrechen", "Retry": "Wiederholen", "Ignore": "Ignorieren", "YesToAll": "Ja, alle",
    "NoToAll": "Nein, keine", "RestoreDefaults": "Standardwerte", "SaveAll": "Alle speichern",
}
_ENGLISH = {"OK": "OK", "&OK": "OK", "Cancel": "Abbrechen", "&Cancel": "Abbrechen", "Close": "Schließen",
            "&Close": "Schließen", "Yes": "Ja", "&Yes": "Ja", "No": "Nein", "&No": "Nein", "Apply": "Übernehmen",
            "&Apply": "Übernehmen", "Save": "Speichern", "&Save": "Speichern", "Discard": "Verwerfen",
            "Open": "Öffnen", "&Open": "Öffnen", "Reset": "Zurücksetzen", "Help": "Hilfe", "Abort": "Abbrechen",
            "Retry": "Wiederholen", "Ignore": "Ignorieren", "Restore Defaults": "Standardwerte",
            "Show Details...": "Details anzeigen …", "Hide Details...": "Details ausblenden …"}


def german_buttons(box):
    """Standardknöpfe einer QDialogButtonBox / QMessageBox deutsch beschriften (falls Qt keine deutsche
    Übersetzung geladen hat). Gibt box zurück."""
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
    """Notlösung ohne qtbase_de.qm: beschriftet beim Anzeigen jedes Dialogs die Standardknöpfe."""

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Type.Show and isinstance(obj, QDialog):
            for bb in obj.findChildren(QDialogButtonBox):
                german_buttons(bb)
            if isinstance(obj, QMessageBox):
                german_buttons(obj)
        return False


_translators: list = []


def install_translations(app) -> bool:
    """Qt-eigene Texte (OK/Abbrechen/Schließen, Datei-Dialoge, Kontextmenüs) auf Deutsch.
    Lädt qtbase_de.qm aus dem Qt-Übersetzungsordner (Debian: Paket qt6-translations-l10n);
    fehlt die Datei, beschriftet ein Ereignisfilter die Standardknöpfe selbst. -> True, wenn Qt-Übersetzung geladen."""
    from PyQt6.QtCore import QLibraryInfo, QLocale, QTranslator
    QLocale.setDefault(QLocale(QLocale.Language.German, QLocale.Country.Germany))
    paths = []
    try:
        paths.append(QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath))
    except Exception:  # noqa: BLE001
        pass
    paths += ["/usr/share/qt6/translations", "/usr/share/qt5/translations"]
    loaded = False
    for name in ("qtbase_de", "qt_de"):
        for p in paths:
            tr = QTranslator(app)
            if p and tr.load(name, p):
                app.installTranslator(tr)
                _translators.append(tr)
                loaded = True
                break
        if loaded:
            break
    g = _ButtonGermanizer(app)
    app.installEventFilter(g)
    _translators.append(g)
    return loaded


# ====================================================================== Rückgängig je Gerät
def device_tag(ctx) -> tuple[str, str, str]:
    """(Profil-ID, Port-Schlüssel, Anzeigename) des gerade verbundenen FM-1."""
    prof = getattr(ctx, "profile", None)
    port = getattr(ctx, "port", None)
    name = prof.name if prof else (port.label if port else "einem anderen FM-1")
    return (prof.id if prof else "", port.key if port else "", name)


def same_device(a: tuple, b: tuple) -> bool:
    if a[0] or b[0]:
        return a[0] == b[0]
    return bool(a[1]) and a[1] == b[1]


class DeviceUndo:
    """Rückgängig-Stapel, dessen Einträge wissen, zu welchem FM-1 sie gehören. Rückgängig wird verweigert,
    wenn gerade ein anderes Gerät verbunden ist – sonst landete der alte Inhalt von Gerät A auf Gerät B."""

    def __init__(self):
        self.items: list[tuple[tuple, object, str]] = []   # (device_tag, data, Beschreibung)

    def push(self, ctx, data, what: str = "", tag: tuple | None = None):
        """tag: device_tag(ctx) von VOR dem Auftrag (falls sich das Gerät inzwischen geändert hat)."""
        self.items.append((tag or device_tag(ctx), data, what))

    def __len__(self):
        return len(self.items)

    def __bool__(self):
        return bool(self.items)

    def __getitem__(self, i):
        """Daten des i-ten Eintrags (wie die frühere Liste: undo[-1])."""
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
        return (f"Dieses Rückgängig gehört zu {self.owner()} – bitte das Gerät wieder verbinden. "
                "(Sonst würde der alte Inhalt auf dem falschen FM-1 landen.)")

    def check(self, ctx, parent=None) -> bool:
        """True, wenn der oberste Eintrag zum verbundenen Gerät gehört; sonst Hinweis und False."""
        if not self.items:
            return False
        if self.matches(ctx):
            return True
        if parent is not None:
            show_error(parent, "Rückgängig nicht möglich", self.refusal())
        return False


# ====================================================================== Dateisystem
def fs_error_text(e: BaseException, what: str = "") -> str:
    """verständliche deutsche Meldung zu OSError & Co. (statt "[Errno 13] …")."""
    import errno as _e
    path = getattr(e, "filename", None) or ""
    where = f" ({path})" if path else ""
    code = getattr(e, "errno", None)
    if code in (_e.EACCES, _e.EPERM, _e.EROFS):
        base = "Keine Schreibrechte" + where + " – bitte Rechte des Ordners prüfen oder in den Einstellungen einen anderen Hauptordner wählen."
    elif code == _e.ENOSPC:
        base = "Der Datenträger ist voll" + where + "."
    elif code == _e.ENOENT:
        base = "Datei oder Ordner nicht gefunden" + where + "."
    elif code == _e.EISDIR:
        base = "Das ist ein Ordner, keine Datei" + where + "."
    elif code == _e.ENOTDIR:
        base = "Ein Teil des Pfads ist kein Ordner" + where + "."
    elif isinstance(e, OSError):
        base = f"Dateifehler{where}: {e.strerror or e}"
    else:
        base = str(e) or type(e).__name__
    return (what + ": " if what else "") + base


def safe_folder(name: str) -> tuple[Path, str]:
    """sichtbarer Unterordner (~/FM-1/<name>), ohne Absturz anlegen. -> (Pfad, Fehlertext oder "")."""
    from .. import config
    try:
        return config.folder(name), ""
    except OSError as e:
        try:
            root = Path(config.load().root)
        except Exception:  # noqa: BLE001
            root = Path.home() / "FM-1"
        return root / name, fs_error_text(e, f"Ordner „{name}“ nicht anlegbar")


# ====================================================================== Layout-Helfer
class FlowLayout(QLayout):
    """Knopfreihe, die bei wenig Platz in die nächste Zeile umbricht. addStretch() wird ignoriert
    (Kompatibilität mit QHBoxLayout-Code)."""

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
        """Zeilen füllen; Elemente einer Zeile werden senkrecht mittig ausgerichtet (Häkchen neben Knöpfen)."""
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
    """Widget mit umbrechender Knopfreihe (None-Einträge werden ignoriert)."""
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
    """legt den bisherigen Inhalt einer Seite in einen Rollbereich (die Seite kann dann beliebig
    klein werden). Aufruf am Ende des Konstruktors, nachdem das Layout gefüllt ist."""
    lay = page.layout()
    inner = QWidget()
    inner.setObjectName("PageScrollInner")
    if lay is not None:
        inner.setLayout(lay)              # übernimmt Layout und Kind-Widgets
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
    """Startordner für Datei-Dialoge: ~/FM-1/<name>, falls anlegbar, sonst der persönliche Ordner."""
    p, err = safe_folder(name)
    return p if not err and p.is_dir() else Path.home()


def fw_label(version: str) -> str:
    """Firmware-Kennung fürs Auge: SLOOP meldet sich (wie alle Felucca-Abkömmlinge) als „FELUCCA SLOOP 2.8.3“ –
    angezeigt wird „SLOOP 2.8.3“. Gespeicherte Daten bleiben unverändert."""
    import re as _re
    v = (version or "").strip()
    return _re.sub(r"\bFELUCCA\s+(?=SLOOP\b)", "", v, flags=_re.IGNORECASE)
