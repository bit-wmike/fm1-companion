# SPDX-License-Identifier: GPL-3.0-only
"""Automatic backups, retention, tags/pinning and mirror folders (e.g. NAS).

Metadata (note, tags, pinned, kind) live in the "companion" block of the backup file
itself – so they travel with it (zip export, NAS mirror). Editing keeps the file's
modification time. For fast listings an index (DATA_DIR/daten/index.json) remembers
the header data of every file (path, size, mtime).

Retention only applies to automatic backups (kind auto / sicherheit / vor-Update):
the last N are kept, plus the newest of each month, the newest per firmware
(so that going back to an old firmware always finds a backup) and all pinned ones.
The app never deletes manual backups.

Important: BACKUP_LIST stops playback on the FM-1. Automatic backups therefore only run
when no other device access is in progress, not while the editor/recorder is open, never
during an installation, and they abort as soon as another access starts.
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
from .i18n import _, ngettext

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
    """Label of every backup: device and *the firmware it was made with* (for going back)."""
    return {"device": profile.name if profile else "", "device_id": profile.id if profile else "",
            "serial": profile.serial if profile else "",
            "firmware": fw_key, "firmware_version": profile.firmware_version if profile else "",
            "version_string": version, "note": note, "kind": kind or "manuell", "labels": [], "pinned": False,
            "app": "FM-1 Companion"}


def capture_to_file(link, folder: Path, profile, fw_key: str, version: str, note: str = "", kind: str = "",
                    progress=None, abort=None) -> Path:
    """Read and save a backup. abort() -> True aborts at the next chunk."""
    def prog(a, b):
        if abort and abort():
            err = backup.BackupError(_("cancelled (the device is needed for something else)"))
            err.cancelled = True
            raise err
        if progress:
            progress(a, b)
    f = backup.capture(link, version, progress=prog,
                       companion=companion_block(profile, fw_key, version, note, kind))
    suffix = {"sicherheit": "auto"}.get(kind, kind if kind != "manuell" else "")
    path = Path(folder) / backup_filename(profile.name if profile else "FM-1", fw_key, suffix)
    backup.write_file(path, f)
    return path


# -------------------------------------------------------------- index ---

@dataclass
class Meta:
    path: Path
    created: str
    firmware: str           # version string from the backup
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
    presets: int = -1        # used preset slots (-1: unknown, older index)
    damaged: str = ""        # not empty: file cannot be read (reason) – shown as "damaged"

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
    """"2 projects, 5 presets · 57 parts" – the same formula everywhere (list, browser, overview)."""
    parts = [ngettext("{n} project", "{n} projects", projects).format(n=projects)]
    if presets >= 0:
        parts.append(ngettext("{n} preset", "{n} presets", presets).format(n=presets))
    return ", ".join(parts) + " · " + ngettext("{n} part", "{n} parts", objects).format(n=objects)


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
            if not isinstance(f, dict) or f.get("format") not in (backup.FORMAT, "sloop-backup", backup.FM1OPEN_FORMAT):
                return None
            c = f.get("companion") or {}
            objs = f.get("objects") or []
            fw = f.get("firmware", "")
            key = c.get("firmware") or next((k for k in ("melodee", "karma", "sloop", "skiff", "jiant", "coralflower")
                                             if k in fw.lower()), "")
            if f.get("format") == backup.FM1OPEN_FORMAT:   # (M-VAVE's FM1 Open: its slots 1..16)
                key = key or backup.FM1OPEN_KEY
            jiant = backup.is_jiant(o.get("id", -1) for o in objs if isinstance(o, dict))
            coral = backup.is_coralflower(o.get("id", -1) for o in objs if isinstance(o, dict))
            try:                                   # count used presets once (stored in the index afterwards)
                from . import backup_inspect as _BI
                presets = len(_BI.inspect(f).used_presets)
            except Exception:                      # noqa: BLE001 – then without a number
                presets = -1
            e = {"presets": presets, "mtime": st.st_mtime, "size": st.st_size, "created": f.get("created") or f.get("date", ""),
                 "firmware": fw, "fw_key": key, "device": c.get("device", ""), "device_id": c.get("device_id", ""),
                 "note": c.get("note", ""), "labels": list(c.get("labels") or []), "pinned": bool(c.get("pinned")),
                 "kind": c.get("kind") or _kind_from_name(path.stem), "objects": len(objs),
                 "projects": sum(1 for o in objs if isinstance(o, dict) and (o.get("size") or o.get("len"))
                                 and (1 <= o.get("id", -1) <= 16 if f.get("format") == backup.FM1OPEN_FORMAT else
                                      backup.is_project(o.get("id", -1), jiant, coral)) and o.get("id") != 0),
                 "fmt": f.get("format")}
            idx[str(path)] = e
            _save_index()
    return Meta(path, e["created"], e["firmware"], e["fw_key"], e["device"], e["device_id"], e["note"], e["labels"],
                e["pinned"], e["kind"], e["size"], e["mtime"], e["objects"], e["projects"], e.get("fmt", backup.FORMAT),
                int(e.get("presets", -1)))


def damaged_meta(path: Path) -> Meta | None:
    """Meta for a *.json in the backup folder that cannot be read as JSON (truncated, broken).
    None if the file is readable (then it is a backup or something else)."""
    path = Path(path)
    try:
        st = path.stat()
        raw = path.read_bytes()
    except OSError as e:
        return Meta(path, "", "", "", "", "", "", [], False, "beschädigt", 0, 0.0, 0, 0, "",  # i18n: ignore
                    damaged=str(e.strerror))
    try:
        json.loads(raw.decode("utf-8"))
        return None
    except (ValueError, UnicodeDecodeError) as e:
        why = _("no valid JSON (truncated or damaged)") if raw else _("empty file")
        return Meta(path, "", "", "", path.parent.name, "", "", [], False, "beschädigt",  # i18n: ignore
                    st.st_size, st.st_mtime, 0, 0,
                    "", damaged=f"{why}: {type(e).__name__}")


def list_backups(folder: Path, recursive: bool = True, include_damaged: bool = False) -> list[Meta]:
    """Backups in the folder (newest first). include_damaged: also return unreadable files as Meta with .damaged."""
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
    """Change note / labels / pinned / kind in the companion block; the modification time stays."""
    path = Path(path)
    st = path.stat()
    try:
        f = json.loads(path.read_text("utf-8"))
    except ValueError:
        raise backup.BackupError(_("The file is not a readable backup (damaged) – note/labels cannot "
                                 "be saved."))
    if not isinstance(f, dict) or f.get("format") not in (backup.FORMAT, "sloop-backup"):
        raise backup.BackupError(_("The file is not a backup of the app or the web editor."))
    c = f.setdefault("companion", {})
    c.update(changes)
    # (SLOOP backups stay in the SLOOP editor's format); modification time stays
    backup.write_text_atomic(path, backup.dumps(f), keep_times=(st.st_atime_ns, st.st_mtime_ns))
    with _lock:                                      # index: the entry is now stale (same mtime possible!)
        _load_index().pop(str(path), None)
        _save_index()


# --------------------------------------------------------- retention ---

def retention(metas: list[Meta], keep_last: int, keep_monthly: bool = True) -> list[Meta]:
    """-> the automatic backups that may be removed."""
    autos = sorted([m for m in metas if m.automatic], key=lambda m: m.when, reverse=True)
    if keep_last <= 0:
        return []
    keep = set(id(m) for m in autos[:keep_last])
    keep |= {id(m) for m in autos if m.pinned}
    newest_fw: dict[str, Meta] = {}
    months: dict[str, Meta] = {}
    for m in autos:                              # newest first
        newest_fw.setdefault(m.fw_key, m)
        months.setdefault(m.when.strftime("%Y-%m"), m)
    keep |= {id(m) for m in newest_fw.values()}
    if keep_monthly:
        keep |= {id(m) for m in months.values()}
    # manual ones are never in autos – never deleted
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


# -------------------------------------------------------------- mirror ---

def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def os_error_text(e: BaseException) -> str:
    """OSError in plain language (without Qt, for log and status lines)."""
    import errno as _e
    code = getattr(e, "errno", None)
    path = getattr(e, "filename", None)
    where = f" ({path})" if path else ""
    if code in (_e.EACCES, _e.EPERM, _e.EROFS):
        return _("no write permission") + where
    if code == _e.ENOSPC:
        return _("the drive is full") + where
    if code == _e.ENOENT:
        return _("folder or file not found") + where
    if code in (_e.EIO, _e.ESTALE, _e.ENOTCONN, _e.EHOSTDOWN, _e.EHOSTUNREACH, _e.ENETUNREACH):
        return _("drive or network drive does not respond") + where
    if code is None:
        return str(e)
    return f"{getattr(e, 'strerror', '') or e}{where}"


def mirror_file(path: Path, root: Path, target: Path) -> Path:
    """Copy a file into the mirror (relative path as under root) and verify it.
    OSError with a German message if the target is missing (NAS not mounted) or the copy does not match."""
    path, target = Path(path), Path(target)
    if not target.is_dir():
        raise OSError(_("Mirror folder {folder} is not reachable – is the drive (NAS) mounted?").format(folder=target))
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
            raise OSError(_("Verifying the copy {name} failed – the copy in the mirror is not identical").format(
                name=dest.name))
        tmp.replace(dest)
        try:
            shutil.copystat(path, dest)
        except OSError:
            pass                                   # (some network drives do not allow setting the times)
    except OSError as e:
        if getattr(e, "errno", None) is None:
            raise
        raise OSError(f"{dest.name}: {os_error_text(e)}") from e
    return dest


def mirror_all(root: Path, target: Path) -> tuple[int, int, list[str]]:
    """mirror all backups under root -> (copied/verified, errors, messages)."""
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


# ----------------------------------------------------- status for the UI ---

@dataclass
class Status:
    text: str = field(default_factory=lambda: _("Automatic backups: nothing has happened yet."))
    last_path: Path | None = None
    last_time: float = 0.0
    mirror: str = ""
    history: list[str] = field(default_factory=list)

    def add(self, line: str):
        self.text = line
        self.history.insert(0, time.strftime("%H:%M ") + line)
        del self.history[30:]


STATUS = Status()


# ------------------------------------------------------------ zip sets ---

def export_zip(paths, dest: Path, root: Path) -> int:
    """Backups together with a table of contents (manifest.json with SHA-256) into a zip file."""
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
    """Check a zip (manifest, SHA-256, backup content) and file it under root. Existing files stay."""
    import zipfile
    from . import backup_inspect
    got, skipped = [], []
    src = Path(src)
    try:
        z = zipfile.ZipFile(src)
    except zipfile.BadZipFile:
        raise backup.BackupError(_("{name} is not a readable zip file (damaged or truncated). "
                                   "Please copy the file again.").format(name=src.name))
    with z:
        try:
            man = json.loads(z.read("manifest.json"))
        except KeyError:
            raise backup.BackupError(_("{name} has no table of contents (manifest.json) – this is not a "
                                       "backup collection of this app. Single backup files (.json) are added with "
                                       "“Add file” in the tab “List”.").format(name=src.name))
        except (ValueError, zipfile.BadZipFile) as e:
            raise backup.BackupError(_("The table of contents in {name} is damaged ({error}). "
                                       "Nothing was taken over.").format(name=src.name, error=type(e).__name__))
        if not isinstance(man, dict) or man.get("format") != "fm1companion-backupset":
            raise backup.BackupError(_("{name} is a zip file but not a backup collection of this app.").format(
                name=src.name))
        entries = man.get("backups")
        for e in entries if isinstance(entries, list) else []:
            name = e.get("file", "") if isinstance(e, dict) else ""
            if not isinstance(name, str) or not name:
                skipped.append(_("{name}: invalid entry in the table of contents").format(name=name or '?'))
                continue
            rel = Path(name).relative_to("backups") if name.startswith("backups/") else Path(Path(name).name)
            if ".." in rel.parts or rel.is_absolute():
                skipped.append(_("{name}: invalid path").format(name=name))
                continue
            try:
                data = z.read(name)
            except KeyError:
                skipped.append(_("{name}: missing in the zip").format(name=name))
                continue
            except zipfile.BadZipFile:
                skipped.append(_("{name}: damaged in the zip").format(name=name))
                continue
            if hashlib.sha256(data).hexdigest() != e.get("sha256"):
                skipped.append(_("{name}: checksum does not match").format(name=name))
                continue
            try:
                backup_inspect.inspect(data.decode("utf-8"))
            except Exception as ex:  # noqa: BLE001
                skipped.append(f"{name}: {ex}")
                continue
            dest = Path(root) / rel
            if dest.exists():
                skipped.append(_("{name}: already there").format(name=rel.name))
                continue
            backup.write_text_atomic(dest, data.decode("utf-8"))
            got.append(dest)
    return got, skipped
