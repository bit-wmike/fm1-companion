# SPDX-License-Identifier: GPL-3.0-only
"""M-VAVE's FM1 Open twin (tools/studio/fm1open_studio.c on FM1 Open's own simulator web/engine/Simulator.c) and the
original firmware's support: the build, versions and picking (also as the stand-in for the original V15/V16), the
firmware core in node (tests/studio_twin_fm1open.mjs), FM1 Open's backup over its own file protocol (fm1openbk.py)
through the suite's code (backup.capture / read / restore, the Studio facade with the node backend), and the original
V15's voice banks (stockv15.py: M-VAVE's sdk/WebUpdate.js restoreFactory) against a simulated V15.

  python3 -m pytest tests/test_studio_fm1open.py -q
"""
from __future__ import annotations

import hashlib
import json
import shutil
import struct
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fm1companion import backup, fm1open as F, fm1openbk, sims, stockv15, studio  # noqa: E402
from fm1companion.demo_device import _identity_frame  # noqa: E402
from fm1companion.transport import Link, Transport  # noqa: E402

NODE = shutil.which("node") or ("/opt/node22/bin/node" if Path("/opt/node22/bin/node").exists() else None)
COMMIT = "4c7496daac04a95ff10d1afdcc8f9ead2dbabc41"           # the commit the bundled fm1-open simulator is built from


@pytest.fixture
def node_studio():
    if not NODE:
        pytest.skip("node missing")
    made = []

    def make(version="", firmware="fm1-open", **kw):
        st = studio.Studio.create(firmware=firmware, version=version, backend="node", **kw)
        made.append(st)
        return st
    yield make
    for st in made:
        st.close()


# ------------------------------------------------------------------ the build
def test_fm1open_build():
    bs = studio.builds("fm1-open")
    assert [b.version for b in bs] == ["V14"] and [b.path.name for b in bs] == ["V14"]
    b = bs[0]
    m = b.manifest
    assert b.verify() == []
    assert m["kind"] == "studio" and m["abi"] == studio.STUDIO_ABI and m["firmware"] == "fm1-open"
    assert m["commit"] == m["ref"] == COMMIT and m["firmware_version"] == "V14" and m["release_status"] == "current"
    assert m["identity"] == "FM-1_8000014" and m["fs"] == 44100 and m["flash_size"] == 0x100000
    assert m["tracks"] == 8 and m["projects"] == 16 and m["source_repo"] == "https://github.com/m-vave/fm1_open"
    assert "linking-exception" in m["license"]
    assert {"LICENSE", "NOTICE", "THIRD_PARTY.md", "twin.wasm"} <= set(m["files"])
    notice = (b.path / "NOTICE").read_text()
    assert "unmodified" in notice and "Written offer" in notice and f"fm1_open-{COMMIT}.tar.gz" in notice
    assert "M-VAVE" in notice and "libfm1_engine.a" in notice and "V15/V16" in notice
    assert m["glue"] == "tools/studio/fm1open_studio.c"
    assert m["glue_sha256"] == hashlib.sha256((ROOT / m["glue"]).read_bytes()).hexdigest()
    assert "linking permission" in (b.path / "LICENSE").read_text()
    third = (b.path / "THIRD_PARTY.md").read_text()
    assert "Open303" in third and "DaisySP" in third and "FM-1_015.fwsc" in third
    # the same source as the bundled simulator (sims/fm1-open), which stays as it is
    sim = json.loads((studio.SIMS_DIR / "fm1-open" / "manifest.json").read_text())
    assert sim["commit"] == COMMIT
    assert not any(s.key == "fm1-open-studio" for s in sims.bundled())


