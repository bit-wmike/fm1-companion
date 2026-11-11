# SPDX-License-Identifier: GPL-3.0-only
"""Tests for slicer, sample recording, clock sync, sample packs and FM1 Open (without Qt).

  python3 -m pytest tests/test_samples_plus.py -q
"""
from __future__ import annotations

import sys
import wave
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fm1companion import recorder as R, samples as S, slicer  # noqa: E402

SR = 44100


def drum_loop(times, kinds, dur, seed=1, sr=SR):
    rng = np.random.default_rng(seed)
    x = rng.normal(0, 0.001, int(dur * sr))
    for t, k in zip(times, kinds):
        i, n = int(t * sr), int(0.25 * sr)
        tt = np.arange(n) / sr
        if k == "k":
            s = 0.9 * np.sin(2 * np.pi * (50 + 80 * np.exp(-tt * 30)) * tt) * np.exp(-tt * 12)
        elif k == "s":
            s = 0.6 * rng.normal(0, 1, n) * np.exp(-tt * 25) + 0.3 * np.sin(2 * np.pi * 190 * tt) * np.exp(-tt * 20)
        else:
            s = 0.3 * np.diff(rng.normal(0, 1, n + 1)) * np.exp(-tt * 60)
        m = min(n, len(x) - i)
        x[i:i + m] += s[:m]
    return x


def save(path, x, sr=SR):
    return slicer.write_wav(path, x, sr) or path


@pytest.mark.parametrize("seed,bpm", [(1, 120), (2, 96), (3, 140)])
def test_onsets_known_positions(seed, bpm):
    step = 60 / bpm / 2                               # eighth
    times = [0.04 + i * step for i in range(8)]
    kinds = "khskhshk"
    x = drum_loop(times, kinds, times[-1] + 0.4, seed)
    found = slicer.detect_onsets(x, SR, 0.5)
    assert len(found) == len(times), found
    assert all(abs(a - b) < 0.01 for a, b in zip(found, times)), (found, times)


def test_onsets_sensitivity_and_gap():
    times = [0.1, 0.5, 0.9]
    x = drum_loop(times, "kkk", 1.3)
    x[int(0.7 * SR):int(0.7 * SR) + 2000] += 0.02 * np.sin(np.arange(2000) / 3)   # quiet tone
    assert len(slicer.detect_onsets(x, SR, 0.0)) == 3
    assert len(slicer.detect_onsets(x, SR, 0.5, min_gap=0.5)) < 3        # the minimum gap merges
    assert slicer.detect_onsets(np.zeros(1000), SR) == []


def test_equal_grid_and_ranges():
    assert slicer.equal_points(2.0, 4) == [0.0, 0.5, 1.0, 1.5]
    g = slicer.grid_points(2.0, 120, 8)
    assert g == [i * 0.25 for i in range(8)]
    assert slicer.grid_points(2.0, 120, 4, offset=0.1)[:2] == [0.1, 0.6]
    assert slicer.guess_bpm(2.0) == 120.0 and slicer.guess_bpm(4.0) == 120.0
    assert slicer.ranges([0.5, 0, 1.0], 1.5) == [(0, 0.5), (0.5, 1.0), (1.0, 1.5)]
    x = np.sin(np.arange(4410) / 10)
    t = slicer.snap_zero(x, SR, 0.0505)
    i = int(round(t * SR))
    assert abs(x[i]) < 0.1 and abs(t - 0.0505) < 0.003


def test_kit_from_slices_and_export(tmp_path):
    times = [0.0, 0.3, 0.6, 0.9, 1.2]
    p = tmp_path / "loop.wav"
    save(p, drum_loop(times, "khskh", 1.5))
    rs = slicer.ranges(slicer.detect_onsets(drum_loop(times, "khskh", 1.5), SR) or times, 1.5)
    kit = slicer.kit_from_slices(str(p), rs, "my loop", base_note=36)
    assert kit.name == "MY LOOP" and [z.root for z in kit.zones] == list(range(36, 36 + len(rs)))
    total, per = S.kit_budget(kit)
    assert total <= S.SLOT_BYTES and len(per) == len(rs)
    hdr, data = S.build_slot(kit)
    assert hdr[:4] == b"FSMP" and hdr[6] == len(rs)
    files = slicer.export_slices(drum_loop(times, "khskh", 1.5), SR, rs, tmp_path / "out", "loop")
    assert len(files) == len(rs) and files[0].name == "loop 01.wav"
    with wave.open(str(files[1])) as w:
        assert abs(w.getnframes() - int((rs[1][1] - rs[1][0]) * SR)) <= 1


