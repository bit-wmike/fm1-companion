# SPDX-License-Identifier: GPL-3.0-only
"""Musik-Bereich: Projektformat (gegen Bytes aus Melodees eigenem Firmware-Code), MIDI-Export,
Generatoren, Pattern schreiben/lesen/rückgängig gegen das Demo-FM-1."""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fm1companion import generators as G, midi_import as MI, midiexport as ME, projectfmt as PF  # noqa: E402
from fm1companion.demo_device import FakeFM1  # noqa: E402
from fm1companion.protocol import frame, unframe  # noqa: E402

FX = ROOT / "tests" / "fixtures" / "musik"


def obj(oid):
    return (FX / f"obj_{oid}.bin").read_bytes()


@pytest.fixture(scope="module")
def expected():
    return json.loads((FX / "expected.json").read_text())["live"]


# ------------------------------------------------------------------------------- Format ---
def test_layout_constants_match_firmware():
    lay = json.loads((FX / "layout.json").read_text())
    assert lay["BANK_STORE_SIZE"] == PF.BANK_STORE_SIZE
    assert lay["PROJ_STORE_SIZE"] == PF.PROJ_STORE_SIZE
    assert lay["BANK_ACTIVE_OFF"] == PF.BANK_ACTIVE_OFF
    assert lay["PROJ_REC_OFF"] == PF.PROJ_REC_OFF and lay["PROJ_NAME_OFF"] == PF.PROJ_NAME_OFF
    assert lay["PROJ_REC_DIR"] == PF.PROJ_REC_DIR and lay["PROJ_FM6_OFF"] == PF.PROJ_FM6_OFF
    off = lay["BANK_ACTIVE_OFF"]
    assert lay["BANKP_CHAIN_OFF"] - off == PF.FBKP["chain"]
    assert lay["BANKP_MOTION_OFF"] - off == PF.FBKP["motion"]
    assert lay["BANKP_REC_OFF"] - off == PF.FBKP["rec"] and lay["BANKP_REC_BYTES"] == PF.FBKP["recn"]
    assert lay["BANKP_TRK_OFF"] - off == PF.FBKP["trk"] and lay["BANKP_STEPS_OFF"] - off == PF.FBKP["steps"]
    assert lay["BANKL_CHAIN_OFF"] - off == PF.FBKL["chain"] and lay["BANKL_STEPS_OFF"] - off == PF.FBKL["steps"]
    assert lay["BANKL_REC_OFF"] - off == PF.FBKL["rec"] and lay["BANKL_TRK_OFF"] - off == PF.FBKL["trk"]
    assert lay["BANK_TIMING_OFF"] - off == 14340 and lay["BANK_CHAIN_OFF"] - off == 14468
    assert lay["BANKM_SONG_BYTES"] == PF.SONG_BYTES and lay["BANKN_FX_BYTES"] == PF.FX_BYTES
    assert lay["BANKO_HEAD_BYTES"] == PF.SOUND_HEAD and lay["sizeof(song_sound_t)"] == PF.SOUND_SIZE
    assert lay["P_COUNT"] == PF.P_COUNT_NOW and lay["NPAT"] == PF.NPAT and lay["NTRK"] == PF.NTRK


@pytest.mark.parametrize("oid", [0, 2, 64])
def test_decode_backup_objects(oid):
    p = PF.decode(obj(oid), "Melodee 1.6.0-rc.7")
    assert p.ok and p.hash_ok, p.warnings
    assert p.format == "HPJ1/FBKP/FUN22"
    assert p.bpm == 98 and p.swing == 12
    assert len(p.used_tracks) == 5


def test_decode_matches_firmware_ram(expected):
    p = PF.decode(obj(64))
    assert p.name == expected["name"]
    for k, et in enumerate(expected["tracks"]):
        t = p.tracks[k]
        assert t.active == et["active"]
        assert (t.level, t.pan, int(t.mute)) == (et["level"], et["pan"], et["mute"])
        for b, eb in enumerate(et["banks"]):
            pat = t.patterns[b]
            assert [pat.length, pat.div, pat.swing, pat.gate] == eb["timing"], (k, b)
            for i, s in enumerate(pat.steps):
                es = eb["steps"].get(str(i))
                if es is None:
                    assert s.empty, (k, b, i)
                    continue
                notes = es["notes"][:es["n"]] if es["time"] == 0 else []
                assert (s.notes, s.time, s.flags, s.vel, s.hit, s.acc) == \
                    (notes, es["time"], es["flags"] & 3, es["vel"], es["hit"], es["acc"]), (k, b, i)
                assert s.chance == (100 if es["prob"] == 0 else es["prob"] if es["prob"] <= 100 else 0)
    song = [(r.repeat, r.banks) for r in p.song]
    assert song == [(r["repeat"], r["banks"]) for r in expected["song"]["rows"]]
    assert [list(m) for m in p.motion] == expected["motion"]
    rec = expected["recording"][0]
    nt = p.tracks[0].patterns[0].recorded[0]
    assert (nt.step, nt.note, nt.vel) == (rec["step"], rec["note"], rec["vel"])
    assert nt.onset == pytest.approx(rec["on"] / 65536)
    assert nt.duration == pytest.approx(rec["duration"] / 65536)


