# SPDX-License-Identifier: GPL-3.0-only
"""Settings and folders.

Everything the user touches directly (backups, projects, samples, recordings) lives
in a visible folder (default ~/FM-1). Internal data (firmware cache,
tool repos, sample index) lives under ~/.local/share/fm1-companion per XDG.
"""
from __future__ import annotations

import dataclasses
import json
import os
import shutil
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

from . import APP_ID
from .i18n import N_


class Msg:
    """a message translated only when shown: the settings are read while the UI language is still being
    determined (i18n reads the language setting from here), so load() must not translate anything itself."""

    def __init__(self, msgid: str, kind: str = "", **kw):
        self.msgid, self.kind, self.kw = msgid, kind, kw

    def __str__(self) -> str:
        from .i18n import _
        return _(self.msgid).format(**self.kw)

    def __repr__(self) -> str:
        return f"Msg({self.msgid!r}, {self.kw!r})"


def _xdg(var: str, default: str) -> Path:
    v = os.environ.get(var)
    return Path(v) if v else Path.home() / default


CONFIG_DIR = _xdg("XDG_CONFIG_HOME", ".config") / APP_ID
DATA_DIR = _xdg("XDG_DATA_HOME", ".local/share") / APP_ID
CACHE_DIR = _xdg("XDG_CACHE_HOME", ".cache") / APP_ID
CONFIG_FILE = CONFIG_DIR / "config.json"
BACKUP_FILE = CONFIG_DIR / "config.json.bak"


@dataclass
class Settings:
    # Updates
    channel: str = "stable"                # "stable" (default) or "rc" (release candidates)
    check_on_start: bool = True
    auto_backup_before_update: bool = True
    keep_backups: int = 20                 # per device, 0 = keep all
    # careful USB mode for flashing (flash/robust.py): "auto" (when the FM-1 is behind a USB hub), "always", "never"
    careful_usb: str = "auto"
    # folders
    user_root: str = str(Path.home() / "FM-1")
    sample_roots: list[str] = field(default_factory=list)   # folders the sample library searches
    # appearance
    theme: str = "system"                  # "system", "light", "dark"
    # Language of the user interface: "system" (system locale if translated, else English), "de", "en", …
    language: str = "system"
    # recording
    record_mode: str = "auto"              # "auto", "stereo", "tracks"
    record_open_audacity: bool = True
    audacity_cmd: str = "audacity"
    # Editor
    editor_zoom: float = 1.0
    # Samples
    sample_default_normalize: bool = True
    sample_default_trim: bool = True
    # firmware catalog: additional firmwares entered by the user
    extra_firmwares: list[dict] = field(default_factory=list)
    # last active device (profile ID)
    last_device: str = ""
    # window
    window_geometry: str = ""
    # one-time risk notice before the first installation/rescue confirmed (version of the text, 0 = never)
    risk_ack: int = 0
    # settings of individual sections of the suite, one dictionary per section
    # (e.g. modules["catalog"]["show_beta"]). Access via config.module(name).
    modules: dict = field(default_factory=dict)

    # ---- derived folders ----
    @property
    def root(self) -> Path:
        return Path(os.path.expanduser(self.user_root))

    def folder(self, name: str, strict: bool = False) -> Path:
        """Subfolder in the main folder (created). strict=False (the properties backups_dir etc.): a
        write error (e.g. read-only main folder) does not abort – the path is still returned,
        the error is recorded in folder_problems; only the actual write then reports it understandably."""
        p = self.root / name
        try:
            p.mkdir(parents=True, exist_ok=True)
            folder_problems.pop(name, None)
        except OSError as e:
            folder_problems[name] = f"{p}: {e.strerror or e}"
            if strict:
                raise
        return p

    @property
    def backups_dir(self) -> Path: return self.folder("Backups")
    @property
    def projects_dir(self) -> Path: return self.folder("Projekte")
    @property
    def samples_dir(self) -> Path: return self.folder("Samples")
    @property
    def kits_dir(self) -> Path: return self.folder("Kits")
    @property
    def recordings_dir(self) -> Path: return self.folder("Aufnahmen")
    @property
    def firmware_dir(self) -> Path: return self.folder("Firmware")

    def all_sample_roots(self) -> list[Path]:
        roots = [self.samples_dir] + [Path(os.path.expanduser(p)) for p in self.sample_roots]
        out, seen = [], set()
        for r in roots:
            k = str(r.resolve()) if r.exists() else str(r)
            if k not in seen:
                seen.add(k)
                out.append(r)
        return out


_lock = threading.Lock()
_settings: Settings | None = None
folder_problems: dict[str, str] = {}     # folder name -> error when creating it (main folder read-only …)
# what was noticed while loading (for a one-time notice in the UI)
load_problems: list[Msg] = []
broken_file: Path | None = None          # unreadable config.json was renamed to this
read_error: str = ""                     # settings file not readable (permissions, folder missing/is a file)
_read_exc: OSError | None = None         # (the reason for it, translated only in problems_text())


