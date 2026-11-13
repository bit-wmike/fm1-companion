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

Restoring first checks every byte of the file, then asks the device whether it takes
every object (begin + abort: nothing of the user's is replaced – the device stops playback
for it, and Melodee 1.6 already prepares flash space), and restores the running music
(object 0) last – exactly like the editor. What the device can only judge from the
content (rc 2 at commit) and SLOOP's "song running" come up while writing: then the
error says which parts were already written.

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

from . import fwfamily
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


# Felucca (firmware/src/editor_backup.c ED_BK_IDS, web/fm1backup.js BACKUP_IDS): 0 the running music, 1 the settings, 2..5
# the projects A..D, 6..7 the user preset banks (UPB1), 8 the FM6 patch bank of 1.0..1.0.2 (listed empty since 1.0.3; an
# older archive's bank goes into the user presets), 9 the user presets' FM6 patches (1.0.3), 32..34 the sample slots
# USR1..USR3. 1.0..1.0.2: without 9 (12 objects, Melodee's inventory), before FM6: without 8 and 9 (11).
FELUCCA_IDS = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 32, 33, 34]
KNOWN_ID_SETS[13] = FELUCCA_IDS
FELUCCA_ID_SETS = [FELUCCA_IDS, [i for i in FELUCCA_IDS if i != 9], [i for i in FELUCCA_IDS if i not in (8, 9)]]


def is_felucca(ids, firmware: str = "") -> bool:
    """is this a backup of Felucca (or of a Felucca fork with its inventory)? Its 13 objects (1.0.3 on) tell; the 12 and
    11 of 1.0..1.0.2 and before FM6 are Melodee's too: with "felucca" in the firmware name only (Skiff, SLOOP and KARMA
    have their own inventories or file format)"""
    try:
        ids = [int(i) for i in ids]
    except (TypeError, ValueError):
        return False
    return ids == FELUCCA_IDS or (ids in FELUCCA_ID_SETS and "felucca" in (firmware or "").lower())


# ChoralRoot FM-1 (firmware/src/cr_backup.c CRB_IDS, web/fm1backup.js CR_BACKUP_IDS; its own backup protocol subset,
# Felucca's commands 65-67 and RESTART 72): from 0.22, 34 objects in this order: 1 the settings (PER5), 8 the FM6 bank,
# 14..21 the CZ-1 banks A..H, 50..62 the sound pool's 13 banks, 23 the favourites, 40..49 the ten loop slots. 0.14 ..
# 0.21: 1, 6 7 the user sound banks, 8, 9 the VA patches, 10 11 the FM6 patches, 12 13 the CZ-1 tones, 14..21, 22 the
# QUAD (FM TONE) patches, 40..49 (28 objects; 27 without 22, 17 without the CZ-1 ones). LIST lists every object, an
# empty one with size 0. No running music (0), no projects, no sample slots: 40..62 are loops and sound banks, not
# Melodee's samples or projects. A restore ends in RESTART (the restored settings wait for it).
CHORALROOT_IDS = [1, 8, *range(14, 22), *range(50, 63), 23, *range(40, 50)]
CHORALROOT_IDS_OLD = [1, 6, 7, 8, 9, 10, 11, 12, 13, *range(14, 22), 22, *range(40, 50)]
CHORALROOT_ID_SETS = [CHORALROOT_IDS, CHORALROOT_IDS_OLD, [i for i in CHORALROOT_IDS_OLD if i != 22],
                      [1, 6, 7, 8, 9, 10, 11, *range(40, 50)]]
KNOWN_ID_SETS[34] = CHORALROOT_IDS
KNOWN_ID_SETS[27] = CHORALROOT_ID_SETS[2]
ALT_ID_SETS.setdefault(28, []).append(CHORALROOT_IDS_OLD)
ALT_ID_SETS.setdefault(17, []).append(CHORALROOT_ID_SETS[3])
_CHORALROOT_ALL = {1, *range(6, 24), *range(40, 63)}


def is_choralroot(ids) -> bool:
    """is this inventory (IDs) ChoralRoot FM-1's (any release's: the ten loop slots 40..49, nothing of Felucca's music)?"""
    try:
        s = [int(i) for i in ids]
    except (TypeError, ValueError):
        return False
    return 1 in s and set(range(40, 50)) <= set(s) and set(s) <= _CHORALROOT_ALL


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


def is_project(oid: int, jiant: bool = False, coral: bool = False, choral: bool = False, felucca: bool = False) -> bool:
    """a project (music) object; jiant: in JIANT FM's inventory (its songs' sections 40..71); coral: in CoralFlower's
    (none: its patterns and song are one store, 50..55); choral: in ChoralRoot's (none: its loops are slots 40..49);
    felucca: in Felucca's (its projects A..D are 2..5, as Melodee's first four)"""
    if coral or choral:
        return False
    if jiant:
        return oid == 0 or 2 <= oid <= 5 or 40 <= oid < 72
    return oid == 0 or 2 <= oid <= 5 or 64 <= oid < 92


def is_sample(oid: int, jiant: bool = False, coral: bool = False, choral: bool = False, felucca: bool = False) -> bool:
    """a user sample slot (32..63); JIANT FM, CoralFlower and ChoralRoot have none (felucca: Felucca's USR1..USR3 are
    32..34, as everywhere else)"""
    return not jiant and not coral and not choral and 32 <= oid < 64


def max_size(oid: int) -> int:
    return 27936 if is_project(oid) else 81920 if oid >= 32 else 3840