def test_melodee_160_song_project():
    """Melodee v1.6.0 (tools/melodee_fixtures/fixturegen16.c, Melodees eigener Code): 16 Patterns auf Spur 1–4,
    64 Abschnitte mit Pausen und fortgesetztem Block, Punch-Effekte, Song-Block- und Pattern-Presets."""
    fx16 = ROOT / "tests" / "fixtures" / "musik16"
    exp = json.loads((fx16 / "expected.json").read_text())
    assert exp["version"] == "v1.6.0"
    e = exp["song16"]
    prj = PF.decode((fx16 / "obj_0.bin").read_bytes(), "MELODEE v1.6.0")
    assert prj.ok and prj.format == "HPJ1/FBKQ/FUN22" and not prj.warnings and prj.bpm == e["bpm"]
    for t, et in zip(prj.tracks, e["patterns"]):
        assert len(t.patterns) == et["npat"]
        assert sum(1 << b for b, p in enumerate(t.patterns) if p.used) == et["used"]
    assert [len(t.patterns) for t in prj.tracks] == [16] * 4 + [8] * 4
    assert len(prj.song) == 64 and prj.song_loop
    for r, er in zip(prj.song, e["song"]["rows"]):
        assert r.repeat == er["repeat"] and r.banks == er["banks"]
        assert r.sounds == er["sound"]
        assert [(x.fx + 1, x.start, x.length) for x in r.punches] == [tuple(f) for f in er["fx"] if f[0]]
    assert [x.text for x in prj.song[0].punches] == ["REVERSE 5–8", "STUTTER 13–14"]
    assert len(prj.song[63].punches) == 4
    assert [(s.name, s.engine, s.preset) for s in prj.sounds] == [(x["name"], x["engine"], x["preset"])
                                                                  for x in e["sounds"]]
    assert prj.pattern_sounds == {(k, b): v for k, row in enumerate(e["pattern_sound"]) for b, v in enumerate(row) if v}
    # Reihenfolge wie die Firmware: Song-Block-Preset, dann Pattern-Preset, dann der Klang der Spur
    assert prj.resolved_sound(2, 0) == (1, "block") and prj.resolved_sound(0, 0) == (0, "")
    assert prj.resolved_sound(7, 1) == (1, "pattern")           # Spur 2 spielt dort Pattern 12
    assert "6 Punch-Effekte" in prj.summary() and "1 Song-/Pattern-Presets" in prj.summary()
    j = json.loads(ME.to_json(prj))
    assert j["song"][0]["punches"][0]["name"] == "REVERSE" and j["presets"][0]["name"] == "BLOCK SOUND"
    ev = ME.events(prj)
    assert ev and {x.pattern for x in ev if x.track == 0} >= set(range(16))


def test_specific_offsets_and_content():
    p = PF.decode(obj(64))
    assert p.tracks[0].patterns[0].steps[0].notes == [36]
    assert p.tracks[0].patterns[0].steps[1].time == PF.ST_TIE
    assert p.tracks[1].patterns[0].steps[0].notes == [60, 64, 67, 71]        # 4-Noten-Akkord
    assert p.tracks[2].active == 15 and p.tracks[2].patterns[15].steps[15].hit == 128
    assert p.tracks[3].patterns[7].steps[63].notes == [72]
    assert p.tracks[4].midi_only and p.tracks[4].patterns[3].steps[2].notes == [50]
    assert p.song[2].banks[2] == PF.CHAIN_HOLD and p.song[1].banks[1] == PF.CHAIN_REST
    # Projekt 1 wurde vor der letzten Änderung gespeichert
    p1 = PF.decode(obj(2))
    assert p1.name == "FIXTURE SONG" and p1.tracks[1].patterns[0].steps[6].empty
    assert p.tracks[1].patterns[0].steps[6].notes == [77]


def test_hpj1_expand_is_record_with_valid_hash():
    rec = PF.expand_hpj1(obj(0))
    assert len(rec) == PF.BANK_STORE_SIZE and rec[:4] == b"FBKP"
    assert int.from_bytes(rec[-4:], "little") == PF.fnv(rec[:-4])


def test_melodee_legacy_fixtures():
    """Fixtures aus Melodees Repo (FBKK/FUN16 und FUN18, von unveränderter Firmware geschrieben)."""
    p = PF.decode((FX / "melodee_fbkk_fun16.bin").read_bytes())
    assert p.ok and p.format == "FBKK/FUN16" and p.hash_ok
    assert p.tracks[0].patterns[p.tracks[0].active].steps[0].notes == [60, 64, 67]   # C/E/G, vel 100
    assert p.tracks[0].patterns[p.tracks[0].active].steps[0].vel == 100
    assert any(s.notes == [72] for t in p.tracks for pat in t.patterns for s in pat.steps)
    nt = p.tracks[1].patterns[3].recorded[0]                                   # owner 11, onset 55, Dauer 217
    assert round(nt.onset * 65536) == 55 and round(nt.duration * 65536) == 217
    p18 = PF.decode((FX / "melodee_fun18.bin").read_bytes())
    assert p18.ok and p18.format == "FUN18" and p18.tracks[0].patterns[0].steps[0].notes == [60]


