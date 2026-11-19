# SPDX-License-Identifier: GPL-3.0-only
"""Whole-flash backup and restore through M-Flasher (fm1companion/tools/flashjob.py) against tests/fake_mflasher.py
– synthetic unit ids and a pseudo-random synthetic flash only.

The point of most tests: on every refusal path nothing reaches the "flash" (the fake's image file stays the same,
its "answers" never hold "yes", its "written" stays empty)."""
from __future__ import annotations

import copy
import hashlib
import json
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fm1companion import devices, flashdump  # noqa: E402
from fm1companion.flash import journal as fj  # noqa: E402
from fm1companion.tools import flashjob as FJ  # noqa: E402
from fm1companion.tools import manifest as mf  # noqa: E402
from fm1companion.tools import mflasher_parse as mp  # noqa: E402
from fm1companion.tools import runner, store as st  # noqa: E402

FAKE = ROOT / "tests" / "fake_mflasher.py"
# synthetic unit ids (the fake's own default 0123456789abcdef counts as a placeholder for the app: devices.BOGUS_SERIAL)
UID, OTHER_UID = "13579bdf02468ace", "2468ace013579bdf"
ARCH = mf.arch() or "x86_64"


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def fake_binary(path: Path) -> bytes:
    body = f"#!{sys.executable}\n" + FAKE.read_text("utf-8").split("\n", 1)[1]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, "utf-8")
    path.chmod(0o755)
    return body.encode()


class Env:
    pass


@pytest.fixture
def env(tmp_path, monkeypatch):
    data = copy.deepcopy(mf.load())
    ver = data["tools"]["m-flasher"]["version"]
    b = fake_binary(tmp_path / "tools-bin" / mf.mflasher_relpath(ver, ARCH))
    data["tools"]["m-flasher"]["arch"][ARCH].update(binary_sha256=sha(b), binary_size=len(b))
    data["tools"]["m-flasher"]["write_verified"][ver][ARCH] = sha(b)
    state = tmp_path / "fake-state.json"
    monkeypatch.setenv("FAKE_MFLASHER_STATE", str(state))
    e = Env()
    e.tmp, e.state, e.data = tmp_path, state, data
    e.store = st.Store(bin_dir=tmp_path / "tools-bin", updates_dir=tmp_path / "upd", data=data, machine=ARCH)
    e.profiles = devices.ProfileStore(tmp_path / "devices.json")
    e.prof = devices.Profile(id="aaaaaaaaaa", name="Unit 1")
    e.profiles.profiles[e.prof.id] = e.prof
    e.backups = tmp_path / "Backups"
    e.logs = tmp_path / "logs"
    e.journal = fj.Journal(tmp_path / "journal")

    def set_state(**kw):
        cur = e.get()
        keep = {k: cur[k] for k in ("calls",) if k in cur}
        state.write_text(json.dumps({"unit_id": UID, **keep, **kw}))
    e.set = set_state
    e.get = lambda: json.loads(state.read_text()) if state.exists() else {}
    e.flash = lambda: (Path(str(state) + ".flash")).read_bytes()
    e.set(mode="midi")

    def job(profile="default", **kw):
        return FJ.Job(backups_dir=e.backups, profiles=e.profiles, profile=e.prof if profile == "default" else profile,
                      store=e.store, log_dir=e.logs, journal=e.journal, **kw)
    e.job = job
    return e


def calls(e, cmd):
    return [c for c in e.get().get("calls", []) if c and c[0] == cmd]


def nothing_written(e, before: bytes):
    s = e.get()
    assert e.flash() == before
    assert not s.get("written")
    assert all(a.strip() != "yes" for a in s.get("answers", []))
    assert not s.get("yes_flag")                              # the app never passes --yes


def make_backup(e) -> FJ.BackupResult:
    return FJ.backup(e.job())


