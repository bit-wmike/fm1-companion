# SPDX-License-Identifier: GPL-3.0-only
"""DX7 voices: read and write SysEx, parameters, generators.

Formats (like the Melodee editor, web/editor.html ``FM6``):
  * VCED – one voice unpacked, 155 bytes (operator 6 first, 21 bytes each, then 19 voice bytes, name 10)
  * VMEM – one voice packed, 128 bytes (the record of a 32-voice bank; Melodee "native kind 1")
  * single-voice SysEx  F0 43 0n 00 01 1B <155> <checksum> F7  (163 bytes)
  * 32-voice bank SysEx F0 43 0n 09 20 00 <4096> <checksum> F7 (4104 bytes)
The checksum is the two's complement of the data sum (7 bits).
"""
from __future__ import annotations

from .i18n import _

import random
from dataclasses import dataclass, field

# operator bytes (VCED order): abbreviation, maximum, name
OP_PARAMS = [
    ("R1", 99, _("Rate 1")), ("R2", 99, _("Rate 2")), ("R3", 99, _("Rate 3")), ("R4", 99, _("Rate 4")),
    ("L1", 99, _("Level 1")), ("L2", 99, _("Level 2")), ("L3", 99, _("Level 3")), ("L4", 99, _("Level 4")),
    ("BP", 99, _("Breakpoint")), ("LD", 99, _("Left depth")), ("RD", 99, _("Right depth")),
    ("LC", 3, _("Left curve")), ("RC", 3, _("Right curve")), ("RS", 7, _("Rate scaling")),
    ("AMS", 3, _("AM sensitivity")), ("KVS", 7, _("Velocity sensitivity")), ("OL", 99, _("Output level")),
    ("MODE", 1, _("Fixed/ratio")), ("FC", 31, _("Frequency coarse")), ("FF", 99, _("Frequency fine")), ("DET", 14, _("Detune")),
]
VOICE_PARAMS = [
    ("PR1", 99, _("Pitch rate 1")), ("PR2", 99, _("Pitch rate 2")), ("PR3", 99, _("Pitch rate 3")), ("PR4", 99, _("Pitch rate 4")),
    ("PL1", 99, _("Pitch level 1")), ("PL2", 99, _("Pitch level 2")), ("PL3", 99, _("Pitch level 3")), ("PL4", 99, _("Pitch level 4")),
    ("ALG", 31, _("Algorithm")), ("FB", 7, _("Feedback")), ("OKS", 1, _("Oscillator sync")),
    ("LFS", 99, _("LFO speed")), ("LFD", 99, _("LFO delay")), ("LPMD", 99, _("LFO pitch depth")),
    ("LAMD", 99, _("LFO amp depth")), ("LKS", 1, _("LFO sync")), ("LFW", 5, _("LFO wave")), ("LPMS", 7, _("Pitch mod sens.")),
    ("TRNSP", 48, _("Transpose")),
]
OPI = {n: i for i, (n, _hi, _name) in enumerate(OP_PARAMS)}
VI = {n: 126 + i for i, (n, _hi, _name) in enumerate(VOICE_PARAMS)}
NAME, SIZE, PACKED = 145, 155, 128
LFO_WAVES = [_("Triangle"), _("Saw down"), _("Saw up"), _("Square"), _("Sine"), "S&H"]
CURVES = ["-LIN", "-EXP", "+EXP", "+LIN"]


def vmax(i: int) -> int:
    return OP_PARAMS[i % 21][1] if i < 126 else VOICE_PARAMS[i - 126][1] if i < NAME else 126


def at(op: int, field_: str) -> int:
    """Index of the field of operator op (1..6) in the VCED."""
    return (6 - op) * 21 + OPI[field_]


def sanitize(v) -> list[int]:
    v = list(v[:SIZE]) + [0] * max(0, SIZE - len(v))
    return [(32 if not 32 <= x <= 126 else x) if i >= NAME else max(0, min(int(x), vmax(i))) for i, x in enumerate(v)]