def test_unknown_and_empty():
    assert not PF.decode(b"").ok
    p = PF.decode(b"XXXX" + bytes(100), "SLOOP 2.8.3")
    assert not p.ok and "andere Firmware" in p.format
    assert not PF.decode(bytes(4096), "Melodee").ok


def test_fuzz_never_raises():
    rnd = random.Random(7)
    base = obj(0)
    rec = PF.expand_hpj1(base)
    for i in range(300):
        kind = i % 4
        if kind == 0:
            data = bytes(rnd.randrange(256) for _ in range(rnd.randrange(0, 600)))
        elif kind == 1:
            b = bytearray(base)
            for _ in range(20):
                b[rnd.randrange(len(b))] = rnd.randrange(256)
            data = bytes(b)
        elif kind == 2:
            b = bytearray(rec)
            for _ in range(40):
                b[rnd.randrange(len(b))] = rnd.randrange(256)
            data = bytes(b)
        else:
            data = base[:rnd.randrange(len(base))]
        p = PF.decode(data)               # darf nie werfen
        assert isinstance(p, PF.Project)
        p.summary()
        if p.ok:
            ME.events(p)                  # auch der Export nicht


def test_backup_inspect_hook():
    from fm1companion import backup_inspect
    info = backup_inspect.project_info(obj(64), "Melodee 1.6.0-rc.7")
    assert "SLOT FIVE" in info.get("summary", "")


# ------------------------------------------------------------------------------- MIDI ---
def test_midi_export_reads_back(tmp_path):
    import mido
    p = PF.decode(obj(64))
    path = ME.save_midi(p, tmp_path / "x.mid")
    mf = mido.MidiFile(str(path))
    assert mf.type == 1 and mf.ticks_per_beat == ME.TPQ
    assert any(m.type == "set_tempo" and round(mido.tempo2bpm(m.tempo)) == 98 for m in mf.tracks[0])
    chans = {}
    for tr in mf.tracks[1:]:
        t, notes = 0, []
        for m in tr:
            t += m.time
            if m.type == "note_on":
                notes.append((t, m.note, m.channel))
        chans[tr.name] = notes
    first = chans[next(n for n in chans if n.startswith("Spur 1"))]
    assert first[0] == (0, 36, 0)
    # Song: Abschnitt 1 = 2 × 16 Sechzehntel (4 Viertel), dann Abschnitt 2 mit Pattern 3 (1/32) auf Spur 1
    sec2 = [n for n in first if n[0] >= 8 * ME.TPQ and n[0] < 12 * ME.TPQ]
    assert sec2[0] == (8 * ME.TPQ, 48, 0)
    total = sum(ME.section_quarters(p, r) for r in p.song)
    assert total == pytest.approx(28.0)
    drums = chans[next(n for n in chans if n.startswith("Spur 3"))]
    assert {n for _, n, _ in drums} <= set(PF.LANE_GM) and drums[0][2] == 2
    # Spur 2 pausiert in Abschnitt 2 und 3
    second = chans[next(n for n in chans if n.startswith("Spur 2"))]
    assert all(t < 8 * ME.TPQ for t, _, _ in second)


def test_midi_drum_channel_and_lane_map(tmp_path):
    import mido
    p = PF.decode(obj(64))
    o = ME.Options(mode="pattern", drum_channel=9, lane_notes=(35, 40, 39, 42, 46, 45, 37, 81))
    mf = mido.MidiFile(str(ME.save_midi(p, tmp_path / "d.mid", o, pattern=(2, 15))))
    ons = [m for tr in mf.tracks for m in tr if m.type == "note_on"]
    assert ons and all(m.channel == 9 for m in ons)
    assert {m.note for m in ons} == {35, 40, 42, 81}
    assert any(m.velocity == 127 for m in ons)          # Akzent


def test_swing_timing():
    pat = PF.Pattern(0, length=4, div=2, swing=50)
    t = ME.step_times(pat)
    assert t[0] == (0.0, pytest.approx(0.25 + 0.05)) and t[1][1] == pytest.approx(0.2)


def test_csv_and_json():
    p = PF.decode(obj(64))
    csv = ME.to_csv(p)
    assert csv.splitlines()[0].startswith("Spur;Pattern")
    d = json.loads(ME.to_json(p))
    assert d["name"] == "SLOT FIVE" and d["tracks"][0]["patterns"]


def test_roundtrip_export_import(tmp_path):
    steps = G.random_walk(G.WalkOptions(seed=3, rest_prob=0.2, tie_prob=0.0, length=16))
    prj = PF.Project(bpm=120, ok=True, tracks=[PF.Track(0, patterns=[PF.Pattern(0, steps=steps, length=16, gate=127)])])
    path = ME.save_midi(prj, tmp_path / "r.mid", ME.Options(mode="active", loops=1))
    tracks, bpm = MI.read_midi(path)
    back = MI.to_pf_steps(tracks[0].notes, MI.ImportOptions(length=16))
    assert [s.notes for s in back[:16]] == [s.notes for s in steps[:16]]


