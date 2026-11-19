# SPDX-License-Identifier: GPL-3.0-only
"""SLOOP 3 projects (isod89/sloop3-fm1, app/project.c): read only, in pure Python, for the project view and the MIDI /
CSV / JSON export.

A SLOOP 3 project is the "GBPJ" file the firmware keeps (one project on the FM-1), that the web editor saves as
``.s3p`` and puts into its ``.s3b`` backup, and that the app's "sloop3-backup" holds as objects 0 (saved) and 2 (as it
was at that moment). Little-endian, versioned: format 1 to 16 (16 = SLOOP 3 3.4.0). The reader follows project.c's
``load_body`` step by step – first a dry run that checks every length (a damaged or cut file is refused before
anything is used), then the real read. A format newer than 16 is refused with a clear sentence: its fields are
unknown here.

What it holds (SLOOP 3 3.4.0): the tempo; 8 track records (SLOOP 3 uses 5: four synths and the drums, which the FM-1
shows as the 5th track and keeps as track index 3); each track's sound (engine, 48 values), mix, mute, grid pull
(QUANT) and current pattern; up to 16 patterns per track (the firmware uses 8) of 1–128 steps with up to 128 notes
(tick at 96 a step, length, note, velocity, condition, ratchet + ACCENT / SLIDE) and 32 parameter locks; the song (bars
where the patterns and mutes change); swing (global, per track), DIV and GATE per track, the sounds' preset numbers,
each pattern's own sound, the device's settings (HOME) and, from format 16, each track's pattern as last saved.

Timing for the export as in seq/seq.c ``play_range``: a step is 96 of the track's ticks, DIV makes the track's step
1/32 … 2 bars long; QUANT pulls a note towards the grid, SWING (global + track, at most 100) moves every second step
late by up to half a step (triplet DIVs are never swung); GATE scales the length (at least 4 ticks); ratchets play 2–4
hits in the step (each 3/4 of its part, quieter by 1/8 each); ACCENT plays 127; SLIDE holds on to the next note. The
song: each entry lasts until the next one's bar (4/4 bars of 16 sequencer steps); a change of any pattern starts them
all from the beginning, an entry that only changes mutes lets them run on.
Not modelled: conditions (FILL-only notes are left out, every other note is exported – chance and 1-of-N cycles
included), arpeggiator, harmony, strum, locks (listed, not played), the pattern sounds.
"""
from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from pathlib import Path

from . import projectfmt as PF
from .i18n import _, ngettext

MAGIC = 0x4A504247                 # "GBPJ"
VERSION = 16                       # the newest format this reader knows (SLOOP 3 3.4.0, app/project.c PVERSION)
SUFFIX = ".s3p"                    # the web editor's project file
PRJ_NTRK = 8                       # track records in a file
SEQ_NTRK = 5                       # SLOOP 3's tracks (the per-track blocks of formats 11 and 16 have 5)
PRJ_NPAT = 16                      # patterns a file may hold per track (the firmware plays 8)
SEQ_NPAT = 8
SEQ_MAXSTEPS = 128
SEQ_PATEV = 128
SEQ_LOCKS = 32
SEQ_SONG = 96
MAX_SONG_BARS = 999                # the song the view expands, at most (999 bars: over half an hour at 120 BPM; a song's
                                   # bars are 16 bits in the file, so a damaged one could ask for 65535)
