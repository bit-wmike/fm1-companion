# SPDX-License-Identifier: GPL-3.0-only
"""Rettung Stufe 2: FM-1 im ROM-Bootmodus („WL82 UBOOT1.00“, USB 4c4a:8057).

Die eigentliche Arbeit macht das unverändert mitgelieferte MvaveFM1Unbricker (MIT, vendor/unbricker/),
das über kagaimiqs jl-uboot-tool (MIT, wird erst nach Zustimmung auf einen festen Stand geholt) mit dem Chip
spricht. Seine Sicherheitsregeln gelten unverändert: nie unter 0x4000 schreiben, kein Chip-/Block-Löschen,
Pflicht-Doppelsicherung mit Vergleich, Chip-Key 0x980F und Flash-ID 0x856014 ohne Übersteuerung.

Diese Datei ergänzt nur: strenge Geräteerkennung über sysfs (VID:PID + Hersteller/Modell, Größe),
Start mit Root-Rechten über pkexec, Abhängigkeitsprüfung, Ausgabe zeilenweise an die Oberfläche.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .. import config
from ..i18n import N_, _

VENDOR = Path(__file__).resolve().parent.parent / "vendor" / "unbricker"
SCRIPT = VENDOR / "fm1_unbrick.py"
TRANSPORTER_SCRIPT = VENDOR / "fm1_transporter_recover.py"
RESTZONE_SCRIPT = Path(__file__).resolve().parent / "restzone.py"
# „Original-V15 zurückschreiben“ und „Einstellungen reparieren“ laufen über diese Sperre: sie prüft die Sicherung aus
# demselben Lauf (V16-Aufbau, V16-Bootkopf), bevor der Unbricker schreibt (Sicherheitsbefunde S1/S2, RESEARCH-2026-11)
GATE_SCRIPT = Path(__file__).resolve().parent / "rescuegate.py"
# eigene Dateien, die als root mit dem Unbricker zusammen laufen (Prüfsummen in uboot_manifest.OWN)
OWN_FILES = {"restzone.py": RESTZONE_SCRIPT, "flashmodel.py": RESTZONE_SCRIPT.parent / "flashmodel.py",
             "known_packages.json": RESTZONE_SCRIPT.parent.parent / "data" / "known_packages.json",
             "rescuegate.py": GATE_SCRIPT}
UBOOT_USB_ID = "4c4a:8057"
EXPECTED_SIZE = 0x100000            # 1 MiB Flash
TRANSPORTER_URL = "https://github.com/kurogedelic/FM-1-transporter"
UNBRICKER_URL = "https://github.com/Quixotic7/MvaveFM1Unbricker"
JLUB_URL = "https://github.com/kagaimiq/jl-uboot-tool"
DEPS = {"crcmod": "python3-crcmod", "yaml": "python3-yaml", "Cryptodome": "python3-pycryptodome",
        "tqdm": "python3-tqdm"}


# FM-1 mit UCUN-UC25HQ80-Flash (JEDEC 0xB36014): Melodee 1.6.0 läuft darauf (#110), die ROM-Rettung prüft aber auf
# 0x856014 (Unbricker, upstream tools/fm1_recover.py) und hält an. Nur eine verständliche Meldung, kein anderes Verhalten.
UCUN_FLASH_ID = 0xB36014
UCUN_TEXT = _("This FM-1 has a flash chip from UCUN (UC25HQ80, ID 0xB36014) instead of the usual Puya "
              "(0x856014). The USB ROM recovery (stage 2) does not support this flash yet and therefore stops – "
              "nothing was written. The firmware itself runs on it (Melodee 1.6.0 and later also saves correctly); "
              "normal installations via “Firmware+” work. For a ROM recovery, please wait for a version of the "
              "recovery tool that knows 0xB36014.")
_FLASH_ID_RE = re.compile(r"(?:flash[ _-]?id|jedec|\bid=)\D{0,12}(?:0x)?([0-9a-f]{6})\b", re.I)


def flash_hint(line: str) -> str:
    """Ausgabezeile des Rettungswerkzeugs -> deutscher Hinweis (UCUN-Flash), sonst ''"""
    m = _FLASH_ID_RE.search(line or "")
    return UCUN_TEXT if m and int(m.group(1), 16) == UCUN_FLASH_ID else ""


@dataclass
class UbootDevice:
    sg: str                 # /dev/sgN – nur dieses Gerät wird benutzt
    block: str              # /dev/sdX (nur zur Anzeige; nie beschrieben)
    size: int               # Bytes laut sysfs (Anzeige / Plausibilität)
    usb_id: str
    vendor: str
    model: str
    usb_path: str

    @property
    def size_ok(self) -> bool:
        # das ROM meldet als Datenträger teils 0 oder kleine Werte – der Flash selbst wird über den Loader gelesen
        return self.size in (0, EXPECTED_SIZE) or self.size < 64 * 1024 * 1024

    def describe(self) -> str:
        sz = f"{self.size // 1024} KiB" if self.size else _("size unknown")
        block = self.block or _("no block device")
        return f"{self.vendor} {self.model} – {self.sg} ({block}, {sz}, USB {self.usb_id})"


def _read(p: Path) -> str:
    try:
        return p.read_text().strip()
    except OSError:
        return ""


def _usb_ancestor(dev: Path) -> Path | None:
    try:
        real = dev.resolve()
    except OSError:
        return None
    for d in [real, *real.parents]:
        if (d / "idVendor").exists():
            return d
    return None


def usb_present(sysroot: str = "/sys") -> list[str]:
    """USB-Pfade aller angeschlossenen Geräte mit 4c4a:8057 (auch ohne sg-Treiber sichtbar)"""
    out = []
    base = Path(sysroot) / "bus" / "usb" / "devices"
    if base.is_dir():
        for d in sorted(base.iterdir()):
            if f"{_read(d / 'idVendor')}:{_read(d / 'idProduct')}".lower() == UBOOT_USB_ID:
                out.append(d.name)
    return out


def scan(sysroot: str = "/sys", devroot: str = "/dev") -> list[UbootDevice]:
    """ausschließlich SCSI-Generic-Geräte, deren USB-Vorfahr 4c4a:8057 ist UND die WL82 / UBOOT1.00 melden"""
    found = []
    base = Path(sysroot) / "class" / "scsi_generic"
    if not base.is_dir():
        return found
    for e in sorted(base.iterdir(), key=lambda p: (len(p.name), p.name)):
        if not re.fullmatch(r"sg\d+", e.name):
            continue
        dev = e / "device"
        vendor, model = _read(dev / "vendor"), _read(dev / "model")
        if vendor.upper() != "WL82" or not model.upper().startswith("UBOOT1.00"):
            continue
        usb = _usb_ancestor(dev)
        if usb is None:
            continue
        uid = f"{_read(usb / 'idVendor')}:{_read(usb / 'idProduct')}".lower()
        if uid != UBOOT_USB_ID:
            continue
        block, size = "", 0
        bdir = dev / "block"
        if bdir.is_dir():
            for b in bdir.iterdir():
                block = str(Path(devroot) / b.name)
                try:
                    size = int(_read(b / "size") or 0) * 512
                except ValueError:
                    size = 0
        found.append(UbootDevice(sg=str(Path(devroot) / e.name), block=block, size=size, usb_id=uid,
                                 vendor=vendor, model=model, usb_path=usb.name))
    return found


def missing_deps() -> list[str]:
    """apt-Pakete, die für jl-uboot-tool fehlen"""
    return [pkg for mod, pkg in DEPS.items() if importlib.util.find_spec(mod) is None]


def work_dir() -> Path:
    d = config.folder("Rettung")
    return d


def jl_dir() -> Path:
    return config.DATA_DIR / "flash" / "jl-uboot-tool"


def tool_ready() -> bool:
    """liegt jl-uboot-tool (geholt über „Werkzeug holen“) vor? Die Prüfsummen prüft der Root-Helfer vor jedem Start."""
    d = jl_dir()
    return (d / "jluboottool.py").is_file() and (d / "data" / "loaderblobs" / "usb" / "wl82loader.bin").is_file()


# Läuft als root (über pkexec): kopiert die Werkzeuge in ein root-eigenes Temp-Verzeichnis, prüft jede Datei gegen
# die eingebaute Prüfsummenliste, führt nur die geprüfte Kopie aus und gibt Dateien, die dabei im Rettungsordner
# als root entstanden sind, dem Benutzer zurück. Steht als Text im Befehl (nicht in einer beschreibbaren Datei).
# Grenze (siehe SECURITY.md): Die Prüfsummen sichern die Unversehrtheit der Werkzeuge – sie sind keine
# Sicherheitsgrenze gegen Code, der schon mit Benutzerrechten läuft. Sicherungen und Protokolle schreibt das
# Werkzeug als root in den Rettungsordner des Benutzers; die Rückgabe folgt keinen symbolischen Links.
ROOT_HELPER = r"""# i18n: ignore (German output, shown in the UI language via helper_line())
import hashlib, json, os, pwd, shutil, subprocess, sys, tempfile
spec = json.loads(sys.argv[1])
tmp = tempfile.mkdtemp(prefix="fm1c-rettung-")
rc = 1
try:
    os.chmod(tmp, 0o700)
    jl = os.path.join(tmp, "jl-uboot-tool")
    plan = [(os.path.join(spec["vendor"], n), os.path.join(tmp, n), h) for n, h in spec["unbricker"].items()]
    plan += [(os.path.join(spec["jl_dir"], n), os.path.join(jl, n), h) for n, h in spec["jl"].items()]
    plan += [(src, os.path.join(tmp, n), h) for n, (src, h) in spec.get("own", {}).items()]
    for src, dst, want in plan:
        try:
            with open(src, "rb") as f:
                data = f.read()
        except OSError as e:
            print("FEHLER: %s fehlt (%s) – zuerst „Werkzeug holen“. Nichts ausgeführt." % (src, e.strerror), flush=True)
            sys.exit(96)
        if hashlib.sha256(data).hexdigest() != want:
            print("FEHLER: %s hat nicht die erwartete Prüfsumme – verändert? Nichts ausgeführt." % src, flush=True)
            sys.exit(97)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as f:
            f.write(data)
    with open(os.path.join(jl, ".fm1-unbrick-commit"), "w") as f:
        f.write(spec["jl_commit"] + chr(10))
    print("Werkzeuge geprüft (%d Dateien, SHA-256) – starte die geprüfte Kopie als root." % len(plan), flush=True)
    cmd = [sys.executable, "-I", os.path.join(tmp, spec["script"])] + spec["argv"] + ["--jl-dir", jl,
                                                                                      "--log-dir", spec["log_dir"]]
    rc = subprocess.call(cmd, cwd=spec["cwd"])