def _expected(f) -> str:
    t = f.type if isinstance(f.type, str) else getattr(f.type, "__name__", str(f.type))
    return t.replace(" ", "")


def _coerce(name: str, typ: str, v, default):
    """Check a value; (value, problem (Msg) or None)"""
    if typ == "bool":
        return (v, None) if isinstance(v, bool) else (default, Msg(N_("{name}: not a yes/no value"), name=name))
    if typ == "int":
        return ((v, None) if isinstance(v, int) and not isinstance(v, bool)
                else (default, Msg(N_("{name}: not a number"), name=name)))
    if typ == "float":
        return ((float(v), None) if isinstance(v, (int, float)) and not isinstance(v, bool)
                else (default, Msg(N_("{name}: not a number"), name=name)))
    if typ == "str":
        return (v, None) if isinstance(v, str) else (default, Msg(N_("{name}: not a text"), name=name))
    if typ.startswith("list"):
        if not isinstance(v, list):
            return default, Msg(N_("{name}: not a list"), name=name)
        inner = typ[5:-1] if typ.startswith("list[") else ""
        want = {"str": str, "dict": dict}.get(inner)
        if want is None:
            return v, None
        good = [x for x in v if isinstance(x, want)]
        return good, (None if len(good) == len(v) else
                      Msg(N_("{name}: {n} invalid entries removed"), name=name, n=len(v) - len(good)))
    if typ == "dict":
        return (v, None) if isinstance(v, dict) else (default, Msg(N_("{name}: not a dictionary"), name=name))
    return v, None


def from_dict(raw) -> tuple[Settings, list[Msg]]:
    """Settings from a (possibly broken) dictionary – each field checked individually, otherwise the default."""
    s = Settings()
    problems: list[Msg] = []
    if not isinstance(raw, dict):
        return s, [Msg(N_("the file contains no JSON object"))]
    fields_ = {f.name: f for f in dataclasses.fields(Settings)}
    for k, v in raw.items():
        f = fields_.get(k)
        if f is None or k.startswith("_"):
            continue
        val, prob = _coerce(k, _expected(f), v, getattr(s, k))
        setattr(s, k, val)
        if prob:
            problems.append(prob)
    if isinstance(s.modules, dict):
        for k in [k for k, v in s.modules.items() if not isinstance(v, dict)]:
            s.modules.pop(k)
            problems.append(Msg(N_("{name}: not a dictionary"), name=f"modules.{k}"))
    if not s.user_root.strip():
        s.user_root = Settings().user_root
        problems.append(Msg(N_("{name}: empty"), name="user_root"))
    return s, problems


def _why(e: OSError) -> str:
    """Operating system errors in plain language (the strerror texts are English). Do not use in load()."""
    import errno as _e
    from .i18n import _
    return {_e.EACCES: _("no permission"), _e.EPERM: _("no permission"),
            _e.EROFS: _("the drive is write-protected"), _e.ENOSPC: _("the drive is full"),
            _e.ENOTDIR: _("part of the path is a file instead of a folder"),
            _e.EISDIR: _("there is a folder instead of the file"), _e.EDQUOT: _("your storage quota is full"),
            _e.EIO: _("read/write error of the drive")}.get(getattr(e, "errno", None), e.strerror or str(e))


def _read_json(p: Path):
    return json.loads(p.read_text("utf-8"))


def load() -> Settings:
    global _settings, broken_file, read_error, _read_exc
    with _lock:
        if _settings is None:
            s = Settings()
            raw = None
            try:
                raw = _read_json(CONFIG_FILE)
            except FileNotFoundError:
                pass
            except (IsADirectoryError, NotADirectoryError, PermissionError) as e:
                # not readable (permissions, ~/.config is a file …) – NOT "damaged" and do not rename anything
                read_error, _read_exc = f"{CONFIG_FILE}: {e.strerror or e}", e
            except (OSError, ValueError, UnicodeDecodeError) as e:
                # unreadable: set aside (do not silently overwrite) and – if present – use the backup
                stamp = time.strftime("%Y-%m-%d_%H%M%S")
                dest = CONFIG_FILE.with_name(f"{CONFIG_FILE.name}.kaputt-{stamp}")
                try:
                    CONFIG_FILE.replace(dest)
                    broken_file = dest
                except OSError:
                    broken_file = CONFIG_FILE
                load_problems.append(Msg(N_("The settings file was damaged ({error})."), "broken",
                                         error=type(e).__name__))
                try:
                    raw = _read_json(BACKUP_FILE)
                    load_problems.append(Msg(N_("The last backup (config.json.bak) was loaded."), "backup"))
                except (OSError, ValueError, UnicodeDecodeError):
                    raw = None
            if raw is not None:
                s, probs = from_dict(raw)
                load_problems.extend(probs)
            demo_root = os.environ.get("FM1C_DEMO_ROOT")
            if demo_root:                      # demo mode: separate main folder, real data stays untouched
                s.user_root = demo_root
            _settings = s
        return _settings


