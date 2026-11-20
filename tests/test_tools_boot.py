# SPDX-License-Identifier: GPL-3.0-only
"""m-bootloader install / removal, "Into download mode" / "Restart the FM-1" and the boot-area awareness of the
install planner (fm1companion/tools/flashjob.py, tools/bootarea.py, flash/planner.py) – against tests/fake_mflasher.py
with synthetic unit ids, a pseudo-random synthetic flash and a synthetic stand-in for M-VAVE's V15 package (its
checksums are patched in; the real package is not in the repository).

As in test_tools_flash: every refusal proves via nothing_written() that the fake's flash is unchanged, no "yes" was
sent and no range was written."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fm1companion import devices, hardware  # noqa: E402
from fm1companion.flash import journal as fj, package as pk, planner, tested  # noqa: E402
from fm1companion.tools import bootarea, flashjob as FJ, mflasher_parse as mp, runner  # noqa: E402
from fm1companion.i18n import _  # noqa: E402
from test_tools_flash import ARCH, OTHER_UID, UID, calls, env, nothing_written, sha  # noqa: E402,F401

OPEN_TOO = ("FM1 Open changes the boot area (measured on V14), and the original firmware does not necessarily put it "
            "back: before a Felucca-family firmware, put back the original boot area too (Recovery › Tools › “Remove "
            "m-bootloader (original boot area)”, with a whole-flash backup first).")
BY_HAND = ("Not tested on a real FM-1 through this app yet: {what} ran only against a stand-in for M-Flasher. The owner "
           "did the same with M-Flasher by hand, outside the app (2026-11-18/19).")
BOOT = (ROOT / "fm1companion" / "tools" / "bundled" / "m-bootloader" / "fm1-uboot.boot").read_bytes()
VERSION = "2.4-395e6cc"


@pytest.fixture
def v15(monkeypatch):
    """a synthetic stand-in for M-VAVE's V15 package (structurally valid, identity FM-1_015) with its checksums
    patched in as the pinned ones"""
    import random
    flash = bytearray(random.Random("synthetic-v15").randbytes(0x93000))
    flash[0:16] = b"SYNTHETIC-V15-BA"
    raw = pk.build_package("FM-1_015", family="stock", app=bytes(flash))
    monkeypatch.setattr(pk, "STOCK_V15_SHA256", sha(raw))
    monkeypatch.setattr(bootarea, "V15_BOOT_AREA_SHA256", sha(bytes(flash[:0x4000])))
    return raw, bytes(flash[:0x4000])


@pytest.fixture(autouse=True)
def _seeded(request):
    """the fake's flash exists from the start (it makes it on first use otherwise), so that "before" can be read"""
    if "env" in request.fixturenames:
        import fake_mflasher
        e = request.getfixturevalue("env")
        f = Path(str(e.state) + ".flash")
        if not f.exists():
            f.write_bytes(fake_mflasher.synthetic_image())
    yield


def ok(plans):
    def confirm(plan):
        plans.append(plan)
        return True
    return confirm


def install(env, plans=None, **kw):
    return FJ.install_bootloader(env.job(**kw), ok(plans if plans is not None else []))


def remove(env, package, plans=None, **kw):
    return FJ.remove_bootloader(env.job(**kw), ok(plans if plans is not None else []), package)


def boot_calls(e):
    return calls(e, "bootloader")


# ------------------------------------------------------------------------------------------------ bootarea helpers

def test_boot_identity_of_the_bundled_file_and_its_stored_form():
    assert bootarea.boot_identity(BOOT) == ("fm1", VERSION)
    assert bootarea.boot_identity(BOOT[:-1] + bytes([BOOT[-1] ^ 1])) is None          # (payload CRC)
    assert bootarea.boot_identity(b"\0" * 64) is None
    area = bytearray(b"\xff" * 0x4000)
    area[0xA0:0xA0 + len(BOOT)] = bootarea.stored_form(BOOT)
    assert bootarea.holds(bytes(area), BOOT)
    area[0x200] ^= 1
    assert not bootarea.holds(bytes(area), BOOT)
    assert bootarea.mboot_name("m-bootloader 2.4-395e6cc (fm1)") == "m-bootloader 2.4-395e6cc"


