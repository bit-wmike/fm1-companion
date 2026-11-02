# SPDX-License-Identifier: GPL-3.0-only
"""SLOOP-Backup (Protokoll v6, Befehle 34–36): byte-genau wie der SLOOP-Web-Editor, Rundreise gegen das
Demo-Gerät und gegen den Mock des Editors selbst (makeMockDevice), rc-Behandlung, Dateiformat.

  python3 -m pytest tests/test_sloop_backup.py -q
"""
from __future__ import annotations

import base64
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

from fm1companion import autobackup, backup, backup_inspect as BI, projects, sloopbk      # noqa: E402
from fm1companion.demo_device import FakeFM1, sloop_project                              # noqa: E402
from fm1companion.protocol import crc32, frame                                           # noqa: E402
from fm1companion.transport import Link, LinkError, SubprocessTransport, Transport       # noqa: E402

EDITOR = Path("/tmp/sloop-fm1/web/editor.html")
NODE = shutil.which("node") or ("/opt/node22/bin/node" if Path("/opt/node22/bin/node").exists() else None)
MOCK = ROOT / "tests" / "mock_sloop.mjs"
need_editor = pytest.mark.skipif(not (EDITOR.exists() and NODE), reason="SLOOP-Editor oder node fehlt")


class LocalTransport(Transport):
    def __init__(self, dev):
        self.dev = dev
        self.sent: list[list[int]] = []

    def send(self, data):
        self.sent.append(list(data))
        r = self.dev.handle(list(data))
        if r is not None and self.receiver:
            threading.Thread(target=self.receiver, args=(r,), daemon=True).start()


def sloop(samples=4):
    dev = FakeFM1(samples, "SLOOP 2.8.3")
    t = LocalTransport(dev)
    return dev, Link(t), t


def node(*args, timeout=120) -> str:
    return subprocess.run([NODE, str(MOCK), str(EDITOR), *map(str, args)], capture_output=True, text=True,
                          timeout=timeout, check=True).stdout


def put_sample(link, slot, name=b"KICK"):
    """ein Sample über SMP_BEGIN/WRITE/END in den Slot (Kopf wie samples.py: SMPF, 1 Zone)"""
    data = bytes((i * 7) & 255 for i in range(1000))
    hdr = bytearray(480)
    struct.pack_into("<IHB", hdr, 0, 0x504D5346, 1, 1)
    hdr[8:8 + len(name)] = name
    struct.pack_into("<II", hdr, 16, len(data), crc32(data))
    link.request(11, [slot])
    from fm1companion.protocol import pack7
    for off in range(0, len(data), 256):
        o = 512 + off
        link.request(12, [slot, o & 127, o >> 7 & 127, o >> 14 & 127, *pack7(data[off:off + 256])])
    assert link.request(13, [slot, *pack7(bytes(hdr))])[-1] == 0


# ------------------------------------------------------------ Protokoll ---

@need_editor
def test_requests_byte_exact_like_editor():
    js = json.loads(node("reqs"))
    d = bytes((i * 29 + 200) & 255 for i in range(20))
    py = [sloopbk.req_list(), sloopbk.req_get(33, 70000, 256), sloopbk.req_get(2, 512, 77),
          sloopbk.req_begin(6, 3080, 0xDEADBEEF), sloopbk.req_data(6, 2816, d), sloopbk.req_commit(6),
          sloopbk.req_abort(11)]
    assert [bytes([c, *a]).hex() for c, a in py] == js


def test_wire_frames_and_parsers():
    dev, link, t = sloop()
    man = backup.list_objects(link)
    assert t.sent[-1] == frame(34, [])
    assert all(o.proto == "sloop" for o in man)
    assert [o.id for o in man] == [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 32, 33, 34, 35]      # 10/11: nur wenn belegt
    o = next(o for o in man if o.id == 2)
    backup.read_object(link, o)
    gets = [f for f in t.sent if f[4] == 35]
    assert gets[0] == frame(35, [2, 0, 0, 0, 0, 0, 0, 2]) and gets[1] == frame(35, [2, 0, 2, 0, 0, 0, 0, 2])
    assert len(gets) == (o.size + 255) // 256
    assert sloopbk.parse_put([2, 6, 3]) == {"op": 2, "id": 6, "rc": 3}


def test_base64_lenient_like_editor():
    raw = bytes(range(256)) * 3
    s = sloopbk.b64enc(raw)
    assert s == base64.b64encode(raw).decode()
    assert sloopbk.b64dec(s) == raw and sloopbk.b64dec(s[:40] + "\n " + s[40:]) == raw
    assert sloopbk.b64dec("") == b""


