# SPDX-License-Identifier: GPL-3.0-only
"""Simulator: play FM-1 firmwares on the computer.

Bundled simulators are the firmware's C code as WebAssembly (no CPU emulator) – they run
in the embedded browser or, faster on the Pi, in Firefox. Plus online simulators from the firmware list.
MIDI from the FM-1 (keys/knobs) and from further MIDI inputs goes into the simulator; the sound can be
recorded as WAV to ~/FM-1/Aufnahmen.
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
from pathlib import Path

import html
import re

from PyQt6.QtCore import QSize, QTimer, QUrl, Qt, pyqtSignal
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                             QListWidgetItem, QPushButton, QScrollArea, QSizePolicy, QSplitter, QVBoxLayout, QWidget)

from .. import web
from ... import config, sims
from ...bridge import MidiBridge
from ..common import fmt_secs, fmt_size, heading, icon
from . import PageSpec
from ...i18n import N_, _, ngettext, pgettext

LEFT_MIN = 380          # left column: never narrower (buttons, address field, description)


class WrapLabel(QLabel):
    """wrapping label that doesn't make the column wider than it is (long words are wrapped)."""

    def __init__(self, *a):
        super().__init__(*a)
        self.setWordWrap(True)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def minimumSizeHint(self) -> QSize:
        return QSize(0, super().minimumSizeHint().height())


def _breakable(text: str) -> str:
    """make long strings (URLs, commit hashes, license abbreviations) breakable at / - _ . @"""
    return re.sub(r"([/\-_.@])(?=\S)", "\\1\u200b", text)


HOST_JS = sims.DIR / "_host" / "fm1sim-host.js"
LATENCY = [("interactive", _("low (play directly)")), ("balanced", _("balanced")),
           ("playback", _("stable (large buffers, against crackling)"))]
# display texts of fm1companion/sims (translated there at run time) – listed here with N_() because
# tools/i18n_extract.py does not scan sims/; keep in sync with sims/__init__.py
SIM_TEXTS = (
    N_("M-VAVE FM1 Open (built from the official source code)"),
    N_("Built from M-VAVE's official source code (not checked by M-VAVE): FM1 Open V14 with 8 tracks, DX7, 808, "
       "303, PSG, sampler – playing, editing, projects, sounds and samples. User interface in English. The "
       "firmware update part is removed (installing works via “Firmware+” in the app)."),
    N_("Melodee 1.6.1 (stable) – user interface and sound of the firmware; nothing is saved. First press “Switch "
       "on” (or simply play a key on the FM-1)."),
    N_("fm1-emu – real firmware (slower)"),
    N_("CPU emulator by Joakim Eriksson (MIT): runs real .fwsc firmwares (Stock, Skiff, ORBIT …). Much slower "
       "than the WASM simulators – on the Pi 5 ORBIT is well playable, Skiff 2 just about, Stock V15 hardly. Best "
       "use “Open in Firefox”."),
    N_("Community workbench by thegiantsnail (GPL-3.0) with a software FM-1."),
    N_("Felucca – the base of most firmwares"),
    N_("SLOOP-based groovebox with event tape"),
    N_("909, 808, two 303s"),
    N_("Omnichord-like chord harp"),
    N_("Chord instrument à la Orchid"),
    N_("Formant vocal synth"),
    N_("RemoteFM1 (link only)"),
    N_("Quixotic7, GPL-3.0: the real FM-1 as a USB control panel for an emulated firmware. Ran badly on the Pi – "
       "therefore only as a reference; opens the project page in the browser."),
    N_("{name} missing"),
    N_("{name} damaged"),
)
SLOW_RATIO = 0.93      # sound clock / real clock below: the computer can't run the simulator in real time
SETTINGS = {"latency": "interactive", "recent": [], "inputs": [], "fm1_keys": True, "last": ""}

if web.HAVE_WEBENGINE:
    from PyQt6.QtWebEngineCore import QWebEngineScript, QWebEngineSettings

    class SimPage(web.RoutingPage):
        """collects console messages (debugging, tests)."""
        console = pyqtSignal(int, str)

        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.messages: list[tuple[int, str, int, str]] = []

        def javaScriptConsoleMessage(self, level, message, line, source):
            lvl = level.value if hasattr(level, "value") else int(level)
            self.messages.append((lvl, message, line, source))
            del self.messages[:-300]
            self.console.emit(lvl, message)


