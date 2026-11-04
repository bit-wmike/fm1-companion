# SPDX-License-Identifier: GPL-3.0-only
""""Clean up leftovers" (fm1companion/flash/restzone.py): hard gates, plan from a fresh dump, erasing with the Unbricker's
mock FM-1, journal and resuming after a crash, block check, root copy via ROOT_HELPER.

Parts with real packages need FM1_TEST_PKGDIR (v16.fwsc, melodee.fwsc, zip_v15.fwsc) – otherwise skipped.
"""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from fm1companion.flash import flashmodel as fm  # noqa: E402
from fm1companion.flash import restzone as rz  # noqa: E402

fu = rz._unbricker()
PKGDIR = os.environ.get("FM1_TEST_PKGDIR", "")
need_pkgs = pytest.mark.skipif(not PKGDIR or not all((Path(PKGDIR) / f).is_file()
                                                     for f in ("v16.fwsc", "melodee.fwsc", "zip_v15.fwsc")),
                               reason="real FM-1 packages missing (FM1_TEST_PKGDIR)")
REFS = fm.load_known() + fm.demo_refs()


def mock(image):
    m = fu.MockUboot(image=bytes(image))
    return fu.Fm1Device("mock://fm1-uboot", m, io_max=512, mock=True)


@pytest.fixture(autouse=True)
def _quiet(tmp_path):
    fu.setup_logging("test", tmp_path / "logs")
    yield
    fu.close_logging()


def test_gate_only_whole_sectors_in_zone():
    for a in (0x93000, 0x9F000, 0xA8000):
        rz.gate(a)
    for a in (0x0, 0x3000, 0x4000, 0x92000, 0xA9000, 0xE9000, 0xEC000, 0xFF000, 0x93001, 0x93800):
        with pytest.raises(rz.Refused):
            rz.gate(a)


def test_plan_demo_and_refusals():
    img = rz.demo_image()
    p = rz.make_plan(img, REFS)
    assert p.ok and p.firmware.startswith("Demo-Melodee")
    assert p.addrs() == list(range(0x97000, 0xA9000, 0x1000))
    assert p.sectors[0]["kind"] == "teilweise" and p.sectors[1]["kind"] == "voll"
    assert [s["addr"] for s in p.skipped] == [0x93000, 0x94000, 0x95000, 0x96000]       # user data stays
    # a sector the firmware has rewritten in the meantime is no longer a leftover
    d = bytearray(img)
    d[0xA0000:0xA0000 + 0x1000] = b"\xff" * 0x1000
    d[0xA0010:0xA0030] = b"Melodee-Pool-Eintrag 0123456789ab"[:32]
    p2 = rz.make_plan(bytes(d), REFS)
    assert 0xA0000 not in p2.addrs() and any(s["addr"] == 0xA0000 for s in p2.skipped)
    # V16 in the app area: never
    v16 = fm._demo_bytes("demo-v16", 0xA9000)
    d = bytearray(img)
    d[0x4000:0xA9000] = v16[0x4000:]
    p3 = rz.make_plan(bytes(d), REFS)
    assert not p3.ok and "V16" in p3.reason and not p3.sectors
    # unknown / damaged firmware: never
    d = bytearray(img)
    d[0x50000] ^= 0xFF
    p4 = rz.make_plan(bytes(d), REFS)
    assert not p4.ok and "byte-genau" in p4.reason
    assert not rz.make_plan(img[:1000], REFS).ok