SEQ_TPS = 96                       # ticks a step
GB_NPARAM = 48
PS_BYTES, PS_BYTES12, PS_DRUM_BYTES = 8 + GB_NPARAM, 7 + GB_NPARAM, 34
PRJ_HOME = 80
DRUM = 3                           # the drum track's index
SHOWN = (0, 1, 2, 4, 3)            # the tracks as the FM-1 (and the editor) shows them: synths 1–4, the drums 5th
GM_PAD = (36, 35, 38, 39, 42, 46, 44, 37, 40, 41, 48, 49, 51, 70, 63, 56)    # web/src/core.js: a pad's GM note
DIV_NAMES = ("1/32", "16T", "1/16", "8T", "1/8", "4T", "1/4", "1/2", "1BAR", "2BAR")
DIV_NUM = (2, 3, 1, 3, 1, 3, 1, 1, 1, 1)
DIV_DEN = (1, 2, 1, 4, 2, 8, 4, 8, 16, 32)
DIV_16 = 2
ALWAYS, FILL, NOFILL, CHANCE, CYCLE = 0, 1, 2, 0x40, 0x80
SLIDE, ACCENT = 0x10, 0x20
US_BASE, FB_BASE, UK_BASE = 1000, 2000, 3000      # a sound number: your sounds, the FM6 bank, a kit (fw/main.c)
S3BK = b"S3BK"


class S3FormatError(Exception):
    pass


_TABLES = None


def tables() -> dict:
    global _TABLES
    if _TABLES is None:
        try:
            _TABLES = json.loads((Path(__file__).parent / "data" / "sloop3_tables.json").read_text("utf-8"))
        except (OSError, ValueError):
            _TABLES = {"engines": [], "presets": [], "kits": [], "kinds": []}
    return _TABLES


def engine_name(e: int) -> str:
    names = tables()["engines"]
    return names[e] if 0 <= e < len(names) else f"ENGINE {e}"


def sound_name(k: int) -> str:
    """a sound number as the project keeps it (preset16 without the "edited" bit)"""
    if k >= UK_BASE:
        kits = tables()["kits"]
        return kits[k - UK_BASE] if k - UK_BASE < len(kits) else f"KIT {k - UK_BASE}"
    if k >= FB_BASE:
        return f"FM6 B{k - FB_BASE + 1}"
    if k >= US_BASE:
        return _("your sound {n}").format(n=k - US_BASE + 1)
    pres = tables()["presets"]
    return pres[k][0] if 0 <= k < len(pres) else f"#{k}"


def div_quarters(div: int) -> float:
    d = div if 0 <= div < len(DIV_NUM) else DIV_16
    return 0.25 * DIV_DEN[d] / DIV_NUM[d]


def cond_text(c: int) -> str:
    if c == ALWAYS:
        return ""
    if c == FILL:
        return "FILL"
    if c == NOFILL:
        return "!FILL"
    if (c & 0xC0) == CHANCE:
        return f"{(c & 15) * 10}%"
    if c & CYCLE:
        return f"{(c & 7) + 1}:{(c >> 3) & 7}"
    return str(c)


# ------------------------------------------------------------------------------------------------ the raw file ---
@dataclass
class Note:
    tick: int
    length: int
    note: int
    vel: int
    cond: int = ALWAYS
    ratchet: int = 1                  # hits 1..4 in the low bits, SLIDE 0x10, ACCENT 0x20

    @property
    def hits(self) -> int:
        return self.ratchet & 7 or 1


@dataclass
class Pat:
    track: int
    index: int
    steps: int = 16
    notes: list[Note] = field(default_factory=list)
    locks: list[tuple[int, int, int]] = field(default_factory=list)     # (step, param, value 0..255)


@dataclass
class TrackRec:
    engine: int = 0
    poly: int = 1
    unison: int = 1
    params: list[float] = field(default_factory=list)
    level: float = 0.0
    pan: float = 0.5
    send_dly: float = 0.0
    send_rev: float = 0.0
    mute: bool = False
    quant: int = 0
    cur: int = 0
    swing: int = 0
    div: int = DIV_16
    gate: int = 100
    preset: int = 0xFFFF              # format 13: the sound (bit 15: edited); 0xFFFF none


@dataclass
class SongEntry:
    bar: int
    mute: int
    pats: list[int]


@dataclass
class Raw:
    version: int
    size: int
    bpm: float
    tracks: list[TrackRec]
    patterns: dict                    # (track, pattern) -> Pat
    song: list[SongEntry]
    song_len: int
    swing: int = 0
    pattern_sounds: dict = field(default_factory=dict)     # (track, pattern) -> sound number (format 11+)
    kept: list = field(default_factory=list)                 # format 16: the tracks whose current pattern differs from
    trailing: int = 0                                        # its last saved one; bytes after the end (allowed)


