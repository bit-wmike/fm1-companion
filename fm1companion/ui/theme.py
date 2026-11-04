# SPDX-License-Identifier: GPL-3.0-only
"""Look of the suite: design tokens, accent color, global stylesheet (light/dark/automatic/system).

Modes (Settings.theme):
  * "auto"   – app design, light or dark like the system (default since 2.0)
  * "light" / "dark" – app design forced (Fusion + own stylesheet)
  * "system" – style and colors of the desktop (KDE/Breeze); only our own building blocks
               (sidebar, cards, headings, notices) get a light stylesheet.

Pages don't need to do anything: heading(), Card(), buttons, tables … are styled globally.
Button variants via the property ``variant`` (primary | secondary | danger | flat),
see ``common.set_variant``. Colors for custom drawings: ``theme.color("accent")``.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPainterPath, QPalette, QPen, QPixmap
from PyQt6.QtWidgets import QApplication, QStyleFactory

from ..i18n import _

ACCENTS = [(_("FM-1 orange"), "#ff7a1a"), (_("Breeze blue"), "#3daee9"), (_("Violet"), "#9b6bff"),
           (_("Green"), "#2ecc71"), (_("Red"), "#ef4f5f"), (_("Yellow"), "#f5b82e"), (_("Turquoise"), "#1abc9c")]
DEFAULT_ACCENT = "#ff7a1a"
MODES = [("auto", _("Automatic (light/dark like the system)")), ("light", _("Light")), ("dark", _("Dark")),
         ("system", _("System style (KDE/Breeze)"))]

_system_palette: QPalette | None = None
_system_style: str = ""
_base_font: QFont | None = None
_current: "Tokens | None" = None
_mode: str = ""


# ------------------------------------------------------------------ Colors ---
def _lin(c: float) -> float:
    c /= 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(col) -> float:
    q = QColor(col)
    return 0.2126 * _lin(q.red()) + 0.7152 * _lin(q.green()) + 0.0722 * _lin(q.blue())


def contrast(a, b) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def mix(a, b, t: float) -> str:
    """blend a towards b (t=0 -> a, t=1 -> b)."""
    qa, qb = QColor(a), QColor(b)
    return QColor(round(qa.red() + (qb.red() - qa.red()) * t), round(qa.green() + (qb.green() - qa.green()) * t),
                  round(qa.blue() + (qb.blue() - qa.blue()) * t)).name()


def _towards(col, target, bg, need: float) -> str:
    """shift col step by step towards target until the contrast to bg is sufficient."""
    c = QColor(col).name()
    for i in range(21):
        cand = mix(col, target, i / 20)
        if contrast(cand, bg) >= need:
            return cand
    return c if contrast(c, bg) >= need else QColor(target).name()


@dataclass(frozen=True)
class Tokens:
    dark: bool
    bg: str            # page background
    panel: str         # sidebar, status bar
    card: str          # cards, menus
    field: str         # input fields, tables
    alt: str           # alternating rows
    border: str        # subtle lines
    field_border: str  # border of input fields (>= 3:1)
    text: str
    muted: str         # secondary text (>= 4.5:1)
    hover: str
    pressed: str
    accent: str        # accent fill (buttons, selection bars)
    on_accent: str     # text on accent (>= 4.5:1)
    accent_fg: str     # accent as text/link on bg (>= 4.5:1)
    accent_soft: str   # selection background
    danger: str
    on_danger: str
    warning: str
    success: str
    info: str
    tooltip: str
    tooltip_text: str
    native: bool = False


def make_tokens(dark: bool, accent: str = DEFAULT_ACCENT) -> Tokens:
    accent = QColor(accent).name() if QColor(accent).isValid() else DEFAULT_ACCENT
    if dark:
        t = dict(bg="#1c1d21", panel="#151619", card="#25272c", field="#1f2125", alt="#23252a",
                 border="#33363c", field_border="#5c6169", text="#eceef1", muted="#a7acb4",
                 hover="#2e3137", pressed="#383b42", danger="#ff6b6b", warning="#f5b82e", success="#3ccf7a",
                 info="#5cb8ff", tooltip="#2f3238", tooltip_text="#eceef1")
    else:
        t = dict(bg="#f3f4f6", panel="#e9ebef", card="#ffffff", field="#ffffff", alt="#f7f8fa",
                 border="#dcdfe4", field_border="#8d939c", text="#1c1e22", muted="#5a616b",
                 hover="#e6e8ec", pressed="#d9dce2", danger="#c92a2a", warning="#b26a00", success="#1e8a4c",
                 info="#1c6fbf", tooltip="#2b2d31", tooltip_text="#f3f4f6")
    bg = t["bg"]
    if dark:
        fill = _towards(accent, "#ffffff", "#000000", 1.0)
        on = "#16171a" if contrast("#16171a", fill) >= contrast("#ffffff", fill) else "#ffffff"
        if contrast(on, fill) < 4.5:
            fill = _towards(fill, "#ffffff" if on != "#ffffff" else "#000000", on, 4.5)
        fg = _towards(accent, "#ffffff", bg, 4.5)
        soft = mix(t["card"], accent, 0.28)
        on_danger = "#16171a"
    else:
        fill = accent
        on = "#ffffff" if contrast("#ffffff", fill) >= contrast("#16171a", fill) else "#16171a"
        if contrast(on, fill) < 4.5:
            fill = _towards(fill, "#000000" if on == "#ffffff" else "#ffffff", on, 4.5)
        fg = _towards(accent, "#000000", bg, 4.5)
        soft = mix("#ffffff", accent, 0.20)
        on_danger = "#ffffff"
    return Tokens(dark=dark, accent=fill, on_accent=on, accent_fg=fg, accent_soft=soft, on_danger=on_danger, **t)


def tokens_from_palette(pal: QPalette, accent: str | None = None) -> Tokens:
    """Tokens for the system style: derived from the desktop palette."""
    win = pal.color(QPalette.ColorRole.Window).name()
    dark = QColor(win).lightness() < 128
    base = make_tokens(dark, accent or pal.color(QPalette.ColorRole.Highlight).name())
    text = pal.color(QPalette.ColorRole.WindowText).name()
    return replace(base, bg=win, panel=mix(win, "#000000" if not dark else "#000000", 0.04 if not dark else 0.18),
                   card=pal.color(QPalette.ColorRole.Base).name(), field=pal.color(QPalette.ColorRole.Base).name(),
                   text=text, muted=_towards(mix(text, win, 0.35), text, win, 4.5),
                   border=mix(win, text, 0.16), hover=mix(win, text, 0.08), pressed=mix(win, text, 0.14),
                   native=True)


def current() -> Tokens:
    global _current
    if _current is None:
        _current = make_tokens(False)
    return _current


def color(name: str) -> QColor:
    """Color of the current design, e.g. color("accent"), color("muted"), color("danger")."""
    return QColor(getattr(current(), name, "#888888"))


def mode() -> str:
    return _mode or "auto"


# ---------------------------------------------------------- Settings ---
def prefs() -> dict:
    from .. import config
    return config.module("rahmen", {"accent": DEFAULT_ACCENT, "icons": "app", "zoom": 1.0, "sidebar": True,
                                    "desktop_notify": "background", "splash": True, "wizard_done": False,
                                    "seen_version": "", "design_migrated": False})


# ------------------------------------------------------------ Graphics ---
def _asset_dir():
    from .. import config
    d = config.CACHE_DIR / "theme"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _draw(name: str, col: str, size: int = 32) -> str:
    """small graphics for the stylesheet (arrows, checkmarks) – with QPainter, without an SVG dependency."""
    key = hashlib.md5(f"{name}{col}{size}v2".encode()).hexdigest()[:10]
    path = _asset_dir() / f"{name}-{key}.png"
    if path.exists():
        return path.as_posix()
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = size / 16
    pen = QPen(QColor(col), 1.9 * s, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    path_ = QPainterPath()
    pts = {"down": [(4, 6), (8, 10), (12, 6)], "up": [(4, 10), (8, 6), (12, 10)],
           "right": [(6, 4), (10, 8), (6, 12)], "left": [(10, 4), (6, 8), (10, 12)],
           "check": [(3.5, 8.5), (6.5, 11.5), (12.5, 4.5)]}.get(name)
    if pts:
        path_.moveTo(QPointF(pts[0][0] * s, pts[0][1] * s))
        for x, y in pts[1:]:
            path_.lineTo(QPointF(x * s, y * s))
        p.drawPath(path_)
    elif name == "dot":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(col))
        p.drawEllipse(QRectF(5 * s, 5 * s, 6 * s, 6 * s))
    elif name == "dash":
        p.drawLine(QPointF(4.5 * s, 8 * s), QPointF(11.5 * s, 8 * s))
    p.end()
    pm.save(str(path))
    return path.as_posix()


# ------------------------------------------------------------ Stylesheet ---
def stylesheet(t: Tokens, pt: float) -> str:
    """full app design (t.native=False) or a light stylesheet for the system style."""
    h1, h2, small = pt * 1.75, pt * 1.15, max(7.0, pt * 0.86)
    common = f"""
