# SPDX-License-Identifier: GPL-3.0-only
"""Tests der Funktionen hinter den Seiten – gegen das simulierte FM-1, ohne Qt.

  python3 -m pytest tests/test_features.py -q
"""
from __future__ import annotations

import math
import os
import struct
import sys
import wave
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fm1companion import backup, midi_import as MI, projects, recorder, samples as S  # noqa: E402
from fm1companion.demo_device import FakeFM1  # noqa: E402
from fm1companion.transport import Link  # noqa: E402
from test_core import LocalTransport  # noqa: E402


def make(samples=0):
    dev = FakeFM1(samples)
    return dev, Link(LocalTransport(dev))


def write_wav(path: Path, f0=220.0, secs=0.5, ch=1, rate=44100, amp=0.5):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(ch)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = []
        for t in range(int(secs * rate)):
            v = int(amp * 32767 * math.sin(2 * math.pi * f0 * t / rate))
            frames.append(struct.pack("<" + "h" * ch, *([v] * ch)))
        w.writeframes(b"".join(frames))
    return path


# ------------------------------------------------------------- Projekte

def test_project_export_import(tmp_path):
    dev, link = make()
    slots, live = projects.list_slots(link)
    assert len(slots) == 32 and live == len(dev.objs[0]) > 0
    assert [s.slot for s in slots if s.used] == [1, 7]
    p = projects.export_slot(link, 7, tmp_path, "Mein Song", "Melodee 1.6.0-rc.7", "FM-1 A", "Strophe")
    pf = projects.load_project(p)
    assert pf.name == "Mein Song" and pf.data == dev.objs[66]
    # in einen freien Platz spielen
    projects.import_to_slot(link, pf, 20)
    assert dev.objs[backup.project_id(20)] == pf.data
    # leeren
    projects.erase_slot(link, 20)
    assert dev.objs[backup.project_id(20)] == b""
    # Bibliothek und Namenskonflikt
    p2 = projects.save_project(tmp_path, "Mein Song", pf.data, "x")
    assert p2.name.startswith("Mein Song (2)")
    assert len(projects.library(tmp_path)) == 2


def test_project_rejected_by_device(tmp_path):
    dev, link = make()
    p = projects.export_slot(link, 1, tmp_path, "A", "fw")
    pf = projects.load_project(p)
    orig = dev._handle

    def refuse(cmd, a):
        if cmd == 67 and a[0] == 0:
            return [0, a[1], 1]
        return orig(cmd, a)
    dev._handle = refuse
    with pytest.raises(backup.BackupError):
        projects.import_to_slot(link, pf, 3)


def test_projects_from_backup():
    _, link = make()
    arch = backup.read(backup.capture(link, "fw"))
    got = projects.projects_from_backup(arch)
    assert [s for s, _ in got] == [1, 7]


# --------------------------------------------------------------- Samples

def test_sample_library_and_kit(tmp_path):
    root = tmp_path / "po-samples" / "PO-33"
    root.mkdir(parents=True)
    write_wav(root / "kick 01.wav", 55, 0.4)
    write_wav(root / "snare.wav", 200, 0.3, ch=2)
    write_wav(root / "vox C4.wav", 261.6, 1.0, rate=48000)
    (root / "readme.txt").write_text("x")
    lib = S.Library(tmp_path / "db.sqlite")
    st = lib.scan([tmp_path / "po-samples"])
    assert st["neu"] == 3 and st["gesamt"] == 3
    st2 = lib.scan([tmp_path / "po-samples"])
    assert st2["neu"] == 0 and st2["geändert"] == 0
    rows = lib.query("kick")
    assert len(rows) == 1 and "kick" in rows[0]["tags"] and "pocket operator" in rows[0]["tags"]
    assert abs(rows[0]["duration"] - 0.4) < 0.01
    lib.set(rows[0]["path"], favorite=1)
    assert len(lib.query(favorites=True)) == 1
    assert lib.query(max_seconds=0.5) and all(r["duration"] <= 0.5 for r in lib.query(max_seconds=0.5))
    (root / "snare.wav").unlink()
    assert lib.scan([tmp_path / "po-samples"])["entfernt"] == 1

    kit = S.Kit(name="PO KIT", mode="drum", base_note=36)
    for r in lib.query():
        kit.zones.append(S.Zone(r["path"]))
    kit.assign_keys()
    assert [z.root for z in kit.zones] == [36, 37]
    total, per = S.kit_budget(kit)
    assert total < S.SLOT_BYTES and len(per) == 2
    hdr, data = S.build_slot(kit)
    assert hdr[:4] == b"FSMP" and hdr[6] == 2 and len(hdr) == 480
    path = kit.save(tmp_path)
    k2 = S.Kit.load(path)
    assert k2.name == "PO KIT" and len(k2.zones) == 2 and k2.zones[1].root == 37
    # Root-Erkennung aus Dateinamen
    sr, x = S.read_mono(root / "vox C4.wav")
    assert sr == 48000 and S.detect_root(sr, x, "vox C4") == 60


