# SPDX-License-Identifier: GPL-3.0-only
"""Projekte einzeln verwalten (Melodee 1.6: 32 Projektplätze; SLOOP: 4 Sections A–D).

Die Firmware bietet keinen eigenen "Projekt exportieren"-Befehl, aber die
Backup-Objekte sind pro Projektplatz einzeln les- und schreibbar (IDs 2–5 und
64–91; SLOOP: 2–5 = Sections A–D über die Befehle 34–36). Daraus baut die App eine Projekt-Bibliothek auf dem Rechner:

  <Name>.fm1proj   JSON: Firmware, Datum, Notiz, Herkunft und das Objekt selbst

Ein Projekt lässt sich so in jeden freien (oder belegten) Platz zurückspielen –
auch auf einem anderen FM-1 mit derselben Firmware.
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import backup
from .i18n import N_, _
from .protocol import crc32

FORMAT = "fm1companion-project"
SUFFIX = ".fm1proj"
# grobe Formatkennung über die ersten 4 Bytes (Magic)
# format labels (translated by format_of(); also written as "kind" into new project files)
MAGICS = {b"HPJ1": N_("compact (1.6)"), b"FBKQ": N_("FBKQ (1.6 rc.6+, with pattern sounds)"), b"FBKP": "FBKP (1.6)",
          b"FBKN": "FBKN", b"FBKM": "FBKM", b"FBKL": "FBKL", b"FBKK": "FBKK", b"FBK9": "FBK9",
          # SLOOP (firmware/src/project.c): Projekte und Sections A..D (Backup-Objekte 0 und 2..5)
          b"FUN6": "FUN6 (SLOOP 2.6+)", b"FUN5": "FUN5 (SLOOP 2.4–2.5)", b"FUN4": "FUN4 (SLOOP 2.0–2.3)",
          b"FUN3": "FUN3 (SLOOP 1.x)", b"FUN2": "FUN2 (SLOOP)", b"FUN1": "FUN1 (SLOOP)"}


def format_of(data: bytes) -> str:
    head = data[:4]
    for k, v in MAGICS.items():
        if head == k or head == k[::-1]:
            return _(v)
    return _("unknown ({magic})").format(magic=head.hex()) if head else _("empty")


@dataclass
class SlotInfo:
    slot: int          # 1..32
    oid: int
    size: int
    crc: int

    @property
    def used(self) -> bool:
        return self.size > 0


def list_slots(link) -> tuple[list[SlotInfo], int]:
    """-> (Projektplätze, Größe der laufenden Musik). Leere Plätze haben size 0."""
    man = backup.list_objects(link)
    slots = []
    live = 0
    for o in man:
        if o.id == 0:
            live = o.size
            continue
        s = backup.project_slot(o.id)
        if s:
            slots.append(SlotInfo(s, o.id, o.size, o.crc))
    slots.sort(key=lambda x: x.slot)
    return slots, live


def _safe(name: str) -> str:
    """Dateiname aus einem Projektnamen: Sonderzeichen werden „_“, höchstens 80 Zeichen, nie leer/„.“."""
    s = re.sub(r"[^\w\-. äöüÄÖÜß]+", "_", name).strip(" .")[:80].strip(" .")  # i18n: ignore
    return s or "Projekt"


def _write_json(path: Path, meta: dict):
    backup.write_text_atomic(path, json.dumps(meta, indent=1, ensure_ascii=False))


@dataclass
class ProjectFile:
    path: Path
    meta: dict

    @property
    def name(self) -> str:
        return self.meta.get("name") or self.path.stem

    @property
    def data(self) -> bytes:
        o = self.meta["object"]
        raw = base64.b64decode(o["data"])
        if len(raw) != o["size"] or crc32(raw) != o["crc"]:
            raise backup.BackupError(_("{name}: checksum does not match").format(name=self.path.name))
        return raw


def export_slot(link, slot: int, folder: Path, name: str, firmware: str, device: str = "",
                notes: str = "", progress: Callable[[int], None] | None = None, manifest=None) -> Path:
    """manifest: schon gelesenes Inventar (backup.list_objects) – spart bei „Alle holen“ einen Stopp je Platz."""
    oid = backup.project_id(slot)
    man = {o.id: o for o in (manifest if manifest is not None else backup.list_objects(link))}
    o = man.get(oid)
    if not o or not o.size:
        raise backup.BackupError(_("Project slot {slot} is empty").format(slot=slot))
    data = backup.read_object(link, o, progress)
    return save_project(folder, name, data, firmware, device, notes, source=_("slot {n}").format(n=slot))


def save_project(folder: Path, name: str, data: bytes, firmware: str, device: str = "", notes: str = "",
                 source: str = "") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    meta = {"format": FORMAT, "version": 1, "name": name, "firmware": firmware, "device": device,
            "notes": notes, "source": source, "kind": format_of(data),
            "created": dt.datetime.now().isoformat(timespec="seconds"),
            "object": {"size": len(data), "crc": crc32(data), "data": base64.b64encode(data).decode()}}
    path = folder / f"{_safe(name)}{SUFFIX}"
    n = 2
    while path.exists():
        path = folder / f"{_safe(name)} ({n}){SUFFIX}"
        n += 1
    _write_json(path, meta)
    return path


def load_project(path: Path) -> ProjectFile:
    meta = json.loads(Path(path).read_text("utf-8"))
    if meta.get("format") != FORMAT or "object" not in meta:
        raise backup.BackupError(_("{name} is not an FM-1 Companion project file").format(name=Path(path).name))
    pf = ProjectFile(Path(path), meta)
    pf.data  # prüfen
    return pf


def library(folder: Path) -> list[ProjectFile]:
    out = []
    for p in sorted(folder.rglob(f"*{SUFFIX}")):
        try:
            out.append(ProjectFile(p, json.loads(p.read_text("utf-8"))))
        except (OSError, ValueError):
            continue
    return out


def update_meta(pf: ProjectFile, **changes):
    pf.meta.update(changes)
    _write_json(pf.path, pf.meta)


def family(firmware: str) -> str:
    """Firmware-Familie aus einer Versionszeichenkette oder einem Schlüssel: melodee / sloop / skiff / ""."""
    v = (firmware or "").lower()
    for k in ("melodee", "sloop", "skiff"):
        if k in v:
            return k
    return ""


def family_of_data(data: bytes) -> str:
    """Familie, soweit sie aus dem Projekt selbst hervorgeht (SLOOP: FUNx, Melodee: HPJ1/FBKx/FUNxx)."""
    k = format_of(data)
    if "SLOOP" in k:
        return "sloop"
    if data[:4] in MAGICS or data[:4][::-1] in MAGICS:
        return "melodee"
    return ""


def import_to_slot(link, pf: ProjectFile, slot: int, progress: Callable[[int], None] | None = None,
                   device_firmware: str = ""):
    data = pf.data
    oid = backup.project_id(slot)
    proto = backup.protocol(link)[0]
    if proto == backup.SLOOP and not 1 <= slot <= 4:
        raise backup.BackupError(_("SLOOP only has project slots 1–4 (sections A–D) – nothing was written."))
    src = family(pf.meta.get("firmware", "")) or family_of_data(data)
    dst = family(device_firmware) or ("sloop" if proto == backup.SLOOP else "")
    if src and dst and src != dst:
        raise backup.BackupError(_("The project comes from {source}, the FM-1 runs {target} – the project formats "
                                   "do not match. Nothing was written.").format(
            source=src.capitalize() if src != 'sloop' else 'SLOOP', target=dst.capitalize() if dst != 'sloop' else 'SLOOP'))
    rc = backup.preflight(link, oid, data, proto)
    if rc == 1:
        raise backup.BackupError(_("The device does not accept this project (different format or different "
                                   "firmware) – nothing was written."))
    if rc == 3:
        raise backup.BackupError(_("Please stop playback on the FM-1 first – nothing was written."))
    if rc:
        raise backup.BackupError(_("The device rejects the project ({reason}) – nothing was written.").format(
            reason=backup.RC_TEXT.get(rc, f'Code {rc}')))
    backup.write_object(link, oid, data, progress, proto)


def erase_slot(link, slot: int):
    """einen Platz leeren: ein leeres Objekt schreiben (wie das Löschen in Melodee)."""
    backup.write_object(link, backup.project_id(slot), b"")


def projects_from_backup(arch: backup.Archive) -> list[tuple[int, bytes]]:
    return [(backup.project_slot(o["id"]), o["bytes"]) for o in arch.project_objects()]
