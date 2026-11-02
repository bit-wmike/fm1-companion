# SPDX-License-Identifier: GPL-3.0-only
"""Backups für Menschen erklären, vergleichen und gezielt zurückspielen (planen).

Grundlage ist das Inventar der Felucca-/Melodee-Backups (web/EDITOR_PROTOCOL.md,
"Historical v7: full backup" und "Shared user library"):

* 0 laufende Musik, 1 Einstellungen (PER5), 2–5 und 64–91 Projektplätze 1–32
* Melodee 1.6 (29/57 Objekte): 22 gemeinsame Preset-Banken im Format UPB2
  (3716 Byte: 8 Byte Kopf, 18 Datensätze × 206 Byte). Bank → Objekt:
  0–3 → 6, 7, 17, 18 (allgemein), 4–7 → 21, 22, 19, 20 (früher FM6-Voices),
  8–15 → 9–16 (früher CZ-1-Töne), 16–20 → 23–27 (früher Prophet), 21 → 28
  (nur sechs sichtbare Plätze). Bank b hält U(b·18+1) … U(b·18+18), zusammen U001–U384.
* Datensatz (firmware/src/upreset.c up_rec_t, kompakt): used (0xA5, Bit 1 = Favorit),
  ver, engine, np, name[12], 144 Byte Werte/Voice, 46 Byte Rest.
* ältere Firmwares: UPB1 (16 × 238 Byte), NFM1 (FM6), CZCB (CZ-1), P5U1 (Prophet) –
  sie werden erkannt und beschrieben; unbekannte Objekte erscheinen generisch.

Auch SLOOP-Backups ("sloop-backup", Befehle 34–36) lassen sich ansehen, vergleichen und
(ganz oder teilweise) zurückspielen – in SLOOPs Reihenfolge (Banken, Projekte, laufendes
Projekt, Einstellungen, Samples).
Die Projektinhalte selbst entschlüsselt der Musik-Bereich: über
``register_project_decoder(fn)`` kann er eine Funktion fn(bytes, firmware) -> dict | None
anmelden (Schlüssel "summary" wird angezeigt).
"""
from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from . import backup

UPB2 = 0x32425055
UPB1 = 0x31425055
NFM1 = 0x314D464E
CZCB = 0x42435A43
P5U1 = 0x31553550
PER5 = 0x50455235
PER3 = 0x50455233
SMPF = 0x504D5346
UP_USED, UP_STAR = 0xA5, 2
PER_BANK = 18
SLOTS = 384
# gemeinsame Bank -> Backup-Objekt (Melodee 1.6, EDITOR_PROTOCOL.md "Shared user library")
SHARED_BANK_IDS = [6, 7, 17, 18, 21, 22, 19, 20, 9, 10, 11, 12, 13, 14, 15, 16, 23, 24, 25, 26, 27, 28]
BANK_OF_OID = {oid: b for b, oid in enumerate(SHARED_BANK_IDS)}
KNOWN_RECORD_VERSIONS = {4, 5, 8, 9, 10, 11, 12, 13}

ENGINES = {0: "ANALOG", 1: "DIGITAL", 2: "PHASE", 3: "SID", 4: "SAMPLE", 5: "VOICE", 6: "TRIO", 7: "WHEEL",
           8: "GRAIN", 9: "PHYS", 10: "DRUM", 11: "NOISE", 12: "FM6", 13: "SLICE", 14: "OBXF", 15: "CZ-1",
           19: "PROPHET", 20: "PROPHET", 21: "MIDI Track"}
RETIRED_ENGINES = {0, 1, 2, 4, 5, 6, 7, 8, 9, 11, 13, 14}


def engine_name(e: int) -> str:
    """Anzeigename wie überall (presetlib.engine_display_name), plus Hinweis auf stillgelegte Engines."""
    from .presetlib import engine_display_name
    n = ENGINES.get(e)
    if n is None:
        return engine_display_name(e)
    return engine_display_name(e) + (" (stillgelegt)" if e in RETIRED_ENGINES else "")


# SLOOP (web/editor.html, Engines in INFO-Reihenfolge)
SLOOP_ENGINES = ["ANALOG", "DIGITAL", "PHASE", "LOFI", "SAMPLE", "VOICE", "TRIO", "WHEEL", "GRAIN", "FM6", "PHYS",
                 "NOISE"]


def bank_origin(b: int) -> str:
    if b < 4:
        return "allgemein"
    if b < 8:
        return "früher FM6-Voices"
    if b < 16:
        return "früher CZ-1-Töne"
    if b < 21:
        return "früher Prophet-Programme"
    return "Zusatzplätze"


def bank_range(b: int) -> tuple[int, int]:
    first = b * PER_BANK + 1
    return first, min(first + PER_BANK - 1, SLOTS)


def slot_label(slot: int) -> str:
    return f"U{slot:03d}"


# ------------------------------------------------------------ Datentypen ---

