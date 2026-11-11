# SPDX-License-Identifier: GPL-3.0-only
"""Complete device backups – Python port of web/fm1backup.js (Melodee).

The file format is byte-for-byte that of the web editor ("felucca-backup", version 1):
the app's backups can be restored in the editor and vice versa. In addition, the app
writes a "companion" block with device, firmware and note into it;
the editor ignores unknown fields.

Procedure (commands 65–67):
  LIST  – inventory: id, size, CRC per object
  GET   – read an object in chunks (256 bytes)
  PUT   – begin / data / commit / abort per object

Restoring first checks every byte of the file, then every object size with the
device (begin + abort, nothing is written), and restores the running music
(object 0) last – exactly like the editor.

SLOOP (2.3+) has its own backup protocol (commands 34–36) and its own
file format ("sloop-backup"): see sloopbk.py. All functions here pick the
protocol themselves (firmware key as a hint, otherwise via INFO) – the UI
does not need to know about it.
"""
from __future__ import annotations

import base64
import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .i18n import N_, _
from .protocol import Cmd, u32, r32, pack7, unpack7, crc32, ProtocolError

CHUNK = 256
FORMAT = "felucca-backup"

# Inventories the editor knows (number of objects -> IDs). Other firmwares of the
# family may have their own lists; the app backs them up generically.
_IDS = [0, 1, 2, 3, 4, 5, 6, 7, 8, 32, 33, 34]
KNOWN_ID_SETS = {
    12: _IDS,
    11: [i for i in _IDS if i != 8],
    9: list(range(9)),
    17: list(range(17)),
    21: list(range(21)),
    23: list(range(23)),
    28: list(range(28)),
    29: list(range(29)),
    57: list(range(29)) + list(range(64, 92)),
}
# Inventories of the same length with other IDs (web/fm1backup.js of that firmware): Skiff 2.x has 29 objects too –
# 0..9, its pattern bank's 16 chunks (16..31) and the sample slots 32..34
ALT_ID_SETS = {
    29: [list(range(10)) + list(range(16, 35))],
}
# JIANT FM (web/fm1backup.js BACKUP_IDS, firmware/src/editor_backup.c ED_BK_IDS): 0..9 as Felucca 1.0.3, 10 the song
# index (every song's rows and scenes), then every song's sections (40 + 4 x song + section; 2..5 stay the current
# song's sections). JIANT 0.3 .. V10.6o: 8 songs, 40..71 (43 objects); from V10.6p: 6 songs, 40..63 (35 objects; an
# older archive's songs 7 and 8 are refused at begin, rc 1). No user sample slots: 40..63 are sections, not samples.
JIANT_IDS = list(range(11)) + list(range(40, 72))
JIANT_IDS_6 = list(range(11)) + list(range(40, 64))
KNOWN_ID_SETS[43] = JIANT_IDS
KNOWN_ID_SETS[35] = JIANT_IDS_6


# CoralFlower (firmware/src/cf_backup.c CRB_IDS, web/fm1backup.js CF_BACKUP_IDS): ChoralRoot's sound objects 6..11 (the
# user sound banks, the FM6 bank, the VA and FM6 patch stores), 30 its settings, 31 its loop (since 0.6 the rhythm
# machine's record), 50..55 the sequencer's patterns and song (one store in six parts), 56..58 the sounds' unsaved
# edits, 59 the patterns' own sounds, 60..67 the tweaked pattern sounds. 0.1 .. 0.3: the first 8, 0.3.1 .. 0.4.1: 14,
# 0.4.2: 17, from 0.5: 26. No running music (0), no settings object 1, no projects, no sample slots: 32..67 are none of
# Melodee's. (8 and 14 objects stay generic inventories; 17 is a known length: CoralFlower's is an alternative there.)
CORALFLOWER_IDS = [6, 7, 8, 9, 10, 11, 30, 31, *range(50, 68)]
CORALFLOWER_ID_SETS = [CORALFLOWER_IDS[:n] for n in (8, 14, 17, 26)]
KNOWN_ID_SETS[26] = CORALFLOWER_IDS
ALT_ID_SETS.setdefault(17, []).append(CORALFLOWER_IDS[:17])