def unpack(b) -> list[int]:
    """VMEM (128) -> VCED (155), like eng_fm6.c fm6_unpack."""
    v = [0] * SIZE
    for k in range(6):
        o, d = k * 17, k * 21
        for i in range(11):
            v[d + i] = b[o + i] & 127
        v[d + 11] = b[o + 11] & 3
        v[d + 12] = b[o + 11] >> 2 & 3
        v[d + 13] = b[o + 12] & 7
        v[d + 20] = b[o + 12] >> 3 & 15
        v[d + 14] = b[o + 13] & 3
        v[d + 15] = b[o + 13] >> 2 & 7
        v[d + 16] = b[o + 14] & 127
        v[d + 17] = b[o + 15] & 1
        v[d + 18] = b[o + 15] >> 1 & 31
        v[d + 19] = b[o + 16] & 127
    for i in range(9):
        v[126 + i] = b[102 + i] & 127
    v[134] &= 31
    v[135] = b[111] & 7
    v[136] = b[111] >> 3 & 1
    for i in range(4):
        v[137 + i] = b[112 + i] & 127
    v[141] = b[116] & 1
    v[142] = b[116] >> 1 & 7
    v[143] = b[116] >> 4 & 7
    v[144] = b[117] & 127
    for i in range(10):
        v[NAME + i] = b[118 + i] & 127
    return sanitize(v)


def pack(v) -> list[int]:
    """VCED (155) -> VMEM (128), like eng_fm6.c fm6_pack."""
    b = [0] * PACKED
    for k in range(6):
        o, d = k * 17, k * 21
        for i in range(11):
            b[o + i] = v[d + i] & 127
        b[o + 11] = (v[d + 11] & 3) | (v[d + 12] & 3) << 2
        b[o + 12] = (v[d + 13] & 7) | (v[d + 20] & 15) << 3
        b[o + 13] = (v[d + 14] & 3) | (v[d + 15] & 7) << 2
        b[o + 14] = v[d + 16] & 127
        b[o + 15] = (v[d + 17] & 1) | (v[d + 18] & 31) << 1
        b[o + 16] = v[d + 19] & 127
    for i in range(9):
        b[102 + i] = v[126 + i] & 127
    b[110] &= 31
    b[111] = (v[135] & 7) | (v[136] & 1) << 3
    for i in range(4):
        b[112 + i] = v[137 + i] & 127
    b[116] = (v[141] & 1) | (v[142] & 7) << 1 | (v[143] & 7) << 4
    b[117] = v[144] & 127
    for i in range(10):
        b[118 + i] = v[NAME + i] & 127
    return b


def name(v) -> str:
    return bytes(v[NAME:NAME + 10]).decode("latin-1").rstrip()


def set_name(v, s: str) -> list[int]:
    t = "".join(c if 32 <= ord(c) <= 126 else " " for c in str(s))[:10].ljust(10)
    v = list(v)
    v[NAME:NAME + 10] = [ord(c) for c in t]
    return v


def checksum(data) -> int:
    return (128 - (sum(data) & 127)) & 127


def init_voice() -> list[int]:
    """the DX7 INIT VOICE: algorithm 1, only operator 1 audible."""
    v = [0] * SIZE
    for op in range(1, 7):
        for f, x in (("R1", 99), ("R2", 99), ("R3", 99), ("R4", 99), ("L1", 99), ("L2", 99), ("L3", 99), ("L4", 0),
                     ("BP", 39), ("FC", 1), ("DET", 7), ("OL", 99 if op == 1 else 0)):
            v[at(op, f)] = x
    for i, x in enumerate((99, 99, 99, 99, 50, 50, 50, 50)):
        v[126 + i] = x
    v[VI["OKS"]] = 1
    v[VI["LFS"]] = 35
    v[VI["LKS"]] = 1
    v[VI["LPMS"]] = 3
    v[VI["TRNSP"]] = 24
    return set_name(v, "INIT VOICE")


# ------------------------------------------------------------------ SysEx ---

def single_syx(v, channel: int = 0) -> bytes:
    data = sanitize(v)
    return bytes([0xF0, 0x43, channel & 15, 0x00, 0x01, 0x1B, *data, checksum(data), 0xF7])


def bank_syx(voices, channel: int = 0) -> bytes:
    vs = list(voices)[:32]
    while len(vs) < 32:
        vs.append(init_voice())
    data = [x for v in vs for x in pack(sanitize(v))]
    return bytes([0xF0, 0x43, channel & 15, 0x09, 0x20, 0x00, *data, checksum(data), 0xF7])


@dataclass
class ParseResult:
    voices: list = field(default_factory=list)      # VCED lists
    warnings: list = field(default_factory=list)    # hints (translated)
    bank: bool = False                              # comes from a 32-voice bank


