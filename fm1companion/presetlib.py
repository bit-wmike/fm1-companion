# SPDX-License-Identifier: GPL-3.0-only
"""Melodee user presets (shared library, command 80, Melodee 1.5+) and the local preset library.

Protocol: web/EDITOR_PROTOCOL.md "Shared user library (command 80)", rebuilt like the editor
(web/editor.html ``sharedBank``) and the firmware (firmware/src/editor_library.c):

  Request  80: op slotLo slotHi args…      Reply: op slotLo slotHi rc …   (rc 0 ok, 1 invalid/empty, 2 rejected/flash)
  op 0 list (count 1..16) · 1 read · 2 write · 3 save track · 4 load onto track · 5 delete · 6 rename

Values are v14 with offset 8192 (like firmware ed_rv / editor v14enc) – not the two's complement from protocol.py.
Older firmware without command 80 (Melodee < 1.5: UP_* 16–21, 64 slots) is detected and clearly rejected.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .i18n import _, ngettext

CMD_LIBRARY = 80
CMD_DESC = 5
CMD_FAV_GET = 37
CMD_FAV_SET = 38
CMD_FM6_PUT = 69
SLOTS = 384
FLASH_TIMEOUT = 2.5
FORMAT = "fm1preset"
FORMAT_VERSION = 1

NATIVE_SIZE = {1: 128, 2: 144, 3: 138, 4: 54}
NATIVE_ENGINE = {1: 12, 2: 15, 3: 19, 4: 10}
NATIVE_NAMES = {0: _("Parameters"), 1: _("DX7 voice"), 2: _("CZ-1 tone"), 3: _("Prophet program"), 4: _("Drum patch")}
# fixed engine numbers (EDITOR_PROTOCOL.md "engine byte"; core.h) – in case INFO provides no names
ENGINE_NAMES = {0: "ANALOG", 1: "DIGITAL", 2: "PHASE", 3: "SID", 4: "SAMPLE", 5: "VOICE", 6: "TRIO", 7: "WHEEL",
                8: "GRAIN", 9: "PHYS", 10: "DRUM", 11: "NOISE", 12: "FM6", 13: "SLICE", 14: "OBXF", 15: "CZ-1",
                19: "PROPHET", 21: "MIDI Track"}
ENGINE_ORDER = ["PROPHET", "ANALOG", "FM6", "DIGITAL", "PHASE", "CZ-1", "SID", "VOICE", "NOISE", "DRUM", "MIDI Track"]


class PresetError(Exception):
    pass


def engine_display_name(e: int, engines=None) -> str:
    """uniform display name of an engine number for all pages (presets, FM-1, voices).
    engines: name list from INFO (info.engines), if present – only used if it provides a
    real name; otherwise the fixed names from the protocol (e.g. 12 -> "FM6")."""
    try:
        e = int(e)
    except (TypeError, ValueError):
        return str(e)
    if engines and len(engines) > 12 and 0 <= e < len(engines):      # short lists: numbers assigned differently
        n = str(engines[e] or "").strip()
        if n and n != "-" and not n.lower().startswith("engine"):
            return n
    return ENGINE_NAMES.get(e, f"Engine {e}")


# ------------------------------------------------------------- encoding ---

def v14enc(v: int) -> list[int]:
    u = max(-8192, min(8191, int(round(v)))) + 8192
    return [u & 0x7F, (u >> 7) & 0x7F]


def v14dec(lo: int, hi: int) -> int:
    return (lo | hi << 7) - 8192


def str_z(s: str) -> list[int]:
    return [ord(c) & 0x7F for c in s] + [0]


def pack7(raw) -> list[int]:
    """Editor czBkPack: every 7 bytes, a mask byte with the top bits up front."""
    out = []
    raw = list(raw)
    for i in range(0, len(raw), 7):
        g = raw[i:i + 7]
        out.append(sum(((x >> 7) & 1) << j for j, x in enumerate(g)))
        out += [x & 127 for x in g]
    return out


def unpack7(a) -> list[int]:
    b, i = [], 0
    a = list(a)
    while i < len(a):
        m = a[i]
        i += 1
        n = min(7, len(a) - i)
        if m >> n:
            raise PresetError(_("invalid packed bytes"))
        for j in range(n):
            b.append(a[i] | ((m >> j) & 1) << 7)
            i += 1
    return b


def clean_name(s: str, limit: int) -> str:
    return re.sub(r"[^ -~]", " ", str(s or "")).strip()[:limit]


# umlauts and ß are transliterated instead of dropped (the FM-1 only accepts ASCII 32–126)
_TRANSLIT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss",
                           "é": "e", "è": "e", "à": "a", "–": "-", "—": "-", "„": '"', "“": '"', "”": '"', "’": "'"})


def name_limit(engine: int) -> int:
    """12 characters for normal presets, 20 for native voices (FM6, CZ-1, Prophet) – like the firmware (op 2/6)."""
    return 20 if engine in (NATIVE_ENGINE[1], NATIVE_ENGINE[2], NATIVE_ENGINE[3]) else 12


def fit_name(s: str, limit: int) -> tuple[str, str]:
    """Adapt names to the FM-1 -> (name, explanation). The explanation is empty if nothing was changed;
    otherwise it says in one sentence what was changed and why (umlauts transliterated, special characters removed,
    shortened to limit characters)."""
    raw = str(s or "")
    t = raw.translate(_TRANSLIT)
    notes = []
    if t != raw:
        notes.append(_("umlauts/special characters transliterated"))
    t2 = re.sub(r"[^ -~]", "", t)
    if t2 != t:
        notes.append(_("characters outside ASCII removed"))
    t2 = re.sub(r"\s+", " ", t2).strip()
    if len(t2) > limit:
        notes.append(_("shortened to {n} characters").format(n=limit))
        t2 = t2[:limit].rstrip()
    why = ""
    if notes:
        why = _("The FM-1 stores names only with letters, digits and simple characters (at most {n}). "
                "Therefore: {changes} → “{name}”.").format(n=limit, changes=", ".join(notes), name=t2)
    return t2, why


class _R:
    def __init__(self, a):
        self.a, self.i = list(a), 0

    def b(self):
        if self.i >= len(self.a):
            raise PresetError(_("Answer too short"))
        self.i += 1
        return self.a[self.i - 1]

    def v(self):
        lo = self.b()
        return v14dec(lo, self.b())

    def s(self):
        j = self.i
        while j < len(self.a) and self.a[j]:
            j += 1
        if j >= len(self.a):
            raise PresetError(_("Text without end"))
        t = bytes(self.a[self.i:j]).decode("latin-1")
        self.i = j + 1
        return t


# ---------------------------------------------------------- capabilities ---

@dataclass
class Caps:
    shared: bool = False           # INFO tag "U" (55 01 lo hi) or command 80 answers
    total: int = SLOTS
    legacy: bool = False           # Melodee < 1.5 (UP_* 16–21)
    p_count: int = 0
    p_e0: int = 0
    ntrk: int = 4
    engines: list = field(default_factory=list)
    general_ns: int | None = None  # favourites category of the user slots (FAV_GET/SET)
    favorites: bool = False
    version: str = ""

    def engine_name(self, e: int) -> str:
        return engine_display_name(e, self.engines)


def parse_caps(info) -> Caps:
    """read the capabilities from protocol.Info (with .raw) like the editor does (parse[CMD.INFO])."""
    c = Caps(p_count=info.p_count, p_e0=info.p_e0, ntrk=info.ntrk or 1, engines=list(info.engines),
             version=info.version)
    raw = list(info.raw or [])
    try:
        r = _R(raw)
        r.s()
        ne = r.b()
        for _i in range(4):
            r.b()
        for _i in range(ne):
            r.s()
        if r.i < len(raw):
            r.b()                                  # NTRK
        tr = raw[r.i:]
    except PresetError:
        tr = []
    ui_caps = tr[3] & 127 if len(tr) >= 4 and tr[0] in (0, 16) and tr[1] == 0x55 and tr[2] == 1 else 0
    for i in range(25, len(tr) - 3):
        if tr[i] == 0x55 and tr[i + 1] == 1:
            c.shared, c.total = True, tr[i + 2] | tr[i + 3] << 7
            break
    for i in range(25, len(tr) - 6):
        if tr[i] == 0x35 and tr[i + 1] == 1 and tr[i + 2] == 19:
            c.general_ns = tr[i + 3]
            break
    c.favorites = bool(ui_caps & 8)
    if c.general_ns is None:
        c.general_ns = info.p_count and len(info.engines)
    return c


def probe(link, info) -> Caps:
    """Determine capabilities; if INFO says nothing, ask with a harmless list request (op 0, 1 slot)."""
    c = parse_caps(info)
    if not c.shared:
        try:
            a = link.request(CMD_LIBRARY, [0, 0, 0, 1], timeout=1.0, retries=0)
            if len(a) >= 7 and a[0] == 0 and a[3] == 0:
                c.shared, c.total = True, a[5] | a[6] << 7
        except Exception:                                  # noqa: BLE001 – no reply = no command 80
            pass
    if not c.shared:
        try:
            a = link.request(16, [0, 1], timeout=0.8, retries=0)     # UP_LIST
            c.legacy = len(a) >= 3
        except Exception:                                  # noqa: BLE001
            pass
    if c.shared and not c.favorites:
        try:                                               # FAV_GET is read-only
            a = link.request(CMD_FAV_GET, [c.general_ns or 0, *v14enc(0), 1], timeout=0.6, retries=0)
            c.favorites = bool(a) and a[0] == 0
        except Exception:                                  # noqa: BLE001
            pass
    return c


def refusal_text(c: Caps) -> str:
    if c.legacy:
        return _("This Melodee version still has the old preset storage (64 slots, before 1.5). The suite's preset "
                 "library works with the shared library from Melodee 1.5 – please update Melodee "
                 "(make a backup first). Until then: edit presets in the editor under “Library”.")
    return _("This firmware has no shared preset library (command 80, available from Melodee 1.5). "
             "You can still send DX7 voices as SysEx (Presets › FM Voices).")


# --------------------------------------------------------------- preset ---

@dataclass
class Preset:
    name: str = ""
    engine: int = 0
    engine_name: str = ""
    params: list = field(default_factory=list)       # P_COUNT values in protocol order
    legacy: list = field(default_factory=lambda: [0] * 32)
    native_kind: int = 0
    native: bytes = b""
    favorite: bool = False
    p_count: int = 0
    p_e0: int = 0
    tags: list = field(default_factory=list)
    device: str = ""
    firmware: str = ""
    read_date: str = ""
    slot: int | None = None                          # slot of origin (0-based)
    used: bool = True
    path: Path | None = None

    @property
    def label(self) -> str:
        return f"U{self.slot + 1:03d}" if self.slot is not None else ""

    def content_hash(self) -> str:
        h = hashlib.sha256()
        h.update(json.dumps([self.engine, self.name, list(self.params), self.native_kind], separators=(",", ":")).encode())
        h.update(bytes(self.native))
        return h.hexdigest()[:16]

    def same_sound(self, other: "Preset") -> bool:
        return (self.engine, list(self.params), self.native_kind, bytes(self.native)) == \
            (other.engine, list(other.params), other.native_kind, bytes(other.native))

    def to_json(self) -> dict:
        return {"format": FORMAT, "version": FORMAT_VERSION, "name": self.name, "engine": self.engine,
                "engine_name": self.engine_name, "params": list(self.params), "p_count": self.p_count or len(self.params),
                "p_e0": self.p_e0, "legacy": list(self.legacy), "native_kind": self.native_kind,
                "native": base64.b64encode(bytes(self.native)).decode(), "favorite": self.favorite,
                "tags": list(self.tags), "device": self.device, "firmware": self.firmware,
                "read_date": self.read_date, "slot": self.slot, "hash": self.content_hash()}

    @classmethod
    def from_json(cls, d: dict) -> "Preset":
        if d.get("format") != FORMAT:
            raise PresetError(_("not a .fm1preset file"))
        if int(d.get("version", 1)) > FORMAT_VERSION:
            raise PresetError(_("File from a newer version of the suite"))
        p = cls(name=str(d.get("name", "")), engine=int(d.get("engine", 0)), engine_name=str(d.get("engine_name", "")),
                params=[int(x) for x in d.get("params", [])], legacy=[int(x) & 127 for x in d.get("legacy", [0] * 32)][:32],
                native_kind=int(d.get("native_kind", 0)), native=base64.b64decode(d.get("native", "") or ""),
                favorite=bool(d.get("favorite")), p_count=int(d.get("p_count", 0)), p_e0=int(d.get("p_e0", 0)),
                tags=[str(t) for t in d.get("tags", [])], device=str(d.get("device", "")),
                firmware=str(d.get("firmware", "")), read_date=str(d.get("read_date", "")), slot=d.get("slot"))
        p.validate()
        return p

    def validate(self):
        if self.native_kind not in NATIVE_SIZE and self.native_kind != 0:
            raise PresetError(_("unknown kind {kind}").format(kind=self.native_kind))
        if self.native_kind:
            if len(self.native) != NATIVE_SIZE[self.native_kind]:
                raise PresetError(_("Native data has the wrong length"))
            if self.engine != NATIVE_ENGINE[self.native_kind]:
                raise PresetError(_("Native data does not match the engine"))
        if any(not -64 <= v <= 127 for v in self.params):
            raise PresetError(_("Parameter value outside −64…127"))

    @property
    def kind_text(self) -> str:
        return NATIVE_NAMES.get(self.native_kind, "?")

    def dx7_voice(self):
        """for FM6 presets the DX7 voice (VCED), otherwise None."""
        if self.native_kind == 1:
            from . import dx7
            return dx7.unpack(self.native)
        return None


def preset_from_voice(vced, name: str = "", p_count: int = 0) -> Preset:
    """DX7 voice -> FM6 preset (native kind 1). The device sets the Felucca control values (PUT bit 6)."""
    from . import dx7
    vm = bytes(dx7.pack(dx7.sanitize(vced)))
    nm = clean_name(name or dx7.name(vced), 20) or "PATCH"
    return Preset(name=nm, engine=12, engine_name="FM6", params=[], native_kind=1, native=vm,
                  p_count=p_count, tags=["DX7"], read_date=_now())


def _now() -> str:
    return dt.datetime.now().replace(microsecond=0).isoformat()


# ------------------------------------------------------------- device ---

class SharedBank:
    """Command 80 over a link (always call from a background thread with exclusive access)."""

    def __init__(self, link, caps: Caps):
        self.link, self.caps = link, caps

    @staticmethod
    def request(op: int, slot: int, *args) -> list[int]:
        if not isinstance(slot, int) or not 0 <= slot < SLOTS:
            raise PresetError(_("invalid preset slot"))
        return [op, slot & 127, slot >> 7, *args]

    @staticmethod
    def check(a, op: int, slot: int) -> _R:
        if len(a) < 4 or a[0] != op or (a[1] | a[2] << 7) != slot:
            raise PresetError(_("unexpected answer from the FM-1"))
        if a[3]:
            label = f"U{slot + 1:03d}"
            raise PresetError({1: _("{slot}: invalid or empty (rc 1)").format(slot=label),
                               2: _("{slot}: refused by the FM-1 or memory error (rc 2) – "
                                    "is playback still running?").format(slot=label)}.get(
                a[3], _("Error {code}").format(code=a[3])))
        return _R(a[4:])

    def _rq(self, args, flash=False):
        return self.link.request(CMD_LIBRARY, args, timeout=FLASH_TIMEOUT if flash else 1.5, retries=0 if flash else 1)

    def list(self, start=0, count=16) -> tuple[int, list[dict]]:
        a = self._rq(self.request(0, start, count))
        r = self.check(a, 0, start)
        n, total = r.b(), r.b() | r.b() << 7
        if not n or n > count or start + n > total:
            raise PresetError(_("invalid list answer"))
        out = []
        for i in range(n):
            used, eng, old = r.b(), r.b(), r.b()
            out.append({"slot": start + i, "used": bool(used & 1), "favorite": bool(used & 2), "engine": eng,
                        "old": old, "name": r.s()})
        return total, out

    def list_all(self, progress=None, cancel=None) -> list[dict]:
        out, total = [], 1
        while len(out) < total:
            if cancel and cancel():
                raise PresetError(_("cancelled"))
            total, chunk = self.list(len(out), 16)
            out += chunk
            if progress:
                progress(len(out), total)
        return out

    def get(self, slot: int) -> Preset:
        a = self._rq(self.request(1, slot))
        r = self.check(a, 1, slot)
        used, eng, nm = r.b(), r.b(), r.s()
        params = [r.v() for _i in range(self.caps.p_count)]
        legacy = [r.b() for _i in range(32)]
        kind = r.b()
        native = b""
        if kind:
            size = r.b() | r.b() << 7
            raw = unpack7(r.a[r.i:])
            if kind not in NATIVE_SIZE or eng != NATIVE_ENGINE[kind] or len(raw) != size or size != NATIVE_SIZE[kind]:
                raise PresetError(_("{slot}: invalid native data").format(slot=f"U{slot + 1:03d}"))
            native = bytes(raw)
            if kind == 1:                                   # full DX7 name if the short name is its beginning
                from . import dx7
                full = dx7.name(dx7.unpack(native))
                if nm.strip() == full[:12].strip():
                    nm = full
        return Preset(name=nm, engine=eng, engine_name=self.caps.engine_name(eng) if used else "", params=params,
                      legacy=legacy, native_kind=kind, native=native, p_count=self.caps.p_count,
                      p_e0=self.caps.p_e0, firmware=self.caps.version, read_date=_now(), slot=slot, used=bool(used & 1))

    def put_args(self, p: Preset, defaults: bool | None = None) -> list[int]:
        """Arguments after the header – byte-exact like the editor's sharedBank.put."""
        kind = p.native_kind
        if kind and p.engine != NATIVE_ENGINE[kind]:
            raise PresetError(_("Native data does not match the engine"))
        if not kind and p.engine in (12, 19):
            raise PresetError(_("FM6 and Prophet presets need their voice data"))
        name = clean_name(p.name or "PATCH", 20 if kind else 12) or "PATCH"
        values = map_params(p, self.caps)
        if defaults is None:
            defaults = not p.params
        native_name = ""
        if kind == 1:
            from . import dx7
            native_name = dx7.name(dx7.unpack(p.native))
        preserve = bool(kind) and kind < 4 and native_name and name == clean_name(native_name, 20)
        args = [p.engine & 127, *str_z(name), *[b for v in values for b in v14enc(v)], *[x & 127 for x in (list(p.legacy) + [0] * 32)[:32]],
                kind | (64 if kind and defaults else 0) | (32 if preserve else 0)]
        if kind:
            raw = bytes(p.native)
            args += [len(raw) & 127, len(raw) >> 7, *pack7(raw)]
        return args

    def put(self, slot: int, p: Preset, defaults: bool | None = None):
        p.validate()
        a = self._rq(self.request(2, slot, *self.put_args(p, defaults)), flash=True)
        self.check(a, 2, slot)

    def store(self, slot: int, track: int, name: str = ""):
        nm = clean_name(name, 20)
        self.check(self._rq(self.request(3, slot, track, *[ord(c) for c in nm]), flash=True), 3, slot)

    def load(self, slot: int, track: int):
        self.check(self._rq(self.request(4, slot, track)), 4, slot)

    def erase(self, slot: int):
        self.check(self._rq(self.request(5, slot), flash=True), 5, slot)

    def rename(self, slot: int, name: str):
        nm = clean_name(name, 20)
        if not nm:
            raise PresetError(_("The name must not be empty"))
        self.check(self._rq(self.request(6, slot, *[ord(c) for c in nm]), flash=True), 6, slot)

    def set_favorite(self, slot: int, on: bool) -> int:
        """-> rc: 0 saved, 3 only in RAM, 4 will be saved after STOP."""
        a = self.link.request(CMD_FAV_SET, [self.caps.general_ns or 0, *v14enc(slot), 1 if on else 0], timeout=1.5, retries=0)
        rc = a[0] if a else 1
        if rc not in (0, 3, 4):
            raise PresetError(_("Favourite refused (rc {rc})").format(rc=rc))
        return rc

    def voice_to_track(self, track: int, vmem: bytes):
        """FM6_PUT target 0: voice live onto a track (not saved)."""
        a = self.link.request(CMD_FM6_PUT, [0, track, *[x & 127 for x in vmem]], timeout=1.5, retries=0)
        if len(a) < 3 or a[2]:
            raise PresetError(_("The voice was not accepted"))

    def write_with_undo(self, slot: int, p: Preset, defaults: bool | None = None) -> Preset:
        """read first (for undo), then write, then read again for verification."""
        old = self.get(slot)
        self.put(slot, p, defaults)
        return old

    def restore(self, slot: int, old: Preset):
        """Undo: write back the old content (or clear the slot if it was empty)."""
        if not old.used:
            self.erase(slot)
        else:
            self.put(slot, old, defaults=False)