@dataclass
class PresetSlot:
    slot: int                 # 1-basiert (U001 = 1)
    used: bool
    favorite: bool = False
    engine: int = 0
    name: str = ""
    version: int = 0
    oid: int = 0
    digest: str = ""          # Inhalt ohne Favoriten-Bit
    raw: bytes = b""
    prefix: str = "U"         # "U" (User-Preset), "F" (FM6-Voice), "CZ", "P5"
    fw: str = ""              # "sloop": SLOOPs Engine-Liste und Datensatz-Format

    @property
    def label(self) -> str:
        return f"{self.prefix}{self.slot:03d}"

    @property
    def engine_name(self) -> str:
        if self.fw == "sloop":
            return SLOOP_ENGINES[self.engine] if self.engine < len(SLOOP_ENGINES) else f"Engine {self.engine}"
        return engine_name(self.engine) if self.prefix == "U" else {"F": "FM6", "CZ": "CZ-1", "P5": "PROPHET"}.get(self.prefix, "")

    @property
    def known_format(self) -> bool:
        return self.version in KNOWN_RECORD_VERSIONS or self.prefix != "U" or self.fw == "sloop"


@dataclass
class ObjectView:
    id: int
    title: str
    group: str                # "Musik", "Projekte", "Presets", "Einstellungen", "Samples", "Sonstiges"
    size: int
    crc: int
    data: bytes
    format: str = ""
    detail: str = ""
    slot: int | None = None   # Projektplatz (0 = laufende Musik)
    presets: list[PresetSlot] = field(default_factory=list)
    project_info: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def used(self) -> bool:
        return self.size > 0

    @property
    def digest(self) -> str:
        return f"{self.size}:{self.crc:08x}"


@dataclass
class Report:
    format: str               # "felucca-backup" / "sloop-backup"
    firmware: str
    created: str
    companion: dict
    objects: list[ObjectView]
    known: bool               # Inventar, das der Web-Editor kennt
    restorable: bool          # die App kann es (teilweise) zurückspielen
    path: Path | None = None
    archive: backup.Archive | None = None
    settings: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def obj(self, oid: int) -> ObjectView | None:
        return next((o for o in self.objects if o.id == oid), None)

    @property
    def presets(self) -> list[PresetSlot]:
        out = [p for o in self.objects for p in o.presets]
        return sorted(out, key=lambda p: (p.prefix, p.slot))

    @property
    def used_presets(self) -> list[PresetSlot]:
        return [p for p in self.presets if p.used]

    @property
    def projects(self) -> list[ObjectView]:
        return sorted([o for o in self.objects if o.slot], key=lambda o: o.slot)

    @property
    def used_projects(self) -> list[ObjectView]:
        return [o for o in self.projects if o.used]

    @property
    def device(self) -> str:
        return self.companion.get("device", "")

    @property
    def firmware_key(self) -> str:
        k = self.companion.get("firmware", "")
        if k:
            return k
        v = self.firmware.lower()
        for name in ("melodee", "sloop", "skiff"):
            if name in v:
                return name
        return "sloop" if self.format == "sloop-backup" else ""

    def summary(self) -> str:
        from .autobackup import content_text
        n_s = sum(1 for o in self.objects if o.group == "Samples" and o.used)
        txt = content_text(len(self.used_projects), len(self.used_presets), len(self.objects))
        return txt.replace(" · ", f", {n_s} Samples · " if n_s else " · ")


# ----------------------------------------------------------- Dekodieren ---

def _name(b: bytes) -> str:
    b = b.split(b"\0", 1)[0]
    return "".join(chr(c) if 32 <= c < 127 else "?" for c in b).strip()


def _u32(d: bytes, off: int) -> int:
    return struct.unpack_from("<I", d, off)[0] if len(d) >= off + 4 else -1


def _digest(b: bytes) -> str:
    return hashlib.sha1(b).hexdigest()[:12]


def decode_up_bank(data: bytes, first_slot: int, oid: int) -> list[PresetSlot] | None:
    """UPB1/UPB2-Bank -> Plätze (None, wenn das Format nicht passt)."""
    if len(data) < 8 or _u32(data, 0) not in (UPB1, UPB2):
        return None
    rsize, n = struct.unpack_from("<HH", data, 4)
    if rsize < 16 or not n or 8 + rsize * n != len(data):
        return None
    out = []
    for i in range(n):
        slot = first_slot + i
        if _u32(data, 0) == UPB2 and slot > SLOTS:
            break                                   # Bank 21: nur sechs sichtbare Plätze
        r = data[8 + i * rsize: 8 + (i + 1) * rsize]
        used = (r[0] & ~UP_STAR) == UP_USED
        if not used:
            out.append(PresetSlot(slot, False, oid=oid))
            continue
        norm = bytes([r[0] & ~UP_STAR]) + r[1:]
        out.append(PresetSlot(slot, True, bool(r[0] & UP_STAR), r[2], _name(r[4:16]), r[1], oid, _digest(norm), r))
    return out


def decode_nfm1(data: bytes, first: int, oid: int) -> list[PresetSlot] | None:
    if len(data) != 2060 or _u32(data, 0) != NFM1:
        return None
    used = _u32(data, 8)
    out = []
    for k in range(16):
        v = data[12 + k * 128: 12 + (k + 1) * 128]
        u = bool(used >> k & 1)
        out.append(PresetSlot(first + k, u, engine=12, name=_name(v[118:128]) if u else "", oid=oid,
                              digest=_digest(v) if u else "", raw=v if u else b"", prefix="F", version=11))
    return out


