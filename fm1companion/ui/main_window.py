# SPDX-License-Identifier: GPL-3.0-only
"""Main window: navigation on the left, pages on the right, device selection at the top."""
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
from ..i18n import _

# built-in pages: (key, title, icons, group, order, description)
PAGES = [("overview", _("Overview"), ("go-home", "user-home"), "geraet", 10,
          _("active device, backup, firmware & updates – and the state of the FM-1 (live load, memory)")),
         ("editor", _("Editor"), ("audio-midi", "audio-card", "applications-multimedia"), "geraet", 40,
          _("the original web editor of the installed firmware")),
         ("samples", _("Samples & Kits"), ("audio-x-generic", "folder-sound"), "klang", 30,
          _("sample library, build and upload kits")),
         ("projects", _("Projects"), ("folder-music", "folder-sound", "folder"), "musik", 10,
          _("fetch, collect and restore project slots – view them and export them as MIDI")),
         ("recorder", _("Recording"), ("media-record", "audio-input-microphone"), "musik", 50,
          _("record the USB audio of the FM-1, Audacity")),
         ("backups", _("Backups"), ("backup", "document-save"), "daten", 10,
          _("create, restore, browse and compare full backups"))]

# merged pages: main key -> [(tab key, tab title, page key of the subpage)].
# The first tab is the main subpage; the other page keys are kept as redirects
# (window.go("fm1") -> Overview › State).
COMPOSITES: dict[str, list[tuple[str, str, str]]] = {
    "overview": [("start", _("Start"), "overview"), ("zustand", _("Status"), "fm1")],
    "projects": [("plaetze", _("Slots & Library"), "projects"), ("ansicht", _("View & Export"), "project_view")],
    "compose": [("erzeugen", _("Generate"), "compose"), ("midi", _("From MIDI File"), "workshop")],
    "backups": [("liste", _("List"), "backups"), ("browser", _("Browse & Compare"), "backup_browser")],
    "presets": [("user", _("User Presets on the FM-1"), "presets"), ("voices", _("FM Voices (DX7)"), "voices")],
}
# pages that are embedded as a further tab into a page's existing tab widget (page.tabs)
FLAT_COMPOSITES: dict[str, list[tuple[str, str, str]]] = {
    "midi": [("klangerzeuger", _("Sound Module"), "soundmodule")],
    # Firmware+ (catalog) and the former “Firmware” page are ONE page: installing, switching versions,
    # update mode and the way back to the original live in the “Install & versions” tab (window.go("firmware"))
    "discover": [("install", _("Install & Versions"), "firmware"), ("simulator", _("Simulator"), "simulator")],
}
# embedded tabs at a fixed position instead of at the end (tab index in page.tabs)
FLAT_TAB_POS: dict[str, int] = {"firmware": 1}
# old page key -> (main page, tab)
ALIASES: dict[str, tuple[str, str]] = {sub: (main, tab) for main, tabs in COMPOSITES.items()
                                       for tab, _t, sub in tabs if sub != main}
ALIASES.update({sub: (main, tab) for main, tabs in FLAT_COMPOSITES.items() for tab, _t, sub in tabs})
# sidebar: wide with names or narrow with icons; automatic mode switches below this window width
SIDEBAR_WIDE = 224
SIDEBAR_COMPACT = 60
SIDEBAR_AUTO_BELOW = 1150
SIDEBAR_MODES = ("auto", "wide", "icons")
# fixed order for Ctrl+1 … Ctrl+9 (the most important pages first)
HOTKEY_ORDER = ["overview", "discover", "editor", "presets", "samples", "projects", "compose", "backups", "help"]


log = logging.getLogger("fm1companion.window")


def needs_maximize(avail_w: int, avail_h: int, want_w: int, want_h: int, frame: int = 40) -> bool:
    """First start without a saved window size: if the default window (1280×820 + title bar) doesn't fit on the
    free screen (14" laptop 1280×720 minus panel), maximize instead of extending over the edge."""
    return want_w > avail_w or want_h + frame > avail_h


