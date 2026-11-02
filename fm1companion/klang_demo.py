# SPDX-License-Identifier: GPL-3.0-only
"""Simulation der Melodee-Preset-Bibliothek für den Demo-Modus (wird von demo_device.FakeFM1 im
markierten klang-Block aufgerufen). Prüft Anfragen wie firmware/src/editor_library.c."""
from __future__ import annotations

import random

from . import dx7
from .presetlib import NATIVE_ENGINE, NATIVE_SIZE, pack7, unpack7, v14dec, v14enc

SLOTS = 384
NENG = 22


def _initial(pcount: int) -> list:
    rng = random.Random(7)
    bank = [None] * SLOTS
    for i in range(SLOTS):
        if not (i < 23 or i in (40, 41, 200)):
            continue
        eng = [12, 10, 3, 15][i % 4]
        p = {"engine": eng, "name": f"DEMO {i + 1}", "p": [rng.randint(0, 100) for _ in range(pcount)],
             "fav": i in (3, 7), "kind": 0, "raw": b""}
        if eng == 12:
            v = dx7.set_name(dx7.random_voice(rng), f"DEMO {i + 1}")
            p["kind"], p["raw"] = 1, bytes(dx7.pack(v))
        bank[i] = p
    return bank


def _s(t: str) -> list[int]:
    return [ord(c) & 127 for c in t] + [0]


def handle(dev, cmd: int, a: list[int], pcount: int, ntrk: int):
    if not hasattr(dev, "klang_bank"):
        dev.klang_bank = _initial(pcount)
        dev.klang_tracks = [{"engine": 3, "p": [10] * pcount, "kind": 0, "raw": b""} for _ in range(ntrk)]
        dev.klang_writes = 0
    bank, tracks = dev.klang_bank, dev.klang_tracks
    if cmd in (37, 38):
        if len(a) != 4 or a[0] not in (16, 2, NENG):   # Kategorie "general" (16 bei 1.6, sonst NENGINES)
            return [1]
        gen = a[0]
        slot, cnt = v14dec(a[1], a[2]), a[3]
        if cmd == 37:
            if not 1 <= cnt <= 32 or slot < 0 or slot + cnt > SLOTS:
                return [1]
            return [0, gen, *v14enc(slot), cnt, *[1 if bank[k] and bank[k]["fav"] else 0 for k in range(slot, slot + cnt)]]
        if not 0 <= slot < SLOTS or cnt > 1 or (cnt and not bank[slot]):
            return [1]
        if bank[slot]:
            bank[slot]["fav"] = bool(cnt)
        rc = 4 if getattr(dev, "playing", False) else 0
        return [rc, gen, *v14enc(slot), cnt]
    if cmd == 69:                               # FM6_PUT: nur Ziel 0 (Spur)
        if len(a) != 130 or a[0] != 0 or a[1] >= ntrk:
            return [a[0] if a else 0, a[1] if len(a) > 1 else 0, 1]
        tracks[a[1]].update(engine=12, kind=1, raw=bytes(a[2:]))
        return [0, a[1], 0]
    # ---- Befehl 80
    n = len(a)
    op = a[0] if n else 127
    k = a[1] | a[2] << 7 if n >= 3 else SLOTS
    rc = 1 if n < 3 or op > 6 or k >= SLOTS else 0
    out_extra = []
    if not rc and op == 0:
        if n != 4 or not 1 <= a[3] <= 16:
            rc = 1
    elif not rc and op == 1:
        rc = 0 if n == 3 else 1
    elif not rc and op == 2:
        ln = 0
        while 4 + ln < n and a[4 + ln]:
            ln += 1
        pos = 5 + ln
        end = pos + 2 * pcount + 32
        eng = a[3] if n > 3 else 255
        if n < end + 1 or not 1 <= ln <= 20 or eng >= NENG or eng in (0, 1, 2, 4, 5, 6, 7, 8, 9, 11, 13, 14, 16, 17, 18, 20):
            rc = 1
        name = "".join(chr(x) for x in a[4:4 + ln])
        if not rc and any(not 32 <= ord(c) <= 126 for c in name):
            rc = 1
        vals = [v14dec(a[pos + 2 * j], a[pos + 2 * j + 1]) for j in range(pcount)] if not rc else []
        if not rc and any(not -64 <= v <= 127 for v in vals):
            rc = 1
        if not rc:
            kind = a[end] & 31
            raw = b""
            if kind:
                size = a[end + 1] | a[end + 2] << 7 if n >= end + 3 else 0
                try:
                    raw = bytes(unpack7(a[end + 3:]))
                except Exception:                    # noqa: BLE001
                    raw = b""
                if NATIVE_ENGINE.get(kind) != eng or size != NATIVE_SIZE.get(kind) or len(raw) != size:
                    rc = 1
            elif n != end + 1 or ln > 12 or eng in (12, 19):
                rc = 1
            if not rc:
                if getattr(dev, "playing", False):
                    dev.playing = False               # Schreiben stoppt die Wiedergabe (ed_flash_stop)
                old = bank[k]
                bank[k] = {"engine": eng, "name": name[:12] if not kind else name, "p": vals, "kind": kind, "raw": raw,
                           "fav": bool(old and old["fav"])}
                if kind == 1 and not (a[end] & 32):
                    v = dx7.set_name(dx7.unpack(raw), name)
                    bank[k]["raw"] = bytes(dx7.pack(v))
                dev.klang_writes += 1
    elif not rc and op == 3:
        ln = n - 4
        if n < 4 or a[3] >= ntrk or ln > 20:
            rc = 1
        else:
            t = tracks[a[3]]
            name = "".join(chr(x) for x in a[4:]) or f"{dx7.name(dx7.unpack(t['raw'])) if t['kind'] else 'SID'} {k + 1:02d}"
            bank[k] = {"engine": t["engine"], "name": name, "p": list(t["p"]), "kind": t["kind"], "raw": t["raw"], "fav": False}
            dev.klang_writes += 1
    elif not rc and op == 4:
        if n != 4 or a[3] >= ntrk or not bank[k]:
            rc = 1
        else:
            b = bank[k]
            tracks[a[3]].update(engine=b["engine"], p=list(b["p"]), kind=b["kind"], raw=b["raw"])
    elif not rc and op == 5:
        if n != 3:
            rc = 1
        else:
            bank[k] = None
            dev.klang_writes += 1
    elif not rc and op == 6:
        ln = n - 3
        name = "".join(chr(x) for x in a[3:])
        if not 1 <= ln <= 20 or not bank[k] or any(not 32 <= ord(c) <= 126 for c in name):
            rc = 1
        else:
            bank[k]["name"] = name
            dev.klang_writes += 1
    out = [op, k & 127, k >> 7, rc]
    if rc:
        return out
    if op == 0:
        cnt = min(a[3], SLOTS - k)
        out += [cnt, SLOTS & 127, SLOTS >> 7]
        for j in range(k, k + cnt):
            b = bank[j]
            out += [(1 | (2 if b["fav"] else 0)) if b else 0, b["engine"] if b else 0, 0, *_s(b["name"][:12] if b else "")]
    elif op == 1:
        b = bank[k]
        out += [1 if b else 0, b["engine"] if b else 0, *_s(b["name"][:12] if b else "")]
        for j in range(pcount):
            out += v14enc(b["p"][j] if b else 0)
        out += [0] * 32
        kind = b["kind"] if b else 0
        out.append(kind)
        if kind:
            out += [len(b["raw"]) & 127, len(b["raw"]) >> 7, *pack7(b["raw"])]
    return out + out_extra
