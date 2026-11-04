# SPDX-License-Identifier: GPL-3.0-only
"""Einstellungen und Ordner.

Alles, was der Nutzer selbst anfasst (Backups, Projekte, Samples, Aufnahmen), liegt
in einem sichtbaren Ordner (Standard ~/FM-1). Interne Daten (Firmware-Cache,
Werkzeug-Repos, Sample-Index) liegen nach XDG unter ~/.local/share/fm1-companion.
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
    channel: str = "stable"                # "stable" (Standard) oder "rc" (Release Candidates)
    check_on_start: bool = True
    auto_backup_before_update: bool = True
    keep_backups: int = 20                 # pro Gerät, 0 = alle behalten
    # Ordner
    user_root: str = str(Path.home() / "FM-1")
    sample_roots: list[str] = field(default_factory=list)   # Ordner, die die Sample-Bibliothek durchsucht
    # Aussehen
    theme: str = "system"                  # "system", "light", "dark"
    # Language of the user interface: "system" (system locale if translated, else English), "de", "en", …
    language: str = "system"
    # Aufnahme
    record_mode: str = "auto"              # "auto", "stereo", "tracks"
    record_open_audacity: bool = True
    audacity_cmd: str = "audacity"
    # Editor
    editor_zoom: float = 1.0
    # Samples
    sample_default_normalize: bool = True
    sample_default_trim: bool = True
    # Firmware-Katalog: zusätzliche, vom Nutzer eingetragene Firmwares
    extra_firmwares: list[dict] = field(default_factory=list)
    # zuletzt aktives Gerät (Profil-ID)
    last_device: str = ""
    # Fenster
    window_geometry: str = ""
    # einmaliger Risiko-Hinweis vor der ersten Installation/Rettung bestätigt (Version des Textes, 0 = nie)
    risk_ack: int = 0
    # Einstellungen einzelner Bereiche der Suite, je Bereich ein eigenes Wörterbuch
    # (z. B. modules["catalog"]["show_beta"]). Zugriff über config.module(name).
    modules: dict = field(default_factory=dict)

    # ---- abgeleitete Ordner ----
    @property
    def root(self) -> Path:
        return Path(os.path.expanduser(self.user_root))

    def folder(self, name: str, strict: bool = False) -> Path:
        """Unterordner im Hauptordner (angelegt). strict=False (die Eigenschaften backups_dir usw.): ein
        Schreibfehler (z. B. schreibgeschützter Hauptordner) bricht nicht ab – der Pfad kommt trotzdem zurück,
        der Fehler steht in folder_problems; erst das eigentliche Schreiben meldet ihn dann verständlich."""
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
folder_problems: dict[str, str] = {}     # Ordnername -> Fehler beim Anlegen (Hauptordner schreibgeschützt …)
# was beim Laden auffiel (für einen einmaligen Hinweis in der Oberfläche)
load_problems: list[Msg] = []
broken_file: Path | None = None          # unlesbare config.json wurde hierhin umbenannt
read_error: str = ""                     # Einstellungsdatei nicht lesbar (Rechte, Ordner fehlt/ist eine Datei)
_read_exc: OSError | None = None         # (der Grund dazu, übersetzt erst in problems_text())


def _expected(f) -> str:
    t = f.type if isinstance(f.type, str) else getattr(f.type, "__name__", str(f.type))
    return t.replace(" ", "")


def _coerce(name: str, typ: str, v, default):
    """Wert prüfen; (Wert, Problem (Msg) oder None)"""
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
    """Settings aus einem (evtl. kaputten) Wörterbuch – jedes Feld einzeln geprüft, sonst Standardwert."""
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
    """Betriebssystem-Fehler in Alltagssprache (die strerror-Texte sind englisch). Nicht in load() benutzen."""
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
                # nicht lesbar (Rechte, ~/.config ist eine Datei …) – NICHT „beschädigt“ und nichts umbenennen
                read_error, _read_exc = f"{CONFIG_FILE}: {e.strerror or e}", e
            except (OSError, ValueError, UnicodeDecodeError) as e:
                # unlesbar: beiseitelegen (nicht still überschreiben) und – wenn vorhanden – die Sicherung nehmen
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
            if demo_root:                      # Demo-Modus: eigener Hauptordner, echte Daten bleiben unberührt
                s.user_root = demo_root
            _settings = s
        return _settings


_save_lock = threading.Lock()
save_error: str = ""                     # letzter Fehler beim Speichern ("" = zuletzt gespeichert)
save_listeners: list = []                # fn(text) – wird bei JEDEM fehlgeschlagenen Speichern gerufen (jeder Thread)


def _write_private(path: Path, text: str) -> None:
    """Datei nur für den Benutzer lesbar schreiben (0600) – die Einstellungen können z. B. ein GitHub-Token
    enthalten. Die Rechte stehen schon beim Anlegen fest (kein kurzes Fenster mit 0644)."""
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.fchmod(fd, 0o600)                   # unabhängig von der umask
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            fd = -1
            f.write(text)
    finally:
        if fd >= 0:
            os.close(fd)


def save(s: Settings | None = None) -> bool:
    """Einstellungen schreiben (atomar, mit Sicherung des vorherigen Stands). -> True, wenn gespeichert.

    Wirft nie: Ist der Ordner nicht beschreibbar (Datenträger voll, Rechte, ~/.config ist eine Datei), gelten
    die Einstellungen bis zum Beenden weiter; der Fehler steht in ``save_error`` und geht an ``save_listeners``
    (das Hauptfenster meldet ihn einmal). Vorher stürzte z. B. schon der erste Start mit einem Traceback ab."""
    global save_error
    s = s or load()
    with _save_lock:                           # zwei Threads dürfen sich die .tmp-Datei nicht gegenseitig ersetzen
        tmp = CONFIG_FILE.with_suffix(".tmp")
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            _write_private(tmp, json.dumps(asdict(s), indent=2, ensure_ascii=False))
            if CONFIG_FILE.exists():
                try:                           # vorherigen Stand behalten (Rückweg bei einer kaputten Datei)
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
                except Exception:  # noqa: BLE001 – ein Hörer darf das Speichern nicht stören
                    pass
            return False
        save_error = ""
        return True


def problems_text() -> str:
    """einmaliger Hinweis für die Oberfläche ("" = alles in Ordnung)"""
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
    """Einstellungen eines Bereichs (veränderbar; danach config.save() aufrufen).
    Fehlende Schlüssel werden mit `defaults` ergänzt, vorhandene bleiben."""
    s = load()
    if not isinstance(s.modules, dict):
        s.modules = {}
    d = s.modules.get(name)
    if not isinstance(d, dict):
        d = s.modules[name] = {}
    for k, v in (defaults or {}).items():
        d.setdefault(k, v)
    return d


def folder(name: str) -> Path:
    """sichtbarer Unterordner im Hauptordner (z. B. "DX7", "MIDI-Export"), wird angelegt (OSError, wenn nicht)."""
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