class SimulatorPage(QWidget):
    navigate = pyqtSignal(str)
    _midi = pyqtSignal(str, list)          # (source, bytes) from MIDI threads into the GUI thread

    def __init__(self, ctx, window):
        super().__init__()
        self.ctx, self.window = ctx, window
        self.server = getattr(window, "server", None)
        self.cfg = config.module("simulator", SETTINGS)
        self.current: sims.Sim | None = None
        self.view = None
        self._listen_link = None
        self.rec_running = False
        self.last_recording: Path | None = None
        self.midi_count = 0
        self.inputs = sims.SystemInputs(lambda name, data: self._midi.emit("sys:" + name, data))
        self._midi.connect(self._on_midi)
        self.popen = subprocess.Popen          # (replaceable for tests)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(6)
        self.split = split
        lay.addWidget(split)

        # ---- left: library
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.addWidget(heading(_("Simulator"), _("Play FM-1 firmwares on the computer – the real code of the "
                                            "firmware as WebAssembly. On the Pi it often runs more "
                                            "smoothly in Firefox.")))
        # “Start” large at the top; double-click/Enter in the list also starts; “Stop” only when one is running
        row = QHBoxLayout()
        self.b_start = QPushButton(icon("media-playback-start"), _("Launch"))
        self.b_start.setProperty("variant", "primary")
        self.b_start.setMinimumHeight(36)
        self.b_start.setToolTip(_("Load the selected simulator here in the app (Enter or double-click in "
                                  "the list)"))
        self.b_start.clicked.connect(self.start)
        self.b_stop = QPushButton(icon("media-playback-stop"), pgettext("simulator", "Stop"))
        self.b_stop.setToolTip(_("Stop the simulator – frees computing power and sound. Without stopping "
                                 "it keeps running, even when you change the page (like a switched-on "
                                 "instrument)."))
        self.b_stop.setEnabled(False)
        self.b_stop.clicked.connect(self.stop)
        row.addWidget(self.b_start, 2)
        row.addWidget(self.b_stop, 1)
        ll.addLayout(row)
        self.list = QListWidget()
        self.list.setMinimumHeight(200)
        self.list.currentItemChanged.connect(lambda *_a: self._show_details())
        self.list.itemActivated.connect(lambda *_a: self.start())
        self.list.itemDoubleClicked.connect(lambda *_a: self.start())
        ll.addWidget(self.list, 1)
        self.details = WrapLabel()
        self.details.setTextFormat(Qt.TextFormat.RichText)
        self.details.setOpenExternalLinks(True)
        ll.addWidget(self.details)
        self.b_ff = QPushButton(icon("firefox", "internet-web-browser"), _("Open in Firefox"))
        self.b_ff.setToolTip(_("Open in a separate Firefox window (often smoother on the Pi). MIDI from "
                               "the FM-1 also arrives there as long as the app is running."))
        self.b_ff.clicked.connect(self.open_firefox)
        ll.addWidget(self.b_ff)
        urow = QHBoxLayout()
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText(_("Address of an online simulator (https://…)"))
        self.url_edit.returnPressed.connect(self._open_typed)
        b_url = QPushButton(icon("go-jump", "internet-web-browser"), _("Open"))
        b_url.clicked.connect(self._open_typed)
        urow.addWidget(self.url_edit, 1)
        urow.addWidget(b_url)
        ll.addLayout(urow)

        # MIDI + performance
        ll.addWidget(QLabel(_("<b>MIDI into the simulator</b>")))
        self.cb_fm1 = QCheckBox(_("Keys and knobs of the FM-1"))
        self.cb_fm1.setChecked(bool(self.cfg.get("fm1_keys", True)))
        self.cb_fm1.setToolTip(_("What you play on the connected FM-1 plays along in the simulator "
                                 "(channel messages and clock)."))
        self.cb_fm1.toggled.connect(self._fm1_toggled)
        ll.addWidget(self.cb_fm1)
        prow = QHBoxLayout()
        self.port_combo = QComboBox()
        self.port_combo.setToolTip(_("additional MIDI input, e.g. a USB keyboard"))
        self.port_combo.activated.connect(self._port_chosen)
        b_scan = QPushButton(icon("view-refresh"), _("Search"))
        b_scan.setToolTip(_("Search MIDI inputs again"))
        b_scan.clicked.connect(self.refresh_ports)
        prow.addWidget(self.port_combo, 1)
        prow.addWidget(b_scan)
        ll.addLayout(prow)
        lrow = QHBoxLayout()
        lrow.addWidget(QLabel(_("Latency:")))
        self.lat_combo = QComboBox()
        for k, t in LATENCY:
            self.lat_combo.addItem(t, k)
        self.lat_combo.setCurrentIndex(max(0, self.lat_combo.findData(self.cfg.get("latency"))))
        self.lat_combo.setToolTip(_("Crackling? Choose “stable” – the sound then comes a little later. "
                                    "Applies at the next start (also in Firefox)."))
        self.lat_combo.activated.connect(self._latency)
        lrow.addWidget(self.lat_combo, 1)
        ll.addLayout(lrow)
        self.midi_state = WrapLabel()
        self.midi_state.setStyleSheet("color: palette(mid);")
        ll.addWidget(self.midi_state)
        for c in (self.port_combo, self.lat_combo):
            c.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            c.setMinimumContentsLength(8)
        for lab in left.findChildren(QLabel):          # (also the page header: never wider than the column)
            if lab.wordWrap() and not isinstance(lab, WrapLabel):
                lab.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
                lab.setMinimumWidth(0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(left)
        scroll.setMinimumWidth(LEFT_MIN)
        split.addWidget(scroll)

        # ---- right: bar + simulator
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        bar.setContentsMargins(8, 6, 8, 0)
        self.badge = QLabel()
        self.state = QLabel(_("Choose a simulator on the left."))
        self.state.setWordWrap(True)
        bar.addWidget(self.badge)
        bar.addWidget(self.state, 1)
        self.b_rec = QPushButton(icon("media-record"), _("Record"))
        self.b_rec.setToolTip(_("Record the simulator's sound as WAV to ~/FM-1/Aufnahmen (Ctrl+R)"))
        self.b_rec.setShortcut("Ctrl+R")
        self.b_rec.setCheckable(True)
        self.b_rec.clicked.connect(self.toggle_record)
        self.b_rec.setEnabled(False)                  # only once a simulator is running
        self.rec_label = QLabel("")
        self.speed_label = QLabel("")                 # honest tempo display: does the computer keep up with real time?
        self._slow = 0
        bar.addWidget(self.speed_label)
        bar.addWidget(self.rec_label)
        bar.addWidget(self.b_rec)
        rl.addLayout(bar)
        if not web.HAVE_WEBENGINE:
            msg = QLabel(_("Qt WebEngine is missing for the built-in simulator (sudo apt install "
                           "python3-pyqt6.qtwebengine). “Open in Firefox” still works."))
            msg.setWordWrap(True)
            rl.addWidget(msg, 1)
        else:
            self.bridge = MidiBridge(self)
            self.gate = sims.OutGate()                   # set anew per loaded page (start)
            self.bridge.out_filter = self._out_allowed
            self.bridge.extra_send = self._page_out
            self.bridge.blockedChanged.connect(self._blocked)
            self.view, self.page, self.channel = web.make_view(self, bridge=self.bridge, allow_remote=True,
                                                               page_class=None)
            self._replace_page()
            rl.addWidget(self.view, 1)
            web.add_download_handler(self._download)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([max(LEFT_MIN, int(self.cfg.get("left_width") or 420)), 900])
        split.splitterMoved.connect(self._split_moved)

        self.rec_timer = QTimer(self)
        self.rec_timer.setInterval(500)
        self.rec_timer.timeout.connect(self._rec_tick)
        self.speed_timer = QTimer(self)
        self.speed_timer.setInterval(2000)
        self.speed_timer.timeout.connect(self._speed_tick)
        self.fill_list()
        self.refresh_ports()
        self._placeholder(_("No simulator started yet"),
                          _("Choose a simulator on the left and press “Launch”. With a connected FM-1 its "
                            "keys play along in the simulator."))
        ctx.changed.connect(self._dev_changed)
        self._dev_changed()

    # ------------------------------------------------------------ Web page
    def _replace_page(self):
        """own page (capture the console, allow sound without a click, host script for online pages)."""
        old = self.page
        page = SimPage(self, allow_remote=True)
        from PyQt6.QtWebChannel import QWebChannel
        self.channel = QWebChannel(page)            # the old channel belongs to the old page
        self.channel.registerObject("fm1midi", self.bridge)
        page.setWebChannel(self.channel)
        s = page.settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)
        script = QWebEngineScript()
        script.setName("fm1sim-host")
        script.setSourceCode(HOST_JS.read_text("utf-8"))
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        page.scripts().insert(script)
        self.view.setPage(page)
        self.page = page
        web.track(page)
        if hasattr(page, "loadingChanged"):          # Qt >= 6.2: also reliably detects error pages
            page.loadingChanged.connect(self._loading)
        else:
            page.loadFinished.connect(self._loaded)
        if old is not None:
            old.deleteLater()

    def _split_moved(self, *_args):
        self.cfg["left_width"] = self.split.sizes()[0]
        try:
            config.save()
        except OSError:
            pass

    def _placeholder(self, title, text):
        """Placeholder in the theme's colors (light/dark) instead of a white area."""
        if not self.view:
            return
        pal = self.palette()
        bg = pal.color(QPalette.ColorRole.Window)
        fg = pal.color(QPalette.ColorRole.PlaceholderText)
        head = pal.color(QPalette.ColorRole.WindowText)
        try:
            self.view.page().setBackgroundColor(bg)
        except Exception:                          # noqa: BLE001
            pass
        self.view.setHtml(
            f"<html><head><meta charset='utf-8'><style>html,body{{background:{bg.name()};color:{fg.name()};"
            f"font-family:sans-serif;margin:0}}body{{padding:3em}}h2{{color:{head.name()};font-weight:600}}"
            f"</style></head><body><h2>{html.escape(title)}</h2><p>{html.escape(text)}</p></body></html>")

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == e.Type.PaletteChange and getattr(self, "view", None) is not None \
                and getattr(self, "current", None) is None:
            self._placeholder(_("No simulator started yet"),
                              _("Choose a simulator on the left and press “Launch”."))

    # ------------------------------------------------------------ Library
    def fill_list(self):
        self.list.clear()

        def head(t):
            it = QListWidgetItem(t)
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            f = it.font()
            f.setBold(True)
            it.setFont(f)
            self.list.addItem(it)

        self.entries: dict[str, sims.Sim] = {}
        local = sims.bundled()
        head(_("BUNDLED (offline)"))
        if not local:
            self.list.addItem(_("No simulators bundled – installation incomplete?"))
        for s in local:
            self._add(s, f"{s.title} · {s.version}", icon("media-playback-start", "audio-x-generic"))
        recent = [r for r in self.cfg.get("recent", []) if isinstance(r, dict) and r.get("url")]
        if recent:
            head(_("RECENTLY OPENED (online)"))
            for r in recent[:6]:
                s = sims.online(r["url"], r.get("title", ""), r.get("kind", "emulator"))
                self._add(s, f"{s.title}  [online]", icon("internet-web-browser"))
        head(_("ONLINE SIMULATORS (need internet)"))
        for s in sims.online_list():
            if s.key not in self.entries:
                self._add(s, f"{s.title}  [online]", icon("internet-web-browser"))
        last = self.cfg.get("last")
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.ItemDataRole.UserRole) in (last, local[0].key if local else None):
                self.list.setCurrentRow(i)
                if self.list.item(i).data(Qt.ItemDataRole.UserRole) == last:
                    break

    def _add(self, s: sims.Sim, text, ic):
        self.entries[s.key] = s
        it = QListWidgetItem(ic, text)
        it.setData(Qt.ItemDataRole.UserRole, s.key)
        it.setToolTip(s.description or s.url)
        self.list.addItem(it)

    def selected(self) -> sims.Sim | None:
        it = self.list.currentItem()
        key = it.data(Qt.ItemDataRole.UserRole) if it else None
        return self.entries.get(key) if key else None

    def _show_details(self):
        s = self.selected()
        if not s:
            self.details.setText("")
            return
        if s.online:
            self.details.setText(f"<b>{s.title}</b> <span style='color:#c80'>● online</span><br>{s.description}<br>"
                                 f"<small>{_breakable(s.url)}<br>"
                                 + _("Runs on the developer's site; needs internet. MIDI: choose the input “FM-1 "
                                     "(keys)” in the page.") + "</small>")
            self.b_ff.setEnabled(True)
            return
        src = f"{s.source_repo}/tree/{s.commit}" if s.commit else s.source_repo
        self.details.setText(_("<b>{title}</b> · version {version}<br>{description}<br><small>Source: <a href='{url}'>"
                               "{repo}</a> · License: {license} · {size}</small>").format(
            title=s.title, version=s.version, description=s.description, url=src,
            repo=s.source_repo.replace("https://github.com/", ""), license=_breakable(s.license), size=fmt_size(s.size)))
        # build details (commit, toolchain, date) only in the tooltip
        self.details.setToolTip(_("State: {ref} ({commit})\nBuilt: {date} with {toolchain}\nLicense: {license}").format(
            ref=s.ref, commit=s.commit, date=s.build_date[:10], toolchain=s.toolchain, license=s.license))
        self.b_ff.setEnabled(self.server is not None)

    # ------------------------------------------------------------ Start / stop
    def _url_for(self, s: sims.Sim, firefox=False) -> str:
        if s.online:
            return s.url
        lat = self.lat_combo.currentData() or "interactive"
        q = f"?fm1latency={lat}"
        return self.server.sim_url(s.key, s.entry) + q

    def start(self, s: sims.Sim | None = None):
        s = s or self.selected()
        if not s:
            return
        if s.kind == "link":
            web.open_external(s.url)
            self.state.setText(_("{title}: project page opened in the browser.").format(title=s.title))
            return
        if not self.view:
            return
        if not s.online:
            bad = sims.verify(s)
            if bad:
                self.state.setText(_("Simulator damaged: {files} – please reinstall the app.").format(files=", ".join(bad)))
                return
        if self.rec_running:
            self.toggle_record(False)
        self.current = s
        self.cfg["last"] = s.key
        config.save()
        self.page.messages.clear()
        self.bridge.blocked = 0
        # editors may talk to the FM-1 (after one-time consent per origin), simulators may not
        if s.online and s.kind == "editor":
            origin = sims.origin_of(s.url)
            if origin and not sims.origin_trusted(origin) and self.ask_origin(origin, s.title):
                sims.trust_origin(origin)
        self.gate = sims.OutGate(s.kind if s.online else "", self._url_for(s) if s.online else "")
        self.bridge.out_filter = self._out_allowed
        self.badge.setText("<b style='color:#c80'>● ONLINE</b>" if s.online else _("<b "
                                                                                     "style='color:#2a2'>● "
                                                                                     "local</b>"))
        self.state.setText(_("Loading {title} …").format(title=s.title))
        self.b_stop.setEnabled(True)
        self.b_rec.setEnabled(True)
        self.speed_timer.stop()
        self._show_speed(None)
        self.view.load(QUrl(self._url_for(s)))

    def stop(self):
        if self.rec_running:
            self.toggle_record(False)
        if self.current and self.view:
            self.state.setText(_("{title} stopped.").format(title=self.current.title))
        self.current = None
        self.b_rec.setEnabled(False)
        self.speed_timer.stop()
        self._show_speed(None)
        self.badge.setText("")
        self.b_stop.setEnabled(False)
        self._placeholder(_("Simulator stopped"), _("Choose a simulator on the left and press “Launch”."))

    def _loading(self, info):
        from PyQt6.QtWebEngineCore import QWebEngineLoadingInfo
        st = info.status()
        if st == QWebEngineLoadingInfo.LoadStatus.LoadStartedStatus or info.url().scheme() in ("data", "about"):
            return
        dom, E = info.errorDomain(), QWebEngineLoadingInfo.ErrorDomain
        # HttpStatusCodeDomain does not exist in every Qt version (e.g. Qt 6.4 of Debian Bookworm)
        http = getattr(E, "HttpStatusCodeDomain", None)
        ok_domains = {d for d in (getattr(E, "NoErrorDomain", None), http) if d is not None}
        failed = (st == QWebEngineLoadingInfo.LoadStatus.LoadFailedStatus
                  or (bool(ok_domains) and dom not in ok_domains)
                  or (http is not None and dom == http and info.errorCode() >= 400))
        if st == QWebEngineLoadingInfo.LoadStatus.LoadStoppedStatus and not failed:
            return
        self._loaded(not failed)

    def _loaded(self, ok: bool):
        s = self.current
        if not s:
            return
        if not ok:
            if s.online:
                self._placeholder(_("No connection"), _("{title} is on the internet ({url}) and cannot be "
                                                        "reached right now. The bundled simulators also "
                                                        "work offline.").format(
                                                          title=s.title, url=s.url))
                self.state.setText(_("Online simulator not reachable – offline?"))
            else:
                self.state.setText(_("Simulator could not be loaded."))
            return
        hint = {"melodee": _("Press “Switch on” or play a key on the FM-1. Computer keyboard: Z S X D C …"),
                "fm1-open": _("Just play a key (mouse, computer keyboard Z S X …, or on the FM-1).")}.get(s.key, "")
        text = (_("{title} is running (online).") if s.online else _("{title} is running.")).format(title=s.title)
        self.state.setText(f"{text} {hint}".strip())
        # Melodee waits for “Switch on”: once switched on, remove the hint from the status line
        self._await_power = s.key == "melodee"
        self._slow = 0
        self.speed_timer.start()

    def open_with(self, url: str = "", title: str = "", kind: str = "emulator", **_kw):
        """Jump from other pages (catalog): load an online emulator/editor."""
        if not url:
            return
        if not url.startswith(("http://", "https://")):
            self.state.setText(_("Invalid address: {url}").format(url=url))
            return
        s = sims.online(url, title, kind)
        rec = [r for r in self.cfg.get("recent", []) if isinstance(r, dict) and r.get("url") != url]
        self.cfg["recent"] = [{"url": url, "title": title or url, "kind": kind}] + rec[:9]
        config.save()
        self.fill_list()
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.ItemDataRole.UserRole) == s.key:
                self.list.setCurrentRow(i)
        self.start(self.entries.get(s.key, s))

    def _open_typed(self):
        u = self.url_edit.text().strip()
        if u and "://" not in u:
            u = "https://" + u
        self.open_with(url=u, title=u.split("//", 1)[-1].rstrip("/"))

    def firefox_command(self, s: sims.Sim | None = None) -> list[str] | None:
        s = s or self.selected()
        if not s or (not s.online and self.server is None):
            return None
        return sims.firefox_command(self._url_for(s, firefox=True))

    def open_firefox(self):
        cmd = self.firefox_command()
        if not cmd:
            self.state.setText(_("Firefox not found – sudo apt install firefox-esr"))
            return
        try:
            self.popen(cmd, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            self.state.setText(_("Firefox could not be started: {error}").format(error=e))
            return
        s = self.selected()
        self.state.setText(_("{title} opened in Firefox. MIDI from the FM-1 arrives there as long as the "
                             "app is running – the simulator here can be stopped.").format(
                               title=s.title))

    # ------------------------------------------------------------ MIDI
    def _dev_changed(self):
        link = self.ctx.link
        if self._listen_link is not None and self._listen_link is not link:
            try:
                self._listen_link.remove_listener(self._link_listener)
            except Exception:
                pass
            self._listen_link = None
        if link is not None and self._listen_link is None and self.cb_fm1.isChecked():
            link.add_listener(self._link_listener)
            self._listen_link = link
        self._update_ports()

    def _fm1_toggled(self, on):
        self.cfg["fm1_keys"] = bool(on)
        config.save()
        if not on and self._listen_link is not None:
            self._listen_link.remove_listener(self._link_listener)
            self._listen_link = None
        self._dev_changed()

    def _link_listener(self, data, direction):
        if direction == "in" and sims.page_midi(data):
            self._midi.emit("fm1", list(data))

    def refresh_ports(self):
        exclude = ()
        if self.ctx.port is not None:
            exclude = (getattr(self.ctx.port, "in_name", "") or "",)
        names = sims.SystemInputs.available(exclude + ("fm-1", "melodee", "felucca"))
        cur = self.cfg.get("inputs") or []
        self.port_combo.clear()
        self.port_combo.addItem(_("no additional MIDI input"), "")
        for n in names:
            self.port_combo.addItem(icon("audio-midi"), n, n)
        if not names:
            self.port_combo.setItemText(0, _("no additional MIDI input found"))
        pick = next((n for n in cur if n in names), "")
        self.port_combo.setCurrentIndex(max(0, self.port_combo.findData(pick)))
        self._apply_inputs([pick] if pick else [])

    def _port_chosen(self, _i):
        n = self.port_combo.currentData() or ""
        self.cfg["inputs"] = [n] if n else []
        config.save()
        self._apply_inputs(self.cfg["inputs"])

    def _apply_inputs(self, names):
        errs = self.inputs.set(names)
        if errs:
            self.state.setText(_("MIDI input cannot be opened: {errors}").format(errors="; ".join(errs)))
        self._update_ports()

    def _update_ports(self):
        ports = []
        if self.ctx.link is not None:
            ports += [{"id": "fm1-keys", "name": _("FM-1 (keys)"), "type": "input"},
                      {"id": "fm1-out", "name": "FM-1", "type": "output"}]
        for n in self.inputs.open:
            ports.append({"id": "sys:" + n, "name": n, "type": "input"})
        if self.view:
            self.bridge.set_extra_ports(ports)
        srcs = (["FM-1"] if self._listen_link else []) + list(self.inputs.open)
        self.midi_state.setText(_("MIDI active from: {sources}").format(sources=", ".join(srcs)) if srcs else
                                _("No MIDI input active – connect the FM-1 or choose a keyboard. Playing also works "
                                  "with the mouse and the computer keyboard."))

    def _on_midi(self, source: str, data: list):
        if not sims.page_midi(data):
            return
        self.midi_count += 1
        if self.server is not None:
            self.server.relay.publish(data)            # Firefox
        if not self.view or not self.current:
            return
        if self.current.online:                        # online pages: via their Web MIDI selection
            self.bridge.inject("fm1-keys" if source == "fm1" else source, data)
        else:
            self.page.runJavaScript(f"window.__fm1sim&&__fm1sim.midi({json.dumps(data)})")

    def ask_origin(self, origin: str, title: str = "") -> bool:
        """one-time prompt: may this web page send editor data (SysEx) to the real FM-1?"""
        from PyQt6.QtWidgets import QMessageBox
        box = QMessageBox(QMessageBox.Icon.Warning, _("Connect the online editor to the FM-1?"),
                          _("<b>{title}</b> is loaded from <b>{origin}</b> – a site on the internet that does not "
                            "belong to this app.").format(title=html.escape(title or origin), origin=html.escape(origin)),
                          parent=self)
        box.setInformativeText(
            _("If you allow it, this site may send editor commands (SysEx) to your FM-1 and thereby "
              "change sounds, presets and settings on the device. Firmware update and boot mode always "
              "stay blocked.\n\nWithout permission only notes, controllers and clock go to the FM-1. The "
              "permission applies only to exactly this address; if the site switches to another address, "
              "the safe filter applies again."))
        yes = box.addButton(_("Allow (remember for this address)"), QMessageBox.ButtonRole.AcceptRole)
        no = box.addButton(_("Allow notes only"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(no)
        box.exec()
        return box.clickedButton() is yes

    def _out_allowed(self, data) -> bool:
        """Filter for every message of the page – checks the CURRENT address of the page, not only the one at start."""
        try:
            cur = self.page.url().toString() if self.page is not None else ""
        except RuntimeError:                           # page already torn down
            cur = ""
        return self.gate.allowed(data, cur)

    def _page_out(self, port_id: str, data: list):
        """MIDI from a page to the FM-1 (beforehand through bridge.out_filter = self._out_allowed / OutGate)."""
        if port_id == "fm1-out" and self.ctx.link is not None:
            self.ctx.link.send_midi(data)

    def _blocked(self, n: int):
        """the page wanted to send something to the FM-1 that the safety filter doesn't let through – don't swallow it silently."""
        if n not in (1, 10, 100) and n % 1000:
            return
        s = self.current
        if s is not None and s.kind == "editor" and self.gate.editor_access(self.page.url().toString()):
            why = _("firmware update and boot mode are blocked for web pages")
        elif s is not None and s.kind == "editor":
            why = (_("this address has no permission for editor commands – only notes, controllers and "
                     "clock; the app asks for permission when opening the online editor"))
        else:
            why = _("simulators may only send notes, controllers and clock to the FM-1 – for device "
                    "access use the page “Editor”")
        self.state.setText(ngettext("{n} message of the page to the FM-1 held back ({reason}).",
                                    "{n} messages of the page to the FM-1 held back ({reason}).", n).format(n=n, reason=why))

    # ------------------------------------------------------------ Recording
    def js(self, code, cb):
        if self.view:
            self.page.runJavaScript(code, cb)

    def toggle_record(self, on: bool = False):
        if on and not self.rec_running:
            if not self.current:
                self.b_rec.setChecked(False)
                self.state.setText(_("Start a simulator first."))
                return

            def started(r):
                if isinstance(r, dict) and r.get("ok"):
                    self.rec_running = True
                    self.b_rec.setChecked(True)
                    self.b_rec.setText(_("Stop"))
                    self.rec_timer.start()
                    self.rec_label.setText("● 0 s")
                else:
                    self.b_rec.setChecked(False)
                    self.state.setText(_("Recording only works once the simulator makes sound – play a "
                                         "key once."))
            self.js("window.__fm1sim?__fm1sim.record.start():null", started)
        elif not on and self.rec_running:
            name = f"Simulator {self.current.title if self.current else ''} {dt.datetime.now():%Y-%m-%d %H-%M-%S}.wav"
            name = "".join(c for c in name if c not in '/\\:*?"<>|').replace("  ", " ")
            self.rec_running = False
            self.rec_timer.stop()
            self.b_rec.setChecked(False)
            self.b_rec.setText(_("Record"))

            def stopped(r):
                if isinstance(r, dict) and r.get("ok"):
                    self.rec_label.setText((_("{duration} saved (silent!)") if r.get("peak", 0) < 1e-4 else
                                            _("{duration} saved")).format(duration=fmt_secs(r.get("seconds"))))
                else:
                    self.rec_label.setText(_("Recording failed"))
            self.js(f"window.__fm1sim?__fm1sim.record.stop({json.dumps(name)}):null", stopped)

    def _rec_tick(self):
        def show(r):
            if isinstance(r, dict) and r.get("recording"):
                lvl = min(10, int(r.get("peak", 0) * 10))
                self.rec_label.setText(f"● {fmt_secs(r.get('seconds'))}  {'▮' * lvl}")
        self.js("window.__fm1sim?__fm1sim.record.state():null", show)

    def _speed_tick(self):
        self.js("window.__fm1sim&&__fm1sim.speed?__fm1sim.speed():null", self._show_speed)
        if getattr(self, "_await_power", False):
            self.js("(function(){var p=document.getElementById('poweron');return p?p.hidden:null})()", self._power_state)

    def _power_state(self, on):
        """Melodee simulator: the switch-on area is gone -> status line without “press Switch on”"""
        s = self.current
        if on is True and s is not None and getattr(self, "_await_power", False):
            self._await_power = False
            self.state.setText(_("{title} is running and switched on. Play: on the FM-1 or with the "
                                 "computer keyboard (Z S X D C …).").format(
                                   title=s.title))

    def _show_speed(self, r):
        """Compares the simulator's sound clock with real time (measurement window 4 s). Only warn after two slow
        measurements in a row – a single stutter (e.g. while loading) should not raise an alarm."""
        ratio = r.get("ratio") if isinstance(r, dict) and r.get("running") else None
        if ratio is None:
            self._slow = 0
            self.speed_label.setText("")
            self.speed_label.setToolTip("")
            return
        if ratio < SLOW_RATIO:
            self._slow += 1
        else:
            self._slow = 0
        pct = max(1, min(100, round(ratio * 100)))
        if self._slow >= 2:
            self.speed_label.setText(_("⚠ too slow: {percent} % tempo").format(percent=pct))
            self.speed_label.setStyleSheet("color: #d35400; font-weight: 600;")
            self.speed_label.setToolTip(
                # Translators: {percent} includes the percent sign ("43 %")
                _("The computer manages only {percent} of the required computing power – the simulator "
                  "plays slower, the sound stutters and crackles. This is not the firmware's "
                  "fault.\nRemedy: close other programs, “Open in Firefox” (often faster on the Pi) or "
                  "choose latency “stable” and restart.").format(percent=f"{pct} %"))
        elif ratio >= SLOW_RATIO:
            self.speed_label.setText(_("Real time"))
            self.speed_label.setStyleSheet("color: palette(mid);")
            self.speed_label.setToolTip(_("The computer keeps up: the simulator plays at normal tempo "
                                          "(measured {percent} %).").format(
                                            percent=pct))

    def _download(self, item) -> bool:
        """WAV recordings of this page go directly to ~/FM-1/Aufnahmen (without a dialog)."""
        try:
            page = item.page()
        except Exception:
            page = None
        name = item.downloadFileName() or ""
        if page is not self.page or not name.lower().endswith(".wav"):
            return False
        folder = self.ctx.settings.recordings_dir
        item.setDownloadDirectory(str(folder))
        item.setDownloadFileName(name)
        self.last_recording = folder / name
        item.accept()
        self.state.setText(_("Recording saved: {path}").format(path=folder / name))
        return True

    # ------------------------------------------------------------ Page hooks
    def _latency(self, _i):
        self.cfg["latency"] = self.lat_combo.currentData()
        config.save()

    def activate(self):
        self._show_details()

    def shutdown(self):
        self.rec_timer.stop()
        self.speed_timer.stop()
        self.inputs.close()
        if self._listen_link is not None:
            self._listen_link.remove_listener(self._link_listener)
            self._listen_link = None
        web.remove_download_handler(self._download)
        if self.view:
            from PyQt6 import sip
            for obj in (self.view, self.page):
                if not sip.isdeleted(obj):
                    sip.delete(obj)
            self.view = None


def _make(ctx, window):
    return SimulatorPage(ctx, window)


PAGE = PageSpec(key="simulator", title=_("Simulator"), group="klang", factory=_make,
                icons=("media-playback-start", "applications-multimedia"), order=40,
                description=_("play FM-1 simulators on the computer (FM1 Open built from the official source code, "
                                     "Melodee, online)"),
                keywords=("emulator", "wasm", "firefox", "melodee", "fm1 open", "simulator", "play", "browser"))
