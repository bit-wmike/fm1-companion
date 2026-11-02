# SPDX-License-Identifier: GPL-3.0-only
"""Automatische Backups, Aufbewahrung, Schlagwörter/Anheften und Spiegel-Ordner (z. B. NAS).

Metadaten (Notiz, Schlagwörter, angeheftet, Art) stehen im Block "companion" der Backup-Datei
selbst – so reisen sie mit (Zip-Export, NAS-Spiegel). Beim Ändern bleibt die Änderungszeit
der Datei erhalten. Für schnelle Listen merkt sich ein Index (DATA_DIR/daten/index.json)
die Kopfdaten jeder Datei (Pfad, Größe, mtime).

Aufbewahrung gilt nur für automatische Backups (Art auto / sicherheit / vor-Update):
die letzten N bleiben, dazu das jeweils neueste jedes Monats, das neueste je Firmware
(damit die Rückkehr zu einer alten Firmware immer ein Backup findet) und alle angehefteten.
Manuelle Backups löscht die App nie.

Wichtig: BACKUP_LIST stoppt am FM-1 die Wiedergabe. Automatische Backups laufen deshalb
nur, wenn kein anderer Gerätezugriff läuft, nicht während Editor/Aufnahme offen sind, nie
während einer Installation, und brechen ab, sobald ein anderer Zugriff beginnt.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import backup, config

DEFAULTS = {"on_connect": True, "days": 1, "periodic": False, "hours": 6, "keep_monthly": True,
            "mirror": "", "delay_s": 20}
AUTO_KINDS = ("auto", "vor-Update", "sicherheit")
_lock = threading.Lock()


def settings() -> dict:
    return config.module("daten", DEFAULTS)


def backup_filename(device: str, fw: str, kind: str = "") -> str:
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    safe = re.sub(r"[^\w\-]+", "_", device).strip("_") or "FM-1"
    return f"{safe}_{fw or 'fw'}_{stamp}{('_' + kind) if kind else ''}.json"


def companion_block(profile, fw_key: str, version: str, note: str = "", kind: str = "") -> dict:
    """Kennzeichnung jedes Backups: Gerät und *die Firmware, mit der es gemacht wurde* (für die Rückkehr)."""
    return {"device": profile.name if profile else "", "device_id": profile.id if profile else "",
            "serial": profile.serial if profile else "",
            "firmware": fw_key, "firmware_version": profile.firmware_version if profile else "",
            "version_string": version, "note": note, "kind": kind or "manuell", "labels": [], "pinned": False,
            "app": "FM-1 Companion"}


def capture_to_file(link, folder: Path, profile, fw_key: str, version: str, note: str = "", kind: str = "",
                    progress=None, abort=None) -> Path:
    """Backup lesen und speichern. abort() -> True bricht beim nächsten Stück ab."""
    def prog(a, b):
        if abort and abort():
            raise backup.BackupError("abgebrochen (Gerät wird anderweitig gebraucht)")
        if progress:
            progress(a, b)
    f = backup.capture(link, version, progress=prog,
                       companion=companion_block(profile, fw_key, version, note, kind))
    suffix = {"sicherheit": "auto"}.get(kind, kind if kind != "manuell" else "")
    path = Path(folder) / backup_filename(profile.name if profile else "FM-1", fw_key, suffix)
    backup.write_file(path, f)
    return path


# -------------------------------------------------------------- Index ---

@dataclass
class Meta:
    path: Path
    created: str
    firmware: str           # Versionszeichenkette aus dem Backup
    fw_key: str
    device: str
    device_id: str
    note: str
    labels: list[str]
    pinned: bool
    kind: str
    size: int
    mtime: float
    objects: int
    projects: int
    fmt: str = "felucca-backup"
    presets: int = -1        # belegte Preset-Plätze (-1: unbekannt, älterer Index)
    damaged: str = ""        # nicht leer: Datei lässt sich nicht lesen (Grund) – wird als „beschädigt“ gezeigt

    @property
    def automatic(self) -> bool:
        return self.kind in AUTO_KINDS or self.path.stem.endswith(("_auto", "_vor-Update"))

    @property
    def when(self) -> dt.datetime:
        try:
            t = dt.datetime.fromisoformat(self.created.replace("Z", "+00:00"))
            return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
        except ValueError:
            return dt.datetime.fromtimestamp(self.mtime, dt.timezone.utc)


def content_text(projects: int, presets: int, objects: int) -> str:
    """„2 Projekte, 5 Presets · 57 Teile“ – überall dieselbe Formel (Liste, Browser, Übersicht)."""
    parts = [f"{projects} Projekt{'e' if projects != 1 else ''}"]
    if presets >= 0:
        parts.append(f"{presets} Preset{'s' if presets != 1 else ''}")
    return ", ".join(parts) + f" · {objects} Teile"


def _index_file() -> Path:
    return config.DATA_DIR / "daten" / "index.json"


_cache: dict | None = None


def _load_index() -> dict:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(_index_file().read_text("utf-8"))
        except (OSError, ValueError):
            _cache = {}
    return _cache


def _save_index():
    if _cache is None:
        return
    p = _index_file()
    try:
        backup.write_text_atomic(p, json.dumps(_cache))
    except OSError:
        pass


def _kind_from_name(stem: str) -> str:
    if stem.endswith("_vor-Update"):
        return "vor-Update"
    if stem.endswith("_auto"):
        return "auto"
    return "manuell"


def read_meta(path: Path) -> Meta | None:
    path = Path(path)
    try:
        st = path.stat()
    except OSError:
        return None
    with _lock:
        idx = _load_index()
        e = idx.get(str(path))
        if not e or e.get("mtime") != st.st_mtime or e.get("size") != st.st_size or "presets" not in e:
            try:
                f = json.loads(path.read_text("utf-8"))
            except (OSError, ValueError):
                return None
            if not isinstance(f, dict) or f.get("format") not in (backup.FORMAT, "sloop-backup"):
                return None
            c = f.get("companion") or {}
            objs = f.get("objects") or []
            fw = f.get("firmware", "")
            key = c.get("firmware") or next((k for k in ("melodee", "sloop", "skiff") if k in fw.lower()), "")
            try:                                   # belegte Presets einmal zählen (steht danach im Index)
                from . import backup_inspect as _BI
                presets = len(_BI.inspect(f).used_presets)
            except Exception:                      # noqa: BLE001 – dann eben ohne Zahl
                presets = -1
            e = {"presets": presets, "mtime": st.st_mtime, "size": st.st_size, "created": f.get("created") or f.get("date", ""),
                 "firmware": fw, "fw_key": key, "device": c.get("device", ""), "device_id": c.get("device_id", ""),
                 "note": c.get("note", ""), "labels": list(c.get("labels") or []), "pinned": bool(c.get("pinned")),
                 "kind": c.get("kind") or _kind_from_name(path.stem), "objects": len(objs),
                 "projects": sum(1 for o in objs if isinstance(o, dict) and (o.get("size") or o.get("len"))
                                 and backup.is_project(o.get("id", -1)) and o.get("id") != 0),
                 "fmt": f.get("format")}
            idx[str(path)] = e
            _save_index()
    return Meta(path, e["created"], e["firmware"], e["fw_key"], e["device"], e["device_id"], e["note"], e["labels"],
                e["pinned"], e["kind"], e["size"], e["mtime"], e["objects"], e["projects"], e.get("fmt", backup.FORMAT),
                int(e.get("presets", -1)))


def damaged_meta(path: Path) -> Meta | None:
    """Meta für eine *.json im Backup-Ordner, die sich nicht als JSON lesen lässt (abgeschnitten, kaputt).
    None, wenn die Datei lesbar ist (dann ist sie ein Backup oder etwas anderes)."""
    path = Path(path)
    try:
        st = path.stat()
        raw = path.read_bytes()
    except OSError as e:
        return Meta(path, "", "", "", "", "", "", [], False, "beschädigt", 0, 0.0, 0, 0, "", damaged=str(e.strerror))
    try:
        json.loads(raw.decode("utf-8"))
        return None
    except (ValueError, UnicodeDecodeError) as e:
        why = "kein gültiges JSON (abgeschnitten oder beschädigt)" if raw else "leere Datei"
        return Meta(path, "", "", "", path.parent.name, "", "", [], False, "beschädigt", st.st_size, st.st_mtime, 0, 0,
                    "", damaged=f"{why}: {type(e).__name__}")


def list_backups(folder: Path, recursive: bool = True, include_damaged: bool = False) -> list[Meta]:
    """Backups im Ordner (neueste zuerst). include_damaged: unlesbare Dateien als Meta mit .damaged mitliefern."""
    folder = Path(folder)
    if not folder.exists():
        return []
    files = list(folder.rglob("*.json") if recursive else folder.glob("*.json"))
    out = []
    for f in files:
        m = read_meta(f)
        if m is None and include_damaged:
            m = damaged_meta(f)
        if m:
            out.append(m)
    out.sort(key=lambda m: m.when, reverse=True)
    return out


def set_meta(path: Path, **changes):
    """note / labels / pinned / kind im companion-Block ändern; die Änderungszeit bleibt."""
    path = Path(path)
    st = path.stat()
    try:
        f = json.loads(path.read_text("utf-8"))
    except ValueError:
        raise backup.BackupError("Die Datei ist kein lesbares Backup (beschädigt) – Notiz/Schlagwörter lassen sich "
                                 "nicht speichern.")
    if not isinstance(f, dict) or f.get("format") not in (backup.FORMAT, "sloop-backup"):
        raise backup.BackupError("Die Datei ist kein Backup der App oder des Web-Editors.")
    c = f.setdefault("companion", {})
    c.update(changes)
    # (SLOOP-Backups bleiben im Format des SLOOP-Editors); Änderungszeit bleibt
    backup.write_text_atomic(path, backup.dumps(f), keep_times=(st.st_atime_ns, st.st_mtime_ns))
    with _lock:                                      # Index: der Eintrag ist jetzt veraltet (gleiche mtime möglich!)
        _load_index().pop(str(path), None)
        _save_index()


# --------------------------------------------------------- Aufbewahrung ---

def retention(metas: list[Meta], keep_last: int, keep_monthly: bool = True) -> list[Meta]:
    """-> die automatischen Backups, die weg dürfen."""
    autos = sorted([m for m in metas if m.automatic], key=lambda m: m.when, reverse=True)
    if keep_last <= 0:
        return []
    keep = set(id(m) for m in autos[:keep_last])
    keep |= {id(m) for m in autos if m.pinned}
    newest_fw: dict[str, Meta] = {}
    months: dict[str, Meta] = {}
    for m in autos:                              # neueste zuerst
        newest_fw.setdefault(m.fw_key, m)
        months.setdefault(m.when.strftime("%Y-%m"), m)
    keep |= {id(m) for m in newest_fw.values()}
    if keep_monthly:
        keep |= {id(m) for m in months.values()}
    # Manuelle sind nie in autos – werden nie gelöscht
    return [m for m in autos if id(m) not in keep]


def prune(folder: Path, keep_last: int) -> list[Path]:
    gone = []
    for m in retention(list_backups(folder, recursive=False), keep_last, settings()["keep_monthly"]):
        m.path.unlink(missing_ok=True)
        gone.append(m.path)
    return gone


def needs_backup(folder: Path, days: float, now: float | None = None) -> bool:
    ms = list_backups(folder, recursive=False)
    if not ms:
        return True
    age = (now or time.time()) - ms[0].when.timestamp()
    return age > days * 86400


# -------------------------------------------------------------- Spiegel ---

def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def os_error_text(e: BaseException) -> str:
    """OSError in Alltagssprache (ohne Qt, für Protokoll- und Statuszeilen)."""
    import errno as _e
    code = getattr(e, "errno", None)
    path = getattr(e, "filename", None)
    where = f" ({path})" if path else ""
    if code in (_e.EACCES, _e.EPERM, _e.EROFS):
        return "keine Schreibrechte" + where
    if code == _e.ENOSPC:
        return "der Datenträger ist voll" + where
    if code == _e.ENOENT:
        return "Ordner oder Datei nicht gefunden" + where
    if code in (_e.EIO, _e.ESTALE, _e.ENOTCONN, _e.EHOSTDOWN, _e.EHOSTUNREACH, _e.ENETUNREACH):
        return "Datenträger oder Netzlaufwerk antwortet nicht" + where
    if code is None:
        return str(e)
    return f"{getattr(e, 'strerror', '') or e}{where}"


def mirror_file(path: Path, root: Path, target: Path) -> Path:
    """Datei in den Spiegel kopieren (relativer Pfad wie unter root) und prüfen.
    OSError mit deutscher Meldung, wenn das Ziel fehlt (NAS nicht eingehängt) oder die Kopie nicht stimmt."""
    path, target = Path(path), Path(target)
    if not target.is_dir():
        raise OSError(f"Spiegel-Ordner {target} ist nicht erreichbar – ist das Laufwerk (NAS) eingehängt?")
    try:
        rel = path.relative_to(root)
    except ValueError:
        rel = Path(path.name)
    dest = target / rel
    try:
        if dest.exists() and dest.stat().st_size == path.stat().st_size and _sha(dest) == _sha(path):
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        shutil.copyfile(path, tmp)
        if _sha(tmp) != _sha(path):
            tmp.unlink(missing_ok=True)
            raise OSError(f"Prüfung der Kopie {dest.name} fehlgeschlagen – die Kopie im Spiegel ist nicht identisch")
        tmp.replace(dest)
        try:
            shutil.copystat(path, dest)
        except OSError:
            pass                                   # (manche Netzlaufwerke erlauben kein Setzen der Zeiten)
    except OSError as e:
        if getattr(e, "errno", None) is None:
            raise
        raise OSError(f"{dest.name}: {os_error_text(e)}") from e
    return dest


def mirror_all(root: Path, target: Path) -> tuple[int, int, list[str]]:
    """alle Backups unter root spiegeln -> (kopiert/geprüft, Fehler, Meldungen)."""
    ok, bad, msgs = 0, 0, []
    for f in sorted(Path(root).rglob("*.json")):
        try:
            mirror_file(f, root, target)
            ok += 1
        except OSError as e:
            bad += 1
            msgs.append(f"{f.name}: {e}")
            if not Path(target).exists():
                break
    return ok, bad, msgs


# ----------------------------------------------------- Status für die UI ---

@dataclass
class Status:
    text: str = "Automatische Backups: noch nichts passiert."
    last_path: Path | None = None
    last_time: float = 0.0
    mirror: str = ""
    history: list[str] = field(default_factory=list)

    def add(self, line: str):
        self.text = line
        self.history.insert(0, time.strftime("%H:%M ") + line)
        del self.history[30:]


STATUS = Status()


# ------------------------------------------------------------ Zip-Sätze ---

def export_zip(paths, dest: Path, root: Path) -> int:
    """Backups samt Inhaltsverzeichnis (manifest.json mit SHA-256) in eine Zip-Datei."""
    import zipfile
    entries = []
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for p in paths:
            p = Path(p)
            try:
                rel = p.relative_to(root)
            except ValueError:
                rel = Path(p.name)
            m = read_meta(p)
            z.write(p, f"backups/{rel.as_posix()}")
            entries.append({"file": f"backups/{rel.as_posix()}", "sha256": _sha(p), "size": p.stat().st_size,
                            "device": m.device if m else "", "firmware": m.firmware if m else "",
                            "created": m.created if m else "", "pinned": m.pinned if m else False})
        z.writestr("manifest.json", json.dumps({"format": "fm1companion-backupset", "version": 1,
                                                "created": dt.datetime.now(dt.timezone.utc).isoformat(),
                                                "backups": entries}, indent=1, ensure_ascii=False))
    return len(entries)


def import_zip(src: Path, root: Path) -> tuple[list[Path], list[str]]:
    """Zip prüfen (Manifest, SHA-256, Backup-Inhalt) und unter root einsortieren. Vorhandenes bleibt."""
    import zipfile
    from . import backup_inspect
    got, skipped = [], []
    src = Path(src)
    try:
        z = zipfile.ZipFile(src)
    except zipfile.BadZipFile:
        raise backup.BackupError(f"{src.name} ist keine lesbare Zip-Datei (beschädigt oder abgeschnitten). "
                                 "Bitte die Datei noch einmal kopieren.")
    with z:
        try:
            man = json.loads(z.read("manifest.json"))
        except KeyError:
            raise backup.BackupError(f"{src.name} enthält kein Inhaltsverzeichnis (manifest.json) – das ist keine "
                                     "Backup-Sammlung dieser App. Einzelne Backup-Dateien (.json) nimmst du über "
                                     "„Datei hinzufügen“ im Reiter „Liste“ auf.")
        except (ValueError, zipfile.BadZipFile) as e:
            raise backup.BackupError(f"Das Inhaltsverzeichnis in {src.name} ist beschädigt ({type(e).__name__}). "
                                     "Es wurde nichts übernommen.")
        if not isinstance(man, dict) or man.get("format") != "fm1companion-backupset":
            raise backup.BackupError(f"{src.name} ist eine Zip-Datei, aber keine Backup-Sammlung dieser App.")
        entries = man.get("backups")
        for e in entries if isinstance(entries, list) else []:
            name = e.get("file", "") if isinstance(e, dict) else ""
            if not isinstance(name, str) or not name:
                skipped.append(f"{name or '?'}: ungültiger Eintrag im Inhaltsverzeichnis")
                continue
            rel = Path(name).relative_to("backups") if name.startswith("backups/") else Path(Path(name).name)
            if ".." in rel.parts or rel.is_absolute():
                skipped.append(f"{name}: ungültiger Pfad")
                continue
            try:
                data = z.read(name)
            except KeyError:
                skipped.append(f"{name}: fehlt im Zip")
                continue
            except zipfile.BadZipFile:
                skipped.append(f"{name}: im Zip beschädigt")
                continue
            if hashlib.sha256(data).hexdigest() != e.get("sha256"):
                skipped.append(f"{name}: Prüfsumme stimmt nicht")
                continue
            try:
                backup_inspect.inspect(data.decode("utf-8"))
            except Exception as ex:  # noqa: BLE001
                skipped.append(f"{name}: {ex}")
                continue
            dest = Path(root) / rel
            if dest.exists():
                skipped.append(f"{rel.name}: schon vorhanden")
                continue
            backup.write_text_atomic(dest, data.decode("utf-8"))
            got.append(dest)
    return got, skipped
