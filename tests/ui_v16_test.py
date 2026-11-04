# SPDX-License-Identifier: GPL-3.0-only
"""V16 and "Is my FM-1 clean?" (demo, offscreen).

Checked: quick check on DEMO-1/2/3 (resource status command 82), device switch during the check,
example dumps (clean / V16 leftovers / V16 truncated), wrong dump file, reading in ROM boot mode (simulation),
V16 installation Melodee → V15 → V16 (plan fixed via V15, "do not unplug now", no cancelling while writing,
honest completion message), way back from V16 (plan via V15), the Unbricker refuses V16, window sizes.
Real packages (V15/V16) via FM1_TEST_PKGDIR; without them the installation part is skipped with a message.

  flock /tmp/fm1c-ui.lock timeout 600 env QT_QPA_PLATFORM=offscreen FM1_TEST_PKGDIR=… python3 tests/ui_v16_test.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("FM1C_LANG", "de")      # these checks expect the German user interface
home = Path(tempfile.mkdtemp(prefix="fm1c-v16-"))
for k, sub in (("XDG_CONFIG_HOME", ".config"), ("XDG_DATA_HOME", ".local/share"), ("XDG_CACHE_HOME", ".cache")):
    os.environ[k] = str(home / sub)
os.environ["FM1C_DEMO"] = "1"
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu")
PKGDIR = Path(os.environ.get("FM1_TEST_PKGDIR", "/nonexistent"))

errors: list[str] = []
sys.excepthook = lambda t, v, tb: errors.append("".join(traceback.format_exception(t, v, tb)))
threading.excepthook = lambda a: errors.append("".join(traceback.format_exception(a.exc_type, a.exc_value,
                                                                                    a.exc_traceback)))

import PyQt6.QtWebEngineWidgets  # noqa: E402,F401
from PyQt6.QtCore import QCoreApplication, QEventLoop, Qt, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog, QFileDialog, QInputDialog, QMessageBox  # noqa: E402

QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
app = QApplication(sys.argv)

from fm1companion import config, editor_host, firmware  # noqa: E402

s = config.load()
s.user_root = str(home / "FM-1")
s.check_on_start = False
config.save(s)
config.ensure_dirs()

log_boxes: list[str] = []
reports: list = []
plans: list = []
plan_titles: list = []
ctl = {"accept_plan": True, "writing_seen": [], "cancel_during_write": None, "on_report": None, "tick": None,
       "shots": os.environ.get("FM1C_V16_SHOTS", ""), "shot_n": 0, "summaries": []}


def shot(widget, name):
    """screenshot for the visual check (only with FM1C_V16_SHOTS=<folder>)"""
    if ctl["shots"]:
        Path(ctl["shots"]).mkdir(parents=True, exist_ok=True)
        ctl["shot_n"] += 1
        widget.grab().save(str(Path(ctl["shots"]) / f"{ctl['shot_n']:02d}-{name}.png"))


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
QInputDialog.getText = staticmethod(lambda *a, **k: ("SCHREIBEN", True))


def dialog_exec(self):
    from fm1companion.ui.clean_check import ReportDialog
    from fm1companion.ui.common import JobDialog
    from fm1companion.ui.firmware_page import InstallDialog, PlanDialog
    if isinstance(self, ReportDialog):
        reports.append(self.rep)
        self.show()
        app.processEvents()
        if ctl["on_report"]:
            ctl["on_report"](self)
        self.hide()
        return 0
    if isinstance(self, PlanDialog):
        for cb in self.confirm_checks.values():
            cb.setChecked(True)
        plans.append(self.plan)
        from PyQt6.QtWidgets import QLabel
        plan_titles.append(" ".join(lb.text() for lb in self.findChildren(QLabel)[:3]))
        if ctl["shots"]:
            self.show()
            wait(300)
            shot(self, "plan")
            self.hide()
        return 1 if ctl["accept_plan"] and self.plan.ok and not self.missing() else 0
    if isinstance(self, JobDialog):
        self.show()
        t0 = time.time()
        while time.time() - t0 < 120:
            app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 30)
            if ctl["tick"]:
                ctl["tick"](self)
            if isinstance(self, InstallDialog) and self.writing:
                if not ctl["writing_seen"]:
                    shot(self, "schreiben")
                ctl["writing_seen"].append((self.danger.isVisible(), self.btn_cancel.isEnabled()))
                if ctl["cancel_during_write"] is None:
                    self._cancel()                 # cancelling in the middle of writing must have no effect
                    ctl["cancel_during_write"] = self.cancel_event.is_set()
            if self.buttons.button(self.buttons.StandardButton.Close).isEnabled():
                break
        if isinstance(self, InstallDialog):
            ctl["summaries"].append(self.summary or self.error)
            ctl.setdefault("heads", []).append(self.head.text())
            shot(self, "ergebnis")
        self.hide()
        self.log_text = self.logv.toPlainText()
        if not self.ok:
            print("---- log " + self.windowTitle() + ":\n" + self.log_text[-1500:], flush=True)
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
        wait(50)
    return False


def flow():
    from fm1companion.flash import flashmodel as fm
    ctx = w.ctx
    editor_host.prepare = lambda *a, **k: None
    check(until(lambda: ctx.connected and ctx.port.serial == "DEMO-1", 20000), "demo Melodee connected")
    w.go("recovery")
    page = w.page("recovery")
    card = page.clean
    check(card.isVisibleTo(w) and card.b_quick.isEnabled(), "Recovery: card \"Is my FM-1 clean?\" visible, ready")

    # ---- quick check DEMO-1 (Melodee rc.7): resources via command 82
    n = len(reports)
    card.quick()
    check(until(lambda: len(reports) > n), "quick check DEMO-1: the result appears")
    r = reports[-1]
    check(r.level == fm.GREEN and any("genau diesem Build" in t for _, t in r.findings),
          f"DEMO-1: green, resources match rc.7 ({r.level})")
    check(any("Reste" in t for t in r.not_covered), "DEMO-1: says honestly what is missing without a dump")

    # ---- DEMO-2 (SLOOP) and DEMO-3 (generic)
    ports = {p.serial: p for p in ctx.ports}
    ctx.connect_port(ports["DEMO-2"])
    check(until(lambda: ctx.connected and ctx.port.serial == "DEMO-2", 20000), "DEMO-2 connected")
    n = len(reports)
    card.quick()
    check(until(lambda: len(reports) > n, 10000), "quick check DEMO-2: the result appears")
    r = reports[-1]
    check(r.level == fm.GREEN and "SLOOP" in r.firmware and any("Befehl 82" in d for d in r.details),
          f"DEMO-2: SLOOP detected, no resource query ({r.firmware}, {r.details})")
    ctx.connect_port(ports["DEMO-3"])
    check(until(lambda: ctx.port.serial == "DEMO-3" and ctx.identity == "FM-1_900", 20000), "DEMO-3 connected")
    n = len(reports)
    card.quick()
    check(until(lambda: len(reports) > n, 5000) and reports[-1].firmware, "DEMO-3: identity checked (without editor)")

    # ---- device switch during the check
    ctx.connect_port(ports["DEMO-1"])
    check(until(lambda: ctx.connected and ctx.port.serial == "DEMO-1", 20000), "back on DEMO-1")
    n = len(reports)
    card.quick()
    ctx.connect_port(ports["DEMO-2"])
    check(until(lambda: len(reports) > n, 10000), "switch during the check: a result anyway")
    check(any("gewechselt" in t for _, t in reports[-1].findings) and not card._busy,
          "switch during the check: the report says which device it applies to")
    check(until(lambda: ctx.connected and ctx.port.serial == "DEMO-2", 20000), "DEMO-2 connected afterwards")

    # ---- dump check with example dumps
    check(card.demo_examples(), "example dumps created")
    folder = config.folder("Rettung") / "Beispiel-Dumps"
    want = {"demo-sauber.bin": fm.GREEN, "demo-v16-reste.bin": fm.YELLOW, "demo-v16-abgeschnitten.bin": fm.RED}
    for name, level in want.items():
        n = len(reports)
        card.dump_file(str(folder / name))
        check(until(lambda: len(reports) > n, 15000) and reports[-1].level == level,
              f"{name}: {fm.LEVEL_TEXT[level]} ({reports[-1].level if len(reports) > n else '–'})")
    check(reports[-2].residue == [(0x97000, 0x12000)], "leftover range named exactly (0x97000–0xA8FFF)")
    bad = home / "kaputt.bin"
    bad.write_bytes(b"\x00" * 4096)
    nb = len(log_boxes)
    card.dump_file(str(bad))
    check(until(lambda: len(log_boxes) > nb, 5000) and "1 MiB" in log_boxes[-1], "wrong dump size: clear message")
    # file selection cancelled: quiet, no error
    QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: ("", ""))
    check(card.dump_file() is False, "file selection cancelled: nothing happens")

    # report: window at 950 px and copying
    seen = {}

    def on_report(dlg):
        dlg.resize(560, 480)
        app.processEvents()
        dlg.copy()
        seen["text"] = QApplication.clipboard().text()
    ctl["on_report"] = on_report
    n = len(reports)
    card.dump_file(str(folder / "demo-v16-reste.bin"))
    until(lambda: len(reports) > n, 15000)
    ctl["on_report"] = None
    check("Mit Hinweisen" in seen.get("text", "") and "Nicht abgedeckt" in seen.get("text", ""),
          "copy report: traffic light, sentences and \"not covered\" as text")

    # ---- ROM boot mode (simulation of the Unbricker)
    page.chk_sim.setChecked(True)
    n = len(reports)
    ok = card.rom_dump()
    check(ok and len(reports) > n and any("Simulation" in t for _, t in reports[-1].findings),
          "ROM boot mode (simulation): read, checked, marked as simulation")

    # ---- expert mode: clean up V16 leftovers (simulation with the Unbricker's mock FM-1)
    check(not card.b_clean.isVisibleTo(card), "clean up leftovers: no button without expert mode")
    card.chk_expert.setChecked(True)
    check(card.b_clean.isVisibleTo(card) and config.module("rettung")["experte"] is True,
          "expert mode: button visible, setting saved")
    QInputDialog.getText = staticmethod(lambda *a, **k: ("falsch", True))
    n = len(reports)
    card.clean_residue()
    plan_rep = reports[n] if len(reports) > n else None
    check(plan_rep is not None and plan_rep.level == "gelb" and plan_rep.residue == [(0x97000, 0x12000)]
          and any("ungetestet" in t for t in plan_rep.not_covered) and len(reports) == n + 1
          and "nichts gelöscht" in card.status.text(),
          "clean up leftovers: plan 0x97000–0xA8FFF, \"untested\"; a wrong confirmation word erases nothing")
    QInputDialog.getText = staticmethod(lambda *a, **k: ("AUFRÄUMEN", True))
    n = len(reports)
    ok = card.clean_residue()
    final = reports[-1] if len(reports) >= n + 2 else None
    check(ok and final is not None and final.level == "gelb" and any("18 Sektoren mit V16-Resten gelöscht" in t
                                                                      for _, t in final.findings)
          and any("Simulation" in t for _, t in final.findings),
          "clean up leftovers (simulation): 18 sectors erased and checked, marked as simulation")
    n = len(reports)
    card.clean_residue()
    check(len(reports) == n + 1 and any("keine V16-Reste" in t for _, t in reports[-1].findings),
          "clean up leftovers: a second run finds nothing more")
    QInputDialog.getText = staticmethod(lambda *a, **k: ("SCHREIBEN", True))
    card.chk_expert.setChecked(False)

    # ---- the Unbricker never writes V16 (TOOL_TRUNCATES)
    from fm1companion.flash import uboot
    if (PKGDIR / "v16.fwsc").exists():
        try:
            uboot.Runner(dry_run=True, log=lambda *_: None).restore(str(PKGDIR / "v16.fwsc"))
            refused = False
        except RuntimeError as e:
            refused = "abgeschnitten" in str(e)
        check(refused, "Unbricker route: V16 is refused (would be truncated)")

    # ---- install V16: DEMO-1 Melodee -> V15 -> V16
    if not ((PKGDIR / "v16.fwsc").exists() and (PKGDIR / "zip_v15.fwsc").exists()):
        print("NOTE: V16 installation skipped – FM1_TEST_PKGDIR with zip_v15.fwsc and v16.fwsc missing", flush=True)
    else:
        from fm1companion.flash import demo
        from fm1companion.ui import firmware_page as fp
        files = {"stock-v15": PKGDIR / "zip_v15.fwsc", "stock-v16": PKGDIR / "v16.fwsc"}
        real_dl = firmware.download
        firmware.download = lambda sp, rel, **k: firmware.Package(sp, rel, files[sp.key], None, True) \
            if sp.key in files else real_dl(sp, rel, **k)
        ctx.connect_port(ports["DEMO-1"])
        check(until(lambda: ctx.connected and ctx.port.serial == "DEMO-1", 20000), "DEMO-1 connected for V16")
        w.go("firmware")
        sp16 = firmware.spec("stock-v16")
        rel16 = firmware.fetch_releases(sp16)[0]
        # device switch while the plan is open: nothing is written
        orig = fp.PlanDialog.exec

        def plan_and_switch(self):
            ctx.connect_port(ports["DEMO-2"])
            until(lambda: ctx.port.serial == "DEMO-2", 5000)
            return dialog_exec(self)
        fp.PlanDialog.exec = plan_and_switch
        nb = len(log_boxes)
        ok = fp.install_flow(w, ctx, sp16, rel16)
        fp.PlanDialog.exec = orig
        check(not ok and any("Gerät hat sich geändert" in b or "getrennt" in b for b in log_boxes[nb:])
              and demo.device("demo-in").identity == "FM-1_91060", "device switch in the plan: nothing written")
        ctx.connect_port(ports["DEMO-1"])
        check(until(lambda: ctx.connected and ctx.port.serial == "DEMO-1", 20000), "DEMO-1 connected again")
        ok = fp.install_flow(w, ctx, sp16, rel16)
        p16 = plans[-1]
        check(not any("V16 V16" in t for t in plan_titles), "plan title without a doubled \"V16\"")
        check(p16.v15_option == "locked" and p16.kinds()[:3] == ["backup", "fetch", "stock"],
              f"plan Melodee → V16: fixed via V15, backup first ({p16.kinds()})")
        check(any("0x93000–0xA8FFF" in x for x in p16.lost) and "v16" in p16.confirmations,
              "the plan names what gets lost and requires the V16 confirmation")
        dev = demo.device("demo-in")
        check(ok and dev.identity == "FM-1_016", f"V16 installed (simulation: {dev.identity}, {dev.log[-3:]})")
        check(ctl["writing_seen"] and all(v and not c for v, c in ctl["writing_seen"]),
              "while writing: \"do not unplug now\" visible, cancel locked")
        check(ctl["cancel_during_write"] is False, "cancelling in the middle of writing has no effect")
        bks = list((Path(s.user_root) / "Backups").rglob("*.json"))
        check(bks, "backup created before V16")
        # way back: the device reports V16 -> hint on the firmware page, plan via V15
        page_fw = w.page("firmware")
        ctx.identity = "FM-1_016"
        ctx.changed.emit()
        check(page_fw.v16_back.isVisibleTo(page_fw), "firmware page: way-back hint when V16 is running")
        from fm1companion.flash import planner
        st = fp.device_state(ctx)
        st.felucca, st.firmware_key = False, "stock-v16"
        back = planner.plan_install(st, planner.Target.from_spec(firmware.spec("melodee"), tag="v1.6.0-rc.7"))
        check(back.v15_option == "locked" and "stock" in back.kinds() and any("Reste von V16" in x for x in back.lost),
              "way back V16 → Melodee: fixed via V15, leftovers are announced")
        # ---- really run the way back: V16 -> V15 -> (abort) -> Melodee rc.7 with resources
        rc7 = Path(os.environ.get("FM1_TEST_RC7", "/tmp/ws-v16/pk/melodee-rc7.fwsc"))
        if rc7.is_file():
            res7 = rc7.with_name(rc7.stem + "-res.bin")
            spm = firmware.spec("melodee")
            rel7 = firmware.Release("v1.6.0-rc.7", True, assets={"SHA256SUMS": "lokal"})
            files["melodee"] = rc7
            firmware.download = lambda sp, rel, **k: firmware.Package(sp, rel, files[sp.key], res7 if sp.key == "melodee"
                                                                      and res7.is_file() else None, True) \
                if sp.key in files else real_dl(sp, rel, **k)
            wrote_res = []
            real_tools, real_wr, real_prep = firmware.ensure_tools, firmware.write_resources, editor_host.prepare
            firmware.ensure_tools = lambda *a, **k: Path(".")
            firmware.write_resources = lambda s_, repo, res, port="", log=print, **k: (wrote_res.append(port),
                                                                                      log("Ressourcen geschrieben"))
            editor_host.prepare = lambda *a, **k: None
            page_fw.releases["melodee"] = [rel7]
            real_state = fp.device_state

            def as_v16(c):          # this is how a real V16 reports: identity FM-1_016, no editor protocol
                st_ = real_state(c)
                st_.identity, st_.felucca, st_.firmware_key = dev.identity, False, ""
                return st_
            fp.device_state = as_v16

            def abort_after_v15(d):
                if "Original-V15 läuft" in d.logv.toPlainText() and not d.cancel_event.is_set():
                    d._cancel()
            ctl["tick"] = abort_after_v15
            ctx.connect_port(ports["DEMO-1"])
            until(lambda: ctx.connected and ctx.port.serial == "DEMO-1", 20000)
            ok = page_fw.return_from_v16("melodee", "v1.6.0-rc.7")
            ctl["tick"] = None
            pb = plans[-1]
            check(pb.v15_option == "locked" and pb.kinds()[:2] == ["fetch", "stock"] and "backup" not in pb.kinds(),
                  f"way-back plan: without backup (V16 has none), fixed via V15 ({pb.kinds()})")
            check(not ok and dev.identity == "FM-1_015", f"abort after V15: V15 runs ({dev.identity})")
            check(any("NICHT geschrieben" in x and "V15" in x for x in ctl["summaries"][-1:]),
                  "abort after V15: the message says what was written and what was not")
            check(ctl["heads"][-1].startswith("Abgebrochen"), f"abort is called \"Abgebrochen\" (cancelled) ({ctl['heads'][-1]})")
            until(lambda: ctx.connected and ctx.port is not None and ctx.port.serial == "DEMO-1", 20000)
            ok = page_fw.return_from_v16("melodee", "v1.6.0-rc.7")
            pm = plans[-1]
            check(pm.v15_option == "none" and "stock" not in pm.kinds() and "resources" in pm.kinds(),
                  f"resuming from V15: directly Melodee, with resources ({pm.kinds()})")
            check(ok and dev.identity == "FM-1_91060" and wrote_res, f"Melodee rc.7 + resources ({dev.identity})")
            check(any("Ressourcen geschrieben" in x for x in ctl["summaries"][-1:]),
                  "the completion message names the resources: " + (ctl["summaries"][-1] if ctl["summaries"] else "–"))
            fp.device_state = real_state
            firmware.ensure_tools, firmware.write_resources, editor_host.prepare = real_tools, real_wr, real_prep
        else:
            print("NOTE: running the way back skipped – Melodee rc.7 missing (FM1_TEST_RC7)", flush=True)
        firmware.download = real_dl
        ctx.connect_port(ports["DEMO-2"])
        check(until(lambda: ctx.connected and ctx.port.serial == "DEMO-2", 20000) and not page_fw.v16_back.isVisibleTo(
            page_fw), "another device: the way-back hint disappears")

    # ---- window sizes: Recovery without horizontal scrolling
    w.go("recovery")
    for size in ((950, 1000), (1280, 720), (1920, 1080)):
        w.resize(*size)
        wait(300)
        sb = w.page_scroll.horizontalScrollBar() if hasattr(w, "page_scroll") else None
        check(sb is None or not sb.isVisible() or sb.maximum() == 0, f"Recovery at {size[0]}×{size[1]}: fits the width")
        cc = w.page("recovery").clean
        btns = [cc.b_quick, cc.b_file, cc.b_rom] + ([cc.b_demo] if hasattr(cc, "b_demo") else [])
        check(all(b.y() + b.height() <= b.parentWidget().height() for b in btns),
              f"\"Is my FM-1 clean?\" at {size[0]}×{size[1]}: no button row clipped")
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
import shutil as _sh  # noqa: E402
_sh.rmtree(home, ignore_errors=True)
sys.exit(1 if fails or errors else 0)
