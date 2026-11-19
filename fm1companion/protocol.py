# SPDX-License-Identifier: GPL-3.0-only
"""The SysEx protocol of the Felucca family (Felucca, Melodee, Skiff, SLOOP).

Request:  F0 7D 46 4C <cmd> <args...> F7   ("FL", non-commercial ID 7D)
Reply:    the same header, the same <cmd>, then the payload (all 7-bit).

Source: web/EDITOR_PROTOCOL.md and firmware/src/editor*.c in the Melodee repo.
"""
from __future__ import annotations

import zlib
from dataclasses import dataclass, field

from .i18n import _

HDR = (0x7D, 0x46, 0x4C)


class Cmd:
    INFO = 1
    GET = 2
    SET = 3
    DESC = 5
    PROJECT = 9
    SMP_BEGIN = 11
    SMP_WRITE = 12
    SMP_END = 13
    SMP_ERASE = 14
    SMP_INFO = 15
    UP_LIST = 16
    PING = 25
    TRACK = 27
    TRACK_STEP = 30
    TRACK_PARAM = 31
    BACKUP_LIST = 65
    BACKUP_GET = 66
    BACKUP_PUT = 67
    AUDIO_STATS = 72
    PATTERN = 73
    BANK_SONG = 74
    RESOURCES = 82      # Melodee 1.6+: resources (argument 0 = status, read only)


class ProtocolError(Exception):
    pass


# ------------------------------------------------- commands that differ inside the family ---
# The Felucca family shares the frame and the low command numbers, but not every number means the same everywhere
# (checked in the firmwares' sources, firmware/src/editor*.c):
#   30 TRACK_STEP with a step: Felucca's 12-byte step (n, 4 notes, time, flags, vel, hit, acc, high bits, chance) in
#      Felucca (ed_step_put), Skiff (the same, plus its FLAM / COND / NUDGE bytes after it), JIANT (the same, plus its
#      ratchet and lane modes) and Melodee. SLOOP 2 and its forks (KARMA …) read bytes 8–10 as lvl / hi / rat
#      (editor.c ed_step_in, protocol v5) once a step has 11 bytes or more: another step. (A step of up to 10 bytes –
#      the first 8 the same everywhere – is not refused.)
#   73 PATTERN and 74 BANK_SONG: Melodee only. On SLOOP 2.5+ and KARMA they are DSYN_GET / DSYN_PUT (the drum synth
#      kits, editor_dsyn.c); on Felucca and JIANT 73 is MENU_SET (a display setting, editor_menu.c) and on Felucca 74 is
#      FM6B_BEGIN (the FM6 bank); on Skiff 74 is KIT_GET (the DRUM-X kit).
# SLOOP 3 speaks no Felucca command at all (its own frame, 7D 53 33). Unknown firmwares get none of these.
#
# A plain "FELUCCA v…" INFO does not prove Felucca: SLOOP's forks built with --release answer "FELUCCA 0.9 BETA" too
# (fwfamily). What does: the tags Felucca's INFO carries after NTRK and CHAIN_ROWS (editor.c ED_INFO) – "motion +
# chance" (4D 01 …, Melodee 4D 02 …), the step's chance byte the 12-byte step ends with. Felucca has sent it since 1.0,
# Skiff and JIANT send it too; SLOOP's INFO ends with NTRK and its protocol byte and has no tags. So "felucca", "skiff"
# and "jiant" count only with that tag (gate_name); without it they are an unknown fork: refused.
STEP12_FIRMWARES = frozenset({"melodee", "felucca", "skiff", "jiant"})
STEP12_FROM = 11                  # step bytes from which SLOOP 2 (editor.c ed_step_in: na >= 11) reads them its own way
MELODEE_FIRMWARES = frozenset({"melodee"})
FEATURE_FIRMWARES = {"pattern_write": STEP12_FIRMWARES, "pattern_banks": MELODEE_FIRMWARES,
                     "song_chain": MELODEE_FIRMWARES}
NEEDS_TAG = frozenset({"felucca", "skiff", "jiant"})
UNPROVEN = "unproven-felucca"     # a gate name: answers as Felucca, but without Felucca's INFO tags


def has_chance_tag(info) -> bool:
    """INFO carries Felucca's "motion + chance" tag (4D 01/02 …) after its track count"""
    raw = list(getattr(info, "raw", []) or [])
    try:
        r = Reader(raw)
        r.s()
        ne = r.b()
        r.i += 4
        for _i in range(ne):
            r.s()
        tail = raw[r.i + 1:]                           # (after NTRK)
    except ProtocolError:
        return False
    return any(tail[i] == 0x4D and tail[i + 1] in (1, 2) for i in range(len(tail) - 3))


