# SPDX-License-Identifier: GPL-3.0-only
"""Studio twin: builds/versions, the firmware core in node (tests/studio_twin.mjs) and the Python facade
(fm1companion/studio.py) with the node backend – read/play in a backup with the suite's code, rendering, stems,
Studio state, version choice. The QtWebEngine page is checked by tests/ui_studio_test.py.

  python3 -m pytest tests/test_studio.py -q
"""
from __future__ import annotations

import base64
import hashlib
import json
import shutil
import struct
import subprocess
import sys
import wave
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fm1companion import backup, sims, studio  # noqa: E402
from fm1companion.demo_device import _identity_frame  # noqa: E402
from fm1companion.protocol import Cmd  # noqa: E402

NODE = shutil.which("node") or ("/opt/node22/bin/node" if Path("/opt/node22/bin/node").exists() else None)
need_node = pytest.mark.skipif(not NODE, reason="node missing")
FIX = ROOT / "tests" / "fixtures" / "musik"
# tools/fm1pkg_make.py release_product: 1.6.0(-rc.x) -> FM-1_91060, 1.6.1 -> FM-1_91061
IDENTITY = {"v1.6.0-rc.7": "FM-1_91060", "v1.6.0-rc.8": "FM-1_91060", "v1.6.0": "FM-1_91060", "v1.6.1": "FM-1_91061"}


# ------------------------------------------------------------------ builds
def test_builds_side_by_side():
    bs = studio.builds("melodee")
    vs = [b.version for b in bs]
    assert {"v1.6.0-rc.7", "v1.6.0-rc.8", "v1.6.0", "v1.6.1"} <= set(vs) and vs == sorted(vs, key=studio.version_key)
    for b in bs:
        m = b.manifest
        assert b.verify() == []
        assert m["kind"] == "studio" and m["abi"] == studio.STUDIO_ABI and m["firmware"] == "melodee"
        assert m["ref"] == b.version and len(m["commit"]) == 40 and m["license"] == "GPL-3.0-only"
        assert m["identity"] == IDENTITY[b.version] and m["fs"] == 44100 and m["flash_size"] == 0x100000
        assert {"LICENSE", "NOTICE", "twin.wasm"} <= set(m["files"])
        assert "unmodified" in (b.path / "NOTICE").read_text() and m["glue"] == "tools/studio/melodee_studio.c"
        assert m["glue_sha256"] == hashlib.sha256((ROOT / m["glue"]).read_bytes()).hexdigest()
    # the Studio builds are not simulator pages
    assert not any(s.key.endswith("-studio") for s in sims.bundled())
    for f in ("twin-core.js", "twin-host.js", "studio-worker.js", "studio-worklet.js", "studio.html", "studio.js"):
        assert (studio.HOST_DIR / f).exists()


def test_versions_and_pick():
    assert studio.norm_version("MELODEE v1.6.0-rc.7") == "v1.6.0-rc.7"
    assert studio.norm_version("Melodee 1.6.0 rc8") == "v1.6.0-rc.8"
    assert studio.norm_version("v1.6") == "v1.6.0" and studio.norm_version("") == ""
    assert studio.version_key("v1.6.0-rc.8") < studio.version_key("v1.6.0") < studio.version_key("v1.6.1-rc.1")
    b, exact, warn = studio.pick_build("melodee", "MELODEE v1.6.0-rc.7")
    assert exact and b.version == "v1.6.0-rc.7" and not warn
    b, exact, warn = studio.pick_build("melodee", "MELODEE v1.6.0")     # the final 1.6.0: exactly that one
    assert exact and b.version == "v1.6.0" and not warn
    b, exact, warn = studio.pick_build("melodee", "MELODEE v1.6.1")     # 1.6.1: exactly that one
    assert exact and b.version == "v1.6.1" and not warn
    b, exact, warn = studio.pick_build("melodee", "v1.6.2")             # newer than everything: the next older one
    assert not exact and b.version == "v1.6.1" and "v1.6.2" in warn and "abweichen" in warn
    b, exact, warn = studio.pick_build("melodee", "v1.5.2")             # older than everything: the oldest
    assert not exact and b.version == "v1.6.0-rc.7"
    assert studio.pick_build("melodee")[0].version == "v1.6.1"          # without a wish: the newest
    with pytest.raises(studio.StudioError):
        studio.pick_build("no-such-firmware")                         # (SLOOP has a twin now)
    vc = studio.check_versions("v1.6.0-rc.8", device="MELODEE v1.6.0-rc.7")
    assert not vc.ok and vc.message == "Studio läuft mit Melodee v1.6.0-rc.8, dein FM-1 hat v1.6.0-rc.7 – Klang kann abweichen."
    assert studio.check_versions("v1.6.0-rc.8", "v1.6.0-rc.8", "MELODEE v1.6.0-rc.8").ok
    assert studio.check_versions("v1.6.0", "MELODEE v1.6.0", "v1.6.0").ok
    vc = studio.check_versions("v1.6.0", device="MELODEE v1.6.0-rc.8")   # pre-release -> 1.6.0: levels differ
    assert not vc.ok and "dein FM-1 hat v1.6.0-rc.8" in vc.message and "CZ-1" in vc.message
    vc = studio.check_versions("v1.6.0-rc.8", backup="MELODEE v1.6.0")
    assert not vc.ok and "CZ-1" in vc.message


def test_wav_header():
    h = studio.wav_header(10, "s24")
    assert h[:4] == b"RIFF" and h[8:16] == b"WAVEfmt " and struct.unpack("<HHIIHH", h[20:36]) == (1, 2, 44100, 264600, 6, 24)
    h = studio.wav_header(10, "f32")
    assert struct.unpack("<HH", h[20:24]) == (3, 2) and b"fact" in h and h.endswith(struct.pack("<I", 80))


# ------------------------------------------------------------------ the firmware core (node)
@need_node
def test_twin_core_headless():
    r = subprocess.run([NODE, str(ROOT / "tests" / "studio_twin.mjs"), str(studio.SIMS_DIR / "melodee-studio"), str(FIX)],
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-3000:]
    res = json.loads(r.stdout)
    assert {x["version"] for x in res} >= {"1.6.0-rc.7", "1.6.0-rc.8", "1.6.0", "1.6.1"}
    expected = json.loads((FIX / "expected.json").read_text())["live"]
    for x in res:
        v = x["firmware_version"]
        assert x["version_export"] == v and x["info"] == "MELODEE " + v and x["ping"] == [0]
        assert bytes(x["identity_reply"]) == _identity_frame(IDENTITY[v])   # ota.c, as demo_device expects
        assert x["objects"] == 57 and all(rc == 0 for rc in x["put_rc"].values())
        assert x["identical"]["2"] and x["identical"]["64"] and x["running_music_roundtrip"] and x["persisted"]
        first = expected["tracks"][0]["banks"][0]["steps"]["0"]
        assert x["live_step0"] == {"note": first["notes"][0], "vel": first["vel"]}
        assert (x["bpm"][2] | x["bpm"][3] << 7) - 8192 == expected["bpm"]
        assert x["projects_used"] == [0, 4]
        a, b = x["takes"]
        assert a["playing"] and a["peak"] > 0.05 and a["nonFinite"] == 0 and a["hash"] == b["hash"]
        assert x["fork_identical"] and x["stopped"]
        print(f"{v}: {a['framesPerSec']:.0f} frames/s = {a['realtime']:.1f}x real time (4 tracks)")
        assert a["realtime"] > 3


# ------------------------------------------------------------------ facade + suite code (node backend)
def _song(st):
    L = st.link
    for k in range(4):
        for i in range(0, 16, 2 + k):
            L.request(Cmd.TRACK_STEP, [k, i, 1, 36 + 12 * (k % 3) + (i % 5), 0, 0, 0, 0, 0, 100])
    assert L.request(Cmd.PROJECT, [1, 0])[2] == 1
    L.request(33, [1, 2, 0, 1, 0, 1])                    # song chain: 2 rows of 1 bar each


@pytest.fixture
def node_studio():
    if not NODE:
        pytest.skip("node missing")
    made = []

    def make(version=None, firmware="melodee", **kw):
        if version is None:
            version = "v1.6.0-rc.8" if firmware == "melodee" else ""
        st = studio.Studio.create(firmware=firmware, version=version, backend="node", **kw)
        made.append(st)
        return st
    yield make
    for st in made:
        st.close()


def test_backup_round_trip_with_suite_code(node_studio):
    a = node_studio()
    _song(a)
    for oid, fx in ((3, 2), (64, 64)):                    # Melodee's own project objects (fixture) -> project 2, 5
        backup.write_object(a.link, oid, (FIX / f"obj_{fx}.bin").read_bytes(), proto=backup.FELUCCA)
    arch = a.backup()
    assert studio.backup_version(arch) == "v1.6.0-rc.8"
    sizes = {o["id"]: o["size"] for o in arch.objects if o["size"]}
    assert set(sizes) >= {0, 1, 2, 3, 64}
    text = backup.dumps(arch.file)
    b = node_studio()
    b.open_from_backup(text)                              # via backup.restore = editor protocol
    arch2 = b.backup()
    for o1, o2 in zip(arch.objects, arch2.objects):
        assert o1["id"] == o2["id"] and o1["bytes"] == o2["bytes"], o1["id"]
    assert b.projects() == [0, 1, 4]
    # choose the matching version
    c = studio.Studio.for_backup(arch, backend="node")
    try:
        assert c.build.version == "v1.6.0-rc.8" and not c.warning and c.projects() == [0, 1, 4]
    finally:
        c.close()


def test_v160_twin_song_project_and_older_backup(node_studio):
    """1.6.0 twin: accepts the 1.6.0 song project from Melodee's own code (16 patterns, 64 sections,
    punch effects, song/pattern presets), gives the backup back unchanged; it plays in an rc.8 backup
    ("Older archives restore here"), and the suite reads the project from the twin as from the device."""
    from fm1companion import projectfmt as PF
    new = node_studio("v1.6.0")
    assert new.build.version == "v1.6.0" and not new.warning and new.info.get("version") in ("v1.6.0", "MELODEE v1.6.0")
    song16 = (ROOT / "tests" / "fixtures" / "musik16" / "obj_0.bin").read_bytes()
    backup.write_object(new.link, 0, song16, proto=backup.FELUCCA)
    arch = new.backup()
    assert studio.backup_version(arch) == "v1.6.0" and len(arch.objects) == 57
    live = next(o for o in arch.objects if o["id"] == 0)["bytes"]
    prj = PF.decode(live, arch.firmware)
    assert prj.ok and [len(t.patterns) for t in prj.tracks[:4]] == [16] * 4
    assert all(sum(p.used for p in t.patterns) == 16 for t in prj.tracks[:4])
    assert len(prj.song) == 64 and sum(len(r.punches) for r in prj.song) == 6 and prj.sounds and prj.pattern_sounds
    old = node_studio("v1.6.0-rc.8")
    _song(old)
    new.open_from_backup(old.backup())                    # rc.8-Backup -> 1.6.0
    assert new.projects() == [0]
    vc = new.check_versions(backup="MELODEE v1.6.0-rc.8")
    assert not vc.ok and "CZ-1" in vc.message


def _clk(L):
    a = L.request(Cmd.GET, [1, 2])
    return (a[2] | a[3] << 7) - 8192


def test_v161_twin_keeps_clk_on_project_load(node_studio):
    """1.6.1 twin (project.c project_restore_runtime without G_CLOCK, editor_backup.c ed_bk_clock): loading a project
    keeps the device's CLK – 1.6.0 took the project's; a backup brings CLK back. Data as in 1.6.0."""
    from fm1companion.protocol import v14
    res = {}
    for ver in ("v1.6.0", "v1.6.1"):
        st = node_studio(ver)
        assert st.build.version == ver and not st.warning
        L = st.link
        L.request(Cmd.SET, [1, 2, *v14(1)])                  # CLK USB
        assert _clk(L) == 1
        assert L.request(Cmd.PROJECT, [1, 0])[2] == 1        # save project 1 with CLK USB
        L.request(Cmd.SET, [1, 2, *v14(0)])                  # CLK INT
        L.request(Cmd.PROJECT, [0, 0])                       # load project 1
        res[ver] = _clk(L)
    assert res == {"v1.6.0": 1, "v1.6.1": 0}
    new = node_studio("v1.6.1")
    song16 = (ROOT / "tests" / "fixtures" / "musik16" / "obj_0.bin").read_bytes()
    backup.write_object(new.link, 0, song16, proto=backup.FELUCCA)   # the 1.6.0 song project: format unchanged
    new.link.request(Cmd.SET, [1, 2, *v14(1)])
    arch = new.backup()
    assert studio.backup_version(arch) == "v1.6.1" and len(arch.objects) == 57
    new.link.request(Cmd.SET, [1, 2, *v14(0)])
    new.open_from_backup(arch)
    assert _clk(new.link) == 1                               # the backup restores CLK
    old = node_studio("v1.6.0")
    old.open_from_backup(arch)                               # 1.6.0 reads backups of 1.6.1
    assert old.projects() == new.projects()


def test_render_stems_and_state(node_studio, tmp_path):
    st = node_studio()
    _song(st)
    r = st.render_wav(tmp_path / "song.wav", until_end=True, start="song", fmt="s24", max_seconds=60)
    assert r.ended and 6.5 < r.seconds < 7.5 and r.peak > 0.05      # 2 bars at 120 BPM + 2 s release
    with wave.open(str(r.path)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()) == (44100, 2, 3, r.frames)
    a = st.render_wav(tmp_path / "a.wav", seconds=2, start="play", source="saved", project=0)
    b = st.render_wav(tmp_path / "b.wav", seconds=2, start="play", source="saved", project=0)
    assert a.path.read_bytes() == b.path.read_bytes() and a.peak > 0.05
    data = a.path.read_bytes()
    assert struct.unpack("<H", data[20:22])[0] == 3 and len(data) == len(studio.wav_header(a.frames)) + a.frames * 8
    s = st.render_wav(tmp_path / "mix.wav", seconds=2, start="play", source="saved", project=0, stems=True)
    assert sorted(s.stems) == [0, 1, 2, 3]
    peaks = []
    for k, p in s.stems.items():
        raw = p.read_bytes()[len(studio.wav_header(s.frames)):]
        fl = struct.unpack(f"<{len(raw) // 4}f", raw)
        peaks.append(max(abs(x) for x in fl))
    assert all(pk > 0.01 for pk in peaks)                 # every track plays something
    # Studio state
    path = st.save_state(tmp_path / "x" / studio.STATE_NAME, note="Test")
    meta, flash = studio.read_state(path)
    assert meta["firmware_version"] == "v1.6.0-rc.8" and len(flash) == 0x100000
    other = node_studio("v1.6.0-rc.7")
    m = other.load_state(path)
    assert other.projects() == [0] and "rc.8" in m["warning"]
    with pytest.raises(studio.StudioError):
        studio.read_state(tmp_path / "song.wav")


def test_state_path_per_profile(monkeypatch, tmp_path):
    from fm1companion import config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    assert studio.state_path("Mein FM-1") == tmp_path / "studio" / "Mein FM-1" / studio.STATE_NAME

    class P:
        folder_name = "Bühne"
    assert studio.state_path(P()) == tmp_path / "studio" / "Bühne" / studio.STATE_NAME
    assert studio.state_path() == tmp_path / "studio" / "Studio" / studio.STATE_NAME


def test_midi_in_and_versions_through_link(node_studio, tmp_path):
    st = node_studio("v1.6.0-rc.7")
    assert studio.device_version(st.link) == "v1.6.0-rc.7"
    assert st.version_info()["identity"] == "FM-1_91060"
    vc = st.check_versions(device="MELODEE v1.6.0-rc.8")
    assert not vc.ok and "dein FM-1 hat v1.6.0-rc.8" in vc.message
    st.note(60, 110)                                      # like playback.py: note via the link
    r = st.render_wav(tmp_path / "note.wav", seconds=0.5, start="none", fmt="f32")
    assert r.peak > 0.05


@need_node
def test_twin_as_subprocess_device():
    """The twin as an 'FM-1 on a line' (transport.SubprocessTransport, like the demo devices)."""
    from fm1companion.transport import Link, SubprocessTransport
    b = studio.pick_build("melodee", "v1.6.0-rc.7")[0]
    t = SubprocessTransport([NODE, str(ROOT / "tests" / "studio_twin_device.mjs"), str(b.wasm)])
    try:
        assert t.wait_started(20)
        link = Link(t)
        assert studio.device_version(link) == "v1.6.0-rc.7" and link.ping()
        objs = backup.list_objects(link, "melodee")
        assert len(objs) == 57 and objs[0].id == 0 and objs[0].size > 0
    finally:
        t.close()


# ------------------------------------------------------------------ Skiff (tools/studio/skiff_studio.c)
SKIFF_IDENTITY = {"v2.1.1": "FM-1_721", "v2.2": "FM-1_722"}     # Skiff's tools/build.py --release X.Y: FM-1_7XY


def test_skiff_builds_side_by_side():
    bs = studio.builds("skiff")
    vs = [b.version for b in bs]
    assert {"v2.1.1", "v2.2"} <= set(vs) and vs == sorted(vs, key=studio.version_key)
    for b in bs:
        m = b.manifest
        assert b.verify() == []
        assert m["kind"] == "studio" and m["abi"] == studio.STUDIO_ABI and m["firmware"] == "skiff"
        assert m["ref"] == b.version and len(m["commit"]) == 40 and m["license"] == "GPL-3.0-only"
        assert m["identity"] == SKIFF_IDENTITY[b.version] and m["fs"] == 44100 and m["flash_size"] == 0x100000
        assert m["tracks"] == 6 and m["projects"] == 4 and m["source_repo"] == "https://github.com/illuminus5/skiff"
        assert {"LICENSE", "NOTICE", "LICENSING.md", "THIRD_PARTY.txt", "twin.wasm"} <= set(m["files"])
        notice = (b.path / "NOTICE").read_text()
        assert "unmodified" in notice and "Written offer" in notice and f"skiff-{m['commit']}.tar.gz" in notice
        assert "illuminus5" in notice and m["glue"] == "tools/studio/skiff_studio.c"
        assert m["glue_sha256"] == hashlib.sha256((ROOT / m["glue"]).read_bytes()).hexdigest()
        third = (b.path / "THIRD_PARTY.txt").read_text()
        assert "DaisySP" in third and "Apache License" in third and "Versilian" in third
    assert not any(s.key.startswith("skiff") for s in sims.bundled())
    # the Melodee twins are untouched by the Skiff entry
    assert {b.firmware for b in studio.builds("melodee")} == {"melodee"}