def decode_czcb(data: bytes, first: int, oid: int) -> list[PresetSlot] | None:
    if len(data) != 2332 or _u32(data, 0) != CZCB:
        return None
    used = _u32(data, 8)
    out = []
    for k in range(16):
        t = data[28 + k * 144: 28 + (k + 1) * 144]
        u = bool(used >> k & 1)
        out.append(PresetSlot(first + k, u, engine=15, name=f"Ton {first + k}" if u else "", oid=oid,
                              digest=_digest(t) if u else "", raw=t if u else b"", prefix="CZ", version=13))
    return out


def decode_p5u1(data: bytes, first: int, oid: int) -> list[PresetSlot] | None:
    if len(data) != 3600 or _u32(data, 0) != P5U1:
        return None
    used, fav = _u32(data, 4), _u32(data, 8)
    out = []
    for k in range(26):
        p = data[12 + k * 138: 12 + (k + 1) * 138]
        u = bool(used >> k & 1)
        out.append(PresetSlot(first + k, u, bool(fav >> k & 1) and u, 19, f"Programm {first + k}" if u else "",
                              12, oid, _digest(p) if u else "", p if u else b"", prefix="P5"))
    return out


def decode_settings(data: bytes) -> dict:
    """die wichtigsten Felder des Einstellungs-Datensatzes (PER5, Melodee; PER3, SLOOP)."""
    if len(data) < 8:
        return {}
    magic = _u32(data, 0)
    if magic == PER5 and len(data) >= 604:
        boot, drumch, lights = _u32(data, 576), _u32(data, 588), _u32(data, 592)
        return {"Format": "PER5 (Melodee)",
                "Vorlage gespeichert": "ja" if len(data) > 604 else "nein",
                "Projekt beim Einschalten": f"Platz {boot}" if 1 <= boot <= 32 else "aus",
                "Drum-MIDI-Kanal": "aus" if drumch == 17 else str(drumch or 10),
                "Tastenbeleuchtung": ["mittel", "aus", "schwach", "mittel", "hell", "voll"][lights]
                if 0 <= lights <= 5 else str(lights)}
    if magic == PER3:
        return {"Format": "PER3 (SLOOP)"}
    return {"Format": f"unbekannt ({data[:4].hex()})"}


# ------------------------------------------------------------ Projekt-Hook ---

_project_decoders: list[Callable[[bytes, str], dict | None]] = []


def register_project_decoder(fn: Callable[[bytes, str], dict | None]):
    """Musik-Bereich: fn(data, firmware) -> {"summary": "...", ...} oder None."""
    if fn not in _project_decoders:
        _project_decoders.append(fn)


def project_info(data: bytes, firmware: str = "") -> dict:
    from .projects import format_of
    info = {"format": format_of(data)}
    for fn in _project_decoders:
        try:
            r = fn(data, firmware)
        except Exception:                           # noqa: BLE001 – Entschlüsseln ist nur Zugabe
            r = None
        if r:
            info.update(r)
            break
    return info


# ------------------------------------------------------------- Objekte ---

