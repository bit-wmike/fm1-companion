# SPDX-License-Identifier: GPL-3.0-only
"""Komplette Geräte-Backups – Python-Port von web/fm1backup.js (Melodee).

Das Dateiformat ist byte-genau das des Web-Editors ("felucca-backup", Version 1):
Backups der App lassen sich im Editor zurückspielen und umgekehrt. Zusätzlich
schreibt die App einen Block "companion" mit Gerät, Firmware und Notiz hinein;
der Editor ignoriert unbekannte Felder.

Ablauf (Befehle 65–67):
  LIST  – Inventar: pro Objekt id, Größe, CRC
  GET   – Objekt stückweise lesen (256 Byte)
  PUT   – begin / data / commit / abort pro Objekt

Wiederherstellen prüft erst jedes Byte der Datei, dann jede Objektgröße beim
Gerät (begin + abort, nichts wird geschrieben), und spielt die laufende Musik
(Objekt 0) zuletzt ein – genau wie der Editor.

SLOOP (ab 2.3) hat ein eigenes Backup-Protokoll (Befehle 34–36) und ein eigenes
Dateiformat ("sloop-backup"): siehe sloopbk.py. Alle Funktionen hier wählen das
Protokoll selbst (Firmware-Schlüssel als Hinweis, sonst über INFO) – die Oberfläche
muss davon nichts wissen.
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

# Inventare, die der Editor kennt (Anzahl Objekte -> IDs). Andere Firmwares der
# Familie können eigene Listen haben; die App sichert sie generisch.
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


def is_project(oid: int) -> bool:
    return oid == 0 or 2 <= oid <= 5 or 64 <= oid < 92


def is_sample(oid: int) -> bool:
    return 32 <= oid < 64


def max_size(oid: int) -> int:
    return 27936 if is_project(oid) else 81920 if oid >= 32 else 3840


def project_slot(oid: int) -> int | None:
    """Objekt-ID -> Projektplatz 1..32 (0 = laufende Musik)."""
    if oid == 0:
        return 0
    if 2 <= oid <= 5:
        return oid - 1
    if 64 <= oid < 92:
        return oid - 59
    return None


def project_id(slot: int) -> int:
    """Projektplatz 1..32 -> Objekt-ID."""
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


def object_title(oid: int, sloop: bool = False) -> str:
    """Name eines Backup-Objekts in Alltagssprache („Preset-Bank U073–U090“ statt „Objekt 17“)."""
    try:
        from . import backup_inspect as BI
        return BI.describe(oid, b"", 0, 57, "", sloop=sloop).title
    except Exception:                               # noqa: BLE001 – Name ist nur Zugabe
        return _("object {id}").format(id=oid)


def _with_written(e: BaseException, written: list) -> BaseException:
    """an einen Fehler anhängen, was vor dem Abbruch schon geschrieben wurde (für die Rückmeldung)."""
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
    proto: str = "felucca"          # "felucca" (65–67) oder "sloop" (34–36)


FELUCCA, SLOOP = "felucca", "sloop"
SLOOP_FORMAT = "sloop-backup"


def protocol(link, hint: str = "") -> tuple[str, object]:
    """welches Backup-Protokoll spricht das Gerät? -> (FELUCCA|SLOOP, Info oder None).
    hint: Firmware-Schlüssel (ctx.firmware_key), falls bekannt."""
    from . import sloopbk
    if hint in ("melodee", "skiff", "felucca"):
        return FELUCCA, None
    try:
        info = sloopbk.read_info(link)
    except Exception:                               # noqa: BLE001 – dann wie bisher
        return (SLOOP if hint == SLOOP else FELUCCA), None
    if info.firmware_name == SLOOP or hint == SLOOP:
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
    known = KNOWN_ID_SETS.get(n)
    if known and [o.id for o in out] != known:
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
    """komplettes Backup lesen – im Format, das zur Firmware gehört (Melodee/Felucca oder SLOOP)."""
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
    read(f)        # dieselbe Prüfung wie beim Laden
    return f


@dataclass
class Archive:
    file: dict
    objects: list[dict]          # mit "bytes"
    known: bool                  # Inventar, das der Editor kennt

    @property
    def format(self) -> str:
        return self.file.get("format", FORMAT)

    @property
    def sloop(self) -> bool:
        return self.format == SLOOP_FORMAT

    @property
    def firmware(self) -> str:
        return self.file.get("firmware", "")

    @property
    def created(self) -> str:
        return self.file.get("created") or self.file.get("date", "")

    @property
    def companion(self) -> dict:
        return self.file.get("companion") or {}

    def project_objects(self):
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
    if not isinstance(file, dict) or file.get("format") != FORMAT or file.get("version") != 1 \
            or not isinstance(file.get("objects"), list) or not file["objects"]:
        raise BackupError(_("Not a complete Felucca/Melodee backup"))
    known_ids = KNOWN_ID_SETS.get(len(file["objects"]))
    objs = []
    for i, o in enumerate(file["objects"]):
        try:
            oid, size, crc, data = o["id"], o["size"], o["crc"], o["data"]
        except (KeyError, TypeError):
            raise BackupError(_("invalid object in the backup"))
        if known_ids and oid != known_ids[i]:
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
    if not objs[0]["size"] or (len(objs) > 1 and not objs[1]["size"]):
        raise BackupError(_("The backup does not contain the running music or the settings"))
    return Archive(file=file, objects=objs, known=bool(known_ids))


def load_file(path: Path) -> Archive:
    return read(Path(path).read_text("utf-8"))


def dumps(file: dict) -> str:
    """Dateitext wie der jeweilige Web-Editor ihn schreibt (SLOOP: JSON.stringify(f, null, 1))."""
    if file.get("format") == SLOOP_FORMAT:
        from . import sloopbk
        return sloopbk.dumps(file)
    return json.dumps(file, separators=(",", ":"))


def write_text_atomic(path: Path, text: str, keep_times: tuple[int, int] | None = None):
    """Datei nur ganz oder gar nicht schreiben: erst in eine versteckte .tmp-Datei im selben Ordner
    (mit fsync), dann umbenennen. Bricht das Schreiben ab (Platte voll, Prozess beendet), bleibt die
    alte Datei unverändert und die .tmp-Datei wird – soweit möglich – wieder entfernt.
    keep_times: (atime_ns, mtime_ns) der neuen Datei (Notiz ändern, ohne das Datum zu ändern)."""
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
    """Reste abgebrochener Schreibvorgänge (.<name>.*.tmp) aufräumen – nur ältere, die niemand mehr schreibt."""
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
    """ein einzelnes Objekt schreiben (begin, data, commit; bei Fehler abort)."""
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
    """fragt das Gerät, ob es dieses Objekt annehmen würde – ohne zu schreiben.
    (SLOOP prüft beim Beginn nur die Größe; den Inhalt erst beim Commit.)"""
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
    return proto, info


def check_16(archive: Archive, supported, device_version: str = ""):
    """Backup von Melodee 1.6 auf eine Firmware ohne 1.6-Inventar? -> BackupError mit Erklärung (vor jedem
    Schreiben; sonst meldete das Gerät nur „älteres Format“ oder „kennt … nicht“)."""
    from . import melodee16 as M
    if archive.sloop:
        return
    if M.backup_is_16(archive.firmware, archive.companion, len(archive.objects), [o["id"] for o in archive.objects
                                                                                     if o["size"]]) \
            and M.device_before_16(device_version, len(supported)):
        c = archive.companion
        raise BackupError(M.restore_refusal(c.get("firmware_version") or archive.firmware, device_version))


def restore(link, archive: Archive, progress: Callable[[int, int], None] | None = None,
            firmware_key: str = ""):
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
    live = next((o.size for o in supported if o.id == 0), 0)
    check_16(archive, supported)
    if len(supported) != 57:
        for o in archive.objects:
            if is_project(o["id"]) and o["size"] > live and not (live >= 19008 and o["size"] in (19084, 19092, 20224)):
                raise BackupError(_("This backup needs a firmware with a larger recording memory"))
    for o in archive.objects:
        if o["id"] not in sup_ids and o["size"]:
            raise BackupError(_("The firmware on the FM-1 does not know “{part}” from the backup "
                                "(different version). Nothing was written.").format(part=object_title(o['id'])))
    # Vorabprüfung jedes Objekts, bevor irgendetwas ersetzt wird
    for o in archive.objects:
        if not is_sample(o["id"]) and o["size"] and o["id"] in sup_ids:
            rc = preflight(link, o["id"], o["bytes"], FELUCCA)
            if rc == 1:
                raise BackupError(_("The backup has an older format. Restore it with Melodee 1.2 first. "
                                    "Nothing was written."))
            if rc == 3:
                raise BackupError(_("Please stop playback on the FM-1 first – nothing was written."))
            if rc:
                raise BackupError(_("“{part}”: {error}. Nothing was written.").format(part=object_title(o['id']),
                                                                                   error=rc_text(rc)))
    written: list[int] = []
    try:
        if len(archive.objects) == 28 and 28 in sup_ids:
            _check(_put(link, [0, 28, *u32(0), *u32(0)])[2])
            _check(_put(link, [2, 28])[2])
        order = archive.objects[2:] + archive.objects[1:2] + archive.objects[0:1]   # laufende Musik zuletzt
        for o in order:
            oid, data = o["id"], o["bytes"]
            if oid not in sup_ids:
                continue
            if is_sample(oid):
                _restore_sample(link, oid - 32, data, step)
            else:
                write_object(link, oid, data, step, FELUCCA)
            written.append(oid)
    except Exception as e:
        raise _with_written(e, written)
    return archive


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


# ------------------------------------------------- Teil-Zurückspielen ---

def restore_selected(link, archive: Archive, ids, progress: Callable[[int, int], None] | None = None,
                     expect: dict | None = None, clear_extension: bool = False,
                     cancelled: Callable[[], bool] | None = None) -> list[int]:
    """nur ausgewählte Objekte zurückspielen – dieselben Regeln wie restore():
    erst jede Datei-Prüfung (read), dann Vorabprüfung jedes Objekts beim Gerät (begin+abort),
    erst danach schreiben; Projekte/Banken/Samples, dann Einstellungen, die laufende Musik zuletzt.
    expect: id -> (size, crc) wie beim Planen gesehen; hat sich das Gerät seitdem verändert, wird abgebrochen.
    Gibt die geschriebenen IDs in Reihenfolge zurück."""
    want = set(ids)
    by_id = {o["id"]: o for o in archive.objects}
    missing = want - set(by_id)
    if missing:
        raise BackupError(_("“{part}” is not in the backup").format(part=object_title(sorted(missing)[0], archive.sloop)))
    proto, info = _match(link, archive)
    if proto == SLOOP:
        return _restore_selected_sloop(link, archive, want, progress, expect, cancelled, info)
    supported = parse_manifest(link.request(Cmd.BACKUP_LIST, [], timeout=3.0, retries=0))
    check_16(archive, supported)
    sup = {o.id: o for o in supported}
    for oid in want:
        if oid not in sup and by_id[oid]["size"]:
            raise BackupError(_("The firmware on the FM-1 does not know “{part}” from the backup "
                                "(different version). Nothing was written.").format(part=object_title(oid)))
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
        if not is_sample(o["id"]) and o["size"]:
            rc = preflight(link, o["id"], o["bytes"], FELUCCA)
            if rc == 1:
                raise BackupError(_("“{part}”: The device does not accept this format "
                                    "(older or different firmware). Nothing was written.").format(
                    part=object_title(o['id'])))
            if rc == 3:
                raise BackupError(_("Please stop playback on the FM-1 first – nothing was written."))
            if rc:
                raise BackupError(_("“{part}”: {error}. Nothing was written.").format(part=object_title(o['id']),
                                                                                   error=rc_text(rc)))
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
            if is_sample(o["id"]):
                _restore_sample(link, o["id"] - 32, o["bytes"], step)
            else:
                write_object(link, o["id"], o["bytes"], step, FELUCCA)
            written.append(o["id"])
    except Exception as e:
        raise _with_written(e, written)
    return written


def written_text(e: BaseException, planned, sloop: bool = False, titles: dict | None = None) -> str:
    """Rückmeldung nach einem Abbruch: was steht jetzt auf dem Gerät? planned: IDs in Schreibreihenfolge."""
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
    """aktuellen Gerätestand nur im Speicher lesen (für Vergleiche) – ohne Datei."""
    return read(capture(link, firmware, progress, companion))
