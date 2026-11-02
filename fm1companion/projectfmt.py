# SPDX-License-Identifier: GPL-3.0-only
"""Melodee-Projekte lesen (Backup-Objekte 0, 2–5, 64–91 und .fm1proj).

Das Format ist aus den Quellen von Melodee 1.6.0-rc.7 abgeleitet (firmware/src/project.c,
pattern_store.c, project_stream.c, step_snapshot.h) und an Bytes geprüft, die Melodees eigener
Firmware-Code geschrieben hat (tools/melodee_fixtures, tests/fixtures/musik). Die Offsets stehen in
docs/melodee-project-format.md.

Schichten:
  HPJ1   kompaktes Abbild: 32-Byte-Kopf, nur die belegten 256-Byte-Seiten   -> 27 904-Byte-Datensatz
  FBK?   Bank-Container (FBKK, FBKL … FBKQ): Kopf, FUN-Datensatz, Bank-Ende mit den übrigen Patterns, Song
  FUN?   der Klang-/Aktiv-Pattern-Datensatz (FUN16 … FUN22, 13 212 Byte)
Ältere Formate (FUN7/FUN8/FBK9) werden nach der Protokoll-Doku gelesen, aber nicht gegen Firmware-Code geprüft.

`decode()` wirft nie: Unbekanntes landet als Hinweis in `Project.warnings` ("unbekannter Teil").
"""
from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------- Konstanten (rc.7, layout.json) ---
NSTEP = 64
NPART = 4
NTRK = 8
NPAT = 16
NPAT_V1 = 8
NPAT_MIDI = 8
RECORD_MAX = 1024
BANK_STORE_SIZE = 27904
PROJ_STORE_SIZE = 13212
BANK_ACTIVE_OFF = 8 + PROJ_STORE_SIZE           # 13220
BANK_TAIL_SIZE = BANK_STORE_SIZE - BANK_ACTIVE_OFF
PJ_HEAD = 32
PJ_TPAGES = (BANK_TAIL_SIZE + 255) // 256       # 58
PJ_SPAGES = (BANK_ACTIVE_OFF + 255) // 256      # 52

# FUN22 (Offsets im FUN-Datensatz)
PROJ_REC_OFF = 4192
PROJ_REC_DIR = 13152
PROJ_NAME_OFF = 13196
PROJ_FM6_OFF = 3104
PROJ_MPC_OCT_WIRE = 3096

# Bank-Ende (Offsets relativ zum Beginn des Bank-Endes)
FBKP = dict(npat=16, timing=8, chain=520, motion=648, fn=712, rec=780, recn=176, trk=956, steps=1456, mask=2)
FBKL = dict(npat=8, timing=8, chain=264, motion=392, fn=456, rec=524, recn=44, trk=568, steps=1068, mask=1)
SONG_BYTES = 580          # count, loop, sounds, 0, 64 × (repeat, 8 Bänke)
FX_BYTES = 768            # 64 × 4 Punch-Effekte × 3 (chain_fx_t: fx 1..27 = Effekt + 1, start, length in Steps)
FX_EVENTS = 4
# Melodee 1.6 firmware/src/punch.c PUNCH_NAME (Effekt-ID = Index; im Song gespeichert als Index + 1)
PUNCH_NAMES = ["LOOP 1/4", "LOOP 1/8", "LOOP 1/16", "LOOP 1/32", "STUTTER", "REVERSE", "STOP", "HALF",
               "LOW PASS", "HIGH PASS", "PHONE", "CRUSH", "ALIAS", "GATE", "ECHO", "WOBBLE",
               "KILL LOW", "KILL MID", "KILL HIGH", "REWIND", "OCT UP", "FLANGER", "AUTO-PAN", "FREEZE", "LOOP 1/8T",
               "MONO", "FADE OUT"]
SOUND_HEAD = 516
SOUND_SIZE = 302
P_COUNT_NOW = 124

MAGIC_HPJ1 = 0x314A5048
BANK_MAGICS = {0x4B4B4246: "FBKK", 0x4C4B4246: "FBKL", 0x4D4B4246: "FBKM", 0x4E4B4246: "FBKN",
               0x4F4B4246: "FBKO", 0x504B4246: "FBKP", 0x514B4246: "FBKQ"}
FUN_MAGICS = {0x46554E49: ("FUN22", 8), 0x46554E48: ("FUN21", 8), 0x46554E47: ("FUN20", 8), 0x46554E46: ("FUN19", 8),
              0x46554E45: ("FUN18", 8), 0x46554E44: ("FUN17", 9), 0x46554E43: ("FUN16", 9)}
LEGACY_MAGICS = {0x37554E46: "FUN7", 0x38554E46: "FUN8", 0x394B4246: "FBK9"}

# Parameter-IDs (gleich in allen gelesenen Formaten, da neue Parameter nur vor P_E0 angehängt werden)
P_LEVEL, P_ROOT, P_SCALE, P_QUANT, P_TRANS = 0, 25, 26, 27, 28
P_SLEN, P_SDIV, P_SSWING, P_SGATE = 29, 30, 31, 32
P_VOICE, P_PAN, P_MUTE = 37, 39, 40
P_AMODE = 17
P_CHRD = 81
G_BPM, G_SWING = 0, 1