def app_icon() -> QIcon:
    """App icon: installed hicolor icon, otherwise the bundled PNG."""
    ic = QIcon.fromTheme("fm1-companion")
    if ic.isNull():
        base = Path(__file__).resolve().parents[1] / "icons"
        ic = QIcon(str(base / "fm1-companion.png"))
    return ic


class NavDelegate(QStyledItemDelegate):
    """Sidebar: group headings in small caps, selection as a pill with an accent stroke."""

    def _compact(self) -> bool:
        return bool(getattr(self.parent(), "compact", False))

    def sizeHint(self, option, index):
        sh = super().sizeHint(option, index)
        head = index.data(Qt.ItemDataRole.UserRole) is None
        fm = option.fontMetrics
        if self._compact():                                   # icons only: groups as a thin separator line
            if head:
                return QSize(40, 11 if index.row() > 0 else 4)
            return QSize(40, max(fm.height() + 10, 32))
        # compact enough that all pages fit into the bar without scrolling even at 1280×720
        return QSize(sh.width(), (fm.height() + 12) if head and index.row() > 0 else max(fm.height() + 10, 30)
                     if not head else fm.height() + 4)

    def paint(self, p: QPainter, option, index):
        t = theme.current()
        r = option.rect
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        compact = self._compact()
        if index.data(Qt.ItemDataRole.UserRole) is None and compact:   # group in the narrow bar: line
            if index.row() > 0:
                p.setPen(QPen(QColor(t.border), 1))
                y = r.center().y()
                p.drawLine(r.left() + 12, y, r.right() - 12, y)
            p.restore()
            return
        if index.data(Qt.ItemDataRole.UserRole) is None:            # group heading
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
    """Sidebar: arrow keys, Page Up/Down, Home and End skip the group headings
    (previously Home landed on “DEVICE” – highlighted, but without a page)."""

    def moveCursor(self, action, modifiers):
        idx = super().moveCursor(action, modifiers)
        A = QListWidget.CursorAction
        step = -1 if action in (A.MoveUp, A.MovePrevious, A.MovePageUp, A.MoveLeft) else 1
        if action == A.MoveEnd:
            step = -1
        row = idx.row() if idx.isValid() else -1
        while 0 <= row < self.count() and not (self.item(row).flags() & Qt.ItemFlag.ItemIsSelectable):
            row += step
        if not 0 <= row < self.count():              # at the top above the first heading: stay put
            return self.currentIndex()
        return self.model().index(row, 0)