QLabel[role="pageTitle"] {{ font-size: {h1:.1f}pt; font-weight: 700; color: {t.text}; }}
QLabel[role="pageSubtitle"] {{ color: {t.muted}; }}
QLabel[role="cardTitle"] {{ font-size: {h2:.1f}pt; font-weight: 600; color: {t.text}; }}
QLabel[role="muted"], QLabel[role="cardSubtitle"] {{ color: {t.muted}; }}
QLabel[role="small"] {{ color: {t.muted}; font-size: {small:.1f}pt; }}
QLabel[role="section"] {{ color: {t.muted}; font-size: {small:.1f}pt; font-weight: 700; letter-spacing: 1px; }}
QLabel[role="badge"] {{ border-radius: 8px; padding: 1px 8px; font-size: {small:.1f}pt; font-weight: 600;
    background: {t.accent_soft}; color: {t.text}; }}
QLabel[role="badge"][level="success"] {{ background: {mix(t.card, t.success, 0.25)}; }}
QLabel[role="badge"][level="warning"] {{ background: {mix(t.card, t.warning, 0.28)}; }}
QLabel[role="badge"][level="error"] {{ background: {mix(t.card, t.danger, 0.28)}; }}
QLabel[role="emptyTitle"] {{ font-size: {h2:.1f}pt; font-weight: 600; color: {t.text}; }}
QFrame#Card, QFrame[card="true"] {{ background: {t.card}; border: 1px solid {t.border}; border-radius: 12px; }}
QFrame#Card QLabel, QFrame[card="true"] QLabel {{ background: transparent; }}
QFrame#Tile {{ background: {t.card}; border: 1px solid {t.border}; border-radius: 12px; }}
QFrame#Tile:hover {{ border: 1px solid {t.accent}; background: {mix(t.card, t.accent, 0.06)}; }}
QFrame#SidebarPanel {{ background: {t.panel}; border: none; border-right: 1px solid {t.border}; }}
QListWidget#Sidebar {{ background: transparent; border: none; outline: 0; }}
QLabel#Brand {{ font-weight: 700; font-size: {pt * 1.08:.1f}pt; color: {t.text}; }}
QLabel#BrandSub {{ color: {t.muted}; font-size: {small:.1f}pt; }}
QFrame#Toast {{ background: {t.card}; border: 1px solid {t.border}; border-radius: 10px; }}
QFrame#Toast[level="success"] {{ border-left: 4px solid {t.success}; }}
QFrame#Toast[level="warning"] {{ border-left: 4px solid {t.warning}; }}
QFrame#Toast[level="error"] {{ border-left: 4px solid {t.danger}; }}
QFrame#Toast[level="info"] {{ border-left: 4px solid {t.accent}; }}
QFrame#Palette {{ background: {t.card}; border: 1px solid {t.field_border}; border-radius: 12px; }}
QLineEdit#PaletteInput {{ border: none; border-bottom: 1px solid {t.border}; border-radius: 0;
    padding: 10px 12px; font-size: {pt * 1.2:.1f}pt; background: transparent; }}
