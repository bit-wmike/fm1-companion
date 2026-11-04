# SPDX-License-Identifier: GPL-3.0-only
"""Der Studio-Zwilling in QtWebEngine (Teil von studio.WebEngineBackend).

Eine unsichtbare QWebEnginePage lädt sims/_studio/studio.html vom lokalen Server der App; das QWebChannel-Objekt
"studio" (StudioBridge) trägt Befehle als JSON hin und Antworten/Ereignisse zurück. Binärdaten (Flash, PCM) gehen
über Einmal-Adressen des Servers (editor_host.Exchange). ``WebTwin.call`` ist aus jedem Thread aufrufbar: im GUI-
Thread wartet eine lokale Ereignisschleife, in anderen Threads ein threading.Event.
"""
from __future__ import annotations

import itertools
import json
import threading
import time
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QEventLoop, QObject, QThread, QTimer, QUrl, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import QApplication


from .i18n import _
from .studio import StudioError


class StudioBridge(QObject):
    command = pyqtSignal(str)

    def __init__(self, owner):
        super().__init__()
        self._owner = owner

    @pyqtSlot(str)
    def reply(self, s: str):
        self._owner._on_reply(s)

    @pyqtSlot()
    def ready(self):
        self._owner._on_ready()


class WebTwin(QObject):
    _post = pyqtSignal(str)                    # aus jedem Thread -> GUI-Thread -> bridge.command

    def __init__(self, server, build, on_midi: Callable | None = None, page=None):
        super().__init__()
        app = QApplication.instance()
        if app is None:
            raise StudioError(_("The studio needs a running Qt application"))
        if self.thread() is not app.thread():
            self.moveToThread(app.thread())
        self.server = server
        self.build = build
        self.on_midi = on_midi
        self.page = page
        self.bridge = StudioBridge(self)
        self.bridge.moveToThread(app.thread())
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._waits: dict[int, dict] = {}
        self._ready = threading.Event()
        self._post.connect(self.bridge.command.emit)
        self.console: list[str] = []
        self.on_event: Callable | None = None      # {ev: "ui", ...} von der Bedienfläche (GUI-Thread)

    # ------------------------------------------------------------- Laden
    def load(self, timeout: float = 30):
        from PyQt6.QtWebChannel import QWebChannel
        from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
        from .ui import web
        if self.page is None:
            twin = self

            class _Page(QWebEnginePage):
                def javaScriptConsoleMessage(self, level, msg, line, src):
                    twin.console.append(f"{src}:{line}: {msg}")

            self.page = _Page(web.profile(), None)
            web.track(self.page)
        self.page.settings().setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
        self.channel = QWebChannel(self.page)
        self.channel.registerObject("studio", self.bridge)
        self.page.setWebChannel(self.channel)
        self.page.load(QUrl(self.server.sim_url("_studio", "studio.html")))
        if not self._wait(lambda: self._ready.is_set(), timeout):
            raise StudioError(_("Studio page does not load: {details}").format(details=" | ".join(self.console[-5:])))

    def close(self):
        p, self.page = self.page, None
        if p is not None:
            from PyQt6 import sip
            if not sip.isdeleted(p):
                sip.delete(p)                  # sofort: das gemeinsame Profil darf keine Seite überleben

    # ------------------------------------------------------------- Warten
    def _wait(self, done: Callable[[], bool], timeout: float) -> bool:
        app = QApplication.instance()
        if QThread.currentThread() is app.thread():
            loop = QEventLoop()
            t = QTimer()
            t.setInterval(2)
            t.timeout.connect(lambda: loop.quit() if done() else None)
            t.start()
            QTimer.singleShot(int(timeout * 1000), loop.quit)
            if not done():
                loop.exec()
            t.stop()
            return done()
        ev = threading.Event()
        end = time.monotonic() + timeout
        while not done():
            if time.monotonic() > end:
                return False
            ev.wait(0.002)
        return True

    def _on_ready(self):
        self._ready.set()

    def _on_reply(self, s: str):
        try:
            m = json.loads(s)
        except ValueError:
            return
        if m.get("ev") == "midi":
            if self.on_midi:
                self.on_midi(m["data"])
            return
        if m.get("ev"):
            if self.on_event:
                try:
                    self.on_event(m)
                except Exception:              # noqa: BLE001 – die Seite darf an einem Fehler nicht hängen
                    pass
            return
        w = self._waits.get(m.get("id"))
        if w is not None:
            w["reply"] = m

    # ------------------------------------------------------------- Befehle
    def call(self, op: str, timeout: float = 60, sink=None, **args) -> dict:
        i = next(self._ids)
        got: dict[str, bytes] = {}
        keys = []
        for k, v in list(args.items()):
            if isinstance(v, (bytes, bytearray, memoryview)):
                args[k] = {"blob": self.server.blob_url(bytes(v))}
        if sink is None:
            def sink(key, body, got=got):
                got[key] = got.get(key, b"") + body
        url, key = self.server.sink_url(sink)
        keys.append(key)
        w: dict = {}
        self._waits[i] = w
        try:
            self._post.emit(json.dumps({"id": i, "op": op, "sink": url, **args}))
            if not self._wait(lambda: "reply" in w, timeout):
                raise StudioError(_("Studio does not respond ({operation})").format(operation=op))
        finally:
            self._waits.pop(i, None)
            for k in keys:
                self.server.exchange.remove(k)
        r = w["reply"]
        if not r.get("ok"):
            raise StudioError(r.get("error", _("Error in the studio")))
        return {k: (got.get(v["sink"], b"") if isinstance(v, dict) and "sink" in v else v) for k, v in r.items()}

    def post(self, op: str, **args):
        """Befehl ohne Antwort abzuwarten (Antwort wird verworfen) – aus jedem Thread."""
        self._post.emit(json.dumps({"id": 0, "op": op, "nowait": True, **args}))

    def render(self, path: Path, params: dict) -> dict:
        path = Path(path)
        f = open(path, "wb")
        lock = threading.Lock()

        def sink(_key, body):
            with lock:
                f.write(body)
        try:
            return self.call("render", timeout=3600, sink=sink, **params)
        finally:
            f.close()
