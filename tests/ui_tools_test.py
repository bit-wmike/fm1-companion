# SPDX-License-Identifier: GPL-3.0-only
"""Recovery › Tools and Settings › tool update check (offscreen, demo mode, German UI, no network):

* the Tools card lists M-Flasher, m-bootloader and SLOOP 3 with bundled version, version in use, checksum state and
  last check; "Show licence" opens the licence texts;
* "Check now" with FM1C_NO_NET=1 does not go online and says so;
* an offer found earlier shows an "Update" button; nothing is installed by itself;
* Settings: the tool check choice (daily / weekly / only at start / never), saved in config.json, disabled while
  the firmware check switch is off.

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
    app.quit()


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
