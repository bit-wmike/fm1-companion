# SPDX-License-Identifier: GPL-3.0-only
"""SLOOP 2.6 – 2.8.x projects (isod89/sloop-fm1, firmware/src/project.c "FUN6"): read only, for the project view and
the MIDI / CSV / JSON export.

A SLOOP 2 project is one song section (the running project, or section A–D: backup objects 0 and 2–5 of a
"sloop-backup"). FUN6 is project.c's ``project_t`` as it lies in memory, 3816 bytes, little-endian: magic "FUN6", its
size, 32 globals (int16: BPM, SWING …), the selected track, then four tracks (three synth parts, the drums as the 4th)
of: 53 parameters (int16, P_LEVEL … P_VLEAD), P_E0 … P_E7 (int16), ten 2.6 parameters (a byte each), engine, preset,
64 steps of 10 bytes (synth: 4 notes, count, NOTE / TIE / REST, ACCENT / SLIDE, velocity, levels, ratchets; drums: 16
lanes on / level / ratchet), each step's nudge and fill condition, 24 parameter locks; an FNV-1a sum at the end (the
firmware refuses a project whose sum does not match, and so does this reader). Older formats (FUN1 – FUN5, SLOOP 1.x
– 2.5) are converted by the firmware when it loads them; here they are named, not shown.

Timing as in seq.c: LEN 1 – 64 steps of DIV (1/4 … 2BAR); SWING (the track's + the global, at most 100) starts every
second step up to half a step late, on the straight grids only (1/4 … 1/32); GATE /128; TIE and SLIDE as on Melodee.
Velocity: ACCENT 127, else the step's (0: 96), a level (ghost / soft / hard) 42 / 72 / 127. The drums play their lane's
GM note (drums.c LANE_NOTE) on channel 10.
Not modelled: nudges, ratchets, the song's section order (it is in the settings, not in a project), arpeggiator and
chords; fill-only steps are left out, parameter locks are listed (JSON), not played.
"""
from __future__ import annotations

import struct

from . import projectfmt as PF
from .i18n import _, ngettext

MAGIC = 0x46554E36                 # "FUN6"
SIZE = 3816
NTRK, NSTEP, NLOCK, DRUM = 4, 64, 24, 3
NC6, NX6 = 53, 10                  # P_LEVEL .. P_VLEAD in int16, then P_ITYPE .. P_LPOL in a byte
P_LEVEL, P_SLEN, P_SDIV, P_SSWING, P_SGATE, P_PAN, P_MUTE, P_E0 = 0, 29, 30, 31, 32, 39, 40, 63
G_BPM, G_SWING = 0, 1
TRK_SIZE = 2 * NC6 + 16 + NX6 + 2 + 10 * NSTEP + NSTEP + 4 * NLOCK          # 934
TRK_AT = 8 + 2 * 32 + 4                                                      # 76
ENGINES = ("ANALOG", "DIGITAL", "PHASE", "LOFI", "SAMPLE", "VOICE", "TRIO", "WHEEL", "GRAIN", "FM6", "PHYS", "NOISE",
           "SLICE")            # params.c N_ENGNAME (SLICE only in builds with FELUCCA_SLICE)
LANE_NOTE = (36, 35, 38, 39, 42, 46, 44, 37, 40, 43, 48, 49, 51, 70, 63, 56)   # drums.c: a lane's GM note
LV_VEL = (0, 42, 72, 127)          # drums.c lvl_vel: LV_NORM (the step's), GHOST, SOFT, HARD
FC_FILL = 1
OLDER = {0x46554E35: "FUN5 (SLOOP 2.4–2.5)", 0x46554E34: "FUN4 (SLOOP 2.0–2.3)", 0x46554E33: "FUN3 (SLOOP 1.x)",
         0x46554E32: "FUN2 (SLOOP)", 0x46554E31: "FUN1 (SLOOP)"}
assert TRK_AT + NTRK * TRK_SIZE + 4 == SIZE


class S2FormatError(Exception):
    pass


def fnv(data: bytes) -> int:
    h = 0x811C9DC5
    for b in data:
        h = ((h ^ b) * 16777619) & 0xFFFFFFFF
    return h


def is_project(data: bytes) -> bool:
    return len(data) >= 4 and struct.unpack_from("<I", data)[0] in (MAGIC, *OLDER)


class S2Track(PF.Track):
    @property
    def name(self) -> str:
        return _("Drums") if self.index == DRUM else _("Track {n}").format(n=self.index + 1)

    @property
    def engine_name(self) -> str:
        return "DRUM" if self.index == DRUM else ENGINES[self.engine] if self.engine < len(ENGINES) else str(self.engine)

    @property
    def preset_name(self) -> str:
        return "" if self.index == DRUM else f"Preset {self.preset + 1}"

    @property
    def level(self) -> int:
        return self.p(P_LEVEL, 104)

    @property
    def pan(self) -> int:
        return self.p(P_PAN, 0)

    @property
    def mute(self) -> bool:
        return bool(self.p(P_MUTE, 0))

    @property
    def midi_channel(self) -> int:
        return 9 if self.index == DRUM else self.index


