# SPDX-License-Identifier: GPL-3.0-only
"""Storage: what the app writes over time, how big it is, and how to clean it up – without Qt.

Categories (``scan()``):

* app-generated, deletable: logs, downloaded firmware packages (main folder › Firmware/<firmware>/<version>),
  the installation journal's package copies, tool copies (git clones), editor copies, catalog images and
  GitHub data, sample-pack downloads (git checkouts – the samples themselves were copied to Samples),
  QtWebEngine cache, leftover temporary files, error reports and damaged settings copies;
* your data (deleted only one by one, on request): Studio states, the rescue folder's flash dumps and tool logs
  (``rescue_items``: the newest full dump of every device needs an extra confirmation), the web editors' site
  data (``webdata``: all at once – while the built-in browser runs, at the next start);
* kept (shown only): rescue tool, sample index, and your own folders in the main folder
  (Backups, Projekte, Samples, Kits, Aufnahmen, Downloads …) – the app never deletes those here.

Never deleted: anything an interrupted installation needs (the journal itself, the package copy and the
source package of the active journal entry, the tool copies while an entry is active), the newest original
firmware V15 (offline rescue), and nothing at all while an installation is running.

Automatic cleanup (``auto_clean``, at most once a day after the start, settings in modules["storage"]):
rotated logs older than N days, keep the newest N downloads per firmware (plus the versions on your
devices), keep the newest N journal copies and editor copies, caches and temporary files older than N days.
"""
from __future__ import annotations

import os
import shutil
import stat
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import applog, config
from .i18n import N_, _

DEFAULTS = {"auto": True, "log_days": 30, "firmware_keep": 0, "packages_keep": 3, "editor_keep": 2,
            "cache_days": 30, "last_auto": 0.0}
DAY = 86400.0
TEMP_PREFIX = "fm1c-"
# bug reports are named in the UI language (ui/crash.default_report_name): en, de, es
REPORT_GLOBS = ("FM-1-Companion-Fehlerbericht-*.zip", "FM-1-Companion-bug-report-*.zip",   # i18n: ignore (file names)
                "FM-1-Companion-informe-de-errores-*.zip", "systemcheck-*.txt")   # i18n: ignore (file names)
RESCUE_RESERVE = "stock-v15"          # the newest download of the original firmware stays (offline rescue)
MIN_TEMP_AGE = 3600.0                 # temporary files younger than this may still be in use


class Busy(RuntimeError):
    pass


def settings() -> dict:
    return config.module("storage", DEFAULTS)


# ------------------------------------------------------------ measuring ---

def tree_size(path: Path) -> tuple[int, int]:
    """(bytes, files) of a file or folder – symbolic links are counted, never followed."""
    try:
        st = os.lstat(path)
    except OSError:
        return 0, 0
    if not stat.S_ISDIR(st.st_mode):
        return st.st_size, 1
    total = files = 0
    for base, dirs, names in os.walk(path, followlinks=False):
        for n in names + [d for d in dirs if os.path.islink(os.path.join(base, d))]:
            try:
                total += os.lstat(os.path.join(base, n)).st_size
                files += 1
            except OSError:
                pass
    return total, files


def newest_mtime(path: Path) -> float:
    """modification time of the newest file in a folder (an empty folder: its own), or of the file"""
    try:
        own = os.lstat(path).st_mtime
    except OSError:
        return 0.0
    if not path.is_dir() or path.is_symlink():
        return own
    best = None
    for base, _dirs, names in os.walk(path, followlinks=False):
        for n in names:
            try:
                t = os.lstat(os.path.join(base, n)).st_mtime
            except OSError:
                continue
            best = t if best is None else max(best, t)
    return own if best is None else best


def human(n: float) -> str:
    from .i18n import decimal
    for unit in ("B", "KiB", "MiB", "GiB"):
        if abs(n) < 1024 or unit == "GiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{decimal(n, 1)} {unit}"
        n /= 1024
    return str(n)


# ------------------------------------------------------------ safety ---

def _journal():
    from .flash import journal
    return journal.Journal()


def install_running() -> bool:
    """an installation runs in this process or in another program window"""
    from .flash import journal
    if journal.Journal.busy():
        return True
    j = _journal()
    try:
        pid = int(j.lockfile.read_text().strip() or 0)
    except (OSError, ValueError):
        return False
    return bool(pid) and journal._alive(pid)


