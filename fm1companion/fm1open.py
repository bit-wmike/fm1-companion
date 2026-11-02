# SPDX-License-Identifier: GPL-3.0-only
# SEA5-Kodierung (encode/decode, Tabellen): nach web/synth/sea_encode.h, sea_decode.c und
#   sample_metadata.inc aus M-VAVE FM1 Open V14 – MIT, Copyright (c) 2025 Dani Biró (SEA,
#   https://github.com/Daninet/sea-codec); modifications (c) 2026 M-VAVE.
# Protokoll und Bank-Format: nach app/transfer.c, app/samples.c, sdk/WebUpdate.js, sdk/Samples.js
#   aus FM1 Open V14 (GPL-3.0-only WITH GPL-3.0-linking-exception, (c) 2026 M-VAVE).
"""Sample-Slots der offiziellen M-VAVE-Firmware „FM1 Open“ (V14): 8 Slots, eine gemeinsame Bank.

Siehe docs/fm1open-samples.md. Kurz:
* Datei-Protokoll: F0 7D 'F' 'M' 02 cmd token-lo token-hi <8-Bit-Daten als LSB-zuerst-7-Bit-Strom>
  Prüfsumme F7; Antwort mit cmd|64, gleichem Token, erstes Byte = Status (0 ok, 64+n = Fehler -n).
* Samples: Befehl 11 Status, 12 Beginn (Größe), 13 Schreiben (Offset + bis 128 Byte), 14 Abschluss,
  15 Lesen (Offset + bis 64 Byte), 16 Abbruch. Die Bank wird immer GANZ geschrieben (Flash wird
  zuerst gelöscht) – deshalb: erst lesen und sichern, dann ändern, dann schreiben und zurücklesen.
* Bank: 512-Byte-Kopf ("FM1S", Version 1, Größe, CRC, Commit, 32000 Hz, 16 Einträge à 28 Byte),
  danach SEA5-Blöcke: 228 Byte für je 320 Frames (32 kHz, 10 ms).
"""
from __future__ import annotations

import queue
import random
import re
import struct
import time
import zlib
from dataclasses import dataclass
from typing import Callable

SLOTS, DISK_SLOTS = 8, 16
HEADER, RATE = 512, 32000
CHUNK_FRAMES, CHUNK_BYTES = 320, 228
REGION = 466944                          # GROOVE_SAMPLE_BYTES
CAPACITY = 446464                        # V14 (Befehl 22); ältere Firmware: 462848
CAPACITY_V13 = 462848
MAGIC = 0x53314D46                       # "FM1S"
BLOCK_MAGIC = b"\x01\x45\x14\x5a"
MAX_FRAMES = (REGION - HEADER) // CHUNK_BYTES * CHUNK_FRAMES
# Pads 1–8 spielen auf diesen MIDI-Noten (sample_metadata.inc: sample_notes)
PAD_NOTES = [36, 38, 37, 41, 47, 48, 42, 46]
PAD_ROLES = ["Kick", "Snare", "Rimshot", "Tom tief", "Tom mittel", "Tom hoch", "Hi-Hat zu", "Hi-Hat offen"]
STATUS_TEXT = {0: "keine gültige Sample-Bank", 1: "prüft die Bank …", 2: "bereit", 3: "empfängt gerade",
               4: "Fehler beim Lesen/Prüfen der Bank"}
ERRORS = {65: "ungültige Flash-Anfrage", 66: "Flash-Fehler", 67: "Gerät beschäftigt (anderer Anschluss überträgt)",
          68: "Flash nicht gelöscht", 69: "Flash-Kontext", 70: "Flash nicht verfügbar",
          71: "Daten oder Parameter ungültig", 72: "zu wenig Speicher",
          73: "alte Bank belegt den erweiterten Speicher – erst sichern und verkleinern"}


class FM1OpenError(Exception):
    def __init__(self, msg: str, code: int = 0):
        super().__init__(msg)
        self.code = code


# =============================================================== Framing ===

def pack7(data) -> list[int]:
    out, bits, value = [], 0, 0
    for b in data:
        value |= (b & 255) << bits
        bits += 8
        while bits >= 7:
            out.append(value & 127)
            value >>= 7
            bits -= 7
    if bits:
        out.append(value & 127)
    return out