def describe(oid: int, data: bytes, crc: int, n_objects: int, firmware: str = "",
             sloop: bool = False) -> ObjectView:
    size = len(data)
    v = ObjectView(oid, f"Objekt {oid}", "Sonstiges", size, crc, data)
    if oid == 0:
        v.title, v.group, v.slot = "Laufende Musik", "Musik", None
        if size:
            v.project_info = project_info(data, firmware)
            v.format = v.project_info.get("format", "")
        return v
    if oid == 1:
        v.title, v.group = "Einstellungen", "Einstellungen"
        v.format = decode_settings(data).get("Format", "")
        return v
    slot = backup.project_slot(oid)
    if slot and not (sloop and oid > 5):
        v.slot = slot
        v.title = f"Song-Section {'ABCD'[slot - 1]}" if sloop else f"Projekt {slot}"
        v.group = "Projekte"
        if size:
            v.project_info = project_info(data, firmware)
            v.format = v.project_info.get("format", "")
            v.detail = v.project_info.get("summary", "")
        return v
    if backup.is_sample(oid):
        v.title, v.group = f"Sample-Slot USR{oid - 31}", "Samples"
        if size >= 512 and _u32(data, 0) == SMPF:
            v.format = "Sample"
            v.detail = f"{data[6]} Zonen, Name {_name(data[8:16]) or '–'}"
        return v
    if sloop:
        names = {6: "Preset-Bank 1–16", 7: "Preset-Bank 17–32", 8: "FM6-Patch-Bank", 9: "SYN-Drum-Kits",
                 10: "MIDI-Learn", 11: "Farbschema"}
        v.title = names.get(oid, v.title)
        v.group = "Presets" if oid in (6, 7, 8, 9) else "Einstellungen"
        if oid in (6, 7):
            first = 1 + (oid - 6) * 16
            pr = decode_up_bank(data, first, oid) if size else [PresetSlot(first + k, False, oid=oid) for k in range(16)]
            if pr is None:
                v.format = "unbekanntes Format"
            else:
                for x in pr:
                    x.fw = "sloop"
                v.presets, v.format = pr, "UPB1" if size else "leer"
                used = [x for x in pr if x.used]
                v.detail = f"{len(used)} von {len(pr)} belegt"
        elif oid == 10 and size:
            v.detail = "gelernte MIDI-Zuordnungen und Kanäle"
        return v
    if oid == 8:
        v.title, v.group = "Alte FM6-Bank (stillgelegt)", "Sonstiges"
        return v
    # Preset-Objekte
    v.group = "Presets"
    magic = _u32(data, 0) if size >= 4 else -1
    if n_objects in (29, 57) or magic == UPB2:
        b = BANK_OF_OID.get(oid)
        if b is not None:
            lo, hi = bank_range(b)
            v.title = f"Preset-Bank {slot_label(lo)}–{slot_label(hi)} ({bank_origin(b)})"
            if size:
                pr = decode_up_bank(data, lo, oid)
                if pr is None:
                    v.format = "unbekanntes Format"
                    v.warnings.append("Bank in einem älteren Format – die Firmware wandelt sie beim Start um")
                else:
                    v.presets, v.format = pr, "UPB2"
            else:
                v.presets = [PresetSlot(s, False, oid=oid) for s in range(lo, hi + 1)]
                v.format = "leer"
    else:
        # ältere Inventare
        if oid in (6, 7, 17, 18):
            first = {6: 1, 7: 17, 17: 33, 18: 49}[oid]
            v.title = f"User-Presets U{first:02d}–U{first + 15:02d}"
            pr = decode_up_bank(data, first, oid) if size else None
        elif 19 <= oid <= 22:
            first = {21: 1, 22: 17, 19: 33, 20: 49}[oid]
            v.title = f"FM6-Voices F{first:03d}–F{first + 15:03d}"
            pr = decode_nfm1(data, first, oid) if size else None
            if size and pr is None:
                v.format = "UPF6 (alte FM6-Voices)" if size in (3728, 4240) else ""
        elif 9 <= oid <= 16:
            first = (oid - 9) * 16 + 1
            v.title = f"CZ-1-Bank {oid - 8} (Töne {first}–{first + 15})"
            pr = decode_czcb(data, first, oid) if size else None
            if size >= 28 and _u32(data, 0) == CZCB:
                v.detail = f"Bankname {_name(data[12:28])}"
        elif 23 <= oid <= 27:
            first = (oid - 23) * 26 + 1
            v.title = f"Prophet-Bank {oid - 22} (Programme {first}–{first + 25})"
            pr = decode_p5u1(data, first, oid) if size else None
        else:
            v.title, v.group, pr = f"Objekt {oid}", "Sonstiges", None
        if pr is not None:
            v.presets = pr
            v.format = v.format or {UPB1: "UPB1", UPB2: "UPB2", NFM1: "NFM1", CZCB: "CZCB", P5U1: "P5U1"}.get(magic, "")
        elif size and not v.format:
            v.format = "unbekanntes Format"
    if v.presets:
        used = [p for p in v.presets if p.used]
        fav = sum(1 for p in used if p.favorite)
        v.detail = (v.detail + " · " if v.detail else "") + f"{len(used)} von {len(v.presets)} belegt" + \
            (f", {fav} Favoriten" if fav else "")
        if any(p.used and not p.known_format for p in v.presets):
            v.warnings.append("enthält Presets in einem Format, das diese Firmware nicht liest (OLD FORMAT)")
    return v


def inspect(source) -> Report:
    """Path / dict / JSON-Text / backup.Archive -> Report. Wirft backup.BackupError bei kaputten Dateien."""
    path = None
    if isinstance(source, backup.Archive):
        arch = source
        return _from_sloop(arch.file, None) if arch.sloop else _from_archive(arch, None)
    if isinstance(source, (str, Path)) and not (isinstance(source, str) and source.lstrip().startswith("{")):
        path = Path(source)
        source = path.read_text("utf-8")
    if isinstance(source, (str, bytes)):
        try:
            source = json.loads(source)
        except ValueError:
            raise backup.BackupError("Keine Backup-Datei (kein JSON)")
    if isinstance(source, dict) and source.get("format") == "sloop-backup":
        return _from_sloop(source, path)
    arch = backup.read(source)
    return _from_archive(arch, path)


def _from_archive(arch: backup.Archive, path: Path | None) -> Report:
    n = len(arch.objects)
    objs = [describe(o["id"], o["bytes"], o["crc"], n, arch.firmware) for o in arch.objects]
    rep = Report(backup.FORMAT, arch.firmware, arch.created, arch.companion, objs, arch.known, True, path, arch)
    s = rep.obj(1)
    if s:
        rep.settings = decode_settings(s.data)
    if not arch.known:
        rep.warnings.append(f"Inventar mit {n} Objekten: kennt der Web-Editor nicht (andere Firmware) – "
                            "die App zeigt es allgemein an.")
    return rep


def _from_sloop(f: dict, path: Path | None) -> Report:
    """SLOOP-Backup: dieselben Prüfungen wie backupObjects() im SLOOP-Editor."""
    arch = backup.read(f)
    objs = [describe(o["id"], o["bytes"], o["crc"], len(arch.objects), arch.firmware, sloop=True)
            for o in arch.objects]
    rep = Report("sloop-backup", arch.firmware, arch.created, arch.companion, objs, True, True, path, arch)
    s = rep.obj(1)
    if s:
        rep.settings = decode_settings(s.data)
    return rep


# ------------------------------------------------------------------ Diff ---

@dataclass
class Change:
    id: int
    title: str
    kind: str                 # "neu", "geleert", "geändert", "hinzugekommen", "fehlt"
    old_size: int = 0
    new_size: int = 0


