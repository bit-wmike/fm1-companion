# SPDX-License-Identifier: GPL-3.0-only
"""Sample-Bibliothek, Bearbeitung, Kit-Baukasten und Upload in die Slots.

* Bibliothek: durchsucht beliebig viele Ordner (z. B. deine Pocket-Operator-
  Samples), merkt sich Länge, Format, Tags, Favoriten und Notizen in einer
  SQLite-Datenbank. Neu einlesen geht inkrementell (nur geänderte Dateien).
* Bearbeitung: Stille abschneiden, Start/Ende, Normalisieren, Lautstärke,
  Fade, Umkehren – nichts davon verändert die Originaldatei.
* Kits: bis zu 16 Sounds pro Slot, jeder auf eigener Taste (Drum-Kit) oder über
  die Tastatur verteilt (Instrument). Ein Slot fasst 80 KiB, das sind etwa
  7,4 s Audio insgesamt (22 050 Hz, IMA-ADPCM) – die App zeigt das Budget live.
* Upload: Befehle 11–15, dasselbe Format wie Felucca, Skiff und SLOOP.
  (Melodee ab 1.5 hat keine Sample-Slots mehr.)
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import struct
import subprocess
import tempfile
import threading
import time
import wave
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable, Iterable

from .config import DATA_DIR
from .protocol import Cmd, pack7, parse_smp_info, SampleSlot
from .vendor import sampleio as sio

try:
    import numpy as np
except ImportError:          # läuft auch ohne, nur langsamer
    np = None

AUDIO_EXT = {".wav", ".wave", ".aif", ".aiff", ".flac", ".mp3", ".ogg", ".opus", ".m4a"}
DB_FILE = DATA_DIR / "samples.sqlite"
SLOT_RATE = sio.SLOT_RATE
SLOT_BYTES = sio.SLOT_MAX_DATA
SLOT_SECONDS = SLOT_BYTES * 2 / SLOT_RATE
KIT_SUFFIX = ".fm1kit"
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def note_name(n: int) -> str:
    return f"{NOTE_NAMES[n % 12]}{n // 12 - 1}"


# ------------------------------------------------------------ Audio I/O ---

def have_ffmpeg() -> bool:
    """ffmpeg vorhanden? (nötig für MP3/FLAC/OGG/M4A; Debian: sudo apt install ffmpeg)"""
    return shutil.which("ffmpeg") is not None


def _ffmpeg_to_wav(path: Path) -> Path:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise ValueError(f"{path.name}: zum Lesen von {path.suffix} wird ffmpeg gebraucht (sudo apt install ffmpeg)")
    tmp = Path(tempfile.mkstemp(suffix=".wav", prefix="fm1c-")[1])
    try:
        r = subprocess.run([exe, "-v", "error", "-y", "-i", str(path), "-ac", "1", "-c:a", "pcm_s16le", str(tmp)],
                           capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        tmp.unlink(missing_ok=True)
        raise ValueError(f"{path.name}: ffmpeg braucht zu lange (Datei zu groß oder beschädigt)") from None
    if r.returncode:
        tmp.unlink(missing_ok=True)
        err = r.stderr.strip().splitlines()
        raise ValueError(f"{path.name}: ffmpeg kann die Datei nicht lesen (beschädigt oder kein Audio)"
                         + (f" – {err[-1][:120]}" if err else ""))
    return tmp


def read_mono(path: Path) -> tuple[int, list[float]]:
    """beliebige Audiodatei -> (Rate, Mono-Floats in [-1, 1)). Fehler sind ValueError mit deutschem Text."""
    path = Path(path)
    if not path.exists():
        raise ValueError(f"{path.name}: Datei nicht gefunden (verschoben oder gelöscht?)")
    is_wav = path.suffix.lower() in (".wav", ".wave")
    if is_wav:
        try:
            if np is not None:
                return _read_wav_np(path)
            return sio.read_any_wav(path)
        except (ValueError, struct.error, IndexError, KeyError):
            if not have_ffmpeg():
                raise ValueError(f"{path.name}: WAV-Datei beschädigt oder in einem Format, das die App nicht lesen "
                                 "kann (z. B. komprimiert). Mit ffmpeg ginge es: sudo apt install ffmpeg") from None
    tmp = _ffmpeg_to_wav(path)
    try:
        return sio.read_any_wav(tmp)
    except (ValueError, struct.error, IndexError, KeyError):
        raise ValueError(f"{path.name}: Audiodatei beschädigt oder leer") from None
    finally:
        tmp.unlink(missing_ok=True)


def _read_wav_np(path: Path):
    d = path.read_bytes()
    ch = sio.wav_chunks(d)
    tag, nch, sr, bits = sio.wav_format(d, ch)
    bps = bits // 8
    o, n = ch.get(b"data", (0, 0))
    frame = bps * nch
    nfr = min(n, len(d) - o) // frame
    raw = d[o:o + nfr * frame]
    if tag == 3:
        x = np.frombuffer(raw, dtype="<f4" if bps == 4 else "<f8").astype(np.float64)
    elif bps == 2:
        x = np.frombuffer(raw, dtype="<i2") / 32768.0
    elif bps == 3:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
        x = (b[:, 0].astype(np.int32) << 8 | b[:, 1].astype(np.int32) << 16 | b[:, 2].astype(np.int32) << 24) / 2147483648.0
    elif bps == 4:
        x = np.frombuffer(raw, dtype="<i4") / 2147483648.0
    elif bps == 1:
        x = (np.frombuffer(raw, dtype=np.uint8).astype(np.float64) - 128) / 128
    else:
        raise ValueError("unbekanntes WAV-Format")
    if nch > 1:
        x = x.reshape(-1, nch).mean(axis=1)
    return sr, x.tolist()


@dataclass
class AudioMeta:
    duration: float = 0.0
    rate: int = 0
    channels: int = 0
    bits: int = 0


def probe(path: Path) -> AudioMeta:
    """schnell: nur den Kopf lesen."""
    path = Path(path)
    if path.suffix.lower() in (".wav", ".wave"):
        try:
            with open(path, "rb") as f:
                head = f.read(65536)
            ch = sio.wav_chunks(head + b"\0" * 8)
            tag, nch, sr, bits = sio.wav_format(head, ch)
            o, n = ch.get(b"data", (0, 0))
            size = path.stat().st_size
            n = min(n, max(0, size - o)) if n else max(0, size - o)
            fr = max(1, nch * max(1, bits // 8))
            return AudioMeta(n / fr / sr if sr else 0, sr, nch, bits)
        except Exception:
            try:
                with wave.open(str(path)) as w:
                    return AudioMeta(w.getnframes() / w.getframerate(), w.getframerate(), w.getnchannels(),
                                     w.getsampwidth() * 8)
            except Exception:
                pass
    exe = shutil.which("ffprobe")
    if exe:
        try:
            r = subprocess.run([exe, "-v", "error", "-select_streams", "a:0", "-show_entries",
                                "stream=sample_rate,channels,bits_per_sample:format=duration", "-of", "json",
                                str(path)], capture_output=True, text=True, timeout=20)
            j = json.loads(r.stdout or "{}")
            st = (j.get("streams") or [{}])[0]
            return AudioMeta(float(j.get("format", {}).get("duration") or 0), int(st.get("sample_rate") or 0),
                             int(st.get("channels") or 0), int(st.get("bits_per_sample") or 0))
        except Exception:
            pass
    return AudioMeta()


def peaks(x, buckets: int = 600) -> list[tuple[float, float]]:
    """x: Liste oder numpy-Array."""
    if x is None or len(x) == 0:
        return []
    if np is not None:
        a = np.asarray(x, dtype=np.float32)
        n = max(1, len(a) // buckets)
        m = len(a) // n * n
        if m == 0:
            return [(float(a.min()), float(a.max()))]
        r = a[:m].reshape(-1, n)
        return list(zip(r.min(axis=1).tolist(), r.max(axis=1).tolist()))
    n = max(1, len(x) // buckets)
    return [(min(x[i:i + n]), max(x[i:i + n])) for i in range(0, len(x), n)]


# ---------------------------------------------------------- Bearbeitung ---

@dataclass
class Edit:
    """nicht-destruktive Bearbeitung eines Sounds (Zeiten in Sekunden)."""
    start: float = 0.0
    end: float = 0.0              # 0 = bis zum Ende
    trim_silence: bool = True
    normalize: bool = True
    gain_db: float = 0.0
    fade_in_ms: float = 0.0
    fade_out_ms: float = 5.0
    reverse: bool = False


def apply_edit(sr: int, x: list[float], e: Edit) -> list[float]:
    a = int(max(0.0, e.start) * sr)
    b = int(e.end * sr) if e.end > 0 else len(x)
    y = list(x[a:b])
    if e.trim_silence and y:
        thr = 0.003 * max(1e-9, max(map(abs, y)))
        s = next((i for i, v in enumerate(y) if abs(v) > thr), 0)
        t = len(y) - next((i for i, v in enumerate(reversed(y)) if abs(v) > thr), 0)
        y = y[max(0, s - int(0.002 * sr)):t]
    if e.reverse:
        y.reverse()
    if not y:
        return y
    g = 10 ** (e.gain_db / 20)
    if e.normalize:
        pk = max(1e-9, max(map(abs, y)))
        g *= 0.97 / pk
    if g != 1.0:
        y = [v * g for v in y]
    fi = int(e.fade_in_ms / 1000 * sr)
    for i in range(min(fi, len(y))):
        y[i] *= i / fi
    fo = int(e.fade_out_ms / 1000 * sr)
    n = len(y)
    for i in range(min(fo, n)):
        y[n - 1 - i] *= i / fo
    return [max(-1.0, min(1.0, v)) for v in y]


def detect_root(sr: int, x: list[float], name: str = "") -> int:
    n = sio.note_from_name(name, -1)
    if n >= 0:
        return n
    try:
        hz = sio.detect_hz(x, sr)
        if hz and 20 < hz < 5000:
            return max(0, min(127, round(sio.hz_to_midi(hz))))
    except Exception:
        pass
    return 60


# ------------------------------------------------------------------ Kits ---

@dataclass
class Zone:
    path: str
    root: int = 60
    lo: int | None = None
    hi: int | None = None
    edit: Edit = field(default_factory=Edit)
    label: str = ""

    @property
    def name(self) -> str:
        return self.label or Path(self.path).stem


def safe_kit_name(name: str) -> str:
    """Dateiname für ein Kit: keine Schrägstriche/Steuerzeichen, kein führender Punkt, höchstens 80 Zeichen."""
    t = re.sub(r"[^\w\-. ()]+", "_", str(name or "").strip()).strip(" ._")
    return t[:80]


@dataclass
class Kit:
    name: str = "KIT"
    mode: str = "drum"            # "drum": jede Taste ein Sound; "keys": über die Tastatur verteilt
    base_note: int = 60           # erste Taste im Drum-Modus
    zones: list[Zone] = field(default_factory=list)
    notes: str = ""
    path: str = ""                # wo es gespeichert ist

    def to_json(self) -> dict:
        d = asdict(self)
        d.pop("path", None)
        d["format"] = "fm1companion-kit"
        d["version"] = 1
        return d

    @staticmethod
    def from_json(d: dict, path: str = "") -> "Kit":
        zs = []
        for z in d.get("zones", []):
            z = dict(z)
            ed = Edit(**{k: v for k, v in (z.pop("edit", {}) or {}).items() if k in Edit.__dataclass_fields__})
            zs.append(Zone(edit=ed, **{k: v for k, v in z.items() if k in Zone.__dataclass_fields__ and k != "edit"}))
        return Kit(name=d.get("name", "KIT"), mode=d.get("mode", "drum"), base_note=d.get("base_note", 60),
                   zones=zs, notes=d.get("notes", ""), path=path)

    def save(self, folder: Path | None = None) -> Path:
        from .backup import write_text_atomic
        p = Path(self.path) if self.path else Path(folder) / f"{safe_kit_name(self.name) or 'Kit'}{KIT_SUFFIX}"
        write_text_atomic(p, json.dumps(self.to_json(), indent=1, ensure_ascii=False))   # ganz oder gar nicht
        self.path = str(p)
        return p

    @staticmethod
    def load(path: Path) -> "Kit":
        return Kit.from_json(json.loads(Path(path).read_text("utf-8")), str(path))

    def assign_keys(self):
        """Tasten nach Modus vergeben."""
        if self.mode == "drum":
            for i, z in enumerate(self.zones):
                z.root = z.lo = z.hi = max(0, min(127, self.base_note + i))
        else:
            for z in self.zones:
                z.lo = z.hi = None


_render_cache: dict = {}


def render_zone(z: Zone) -> list[int]:
    """Zone -> int16 bei 22 050 Hz, wie sie auf das Gerät geht (mit Cache)."""
    try:
        st = os.stat(z.path)
        key = (z.path, st.st_mtime, st.st_size, json.dumps(asdict(z.edit), sort_keys=True))
    except OSError:
        raise ValueError(f"{Path(z.path).name} fehlt")
    if key in _render_cache:
        return _render_cache[key]
    sr, x = read_mono(Path(z.path))
    y = apply_edit(sr, x, z.edit)
    y = sio.resample(y, sr, SLOT_RATE)
    if not y:
        raise ValueError(f"{Path(z.path).name}: kein Audio übrig")
    if z.edit.normalize:
        s = sio.to_int16(y)
    else:
        s = [max(-32768, min(32767, int(v * 32767))) for v in y]
    if len(_render_cache) > 64:
        _render_cache.clear()
    _render_cache[key] = s
    return s


def kit_budget(kit: Kit) -> tuple[int, list[int]]:
    """-> (ADPCM-Bytes gesamt, Bytes je Zone). Ein Slot fasst SLOT_BYTES."""
    per = [(len(render_zone(z)) + 1) // 2 for z in kit.zones]
    return sum(per), per


def fit_to_slot(kit: Kit):
    """kürzt die längsten Sounds, bis alles in einen Slot passt."""
    total, per = kit_budget(kit)
    guard = 0
    while total > SLOT_BYTES and guard < 200:
        guard += 1
        i = max(range(len(per)), key=lambda k: per[k])
        z = kit.zones[i]
        sec = per[i] * 2 / SLOT_RATE
        over = (total - SLOT_BYTES) * 2 / SLOT_RATE
        new_len = max(0.05, sec - over - 0.01) if len(per) == 1 else max(0.05, sec * 0.85)
        z.edit.end = (z.edit.start or 0) + new_len
        z.edit.trim_silence = False
        total, per = kit_budget(kit)


def budget_text(total_bytes: int) -> str:
    """„ZU LANG: 9,2 s von 7,4 s“ – verständliche Erklärung des Slot-Budgets."""
    secs = total_bytes * 2 / SLOT_RATE
    return (f"Das Kit ist zu lang: {secs:.2f} s, ein Slot fasst {SLOT_SECONDS:.1f} s".replace(".", ",") + " (alle Sounds zusammen, "
            f"{SLOT_BYTES // 1024} KiB IMA-ADPCM bei {SLOT_RATE // 1000} kHz). „Passend kürzen“ schneidet die längsten "
            "Sounds, oder entferne Sounds aus dem Kit.")


def build_slot(kit: Kit) -> tuple[bytes, bytes]:
    if not 1 <= len(kit.zones) <= 16:
        raise ValueError("Ein Slot hat 1 bis 16 Sounds")
    zones = []
    for z in kit.zones:
        zones.append((render_zone(z), z.root, z.lo, z.hi))
    total = sum((len(s) + 1) // 2 for s, *_ in zones)
    if total > SLOT_BYTES:
        raise ValueError(budget_text(total))
    try:
        return sio.user_slot(kit.name, zones)
    except ValueError as e:                      # englische Meldungen des Hilfsmoduls übersetzen
        msg = str(e)
        if "too long" in msg:
            raise ValueError(budget_text(total)) from e
        raise ValueError(f"Slot lässt sich nicht bauen: {msg}") from e


def export_wavs(kit: Kit, folder: Path) -> list[Path]:
    """bearbeitete Sounds als WAV (22 050 Hz, so wie auf dem FM-1)."""
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for i, z in enumerate(kit.zones):
        s = render_zone(z)
        p = folder / f"{i + 1:02d} {z.name} ({note_name(z.root)}).wav"
        with wave.open(str(p), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SLOT_RATE)
            w.writeframes(struct.pack(f"<{len(s)}h", *s))
        out.append(p)
    return out


# --------------------------------------------------------------- Geräte ---

def slot_info(link) -> tuple[int, list[SampleSlot]]:
    return parse_smp_info(link.request(Cmd.SMP_INFO, [], timeout=2.0))


def upload(link, slot: int, header: bytes, data: bytes, progress: Callable[[int, int], None] | None = None):
    rc_text = {1: "Größe", 2: "Kopf", 3: "Daten-Prüfsumme", 4: "Flash", 5: "Zonen"}
    a = link.request(Cmd.SMP_BEGIN, [slot], timeout=5.0, retries=0)
    if a[0] != slot or a[-1]:
        raise RuntimeError("Slot lässt sich nicht öffnen (Wiedergabe stoppen?)")
    total = len(data)
    for off in range(0, total, 256):
        addr = sio.SLOT_DATA_OFF + off
        a = link.request(Cmd.SMP_WRITE, [slot, addr & 127, addr >> 7 & 127, addr >> 14 & 127, *pack7(data[off:off + 256])],
                         timeout=4.0, retries=0)
        if a[-1]:
            raise RuntimeError(f"Schreibfehler bei {addr:#x} ({a[-1]})")
        if progress:
            progress(min(total, off + 256), total)
    a = link.request(Cmd.SMP_END, [slot, *pack7(header[:480])], timeout=8.0, retries=0)
    if a[-1]:
        raise RuntimeError(f"Abschluss fehlgeschlagen: {rc_text.get(a[-1], a[-1])}")


def erase(link, slot: int):
    a = link.request(Cmd.SMP_ERASE, [slot], timeout=8.0, retries=0)
    if a[-1]:
        raise RuntimeError("Löschen fehlgeschlagen")


def read_slot(link, slot: int, hint: str = "") -> bytes:
    """aktuellen Inhalt eines Sample-Slots lesen (Backup-Objekt 32+slot: Kopf, Füllbytes, Daten) – für
    Rückgängig vor dem Überschreiben/Leeren. b"" = leerer Slot. hint: Firmware-Schlüssel (ctx.firmware_key)."""
    from . import backup
    objs = backup.list_objects(link, hint)
    o = next((x for x in objs if x.id == 32 + slot), None)
    if o is None:
        raise RuntimeError(f"USR{slot + 1} taucht im Inventar des FM-1 nicht auf – Sicherung nicht möglich.")
    return backup.read_object(link, o) if o.size else b""


def restore_slot(link, slot: int, data: bytes, progress: Callable[[int, int], None] | None = None):
    """mit read_slot() gelesenen Inhalt zurückschreiben (leer -> Slot leeren)."""
    if not data:
        erase(link, slot)
        return
    if len(data) < sio.SLOT_DATA_OFF:
        raise RuntimeError("Gesicherter Slot ist zu kurz")
    upload(link, slot, data[:480], data[sio.SLOT_DATA_OFF:], progress)


# ------------------------------------------------------------ Bibliothek ---

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
  path TEXT PRIMARY KEY, root TEXT, folder TEXT, name TEXT, ext TEXT, size INTEGER, mtime REAL,
  duration REAL, rate INTEGER, channels INTEGER, bits INTEGER,
  tags TEXT DEFAULT '', favorite INTEGER DEFAULT 0, rating INTEGER DEFAULT 0, notes TEXT DEFAULT '',
  used INTEGER DEFAULT 0, added REAL
);
CREATE INDEX IF NOT EXISTS i_folder ON samples(folder);
CREATE INDEX IF NOT EXISTS i_name ON samples(name);
"""