def test_import_drums_and_split(tmp_path):
    notes = [MI.Note(i * 0.25, 0.1, 36 if i % 4 == 0 else 42, 110 if i % 8 == 0 else 80) for i in range(32)]
    notes.append(MI.Note(0, 0.1, 99, 80))       # keine Lane
    pats, rep = MI.import_patterns(notes, MI.ImportOptions(length=16, drums=True, patterns=2, accent_from=100))
    assert len(pats) == 2
    assert pats[0][0].hit == 1 and pats[0][1].hit == 1 << 3 and pats[1][0].hit == 1
    assert pats[0][0].acc == 1 and pats[0][4].acc == 0
    assert rep.no_lane == 1 and rep.used == 32
    assert "keine Lane" in rep.text()


def test_import_chord_overflow():
    notes = [MI.Note(0, 1, 60 + i, 100) for i in range(6)]
    steps = MI.to_pf_steps(notes, MI.ImportOptions(length=16), report=(rep := MI.LossReport()))
    assert len(steps[0].notes) == 4 and rep.chord_overflow == 2
    assert steps[1].time == PF.ST_TIE


# ------------------------------------------------------------------------------- Generatoren ---
def test_euclid():
    s = lambda p: "".join("x" if b else "." for b in p)  # noqa: E731
    assert s(G.euclid(8, 3)) == "x..x..x."
    assert s(G.euclid(8, 5)).count("x") == 5
    assert s(G.euclid(16, 4)) == "x...x...x...x..."
    assert s(G.euclid(8, 3, 1)) == ".x..x..x"
    assert s(G.euclid(5, 0)) == "....." and s(G.euclid(4, 9)) == "xxxx"


def test_drum_pattern_lanes():
    st = G.drum_pattern([G.LaneSpec(0, 8, 3, 0, 1), G.LaneSpec(7, 4, 1)], 8)
    assert [bool(s.hit & 1) for s in st[:8]] == G.euclid(8, 3)
    assert st[0].acc & 1 and st[0].hit & 128


def test_scales_and_chords():
    assert G.scale_notes(0, "Dur (Ionisch)", 60, 72) == [60, 62, 64, 65, 67, 69, 71, 72]
    assert G.chord_from_roman("I", 0) == [60, 64, 67]
    assert G.chord_from_roman("vi", 0) == [69, 72, 76]
    assert G.chord_from_roman("V7", 7) == [74, 78, 81, 84]        # D7 in G-Dur: D F# A C
    assert G.chord_from_roman("ii7", 0) == [62, 65, 69, 72]
    assert G.chord_from_roman("Imaj7", 0) == [60, 64, 67, 71]
    assert G.chord_from_roman("vii°", 0) == [71, 74, 77]
    assert G.chord_from_roman("bVII", 0) == [70, 74, 77]
    assert G.chord_from_roman("i", 9, "Moll (Äolisch)") == [69, 72, 76]
    with pytest.raises(ValueError):
        G.parse_roman("X")
    assert G.voice([60, 64, 67], "eng", 1) == [64, 67, 72]
    assert G.voice([60, 64, 67, 71], "drop-2") == [55, 60, 64, 71]


def test_chord_steps_max_four_and_ties():
    st, names = G.chord_steps(G.ChordOptions(progression="ii7 V7 Imaj7 I", length=16))
    assert names == ["ii7", "V7", "Imaj7", "I"]
    assert all(len(s.notes) <= 4 for s in st)
    assert st[0].time == PF.ST_NOTE and st[1].time == PF.ST_TIE


def test_seeds_deterministic():
    a = G.random_walk(G.WalkOptions(seed=42))
    b = G.random_walk(G.WalkOptions(seed=42))
    c = G.random_walk(G.WalkOptions(seed=43))
    assert [s.notes for s in a] == [s.notes for s in b] != [s.notes for s in c]
    pool = set(G.scale_notes(0, "Moll-Pentatonik", 60, 79))
    assert all(n in pool for s in a for n in s.notes)


def test_markov_and_call_response(tmp_path):
    m = G.Markov(1)
    m.train([60, 62, 64, 65, 67, 65, 64, 62] * 3, [1, 1, 2, 1, 1, 1, 2, 1] * 3)
    a, b = m.generate(16, seed=5), m.generate(16, seed=5)
    assert [s.notes for s in a] == [s.notes for s in b]
    assert {n for s in a for n in s.notes} <= {60, 62, 64, 65, 67}
    call = G.random_walk(G.WalkOptions(seed=1, length=8, rest_prob=0))
    cr = G.call_response(call, 16, 0, "Dur (Ionisch)", 1)
    assert [s.notes for s in cr[:8]] == [s.notes for s in call[:8]]
    last = max(i for i in range(16) if cr[i].notes)
    assert cr[last].notes[0] % 12 == 0


