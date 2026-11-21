# SPDX-License-Identifier: GPL-3.0-only
"""Step 4, item 2: SLOOP 3 over m-bootloader's MIDI MODE (fm1companion/tools/mbinstall.py).

* The package: the Python port of SLOOP 3's web/fwpkg.js (v3.4.0-beta) must build byte for byte what the page's own
  JavaScript builds. Recorded here: the SHA-256 node computed with the page's fwpkg.js for the bundled 3.4.0 area and
  four chip keys (run when the pins were made); with FM1C_SLOOP3_SRC pointing at a checkout of the tag (and node on
  the PATH) the JavaScript runs again, on random areas too.
* The install against tests/fake_mboot.py (the specification, written again) with its failure modes, and – with
  FM1C_MBOOT_SRC pointing at a checkout of m-bootloader v2.4 and a C compiler – against m-bootloader's own MIDI MODE
  code (src/midi_update.c, src/core.c) compiled for the PC around tests/mbsim.c.

Synthetic values only: the fakes' unit id is a0a1a2a3a4a5a6a7, their flash pseudo-random."""
from __future__ import annotations

import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fm1companion import hardware  # noqa: E402
from fm1companion.flash import mboot  # noqa: E402
from fm1companion.tools import bootarea, flashjob as FJ, mbinstall as M  # noqa: E402
from fake_mboot import FakeMboot  # noqa: E402
from test_tools_flash import env  # noqa: E402,F401

AREA_FILE = ROOT / "fm1companion" / "tools" / "bundled" / "sloop3" / "sloop3-3.4.0.area"
BOOT = (ROOT / "fm1companion" / "tools" / "bundled" / "m-bootloader" / "fm1-uboot.boot").read_bytes()
MB_UID = "a0a1a2a3a4a5a6a7"
# sha256 of fwpkg.js buildPackage(AREA, key).pkg – computed with node and the page's own fwpkg.js at v3.4.0-beta
NODE_PACKAGES = {0x980F: "c9cd8b85701ff4307481dcb201e0d983591e16277cef27b65198740a353a9fa5",
                 0x1234: "efed127dd3c1f87549e25a56d2373427c34d20bc429102d07a912c9a22d4e8a1",
                 0x0000: "6d22d4a1610f6c4f3db52d1bbe48d3c50508af342879a521c2f41bdd86f5877a",
                 0xFFFF: "9af924197195f63f0420e75ffa10f4bdf5860a504871004f01811a191f26b044"}


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def small_area(seed="synthetic-sloop3", n=0x9100) -> bytes:
    """a synthetic application area of the mkfw.py form: two records with valid CRC-16s, the rest random"""
    a = bytearray(random.Random(seed).randbytes(n))
    for at in (0, 0x20):
        a[at:at + 2] = M.crc16(a[at + 2:at + 32]).to_bytes(2, "little")
    return bytes(a)


def mboot_flash(seed="fm1c-fake-flash") -> bytes:
    import fake_mflasher
    img = bytearray(fake_mflasher.synthetic_image(seed))
    img[:0x4000] = fake_mflasher.short_boot_area(bytes(img), BOOT)
    return bytes(img)


# ------------------------------------------------------------------------------------------------ the package

def test_the_package_is_byte_for_byte_the_one_the_pages_javascript_builds():
    area = AREA_FILE.read_bytes()
    assert len(area) == 474706 and M.check_area(area) == ""
    for key, want in NODE_PACKAGES.items():
        p = M.build_package(area, key)
        assert p.size == 0x78000 and sha(p.pkg) == want and M.check_package(p, key) == ""
        assert p.flash[:0x4000] == b"\xff" * 0x4000                  # (never written: m-bootloader starts at 0x4000)


@pytest.mark.skipif(not (os.environ.get("FM1C_SLOOP3_SRC") and shutil.which("node")),
                    reason="needs FM1C_SLOOP3_SRC (a checkout of isod89/sloop3-fm1 at v3.4.0-beta) and node")
