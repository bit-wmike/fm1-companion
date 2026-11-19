# SPDX-License-Identifier: GPL-3.0-only
""""Which firmware is running here?" can be answered again (Overview › Device › Firmware: "change").

The question came once, when a device with an ambiguous ID was connected; "don't know" (or closing the dialog) was
remembered and nothing in the app let you change it. Seen with a new FM-1 on the original firmware (ID FM-1_015:
V15 or another original version). The demo device here answers with that ID.

  QT_QPA_PLATFORM=offscreen python3 tests/ui_firmware_choice_test.py
"""
from __future__ import annotations

import json
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
home = Path(tempfile.mkdtemp(prefix="fm1c-fwchoice-"))
for k, sub in (("XDG_CONFIG_HOME", ".config"), ("XDG_DATA_HOME", ".local/share"), ("XDG_CACHE_HOME", ".cache"),
               ("XDG_STATE_HOME", ".local/state")):
    os.environ[k] = str(home / sub)
os.environ["HOME"] = str(home)
os.environ["FM1C_DEMO"] = "1"
os.environ["FM1C_DEMO_CMD"] = "\x1f".join([sys.executable, "-m", "fm1companion.demo_device", "--generic", "FM-1_015"])
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu")

errors: list[str] = []
sys.excepthook = lambda t, v, tb: errors.append("".join(traceback.format_exception(t, v, tb)))

try:
    import PyQt6.QtWebEngineWidgets  # noqa: F401
except ImportError:
    pass
from PyQt6.QtCore import QCoreApplication, QEventLoop, Qt, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
app = QApplication(sys.argv)

from fm1companion import config, firmware  # noqa: E402
from fm1companion.ui import theme  # noqa: E402

theme.apply(app, "light")
s = config.load()
s.user_root = str(home / "FM-1")
s.check_on_start = False
config.save(s)
config.ensure_dirs()

ID = "FM-1_015"
asked = {"answer": "?", "n": 0, "preselected": [], "offered": []}     # answer: a firmware key, "?" or None (close)


def dialog_exec(self):
    from fm1companion.ui.device import FirmwareChoiceDialog
    if not isinstance(self, FirmwareChoiceDialog):
        return 1
    asked["n"] += 1
    asked["preselected"].append(self.choice())
    asked["offered"] = [self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())]
    if asked["answer"] is None:
        return 0
    for i in range(self.list.count()):
        if self.list.item(i).data(Qt.ItemDataRole.UserRole) == asked["answer"]:
            self.list.setCurrentRow(i)
    return 1


QMessageBox.exec = lambda self: 0
QDialog.exec = dialog_exec

from fm1companion.ui.main_window import MainWindow  # noqa: E402

results = []


def check(cond, what):
    results.append((bool(cond), what))
    print(("ok   " if cond else "FAIL ") + what, flush=True)


def wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def until(cond, ms=15000):
    t0 = time.time()
    while time.time() - t0 < ms / 1000:
        if cond():
            return True
        wait(100)
    return False


