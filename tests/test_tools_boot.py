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
            "back: before a Felucca-family firmware, the original boot area must come back too.")
BY_HAND = ("Not tested on a real FM-1 through this app yet: {what} ran only against a stand-in for M-Flasher. The owner "
           "did the same with M-Flasher by hand, outside the app (2026-11-18/19).")
BOOT = (ROOT / "fm1companion" / "tools" / "bundled" / "m-bootloader" / "fm1-uboot.boot").read_bytes()
VERSION = "2.4-395e6cc"


@pytest.fixture
def v15(monkeypatch):
    """a synthetic stand-in for M-VAVE's V15 package (structurally valid, identity FM-1_015) with its checksums
    patched in as the pinned ones"""
    import random
    import fake_mflasher
    flash = bytearray(random.Random("synthetic-v15").randbytes(0x93000))
    flash[:0x4000] = fake_mflasher.synthetic_image("synthetic-v15")[:0x4000]     # (a structured boot area)
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
    assert not bootarea.note(p, bootarea.ORIGINAL, "backup", when=100.0)               # (nothing new)
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
    env.prof.unit_id = UID                                                             # (the profile knows its unit)
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
    assert planner.boot_trap_text(_state(boot_area=kind, boot_area_text="x")) in p.blockers
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

def test_new_tool_kinds_are_marked_per_the_hardware_run_and_the_newer_unit_stays_open():
    for k in ("mf_download_button", "mf_bootloader_install", "mf_bootloader_remove", "mf_newer_removal_refused",
              "plan_boot_block"):
        assert tested.TOOLS_HARDWARE[k][0], k                            # (owner's run 2026-11-21)
    assert not tested.TOOLS_HARDWARE["mf_new_chip_bootloader"][0] and "app_step3_pi" in tested.OWNER_REPORTS
    assert "outside the app" in tested.OWNER_REPORTS["mboot_by_hand"]
    assert tested.tool_note("mf_bootloader_install", new_chip=True) == _(BY_HAND).format(what=_(tested.TOOLS_HARDWARE[
        "mf_new_chip_bootloader"][1]))
    assert tested.tool_note("mf_bootloader_install") != tested.tool_note("mf_bootloader_install", new_chip=True)
    doc = (ROOT / "docs" / "flash-protocol.md").read_text("utf-8")
    assert "Step 3" in doc and "bootloader --stock" in doc and bootarea.V15_BOOT_AREA_SHA256[:8] in doc


# ------------------------------------------------------------------------------------------------ review fixes

def _install_with_v15_area(env, area):
    """the fake unit's boot area is V15's (synthetic stand-in) – as on an earlier unit with the original boot area"""
    img = bytearray(env.flash())
    img[:0x4000] = area
    Path(str(env.state) + ".flash").write_bytes(bytes(img))


def test_h_removal_on_a_newer_unit_is_refused_and_names_the_way_back(env, v15):
    raw, _area = v15
    env.set(mode="midi", flash=0xB36014)
    old = FJ.backup(env.job())                                         # (a backup made before m-bootloader)
    env.prof.unit_id = UID
    install(env)
    before = env.flash()
    nb = len(boot_calls(env))
    with pytest.raises(FJ.Refused) as e:
        remove(env, raw)
    offer = e.value.offer
    assert e.value.kind == "newer-chip-stock" and offer is not None and offer.name in e.value.reason
    assert offer.parent == old.path.parent and dict(FJ.unit_backups(offer.parent, UID))[offer] != bootarea.MBOOT
    assert env.flash() == before and len(boot_calls(env)) == nb
    # the profile now knows it is a newer unit: refused before the FM-1 is even sent into download mode
    n = len(env.get()["calls"])
    with pytest.raises(FJ.Refused) as e:
        remove(env, raw)
    assert e.value.kind == "newer-chip-stock" and len(env.get()["calls"]) == n


def test_h_removal_on_a_newer_unit_without_a_backup_says_there_is_no_way(env, v15):
    raw, _area = v15
    before = env.flash()
    env.set(mode="midi", flash=0xB36014)
    with pytest.raises(FJ.Refused) as e:
        remove(env, raw)
    assert e.value.kind == "newer-chip-stock" and e.value.offer is None
    nothing_written(env, before)
    assert not boot_calls(env) and not calls(env, "backup")


