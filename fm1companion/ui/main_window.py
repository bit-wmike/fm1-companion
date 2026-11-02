# SPDX-License-Identifier: GPL-3.0-only
"""Hauptfenster: Navigation links, Seiten rechts, Geräteauswahl oben."""
from __future__ import annotations

import os

import logging
from pathlib import Path

from PyQt6.QtCore import QByteArray, QRectF, QSize, QTimer, QUrl, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QActionGroup, QColor, QDesktopServices, QFont, QIcon, QKeySequence, QPainter, QPen
from PyQt6.QtWidgets import (QApplication, QBoxLayout, QComboBox, QFrame, QHBoxLayout, QLabel, QListWidget,
                             QListWidgetItem, QMainWindow, QMessageBox, QScrollArea, QSizePolicy, QStackedWidget, QStyle,
                             QStyledItemDelegate, QTabWidget, QToolBar, QToolButton, QVBoxLayout, QWidget)

from .. import APP_NAME, SUITE_NAME, __version__, config, editor_host
from . import responsive, theme
from .notify import HistoryDialog, NotificationCenter
from .statusbar import PiStatus, SystemCheckDialog
from .backups_page import BackupsPage, make_backup
from .common import icon
from .pages import GROUP_TITLES, PageSpec, discover, sort_specs
from .device import DEMO, DeviceContext
from .editor_page import EditorPage
from .firmware_page import FirmwarePage, find_backup_for
from .fm1_page import FM1Page
from .overview import OverviewPage
from .projects_page import ProjectsPage
from .recorder_page import RecorderPage
from .samples_page import SamplesPage
from .settings_dialog import SettingsDialog
from .workshop_page import WorkshopPage

# eingebaute Seiten: (Schlüssel, Titel, Symbole, Gruppe, Reihenfolge, Beschreibung)
PAGES = [("overview", "Übersicht", ("go-home", "user-home"), "geraet", 10,
          "aktives Gerät, Sicherung, Firmware & Updates – und der Zustand des FM-1 (Live-Last, Speicher)"),
         ("editor", "Editor", ("audio-midi", "audio-card", "applications-multimedia"), "geraet", 40,
          "der Original-Web-Editor der installierten Firmware"),
         ("samples", "Samples & Kits", ("audio-x-generic", "folder-sound"), "klang", 30,
          "Sample-Bibliothek, Kits bauen und hochladen"),
         ("projects", "Projekte", ("folder-music", "folder-sound", "folder"), "musik", 10,
          "Projektplätze holen, sammeln, zurückspielen – ansehen und als MIDI exportieren"),
         ("recorder", "Aufnahme", ("media-record", "audio-input-microphone"), "musik", 50,
          "USB-Audio des FM-1 aufnehmen, Audacity"),
         ("backups", "Backups", ("backup", "document-save"), "daten", 10,
          "komplette Sicherungen anlegen, zurückspielen, durchsuchen und vergleichen")]

# Zusammengelegte Seiten: Haupt-Schlüssel -> [(Reiter-Schlüssel, Reiter-Titel, Seiten-Schlüssel der Unterseite)].
# Der erste Reiter ist die Hauptunterseite; die weiteren Seiten-Schlüssel bleiben als Weiterleitung erhalten
# (window.go("fm1") -> Übersicht › Zustand).
COMPOSITES: dict[str, list[tuple[str, str, str]]] = {
    "overview": [("start", "Start", "overview"), ("zustand", "Zustand", "fm1")],
    "projects": [("plaetze", "Plätze & Bibliothek", "projects"), ("ansicht", "Ansicht & Export", "project_view")],
    "compose": [("erzeugen", "Erzeugen", "compose"), ("midi", "Aus MIDI-Datei", "workshop")],
    "backups": [("liste", "Liste", "backups"), ("browser", "Durchsuchen & Vergleichen", "backup_browser")],
    "presets": [("user", "User-Presets am FM-1", "presets"), ("voices", "FM-Voices (DX7)", "voices")],
}
# Seiten, die als weiterer Reiter in das vorhandene Reiter-Widget einer Seite (page.tabs) eingehängt werden
FLAT_COMPOSITES: dict[str, list[tuple[str, str, str]]] = {
    "midi": [("klangerzeuger", "Klangerzeuger", "soundmodule")],
    # Firmware+ (Katalog) und die frühere Seite „Firmware“ sind EINE Seite: Installieren, Versionen wechseln,
    # Update-Modus und Rückweg zum Original liegen im Reiter „Installieren & Versionen“ (window.go("firmware"))
    "discover": [("install", "Installieren & Versionen", "firmware"), ("simulator", "Simulator", "simulator")],
}
# eingehängte Reiter an fester Position statt hinten (Reiter-Index in page.tabs)
FLAT_TAB_POS: dict[str, int] = {"firmware": 1}
# alter Seiten-Schlüssel -> (Hauptseite, Reiter)
ALIASES: dict[str, tuple[str, str]] = {sub: (main, tab) for main, tabs in COMPOSITES.items()
                                       for tab, _t, sub in tabs if sub != main}
ALIASES.update({sub: (main, tab) for main, tabs in FLAT_COMPOSITES.items() for tab, _t, sub in tabs})
# Seitenleiste: breit mit Namen bzw. schmal mit Symbolen; Automatik schaltet unter dieser Fensterbreite um
SIDEBAR_WIDE = 224
SIDEBAR_COMPACT = 60
SIDEBAR_AUTO_BELOW = 1150
SIDEBAR_MODES = ("auto", "wide", "icons")
# feste Reihenfolge für Strg+1 … Strg+9 (die wichtigsten Seiten zuerst)
HOTKEY_ORDER = ["overview", "discover", "editor", "presets", "samples", "projects", "compose", "backups", "help"]


log = logging.getLogger("fm1companion.window")


def needs_maximize(avail_w: int, avail_h: int, want_w: int, want_h: int, frame: int = 40) -> bool:
    """Erststart ohne gespeicherte Fenstergröße: passt das Standardfenster (1280×820 + Titelleiste) nicht auf den
    freien Bildschirm (14"-Laptop 1280×720 abzüglich Leiste), wird maximiert statt über den Rand zu ragen."""
    return want_w > avail_w or want_h + frame > avail_h


def app_icon() -> QIcon:
    """App-Symbol: installiertes hicolor-Symbol, sonst das mitgelieferte PNG."""
    ic = QIcon.fromTheme("fm1-companion")
    if ic.isNull():
        base = Path(__file__).resolve().parents[1] / "icons"
        ic = QIcon(str(base / "fm1-companion.png"))
    return ic


