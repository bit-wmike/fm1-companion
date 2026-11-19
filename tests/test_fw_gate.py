# SPDX-License-Identifier: GPL-3.0-only
"""Commands whose number means something else on another firmware of the Felucca family are only sent to the firmware
that speaks them (protocol.command_refusal, transport.Link.request) – and Compose, the workshop and the project view
disable their buttons for the others.

  TRACK_STEP 30 with a step: Melodee, Felucca, Skiff (the 12-byte step); SLOOP 2 / KARMA read it as their own v5 step
  PATTERN 73, BANK_SONG 74: Melodee only (SLOOP 2 / KARMA: DSYN_GET / DSYN_PUT; Felucca 74 FM6B_BEGIN; Skiff 74 KIT_GET)
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fm1companion import midi_import as MI, playback as P, projectfmt as PF, protocol  # noqa: E402
from fm1companion.demo_device import FakeFM1  # noqa: E402
from fm1companion.transport import Link, Transport, UnsupportedCommand  # noqa: E402

MELODEE_ONLY = (73, 74)


class Recorder(Transport):
    """every Felucca frame the app sends; a device behind it (FakeFM1) or a minimal one that answers INFO with
    `version` (and echoes TRACK_STEP / PATTERN / BANK_SONG as a firmware would that has them – the test is that they are
    never sent, not what comes back)"""

    def __init__(self, dev=None, version: str = "", engines=("ANALOG",), answers_info: bool = True, tail=(5,)):
        self.dev, self.version, self.engines, self.answers_info = dev, version, engines, answers_info
        self.tail = list(tail)                     # what INFO says after the engines: NTRK, then tags or a protocol byte
        self.sent: list[list[int]] = []

    def cmds(self):
        return [(f[0], f[1]) for f in (protocol.unframe(m) for m in self.sent) if f]

    def send(self, data):
        self.sent.append(list(data))
        r = self.dev.handle(list(data)) if self.dev is not None else self._answer(list(data))
        if r is not None and self.receiver:
            threading.Thread(target=self.receiver, args=(r,), daemon=True).start()

    def _answer(self, data):
        f = protocol.unframe(data)
        if not f:
            return None
        cmd, a = f
        if cmd == 1 and self.answers_info:
            s = lambda t: list(t.encode()) + [0]   # noqa: E731
            body = [*s(self.version), len(self.engines), 71, 50, 64, 63, *[x for e in self.engines for x in s(e)], *self.tail]
        elif cmd == 30 and len(a) >= 2:
            body = [a[0], a[1], *(a[2:14] if len(a) > 2 else [0, 0, 0, 0, 0, 2, 0, 100, 0, 0, 0, 100])]
        elif cmd == 31 and len(a) >= 2:
            body = [a[0], a[1], *(a[2:4] if len(a) >= 4 else [0, 64])]
        elif cmd == 5 and len(a) >= 2:
            body = [0, a[1], 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, *list(b"LEN\0"), 0]
        else:
            return None
        return [0xF0, *protocol.HDR, cmd, *body, 0xF7]


# Felucca's INFO after the engines (editor.c ED_INFO): NTRK, CHAIN_ROWS, then tags – 55 01 (preferences), 4D 01
# (motion + chance: the 12-byte step's chance byte), 42 01 (backup) …; SLOOP's ends with NTRK and its protocol byte
FELUCCA_TAIL = (4, 16, 0x55, 1, 3, 0x4D, 1, 64, 1, 0x42, 1, 3)


def _sloop_release():
    """a SLOOP fork built with tools/build.py --release: INFO "FELUCCA 0.9 BETA", SLOOP's step and commands"""
    d = FakeFM1(version="SLOOP 2.8.5")
    d.version = "FELUCCA 0.9 BETA"
    return Recorder(d)


FAKES = {
    "sloop2": lambda: Recorder(FakeFM1(version="SLOOP 2.8.5")),
    "sloop-release": _sloop_release,
    "karma": lambda: Recorder(version="FELUCCA KARMA 3.1.5"),
    "skiff": lambda: Recorder(version="FELUCCA v2.7", engines=("ANALOG", "DRUM-X"), tail=FELUCCA_TAIL),
    "felucca": lambda: Recorder(version="FELUCCA v1.8.1", tail=FELUCCA_TAIL),
    "felucca-untagged": lambda: Recorder(version="FELUCCA v1.8.1", tail=(4, 12)),
    "jiant": lambda: Recorder(version="JIANT v0.9.18", tail=(6, 16, 0x4D, 1, 64, 1)),
    "sloop3": lambda: Recorder(answers_info=False),           # (SLOOP 3 answers no Felucca frame)
}
STEP12_FAKES = ("skiff", "felucca", "jiant")