def is_coralflower(ids) -> bool:
    """is this inventory (IDs in order) CoralFlower's (any release's)?"""
    try:
        return [int(i) for i in ids] in CORALFLOWER_ID_SETS
    except (TypeError, ValueError):
        return False


def is_jiant(ids) -> bool:
    """is this inventory (IDs in order) JIANT FM's (8 or, from V10.6p, 6 songs)?"""
    try:
        return [int(i) for i in ids] in (JIANT_IDS, JIANT_IDS_6)
    except (TypeError, ValueError):
        return False


def known_ids(ids: list[int]) -> list[int] | None:
    """the known inventory these IDs (in this order) are, or None (an unknown one: backed up generically); an
    inventory of a known length with other IDs -> []"""
    sets = [s for s in [KNOWN_ID_SETS.get(len(ids))] + ALT_ID_SETS.get(len(ids), []) if s]
    if not sets:
        return None
    return next((s for s in sets if s == list(ids)), [])


class _Texts(dict):
    """dict of N_()-marked texts that translates on access (RC_TEXT[rc], RC_TEXT.get(rc, default))"""

    def __getitem__(self, key):
        return _(super().__getitem__(key))

    def get(self, key, default=None):
        return self[key] if key in self else default


# device return codes -> text
RC_TEXT = _Texts({1: N_("invalid object"), 2: N_("data faulty (check failed)"),
                  3: N_("stop playback first"), 4: N_("flash write error"), 5: N_("restart the backup (outdated)")})


class BackupError(Exception):
    pass


def is_project(oid: int, jiant: bool = False, coral: bool = False) -> bool:
    """a project (music) object; jiant: in JIANT FM's inventory (its songs' sections 40..71); coral: in CoralFlower's
    (none: its patterns and song are one store, 50..55)"""
    if coral:
        return False
    if jiant:
        return oid == 0 or 2 <= oid <= 5 or 40 <= oid < 72
    return oid == 0 or 2 <= oid <= 5 or 64 <= oid < 92


def is_sample(oid: int, jiant: bool = False, coral: bool = False) -> bool:
    """a user sample slot (32..63); JIANT FM and CoralFlower have none"""
    return not jiant and not coral and 32 <= oid < 64


def max_size(oid: int) -> int:
    return 27936 if is_project(oid) else 81920 if oid >= 32 else 3840


def project_slot(oid: int) -> int | None:
    """Object ID -> project slot 1..32 (0 = running music)."""
    if oid == 0:
        return 0
    if 2 <= oid <= 5:
        return oid - 1
    if 64 <= oid < 92:
        return oid - 59
    return None


def project_id(slot: int) -> int:
    """Project slot 1..32 -> object ID."""
    if slot == 0:
        return 0
    if 1 <= slot <= 4:
        return slot + 1
    if 5 <= slot <= 32:
        return slot + 59
    raise ValueError("Projektplatz 1..32")


def rc_text(rc: int) -> str:
    return RC_TEXT.get(rc) or _("error {code}").format(code=rc)


def _check(rc: int):
    if rc:
        raise BackupError(rc_text(rc))


def object_title(oid: int, sloop: bool = False, jiant: bool = False, coral: bool = False) -> str:
    """Name of a backup object in plain language ("Preset bank U073–U090" instead of "Object 17"); jiant: in JIANT FM's
    inventory (its songs' sections); coral: in CoralFlower's."""
    try:
        from . import backup_inspect as BI
        return BI.describe(oid, b"", 0, 26 if coral else 43 if jiant else 57, "", sloop=sloop, jiant=jiant,
                           coral=coral).title
    except Exception:                               # noqa: BLE001 – the name is just a bonus
        return _("object {id}").format(id=oid)


def _with_written(e: BaseException, written: list) -> BaseException:
    """append to an error what had already been written before the abort (for the feedback)."""
    if not hasattr(e, "written") or not getattr(e, "written"):
        try:
            e.written = list(written)
        except Exception:                           # noqa: BLE001
            pass
    return e


@dataclass
class ObjInfo:
    id: int
    size: int
    crc: int
    proto: str = "felucca"          # "felucca" (65–67) or "sloop" (34–36)


