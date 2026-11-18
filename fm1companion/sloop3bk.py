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

Objects 0 and 1 hold the same bytes the web editor's own "Backup all" puts into its .s3b file; the app does not write
.s3b files (use the editor's own button on the Editor page for one).

Reading sends read-only commands only. Restoring is what the editor's own "Restore" does (project and sounds) plus its
bank pages' write – the FM-1 checks every file (CRC-32, format) before it keeps it, and the app reads every part
back afterwards (``restore``). Checked against the author's PC
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
    """(FM6 bank's source, SYN kits' source) at backup time: 0 none / the defaults, 1 SLOOP 2.8's, 2 yours. Anything
    else in a hand-edited file counts as 0 (the bank is then not written)."""
    b = file.get("bank") if isinstance(file.get("bank"), dict) else {}

    def one(v):
        return v if isinstance(v, int) and not isinstance(v, bool) and v in (0, 1, 2) else 0
    return one(b.get("fm6_source")), one(b.get("syn_source"))


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


class Restored(list):
    """the ids written, in order. ``unverified``: those of them that were written and accepted by the FM-1 but could
    not be compared afterwards (the others were read back and are byte for byte what the backup holds)"""
    unverified: list


def restore(link, file: dict, progress=None, project: str = "saved") -> Restored:
    """write a backup back (see the module text) -> Restored. The FM-1 must run SLOOP 3. Every part is read back
    and compared: the project and your sounds whole (READ), the bank images by their contents. Your sounds are the
    one part that may come back in another shape – SLOOP 3 takes older layouts of that file and keeps its own; a
    read-back of another length is then noted as "written, not compared", the same length with other bytes is an
    error.
    On an error the exception says what state each part is in: ``written`` (written and compared), ``unclear`` (the
    part that was being written: it may be the old one, the new one or – a bank or your sounds – damaged),
    ``untouched`` (not reached: as before)."""
    m = objects(file)
    plan = restore_plan(file, project)
    total = sum(len(m[i]) for i in plan) or 1
    done = 0
    written = Restored()
    written.unverified = []
    current = None

    def step(n):
        nonlocal done
        done += n
        if progress:
            progress(min(done, total), total)

    def differs(oid):
        return S3.Sloop3Error(_("{part}: the FM-1 holds something else than was written.").format(part=title(oid)))
    try:
        with S3.Client(link) as c:
            c.info()                                          # (SLOOP 3 answers: only then is anything written)
            for oid in plan:
                data = m[oid]
                current = oid
                if oid == SOUNDS:
                    c.write_file(S3.KIND_SOUNDS, data, step)
                    c.wait_idle()
                    back = c.read_file(S3.KIND_SOUNDS)
                    if back != data:
                        if len(back) == len(data):
                            raise differs(oid)
                        written.unverified.append(oid)        # (kept in SLOOP 3's own layout: nothing to compare)
                elif oid in (FM6, SYN):
                    which = S3.FM6 if oid == FM6 else S3.SYN
                    c.bank_write(which, data, step)
                    c.wait_idle()
                    if c.bank_read(which)[16:] != data[16:]:
                        raise differs(oid)
                else:
                    c.write_file(S3.KIND_PROJECT, data, step)
                    c.wait_idle()
                    if c.read_file(S3.KIND_PROJECT) != data:
                        raise differs(oid)
                written.append(oid)
                current = None
    except S3.Sloop3Error as e:
        err = Sloop3BackupError(str(e))
        err.written = list(written)
        err.unverified = list(written.unverified)
        err.unclear = [current] if current is not None else []
        err.untouched = [i for i in plan if i not in written and i != current]
        raise err from e
    return written


def dumps(file: dict) -> str:
    return json.dumps(file, separators=(",", ":"))