class _R:
    def __init__(self, b: bytes, pos: int = 0, end: int | None = None):
        self.b, self.n, self.len = b, pos, len(b) if end is None else end

    def need(self, k: int):
        if self.n + k > self.len:
            raise S3FormatError(_("the file ends too early (cut off or damaged)"))

    def u8(self) -> int:
        self.need(1)
        v = self.b[self.n]
        self.n += 1
        return v

    def u16(self) -> int:
        self.need(2)
        v = self.b[self.n] | self.b[self.n + 1] << 8
        self.n += 2
        return v

    def u32(self) -> int:
        return self.u16() | self.u16() << 16

    def f32(self) -> float:
        self.need(4)
        v = struct.unpack_from("<f", self.b, self.n)[0]
        self.n += 4
        return 0.0 if v != v else v                      # (no NaN, as rf)

    def skip(self, k: int):
        self.need(k)
        self.n += k

    def rv(self) -> int:
        v = 0
        for k in range(0, 21, 7):
            b = self.u8()
            v |= (b & 0x7F) << k
            if not b & 0x80:
                return v
        raise S3FormatError(_("damaged note data"))


def _gdecode(c: int) -> int:
    v = c >> 2
    return v * SEQ_TPS if c & 3 == 0 else v * SEQ_TPS - 2 if c & 3 == 1 else v


def _unpack_notes(r: _R, n: int) -> list[Note]:
    """format 15: a pattern's notes packed (project.c ev_unpack)"""
    out = []
    pt, pv, pl, pn = 0, 100, SEQ_TPS - 2, 60
    for e in range(n):
        b = r.u8()
        note = r.u8() if b & 15 == 15 else pn + (b & 15) - 7
        t = pt + (0 if (b & 0x80) and e else _gdecode(r.rv()))
        vel = r.u8() if b & 0x40 else pv
        ln = _gdecode(r.rv()) if b & 0x20 else pl
        cond, rat = ALWAYS, 1
        if b & 0x10:
            cond, rat = r.u8(), r.u8()
        if not 0 <= note <= 127 or t > 0xFFFF or ln > 0xFFFF:
            raise S3FormatError(_("damaged note data"))
        pt, pv, pl, pn = t, vel, ln, note
        out.append(Note(t, ln, note, vel & 127, cond, _rat(rat)))
    return out


def _rat(ra: int) -> int:
    rn = ra & 7
    return (1 if rn < 1 else 4 if rn > 4 else rn) | (ra & 0x30)


def version_of(data: bytes) -> int | None:
    """the format number of a SLOOP 3 project file, None if it is none"""
    if len(data) >= 5 and struct.unpack_from("<I", data)[0] == MAGIC:
        return data[4]
    return None


def is_project(data: bytes) -> bool:
    return version_of(data) is not None


