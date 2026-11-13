# SPDX-License-Identifier: GPL-3.0-only
"""Explain, compare and selectively restore (plan) backups for humans.

The basis is the inventory of Felucca/Melodee backups (web/EDITOR_PROTOCOL.md,
"Historical v7: full backup" and "Shared user library"):

* 0 running music, 1 settings (PER5), 2–5 and 64–91 project slots 1–32
* Melodee 1.6 (29/57 objects): 22 shared preset banks in UPB2 format
  (3716 bytes: 8-byte header, 18 records × 206 bytes). Bank → object:
  0–3 → 6, 7, 17, 18 (general), 4–7 → 21, 22, 19, 20 (formerly FM6 voices),
  8–15 → 9–16 (formerly CZ-1 tones), 16–20 → 23–27 (formerly Prophet), 21 → 28
  (only six visible slots). Bank b holds U(b·18+1) … U(b·18+18), together U001–U384.
* Record (firmware/src/upreset.c up_rec_t, compact): used (0xA5, bit 1 = favourite),
  ver, engine, np, name[12], 144 bytes of values/voice, 46 bytes rest.
* older firmwares: UPB1 (16 × 238 bytes), NFM1 (FM6), CZCB (CZ-1), P5U1 (Prophet) –
  they are recognised and described; unknown objects appear generically.

SLOOP backups ("sloop-backup", commands 34–36) can be viewed, compared and
restored (fully or partially) as well – in SLOOP's order (banks, projects, running
project, settings, samples).
The project contents themselves are decoded by the music section: via
``register_project_decoder(fn)`` it can register a function fn(bytes, firmware) -> dict | None
(key "summary" is displayed).
"""
from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from . import backup
from .i18n import N_, _, ngettext, pgettext

# object groups – identifiers (compared in code and tests); display text: group_text()
G_MUSIC, G_PROJECTS, G_PRESETS = "Musik", "Projekte", "Presets"          # i18n: ignore
G_SETTINGS, G_SAMPLES, G_OTHER = "Einstellungen", "Samples", "Sonstiges"  # i18n: ignore
GROUP_TEXT = {G_MUSIC: N_("Music"), G_PROJECTS: N_("Projects"), G_PRESETS: N_("Presets"), G_SETTINGS: N_("Settings"),
              G_SAMPLES: N_("Samples"), G_OTHER: N_("Other")}
# change kinds – identifiers; display text: kind_text()
K_NEW, K_CLEARED, K_CHANGED, K_ADDED, K_MISSING, K_DELETED, K_SAME = (
    "neu", "geleert", "geändert", "hinzugekommen", "fehlt", "gelöscht", "gleich")    # i18n: ignore
KIND_TEXT = {K_NEW: N_("new"), K_CLEARED: N_("cleared"), K_CHANGED: N_("changed"), K_ADDED: N_("added"),
             K_MISSING: N_("missing"), K_DELETED: N_("deleted"), K_SAME: N_("same")}
# changed preset fields – identifiers
F_NAME, F_ENGINE, F_SOUND, F_FAVORITE = "Name", "Engine", "Klang", "Favorit"        # i18n: ignore


def group_text(group: str) -> str:
    return _(GROUP_TEXT[group]) if group in GROUP_TEXT else group


def kind_text(kind: str) -> str:
    return _(KIND_TEXT[kind]) if kind in KIND_TEXT else kind

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
# shared bank -> backup object (Melodee 1.6, EDITOR_PROTOCOL.md "Shared user library")
SHARED_BANK_IDS = [6, 7, 17, 18, 21, 22, 19, 20, 9, 10, 11, 12, 13, 14, 15, 16, 23, 24, 25, 26, 27, 28]
BANK_OF_OID = {oid: b for b, oid in enumerate(SHARED_BANK_IDS)}
KNOWN_RECORD_VERSIONS = {4, 5, 8, 9, 10, 11, 12, 13}

ENGINES = {0: "ANALOG", 1: "DIGITAL", 2: "PHASE", 3: "SID", 4: "SAMPLE", 5: "VOICE", 6: "TRIO", 7: "WHEEL",
           8: "GRAIN", 9: "PHYS", 10: "DRUM", 11: "NOISE", 12: "FM6", 13: "SLICE", 14: "OBXF", 15: "CZ-1",
           19: "PROPHET", 20: "PROPHET", 21: "MIDI Track"}
RETIRED_ENGINES = {0, 1, 2, 4, 5, 6, 7, 8, 9, 11, 13, 14}


def engine_name(e: int) -> str:
    """Display name as everywhere (presetlib.engine_display_name), plus a note on retired engines."""
    from .presetlib import engine_display_name
    n = ENGINES.get(e)
    if n is None:
        return engine_display_name(e)
    if e in RETIRED_ENGINES:
        return _("{engine} (retired)").format(engine=engine_display_name(e))
    return engine_display_name(e)


# SLOOP (web/editor.html, engines in INFO order)
SLOOP_ENGINES = ["ANALOG", "DIGITAL", "PHASE", "LOFI", "SAMPLE", "VOICE", "TRIO", "WHEEL", "GRAIN", "FM6", "PHYS",
                 "NOISE"]


def bank_origin(b: int) -> str:
    if b < 4:
        return _("general")
    if b < 8:
        return _("formerly FM6 voices")
    if b < 16:
        return _("formerly CZ-1 tones")
    if b < 21:
        return _("formerly Prophet programs")
    return _("extra slots")


def bank_range(b: int) -> tuple[int, int]:
    first = b * PER_BANK + 1
    return first, min(first + PER_BANK - 1, SLOTS)


def slot_label(slot: int) -> str:
    return f"U{slot:03d}"


# ------------------------------------------------------------ data types ---

