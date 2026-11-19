# SPDX-License-Identifier: GPL-3.0-only
"""Recovery › Tools and Settings › tool update check (offscreen, demo mode, German UI, no network):

* the Tools card lists M-Flasher, m-bootloader and SLOOP 3 with bundled version, version in use, checksum state and
  last check; "Show licence" opens the licence texts;
* "Check now" with FM1C_NO_NET=1 does not go online and says so;
* an offer found earlier shows an "Update" button; nothing is installed by itself;
* Settings: the tool check choice (daily / weekly / only at start / never), saved in config.json, disabled while
  the firmware check switch is off;
* "Back up the whole flash" (Recovery › Tools) and "Write back …" (Backups › Flash backups) with the fake
  M-Flasher (tests/fake_mflasher.py, synthetic ids): the copy is listed, the write-back asks the danger question,
  answers M-Flasher's "yes" only on a match, and an image of another unit is refused with nothing written.

  QT_QPA_PLATFORM=offscreen python3 tests/ui_tools_test.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("FM1C_LANG", "de")
home = Path(tempfile.mkdtemp(prefix="fm1c-tools-"))
for k, sub in (("XDG_CONFIG_HOME", ".config"), ("XDG_DATA_HOME", ".local/share"), ("XDG_CACHE_HOME", ".cache")):
    os.environ[k] = str(home / sub)
os.environ["HOME"] = str(home)
os.environ["FM1C_DEMO"] = "1"
os.environ["FM1C_NO_NET"] = "1"
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu")

errors: list[str] = []
sys.excepthook = lambda t, v, tb: errors.append("".join(traceback.format_exception(t, v, tb)))

import PyQt6.QtWebEngineWidgets  # noqa: E402,F401
from PyQt6.QtCore import QCoreApplication, QEventLoop, Qt, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog, QLabel, QMessageBox, QPushButton  # noqa: E402

QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
app = QApplication(sys.argv)

from fm1companion import config  # noqa: E402

s = config.load()
s.user_root = str(home / "FM-1")
config.save(s)
QMessageBox.exec = lambda self: 0

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


def until(cond, ms=10000):
    t0 = time.time()
    while time.time() - t0 < ms / 1000:
        if cond():
            return True
        wait(100)
    return False


def flow():
    from fm1companion.tools import updates
    from fm1companion.ui import tools_card
    w.go("recovery")
    rec = w.page("recovery")
    card = getattr(rec, "tools", None)
    check(isinstance(card, tools_card.ToolsCard), "Recovery has the Tools card")
    check(set(card.rows) == {"m-flasher", "m-bootloader", "sloop3"}, f"three tools listed ({sorted(card.rows)})")
    texts = " ".join(lab.text() for lab in card.findChildren(QLabel))
    check("2.4" in texts and "3.4.0" in texts and "0.12.1" in texts, "bundled versions shown")
    check(card.rows["m-bootloader"]["verified"].text() == "geprüft" and
          card.rows["sloop3"]["verified"].text() == "geprüft", "m-bootloader and SLOOP 3: checksum verified")
    mf = card.rows["m-flasher"]["status"]
    if not mf.bundled_present:                 # (a source checkout without tools-bin/)
        check(card.rows["m-flasher"]["verified"].text() == "nicht geprüft" and not card.b_find.isEnabled() and
              "fetch_tools.py" in card.note.text(), "M-Flasher not bundled here: said so, find disabled")
    # licence dialog
    seen = []
    orig = QDialog.exec

    def grab(self):
        if isinstance(self, tools_card.LicenceDialog):
            seen.append(self.text.toPlainText())
            return 0
        return orig(self)
    QDialog.exec = grab
    try:
        card.rows["m-flasher"]["licence"].click()
        card.rows["sloop3"]["licence"].click()
    finally:
        QDialog.exec = orig
    check(len(seen) == 2 and "Permission is hereby granted" in seen[0] and "GNU GENERAL PUBLIC LICENSE" in seen[1],
          "Show licence: MIT text for M-Flasher, GPL for SLOOP 3")
    # Check now without network
    card.check_now()
    check(until(lambda: "FM1C_NO_NET" in card.note.text(), 8000), f"Check now with FM1C_NO_NET: says so "
                                                                   f"({card.note.text()[:80]})")
    # an offer from an earlier check: Update button, nothing installed by itself
    cache = {"last": time.time(), "tools": {"sloop3": {"checked": time.time(), "error": "", "notified": "",
                                                        "offer": {"key": "sloop3", "tag": "v3.9.0-beta",
                                                                  "version": "3.9.0", "date": "2027-01-01",
                                                                  "asset": "sloop3-3.9.0.area",
                                                                  "url": "https://example.invalid/x.area",
                                                                  "digest": "a" * 64}}}}
    updates.cache_file().parent.mkdir(parents=True, exist_ok=True)
    updates.cache_file().write_text(json.dumps(cache))
    card.refresh()
    up = card.rows["sloop3"].get("update")
    check(isinstance(up, QPushButton) and "3.9.0" in card.note.text(), "offer: Update button and notice")
    check(not (Path(os.environ["XDG_DATA_HOME"]) / "fm1-companion/tools/sloop3/3.9.0").exists(),
          "nothing fetched by itself")
    # a second click on Update while the first download runs starts nothing (review L8)
    asked, jobs = [], []
    orig_confirm, orig_job = tools_card.confirm, tools_card.run_job
    tools_card.confirm = lambda *a, **k: asked.append(a[1]) or True
    tools_card.run_job = lambda *a, **k: jobs.append(a)
    try:
        up.click()
        up.click()
        card.update_tool(card.rows["sloop3"]["status"].offer)
    finally:
        tools_card.confirm, tools_card.run_job = orig_confirm, orig_job
    check(len(asked) == 1 and len(jobs) == 1 and not up.isEnabled(), f"Update: double click starts one download "
                                                                      f"({len(asked)} asked, {len(jobs)} jobs)")
    card._updating = False
    # Settings
    from fm1companion.ui.settings_dialog import SettingsDialog
    dlg = SettingsDialog(w.ctx, w)
    combo = dlg.tools_check
    check([combo.itemData(i) for i in range(combo.count())] == ["daily", "weekly", "start", "off"] and
          combo.currentData() == "daily", "Settings: daily (default) / weekly / start / off")
    dlg.check.setChecked(False)
    check(not combo.isEnabled(), "disabled while the firmware check is off")
    dlg.check.setChecked(True)
    combo.setCurrentIndex(combo.findData("weekly"))
    dlg._ok()
    check(config.load().tools_check == "weekly", "saved in the settings")
    dlg.deleteLater()
    check(updates.auto_check_allowed(config.load())[0] is False, "FM1C_NO_NET: no automatic check")
    flash_flow(card)
    app.quit()


def _job_exec(self):
    """a job dialog: wait for the job, then close"""
    loop = QEventLoop()
    t = QTimer()
    t.timeout.connect(lambda: loop.quit() if self.ok or self.error else None)
    t.start(100)
    QTimer.singleShot(60000, loop.quit)
    loop.exec()
    t.stop()
    wait(200)
    return 0


def fake_store():
    """a tool store whose bundled, write-verified M-Flasher is tests/fake_mflasher.py"""
    import copy
    import hashlib
    from fm1companion.tools import manifest as mfm, store as st
    arch = mfm.arch() or "x86_64"
    data = copy.deepcopy(mfm.load())
    ver = data["tools"]["m-flasher"]["version"]
    binp = home / "tools-bin" / mfm.mflasher_relpath(ver, arch)
    binp.parent.mkdir(parents=True, exist_ok=True)
    body = f"#!{sys.executable}\n" + (ROOT / "tests" / "fake_mflasher.py").read_text("utf-8").split("\n", 1)[1]
    binp.write_text(body, "utf-8")
    binp.chmod(0o755)
    h = hashlib.sha256(body.encode()).hexdigest()
    data["tools"]["m-flasher"]["arch"][arch].update(binary_sha256=h, binary_size=len(body.encode()))
    data["tools"]["m-flasher"]["write_verified"][ver][arch] = h
    return st.Store(bin_dir=home / "tools-bin", updates_dir=home / "upd", data=data, machine=arch)


def flash_flow(card):
    from fm1companion.ui import common as _common, flash_tools
    from fm1companion import flashdump
    uid, other = "13579bdf02468ace", "2468ace013579bdf"           # synthetic
    state = home / "fake-mflasher.json"
    os.environ["FAKE_MFLASHER_STATE"] = str(state)
    state.write_text(json.dumps({"mode": "midi", "unit_id": uid}))
    card._store = w.ctx.tools_store = fake_store()
    card.refresh()
    check(card.b_backup.isEnabled() and card.b_restore.isEnabled(), "flash buttons enabled with an M-Flasher")
    _common.JobDialog.exec = lambda self: _job_exec(self)
    asked, errors_shown = [], []
    flash_tools.confirm = lambda parent, title, text, ok=None, danger=False: asked.append((title, text, danger)) or True
    flash_tools.show_error = lambda parent, title, msg, details="": errors_shown.append(msg)
    c = w.ctx
    prof = c.profile
    check(prof is not None, f"demo device has a profile ({getattr(prof, 'name', '-')})")
    card.b_backup.click()
    folder = c.device_folder(c.settings.backups_dir) / "Flash"
    bins = sorted(folder.glob("*.bin"))
    st_ = json.loads(state.read_text())
    check(len(bins) == 1 and [x[0] for x in st_["calls"]].count("backup") == 2,
          f"whole flash backed up: read twice, one copy kept ({[b.name for b in bins]})")
    check(bins and json.loads(bins[0].with_suffix(".json").read_text())["unit id"] == uid and
          "Flash" in card.note.text(), "its description names the unit; the card says where it is")
    check(st_["mode"] == "midi" and prof.unit_id == uid, "restarted afterwards; the profile learned the unit id")
    # Backups › Flash backups lists it
    w.go("backups")
    wait(300)
    bp = w.backups
    bp.refresh()
    until(lambda: bp.flash_tree.topLevelItemCount() == 1, 5000)
    check(bp.flash_tree.topLevelItemCount() == 1 and bp.flash_restore_btn.isEnabled(), "listed on the Backups page")
    # the flash changes; "Write back …" puts the copy back after the danger question
    img = bytearray((Path(str(state) + ".flash")).read_bytes())
    img[0:16] = b"\x5a" * 16
    Path(str(state) + ".flash").write_bytes(bytes(img))
    bp.flash_tree.setCurrentItem(bp.flash_tree.topLevelItem(0))
    asked.clear()
    bp.flash_restore_btn.click()
    st_ = json.loads(state.read_text())
    danger = [a for a in asked if a[2]]
    check(len(danger) == 1 and prof.name in danger[0][1] and "0x00000" in danger[0][1],
          "the danger question names the unit and says the boot area is included")
    check([a.strip() for a in st_.get("answers", [])] == ["yes"] and
          Path(str(state) + ".flash").read_bytes() == bins[0].read_bytes(), "written back: the flash equals the copy")
    check(len(flashdump.list_backups(folder)) == 2, "the automatic backup before the write is listed too")
    # an image of another unit: refused, nothing written
    state.write_text(json.dumps({"mode": "midi", "unit_id": other}))
    prof.unit_id = ""
    img[0:16] = b"\x33" * 16
    Path(str(state) + ".flash").write_bytes(bytes(img))
    before = bytes(img)
    asked.clear()
    flash_tools.restore_flow(bp, c, bins[0], store=card._store)
    st_ = json.loads(state.read_text())
    check(not st_.get("answers") and not st_.get("written") and Path(str(state) + ".flash").read_bytes() == before and
          not [a for a in asked if a[2]], "another unit's image: no danger question, no yes, nothing written")


def run():
    try:
        flow()
    except Exception:  # noqa: BLE001
        errors.append(traceback.format_exc())
        app.quit()


QTimer.singleShot(800, run)
QTimer.singleShot(120000, app.quit)
app.exec()
fails = [r for r in results if not r[0]]
for e in errors:
    print("EXCEPTION:\n" + e)
print(f"{len(results) - len(fails)}/{len(results)} passed, {len(errors)} exceptions")
sys.exit(1 if fails or errors else 0)
