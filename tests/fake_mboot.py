# SPDX-License-Identifier: GPL-3.0-only
"""A stand-in for an FM-1 in m-bootloader's MIDI MODE, written from the specification (m-bootloader docs/SPEC.md
§11.6) for the tests of fm1companion/flash/mboot.py – none of m-bootloader's code. An in-process transport
(``send`` / ``receiver``), no USB.

It answers what the specification says MIDI MODE answers outside an install: 0x11 (identity), 0x12 (MBOOT …),
0x23 (read ≤ 512 bytes), 0x24 (CRC-32 of a range); everything else, and anything refused, gets command 0 with the
body 01. It counts what would be dangerous on other units (``writes``: 0x21 / 0x22, ``installs``: F0 22 24 35 7F,
``restarts``: … 7E, ``downloads``: … 7D) – a test asserts they stay 0.

The app's module was also run against m-bootloader's own MIDI mode code (src/midi_update.c of 2.4, compiled for the
PC with a flash file behind it): see docs/UNTESTED.md, "SLOOP 3".
"""
from __future__ import annotations

import zlib

KEY = 0x980F


def pack(raw: bytes) -> list[int]:
    out, acc, bits = [], 0, 0
    for b in raw:
        acc |= b << bits
        bits += 8
        while bits >= 7:
            out.append(acc & 0x7F)
            acc >>= 7
            bits -= 7
    if bits:
        out.append(acc & 0x7F)
    return out


def unpack(d) -> bytes:
    out, acc, bits = bytearray(), 0, 0
    for b in d:
        acc |= (b & 0x7F) << bits
        bits += 7
        if bits >= 8:
            out.append(acc & 0xFF)
            acc >>= 8
            bits -= 8
    return bytes(out)


class FakeMboot:
    def __init__(self, flash: bytes, jedec: int = 0x856014, version: str = "2.4-test", kind: int = 1,
                 answer_0x12: bool = True, corrupt_at: int = -1):
        self.flash = bytes(flash)
        self.jedec, self.version, self.kind = jedec, version, kind
        self.answer_0x12 = answer_0x12
        self.corrupt_at = corrupt_at           # a read of this address returns a wrong byte (a bad cable)
        self.receiver = None
        self.closed = False
        self.seen: list[bytes] = []
        self.commands: list[int] = []
        self.writes = self.installs = self.restarts = self.downloads = 0

    # ---- the transport's side
    def send(self, data):
        d = bytes(data)
        self.seen.append(d)
        if len(d) == 6 and d[:4] == bytes([0xF0, 0x22, 0x24, 0x35]):
            self.installs += d[4] == 0x7F
            self.downloads += d[4] == 0x7D
            self.restarts += d[4] == 0x7E
            return
        m = unpack(d[1:-1])
        if len(m) < 7 or m[0] != 0 or m[1] != 0x59 or (m[3] | m[4] << 8) != len(m) - 7 or m[5]:
            return
        if sum(m[6:]) & 0xFF != 0xFF:
            return
        cmd, body = m[2], m[6:-1]
        self.commands.append(cmd)
        if cmd in (0x21, 0x22):
            self.writes += 1
        if cmd == 0x11 and not body:
            self._answer(0x11, b"ota-FM-1_900".ljust(27, b"\0"))
        elif cmd == 0x12 and not body and self.answer_0x12:
            info = b"MBOOT" + bytes([1, self.kind, 1]) + KEY.to_bytes(2, "little") + \
                len(self.flash).to_bytes(4, "little") + self.jedec.to_bytes(4, "little") + bytes(range(0xA0, 0xA8)) + \
                b"\0\0" + self.version.encode() + b"\0"
            self._answer(0x12, info)
        elif cmd in (0x23, 0x24) and self.answer_0x12:
            addr, count = int.from_bytes(body[1:5], "little"), int.from_bytes(body[5:8], "little")
            if len(body) != 8 or body[0] or not count or addr >= len(self.flash) or count > len(self.flash) - addr \
                    or (cmd == 0x23 and count > 512):
                return self._answer(0, b"\x01")
            part = bytearray(self.flash[addr:addr + count])
            if cmd == 0x23:
                if addr <= self.corrupt_at < addr + count:
                    part[self.corrupt_at - addr] ^= 0x40
                self._answer(cmd, body + bytes(part))
            else:
                self._answer(cmd, body + (zlib.crc32(bytes(part)) & 0xFFFFFFFF).to_bytes(4, "little"))
        elif cmd != 0x30 and self.answer_0x12:
            self._answer(0, b"\x01")

    def _answer(self, cmd: int, body: bytes):
        n = len(body)
        raw = bytes([0, 0x59, cmd, n & 255, n >> 8 & 255, 0]) + body + bytes([~sum(body) & 255])
        if self.receiver:
            self.receiver([0xF0, *pack(raw), 0xF7])

    def close(self):
        self.closed = True

    @property
    def alive(self):
        return not self.closed