def test_the_port_against_the_pages_own_fwpkg_js(tmp_path):
    js = Path(os.environ["FM1C_SLOOP3_SRC"]) / "web" / "fwpkg.js"
    script = tmp_path / "run.js"
    script.write_text("const fs=require('fs');const m=require(process.argv[2]);const a=fs.readFileSync(process.argv[3]);"
                      "const o=process.argv[5]?fs.readFileSync(process.argv[5]):undefined;"
                      "const p=m.buildPackage(a,parseInt(process.argv[4],16),o);"
                      "fs.writeFileSync(process.argv[6],Buffer.from(p.pkg));console.log(p.size,m.checkPackage(p,"
                      "parseInt(process.argv[4],16))||'ok');")
    cases = [(AREA_FILE.read_bytes(), 0x980F, None)] + [
        (small_area(f"r{i}", n), k, (random.Random(i).randbytes(300) if i % 2 else None))
        for i, (n, k) in enumerate([(0x1500, 0x0001), (0x4FFF, 0xBEEF), (0x5000, 0x7FFF), (0x20001, 0x8000)])]
    for i, (area, key, ota) in enumerate(cases):
        (tmp_path / "a").write_bytes(area)
        if ota:
            (tmp_path / "o").write_bytes(ota)
        out = subprocess.run(["node", str(script), str(js), str(tmp_path / "a"), f"{key:x}",
                              str(tmp_path / "o") if ota else "", str(tmp_path / f"p{i}")],
                             capture_output=True, text=True, timeout=60)
        assert out.returncode == 0, out.stderr
        mine = M.build_package(area, key, ota)
        assert (tmp_path / f"p{i}").read_bytes() == mine.pkg
        assert out.stdout.split()[1] == (M.check_package(mine, key) or "ok")


def test_check_package_refuses_what_m_bootloader_refuses():
    area = small_area()
    p = M.build_package(area, 0x980F)
    assert M.check_package(p, 0x980F) == ""
    assert M.check_package(p, 0x980E) == "key"
    bad = bytearray(p.pkg)
    bad[0x400 + 0x5000] ^= 1
    assert M.check_package(M.Package(bytes(bad), p.size), 0x980F) == "crc"
    bad = bytearray(p.pkg)
    bad[5] ^= 1
    assert M.check_package(M.Package(bytes(bad), p.size), 0x980F) == "header"
    bad = bytearray(p.pkg)
    bad[0x41] ^= 1
    assert M.check_package(M.Package(bytes(bad), p.size), 0x980F) in ("list", "header")
    assert M.check_package(M.build_package(small_area(n=0x80), 0x980F), 0x980F) == "entry"      # (too small)


def test_check_area_and_the_bundled_pin(monkeypatch):
    assert M.check_area(b"\0" * 0x80) == "size"
    a = bytearray(small_area())
    a[0x21] ^= 1
    assert M.check_area(bytes(a)).startswith("record")
    data, ver = M.bundled_area()
    assert ver == "3.4.0" and sha(data) == sha(AREA_FILE.read_bytes())
    from fm1companion.tools import store as st
    s = st.Store()
    c = s.bundled("sloop3")
    monkeypatch.setattr(st.Store, "bundled", lambda self, key: type(c)(**{**c.__dict__, "sha256": "0" * 64}))
    with pytest.raises(M.InstallError) as e:
        M.bundled_area(st.Store())
    assert e.value.kind == "area-checksum"


# ------------------------------------------------------------------------------------------------ the install

@pytest.fixture
def unit(env):
    """the fake MIDI MODE unit (m-bootloader in its boot area), the env's profile knows its unit id and chip"""
    env.prof.unit_id = MB_UID
    hardware.note_flash_id(env.prof, 0x856014, "identity")
    env.fake = FakeMboot(mboot_flash(), install=True, version="2.4-395e6cc")
    return env


def go(env, area=None, plans=None, confirm=True, **kw):
    plans = plans if plans is not None else []

    def ok(plan):
        plans.append(plan)
        return confirm
    new_chip_ok = kw.pop("new_chip_ok", False)
    return M.install(env.job(**kw), env.fake, ok, area=area if area is not None else small_area(), version="3.4.0",
                     new_chip_ok=new_chip_ok)


def untouched(env, before: bytes):
    assert env.fake.flash == before and not env.fake.erased