QListWidget#PaletteList {{ border: none; background: transparent; outline: 0; }}
QWidget#PiStatus QLabel {{ color: {t.muted}; font-size: {small:.1f}pt; }}
QToolButton#PiStatusButton {{ border: none; border-radius: 6px; padding: 1px 6px; color: {t.muted}; }}
QToolButton#PiStatusButton:hover {{ background: {t.hover}; }}
QPushButton[variant="primary"] {{ background: {t.accent}; color: {t.on_accent}; border: 1px solid {t.accent};
    border-radius: 6px; padding: 5px 14px; font-weight: 600; }}
QPushButton[variant="primary"]:hover {{ background: {mix(t.accent, t.on_accent, 0.12)}; }}
QPushButton[variant="primary"]:pressed {{ background: {mix(t.accent, t.on_accent, 0.22)}; }}
QPushButton[variant="primary"]:disabled {{ background: {mix(t.bg, t.accent, 0.35)}; color: {mix(t.on_accent, t.bg, 0.4)};
    border-color: transparent; }}
QPushButton[variant="danger"] {{ background: {t.danger}; color: {t.on_danger}; border: 1px solid {t.danger};
    border-radius: 6px; padding: 5px 14px; font-weight: 600; }}
QPushButton[variant="danger"]:hover {{ background: {mix(t.danger, t.on_danger, 0.12)}; }}
QPushButton[variant="danger"]:disabled {{ background: {mix(t.bg, t.danger, 0.35)}; color: {mix(t.on_danger, t.bg, 0.4)}; }}
QPushButton[variant="flat"] {{ background: transparent; border: 1px solid transparent; border-radius: 6px;
    padding: 5px 10px; color: {t.accent_fg}; }}
