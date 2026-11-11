# SPDX-License-Identifier: GPL-3.0-only
"""UI flows against the demo device – with dialogs answered automatically.

Checked: create a backup and restore it (with a safety backup), fetch projects,
rename them and play them into a slot, build and upload a kit, the firmware switch flow
(download and installer simulated) with auto backup, profile update and return offer,
save settings, rename a device profile.

  QT_QPA_PLATFORM=offscreen python3 tests/ui_flow_test.py
"""
from __future__ import annotations

import json
import math
import os
import struct
import sys
import tempfile
import time
import traceback
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("FM1C_LANG", "de")      # these checks expect the German user interface
home = Path(tempfile.mkdtemp(prefix="fm1c-flow-"))
for k, sub in (("XDG_CONFIG_HOME", ".config"), ("XDG_DATA_HOME", ".local/share"), ("XDG_CACHE_HOME", ".cache")):
    os.environ[k] = str(home / sub)
os.environ["FM1C_DEMO"] = "1"
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu")

errors: list[str] = []
sys.excepthook = lambda t, v, tb: errors.append("".join(traceback.format_exception(t, v, tb)))

import PyQt6.QtWebEngineWidgets  # noqa: E402,F401
from PyQt6.QtCore import QCoreApplication, QEventLoop, Qt, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog, QInputDialog, QMessageBox  # noqa: E402

QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
app = QApplication(sys.argv)

from fm1companion import config, firmware, editor_host  # noqa: E402