ST_NOTE, ST_TIE, ST_REST = 0, 1, 2
CHAIN_REST, CHAIN_HOLD = NPAT, NPAT + 1

LANES = ["KICK", "SNARE", "CLAP", "HAT CL", "HAT OP", "TOM", "RIM", "BELL"]
LANE_GM = [36, 38, 39, 42, 46, 45, 37, 56]
DIV_QUARTERS = [1.0, 0.5, 0.25, 0.125, 1 / 3, 1 / 6, 2.0, 4.0, 8.0, 16.0]   # 1/4 1/8 1/16 1/32 8T 16T 1/2 1/1 2BAR 4BAR
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

_TABLES = None


def tables() -> dict:
    """Parameter-, Engine- und Preset-Tabellen von Melodee 1.6.0-rc.7 (aus dem Firmware-Code erzeugt)."""
    global _TABLES
    if _TABLES is None:
        try:
            _TABLES = json.loads((Path(__file__).parent / "data" / "melodee_tables.json").read_text("utf-8"))
        except (OSError, ValueError):
            _TABLES = {"params": [], "globals": [], "engines": []}
    return _TABLES


def note_name(n: int) -> str:
    return f"{NOTE_NAMES[n % 12]}{n // 12 - 1}"


def engine_name(e: int) -> str:
    eng = tables()["engines"]
    if 0 <= e < len(eng) and eng[e]["name"] not in ("-", ""):
        return eng[e]["name"]
    return "(alte Engine)" if 0 <= e < 22 else f"Engine {e}"


def preset_name(engine: int, preset: int) -> str:
    eng = tables()["engines"]
    if 0 <= engine < len(eng) and 0 <= preset < len(eng[engine]["presets"]):
        return eng[engine]["presets"][preset]
    return ""


def param_label(pid: int) -> str:
    p = tables()["params"]
    return p[pid]["label"] if 0 <= pid < len(p) else f"P{pid}"


def enum_name(pid: int, value: int) -> str:
    p = tables()["params"]
    if 0 <= pid < len(p) and p[pid]["names"]:
        i = value - p[pid]["min"]
        if 0 <= i < len(p[pid]["names"]):
            return p[pid]["names"][i]
    return str(value)


class FormatError(Exception):
    pass


# --------------------------------------------------------------------------------- Datenmodell ---
@dataclass
class Step:
    notes: list[int] = field(default_factory=list)   # 0..4 MIDI-Noten
    time: int = ST_REST                              # 0 NOTE, 1 TIE, 2 REST
    accent: bool = False
    slide: bool = False
    recorded: bool = False                           # Übersicht aufgenommener Noten (siehe Pattern.recorded)
    vel: int = 0                                     # 0 = Standard (96 bei Drum-Hits)
    hit: int = 0                                     # Bit l: Drum-Lane l schlägt an
    acc: int = 0                                     # Akzente der Lanes (ohne Hits: aufgenommenes Gate 1..255)
    chance: int = 100                                # 0..100 %

    @property
    def empty(self) -> bool:
        return self.time == ST_REST and not self.hit

    @property
    def flags(self) -> int:
        return (1 if self.accent else 0) | (2 if self.slide else 0)

    @classmethod
    def from_bits(cls, b: bytes) -> "Step":
        w0, w1 = struct.unpack_from("<II", b)
        code = (w0 >> 28) & 7
        notes = [(w0 >> (7 * i)) & 127 for i in range(4)]
        n = code if code < 5 else 0
        time = ST_NOTE if code < 5 else ST_TIE if code == 5 else ST_REST
        prob = w1 >> 25
        if code == 7 or prob > 101:
            raise FormatError("ungültiger Step")
        return cls(notes=notes[:n], time=time, accent=bool(w1 & 1), slide=bool(w1 & 2), recorded=bool(w0 >> 31),
                   vel=(w1 >> 2) & 127, hit=(w1 >> 9) & 255, acc=(w1 >> 17) & 255,
                   chance=100 if prob == 0 else prob if prob <= 100 else 0)

    @classmethod
    def from_nine(cls, b: bytes) -> "Step":
        """FUN7/FUN8/FUN16/FUN17: 4 Noten, n | time << 3 | flags << 5 | recorded << 7, vel, hit, acc, chance."""
        meta = b[4]
        n, time = meta & 7, (meta >> 3) & 3
        if n > 4 or time > 2 or b[8] > 101:
            raise FormatError("ungültiger Step")
        prob = b[8]
        return cls(notes=list(b[:4])[:n] if time == ST_NOTE else [], time=time, accent=bool(meta & 32),
                   slide=bool(meta & 64), recorded=bool(meta & 128), vel=b[5] & 127, hit=b[6], acc=b[7],
                   chance=100 if prob == 0 else prob if prob <= 100 else 0)