def test_hw2_removal_says_whether_this_units_own_boot_area_was_v15s(env, v15):
    """(hardware run 2026-11-21) the removal compares the unit's own earlier backups with V15's boot area"""
    raw, area = v15
    same = _("An earlier backup of this unit ({name}) shows exactly V15's boot area: the removal puts back this "
             "unit's own original boot area.")
    differs = _("An earlier backup of this unit ({name}) shows a boot area that differs from V15's: the removal "
                "writes V15's, not this unit's own. The exact way back to that state is writing {name} back "
                "(Recovery › Tools › “Write flash back …”).")
    none = _("The app has no earlier backup of this unit that shows its boot area before m-bootloader, so it cannot "
             "tell whether this FM-1's own original boot area was V15's (units sold with V16, for example, may have "
             "come with another one).")
    # (a) only m-bootloader states known (m-bootloader put on outside the app): the generic sentence
    import fake_mflasher
    img = bytearray(fake_mflasher.synthetic_image())
    img[:0x4000] = bootarea.compose_short(bytes(img[:0x4000]), BOOT)
    Path(str(env.state) + ".flash").write_bytes(bytes(img))
    env.set(mode="midi", bootloader="m-bootloader 2.4-395e6cc (fm1)")
    plans = []
    remove(env, raw, plans)
    assert none in plans[0].notes
    # (b) an earlier backup with another boot area than V15's (the fake's own): "differs", naming it
    Path(str(env.state) + ".flash").write_bytes(fake_mflasher.synthetic_image())
    env.set(mode="midi")
    other = FJ.backup(env.job())
    install(env)
    plans = []
    remove(env, raw, plans)
    assert any(n.startswith(differs.split("{name}")[0]) for n in plans[0].notes)
    assert none not in plans[0].notes and other.path.exists()
    # (c) an earlier backup with V15's boot area: "exactly its original"
    _install_with_v15_area(env, area)
    FJ.backup(env.job())
    install(env)
    plans = []
    remove(env, raw, plans)
    assert any(n.startswith(same.split("{name}")[0]) for n in plans[0].notes) and none not in plans[0].notes


@pytest.mark.parametrize("rev,removal", [("old", True), ("new", False), ("", True)])
def test_h_the_planner_names_the_right_way_out_per_unit(rev, removal):
    st = _state(boot_area=bootarea.OTHER, boot_area_text="x", revision=rev)
    p = planner.plan_install(st, _target())
    text = next(b for b in p.blockers if planner.BOOT_TRAP.format(what="x") in b)
    assert planner.boot_way(st) in text
    assert (_("Remove m-bootloader (original boot area)") in text) is removal
    if rev == "new":
        assert planner.BOOT_WAY["new"] in text
    op = planner.plan_install(_state(identity="FM1_OFFICIAL_OPEN", felucca=False, firmware_key="fm1-open",
                                     revision=rev), _target())
    assert any(planner.boot_way(st) in b for b in op.blockers)
    mb = planner.plan_install(_state(identity="FM-1_727", firmware_key="skiff", boot_area=bootarea.MBOOT,
                                     revision=rev), _target())
    assert any(planner.boot_way(st) in w for w in mb.warnings)


def test_m1_restart_refused_while_a_write_record_is_open(env):
    env.set(mode="midi", fault_bootloader="write-fail")
    with pytest.raises(FJ.WriteFailed):
        install(env)
    assert env.get()["mode"] == "download"
    env.prof.unit_id = UID
    n = len(calls(env, "restart"))
    with pytest.raises(FJ.Refused) as e:
        FJ.leave_download_mode(env.job())
    assert e.value.kind == "open-record" and len(calls(env, "restart")) == n and env.get()["mode"] == "download"
    FJ.forget_write(env.journal.dir)
    assert FJ.leave_download_mode(env.job()) == "/dev/sg1"


