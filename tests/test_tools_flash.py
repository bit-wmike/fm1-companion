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
        kw.setdefault("softkey_for", "FM-1 MIDI 1")            # (the app's own FM-1 port: the fake's card is "FM-1")
        kw.setdefault("work_dir", tmp_path / "work")            # (private local folder for M-Flasher's files)
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


def change_flash(e, at=(0x0080, 0x5000, 0x93000)):
    """the unit's flash changes after the backup (another firmware, m-bootloader …) – the boot area's first sector
    changes at its last file-table entry, so it stays an intact boot area"""
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
                            log_dir=env.logs, journal=env.journal, gate=lambda out: "", out_dir=env.tmp / "o")
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


def test_m_flashers_own_backup_is_kept_when_it_differs(env):
    old = make_backup(env)
    change_flash(env)
    env.set(mode="download", fault_restore="drift")
    res, _seen = restore(env, old.path)
    assert res.written and res.verified and res.mflasher_backup is not None
    assert res.mflasher_backup.parent == old.path.parent and res.mflasher_backup.name.startswith("m-flasher-auto-")
    log = sorted(env.logs.glob("mflasher-restore-*.txt"))[-1].read_text()
    assert "DIFFERS from the one made before" in log and not list(env.backups.rglob(".m-flasher-*"))


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
    assert e.value.backup.name in e.value.reason
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


# ------------------------------------------------------------------------------------------------ docs

def test_tools_hardware_table_matches_untested_md():
    """nothing of the M-Flasher flows counts as run on hardware through the app; docs/UNTESTED.md says the same and
    records the owner's runs by hand as outside the app"""
    import re
    from fm1companion.flash import tested
    doc = (ROOT / "docs" / "UNTESTED.md").read_text("utf-8")
    sec = doc[doc.index("### M-Flasher through the app"):doc.index("### Backups, presets, projects, samples")]
    rows = dict(re.findall(r"^\| `(\w+)` – [^|]+\| (run on hardware|not run)\b[^|]*\|$", sec, re.M))
    assert rows == {k: ("run on hardware" if ok else "not run") for k, (ok, _l) in tested.TOOLS_HARDWARE.items()}
    assert [k for k, (ok, _l) in tested.TOOLS_HARDWARE.items() if ok] == [
        "mf_download_mode", "mf_backup", "mf_restore", "mf_new_chip_backup"]           # (Pi run 2026-11-20)
    assert not tested.TOOLS_HARDWARE["mf_new_chip_restore"][0]
    assert "2026-11-20" in sec and "ONE Raspberry Pi 5" in sec and "TWO units" in sec and "Not run: a large" in sec
    assert "app_flows_pi" in tested.OWNER_REPORTS
    for key, (_ok, label) in tested.TOOLS_HARDWARE.items():
        assert f"| `{key}` – {label} |" in sec, key
        assert key not in tested.STEPS
    assert "mflasher_by_hand" in tested.OWNER_REPORTS and "outside the app" in tested.OWNER_REPORTS["mflasher_by_hand"]
    assert "Owner, outside the app (2026-11-18/19" in sec
    assert tested.tool_note("mf_restore") != tested.tool_note("mf_restore", new_chip=True)
    assert "2026" in tested.tool_note("mf_restore", new_chip=True)
    for lang, needle in (("en", "what ran on real hardware (2026-11-20"),
                         ("de", "was an echter Hardware lief (20.11.2026"),
                         ("es", "lo que se probó en hardware real (20-11-2026")):
        page = (ROOT / "fm1companion" / "data" / "help" / lang / "03-update-modus.md").read_text("utf-8")
        assert needle in page and ("PLAY" in page and "REC" in page and "DOWNLOAD MODE" in page), lang


