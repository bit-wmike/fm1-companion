# SPDX-License-Identifier: GPL-3.0-only
"""Understand MIDI: translate messages into readable text (for the MIDI monitor).

* Channel messages: note names, CC names (General MIDI + what Melodee does with the CC),
  program change, pitch bend, aftertouch.
* System: clock (with measured tempo), start/stop/continue, song position, active sensing.
* SysEx: Felucca/Melodee editor commands with names and short content, Melodee preset files,
  Yamaha DX7 (voice/bank/parameter), the M-VAVE update protocol, universal SysEx.

Sources: Melodee web/EDITOR_PROTOCOL.md, web/editor.html (``const CMD``),
firmware/src/midi_control.c, mod.c, midi_knobs.c (CC mapping), tools/fm1_install.py (update).
"""
from __future__ import annotations

import csv
import io
import time
from collections import deque
from dataclasses import dataclass, field

from .i18n import N_, _, pgettext

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def note_name(n: int) -> str:
    """60 -> C4 (like Melodee and the samples page)."""
    return f"{NOTE_NAMES[n % 12]}{n // 12 - 1}"


# ------------------------------------------------------------- Felucca SysEx ---
FELUCCA_HDR = (0x7D, 0x46, 0x4C)
# command names: the app's protocol.Cmd + the Melodee editor's ``const CMD`` table
FELUCCA_CMDS = {
    1: "INFO", 2: "GET", 3: "SET", 4: "DUMP", 5: "DESC", 6: "STEP_GET", 7: "STEP_SET", 8: "PRESET",
    9: "PROJECT", 10: "NAMES", 11: "SMP_BEGIN", 12: "SMP_WRITE", 13: "SMP_END", 14: "SMP_ERASE",
    15: "SMP_INFO", 16: "UP_LIST", 17: "UP_GET", 18: "UP_PUT", 19: "UP_STORE", 20: "UP_LOAD",
    21: "UP_ERASE", 22: "WATCH", 23: "CHANGED", 24: "RELOAD", 25: "PING", 26: "STEP_CHANGED",
    27: "TRACK", 28: "TRACK_MIX", 29: "TRACK_DUMP", 30: "TRACK_STEP", 31: "TRACK_PARAM",
    32: "TRACK_CHANGED", 33: "SONG", 34: "UI_STATE", 35: "UI_SET", 36: "UI_PALETTES", 37: "FAV_GET",
    38: "FAV_SET", 64: "MOTION", 65: "BACKUP_LIST", 66: "BACKUP_GET", 67: "BACKUP_PUT", 68: "FM6_GET",
    69: "FM6_PUT", 70: "FM6_LIST", 71: "FM6_ERASE", 72: "AUDIO_STATS", 73: "PATTERN", 74: "BANK_SONG",
    75: "CZ_GET", 76: "CZ_PUT", 77: "CZ_BANK", 78: "NATIVE", 80: "LIBRARY", 95: "PROPHET",
}
FELUCCA_PUSH = {23, 24, 26, 32}
FELUCCA_TEXT = {                         # shown translated: _(FELUCCA_TEXT[name])
    "INFO": N_("device info"), "GET": N_("read parameter"), "SET": N_("set parameter"), "DUMP": N_("all values"),
    "DESC": N_("parameter description"), "PING": N_("sign of life"), "WATCH": N_("live sync"),
    "CHANGED": N_("value changed on the device"), "RELOAD": N_("sound reloaded"), "STEP_CHANGED": N_("step changed"),
    "TRACK": N_("select/query track"), "TRACK_PARAM": N_("track parameter"),
    "TRACK_CHANGED": N_("track value changed"), "BACKUP_LIST": N_("backup list"), "BACKUP_GET": N_("read backup"),
    "BACKUP_PUT": N_("write backup"), "AUDIO_STATS": N_("audio statistics"), "LIBRARY": N_("preset library"),
    "PROJECT": N_("project"), "PATTERN": N_("pattern"),
}


def v14_dec(lo: int, hi: int) -> int:
    """Melodee value: 14 bits offset by +8192 (editor.c ed_rv). protocol.r14 computes differently!"""
    return (lo | hi << 7) - 8192


