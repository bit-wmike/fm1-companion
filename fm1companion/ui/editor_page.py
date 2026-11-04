# SPDX-License-Identifier: GPL-3.0-only
"""The original web editor of the installed firmware, embedded in the app."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

import os

from .. import config, editor_host, firmware
from ..bridge import MidiBridge
from . import web
from .common import icon, run_job
from ..i18n import _

HAVE_WEBENGINE = web.HAVE_WEBENGINE


class EditorPage(QWidget):
    navigate = pyqtSignal(str)          # open a page of the app (e.g. "firmware")

    def __init__(self, ctx, server: editor_host.LocalServer, parent=None):
        super().__init__(parent)
        self.current = None             # (spec, tag, demo) of the loaded editor
        self.ctx = ctx
        self.server = server
        self.loaded_for = ""
        self.visible_now = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        bar.setContentsMargins(8, 6, 8, 0)
        self.state = QLabel(_("Editor"))
        self.state.setWordWrap(True)              # wrap long notices instead of forcing the window wide
        self.state.setMinimumWidth(160)
        bar.addWidget(self.state, 1)
        self.b_reload = QPushButton(icon("view-refresh"), _("Reload"))
        self.b_reload.clicked.connect(lambda: self.load(force=True))
        self.b_reload.setToolTip(_("Reload the editor page (it reconnects by itself afterwards)"))
        self.b_zoom_out = QPushButton(icon("zoom-out"), "−")
        self.b_zoom_in = QPushButton(icon("zoom-in"), "+")
        self.zoom_label = QPushButton("100 %")
        self.zoom_label.setFlat(True)
        self.zoom_label.setToolTip(_("Zoom of the editor – a click resets to 100 %. Remembered."))
        self.zoom_label.clicked.connect(lambda: self._zoom(None))
        self.b_zoom_out.setToolTip(_("Zoom out the editor (Ctrl+−)"))
        self.b_zoom_in.setToolTip(_("Zoom in the editor (Ctrl++)"))
        self.b_zoom_out.setShortcut("Ctrl+-")
        self.b_zoom_in.setShortcut("Ctrl++")
        self.b_zoom_out.clicked.connect(lambda: self._zoom(-0.1))
        self.b_zoom_in.clicked.connect(lambda: self._zoom(0.1))
        for b in (self.b_zoom_out, self.zoom_label, self.b_zoom_in, self.b_reload):
            bar.addWidget(b)
        lay.addLayout(bar)
        if not HAVE_WEBENGINE:
            msg = QLabel(_("Qt WebEngine is missing for the built-in editor.\n\nsudo apt install "
                           "python3-pyqt6.qtwebengine"))
            msg.setWordWrap(True)
            lay.addWidget(msg, 1)
            self.view = None
            return
        self.bridge = MidiBridge(self)
        # redirect the editor's links the way they make sense in the app: installer -> firmware page,
        # manual -> local copy, editor -> editor, internet -> normal browser (web.RoutingPage)
        self.view, self.page, self.channel = web.make_view(self, bridge=self.bridge, router=self.route_local)
        self.profile = web.profile()
        web.add_download_handler(self._download, first=False)
        self.view.setZoomFactor(ctx.settings.editor_zoom or 1.0)
        self.zoom_label.setText(f"{round(self.view.zoomFactor() * 100)} %")
        self.view.loadFinished.connect(self._loaded)
        lay.addWidget(self.view, 1)
        self.view.setHtml(self._placeholder(_("No editor loaded yet.")))
        ctx.changed.connect(self._dev_changed)
        ctx.editor_pause.connect(self.bridge.set_paused)

    # --------------------------------------------------------------
    def _placeholder(self, text: str) -> str:
        return web.placeholder_html(_("Editor"), text)

    def _zoom(self, d):
        """d = step, None = back to 100 %."""
        if self.view:
            z = 1.0 if d is None else max(0.5, min(2.0, round(self.view.zoomFactor() + d, 2)))
            self.view.setZoomFactor(z)
            self.zoom_label.setText(f"{round(z * 100)} %")
            self.ctx.settings.editor_zoom = z
            try:
                config.save(self.ctx.settings)
            except OSError:
                pass

    def _dev_changed(self):
        if not self.view:
            return
        if self.ctx.connected and self.ctx.link:
            self.bridge.attach(self.ctx.link, self.ctx.port.label if self.ctx.port else "FM-1")
            if self.visible_now:
                self.load()
        elif getattr(self.ctx, "protocol", "") == "generic":
            self.bridge.detach()
            if self.visible_now:
                self.load()                    # shows the empty state “no editor for this firmware”
        else:
            self.bridge.detach()
            self.state.setText(_("No FM-1 connected – the editor waits and connects by itself as soon as "
                                 "an FM-1 is plugged in."))

    def activate(self):
        self.visible_now = True
        self.load()

    def deactivate(self):
        self.visible_now = False

    def load(self, force=False):
        if not self.view:
            return
        key, tag = self.ctx.firmware_key, self.ctx.firmware_tag
        spec = firmware.spec(key)
        proto = getattr(self.ctx, "protocol", "")
        if proto == "generic" or (spec and (spec.protocol != "felucca" or not spec.editor)):
            # firmware without editor protocol (e.g. ORBIT, original firmware): clear empty state instead of waiting
            name = spec.name if spec else _("This firmware")
            self.loaded_for = ""
            self.state.setText(_("{name}: no built-in editor – presets via SysEx on the page “Presets”.").format(
                                   name=name))
            extra = ("<br><br>" + _('Online editor of the author: <a href="{url}">{url}</a>').format(url=spec.editor_url)
                     if spec and spec.editor_url else "")
            self.view.setHtml(self._placeholder(
                _("{name} does not come with an editor the app can embed.<br>Presets can be sent via SysEx on the page "
                  "<b>Presets</b> (FM voices); MIDI works via the page <b>MIDI</b>.").format(name=name) + extra))
            return
        if not spec:
            self.state.setText(_("There is no known editor for this firmware."))
            self.view.setHtml(self._placeholder(_("Connect an FM-1 with Melodee, Skiff or SLOOP. If the "
                                                  "version is unknown: install it once via the app or "
                                                  "enter the version in Settings › Devices.")))
            return
        want_tag = tag
        self.state.setText(_("Preparing the editor of {name} {version} …").format(
            name=spec.name, version=tag or _("(determining the version)")))
        if not force and self.loaded_for and (f"{key}/{tag}" == self.loaded_for if tag else self.loaded_for.startswith(key + "/")):
            self.state.setText(_("Editor: {name} {version} · MIDI via the app").format(name=spec.name, version=tag))
            return

        def work(w):
            t = want_tag
            if not t:                      # version unknown: existing copy, otherwise the newest
                avail = editor_host.available(key)
                t = avail[-1] if avail else ""
                if not t:
                    rels = firmware.fetch_releases(spec)
                    t = rels[0].tag if rels else ""
            if not t:
                raise RuntimeError(_("no version found (offline?)"))
            editor_host.prepare(spec, t, log=w.log.emit)
            return t

        def done(t):
            if not self.view:                  # finished in the meantime (shutdown)
                return
            self.loaded_for = f"{key}/{t}"
            # demo mode: the editors bring their own simulated FM-1 (?mock=1) –
            # it can do everything the editor needs; the app's demo device only the app functions
            demo = os.environ.get("FM1C_DEMO") == "1" and not os.environ.get("FM1C_DEMO_CMD")
            self.current = (spec, t, demo)
            self.view.load(self._editor_url())
            if demo:
                self.state.setText(_("Editor: {name} {version} · demo – simulation of the editor (own "
                                     "memory)").format(name=spec.name, version=t))
                return
            self.state.setText(_("Editor: {name} {version} · MIDI via the app").format(name=spec.name, version=t)
                               if want_tag else
                               _("Editor: {name} {version} · MIDI via the app (device version unknown – set it in "
                                 "Settings › Devices)").format(name=spec.name, version=t))

        def fail(msg, tb):
            if not self.view:
                return
            self.state.setText(_("Editor not available: {error}").format(error=msg))
            self.view.setHtml(self._placeholder(_("The editor for {name} {version} could not be "
                                                  "provided:<br><code>{error}</code><br><br>The first "
                                                  "time the app needs internet once, after that no more.").format(
                                                    name=spec.name, version=want_tag, error=msg)))
        run_job(self, work, on_done=done, on_fail=fail)

    # ------------------------------------------------------------ Links in the editor
    def _editor_url(self) -> QUrl:
        spec, tag, demo = self.current
        return QUrl(self.server.url(spec, tag) + ("?mock=1" if demo else ""))

    def route_local(self, url: QUrl) -> bool:
        """Navigation within the local server. True = load the page normally."""
        if not self.current:
            return True
        path = url.path().rstrip("/")
        last = path.rsplit("/", 1)[-1]
        if last == "installer":
            self.navigate.emit("firmware")
            return False
        if last == "editor":
            QTimer.singleShot(0, lambda: self.view.load(self._editor_url()))
            return False
        if last == "manual" and "/web/manual" not in url.path():
            QTimer.singleShot(0, self.open_manual)
            return False
        return True

    def open_manual(self):
        spec, tag, _demo = self.current
        idx = editor_host.manual_index(spec, tag)
        if not idx:
            if spec.manual_online:
                QDesktopServices.openUrl(QUrl(spec.manual_online))
            else:
                self.state.setText(_("{name} {version} comes without a manual.").format(name=spec.name, version=tag))
            return
        rel = idx.relative_to(editor_host.SITES).as_posix()
        target = QUrl(f"http://127.0.0.1:{self.server.port}/{rel}")
        if editor_host.missing_manual_images(idx) and spec.manual_online:
            self.state.setText(_("Manual: fetching the images once from the online manual …"))

            def done(n):
                left = len(editor_host.missing_manual_images(idx))
                self.state.setText(_("Manual {name} {version} · {n} images missing (offline?) – the text is "
                                     "complete").format(name=spec.name, version=tag, n=left) if left else
                                   _("Manual {name} {version}").format(name=spec.name, version=tag))
                self.view.load(target)
            run_job(self, lambda w: editor_host.fetch_manual_images(spec, idx), on_done=done,
                    on_fail=lambda *_a: done(0))
        else:
            self.state.setText(_("Manual {name} {version}").format(name=spec.name, version=tag))
            self.view.load(target)

    def shutdown(self):
        """on exit: tear down the page before the profile (otherwise Qt WebEngine warns)."""
        if not self.view:
            return
        from PyQt6 import sip
        self.bridge.detach()
        for obj in (self.view, self.page):
            if not sip.isdeleted(obj):
                sip.delete(obj)
        self.view = None

    def _loaded(self, ok):
        if not ok or not self.ctx.connected:
            return
        # connect automatically if the editor has a connect button
        self.page.runJavaScript("setTimeout(()=>{const b=document.getElementById('connect');"
                                "if(b && !b.disabled && /connect|verbind/i.test(b.textContent)) b.click();},400)")

    def _download(self, item) -> bool:
        """Downloads from the editor: backups (.json) into the device folder, everything else to editor exports.
        Only for our own page – files from the simulator go through its handler or to ~/FM-1/Downloads."""
        try:
            if item.page() is not self.page:
                return False
        except Exception:                                   # noqa: BLE001 – without page(): handle as before
            pass
        name = item.downloadFileName() or "download"
        if name.endswith(".json"):
            folder = self.ctx.device_folder(self.ctx.settings.backups_dir)
        else:
            folder = self.ctx.settings.root / "Editor-Exporte"  # i18n: ignore – folder name on disk
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            folder = Path.home()
        title = _("Save backup from the editor") if name.endswith(".json") else _("Save file from the "
                                                                                   "editor")
        path, _filter = QFileDialog.getSaveFileName(self, title, str(folder / name))
        if not path:
            item.cancel()
            self.state.setText(_("Saving cancelled – nothing written."))
            return True
        item.setDownloadDirectory(str(Path(path).parent))
        item.setDownloadFileName(Path(path).name)
        try:
            item.isFinishedChanged.connect(lambda p=path: self.state.setText(_("Saved: {path}").format(path=p)))
        except Exception:                                   # noqa: BLE001 – older Qt without the signal
            pass
        item.accept()
        self.state.setText(_("Saving {name} …").format(name=Path(path).name))
        return True
