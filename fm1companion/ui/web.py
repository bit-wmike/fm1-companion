# SPDX-License-Identifier: GPL-3.0-only
"""Shared web engine for all embedded web pages of the suite.

Qt only allows ONE profile with the same storage name per process. That's why there is
exactly one here (``profile()``), with the Web MIDI replacement (webmidi_polyfill.js), which
connects every page to the app via QWebChannel – editor, simulator, online editors.

Building blocks:
  * ``profile()``        – the shared profile (lazy, attached to the QApplication)
  * ``RoutingPage``      – QWebEnginePage: links to the internet -> normal browser,
                           links on the local server -> optional router(url) -> bool
  * ``make_view(...)``   – view + page + channel (+ MidiBridge as "fm1midi") in one step
  * ``add_download_handler(fn)`` – fn(download_request) -> True if handled
  * ``shutdown_all()``   – on exit: tear down views and pages BEFORE the profile
"""
from __future__ import annotations

import weakref
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QFile, QIODevice, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QApplication, QFileDialog

from .. import config
from ..i18n import _

try:
    from PyQt6.QtWebChannel import QWebChannel
    from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineScript
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    HAVE_WEBENGINE = True
except ImportError:          # python3-pyqt6.qtwebengine missing
    HAVE_WEBENGINE = False

POLYFILL = Path(__file__).resolve().parents[1] / "web" / "webmidi_polyfill.js"
LOCAL_HOSTS = ("127.0.0.1", "localhost")

_profile = None
_tracked: list[weakref.ref] = []            # (view/page) for shutdown_all, views first
_download_handlers: list[Callable] = []


def _qwebchannel_js() -> str:
    f = QFile(":/qtwebchannel/qwebchannel.js")
    if f.open(QIODevice.OpenModeFlag.ReadOnly):
        return bytes(f.readAll()).decode()
    return ""


def profile():
    """the one shared profile (with Web MIDI replacement)."""
    global _profile
    if not HAVE_WEBENGINE:
        raise RuntimeError(_("Qt WebEngine is missing (sudo apt install python3-pyqt6.qtwebengine)"))
    if _profile is None:
        try:                                   # "Clear web editor data" requested while it was open: now
            from .. import storage
            storage.apply_pending_webdata_clear()
        except Exception:                      # noqa: BLE001 – never stop the browser from starting
            pass
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


def profile_open() -> bool:
    """has the built-in browser opened its storage in this process?"""
    return _profile is not None


def clear_site_data() -> bool:
    """Clear the web editors' site data (cookies, local storage, IndexedDB, service workers …).
    -> True: cleared now. False: the browser has its storage open (Chromium keeps the files open as long as the
    profile lives, deleting them underneath is unsafe): cookies and visited links are cleared right away, the rest
    at the next start, before the browser opens it (works the same with Qt 6.4 and 6.8)."""
    from .. import storage
    if _profile is None:
        res = storage.clear_webdata_now()
        if res.errors:
            raise OSError(res.errors[0])
        return True
    try:
        _profile.cookieStore().deleteAllCookies()
        _profile.clearAllVisitedLinks()
    except Exception:                          # noqa: BLE001 – the rest still happens at the next start
        pass
    storage.request_webdata_clear()
    return False


def track(*objs):
    """remember for shutdown_all() (order: handed over first = torn down first)."""
    for o in objs:
        _tracked.append(weakref.ref(o))


def shutdown_all():
    """Tear down views and pages while the profile is still alive (otherwise Qt warns on exit)."""
    from PyQt6 import sip
    views = [r() for r in _tracked]
    _tracked.clear()
    # first all views, then all pages
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
    """fn(QWebEngineDownloadRequest) -> True if handled. Later handlers are asked first."""
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
    # default: into ~/FM-1/Downloads, with a prompt
    folder = config.folder("Downloads")
    name = item.downloadFileName() or "download"
    path, _filter = QFileDialog.getSaveFileName(None, _("Save"), str(folder / name))
    if not path:
        item.cancel()
        return
    item.setDownloadDirectory(str(Path(path).parent))
    item.setDownloadFileName(Path(path).name)
    item.accept()


# ------------------------------------------------------------ Pages
if HAVE_WEBENGINE:
    class RoutingPage(QWebEnginePage):
        """Open links to the internet in the normal browser (unless allow_remote=True);
        navigation on the local server asks router(url) -> True = load normally."""

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
            # target=_blank / window.open: only open http/https/mailto in the normal browser, discard everything
            # else (file:, custom schemes, javascript: …)
            tmp = QWebEnginePage(self.profile(), self)
            tmp.urlChanged.connect(lambda u, p=tmp: (open_new_window_url(u), p.deleteLater()))
            return tmp


def make_view(parent, bridge=None, router: Callable | None = None, allow_remote: bool = False,
              page_class=None):
    """-> (view, page, channel). bridge: MidiBridge (registered as "fm1midi") or None."""
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
    """may this address be opened from a web page in the default application?"""
    u = url if isinstance(url, QUrl) else QUrl(str(url))
    return u.isValid() and u.scheme().lower() in EXTERNAL_SCHEMES


def open_new_window_url(url) -> bool:
    """new window of a web page: only http/https/mailto via QDesktopServices -> True if opened."""
    if not external_link_allowed(url):
        return False
    QDesktopServices.openUrl(url if isinstance(url, QUrl) else QUrl(str(url)))
    return True


def placeholder_html(title: str, text: str) -> str:
    return (f"<html><body style='font-family:sans-serif;padding:3em;color:#888'><h2>{title}</h2>"
            f"<p>{text}</p></body></html>")


def open_external(url: str):
    QDesktopServices.openUrl(QUrl(url))