def _run_everything(link):
    """what Compose, the workshop and the project view send – each may refuse"""
    steps = [PF.Step(notes=[60], time=PF.ST_NOTE, vel=100)] + [PF.Step() for _i in range(63)]
    for fn in (lambda: MI.read_device_pattern(link, 0, 2), lambda: MI.read_device_pattern(link, 0, None),
               lambda: MI.write_device_pattern(link, 0, 2, steps, 16),
               lambda: MI.write_device_pattern(link, 0, None, steps, 16, swing=10),
               lambda: MI.write_pattern(link, 0, 1, steps, 16),
               lambda: MI.read_song(link), lambda: MI.write_song(link, [([1, 0, 0, 0], 1)]),
               lambda: P.song_start(link, 0), lambda: P.transport_stop(link)):
        try:
            fn()
        except Exception:  # noqa: BLE001 – refused or no answer: what matters is what was sent
            pass


@pytest.mark.parametrize("name", sorted(FAKES))
def test_no_melodee_command_reaches_another_firmware(name):
    t = FAKES[name]()
    link = Link(t)
    if name == "sloop3":
        link.firmware = "sloop3"                               # (as ui/device.py sets it)
    _run_everything(link)
    sent = t.cmds()
    assert not [c for c, a in sent if c in MELODEE_ONLY], sent
    writes = [a for c, a in sent if c == 30 and len(a) - 2 >= protocol.STEP12_FROM]
    if name in STEP12_FAKES:
        assert writes                                          # they read Felucca's 12-byte step (editor.c ed_step_put)
    else:
        assert not writes, f"{name}: TRACK_STEP with a step was sent"
    if name == "sloop3":                                       # (reads of TRACK_STEP: Felucca frames SLOOP 3 ignores)
        assert {c for c, _a in sent} <= {30, 31, 1, 5}, sent


@pytest.mark.parametrize("name", ["sloop2", "sloop-release", "felucca-untagged", "karma", "sloop3"])
def test_refused_before_sending(name):
    t = FAKES[name]()
    link = Link(t)
    if name == "sloop3":
        link.firmware = "sloop3"
    steps = [PF.Step() for _i in range(64)]
    with pytest.raises(UnsupportedCommand) as e:
        MI.write_device_pattern(link, 0, None, steps, 16)
    assert e.value.feature == "pattern_write"
    assert not [c for c, a in t.cmds() if c == 30 and len(a) - 2 >= protocol.STEP12_FROM]
    with pytest.raises(UnsupportedCommand):
        link.request(protocol.Cmd.BANK_SONG, [2])
    with pytest.raises(UnsupportedCommand):
        link.request(protocol.Cmd.PATTERN, [0, 0])


def test_melodee_unchanged():
    t = Recorder(FakeFM1())
    link = Link(t)
    steps = [PF.Step(notes=[60], time=PF.ST_NOTE, vel=100)] + [PF.Step() for _i in range(63)]
    snap = MI.read_device_pattern(link, 1, 3)
    MI.write_device_pattern(link, 1, 3, steps, 16)
    MI.restore_snapshot(link, snap)
    assert MI.write_song(link, [([1, 0, 0, 0], 2)]) == 0 and MI.read_song(link).rows == [([1, 0, 0, 0], 2)]
    cmds = [c for c, _a in t.cmds()]
    assert 73 in cmds and 74 in cmds and 30 in cmds
    assert link.firmware == "melodee"                         # (asked INFO once by itself)
    assert cmds.count(1) == 1


def test_felucca_and_skiff_get_steps_but_no_banks():
    assert protocol.supports("felucca", "pattern_write") and protocol.supports("skiff", "pattern_write")
    for fw in ("felucca", "skiff", "sloop", "karma", "sloop3", "", None, "ghoulbox", "clipper"):
        assert not protocol.supports(fw, "pattern_banks") and not protocol.supports(fw, "song_chain")
        assert protocol.command_refusal(fw, 73, [0, 0]) and protocol.command_refusal(fw, 74, [0])
        assert protocol.command_refusal(fw, 30, [0, 1]) == ""          # (a read is the same everywhere)
    for fw in ("sloop", "karma", "sloop3", "", "orbit", "clipper"):
        assert protocol.command_refusal(fw, 30, [0, 1] + [0] * 12) == "pattern_write"
        assert protocol.command_refusal(fw, 30, [0, 1] + [0] * 11) == "pattern_write"     # (SLOOP 2: na >= 11)
        assert protocol.command_refusal(fw, 30, [0, 1, 1, 60, 0, 0, 0, 0, 0, 100, 0, 0]) == ""   # its own 10 bytes
    assert protocol.command_refusal("melodee", 74, [1, 0]) == "" and protocol.command_refusal("melodee", 73, [0, 0]) == ""


def test_info_names_decide():
    for version, engines, name in (("MELODEE v1.6.1", (), "melodee"), ("FELUCCA SLOOP 2.8.5", (), "sloop"),
                                   ("FELUCCA KARMA 3.1", (), "karma"), ("FELUCCA v2.7", ("DRUM-X",), "skiff"),
                                   ("FELUCCA v1.8.1", ("ANALOG",), "felucca")):
        assert protocol.Info(version=version, engines=list(engines)).firmware_name == name