def parse_syx(data: bytes) -> ParseResult:
    """reads everything that looks like DX7 voices – including messy files: several messages,
    wrong checksums (warning), raw 4096/155-byte files, missing F7 at the end."""
    d = bytes(data)
    res = ParseResult()
    if not d:
        res.warnings.append(_("The file is empty."))
        return res
    if d[0] != 0xF0 and len(d) in (4096, 4096 + 1):
        res.voices = [unpack(d[k * 128:k * 128 + 128]) for k in range(32)]
        res.bank = True
        res.warnings.append(_("Read a raw 4096-byte bank without SysEx frame."))
        return res
    if d[0] != 0xF0 and len(d) == SIZE:
        res.voices = [sanitize(d)]
        res.warnings.append(_("Read a raw 155-byte voice without SysEx frame."))
        return res
    i = 0
    n_msg = 0
    while i < len(d):
        if d[i] != 0xF0:
            i += 1
            continue
        e = i + 1
        while e < len(d) and d[e] != 0xF7 and d[e] != 0xF0:
            e += 1
        m = d[i:e + 1] if e < len(d) and d[e] == 0xF7 else d[i:e]
        i = e if e < len(d) and d[e] == 0xF0 else e + 1
        if len(m) < 8 or m[1] != 0x43 or (m[2] & 0xF0) != 0:
            continue
        n_msg += 1
        body = m[6:]
        if m[-1] == 0xF7:
            body = body[:-1]
        if m[3] == 0x09 and m[4] == 0x20 and m[5] == 0x00:
            if len(body) < 4096:
                res.warnings.append(_("Bank message {n} is too short ({size} instead of 4097 bytes) – skipped.").format(n=n_msg, size=len(body)))
                continue
            vm = body[:4096]
            if any(x > 127 for x in vm):
                res.warnings.append(_("Bank message {n} contains invalid bytes – skipped.").format(n=n_msg))
                continue
            if len(body) < 4097 or checksum(vm) != body[4096]:
                res.warnings.append(_("Bank message {n}: checksum missing or wrong – read anyway.").format(n=n_msg))
            res.voices += [unpack(vm[k * 128:k * 128 + 128]) for k in range(32)]
            res.bank = True
        elif m[3] == 0x00 and m[4] == 0x01 and m[5] == 0x1B:
            if len(body) < SIZE:
                res.warnings.append(_("Voice message {n} is too short – skipped.").format(n=n_msg))
                continue
            vc = body[:SIZE]
            if len(body) < SIZE + 1 or checksum(vc) != body[SIZE]:
                res.warnings.append(_("Voice message {n}: checksum missing or wrong – read anyway.").format(n=n_msg))
            res.voices.append(sanitize(vc))
        elif m[3] == 0x7D and m[4] == 4 and m[5] == 8 and len(m) == 231:
            vc = m[6:6 + SIZE]
            if all(x <= vmax(k) and (k < NAME or x >= 32) for k, x in enumerate(vc)):
                res.voices.append(list(vc))
                res.warnings.append(_("MainStage wrapper detected: only voice and name taken over."))
    if not res.voices:
        res.warnings.append(_("No DX7 voice found in the file."))
    return res


def voice_key(v) -> str:
    """Content key (without name) – for duplicates."""
    return bytes(pack(sanitize(v))[:118]).hex()


