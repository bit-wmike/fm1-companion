#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Installation core of the FM-1 Companion Suite (standard library only).

Used by install.sh/uninstall.sh, by the graphical .run installer and by the uninstall dialog.

  core.py install   --source DIR [--prefix ~/.local | --system] [--no-menu] [--desktop] [--demo-data]
  core.py uninstall [--prefix …|--system] [--settings] [--data] [--yes]
  core.py info      [--prefix …|--system]
  core.py usb-power-rule [--remove]   optional udev rule: keep the FM-1 and its USB hubs awake (asks for sudo)
  core.py download-mode-rule [--remove]
                                      udev rule (uaccess for USB 4c4a:8057) + `sg` at boot: the bundled M-Flasher
                                      reaches an FM-1 in ROM download mode without root (asks for sudo)

User installation (default): app in <prefix>/share/fm1-companion/app, launcher <prefix>/bin/fm1-companion.
System-wide (--system, as root): /usr/local/lib/fm1-companion, /usr/local/bin/fm1-companion.
Settings (~/.config/fm1-companion) are kept across updates. The main folder (~/FM-1) is NEVER touched.

Messages are English; German when the system language is German (see ``language()``). The installer runs
standalone (possibly before the app is installed), so it has its own tiny ``tr(english, german)`` instead of
the app's translation catalogs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
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
GENERIC_NAME = "FM-1 manager"
COMMENT = "Firmware, backups, sounds, MIDI, simulator, samples and recording for the M-VAVE FM-1"
L10N = ("GenericName[de]=FM-1-Verwaltung\n"
        "Comment[de]=Firmware, Backups, Klänge, MIDI, Simulator, Samples und Aufnahme für das M-VAVE FM-1\n")


def desktop_l10n(src: Path | None = None) -> str:
    """translated GenericName[xx]=/Comment[xx]= lines from the shared template (fallback: German)"""
    try:
        lines = [ln for ln in (Path(src) / "data" / f"{APP_ID}.desktop.in").read_text("utf-8").splitlines()
                 if ln.startswith(("GenericName[", "Comment["))]
        return "".join(ln + "\n" for ln in lines)
    except (OSError, TypeError):
        return L10N


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


