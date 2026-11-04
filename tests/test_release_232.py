# SPDX-License-Identifier: GPL-3.0-only
"""2.3.2: Melodee 1.6.1 (ohne Qt-Widgets).

  python3 -m pytest tests/test_release_232.py -q

* Studio-Zwilling 1.6.1 neben rc.7, rc.8 und 1.6.0; Simulator auf 1.6.1; NOTICE mit schriftlichem Angebot
* Werkzeug-Pin v1.6.1 (M4)
* Clock/Sync von 1.6.1: CLK INT sendet selbst Clock, Song-Start ab Abschnitt (Song Position + Continue),
  Song Position von der App (ClockMaster)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fm1companion import __version__, clock, firmware as fw, playback as P, studio  # noqa: E402

COMMIT_161 = "165d177a425944f3d30b0b669255a6ed0050c3a5"


def test_version_232():
    assert tuple(int(x) for x in __version__.split(".")) >= (2, 3, 2)
    meta = (ROOT / "packaging" / "fm1-companion.metainfo.xml").read_text("utf-8")
    assert '<release version="2.3.2"' in meta and meta.index("2.3.2") < meta.index("2.3.1")
    readme = (ROOT / "README.md").read_text("utf-8")
    assert "1.6.1" in readme


def test_twin_161_built_and_picked():
    vs = [b.version for b in studio.builds("melodee")]
    assert vs == ["v1.6.0-rc.7", "v1.6.0-rc.8", "v1.6.0", "v1.6.1"]
    m = json.loads((studio.SIMS_DIR / "melodee-studio" / "1.6.1" / "manifest.json").read_text())
    assert m["commit"] == COMMIT_161 and m["ref"] == "v1.6.1" and m["identity"] == "FM-1_91061"
    notice = (studio.SIMS_DIR / "melodee-studio" / "1.6.1" / "NOTICE").read_text()
    assert f"melodee-{COMMIT_161}.tar.gz" in notice and "Written offer" in notice
    b, exact, warn = studio.pick_build("melodee", "MELODEE v1.6.1")
    assert exact and b.version == "v1.6.1" and not warn
    b, exact, warn = studio.pick_build("melodee", "MELODEE v1.6.0")
    assert exact and b.version == "v1.6.0" and not warn
    assert studio.pick_build("melodee")[0].version == "v1.6.1"
    vc = studio.check_versions("v1.6.1", device="MELODEE v1.6.0")         # gleiche Pegel: nur der Versionshinweis
    assert not vc.ok and "CZ-1" not in vc.message


def test_simulator_161():
    m = json.loads((ROOT / "fm1companion" / "sims" / "melodee" / "manifest.json").read_text())
    assert m["version"] == "1.6.1" and m["commit"] == COMMIT_161
    notice = (ROOT / "fm1companion" / "sims" / "melodee" / "NOTICE").read_text()
    assert "Melodee v1.6.1" in notice and f"melodee-{COMMIT_161}.tar.gz" in notice and "Written offer" in notice


def test_tool_pin_161():
    assert fw.KNOWN_TOOL_COMMITS["keremimo/melodee"]["v1.6.1"] == COMMIT_161


def test_clock_out_versions_and_notes():
    assert P.sends_clock("MELODEE v1.6.1") and P.sends_clock("Melodee 1.7.0")
    assert not P.sends_clock("MELODEE v1.6.0") and not P.sends_clock("SLOOP v2.8.3") and not P.sends_clock("")
    assert P.song_sync_note("MELODEE v1.6.0", 0, 3) == ""
    assert "Song Position" in P.song_sync_note("MELODEE v1.6.1", 0, 3)
    assert "Start" in P.song_sync_note("MELODEE v1.6.1", 0, 0) and "Song Position" not in P.song_sync_note("MELODEE v1.6.1", 0, 0)
    assert "USB" in P.song_sync_note("MELODEE v1.6.1", 1, 2) and "sendet selbst keine" in P.song_sync_note("v1.6.1 melodee", 1)
    assert "CLK" in P.CLOCK_CONFLICT and "USB" in P.CLOCK_CONFLICT


def test_song_start_from_section_and_clock_on_demo():
    from test_music import _DemoLink
    link = _DemoLink()
    link.d.song["rows"] = [[0, 0, 0, 0, 1], [1, 1, 1, 1, 2]]
    assert P.song_start(link, 2)[0] == 1                     # gibt es nicht
    assert P.song_start(link, 1)[0] == 0 and link.d.song["row"] == 1
    assert P.song_start(link, 0)[0] == 2                     # läuft schon: erst stoppen (wie chain_prepare_from)
    assert P.transport_stop(link)
    link.d.globals[P.G_CLOCK] = 1
    assert P.read_clock(link) == 1


def test_song_position_messages():
    assert clock.song_position(0) == [0xF2, 0, 0]
    assert clock.song_position(200) == [0xF2, 200 & 127, 200 >> 7]
    assert clock.song_position(99999) == [0xF2, 127, 127]
    assert clock.bar_position(1) == 0 and clock.bar_position(5) == 64
    sent = []
    m = clock.ClockMaster(sent.append, 300)
    m.start(position=64)
    m.stop()
    assert sent[0] == [0xF2, 64, 0] and sent[1] == [clock.CONTINUE] and sent[-1] == [clock.STOP]
    assert all(s == [clock.CLOCK] for s in sent[2:-1])
    sent.clear()
    m.start()
    m.stop()
    assert sent[0] == [clock.START]
