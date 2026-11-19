# SPDX-License-Identifier: GPL-3.0-only
"""SLOOP 3 projects (sloop3proj.py): the GBPJ file read in Python, into the project view's model and the MIDI / CSV /
JSON export; the .s3p library (projects.py); fetching the project from the FM-1 (read only).

Fixtures (tests/fixtures/sloop3/):
* twin-3.4.0-now.s3p – format 16, written by SLOOP 3 3.4.0's own project.c on its PC twin
  (tests/fixtures/sloop3/make_project_fixture.py says how). Every value asserted below was also printed by the
  firmware's own loader (project_load, compiled from the v3.4.0-beta tag) from the same file.
* twin-3.3.7.json "now" – format 15, the 3.3.7 twin's project as the existing SLOOP 3 tests replay it.
"""
from __future__ import annotations

import base64
import json
import random
import struct
import sys
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fm1companion import midiexport as ME, projectfmt as PF, projects, sloop3 as S3, sloop3proj as P  # noqa: E402
from fm1companion import sloop3bk as BK  # noqa: E402
from fm1companion.transport import Link, SubprocessTransport  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "sloop3"
V16 = (FIX / "twin-3.4.0-now.s3p").read_bytes()
V15 = base64.b64decode(json.loads((FIX / "twin-3.3.7.json").read_text("utf-8"))["objects"]["now"])


def test_format16_as_the_firmware_reads_it():
    r = P.parse(V16)
    assert (r.version, r.bpm, r.swing, r.song_len, r.trailing) == (16, 122.5, 20, 6, 0)
    t0, t1, t2, dr = r.tracks[0], r.tracks[1], r.tracks[2], r.tracks[3]
    assert (t0.engine, t0.div, t0.gate, t0.quant, t0.preset) == (0, 2, 100, 100, 0)
    assert (t1.engine, t1.div, t1.gate, t1.quant, t1.swing, t1.preset) == (2, 4, 50, 50, 30, 22)
    assert (t2.engine, t2.div, t2.preset) == (11, 1, 65) and (dr.engine, dr.preset) == (1, 153)
    assert r.tracks[4].preset == 5
    p = r.patterns[(0, 0)]
    assert p.steps == 32 and [(n.tick, n.length, n.note, n.vel, n.cond, n.ratchet) for n in p.notes] == [
        (0, 94, 36, 100, 0, 1), (384, 94, 39, 90, 0, 34), (768, 180, 41, 100, 0, 17), (960, 94, 43, 80, 69, 1),
        (1152, 94, 48, 100, 1, 1), (1566, 50, 36, 70, 0, 1)]
    assert p.locks == [(4, 5, 200), (8, 48, 64)]
    assert r.patterns[(2, 0)].steps == 128 and len(r.patterns[(2, 0)].notes) == 16
    assert [(e.bar, e.mute, e.pats) for e in r.song] == [(0, 0, [0] * 5), (2, 8, [1, 0, 0, 0, 0]), (3, 0, [1, 0, 0, 0, 0])]
    assert r.kept == [0, 1, 2, 3]                    # (never saved on the twin: the playing patterns differ from it)


def test_format15_of_337():
    r = P.parse(V15)
    assert r.version == 15 and r.bpm == 92.0 and sorted(r.patterns) == [(0, 0), (1, 0), (3, 0)]
    assert [len(r.patterns[k].notes) for k in sorted(r.patterns)] == [9, 12, 10] and r.patterns[(1, 0)].steps == 64
    prj = P.decode(V15)
    assert prj.ok and prj.format == "SLOOP 3 (GBPJ 15)" and len(prj.used_tracks) == 5


