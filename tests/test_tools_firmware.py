# SPDX-License-Identifier: GPL-3.0-only
"""Step 4, item 1: a Felucca-family firmware onto a unit with m-bootloader through USB download mode – `m-flasher
firmware PACKAGE.fwsc` (fm1companion/tools/flashjob.py install_firmware, firmware_gate) and the planner's route for
it (flash/planner.py) – against tests/fake_mflasher.py with synthetic unit ids, a pseudo-random synthetic flash with
m-bootloader composed in and a synthetic Felucca-family package.

As everywhere in the M-Flasher tests: every refusal proves via nothing_written() that the fake's flash is unchanged,
no "yes" was sent and no range was written."""
from __future__ import annotations

import json
import random
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fm1companion import hardware  # noqa: E402
from fm1companion.flash import journal as fj, package as pk, planner  # noqa: E402
from fm1companion.tools import bootarea, flashjob as FJ, mflasher_parse as mp, runner  # noqa: E402
from test_tools_flash import ARCH, UID, calls, env, nothing_written, sha  # noqa: E402,F401

BOOT = (ROOT / "fm1companion" / "tools" / "bundled" / "m-bootloader" / "fm1-uboot.boot").read_bytes()
MBOOT_LINE = "m-bootloader 2.4-395e6cc (fm1)"
END = 0x60000


def package(ident="FM-1_727", seed="synthetic-skiff", end=END) -> bytes:
    """a synthetic Felucca-family package: flash.bin of `end` bytes (its own boot area – which M-Flasher never writes
    on this route – and a pseudo-random firmware area)"""
    flash = random.Random(seed).randbytes(end)
    return pk.build_package(ident, family="felucca", app=flash)


def pkg_flash(raw: bytes) -> bytes:
    return bootarea.package_flash(raw)[1]


@pytest.fixture(autouse=True)
def _mboot_unit(request):
    """the fake's unit has m-bootloader 2.4 in its boot area (composed as M-Flasher composes it) and M-Flasher
    reports it"""
    if "env" in request.fixturenames:
        import fake_mflasher
        e = request.getfixturevalue("env")
        img = bytearray(fake_mflasher.synthetic_image())
        img[:0x4000] = fake_mflasher.short_boot_area(bytes(img), BOOT)
        Path(str(e.state) + ".flash").write_bytes(bytes(img))
        e.set(mode="midi", bootloader=MBOOT_LINE, firmware="SLOOP 2.8")
    yield
    FJ.WRITING.clear()


def run(env, raw=None, plans=None, data=None, *, confirm=True, confirm_data=True, **kw):
    plans = plans if plans is not None else []
    data = data if data is not None else []

    def ok(plan):
        plans.append(plan)
        return confirm

    def ok_data(plan, text):
        data.append(text)
        return confirm_data
    new_chip_ok = kw.pop("new_chip_ok", False)
    return FJ.install_firmware(env.job(**kw), raw if raw is not None else package(), ok, ok_data, name="Skiff 2.7",
                               new_chip_ok=new_chip_ok)


# ------------------------------------------------------------------------------------------------ the source facts

def test_m_flashers_firmware_plan_texts_are_the_ones_of_its_source():
    """ops.rs plan_firmware at v0.12.1: the title, the note of the allowed range and the data-places warning"""
    assert mp.firmware_title("FM-1_727") == "Install firmware FM-1_727"
    assert mp.firmware_note("FM-1_727", 0x78000) == "Installs FM-1_727 (0x04000-0x77FFF)."
    w = mp.data_places_warning("SLOOP 2.8", "FM-1_727")
    assert w.startswith("The installed firmware (SLOOP 2.8) keeps its data in other places than FM-1_727; what it "
                        "leaves behind can confuse the new firmware (wrong presets, no sound).")
    assert w.endswith("To go back to a firmware cleanly, restore a backup made while it was installed.")


def test_the_target_keeps_the_boot_area_and_everything_after_the_package():
    pre = bytes(random.Random("pre").randbytes(FJ.FLASH_SIZE))
    flash = pkg_flash(package())
    t = FJ.firmware_target(pre, flash)
    assert len(t) == FJ.FLASH_SIZE and t[:0x4000] == pre[:0x4000] and t[END:] == pre[END:]
    assert t[0x4000:END] == flash[0x4000:]
    assert all(a >= 0x4000 and b <= END for a, b in FJ.diff_runs(t, pre))


# ------------------------------------------------------------------------------------------------ runner forms