def test_v15_area_needs_the_pinned_package_and_its_measured_boot_area(v15, monkeypatch):
    raw, area = v15
    assert bootarea.v15_area(raw) == area
    assert bootarea.classify(area) == bootarea.ORIGINAL
    assert bootarea.classify(b"\0" * 0x4000, "m-bootloader 2.4-395e6cc (fm1)") == bootarea.MBOOT
    assert bootarea.classify(b"\0" * 0x4000, "stock (M-VAVE)") == bootarea.OTHER        # (FM1 Open says "stock" too)
    with pytest.raises(bootarea.BootAreaError):
        bootarea.v15_area(raw + b"\0")                                                 # (another file)
    monkeypatch.setattr(bootarea, "V15_BOOT_AREA_SHA256", "0" * 64)
    with pytest.raises(bootarea.BootAreaError):
        bootarea.v15_area(raw)                                                         # (not the measured boot area)


def test_the_pinned_v15_boot_area_hash_is_a_public_value():
    assert len(bootarea.V15_BOOT_AREA_SHA256) == 64 and bootarea.V15_BOOT_AREA_SHA256.startswith("d9f43191")


def test_current_boot_area_from_record_history_and_sloop3():
    p = devices.Profile(id="aaaaaaaaaa", name="Unit 1")
    assert bootarea.current(p)["kind"] == ""
    p.firmware = "sloop3"
    assert bootarea.current(p)["kind"] == bootarea.MBOOT                               # (SLOOP 3 needs m-bootloader)
    p.firmware = "skiff"
    assert bootarea.note(p, bootarea.ORIGINAL, "backup", when=100.0)
    assert not bootarea.note(p, bootarea.ORIGINAL, "backup", when=200.0)               # (nothing new)
    assert bootarea.current(p)["kind"] == bootarea.ORIGINAL
    p.firmware_history = [{"time": 50.0, "firmware": "fm1-open"}]                      # (before the exact reading)
    assert bootarea.current(p)["kind"] == bootarea.ORIGINAL
    p.firmware_history.append({"time": 150.0, "firmware": "fm1-open"})                 # (FM1 Open came afterwards)
    assert bootarea.current(p)["kind"] == bootarea.OPEN
    p.firmware_history.append({"time": 160.0, "firmware": "stock-v15"})                # (V15 does not put it back)
    assert bootarea.current(p)["kind"] == bootarea.OPEN
    q = devices.Profile(id="bbbbbbbbbb", name="Unit 2", firmware_history=[{"time": 1.0, "firmware": "fm1-open"}])
    assert bootarea.current(q)["kind"] == bootarea.OPEN
    assert "FM1 Open" in bootarea.describe(bootarea.current(q))


def test_the_profile_keeps_its_boot_area_across_saves(tmp_path):
    store = devices.ProfileStore(tmp_path / "devices.json")
    p = devices.Profile(id="aaaaaaaaaa", name="Unit 1")
    bootarea.note(p, bootarea.MBOOT, "m-flasher", bootloader="m-bootloader 2.4-395e6cc (fm1)")
    store.profiles[p.id] = p
    store.save()
    q = devices.ProfileStore(tmp_path / "devices.json").profiles[p.id]
    assert q.boot_area["kind"] == bootarea.MBOOT and q.boot_area["bootloader"] == "m-bootloader 2.4-395e6cc"


# ------------------------------------------------------------------------------------------------ runner forms

@pytest.mark.parametrize("args", [
    ["bootloader", "--file", "/x/fm1-uboot.boot", "--device", "/dev/sg1", "--backup-dir", "/x"],
    ["bootloader", "--stock", "/x/FM-1_015.fwsc", "--device", "/dev/sg1", "--backup-dir", "/x"]])
def test_bootloader_forms_are_known_but_only_with_a_gate(env, args):
    runner._check_args(args)
    with pytest.raises(runner.ToolError):
        runner.run_mflasher(args, store=env.store, journal=env.journal, log_dir=env.logs)     # (no gate)
    assert not boot_calls(env)