class MainWindow(QMainWindow):
    save_failed = pyqtSignal(str)              # config.save failed (from any thread)

    def __init__(self, progress=None):
        super().__init__()
        self._progress = progress or (lambda text: None)
        self.commands: list[dict] = []          # additional entries for the command search
        self.setWindowTitle(APP_NAME + (_(" – demo mode – data kept separate") if DEMO else ""))
        self.setWindowIcon(app_icon())
        self.resize(1280, 820)
        self.ctx = DeviceContext(self)
        self.ctx.window = self
        _qa = QApplication.instance()
        if _qa is not None:                          # tear down in order even without closing the window (app.quit)
            _qa.aboutToQuit.connect(self.shutdown)
        self.ctx.pending_restore_check = ""
        self.server = editor_host.LocalServer()
        self.studio = None                     # the studio twin (ui/studio_service.py) – only starts when needed
        try:
            from .studio_service import StudioService
            self.studio = StudioService(self)
        except Exception:                      # noqa: BLE001 – without the studio the rest of the app keeps running
            log.exception("Studio service not available")

        # navigation (grouped; pages from ui/pages register themselves)
        self.nav = NavList()
        self.nav.compact = False
        self.nav.setObjectName("Sidebar")
        self.nav.setIconSize(QSize(18, 18))
        self.nav.setItemDelegate(NavDelegate(self.nav))
        self.nav.setMouseTracking(True)
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.stack = QStackedWidget()
        self.stack.setObjectName("PageStack")
        self._progress(_("Starting the basic pages …"))
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
        self.absorbed: dict[str, PageSpec] = {    # pages that are merged as tabs into another page
            "fm1": PageSpec("fm1", _("FM-1 Status"), "geraet", (lambda c, w: w.fm1), ("audio-card", "hwinfo"), 20,
                            _("State of the FM-1: live load, memory, user presets"),
                            ("zustand", "live", "cpu", "speicher", "presets", "diagnose", "status", "memory", "load",
                             "diagnostics")),
            "workshop": PageSpec("workshop", _("From MIDI File"), "musik", (lambda c, w: w.workshop),
                                 ("tools", "audio-x-midi"), 40, _("MIDI file → pattern on an FM-1 track"),
                                 ("werkstatt", "midi", "pattern", "daw", "musescore", "lmms", "import", "workshop",
                                  "midi file")),
            # the former “Firmware” page – now a tab of Firmware+ (key "firmware" stays valid)
            "firmware": PageSpec("firmware", _("Install & Versions"), "daten", (lambda c, w: w.firmware),
                                 ("system-software-update", "update-none"), 30,
                                 _("install, switch and update firmware; download versions; back to the original"),
                                 ("firmware", "installieren", "update", "aktualisieren", "version", "wechseln",
                                  "herunterladen", "original", "v15", "v16", "fwsc", "hinzufügen", "rückweg", "install",  # i18n: ignore
                                  "switch", "download", "add", "way back")),
        }
        absorbed_keys = set(ALIASES)
        for s in discover():
            if s.key in builtin or s.key in {x.key for x in specs}:
                continue                       # key already taken
            if s.key in absorbed_keys:
                self.absorbed[s.key] = s
                continue
            specs.append(s)
        if not any(s.key == "discover" for s in specs):   # catalog module missing/broken: installing stays reachable
            specs.append(PageSpec("discover", "Firmware+", "daten", (lambda c, w: w.firmware),
                                  ("system-software-update", "update-none"), 12,
                                  _("install, switch and update firmware")))
        self.subpages: dict[str, QWidget] = {}    # old key -> subpage
        self._build_nav(sort_specs(specs))
        self.nav.currentRowChanged.connect(self._page)
        self._progress(_("Setting up the window …"))
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
        # notices (toasts) with a small window: own row above the page – then they don't cover buttons
        self.toast_lane = QWidget()
        self.toast_lane.setObjectName("ToastLane")
        tl = QHBoxLayout(self.toast_lane)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(8)
        tl.addStretch(1)
        self.toast_lane.hide()
        pv.addWidget(self.toast_lane)
        # pages in a scroll area: a wide page (simulator) no longer forces the window wide,
        # it scrolls horizontally; only the visible page counts for the minimum size (responsive.current_only)
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
        self.setMinimumSize(640, 480)                 # below this nothing would be usable anymore; pages scroll from here on

        # toolbar: device
        tb = QToolBar(_("Device"))
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
        self.search_button.setText(_("Search …  Ctrl+K"))
        self.search_button.setIcon(icon("system-search"))
        self.search_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.search_button.setToolTip(_("Command search: find pages, actions and help (Ctrl+K)"))
        self.search_button.clicked.connect(self.open_palette)
        tb.addWidget(self.search_button)
        a_bk = QAction(icon("document-save", "backup"), _("Back up now"), self)
        a_bk.setToolTip(_("Create a full backup of the active FM-1 (Ctrl+B)"))
        a_bk.triggered.connect(self._backup_now)
        tb.addAction(a_bk)
        self.bell = QToolButton()
        self.bell.setIcon(icon("notifications"))
        self.bell.setToolTip(_("Notifications"))
        self.bell.clicked.connect(self.show_notifications)
        tb.addWidget(self.bell)

        # status bar: computer values, notices
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
        disc = self.pages.get("discover")         # Firmware+: list changed in the “Install & versions” tab
        if disc is not None and hasattr(disc, "set_catalog"):
            self.firmware.list_changed.connect(lambda: disc.set_catalog(disc.cat))
        if hasattr(self.editor, "navigate"):
            self.editor.navigate.connect(self.go)
        self.ctx.changed.connect(self._dev_changed)
        self.ctx.ports_changed.connect(self._fill_devices)
        # FM-1 plugged in: don't let it become the computer's default output unnoticed
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
        self.nav.compact = None                       # forces the first build
        self._update_sidebar()
        self._last_conn = None
        self._checked_for = ""
        self._switching = False
        self._dev_changed()
        self._save_warned = False
        self.save_failed.connect(self._warn_save_failed)
        self._save_hook = self.save_failed.emit
        config.save_listeners.append(self._save_hook)
        if config.save_error:                         # already failed at startup (before the window)
            QTimer.singleShot(1400, lambda: self._warn_save_failed(config.save_error))
        cfg_msg = config.problems_text()
        if cfg_msg:                                   # one-time: broken/invalid settings
            self.config_notice = cfg_msg
            QTimer.singleShot(1200, lambda: self.notify(cfg_msg, "warning"))
        self.missing_deps: list[str] = []
        QTimer.singleShot(2500, self._check_required_deps)
        QTimer.singleShot(60000, self._storage_autoclean)     # old logs, downloads, caches (once a day)

    def _storage_autoclean(self):
        from .. import storage
        from .common import run_job
        if not storage.auto_due():
            return

        def done(res):
            if res is not None and res.freed:
                log.info("automatic cleanup: %d items, %d bytes freed", res.removed, res.freed)
        run_job(self, lambda w: storage.run_auto(), on_done=done, on_fail=lambda m, tb: log.warning(
            "automatic cleanup failed: %s", m))

    def _warn_save_failed(self, text: str):
        """Settings couldn't be saved: say so once per session (not on every click)."""
        if self._save_warned or not text:
            return
        self._save_warned = True
        self.notify(text, "warning")

    def _check_required_deps(self):
        """Report missing required packages (QtWebEngine, rtmidi …) once at startup – with the apt command."""
        from .. import diag
        from .common import run_job

        def done(states):
            miss = [s for s in states if s.required and not s.ok]
            if not miss:
                return
            self.missing_deps = [s.package for s in miss]
            what = ", ".join(f"{s.package} ({s.why})" if s.why else s.package for s in miss)
            self.notify(_("Something is missing: {what}. The app runs, but not all pages. Install with: {command}")
                        .format(what=what, command=diag.apt_command(miss, include_optional=False)), "warning",
                        (_("Open diagnostics"), lambda: self.open_help("diagnose")))
        # never load Qt modules for the first time in a background thread (Qt Multimedia pulls in PipeWire/FFmpeg
        # plugins; in parallel to the GUI thread this occasionally crashed): do it here in the GUI thread beforehand
        import importlib
        for d in diag.deps().get("required", []) + diag.deps().get("recommended", []):
            mod = d.get("check", {}).get("module", "")
            if mod.startswith("PyQt6."):
                try:
                    importlib.import_module(mod)
                except Exception:  # noqa: BLE001 – missing: check_deps reports it
                    pass
        run_job(self, lambda w: diag.check_deps(), on_done=done, on_fail=lambda m, tb: None)

    # ------------------------------------------------------------ Menus
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
        m = mb.addMenu(_("&File"))
        # fixed Ctrl+, (as in the help): StandardKey.Preferences is Ctrl+Shift+, under KDE and empty otherwise
        self._act(m, _("Settings …"), self._settings, "Ctrl+,",
                  ("configure", "preferences-system"))
        self._act(m, _("Open main folder"), lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.ctx.settings.root))),
                  None, ("folder-open",))
        m.addSeparator()
        self._act(m, _("Quit"), self.close, QKeySequence.StandardKey.Quit, ("application-exit",))

        g = mb.addMenu(_("&Device"))
        self._act(g, _("Back up now"), self._backup_now, "Ctrl+B", ("document-save",))
        self._act(g, _("Reconnect"), self._reconnect, "F5", ("view-refresh",))
        self._act(g, _("Rename device …"), lambda: self.overview._rename() if self.ctx.profile else None, None,
                  ("edit-rename",))
        self._act(g, _("FM-1 status"), lambda: self.go("fm1"), None, ("audio-card",))

        v = mb.addMenu(_("&View"))
        self.a_sidebar = self._act(v, _("Sidebar"), self.toggle_sidebar, "F9", ("view-sidebar",), checkable=True)
        sm = v.addMenu(icon("view-sidebar"), _("Sidebar: width"))
        sgrp = QActionGroup(self)
        self.sidebar_mode_actions = {}
        for key, title in (("auto", _("Automatic (by window width)")), ("wide", _("Wide, with names")),
                           ("icons", _("Narrow, icons only"))):
            a = self._act(sm, title, lambda _=False, k=key: self.set_sidebar_mode(k), checkable=True)
            sgrp.addAction(a)
            a.setChecked(self.sidebar_mode() == key)
            self.sidebar_mode_actions[key] = a
        self.a_full = self._act(v, _("Full screen"), self.toggle_fullscreen, "F11", ("view-fullscreen",), checkable=True)
        v.addSeparator()
        self._act(v, _("Zoom in"), lambda: self.zoom(+0.1), QKeySequence.StandardKey.ZoomIn, ("zoom-in",))
        self._act(v, _("Zoom out"), lambda: self.zoom(-0.1), QKeySequence.StandardKey.ZoomOut, ("zoom-out",))
        self._act(v, _("Normal size"), lambda: self.zoom(0), "Ctrl+0", ("zoom-original",))
        dm = v.addMenu(icon("preferences-desktop-theme"), _("Theme"))
        grp = QActionGroup(self)
        self.theme_actions = {}
        for key, title in theme.MODES:
            a = self._act(dm, title, lambda _=False, k=key: self.set_theme(k), checkable=True)
            grp.addAction(a)
            a.setChecked(self.ctx.settings.theme == key)
            self.theme_actions[key] = a
        v.addSeparator()
        self._act(v, _("Notifications …"), self.show_notifications, None, ("notifications",))

        w = mb.addMenu(_("&Tools"))
        self._act(w, _("Command search …"), self.open_palette, "Ctrl+K", ("system-search",),
                  _("Find pages, actions and help"))
        self._act(w, _("System check …"), self.open_syscheck, None, ("utilities-system-monitor",),
                  _("Check USB, power and audio for problems"))
        self._act(w, _("Storage and cleanup …"), lambda: self._settings("storage"), None,
                  ("drive-harddisk", "edit-clear"), _("Logs, downloaded firmware and caches: sizes, delete"))

        hm = mb.addMenu(_("&Help"))
        self._act(hm, _("Help Center"), lambda: self.open_help(), "F1", ("help-contents",))
        self._act(hm, _("Keyboard shortcuts"), self.show_shortcuts, "Ctrl+/", ("help-keyboard-shortcuts",))
        hm.addSeparator()
        self._act(hm, _("Setup assistant …"), self.open_wizard, None, ("tools-wizard",))
        self._act(hm, _("What's new in 2.0"), self.open_whats_new, None, ("help-hint",))
        hm.addSeparator()
        self._act(hm, _("Create bug report …"), self.save_report, None, ("tools-report-bug",))
        self._act(hm, _("Diagnostics"), lambda: self.open_help("diagnose"), None, ("hwinfo",))
        hm.addSeparator()
        self._act(hm, _("About {name}").format(name=SUITE_NAME), self._about, None, ("help-about",))

        self.hotkeys: list[tuple[str, str]] = []     # (shortcut, page title) – for the shortcuts dialog
        keys = [k for k in HOTKEY_ORDER if k in self.specs] + [k for k in self.specs if k not in HOTKEY_ORDER]
        for i, key in enumerate(keys[:9]):
            act = QAction(self.specs[key].title.replace("&", "&&"), self)
            act.setShortcut(f"Ctrl+{i + 1}")
            act.triggered.connect(lambda _=False, k=key: self.go(k))
            self.addAction(act)
            self.hotkeys.append((_("Ctrl+{n}").format(n=i + 1), self.specs[key].title))

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
        logo.setToolTip(f"{SUITE_NAME} {__version__}" + (_(" – demo mode") if DEMO else ""))
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
        # wide/narrow toggle at the top in the brand row (at the bottom it cost height – at 720 px the bar scrolled)
        self.sidebar_button = QToolButton()
        self.sidebar_button.setObjectName("SidebarToggle")
        self.sidebar_button.setAutoRaise(True)
        self.sidebar_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.sidebar_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)   # via keyboard: View › Sidebar: width
        self.sidebar_button.clicked.connect(self._toggle_compact)
        brand.addWidget(self.sidebar_button, 0, Qt.AlignmentFlag.AlignTop)
        v.addLayout(brand)
        v.addWidget(self.nav, 1)
        return panel

    # ------------------------------------------------------------ Sidebar: wide / icons only
    def sidebar_mode(self) -> str:
        """“auto” (by window width), “wide” or “icons” – a manual choice takes precedence over the automatic mode."""
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
        """Automatic mode with hysteresis: narrow below SIDEBAR_AUTO_BELOW, wide again from +32 px."""
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
        """Sidebar with icons only (names as tooltip, groups as separator line) or wide again."""
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
        self.sidebar_button.setToolTip(_("Show the sidebar with names") if on
                                       else _("Narrow sidebar: icons only (more room for the page)"))
        self.nav.doItemsLayout()
        self.nav.viewport().update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, "nav") and hasattr(self, "brand_texts"):
            self._update_sidebar()
            self._update_toolbar()

    def _update_toolbar(self):
        """narrow window: more compact toolbar (search and backup as icons only, shorter device state),
        so that “Back up now” and the bell don't disappear into the overflow menu."""
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

    # ------------------------------------------------------------ Pages
    def _build_nav(self, specs: list[PageSpec]):
        group = None
        for s in specs:
            self._progress(_("Loading {page} …").format(page=s.title))
            try:
                page = self._make_page(s)
            except Exception:                  # noqa: BLE001 – a broken page must not stop the app
                import traceback
                from .pages import load_errors
                load_errors.append((s.key, traceback.format_exc()))
                log.error("Page %s could not be loaded:\n%s", s.key, load_errors[-1][1])
                if s.key != "discover":
                    continue
                page = self.firmware               # catalog broken: still offer “Install & versions”
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
        """Build a page – merged pages (COMPOSITES) as a TabbedPage with their subpages."""
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
                tw = main.tabs                      # tab icons: like the main page's own tabs
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
        """Page for a key – also for old keys that are now tabs of another page
        (page("backup_browser") is the “Browse & compare” subpage of “Backups”)."""
        if key in ALIASES:
            return self.subpages.get(key)
        return self.pages.get(key)

    def resolve(self, key: str) -> tuple[str, str]:
        """(navigation key, tab key or "") for a – possibly old – page key."""
        if key in ALIASES:
            return ALIASES[key]
        if key in COMPOSITES:
            return key, COMPOSITES[key][0][0]
        return key, ""

    def current_key(self) -> str:
        cur = self.stack.currentWidget()
        return next((k for k, p in self.pages.items() if p is cur), "")

    def go(self, key: str, **kwargs):
        """Open a page; kwargs go to page.open_with(**kwargs) if present.
        Old keys (fm1, workshop, project_view, backup_browser) redirect to the new page and the
        matching tab; ``tab=`` selects a tab of a merged page directly."""
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
        if key is None:                        # group heading
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

    # ------------------------------------------------------------ Device
    def _fill_devices(self):
        self.dev_combo.blockSignals(True)
        self.dev_combo.clear()
        if not self.ctx.ports:
            self.dev_combo.addItem(_("no FM-1 connected"), None)
        for p in self.ctx.ports:
            prof = self.ctx.profile_for_port(p)
            name = prof.name if prof else _("new FM-1")
            self.dev_combo.addItem(icon("audio-midi", "media-flash"), f"{name}  ·  {p.label}"
                                   + (_("  (update mode)") if p.update_mode else ""), p.key)
        vports = list(getattr(self.ctx, "vports", []) or [])
        if vports:
            self.dev_combo.insertSeparator(self.dev_combo.count())
        for p in vports:                       # the studio twin: clearly marked as virtual
            self.dev_combo.addItem(icon("computer", "audio-card"), _("{label}  ·  virtual (not a real FM-1)").format(label=p.label),
                                   p.key)
            self.dev_combo.setItemData(self.dev_combo.count() - 1,
                                       _("The twin in the Studio: Projects, Compose, Presets, Backups and Editor "
                                         "then work with it instead of a real FM-1. Firmware, Recovery and USB "
                                         "audio are not available for it."), Qt.ItemDataRole.ToolTipRole)
        if self.ctx.port:
            self.dev_combo.setCurrentIndex(max(0, self.dev_combo.findData(self.ctx.port.key)))
        self.dev_combo.blockSignals(False)

    def _choose_device(self, i):
        key = self.dev_combo.itemData(i)
        port = next((p for p in list(self.ctx.ports) + list(getattr(self.ctx, "vports", []) or [])
                     if p.key == key), None)
        if port and (not self.ctx.port or port.key != self.ctx.port.key):
            self._switching = True             # deliberate switch: no “disconnected” warning, only “Active: …”
            QTimer.singleShot(8000, lambda: setattr(self, "_switching", False))
            self.ctx.connect_port(port)

    def _reconnect(self):
        port = self.ctx.port
        if port:
            self.statusBar().showMessage(_("Reconnecting to “{port}” …").format(port=port.label), 6000)
            self.ctx.connect_port(port)
        else:
            self.ctx.scan()
            self.statusBar().showMessage(_("Searched again: {result}").format(
                result=_("{n} FM-1 found.").format(n=len(self.ctx.ports)) if self.ctx.ports
                else _("no FM-1 found – check the USB data cable.")), 8000)

    def _check_blocking_services(self):
        """Debian's fluidsynth service occupies the HDMI card (no more sound via PipeWire): offer to turn it off."""
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
                        on_fail=lambda m, tb: self.notify(_("Could not disable it: {error}").format(error=m), "warning"))
            self.notify(_("The system's FluidSynth service starts at every login and takes the first sound card "
                          "(on the Pi: HDMI) – then your speaker/HDMI output is missing. The app does not need the "
                          "service (the sound module starts FluidSynth itself)."), "warning",
                        (_("Disable service"), fix))
        run_job(self, lambda w: AF.fluidsynth_service(), on_done=done, on_fail=lambda m, tb: None)

    def _audio_guard(self):
        if getattr(self, "_shut_down", False) or not (self.ctx.ports or []):
            return
        try:
            from .audio import guard_default_output
            guard_default_output()
        except Exception:  # noqa: BLE001 – convenience, must never interfere
            pass

    def _dev_changed(self):
        if getattr(self, "_shut_down", False):   # exiting disconnects the device – that is not an “FM-1 disconnected” notice
            return
        self._fill_devices()
        c = self.ctx
        col = c.profile.color if c.profile else "#888"
        if getattr(c, "protocol", "") and hasattr(c, "status_text"):
            state = c.status_text()
        else:
            state = _("connected") if c.connected else (_("not responding") if c.port else _("not connected"))
        import html
        # without a device only “No FM-1 connected” (previously duplicated: “No FM-1 connected – not connected”)
        text = html.escape(c.title()) + (f" – {html.escape(state)}" if c.port else "")
        self._dev_texts = (f"  <b style='color:{col}'>●</b> {text}  ",
                           # narrow window: only the state – name and firmware are already in the selection
                           f"  <b style='color:{col}'>●</b> {html.escape(state) if c.port else text}  ")
        self.dev_state.setToolTip(c.title() + (f" – {state}" if c.port else ""))
        self._apply_dev_text()
        conn = (c.profile.id if c.profile else "", c.connected)
        switching = getattr(self, "_switching", False)
        if c.connected and conn != self._last_conn and c.profile:
            from .common import fw_label
            if switching:
                self._switching = False
                self.notifications.post(_("Active: {name} – {firmware}").format(
                    name=c.profile.name, firmware=fw_label(c.info.version) if c.info else ""),
                                        "info", quiet=True)
            else:
                self.notify(_("{name} connected – {firmware}").format(
                    name=c.profile.name, firmware=fw_label(c.info.version) if c.info else ""), "success")
        elif c.port and switching and getattr(c, "protocol", "") == "generic" and c.profile and conn != self._last_conn:
            self._switching = False
            self.notifications.post(_("Active: {name} – {firmware}").format(
                name=c.profile.name, firmware=c.title().split(" – ", 1)[-1]), "info", quiet=True)
        elif self._last_conn and self._last_conn[1] and not c.port and not switching:
            self.notify(_("FM-1 disconnected"), "warning")
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
            msg = _("New firmware available: {new} (installed: {current})").format(new=new, current=cur or _("unknown"))
            self.statusBar().showMessage(msg, 15000)
            self.notify(msg, "info",
                        (_("View"), lambda: self.go("firmware")))

    def _offer_restore(self, key):
        f = find_backup_for(self.ctx, key)
        if not f:
            return
        box = QMessageBox(QMessageBox.Icon.Question, _("Back to your data?"),
                          _("There is a backup from this device for this firmware:\n{name}\n\n"
                            "Restore it now? (Projects, presets and settings from back then.)").format(name=f.name),
                          parent=self)
        yes = box.addButton(_("Go to backups"), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(_("No, thanks"), QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is yes:
            self.go("backups")

    def _backup_now(self):
        path = make_backup(self.ctx, self)
        if path:
            self.backups.refresh()
            self.overview.refresh()
            self.notify(_("Backup saved: {name}").format(name=Path(path).name), "success",
                        (_("Open folder"), lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).parent)))))

    # ------------------------------------------------------------ Miscellaneous
    def _settings(self, tab: str = ""):
        d = SettingsDialog(self.ctx, self, tab=tab)
        if d.exec():
            self.apply_theme()
            self.backups.refresh()
            self.projects.refresh_library()
            self.samples.refresh_filters()
            self.recorder.refresh_list()
            self.overview.refresh()

    # ------------------------------------------------------------ Design / view
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
        self.statusBar().showMessage(_("Zoom {percent}%").format(percent=round(z * 100)), 2500)

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

    # ------------------------------------------------------------ Notices
    def notify(self, text: str, level: str = "info", action=None):
        """Toast + history (+ desktop notification if the window is in the background)."""
        return self.notifications.post(text, level, action)

    def _bell(self):
        n = self.notifications.unread()
        self.bell.setText(str(n) if n else "")
        self.bell.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon if n
                                     else Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.bell.setToolTip(_("Notifications ({n} new)").format(n=n) if n else _("Notifications"))

    def show_notifications(self):
        HistoryDialog(self.notifications, self).exec()

    # ------------------------------------------------------------ Commands, help, wizards
    def register_command(self, title: str, callback, *, subtitle: str = "", keywords: tuple = (), icon_name: str = ""):
        """Add a custom entry to the command search (e.g. from a page)."""
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
        """on a real start (not in tests): wizard for new users, 'What's new' after an update."""
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
            except Exception:  # noqa: BLE001 – broken saved geometry: use the default size
                pass
        if QApplication.platformName() != "offscreen":
            scr = self.screen() or QApplication.primaryScreen()
            if scr is not None and needs_maximize(scr.availableGeometry().width(), scr.availableGeometry().height(),
                                                  self.width(), self.height()):
                self.setWindowState(Qt.WindowState.WindowMaximized)

    def closeEvent(self, e):
        rec = self.recorder.rec
        if rec and rec.running:
            if QMessageBox.question(self, _("Recording in progress"), _("A recording is running. Stop and save it?")) \
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
        """stop everything in order – once, on close OR on app exit (aboutToQuit).
        Order: timers, pages, web views (while the profile lives), then device/threads, finally the server.
        That way no background threads run into Qt objects anymore while the QApplication is torn down (crash 139)."""
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
            steps += [lambda: self.studio.shutdown()]     # save the state, tear down the twin (before the web pages)

        def web_down():
            from . import web
            web.shutdown_all()
        steps += [web_down, lambda: self.ctx.disconnect(None), lambda: self.server.stop()]
        for st in steps:
            try:
                st()
            except Exception:                  # noqa: BLE001 – exit must never get stuck
                pass
