# SPDX-License-Identifier: GPL-3.0-only
"""Gemeinsame Web-Engine für alle eingebetteten Web-Seiten der Suite.

Qt erlaubt pro Prozess nur EIN Profil mit demselben Speicher-Namen. Deshalb gibt es
hier genau eins (``profile()``), mit dem Web-MIDI-Ersatz (webmidi_polyfill.js), der
jede Seite über QWebChannel mit der App verbindet – Editor, Simulator, Online-Editoren.

Bausteine:
  * ``profile()``        – das gemeinsame Profil (lazy, an die QApplication gehängt)
  * ``RoutingPage``      – QWebEnginePage: Links ins Internet -> normaler Browser,
                           Links auf dem lokalen Server -> optionaler router(url) -> bool
  * ``make_view(...)``   – View + Page + Kanal (+ MidiBridge als "fm1midi") in einem Schritt
  * ``add_download_handler(fn)`` – fn(download_request) -> True, wenn erledigt
  * ``shutdown_all()``   – beim Beenden: Views und Seiten VOR dem Profil abbauen
"""
from __future__ import annotations

import weakref
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QFile, QIODevice, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QApplication, QFileDialog

from .. import config

try:
    from PyQt6.QtWebChannel import QWebChannel
    from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineScript
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    HAVE_WEBENGINE = True
except ImportError:          # python3-pyqt6.qtwebengine fehlt
    HAVE_WEBENGINE = False

POLYFILL = Path(__file__).resolve().parents[1] / "web" / "webmidi_polyfill.js"
LOCAL_HOSTS = ("127.0.0.1", "localhost")

_profile = None
_tracked: list[weakref.ref] = []            # (View/Seite) für shutdown_all, Views zuerst
_download_handlers: list[Callable] = []


def _qwebchannel_js() -> str:
    f = QFile(":/qtwebchannel/qwebchannel.js")
    if f.open(QIODevice.OpenModeFlag.ReadOnly):
        return bytes(f.readAll()).decode()
    return ""


def profile():
    """das eine gemeinsame Profil (mit Web-MIDI-Ersatz)."""
    global _profile
    if not HAVE_WEBENGINE:
        raise RuntimeError("Qt WebEngine fehlt (sudo apt install python3-pyqt6.qtwebengine)")
    if _profile is None:
        prof = QWebEngineProfile("fm1-companion", QApplication.instance())
        prof.setPersistentStoragePath(str(config.DATA_DIR / "webengine"))
        prof.setCachePath(str(config.CACHE_DIR / "webengine"))
        prof.downloadRequested.connect(_download)
        script = QWebEngineScript()
        script.setName("fm1-webmidi")
        script.setSourceCode(_qwebchannel_js() + "\n" + POLYFILL.read_text("utf-8"))
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(False)
        prof.scripts().insert(script)
        _profile = prof
    return _profile


def track(*objs):
    """für shutdown_all() merken (Reihenfolge: zuerst übergeben = zuerst abgebaut)."""
    for o in objs:
        _tracked.append(weakref.ref(o))


def shutdown_all():
    """Views und Seiten abbauen, solange das Profil noch lebt (sonst warnt Qt beim Beenden)."""
    from PyQt6 import sip
    views = [r() for r in _tracked]
    _tracked.clear()
    # erst alle Views, dann alle Seiten
    ordered = [o for o in views if o is not None and HAVE_WEBENGINE and isinstance(o, QWebEngineView)] + \
              [o for o in views if o is not None and not (HAVE_WEBENGINE and isinstance(o, QWebEngineView))]
    for o in ordered:
        try:
            if not sip.isdeleted(o):
                sip.delete(o)
        except Exception:                      # noqa: BLE001
            pass


# ------------------------------------------------------------ Downloads
def add_download_handler(fn: Callable, first: bool = True):
    """fn(QWebEngineDownloadRequest) -> True, wenn erledigt. Spätere Handler werden zuerst gefragt."""
    if first:
        _download_handlers.insert(0, fn)
    else:
        _download_handlers.append(fn)


def remove_download_handler(fn: Callable):
    if fn in _download_handlers:
        _download_handlers.remove(fn)


def _download(item):
    for fn in list(_download_handlers):
        try:
            if fn(item):
                return
        except Exception:                      # noqa: BLE001
            continue
    # Standard: in ~/FM-1/Downloads, mit Nachfrage
    folder = config.folder("Downloads")
    name = item.downloadFileName() or "download"
    path, _ = QFileDialog.getSaveFileName(None, "Speichern", str(folder / name))
    if not path:
        item.cancel()
        return
    item.setDownloadDirectory(str(Path(path).parent))
    item.setDownloadFileName(Path(path).name)
    item.accept()


# ------------------------------------------------------------ Seiten
if HAVE_WEBENGINE:
    class RoutingPage(QWebEnginePage):
        """Links ins Internet öffnen im normalen Browser (außer allow_remote=True);
        Navigation auf dem lokalen Server fragt router(url) -> True = normal laden."""

        def __init__(self, parent=None, router: Callable | None = None, allow_remote: bool = False):
            super().__init__(profile(), parent)
            self.router = router
            self.allow_remote = allow_remote

        def acceptNavigationRequest(self, url, nav_type, is_main_frame):
            if not is_main_frame or url.scheme() in ("data", "about", "qrc", "blob"):
                return True
            if url.host() not in LOCAL_HOSTS:
                if self.allow_remote and url.scheme() in ("http", "https"):
                    return True
                if url.scheme() in ("http", "https", "mailto"):
                    QDesktopServices.openUrl(url)
                    return False
                return True
            return self.router(url) if self.router else True

        def createWindow(self, _type):
            # target=_blank / window.open: nur http/https/mailto im normalen Browser öffnen, alles andere
            # (file:, eigene Schemata, javascript: …) verwerfen
            tmp = QWebEnginePage(self.profile(), self)
            tmp.urlChanged.connect(lambda u, p=tmp: (open_new_window_url(u), p.deleteLater()))
            return tmp


def make_view(parent, bridge=None, router: Callable | None = None, allow_remote: bool = False,
              page_class=None):
    """-> (view, page, channel). bridge: MidiBridge (wird als "fm1midi" angemeldet) oder None."""
    cls = page_class or RoutingPage
    page = cls(parent, router=router, allow_remote=allow_remote) if cls is RoutingPage else cls(profile(), parent)
    channel = QWebChannel(page)
    if bridge is not None:
        channel.registerObject("fm1midi", bridge)
    page.setWebChannel(channel)
    view = QWebEngineView(parent)
    view.setPage(page)
    track(view, page)
    return view, page, channel


EXTERNAL_SCHEMES = ("http", "https", "mailto")


def external_link_allowed(url) -> bool:
    """darf diese Adresse aus einer Webseite heraus im Standardprogramm geöffnet werden?"""
    u = url if isinstance(url, QUrl) else QUrl(str(url))
    return u.isValid() and u.scheme().lower() in EXTERNAL_SCHEMES


def open_new_window_url(url) -> bool:
    """neues Fenster einer Webseite: nur http/https/mailto über QDesktopServices -> True, wenn geöffnet."""
    if not external_link_allowed(url):
        return False
    QDesktopServices.openUrl(url if isinstance(url, QUrl) else QUrl(str(url)))
    return True


def placeholder_html(title: str, text: str) -> str:
    return (f"<html><body style='font-family:sans-serif;padding:3em;color:#888'><h2>{title}</h2>"
            f"<p>{text}</p></body></html>")


def open_external(url: str):
    QDesktopServices.openUrl(QUrl(url))