# Schlagwörter aus Datei- und Ordnernamen (Pocket Operator & Co.)
AUTO_TAGS = {
    "kick": ["kick", "bd", "bassdrum", "bass drum"], "snare": ["snare", "sd", "snr"], "clap": ["clap", "cp", "clp"],
    "hihat": ["hat", "hh", "hihat", "chh", "ohh"], "cymbal": ["cym", "crash", "ride"], "tom": ["tom"],
    "perc": ["perc", "conga", "bongo", "shaker", "cowbell", "rim", "clave", "tamb"],
    "bass": ["bass", "sub", "808"], "vocal": ["vox", "vocal", "voice", "speech"], "loop": ["loop", "break"],
    "fx": ["fx", "sfx", "riser", "noise", "sweep"], "keys": ["piano", "key", "rhodes", "organ"],
    "pad": ["pad", "drone", "ambient"], "pocket operator": ["po-", "po_", "pocket operator", "po33", "po-33", "ko"],
}


def auto_tags(path: Path) -> str:
    s = (" " + str(path).lower().replace("_", " ").replace("-", " ") + " ")
    tags = []
    for tag, keys in AUTO_TAGS.items():
        for k in keys:
            k2 = k.replace("-", " ").replace("_", " ")
            if re.search(r"(?<![a-z])" + re.escape(k2.strip()) + r"(?![a-z])", s):
                tags.append(tag)
                break
    return ",".join(tags)