finally:
    shutil.rmtree(tmp, ignore_errors=True)
    uid = int(os.environ.get("PKEXEC_UID") or os.environ.get("SUDO_UID") or spec["uid"])
    try:
        gid = int(os.environ.get("SUDO_GID") or pwd.getpwuid(uid).pw_gid)
    except KeyError:
        gid = spec["gid"]
    if uid != 0:
        for top in spec["chown"]:
            if not os.path.isdir(top) or os.path.islink(top):
                continue
            try:
                if os.lstat(top).st_uid == 0:
                    os.lchown(top, uid, gid)
            except OSError:
                pass
            for _dp, dirs, files, dfd in os.fwalk(top, follow_symlinks=False):
                for n in dirs + files:
                    try:
                        if os.stat(n, dir_fd=dfd, follow_symlinks=False).st_uid == 0:
                            os.chown(n, uid, gid, dir_fd=dfd, follow_symlinks=False)
                    except OSError:
                        pass
sys.exit(rc)
"""


# what ROOT_HELPER prints (German, the helper text stays as it is) -> message shown in the UI language
_HELPER_LINES = [
    (re.compile(r"^FEHLER: (?P<path>.+) fehlt \((?P<error>.*)\) – zuerst „Werkzeug holen“\. Nichts ausgeführt\.$"),  # i18n: ignore
     N_("ERROR: {path} is missing ({error}) – first “Fetch tool”. Nothing executed.")),
    (re.compile(r"^FEHLER: (?P<path>.+) hat nicht die erwartete Prüfsumme – verändert\? Nichts ausgeführt\.$"),  # i18n: ignore
     N_("ERROR: {path} does not have the expected checksum – modified? Nothing executed.")),
    (re.compile(r"^Werkzeuge geprüft \((?P<n>\d+) Dateien, SHA-256\) – starte die geprüfte Kopie als root\.$"),  # i18n: ignore
     N_("Tools checked ({n} files, SHA-256) – starting the checked copy as root.")),
]


def helper_line(line: str) -> str:
    """an output line of ROOT_HELPER in the UI language (other lines unchanged)"""
    for rx, msg in _HELPER_LINES:
        m = rx.match(line)
        if m:
            return _(msg).format(**m.groupdict())
    return line


def root_python() -> str:
    """System-Python für den root-Teil (dort liegen die apt-Pakete crcmod, yaml, pycryptodome)"""
    return "/usr/bin/python3" if Path("/usr/bin/python3").exists() else sys.executable


def root_spec(script: Path, args: list[str], cwd: Path, log_dir: Path) -> dict:
    from . import uboot_manifest as M
    own = {n: [str(OWN_FILES[n]), h] for n, h in M.OWN.items()} if Path(script).name in M.OWN else {}
    return {"vendor": str(VENDOR), "jl_dir": str(jl_dir()), "unbricker": dict(M.UNBRICKER), "jl": dict(M.JL), "own": own,
            "jl_commit": M.JL_COMMIT, "script": Path(script).name, "argv": list(args), "cwd": str(cwd),
            "log_dir": str(log_dir), "chown": [str(cwd)], "uid": os.getuid(), "gid": os.getgid()}


class Runner:
    """führt fm1_unbrick.py / fm1_transporter_recover.py aus; dry_run = mit dessen eingebautem Mock-FM-1"""

    def __init__(self, dry_run: bool = False, log: Callable[[str], None] = print, cwd: Path | None = None,
                 root_cmd: list[str] | None = None):
        self.dry_run = dry_run
        self.log = log
        self.cwd = Path(cwd) if cwd else work_dir()
        self.root_cmd = root_cmd if root_cmd is not None else (["pkexec"] if shutil.which("pkexec") else ["sudo"])
        self.proc: subprocess.Popen | None = None
        self.last_result: Path | None = None
        self.hint = ""                  # deutscher Hinweis aus der Ausgabe (z. B. UCUN-Flash), sonst ""

    def argv(self, cmd: str, *args: str, script: Path = SCRIPT, root: bool = True) -> list[str]:
        common = ["--jl-dir", str(jl_dir()), "--log-dir", str(self.cwd / "logs")]
        if self.dry_run:
            common.append("--dry-run")
        base = [sys.executable, "-I", str(script), cmd, *args, *common]
        if self.dry_run or not root:
            return base
        # als root nie Code aus beschreibbaren Ordnern ausführen: geprüfte Kopie über ROOT_HELPER
        spec = root_spec(script, [cmd, *args], self.cwd, self.cwd / "logs")
        return [*self.root_cmd, root_python(), "-I", "-c", ROOT_HELPER, json.dumps(spec)]

    def run(self, cmd: str, *args: str, script: Path = SCRIPT, root: bool = True, stdin: str = "") -> int:
        argv = self.argv(cmd, *args, script=script, root=root)
        self.cwd.mkdir(parents=True, exist_ok=True)
        if "-c" in argv and ROOT_HELPER in argv:
            self.log(_("$ {root_cmd} {script} {command} {args}  (checked copy with root rights)").format(
                root_cmd=" ".join(self.root_cmd), script=Path(script).name, command=cmd, args=" ".join(args)))
        else:
            self.log("$ " + " ".join(a if " " not in a else repr(a) for a in argv))
        env = dict(os.environ, PYTHONUNBUFFERED="1")
        self.proc = subprocess.Popen(argv, cwd=self.cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True, bufsize=1, env=env)
        try:
            if stdin:
                self.proc.stdin.write(stdin)
            self.proc.stdin.close()
        except OSError:
            pass
        hinted = False
        for line in self.proc.stdout:
            line = line.rstrip()
            if line:
                self.log(helper_line(line))
                if not hinted and flash_hint(line):
                    hinted = True
                    self.hint = UCUN_TEXT
                    self.log(_("NOTE: {text}").format(text=UCUN_TEXT))
        return self.proc.wait()

    # --------------------------------------------------- die Befehle
    def setup(self) -> int:
        """jl-uboot-tool auf den geprüften Stand holen (ohne pip; Abhängigkeiten kommen per apt)"""
        return self.run("setup", "--no-pip", root=False)

    def find(self) -> int:
        return self.run("find")

    def info(self, device: str = "") -> int:
        return self.run("info", *(["--device", device] if device else []))

    def backup(self, folder: str, device: str = "", package: str = "") -> int:
        return self.run("backup", folder, *(["--package", package] if package else []),
                        *(["--device", device] if device else []))

    def _gate_file(self, cmd: str) -> Path:
        p = self.cwd / "logs" / f"pruefung-{cmd}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            p.unlink()
        except FileNotFoundError:
            pass
        self.last_result = p
        return p

    def _sim_image(self, mock_image: str = "") -> list[str]:
        """Simulation: Mock-Flash mit Demo-Kopf und Demo-Firmware (V15-Aufbau) – die Sperre kennt ihn über
        --demo-refs. Ein eigenes Abbild (Tests) über mock_image."""
        if not self.dry_run:
            return []
        if not mock_image:
            from . import flashmodel as fm
            img = self.cwd / "simulation" / "simulierter-flash-rettung.bin"
            img.parent.mkdir(parents=True, exist_ok=True)
            img.write_bytes(fm.demo_dumps()["demo-sauber.bin"])
            mock_image = str(img)
        return ["--mock-image", str(mock_image), "--demo-refs"]

    def last_verdict(self) -> dict:
        """Ergebnis der Sperre aus dem letzten restore/repair_settings ({} = keins)"""
        try:
            return json.loads(Path(self.last_result).read_text("utf-8")) if self.last_result else {}
        except (OSError, ValueError):
            return {}

    def restore(self, package: str, device: str = "", verify_v15: bool = True, mock_image: str = "") -> int:
        """Pflicht-Doppelsicherung, Schreiben nur 0x4000..0x92FFF, Rücklesen – alles im Werkzeug selbst.
        Vorher (flashmodel.preflight, TOOL_TRUNCATES): Pakete, die über 0x92FFF hinausreichen (V16), nie – der
        Unbricker würde sie ohne --verify-v15 abgeschnitten schreiben.
        Im Lauf (rescuegate.py, nach der Sicherung, vor dem Schreiben): nur unter dem Bootkopf ab Werk – bei einem
        V16- oder unbekannten Kopf wird nichts geschrieben (Rückgabe rescuegate.EXIT_REFUSED, Grund in last_verdict)."""
        from . import flashmodel as fm
        try:
            pkg = fm.Package.load(package)
        except fm.PackageError as e:
            raise RuntimeError(_("The package cannot be read ({error}). Nothing was written.").format(error=e))
        if any(c == "TOOL_TRUNCATES" for _lv, c, _t in fm.preflight(pkg, have_backup=True, mode="from4000")):
            raise RuntimeError(_("{name} is {identity} and extends to {end}. In ROM boot mode the tool only writes "
                                 "0x04000–0x92FFF – V16 would then be truncated (end of the program and factory voices "
                                 "missing). Nothing was written. Only the original V15 here, V16 afterwards via the "
                                 "normal update path.").format(name=Path(package).name, identity=pkg.identity,
                                                                end=fm.hx(pkg.write_end - 1)))
        return self.run("restore", package, "--yes", *(["--verify-v15"] if verify_v15 else []),
                        *(["--device", device] if device else []), "--result", str(self._gate_file("restore")),
                        *self._sim_image(mock_image), script=GATE_SCRIPT)

    def repair_settings(self, factory_dump: str, device: str = "", mock_image: str = "") -> int:
        """Einstellungsbereiche 0x93000–0xE8FFF und 0xFC000–0xFEFFF auf einen Werks-Dump (Unbricker repair-settings).
        Im Lauf (rescuegate.py): nur, wenn die frische Sicherung den V15-Aufbau beweist – sonst nichts geschrieben."""
        return self.run("repair-settings", "--factory", factory_dump, "--yes",
                        *(["--device", device] if device else []), "--result", str(self._gate_file("repair-settings")),
                        *self._sim_image(mock_image), script=GATE_SCRIPT)

    def residue(self, command: str, folder: str, result: str, only: list[int] | None = None, device: str = "",
                v16_packages: list[str] | None = None) -> int:
        """„Reste aufräumen“ (flash/restzone.py): command „plan“ (sichern + planen, nichts löschen) oder „clean“
        (sichern + nur die bestätigten V16-Rest-Sektoren löschen, die die frische Sicherung bestätigt)."""
        if command not in ("plan", "clean"):
            raise ValueError(command)
        args = [folder, "--result", result]
        if command == "clean":
            if not only:
                raise RuntimeError(_("Without a confirmed sector list nothing is erased."))
            args += ["--only", ",".join(f"0x{a:05X}" for a in only)]
        for p in v16_packages or []:
            args += ["--v16-package", p]
        if self.dry_run:
            img = Path(folder) / "simulierter-flash.bin"
            if not img.is_file():
                from . import restzone
                Path(folder).mkdir(parents=True, exist_ok=True)
                img.write_bytes(restzone.demo_image())
            args += ["--demo-refs", "--mock-image", str(img)]
        return self.run(command, *args, *(["--device", device] if device else []), script=RESTZONE_SCRIPT)

    def softkey(self, port: str = "") -> int:
        """laufendes FM-1 per Software in den ROM-Bootmodus schicken (für die Notfall-Komplettsicherung)"""
        return self.run("softkey", "--yes", *(["--port", port] if port else []), root=False)

    def cancel(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
