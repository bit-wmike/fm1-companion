# SPDX-License-Identifier: GPL-3.0-only
"""A stand-in for an FM-1 in m-bootloader's MIDI MODE, written from the specification (m-bootloader docs/SPEC.md
§11.6) for the tests of fm1companion/flash/mboot.py – none of m-bootloader's code. An in-process transport
(``send`` / ``receiver``), no USB.

It answers what the specification says MIDI MODE answers outside an install: 0x11 (identity), 0x12 (MBOOT …),
0x23 (read ≤ 512 bytes), 0x24 (CRC-32 of a range); everything else, and anything refused, gets command 0 with the
body 01. It counts what would be dangerous on other units (``writes``: 0x21 / 0x22, ``installs``: F0 22 24 35 7F,
``restarts``: … 7E, ``downloads``: … 7D) – a test asserts they stay 0.

With ``install=True`` the start key F0 22 24 35 7F F7 starts an install as SPEC §11.6 describes it (and as
m-bootloader's src/midi_update.c does it – written again here, none of its code): it asks the host for the package
with 0x30 requests (≤ 512 bytes), checks the header, the entry, the chip key and flash.bin's CRC-16 after reading it
all once, and only then erases 0x4000 and writes [0x5000, size), the first sector last, each sector only where it
differs, and asks for FINISH (0xF0000000). As midi_update.c it erases 0x4000 right after the last answer of the
check pass, before any further request, and it never sends a refusal frame during an install – it simply stops
asking. Its faults (``fail``): "crc" (a byte of the package arrives changed: flash.bin's CRC does not match – it stops
asking, nothing erased), "wrong-key" (it checks with another chip key – it stops asking), "silent" (never asks),
"erase-fail" (the first erase of 0x4000 happens, then it stops: MU_FLASH_ERROR), "stop-writing" (no more requests
after two sectors were written). ``erased`` lists what it erased.

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


def crc16(d, c=0):
    for b in d:
        c ^= b << 8
        for _i in range(8):
            c = ((c << 1) ^ 0x1021) & 0xFFFF if c & 0x8000 else (c << 1) & 0xFFFF
    return c


def enc(d: bytes, key: int) -> bytes:
    out, k = bytearray(d), key & 0xFFFF
    for i in range(len(out)):
        out[i] ^= k & 0xFF
        k = ((k << 1) ^ 0x1021) & 0xFFFF if k & 0x8000 else (k << 1) & 0xFFFF
    return bytes(out)


class FakeMboot:
    def __init__(self, flash: bytes, jedec: int = 0x856014, version: str = "2.4-test", kind: int = 1,
                 answer_0x12: bool = True, corrupt_at: int = -1, install: bool = False, fail: str = "",
                 key: int = KEY):
        self.key = key
        self.uid_bytes = bytes(range(0xA0, 0xA8))
        self.install_ok, self.fail = install, fail
        self.erased: list[int] = []
        self.finished = 0
        self._want = None                      # (address, length) of the request waiting for its answer
        self._gen = None
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
            if d[4] == 0x7F and self.install_ok and self._gen is None:
                self._gen = self._install()
                self._step(None)
            return
        m = unpack(d[1:-1])
        if len(m) < 7 or m[0] != 0 or m[1] != 0x59 or (m[3] | m[4] << 8) != len(m) - 7 or m[5]:
            return
        if sum(m[6:]) & 0xFF != 0xFF:
            return
        cmd, body = m[2], m[6:-1]
        self.commands.append(cmd)
        if cmd == 0x30 and self._gen is not None:
            if self._want and len(body) == 8 + self._want[1] and int.from_bytes(body[1:5], "little") == self._want[0]:
                self._step(bytes(body[8:]))
            return
        if cmd in (0x21, 0x22):
            self.writes += 1
        if cmd == 0x11 and not body:
            self._answer(0x11, b"ota-FM-1_900".ljust(27, b"\0"))
        elif cmd == 0x12 and not body and self.answer_0x12:
            info = b"MBOOT" + bytes([1, self.kind, 1]) + KEY.to_bytes(2, "little") + \
                len(self.flash).to_bytes(4, "little") + self.jedec.to_bytes(4, "little") + self.uid_bytes + \
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

    # ---- the install (SPEC §11.6): a generator that yields its requests and gets the answers back
    def _step(self, data):
        try:
            req = self._gen.send(data) if data is not None else next(self._gen)
        except StopIteration:
            self._gen = self._want = None
            return
        if req is None:                         # (it stops asking)
            self._gen = self._want = None
            return
        if req == "refuse":
            self._gen = self._want = None
            return self._answer(0, b"\x01")
        self._want = req
        addr, n = req
        self._answer(0x30, bytes([0]) + addr.to_bytes(4, "little") + n.to_bytes(3, "little"))

    def _install(self):
        if self.fail == "silent":
            yield None
        head = b""
        for at in range(0, 0x400, 512):
            head += yield (at, 512)
        h = enc(head[:0x40], 0xFFFF)
        count = h[8] | h[9] << 8
        if not count or count > 11 or crc16(h[2:0x40]) != (h[0] | h[1] << 8):
            yield None
        e = enc(head[0x40:0x40 + 0x50], 0xFFFF)
        crc, off, size = e[4] | e[5] << 8, int.from_bytes(e[8:12], "little"), int.from_bytes(e[12:16], "little")
        if e[0] | e[1] << 8 or size <= 0x4000 or size > 0xFF000 or size % 0x1000:
            yield None
        r = enc((yield (off + 0x4000, 32)), KEY ^ 0x5555 if self.fail == "wrong-key" else self.key)
        if crc16(r[2:32]) != (r[0] | r[1] << 8):
            yield None
        whole, scrc = 0, {}
        for at in range(0, size, 0x1000):
            sec = b""
            for i in range(0, 0x1000, 512):
                part = yield (off + at + i, 512)
                if self.fail == "crc" and at == 0x8000 and i == 0:
                    part = bytes([part[0] ^ 1]) + part[1:]
                sec += part
            whole = crc16(sec, whole)
            scrc[at] = crc16(sec)
        if whole != crc:
            yield None
        img = bytearray(self.flash)
        img[0x4000:0x5000] = b"\xff" * 0x1000
        self.erased.append(0x4000)
        self.flash = bytes(img)
        if self.fail == "erase-fail":
            yield None
        written = 0
        for at in list(range(0x5000, size, 0x1000)) + [0x4000]:
            sec = b""
            for i in range(0, 0x1000, 512):
                sec += yield (off + at + i, 512)
            if crc16(sec) != scrc[at]:
                yield None
            if img[at:at + 0x1000] != sec:
                img[at:at + 0x1000] = sec
                if at != 0x4000:
                    self.erased.append(at)
                self.flash = bytes(img)
                written += 1
            if written == 2 and self.fail == "stop-writing":
                yield None
        yield (0xF0000000, 8)
        self.finished += 1

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
