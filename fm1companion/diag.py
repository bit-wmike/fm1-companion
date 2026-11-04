# SPDX-License-Identifier: GPL-3.0-only
"""Gemeinsame Diagnose-Bausteine: Versionen, Abhängigkeiten (aus data/deps.json), Mitwirkende.

Ohne Qt nutzbar (für --check, Installer und Fehlerberichte).
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import platform
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from .i18n import N_, SOURCE_LANGUAGE, _, current_language

DATA = Path(__file__).resolve().parent / "data"
FALLBACK_REQUIRED = ["python3", "python3-pyqt6", "python3-pyqt6.qtsvg", "python3-pyqt6.qtwebengine", "python3-mido",
                     "python3-rtmidi", "python3-numpy", "git"]


def deps() -> dict:
    try:
        return json.loads((DATA / "deps.json").read_text("utf-8"))
    except (OSError, ValueError):
        return {"required": [{"package": p} for p in FALLBACK_REQUIRED], "recommended": []}


def packages(kind: str = "required") -> list[str]:
    return [d["package"] for d in deps().get(kind, [])]


def debian_field(kind: str = "required") -> str:
    """'python3 (>= 3.11), python3-pyqt6, …' für die control-Datei."""
    return ", ".join(f"{d['package']} ({d['version']})" if d.get("version") else d["package"]
                     for d in deps().get(kind, []))


# the "why" texts of data/deps.json (English) – marked here so they get into the translation template
DEP_WHY = (
    N_("Python 3.11 or newer"), N_("user interface"), N_("icons in the app design"), N_("editor, simulator"),
    N_("Web MIDI bridge for editor and simulator"), N_("MIDI"), N_("MIDI connection"), N_("samples, sounds"),
    N_("firmware tools"),
    N_("Qt dialogs in your language (shortcuts “Ctrl”, “Cancel”); same package name in Bookworm (6.4.2) and Trixie (6.8.2)"),
    N_("previewing (fallback when pw-play/paplay/aplay are missing)"), N_("MP3/FLAC/AIFF samples"),
    N_("recording (ALSA), MIDI connections"), N_("recording and previewing (pw-record, pw-play)"),
    N_("editing recordings"), N_("sound generator"), N_("General MIDI sounds"),
    N_("rescue via U-Boot (jl-uboot-tool)"),
)
# needed only when the UI is not English (Qt's own dialog texts) – otherwise just recommended
UI_LANGUAGE_PACKAGES = {"qt6-translations-l10n"}


@dataclass
class DepState:
    package: str
    ok: bool
    required: bool
    why: str = ""
    detail: str = ""


def check_deps() -> list[DepState]:
    out = []
    for kind in ("required", "recommended"):
        for d in deps().get(kind, []):
            c, ok, detail = d.get("check", {}), True, ""
            if "module" in c:
                try:
                    importlib.import_module(c["module"])
                except Exception as e:  # noqa: BLE001
                    ok, detail = False, str(e)
            elif "exe" in c:
                ok = shutil.which(c["exe"]) is not None
            elif "file" in c:
                ok = Path(c["file"]).exists()
            elif "python" in c:
                want = tuple(int(x) for x in c["python"].split("."))
                ok = sys.version_info[:len(want)] >= want
                detail = platform.python_version()
            required = kind == "required" and not (d["package"] in UI_LANGUAGE_PACKAGES
                                                   and current_language() == SOURCE_LANGUAGE)
            why = d.get("why", "")
            out.append(DepState(d["package"], ok, required, _(why) if why else "", detail))
    return out


def apt_command(states: list[DepState] | None = None, include_optional: bool = True) -> str:
    states = states if states is not None else check_deps()
    miss = [s.package for s in states if not s.ok and (s.required or include_optional)]
    return ("sudo apt install " + " ".join(miss)) if miss else ""


def _ver(mod: str, attr: str = "__version__") -> str:
    try:
        m = importlib.import_module(mod)
    except Exception:  # noqa: BLE001
        return _("missing")
    for a in (attr, "__version__", "version"):
        v = getattr(m, a, None)
        if isinstance(v, str):
            return v
    try:
        from importlib import metadata
        return metadata.version({"rtmidi": "python-rtmidi"}.get(mod, mod))
    except Exception:  # noqa: BLE001
        return _("present")


def versions() -> dict[str, str]:
    from . import __version__
    v = {"FM-1 Companion": __version__, "Python": platform.python_version()}
    try:
        from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR
        v["Qt"], v["PyQt6"] = QT_VERSION_STR, PYQT_VERSION_STR
    except ImportError:
        v["Qt"] = v["PyQt6"] = _("missing")
    try:
        from PyQt6.QtWebEngineCore import qWebEngineVersion
        v["QtWebEngine"] = qWebEngineVersion()
    except Exception:  # noqa: BLE001
        v["QtWebEngine"] = _("missing") if importlib.util.find_spec("PyQt6.QtWebEngineCore") is None else _("present")
    v["numpy"], v["mido"], v["rtmidi"] = _ver("numpy"), _ver("mido"), _ver("rtmidi")
    osr = {}
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if "=" in line:
                k, val = line.split("=", 1)
                osr[k] = val.strip('"')
    except OSError:
        pass
    v["System"] = osr.get("PRETTY_NAME", platform.system())
    v["Kernel"] = platform.release()
    model = Path("/proc/device-tree/model")
    v[_("Computer")] = model.read_text(errors="replace").strip("\0").strip() if model.exists() else platform.machine()
    import os
    v["Desktop"] = (os.environ.get("XDG_CURRENT_DESKTOP", "") + (" (Wayland)" if os.environ.get("WAYLAND_DISPLAY") else "")).strip()
    v[_("Installed")] = install_kind()
    from .redact import redact
    return redact(v)


def install_kind() -> str:
    here = Path(__file__).resolve().parent
    if str(here).startswith("/usr/lib/fm1-companion"):
        return _("Debian package ({path})").format(path="/usr/lib/fm1-companion")
    info = here.parent / "install-info.json"
    if info.exists():
        try:
            return _("Installer: {mode} ({path})").format(mode=json.loads(info.read_text()).get("mode", "?"),
                                                          path=here.parent)
        except (OSError, ValueError):
            pass
    return _("from the source folder ({path})").format(path=here.parent)


def credits() -> list[dict]:
    out = []
    files = [DATA / "credits.json"] + sorted((DATA / "credits.d").glob("*.json"))
    seen = set()
    for f in files:
        try:
            for e in json.loads(f.read_text("utf-8")).get("entries", []):
                if e.get("name") and e["name"] not in seen:
                    seen.add(e["name"])
                    out.append(e)
        except (OSError, ValueError):
            continue
    return out


def midi_ports() -> tuple[list[str], list[str], str]:
    try:
        import mido
        return list(mido.get_input_names()), list(mido.get_output_names()), ""
    except Exception as e:  # noqa: BLE001
        return [], [], str(e) or type(e).__name__
