# SPDX-License-Identifier: GPL-3.0-only
"""Core tests against the simulated FM-1 (without Qt, without a real device).

  python3 -m pytest tests/test_core.py -q
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fm1companion import backup, protocol            # noqa: E402
from fm1companion.transport import Link, Transport    # noqa: E402
from fm1companion.demo_device import FakeFM1          # noqa: E402

MELODEE_WEB = Path("/tmp/melodee/web")


class LocalTransport(Transport):
    """calls the simulated device directly (answer in its own thread, like MIDI)."""

    def __init__(self, dev):
        self.dev = dev

    def send(self, data):
        r = self.dev.handle(list(data))
        if r is not None and self.receiver:
            threading.Thread(target=self.receiver, args=(r,), daemon=True).start()


def make(samples=0):
    dev = FakeFM1(samples)
    return dev, Link(LocalTransport(dev))


def test_numbers():
    assert protocol.r32(protocol.u32(0xDEADBEEF)) == 0xDEADBEEF
    data = bytes(range(256)) * 3
    assert protocol.unpack7(protocol.pack7(data), len(data)) == data
    assert protocol.crc32(b"123456789") == 0xCBF43926


def test_info():
    _, link = make()
    info = protocol.parse_info(link.request(protocol.Cmd.INFO))
    assert info.version.startswith("Melodee") and info.p_count == 91 and info.ntrk == 4
    assert info.firmware_name == "melodee"


def test_backup_roundtrip_and_restore_order():
    dev, link = make()
    f = backup.capture(link, "TEST", companion={"device": "A"})
    assert len(f["objects"]) == 57 and f["companion"]["device"] == "A"
    arch = backup.read(json.dumps(f))
    assert arch.known
    assert [o["id"] for o in arch.project_objects()] == [2, 66]
    # change the device, then restore
    dev.objs[2] = b"x" * 10
    dev.objs[0] = b"y" * 10
    dev.log.clear()
    backup.restore(link, arch)
    assert dev.objs[2] == arch.objects[2]["bytes"]
    assert dev.log[-1] == 0 and dev.log[-2] == 1          # running music last, settings before it
    assert not [x for x in dev.log if str(x).startswith("abort")]


def test_damaged_file_refused():
    _, link = make()
    f = backup.capture(link, "TEST")
    f["objects"][2]["crc"] ^= 1
    with pytest.raises(backup.BackupError):
        backup.read(f)


def test_change_during_capture_detected():
    dev, link = make()
    orig = dev._handle

    def changing(cmd, a):
        if cmd == 66 and a[0] == 2 and protocol.r32(a, 1) == 0:
            dev.objs[2] = bytes([dev.objs[2][0] ^ 1]) + dev.objs[2][1:]
        return orig(cmd, a)
    dev._handle = changing
    with pytest.raises(backup.BackupError):
        backup.capture(link, "TEST")


def test_single_project_object():
    dev, link = make()
    data = backup.read_object(link, next(o for o in backup.list_objects(link) if o.id == 66))
    assert data == dev.objs[66]
    assert backup.project_slot(66) == 7 and backup.project_id(7) == 66 and backup.project_id(3) == 4
    backup.write_object(link, backup.project_id(32), data)
    assert dev.objs[91] == data


@pytest.mark.skipif(not shutil.which("node") or not (MELODEE_WEB / "fm1backup.js").exists(),
                    reason="node or Melodee repo missing")
def test_interop_with_original_js(tmp_path):
    """A backup of the app must be accepted by the original editor (fm1backup.js) – and vice versa."""
    _, link = make()
    f = backup.capture(link, "TEST", companion={"device": "A"})
    p = tmp_path / "b.json"
    p.write_text(json.dumps(f))
    shutil.copy(MELODEE_WEB / "fm1backup.js", tmp_path / "fm1backup.mjs")
    script = tmp_path / "check.mjs"
    script.write_text(f"""
import {{ readFileSync, writeFileSync }} from "node:fs";
import {{ readBackup }} from "./fm1backup.mjs";
const a = readBackup(readFileSync({json.dumps(str(p))}, "utf8"));
const again = {{ format: "felucca-backup", version: 1, firmware: "JS", created: a.created,
  objects: a.objects.map(o => ({{ id: o.id, size: o.size, crc: o.crc, data: o.data }})) }};
writeFileSync({json.dumps(str(tmp_path / "js.json"))}, JSON.stringify(again));
console.log("ok", a.objects.length);
""")
    r = subprocess.run(["node", str(script)], capture_output=True, text=True, cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith("ok 57")
    arch = backup.load_file(tmp_path / "js.json")
    assert arch.firmware == "JS" and len(arch.objects) == 57