def test_kit_from_many_long_slices_fits(tmp_path):
    p = tmp_path / "long.wav"
    save(p, drum_loop([i * 0.5 for i in range(20)], "k" * 20, 10.5))
    kit = slicer.kit_from_slices(str(p), slicer.ranges(slicer.equal_points(10.5, 20), 10.5))
    assert len(kit.zones) == 16                                  # at most 16 zones
    assert S.kit_budget(kit)[0] <= S.SLOT_BYTES


# ------------------------------------------------------------- recording

def test_take_processing():
    sr = 1000
    x = np.zeros((3000, 2))
    x[1000:1500] = 0.25
    y = R.process_take(x, sr, trim=True, norm=True)
    assert 500 <= len(y) <= 560 and abs(np.abs(y).max() - 0.97) < 1e-6
    assert len(R.trim_silence(np.zeros(100), sr)) == 0


def test_wav_roundtrip_split_cut_export(tmp_path):
    sr = 8000
    a = np.stack([np.full(800, 0.1 * c) for c in range(4)], axis=1)
    p = R.write_wav_array(tmp_path / "multi.wav", a, sr)
    mono = R.split_tracks(p, "mono")
    assert len(mono) == 4 and abs(R.read_wav_array(mono[3])[1][0, 0] - 0.3) < 1e-3
    pairs = R.split_tracks(p, "pairs")
    assert len(pairs) == 2
    c = R.cut_wav(mono[1], tmp_path / "cut.wav", 0.01, 0.05, norm=True, fade_out_ms=5)
    sr2, b = R.read_wav_array(c)
    assert len(b) == 320 and abs(b[:200].max() - 0.97) < 1e-3 and abs(b[-1, 0]) < 0.01
    assert R.export_audio(c, "wav", tmp_path / "x.wav").exists()


def test_tail_reader_growing_file(tmp_path):
    p = R.write_wav_array(tmp_path / "g.wav", np.zeros((10, 2)), 8000)
    tr = R.TailReader(p, 2)
    assert len(tr.read()) == 10
    with open(p, "ab") as f:
        f.write(np.full(8, 16384, "<i2").tobytes())
    blk = tr.read()
    assert blk.shape == (4, 2) and abs(blk[0, 0] - 0.5) < 1e-6


# -------------------------------------------------------------- clock sync

def clock_stream(bpm, bars, t0=10.0, start_after_ticks=0, beats=4):
    """(status, t) sequence: a few ticks before start, then start and clock."""
    dt = 60 / bpm / 24
    ev, t = [], t0
    for _ in range(start_after_ticks):
        ev.append((0xF8, t))
        t += dt
    ev.append((0xFA, t))
    t += 0.001
    for _ in range(bars * beats * 24 + 30):
        ev.append((0xF8, t))
        t += dt
    ev.append((0xFC, t))
    return ev


def test_barsync_start_and_n_bars():
    b = R.BarSync()
    b.arm(bars=2)
    ev = clock_stream(120, 3, start_after_ticks=10)
    first_tick = next(t for s, t in ev if s == 0xF8 and t > next(t2 for s2, t2 in ev if s2 == 0xFA))
    for s, t in ev:
        b.feed(s, t)
    assert b.start_time == first_tick
    assert abs((b.end_time - b.start_time) - 2 * 4 * 0.5) < 1e-9       # 2 bars at 120 BPM = 4 s
    assert abs(b.bpm - 120) < 0.01 and b.state == "fertig" and not b.stopped_early


def test_barsync_next_bar_while_running_and_stop():
    b = R.BarSync()
    ev = clock_stream(100, 4)
    for s, t in ev[:150]:                                   # already running (bar 2)
        b.feed(s, t)
    assert b.position[0] == 2
    b.arm(bars=0, mode="bar")                                # until stop
    for s, t in ev[150:]:
        b.feed(s, t)
    assert b.start_tick == 192                               # start of bar 3
    assert b.end_time == ev[-1][1]                           # ends at stop
    b2 = R.BarSync()
    b2.arm(bars=8)
    for s, t in ev:
        b2.feed(s, t)
    assert b2.stopped_early and b2.end_time == ev[-1][1]