def test_demo_speaks_only_its_own_protocol():
    _, link, _ = sloop()
    with pytest.raises(LinkError):
        link.request(65, [], timeout=0.3, retries=0)                 # Melodees BACKUP_LIST: SLOOP schweigt
    mel = Link(LocalTransport(FakeFM1()))
    with pytest.raises(LinkError):
        mel.request(34, [], timeout=0.3, retries=0)
    assert backup.protocol(mel)[0] == "felucca" and backup.protocol(link)[0] == "sloop"
    assert backup.protocol(link, "sloop")[1].version == "FELUCCA SLOOP 2.8.3"
    assert sloopbk.info_proto(backup.protocol(link)[1].raw) == 12


def test_old_sloop_without_backup_is_explained():
    dev, link, _ = sloop()
    orig = dev._handle
    dev._handle = lambda c, a: orig(c, a)[:-1] + [5] if c == 1 else orig(c, a)   # INFO: Protokoll 5 (2.0–2.2)
    with pytest.raises(backup.BackupError, match="ab SLOOP 2.3"):
        backup.list_objects(link)


# ---------------------------------------------------- Rundreise (Demo) ---

def test_capture_restore_roundtrip_demo(tmp_path):
    dev, link, _ = sloop()
    put_sample(link, 1)
    f = backup.capture(link, "egal", companion={"firmware": "sloop", "device": "B"})
    assert f["format"] == "sloop-backup" and f["version"] == 1 and f["firmware"] == "FELUCCA SLOOP 2.8.3"
    assert list(f)[:5] == ["format", "version", "firmware", "date", "objects"] and f["companion"]["device"] == "B"
    assert f["date"].endswith("Z") and len(f["date"]) == 24
    by = {o["id"]: o for o in f["objects"]}
    assert set(by[33]) == {"id", "len", "crc", "data"} and by[33]["len"] == 512 + 1000 and by[35]["len"] == 0
    p = tmp_path / "b.json"
    backup.write_file(p, f)
    assert p.read_text() == json.dumps(f, indent=1, ensure_ascii=False)
    arch = backup.load_file(p)
    assert arch.sloop and arch.created == f["date"]
    before = {k: v for k, v in dev.objs.items()}
    # Gerät verändern …
    dev.objs[2], dev.objs[3], dev.objs[6] = b"", sloop_project(77), b""
    dev.objs[0] = sloop_project(78)
    link.request(14, [1])                                            # USR2 leeren
    dev.log.clear()
    backup.restore(link, arch)
    # … SLOOPs Reihenfolge: Banken, Sections, laufendes Projekt, Einstellungen (dann Samples über SMP_*)
    assert dev.log == [6, 7, 8, 9, 10, 2, 3, 4, 5, 0, 1]
    assert {k: dev.objs[k] for k in (0, 1, 2, 3, 6, 7, 9)} == {k: before[k] for k in (0, 1, 2, 3, 6, 7, 9)}
    g = backup.capture(link, "")
    assert [(o["id"], o["len"], o["crc"], o["data"]) for o in g["objects"]] == \
        [(o["id"], o["len"], o["crc"], o["data"]) for o in f["objects"]]


def test_song_playing_rc3_writes_nothing():
    dev, link, _ = sloop()
    arch = backup.read(backup.capture(link, ""))
    dev.objs[2] = b""
    dev.playing = True
    dev.log.clear()
    with pytest.raises(backup.BackupError) as e:
        backup.restore(link, arch)
    assert e.value.code == "bkStop" and "STOP" in str(e.value) and "nichts geschrieben" in str(e.value)
    assert dev.log == [] and dev.objs[2] == b""
    # nur die Einstellungen: geht auch beim Spielen, solange die Song-Reihenfolge gleich bleibt
    assert backup.restore_selected(link, arch, [1]) == [1]
    s = bytearray(arch.objects[1]["bytes"])
    s[51] = 2                                                         # Section A: 2 statt 4 Takte
    f2 = dict(arch.file, objects=[dict(o, data=sloopbk.b64enc(bytes(s)), crc=crc32(bytes(s))) if o["id"] == 1
                                  else o for o in arch.file["objects"]])
    with pytest.raises(backup.BackupError, match="STOP"):
        backup.restore_selected(link, backup.read(f2), [1])
    dev.playing = False
    assert backup.restore_selected(link, backup.read(f2), [1]) == [1] and dev.objs[1] == bytes(s)


