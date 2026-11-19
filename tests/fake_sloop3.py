#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""A stand-in FM-1 that runs SLOOP 3, for the tests: one MIDI message per line as hex on stdin/stdout (the app's
SubprocessTransport).

It is not SLOOP 3: it replays what SLOOP 3's PC twin answered (tests/fixtures/sloop3/twin-3.3.7.json – INFO, the
lists, a sound, an engine's description, a pattern, the song, the settings) and keeps the files and
bank images the way fw/main.c's ed_request does (READ, WBEGIN/WDATA/WEND, BANK 0–4: lengths, offsets, CRC-32, the
"busy" and "bad" refusals that matter to a host). LIVE (15) is not replayed but built as ed_request's ED_LIVE of
3.4.0 lays the state out (op, 45 bytes, 22 per track, 16 pads) from ``Fake.live``; the ops it got are in ``live_ops``. Like SLOOP 3 it answers nothing that is not its own protocol
(app/edproto.c ed_parse) – the twin left the Felucca INFO, SLOOP 2's and Felucca's backup LIST, Hortator's INFO and the
update handshake unanswered (the fixture's "silent").

    fake_sloop3.py [--log FILE] [--version 3.3.7] [--samples]

--log: every message received, one hex line each (what did the app send?). --samples: slot USR1 holds a sample.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import zlib
from pathlib import Path

HDR = bytes([0xF0, 0x7D, 0x53, 0x33])
FIX = Path(__file__).resolve().parent / "fixtures" / "sloop3" / "twin-3.3.7.json"
FM6_LEN, SYN_LEN, PMAX = 16 + 27 * 128, 16 + 4 * (4 + 16 * 22), 52992
BAD, BUSY, NONE, CRC, FILE = 1, 2, 3, 4, 5


def pack(raw: bytes) -> bytes:
    out, acc, bits = bytearray(), 0, 0
    for b in raw:
        acc |= b << bits
        bits += 8
        while bits >= 7:
            out.append(acc & 0x7F)
            acc >>= 7
            bits -= 7
    if bits:
        out.append(acc & 0x7F)
    return bytes(out)


def unpack(d: bytes) -> bytes:
    out, acc, bits = bytearray(), 0, 0
    for b in d:
        acc |= (b & 0x7F) << bits
        bits += 7
        if bits >= 8:
            out.append(acc & 0xFF)
            acc >>= 8
            bits -= 8
    return bytes(out)


def u16(a, i):
    return a[i] | a[i + 1] << 8


def u32(a, i):
    return a[i] | a[i + 1] << 8 | a[i + 2] << 16 | a[i + 3] << 24


def w16(v):
    return bytes([v & 255, v >> 8 & 255])


def w32(v):
    return bytes([v & 255, v >> 8 & 255, v >> 16 & 255, v >> 24 & 255])


class Fake:
    def __init__(self, version: str = "3.3.7", samples: bool = False, fault: str = ""):
        # fault (tests of the restore's honesty): "sounds_dropped" = WEND of your sounds is acknowledged but the old
        # ones stay; "project_differs" = the project that was written reads back with another byte
        self.fault = fault
        fx = json.loads(FIX.read_text("utf-8"))
        self.replay = {bytes.fromhex(k): bytes.fromhex(v) for k, v in fx["replay"].items()}
        o = {k: base64.b64decode(v) for k, v in fx["objects"].items()}
        self.now, self.sounds, self.fm6, self.syn = o["now"], o["sounds"], o["fm6"], o["syn"]
        self.saved = b""                       # no project saved yet, as on a fresh FM-1
        self.fm6_src = self.syn_src = 0
        self.version = version
        self.samples = samples
        self.wr = None                         # a file being written: [kind, total, bytearray]
        self.bk = None                         # a bank image being written: [which, total, bytearray]
        self.out = []
        # LIVE (15): the state its op 0 reports, laid out as fw/main.c ed_request ED_LIVE (3.4.0) writes it; every op
        # received is kept in live_ops (the app may only ever send op 0)
        self.live = {"playing": False, "bpm10": 920, "bar": 0, "beat": 0, "cpu": 13, "peak": 21, "voices": 0,
                     "meters": [0, 0, 0, 0, 0], "patterns": [0, 0, 0, 0, 0], "mute": [0, 0, 0, 0, 0]}
        self.live_ops: list[int] = []

    def live_state(self, op: int) -> bytes:
        v = self.live
        o = bytearray(45)
        o[0] = 1 if v["playing"] else 0
        o[1:3] = w16(v["bpm10"])
        o[3:5] = w16(v["bar"])
        o[5], o[6], o[7], o[8] = v["beat"], 0, 0, 1 if self.saved else 0
        o[9], o[10], o[11] = v["cpu"], v["peak"], v["voices"]
        o[12], o[13] = 255, 255
        o[14], o[15], o[16] = 0, 0, 100
        for t in range(5):
            q = bytearray(22)
            q[0] = q[1] = v["patterns"][t]
            q[2] = v["mute"][t]
            q[4], q[5] = 100, 64
            q[9] = 64
            q[11:13] = b"\xff\xff"
            q[13] = v["meters"][t]
            q[14:16] = w16(1)
            q[16] = 3 if v["playing"] else 255
            q[17] = 16
            q[18], q[19] = 2, 100
            o += q
        o += bytes([0x84]) * 16
        return bytes([op]) + bytes(o)

    def send(self, cmd: int, raw: bytes = b""):
        self.out.append(HDR + bytes([cmd]) + pack(raw) + b"\xF7")

    def err(self, code: int):
        self.send(0, bytes([code]))

    def handle(self, msg: bytes) -> list[bytes]:
        self.out = []
        if len(msg) < 6 or msg[:4] != HDR or msg[-1] != 0xF7 or any(b & 0x80 for b in msg[4:-1]):
            return []                          # not ours: no answer, nothing changes
        cmd, r = msg[4], unpack(msg[5:-1])
        n = len(r)
        if cmd == 1:
            first = unpack(next(v for k, v in self.replay.items() if k[4] == 1)[5:-1])
            self.send(1, first[:4] + w16(1 if self.saved else 0) + first[6:10] + self.version.encode() + b"\0")
        elif cmd == 5:
            self.read(r, n)
        elif cmd == 6:
            if n < 6:
                return self.err(BAD) or self.out
            kind, slot, total = r[0], r[1], u32(r, 2)
            if kind > 1 or (kind == 0 and (slot != 0 or total < 16 or total > PMAX)) or \
                    (kind == 1 and total != len(self.sounds)):
                self.err(BAD)
            else:
                self.wr = [kind, total, bytearray()]
                self.send(6, r[:6])
        elif cmd == 7:
            if not self.wr or n < 4 or u32(r, 0) != len(self.wr[2]) or u32(r, 0) + n - 4 > self.wr[1]:
                self.err(BAD)
            else:
                self.wr[2] += r[4:]
                self.send(7, r[:4] + w32(len(self.wr[2])))
        elif cmd == 8:
            if not self.wr or n < 4:
                return self.err(BAD) or self.out
            kind, total, data = self.wr
            self.wr = None
            if len(data) != total or zlib.crc32(bytes(data)) & 0xFFFFFFFF != u32(r, 0):
                self.err(CRC)
            elif kind == 1 and bytes(data[:4]) != b"S3US":
                self.err(FILE)
            elif kind == 0 and bytes(data[:2]) != self.now[:2]:
                self.err(FILE)
            else:
                if kind == 1:
                    if self.fault != "sounds_dropped":
                        self.sounds = bytes(data)
                else:
                    self.saved = self.now = bytes(data)
                    if self.fault == "project_differs":
                        self.saved = self.saved[:-1] + bytes([self.saved[-1] ^ 1])
                self.send(8, r[:4])
        elif cmd == 14:
            self.bank(r, n)
        elif cmd == 15:
            op = r[0] if n else 0
            self.live_ops.append(op)
            self.send(15, self.live_state(op))
        elif cmd == 13 and n and r[0] == 0:
            body = bytearray([0, 4, 64])
            for i in range(4):
                held = self.samples and i == 0
                body += bytes([1 if held else 0]) + (b"BREAK\0\0\0" if held else bytes(8)) + w16(40 if held else 0)
            self.send(13, bytes(body))
        elif msg in self.replay:
            self.out.append(self.replay[msg])
        elif cmd == 19 and n == 0:
            self.out.append(next(v for k, v in self.replay.items() if k[4] == 19))
        else:
            self.err(BAD)                      # (a request the recording does not hold)
        return self.out

    def read(self, r: bytes, n: int):
        if n < 8:
            return self.err(BAD)
        kind, slot, off, ln = r[0], r[1], u32(r, 2), u16(r, 6)
        if kind > 2 or (kind == 0 and slot != 0) or ln > 384:
            return self.err(BAD)
        data = (self.saved, self.sounds, self.now)[kind]
        if not data:
            return self.err(NONE)
        if off > len(data):
            return self.err(BAD)
        part = data[off:off + ln]
        self.send(5, r[:6] + w32(len(data)) + w32(zlib.crc32(data) & 0xFFFFFFFF) + part)

    def bank(self, r: bytes, n: int):
        sub = r[0] if n else 255
        which = r[1] if n > 1 else 255
        if sub == 0:
            used = u32(self.fm6, 8) if self.fm6_src else 0
            return self.send(14, bytes([0, self.fm6_src]) + w32(used) + bytes([self.syn_src]))
        if which > 1:
            return self.err(BAD)
        img = self.syn if which else self.fm6
        want = SYN_LEN if which else FM6_LEN
        if sub == 1:
            if n < 6 or u16(r, 2) > want:
                return self.err(BAD)
            off, m = u16(r, 2), min(u16(r, 4), 384, want - u16(r, 2))
            return self.send(14, bytes([1, which]) + w16(off) + w16(want) + img[off:off + m])
        if sub == 2:
            if n < 4 or u16(r, 2) != want:
                return self.err(BAD)
            self.bk = [which, want, bytearray()]
            return self.send(14, bytes([2, which]))
        if sub == 3:
            if not self.bk or self.bk[0] != which or n < 4 or u16(r, 2) != len(self.bk[2]) or \
                    u16(r, 2) + n - 4 > self.bk[1]:
                return self.err(BAD)
            self.bk[2] += r[4:]
            return self.send(14, bytes([3, which]) + w16(len(self.bk[2])))
        if sub == 4:
            if not self.bk or self.bk[0] != which:
                return self.err(BAD)
            data = bytes(self.bk[2])
            self.bk = None
            magic = b"S3SY" if which else b"S3F6"
            if len(data) != want or data[:4] != magic or zlib.crc32(data[16:]) & 0xFFFFFFFF != u32(data, 12):
                return self.err(CRC)
            if which:
                self.syn, self.syn_src = data, 2
            else:
                self.fm6, self.fm6_src = data, 2
            return self.send(14, bytes([4, which]))
        return self.err(BAD)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default="")
    ap.add_argument("--version", default="3.3.7")
    ap.add_argument("--samples", action="store_true")
    a = ap.parse_args()
    fake = Fake(a.version, a.samples)
    log = open(a.log, "a") if a.log else None
    print("# fake SLOOP 3 ready", flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            msg = bytes.fromhex(line)
        except ValueError:
            continue
        if log:
            log.write(line + "\n")
            log.flush()
        for out in fake.handle(msg):
            print(out.hex(), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


class InProcess:
    """the fake as a transport in the test's own process (transport.Transport: send / receiver), every message it
    received in ``seen``; silent=True: a device that answers nothing at all"""

    def __init__(self, fake: Fake | None = None, silent: bool = False):
        self.fake, self.silent = fake or Fake(), silent
        self.receiver = None
        self.seen: list[bytes] = []
        self.closed = False

    def send(self, data):
        self.seen.append(bytes(data))
        if self.silent:
            return
        for out in self.fake.handle(bytes(data)):
            if self.receiver:
                self.receiver(list(out))

    def close(self):
        self.closed = True

    @property
    def alive(self):
        return not self.closed