def resume_possible() -> bool:
    """the journal has an active (interrupted) installation – it may still be finished"""
    return bool(_journal().active)


def protected_paths() -> set[Path]:
    """files an interrupted installation needs (package copy and source of the active journal entry)"""
    act = _journal().active or {}
    out = set()
    for k in ("package", "source"):
        if act.get(k):
            out.add(_real(Path(act[k])))
    return out


def _real(p: Path) -> Path:
    try:
        return p.resolve()
    except OSError:
        return p


def _contains_protected(item: Path, prot: set[Path]) -> bool:
    r = _real(item)
    return any(p == r or r in p.parents for p in prot)


def remove(item: Path) -> int:
    """delete a file or folder (links themselves, never their target). -> freed bytes"""
    size, _n = tree_size(item)
    if item.is_dir() and not item.is_symlink():
        shutil.rmtree(item)
    else:
        item.unlink()
    return size


# ------------------------------------------------------------ categories ---

@dataclass
class Category:
    key: str
    title: str                     # msgid (translate with _() where shown)
    detail: str                    # msgid
    kind: str                      # "clean" (app-generated), "data" (yours, one by one), "keep" (shown only)
    folder: Path | None = None     # what "Open folder" opens
    size: int = 0
    files: int = 0
    cleanable: int = 0             # bytes "Delete" would free (protected parts excluded)

    @property
    def deletable(self) -> bool:
        return self.kind == "clean"


def _children(d: Path) -> list[Path]:
    try:
        return sorted(d.iterdir()) if d.is_dir() else []
    except OSError:
        return []


def _firmware_keys() -> set[str]:
    from . import firmware
    try:
        return {s.key for s in firmware.catalog() + list(firmware.STOCK)}
    except Exception:  # noqa: BLE001 – a broken own entry must not stop the cleanup
        return set()


def firmware_downloads() -> dict[str, list[Path]]:
    """firmware key -> version folders (Firmware/<key>/<tag>), newest first. Only folders of known firmwares
    – files you put into the firmware folder yourself are not touched."""
    root = config.load().firmware_dir
    keys = _firmware_keys()
    out = {}
    for kd in _children(root):
        if kd.is_dir() and not kd.is_symlink() and kd.name in keys:
            tags = [t for t in _children(kd) if t.is_dir() and not t.is_symlink()]
            if tags:
                out[kd.name] = sorted(tags, key=newest_mtime, reverse=True)
    return out


def _device_versions() -> set[tuple[str, str]]:
    """(firmware, version) of the known devices – their packages stay with the automatic cleanup"""
    import json
    from .devices import PROFILES_FILE
    try:
        raw = json.loads(PROFILES_FILE.read_text("utf-8"))
        return {(p.get("firmware", ""), p.get("firmware_version", "")) for p in raw.get("profiles", [])
                if isinstance(p, dict)}
    except (OSError, ValueError, AttributeError):
        return set()


def kept_packages_dir() -> Path:
    from .flash import journal
    return journal.root() / "packages"


def repos_dir() -> Path:
    from . import firmware
    return firmware.REPOS_DIR


def editor_dir() -> Path:
    return config.DATA_DIR / "editor"


def catalog_cache_parts() -> list[Path]:
    """images and GitHub answers of the catalog (the catalog itself and the News list stay)"""
    d = config.CACHE_DIR / "catalog"
    out = [d / "thumbs", d / "github"]
    out += sorted(config.CACHE_DIR.glob("releases-*.json")) if config.CACHE_DIR.is_dir() else []
    return [p for p in out if p.exists()]


def samplepacks_dir() -> Path:
    return config.CACHE_DIR / "samplepacks"


def webengine_cache_dir() -> Path:
    return config.CACHE_DIR / "webengine"


def temp_files(min_age: float = MIN_TEMP_AGE, now: float | None = None) -> list[Path]:
    """leftover temporary files/folders of the app (fm1c-* in the temp folder, yours, not in use)"""
    now = now or time.time()
    out = []
    for p in _children(Path(tempfile.gettempdir())):
        if not p.name.startswith(TEMP_PREFIX) or p.name.startswith("fm1c-simbuild"):   # (build tools' work dir)
            continue
        try:
            st = os.lstat(p)
        except OSError:
            continue
        if st.st_uid != os.getuid():          # e.g. the rescue's root folder – not ours to delete
            continue
        if now - newest_mtime(p) >= min_age:
            out.append(p)
    return out