def unpack7(a) -> bytes | None:
    out, bits, value = bytearray(), 0, 0
    for b in a:
        value |= b << bits
        bits += 7
        if bits >= 8:
            out.append(value & 255)
            value >>= 8
            bits -= 8
    if value or bits >= 7:
        return None
    return bytes(out)


def frame(cmd: int, token: int, payload=b"") -> list[int]:
    if not 0 <= cmd < 128 or not 0 <= token < 16384 or len(payload) > 132:
        raise ValueError("ungültige Protokoll-Parameter")
    d = [0xF0, 0x7D, 0x46, 0x4D, 0x02, cmd, token & 127, token >> 7, *pack7(payload)]
    d.append((-sum(d[1:])) & 127)
    d.append(0xF7)
    return d


def parse(msg) -> tuple[int, int, bytes] | None:
    """-> (cmd, token, Daten) oder None"""
    m = list(msg)
    if len(m) < 10 or m[0] != 0xF0 or m[-1] != 0xF7 or m[1:5] != [0x7D, 0x46, 0x4D, 0x02]:
        return None
    if any(b > 127 for b in m[1:-1]) or sum(m[1:-1]) % 128:
        return None
    data = unpack7(m[8:-2])
    if data is None:
        return None
    return m[5], m[6] | m[7] << 7, data


# =================================================================== SEA5 ===

DQT = [
    1, -1, 3, -3, 5, -5, 7, -7, 9, -9, 11, -11, 13, -13, 15, -15,
    17, -17, 19, -19, 21, -21, 23, -23, 25, -25, 27, -27, 29, -29, 31, -31,
    4, -4, 13, -13, 23, -23, 33, -33, 43, -43, 53, -53, 63, -63, 73, -73,
    83, -83, 93, -93, 103, -103, 113, -113, 123, -123, 133, -133, 143, -143, 155, -155,
    11, -11, 35, -35, 63, -63, 91, -91, 119, -119, 147, -147, 175, -175, 203, -203,
    231, -231, 259, -259, 287, -287, 315, -315, 343, -343, 371, -371, 399, -399, 434, -434,
    21, -21, 70, -70, 126, -126, 182, -182, 238, -238, 294, -294, 350, -350, 406, -406,
    462, -462, 518, -518, 574, -574, 630, -630, 686, -686, 742, -742, 798, -798, 868, -868,
    36, -36, 120, -120, 216, -216, 312, -312, 408, -408, 504, -504, 600, -600, 696, -696,
    792, -792, 888, -888, 984, -984, 1080, -1080, 1176, -1176, 1272, -1272, 1368, -1368, 1488, -1488,
    56, -56, 188, -188, 338, -338, 488, -488, 638, -638, 788, -788, 938, -938, 1088, -1088,
    1238, -1238, 1388, -1388, 1538, -1538, 1688, -1688, 1838, -1838, 1988, -1988, 2138, -2138, 2325, -2325,
    81, -81, 270, -270, 486, -486, 702, -702, 918, -918, 1134, -1134, 1350, -1350, 1566, -1566,
    1782, -1782, 1998, -1998, 2214, -2214, 2430, -2430, 2646, -2646, 2862, -2862, 3078, -3078, 3348, -3348,
    113, -113, 375, -375, 675, -675, 975, -975, 1275, -1275, 1575, -1575, 1875, -1875, 2175, -2175,
    2475, -2475, 2775, -2775, 3075, -3075, 3375, -3375, 3675, -3675, 3975, -3975, 4275, -4275, 4650, -4650,
    149, -149, 498, -498, 896, -896, 1294, -1294, 1692, -1692, 2090, -2090, 2488, -2488, 2886, -2886,
    3284, -3284, 3682, -3682, 4080, -4080, 4478, -4478, 4876, -4876, 5274, -5274, 5672, -5672, 6169, -6169,
    193, -193, 643, -643, 1157, -1157, 1671, -1671, 2185, -2185, 2699, -2699, 3213, -3213, 3727, -3727,
    4241, -4241, 4755, -4755, 5269, -5269, 5783, -5783, 6297, -6297, 6811, -6811, 7325, -7325, 7967, -7967,
    242, -242, 808, -808, 1454, -1454, 2100, -2100, 2746, -2746, 3392, -3392, 4038, -4038, 4684, -4684,
    5330, -5330, 5976, -5976, 6622, -6622, 7268, -7268, 7914, -7914, 8560, -8560, 9206, -9206, 10013, -10013,
    299, -299, 995, -995, 1791, -1791, 2587, -2587, 3383, -3383, 4179, -4179, 4975, -4975, 5771, -5771,
    6567, -6567, 7363, -7363, 8159, -8159, 8955, -8955, 9751, -9751, 10547, -10547, 11343, -11343, 12338, -12338,
    362, -362, 1208, -1208, 2174, -2174, 3140, -3140, 4106, -4106, 5072, -5072, 6038, -6038, 7004, -7004,
    7970, -7970, 8936, -8936, 9902, -9902, 10868, -10868, 11834, -11834, 12800, -12800, 13766, -13766, 14973, -14973,
    434, -434, 1445, -1445, 2601, -2601, 3757, -3757, 4913, -4913, 6069, -6069, 7225, -7225, 8381, -8381,
    9537, -9537, 10693, -10693, 11849, -11849, 13005, -13005, 14161, -14161, 15317, -15317, 16473, -16473, 17918, -17918,
    512, -512, 1705, -1705, 3069, -3069, 4433, -4433, 5797, -5797, 7161, -7161, 8525, -8525, 9889, -9889,
    11253, -11253, 12617, -12617, 13981, -13981, 15345, -15345, 16709, -16709, 18073, -18073, 19437, -19437, 21142, -21142,
    598, -598, 1993, -1993, 3587, -3587, 5181, -5181, 6775, -6775, 8369, -8369, 9963, -9963, 11557, -11557,
    13151, -13151, 14745, -14745, 16339, -16339, 17933, -17933, 19527, -19527, 21121, -21121, 22715, -22715, 24707, -24707
]
RECIP = [65536, 13107, 4681, 2340, 1365, 873, 606, 436, 329, 255, 202, 164, 135, 113, 96, 82]


