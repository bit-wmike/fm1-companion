# SPDX-License-Identifier: GPL-3.0-only
"""Acceptance (p1): device, firmware, recovery, device management – edge cases in demo mode.

Network gone (urllib raises), two devices at the same time, device switch with the Status page open, Devices page
(renaming with umlauts/long/equal names, forgetting, copying), wizard forward/back/cancel,
Overview with a generic device and update mode. Prints "N/M passed".

  flock /tmp/fm1c-ui.lock timeout 600 env QT_QPA_PLATFORM=offscreen python3 tests/ui_device_safety_test.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("FM1C_LANG", "de")      # these checks expect the German user interface
home = Path(tempfile.mkdtemp(prefix="fm1c-devsafe-"))
for k, sub in (("XDG_CONFIG_HOME", ".config"), ("XDG_DATA_HOME", ".local/share"), ("XDG_CACHE_HOME", ".cache")):
    os.environ[k] = str(home / sub)
os.environ["FM1C_DEMO"] = "1"
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu")
os.environ.setdefault("QT_LOGGING_RULES", "*.warning=true")

errors: list[str] = []
sys.excepthook = lambda t, v, tb: errors.append("".join(traceback.format_exception(t, v, tb)))
threading.excepthook = lambda a: errors.append("".join(traceback.format_exception(a.exc_type, a.exc_value, a.exc_traceback)))

import PyQt6.QtWebEngineWidgets  # noqa: E402,F401
from PyQt6.QtCore import QCoreApplication, QEventLoop, Qt, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog, QInputDialog, QMessageBox, QWizard  # noqa: E402

QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
app = QApplication(sys.argv)

from fm1companion import config, firmware  # noqa: E402

s = config.load()
s.user_root = str(home / "FM-1")
s.check_on_start = False
config.save(s)
config.ensure_dirs()

log_boxes: list[str] = []
answers = {"text": []}


def fake_msg_exec(self):
    log_boxes.append(self.text())
    for b in self.buttons():
        if self.buttonRole(b) in (QMessageBox.ButtonRole.AcceptRole, QMessageBox.ButtonRole.YesRole):
            self._clicked = b
            return 0
    self._clicked = None
    return 0


QMessageBox.exec = fake_msg_exec
QMessageBox.clickedButton = lambda self: getattr(self, "_clicked", None)
QInputDialog.getText = staticmethod(lambda *a, **k: (answers["text"].pop(0) if answers["text"] else "X", True))
QInputDialog.getMultiLineText = staticmethod(lambda *a, **k: ("Notiz", True))
_dialog_exec = QDialog.exec
plan_ctl = {"plans": [], "accept": True, "last_log": ""}


def dialog_exec(self):
    from fm1companion.ui.common import JobDialog
    from fm1companion.ui.device import FirmwareChoiceDialog
    from fm1companion.ui.firmware_page import PlanDialog
    if isinstance(self, FirmwareChoiceDialog):
        return 1
    if isinstance(self, PlanDialog):
        for cb in self.confirm_checks.values():
            cb.setChecked(True)
        plan_ctl["plans"].append(self.plan)
        return 1 if plan_ctl["accept"] else 0
    if isinstance(self, JobDialog):
        self.show()
        t0 = time.time()
        while time.time() - t0 < 60:
            app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
            if self.buttons.button(self.buttons.StandardButton.Close).isEnabled():
                break
        self.hide()
        plan_ctl["last_log"] = self.logv.toPlainText()
        return 1 if self.ok else 0
    return 1


QDialog.exec = dialog_exec

from fm1companion.ui.main_window import MainWindow  # noqa: E402

w = MainWindow()
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


def flow():
    from fm1companion.flash import planner
    from fm1companion.ui import firmware_page as fp
    ctx = w.ctx
    check(until(lambda: ctx.connected and ctx.port.serial == "DEMO-1"), "demo Melodee connected")

    # ---------------------------------------------------------------- Firmware offline
    w.go("firmware")
    check(until(lambda: not w.firmware.loading, 60000), "version list (with network) finished loading")
    real_open = urllib.request.urlopen

    def offline(*a, **k):
        raise urllib.error.URLError("Network is unreachable")
    urllib.request.urlopen = offline
    real_fw_open = firmware.urlopen                       # (the firmware module's guarded opener: https only)
    firmware.urlopen = offline
    firmware._releases_from_tags = lambda sp: []          # git ls-remote would otherwise go to the network
    for c in list((home / ".cache").rglob("releases-*.json")):
        c.unlink()
    try:
        w.go("firmware")
        page = w.firmware
        page.releases.clear()
        page.load_releases(force=True)
        ok = until(lambda: page.tree.topLevelItemCount() == 1 and "lade …" not in page.tree.topLevelItem(0).text(0), 20000)
        row = page.tree.topLevelItem(0).text(0) if page.tree.topLevelItemCount() else ""
        check(ok and "Keine Versionen" in row and "online" in row, f"offline: understandable line instead of \"lade …\" ({row[:90]})")
        check(not page.b_install.isEnabled() and not page.b_dl.isEnabled() and "Neu laden" in page.b_install.toolTip(),
              "offline: Install/Download off, tooltip explains")
        # Back to the original needs no network for the version list; the download fails understandably
        plan_ctl["accept"] = True
        log_boxes.clear()
        ok = page.return_to_original()
        log = plan_ctl["last_log"]
        tail = log.split("FEHLER:")[-1]
        check(not ok and "FEHLER" in log and "online" in tail and "nichts geändert" in tail,
              f"offline: V15 download fails understandably ({tail.strip()[:100]})")
        check(until(lambda: ctx.connected, 10000), "connected again after the failure")
        page._resources()
        tail = plan_ctl["last_log"].split("FEHLER:")[-1]
        check(any("Version nicht in der Liste" in b for b in log_boxes[-1:]) or "online" in tail,
              f"offline: rewriting resources fails understandably ({tail.strip()[:80]})")
        check(until(lambda: ctx.connected and ctx.port.serial == "DEMO-1", 10000), "connected again")
    finally:
        urllib.request.urlopen = real_open
        firmware.urlopen = real_fw_open

    # ---------------------------------------------------------------- two devices at the same time
    st = fp.device_state(ctx)
    check(st.ports >= 2 and st.pinned, f"DEMO-1 and DEMO-2 plugged in, target pinned ({st.ports}, {st.pinned})")
    p = planner.plan_install(st, planner.Target.from_spec(firmware.spec("melodee"), tag="v1.6.0"))
    check(p.ok and any("mehrere FM-1" in x for x in p.warnings), "two devices: plan allowed with a note (target at the USB port)")
    # switching while a job runs: ctx.hold blocks the scan, switching is not possible (modal)
    ctx.hold = True
    ctx.scan()
    check(ctx.port.serial == "DEMO-1", "hold: the scan does not switch the device")
    ctx.hold = False

    # ---------------------------------------------------------------- Overview / Status with DEMO-3 and update mode
    w.go("fm1")
    fm1 = w.page("fm1")
    check(until(lambda: fm1.presets or "eingelesen" in fm1.live_state.text(), 15000), "Status: Melodee read")
    ctx.connect_port(next(x for x in ctx.ports if x.serial == "DEMO-3"))
    check(until(lambda: ctx.identity == "FM-1_900", 15000), "DEMO-3 (generic) active")
    check(not fm1.content.isVisible() and "ohne Editor-Protokoll" in fm1.none.text() and not fm1.timer.isActive(),
          "Status with a generic device: explanation, no live queries")
    w.go("overview")
    ov = w.overview
    check("unbekannt" in ov.lbl["Firmware"].text() and "nur MIDI" in ov.lbl["Verbindung"].text()
          and not ov.b_backup.isEnabled() and "Editor-Protokoll" in ov.b_backup.toolTip(),
          f"Overview with DEMO-3: firmware unknown, MIDI only, backup off ({ov.lbl['Firmware'].text()})")
    check("unbekannt" in ov.up_text.text().lower(), f"Overview: the firmware card explains ({ov.up_text.text()[:60]})")
    ctx.disconnect("FM-1 getrennt")
    ctx.hold = True
    wait(200)
    check(all(ov.lbl[k].text() == "–" for k in ("Name", "Firmware", "Version", "Seriennummer", "USB"))
          and "kein FM-1" in ov.lbl["Verbindung"].text() and fm1.none.text() == "Kein FM-1 verbunden.",
          "disconnected: Overview and Status show \"–\"")
    ctx.hold = False
    check(until(lambda: ctx.connected and ctx.port.serial == "DEMO-1", 15000), "connected again (DEMO-1)")
    w.go("fm1")
    check(until(lambda: "eingelesen" in fm1.live_state.text() or fm1.timer.isActive(), 15000), "Status reads again")
    w.go("overview")
    wait(300)
    check(not fm1.timer.isActive() and fm1.held_link is None, "live values stop when leaving the page")

    # ---------------------------------------------------------------- Devices page
    w.go("devices")
    dv = w.page("devices")
    prof = ctx.profile
    ctx.store.create("Zweites Gerät", serial="DEMO-X")
    dv.refresh()
    dv.rename(prof.id, "Mein Studio – Gerät ÄÖÜß")
    check(prof.name == "Mein Studio – Gerät ÄÖÜß" and (Path(s.user_root) / "Backups" / prof.folder_name).is_dir(),
          f"renaming with umlauts ({prof.folder_name})")
    long = "L" * 300
    dv.rename(prof.id, long)
    check(prof.name == long and len(prof.folder_name.encode()) <= 100, "very long name: folder shortened")
    log_boxes.clear()
    dv.rename(prof.id, "zweites gerät")
    check(prof.name == long and any("schon vergeben" in b for b in log_boxes), "same name as another device: refused")
    dv.rename(prof.id, "FM-1 A")
    other = next(x for x in ctx.store.profiles.values() if x.id != prof.id)
    check(not dv.forget(prof.id, ask=False) and prof.id in ctx.store.profiles, "the active profile cannot be forgotten")
    check(not dv.b_copy.isEnabled() or dv.a.currentData() != prof.id, "copying: A = target is locked")
    comp, ok = dv.copy(other.id)
    check(not ok and any("kein Backup" in b for b in log_boxes[-1:]), "copying without a backup of the source: clear message")
    check(dv.forget(other.id, ask=False) and other.id not in ctx.store.profiles, "another profile forgotten")

    # ---------------------------------------------------------------- setup wizard
    from fm1companion.ui.wizard import FirstRunWizard
    wz = FirstRunWizard(ctx, w)
    wz.show()
    ids = wz.pageIds()
    for _ in ids[:-1]:
        wz.next()
        app.processEvents()
    check(wz.currentId() == ids[-1], "wizard: to the last page")
    for _ in ids[:-1]:
        wz.back()
    check(wz.currentId() == ids[0], "wizard: all the way back")
    wz.next()
    wz.p_folder.edit.setText("/proc/fm1c-nicht-beschreibbar")
    log_boxes.clear()
    wz.next()
    check(wz.currentPage() is wz.p_folder and any("nicht beschreibbar" in b or "nicht anlegen" in b for b in log_boxes),
          "wizard: a non-writable main folder is refused")
    wz.p_folder.edit.setText(s.user_root)
    wz.next()
    wz.next()
    wz.next()
    check("Gefunden" in wz.p_device.state.text(), f"wizard detects the device ({wz.p_device.state.text()[:60]})")
    wz.reject()
    check(not wz.isVisible(), "wizard cancelled")
    wz2 = FirstRunWizard(ctx, w)
    check(wz2.pageIds() == ids, "open the wizard again")
    wz2.reject()
    for x in (wz, wz2):                 # tear down before the window (otherwise a crash on quit)
        x.setParent(None)
        x.deleteLater()
    wait(200)
    app.quit()


def run():
    try:
        flow()
    except Exception:  # noqa: BLE001
        errors.append(traceback.format_exc())
        app.quit()


QTimer.singleShot(500, run)
QTimer.singleShot(400000, app.quit)
app.exec()
fails = [r for r in results if not r[0]]
for e in errors:
    print("EXCEPTION:\n" + e)
print(f"{len(results) - len(fails)}/{len(results)} passed, {len(errors)} exceptions")
import shutil as _sh
_sh.rmtree(home, ignore_errors=True)
sys.exit(1 if fails or errors else 0)