def test_humanize_and_variation():
    st = G.arp_steps([60, 64, 67], "auf", 16)
    h = G.humanize(st, 16, "Zufall ±", 10, chance=60, seed=2)
    assert all(1 <= s.vel <= 127 for s in h[:16])
    assert h[1].chance == 60 and h[0].chance == 100
    assert [s.notes for s in G.variation(st, 16, "B", seed=1)[:12]] == [s.notes for s in st[:12]]


# ------------------------------------------------------------------------------- Gerät ---
class FakeLink:
    def __init__(self):
        self.dev = FakeFM1()

    def request(self, cmd, args, timeout=1.0, retries=0):
        r = self.dev.handle(frame(cmd, args))
        if r is None:
            raise TimeoutError("keine Antwort")
        return unframe(r)[1]


def test_wire_step_roundtrip():
    s = PF.Step(notes=[60, 64], time=0, accent=True, vel=90, hit=0b10000001, acc=0b10000000, chance=33)
    assert MI.same_step(MI.unwire_step(MI.wire_step(s)), s)


def test_write_pattern_roundtrip_and_undo():
    link = FakeLink()
    before = MI.read_device_pattern(link, 1, 5)
    steps, _ = G.chord_steps(G.ChordOptions(length=16))
    steps[3] = PF.Step(time=0, vel=100, hit=1 | 128, acc=128, chance=40)
    MI.write_device_pattern(link, 1, 5, steps, 16)
    back = MI.read_device_pattern(link, 1, 5)
    assert back.length == 16 and all(MI.same_step(a, b) for a, b in zip(back.steps, steps))
    MI.restore_snapshot(link, before)
    again = MI.read_device_pattern(link, 1, 5)
    assert all(MI.same_step(a, b) for a, b in zip(again.steps, before.steps))
    assert link.dev.tracks[1]["len"] == before.length


# ------------------------------------------------------------ Generatoren: Extremwerte (Abnahme) ---
def _ok_steps(r):
    r = r[0] if isinstance(r, tuple) else r
    assert len(r) == PF.NSTEP
    for st in r:
        assert len(st.notes) <= 4 and all(0 <= n <= 127 for n in st.notes)
    return r


@pytest.mark.parametrize("n", [0, 1, 2, 3, 63, 64, 65, 1000])
@pytest.mark.parametrize("key,scale", [(0, "Dur (Ionisch)"), (11, "Dur-Pentatonik"), (11, "Blues"), (6, "Chromatisch")])
def test_generators_extremes(n, key, scale):
    """0 und 64+ Steps, 0 Schläge, Tonart H, Fünfton-Skala, leere Akkorde, Noten am Rand – nie eine Ausnahme."""
    lanes = [G.LaneSpec(lane, st, pu, ro, ac) for lane, (st, pu, ro, ac) in enumerate(
        [(0, 0, 0, 0), (1, 0, 0, 1), (64, 64, 63, 1), (16, 20, 70, 16), (1, 1, 0, 1), (3, 2, 1, 2), (64, 0, 0, 0), (16, 4, 0, 3)])]
    d = _ok_steps(G.drum_pattern(lanes, n))
    assert sum(bin(s.hit).count("1") for s in d) == (0 if n == 0 else sum(bin(s.hit).count("1") for s in d[:min(64, n)]))
    for voicing in ("eng", "weit", "drop-2", "Grundton tief"):
        for rhythm in G.RHYTHMS:
            for prog in ("I V vi IV", "i bVII bVI V7", "Imaj7 ii7 V7 I", "", "I I I I I I I I I I I I I I I I I I"):
                for spc in (0, 1, 32):
                    o = G.ChordOptions(key=key, scale=scale, progression=prog, voicing=voicing, inversion=3,
                                       rhythm=rhythm, steps_per_chord=spc, length=n, octave=6)
                    _ok_steps(G.chord_steps(o))
    chords = [G.chord_from_roman(x, key, scale, 4) for x in "I V vi IV".split()]
    for style in ("Grundton", "Oktaven", "Achtel-Puls", "Walking"):
        for spc in (0, 1, 4):
            _ok_steps(G.bass_steps(chords, spc, n, style, seed=3))
            _ok_steps(G.bass_steps([], spc, n, style, seed=3))
            _ok_steps(G.bass_steps([[]], spc, n, style, seed=3))
    for mode in G.ARP_MODES:
        for octs in (0, 1, 3):
            _ok_steps(G.arp_steps(chords[0], mode, n, octs, seed=2))
            _ok_steps(G.arp_steps([], mode, n, octs, seed=2))
            _ok_steps(G.arp_steps([120, 124, 127], mode, n, octs, seed=2))
    for lo, hi in ((60, 79), (24, 24), (108, 24), (0, 127)):
        wo = G.WalkOptions(key=key, scale=scale, low=lo, high=hi, max_leap=7, rest_prob=0.9, repeat_prob=0.9,
                           tie_prob=0.9, length=n, seed=5)
        walk = _ok_steps(G.random_walk(wo))
        _ok_steps(G.call_response(walk, n, key, scale, 7))
        _ok_steps(G.call_response(walk[:3], n, key, scale, 7))
        for curve in G.VEL_CURVES:
            _ok_steps(G.humanize(walk, n, curve, 60, 10, True, 1))
            _ok_steps(G.humanize(walk[:5], n, curve, 0, 100, False, 1))
        for kind in ("B", "C", "X"):
            _ok_steps(G.variation(walk, n, kind, key, scale, 1))
        assert G.describe(walk, n)
    m = G.Markov(2)
    m.train([60, 62, 64, 65], [1, 1, 1, 1])
    _ok_steps(m.generate(n, 1, 60))
    m1 = G.Markov(1)
    m1.train([60, 62], [0, 0])                    # Dauer 0 darf keine Endlosschleife werden
    _ok_steps(m1.generate(n, 1))