def test_skiff_versions_and_pick():
    b, exact, warn = studio.pick_build("skiff", "FELUCCA v2.2")            # Skiff answers INFO like Felucca
    assert exact and b.version == "v2.2" and not warn
    b, exact, warn = studio.pick_build("skiff", "v2.1.1")
    assert exact and b.version == "v2.1.1"
    b, exact, warn = studio.pick_build("skiff", "v2.3")                    # newer: the next older one
    assert not exact and b.version == "v2.2" and "Skiff" in warn
    b, exact, warn = studio.pick_build("skiff", "v2.0.3")                  # older than everything: the oldest
    assert not exact and b.version == "v2.1.1"
    assert studio.pick_build("skiff")[0].version == "v2.2"
    assert studio.check_versions("v2.2", device="FELUCCA v2.2", firmware="Skiff").ok
    vc = studio.check_versions("v2.2", device="FELUCCA v2.1.1", firmware="Skiff")
    assert not vc.ok and "Skiff" in vc.message and "CZ-1" not in vc.message
    assert studio.firmware_name("skiff") == "Skiff" and studio.FIRMWARES["skiff"]["projects"] == 4


@need_node
def test_skiff_twin_core_headless():
    r = subprocess.run([NODE, str(ROOT / "tests" / "studio_twin_skiff.mjs"), str(studio.SIMS_DIR / "skiff-studio")],
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-3000:]
    res = json.loads(r.stdout)
    assert {x["version"] for x in res} >= {"2.1.1", "2.2"}
    for x in res:
        v = x["firmware_version"]
        assert x["version_export"] == v and x["info"] == "FELUCCA " + v and x["ping"] == [0]
        assert bytes(x["identity_reply"]) == _identity_frame(SKIFF_IDENTITY[v])   # ota.c, as demo_device expects
        assert x["ids"] == studio.SKIFF_BACKUP_IDS and x["screen_lit"] > 10000
        assert {0, 1, 2, 9, 16} <= {int(k) for k in x["sizes"]}                 # music, settings, A, FM6, pattern bank
        assert all(x["identical"].values()) and x["persisted"] and x["projects_used"] == [0]
        assert x["live_step0"] == {"note": 40, "vel": 100}
        a, b = x["takes"]
        assert a["playing"] and a["play_lit"] and a["peak"] > 0.05 and a["nonFinite"] == 0 and a["hash"] == b["hash"]
        assert x["fork_identical"] and x["stopped"] and x["editor_play"] and x["editor_stop"]
        if v == "v2.2":
            assert a["note_ons"] > 0                                            # (2.2) USB MIDI out of the tracks
        print(f"{v}: {a['realtime']:.1f}x real time (6 tracks)")
        assert a["realtime"] > 3


def _skiff_song(st):
    L = st.link
    for k in range(6):
        for i in range(0, 16, 2 + k % 3):
            L.request(Cmd.TRACK_STEP, [k, i, 1, 40 + 3 * k + (i % 5), 0, 0, 0, 0, 0, 100])
    assert L.request(Cmd.PROJECT, [1, 0])[2] == 1                          # project A


def test_skiff_backup_round_trip_with_suite_code(node_studio, tmp_path):
    a = node_studio(firmware="skiff")
    assert a.build.firmware == "skiff" and a.build.version == "v2.2" and a.tracks == 6 and a.project_slots == 4
    assert studio.device_version(a.link) == "v2.2.0" and a.info_name == "FELUCCA"
    _skiff_song(a)
    arch = a.backup()
    assert [o["id"] for o in arch.objects] == studio.SKIFF_BACKUP_IDS and arch.known
    assert studio.backup_version(arch) == "v2.2.0" and studio.backup_firmware(arch) == "skiff"
    assert arch.companion["version_string"] == "FELUCCA v2.2"
    text = backup.dumps(arch.file)
    plain = json.loads(text)
    plain.pop("companion")                                                  # a backup of Skiff's own web editor
    assert studio.backup_firmware(backup.read(plain)) == "skiff"
    b = node_studio(firmware="skiff", version="v2.1.1")
    b.open_from_backup(text)                                                # via backup.restore = editor protocol
    for o1, o2 in zip(arch.objects, b.backup().objects):
        assert o1["id"] == o2["id"] and o1["bytes"] == o2["bytes"], o1["id"]
    assert b.projects() == [0]
    vc = b.check_versions(archive=arch)
    assert not vc.ok and "Skiff" in vc.message
    c = studio.Studio.for_backup(arch, backend="node")                     # the backup's firmware and version
    try:
        assert c.build.firmware == "skiff" and c.build.version == "v2.2" and not c.warning and c.projects() == [0]
        assert c.load_project(0) == 0 and c.load_project(1) == 1
        assert c.play()["busy"] and not c.stop()["busy"]                   # st_transport_set (Skiff's TRANSPORT differs)
    finally:
        c.close()
    # the inspector names Skiff's pattern bank
    from fm1companion import backup_inspect as BI
    rep = BI.inspect(arch)
    assert rep.firmware_key == "skiff" and "Pattern" in rep.obj(16).title and "FM6" in rep.obj(9).title


def test_skiff_render_stems(node_studio, tmp_path):
    st = node_studio(firmware="skiff")
    _skiff_song(st)
    s = st.render_wav(tmp_path / "mix.wav", seconds=2, start="play", source="saved", project=0, stems=True)
    assert sorted(s.stems) == [0, 1, 2, 3, 4, 5] and s.peak > 0.05
    peaks = []
    for k, p in s.stems.items():
        raw = p.read_bytes()[len(studio.wav_header(s.frames)):]
        fl = struct.unpack(f"<{len(raw) // 4}f", raw)
        peaks.append(max(abs(x) for x in fl))
    assert all(pk > 0.01 for pk in peaks)                 # every track plays something
    path = st.save_state(tmp_path / "x" / studio.STATE_NAME)
    meta, flash = studio.read_state(path)
    assert meta["firmware"] == "skiff" and meta["firmware_version"] == "v2.2" and len(flash) == 0x100000


def test_skiff_inventory_in_backup_layer():
    """Skiff 2.x has 29 objects like Melodee 1.5, with other IDs (the pattern bank): both inventories are known."""
    assert backup.known_ids(list(range(29))) == list(range(29))
    assert backup.known_ids(studio.SKIFF_BACKUP_IDS) == studio.SKIFF_BACKUP_IDS
    assert backup.known_ids(list(range(28)) + [40]) == []
    assert backup.known_ids(list(range(5))) is None


# ------------------------------------------------------------------ JIANT FM (tools/studio/jiant_studio.c)
# JIANT has no tags: the builds are commits its installer offered (web/STABLE, web/PUBLIC_RELEASE.json: V10.6z2 the public
# stable now; web/EDITIONS.json: V10.60 the stable and V10.61 the beta before); its web installer builds with a plain
# tools/build.py, whose PRODUCT is the package identity FM-1_909
JIANT_COMMIT = {"V10.60": "43b8ea8111949c990bf76523c7d4f4e77b715114", "V10.61": "6c48e2a8e7049c31ab77c57b97c3ef6790d24090",
                "V10.6z2": "cac23c6d0bb9a7f6986eb79bbe42ae710805ce98"}
JIANT_STATUS = {"V10.60": "previous stable", "V10.61": "previous beta", "V10.6z2": "stable"}


def test_jiant_builds_side_by_side():
    bs = studio.builds("jiant")
    vs = [b.version for b in bs]
    assert vs == ["V10.60", "V10.61", "V10.6z2"]                           # JIANT's release order
    for b in bs:
        m = b.manifest
        assert b.verify() == [] and b.path.name == b.version.lstrip("V")
        assert m["kind"] == "studio" and m["abi"] == studio.STUDIO_ABI and m["firmware"] == "jiant"
        assert m["ref"] == m["commit"] == JIANT_COMMIT[b.version] and m["license"] == "GPL-3.0-only"
        assert m["release_status"] == JIANT_STATUS[b.version]
        assert m["identity"] == "FM-1_909" and m["fs"] == 44100 and m["flash_size"] == 0x100000
        assert m["tracks"] == 6 and m["projects"] == 4 and m["source_repo"] == "https://github.com/juanjiant-bit/JIANT"
        assert {"LICENSE", "NOTICE", "LICENSING.md", "THIRD_PARTY.txt", "twin.wasm"} <= set(m["files"])
        notice = (b.path / "NOTICE").read_text()
        assert "unmodified" in notice and "Written offer" in notice and f"JIANT-{m['commit']}.tar.gz" in notice
        assert "juanjiant-bit" in notice and m["glue"] == "tools/studio/jiant_studio.c"
        assert m["glue_sha256"] == hashlib.sha256((ROOT / m["glue"]).read_bytes()).hexdigest()
        third = (b.path / "THIRD_PARTY.txt").read_text()
        assert "Apache License" in third and "SIL OPEN FONT LICENSE" in third.upper() and "Carnegie Mellon" in third
    assert not any(s.key.startswith("jiant") for s in sims.bundled())
    assert {b.firmware for b in studio.builds("melodee")} == {"melodee"}
    assert {b.firmware for b in studio.builds("skiff")} == {"skiff"}


def test_jiant_versions_and_pick():
    b, exact, warn = studio.pick_build("jiant", "JIANT V10.60")           # JIANT's INFO
    assert exact and b.version == "V10.60" and not warn
    b, exact, warn = studio.pick_build("jiant", "V10.61")
    assert exact and b.version == "V10.61"
    b, exact, warn = studio.pick_build("jiant", "JIANT V10.6z2")          # the public stable
    assert exact and b.version == "V10.6z2" and not warn
    b, exact, warn = studio.pick_build("jiant", "JIANT V10.59")           # older than everything: the oldest
    assert not exact and b.version == "V10.60" and "JIANT FM V10.59" in warn
    b, exact, warn = studio.pick_build("jiant", "JIANT V10.6c")           # after V10.61, before V10.6z2
    assert not exact and b.version == "V10.61" and "V10.6c" in warn
    b, exact, warn = studio.pick_build("jiant", "JIANT V10.6z10")         # newer: the next older one
    assert not exact and b.version == "V10.6z2" and "V10.6z10" in warn and "v10.6" not in warn
    assert studio.pick_build("jiant")[0].version == "V10.6z2"              # without a wish: the newest = the stable
    assert studio.pick_build("jiant", studio.norm_version("JIANT V10.6z2"))[0].version == "V10.6z2"   # (device_version)
    # JIANT's release order (its git history): the number after the dot whole, then the letter suffixes after V10.61
    order = ["V10.7", "V10.10", "V10.59", "V10.60", "V10.61", "V10.6c", "V10.6d", "V10.6z", "V10.6z2", "V10.6z9",
             "V10.6z10"]
    assert sorted(reversed(order), key=studio.jiant_version_key) == order
    assert studio.jiant_version("JIANT V10.6z2") == studio.jiant_version("v10.6z2") == "V10.6z2"
    assert studio.jiant_version("v10.60.0") == "V10.60"
    # the generic key and the normaliser are unchanged for Melodee and Skiff
    assert studio.norm_version("MELODEE v1.6.0-rc.7") == "v1.6.0-rc.7" and studio.norm_version("FELUCCA v2.2") == "v2.2.0"
    assert studio.norm_version("Melodee 1.6 rc8") == "v1.6.0-rc.8" and studio.norm_version("JIANT V10.6z2") == "v10.6z2"
    assert studio.fw_version_key("melodee", "v1.6.0-rc.8") == studio.version_key("v1.6.0-rc.8")
    assert studio.check_versions("V10.60", device="JIANT V10.60", firmware="JIANT FM").ok
    vc = studio.check_versions("V10.60", device="JIANT V10.61", firmware="JIANT FM")
    assert not vc.ok and "JIANT FM" in vc.message and "CZ-1" not in vc.message
    vc = studio.check_versions("V10.6z2", device="JIANT V10.6z3", firmware="JIANT FM")
    assert not vc.ok and "V10.6z3" in vc.message and "V10.6z2" in vc.message
    assert studio.firmware_name("jiant") == "JIANT FM" and studio.FIRMWARES["jiant"]["projects"] == 4
    # the device layer: INFO "JIANT V10.60" and the catalog's key "jiant-fm" both select the JIANT twin
    from fm1companion.protocol import Info
    from fm1companion.ui.studio_service import StudioService
    from fm1companion import firmware
    assert Info("JIANT V10.60").firmware_name == "jiant"
    assert StudioService.firmware_for("jiant") == StudioService.firmware_for("jiant-fm") == "jiant"
    assert firmware.guess_firmware("", "JIANT V10.60")[0] == "jiant-fm"
    assert firmware.guess_firmware("FM-1_909")[0] in ("jiant-fm", "")   # ("" if this machine remembered it otherwise)


@need_node
def test_jiant_twin_core_headless():
    r = subprocess.run([NODE, str(ROOT / "tests" / "studio_twin_jiant.mjs"), str(studio.SIMS_DIR / "jiant-studio")],
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-3000:]
    res = json.loads(r.stdout)
    assert {x["version"] for x in res} >= {"10.60", "10.61", "10.6z2"}
    for x in res:
        v = x["firmware_version"]
        assert x["version_export"] == v and x["info"] == "JIANT " + v and x["ping"] == [0]
        assert bytes(x["identity_reply"]) == _identity_frame("FM-1_909")   # ota.c, as demo_device expects
        six = studio.jiant_version_key(v) >= studio.jiant_version_key("V10.6p")   # (V10.6p: 6 songs, 35 objects)
        assert x["ids"] == (backup.JIANT_IDS_6 if six else backup.JIANT_IDS) and x["screen_lit"] > 2000
        assert {0, 1, 2, 9, 10, 40} <= {int(k) for k in x["sizes"]}      # music, settings, A, FM6, song index, song 1 A
        assert all(rc == 0 for rc in x["put_rc"].values())
        assert all(x["identical"].values()) and x["persisted"] and x["projects_used"] == [0]
        assert x["live_step0"] == {"note": 40, "vel": 100}
        a, b = x["takes"]
        assert a["playing"] and a["play_lit"] and a["peak"] > 0.05 and a["nonFinite"] == 0 and a["hash"] == b["hash"]
        assert x["fork_identical"] and x["stopped"] and x["song_play"] and x["editor_stop"]
        print(f"{v}: {a['realtime']:.1f}x real time (6 tracks)")
        assert a["realtime"] > 3


def _jiant_song(st):
    L = st.link
    for k in range(6):
        for i in range(0, 16, 2 + k % 3):
            L.request(Cmd.TRACK_STEP, [k, i, 1, 40 + 3 * k + (i % 5), 0, 0, 0, 0, 0, 100])
    assert L.request(Cmd.PROJECT, [1, 0])[2] == 1                          # section A of the current song


def test_jiant_backup_round_trip_with_suite_code(node_studio):
    a = node_studio(firmware="jiant", version="V10.60")
    assert a.build.firmware == "jiant" and a.build.version == "V10.60" and a.tracks == 6 and a.project_slots == 4
    assert studio.device_version(a.link) == "v10.60.0" and a.info_name == "JIANT"
    _jiant_song(a)
    arch = a.backup()
    assert [o["id"] for o in arch.objects] == studio.JIANT_BACKUP_IDS and arch.known
    assert studio.backup_version(arch) == "v10.60.0" and studio.backup_firmware(arch) == "jiant"
    assert arch.companion["version_string"] == "JIANT V10.60"
    # a backup of JIANT's own web editor (no companion block; its 'firmware' is the INFO string) and one with an
    # unknown name: the inventory tells
    plain = json.loads(backup.dumps(arch.file))
    plain.pop("companion")
    plain["firmware"] = "JIANT V10.60"
    assert studio.backup_firmware(backup.read(plain)) == "jiant"
    plain["firmware"] = "?"
    assert studio.backup_firmware(backup.read(plain)) == "jiant"
    # song 8's sections (ids 68..71, inside Melodee 1.6's project range 64..91): no Melodee 1.6 refusal, restored as
    # sections (not as sample slots)
    f = json.loads(backup.dumps(arch.file))
    src = next(o for o in f["objects"] if o["id"] == 40)
    for o in f["objects"]:
        if o["id"] == 68:
            o.update(size=src["size"], crc=src["crc"], data=src["data"])
    arch8 = backup.read(f)
    b = node_studio(firmware="jiant", version="V10.61")
    b.open_from_backup(arch8)                                                # via backup.restore = editor protocol
    back = {o["id"]: o["bytes"] for o in b.backup().objects}
    for o in arch8.objects:
        if o["id"] != 1:                                                     # (the settings: V10.61 adds its own)
            assert back[o["id"]] == o["bytes"], o["id"]
    assert back[68] == back[40] and b.projects() == [0] and not b.rejected
    vc = b.check_versions(archive=arch)
    assert not vc.ok and "JIANT FM" in vc.message
    c = studio.Studio.for_backup(arch8, backend="node")                     # the backup's firmware and version
    try:
        assert c.build.firmware == "jiant" and c.build.version == "V10.60" and not c.warning and c.projects() == [0]
        assert c.load_project(0) == 0 and c.load_project(1) == 1
        assert c.play()["busy"] and not c.stop()["busy"]                   # st_transport_set
        assert c.play("song")["busy"] and not c.stop()["busy"]
    finally:
        c.close()
    # the tolerant load (the studio page's) skips nothing of its own firmware
    d = node_studio(firmware="jiant", version="V10.60")
    d.open_from_backup(arch8, tolerant=True)
    assert d.rejected == [] and {o["id"]: o["bytes"] for o in d.backup().objects}[68] == back[68]
    # the inspector names JIANT's songs
    from fm1companion import backup_inspect as BI
    rep = BI.inspect(arch8)
    assert rep.firmware_key == "jiant" and BI.inspect(backup.read(plain)).firmware_key == "jiant"
    for oid in (2, 10, 40, 68):
        assert rep.obj(oid).group == BI.G_PROJECTS and "USR" not in rep.obj(oid).title, oid
    assert "8" in rep.obj(68).title and "A" in rep.obj(68).title
    assert BI.compatibility(rep, "jiant-fm", "JIANT V10.60", backup.JIANT_IDS).ok


