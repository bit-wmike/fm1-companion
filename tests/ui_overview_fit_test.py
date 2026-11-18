# SPDX-License-Identifier: GPL-3.0-only
"""Overview at 1280×720 with a tall font: "Device", "Backup" and "Firmware & Updates" fit without scrolling.

tests/ui_ux_test.py checks this with whatever font the machine has – DejaVu Sans in CI (14 px per line), where it
passed, while on Raspberry Pi OS (Noto Sans 10 pt: 18 px per line) the "Device" card ended below the window's edge.
This script does not depend on the installed font: it enlarges the application font until a line is at least
LINE_PX pixels tall (a bit more than Noto Sans 10 pt), which also makes it wider than Noto Sans, and it wants
RESERVE of the page's height left over.

  QT_QPA_PLATFORM=offscreen python3 tests/ui_overview_fit_test.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("FM1C_LANG", "de")      # German: the longer texts
home = Path(tempfile.mkdtemp(prefix="fm1c-fit-"))
for k, sub in (("XDG_CONFIG_HOME", ".config"), ("XDG_DATA_HOME", ".local/share"), ("XDG_CACHE_HOME", ".cache"),
               ("XDG_STATE_HOME", ".local/state")):
    os.environ[k] = str(home / sub)
os.environ["HOME"] = str(home)
os.environ["FM1C_DEMO"] = "1"
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu")

LINE_PX = 19                                   # Noto Sans 10 pt at 96 dpi: 18
RESERVE = 0.10                                 # of the page's height: notices above the page differ in height from
#                                                machine to machine (on the Pi they took more than here)

errors: list[str] = []
sys.excepthook = lambda t, v, tb: errors.append("".join(traceback.format_exception(t, v, tb)))

try:
    import PyQt6.QtWebEngineWidgets  # noqa: F401
except ImportError:
    pass
from PyQt6.QtCore import QCoreApplication, QEventLoop, Qt, QTimer  # noqa: E402
from PyQt6.QtGui import QFontMetrics  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox, QScrollArea  # noqa: E402

QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
app = QApplication(sys.argv)
font = app.font()
start_pt = font.pointSizeF() if font.pointSizeF() > 0 else 10.0
font.setPointSizeF(start_pt)
while QFontMetrics(font).lineSpacing() < LINE_PX and font.pointSizeF() < 3 * start_pt:
    font.setPointSizeF(font.pointSizeF() + 0.25)
app.setFont(font)

from fm1companion import config  # noqa: E402
from fm1companion.ui import theme  # noqa: E402

theme.apply(app, "light")
s = config.load()
s.user_root = str(home / "FM-1")
s.check_on_start = False
config.save(s)
config.ensure_dirs()

QMessageBox.exec = lambda self: 0
QDialog.exec = lambda self: 1

from fm1companion.ui.main_window import MainWindow  # noqa: E402

results = []


def check(cond, what):
    results.append((bool(cond), what))
    print(("ok   " if cond else "FAIL ") + what, flush=True)


def wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def flow():
    w = MainWindow()
    w.resize(1280, 720)
    w.show()
    t0 = time.time()
    while not w.ctx.connected and time.time() - t0 < 10:
        wait(100)
    check(w.ctx.connected, "demo device connected")
    w.go("overview")
    wait(800)
    ov = w.overview
    vp = ov.findChild(QScrollArea).viewport()
    line = ov.lbl["Name"].fontMetrics().lineSpacing()
    check(line >= LINE_PX, f"the page really uses the tall font ({ov.lbl['Name'].font().family()} "
                           f"{ov.lbl['Name'].font().pointSizeF():g} pt, {line} px per line)")
    for name, card in (("Device", ov.dev_card), ("Backup", ov.bk_card), ("Firmware & Updates", ov.up_card)):
        top = card.mapTo(vp, card.rect().topLeft()).y()
        bottom = top + card.height()
        check(card.isVisible() and top >= 0 and bottom <= vp.height() * (1 - RESERVE),
              f"\"{name}\" fits at 1280×720 without scrolling, with {RESERVE:.0%} to spare "
              f"(card {top}–{bottom} px of {vp.height()} px)")
    check(ov.b_rename.isVisible() and ov.b_color.isVisible() and ov.notes.isVisible(),
          "\"Rename\", \"Colour\" and the notes field are still there")
    w.close()
    app.quit()


def run():
    try:
        flow()
    except Exception:  # noqa: BLE001
        errors.append(traceback.format_exc())
        app.quit()


QTimer.singleShot(300, run)
QTimer.singleShot(120000, app.quit)
app.exec()
shutil.rmtree(home, ignore_errors=True)
fails = [r for r in results if not r[0]]
for e in errors:
    print("EXCEPTION:\n" + e)
print(f"{len(results) - len(fails)}/{len(results)} passed, {len(errors)} exceptions")
sys.exit(1 if fails or errors else 0)