# ------------------------------------------------------------------------------------------------ review fixes

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
FJ.restore(job, Path(a["image"]), lambda plan: True)
'''


def test_h1_the_write_finishes_when_the_app_dies_after_yes(env):
    """H1: the app is killed (SIGKILL) right after "yes": M-Flasher is no child that dies with it – it writes to its
    end; the fsync'd record is there, and the next start finds the unfinished write-back"""
    import os
    import signal
    import subprocess
    old = make_backup(env)
    changed = change_flash(env)
    env.profiles.save()
    env.set(mode="midi", fault_restore="slow-write")
    args = env.tmp / "child.json"
    args.write_text(json.dumps({"bin": str(env.tmp / "tools-bin"), "upd": str(env.tmp / "upd"), "data": env.data,
                                "arch": ARCH, "profiles": str(env.tmp / "devices.json"), "prof": env.prof.id,
                                "backups": str(env.backups), "logs": str(env.logs), "journal": str(env.journal.dir),
                                "image": str(old.path), "work": str(env.tmp / "work")}))
    child = subprocess.Popen([sys.executable, "-c", CHILD, str(ROOT), str(args)])
    rec_path = FJ.record_file(env.journal.dir)
    t0 = time.monotonic()
    while time.monotonic() - t0 < 60 and not (rec_path.exists() and json.loads(rec_path.read_text()).get("pid")):
        time.sleep(0.05)
    rec = json.loads(rec_path.read_text())
    assert rec["state"] == "writing" and rec["image_sha256"] == sha(old.path.read_bytes())
    time.sleep(0.3)                                          # ("yes" is sent, the first run is being written)
    child.kill()
    child.wait(10)
    pid = rec["pid"]
    t0 = time.monotonic()
    while time.monotonic() - t0 < 30 and fj._alive(pid):
        time.sleep(0.1)
    assert not fj._alive(pid)
    assert env.flash() == old.path.read_bytes() != changed          # written completely, after the app's death
    assert len(env.get()["written"]) == 3
    left = FJ.unfinished_write(env.journal.dir)
    assert left is not None and left["state"] == "writing" and left["done"] and not left["alive"]
    assert Path(left["pre_backup"]).read_bytes() == changed and Path(left["out"]).is_file()
    assert (Path(left["staging"]) / "app-log-so-far.txt").is_file()
    assert UID not in rec_path.read_text()                         # (masked there too)
    FJ.forget_write(env.journal.dir)
    assert FJ.unfinished_write(env.journal.dir) is None
    del os, signal


def test_h1_restore_runs_without_death_signal_and_into_files(env, monkeypatch):
    from fm1companion.tools import proc
    seen = []
    orig = proc.popen

    def spy(argv, survive=False, **kw):
        seen.append((argv[1], survive, kw.get("stdout"), "preexec_fn" in kw))
        p = orig(argv, survive=survive, **kw)
        seen[-1] += (p in proc._LIVE,)
        return p
    monkeypatch.setattr(proc, "popen", spy)
    old = make_backup(env)
    change_flash(env)
    env.set(mode="download")
    restore(env, old.path)
    r = [x for x in seen if x[0] == "restore"]
    assert r and r[0][1] is True and hasattr(r[0][2], "fileno") and r[0][3] is False and r[0][4] is False
    assert all(x[1] is False for x in seen if x[0] != "restore")


def test_h1_record_and_on_writing_come_before_yes_and_the_record_ends_verified(env):
    old = make_backup(env)
    change_flash(env)
    env.set(mode="download")
    at = []

    def on_writing(rec):
        at.append((FJ.WRITING.is_set(), env.get().get("answers", []), json.loads(
            FJ.record_file(env.journal.dir).read_text())["state"]))
    FJ.restore(env.job(on_writing=on_writing), old.path, lambda plan: True)
    assert at == [(True, [], "writing")]                           # before M-Flasher had its answer
    assert json.loads(FJ.record_file(env.journal.dir).read_text())["state"] == "verified"
    assert not FJ.WRITING.is_set() and FJ.unfinished_write(env.journal.dir) is None


def test_m1_a_foreign_image_under_this_units_id_is_refused_by_its_factory_sector(env):
    old = make_backup(env)
    img = bytearray(old.path.read_bytes())
    img[0xFF100:0xFF110] = b"\x11" * 16                            # another unit's calibration / Bluetooth address
    foreign = old.path.with_name("foreign.bin")
    foreign.write_bytes(bytes(img))
    side = json.loads(old.path.with_suffix(".json").read_text())
    side.update(sha256=sha(bytes(img)), file=foreign.name, crc32=f"{__import__('zlib').crc32(bytes(img)):08x}")
    foreign.with_suffix(".json").write_text(json.dumps(side))
    before = change_flash(env)
    env.set(mode="download", calls=[])
    with pytest.raises(FJ.Refused) as e:
        restore(env, foreign)
    assert e.value.kind == "factory" and not calls(env, "restore")
    nothing_written(env, before)


def test_m1_a_blank_factory_sector_takes_the_images_with_exactly_that_warning(env):
    old = make_backup(env)
    img = bytearray(env.flash())
    img[0xFF000:] = b"\xff" * 0x1000                               # this unit's factory data is blank
    img[0x5000] ^= 1
    Path(str(env.state) + ".flash").write_bytes(bytes(img))
    env.set(mode="download")
    plans = []
    res = FJ.restore(env.job(), old.path, lambda plan: plans.append(plan) or True)
    assert res.written and plans[0].factory_blank and env.flash() == old.path.read_bytes()
    # M-Flasher's factory WARNING is accepted only where the app expects it
    gate = FJ.restore_gate({"unit_id": UID, "flash_id": 0x856014, "chip_key": 0x980F, "runs": [(0xFF000, 0x100000)],
                            "factory_blank": False})
    out = (INFO.format(uid=UID) + "\nRestore a backup\nWrites 1 sectors (4 KiB): 0xFF000-0xFFFFF\n" + mp.WHOLE_FLASH_NOTE
           + "\nWARNING: " + mp.FACTORY_WARNING + "\n" + mp.PROMPT)
    assert gate(out).startswith("M-Flasher warns")


def test_m1_the_fake_prints_m_flashers_factory_warning_for_the_same_unit(env):
    old = make_backup(env)
    img = bytearray(env.flash())
    img[0xFF200] ^= 0xFF                                           # (not blank, just different)
    Path(str(env.state) + ".flash").write_bytes(bytes(img))
    env.set(mode="download")
    gate_out = []
    orig = FJ.restore_gate

    def spy(expect):
        g = orig(expect)
        return lambda out: gate_out.append(out) or g(out)
    FJ.restore_gate = spy
    try:
        with pytest.raises(FJ.Refused) as e:                        # (the app's own guard is first)
            restore(env, old.path)
    finally:
        FJ.restore_gate = orig
    assert e.value.kind == "factory" and not gate_out
    import subprocess
    out = subprocess.run([str(env.tmp / "tools-bin" / mf.mflasher_relpath(env.data["tools"]["m-flasher"]["version"],
                                                                           ARCH)),
                          "restore", str(old.path), "--device", "/dev/sg1", "--backup-dir", str(env.tmp / "x")],
                         input="", capture_output=True, text=True, timeout=30).stdout
    assert "WARNING: " + mp.FACTORY_WARNING in out and "Nothing written." in out


def test_m2_the_image_swapped_during_the_dialog_is_not_what_is_written(env):
    old = make_backup(env)
    original = old.path.read_bytes()
    change_flash(env)
    env.set(mode="download")

    def swap(plan):
        b = bytearray(original)
        b[0x5000:0x5010] = b"\x77" * 16                            # a sector that differs anyway
        old.path.write_bytes(bytes(b))
        side = json.loads(old.path.with_suffix(".json").read_text())
        side["sha256"] = sha(bytes(b))
        old.path.with_suffix(".json").write_text(json.dumps(side))
        return True
    res = FJ.restore(env.job(), old.path, swap)
    assert res.written and res.verified and env.flash() == original
    rc = calls(env, "restore")[-1]
    assert rc[1] != str(old.path) and Path(rc[1]).name == "image.bin"


@pytest.mark.parametrize("card,own,ok", [("FM-1", "FM-1 MIDI 1", True), ("Felucca", "Felucca MIDI 1", True),
                                          ("FM-1 SLOOP 3", "FM-1 SLOOP 3 MIDI 1", True),
                                          ("SMK-37 Elite", "SMK-37 Elite MIDI 1", False),
                                          ("FM-1 Update", "FM-1 Update MIDI 1", False), ("FM-1", "", False),
                                          ("FM-1", "Felucca MIDI 1", False), ("Some Synth", "Some Synth", False)])
def test_m4_soft_key_target(card, own, ok):
    assert FJ.softkey_target_ok(card, own) is ok


@pytest.mark.parametrize("name,own", [("SMK-37 Elite", "SMK-37 Elite MIDI 1"), ("FM-1", ""),
                                      ("FM-1 Update", "FM-1 Update MIDI 1")])
def test_m4_no_soft_key_to_a_unit_the_app_did_not_recognise(env, name, own):
    env.set(mode="midi", midi_name=name)
    with pytest.raises(FJ.Refused) as e:
        FJ.backup(env.job(softkey_for=own))
    assert e.value.kind == "guide" and not calls(env, "download-mode") and env.get()["mode"] == "midi"


def test_m4_softkey_port_from_the_apps_device_state():
    from types import SimpleNamespace as NS
    from fm1companion.ui.flash_tools import softkey_port
    port = lambda **kw: NS(**{"label": "FM-1 MIDI 1", "usb_id": "4c4a:c755", "update_mode": False, **kw})  # noqa: E731
    ctx = lambda p, **kw: NS(**{"port": p, "is_virtual": False, "mboot": None, "protocol": "generic",  # noqa: E731
                                "sloop3": None, **kw})
    assert softkey_port(ctx(port())) == "FM-1 MIDI 1"                              # original firmware
    assert softkey_port(ctx(port(usb_id="1209:0001", label="Felucca"), protocol="felucca")) == "Felucca"
    assert softkey_port(ctx(port(usb_id="1209:0001", label="FM-1 SLOOP 3"), sloop3=object())) == "FM-1 SLOOP 3"
    assert softkey_port(ctx(port(usb_id="1209:0001", label="X"))) == ""            # unknown firmware
    assert softkey_port(ctx(port(usb_id="3654:7756"), protocol="felucca")) == ""   # FM1 Open
    assert softkey_port(ctx(port(update_mode=True))) == ""
    assert softkey_port(ctx(port(usb_id="1209:0002", label="FM-1 Update"), mboot=object())) == ""
    assert softkey_port(ctx(port(), is_virtual=True)) == "" and softkey_port(ctx(None)) == ""


def test_m4_a_refusal_after_the_apps_download_mode_restarts_the_unit(env):
    env.prof.unit_id = OTHER_UID                                   # refused after download-mode (another unit)
    with pytest.raises(FJ.Refused) as e:
        FJ.backup(env.job())
    assert e.value.kind == "other-profile" and calls(env, "download-mode") and calls(env, "restart")
    assert env.get()["mode"] == "midi"


INFO = ("  model       FM-1\n  bootloader  stock (M-VAVE)\n  firmware    Felucca\n  app         OK\n"
        "  chip key    0x980F\n  flash       0x856014\n  unit id     {uid}\n  loader      M-Flasher 1.3\n"
        "    - the firmware uses the FM-1 flash layout\n")


def test_l1_gate_rejects_unknown_notes_a_second_plan_line_and_stray_lines():
    expect = {"unit_id": UID, "flash_id": 0x856014, "chip_key": 0x980F, "runs": [(0, 0x1000)]}
    gate = FJ.restore_gate(expect)
    plan = "\nRestore a backup\nWrites 1 sectors (4 KiB): 0x00000-0x00FFF\n" + mp.WHOLE_FLASH_NOTE + "\n"
    good = INFO.format(uid=UID) + plan + mp.PROMPT
    assert gate(good) == ""
    for bad in (good.replace(mp.WHOLE_FLASH_NOTE, mp.WHOLE_FLASH_NOTE + "\nThis unit keeps its bootloader (x)."),
                good.replace(mp.WHOLE_FLASH_NOTE, mp.WHOLE_FLASH_NOTE + "\nSomething new."),
                good.replace(mp.WHOLE_FLASH_NOTE, mp.WHOLE_FLASH_NOTE + "\nWrites 1 sectors (4 KiB): 0xFF000-0xFFFFF"),
                good.replace("\nRestore a backup", "\nSomething before the title\nRestore a backup")):
        assert gate(bad) != "", bad


def test_l3_m_flasher_failing_before_any_erase_is_reported_as_nothing_written(env):
    old = make_backup(env)
    before = change_flash(env)
    env.set(mode="download", fault_restore="backup-fails")
    with pytest.raises(FJ.Refused) as e:
        restore(env, old.path)
    assert e.value.kind == "not-started" and not e.value.written and not isinstance(e.value, FJ.WriteFailed)
    assert env.flash() == before and FJ.unfinished_write(env.journal.dir) is None


def test_l4_a_profile_learns_nothing_from_a_unit_it_did_not_send_into_download_mode(env):
    env.set(mode="download")
    res = FJ.backup(env.job())                                     # (profile "Unit 1" active, unit already there)
    assert env.prof.unit_id == "" and env.prof.flash_id == "" and res.path.parent.parent.name == FJ.UNKNOWN_FOLDER


def test_l5_the_backup_folder_is_synced(tmp_path, monkeypatch):
    import os
    import stat
    synced = []
    real = os.fsync

    def spy(fd):
        synced.append(stat.S_ISDIR(os.fstat(fd).st_mode))
        return real(fd)
    monkeypatch.setattr(os, "fsync", spy)
    flashdump.write_backup(tmp_path / "Flash", "x.bin", b"\0" * 4096, device="d", read_through="t", jedec=0x856014)
    assert True in synced


def test_l7_a_killed_restore_does_not_mark_an_update_failed(env):
    marked = []
    c = st.Candidate("m-flasher", "0.13.0", "update", None, "0" * 64)
    env.store.mark = lambda *a, **k: marked.append((a, k))
    runner._judge(env.store, c, runner.RunResult("0.13.0", "update", ["restore", "/x/a.bin"], -9))
    assert marked == []
    runner._judge(env.store, c, runner.RunResult("0.13.0", "update", ["info", "--device", "/dev/sg1"], -9))
    assert marked and marked[0][1].get("failed")


def test_l8_output_is_decoded_as_a_stream(env):
    env.set(mode="download", fault_info="split-utf8")
    r, info = runner.info("/dev/sg1", store=env.store, log_dir=env.logs, journal=env.journal)
    assert r.ok and info is not None and info.unit_id == "" and "�" not in r.out



# ------------------------------------------------------------------------------------------------ re-review (N1–N9)

def test_n1_children_start_with_the_default_sigterm(tmp_path):
    """the app may ignore SIGTERM while M-Flasher writes; its children must not inherit that"""
    import signal
    import subprocess
    from fm1companion.tools import proc
    old = signal.signal(signal.SIGTERM, signal.SIG_IGN)
    try:
        for survive in (False, True):
            p = proc.popen(["sh", "-c", "grep SigIgn /proc/self/status"], survive=survive, stdout=subprocess.PIPE)
            out, _e = p.communicate(10)
            proc.forget(p)
            mask_ = int(out.split()[1], 16)
            assert not mask_ & (1 << (signal.SIGTERM - 1)), survive
    finally:
        signal.signal(signal.SIGTERM, old)


def test_n1_sigterm_is_ignored_only_while_writing():
    import os
    import signal
    from fm1companion.ui import flash_tools
    seen = []
    old = signal.signal(signal.SIGTERM, signal.SIG_DFL)
    try:
        FJ.WRITING.set()
        flash_tools._sigterm(signal.SIGTERM, None)                 # (while writing: nothing happens)
        FJ.WRITING.clear()
        real_kill = os.kill
        os.kill = lambda pid, sig: seen.append(sig)
        try:
            flash_tools._sigterm(signal.SIGTERM, None)             # otherwise: the default action
        finally:
            os.kill = real_kill
        assert seen == [signal.SIGTERM] and signal.getsignal(signal.SIGTERM) == signal.SIG_DFL
    finally:
        FJ.WRITING.clear()
        signal.signal(signal.SIGTERM, old)


def test_n2_m_flashers_files_live_in_a_private_local_folder(env, monkeypatch):
    seen = []
    orig = runner.run_mflasher

    def spy(args, **kw):
        seen.append((args, kw.get("out_dir")))
        return orig(args, **kw)
    monkeypatch.setattr(runner, "run_mflasher", spy)
    old = make_backup(env)
    change_flash(env)
    env.set(mode="download")
    restore(env, old.path)
    dirs = [Path(a[a.index("--backup-dir") + 1]) for a, _o in seen if "--backup-dir" in a]
    dirs += [o for _a, o in seen if o is not None]
    assert dirs and all(d.parent == env.tmp / "work" for d in dirs)
    assert all(not str(d).startswith(str(env.backups)) for d in dirs)
    assert (env.tmp / "work").stat().st_mode & 0o077 == 0 or all(d.stat().st_mode & 0o077 == 0 for d in dirs
                                                                 if d.exists())


def test_n2_private_dir_is_local_and_private(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "rt"))
    (tmp_path / "rt").mkdir()
    d = FJ.private_dir()
    assert d.parent.parent == tmp_path / "rt" and d.stat().st_mode & 0o777 == 0o700
    monkeypatch.setattr(FJ, "_fs_type", lambda p: "nfs4" if str(p).startswith(str(tmp_path / "rt")) else "ext4")
    from fm1companion import config
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    assert FJ.private_dir() == tmp_path / "cache" / "m-flasher-work"


def test_n2_too_little_free_space_refuses_before_yes(env, monkeypatch):
    old = make_backup(env)
    before = change_flash(env)
    env.set(mode="download")
    monkeypatch.setattr(FJ, "_free_enough", lambda d: False)
    with pytest.raises(FJ.Refused) as e:
        restore(env, old.path)
    assert e.value.kind == "space" and not calls(env, "restore")
    nothing_written(env, before)


def test_n3_a_reused_pid_is_not_still_writing(env):
    import os
    FJ._save_record(FJ.record_file(env.journal.dir), {"state": "writing", "pid": os.getpid(), "pid_start": "1"})
    rec = FJ.unfinished_write(env.journal.dir)
    assert rec is not None and rec["alive"] is False                # (this pid lives, but not since then)
    from fm1companion.flash import journal as fj_
    FJ._save_record(FJ.record_file(env.journal.dir), {"state": "writing", "pid": os.getpid(),
                                                      "pid_start": fj_._started(os.getpid())})
    assert FJ.unfinished_write(env.journal.dir)["alive"] is True


def test_n4_a_nothing_to_write_rerun_closes_the_record(env):
    old = make_backup(env)
    FJ._save_record(FJ.record_file(env.journal.dir), {"state": "writing", "pid": 0, "profile": env.prof.id,
                                                      "unit_sha256": sha(UID.encode()),
                                                      "image_sha256": sha(old.path.read_bytes())})
    env.set(mode="download")
    res, _s = restore(env, old.path)
    assert res.note == "identical" and FJ.unfinished_write(env.journal.dir) is None


def test_n5_guided_backup_at_the_profiles_port_names_and_teaches_the_profile(env, monkeypatch):
    env.set(mode="download")                                       # (put into download mode by hand)
    monkeypatch.setattr(FJ, "_usb_path_of", lambda dev: "1-1.2")
    res = FJ.backup(env.job(usb_path="1-1.2"))
    assert res.profile is env.prof and res.path.parent == env.backups / "Unit 1" / "Flash"
    assert env.prof.unit_id == UID and not calls(env, "download-mode") and not res.restarted
    # … and so its image can be written back (an FM1 Open / unknown-firmware unit has only this path)
    change_flash(env)
    env.set(mode="download")
    r, _s = restore(env, res.path, usb_path="", softkey_for="")
    assert r.written and r.verified


def test_n5_another_port_names_nothing(env, monkeypatch):
    env.set(mode="download")
    monkeypatch.setattr(FJ, "_usb_path_of", lambda dev: "1-1.3")
    res = FJ.backup(env.job(usb_path="1-1.2"))
    assert res.profile is None and env.prof.unit_id == ""


def test_n5_unknown_unit_hint_says_how_to_teach_it(env):
    old = make_backup(env)
    env.prof.unit_id = ""
    env.profiles.profiles.clear()
    env.set(mode="download")
    with pytest.raises(FJ.Refused) as e:
        restore(env, old.path, profile=None)
    assert e.value.kind == "unknown-unit" and "make a flash backup with it first" not in e.value.reason


def test_n6_soft_key_needs_the_usb_id():
    from types import SimpleNamespace as NS
    from fm1companion.ui.flash_tools import softkey_port
    c = NS(port=NS(label="Felucca", usb_id="", update_mode=False), is_virtual=False, mboot=None, protocol="felucca",
           sloop3=None)
    assert softkey_port(c) == ""                                   # (BLE / virtual cable: no USB id)
    c.port.usb_id = "1209:0001"
    assert softkey_port(c) == "Felucca"


def test_n7_long_after_yes_the_app_may_close_never_kill(env, monkeypatch):
    old = make_backup(env)
    change_flash(env)
    env.set(mode="download", fault_restore="slow-write")
    monkeypatch.setattr(runner, "WRITE_SOFT_LIMIT", 0.3)
    told = []
    res = FJ.restore(env.job(on_overdue=lambda: told.append(FJ.OVERDUE.is_set())), old.path, lambda plan: True)
    assert told == [True] and res.written and res.verified and not FJ.OVERDUE.is_set()


def test_n8_a_rerun_needs_the_recorded_file(env):
    old = make_backup(env)
    rec = {"image": str(old.path), "image_sha256": sha(old.path.read_bytes()), "pre_backup": str(old.path),
           "pre_sha256": "0" * 64}
    assert FJ.rerun_problem(rec, "image") == "" and FJ.rerun_problem(rec, "before") != ""
    old.path.write_bytes(b"x" + old.path.read_bytes()[1:])
    assert FJ.rerun_problem(rec, "image") != ""


def test_n9_the_units_own_changed_factory_data_is_named_accurately(env):
    old = make_backup(env)
    img = bytearray(env.flash())
    img[0xFF800:] = b"\xff" * 0x800                                # (an interrupted program: second half erased)
    Path(str(env.state) + ".flash").write_bytes(bytes(img))
    env.set(mode="download")
    with pytest.raises(FJ.Refused) as e:
        restore(env, old.path)
    assert e.value.kind == "factory-partial" and "not this unit's backup" not in e.value.reason
    img[0xFF000:] = old.path.read_bytes()[0xFF000:]
    img[0xFF010] ^= 0x01                                           # (one bit rotted)
    Path(str(env.state) + ".flash").write_bytes(bytes(img))
    with pytest.raises(FJ.Refused) as e:
        restore(env, old.path)
    assert e.value.kind == "factory" and "not this unit's backup" not in e.value.reason


# ------------------------------------------------------------------------------------------------ hardware run 2026-11-20

def _mflasher_own_json(path: Path, img: bytes, uid: str, flash: str = "0x856014"):
    """M-Flasher's own .json, as src/ops.rs save_backup writes it (synthetic values)"""
    path.with_suffix(".json").write_text(
        "{\n" + "".join(f'  "{k}": "{v}",\n' for k, v in (
            ("model", "FM-1"), ("bootloader", "stock (M-VAVE)"), ("firmware", "Felucca"), ("app", "OK"),
            ("chip key", "0x980F"), ("flash", flash), ("unit id", uid), ("loader", "M-Flasher 1.3, CPU at 96 MHz")))
        + f'  "sha256": "{sha(img)}",\n  "saved": "20000101-000000",\n  "tool": "m-flasher 0.12.1"\n}}\n')


def test_hw4_m_flashers_own_json_is_accepted_under_the_same_gates(env):
    old = make_backup(env)
    img = old.path.read_bytes()
    own = old.path.with_name("fm1-20000101-000000.bin")
    own.write_bytes(img)
    _mflasher_own_json(own, img, UID)
    side = FJ.check_image(own)
    assert side["unit id"] == UID and flashdump.side_flash_id(side) == 0x856014 and side["format"] == "m-flasher"
    change_flash(env)
    env.set(mode="download")
    res, plans = restore(env, own)
    assert res.written and res.verified and env.flash() == img and plans[0].image_date.startswith("2000-01-01")
    # (listed on the Backups page as checked, too)
    fb = flashdump.describe(own)
    assert fb.ok and fb.unit_id == UID


def test_hw4_an_unknown_sidecar_is_named_as_such_never_damaged(env):
    old = make_backup(env)
    old.path.with_suffix(".json").write_text(json.dumps({"something": "else"}))
    with pytest.raises(FJ.Refused) as e:
        FJ.check_image(old.path)
    from fm1companion.i18n import _
    assert e.value.kind == "image-damaged"
    assert "M-Flasher" in e.value.reason and "damaged" not in e.value.reason and "beschädigt" not in e.value.reason
    old.path.with_suffix(".json").unlink()
    with pytest.raises(FJ.Refused) as e:
        FJ.check_image(old.path)
    assert "damaged" not in e.value.reason and "beschädigt" not in e.value.reason
    del _


def test_hw5_a_known_profiles_foreign_image_is_refused_before_download_mode(env):
    old = make_backup(env)
    env.prof.unit_id = OTHER_UID                                   # (the active profile knows its own unit id)
    env.set(mode="midi", calls=[])
    with pytest.raises(FJ.Refused) as e:
        restore(env, old.path)
    assert e.value.kind == "other-unit" and env.get()["calls"] == [] and env.get()["mode"] == "midi"


def test_hw6_the_inhibitor_is_active_before_yes_and_released_after_verify(env):
    old = make_backup(env)
    change_flash(env)
    env.set(mode="download")
    events = []

    class H:
        def __init__(self, why):
            self.why = why

        def release(self):
            events.append(("release", self.why, len(env.get().get("answers", [])),
                           len([c for c in env.get()["calls"] if c[0] == "backup"])))

    def inhibit(why, pid):
        events.append(("start", why, len(env.get().get("answers", [])), len(calls(env, "restore"))))
        return H(why)
    FJ.restore(env.job(inhibit=inhibit), old.path, lambda plan: True)
    starts = [e for e in events if e[0] == "start"]
    assert starts[0][1:] == ("planned", 0, 0)                      # before M-Flasher's restore even starts
    assert starts[1][1] == "writing" and starts[1][2] == 0          # bound to M-Flasher, still before its answer
    rel = [e for e in events if e[0] == "release"]
    assert rel and rel[0][1] == "planned" and rel[0][2] == 1        # released after "yes" …
    assert rel[0][3] == len(calls(env, "backup"))                   # … and after the verify read


def test_hw8_one_sector_is_singular():
    from types import SimpleNamespace as NS
    from fm1companion.ui.flash_tools import plan_text
    from fm1companion.i18n import ngettext
    plan = NS(revision="old", image_date="", unit_name="Unit 1", unit_id=UID, image=Path("a.bin"), image_firmware="",
              sectors=1, boot_area=False, backup=Path("b.bin"))
    one = ngettext("{n} sector of 4 KiB differs from what the FM-1 holds now{boot}.",
                   "{n} sectors of 4 KiB differ from what the FM-1 holds now{boot}.", 1).format(n=1, boot="")
    assert one in plan_text(plan)
    plan.sectors = 3
    assert ngettext("{n} sector of 4 KiB differs from what the FM-1 holds now{boot}.",
                    "{n} sectors of 4 KiB differ from what the FM-1 holds now{boot}.", 3).format(n=3, boot="") \
        in plan_text(plan)


def test_hw7_the_job_dialog_has_no_cancel_once_done():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from fm1companion.ui.common import JobDialog
    d = JobDialog(None, "T", lambda w: 1, cancellable=True, actions=[("Open", lambda r: None)])
    assert d.cancel_button in d.buttons.buttons()
    d._done(1)
    app.processEvents()
    assert d.cancel_button not in d.buttons.buttons() and not d.cancel_button.isEnabled()
    d.deleteLater()


def test_hw2_the_unit_coming_back_after_a_flash_job_is_the_jobs_unit(env, monkeypatch):
    monkeypatch.setattr(FJ, "_usb_path_of", lambda dev: "3-1.3.1")
    job = env.job()
    FJ.backup(job)
    assert job.unit_usb == "3-1.3.1"
    from types import SimpleNamespace as NS
    from fm1companion.ui.flash_tools import remember_unit
    noted = []
    remember_unit(NS(note_installed=lambda p, usb: noted.append((p, usb))), job)
    assert noted == [(env.prof, "3-1.3.1")]                         # (no "Which FM-1 is this?" when it comes back)


def test_hw3_the_ambiguous_question_preselects_nothing(monkeypatch):
    pytest.importorskip("PyQt6.QtWidgets")
    from types import SimpleNamespace as NS
    from PyQt6.QtWidgets import QInputDialog
    from fm1companion.ui.device import DeviceContext
    a, b = devices.Profile(id="aaaaaaaaaa", name="Unit 1"), devices.Profile(id="bbbbbbbbbb", name="Unit 2")
    seen = []

    def get_item(parent, title, text, names, cur, editable):
        seen.append((list(names), cur))
        return names[cur], True                                    # (the user just presses OK)
    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(get_item))
    me = NS(_asking=False, window=None, settings=NS(last_device="bbbbbbbbbb"), _new_profile=lambda n="": None)
    assert DeviceContext._ask_profile(me, [a, b], b) is None         # OK on the placeholder: nothing assumed
    names, cur = seen[0]
    assert cur == 0 and "Unit" not in names[0] and names[1].startswith("Unit 1")
    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *x, **k: (x[3][2], True)))
    assert DeviceContext._ask_profile(me, [a, b], None) is b          # an explicit choice still works


def test_hw10_download_mode_on_purpose_is_not_the_emergency(env, monkeypatch):
    from fm1companion.flash.uboot import UbootDevice
    from fm1companion.ui.pages import recovery
    FJ.INTENDED.clear()
    FJ.GUIDE_SHOWN.clear()
    dev = UbootDevice(sg="/dev/sg1", block="", size=0, usb_id="4c4a:8057", vendor="WL82", model="UBOOT1.00",
                      usb_path="3-1.3.1", product="WL80UBOOT1.00")
    assert recovery.intended_text(dev) == ""                         # (not known: the emergency text applies)
    monkeypatch.setattr(FJ, "_usb_path_of", lambda d: "3-1.3.1")
    env.set(mode="midi")
    FJ.backup(env.job(), restart_after=False)                       # (the app sent it there and left it)
    assert FJ.intended_download_mode("3-1.3.1") == "sent" and "WL80UBOOT1.00" in recovery.intended_text(dev)
    FJ.INTENDED.clear()
    env.set(mode="none")
    with pytest.raises(FJ.Refused):
        FJ.backup(env.job())                                       # (guide shown: keys or m-bootloader's menu)
    assert FJ.intended_download_mode("9-9") == "guide"
    FJ.INTENDED.clear()
    FJ.GUIDE_SHOWN.clear()


def test_hw11_known_devices_say_last_seen_with_a_date():
    from types import SimpleNamespace as NS
    from fm1companion.ui.overview import known_firmware_text
    from fm1companion.ui.device import last_firmware_text
    import time as _t
    p = NS(firmware="skiff", firmware_version="v2.7", last_seen=_t.mktime((2026, 11, 19, 12, 0, 0, 0, 0, -1)))
    live, other = known_firmware_text(p, live=True), known_firmware_text(p, live=False)
    assert live == last_firmware_text(p) and other != live and last_firmware_text(p) in other and "19.11.2026" in other
