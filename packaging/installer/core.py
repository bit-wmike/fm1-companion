#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Installation core of the FM-1 Companion Suite (standard library only).

Used by install.sh/uninstall.sh, by the graphical .run installer and by the uninstall dialog.

  core.py install   --source DIR [--prefix ~/.local | --system] [--no-menu] [--desktop] [--demo-data]
  core.py uninstall [--prefix …|--system] [--settings] [--data] [--yes]
  core.py info      [--prefix …|--system]

User installation (default): app in <prefix>/share/fm1-companion/app, launcher <prefix>/bin/fm1-companion.
System-wide (--system, as root): /usr/local/lib/fm1-companion, /usr/local/bin/fm1-companion.
Settings (~/.config/fm1-companion) are kept across updates. The main folder (~/FM-1) is NEVER touched.

Messages are English; German when the system language is German (see ``language()``). The installer runs
standalone (possibly before the app is installed), so it has its own tiny ``tr(english, german)`` instead of
the app's translation catalogs.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

APP_ID = "fm1-companion"
SYSTEM_PREFIX = Path("/usr/local")
ICON_SIZES = (16, 22, 24, 32, 48, 64, 128, 256, 512)


def language(env=None) -> str:
    """"de" if the system language is German, else "en".

    ``FM1C_LANG`` (the app's override, unless "system") wins, then the POSIX order LC_ALL > LC_MESSAGES > LANG:
    the first non-empty one decides. The shell scripts (install.sh, uninstall.sh, run-stub.sh) use the same rule."""
    env = os.environ if env is None else env
    keys = ("LC_ALL", "LC_MESSAGES", "LANG")
    if (env.get("FM1C_LANG") or "").strip().lower() not in ("", "system"):
        keys = ("FM1C_LANG",)
    for k in keys:
        v = (env.get(k) or "").strip()
        if v:
            return "de" if v.lower().startswith("de") else "en"
    return "en"


def tr(en: str, de: str) -> str:
    """the English or the German text, depending on ``language()`` (read at call time)"""
    return de if language() == "de" else en


def user_prefix() -> Path:
    return Path.home() / ".local"


def layout(prefix: Path, system: bool) -> dict:
    share = prefix / "share"
    return {"app": (prefix / "lib" / APP_ID) if system else (share / APP_ID / "app"),
            "bin": prefix / "bin", "apps": share / "applications", "icons": share / "icons" / "hicolor",
            "metainfo": share / "metainfo", "desktop": Path(_desktop_dir())}


def _desktop_dir() -> str:
    try:
        out = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True, text=True, timeout=3).stdout.strip()
        if out:
            return out
    except (OSError, subprocess.TimeoutExpired):
        pass
    for n in ("Schreibtisch", "Desktop"):          # real folder names (German, English desktop)
        if (Path.home() / n).is_dir():
            return str(Path.home() / n)
    return str(Path.home() / "Desktop")


def read_version(src: Path) -> str:
    for line in (src / "fm1companion" / "__init__.py").read_text("utf-8").splitlines():
        if line.startswith("__version__"):
            return line.split("=", 1)[1].strip().strip('"\'')
    return "?"


def installed_info(prefix: Path, system: bool) -> dict | None:
    f = layout(prefix, system)["app"] / "install-info.json"
    try:
        return json.loads(f.read_text("utf-8"))
    except (OSError, ValueError):
        return None


def _ignore(_d, names):
    return [n for n in names if n in ("__pycache__", ".pytest_cache") or n.endswith(".pyc")]


# GenericName/Comment as in the .deb (data/fm1-companion.desktop.in) – both packages show the same in the menu
GENERIC_NAME = "FM-1-Verwaltung"
COMMENT = "Firmware, Backups, Klänge, MIDI, Simulator, Samples und Aufnahme für das M-VAVE FM-1"


def desktop_fields(src: Path | None = None) -> tuple[str, str]:
    """(GenericName, Comment) from the shared template data/fm1-companion.desktop.in"""
    g, c = GENERIC_NAME, COMMENT
    try:
        for line in (Path(src) / "data" / f"{APP_ID}.desktop.in").read_text("utf-8").splitlines():
            if line.startswith("GenericName="):
                g = line.split("=", 1)[1].strip() or g
            elif line.startswith("Comment="):
                c = line.split("=", 1)[1].strip() or c
    except (OSError, TypeError):
        pass
    return g, c