@dataclass
class PresetSlot:
    slot: int                 # 1-based (U001 = 1)
    used: bool
    favorite: bool = False
    engine: int = 0
    name: str = ""
    version: int = 0
    oid: int = 0
    digest: str = ""          # content without the favourite bit
    raw: bytes = b""
    prefix: str = "U"         # "U" (user preset), "F" (FM6 voice), "CZ", "P5"
    fw: str = ""              # "sloop": SLOOP's engine list and record format

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
    group: str                # "Musik", "Projekte", "Presets", "Einstellungen", "Samples", "Sonstiges" (category keys)
    size: int
    crc: int
    data: bytes
    format: str = ""
    detail: str = ""
    slot: int | None = None   # project slot (0 = running music)
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
    known: bool               # inventory the web editor knows
    restorable: bool          # the app can restore it (partially)
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
            return "jiant" if k.lower().startswith("jiant") else k       # (the catalog's "jiant-fm" and the twin's "jiant")
        v = self.firmware.lower()
        if "karma" in v:                                                  # (KARMA: "FELUCCA KARMA 3.1", SLOOP's format)
            return "karma"
        if "orbit" in v or (self.archive is not None and self.archive.file.get("product") == "ORBIT"):
            return "orbit"                                                # (ORBIT: "FELUCCA ORBIT 1.0.0", SLOOP's format)
        for name in ("melodee", "sloop", "skiff", "jiant", "coralflower", "choralroot"):
            if name in v:
                return name
        if self.format in backup.IMAGE_FORMATS:
            return backup.IMAGE_FORMATS[self.format]
        if self.format != "sloop-backup" and backup.is_jiant(o.id for o in self.objects):
            return "jiant"
        if self.format != "sloop-backup" and backup.is_coralflower(o.id for o in self.objects):
            return "coralflower"
        if self.format != "sloop-backup" and backup.is_felucca([o.id for o in self.objects], v):
            return "felucca"                                              # (Felucca's own editor: "FELUCCA v1.8.1")
        if self.format != "sloop-backup" and backup.is_choralroot(o.id for o in self.objects):
            return "choralroot"
        return "sloop" if self.format == "sloop-backup" else ""

    def summary(self) -> str:
        from .autobackup import content_text
        n_s = sum(1 for o in self.objects if o.group == G_SAMPLES and o.used)
        txt = content_text(len(self.used_projects), len(self.used_presets), len(self.objects))
        if not n_s:
            return txt
        return txt.replace(" · ", ", " + ngettext("{n} sample", "{n} samples", n_s).format(n=n_s) + " · ", 1)


# ----------------------------------------------------------- decoding ---

def _name(b: bytes) -> str:
    b = b.split(b"\0", 1)[0]
    return "".join(chr(c) if 32 <= c < 127 else "?" for c in b).strip()


def _u32(d: bytes, off: int) -> int:
    return struct.unpack_from("<I", d, off)[0] if len(d) >= off + 4 else -1


def _digest(b: bytes) -> str:
    return hashlib.sha1(b).hexdigest()[:12]