def test_l5_restart_checks_which_unit_it_is(env):
    env.set(mode="download")
    env.prof.unit_id = OTHER_UID                                        # (the active profile is another unit)
    with pytest.raises(FJ.Refused) as e:
        FJ.leave_download_mode(env.job())
    assert e.value.kind == "other-unit" and not calls(env, "restart")
    env.prof.unit_id = ""                                               # (nothing tells: ask)
    asked = []
    with pytest.raises(FJ.Refused) as e:
        FJ.leave_download_mode(env.job(), lambda text: asked.append(text) or False)
    assert e.value.kind == "not-confirmed" and asked and "…" + UID[-4:] in asked[0] and not calls(env, "restart")
    assert FJ.leave_download_mode(env.job(), lambda text: True) == "/dev/sg1"
    env.set(mode="download")
    env.prof.unit_id = UID                                              # (its own unit: no question)
    assert FJ.leave_download_mode(env.job(), lambda text: pytest.fail("asked")) == "/dev/sg1"


def test_m2_only_the_listed_build_counts_on_a_newer_unit(env, monkeypatch):
    # the bundled build is the one hardware.json lists – a pin bump without a table entry makes this fail
    assert hardware.support("m-bootloader", bootarea.boot_identity(BOOT)[1]).status == hardware.YES
    for v in ("2.5", "2.5-abcdef0", "3.0", "2.10"):
        assert hardware.support("m-bootloader", v).status != hardware.YES, v
    env.set(mode="midi", flash=0xB36014)
    before = env.flash()
    monkeypatch.setattr(FJ, "bundled_bootloader", lambda store: (BOOT, "2.5-abcdef0"))
    with pytest.raises(FJ.Refused) as e:
        install(env)
    assert e.value.kind == "newer-chip"
    nothing_written(env, before)


def test_l1_the_composed_boot_area_matches_m_flashers_and_keeps_the_chip_key():
    import fake_mflasher
    img = fake_mflasher.synthetic_image()
    area = bootarea.compose_short(img[:0x4000], BOOT)
    assert area is not None and area == fake_mflasher.short_boot_area(img, BOOT)      # (two independent writings)
    assert bootarea.holds(area, BOOT) and bootarea.stored_mbootloader(area) == ("fm1", VERSION)
    assert bootarea.chip_key_record(area) == bootarea.chip_key_record(img[:0x4000]) != b""
    names = [(n, a) for _e, a, _l, n in bootarea.boot_table(area)]
    assert names[:2] == [("uboot.boot", 0xA0), ("isd_config.ini", 0x3FA0)]
    broken = bytearray(img[:0x4000])
    broken[0x38D0] ^= 1                                                 # (a damaged chip-key record)
    assert bootarea.compose_short(bytes(broken), BOOT) is None


def test_l1_a_stale_sector_fails_the_verification(env):
    """the unit has m-bootloader already, but its configuration sector (0x3000-0x3FFF: config and chip-key record) is
    stale: only that sector must be written; M-Flasher announces it, says Done and leaves it – the fresh read of the
    WHOLE boot area against the composed one catches it"""
    install(env)
    img = bytearray(env.flash())
    img[0x3FD0:0x3FE0] = b"\x00" * 16                                  # (stale settings behind the chip-key record)
    Path(str(env.state) + ".flash").write_bytes(bytes(img))
    env.set(mode="midi", fault_bootloader="skip-sector")
    with pytest.raises(FJ.WriteFailed) as e:
        install(env)
    assert e.value.kind == "verify-mismatch" and env.get()["written"][-1] == [0x3000, 0x4000]


def test_l1_a_plan_that_leaves_out_a_sector_is_refused_before_yes(env):
    before = env.flash()
    env.set(mode="midi", fault_bootloader="plan-partial")
    with pytest.raises(FJ.Refused) as e:
        install(env)
    assert e.value.kind == "gate"
    nothing_written(env, before)


def test_l1_the_gate_wants_exactly_the_sectors_that_differ():
    title, note = mp.bootloader_title(VERSION), mp.BOOT_SHORT_NOTE
    expect = {"unit_id": UID, "flash_id": 0x856014, "chip_key": 0x980F, "title": title, "note": note,
              "runs": [(0, 0x4000)]}
    gate = FJ.boot_gate(expect)
    assert gate(_out(title, [(0, 0x4000)], note)) == ""
    assert gate(_out(title, [(0x3000, 0x4000)], note)) != ""                # (sectors 0-2 must change too)


