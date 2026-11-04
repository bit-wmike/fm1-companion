# SPDX-License-Identifier: GPL-3.0-only
"""Read-only main folder (~/FM-1): all pages still load, errors come as readable messages
(no crash, no "[Errno 13]"). As in the QA case the standard subfolders already exist (Backups, Projekte …),
new ones (DX7, Presets, MIDI-Export, SoundFonts …) cannot be created. As root, file permissions do not apply –
so the test refuses os.mkdir under ~/FM-1 (EACCES).

  flock /tmp/fm1c-ui.lock timeout 600 env QT_QPA_PLATFORM=offscreen python3 tests/ui_readonly_test.py
"""
from __future__ import annotations

import errno
import os
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("FM1C_LANG", "de")      # these checks expect the German user interface
home = Path(tempfile.mkdtemp(prefix="fm1c-ro-"))
for k, sub in (("XDG_CONFIG_HOME", ".config"), ("XDG_DATA_HOME", ".local/share"), ("XDG_CACHE_HOME", ".cache")):
    os.environ[k] = str(home / sub)
os.environ["FM1C_DEMO"] = "1"
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu")
RO = home / "FM-1"
for sub in ("Backups", "Projekte", "Samples", "Kits", "Aufnahmen", "Firmware"):
    (RO / sub).mkdir(parents=True)
BEFORE = sorted(str(p) for p in RO.rglob("*"))

_mkdir = os.mkdir


def denied_mkdir(p, *a, **k):
    if str(p).startswith(str(RO)):
        raise PermissionError(errno.EACCES, "Permission denied", str(p))
    return _mkdir(p, *a, **k)


os.mkdir = denied_mkdir

errors: list[str] = []
sys.excepthook = lambda t, v, tb: errors.append("".join(traceback.format_exception(t, v, tb)))

import PyQt6.QtWebEngineWidgets  # noqa: E402,F401
from PyQt6.QtCore import QCoreApplication, QEventLoop, Qt, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox  # noqa: E402

QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
app = QApplication(sys.argv)
from fm1companion import config  # noqa: E402

s = config.load()
s.user_root = str(RO)
s.check_on_start = False
config.save(s)
boxes: list[str] = []


def fake_exec(self):
    boxes.append(self.text())
    return 0


QMessageBox.exec = fake_exec
QDialog.exec = lambda self: 0
save_target = {"p": str(RO / "MIDI-Export" / "x.mid")}
QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (save_target["p"], ""))

from fm1companion.ui.main_window import MainWindow  # noqa: E402

try:
    w = MainWindow()
except Exception:  # noqa: BLE001
    traceback.print_exc()
    os.mkdir = _mkdir
    sys.exit(1)
w.resize(1280, 720)
w.show()
results = []


def check(cond, what):
    results.append((bool(cond), what))
    print(("ok   " if cond else "FAIL ") + what, flush=True)


def wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def until(cond, ms=10000):
    t0 = time.time()
    while time.time() - t0 < ms / 1000:
        if cond():
            return True
        wait(100)
    return False


def no_raw(texts) -> bool:
    return not any("Errno" in t or "Traceback" in t for t in texts)


def flow():
    from fm1companion.ui.pages import load_errors
    check(not (RO / "DX7").exists(), "new subfolders cannot be created")
    mine = ("presets", "voices", "soundmodule", "compose", "project_view", "midi", "samples", "workshop",
            "recorder", "backup_browser", "devices", "discover", "help", "simulator", "projects")
    bad = [k for k, _ in load_errors if k in mine]
    check(not bad, f"all pages loaded (errors: {bad})")
    for k in mine:
        check(w.page(k) is not None, f"page \"{k}\" in the sidebar")
    for k in list(w.specs):
        w.go(k)
        wait(150)
    check(until(lambda: w.ctx.connected), "demo device connected")
    vp = w.page("voices")
    vp.scan()
    check("Schreibrechte" in vp.state.text() and no_raw([vp.state.text()]), f"FM Voices: readable hint ({vp.state.text()[:60]})")
    pp = w.page("presets")
    pp.refresh_library()
    check(no_raw([pp.state.text(), pp.lib_empty.text()]), "Presets: no \"[Errno 13]\"")
    # Compose: save as MIDI
    cp = w.page("compose")
    boxes.clear()
    r = cp.save_midi(str(RO / "MIDI-Export" / "Idee.mid"))
    check(r is None and boxes and "nicht gespeichert" in boxes[-1] and no_raw(boxes),
          f"Compose: MIDI export reports the error readably ({boxes[-1][:60] if boxes else '–'})")
    # MIDI page: export the log
    mp = w.page("midi")
    boxes.clear()
    mon = getattr(mp, "monitor", None)
    target = next((x for x in (mon, mp) if x is not None and hasattr(x, "export")), None)
    if target is not None:
        save_target["p"] = str(RO / "MIDI" / "log.txt")
        target.export()
        check(boxes and no_raw(boxes) and "nicht gespeichert" in boxes[-1].lower(), "MIDI: export error readable, no crash")
    else:
        check(False, "MIDI monitor export found")
    # Presets: export into a read-only folder
    import fm1companion.presetlib as pl
    pp.lib_items = [pl.Preset(name="TEST", engine=12)] if hasattr(pl, "Preset") else []
    pp.fill()
    if pp.lib.topLevelItemCount():
        pp.lib.topLevelItem(0).setSelected(True)
        boxes.clear()
        pp.export(str(RO / "Presets" / "x.fm1preset"))
        check(boxes and no_raw(boxes), f"Presets: export error readable ({boxes[-1][:60] if boxes else '–'})")
    # project view: write a file
    pv = w.page("project_view")
    boxes.clear()
    ok = pv._write(RO / "neu" / "x.csv", lambda: (RO / "neu" / "x.csv").write_text("x"))
    check(ok is False and boxes and no_raw(boxes), "project view: write error readable")
    after = sorted(str(p) for p in RO.rglob("*"))
    check(after == BEFORE, f"nothing created in the read-only folder ({sorted(set(after) - set(BEFORE))[:3]})")
    app.quit()


def run():
    try:
        flow()
    except Exception:  # noqa: BLE001
        errors.append(traceback.format_exc())
        app.quit()


QTimer.singleShot(800, run)
QTimer.singleShot(240000, app.quit)
app.exec()
fails = [r for r in results if not r[0]]
# exceptions from files of the other area (Overview, Backups page, ui/device.py) are only reported – they are
# in the report as an integration request; this test checks the pages of this area.
OTHER = ("ui/overview.py", "ui/backups_page.py", "ui/device.py", "ui/main_window.py", "ui/fm1_page.py",
         "ui/firmware_page.py", "ui/pages/recovery.py")
foreign = [e for e in errors if any(f"fm1companion/{o}" in e.splitlines()[1] for o in OTHER)]   # outermost call
errors = [e for e in errors if e not in foreign]
for e in foreign:
    print("NOTE (other area):\n" + e.strip().splitlines()[1] + "\n  " + e.strip().splitlines()[-1])
for e in errors:
    print("EXCEPTION:\n" + e)
print(f"{len(results) - len(fails)}/{len(results)} passed, {len(errors)} exceptions")
os.mkdir = _mkdir
shutil.rmtree(home, ignore_errors=True)
sys.stdout.flush()
os._exit(1 if fails or errors else 0)    # without Qt cleanup (WebEngine crashes on exit otherwise)