def test_roman_errors_are_german():
    with pytest.raises(ValueError) as e:
        G.chord_steps(G.ChordOptions(progression="garbage xx"))
    assert "Stufe" in str(e.value)


def test_read_midi_splits_channels_and_handles_empty(tmp_path):
    """Format-0-Datei (alles in einer Spur): Schlagzeug (Kanal 10) und Bass werden getrennte Einträge;
    16 Spuren bleiben 16; eine Datei ohne Noten ergibt keine Einträge, aber das Tempo."""
    import mido
    mf = mido.MidiFile(ticks_per_beat=480)
    tr = mido.MidiTrack()
    mf.tracks.append(tr)
    tr.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(97)))
    for i in range(8):
        tr.append(mido.Message("note_on", note=36, velocity=100, channel=9, time=0))
        tr.append(mido.Message("note_on", note=40 + i, velocity=90, channel=1, time=0))
        tr.append(mido.Message("note_off", note=36, channel=9, time=240))
        tr.append(mido.Message("note_off", note=40 + i, channel=1, time=0))
    p = tmp_path / "f0.mid"
    mf.save(str(p))
    tracks, bpm = MI.read_midi(p)
    assert round(bpm) == 97 and [t.channel for t in tracks] == [1, 9]
    assert all(len(t.notes) == 8 for t in tracks) and all(n.pitch == 36 for n in tracks[1].notes)
    assert tracks[0].name == tracks[1].name                                  # Spur-Name bleibt, Kanal zeigt die Liste
    mf = mido.MidiFile(ticks_per_beat=96)
    for k in range(16):
        t = mido.MidiTrack()
        t.name = f"Spur {k}"
        t.append(mido.Message("note_on", note=60 + k, velocity=64, channel=k, time=0))
        t.append(mido.Message("note_off", note=60 + k, channel=k, time=96))
        mf.tracks.append(t)
    p16 = tmp_path / "16.mid"
    mf.save(str(p16))
    tracks, _ = MI.read_midi(p16)
    assert len(tracks) == 16 and tracks[9].channel == 9 and tracks[9].summary.startswith("1 Noten")
    seen = []
    MI.read_midi(p16, progress=lambda a, t: seen.append((a, t)))
    assert seen[0] == (0, 16) and seen[-1] == (15, 16)
    empty = mido.MidiFile()
    t = mido.MidiTrack()
    t.append(mido.MetaMessage("set_tempo", tempo=500000))
    t.append(mido.Message("control_change", control=7, value=100, time=10))
    empty.tracks.append(t)
    pe = tmp_path / "leer.mid"
    empty.save(str(pe))
    assert MI.read_midi(pe) == ([], 120.0)
    assert MI.patterns_needed([], MI.ImportOptions()) == 1
    pats, rep = MI.import_patterns([], MI.ImportOptions())
    assert len(pats) == 1 and "Keine Noten" in rep.text()


def test_import_patterns_window_equals_full_pass():
    """Aufteilen auf viele Patterns: das Zeitfenster je Pattern liefert genau dasselbe wie der volle Durchlauf
    (auch bei weicher Rasterung, Akkord-Überlauf und unsortierten Noten) – und bleibt schnell."""
    import random
    import time
    from fm1companion import midi_import as MI
    rnd = random.Random(7)
    notes = [MI.Note(rnd.random() * 260, rnd.choice((0.1, 0.25, 1.0, 3.0)), rnd.randint(30, 90), rnd.randint(1, 127))
             for _ in range(3000)]
    for q, spb, start in ((100, 4, 0), (40, 4, 3), (0, 2, 8), (73, 4, 1)):
        o = MI.ImportOptions(steps_per_beat=spb, start_beat=start, length=32, patterns=16, quantize=q)
        pats, rep = MI.import_patterns(notes, o)
        full_rep = MI.LossReport()
        full = [MI.to_pf_steps(notes, o, c, full_rep) for c in range(16)]
        assert rep == full_rep
        for a, b in zip(pats, full):
            assert all(MI.same_step(x, y) and x.time == y.time for x, y in zip(a, b))
    many = notes * 7
    t = time.perf_counter()
    MI.import_patterns(many, MI.ImportOptions(steps_per_beat=4, length=64, patterns=16))
    assert time.perf_counter() - t < 0.5