def map_params(p: Preset, caps: Caps) -> list[int]:
    """Map values onto the device's P_COUNT (like the firmware: leading values in order,
    the last 8 are P_E0..P_E7; missing = 0) and keep them within −64…127."""
    n, pe0 = caps.p_count, caps.p_e0 or max(0, caps.p_count - 8)
    src = list(p.params)
    if not src:
        return [0] * n
    if len(src) == n:
        out = src
    else:
        out = [0] * n
        common = max(0, len(src) - 8)
        for i in range(min(common, pe0)):
            out[i] = src[i]
        for k in range(8):
            if common + k < len(src) and pe0 + k < n:
                out[pe0 + k] = src[common + k]
    return [max(-64, min(127, int(v))) for v in out]


# ---------------------------------------------------------- library ---

def safe_file(s: str) -> str:
    return re.sub(r"[^\w .,+()-]", "_", s).strip() or "Preset"


class Library:
    """~/FM-1/Presets/<device>/U001 Name.fm1preset (+ own folders). Duplicates by content hash."""

    def __init__(self, root: Path):
        self.root = Path(root)
        try:                                   # folder only when needed; read-only -> read only
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    def all(self) -> list[Preset]:
        out = []
        try:
            files = sorted(self.root.rglob("*.fm1preset")) if self.root.is_dir() else []
        except OSError:
            files = []
        for f in files:
            try:
                p = Preset.from_json(json.loads(f.read_text("utf-8")))
                p.path = f
                out.append(p)
            except (OSError, ValueError, PresetError, KeyError, TypeError):
                continue
        return out

    def hashes(self) -> dict[str, Path]:
        return {p.content_hash(): p.path for p in self.all()}

    def save(self, p: Preset, folder: str = "", known: dict | None = None) -> tuple[Path, bool]:
        """-> (path, new?). The same content is not stored twice."""
        known = self.hashes() if known is None else known
        h = p.content_hash()
        if h in known and known[h] and Path(known[h]).exists():
            return Path(known[h]), False
        d = self.root / safe_file(folder) if folder else self.root
        d.mkdir(parents=True, exist_ok=True)
        stem = (f"{p.label} " if p.slot is not None else "") + safe_file(p.name or "Preset")
        f = d / f"{stem}.fm1preset"
        k = 2
        while f.exists():
            f = d / f"{stem} ({k}).fm1preset"
            k += 1
        self.write(p, f)
        known[h] = f
        return f, True

    @staticmethod
    def write(p: Preset, f: Path):
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(p.to_json(), indent=1, ensure_ascii=False), "utf-8")
        tmp.replace(f)
        p.path = f

    def update(self, p: Preset):
        if p.path:
            self.write(p, p.path)

    def delete(self, p: Preset):
        if p.path and p.path.exists():
            p.path.unlink()

    def rename(self, p: Preset, name: str) -> Path:
        """Rename a preset in the library (name in the file and file name); nothing changes on the FM-1."""
        name = clean_name(name, 20)
        if not name:
            raise PresetError(_("The name must not be empty"))
        old = p.path
        if not old:
            raise PresetError(_("This preset does not exist as a file"))
        stem = (f"{p.label} " if p.slot is not None else "") + safe_file(name)
        f = old.with_name(f"{stem}.fm1preset")
        k = 2
        while f.exists() and f != old:
            f = old.with_name(f"{stem} ({k}).fm1preset")
            k += 1
        before = p.name
        p.name = name
        try:
            self.write(p, f)
        except OSError:
            p.name, p.path = before, old         # nothing changed – not even in memory
            raise
        if f != old and old.exists():
            old.unlink()
        return f

    def import_file(self, f: Path, folder: str = "Importiert") -> list[Path]:
        """.fm1preset, set (.zip), editor library (felucca-library .json) or DX7 SysEx (.syx)."""
        f = Path(f)
        data = f.read_bytes()
        presets = read_any(data, f.name)
        known = self.hashes()
        return [self.save(p, folder, known)[0] for p in presets]