def test_jiant_render_stems(node_studio, tmp_path):
    st = node_studio(firmware="jiant")
    _jiant_song(st)
    s = st.render_wav(tmp_path / "mix.wav", seconds=2, start="play", source="saved", project=0, stems=True)
    assert sorted(s.stems) == [0, 1, 2, 3, 4, 5] and s.peak > 0.05
    peaks = []
    for k, p in s.stems.items():
        raw = p.read_bytes()[len(studio.wav_header(s.frames)):]
        fl = struct.unpack(f"<{len(raw) // 4}f", raw)
        peaks.append(max(abs(x) for x in fl))
    # T1..T4 play; T5 and T6 are asleep with JIANT's default MENU > TRACKS 4 (voice.c trk_live): silent stems
    assert all(pk > 0.01 for pk in peaks[:4]) and peaks[4:] == [0.0, 0.0], peaks
    path = st.save_state(tmp_path / "x" / studio.STATE_NAME)
    meta, flash = studio.read_state(path)
    assert meta["firmware"] == "jiant" and meta["firmware_version"] == "V10.6z2" and len(flash) == 0x100000


def test_jiant_inventory_in_backup_layer():
    """JIANT FM: 43 objects (0..10, the songs' sections 40..71); 40..63 are sections, not sample slots."""
    ids = backup.JIANT_IDS
    assert backup.known_ids(ids) == ids and backup.is_jiant(ids) and not backup.is_jiant(list(range(43)))
    assert backup.known_ids(list(range(43))) == []
    assert backup.is_sample(40) and not backup.is_sample(40, jiant=True)
    assert not backup.is_project(40) and backup.is_project(40, jiant=True) and backup.is_project(71, jiant=True)
    assert not backup.is_project(72, jiant=True) and not backup.is_project(80, jiant=True)
    assert "USR" not in backup.object_title(41, jiant=True)
    six = backup.JIANT_IDS_6                                                # V10.6p on: 6 songs, 40..63
    assert backup.known_ids(six) == six and backup.is_jiant(six) and studio.JIANT_BACKUP_IDS_6 == six


def test_jiant_six_songs_stable(node_studio):
    """V10.6z2 (the public stable): 35 objects (6 songs); an 8-song archive's songs 7 and 8 do not fit – the tolerant
    load (the studio page's) leaves them out and names them, everything else is taken."""
    old = node_studio(firmware="jiant", version="V10.60")
    _jiant_song(old)
    f = json.loads(backup.dumps(old.backup().file))
    src = next(o for o in f["objects"] if o["id"] == 40)
    for o in f["objects"]:
        if o["id"] == 68:
            o.update(size=src["size"], crc=src["crc"], data=src["data"])
    arch8 = backup.read(f)
    st = node_studio(firmware="jiant", version="V10.6z2")
    assert st.build.version == "V10.6z2" and st.build.manifest["release_status"] == "stable"
    assert studio.device_version(st.link) == "v10.6z2"
    st.open_from_backup(arch8, tolerant=True)
    assert st.rejected == [68] and st.projects() == [0]
    arch6 = st.backup()
    assert [o["id"] for o in arch6.objects] == backup.JIANT_IDS_6 and studio.backup_firmware(arch6) == "jiant"
    by = {o["id"]: o["bytes"] for o in arch6.objects}
    assert by[40] == by[2] and len(by[2]) > 0                  # (V10.6z2 stores V10.60's section in its own format)
    assert "7" in backup.object_title(64, jiant=True)
    c = studio.Studio.for_backup(arch6, backend="node")
    try:
        assert c.build.version == "V10.6z2" and not c.warning and c.projects() == [0]
    finally:
        c.close()


# ------------------------------------------------------------------ SLOOP (tools/studio/sloop_studio.c)
# its release tags; v2.8.5 is the stable its web installer ships (docs/firmware/sloop-2.8.5.fwsc)
SLOOP_COMMIT = {"2.8.4": "91f30182d9095e0ecdb43396554f2ce5a67df179", "2.8.5": "bac9c05ea7627449405aa10e48da21d75b6c8c56"}
SLOOP_IDS = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 32, 33, 34, 35]   # BK_LIST of a new unit (10, 11 only when not default)


def test_sloop_builds_side_by_side():
    bs = studio.builds("sloop")
    vs = [b.version for b in bs]
    assert vs == ["2.8.4", "2.8.5"]
    for b in bs:
        m = b.manifest
        assert b.verify() == [] and b.path.name == b.version and m["firmware_version"] == "SLOOP " + b.version
        assert m["kind"] == "studio" and m["abi"] == studio.STUDIO_ABI and m["firmware"] == "sloop"
        assert m["ref"] == "v" + b.version and m["commit"] == SLOOP_COMMIT[b.version] and m["license"] == "GPL-3.0-only"
        assert m["identity"] == "FM-1_900" and m["fs"] == 44100 and m["flash_size"] == 0x100000
        assert m["tracks"] == 4 and m["projects"] == 4 and m["source_repo"] == "https://github.com/isod89/sloop-fm1"
        assert {"LICENSE", "NOTICE", "LICENSING.md", "THIRD_PARTY.txt", "twin.wasm"} <= set(m["files"])
        notice = (b.path / "NOTICE").read_text()
        assert "unmodified" in notice and "Written offer" in notice and f"sloop-fm1-{m['commit']}.tar.gz" in notice
        assert "isod89" in notice and m["glue"] == "tools/studio/sloop_studio.c"
        assert m["glue_sha256"] == hashlib.sha256((ROOT / m["glue"]).read_bytes()).hexdigest()
        third = (b.path / "THIRD_PARTY.txt").read_text()
        assert "Apache License" in third and "SIL OPEN FONT LICENSE" in third.upper() and "Electrosmith" in third
        assert "Emilie Gillet" in third and "CC0" in third
    assert not any(s.key.startswith("sloop") for s in sims.bundled())
    assert {b.firmware for b in studio.builds("jiant")} == {"jiant"}


def test_sloop_versions_and_pick():
    b, exact, warn = studio.pick_build("sloop", "FELUCCA SLOOP 2.8.5")   # SLOOP's INFO
    assert exact and b.version == "2.8.5" and not warn
    assert studio.pick_build("sloop", "SLOOP 2.8.4")[0].version == "2.8.4"
    assert studio.pick_build("sloop", "v2.8.5")[1]                        # (device_version)
    b, exact, warn = studio.pick_build("sloop", "FELUCCA SLOOP 2.8.3")   # older than everything: the oldest
    assert not exact and b.version == "2.8.4" and "SLOOP v2.8.3" in warn and "2.8.4" in warn
    b, exact, warn = studio.pick_build("sloop", "FELUCCA SLOOP 2.9")      # newer: the next older one
    assert not exact and b.version == "2.8.5"
    assert studio.pick_build("sloop")[0].version == "2.8.5"               # without a wish: the newest = the stable
    assert studio.version_key("SLOOP 2.8.4") < studio.version_key("SLOOP 2.8.5") < studio.version_key("SLOOP 2.10")
    assert studio.check_versions("2.8.5", device="FELUCCA SLOOP 2.8.5", firmware="SLOOP").ok
    vc = studio.check_versions("2.8.5", device="FELUCCA SLOOP 2.8.4", firmware="SLOOP")
    assert not vc.ok and "SLOOP" in vc.message and "CZ-1" not in vc.message
    assert studio.firmware_name("sloop") == "SLOOP" and studio.FIRMWARES["sloop"]["projects"] == 4
    # the device layer: INFO "FELUCCA SLOOP 2.8.5" and the catalog's key "sloop" select the SLOOP twin; the package
    # identity FM-1_900 is Felucca's too (SLOOP's tools/build.py PRODUCT), so INFO decides
    from fm1companion.protocol import Info
    from fm1companion.ui.studio_service import StudioService
    from fm1companion import firmware
    assert Info("FELUCCA SLOOP 2.8.5").firmware_name == "sloop"
    assert StudioService.firmware_for("sloop") == "sloop" and StudioService.firmware_for("skiff") == "skiff"
    assert firmware.guess_firmware("FM-1_900", "FELUCCA SLOOP 2.8.5") in (("sloop", "v2.8.5"), firmware.guess_firmware(
        "FM-1_900"))
    assert firmware.guess_firmware("", "FELUCCA SLOOP 2.8.5") == ("sloop", "v2.8.5")


@need_node
def test_sloop_twin_core_headless():
    r = subprocess.run([NODE, str(ROOT / "tests" / "studio_twin_sloop.mjs"), str(studio.SIMS_DIR / "sloop-studio")],
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-3000:]
    res = json.loads(r.stdout)
    assert {x["version"] for x in res} >= {"2.8.4", "2.8.5"}
    for x in res:
        v = x["firmware_version"]
        assert x["version_export"] == v and x["info"] == "FELUCCA " + v and x["info_proto"] == 12 and x["ping"] == [0]
        assert bytes(x["identity_reply"]) == _identity_frame("FM-1_900")   # ota.c, as demo_device expects
        assert x["ids"] == SLOOP_IDS and x["screen_lit"] > 2000
        assert {0, 1, 2, 9} <= {int(k) for k in x["sizes"]} and x["sizes"]["2"] == 3816   # FUN6 projects
        assert all(rc == 0 for rc in x["put_rc"].values())
        assert all(x["identical"].values()) and x["persisted"] and x["projects_used"] == [0]
        assert x["live_step0"] == {"note": 48, "vel": 100}
        a, b = x["takes"]
        assert a["playing"] and a["leds_lit"] and a["peak"] > 0.05 and a["nonFinite"] == 0 and a["hash"] == b["hash"]
        assert x["fork_identical"] and x["stopped"] and x["song_play"] and x["editor_stop"]
        print(f"{v}: {a['realtime']:.1f}x real time (4 tracks)")
        assert a["realtime"] > 3


def _sloop_song(st, section=0):
    L = st.link
    for k in range(3):
        for i in range(0, 16, 2 + k):
            L.request(Cmd.TRACK_STEP, [k, i, 1, 48 + 5 * k + (i % 5), 0, 0, 0, 0, 0, 100])
    for i in range(0, 16, 4):                                                # DRUM_STEP: the kick on every beat
        L.request(33, [i, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0])
    assert L.request(Cmd.PROJECT, [1, section])[2] == 1                      # SAVE > SECTION A


def _sloop_sample(st, slot=1):
    """a one-zone kit into the user sample slot USR<slot+1> (samples.py's upload, the format of SLOOP's editor)"""
    import math
    from fm1companion import samples
    from fm1companion.vendor import sampleio as sio
    hdr, data = sio.user_slot("SINE", [([int(12000 * math.sin(i / 7)) for i in range(4000)], 60, None, None)])
    samples.upload(st.link, slot, hdr, data)
    return samples.slot_info(st.link)[1][slot]


def test_sloop_backup_round_trip_with_suite_code(node_studio, tmp_path):
    from fm1companion import backup_inspect as BI, sloopbk
    a = node_studio(firmware="sloop", version="SLOOP 2.8.5")
    assert a.build.firmware == "sloop" and a.build.version == "2.8.5" and a.tracks == 4 and a.project_slots == 4
    assert studio.device_version(a.link) == "v2.8.5" and a.info_name == "FELUCCA SLOOP"
    _sloop_song(a)
    slot = _sloop_sample(a)
    assert slot.zones == 1 and slot.name == "SINE"
    arch = a.backup()                                                        # SLOOP's own format (sloopbk.py)
    assert arch.sloop and arch.file["format"] == "sloop-backup" and arch.firmware == "FELUCCA SLOOP 2.8.5"
    assert [o["id"] for o in arch.objects] == SLOOP_IDS and arch.known
    assert studio.backup_version(arch) == "v2.8.5" and studio.backup_firmware(arch) == "sloop"
    assert arch.companion["version_string"] == "FELUCCA SLOOP 2.8.5"
    by = {o["id"]: o["bytes"] for o in arch.objects}
    assert len(by[33]) > sloopbk.SMP_DATA_OFF and not by[32] and sloopbk.proj_format(by[2]) == 6
    # the file as SLOOP's web editor writes it (JSON.stringify(f, null, 1)), without the companion block: the format tells
    p = tmp_path / "sloop.json"
    backup.write_file(p, arch.file)
    plain = json.loads(p.read_text("utf-8"))
    assert p.read_text("utf-8") == sloopbk.dumps(plain)
    plain.pop("companion")
    parch = backup.read(plain)
    assert studio.backup_firmware(parch) == "sloop" and studio.backup_version(parch) == "v2.8.5"
    # into the older twin via backup.restore (= the editor protocol, SLOOP's order): byte-identical back
    b = node_studio(firmware="sloop", version="2.8.4")
    b.open_from_backup(parch)
    back = {o["id"]: o["bytes"] for o in b.backup().objects}
    assert back == by and b.projects() == [0] and not b.rejected
    assert b.load_project(0) == 0 and b.load_project(1) == 1
    vc = b.check_versions(archive=arch)
    assert not vc.ok and "SLOOP" in vc.message
    c = studio.Studio.for_backup(parch, backend="node")                     # the backup's firmware and version
    try:
        assert c.build.firmware == "sloop" and c.build.version == "2.8.5" and not c.warning and c.projects() == [0]
        assert c.play()["busy"] and not c.stop()["busy"]                   # st_transport_set
        assert c.play("song")["busy"] and not c.stop()["busy"]
    finally:
        c.close()
    # the tolerant load (the studio page's): nothing of its own firmware skipped; a section in a newer project format
    # than the twin reads, and a damaged settings object, are left out and named; the rest is taken
    d = node_studio(firmware="sloop")
    d.open_from_backup(arch, tolerant=True)
    assert d.rejected == [] and {o["id"]: o["bytes"] for o in d.backup().objects} == by
    f = json.loads(backup.dumps(arch.file))
    for o in f["objects"]:
        if o["id"] in (1, 3):
            raw = bytearray(by[2] if o["id"] == 3 else by[1])
            if o["id"] == 3:
                raw[0] = ord("9")                                            # "FUN9"
            else:
                raw[0:4] = b"XXXX"                                           # (not "PER3")
            o.update(len=len(raw), crc=backup.crc32(bytes(raw)), data=sloopbk.b64enc(bytes(raw)))
    e = node_studio(firmware="sloop")
    e.open_from_backup(backup.read(f), tolerant=True)
    assert sorted(e.rejected) == [1, 3] and e.projects() == [0]
    # the inspector reads it as SLOOP's (sections A..D, the sample slots)
    rep = BI.inspect(arch)
    assert rep.firmware_key == "sloop" and BI.is_sloop(rep)
    assert "A" in rep.obj(2).title and "USR2" in rep.obj(33).title
    assert BI.compatibility(rep, "sloop", "FELUCCA SLOOP 2.8.5", SLOOP_IDS).ok


def test_sloop_demo_device_and_write_back(node_studio):
    """The demo's SLOOP device (demo_device.py, as the app's demo mode starts it): fetched into the twin, written back
    with the checks of a real SLOOP (its own protocol, the settings left as they are)."""
    from fm1companion.demo_device import FakeFM1
    from fm1companion.transport import Link
    sys.path.insert(0, str(ROOT / "tests"))
    from test_sloop_backup import LocalTransport
    dev = FakeFM1(4, "SLOOP 2.8.5")
    link = Link(LocalTransport(dev))
    st = node_studio(firmware="sloop", version="SLOOP 2.8.5")
    arch, vc = st.open_from_device(link, tolerant=True)
    assert arch.sloop and vc.ok and st.projects() == [0]
    assert set(st.rejected) <= {1, 9}                                        # (the demo's settings and SYN kits are filler)
    before = {o["id"]: o["bytes"] for o in backup.read(backup.capture(link, "x", firmware_key="sloop")).objects}
    _sloop_song(st, section=1)
    out = st.write_to_device(link, settings=False, exclude=st.rejected)
    now = {o["id"]: o["bytes"] for o in backup.read(backup.capture(link, "x", firmware_key="sloop")).objects}
    twin = {o["id"]: o["bytes"] for o in out.objects}
    for i in now:
        assert now[i] == (before[i] if i in (1, *st.rejected) else twin[i]), i
    assert now[3] and now[3] == twin[3]                                       # section B: the twin's
    other = node_studio(firmware="melodee")
    with pytest.raises(backup.BackupError, match="SLOOP"):                   # a Melodee twin's data onto SLOOP: refused
        other.write_to_device(link, force=True)


def test_sloop_render_stems(node_studio, tmp_path):
    st = node_studio(firmware="sloop")
    _sloop_song(st)
    s = st.render_wav(tmp_path / "mix.wav", seconds=2, start="play", source="saved", project=0, stems=True)
    assert sorted(s.stems) == [0, 1, 2, 3] and s.peak > 0.05
    peaks = []
    for k, p in s.stems.items():
        raw = p.read_bytes()[len(studio.wav_header(s.frames)):]
        fl = struct.unpack(f"<{len(raw) // 4}f", raw)
        peaks.append(max(abs(x) for x in fl))
    assert all(pk > 0.005 for pk in peaks), peaks
    path = st.save_state(tmp_path / "x" / studio.STATE_NAME)
    meta, flash = studio.read_state(path)
    assert meta["firmware"] == "sloop" and meta["firmware_version"] == "2.8.5" and len(flash) == 0x100000