@dataclass
class PresetChange:
    label: str
    kind: str                 # "neu", "gelöscht", "geändert"
    old: PresetSlot | None
    new: PresetSlot | None
    fields: list[str] = field(default_factory=list)

    def text(self) -> str:
        if self.kind == "neu":
            return f"{self.label}: neu „{self.new.name}“ ({self.new.engine_name})"
        if self.kind == "gelöscht":
            return f"{self.label}: „{self.old.name}“ gelöscht"
        bits = []
        if "Name" in self.fields:
            bits.append(f"„{self.old.name}“ → „{self.new.name}“")
        if "Engine" in self.fields:
            bits.append(f"{self.old.engine_name} → {self.new.engine_name}")
        if "Klang" in self.fields:
            bits.append("Klang geändert")
        if "Favorit" in self.fields:
            bits.append("Favorit " + ("an" if self.new.favorite else "aus"))
        return f"{self.label} „{self.new.name}“: " + ", ".join(bits)


@dataclass
class Diff:
    a: Report
    b: Report
    objects: list[Change]
    presets: list[PresetChange]

    @property
    def identical(self) -> bool:
        return not self.objects

    def changed_ids(self) -> set[int]:
        return {c.id for c in self.objects}

    def summary(self) -> str:
        if self.identical:
            return "Keine Unterschiede – beide Stände sind gleich."
        parts = []
        pc = {k: sum(1 for p in self.presets if p.kind == k) for k in ("neu", "geändert", "gelöscht")}
        for k, word in (("geändert", "geändert"), ("neu", "neu"), ("gelöscht", "gelöscht")):
            if pc[k]:
                parts.append(f"{pc[k]} Preset{'s' if pc[k] != 1 else ''} {word}")
        bank_changes = [c for c in self.objects if c.title.startswith(("Preset", "User-Presets", "FM6", "CZ", "Prophet"))]
        if bank_changes and not self.presets:
            parts.append(f"{len(bank_changes)} Preset-Objekte geändert")
        for c in self.objects:
            if c.title.startswith(("Projekt", "Song-Section")):
                word = {"neu": "neu", "geleert": "geleert", "geändert": "geändert",
                        "hinzugekommen": "neu", "fehlt": "fehlt"}[c.kind]
                parts.append(f"{c.title} {word}")
        live = next((c for c in self.objects if c.id == 0), None)
        if live:
            parts.append("laufende Musik geändert")
        parts.append("Einstellungen " + ("geändert" if any(c.id == 1 for c in self.objects) else "gleich"))
        rest = [c for c in self.objects if c.id not in (0, 1) and not c.title.startswith(
            ("Projekt", "Song-Section", "Preset", "User-Presets", "FM6", "CZ", "Prophet"))]
        if rest:
            parts.append(f"{len(rest)} weitere Objekte geändert")
        return ", ".join(parts)


def diff(a: Report, b: Report) -> Diff:
    """Unterschiede von a (alt) nach b (neu)."""
    changes = []
    ids = [o.id for o in a.objects] + [o.id for o in b.objects if not a.obj(o.id)]
    for oid in ids:
        oa, ob = a.obj(oid), b.obj(oid)
        title = (ob or oa).title
        if oa is None:
            if ob.used:
                changes.append(Change(oid, title, "hinzugekommen", 0, ob.size))
            continue
        if ob is None:
            if oa.used:
                changes.append(Change(oid, title, "fehlt", oa.size, 0))
            continue
        if oa.digest == ob.digest:
            continue
        kind = "neu" if not oa.used else "geleert" if not ob.used else "geändert"
        changes.append(Change(oid, title, kind, oa.size, ob.size))
    pa = {p.label: p for p in a.presets}
    pb = {p.label: p for p in b.presets}
    pchanges = []
    for label in sorted(set(pa) | set(pb)):
        x, y = pa.get(label), pb.get(label)
        xu, yu = bool(x and x.used), bool(y and y.used)
        if not xu and not yu:
            continue
        if not xu:
            pchanges.append(PresetChange(label, "neu", None, y))
        elif not yu:
            pchanges.append(PresetChange(label, "gelöscht", x, None))
        else:
            f = []
            if x.name != y.name:
                f.append("Name")
            if x.engine != y.engine:
                f.append("Engine")
            # Name/Engine stecken im Datensatz – "Klang" nur, wenn sich mehr geändert hat
            if x.digest != y.digest and _payload(x) != _payload(y):
                f.append("Klang")
            if x.favorite != y.favorite:
                f.append("Favorit")
            if f:
                pchanges.append(PresetChange(label, "geändert", x, y, f))
    return Diff(a, b, changes, pchanges)


def _payload(p: PresetSlot) -> bytes:
    r = p.raw
    if p.prefix == "U" and len(r) >= 16:
        return r[1:2] + r[3:4] + r[16:]            # ohne used/engine/Name
    return r


# ------------------------------------------------- Teil-Zurückspielen ---

@dataclass
class PlanItem:
    id: int
    title: str
    data: bytes
    action: str               # "überschreiben", "neu belegen", "leeren", "gleich"
    detail: str = ""