def v14_enc(v: int) -> list[int]:
    u = max(-8192, min(8191, int(round(v)))) + 8192
    return [u & 0x7F, u >> 7 & 0x7F]


# ---------------------------------------------------------------- CC names ---
GM_CC = {                                # names translated where used (cc_name)
    0: "Bank Select MSB", 1: "Modulation", 2: "Breath", 4: "Foot", 5: N_("Portamento time"), 6: "Data Entry MSB",
    7: N_("Volume"), 8: "Balance", 10: N_("Pan"), 11: "Expression", 12: N_("Effect 1"), 13: N_("Effect 2"),
    16: N_("General purpose 1"), 17: N_("General purpose 2"), 18: N_("General purpose 3"), 19: N_("General purpose 4"),
    32: "Bank Select LSB", 38: "Data Entry LSB", 64: "Sustain", 65: N_("Portamento on/off"), 66: "Sostenuto",
    67: N_("Soft pedal"), 68: "Legato", 69: "Hold 2", 70: N_("Sound variation"), 71: N_("Resonance"), 72: "Release",
    73: "Attack", 74: N_("Brightness/cutoff"), 75: "Decay", 76: N_("Vibrato rate"), 77: N_("Vibrato depth"),
    78: N_("Vibrato delay"), 80: N_("General purpose 5"), 81: N_("General purpose 6"), 82: N_("General purpose 7"),
    83: N_("General purpose 8"), 84: N_("Portamento control"), 91: N_("Reverb"), 93: "Chorus", 94: N_("Effect 4"),
    95: N_("Effect 5"), 96: "Data +", 97: "Data −", 98: "NRPN LSB", 99: "NRPN MSB", 100: "RPN LSB", 101: "RPN MSB",
    120: "All Sound Off", 121: "Reset Controller", 122: "Local Control", 123: "All Notes Off", 124: N_("Omni off"),
    125: N_("Omni on"), 126: "Mono", 127: "Poly",
}
# what Melodee does with incoming CCs (midi_control.c midi_parameter/midi_control, mod.c mod_midi,
# midi_knobs.c profile GENERAL); profile MINILAB: 74 71 76 77 93 18 19 16 instead of 16..19, 80..83
MELODEE_CC = {
    1: N_("Mod wheel (matrix MODW)"), 2: "Breath (FM6)", 4: "Foot (FM6)", 5: "Glide / FM6-Portamento",
    6: N_("Data Entry (RPN 0 = bend range)"), 7: N_("Track volume (LEVEL)"), 10: "Panorama (PAN)",
    11: "Expression (Matrix EXPR)", 16: N_("MIDI knob: Volume"), 17: N_("MIDI knob: Select"),
    18: N_("MIDI knob: Presets"), 19: N_("MIDI knob: track select"), 38: N_("Data Entry LSB (bend cents)"),
    64: "Sustain", 65: N_("Portamento on/off (FM6)"), 71: N_("Resonance (engine macro)"), 72: "Release", 73: "Attack",
    74: N_("Brightness (engine macro)"), 75: "Decay", 80: N_("MIDI knob: KNOB1"), 81: N_("MIDI knob: KNOB2"),
    82: N_("MIDI knob: KNOB3"), 83: N_("MIDI knob: KNOB4"), 91: N_("– (Melodee: no effect)"), 100: "RPN LSB",
    101: "RPN MSB", 120: "All Sound Off", 121: "Reset Controller", 123: "All Notes Off",
}


def cc_name(cc: int, firmware: str = "") -> str:
    if firmware == "melodee" and cc in MELODEE_CC:
        return _(MELODEE_CC[cc])
    return _(GM_CC[cc]) if cc in GM_CC else f"CC {cc}"


# ------------------------------------------------------------- messages ---
TYPE_NAMES = {0x80: N_("Note off"), 0x90: N_("Note on"), 0xA0: N_("Poly aftertouch"), 0xB0: N_("Controller"),
              0xC0: N_("Program"), 0xD0: N_("Aftertouch"), 0xE0: N_("Pitch bend")}      # translated in decode()
