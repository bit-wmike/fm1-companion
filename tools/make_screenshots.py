# SPDX-License-Identifier: GPL-3.0-only
"""README screenshots: English UI, demo mode, 1280x800 (offscreen; needs PyQt6 with QtWebEngine).

  cd <repo> && PYTHONPATH=. QT_QPA_PLATFORM=offscreen QTWEBENGINE_DISABLE_SANDBOX=1 \
      python3 tools/make_screenshots.py . /path/to/out
"""
import os
import sys
import tempfile
import time
from pathlib import Path

REPO, OUT = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(REPO))
home = Path(tempfile.mkdtemp(prefix="fm1c-shots-"))
for k, sub in (("XDG_CONFIG_HOME", ".config"), ("XDG_DATA_HOME", ".local/share"), ("XDG_CACHE_HOME", ".cache")):
    os.environ[k] = str(home / sub)
os.environ.update(FM1C_DEMO="1", FM1C_LANG="en", FM1C_NO_NET="1", HOME=str(home))
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu")

import PyQt6.QtWebEngineWidgets  # noqa: F401,E402
from PyQt6.QtCore import QCoreApplication, QEventLoop, Qt, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
app = QApplication(sys.argv)
from fm1companion import config  # noqa: E402

s = config.load()
s.user_root = str(home / "FM-1")
s.check_on_start = False
config.save(s)
config.ensure_dirs()
from fm1companion.ui import theme  # noqa: E402

theme.apply(app, os.environ.get("SHOT_THEME", "light"))
from PyQt6.QtWidgets import QDialog, QMessageBox  # noqa: E402


def _msg_exec(self):
    for b in self.buttons():
        if self.buttonRole(b) in (QMessageBox.ButtonRole.AcceptRole, QMessageBox.ButtonRole.YesRole):
            self._clicked = b
            return 0
    self._clicked = None
    return 0


QMessageBox.exec = _msg_exec
QMessageBox.clickedButton = lambda self: getattr(self, "_clicked", None)


def _dlg_exec(self):
    from fm1companion.ui.common import JobDialog
    if isinstance(self, JobDialog):
        self.show()
        t0 = time.time()
        while time.time() - t0 < 120:
            app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
            if self.buttons.button(self.buttons.StandardButton.Close).isEnabled():
                break
        self.hide()
        return 1 if self.ok else 0
    return 1


QDialog.exec = _dlg_exec
from fm1companion.ui.main_window import MainWindow  # noqa: E402

w = MainWindow()
w.resize(1280, 800)
w.show()
ctx = w.ctx


def wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def until(cond, ms=20000):
    t = time.monotonic()
    while time.monotonic() - t < ms / 1000:
        try:
            if cond():
                return True
        except Exception:  # noqa: BLE001
            pass
        wait(100)
    return False


def close_toasts():
    from fm1companion.ui.notify import Toast
    for t in w.findChildren(Toast):
        t.hide()
    lane = getattr(w, "toast_lane", None)
    if lane is not None:
        lane.hide()


def shot(name, wait_ms=900):
    wait(wait_ms)
    close_toasts()
    wait(200)
    w.grab().save(str(OUT / name))
    print("saved", name)


def tab(page, text):
    from PyQt6.QtWidgets import QTabWidget
    for tw in page.findChildren(QTabWidget):
        for i in range(tw.count()):
            if tw.tabText(i).replace("&", "").startswith(text):
                tw.setCurrentIndex(i)
                return True
    return False


until(lambda: ctx.connected, 30000)
wait(2500)
w.go("overview")
shot("01-overview.png")

# Studio: fetch from the (demo) FM-1
w.go("studio")
page = w.page("studio")
svc = w.studio
until(lambda: svc.running, 40000)
until(lambda: page.view.page() is svc.page, 10000)
wait(1500)
bfile = None
try:
    bfile = page.fetch_from_device()
except Exception as e:  # noqa: BLE001
    print("fetch failed", e)
wait(1500)
shot("02-studio.png", 1500)

# Studio with a second firmware: the demo's SLOOP FM-1 (DEMO-2) in its SLOOP twin
port1 = next((x for x in ctx.ports if x.serial == "DEMO-1"), None)
port2 = next((x for x in ctx.ports if x.serial == "DEMO-2"), None)
if port2 is not None:
    try:
        ctx.connect_port(port2)
        print("sloop connected", until(lambda: ctx.connected and ctx.firmware_key == "sloop", 20000), flush=True)
        page.refresh()
        wait(800)
        page.fetch_from_device()
        until(lambda: svc.running and svc.firmware == "sloop", 30000)
        wait(2000)
        shot("02b-studio-sloop.png", 1500)
    except Exception as e:  # noqa: BLE001
        print("sloop studio", e)
    if port1 is not None:
        ctx.connect_port(port1)
        until(lambda: ctx.connected and ctx.firmware_key != "sloop", 20000)
        wait(1000)

# Projects › View & Export with the fetched backup
w.go("projects")
pr = w.page("projects")
pv = w.page("project_view")
if pv is not None and bfile:
    try:
        tab(pr, "View")
        pv.open_backup(str(bfile))
    except Exception as e:  # noqa: BLE001
        print("project view", e)
    w.go("project_view")
shot("03-project-view.png", 1500)

# Backups › Browse & Compare
w.go("backups")
bp = w.page("backups")
tab(bp, "Browse")
shot("04-backups-browse.png", 1500)

# Firmware+ catalog and Install & Versions
w.go("discover")
shot("05-firmware-catalog.png", 2000)
w.go("firmware")
shot("06-firmware-install.png", 1500)

w.go("compose")
shot("07-compose.png")
w.go("presets")
shot("08-presets.png")
w.go("midi")
mp = w.page("midi")
tab(mp, "Keyboard")
shot("09-midi.png")
w.go("recovery")
shot("10-recovery.png")

# Settings › Storage
try:
    from fm1companion.ui.settings_dialog import SettingsDialog
    d = SettingsDialog(ctx, w, tab="storage")
    d.resize(980, 720)
    d.show()
    if hasattr(d, "storage_index"):
        for tw in d.findChildren(__import__("PyQt6.QtWidgets", fromlist=["QTabWidget"]).QTabWidget):
            if tw.indexOf(d.storage) >= 0:
                tw.setCurrentIndex(tw.indexOf(d.storage))
    wait(2500)
    d.grab().save(str(OUT / "11-storage.png"))
    print("saved 11-storage.png")
    d.close()
except Exception as e:  # noqa: BLE001
    print("storage", e)
w.close()
wait(500)
os._exit(0)
