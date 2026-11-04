# SPDX-License-Identifier: GPL-3.0-only
"""UI smoke test in demo mode (without a screen): open every page, take screenshots,
collect unhandled exceptions.

  QT_QPA_PLATFORM=offscreen python3 tests/ui_smoke.py OUTDIR
"""
from __future__ import annotations

import os
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("FM1C_LANG", "de")      # these checks expect the German user interface
out = Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp())
out.mkdir(parents=True, exist_ok=True)
home = Path(os.environ.get("FM1C_TEST_HOME") or tempfile.mkdtemp(prefix="fm1c-home-"))
for k, sub in (("XDG_CONFIG_HOME", ".config"), ("XDG_DATA_HOME", ".local/share"), ("XDG_CACHE_HOME", ".cache")):
    os.environ[k] = str(home / sub)
os.environ["FM1C_DEMO"] = "1"
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

errors: list[str] = []


def hook(t, v, tb):
    errors.append("".join(traceback.format_exception(t, v, tb)))


sys.excepthook = hook

import PyQt6.QtWebEngineWidgets  # noqa: E402,F401
from PyQt6.QtCore import QCoreApplication, Qt, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox, QDialog  # noqa: E402

QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
app = QApplication(sys.argv)
from fm1companion.ui import theme as _theme  # noqa: E402
_theme.apply(app, os.environ.get("FM1C_THEME", "light"))
from fm1companion.ui import common as _common
_common.install_translations(app)

from fm1companion import config  # noqa: E402

s = config.load()
s.user_root = str(home / "FM-1")
s.check_on_start = False
# sample folder with a few generated WAVs (like PO samples)
samp = home / "po-samples" / "PO-33 kicks"
samp.mkdir(parents=True, exist_ok=True)
import math, struct, wave  # noqa: E401,E402
for i, (name, f0) in enumerate([("kick 01", 55), ("snare_02", 180), ("hat C4", 4000), ("vox hey", 300)]):
    with wave.open(str(samp / f"{name}.wav"), "wb") as w:
        w.setnchannels(2 if i % 2 else 1)
        w.setsampwidth(2)
        w.setframerate(44100)
        n = 22050 if i != 3 else 66150
        w.writeframes(b"".join(struct.pack("<" + "h" * w.getnchannels(),
                                           *[int(12000 * math.sin(2 * math.pi * f0 * t / 44100) * math.exp(-t / 9000))] * w.getnchannels())
                               for t in range(n)))
s.sample_roots = [str(home / "po-samples")]
config.save(s)
config.ensure_dirs()

# do not let dialogs block in the test
QMessageBox.exec = lambda self: 0
_orig_dialog_exec = QDialog.exec


def auto_dialog_exec(self):
    QTimer.singleShot(1500, self.close)
    return _orig_dialog_exec(self)


QDialog.exec = auto_dialog_exec

from fm1companion.ui.main_window import MainWindow  # noqa: E402

w = MainWindow()
w.resize(int(os.environ.get("FM1C_SHOT_W", 1360)), int(os.environ.get("FM1C_SHOT_H", 860)))
w.show()
steps = []


def shot(name):
    w.grab().save(str(out / f"{name}.png"))


def run_steps():
    if not steps:
        app.quit()
        return
    name, fn, delay = steps.pop(0)
    try:
        fn()
    except Exception:  # noqa: BLE001
        errors.append(f"{name}:\n{traceback.format_exc()}")
    QTimer.singleShot(delay, lambda: (shot(name), run_steps()))


steps.append(("00_start", lambda: None, 3000))
for i, key in enumerate(w.specs):
    steps.append((f"{i + 1:02d}_{key}", lambda k=key: w.go(k), 6000 if key in ("editor", "samples") else 1500))
# tabs of merged pages (old keys redirect)
from fm1companion.ui.main_window import ALIASES  # noqa: E402
for i, key in enumerate(ALIASES):
    steps.append((f"{15 + i:02d}_{key}", lambda k=key: w.go(k), 6000 if key == "simulator" else 1500))
# actions on the demo device
steps.append(("30_projects_read", lambda: (w.go("projects"), w.projects.read_slots()), 2500))
steps.append(("31_fm1_page", lambda: w.go("fm1"), 4000))
steps.append(("32_samples_slots", lambda: (w.go("samples"), w.samples.tabs.setCurrentIndex(w.samples.dev_tab_index),
                                           w.samples.read_slots()), 2500))


def add_kit():
    w.go("samples")
    t = w.samples.table
    t.selectAll()
    w.samples._add_to_kit()


steps.append(("33_samples_kit", add_kit, 4000))
steps.append(("34_backup", lambda: w._backup_now(), 4000))
steps.append(("35_backups_list", lambda: (w.go("backups"), w.backups.refresh()), 1500))
QTimer.singleShot(500, run_steps)
QTimer.singleShot(120000, app.quit)
app.exec()
print(f"Screenshots: {out}")
print(f"Test folder: {home}")
if errors:
    print(f"{len(errors)} ERRORS:")
    for e in errors:
        print(e)
    sys.exit(1)
print("no errors")