@dataclass
class Plan:
    items: list[PlanItem]                  # in Schreibreihenfolge
    skipped: list[PlanItem]
    errors: list[str]
    notes: list[str]
    clear_extension: bool = False
    expect: dict = field(default_factory=dict)   # id -> (size, crc) auf dem Gerät beim Planen

    @property
    def ok(self) -> bool:
        return not self.errors and bool(self.items)

    def ids(self) -> list[int]:
        return [i.id for i in self.items]

    def html(self, device: str = "dem FM-1") -> str:
        if self.errors:
            return "<br>".join(self.errors)
        lines = [f"Auf <b>{device}</b> wird jetzt geschrieben:", "<ul>"]
        for it in self.items:
            lines.append(f"<li><b>{it.title}</b>: {it.action}" + (f"<br><small>{it.detail}</small>" if it.detail else "")
                         + "</li>")
        lines.append("</ul>")
        if self.skipped:
            lines.append("Schon identisch (wird nicht geschrieben): " + ", ".join(i.title for i in self.skipped) + "<br>")
        for n in self.notes:
            lines.append(n + "<br>")
        lines.append("Alles andere auf dem Gerät bleibt unverändert. Das Gerät prüft jedes Objekt vorher – "
                     "passt eines nicht, wird gar nichts geschrieben.")
        return "".join(lines)


SELECTABLE_SKIP = {8}                     # Melodee: alte, stillgelegte FM6-Bank


def is_sloop(rep: Report) -> bool:
    return rep.format == "sloop-backup"


def skip_ids(rep: Report) -> set[int]:
    """Objekte, die man nicht einzeln zurückspielen kann (SLOOP: keine)."""
    return set() if is_sloop(rep) else SELECTABLE_SKIP


def selectable(rep: Report) -> list[ObjectView]:
    """Objekte, die man einzeln zurückspielen kann."""
    return [o for o in rep.objects if o.id not in skip_ids(rep)]


def plan_restore(rep: Report, ids: Iterable[int], device_manifest: list | None,
                 current: Report | None = None, device_inventory_len: int | None = None) -> Plan:
    """Prüft eine Auswahl gegen das Gerät (Inventar aus BACKUP_LIST) und ordnet sie wie der Editor:
    Projekte/Banken/Samples, dann Einstellungen, die laufende Musik zuletzt."""
    want = [i for i in dict.fromkeys(ids) if i not in skip_ids(rep)]
    errors, notes = [], []
    if not rep.restorable or rep.archive is None:
        return Plan([], [], ["Dieses Backup-Format kann die App nicht zurückspielen."], [])
    if is_sloop(rep):
        return _plan_sloop(rep, want, device_manifest, current)
    man = {o.id: o for o in (device_manifest or [])}
    n_dev = device_inventory_len if device_inventory_len is not None else len(man)
    r16 = refuse_16(rep, current.firmware if current is not None else "", n_dev if man else None)
    if r16:
        return Plan([], [], [r16], [])
    # alte Preset-Banken (nicht UPB2) nur gemeinsam – die Firmware wandelt sie zusammen um
    old_banks = [o.id for o in rep.objects if o.group == "Presets" and o.used and o.format != "UPB2"]
    if n_dev in (29, 57) and any(i in old_banks for i in want):
        missing = [i for i in old_banks if i not in want]
        if missing:
            want += missing
            notes.append("Preset-Banken aus einer älteren Firmware gehen nur gemeinsam zurück – "
                         "die Firmware wandelt sie beim nächsten Start zusammen um. Alle Banken wurden mit ausgewählt.")
    clear_ext = len(rep.objects) == 28 and 28 in man and any(i in old_banks for i in want)
    live = man[0].size if 0 in man else 0
    items, skipped = [], []
    for oid in want:
        o = rep.obj(oid)
        if o is None:
            errors.append(f"Objekt {oid} ist nicht im Backup.")
            continue
        if man and oid not in man:
            if o.used:
                errors.append(f"<b>{o.title}</b>: diese Firmware auf dem Gerät kennt das Objekt nicht.")
            continue
        if man and n_dev != 57 and o.slot is not None and o.size > live and live and \
                not (live >= 19008 and o.size in (19084, 19092, 20224)):
            errors.append(f"<b>{o.title}</b> braucht eine Firmware mit größerem Aufnahmespeicher.")
            continue
        cur = man.get(oid)
        if cur is not None and cur.size == o.size and cur.crc == o.crc:
            skipped.append(PlanItem(oid, o.title, o.data, "gleich"))
            continue
        if not o.used:
            action = "wird geleert" if (cur is None or cur.size) else "bleibt leer"
        elif cur is not None and not cur.size:
            action = "leerer Platz wird belegt"
        else:
            action = "wird überschrieben"
        detail = ""
        if current is not None and o.presets:
            co = current.obj(oid)
            if co is not None:
                d = diff(Report(rep.format, "", "", {}, [co], True, True),
                         Report(rep.format, "", "", {}, [o], True, True))
                if d.presets:
                    txt = [p.text() for p in d.presets[:8]]
                    detail = "; ".join(txt) + (f" … und {len(d.presets) - 8} weitere" if len(d.presets) > 8 else "")
        elif current is not None and o.slot is not None:
            co = current.obj(oid)
            if co is not None and co.used:
                detail = f"jetzt auf dem Gerät: {co.size} Byte ({co.format or 'Projekt'})"
        items.append(PlanItem(oid, o.title, o.data, action, detail))
    order = {oid: i for i, oid in enumerate([o.id for o in rep.objects[2:]] + [1, 0])}
    items.sort(key=lambda it: order.get(it.id, 999))
    if any(it.id == 0 for it in items):
        notes.append("Die laufende Musik wird zuletzt ersetzt – die Wiedergabe stoppt dabei.")
    if not items and not errors:
        errors.append("Nichts zu tun: alles Ausgewählte ist auf dem Gerät schon genau so." if skipped
                      else "Nichts ausgewählt.")
    expect = {oid: (m.size, m.crc) for oid, m in man.items() if oid in {it.id for it in items}}
    return Plan(items, skipped, errors, notes, clear_ext, expect)