def decode_up_bank(data: bytes, first_slot: int, oid: int) -> list[PresetSlot] | None:
    """UPB1/UPB2 bank -> slots (None if the format does not match)."""
    if len(data) < 8 or _u32(data, 0) not in (UPB1, UPB2):
        return None
    rsize, n = struct.unpack_from("<HH", data, 4)
    if rsize < 16 or not n or 8 + rsize * n != len(data):
        return None
    out = []
    for i in range(n):
        slot = first_slot + i
        if _u32(data, 0) == UPB2 and slot > SLOTS:
            break                                   # bank 21: only six visible slots
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
        out.append(PresetSlot(first + k, u, engine=15, name=_("Tone {n}").format(n=first + k) if u else "", oid=oid,
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
        out.append(PresetSlot(first + k, u, bool(fav >> k & 1) and u, 19, _("Program {n}").format(n=first + k) if u else "",
                              12, oid, _digest(p) if u else "", p if u else b"", prefix="P5"))
    return out


def decode_settings(data: bytes) -> dict:
    """the most important fields of the settings record (PER5, Melodee; PER3, SLOOP)."""
    if len(data) < 8:
        return {}
    magic = _u32(data, 0)
    if magic == PER5 and len(data) >= 604:
        boot, drumch, lights = _u32(data, 576), _u32(data, 588), _u32(data, 592)
        off = pgettext("setting", "off")
        return {"Format": "PER5 (Melodee)",
                _("Template saved"): _("yes") if len(data) > 604 else _("no"),
                _("Project at power-on"): _("slot {n}").format(n=boot) if 1 <= boot <= 32 else off,
                _("Drum MIDI channel"): off if drumch == 17 else str(drumch or 10),
                _("Key lighting"): [_("medium"), off, _("low"), _("medium"), _("bright"), _("full")][lights]
                if 0 <= lights <= 5 else str(lights)}
    if magic == PER3:
        return {"Format": "PER3 (SLOOP)"}
    return {"Format": _("unknown ({magic})").format(magic=data[:4].hex())}


# ------------------------------------------------------------ project hook ---

_project_decoders: list[Callable[[bytes, str], dict | None]] = []


def register_project_decoder(fn: Callable[[bytes, str], dict | None]):
    """Music section: fn(data, firmware) -> {"summary": "...", ...} or None."""
    if fn not in _project_decoders:
        _project_decoders.append(fn)


def project_info(data: bytes, firmware: str = "") -> dict:
    from .projects import format_of
    info = {"format": format_of(data)}
    for fn in _project_decoders:
        try:
            r = fn(data, firmware)
        except Exception:                           # noqa: BLE001 – decoding is just a bonus
            r = None
        if r:
            info.update(r)
            break
    return info


# ------------------------------------------------------------- objects ---

SKIFF_IDS = list(range(10)) + list(range(16, 35))     # Skiff 2.x (web/fm1backup.js BACKUP_IDS)


def _jiant_object(v: ObjectView, oid: int, size: int) -> ObjectView | None:
    """JIANT FM's music objects (editor_backup.c): 2..5 the current song's sections, 10 the song index, 40..71 every
    song's sections (40 + 4 x song + section). JIANT's section format is not decoded here (no project view)."""
    if 2 <= oid <= 5:
        v.title = _("Section {letter} of the current song").format(letter="ABCD"[oid - 2])
    elif oid == 10:
        v.title = _("Song index (every song's rows and scenes)")
    elif 40 <= oid < 72:
        v.title = _("Song {song}, section {letter}").format(song=(oid - 40) // 4 + 1, letter="ABCD"[(oid - 40) % 4])
    else:
        return None
    v.group = G_PROJECTS
    v.format = "" if size else _("empty")
    return v


def _coral_object(v: ObjectView, oid: int, size: int) -> ObjectView:
    """CoralFlower's objects (cf_backup.c): ChoralRoot's sound stores 6..11, its settings 30, its loop 31, the
    sequencer's patterns and song 50..55, the sounds' unsaved edits 56..58, the patterns' own sounds 59, the tweaked
    pattern sounds 60..67. Their formats are not decoded here (no project or preset view)."""
    if oid in (6, 7):
        v.title, v.group = _("User sounds {first}–{last}").format(first=1 + (oid - 6) * 16, last=16 + (oid - 6) * 16), G_PRESETS
    elif oid == 8:
        v.title, v.group = _("FM6 patch bank"), G_PRESETS
    elif oid == 9:
        v.title, v.group = _("VA patches of the user sounds"), G_PRESETS
    elif oid in (10, 11):
        v.title, v.group = _("FM6 patches of the user sounds {first}–{last}").format(
            first=1 + (oid - 10) * 16, last=16 + (oid - 10) * 16), G_PRESETS
    elif oid == 30:
        v.title, v.group = _("Settings"), G_SETTINGS
    elif oid == 31:
        v.title, v.group = _("Loop / rhythm machine"), G_PROJECTS
    elif 50 <= oid <= 55:
        v.title, v.group = _("Patterns and song (part {n} of 6)").format(n=oid - 49), G_PROJECTS
    elif 56 <= oid <= 58:
        v.title = (_("Unsaved edits of the chord sound"), _("Unsaved edits of the bass sound"),
                   _("Unsaved edits of the layer sound"))[oid - 56]
        v.group = G_PRESETS
    elif oid == 59:
        v.title, v.group = _("The patterns' own sounds"), G_PROJECTS
    elif 60 <= oid <= 67:
        v.title, v.group = _("Tweaked pattern sound {n}").format(n=oid - 59), G_PROJECTS
    v.format = "" if size else _("empty")
    return v


def _choral_object(v: ObjectView, oid: int, size: int) -> ObjectView:
    """ChoralRoot FM-1's objects (cr_backup.c, web/fm1backup.js objectName): 1 its settings, 8 the FM6 bank, 14..21 the
    CZ-1 banks A..H, 23 the favourites, 40..49 the loop slots, 50..62 the sound pool's banks (0.22); up to 0.21 the
    user sounds 6, 7 with their patches 9..13 and 22. Their formats are not decoded here (no project or preset view)."""
    if oid == 1:
        v.title, v.group = _("Settings"), G_SETTINGS
    elif oid in (6, 7):
        v.title, v.group = _("User sounds {first}–{last}").format(first=1 + (oid - 6) * 16, last=16 + (oid - 6) * 16), G_PRESETS
    elif oid == 8:
        v.title, v.group = _("FM6 patch bank"), G_PRESETS
    elif oid == 9:
        v.title, v.group = _("VA patches of the user sounds"), G_PRESETS
    elif oid in (10, 11):
        v.title, v.group = _("FM6 patches of the user sounds {first}–{last}").format(
            first=1 + (oid - 10) * 16, last=16 + (oid - 10) * 16), G_PRESETS
    elif oid in (12, 13):
        v.title, v.group = _("CZ-1 tones of the user sounds {first}–{last}").format(
            first=1 + (oid - 12) * 16, last=16 + (oid - 12) * 16), G_PRESETS
    elif 14 <= oid <= 21:
        v.title, v.group = _("CZ-1 bank {letter}").format(letter="ABCDEFGH"[oid - 14]), G_PRESETS
    elif oid == 22:
        v.title, v.group = _("FM TONE patches of the user sounds"), G_PRESETS
    elif oid == 23:
        v.title, v.group = _("Favorites"), G_PRESETS
    elif 40 <= oid <= 49:
        v.title, v.group = _("Loop slot {n}").format(n=oid - 39), G_PROJECTS
    elif 50 <= oid <= 62:
        first = (oid - 50) * 10 + 1
        v.title, v.group = _("User sounds {first}–{last}").format(first=f"{first:03d}", last=f"{min(first + 9, 128):03d}"), G_PRESETS
    v.format = "" if size else _("empty")
    return v


def describe(oid: int, data: bytes, crc: int, n_objects: int, firmware: str = "",
             sloop: bool = False, skiff: bool = False, jiant: bool = False, coral: bool = False,
             choral: bool = False) -> ObjectView:
    size = len(data)
    v = ObjectView(oid, _("Object {id}").format(id=oid), G_OTHER, size, crc, data)
    if coral:
        return _coral_object(v, oid, size)
    if choral:
        return _choral_object(v, oid, size)
    if oid == 0:
        v.title, v.group, v.slot = _("Running music"), G_MUSIC, None
        if size:
            v.project_info = project_info(data, firmware)
            v.format = v.project_info.get("format", "")
        return v
    if oid == 1:
        v.title, v.group = _("Settings"), G_SETTINGS
        v.format = decode_settings(data).get("Format", "")
        return v
    if jiant and _jiant_object(v, oid, size):
        return v
    slot = backup.project_slot(oid)
    if slot and not (sloop and oid > 5):
        v.slot = slot
        v.title = (_("Song section {letter}").format(letter='ABCD'[slot - 1]) if sloop
                   else _("Project {n}").format(n=slot))
        v.group = G_PROJECTS
        if size:
            v.project_info = project_info(data, firmware)
            v.format = v.project_info.get("format", "")
            v.detail = v.project_info.get("summary", "")
        return v
    if backup.is_sample(oid, jiant):
        v.title, v.group = _("Sample slot USR{n}").format(n=oid - 31), G_SAMPLES
        if size >= 512 and _u32(data, 0) == SMPF:
            v.format = "Sample"
            v.detail = ngettext("{n} zone, name {name}", "{n} zones, name {name}", data[6]).format(
                n=data[6], name=_name(data[8:16]) or '–')
        return v
    if sloop:
        from .sloopbk import title as sloop_title
        if oid in (6, 7, 8, 9, 10, 11):
            v.title = sloop_title(oid)
        v.group = G_PRESETS if oid in (6, 7, 8, 9) else G_SETTINGS
        if oid in (6, 7):
            first = 1 + (oid - 6) * 16
            pr = decode_up_bank(data, first, oid) if size else [PresetSlot(first + k, False, oid=oid) for k in range(16)]
            if pr is None:
                v.format = _("unknown format")
            else:
                for x in pr:
                    x.fw = "sloop"
                v.presets, v.format = pr, "UPB1" if size else _("empty")
                used = [x for x in pr if x.used]
                v.detail = _("{used} of {total} used").format(used=len(used), total=len(pr))
        elif oid == 10 and size:
            v.detail = _("learned MIDI assignments and channels")
        return v
    if oid == 8:
        v.title, v.group = _("Old FM6 bank (retired)"), G_OTHER
        return v
    if (skiff or jiant or n_objects == 13) and oid == 9:   # (Skiff, JIANT, Felucca 1.0.3+: editor_backup.c, up_fm6.c)
        v.title, v.group = _("FM6 patches of the user presets"), G_PRESETS
        return v
    if skiff and 16 <= oid < 32:                 # (Skiff: bank.c, 16 chunks of 4 patterns)
        first = (oid - 16) * 4 + 1
        v.title, v.group = _("Pattern bank, patterns {first}–{last}").format(first=first, last=first + 3), G_OTHER
        v.format = "" if size else _("empty")
        return v
    # preset objects
    v.group = G_PRESETS
    magic = _u32(data, 0) if size >= 4 else -1
    if (n_objects in (29, 57) and not skiff) or magic == UPB2:
        b = BANK_OF_OID.get(oid)
        if b is not None:
            lo, hi = bank_range(b)
            v.title = _("Preset bank {first}–{last} ({origin})").format(first=slot_label(lo), last=slot_label(hi),
                                                                         origin=bank_origin(b))
            if size:
                pr = decode_up_bank(data, lo, oid)
                if pr is None:
                    v.format = _("unknown format")
                    v.warnings.append(_("bank in an older format – the firmware converts it at start-up"))
                else:
                    v.presets, v.format = pr, "UPB2"
            else:
                v.presets = [PresetSlot(s, False, oid=oid) for s in range(lo, hi + 1)]
                v.format = _("empty")
    else:
        # older inventories
        if oid in (6, 7, 17, 18):
            first = {6: 1, 7: 17, 17: 33, 18: 49}[oid]
            v.title = _("User presets U{first:02d}–U{last:02d}").format(first=first, last=first + 15)
            pr = decode_up_bank(data, first, oid) if size else None
        elif 19 <= oid <= 22:
            first = {21: 1, 22: 17, 19: 33, 20: 49}[oid]
            v.title = _("FM6 voices F{first:03d}–F{last:03d}").format(first=first, last=first + 15)
            pr = decode_nfm1(data, first, oid) if size else None
            if size and pr is None:
                v.format = _("UPF6 (old FM6 voices)") if size in (3728, 4240) else ""
        elif 9 <= oid <= 16:
            first = (oid - 9) * 16 + 1
            v.title = _("CZ-1 bank {n} (tones {first}–{last})").format(n=oid - 8, first=first, last=first + 15)
            pr = decode_czcb(data, first, oid) if size else None
            if size >= 28 and _u32(data, 0) == CZCB:
                v.detail = _("bank name {name}").format(name=_name(data[12:28]))
        elif 23 <= oid <= 27:
            first = (oid - 23) * 26 + 1
            v.title = _("Prophet bank {n} (programs {first}–{last})").format(n=oid - 22, first=first, last=first + 25)
            pr = decode_p5u1(data, first, oid) if size else None
        else:
            v.title, v.group, pr = _("Object {id}").format(id=oid), G_OTHER, None
        if pr is not None:
            v.presets = pr
            v.format = v.format or {UPB1: "UPB1", UPB2: "UPB2", NFM1: "NFM1", CZCB: "CZCB", P5U1: "P5U1"}.get(magic, "")
        elif size and not v.format:
            v.format = _("unknown format")
    if v.presets:
        used = [p for p in v.presets if p.used]
        fav = sum(1 for p in used if p.favorite)
        v.detail = (v.detail + " · " if v.detail else "") + \
            _("{used} of {total} used").format(used=len(used), total=len(v.presets)) + \
            (", " + ngettext("{n} favourite", "{n} favourites", fav).format(n=fav) if fav else "")
        if any(p.used and not p.known_format for p in v.presets):
            v.warnings.append(_("contains presets in a format this firmware does not read (OLD FORMAT)"))
    return v


def inspect(source) -> Report:
    """Path / dict / JSON text / backup.Archive -> Report. Raises backup.BackupError for broken files."""
    path = None
    if isinstance(source, backup.Archive):
        arch = source
        return _from_sloop(arch.file, None) if arch.sloop else _from_x0x(arch, None) if arch.image else \
            _from_archive(arch, None)
    if isinstance(source, (str, Path)) and not (isinstance(source, str) and source.lstrip().startswith("{")):
        path = Path(source)
        raw = path.read_bytes()
        from . import x0xbk
        if x0xbk.is_image(raw):                    # (X0X's own .x0b file, LOOM-8's .loom8)
            return _from_x0x(backup.read(raw), path)
        try:
            source = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise backup.BackupError(_("Not a backup file (no JSON)"))
    if isinstance(source, (str, bytes)):
        try:
            source = json.loads(source)
        except ValueError:
            raise backup.BackupError(_("Not a backup file (no JSON)"))
    if isinstance(source, dict) and source.get("format") == "sloop-backup":
        return _from_sloop(source, path)
    arch = backup.read(source)
    return _from_x0x(arch, path) if arch.image else _from_archive(arch, path)


def _from_x0x(arch: backup.Archive, path: Path | None) -> Report:
    """X0X's or LOOM-8's image (x0xbk.py): its parts, restored whole; their formats are not decoded here (no project
    view)"""
    from . import x0xbk
    p = x0xbk.profile_of(arch.file)
    objs = []
    for o in arch.objects:
        v = ObjectView(o["id"], p.title(o["id"]), G_SETTINGS if p.settings(o["id"]) else
                       G_PRESETS if p.key == "loom-8" and o["id"] == 1 else G_PROJECTS, o["size"], o["crc"], o["bytes"])
        v.format = "" if o["size"] else _("empty")
        objs.append(v)
    rep = Report(arch.format, arch.firmware, arch.created, arch.companion, objs, True, True, path, arch)
    if x0xbk.parse(x0xbk.image_of(arch.file), p)[0] & 1:
        rep.warnings.append(_("{firmware} was playing during this backup: changes it had not saved yet are not in "
                              "it.").format(firmware=p.name))
    return rep


def _from_archive(arch: backup.Archive, path: Path | None) -> Report:
    n = len(arch.objects)
    ids = [o["id"] for o in arch.objects]
    skiff, jiant, coral = ids == SKIFF_IDS, backup.is_jiant(ids), backup.is_coralflower(ids)
    choral = backup.is_choralroot(ids)
    objs = [describe(o["id"], o["bytes"], o["crc"], n, arch.firmware, skiff=skiff, jiant=jiant, coral=coral,
                     choral=choral) for o in arch.objects]
    rep = Report(backup.FORMAT, arch.firmware, arch.created, arch.companion, objs, arch.known, True, path, arch)
    s = rep.obj(1) if not coral and not choral else None   # (CoralFlower's settings, 30, and ChoralRoot's PER5 are their
                                                           # own records: not decoded)
    if s:
        rep.settings = decode_settings(s.data)
    if not arch.known:
        rep.warnings.append(_("Inventory with {n} objects: unknown to the web editor (different firmware) – "
                              "the app shows it in a general way.").format(n=n))
    return rep


def _from_sloop(f: dict, path: Path | None) -> Report:
    """SLOOP backup: the same checks as backupObjects() in the SLOOP editor."""
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
    kind: str                 # K_NEW, K_CLEARED, K_CHANGED, K_ADDED, K_MISSING
    old_size: int = 0
    new_size: int = 0


@dataclass
class PresetChange:
    label: str
    kind: str                 # K_NEW, K_DELETED, K_CHANGED
    old: PresetSlot | None
    new: PresetSlot | None
    fields: list[str] = field(default_factory=list)

    def text(self) -> str:
        if self.kind == K_NEW:
            return _("{slot}: new “{name}” ({engine})").format(slot=self.label, name=self.new.name,
                                                              engine=self.new.engine_name)
        if self.kind == K_DELETED:
            return _("{slot}: “{name}” deleted").format(slot=self.label, name=self.old.name)
        bits = []
        if F_NAME in self.fields:
            bits.append(_("“{old}” → “{new}”").format(old=self.old.name, new=self.new.name))
        if F_ENGINE in self.fields:
            bits.append(f"{self.old.engine_name} → {self.new.engine_name}")
        if F_SOUND in self.fields:
            bits.append(_("sound changed"))
        if F_FAVORITE in self.fields:
            bits.append(_("favourite on") if self.new.favorite else _("favourite off"))
        return _("{slot} “{name}”: {changes}").format(slot=self.label, name=self.new.name, changes=", ".join(bits))


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
            return _("No differences – both states are the same.")
        parts = []
        pc = {k: sum(1 for p in self.presets if p.kind == k) for k in (K_NEW, K_CHANGED, K_DELETED)}
        if pc[K_CHANGED]:
            parts.append(ngettext("{n} preset changed", "{n} presets changed", pc[K_CHANGED]).format(n=pc[K_CHANGED]))
        if pc[K_NEW]:
            parts.append(ngettext("{n} preset new", "{n} presets new", pc[K_NEW]).format(n=pc[K_NEW]))
        if pc[K_DELETED]:
            parts.append(ngettext("{n} preset deleted", "{n} presets deleted", pc[K_DELETED]).format(n=pc[K_DELETED]))
        bank_changes = [c for c in self.objects if self._is_bank(c.id)]
        if bank_changes and not self.presets:
            parts.append(ngettext("{n} preset object changed", "{n} preset objects changed",
                                  len(bank_changes)).format(n=len(bank_changes)))
        for c in self.objects:
            if self._is_project(c.id):
                word = {K_NEW: _("{part} new"), K_CLEARED: _("{part} cleared"), K_CHANGED: _("{part} changed"),
                        K_ADDED: _("{part} new"), K_MISSING: _("{part} missing")}[c.kind]
                parts.append(word.format(part=c.title))
        live = next((c for c in self.objects if c.id == 0), None)
        if live:
            parts.append(_("running music changed"))
        parts.append(_("settings changed") if any(c.id == 1 for c in self.objects) else _("settings unchanged"))
        rest = [c for c in self.objects if c.id not in (0, 1) and not self._is_project(c.id) and not self._is_bank(c.id)]
        if rest:
            parts.append(ngettext("{n} more object changed", "{n} more objects changed", len(rest)).format(n=len(rest)))
        return ", ".join(parts)

    def _view(self, oid: int):
        return self.b.obj(oid) or self.a.obj(oid)

    def _is_project(self, oid: int) -> bool:
        o = self._view(oid)
        return bool(o is not None and o.slot)

    def _is_bank(self, oid: int) -> bool:
        """preset banks (also older user preset, FM6, CZ-1 and Prophet banks); not SLOOP's drum kits"""
        o = self._view(oid)
        return bool(o is not None and o.group == G_PRESETS and not (is_sloop(self.b) and oid == 9))


def diff(a: Report, b: Report) -> Diff:
    """Differences from a (old) to b (new)."""
    changes = []
    ids = [o.id for o in a.objects] + [o.id for o in b.objects if not a.obj(o.id)]
    for oid in ids:
        oa, ob = a.obj(oid), b.obj(oid)
        title = (ob or oa).title
        if oa is None:
            if ob.used:
                changes.append(Change(oid, title, K_ADDED, 0, ob.size))
            continue
        if ob is None:
            if oa.used:
                changes.append(Change(oid, title, K_MISSING, oa.size, 0))
            continue
        if oa.digest == ob.digest:
            continue
        kind = K_NEW if not oa.used else K_CLEARED if not ob.used else K_CHANGED
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
            pchanges.append(PresetChange(label, K_NEW, None, y))
        elif not yu:
            pchanges.append(PresetChange(label, K_DELETED, x, None))
        else:
            f = []
            if x.name != y.name:
                f.append(F_NAME)
            if x.engine != y.engine:
                f.append(F_ENGINE)
            # name/engine are inside the record – "sound" only if more has changed
            if x.digest != y.digest and _payload(x) != _payload(y):
                f.append(F_SOUND)
            if x.favorite != y.favorite:
                f.append(F_FAVORITE)
            if f:
                pchanges.append(PresetChange(label, K_CHANGED, x, y, f))
    return Diff(a, b, changes, pchanges)


def _payload(p: PresetSlot) -> bytes:
    r = p.raw
    if p.prefix == "U" and len(r) >= 16:
        return r[1:2] + r[3:4] + r[16:]            # without used/engine/name
    return r


# ------------------------------------------------- partial restore ---

@dataclass
class PlanItem:
    id: int
    title: str
    data: bytes
    action: str               # display text ("will be overwritten" …); K_SAME for skipped items
    detail: str = ""


@dataclass
class Plan:
    items: list[PlanItem]                  # in write order
    skipped: list[PlanItem]
    errors: list[str]
    notes: list[str]
    clear_extension: bool = False
    expect: dict = field(default_factory=dict)   # id -> (size, crc) on the device at planning time

    @property
    def ok(self) -> bool:
        return not self.errors and bool(self.items)

    def ids(self) -> list[int]:
        return [i.id for i in self.items]

    def html(self, device: str = "") -> str:
        if self.errors:
            return "<br>".join(self.errors)
        head = (_("This will now be written to <b>{device}</b>:").format(device=device) if device
                else _("This will now be written to <b>the FM-1</b>:"))
        lines = [head, "<ul>"]
        for it in self.items:
            lines.append(f"<li><b>{it.title}</b>: {it.action}" + (f"<br><small>{it.detail}</small>" if it.detail else "")
                         + "</li>")
        lines.append("</ul>")
        if self.skipped:
            lines.append(_("Already identical (not written): {parts}").format(
                parts=", ".join(i.title for i in self.skipped)) + "<br>")
        for n in self.notes:
            lines.append(n + "<br>")
        # (honest about what is checked: sizes and formats are asked before the first write; what the device can
        # only judge from the content, and SLOOP's "song is running", comes up while writing)
        lines.append(_("Everything else on the device stays unchanged. Before writing, the app asks the device "
                       "whether it takes each part; if it refuses one then, nothing is written. If the device only "
                       "finds a problem while writing (or the connection breaks), the restore stops there and the "
                       "message says which parts were already written."))
        return "".join(lines)


SELECTABLE_SKIP = {8}                     # Melodee: old, retired FM6 bank


def is_sloop(rep: Report) -> bool:
    return rep.format == "sloop-backup"


def skip_ids(rep: Report) -> set[int]:
    """Objects that cannot be restored individually (SLOOP: none)."""
    if is_sloop(rep) or backup.is_coralflower(o.id for o in rep.objects) or backup.is_choralroot(o.id for o in rep.objects):
        return set()                              # (CoralFlower, ChoralRoot: 8 is their FM6 patch bank, in use)
    return SELECTABLE_SKIP


def selectable(rep: Report) -> list[ObjectView]:
    """Objects that can be restored individually."""
    return [o for o in rep.objects if o.id not in skip_ids(rep)]


def plan_restore(rep: Report, ids: Iterable[int], device_manifest: list | None,
                 current: Report | None = None, device_inventory_len: int | None = None) -> Plan:
    """Checks a selection against the device (inventory from BACKUP_LIST) and orders it like the editor:
    projects/banks/samples, then settings, the running music last."""
    want = [i for i in dict.fromkeys(ids) if i not in skip_ids(rep)]
    errors, notes = [], []
    if not rep.restorable or rep.archive is None:
        return Plan([], [], [_("The app cannot restore this backup format.")], [])
    if is_sloop(rep):
        return _plan_sloop(rep, want, device_manifest, current)
    man = {o.id: o for o in (device_manifest or [])}
    n_dev = device_inventory_len if device_inventory_len is not None else len(man)
    r16 = refuse_16(rep, current.firmware if current is not None else "", n_dev if man else None)
    if r16:
        return Plan([], [], [r16], [])
    # old preset banks (not UPB2) only together – the firmware converts them as a group
    old_banks = [o.id for o in rep.objects if o.group == "Presets" and o.used and o.format != "UPB2"]
    if n_dev in (29, 57) and any(i in old_banks for i in want):
        missing = [i for i in old_banks if i not in want]
        if missing:
            want += missing
            notes.append(_("Preset banks from an older firmware can only be restored together – "
                           "the firmware converts them together at the next start. All banks were selected too."))
    clear_ext = len(rep.objects) == 28 and 28 in man and any(i in old_banks for i in want)
    live = man[0].size if 0 in man else 0
    items, skipped = [], []
    for oid in want:
        o = rep.obj(oid)
        if o is None:
            errors.append(_("Object {id} is not in the backup.").format(id=oid))
            continue
        if man and oid not in man:
            if o.used:
                errors.append(_("<b>{part}</b>: the firmware on the device does not know this object.").format(part=o.title))
            continue
        if man and n_dev != 57 and o.slot is not None and o.size > live and live and \
                not (live >= 19008 and o.size in (19084, 19092, 20224)):
            errors.append(_("<b>{part}</b> needs a firmware with a larger recording memory.").format(part=o.title))
            continue
        cur = man.get(oid)
        if cur is not None and cur.size == o.size and cur.crc == o.crc:
            skipped.append(PlanItem(oid, o.title, o.data, K_SAME))
            continue
        if not o.used:
            action = _("will be cleared") if (cur is None or cur.size) else _("stays empty")
        elif cur is not None and not cur.size:
            action = _("empty slot will be filled")
        else:
            action = _("will be overwritten")
        detail = ""
        if current is not None and o.presets:
            co = current.obj(oid)
            if co is not None:
                d = diff(Report(rep.format, "", "", {}, [co], True, True),
                         Report(rep.format, "", "", {}, [o], True, True))
                if d.presets:
                    txt = [p.text() for p in d.presets[:8]]
                    detail = "; ".join(txt) + (" … " + _("and {n} more").format(n=len(d.presets) - 8)
                                               if len(d.presets) > 8 else "")
        elif current is not None and o.slot is not None:
            co = current.obj(oid)
            if co is not None and co.used:
                detail = _("now on the device: {size} bytes ({format})").format(size=co.size,
                                                                                  format=co.format or _("project"))
        items.append(PlanItem(oid, o.title, o.data, action, detail))
    order = {oid: i for i, oid in enumerate([o.id for o in rep.objects[2:]] + [1, 0])}
    items.sort(key=lambda it: order.get(it.id, 999))
    if any(it.id == 0 for it in items):
        notes.append(_("The running music is replaced last – playback stops then."))
    if not items and not errors:
        errors.append(_("Nothing to do: everything selected is already exactly like this on the device.") if skipped
                      else _("Nothing selected."))
    expect = {oid: (m.size, m.crc) for oid, m in man.items() if oid in {it.id for it in items}}
    return Plan(items, skipped, errors, notes, clear_ext, expect)


def _plan_sloop(rep: Report, want: list[int], device_manifest: list | None, current: Report | None) -> Plan:
    """Partial restore of a SLOOP backup: order as in backupRestore in the SLOOP editor."""
    from . import sloopbk
    errors, notes = [], []
    man = {o.id: o for o in (device_manifest or [])}
    items, skipped = [], []
    for oid in want:
        o = rep.obj(oid)
        if o is None:
            errors.append(_("Object {id} is not in the backup.").format(id=oid))
            continue
        if oid in (0, 1) and not o.used:
            errors.append(_("<b>{part}</b> is empty in the backup – SLOOP does not accept that.").format(part=o.title))
            continue
        if man and oid not in man and oid not in (10, 11):
            # 8 (FM6 bank) and 9 (SYN kits) are listed by every firmware that knows them; 10/11 only if used
            if o.used:
                notes.append(_("{part}: unknown to the SLOOP version on the device – skipped.").format(part=o.title))
            continue
        cur = man.get(oid)
        if cur is not None and cur.size == o.size and cur.crc == o.crc:
            skipped.append(PlanItem(oid, o.title, o.data, K_SAME))
            continue
        if oid in (10, 11) and cur is None and not o.used:
            skipped.append(PlanItem(oid, o.title, o.data, K_SAME))
            continue
        if not o.used:
            action = _("will be cleared") if (cur is None or cur.size) else _("stays empty")
        elif cur is not None and not cur.size:
            action = _("empty slot will be filled")
        else:
            action = _("will be overwritten")
        detail = ""
        if current is not None and o.presets:
            co = current.obj(oid)
            if co is not None:
                d = diff(Report(rep.format, "", "", {}, [co], True, True), Report(rep.format, "", "", {}, [o], True, True))
                if d.presets:
                    detail = "; ".join(p.text() for p in d.presets[:8]) + \
                        (" … " + _("and {n} more").format(n=len(d.presets) - 8) if len(d.presets) > 8 else "")
        elif current is not None and (o.slot is not None or oid == 0):
            co = current.obj(oid)
            if co is not None and co.used:
                detail = _("now on the device: {size} bytes ({format})").format(size=co.size,
                                                                                  format=co.format or _("project"))
        items.append(PlanItem(oid, o.title, o.data, action, detail))
    order = {oid: i for i, oid in enumerate(sloopbk.RESTORE + sloopbk.SAMPLES)}
    items.sort(key=lambda it: order.get(it.id, 999))
    newest = max([0] + [sloopbk.proj_format(it.data) for it in items if it.id in (0, 2, 3, 4, 5)])
    dev_fmt = max([0] + [sloopbk.proj_format(current.obj(i).data) for i in (0,) if current and current.obj(i)])
    if dev_fmt and newest > dev_fmt:
        errors.append(_("The projects in the backup (format FUN{backup}) are newer than the SLOOP version on the "
                        "device reads (FUN{device}). Update SLOOP first.").format(backup=newest, device=dev_fmt))
    if items and any(it.id not in sloopbk.SAMPLES and it.id != 1 for it in items):
        notes.append(_("The song must be stopped on the FM-1 (STOP) – otherwise SLOOP refuses to write "
                       "(then nothing is replaced)."))
    if any(it.id == 0 for it in items):
        notes.append(_("The running project is loaded immediately."))
    if not items and not errors:
        errors.append(_("Nothing to do: everything selected is already exactly like this on the device.") if skipped
                      else _("Nothing selected."))
    expect = {oid: (m.size, m.crc) for oid, m in man.items() if oid in {it.id for it in items}}
    return Plan(items, skipped, errors, notes, False, expect)


# --------------------------------------------- device compatibility ---

def fw_name(key: str) -> str:
    """Display name for a firmware key ("melodee" → "Melodee")."""
    if not key:
        return _("unknown firmware")
    try:
        from .firmware import spec
        sp = spec(key)
        if sp:
            return sp.name
    except Exception:                               # noqa: BLE001
        pass
    return {"melodee": "Melodee", "sloop": "SLOOP", "skiff": "Skiff", "felucca": "Felucca"}.get(key, key)


def comparable(a: Report, b: Report) -> str:
    """"" if two states can sensibly be compared, otherwise the reason in plain language.
    Different firmware families (Melodee ↔ SLOOP) have different objects and formats –
    a diff would be nonsense ("Project 5 missing")."""
    if a.format != b.format:
        return _("Not comparable: one is a Melodee/Felucca backup, the other a SLOOP backup. "
                  "The two firmwares store projects, presets and settings completely differently.")
    ka, kb = a.firmware_key, b.firmware_key
    if ka and kb and ka != kb and _layout(a) != _layout(b):
        # (the key alone is no reason: the app named some firmwares differently in earlier versions, and a web
        # editor's file has no key at all – two states of one firmware keep the same things in the same places)
        return _("Not comparable: one state comes from <b>{a}</b>, the other from "
                 "<b>{b}</b>. Comparing only works within the same firmware.").format(a=fw_name(ka), b=fw_name(kb))
    return ""


@dataclass
class Compat:
    ok: bool
    reason: str
    possible: list[str] = field(default_factory=list)   # what still works
    allowed_ids: set[int] = field(default_factory=set)


def _layout(rep: Report) -> str:
    """where a backup's objects live (backup.layout_of; SLOOP's format: its fork's own layout or "sloop")"""
    from . import fwfamily
    if rep.format == backup.SLOOP_FORMAT:
        fam = fwfamily.by_info(rep.firmware)
        return "orbit" if rep.firmware_key == "orbit" else (fam.layout if fam is not None and fam.layout else "sloop")
    if rep.format != backup.FORMAT:
        return rep.format
    return backup.layout_of([o.id for o in rep.objects], rep.firmware or rep.companion.get("version_string", ""),
                            rep.companion.get("firmware", ""))


def _target_layout(key: str, version: str) -> str:
    """the layout of a Felucca-protocol device by its name alone (its inventory not known): "" = only its
    inventory tells (a plain "FELUCCA v…" may be Felucca, Skiff or a fork with objects of its own)"""
    from . import fwfamily
    fam = fwfamily.by_info(version) or fwfamily.by_key(key)
    if fam is not None and fam.layout:
        return fam.layout
    name = fwfamily.info_name(version) or (fam.name if fam is not None else key)
    if name.startswith("jiant"):
        return "jiant"
    return name if name in ("coralflower", "choralroot") else ""


_ELSE = [N_("transfer presets one by one via the preset library (once available)"),
         N_("export projects as MIDI and rebuild them there"),
         N_("or bring the target FM-1 to the same firmware")]


def compatibility(src: Report, target_fw_key: str, target_version: str, target_ids: list[int] | None,
                  proto: str = "") -> Compat:
    """Can the content of src go onto a device with this firmware? Decided as the restore itself decides
    (backup.match_reason, backup.layout_reason): by the backup protocol and by where the objects live – not by the
    firmware key the app wrote into the file (keys were renamed between app versions, a web editor's file has
    none). proto: the protocol the device answered (backup.device_inventory); otherwise taken from its name.
    target_ids: its inventory, if it was read – without it only what the names tell is refused here, and the
    restore checks the rest before it writes."""
    from . import fwfamily
    sk = src.firmware_key
    tk = target_fw_key or ""
    if tk.lower().startswith("jiant"):
        tk = "jiant"                                # (the catalog's "jiant-fm": one family with the twin's "jiant")
    if not src.restorable or src.archive is None:
        return Compat(False, _("The app cannot load this backup format."))
    if not tk and not target_version:
        return Compat(False, _("The firmware of one side is unknown – to be safe, nothing is copied."))
    arch = src.archive
    proto = proto or backup.guess_protocol(target_fw_key or "", target_version)
    why = ""
    if proto:
        tfam = fwfamily.by_info(target_version)
        tname = tfam.name if tfam is not None else next(
            (n for n in ("karma", "orbit") if n in (target_version or "").lower()), "")
        why = backup.match_reason(arch, proto, target_version, tname)
    elif arch.fm1open or arch.image:
        why = backup.match_reason(arch, backup.FELUCCA)           # (only its own firmware takes it)
    if not why and proto == backup.FELUCCA and not arch.sloop:
        if target_ids is not None:
            why = backup.layout_reason(arch, target_ids, target_version)
        else:
            d = _target_layout(target_fw_key or "", target_version)
            tfam = fwfamily.by_info(target_version) or fwfamily.by_key(target_fw_key or "")
            if d and not backup.layouts_fit(_layout(src), d, tfam.reads if tfam is not None else ()):
                why = backup.layout_text(arch, target_version or fw_name(tk))
    if not why and not target_version and not proto and sk and tk and sk != tk:
        # (nothing known of the target but a key the app does not know: the old rule, to be safe)
        why = _("The backup comes from <b>{source}</b>, the target runs <b>{target}</b>. "
                "Projects, presets and settings have different formats in different firmwares – "
                "the target would refuse them or read them wrongly. Nothing is written.").format(
                    source=fw_name(sk), target=fw_name(tk))
    if why:
        return Compat(False, why, [_(x) for x in _ELSE])
    ids = {o.id for o in src.objects}
    r16 = refuse_16(src, target_version, None if target_ids is None else len(target_ids))
    if r16:
        return Compat(False, r16, [_("bring the FM-1 to Melodee 1.6 or newer (Firmware+), then restore"),
                                   _("use a backup from before the update to Melodee 1.6"),
                                   _("export projects as MIDI (Projects › MIDI export)")])
    if target_ids is not None and is_sloop(src):
        target_ids = list(target_ids) + [i for i in (10, 11) if i not in target_ids]   # only used ones in the inventory
    if target_ids is not None:
        common = ids & set(target_ids)
        if not common:
            return Compat(False, _("The target knows none of the objects from the source."))
        note = ""
        if len(target_ids) != len(src.objects):
            note = " " + _("Inventory differs ({source} ↔ {target} objects, different version): "
                           "only common objects, the device checks each one first.").format(
                               source=len(src.objects), target=len(target_ids))
        from .firmware import version_key
        sv = _ver(src.firmware)
        tv = _ver(target_version)
        if sv and tv and version_key(sv) > version_key(tv):
            note += " " + _("The source is newer ({source} → {target}): the device may refuse newer projects.").format(
                source=sv, target=tv)
        return Compat(True, _("Same firmware family – copying possible.") + note, allowed_ids=common - skip_ids(src))
    return Compat(True, _("Same firmware family."), allowed_ids=ids - skip_ids(src))


def refuse_16(src: Report, target_version: str = "", target_inventory: int | None = None) -> str:
    """Backup from Melodee 1.6 onto an older Melodee? -> German explanation, otherwise ''. Older firmware rejects
    backups from 1.6 (RELEASE_1.6.0.md "Upgrading") – the app says so up front instead of a device error."""
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
    rows: list[tuple[str, str, str]]        # (property, A, B)
    only_a: list[str]
    only_b: list[str]
    both: list[str]


def compare_devices(a: Report | None, b: Report | None, fw_a: str = "", fw_b: str = "") -> DeviceComparison:
    def f(r, fallback):
        return (r.firmware if r else "") or fallback or "–"
    none = _("none")
    rows = [(_("Firmware"), f(a, fw_a), f(b, fw_b)),
            (_("State of"), a.created if a else _("no backup"), b.created if b else _("no backup")),
            (_("Projects used"), ", ".join(str(o.slot) for o in a.used_projects) or none if a else "–",
             ", ".join(str(o.slot) for o in b.used_projects) or none if b else "–"),
            (_("Presets used"), str(len(a.used_presets)) if a else "–", str(len(b.used_presets)) if b else "–")]
    na = {p.name for p in a.used_presets} if a else set()
    nb = {p.name for p in b.used_presets} if b else set()
    return DeviceComparison(rows, sorted(na - nb), sorted(nb - na), sorted(na & nb))
