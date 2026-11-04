# SPDX-License-Identifier: GPL-3.0-only
"""Studio-Zwilling: Builds/Versionen, der Firmware-Kern in node (tests/studio_twin.mjs) und die Python-Fassade
(fm1companion/studio.py) mit dem node-Backend – Backup lesen/einspielen mit dem Code der Suite, Rendern, Stems,
Studio-Stand, Versionswahl. Die QtWebEngine-Seite prüft tests/ui_studio_test.py.

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
need_node = pytest.mark.skipif(not NODE, reason="node fehlt")
FIX = ROOT / "tests" / "fixtures" / "musik"
# tools/fm1pkg_make.py release_product: 1.6.0(-rc.x) -> FM-1_91060, 1.6.1 -> FM-1_91061
IDENTITY = {"v1.6.0-rc.7": "FM-1_91060", "v1.6.0-rc.8": "FM-1_91060", "v1.6.0": "FM-1_91060", "v1.6.1": "FM-1_91061"}


# ------------------------------------------------------------------ Builds
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
    # die Studio-Builds sind keine Simulator-Seiten
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
    b, exact, warn = studio.pick_build("melodee", "MELODEE v1.6.0")     # die fertige 1.6.0: genau die
    assert exact and b.version == "v1.6.0" and not warn
    b, exact, warn = studio.pick_build("melodee", "MELODEE v1.6.1")     # 1.6.1: genau die
    assert exact and b.version == "v1.6.1" and not warn
    b, exact, warn = studio.pick_build("melodee", "v1.6.2")             # neuer als alles: die nächstältere
    assert not exact and b.version == "v1.6.1" and "v1.6.2" in warn and "abweichen" in warn
    b, exact, warn = studio.pick_build("melodee", "v1.5.2")             # älter als alles: die älteste
    assert not exact and b.version == "v1.6.0-rc.7"
    assert studio.pick_build("melodee")[0].version == "v1.6.1"          # ohne Wunsch: die neueste
    with pytest.raises(studio.StudioError):
        studio.pick_build("sloop")
    vc = studio.check_versions("v1.6.0-rc.8", device="MELODEE v1.6.0-rc.7")
    assert not vc.ok and vc.message == "Studio läuft mit Melodee v1.6.0-rc.8, dein FM-1 hat v1.6.0-rc.7 – Klang kann abweichen."
    assert studio.check_versions("v1.6.0-rc.8", "v1.6.0-rc.8", "MELODEE v1.6.0-rc.8").ok
    assert studio.check_versions("v1.6.0", "MELODEE v1.6.0", "v1.6.0").ok
    vc = studio.check_versions("v1.6.0", device="MELODEE v1.6.0-rc.8")   # Vorabversion -> 1.6.0: Pegel anders
    assert not vc.ok and "dein FM-1 hat v1.6.0-rc.8" in vc.message and "CZ-1" in vc.message
    vc = studio.check_versions("v1.6.0-rc.8", backup="MELODEE v1.6.0")
    assert not vc.ok and "CZ-1" in vc.message


def test_wav_header():
    h = studio.wav_header(10, "s24")
    assert h[:4] == b"RIFF" and h[8:16] == b"WAVEfmt " and struct.unpack("<HHIIHH", h[20:36]) == (1, 2, 44100, 264600, 6, 24)
    h = studio.wav_header(10, "f32")
    assert struct.unpack("<HH", h[20:24]) == (3, 2) and b"fact" in h and h.endswith(struct.pack("<I", 80))


# ------------------------------------------------------------------ der Firmware-Kern (node)
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
        assert bytes(x["identity_reply"]) == _identity_frame(IDENTITY[v])   # ota.c, wie demo_device erwartet
        assert x["objects"] == 57 and all(rc == 0 for rc in x["put_rc"].values())
        assert x["identical"]["2"] and x["identical"]["64"] and x["running_music_roundtrip"] and x["persisted"]
        first = expected["tracks"][0]["banks"][0]["steps"]["0"]
        assert x["live_step0"] == {"note": first["notes"][0], "vel": first["vel"]}
        assert (x["bpm"][2] | x["bpm"][3] << 7) - 8192 == expected["bpm"]
        assert x["projects_used"] == [0, 4]
        a, b = x["takes"]
        assert a["playing"] and a["peak"] > 0.05 and a["nonFinite"] == 0 and a["hash"] == b["hash"]
        assert x["fork_identical"] and x["stopped"]
        print(f"{v}: {a['framesPerSec']:.0f} frames/s = {a['realtime']:.1f}x Echtzeit (4 Spuren)")
        assert a["realtime"] > 3


# ------------------------------------------------------------------ Fassade + Suite-Code (node-Backend)
def _song(st):
    L = st.link
    for k in range(4):
        for i in range(0, 16, 2 + k):
            L.request(Cmd.TRACK_STEP, [k, i, 1, 36 + 12 * (k % 3) + (i % 5), 0, 0, 0, 0, 0, 100])
    assert L.request(Cmd.PROJECT, [1, 0])[2] == 1
    L.request(33, [1, 2, 0, 1, 0, 1])                    # Song-Kette: 2 Zeilen à 1 Takt


@pytest.fixture
def node_studio():
    if not NODE:
        pytest.skip("node fehlt")
    made = []

    def make(version="v1.6.0-rc.8", **kw):
        st = studio.Studio.create(version=version, backend="node", **kw)
        made.append(st)
        return st
    yield make
    for st in made:
        st.close()


def test_backup_round_trip_with_suite_code(node_studio):
    a = node_studio()
    _song(a)
    for oid, fx in ((3, 2), (64, 64)):                    # Melodees eigene Projekt-Objekte (Fixture) -> Projekt 2, 5
        backup.write_object(a.link, oid, (FIX / f"obj_{fx}.bin").read_bytes(), proto=backup.FELUCCA)
    arch = a.backup()
    assert studio.backup_version(arch) == "v1.6.0-rc.8"
    sizes = {o["id"]: o["size"] for o in arch.objects if o["size"]}
    assert set(sizes) >= {0, 1, 2, 3, 64}
    text = backup.dumps(arch.file)
    b = node_studio()
    b.open_from_backup(text)                              # über backup.restore = Editor-Protokoll
    arch2 = b.backup()
    for o1, o2 in zip(arch.objects, arch2.objects):
        assert o1["id"] == o2["id"] and o1["bytes"] == o2["bytes"], o1["id"]
    assert b.projects() == [0, 1, 4]
    # Version passend wählen
    c = studio.Studio.for_backup(arch, backend="node")
    try:
        assert c.build.version == "v1.6.0-rc.8" and not c.warning and c.projects() == [0, 1, 4]
    finally:
        c.close()


def test_v160_twin_song_project_and_older_backup(node_studio):
    """1.6.0-Zwilling: nimmt das 1.6.0-Songprojekt aus Melodees eigenem Code an (16 Patterns, 64 Abschnitte,
    Punch-Effekte, Song-/Pattern-Presets), das Backup gibt es unverändert zurück; ein rc.8-Backup spielt er ein
    („Older archives restore here“), und die Suite liest das Projekt aus dem Zwilling wie vom Gerät."""
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
    """1.6.1-Zwilling (project.c project_restore_runtime ohne G_CLOCK, editor_backup.c ed_bk_clock): ein Projekt zu
    laden behält den CLK des Geräts – 1.6.0 übernahm den des Projekts; ein Backup bringt CLK zurück. Daten wie 1.6.0."""
    from fm1companion.protocol import v14
    res = {}
    for ver in ("v1.6.0", "v1.6.1"):
        st = node_studio(ver)
        assert st.build.version == ver and not st.warning
        L = st.link
        L.request(Cmd.SET, [1, 2, *v14(1)])                  # CLK USB
        assert _clk(L) == 1
        assert L.request(Cmd.PROJECT, [1, 0])[2] == 1        # Projekt 1 mit CLK USB speichern
        L.request(Cmd.SET, [1, 2, *v14(0)])                  # CLK INT
        L.request(Cmd.PROJECT, [0, 0])                       # Projekt 1 laden
        res[ver] = _clk(L)
    assert res == {"v1.6.0": 1, "v1.6.1": 0}
    new = node_studio("v1.6.1")
    song16 = (ROOT / "tests" / "fixtures" / "musik16" / "obj_0.bin").read_bytes()
    backup.write_object(new.link, 0, song16, proto=backup.FELUCCA)   # das 1.6.0-Songprojekt: Format unverändert
    new.link.request(Cmd.SET, [1, 2, *v14(1)])
    arch = new.backup()
    assert studio.backup_version(arch) == "v1.6.1" and len(arch.objects) == 57
    new.link.request(Cmd.SET, [1, 2, *v14(0)])
    new.open_from_backup(arch)
    assert _clk(new.link) == 1                               # Backup stellt CLK wieder her
    old = node_studio("v1.6.0")
    old.open_from_backup(arch)                               # 1.6.0 liest Backups von 1.6.1
    assert old.projects() == new.projects()


def test_render_stems_and_state(node_studio, tmp_path):
    st = node_studio()
    _song(st)
    r = st.render_wav(tmp_path / "song.wav", until_end=True, start="song", fmt="s24", max_seconds=60)
    assert r.ended and 6.5 < r.seconds < 7.5 and r.peak > 0.05      # 2 Takte bei 120 BPM + 2 s Ausklang
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
    assert all(pk > 0.01 for pk in peaks)                 # jede Spur spielt etwas
    # Studio-Stand
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
    st.note(60, 110)                                      # wie playback.py: Note über den Link
    r = st.render_wav(tmp_path / "note.wav", seconds=0.5, start="none", fmt="f32")
    assert r.peak > 0.05


@need_node
def test_twin_as_subprocess_device():
    """Der Zwilling als 'FM-1 an einer Leitung' (transport.SubprocessTransport, wie die Demo-Geräte)."""
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