def test_kit_too_long_and_fit(tmp_path):
    p = write_wav(tmp_path / "long.wav", 110, 12.0)
    kit = S.Kit(zones=[S.Zone(str(p))])
    total, _ = S.kit_budget(kit)
    assert total > S.SLOT_BYTES
    with pytest.raises(ValueError):
        S.build_slot(kit)
    S.fit_to_slot(kit)
    total, _ = S.kit_budget(kit)
    assert total <= S.SLOT_BYTES
    S.build_slot(kit)


def test_upload_to_device(tmp_path):
    dev, link = make(samples=4)
    kib, slots = S.slot_info(link)
    assert kib == 80 and len(slots) == 4 and all(s.empty for s in slots)
    kit = S.Kit(name="TEST", zones=[S.Zone(str(write_wav(tmp_path / f"{n}.wav", 100 + n * 50, 0.3))) for n in range(5)])
    kit.assign_keys()
    hdr, data = S.build_slot(kit)
    S.upload(link, 2, hdr, data)
    kib, slots = S.slot_info(link)
    assert slots[2].name == "TEST" and slots[2].zones == 5
    assert dev.smp[2][1] == data
    S.erase(link, 2)
    assert S.slot_info(link)[1][2].empty


def test_melodee_has_no_slots():
    _, link = make(samples=0)
    kib, slots = S.slot_info(link)
    assert slots == []


def test_edit_reverse_and_gain():
    x = [i / 100 for i in range(100)]
    y = S.apply_edit(100, x, S.Edit(trim_silence=False, normalize=False, reverse=True, fade_out_ms=0))
    assert y[0] == pytest.approx(0.99) and y[-1] == 0
    y2 = S.apply_edit(100, x, S.Edit(trim_silence=False, normalize=True, fade_out_ms=0))
    assert max(map(abs, y2)) == pytest.approx(0.97)


def test_non_wav_with_ffmpeg(tmp_path):
    import shutil
    import subprocess
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg fehlt")
    w = write_wav(tmp_path / "a.wav", 300, 0.5)
    mp3 = tmp_path / "a.mp3"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(w), str(mp3)], check=True)
    sr, x = S.read_mono(mp3)
    assert sr and abs(len(x) / sr - 0.5) < 0.1
    assert S.probe(mp3).duration > 0.4


# ---------------------------------------------------------- MIDI-Import

def _midi(path: Path):
    import mido
    mf = mido.MidiFile(ticks_per_beat=480)
    tr = mido.MidiTrack()
    mf.tracks.append(tr)
    tr.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(100)))
    tr.append(mido.MetaMessage("track_name", name="Bass"))
    # C3 eine Viertel, dann Akkord C-E-G als Achtel, dann Pause, dann D3 lang
    seq = [(0, [48], 480, 100), (480, [60, 64, 67], 240, 120), (1440, [50], 960, 90)]
    events = []
    for start, notes, length, vel in seq:
        for n in notes:
            events.append((start, mido.Message("note_on", note=n, velocity=vel)))
            events.append((start + length, mido.Message("note_off", note=n, velocity=0)))
    events.sort(key=lambda e: (e[0], e[1].type == "note_on"))
    t = 0
    for at, msg in events:
        msg.time = at - t
        t = at
        tr.append(msg)
    mf.save(str(path))
    return path


def test_midi_to_steps_and_write(tmp_path):
    tracks, bpm = MI.read_midi(_midi(tmp_path / "a.mid"))
    assert round(bpm) == 100 and len(tracks) == 1 and len(tracks[0].notes) == 5
    steps = MI.to_steps(tracks[0].notes, MI.Options(length=16))
    assert steps[0].notes == [48] and steps[0].time == 0
    assert [steps[i].time for i in (1, 2, 3)] == [1, 1, 1]          # C3 eine Viertel = 4 Sechzehntel
    assert sorted(steps[4].notes) == [60, 64, 67] and steps[4].flags & 1  # Akkord mit Akzent
    assert steps[5].time == 1 and steps[6].time == 2                   # Achtel = 2 Steps, dann Pause
    assert steps[12].notes == [50] and all(steps[i].time == 1 for i in (13, 14, 15))
    dev, link = make()
    note = MI.write_pattern(link, 1, 3, steps, 16)
    t = dev.tracks[1]
    assert t["pattern"] == 3 and t["len"] == 16
    assert t["steps"][4][:5] == [3, 60, 64, 67, 0]
    assert note == ""


# ------------------------------------------------------------ Aufnahme

def test_split_pairs(tmp_path):
    p = tmp_path / "rec.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(8)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(b"".join(struct.pack("<8h", *[c * 1000 for c in range(8)]) for _ in range(100)))
    out = recorder.split_pairs(p)
    assert len(out) == 4
    with wave.open(str(out[2])) as w:
        assert w.getnchannels() == 2
        assert struct.unpack("<2h", w.readframes(1)) == (4000, 5000)


def test_fix_header(tmp_path):
    p = tmp_path / "cut.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(b"\0" * 4000)
    raw = bytearray(p.read_bytes())
    raw[4:8] = b"\xff\xff\xff\xff"
    raw[40:44] = b"\xff\xff\xff\xff"
    p.write_bytes(bytes(raw))
    recorder._fix_header(p)
    with wave.open(str(p)) as w:
        assert w.getnframes() == 1000