def _check(b: bytes, ver: int):
    """project.c's dry run: every length checked before anything is read"""
    np_ = GB_NPARAM if ver >= 12 else 24 if ver >= 5 else 16
    evb = 8 if ver >= 2 else 6
    r = _R(b, 5)
    r.skip(4)
    r.skip(PRJ_NTRK * (4 + 4 * np_ + 16 + 3))
    for _c in range(r.u16()):
        t, i, steps, n = r.u8(), r.u8(), r.u16(), r.u16()
        nl = r.u8() if ver >= 2 else 0
        if t >= PRJ_NTRK or i >= PRJ_NPAT or not 1 <= steps <= SEQ_MAXSTEPS or n > SEQ_PATEV or nl > SEQ_LOCKS:
            raise S3FormatError(_("a pattern with impossible sizes (damaged file)"))
        if ver >= 15:
            md, nb = r.u8(), r.u16()
            if md > 1 or (md == 0 and nb != 8 * n):
                raise S3FormatError(_("a pattern with impossible sizes (damaged file)"))
            r.need(nb)
            if md == 1:
                sub = _R(b, r.n, r.n + nb)
                _unpack_notes(sub, n)
                if sub.n != r.n + nb:
                    raise S3FormatError(_("damaged note data"))
            r.skip(nb + 3 * nl)
        else:
            r.skip(n * evb + 3 * nl)
    if ver >= 2:
        sn = r.u16()
        r.u16()
        if sn > SEQ_SONG:
            raise S3FormatError(_("a song with too many entries (damaged file)"))
        r.skip(sn * (3 + PRJ_NTRK))
    fixed = ((9 + PRJ_NTRK) if ver >= 3 else 0) + ((1 + PRJ_NTRK) if ver >= 4 else 0) + \
        (5 * PRJ_NTRK if ver >= 5 else 0) + ((4 + PRJ_NTRK) if ver >= 6 else 0) + \
        ((11 * PRJ_NTRK + 1) if ver >= 7 else 0) + (34 * PRJ_NTRK if ver >= 8 else 0) + \
        (2 * PRJ_NTRK if ver >= 9 else 0) + (8 if ver >= 10 else 0)
    r.skip(fixed)
    if ver >= 11:
        for t in range(SEQ_NTRK):
            m = r.u16()
            per = (PS_BYTES if ver >= 13 else PS_BYTES12 if ver >= 12 else 31) + (PS_DRUM_BYTES if t == DRUM else 0)
            r.skip(per * bin(m & 0xFFFF).count("1"))
    if ver >= 13:
        r.skip(PRJ_HOME + 2 * PRJ_NTRK + 8)
    if ver >= 14:
        r.skip(4)
    if ver >= 16:
        for t in range(SEQ_NTRK):
            fl = r.u8()
            if fl & 1:
                r.skip(PS_BYTES + (PS_DRUM_BYTES if t == DRUM else 0))
            if fl & 2:
                steps, n, nl = r.u16(), r.u16(), r.u8()
                if not 1 <= steps <= SEQ_MAXSTEPS or n > SEQ_PATEV or nl > SEQ_LOCKS:
                    raise S3FormatError(_("a pattern with impossible sizes (damaged file)"))
                r.skip(8 * n + 3 * nl)
    return r.n


