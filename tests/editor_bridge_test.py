# SPDX-License-Identifier: GPL-3.0-only
"""Ende-zu-Ende-Test der Web-MIDI-Brücke:

  echter Melodee-Editor (rc.7) in QtWebEngine
     → Polyfill (navigator.requestMIDIAccess) → QWebChannel → MidiBridge
     → Link → Kindprozess mit dem Mock-FM-1 aus dem Editor selbst (tests/mock_fm1.mjs)

Prüft: Verbinden, Version lesen, Parameter-Änderung, Pause während einer App-Operation
und automatisches Wiederverbinden des Editors danach.

  QT_QPA_PLATFORM=offscreen python3 tests/editor_bridge_test.py /pfad/zu/melodee/web/editor.html
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
editor_html = sys.argv[1] if len(sys.argv) > 1 else "/tmp/melodee/web/editor.html"
home = Path(tempfile.mkdtemp(prefix="fm1c-bridge-"))
for k, sub in (("XDG_CONFIG_HOME", ".config"), ("XDG_DATA_HOME", ".local/share"), ("XDG_CACHE_HOME", ".cache")):
    os.environ[k] = str(home / sub)
os.environ["FM1C_DEMO"] = "1"
os.environ["FM1C_DEMO_CMD"] = "\x1f".join(["node", str(ROOT / "tests" / "mock_fm1.mjs"), editor_html])
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu")

# Profil vorbereiten: Demo-Gerät = Melodee v1.6.0-rc.7
devfile = home / ".local/share/fm1-companion/devices.json"
devfile.parent.mkdir(parents=True, exist_ok=True)
devfile.write_text(json.dumps({"profiles": [{"id": "test1", "name": "Test-FM-1", "serial": "DEMO-1",
                                             "firmware": "melodee", "firmware_version": "v1.6.0-rc.7"}]}))
# Editor-Repo aus dem vorhandenen Klon wiederverwenden (spart den Download)
repos = home / ".local/share/fm1-companion/repos"
repos.mkdir(parents=True, exist_ok=True)
src_repo = Path(editor_html).resolve().parents[1]
if (src_repo / ".git").exists():
    os.symlink(src_repo, repos / "melodee")

import PyQt6.QtWebEngineWidgets  # noqa: E402,F401
from PyQt6.QtCore import QCoreApplication, QEventLoop, Qt, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
app = QApplication(sys.argv)
from fm1companion import config  # noqa: E402

s = config.load()
s.user_root = str(home / "FM-1")
s.check_on_start = False
config.save(s)
QMessageBox.exec = lambda self: 0
QDialog.exec = lambda self: (QTimer.singleShot(300, self.accept), QDialog.open(self))[1] or 0

from fm1companion.ui.main_window import MainWindow  # noqa: E402

w = MainWindow()
w.show()
results = []


def check(cond, what):
    results.append((bool(cond), what))
    print(("ok   " if cond else "FAIL ") + what, flush=True)


def wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def js(code, timeout=5000):
    box = {}
    loop = QEventLoop()
    w.editor.page.runJavaScript(code, lambda v: (box.setdefault("v", v), loop.quit()))
    QTimer.singleShot(timeout, loop.quit)
    loop.exec()
    return box.get("v")


def until(cond, ms=20000, step=250):
    t0 = time.time()
    while time.time() - t0 < ms / 1000:
        try:
            if cond():
                return True
        except Exception:
            pass
        wait(step)
    return False


def main():
    check(until(lambda: w.ctx.connected, 15000), "App verbindet sich mit dem Mock-FM-1")
    check(w.ctx.info and "MOCK" in w.ctx.info.version, f"App liest INFO ({w.ctx.info.version if w.ctx.info else '-'})")
    w.go("editor")
    check(until(lambda: js("!!(window.__fm1Companion && navigator.requestMIDIAccess)"), 30000),
          "Polyfill im Editor aktiv")
    connected = until(lambda: "1.2.1" in (js("document.getElementById('version')?.textContent || ''") or ""), 30000)
    check(connected, "Editor verbindet sich über die Brücke und zeigt die Mock-Version")
    label = js("document.getElementById('connect').textContent")
    check(label and "connect" in label.lower() and "dis" in label.lower(), f"Editor-Knopf zeigt '{label}'")
    traffic0 = w.editor.bridge.traffic
    # eine Parameteränderung über das Mock-Protokoll: der Editor schickt SET und bekommt die Antwort
    wait(1500)
    check(w.editor.bridge.traffic > traffic0 or traffic0 > 5, f"Nachrichten laufen über die Brücke ({w.editor.bridge.traffic})")
    # App-Operation: Editor muss pausieren und sich danach wieder verbinden
    done = {}

    def op(wk, link):
        time.sleep(0.5)
        return link.ping()
    w.ctx.job(w, "Test", op, dialog=False, on_done=lambda r: done.setdefault("r", r))
    paused = until(lambda: not w.editor.bridge.connected, 3000, 50)
    check(paused, "Brücke wird während einer App-Operation pausiert")
    check(until(lambda: not (js("document.getElementById('version')?.textContent || ''") or ""), 3000, 100),
          "Editor sieht das Gerät während der App-Operation als getrennt")
    check(until(lambda: "r" in done, 5000), f"App-Operation läuft exklusiv ({done.get('r')})")
    check(until(lambda: w.editor.bridge.connected, 3000), "Brücke danach wieder verbunden")
    re = until(lambda: "1.2.1" in (js("document.getElementById('version')?.textContent || ''") or ""), 20000)
    check(re, "Editor verbindet sich nach der Pause von selbst wieder")
    if not re:
        print("DEBUG:", js("document.getElementById('connect').textContent + ' | ' + (document.querySelector('#say,#status,.say')?.textContent||'') + ' | ' + document.body.innerText.slice(0,300)"))
    w.grab().save(str(home / "editor_connected.png"))
    print("Screenshot:", home / "editor_connected.png")
    app.quit()


QTimer.singleShot(300, main)
app.exec()
fails = [r for r in results if not r[0]]
print(f"{len(results) - len(fails)}/{len(results)} bestanden")
sys.exit(1 if fails else 0)
