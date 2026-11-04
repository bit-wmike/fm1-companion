# SPDX-License-Identifier: GPL-3.0-only
"""Das SysEx-Protokoll der Felucca-Familie (Felucca, Melodee, Skiff, SLOOP).

Anfrage:  F0 7D 46 4C <cmd> <args...> F7   ("FL", nicht-kommerzielle ID 7D)
Antwort:  derselbe Kopf, dasselbe <cmd>, danach die Nutzdaten (alles 7 Bit).

Quelle: web/EDITOR_PROTOCOL.md und firmware/src/editor*.c im Melodee-Repo.
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
    RESOURCES = 82      # Melodee ab 1.6: Ressourcen (Argument 0 = Status, nur lesen)


class ProtocolError(Exception):
    pass


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
    """32 Bit als 5 × 7 Bit, LSB zuerst (das letzte Byte 0..15)."""
    n &= 0xFFFFFFFF
    return [(n >> (i * 7)) & (15 if i == 4 else 127) for i in range(5)]


def r32(a, off: int = 0) -> int:
    if len(a) < off + 5 or a[off + 4] > 15:
        raise ProtocolError(_("invalid number in the data stream"))
    return (a[off] | a[off + 1] << 7 | a[off + 2] << 14 | a[off + 3] << 21 | a[off + 4] << 28) & 0xFFFFFFFF


def v14(n: int) -> list[int]:
    """vorzeichenbehaftet 14 Bit mit Versatz 8192, LSB zuerst (wie Firmware ed_rv / Editor v14enc)."""
    u = max(-8192, min(8191, int(round(n)))) + 8192
    return [u & 127, u >> 7]


def r14(a, off: int = 0) -> int:
    return (a[off] | a[off + 1] << 7) - 8192


def pack7(data: bytes) -> list[int]:
    """Gruppen zu 7 Bytes, jeweils mit ihren obersten Bits vorneweg."""
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
    """liest Bytes, NUL-terminierte Strings und 14-Bit-Werte aus einer Antwort."""

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
        """Melodee meldet "MELODEE v…", SLOOP "FELUCCA SLOOP …", Skiff (wie das Original) "FELUCCA v…"."""
        v = self.version.lower()
        for name in ("melodee", "sloop", "skiff", "orbit", "x0x", "loom", "choralroot", "jiant"):
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
        pass                                 # ältere / andere Firmware: nur die Version
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
    """-> (Slot-Größe in KiB, Slots). Melodee 1.6: 0 Slots."""
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


# Felder von AUDIO_STATS (72) je Schema – wie tools/usb_audio_stats.py von Melodee.
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
# Anfrage-Flags (nur lesend; Bit 0 würde die Maxima zurücksetzen – nutzen wir nicht)
AUDIO_FLAGS_ALL = 2 | 4 | 8 | 16


def parse_audio_stats(a) -> dict:
    if not a or a[0] < 2:
        raise ProtocolError(_("unknown format of the audio statistics"))
    names = AUDIO_SCHEMAS.get(a[0])
    if names is None:                       # neueres Schema: die bekannten Felder vorn lesen
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


# Gemeinsame User-Bibliothek (Befehl 80, Melodee ≥ 1.5): Liste aller Preset-Plätze
CMD_LIBRARY = 80


@dataclass
class LibrarySlot:
    slot: int          # 0-basiert (U001 = 0)
    used: bool
    engine: int
    favorite: bool
    old_format: int
    name: str

    @property
    def label(self) -> str:
        return f"U{self.slot + 1:03d}"


def parse_library_list(a) -> tuple[int, list[LibrarySlot]]:
    """Antwort auf LIBRARY op 0 -> (Gesamtzahl Plätze, Einträge)."""
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
    notes: list[int] = field(default_factory=list)   # bis zu 4 MIDI-Noten
    time: int = 2                                     # 0 NOTE, 1 TIE, 2 REST
    flags: int = 0                                    # 1 Akzent, 2 Slide
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