def flow():
    from fm1companion.flash import planner
    from fm1companion.ui import firmware_page as fp
    w = MainWindow()
    w.resize(1280, 720)
    w.show()
    ctx, ov = w.ctx, w.overview
    check(until(lambda: ctx.identity == ID and ctx.protocol == "generic"), f"demo FM-1 answers with {ID}, MIDI only")
    check(until(lambda: ctx.profile is not None and ctx.profile.firmware_choice.get(ID) == "?", 8000) and asked["n"] == 1,
          f"asked once on connecting, \"don't know\" remembered ({asked['n']}, offered {asked['offered']})")
    check(asked["offered"][:2] == ["stock-v15", "stock"], f"candidates of {ID}: {asked['offered']}")
    w.go("overview")
    wait(400)
    row = ov.lbl["Firmware"].text()
    check("unbekannt" in row and "href='#firmware'" in row and "ändern" in row,
          f"Overview › Gerät › Firmware: unknown, with the link \"ändern\" ({row})")
    check("Hinweis oben" not in ov.up_text.text() and "ändern" in ov.up_text.text(),
          f"\"Firmware & Updates\" points to that link, not to a note that is not there ({ov.up_text.text()[:90]})")
    st = fp.device_state(ctx)
    check(st.firmware_key == "", "the plan does not know the firmware yet")

    # ---- what the other pages say about an FM-1 whose firmware has no editor protocol (the original firmware)
    check("Mach am besten jetzt eins" not in ov.bk_text.text() and "Editor-Protokoll" in ov.bk_text.text()
          and not ov.b_backup.isEnabled(),
          f"Overview › Sicherung: no \"make one now\" above a greyed-out button, it says why ({ov.bk_text.text()[:80]})")
    w.go("projects")
    wait(600)
    pp = w.projects
    check("Editor-Protokoll" in pp.dev_info.text() and "Kein Gerät" not in pp.dev_info.text(),
          f"Projects: not \"no device connected\" while one is connected ({pp.dev_info.text()[:80]})")
    check(not pp.b_imp.isEnabled() and "Editor-Protokoll" in pp.b_imp.toolTip(),
          f"Projects: \"to the FM-1 into the chosen slot\" is off and says why ({pp.b_imp.toolTip()[:60]})")
    check(pp._origin(ctx.profile.name) == ctx.profile.name and pp._origin("") == ""
          and pp._origin("FM-1 Gone").startswith("FM-1 Gone (") and "kein Profil" in pp._origin("FM-1 Gone"),
          f"Projects › library: an origin no profile has is marked, not hidden ({pp._origin('FM-1 Gone')})")
    w.go("overview")
    wait(300)

    # closing the dialog changes nothing
    asked["answer"] = None
    got = ov.change_firmware()
    check(got == "" and asked["n"] == 2 and ctx.profile.firmware_choice.get(ID) == "?" and ctx.firmware_key == "",
          "\"ändern\", dialog closed: nothing changed")
    check(asked["preselected"][-1] == "?", f"the stored answer is preselected ({asked['preselected'][-1]})")

    # the link itself (not only the method behind it)
    asked["answer"] = "stock-v15"
    ov.lbl["Firmware"].linkActivated.emit("#firmware")
    check(until(lambda: ctx.profile.firmware_choice.get(ID) == "stock-v15", 5000) and asked["n"] == 3,
          f"\"ändern\" → V15 chosen: firmware_choice[{ID}] = {ctx.profile.firmware_choice.get(ID)!r}")
    check(ctx.profile.firmware == "stock-v15" and ctx.firmware_key == "stock-v15" and not ctx.firmware_ambiguous,
          f"profile and context know it ({ctx.profile.firmware!r}, {ctx.firmware_key!r})")
    wait(300)
    row = ov.lbl["Firmware"].text()
    name = firmware.spec("stock-v15").name
    check(name in row and "unbekannt" not in row and "href='#firmware'" in row,
          f"Overview shows {name}, still changeable ({row})")
    check("bitte wählen" not in ov.up_text.text(), f"\"Firmware & Updates\" no longer asks to choose ({ov.up_text.text()[:70]})")
    stored = json.loads((Path(os.environ["XDG_DATA_HOME"]) / "fm1-companion" / "devices.json").read_text("utf-8"))
    check("stock-v15" in json.dumps(stored), "… and it is saved in the profile file")

    # the install plan from V15 to a Felucca-family firmware: no V15 step
    st = fp.device_state(ctx)
    target = planner.Target.from_spec(firmware.spec("melodee"))
    plan = planner.plan_install(st, target, via_v15=planner.v15_option(st, target) != "none", backup=False)
    check(st.firmware_key == "stock-v15", f"the plan knows the running firmware ({st.firmware_key!r})")
    check(planner.v15_option(st, target) == "none" and not plan.via_stock,
          f"plan stock-v15 → Melodee: no V15 intermediate step ({planner.v15_option(st, target)}, via_stock {plan.via_stock})")

    # State tab: the same link; a wrong answer can be corrected, also back to "don't know"
    w.go("fm1")
    fm1 = w.page("fm1")
    wait(500)
    check("href='#firmware'" in fm1.none.text() and name in fm1.none.text(),
          "Overview › Zustand: firmware with the same link")
    asked["answer"] = "stock"
    fm1.none.linkActivated.emit("#firmware")
    check(until(lambda: ctx.profile.firmware_choice.get(ID) == "stock", 5000) and asked["preselected"][-1] == "stock-v15",
          f"corrected from the State tab (preselected {asked['preselected'][-1]}, now {ctx.profile.firmware_choice.get(ID)})")
    asked["answer"] = "?"
    fm1.none.linkActivated.emit("#firmware")
    check(until(lambda: ctx.profile.firmware_choice.get(ID) == "?", 5000) and ctx.firmware_key == ""
          and ctx.firmware_ambiguous and "unbekannt" in fm1.none.text(),
          "back to \"don't know\": unknown again, still changeable")
    n = asked["n"]
    ctx.connect_port(ctx.port)
    until(lambda: ctx.identity == ID and ctx.midi_connected, 10000)
    wait(800)
    check(asked["n"] == n, "reconnecting does not ask again by itself")

    # a device that tells its firmware itself has no such link
    from fm1companion.ui.common import firmware_changeable
    check(firmware_changeable(ctx) and not firmware_changeable(type("C", (), {
        "port": ctx.port, "identity": "FM-1_91060", "info": object(), "protocol": "felucca", "profile": None})()),
        "no link where the firmware answers for itself")
    w.close()
    app.quit()


def run():
    try:
        flow()
    except Exception:  # noqa: BLE001
        errors.append(traceback.format_exc())
        app.quit()


QTimer.singleShot(300, run)
QTimer.singleShot(180000, app.quit)
app.exec()
shutil.rmtree(home, ignore_errors=True)
fails = [r for r in results if not r[0]]
for e in errors:
    print("EXCEPTION:\n" + e)
print(f"{len(results) - len(fails)}/{len(results)} passed, {len(errors)} exceptions")
sys.exit(1 if fails or errors else 0)