FELUCCA, SLOOP = "felucca", "sloop"
SLOOP_FORMAT = "sloop-backup"
# firmwares that speak SLOOP's backup protocol (34–36) and write its "sloop-backup" files: SLOOP and its fork KARMA
# (editor.c ED_BK_LIST / GET / PUT, web/editor.html BK: the same commands, objects 0..10 and 32..35, the same file)
SLOOP_PROTOCOL = (SLOOP, "karma")
# M-VAVE's FM1 Open: no editor protocol; its own file transfer protocol and file (fm1openbk.py), picked by the firmware key
FM1OPEN_KEY, FM1OPEN_FORMAT = "fm1-open", "fm1open-backup"


def protocol(link, hint: str = "") -> tuple[str, object]:
    """which backup protocol does the device speak? -> (FELUCCA|SLOOP, info or None).
    hint: firmware key (ctx.firmware_key), if known."""
    from . import sloopbk
    if hint in ("melodee", "skiff", "felucca", "coralflower") or (hint or "").startswith("jiant"):
        return FELUCCA, None
    try:
        info = sloopbk.read_info(link)
    except Exception:                               # noqa: BLE001 – then as before
        return (SLOOP if hint in SLOOP_PROTOCOL else FELUCCA), None
    if info.firmware_name in SLOOP_PROTOCOL or hint in SLOOP_PROTOCOL:
        proto = sloopbk.info_proto(info.raw)
        if proto and proto < sloopbk.MIN_PROTO:
            raise BackupError(_("{version} cannot make backups over USB yet – that needs SLOOP 2.3 or later. "
                                "Please update SLOOP.").format(version=info.version))
        return SLOOP, info
    return FELUCCA, info


def parse_manifest(a) -> list[ObjInfo]:
    if not a or a[0] != 1:
        raise BackupError(_("the device does not know this backup protocol"))
    _check(a[1])
    n = a[2]
    if len(a) != 3 + n * 11:
        raise BackupError(_("incomplete inventory"))
    out = []
    for i in range(n):
        p = 3 + i * 11
        oid, size, crc = a[p], r32(a, p + 1), r32(a, p + 6)
        if size > max_size(oid) or (not size and crc):
            raise BackupError(_("object too large"))
        out.append(ObjInfo(oid, size, crc))
    if known_ids([o.id for o in out]) == []:
        raise BackupError(_("unexpected object order"))
    return out


def list_objects(link, hint: str = "") -> list[ObjInfo]:
    if protocol(link, hint)[0] == SLOOP:
        from . import sloopbk
        return sloopbk.list_objects(link)
    return parse_manifest(link.request(Cmd.BACKUP_LIST, [], timeout=3.0, retries=0))


def read_object(link, o: ObjInfo, progress: Callable[[int], None] | None = None) -> bytes:
    if o.proto == SLOOP:
        from . import sloopbk
        return sloopbk.read_object(link, o, progress)
    buf = bytearray(o.size)
    for off in range(0, o.size, CHUNK):
        size = min(CHUNK, o.size - off)
        a = link.request(Cmd.BACKUP_GET, [o.id, *u32(off), size & 127, size >> 7], timeout=1.0, retries=1)
        _check(a[1])
        if a[0] != o.id or r32(a, 2) != off or (a[7] | a[8] << 7) != size:
            raise BackupError(_("unexpected answer while reading"))
        buf[off:off + size] = unpack7(a[9:], size)
        if progress:
            progress(size)
    data = bytes(buf)
    if crc32(data) != o.crc:
        raise BackupError(_("The device changed during the backup. Please try again with playback stopped."))
    return data