def parse(data: bytes) -> PF.Project:
    """a FUN6 project -> projectfmt.Project. S2FormatError (a translated sentence) if it is none or is damaged."""
    b = bytes(data)
    magic = struct.unpack_from("<I", b)[0] if len(b) >= 4 else 0
    if magic in OLDER:
        raise S2FormatError(_("SLOOP project in the older format {format}: SLOOP converts it when it loads it; the "
                              "project view reads the format of SLOOP 2.6 and later (FUN6). Load it on the FM-1 and "
                              "save it again, then read it again.").format(format=OLDER[magic]))
    if magic != MAGIC:
        raise S2FormatError(_("not a SLOOP project"))
    if len(b) < SIZE or struct.unpack_from("<I", b, 4)[0] != SIZE:
        raise S2FormatError(_("the file ends too early (cut off or damaged)"))
    if struct.unpack_from("<I", b, SIZE - 4)[0] != fnv(b[:SIZE - 4]):
        raise S2FormatError(_("the checksum does not match – the project is damaged (SLOOP would not load it "
                              "either)"))
    g = struct.unpack_from("<32h", b, 8)
    prj = PF.Project(format="SLOOP 2 (FUN6)", size=len(b), bpm=g[G_BPM], swing=max(0, min(100, g[G_SWING])),
                     globals=list(g), selected=b[72], tracks_in_use=NTRK, hash_ok=True)
    locks = 0
    for k in range(NTRK):
        at = TRK_AT + k * TRK_SIZE
        p = list(struct.unpack_from(f"<{NC6}h", b, at))
        e = list(struct.unpack_from("<8h", b, at + 2 * NC6))
        x = list(b[at + 2 * NC6 + 16: at + 2 * NC6 + 16 + NX6])
        params = p + x + e                               # (ids: P_LEVEL .. P_VLEAD, P_ITYPE .. P_LPOL, P_E0 .. P_E7)
        engine, preset = b[at + 2 * NC6 + 16 + NX6], b[at + 2 * NC6 + 17 + NX6]
        steps_at = at + 2 * NC6 + 18 + NX6
        mf_at = steps_at + 10 * NSTEP
        t = S2Track(index=k, engine=engine, preset=preset, params=params)
        ln = max(1, min(NSTEP, params[P_SLEN] or 16))
        div = params[P_SDIV] if 0 <= params[P_SDIV] <= 8 else 2
        pat = PF.Pattern(index=0, length=ln, div=div, swing=max(0, min(100, params[P_SSWING])),
                         gate=max(1, min(127, params[P_SGATE] or 64)), swing_den=200 if div < 4 else 0)
        for i in range(NSTEP):
            s = b[steps_at + 10 * i: steps_at + 10 * i + 10]
            fill_only = (b[mf_at + i] >> 6) == FC_FILL
            if k == DRUM:
                on = s[0] | s[1] << 8
                lv = int.from_bytes(s[2:6], "little")
                st = pat.steps[i]
                for lane in range(16):
                    if on >> lane & 1 and not fill_only:
                        lvl = lv >> (2 * lane) & 3
                        note = LANE_NOTE[lane]
                        pat.recorded.append(PF.TimedNote(i, 0.0, 0.5, note, LV_VEL[lvl] if lvl else 100, True))
                        st.time, st.recorded, st.vel = PF.ST_NOTE, True, 100
                        if len(st.notes) < 4:
                            st.notes.append(note)
                continue
            n, time, flags, vel, lvl = min(4, s[4]), s[5], s[6], s[7], s[8]
            if time > 2:
                raise S2FormatError(_("a step that cannot be (damaged project)"))
            base = 127 if flags & 1 else vel or 96
            v = LV_VEL[lvl & 3] if lvl & 3 else base
            if time == PF.ST_NOTE and (not n or fill_only):
                time = PF.ST_REST                        # (empty, or only in a fill: not exported)
            pat.steps[i] = PF.Step(notes=list(s[:n]) if time == PF.ST_NOTE else [], time=time,
                                   accent=bool(flags & 1), slide=bool(flags & 2), vel=v)
        t.patterns = [pat]
        t.active = 0
        lk_at = mf_at + NSTEP
        for j in range(NLOCK):
            step, param, val = struct.unpack_from("<BBh", b, lk_at + 4 * j)
            if step < NSTEP:
                prj.motion.append((k, step, param, val))
                locks += 1
        prj.tracks.append(t)
    if locks:
        prj.note = ngettext("{n} parameter lock (listed in the JSON export, not played)",
                            "{n} parameter locks (listed in the JSON export, not played)", locks).format(n=locks)
    prj.ok = True
    return prj


def decode(data: bytes) -> PF.Project:
    """never raises: a project that cannot be read comes back with ok False and the reason"""
    try:
        return parse(data)
    except (S2FormatError, struct.error, IndexError, ValueError) as e:
        magic = struct.unpack_from("<I", data)[0] if len(data) >= 4 else 0
        prj = PF.Project(format=f"SLOOP {OLDER.get(magic, 'FUN6' if magic == MAGIC else '?')}", size=len(data))
        prj.warnings.append(str(e))
        return prj