MAX_OBJECT = 0x20000            # an object of an inventory the app does not know (a fork's own: its sizes are its own)


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


def object_title(oid: int, sloop: bool = False, jiant: bool = False, coral: bool = False, choral: bool = False,
                 felucca: bool = False) -> str:
    """Name of a backup object in plain language ("Preset bank U073–U090" instead of "Object 17"); jiant: in JIANT FM's
    inventory (its songs' sections); coral: in CoralFlower's; choral: in ChoralRoot's; felucca: in Felucca's (13
    objects). (The same flags, in the same order, as is_project / is_sample.)"""
    try:
        from . import backup_inspect as BI
        return BI.describe(oid, b"", 0, 34 if choral else 26 if coral else 43 if jiant else 13 if felucca else 57, "",
                           sloop=sloop, jiant=jiant, coral=coral, choral=choral).title
    except Exception:                               # noqa: BLE001 – the name is just a bonus
        return _("object {id}").format(id=oid)


def _with_written(e: BaseException, written: list, current=None, sample: bool = False) -> BaseException:
    """append to an error what had already been written before the abort (for the feedback). current: the object
    that was being written when it happened – its state on the device is unclear when the connection broke (the
    device may have committed it, only the answer got lost), and a sample slot is erased before it is written
    (sample: it may be empty now). An object the device refused with an answer stays in its old state."""
    if not hasattr(e, "written") or not getattr(e, "written"):
        try:
            e.written = list(written)
        except Exception:                           # noqa: BLE001
            pass
    if current is not None and current not in (getattr(e, "written", None) or []) and not hasattr(e, "unclear"):
        try:
            if sample:
                e.unclear, e.unclear_sample = current, True
            elif not isinstance(e, BackupError):    # (no answer / connection lost; an rc is an answer: not committed)
                e.unclear, e.unclear_sample = current, False
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
# X0X (charlesvestal/fm1-x0x) and fm1-base's LOOM-8: their own protocol and image (backup.c, x0xbk.py: the saved
# state whole, in the update's framing); the suite keeps it as an "x0x-backup" / "loom8-backup" file
X0X = "x0x"
X0X_FORMAT = "x0x-backup"
LOOM = "loom-8"
LOOM_FORMAT = "loom8-backup"
IMAGE_FORMATS = {X0X_FORMAT: X0X, LOOM_FORMAT: LOOM}     # the suite's format -> the firmware (= the protocol's name)
IMAGE_PROTOS = (X0X, LOOM)
# ORBIT (dspaudio/orbit, a SLOOP fork) too: firmware/src/editor.c ED_BK_LIST / GET / PUT are 34-36, ED_BK_IDS 0..10 and
# 32..35 (10, its MIDI settings, always listed), web/editor.html BK: "sloop-backup" files (version 2 with product
# "ORBIT"); its INFO is "FELUCCA ORBIT 1.0.0" (protocol.Info.firmware_name "orbit"). It answers no command 65..67.
SLOOP_PROTOCOL += ("orbit",)
# … and every other fork with SLOOP's protocol the catalog audit found (fwfamily: sloopDX, Clipper, Jangada, …)
SLOOP_PROTOCOL += tuple(k for k in fwfamily.sloop_keys() if k not in SLOOP_PROTOCOL)


def is_orbit_backup(file) -> bool:
    """an ORBIT backup (SLOOP's file format: its 'firmware' the INFO string "FELUCCA ORBIT …", or product "ORBIT")"""
    f = file if isinstance(file, dict) else {}
    return f.get("format") == SLOOP_FORMAT and (f.get("product") == "ORBIT" or "orbit" in str(f.get("firmware", "")).lower())


def protocol(link, hint: str = "") -> tuple[str, object]:
    """which backup protocol does the device speak? -> (FELUCCA|SLOOP|X0X|LOOM, info or None).
    hint: firmware key (ctx.firmware_key), if known. A firmware that names itself in its INFO gets its own protocol
    (fwfamily: SLOOP's forks SLOOP's, whatever the hint says); one that answers a plain "FELUCCA v…" or an unknown
    name is asked (a LIST that changes none of the user's data: Felucca's 65, then SLOOP's 34; the device stops
    playback for it, as for every backup) – a protocol is never guessed for a write. A
    firmware with a backup protocol of its own, or none, is refused with a clear message instead of a timeout."""
    from . import sloopbk
    if hint in ("melodee", "skiff", "coralflower", "choralroot") or (hint or "").startswith("jiant"):
        return FELUCCA, None                        # ("felucca": its INFO decides – SLOOP's builds share FM-1_900)
    if hint == LOOM:                                # (fm1-base has no editor: no INFO to ask)
        return LOOM, None
    try:
        info = sloopbk.read_info(link)
    except Exception:                               # noqa: BLE001 – then as before
        fam = fwfamily.by_key(hint)
        if fam is not None and fam.backup in ("own", "none"):
            raise BackupError(no_backup_text(fam, ""))
        return (SLOOP if hint in SLOOP_PROTOCOL else hint if hint in IMAGE_PROTOS else FELUCCA), None
    if info.firmware_name == X0X:
        return X0X, info
    fam = fwfamily.by_info(info.version)
    if fam is not None and fam.backup in ("own", "none"):
        raise BackupError(no_backup_text(fam, info.version))
    if (fam is not None and fam.backup == SLOOP) or info.firmware_name in SLOOP_PROTOCOL:
        proto = SLOOP
    elif fam is not None and fam.backup == FELUCCA:
        proto = FELUCCA
    elif info.firmware_name == "skiff":             # (Skiff answers as Felucca does: its DRUM-X engine names it)
        proto = FELUCCA
    else:                                           # a plain "FELUCCA v…" or an unknown name: ask the device
        proto = probe(link, info)
    if proto == SLOOP:
        v = sloopbk.info_proto(info.raw)
        if v and v < sloopbk.MIN_PROTO:
            raise BackupError(_("{version} cannot make backups over USB yet – that needs SLOOP 2.3 or later. "
                                "Please update SLOOP.").format(version=info.version))
    return proto, info


