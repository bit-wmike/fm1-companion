# SPDX-License-Identifier: GPL-3.0-only
"""Links im eingebetteten Editor: Installer -> Firmware-Seite, Handbuch -> lokale Kopie (Bilder einmalig
nachladen, danach offline), zurück zum Editor, externe Links -> normaler Browser, Beenden ohne Warnung.

Die Online-Quelle der Handbuch-Bilder wird durch einen lokalen Server ersetzt.

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

# Stellvertreter für das Online-Handbuch: liefert für jeden *.png-Namen ein kleines PNG
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
check(until(lambda: w.ctx.connected and w.ctx.firmware_key == "melodee"), "Demo-Melodee verbunden")
w.go("editor")
check(until(lambda: "web/editor.html?mock=1" in url(), 180000), f"Editor geladen ({url()})")
check(until(lambda: js("document.readyState") == "complete"), "Editor fertig")

# 1) Installer -> Firmware-Seite der App, Editor bleibt stehen
js("document.querySelector('a[href=\"../installer/\"]').click()")
# (die frühere Firmware-Seite ist jetzt der Reiter „Installieren & Versionen“ in Firmware+)
check(until(lambda: w.firmware.isVisible() and current_page() is not w.editor, 5000),
      "Installer-Link öffnet Firmware+ › Installieren & Versionen")
wait(500)
check("web/editor.html?mock=1" in url(), "Editor ist nicht auf eine 404-Seite gewechselt")

# 2) Handbuch -> lokale Kopie, Bilder werden einmalig geholt
w.go("editor")
idx = editor_host.manual_index(firmware.spec("melodee"), w.editor.current[1])
check(idx is not None, "Handbuch liegt der Version bei")
n_missing = len(editor_host.missing_manual_images(idx))
check(n_missing > 10, f"vor dem ersten Öffnen fehlen die Bilder ({n_missing})")
js("document.querySelector('a[href=\"../manual/\"]').click()")
check(until(lambda: "/web/manual/" in url()), f"Handbuch geöffnet ({url()})")
check(until(lambda: not editor_host.missing_manual_images(idx)), "alle Bilder geholt und abgelegt")
check(len([h for h in hits if h.endswith(".png")]) == n_missing, f"jedes Bild genau einmal geladen ({len(hits)})")
check(until(lambda: js("document.title") not in (None, "") and "404" not in js("document.title")),
      f"Handbuch-Seite ok ({js('document.title')})")
check(until(lambda: js("[...document.images].filter(i=>i.complete&&i.naturalWidth>0).length") == len(
    js("[...document.images].map(i=>i.src)") or [1, 2]) and js("document.images.length") > 0),
      "Bilder im Handbuch werden angezeigt")

# 3) aus dem Handbuch zurück zum Editor
js("document.querySelector('a[href=\"../editor/\"]').click()")
check(until(lambda: "web/editor.html?mock=1" in url()), f"Editor-Link führt zurück ({url()})")

# 4) zweites Öffnen: keine weiteren Downloads (offline-fähig)
before = len(hits)
w.editor.open_manual()
check(until(lambda: "/web/manual/" in url()), "Handbuch erneut geöffnet")
check(len(hits) == before, "zweites Öffnen ohne Netz-Zugriff")

# 5) externer Link -> normaler Browser, Seite bleibt
js("const a=document.querySelector('a[href^=\"https://github.com\"]'); a.removeAttribute('target'); a.click()")
check(until(lambda: any(u.startswith("https://github.com") for u in opened), 5000), f"externer Link im Browser ({opened})")
check("/web/manual/" in url(), "eingebettete Seite bleibt beim Handbuch")
js("const b=document.querySelector('a[href^=\"https://\"]'); b.setAttribute('target','_blank'); b.click()")
check(until(lambda: len(opened) >= 2, 5000), f"target=_blank ebenfalls im Browser ({opened[-1] if opened else ''})")

# 6) Offline: Bilder fehlen -> Text trotzdem, freundlicher Hinweis
for p in idx.parent.glob("*.png"):
    p.unlink()
srv.shutdown()
w.editor.open_manual()
check(until(lambda: "Bilder fehlen" in w.editor.state.text(), 30000), f"offline: Hinweis ({w.editor.state.text()})")
check(until(lambda: "/web/manual/" in url()), "offline: Handbuch-Text trotzdem geöffnet")

# 6b) Download aus dem Editor (Backup-JSON): Dialog schlägt den Geräte-Ordner der Backups vor, Datei landet dort
from PyQt6.QtWidgets import QFileDialog
asked = []


def fake_save(parent, title, start, *a, **k):
    asked.append((title, start))
    return start, ""


QFileDialog.getSaveFileName = staticmethod(fake_save)
w.editor.view.load(w.editor._editor_url())
check(until(lambda: "web/editor.html" in url() and js("document.readyState") == "complete", 60000), "Editor wieder geladen")
js("const a=document.createElement('a');a.href=URL.createObjectURL(new Blob(['{\"fm1\":1}'],{type:'application/json'}));"
   "a.download='melodee-backup-test.json';document.body.appendChild(a);a.click()")
dev_folder = w.ctx.device_folder(w.ctx.settings.backups_dir)
check(until(lambda: asked, 10000) and "Backup" in asked[0][0] and str(asked[0][1]).startswith(str(dev_folder)),
      f"Backup-Download: Dialog im Geräte-Ordner ({asked[0] if asked else '-'})")
target = dev_folder / "melodee-backup-test.json"
check(until(lambda: target.exists() and target.read_text().startswith("{"), 10000), f"Datei liegt unter {target}")
check(until(lambda: "Gespeichert" in w.editor.state.text(), 5000), f"Rückmeldung: {w.editor.state.text()}")

# 7) Beenden: Seite vor Profil abbauen
w.close()
wait(300)
check(w.editor.view is None, "Editor beim Beenden sauber abgebaut")

ok = sum(r for r, _ in results)
print(f"\n{ok}/{len(results)} bestanden, {len(errors)} Ausnahmen")
for e in errors:
    print(e)
sys.exit(0 if ok == len(results) and not errors else 1)