def test_execute_erases_exactly_the_plan(tmp_path):
    img = rz.demo_image()
    dev = mock(img)
    res = rz.execute(dev, fu, tmp_path, REFS, plan_only=True)
    assert res["ok"] and len(res["planned"]) == 18 and not [o for o in dev.ldr.ops if o[0] != "write"]
    only = {int(p["addr"], 16) for p in res["planned"]} - {0xA8000}
    res = rz.execute(dev, fu, tmp_path, REFS, only=only)
    assert res["ok"] and res["level"] == "gruen" and len(res["erased"]) == 17
    assert all(op == ("erase_sector", a, 0x1000) for op, a in zip(dev.ldr.ops, sorted(only, reverse=True)))
    assert {o[0] for o in dev.ldr.ops} == {"erase_sector"}                            # never a block, never writing
    want = bytearray(img)
    for a in only:
        want[a:a + 0x1000] = b"\xff" * 0x1000
    assert bytes(dev.ldr.flash) == bytes(want)                                         # otherwise not a byte changed
    assert any("nicht bestätigt" in s["why"] for s in res["skipped"])
    assert Path(res["backup"]).read_bytes() == img and Path(res["after"]).read_bytes() == bytes(want)
    j = json.loads((tmp_path / rz.JOURNAL_NAME).read_text())
    assert j["state"] == "abgeschlossen" and all(e["state"] == "fertig" for e in j["sectors"].values())
    rep = rz.report(res)
    assert rep.level == fm.GREEN and any("ungetestet" in t for t in rep.not_covered)


def test_abort_and_resume_with_journal(tmp_path):
    img = rz.demo_image()
    dev = mock(img)
    only = set(range(0x97000, 0xA9000, 0x1000))
    res = rz.execute(dev, fu, tmp_path, REFS, only=only, abort_after=3)
    assert not res["ok"] and res["level"] == "rot" and res["erased"] == ["0xA8000", "0xA7000"]
    j = json.loads((tmp_path / rz.JOURNAL_NAME).read_text())
    assert j["state"] == "laeuft" and j["sectors"]["0xA6000"]["state"] == "loeschen"
    half = bytes(dev.ldr.flash[0xA6000:0xA7000])
    assert half != b"\xff" * 0x1000 and not rz.v16_kind(half, 0xA6000, [r for r in REFS if r.family == "v16-layout"])
    assert "Angehalten" in rz.report(res).findings[0][1]
    # new run, new backup: thanks to the journal the half-erased sector is recognised as "interrupted"
    plan = rz.execute(dev, fu, tmp_path, REFS, plan_only=True)
    assert {"addr": "0xA6000", "kind": "unterbrochen"} in plan["planned"] and len(plan["planned"]) == 16
    res = rz.execute(dev, fu, tmp_path, REFS, only={int(p["addr"], 16) for p in plan["planned"]})
    assert res["ok"] and len(res["erased"]) == 16
    assert bytes(dev.ldr.flash[0x97000:0xA9000]) == b"\xff" * 0x12000
    assert bytes(dev.ldr.flash[:0x97000]) == img[:0x97000] and bytes(dev.ldr.flash[0xA9000:]) == img[0xA9000:]


def test_resume_never_erases_rewritten_sector(tmp_path):
    """after the crash the firmware rewrote the half-erased sector -> stays"""
    img = rz.demo_image()
    dev = mock(img)
    rz.execute(dev, fu, tmp_path, REFS, only={0x98000}, abort_after=1)
    new = bytearray(b"\xff" * 0x1000)
    new[:64] = hashlib.sha256(b"melodee").digest() * 2
    dev.ldr.flash[0x98000:0x99000] = new
    plan = rz.execute(dev, fu, tmp_path, REFS, plan_only=True)
    assert "0x98000" not in [p["addr"] for p in plan["planned"]]
    assert any(s["addr"] == "0x98000" and "unterbrochen" in s["why"] for s in plan["skipped"])
    # a journal of another device/firmware state (different app area) does not count
    j = json.loads((tmp_path / rz.JOURNAL_NAME).read_text())
    j["app_sha256"] = "0" * 64
    (tmp_path / rz.JOURNAL_NAME).write_text(json.dumps(j))
    assert rz.make_plan(bytes(dev.ldr.flash), REFS, journal=j, jread=lambda f: Path(f).read_bytes()).sectors[0]["addr"] != 0x98000