def test_install_writes_only_from_0x4000_to_the_package_end_after_the_double_read(unit):
    env = unit
    before = env.fake.flash
    area = small_area()
    plans = []
    res = go(env, area, plans)
    pkg = M.build_package(area, 0x980F)
    assert res.written and res.finished and env.fake.finished == 1 and env.fake.installs == 1
    assert env.fake.flash == M.expected_flash(pkg, before) == res.expected
    assert env.fake.flash[:0x4000] == before[:0x4000] and env.fake.flash[pkg.size:] == before[pkg.size:]
    assert min(env.fake.erased) == 0x4000 and max(env.fake.erased) < pkg.size
    assert env.fake.writes == env.fake.restarts == env.fake.downloads == 0
    assert set(env.fake.commands) <= {0x12, 0x23, 0x24, 0x30}
    p = plans[0]
    assert p.unit_id == MB_UID and p.size == pkg.size and p.version == "3.4.0" and p.revision == "old"
    assert p.runs == M.runs_to_write(pkg, before) and p.backup == res.backup
    side = json.loads(res.backup.with_suffix(".json").read_text())
    assert side["double_read"] and side["unit id"] == MB_UID and res.backup.read_bytes() == before
    rec = json.loads(FJ.record_file(env.journal.dir).read_text())
    assert rec["op"] == "sloop3-install" and rec["state"] == "verified" and MB_UID not in json.dumps(rec)
    log = next(env.logs.glob("mboot-sloop3-*.txt")).read_text()
    assert "FINISH" in log and MB_UID not in log
    assert env.prof.boot_area["kind"] == bootarea.MBOOT
    # again: nothing to write
    env.fake.installs = 0
    res2 = go(env, area)
    assert not res2.written and res2.note == "already" and env.fake.installs == 0


def test_install_of_the_bundled_340_area(unit):
    env = unit
    before = env.fake.flash
    res = M.install(env.job(), env.fake, lambda plan: True)
    pkg = M.build_package(AREA_FILE.read_bytes(), 0x980F)
    assert res.written and res.version == "3.4.0" and env.fake.flash == M.expected_flash(pkg, before)
    area = bytearray(env.fake.flash[0x4000:0x4000 + 474706])
    assert M.enc_area(bytes(area), 0x980F) == AREA_FILE.read_bytes()      # (decrypts to the author's area)


@pytest.mark.parametrize("fail,kind", [("wrong-key", "stopped"), ("silent", "stopped")])
def test_a_refusal_or_silence_before_erasing_writes_nothing(unit, monkeypatch, fail, kind):
    env = unit
    monkeypatch.setattr(M, "REQUEST_TIMEOUT", 0.5)
    env.fake.fail = fail
    before = env.fake.flash
    with pytest.raises(FJ.Refused) as e:
        go(env)
    assert e.value.kind == kind and not e.value.written and not isinstance(e.value, FJ.WriteFailed)
    untouched(env, before)
    assert env.fake.installs == 1 and not FJ.record_file(env.journal.dir).exists()
    assert not FJ.WRITING.is_set()


@pytest.mark.parametrize("fail,kind", [("crc", "stopped-writing"),        # (it stops right at the erase boundary:
                                       ("erase-fail", "stopped-writing"),  #  the app cannot rule an erase out)
                                       ("stop-writing", "stopped-writing")])
def test_a_refusal_or_silence_while_writing_is_reported_with_the_backup(unit, monkeypatch, fail, kind):
    env = unit
    monkeypatch.setattr(M, "REQUEST_TIMEOUT", 0.5)
    env.fake.fail = fail
    before = env.fake.flash
    with pytest.raises(FJ.WriteFailed) as e:
        go(env)
    assert e.value.kind == kind and e.value.backup.read_bytes() == before
    assert env.fake.flash[:0x4000] == before[:0x4000]                 # (the boot area: never)
    rec = FJ.unfinished_write(env.journal.dir)
    assert rec is not None and rec["op"] == "sloop3-install" and rec["state"] == "failed" and not rec["alive"]


@pytest.mark.parametrize("case,kind", [("other-chip", "other-chip"), ("newer", "newer-chip"),
                                       ("unknown-chip", "unknown-chip"), ("ram-stage", "ram-stage"),
                                       ("no-answer", "not-mboot"), ("other-unit", "unknown-unit"),
                                       ("no-profile", "unknown-unit")])