class NavDelegate(QStyledItemDelegate):
    """Seitenleiste: Gruppenüberschriften in Kapitälchen, Auswahl als Pille mit Akzentstrich."""

    def _compact(self) -> bool:
        return bool(getattr(self.parent(), "compact", False))

    def sizeHint(self, option, index):
        sh = super().sizeHint(option, index)
        head = index.data(Qt.ItemDataRole.UserRole) is None
        fm = option.fontMetrics
        if self._compact():                                   # nur Symbole: Gruppen als dünne Trennlinie
            if head:
                return QSize(40, 11 if index.row() > 0 else 4)
            return QSize(40, max(fm.height() + 10, 32))
        # kompakt genug, dass alle Seiten auch bei 1280×720 ohne Rollen in die Leiste passen
        return QSize(sh.width(), (fm.height() + 12) if head and index.row() > 0 else max(fm.height() + 10, 30)
                     if not head else fm.height() + 4)

    def paint(self, p: QPainter, option, index):
        t = theme.current()
        r = option.rect
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        compact = self._compact()
        if index.data(Qt.ItemDataRole.UserRole) is None and compact:   # Gruppe in der schmalen Leiste: Linie
            if index.row() > 0:
                p.setPen(QPen(QColor(t.border), 1))
                y = r.center().y()
                p.drawLine(r.left() + 12, y, r.right() - 12, y)
            p.restore()
            return
        if index.data(Qt.ItemDataRole.UserRole) is None:            # Gruppenüberschrift
            f = QFont(option.font)
            f.setPointSizeF(max(6.5, f.pointSizeF() * 0.78))
            f.setBold(True)
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.0)
            p.setFont(f)
            p.setPen(QColor(t.muted))
            p.drawText(r.adjusted(14, 0, -8, -3), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
                       index.data(Qt.ItemDataRole.DisplayRole))
            p.restore()
            return
        sel = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        pill = QRectF(r.adjusted(6, 2, -6, -2))
        if sel:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(t.accent_soft))
            p.drawRoundedRect(pill, 8, 8)
            p.setBrush(QColor(t.accent))
            p.drawRoundedRect(QRectF(pill.left() + 1, pill.top() + 7, 3, pill.height() - 14), 1.5, 1.5)
        elif hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(t.hover))
            p.drawRoundedRect(pill, 8, 8)
        if not sel and option.state & QStyle.StateFlag.State_HasFocus and option.widget and option.widget.hasFocus():
            p.setPen(QPen(QColor(t.accent), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(pill.adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)
        ic = index.data(Qt.ItemDataRole.DecorationRole)
        x = int(pill.left()) + 12
        if compact:
            x = int(pill.center().x()) - 9 + (2 if sel else 0)
        if isinstance(ic, QIcon) and not ic.isNull():
            s = 18
            ic.paint(p, x, r.top() + (r.height() - s) // 2, s, s)
        if compact:
            p.restore()
            return
        x += 28
        f = QFont(option.font)
        f.setBold(sel)
        p.setFont(f)
        p.setPen(QColor(t.text))
        p.drawText(r.adjusted(x - r.left(), 0, -10, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   index.data(Qt.ItemDataRole.DisplayRole))
        p.restore()


class NavList(QListWidget):
    """Seitenleiste: Pfeiltasten, Bild auf/ab, Pos1 und Ende überspringen die Gruppenüberschriften
    (vorher landete Pos1 auf „GERÄT“ – markiert, aber ohne Seite)."""

    def moveCursor(self, action, modifiers):
        idx = super().moveCursor(action, modifiers)
        A = QListWidget.CursorAction
        step = -1 if action in (A.MoveUp, A.MovePrevious, A.MovePageUp, A.MoveLeft) else 1
        if action == A.MoveEnd:
            step = -1
        row = idx.row() if idx.isValid() else -1
        while 0 <= row < self.count() and not (self.item(row).flags() & Qt.ItemFlag.ItemIsSelectable):
            row += step
        if not 0 <= row < self.count():              # oben über der ersten Überschrift: stehen bleiben
            return self.currentIndex()
        return self.model().index(row, 0)


class MainWindow(QMainWindow):
    save_failed = pyqtSignal(str)              # config.save scheiterte (aus beliebigem Thread)

    def __init__(self, progress=None):
        super().__init__()
        self._progress = progress or (lambda text: None)
        self.commands: list[dict] = []          # zusätzliche Einträge für die Befehlssuche
        self.setWindowTitle(APP_NAME + (" – Demo-Modus – Daten getrennt" if DEMO else ""))
        self.setWindowIcon(app_icon())
        self.resize(1280, 820)
        self.ctx = DeviceContext(self)
        self.ctx.window = self
        _qa = QApplication.instance()
        if _qa is not None:                          # auch ohne Fenster-Schließen (app.quit) geordnet abbauen
            _qa.aboutToQuit.connect(self.shutdown)
        self.ctx.pending_restore_check = ""
        self.server = editor_host.LocalServer()
        self.studio = None                     # der Studio-Zwilling (ui/studio_service.py) – startet erst bei Bedarf
        try:
            from .studio_service import StudioService
            self.studio = StudioService(self)
        except Exception:                      # noqa: BLE001 – ohne Studio läuft der Rest der App weiter
            log.exception("Studio-Dienst nicht verfügbar")

        # Navigation (gruppiert; Seiten aus ui/pages melden sich selbst an)
        self.nav = NavList()
        self.nav.compact = False
        self.nav.setObjectName("Sidebar")
        self.nav.setIconSize(QSize(18, 18))
        self.nav.setItemDelegate(NavDelegate(self.nav))
        self.nav.setMouseTracking(True)
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.stack = QStackedWidget()
        self.stack.setObjectName("PageStack")
        self._progress("Starte die Grundseiten …")
        self.pages: dict[str, QWidget] = {}
        self.specs: dict[str, PageSpec] = {}
        self.overview = OverviewPage(self.ctx)
        self.firmware = FirmwarePage(self.ctx)
        self.editor = EditorPage(self.ctx, self.server)
        self.projects = ProjectsPage(self.ctx)
        self.backups = BackupsPage(self.ctx)
        self.samples = SamplesPage(self.ctx)
        self.recorder = RecorderPage(self.ctx)
        self.workshop = WorkshopPage(self.ctx)
        self.fm1 = FM1Page(self.ctx)
        specs = [PageSpec(key, title, group, (lambda c, w, k=key: getattr(w, k)), icons, order, desc)
                 for key, title, icons, group, order, desc in PAGES]
        builtin = {s.key for s in specs}
        self.absorbed: dict[str, PageSpec] = {    # Seiten, die als Reiter in einer anderen Seite aufgehen
            "fm1": PageSpec("fm1", "FM-1-Zustand", "geraet", (lambda c, w: w.fm1), ("audio-card", "hwinfo"), 20,
                            "Zustand des FM-1: Live-Last, Speicher, User-Presets",
                            ("zustand", "live", "cpu", "speicher", "presets", "diagnose")),
            "workshop": PageSpec("workshop", "Aus MIDI-Datei", "musik", (lambda c, w: w.workshop),
                                 ("tools", "audio-x-midi"), 40, "MIDI-Datei → Pattern auf eine FM-1-Spur",
                                 ("werkstatt", "midi", "pattern", "daw", "musescore", "lmms", "import")),
            # die frühere Seite „Firmware“ – jetzt Reiter von Firmware+ (Schlüssel "firmware" bleibt gültig)
            "firmware": PageSpec("firmware", "Installieren & Versionen", "daten", (lambda c, w: w.firmware),
                                 ("system-software-update", "update-none"), 30,
                                 "Firmware installieren, wechseln, aktualisieren; Versionen herunterladen; "
                                 "zurück zum Original",
                                 ("firmware", "installieren", "update", "aktualisieren", "version", "wechseln",
                                  "herunterladen", "original", "v15", "v16", "fwsc", "hinzufügen", "rückweg")),
        }
        absorbed_keys = set(ALIASES)
        for s in discover():
            if s.key in builtin or s.key in {x.key for x in specs}:
                continue                       # Schlüssel schon vergeben
            if s.key in absorbed_keys:
                self.absorbed[s.key] = s
                continue
            specs.append(s)
        if not any(s.key == "discover" for s in specs):   # Katalog-Modul fehlt/kaputt: Installieren bleibt erreichbar
            specs.append(PageSpec("discover", "Firmware+", "daten", (lambda c, w: w.firmware),
                                  ("system-software-update", "update-none"), 12,
                                  "Firmware installieren, wechseln, aktualisieren"))
        self.subpages: dict[str, QWidget] = {}    # alter Schlüssel -> Unterseite
        self._build_nav(sort_specs(specs))
        self.nav.currentRowChanged.connect(self._page)
        self._progress("Richte das Fenster ein …")
        central = QWidget()
        h = QHBoxLayout(central)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        self.sidebar = self._sidebar_panel()
        h.addWidget(self.sidebar)
        page_box = QWidget()
        pv = QVBoxLayout(page_box)
        pv.setContentsMargins(14, 10, 14, 6)
        pv.setSpacing(6)
        # Hinweise (Toasts) bei kleinem Fenster: eigene Zeile über der Seite – verdeckt dann keine Knöpfe
        self.toast_lane = QWidget()
        self.toast_lane.setObjectName("ToastLane")
        tl = QHBoxLayout(self.toast_lane)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(8)
        tl.addStretch(1)
        self.toast_lane.hide()
        pv.addWidget(self.toast_lane)
        # Seiten in einem Rollbereich: eine breite Seite (Simulator) zwingt das Fenster nicht mehr breit,
        # sie rollt waagrecht; nur die sichtbare Seite zählt für die Mindestgröße (responsive.current_only)
        self.page_scroll = QScrollArea()
        self.page_scroll.setObjectName("PageScroll")
        self.page_scroll.setWidgetResizable(True)
        self.page_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.page_host = responsive.PageHost(self.stack)
        self.page_scroll.setWidget(self.page_host)
        pv.addWidget(self.page_scroll, 1)
        h.addWidget(page_box, 1)
        self.setCentralWidget(central)
        responsive.current_only_all(self.stack)
        responsive.current_only(self.stack)
        self.setMinimumSize(640, 480)                 # darunter wäre nichts mehr bedienbar; Seiten rollen ab hier

        # Werkzeugleiste: Gerät
        tb = QToolBar("Gerät")
        tb.setObjectName("MainToolbar")
        tb.setMovable(False)
        tb.setIconSize(QSize(18, 18))
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toolbar = tb
        self.addToolBar(tb)
        dl = QLabel()
        dl.setPixmap(icon("audio-midi").pixmap(18, 18))
        tb.addWidget(dl)
        self.dev_combo = QComboBox()
        self.dev_combo.setMinimumWidth(320)
        self.dev_combo.activated.connect(self._choose_device)
        tb.addWidget(self.dev_combo)
        self.dev_state = QLabel()
        tb.addWidget(self.dev_state)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)
        self.search_button = QToolButton()
        self.search_button.setText("Suchen …  Strg+K")
        self.search_button.setIcon(icon("system-search"))
        self.search_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.search_button.setToolTip("Befehlssuche: Seiten, Aktionen und Hilfe finden (Strg+K)")
        self.search_button.clicked.connect(self.open_palette)
        tb.addWidget(self.search_button)
        a_bk = QAction(icon("document-save", "backup"), "Backup jetzt", self)
        a_bk.setToolTip("Komplettes Backup des aktiven FM-1 anlegen (Strg+B)")
        a_bk.triggered.connect(self._backup_now)
        tb.addAction(a_bk)
        self.bell = QToolButton()
        self.bell.setIcon(icon("notifications"))
        self.bell.setToolTip("Benachrichtigungen")
        self.bell.clicked.connect(self.show_notifications)
        tb.addWidget(self.bell)

        # Statusleiste: Rechner-Werte, Hinweise
        self.notifications = NotificationCenter(self)
        self.notifications.changed.connect(self._bell)
        self.pi_status = PiStatus(lambda: self.ctx.settings.root, self.open_syscheck, self)
        self.statusBar().addPermanentWidget(self.pi_status)
        self.statusBar().setSizeGripEnabled(False)

        self._menus()
        self.overview.navigate.connect(self.go)
        self.overview.backup_now.connect(self._backup_now)
        self.overview.check_updates.connect(lambda: self.firmware.check_for_device())
        self.firmware.updates_checked.connect(self._updates)
        disc = self.pages.get("discover")         # Firmware+: Liste im Reiter „Installieren & Versionen“ geändert
        if disc is not None and hasattr(disc, "set_catalog"):
            self.firmware.list_changed.connect(lambda: disc.set_catalog(disc.cat))
        if hasattr(self.editor, "navigate"):
            self.editor.navigate.connect(self.go)
        self.ctx.changed.connect(self._dev_changed)
        self.ctx.ports_changed.connect(self._fill_devices)
        # FM-1 angesteckt: nicht zulassen, dass es unbemerkt der Standard-Ausgang des Rechners wird
        self.ctx.ports_changed.connect(self._audio_guard)
        from PyQt6.QtCore import QTimer as _QT
        _QT.singleShot(1500, self._audio_guard)
        _QT.singleShot(2500, self._check_blocking_services)
        self.ctx.message.connect(lambda m: self.statusBar().showMessage(m, 8000))
        if hasattr(self.overview, "attach"):
            self.overview.attach(self)
        self.go("overview")
        self._restore_geometry()
        pr = theme.prefs()
        self.sidebar.setVisible(bool(pr.get("sidebar", True)))
        self.a_sidebar.setChecked(self.sidebar.isVisible())
        self.nav.compact = None                       # erzwingt den ersten Aufbau
        self._update_sidebar()
        self._last_conn = None
        self._checked_for = ""
        self._switching = False
        self._dev_changed()
        self._save_warned = False
        self.save_failed.connect(self._warn_save_failed)
        self._save_hook = self.save_failed.emit
        config.save_listeners.append(self._save_hook)
        if config.save_error:                         # schon beim Start (vor dem Fenster) gescheitert
            QTimer.singleShot(1400, lambda: self._warn_save_failed(config.save_error))
        cfg_msg = config.problems_text()
        if cfg_msg:                                   # einmalig: kaputte/ungültige Einstellungen
            self.config_notice = cfg_msg
            QTimer.singleShot(1200, lambda: self.notify(cfg_msg, "warning"))
        self.missing_deps: list[str] = []
        QTimer.singleShot(2500, self._check_required_deps)

    def _warn_save_failed(self, text: str):
        """Einstellungen ließen sich nicht speichern: einmal pro Sitzung sagen (nicht bei jedem Klick)."""
        if self._save_warned or not text:
            return
        self._save_warned = True
        self.notify(text, "warning")

    def _check_required_deps(self):
        """fehlende Pflichtpakete (QtWebEngine, rtmidi …) einmal beim Start melden – mit dem apt-Befehl."""
        from .. import diag
        from .common import run_job

        def done(states):
            miss = [s for s in states if s.required and not s.ok]
            if not miss:
                return
            self.missing_deps = [s.package for s in miss]
            what = ", ".join(f"{s.package} ({s.why})" if s.why else s.package for s in miss)
            self.notify(f"Es fehlt etwas: {what}. Die App läuft, aber nicht alle Seiten. Nachinstallieren mit: "
                        f"{diag.apt_command(miss, include_optional=False)}", "warning",
                        ("Diagnose öffnen", lambda: self.open_help("diagnose")))
        # Qt-Module nie erstmals in einem Hintergrund-Thread laden (Qt Multimedia zieht dabei PipeWire/FFmpeg-
        # Plugins nach; parallel zum GUI-Thread führte das gelegentlich zum Absturz): vorher hier im GUI-Thread
        import importlib
        for d in diag.deps().get("required", []) + diag.deps().get("recommended", []):
            mod = d.get("check", {}).get("module", "")
            if mod.startswith("PyQt6."):
                try:
                    importlib.import_module(mod)
                except Exception:  # noqa: BLE001 – fehlt: meldet check_deps
                    pass
        run_job(self, lambda w: diag.check_deps(), on_done=done, on_fail=lambda m, tb: None)

    # ------------------------------------------------------------ Menüs
    def _act(self, menu, text, slot, shortcut=None, icon_names=(), tip="", checkable=False):
        a = QAction(icon(*icon_names) if icon_names else QIcon(), text, self)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut) if isinstance(shortcut, str) else shortcut)
            a.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        if tip:
            a.setStatusTip(tip)
        a.setCheckable(checkable)
        a.triggered.connect(slot)
        menu.addAction(a)
        return a

    def _menus(self):
        mb = self.menuBar()
        m = mb.addMenu("&Datei")
        # fest Strg+, (wie in der Hilfe): StandardKey.Preferences ist unter KDE Strg+Umschalt+, und sonst leer
        self._act(m, "Einstellungen …", self._settings, "Ctrl+,",
                  ("configure", "preferences-system"))
        self._act(m, "Hauptordner öffnen", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.ctx.settings.root))),
                  None, ("folder-open",))
        m.addSeparator()
        self._act(m, "Beenden", self.close, QKeySequence.StandardKey.Quit, ("application-exit",))

        g = mb.addMenu("&Gerät")
        self._act(g, "Backup jetzt", self._backup_now, "Ctrl+B", ("document-save",))
        self._act(g, "Neu verbinden", self._reconnect, "F5", ("view-refresh",))
        self._act(g, "Gerät umbenennen …", lambda: self.overview._rename() if self.ctx.profile else None, None,
                  ("edit-rename",))
        self._act(g, "Zustand des FM-1", lambda: self.go("fm1"), None, ("audio-card",))

        v = mb.addMenu("&Ansicht")
        self.a_sidebar = self._act(v, "Seitenleiste", self.toggle_sidebar, "F9", ("view-sidebar",), checkable=True)
        sm = v.addMenu(icon("view-sidebar"), "Seitenleiste: Breite")
        sgrp = QActionGroup(self)
        self.sidebar_mode_actions = {}
        for key, title in (("auto", "Automatisch (nach Fensterbreite)"), ("wide", "Breit, mit Namen"),
                           ("icons", "Schmal, nur Symbole")):
            a = self._act(sm, title, lambda _=False, k=key: self.set_sidebar_mode(k), checkable=True)
            sgrp.addAction(a)
            a.setChecked(self.sidebar_mode() == key)
            self.sidebar_mode_actions[key] = a
        self.a_full = self._act(v, "Vollbild", self.toggle_fullscreen, "F11", ("view-fullscreen",), checkable=True)
        v.addSeparator()
        self._act(v, "Vergrößern", lambda: self.zoom(+0.1), QKeySequence.StandardKey.ZoomIn, ("zoom-in",))
        self._act(v, "Verkleinern", lambda: self.zoom(-0.1), QKeySequence.StandardKey.ZoomOut, ("zoom-out",))
        self._act(v, "Normalgröße", lambda: self.zoom(0), "Ctrl+0", ("zoom-original",))
        dm = v.addMenu(icon("preferences-desktop-theme"), "Design")
        grp = QActionGroup(self)
        self.theme_actions = {}
        for key, title in theme.MODES:
            a = self._act(dm, title, lambda _=False, k=key: self.set_theme(k), checkable=True)
            grp.addAction(a)
            a.setChecked(self.ctx.settings.theme == key)
            self.theme_actions[key] = a
        v.addSeparator()
        self._act(v, "Benachrichtigungen …", self.show_notifications, None, ("notifications",))

        w = mb.addMenu("&Werkzeuge")
        self._act(w, "Befehlssuche …", self.open_palette, "Ctrl+K", ("system-search",),
                  "Seiten, Aktionen und Hilfe finden")
        self._act(w, "Systemcheck …", self.open_syscheck, None, ("utilities-system-monitor",),
                  "USB, Strom und Audio auf Probleme prüfen")

        hm = mb.addMenu("&Hilfe")
        self._act(hm, "Hilfe-Center", lambda: self.open_help(), "F1", ("help-contents",))
        self._act(hm, "Tastenkürzel", self.show_shortcuts, "Ctrl+/", ("help-keyboard-shortcuts",))
        hm.addSeparator()
        self._act(hm, "Einrichtungs-Assistent …", self.open_wizard, None, ("tools-wizard",))
        self._act(hm, "Was ist neu in 2.0", self.open_whats_new, None, ("help-hint",))
        hm.addSeparator()
        self._act(hm, "Fehlerbericht erstellen …", self.save_report, None, ("tools-report-bug",))
        self._act(hm, "Diagnose", lambda: self.open_help("diagnose"), None, ("hwinfo",))
        hm.addSeparator()
        self._act(hm, f"Über {SUITE_NAME}", self._about, None, ("help-about",))

        self.hotkeys: list[tuple[str, str]] = []     # (Tastenkürzel, Seitentitel) – für den Kürzel-Dialog
        keys = [k for k in HOTKEY_ORDER if k in self.specs] + [k for k in self.specs if k not in HOTKEY_ORDER]
        for i, key in enumerate(keys[:9]):
            act = QAction(self.specs[key].title.replace("&", "&&"), self)
            act.setShortcut(f"Ctrl+{i + 1}")
            act.triggered.connect(lambda _=False, k=key: self.go(k))
            self.addAction(act)
            self.hotkeys.append((f"Strg+{i + 1}", self.specs[key].title))

    def _sidebar_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("SidebarPanel")
        panel.setFixedWidth(SIDEBAR_WIDE)
        v = QVBoxLayout(panel)
        v.setContentsMargins(6, 8, 6, 4)
        v.setSpacing(4)
        brand = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        brand.setContentsMargins(10, 0, 0, 2)
        self.brand_box = brand
        logo = QLabel()
        logo.setPixmap(self.windowIcon().pixmap(30, 30))
        logo.setToolTip(f"{SUITE_NAME} {__version__}" + (" – Demo-Modus" if DEMO else ""))
        brand.addWidget(logo)
        tv = QVBoxLayout()
        tv.setSpacing(0)
        t1 = QLabel("FM-1 Companion")
        t1.setObjectName("Brand")
        t2 = QLabel(f"Suite {__version__}" + ("  ·  Demo" if DEMO else ""))
        t2.setObjectName("BrandSub")
        tv.addWidget(t1)
        tv.addWidget(t2)
        self.brand_texts = (t1, t2)
        brand.addLayout(tv, 1)
        # Umschalter breit/schmal oben in der Markenzeile (unten kostete er Höhe – bei 720 px rollte die Leiste)
        self.sidebar_button = QToolButton()
        self.sidebar_button.setObjectName("SidebarToggle")
        self.sidebar_button.setAutoRaise(True)
        self.sidebar_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.sidebar_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)   # per Tastatur: Ansicht › Seitenleiste: Breite
        self.sidebar_button.clicked.connect(self._toggle_compact)
        brand.addWidget(self.sidebar_button, 0, Qt.AlignmentFlag.AlignTop)
        v.addLayout(brand)
        v.addWidget(self.nav, 1)
        return panel

    # ------------------------------------------------------------ Seitenleiste: breit / nur Symbole
    def sidebar_mode(self) -> str:
        """„auto“ (nach Fensterbreite), „wide“ oder „icons“ – manuell Gewähltes hat Vorrang vor der Automatik."""
        m = theme.prefs().get("sidebar_mode", "auto")
        return m if m in SIDEBAR_MODES else "auto"

    def set_sidebar_mode(self, mode: str):
        if mode not in SIDEBAR_MODES:
            mode = "auto"
        theme.prefs()["sidebar_mode"] = mode
        config.save(self.ctx.settings)
        for k, a in getattr(self, "sidebar_mode_actions", {}).items():
            a.setChecked(k == mode)
        self._update_sidebar()

    def _toggle_compact(self):
        self.set_sidebar_mode("wide" if self.nav.compact else "icons")

    def _auto_compact(self) -> bool:
        """Automatik mit Hysterese: unter SIDEBAR_AUTO_BELOW schmal, ab +32 px wieder breit."""
        w = self.width()
        cur = getattr(self, "_auto_state", None)
        if cur is None or (not cur and w < SIDEBAR_AUTO_BELOW) or (cur and w >= SIDEBAR_AUTO_BELOW + 32):
            self._auto_state = w < SIDEBAR_AUTO_BELOW
        return self._auto_state

    def _update_sidebar(self):
        mode = self.sidebar_mode()
        auto = self._auto_compact()
        self.set_sidebar_compact(auto if mode == "auto" else mode == "icons")

    def set_sidebar_compact(self, on: bool):
        """Seitenleiste nur mit Symbolen (Namen als Tooltip, Gruppen als Trennlinie) bzw. wieder breit."""
        on = bool(on)
        if getattr(self.nav, "compact", None) == on:
            return
        self.nav.compact = on
        self.sidebar.setFixedWidth(SIDEBAR_COMPACT if on else SIDEBAR_WIDE)
        for lab in self.brand_texts:
            lab.setVisible(not on)
        self.brand_box.setDirection(QBoxLayout.Direction.TopToBottom if on else QBoxLayout.Direction.LeftToRight)
        self.brand_box.setContentsMargins(9 if on else 10, 0, 0, 2)
        for i in range(self.nav.count()):
            it = self.nav.item(i)
            key = it.data(Qt.ItemDataRole.UserRole)
            if key is None:
                continue
            spec = self.specs.get(key)
            desc = spec.description if spec else ""
            it.setToolTip(f"{spec.title if spec else it.text()}" + (f" – {desc}" if desc else "") if on else desc)
        self.sidebar_button.setIcon(icon("sidebar-expand-left", "panel-left-open") if on
                                    else icon("sidebar-collapse-left", "panel-left-close"))
        self.sidebar_button.setToolTip("Seitenleiste mit Namen zeigen" if on
                                       else "Seitenleiste schmal: nur Symbole (mehr Platz für die Seite)")
        self.nav.doItemsLayout()
        self.nav.viewport().update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, "nav") and hasattr(self, "brand_texts"):
            self._update_sidebar()
            self._update_toolbar()

    def _update_toolbar(self):
        """schmales Fenster: Werkzeugleiste kompakter (Suche und Backup nur als Symbol, kürzerer Gerätezustand),
        damit „Backup jetzt“ und die Glocke nicht im Überlauf-Menü verschwinden."""
        compact = bool(getattr(self, "_auto_state", False))
        if getattr(self, "_toolbar_compact", None) == compact:
            return
        self._toolbar_compact = compact
        icon_only = Qt.ToolButtonStyle.ToolButtonIconOnly
        beside = Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        self.toolbar.setToolButtonStyle(icon_only if compact else beside)
        self.search_button.setToolButtonStyle(icon_only if compact else beside)
        self.dev_combo.setMinimumWidth(260 if compact else 320)
        self._apply_dev_text()

    def _apply_dev_text(self):
        texts = getattr(self, "_dev_texts", None)
        if texts:
            self.dev_state.setText(texts[1] if getattr(self, "_toolbar_compact", False) else texts[0])

    # ------------------------------------------------------------ Seiten
    def _build_nav(self, specs: list[PageSpec]):
        group = None
        for s in specs:
            self._progress(f"Lade {s.title} …")
            try:
                page = self._make_page(s)
            except Exception:                  # noqa: BLE001 – eine kaputte Seite darf die App nicht stoppen
                import traceback
                from .pages import load_errors
                load_errors.append((s.key, traceback.format_exc()))
                log.error("Seite %s konnte nicht geladen werden:\n%s", s.key, load_errors[-1][1])
                if s.key != "discover":
                    continue
                page = self.firmware               # Katalog kaputt: „Installieren & Versionen“ trotzdem anbieten
                self.subpages["firmware"] = page
            if s.group != group:
                group = s.group
                head = QListWidgetItem(GROUP_TITLES.get(group, group).upper())
                head.setFlags(Qt.ItemFlag.NoItemFlags)
                head.setData(Qt.ItemDataRole.UserRole, None)
                self.nav.addItem(head)
            self.pages[s.key] = page
            self.specs[s.key] = s
            self.stack.addWidget(page)
            it = QListWidgetItem(icon(*s.icons) if s.icons else QIcon(), s.title)
            it.setData(Qt.ItemDataRole.UserRole, s.key)
            if s.description:
                it.setToolTip(s.description)
            self.nav.addItem(it)
            if hasattr(page, "navigate"):
                try:
                    page.navigate.connect(self.go)
                except (TypeError, AttributeError):
                    pass

    def _make_page(self, s: PageSpec):
        """Seite bauen – zusammengelegte Seiten (COMPOSITES) als TabbedPage mit ihren Unterseiten."""
        tabs = COMPOSITES.get(s.key)
        flat = FLAT_COMPOSITES.get(s.key)
        if not tabs and not flat:
            return s.factory(self.ctx, self)
        from .tabbed import TabAdopter, TabbedPage
        if flat:
            main = s.factory(self.ctx, self)
            self.subpages[s.key] = main
            parts = []
            for tab_key, tab_title, sub_key in flat:
                if sub_key in self.absorbed:
                    sub = self.absorbed[sub_key].factory(self.ctx, self)
                    self.subpages[sub_key] = sub
                    parts.append((tab_key, tab_title, sub))
            if parts and isinstance(getattr(main, "tabs", None), QTabWidget):
                TabAdopter(main, parts)
                tw = main.tabs                      # Reiter-Symbole: wie die eigenen Reiter der Hauptseite
                for sub_key, pos in FLAT_TAB_POS.items():
                    sub = self.subpages.get(sub_key)
                    i = tw.indexOf(sub) if sub is not None else -1
                    if i >= 0 and 0 <= pos < tw.count() and i != pos:
                        tw.tabBar().moveTab(i, pos)
                if any(not tw.tabIcon(i).isNull() for i in range(tw.count() - len(parts))):
                    for tab_key, tab_title, sub in parts:
                        spec = next((x for x in self.absorbed.values() if self.subpages.get(x.key) is sub), None)
                        if spec is not None and spec.icons:
                            tw.setTabIcon(tw.indexOf(sub), icon(*spec.icons))
            return main
        parts = []
        for tab_key, tab_title, sub_key in tabs:
            if sub_key == s.key:
                sub = s.factory(self.ctx, self)
            elif sub_key in self.absorbed:
                sub = self.absorbed[sub_key].factory(self.ctx, self)
            elif hasattr(self, sub_key):
                sub = getattr(self, sub_key)
            else:
                continue
            self.subpages[sub_key] = sub
            if sub_key == s.key:
                self.subpages[s.key] = sub
            parts.append((tab_key, tab_title, sub))
        return TabbedPage(s.title, s.description, parts)

    def page(self, key: str):
        """Seite zu einem Schlüssel – auch für alte Schlüssel, die jetzt Reiter einer anderen Seite sind
        (page("backup_browser") ist die Unterseite „Durchsuchen & Vergleichen“ von „Backups“)."""
        if key in ALIASES:
            return self.subpages.get(key)
        return self.pages.get(key)

    def resolve(self, key: str) -> tuple[str, str]:
        """(Navigations-Schlüssel, Reiter-Schlüssel oder "") zu einem – ggf. alten – Seiten-Schlüssel."""
        if key in ALIASES:
            return ALIASES[key]
        if key in COMPOSITES:
            return key, COMPOSITES[key][0][0]
        return key, ""

    def current_key(self) -> str:
        cur = self.stack.currentWidget()
        return next((k for k, p in self.pages.items() if p is cur), "")

    def go(self, key: str, **kwargs):
        """Seite öffnen; kwargs gehen an page.open_with(**kwargs), falls vorhanden.
        Alte Schlüssel (fm1, workshop, project_view, backup_browser) leiten auf die neue Seite und den
        passenden Reiter weiter; ``tab=`` wählt einen Reiter einer zusammengelegten Seite direkt."""
        nav_key, tab = self.resolve(key)
        tab = kwargs.pop("tab", None) or tab
        for i in range(self.nav.count()):
            if self.nav.item(i).data(Qt.ItemDataRole.UserRole) == nav_key:
                self.nav.setCurrentRow(i)
                break
        page = self.pages.get(nav_key)
        if page is None:
            return
        if tab and hasattr(page, "set_tab"):
            page.set_tab(tab)
        if kwargs and hasattr(page, "open_with"):
            page.open_with(**kwargs)

    def _page(self, row: int):
        if row < 0:
            return
        key = self.nav.item(row).data(Qt.ItemDataRole.UserRole)
        if key is None:                        # Gruppenüberschrift
            return
        prev = self.stack.currentWidget()
        page = self.pages[key]
        if prev is page:
            return
        if hasattr(prev, "deactivate"):
            prev.deactivate()
        self.stack.setCurrentWidget(page)
        if hasattr(page, "activate"):
            page.activate()

    # ------------------------------------------------------------ Gerät
    def _fill_devices(self):
        self.dev_combo.blockSignals(True)
        self.dev_combo.clear()
        if not self.ctx.ports:
            self.dev_combo.addItem("kein FM-1 angeschlossen", None)
        for p in self.ctx.ports:
            prof = self.ctx.profile_for_port(p)
            name = prof.name if prof else "neues FM-1"
            self.dev_combo.addItem(icon("audio-midi", "media-flash"), f"{name}  ·  {p.label}"
                                   + ("  (Update-Modus)" if p.update_mode else ""), p.key)
        vports = list(getattr(self.ctx, "vports", []) or [])
        if vports:
            self.dev_combo.insertSeparator(self.dev_combo.count())
        for p in vports:                       # der Studio-Zwilling: klar als virtuell gekennzeichnet
            self.dev_combo.addItem(icon("computer", "audio-card"), f"{p.label}  ·  virtuell (kein echtes FM-1)",
                                   p.key)
            self.dev_combo.setItemData(self.dev_combo.count() - 1,
                                       "Der Zwilling im Studio: Projekte, Komponieren, Presets, Backups und Editor "
                                       "arbeiten dann mit ihm statt mit einem echten FM-1. Firmware, Rettung und "
                                       "USB-Audio gibt es für ihn nicht.", Qt.ItemDataRole.ToolTipRole)
        if self.ctx.port:
            self.dev_combo.setCurrentIndex(max(0, self.dev_combo.findData(self.ctx.port.key)))
        self.dev_combo.blockSignals(False)

    def _choose_device(self, i):
        key = self.dev_combo.itemData(i)
        port = next((p for p in list(self.ctx.ports) + list(getattr(self.ctx, "vports", []) or [])
                     if p.key == key), None)
        if port and (not self.ctx.port or port.key != self.ctx.port.key):
            self._switching = True             # bewusster Wechsel: kein „getrennt“-Warnhinweis, nur „Aktiv: …“
            QTimer.singleShot(8000, lambda: setattr(self, "_switching", False))
            self.ctx.connect_port(port)

    def _reconnect(self):
        port = self.ctx.port
        if port:
            self.statusBar().showMessage(f"Verbinde neu mit „{port.label}“ …", 6000)
            self.ctx.connect_port(port)
        else:
            self.ctx.scan()
            self.statusBar().showMessage("Neu gesucht: " + (f"{len(self.ctx.ports)} FM-1 gefunden." if self.ctx.ports
                                         else "kein FM-1 gefunden – USB-Datenkabel prüfen."), 8000)

    def _check_blocking_services(self):
        """Debians fluidsynth-Dienst belegt die HDMI-Karte (kein Ton mehr über PipeWire): anbieten, ihn abzuschalten."""
        from .. import audiofix as AF
        from .common import run_job

        def done(state):
            enabled, active = state
            if not (enabled or active):
                return

            def fix():
                def ok(res):
                    self.notify(res[1], "success" if res[0] else "warning")
                run_job(self, lambda w: AF.disable_fluidsynth_service(), on_done=ok,
                        on_fail=lambda m, tb: self.notify(f"Abschalten ging nicht: {m}", "warning"))
            self.notify("Der FluidSynth-Dienst des Systems startet bei jeder Anmeldung und belegt dabei die erste "
                        "Soundkarte (am Pi: HDMI) – dann fehlt dein Lautsprecher-/HDMI-Ausgang. Die App braucht den "
                        "Dienst nicht (der Klangerzeuger startet FluidSynth selbst).", "warning",
                        ("Dienst abschalten", fix))
        run_job(self, lambda w: AF.fluidsynth_service(), on_done=done, on_fail=lambda m, tb: None)

    def _audio_guard(self):
        if getattr(self, "_shut_down", False) or not (self.ctx.ports or []):
            return
        try:
            from .audio import guard_default_output
            guard_default_output()
        except Exception:  # noqa: BLE001 – Komfort, darf nie stören
            pass

    def _dev_changed(self):
        if getattr(self, "_shut_down", False):   # Beenden trennt das Gerät – das ist kein „FM-1 getrennt“-Hinweis
            return
        self._fill_devices()
        c = self.ctx
        col = c.profile.color if c.profile else "#888"
        if getattr(c, "protocol", "") and hasattr(c, "status_text"):
            state = c.status_text()
        else:
            state = "verbunden" if c.connected else ("antwortet nicht" if c.port else "nicht verbunden")
        import html
        # ohne Gerät nur „Kein FM-1 verbunden“ (vorher doppelt: „Kein FM-1 verbunden – nicht verbunden“)
        text = html.escape(c.title()) + (f" – {html.escape(state)}" if c.port else "")
        self._dev_texts = (f"  <b style='color:{col}'>●</b> {text}  ",
                           # schmales Fenster: nur der Zustand – Name und Firmware stehen schon in der Auswahl
                           f"  <b style='color:{col}'>●</b> {html.escape(state) if c.port else text}  ")
        self.dev_state.setToolTip(c.title() + (f" – {state}" if c.port else ""))
        self._apply_dev_text()
        conn = (c.profile.id if c.profile else "", c.connected)
        switching = getattr(self, "_switching", False)
        if c.connected and conn != self._last_conn and c.profile:
            from .common import fw_label
            if switching:
                self._switching = False
                self.notifications.post(f"Aktiv: {c.profile.name} – {fw_label(c.info.version) if c.info else ''}",
                                        "info", quiet=True)
            else:
                self.notify(f"{c.profile.name} verbunden – {fw_label(c.info.version) if c.info else ''}", "success")
        elif c.port and switching and getattr(c, "protocol", "") == "generic" and c.profile and conn != self._last_conn:
            self._switching = False
            self.notifications.post(f"Aktiv: {c.profile.name} – {c.title().split(' – ', 1)[-1]}", "info", quiet=True)
        elif self._last_conn and self._last_conn[1] and not c.port and not switching:
            self.notify("FM-1 getrennt", "warning")
        self._last_conn = conn
        if c.connected and not getattr(c, "is_virtual", False):
            key = (c.profile.id if c.profile else "") + c.firmware_key
            if c.settings.check_on_start and key and key != self._checked_for and not os.environ.get("FM1C_NO_NET"):
                self._checked_for = key
                QTimer.singleShot(500, self.firmware.check_for_device)
            if c.pending_restore_check and c.firmware_key == c.pending_restore_check:
                k = c.pending_restore_check
                c.pending_restore_check = ""
                QTimer.singleShot(800, lambda: self._offer_restore(k))

    def _updates(self, info: dict):
        self.overview.set_update_info(info)
        new, cur = info.get("latest"), info.get("current")
        if new and new != cur:
            self.statusBar().showMessage(f"Neue Firmware verfügbar: {new} (installiert: {cur or 'unbekannt'})", 15000)
            self.notify(f"Neue Firmware verfügbar: {new} (installiert: {cur or 'unbekannt'})", "info",
                        ("Ansehen", lambda: self.go("firmware")))

    def _offer_restore(self, key):
        f = find_backup_for(self.ctx, key)
        if not f:
            return
        box = QMessageBox(QMessageBox.Icon.Question, "Zurück zu deinen Daten?",
                          f"Für diese Firmware gibt es ein Backup von diesem Gerät:\n{f.name}\n\n"
                          "Jetzt zurückspielen? (Projekte, Presets und Einstellungen von damals.)", parent=self)
        yes = box.addButton("Zu den Backups", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Nein, danke", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is yes:
            self.go("backups")

    def _backup_now(self):
        path = make_backup(self.ctx, self)
        if path:
            self.backups.refresh()
            self.overview.refresh()
            self.notify(f"Backup gespeichert: {Path(path).name}", "success",
                        ("Ordner öffnen", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).parent)))))

    # ------------------------------------------------------------ Sonstiges
    def _settings(self):
        d = SettingsDialog(self.ctx, self)
        if d.exec():
            self.apply_theme()
            self.backups.refresh()
            self.projects.refresh_library()
            self.samples.refresh_filters()
            self.recorder.refresh_list()
            self.overview.refresh()

    # ------------------------------------------------------------ Design / Ansicht
    def apply_theme(self):
        theme.apply(QApplication.instance(), self.ctx.settings.theme)
        for k, a in getattr(self, "theme_actions", {}).items():
            a.setChecked(k == self.ctx.settings.theme)
        self.nav.viewport().update()

    def set_theme(self, mode: str):
        self.ctx.settings.theme = mode
        config.save(self.ctx.settings)
        self.apply_theme()

    def zoom(self, delta: float):
        pr = theme.prefs()
        z = 1.0 if delta == 0 else round(min(1.6, max(0.8, float(pr.get("zoom", 1.0)) + delta)), 2)
        pr["zoom"] = z
        config.save(self.ctx.settings)
        self.apply_theme()
        self.statusBar().showMessage(f"Zoom {round(z * 100)} %", 2500)

    def toggle_sidebar(self, on=None):
        on = (not self.sidebar.isVisible()) if on is None or not isinstance(on, bool) else on
        self.sidebar.setVisible(on)
        self.a_sidebar.setChecked(on)
        theme.prefs()["sidebar"] = on
        config.save(self.ctx.settings)

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()
        self.a_full.setChecked(self.isFullScreen())

    # ------------------------------------------------------------ Hinweise
    def notify(self, text: str, level: str = "info", action=None):
        """Toast + Verlauf (+ Desktop-Benachrichtigung, wenn das Fenster im Hintergrund ist)."""
        return self.notifications.post(text, level, action)

    def _bell(self):
        n = self.notifications.unread()
        self.bell.setText(str(n) if n else "")
        self.bell.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon if n
                                     else Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.bell.setToolTip(f"Benachrichtigungen ({n} neu)" if n else "Benachrichtigungen")

    def show_notifications(self):
        HistoryDialog(self.notifications, self).exec()

    # ------------------------------------------------------------ Befehle, Hilfe, Assistenten
    def register_command(self, title: str, callback, *, subtitle: str = "", keywords: tuple = (), icon_name: str = ""):
        """eigenen Eintrag in die Befehlssuche aufnehmen (z. B. aus einer Seite)."""
        self.commands.append({"title": title, "callback": callback, "subtitle": subtitle,
                              "keywords": tuple(keywords), "icon": icon_name})

    def open_palette(self):
        from .palette import CommandPalette
        self._palette = CommandPalette(self)
        self._palette.popup()
        return self._palette

    def open_help(self, topic: str = ""):
        if self.page("help") is not None:
            self.go("help", topic=topic) if topic else self.go("help")
        else:
            QDesktopServices.openUrl(QUrl("https://github.com/cicloid/awesome-fm-1"))

    def show_shortcuts(self):
        from .about import ShortcutsDialog
        ShortcutsDialog(self).exec()

    def open_syscheck(self):
        self._syscheck = SystemCheckDialog(self.ctx, self)
        self._syscheck.show()
        return self._syscheck

    def open_wizard(self):
        from .wizard import FirstRunWizard
        wz = FirstRunWizard(self.ctx, self)
        ok = wz.exec()
        if ok:
            self.apply_theme()
            self.overview.refresh()
        return wz

    def open_whats_new(self):
        from .wizard import WhatsNewDialog
        d = WhatsNewDialog(self)
        d.exec()
        return d

    def maybe_first_run(self):
        """beim echten Start (nicht in Tests): Assistent für neue Nutzer, 'Was ist neu' nach einem Update."""
        pr = theme.prefs()
        if not pr.get("wizard_done"):
            pr["wizard_done"] = True
            pr["seen_version"] = __version__
            config.save(self.ctx.settings)
            if getattr(self, "_was_installed_before", False):
                self.open_whats_new()
            else:
                self.open_wizard()
        elif pr.get("seen_version") != __version__:
            pr["seen_version"] = __version__
            config.save(self.ctx.settings)
            self.open_whats_new()

    def save_report(self):
        from .crash import save_report_dialog
        return save_report_dialog(self)

    def _about(self):
        from .about import AboutDialog
        AboutDialog(self).exec()

    def _restore_geometry(self):
        g = self.ctx.settings.window_geometry
        if g:
            try:
                if self.restoreGeometry(QByteArray.fromHex(g.encode())):
                    return
            except Exception:  # noqa: BLE001 – kaputte gespeicherte Geometrie: Standardgröße nehmen
                pass
        if QApplication.platformName() != "offscreen":
            scr = self.screen() or QApplication.primaryScreen()
            if scr is not None and needs_maximize(scr.availableGeometry().width(), scr.availableGeometry().height(),
                                                  self.width(), self.height()):
                self.setWindowState(Qt.WindowState.WindowMaximized)

    def closeEvent(self, e):
        rec = self.recorder.rec
        if rec and rec.running:
            if QMessageBox.question(self, "Aufnahme läuft", "Eine Aufnahme läuft. Beenden und speichern?") \
                    != QMessageBox.StandardButton.Yes:
                e.ignore()
                return
            rec.stop()
        try:
            self.ctx.settings.window_geometry = bytes(self.saveGeometry().toHex()).decode()
            config.save(self.ctx.settings)
        except OSError:
            pass
        self.shutdown()
        super().closeEvent(e)

    def shutdown(self):
        """alles geordnet anhalten – einmal, beim Schließen ODER beim Beenden der App (aboutToQuit).
        Reihenfolge: Timer, Seiten, Web-Views (solange das Profil lebt), dann Gerät/Threads, zuletzt der Server.
        So laufen beim Abbau der QApplication keine Hintergrund-Threads mehr in Qt-Objekte (Absturz 139)."""
        if getattr(self, "_shut_down", False):
            return
        self._shut_down = True
        try:
            config.save_listeners.remove(self._save_hook)
        except (ValueError, AttributeError):
            pass
        steps = [lambda: self.pi_status.stop(), lambda: self.ctx.timer.stop(), lambda: self.fm1.deactivate()]
        steps += [(lambda p=p: p.shutdown()) for p in self.pages.values()
                  if p is not self.editor and hasattr(p, "shutdown")]
        steps += [lambda: self.editor.shutdown()]
        if getattr(self, "studio", None) is not None:
            steps += [lambda: self.studio.shutdown()]     # Stand sichern, Zwilling abbauen (vor den Web-Seiten)

        def web_down():
            from . import web
            web.shutdown_all()
        steps += [web_down, lambda: self.ctx.disconnect(None), lambda: self.server.stop()]
        for st in steps:
            try:
                st()
            except Exception:                  # noqa: BLE001 – Beenden darf nie hängen bleiben
                pass