def reports() -> list[Path]:
    home = Path.home()
    out = []
    for g in REPORT_GLOBS:
        out += sorted(home.glob(g))
    out += sorted(config.CONFIG_DIR.glob("config.json.kaputt-*")) if config.CONFIG_DIR.is_dir() else []  # i18n: ignore
    return [p for p in out if p.is_file()]


def studio_states() -> list[tuple[str, Path, int, float]]:
    """(profile folder, folder, bytes, modified) of the saved Studio states"""
    base = config.DATA_DIR / "studio"
    out = []
    for d in _children(base):
        if d.is_dir() and not d.is_symlink():
            size, _n = tree_size(d)
            out.append((d.name, d, size, newest_mtime(d)))
    return out


def studio_state_in_use(folder_name: str, service=None) -> bool:
    """the running Studio twin works on this state (deleting it now: the autosave writes it again)"""
    return bool(service is not None and getattr(service, "st", None) is not None
                and service.profile_folder() == folder_name)


def delete_studio_state(folder_name: str) -> int:
    base = config.DATA_DIR / "studio"
    d = base / folder_name
    if d.parent != base or folder_name in ("", ".", "..") or not d.is_dir():
        raise FileNotFoundError(folder_name)
    return remove(d)


# ------------------------------------------------------------ rescue folder ---

FULL_DUMP_SIZE = 0x100000             # a complete flash dump of the FM-1 (1 MiB)
KEY_AREA = (0xFF000, 0x100000)        # key_mac area: differs from device to device (fingerprint of the dump)
SESSION_PREFIX = "fm1-recovery-"      # the rescue tool's session folders (full-flash-before.bin, backup.json …)
SIM_PARTS = ("simulation", "Beispiel-Dumps")   # i18n: ignore (folder names on disk: simulation, example dumps)
RESCUE_BUSY_AGE = 600.0               # younger items may still be written by a running tool


def rescue_dir() -> Path:
    return config.load().root / "Rettung"   # i18n: ignore (folder name on disk)


@dataclass
class RescueItem:
    path: Path
    kind: str                  # "session" (folder of a tool run), "dump" (.bin), "logs" (tool logs folder)
    size: int
    mtime: float
    full: bool = False         # holds a complete flash dump (1 MiB)
    device: str = ""           # fingerprint of the device from the dump ("" = not recognisable)
    simulated: bool = False    # simulation / example dump (not from a real device)
    newest: bool = False       # the newest (or only) full dump of its device – the safety net

    @property
    def name(self) -> str:
        try:
            return self.path.relative_to(rescue_dir()).as_posix()
        except ValueError:
            return self.path.name


def dump_fingerprint(path: Path) -> str:
    """short fingerprint of the device a 1 MiB dump comes from (its key_mac area) – "" if empty/unreadable"""
    try:
        with open(path, "rb") as f:
            f.seek(KEY_AREA[0])
            area = f.read(KEY_AREA[1] - KEY_AREA[0])
    except OSError:
        return ""
    if len(area) != KEY_AREA[1] - KEY_AREA[0] or len(set(area)) <= 1:
        return ""
    import hashlib
    return hashlib.sha256(area).hexdigest()[:12]


def _full_dump(p: Path) -> bool:
    try:
        st = os.lstat(p)
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode) and st.st_size == FULL_DUMP_SIZE


def _rescue_item(p: Path, kind: str, base: Path) -> RescueItem:
    size, _n = tree_size(p)
    it = RescueItem(p, kind, size, newest_mtime(p))
    rel = p.relative_to(base).parts
    it.simulated = "-dryrun" in p.name or any(x in SIM_PARTS for x in rel) or p.name.startswith("simulierter")  # i18n: ignore
    dumps = [p] if kind == "dump" else (
        [p / n for n in ("full-flash-before.bin", "full-flash-after.bin")] if kind == "session" else [])
    for d in dumps:
        if _full_dump(d):
            it.full = True
            it.device = it.device or dump_fingerprint(d)
    return it