def test_into_the_project_views_model():
    prj = PF.decode(V16, "sloop3")                    # (projectfmt hands GBPJ to sloop3proj)
    assert prj.ok and prj.bpm == 122.5 and prj.swing == 20
    names = [t.name for t in prj.tracks]
    assert names[:4] == [f"Spur {n}" for n in (1, 2, 3, 4)] and names[4] == "Schlagzeug"    # (German test UI)
    assert [t.midi_channel for t in prj.tracks] == [0, 1, 2, 3, 9]
    assert prj.tracks[0].engine_name == "ANALOG" and prj.tracks[0].preset_name == "808 BOOM"
    assert prj.tracks[1].engine_name == "DIGITAL" and prj.tracks[4].engine_name == "DRUM"
    assert prj.tracks[4].preset_name == "ACOUSTIC"
    p = prj.tracks[0].patterns[0]
    assert p.length == 32 and p.used and p.step_quarters == 0.25 and p.div_name == "1/16"
    assert prj.tracks[1].patterns[0].step_quarters == 0.5 and prj.tracks[2].patterns[0].div_name == "16T"
    assert len(prj.tracks[2].patterns[0].steps) == 128 and len(prj.tracks[0].patterns) == 8
    # FILL-only note left out; the ratchet ×2 is two hits, the accent plays 127
    rec = p.recorded
    assert 48 not in [n.note for n in rec] and [n.note for n in rec].count(39) == 2
    assert max(n.vel for n in rec if n.note == 39) == 127
    assert prj.motion == [(0, 4, 5, 200), (0, 8, 48, 64)]
    assert len(prj.song) == 3 and [r.quarters for r in prj.song] == [8.0, 4.0, 12.0]
    assert prj.song[1].banks[4] == PF.CHAIN_REST and prj.song[2].banks[0] == PF.CHAIN_HOLD
    assert prj.song[2].banks[4] == 0                  # (unmuted again: played from its start, not lost)
    assert "2" in prj.note and prj.summary()


def test_timing_like_seq_c():
    prj = P.decode(V16)
    o = ME.Options(mode="pattern")
    ev = ME.events(prj, o, (4, 0))                    # the drums: swing 20 moves every second step 9.6 ticks late
    starts = [round(e.start, 4) for e in ev]
    assert starts[:3] == [0.0, round(0.25 + 9 / 96 * 0.25, 4), 0.5] and all(e.channel == 9 and e.drum for e in ev)
    assert ev[0].note == P.GM_PAD[36 & 15]
    ev1 = ME.events(prj, o, (1, 0))                    # DIV 1/8, GATE 50: 380 ticks -> 190, a step is half a quarter
    assert round(ev1[0].length, 4) == round(190 / 96 * 0.5, 4) and ME.pattern_quarters(prj.tracks[1].patterns[0]) == 4
    ev2 = ME.events(prj, o, (2, 0))                    # 16T is never swung; 128 steps of 1/6 quarter
    assert ME.pattern_quarters(prj.tracks[2].patterns[0]) == pytest.approx(128 / 6)
    assert ev2[1].start == pytest.approx(8 / 6)          # (tick 775: QUANT 100 plays it on its step, 768)
    song = ME.events(prj, ME.Options(mode="song"))
    assert max(e.start for e in song) < 24 and any(e.track == 4 and e.start >= 12 for e in song)
    assert not any(e.track == 4 and 8 <= e.start < 12 for e in song)        # (the drums muted in bar 3)


def test_midi_csv_json_export(tmp_path):
    import mido
    prj = P.decode(V16)
    path = ME.save_midi(prj, tmp_path / "s3.mid", ME.Options(mode="song"))
    mf = mido.MidiFile(str(path))
    assert mf.type == 1 and len(mf.tracks) == 5          # tempo + the 4 tracks with notes (the 4th has none)
    assert any(m.type == "set_tempo" and round(mido.tempo2bpm(m.tempo), 1) == 122.5 for m in mf.tracks[0])
    assert {m.channel for m in mf.tracks[4] if m.type == "note_on"} == {9}
    csv = ME.to_csv(prj, ME.Options(mode="active"))
    assert csv.count("\n") > 30
    js = json.loads(ME.to_json(prj))
    assert js["format"] == "SLOOP 3 (GBPJ 16)" and js["motion"] == [[0, 4, 5, 200], [0, 8, 48, 64]]


def test_every_cut_is_refused_cleanly():
    for n in range(len(V16)):
        with pytest.raises(P.S3FormatError):
            P.parse(V16[:n])
        prj = P.decode(V16[:n])
        assert not prj.ok and prj.warnings
    assert P.parse(V16 + b"\0\0").trailing == 2        # (bytes after the end: the firmware allows them too)


