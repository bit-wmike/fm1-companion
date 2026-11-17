# SPDX-License-Identifier: GPL-3.0-only
"""Backups of SLOOP 3 (isod89/sloop3-fm1) over its own editor protocol (sloop3.py) – the "sloop3-backup" file.

What SLOOP 3 lets a host read, and what this file therefore holds:

    id  what                                         how it is read                 restored
    0   the project saved on the FM-1                READ kind 0                    yes (WBEGIN/WDATA/WEND kind 0)
    1   your sounds (32 places, favourites, theme)   READ kind 1                    yes (… kind 1)
    2   the project as it was at that moment         READ kind 2                    only on request, in place of 0
        (also what was not saved yet)
    3   your FM6 bank, as it plays                   BANK 1, image "S3F6"           yes, if the FM-1 had one (BANK 2–4)
    4   your SYN kits, as they play                  BANK 1, image "S3SY"           yes, if they were not the defaults
    5   the device's settings as the editor sees     HOME                           no – kept to look at
        them

NOT in the file, because SLOOP 3's protocol has no command to read them: **your samples** (USR1–USR4, the slices and
kits made from them). The file lists what the slots held (names, sizes); the samples themselves are only in a backup
of the whole flash (m-bootloader's MIDI MODE: flash/mboot.py, or M-Flasher) – or in the files you sent them from.

Objects 0 and 1 are exactly what the web editor's own "Backup all" writes (.s3b: "S3BK", 1, count; then kind, place,
length, bytes): ``to_s3b`` gives that file, so a backup made here can also be put back with SLOOP 3's editor.

Reading sends read-only commands only. Restoring is what the editor's own "Restore" does (project and sounds) plus its
bank pages' write – the FM-1 checks every file (CRC-32, format) before it keeps it. Checked against the author's PC
twin (fw/main.c of 3.3.6 and 3.3.7 on a simulated FM-1), never on a real FM-1 by this app.
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import zlib

from . import sloop3 as S3
from .i18n import N_, _

FORMAT = "sloop3-backup"
VERSION = 1
PROJECT, SOUNDS, NOW, FM6, SYN, SETTINGS = 0, 1, 2, 3, 4, 5
IDS = (PROJECT, SOUNDS, NOW, FM6, SYN, SETTINGS)
TITLES = {PROJECT: N_("Project saved on the FM-1"), SOUNDS: N_("Your sounds (with favourites and theme)"),
          NOW: N_("Project as it was at that moment (unsaved changes too)"), FM6: N_("Your FM6 bank"),
          SYN: N_("Your SYN kits"), SETTINGS: N_("Settings (kept to look at, not restored)")}
RESTORABLE = (SOUNDS, FM6, SYN, PROJECT)            # the order they are written in: the project last (it is opened)
MAX_OBJECT = 1 << 20


class Sloop3BackupError(Exception):
    """a ready (translated) sentence. After a restore that failed part-way: ``written`` (ids already on the FM-1)"""


def title(oid: int) -> str:
    return _(TITLES[oid]) if oid in TITLES else str(oid)


def _obj(oid: int, data: bytes) -> dict:
    return {"id": oid, "size": len(data), "crc": zlib.crc32(data) & 0xFFFFFFFF,
            "data": base64.b64encode(data).decode()}


def capture(link, firmware: str = "", progress=None, companion: dict | None = None) -> dict:
    """read a complete SLOOP 3 backup -> file dict. Read-only commands; nothing on the FM-1 changes."""
    try:
        with S3.Client(link) as c:
            info = c.info()
            fm6_src, fm6_used, syn_src = c.bank_state()
            samples = c.samples()
            done = 0
            total = 60_000 + S3.FM6_LEN + S3.SYN_LEN          # (a guess until the sizes are known: never "100 %" early)

            def step(n):
                nonlocal done
                done += n
                if progress:
                    progress(min(done, total - 1), total)

            def maybe(kind):
                try:
                    return c.read_file(kind, 0, step)
                except S3.Sloop3Error as e:
                    if e.code == 3:                           # nothing there (no project saved, no sounds of yours)
                        return b""
                    raise
            parts = {NOW: c.read_file(S3.KIND_NOW, 0, step), PROJECT: maybe(S3.KIND_PROJECT),
                     SOUNDS: maybe(S3.KIND_SOUNDS), FM6: c.bank_read(S3.FM6, step), SYN: c.bank_read(S3.SYN, step),
                     SETTINGS: bytes(c.home())}
    except S3.Sloop3Error as e:
        raise Sloop3BackupError(str(e)) from e
    f = {"format": FORMAT, "version": VERSION, "firmware": firmware or info.label,
         "created": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
         "info": {"proto": info.proto, "tracks": info.tracks, "version": info.version,
                  "sounds_used": info.sounds_used, "project_saved": bool(info.projects_used & 1)},
         "bank": {"fm6_source": fm6_src, "fm6_used": fm6_used, "syn_source": syn_src},
         "samples_not_included": samples,
         "objects": [_obj(i, parts[i]) for i in IDS]}
    if companion:
        f["companion"] = companion
    objects(f)                                                # the same check as when loading
    if progress:
        progress(total, total)
    return f


def objects(file) -> dict[int, bytes]:
    """check a SLOOP 3 backup file -> {id: bytes}; Sloop3BackupError if it is not one or damaged"""
    if not isinstance(file, dict) or file.get("format") != FORMAT or file.get("version") != VERSION \
            or not isinstance(file.get("objects"), list):
        raise Sloop3BackupError(_("Not a SLOOP 3 backup"))
    out: dict[int, bytes] = {}
    for o in file["objects"]:
        try:
            oid, size, crc, data = o["id"], o["size"], o["crc"], o["data"]
            raw = base64.b64decode(data, validate=True)
        except (KeyError, TypeError, ValueError):
            raise Sloop3BackupError(_("invalid object in the backup"))
        if not isinstance(oid, int) or isinstance(oid, bool) or oid not in IDS or oid in out or len(raw) != size \
                or size > MAX_OBJECT:
            raise Sloop3BackupError(_("invalid object in the backup"))
        if zlib.crc32(raw) & 0xFFFFFFFF != crc:
            raise Sloop3BackupError(_("Checksum in the backup does not match"))
        out[oid] = raw
    for which, oid in ((S3.FM6, FM6), (S3.SYN, SYN)):
        if out.get(oid):
            try:
                S3.check_image(out[oid], which)
            except S3.Sloop3Error as e:
                raise Sloop3BackupError(str(e))
    if not out.get(NOW) and not out.get(PROJECT):
        raise Sloop3BackupError(_("The backup does not contain a project"))
    return out


def bank_sources(file) -> tuple[int, int]:
    """(FM6 bank's source, SYN kits' source) at backup time: 0 none / the defaults, 1 SLOOP 2.8's, 2 yours"""
    b = file.get("bank") if isinstance(file.get("bank"), dict) else {}
    try:
        return int(b.get("fm6_source", 0)), int(b.get("syn_source", 0))
    except (TypeError, ValueError):
        return 0, 0