# ------------------------------------------------------------------ CoralFlower (tools/studio/coralflower_studio.c)
# CoralFlower's releases are tags; its web installer offers the first entry of web/releases.json (0.7.1, the stable),
# 0.7 is the release before. tools/build.py --release X.Y: INFO "CoralFlower X.Y", the identity PRODUCT FM-1_930
CORAL_COMMIT = {"0.7": "65c6b767cc446b1c2b85c32b0812d50719fd10a4", "0.7.1": "46790b943bdb5e4143212d83dc495c832eefdbb8"}
CORAL_STATUS = {"0.7": "previous stable", "0.7.1": "stable"}


def test_coralflower_builds_side_by_side():
    bs = studio.builds("coralflower")
    assert [b.version for b in bs] == ["0.7", "0.7.1"]
    for b in bs:
        m = b.manifest
        assert b.verify() == [] and b.path.name == b.version
        assert m["kind"] == "studio" and m["abi"] == studio.STUDIO_ABI and m["firmware"] == "coralflower"
        assert m["ref"] == "v" + b.version and m["commit"] == CORAL_COMMIT[b.version] and m["license"] == "GPL-3.0-only"
        assert m["release_status"] == CORAL_STATUS[b.version]
        assert m["identity"] == "FM-1_930" and m["fs"] == 44100 and m["flash_size"] == 0x100000
        assert m["tracks"] == 4 and m["projects"] == 0 and m["source_repo"] == "https://github.com/FicusFicusov/CoralFlower"
        assert {"LICENSE", "NOTICE", "LICENSING.md", "LICENSING-choralroot.md", "THIRD_PARTY.txt", "twin.wasm"} <= \
            set(m["files"])
        notice = (b.path / "NOTICE").read_text()
        assert "unmodified" in notice and "Written offer" in notice and f"CoralFlower-{m['commit']}.tar.gz" in notice
        assert "FicusFicusov" in notice and "ChoralRoot" in notice and m["glue"] == "tools/studio/coralflower_studio.c"
        assert m["glue_sha256"] == hashlib.sha256((ROOT / m["glue"]).read_bytes()).hexdigest()
        third = (b.path / "THIRD_PARTY.txt").read_text()
        assert "Apache License" in third and "Helena Zhang" in third and "Inter Project" in third and "Fukiai" in third
    assert not any(s.key.startswith("coralflower") for s in sims.bundled())
    for fw in ("melodee", "skiff", "jiant"):                                 # the other twins are untouched
        assert {b.firmware for b in studio.builds(fw)} == {fw}


def test_coralflower_versions_and_pick():
    b, exact, warn = studio.pick_build("coralflower", "CoralFlower 0.7.1")   # CoralFlower's INFO
    assert exact and b.version == "0.7.1" and not warn
    b, exact, warn = studio.pick_build("coralflower", "CoralFlower 0.7")
    assert exact and b.version == "0.7"
    b, exact, warn = studio.pick_build("coralflower", "CoralFlower 0.6.1")   # older than everything: the oldest
    assert not exact and b.version == "0.7" and "CoralFlower v0.6.1" in warn
    b, exact, warn = studio.pick_build("coralflower", "CoralFlower 0.8")     # newer: the next older one
    assert not exact and b.version == "0.7.1"
    b, exact, warn = studio.pick_build("coralflower", "CoralFlower 0.7.2-rc1")
    assert not exact and b.version == "0.7.1"
    assert studio.pick_build("coralflower")[0].version == "0.7.1"           # without a wish: the newest = the stable
    assert studio.pick_build("coralflower", studio.norm_version("CoralFlower 0.7"))[0].version == "0.7"
    # the generic order is CoralFlower's (0.6.1 < 0.6.2-rc1 < 0.7 < 0.7.1)
    order = ["0.4.2", "0.6", "0.6.1", "0.6.2-rc1", "0.7", "0.7.1"]
    assert sorted(reversed(order), key=studio.version_key) == order
    assert studio.check_versions("0.7.1", device="CoralFlower 0.7.1", firmware="CoralFlower").ok
    vc = studio.check_versions("0.7.1", device="CoralFlower 0.7", firmware="CoralFlower")
    assert not vc.ok and "CoralFlower" in vc.message and "CZ-1" not in vc.message
    assert studio.firmware_name("coralflower") == "CoralFlower" and studio.FIRMWARES["coralflower"]["projects"] == 0
    assert "CoralFlower" in studio.firmware_names() and "JIANT FM" in studio.firmware_names("or")
    # the device layer: INFO "CoralFlower 0.7.1", the catalog's key "coralflower" and the identity FM-1_930
    from fm1companion.protocol import Info
    from fm1companion.ui.studio_service import StudioService
    from fm1companion import firmware
    assert Info("CoralFlower 0.7.1").firmware_name == "coralflower"
    assert StudioService.firmware_for("coralflower") == "coralflower"
    assert firmware.guess_firmware("", "CoralFlower 0.7.1")[0] == "coralflower"
    assert firmware.guess_firmware("FM-1_930")[0] in ("coralflower", "")   # ("" if this machine remembered it otherwise)


@need_node
def test_coralflower_twin_core_headless():
    r = subprocess.run([NODE, str(ROOT / "tests" / "studio_twin_coralflower.mjs"),
                        str(studio.SIMS_DIR / "coralflower-studio")], capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-3000:]
    res = json.loads(r.stdout)
    assert {x["version"] for x in res} >= {"0.7", "0.7.1"}
    for x in res:
        v = x["firmware_version"]
        assert x["version_export"] == x["info"] == "CoralFlower " + v
        assert x["info_tail"] == [0] * 7 + [0x42, 1, 3, 0x43, 1, 2]       # no editor; backup read + restore, RESTART
        assert bytes(x["identity_reply"]) == _identity_frame("FM-1_930")   # ota.c, as demo_device expects
        assert x["ids"] == backup.CORALFLOWER_IDS and x["fresh_sizes"] == {"30": 200} and x["screen_lit"] > 10000
        assert x["key_lit"] and x["rec_flag"] and {30, 50, 51, 52, 53, 54, 55} <= {int(k) for k in x["sizes"]}
        assert all(rc == 0 for rc in x["put_rc"].values()) and x["restart_rc"] == [0]
        assert all(x["identical"].values()) and x["persisted"] and x["bad_crc"] == 2 and x["unknown_id"] == 1
        a, b = x["takes"]
        assert a["playing"] and a["play_lit"] and a["peak"] > 0.05 and a["nonFinite"] == 0 and a["hash"] == b["hash"]
        assert a["note_ons"] > 0 and a["project"] == 255 and not a["projects"]
        assert x["fork_identical"] and x["stopped"] and x["song_play"] and x["restart_play"]
        print(f"{v}: {a['realtime']:.1f}x real time (4 parts)")
        assert a["realtime"] > 3


def _coral_take(st):
    """a take on the twin's panel, as a player records one (no editor in CoralFlower): REC on HOME, a bar counted in,
    four chords, REC again -> pattern A; the clock held still meanwhile (only these steps move it)"""
    be = st.backend
    st.set_clock(False)
    st.press("REC")
    be.call("run", ms=2100)
    for k in (7, 11, 14, 9):
        be.call("keys", mask=1 << k)
        be.call("run", ms=450)
        be.call("keys", mask=0)
        be.call("run", ms=50)
    st.press("REC")
    be.call("run", ms=2500)
    st.stop()
    be.call("run", ms=100)
    st.set_clock(True)


def test_coralflower_backup_round_trip_with_suite_code(node_studio):
    a = node_studio(firmware="coralflower", version="CoralFlower 0.7.1")
    assert a.build.firmware == "coralflower" and a.build.version == "0.7.1" and a.tracks == 4 and a.project_slots == 0
    assert studio.device_version(a.link) == "v0.7.1" and a.info_name == "CoralFlower" and a.projects() == []
    fresh = a.backup()                                                      # a new device: the sound banks empty
    assert not fresh.objects[0]["size"] and fresh.coralflower and fresh.project_objects() == []
    _coral_take(a)
    arch = a.backup()
    assert [o["id"] for o in arch.objects] == backup.CORALFLOWER_IDS and arch.known
    assert studio.backup_version(arch) == "v0.7.1" and studio.backup_firmware(arch) == "coralflower"
    assert arch.companion["version_string"] == "CoralFlower 0.7.1"
    seq = {o["id"]: o["bytes"] for o in arch.objects if 50 <= o["id"] <= 55}
    assert all(seq.values())
    # a backup of CoralFlower's own web installer (no companion block; 'firmware' is the INFO string) and one with an
    # unknown name: the inventory tells
    plain = json.loads(backup.dumps(arch.file))
    plain.pop("companion")
    plain["firmware"] = "CoralFlower 0.7.1"
    assert studio.backup_firmware(backup.read(plain)) == "coralflower"
    plain["firmware"] = "?"
    assert studio.backup_firmware(backup.read(plain)) == "coralflower"
    # into the 0.7 twin through backup.restore (the backup protocol, then RESTART): the twin restarts (a power cycle)
    # and reads the patterns in
    b = node_studio(firmware="coralflower", version="0.7")
    b.open_from_backup(arch)
    back = {o["id"]: o["bytes"] for o in b.backup().objects}
    assert {i: back[i] for i in seq} == seq and not b.rejected
    vc = b.check_versions(archive=arch)
    assert not vc.ok and "CoralFlower" in vc.message
    c = studio.Studio.for_backup(arch, backend="node")                     # the backup's firmware and version
    try:
        assert c.build.firmware == "coralflower" and c.build.version == "0.7.1" and not c.warning
        assert c.projects() == [] and c.load_project(0) == 1
        assert c.play()["busy"] and not c.stop()["busy"]                   # st_transport_set (PLAY: pattern A)
        assert c.play("song")["busy"] and not c.stop()["busy"]
    finally:
        c.close()
    # the tolerant load (the studio page's) skips nothing of its own firmware
    d = node_studio(firmware="coralflower", version="0.7.1")
    d.open_from_backup(arch, tolerant=True)
    assert d.rejected == [] and {o["id"]: o["bytes"] for o in d.backup().objects}[50] == seq[50]
    # writing back (the studio's write_to_device; here onto another twin as the "FM-1"): every object but the
    # settings (30) when asked, then RESTART
    e = node_studio(firmware="coralflower", version="0.7.1")
    before = {o["id"]: o["bytes"] for o in e.backup().objects}
    a.write_to_device(e.link, settings=False)
    after = {o["id"]: o["bytes"] for o in e.backup().objects}
    assert {i: after[i] for i in seq} == seq and after[30] == before[30]
    # the inspector names CoralFlower's objects (no projects, no sample slots, 8 the FM6 bank in use)
    from fm1companion import backup_inspect as BI
    rep = BI.inspect(arch)
    assert rep.firmware_key == "coralflower" and BI.inspect(backup.read(plain)).firmware_key == "coralflower"
    for oid in (50, 55, 60, 64, 67):
        assert rep.obj(oid).group == BI.G_PROJECTS and "USR" not in rep.obj(oid).title and not rep.obj(oid).slot, oid
    assert rep.obj(30).group == BI.G_SETTINGS and "FM6" in rep.obj(8).title and rep.projects == []
    assert BI.skip_ids(rep) == set() and not rep.warnings
    comp = BI.compatibility(rep, "coralflower", "CoralFlower 0.7.1", backup.CORALFLOWER_IDS)
    assert comp.ok and 8 in comp.allowed_ids


def test_coralflower_render_stems(node_studio, tmp_path):
    st = node_studio(firmware="coralflower")
    _coral_take(st)
    st.link.request(Cmd.BACKUP_LIST, [], timeout=3.0)                      # (LIST writes the patterns not yet saved)
    s = st.render_wav(tmp_path / "mix.wav", seconds=2, start="play", source="saved", stems=True)
    assert sorted(s.stems) == [0, 1, 2, 3] and s.peak > 0.05
    peaks = []
    for k, p in s.stems.items():
        raw = p.read_bytes()[len(studio.wav_header(s.frames)):]
        fl = struct.unpack(f"<{len(raw) // 4}f", raw)
        peaks.append(max(abs(x) for x in fl))
    # the take is on the Chords track; the bass, the LAYER and the drums have nothing in pattern A (and a stem carries
    # neither the metronome's click nor the power-on sound)
    assert peaks[0] > 0.05 and max(peaks[1:]) < 0.05, peaks
    path = st.save_state(tmp_path / "x" / studio.STATE_NAME)
    meta, flash = studio.read_state(path)
    assert meta["firmware"] == "coralflower" and meta["firmware_version"] == "0.7.1" and len(flash) == 0x100000


def test_coralflower_inventory_in_backup_layer():
    """CoralFlower: 26 objects (ChoralRoot's sounds 6..11, its settings 30, loop 31, the sequencer 50..55, the sounds'
    edits 56..58, the pattern sounds 59..67); 50..63 are no sample slots, 64..67 no projects, no object 0 or 1."""
    ids = backup.CORALFLOWER_IDS
    assert len(ids) == 26 and backup.known_ids(ids) == ids and backup.is_coralflower(ids)
    for n in (8, 14, 17):                                                   # the older releases' inventories
        assert backup.is_coralflower(ids[:n]) and backup.known_ids(ids[:n]) in (ids[:n], None)
    assert backup.known_ids(list(range(17))) == list(range(17))             # (Melodee's 17 stays known)
    assert not backup.is_coralflower(list(range(26))) and not backup.is_coralflower(ids[:20])
    assert backup.is_sample(50) and not backup.is_sample(50, coral=True)
    assert backup.is_project(64) and not backup.is_project(64, coral=True)
    assert "USR" not in backup.object_title(50, coral=True) and "Project" not in backup.object_title(64, coral=True)
    # a CoralFlower archive whose sound banks (its first objects) are empty is a valid backup; a Melodee 1.6 check
    # (project ids 64..91) does not apply to its pattern sounds 64..67
    f = {"format": backup.FORMAT, "version": 1, "firmware": "CoralFlower 0.7.1", "objects": [
        {"id": i, "size": 4 if i in (30, 64) else 0, "crc": backup.crc32(b"abcd") if i in (30, 64) else 0,
         "data": base64.b64encode(b"abcd" if i in (30, 64) else b"").decode()} for i in ids]}
    arch = backup.read(f)
    assert arch.coralflower and arch.project_objects() == []
    backup.check_16(arch, [backup.ObjInfo(i, 0, 0) for i in ids])
    with pytest.raises(backup.BackupError):
        backup.read({**f, "objects": [{**o, "size": 0, "crc": 0, "data": ""} for o in f["objects"]]})


# ------------------------------------------------------------------ ChoralRoot FM-1 (tools/studio/choralroot_studio.c)
# ChoralRoot's FM-1 releases are tags vX.Y (the -smk37 tags are the SMK-37's); its web installer offers the first entry of
# web/releases.json (0.39, 2026-11-10), 0.38 is the release before. tools/build.py --release X.Y: INFO "ChoralRoot X.Y",
# the identity PRODUCT FM-1_920
CHORAL_COMMIT = {"0.38": "3cb2e8fd25d3d4292cb5c66c2623589d7a2ba8e3", "0.39": "36867e5c76fa1ecc02cba57102ee870695e1de10"}
CHORAL_STATUS = {"0.38": "previous stable", "0.39": "stable"}


def test_choralroot_builds_side_by_side():
    bs = studio.builds("choralroot")
    assert [b.version for b in bs] == ["0.38", "0.39"]
    for b in bs:
        m = b.manifest
        assert b.verify() == [] and b.path.name == b.version
        assert m["kind"] == "studio" and m["abi"] == studio.STUDIO_ABI and m["firmware"] == "choralroot"
        assert m["ref"] == "v" + b.version and m["commit"] == CHORAL_COMMIT[b.version] and m["license"] == "GPL-3.0-only"
        assert m["release_status"] == CHORAL_STATUS[b.version] and m["firmware_version"] == b.version
        assert m["identity"] == "FM-1_920" and m["fs"] == 44100 and m["flash_size"] == 0x100000
        assert m["tracks"] == 2 and m["projects"] == 0 and m["source_repo"] == "https://github.com/Quixotic7/ChoralRootFM1"
        assert {"LICENSE", "NOTICE", "LICENSING.md", "THIRD_PARTY.txt", "twin.wasm"} <= set(m["files"])
        notice = (b.path / "NOTICE").read_text()
        assert "unmodified" in notice and "Written offer" in notice and f"ChoralRootFM1-{m['commit']}.tar.gz" in notice
        assert "Quixotic7" in notice and "Casio" in notice and m["glue"] == "tools/studio/choralroot_studio.c"
        assert m["glue_sha256"] == hashlib.sha256((ROOT / m["glue"]).read_bytes()).hexdigest()
        third = (b.path / "THIRD_PARTY.txt").read_text()
        assert "Apache License" in third and "Mozilla Public License" in third and "Inter Project" in third
        assert "Fukiai" in third and ("uPD933" in third or "Devin Acker" in third)
    assert not any(s.key.startswith("choralroot") for s in sims.bundled())
    for fw in ("melodee", "skiff", "jiant", "coralflower"):                  # the other twins are untouched
        assert {b.firmware for b in studio.builds(fw)} == {fw}