def capture(link, firmware: str, progress: Callable[[int, int], None] | None = None,
            companion: dict | None = None, firmware_key: str = "") -> dict:
    """read a complete backup – in the format that belongs to the firmware (Melodee/Felucca or SLOOP)."""
    if (firmware_key or (companion or {}).get("firmware", "")) == FM1OPEN_KEY:   # (M-VAVE's FM1 Open: fm1openbk)
        from . import fm1openbk
        f = fm1openbk.capture(link, firmware, progress, companion)
        read(f)
        return f
    proto, info = protocol(link, firmware_key or (companion or {}).get("firmware", ""))
    if proto == SLOOP:
        from . import sloopbk
        f = sloopbk.capture(link, firmware, progress, companion, info)
        read(f)
        return f
    manifest = parse_manifest(link.request(Cmd.BACKUP_LIST, [], timeout=3.0, retries=0))
    total = sum(o.size for o in manifest) or 1
    done = 0

    def step(n):
        nonlocal done
        done += n
        if progress:
            progress(done, total)

    objects = []
    for o in manifest:
        data = read_object(link, o, step)
        objects.append({"id": o.id, "size": o.size, "crc": o.crc, "data": base64.b64encode(data).decode()})
    f = {"format": FORMAT, "version": 1, "firmware": firmware,
         "created": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"), "objects": objects}
    if companion:
        f["companion"] = companion
    read(f)        # the same check as when loading
    return f


@dataclass
class Archive:
    file: dict
    objects: list[dict]          # with "bytes"
    known: bool                  # inventory the editor knows

    @property
    def format(self) -> str:
        return self.file.get("format", FORMAT)

    @property
    def sloop(self) -> bool:
        return self.format == SLOOP_FORMAT

    @property
    def fm1open(self) -> bool:
        """M-VAVE's FM1 Open (fm1openbk.py: its own protocol and file)"""
        return self.format == FM1OPEN_FORMAT

    @property
    def firmware(self) -> str:
        return self.file.get("firmware", "")

    @property
    def created(self) -> str:
        return self.file.get("created") or self.file.get("date", "")

    @property
    def companion(self) -> dict:
        return self.file.get("companion") or {}

    @property
    def coralflower(self) -> bool:
        """CoralFlower's inventory (no projects, no running music: its patterns and song are objects 50..55)"""
        return not self.sloop and is_coralflower(o["id"] for o in self.objects)

    def project_objects(self):
        if self.coralflower or self.fm1open:        # (FM1 Open's projects are no Felucca projects)
            return []
        return [o for o in self.objects if is_project(o["id"]) and o["id"] != 0 and o["size"]]


def read(file) -> Archive:
    if isinstance(file, (str, bytes)):
        try:
            file = json.loads(file)
        except ValueError:
            raise BackupError(_("Not a backup file (no JSON)"))
    if isinstance(file, dict) and file.get("format") == SLOOP_FORMAT:
        from . import sloopbk
        m = sloopbk.objects(file)
        objs = [{**o, "id": int(o["id"]), "size": len(m[int(o["id"])]), "crc": int(o["crc"]) & 0xFFFFFFFF,
                 "bytes": m[int(o["id"])]} for o in file["objects"]]
        return Archive(file=file, objects=objs, known=True)
    if isinstance(file, dict) and file.get("format") == FM1OPEN_FORMAT:
        from . import fm1openbk
        try:
            m = fm1openbk.objects(file)
        except fm1openbk.FM1OpenBackupError as e:
            raise BackupError(str(e))
        objs = [{"id": o["id"], "size": o["size"], "crc": o["crc"], "bytes": m[o["id"]]} for o in file["objects"]]
        return Archive(file=file, objects=objs, known=True)
    if not isinstance(file, dict) or file.get("format") != FORMAT or file.get("version") != 1 \
            or not isinstance(file.get("objects"), list) or not file["objects"]:
        raise BackupError(_("Not a complete Felucca/Melodee backup"))
    try:
        known = known_ids([o["id"] for o in file["objects"]])
    except (KeyError, TypeError):
        raise BackupError(_("invalid object in the backup"))
    objs = []
    for i, o in enumerate(file["objects"]):
        try:
            oid, size, crc, data = o["id"], o["size"], o["crc"], o["data"]
        except (KeyError, TypeError):
            raise BackupError(_("invalid object in the backup"))
        if known == []:
            raise BackupError(_("unexpected object order in the backup"))
        if not isinstance(oid, int) or not isinstance(size, int) or not 0 <= size <= max_size(oid) or not isinstance(crc, int) \
                or not 0 <= crc <= 0xFFFFFFFF or not isinstance(data, str) or len(data) != 4 * ((size + 2) // 3):
            raise BackupError(_("invalid object in the backup"))
        try:
            raw = base64.b64decode(data, validate=True)
        except ValueError:
            raise BackupError(_("Backup damaged (Base64)"))
        if len(raw) != size or crc32(raw) != crc:
            raise BackupError(_("Checksum in the backup does not match"))
        objs.append({**o, "bytes": raw})
    if is_coralflower(o["id"] for o in objs):       # (no running music and no object 1: its sounds may be empty)
        if not any(o["size"] for o in objs):
            raise BackupError(_("The backup holds nothing"))
    elif not objs[0]["size"] or (len(objs) > 1 and not objs[1]["size"]):
        raise BackupError(_("The backup does not contain the running music or the settings"))
    return Archive(file=file, objects=objs, known=bool(known))


def load_file(path: Path) -> Archive:
    return read(Path(path).read_text("utf-8"))


def dumps(file: dict) -> str:
    """File text as the respective web editor writes it (SLOOP: JSON.stringify(f, null, 1))."""
    if file.get("format") == SLOOP_FORMAT:
        from . import sloopbk
        return sloopbk.dumps(file)
    if file.get("format") == FM1OPEN_FORMAT:
        from . import fm1openbk
        return fm1openbk.dumps(file)
    return json.dumps(file, separators=(",", ":"))


def write_text_atomic(path: Path, text: str, keep_times: tuple[int, int] | None = None):
    """Write a file completely or not at all: first into a hidden .tmp file in the same folder
    (with fsync), then rename. If writing aborts (disk full, process killed), the old file
    stays unchanged and the .tmp file is removed again – as far as possible.
    keep_times: (atime_ns, mtime_ns) of the new file (change the note without changing the date)."""
    import os
    import tempfile
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _sweep_tmp(path.parent)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            mode = path.stat().st_mode & 0o777
        except OSError:
            mode = 0o644
        os.chmod(tmp, mode)
        if keep_times is not None:
            os.utime(tmp, ns=keep_times)
        tmp.replace(path)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _sweep_tmp(folder: Path, older_than_s: float = 600):
    """Clean up leftovers of aborted writes (.<name>.*.tmp) – only older ones that nobody is writing any more."""
    import time
    try:
        now = time.time()
        for p in folder.glob(".*.tmp"):
            try:
                if now - p.stat().st_mtime > older_than_s:
                    p.unlink()
            except OSError:
                pass
    except OSError:
        pass


def write_file(path: Path, file: dict):
    write_text_atomic(Path(path), dumps(file))


# ------------------------------------------------------------- Restore ---

def _put(link, args, timeout=4.0) -> list[int]:
    a = link.request(Cmd.BACKUP_PUT, args, timeout=timeout, retries=0)
    return a


def write_object(link, oid: int, data: bytes, progress: Callable[[int], None] | None = None,
                 proto: str | None = None):
    """write a single object (begin, data, commit; abort on error)."""
    if (proto or protocol(link)[0]) == SLOOP:
        from . import sloopbk
        sloopbk.write_object(link, oid, data, progress)
        return
    a = _put(link, [0, oid, *u32(len(data)), *u32(crc32(data))])
    _check(a[2])
    try:
        for off in range(0, len(data), CHUNK):
            chunk = data[off:off + CHUNK]
            a = _put(link, [1, oid, *u32(off), *pack7(chunk)])
            _check(a[2])
            if progress:
                progress(len(chunk))
        a = _put(link, [2, oid], timeout=8.0)
        _check(a[2])
    except Exception:
        try:
            _put(link, [3, oid])
        except Exception:
            pass
        raise


def preflight(link, oid: int, data: bytes, proto: str | None = None) -> int:
    """asks the device whether it would accept this object – without writing.
    (SLOOP only checks the size at begin; the content only at commit.)"""
    if (proto or protocol(link)[0]) == SLOOP:
        from . import sloopbk
        rc = sloopbk.parse_put(link.request(*sloopbk.req_begin(oid, len(data), crc32(data)), timeout=1.5,
                                            retries=1))["rc"]
        try:
            link.request(*sloopbk.req_abort(oid), timeout=1.0, retries=0)
        except Exception:                           # noqa: BLE001
            pass
        return rc
    a = _put(link, [0, oid, *u32(len(data)), *u32(crc32(data))])
    _put(link, [3, oid])
    return a[2]


def _match(link, archive: Archive, hint: str = "") -> tuple[str, object]:
    proto, info = protocol(link, hint)
    if archive.sloop and proto != SLOOP:
        raise BackupError(_("This is a SLOOP backup, but the FM-1 runs a different firmware. "
                            "Nothing was written."))
    if not archive.sloop and proto == SLOOP:
        raise BackupError(_("This is a Melodee/Felucca backup, but the FM-1 runs SLOOP – the formats "
                            "do not match. Nothing was written."))
    if archive.sloop and info is not None and info.firmware_name != "karma" and "karma" in archive.firmware.lower():
        # (KARMA reads SLOOP's backups up to 2.6, as its own editor does; SLOOP cannot read KARMA's projects, format 7)
        raise BackupError(_("This backup comes from {backup}, the FM-1 runs {device}, which cannot read KARMA's "
                            "projects and drum kits. Nothing was written.").format(backup=archive.firmware,
                                                                                   device=info.version))
    return proto, info


def check_16(archive: Archive, supported, device_version: str = ""):
    """Backup from Melodee 1.6 onto a firmware without the 1.6 inventory? -> BackupError with explanation (before any
    write; otherwise the device would only report "older format" or "does not know …")."""
    from . import melodee16 as M
    if archive.sloop or is_jiant(o["id"] for o in archive.objects) or archive.coralflower:
        return                                      # (JIANT's song sections 64..71 and CoralFlower's pattern sounds
                                                    # 64..67 are no Melodee 1.6 projects)
    if M.backup_is_16(archive.firmware, archive.companion, len(archive.objects), [o["id"] for o in archive.objects
                                                                                     if o["size"]]) \
            and M.device_before_16(device_version, len(supported)):
        c = archive.companion
        raise BackupError(M.restore_refusal(c.get("firmware_version") or archive.firmware, device_version))


def restore(link, archive: Archive, progress: Callable[[int, int], None] | None = None,
            firmware_key: str = ""):
    if archive.fm1open or firmware_key == FM1OPEN_KEY:
        return _restore_fm1open(link, archive, progress)
    proto, info = _match(link, archive, firmware_key)
    if proto == SLOOP:
        from . import sloopbk
        sloopbk.restore(link, archive.file, progress, info=info)
        return archive
    total = sum(o["size"] for o in archive.objects) or 1
    done = 0

    def step(n):
        nonlocal done
        done += n
        if progress:
            progress(done, total)

    supported = parse_manifest(link.request(Cmd.BACKUP_LIST, [], timeout=3.0, retries=0))
    sup_ids = {o.id for o in supported}
    jiant = is_jiant(o.id for o in supported) or is_jiant(o["id"] for o in archive.objects)
    coral = is_coralflower(o.id for o in supported) or archive.coralflower
    live = next((o.size for o in supported if o.id == 0), 0)
    check_16(archive, supported)
    if len(supported) != 57:
        for o in archive.objects:
            if is_project(o["id"], jiant, coral) and o["size"] > live and not (live >= 19008 and o["size"] in (19084, 19092, 20224)):
                raise BackupError(_("This backup needs a firmware with a larger recording memory"))
    for o in archive.objects:
        if o["id"] not in sup_ids and o["size"]:
            raise BackupError(_("The firmware on the FM-1 does not know “{part}” from the backup "
                                "(different version). Nothing was written.").format(part=object_title(o['id'], jiant=jiant,
                                                                                         coral=coral)))
    # pre-check every object before anything is replaced
    for o in archive.objects:
        if not is_sample(o["id"], jiant, coral) and o["size"] and o["id"] in sup_ids:
            rc = preflight(link, o["id"], o["bytes"], FELUCCA)
            if rc == 1:
                raise BackupError(_("The backup has an older format. Restore it with Melodee 1.2 first. "
                                    "Nothing was written."))
            if rc == 3:
                raise BackupError(_("Please stop playback on the FM-1 first – nothing was written."))
            if rc:
                raise BackupError(_("“{part}”: {error}. Nothing was written.").format(
                    part=object_title(o['id'], jiant=jiant, coral=coral), error=rc_text(rc)))
    written: list[int] = []
    try:
        if len(archive.objects) == 28 and 28 in sup_ids:
            _check(_put(link, [0, 28, *u32(0), *u32(0)])[2])
            _check(_put(link, [2, 28])[2])
        order = archive.objects[2:] + archive.objects[1:2] + archive.objects[0:1]   # running music last
        for o in order:
            oid, data = o["id"], o["bytes"]
            if oid not in sup_ids:
                continue
            if is_sample(oid, jiant, coral):
                _restore_sample(link, oid - 32, data, step)
            else:
                write_object(link, oid, data, step, FELUCCA)
            written.append(oid)
    except Exception as e:
        raise _with_written(e, written)
    if coral:
        restart_coralflower(link)
    return archive


def _restore_fm1open(link, archive: Archive, progress=None, ids=None) -> Archive:
    """M-VAVE's FM1 Open: its own protocol and file (fm1openbk.py); a Felucca/SLOOP backup does not fit it"""
    from . import fm1openbk
    if not archive.fm1open:
        raise BackupError(_("This backup is not from FM1 Open, but the FM-1 runs FM1 Open – the formats do not match. "
                            "Nothing was written."))
    try:
        fm1openbk.restore(link, archive.file, progress, ids=ids)
    except fm1openbk.FM1OpenBackupError as e:
        raise BackupError(str(e))
    return archive


def restart_coralflower(link) -> bool:
    """CoralFlower reads what was restored at its next start (cf_backup.c: the restored settings and patterns wait for
    it, nothing in RAM is saved over them): RESTART (72), as its web installer does after a restore (fm1backup.js
    restartDevice). The FM-1 restarts once it has sent the reply and reconnects. -> False: no reply (restart it by hand)"""
    try:
        _check(link.request(CORALFLOWER_RESTART, [], timeout=2.0, retries=0)[0])
        return True
    except Exception:                               # noqa: BLE001 – what was restored stays in flash either way
        return False


CORALFLOWER_RESTART = 72                            # (cf_backup.c CRB_RESTART; on Melodee 72 is AUDIO_STATS)


def _restore_sample(link, slot: int, data: bytes, step):
    def check(a):
        if a[0] != slot:
            raise BackupError(_("unexpected sample answer"))
        _check(a[-1])
    if not data:
        check(link.request(Cmd.SMP_ERASE, [slot], timeout=4.0, retries=0))
        return
    if len(data) < 512:
        raise BackupError(_("sample object too short"))
    check(link.request(Cmd.SMP_BEGIN, [slot], timeout=4.0, retries=0))
    for off in range(512, len(data), CHUNK):
        chunk = data[off:off + CHUNK]
        check(link.request(Cmd.SMP_WRITE, [slot, off & 127, off >> 7 & 127, off >> 14 & 127, *pack7(chunk)],
                           timeout=4.0, retries=0))
        step(len(chunk))
    check(link.request(Cmd.SMP_END, [slot, *pack7(data[:480])], timeout=4.0, retries=0))
    step(512)


# ------------------------------------------------- partial restore ---

def restore_selected(link, archive: Archive, ids, progress: Callable[[int, int], None] | None = None,
                     expect: dict | None = None, clear_extension: bool = False,
                     cancelled: Callable[[], bool] | None = None) -> list[int]:
    """restore only selected objects – the same rules as restore():
    first every file check (read), then a pre-check of every object with the device (begin+abort),
    only then write; projects/banks/samples, then settings, the running music last.
    expect: id -> (size, crc) as seen when planning; if the device has changed since then, it aborts.
    Returns the written IDs in order."""
    want = set(ids)
    by_id = {o["id"]: o for o in archive.objects}
    missing = want - set(by_id)
    if missing:
        raise BackupError(_("“{part}” is not in the backup").format(part=object_title(sorted(missing)[0], archive.sloop)))
    if archive.fm1open:                             # (M-VAVE's FM1 Open: fm1openbk.py)
        _restore_fm1open(link, archive, progress, ids=want)
        return [o["id"] for o in archive.objects if o["id"] in want]
    proto, info = _match(link, archive)
    if proto == SLOOP:
        return _restore_selected_sloop(link, archive, want, progress, expect, cancelled, info)
    supported = parse_manifest(link.request(Cmd.BACKUP_LIST, [], timeout=3.0, retries=0))
    check_16(archive, supported)
    sup = {o.id: o for o in supported}
    jiant = is_jiant(o.id for o in supported) or is_jiant(o["id"] for o in archive.objects)
    coral = is_coralflower(o.id for o in supported) or archive.coralflower
    for oid in want:
        if oid not in sup and by_id[oid]["size"]:
            raise BackupError(_("The firmware on the FM-1 does not know “{part}” from the backup "
                                "(different version). Nothing was written.").format(
                part=object_title(oid, jiant=jiant, coral=coral)))
    if expect:
        for oid, (size, crc) in expect.items():
            cur = sup.get(oid)
            if cur is not None and (cur.size, cur.crc) != (size, crc):
                raise BackupError(_("The device has changed since it was read. Please select and confirm "
                                    "again – nothing was written."))
    order_ids = [o["id"] for o in archive.objects[2:] + archive.objects[1:2] + archive.objects[0:1]]
    todo = [by_id[i] for i in order_ids if i in want and i in sup]
    total = sum(o["size"] for o in todo) or 1
    done = 0

    def step(n):
        nonlocal done
        done += n
        if progress:
            progress(done, total)

    for o in todo:
        if not is_sample(o["id"], jiant, coral) and o["size"]:
            rc = preflight(link, o["id"], o["bytes"], FELUCCA)
            if rc == 1:
                raise BackupError(_("“{part}”: The device does not accept this format "
                                    "(older or different firmware). Nothing was written.").format(
                    part=object_title(o['id'], jiant=jiant, coral=coral)))
            if rc == 3:
                raise BackupError(_("Please stop playback on the FM-1 first – nothing was written."))
            if rc:
                raise BackupError(_("“{part}”: {error}. Nothing was written.").format(
                    part=object_title(o['id'], jiant=jiant, coral=coral), error=rc_text(rc)))
    written: list[int] = []
    try:
        if clear_extension and 28 in sup and 28 not in want:
            _check(_put(link, [0, 28, *u32(0), *u32(0)])[2])
            _check(_put(link, [2, 28])[2])
        for o in todo:
            if cancelled and cancelled():
                err = BackupError(_("cancelled"))
                err.cancelled = True
                raise err
            if is_sample(o["id"], jiant, coral):
                _restore_sample(link, o["id"] - 32, o["bytes"], step)
            else:
                write_object(link, o["id"], o["bytes"], step, FELUCCA)
            written.append(o["id"])
    except Exception as e:
        raise _with_written(e, written)
    if coral and written:
        restart_coralflower(link)
    return written


def written_text(e: BaseException, planned, sloop: bool = False, titles: dict | None = None) -> str:
    """Feedback after an abort: what is on the device now? planned: IDs in write order."""
    done = list(getattr(e, "written", None) or [])
    titles = titles or {}

    def name(i):
        return titles.get(i) or object_title(i, sloop)
    rest = [i for i in planned if i not in done]
    if not done:
        return _("Nothing was changed on the FM-1.")
    txt = _("Already written to the FM-1: {parts}.").format(parts=", ".join(name(i) for i in done))
    if rest:
        txt += " " + _("Still in the old state: {parts}.").format(parts=", ".join(name(i) for i in rest))
    return txt


def _restore_selected_sloop(link, archive: Archive, want: set, progress, expect, cancelled, info) -> list[int]:
    from . import sloopbk
    if expect:
        cur = {o.id: o for o in sloopbk.list_objects(link)}
        for oid, (size, crc) in expect.items():
            c = cur.get(oid)
            if c is not None and (c.size, c.crc) != (size, crc):
                raise BackupError(_("The device has changed since it was read. Please select and confirm "
                                    "again – nothing was written."))
    return sloopbk.restore(link, archive.file, progress, ids=sorted(want), cancelled=cancelled, info=info)


def capture_live(link, firmware: str, progress=None, companion: dict | None = None) -> Archive:
    """read the current device state into memory only (for comparisons) – without a file."""
    return read(capture(link, firmware, progress, companion))