@dataclass
class Pattern:
    index: int
    steps: list[Step] = field(default_factory=lambda: [Step() for _ in range(NSTEP)])
    length: int = 16       # LEN (Steps)
    div: int = 2           # DIV-Index (2 = 1/16)
    swing: int = 0
    gate: int = 64
    recorded: list["TimedNote"] = field(default_factory=list)

    @property
    def used(self) -> bool:
        return any(not s.empty for s in self.steps) or bool(self.recorded)

    @property
    def step_quarters(self) -> float:
        return DIV_QUARTERS[self.div] if 0 <= self.div < len(DIV_QUARTERS) else 0.25

    @property
    def has_drums(self) -> bool:
        return any(s.hit for s in self.steps)

    @property
    def note_count(self) -> int:
        return sum(len(s.notes) for s in self.steps if s.time == ST_NOTE) + sum(bin(s.hit).count("1") for s in self.steps)


@dataclass
class TimedNote:
    """eine aufgenommene Note mit Originaltiming (FUN13+): Beginn im Step + Bruchteil, Dauer in Steps."""
    step: int
    onset: float           # 0..1 des (geswingten) Steps
    duration: float        # in Steps
    note: int
    vel: int


@dataclass
class Track:
    index: int
    engine: int = 0
    preset: int = 0
    params: list[int] = field(default_factory=list)
    patterns: list[Pattern] = field(default_factory=list)
    active: int = 0
    user_slot: int = 0                  # gemeinsame Bibliothek: Platz + 1 (U,3-Herkunft), 0 = keiner
    midi_only: bool = False
    in_use: bool = True
    fm6: bytes | None = None             # gepackter FM6-Patch (128 Byte) der Spur, falls im Projekt lesbar

    def p(self, pid: int, default: int = 0) -> int:
        return self.params[pid] if 0 <= pid < len(self.params) else default

    @property
    def name(self) -> str:
        return f"Spur {self.index + 1}"

    @property
    def engine_name(self) -> str:
        return "MIDI" if self.midi_only and self.engine in (0, 21) else engine_name(self.engine)

    @property
    def preset_name(self) -> str:
        if self.preset in (0xFF, 0xFE):
            return "Grundklang"
        return preset_name(self.engine, self.preset)

    @property
    def level(self) -> int:
        return self.p(P_LEVEL, 100)

    @property
    def pan(self) -> int:
        return self.p(P_PAN, 0)

    @property
    def mute(self) -> bool:
        return bool(self.p(P_MUTE, 0))

    @property
    def midi_channel(self) -> int:
        """0-basiert. Parts: Spur 1–4 = Kanal 1–4 (Melodee ROUT CH1-4); MIDI-Spuren: ihr Kanal-Parameter (E1)."""
        if self.midi_only and len(self.params) > 116:
            ch = self.params[116]
            if 1 <= ch <= 16:
                return ch - 1
        return self.index % 16


@dataclass
class Punch:
    """ein zeitgesteuerter Punch-Effekt im Song (Melodee 1.6, chain_fx_t): Steps start .. start+length-1"""
    fx: int                   # 0..26 (PUNCH_NAMES)
    start: int
    length: int

    @property
    def name(self) -> str:
        return PUNCH_NAMES[self.fx] if 0 <= self.fx < len(PUNCH_NAMES) else f"FX {self.fx + 1}"

    @property
    def text(self) -> str:
        """„REVERSE 5–8“ (Steps 1-basiert, wie START/END am Gerät)"""
        a = self.start + 1
        return f"{self.name} {a}–{a + max(1, self.length) - 1}" if self.length > 1 else f"{self.name} {a}"


@dataclass
class SongSound:
    """Preset-Kopie für einen Song-Block oder ein Pattern (Melodee 1.6, song_sound_t) – nur zum Anzeigen"""
    name: str
    engine: int
    preset: int

    @property
    def engine_name(self) -> str:
        return engine_name(self.engine)


@dataclass
class SongRow:
    repeat: int
    banks: list[int]          # je Spur: Pattern-Index, CHAIN_REST oder CHAIN_HOLD
    sounds: list[int] = field(default_factory=list)    # je Spur: Song-Block-Preset (Project.sounds, 1-basiert), 0 = keins
    punches: list[Punch] = field(default_factory=list)