# ================================================================== Abspielen (fm1companion/playback.py) ===
from fm1companion import playback as P  # noqa: E402


class _DemoLink:
    """Link-Ersatz um ein FakeFM1: Anfragen werden beantwortet, Noten mitgeschrieben; busy=True wie während
    einer App-Anfrage (normale Noten gehen dann nicht raus, force-Nachrichten schon)."""

    def __init__(self, dev=None):
        self.d = dev or FakeFM1()
        self.closed = False
        self.busy = False
        self.out: list = []

    def request(self, cmd, args=(), timeout=1.0, retries=0):
        r = self.d.handle(frame(cmd, list(args)))
        if r is None:
            raise RuntimeError(f"keine Antwort auf Befehl {cmd}")
        return unframe(r)[1]

    def send_midi(self, data, force=False):
        if self.busy and not force:
            return False
        self.out.append((tuple(data), force))
        self.d.handle(list(data))
        return True


def test_routing_channels():
    r = P.Routing()
    assert [r.channel(k) for k in range(5)] == [0, 1, 2, 3, None]       # CH1-4, Spur 5 = MIDI-Spur
    r.route = 3                                                          # CH9-12
    assert [r.channel(k) for k in range(4)] == [8, 9, 10, 11] and "Kanal 9–12" in r.describe()
    r.route = 1                                                          # SEL
    assert r.channel(0) is None and "gewählte Spur" in r.describe()


def test_read_routing_from_demo_device():
    link = _DemoLink()
    link.d.globals[14] = 2                                               # ROUT CH5-8
    link.d.track_sounds[2][2] = 1                                        # Spur 3 scharf
    r = P.read_routing(link)
    assert r.known and r.route == 2 and r.drum_channel == 10 and r.first == 4
    assert [t.engine for t in r.tracks] == [19, 12, 15, 10] and r.armed_tracks() == [2]
    assert "PROPHET" in r.tracks[0].label


def test_read_routing_without_answers_keeps_defaults():
    class Dead:
        def request(self, *a, **k):
            raise RuntimeError("keine Antwort")
    r = P.read_routing(Dead())
    assert not r.known and r.route == 0 and r.channel(1) == 1 and r.note


def test_plan_for_device_skips_midi_tracks_and_warns_on_sel():
    evs = [ME.NoteEvent(0, 0, 1, 60, 100, 0, 0, 0), ME.NoteEvent(4, 0, 1, 62, 100, 4, 0, 0),
           ME.NoteEvent(3, 0, 0.25, 36, 100, 3, 0, 0, True)]
    plan = P.plan_for_device(evs, P.Routing())
    assert [(e.track, e.channel) for e in plan.events] == [(0, 0), (3, 3)]
    assert any("Spur 5" in s and "MIDI-Spur" in s for s in plan.skipped)
    plan = P.plan_for_device(evs, P.Routing(route=1))
    assert any("SEL" in s for s in plan.skipped)
    # Komponieren: alles auf die Zielspur
    plan = P.plan_for_device(evs[:1], P.Routing(route=2), track_override=1)
    assert [(e.track, e.channel) for e in plan.events] == [(1, 5)]


def test_plan_drum_channel_collision():
    r = P.Routing(route=3, tracks=[P.TrackSound(0, 10, 0), P.TrackSound(1, 19, 0), P.TrackSound(2, 12, 0),
                                   P.TrackSound(3, 15, 0)])       # CH9-12, DRUM 10 = Kanal von Spur 2
    plan = P.plan_for_device([ME.NoteEvent(1, 0, 1, 60, 90, 1, 0, 0)], r)
    assert not plan.events and "DRUM-Kanal" in plan.skipped[0]


def test_sequencer_plays_and_leaves_no_hanging_notes():
    import time
    link = _DemoLink()
    evs = [ME.NoteEvent(0, i * 0.5, 0.4, 60 + i, 100, 0, 0, i) for i in range(4)]
    evs.append(ME.NoteEvent(1, 0, 8.0, 48, 90, 1, 0, 0))               # lange Note – beim Stopp noch an
    seq = P.MidiSequencer(P.link_sender(link))
    seq.start(evs, 240)
    time.sleep(0.45)
    assert seq.running and link.d.sounding
    seq.stop()
    assert not seq.running and not link.d.sounding                       # alles aus
    assert ((0xB1, 123, 0), True) in link.out and ((0xB0, 64, 0), True) in link.out


def test_sequencer_overlap_and_busy_device():
    import time
    link = _DemoLink()
    # gleiche Note überlappend: das erste Off darf die zweite Note nicht abschneiden
    evs = [ME.NoteEvent(0, 0.0, 1.0, 60, 100, 0, 0, 0), ME.NoteEvent(0, 0.5, 1.0, 60, 100, 0, 0, 1)]
    seq = P.MidiSequencer(P.link_sender(link))
    seq.start(evs, 600)                                                  # 0,1 s je Viertel
    time.sleep(0.12)                                                     # nach dem ersten Off-Zeitpunkt (0,1 s)
    assert (0, 60) in link.d.sounding
    while seq.running:
        time.sleep(0.02)
    assert not link.d.sounding
    # Gerät belegt: Note-Ons fallen weg, das Aufräumen geht trotzdem raus
    link2 = _DemoLink()
    link2.busy = True
    seq2 = P.MidiSequencer(P.link_sender(link2))
    seq2.start([ME.NoteEvent(0, 0, 0.2, 64, 100, 0, 0, 0)], 600)
    while seq2.running:
        time.sleep(0.02)
    assert seq2.dropped == 1 and all(f for _m, f in link2.out) and link2.out


