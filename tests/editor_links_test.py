# SPDX-License-Identifier: GPL-3.0-only
"""Links in the embedded editor: installer -> firmware page, manual -> local copy (images fetched once,
offline afterwards), back to the editor, external links -> normal browser, quitting without a warning.

The online source of the manual images is replaced by a local server.

  QT_QPA_PLATFORM=offscreen python3 tests/editor_links_test.py
"""
from __future__ import annotations

import functools
import os
import sys
import tempfile
import threading
import time
import traceback
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("FM1C_LANG", "de")      # these checks expect the German user interface
home = Path(tempfile.mkdtemp(prefix="fm1c-links-"))
for k, sub in (("XDG_CONFIG_HOME", ".config"), ("XDG_DATA_HOME", ".local/share"), ("XDG_CACHE_HOME", ".cache")):
    os.environ[k] = str(home / sub)
os.environ["FM1C_DEMO"] = "1"
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu")

errors: list[str] = []
sys.excepthook = lambda t, v, tb: errors.append("".join(traceback.format_exception(t, v, tb)))

import PyQt6.QtWebEngineWidgets  # noqa: E402,F401
from PyQt6.QtCore import QCoreApplication, QEventLoop, Qt, QTimer, QUrl  # noqa: E402
from PyQt6.QtGui import QDesktopServices  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
app = QApplication(sys.argv)

from fm1companion import config, editor_host, firmware  # noqa: E402

s = config.load()
s.user_root = str(home / "FM-1")
s.check_on_start = False
config.save(s)
config.ensure_dirs()

# stand-in for the online manual: delivers a small PNG for every *.png name
PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")
hits = []


class FakeManual(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        hits.append(self.path)
        if self.path.endswith(".png"):
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.end_headers()
            self.wfile.write(PNG)
        else:
            self.send_error(404)


srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeManual)
threading.Thread(target=srv.serve_forever, daemon=True).start()
firmware.spec("melodee").manual_online = f"http://127.0.0.1:{srv.server_address[1]}/melodee/webapp/manual/"

opened: list[str] = []
QDesktopServices.openUrl = staticmethod(lambda u: opened.append(u.toString()) or True)

from fm1companion.ui.main_window import MainWindow  # noqa: E402

results = []


def check(cond, what):
    results.append((bool(cond), what))
    print(("ok  " if cond else "FAIL"), what, flush=True)


def wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def until(cond, ms=20000):
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        try:
            if cond():
                return True
        except Exception:
            pass
        wait(100)
    return False


def js(code):
    box = {}
    w.editor.page.runJavaScript(code, lambda v: box.setdefault("v", v))
    until(lambda: "v" in box, 5000)
    return box.get("v")


def url():
    return w.editor.view.url().toString()


def current_page():
    return w.stack.currentWidget()


w = MainWindow()
w.show()
check(until(lambda: w.ctx.connected and w.ctx.firmware_key == "melodee"), "demo Melodee connected")
w.go("editor")
check(until(lambda: "web/editor.html?mock=1" in url(), 180000), f"editor loaded ({url()})")
check(until(lambda: js("document.readyState") == "complete"), "editor ready")

# 1) installer -> the app's firmware page, the editor stays
js("document.querySelector('a[href=\"../installer/\"]').click()")
# (the former firmware page is now the tab "Install & Versions" in Firmware+)
check(until(lambda: w.firmware.isVisible() and current_page() is not w.editor, 5000),
      "installer link opens Firmware+ › Install & Versions")
wait(500)
check("web/editor.html?mock=1" in url(), "editor has not switched to a 404 page")

