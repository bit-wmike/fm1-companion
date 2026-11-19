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

Objects 0 and 1 hold the same bytes the web editor's own "Backup all" puts into its .s3b file ("S3BK" version 1:
web/src/files.js backupAll / restoreAll). ``to_s3b`` writes such a file from a backup (the project and your sounds –
all an .s3b holds), ``from_s3b`` makes a backup file of one (without the banks and the settings, which an .s3b does
not have; such a backup restores the project and your sounds only).

``decode_sounds``, ``decode_fm6``, ``decode_syn``, ``decode_settings`` and ``project_head`` read the parts for the
Backup Browser (backup_inspect.py) – offline, nothing is sent.

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


# ------------------------------------------------------- the editor's .s3b ---
S3B_MAGIC = b"S3BK"


def to_s3b(file: dict, project: str = "saved") -> bytes:
    """the editor's backup file of a SLOOP 3 backup: "S3BK", 1, the count, 0, 0; then each part: kind (0 the project,
    1 your sounds), place 0, length u32, the bytes. project: "saved" (object 0; the state at that moment when nothing
    was saved) or "now" (object 2). Sloop3BackupError if there is no project."""
    m = objects(file)
    proj = m.get(NOW) if project == "now" else (m.get(PROJECT) or m.get(NOW))
    parts = []
    if proj:
        parts.append((0, 0, proj))
    if m.get(SOUNDS):
        parts.append((1, 0, m[SOUNDS]))
    if not proj:
        raise Sloop3BackupError(_("The backup does not contain a project"))
    out = bytearray(S3B_MAGIC + bytes([1, len(parts), 0, 0]))
    for kind, slot, data in parts:
        out += bytes([kind, slot]) + S3.w32(len(data)) + data
    return bytes(out)


def s3b_parts(raw: bytes) -> list[tuple[int, int, bytes]]:
    """the parts of an .s3b file -> [(kind, place, bytes)], read as the editor's restoreAll reads them.
    Sloop3BackupError if it is not one."""
    raw = bytes(raw)
    if len(raw) < 8 or raw[:4] != S3B_MAGIC:
        raise Sloop3BackupError(_("Not a SLOOP 3 editor backup (.s3b)"))
    if raw[4] != 1:
        raise Sloop3BackupError(_("This .s3b file has a version the app does not know ({v}).").format(v=raw[4]))
    parts, o = [], 8
    for _i in range(raw[5]):
        if o + 6 > len(raw):
            raise Sloop3BackupError(_("The .s3b file is cut off."))
        kind, slot, ln = raw[o], raw[o + 1], S3.u32(raw, o + 2)
        if kind not in (0, 1) or o + 6 + ln > len(raw) or ln > MAX_OBJECT:
            raise Sloop3BackupError(_("The .s3b file is cut off."))
        parts.append((kind, slot, raw[o + 6:o + 6 + ln]))
        o += 6 + ln
    return parts


def s3b_projects(raw: bytes) -> list[int]:
    """the places of the projects in an .s3b (more than one: a backup of SLOOP 3 before 3.3.5, which had 8)"""
    return [slot for kind, slot, _d in s3b_parts(raw) if kind == 0]


def from_s3b(raw: bytes, place: int | None = None, companion: dict | None = None, name: str = "") -> dict:
    """a SLOOP 3 backup file (this module's format) from the editor's .s3b. place: which project, when it holds
    several (None: the only one, else the last – as the editor proposes). Only the project and your sounds are in an
    .s3b: the banks and the settings are left empty, and the banks' sources say "none", so a restore of it writes the
    project and your sounds only."""
    parts = s3b_parts(raw)
    projs = [(slot, d) for kind, slot, d in parts if kind == 0]
    if not projs:
        raise Sloop3BackupError(_("The backup does not contain a project"))
    if place is None:
        proj = projs[-1][1]
    else:
        hit = [d for slot, d in projs if slot == place]
        if not hit:
            raise Sloop3BackupError(_("The .s3b file holds no project at place {n}.").format(n=place + 1))
        proj = hit[0]
    sounds = next((d for kind, _s, d in parts if kind == 1), b"")
    f = {"format": FORMAT, "version": VERSION, "firmware": NAME_S3B,
         "created": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
         "info": {"proto": 2, "tracks": 5, "version": "", "sounds_used": 0, "project_saved": True},
         "bank": {"fm6_source": 0, "fm6_used": 0, "syn_source": 0},
         "samples_not_included": [], "from_s3b": name or True,
         "objects": [_obj(PROJECT, proj), _obj(SOUNDS, sounds), _obj(NOW, b""), _obj(FM6, b""), _obj(SYN, b""),
                     _obj(SETTINGS, b"")]}
    if companion:
        f["companion"] = companion
    objects(f)
    return f


NAME_S3B = "SLOOP 3 (editor .s3b)"


# ------------------------------------------------------ reading the parts ---
ENGINES = ("ANALOG", "DRUM", "DIGITAL", "LOFI", "PHASE", "WHEEL", "NOISE", "VOICE", "TRIO", "PHYS", "FM6", "SAMPLE",
           "GRAIN", "SLICE")                  # engine/engine.h ENG_* (web/src/core.js ENGINES)