@pytest.mark.parametrize("args", [
    ["bootloader"], ["bootloader", "--device", "/dev/sg1"],
    ["bootloader", "--file", "/x/a.bin", "--device", "/dev/sg1", "--backup-dir", "/x"],
    ["bootloader", "--stock", "/x/a.boot", "--device", "/dev/sg1", "--backup-dir", "/x"],
    ["bootloader", "--file", "/x/a.boot", "--device", "/dev/sg1", "--backup-dir", "/x", "--yes"],
    ["bootloader", "--file", "/x/a.boot", "--device", "/dev/sg1", "--backup-dir", "/x", "--restart"],
    ["bootloader", "--file", "x.boot", "--device", "/dev/sg1", "--backup-dir", "/x"],
    ["bootloader", "--file", "/x/../a.boot", "--device", "/dev/sg1", "--backup-dir", "/x"],
    ["bootloader", "--file", "/x/a.boot", "--device", "/dev/sda", "--backup-dir", "/x"],
    ["firmware", "/x/p.fwsc", "--device", "/dev/sg1", "--backup-dir", "/x"]])
def test_other_bootloader_forms_are_refused(args):
    with pytest.raises(runner.ToolError):
        runner._check_args(args)


# ------------------------------------------------------------------------------------------------ download mode

def test_into_download_mode_and_restart_as_their_own_actions(env):
    res = FJ.download_mode(env.job())
    assert res.device == "/dev/sg1" and res.sent
    assert [c[0] for c in env.get()["calls"]] == ["find", "download-mode"]           # (stays in download mode)
    assert env.get()["mode"] == "download"
    res = FJ.download_mode(env.job())
    assert not res.sent                                                                # (it is there already)
    assert FJ.leave_download_mode(env.job()) == "/dev/sg1"
    assert env.get()["mode"] == "midi" and calls(env, "restart") == [["restart", "--device", "/dev/sg1"]]
    with pytest.raises(FJ.Refused) as e:
        FJ.leave_download_mode(env.job())
    assert e.value.kind == "none"
    assert list(env.logs.glob("mflasher-download-*.txt"))


@pytest.mark.parametrize("mode,own,kind", [("midi", "", "guide"), ("midi", "SMK-37 MIDI 1", "guide"),
                                           ("two-midi", "FM-1 MIDI 1", "several"), ("none", "FM-1 MIDI 1", "guide"),
                                           ("sg-missing", "FM-1 MIDI 1", "sg")])
def test_into_download_mode_sends_the_soft_key_only_to_the_apps_own_unit(env, mode, own, kind):
    env.set(mode=mode)
    with pytest.raises(FJ.Refused) as e:
        FJ.download_mode(env.job(softkey_for=own))
    assert e.value.kind == kind
    assert not calls(env, "download-mode")


def test_restart_refuses_with_two_units(env):
    env.set(mode="two")
    with pytest.raises(FJ.Refused) as e:
        FJ.leave_download_mode(env.job())
    assert e.value.kind == "several" and not calls(env, "restart")


# ------------------------------------------------------------------------------------------------ install

def test_install_writes_only_the_boot_area_after_backup_gate_and_verify(env):
    before = env.flash()
    plans = []
    res = install(env, plans)
    after = env.flash()
    assert res.written and res.verified and res.restarted and res.version == VERSION
    assert [c[0] for c in env.get()["calls"]] == ["find", "download-mode", "info", "backup", "backup", "bootloader",
                                                  "backup", "restart"]
    s = env.get()
    assert s["answers"] == ["yes\n"] and not s.get("yes_flag")
    assert s["written"] and all(b <= 0x4000 for _a, b in s["written"])
    assert after[0x4000:] == before[0x4000:] and after[:0x4000] != before[:0x4000]
    assert bootarea.holds(after[:0x4000], BOOT)
    argv = boot_calls(env)[0]
    assert argv[1] == "--file" and argv[2].endswith("/fm1-uboot.boot") and Path(argv[2]).parent.name.startswith(
        "write-") and "--yes" not in argv and "--restart" not in argv
    p = plans[0]
    assert p.op == "install" and p.version == VERSION and p.unit_name == "Unit 1" and p.revision == "old"
    assert p.backup.is_file() and p.backup == res.backup and p.current == "stock (M-VAVE)"
    assert env.prof.boot_area["kind"] == bootarea.MBOOT and env.prof.boot_area["bootloader"] == f"m-bootloader {VERSION}"
    assert env.prof.boot_area["how"] == "installed"
    rec = json.loads(FJ.record_file(env.journal.dir).read_text())
    assert rec["state"] == "verified" and rec["op"] == "bootloader-install" and UID not in json.dumps(rec)
    log = next(env.logs.glob("mflasher-bootloader-*.txt")).read_text()
    assert "answered yes" in log and UID not in log
    # (a second click: it is there already – nothing is written)
    n = len(s["written"])
    res2 = install(env)
    assert not res2.written and res2.note == "already" and len(env.get()["written"]) == n


