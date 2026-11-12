# SPDX-License-Identifier: GPL-3.0-only
# Protocol: after app/transfer.c, app/project.c, app/fm_library.c and sdk/Update.html.in (the workbench's project
#   library) of M-VAVE's FM1 Open (github.com/m-vave/fm1_open; GPL-3.0-only WITH GPL-3.0-linking-exception, (c) 2026
#   M-VAVE). The framing and the sample bank are fm1open.py's.
"""Backups of M-VAVE's FM1 Open (V14) over its own file transfer protocol – there is no backup command, so a backup
is made of the commands its workbench uses (F0 7D 'F' 'M' 02 cmd token … , fm1open.Client):

* 0 the project library (format, 16 slots, the current slot), 22 the capabilities (DX7 library available), 9 a
  slot's name (status 1: empty), 8 load a slot into the working area, 7 save the working area into a slot,
  1/2 download (kind 3: the working project with its tuning block, 4264 bytes; kind 4: a DX7 library bank, 4096),
  3/4/5 upload (kind 0: into the working area, which accepts the 4264 bytes; kind 4: a library bank), 11/15 the sample
  bank (fm1open.Client.read_bank / write_bank).
* A slot can only be read by loading it into the working area. So a backup first downloads the working area (also
  unsaved changes), loads and downloads every used slot (the current one last, so the device's "current slot"
  setting stays), then uploads the working area again: afterwards the FM-1 shows the same project, marked as changed.
  Playback is stopped first (MMC STOP, which transfer.c understands).
* Restore: the library banks, every used slot of the backup (upload, then SAVE into the slot), the current slot,
  the working area, the sample bank last (only if it differs: it is erased and written whole). Slots that are empty
  in the backup stay as they are on the device (the protocol cannot delete a slot).

File: {"format": "fm1open-backup", "version": 1, "firmware": "FM1 Open V14", "identity": "FM-1_8000014",
"created", "slot": the current slot, "names": the 16 slot names, "objects": [{"id", "size", "crc", "data" (Base64)}],
"companion": {...}}. Objects: 0 the working project, 1..16 the slots 1..16 (size 0: empty), 20..23 the DX7 library
banks A..D, 32 the sample bank (size 0: none).
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import re
import struct
import zlib

from . import fm1open as F
from .i18n import _

FORMAT = "fm1open-backup"
VERSION = 1
SLOTS = 16
WORKING, SAMPLES = 0, 32
BANK_IDS = [20, 21, 22, 23]
SLOT_IDS = list(range(1, SLOTS + 1))
IDS = [WORKING, *SLOT_IDS, *BANK_IDS, SAMPLES]
PROJECT, PROJECT_TUNED, PROJECT_LEGACY, BANK = 4064, 4264, 4048, 4096
KIND_PROJECT, KIND_TUNED, KIND_BANK = 0, 3, 4
MMC_STOP = [0xF0, 0x7F, 0x7F, 0x06, 0x01, 0xF7]
IDENTITY = re.compile(r"^(FM1_OFFICIAL_OPEN|FM1_COMMUNITY_\d+|FM-1_8\d{6})$")
KNOWN_RELEASES = {"FM-1_8000014": "V14"}        # app/version.h FM1_UPDATE_PRODUCT of each release


class FM1OpenBackupError(Exception):
    pass


def title(oid: int) -> str:
    if oid == WORKING:
        return _("working project")
    if oid in SLOT_IDS:
        return _("Project {n}").format(n=oid)
    if oid in BANK_IDS:
        return _("DX7 library {bank}").format(bank="ABCD"[oid - 20])
    if oid == SAMPLES:
        return _("sample bank")
    return str(oid)


def release(identity: str) -> str:
    """the package identity -> FM1 Open's release ("V14"; "" if unknown)"""
    return KNOWN_RELEASES.get(identity or "", "")


def is_identity(identity: str) -> bool:
    """an identity of M-VAVE's FM1 Open (and of firmwares built with its SDK, which keep its numbering)"""
    return bool(IDENTITY.match(identity or ""))


# ------------------------------------------------------------------ the device
def identity(link, timeout: float = 1.0) -> str:
    """the package identity the device answers (the update handshake's 17; "" if none)"""
    from .stockv15 import Client
    try:
        with Client(link) as c:
            return c.identity(timeout)
    except Exception:                               # noqa: BLE001
        return ""