@dataclass
class Project:
    format: str = "unbekannt"
    container: str = ""
    name: str = ""
    bpm: int = 120
    swing: int = 0
    globals: list[int] = field(default_factory=list)
    tracks: list[Track] = field(default_factory=list)
    song: list[SongRow] = field(default_factory=list)
    song_loop: bool = False
    sounds: list[SongSound] = field(default_factory=list)        # Preset-Kopien (Song-Blöcke, Patterns)
    pattern_sounds: dict = field(default_factory=dict)           # (Spur, Pattern) -> Preset-Kopie (1-basiert)
    motion: list[tuple[int, int, int, int]] = field(default_factory=list)   # (Spur, Step, Parameter, Wert)
    selected: int = 0
    tracks_in_use: int = NPART
    warnings: list[str] = field(default_factory=list)
    size: int = 0
    ok: bool = False
    hash_ok: bool | None = None

    @property
    def used_tracks(self) -> list[Track]:
        return [t for t in self.tracks if t.in_use]

    def summary(self) -> str:
        if not self.ok:
            return f"{self.format}: " + ("; ".join(self.warnings) or "nicht lesbar")
        used = sum(1 for t in self.tracks for p in t.patterns if p.used)
        name = f"„{self.name}“, " if self.name else ""
        song = f", Song mit {len(self.song)} Abschnitten" if self.song else ""
        n_p = sum(len(r.punches) for r in self.song)
        extra = (f", {n_p} Punch-Effekte" if n_p else "") + (f", {len(self.sounds)} Song-/Pattern-Presets"
                                                              if self.sounds else "")
        return f"{name}{self.bpm} BPM, {len(self.used_tracks)} Spuren, {used} Patterns belegt{song}{extra}"

    def sound_name(self, sid: int) -> str:
        return self.sounds[sid - 1].name if 0 < sid <= len(self.sounds) else f"Preset {sid}"

    def resolved_sound(self, row: int, track: int) -> tuple[int, str]:
        """welcher Klang spielt? Reihenfolge wie Melodee 1.6: Song-Block-Preset, dann Pattern-Preset, dann der
        Klang der Spur. -> (Preset-Kopie oder 0, "block"|"pattern"|"")"""
        if not 0 <= row < len(self.song):
            return 0, ""
        while row > 0 and track < len(self.song[row].banks) and self.song[row].banks[track] == CHAIN_HOLD:
            row -= 1                                     # fortgesetzter Block: der Klang seines Anfangs
        r = self.song[row]
        sid = r.sounds[track] if track < len(r.sounds) else 0
        if sid:
            return sid, "block"
        b = r.banks[track] if track < len(r.banks) else CHAIN_REST
        sid = self.pattern_sounds.get((track, b), 0) if b < NPAT else 0
        return (sid, "pattern") if sid else (0, "")

    def to_dict(self) -> dict:
        def step(s: Step):
            return {"notes": s.notes, "time": s.time, "accent": s.accent, "slide": s.slide, "vel": s.vel,
                    "hit": s.hit, "acc": s.acc, "chance": s.chance}
        return {
            "format": self.format, "container": self.container, "name": self.name, "bpm": self.bpm,
            "swing": self.swing, "selected": self.selected, "warnings": self.warnings,
            "tracks": [{"track": t.index + 1, "engine": t.engine, "engine_name": t.engine_name, "preset": t.preset,
                        "preset_name": t.preset_name, "user_slot": t.user_slot, "level": t.level, "pan": t.pan,
                        "mute": t.mute, "midi_only": t.midi_only, "active_pattern": t.active + 1,
                        "patterns": [{"pattern": p.index + 1, "length": p.length, "div": p.div, "swing": p.swing,
                                      "gate": p.gate,
                                      "steps": {str(i): step(s) for i, s in enumerate(p.steps) if not s.empty},
                                      "recorded": [vars(n) for n in p.recorded]}
                                     for p in t.patterns if p.used]}
                       for t in self.tracks if t.in_use],
            "song": [{"repeat": r.repeat, "patterns": r.banks,
                      **({"presets": r.sounds} if any(r.sounds) else {}),
                      **({"punches": [vars(x) | {"name": x.name} for x in r.punches]} if r.punches else {})}
                     for r in self.song], "song_loop": self.song_loop,
            "presets": [vars(x) for x in self.sounds],
            "pattern_presets": [{"track": k + 1, "pattern": b + 1, "preset": v}
                                for (k, b), v in sorted(self.pattern_sounds.items())],
            "motion": [list(m) for m in self.motion]}


# ------------------------------------------------------------------------------------ Lesen ---
def fnv(data: bytes, h: int = 2166136261) -> int:
    for b in data:
        h = ((h ^ b) * 16777619) & 0xFFFFFFFF
    return h


def _bits_get(b: bytes, pos: int, n: int) -> tuple[int, int]:
    v = 0
    for i in range(n):
        v |= ((b[(pos + i) >> 3] >> ((pos + i) & 7)) & 1) << i
    return v, pos + n


def expand_hpj1(data: bytes) -> bytes:
    """kompaktes HPJ1-Abbild -> der 27 904-Byte-Datensatz (Löcher = Nullen)."""
    magic, length, *maps = struct.unpack_from("<IIIIII", data)
    if magic != MAGIC_HPJ1 or length != BANK_STORE_SIZE:
        raise FormatError("kein HPJ1-Abbild")
    bitmap = maps[0] | maps[1] << 32 | maps[2] << 64 | maps[3] << 96
    out = bytearray(BANK_STORE_SIZE)
    at = PJ_HEAD
    for p in range(PJ_TPAGES):                          # das Bank-Ende T
        if bitmap >> p & 1:
            rel = p * 256
            k = min(256, BANK_TAIL_SIZE - rel)
            if at + k > len(data):
                raise FormatError("HPJ1-Abbild zu kurz")
            out[BANK_ACTIVE_OFF + rel:BANK_ACTIVE_OFF + rel + k] = data[at:at + k]
            at += k
    for p in range(PJ_SPAGES):                          # Kopf + FUN-Datensatz S
        if bitmap >> (64 + p) & 1:
            rel = p * 256
            k = min(256, BANK_ACTIVE_OFF - rel)
            if at + k > len(data):
                raise FormatError("HPJ1-Abbild zu kurz")
            out[rel:rel + k] = data[at:at + k]
            at += k
    return bytes(out)