def test_choralroot_versions_and_pick():
    b, exact, warn = studio.pick_build("choralroot", "ChoralRoot 0.39")      # ChoralRoot's INFO
    assert exact and b.version == "0.39" and not warn
    assert studio.pick_build("choralroot", "ChoralRoot 0.39 1-core")[0].version == "0.39"   # (the single-core package)
    b, exact, warn = studio.pick_build("choralroot", "ChoralRoot 0.38")
    assert exact and b.version == "0.38"
    b, exact, warn = studio.pick_build("choralroot", "ChoralRoot 0.29.2")    # older than everything: the oldest
    assert not exact and b.version == "0.38" and "ChoralRoot v0.29.2" in warn
    b, exact, warn = studio.pick_build("choralroot", "ChoralRoot 0.40-beta1")   # newer: the next older one
    assert not exact and b.version == "0.39"
    assert studio.pick_build("choralroot")[0].version == "0.39"              # without a wish: the newest = the stable
    order = ["0.22-beta4", "0.22", "0.29.2", "0.38", "0.39", "0.40"]
    assert sorted(reversed(order), key=studio.version_key) == order
    vc = studio.check_versions("0.39", device="ChoralRoot 0.38", firmware="ChoralRoot")
    assert not vc.ok and "ChoralRoot" in vc.message
    assert studio.firmware_name("choralroot") == "ChoralRoot" and studio.FIRMWARES["choralroot"]["projects"] == 0
    assert "ChoralRoot" in studio.firmware_names() and "ChoralRoot" in studio.firmware_names("or")
    # the device layer: INFO "ChoralRoot 0.39", the catalog's key "choralroot-fm-1" and the identity FM-1_920
    from fm1companion.protocol import Info
    from fm1companion.ui.studio_service import StudioService
    from fm1companion import firmware
    assert Info("ChoralRoot 0.39").firmware_name == "choralroot"
    assert StudioService.firmware_for("choralroot-fm-1") == "choralroot" == StudioService.firmware_for("choralroot")
    assert firmware.guess_firmware("", "ChoralRoot 0.39")[0] == "choralroot-fm-1"
    assert firmware.guess_firmware("FM-1_920")[0] in ("choralroot-fm-1", "")   # ("" if this machine remembered it otherwise)
    assert firmware.guess_firmware("", "CoralFlower 0.7.1")[0] == "coralflower"


@need_node
def test_choralroot_twin_core_headless():
    r = subprocess.run([NODE, str(ROOT / "tests" / "studio_twin_choralroot.mjs"),
                        str(studio.SIMS_DIR / "choralroot-studio")], capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-3000:]
    res = json.loads(r.stdout)
    assert {x["version"] for x in res} >= {"0.38", "0.39"}
    for x in res:
        v = x["firmware_version"]
        assert x["version_export"] == x["info"] == "ChoralRoot " + v
        assert x["info_tail"] == [0] * 7 + [0x42, 1, 3, 0x43, 1, 3]       # no editor; backup read + restore, RESTART
        assert bytes(x["identity_reply"]) == _identity_frame("FM-1_920")   # ota.c, as demo_device expects
        assert x["ids"] == backup.CHORALROOT_IDS and x["fresh_used"] == 0 and x["screen_lit"] > 1000
        assert x["take"] and x["sizes"].keys() == {"1", "40"}
        assert all(rc == 0 for rc in x["put_rc"].values()) and x["restart_rc"] == 0
        assert all(x["identical"].values()) and x["persisted"] and x["bad_crc"] == 2 and x["unknown_id"] == [1, 1]
        a, b = x["takes"]
        assert a["playing"] and a["play_lit"] and a["peak"] > 0.05 and a["nonFinite"] == 0 and a["hash"] == b["hash"]
        assert a["note_ons"] > 0 and a["project"] == 255 and not a["projects"]
        assert x["fork_identical"] and x["stopped"] and x["song_play"] and x["restart_play"]
        print(f"{v}: {a['realtime']:.1f}x real time")
        assert a["realtime"] > 3


def _choral_take(st):
    """a loop taken on the twin's panel, as a player records one (no editor in ChoralRoot): REC, four chords, REC
    (it plays), stop, SAVE held (the settings and loop slot 1); the clock held still meanwhile"""
    be = st.backend
    st.set_clock(False)
    st.press("REC")
    be.call("run", ms=200)
    for k in (7, 12, 14, 20):
        be.call("keys", mask=1 << k)
        be.call("run", ms=450)
        be.call("keys", mask=0)
        be.call("run", ms=50)
    st.press("REC")
    be.call("run", ms=300)
    st.stop()
    be.call("run", ms=100)
    be.call("press", label=studio.B["SAVE"], ms=1500)
    be.call("run", ms=1500)
    st.set_clock(True)


def test_choralroot_backup_round_trip_with_suite_code(node_studio):
    a = node_studio(firmware="choralroot", version="ChoralRoot 0.39")
    assert a.build.firmware == "choralroot" and a.build.version == "0.39" and a.tracks == 2 and a.project_slots == 0
    assert studio.device_version(a.link) == "v0.39.0" and a.info_name == "ChoralRoot" and a.projects() == []
    fresh = a.backup()                                                      # a new device: nothing saved yet
    assert not any(o["size"] for o in fresh.objects) and fresh.choralroot and fresh.project_objects() == []
    _choral_take(a)
    arch = a.backup()
    assert [o["id"] for o in arch.objects] == backup.CHORALROOT_IDS and arch.known
    assert studio.backup_version(arch) == "v0.39.0" and studio.backup_firmware(arch) == "choralroot"
    assert arch.companion["version_string"] == "ChoralRoot 0.39"
    used = {o["id"]: o["bytes"] for o in arch.objects if o["size"]}
    assert set(used) == {1, 40}
    # a backup of ChoralRoot's own web installer (no companion block; 'firmware' is the INFO string) and one with an
    # unknown name: the inventory tells
    plain = json.loads(backup.dumps(arch.file))
    plain.pop("companion")
    plain["firmware"] = "ChoralRoot 0.39"
    assert studio.backup_firmware(backup.read(plain)) == "choralroot"
    plain["firmware"] = "?"
    assert studio.backup_firmware(backup.read(plain)) == "choralroot"
    # into the 0.38 twin through backup.restore (the backup protocol, then RESTART): the twin restarts (a power cycle)
    # and reads the loop in
    b = node_studio(firmware="choralroot", version="0.38")
    b.open_from_backup(arch)
    back = {o["id"]: o["bytes"] for o in b.backup().objects}
    assert back[40] == used[40] and back[1] and not b.rejected
    vc = b.check_versions(archive=arch)
    assert not vc.ok and "ChoralRoot" in vc.message
    c = studio.Studio.for_backup(arch, backend="node")                     # the backup's firmware and version
    try:
        assert c.build.firmware == "choralroot" and c.build.version == "0.39" and not c.warning
        assert c.projects() == [] and c.load_project(0) == 1
        assert c.play()["busy"] and not c.stop()["busy"]                   # st_transport_set (PLAY: the loop)
        assert c.play("song")["busy"] and not c.stop()["busy"]
    finally:
        c.close()
    # the tolerant load (the studio page's) skips nothing of its own firmware; an old (0.21) archive's sound objects
    # are named and left out
    d = node_studio(firmware="choralroot", version="0.39")
    d.open_from_backup(arch, tolerant=True)
    assert d.rejected == [] and {o["id"]: o["bytes"] for o in d.backup().objects}[40] == used[40]
    old = {"format": backup.FORMAT, "version": 1, "firmware": "ChoralRoot 0.21", "objects": [
        {"id": i, "size": len(used.get(i, b"x" if i == 6 else b"")), "crc": backup.crc32(used.get(i, b"x" if i == 6 else b"")),
         "data": base64.b64encode(used.get(i, b"x" if i == 6 else b"")).decode()} for i in backup.CHORALROOT_IDS_OLD]}
    d.open_from_backup(old, tolerant=True)
    assert d.rejected == [6] and {o["id"]: o["bytes"] for o in d.backup().objects}[40] == used[40]
    with pytest.raises(backup.BackupError):                                 # the strict restore: nothing written
        backup.restore(d.link, backup.read(old), firmware_key="choralroot")
    # writing back (the studio's write_to_device; here onto another twin as the "FM-1"): every object but the
    # settings (1) when asked, then RESTART
    e = node_studio(firmware="choralroot", version="0.39")
    a.write_to_device(e.link, settings=False)
    e.backend.call("reset")
    after = {o["id"]: o["bytes"] for o in e.backup().objects}
    assert after[40] == used[40] and not after[1]
    # the inspector names ChoralRoot's objects (no projects, no sample slots, 8 the FM6 bank in use)
    from fm1companion import backup_inspect as BI
    rep = BI.inspect(arch)
    assert rep.firmware_key == "choralroot" and BI.inspect(backup.read(plain)).firmware_key == "choralroot"
    for oid in (40, 49):
        assert rep.obj(oid).group == BI.G_PROJECTS and "Loop" in rep.obj(oid).title and not rep.obj(oid).slot, oid
    for oid in (50, 62):
        assert rep.obj(oid).group == BI.G_PRESETS and "USR" not in rep.obj(oid).title, oid
    assert rep.obj(62).title.endswith("121–128") and "CZ-1" in rep.obj(14).title and "FM6" in rep.obj(8).title
    assert rep.obj(1).group == BI.G_SETTINGS and rep.projects == [] and BI.skip_ids(rep) == set()
    comp = BI.compatibility(rep, "choralroot", "ChoralRoot 0.39", backup.CHORALROOT_IDS)
    assert comp.ok and {8, 40} <= comp.allowed_ids


def test_choralroot_render_stems(node_studio, tmp_path):
    st = node_studio(firmware="choralroot")
    _choral_take(st)
    s = st.render_wav(tmp_path / "mix.wav", seconds=2, start="play", source="saved", stems=True)
    assert sorted(s.stems) == [0, 1] and s.peak > 0.05
    peaks = []
    for k, p in s.stems.items():
        raw = p.read_bytes()[len(studio.wav_header(s.frames)):]
        fl = struct.unpack(f"<{len(raw) // 4}f", raw)
        peaks.append(max(abs(x) for x in fl))
    # the loop is the chord part's; the bass is off by default (and a stem carries no metronome click)
    assert peaks[0] > 0.05 and peaks[1] < 0.01, peaks
    path = st.save_state(tmp_path / "x" / studio.STATE_NAME)
    meta, flash = studio.read_state(path)
    assert meta["firmware"] == "choralroot" and meta["firmware_version"] == "0.39" and len(flash) == 0x100000


def test_choralroot_inventory_in_backup_layer():
    """ChoralRoot FM-1: 34 objects from 0.22 (1 the settings, 8 the FM6 bank, 14..21 the CZ-1 banks, 50..62 the sound
    pool, 23 the favourites, 40..49 the loop slots), 28 / 27 / 17 in 0.14 .. 0.21; 40..62 are no sample slots, nothing
    is a project, no object 0."""
    ids = backup.CHORALROOT_IDS
    assert len(ids) == 34 and backup.known_ids(ids) == ids and backup.is_choralroot(ids)
    for old in backup.CHORALROOT_ID_SETS[1:]:                               # the older releases' inventories
        assert backup.is_choralroot(old) and backup.known_ids(old) == old
    assert backup.known_ids(list(range(17))) == list(range(17)) and backup.known_ids(list(range(28))) == list(range(28))
    assert not backup.is_choralroot(list(range(34))) and not backup.is_choralroot(backup.CORALFLOWER_IDS)
    assert not backup.is_coralflower(ids) and not backup.is_jiant(ids)
    assert backup.is_sample(40) and not backup.is_sample(40, choral=True) and not backup.is_sample(62, choral=True)
    assert not backup.is_project(0, choral=True)
    assert "USR" not in backup.object_title(50, choral=True) and "Loop" in backup.object_title(41, choral=True)
    # an archive with only the settings (the FM6 bank, its second object, empty) and an empty one are valid backups
    f = {"format": backup.FORMAT, "version": 1, "firmware": "ChoralRoot 0.39", "objects": [
        {"id": i, "size": 4 if i == 1 else 0, "crc": backup.crc32(b"abcd") if i == 1 else 0,
         "data": base64.b64encode(b"abcd" if i == 1 else b"").decode()} for i in ids]}
    arch = backup.read(f)
    assert arch.choralroot and not arch.coralflower and arch.project_objects() == []
    backup.check_16(arch, [backup.ObjInfo(i, 0, 0) for i in ids])
    assert backup.read({**f, "objects": [{**o, "size": 0, "crc": 0, "data": ""} for o in f["objects"]]}).choralroot
    from fm1companion import studio as S
    assert S.backup_firmware(arch) == "choralroot"


# ------------------------------------------------------------------ KARMA (tools/studio/karma_studio.c)
# KARMA (eatyourgreensstudio-dotcom/karma-fm1, a SLOOP fork): v3.1 is what its web installer serves (docs/webapp/installer,
# 2026-11-10), v3.0.5 the release before. Every build answers INFO "FELUCCA KARMA X.Y" and the identity FM-1_926
# (tools/build.py PRODUCT); it speaks SLOOP's backup protocol (34-36) and writes SLOOP's "sloop-backup" files.
KARMA_COMMIT = {"3.0.5": "23304e93d17d12a79546879d60d58c5c3421aac5", "3.1": "718de454ee8ce0e5c6d12ec5fe5e0ba85d0cb4ba"}
KARMA_STATUS = {"3.0.5": "previous stable", "3.1": "stable"}
KARMA_IDS = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 32, 33, 34, 35]   # editor.c ED_BK_IDS (10, MIDI LEARN: only when learned)


def test_karma_builds_side_by_side():
    bs = studio.builds("karma")
    vs = [b.version for b in bs]
    assert vs == ["3.0.5", "3.1"] and [b.path.name for b in bs] == vs      # (KARMA's version string: "KARMA 3.1")
    for b in bs:
        m = b.manifest
        assert b.verify() == []
        assert m["kind"] == "studio" and m["abi"] == studio.STUDIO_ABI and m["firmware"] == "karma"
        assert m["commit"] == KARMA_COMMIT[b.version] and m["ref"] == "v" + b.version and m["version"] == b.version
        assert m["firmware_version"] == "KARMA " + b.version
        assert m["license"] == "GPL-3.0-only" and m["release_status"] == KARMA_STATUS[b.version]
        assert m["identity"] == "FM-1_926" and m["fs"] == 44100 and m["flash_size"] == 0x100000
        assert m["tracks"] == 4 and m["projects"] == 4
        assert m["source_repo"] == "https://github.com/eatyourgreensstudio-dotcom/karma-fm1"
        assert {"LICENSE", "NOTICE", "LICENSING.md", "THIRD_PARTY.txt", "twin.wasm"} <= set(m["files"])
        notice = (b.path / "NOTICE").read_text()
        assert "unmodified" in notice and "Written offer" in notice and f"karma-fm1-{m['commit']}.tar.gz" in notice
        assert "Ariel Muchnik" in notice and m["glue"] == "tools/studio/karma_studio.c"
        assert m["glue_sha256"] == hashlib.sha256((ROOT / m["glue"]).read_bytes()).hexdigest()
        third = (b.path / "THIRD_PARTY.txt").read_text()
        assert "Emilie Gillet" in third and "Erica Synths" in third and "Terminus" in third and "Electrosmith" in third
        assert ("Mozilla Public License" in third) == (b.version == "3.1")      # (3.1: the screen reader)
    assert not any(s.key.startswith("karma") for s in sims.bundled())


def test_karma_versions_and_pick():
    b, exact, warn = studio.pick_build("karma", "FELUCCA KARMA 3.1")      # KARMA's INFO
    assert exact and b.version == "3.1" and not warn
    b, exact, warn = studio.pick_build("karma", "KARMA 3.0.5")
    assert exact and b.version == "3.0.5"
    b, exact, warn = studio.pick_build("karma", "FELUCCA KARMA 3.0.4")    # between nothing and 3.0.5: the oldest
    assert not exact and b.version == "3.0.5" and "KARMA" in warn
    b, exact, warn = studio.pick_build("karma", "FELUCCA KARMA 3.2")      # newer: the next older one
    assert not exact and b.version == "3.1"
    assert studio.pick_build("karma")[0].version == "3.1"            # without a wish: the newest = the stable
    assert studio.pick_build("karma", studio.norm_version("FELUCCA KARMA 3.1"))[0].version == "3.1"
    assert studio.check_versions("3.1", device="FELUCCA KARMA 3.1", firmware="KARMA").ok
    vc = studio.check_versions("3.1", device="FELUCCA KARMA 3.0.5", firmware="KARMA")
    assert not vc.ok and "KARMA" in vc.message and "CZ-1" not in vc.message
    assert studio.firmware_name("karma") == "KARMA" and studio.FIRMWARES["karma"]["projects"] == 4
    # the device layer: INFO, the identity and the twin's key select the KARMA twin (KARMA is not in the catalog)
    from fm1companion import firmware
    from fm1companion.protocol import Info
    from fm1companion.ui.common import fw_label
    from fm1companion.ui.studio_service import StudioService
    assert Info("FELUCCA KARMA 3.1").firmware_name == "karma" and Info("FELUCCA SLOOP 2.8.3").firmware_name == "sloop"
    assert StudioService.firmware_for("karma") == "karma" and fw_label("FELUCCA KARMA 3.1") == "KARMA 3.1"
    assert firmware.guess_firmware("", "FELUCCA KARMA 3.1") == ("karma", "v3.1")
    assert firmware.guess_firmware("FM-1_926")[0] in ("karma", "")          # ("" if this machine remembered it otherwise)