def test_fm1open_versions_and_pick():
    assert studio.fm1open_version("FM1 OPEN V14") == "V14" and studio.fm1open_version("v14") == "V14"
    assert studio.fm1open_version("FM1 Open") == "" and studio.fw_version("FM1 Open", "V14") == "V14"
    b, exact, warn = studio.pick_build("fm1-open", "FM1 OPEN V14")
    assert exact and b.version == "V14" and not warn
    b, exact, warn = studio.pick_build("fm1-open", "V13")              # older: the oldest bundled, with a warning
    assert not exact and b.version == "V14" and "FM1 Open" in warn
    assert studio.pick_build("fm1-open")[0].version == "V14"
    assert studio.check_versions("V14", device="V14", firmware="FM1 Open").ok
    vc = studio.check_versions("V14", device="V13", firmware="FM1 Open")
    assert not vc.ok and "FM1 Open" in vc.message
    assert studio.firmware_name("fm1-open") == "FM1 Open" and studio.FIRMWARES["fm1-open"]["projects"] == 16
    assert "FM1 Open" in studio.firmware_names()
    # the original firmware opens the FM1 Open twin as a stand-in (with the note); FM1 Open itself by its key
    assert studio.stand_in_for("stock-v15") == studio.stand_in_for("stock-v16") == "fm1-open"
    assert studio.stand_in_for("melodee") == "" and "V15" in studio.STAND_IN_NOTE
    from fm1companion import dx7, firmware
    from fm1companion.ui.studio_service import StudioService
    assert StudioService.firmware_for("fm1-open") == "fm1-open" == StudioService.firmware_for("stock-v15")
    assert StudioService.firmware_for("stock-v16") == "fm1-open" and StudioService.firmware_for("x0x") == ""
    assert firmware.guess_firmware("FM-1_8000014")[0] in ("fm1-open", "")   # ("" / other: remembered on this machine)
    assert firmware.guess_firmware("FM1_OFFICIAL_OPEN")[0] in ("fm1-open", "")
    assert dx7.send_plan("fm1-open") == dx7.send_plan("stock-v15") == "single"
    assert fm1openbk.release("FM-1_8000014") == "V14" and fm1openbk.is_identity("FM-1_8000014")
    assert not fm1openbk.is_identity("FM-1_015")


@pytest.mark.skipif(not NODE, reason="node missing")
def test_fm1open_twin_core_headless():
    r = subprocess.run([NODE, str(ROOT / "tests" / "studio_twin_fm1open.mjs"), str(studio.SIMS_DIR / "fm1-open-studio")],
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-3000:]
    res = json.loads(r.stdout)
    assert [x["version"] for x in res] == ["V14"]
    for x in res:
        assert x["version_export"] == "FM1 OPEN V14" and x["identity"] == "FM-1_8000014" and x["update_key"] == 0
        assert x["library"]["slots"] == 16 and x["library"]["format"] == 4 and x["library_available"] == 1
        assert x["names_fresh"] == ["EMPTY"] * 3 and x["samples"]["status"] == 2
        assert x["bank_a_first"] == "PIANO 1   "                    # the V15 factory voices' first
        assert x["persisted"] and x["working_same"] and x["bank_d_same"]
        a, b = x["takes"]
        assert a["playing"] and a["play_lit"] and a["peak"] > 0.02 and a["hash"] == b["hash"]
        assert x["fork_identical"] and x["stopped"] and x["song_play"] and x["restart_play"]
        assert x["mute_rms"][1] < x["mute_rms"][0] / 20 and x["note_peak"] > 0.01
        print(f"V14: {a['realtime']:.1f}x real time (8 tracks)")
        assert a["realtime"] > 3


# ------------------------------------------------------------------ backups over FM1 Open's file protocol
def _two_projects(st):
    """slot 2 and slot 5 saved (the working area's factory project, renamed), the working area changed after"""
    with F.Client(st.link) as c:
        c.query(7, bytes([1]))
        c.query(10, b"SECOND PROJ ")
        c.query(7, bytes([4]))
        c.query(10, b"WORKING EDIT")
        return fm1openbk._working(c)