def test_l2_already_installed_settles_an_open_record(env):
    install(env)
    rec_path = FJ.record_file(env.journal.dir)
    rec = json.loads(rec_path.read_text())
    rec["state"] = "writing"                                            # (the app died after "yes")
    FJ._save_record(rec_path, rec)
    assert FJ.unfinished_write(env.journal.dir) is not None
    assert install(env).note == "already"
    assert FJ.unfinished_write(env.journal.dir) is None


def test_l2_already_original_settles_an_open_record(env, v15):
    raw, area = v15
    install(env)
    remove(env, raw)
    rec_path = FJ.record_file(env.journal.dir)
    rec = json.loads(rec_path.read_text())
    rec["state"] = "failed"
    FJ._save_record(rec_path, rec)
    assert remove(env, raw).note == "already" and FJ.unfinished_write(env.journal.dir) is None


def test_l3_the_newest_evidence_wins_and_a_bad_time_does_not_block_forever():
    p = devices.Profile(id="aaaaaaaaaa", name="Unit 1")
    p.firmware_history = [{"time": "not a time", "firmware": "fm1-open"}]
    cur = bootarea.current(p)
    assert cur["kind"] == "" and cur["how"] == "unsure"                 # (not known – the plan suggests a backup)
    st = _state(boot_area=cur["kind"], boot_area_unsure=True)
    pl = planner.plan_install(st, _target())
    assert not pl.boot_restore and any("Back up the whole flash" in w or "FM1 Open" in w for w in pl.warnings)
    bootarea.note(p, bootarea.ORIGINAL, "installed", when=100.0)
    assert bootarea.current(p)["kind"] == bootarea.ORIGINAL             # (a measurement beats an undated entry)
    p.firmware_history.append({"time": 200.0, "firmware": "sloop3"})    # (SLOOP 3 seen after the measurement)
    assert bootarea.current(p)["kind"] == bootarea.MBOOT
    bootarea.note(p, bootarea.ORIGINAL, "installed", when=300.0)
    assert bootarea.current(p)["kind"] == bootarea.ORIGINAL
    p.firmware_history.append({"time": 300.0, "firmware": "fm1-open"})  # (a tie: the measurement wins)
    assert bootarea.current(p)["kind"] == bootarea.ORIGINAL


def test_l4_removal_while_fm1_open_runs_says_what_that_means(env, v15):
    raw, _area = v15
    env.prof.firmware = "fm1-open"
    plans = []
    remove(env, raw, plans)
    assert any("Workbench" in n for n in plans[0].notes)


# ------------------------------------------------------------------------------------------------ re-review fixes

def _broken_backup(env):
    """a backup whose boot area is half-written (sector 3 erased: no chip-key record), the newest one of the unit"""
    import fake_mflasher
    good = FJ.backup(env.job())
    img = bytearray(env.flash())
    img[0x3000:0x4000] = b"\xff" * 0x1000
    Path(str(env.state) + ".flash").write_bytes(bytes(img))
    time.sleep(1.1)
    broken = FJ.backup(env.job())
    Path(str(env.state) + ".flash").write_bytes(fake_mflasher.synthetic_image())     # (the unit put right again)
    return good, broken


def test_n1_a_half_written_backup_is_never_offered(env, v15):
    raw, _area = v15
    env.set(mode="midi", flash=0xB36014)
    good, broken = _broken_backup(env)
    assert not bootarea.intact(broken.path.read_bytes()[:0x4000]) and bootarea.intact(good.path.read_bytes()[:0x4000])
    env.prof.unit_id = UID
    with pytest.raises(FJ.Refused) as e:
        remove(env, raw)
    assert e.value.kind == "newer-chip-stock" and e.value.offer is not None
    assert e.value.offer != broken.path and bootarea.intact(e.value.offer.read_bytes()[:0x4000])
    assert broken.path not in [p for p, _k in FJ.unit_backups(broken.path.parent, UID)]