@need_node
def test_karma_twin_core_headless():
    r = subprocess.run([NODE, str(ROOT / "tests" / "studio_twin_karma.mjs"), str(studio.SIMS_DIR / "karma-studio")],
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-3000:]
    res = json.loads(r.stdout)
    assert {x["version"] for x in res} >= {"3.0.5", "3.1"}
    for x in res:
        v = x["firmware_version"]
        assert x["version_export"] == v and x["info"] == "FELUCCA " + v and x["ping"] == [0]
        assert x["ntrk"] == 4 and x["proto"] == 11
        assert bytes(x["identity_reply"]) == _identity_frame("FM-1_926")   # ota.c, as demo_device expects
        assert x["ids"] == KARMA_IDS and x["screen_lit"] > 2000
        assert {0, 1, 2, 3, 4, 5, 9} <= {int(k) for k in x["sizes"]}      # the working project, settings, A..D, HEX kits
        assert all(rc == 0 for rc in x["put_rc"].values())
        assert all(x["identical"].values()) and x["persisted"] and x["projects_used"] == [0, 1, 2, 3]
        assert x["live_step0"] == {"note": 45, "vel": 100}
        a, b = x["takes"]
        assert a["playing"] and a["play_lit"] and a["peak"] > 0.05 and a["nonFinite"] == 0 and a["hash"] == b["hash"]
        assert x["fork_identical"] and x["stopped"] and x["load_while_playing"] == 2
        assert x["song_play"] and x["song_stop"]
        print(f"{v}: {a['realtime']:.1f}x real time (4 tracks)")
        assert a["realtime"] > 3


def _karma_song(st, parts=1):
    L = st.link
    for k in range(4):
        for i in range(0, 16, 2 + k % 2):
            L.request(Cmd.TRACK_STEP, [k, i, 1, (36 + 2 * (i % 3)) if k == 3 else 45 + 5 * k + (i % 5), 0, 0, 0, 0, 0, 100])
    for p in range(parts):
        assert L.request(Cmd.PROJECT, [1, p])[2] == 1                      # part A (B, C, D)


def test_karma_backup_round_trip_with_suite_code(node_studio):
    a = node_studio(firmware="karma", version="FELUCCA KARMA 3.0.5")
    assert a.build.firmware == "karma" and a.build.version == "3.0.5" and a.tracks == 4 and a.project_slots == 4
    assert studio.device_version(a.link) == "v3.0.5" and a.info_name == "FELUCCA KARMA"
    assert a.info["version"] == a.build.manifest["firmware_version"] == "KARMA 3.0.5"
    assert backup.protocol(a.link)[0] == backup.SLOOP                       # (INFO alone tells: SLOOP's commands 34-36)
    _karma_song(a)
    arch = a.backup()
    assert arch.sloop and [o["id"] for o in arch.objects] == KARMA_IDS and arch.firmware == "FELUCCA KARMA 3.0.5"
    assert studio.backup_version(arch) == "v3.0.5" and studio.backup_firmware(arch) == "karma"
    assert arch.companion["version_string"] == "FELUCCA KARMA 3.0.5"
    by = {o["id"]: o["bytes"] for o in arch.objects}
    from fm1companion import sloopbk
    assert sloopbk.proj_format(by[0]) == sloopbk.proj_format(by[2]) == 7 and by[8] == b""     # FUN7; FM6 is gone
    # a backup of KARMA's own web editor (no companion block; its 'firmware' is the INFO string)
    plain = json.loads(backup.dumps(arch.file))
    plain.pop("companion")
    assert studio.backup_firmware(backup.read(plain)) == "karma"
    # into the 3.1 twin through the suite's SLOOP restore (format 7 accepted on KARMA: its own editor refuses it)
    b = node_studio(firmware="karma", version="3.1")
    b.open_from_backup(backup.read(plain))
    back = {o["id"]: o["bytes"] for o in b.backup().objects}
    assert all(back[i] == by[i] for i in KARMA_IDS) and b.projects() == [0] and not b.rejected
    vc = b.check_versions(archive=arch)
    assert not vc.ok and "KARMA" in vc.message
    c = studio.Studio.for_backup(arch, backend="node")                     # the backup's firmware and version
    try:
        assert c.build.firmware == "karma" and c.build.version == "3.0.5" and not c.warning and c.projects() == [0]
        assert c.load_project(0) == 0 and c.load_project(1) == 1
        assert c.play()["busy"] and not c.stop()["busy"]                   # st_transport_set (no editor TRANSPORT)
        assert c.play("song")["busy"] and not c.stop()["busy"]
    finally:
        c.close()
    # the tolerant load (the studio page's): SLOOP 2.5's SYN kits ("DSU1") do not fit KARMA's HEX kits – left out and
    # named (KARMA's editor does the same), everything else is taken
    f = json.loads(backup.dumps(arch.file))
    old = b"DSU1" + bytes(60)
    for o in f["objects"]:
        if o["id"] == 9:
            o.update(len=len(old), crc=backup.crc32(old), data=sloopbk.b64enc(old))
    d = node_studio(firmware="karma", version="3.1")
    d.open_from_backup(backup.read(f), tolerant=True)
    assert d.rejected == [9] and d.projects() == [0]
    assert {o["id"]: o["bytes"] for o in d.backup().objects}[2] == by[2]
    d.open_from_backup(backup.read(f))                                      # (the plain restore leaves them out too)
    # writing back (backup.restore_selected without the settings, as the page's "keep the settings")
    e = node_studio(firmware="karma", version="3.1")
    written = backup.restore_selected(e.link, arch, [i for i in KARMA_IDS if i != 1])
    assert 1 not in written and 2 in written and e.projects() == [0]
    # the inspector and the compatibility check name KARMA (not SLOOP)
    from fm1companion import backup_inspect as BI
    rep = BI.inspect(arch)
    assert rep.firmware_key == "karma" and BI.inspect(backup.read(plain)).firmware_key == "karma"
    assert BI.compatibility(rep, "karma", "FELUCCA KARMA 3.1", KARMA_IDS).ok
    assert not BI.compatibility(rep, "sloop", "FELUCCA SLOOP 2.6", KARMA_IDS).ok


def test_karma_render_stems(node_studio, tmp_path):
    st = node_studio(firmware="karma")
    _karma_song(st)
    s = st.render_wav(tmp_path / "mix.wav", seconds=2, start="play", source="saved", project=0, stems=True)
    assert sorted(s.stems) == [0, 1, 2, 3] and s.peak > 0.05
    peaks = []
    for k, p in s.stems.items():
        raw = p.read_bytes()[len(studio.wav_header(s.frames)):]
        fl = struct.unpack(f"<{len(raw) // 4}f", raw)
        peaks.append(max(abs(x) for x in fl))
    assert all(pk > 0.01 for pk in peaks), peaks          # every track plays something
    path = st.save_state(tmp_path / "x" / studio.STATE_NAME)
    meta, flash = studio.read_state(path)
    assert meta["firmware"] == "karma" and meta["firmware_version"] == "3.1" and len(flash) == 0x100000


def test_karma_in_backup_layer():
    """KARMA's backups are SLOOP's (format, commands, objects 0..10 and 32..35); its projects are format 7 (FUN7)."""
    from fm1companion import sloopbk
    from fm1companion.protocol import Info, parse_info
    assert sloopbk.dev_format(71, "FELUCCA KARMA 3.1") == 7 and sloopbk.dev_format(71, "FELUCCA SLOOP 2.6") == 6
    assert sloopbk.dsu_old(b"DSU1....") and not sloopbk.dsu_old(b"HEX1....")
    assert backup.SLOOP_PROTOCOL[:2] == ("sloop", "karma") and all(i in sloopbk.RESTORE + sloopbk.SAMPLES for i in KARMA_IDS)

    class FakeLink:                                     # an FM-1 that only answers INFO
        def __init__(self, version):
            self.raw = [*version.encode(), 0, 0, 71, 108, 64, 63, 4, 11]

        def request(self, cmd, args=(), timeout=1.0, retries=0):
            assert cmd == Cmd.INFO
            return self.raw
    assert parse_info(FakeLink("FELUCCA KARMA 3.1").raw).firmware_name == "karma"
    assert backup.protocol(FakeLink("FELUCCA KARMA 3.1"))[0] == backup.SLOOP
    assert backup.protocol(FakeLink("FELUCCA KARMA 3.1"), "karma")[0] == backup.SLOOP
    karma = {"format": "sloop-backup", "version": 1, "firmware": "FELUCCA KARMA 3.1", "date": "", "objects": [
        {"id": i, "len": 4, "crc": backup.crc32(b"\x37NUF"), "data": sloopbk.b64enc(b"\x37NUF")} for i in (0, 1)]}
    arch = backup.read(karma)
    with pytest.raises(backup.BackupError, match="KARMA"):              # KARMA's projects onto SLOOP: refused, unwritten
        backup._match(FakeLink("FELUCCA SLOOP 2.6"), arch)
    assert backup._match(FakeLink("FELUCCA KARMA 3.1"), arch)[0] == backup.SLOOP
    assert Info("FELUCCA KARMA 3.1").firmware_name == "karma"


# ------------------------------------------------------------------ Felucca (tools/studio/felucca_studio.c)
# hugelton/Felucca, the base of the family: v1.8.1 is the stable its web installer installs (gh-pages
# firmware/felucca-1.8.1.fwsc), v1.8.0 the stable before; its submodules felucca-bsp and Felucca-Engines at the commits
# the tags pin. Release builds: INFO "FELUCCA v1.8.1", identity FM-1_918 (tools/build.py --release 1.8.1)
FELUCCA_COMMIT = {"v1.8.0": "9f62297c2db627bbcb20d7ce896683a17318674c", "v1.8.1": "f4df2964649f301ba5abd0ec6000110068672f25"}
FELUCCA_STATUS = {"v1.8.0": "previous stable", "v1.8.1": "stable"}
FELUCCA_SOURCES = [
    {"path": "firmware/bsp", "repo": "https://github.com/hugelton/felucca-bsp",
     "commit": "5942e68b89ab9ccd23f2c541d712ca38fe635f11"},
    {"path": "firmware/engines", "repo": "https://github.com/hugelton/Felucca-Engines",
     "commit": "d0260074ce100a47675814b7f38d61f11176c926"}]


def test_felucca_builds_side_by_side():
    bs = studio.builds("felucca")
    assert [b.version for b in bs] == ["v1.8.0", "v1.8.1"]
    for b in bs:
        m = b.manifest
        assert b.verify() == [] and b.path.name == b.version.lstrip("v") and m["firmware_version"] == b.version
        assert m["kind"] == "studio" and m["abi"] == studio.STUDIO_ABI and m["firmware"] == "felucca"
        assert m["ref"] == b.version and m["commit"] == FELUCCA_COMMIT[b.version] and m["license"] == "GPL-3.0-only"
        assert m["release_status"] == FELUCCA_STATUS[b.version] and m["sources"] == FELUCCA_SOURCES
        assert m["identity"] == "FM-1_918" and m["fs"] == 44100 and m["flash_size"] == 0x100000
        assert m["tracks"] == 4 and m["projects"] == 4 and m["source_repo"] == "https://github.com/hugelton/Felucca"
        assert {"LICENSE", "NOTICE", "LICENSING.md", "THIRD_PARTY.txt", "twin.wasm"} <= set(m["files"])
        notice = (b.path / "NOTICE").read_text()
        assert "unmodified" in notice and "Written offer" in notice and f"Felucca-{m['commit']}.tar.gz" in notice
        assert all(s["commit"] in notice and s["repo"] in notice for s in FELUCCA_SOURCES)
        assert "Hügelton" in notice and m["glue"] == "tools/studio/felucca_studio.c"
        assert m["glue_sha256"] == hashlib.sha256((ROOT / m["glue"]).read_bytes()).hexdigest()
        third = (b.path / "THIRD_PARTY.txt").read_text()
        assert "Mozilla Public License" in third and "Apache License" in third and "SIL OPEN FONT LICENSE" in third.upper()
        assert "MIT" in third and "felucca-bsp" in third
    assert not any(s.key.startswith("felucca") for s in sims.bundled())
    # tools/make_source_tarballs.py packs the submodules' commits beside Felucca's own
    sys.path.insert(0, str(ROOT / "tools"))
    import make_source_tarballs as MST
    want = MST.wanted()
    assert ("Felucca", FELUCCA_COMMIT["v1.8.1"]) in want
    assert ("felucca-bsp", FELUCCA_SOURCES[0]["commit"]) in want and ("Felucca-Engines", FELUCCA_SOURCES[1]["commit"]) in want


def test_felucca_versions_and_pick(monkeypatch, tmp_path):
    b, exact, warn = studio.pick_build("felucca", "FELUCCA v1.8.1")      # Felucca's INFO
    assert exact and b.version == "v1.8.1" and not warn
    b, exact, warn = studio.pick_build("felucca", "v1.8.0")
    assert exact and b.version == "v1.8.0"
    b, exact, warn = studio.pick_build("felucca", "FELUCCA v1.8.2-beta")  # the beta: the next older one, with a warning
    assert not exact and b.version == "v1.8.1" and "Felucca" in warn
    b, exact, warn = studio.pick_build("felucca", "FELUCCA v1.6.1")      # older than everything: the oldest
    assert not exact and b.version == "v1.8.0" and "v1.6.1" in warn
    assert studio.pick_build("felucca")[0].version == "v1.8.1"            # without a wish: the newest = the stable
    assert studio.firmware_name("felucca") == "Felucca" and studio.FIRMWARES["felucca"]["projects"] == 4
    assert "Felucca" in studio.firmware_names()
    vc = studio.check_versions("v1.8.1", device="FELUCCA v1.8.0", firmware="Felucca")
    assert not vc.ok and "Felucca" in vc.message and "CZ-1" not in vc.message
    # the device layer: Felucca answers a plain INFO; SLOOP (the same identity FM-1_900 in its builds) names itself
    from fm1companion.protocol import Info
    from fm1companion.ui.studio_service import StudioService
    from fm1companion.ui.common import fw_label
    from fm1companion import firmware
    monkeypatch.setattr(firmware, "IDENT_FILE", tmp_path / "identities.json")
    assert Info("FELUCCA v1.8.1").firmware_name == "felucca" and Info("FELUCCA SLOOP 2.8.5").firmware_name == "sloop"
    assert StudioService.firmware_for("felucca") == "felucca" and StudioService.firmware_for("sloop") == "sloop"
    assert StudioService.firmware_for("felucca-mpe") == "felucca"         # a fork chosen from the catalog: with a warning
    assert fw_label("FELUCCA v1.8.1") == "FELUCCA v1.8.1"
    assert firmware.guess_firmware("FM-1_918", "FELUCCA v1.8.1", "felucca") == ("felucca", "v1.8.1")
    assert firmware.guess_firmware("FM-1_900", "FELUCCA v1.8.1", "felucca") == ("felucca", "v1.8.1")
    assert firmware.guess_firmware("FM-1_900", "FELUCCA SLOOP 2.8.5", "sloop") == ("sloop", "v2.8.5")
    assert firmware.guess_firmware("FM-1_722", "FELUCCA v2.2", "skiff")[0] == "skiff"
    assert firmware.guess_firmware("FM-1_909", "JIANT V10.6z2", "jiant")[0] == "jiant-fm"
    assert firmware.guess_firmware("FM-1_900") == ("", "")                # the identity alone: no guess
    # what this machine remembered for FM-1_900 (SLOOP, installed by the app) does not outweigh a plain Felucca INFO;
    # a remembered fork key does
    firmware.remember_identity("FM-1_900", "sloop", "v2.8.5")
    assert firmware.guess_firmware("FM-1_900", "FELUCCA v1.8.1", "felucca") == ("felucca", "v1.8.1")
    assert firmware.guess_firmware("FM-1_900", "FELUCCA SLOOP 2.8.5", "sloop")[0] == "sloop"
    firmware.remember_identity("FM-1_918", "felucca-mpe", "v1.8.1")
    assert firmware.guess_firmware("FM-1_918", "FELUCCA v1.8.1", "felucca")[0] == "felucca-mpe"
    # the fork note: a fork's key, or another identity than the twin's (a development build, FM-1_900)
    assert studio.felucca_fork_note("felucca", "FM-1_918", "FM-1_918") == ""
    assert studio.felucca_fork_note("felucca", "", "FM-1_918") == ""
    assert "felucca-mpe" in studio.felucca_fork_note("felucca-mpe", "", "FM-1_918")
    assert "FM-1_900" in studio.felucca_fork_note("felucca", "ota-FM-1_900", "FM-1_918")


@need_node
def test_felucca_twin_core_headless():
    r = subprocess.run([NODE, str(ROOT / "tests" / "studio_twin_felucca.mjs"), str(studio.SIMS_DIR / "felucca-studio")],
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-3000:]
    res = json.loads(r.stdout)
    assert {x["version"] for x in res} >= {"1.8.0", "1.8.1"}
    for x in res:
        v = x["firmware_version"]
        assert x["version_export"] == v and x["info"] == "FELUCCA " + v and x["ping"] == [0]
        assert bytes(x["identity_reply"]) == _identity_frame("FM-1_918")   # ota.c, as demo_device expects
        assert x["ids"] == backup.FELUCCA_IDS and x["screen_lit"] > 2000
        assert {0, 1, 2, 9, 32} <= {int(k) for k in x["sizes"]} and x["sizes"]["32"] == 1512   # music, settings, A, FM6, USR1
        assert all(rc == 0 for rc in x["put_rc"].values()) and all(rc == 0 for rc in x["smp_rc"])
        assert all(x["identical"].values()) and x["persisted"] and x["projects_used"] == [0]
        assert x["live_step0"] == {"note": 40, "vel": 100}
        a, b = x["takes"]
        assert a["playing"] and a["play_lit"] and a["peak"] > 0.05 and a["nonFinite"] == 0 and a["hash"] == b["hash"]
        assert x["fork_identical"] and x["stopped"] and x["song_play"] and x["editor_stop"]
        print(f"{v}: {a['realtime']:.1f}x real time (4 tracks)")
        assert a["realtime"] > 3


def _felucca_sample() -> bytes:
    """a valid user sample slot (eng_sample.c smp_user_hdr_t "FSMP", one zone over 1000 bytes of IMA ADPCM)"""
    import zlib
    data = bytes(((i * 37 + 11) & 255) for i in range(1000))
    h = bytearray(512)
    struct.pack_into("<IHBB8sII", h, 0, 0x504D5346, 1, 1, 0, b"TWIN", len(data), zlib.crc32(data))
    struct.pack_into("<IIIIIhhBBBB", h, 32, 0, 2000, 0, 1999, 65536, 60 * 16, 0, 0, 0, 127, 0)
    return bytes(h) + data