def test_felucca_only_with_its_info_tags():
    """H1: "FELUCCA 0.9 BETA" (a SLOOP fork built with --release) names no firmware: without Felucca's INFO tags the
    gate calls it unproven and sends no 12-byte step"""
    for name, gate in (("felucca", "felucca"), ("skiff", "skiff"), ("jiant", "jiant"), ("felucca-untagged", protocol.UNPROVEN),
                       ("sloop-release", protocol.UNPROVEN), ("sloop2", "sloop"), ("karma", "karma")):
        t = FAKES[name]()
        link = Link(t)
        assert link.firmware_name() == gate, name
    t = FAKES["sloop-release"]()
    link = Link(t)
    _run_everything(link)
    assert not [a for c, a in t.cmds() if c == 30 and len(a) - 2 >= protocol.STEP12_FROM]


def test_unanswered_info_is_asked_once():
    """L6: a link whose INFO does not answer is asked once, then stays refused (no INFO wait per step)"""
    t = Recorder(answers_info=False)
    link = Link(t)
    steps = [PF.Step(notes=[60], time=PF.ST_NOTE, vel=100)] + [PF.Step() for _i in range(63)]
    for _i in range(3):
        with pytest.raises(UnsupportedCommand):
            MI.write_device_pattern(link, 0, None, steps, 16)
    assert [c for c, _a in t.cmds()].count(1) == 2 and link.firmware == ""      # (one probe: INFO and its retry)
    t2 = Recorder(answers_info=False)
    link2 = Link(t2)
    for i in range(4):                                            # reads: no INFO asked at all
        try:
            link2.request(30, [0, i], timeout=0.05, retries=0)
        except Exception:  # noqa: BLE001
            pass
    assert 1 not in [c for c, _a in t2.cmds()]


# ------------------------------------------------------------------------------------------------ the pages ---
@pytest.fixture
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _ctx(t, info_version: str | None):
    from fm1companion.ui import device as D
    ctx = D.DeviceContext()
    ctx.timer.stop()
    ctx.link = Link(t)
    from types import SimpleNamespace
    ctx.port = SimpleNamespace(key="test-port", label="FM-1", virtual=False, update_mode=False, usb_id="", usb_path="",
                               in_name="", out_name="", serial="")
    if info_version is None:
        from fm1companion import sloop3 as S3
        ctx.sloop3 = S3.Info(proto=2, tracks=5, values=48, project=0, projects_used=0, sounds_used=0, version="3.4.0")
        ctx.link.firmware = "sloop3"
    else:
        ctx.info = protocol.parse_info(t._answer(protocol.frame(1))[5:-1]) if t.dev is None else \
            protocol.parse_info(t.dev.handle(protocol.frame(1))[5:-1])
        ctx.link.firmware = protocol.gate_name(ctx.info)     # (as ui/device.py sets it)
    t.sent.clear()
    return ctx


@pytest.mark.parametrize("name", ["sloop2", "sloop-release", "karma", "sloop3", "skiff", "jiant", "melodee"])
def test_pages_gate_their_buttons(qapp, name, monkeypatch):
    from fm1companion.ui import common
    from fm1companion.ui.pages.compose import ComposePage
    from fm1companion.ui.pages.project_view import ProjectView
    from fm1companion.ui.workshop_page import WorkshopPage
    t = Recorder(FakeFM1()) if name == "melodee" else FAKES[name]()
    ctx = _ctx(t, None if name == "sloop3" else "x")
    shown = []
    monkeypatch.setattr("fm1companion.ui.pages.compose.show_error", lambda *a: shown.append(a[-1]))
    monkeypatch.setattr("fm1companion.ui.workshop_page.show_error", lambda *a: shown.append(a[-1]))
    monkeypatch.setattr(common, "show_error", lambda *a: shown.append(a[-1]))
    c, w, v = ComposePage(ctx), WorkshopPage(ctx), ProjectView(ctx)
    writes = name in ("melodee", *STEP12_FAKES)
    assert c.b_write.isEnabled() == writes and w.b_send.isEnabled() == writes
    assert w.song.isEnabled() == (name == "melodee")
    if not writes:
        assert "does not support" in c.b_write.toolTip() or "unterstützt" in c.b_write.toolTip()
        assert c.b_write.toolTip() == w.b_send.toolTip()
        ctx.require = lambda parent: True                     # (SLOOP 3: not "connected" – the gate must hold anyway)
        c.write()                                             # a click that got through (keyboard, an old state)
        w.chunks = [[PF.Step() for _i in range(64)]]
        w._send()
        assert shown and not [x for x, _a in t.cmds() if x in (30, 31, 73, 74)]
    # the project view's "start the song on the FM-1": a Melodee-only command (BANK_SONG)
    from fm1companion.ui.common import device_tag
    v.prj = PF.Project(ok=True, song=[PF.SongRow(1, [0] * 8)], tracks=[])
    v.live_tag, v.live_slot = device_tag(ctx), 0
    monkeypatch.setattr(type(ctx), "connected", property(lambda self: True))
    v._update_song_row()
    assert (not v.song_row.isHidden()) == (name == "melodee")
    if name != "melodee":
        v.device_song()
        v.device_stop()
        assert not [x for x, _a in t.cmds() if x in MELODEE_ONLY]
    for p in (c, w, v):
        p.deleteLater()
    ctx.link.close()