def read_any(data: bytes, filename: str = "") -> list[Preset]:
    """like _read_any, but every read error is a PresetError with a German explanation."""
    try:
        return _read_any(data, filename)
    except PresetError:
        raise
    except zipfile.BadZipFile as e:
        raise PresetError(_("The .zip file is damaged or not a zip file")) from e
    except UnicodeDecodeError as e:
        raise PresetError(_("The file is not a text file (not .fm1preset/.json)")) from e
    except (ValueError, TypeError, KeyError, AttributeError) as e:
        raise PresetError(_("The file is damaged or not a known preset format "
                            "(.fm1preset, .zip set, editor .json, DX7 .syx)")) from e


def _read_any(data: bytes, filename: str = "") -> list[Preset]:
    low = filename.lower()
    if low.endswith(".zip") or data[:2] == b"PK":
        out = []
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for n in z.namelist():
                if n.lower().endswith(".fm1preset"):
                    out.append(Preset.from_json(json.loads(z.read(n).decode("utf-8"))))
        return out
    if low.endswith(".syx") or data[:1] == b"\xf0":
        from . import dx7
        return [preset_from_voice(v) for v in dx7.parse_syx(data).voices]
    d = json.loads(data.decode("utf-8"))
    if d.get("format") == FORMAT:
        return [Preset.from_json(d)]
    if d.get("format") in ("felucca-library", "felucca-patch"):
        return from_felucca(d)
    raise PresetError(_("Unknown file format"))