def test_fm1open_backup_round_trip_with_suite_code(node_studio, tmp_path):
    a = node_studio()
    assert a.build.firmware == "fm1-open" and a.tracks == 8 and a.project_slots == 16
    assert a.device_version(a.link) == "V14" and fm1openbk.identity(a.link) == "FM-1_8000014"
    work = _two_projects(a)
    f = backup.capture(a.link, "V14", companion={"firmware": "fm1-open", "firmware_version": "V14"})
    arch = backup.read(f)
    assert arch.fm1open and not arch.sloop and arch.known and arch.project_objects() == []
    assert [o["id"] for o in arch.objects] == fm1openbk.IDS
    sizes = {o["id"]: o["size"] for o in arch.objects}
    assert sizes[0] == sizes[2] == sizes[5] == 4264 and sizes[1] == sizes[3] == 0 and sizes[32] > 512
    assert all(sizes[i] == 4096 for i in fm1openbk.BANK_IDS)
    assert f["names"][1] == "UNTITLED" and f["names"][4] == "SECOND PROJ" and f["slot"] == 4
    assert f["identity"] == "FM-1_8000014" and studio.backup_firmware(arch) == "fm1-open"
    assert studio.backup_version(arch) == "V14"
    with F.Client(a.link) as c:                     # the backup leaves the working area as it was (unsaved too)
        assert fm1openbk._working(c) == work and c.probe()["project"] == 4
    # file round trip as the app writes it
    p = tmp_path / "b.json"
    backup.write_file(p, f)
    assert backup.load_file(p).objects[0]["bytes"] == work
    from fm1companion import autobackup as AB
    meta = AB.read_meta(p)
    assert meta is not None and meta.fmt == "fm1open-backup" and meta.projects == 2 and meta.fw_key == "fm1-open"
    # into another twin (the studio's tolerant load) and back: identical
    b = node_studio()
    b.open_from_backup(arch, tolerant=True)
    assert b.rejected == [] and b.projects() == [1, 4]
    back = {o["id"]: o["bytes"] for o in b.backup().objects}
    assert all(back[o["id"]] == o["bytes"] for o in arch.objects)
    # Studio.for_backup picks the FM1 Open twin; open_from_device reads a "device" (the first twin)
    c = studio.Studio.for_backup(arch, backend="node")
    try:
        assert c.build.firmware == "fm1-open" and c.projects() == [1, 4]
        arch2, vc = c.open_from_device(a.link)
        assert arch2.fm1open and vc.ok
        assert c.load_project(4) == 0 and c.load_project(0) == 1
        assert c.play()["busy"] and not c.stop()["busy"]
    finally:
        c.close()
    # write back (Studio -> "device"), then the plain restore of the backup layer
    d = node_studio()
    d.write_to_device(b.link)
    backup.restore(d.link, arch)
    assert d.projects() == [1, 4]
    # a Felucca backup does not fit FM1 Open, an FM1 Open backup no Felucca twin
    felucca = {"format": "felucca-backup", "version": 1, "firmware": "MELODEE v1.6.1", "objects": [
        {"id": 0, "size": 1, "crc": backup.crc32(b"x"), "data": "eA=="},
        {"id": 1, "size": 1, "crc": backup.crc32(b"x"), "data": "eA=="}]}
    with pytest.raises(backup.BackupError, match="FM1 Open"):
        backup.restore(d.link, backup.read(felucca), firmware_key="fm1-open")
    with pytest.raises(studio.StudioError):
        d.open_from_backup(backup.read(felucca))


def test_fm1open_backup_file_checks():
    good = {"format": "fm1open-backup", "version": 1, "firmware": "FM1 Open V14", "objects": [
        {"id": 0, "size": 4064, "crc": backup.crc32(bytes(4064)), "data": __import__("base64").b64encode(bytes(4064)).decode()}]}
    assert fm1openbk.objects(good)[0] == bytes(4064)
    bad = json.loads(json.dumps(good))
    bad["objects"][0]["crc"] ^= 1
    with pytest.raises(backup.BackupError):
        backup.read(bad)
    bad = json.loads(json.dumps(good))
    bad["objects"][0]["id"] = 7                       # slot 7 alone: no working project
    with pytest.raises(backup.BackupError):
        backup.read(bad)
    bad = json.loads(json.dumps(good))
    bad["objects"][0]["id"] = 99
    with pytest.raises(backup.BackupError):
        backup.read(bad)
    assert fm1openbk.title(20).endswith(" A") and fm1openbk.title(3).endswith(" 3")