def rescue_items(base: Path | None = None) -> list[RescueItem]:
    """what the rescue folder holds, newest first: the tool's session folders, loose dump files (.bin) and the
    tool logs. Marks the newest full dump of every device (unrecognisable devices count as one device)."""
    base = Path(base) if base is not None else rescue_dir()
    out: list[RescueItem] = []

    def walk(d: Path, depth: int):
        for c in _children(d):
            if c.is_symlink():
                continue
            if c.is_dir():
                if c.name.startswith(SESSION_PREFIX):
                    out.append(_rescue_item(c, "session", base))
                elif c.name == "logs":
                    out.append(_rescue_item(c, "logs", base))
                elif depth < 3:
                    walk(c, depth + 1)
            elif c.suffix.lower() == ".bin":
                out.append(_rescue_item(c, "dump", base))
    if base.is_dir():
        walk(base, 0)
    out.sort(key=lambda i: i.mtime, reverse=True)
    seen: set[str] = set()
    for it in out:                                  # newest first
        if it.full and not it.simulated and it.device not in seen:
            seen.add(it.device)
            it.newest = True
    return out


def delete_rescue_item(path: Path, base: Path | None = None, now: float | None = None) -> int:
    """delete ONE item of the rescue folder (never automatically – the UI asks, twice for the newest full dump
    of a device). Refused while an installation runs and for items that are still being written. -> bytes"""
    _guard()
    base = _real(Path(base) if base is not None else rescue_dir())
    p = Path(path)
    if p.is_symlink() or _real(p) == base or base not in _real(p).parents:
        raise FileNotFoundError(str(path))
    if not p.exists():
        raise FileNotFoundError(str(path))
    if (now or time.time()) - newest_mtime(p) < RESCUE_BUSY_AGE:
        raise Busy(_("“{name}” was written a moment ago – a rescue may still be running. Try again in a few "
                     "minutes.").format(name=p.name))
    return remove(p)


# ------------------------------------------------------------ web editor data ---

def webdata_dir() -> Path:
    return config.DATA_DIR / "webengine"


def _webdata_mark() -> Path:
    return config.DATA_DIR / "webengine-clear-on-start"


def webdata_clear_pending() -> bool:
    return _webdata_mark().exists()


def request_webdata_clear():
    """the built-in browser is running: its storage is cleared at the next start, before it opens it"""
    m = _webdata_mark()
    m.parent.mkdir(parents=True, exist_ok=True)
    m.write_text(str(time.time()), "utf-8")


def cancel_webdata_clear():
    _webdata_mark().unlink(missing_ok=True)


def clear_webdata_now() -> Result:
    """delete the web editors' site data (cookies, local storage, IndexedDB …). Only while the built-in browser
    has not opened its storage in this process (ui/web.py takes care of that)."""
    res = Result()
    _remove_all(_children(webdata_dir()), res)
    if not res.errors:
        cancel_webdata_clear()
    return res


def apply_pending_webdata_clear() -> Result | None:
    """at the start of the built-in browser: carry out a "Clear" that was requested while it was running"""
    if not webdata_clear_pending():
        return None
    return clear_webdata_now()


def candidates(key: str, now: float | None = None) -> list[Path]:
    """what "Delete" removes in a category (protected items already left out)"""
    prot = protected_paths()
    if key == "logs":
        d = applog.log_dir()
        return [p for p in _children(d) if p.is_file() and p.name != applog.log_file().name]
    if key == "firmware":
        out = []
        for k, tags in firmware_downloads().items():
            for i, t in enumerate(tags):
                if k == RESCUE_RESERVE and i == 0:
                    continue
                if not _contains_protected(t, prot):
                    out.append(t)
        return out
    if key == "packages":
        return [p for p in _children(kept_packages_dir()) if p.is_file() and not _contains_protected(p, prot)]
    if key == "repos":
        return [] if resume_possible() else [p for p in _children(repos_dir()) if p.is_dir()]
    if key == "editor":
        return [t for k in _children(editor_dir()) if k.is_dir() for t in _children(k) if t.is_dir()]
    if key == "catalog":
        return catalog_cache_parts()
    if key == "samplepacks":
        return [p for p in _children(samplepacks_dir()) if p.is_dir()]
    if key == "webengine":
        return _children(webengine_cache_dir())
    if key == "temp":
        return temp_files(now=now)
    if key == "reports":
        return reports()
    return []


def _cat(key, title, detail, kind, folder) -> Category:
    return Category(key, title, detail, kind, folder)