def test_rc3_in_the_middle_names_what_was_written():
    dev, link, _ = sloop()
    arch = backup.read(backup.capture(link, ""))
    orig = dev._sloop_commit

    def commit():
        if dev.bk_put["id"] == 2:
            dev.playing = True                                       # jemand drückt PLAY mitten im Zurückspielen
        return orig()
    dev._sloop_commit = commit
    with pytest.raises(backup.BackupError) as e:
        backup.restore(link, arch)
    assert e.value.code == "bkStop" and e.value.written == [6, 7, 8, 9, 10]
    assert "Schon geschrieben" in str(e.value) and "Preset-Bank 1–16" in str(e.value)


def test_damaged_and_invalid_objects():
    dev, link, _ = sloop()
    f = backup.capture(link, "")
    bad = json.loads(json.dumps(f))
    d = bytearray(sloopbk.b64dec(bad["objects"][2]["data"]))
    d[3] ^= 1
    bad["objects"][2]["data"] = sloopbk.b64enc(bytes(d))
    with pytest.raises(backup.BackupError):
        backup.read(bad)                                              # vor dem ersten Schreiben abgelehnt
    nope = json.loads(json.dumps(f))
    nope["objects"] = [o for o in nope["objects"] if o["id"] != 1]
    with pytest.raises(backup.BackupError, match="unvollständig"):
        backup.read(nope)
    # gültige Datei, aber ein Projekt mit falscher Summe: Commit rc 2, abort-frei, Meldung auf Deutsch
    p = bytearray(sloop_project(5))
    p[100] ^= 0xFF
    f3 = json.loads(json.dumps(f))
    for o in f3["objects"]:
        if o["id"] == 2:
            o.update(len=len(p), crc=crc32(bytes(p)), data=sloopbk.b64enc(bytes(p)))
    with pytest.raises(backup.BackupError, match="Section A: vom FM-1 abgelehnt"):
        backup.restore_selected(link, backup.read(f3), [2])


def test_snapshot_gone_is_stale():
    dev, link, _ = sloop()
    man = backup.list_objects(link)
    link.request(*sloopbk.req_begin(2, 10, 0))                       # ein Restore beginnt: Schnappschuss weg
    with pytest.raises(backup.BackupError) as e:
        backup.read_object(link, man[0])
    assert e.value.code == "bkStale"


def test_newer_project_format_refused_before_writing():
    dev, link, _ = sloop()
    f = backup.capture(link, "")
    p = bytearray(sloop_project(3))
    p[0] = 0x37                                                       # "FUN7"
    for o in f["objects"]:
        if o["id"] == 2:
            o.update(len=len(p), crc=crc32(bytes(p)), data=sloopbk.b64enc(bytes(p)))
    dev.log.clear()
    with pytest.raises(backup.BackupError) as e:
        backup.restore(link, backup.read(f))
    assert e.value.code == "bkNewer" and dev.log == []


def test_midi_learn_default_added_for_25_backups():
    m = {0: b"x", 1: b"y"}
    out = sloopbk.check_restore(m, {"firmware": "FELUCCA SLOOP 2.5.1"}, None)
    assert out[10] == bytes(32) + bytes([1, 2, 3, 10, 2]) + bytes(3)
    assert 10 not in sloopbk.check_restore(m, {"firmware": "FELUCCA SLOOP 2.4"}, None)
    assert sloopbk.fw_at_least("SLOOP 2.10", (2, 5, 1)) and not sloopbk.fw_at_least("x", (1, 0, 0))


def test_formats_must_match_device():
    _, slink, _ = sloop()
    mlink = Link(LocalTransport(FakeFM1()))
    mel = backup.read(backup.capture(mlink, "Melodee"))
    slo = backup.read(backup.capture(slink, ""))
    with pytest.raises(backup.BackupError, match="läuft aber SLOOP"):
        backup.restore(slink, mel)
    with pytest.raises(backup.BackupError, match="SLOOP-Backup"):
        backup.restore_selected(mlink, slo, [2])


# --------------------------------------------- Projekte, Browser, Dateien ---

def test_projects_on_sloop(tmp_path):
    dev, link, _ = sloop()
    slots, live = projects.list_slots(link)
    assert [s.slot for s in slots] == [1, 2, 3, 4] and [s.used for s in slots] == [True, False, False, False]
    assert live == 3816
    p = projects.export_slot(link, 1, tmp_path, "Groove", "FELUCCA SLOOP 2.8.3")
    pf = projects.load_project(p)
    assert pf.meta["kind"].startswith("FUN6") and pf.data == dev.objs[2]
    projects.import_to_slot(link, pf, 4)
    assert dev.objs[5] == pf.data
    projects.erase_slot(link, 4)
    assert dev.objs[5] == b""
    with pytest.raises(backup.BackupError, match="1–4"):
        projects.import_to_slot(link, pf, 7)
    dev.playing = True
    with pytest.raises(backup.BackupError, match="STOP"):
        projects.import_to_slot(link, pf, 3)