def test_sequencer_loop_and_finish_callback():
    import time
    link = _DemoLink()
    seq = P.MidiSequencer(P.link_sender(link))
    done = []
    seq.on_finish = lambda: done.append(1)
    seq.start([ME.NoteEvent(0, 0, 0.5, 60, 100, 0, 0, 0)], 600, loop_quarters=1.0)
    time.sleep(0.35)
    assert seq.loops >= 2 and seq.running
    seq.stop()
    assert not done                                                      # Stopp ist kein „fertig“
    seq.start([ME.NoteEvent(0, 0, 0.2, 60, 100, 0, 0, 0)], 600)
    time.sleep(0.2)
    assert done and not seq.running


def test_compare_sounds():
    prj = PF.decode(obj(2))
    r = P.Routing(tracks=[P.TrackSound(t.index, t.engine, t.preset) for t in prj.tracks[:4]])
    assert P.compare_sounds(prj, r) == []
    r.tracks[1] = P.TrackSound(1, 12, 3)
    diff = P.compare_sounds(prj, r)
    assert len(diff) == 1 and diff[0].startswith("Spur 2") and "FM6" in diff[0]


def test_song_start_and_stop_on_demo():
    link = _DemoLink()
    rc, _rows = P.song_start(link)
    assert rc == 1                                    # kein Song im Gerät: abgelehnt
    link.d.song["rows"] = [[0, 0, 0, 0, 1]]
    assert P.song_start(link)[0] == 0 and link.d.song["running"]
    assert P.transport_stop(link) and not link.d.song["running"]


def test_render_offline_uses_fm6_patch_and_is_fast():
    import time
    import numpy as np
    from fm1companion import dx7
    prj = PF.decode(obj(2))
    t = prj.tracks[1]
    t.engine = P.ENGINE_FM6
    t.fm6 = bytes(dx7.pack(dx7.init_voice()))
    evs = [ME.NoteEvent(1, i * 0.5, 0.5, 60 + i, 100, 1, 0, i) for i in range(8)]
    evs += [ME.NoteEvent(0, i, 0.2, 36, 120, 0, 0, i, True) for i in range(4)]
    t0 = time.perf_counter()
    x = P.render_offline(evs, 120, prj)
    assert time.perf_counter() - t0 < 10
    assert x.dtype == np.float32 and len(x) > 22050 and 0.05 < float(np.max(np.abs(x))) <= 0.951
    assert "nicht exakt" in P.OFFLINE_LABEL
    assert len(P.to_int16(x)) == len(x)


def test_fm6_patch_read_from_project_bytes():
    from fm1companion import dx7
    rec = bytearray(PF.PROJ_FM6_OFF + 4 * 128)
    patch = bytes(dx7.pack(dx7.init_voice()))
    rec[PF.PROJ_FM6_OFF + 128:PF.PROJ_FM6_OFF + 256] = patch
    rec[PF.PROJ_FM6_OFF + 256 + 110] = 99                                 # unplausibel: Algorithmus > 31
    prj = PF.Project(tracks=PF._new_tracks())
    PF._read_fm6(prj, bytes(rec), PF.PROJ_FM6_OFF)
    assert prj.tracks[1].fm6 == patch and prj.tracks[0].fm6 is None and prj.tracks[2].fm6 is None


def test_edit_history_undo_redo():
    from fm1companion.ui.musik_widgets import EditHistory
    h = EditHistory(limit=3)
    a = [PF.Step([60], PF.ST_NOTE, vel=100)] + [PF.Step() for _ in range(3)]
    b = [PF.Step([62], PF.ST_NOTE, vel=100)] + [PF.Step() for _ in range(3)]
    h.push(a, 4, "eins")
    h.push(a, 4, "doppelt")                                              # gleicher Zustand: kein neuer Eintrag
    assert len(h.undo_stack) == 1
    st = h.undo(b, 4)
    assert st[0][0].notes == [60] and h.can_redo() and not h.can_undo()
    st = h.redo(a, 4)
    assert st[0][0].notes == [62]
    for i in range(5):
        h.push([PF.Step([i], PF.ST_NOTE)], 1)
    assert len(h.undo_stack) == 3 and not h.can_redo()                  # begrenzt, Wiederholen verworfen


def test_demo_device_records_notes():
    d = FakeFM1()
    d.handle([0x90, 60, 100])
    d.handle([0x91, 62, 100])
    assert d.sounding == {(0, 60), (1, 62)}
    d.handle([0x80, 60, 0])
    d.handle([0xB1, 123, 0])
    assert not d.sounding and len(d.midi_in) == 4