# ------------------------------------------------------------------ the original V15: voice banks (stockv15.py)
class FakeV15(Transport):
    """an FM-1 with the original V15 as far as M-VAVE's workbench uses it: identity (17), 33 (prepare a bank), 34 (write
    512 bytes), each answered by command 0 status 0 (WebUpdate.js restoreFactory)"""

    def __init__(self, identity="FM-1_015"):
        self.identity = identity
        self.bank = bytearray(b"\xff" * 16384)
        self.log = []

    def _reply(self, cmd, body=b""):
        r = self.receiver
        if r is not None:
            threading.Timer(0.001, lambda: r(stockv15.frame(cmd, body))).start()

    def send(self, data):
        p = stockv15.parse(data)
        assert p, data[:12]
        cmd, body = p
        self.log.append((cmd, bytes(body[:8])))
        if cmd == 17:
            t = self.identity.encode()
            self._reply(17, t + bytes(27 - len(t)))
        elif cmd == 33:
            assert body[0] == 5 and len(body) == 5
            at = body[2] << 8
            self.bank[at:at + 4096] = b"\xff" * 4096
            self._reply(0, b"\0")
        elif cmd == 34:
            assert body[0] == 5 and body[5:8] == b"\0\x02\0" and len(body) == 8 + 512
            at = body[1] | body[2] << 8
            self.bank[at:at + 512] = body[8:]
            self._reply(0, b"\0")


def test_stock_v15_frames_and_bank_write():
    f = stockv15.frame(17)
    assert bytes(f) == bytes([0xF0, *stockv15.pack7(bytes([0, 89, 17, 0, 0, 0, 255])), 0xF7])
    assert stockv15.parse(_identity_frame("FM-1_015")) == (17, b"FM-1_015" + bytes(19))
    # the factory restore's own write path (Client.write_banks, M-VAVE's restoreFactory): one bank, frame by frame
    dev = FakeV15()
    link = Link(dev)
    data = bytes((i * 7) & 255 for i in range(4096))
    with stockv15.Client(link) as c:
        c.write_banks(data, 2)
    assert dev.bank[8192:12288] == data and dev.bank[:8192] == b"\xff" * 8192
    cmds = [c for c, _b in dev.log]
    assert cmds == [17, 33] + [34] * 8 + [17]
    assert dev.log[1][1] == bytes([5, 0, 32, 0, 0])                 # bank C at 8192 (WebUpdate.js: bank >>> 8)
    with pytest.raises(stockv15.StockError):                        # V16: refused before anything is written
        with stockv15.Client(Link(FakeV15("FM-1_016"))) as c:
            c.write_banks(data, 0)
    with pytest.raises(stockv15.StockError):
        with stockv15.Client(link) as c:
            c.write_banks(data[:100], 0)
    # writing own voices into a bank is not offered (it needs a hardware test, docs/ROADMAP.md)
    assert not hasattr(stockv15, "write_bank")


def _v15_package() -> bytes | None:
    from fm1companion import firmware
    for p in [Path("/tmp/fm1_open/sdk/firmware/FM-1_015.fwsc"), *firmware.local_packages()]:
        try:
            b = p.read_bytes()
        except OSError:
            continue
        if hashlib.sha256(b).hexdigest() == stockv15.PACKAGE_SHA256:
            return b
    return None


def test_stock_v15_factory_voices():
    pkg = _v15_package()
    if pkg is None:
        pytest.skip("the V15 package (FM-1_015.fwsc) is not on this machine")
    voices = stockv15.factory_voices(pkg)
    assert len(voices) == 16384 and hashlib.sha256(voices).hexdigest() == stockv15.FACTORY_SHA256
    assert [voices[k * 128 + 118:k * 128 + 128] for k in range(4)] == [b"PIANO 1   ", b"ORGAN 1   ", b"SYN LEAD 1",
                                                                          b"SYN PAD 1 "]
    dev = FakeV15()
    stockv15.restore_factory(Link(dev), pkg)
    assert bytes(dev.bank) == voices
    with pytest.raises(stockv15.StockError):
        stockv15.factory_voices(pkg[:-1] + bytes([pkg[-1] ^ 1]))
    # the FM1 Open twin's DX7 library holds the same 128 voices (FM1 Open's app/fm_factory.inc, from this package)
    if NODE:
        st = studio.Studio.create("fm1-open", backend="node")
        try:
            with F.Client(st.link) as c:
                lib = b"".join(fm1openbk._download(c, 4, k) for k in range(4))
            assert lib == voices
        finally:
            st.close()


def test_stock_device_layer():
    from fm1companion import firmware
    assert firmware.guess_firmware("FM-1_015")[0] in ("stock-v15", "") and struct.calcsize("<I") == 4
    assert firmware.spec("stock-v15").sha256 == stockv15.PACKAGE_SHA256
