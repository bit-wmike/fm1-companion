# SPDX-License-Identifier: GPL-3.0-only
"""Installer workshop: plan dialog, installation, return to the original, recovery, generic device (demo).

Checked: plan with confirmations, installation of a file of your own with progress, dry run, failed or
deselected backup, renamed foreign file, several FM-1, device gone while the plan is open, renaming,
back to the original (V15) including a broken target package and abort after the V15 step,
recovery of an interrupted installation on DEMO-2 and level 2 in the simulation.

  QT_QPA_PLATFORM=offscreen python3 tests/ui_install_test.py
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
home = Path(tempfile.mkdtemp(prefix="fm1c-inst-"))
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
QInputDialog.getText = staticmethod(lambda *a, **k: (answers["text"].pop(0) if answers["text"] else "SCHREIBEN", True))
QInputDialog.getMultiLineText = staticmethod(lambda *a, **k: ("Notiz", True))
QInputDialog.getInt = staticmethod(lambda *a, **k: (answers["int"].pop(0) if answers["int"] else 1, True))

_dialog_exec = QDialog.exec


plan_ctl = {"confirm": True, "backup": None, "plans": [], "accept": True, "asked_fw": 0, "last_log": ""}


def dialog_exec(self):
    """let JobDialogs run until they are done; confirm other dialogs immediately.
    Installation plan: tick all confirmations (plan_ctl), backup checkbox as wanted."""
    from fm1companion.ui.common import JobDialog
    from fm1companion.ui.device import FirmwareChoiceDialog
    from fm1companion.ui.firmware_page import PlanDialog
    if isinstance(self, FirmwareChoiceDialog):
        plan_ctl["asked_fw"] += 1
        return 1                                   # preselection "weiß nicht" (don't know)
    if isinstance(self, PlanDialog):
        if plan_ctl["backup"] is not None:
            self.chk_backup.setChecked(plan_ctl["backup"])
        if plan_ctl["confirm"]:
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
        self.log_text = self.logv.toPlainText()
        plan_ctl["last_log"] = self.log_text
        if not self.ok or os.environ.get("FM1C_TEST_VERBOSE"):
            print("---- log " + self.windowTitle() + ":\n" + self.log_text[-1500:], flush=True)
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
    import shutil
    from fm1companion.flash import demo, journal, package as fpk
    from fm1companion.ui import firmware_page as fp
    from fm1companion.vendor import fm1_install as fi
    editor_host.prepare = lambda *a, **k: None
    fp.editor_host.prepare = editor_host.prepare
    ctx = w.ctx
    check(until(lambda: ctx.connected and ctx.port.serial == "DEMO-1"), "demo Melodee connected")
    check(until(lambda: ctx.identity == "FM-1_91060"), f"handshake identity read ({ctx.identity})")
    check(ctx.protocol == "felucca" and ctx.midi_connected, "protocol: felucca")

    # ---- generic device (firmware without editor protocol)
    ctx.connect_port(next(p for p in ctx.ports if p.serial == "DEMO-3"))
    check(until(lambda: ctx.identity == "FM-1_900", 15000), f"DEMO-3 reports FM-1_900 ({ctx.identity})")
    check(not ctx.connected and ctx.midi_connected and ctx.protocol == "generic", "DEMO-3: MIDI only, no Felucca")
    check("FM-1_900" in ctx.title() and "nur MIDI" in ctx.status_text(), f"Titel/Status: {ctx.title()} / {ctx.status_text()}")
    check(ctx.firmware_guess[:1] == ["sloop"] or "sloop" in ctx.firmware_guess, f"candidates {ctx.firmware_guess}")
    check(ctx.firmware_key == "" and "unbekannt – bitte wählen" in ctx.title(),
          f"DEMO-3 (ORBIT) is not guessed as SLOOP ({ctx.firmware_key!r}, {ctx.title()})")
    check(until(lambda: ctx.profile.firmware_choice.get("FM-1_900") == "?", 5000) and plan_ctl["asked_fw"] == 1,
          f"asked once, \"don't know\" remembered ({plan_ctl['asked_fw']})")
    from fm1companion.flash import planner
    st3 = fp.device_state(ctx)
    check(st3.firmware_key == "" and planner.v15_option(st3, planner.Target.from_spec(firmware.spec("sloop")))
          == "default_on", "unknown firmware counts as a switch: V15 intermediate step preselected")
    ctx.connect_port(next(p for p in ctx.ports if p.serial == "DEMO-3"))
    check(until(lambda: ctx.identity == "FM-1_900", 15000), "DEMO-3 reconnected")
    wait(800)
    check(plan_ctl["asked_fw"] == 1, "not asked again")
    ctx.choose_firmware("orbit")
    check(ctx.profile.firmware_choice.get("FM-1_900") == "orbit" and ctx.firmware_key == "orbit",
          "the firmware choice is remembered")

    # ---- own file on Melodee (update of the same firmware: no V15 step)
    ctx.connect_port(next(p for p in ctx.ports if p.serial == "DEMO-1"))
    check(until(lambda: ctx.connected and ctx.port.serial == "DEMO-1"), "back on DEMO-1")
    # ---- the plan dialog: a switch goes via V15 first, the expert opt-out is off and not remembered
    stm = fp.device_state(ctx)
    check(stm.firmware_name == "Melodee", f"the plan names the running firmware ({stm.firmware_name})")

    def plan_for(spec):
        return lambda via, b=True: planner.plan_install(stm, planner.Target.from_spec(spec, tag="v3.1"), via_v15=via,
                                                        backup=b)
    dlg = fp.PlanDialog(w, plan_for(firmware.spec("karma")), "KARMA", True)
    check(not dlg.chk_direct.isHidden() and not dlg.chk_direct.isChecked() and dlg.plan.via_stock
          and dlg.plan.kinds()[:3] == ["backup", "fetch", "stock"] and "Melodee" in dlg.v15_note.text()
          and "KARMA" in dlg.v15_note.text(), f"switch Melodee → KARMA: V15 first, shown ({dlg.v15_note.text()[:80]})")
    dlg.chk_direct.setChecked(True)
    check(dlg.plan.direct and not dlg.plan.via_stock and "stock" not in dlg.plan.kinds()
          and any("V15" in x and "KARMA" in x for x in dlg.plan.warnings), "expert opt-out: directly, with a warning")
    dlg.chk_direct.setChecked(False)
    check(dlg.plan.via_stock and not dlg.plan.direct, "opt-out undone: V15 again")
    dlg2 = fp.PlanDialog(w, plan_for(firmware.spec("karma")), "KARMA", True)
    check(dlg2.plan.via_stock and not dlg2.chk_direct.isChecked(), "a new plan does not remember the opt-out")
    dlg3 = fp.PlanDialog(w, plan_for(firmware.spec("melodee")), "Melodee", True)
    check(dlg3.chk_direct.isHidden() and dlg3.v15_note.isHidden() and "stock" not in dlg3.plan.kinds(),
          "update of the same firmware: no V15 step, no option")
    for d in (dlg, dlg2, dlg3):
        d.deleteLater()
    pkg = home / "melodee-1.7.0.fwsc"
    pkg.write_bytes(fpk.build_package("FM-1_91070"))
    ok = fp.install_flow(w, ctx, firmware.spec("melodee"), None, pkg, "v1.7.0")
    dev = demo.device("demo-in")
    check(ok and dev.identity == "FM-1_91070" and dev.flash == fpk.analyze(pkg).image,
          f"installation via plan + progress ({dev.identity})")
    hist = journal.Journal().history()
    check(hist and hist[-1]["phase"] == "done" and hist[-1]["product"] == "FM-1_91070", "journal: done")
    prof = ctx.store.by_serial("DEMO-1")
    check(prof.firmware_history and prof.firmware_history[-1]["version"] == "v1.7.0", "firmware history in the profile")

    p0 = plan_ctl["plans"][-1]
    check("unknown_file" in p0.confirmations and "backup" in p0.kinds(),
          "own file without a known checksum: confirmation \"unknown file\", backup step in the plan")

    # ---- a dry run writes nothing
    check(until(lambda: ctx.connected, 10000), "connected again after the installation")
    before = len(dev.sends)
    ok = fp.install_flow(w, ctx, firmware.spec("melodee"), None, pkg, "v1.7.0", dry_run=True)
    check(ok and bytes(fi.UPGRADE) not in dev.sends[before:], "dry run sends no UPGRADE")

    # ---- backup wanted, but failed: always abort, nothing sent
    from fm1companion import autobackup
    check(until(lambda: ctx.connected, 10000), "connected")
    real_capture = autobackup.capture_to_file

    def broken_capture(*a, **k):
        raise PermissionError(13, "Permission denied", str(home / "FM-1" / "Backups"))
    autobackup.capture_to_file = broken_capture
    before, ident0 = len(dev.sends), dev.identity
    try:
        ok = fp.install_flow(w, ctx, firmware.spec("melodee"), None, pkg, "v1.7.0")
    finally:
        autobackup.capture_to_file = real_capture
    check(not ok and bytes(fi.UPGRADE) not in dev.sends[before:] and dev.identity == ident0
          and "Backup fehlgeschlagen" in plan_ctl["last_log"] and "Keine Schreibrechte" in plan_ctl["last_log"],
          "a failed backup aborts the installation (readable message, nothing sent)")

    # ---- backup deselected: confirmation "no_backup" in the plan, no backup step
    check(until(lambda: ctx.connected, 10000), "connected")
    plan_ctl["backup"], plan_ctl["confirm"] = False, False
    before = len(dev.sends)
    ok = fp.install_flow(w, ctx, firmware.spec("melodee"), None, pkg, "v1.7.0")
    p1 = plan_ctl["plans"][-1]
    check(not ok and "no_backup" in p1.confirmations and "backup" not in p1.kinds()
          and bytes(fi.UPGRADE) not in dev.sends[before:],
          "without a backup: required confirmation in the plan, without it nothing is installed")
    plan_ctl["backup"], plan_ctl["confirm"] = None, True
    s.auto_backup_before_update = False
    check(until(lambda: ctx.connected, 10000), "connected")
    ok = fp.install_flow(w, ctx, firmware.spec("melodee"), None, pkg, "v1.7.0", dry_run=True)
    p2 = plan_ctl["plans"][-1]
    s.auto_backup_before_update = True
    check("no_backup" in p2.confirmations and "backup" not in p2.kinds(),
          "setting \"no auto backup\": the plan requires the confirmation \"no_backup\"")

    # ---- renamed foreign file: not detected as Melodee (only the checksum counts)
    check(until(lambda: ctx.connected, 10000), "connected")
    fake = home / "melodee-1.6.0-rc.7.fwsc"
    fake.write_bytes(fpk.build_package("FM-1_906", seed=9))
    plan_ctl["accept"] = False
    log_boxes.clear()
    w.firmware._install_local(str(fake))
    plan_ctl["accept"] = True
    p3 = plan_ctl["plans"][-1]
    check(p3.target.key == "eigene" and not p3.target.known and "unknown_file" in p3.confirmations
          and any("unbekannt" in b for b in log_boxes),
          f"renamed foreign file stays unknown ({p3.target.key}, {p3.confirmations})")

    # ---- several FM-1, target cannot be pinned: lock
    real_pin = fp.target_pin
    fp.target_pin = lambda c: None
    try:
        before = len(dev.sends)
        ok = fp.install_flow(w, ctx, firmware.spec("melodee"), None, pkg, "v1.7.0")
        p4 = plan_ctl["plans"][-1]
    finally:
        fp.target_pin = real_pin
    check(not ok and any(planner.MULTI_DEVICE_TEXT in b for b in p4.blockers)
          and bytes(fi.UPGRADE) not in dev.sends[before:], "several FM-1 without a unique target: lock")

    # ---- the device disappears while the plan is open: no exception, nothing sent
    check(until(lambda: ctx.connected, 10000), "connected")
    plan_ctl["accept"] = True
    _orig_exec = QDialog.exec

    def exec_and_unplug(self):
        if isinstance(self, fp.PlanDialog):
            ctx.disconnect("FM-1 getrennt")       # like a pulled cable during the dialog
            ctx.hold = True                       # the demo scan should not reconnect immediately
        return _orig_exec(self)
    QDialog.exec = exec_and_unplug
    log_boxes.clear()
    before, n_err = len(dev.sends), len(errors)
    try:
        ok = fp.install_flow(w, ctx, firmware.spec("melodee"), None, pkg, "v1.7.0")
    finally:
        QDialog.exec = _orig_exec
        ctx.hold = False
    check(not ok and len(errors) == n_err and bytes(fi.UPGRADE) not in dev.sends[before:]
          and any("Gerät hat sich geändert" in b or "getrennt" in b for b in log_boxes),
          f"device gone during the plan: aborted cleanly ({log_boxes[-1][:60] if log_boxes else '-'})")

    # ---- renaming: another device's name is refused (otherwise a shared backup folder)
    check(until(lambda: ctx.connected and ctx.port.serial == "DEMO-1", 10000), "connected")
    other = ctx.store.create("Zweites Gerät", serial="DEMO-9")
    try:
        ctx.rename_profile("zweites gerät")
        check(False, "renaming to a foreign name refused")
    except OSError as e:
        check("schon vergeben" in str(e) and ctx.profile.name != "zweites gerät", "renaming to a foreign name refused")
    ctx.rename_profile("Mein FM-1 ÄÖÜ – Studio  ")
    check(ctx.profile.name == "Mein FM-1 ÄÖÜ – Studio" and (Path(s.user_root) / "Backups" / ctx.profile.folder_name).exists(),
          f"umlauts/spaces in the name, folder moved along ({ctx.profile.folder_name})")
    ctx.store.delete(other.id)

    # ---- back to the original (V15, hash-checked, from the cache)
    v15 = Path("/tmp/fm1_open/sdk/firmware/FM-1_015.fwsc")
    if v15.exists():
        d = s.firmware_dir / "stock-v15" / "V15"
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy(v15, d / "FM-1_015.fwsc")
        # ---- target package broken: detected BEFORE the V15 intermediate step, nothing written
        check(until(lambda: ctx.connected, 10000), "connected again")
        bad = home / "dl" / "sloop-9.9.9.fwsc"
        bad.parent.mkdir(exist_ok=True)
        raw = bytearray(fpk.build_package("FM-1_900", seed=3))
        raw[-300] ^= 0xFF                                    # flash.bin/ota.bin checksum broken
        bad.write_bytes(bytes(raw))
        real_dl = firmware.download
        rel = firmware.Release(tag="v9.9.9", prerelease=False, assets={"sloop-9.9.9.fwsc": "https://example.invalid/x"})

        def fake_dl(sp, r, **k):
            if sp.key == "sloop":
                return firmware.Package(sp, r, bad, None, False)
            return real_dl(sp, r, **k)
        firmware.download = fake_dl
        before, ident0 = len(dev.sends), dev.identity
        try:
            ok = fp.install_flow(w, ctx, firmware.spec("sloop"), rel)
        finally:
            firmware.download = real_dl
        p5 = plan_ctl["plans"][-1]
        check(p5.via_stock and "no_checksum" in p5.confirmations, f"SLOOP without SHA256SUMS: confirmation "
              f"\"checksum not available\", V15 intermediate step ({p5.confirmations})")
        check(not ok and bytes(fi.UPGRADE) not in dev.sends[before:] and dev.identity == ident0
              and "wird nicht installiert" in plan_ctl["last_log"],
              f"broken target package detected before any writing – V15 not written ({dev.identity})")

        # ---- abort after the V15 step: the message says what was written
        check(until(lambda: ctx.connected, 10000), "connected again")
        good = home / "dl" / "sloop-9.9.8.fwsc"
        good.write_bytes(fpk.build_package("FM-1_900", seed=4))
        rel2 = firmware.Release(tag="v9.9.8", prerelease=False, assets={"sloop-9.9.8.fwsc": "https://example.invalid/y"})
        real_inst = firmware.install

        def inst_then_cancel(sp, *a, **k):
            r = real_inst(sp, *a, **k)
            if sp.key == "stock-v15" and k.get("cancel") is not None:
                k["cancel"].set()
            return r
        firmware.download = lambda sp, r, **k: firmware.Package(sp, r, good, None, False) if sp.key == "sloop" \
            else real_dl(sp, r, **k)
        firmware.install = inst_then_cancel
        try:
            ok = fp.install_flow(w, ctx, firmware.spec("sloop"), rel2)
        finally:
            firmware.download, firmware.install = real_dl, real_inst
        log = plan_ctl["last_log"]
        check(not ok and dev.identity == "FM-1_015" and "Original-Firmware V15" in log.split("FEHLER:")[-1]
              and "nichts geändert" not in log.split("FEHLER:")[-1],
              f"abort after V15 names what was written ({log.split('FEHLER:')[-1].strip()[:120]})")
        hist = journal.Journal().history()
        check(hist and "V15 V15" not in hist[-1].get("target", "") and hist[-1].get("target") ==
              "Original-Firmware V15", f"journal name without a doubled version ({hist[-1].get('target')})")
        # ---- the two-step switch stopped after V15: the journal remembers SLOOP, the page continues with it
        ch = journal.Journal().chain
        check(ch is not None and ch.get("key") == "sloop" and ch.get("tag") == "v9.9.8",
              f"journal: the second step is remembered ({ch})")
        check(dev.identity == "FM-1_015", f"the FM-1 runs V15 ({dev.identity})")
        check(until(lambda: ctx.connected, 10000), "connected again")
        w.go("firmware")
        page = w.firmware
        page._show_chain()
        check(page.chain_box.isHidden(), "“Continue” only while the FM-1 answers as V15")
        ctx.identity = "FM-1_015"                 # (the demo's MIDI side keeps answering as Melodee: as V15 would)
        page._show_chain()
        check(page.pending_chain() is not None and not page.chain_box.isHidden() and "SLOOP" in page.b_chain.text(),
              f"“Continue” offered ({page.b_chain.text()})")
        real_fr = firmware.fetch_releases
        firmware.fetch_releases = lambda sp, *a, **k: [rel2] if sp.key == "sloop" else real_fr(sp, *a, **k)
        firmware.download = lambda sp, r, **k: firmware.Package(sp, r, good, None, False) if sp.key == "sloop" \
            else real_dl(sp, r, **k)
        try:
            ok = page.continue_chain()
        finally:
            firmware.download, firmware.fetch_releases = real_dl, real_fr
        pc = plan_ctl["plans"][-1]
        check(ok and pc.chain_resume and pc.v15_option == "none" and "stock" not in pc.kinds()
              and dev.identity == "FM-1_900", f"resume continues with SLOOP directly ({pc.kinds()}, {dev.identity})")
        check(journal.Journal().chain is None, "chain done: cleared")
        page._show_chain()
        check(page.chain_box.isHidden(), "“Continue” gone")

        check(until(lambda: ctx.connected, 10000), "connected again")
        w.go("firmware")
        ok = w.firmware.return_to_original()
        check(ok and dev.identity == "FM-1_015" and not dev.bricked, f"back to V15 ({dev.identity})")
    else:
        check(True, "V15 file missing – return skipped")

    # ---- recovery: interrupted installation on DEMO-2
    page = w.page("recovery") if hasattr(w, "page") else None
    check(page is not None, "page Recovery registered")
    d2 = demo.device("demo2-in")
    pkg2 = home / "sloop-9.9.fwsc"
    pkg2.write_bytes(fpk.build_package("FM-1_901", seed=5))
    demo.register(pkg2.read_bytes())
    d2.power_loss_at = 0.4
    from fm1companion.flash import engine
    try:
        engine.Engine(log=lambda *_: None).write(fpk.analyze(pkg2), port="demo2-in", label="SLOOP 9.9")
        check(False, "power cut simulated")
    except engine.FlashError as e:
        check(e.state == engine.PAUSED, f"power cut → update mode ({e.code})")
    check(until(lambda: any(p.update_mode for p in ctx.ports), 8000), "update mode port appears live")
    w.go("recovery")
    page.refresh()
    check("wartet im Update-Modus" in page.status.text() and "SLOOP 9.9" in page.status.text(),
          "Recovery shows device and journal")
    # wrong package (original V15) for the Felucca loader: button off, the call refuses, nothing sent
    ctx.connect_port(next(p for p in ctx.ports if p.update_mode))      # as with only one device: the loader becomes active
    page.refresh()
    check(until(lambda: ctx.identity.startswith("ota-"), 8000) and not page.b_v15.isEnabled()
          and "Felucca-Loader" in page.b_v15.toolTip(), f"\"Original V15\" locked with a Felucca loader ({ctx.identity})")
    n_before = len(d2.sends)
    log_boxes.clear()
    check(not page.finish_install(stock=True) and len(d2.sends) == n_before
          and any("Passt nicht" in b or "nimmt die Original-V15 nicht an" in b for b in log_boxes),
          "V15 to a Felucca loader: refused, nothing sent")
    ok = page.finish_install()
    check(ok and d2.identity == "FM-1_901" and d2.flash == fpk.analyze(pkg2).image,
          f"recovery finished ({ok}, {d2.identity}, {d2.flash == fpk.analyze(pkg2).image}, {d2.log[-3:]})")
    check(journal.Journal().active is None, "journal empty again")

    # ---- level 2 in the simulation (mock FM-1 of MvaveFM1Unbricker)
    if v15.exists():
        page.chk_sim.setChecked(True)
        ok = page.uboot_restore()
        check(ok and list((Path(s.user_root) / "Rettung" / "backups").glob("*/full-flash-before.bin")),
              "level 2 (simulation): double backup, writing, reading back")
    w.go("overview")
    app.quit()


def run():
    try:
        flow()
    except Exception:  # noqa: BLE001
        errors.append(traceback.format_exc())
        app.quit()


QTimer.singleShot(500, run)
QTimer.singleShot(300000, app.quit)
app.exec()
fails = [r for r in results if not r[0]]
for e in errors:
    print("EXCEPTION:\n" + e)
print(f"{len(results) - len(fails)}/{len(results)} passed, {len(errors)} exceptions")
import shutil as _sh
_sh.rmtree(home, ignore_errors=True)
sys.exit(1 if fails or errors else 0)