# ------------------------------------------------------------ algorithms ---
# The 32 DX7 algorithms as a bus table like msfa (Google, Apache-2.0) / FM1 Open web/synth/dx7core.c:
# entry k applies to operator 6-k (operator 6 first). Bits: 1/2 output to bus 1/2 (0 = output),
# 4 add, 0x10/0x20 input from bus 1/2, 0x40 feedback input, 0x80 feedback output.
ALGORITHMS = [
    (0xc1, 0x11, 0x11, 0x14, 0x01, 0x14), (0x01, 0x11, 0x11, 0x14, 0xc1, 0x14), (0xc1, 0x11, 0x14, 0x01, 0x11, 0x14),
    (0x41, 0x11, 0x94, 0x01, 0x11, 0x14), (0xc1, 0x14, 0x01, 0x14, 0x01, 0x14), (0x41, 0x94, 0x01, 0x14, 0x01, 0x14),
    (0xc1, 0x11, 0x05, 0x14, 0x01, 0x14), (0x01, 0x11, 0xc5, 0x14, 0x01, 0x14), (0x01, 0x11, 0x05, 0x14, 0xc1, 0x14),
    (0x01, 0x05, 0x14, 0xc1, 0x11, 0x14), (0xc1, 0x05, 0x14, 0x01, 0x11, 0x14), (0x01, 0x05, 0x05, 0x14, 0xc1, 0x14),
    (0xc1, 0x05, 0x05, 0x14, 0x01, 0x14), (0xc1, 0x05, 0x11, 0x14, 0x01, 0x14), (0x01, 0x05, 0x11, 0x14, 0xc1, 0x14),
    (0xc1, 0x11, 0x02, 0x25, 0x05, 0x14), (0x01, 0x11, 0x02, 0x25, 0xc5, 0x14), (0x01, 0x11, 0x11, 0xc5, 0x05, 0x14),
    (0xc1, 0x14, 0x14, 0x01, 0x11, 0x14), (0x01, 0x05, 0x14, 0xc1, 0x14, 0x14), (0x01, 0x14, 0x14, 0xc1, 0x14, 0x14),
    (0xc1, 0x14, 0x14, 0x14, 0x01, 0x14), (0xc1, 0x14, 0x14, 0x01, 0x14, 0x04), (0xc1, 0x14, 0x14, 0x14, 0x04, 0x04),
    (0xc1, 0x14, 0x14, 0x04, 0x04, 0x04), (0xc1, 0x05, 0x14, 0x01, 0x14, 0x04), (0x01, 0x05, 0x14, 0xc1, 0x14, 0x04),
    (0x04, 0xc1, 0x11, 0x14, 0x01, 0x14), (0xc1, 0x14, 0x01, 0x14, 0x04, 0x04), (0x04, 0xc1, 0x11, 0x14, 0x04, 0x04),
    (0xc1, 0x14, 0x04, 0x04, 0x04, 0x04), (0xc4, 0x04, 0x04, 0x04, 0x04, 0x04),
]


def graph(alg: int) -> dict:
    """Algorithm 0..31 -> {"carriers": [op], "edges": [(from, to)], "feedback": (from, to)}; operators 1..6."""
    flags = ALGORITHMS[alg & 31]
    bus: dict[int, list[int]] = {1: [], 2: []}
    edges, car = [], []
    fb_out = fb_in = None
    for k, f in enumerate(flags):
        op = 6 - k
        inbus, outbus = (f >> 4) & 3, f & 3
        if inbus:
            edges += [(src, op) for src in bus[inbus]]
        if outbus == 0:
            car.append(op)
        elif f & 4:
            bus[outbus].append(op)
        else:
            bus[outbus] = [op]
        if f & 0x80:
            fb_out = op
        if f & 0x40:
            fb_in = op
    return {"carriers": sorted(car), "edges": edges, "feedback": (fb_out, fb_in) if fb_out else None}


# ------------------------------------------------------------- generators ---

def _clamp(i, x):
    return max(0, min(vmax(i), int(round(x))))


def carriers(alg: int) -> list[int]:
    return graph(alg)["carriers"]


def random_voice(rng: random.Random | None = None, alg: int | None = None) -> list[int]:
    """musically constrained randomness: audible carriers, sensible envelopes, integer ratios."""
    rng = rng or random.Random()
    v = init_voice()
    a = rng.randrange(32) if alg is None else alg
    v[VI["ALG"]] = a
    v[VI["FB"]] = rng.choice([0, 0, 3, 5, 6, 7])
    car = set(carriers(a))
    style = rng.choice(["pluck", "pad", "keys", "bell", "bass"])
    for op in range(1, 7):
        is_car = op in car
        if style == "pad":
            r = [rng.randint(30, 60), rng.randint(20, 50), rng.randint(20, 50), rng.randint(25, 55)]
            lv = [99, rng.randint(85, 99), rng.randint(70, 95), 0]
        elif style == "pluck" or style == "bass":
            r = [rng.randint(85, 99), rng.randint(40, 75), rng.randint(20, 50), rng.randint(50, 75)]
            lv = [99, rng.randint(60, 90), 0 if style == "pluck" else rng.randint(40, 80), 0]
        else:
            r = [rng.randint(80, 99), rng.randint(30, 70), rng.randint(15, 45), rng.randint(40, 70)]
            lv = [99, rng.randint(70, 95), rng.randint(0, 70), 0]
        for k in range(4):
            v[at(op, f"R{k + 1}")] = r[k]
            v[at(op, f"L{k + 1}")] = lv[k]
        ratios = [1, 1, 1, 2, 2, 3, 4, 0] if style != "bell" else [1, 2, 3, 3, 5, 7, 9]
        v[at(op, "FC")] = 1 if (is_car and style != "bell") else rng.choice(ratios) if style != "bass" else rng.choice([0, 1, 1, 2])
        v[at(op, "FF")] = 0 if rng.random() < 0.85 else rng.randint(0, 50)
        v[at(op, "DET")] = rng.randint(5, 9)
        v[at(op, "OL")] = rng.randint(88, 99) if is_car else rng.randint(45, 88)
        v[at(op, "KVS")] = rng.randint(0, 4)
        v[at(op, "RS")] = rng.randint(0, 3)
    v[VI["TRNSP"]] = 24 if style != "bass" else 12
    v[VI["LFS"]] = rng.randint(20, 40)
    v[VI["LPMD"]] = rng.choice([0, 0, 3, 6])
    return set_name(v, f"{style.upper()} {rng.randint(1, 99):02d}")