def parse(data: bytes) -> Raw:
    """a SLOOP 3 project file -> Raw. S3FormatError (a translated sentence) if it is none, is damaged or is of a
    format newer than this reader knows."""
    b = bytes(data)
    if len(b) < 5 or struct.unpack_from("<I", b)[0] != MAGIC:
        raise S3FormatError(_("not a SLOOP 3 project"))
    ver = b[4]
    if ver < 1:
        raise S3FormatError(_("not a SLOOP 3 project"))
    if ver > VERSION:
        raise S3FormatError(_("project format {found} – made by a newer SLOOP 3 than the app knows (up to format "
                              "{known}, SLOOP 3 3.4.0). The app does not guess at it; the file itself is fine and "
                              "stays as it is.").format(found=ver, known=VERSION))
    end = _check(b, ver)
    np_ = GB_NPARAM if ver >= 12 else 24 if ver >= 5 else 16
    r = _R(b, 5)
    bpm = r.f32()
    tracks = []
    for _t in range(PRJ_NTRK):
        k = TrackRec(engine=r.u8(), poly=r.u8() or 1, unison=r.u8() or 1)
        r.u8()                                           # protect
        k.params = [min(1.0, max(0.0, r.f32())) for _i in range(np_)]
        k.level, k.pan, k.send_dly, k.send_rev = (min(1.0, max(0.0, r.f32())) for _i in range(4))
        mu, qu, cu = r.u8(), r.u8(), r.u8()
        k.mute, k.quant, k.cur = mu != 0, min(100, qu), cu if cu < SEQ_NPAT else 0
        tracks.append(k)
    pats = {}
    for _c in range(r.u16()):
        t, i = r.u8(), r.u8()
        p = Pat(t, i, r.u16())
        n = r.u16()
        nl = r.u8() if ver >= 2 else 0
        md, nb = (r.u8(), r.u16()) if ver >= 15 else (0, 0)
        if md == 1:
            sub = _R(b, r.n, r.n + nb)
            p.notes = _unpack_notes(sub, n)
            r.n += nb
        else:
            for _e in range(n):
                tick, ln, note, vel = r.u16(), r.u16(), r.u8() & 127, r.u8() & 127
                cond, ra = (r.u8(), r.u8()) if ver >= 2 else (ALWAYS, 1)
                p.notes.append(Note(tick, ln, note, vel, cond, _rat(ra)))
        p.locks = [(r.u8(), r.u8() & 63, r.u8()) for _l in range(nl)]
        if ver < 12:
            p.locks = []                                 # (locks of another sound model)
        p.notes.sort(key=lambda x: x.tick)
        if t < SEQ_NTRK and i < SEQ_NPAT:                # (a track or pattern the firmware does not have: passed over)
            pats[(t, i)] = p
    song, song_len = [], 0
    if ver >= 2:
        sn, song_len = r.u16(), r.u16()
        for _i in range(sn):
            bar, mute = r.u16(), r.u8()
            ps = [r.u8() for _t in range(PRJ_NTRK)]
            song.append(SongEntry(bar, mute, [x if x < SEQ_NPAT else 0 for x in ps[:SEQ_NTRK]]))
    raw = Raw(ver, len(b), bpm, tracks, pats, song, song_len, trailing=len(b) - end)
    if ver >= 3:
        r.u8()
        r.f32()
        r.f32()
        old = [r.u8() for _t in range(PRJ_NTRK)]          # format 3–12: a 7-bit preset, 0xFF none
        if ver < 12:
            old = [0xFF] * PRJ_NTRK
        if ver < 13:
            for k, v in zip(tracks, old):
                k.preset = 0xFFFF if v == 0xFF else (v & 0x7F) | (0x8000 if v & 0x80 else 0)
    if ver >= 4:
        raw.swing = min(100, r.u8())
        for k in tracks:
            k.swing = min(100, r.u8())
    if ver >= 5:
        r.skip(5 * PRJ_NTRK)                              # harmony
    if ver >= 6:
        r.skip(4 + PRJ_NTRK)                              # dust, duck, master filter, fade; track filters
    if ver >= 7:
        for k in tracks:
            dv, gt = r.u8(), r.u8()
            k.div = dv if dv < len(DIV_NAMES) else DIV_16
            k.gate = min(200, max(10, gt))
            r.skip(1 + 8)                                 # chorus send, arpeggiator
        r.u8()
    if ver >= 8:
        r.skip(34 * PRJ_NTRK)                             # the drums' tune, decay, mute
    if ver >= 9:
        r.skip(2 * PRJ_NTRK)
    if ver >= 10:
        r.skip(8)
    if ver >= 11:
        for t in range(SEQ_NTRK):
            m = r.u16()
            per = (PS_BYTES if ver >= 13 else PS_BYTES12 if ver >= 12 else 31) + (PS_DRUM_BYTES if t == DRUM else 0)
            for i in range(16):
                if m >> i & 1:
                    if ver >= 12 and i < SEQ_NPAT:
                        pre = b[r.n + 4] | b[r.n + 5] << 8 if ver >= 13 else b[r.n + 4]
                        if ver >= 13:
                            pre = pre - 0x10000 if pre >= 0x8000 else pre
                        else:
                            pre = pre - 0x100 if pre >= 0x80 else pre
                        if pre != -1:                     # (-1 none, k, -k - 2: k edited)
                            raw.pattern_sounds[(t, i)] = pre if pre >= 0 else -pre - 2
                    r.skip(per)
    if ver >= 13:
        r.skip(PRJ_HOME)
        for k in tracks:
            k.preset = r.u16()
        r.skip(8)
    if ver >= 14:
        r.skip(4)
    if ver >= 16:
        for t in range(SEQ_NTRK):
            fl = r.u8()
            if fl & 1:
                r.skip(PS_BYTES + (PS_DRUM_BYTES if t == DRUM else 0))
            if fl & 2:
                r.u16()
                n, nl = r.u16(), r.u8()
                r.skip(8 * n + 3 * nl)
                raw.kept.append(t)
    return raw