def _felucca_song(st):
    L = st.link
    for k in range(4):
        for i in range(0, 16, 2 + k % 3):
            L.request(Cmd.TRACK_STEP, [k, i, 1, 40 + 3 * k + (i % 5), 0, 0, 0, 0, 0, 100])
    assert L.request(Cmd.PROJECT, [1, 0])[2] == 1                          # project A
    backup._restore_sample(L, 0, _felucca_sample(), lambda n: None)        # USR1 as Felucca's editor uploads it


def test_felucca_backup_round_trip_with_suite_code(node_studio):
    a = node_studio(firmware="felucca", version="FELUCCA v1.8.0")
    assert a.build.firmware == "felucca" and a.build.version == "v1.8.0" and a.tracks == 4 and a.project_slots == 4
    assert studio.device_version(a.link) == "v1.8.0" and a.info_name == "FELUCCA"
    assert backup.protocol(a.link, "felucca")[0] == backup.FELUCCA        # (the hint "felucca": its INFO decides)
    _felucca_song(a)
    arch = a.backup()
    assert [o["id"] for o in arch.objects] == backup.FELUCCA_IDS and arch.known
    by = {o["id"]: o["bytes"] for o in arch.objects}
    smp = _felucca_sample()                              # (the header's last 32 bytes stay erased flash: SMP_END writes 480)
    assert len(by[0]) == len(by[2]) == 3840 and len(by[9]) == 3728 and by[8] == b""
    assert by[32][:480] == smp[:480] and by[32][512:] == smp[512:] and len(by[32]) == len(smp)
    assert studio.backup_version(arch) == "v1.8.0" and studio.backup_firmware(arch) == "felucca"
    assert arch.companion["version_string"] == "FELUCCA v1.8.0"
    # a backup of Felucca's own web editor (no companion block; 'firmware' the INFO string) and one with an unknown
    # name: the 13-object inventory tells
    plain = json.loads(backup.dumps(arch.file))
    plain.pop("companion")
    plain["firmware"] = "FELUCCA v1.8.0"
    assert studio.backup_firmware(backup.read(plain)) == "felucca" and backup.read(plain).known
    plain["firmware"] = "?"
    assert studio.backup_firmware(backup.read(plain)) == "felucca"
    plain["firmware"] = "FELUCCA v1.8.0"
    # into the 1.8.1 twin through the suite's restore: every object, the sample through SMP_*
    b = node_studio(firmware="felucca", version="v1.8.1")
    b.open_from_backup(backup.read(plain))
    back = {o["id"]: o["bytes"] for o in b.backup().objects}
    assert all(back[i] == by[i] for i in backup.FELUCCA_IDS if i != 1) and b.projects() == [0] and not b.rejected
    vc = b.check_versions(archive=arch)
    assert not vc.ok and "Felucca" in vc.message
    c = studio.Studio.for_backup(arch, backend="node")                     # the backup's firmware and version
    try:
        assert c.build.firmware == "felucca" and c.build.version == "v1.8.0" and not c.warning and c.projects() == [0]
        assert c.load_project(0) == 0 and c.load_project(1) == 1
        assert c.play()["busy"] and not c.stop()["busy"]                   # st_transport_set
        assert c.play("song")["busy"] and not c.stop()["busy"]
    finally:
        c.close()
    # the tolerant load (the studio page's "Fetch from FM-1") takes the sample slots too: writing back keeps them
    d = node_studio(firmware="felucca", version="v1.8.1")
    d.open_from_backup(arch, tolerant=True)
    assert d.rejected == [] and {o["id"]: o["bytes"] for o in d.backup().objects}[32] == by[32]
    # writing back as the page does, without the settings, onto a "device" (another twin of the same version)
    e = node_studio(firmware="felucca", version="v1.8.1")
    e_settings = {o["id"]: o["bytes"] for o in e.backup().objects}[1]
    out = d.write_to_device(e.link, settings=False, exclude=d.rejected)
    now = {o["id"]: o["bytes"] for o in e.backup().objects}
    assert now[1] == e_settings and now[2] == by[2] and now[32] == by[32] and e.projects() == [0]
    assert [o["id"] for o in out.objects] == backup.FELUCCA_IDS
    with pytest.raises(studio.StudioError):                                 # another version on the device: refused
        d.write_to_device(a.link)
    sl = node_studio(firmware="sloop")                                      # (FM-1_900 is SLOOP's identity too)
    with pytest.raises(backup.BackupError, match="SLOOP"):                  # Felucca's data onto SLOOP: refused
        d.write_to_device(sl.link, force=True)
    # the inspector: Felucca's editor's inventory (no "unknown inventory" warning), object 9 its FM6 patches
    from fm1companion import backup_inspect as BI
    rep = BI.inspect(backup.read(plain))
    assert rep.firmware_key == "felucca" and rep.known and not rep.warnings
    assert rep.obj(9).group == BI.G_PRESETS and "FM6" in rep.obj(9).title and "CZ" not in rep.obj(9).title
    assert rep.obj(32).group == BI.G_SAMPLES and rep.obj(2).group == BI.G_PROJECTS
    assert BI.compatibility(rep, "felucca", "FELUCCA v1.8.1", backup.FELUCCA_IDS).ok
    assert not BI.compatibility(rep, "sloop", "FELUCCA SLOOP 2.8.5", list(range(10)) + [32, 33, 34, 35]).ok
    assert "FM6" in backup.object_title(9, felucca=True) and "CZ" not in backup.object_title(9, felucca=True)


def test_felucca_render_stems(node_studio, tmp_path):
    st = node_studio(firmware="felucca")
    _felucca_song(st)
    s = st.render_wav(tmp_path / "mix.wav", seconds=2, start="play", source="saved", project=0, stems=True)
    assert sorted(s.stems) == [0, 1, 2, 3] and s.peak > 0.05
    peaks = []
    for k, p in s.stems.items():
        raw = p.read_bytes()[len(studio.wav_header(s.frames)):]
        fl = struct.unpack(f"<{len(raw) // 4}f", raw)
        peaks.append(max(abs(x) for x in fl))
    assert all(pk > 0.002 for pk in peaks), peaks         # every track plays something (T2's default sound is soft)
    path = st.save_state(tmp_path / "x" / studio.STATE_NAME)
    meta, flash = studio.read_state(path)
    assert meta["firmware"] == "felucca" and meta["firmware_version"] == "v1.8.1" and len(flash) == 0x100000


def test_felucca_in_backup_layer():
    """Felucca's 13 objects (editor_backup.c ED_BK_IDS: 0..9, the sample slots 32..34); its backups and SLOOP's apart."""
    from fm1companion.protocol import parse_info
    ids = backup.FELUCCA_IDS
    assert ids == [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 32, 33, 34] and backup.known_ids(ids) == ids
    assert backup.known_ids(list(range(13))) == []
    assert backup.is_felucca(ids) and backup.is_felucca(ids, "?") and not backup.is_felucca(list(range(13)))
    old = [i for i in ids if i != 9]                                      # 1.0..1.0.2 (Melodee's inventory too)
    assert backup.is_felucca(old, "FELUCCA v1.0.2") and not backup.is_felucca(old, "MELODEE v1.5")
    assert not backup.is_felucca(backup.JIANT_IDS) and not backup.is_felucca(studio.SKIFF_BACKUP_IDS, "FELUCCA v2.2")
    assert backup.is_sample(32) and backup.is_project(2) and not backup.is_project(9)
    assert "U01–U16" in backup.object_title(6, felucca=True)

    class FakeLink:                                     # an FM-1 that only answers INFO
        def __init__(self, version):
            self.raw = [*version.encode(), 0, 0, 71, 108, 64, 63, 4, 11]

        def request(self, cmd, args=(), timeout=1.0, retries=0):
            assert cmd == Cmd.INFO
            return self.raw
    assert parse_info(FakeLink("FELUCCA v1.8.1").raw).firmware_name == "felucca"
    assert backup.protocol(FakeLink("FELUCCA v1.8.1"), "felucca")[0] == backup.FELUCCA
    # a SLOOP FM-1 whose profile says "felucca" (FM-1_900 is both's): its INFO wins – SLOOP's commands
    assert backup.protocol(FakeLink("FELUCCA SLOOP 2.8.5"), "felucca")[0] == backup.SLOOP
    sloop = {"format": "sloop-backup", "version": 1, "firmware": "FELUCCA SLOOP 2.8.5", "date": "", "objects": []}
    arch = backup.read({"format": backup.FORMAT, "version": 1, "firmware": "FELUCCA v1.8.1", "objects": [
        {"id": i, "size": 0, "crc": 0, "data": ""} for i in ids]} | {"objects": [
        {"id": i, "size": 4 if i < 2 else 0, "crc": backup.crc32(b"abcd") if i < 2 else 0,
         "data": "YWJjZA==" if i < 2 else ""} for i in ids]})
    with pytest.raises(backup.BackupError, match="SLOOP"):                 # a Felucca backup onto SLOOP: refused
        backup._match(FakeLink("FELUCCA SLOOP 2.8.5"), arch, "felucca")
    assert backup._match(FakeLink("FELUCCA v1.8.1"), arch, "felucca")[0] == backup.FELUCCA
    assert sloop["format"] == backup.SLOOP_FORMAT


@pytest.mark.parametrize("fw", ["skiff", "felucca", "sloop", "karma"])
def test_fetch_and_write_back_keep_the_sample_slots(node_studio, fw):
    """"Fetch from FM-1" (the tolerant load) and "Write back" keep the device's user samples: before, the tolerant load left
    the sample slots out of the twin, and writing back then erased them on the FM-1 (SMP_ERASE of the empty slot).
    (SLOOP and KARMA: SLOOP's protocol, sloopbk.restore_tolerant takes the sample slots 32..35 too.)"""
    dev = node_studio(firmware=fw)                                           # the "device": a twin of the same version
    if studio.FIRMWARES[fw].get("backup") == "sloop":
        from fm1companion import sloopbk
        sloopbk.write_sample(dev.link, 32, _felucca_sample())
    else:
        backup._restore_sample(dev.link, 0, _felucca_sample(), lambda n: None)
    usr1 = {o["id"]: o["bytes"] for o in dev.backup().objects}[32]
    assert len(usr1) == len(_felucca_sample())
    st = node_studio(firmware=fw)
    st.open_from_device(dev.link, tolerant=True)
    assert st.rejected == [] and {o["id"]: o["bytes"] for o in st.backup().objects}[32] == usr1
    st.write_to_device(dev.link, settings=False, exclude=st.rejected)
    assert {o["id"]: o["bytes"] for o in dev.backup().objects}[32] == usr1


def test_sample_slot_twins_are_flagged(node_studio):
    """every twin whose backup (Felucca's protocol) has sample slots says so in FIRMWARES ("samples": the tolerant load
    takes them, writing back keeps them); SLOOP's protocol always takes them (sloopbk.restore_tolerant); the firmwares
    with their own backup format (FM1 Open, X0X, LOOM-8) or none (AMB-1, FoMni) keep or leave out their samples
    themselves. Checked on every bundled version (Melodee 1.6, JIANT, CoralFlower, ChoralRoot: no sample slots)."""
    for fw, spec in studio.FIRMWARES.items():
        if studio.no_backup(fw) or spec.get("backup") in ("sloop", "fm1open", "x0x", "loom-8"):
            continue
        for b in studio.builds(fw):
            arch = node_studio(firmware=fw, version=b.version).backup()
            ids = [o["id"] for o in arch.objects]
            has = any(backup.is_sample(i, backup.is_jiant(ids), arch.coralflower, arch.choralroot) for i in ids)
            assert has == bool(spec.get("samples")), (fw, b.version, ids)


# ------------------------------------------------------------------ X0X (tools/studio/x0x_studio.c)
# X0X (charlesvestal/fm1-x0x): v1.2.1 is its latest release (2026-11-05), v1.2.0 the one before. Its release workflow
# builds tools/build.py --release X.Y.Z: INFO "X0X X.Y.Z BETA", the identity FM-1_9XXYYZZ. Its own backup protocol
# (backup.c: the project whole, in the update's framing) and image ("X0XB", six objects): x0xbk.py.
X0X_COMMIT = {"1.2.0": "ca2d377e3b9522579245152a81781a575f4d2fea", "1.2.1": "502b2c28c14ab2bff31eb4f0777fdfd0efcf8bea"}
X0X_STATUS = {"1.2.0": "previous stable", "1.2.1": "stable"}


def test_x0x_builds_side_by_side():
    bs = studio.builds("x0x")
    vs = [b.version for b in bs]
    assert vs == ["1.2.0", "1.2.1"] and [b.path.name for b in bs] == vs
    for b in bs:
        m = b.manifest
        assert b.verify() == []
        assert m["kind"] == "studio" and m["abi"] == studio.STUDIO_ABI and m["firmware"] == "x0x"
        assert m["commit"] == X0X_COMMIT[b.version] and m["ref"] == "v" + b.version and m["version"] == b.version
        assert m["firmware_version"] == b.version and m["release_status"] == X0X_STATUS[b.version]
        assert m["identity"] == "FM-1_90102%02d" % int(b.version.split(".")[2]) and m["license"] == "GPL-3.0-only"
        assert m["fs"] == 44100 and m["flash_size"] == 0x100000 and m["tracks"] == 5 and m["projects"] == 0
        assert m["source_repo"] == "https://github.com/charlesvestal/fm1-x0x"
        assert {"LICENSE", "NOTICE", "LICENSING.md", "THIRD_PARTY.txt", "twin.wasm"} <= set(m["files"])
        notice = (b.path / "NOTICE").read_text()
        assert "unmodified" in notice and "Written offer" in notice and f"fm1-x0x-{m['commit']}.tar.gz" in notice
        assert "Charles Vestal" in notice and "Open303" in notice and m["glue"] == "tools/studio/x0x_studio.c"
        assert m["glue_sha256"] == hashlib.sha256((ROOT / m["glue"]).read_bytes()).hexdigest()
        assert "Barlow" in (b.path / "THIRD_PARTY.txt").read_text()
        assert "9W9" in (b.path / "LICENSING.md").read_text()
    assert not any(s.key == "x0x-studio" for s in sims.bundled())


def test_x0x_versions_and_pick():
    b, exact, warn = studio.pick_build("x0x", "X0X 1.2.1 BETA")             # X0X's INFO
    assert exact and b.version == "1.2.1" and not warn
    assert studio.pick_build("x0x", "X0X 1.2.0 BETA")[0].version == "1.2.0"
    b, exact, warn = studio.pick_build("x0x", "X0X 1.1.0 BETA")             # older than everything: the oldest
    assert not exact and b.version == "1.2.0" and "X0X" in warn
    b, exact, warn = studio.pick_build("x0x", "X0X 1.3 BETA")               # newer: the next older one
    assert not exact and b.version == "1.2.1"
    assert studio.pick_build("x0x")[0].version == "1.2.1"
    assert studio.check_versions("1.2.1", device="X0X 1.2.1 BETA", firmware="X0X").ok
    assert studio.firmware_name("x0x") == "X0X" and studio.FIRMWARES["x0x"]["projects"] == 0
    from fm1companion import firmware
    from fm1companion.protocol import Info
    from fm1companion.ui.studio_service import StudioService
    assert Info("X0X 1.2.1 BETA").firmware_name == "x0x" and StudioService.firmware_for("x0x") == "x0x"
    assert firmware.guess_firmware("", "X0X 1.2.1 BETA") == ("x0x", "v1.2.1")


@need_node
def test_x0x_twin_core_headless():
    r = subprocess.run([NODE, str(ROOT / "tests" / "studio_twin_x0x.mjs"), str(studio.SIMS_DIR / "x0x-studio")],
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-3000:]
    res = json.loads(r.stdout)
    assert {x["version"] for x in res} >= {"1.2.0", "1.2.1"}
    for x in res:
        assert x["info"] == x["version_export"] == f"X0X {x['version']} BETA"
        assert x["records"] == [[0, 36], [1, 1348], [2, 1800], [3, 4], [5, 4], [6, 4]] or \
            [r[0] for r in x["records"]] == [0, 1, 2, 3, 5, 6]
        assert x["restore_rc"] == 0 and x["identical"] and x["playing_rc"] == 10 and x["damaged_rc"] == 5
        a, b = x["takes"]
        assert a["playing"] and a["play_lit"] and a["peak"] > 0.1 and a["hash"] == b["hash"] and a["clocks"] > 150
        assert x["fork_identical"] and x["stopped"] and x["song_play"] and x["restart_play"]
        assert x["midi_in_peak"] > 0.1 and x["part_peak"][0] > 0.1 and x["part_peak"][1] == 0
        print(f"{x['version']}: {a['realtime']:.1f}x real time")
        assert a["realtime"] > 3


def _x0x_pattern(st):
    for k in (0, 7, 14, 21):                             # the 909's steps 1, 5, 9, 13 (white keys 1, 5, 9, 13)
        st.backend.call("keys", mask=1 << k)
        st.backend.call("run", ms=40)
        st.backend.call("keys", mask=0)
        st.backend.call("run", ms=40)


