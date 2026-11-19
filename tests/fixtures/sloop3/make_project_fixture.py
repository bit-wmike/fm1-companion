#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""How tests/fixtures/sloop3/twin-3.4.0-now.s3p was made: SLOOP 3 3.4.0's PC twin (isod89/sloop3-fm1 v3.4.0-beta,
commit 2228313, `make build/twin`: host/twin.c 'serve' runs fw/main.c on a simulated FM-1) was given notes, locks,
a song, DIV / GATE / QUANT / SWING, tempo and a sound over SLOOP 3's own editor protocol, then the project as it is
(READ kind 2, the bytes the web editor saves as .s3p) was read back. The file is what the firmware's project.c wrote.

    make_project_fixture.py OUT_DIR TWIN_CMD…     (TWIN_CMD: a command that speaks the app's hex lines to the twin)
"""

import struct, sys, time
from fm1companion import sloop3 as S3
from fm1companion.transport import Link, SubprocessTransport
OUT = sys.argv[1]
t = SubprocessTransport(sys.argv[2:])
assert t.wait_started(60)
link = Link(t)
def note(tick, ln, n, vel=100, cond=0, rat=1):
    return struct.pack("<HHBBBB", tick, ln, n, vel, cond, rat)
with S3.Client(link, timeout=5) as c:
    print(c.info())
    time.sleep(1)
    live = lambda *a: c.ask(S3.Cmd.LIVE, bytes(a))
    live(13, 1225 & 255, 1225 >> 8, 20)            # 122.5 BPM, swing 20
    # track 0: a 32-step bass line, ratchets, accent, slide, a chance and a fill-only note
    ns = [note(0, 94, 36), note(96 * 4, 94, 39, 90, 0, 2 | 0x20), note(96 * 8, 180, 41, 100, 0, 1 | 0x10),
          note(96 * 10, 94, 43, 80, 0x45), note(96 * 12, 94, 48, 100, 1), note(96 * 16 + 30, 50, 36, 70)]
    print(c.ask(S3.Cmd.NOTES2, bytes([0, 0, 0, 0, 32, 0, len(ns)]) + b"".join(ns)).hex())
    # track 0, pattern 1 (a second pattern), 16 steps
    ns = [note(96 * i, 94, 60 + i % 5) for i in range(0, 16, 2)]
    print(c.ask(S3.Cmd.NOTES2, bytes([0, 1, 0, 0, 16, 0, len(ns)]) + b"".join(ns)).hex())
    # track 1: chords, DIV 1/8, gate 50, quant 50, its own swing
    ns = [note(0, 380, 60), note(0, 380, 64), note(0, 380, 67), note(96 * 4, 380, 62), note(96 * 4, 380, 65)]
    print(c.ask(S3.Cmd.NOTES2, bytes([1, 0, 0, 0, 8, 0, len(ns)]) + b"".join(ns)).hex())
    live(16, 1, 0, 4); live(16, 1, 1, 50); live(16, 1, 3, 50); live(16, 1, 2, 30)
    # track 2: 128 steps, a triplet DIV
    ns = [note(96 * i + 7, 60, 72 + i % 12, 100) for i in range(0, 128, 8)]
    print(c.ask(S3.Cmd.NOTES2, bytes([2, 0, 0, 0, 128, 0, len(ns)]) + b"".join(ns)).hex())
    live(16, 2, 0, 1)
    # drums (track index 3): kick / snare / hat pads
    ns = [note(96 * i, 48, [0, 7, 4, 4][i % 4] + 36, 110) for i in range(16)]   # notes 36.. -> pads by & 15
    print(c.ask(S3.Cmd.NOTES2, bytes([3, 0, 0, 0, 16, 0, len(ns)]) + b"".join(ns)).hex())
    # locks on track 0 pattern 0
    print(c.ask(S3.Cmd.LOCKS, bytes([0, 0, 2, 4, 5, 200, 8, 48, 64])).hex())
    # the song: bar 0 all pattern 0; bar 2 track 0 pattern 1, drums muted; bar 3 only an unmute; 6 bars long
    ent = lambda bar, mute, pats: struct.pack("<HB", bar, mute) + bytes(pats)
    song = ent(0, 0, [0, 0, 0, 0, 0]) + ent(2, 1 << 3, [1, 0, 0, 0, 0]) + ent(3, 0, [1, 0, 0, 0, 0])
    print(c.ask(S3.Cmd.SONG, bytes([0, 3]) + struct.pack("<H", 6) + song).hex())
    live(10, 4, 5, 0)                                 # track 5th-shown (index 4): factory sound 5
    time.sleep(0.5)
    now = c.read_file(S3.KIND_NOW)
open(OUT + "/now.s3p", "wb").write(now)
print(len(now), now[:6].hex())
link.close()