def test_n1_a_backup_with_a_broken_boot_area_is_not_written_back(env):
    _good, broken = _broken_backup(env)
    before = env.flash()
    env.set(mode="midi")
    with pytest.raises(FJ.Refused) as e:
        FJ.restore(env.job(), broken.path, lambda plan: True)
    assert e.value.kind == "image-boot-broken"
    nothing_written(env, before)
    assert not calls(env, "restore")


def test_n1_backups_taken_during_an_open_write_are_marked_and_refused(env):
    env.set(mode="midi", fault_bootloader="write-fail")
    with pytest.raises(FJ.WriteFailed):
        install(env)
    env.set(mode="download")
    during = FJ.backup(env.job())                                       # (taken while the record is open)
    side = json.loads(during.path.with_suffix(".json").read_text())
    assert side.get("taken_during_open_write") is True
    assert during.path not in [p for p, _k in FJ.unit_backups(during.path.parent, UID)]
    with pytest.raises(FJ.Refused) as e:
        FJ.restore(env.job(), during.path, lambda plan: True)
    assert e.value.kind == "image-during-write"


def test_n1_the_original_is_preferred_over_newer_backups(env, monkeypatch):
    first = FJ.backup(env.job())
    install(env)
    time.sleep(1.1)
    img = bytearray(env.flash())
    img[:0x4000] = first.path.read_bytes()[:0x4000]
    img[0x80:0xA0] = fake_table_entry()                                 # (another intact, non-m-bootloader state)
    Path(str(env.state) + ".flash").write_bytes(bytes(img))
    newest = FJ.backup(env.job())
    monkeypatch.setattr(bootarea, "V15_BOOT_AREA_SHA256", sha(first.path.read_bytes()[:0x4000]))
    order = FJ.unit_backups(first.path.parent, UID)
    kinds = dict(order)
    assert order[0][1] == bootarea.ORIGINAL and kinds[newest.path] == bootarea.OTHER
    assert [p for p, _k in order].index(newest.path) > [p for p, _k in order].index(first.path)


def fake_table_entry():
    import fake_mflasher
    return fake_mflasher._table_entry("key_mac", 0xFF000, 0x1000, 1)


def test_n2_already_installed_on_unit_b_does_not_settle_unit_as_record(env):
    install(env)
    rec_path = FJ.record_file(env.journal.dir)
    rec = json.loads(rec_path.read_text())
    rec.update(state="writing", profile="bbbbbbbbbb", unit_sha256=sha(OTHER_UID.encode()))     # (unit A's record)
    FJ._save_record(rec_path, rec)
    assert install(env).note == "already"                                                    # (unit B)
    assert FJ.unfinished_write(env.journal.dir) is not None
    rec.update(profile=env.prof.id, unit_sha256=sha(UID.encode()))                          # (its own record)
    FJ._save_record(rec_path, rec)
    assert install(env).note == "already" and FJ.unfinished_write(env.journal.dir) is None


def test_n2_restores_nothing_to_write_does_not_settle_another_units_record(env):
    old = FJ.backup(env.job())
    FJ._save_record(FJ.record_file(env.journal.dir), {"state": "writing", "pid": 0, "profile": "bbbbbbbbbb",
                                                      "unit_sha256": sha(OTHER_UID.encode()),
                                                      "image_sha256": sha(old.path.read_bytes())})
    env.set(mode="download")
    res = FJ.restore(env.job(), old.path, lambda plan: True)
    assert res.note == "identical" and FJ.unfinished_write(env.journal.dir) is not None


def test_low_a_future_dated_fm1_open_entry_does_not_block_for_ever():
    p = devices.Profile(id="aaaaaaaaaa", name="Unit 1")
    p.firmware_history = [{"time": time.time() + 10 * 365 * 86400, "firmware": "fm1-open"}]
    assert bootarea.current(p)["how"] == "unsure"
    bootarea.note(p, bootarea.ORIGINAL, "installed")
    assert bootarea.current(p)["kind"] == bootarea.ORIGINAL



# ------------------------------------------------------------------------------------------------ hardware run fixes