def test_inspect_plan_and_selective_restore():
    dev, link, _ = sloop()
    arch = backup.read(backup.capture(link, "", companion={"firmware": "sloop"}))
    rep = BI.inspect(arch)
    assert rep.restorable and rep.format == "sloop-backup" and rep.firmware_key == "sloop"
    names = [p.name for p in rep.used_presets]
    assert names == ["ACID LINE", "SLOOP KEYS", "GRAIN PAD"] and rep.used_presets[1].engine_name == "FM6"
    assert rep.obj(8) in BI.selectable(rep)                         # (bei Melodee ausgenommen)
    dev.objs[2] = b""
    dev.objs[6] = b""
    cur = BI.inspect(backup.capture_live(link, ""))
    man = [backup.ObjInfo(o.id, o.size, o.crc) for o in cur.objects]
    plan = BI.plan_restore(rep, [1, 2, 6, 3, 0], man, cur)
    assert plan.ok and plan.ids() == [6, 2] and {i.id for i in plan.skipped} == {0, 1, 3}
    assert any("STOP" in n for n in plan.notes)
    dev.log.clear()
    assert backup.restore_selected(link, arch, plan.ids(), expect=plan.expect) == [6, 2]
    assert dev.log == [6, 2] and dev.objs[2] == arch.objects[2]["bytes"]
    # Gerät inzwischen verändert -> nichts schreiben
    dev.objs[3] = sloop_project(9)
    exp = {3: (0, 0)}
    with pytest.raises(backup.BackupError, match="verändert"):
        backup.restore_selected(link, arch, [3], expect=exp)
    # Melodee -> SLOOP verweigert, SLOOP -> SLOOP erlaubt (auch MIDI-Learn, das nicht im Inventar steht)
    c = BI.compatibility(rep, "sloop", "FELUCCA SLOOP 2.8.3", [o.id for o in man])
    assert c.ok and {8, 10, 11} & c.allowed_ids == {8}


def test_autobackup_file_and_meta_keep_editor_format(tmp_path):
    dev, link, _ = sloop()
    p = autobackup.capture_to_file(link, tmp_path, None, "sloop", "FELUCCA SLOOP 2.8.3", "Notiz", "auto")
    f = json.loads(p.read_text())
    assert f["format"] == "sloop-backup" and p.read_text() == sloopbk.dumps(f)
    autobackup.set_meta(p, note="neu", pinned=True)
    g = json.loads(p.read_text())
    assert g["companion"]["note"] == "neu" and p.read_text() == sloopbk.dumps(g)
    m = autobackup.read_meta(p)
    assert m.fw_key == "sloop" and m.projects == 1 and m.fmt == "sloop-backup"


# ------------------------------------------- gegen den SLOOP-Editor selbst ---

@need_editor
def test_file_text_byte_exact_with_editor(tmp_path):
    _, link, _ = sloop()
    f = backup.capture(link, "")
    f["firmware"] = "FELUCCA SLOOP 2.8.3 – ä"                         # auch Nicht-ASCII wie JSON.stringify
    p = tmp_path / "py.json"
    p.write_text(sloopbk.dumps(f), "utf-8")
    assert node("stringify", p) == p.read_text("utf-8")
    sizes = json.loads(node("objects", p))
    assert {int(k): v for k, v in sizes.items()} == {o["id"]: o["len"] for o in f["objects"]}


@need_editor
def test_roundtrip_against_editor_mock(tmp_path):
    # 1. der Editor sichert seinen Mock (mit Preset, Projekt in C, Sample in USR2)
    js = json.loads(node("capture"))
    ids = [o["id"] for o in js["objects"]]
    assert ids[:2] == [0, 1] and 33 in ids
    arch = backup.read(js)                                            # die App liest Editor-Dateien
    assert BI.inspect(arch).restorable
    # 2. die App spielt es über die Befehle 34–36 (+ SMP_*) in einen frischen Mock zurück und liest wieder
    t = SubprocessTransport([NODE, str(MOCK), str(EDITOR)])
    link = Link(t)
    try:
        assert backup.protocol(link)[0] == "sloop"
        backup.restore(link, arch)
        back = backup.capture(link, "")
    finally:
        link.close() if hasattr(link, "close") else None
        t.close()
    strip = lambda f: [(o["id"], o["len"], o["crc"], o["data"]) for o in f["objects"]]   # noqa: E731
    assert strip(back) == strip(js)
    # 3. und der Editor spielt die Datei der App zurück: gleicher Stand
    p = tmp_path / "app.json"
    backup.write_file(p, back)
    again = json.loads(node("restore", p))
    assert strip(again) == strip(js)