def test_take_window_and_start_estimator():
    est = R.StartEstimator(1000)
    for t, frames in ((1.10, 80), (1.20, 195), (1.30, 270)):
        est.observe(t, frames)
    assert abs(est.t0 - 1.005) < 1e-9
    assert R.take_window(2.0, 4.0, 1.0, 1000, latency_ms=10) == (1010, 3010)


# --------------------------------------------------------- sample packs

from fm1companion import samplepacks as P  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "samplepacks"


def test_pack_tree_fixtures():
    ents = P.parse_tree((FIX / "dirt.tree").read_text())
    assert ents and all(Path(e.path).suffix.lower() in P.AUDIO for e in ents)
    fol = P.folders(ents)
    assert "808" in fol and fol["808"][0] >= 1
    v = P.parse_tree((FIX / "vcsl.tree").read_text())
    assert any(e.path.startswith("Membranophones/") for e in v)
    # with sizes (ls-tree -l) and special characters
    t = "100644 blob abc  1234\tA/b.wav\n100644 blob abd\t\"A/\\303\\244.wav\"\n040000 tree x\tA\n100644 blob x\tREADME.md"
    e = P.parse_tree(t)
    assert [x.path for x in e] == ["A/b.wav", "A/ä.wav"] and e[0].size == 1234


def test_licence_rules():
    for p in P.PACKS:
        txt = P.licence_text(p)
        if p.licence in P.FREE:
            assert p.free and "unklar" not in txt
        else:
            assert not p.free and txt.startswith("Lizenz unklar")
        assert p.licence_note
    assert not P.pack("dirt").free                       # no LICENSE in the repository
    assert P.pack("vcsl").free and P.pack("vcsl").licence == "CC0-1.0"
    assert "unklar" in P.note_for(P.pack("dirt"))