def test_refused_before_anything_is_read_or_sent(unit, case, kind):
    env = unit
    before = env.fake.flash
    kw = {}
    if case == "other-chip":
        env.fake.jedec = 0xB36014                    # (the profile recorded the earlier chip)
    elif case == "newer":
        env.prof.flash_id = ""
        env.fake.jedec = 0xB36014
    elif case == "unknown-chip":
        env.prof.flash_id = ""
        env.fake.jedec = 0x123456
    elif case == "ram-stage":
        env.fake.kind = 2
    elif case == "no-answer":
        env.fake.answer_0x12 = False
    elif case == "other-unit":
        env.prof.unit_id = "13579bdf02468ace"
    elif case == "no-profile":
        kw["profile"] = None
    with pytest.raises(FJ.Refused) as e:
        go(env, **kw)
    assert e.value.kind == kind
    untouched(env, before)
    assert env.fake.installs == 0 and 0x23 not in env.fake.commands and not list(env.backups.rglob("*.bin"))


def test_a_newer_unit_needs_the_hardware_rule_for_both(unit):
    env = unit
    env.prof.flash_id = ""
    env.fake.jedec = 0xB36014
    env.fake.version = "2.4-395e6cc"
    assert hardware.support("m-bootloader", "2.4-395e6cc").status == hardware.YES
    res = go(env, new_chip_ok=True)                                     # (SLOOP 3's row lets it through too)
    assert res.written and env.fake.finished == 1
    env.fake = FakeMboot(mboot_flash(), install=True, jedec=0xB36014, version="2.3-abcdef0")
    env.prof.flash_id = ""
    with pytest.raises(FJ.Refused) as e:
        go(env, new_chip_ok=True)
    assert e.value.kind in ("newer-chip", "mboot-build") and env.fake.installs == 0


def test_a_bad_read_keeps_nothing_and_sends_no_start_key(unit):
    env = unit
    env.fake.corrupt_at = 0x30000
    with pytest.raises(FJ.Refused) as e:
        go(env)
    assert e.value.kind == "read" and env.fake.installs == 0 and not list(env.backups.rglob("*.bin"))


def test_declined_writes_nothing_and_keeps_the_backup(unit):
    env = unit
    before = env.fake.flash
    with pytest.raises(FJ.Refused) as e:
        go(env, confirm=False)
    assert e.value.kind == "declined" and env.fake.installs == 0
    untouched(env, before)
    assert len(list(env.backups.rglob("*.bin"))) == 1


def test_no_m_bootloader_in_the_backup_is_refused(unit):
    import fake_mflasher
    env = unit
    env.fake = FakeMboot(fake_mflasher.synthetic_image(), install=True, version="2.4-395e6cc")   # (stock boot area)
    with pytest.raises(FJ.Refused) as e:
        go(env)
    assert e.value.kind == "not-mboot" and env.fake.installs == 0


def test_writing_is_flagged_only_while_m_bootloader_writes(unit):
    env = unit
    seen = []
    job = env.job(on_writing=lambda rec: seen.append(FJ.WRITING.is_set()))
    M.install(job, env.fake, lambda plan: not FJ.WRITING.is_set(), area=small_area(), version="3.4.0")
    assert seen == [True] and not FJ.WRITING.is_set()


def test_mboot_module_stays_read_only():
    with pytest.raises(mboot.MbootError):
        mboot._frame(0x30)
    assert M.START == (0xF0, 0x22, 0x24, 0x35, 0x7F, 0xF7)
    src = (ROOT / "fm1companion" / "tools" / "mbinstall.py").read_text()
    for never in ("0x21", "0x25", "0x7D", "0x7E"):                    # (erase, run a RAM app, the other keys)
        assert never not in src, never
    assert src.count("0x22") == 1                                      # (only in the start key's manufacturer id)
    assert mboot._unpack(M._answer_frame(bytes(8), b"")[1:-1])[2] == 0x30


# ------------------------------------------------------------------------------------------------ m-bootloader's own code

@pytest.mark.skipif(not (os.environ.get("FM1C_MBOOT_SRC") and shutil.which("cc")),
                    reason="needs FM1C_MBOOT_SRC (a checkout of jvitkauskas/m-bootloader v2.4) and a C compiler")