def restore_plan(file, project: str = "saved") -> list[int]:
    """the objects a restore writes, in order. project: "saved" (object 0; the state at that moment if nothing was
    saved), "now" (object 2 in the project's place) or "none"."""
    m = objects(file)
    fm6_src, syn_src = bank_sources(file)
    plan = []
    if m.get(SOUNDS):
        plan.append(SOUNDS)
    if m.get(FM6) and fm6_src:
        plan.append(FM6)
    if m.get(SYN) and syn_src:
        plan.append(SYN)
    if project == "now" or (project == "saved" and not m.get(PROJECT)):
        if m.get(NOW):
            plan.append(NOW)
    elif project == "saved":
        plan.append(PROJECT)
    return plan


def restore(link, file: dict, progress=None, project: str = "saved") -> list[int]:
    """write a backup back (see the module text) -> the ids written. The FM-1 must run SLOOP 3; every part is read
    back afterwards where the protocol allows it and compared."""
    m = objects(file)
    plan = restore_plan(file, project)
    total = sum(len(m[i]) for i in plan) or 1
    done = 0
    written: list[int] = []

    def step(n):
        nonlocal done
        done += n
        if progress:
            progress(min(done, total), total)
    try:
        with S3.Client(link) as c:
            c.info()                                          # (SLOOP 3 answers: only then is anything written)
            for oid in plan:
                data = m[oid]
                if oid == SOUNDS:
                    c.write_file(S3.KIND_SOUNDS, data, step)
                    c.wait_idle()
                elif oid in (FM6, SYN):
                    which = S3.FM6 if oid == FM6 else S3.SYN
                    c.bank_write(which, data, step)
                    c.wait_idle()
                    if c.bank_read(which)[16:] != data[16:]:
                        raise S3.Sloop3Error(_("{part}: the FM-1 holds something else than was written.").format(
                            part=title(oid)))
                else:
                    c.write_file(S3.KIND_PROJECT, data, step)
                    c.wait_idle()
                    if c.read_file(S3.KIND_PROJECT) != data:
                        raise S3.Sloop3Error(_("{part}: the FM-1 holds something else than was written.").format(
                            part=title(oid)))
                written.append(oid)
    except S3.Sloop3Error as e:
        err = Sloop3BackupError(str(e))
        err.written = written
        raise err from e
    return written


def to_s3b(file) -> bytes:
    """the saved project and your sounds as the web editor's own "Backup all" file (.s3b) – its "Restore" takes it"""
    m = objects(file)
    parts = [(k, m[i]) for k, i in ((0, PROJECT), (1, SOUNDS)) if m.get(i)]
    out = bytearray(b"S3BK" + bytes([1, len(parts), 0, 0]))
    for kind, data in parts:
        out += bytes([kind, 0]) + S3.w32(len(data)) + data
    return bytes(out)


def dumps(file: dict) -> str:
    return json.dumps(file, separators=(",", ":"))