def gate_name(info) -> str:
    """the name the command gate goes by: Info.firmware_name, but a Felucca-like name only with Felucca's tags"""
    name = info.firmware_name
    if name in NEEDS_TAG and not has_chance_tag(info):
        return UNPROVEN
    return name


def supports(firmware: str | None, feature: str) -> bool:
    """does the firmware (protocol.Info.firmware_name, "sloop3", "" unknown) do this the way the app sends it?
    feature: "pattern_write" (TRACK_STEP with a step), "pattern_banks" (PATTERN 73), "song_chain" (BANK_SONG 74)"""
    return (firmware or "").lower() in FEATURE_FIRMWARES[feature]


def command_refusal(firmware: str | None, cmd: int, args=()) -> str:
    """"" if the app may send this request to this firmware, else the feature it would need (see supports)"""
    if cmd == Cmd.PATTERN and not supports(firmware, "pattern_banks"):
        return "pattern_banks"
    if cmd == Cmd.BANK_SONG and not supports(firmware, "song_chain"):
        return "song_chain"
    if cmd == Cmd.TRACK_STEP and len(args) - 2 >= STEP12_FROM and not supports(firmware, "pattern_write"):
        return "pattern_write"                    # (a step of at most 10 bytes – n, 4 notes, time, flags, vel and
    return ""                                     # 2 zeros – means the same everywhere: SLOOP 2 reads lvl / rat from 11)


def unsupported_text(feature: str) -> str:
    """the tooltip / message for a button whose command this firmware does not have"""
    if feature == "pattern_write":
        return _("This firmware does not support writing patterns from the app (Melodee, Felucca, Skiff and JIANT "
                 "do).")
    if feature == "pattern_banks":
        return _("This firmware does not support choosing a pattern from the app (only Melodee does).")
    return _("This firmware does not support the song chain from the app (only Melodee does).")


# ---------------------------------------------------------------- framing ---

def frame(cmd: int, args=()) -> list[int]:
    a = [int(x) for x in args]
    if any(x < 0 or x > 127 for x in a) or not 0 <= cmd <= 127:
        raise ProtocolError(_("SysEx data must be 7-bit"))
    return [0xF0, *HDR, cmd, *a, 0xF7]


def unframe(data) -> tuple[int, list[int]] | None:
    d = list(data)
    if len(d) < 6 or d[0] != 0xF0 or d[-1] != 0xF7 or tuple(d[1:4]) != HDR:
        return None
    return d[4], d[5:-1]


# ------------------------------------------------------------ numbers ---

def u32(n: int) -> list[int]:
    """32 bits as 5 × 7 bits, LSB first (the last byte 0..15)."""
    n &= 0xFFFFFFFF
    return [(n >> (i * 7)) & (15 if i == 4 else 127) for i in range(5)]


def r32(a, off: int = 0) -> int:
    if len(a) < off + 5 or a[off + 4] > 15:
        raise ProtocolError(_("invalid number in the data stream"))
    return (a[off] | a[off + 1] << 7 | a[off + 2] << 14 | a[off + 3] << 21 | a[off + 4] << 28) & 0xFFFFFFFF


def v14(n: int) -> list[int]:
    """signed 14 bits with offset 8192, LSB first (like firmware ed_rv / editor v14enc)."""
    u = max(-8192, min(8191, int(round(n)))) + 8192
    return [u & 127, u >> 7]


def r14(a, off: int = 0) -> int:
    return (a[off] | a[off + 1] << 7) - 8192


def pack7(data: bytes) -> list[int]:
    """Groups of 7 bytes, each with their top bits up front."""
    out = []
    for i in range(0, len(data), 7):
        g = data[i:i + 7]
        out.append(sum(((x >> 7) & 1) << j for j, x in enumerate(g)))
        out.extend(x & 0x7F for x in g)
    return out


def unpack7(a, size: int) -> bytes:
    out = bytearray()
    i = 0
    while len(out) < size:
        n = min(7, size - len(out))
        if i >= len(a):
            raise ProtocolError(_("short data block"))
        mask = a[i]
        i += 1
        if mask >> n:
            raise ProtocolError(_("invalid bytes in the data block"))
        for k in range(n):
            if i >= len(a) or a[i] > 127:
                raise ProtocolError(_("short data block"))
            out.append(a[i] | ((mask >> k) & 1) << 7)
            i += 1
    if i != len(a):
        raise ProtocolError(_("surplus bytes in the data block"))
    return bytes(out)


def crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


class Reader:
    """reads bytes, NUL-terminated strings and 14-bit values from a reply."""

    def __init__(self, a):
        self.a = list(a)
        self.i = 0

    def more(self) -> bool:
        return self.i < len(self.a)

    def b(self) -> int:
        if self.i >= len(self.a):
            raise ProtocolError(_("answer too short"))
        v = self.a[self.i]
        self.i += 1
        return v

    def s(self) -> str:
        j = self.i
        while j < len(self.a) and self.a[j] != 0:
            j += 1
        if j >= len(self.a):
            raise ProtocolError(_("string without end"))
        txt = bytes(self.a[self.i:j]).decode("latin-1")
        self.i = j + 1
        return txt

    def v(self) -> int:
        x = r14(self.a, self.i)
        self.i += 2
        return x


# --------------------------------------------------------------- parsers ---

@dataclass
class Info:
    version: str
    engines: list[str] = field(default_factory=list)
    p_count: int = 0
    g_count: int = 0
    nstep: int = 0
    p_e0: int = 0
    ntrk: int = 1
    raw: list[int] = field(default_factory=list)

    @property
    def firmware_name(self) -> str:
        """Melodee reports "MELODEE v…", SLOOP "FELUCCA SLOOP …", KARMA (a SLOOP fork) "FELUCCA KARMA 3.1", Skiff (like the
        original) "FELUCCA v…"; a fork that names itself is the catalog's key for it (fwfamily: "FELUCCA CLIPPER 1.3.1"
        -> "clipper"), a plain "FELUCCA v…" (Felucca and the forks that answer as it does) "felucca"."""
        v = self.version.lower()
        from . import fwfamily                  # the firmwares that name themselves (the catalog audit's table): the
        fam = fwfamily.by_info(self.version)    # name first – "FELUCCA sloopDX" is no SLOOP, "FELUCCA CLIPPER" no Felucca
        if fam is not None:
            return fam.name
        if "karma" in v:
            return "karma"
        for name in ("melodee", "sloop", "skiff", "orbit", "x0x", "loom", "choralroot", "jiant", "coralflower"):
            if name in v:
                return name
        if "felucca" in v:
            return "skiff" if any(e.upper().startswith("DRUM-X") for e in self.engines) else "felucca"
        return ""


def parse_info(a) -> Info:
    r = Reader(a)
    ver = r.s()
    info = Info(version=ver, raw=list(a))
    try:
        ne = r.b()
        info.p_count = r.b()
        info.g_count = r.b()
        info.nstep = r.b()
        info.p_e0 = r.b()
        info.engines = [r.s() for _i in range(ne)]
        if r.more():
            info.ntrk = r.b()
    except ProtocolError:
        pass                                 # older / other firmware: version only
    return info


@dataclass
class SampleSlot:
    index: int
    zones: int
    name: str
    kib: int

    @property
    def empty(self) -> bool:
        return self.zones == 0


def parse_smp_info(a) -> tuple[int, list[SampleSlot]]:
    """-> (slot size in KiB, slots). Melodee 1.6: 0 slots."""
    r = Reader(a)
    n = r.b()
    kib = r.b()
    slots = []
    for k in range(n):
        z = r.b()
        name = r.s()
        size = r.b()
        slots.append(SampleSlot(k, z, name, size))
    return kib, slots


# fields of AUDIO_STATS (72) per schema – like Melodee's tools/usb_audio_stats.py.
AUDIO_STAT_NAMES = [
    "play_alt", "capture_alt", "play_rate", "capture_rate", "play_fill", "capture_fill",
    "play_underruns", "play_overruns", "capture_underruns", "capture_overruns", "bad_packets",
    "packets_received", "packets_sent", "missed_usb_frames", "longest_gap_us", "longest_service_us",
    "late_renders", "feedback", "longest_render_us", "cpu_q8",
]
AUDIO_VOICE_FIELDS = ["voices_active", "voices_held", "core1_rejected", "core1_faults", "voices_shed", "voices_given_up"]
AUDIO_CORE_FIELDS = ["core1_online", "core1_jobs", "core1_max_job_us", "core1_max_wait_us", "core1_timeouts", "fm6_pairs"]
AUDIO_MEMORY_FIELDS = ["cache_status", "cache_con_before", "cache_data_before", "cache_instruction_before",
                       "cache_con_after", "cache_data_after", "cache_instruction_after", "cache_failure_address",
                       "cache_test_us", "resource_capacity", "resource_used", "resource_peak", "resource_failures",
                       "cache_capacity", "cache_used"]