PROBE_TIMEOUT = 1.5


def probe(link, info) -> str:
    """ask the device which backup protocol it answers – only requests that change none of the user's data ("read-only"
    in that sense: Felucca and Melodee stop playback for a LIST, and Melodee 1.6 saves the running music's stream
    into free flash for it): Felucca's LIST (65; on SLOOP and its
    forks no command), then SLOOP's LIST (34; Felucca's 34 is UI_STATE, a query, and every audited firmware's 34 reads
    or does nothing without arguments). -> FELUCCA or SLOOP; BackupError if it answers neither (its own protocol, or
    no backup over USB). The answer is kept with the link for this INFO."""
    from . import sloopbk
    from .transport import LinkError
    cached = getattr(link, "_backup_probe", None)
    if cached and cached[0] == info.version:
        proto = cached[1]
    else:
        proto = ""
        try:
            a = link.request(Cmd.BACKUP_LIST, [], timeout=PROBE_TIMEOUT, retries=0)
            if a and a[0] == 1 and len(a) >= 3:
                proto = FELUCCA
        except (LinkError, ProtocolError, OSError):
            pass
        if not proto:
            try:
                a = link.request(sloopbk.BK_LIST, [], timeout=PROBE_TIMEOUT, retries=0)
                if len(a) >= 2 and a[0] in (0, 4) and len(a) == 2 + 11 * a[1]:
                    proto = SLOOP
            except (LinkError, ProtocolError, OSError):
                pass
        try:
            link._backup_probe = (info.version, proto)
        except AttributeError:
            pass
    if not proto:
        raise BackupError(_("{version} answers none of the backup requests the app knows (neither Felucca's nor "
                            "SLOOP's). Nothing was read or written – please back up with the firmware's own editor or "
                            "tool.").format(version=info.version or _("The firmware")))
    return proto


def no_backup_text(fam, version: str = "") -> str:
    """why a firmware of the audit's table (fwfamily) cannot be backed up through the app"""
    from . import catalog
    try:
        e = catalog.load().by_key(fam.key)
        name = e.name if e else ""
    except Exception:                               # noqa: BLE001 – the catalog is a bonus here
        name = ""
    name = name or version or fam.key
    if fam.backup == "own":
        return _("{firmware} keeps its backups in a protocol of its own, which the app does not speak. Nothing was "
                 "read or written – please use the firmware's own editor for backups.").format(firmware=name)
    return _("{firmware} has no backup over USB. Nothing was read or written.").format(firmware=name)


def parse_manifest(a) -> list[ObjInfo]:
    if not a or a[0] != 1:
        raise BackupError(_("the device does not know this backup protocol"))
    _check(a[1])
    n = a[2]
    if len(a) != 3 + n * 11:
        raise BackupError(_("incomplete inventory"))
    out = []
    ids = [a[3 + i * 11] for i in range(n)]
    known = bool(known_ids(ids)) or is_choralroot(ids) or is_coralflower(ids)
    for i in range(n):
        p = 3 + i * 11
        oid, size, crc = a[p], r32(a, p + 1), r32(a, p + 6)
        if size > (max_size(oid) if known else MAX_OBJECT) or (not size and crc):
            raise BackupError(_("object too large"))
        out.append(ObjInfo(oid, size, crc))
    # (an inventory the app does not know – a fork's, even one as long as a known one – is backed up generically:
    # its objects are kept as they are; where they go back is decided by its layout when restoring)
    return out


def list_objects(link, hint: str = "") -> list[ObjInfo]:
    proto = protocol(link, hint)[0]
    if proto in IMAGE_PROTOS:                       # (X0X and LOOM-8 list nothing: the whole image, read and taken apart)
        from . import x0xbk
        return [ObjInfo(o["id"], o["size"], o["crc"], proto) for o in x0xbk.objects(x0xbk.capture(link, "", key=proto))[1]]
    if proto == SLOOP:
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
    if proto in IMAGE_PROTOS:
        from . import x0xbk
        f = x0xbk.capture(link, info.version if info is not None else firmware, progress, companion, key=proto)
        read(f)
        return f
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
    def x0x(self) -> bool:
        """X0X's project image (x0xbk.py): restored whole only"""
        return self.format == X0X_FORMAT

    @property
    def image(self) -> str:
        """the firmware whose image this is (X0X's or LOOM-8's, x0xbk.py: restored whole only); empty otherwise"""
        return IMAGE_FORMATS.get(self.format, "")

    @property
    def firmware(self) -> str:
        return self.file.get("firmware") or ""

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

    @property
    def choralroot(self) -> bool:
        """ChoralRoot's inventory (no projects, no running music: its loops are slots 40..49)"""
        return not self.sloop and is_choralroot(o["id"] for o in self.objects)

    def project_objects(self):
        if self.coralflower or self.choralroot or self.fm1open or self.image:
            return []                               # (FM1 Open's projects are no Felucca projects; X0X, LOOM-8: one
                                                    # image, its parts no project slots)
        return [o for o in self.objects if is_project(o["id"]) and o["id"] != 0 and o["size"]]