def test_install_through_m_bootloaders_own_midi_mode_code(env, tmp_path):
    from fm1companion.transport import SubprocessTransport
    src = Path(os.environ["FM1C_MBOOT_SRC"])
    exe = tmp_path / "mbsim"
    cc = subprocess.run(["cc", "-O1", "-std=c11", "-D_DEFAULT_SOURCE", f"-I{src / 'include'}", "-o", str(exe),
                         str(ROOT / "tests" / "mbsim.c"), str(src / "src" / "midi_update.c"),
                         str(src / "src" / "core.c")], capture_output=True, text=True, timeout=120)
    assert cc.returncode == 0, cc.stderr
    before = mboot_flash()
    (tmp_path / "in.bin").write_bytes(before)
    env.prof.unit_id = MB_UID
    t = SubprocessTransport([str(exe), str(tmp_path / "in.bin"), str(tmp_path / "out.bin"), "980f", "856014",
                             "2.4-395e6cc"])
    assert t.wait_started(10)
    try:
        area = AREA_FILE.read_bytes()
        res = M.install(env.job(), t, lambda plan: True, area=area, version="3.4.0")
    finally:
        t.close()
    import time
    for _i in range(50):
        if (tmp_path / "out.bin").exists():
            break
        time.sleep(0.1)
    time.sleep(0.5)
    out = (tmp_path / "out.bin").read_bytes()
    pkg = M.build_package(area, 0x980F)
    assert res.written and res.finished
    assert out == M.expected_flash(pkg, before)
    assert M.enc_area(out[0x4000:0x4000 + len(area)], 0x980F) == area


# ------------------------------------------------------------------------------------------------ review fixes

def test_h1_the_erase_boundary_is_the_last_answer_of_the_check_pass():
    p = M.build_package(small_area(), 0x980F)
    assert not M.Served(flash_bytes=p.size + 31).erasing_possible(p)
    assert M.Served(flash_bytes=p.size + 32).erasing_possible(p)            # (midi_update.c erases right then)


def test_h1_a_failed_first_erase_is_a_failed_write_and_the_record_stays(unit, monkeypatch):
    env = unit
    monkeypatch.setattr(M, "REQUEST_TIMEOUT", 0.5)
    env.fake.fail = "erase-fail"
    before = env.fake.flash
    with pytest.raises(FJ.WriteFailed) as e:
        go(env)
    assert env.fake.erased == [0x4000] and env.fake.flash != before
    assert e.value.backup.read_bytes() == before and e.value.backup.name in e.value.reason
    rec = FJ.unfinished_write(env.journal.dir)
    assert rec is not None and rec["state"] == "failed" and not rec["alive"]


class _PortGone(FakeMboot):
    """the cable is pulled after a number of answers: the transport raises"""
    def __init__(self, *a, after=40, **kw):
        super().__init__(*a, **kw)
        self.after, self.n = after, 0

    def send(self, data):
        if self._gen is not None:
            self.n += 1
            if self.n > self.after:
                from fm1companion.transport import LinkError
                raise LinkError("the port is gone")
        return super().send(data)


@pytest.mark.parametrize("after", [3, 130])
def test_m1_the_port_lost_during_the_write_is_a_failed_write_with_the_backup(unit, after):
    env = unit
    env.fake = _PortGone(mboot_flash(), install=True, version="2.4-395e6cc", after=after)
    with pytest.raises(FJ.WriteFailed) as e:
        go(env)
    assert e.value.kind == "connection" and e.value.backup.name in e.value.reason
    rec = FJ.unfinished_write(env.journal.dir)
    assert rec["state"] == "failed" and rec["op"] == "sloop3-install" and not rec["alive"]
    assert not FJ.WRITING.is_set()


@pytest.mark.parametrize("swap", ["flash", "unit", "version"])
def test_m2_a_unit_swapped_while_the_dialog_was_open_is_not_written(unit, swap):
    env = unit
    before = env.fake.flash

    def confirm(plan):
        if swap == "flash":
            b = bytearray(env.fake.flash)
            b[0x50000] ^= 1
            env.fake.flash = bytes(b)
        elif swap == "unit":
            env.fake.uid_bytes = bytes(range(0xB0, 0xB8))
        else:
            env.fake.version = "2.4-395e6cd"
        return True
    with pytest.raises(FJ.Refused) as e:
        M.install(env.job(), env.fake, confirm, area=small_area(), version="3.4.0")
    assert e.value.kind in ("flash-changed", "unit-changed") and not e.value.written
    assert env.fake.installs == 0 and not env.fake.erased and not FJ.record_file(env.journal.dir).exists()
    if swap != "flash":
        assert env.fake.flash == before


def test_l2_the_writer_is_named_m_bootloader_while_it_writes(unit):
    env = unit
    seen = []
    job = env.job(on_writing=lambda rec: seen.append(FJ.WRITER["who"]))
    M.install(job, env.fake, lambda plan: True, area=small_area(), version="3.4.0")
    assert seen == ["m-bootloader"] and FJ.WRITER["who"] == "M-Flasher"