def _plan_sloop(rep: Report, want: list[int], device_manifest: list | None, current: Report | None) -> Plan:
    """Teil-Zurückspielen eines SLOOP-Backups: Reihenfolge wie backupRestore im SLOOP-Editor."""
    from . import sloopbk
    errors, notes = [], []
    man = {o.id: o for o in (device_manifest or [])}
    items, skipped = [], []
    for oid in want:
        o = rep.obj(oid)
        if o is None:
            errors.append(f"Objekt {oid} ist nicht im Backup.")
            continue
        if oid in (0, 1) and not o.used:
            errors.append(f"<b>{o.title}</b> ist im Backup leer – das nimmt SLOOP nicht an.")
            continue
        if man and oid not in man and oid not in (10, 11):
            # 8 (FM6-Bank) und 9 (SYN-Kits) listet jede Firmware, die sie kennt; 10/11 nur, wenn belegt
            if o.used:
                notes.append(f"{o.title}: kennt die SLOOP-Version auf dem Gerät nicht – wird übersprungen.")
            continue
        cur = man.get(oid)
        if cur is not None and cur.size == o.size and cur.crc == o.crc:
            skipped.append(PlanItem(oid, o.title, o.data, "gleich"))
            continue
        if oid in (10, 11) and cur is None and not o.used:
            skipped.append(PlanItem(oid, o.title, o.data, "gleich"))
            continue
        if not o.used:
            action = "wird geleert" if (cur is None or cur.size) else "bleibt leer"
        elif cur is not None and not cur.size:
            action = "leerer Platz wird belegt"
        else:
            action = "wird überschrieben"
        detail = ""
        if current is not None and o.presets:
            co = current.obj(oid)
            if co is not None:
                d = diff(Report(rep.format, "", "", {}, [co], True, True), Report(rep.format, "", "", {}, [o], True, True))
                if d.presets:
                    detail = "; ".join(p.text() for p in d.presets[:8]) + \
                        (f" … und {len(d.presets) - 8} weitere" if len(d.presets) > 8 else "")
        elif current is not None and (o.slot is not None or oid == 0):
            co = current.obj(oid)
            if co is not None and co.used:
                detail = f"jetzt auf dem Gerät: {co.size} Byte ({co.format or 'Projekt'})"
        items.append(PlanItem(oid, o.title, o.data, action, detail))
    order = {oid: i for i, oid in enumerate(sloopbk.RESTORE + sloopbk.SAMPLES)}
    items.sort(key=lambda it: order.get(it.id, 999))
    newest = max([0] + [sloopbk.proj_format(it.data) for it in items if it.id in (0, 2, 3, 4, 5)])
    dev_fmt = max([0] + [sloopbk.proj_format(current.obj(i).data) for i in (0,) if current and current.obj(i)])
    if dev_fmt and newest > dev_fmt:
        errors.append(f"Die Projekte im Backup (Format FUN{newest}) sind neuer, als die SLOOP-Version auf dem "
                      f"Gerät liest (FUN{dev_fmt}). Erst SLOOP aktualisieren.")
    if items and any(it.id not in sloopbk.SAMPLES and it.id != 1 for it in items):
        notes.append("Der Song muss am FM-1 gestoppt sein (STOP) – sonst lehnt SLOOP das Schreiben ab "
                     "(dann wird nichts ersetzt).")
    if any(it.id == 0 for it in items):
        notes.append("Das laufende Projekt wird sofort geladen.")
    if not items and not errors:
        errors.append("Nichts zu tun: alles Ausgewählte ist auf dem Gerät schon genau so." if skipped
                      else "Nichts ausgewählt.")
    expect = {oid: (m.size, m.crc) for oid, m in man.items() if oid in {it.id for it in items}}
    return Plan(items, skipped, errors, notes, False, expect)


# --------------------------------------------- Geräte-Kompatibilität ---

def fw_name(key: str) -> str:
    """Anzeigename zu einem Firmware-Schlüssel („melodee“ → „Melodee“)."""
    if not key:
        return "unbekannte Firmware"
    try:
        from .firmware import spec
        sp = spec(key)
        if sp:
            return sp.name
    except Exception:                               # noqa: BLE001
        pass
    return {"melodee": "Melodee", "sloop": "SLOOP", "skiff": "Skiff", "felucca": "Felucca"}.get(key, key)


def comparable(a: Report, b: Report) -> str:
    """"" wenn sich zwei Stände sinnvoll vergleichen lassen, sonst der Grund in Alltagssprache.
    Verschiedene Firmware-Familien (Melodee ↔ SLOOP) haben andere Objekte und Formate –
    ein Unterschieds-Vergleich wäre Unsinn („Projekt 5 fehlt“)."""
    if a.format != b.format:
        return ("Nicht vergleichbar: das eine ist ein Melodee-/Felucca-Backup, das andere ein SLOOP-Backup. "
                "Die beiden Firmwares speichern Projekte, Presets und Einstellungen völlig verschieden.")
    ka, kb = a.firmware_key, b.firmware_key
    if ka and kb and ka != kb:
        return (f"Nicht vergleichbar: der eine Stand stammt von <b>{fw_name(ka)}</b>, der andere von "
                f"<b>{fw_name(kb)}</b>. Vergleichen geht nur innerhalb derselben Firmware.")
    return ""