SYS_NAMES = {0xF1: N_("MTC quarter frame"), 0xF2: N_("Song position"), 0xF3: N_("Song select"), 0xF6: "Tune Request",
             0xF8: "Clock", 0xFA: "Start", 0xFB: "Continue", 0xFC: "Stop", 0xFE: "Active Sensing", 0xFF: "Reset"}
# filter groups for the monitor
KIND_NOTE, KIND_CC, KIND_PROG, KIND_BEND, KIND_AT, KIND_CLOCK, KIND_TRANSPORT, KIND_SENSE, KIND_SYSEX, KIND_OTHER = (
    "note", "cc", "prog", "bend", "at", "clock", "transport", "sense", "sysex", "other")


@dataclass
class Decoded:
    kind: str
    type: str
    channel: int | None          # 1..16
    data: str                    # raw data, short (hex)
    meaning: str


def _hex(b, limit=24) -> str:
    s = " ".join(f"{x:02X}" for x in b[:limit])
    return s + (f" … ({len(b)} Bytes)" if len(b) > limit else "")


def unpack_bits(s) -> bytes:
    """8→7-bit stream of the M-VAVE update back into bytes (like fm1_install.unpack7)."""
    out, acc, nb = bytearray(), 0, 0
    for b in s:
        acc |= (b & 0x7F) << nb
        nb += 7
        while nb >= 8:
            out.append(acc & 0xFF)
            acc >>= 8
            nb -= 8
    return bytes(out)


def describe_felucca(cmd: int, a: list[int], direction: str = "") -> str:
    name = FELUCCA_CMDS.get(cmd) or _("command {n}").format(n=cmd)
    text = _(FELUCCA_TEXT[name]) if name in FELUCCA_TEXT else ""
    head = f"Felucca {name}" + (f" – {text}" if text else "")
    if cmd in FELUCCA_PUSH:
        head += " " + _("(notification)")
    detail = ""
    try:
        if cmd == 1 and direction == "in" and a:
            end = a.index(0)
            detail = bytes(a[:end]).decode("latin-1")
        elif cmd in (2, 3, 23) and len(a) >= 2:
            detail = (_("global parameter {n}") if a[0] == 1 else _("parameter {n}")).format(n=a[1])
            if len(a) >= 4:
                detail += f" = {v14_dec(a[2], a[3])}"
        elif cmd in (31, 32) and len(a) >= 2:
            detail = _("track {track}, parameter {n}").format(track=a[0] + 1, n=a[1])
            if len(a) >= 4:
                detail += f" = {v14_dec(a[2], a[3])}"
        elif cmd == 5 and len(a) >= 2:
            detail = (_("global parameter {n}") if a[0] == 1 else _("parameter {n}")).format(n=a[1])
            if len(a) > 9:
                r = a[9:]
                lab = bytes(r[:r.index(0)]).decode("latin-1") if 0 in r else ""
                detail += f" „{lab}“ {v14_dec(a[3], a[4])}…{v14_dec(a[5], a[6])}"
        elif cmd == 27 and a:
            detail = (_("track {track} selected") if direction == "in" else _("track {track}")).format(track=a[0] + 1)
        elif cmd == 22 and a:
            detail = pgettext("state", "on") if a[0] & 1 else pgettext("state", "off")
        elif cmd in (65, 66, 67, 9, 73) and a:
            detail = _("arguments {args}").format(args=a[:4])
        elif cmd == 24 and len(a) >= 2:
            detail = (_("engine {engine}, preset {preset}, track {track}") if len(a) > 2 else
                      _("engine {engine}, preset {preset}")).format(engine=a[0], preset=a[1],
                                                                    track=a[2] + 1 if len(a) > 2 else 0)
    except (ValueError, IndexError):
        detail = ""
    n = len(a)
    return head + (f": {detail}" if detail else "") + (f" [{n} Bytes]" if n > 8 else "")


DX7_FMT = {0: N_("1 voice (155 parameters)"), 9: N_("32 voices (bank)"), 1: "Performance", 2: "Bank Performances"}