def _mkdir(d: Path, created: list[str]):
    """create a folder and remember which of them are new (uninstalling removes those again when empty)"""
    missing = []
    p = Path(d)
    while not p.exists() and p != p.parent:
        missing.append(p)
        p = p.parent
    Path(d).mkdir(parents=True, exist_ok=True)
    for m in missing:
        if str(m) not in created:
            created.append(str(m))


def exec_quote(path: str) -> str:
    """path for the Exec line of a .desktop file (Desktop Entry Spec): quoted when it has spaces & co.,
    otherwise e.g. "/home/Jane Doe/.local/bin/fm1-companion" does not start from the start menu."""
    path = path.replace("%", "%%")             # field codes: a literal % is %% (also inside quotes)
    if not any(c in path for c in ' \t\n"\'\\><~|&;$*?#()`'):
        return path
    inner = "".join("\\" + c if c in '"`$\\' else c for c in path)
    return '"' + inner.replace("\\", "\\\\") + '"'


def _desktop_entry(exe: str, name: str, comment: str, extra: str = "", generic: str = GENERIC_NAME) -> str:
    return (f"[Desktop Entry]\nType=Application\nName={name}\nGenericName={generic}\nComment={comment}\n"
            f"Exec={exe}\nIcon={APP_ID}\nTerminal=false\nCategories=AudioVideo;Audio;Midi;Music;\n"
            "Keywords=FM-1;M-VAVE;Melodee;Skiff;SLOOP;Synthesizer;MIDI;Firmware;\n"
            f"StartupWMClass={APP_ID}\n{extra}")


def fluidsynth_service_enabled() -> bool:
    """is Debian's FluidSynth user service enabled? (otherwise it grabs the first sound card at every login)"""
    if os.geteuid() == 0 or not shutil.which("systemctl"):
        return False
    try:
        r = subprocess.run(["systemctl", "--user", "is-enabled", "fluidsynth.service"], capture_output=True,
                           text=True, timeout=5)
        return bool(r) and (getattr(r, "stdout", "") or "").strip() == "enabled"
    except (OSError, subprocess.SubprocessError):
        return False