def _quant() -> list[int]:
    lim = 32
    q = [0] * 65
    x = lim - 1
    q[0] = x
    for i in range(1, lim, 2):
        q[i] = q[i + 1] = x
        x -= 2
    x = 0
    for i in range(lim, 2 * lim, 2):
        q[i] = q[i + 1] = x
        x += 2
    q[2 * lim] = x - 2
    return q


QUANT = _quant()


def sea_encode(pcm, progress: Callable[[int, int], None] | None = None) -> bytes:
    """int16-Folge (32 kHz) -> SEA5-Blöcke, Byte für Byte wie fm1_sea_encode() der Workbench."""
    pcm = [int(v) for v in pcm]
    frames = len(pcm)
    if not frames:
        return b""
    blocks = (frames + CHUNK_FRAMES - 1) // CHUNK_FRAMES
    out = bytearray(blocks * CHUNK_BYTES)
    pcm += [0] * (blocks * CHUNK_FRAMES - frames)
    h0 = h1 = h2 = h3 = 0
    w0, w1, w2, w3 = 0, 0, -8192, 16384
    previous = 0
    quant, recip = QUANT, RECIP
    for block in range(blocks):
        base = block * CHUNK_BYTES
        out[base:base + 4] = BLOCK_MAGIC
        struct.pack_into("<8H", out, base + 4, *[v & 0xFFFF for v in (h0, h1, h2, h3, w0, w1, w2, w3)])
        for group in range(16):
            p0 = block * CHUNK_FRAMES + group * 20
            seg = pcm[p0:p0 + 20]
            best_rank = None
            best = None
            for search in range(16):
                sf = (search + previous) & 15
                rec = recip[sf]
                dq = DQT[sf * 32:sf * 32 + 32]
                a0, a1, a2, a3, b0, b1, b2, b3 = h0, h1, h2, h3, w0, w1, w2, w3
                rank = 0
                codes = []
                ok = True
                for s in seg:
                    pred = (a0 * b0 + a1 * b1 + a2 * b2 + a3 * b3) >> 13
                    v = s - pred
                    n = (v * rec + 32768) >> 16
                    sc = n + ((v > 0) - (v < 0)) - ((n > 0) - (n < 0))
                    if sc > 32:
                        sc = 32
                    elif sc < -32:
                        sc = -32
                    q = quant[32 + sc]
                    res = dq[q]
                    r = pred + res
                    if r > 32767:
                        r = 32767
                    elif r < -32768:
                        r = -32768
                    e = s - r
                    pen = ((b0 * b0 + b1 * b1 + b2 * b2 + b3 * b3) >> 18) - 0x8FF
                    rank += e * e + (pen * pen if pen > 0 else 0)
                    if best_rank is not None and rank > best_rank:
                        ok = False
                        break
                    codes.append(q)
                    d = res >> 4
                    b0 += -d if a0 < 0 else d
                    b1 += -d if a1 < 0 else d
                    b2 += -d if a2 < 0 else d
                    b3 += -d if a3 < 0 else d
                    b0 = 32767 if b0 > 32767 else (-32768 if b0 < -32768 else b0)
                    b1 = 32767 if b1 > 32767 else (-32768 if b1 < -32768 else b1)
                    b2 = 32767 if b2 > 32767 else (-32768 if b2 < -32768 else b2)
                    b3 = 32767 if b3 > 32767 else (-32768 if b3 < -32768 else b3)
                    a0, a1, a2, a3 = a1, a2, a3, r
                if ok and (best_rank is None or rank < best_rank):
                    best_rank = rank
                    best = (sf, codes, a0, a1, a2, a3, b0, b1, b2, b3)
            sf, codes, h0, h1, h2, h3, w0, w1, w2, w3 = best
            previous = sf
            out[base + 20 + group // 2] |= sf << (0 if group & 1 else 4)
            for i, q in enumerate(codes):
                at = (group * 20 + i) * 5
                packed = q << (11 - (at & 7))
                out[base + 28 + at // 8] |= (packed >> 8) & 255
                if (at & 7) > 3:
                    out[base + 29 + at // 8] |= packed & 255
        if progress and block % 16 == 0:
            progress(block, blocks)
    return bytes(out)


def sea_decode(data: bytes) -> list[int]:
    """SEA5-Blöcke -> int16 (wie fm1_sea_decode)."""
    out = []
    for c in range(0, len(data) - CHUNK_BYTES + 1, CHUNK_BYTES):
        ch = data[c:c + CHUNK_BYTES]
        if ch[:4] != BLOCK_MAGIC:
            raise FM1OpenError("SEA5-Block ungültig")
        st = list(struct.unpack_from("<8h", ch, 4))
        for g in range(16):
            scale = (ch[20 + g // 2] >> (0 if g & 1 else 4)) & 15
            for i in range(20):
                at = (g * 20 + i) * 5
                code = 0
                for b in range(5):
                    code = (code << 1) | ((ch[28 + (at + b) // 8] >> (7 - (at + b) % 8)) & 1)
                pred = (st[0] * st[4] + st[1] * st[5] + st[2] * st[6] + st[3] * st[7]) >> 13
                res = DQT[(scale << 5) + code]
                v = max(-32768, min(32767, pred + res))
                d = res >> 4
                for j in range(4):
                    st[4 + j] = max(-32768, min(32767, st[4 + j] + (-d if st[j] < 0 else d)))
                st[0], st[1], st[2], st[3] = st[1], st[2], st[3], v
                out.append(v)
    return out


def chunk_bytes(frames: int) -> int:
    return (frames + CHUNK_FRAMES - 1) // CHUNK_FRAMES * CHUNK_BYTES


# =================================================================== Bank ===

@dataclass
class SampleEntry:
    name: str = ""
    frames: int = 0
    data: bytes = b""

    @property
    def empty(self) -> bool:
        return not self.frames

    @property
    def seconds(self) -> float:
        return self.frames / RATE


def bank_crc(data: bytes) -> int:
    """CRC-32 über die Bank, CRC- und Commit-Feld (Byte 12–19) als Null gezählt."""
    return zlib.crc32(data[20:], zlib.crc32(bytes(min(8, max(0, len(data) - 12))), zlib.crc32(data[:12]))) & 0xFFFFFFFF


def read_bank(data: bytes) -> list[SampleEntry]:
    """prüft eine Bank wie die Workbench (Samples.js read) -> 8 Einträge."""
    data = bytes(data)
    if not HEADER <= len(data) <= REGION:
        raise FM1OpenError("Sample-Bank: Länge ungültig")
    magic, ver, size, crc, commit, rate, r0, r1 = struct.unpack_from("<8I", data)
    if magic != MAGIC or ver != 1 or size != len(data) or commit or rate != RATE or r0 or r1:
        raise FM1OpenError("Sample-Bank: Format ungültig (keine FM1-Open-Bank?)")
    if crc != bank_crc(data):
        raise FM1OpenError("Sample-Bank: Prüfsumme falsch (Datei beschädigt)")
    if any(data[480:512]):
        raise FM1OpenError("Sample-Bank: reservierte Felder belegt")
    nxt, out = HEADER, []
    for i in range(DISK_SLOTS):
        at = 32 + i * 28
        off, frames, n = struct.unpack_from("<3I", data, at)
        name = data[at + 12:at + 28]
        if (off != nxt or frames > MAX_FRAMES or n != chunk_bytes(frames) or n > len(data) - nxt or name[15]
                or any(b and not 32 <= b <= 126 for b in name)):
            raise FM1OpenError(f"Sample-Bank: Slot {i + 1} ungültig")
        for p in range(off, off + n, CHUNK_BYTES):
            if data[p:p + 4] != BLOCK_MAGIC:
                raise FM1OpenError("Sample-Bank: SEA5-Block ungültig")
        txt = name.split(b"\0", 1)[0].decode("ascii")
        if i >= SLOTS and (frames or txt):
            raise FM1OpenError("Sample-Bank: Format ungültig")
        out.append(SampleEntry(txt, frames, data[off:off + n]))
        nxt += n
    if nxt != len(data):
        raise FM1OpenError("Sample-Bank: überzählige Daten")
    return out[:SLOTS]


def clean_name(name: str) -> str:
    return re.sub(r"[^\x20-\x7e]", "_", name)[:15]


def build_bank(entries: list[SampleEntry], capacity: int = CAPACITY) -> bytes:
    """8 Einträge -> Bank (wie Samples.js encodeBank)."""
    if len(entries) != SLOTS:
        raise FM1OpenError("Die Bank braucht genau 8 Slots")
    size = HEADER + sum(len(e.data) for e in entries)
    if size > capacity:
        raise FM1OpenError(f"Zu lang: {used_seconds(entries):.2f} s – alle 8 Slots zusammen fassen "
                           f"{capacity_seconds(capacity):.2f} s. Kürzen oder andere Slots leeren.")
    d = bytearray(size)
    struct.pack_into("<3I", d, 0, MAGIC, 1, size)
    struct.pack_into("<I", d, 20, RATE)
    off = HEADER
    for i, e in enumerate(entries):
        at = 32 + i * 28
        struct.pack_into("<3I", d, at, off, e.frames, len(e.data))
        nm = clean_name(e.name).encode("ascii")
        d[at + 12:at + 12 + len(nm)] = nm
        d[off:off + len(e.data)] = e.data
        off += len(e.data)
    for i in range(SLOTS, DISK_SLOTS):
        struct.pack_into("<I", d, 32 + i * 28, off)
    struct.pack_into("<I", d, 12, bank_crc(bytes(d)))
    read_bank(bytes(d))
    return bytes(d)


def empty_bank() -> list[SampleEntry]:
    return [SampleEntry() for _ in range(SLOTS)]


def used_seconds(entries: list[SampleEntry]) -> float:
    return sum(len(e.data) for e in entries) // CHUNK_BYTES * CHUNK_FRAMES / RATE


def capacity_seconds(capacity: int = CAPACITY) -> float:
    return (capacity - HEADER) // CHUNK_BYTES * CHUNK_FRAMES / RATE


def free_frames(entries: list[SampleEntry], slot: int, capacity: int = CAPACITY) -> int:
    used = HEADER + sum(len(e.data) for i, e in enumerate(entries) if i != slot)
    return max(0, (capacity - used) // CHUNK_BYTES * CHUNK_FRAMES)


def make_entry(pcm, name: str, progress=None) -> SampleEntry:
    pcm = list(pcm)
    if not pcm:
        raise FM1OpenError("kein Audio")
    if len(pcm) > MAX_FRAMES:
        raise FM1OpenError("zu lang für die Sample-Bank")
    return SampleEntry(clean_name(name), len(pcm), sea_encode(pcm, progress))


# ---------------------------------------------------------- Audio -> PCM ---

def resample(x, sr: int, to: int = RATE):
    """Tiefpass (gefenstertes Sinc) + lineare Interpolation; numpy."""
    import numpy as np
    a = np.asarray(x, dtype=np.float64)
    if sr == to or not len(a):
        return a
    if sr > to:
        fc = 0.45 * to / sr
        n = 63
        k = np.arange(n) - (n - 1) / 2
        h = 2 * fc * np.sinc(2 * fc * k) * np.blackman(n)
        a = np.convolve(a, h / h.sum(), mode="same")
    m = int(len(a) * to / sr)
    return np.interp(np.arange(m) * (sr / to), np.arange(len(a)), a)


def to_pcm(x) -> list[int]:
    """float -> int16 wie die Workbench: round(v * 32768), begrenzt."""
    import numpy as np
    a = np.clip(np.round(np.asarray(x, dtype=np.float64) * 32768), -32768, 32767)
    return a.astype(np.int64).tolist()


def render_zone(zone) -> list[int]:
    """Kit-Zone (Datei + Bearbeitung) -> int16 bei 32 kHz für einen FM1-Open-Slot."""
    from pathlib import Path
    from . import samples as S
    sr, x = S.read_mono(Path(zone.path))
    y = S.apply_edit(sr, x, zone.edit)
    if not y:
        raise FM1OpenError(f"{Path(zone.path).name}: kein Audio übrig")
    return to_pcm(resample(y, sr))


# ================================================================= Gerät ===

class Client:
    """Datei-Protokoll über einen Link (transport.Link). Nur aus einem Hintergrund-Thread benutzen,
    mit link.hold()/exclusive() (ctx.job macht das)."""

    def __init__(self, link, timeout: float = 1.0, attempts: int = 3):
        self.link = link
        self.timeout = timeout
        self.attempts = attempts
        self.token = random.randrange(16384)
        self.q: queue.Queue = queue.Queue()
        self.capacity = CAPACITY_V13
        self.version = 0

    def __enter__(self):
        self.link.add_listener(self._listen)
        return self

    def __exit__(self, *a):
        self.link.remove_listener(self._listen)

    def _listen(self, data, direction):
        if direction == "in" and data and data[0] == 0xF0 and len(data) > 5 and data[2:4] == [0x46, 0x4D]:
            self.q.put(list(data))

    def _send(self, msg):
        if hasattr(self.link, "send_midi"):
            if not self.link.send_midi(msg, force=True):
                raise FM1OpenError("Gerät nicht verbunden")
        else:
            self.link.send(msg)

    def query(self, cmd: int, payload=b"", empty_ok: bool = False) -> bytes:
        self.token = (self.token + 1) & 16383
        tok = self.token
        msg = frame(cmd, tok, bytes(payload))
        while not self.q.empty():
            self.q.get_nowait()
        for _ in range(self.attempts):
            self._send(msg)                      # Wiederholung: gleiches Token -> Gerät schickt die gespeicherte Antwort
            end = time.monotonic() + self.timeout
            while True:
                left = end - time.monotonic()
                if left <= 0:
                    break
                try:
                    r = parse(self.q.get(timeout=left))
                except queue.Empty:
                    break
                if not r or r[0] != (cmd | 64) or r[1] != tok:
                    continue
                data = r[2]
                if not data:
                    raise FM1OpenError("Antwort unvollständig")
                code = data[0]
                if code and not (empty_ok and code == 1):
                    raise FM1OpenError(ERRORS.get(code, f"Gerät meldet Fehler {code}"), code)
                return data[1:]
        raise FM1OpenError("Keine Antwort – läuft FM1 Open auf dem FM-1?")

    # ---- Abfragen
    def probe(self) -> dict:
        """Befehl 0 (+21/22): erkennt FM1 Open. -> Infos"""
        info = self.query(0)
        if len(info) != 14 or info[1] != 16 or not ((info[0] == 3 and info[3] | info[4] << 8 == 4048) or
                                                     (info[0] == 4 and info[3] | info[4] << 8 == 4064)):
            raise FM1OpenError("Antwort passt nicht zu FM1 Open")
        self.version = info[0]
        self.capacity = CAPACITY_V13
        try:
            ext = self.query(22)
            if len(ext) == 9 and ext[0] == 1 and struct.unpack_from("<I", ext, 4)[0] == CAPACITY:
                self.capacity = CAPACITY
        except FM1OpenError as e:
            if e.code != 71:
                raise
        return {"version": info[0], "project": info[2], "capacity": self.capacity}

    def sample_status(self) -> tuple[int, int]:
        d = self.query(11)
        if len(d) != 9 or d[0] > 4:
            raise FM1OpenError("Sample-Status: Antwort ungültig")
        size, region = struct.unpack_from("<II", d, 1)
        if region != REGION or (size and not (d[0] == 2 and HEADER <= size <= REGION)):
            raise FM1OpenError("Sample-Bereich passt nicht – nichts verändert")
        return d[0], size

    def read_bytes(self, start: int, n: int) -> bytes:
        """n Bytes ab start aus der Bank lesen (Befehl 15, Stücke zu 64 Byte)."""
        out = bytearray()
        for at in range(start, start + n, 64):
            k = min(64, start + n - at)
            p = self.query(15, struct.pack("<IB", at, k))
            if len(p) != k:
                raise FM1OpenError("Sample-Bank: Stück unvollständig")
            out += p
        return bytes(out)

    def unchanged(self, cached: bytes) -> bool:
        """Stimmt die Bank auf dem Gerät noch mit cached überein? Prüft nur den 512-Byte-Kopf (Größe + CRC-32
        über die ganze Bank) – statt die ganze Bank noch einmal zu lesen."""
        st, size = self.sample_status()
        if st != 2:
            return not cached
        if not cached or len(cached) != size:
            return False
        return self.read_bytes(0, HEADER) == cached[:HEADER]

    def read_bank(self, progress=None, cancel=lambda: False) -> bytes:
        st, size = self.sample_status()
        if st != 2:
            raise FM1OpenError(f"Sample-Bank nicht bereit ({STATUS_TEXT.get(st, st)})")
        out = bytearray()
        for at in range(0, size, 64):
            if cancel():
                raise FM1OpenError("abgebrochen")
            n = min(64, size - at)
            p = self.query(15, struct.pack("<IB", at, n))
            if len(p) != n:
                raise FM1OpenError("Sample-Bank: Stück unvollständig")
            out += p
            if progress:
                progress(at + n, size)
        return bytes(out)

    def write_bank(self, data: bytes, progress=None, cancel=lambda: False, verify: bool = True):
        """ganze Bank schreiben (löscht zuerst!) und zurücklesen."""
        read_bank(data)
        if len(data) > self.capacity:
            raise FM1OpenError("Bank zu groß für dieses Gerät")
        st, _ = self.sample_status()
        if st in (1, 3):
            raise FM1OpenError("Sample-Bank ist gerade beschäftigt – kurz warten")
        n = len(data)
        total = n * (2 if verify else 1)
        self.query(12, struct.pack("<I", n))
        try:
            for at in range(0, n, 128):
                if cancel():
                    raise FM1OpenError("Abgebrochen – die Bank auf dem FM-1 ist jetzt leer/ungültig. "
                                       "Bitte das Backup zurückspielen.")
                self.query(13, struct.pack("<I", at) + data[at:at + 128])
                if progress:
                    progress(min(n, at + 128), total)
            self.query(14)
        finally:
            try:
                self.query(16)
            except FM1OpenError:
                pass
        if verify:
            for _ in range(20):
                st, size = self.sample_status()
                if st != 1:
                    break
                time.sleep(0.25)
            back = self.read_bank(lambda a, b: progress(n + a, total) if progress else None)
            if back != data:
                raise FM1OpenError("Zurückgelesene Bank weicht ab – Schreiben nicht bestätigt, bitte wiederholen")


def detect(link, timeout: float = 0.8) -> dict | None:
    """True-ish, wenn am Link FM1 Open antwortet (aus Hintergrund-Thread, Link reserviert)."""
    try:
        with Client(link, timeout=timeout, attempts=1) as c:
            return c.probe()
    except FM1OpenError:
        return None