def test_pack_download_from_local_repo(tmp_path, monkeypatch):
    import subprocess
    src = tmp_path / "base" / "vcsl"
    (src / "Drums" / "Kick").mkdir(parents=True)
    (src / "Bells").mkdir(parents=True)
    save(src / "Drums" / "Kick" / "k1.wav", np.zeros(100))
    save(src / "Bells" / "b1.wav", np.zeros(100))
    (src / "README.md").write_text("x")
    g = ["git", "-c", "user.email=a@b", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q", str(src)], check=True)
    subprocess.run([*g, "-C", str(src), "add", "-A"], check=True)
    subprocess.run([*g, "-C", str(src), "commit", "-qm", "x"], check=True)
    subprocess.run(["git", "-C", str(src), "config", "uploadpack.allowfilter", "true"], check=True)
    monkeypatch.setenv("FM1C_PACKS_GITBASE", "file://" + str(tmp_path / "base"))
    monkeypatch.delenv("FM1C_PACKS_FIXTURES", raising=False)
    monkeypatch.setattr(P, "CACHE_DIR", tmp_path / "cache")
    p = P.pack("vcsl")
    ents = P.index(p)
    assert sorted(e.path for e in ents) == ["Bells/b1.wav", "Drums/Kick/k1.wav"]
    out = P.download(p, tmp_path / "dest", ["Drums/Kick"])
    assert [f.name for f in out] == ["k1.wav"] and out[0].exists()
    # the git copy is gone right after copying (no second copy on the disk), the contents list stays
    assert not (tmp_path / "cache" / "samplepacks" / "vcsl" / "repo").exists()
    assert (tmp_path / "cache" / "samplepacks" / "vcsl" / "index.json").exists()
    note = (tmp_path / "dest" / "VCSL" / "QUELLE.txt").read_text()
    assert "CC0" in note and p.url in note
    assert len(P.download(p, tmp_path / "dest", None)) == 2       # downloading again works (cloned again)
    assert not (tmp_path / "cache" / "samplepacks" / "vcsl" / "repo").exists()


# --------------------------------------------------------------- FM1 Open

import shutil as _sh  # noqa: E402
import struct as _st  # noqa: E402
import subprocess as _sp  # noqa: E402

from fm1companion import fm1open as F  # noqa: E402
from fm1companion.transport import Link  # noqa: E402
from fm1open_sim import FM1OpenDevice, demo_bank  # noqa: E402
from test_core import LocalTransport  # noqa: E402

FM1_OPEN = Path("/tmp/fm1_open")


@pytest.fixture(scope="module")
def sea_ref(tmp_path_factory):
    """reference encoder/decoder from the original C sources of FM1 Open (clang/gcc)."""
    cc = _sh.which("clang") or _sh.which("gcc")
    if not cc or not (FM1_OPEN / "web/synth/sea_encode.h").exists():
        pytest.skip("compiler or /tmp/fm1_open missing")
    d = tmp_path_factory.mktemp("sea")
    for f in ("web/synth/sea_encode.h", "web/synth/sample_metadata.inc", "web/synth/sea_decode.c",
              "sdk/include/fm1_sea.h"):
        _sh.copy(FM1_OPEN / f, d)
    (d / "h.c").write_text(r'''
#include <stdio.h>
#include <stdint.h>
#define FM1_DSP_DATA
enum { SAMPLE_DRUM_PADS = 8 };
#include "fm1_sea.h"
#include "sample_metadata.inc"
#include "sea_encode.h"
static uint8_t buf[1<<24];
int main(int c, char **v) {
  size_t n = fread(buf, 1, sizeof buf, stdin);
  if (v[1][0] == 'e') { unsigned f = n / 2; uint8_t *o = fm1_sea_encode((int16_t *)buf, f, sample_dqt);
    fwrite(o, 1, ((f + 319) / 320) * 228, stdout); }
  else for (size_t k = 0; k + 228 <= n; k += 228) { int16_t p[320];
    if (fm1_sea_decode(buf + k, 228, 5, sample_dqt, p)) return 2; fwrite(p, 2, 320, stdout); }
  return 0; }''')
    _sp.run([cc, "-O2", "-o", str(d / "sea"), str(d / "h.c"), str(d / "sea_decode.c")], check=True)
    return d / "sea"


def _signals():
    rng = np.random.default_rng(7)
    t = np.arange(9000)
    return {"sinus": (20000 * np.sin(t / 7)).astype(int).tolist(),
            "rauschen": rng.integers(-32768, 32768, 3000).tolist(),
            "clipping": [32767 if (i // 50) % 2 else -32768 for i in range(2000)],
            "stille": [0] * 650, "kurz": [1000, -2000, 3],
            "impuls": [0] * 100 + [30000] + [0] * 900,
            "drum": (np.clip(drum_loop([0.0, 0.1], "ks", 0.25, 3, 32000), -1, 1) * 32767).astype(int).tolist()}


@pytest.mark.parametrize("name", list(_signals()))
def test_sea5_encoder_byte_exact(sea_ref, name):
    pcm = _signals()[name]
    raw = _st.pack(f"<{len(pcm)}h", *pcm)
    ref = _sp.run([str(sea_ref), "e"], input=raw, capture_output=True, check=True).stdout
    mine = F.sea_encode(pcm)
    assert mine == ref
    dec = _sp.run([str(sea_ref), "d"], input=ref, capture_output=True, check=True).stdout
    assert F.sea_decode(mine) == list(_st.unpack(f"<{len(dec) // 2}h", dec))


def test_framing_roundtrip():
    for payload in (b"", b"\x00", bytes(range(132)), b"\xff" * 64):
        m = F.frame(13, 9999, payload)
        assert m[0] == 0xF0 and m[-1] == 0xF7 and all(b < 128 for b in m[1:-1]) and sum(m[1:-1]) % 128 == 0
        assert F.parse(m) == (13, 9999, payload)
    bad = F.frame(11, 5)
    bad[-2] ^= 1
    assert F.parse(bad) is None
    with pytest.raises(ValueError):
        F.frame(13, 1, bytes(133))


def test_bank_build_read_and_limits():
    ents = F.empty_bank()
    ents[2] = F.make_entry([1000] * 700, "Snare ä 1234567890")
    bank = F.build_bank(ents)
    back = F.read_bank(bank)
    assert back[2].name == "Snare _ 1234567" and back[2].frames == 700 and len(back[2].data) == 3 * 228
    assert len(bank) == 512 + 3 * 228 and F.bank_crc(bank) == _st.unpack_from("<I", bank, 12)[0]
    broken = bytearray(bank)
    broken[600] ^= 1
    with pytest.raises(F.FM1OpenError):
        F.read_bank(bytes(broken))
    big = F.SampleEntry("X", 640000, bytes(F.chunk_bytes(640000)))
    with pytest.raises(F.FM1OpenError):
        F.build_bank([big] + F.empty_bank()[1:])
    assert abs(F.capacity_seconds() - 19.55) < 0.01
    assert F.free_frames(ents, 2) == F.free_frames(F.empty_bank(), 0)


def make_open(**kw):
    dev = FM1OpenDevice(demo_bank(), **kw)
    return dev, Link(LocalTransport(dev))


def test_protocol_roundtrip_against_simulator():
    dev, link = make_open()
    with F.Client(link, timeout=0.5) as c:
        info = c.probe()
        assert info["capacity"] == F.CAPACITY and info["version"] == 4
        assert c.sample_status()[0] == 2
        bank = c.read_bank()
        ents = F.read_bank(bank)
        assert [e.name for e in ents][:2] == ["BWOOM", "PAH"]
        ents[1] = F.make_entry((np.sin(np.arange(5000) / 5) * 9000).astype(int).tolist(), "MYSNARE")
        new = F.build_bank(ents)
        c.write_bank(new)
        assert bytes(dev.flash[:len(new)]) == new and dev.state == 2
        assert F.read_bank(c.read_bank())[1].name == "MYSNARE"


def test_protocol_retry_uses_cached_reply():
    dev, link = make_open(drop_replies=1)          # the first reply gets lost
    with F.Client(link, timeout=0.3) as c:
        assert c.sample_status()[0] == 2


def test_write_rejected_bank_and_errors():
    dev, link = make_open()
    with F.Client(link, timeout=0.3) as c:
        with pytest.raises(F.FM1OpenError):
            c.write_bank(b"\0" * 600)                  # rejected even before erasing
        assert dev.state == 2 and 12 not in dev.log
        with pytest.raises(F.FM1OpenError) as ex:
            c.query(12, _st.pack("<I", 100))          # too small -> invalid
        assert ex.value.code == 71


# ================================================================== preview: players and outputs ===
PW_DUMP = """[
 {"id": 0, "type": "PipeWire:Interface:Metadata", "props": {"metadata.name": "default"},
  "metadata": [{"subject": 0, "key": "default.audio.sink", "type": "Spa:String:JSON",
                "value": {"name": "alsa_output.usb-Melodee_FM-1-00.analog-stereo"}}]},
 {"id": 40, "type": "PipeWire:Interface:Node", "info": {"props": {"media.class": "Audio/Sink",
  "node.name": "alsa_output.platform-107c701400.hdmi.hdmi-stereo", "node.description": "Eingebaute Audio Digital Stereo (HDMI)"}}},
 {"id": 41, "type": "PipeWire:Interface:Node", "info": {"props": {"media.class": "Audio/Sink",
  "node.name": "alsa_output.usb-Melodee_FM-1-00.analog-stereo", "node.description": "Melodee FM-1 Analog Stereo"}}},
 {"id": 42, "type": "PipeWire:Interface:Node", "info": {"props": {"media.class": "Audio/Source",
  "node.name": "alsa_input.usb-Melodee_FM-1-00.analog-stereo"}}}
]"""


def test_parse_pw_dump_finds_default_fm1_sink():
    from fm1companion import audioout as A
    outs = A.parse_pw_dump(PW_DUMP)
    assert [o.description for o in outs] == ["Eingebaute Audio Digital Stereo (HDMI)", "Melodee FM-1 Analog Stereo"]
    d = next(o for o in outs if o.default)
    assert d.is_fm1 and not outs[0].is_fm1
    assert "FM-1 selbst" in A.output_hint(d) and A.output_hint(outs[0]) == ""
    assert A.parse_pw_dump("kaputt") == []


def test_parse_pactl():
    from fm1companion import audioout as A
    outs = A.parse_pactl("51\talsa_output.hdmi\tPipeWire\ts32le 2ch 48000Hz\tSUSPENDED\n", "alsa_output.hdmi\n")
    assert len(outs) == 1 and outs[0].default


def test_wav_from_floats_and_ints(tmp_path):
    """before: float samples made struct.pack fail – now they are scaled."""
    from fm1companion.ui import audio as AU
    p = AU.write_wav(tmp_path / "f.wav", np.array([0.0, 0.5, -1.0, 2.0], dtype=np.float32), 22050)
    with wave.open(str(p)) as w:
        fr = np.frombuffer(w.readframes(4), "<i2")
    assert list(fr) == [0, 16384, -32767, 32767] or list(fr) == [0, 16383, -32767, 32767]
    p = AU.write_wav(tmp_path / "i.wav", [0, 1000, -1000], 8000)
    with wave.open(str(p)) as w:
        assert list(np.frombuffer(w.readframes(3), "<i2")) == [0, 1000, -1000]


def _fake_bin(d: Path, name: str, body: str):
    f = d / name
    f.write_text("#!/bin/sh\n" + body + "\n")
    f.chmod(0o755)


def test_player_falls_back_to_next_program(tmp_path, monkeypatch):
    from fm1companion.ui import audio as AU
    log = tmp_path / "log"
    _fake_bin(tmp_path, "pw-play", 'echo "pw-play: kann PipeWire nicht erreichen" >&2; exit 1')
    _fake_bin(tmp_path, "paplay", f'echo "$@" > {log}; sleep 2')
    monkeypatch.setenv("PATH", f"{tmp_path}:/usr/bin:/bin")
    monkeypatch.setattr(AU, "available_backends", lambda: ["pw-play", "paplay"])
    monkeypatch.setattr(AU, "chosen_output", lambda: "alsa_output.hdmi")
    monkeypatch.setattr(AU.Player, "START_CHECK", 0.5)
    p = AU.Player()
    assert p.play_samples([0.0, 0.3, -0.3] * 2000, 22050)
    assert p.backend == "paplay" and "PipeWire" in p.errors[0]
    import time
    for _ in range(50):
        if log.exists():
            break
        time.sleep(0.02)
    assert "--device=alsa_output.hdmi" in log.read_text()
    p.stop()
    assert not p.playing


def test_player_reports_clearly_when_nothing_works(tmp_path, monkeypatch):
    from fm1companion.ui import audio as AU
    _fake_bin(tmp_path, "aplay", 'echo "aplay: main:831: audio open error: No such file or directory" >&2; exit 1')
    monkeypatch.setenv("PATH", f"{tmp_path}:/usr/bin:/bin")
    monkeypatch.setattr(AU, "available_backends", lambda: ["aplay"])
    monkeypatch.setattr(AU, "_have_mm", lambda: False)
    monkeypatch.setattr(AU, "chosen_output", lambda: "")
    monkeypatch.setattr(AU.Player, "START_CHECK", 0.5)
    p = AU.Player()
    with pytest.raises(AU.PlayerError) as e:
        p.play_samples_strict([0, 100, -100], 8000)
    msg = str(e.value)
    assert "Kein Ton möglich" in msg and "audio open error" in msg and "pipewire-bin" in msg
    monkeypatch.setattr(AU, "available_backends", lambda: [])
    with pytest.raises(AU.PlayerError) as e:
        AU.Player().play_samples_strict([0], 8000)
    assert "Kein Abspielprogramm" in str(e.value)


def test_syscheck_playback_finding():
    from fm1companion import audioout as A, syscheck
    assert syscheck.playback_finding([], None).level == "warn"
    fm1 = A.Output("alsa_output.usb-Melodee_FM-1", "Melodee FM-1 Analog Stereo", True)
    f = syscheck.playback_finding(["pw-play"], fm1)
    assert f.level == "warn" and "FM-1 selbst" in f.detail
    assert syscheck.playback_finding(["pw-play"], A.Output("hdmi", "HDMI", True)).level == "ok"


# ---- never unnoticed to the FM-1 (Pi 5: Studio sound went to the FM-1, whose audio start crashed the USB controller)
def test_effective_output_avoids_fm1_and_guard_resets_default():
    from fm1companion import audioout as A
    fm1 = A.Output("alsa_output.usb-Generic_Melodee_FM-1-00.analog-stereo", "Melodee FM-1 Analog Stereo", True, 51)
    hdmi = A.Output("alsa_output.platform-107c701400.hdmi.hdmi-stereo", "Built-in Audio Digital Stereo (HDMI)", False, 48)
    dummy = A.Output("auto_null", "Schein-Ausgabe", False, 30)
    outs = [fm1, dummy, hdmi]
    assert A.best_other(outs) is hdmi
    assert A.effective_output("", outs) == hdmi.name                 # the default is the FM-1 → HDMI
    assert A.effective_output(fm1.name, outs) == fm1.name            # chosen explicitly: stays
    fm1.default, hdmi.default = False, True
    assert A.effective_output("", outs) == ""                        # the default is HDMI → use the default
    fm1.default, hdmi.default = True, False
    calls = []
    r = A.keep_default_off_fm1(outs, setter=lambda o: calls.append(o.name) or True)
    assert r == (fm1, hdmi) and calls == [hdmi.name]
    fm1.default, hdmi.default = False, True
    assert A.keep_default_off_fm1(outs, setter=lambda o: 1 / 0) is None   # nothing to do: no call
    only = [A.Output(fm1.name, fm1.description, True)]
    assert A.best_other(only) is None and A.keep_default_off_fm1(only, setter=lambda o: True) is None