def test_the_firmware_form_is_known_but_only_with_a_gate(env):
    args = ["firmware", "/x/p.fwsc", "--device", "/dev/sg1", "--backup-dir", "/x"]
    runner._check_args(args)
    with pytest.raises(runner.ToolError):
        runner.run_mflasher(args, store=env.store, journal=env.journal, log_dir=env.logs)     # (no gate)
    assert not calls(env, "firmware")


@pytest.mark.parametrize("args", [
    ["firmware", "/x/p.fwsc"], ["firmware", "/x/p.bin", "--device", "/dev/sg1", "--backup-dir", "/x"],
    ["firmware", "/x/p.fwsc", "--device", "/dev/sg1", "--backup-dir", "/x", "--yes"],
    ["firmware", "/x/p.fwsc", "--device", "/dev/sg1", "--backup-dir", "/x", "--restart"],
    ["firmware", "p.fwsc", "--device", "/dev/sg1", "--backup-dir", "/x"],
    ["firmware", "/x/../p.fwsc", "--device", "/dev/sg1", "--backup-dir", "/x"],
    ["firmware", "/x/p.fwsc", "--device", "/dev/sda", "--backup-dir", "/x"]])
def test_other_firmware_forms_are_refused(args):
    with pytest.raises(runner.ToolError):
        runner._check_args(args)


# ------------------------------------------------------------------------------------------------ the install

def test_install_writes_only_from_0x4000_to_the_package_end_after_backup_gate_and_verify(env):
    before = env.flash()
    raw = package()
    plans = []
    res = run(env, raw, plans)
    after = env.flash()
    assert res.written and res.verified and res.restarted and res.identity == "FM-1_727" and not res.data_warning
    assert [c[0] for c in env.get()["calls"]] == ["find", "download-mode", "info", "backup", "backup", "firmware",
                                                  "backup", "restart"]
    s = env.get()
    assert s["answers"] == ["yes\n"] and not s.get("yes_flag")
    assert s["written"] and all(a >= 0x4000 and b <= END for a, b in s["written"])
    assert after == FJ.firmware_target(before, pkg_flash(raw))
    assert after[:0x4000] == before[:0x4000] and bootarea.stored_mbootloader(after[:0x4000])     # (m-bootloader stays)
    argv = calls(env, "firmware")[0]
    assert argv[1].endswith("/package.fwsc") and Path(argv[1]).parent.name.startswith("write-")
    assert "--yes" not in argv and "--restart" not in argv
    p = plans[0]
    assert p.identity == "FM-1_727" and p.name == "Skiff 2.7" and p.end == END and p.revision == "old"
    assert p.runs == FJ.diff_runs(FJ.firmware_target(before, pkg_flash(raw)), before) and p.backup == res.backup
    assert p.current == MBOOT_LINE and p.backup.is_file()
    rec = json.loads(FJ.record_file(env.journal.dir).read_text())
    assert rec["state"] == "verified" and rec["op"] == "firmware" and UID not in json.dumps(rec)
    log = next(env.logs.glob("mflasher-firmware-*.txt")).read_text()
    assert "answered yes" in log and "the boot area stays" in log and UID not in log
    # (a second click: it is there already – nothing is written)
    n = len(env.get()["written"])
    res2 = run(env, raw)
    assert not res2.written and res2.note == "already" and len(env.get()["written"]) == n


@pytest.mark.parametrize("fault", ["plan-outside", "plan-extra", "warning", "model-warning", "note", "stray",
                                   "other-unit", "other-chip"])
def test_the_gate_closes_stdin_on_any_mismatch_nothing_is_written(env, fault):
    before = env.flash()
    env.set(mode="midi", bootloader=MBOOT_LINE, fault_firmware=fault)
    data = []
    with pytest.raises(FJ.Refused) as e:
        run(env, data=data)
    assert e.value.kind == "gate" and not e.value.written and not data
    nothing_written(env, before)
    assert len(env.get()["answers"]) == 1                              # (it asked; the app closed stdin)
    assert not FJ.record_file(env.journal.dir).exists()