KINDS = ("BASS", "KEYS", "ORGAN", "PAD", "LEAD", "PLUCK", "BRASS", "VOICE", "FX", "KIT")   # app/presets.h K_*
US_N, US_REC, US_HEAD = 32, 80, 8
US_LEN = US_HEAD + US_N * US_REC
FAV_BASE_USER, FAV_BASE_FM6 = 1000, 2000
HS_NAMES = ("KNOB ACCEL", "LIGHTS", "KEYS", "NOTES", "SPEAKER LOWCUT", "USB AUDIO", "USB RATE", "FILTER RESO",
            "DRUM PREVIEW", "2ND CORE")      # fw/main.c HS_LABEL (3.4.0)
MORE_NAMES = ("MIDI IN", "SYNC", "MIDI OUT", "CLOCK OUT", "DRUM MAP", "REMOTE CH", "FX CH", "BEND", "SCREEN COLOR",
              "DLY TIME", "DLY COLR", "DLY MIX", "REV DAMP", "CHO RATE", "REC MODE", "REC BARS", "COUNT-IN")


def _text(b: bytes) -> str:
    return "".join(chr(x) for x in b.split(b"\0")[0] if 32 <= x < 127).strip()


def decode_sounds(data: bytes) -> dict | None:
    """your sounds ("S3US", app/usersnd.h): {"places": [{"place" 1..32, "used", "engine", "kind", "name", "raw"}],
    "favourites": [u16 …], "theme": bool}. None if it is not that file."""
    d = bytes(data)
    if len(d) < US_LEN or d[:4] != b"S3US" or d[4] != 1 or d[5] != US_N:
        return None
    places = []
    for k in range(US_N):
        r = d[US_HEAD + k * US_REC:US_HEAD + (k + 1) * US_REC]
        used = r[0] == 0xA5 and r[1] < len(ENGINES) and r[1] != 1
        places.append({"place": k + 1, "used": used, "engine": r[1], "kind": r[2], "name": _text(r[4:16]) if used else "",
                       "raw": r})
    favs = []
    if len(d) >= US_LEN + 2 + 32 and d[US_LEN:US_LEN + 2] == b"FV":
        favs = [x for x in (S3.u16(d, US_LEN + 2 + 2 * i) for i in range(16)) if x != 0xFFFF]
    theme = len(d) >= US_LEN + 2 + 32 + 40 and d[US_LEN + 34:US_LEN + 38] == b"THM2"
    return {"places": places, "favourites": favs, "theme": theme}


def decode_fm6(img: bytes) -> list[dict] | None:
    """your FM6 bank ("S3F6", app/xbank.h): 27 DX7 voices (VMEM, 128 bytes, the name in its last 10) ->
    [{"slot" 1..27, "used", "name", "raw"}], None if the image is not whole"""
    try:
        S3.check_image(bytes(img), S3.FM6)
    except S3.Sloop3Error:
        return None
    used = S3.u32(img, 8)
    out = []
    for k in range(27):
        v = bytes(img[16 + 128 * k:16 + 128 * (k + 1)])
        out.append({"slot": k + 1, "used": bool(used >> k & 1), "name": _text(v[118:128]), "raw": v})
    return out


def decode_syn(img: bytes) -> list[dict] | None:
    """your SYN kits ("S3SY", app/xbank.h): 4 kits of a crush value and 16 drum sounds of 22 bytes ->
    [{"kit" 1..4, "crush", "raw"}], None if the image is not whole"""
    try:
        S3.check_image(bytes(img), S3.SYN)
    except S3.Sloop3Error:
        return None
    kl = 4 + 16 * 22
    return [{"kit": k + 1, "crush": img[16 + kl * k], "raw": bytes(img[16 + kl * k:16 + kl * (k + 1)])}
            for k in range(4)]


def decode_settings(data: bytes) -> dict:
    """the settings as HOME answered them (fw/main.c ED_HOME: 32 values of HOME / SELECT – the first 10 used –, then
    the MIDI and recording settings) -> {name: value}; {} if too short"""
    d = bytes(data)
    if len(d) < 32 + 18:
        return {}
    out = {name: d[i] for i, name in enumerate(HS_NAMES)}
    for i, name in enumerate(MORE_NAMES):
        out[name] = d[32 + i]
    return out


def project_head(data: bytes) -> str:
    """what a project's first bytes tell ("GBPJ", its format): a short text, "" if they say nothing"""
    d = bytes(data)
    if len(d) >= 16 and d[:4] == b"GBPJ":                  # (app/project.c: "GBPJ", the format byte, the tempo)
        import struct
        bpm = struct.unpack_from("<f", d, 5)[0]
        if 20 <= bpm <= 400:
            return _("SLOOP 3 project, format {v}, {bpm} BPM").format(v=d[4], bpm=f"{bpm:.1f}".rstrip("0").rstrip("."))
        return _("SLOOP 3 project, format {v}").format(v=d[4])
    return ""