def test_damaged_files_never_crash():
    rng = random.Random(3)
    for _i in range(400):
        b = bytearray(rng.choice((V15, V16)))
        for _k in range(rng.randrange(1, 5)):
            b[rng.randrange(5, len(b))] = rng.randrange(256)
        prj = P.decode(bytes(b))
        assert prj.ok or prj.warnings
    bad = bytearray(V16)
    count_at = 5 + 4 + 8 * (4 + 4 * 48 + 16 + 3)
    struct.pack_into("<H", bad, count_at + 4, 0)       # the first pattern: 0 steps
    with pytest.raises(P.S3FormatError, match="beschädigt"):            # (German test UI)
        P.parse(bytes(bad))


def test_a_newer_format_is_refused_not_guessed():
    future = V16[:4] + bytes([17]) + V16[5:]
    with pytest.raises(P.S3FormatError) as e:
        P.parse(future)
    assert "17" in str(e.value) and "16" in str(e.value)
    prj = P.decode(future)
    assert not prj.ok and prj.format == "SLOOP 3 (GBPJ 17)"
    for b in (b"", b"GBPJ", b"GBPJ\x00" + bytes(20), b"FUN6" + bytes(40)):
        assert not P.decode(b).ok


def test_containers(tmp_path):
    s3b = P.to_s3b([(0, 0, V16), (1, 0, b"S3US" + bytes(20))])
    assert P.from_s3b(s3b) == [(0, 0, V16), (1, 0, b"S3US" + bytes(20))]
    with pytest.raises(P.S3FormatError):
        P.from_s3b(s3b[:-3])
    (tmp_path / "a.s3b").write_bytes(s3b)
    (tmp_path / "b.s3p").write_bytes(V16)
    obj = lambda i, d: {"id": i, "size": len(d), "crc": zlib.crc32(d) & 0xFFFFFFFF,     # noqa: E731
                        "data": base64.b64encode(d).decode()}
    bk = {"format": BK.FORMAT, "version": BK.VERSION, "objects": [obj(BK.PROJECT, V15), obj(BK.NOW, V16)]}
    (tmp_path / "c.json").write_text(json.dumps(bk), "utf-8")
    assert [len(d) for _t, d in P.projects_in(tmp_path / "a.s3b")] == [len(V16)]
    for name, n in (("a.s3b", 1), ("b.s3p", 1), ("c.json", 2)):
        items = PF.load_any(tmp_path / name)
        assert len(items) == n and all(p.ok for _t, p in items)
    assert [p.format for _t, p in PF.load_any(tmp_path / "c.json")] == ["SLOOP 3 (GBPJ 15)", "SLOOP 3 (GBPJ 16)"]


def test_library(tmp_path):
    p1 = projects.save_s3p(tmp_path, "My/Song", V16)
    p2 = projects.save_s3p(tmp_path, "My/Song", V15)
    assert p1.suffix == ".s3p" and p1.read_bytes() == V16 and p2 != p1          # never over another file
    with pytest.raises(P.S3FormatError):
        projects.save_s3p(tmp_path, "x", b"not a project")
    (tmp_path / "broken.s3p").write_bytes(V16[:100])
    lib = {f.name: f for f in projects.s3p_library(tmp_path)}
    assert lib["My_Song"].bpm == 122.5 and lib["My_Song"].bars == 6.0 and lib["My_Song"].version == 16
    assert lib["My_Song (2)"].bpm == 92 and lib["My_Song (2)"].bars == 4.0
    assert lib["broken"].problem and lib["broken"].bpm is None


@pytest.fixture
def s3dev(tmp_path):
    log = tmp_path / "seen.log"
    t = SubprocessTransport([sys.executable, str(ROOT / "tests" / "fake_sloop3.py"), "--log", str(log)])
    assert t.wait_started(60)
    link = Link(t)
    link.firmware = "sloop3"
    yield link, (lambda: [bytes.fromhex(x) for x in log.read_text().split()] if log.exists() else [])
    link.close()