def mutate(v, amount: float = 0.3, keep_alg: bool = True, rng: random.Random | None = None) -> list[int]:
    """amount 0..1: how many values change and how much. Carriers stay audible."""
    rng = rng or random.Random()
    v = sanitize(v)
    out = list(v)
    for i in range(NAME):
        if keep_alg and i == VI["ALG"]:
            continue
        if rng.random() > amount:
            continue
        mx = vmax(i)
        if i < 126 and OP_PARAMS[i % 21][0] in ("MODE",):
            continue
        out[i] = _clamp(i, v[i] + rng.gauss(0, max(1.0, mx * amount * 0.35)))
    if not keep_alg and rng.random() < amount:
        out[VI["ALG"]] = rng.randrange(32)
    for op in carriers(out[VI["ALG"]]):
        out[at(op, "OL")] = max(out[at(op, "OL")], 80)
        out[at(op, "L1")] = max(out[at(op, "L1")], 80)
    out[VI["TRNSP"]] = v[VI["TRNSP"]]
    return out


def morph(a, b, t: float) -> list[int]:
    """linear blend of two voices (t 0 = a, 1 = b); algorithm, modes and name from the nearer one."""
    a, b = sanitize(a), sanitize(b)
    near = a if t < 0.5 else b
    out = list(near)
    discrete = {VI["ALG"], VI["OKS"], VI["LKS"], VI["LFW"]} | {k * 21 + OPI[f] for k in range(6) for f in ("MODE", "LC", "RC")}
    for i in range(NAME):
        if i not in discrete:
            out[i] = _clamp(i, a[i] + (b[i] - a[i]) * t)
    return out


def variations(v, n: int = 8, amount: float = 0.25, seed: int | None = None) -> list[list[int]]:
    rng = random.Random(seed)
    out = []
    base = name(v)[:7].rstrip() or "VAR"
    for k in range(n):
        c = mutate(v, amount * (0.6 + 0.8 * k / max(1, n - 1)), keep_alg=k < n // 2, rng=rng)
        out.append(set_name(c, f"{base} {k + 1}"))
    return out


def describe(v) -> str:
    alg = v[VI["ALG"]] + 1
    return _("Algorithm {alg}, feedback {fb}").format(alg=alg, fb=v[VI['FB']])


# ------------------------------------------------------------ sending ---
# Like "FM1 Editor & Librarian" (benny-sparra/fm1-dx7-patch-importer, MIT): M-VAVE's own firmware
# (identity FM-1_0xx up to 019) gets the single-voice dump into the edit buffer (save with SAVE on the
# device); every other/unknown firmware gets the 155 DX7 parameter changes F0 43 1n gg pp dd F7, which only
# change the current sound and overwrite nothing. Melodee and FM1 Open accept the single dump.

def param_changes(v, channel: int = 0) -> list[bytes]:
    v = sanitize(v)
    return [bytes([0xF0, 0x43, 0x10 | (channel & 15), (i >> 7) & 3, i & 127, v[i] & 127, 0xF7]) for i in range(SIZE)]


def is_mvave_stock(identity: str) -> bool:
    import re as _re
    m = _re.search(r"FM-1_(\d+)", identity or "")
    return bool(m) and int(m.group(1)) <= 19


def send_plan(firmware_key: str, identity: str = "") -> str:
    """-> "single" (single-voice dump) or "params" (parameter changes)."""
    fw = (firmware_key or "").lower()
    if fw in ("melodee", "fm1open", "fm1_open", "open", "stock", "mvave") or is_mvave_stock(identity):
        return "single"
    return "params"