def test_x0x_backup_round_trip_with_suite_code(node_studio, tmp_path):
    from fm1companion import backup_inspect as BI, x0xbk
    a = node_studio(firmware="x0x", version="X0X 1.2.0 BETA")
    assert a.build.version == "1.2.0" and a.tracks == 5 and a.project_slots == 0 and a.info_name == "X0X"
    assert studio.device_version(a.link) == "v1.2.0" and backup.protocol(a.link)[0] == backup.X0X
    _x0x_pattern(a)
    arch = a.backup()
    assert arch.x0x and [o["id"] for o in arch.objects] == x0xbk.OBJ_IDS and arch.firmware == "X0X 1.2.0 BETA"
    assert studio.backup_firmware(arch) == "x0x" and studio.backup_version(arch) == "v1.2.0"
    assert all(o["size"] for o in arch.objects[:3]) and arch.project_objects() == []
    rep = BI.inspect(arch)
    assert rep.format == backup.X0X_FORMAT and rep.firmware_key == "x0x" and rep.obj(2).title.startswith("Patterns")
    assert BI.compatibility(rep, "x0x", "X0X 1.2.1 BETA", x0xbk.OBJ_IDS).ok
    assert not BI.compatibility(rep, "melodee", "MELODEE v1.6.1", None).ok
    # the suite's JSON file and X0X's own .x0b file (tools/x0x_backup.py, web/backup) read the same
    p = tmp_path / "x.json"
    backup.write_file(p, arch.file)
    raw = x0xbk.image_of(arch.file)
    (tmp_path / "x.x0b").write_bytes(raw)
    for src in (backup.load_file(p), backup.load_file(tmp_path / "x.x0b"), backup.read(raw)):
        assert [o["bytes"] for o in src.objects] == [o["bytes"] for o in arch.objects] and src.x0x
    assert studio.backup_firmware(backup.load_file(tmp_path / "x.x0b")) == "x0x"
    assert BI.inspect(tmp_path / "x.x0b").firmware_key == "x0x"
    # into a 1.2.1 twin (the image on its side of the cable, restore_rx checks and commits it), then back
    b = node_studio(firmware="x0x", version="1.2.1")
    b.open_from_backup(backup.load_file(tmp_path / "x.x0b"))
    assert [o["bytes"] for o in b.backup().objects] == [o["bytes"] for o in arch.objects] and not b.rejected
    c = studio.Studio.for_backup(arch, backend="node")
    try:
        assert c.build.version == "1.2.0" and not c.warning
        assert c.play()["busy"] and not c.stop()["busy"]
        assert c.play("song")["busy"] and not c.stop()["busy"]
    finally:
        c.close()
    # refused: while X0X plays (nothing changes), a part only (X0X takes its image whole), a damaged image
    d = node_studio(firmware="x0x")
    d.play()
    with pytest.raises(backup.BackupError):              # (STOP FIRST: RS_FAIL_ADDR | 10)
        backup.restore(d.link, arch)
    d.stop()
    assert not d.backup().objects[2]["size"]
    with pytest.raises(backup.BackupError, match="X0X"):  # (a part alone: X0X takes its image whole)
        backup.restore_selected(d.link, arch, [1, 2])
    assert backup.restore_selected(d.link, arch, x0xbk.OBJ_IDS) == x0xbk.OBJ_IDS
    assert [o["bytes"] for o in d.backup().objects] == [o["bytes"] for o in arch.objects]
    bad = bytearray(raw)
    bad[30] ^= 1
    with pytest.raises(backup.BackupError):
        backup.read(bytes(bad))
    # another firmware's backup onto X0X, X0X's onto another firmware: refused before anything is written
    m = node_studio()
    with pytest.raises(backup.BackupError, match="X0X"):
        backup.restore(m.link, arch)
    with pytest.raises(backup.BackupError, match="X0X"):
        backup.restore(d.link, m.backup())


def test_x0x_write_back_keeps_the_device_settings(node_studio):
    """write_to_device(settings=False) onto an X0X: the image whole, with the device's own settings (object 0) in it"""
    a = node_studio(firmware="x0x")
    _x0x_pattern(a)
    dev = node_studio(firmware="x0x")
    for _i in range(4):                                 # HOME held, SEQ tapped: the tempo (200 BPM), a setting
        dev.backend.call("buttons", mask=1 << 8 | 1 << 11)
        dev.backend.call("run", ms=40)
        dev.backend.call("buttons", mask=1 << 8)
        dev.backend.call("run", ms=260)
    dev.backend.call("buttons", mask=0)
    dev.backend.call("run", ms=3000)
    before = {o["id"]: o["bytes"] for o in dev.backup().objects}
    a.write_to_device(dev.link, settings=False)
    after = {o["id"]: o["bytes"] for o in dev.backup().objects}
    mine = {o["id"]: o["bytes"] for o in a.backup().objects}
    assert before[0] != mine[0] and after[0] == before[0] and after[2] == mine[2] and after[1] == mine[1]


def test_x0x_render_stems(node_studio, tmp_path):
    st = node_studio(firmware="x0x")
    _x0x_pattern(st)
    s = st.render_wav(tmp_path / "mix.wav", seconds=2, start="play", source="live", stems=True)
    assert sorted(s.stems) == [0, 1, 2, 3, 4] and s.peak > 0.1
    peaks = []
    for k, p in sorted(s.stems.items()):
        raw = p.read_bytes()[len(studio.wav_header(s.frames)):]
        fl = struct.unpack(f"<{len(raw) // 4}f", raw)
        peaks.append(max(abs(x) for x in fl))
    assert peaks[0] > 0.1 and max(peaks[1:]) < 1e-3, peaks       # (the pattern is the 909's alone)


# ------------------------------------------------------------------ AMB-1 (tools/studio/fm1base_studio.c)
# AMB-1 (charlesvestal/fm1-amb, on fm1-base and felucca-bsp, its submodules): v1.3.2 is its latest release (2026-11-06),
# v1.3.1 the one before. fm1-base's tools/build.py --release X.Y.Z: APP_VERSION "X.Y.Z", the identity FM-1_7XXYYZZ.
# No editor (no INFO), no backup over USB.
AMB_COMMIT = {"1.3.1": "285215af014b9c6a7c2b404c538d9261e8a9d29f", "1.3.2": "7c538516d6c52216ff4c8a4881cf2f0dcb07c44b"}
AMB_BASE = {"1.3.1": "9bca7154c7db1fc4db7381502045dcc67fbf8d74", "1.3.2": "3ae2a65ddd02e15bf9ce925b5a7136d5d207ee25"}


def test_amb_builds_side_by_side():
    bs = studio.builds("amb-1")
    vs = [b.version for b in bs]
    assert vs == ["1.3.1", "1.3.2"] and [b.path.name for b in bs] == vs
    for b in bs:
        m = b.manifest
        assert b.verify() == [] and m["kind"] == "studio" and m["abi"] == studio.STUDIO_ABI and m["firmware"] == "amb-1"
        assert m["commit"] == AMB_COMMIT[b.version] and m["ref"] == "v" + b.version and m["firmware_version"] == b.version
        assert m["identity"] == "FM-1_70103%02d" % int(b.version.split(".")[2]) and m["license"] == "MPL-2.0"
        assert m["tracks"] == 4 and m["projects"] == 0 and m["fs"] == 44100 and m["flash_size"] == 0x100000
        assert [s["path"] for s in m["sources"]] == ["base", "base/bsp"] and m["sources"][0]["commit"] == AMB_BASE[b.version]
        assert m["release_status"] == ("stable" if b.version == "1.3.2" else "previous stable")
        assert {"LICENSE", "NOTICE", "LICENSING.md", "LICENSING-fm1-base.md", "THIRD_PARTY.txt", "twin.wasm"} <= set(m["files"])
        notice = (b.path / "NOTICE").read_text()
        assert "unmodified" in notice and "Written offer" in notice and AMB_BASE[b.version] in notice
        assert "Mozilla Public License" in (b.path / "LICENSE").read_text() and m["glue"] == "tools/studio/fm1base_studio.c"
        assert m["glue_sha256"] == hashlib.sha256((ROOT / m["glue"]).read_bytes()).hexdigest()


def test_amb_versions_and_pick():
    assert studio.pick_build("amb-1")[0].version == "1.3.2"
    b, exact, warn = studio.pick_build("amb-1", "1.3")
    assert not exact and b.version == "1.3.1" and "AMB-1" in warn
    assert studio.pick_build("amb-1", "v1.3.1")[1]
    from fm1companion.ui.studio_service import StudioService
    assert StudioService.firmware_for("amb-1") == "amb-1" and StudioService.firmware_for("ambwave") == ""
    assert studio.firmware_name("amb-1") == "AMB-1" and studio.no_backup("amb-1") and not studio.no_backup("x0x")


@need_node
def test_amb_twin_core_headless():
    r = subprocess.run([NODE, str(ROOT / "tests" / "studio_twin_amb.mjs"), str(studio.SIMS_DIR / "amb-1-studio")],
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-3000:]
    res = json.loads(r.stdout)
    assert {x["version"] for x in res} >= {"1.3.1", "1.3.2"}
    for x in res:
        assert x["identity"] == "FM-1_701" and x["live"]["peak"] > 0.05 and x["flash_used"] > 0
        assert x["fork_identical"] and x["resting"] and x["growing"]
        print(f"{x['version']}: {x['takes'][0]['realtime']:.1f}x real time")
        assert x["takes"][0]["realtime"] > 3


def test_amb_studio_without_backup(node_studio, tmp_path):
    st = node_studio(firmware="amb-1")
    assert st.build.version == "1.3.2" and st.tracks == 4 and st.project_slots == 0 and st.transport_state()["busy"]
    for call in (lambda: st.backup(), lambda: st.open_from_backup({"format": "felucca-backup"}),
                 lambda: st.open_from_device(st.link), lambda: st.write_to_device(st.link)):
        with pytest.raises(studio.StudioError, match="AMB-1"):
            call()
    s = st.render_wav(tmp_path / "mix.wav", seconds=3, start="none", source="live", stems=True)
    assert sorted(s.stems) == [0, 1, 2, 3] and s.peak > 0.05
    assert not st.stop()["busy"] and st.play()["busy"]
    path = st.save_state(tmp_path / "x" / studio.STATE_NAME)
    meta, flash = studio.read_state(path)
    assert meta["firmware"] == "amb-1" and meta["firmware_version"] == "1.3.2" and len(flash) == 0x100000


# ------------------------------------------------------------------ FoMni (tools/studio/fomni_studio.c)
# FoMni (charlesvestal/fm1-fomni, FoMni-1, on X0X's platform): v0.4.1 is its latest release (2026-11-05), v0.4 the one
# before; tools/build.py --release X.Y[.Z]: "X.Y.Z BETA", the identity FM-1_8XXYYZZ. No editor, no backup over USB.
FOMNI_COMMIT = {"0.4": "6ecd227013cc18def0e89d035b3760eb268b342b", "0.4.1": "17a740b7b2d19615ffe19aa73f8eda67f60f223d"}


def test_fomni_builds_side_by_side():
    bs = studio.builds("fomni")
    assert [b.version for b in bs] == ["0.4", "0.4.1"] and [b.path.name for b in bs] == ["0.4", "0.4.1"]
    for b in bs:
        m = b.manifest
        assert b.verify() == [] and m["kind"] == "studio" and m["firmware"] == "fomni" and m["abi"] == studio.STUDIO_ABI
        assert m["commit"] == FOMNI_COMMIT[b.version] and m["ref"] == "v" + b.version and m["license"] == "GPL-3.0-only"
        assert m["identity"] == {"0.4": "FM-1_8000400", "0.4.1": "FM-1_8000401"}[b.version]
        assert m["tracks"] == 4 and m["projects"] == 0 and m["release_status"] == ("stable" if b.version == "0.4.1"
                                                                                   else "previous stable")
        notice = (b.path / "NOTICE").read_text()
        assert "unmodified" in notice and "Written offer" in notice and "Chordian" in notice
        assert m["glue_sha256"] == hashlib.sha256((ROOT / m["glue"]).read_bytes()).hexdigest()
    assert studio.pick_build("fomni")[0].version == "0.4.1" and studio.no_backup("fomni")
    from fm1companion.ui.studio_service import StudioService
    assert StudioService.firmware_for("fomni") == "fomni" and studio.firmware_name("fomni") == "FoMni"


@need_node
def test_fomni_twin_core_headless():
    r = subprocess.run([NODE, str(ROOT / "tests" / "studio_twin_fomni.mjs"), str(studio.SIMS_DIR / "fomni-studio")],
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-3000:]
    res = json.loads(r.stdout)
    assert {x["version"] for x in res} >= {"0.4", "0.4.1"}
    for x in res:
        assert x["identity"] == ("FM-1_800" if x["version"] == "0.4.1" else "FM-1_8000400")   # (0.4.1: M-UPGRADE 3.11's)
        assert x["notes"] > 0 and x["rhythm"] and x["flash_used"] > 0 and x["fork_identical"]
        assert x["takes"][0]["realtime"] > 3


def test_fomni_studio_without_backup(node_studio, tmp_path):
    st = node_studio(firmware="fomni")
    assert st.build.version == "0.4.1" and st.tracks == 4 and not st.transport_state()["busy"]
    with pytest.raises(studio.StudioError, match="FoMni"):
        st.backup()
    assert st.play()["busy"]
    s = st.render_wav(tmp_path / "mix.wav", seconds=2, start="play", source="live", stems=True)
    assert sorted(s.stems) == [0, 1, 2, 3] and s.peak > 0.05


# ------------------------------------------------------------------ LOOM-8 (tools/studio/fm1base_studio.c)
# LOOM-8 (charlesvestal/fm1-loom, on fm1-base and felucca-bsp): v0.14 is its latest release (2026-11-06), v0.13.1 the
# one before; the identity FM-1_10XXYYZZ. No editor (no INFO); fm1-base's backup (x0xbk.py's "LM8B" profile).
LOOM_COMMIT = {"0.13.1": "a89a8fd23600705a51b928578b5464b4530f47bd", "0.14": "faf459d1f9cea3e634b6e2622a1c3e91d58b3452"}
LOOM_BASE = {"0.13.1": "3ae2a65ddd02e15bf9ce925b5a7136d5d207ee25", "0.14": "317cb86cc60d6286fa80a02743e94e6a60ff842d"}


def test_loom_builds_side_by_side():
    bs = studio.builds("loom-8")
    assert [b.version for b in bs] == ["0.13.1", "0.14"] and [b.path.name for b in bs] == ["0.13.1", "0.14"]
    for b in bs:
        m = b.manifest
        assert b.verify() == [] and m["kind"] == "studio" and m["firmware"] == "loom-8" and m["license"] == "GPL-3.0-only"
        assert m["commit"] == LOOM_COMMIT[b.version] and m["sources"][0]["commit"] == LOOM_BASE[b.version]
        assert m["identity"] == {"0.13.1": "FM-1_10001301", "0.14": "FM-1_10001400"}[b.version]
        assert m["tracks"] == 8 and m["projects"] == 0 and {"COPYING", "LICENSE", "LICENSING.md"} <= set(m["files"])
        notice = (b.path / "NOTICE").read_text()
        assert "GPL-3.0-only as a whole" in notice and LOOM_BASE[b.version] in notice and "Written offer" in notice
        assert m["glue_sha256"] == hashlib.sha256((ROOT / m["glue"]).read_bytes()).hexdigest()
    from fm1companion.ui.studio_service import StudioService
    assert StudioService.firmware_for("loom-8") == "loom-8" and StudioService.firmware_for("loom-fm-1") == ""
    assert studio.pick_build("loom-8")[0].version == "0.14"


@need_node
def test_loom_twin_core_headless():
    r = subprocess.run([NODE, str(ROOT / "tests" / "studio_twin_loom.mjs"), str(studio.SIMS_DIR / "loom-8-studio")],
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-3000:]
    res = json.loads(r.stdout)
    assert {x["version"] for x in res} >= {"0.13.1", "0.14"}
    for x in res:
        assert x["restore_rc"] == 0 and x["identical"] and x["playing_rc"] == 10 and x["damaged_rc"] == 5
        assert x["notes_out"] > 0 and x["fork_identical"] and x["takes"][0]["hash"] == x["takes"][1]["hash"]
        assert x["takes"][0]["realtime"] > 3


def _loom_steps(st):
    for k in (0, 7, 14, 21):
        st.backend.call("keys", mask=1 << k)
        st.backend.call("run", ms=60)
        st.backend.call("keys", mask=0)
        st.backend.call("run", ms=60)
    st.press("SAVE")
    st.backend.call("run", ms=3000)


def test_loom_backup_round_trip_with_suite_code(node_studio, tmp_path):
    from fm1companion import backup_inspect as BI, x0xbk
    a = node_studio(firmware="loom-8", version="0.13.1")
    assert a.build.version == "0.13.1" and a.tracks == 8 and backup.protocol(a.link, "loom-8")[0] == backup.LOOM
    _loom_steps(a)
    arch = a.backup()
    assert arch.image == "loom-8" and arch.format == backup.LOOM_FORMAT and not arch.x0x
    assert [o["id"] for o in arch.objects] == list(x0xbk.LOOM_PROFILE.ids) and any(o["size"] for o in arch.objects)
    assert studio.backup_firmware(arch) == "loom-8" and studio.backup_version(arch) == "v0.13.1"
    rep = BI.inspect(arch)
    assert rep.firmware_key == "loom-8" and rep.obj(1).title and rep.obj(2).title != rep.obj(3).title
    raw = x0xbk.image_of(arch.file)
    (tmp_path / "x.loom8").write_bytes(raw)                 # (LOOM-8's own backup page's file)
    assert [o["bytes"] for o in backup.load_file(tmp_path / "x.loom8").objects] == [o["bytes"] for o in arch.objects]
    b = node_studio(firmware="loom-8")
    b.open_from_backup(backup.load_file(tmp_path / "x.loom8"))
    assert [o["bytes"] for o in b.backup().objects] == [o["bytes"] for o in arch.objects] and not b.rejected
    with pytest.raises(backup.BackupError, match="LOOM-8"):
        backup.restore_selected(b.link, arch, [1])
    x = node_studio(firmware="x0x")
    with pytest.raises(backup.BackupError, match="LOOM-8"):   # (an X0X FM-1 refuses LOOM-8's image, nothing written)
        backup.restore(x.link, arch)
    s = b.render_wav(tmp_path / "mix.wav", seconds=2, start="play", source="live", stems=True)
    assert sorted(s.stems) == list(range(8)) and s.peak > 0.05