def categories() -> list[Category]:
    s = config.load()
    from .flash import journal
    return [
        _cat("logs", N_("Logs"), N_("The app's log (at most 5 × 1 MB, rotating). “Delete” removes the older "
                                    "parts and empties the current log."), "clean", applog.log_dir()),
        _cat("firmware", N_("Downloaded firmware"),
             N_("Packages the app downloaded (main folder › Firmware). They are downloaded again when needed. "
                "Kept: the newest original firmware V15 (for a rescue without internet) and the package of an "
                "interrupted installation. Files you put there yourself stay."), "clean", s.firmware_dir),
        _cat("packages", N_("Copies of installed packages"),
             N_("The installation journal keeps a copy of every package it installs (for finishing an "
                "interrupted installation). The copy of an interrupted installation is kept."), "clean",
             kept_packages_dir()),
        _cat("repos", N_("Firmware tools (git copies)"),
             N_("Installers and tools of the firmwares, fetched from GitHub. Fetched again on the next "
                "installation (needs internet). Kept while an interrupted installation can be finished."),
             "clean", repos_dir()),
        _cat("editor", N_("Editor copies"), N_("Web editors of the firmware versions. Prepared again when the "
                                               "editor is opened – close the editor first."), "clean",
             editor_dir()),
        _cat("catalog", N_("Catalog images and GitHub data"),
             N_("Screenshots, stars and release lists from GitHub. The catalog itself and the News list stay."),
             "clean", config.CACHE_DIR / "catalog"),
        _cat("samplepacks", N_("Sample pack downloads"),
             N_("Contents lists of the sample packs (and git copies left by older versions or an interrupted "
                "download – the app now deletes the copy right after copying). The samples you downloaded are in "
                "your Samples folder and stay there."), "clean", samplepacks_dir()),
        _cat("webengine", N_("Web cache"), N_("Cache of the built-in browser (editors, catalog pages)."),
             "clean", webengine_cache_dir()),
        _cat("temp", N_("Temporary files"),
             N_("Leftovers of previews and recordings in the temporary folder (only files older than an hour)."),
             "clean", Path(tempfile.gettempdir())),
        _cat("reports", N_("Error reports"),
             N_("Error reports and system check reports in your home folder, damaged settings copies."),
             "clean", Path.home()),
        _cat("studio", N_("Studio states"),
             N_("Your work in the Studio, one state per device. Only deleted one by one, on request."),
             "data", config.DATA_DIR / "studio"),
        _cat("webdata", N_("Web editor storage"),
             N_("Saved data of the built-in browser (cookies, local storage) – web editors may keep your settings "
                "there. Only cleared on request, all at once."),
             "data", webdata_dir()),
        _cat("rescuedumps", N_("Rescue dumps and tool logs"),
             N_("Full flash backups and logs of the rescue tool in the main folder › Rettung – your safety net. "
                "Only deleted one by one, on request; the newest full backup of a device needs an extra "
                "confirmation."), "data", rescue_dir()),
        _cat("rescue", N_("Rescue tool and installation journal"),
             N_("Needed for a rescue and for finishing an interrupted installation. Kept."), "keep",
             journal.root()),
        _cat("userdata", N_("Your files in the main folder"),
             N_("Backups, projects, samples, kits, recordings, rescue dumps and downloads. The app never "
                "deletes them here – automatic backups follow the setting “Keep automatic backups”, rescue dumps "
                "have their own row."),
             "keep", s.root),
    ]


def _keep_size(key: str) -> int:
    if key == "rescue":
        from .flash import journal
        total, _n = tree_size(journal.root())
        return total - tree_size(kept_packages_dir())[0]
    if key == "userdata":
        root = config.load().root
        total, _n = tree_size(root)
        fw, _n = tree_size(config.load().firmware_dir)
        return total - fw
    return 0


def scan(now: float | None = None, include_user: bool = True) -> list[Category]:
    """all categories with measured sizes (runs in the background – walks the folders).
    include_user=False: without "your files in the main folder" (the slowest walk)"""
    out = []
    for c in categories():
        if c.key == "userdata" and not include_user:
            continue
        if c.key in ("rescue", "userdata"):
            c.size = _keep_size(c.key)
        elif c.key == "logs":
            c.size, c.files = tree_size(applog.log_dir())
        elif c.key == "firmware":
            for tags in firmware_downloads().values():
                for t in tags:
                    b, n = tree_size(t)
                    c.size += b
                    c.files += n
        elif c.key == "catalog":
            for p in catalog_cache_parts():
                b, n = tree_size(p)
                c.size += b
                c.files += n
        elif c.key == "temp":
            for p in temp_files(0, now):
                b, n = tree_size(p)
                c.size += b
                c.files += n
        elif c.key == "reports":
            for p in reports():
                b, n = tree_size(p)
                c.size += b
                c.files += n
        elif c.folder is not None:
            c.size, c.files = tree_size(c.folder)
        if c.deletable:
            c.cleanable = sum(tree_size(p)[0] for p in candidates(c.key, now))
            if c.key == "logs":
                c.cleanable += tree_size(applog.log_file())[0]
        out.append(c)
    return out


