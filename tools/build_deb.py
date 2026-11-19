#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Build the Debian package: dist/fm1-companion_<version>_all.deb

  python3 tools/build_deb.py [--maintainer "Name <mail>"] [--out DIR] [--require-tools]

The package stays Architecture: all. It carries the bundled M-Flasher for both builds (aarch64, x86_64) from
tools-bin/ (tools/fetch_tools.py; --require-tools: stop when one is missing) under
/usr/lib/fm1-companion/tools-bin; the app picks the one for the computer and checks its SHA-256 before every run.
It installs the udev rule for the FM-1 in ROM download mode and loads `sg` at boot (owner decision, 2026-11-19).

Layout: /usr/lib/fm1-companion/fm1companion, /usr/bin/fm1-companion (always /usr/bin/python3),
start menu entry, hicolor icons, AppStream metadata, man page. Dependencies from fm1companion/data/deps.json.
"""
from __future__ import annotations

import argparse
import gzip
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_common import DIST, EPOCH, MAINTAINER, ROOT, app_files, changelog_date, tool_binaries, version  # noqa: E402

sys.path.insert(0, str(ROOT))
from fm1companion import diag  # noqa: E402

LAUNCHER = """#!/bin/sh
# FM-1 Companion Suite – immer das System-Python (auch wenn ein venv aktiv ist)
unset PYTHONHOME
export PYTHONPATH=/usr/lib/fm1-companion
exec /usr/bin/python3 -m fm1companion "$@"
"""
POSTINST = """#!/bin/sh
set -e
if [ "$1" = configure ] && [ -x /usr/bin/python3 ]; then
  /usr/bin/python3 -m compileall -q "${DPKG_ROOT:-}/usr/lib/fm1-companion/fm1companion" >/dev/null 2>&1 || true
fi
if [ "$1" = configure ]; then
  # the bundled M-Flasher: check the SHA-256 (the app checks it again before every run and never runs a changed one)
  d="${DPKG_ROOT:-}/usr/lib/fm1-companion/tools-bin"
  if [ -f "$d/SHA256SUMS" ] && command -v sha256sum >/dev/null 2>&1; then
    if ! (cd "$d" && sha256sum --quiet -c SHA256SUMS) >/dev/null 2>&1; then
      echo "fm1-companion: WARNING: a bundled M-Flasher binary does not match its checksum; the app will not run it. Reinstall the package." >&2
    fi
  fi
  # FM-1 in ROM download mode (USB 4c4a:8057): the sg driver now, the udev rule for a unit already connected
  # (both take effect by themselves at the next boot or connection, too)
  if [ -z "${DPKG_ROOT:-}" ]; then
    modprobe sg 2>/dev/null || true
    if command -v udevadm >/dev/null 2>&1; then
      udevadm control --reload-rules 2>/dev/null || true
      udevadm trigger --action=add --subsystem-match=scsi_generic 2>/dev/null || true
      udevadm trigger --action=add --subsystem-match=usb --attr-match=idVendor=4c4a 2>/dev/null || true
    fi
  fi
fi
exit 0
"""
POSTRM = """#!/bin/sh
set -e
if [ "$1" = remove ] || [ "$1" = purge ]; then
  if [ -z "${DPKG_ROOT:-}" ] && command -v udevadm >/dev/null 2>&1; then
    udevadm control --reload-rules 2>/dev/null || true
  fi
