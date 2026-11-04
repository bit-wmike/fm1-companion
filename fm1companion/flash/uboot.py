# SPDX-License-Identifier: GPL-3.0-only
"""Rescue stage 2: FM-1 in ROM boot mode ("WL82 UBOOT1.00", USB 4c4a:8057).

The actual work is done by the bundled, unmodified MvaveFM1Unbricker (MIT, vendor/unbricker/),
which talks to the chip via kagaimiq's jl-uboot-tool (MIT, fetched at a fixed revision only after consent).
Its safety rules apply unchanged: never write below 0x4000, no chip/block erase,
mandatory double backup with comparison, chip key 0x980F and flash ID 0x856014 without override.

This file only adds: strict device detection via sysfs (VID:PID + vendor/model, size),
launch with root privileges via pkexec, dependency check, line-by-line output to the UI.
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
# "Write back stock V15" and "Repair settings" go through this lock: it checks the backup from
# the same run (V16 layout, V16 boot header) before the Unbricker writes (security findings S1/S2, RESEARCH-2026-11)
GATE_SCRIPT = Path(__file__).resolve().parent / "rescuegate.py"
# own files that run as root together with the Unbricker (checksums in uboot_manifest.OWN)
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


# FM-1 with UCUN-UC25HQ80 flash (JEDEC 0xB36014): Melodee 1.6.0 runs on it (#110), but the ROM rescue checks for
# 0x856014 (Unbricker, upstream tools/fm1_recover.py) and stops. Only an understandable message, no other behaviour.
UCUN_FLASH_ID = 0xB36014
UCUN_TEXT = _("This FM-1 has a flash chip from UCUN (UC25HQ80, ID 0xB36014) instead of the usual Puya "
              "(0x856014). The USB ROM recovery (stage 2) does not support this flash yet and therefore stops – "
              "nothing was written. The firmware itself runs on it (Melodee 1.6.0 and later also saves correctly); "
              "normal installations via “Firmware+” work. For a ROM recovery, please wait for a version of the "
              "recovery tool that knows 0xB36014.")
_FLASH_ID_RE = re.compile(r"(?:flash[ _-]?id|jedec|\bid=)\D{0,12}(?:0x)?([0-9a-f]{6})\b", re.I)


def flash_hint(line: str) -> str:
    """Output line of the rescue tool -> German hint (UCUN flash), otherwise ''"""
    m = _FLASH_ID_RE.search(line or "")
    return UCUN_TEXT if m and int(m.group(1), 16) == UCUN_FLASH_ID else ""


@dataclass
class UbootDevice:
    sg: str                 # /dev/sgN – only this device is used
    block: str              # /dev/sdX (display only; never written)
    size: int               # bytes according to sysfs (display / plausibility)
    usb_id: str
    vendor: str
    model: str
    usb_path: str

    @property
    def size_ok(self) -> bool:
        # the ROM sometimes reports 0 or small values as the disk size – the flash itself is read via the loader
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
    """USB paths of all connected devices with 4c4a:8057 (visible even without the sg driver)"""
    out = []
    base = Path(sysroot) / "bus" / "usb" / "devices"
    if base.is_dir():
        for d in sorted(base.iterdir()):
            if f"{_read(d / 'idVendor')}:{_read(d / 'idProduct')}".lower() == UBOOT_USB_ID:
                out.append(d.name)
    return out


def scan(sysroot: str = "/sys", devroot: str = "/dev") -> list[UbootDevice]:
    """only SCSI generic devices whose USB ancestor is 4c4a:8057 AND that report WL82 / UBOOT1.00"""
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
    """apt packages missing for jl-uboot-tool"""
    return [pkg for mod, pkg in DEPS.items() if importlib.util.find_spec(mod) is None]


def work_dir() -> Path:
    d = config.folder("Rettung")
    return d


def jl_dir() -> Path:
    return config.DATA_DIR / "flash" / "jl-uboot-tool"


def tool_ready() -> bool:
    """is jl-uboot-tool (fetched via "Fetch tool") present? The root helper checks the checksums before every start."""
    d = jl_dir()
    return (d / "jluboottool.py").is_file() and (d / "data" / "loaderblobs" / "usb" / "wl82loader.bin").is_file()


# Runs as root (via pkexec): copies the tools into a root-owned temp directory, checks every file against
# the built-in checksum list, runs only the verified copy and hands files that were created as root in the
# rescue folder back to the user. Stored as text in the command (not in a writable file).
# Limit (see SECURITY.md): the checksums ensure the integrity of the tools – they are not a
# security boundary against code that already runs with user privileges. The tool writes backups and logs
# as root into the user's rescue folder; handing them back does not follow symbolic links.
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
    """an output line of ROOT_HELPER or of the pinned rescue scripts (restzone.py, rescuegate.py) in the UI
    language; other lines (the English unbricker) unchanged"""
    for rx, msg in _HELPER_LINES:
        m = rx.match(line)
        if m:
            return _(msg).format(**m.groupdict())
    from . import texts
    return texts.tr(line)


def root_python() -> str:
    """System Python for the root part (that is where the apt packages crcmod, yaml, pycryptodome live)"""
    return "/usr/bin/python3" if Path("/usr/bin/python3").exists() else sys.executable


def root_spec(script: Path, args: list[str], cwd: Path, log_dir: Path) -> dict:
    from . import uboot_manifest as M
    own = {n: [str(OWN_FILES[n]), h] for n, h in M.OWN.items()} if Path(script).name in M.OWN else {}
    return {"vendor": str(VENDOR), "jl_dir": str(jl_dir()), "unbricker": dict(M.UNBRICKER), "jl": dict(M.JL), "own": own,
            "jl_commit": M.JL_COMMIT, "script": Path(script).name, "argv": list(args), "cwd": str(cwd),
            "log_dir": str(log_dir), "chown": [str(cwd)], "uid": os.getuid(), "gid": os.getgid()}


class Runner:
    """runs fm1_unbrick.py / fm1_transporter_recover.py; dry_run = with its built-in mock FM-1"""

    def __init__(self, dry_run: bool = False, log: Callable[[str], None] = print, cwd: Path | None = None,
                 root_cmd: list[str] | None = None):
        self.dry_run = dry_run
        self.log = log
        self.cwd = Path(cwd) if cwd else work_dir()
        self.root_cmd = root_cmd if root_cmd is not None else (["pkexec"] if shutil.which("pkexec") else ["sudo"])
        self.proc: subprocess.Popen | None = None
        self.last_result: Path | None = None
        self.hint = ""                  # German hint from the output (e.g. UCUN flash), otherwise ""

    def argv(self, cmd: str, *args: str, script: Path = SCRIPT, root: bool = True) -> list[str]:
        common = ["--jl-dir", str(jl_dir()), "--log-dir", str(self.cwd / "logs")]
        if self.dry_run:
            common.append("--dry-run")
        base = [sys.executable, "-I", str(script), cmd, *args, *common]
        if self.dry_run or not root:
            return base
        # as root, never run code from writable folders: verified copy via ROOT_HELPER
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

    # --------------------------------------------------- the commands
    def setup(self) -> int:
        """fetch jl-uboot-tool at the verified revision (without pip; dependencies come via apt)"""
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
        """Simulation: mock flash with demo header and demo firmware (V15 layout) – the lock knows it via
        --demo-refs. A custom image (tests) via mock_image."""
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
        """Verdict of the lock from the last restore/repair_settings ({} = none)"""
        try:
            return json.loads(Path(self.last_result).read_text("utf-8")) if self.last_result else {}
        except (OSError, ValueError):
            return {}

    def restore(self, package: str, device: str = "", verify_v15: bool = True, mock_image: str = "") -> int:
        """Mandatory double backup, writing only 0x4000..0x92FFF, read-back – all inside the tool itself.
        Before (flashmodel.preflight, TOOL_TRUNCATES): packages that extend beyond 0x92FFF (V16), never – the
        Unbricker would write them truncated without --verify-v15.
        During the run (rescuegate.py, after the backup, before writing): only under the factory boot header – with a
        V16 or unknown header nothing is written (returns rescuegate.EXIT_REFUSED, reason in last_verdict)."""
        from . import flashmodel as fm
        try:
            pkg = fm.Package.load(package)
        except fm.PackageError as e:
            from . import texts
            raise RuntimeError(_("The package cannot be read ({error}). Nothing was written.").format(
                error=texts.tr(str(e))))
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
        """Settings areas 0x93000–0xE8FFF and 0xFC000–0xFEFFF to a factory dump (Unbricker repair-settings).
        During the run (rescuegate.py): only if the fresh backup proves the V15 layout – otherwise nothing is written."""
        return self.run("repair-settings", "--factory", factory_dump, "--yes",
                        *(["--device", device] if device else []), "--result", str(self._gate_file("repair-settings")),
                        *self._sim_image(mock_image), script=GATE_SCRIPT)

    def residue(self, command: str, folder: str, result: str, only: list[int] | None = None, device: str = "",
                v16_packages: list[str] | None = None) -> int:
        """"Clean up leftovers" (flash/restzone.py): command "plan" (back up + plan, erase nothing) or "clean"
        (back up + erase only the confirmed V16 leftover sectors that the fresh backup confirms)."""
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
        """send a running FM-1 into ROM boot mode via software (for the emergency full backup)"""
        return self.run("softkey", "--yes", *(["--port", port] if port else []), root=False)

    def cancel(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