def device_version(link) -> str:
    """'V14' of an FM-1 with FM1 Open (from its identity); '' if unknown"""
    return release(identity(link))


def _download(c: F.Client, kind: int, target: int = 0) -> bytes:
    d = c.query(1, bytes([kind, target]))
    if len(d) != 2:
        raise FM1OpenBackupError(_("unexpected answer while reading"))
    size = d[0] | d[1] << 8
    out = bytearray()
    for at in range(0, size, 64):
        n = min(64, size - at)
        p = c.query(2, bytes([at & 255, at >> 8, n]))
        if len(p) != n:
            raise FM1OpenBackupError(_("unexpected answer while reading"))
        out += p
    return bytes(out)


def _upload(c: F.Client, kind: int, target: int, data: bytes):
    c.query(3, bytes([kind, target, len(data) & 255, len(data) >> 8]))
    for at in range(0, len(data), 64):
        c.query(4, bytes([at & 255, at >> 8]) + data[at:at + 64])
    c.query(5, struct.pack("<I", zlib.crc32(data) & 0xFFFFFFFF))


def _working(c: F.Client) -> bytes:
    try:
        return _download(c, KIND_TUNED)
    except F.FM1OpenError as e:                     # (a firmware without the tuning block: the plain project)
        if e.code != 71:
            raise
        return _download(c, KIND_PROJECT)


def _names(c: F.Client) -> tuple[list[str], list[bool]]:
    names, used = [], []
    for s in range(SLOTS):
        r = c.query(9, bytes([s]), empty_ok=True)
        names.append(bytes(r[:12]).decode("latin-1").rstrip())
        used.append(getattr(c, "last_code", 0) == 0)
    return names, used


def _probe(c: F.Client) -> dict:
    info = c.probe()
    lib = 0
    try:
        ext = c.query(22)
        lib = ext[3] if len(ext) >= 4 else 0
    except F.FM1OpenError:
        pass
    info["library"] = bool(lib)
    return info


def capture(link, firmware: str = "", progress=None, companion: dict | None = None, samples: bool = True) -> dict:
    """read a complete FM1 Open backup (see the module text) -> file dict"""
    ident = identity(link)
    with F.Client(link, timeout=1.0) as c:
        link.send_midi(MMC_STOP, force=True)
        info = _probe(c)
        current = info["project"]
        names, used = _names(c)
        work = _working(c)
        order = [s for s in range(SLOTS) if used[s] and s != current] + ([current] if used[current] else [])
        banks = list(range(4)) if info["library"] else []
        st, sample_size = c.sample_status() if samples else (0, 0)
        total = (len(order) + 1) * PROJECT_TUNED + len(banks) * BANK + (sample_size if st == 2 else 0) or 1
        done = 0

        def step(n):
            nonlocal done
            done += n
            if progress:
                progress(min(done, total), total)
        step(len(work))
        slots: dict[int, bytes] = {}
        try:
            for s in order:
                c.query(8, bytes([s]))
                slots[s] = _working(c)
                step(len(slots[s]))
        finally:
            if order:                               # the working area as it was (also unsaved changes)
                _upload(c, KIND_PROJECT, 0, work)
        objs = [(WORKING, work)] + [(s + 1, slots.get(s, b"")) for s in range(SLOTS)]
        for b in banks:
            objs.append((BANK_IDS[b], _download(c, KIND_BANK, b)))
            step(BANK)
        bank = b""
        if st == 2:
            bank = c.read_bank(lambda a, b: progress(min(done + a, total), total) if progress else None)
        objs.append((SAMPLES, bank))
    f = {"format": FORMAT, "version": VERSION, "firmware": firmware or ("FM1 Open " + (release(ident) or "")).strip(),
         "identity": ident, "created": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
         "slot": current, "names": names,
         "objects": [{"id": i, "size": len(d), "crc": zlib.crc32(d) & 0xFFFFFFFF, "data": base64.b64encode(d).decode()}
                     for i, d in objs]}
    if companion:
        f["companion"] = companion
    objects(f)                                      # the same check as when loading
    return f