fi
exit 0
"""
PRERM = """#!/bin/sh
set -e
find "${DPKG_ROOT:-}/usr/lib/fm1-companion" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
exit 0
"""


def build(out: Path = DIST, maintainer: str = MAINTAINER, require_tools: bool = False) -> Path:
    ver = version()
    tools = tool_binaries(require_tools)
    with tempfile.TemporaryDirectory(prefix="fm1c-deb-") as tmp:
        root = Path(tmp) / "root"
        lib = root / "usr/lib/fm1-companion"
        for f in app_files():
            dst = lib / f.relative_to(ROOT)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dst)
        (lib / "installed-by-deb").write_text("This directory belongs to the Debian package fm1-companion.\n")
        exes = []
        if tools:                           # M-Flasher for both architectures (the app picks; arch: all package)
            import hashlib
            sums = []
            for rel, f in tools:
                dst = lib / "tools-bin" / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(f, dst)
                exes.append(dst)
                sums.append(f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {rel}\n")
            (lib / "tools-bin" / "SHA256SUMS").write_text("".join(sums))
        b = root / "usr/bin/fm1-companion"
        b.parent.mkdir(parents=True)
        b.write_text(LAUNCHER)
        desk = (ROOT / "data/fm1-companion.desktop.in").read_text().replace("@EXEC@", "fm1-companion")
        desk += "Actions=demo;\n\n[Desktop Action demo]\nName=Demo mode (no device)\nName[de]=Demo-Modus (ohne Gerät)\nExec=fm1-companion --demo\n"
        (root / "usr/share/applications").mkdir(parents=True)
        (root / "usr/share/applications/fm1-companion.desktop").write_text(desk)
        for d in (ROOT / "data/icons/hicolor").iterdir():
            t = root / "usr/share/icons/hicolor" / d.name / "apps"
            t.mkdir(parents=True)
            shutil.copy2(d / "apps/fm1-companion.png", t)
        t = root / "usr/share/icons/hicolor/scalable/apps"
        t.mkdir(parents=True)
        shutil.copy2(ROOT / "data/fm1-companion.svg", t)
        # optional udev rule (keeps the FM-1 and its USB hubs awake): shipped INACTIVE – the package never activates
        # it by itself; the system check names the command (sudo install … /etc/udev/rules.d/)
        udev = root / "usr/share/fm1-companion/udev"
        udev.mkdir(parents=True)
        shutil.copy2(ROOT / "packaging/udev/60-fm1-companion.rules", udev)
        # FM-1 in ROM download mode reachable for the user at the computer (uaccess) and `sg` at boot: ACTIVE,
        # owner decision 2026-11-19 (the bundled M-Flasher then needs no root)
        rules = root / "usr/lib/udev/rules.d"
        rules.mkdir(parents=True)
        shutil.copy2(ROOT / "packaging/udev/70-fm1-companion-download-mode.rules", rules)
        mods = root / "usr/lib/modules-load.d"
        mods.mkdir(parents=True)
        shutil.copy2(ROOT / "packaging/udev/fm1-companion-sg.conf", mods)
        (root / "usr/share/metainfo").mkdir(parents=True)
        shutil.copy2(ROOT / "packaging/fm1-companion.metainfo.xml", root / "usr/share/metainfo")
        man = root / "usr/share/man/man1"
        man.mkdir(parents=True)
        with gzip.GzipFile(man / "fm1-companion.1.gz", "wb", mtime=0) as g:
            g.write((ROOT / "packaging/fm1-companion.1").read_bytes())
        doc = root / "usr/share/doc/fm1-companion"
        doc.mkdir(parents=True)
        (doc / "copyright").write_text(
            "Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/\nUpstream-Name: FM-1 Companion Suite\n\n"
            "Files: *\nCopyright: 2026 bit-wmike and the FM-1 Companion contributors\nLicense: GPL-3.0-only\n On Debian systems see "
            "/usr/share/common-licenses/GPL-3.\n\nFiles: usr/lib/fm1-companion/fm1companion/icons/lucide/*\n"
            "Copyright: Lucide Contributors; Cole Bemis (Feather)\nLicense: ISC and MIT\n See "
            "/usr/lib/fm1-companion/fm1companion/icons/LICENSE.lucide\n\nFiles: usr/lib/fm1-companion/fm1companion/vendor/*\n"
            "Copyright: Kerem Kilic (Ellic Studio); Leo Kuroshita, Hügelton Instruments\nLicense: GPL-3.0-only\n\n"
            "Files: usr/lib/fm1-companion/fm1companion/vendor/unbricker/*\nCopyright: Michael P Jones\nLicense: MIT\n See "
            "/usr/lib/fm1-companion/fm1companion/vendor/unbricker/LICENSE\n\n"
            "Files: usr/lib/fm1-companion/fm1companion/vendor/unbricker/transporter/*.uf2\n"
            "Copyright: kurogedelic (FM-1 Transporter); Raspberry Pi (Trading) Ltd. (pico-sdk); Ha Thach (TinyUSB); "
            "sekigon-gonnoc (Pico-PIO-USB)\nLicense: MIT and BSD-3-Clause\n See "
            "/usr/lib/fm1-companion/fm1companion/vendor/unbricker/transporter/THIRD_PARTY.md\n\n"
            "Files: usr/lib/fm1-companion/fm1companion/sims/*\nCopyright: the authors of each firmware project; see "
            "the NOTICE file in each folder\nLicense: GPL-3.0-only, or GPL-3.0-only WITH FM1 SDK linking "
            "exception (FM1 Open); the exact licence of each build is in its folder (LICENSE*, NOTICE)\n "
            "Corresponding source: see NOTICE (written offer, tarballs attached to the GitHub release).\n\n"
            "Files: usr/lib/fm1-companion/tools-bin/m-flasher/* usr/lib/fm1-companion/fm1companion/tools/bundled/m-flasher/*\n"
            "Copyright: 2026 Julius Vitkauskas (M-Flasher); the authors of the bundled fonts and Rust crates\n"
            "License: MIT and OFL-1.1\n M-Flasher, bundled unchanged from its GitHub release. See "
            "/usr/lib/fm1-companion/fm1companion/tools/bundled/m-flasher/ (LICENSE, NOTICE.md, copyright, OFL-*.txt, "
            "THIRD-PARTY.md)\n\n"
            "Files: usr/lib/fm1-companion/fm1companion/tools/bundled/m-bootloader/*\n"
            "Copyright: 2026 Julius Vitkauskas\nLicense: MIT\n m-bootloader, bundled unchanged from its GitHub "
            "release. See /usr/lib/fm1-companion/fm1companion/tools/bundled/m-bootloader/LICENSE\n\n"
            "Files: usr/lib/fm1-companion/fm1companion/tools/bundled/sloop3/*\n"
            "Copyright: isod89 (SLOOP 3)\nLicense: GPL-3.0; samples CC0-1.0; fonts OFL-1.1 (Inter) and Terminus\n "
            "SLOOP 3, bundled unchanged from its GitHub release. Corresponding source: see "
            "/usr/lib/fm1-companion/fm1companion/tools/bundled/sloop3/NOTICE\n")
        with gzip.GzipFile(doc / "changelog.gz", "wb", mtime=0) as g:
            g.write(f"fm1-companion ({ver}) stable; urgency=low\n\n  * Release {ver}, see "
                    f"https://github.com/bit-wmike/fm1-companion/releases\n\n -- {maintainer}  "
                    f"{changelog_date(ver)}\n".encode())
        for p in root.rglob("*"):
            p.chmod(0o755 if p.is_dir() or p == b or p in exes else 0o644)
        for p in [root, *root.rglob("*")]:             # same commit -> same package, whatever the checkout's file times
            os.utime(p, (EPOCH, EPOCH))
        size = sum(p.stat().st_size for p in root.rglob("*") if p.is_file()) // 1024 + 1
        deb = root / "DEBIAN"
        deb.mkdir()
        (deb / "control").write_text(
            f"Package: fm1-companion\nVersion: {ver}\nArchitecture: all\nMaintainer: {maintainer}\n"
            f"Installed-Size: {size}\nDepends: {diag.debian_field('required')}\n"
            f"Recommends: {diag.debian_field('recommended')}\nSuggests: {diag.debian_field('suggested')}\n"
            "Section: sound\nPriority: optional\nHomepage: https://github.com/bit-wmike/fm1-companion\n"
            "Description: Desktop suite for the M-VAVE FM-1\n"
            " Desktop suite for the M-VAVE FM-1 pocket synthesizer: backups, firmware\n"
            " install and recovery, a Studio with digital twins of 13 firmwares (Melodee,\n"
            " Skiff, SLOOP, JIANT FM, KARMA and more), presets, samples, MIDI tools and a\n"
            " catalog of more than 250 community firmwares. No telemetry.\n"
            " .\n"
            " Unofficial community project, not affiliated with M-VAVE. Installing firmware\n"
            " and recovery change the device and are done at your own risk.\n")
        for name, body in (("postinst", POSTINST), ("prerm", PRERM), ("postrm", POSTRM)):
            (deb / name).write_text(body)
            (deb / name).chmod(0o755)
        out.mkdir(parents=True, exist_ok=True)
        target = out / f"fm1-companion_{ver}_all.deb"
        for p in [deb, *deb.iterdir()]:
            os.utime(p, (EPOCH, EPOCH))
        env = dict(os.environ, SOURCE_DATE_EPOCH=str(EPOCH))
        subprocess.run(["dpkg-deb", "--root-owner-group", "-Zxz", "--build", str(root), str(target)], check=True,
                       env=env, stdout=subprocess.DEVNULL)
    return target


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DIST))
    ap.add_argument("--maintainer", default=MAINTAINER)
    ap.add_argument("--require-tools", action="store_true", help="stop when a bundled M-Flasher binary is missing")
    a = ap.parse_args()
    print(build(Path(a.out), a.maintainer, a.require_tools))