@dataclass
class Compat:
    ok: bool
    reason: str
    possible: list[str] = field(default_factory=list)   # was trotzdem geht
    allowed_ids: set[int] = field(default_factory=set)


def compatibility(src: Report, target_fw_key: str, target_version: str, target_ids: list[int] | None) -> Compat:
    """Kann der Inhalt von src auf ein Gerät mit dieser Firmware?"""
    sk = src.firmware_key
    tk = target_fw_key or ""
    if not src.restorable:
        return Compat(False, "Dieses Backup-Format kann die App nicht einspielen.")
    if not sk or not tk:
        return Compat(False, "Die Firmware einer Seite ist unbekannt – zur Sicherheit wird nichts kopiert.")
    if sk != tk:
        return Compat(False, f"Das Backup stammt von <b>{fw_name(sk)}</b>, auf dem Ziel läuft <b>{fw_name(tk)}</b>. "
                             "Projekte, Presets und Einstellungen haben bei verschiedenen Firmwares andere "
                             "Formate – das Ziel würde sie ablehnen oder falsch lesen. Es wird nichts geschrieben.",
                      ["Presets einzeln über die Preset-Bibliothek übertragen (sobald verfügbar)",
                       "Projekte als MIDI exportieren und dort neu aufbauen",
                       "oder das Ziel-FM-1 auf dieselbe Firmware bringen"])
    ids = {o.id for o in src.objects}
    r16 = refuse_16(src, target_version, None if target_ids is None else len(target_ids))
    if r16:
        return Compat(False, r16, ["das FM-1 auf Melodee 1.6 oder neuer bringen (Firmware+), dann zurückspielen",
                                   "ein Backup von vor dem Update auf Melodee 1.6 nehmen",
                                   "Projekte als MIDI exportieren (Projekte › MIDI-Export)"])
    if target_ids is not None and is_sloop(src):
        target_ids = list(target_ids) + [i for i in (10, 11) if i not in target_ids]   # nur belegt im Inventar
    if target_ids is not None:
        common = ids & set(target_ids)
        if not common:
            return Compat(False, "Ziel kennt keines der Objekte aus der Quelle.")
        note = ""
        if len(target_ids) != len(src.objects):
            note = (f" Inventar unterschiedlich ({len(src.objects)} ↔ {len(target_ids)} Objekte, andere Version): "
                    "nur gemeinsame Objekte, das Gerät prüft jedes vorher.")
        from .firmware import version_key
        sv = _ver(src.firmware)
        tv = _ver(target_version)
        if sv and tv and version_key(sv) > version_key(tv):
            note += f" Quelle ist neuer ({sv} → {tv}): neuere Projekte lehnt das Gerät evtl. ab."
        return Compat(True, "Gleiche Firmware-Familie – kopieren möglich." + note, allowed_ids=common - skip_ids(src))
    return Compat(True, "Gleiche Firmware-Familie.", allowed_ids=ids - skip_ids(src))


def refuse_16(src: Report, target_version: str = "", target_inventory: int | None = None) -> str:
    """Backup von Melodee 1.6 auf eine ältere Melodee? -> deutsche Erklärung, sonst ''. Ältere Firmware lehnt
    Backups von 1.6 ab (RELEASE_1.6.0.md „Upgrading“) – die App sagt das vorher statt eines Gerätefehlers."""
    from . import melodee16 as M
    if is_sloop(src) or src.firmware_key not in ("melodee", ""):
        return ""
    if not M.backup_is_16(src.firmware, src.companion, len(src.objects), [o.id for o in src.objects if o.used]):
        return ""
    if not M.device_before_16(target_version, target_inventory, "melodee"):
        return ""
    return M.restore_refusal(src.companion.get("firmware_version") or src.firmware, target_version)


def _ver(s: str) -> str:
    import re
    m = re.search(r"(\d+\.\d+(?:\.\d+)?(?:-rc\.?\d+)?)", s or "")
    return m.group(1) if m else ""


@dataclass
class DeviceComparison:
    rows: list[tuple[str, str, str]]        # (Eigenschaft, A, B)
    only_a: list[str]
    only_b: list[str]
    both: list[str]


def compare_devices(a: Report | None, b: Report | None, fw_a: str = "", fw_b: str = "") -> DeviceComparison:
    def f(r, fallback):
        return (r.firmware if r else "") or fallback or "–"
    rows = [("Firmware", f(a, fw_a), f(b, fw_b)),
            ("Stand vom", a.created if a else "kein Backup", b.created if b else "kein Backup"),
            ("Projekte belegt", ", ".join(str(o.slot) for o in a.used_projects) or "keine" if a else "–",
             ", ".join(str(o.slot) for o in b.used_projects) or "keine" if b else "–"),
            ("Presets belegt", str(len(a.used_presets)) if a else "–", str(len(b.used_presets)) if b else "–")]
    na = {p.name for p in a.used_presets} if a else set()
    nb = {p.name for p in b.used_presets} if b else set()
    return DeviceComparison(rows, sorted(na - nb), sorted(nb - na), sorted(na & nb))