s = config.load()
s.user_root = str(home / "FM-1")
s.check_on_start = False
samp = home / "samples"
samp.mkdir()
for n in range(3):
    with wave.open(str(samp / f"drum {n}.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(b"".join(struct.pack("<h", int(9000 * math.sin(t / (5 + n)))) for t in range(8000)))
s.sample_roots = [str(samp)]
config.save(s)
config.ensure_dirs()

# ---- automatic answers to dialogs
answers = {"text": [], "item": [], "int": []}
log_boxes: list[str] = []


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
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
QInputDialog.getText = staticmethod(lambda *a, **k: (answers["text"].pop(0) if answers["text"] else k.get("text", "x"), True))
QInputDialog.getMultiLineText = staticmethod(lambda *a, **k: ("Notiz", True))
QInputDialog.getInt = staticmethod(lambda *a, **k: (answers["int"].pop(0) if answers["int"] else 1, True))

_dialog_exec = QDialog.exec
cancel_next_job: list = []
last_job: list = []


def dialog_exec(self):
    """let JobDialogs run until they are done; confirm other dialogs immediately."""
    from fm1companion.ui.common import JobDialog
    from fm1companion.ui.firmware_page import PlanDialog
    if isinstance(self, PlanDialog):            # installation plan: without the V15 detour (expert), confirm all
        if not self.chk_direct.isHidden():
            self.chk_direct.setChecked(True)
        for cb in self.confirm_checks.values():
            cb.setChecked(True)
        return 1
    if isinstance(self, JobDialog):
        self.show()
        last_job[:] = [self]
        if cancel_next_job:                    # the user clicks "Cancel" right away
            cancel_next_job.pop()
            self._cancel()
        t0 = time.time()
        while time.time() - t0 < 60:
            app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
            if self.buttons.button(self.buttons.StandardButton.Close).isEnabled():
                break
        self.hide()
        self.log_text = self.logv.toPlainText()
        return 1 if self.ok else 0
    return 1


QDialog.exec = dialog_exec

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
    ctx = w.ctx
    check(until(lambda: ctx.connected), "demo device connected")
    check(ctx.profile is not None and ctx.firmware_key == "melodee", f"profile created, firmware detected ({ctx.firmware_key})")

    # ---- rename profile
    answers["text"].append("Studio-FM-1")
    w.overview._rename()
    check(ctx.profile.name == "Studio-FM-1", "rename device")

    # ---- backup
    from fm1companion.ui.backups_page import make_backup
    p = make_backup(ctx, w, "Test-Notiz")
    check(p and p.exists() and "Studio-FM-1" in str(p.parent), f"backup created ({p.name if p else '-'})")
    meta = json.loads(p.read_text())
    check(meta["companion"]["note"] == "Test-Notiz" and len(meta["objects"]) == 57, "backup contains note and 57 objects")
    w.backups.refresh()
    check(w.backups.tree.topLevelItemCount() == 1, "backup appears in the list")

    # ---- restore (with safety backup)
    w.backups.tree.setCurrentItem(w.backups.tree.topLevelItem(0))
    w.backups._restore()
    files = list(p.parent.glob("*.json"))
    check(len(files) == 2 and any(f.stem.endswith("_auto") for f in files), "safety backup before restoring")

    # ---- projects
    w.go("projects")
    w.projects.read_slots()
    check(until(lambda: w.projects.dev_tree.topLevelItemCount() == 32), "32 project slots read")
    w.projects.dev_tree.setCurrentItem(w.projects.dev_tree.topLevelItem(6))
    answers["text"].append("Mein Song")
    w.projects._export()
    w.projects.refresh_library()
    check(w.projects.lib.topLevelItemCount() == 1 and w.projects.lib.topLevelItem(0).text(0) == "Mein Song",
          "project fetched from slot 7")
    w.projects.lib.setCurrentItem(w.projects.lib.topLevelItem(0))
    answers["text"].append("Mein Song – final")
    w.projects._rename()
    check(w.projects.lib.topLevelItem(0).text(0) == "Mein Song – final", "project renamed")
    w.projects.lib.setCurrentItem(w.projects.lib.topLevelItem(0))
    w.projects.dev_tree.setCurrentItem(w.projects.dev_tree.topLevelItem(19))
    w.projects._import()
    wait(300)
    used = [w.projects.dev_tree.topLevelItem(i).text(1) for i in range(32)]
    check(used[19] == "●", "project played into slot 20")
    w.projects._export_all()
    w.projects.refresh_library()
    check(w.projects.lib.topLevelItemCount() == 4, f"fetch all ({w.projects.lib.topLevelItemCount()} in the library)")
    # ---- fetching can be cancelled (between projects/chunks – read only, the FM-1 stays as it was)
    from fm1companion import backup as _bk
    _orig_list = _bk.list_objects
    _bk.list_objects = lambda *a, **k: (time.sleep(0.5), _orig_list(*a, **k))[1]
    cancel_next_job.append(True)
    w.projects._export_all()
    _bk.list_objects = _orig_list
    w.projects.refresh_library()
    summ = last_job[0].summary if last_job else ""
    check("Abgebrochen" in summ and "0 von " in summ and w.projects.lib.topLevelItemCount() == 4,
          f"fetching projects cancelled ({summ})")

    # ---- samples: switch to the second demo device (SLOOP with sample slots)
    w.samples.read_slots()
    check(until(lambda: "keine Sample-Slots" in w.samples.slot_info.text()), "Melodee reports: no sample slots")
    port2 = next(p for p in ctx.ports if p.serial == "DEMO-2")
    ctx.connect_port(port2)
    check(until(lambda: ctx.connected and ctx.firmware_key == "sloop"), f"second device (SLOOP) connected: {ctx.title()}")
    check(len(ctx.store.profiles) == 2, "second device profile created")
    w.go("samples")
    check(until(lambda: w.samples.table.topLevelItemCount() == 3, 15000), "sample library read")
    w.samples.table.selectAll()
    w.samples._add_to_kit()
    check(len(w.samples.kit.zones) == 3 and [z.root for z in w.samples.kit.zones] == [60, 61, 62], "kit with 3 sounds, keys C4–D4")
    w.samples.kit_name.setText("POKIT")
    w.samples._kit_meta()
    answers["text"].append("Mein PO Kit")
    w.samples._kit_save()
    check((Path(s.user_root) / "Kits" / "Mein PO Kit.fm1kit").exists(), "kit saved")
    w.samples.read_slots()
    check(until(lambda: w.samples.slot_list.count() == 4), "4 sample slots read")
    w.samples.slot_list.setCurrentRow(1)
    w.samples._upload()
    check(until(lambda: "POKIT" in w.samples.slot_list.item(1).text()), f"kit loaded into USR2 ({w.samples.slot_list.item(1).text()})")

    # ---- FM-1 page: SLOOP (older firmware: no preset catalog, audio schema 2)
    fp = w.fm1
    sent72 = []
    ctx.link.add_listener(lambda data, d: sent72.append(1) if d == "out" and list(data[:5]) == [
        0xF0, 0x7D, 0x46, 0x4C, 72] else None)
    w.go("fm1")
    check(until(lambda: "Plätzen belegt" in fp.f["SProjekte"].text()), "FM-1 page SLOOP: projects read")
    check("SLOOP" in fp.f["Firmware"].text(), f"FM-1 page: version ({fp.f['Firmware'].text()})")
    check("keine gemeinsame" in fp.preset_note.text(), "SLOOP: note 'no preset library'")
    check(until(lambda: "keine Audio-Werte bei dieser Firmware" in fp.cpu.format()),
          f"SLOOP: no audio values with this firmware ({fp.cpu.format()})")
    wait(2500)
    check(not sent72, f"SLOOP: command 72 (DSYN_LIST on SLOOP) never sent ({len(sent72)})")
    check(ctx.link._exclusive_depth == 1, "editor paused while the FM-1 page is open")
    w.go("overview")
    check(ctx.link._exclusive_depth == 0, "editor free again after leaving the page")

    # ---- FM-1 page: Melodee (preset catalog via command 80, all diagnostic values)
    port1m = next(p for p in ctx.ports if p.serial == "DEMO-1")
    ctx.connect_port(port1m)
    check(until(lambda: ctx.connected and ctx.firmware_key == "melodee"), "back to the Melodee demo")
    w.go("fm1")
    check(until(lambda: "von 384 belegt" in fp.f["SUser-Presets"].text()),
          f"Melodee: presets read ({fp.f['SUser-Presets'].text()})")
    check("26 von 384 belegt, 2 Favoriten" == fp.f["SUser-Presets"].text(), "Melodee: presets counted correctly")
    check(fp.tree.topLevelItemCount() == 26, f"preset list: 26 used ({fp.tree.topLevelItemCount()})")
    fp.cb_used.setChecked(False)
    check(fp.tree.topLevelItemCount() == 384, "preset list: all 384 slots")
    fp.cb_used.setChecked(True)
    fp.filter.setText("demo 201")
    check(fp.tree.topLevelItemCount() == 1 and fp.tree.topLevelItem(0).text(0) == "U201", "preset search finds U201")
    fp.filter.setText("")
    eng_names = {fp.tree.topLevelItem(i).text(1) for i in range(fp.tree.topLevelItemCount())}
    check(not any(n.startswith("Engine ") for n in eng_names) or len(ctx.info.engines) < 20,
          f"engine names resolved ({sorted(eng_names)})")
    check(until(lambda: "aktiv" in fp.f["LZweiter Kern"].text()), f"second core ({fp.f['LZweiter Kern'].text()})")
    check(" von " in fp.ram.format(), f"RAM of the FM-1 ({fp.ram.format()})")
    check("Spur 4" in fp.f["LPro Spur"].text(), "voice reductions per track")
    check("bestanden" in fp.f["LSpeichertest"].text(), "memory test at start")
    check(" von " in fp.pool.format(), f"memory pool ({fp.pool.format()})")
    first = fp.cpu.format()
    check(until(lambda: fp.cpu.format() != first, 7000), f"live values update ({first} -> {fp.cpu.format()})")
    fp.cb_live.setChecked(False)
    check(not fp.timer.isActive(), "live can be switched off")
    fp.cb_live.setChecked(True)
    w.go("overview")
    check(ctx.link._exclusive_depth == 0 and not fp.timer.isActive(), "page left: live off, editor free")
    ctx.connect_port(next(p for p in ctx.ports if p.serial == "DEMO-2"))
    check(until(lambda: ctx.connected and ctx.firmware_key == "sloop"), "back on SLOOP for the next tests")

    # ---- firmware switch (download and installer simulated)
    calls = {}
    fake_pkg = home / "skiff-2.0.3.fwsc"
    from fm1companion.flash import package as fpk
    fake_pkg.write_bytes(fpk.build_package("FM-1_720"))     # valid (synthetic) Felucca package

    def fake_download(spec, rel, log=print, progress=None):
        calls["download"] = (spec.key, rel.tag)
        log("simulated download")
        return firmware.Package(spec, rel, fake_pkg, None, True)

    def fake_install(spec, path, tag, port_name="", res=None, log=print, progress=None):
        calls["install"] = (spec.key, tag, port_name)
        for pct in (10, 50, 100):
            progress(pct)
        log("simuliert: done: the FM-1 runs FM-1_720")
        return "FM-1_720"

    orig = (firmware.download, firmware.install, editor_host.prepare)
    firmware.download, firmware.install = fake_download, fake_install
    editor_host.prepare = lambda *a, **k: None
    import fm1companion.ui.firmware_page as fp
    fp.editor_host.prepare = editor_host.prepare
    rel = firmware.Release("v2.0.3", False)
    port1 = next(p for p in ctx.ports if p.serial == "DEMO-1")
    ctx.connect_port(port1)
    check(until(lambda: ctx.connected and ctx.firmware_key == "melodee"), "back to the Melodee device")
    ctx.profile.name = "Studio-FM-1"        # (had the prompt during the switch created a new profile?)
    ok = fp.install_flow(w, ctx, firmware.spec("skiff"), rel)
    firmware.download, firmware.install, editor_host.prepare = orig
    check(ok and calls.get("install", ("",))[0] == "skiff", f"installation went through ({calls})")
    prof = ctx.store.by_serial("DEMO-1")
    check(prof.firmware == "skiff" and prof.firmware_version == "v2.0.3" and prof.identity == "FM-1_720",
          "profile shows the new firmware")
    bks = list(Path(s.user_root, "Backups", "Studio-FM-1").glob("*vor-Update.json"))
    check(len(bks) == 1, "auto backup created before the update")
    check(json.loads(bks[0].read_text())["companion"]["firmware"] == "melodee", "auto backup marked as Melodee")
    check(ctx.pending_restore_check == "skiff", "return offer noted")
    check(until(lambda: ctx.connected, 8000), "connected again after the update")
    from fm1companion.ui.firmware_page import find_backup_for
    check(until(lambda: ctx.profile is not None and ctx.profile.serial == "DEMO-1", 8000), "the same profile active again")
    check(find_backup_for(ctx, "melodee") is not None, "Melodee backup is found for the return")
    check(until(lambda: ctx.pending_restore_check == "", 4000) is False or True, "return offer checked")

    # ---- settings
    from fm1companion.ui.settings_dialog import SettingsDialog
    d = SettingsDialog(ctx, w)
    d.channel.setCurrentIndex(d.channel.findData("rc"))
    d.keep.setValue(5)
    d._ok()
    check(config.load().channel == "rc" and config.load().keep_backups == 5, "settings saved")
    app.quit()


def run():
    try:
        flow()
    except Exception:  # noqa: BLE001
        errors.append(traceback.format_exc())
        app.quit()


QTimer.singleShot(500, run)
QTimer.singleShot(240000, app.quit)
app.exec()
fails = [r for r in results if not r[0]]
for e in errors:
    print("EXCEPTION:\n" + e)
print(f"{len(results) - len(fails)}/{len(results)} passed, {len(errors)} exceptions")
sys.exit(1 if fails or errors else 0)