_save_lock = threading.Lock()
save_error: str = ""                     # last error while saving ("" = saved last time)
save_listeners: list = []                # fn(text) – called on EVERY failed save (any thread)


def _write_private(path: Path, text: str) -> None:
    """Write a file readable only by the user (0600) – the settings may contain e.g. a GitHub token.
    The permissions are fixed at creation time (no short window with 0644)."""
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.fchmod(fd, 0o600)                   # independent of the umask
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            fd = -1
            f.write(text)
    finally:
        if fd >= 0:
            os.close(fd)


def save(s: Settings | None = None) -> bool:
    """Write the settings (atomically, with a backup of the previous state). -> True if saved.

    Never raises: if the folder is not writable (disk full, permissions, ~/.config is a file), the
    settings remain in effect until exit; the error is in ``save_error`` and goes to ``save_listeners``
    (the main window reports it once). Previously, e.g., even the first start crashed with a traceback."""
    global save_error
    s = s or load()
    with _save_lock:                           # two threads must not replace each other's .tmp file
        tmp = CONFIG_FILE.with_suffix(".tmp")
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            _write_private(tmp, json.dumps(asdict(s), indent=2, ensure_ascii=False))
            if CONFIG_FILE.exists():
                try:                           # keep the previous state (way back in case of a broken file)
                    _read_json(CONFIG_FILE)
                    shutil.copy2(CONFIG_FILE, BACKUP_FILE)
                    os.chmod(BACKUP_FILE, 0o600)
                except (OSError, ValueError, UnicodeDecodeError):
                    pass
            tmp.replace(CONFIG_FILE)
        except OSError as e:
            try:
                tmp.unlink()
            except OSError:
                pass
            from .i18n import _
            save_error = _("The settings could not be saved ({folder}: {reason}). They apply until you quit the "
                           "app.").format(folder=CONFIG_FILE.parent, reason=_why(e))
            import logging
            logging.getLogger("fm1companion.config").warning(save_error)
            for fn in list(save_listeners):
                try:
                    fn(save_error)
                except Exception:  # noqa: BLE001 – a listener must not disturb saving
                    pass
            return False
        save_error = ""
        return True


def problems_text() -> str:
    """one-time notice for the UI ("" = all fine)"""
    from .i18n import _
    fields_ = [str(p) for p in load_problems if getattr(p, "kind", "") not in ("broken", "backup")]
    if broken_file is None and not fields_ and not read_error:
        return ""
    if read_error and broken_file is None:
        where = f"{CONFIG_FILE}: {_why(_read_exc)}" if _read_exc is not None else read_error
        head = _("The settings file is not readable ({error}). The default settings apply; "
                 "check the permissions of your folder ~/.config.").format(error=where)
    elif broken_file is not None:
        head = (_("The settings file was damaged and was put aside as “{name}”.").format(name=broken_file.name) + " "
                + (_("The last backup (config.json.bak) was loaded.")
                   if any(getattr(p, "kind", "") == "backup" for p in load_problems)
                   else _("The default settings apply again.")))
    else:
        head = _("The settings contained invalid values; they were replaced by default values.")
    return head + (" " + _("Affected: {fields}.").format(fields="; ".join(fields_[:6])) if fields_ else "")


def module(name: str, defaults: dict | None = None) -> dict:
    """Settings of a section (mutable; call config.save() afterwards).
    Missing keys are filled in from `defaults`, existing ones stay."""
    s = load()
    if not isinstance(s.modules, dict):
        s.modules = {}
    d = s.modules.get(name)
    if not isinstance(d, dict):
        d = s.modules[name] = {}
    for k, v in (defaults or {}).items():
        d.setdefault(k, v)
    return d


def reset_module(name: str, defaults: dict, keep=()) -> dict:
    """Put the settings of a section back to `defaults` – IN PLACE (pages hold the dictionary), the keys in
    `keep` stay (e.g. a token, a wishlist, MIDI learn assignments). Call config.save() afterwards."""
    import copy
    d = module(name)
    kept = {k: d[k] for k in keep if k in d}
    d.clear()
    d.update(copy.deepcopy(defaults))
    d.update(kept)
    return d


def folder(name: str) -> Path:
    """visible subfolder in the main folder (e.g. "DX7", "MIDI-Export"), gets created (OSError if not possible)."""
    return load().folder(name, strict=True)


def ensure_dirs() -> None:
    for d in (CONFIG_DIR, DATA_DIR, CACHE_DIR):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    s = load()
    for f in ("backups_dir", "projects_dir", "samples_dir", "kits_dir", "recordings_dir", "firmware_dir"):
        getattr(s, f)