IMPORT_HINT = ("The app has no such backup of this unit. If M-Flasher itself made one (its .bin with its .json), import "
               "it first – “Import a flash backup …” on Backups › Flash backups or Recovery › Tools – then the app "
               "offers it here. Without one there is no way back from the app.")


def _mflasher_dump(folder: Path, image: bytes, uid=UID, flash="0xB36014", saved="20000101-000000", json_ok=True):
    """a dump the way M-Flasher's own `backup` saves it (ops.rs save_backup: .bin + .json), synthetic values"""
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / f"fm1-{saved}.bin"
    p.write_bytes(image)
    rows = {"model": "FM-1", "bootloader": "stock (M-VAVE)", "firmware": "Felucca", "app": "OK", "chip key": "0x980F",
            "flash": flash, "unit id": uid, "loader": "M-Flasher 1.3, CPU at 96 MHz",
            "sha256": sha(image) if json_ok else "0" * 64, "saved": saved, "tool": "m-flasher 0.12.1"}
    p.with_suffix(".json").write_text(json.dumps(rows, indent=2))
    return p


def test_hw1_an_m_flasher_dump_is_imported_to_the_unit_it_names(env):
    import fake_mflasher
    img = fake_mflasher.synthetic_image("dump-before-mboot")
    src = _mflasher_dump(env.tmp / "elsewhere", img)
    env.prof.unit_id = UID
    res = FJ.import_backup(src, env.backups, env.profiles)
    assert res.profile is env.prof and res.path.parent == env.backups / "Unit 1" / "Flash" and not res.already
    assert res.path.read_bytes() == img
    side = json.loads(res.path.with_suffix(".json").read_text())
    assert side["imported"]["from"] == "M-Flasher" and side["unit id"] == UID and side["flash"] == "0xB36014"
    assert side["created"].startswith("2000-01-01") and "imported from M-Flasher" in side["note"]
    assert FJ.import_backup(src, env.backups, env.profiles).already                     # (twice: nothing new)
    assert (res.path, bootarea.OTHER) in FJ.unit_backups(res.path.parent, UID)


def test_hw1_import_asks_for_the_profile_and_never_guesses(env):
    import fake_mflasher
    src = _mflasher_dump(env.tmp / "elsewhere", fake_mflasher.synthetic_image("x"))
    asked = []
    with pytest.raises(FJ.Refused) as e:
        FJ.import_backup(src, env.backups, env.profiles, choose=lambda uid, c: asked.append((uid, c)) or None)
    assert e.value.kind == "import-unassigned" and asked and asked[0][1] == [env.prof]
    with pytest.raises(FJ.Refused):
        FJ.import_backup(src, env.backups, env.profiles)                               # (no one to ask: refused)
    res = FJ.import_backup(src, env.backups, env.profiles, choose=lambda uid, c: c[0])
    assert res.profile is env.prof and env.prof.unit_id == UID                       # (the chosen one learns the id)
    other = devices.Profile(id="bbbbbbbbbb", name="Unit 2", unit_id=OTHER_UID)
    env.profiles.profiles[other.id] = other
    src2 = _mflasher_dump(env.tmp / "elsewhere2", fake_mflasher.synthetic_image("y"), uid="0f1e2d3c4b5a6978")
    with pytest.raises(FJ.Refused) as e:                                              # (only profiles without an id)
        FJ.import_backup(src2, env.backups, env.profiles, choose=lambda uid, c: other)
    assert e.value.kind == "import-unassigned"


@pytest.mark.parametrize("case,kind", [("no-json", "import-no-json"), ("size", "import-size"),
                                       ("sha", "import-damaged"), ("broken", "import-boot-broken"),
                                       ("no-uid", "import-no-unit-id")])
def test_hw1_import_refuses_what_could_never_be_a_way_back(env, case, kind):
    import fake_mflasher
    img = bytearray(fake_mflasher.synthetic_image("z"))
    if case == "broken":
        img[0x3000:0x4000] = b"\xff" * 0x1000
    if case == "size":
        img = img[:-1]
    src = _mflasher_dump(env.tmp / "elsewhere", bytes(img), uid="" if case == "no-uid" else UID,
                         json_ok=case != "sha")
    if case == "no-json":
        src.with_suffix(".json").unlink()
    env.prof.unit_id = UID
    with pytest.raises(FJ.Refused) as e:
        FJ.import_backup(src, env.backups, env.profiles)
    assert e.value.kind == kind and not list((env.backups).rglob("*.bin"))