def change_flash(e, at=(0x0000, 0x5000, 0x93000)):
    """the unit's flash changes after the backup (another firmware, m-bootloader …)"""
    img = bytearray(e.flash())
    for a in at:
        img[a:a + 16] = b"\x5a" * 16
    Path(str(e.state) + ".flash").write_bytes(bytes(img))
    return bytes(img)


# ------------------------------------------------------------------------------------------------ parser

def test_parse_plan_saved_done_and_download_device():
    out = ("  unit id     0123456789abcdef\n\nRestore a backup\nWrites 147 sectors (588 KiB): 0x00000-0x77FFF, "
           "0x7A000-0x92FFF, 0x95000-0x96FFF\nThe whole flash is restored, the bootloader included.\n"
           'Type "yes" to write: ')
    p = mp.parse_plan(out)
    assert p.title == "Restore a backup" and p.sectors == 147 and p.kib == 588 and not p.warnings
    assert p.runs == [(0, 0x78000), (0x7A000, 0x93000), (0x95000, 0x97000)]
    assert mp.WHOLE_FLASH_NOTE in p.notes
    assert sum((b - a) // 0x1000 for a, b in p.runs) == 147
    w = mp.parse_plan("Restore a backup\nWrites 1 sectors (4 KiB): 0x00000-0x00FFF\nWARNING: careful\n")
    assert w.warnings == ["careful"]
    assert mp.parse_plan("Restore a backup\nNothing to write: the flash already holds this.\n").nothing
    with pytest.raises(mp.ParseError):
        mp.parse_ranges("0x00000-0x00FFF, oops")
    assert mp.saved_paths("x\nBackup saved: /x/fm1-20000101-000000.bin\n") == ["/x/fm1-20000101-000000.bin"]
    # (after the answer M-Flasher goes on on the question's line: the question has no newline)
    assert mp.saved_paths(mp.PROMPT + "Backup saved: /x/a.bin\nWriting 0x00000-0x00FFF\n") == ["/x/a.bin"]
    assert mp.written_runs(mp.PROMPT + "Writing 0x00000-0x00FFF\n") == [(0, 0x1000)]
    assert mp.done_verified("Done: everything written is verified (the chip's CRC32 matches)\n")
    assert mp.done_verified("Done: everything written was read back and matches\n")
    assert not mp.done_verified("Done: something else\n")
    assert mp.download_device("Sent …\nIn download mode: /dev/sg2\tWL82 UBOOT1.00 (/dev/sg2)\n") == "/dev/sg2"


# ------------------------------------------------------------------------------------------------ runner forms

@pytest.mark.parametrize("args", [
    ["restore", "/x/a.bin", "--device", "/dev/sg1", "--backup-dir", "/x"],          # (no gate)
    ["restore", "/x/a.bin", "--device", "/dev/sg1", "--backup-dir", "/x", "--yes"],
    ["restore", "/x/a.bin", "--device", "/dev/sg1", "--backup-dir", "/x", "--restart"],
    ["restore", "/x/a.bin", "--keep-bootloader", "--device", "/dev/sg1", "--backup-dir", "/x"],
    ["restore", "a.bin", "--device", "/dev/sg1", "--backup-dir", "/x"],
    ["backup", "--device", "/dev/sg1"], ["backup", "--device", "/dev/sg1", "--backup-dir", "rel"],
    ["backup", "--device", "/dev/sg1", "--backup-dir", "/x", "--yes"], ["backup", "/x"],
    ["backup", "--device", "/dev/sg1", "--backup-dir", "/x/../etc"],
    ["download-mode", "--yes"], ["restart"], ["restart", "--device", "/dev/sda"],
    ["firmware", "/x/p.fwsc", "--device", "/dev/sg1"], ["bootloader", "--device", "/dev/sg1"]])
def test_only_the_known_forms_run(env, args):
    with pytest.raises(runner.ToolError):
        runner.run_mflasher(args, store=env.store, log_dir=env.logs, journal=env.journal)
    assert "calls" not in env.get()


def test_restore_command_needs_a_write_verified_binary(env):
    env.data["tools"]["m-flasher"]["write_verified"] = {}
    with pytest.raises(runner.ToolUnavailable):
        runner.run_mflasher(["restore", "/x/a.bin", "--device", "/dev/sg1", "--backup-dir", "/x"], store=env.store,
                            log_dir=env.logs, journal=env.journal, gate=lambda out: "")
    assert "calls" not in env.get()


# ------------------------------------------------------------------------------------------------ backup

def test_backup_sends_download_mode_reads_twice_and_keeps_one_image(env):
    before = env.flash() if Path(str(env.state) + ".flash").exists() else None
    res = make_backup(env)
    assert len(calls(env, "download-mode")) == 1 and len(calls(env, "backup")) == 2 and calls(env, "info")
    assert res.restarted and env.get()["mode"] == "midi"            # (the app had sent it there: back again)
    folder = env.backups / "Unit 1" / "Flash"
    bins = sorted(folder.glob("*.bin"))
    assert bins == [res.path] and not [p for p in folder.iterdir() if p.name.startswith(".")]   # staging gone
    img = res.path.read_bytes()
    assert img == env.flash() and len(img) == 0x100000 and (before is None or before == img)
    side = json.loads(res.path.with_suffix(".json").read_text())
    assert side["format"] == "fm1-flash-backup" and side["version"] == 2 and side["double_read"] is True
    assert side["unit id"] == UID and side["flash"] == "0x856014" and side["chip key"] == "0x980F"
    assert side["sha256"] == sha(img) == res.sha256 and side["boot_area_sha256"] == sha(img[:0x4000])
    assert side["tool"]["name"] == "M-Flasher" and side["tool"]["version"] == "0.12.1"
    assert side["tool"]["binary_sha256"] == env.store.select("m-flasher")[0].sha256
    assert side["firmware"]["m_flasher_guess"] == "Felucca" and side["profile"] == "aaaaaaaaaa"
    assert side["created"] and "M-Flasher 0.12.1" in side["read_through"]
    # M-Flasher's keys come first in the file (its restore takes the first "sha256" / "unit id")
    text = res.path.with_suffix(".json").read_text()
    assert text.index('"sha256"') < text.index("_sha256") and text.index('"unit id"') < text.index("unique_id")
    # the Backups page lists it, checked
    fb = flashdump.list_backups(env.backups, verify=True)
    assert [b.path for b in fb] == [res.path] and fb[0].ok and fb[0].unit_id == UID and fb[0].double_read
    # the profile learned the unit id and the flash chip (the app sent this unit into download mode itself)
    assert env.prof.unit_id == UID and env.prof.flash_id.upper() == "856014" and env.prof.hw_revision_source == "rom"
    # flight log: every step, the unit id masked
    logs = sorted(env.logs.glob("mflasher-backup-*.txt"))
    assert logs and UID not in logs[0].read_text() and "both reads identical" in logs[0].read_text()
    assert not any(UID in p.read_text() for p in env.logs.glob("*.txt"))
    nothing_written(env, img)


def test_backup_of_a_unit_already_in_download_mode_is_not_restarted(env):
    env.set(mode="download")
    res = FJ.backup(env.job(profile=None))
    assert not calls(env, "download-mode") and not res.restarted and env.get()["mode"] == "download"
    assert res.path.parent == env.backups / FJ.UNKNOWN_FOLDER / "Flash"
    assert env.prof.unit_id == ""                                     # (no profile named it: nothing learned)


def test_backup_names_the_profile_by_unit_id(env):
    env.prof.unit_id = UID
    env.set(mode="download")
    res = FJ.backup(env.job(profile=None))
    assert res.profile is env.prof and res.path.parent == env.backups / "Unit 1" / "Flash"


@pytest.mark.parametrize("fault,kind", [("read-mismatch", "read-mismatch"), ("crc-mismatch", "crc"),
                                        ("crc-refuse", "read"), ("garbage", "parse")])
def test_backup_refusals_keep_nothing(env, fault, kind):
    env.set(mode="download", fault_backup=fault)
    with pytest.raises(FJ.Refused) as e:
        FJ.backup(env.job())
    assert e.value.kind == kind and not e.value.written
    assert not list((env.backups).rglob("*.bin")) and not list(env.backups.rglob(".m-flasher-*"))


@pytest.mark.parametrize("state,kind", [({"mode": "midi", "fault_download-mode": "never"}, "guide"),
                                        ({"mode": "none"}, "guide"), ({"mode": "two"}, "several"),
                                        ({"mode": "two-midi"}, "several"), ({"mode": "sg-missing"}, "sg")])
def test_download_mode_refusals_and_guide(env, state, kind):
    env.set(**state)
    with pytest.raises(FJ.Refused) as e:
        FJ.backup(env.job())
    assert e.value.kind == kind and not calls(env, "backup")
    if kind == "guide":
        assert "PLAY" in e.value.reason and "DOWNLOAD MODE" in e.value.reason and "REC" in e.value.reason


def test_backup_of_another_profiles_unit_is_refused(env):
    env.prof.unit_id = OTHER_UID                                       # the profile is another unit
    with pytest.raises(FJ.Refused) as e:
        FJ.backup(env.job())
    assert e.value.kind == "other-profile" and not calls(env, "backup")
    assert env.prof.unit_id == OTHER_UID                               # nothing overwritten
    other = devices.Profile(id="bbbbbbbbbb", name="Unit 2", unit_id=UID)
    env.prof.unit_id = ""
    env.profiles.profiles[other.id] = other
    env.set(mode="midi")
    with pytest.raises(FJ.Refused) as e:
        FJ.backup(env.job())
    assert e.value.kind == "other-profile" and env.prof.unit_id == ""


def test_the_lock_is_held_for_the_whole_job(env):
    env.journal.acquire()
    try:
        other_window = fj.Journal(env.tmp / "journal")
        with pytest.raises(FJ.Refused) as e:
            FJ.backup(FJ.Job(backups_dir=env.backups, profiles=env.profiles, profile=env.prof, store=env.store,
                             log_dir=env.logs, journal=other_window))
        assert e.value.kind == "busy" and "calls" not in env.get()
    finally:
        env.journal.release()
    seen = []
    orig = FJ._one_read

    def spy(job, staging, n):
        seen.append(fj.Journal.busy())
        return orig(job, staging, n)
    FJ._one_read = spy
    try:
        make_backup(env)
    finally:
        FJ._one_read = orig
    assert seen == [True, True] and not fj.Journal.busy()


def test_mboot_flash_copy_has_the_same_description(tmp_path):
    from fm1companion.flash import mboot
    info = mboot.Info(version="2.4-395e6cc", kind=1, key=0x980F, capacity=0x100000, jedec=0x856014, uid=UID)
    img = bytes(range(256)) * 4096
    p = mboot.save_backup(tmp_path / "Flash", "Unit 1", info, img)
    side = mboot.check_backup(p)
    assert side["unit id"] == UID and side["flash"] == "0x856014" and side["chip key"] == "0x980F"
    assert side["version"] == 2 and side["boot_area_sha256"] == sha(img[:0x4000]) and not side["double_read"]
    assert flashdump.side_unit_id(side) == UID and flashdump.side_flash_id(side) == 0x856014
    assert FJ.check_image(p)["sha256"] == sha(img)


# ------------------------------------------------------------------------------------------------ restore

def restore(e, image, answer=True, **kw):
    seen = []

    def confirm(plan):
        seen.append(plan)
        return answer
    res = FJ.restore(e.job(**kw), image, confirm)
    return res, seen


def test_restore_writes_back_after_backup_gate_and_verify(env):
    old = make_backup(env)
    changed = change_flash(env)
    env.set(mode="midi", calls=[])
    res, seen = restore(env, old.path)
    assert res.written and res.verified and res.restarted and env.get()["mode"] == "midi"
    assert env.flash() == old.path.read_bytes()
    s = env.get()
    assert [a.strip() for a in s["answers"]] == ["yes"] and len(s["written"]) == 3 and not s.get("yes_flag")
    # the order: info, two reads (the automatic backup), the restore, one fresh read, restart
    order = [c[0] for c in s["calls"]]
    assert order == ["find", "download-mode", "info", "backup", "backup", "restore", "backup", "restart"]
    rc = calls(env, "restore")[0]
    assert "--yes" not in rc and "--restart" not in rc and "--keep-bootloader" not in rc
    # the automatic backup holds the state before the write and is listed
    assert res.backup.read_bytes() == changed and res.backup.parent == old.path.parent
    assert len(flashdump.list_backups(env.backups)) == 2
    plan = seen[0]
    assert plan.unit_name == "Unit 1" and plan.unit_id == UID and plan.boot_area and plan.sectors == 3
    assert plan.runs == [(0, 0x1000), (0x5000, 0x6000), (0x93000, 0x94000)] and plan.backup == res.backup
    assert plan.revision == "old"
    log = sorted(env.logs.glob("mflasher-restore-*.txt"))[-1].read_text()
    for needle in ("answered yes", "fresh read equals the image", "confirmed by the user", "equals the one made",
                   "Writing 0x93000-0x93FFF", "Done: everything written is verified", "m-flasher restore"):
        assert needle in log, needle
    assert not list(env.logs.glob("tool-*.txt"))                    # (one log per job, not one per run)
    assert not any(UID in p.read_text() for p in env.logs.glob("*.txt"))
    assert not list(env.backups.rglob(".m-flasher-*"))


def test_restore_of_an_identical_image_writes_nothing(env):
    old = make_backup(env)
    env.set(mode="midi", calls=[])
    res, seen = restore(env, old.path)
    assert not res.written and res.note == "identical" and not seen and not calls(env, "restore")


@pytest.mark.parametrize("change,kind", [({"unit_id": OTHER_UID}, "other-unit"),
                                         ({"flash": 0xB36014}, "other-chip")])
def test_image_of_another_unit_or_chip_is_refused_without_override(env, change, kind):
    old = make_backup(env)
    before = change_flash(env)
    env.prof.flash_id, env.prof.hw_revision, env.prof.hw_revision_source = "", "", ""
    if kind == "other-unit":                     # the connected unit is a known one – but not the image's
        env.profiles.profiles["bbbbbbbbbb"] = devices.Profile(id="bbbbbbbbbb", name="Unit 2", unit_id=OTHER_UID)
    env.set(mode="download", calls=[], **change)
    with pytest.raises(FJ.Refused) as e:
        restore(env, old.path, profile=None)
    assert e.value.kind == kind and not e.value.written
    assert not calls(env, "restore") and not calls(env, "backup")      # (refused before the automatic backup)
    nothing_written(env, before)


@pytest.mark.parametrize("fault", ["other-unit", "other-chip", "plan-extra", "warning", "other-unit-plan"])
def test_gate_closes_stdin_on_any_mismatch_nothing_is_written(env, fault):
    """M-Flasher's own block right before "yes" differs from the plan: the app never answers "yes" """
    old = make_backup(env)
    before = change_flash(env)
    env.set(mode="midi", fault_restore=fault)
    with pytest.raises(FJ.Refused) as e:
        restore(env, old.path)
    assert e.value.kind == "gate" and not e.value.written
    s = env.get()
    assert s["answers"] == [""]                                        # EOF: stdin closed, no "yes"
    nothing_written(env, before)
    assert len(flashdump.list_backups(env.backups)) == 2              # (the automatic backup stays)


def test_gate_function_answers_only_on_an_exact_match():
    info = ("  model       FM-1\n  bootloader  stock (M-VAVE)\n  firmware    Felucca\n  app         OK\n"
            "  chip key    0x980F\n  flash       0x856014\n  unit id     {uid}\n  loader      M-Flasher 1.3\n")
    plan = ("\nRestore a backup\nWrites 2 sectors (8 KiB): 0x00000-0x00FFF, 0x05000-0x05FFF\n"
            "The whole flash is restored, the bootloader included.\n")
    expect = {"unit_id": UID, "flash_id": 0x856014, "chip_key": 0x980F, "runs": [(0, 0x1000), (0x5000, 0x6000)]}
    gate = FJ.restore_gate(expect)
    good = info.format(uid=UID) + plan + mp.PROMPT
    assert gate(good) == ""
    bad = [info.format(uid=OTHER_UID) + plan, info.format(uid="–") + plan,
           good.replace("0x856014", "0xB36014"), good.replace("0x980F", "0x980E"),
           good.replace("0x05000-0x05FFF", "0x05000-0x06FFF"), good.replace("2 sectors (8 KiB)", "3 sectors (12 KiB)"),
           good.replace("The whole flash is restored, the bootloader included.\n", ""),
           good.replace("included.\n", "included.\nWARNING: x\n"), good.replace("Restore a backup", "Install firmware"),
           info.format(uid=UID) + mp.PROMPT, "garbage" + mp.PROMPT]
    for b in bad:
        assert gate(b) != "", b[-200:]


def test_restore_without_the_automatic_backup_is_impossible(env):
    old = make_backup(env)
    before = change_flash(env)
    env.set(mode="midi", calls=[], fault_backup="read-mismatch")
    with pytest.raises(FJ.Refused) as e:
        restore(env, old.path)
    assert e.value.kind == "read-mismatch" and not calls(env, "restore")
    nothing_written(env, before)
    # and the helper itself
    j = env.job()
    r = FJ.backup_before_boot_write(j)
    assert isinstance(r, FJ.Refusal) and r.kind == "not-identified"


def test_declining_the_danger_dialog_writes_nothing(env):
    old = make_backup(env)
    before = change_flash(env)
    env.set(mode="midi", calls=[])
    with pytest.raises(FJ.Refused) as e:
        restore(env, old.path, answer=False)
    assert e.value.kind == "declined" and not calls(env, "restore")
    nothing_written(env, before)


def test_cancel_before_yes_writes_nothing(env):
    old = make_backup(env)
    before = change_flash(env)
    env.set(mode="download", fault_restore="slow")
    ev = threading.Event()

    def confirm(plan):
        threading.Timer(1.0, ev.set).start()                     # cancelled while M-Flasher reads, before its question
        return True
    with pytest.raises(FJ.Refused) as e:
        FJ.restore(env.job(cancel=ev), old.path, confirm)
    assert e.value.kind == "cancelled" and not e.value.written
    nothing_written(env, before)


def test_timeout_before_the_question_writes_nothing(env, monkeypatch):
    old = make_backup(env)
    before = change_flash(env)
    env.set(mode="download", fault_restore="hang")
    monkeypatch.setitem(runner.TIMEOUTS, "restore", 2.0)
    t0 = time.monotonic()
    with pytest.raises(FJ.Refused) as e:
        restore(env, old.path)
    assert e.value.kind == "timeout" and time.monotonic() - t0 < 30
    nothing_written(env, before)


@pytest.mark.parametrize("fault", ["killed-in-write", "write-fail", "no-done"])
def test_a_failed_write_is_reported_with_the_backup_made_before(env, fault):
    old = make_backup(env)
    changed = change_flash(env)
    env.set(mode="download", calls=[], fault_restore=fault)
    with pytest.raises(FJ.WriteFailed) as e:
        restore(env, old.path)
    assert e.value.written and e.value.backup is not None and e.value.backup.read_bytes() == changed
    assert e.value.backup.name in e.value.reason and "download mode" in e.value.reason
    assert [a.strip() for a in env.get()["answers"]] == ["yes"]
    assert not calls(env, "restart")                                 # (stays in download mode)


def test_cancel_after_yes_is_ignored(env):
    old = make_backup(env)
    change_flash(env)
    env.set(mode="download")
    ev = threading.Event()
    orig = FJ.restore_gate

    def gate_then_cancel(expect):
        g = orig(expect)

        def wrapped(out):
            threading.Timer(0.05, ev.set).start()                 # cancel pressed right after "yes"
            return g(out)
        return wrapped
    FJ.restore_gate = gate_then_cancel
    try:
        res = FJ.restore(env.job(cancel=ev), old.path, lambda plan: True)
    finally:
        FJ.restore_gate = orig
    assert ev.is_set() and res.written and res.verified              # write, check and restart ran to their end
    assert env.flash() == old.path.read_bytes()
    assert [a.strip() for a in env.get()["answers"]] == ["yes"]


@pytest.mark.parametrize("damage", ["byte", "no-unit-id", "no-flash", "no-side"])
def test_damaged_or_anonymous_images_are_refused_before_anything_runs(env, damage):
    old = make_backup(env)
    env.set(mode="download", calls=[])
    side_p = old.path.with_suffix(".json")
    side = json.loads(side_p.read_text())
    if damage == "byte":
        b = bytearray(old.path.read_bytes())
        b[100] ^= 1
        old.path.write_bytes(bytes(b))
    elif damage == "no-unit-id":
        side["unit id"] = ""
        side["flash_chip"]["unique_id"] = ""
        side_p.write_text(json.dumps(side))
    elif damage == "no-flash":
        side["flash"] = ""
        side["flash_chip"]["jedec"] = ""
        side_p.write_text(json.dumps(side))
    else:
        side_p.unlink()
    with pytest.raises(FJ.Refused) as e:
        restore(env, old.path)
    assert e.value.kind in ("image-damaged", "no-unit-id", "no-flash-id") and not env.get()["calls"]


def test_restore_onto_a_unit_the_app_does_not_know_is_refused(env):
    old = make_backup(env)
    env.prof.unit_id = ""
    env.profiles.profiles.clear()
    before = change_flash(env)
    env.set(mode="download", calls=[])
    with pytest.raises(FJ.Refused) as e:
        restore(env, old.path, profile=None)
    assert e.value.kind == "unknown-unit" and not calls(env, "restore")
    nothing_written(env, before)


def test_restore_is_refused_without_a_write_verified_binary(env):
    old = make_backup(env)
    env.set(mode="download", calls=[])
    env.data["tools"]["m-flasher"]["write_verified"] = {}
    with pytest.raises(FJ.Refused) as e:
        restore(env, old.path)
    assert e.value.kind == "not-write-verified" and not env.get()["calls"]


def test_sidecar_unit_id_never_names_or_changes_a_profile(env):
    """the image's description is not evidence about the connected unit: only M-Flasher's read is"""
    old = make_backup(env)
    side_p = old.path.with_suffix(".json")
    # an image that claims another unit id (edited description): refused, and no profile learns that id
    side = json.loads(side_p.read_text())
    side["unit id"] = OTHER_UID
    side["flash_chip"]["unique_id"] = OTHER_UID
    side_p.write_text(json.dumps(side))
    env.prof.unit_id = ""
    env.set(mode="download")
    with pytest.raises(FJ.Refused) as e:
        restore(env, old.path, profile=None)
    assert e.value.kind == "unknown-unit"                             # (named by nobody: M-Flasher's id is in no profile)
    assert all(OTHER_UID not in devices.profile_unit_ids(p) for p in env.profiles.profiles.values())