def describe_sysex(b: list[int], direction: str = "") -> str:
    if len(b) < 3:
        return _("SysEx (empty)")
    body = b[1:-1] if b[-1] == 0xF7 else b[1:]
    if tuple(body[:3]) == FELUCCA_HDR and len(body) >= 4:
        return describe_felucca(body[3], body[4:], direction)
    if body[:3] == [0x7D, 0x4D, 0x4C]:
        return _("Melodee preset file (user preset as SysEx)")
    if body[0] == 0x43 and len(body) >= 3:                       # Yamaha
        sub, ch = body[1] >> 4, (body[1] & 15) + 1
        if sub == 0 and len(body) >= 3:
            fmt = _(DX7_FMT[body[2]]) if body[2] in DX7_FMT else _("format {n}").format(n=body[2])
            return _("Yamaha DX7 dump: {format}, device {device}").format(format=fmt, device=ch)
        if sub == 1 and len(body) >= 4:
            grp = body[2]
            pnum = body[3] + (128 if grp & 3 == 1 else 0)
            what = _("Yamaha DX7 function {n} = {value}, device {device}") if grp >> 2 == 2 else \
                _("Yamaha DX7 voice parameter {n} = {value}, device {device}")
            return what.format(n=pnum, value=body[4] if len(body) > 4 else "?", device=ch)
        if sub == 2:
            f = body[2] if len(body) > 2 else -1
            return _("Yamaha DX7 dump request ({format})").format(format=_(DX7_FMT[f]) if f in DX7_FMT else "?")
        return "Yamaha SysEx"
    if body[:3] == [0x22, 0x24, 0x35]:
        key = body[3] if len(body) > 3 else -1
        if key == 0x7F:
            return _("M-VAVE update: start update mode (device restarts into the loader)")
        if key == 0x7D:
            return _("M-VAVE soft key: ROM boot mode (WL82 UBOOT) – device stays silent until switched "
                     "off and on")
        return _("M-VAVE control SysEx 22 24 35 {key}").format(key=f"{key:02X}") if key >= 0 else \
            _("M-VAVE control SysEx 22 24 35 (incomplete)")
    if body[:3] == [0x00, 0x32, 0x45]:
        return _("M-VAVE update: query identifier (handshake)")
    if body[0] == 0x00 and len(body) >= 4:
        u = unpack_bits(body)
        if u[:2] == b"\x00\x59":
            chn = u[2]
            if chn == 0x11:
                if len(u) >= 33:
                    return _("M-VAVE update: identifier {id}").format(id=u[6:33].rstrip(b"\0").decode("latin-1", "replace"))
                return _("M-VAVE update: query identifier")
            if chn == 0x30:
                if len(u) == 15:
                    addr = int.from_bytes(u[7:11], "little")
                    return _("M-VAVE update: device requests data (address {address})").format(address=addr)
                return _("M-VAVE update: firmware data ({n} bytes)").format(n=max(0, len(u) - 15))
            return _("M-VAVE update protocol (channel {channel})").format(channel=chn)
    if body[0] == 0x7E and len(body) >= 4:
        if body[2:4] == [0x06, 0x01]:
            return "Universal: Identity Request"
        if body[2:4] == [0x06, 0x02]:
            return "Universal: Identity Reply"
        if body[2] == 0x09:
            return _("Universal: General MIDI on") if body[3] in (1, 3) else _("Universal: General MIDI off")
        return "Universal Non-Realtime"
    if body[0] == 0x7F and len(body) >= 4:
        if body[2:4] == [0x04, 0x01] and len(body) >= 6:
            return _("Universal: master volume {percent} %").format(percent=round((body[4] | body[5] << 7) / 163.83))
        return "Universal Realtime"
    if body[0] == 0x01:
        return "Sequential (Prophet) SysEx"
    if body[0] == 0x41:
        return "Roland SysEx"
    return _("SysEx manufacturer {id}").format(id=body[0])