def app_total(cats: list[Category] | None = None) -> tuple[int, int]:
    """(bytes the app keeps besides your own files, of which can be cleaned up)"""
    cats = cats if cats is not None else scan(include_user=False)
    own = [c for c in cats if c.key not in ("userdata", "rescuedumps")]   # (both in your main folder)
    return sum(c.size for c in own), sum(c.cleanable for c in own)


# ------------------------------------------------------------ cleaning ---

@dataclass
class Result:
    freed: int = 0
    removed: int = 0
    errors: list[str] = field(default_factory=list)

    def add(self, other: "Result"):
        self.freed += other.freed
        self.removed += other.removed
        self.errors += other.errors


def _remove_all(items, res: Result):
    for p in items:
        try:
            res.freed += remove(p)
            res.removed += 1
        except FileNotFoundError:
            pass
        except OSError as e:
            res.errors.append(f"{p.name}: {e.strerror or e}")


def _truncate_log(res: Result):
    f = applog.log_file()
    try:
        size = f.stat().st_size
    except OSError:
        return
    try:
        with open(f, "r+b") as fh:          # the handler appends (O_APPEND): it simply goes on at 0
            fh.truncate(0)
        res.freed += size
    except OSError as e:
        res.errors.append(f"{f.name}: {e.strerror or e}")


def _guard():
    if install_running():
        raise Busy(_("An installation is running – nothing is deleted until it has finished."))


def clear(key: str, now: float | None = None) -> Result:
    """“Delete …” of one category (only app-generated data, protected items stay)."""
    _guard()
    res = Result()
    _remove_all(candidates(key, now), res)
    if key == "logs":
        _truncate_log(res)
    return res


def _older(items, days: float, now: float) -> list[Path]:
    return [p for p in items if now - newest_mtime(p) > days * DAY]


def auto_clean(cfg: dict | None = None, now: float | None = None) -> Result:
    """The automatic cleanup with the settings in modules["storage"] (or cfg)."""
    cfg = {**DEFAULTS, **(cfg if cfg is not None else settings())}
    now = now or time.time()
    res = Result()
    if install_running():
        return res
    prot = protected_paths()
    log_days, cache_days = float(cfg["log_days"]), float(cfg["cache_days"])
    if log_days > 0:
        _remove_all(_older(candidates("logs"), log_days, now), res)
    keep = int(cfg["firmware_keep"])
    if keep > 0:
        mine = _device_versions()
        for k, tags in firmware_downloads().items():
            drop = [t for t in tags[keep:] if (k, t.name) not in mine and not _contains_protected(t, prot)]
            _remove_all(drop, res)
    keep = int(cfg["packages_keep"])
    if keep > 0:
        pk = sorted((p for p in _children(kept_packages_dir()) if p.is_file()), key=newest_mtime, reverse=True)
        _remove_all([p for p in pk[keep:] if not _contains_protected(p, prot)], res)
    keep = int(cfg["editor_keep"])
    if keep > 0:
        for k in _children(editor_dir()):
            tags = sorted((t for t in _children(k) if t.is_dir()), key=newest_mtime, reverse=True)
            _remove_all(tags[keep:], res)
    if cache_days > 0:
        cat = config.CACHE_DIR / "catalog"
        items = _children(cat / "thumbs") + [p for p in _children(cat / "github") if p.name != "stars.json"]
        _remove_all(_older(items, cache_days, now), res)
        _remove_all(_older([p for p in _children(samplepacks_dir()) if p.is_dir()], cache_days, now), res)
        _remove_all(temp_files(min(cache_days, 1.0) * DAY, now), res)
    return res


def auto_due(now: float | None = None) -> bool:
    cfg = settings()
    return bool(cfg.get("auto", True)) and (now or time.time()) - float(cfg.get("last_auto") or 0) > DAY


def run_auto(now: float | None = None) -> Result | None:
    """at the start (background): once a day, remembers when it ran"""
    if not auto_due(now):
        return None
    res = auto_clean(now=now)
    settings()["last_auto"] = now or time.time()
    config.save()
    return res