def export_zip(presets: list[Preset], target: Path):
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        seen = set()
        for p in presets:
            n = (f"{p.label} " if p.slot is not None else "") + safe_file(p.name)
            base, k = n, 2
            while n in seen:
                n = f"{base} ({k})"
                k += 1
            seen.add(n)
            z.writestr(n + ".fm1preset", json.dumps(p.to_json(), indent=1, ensure_ascii=False))


def to_felucca(presets: list[Preset], caps_engines=None, firmware: str = "") -> dict:
    """Library file of the Melodee web editor (felucca-library v1, kind "bank")."""
    pats = []
    pc = max((len(p.params) for p in presets), default=0)
    for p in presets:
        o = {"name": p.name, "engine": p.engine, "engineName": p.engine_name or engine_display_name(p.engine),
             "params": list(p.params), "pattern": None, "tags": list(p.tags), "created": p.read_date or _now(),
             "modified": p.read_date or _now()}
        key = {1: "fm6", 2: "cz", 3: "p5", 4: "drum"}.get(p.native_kind)
        if key:
            o[key] = list(p.native)
        if p.slot is not None:
            o["slot"] = p.slot
        pats.append(o)
    return {"format": "felucca-library", "version": 1, "kind": "bank", "firmware": firmware, "pCount": pc,
            "pE0": presets[0].p_e0 if presets else None, "paramLabels": [], "engines": list(caps_engines or []),
            "exported": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "patches": pats}