def read(file) -> Archive:
    if isinstance(file, (bytes, bytearray)):
        from . import x0xbk                         # (X0X's own .x0b file, LOOM-8's .loom8)
        if x0xbk.is_image(file):
            file = x0xbk.from_bytes(bytes(file))
    if isinstance(file, (str, bytes)):
        try:
            file = json.loads(file)
        except ValueError:
            raise BackupError(_("Not a backup file (no JSON)"))
    if isinstance(file, dict) and not isinstance(file.get("format"), str):
        raise BackupError(_("Not a complete Felucca/Melodee backup"))      # ("format": [] / {} / 5 – no backup)
    if isinstance(file, dict):                      # (fields of a hand-edited file: texts or nothing)
        for k in ("firmware", "created", "date"):
            if file.get(k) is not None and not isinstance(file[k], str):
                raise BackupError(_("invalid field “{name}” in the backup").format(name=k))
        if "companion" in file and file["companion"] is not None and not isinstance(file["companion"], dict):
            raise BackupError(_("invalid field “{name}” in the backup").format(name="companion"))
    if isinstance(file, dict) and file.get("format") == SLOOP_FORMAT:
        from . import sloopbk
        try:
            m = sloopbk.objects(file)
            objs = [{**o, "id": int(o["id"]), "size": len(m[int(o["id"])]), "crc": int(o["crc"]) & 0xFFFFFFFF,
                     "bytes": m[int(o["id"])]} for o in file["objects"]]
        except (ValueError, OverflowError, TypeError, KeyError, AttributeError, IndexError):
            raise BackupError(_("invalid object in the backup"))
        return Archive(file=file, objects=objs, known=True)
    if isinstance(file, dict) and file.get("format") == FM1OPEN_FORMAT:
        from . import fm1openbk
        try:
            m = fm1openbk.objects(file)
        except fm1openbk.FM1OpenBackupError as e:
            raise BackupError(str(e))
        except (ValueError, OverflowError, TypeError, KeyError, AttributeError):
            raise BackupError(_("invalid object in the backup"))
        objs = [{"id": o["id"], "size": o["size"], "crc": o["crc"], "bytes": m[o["id"]]} for o in file["objects"]]
        return Archive(file=file, objects=objs, known=True)
    if isinstance(file, dict) and file.get("format") in IMAGE_FORMATS:
        from . import x0xbk
        try:
            return Archive(file=file, objects=x0xbk.objects(file)[1], known=True)
        except (ValueError, OverflowError, TypeError, KeyError, AttributeError):
            raise BackupError(_("invalid object in the backup"))
    if not isinstance(file, dict) or file.get("format") != FORMAT or file.get("version") != 1 \
            or not isinstance(file.get("objects"), list) or not file["objects"]:
        raise BackupError(_("Not a complete Felucca/Melodee backup"))
    try:
        ids = [o["id"] for o in file["objects"]]
    except (KeyError, TypeError):
        raise BackupError(_("invalid object in the backup"))
    # an object ID is one 7-bit number on the wire, and every object is there once (a second "running music" in
    # place of the settings would pass every other check)
    if any(not isinstance(i, int) or isinstance(i, bool) or not 0 <= i <= 127 for i in ids) or len(set(ids)) != len(ids):
        raise BackupError(_("invalid object in the backup"))
    known = known_ids(ids)
    objs = []
    for i, o in enumerate(file["objects"]):
        try:
            oid, size, crc, data = o["id"], o["size"], o["crc"], o["data"]
        except (KeyError, TypeError):
            raise BackupError(_("invalid object in the backup"))
        if not isinstance(oid, int) or not isinstance(size, int) or \
                not 0 <= size <= (max_size(oid) if known else MAX_OBJECT) or not isinstance(crc, int) \
                or not 0 <= crc <= 0xFFFFFFFF or not isinstance(data, str) or len(data) != 4 * ((size + 2) // 3):
            raise BackupError(_("invalid object in the backup"))
        try:
            raw = base64.b64decode(data, validate=True)
        except ValueError:
            raise BackupError(_("Backup damaged (Base64)"))
        if len(raw) != size or crc32(raw) != crc:
            raise BackupError(_("Checksum in the backup does not match"))
        objs.append({**o, "bytes": raw})
    if is_choralroot(o["id"] for o in objs):
        pass                                        # (no running music; nothing is written before the first SAVE: a
                                                    # new ChoralRoot's backup is all empty, and valid – it restores
                                                    # nothing)
    elif is_coralflower(o["id"] for o in objs):     # (no running music and no object 1: its sounds may be empty)
        if not any(o["size"] for o in objs):
            raise BackupError(_("The backup holds nothing"))
    elif {o["id"] for o in objs} & {0, 1}:          # (the running music and the settings, where the inventory has them)
        if any(not o["size"] for o in objs if o["id"] in (0, 1)):
            raise BackupError(_("The backup does not contain the running music or the settings"))
    elif not any(o["size"] for o in objs):          # (a fork's own inventory without either: Saadet's starts at 1 …)
        raise BackupError(_("The backup holds nothing"))
    return Archive(file=file, objects=objs, known=bool(known))


def load_file(path: Path) -> Archive:
    raw = Path(path).read_bytes()
    from . import x0xbk
    if x0xbk.is_image(raw):                         # (X0X's own .x0b file, LOOM-8's .loom8)
        return read(raw)
    try:
        return read(raw.decode("utf-8"))
    except UnicodeDecodeError:
        raise BackupError(_("Not a backup file (no JSON)"))


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

def restore_order(objects: list) -> list:
    """the order a backup's objects are written in: as the editor does – everything else first, then the settings
    (1), the running music (0) last. The editor's files start with 0 and 1 (then by position, as it does; so do the
    inventories without running music: CoralFlower's, ChoralRoot's, Saadet's). A file that has the running music
    somewhere else (not written by the editor or the app) is ordered by ID, so that the rule still holds."""
    ids = [o["id"] for o in objects]
    if 0 not in ids or ids[0] == 0 and (1 not in ids or ids[1:2] == [1]):
        return objects[2:] + objects[1:2] + objects[0:1]
    return [o for o in objects if o["id"] not in (0, 1)] + [o for o in objects if o["id"] == 1] + \
        [o for o in objects if o["id"] == 0]


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
    """asks the device whether it would accept this object – begin + abort: the stored object is not replaced
    (Melodee 1.6 already prepares flash space for a project at begin; the old copy stays valid).
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


# ------------------------------------------------- layouts (where a backup's objects go) ---
# Felucca's places (0 the running music, 1 the settings, 2..5 the projects, 6..9 its banks, 32..34 the sample slots):
# Melodee 1.x's 12 objects, Felucca's 13, and every fork the audit found that keeps them (or fewer: fmini-1, Switch
# Blade); Melodee's own (its 57: 0..28, the projects 64..91)
FELUCCA_PLACES = frozenset(range(10)) | {32, 33, 34}
MELODEE_PLACES = frozenset(range(29)) | frozenset(range(64, 92)) | {32, 33, 34}
SKIFF_IDS = ALT_ID_SETS[29][0]
MELODEE_SETS = [KNOWN_ID_SETS[n] for n in (9, 17, 21, 23, 28, 29, 57)]    # (its inventories, release by release)


def archive_name(archive) -> str:
    """the firmware a backup comes from, as its INFO said (the file's 'firmware'; the companion block's otherwise)"""
    c = getattr(archive, "companion", None) or {}
    return str(getattr(archive, "firmware", "") or c.get("version_string") or "")


def layout_of(ids, version: str = "", key: str = "") -> str:
    """where the objects of this inventory live: a firmware's own layout (fwfamily: Saadet, DUELL, sloopDX …),
    else by the inventory – "felucca" (Felucca's places), "melodee", "skiff", "jiant", "coralflower", "choralroot" –
    else "own:<its INFO name>" (a fork's own inventory: its objects only fit itself)"""
    fam = fwfamily.by_info(version) or (fwfamily.by_key(key) if key else None)
    if fam is not None and fam.layout:
        return fam.layout
    try:
        ids = [int(i) for i in ids]
    except (TypeError, ValueError):
        ids = []
    s = set(ids)
    name = fwfamily.info_name(version)
    if is_jiant(ids):
        return "jiant"
    if is_coralflower(ids):
        return "coralflower"
    if is_choralroot(ids):
        return "choralroot"
    if ids == SKIFF_IDS:
        return "skiff"
    if s <= FELUCCA_PLACES:
        return "felucca"
    if (name == "melodee" and s <= MELODEE_PLACES) or ids in MELODEE_SETS:
        return "melodee"
    return "own:" + name


def layouts_fit(backup_layout: str, device_layout: str, reads=()) -> bool:
    """can a backup of this layout go onto a device of that one without objects landing in the wrong places?"""
    a, d = backup_layout, device_layout
    if a == d or a in reads:
        return True
    if {a, d} <= {"felucca", "melodee"}:           # (Melodee keeps Felucca's places: Felucca 1.0 took its backups)
        return True
    # a fork that answers as Felucca does and grew its own objects ("own:", no name): Felucca's keep their places
    # (its extra objects are refused when the other side does not know them)
    return {a, d} == {"felucca", "own:"}


def layout_reason(archive, device_ids, device_version: str = "") -> str:
    """why a backup's objects would mean something else on the device's firmware ("" = they fit). The one rule for
    the restore itself (check_layout) and for the pages that say so before a restore is planned."""
    ids = [o["id"] for o in archive.objects]
    a = layout_of(ids, archive_name(archive), (archive.companion or {}).get("firmware", ""))
    d = layout_of(device_ids, device_version)
    fam = fwfamily.by_info(device_version)
    fits = layouts_fit(a, d, fam.reads if fam else ())
    if fits and a == "felucca" and d == "melodee" and 9 in ids and 9 in [int(i) for i in device_ids]:
        # Felucca from 1.0.3 keeps its user presets' FM6 patches in object 9; on a Melodee that lists 9 (1.3 on) it
        # is the preset bank U145–U162. Melodee takes the 12 objects of Felucca 1.0 / Melodee 1.2 (its own legacy
        # import), not this one: it would refuse it after the projects were replaced – or, empty, clear the bank.
        fits = False
    return "" if fits else layout_text(archive, device_version)


def layout_text(archive, device_version: str = "") -> str:
    return _("This backup comes from {backup}, the FM-1 runs {device}: the two keep different things "
             "under the same backup objects, so its parts would land in the wrong places. Nothing was "
             "written.").format(backup=archive_name(archive) or _("another firmware"),
                                device=device_version or _("a different firmware"))


def check_layout(archive, device_ids, device_version: str = ""):
    """refuse (before anything is written) a backup whose objects mean something else on the device's firmware"""
    why = layout_reason(archive, device_ids, device_version)
    if why:
        raise BackupError(why)


def sample_slots(link, device_layout: str, device_version: str, ids) -> set[int]:
    """the backup objects that are user sample slots on this device (restored with SMP_BEGIN/WRITE/END; an empty one
    erases the slot). Its family's (fwfamily: Saadet has none, its 32..40 are its own); Felucca's and Melodee's 32..63;
    for an inventory of its own the device is asked, without replacing anything: an object it takes with Felucca's
    PUT is no sample (begin + abort; Felucca refuses PUT for its sample slots, rc 1). No answer is no answer: asked
    once more, then the restore stops before anything is written – a write is never guessed."""
    fam = fwfamily.by_info(device_version)
    if fam is not None and fam.samples is not None:
        return set(fam.samples) & set(ids)
    cand = {i for i in ids if 32 <= i < 64}
    if device_layout in ("jiant", "coralflower", "choralroot"):
        return set()                                # (their 40.. are songs, loops and sound banks)
    if not device_layout.startswith("own:") or not cand:
        return cand
    out = set()
    for oid in sorted(cand):
        rc = None
        for _try in range(2):                       # (a lost answer: asked once more – never guessed for a write)
            try:
                rc = _put(link, [0, oid, *u32(0), *u32(0)])[2]
                if rc == 0:
                    _put(link, [3, oid])
                break
            except BackupError:
                raise
            except Exception:                       # noqa: BLE001 – no answer
                rc = None
        if rc is None:
            raise BackupError(_("The FM-1 does not answer the question whether “{part}” is a sample slot. Nothing was "
                                "written – check the cable and try again.").format(part=object_title(oid)))
        if rc == 1:
            out.add(oid)
    return out


def guess_protocol(key: str = "", version: str = "") -> str:
    """the backup protocol of a firmware by its name alone (no device asked): FELUCCA, SLOOP, an image protocol,
    FM1OPEN_KEY – or "" when only the device can tell (a plain "FELUCCA v…", an unknown name) or it has none"""
    key = (key or "").lower()
    if key == FM1OPEN_KEY:
        return FM1OPEN_KEY
    fam = fwfamily.by_info(version) or fwfamily.by_key(key)
    if fam is not None:
        return fam.backup if fam.backup in (FELUCCA, SLOOP, *IMAGE_PROTOS) else ""
    if key in SLOOP_PROTOCOL:
        return SLOOP
    if key in IMAGE_PROTOS:
        return key
    if key in ("melodee", "skiff", "coralflower", "choralroot") or key.startswith("jiant"):
        return FELUCCA
    return ""


def match_reason(archive: Archive, proto: str, version: str = "", name: str = "") -> str:
    """why this backup does not go onto a device that speaks `proto` and answered INFO `version` (name: its
    protocol.Info.firmware_name; version "" = INFO not known: only what the protocol alone tells). "" = no
    objection here (the Felucca protocol's layouts: layout_reason). The one rule for the restore itself (_match)
    and for the pages that say so before a restore is planned."""
    from . import x0xbk
    dev_image = proto if proto in IMAGE_PROTOS else ""
    if archive.fm1open != (proto == FM1OPEN_KEY):
        return (_("This backup is not from FM1 Open, but the FM-1 runs FM1 Open – the formats do not match. "
                  "Nothing was written.") if proto == FM1OPEN_KEY else
                _("This is an FM1 Open backup, but the FM-1 runs a different firmware. Nothing was written."))
    if archive.image != dev_image:
        return (_("This is a {firmware} backup, but the FM-1 runs a different firmware. Nothing was written.")
                .format(firmware=x0xbk.profile_of(archive.image).name) if archive.image else
                _("The FM-1 runs {firmware}, which takes only its own backups. Nothing was written.").format(
                    firmware=x0xbk.profile_of(dev_image).name))
    if archive.sloop and proto != SLOOP:
        return _("This is a SLOOP backup, but the FM-1 runs a different firmware. "
                 "Nothing was written.")
    if not archive.sloop and proto == SLOOP:
        return _("This is a Melodee/Felucca backup, but the FM-1 runs SLOOP – the formats "
                 "do not match. Nothing was written.")
    if not version:
        return ""
    if archive.sloop and (name == "orbit") != is_orbit_backup(archive.file):
        # (ORBIT writes ORP8 projects and OPB3 banks, SLOOP 2.6's FUN6 is never an ORBIT project: ORBIT's editor
        # refuses any backup that is not ORBIT's, and any target that is not ORBIT – web/editor.html bkForeign)
        return _("This backup comes from {backup}, the FM-1 runs {device}: ORBIT and SLOOP store their "
                 "projects and presets differently, neither reads the other's backups. Nothing was "
                 "written.").format(backup=archive.firmware or "?", device=version)
    if archive.sloop and name != "karma" and "karma" in archive.firmware.lower():
        # (KARMA reads SLOOP's backups up to 2.6, as its own editor does; SLOOP cannot read KARMA's projects, format 7)
        return _("This backup comes from {backup}, the FM-1 runs {device}, which cannot read KARMA's "
                 "projects and drum kits. Nothing was written.").format(backup=archive.firmware, device=version)
    if archive.sloop:                               # (SLOOP's forks: sloopDX's 10 and 11, Clipper's 12..14 are their own)
        dfam, afam = fwfamily.by_info(version), fwfamily.by_info(archive.firmware)
        d = dfam.layout if dfam is not None and dfam.layout else SLOOP
        a = afam.layout if afam is not None and afam.layout else SLOOP
        if not (a == d or a in (dfam.reads if dfam is not None else ())):
            return _("This backup comes from {backup}, the FM-1 runs {device}: the two keep different "
                     "things under the same backup objects, so its parts would land in the wrong places. "
                     "Nothing was written.").format(backup=archive.firmware or "?", device=version)
    return ""


def _match(link, archive: Archive, hint: str = "") -> tuple[str, object]:
    proto, info = protocol(link, hint)
    if info is None and proto not in IMAGE_PROTOS:
        # (the protocol was taken from what the app remembers of this device, or its INFO did not arrive: the rules
        # between firmwares need the name the device itself gives – asked once more, never skipped for a write;
        # LOOM-8 has no INFO, the image protocols check their own magic)
        from . import sloopbk
        try:
            info = sloopbk.read_info(link)
        except Exception:                           # noqa: BLE001
            raise BackupError(_("The FM-1 did not answer which firmware it runs, so the app cannot check that this "
                                "backup fits it. Nothing was written – check the cable and try again."))
    why = match_reason(archive, proto, info.version if info is not None else "",
                       info.firmware_name if info is not None else "")
    if why:
        raise BackupError(why)
    return proto, info


def device_inventory(link, hint: str = "") -> tuple[str, str, list[int] | None]:
    """for the pages that check a backup against the connected device before a restore is planned: (protocol, INFO
    version, object IDs – None for the image protocols, whose inventory is the image itself). Only reads (the
    inventory request stops playback on the FM-1, as every backup does)."""
    proto, info = protocol(link, hint)
    version = info.version if info is not None else ""
    if proto in IMAGE_PROTOS:
        return proto, version, None
    if proto == SLOOP:
        from . import sloopbk
        return proto, version, [o.id for o in sloopbk.list_objects(link)]
    return proto, version, [o.id for o in parse_manifest(link.request(Cmd.BACKUP_LIST, [], timeout=3.0, retries=0))]


def check_16(archive: Archive, supported, device_version: str = ""):
    """Backup from Melodee 1.6 onto a firmware without the 1.6 inventory? -> BackupError with explanation (before any
    write; otherwise the device would only report "older format" or "does not know …")."""
    from . import melodee16 as M
    if archive.sloop or is_jiant(o["id"] for o in archive.objects) or archive.coralflower or archive.choralroot:
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
    if proto in IMAGE_PROTOS:
        from . import x0xbk
        x0xbk.restore(link, archive.file, progress)
        return archive
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
    choral = is_choralroot(o.id for o in supported) or archive.choralroot
    live = next((o.size for o in supported if o.id == 0), 0)
    dev_version = info.version if info is not None else ""
    check_layout(archive, [o.id for o in supported], dev_version)
    check_16(archive, supported)
    if len(supported) != 57:
        for o in archive.objects:
            if is_project(o["id"], jiant, coral, choral) and o["size"] > live and not (live >= 19008 and o["size"] in (19084, 19092, 20224)):
                raise BackupError(_("This backup needs a firmware with a larger recording memory"))
    for o in archive.objects:
        if o["id"] not in sup_ids and o["size"]:
            raise BackupError(_("The firmware on the FM-1 does not know “{part}” from the backup "
                                "(different version). Nothing was written.").format(part=object_title(o['id'], jiant=jiant,
                                                                                         coral=coral, choral=choral)))
    smp = set() if jiant or coral or choral else sample_slots(
        link, layout_of(sup_ids, dev_version), dev_version, [o["id"] for o in archive.objects if o["id"] in sup_ids])
    # pre-check every object before anything is replaced
    for o in archive.objects:
        if o["id"] not in smp and o["size"] and o["id"] in sup_ids:
            rc = preflight(link, o["id"], o["bytes"], FELUCCA)
            if rc == 1:
                raise BackupError(_("The backup has an older format. Restore it with Melodee 1.2 first. "
                                    "Nothing was written."))
            if rc == 3:
                raise BackupError(_("Please stop playback on the FM-1 first – nothing was written."))
            if rc:
                raise BackupError(_("“{part}”: {error}. Nothing was written.").format(
                    part=object_title(o['id'], jiant=jiant, coral=coral, choral=choral), error=rc_text(rc)))
    written: list[int] = []
    cur = None
    try:
        if len(archive.objects) == 28 and 28 in sup_ids:
            _check(_put(link, [0, 28, *u32(0), *u32(0)])[2])
            _check(_put(link, [2, 28])[2])
        order = restore_order(archive.objects)      # running music last
        for o in order:
            oid, data = o["id"], o["bytes"]
            if oid not in sup_ids:
                continue
            cur = oid
            if oid in smp:
                _restore_sample(link, oid - 32, data, step)
            else:
                write_object(link, oid, data, step, FELUCCA)
            written.append(oid)
            cur = None
    except Exception as e:
        raise _with_written(e, written, cur, cur in smp)
    if coral or choral:                             # (ChoralRoot too: its restored settings wait for the restart)
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
        err = BackupError(str(e))
        for k in ("written", "unclear", "unclear_sample"):          # (what was already written: for the feedback)
            if hasattr(e, k):
                setattr(err, k, getattr(e, k))
        raise err
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
    if proto in IMAGE_PROTOS:                       # (X0X, LOOM-8: the image whole, nothing but all of it)
        from . import x0xbk
        if want != {o["id"] for o in archive.objects}:
            raise BackupError(_("{firmware} restores a backup only whole. Nothing was written.").format(
                firmware=x0xbk.profile_of(proto).name))
        x0xbk.restore(link, archive.file, progress)
        return [o["id"] for o in archive.objects]
    if proto == SLOOP:
        return _restore_selected_sloop(link, archive, want, progress, expect, cancelled, info)
    supported = parse_manifest(link.request(Cmd.BACKUP_LIST, [], timeout=3.0, retries=0))
    dev_version = info.version if info is not None else ""
    check_layout(archive, [o.id for o in supported], dev_version)
    check_16(archive, supported)
    sup = {o.id: o for o in supported}
    jiant = is_jiant(o.id for o in supported) or is_jiant(o["id"] for o in archive.objects)
    coral = is_coralflower(o.id for o in supported) or archive.coralflower
    choral = is_choralroot(o.id for o in supported) or archive.choralroot
    for oid in want:
        if oid not in sup and by_id[oid]["size"]:
            raise BackupError(_("The firmware on the FM-1 does not know “{part}” from the backup "
                                "(different version). Nothing was written.").format(
                part=object_title(oid, jiant=jiant, coral=coral, choral=choral)))
    if expect:
        for oid, (size, crc) in expect.items():
            cur = sup.get(oid)
            if cur is not None and (cur.size, cur.crc) != (size, crc):
                raise BackupError(_("The device has changed since it was read. Please select and confirm "
                                    "again – nothing was written."))
    order_ids = [o["id"] for o in restore_order(archive.objects)]
    todo = [by_id[i] for i in order_ids if i in want and i in sup]
    total = sum(o["size"] for o in todo) or 1
    done = 0

    def step(n):
        nonlocal done
        done += n
        if progress:
            progress(done, total)

    smp = set() if jiant or coral or choral else sample_slots(link, layout_of(sup, dev_version), dev_version,
                                                              [o["id"] for o in todo])
    for o in todo:
        if o["id"] not in smp and o["size"]:
            rc = preflight(link, o["id"], o["bytes"], FELUCCA)
            if rc == 1:
                raise BackupError(_("“{part}”: The device does not accept this format "
                                    "(older or different firmware). Nothing was written.").format(
                    part=object_title(o['id'], jiant=jiant, coral=coral, choral=choral)))
            if rc == 3:
                raise BackupError(_("Please stop playback on the FM-1 first – nothing was written."))
            if rc:
                raise BackupError(_("“{part}”: {error}. Nothing was written.").format(
                    part=object_title(o['id'], jiant=jiant, coral=coral, choral=choral), error=rc_text(rc)))
    written: list[int] = []
    cur = None
    try:
        if clear_extension and 28 in sup and 28 not in want:
            _check(_put(link, [0, 28, *u32(0), *u32(0)])[2])
            _check(_put(link, [2, 28])[2])
        for o in todo:
            if cancelled and cancelled():
                err = BackupError(_("cancelled"))
                err.cancelled = True
                raise err
            cur = o["id"]
            if o["id"] in smp:
                _restore_sample(link, o["id"] - 32, o["bytes"], step)
            else:
                write_object(link, o["id"], o["bytes"], step, FELUCCA)
            written.append(o["id"])
            cur = None
    except Exception as e:
        raise _with_written(e, written, cur, cur in smp)
    if (coral or choral) and written:
        restart_coralflower(link)
    return written


NAMES_SHOWN = 8


def _names(ids, name) -> str:
    """"Project 1, Project 2 … and 46 more" – a Melodee 1.6 restore has 57 parts, nobody reads them in one line"""
    ids = list(ids)
    txt = ", ".join(name(i) for i in ids[:NAMES_SHOWN])
    if len(ids) > NAMES_SHOWN:
        txt += " " + _("and {n} more").format(n=len(ids) - NAMES_SHOWN)
    return txt


def written_text(e: BaseException, planned, sloop: bool = False, titles: dict | None = None, full: bool = False) -> str:
    """Feedback after an abort: what is on the device now? planned: IDs in write order. full: every name (for
    the log) instead of the first few and a count."""
    done = list(getattr(e, "written", None) or [])
    unclear = getattr(e, "unclear", None)
    titles = titles or {}

    def name(i):
        return titles.get(i) or object_title(i, sloop)

    def names(ids):
        return ", ".join(name(i) for i in ids) if full else _names(ids, name)
    rest = [i for i in planned if i not in done and i != unclear]
    if not done and unclear is None:
        return _("Nothing was changed on the FM-1.")
    parts = []
    if done:
        parts.append(_("Already written to the FM-1: {parts}.").format(parts=names(done)))
    if unclear is not None and getattr(e, "unclear_sample", False):
        parts.append(_("“{part}” was being written when it stopped – a sample slot is erased first, so it may be "
                       "empty now. Check it on the FM-1.").format(part=name(unclear)))
    elif unclear is not None:
        parts.append(_("Unclear: “{part}” was being written when the connection broke – it may be the old or the "
                       "new state. Check it on the FM-1.").format(part=name(unclear)))
    if rest:
        parts.append(_("Still in the old state: {parts}.").format(parts=names(rest)))
    return " ".join(parts)


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