def _new_tracks(n_parts_banks=NPAT) -> list[Track]:
    out = []
    for k in range(NTRK):
        npat = n_parts_banks if k < NPART else NPAT_MIDI
        out.append(Track(k, patterns=[Pattern(b) for b in range(npat)], midi_only=k >= NPART))
    return out


def _decode_fun(prj: Project, r: bytes, steps8: bool, np_: int, fx_in_wire: bool = False):
    """FUN16..FUN22: Globals, je Part Parameter, Engine, Preset und das aktive Pattern."""
    g = list(struct.unpack_from("<27h", r, 8))
    prj.globals = g
    prj.bpm, prj.swing = g[G_BPM], g[G_SWING]
    prj.selected, prj.tracks_in_use = r[62], r[63] or NPART
    banks = r[65] | r[67] << 8
    pos = 68
    stride = 8 if steps8 else 9
    for t in range(NPART):
        tr = prj.tracks[t]
        vals = []
        for i in range(np_):
            if fx_in_wire and 96 <= i <= 99:              # FUN17: FX-Felder liegen im Global-Bereich
                vals.append(0)
                continue
            vals.append(r[pos] - 64)
            pos += 1
        # nach Anzahl abbilden: die ersten np-8 in Reihenfolge, die letzten 8 = Engine-Parameter
        defaults = [p["def"] for p in tables()["params"]] or [0] * P_COUNT_NOW
        params = [vals[i] if i < np_ - 8 else defaults[i] for i in range(P_COUNT_NOW - 8)] + vals[np_ - 8:]
        tr.params = params
        tr.engine, tr.preset = r[pos], r[pos + 1]
        pos += 2
        tr.active = ((banks >> (3 * t)) & 7) | ((banks >> (12 + t)) & 1) << 3
        if tr.active >= len(tr.patterns):
            raise FormatError("aktives Pattern außerhalb")
        pat = tr.patterns[tr.active]
        for i in range(NSTEP):
            chunk = r[pos:pos + stride]
            pat.steps[i] = Step.from_bits(chunk) if steps8 else Step.from_nine(chunk)
            pos += stride
        pat.length, pat.div, pat.swing, pat.gate = (tr.p(P_SLEN, 16), tr.p(P_SDIV, 2), tr.p(P_SSWING, 0),
                                                    tr.p(P_SGATE, 64))
    # Song (alt, 16 Zeilen) und Motion
    count = r[pos]
    legacy = [(r[pos + 4 + 2 * i], r[pos + 5 + 2 * i]) for i in range(16)]
    pos += 36
    mcount = r[pos]
    for i in range(min(mcount, 64)):
        place, param, value = struct.unpack_from("<BBh", r, pos + 4 + 4 * i)
        prj.motion.append((place >> 6, place & 63, param, value))
    pos += 260
    if r[pos:pos + 2] == b"U\x03":
        for t in range(NPART):
            prj.tracks[t].user_slot = struct.unpack_from("<H", r, pos + 2 + 2 * t)[0]
    _read_fm6(prj, r, PROJ_FM6_OFF)
    raw_name = r[PROJ_NAME_OFF:PROJ_NAME_OFF + 12].split(b"\0")[0]
    if raw_name and all(32 <= c <= 126 for c in raw_name):
        prj.name = raw_name.decode("ascii").upper()
    return count, legacy


def _read_fm6(prj: Project, r: bytes, off: int):
    """die vier FM6-Patches (je 128 Byte, 7 Bit) – nur übernehmen, wenn sie plausibel sind (für das Vorhören)."""
    if len(r) < off + NPART * 128:
        return
    for t in range(NPART):
        b = bytes(r[off + 128 * t: off + 128 * (t + 1)])
        if all(x <= 127 for x in b) and b[110] <= 31 and any(b):
            prj.tracks[t].fm6 = b


def _records(prj: Project, r: bytes, counts: list[int], owners: list[int]):
    """aufgenommene Noten: Owner für Owner hintereinander ab PROJ_REC_OFF."""
    at = 0
    for owner, count in zip(owners, counts):
        if count > RECORD_MAX - at:
            raise FormatError("zu viele aufgenommene Noten")
        k, b = divmod(owner, NPAT)
        for _ in range(count):
            v = r[PROJ_REC_OFF + at * 8:PROJ_REC_OFF + at * 8 + 8]
            at += 1
            on, pos = _bits_get(v, 0, 16)
            dur, pos = _bits_get(v, pos, 16)
            note, pos = _bits_get(v, pos, 7)
            vel, pos = _bits_get(v, pos, 7)
            step, pos = _bits_get(v, pos, 8)
            exp, pos = _bits_get(v, pos, 3)
            if k < len(prj.tracks) and b < len(prj.tracks[k].patterns) and vel:
                prj.tracks[k].patterns[b].recorded.append(
                    TimedNote(step & 63, on / 65536, dur * (1 << exp) / 65536, note, vel))


def _counts(buf: bytes, n: int) -> list[int]:
    out, pos = [], 0
    for _ in range(n):
        v, pos = _bits_get(buf, pos, 11)
        out.append(v)
    return out