def _desktop_entry(exe: str, name: str, comment: str, extra: str = "", generic: str = GENERIC_NAME,
                   l10n: str = "") -> str:
    return (f"[Desktop Entry]\nType=Application\nName={name}\nGenericName={generic}\nComment={comment}\n{l10n}"
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


def _fluidsynth_state() -> str:
    """what `systemctl --user is-enabled fluidsynth.service` says: enabled, disabled, masked … ("" = no answer)"""
    try:
        r = subprocess.run(["systemctl", "--user", "is-enabled", "fluidsynth.service"], capture_output=True,
                           text=True, timeout=5)
        return ((getattr(r, "stdout", "") or "").strip().splitlines() or [""])[0]
    except (OSError, subprocess.SubprocessError):
        return ""


def stop_fluidsynth_service() -> str:
    """Stop Debian's FluidSynth user service and keep it from starting at the next login.
    -> "disabled" (switched off the normal way) | "masked" (it is enabled for all users under /etc/systemd/user,
    where `systemctl --user disable` changes nothing: masked for this user, reversible with `unmask`) |
    "failed" (still enabled). The result is checked, never assumed."""
    def run(*args):
        try:
            subprocess.run(["systemctl", "--user", *args, "fluidsynth.service"], capture_output=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            pass
    run("disable", "--now")
    if _fluidsynth_state() != "enabled":
        return "disabled"
    run("mask", "--now")
    return "masked" if _fluidsynth_state() == "masked" else "failed"


UDEV_RULE = "60-fm1-companion.rules"
UDEV_DIR = Path("/etc/udev/rules.d")


def udev_rule_source(src: Path) -> Path | None:
    """the optional udev rule in a source tree (packaging/udev) or an installed app (udev/)"""
    for p in (Path(src) / "packaging" / "udev" / UDEV_RULE, Path(src) / "udev" / UDEV_RULE):
        if p.exists():
            return p
    return None


def udev_rule_installed() -> bool:
    return (UDEV_DIR / UDEV_RULE).exists()


def _sudo() -> list[str]:
    """how to become root: nothing as root, sudo in a terminal, pkexec in the graphical installer"""
    if os.geteuid() == 0:
        return []
    if sys.stdin is not None and sys.stdin.isatty() and shutil.which("sudo"):
        return ["sudo"]
    return ["pkexec"] if shutil.which("pkexec") else ["sudo"]


def install_udev_rule(rule: Path, log=print, sudo: list[str] | None = None, remove: bool = False) -> bool:
    """copy the udev rule to /etc/udev/rules.d (or remove it) and reload udev – one root command.
    Only on explicit request (install.sh --usb-power-rule / question, checkbox in the graphical installer)."""
    target = UDEV_DIR / UDEV_RULE
    sudo = _sudo() if sudo is None else sudo
    if remove:
        script = f"rm -f {shlex.quote(str(target))} && udevadm control --reload-rules"
    else:
        script = (f"install -m 0644 {shlex.quote(str(rule))} {shlex.quote(str(target))} && "
                  "udevadm control --reload-rules && udevadm trigger --subsystem-match=usb --action=change")
    try:
        r = subprocess.run([*sudo, "sh", "-c", script], timeout=300)
        ok = getattr(r, "returncode", 1) == 0
    except (OSError, subprocess.SubprocessError):
        ok = False
    if ok and not remove:
        log(tr(f"  udev rule installed: {target} (the FM-1 and the USB hubs in front of it stay awake)",
               f"  udev-Regel installiert: {target} (das FM-1 und die USB-Hubs davor bleiben wach)"))
    elif ok:
        log(tr(f"  udev rule removed: {target}", f"  udev-Regel entfernt: {target}"))
    else:
        log(tr("NOTE: the udev rule could not be installed (no administrator rights?). By hand: "
               f"sudo install -m 0644 {rule} {UDEV_DIR}/ && sudo udevadm control --reload-rules",
               "HINWEIS: Die udev-Regel ließ sich nicht installieren (keine Administratorrechte?). Von Hand: "
               f"sudo install -m 0644 {rule} {UDEV_DIR}/ && sudo udevadm control --reload-rules"))
    return ok


DL_RULE = "70-fm1-companion-download-mode.rules"
SG_CONF = "fm1-companion-sg.conf"
MODULES_DIR = Path("/etc/modules-load.d")
# after copying: load sg now and let udev apply the rule to a unit that is already connected (tests replace these)
DL_ACTIVATE = ("(modprobe sg || true) && (udevadm control --reload-rules || true) && "
               "(udevadm trigger --action=add --subsystem-match=scsi_generic || true) && "
               "(udevadm trigger --action=add --subsystem-match=usb --attr-match=idVendor=4c4a || true)")
DL_RELOAD = "(udevadm control --reload-rules || true)"


def download_rule_sources(src: Path) -> tuple[Path, Path] | None:
    """(udev rule, modules-load file) for ROM download mode in a source tree (packaging/udev) or an installed app"""
    for d in (Path(src) / "packaging" / "udev", Path(src) / "udev"):
        if (d / DL_RULE).exists() and (d / SG_CONF).exists():
            return d / DL_RULE, d / SG_CONF
    return None


def download_rule_installed() -> bool:
    return (UDEV_DIR / DL_RULE).exists()


def install_download_rule(rule: Path | None, conf: Path | None, log=print, sudo: list[str] | None = None,
                          remove: bool = False) -> bool:
    """owner decision (2026-11-19): the FM-1 in ROM download mode (USB 4c4a:8057) is reachable for the user at the
    computer (udev TAG+="uaccess") and the `sg` driver loads at boot, so the bundled M-Flasher needs no root.
    One root command; remove=True takes both out again."""
    target, mod = UDEV_DIR / DL_RULE, MODULES_DIR / SG_CONF
    q = shlex.quote
    sudo = _sudo() if sudo is None else sudo
    if remove:
        script = f"rm -f {q(str(target))} {q(str(mod))} && {DL_RELOAD}"
    else:
        script = (f"install -d {q(str(UDEV_DIR))} {q(str(MODULES_DIR))} && "
                  f"install -m 0644 {q(str(rule))} {q(str(target))} && "
                  f"install -m 0644 {q(str(conf))} {q(str(mod))} && {DL_ACTIVATE}")
    try:
        r = subprocess.run([*sudo, "sh", "-c", script], timeout=300)
        ok = getattr(r, "returncode", 1) == 0
    except (OSError, subprocess.SubprocessError):
        ok = False
    if ok and not remove:
        log(tr(f"  Download mode: udev rule {target} and {mod} installed (the app reaches an FM-1 in ROM download "
               "mode without an administrator password)",
               f"  Download-Modus: udev-Regel {target} und {mod} installiert (die App erreicht ein FM-1 im "
               "ROM-Download-Modus ohne Administrator-Passwort)"))
    elif ok:
        log(tr(f"  removed: {target}, {mod}", f"  entfernt: {target}, {mod}"))
    elif remove:
        log(tr(f"NOTE: could not remove {target} and {mod} (no administrator rights?). By hand: "
               f"sudo rm {target} {mod} && sudo udevadm control --reload-rules",
               f"HINWEIS: {target} und {mod} ließen sich nicht entfernen (keine Administratorrechte?). Von Hand: "
               f"sudo rm {target} {mod} && sudo udevadm control --reload-rules"))
    else:
        log(tr("NOTE: the download-mode rule could not be installed (no administrator rights?) – the app then asks "
               f"for the password when it needs the FM-1 in download mode. By hand: sudo install -m 0644 {rule} "
               f"{UDEV_DIR}/ && sudo install -m 0644 {conf} {MODULES_DIR}/ && sudo modprobe sg",
               "HINWEIS: Die Regel für den Download-Modus ließ sich nicht installieren (keine Administratorrechte?) – "
               "die App fragt dann nach dem Passwort, wenn sie das FM-1 im Download-Modus braucht. Von Hand: "
               f"sudo install -m 0644 {rule} {UDEV_DIR}/ && sudo install -m 0644 {conf} {MODULES_DIR}/ && "
               "sudo modprobe sg"))
    return ok


TOOLS_BIN = "tools-bin"


def machine_arch(machine: str | None = None) -> str:
    """'aarch64' / 'x86_64' (the M-Flasher builds), '' for anything else (32-bit Raspberry Pi OS …)"""
    m = (machine if machine is not None else platform.machine()).strip().lower()
    return {"aarch64": "aarch64", "arm64": "aarch64", "armv8l": "aarch64", "x86_64": "x86_64",
            "amd64": "x86_64"}.get(m, "")


def install_tool_binaries(src: Path, app: Path, log=print, machine: str | None = None) -> list[str]:
    """copy the bundled M-Flasher binary for THIS computer (tools-bin/m-flasher/<version>/<arch>/m-flasher) into
    the app – only when its SHA-256 and size match fm1companion/tools/manifest.json. -> installed relative paths"""
    src, app = Path(src), Path(app)
    if (app / TOOLS_BIN).exists():
        shutil.rmtree(app / TOOLS_BIN)
    try:
        t = json.loads((src / "fm1companion" / "tools" / "manifest.json").read_text("utf-8"))["tools"]["m-flasher"]
    except (OSError, ValueError, KeyError):
        return []
    arch = machine_arch(machine)
    e = t.get("arch", {}).get(arch)
    rel = f"m-flasher/{t.get('version')}/{arch}/m-flasher"
    if not e:
        log(tr("  M-Flasher: no build for this computer (it exists for 64-bit ARM and x86-64) – the other "
               "recovery tools work as before",
               "  M-Flasher: keine Version für diesen Rechner (es gibt sie für 64-Bit-ARM und x86-64) – die anderen "
               "Rettungswerkzeuge funktionieren wie bisher"))
        return []
    f = src / TOOLS_BIN / rel
    if not f.is_file():
        log(tr("  M-Flasher: not part of this source (run tools/fetch_tools.py once to bundle it)",
               "  M-Flasher: nicht in dieser Quelle enthalten (einmal tools/fetch_tools.py ausführen, um ihn "
               "mitzubringen)"))
        return []
    b = f.read_bytes()
    if len(b) != e.get("binary_size") or hashlib.sha256(b).hexdigest() != e.get("binary_sha256"):
        log(tr(f"WARNING: {rel} does not match its checksum – not installed",
               f"WARNUNG: {rel} passt nicht zu seiner Prüfsumme – nicht installiert"))
        return []
    dst = app / TOOLS_BIN / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(b)
    dst.chmod(0o755)
    log(tr(f"  M-Flasher {t.get('version')} ({arch}) installed, checksum verified",
           f"  M-Flasher {t.get('version')} ({arch}) installiert, Prüfsumme geprüft"))
    return [rel]


def make_world_readable(app: Path, files=(), dirs=()) -> None:
    """System-wide installation: every folder 0755, every file 0644 (0755 if it is executable for its owner) –
    for the app folder and for everything else the installer wrote. Folders that existed before are not touched.
    Symbolic links are skipped."""
    def fix(p: Path):
        try:
            if p.is_symlink():
                return
            if p.is_dir():
                p.chmod(0o755)
            elif p.is_file():
                p.chmod(0o755 if p.stat().st_mode & 0o100 else 0o644)
        except OSError:
            pass
    app = Path(app)
    if app.is_dir():
        fix(app)
        for p in app.rglob("*"):
            fix(p)
    for p in list(files) + list(dirs):
        fix(Path(p))


def install(src: Path, prefix: Path, system: bool = False, menu: bool = True, desktop: bool = False,
            demo_data: bool = False, python: str = "/usr/bin/python3", log=print, progress=lambda a, b: None,
            fluidsynth_fix: bool = True, usb_power_rule: bool = False, udev_hint: bool = True,
            download_mode_rule: bool = False) -> dict:
    # udev_hint=False: the caller (install.sh) asks about the udev rule or installs it itself right afterwards
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
    rule = udev_rule_source(src)
    if rule is not None:                    # shipped inactive; activated only on request (usb_power_rule)
        if (app / "udev").exists():
            shutil.rmtree(app / "udev")
        (app / "udev").mkdir()
        shutil.copy2(rule, app / "udev" / UDEV_RULE)
    dl_rule = download_rule_sources(src)
    if dl_rule is not None:                 # (the uninstaller needs to know them; installed below when asked)
        (app / "udev").mkdir(exist_ok=True)
        for f in dl_rule:
            shutil.copy2(f, app / "udev" / f.name)
    tools = install_tool_binaries(src, app, log)
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
    entry = _desktop_entry(exec_quote(str(launcher)), "FM-1 Companion", comment, actions, generic, desktop_l10n(src))
    if menu:
        _mkdir(L["apps"], created)
        (L["apps"] / f"{APP_ID}.desktop").write_text(entry)
        files.append(str(L["apps"] / f"{APP_ID}.desktop"))
        (L["apps"] / f"{APP_ID}-uninstall.desktop").write_text(_desktop_entry(
            exec_quote(str(uninst)), "Uninstall FM-1 Companion", "Removes the FM-1 Companion Suite (your folder ~/FM-1 stays)",
            "Name[de]=FM-1 Companion deinstallieren\n"
            "Comment[de]=Entfernt die FM-1 Companion Suite (dein Ordner ~/FM-1 bleibt)\nNoDisplay=false\n",
            generic="Uninstaller", l10n="GenericName[de]=Deinstallationsprogramm\n"
            ).replace("Categories=AudioVideo;Audio;Midi;Music;", "Categories=Settings;"))
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
    fluidsynth_masked = bool((old or {}).get("fluidsynth_masked"))   # (masked by an earlier installation: remembered)
    if not system and fluidsynth_service_enabled():
        if fluidsynth_fix:
            how = stop_fluidsynth_service()
            if how == "disabled":
                log(tr("NOTE: I disabled the system's FluidSynth user service (fluidsynth.service) – otherwise it "
                       "grabbed the HDMI sound card at every login. FluidSynth stays installed; the app starts it "
                       "itself. Re-enable it: systemctl --user enable --now fluidsynth.service",
                       "HINWEIS: Den FluidSynth-Benutzerdienst des Systems (fluidsynth.service) habe ich abgeschaltet – "
                       "er belegte sonst bei jeder Anmeldung die HDMI-Soundkarte. FluidSynth bleibt installiert; die App "
                       "startet es selbst. Wieder einschalten: systemctl --user enable --now fluidsynth.service"))
            elif how == "masked":
                fluidsynth_masked = True
                log(tr("NOTE: The system's FluidSynth user service (fluidsynth.service) is switched on for all users "
                       "of this computer, so it cannot simply be disabled for you. I stopped it and masked it for "
                       "your user – otherwise it grabbed the HDMI sound card at every login. FluidSynth stays "
                       "installed; the app starts it itself. Undo: systemctl --user unmask fluidsynth.service "
                       "(uninstalling the app does that too).",
                       "HINWEIS: Der FluidSynth-Benutzerdienst des Systems (fluidsynth.service) ist für alle Benutzer "
                       "dieses Rechners eingeschaltet und lässt sich deshalb nicht einfach für dich abschalten. Ich "
                       "habe ihn gestoppt und für deinen Benutzer maskiert – er belegte sonst bei jeder Anmeldung die "
                       "HDMI-Soundkarte. FluidSynth bleibt installiert; die App startet es selbst. Rückgängig: "
                       "systemctl --user unmask fluidsynth.service (die Deinstallation der App macht das auch)."))
            else:
                log(tr("NOTE: The system's FluidSynth user service (fluidsynth.service) could NOT be switched off – "
                       "it is still enabled and can grab the HDMI sound card at the next login (no sound over HDMI). "
                       "By hand: systemctl --user mask --now fluidsynth.service",
                       "HINWEIS: Der FluidSynth-Benutzerdienst des Systems (fluidsynth.service) ließ sich NICHT "
                       "abschalten – er ist weiter eingeschaltet und kann bei der nächsten Anmeldung die "
                       "HDMI-Soundkarte belegen (kein Ton über HDMI). Von Hand: "
                       "systemctl --user mask --now fluidsynth.service"))
        else:
            log(tr("NOTE: fluidsynth.service is active and can grab the HDMI sound card (no sound over HDMI). "
                   "Not changed (--no-fluidsynth-fix). Disable it: systemctl --user disable --now fluidsynth.service",
                   "HINWEIS: fluidsynth.service ist aktiv und kann die HDMI-Soundkarte belegen (kein Ton über HDMI). "
                   "Nicht verändert (--no-fluidsynth-fix). Abschalten: systemctl --user disable --now fluidsynth.service"))
    # optional udev rule (USB power saving off for the FM-1 and its hubs): only when asked for
    if usb_power_rule:
        if rule is None:
            log(tr("NOTE: the udev rule is missing in this package.", "HINWEIS: Die udev-Regel fehlt in diesem Paket."))
        else:
            install_udev_rule(app / "udev" / UDEV_RULE, log)
    elif rule is not None and udev_hint and not udev_rule_installed():
        log(tr("Optional: a udev rule keeps the FM-1 and its USB hub awake while flashing (helps with hubs): "
               f"{app / 'installer' / 'core.py'} usb-power-rule",
               "Optional: Eine udev-Regel hält das FM-1 und seinen USB-Hub beim Flashen wach (hilft bei Hubs): "
               f"{app / 'installer' / 'core.py'} usb-power-rule"))
    # FM-1 in ROM download mode reachable without root (udev uaccess + `sg`): install.sh and the graphical installer
    # ask for it (default yes, owner decision); the .deb installs it always
    dl_installed = False
    if download_mode_rule:
        if dl_rule is None:
            log(tr("NOTE: the download-mode rule is missing in this package.",
                   "HINWEIS: Die Regel für den Download-Modus fehlt in diesem Paket."))
        elif download_rule_installed():
            dl_installed = True
        else:
            dl_installed = install_download_rule(app / "udev" / DL_RULE, app / "udev" / SG_CONF, log)
    # keep remembering files of an earlier installation that were not rewritten this time (e.g. the desktop
    # shortcut) – otherwise uninstalling no longer finds them
    files += [f for f in (old or {}).get("files", []) if f not in files and Path(f).exists()]
    info = {"version": version, "mode": "system-wide" if system else "just for me", "system": system,
            "prefix": str(prefix), "app": str(app), "files": files, "dirs": created,
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "components": {"menu": menu, "desktop": desktop, "demo_data": demo_data}, "tools": tools}
    if dl_installed or (old or {}).get("download_mode_rule"):
        info["download_mode_rule"] = True   # uninstall.sh offers to remove it again
    if fluidsynth_masked:                   # uninstalling takes the mask away again
        info["fluidsynth_masked"] = True
    (app / "install-info.json").write_text(json.dumps(info, indent=2, ensure_ascii=False))
    if system:                              # a strict umask (077) of the installing user must not lock others out
        make_world_readable(app, files, created)
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
    if info.get("download_mode_rule") and (download_rule_installed() or (MODULES_DIR / SG_CONF).exists()):
        install_download_rule(None, None, log, remove=True)     # this installation put it there: take it out
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
    if info.get("fluidsynth_masked") and not system and _fluidsynth_state() == "masked":
        # the installation masked Debian's FluidSynth user service for this user: put it back as it was
        try:
            subprocess.run(["systemctl", "--user", "unmask", "fluidsynth.service"], capture_output=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            pass
        if _fluidsynth_state() != "masked":
            log(tr("The FluidSynth user service (fluidsynth.service) is no longer masked – it is as it was before "
                   "the installation and starts again at the next login.",
                   "Der FluidSynth-Benutzerdienst (fluidsynth.service) ist nicht mehr maskiert – er ist wieder wie "
                   "vor der Installation und startet bei der nächsten Anmeldung wieder."))
        else:
            log(tr("The FluidSynth user service is still masked. Undo by hand: "
                   "systemctl --user unmask fluidsynth.service",
                   "Der FluidSynth-Benutzerdienst ist weiter maskiert. Von Hand rückgängig: "
                   "systemctl --user unmask fluidsynth.service"))
    for r in removed:
        log(tr(f"  removed: {r}", f"  entfernt: {r}"))
    log(tr(f"Your folder {root} is left untouched.", f"Dein Ordner {root} bleibt unangetastet."))
    if udev_rule_installed():
        log(tr(f"The optional udev rule stays (needs root to remove): sudo rm {UDEV_DIR / UDEV_RULE} && "
               "sudo udevadm control --reload-rules",
               f"Die optionale udev-Regel bleibt (Entfernen braucht root): sudo rm {UDEV_DIR / UDEV_RULE} && "
               "sudo udevadm control --reload-rules"))
    return removed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=tr("Install/remove the FM-1 Companion Suite",
                                                "FM-1 Companion Suite installieren/entfernen"))
    ap.add_argument("cmd", choices=["install", "uninstall", "info", "usb-power-rule", "download-mode-rule"])
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
    ap.add_argument("--no-udev-hint", action="store_true",
                    help=tr("do not mention the optional udev rule (the caller takes care of it)",
                            "die optionale udev-Regel nicht erwähnen (der Aufrufer kümmert sich darum)"))
    ap.add_argument("--usb-power-rule", action="store_true",
                    help=tr("also install the optional udev rule (keeps the FM-1 and its USB hubs awake; asks for "
                            "sudo)", "auch die optionale udev-Regel installieren (hält das FM-1 und seine USB-Hubs wach; "
                            "fragt nach sudo)"))
    ap.add_argument("--download-mode-rule", action="store_true",
                    help=tr("also let the app reach an FM-1 in ROM download mode without root (udev rule + sg; "
                            "asks for sudo)", "die App ein FM-1 im ROM-Download-Modus ohne root erreichen lassen "
                            "(udev-Regel + sg; fragt nach sudo)"))
    ap.add_argument("--remove", action="store_true", help=tr("usb-power-rule, download-mode-rule: remove again",
                                                            "usb-power-rule, download-mode-rule: wieder entfernen"))
    a = ap.parse_args(argv)
    if a.cmd == "usb-power-rule":
        rule = udev_rule_source(Path(a.source)) or udev_rule_source(Path(__file__).resolve().parents[1])
        if rule is None and not a.remove:
            print(tr("udev rule not found", "udev-Regel nicht gefunden"))
            return 1
        return 0 if install_udev_rule(rule or Path(UDEV_RULE), remove=a.remove) else 1
    if a.cmd == "download-mode-rule":
        if a.remove:
            if not download_rule_installed() and not (MODULES_DIR / SG_CONF).exists():
                return 0
            return 0 if install_download_rule(None, None, remove=True) else 1
        srcs = download_rule_sources(Path(a.source)) or download_rule_sources(Path(__file__).resolve().parents[1])
        if srcs is None:
            print(tr("udev rule not found", "udev-Regel nicht gefunden"))
            return 1
        return 0 if install_download_rule(*srcs) else 1
    prefix = Path(a.prefix).expanduser() if a.prefix else (SYSTEM_PREFIX if a.system else user_prefix())
    if a.cmd == "install":
        install(Path(a.source), prefix, a.system, not a.no_menu, a.desktop, a.demo_data, a.python,
                fluidsynth_fix=not a.no_fluidsynth_fix, usb_power_rule=a.usb_power_rule,
                udev_hint=not a.no_udev_hint, download_mode_rule=a.download_mode_rule)
    elif a.cmd == "uninstall":
        uninstall(prefix, a.system, a.settings, a.data)
    else:
        print(json.dumps(installed_info(prefix, a.system), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