def _out(runs, *, ident="FM-1_727", end=END, uid=UID, flash="0x856014", key="0x980F", warn="", note=None,
         title=None, fw="SLOOP 2.8"):
    n = sum((b - a) // 0x1000 for a, b in runs)
    rng = ", ".join(f"0x{a:05X}-0x{b - 1:05X}" for a, b in runs)
    note = note if note is not None else mp.firmware_note(ident, end)
    return (f"Connecting to /dev/sg1\n  model       FM-1\n  bootloader  {MBOOT_LINE}\n  firmware    {fw}\n"
            f"  app         OK\n  chip key    {key}\n  flash       {flash}\n  unit id     {uid}\n"
            f"  loader      M-Flasher 1.3\n    - evidence\n\n{title or mp.firmware_title(ident)}\n"
            f"Writes {n} sectors ({n * 4} KiB): {rng}\n{note}\n" + (f"WARNING: {warn}\n" if warn else "") + mp.PROMPT)


def test_firmware_gate_answers_only_on_an_exact_match():
    runs = [(0x4000, 0x8000), (0x10000, 0x11000)]
    exp = {"unit_id": UID, "flash_id": 0x856014, "chip_key": 0x980F, "identity": "FM-1_727", "end": END, "runs": runs}
    g = FJ.firmware_gate(dict(exp))
    assert g(_out(runs)) == ""
    warn = mp.data_places_warning("SLOOP 2.8", "FM-1_727")
    for bad in (_out(runs, uid="2468ace013579bdf"), _out(runs, flash="0xB36014"), _out(runs, key="0x1234"),
                _out(runs + [(0x20000, 0x21000)]), _out([(0x3000, 0x4000)] + runs),
                _out([(0x4000, 0x8000), (0x5F000, 0x61000)]), _out(runs, ident="FM-1_900"),
                _out(runs, note="Installs FM-1_727 (0x04000-0x92FFF)."), _out(runs, title="Restore a backup"),
                _out(runs, warn="The unit runs MiniFM firmware; this package is for the FM-1."),
                _out(runs, warn=mp.data_places_warning("Other", "FM-1_727"))):
        assert FJ.firmware_gate(dict(exp))(bad) != "", bad[-300:]
    # the data-places warning: noted and refused once, then let through only as that very text
    e = dict(exp)
    assert FJ.firmware_gate(e)(_out(runs, warn=warn)) == FJ.DATA_WARNING and e["seen_warning"] == warn
    assert FJ.firmware_gate(dict(exp, accept=warn))(_out(runs, warn=warn)) == ""
    assert FJ.firmware_gate(dict(exp, accept=warn))(_out(runs, warn=warn, fw="Other")) != ""
    assert FJ.firmware_gate(dict(exp, accept=warn))(_out(runs)) == ""


def test_the_data_places_warning_needs_its_own_confirmation_declined_writes_nothing(env):
    before = env.flash()
    env.set(mode="midi", bootloader=MBOOT_LINE, firmware="SLOOP 2.8", data_warning=True)
    data = []
    with pytest.raises(FJ.Refused) as e:
        run(env, data=data, confirm_data=False)
    assert e.value.kind == "declined-data" and not e.value.written
    assert data == [mp.data_places_warning("SLOOP 2.8", "FM-1_727")]
    nothing_written(env, before)
    assert len(calls(env, "firmware")) == 1 and env.get()["answers"] == [""]      # (stdin closed at its question)


def test_the_data_places_warning_confirmed_then_written_in_a_second_session(env):
    before = env.flash()
    env.set(mode="midi", bootloader=MBOOT_LINE, firmware="SLOOP 2.8", data_warning=True)
    raw = package()
    data, plans = [], []
    res = run(env, raw, plans, data)
    assert res.written and res.verified and res.data_warning == data[0]
    assert len(calls(env, "firmware")) == 2 and env.get()["answers"] == ["", "yes\n"]
    assert env.flash() == FJ.firmware_target(before, pkg_flash(raw))
    assert len(plans) == 1                                             # (the danger dialog once, the warning once)


def test_a_changed_data_places_warning_in_the_second_session_is_refused(env):
    before = env.flash()
    env.set(mode="midi", bootloader=MBOOT_LINE, firmware="SLOOP 2.8", data_warning=True,
            fault_firmware="data-warning-changed")
    with pytest.raises(FJ.Refused) as e:
        run(env)
    assert e.value.kind == "gate"
    nothing_written(env, before)


def test_only_on_a_unit_with_m_bootloader(env):
    import fake_mflasher
    Path(str(env.state) + ".flash").write_bytes(fake_mflasher.synthetic_image())
    before = env.flash()
    env.set(mode="midi", bootloader="stock (M-VAVE)")
    with pytest.raises(FJ.Refused) as e:
        run(env)
    assert e.value.kind == "not-mboot" and not calls(env, "backup") and not calls(env, "firmware")
    nothing_written(env, before)
    assert calls(env, "restart")                                       # (the app had sent it there: started again)


def test_the_backup_must_show_m_bootloader_too(env):
    import fake_mflasher
    Path(str(env.state) + ".flash").write_bytes(fake_mflasher.synthetic_image())     # (stock boot area …)
    before = env.flash()
    env.set(mode="midi", bootloader=MBOOT_LINE)                                       # (… but M-Flasher says m-bootloader)
    with pytest.raises(FJ.Refused) as e:
        run(env)
    assert e.value.kind == "not-mboot" and not calls(env, "firmware")
    nothing_written(env, before)


def test_newer_unit_needs_the_hardware_rule(env):
    before = env.flash()
    hardware.note_flash_id(env.prof, 0xB36014, "rom")
    assert hardware.revision(env.prof) == hardware.NEW
    with pytest.raises(FJ.Refused) as e:
        run(env)
    assert e.value.kind == "newer-chip" and not env.get().get("calls")            # (before download mode)
    nothing_written(env, before)
    # the profile does not know it, the session reads the newer chip: refused before the backup
    env.prof = env.profiles.profiles[env.prof.id] = type(env.prof)(id="aaaaaaaaaa", name="Unit 1")
    env.set(mode="midi", bootloader=MBOOT_LINE, flash=0xB36014)
    with pytest.raises(FJ.Refused) as e:
        run(env)
    assert e.value.kind == "newer-chip" and not calls(env, "backup") and not calls(env, "firmware")
    nothing_written(env, before)
    # the hardware rule lets the target through: written
    env.set(mode="midi", bootloader=MBOOT_LINE, flash=0xB36014)
    res = run(env, new_chip_ok=True)
    assert res.written and res.verified


@pytest.mark.parametrize("ident", ["SMK-37 Pro_101", "MiniFM_012"])
def test_a_package_for_another_model_is_refused_before_anything(env, ident):
    before = env.flash()
    with pytest.raises(FJ.Refused) as e:
        run(env, package(ident))
    assert e.value.kind == "package-model" and not env.get().get("calls")
    nothing_written(env, before)


def test_not_a_package_is_refused_before_anything(env):
    with pytest.raises(FJ.Refused) as e:
        run(env, b"\0" * 0x2000)
    assert e.value.kind == "package" and not env.get().get("calls")


def test_declined_or_cancelled_writes_nothing(env):
    before = env.flash()
    with pytest.raises(FJ.Refused) as e:
        run(env, confirm=False)
    assert e.value.kind == "declined" and not calls(env, "firmware")
    nothing_written(env, before)


def test_needs_a_write_verified_m_flasher(env, monkeypatch):
    before = env.flash()
    monkeypatch.setitem(env.store.data["tools"]["m-flasher"]["write_verified"], "0.12.1", {})
    with pytest.raises(FJ.Refused) as e:
        run(env)
    assert e.value.kind == "not-write-verified" and not env.get().get("calls")
    nothing_written(env, before)


def test_unknown_unit_is_refused(env):
    before = env.flash()
    with pytest.raises(FJ.Refused) as e:
        run(env, profile=None, softkey_for="")
    assert e.value.kind in ("unknown-unit", "guide")
    nothing_written(env, before)


@pytest.mark.parametrize("fault,kind,written", [("write-fail", "write-failed", True),
                                                ("no-done", "write-failed", True),
                                                ("skip-sector", "verify-mismatch", True),
                                                ("backup-fails", "not-started", False)])
def test_a_failed_write_is_reported_with_the_backup(env, fault, kind, written):
    env.set(mode="midi", bootloader=MBOOT_LINE, fault_firmware=fault)
    with pytest.raises(FJ.Refused) as e:
        run(env)
    assert e.value.kind == kind and e.value.written == written
    rec = FJ.unfinished_write(env.journal.dir)
    if written:
        assert isinstance(e.value, FJ.WriteFailed) and e.value.backup.is_file()
        assert rec is not None and rec["op"] == "firmware" and rec["state"] == "failed"
        assert not calls(env, "restart")[1:]                          # (it stays in download mode)
    else:
        assert rec is None


CHILD = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
sys.path.insert(0, sys.argv[1] + "/tests")
from pathlib import Path
from fm1companion import devices
from fm1companion.flash import journal as fj
from fm1companion.tools import flashjob as FJ, store as st
a = json.loads(Path(sys.argv[2]).read_text())
store = st.Store(bin_dir=a["bin"], updates_dir=a["upd"], data=a["data"], machine=a["arch"])
profiles = devices.ProfileStore(Path(a["profiles"]))
job = FJ.Job(backups_dir=Path(a["backups"]), profiles=profiles, profile=profiles.profiles[a["prof"]], store=store,
             log_dir=Path(a["logs"]), journal=fj.Journal(Path(a["journal"])), softkey_for="FM-1 MIDI 1",
             work_dir=Path(a["work"]))
FJ.install_firmware(job, Path(a["pkg"]).read_bytes(), lambda plan: True, lambda plan, text: True)
'''


def test_kill_after_yes_m_flasher_writes_the_firmware_to_its_end(env):
    """the app is killed (SIGKILL) right after "yes": M-Flasher writes to its end on its own; the record (op
    firmware) is on disk and the next start finds it"""
    before = env.flash()
    raw = package()
    (env.tmp / "pkg.fwsc").write_bytes(raw)
    env.profiles.save()
    env.set(mode="midi", bootloader=MBOOT_LINE, fault_firmware="slow-write")
    args = env.tmp / "child.json"
    args.write_text(json.dumps({"bin": str(env.tmp / "tools-bin"), "upd": str(env.tmp / "upd"), "data": env.data,
                                "arch": ARCH, "profiles": str(env.tmp / "devices.json"), "prof": env.prof.id,
                                "backups": str(env.backups), "logs": str(env.logs), "journal": str(env.journal.dir),
                                "work": str(env.tmp / "work"), "pkg": str(env.tmp / "pkg.fwsc")}))
    child = subprocess.Popen([sys.executable, "-c", CHILD, str(ROOT), str(args)])
    rec_path = FJ.record_file(env.journal.dir)
    t0 = time.monotonic()
    while time.monotonic() - t0 < 60 and not (rec_path.exists() and json.loads(rec_path.read_text()).get("pid")):
        time.sleep(0.05)
    rec = json.loads(rec_path.read_text())
    assert rec["state"] == "writing" and rec["op"] == "firmware" and rec["identity"] == "FM-1_727"
    time.sleep(0.3)
    child.kill()
    child.wait(10)
    t0 = time.monotonic()
    while time.monotonic() - t0 < 60 and fj._alive(rec["pid"]):
        time.sleep(0.1)
    after = env.flash()
    assert after == FJ.firmware_target(before, pkg_flash(raw))          # (written to its end)
    left = FJ.unfinished_write(env.journal.dir)
    assert left is not None and left["done"] and not left["alive"] and Path(left["pre_backup"]).read_bytes() == before


def test_writing_flag_is_set_only_after_yes(env):
    seen = []
    job = env.job(on_writing=lambda rec: seen.append(FJ.WRITING.is_set()))
    FJ.install_firmware(job, package(), lambda plan: not FJ.WRITING.is_set(), lambda p, t: True)
    assert seen == [True] and not FJ.WRITING.is_set()


def test_the_interrupted_question_offers_only_the_state_before_for_a_firmware_record():
    from fm1companion.ui import flash_tools
    text = flash_tools.interrupted_text({"op": "firmware", "device": "Unit 1", "unit": "…0ace", "started": 0,
                                         "pre_backup": "/x/Unit 1_flash.bin", "identity": "FM-1_727"})
    assert "FM-1_727" in text and "Unit 1_flash.bin" in text


# ------------------------------------------------------------------------------------------------ the planner

def _state(**kw):
    kw.setdefault("identity", "FM-1_91061")
    kw.setdefault("felucca", True)
    kw.setdefault("firmware_key", "melodee")
    kw.setdefault("boot_area", bootarea.MBOOT)
    return planner.DeviceState(**kw)


def _target(key="skiff", family="felucca", name="Skiff"):
    return planner.Target(name=name, product="FM-1_727", family=family, key=key)


def test_m_bootloader_and_a_felucca_switch_get_the_download_route_instead_of_the_block():
    p = planner.plan_install(_state(), _target())
    assert p.ok and p.route == "download-mode" and not p.boot_restore and not p.via_stock
    assert p.kinds() == ["backup", "flashbackup", "fetch", "install"]
    assert "stock" not in p.kinds()
    assert any("0x04000" in w and "m-bootloader" in w for w in p.warnings)


def test_the_running_sloop3_gets_the_download_route():
    st = _state(identity="", felucca=False, firmware_key="sloop3")
    p = planner.plan_install(st, _target())
    assert p.ok and p.route == "download-mode" and p.kinds() == ["flashbackup", "fetch", "install"]
    # the original V15 (or any target outside the Felucca family) cannot go this way: blocked, with the way
    p = planner.plan_install(st, planner.Target(name="Original V15", product="FM-1_015", family="stock",
                                                key="stock-v15", sha256=pk.STOCK_V15_SHA256))
    assert not p.ok and p.route == "" and any("SLOOP 3" in b for b in p.blockers)


def test_the_route_keeps_blocking_where_it_cannot_apply():
    # FM1 Open's or another boot area: the owner's rule stays (the original boot area first)
    for kind in (bootarea.OPEN, bootarea.OTHER):
        p = planner.plan_install(_state(boot_area=kind), _target())
        assert not p.ok and p.boot_restore and p.route == ""
    # the original V15 from a running Felucca firmware on m-bootloader: still the trap
    p = planner.plan_install(_state(), planner.Target(name="Original V15", product="FM-1_015", family="stock",
                                                      key="stock-v15", sha256=pk.STOCK_V15_SHA256))
    assert not p.ok and p.boot_restore and p.route == ""
    # an update of the same firmware on m-bootloader: the usual route (a warning), as before
    p = planner.plan_install(_state(identity="FM-1_727", firmware_key="skiff"), _target())
    assert p.route == "" and not p.boot_restore
    # an unknown or the original boot area: nothing changes
    for kind in ("", bootarea.ORIGINAL):
        assert planner.plan_install(_state(boot_area=kind), _target()).route == ""


def test_the_route_keeps_the_newer_unit_rule():
    p = planner.plan_install(_state(revision="new"), planner.Target(name="Old", product="FM-1_727", family="felucca",
                                                                     key="skiff", tag="v2.1", flash=["856014"],
                                                                     flash_tag="v2.1"))
    assert p.route == "download-mode" and not p.ok and any("UC25HQ80" in b for b in p.blockers)


# ------------------------------------------------------------------------------------------------ verify read, "Back to …"

def test_verify_install_compares_a_fresh_read_and_writes_nothing(env):
    before = env.flash()
    v = FJ.verify_install(env.job(), before, 0x78000, "SLOOP 3")
    assert v.same and v.first_difference == -1 and v.restarted
    assert [c[0] for c in env.get()["calls"]] == ["find", "download-mode", "info", "backup", "restart"]
    wrong = bytearray(before)
    wrong[0x5001] ^= 1
    wrong[0x90000] ^= 1                                              # (beyond the end: not compared)
    v = FJ.verify_install(env.job(), bytes(wrong), 0x78000, "SLOOP 3")
    assert not v.same and v.first_difference == 0x5001
    nothing_written(env, before)


def _ctx(env, **kw):
    from types import SimpleNamespace
    return SimpleNamespace(settings=SimpleNamespace(backups_dir=str(env.backups)), profile=env.prof, mboot=None,
                           is_virtual=False, **kw)


def test_way_back_offers_each_target_with_its_route(env, monkeypatch):
    from fm1companion import i18n
    from fm1companion.ui import flash_tools
    monkeypatch.setattr(i18n, "gettext", lambda m: m)          # (English, whatever an earlier test set)
    monkeypatch.setattr(flash_tools, "_", lambda m: m)
    env.prof.unit_id = UID
    opts = {o["key"]: o for o in flash_tools.way_back_options(_ctx(env))}
    assert list(opts) == ["backup", "sloop3", "felucca", "v15"]
    assert not opts["backup"]["ok"] and opts["backup"]["why"]          # (no backup of this unit yet)
    assert opts["sloop3"]["ok"] and "MIDI MODE" in opts["sloop3"]["route"]
    FJ.backup(env.job())                                              # (a whole-flash backup of this unit)
    opts = {o["key"]: o for o in flash_tools.way_back_options(_ctx(env))}
    assert opts["backup"]["ok"] and opts["backup"]["path"].is_file() and opts["backup"]["path"].name in \
        opts["backup"]["route"]
    assert "m-bootloader" in opts["v15"]["route"] and opts["v15"]["ok"]    # (the backup showed m-bootloader)
    assert "download mode" in opts["felucca"]["route"]
    hardware.note_flash_id(env.prof, 0xB36014, "rom")                 # (a newer unit: no V15 boot area there)
    opts = {o["key"]: o for o in flash_tools.way_back_options(_ctx(env))}
    assert not opts["v15"]["ok"] and "newer unit" in opts["v15"]["why"]