def decode(b, direction: str = "", firmware: str = "") -> Decoded:
    b = list(b)
    if not b:
        return Decoded(KIND_OTHER, _("empty"), None, "", "")
    st = b[0]
    raw = _hex(b)
    if st == 0xF0:
        return Decoded(KIND_SYSEX, "SysEx", None, raw, describe_sysex(b, direction))
    if st >= 0xF0:
        name = _(SYS_NAMES[st]) if st in SYS_NAMES else f"System {st:02X}"
        kind = (KIND_CLOCK if st == 0xF8 else KIND_SENSE if st == 0xFE
                else KIND_TRANSPORT if st in (0xFA, 0xFB, 0xFC, 0xF2) else KIND_OTHER)
        meaning = name
        if st == 0xF2 and len(b) >= 3:
            meaning = _("Song position {n} (16ths)").format(n=b[1] | b[2] << 7)
        return Decoded(kind, name, None, raw, meaning)
    if st < 0x80:
        return Decoded(KIND_OTHER, _("Data"), None, raw, _("data bytes without status"))
    t, ch = st & 0xF0, (st & 15) + 1
    d1 = b[1] if len(b) > 1 else 0
    d2 = b[2] if len(b) > 2 else 0
    if t == 0x90 and d2 == 0:
        t = 0x80                                           # note on with velocity 0 = note off
    name = _(TYPE_NAMES[t])
    if t in (0x80, 0x90):
        return Decoded(KIND_NOTE, name, ch, raw, f"{note_name(d1)} ({d1}), Velocity {d2}")
    if t == 0xA0:
        return Decoded(KIND_AT, name, ch, raw, _("{note} pressure {value}").format(note=note_name(d1), value=d2))
    if t == 0xB0:
        return Decoded(KIND_CC, name, ch, raw, f"CC {d1} {cc_name(d1, firmware)} = {d2}")
    if t == 0xC0:
        prog = _("Program {n}").format(n=d1 + 1)
        if ch != 10:
            prog += f" – {GM_PROGRAMS[d1]}"
        return Decoded(KIND_PROG, name, ch, raw, prog)
    if t == 0xD0:
        return Decoded(KIND_AT, name, ch, raw, _("pressure {value}").format(value=d1))
    v = (d1 | d2 << 7) - 8192
    return Decoded(KIND_BEND, name, ch, raw, f"{v:+d}" + (_(" (center)") if v == 0 else ""))


# --------------------------------------------------------- tempo from clock ---
class ClockMeter:
    """measures the tempo of an incoming MIDI clock (24 pulses per quarter) and its stability."""

    def __init__(self, window: int = 96):
        self.times: deque[float] = deque(maxlen=window)
        self.running = False

    def reset(self):
        self.times.clear()

    def feed(self, status: int, t: float | None = None):
        t = time.perf_counter() if t is None else t
        if status == 0xF8:
            if self.times and t - self.times[-1] > 0.5:   # pause: re-measure
                self.times.clear()
            self.times.append(t)
        elif status in (0xFA, 0xFB):
            self.running = True
            self.times.clear()
        elif status == 0xFC:
            self.running = False

    @property
    def bpm(self) -> float | None:
        if len(self.times) < 7:
            return None
        span = self.times[-1] - self.times[0]
        if span <= 0:
            return None
        return 60.0 / (span / (len(self.times) - 1) * 24)

    @property
    def jitter_ms(self) -> float | None:
        """Standard deviation of the pulse intervals in ms."""
        if len(self.times) < 8:
            return None
        iv = [b - a for a, b in zip(self.times, list(self.times)[1:])]
        m = sum(iv) / len(iv)
        return (sum((x - m) ** 2 for x in iv) / len(iv)) ** 0.5 * 1000

    def alive(self, now: float | None = None) -> bool:
        now = time.perf_counter() if now is None else now
        return bool(self.times) and now - self.times[-1] < 0.5


# ------------------------------------------------------------- protocol ---
@dataclass
class Event:
    t: float                     # seconds since the monitor started
    direction: str               # "in" / "out"
    port: str
    data: bytes
    dec: Decoded = field(default=None)  # type: ignore[assignment]


class EventLog:
    """Ring buffer for the monitor (the MIDI thread writes, the UI reads in batches)."""

    def __init__(self, size: int = 5000):
        self.events: deque[Event] = deque(maxlen=size)
        self.t0 = time.perf_counter()
        self.pending: deque[Event] = deque(maxlen=size)

    def add(self, data, direction: str, port: str, firmware: str = "") -> Event:
        ev = Event(time.perf_counter() - self.t0, direction, port, bytes(data))
        ev.dec = decode(ev.data, direction, firmware)
        self.pending.append(ev)
        return ev

    def take(self) -> list[Event]:
        out = []
        while self.pending:
            ev = self.pending.popleft()
            self.events.append(ev)
            out.append(ev)
        return out

    def clear(self):
        self.events.clear()
        self.pending.clear()
        self.t0 = time.perf_counter()