QPushButton[variant="flat"]:hover {{ background: {t.hover}; }}
"""
    if t.native:
        return common
    down, up, right = _draw("down", t.muted), _draw("up", t.muted), _draw("right", t.muted)
    check, dash, dot = _draw("check", t.on_accent), _draw("dash", t.on_accent), _draw("dot", t.on_accent)
    return common + f"""
QWidget {{ selection-background-color: {t.accent_soft}; selection-color: {t.text}; }}
QMainWindow, QDialog {{ background: {t.bg}; }}
QStackedWidget#PageStack {{ background: {t.bg}; }}
QToolTip {{ background: {t.tooltip}; color: {t.tooltip_text}; border: 1px solid {t.border}; border-radius: 6px; padding: 5px 8px; }}
QMenuBar {{ background: {t.panel}; border-bottom: 1px solid {t.border}; padding: 2px 4px; }}
QMenuBar::item {{ padding: 4px 10px; border-radius: 6px; background: transparent; }}
QMenuBar::item:selected {{ background: {t.hover}; }}
QMenu {{ background: {t.card}; border: 1px solid {t.border}; padding: 6px; }}
QMenu::item {{ padding: 6px 28px 6px 10px; border-radius: 6px; }}
QMenu::item:selected {{ background: {t.accent_soft}; color: {t.text}; }}
QMenu::item:disabled {{ color: {t.muted}; }}
QMenu::separator {{ height: 1px; background: {t.border}; margin: 5px 8px; }}
QMenu::icon {{ padding-left: 8px; }}
QToolBar {{ background: {t.panel}; border: none; border-bottom: 1px solid {t.border}; padding: 4px 8px; spacing: 6px; }}
QToolBar::separator {{ width: 1px; background: {t.border}; margin: 4px 6px; }}
QToolButton {{ border: 1px solid transparent; border-radius: 6px; padding: 4px 8px; background: transparent; }}
QToolButton:hover {{ background: {t.hover}; }}
QToolButton:pressed, QToolButton:checked {{ background: {t.pressed}; }}
QToolButton:focus {{ border: 1px solid {t.accent}; }}
QStatusBar {{ background: {t.panel}; border-top: 1px solid {t.border}; color: {t.muted}; }}
QStatusBar::item {{ border: none; }}
QPushButton {{ background: {t.card}; color: {t.text}; border: 1px solid {t.field_border}; border-radius: 6px;
    padding: 5px 14px; min-height: 18px; }}