def _sizes_ok(oid: int, n: int) -> bool:
    if oid in (WORKING, *SLOT_IDS):
        return n in (PROJECT, PROJECT_TUNED, PROJECT_LEGACY) or (oid != WORKING and n == 0)
    if oid in BANK_IDS:
        return n == BANK
    if oid == SAMPLES:
        return n == 0 or F.HEADER <= n <= F.REGION
    return False


def objects(file) -> dict[int, bytes]:
    """check an FM1 Open backup file -> {id: bytes}; FM1OpenBackupError if it is not one or damaged"""
    if not isinstance(file, dict) or file.get("format") != FORMAT or file.get("version") != VERSION \
            or not isinstance(file.get("objects"), list):
        raise FM1OpenBackupError(_("Not an FM1 Open backup"))
    out: dict[int, bytes] = {}
    for o in file["objects"]:
        try:
            oid, size, crc, data = o["id"], o["size"], o["crc"], o["data"]
            raw = base64.b64decode(data, validate=True)
        except (KeyError, TypeError, ValueError):
            raise FM1OpenBackupError(_("invalid object in the backup"))
        if not isinstance(oid, int) or oid not in IDS or oid in out or len(raw) != size or not _sizes_ok(oid, size):
            raise FM1OpenBackupError(_("invalid object in the backup"))
        if zlib.crc32(raw) & 0xFFFFFFFF != crc:
            raise FM1OpenBackupError(_("Checksum in the backup does not match"))
        if oid == SAMPLES and raw:
            try:
                F.read_bank(raw)
            except F.FM1OpenError as e:
                raise FM1OpenBackupError(_("sample bank: {error}").format(error=e))
        out[oid] = raw
    if not out.get(WORKING):
        raise FM1OpenBackupError(_("The backup does not contain the working project"))
    return out


def restore(link, file: dict, progress=None, tolerant: bool = False, ids=None) -> list[int]:
    """write an FM1 Open backup to the device (see the module text). tolerant: a part the firmware refuses is skipped
    (-> its id in the returned list) instead of stopping. ids: only these objects."""
    m = objects(file)
    want = set(ids) if ids is not None else set(m)
    rejected: list[int] = []
    with F.Client(link, timeout=1.0) as c:
        link.send_midi(MMC_STOP, force=True)
        info = _probe(c)
        banks = [i for i in BANK_IDS if i in want and m.get(i)]
        if banks and not info["library"]:
            if not tolerant:
                raise FM1OpenBackupError(_("This FM1 Open has no DX7 library (an older version) – nothing was "
                                           "written."))
            rejected += banks
            banks = []
        slots = [i for i in SLOT_IDS if i in want and m.get(i)]
        work = m[WORKING] if WORKING in want else b""
        smp = m.get(SAMPLES, b"") if SAMPLES in want else b""
        total = len(banks) * BANK + (len(slots) + 1) * PROJECT_TUNED + len(smp) * 2 or 1
        done = 0

        def step(n):
            nonlocal done
            done += n
            if progress:
                progress(min(done, total), total)

        def attempt(oid, fn):
            try:
                fn()
            except F.FM1OpenError:
                if not tolerant:
                    raise
                rejected.append(oid)
        for i in banks:
            attempt(i, lambda i=i: _upload(c, KIND_BANK, i - 20, m[i]))
            step(BANK)
        for i in slots:
            attempt(i, lambda i=i: (_upload(c, KIND_PROJECT, 0, m[i]), c.query(7, bytes([i - 1]))))
            step(PROJECT_TUNED)
        slot = file.get("slot")
        if isinstance(slot, int) and 0 <= slot < SLOTS and m.get(slot + 1) and (slot + 1) not in rejected:
            attempt(slot + 1, lambda: c.query(8, bytes([slot])))
        if work:
            attempt(WORKING, lambda: _upload(c, KIND_PROJECT, 0, work))
        step(PROJECT_TUNED)
        if smp:
            if c.unchanged(smp):
                step(len(smp) * 2)
            else:
                attempt(SAMPLES, lambda: c.write_bank(smp, lambda a, b: progress(min(done + a, total), total)
                                                      if progress else None))
    return rejected


def dumps(file: dict) -> str:
    return json.dumps(file, separators=(",", ":"))

