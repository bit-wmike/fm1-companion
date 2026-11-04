# SPDX-License-Identifier: GPL-3.0-only
"""Simulator: FM-1-Firmwares im Rechner spielen.

Mitgelieferte Simulatoren sind der C-Code der Firmware als WebAssembly (kein CPU-Emulator) – sie laufen
im eingebetteten Browser oder, schneller auf dem Pi, in Firefox. Dazu Online-Simulatoren der Firmware-Liste.
MIDI vom FM-1 (Tasten/Knöpfe) und von weiteren MIDI-Eingängen geht in den Simulator; der Ton lässt sich
als WAV nach ~/FM-1/Aufnahmen aufnehmen.
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
from ...i18n import _

LEFT_MIN = 380          # linke Spalte: nie schmaler (Knöpfe, Adressfeld, Beschreibung)


class WrapLabel(QLabel):
    """umbrechendes Label, das die Spalte nicht breiter macht als sie ist (lange Wörter werden umbrochen)."""

    def __init__(self, *a):
        super().__init__(*a)
        self.setWordWrap(True)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def minimumSizeHint(self) -> QSize:
        return QSize(0, super().minimumSizeHint().height())


def _breakable(text: str) -> str:
    """lange Zeichenketten (URLs, Commit-Hashes, Lizenz-Kürzel) an / - _ . @ umbrechbar machen"""
    return re.sub(r"([/\-_.@])(?=\S)", "\\1\u200b", text)


HOST_JS = sims.DIR / "_host" / "fm1sim-host.js"
LATENCY = [("interactive", "niedrig (direkt spielen)"), ("balanced", "ausgewogen"),
           ("playback", "stabil (große Puffer, gegen Knackser)")]
SLOW_RATIO = 0.93      # Ton-Uhr / echte Uhr darunter: der Rechner schafft den Simulator nicht in Echtzeit
SETTINGS = {"latency": "interactive", "recent": [], "inputs": [], "fm1_keys": True, "last": ""}

if web.HAVE_WEBENGINE:
    from PyQt6.QtWebEngineCore import QWebEngineScript, QWebEngineSettings

    class SimPage(web.RoutingPage):
        """sammelt Konsolen-Meldungen (Fehlersuche, Tests)."""
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
    _midi = pyqtSignal(str, list)          # (Quelle, Bytes) aus MIDI-Threads in den GUI-Thread

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
        self.popen = subprocess.Popen          # (für Tests austauschbar)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(6)
        self.split = split
        lay.addWidget(split)

        # ---- links: Bibliothek
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.addWidget(heading("Simulator", "FM-1-Firmwares im Rechner spielen – der echte Code der Firmware als "
                                          "WebAssembly. Am Pi läuft er in Firefox oft flüssiger."))
        # „Starten“ groß oben; Doppelklick/Enter in der Liste startet ebenfalls; „Stoppen“ nur, wenn einer läuft
        row = QHBoxLayout()
        self.b_start = QPushButton(icon("media-playback-start"), "Starten")
        self.b_start.setProperty("variant", "primary")
        self.b_start.setMinimumHeight(36)
        self.b_start.setToolTip("Gewählten Simulator hier in der App laden (Enter oder Doppelklick in der Liste)")
        self.b_start.clicked.connect(self.start)
        self.b_stop = QPushButton(icon("media-playback-stop"), "Stoppen")
        self.b_stop.setToolTip("Simulator beenden – gibt Rechenleistung und Ton frei. Ohne Stoppen läuft er weiter, "
                               "auch wenn du die Seite wechselst (wie ein eingeschaltetes Instrument).")
        self.b_stop.setEnabled(False)
        self.b_stop.clicked.connect(self.stop)
        row.addWidget(self.b_start, 2)
        row.addWidget(self.b_stop, 1)
        ll.addLayout(row)
        self.list = QListWidget()
        self.list.setMinimumHeight(200)
        self.list.currentItemChanged.connect(lambda *_: self._show_details())
        self.list.itemActivated.connect(lambda *_: self.start())
        self.list.itemDoubleClicked.connect(lambda *_: self.start())
        ll.addWidget(self.list, 1)
        self.details = WrapLabel()
        self.details.setTextFormat(Qt.TextFormat.RichText)
        self.details.setOpenExternalLinks(True)
        ll.addWidget(self.details)
        self.b_ff = QPushButton(icon("firefox", "internet-web-browser"), "In Firefox öffnen")
        self.b_ff.setToolTip("Im eigenen Firefox-Fenster öffnen (oft flüssiger am Pi). MIDI vom FM-1 kommt "
                             "auch dort an, solange die App läuft.")
        self.b_ff.clicked.connect(self.open_firefox)
        ll.addWidget(self.b_ff)
        urow = QHBoxLayout()
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("Adresse eines Online-Simulators (https://…)")
        self.url_edit.returnPressed.connect(self._open_typed)
        b_url = QPushButton(icon("go-jump", "internet-web-browser"), "Öffnen")
        b_url.clicked.connect(self._open_typed)
        urow.addWidget(self.url_edit, 1)
        urow.addWidget(b_url)
        ll.addLayout(urow)

        # MIDI + Leistung
        ll.addWidget(QLabel("<b>MIDI in den Simulator</b>"))
        self.cb_fm1 = QCheckBox("Tasten und Knöpfe des FM-1")
        self.cb_fm1.setChecked(bool(self.cfg.get("fm1_keys", True)))
        self.cb_fm1.setToolTip("Was du am angeschlossenen FM-1 spielst, spielt im Simulator mit "
                               "(Kanal-Nachrichten und Takt).")
        self.cb_fm1.toggled.connect(self._fm1_toggled)
        ll.addWidget(self.cb_fm1)
        prow = QHBoxLayout()
        self.port_combo = QComboBox()
        self.port_combo.setToolTip("weiterer MIDI-Eingang, z. B. ein USB-Keyboard")
        self.port_combo.activated.connect(self._port_chosen)
        b_scan = QPushButton(icon("view-refresh"), "Suchen")
        b_scan.setToolTip("MIDI-Eingänge neu suchen")
        b_scan.clicked.connect(self.refresh_ports)
        prow.addWidget(self.port_combo, 1)
        prow.addWidget(b_scan)
        ll.addLayout(prow)
        lrow = QHBoxLayout()
        lrow.addWidget(QLabel("Latenz:"))
        self.lat_combo = QComboBox()
        for k, t in LATENCY:
            self.lat_combo.addItem(t, k)
        self.lat_combo.setCurrentIndex(max(0, self.lat_combo.findData(self.cfg.get("latency"))))
        self.lat_combo.setToolTip("Knackst es? „stabil“ wählen – der Ton kommt dann etwas später. "
                                  "Gilt beim nächsten Start (auch in Firefox).")
        self.lat_combo.activated.connect(self._latency)
        lrow.addWidget(self.lat_combo, 1)
        ll.addLayout(lrow)
        self.midi_state = WrapLabel()
        self.midi_state.setStyleSheet("color: palette(mid);")
        ll.addWidget(self.midi_state)
        for c in (self.port_combo, self.lat_combo):
            c.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            c.setMinimumContentsLength(8)
        for lab in left.findChildren(QLabel):          # (auch der Seitenkopf: nie breiter als die Spalte)
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

        # ---- rechts: Leiste + Simulator
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        bar.setContentsMargins(8, 6, 8, 0)
        self.badge = QLabel()
        self.state = QLabel("Wähle links einen Simulator.")
        self.state.setWordWrap(True)
        bar.addWidget(self.badge)
        bar.addWidget(self.state, 1)
        self.b_rec = QPushButton(icon("media-record"), "Aufnehmen")
        self.b_rec.setToolTip("Ton des Simulators als WAV nach ~/FM-1/Aufnahmen aufnehmen (Strg+R)")
        self.b_rec.setShortcut("Ctrl+R")
        self.b_rec.setCheckable(True)
        self.b_rec.clicked.connect(self.toggle_record)
        self.b_rec.setEnabled(False)                  # erst, wenn ein Simulator läuft
        self.rec_label = QLabel("")
        self.speed_label = QLabel("")                 # ehrliche Tempo-Anzeige: hält der Rechner Echtzeit?
        self._slow = 0
        bar.addWidget(self.speed_label)
        bar.addWidget(self.rec_label)
        bar.addWidget(self.b_rec)
        rl.addLayout(bar)
        if not web.HAVE_WEBENGINE:
            msg = QLabel("Für den eingebauten Simulator fehlt Qt WebEngine (sudo apt install "
                         "python3-pyqt6.qtwebengine). „In Firefox öffnen“ geht trotzdem.")
            msg.setWordWrap(True)
            rl.addWidget(msg, 1)
        else:
            self.bridge = MidiBridge(self)
            self.gate = sims.OutGate()                   # pro geladener Seite neu gesetzt (start)
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
        self._placeholder("Noch kein Simulator gestartet",
                          "Links einen Simulator wählen und „Starten“ drücken. Mit angeschlossenem FM-1 spielen "
                          "seine Tasten im Simulator mit.")
        ctx.changed.connect(self._dev_changed)
        self._dev_changed()

    # ------------------------------------------------------------ Web-Seite
    def _replace_page(self):
        """eigene Seite (Konsole mitschneiden, Ton ohne Klick erlauben, Host-Skript für Online-Seiten)."""
        old = self.page
        page = SimPage(self, allow_remote=True)
        from PyQt6.QtWebChannel import QWebChannel
        self.channel = QWebChannel(page)            # der alte Kanal gehört der alten Seite
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
        if hasattr(page, "loadingChanged"):          # Qt >= 6.2: erkennt auch Fehlerseiten sicher
            page.loadingChanged.connect(self._loading)
        else:
            page.loadFinished.connect(self._loaded)
        if old is not None:
            old.deleteLater()

    def _split_moved(self, *_):
        self.cfg["left_width"] = self.split.sizes()[0]
        try:
            config.save()
        except OSError:
            pass

    def _placeholder(self, title, text):
        """Platzhalter in den Farben des Themas (hell/dunkel) statt einer weißen Fläche."""
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
            self._placeholder("Noch kein Simulator gestartet",
                              "Links einen Simulator wählen und „Starten“ drücken.")

    # ------------------------------------------------------------ Bibliothek
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
        head("MITGELIEFERT (offline)")
        if not local:
            self.list.addItem("Keine Simulatoren mitgeliefert – Installation unvollständig?")
        for s in local:
            self._add(s, f"{s.title} · {s.version}", icon("media-playback-start", "audio-x-generic"))
        recent = [r for r in self.cfg.get("recent", []) if isinstance(r, dict) and r.get("url")]
        if recent:
            head("ZULETZT GEÖFFNET (online)")
            for r in recent[:6]:
                s = sims.online(r["url"], r.get("title", ""), r.get("kind", "emulator"))
                self._add(s, f"{s.title}  [online]", icon("internet-web-browser"))
        head("ONLINE-SIMULATOREN (brauchen Internet)")
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
                                 f"<small>{_breakable(s.url)}<br>Läuft auf der Seite des Entwicklers; braucht Internet. MIDI: "
                                 f"in der Seite den Eingang „FM-1 (Tasten)“ wählen.</small>")
            self.b_ff.setEnabled(True)
            return
        src = f"{s.source_repo}/tree/{s.commit}" if s.commit else s.source_repo
        self.details.setText(
            f"<b>{s.title}</b> · Version {s.version}<br>{s.description}<br><small>Quelle: <a href='{src}'>"
            f"{s.source_repo.replace('https://github.com/', '')}</a> · Lizenz: {_breakable(s.license)} · "
            f"{fmt_size(s.size)}</small>")
        # Build-Einzelheiten (Commit, Werkzeugkette, Datum) nur im Tooltip
        self.details.setToolTip(f"Stand: {s.ref} ({s.commit})\nGebaut: {s.build_date[:10]} mit {s.toolchain}\n"
                                f"Lizenz: {s.license}")
        self.b_ff.setEnabled(self.server is not None)

    # ------------------------------------------------------------ Starten / Stoppen
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
            self.state.setText(f"{s.title}: Projektseite im Browser geöffnet.")
            return
        if not self.view:
            return
        if not s.online:
            bad = sims.verify(s)
            if bad:
                self.state.setText("Simulator beschädigt: " + ", ".join(bad) + " – bitte die App neu installieren.")
                return
        if self.rec_running:
            self.toggle_record(False)
        self.current = s
        self.cfg["last"] = s.key
        config.save()
        self.page.messages.clear()
        self.bridge.blocked = 0
        # Editoren dürfen (nach einmaliger Zustimmung je Herkunft) mit dem FM-1 reden, Simulatoren nicht
        if s.online and s.kind == "editor":
            origin = sims.origin_of(s.url)
            if origin and not sims.origin_trusted(origin) and self.ask_origin(origin, s.title):
                sims.trust_origin(origin)
        self.gate = sims.OutGate(s.kind if s.online else "", self._url_for(s) if s.online else "")
        self.bridge.out_filter = self._out_allowed
        self.badge.setText("<b style='color:#c80'>● ONLINE</b>" if s.online else "<b style='color:#2a2'>● lokal</b>")
        self.state.setText(f"Lade {s.title} …")
        self.b_stop.setEnabled(True)
        self.b_rec.setEnabled(True)
        self.speed_timer.stop()
        self._show_speed(None)
        self.view.load(QUrl(self._url_for(s)))

    def stop(self):
        if self.rec_running:
            self.toggle_record(False)
        if self.current and self.view:
            self.state.setText(f"{self.current.title} beendet.")
        self.current = None
        self.b_rec.setEnabled(False)
        self.speed_timer.stop()
        self._show_speed(None)
        self.badge.setText("")
        self.b_stop.setEnabled(False)
        self._placeholder("Simulator gestoppt", "Links einen Simulator wählen und „Starten“ drücken.")

    def _loading(self, info):
        from PyQt6.QtWebEngineCore import QWebEngineLoadingInfo
        st = info.status()
        if st == QWebEngineLoadingInfo.LoadStatus.LoadStartedStatus or info.url().scheme() in ("data", "about"):
            return
        dom, E = info.errorDomain(), QWebEngineLoadingInfo.ErrorDomain
        failed = st == QWebEngineLoadingInfo.LoadStatus.LoadFailedStatus or dom not in (
            E.NoErrorDomain, E.HttpStatusCodeDomain) or (dom == E.HttpStatusCodeDomain and info.errorCode() >= 400)
        if st == QWebEngineLoadingInfo.LoadStatus.LoadStoppedStatus and not failed:
            return
        self._loaded(not failed)

    def _loaded(self, ok: bool):
        s = self.current
        if not s:
            return
        if not ok:
            if s.online:
                self._placeholder("Keine Verbindung", f"{s.title} liegt im Internet ({s.url}) und ist gerade nicht "
                                  "erreichbar. Die mitgelieferten Simulatoren gehen auch offline.")
                self.state.setText("Online-Simulator nicht erreichbar – offline?")
            else:
                self.state.setText("Simulator konnte nicht geladen werden.")
            return
        hint = {"melodee": "„Switch on“ drücken oder am FM-1 eine Taste spielen. Computertastatur: Z S X D C …",
                "fm1-open": "Einfach eine Taste spielen (Maus, Computertastatur Z S X …, oder am FM-1)."}.get(s.key, "")
        self.state.setText(f"{s.title} läuft{' (online)' if s.online else ''}. {hint}")
        # Melodee wartet auf „Switch on“: sobald eingeschaltet, den Hinweis aus der Statuszeile nehmen
        self._await_power = s.key == "melodee"
        self._slow = 0
        self.speed_timer.start()

    def open_with(self, url: str = "", title: str = "", kind: str = "emulator", **_):
        """Sprung von anderen Seiten (Katalog): Online-Emulator/-Editor laden."""
        if not url:
            return
        if not url.startswith(("http://", "https://")):
            self.state.setText(f"Ungültige Adresse: {url}")
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
            self.state.setText("Firefox nicht gefunden – sudo apt install firefox-esr")
            return
        try:
            self.popen(cmd, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            self.state.setText(f"Firefox ließ sich nicht starten: {e}")
            return
        s = self.selected()
        self.state.setText(f"{s.title} in Firefox geöffnet. MIDI vom FM-1 kommt dort an, solange die App läuft"
                           " – der Simulator hier kann gestoppt werden.")

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
        self.port_combo.addItem("kein weiterer MIDI-Eingang", "")
        for n in names:
            self.port_combo.addItem(icon("audio-midi"), n, n)
        if not names:
            self.port_combo.setItemText(0, "kein weiterer MIDI-Eingang gefunden")
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
            self.state.setText("MIDI-Eingang lässt sich nicht öffnen: " + "; ".join(errs))
        self._update_ports()

    def _update_ports(self):
        ports = []
        if self.ctx.link is not None:
            ports += [{"id": "fm1-keys", "name": "FM-1 (Tasten)", "type": "input"},
                      {"id": "fm1-out", "name": "FM-1", "type": "output"}]
        for n in self.inputs.open:
            ports.append({"id": "sys:" + n, "name": n, "type": "input"})
        if self.view:
            self.bridge.set_extra_ports(ports)
        srcs = (["FM-1"] if self._listen_link else []) + list(self.inputs.open)
        self.midi_state.setText(("MIDI aktiv von: " + ", ".join(srcs)) if srcs else
                                "Kein MIDI-Eingang aktiv – FM-1 anschließen oder ein Keyboard wählen. "
                                "Spielen geht auch mit Maus und Computertastatur.")

    def _on_midi(self, source: str, data: list):
        if not sims.page_midi(data):
            return
        self.midi_count += 1
        if self.server is not None:
            self.server.relay.publish(data)            # Firefox
        if not self.view or not self.current:
            return
        if self.current.online:                        # Online-Seiten: über ihre Web-MIDI-Auswahl
            self.bridge.inject("fm1-keys" if source == "fm1" else source, data)
        else:
            self.page.runJavaScript(f"window.__fm1sim&&__fm1sim.midi({json.dumps(data)})")

    def ask_origin(self, origin: str, title: str = "") -> bool:
        """einmalige Rückfrage: darf diese Web-Seite Editor-Daten (SysEx) an das echte FM-1 senden?"""
        from PyQt6.QtWidgets import QMessageBox
        box = QMessageBox(QMessageBox.Icon.Warning, "Online-Editor mit dem FM-1 verbinden?",
                          f"<b>{html.escape(title or origin)}</b> wird von <b>{html.escape(origin)}</b> geladen – "
                          "einer Seite im Internet, die nicht zu dieser App gehört.", parent=self)
        box.setInformativeText(
            "Erlaubst du es, darf diese Seite Editor-Befehle (SysEx) an dein FM-1 senden und damit Klänge, "
            "Presets und Einstellungen auf dem Gerät ändern. Firmware-Update und Bootmodus bleiben immer gesperrt.\n\n"
            "Ohne Erlaubnis gehen nur Noten, Controller und Takt ans FM-1. Die Erlaubnis gilt nur für genau diese "
            "Adresse; wechselt die Seite zu einer anderen Adresse, gilt wieder der sichere Filter.")
        yes = box.addButton("Erlauben (für diese Adresse merken)", QMessageBox.ButtonRole.AcceptRole)
        no = box.addButton("Nur Noten erlauben", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(no)
        box.exec()
        return box.clickedButton() is yes

    def _out_allowed(self, data) -> bool:
        """Filter für jede Nachricht der Seite – prüft die AKTUELLE Adresse der Seite, nicht nur die beim Start."""
        try:
            cur = self.page.url().toString() if self.page is not None else ""
        except RuntimeError:                           # Seite schon abgebaut
            cur = ""
        return self.gate.allowed(data, cur)

    def _page_out(self, port_id: str, data: list):
        """MIDI aus einer Seite an das FM-1 (vorher durch bridge.out_filter = self._out_allowed / OutGate)."""
        if port_id == "fm1-out" and self.ctx.link is not None:
            self.ctx.link.send_midi(data)

    def _blocked(self, n: int):
        """die Seite wollte etwas ans FM-1 schicken, das der Sicherheitsfilter nicht durchlässt – nicht still schlucken."""
        if n not in (1, 10, 100) and n % 1000:
            return
        s = self.current
        if s is not None and s.kind == "editor" and self.gate.editor_access(self.page.url().toString()):
            why = "Firmware-Update und Bootmodus sind aus Web-Seiten gesperrt"
        elif s is not None and s.kind == "editor":
            why = ("diese Adresse hat keine Erlaubnis für Editor-Befehle – nur Noten, Controller und Takt; "
                   "die Erlaubnis fragt die App beim Öffnen des Online-Editors")
        else:
            why = "Simulatoren dürfen nur Noten, Controller und Takt ans FM-1 schicken – für Geräte-Zugriff die Seite „Editor“"
        self.state.setText(f"{n} Nachricht{'en' if n != 1 else ''} der Seite ans FM-1 zurückgehalten ({why}).")

    # ------------------------------------------------------------ Aufnahme
    def js(self, code, cb):
        if self.view:
            self.page.runJavaScript(code, cb)

    def toggle_record(self, on: bool = False):
        if on and not self.rec_running:
            if not self.current:
                self.b_rec.setChecked(False)
                self.state.setText("Erst einen Simulator starten.")
                return

            def started(r):
                if isinstance(r, dict) and r.get("ok"):
                    self.rec_running = True
                    self.b_rec.setChecked(True)
                    self.b_rec.setText("Stopp")
                    self.rec_timer.start()
                    self.rec_label.setText("● 0 s")
                else:
                    self.b_rec.setChecked(False)
                    self.state.setText("Aufnahme geht erst, wenn der Simulator Ton macht – einmal eine Taste spielen.")
            self.js("window.__fm1sim?__fm1sim.record.start():null", started)
        elif not on and self.rec_running:
            name = f"Simulator {self.current.title if self.current else ''} {dt.datetime.now():%Y-%m-%d %H-%M-%S}.wav"
            name = "".join(c for c in name if c not in '/\\:*?"<>|').replace("  ", " ")
            self.rec_running = False
            self.rec_timer.stop()
            self.b_rec.setChecked(False)
            self.b_rec.setText("Aufnehmen")

            def stopped(r):
                if isinstance(r, dict) and r.get("ok"):
                    self.rec_label.setText(f"{fmt_secs(r.get('seconds'))} gespeichert"
                                           + (" (still!)" if r.get("peak", 0) < 1e-4 else ""))
                else:
                    self.rec_label.setText("Aufnahme fehlgeschlagen")
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
        """Melodee-Simulator: die Einschalt-Fläche ist weg -> Statuszeile ohne „Switch on drücken“"""
        s = self.current
        if on is True and s is not None and getattr(self, "_await_power", False):
            self._await_power = False
            self.state.setText(f"{s.title} läuft und ist eingeschaltet. Spielen: am FM-1 oder mit der "
                               "Computertastatur (Z S X D C …).")

    def _show_speed(self, r):
        """Vergleicht die Ton-Uhr des Simulators mit der echten Zeit (Messfenster 4 s). Erst nach zwei langsamen
        Messungen in Folge warnen – ein einzelner Ruckler (z. B. beim Laden) soll nicht Alarm schlagen."""
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
            self.speed_label.setText(f"⚠ zu langsam: {pct} % Tempo")
            self.speed_label.setStyleSheet("color: #d35400; font-weight: 600;")
            self.speed_label.setToolTip(
                f"Der Rechner schafft nur {pct} % der nötigen Rechenleistung – der Simulator spielt langsamer, "
                "der Ton stockt und knackt. Das liegt nicht an der Firmware.\nAbhilfe: andere Programme schließen, "
                "„In Firefox öffnen“ (am Pi oft schneller) oder Latenz „stabil“ wählen und neu starten.")
        elif ratio >= SLOW_RATIO:
            self.speed_label.setText("Echtzeit")
            self.speed_label.setStyleSheet("color: palette(mid);")
            self.speed_label.setToolTip("Der Rechner hält mit: der Simulator spielt in normalem Tempo "
                                        f"(gemessen {pct} %).")

    def _download(self, item) -> bool:
        """WAV-Aufnahmen dieser Seite direkt nach ~/FM-1/Aufnahmen (ohne Dialog)."""
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
        self.state.setText(f"Aufnahme gespeichert: {folder / name}")
        return True

    # ------------------------------------------------------------ Seiten-Hooks
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