def test_hw1_the_newer_unit_refusal_offers_the_imported_dump(env, v15):
    import fake_mflasher
    raw, _area = v15
    env.set(mode="midi", flash=0xB36014)
    env.prof.unit_id = UID
    with pytest.raises(FJ.Refused) as e:
        remove(env, raw)
    assert e.value.offer is None and _(IMPORT_HINT) in e.value.reason
    res = FJ.import_backup(_mflasher_dump(env.tmp / "elsewhere", fake_mflasher.synthetic_image("pre")), env.backups,
                           env.profiles)
    with pytest.raises(FJ.Refused) as e:
        remove(env, raw)
    assert e.value.offer == res.path


def test_hw3_a_known_newer_unit_is_refused_before_any_question(env, monkeypatch):
    from types import SimpleNamespace as NS
    from fm1companion.ui import flash_tools
    hardware.set_revision(env.prof, hardware.NEW, "rom", hardware.NEW_ID)
    assert FJ.removal_refused_early(env.job()) is not None
    asked, shown = [], []
    monkeypatch.setattr(flash_tools, "confirm", lambda *a, **k: asked.append(a) or False)
    monkeypatch.setattr(flash_tools, "show_error", lambda p, t, m, d="": shown.append(m))
    ctx = NS(settings=NS(backups_dir=str(env.backups)), store=env.profiles, profile=env.prof, is_virtual=False,
             tools_store=env.store)
    assert flash_tools.remove_boot_flow(None, ctx, store=env.store) is False
    assert shown and not asked and not env.get().get("calls")                        # (no flow promised, nothing run)
    p2 = devices.Profile(id="cccccccccc", name="Unit 3")
    assert FJ.removal_refused_early(env.job(profile=p2)) is None


def test_hw5_download_mode_names_its_real_reason_and_the_rescue_write_is_off(env, monkeypatch):
    from fm1companion.flash.uboot import UbootDevice
    from fm1companion.ui.pages import recovery
    FJ.INTENDED.clear()
    FJ.GUIDE_SHOWN.clear()
    monkeypatch.setattr(FJ, "_usb_path_of", lambda d: "1-1")
    dev = UbootDevice(sg="/dev/sg1", block="", size=0, usb_id="4c4a:8057", vendor="WL82", model="UBOOT1.00",
                      usb_path="1-1", product="WL80UBOOT1.00")
    FJ.download_mode(env.job())
    assert FJ.intended_reason("1-1") == "button"
    text = recovery.intended_text(dev)
    assert _(recovery.REASONS[("button", "")]) in text
    assert recovery.rescue_write_blocked(dev) and recovery.rescue_write_blocked(None) == ""
    env.set(mode="midi")
    FJ.backup(env.job(), restart_after=False)
    assert FJ.intended_reason("1-1") == "backup" and _(recovery.REASONS[("backup", "sent")]) in recovery.intended_text(dev)
    FJ.INTENDED.clear()


def test_hw6_the_usb_name_is_shown_and_no_chip_difference_implied():
    from fm1companion.flash.uboot import UbootDevice
    from fm1companion.ui.pages import recovery
    dev = UbootDevice(sg="/dev/sg1", block="", size=0, usb_id="4c4a:8057", vendor="WL82", model="UBOOT1.00",
                      usb_path="1-1", product="WL80UBOOT1.00")
    name = recovery.usb_name(dev)
    assert "WL80UBOOT1.00" in name and "WL82" not in name
    assert "WL80UBOOT1.00" in FJ.guide_text()


def test_hw7_a_blocked_plan_has_no_second_check_head_hint():
    st = _state(boot_area=bootarea.MBOOT, boot_area_text="x", revision="old")
    p = planner.plan_install(st, _target())
    assert p.boot_restore and planner.TRAP_TEXT not in p.warnings
    q = planner.plan_install(_state(), _target())                                     # (not blocked: the hint stays)
    assert planner.TRAP_TEXT in q.warnings
