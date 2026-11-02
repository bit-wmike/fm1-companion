# SPDX-License-Identifier: GPL-3.0-only
"""SLOOP-Backups (Protokoll v6, SLOOP ≥ 2.3): Befehle 34 BK_LIST, 35 BK_GET, 36 BK_PUT.

Python-Port von backupCapture / backupObjects / backupRestore aus dem SLOOP-Web-Editor
(isod89/sloop-fm1, web/editor.html, GPL-3.0; Protokoll: web/EDITOR_PROTOCOL.md "v6: backup / restore").
Die Dateien sind byte-genau die des Editors (``JSON.stringify(obj, null, 1)``):

    {"format": "sloop-backup", "version": 1, "firmware": <INFO-Version>, "date": <ISO, ms, Z>,
     "objects": [{"id", "len", "crc", "data" (Base64)}, ...]}

Die App hängt höchstens einen Block "companion" an (Gerät, Notiz …); der Editor ignoriert ihn.

Objekte: 0 laufendes Projekt, 1 Einstellungen (PER3), 2..5 Projekte/Sections A..D, 6..7 User-Preset-
Banken, 8 FM6-Patch-Bank (v9), 9 SYN-Drum-Kits (v10), 10 MIDI LEARN (2.6, nur wenn nicht Standard),
11 eigenes Farbschema (2.7, nur wenn vorhanden), 32..35 Sample-Slots USR1..4.

Zurückspielen in der Reihenfolge des Editors: Banken, Projekte, laufendes Projekt, Einstellungen,
danach die Samples (über SMP_BEGIN/WRITE/END bzw. SMP_ERASE). Jedes Objekt wird im RAM des FM-1
gesammelt und erst beim Commit geprüft und geschrieben. Läuft der Song, antwortet der Commit mit
rc 3 – weil die Banken (die auch rc 3 kennen) zuerst kommen, wird dann noch nichts ersetzt.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from typing import Callable

from .protocol import Cmd, ProtocolError, Reader, crc32, pack7, parse_info, r32, u32, unpack7

BK_LIST, BK_GET, BK_PUT = 34, 35, 36
FORMAT = "sloop-backup"
VERSION = 1
CHUNK = 256
SAMPLES = [32, 33, 34, 35]
RESTORE = [6, 7, 8, 9, 10, 11, 2, 3, 4, 5, 0, 1]
OPTIONAL = (8, 9, 10, 11)                   # ältere Firmware kennt sie nicht: übersprungen
SMP_DATA_OFF, SMP_HDR_LEN = 512, 32 + 16 * 28
MAX_LEN = 0x14000                          # größtes Objekt: ein Sample-Slot (80 KiB)
MIN_PROTO = 6

TITLES = {0: "laufendes Projekt", 1: "Einstellungen", 2: "Section A", 3: "Section B", 4: "Section C",
          5: "Section D", 6: "Preset-Bank 1–16", 7: "Preset-Bank 17–32", 8: "FM6-Patch-Bank",
          9: "SYN-Drum-Kits", 10: "MIDI-Learn", 11: "Farbschema", 32: "Sample USR1", 33: "Sample USR2",
          34: "Sample USR3", 35: "Sample USR4"}

RC_TEXT = {1: "ungültige Anfrage", 2: "Objekt ungültig (Prüfsumme, Kennung oder Größe)",
           3: "Song läuft – bitte erst STOP drücken", 4: "Flash-Fehler",
           5: "Backup veraltet oder abgelaufen – bitte noch einmal starten"}

STOP_HINT = ("Bitte am FM-1 erst den Song stoppen (STOP) und dann noch einmal zurückspielen. "
             "SLOOP schreibt Projekte, Preset-Banken und Einstellungen nur, solange nichts läuft.")


def _err(code: str, text: str, written=None):
    from .backup import BackupError
    e = BackupError(text)
    e.code = code
    e.written = list(written or [])
    return e


def title(oid: int) -> str:
    return TITLES.get(oid, f"Objekt {oid}")


# ------------------------------------------------------------ Anfragen ---
# (Befehl, Argumente) – byte-genau wie req.bkList / bkGet / bkBegin / bkData / bkCommit / bkAbort

def req_list():
    return BK_LIST, []


def req_get(oid: int, off: int, n: int):
    return BK_GET, [oid & 0x7F, *u32(off), n & 0x7F, (n >> 7) & 0x7F]


def req_begin(oid: int, length: int, crc: int):
    return BK_PUT, [0, oid & 0x7F, *u32(length), *u32(crc)]


def req_data(oid: int, off: int, data: bytes):
    return BK_PUT, [1, oid & 0x7F, *u32(off), *pack7(data)]


def req_commit(oid: int):
    return BK_PUT, [2, oid & 0x7F]


def req_abort(oid: int):
    return BK_PUT, [3, oid & 0x7F]


# ------------------------------------------------------------ Antworten ---

def parse_list(a) -> tuple[int, list[tuple[int, int, int]]]:
    """-> (rc, [(id, len, crc)])"""
    r = Reader(a)
    rc, n = r.b(), r.b()
    items = []
    for _ in range(n):
        oid = r.b()
        if r.i + 10 > len(r.a):
            raise ProtocolError("Inventar zu kurz")
        ln, crc = r32(r.a, r.i), r32(r.a, r.i + 5)
        r.i += 10
        items.append((oid, ln, crc))
    return rc, items


def parse_get(a) -> dict:
    if len(a) < 9:
        raise ProtocolError("Antwort zu kurz")
    oid, rc, off, count = a[0], a[1], r32(a, 2), a[7] | a[8] << 7
    return {"id": oid, "rc": rc, "off": off, "count": count, "raw": list(a[9:])}


def parse_put(a) -> dict:
    if len(a) < 3:
        raise ProtocolError("Antwort zu kurz")
    return {"op": a[0], "id": a[1], "rc": a[2]}


# ------------------------------------------------------------- Base64 ---
_B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"


def b64enc(b: bytes) -> str:
    import base64
    return base64.b64encode(bytes(b)).decode("ascii")


def b64dec(s) -> bytes:
    """wie b64dec im Editor: alles außer A–Z a–z 0–9 + / wird übergangen."""
    s = re.sub(r"[^A-Za-z0-9+/]", "", str(s))
    out = bytearray()
    acc = bits = 0
    for c in s:
        acc = ((acc << 6) | _B64.index(c)) & 0xFFFFFF
        bits += 6
        if bits >= 8:
            bits -= 8
            out.append((acc >> bits) & 255)
    return bytes(out[: (len(s) * 3) >> 2])


# ------------------------------------------------------------- Datei ---

def dumps(file: dict) -> str:
    """genau wie JSON.stringify(obj, null, 1) im Editor (download())."""
    return json.dumps(file, indent=1, ensure_ascii=False)


def now_iso() -> str:
    """new Date().toISOString()"""
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def objects(file) -> dict[int, bytes]:
    """backupObjects(): Datei -> {id: bytes}; jedes Objekt gegen Länge und CRC geprüft."""
    if isinstance(file, (str, bytes)):
        try:
            file = json.loads(file)
        except ValueError:
            raise _err("bkBad", "Keine Backup-Datei (kein JSON)")
    if not isinstance(file, dict) or file.get("format") != FORMAT or not _num(file.get("version")) \
            or not file.get("version") >= 1 or not isinstance(file.get("objects"), list):
        raise _err("bkBad", "Kein SLOOP-Backup")
    m: dict[int, bytes] = {}
    for o in file["objects"]:
        if not isinstance(o, dict):
            raise _err("bkBad", "SLOOP-Backup: ungültiger Eintrag")
        oid = o.get("id")
        d = b64dec(o.get("data") or "")
        crc = o.get("crc")
        if not _num(oid) or oid not in RESTORE + SAMPLES or not _num(o.get("len")) or len(d) != o["len"] \
                or not _num(crc) or crc32(d) != (int(crc) & 0xFFFFFFFF):
            raise _err("bkBad", f"SLOOP-Backup: Objekt {oid} beschädigt")
        m[int(oid)] = d
    if 0 not in m or 1 not in m:
        raise _err("bkBad", "SLOOP-Backup unvollständig (laufendes Projekt oder Einstellungen fehlen)")
    return m


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def proj_format(d: bytes) -> int:
    """bkProjFormat: "FUN6" … (wie gespeichert, niedriges Byte zuerst) -> 6 …; 0 = kein Projekt"""
    return d[0] - 0x30 if d and len(d) >= 4 and d[1] == 0x4E and d[2] == 0x55 and d[3] == 0x46 else 0


def dev_format(p_count: int) -> int:
    """bkDevFormat: neuestes Projektformat, das das Gerät liest (aus INFO)."""
    return 6 if p_count >= 71 else 5 if p_count >= 61 else 4


def fw_at_least(v: str, want) -> bool:
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", str(v or ""))
    if not m:
        return False
    a = [int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)]
    for i in range(3):
        if a[i] != want[i]:
            return a[i] > want[i]
    return True


def info_proto(raw) -> int:
    """das Protokoll-Byte am Ende von INFO (v5+), 0 wenn keins da ist."""
    try:
        r = Reader(raw)
        r.s()
        ne = r.b()
        for _ in range(4):
            r.b()
        for _ in range(ne):
            r.s()
        r.b()                     # NTRK
        return r.b() if r.more() else 0
    except ProtocolError:
        return 0


# ----------------------------------------------------------- Gerät ---

def _rq(link, req, timeout, retries):
    cmd, args = req
    return link.request(cmd, args, timeout=timeout, retries=retries)


def read_info(link):
    return parse_info(link.request(Cmd.INFO, [], timeout=1.5, retries=2))


def list_objects(link) -> list:
    from .backup import ObjInfo
    rc, items = parse_list(_rq(link, req_list(), 5.0, 0))
    if rc:
        raise _err(f"bkRc{rc}", "Das FM-1 hat keinen nutzbaren Flash-Speicher – Backup nicht möglich."
                   if rc == 4 else f"Inventar abgelehnt ({RC_TEXT.get(rc, rc)})")
    out = []
    for oid, ln, crc in items:
        if ln > MAX_LEN:
            raise _err("bkRead", f"Objekt {oid} ist unplausibel groß ({ln} Byte)")
        out.append(ObjInfo(oid, ln, crc, "sloop"))
    return out


def read_object(link, o, progress: Callable[[int], None] | None = None) -> bytes:
    buf = bytearray(o.size)
    for off in range(0, o.size, CHUNK):
        n = min(CHUNK, o.size - off)
        g = parse_get(_rq(link, req_get(o.id, off, n), 1.5, 2))
        if g["rc"]:
            raise _err("bkStale" if g["rc"] == 5 else "bkRead",
                       "Das FM-1 hat sich während des Backups verändert – bitte noch einmal versuchen."
                       if g["rc"] == 5 else f"Lesen von {title(o.id)} abgelehnt ({RC_TEXT.get(g['rc'], g['rc'])})")
        if g["id"] != o.id or g["off"] != off or g["count"] != n:
            raise _err("bkRead", "unerwartete Antwort beim Lesen")
        try:
            data = unpack7(g["raw"], n)
        except ProtocolError:
            raise _err("bkRead", f"{title(o.id)}: {len(g['raw'])} Byte statt {n}")
        buf[off:off + n] = data
        if progress:
            progress(n)
    data = bytes(buf)
    if crc32(data) != o.crc:
        raise _err("bkStale", "Das FM-1 hat sich während des Backups verändert (Prüfsumme) – bitte noch einmal "
                              "versuchen, am besten mit gestopptem Song.")
    return data


def capture(link, firmware: str = "", progress: Callable[[int, int], None] | None = None,
            companion: dict | None = None, info=None) -> dict:
    """backupCapture(): alles lesen. firmware: Versionszeichenkette (Standard: INFO des Geräts)."""
    if info is None:
        info = read_info(link)
    man = list_objects(link)
    total = sum(o.size for o in man)
    done = 0

    def step(n):
        nonlocal done
        done += n
        if progress:
            progress(done, total)

    objs = []
    for o in man:
        data = read_object(link, o, step)
        objs.append({"id": o.id, "len": o.size, "crc": o.crc, "data": b64enc(data)})
    f = {"format": FORMAT, "version": VERSION, "firmware": info.version if info else firmware,
         "date": now_iso(), "objects": objs}
    if companion:
        f["companion"] = companion
    return f


def write_object(link, oid: int, data: bytes, progress: Callable[[int], None] | None = None,
                 written=None) -> int:
    """begin, data, commit eines Objekts. -> 0, oder rc des begin (1/5) bei einem optionalen
    Objekt, das die Firmware nicht kennt (dann wurde nichts geschrieben)."""
    r = parse_put(_rq(link, req_begin(oid, len(data), crc32(data)), 1.5, 1))
    if r["rc"] in (1, 5) and oid in OPTIONAL:
        return r["rc"]
    if r["rc"]:
        raise _err("bkWrite", f"{title(oid)}: Beginn abgelehnt ({RC_TEXT.get(r['rc'], r['rc'])})", written)
    try:
        for off in range(0, len(data), CHUNK):
            chunk = data[off:off + CHUNK]
            r = parse_put(_rq(link, req_data(oid, off, chunk), 1.5, 1))
            if r["rc"]:
                raise _err("bkWrite", f"{title(oid)}: Daten bei Byte {off} abgelehnt "
                                      f"({RC_TEXT.get(r['rc'], r['rc'])})", written)
            if progress:
                progress(len(chunk))
        r = parse_put(_rq(link, req_commit(oid), 5.0, 0))
    except Exception:
        try:
            _rq(link, req_abort(oid), 1.0, 0)
        except Exception:                           # noqa: BLE001
            pass
        raise
    if r["rc"] == 3:
        if written:
            raise _err("bkStop", "Der Song lief beim Zurückspielen. Schon geschrieben: "
                       + ", ".join(title(i) for i in written) + f". Nicht geschrieben: {title(oid)} und alles danach. "
                       + STOP_HINT, written)
        raise _err("bkStop", STOP_HINT + " Es wurde nichts geschrieben.", written)
    if r["rc"]:
        raise _err("bkWrite", f"{title(oid)}: vom FM-1 abgelehnt ({RC_TEXT.get(r['rc'], r['rc'])})", written)
    return 0


def _smp(link, cmd, args, slot, timeout):
    a = link.request(cmd, args, timeout=timeout, retries=0 if cmd != Cmd.SMP_WRITE else 1)
    if not a or a[0] != slot:
        raise _err("bkWrite", "unerwartete Sample-Antwort")
    return a[-1]


def write_sample(link, oid: int, d: bytes, progress: Callable[[int], None] | None = None, written=None):
    k = oid - 32
    if not d:
        rc = _smp(link, Cmd.SMP_ERASE, [k], k, 2.5)
        if rc:
            raise _err("bkWrite", f"USR{k + 1} leeren: Fehler {rc}", written)
        return
    if len(d) < SMP_DATA_OFF:
        raise _err("bkBad", f"USR{k + 1}: Sample-Objekt zu kurz", written)
    rc = _smp(link, Cmd.SMP_BEGIN, [k], k, 1.0)
    if rc:
        raise _err("bkWrite", f"USR{k + 1}: Beginn abgelehnt (Fehler {rc})", written)
    data = d[SMP_DATA_OFF:]
    for off in range(0, len(data), CHUNK):
        o = SMP_DATA_OFF + off
        chunk = data[off:off + CHUNK]
        rc = _smp(link, Cmd.SMP_WRITE, [k, o & 0x7F, (o >> 7) & 0x7F, (o >> 14) & 0x7F, *pack7(chunk)], k, 1.0)
        if rc:
            raise _err("bkWrite", f"USR{k + 1}: Schreiben abgelehnt (Fehler {rc})", written)
        if progress:
            progress(len(chunk))
    rc = _smp(link, Cmd.SMP_END, [k, *pack7(d[:SMP_HDR_LEN])], k, 2.0)
    if rc:
        raise _err("bkWrite", f"USR{k + 1}: Abschluss abgelehnt (Fehler {rc})", written)


def check_restore(m: dict[int, bytes], file: dict, info) -> dict[int, bytes]:
    """Prüfungen vor dem ersten Schreiben (wie backupRestore). -> ggf. ergänztes {id: bytes}"""
    if not m.get(0) or not m.get(1):
        raise _err("bkBad", "Im Backup fehlen das laufende Projekt oder die Einstellungen – es wurde nichts "
                            "geschrieben.")
    newest = max([0] + [proj_format(m[i]) for i in (0, 2, 3, 4, 5) if i in m])
    if info is not None and newest > dev_format(info.p_count):
        raise _err("bkNewer", f"Das Backup stammt von einer neueren SLOOP-Version ({file.get('firmware') or '?'}) "
                              f"als auf dem FM-1 ({info.version}). Erst SLOOP aktualisieren – es wurde nichts "
                              "geschrieben.")
    m = dict(m)
    if 10 not in m and fw_at_least(file.get("firmware", ""), (2, 5, 1)):
        d = bytearray(40)                          # MIDI LEARN in der Grundstellung (nichts gelernt, 1 2 3, 10, 2)
        d[32:37] = bytes([1, 2, 3, 10, 2])
        m[10] = bytes(d)
    return m


def restore(link, file: dict, progress: Callable[[int, int], None] | None = None, ids=None,
            cancelled: Callable[[], bool] | None = None, info=None) -> list[int]:
    """backupRestore(): alles (ids=None) oder nur die gewählten Objekte zurückspielen.
    -> geschriebene IDs in Reihenfolge."""
    m = objects(file)
    if info is None:
        info = read_info(link)
    m = check_restore(m, file, info) if ids is None else _check_part(m, file, info, ids)
    want = set(m) if ids is None else set(ids)
    todo = [i for i in RESTORE + SAMPLES if i in m and i in want]
    total = sum(len(m[i]) for i in todo) or 1
    done = 0

    def step(n):
        nonlocal done
        done += n
        if progress:
            progress(done, total)

    written: list[int] = []
    try:
        for oid in todo:
            if cancelled and cancelled():
                raise _err("cancel", "abgebrochen", written)
            d = m[oid]
            if oid in SAMPLES:
                write_sample(link, oid, d, step, written)
            elif write_object(link, oid, d, step, written):
                continue                            # (Firmware kennt das Objekt nicht)
            written.append(oid)
    except Exception as e:
        # auch Zeitüberschreitungen/Verbindungsfehler (ProtocolError, LinkError …) sollen melden,
        # was schon geschrieben wurde – sonst hieße es fälschlich „nichts verändert“
        from .backup import _with_written
        raise _with_written(e, written)
    return written


def _check_part(m, file, info, ids) -> dict[int, bytes]:
    missing = [i for i in ids if i not in m]
    if missing:
        raise _err("bkBad", f"{title(missing[0])} ist nicht im Backup")
    if 0 in ids and not m[0] or 1 in ids and not m[1]:
        raise _err("bkBad", "Laufendes Projekt/Einstellungen im Backup sind leer – es wurde nichts geschrieben.")
    newest = max([0] + [proj_format(m[i]) for i in ids if i in (0, 2, 3, 4, 5)])
    if info is not None and newest > dev_format(info.p_count):
        raise _err("bkNewer", f"Projekte aus einer neueren SLOOP-Version ({file.get('firmware') or '?'}) liest "
                              f"das FM-1 ({info.version}) nicht. Erst SLOOP aktualisieren – es wurde nichts "
                              "geschrieben.")
    return m