@pytest.mark.parametrize("fault", ["plan-outside", "plan-extra", "warning", "note", "stray", "other-unit",
                                   "other-chip"])
def test_install_gate_closes_stdin_on_any_mismatch_nothing_is_written(env, fault):
    before = env.flash()
    env.set(mode="midi", fault_bootloader=fault)
    with pytest.raises(FJ.Refused) as e:
        install(env)
    assert e.value.kind == "gate" and not e.value.written
    nothing_written(env, before)
    assert len(env.get()["answers"]) == 1                              # (it asked; the app closed stdin)
    assert not FJ.record_file(env.journal.dir).exists()
    assert env.prof.boot_area.get("kind") != bootarea.MBOOT


def _out(title, runs, note, *, uid=UID, flash="0x856014", key="0x980F", warn="", extra=""):
    n = sum((b - a) // 0x1000 for a, b in runs)
    rng = ", ".join(f"0x{a:05X}-0x{b - 1:05X}" for a, b in runs)
    return (f"Connecting to /dev/sg1\n  model       FM-1\n  bootloader  stock (M-VAVE)\n  firmware    Felucca\n"
            f"  app         OK\n  chip key    {key}\n  flash       {flash}\n  unit id     {uid}\n"
            f"  loader      M-Flasher 1.3\n    - evidence\n{extra}\n{title}\nWrites {n} sectors ({n * 4} KiB): {rng}\n"
            f"{note}\n" + (f"WARNING: {warn}\n" if warn else "") + mp.PROMPT)


def test_boot_gate_answers_only_on_an_exact_match():
    title, note = mp.bootloader_title(VERSION), mp.BOOT_SHORT_NOTE
    expect = {"unit_id": UID, "flash_id": 0x856014, "chip_key": 0x980F, "title": title, "note": note, "runs": None}
    gate = FJ.boot_gate(expect)
    assert gate(_out(title, [(0, 0x4000)], note)) == ""
    assert gate(_out(title, [(0, 0x3000)], note)) == ""                                 # (inside the boot area)
    bad = [_out(title, [(0, 0x5000)], note), _out(title, [(0, 0x4000), (0x4000, 0x5000)], note),
           _out(title, [(0xF0000, 0xF1000)], note), _out(title, [(0, 0x4000)], mp.BOOT_KEEP_NOTE),
           _out("Install the bootloader 2.3-abcdef0 for the FM-1", [(0, 0x4000)], note),
           _out(mp.stock_title("FM-1_015"), [(0, 0x4000)], note),
           _out(title, [(0, 0x4000)], note, uid=OTHER_UID), _out(title, [(0, 0x4000)], note, flash="0xB36014"),
           _out(title, [(0, 0x4000)], note, key="0x1234"), _out(title, [(0, 0x4000)], note, warn="careful"),
           _out(title, [(0, 0x4000)], note, extra="something new"),
           _out(title, [(0, 0x4000)], note).replace("Writes 4 sectors (16 KiB)", "Writes 3 sectors (12 KiB)"),
           _out(title, [(0, 0x4000)], note).replace(mp.PROMPT, "Writes 1 sectors (4 KiB): 0x00000-0x00FFF\n"
                                                              + mp.PROMPT)]
    for out in bad:
        assert gate(out) != "", out[-200:]
    stock = dict(expect, title=mp.stock_title("FM-1_015"), note=mp.BOOT_STOCK_NOTE, runs=[(0, 0x2000)])
    g2 = FJ.boot_gate(stock)
    assert g2(_out(stock["title"], [(0, 0x2000)], mp.BOOT_STOCK_NOTE)) == ""
    assert g2(_out(stock["title"], [(0, 0x4000)], mp.BOOT_STOCK_NOTE)) != ""          # (not the app's own diff)


def test_install_on_a_newer_unit_needs_a_build_marked_yes(env, monkeypatch):
    assert hardware.support("m-bootloader", VERSION).status == hardware.YES          # (2.4-395e6cc counts per the table)
    env.set(mode="midi", flash=0xB36014)
    plans = []
    res = install(env, plans)
    assert res.written and plans[0].revision == "new" and plans[0].notes
    # a build hardware.json does not mark "yes": refused – in the session, before the backup
    env.set(mode="midi", flash=0xB36014)
    before, n = env.flash(), len(env.get().get("written", []))
    nb, nr = len(boot_calls(env)), len(calls(env, "backup"))
    monkeypatch.setattr(FJ, "bundled_bootloader", lambda store: (BOOT, "2.3-abcdef0"))
    with pytest.raises(FJ.Refused) as e:
        install(env)
    assert e.value.kind == "newer-chip"
    assert env.flash() == before and len(env.get().get("written", [])) == n
    assert len(boot_calls(env)) == nb and len(calls(env, "backup")) == nr            # (refused before the backup)
    # a profile that is known to be a newer unit: refused before the FM-1 is even sent into download mode
    hardware.set_revision(env.prof, hardware.NEW, "rom", hardware.NEW_ID)
    n = len(env.get()["calls"])
    with pytest.raises(FJ.Refused) as e:
        install(env)
    assert e.value.kind == "newer-chip" and len(env.get()["calls"]) == n


def test_install_refused_without_the_automatic_backup(env, monkeypatch):
    before = env.flash()
    monkeypatch.setattr(FJ, "backup_before_boot_write", lambda job: FJ.Refusal("no backup today", "no-backup"))
    with pytest.raises(FJ.Refused) as e:
        install(env)
    assert e.value.kind == "no-backup"
    nothing_written(env, before)
    assert not boot_calls(env)


@pytest.mark.parametrize("fault", ["read-mismatch", "crc-refuse"])
def test_install_refused_when_the_backup_reads_differ(env, fault):
    before = env.flash()
    env.set(mode="midi", fault_backup=fault)
    with pytest.raises(FJ.Refused):
        install(env)
    nothing_written(env, before)
    assert not boot_calls(env) and calls(env, "restart")                              # (the app's own download mode)


@pytest.mark.parametrize("mode", ["two", "two-midi"])
def test_install_refused_with_two_units(env, mode):
    before = env.flash()
    env.set(mode=mode)
    with pytest.raises(FJ.Refused) as e:
        install(env)
    assert e.value.kind == "several"
    nothing_written(env, before)


def test_install_declined_or_cancelled_writes_nothing(env):
    before = env.flash()
    with pytest.raises(FJ.Refused) as e:
        FJ.install_bootloader(env.job(), lambda plan: False)
    assert e.value.kind == "declined"
    nothing_written(env, before)
    assert not boot_calls(env)


def test_install_needs_a_write_verified_m_flasher_and_the_pinned_file(env, monkeypatch):
    env.data["tools"]["m-flasher"]["write_verified"] = {}
    with pytest.raises(FJ.Refused) as e:
        install(env)
    assert e.value.kind == "not-write-verified" and not env.get().get("calls")
    env.data["tools"]["m-flasher"]["write_verified"] = {"0.12.1": {ARCH: env.store.bundled("m-flasher").sha256}}
    env.data["tools"]["m-bootloader"]["files"]["fm1-uboot.boot"]["sha256"] = "0" * 64
    with pytest.raises(FJ.Refused) as e:
        install(env)
    assert e.value.kind == "boot-checksum" and not env.get().get("calls")


def test_install_on_an_unknown_unit_is_refused(env):
    before = env.flash()
    env.set(mode="download")
    with pytest.raises(FJ.Refused) as e:
        install(env, profile=None)
    assert e.value.kind == "unknown-unit"
    nothing_written(env, before)


@pytest.mark.parametrize("fault,kind,written", [("write-fail", "write-failed", True),
                                                ("no-done", "write-failed", True),
                                                ("backup-fails", "not-started", False),
                                                ("wrong-area", "verify-mismatch", True)])
def test_a_failed_boot_write_is_reported_with_the_backup(env, fault, kind, written):
    env.set(mode="midi", fault_bootloader=fault)
    with pytest.raises(FJ.Refused) as e:
        install(env)
    assert e.value.kind == kind and e.value.written is written
    rec = FJ.record_file(env.journal.dir)
    if written:
        assert isinstance(e.value, FJ.WriteFailed) and e.value.backup.is_file()
        assert json.loads(rec.read_text())["state"] == "failed"
        assert calls(env, "restart") == []                                             # (stays in download mode)
    else:
        assert not rec.exists()
    assert env.prof.boot_area.get("how") != "installed"


CHILD = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
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
FJ.install_bootloader(job, lambda plan: True)
'''


def test_kill_after_yes_the_boot_write_finishes_and_the_record_says_so(env):
    """the app is killed (SIGKILL) right after "yes": M-Flasher writes the boot area to its end on its own; the
    record (op bootloader-install) is on disk and the next start finds it"""
    before = env.flash()
    env.profiles.save()
    env.set(mode="midi", fault_bootloader="slow-write")
    args = env.tmp / "child.json"
    args.write_text(json.dumps({"bin": str(env.tmp / "tools-bin"), "upd": str(env.tmp / "upd"), "data": env.data,
                                "arch": ARCH, "profiles": str(env.tmp / "devices.json"), "prof": env.prof.id,
                                "backups": str(env.backups), "logs": str(env.logs), "journal": str(env.journal.dir),
                                "work": str(env.tmp / "work")}))
    child = subprocess.Popen([sys.executable, "-c", CHILD, str(ROOT), str(args)])
    rec_path = FJ.record_file(env.journal.dir)
    t0 = time.monotonic()
    while time.monotonic() - t0 < 60 and not (rec_path.exists() and json.loads(rec_path.read_text()).get("pid")):
        time.sleep(0.05)
    rec = json.loads(rec_path.read_text())
    assert rec["state"] == "writing" and rec["op"] == "bootloader-install" and rec["file"] == "fm1-uboot.boot"
    time.sleep(0.3)
    child.kill()
    child.wait(10)
    t0 = time.monotonic()
    while time.monotonic() - t0 < 30 and fj._alive(rec["pid"]):
        time.sleep(0.1)
    after = env.flash()
    assert bootarea.holds(after[:0x4000], BOOT) and after[0x4000:] == before[0x4000:]   # (written to its end)
    left = FJ.unfinished_write(env.journal.dir)
    assert left is not None and left["done"] and not left["alive"] and Path(left["pre_backup"]).read_bytes() == before


def test_writing_flag_blocks_quitting_only_after_yes(env):
    seen = []
    job = env.job(on_writing=lambda rec: seen.append(FJ.WRITING.is_set()))
    FJ.install_bootloader(job, lambda plan: not FJ.WRITING.is_set())
    assert seen == [True] and not FJ.WRITING.is_set()


# ------------------------------------------------------------------------------------------------ remove

def test_remove_puts_back_v15s_boot_area_only(env, v15):
    raw, area = v15
    install(env)
    mid = env.flash()
    plans = []
    res = remove(env, raw, plans)
    after = env.flash()
    assert res.written and res.verified and plans[0].op == "remove" and plans[0].source == "FM-1_015"
    assert after[:0x4000] == area and after[0x4000:] == mid[0x4000:]
    argv = boot_calls(env)[-1]
    assert argv[1] == "--stock" and argv[2].endswith("/FM-1_015.fwsc")
    assert Path(argv[2]).parent.name.startswith("write-")
    assert env.prof.boot_area["kind"] == bootarea.ORIGINAL and env.prof.boot_area["how"] == "installed"
    assert all(b <= 0x4000 for _a, b in env.get()["written"])
    # nothing to do a second time
    n = len(env.get()["written"])
    again = remove(env, raw)
    assert not again.written and again.note == "already" and len(env.get()["written"]) == n


def test_remove_needs_the_pinned_v15_package(env, v15):
    raw, _area = v15
    for bad in (raw + b"\0", raw[:-1], b"not a package"):
        with pytest.raises(FJ.Refused) as e:
            remove(env, bad)
        assert e.value.kind == "package"
    assert not env.get().get("calls")                                                  # (nothing was even asked)


@pytest.mark.parametrize("fault", ["plan-outside", "warning", "other-unit", "note"])
def test_remove_gate_refuses_anything_but_the_boot_area_diff(env, v15, fault):
    raw, _area = v15
    before = env.flash()
    env.set(mode="midi", fault_bootloader=fault)
    with pytest.raises(FJ.Refused) as e:
        remove(env, raw)
    assert e.value.kind == "gate"
    nothing_written(env, before)


def test_remove_refused_by_m_flasher_writes_nothing(env, v15):
    raw, _area = v15
    before = env.flash()
    env.set(mode="midi", fault_bootloader="refuse-key")
    with pytest.raises(FJ.Refused) as e:
        remove(env, raw)
    assert e.value.kind == "mflasher" and "chip key" in e.value.reason
    nothing_written(env, before)


def test_remove_warns_about_sloop3(env, v15):
    raw, _area = v15
    env.prof.firmware = "sloop3"
    plans = []
    remove(env, raw, plans)
    assert any("SLOOP 3" in n for n in plans[0].notes)


# ------------------------------------------------------------------------------------------------ detection

def test_backup_records_the_boot_area_and_m_flasher_info_names_m_bootloader(env, monkeypatch):
    img = env.flash() if Path(str(env.state) + ".flash").exists() else None
    FJ.backup(env.job())
    assert env.prof.boot_area["kind"] == bootarea.OTHER and env.prof.boot_area["how"] == "backup"
    monkeypatch.setattr(bootarea, "V15_BOOT_AREA_SHA256", sha(env.flash()[:0x4000]))
    FJ.backup(env.job())
    assert env.prof.boot_area["kind"] == bootarea.ORIGINAL
    env.set(mode="midi", bootloader="m-bootloader 2.4-395e6cc (fm1)")
    monkeypatch.setattr(bootarea, "V15_BOOT_AREA_SHA256", "0" * 64)
    FJ.backup(env.job())
    assert env.prof.boot_area["kind"] == bootarea.MBOOT and env.prof.boot_area["bootloader"] == "m-bootloader 2.4-395e6cc"
    saved = json.loads(env.profiles.path.read_text())["profiles"][0]["boot_area"]
    assert saved["kind"] == bootarea.MBOOT
    del img


# ------------------------------------------------------------------------------------------------ the planner

def _state(**kw):
    kw.setdefault("identity", "FM-1_91061")
    kw.setdefault("felucca", True)
    kw.setdefault("firmware_key", "melodee")
    return planner.DeviceState(**kw)


def _target(key="skiff", family="felucca", name="Skiff"):
    return planner.Target(name=name, product="FM-1_727", family=family, key=key)


def _stock():
    return planner.Target(name="Original V15", product="FM-1_015", family="stock", key="stock-v15",
                          sha256=pk.STOCK_V15_SHA256)


@pytest.mark.parametrize("kind", [bootarea.MBOOT, bootarea.OPEN, bootarea.OTHER])
def test_check_head_trap_a_felucca_target_on_a_changed_boot_area_is_refused(kind):
    p = planner.plan_install(_state(boot_area=kind, boot_area_text="x"), _target())
    assert not p.ok and p.boot_restore
    assert planner.BOOT_TRAP.format(what="x") in p.blockers
    # the way back to the original V15 from a running Felucca-family firmware too
    p = planner.plan_install(_state(boot_area=kind), _stock())
    assert not p.ok and p.boot_restore


def test_m_bootloader_with_an_update_of_the_same_firmware_is_no_trap_but_warned():
    st = _state(identity="FM-1_727", firmware_key="skiff", boot_area=bootarea.MBOOT)
    p = planner.plan_install(st, _target())
    assert p.v15_option == "none" and not p.boot_restore
    assert any("m-bootloader" in w and "FAILED -5" in w for w in p.warnings)            # (both words in de/es too)
    p = planner.plan_install(_state(identity="FM-1_727", firmware_key="skiff", boot_area=bootarea.OPEN), _target())
    assert p.boot_restore                                                    # (FM1 Open's boot area: refused)
    p = planner.plan_install(st, _target(), via_v15=False)                  # (expert: no V15 step, no trap)
    assert not p.boot_restore


@pytest.mark.parametrize("kind", ["", bootarea.ORIGINAL])
def test_no_trap_with_the_original_or_an_unknown_boot_area(kind):
    p = planner.plan_install(_state(boot_area=kind), _target())
    assert not p.boot_restore and not any("original boot area" in b for b in p.blockers)
    p = planner.plan_install(_state(boot_area=kind), _stock())
    assert not p.boot_restore


def test_v15_from_the_original_firmware_is_no_trap():
    st = _state(identity="FM-1_015", felucca=False, firmware_key="stock-v15", boot_area=bootarea.OPEN)
    p = planner.plan_install(st, _stock())
    assert not p.boot_restore                         # (the original firmware has no CHECK HEAD; V15 policy as it was)
    p = planner.plan_install(st, _target())
    assert p.boot_restore                             # (… but a Felucca-family target is refused)


def test_away_from_fm1_open_needs_the_original_boot_area_first():
    st = _state(identity="FM1_OFFICIAL_OPEN", felucca=False, firmware_key="fm1-open")
    p = planner.plan_install(st, _target())
    assert any(_(OPEN_TOO) in b for b in p.blockers)
    # after the Workbench took it back to V15: the profile still remembers FM1 Open – the planner refuses a Felucca
    # target until the original boot area is back
    prof = devices.Profile(id="aaaaaaaaaa", name="Unit 1", firmware="stock-v15",
                           firmware_history=[{"time": 1.0, "firmware": "fm1-open"},
                                             {"time": 2.0, "firmware": "stock-v15"}])
    cur = bootarea.current(prof)
    st = _state(identity="FM-1_015", felucca=False, firmware_key="stock-v15", boot_area=cur["kind"],
                boot_area_text=bootarea.describe(cur))
    p = planner.plan_install(st, _target())
    assert p.boot_restore and not p.ok
    bootarea.note(prof, bootarea.ORIGINAL, "installed")                               # (put back by the app)
    st.boot_area = bootarea.current(prof)["kind"]
    assert not planner.plan_install(st, _target()).boot_restore


def test_the_v15_policy_is_otherwise_unchanged():
    a = planner.plan_install(_state(), _target())
    b = planner.plan_install(_state(boot_area=bootarea.ORIGINAL), _target())
    assert a.kinds() == b.kinds() and a.v15_option == b.v15_option and a.blockers == b.blockers


# ------------------------------------------------------------------------------------------------ docs

def test_new_tool_kinds_are_not_marked_tested_and_the_owner_report_is_outside_the_app():
    for k in ("mf_download_button", "mf_bootloader_install", "mf_bootloader_remove"):
        assert k in tested.TOOLS_HARDWARE and not tested.TOOLS_HARDWARE[k][0]
    assert "outside the app" in tested.OWNER_REPORTS["mboot_by_hand"]
    assert tested.tool_note("mf_bootloader_install") == _(BY_HAND).format(what=_(tested.TOOLS_HARDWARE[
        "mf_bootloader_install"][1]))
    doc = (ROOT / "docs" / "flash-protocol.md").read_text("utf-8")
    assert "Step 3" in doc and "bootloader --stock" in doc and bootarea.V15_BOOT_AREA_SHA256[:8] in doc