def install(src: Path, prefix: Path, system: bool = False, menu: bool = True, desktop: bool = False,
            demo_data: bool = False, python: str = "/usr/bin/python3", log=print, progress=lambda a, b: None,
            fluidsynth_fix: bool = True) -> dict:
    src = Path(src).resolve()
    L = layout(prefix, system)
    version = read_version(src)
    old = installed_info(prefix, system)
    files: list[str] = []
    created: list[str] = list((old or {}).get("dirs", []))
    steps = 7
    log(tr(f"Installing FM-1 Companion Suite {version} to {L['app']}",
           f"Installiere FM-1 Companion Suite {version} nach {L['app']}") +
        (tr(f" (update from {old.get('version')}; settings are kept)",
            f" (Update von {old.get('version')}; Einstellungen bleiben erhalten)") if old else ""))
    progress(0, steps)
    # 1. app files
    app = L["app"]
    _mkdir(app, created)
    for sub in ("fm1companion", "installer", "data"):
        if (app / sub).exists():
            shutil.rmtree(app / sub)
    shutil.copytree(src / "fm1companion", app / "fm1companion", ignore=_ignore)
    inst_src = src / "packaging" / "installer" if (src / "packaging" / "installer").exists() else src / "installer"
    shutil.copytree(inst_src, app / "installer", ignore=_ignore)
    if (src / "data").exists():
        shutil.copytree(src / "data", app / "data", ignore=_ignore)
    for f in ("LICENSE", "README.md", "uninstall.sh"):
        if (src / f).exists():
            shutil.copy2(src / f, app / f)
    if (app / "uninstall.sh").exists():
        (app / "uninstall.sh").chmod(0o755)
    log(tr("  App files copied", "  App-Dateien kopiert"))
    progress(1, steps)
    # 2. launcher (always the system Python, even if a venv is active)
    _mkdir(L["bin"], created)
    launcher = L["bin"] / APP_ID
    q = shlex.quote
    launcher.write_text(f"#!/bin/sh\n# FM-1 Companion Suite – launcher (created by the installer)\nunset PYTHONHOME\n"
                        f"export PYTHONPATH={q(str(app))}\nexec {q(python)} -m fm1companion \"$@\"\n")
    launcher.chmod(0o755)
    files.append(str(launcher))
    uninst = L["bin"] / f"{APP_ID}-uninstall"
    uninst.write_text(f"#!/bin/sh\n# FM-1 Companion Suite – uninstall\nexec {q(python)} "
                      f"{q(str(app) + '/installer/uninstall_gui.py')} "
                      f"--prefix {q(str(prefix))}{' --system' if system else ''} \"$@\"\n")
    uninst.chmod(0o755)
    files.append(str(uninst))
    log(tr(f"  Launcher: {launcher}", f"  Starter: {launcher}"))
    progress(2, steps)
    # 3. icons
    for n in ICON_SIZES:
        png = src / "data" / "icons" / "hicolor" / f"{n}x{n}" / "apps" / f"{APP_ID}.png"
        if png.exists():
            d = L["icons"] / f"{n}x{n}" / "apps"
            _mkdir(d, created)
            shutil.copy2(png, d / png.name)
            files.append(str(d / png.name))
    svg = src / "data" / f"{APP_ID}.svg"
    if svg.exists():
        d = L["icons"] / "scalable" / "apps"
        _mkdir(d, created)
        shutil.copy2(svg, d / svg.name)
        files.append(str(d / svg.name))
    log(tr("  Icons installed", "  Symbole installiert"))
    progress(3, steps)
    # 4. start menu, uninstall entry, AppStream (English names, German as [de] keys)
    generic, comment = desktop_fields(src)
    actions = (f"Actions=demo;\n\n[Desktop Action demo]\nName=Demo mode (no device)\nName[de]=Demo-Modus (ohne Gerät)\n"
               f"Exec={exec_quote(str(launcher))} --demo\n")
    entry = _desktop_entry(exec_quote(str(launcher)), "FM-1 Companion", comment, actions, generic)
    if menu:
        _mkdir(L["apps"], created)
        (L["apps"] / f"{APP_ID}.desktop").write_text(entry)
        files.append(str(L["apps"] / f"{APP_ID}.desktop"))
        (L["apps"] / f"{APP_ID}-uninstall.desktop").write_text(_desktop_entry(
            exec_quote(str(uninst)), "Uninstall FM-1 Companion", "Removes the FM-1 Companion Suite (your folder ~/FM-1 stays)",
            "Name[de]=FM-1 Companion deinstallieren\n"
            "Comment[de]=Entfernt die FM-1 Companion Suite (dein Ordner ~/FM-1 bleibt)\nNoDisplay=false\n").replace("Categories=AudioVideo;Audio;Midi;Music;", "Categories=Settings;"))
        files.append(str(L["apps"] / f"{APP_ID}-uninstall.desktop"))
        meta = src / "packaging" / f"{APP_ID}.metainfo.xml"
        if meta.exists():
            _mkdir(L["metainfo"], created)
            shutil.copy2(meta, L["metainfo"] / meta.name)
            files.append(str(L["metainfo"] / meta.name))
        log(tr("  Start menu entries created (Multimedia, Settings)", "  Startmenü-Einträge angelegt (Multimedia, Einstellungen)"))
    if desktop and not system:
        _mkdir(L["desktop"], created)
        f = L["desktop"] / f"{APP_ID}.desktop"
        f.write_text(entry)
        f.chmod(0o755)
        files.append(str(f))
        log(tr(f"  Desktop shortcut: {f}", f"  Desktop-Verknüpfung: {f}"))
    progress(4, steps)
    # 5. example data (the only write access to the main folder: new files in their own subfolder;
    #    "Beispiele (Demo)" is the real folder name, = "Examples (demo)")
    if demo_data and not system:
        sys.path.insert(0, str(app))
        try:
            from fm1companion import demodata
            root = _user_root()
            made = demodata.create(root / "Samples" / "Beispiele (Demo)")
            log(tr(f"  {len(made)} example samples in {made[0].parent}", f"  {len(made)} Beispiel-Samples in {made[0].parent}"))
        finally:
            sys.path.remove(str(app))
    progress(5, steps)
    # 6. precompile bytecode (faster start)
    subprocess.run([python if Path(python).exists() else sys.executable, "-m", "compileall", "-q", str(app / "fm1companion")],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    progress(6, steps)
    # 7. menu caches
    refresh_menus(L)
    # Otherwise Debian's fluidsynth user service grabs the first sound card (Pi: HDMI) at every login, bypassing
    # PipeWire – no more HDMI sound. The app starts FluidSynth itself; only for "just for me" installations and only
    # if not deselected (graphical installer: checkbox, otherwise --no-fluidsynth-fix). Always clearly reported.
    if not system and fluidsynth_service_enabled():
        if fluidsynth_fix:
            try:
                subprocess.run(["systemctl", "--user", "disable", "--now", "fluidsynth.service"],
                               capture_output=True, timeout=15)
                log(tr("NOTE: I disabled the system's FluidSynth user service (fluidsynth.service) – otherwise it "
                       "grabbed the HDMI sound card at every login. FluidSynth stays installed; the app starts it "
                       "itself. Re-enable it: systemctl --user enable --now fluidsynth.service",
                       "HINWEIS: Den FluidSynth-Benutzerdienst des Systems (fluidsynth.service) habe ich abgeschaltet – "
                       "er belegte sonst bei jeder Anmeldung die HDMI-Soundkarte. FluidSynth bleibt installiert; die App "
                       "startet es selbst. Wieder einschalten: systemctl --user enable --now fluidsynth.service"))
            except (OSError, subprocess.SubprocessError):
                pass
        else:
            log(tr("NOTE: fluidsynth.service is active and can grab the HDMI sound card (no sound over HDMI). "
                   "Not changed (--no-fluidsynth-fix). Disable it: systemctl --user disable --now fluidsynth.service",
                   "HINWEIS: fluidsynth.service ist aktiv und kann die HDMI-Soundkarte belegen (kein Ton über HDMI). "
                   "Nicht verändert (--no-fluidsynth-fix). Abschalten: systemctl --user disable --now fluidsynth.service"))
    # keep remembering files of an earlier installation that were not rewritten this time (e.g. the desktop
    # shortcut) – otherwise uninstalling no longer finds them
    files += [f for f in (old or {}).get("files", []) if f not in files and Path(f).exists()]
    info = {"version": version, "mode": "system-wide" if system else "just for me", "system": system,
            "prefix": str(prefix), "app": str(app), "files": files, "dirs": created,
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "components": {"menu": menu, "desktop": desktop, "demo_data": demo_data}}
    (app / "install-info.json").write_text(json.dumps(info, indent=2, ensure_ascii=False))
    progress(7, steps)
    log(tr("Done.", "Fertig."))
    return info


def _user_root() -> Path:
    cfg = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / APP_ID / "config.json"
    try:
        return Path(os.path.expanduser(json.loads(cfg.read_text("utf-8")).get("user_root", "~/FM-1")))
    except (OSError, ValueError):
        return Path.home() / "FM-1"


def refresh_menus(L: dict):
    for cmd in (["update-desktop-database", str(L["apps"])], ["gtk-update-icon-cache", "-q", "-t", str(L["icons"])]):
        if shutil.which(cmd[0]):
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for k in ("kbuildsycoca6", "kbuildsycoca5"):
        if shutil.which(k):
            subprocess.run([k], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            break


def internal_dirs() -> dict[str, Path]:
    env = os.environ.get
    return {"settings": Path(env("XDG_CONFIG_HOME") or Path.home() / ".config") / APP_ID,
            "data": Path(env("XDG_DATA_HOME") or Path.home() / ".local" / "share") / APP_ID,
            "cache": Path(env("XDG_CACHE_HOME") or Path.home() / ".cache") / APP_ID,
            "state": Path(env("XDG_STATE_HOME") or Path.home() / ".local" / "state") / APP_ID}


def _safe_rm(p: Path, protect: Path, log) -> bool:
    """deletes p – unless the main folder lies inside it (that one is never touched)."""
    try:
        pr, protect_r = p.resolve(), protect.resolve()
    except OSError:
        return False
    if pr == protect_r or pr in protect_r.parents or not p.exists():
        if p.exists():
            log(tr(f"  skipped (contains your main folder): {p}", f"  übersprungen (enthält deinen Hauptordner): {p}"))
        return False
    if p.is_dir() and not p.is_symlink():
        shutil.rmtree(p, ignore_errors=True)
    else:
        p.unlink(missing_ok=True)
    return True


def uninstall(prefix: Path, system: bool = False, settings: bool = False, data: bool = False, log=print) -> list[str]:
    L = layout(prefix, system)
    info = installed_info(prefix, system) or {}
    root = _user_root()
    removed = []
    for f in info.get("files", []) or [str(L["bin"] / APP_ID), str(L["bin"] / f"{APP_ID}-uninstall"),
                                        str(L["apps"] / f"{APP_ID}.desktop"), str(L["apps"] / f"{APP_ID}-uninstall.desktop")]:
        if _safe_rm(Path(f), root, log):
            removed.append(f)
    if _safe_rm(L["app"], root, log):
        removed.append(str(L["app"]))
    d = internal_dirs()
    if data:
        for k in ("data", "cache", "state"):
            if _safe_rm(d[k], root, log):
                removed.append(str(d[k]))
    if settings and _safe_rm(d["settings"], root, log):
        removed.append(str(d["settings"]))
    # empty folders the installation created itself (never the main folder, never with content)
    dirs = list(info.get("dirs", []) or []) + [str(L["app"].parent)] if not system else list(info.get("dirs", []) or [])
    for dd in sorted(set(dirs), key=lambda x: len(Path(x).parts), reverse=True):
        p = Path(dd)
        try:
            if p.is_dir() and not p.is_symlink() and not any(p.iterdir()) and p.resolve() != root.resolve() \
                    and p.resolve() not in root.resolve().parents and p != Path.home():
                p.rmdir()
                removed.append(str(p))
        except OSError:
            pass
    refresh_menus(L)
    for r in removed:
        log(tr(f"  removed: {r}", f"  entfernt: {r}"))
    log(tr(f"Your folder {root} is left untouched.", f"Dein Ordner {root} bleibt unangetastet."))
    return removed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=tr("Install/remove the FM-1 Companion Suite",
                                                "FM-1 Companion Suite installieren/entfernen"))
    ap.add_argument("cmd", choices=["install", "uninstall", "info"])
    ap.add_argument("--source", default=str(Path(__file__).resolve().parents[2]))
    ap.add_argument("--prefix", default="")
    ap.add_argument("--system", action="store_true")
    ap.add_argument("--no-menu", action="store_true")
    ap.add_argument("--desktop", action="store_true")
    ap.add_argument("--demo-data", action="store_true")
    ap.add_argument("--python", default="/usr/bin/python3" if Path("/usr/bin/python3").exists() else sys.executable)
    ap.add_argument("--settings", action="store_true", help=tr("when removing, also delete settings",
                                                                  "beim Entfernen auch Einstellungen löschen"))
    ap.add_argument("--data", action="store_true", help=tr("when removing, also delete internal data",
                                                              "beim Entfernen auch interne Daten löschen"))
    ap.add_argument("--no-fluidsynth-fix", action="store_true",
                    help=tr("do NOT disable the system's FluidSynth user service (only a note)",
                            "den FluidSynth-Benutzerdienst des Systems NICHT abschalten (nur Hinweis)"))
    a = ap.parse_args(argv)
    prefix = Path(a.prefix).expanduser() if a.prefix else (SYSTEM_PREFIX if a.system else user_prefix())
    if a.cmd == "install":
        install(Path(a.source), prefix, a.system, not a.no_menu, a.desktop, a.demo_data, a.python,
                fluidsynth_fix=not a.no_fluidsynth_fix)
    elif a.cmd == "uninstall":
        uninstall(prefix, a.system, a.settings, a.data)
    else:
        print(json.dumps(installed_info(prefix, a.system), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