def from_felucca(d: dict) -> list[Preset]:
    out = []
    for c in d.get("patches") or ([d.get("patch")] if d.get("patch") else []):
        params = [0 if v is None else int(v) for v in (c.get("params") or c.get("p") or [])]
        p = Preset(name=str(c.get("name", "PATCH")), engine=int(c.get("engine", 0)),
                   engine_name=str(c.get("engineName", "")), params=params, p_count=len(params),
                   p_e0=int(d.get("pE0") or max(0, len(params) - 8)), tags=list(c.get("tags") or []),
                   firmware=str(d.get("firmware", "")), read_date=str(c.get("modified", "")))
        for kind, key in ((1, "fm6"), (2, "cz"), (3, "p5"), (4, "drum")):
            if c.get(key) is not None:
                p.native_kind, p.native = kind, bytes(int(x) & 255 for x in c[key])
        try:
            p.validate()
        except PresetError:
            continue
        out.append(p)
    return out


def diff(a: Preset, b: Preset) -> list[str]:
    """short list of the differences (device a / library b)."""
    out = []
    if a.name != b.name:
        out.append(f"Name: {a.name!r} ↔ {b.name!r}")
    if a.engine != b.engine:
        out.append(f"Engine: {a.engine_name or engine_display_name(a.engine)} ↔ {b.engine_name or engine_display_name(b.engine)}")
    n = 0
    for i, (x, y) in enumerate(zip(a.params, b.params)):
        if x != y:
            n += 1
            if n <= 12:
                out.append(_("Parameter {i}: {a} ↔ {b}").format(i=i, a=x, b=y))
    if n > 12:
        out.append(ngettext("… and {n} more parameter", "… and {n} more parameters", n - 12).format(n=n - 12))
    if len(a.params) != len(b.params):
        out.append(_("Number of parameters: {a} ↔ {b}").format(a=len(a.params), b=len(b.params)))
    if (a.native_kind, bytes(a.native)) != (b.native_kind, bytes(b.native)):
        out.append(_("Native data ({a} ↔ {b}) differs").format(a=a.kind_text, b=b.kind_text))
    return out or [_("identical")]