AUDIO_PART_FIELDS = [f"track_{p}_{f}" for f in ("voices_shed", "voices_given_up") for p in range(1, 5)]
AUDIO_SCHEMAS = {
    2: AUDIO_STAT_NAMES,
    3: AUDIO_STAT_NAMES + AUDIO_VOICE_FIELDS,
    4: AUDIO_STAT_NAMES + AUDIO_VOICE_FIELDS + AUDIO_CORE_FIELDS,
    5: AUDIO_STAT_NAMES + AUDIO_VOICE_FIELDS + AUDIO_CORE_FIELDS + AUDIO_MEMORY_FIELDS,
    6: AUDIO_STAT_NAMES + AUDIO_VOICE_FIELDS + AUDIO_CORE_FIELDS + AUDIO_MEMORY_FIELDS + AUDIO_PART_FIELDS,
}
# request flags (read only; bit 0 would reset the maxima – we do not use it)
AUDIO_FLAGS_ALL = 2 | 4 | 8 | 16


def parse_audio_stats(a) -> dict:
    if not a or a[0] < 2:
        raise ProtocolError(_("unknown format of the audio statistics"))
    names = AUDIO_SCHEMAS.get(a[0])
    if names is None:                       # newer schema: read the known fields at the front
        names = AUDIO_SCHEMAS[max(AUDIO_SCHEMAS)]
    vals = {"schema": a[0]}
    for i, name in enumerate(names):
        off = 1 + i * 5
        if off + 5 > len(a):
            break
        vals[name] = r32(a, off)
    if "cpu_q8" in vals:                      # Q8: 256 = 100 %
        vals["cpu_percent"] = vals["cpu_q8"] * 100 / 256
    return vals


# shared user library (command 80, Melodee ≥ 1.5): list of all preset slots
CMD_LIBRARY = 80


@dataclass
class LibrarySlot:
    slot: int          # 0-based (U001 = 0)
    used: bool
    engine: int
    favorite: bool
    old_format: int
    name: str

    @property
    def label(self) -> str:
        return f"U{self.slot + 1:03d}"


def parse_library_list(a) -> tuple[int, list[LibrarySlot]]:
    """Reply to LIBRARY op 0 -> (total number of slots, entries)."""
    r = Reader(a)
    op, lo, hi, rc = r.b(), r.b(), r.b(), r.b()
    if op != 0 or rc != 0:
        raise ProtocolError(_("preset list rejected (rc {code})").format(code=rc))
    first = lo | hi << 7
    count = r.b()
    total = r.b() | r.b() << 7
    out = []
    for i in range(count):
        used, eng, old = r.b(), r.b(), r.b()
        out.append(LibrarySlot(first + i, bool(used & 1), eng, bool(used & 2), old, r.s()))
    return total, out


@dataclass
class PatternState:
    track: int
    op: int
    rc: int
    active: int
    queued: int
    count: int


def parse_pattern(a) -> PatternState:
    return PatternState(*a[:6])


@dataclass
class Step:
    notes: list[int] = field(default_factory=list)   # up to 4 MIDI notes
    time: int = 2                                     # 0 NOTE, 1 TIE, 2 REST
    flags: int = 0                                    # 1 accent, 2 slide
    vel: int = 100
    chance: int = 100

    def encode(self) -> list[int]:
        n = min(4, len(self.notes))
        notes = (list(self.notes[:4]) + [0, 0, 0, 0])[:4]
        return [n, *[x & 127 for x in notes], self.time, self.flags & 3, self.vel & 127, 0, 0, 0, min(100, self.chance)]


@dataclass
class Desc:
    scope: int
    id: int
    fmt: int
    min: int
    max: int
    default: int
    label: str
    unit: str
    names: list[str] = field(default_factory=list)


def parse_desc(a) -> Desc:
    r = Reader(a)
    scope, pid, fmt = r.b(), r.b(), r.b()
    mn, mx, df = r.v(), r.v(), r.v()
    label, unit = r.s(), r.s()
    d = Desc(scope, pid, fmt, mn, mx, df, label, unit)
    if fmt == 8:
        try:
            while r.more() and len(d.names) <= mx - mn:
                d.names.append(r.s())
        except ProtocolError:
            pass
    return d