def test_chip_that_erases_more_stops_at_once(tmp_path):
    img = rz.demo_image()
    dev = mock(img)

    def greedy(addr):                                  # "erase sector" really erases 64 KiB
        dev.ldr.ops.append(("erase_sector", addr, 0x1000))
        lo = addr - addr % 0x10000
        dev.ldr.flash[lo:lo + 0x10000] = b"\xff" * 0x10000
    dev.ldr.flash_erase_sector = greedy
    res = rz.execute(dev, fu, tmp_path, REFS, only={0x97000, 0x98000})
    assert not res["ok"] and "mehr gelöscht" in res["error"] and len(dev.ldr.ops) == 1


def test_changed_since_backup_and_flaky_reads(tmp_path):
    img = rz.demo_image()
    dev = mock(img)
    real_read = dev.ldr.flash_read
    calls = {"n": 0}

    def drift(addr, n):                                # after the backup a sector changes (must not happen)
        calls["n"] += 1
        if calls["n"] > 2 * 2048:
            dev.ldr.flash[0x97100] = 0x00
        return real_read(addr, n)
    dev.ldr.flash_read = drift
    res = rz.execute(dev, fu, tmp_path, REFS, only={0x97000})
    assert not res["ok"] and "verändert" in res["error"] and not [o for o in dev.ldr.ops if o[0] != "write"]
    dev2 = mock(img)
    dev2.ldr.flaky_read = 5                            # backup: two reads differ -> nothing happens
    with pytest.raises(fu.UnbrickError):
        rz.execute(dev2, fu, tmp_path / "b", REFS, only={0x97000})
    assert not dev2.ldr.ops