QPushButton:hover {{ background: {t.hover}; }}
QPushButton:pressed, QPushButton:checked {{ background: {t.pressed}; }}
QPushButton:disabled {{ color: {mix(t.text, t.bg, 0.55)}; border-color: {t.border}; background: {mix(t.card, t.bg, 0.5)}; }}
QPushButton:focus {{ border: 1px solid {t.accent}; }}
QPushButton:default {{ border: 1px solid {t.accent}; }}
QPushButton::menu-indicator {{ image: url({down}); width: 12px; height: 12px; subcontrol-position: right center; right: 6px; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QDateTimeEdit, QTimeEdit, QDateEdit, QKeySequenceEdit {{
    background: {t.field}; color: {t.text}; border: 1px solid {t.field_border}; border-radius: 6px; padding: 4px 8px;
    min-height: 18px; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QDateTimeEdit:focus {{
    border: 2px solid {t.accent}; padding: 3px 7px; }}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{
    color: {mix(t.text, t.bg, 0.55)}; background: {mix(t.field, t.bg, 0.6)}; border-color: {t.border}; }}
QComboBox {{ padding-right: 26px; }}
QComboBox:focus {{ padding-right: 25px; }}
QComboBox::drop-down {{ border: none; width: 24px; subcontrol-origin: padding; subcontrol-position: center right; }}
QComboBox::down-arrow {{ image: url({down}); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{ background: {t.card}; border: 1px solid {t.border}; padding: 4px; outline: 0;
    selection-background-color: {t.accent_soft}; selection-color: {t.text}; }}
QSpinBox::up-button, QDoubleSpinBox::up-button, QDateTimeEdit::up-button {{ subcontrol-origin: border;
    subcontrol-position: top right; width: 20px; border: none; background: transparent; }}
QSpinBox::down-button, QDoubleSpinBox::down-button, QDateTimeEdit::down-button {{ subcontrol-origin: border;
    subcontrol-position: bottom right; width: 20px; border: none; background: transparent; }}
QSpinBox::up-button:hover, QSpinBox::down-button:hover, QDoubleSpinBox::up-button:hover,
QDoubleSpinBox::down-button:hover {{ background: {t.hover}; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow, QDateTimeEdit::up-arrow {{ image: url({up}); width: 10px; height: 10px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow, QDateTimeEdit::down-arrow {{ image: url({down}); width: 10px; height: 10px; }}
QSpinBox, QDoubleSpinBox {{ padding-left: 8px; padding-right: 22px; }}
QTextEdit, QPlainTextEdit, QTextBrowser {{ background: {t.field}; color: {t.text}; border: 1px solid {t.border};
    border-radius: 8px; padding: 4px; }}
QTextEdit:focus, QPlainTextEdit:focus {{ border: 1px solid {t.accent}; }}
QCheckBox, QRadioButton {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator, QRadioButton::indicator, QTreeView::indicator, QListView::indicator, QTableView::indicator,
QGroupBox::indicator {{ width: 16px; height: 16px; border: 1px solid {t.field_border}; background: {t.field}; }}
QCheckBox::indicator, QTreeView::indicator, QListView::indicator, QTableView::indicator, QGroupBox::indicator {{ border-radius: 4px; }}
QRadioButton::indicator {{ border-radius: 9px; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {t.accent}; }}
QCheckBox::indicator:focus, QRadioButton::indicator:focus {{ border: 2px solid {t.accent}; }}
QCheckBox::indicator:checked, QTreeView::indicator:checked, QListView::indicator:checked, QTableView::indicator:checked,
QGroupBox::indicator:checked {{ background: {t.accent}; border-color: {t.accent}; image: url({check}); }}
QCheckBox::indicator:indeterminate, QTreeView::indicator:indeterminate {{ background: {t.accent}; border-color: {t.accent}; image: url({dash}); }}
QRadioButton::indicator:checked {{ background: {t.accent}; border-color: {t.accent}; image: url({dot}); }}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{ background: {mix(t.field, t.bg, 0.6)}; border-color: {t.border}; }}
QCheckBox:disabled, QRadioButton:disabled {{ color: {mix(t.text, t.bg, 0.55)}; }}
QGroupBox {{ background: {t.card}; border: 1px solid {t.border}; border-radius: 12px; margin-top: 8px;
    padding: 30px 12px 10px 12px; font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: padding; subcontrol-position: top left; left: 14px; top: 8px; padding: 0; color: {t.text}; }}
QGroupBox QLabel, QGroupBox QCheckBox, QGroupBox QRadioButton {{ font-weight: normal; }}
QTabWidget::pane {{ border: 1px solid {t.border}; border-radius: 10px; background: {t.card}; top: -1px; }}
QTabBar {{ qproperty-drawBase: 0; }}
QTabBar::tab {{ background: transparent; color: {t.muted}; padding: 7px 14px; margin-right: 2px;
    border: none; border-bottom: 2px solid transparent; }}
QTabBar::tab:hover {{ color: {t.text}; border-bottom: 2px solid {t.border}; }}
QTabBar::tab:selected {{ color: {t.text}; border-bottom: 2px solid {t.accent}; font-weight: 600; }}
QTabBar::tab:focus {{ color: {t.accent_fg}; }}
QTreeView, QListView, QTableView, QTreeWidget, QListWidget, QTableWidget, QColumnView {{
    background: {t.field}; alternate-background-color: {t.alt}; color: {t.text}; border: 1px solid {t.border};
    border-radius: 8px; outline: 0; gridline-color: {t.border}; }}
/* background needed: without it Qt (6.8+, Breeze) draws the check mark twice – once via ::indicator, once natively */
QTreeView::item, QListView::item, QTableView::item {{ padding: 3px 4px; background: transparent; }}
QTreeView::item:hover, QListView::item:hover, QTableView::item:hover {{ background: {t.hover}; }}
QTreeView::item:selected, QListView::item:selected, QTableView::item:selected {{ background: {t.accent_soft}; color: {t.text}; }}
QTreeView::branch {{ background: transparent; }}
QTreeView::branch:selected {{ background: {t.accent_soft}; }}
QTreeView::branch:has-children:!has-siblings:closed, QTreeView::branch:closed:has-children:has-siblings {{ image: url({right}); }}
QTreeView::branch:open:has-children:!has-siblings, QTreeView::branch:open:has-children:has-siblings {{ image: url({down}); }}
QHeaderView {{ background: transparent; border: none; }}
QHeaderView::section {{ background: {t.card}; color: {t.muted}; padding: 5px 8px; border: none;
    border-bottom: 1px solid {t.border}; border-right: 1px solid {t.border}; font-weight: 600; }}
QHeaderView::section:last {{ border-right: none; }}
QTableCornerButton::section {{ background: {t.card}; border: none; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 11px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {mix(t.bg, t.text, 0.25)}; border-radius: 3px; min-height: 28px; margin: 0 1px; }}
QScrollBar::handle:horizontal {{ background: {mix(t.bg, t.text, 0.25)}; border-radius: 3px; min-width: 28px; margin: 1px 0; }}
QScrollBar::handle:hover {{ background: {mix(t.bg, t.text, 0.42)}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; border: none; background: none; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
QProgressBar {{ background: {mix(t.field, t.text, 0.08)}; border: none; border-radius: 6px; text-align: center;
    color: {t.text}; min-height: 16px; }}
QProgressBar::chunk {{ background: {mix(t.field, t.accent, 0.5)}; border-radius: 6px; }}
QSlider::groove:horizontal {{ height: 4px; background: {mix(t.field, t.text, 0.18)}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {t.accent}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {t.card}; border: 2px solid {t.accent}; width: 12px; height: 12px;
    margin: -6px 0; border-radius: 8px; }}
QSlider::groove:vertical {{ width: 4px; background: {mix(t.field, t.text, 0.18)}; border-radius: 2px; }}
QSlider::add-page:vertical {{ background: {t.accent}; border-radius: 2px; }}
QSlider::handle:vertical {{ background: {t.card}; border: 2px solid {t.accent}; width: 12px; height: 12px;
    margin: 0 -6px; border-radius: 8px; }}
QSplitter::handle {{ background: transparent; }}
QSplitter::handle:hover {{ background: {t.accent_soft}; }}
QDialogButtonBox {{ dialogbuttonbox-buttons-have-icons: 0; }}
QWizard {{ background: {t.bg}; }}
"""


def _palette(t: Tokens) -> QPalette:
    p = QPalette()
    R = QPalette.ColorRole
    for role, col in [(R.Window, t.bg), (R.WindowText, t.text), (R.Base, t.field), (R.AlternateBase, t.alt),
                      (R.Text, t.text), (R.Button, t.card), (R.ButtonText, t.text), (R.Highlight, t.accent_soft),
                      (R.HighlightedText, t.text), (R.ToolTipBase, t.tooltip), (R.ToolTipText, t.tooltip_text),
                      (R.PlaceholderText, t.muted), (R.Link, t.accent_fg), (R.LinkVisited, t.accent_fg),
                      (R.BrightText, "#ffffff"), (R.Light, mix(t.card, "#ffffff", 0.3)), (R.Midlight, t.hover),
                      (R.Mid, t.muted), (R.Dark, t.border), (R.Shadow, "#000000")]:
        p.setColor(role, QColor(col))
    dis = mix(t.text, t.bg, 0.55)
    for role in (R.Text, R.ButtonText, R.WindowText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor(dis))
    try:
        p.setColor(R.Accent, QColor(t.accent))    # Qt >= 6.6
    except AttributeError:
        pass
    return p


def system_is_dark() -> bool:
    pal = _system_palette or QApplication.palette()
    try:
        hints = QGuiApplication.styleHints()
        scheme = hints.colorScheme()          # Qt >= 6.5
        if scheme == Qt.ColorScheme.Dark:
            return True
        if scheme == Qt.ColorScheme.Light:
            return False
    except AttributeError:
        pass
    return pal.color(QPalette.ColorRole.Window).lightness() < 128


def apply(app: QApplication, mode: str, accent: str | None = None, zoom: float | None = None):
    """Apply the design (can be called again at any time, e.g. after the settings)."""
    global _system_palette, _system_style, _current, _mode, _base_font
    if _system_palette is None:
        _system_palette = QPalette(app.palette())
        _system_style = app.style().objectName()
        _base_font = QFont(app.font())
    try:
        pr = prefs()
    except Exception:  # noqa: BLE001 – design must never fail because of saving
        pr = {}
    accent = accent or pr.get("accent") or DEFAULT_ACCENT
    zoom = float(zoom if zoom is not None else pr.get("zoom", 1.0) or 1.0)
    zoom = min(1.6, max(0.75, zoom))
    mode = mode if mode in dict(MODES) else "auto"
    _mode = mode
    f = QFont(_base_font)
    if f.pointSizeF() > 0:
        f.setPointSizeF(f.pointSizeF() * zoom)
    elif f.pixelSize() > 0:
        f.setPixelSize(round(f.pixelSize() * zoom))
    app.setFont(f)
    pt = f.pointSizeF() if f.pointSizeF() > 0 else 10.0
    if mode == "system":
        if _system_style:
            _set_style(app, _system_style)
        app.setPalette(_system_palette)
        _current = tokens_from_palette(_system_palette, accent if pr.get("accent_in_system", True) else None)
    else:
        dark = mode == "dark" or (mode == "auto" and system_is_dark())
        _current = make_tokens(dark, accent)
        _set_style(app, "fusion")
        app.setPalette(_palette(_current))
    app.setStyleSheet(stylesheet(_current, pt))
    try:
        from . import common
        common.icon_cache_clear()
        common.refresh_icon_labels(app)
    except Exception:  # noqa: BLE001
        pass
    for w in app.topLevelWidgets():
        w.update()


_styles: dict = {}


def _set_style(app: QApplication, name: str):
    """Only switch the style if it changes; keep style objects (repeated setStyle with new
    objects crashes Qt when a stylesheet is set)."""
    base = app.style()
    cur = (base.baseStyle() if hasattr(base, "baseStyle") and base.baseStyle() else base).objectName().lower()
    if cur == name.lower() or app.style().objectName().lower() == name.lower():
        return
    st = _styles.get(name.lower())
    if st is None:
        st = QStyleFactory.create(name)
        if st is None:
            return
        _styles[name.lower()] = st
    app.setStyle(st)


def is_dark(app: QApplication | None = None) -> bool:
    if _current is not None:
        return _current.dark
    app = app or QApplication.instance()
    return app.palette().color(QPalette.ColorRole.Window).lightness() < 128