def _decode_bank(prj: Project, rec: bytes):
    magic, size = struct.unpack_from("<II", rec)
    kind = BANK_MAGICS[magic]
    prj.container = kind
    if size != BANK_STORE_SIZE:
        raise FormatError("Größe im Kopf stimmt nicht")
    stored = struct.unpack_from("<I", rec, BANK_STORE_SIZE - 4)[0]
    prj.hash_ok = stored == fnv(rec[:BANK_STORE_SIZE - 4])
    if not prj.hash_ok:
        prj.warnings.append("Prüfsumme des Projekts stimmt nicht – Daten evtl. beschädigt")
    fun = rec[8:8 + PROJ_STORE_SIZE]
    fmagic = struct.unpack_from("<I", fun)[0]
    if fmagic not in FUN_MAGICS:
        raise FormatError(f"unbekannter Teil: Klang-Datensatz {fun[:4]!r}")
    fname, stride = FUN_MAGICS[fmagic]
    prj.format = f"{kind}/{fname}"
    p16 = kind in ("FBKP", "FBKQ")
    wide = kind != "FBKK"
    prj.tracks = _new_tracks(NPAT if p16 else NPAT_V1)
    count, legacy = _decode_fun(prj, fun, stride == 8, fun[66], fname == "FUN17")
    tail = rec[BANK_ACTIVE_OFF:]
    if not wide:                                          # FBKK: dicht, 4 Parts × 8 Bänke
        _tail_fbkk(prj, tail, count, legacy)
        counts = _counts(fun[PROJ_REC_DIR - 0:], 32)
        owners = [o // NPAT_V1 * NPAT + o % NPAT_V1 for o in range(32)]
    else:
        L = FBKP if p16 else FBKL
        _tail_wide(prj, tail, kind, L)
        if p16:
            counts = _counts(tail[L["rec"]:], NTRK * NPAT)
            owners = list(range(NTRK * NPAT))
        else:
            counts = _counts(fun[PROJ_REC_DIR:], 32) + _counts(tail[L["rec"]:], NTRK * NPAT_V1 - 32)
            owners = [o // NPAT_V1 * NPAT + o % NPAT_V1 for o in range(NTRK * NPAT_V1)]
    try:
        _records(prj, fun, counts, owners)
    except FormatError as e:
        prj.warnings.append(f"aufgenommene Noten nicht lesbar ({e})")
    for k, t in enumerate(prj.tracks):
        t.in_use = k < max(1, prj.tracks_in_use)


def _tail_fbkk(prj: Project, tail: bytes, count: int, legacy):
    pos = 4
    for k in range(NPART):
        tr = prj.tracks[k]
        for b in range(NPAT_V1):
            o = 14340 + (k * NPAT_V1 + b) * 4
            pat = tr.patterns[b]
            if b != tr.active:
                pat.length, pat.div, pat.swing, pat.gate = tail[o], tail[o + 1], tail[o + 2], tail[o + 3]
                pat.steps = [Step.from_bits(tail[pos + 8 * i:pos + 8 * i + 8]) for i in range(NSTEP)]
                pos += NSTEP * 8
    chain = [list(tail[14468 + r * 4:14468 + r * 4 + 4]) + [0] * 4 for r in range(16)]
    prj.song = [SongRow(legacy[r][1], chain[r]) for r in range(min(count, 16))]


def _tail_wide(prj: Project, tail: bytes, kind: str, L: dict):
    p16 = L["npat"] == 16
    song64 = kind in ("FBKM", "FBKN", "FBKO", "FBKP", "FBKQ")
    songfx = kind in ("FBKN", "FBKO", "FBKP", "FBKQ")
    sounds = kind == "FBKO"
    pos = L["steps"]
    if song64:
        song = tail[pos:pos + SONG_BYTES]
        prj.song_loop = bool(song[1])
        if p16:
            sounds = bool(song[2])
        rows = []
        for r in range(min(song[0], 64)):
            rep = song[4 + r * 9]
            banks = list(song[5 + r * 9:5 + r * 9 + NTRK])
            if not p16:
                banks = [CHAIN_REST if v == NPAT_V1 else CHAIN_HOLD if v == NPAT_V1 + 1 else v for v in banks]
            rows.append(SongRow(rep, banks))
        prj.song = rows
        pos += SONG_BYTES
    else:
        pass   # FBKL: Song aus der alten Kette (unten)
    if songfx:
        fx = tail[pos:pos + FX_BYTES]
        for r, row in enumerate(prj.song):
            for e in range(FX_EVENTS):
                f, start, length = fx[(r * FX_EVENTS + e) * 3:(r * FX_EVENTS + e) * 3 + 3]
                if f:
                    row.punches.append(Punch(f - 1, start, length))
        pos += FX_BYTES
    if sounds:
        cnt = tail[pos]
        size = tail[pos + 2] | tail[pos + 3] << 8
        if size != SOUND_SIZE:
            raise FormatError("unbekannter Teil: Song-Klänge in anderer Größe")
        ids = tail[pos + 4:pos + 4 + 64 * NTRK]
        for r, row in enumerate(prj.song):
            row.sounds = [v if v <= cnt else 0 for v in ids[r * NTRK:(r + 1) * NTRK]]
        npar = tail[pos + 1]
        for i in range(cnt):
            o = pos + SOUND_HEAD + i * size
            raw = tail[o + npar + 6:o + npar + 22].split(b"\0")[0]
            name = raw.decode("ascii", "replace") if raw else f"Preset {i + 1}"
            prj.sounds.append(SongSound(name, tail[o + npar], tail[o + npar + 1]))
        pos += SOUND_HEAD + cnt * size
    if kind == "FBKQ":
        ps = tail[pos:pos + NTRK * NPAT]
        prj.pattern_sounds = {(k, b): ps[k * NPAT + b] for k in range(NTRK) for b in range(NPAT)
                              if ps[k * NPAT + b] and ps[k * NPAT + b] <= len(prj.sounds)}
        pos += NTRK * NPAT
    if not song64:
        chain = L["chain"]
        prj.song = []
    for k in range(NTRK):
        tr = prj.tracks[k]
        tr.active = tail[k] if k >= NPART else tr.active
        present = tail[pos] | (tail[pos + 1] << 8 if L["mask"] == 2 else 0)
        pos += L["mask"]
        for b in range(min(L["npat"], len(tr.patterns))):
            o = L["timing"] + (k * L["npat"] + b) * 4
            pat = tr.patterns[b]
            if not (k < NPART and b == tr.active):
                pat.length, pat.div = tail[o], tail[o + 1]
                pat.swing, pat.gate = struct.unpack_from("<b", tail, o + 2)[0], tail[o + 3]
            if present >> b & 1:
                lo, hi = struct.unpack_from("<II", tail, pos)
                mask = lo | hi << 32
                pos += 8
                for i in range(NSTEP):
                    if mask >> i & 1:
                        pat.steps[i] = Step.from_bits(tail[pos:pos + 8])
                        pos += 8
        if k >= NPART:                                       # MIDI-Spur: Parameter im Bank-Ende
            b0 = L["trk"] + (k - NPART) * (P_COUNT_NOW + 1)
            tr.params = [v - 64 for v in tail[b0:b0 + P_COUNT_NOW]]
            tr.engine = 21
            for pat in tr.patterns:
                pass
            ap = tr.patterns[tr.active] if tr.active < len(tr.patterns) else None
            if ap and not any(tail[L["timing"] + (k * L["npat"] + tr.active) * 4:][:1]):
                ap.length = tr.p(P_SLEN, 16)
    if pos > len(tail) - 4:
        raise FormatError("Bank-Ende übergelaufen")


def _decode_legacy(prj: Project, data: bytes, kind: str):
    """FUN7 (3388), FUN8 (3584), FBK9 (20224) nach web/EDITOR_PROTOCOL.md – nicht gegen Firmware-Code geprüft."""
    prj.container = kind
    rec = data[8:8 + 3584] if kind == "FBK9" else data
    prj.format = kind + " (älteres Format)"
    prj.tracks = _new_tracks(NPAT_V1)
    np_ = rec[66]
    if not 8 <= np_ <= 127:
        raise FormatError("Parameterzahl unplausibel")
    g = list(struct.unpack_from("<27h", rec, 8))
    prj.globals, prj.bpm, prj.swing, prj.selected = g, g[0], g[1], rec[62]
    pos = 68
    defaults = [p["def"] for p in tables()["params"]] or [0] * P_COUNT_NOW
    for t in range(NPART):
        vals = [v - 64 for v in rec[pos:pos + np_]]
        pos += np_
        tr = prj.tracks[t]
        tr.params = [vals[i] if i < np_ - 8 else defaults[i] for i in range(P_COUNT_NOW - 8)] + vals[np_ - 8:]
        tr.engine, tr.preset = rec[pos], rec[pos + 1]
        pos += 2
        tr.active = data[3592 + t] if kind == "FBK9" and data[3592 + t] < NPAT_V1 else 0
        pat = tr.patterns[tr.active]
        pat.steps = [Step.from_nine(rec[pos + 9 * i:pos + 9 * i + 9]) for i in range(NSTEP)]
        pos += 9 * NSTEP
        pat.length, pat.div, pat.swing, pat.gate = tr.p(P_SLEN, 16), tr.p(P_SDIV, 2), tr.p(P_SSWING), tr.p(P_SGATE, 64)
    if len(rec) >= 3584:
        _read_fm6(prj, rec, 3056)
    count = rec[pos]
    prj.song = [SongRow(rec[pos + 5 + 2 * r], [rec[pos + 4 + 2 * r]] * NPART + [0] * 4) for r in range(min(count, 16))]
    name = rec[len(rec) - 16:len(rec) - 4].split(b"\0")[0]
    if name and all(32 <= c <= 126 for c in name):
        prj.name = name.decode()
    if kind == "FBK9":
        p = 3596
        for t in range(NPART):
            tr = prj.tracks[t]
            for b in range(NPAT_V1):
                if b == tr.active:
                    continue
                tr.patterns[b].steps = [Step.from_nine(data[p + 9 * i:p + 9 * i + 9]) for i in range(NSTEP)]
                p += 9 * NSTEP
        for t in range(NPART):
            for b in range(NPAT_V1):
                ln, dv, sw, gt = struct.unpack_from("<4h", data, 19724 + (t * 8 + b) * 8)
                if b != prj.tracks[t].active:
                    pat = prj.tracks[t].patterns[b]
                    pat.length, pat.div, pat.swing, pat.gate = ln or 16, dv, sw, gt
    for k, t in enumerate(prj.tracks):
        t.in_use = k < NPART


def decode(data: bytes, firmware: str = "") -> Project:
    """Projekt-Objekt (Backup/.fm1proj) -> Project. Wirft nie."""
    prj = Project(size=len(data))
    try:
        if not data:
            prj.format = "leer"
            prj.warnings.append("Projektplatz ist leer")
            return prj
        if len(data) < 8:
            raise FormatError("zu kurz")
        magic = struct.unpack_from("<I", data)[0]
        if magic == MAGIC_HPJ1:
            rec = expand_hpj1(data)
            prj.format = "HPJ1"
            _decode_bank(prj, rec)
            prj.format = "HPJ1/" + prj.format
        elif magic in BANK_MAGICS:
            if len(data) != BANK_STORE_SIZE:
                raise FormatError("Bank-Container mit falscher Größe")
            _decode_bank(prj, data)
        elif magic in FUN_MAGICS and len(data) == PROJ_STORE_SIZE:
            fname, stride = FUN_MAGICS[magic]
            prj.format = fname
            prj.tracks = _new_tracks(NPAT)
            prj.hash_ok = struct.unpack_from("<I", data, PROJ_STORE_SIZE - 4)[0] == fnv(data[:PROJ_STORE_SIZE - 4])
            count, legacy = _decode_fun(prj, data, stride == 8, data[66], fname == "FUN17")
            prj.song = [SongRow(rep, [slot] * NPART + [0] * 4) for slot, rep in legacy[:count]]
            for k, t in enumerate(prj.tracks):
                t.in_use = k < NPART
        elif magic in LEGACY_MAGICS and len(data) in (3388, 3584, 20224):
            _decode_legacy(prj, data, LEGACY_MAGICS[magic])
        else:
            head = data[:4]
            if head[1:4] == b"NUF" and 0x30 <= head[0] <= 0x39:        # "6NUF" = FUN6, SLOOP (project.c)
                prj.format = f"SLOOP-Projekt (FUN{head[0] - 0x30})"
                raise FormatError("Das ist ein SLOOP-Projekt. Die Projekt-Ansicht kennt nur das Melodee-Format – "
                                  "SLOOP-Projekte sind anders aufgebaut (Sections, eigener Sequencer). Zum Ansehen "
                                  "und Bearbeiten nimm den SLOOP-Web-Editor (Seite „Editor“); sichern und "
                                  "zurückspielen geht wie gewohnt über Backups.")
            fw = firmware.lower()
            other = "andere Firmware" if fw and "melodee" not in fw else "unbekanntes Format"
            prj.format = other
            raise FormatError(f"unbekannter Teil (Kennung {head.hex()}, {len(data)} Byte) – {other}")
        prj.ok = True
    except (FormatError, struct.error, IndexError, ValueError, KeyError) as e:
        prj.ok = False
        prj.warnings.append(str(e) or "unbekannter Teil")
    return prj


def summary_for_backup(data: bytes, firmware: str = "") -> dict | None:
    """Hook für backup_inspect.register_project_decoder."""
    prj = decode(data, firmware)
    if not prj.ok:
        return None
    return {"summary": prj.summary(), "name": prj.name, "bpm": prj.bpm, "decoded_format": prj.format}


def load_any(path: Path) -> list[tuple[str, Project]]:
    """Backup (.json) oder .fm1proj -> [(Titel, Projekt)]."""
    from . import backup, projects
    path = Path(path)
    if path.suffix == projects.SUFFIX:
        pf = projects.load_project(path)
        return [(pf.name, decode(pf.data, pf.meta.get("firmware", "")))]
    arch = backup.load_file(path)
    out = []
    for o in arch.objects:
        if backup.is_project(o["id"]) and o["size"]:
            slot = backup.project_slot(o["id"])
            if arch.sloop and slot and slot > 4:
                continue
            title = ("Laufendes Projekt" if arch.sloop else "Laufende Musik") if slot == 0 else \
                (f"Section {'ABCD'[slot - 1]}" if arch.sloop else f"Projekt {slot}")
            prj = decode(o["bytes"], arch.firmware)
            if prj.name:
                title += f" – {prj.name}"
            out.append((title, prj))
    return out


def register():
    try:
        from . import backup_inspect
        backup_inspect.register_project_decoder(summary_for_backup)
    except Exception:   # noqa: BLE001
        pass


register()


def demo_project_bytes() -> bytes:
    """ein echtes Melodee-1.6-Projektobjekt (von Melodees Firmware-Code geschrieben) für Demo-Modus und Tests."""
    try:
        return (Path(__file__).parent / "data" / "melodee_demo_project.bin").read_bytes()
    except OSError:
        return b""