# 2) manual -> local copy, images are fetched once
w.go("editor")
idx = editor_host.manual_index(firmware.spec("melodee"), w.editor.current[1])
check(idx is not None, "the manual comes with the version")
n_missing = len(editor_host.missing_manual_images(idx))
check(n_missing > 10, f"before the first opening the images are missing ({n_missing})")
js("document.querySelector('a[href=\"../manual/\"]').click()")
check(until(lambda: "/web/manual/" in url()), f"manual opened ({url()})")
check(until(lambda: not editor_host.missing_manual_images(idx)), "all images fetched and stored")
check(len([h for h in hits if h.endswith(".png")]) == n_missing, f"every image loaded exactly once ({len(hits)})")
check(until(lambda: js("document.title") not in (None, "") and "404" not in js("document.title")),
      f"manual page ok ({js('document.title')})")
check(until(lambda: js("[...document.images].filter(i=>i.complete&&i.naturalWidth>0).length") == len(
    js("[...document.images].map(i=>i.src)") or [1, 2]) and js("document.images.length") > 0),
      "images in the manual are shown")

# 3) from the manual back to the editor
js("document.querySelector('a[href=\"../editor/\"]').click()")
check(until(lambda: "web/editor.html?mock=1" in url()), f"editor link leads back ({url()})")

# 4) second opening: no further downloads (works offline)
before = len(hits)
w.editor.open_manual()
check(until(lambda: "/web/manual/" in url()), "manual opened again")
check(len(hits) == before, "second opening without network access")

# 5) external link -> normal browser, the page stays
js("const a=document.querySelector('a[href^=\"https://github.com\"]'); a.removeAttribute('target'); a.click()")
check(until(lambda: any(u.startswith("https://github.com") for u in opened), 5000), f"external link in the browser ({opened})")
check("/web/manual/" in url(), "embedded page stays on the manual")
js("const b=document.querySelector('a[href^=\"https://\"]'); b.setAttribute('target','_blank'); b.click()")
check(until(lambda: len(opened) >= 2, 5000), f"target=_blank in the browser as well ({opened[-1] if opened else ''})")

# 6) offline: images missing -> text anyway, friendly hint
for p in idx.parent.glob("*.png"):
    p.unlink()
srv.shutdown()
w.editor.open_manual()
check(until(lambda: "Bilder fehlen" in w.editor.state.text(), 30000), f"offline: hint ({w.editor.state.text()})")
check(until(lambda: "/web/manual/" in url()), "offline: manual text opened anyway")

# 6b) download from the editor (backup JSON): the dialog suggests the device folder of the backups, the file lands there
from PyQt6.QtWidgets import QFileDialog
asked = []


def fake_save(parent, title, start, *a, **k):
    asked.append((title, start))
    return start, ""


QFileDialog.getSaveFileName = staticmethod(fake_save)
w.editor.view.load(w.editor._editor_url())
check(until(lambda: "web/editor.html" in url() and js("document.readyState") == "complete", 60000), "editor loaded again")
js("const a=document.createElement('a');a.href=URL.createObjectURL(new Blob(['{\"fm1\":1}'],{type:'application/json'}));"
   "a.download='melodee-backup-test.json';document.body.appendChild(a);a.click()")
dev_folder = w.ctx.device_folder(w.ctx.settings.backups_dir)
check(until(lambda: asked, 10000) and "Backup" in asked[0][0] and str(asked[0][1]).startswith(str(dev_folder)),
      f"backup download: dialog in the device folder ({asked[0] if asked else '-'})")
target = dev_folder / "melodee-backup-test.json"
check(until(lambda: target.exists() and target.read_text().startswith("{"), 10000), f"file is at {target}")
check(until(lambda: "Gespeichert" in w.editor.state.text(), 5000), f"feedback: {w.editor.state.text()}")

# 7) quit: tear down the page before the profile
w.close()
wait(300)
check(w.editor.view is None, "editor torn down cleanly when quitting")

ok = sum(r for r, _ in results)
print(f"\n{ok}/{len(results)} passed, {len(errors)} exceptions")
for e in errors:
    print(e)
sys.exit(0 if ok == len(results) and not errors else 1)
