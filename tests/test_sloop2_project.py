# SPDX-License-Identifier: GPL-3.0-only
"""SLOOP 2.6 – 2.8.x projects (sloop2proj.py, FUN6) in the project view and the MIDI export.

Fixture tests/fixtures/musik/sloop285_project.bin: SLOOP 2.8.5's own firmware code (the Studio twin bundled with the
app) given steps, drums, a lock, a nudge and a fill condition over SLOOP's editor protocol, then backup object 0 read
back (make_sloop2_fixture.mjs beside it). Every value asserted is one that was sent.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fm1companion import midiexport as ME, projectfmt as PF, sloop2proj as S2  # noqa: E402
from fm1companion.demo_device import FakeFM1, sloop_project  # noqa: E402

FIX = (ROOT / "tests" / "fixtures" / "musik" / "sloop285_project.bin").read_bytes()


def test_fun6_as_written():
    prj = PF.decode(FIX, "FELUCCA SLOOP 2.8.5")
    assert prj.ok and prj.format == "SLOOP 2 (FUN6)" and (prj.bpm, prj.swing) == (128, 10)
    t1, t2, t3, dr = prj.tracks
    assert [t.name for t in prj.tracks] == ["Spur 1", "Spur 2", "Spur 3", "Schlagzeug"]
    assert [t.midi_channel for t in prj.tracks] == [0, 1, 2, 9] and t1.engine_name == "ANALOG"
    p = t1.patterns[0]
    assert (p.length, p.div, p.swing, p.gate) == (32, 2, 20, 96)
    used = {i: (s.notes, s.time, s.vel, s.accent, s.slide) for i, s in enumerate(p.steps) if not s.empty}
    assert used == {0: ([48], 0, 100, False, False), 1: ([], 1, 96, False, False), 4: ([51], 0, 127, True, False),
                    8: ([53], 0, 90, False, True), 9: ([55], 0, 90, False, False), 16: ([60, 64, 67], 0, 80, False, False)}
    assert 24 not in used                                      # (only in a fill: left out)
    assert (t2.patterns[0].length, t2.patterns[0].div_name) == (8, "1/8")
    assert t3.patterns[0].div == 4 and t3.patterns[0].swing_den == 0           # 8T: never swung
    hits = sorted((n.step, n.note) for n in dr.patterns[0].recorded)
    assert hits[:4] == [(0, 36), (2, 42), (4, 36), (4, 38)] and all(n.drum for n in dr.patterns[0].recorded)
    assert prj.motion == [(0, 4, 0, 90)] and "1" in prj.note


def test_timing():
    prj = S2.decode(FIX)
    ev = ME.events(prj, ME.Options(mode="pattern"), (0, 0))
    # swing 20 + 10: an odd step starts 30 × ¼ / 200 quarters late; TIE: step 0 lasts two steps (× GATE 96/128)
    sw = 30 * 0.25 / 200
    first = ev[0]
    assert first.note == 48 and first.length == pytest.approx(0.5 - (0.25 + sw) + (0.25 + sw) * 96 / 128)
    n55 = next(e for e in ev if e.note == 55)
    assert n55.start == pytest.approx(9 * 0.25 + sw)
    drums = ME.events(prj, ME.Options(mode="pattern"), (3, 0))
    assert {e.channel for e in drums} == {9} and drums[0].start == 0.0


def test_damaged_and_older():
    bad = bytearray(FIX)
    bad[500] ^= 1
    prj = S2.decode(bytes(bad))
    assert not prj.ok and "Prüfsumme" in prj.warnings[0]
    for n in (0, 4, 100, 3815):
        assert not S2.decode(FIX[:n]).ok
    old = struct.pack("<I", 0x46554E35) + FIX[4:]
    prj = PF.decode(old)
    assert not prj.ok and "FUN5" in prj.format
    for seed in range(20):                                    # the demo device's random projects: refused cleanly
        S2.decode(sloop_project(seed))


def test_project_view_reads_sloop2_sections(monkeypatch):
    import os
    from types import SimpleNamespace
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])  # noqa: F841
    from fm1companion import protocol
    from fm1companion.transport import Link
    from fm1companion.ui import device as D
    from fm1companion.ui.pages.project_view import ProjectView
    from test_core import LocalTransport
    dev = FakeFM1(version="SLOOP 2.8.5")
    dev.objs[3] = FIX                                       # section B
    ctx = D.DeviceContext()
    ctx.timer.stop()
    ctx.link = Link(LocalTransport(dev))
    ctx.info = protocol.parse_info(dev.handle(protocol.frame(1))[5:-1])
    ctx.port = SimpleNamespace(key="s2", label="FM-1", virtual=False, update_mode=False, usb_id="", usb_path="",
                               in_name="", out_name="", serial="")
    from fm1companion import sloopbk
    monkeypatch.setattr(sloopbk, "MAX_LEN", max(sloopbk.MAX_LEN, len(FIX)))

    class W:
        log = progress = SimpleNamespace(emit=lambda *a: None)
        cancelled = False

    def job(parent, title, fn, *a, on_done=None, **k):
        with ctx.link.exclusive():
            res = fn(W, ctx.link)
        on_done(res)
    ctx.job = job
    v = ProjectView(ctx)
    v.slot.setValue(2)
    v.read_device()
    assert v.prj is not None and v.prj.ok and v.prj.bpm == 128 and "SLOOP 2" in v.info.text()
    assert v.song_row.isHidden()