def test_cli_dry_run_and_refusals(tmp_path):
    script = ROOT / "fm1companion" / "flash" / "restzone.py"
    img = tmp_path / "img.bin"
    img.write_bytes(rz.demo_image())
    base = [sys.executable, "-I", str(script)]
    common = ["--dry-run", "--demo-refs", "--mock-image", str(img), "--log-dir", str(tmp_path / "logs")]
    r = subprocess.run(base + ["plan", str(tmp_path / "o"), "--result", str(tmp_path / "p.json")] + common,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert len(json.loads((tmp_path / "p.json").read_text())["planned"]) == 18
    r = subprocess.run(base + ["clean", str(tmp_path / "o"), "--result", str(tmp_path / "c.json")] + common,
                       capture_output=True, text=True)
    assert r.returncode == rz.EXIT_REFUSED and "bestätigte" in json.loads((tmp_path / "c.json").read_text())["refused"]
    r = subprocess.run(base + ["clean", str(tmp_path / "o"), "--result", str(tmp_path / "c.json"), "--only",
                               "0x3000"] + common, capture_output=True, text=True)
    assert r.returncode == rz.EXIT_REFUSED and img.read_bytes() == rz.demo_image()
    r = subprocess.run(base + ["plan", str(tmp_path / "o"), "--result", str(tmp_path / "x.json"), "--demo-refs",
                               "--log-dir", str(tmp_path / "logs")], capture_output=True, text=True)
    assert r.returncode == rz.EXIT_REFUSED                        # demo options only in the simulation


def test_root_copy_contains_own_files_and_hashes_match(tmp_path):
    from fm1companion.flash import uboot, uboot_manifest as M
    assert set(M.OWN) == set(uboot.OWN_FILES)
    for n, p in uboot.OWN_FILES.items():
        assert hashlib.sha256(p.read_bytes()).hexdigest() == M.OWN[n], f"uboot_manifest.OWN outdated: {n}"
    argv = uboot.Runner(dry_run=False, cwd=tmp_path, root_cmd=["pkexec"]).argv("plan", "x", "--result", "r.json",
                                                                               script=uboot.RESTZONE_SCRIPT)
    spec = json.loads(argv[-1])
    assert spec["script"] == "restzone.py" and set(spec["own"]) == set(M.OWN)
    # restore & co. do not get the own files
    assert json.loads(uboot.Runner(dry_run=False, cwd=tmp_path, root_cmd=["pkexec"]).argv("backup", "x")[-1])["own"] == {}


def test_root_helper_runs_verified_copy_in_simulation(tmp_path):
    """the real ROOT_HELPER (without pkexec) copies Unbricker + restzone + flashmodel + profiles, checks and starts"""
    from fm1companion.flash import uboot, uboot_manifest as M
    jl = tmp_path / "jl"
    jl.mkdir()
    (jl / "x.py").write_text("")
    spec = uboot.root_spec(uboot.RESTZONE_SCRIPT, ["plan", str(tmp_path / "o"), "--result", str(tmp_path / "r.json"),
                                                   "--dry-run", "--mock-demo", "--demo-refs"], tmp_path, tmp_path / "logs")
    spec["jl_dir"], spec["jl"] = str(jl), {"x.py": hashlib.sha256(b"").hexdigest()}
    r = subprocess.run([sys.executable, "-I", "-c", uboot.ROOT_HELPER, json.dumps(spec)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "geprüft" in r.stdout and len(json.loads((tmp_path / "r.json").read_text())["planned"]) == 18
    assert M.OWN["restzone.py"]


@need_pkgs
def test_real_chain_melodee_v16_v15_melodee(tmp_path):
    """real packages: Melodee → V16 (complete, booted) → V15 → Melodee; plan = exactly the model's V16 leftovers"""
    pk = {k: fm.Package.load(Path(PKGDIR) / f) for k, f in (("v16", "v16.fwsc"), ("mel", "melodee.fwsc"),
                                                            ("v15", "zip_v15.fwsc"))}
    known = list(pk.values())
    start = fm.installed_state(pk["mel"])
    rep = fm.replay(start, [("ota", pk["v16"], 1.0), ("boot", pk["v16"]), ("ota", pk["v15"], 1.0),
                            ("boot", pk["v15"]), ("ota", pk["mel"], 1.0)], pk["mel"], known)
    flash = bytes(rep["flash"].data)
    want = sorted(r["sector"] for r in rep["foreign_residue"] if 0x93000 <= r["sector"] < 0xA9000)
    refs = fm.load_known()
    p = rz.make_plan(flash, refs)                       # only profile hashes, without a package file
    assert p.ok and p.firmware.startswith("Melodee") and p.addrs() == want and len(want) == 18
    p2 = rz.make_plan(flash, refs, [pk["v16"]])
    assert p2.addrs() == want
    dev = mock(flash)
    res = rz.execute(dev, fu, tmp_path, refs, [pk["v16"]], only=set(want))
    assert res["ok"] and len(res["erased"]) == 18
    assert bytes(dev.ldr.flash[:0x93000]) == flash[:0x93000] and bytes(dev.ldr.flash[0xA9000:]) == flash[0xA9000:]
    # aborted V16 (at 10 %): the partial sector at the abort point is detected
    rep = fm.replay(start, [("ota", pk["v16"], 0.1), ("ota", pk["v15"], 1.0), ("ota", pk["mel"], 1.0)], pk["mel"], known)
    flash = bytes(rep["flash"].data)
    want = sorted(r["sector"] for r in rep["foreign_residue"] if 0x93000 <= r["sector"] < 0xA9000)
    assert rz.make_plan(flash, refs).addrs() == want
    # V16 running: never
    v16_state = bytes(fm.installed_state(pk["v16"]).data)
    assert not rz.make_plan(v16_state, refs).ok


def test_erase_order_top_down():
    """The first erase command must never lie in the 64 KiB block of the firmware end (0x90000–0x9FFFF)."""
    import inspect
    from fm1companion.flash import restzone
    src = inspect.getsource(restzone)
    assert 'key=lambda s: s["addr"], reverse=True' in src
