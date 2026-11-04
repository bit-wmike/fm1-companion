#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""A simulated FM-1 (Melodee-like) for tests and demo mode without a device.

Supports: INFO, PING, BACKUP_LIST/GET/PUT (57 objects like Melodee 1.6, or with
sample slots like Skiff), SMP_INFO/BEGIN/WRITE/END/ERASE, AUDIO_STATS,
PATTERN, TRACK_STEP, DESC, TRACK_PARAM, PROJECT.

With a SLOOP version ("SLOOP 2.8.3") it behaves like SLOOP: INFO with
protocol byte 12 and the backup commands 34–36 (BK_LIST/GET/PUT, rules as in
firmware/src/editor.c ed_backup) instead of 65–67 – SLOOP does not answer those.

  python3 -m fm1companion.demo_device [--samples N]   (stdin/stdout, one message per line as hex)
"""
from __future__ import annotations

import argparse
import random
import struct
import sys
import zlib

from .protocol import frame, unframe, u32, r32, pack7, unpack7, v14

NSTEP, NTRK = 64, 4
HS_QUERY = bytes([0xF0, 0x00, 0x32, 0x45, 0x00, 0x00, 0x00, 0x40, 0x7F, 0xF7])   # installer workshop


def _identity_frame(text: str) -> bytes:
    """Reply to the update handshake (like felucca-bsp chip/ota.c ota_reply_identity)"""
    from .vendor.fm1_install import pack7
    t = text.encode("latin-1")[:26]
    body = t + bytes(27 - len(t))
    return b"\xF0" + pack7(bytes([0x00, 0x59, 0x11, 27, 0, 0]) + body + bytes([~sum(body) & 0xFF])) + b"\xF7"


def rnd(n, seed):
    return bytes((i * 131 + seed * 17 + (i >> 3)) & 255 for i in range(n))


SLOOP_ENGINES = ["ANALOG", "DIGITAL", "PHASE", "LOFI", "SAMPLE", "VOICE", "TRIO", "WHEEL", "GRAIN", "FM6",
                 "PHYS", "NOISE"]
SLOOP_IDS = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 32, 33, 34, 35]
SLOOP_PROJ = 3816                  # project_t (FUN6)
SLOOP_STAGE = 8192                 # proj_tmp: largest object BK_PUT accepts (samples go via SMP_*)


def fnv1a(b: bytes) -> int:
    s = 0x811C9DC5
    for x in b:
        s = ((s ^ x) * 16777619) & 0xFFFFFFFF
    return s


def sloop_project(seed: int) -> bytes:
    """a valid SLOOP project (FUN6: magic, size, …, FNV-1a sum at the end)"""
    body = bytearray(rnd(SLOOP_PROJ, seed))
    struct.pack_into("<II", body, 0, 0x46554E36, SLOOP_PROJ)
    struct.pack_into("<I", body, SLOOP_PROJ - 4, fnv1a(bytes(body[:SLOOP_PROJ - 4])))
    return bytes(body)


def sloop_settings() -> bytes:
    """persist_t (PER3, 88 bytes) with song order A B C D (4 bars each) from byte 48"""
    b = bytearray((i * 37 + 5) & 0xFF for i in range(88))
    struct.pack_into("<IIII", b, 0, 0x50455233, 0, 0, 0)
    b[48:84] = bytes([4, 0, 0, 0]) + b"".join(bytes([k, 4]) for k in range(4)) + bytes(24)
    struct.pack_into("<I", b, 84, 0x11)
    return bytes(b)


def sloop_bank(names) -> bytes:
    """up_bank_t (UPB1): 16 records of 192 bytes"""
    b = bytearray(struct.pack("<IHH", 0x31425055, 192, 16)) + bytes(16 * 192)
    for k, (name, eng) in enumerate(names):
        r = 8 + k * 192
        b[r:r + 4] = bytes([0xA5, 1, eng, 71])
        b[r + 4:r + 16] = name.encode("ascii")[:12].ljust(12, b"\0")
        b[r + 16:r + 160] = rnd(144, k + 20)
    return bytes(b)


class FakeFM1:
    def __init__(self, samples: int = 0, version: str = "Melodee 1.6.0-rc.7", ids=None, identity: str = "",
                 generic: bool = False):
        self.sloop = "sloop" in version.lower()
        if self.sloop and not version.upper().startswith("FELUCCA"):
            version = "FELUCCA " + version          # this is how SLOOP identifies itself (editor.c ED_INFO)
        self.version = version
        self.identity = identity or ("FM-1_91060" if "melodee" in version.lower() else "FM-1_900")
        self.generic = generic
        self.samples = samples
        if ids is None:
            ids = list(range(29)) + list(range(64, 92)) if not samples else [0, 1, 2, 3, 4, 5, 6, 7, 8, 32, 33, 34][:9 + samples]
        self.ids = ids
        self.objs = {i: b"" for i in ids}
        self.objs[0] = rnd(4096, 1)
        self.objs[1] = rnd(604, 2)
        self.objs[2] = rnd(3000, 3)
        if 66 in self.objs:
            self.objs[66] = rnd(2500, 4)
        self.objs[6] = rnd(3716, 5)
        if not samples:                           # Melodee demo: a real project (project view)
            from .projectfmt import demo_project_bytes
            if demo_project_bytes():
                self.objs[0] = self.objs[2] = demo_project_bytes()
        self.song = {"rows": [], "running": False, "row": 0, "loop": 0}   # BANK_SONG (74)
        self.staged = None
        self.log: list = []
        self.smp = [None] * samples          # (header, data)
        self.smp_stage = {}
        self.tracks = [{"steps": [[0, 0, 0, 0, 0, 2, 0, 100, 0, 0, 0, 100] for _ in range(NSTEP)],
                        "pattern": 0, "patterns": {}, "len": 16} for _ in range(NTRK)]
        self.playing = False
        # playback simulation: received MIDI notes (status, note, velocity) and currently sounding notes
        self.midi_in: list[tuple[int, int, int]] = []
        self.sounding: set[tuple[int, int]] = set()
        self.globals = {2: 0, 13: 10, 14: 0}          # CLK INT, DRUM 10, ROUT CH1-4
        self.track_sounds = [[19, 1, 0], [12, 0, 0], [15, 2, 0], [10, 0, 0]]   # engine, preset, armed (REC)
        if self.sloop:
            self._sloop_init()

    def _bank_song(self, a):
        sg, op, rc = self.song, a[0], 0
        if op == 1:
            n = a[1] if len(a) > 1 else -1
            rows = [a[2 + 5 * i: 7 + 5 * i] for i in range(max(0, n))]
            if not 0 <= n <= 16 or len(a) != 2 + 5 * n or sg["running"] or \
                    any(any(b > 7 for b in r[:4]) or not 1 <= r[4] <= 16 for r in rows):
                rc = 1                                 # fully checked, otherwise everything stays as it was
            else:
                sg["rows"], sg["row"] = [list(r) for r in rows], 0
        elif op == 2:
            start = a[1] if len(a) == 2 else 0            # editor.c: chain_prepare_from(a[1])
            if not sg["rows"] or start >= len(sg["rows"]):
                rc = 1
            elif sg["running"]:
                rc = 2
            else:
                sg["running"], sg["row"] = True, start
                self.playing = True
        elif op == 3:
            sg["running"] = False
            self.playing = False
        elif op != 0:
            rc = 1
        rem = sg["rows"][sg["row"]][4] if sg["running"] and sg["rows"] else 0
        return [op & 127, rc, len(sg["rows"]), int(sg["running"]), sg["row"], rem,
                *[b for r in sg["rows"] for b in r]]

    # --------------------------------------------------------- SLOOP v6 ---
    def _sloop_init(self):
        self.ids = SLOOP_IDS
        self.objs = {0: sloop_project(1), 1: sloop_settings(), 2: sloop_project(3), 3: b"", 4: b"", 5: b"",
                     6: sloop_bank([("ACID LINE", 0), ("SLOOP KEYS", 9), ("GRAIN PAD", 8)]), 7: b"", 8: b"",
                     9: rnd(1464, 9), 10: b"", 11: b""}
        self.bk_snap: dict | None = None
        self.bk_put: dict | None = None

    def _sloop_obj(self, oid: int) -> bytes | None:
        if oid in (0, 1):
            return self.bk_snap[oid] if self.bk_snap else self.objs[oid]   # (without snapshot: GET -> rc 5)
        if 32 <= oid < 32 + len(self.smp):
            s = self.smp[oid - 32]
            return s[0] + b"\xff" * 32 + s[1] if s else b""
        return self.objs.get(oid) if oid in SLOOP_IDS else None

    def _sloop_backup(self, cmd, a):
        if cmd == 34:                                  # BK_LIST: snapshot of 0 and 1
            self.bk_snap = {0: self.objs[0], 1: self.objs[1]}
            self.bk_put = None
            ids = [i for i in self.ids if not (i in (10, 11) and not self.objs.get(i)) and
                   (i < 32 or i - 32 < len(self.smp))]
            out = [0, len(ids)]
            for i in ids:
                v = self._sloop_obj(i)
                out += [i, *u32(len(v)), *u32(zlib.crc32(v))]
            return out
        if cmd == 35:                                  # BK_GET
            na = len(a)
            off = r32(a, 1) if na >= 6 else 0
            n = a[6] | a[7] << 7 if na >= 8 else 0
            d = self._sloop_obj(a[0]) if na >= 1 else None
            rc = 1 if na != 8 or d is None or not n or n > 256 or off > len(d) or n > len(d) - off else \
                5 if self.bk_snap is None or (a[0] == 0 and self.bk_put) else 0
            out = [a[0] if na else 127, rc, *u32(off), 0 if rc else n & 127, 0 if rc else n >> 7]
            return out + ([] if rc else pack7(d[off:off + n]))
        if cmd == 36:                                  # BK_PUT
            na = len(a)
            op, oid = (a[0], a[1]) if na >= 2 else (99, 127)
            rc = 1
            if op == 0 and na == 12 and oid <= 11:
                ln = r32(a, 2)
                if (oid >= 2 or ln) and ln <= SLOOP_STAGE:
                    self.bk_put = {"id": oid, "len": ln, "crc": r32(a, 7), "buf": bytearray()}
                    self.bk_snap = None                # (the buffer belongs to the snapshot)
                    rc = 0
            elif not self.bk_put or self.bk_put["id"] != oid:
                rc = 5
            elif op == 1 and na >= 9 and r32(a, 2) == len(self.bk_put["buf"]):
                k = _pack7_len(a[7:])
                if k and k <= self.bk_put["len"] - len(self.bk_put["buf"]):
                    self.bk_put["buf"] += unpack7(a[7:], k)
                    rc = 0
            elif op == 2 and na == 2:
                rc = self._sloop_commit()
                self.bk_put = None
                if not rc:
                    self.log.append(oid)
            elif op == 3 and na == 2:
                if self.bk_put["buf"]:
                    self.log.append(f"abort {oid}")
                self.bk_put = None
                rc = 0
            return [op & 127, oid & 127, rc]
        return None

    def _sloop_commit(self) -> int:
        p = self.bk_put
        oid, raw = p["id"], bytes(p["buf"])
        if len(raw) != p["len"] or zlib.crc32(raw) != p["crc"]:
            return 2
        n = len(raw)
        if oid == 1:                                   # settings_restore
            if n not in (88, 84) or struct.unpack_from("<I", raw)[0] != 0x50455233 or not 1 <= raw[48] <= 16 \
                    or raw[49] > 1:
                return 2
            if self.playing and raw[48:84] != self.objs[1][48:84]:
                return 3
            self.objs[1] = raw
            return 0
        if self.playing:                              # everything else only stopped (erase flash)
            return 3
        if oid <= 5:                                   # project_restore
            if oid >= 2 and not n:
                self.objs[oid] = b""
                return 0
            magic, size = struct.unpack_from("<II", raw) if n >= 8 else (0, 0)
            if magic not in (0x46554E34, 0x46554E35, 0x46554E36) or size != n or \
                    (magic == 0x46554E36 and struct.unpack_from("<I", raw, n - 4)[0] != fnv1a(raw[:n - 4])):
                return 2
            self.objs[oid] = raw
            return 0
        if oid in (6, 7):
            if n and (n != 3080 or struct.unpack_from("<IHH", raw) != (0x31425055, 192, 16)):
                return 2
        elif oid == 8:
            if n and n != 3472:
                return 2
        elif oid == 9:
            if n != 1464:
                return 2
        elif oid == 10:
            if n not in (32, 36, 40):
                return 2
            if raw[:32] == bytes(32) and raw[32:37] in (b"\x01\x02\x03\x0a\x02", b"\x01\x02\x03\x0a"):
                raw = b""                              # initial state: does not appear in the inventory
        elif oid == 11:
            if n and n not in (36, 40, 48):
                return 2
        self.objs[oid] = raw
        return 0

    # --------------------------------------------------------------------
    def handle(self, data: list[int]) -> list[int] | None:
        if bytes(data) == HS_QUERY:           # update handshake (cmd 0x11): package identity
            return list(_identity_frame(self.identity))
        if data and 0x80 <= data[0] < 0xF0:  # channel message (notes, controllers): listen along like a sound module
            self._channel_msg(data)
            return None
        if self.generic:                     # firmware without editor protocol: only the handshake
            return None
        f = unframe(data)
        if not f:
            return None
        cmd, a = f
        out = self._handle(cmd, a)
        return None if out is None else frame(cmd, out)

    def _channel_msg(self, data):
        st, ch = data[0] & 0xF0, data[0] & 15
        if len(self.midi_in) < 20000:
            self.midi_in.append(tuple(data[:3]))
        if st == 0x90 and len(data) > 2 and data[2]:
            self.sounding.add((ch, data[1]))
        elif st == 0x80 or (st == 0x90 and len(data) > 2):
            self.sounding.discard((ch, data[1]))
        elif st == 0xB0 and len(data) > 2 and data[1] in (120, 123):
            self.sounding = {k for k in self.sounding if k[0] != ch}

    def _s(self, txt):
        return list(txt.encode("latin-1")) + [0]

    def _handle(self, cmd, a):
        if self.sloop:
            if cmd == 1:      # INFO like SLOOP 2.8 (12 engines, 71/50 parameters, protocol 12)
                return [*self._s(self.version), len(SLOOP_ENGINES), 71, 50, NSTEP, 63,
                        *[x for e in SLOOP_ENGINES for x in self._s(e)], NTRK, 12]
            if cmd in (34, 35, 36):
                return self._sloop_backup(cmd, a)
            if cmd in (65, 66, 67):
                return None   # SLOOP does not know Melodee's backup commands
            if cmd == 72:
                return None   # on SLOOP, 72 is DSYN_LIST, not AUDIO_STATS – the demo does not answer it
            if cmd == 9 and len(a) >= 2:     # PROJECT: op 0 load, 1 save, 2 query (section A..D)
                op, slot = a[0], a[1] & 3
                if op == 1:
                    self.objs[2 + slot] = self.objs[0]
                elif op == 0 and self.objs[2 + slot]:
                    self.objs[0] = self.objs[2 + slot]
                return [op, slot, 1 if self.objs[2 + slot] else 0]
        if cmd == 1:      # INFO
            return [*self._s(self.version), 2, 91, 50, NSTEP, 83, *self._s("FM6"), *self._s("DRUM"), NTRK]
        if cmd == 25:
            return [0]
        if cmd == 65:     # BACKUP_LIST
            out = [1, 0, len(self.ids)]
            for i in self.ids:
                v = self.objs.get(i, b"")
                out += [i, *u32(len(v)), *u32(zlib.crc32(v) if v else 0)]
            return out
        if cmd == 66:
            oid, off, n = a[0], r32(a, 1), a[6] | a[7] << 7
            v = self.objs[oid]
            return [oid, 0, *u32(off), n & 127, n >> 7, *pack7(v[off:off + n])]
        if cmd == 67:
            op, oid = a[0], a[1]
            if oid not in self.objs:
                return [op, oid, 1]
            if op == 0:
                self.staged = {"id": oid, "size": r32(a, 2), "crc": r32(a, 7), "bytes": bytearray(), "chunks": 0}
                return [op, oid, 0]
            if op == 1:
                st = self.staged
                st["chunks"] += 1
                off = r32(a, 2)
                n = min(256, st["size"] - off)
                st["bytes"] += unpack7(a[7:], n)
                return [op, oid, 0]
            if op == 2:
                st = self.staged
                ok = zlib.crc32(bytes(st["bytes"])) == st["crc"]
                if ok:
                    self.objs[oid] = bytes(st["bytes"])
                    self.log.append(oid)
                self.staged = None
                return [op, oid, 0 if ok else 2]
            if op == 3:
                if self.staged and self.staged["chunks"]:
                    self.log.append(f"abort {oid}")
                self.staged = None
                return [op, oid, 0]
        if cmd == 82 and not self.sloop and a == [0]:   # resource status (read only) like Melodee 1.6.0-rc.7
            return [0, 1, *u32(53844), *u32(0x2035D335)]
        if cmd == 15:     # SMP_INFO
            out = [len(self.smp), 80]
            for s in self.smp:
                if s:
                    hdr, data = s
                    name = hdr[8:16].rstrip(b"\0").decode()
                    out += [hdr[6], *self._s(name), (len(data) + 1023) // 1024]
                else:
                    out += [0, 0, 0]
            return out
        if cmd == 11:
            slot = a[0]
            if slot >= len(self.smp):
                return [slot, 1]
            self.smp[slot] = None
            self.smp_stage[slot] = bytearray()
            return [slot, 0]
        if cmd == 12:
            slot, off = a[0], a[1] | a[2] << 7 | a[3] << 14
            st = self.smp_stage.get(slot)
            if st is None:
                return [slot, a[1], a[2], a[3], 4]
            # the length follows from the pack structure (mask byte + up to 7 data bytes)
            n = 0
            i = 0
            rest = a[4:]
            while i < len(rest):
                g = min(7, len(rest) - i - 1)
                n += g
                i += g + 1
            chunk = unpack7(a[4:], n)
            if off - 512 != len(st):
                return [slot, a[1], a[2], a[3], 1]
            st += chunk
            return [slot, a[1], a[2], a[3], 0]
        if cmd == 13:
            slot = a[0]
            hdr = unpack7(a[1:], 480)
            data = bytes(self.smp_stage.pop(slot, b""))
            magic, ver, zones = struct.unpack_from("<IHB", hdr)
            length, crc = struct.unpack_from("<II", hdr, 16)
            if magic != 0x504D5346 or not 1 <= zones <= 16:
                return [slot, 2]
            if length != len(data) or zlib.crc32(data) != crc:
                return [slot, 3]
            self.smp[slot] = (hdr, data)
            return [slot, 0]
        if cmd == 14:
            slot = a[0]
            if slot < len(self.smp):
                self.smp[slot] = None
            return [slot, 0]
        if cmd == 72:     # AUDIO_STATS (schema depending on flags, like Melodee 1.6)
            flags = a[0] if a else 0
            vals = [1, 1, 44100, 44100, 128, 120, 0, 0, 0, 0, 0, 12345, 12340, 0, 900, 300, 0, 44 << 14,
                    700, int(random.uniform(0.25, 0.45) * 256)]
            schema = 2
            if flags & 2:
                vals += [random.randint(2, 9), 1, 0, 0, 3, 5]
                schema = 3
            if flags & 4:
                vals += [1, 48211, 410, 95, 0, 7]
                schema = 4
            if flags & 8:
                vals += [1, 0, 0, 0, 0, 0, 0, 0, 1200, 262144, 201000, 214500, 0, 16384, 9100]
                schema = 5
            if flags & 16:
                vals += [1, 0, 2, 0, 2, 0, 3, 0]
                schema = 6
            if self.samples:          # older firmware: schema 2 only
                vals, schema = vals[:20], 2
            out = [schema]
            for v in vals:
                out += u32(v)
            return out
        # --- klang: LIBRARY ops 1-6 / FM6 ---
        # Shared user library (command 80, all ops 0–6) with state, favourites (37/38) and
        # FM6_PUT onto a track (69) – rules as in firmware/src/editor_library.c. Initial content like the
        # former plain list (26 used, favourites U004/U008, names "DEMO n").
        if cmd in (80, 37, 38, 69) and not self.samples:
            from . import klang_demo
            return klang_demo.handle(self, cmd, a, 91, NTRK)
        # --- end klang ---
        if cmd == 80 and a and a[0] == 0 and not self.samples:   # LIBRARY list
            first = a[1] | a[2] << 7
            cnt = min(a[3] if len(a) > 3 else 16, 16, max(0, 384 - first))
            body = [0, a[1], a[2], 0, cnt, 384 & 0x7F, 384 >> 7]
            for i in range(first, first + cnt):
                used = 1 if i < 23 or i in (40, 41, 200) else 0
                eng = [0, 5, 9, 19][i % 4]
                body += [used | (2 if i in (3, 7) else 0), eng, 0, *self._s(f"DEMO {i + 1}" if used else "")]
            return body
        if cmd == 73:     # PATTERN
            tr, op = a[0], a[1]
            t = self.tracks[tr]
            if op == 1:
                t["patterns"][t["pattern"]] = [s[:] for s in t["steps"]]
                t["pattern"] = a[2]
                t["steps"] = t["patterns"].get(a[2]) or [[0, 0, 0, 0, 0, 2, 0, 100, 0, 0, 0, 100] for _ in range(NSTEP)]
            return [tr, op, 0, t["pattern"], 127, 16]
        if cmd == 2 and len(a) >= 2:      # GET (global: ROUT, DRUM, CLK of the simulation)
            return [a[0], a[1], *v14(self.globals.get(a[1], 0) if a[0] == 1 else 0)]
        if cmd == 27 and not a:           # TRACK: selected track, NTRK, per track engine, preset, level, muted, armed
            body = [0, len(self.track_sounds)]
            for eng, pre, armed in self.track_sounds:
                body += [eng, pre & 127, *v14(100), 0, armed]
            return body + [pre >> 7 for _e, pre, _a in self.track_sounds]
        if cmd == 74 and a and not self.sloop:   # BANK_SONG (Melodee "Pattern banks", version 1: 16 rows)
            return self._bank_song(a)
        if cmd == 30:     # TRACK_STEP
            tr, idx = a[0], a[1]
            st = self.tracks[tr]["steps"][idx]
            if len(a) >= 10:
                st[:len(a) - 2] = a[2:]
            return [tr, idx, *st]
        if cmd == 5:      # DESC
            scope, pid = a[0], a[1]
            label = {29: "LEN", 30: "DIV"}.get(pid, f"P{pid}")
            return [scope, pid, 0, *v14(1), *v14(64), *v14(16), *self._s(label), *self._s("")]
        if cmd == 31:     # TRACK_PARAM
            tr, pid = a[0], a[1]
            if len(a) >= 4 and pid == 29:
                self.tracks[tr]["len"] = (a[2] | a[3] << 7) - 8192
            val = self.tracks[tr]["len"] if pid == 29 else 0
            return [tr, pid, *v14(val)]
        if cmd == 9:
            return [a[0], a[1], 1]
        if cmd == 10 and a:   # NAMES: preset names of an engine
            e = a[0] & 0x7F
            names = [f"DEMO {e}-{i + 1}" for i in range(8)]
            return [e, len(names), *[b for n in names for b in self._s(n)], *self._s("A"), *self._s("B")]
        return None


def _pack7_len(a) -> int:
    """Number of bytes in a pack7 block (mask byte + up to 7 data bytes per group)."""
    n = i = 0
    while i < len(a):
        g = min(7, len(a) - i - 1)
        n += g
        i += g + 1
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", type=int, default=0)
    ap.add_argument("--version", default="Melodee 1.6.0-rc.7")
    ap.add_argument("--generic", metavar="KENNUNG", default="",
                    help="firmware without editor protocol: only answers the update handshake")
    args = ap.parse_args()
    dev = FakeFM1(args.samples, args.version, identity=args.generic, generic=bool(args.generic))
    print("# fake FM-1 bereit", flush=True)  # i18n: ignore – read by the app/tests
    for line in sys.stdin:
        line = line.strip()
        if not line:
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