class Library:
    def __init__(self, db: Path = DB_FILE):
        db.parent.mkdir(parents=True, exist_ok=True)
        self.db = db
        self._lock = threading.Lock()
        with self._conn() as c:
            c.executescript(SCHEMA)

    def _conn(self):
        c = sqlite3.connect(self.db, timeout=10)
        c.row_factory = sqlite3.Row
        return c

    def scan(self, roots: Iterable[Path], progress: Callable[[int, str], None] | None = None,
             cancel: Callable[[], bool] = lambda: False) -> dict:
        """inkrementell: neue/geänderte Dateien lesen, verschwundene entfernen."""
        stats = {"neu": 0, "geändert": 0, "entfernt": 0, "gesamt": 0}
        roots = [Path(r).expanduser() for r in roots]
        with self._lock, self._conn() as c:
            known = {r["path"]: (r["mtime"], r["size"]) for r in c.execute("SELECT path, mtime, size FROM samples")}
            seen = set()
            n = 0
            for root in roots:
                if not root.exists():
                    continue
                for dirpath, dirnames, files in os.walk(root, followlinks=False):
                    dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                    for fn in files:
                        if cancel():
                            return stats
                        p = Path(dirpath) / fn
                        if p.suffix.lower() not in AUDIO_EXT or fn.startswith("."):
                            continue
                        sp = str(p)
                        seen.add(sp)
                        try:
                            st = p.stat()
                        except OSError:
                            continue
                        n += 1
                        if progress and n % 50 == 0:
                            progress(n, fn)
                        old = known.get(sp)
                        if old and abs(old[0] - st.st_mtime) < 1e-6 and old[1] == st.st_size:
                            continue
                        m = probe(p)
                        if old:
                            c.execute("UPDATE samples SET size=?, mtime=?, duration=?, rate=?, channels=?, bits=? "
                                      "WHERE path=?", (st.st_size, st.st_mtime, m.duration, m.rate, m.channels, m.bits, sp))
                            stats["geändert"] += 1
                        else:
                            c.execute("INSERT INTO samples(path, root, folder, name, ext, size, mtime, duration, rate, "
                                      "channels, bits, tags, added) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                      (sp, str(root), str(p.parent), p.stem, p.suffix.lower(), st.st_size, st.st_mtime,
                                       m.duration, m.rate, m.channels, m.bits, auto_tags(p.relative_to(root)), time.time()))
                            stats["neu"] += 1
            # nur unter Ordnern, die es gibt (abgezogener Stick löscht keine Tags/Favoriten), und nur echte
            # Unterpfade ("/x/Samples" darf "/x/Samples2/…" nicht erfassen)
            alive = [str(r).rstrip(os.sep) + os.sep for r in roots if r.exists()]
            gone = [p for p in known if p not in seen and any(p.startswith(r) for r in alive)]
            for p in gone:
                c.execute("DELETE FROM samples WHERE path=?", (p,))
            stats["entfernt"] = len(gone)
            stats["gesamt"] = c.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
        return stats

    def add_file(self, path: Path, root: Path | None = None, tags: str = "", notes: str = "") -> None:
        """eine Datei sofort aufnehmen (Aufnahme, Slices, Sample-Pakete) – mit zusätzlichen Schlagwörtern."""
        p = Path(path)
        st = p.stat()
        m = probe(p)
        root = Path(root) if root else p.parent
        try:
            rel = p.relative_to(root)
        except ValueError:
            rel = Path(p.name)
        auto = [t for t in auto_tags(rel).split(",") if t]
        extra = [t.strip().lower() for t in tags.split(",") if t.strip()]
        alltags = ",".join(dict.fromkeys(auto + extra))
        with self._lock, self._conn() as c:
            c.execute("INSERT OR REPLACE INTO samples(path, root, folder, name, ext, size, mtime, duration, rate, "
                      "channels, bits, tags, notes, added) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (str(p), str(root), str(p.parent), p.stem, p.suffix.lower(), st.st_size, st.st_mtime,
                       m.duration, m.rate, m.channels, m.bits, alltags, notes, time.time()))

    def get(self, path: str):
        with self._conn() as c:
            return c.execute("SELECT * FROM samples WHERE path=?", (str(path),)).fetchone()

    def remove(self, paths: Iterable[str]):
        with self._lock, self._conn() as c:
            c.executemany("DELETE FROM samples WHERE path=?", [(str(p),) for p in paths])

    def query(self, text: str = "", folder: str = "", tag: str = "", favorites: bool = False,
              max_seconds: float = 0, limit: int = 5000) -> list[sqlite3.Row]:
        q, args = "SELECT * FROM samples WHERE 1=1", []
        for word in text.lower().split():
            q += " AND (lower(name) LIKE ? OR lower(tags) LIKE ? OR lower(notes) LIKE ? OR lower(folder) LIKE ?)"
            args += [f"%{word}%"] * 4
        if folder:
            q += " AND (folder = ? OR folder LIKE ?)"
            args += [folder, folder.rstrip("/") + "/%"]
        if tag:
            q += " AND (',' || tags || ',') LIKE ?"
            args.append(f"%,{tag},%")
        if favorites:
            q += " AND favorite = 1"
        if max_seconds:
            q += " AND duration > 0 AND duration <= ?"
            args.append(max_seconds)
        q += " ORDER BY folder, name LIMIT ?"
        args.append(limit)
        with self._conn() as c:
            return list(c.execute(q, args))

    def folders(self) -> list[tuple[str, int]]:
        with self._conn() as c:
            return [(r[0], r[1]) for r in c.execute("SELECT folder, COUNT(*) FROM samples GROUP BY folder ORDER BY folder")]

    def tags(self) -> list[tuple[str, int]]:
        counts: dict[str, int] = {}
        with self._conn() as c:
            for (t,) in c.execute("SELECT tags FROM samples WHERE tags != ''"):
                for x in t.split(","):
                    if x:
                        counts[x] = counts.get(x, 0) + 1
        return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))

    def stats(self) -> dict:
        with self._conn() as c:
            r = c.execute("SELECT COUNT(*), COALESCE(SUM(size),0), COALESCE(SUM(duration),0), "
                          "COALESCE(SUM(favorite),0) FROM samples").fetchone()
            return {"count": r[0], "bytes": r[1], "seconds": r[2], "favorites": r[3]}

    def set(self, path: str, **fields):
        allowed = {"tags", "favorite", "rating", "notes", "used"}
        sets = [(k, v) for k, v in fields.items() if k in allowed]
        if not sets:
            return
        with self._lock, self._conn() as c:
            c.execute(f"UPDATE samples SET {', '.join(k + '=?' for k, _ in sets)} WHERE path=?",
                      [v for _, v in sets] + [path])

    def mark_used(self, paths: Iterable[str]):
        with self._lock, self._conn() as c:
            c.executemany("UPDATE samples SET used = used + 1 WHERE path=?", [(p,) for p in paths])

    def duplicates(self) -> list[list[str]]:
        """gleiche Größe und gleicher Inhalt (Hash der ersten 64 KiB + Größe)."""
        import hashlib
        with self._conn() as c:
            rows = list(c.execute("SELECT path, size FROM samples WHERE size IN "
                                  "(SELECT size FROM samples GROUP BY size HAVING COUNT(*) > 1) ORDER BY size"))
        groups: dict[tuple, list[str]] = {}
        for r in rows:
            try:
                with open(r["path"], "rb") as f:
                    h = hashlib.sha1(f.read(65536)).hexdigest()
            except OSError:
                continue
            groups.setdefault((r["size"], h), []).append(r["path"])
        return [g for g in groups.values() if len(g) > 1]