# ------------------------------------------------------------------------------------- into the project model ---
class S3Track(PF.Track):
    """a SLOOP 3 track in the project view's model (projectfmt.Track): index = the place the FM-1 shows it at"""

    def __init__(self, index: int, rec: TrackRec, internal: int):
        super().__init__(index=index, engine=rec.engine, preset=rec.preset)
        self.rec, self.internal = rec, internal

    @property
    def name(self) -> str:
        return _("Drums") if self.internal == DRUM else _("Track {n}").format(n=self.index + 1)

    @property
    def engine_name(self) -> str:
        return engine_name(self.engine)

    @property
    def preset_name(self) -> str:
        p = self.rec.preset
        if p == 0xFFFF:
            return ""
        return sound_name(p & 0x7FFF) + (" *" if p & 0x8000 else "")

    @property
    def level(self) -> int:
        return int(round(self.rec.level * 127))

    @property
    def pan(self) -> int:
        return max(-64, min(63, int(round((self.rec.pan - 0.5) * 128))))

    @property
    def mute(self) -> bool:
        return self.rec.mute

    @property
    def midi_channel(self) -> int:
        return 9 if self.internal == DRUM else self.index      # as the editor's MIDI out: synths 1–4, drums 10


def _timed(p: Pat, k: TrackRec, swing: int, drum: bool) -> list[PF.TimedNote]:
    """seq.c play_range for one loop: each note (and each ratchet hit) at its tick, as a fraction of its step"""
    plen = p.steps * SEQ_TPS
    sw = min(100, swing + k.swing)
    if DIV_NUM[k.div] == 3:
        sw = 0                                             # (triplets are never swung)
    out = []
    by_tick = sorted(n.tick for n in p.notes if n.tick < plen)
    for n in p.notes:
        if n.tick >= plen or n.cond == FILL:
            continue
        g = (n.tick + SEQ_TPS // 2) // SEQ_TPS * SEQ_TPS
        x = n.tick + (g - n.tick) * k.quant // 100
        if sw and (g // SEQ_TPS) & 1:
            x += sw * (SEQ_TPS // 2) // 100
        et = x % plen
        nr = n.hits
        gap = SEQ_TPS // nr
        ln = (n.length if n.length < gap else gap * 3 // 4) if nr > 1 else n.length
        if k.gate and k.gate != 100:
            ln = max(4, ln * k.gate // 100)
        if n.ratchet & SLIDE:                             # on into the next note (and a little past it)
            later = [(t - n.tick) % plen for t in by_tick if (t - n.tick) % plen]
            ln = min(later) + SEQ_TPS // 8 if later else n.length
        vel = 127 if n.ratchet & ACCENT else n.vel
        note = GM_PAD[n.note & 15] if drum else n.note
        if drum:
            ln = SEQ_TPS // 2
        for h in range(nr):
            t = (et + h * gap) % plen
            out.append(PF.TimedNote(t // SEQ_TPS, (t % SEQ_TPS) / SEQ_TPS, ln / SEQ_TPS, note,
                                    max(1, vel * (8 - h) // 8 if h else vel), drum))
    return out


def _pattern(p: Pat | None, index: int, k: TrackRec, swing: int, drum: bool) -> PF.Pattern:
    steps = p.steps if p else 16
    pat = PF.Pattern(index=index, steps=[PF.Step() for _i in range(steps)], length=steps, div=k.div,
                     swing=min(100, swing + k.swing), gate=k.gate, quarters=div_quarters(k.div),
                     div_label=DIV_NAMES[k.div], exact=True)
    if p:
        pat.recorded = _timed(p, k, swing, drum)
        for nt in pat.recorded:                          # the overview the piano roll draws
            st = pat.steps[min(steps - 1, nt.step)]
            st.time, st.recorded = PF.ST_NOTE, True
            if len(st.notes) < 4 and nt.note not in st.notes:
                st.notes.append(nt.note)
            st.vel = max(st.vel, nt.vel)
    return pat


def to_project(raw: Raw) -> PF.Project:
    prj = PF.Project(format=f"SLOOP 3 (GBPJ {raw.version})", size=raw.size, bpm=_bpm(raw.bpm), swing=raw.swing)
    order = list(SHOWN)
    for pos, t in enumerate(order):
        k = raw.tracks[t]
        tr = S3Track(pos, k, t)
        tr.patterns = [_pattern(raw.patterns.get((t, i)), i, k, raw.swing, t == DRUM) for i in range(SEQ_NPAT)]
        tr.active = k.cur
        prj.tracks.append(tr)
        for i in range(SEQ_NPAT):
            p = raw.patterns.get((t, i))
            for step, param, value in (p.locks if p else []):
                prj.motion.append((pos, step, param, value))
            sid = raw.pattern_sounds.get((t, i))
            if sid is not None and (k.preset == 0xFFFF or sid != k.preset & 0x7FFF):
                prj.sounds.append(PF.SongSound(sound_name(sid), k.engine, sid))
                prj.pattern_sounds[(pos, i)] = len(prj.sounds)
    prj.tracks_in_use = SEQ_NTRK
    # the song: entry i from its bar to the next one's (the last: to the song's length)
    total_bars, song_cut = 0, False
    for i, e in enumerate(raw.song):
        nxt = raw.song[i + 1].bar if i + 1 < len(raw.song) else raw.song_len
        bars = max(1, nxt - e.bar)
        if total_bars + bars > MAX_SONG_BARS:
            bars, song_cut = MAX_SONG_BARS - total_bars, True
            if bars <= 0:
                break
        total_bars += bars
        changed = i == 0 or any(e.pats[t] != raw.song[i - 1].pats[t] for t in range(SEQ_NTRK))
        banks = []
        for pos, t in enumerate(order):
            if e.mute >> t & 1:
                banks.append(PF.CHAIN_REST)
            elif changed or not prj.song or prj.song[-1].banks[pos] == PF.CHAIN_REST:
                banks.append(e.pats[t])           # (after a mute: from its start – the FM-1 let it run on, muted)
            else:
                banks.append(PF.CHAIN_HOLD)
        prj.song.append(PF.SongRow(repeat=bars, banks=banks, quarters=4.0 * bars))
        if song_cut:
            break
    if song_cut:
        prj.warnings.append(_("The song is longer than {n} bars (the file says {bars}) – only its first {n} bars are "
                              "shown, played and exported.").format(n=MAX_SONG_BARS, bars=max(raw.song_len, max(
                                  (x.bar for x in raw.song), default=0))))
    notes = []
    locks = len(prj.motion)
    if locks:
        notes.append(ngettext("{n} parameter lock (listed in the JSON export, not played)",
                              "{n} parameter locks (listed in the JSON export, not played)", locks).format(n=locks))
    if raw.kept:
        notes.append(_("tracks {tracks}: the playing pattern has changes that are not saved into the pattern "
                       "yet – shown as it plays").format(
            tracks=", ".join(str(SHOWN.index(t) + 1) for t in raw.kept)))
    if raw.trailing:
        notes.append(ngettext("{n} byte after the end of the project was ignored",
                              "{n} bytes after the end of the project were ignored", raw.trailing).format(n=raw.trailing))
    prj.note = "; ".join(notes)
    prj.ok = True
    return prj


def _bpm(v: float):
    v = round(v, 1)
    return int(v) if v == int(v) else v


def decode(data: bytes) -> PF.Project:
    """a SLOOP 3 project file -> projectfmt.Project (never raises; ok False with the reason in warnings)"""
    try:
        return to_project(parse(data))
    except (S3FormatError, IndexError, ValueError, struct.error) as e:      # (never raises: a damaged file says so)
        ver = version_of(bytes(data))
        prj = PF.Project(format=f"SLOOP 3 (GBPJ {ver})" if ver is not None else "SLOOP 3", size=len(data))
        prj.warnings.append(str(e))
        return prj


# ------------------------------------------------------------------------------------------------ containers ---
def from_s3b(data: bytes) -> list[tuple[int, int, bytes]]:
    """the web editor's backup (.s3b, files.js backupAll: "S3BK", version 1, the number of parts, 0, 0; then each part:
    kind (0 a project, 1 your sounds), slot, length u32, the bytes) -> [(kind, slot, bytes)]. S3FormatError if it is
    none or damaged."""
    if len(data) < 8 or data[:4] != S3BK or data[4] != 1:
        raise S3FormatError(_("not a SLOOP 3 backup file (.s3b)"))
    out, i = [], 8
    for _k in range(data[5]):
        if i + 6 > len(data):
            raise S3FormatError(_("the file ends too early (cut off or damaged)"))
        kind, slot, n = data[i], data[i + 1], struct.unpack_from("<I", data, i + 2)[0]
        i += 6
        if i + n > len(data):
            raise S3FormatError(_("the file ends too early (cut off or damaged)"))
        out.append((kind, slot, bytes(data[i:i + n])))
        i += n
    return out


def to_s3b(parts: list[tuple[int, int, bytes]]) -> bytes:
    """the other way round (for tests and fixtures; the app writes .s3p files)"""
    out = bytearray(S3BK + bytes([1, len(parts), 0, 0]))
    for kind, slot, d in parts:
        out += bytes([kind, slot]) + struct.pack("<I", len(d)) + d
    return bytes(out)


def projects_in(path: Path) -> list[tuple[str, bytes]]:
    """the SLOOP 3 projects in a file: .s3p, .s3b or the app's sloop3-backup (.json) -> [(title, bytes)]"""
    path = Path(path)
    raw = path.read_bytes()
    if raw[:4] == S3BK:
        return [(_("Project in the editor backup") + (f" {slot + 1}" if slot else ""), d)
                for kind, slot, d in from_s3b(raw) if kind == 0 and d]
    if version_of(raw) is not None:
        return [(path.stem, raw)]
    try:
        f = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise S3FormatError(_("not a SLOOP 3 project"))
    from . import sloop3bk as BK
    try:
        objs = BK.objects(f)
    except BK.Sloop3BackupError as e:
        raise S3FormatError(str(e))
    out = []
    if objs.get(BK.PROJECT):
        out.append((_("Project saved on the FM-1"), objs[BK.PROJECT]))
    if objs.get(BK.NOW):
        out.append((_("Project as it was at that moment"), objs[BK.NOW]))
    return out


def is_sloop3_backup(path: Path) -> bool:
    """a file the app's sloop3-backup format (without reading the whole thing twice for big files)"""
    try:
        head = Path(path).read_bytes()[:200]
    except OSError:
        return False
    return b'"sloop3-backup"' in head


def summary(raw: Raw) -> dict:
    """name / date-free facts for the library list: tempo, patterns used, the song's length in bars"""
    used = sum(1 for p in raw.patterns.values() if p.notes)
    return {"bpm": _bpm(raw.bpm), "patterns": used, "song_bars": raw.song_len if raw.song else 0,
            "format": raw.version}


def length_bars(raw: Raw) -> float:
    """how long the project plays: the song's bars, else the longest current pattern in 4/4 bars"""
    if raw.song and raw.song_len:
        return float(raw.song_len)
    best = 0.0
    for t in range(SEQ_NTRK):
        k = raw.tracks[t]
        p = raw.patterns.get((t, k.cur))
        if p and p.notes:
            best = max(best, p.steps * div_quarters(k.div) / 4)
    return round(best, 2)
