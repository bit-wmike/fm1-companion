#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Simulated FM-1 with M-VAVE FM1 Open V14 – the device side of the file protocol after
app/transfer.c and app/samples.c (GPL-3.0-only WITH linking exception, (c) 2026 M-VAVE).

Can do: command 0 (project info), 21/22 (capabilities), 11–16 (sample bank: status, begin,
write, commit, read, abort) with token, checksum, bit packing, reply cache for
repeats and the same check of the bank as the firmware (valid(), SEA5 block headers, CRC).
Does NOT speak the Felucca protocol (like the real device).

  python3 tests/fm1open_sim.py     (stdin/stdout: one MIDI message per line as hex)
"""
from __future__ import annotations

import math
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

GROOVE_SAMPLE_BYTES = 466944
GROOVE_SAMPLE_LIMIT = 446464
INVALID, MEMORY = -7, -8
MAX_FRAMES = (466944 - 512) // 228 * 320


class FM1OpenDevice:
    def __init__(self, bank: bytes | None = None, drop_replies: int = 0):
        self.flash = bytearray(b"\xff" * GROOVE_SAMPLE_BYTES)
        self.state, self.total, self.position, self.header = 0, 0, 0, bytearray(512)
        self.cache_req, self.cache_reply = None, None
        self.drop = drop_replies                 # tests: "lose" this many replies
        self.log: list[int] = []
        if bank:
            self.flash[:len(bank)] = bank
        self._load()

    # ---------------------------------------------------- samples.c
    def _valid(self, h: bytes) -> bool:
        magic, ver, nbytes, crc, commit, rate, r0, r1 = struct.unpack_from("<8I", h)
        if magic != 0x53314D46 or ver != 1 or commit or rate != 32000 or nbytes < 512 or \
                nbytes > GROOVE_SAMPLE_BYTES or r0 or r1 or any(h[480:512]):
            return False
        nxt = 512
        for i in range(16):
            off, frames, n = struct.unpack_from("<3I", h, 32 + i * 28)
            name = h[32 + i * 28 + 12:32 + i * 28 + 28]
            if off != nxt or frames > MAX_FRAMES or (i >= 8 and (frames or name[0])) or \
                    n != (frames + 319) // 320 * 228 or n > nbytes - nxt or name[15]:
                return False
            if any(b and not 32 <= b <= 126 for b in name[:15]):
                return False
            nxt += n
        return nxt == nbytes

    @staticmethod
    def _crc(data: bytes) -> int:
        d = bytearray(data)
        d[12:20] = bytes(min(8, max(0, len(d) - 12)))
        return zlib.crc32(bytes(d)) & 0xFFFFFFFF

    def _blocks_ok(self, total) -> bool:
        return all(self.flash[p:p + 4] == b"\x01\x45\x14\x5a" for p in range(512, total, 228))

    def _load(self):
        h = bytes(self.flash[:512])
        if not self._valid(h):
            self.state, self.total = 0, 0
            return
        total = struct.unpack_from("<I", h, 8)[0]
        if self._crc(bytes(self.flash[:total])) != struct.unpack_from("<I", h, 12)[0] or not self._blocks_ok(total):
            self.state, self.total = 4, 0
            return
        self.state, self.total = 2, total

    def begin(self, n):
        if n < 512 or n > GROOVE_SAMPLE_LIMIT:
            return INVALID
        self.flash[:4096] = b"\xff" * 4096          # first sector erased
        self.state, self.total, self.position = 3, n, 0
        self.header = bytearray(512)
        self.log.append(12)
        return 0

    def write(self, off, data):
        if self.state != 3 or not data or len(data) > 128 or off != self.position or len(data) > self.total - off:
            return INVALID
        copy = bytearray(data)
        if off < 512:
            n = min(512 - off, len(data))
            self.header[off:off + n] = data[:n]
            for i in range(len(data)):
                if 16 <= off + i < 20:
                    copy[i] = 255                       # commit word stays erased until the commit
        first, last = off // 4096, (off + len(data) - 1) // 4096
        if off and off % 4096 == 0:
            self.flash[first * 4096:(first + 1) * 4096] = b"\xff" * 4096
        if last != first:
            self.flash[last * 4096:(last + 1) * 4096] = b"\xff" * 4096
        self.flash[off:off + len(data)] = copy
        self.position += len(data)
        return 0

    def finish(self):
        h = bytes(self.header)
        if self.state != 3 or self.position != self.total or struct.unpack_from("<I", h, 8)[0] != self.total or \
                not self._valid(h):
            return INVALID
        d = bytearray(self.flash[:self.total])
        d[16:20] = b"\0\0\0\0"
        if self._crc(bytes(d)) != struct.unpack_from("<I", h, 12)[0] or not self._blocks_ok(self.total):
            return INVALID
        self.flash[16:20] = b"\0\0\0\0"
        self.state = 2
        self.log.append(14)
        return 0

    def read(self, off, n):
        if self.state != 2 or not n or n > 64 or off > self.total or n > self.total - off:
            return INVALID, b""
        return 0, bytes(self.flash[off:off + n])

    # ---------------------------------------------------- transfer.c
    def command(self, cmd, data):
        if cmd == 22 and not data:
            return 0, bytes([1, 128, 4, 1]) + struct.pack("<I", GROOVE_SAMPLE_LIMIT) + bytes([3])
        if cmd == 21 and not data:
            return 0, bytes([1, 4264 & 255, 4264 >> 8]) + struct.pack("<I", GROOVE_SAMPLE_BYTES - 4096)
        if cmd == 0 and not data:
            return 0, bytes([4, 16, 0, 4064 & 255, 4064 >> 8, 0, 0, 1, 2, 3, 4, 5, 0, 1])
        if cmd == 11 and not data:
            return 0, bytes([self.state]) + struct.pack("<II", self.total if self.state == 2 else 0, GROOVE_SAMPLE_BYTES)
        if cmd == 12 and len(data) == 4:
            return self.begin(struct.unpack("<I", data)[0]), b""
        if cmd == 13 and 4 < len(data) <= 132:
            return self.write(struct.unpack_from("<I", data)[0], data[4:]), b""
        if cmd == 14 and not data:
            return self.finish(), b""
        if cmd == 15 and len(data) == 5 and data[4] <= 64:
            return self.read(struct.unpack_from("<I", data)[0], data[4])
        if cmd == 16 and not data:
            if self.state == 3:
                self.state = 4
            return 0, b""
        return INVALID, b""

    def handle(self, m: list[int]) -> list[int] | None:
        n = len(m)
        if n < 10 or n > 192 or m[0] != 0xF0 or m[-1] != 0xF7 or m[1:5] != [0x7D, 0x46, 0x4D, 2] or m[5] >= 64:
            return None
        if any(b >= 128 for b in m[1:-1]) or sum(m[1:-1]) & 127:
            return None
        if self.cache_req == m and self.cache_reply:      # exact repeat: stored reply
            return self.cache_reply
        data, bits, value = bytearray(), 0, 0
        for b in m[8:-2]:
            value |= b << bits
            bits += 7
            if bits >= 8:
                if len(data) == 132:
                    return None
                data.append(value & 255)
                value >>= 8
                bits -= 8
        if value or bits >= 7:
            return None
        r, out = self.command(m[5], bytes(data))
        resp = bytes([64 - r if r < 0 else r]) + (out if r == 0 else b"")
        reply = m[:8]
        reply[5] |= 64
        value = bits = 0
        for b in resp:
            value |= b << bits
            bits += 8
            while bits >= 7:
                reply.append(value & 127)
                value >>= 7
                bits -= 7
        if bits:
            reply.append(value & 127)
        reply.append((-sum(reply[1:])) & 127)
        reply.append(0xF7)
        self.cache_req, self.cache_reply = list(m), reply
        if self.drop > 0:
            self.drop -= 1
            return None
        return reply


def demo_bank() -> bytes:
    """8 short synthetic sounds (like the factory bank: names, lengths in 10 ms steps)."""
    from fm1companion import fm1open as F
    names = ["BWOOM", "PAH", "HEY", "WAH", "HAHA", "YOI", "TSI", "SHAA"]
    ents = []
    for i, nm in enumerate(names):
        n = 1600 + 320 * i
        pcm = [int(12000 * math.sin(2 * math.pi * (80 + 60 * i) * t / 32000) * math.exp(-t / (800 + 200 * i)))
               for t in range(n)]
        ents.append(F.make_entry(pcm, nm))
    return F.build_bank(ents)


def main():
    dev = FM1OpenDevice(demo_bank() if "--empty" not in sys.argv else None)
    print("# FM1 Open simulation ready", flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            r = dev.handle(list(bytes.fromhex(line)))
        except Exception as e:  # noqa: BLE001
            print(f"# error: {e}", flush=True)
            continue
        if r is not None:
            print(bytes(r).hex(), flush=True)


if __name__ == "__main__":
    main()