def test_fetch_from_the_fm1_is_read_only(s3dev, tmp_path):
    link, seen = s3dev
    data = projects.fetch_sloop3(link, S3.KIND_NOW)
    assert data == V15 and P.decode(data).ok
    path = projects.save_s3p(tmp_path, "now", data)
    assert path.read_bytes() == V15
    cmds = {S3.unframe(m)[0] for m in seen() if S3.unframe(m)}
    assert cmds <= {S3.Cmd.INFO, S3.Cmd.READ}                       # nothing that writes


# ------------------------------------------------------------------------------------------------ the pages ---
@pytest.fixture
def s3ctx(s3dev, tmp_path, monkeypatch):
    """a DeviceContext as ui/device.py leaves it for SLOOP 3, its jobs run at once (no dialog)"""
    import os
    from types import SimpleNamespace
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])  # noqa: F841
    from fm1companion.ui import device as D
    link, seen = s3dev
    ctx = D.DeviceContext()
    ctx.timer.stop()
    ctx.settings.user_root = str(tmp_path / "FM-1")
    ctx.link = link
    ctx.port = SimpleNamespace(key="s3", label="FM-1 SLOOP 3", virtual=False, update_mode=False, usb_id="",
                               usb_path="", in_name="", out_name="", serial="")
    with S3.Client(link) as c:
        ctx.sloop3 = c.info()
    seen_before = len(seen())

    class W:
        log = progress = SimpleNamespace(emit=lambda *a: None)
        cancelled = False

    def job(parent, title, fn, *a, on_done=None, **k):
        with link.exclusive():
            res = fn(W, link)
        if on_done:
            on_done(res)
        return SimpleNamespace(ok=True, result_value=res)
    ctx.job = job
    yield ctx, (lambda: seen()[seen_before:])


def test_projects_page_for_sloop3(s3ctx, monkeypatch):
    from PyQt6.QtWidgets import QInputDialog
    from fm1companion.ui import projects_page as PP
    ctx, seen = s3ctx
    shown = []
    monkeypatch.setattr(PP, "show_error", lambda *a: shown.append(a[-1]))
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: (k.get("text", "x"), True)))
    page = PP.ProjectsPage(ctx)
    page.activate()
    assert page.dev_tree.topLevelItemCount() == 2 and "SLOOP 3" in page.dev_info.text()
    assert not page.b_imp.isEnabled() and "Noch nicht" in page.b_imp.toolTip()       # writing: not yet
    page._import()
    assert shown and "Noch nicht" in shown[-1]
    page._export_all()                                                              # (no project saved: only "now")
    lib = projects.s3p_library(ctx.settings.projects_dir)
    assert len(lib) == 1 and lib[0].path.read_bytes() == V15 and lib[0].bpm == 92
    page.refresh_library()
    it = page.lib.topLevelItem(0)
    assert it.text(1) == "SLOOP 3" and it.text(6) == "92 BPM" and it.text(7)
    page.lib.setCurrentItem(it)
    page._view_lib()                                                                # (no main window: its own view)
    assert page._pv.prj.ok and page._pv.prj.format == "SLOOP 3 (GBPJ 15)"
    page._erase()
    cmds = {S3.unframe(m)[0] for m in seen() if S3.unframe(m)}
    assert cmds <= {S3.Cmd.INFO, S3.Cmd.READ}                                       # read only, all the way


def test_project_view_reads_sloop3_live(s3ctx):
    from fm1companion.ui.pages.project_view import ProjectView
    ctx, seen = s3ctx
    v = ProjectView(ctx)
    v.slot.setValue(0)
    v.read_device()
    assert v.prj is not None and v.prj.ok and "SLOOP 3" in v.info.text()
    assert v.song_row.isHidden()                                                    # (no "start the song": Melodee's)
    assert v.tracks.rowCount() == 5 and v.tracks.item(4, 0).text() == "Schlagzeug"
    v.show_pattern(1, 0)
    assert "64 Steps" in v.roll_title.text() and "1/16" in v.roll_title.text()
    cmds = {S3.unframe(m)[0] for m in seen() if S3.unframe(m)}
    assert cmds <= {S3.Cmd.INFO, S3.Cmd.READ}