CHILD = r'''
import json, os, signal, sys
sys.path.insert(0, sys.argv[1]); sys.path.insert(0, sys.argv[1] + "/tests")
from pathlib import Path
from fm1companion import devices
from fm1companion.flash import journal as fj
from fm1companion.tools import flashjob as FJ, mbinstall as M
from fake_mboot import FakeMboot
from test_sloop3_install import mboot_flash, small_area
a = json.loads(Path(sys.argv[2]).read_text())
profiles = devices.ProfileStore(Path(a["profiles"]))
job = FJ.Job(backups_dir=Path(a["backups"]), profiles=profiles, profile=profiles.profiles[a["prof"]],
             log_dir=Path(a["logs"]), journal=fj.Journal(Path(a["journal"])))
job.progress = lambda d, t, checking: os.kill(os.getpid(), signal.SIGKILL) if not checking else None
M.install(job, FakeMboot(mboot_flash(), install=True, version="2.4-395e6cc"), lambda p: True, area=small_area(),
          version="3.4.0")
'''


def test_l4_the_app_killed_during_the_midi_mode_write_leaves_the_record_for_the_next_start(unit):
    import subprocess
    env = unit
    env.profiles.save()
    args = env.tmp / "child.json"
    args.write_text(json.dumps({"profiles": str(env.tmp / "devices.json"), "prof": env.prof.id,
                                "backups": str(env.backups), "logs": str(env.logs), "journal": str(env.journal.dir)}))
    r = subprocess.run([sys.executable, "-c", CHILD, str(ROOT), str(args)], timeout=120, cwd=str(ROOT / "tests"))
    assert r.returncode == -9
    rec = FJ.unfinished_write(env.journal.dir)
    assert rec is not None and rec["op"] == "sloop3-install" and rec["state"] == "writing" and not rec["alive"]
    from fm1companion.ui import flash_tools
    assert Path(rec["pre_backup"]).name in flash_tools.interrupted_text(rec)


def test_l6_only_a_reviewed_m_bootloader_build_also_on_an_earlier_unit(unit):
    env = unit
    env.fake.version = "2.3-abcdef0"
    with pytest.raises(FJ.Refused) as e:
        go(env)
    assert e.value.kind == "mboot-build" and env.fake.installs == 0 and 0x23 not in env.fake.commands


def test_l8_the_hardware_rule_is_judged_for_the_bundled_version():
    from fm1companion import sloop3 as S3
    from fm1companion.ui import sloop3_ui
    assert sloop3_ui.bundled_version() == M.bundled_area()[1] == S3.CHECKED_VERSIONS[-1] == "3.4.0"


@pytest.mark.skipif(not (os.environ.get("FM1C_MBOOT_SRC") and shutil.which("cc")),
                    reason="needs FM1C_MBOOT_SRC (a checkout of jvitkauskas/m-bootloader v2.4) and a C compiler")
def test_h1_m_bootloaders_own_code_failing_its_first_erase(env, tmp_path, monkeypatch):
    from fm1companion.transport import SubprocessTransport
    src = Path(os.environ["FM1C_MBOOT_SRC"])
    exe = tmp_path / "mbsim"
    cc = subprocess.run(["cc", "-O1", "-std=c11", "-D_DEFAULT_SOURCE", f"-I{src / 'include'}", "-o", str(exe),
                         str(ROOT / "tests" / "mbsim.c"), str(src / "src" / "midi_update.c"),
                         str(src / "src" / "core.c")], capture_output=True, text=True, timeout=120)
    assert cc.returncode == 0, cc.stderr
    monkeypatch.setattr(M, "REQUEST_TIMEOUT", 2.0)
    monkeypatch.setenv("MBSIM_FAIL_ERASE", "1")
    (tmp_path / "in.bin").write_bytes(mboot_flash())
    env.prof.unit_id = MB_UID
    t = SubprocessTransport([str(exe), str(tmp_path / "in.bin"), str(tmp_path / "out.bin"), "980f", "856014",
                             "2.4-395e6cc"])
    assert t.wait_started(10)
    try:
        with pytest.raises(FJ.WriteFailed):
            M.install(env.job(), t, lambda plan: True, area=small_area(), version="3.4.0")
    finally:
        t.close()
    assert FJ.unfinished_write(env.journal.dir)["state"] == "failed"