DIR_TEXT = {"in": N_("← from the device"), "out": N_("→ to the device")}       # translated where used


def export_text(events, fmt: str = "txt") -> str:
    rows = [(f"{e.t:10.3f}", _(DIR_TEXT[e.direction]) if e.direction in DIR_TEXT else e.direction, e.port, e.dec.type,
             str(e.dec.channel or ""), e.data.hex(" ").upper(), e.dec.meaning) for e in events]
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow([_("Time (s)"), _("Direction"), _("Port"), _("Type"), _("Channel"), _("Data"), _("Meaning")])
        w.writerows([[c.strip() for c in r] for r in rows])
        return buf.getvalue()
    return "\n".join("  ".join(r) for r in rows) + ("\n" if rows else "")


# ------------------------------------------------------- General MIDI ---
GM_PROGRAMS = [
    "Acoustic Grand Piano", "Bright Acoustic Piano", "Electric Grand Piano", "Honky-tonk Piano", "Electric Piano 1",
    "Electric Piano 2", "Harpsichord", "Clavinet", "Celesta", "Glockenspiel", "Music Box", "Vibraphone", "Marimba",
    "Xylophone", "Tubular Bells", "Dulcimer", "Drawbar Organ", "Percussive Organ", "Rock Organ", "Church Organ",
    "Reed Organ", "Accordion", "Harmonica", "Tango Accordion", "Acoustic Guitar (nylon)", "Acoustic Guitar (steel)",
    "Electric Guitar (jazz)", "Electric Guitar (clean)", "Electric Guitar (muted)", "Overdriven Guitar",
    "Distortion Guitar", "Guitar Harmonics", "Acoustic Bass", "Electric Bass (finger)", "Electric Bass (pick)",
    "Fretless Bass", "Slap Bass 1", "Slap Bass 2", "Synth Bass 1", "Synth Bass 2", "Violin", "Viola", "Cello",
    "Contrabass", "Tremolo Strings", "Pizzicato Strings", "Orchestral Harp", "Timpani", "String Ensemble 1",
    "String Ensemble 2", "Synth Strings 1", "Synth Strings 2", "Choir Aahs", "Voice Oohs", "Synth Voice",
    "Orchestra Hit", "Trumpet", "Trombone", "Tuba", "Muted Trumpet", "French Horn", "Brass Section", "Synth Brass 1",
    "Synth Brass 2", "Soprano Sax", "Alto Sax", "Tenor Sax", "Baritone Sax", "Oboe", "English Horn", "Bassoon",
    "Clarinet", "Piccolo", "Flute", "Recorder", "Pan Flute", "Blown Bottle", "Shakuhachi", "Whistle", "Ocarina",
    "Lead 1 (square)", "Lead 2 (sawtooth)", "Lead 3 (calliope)", "Lead 4 (chiff)", "Lead 5 (charang)",
    "Lead 6 (voice)", "Lead 7 (fifths)", "Lead 8 (bass + lead)", "Pad 1 (new age)", "Pad 2 (warm)",
    "Pad 3 (polysynth)", "Pad 4 (choir)", "Pad 5 (bowed)", "Pad 6 (metallic)", "Pad 7 (halo)", "Pad 8 (sweep)",
    "FX 1 (rain)", "FX 2 (soundtrack)", "FX 3 (crystal)", "FX 4 (atmosphere)", "FX 5 (brightness)", "FX 6 (goblins)",
    "FX 7 (echoes)", "FX 8 (sci-fi)", "Sitar", "Banjo", "Shamisen", "Koto", "Kalimba", "Bagpipe", "Fiddle", "Shanai",
    "Tinkle Bell", "Agogo", "Steel Drums", "Woodblock", "Taiko Drum", "Melodic Tom", "Synth Drum", "Reverse Cymbal",
    "Guitar Fret Noise", "Breath Noise", "Seashore", "Bird Tweet", "Telephone Ring", "Helicopter", "Applause",
    "Gunshot",
]
assert len(GM_PROGRAMS) == 128
