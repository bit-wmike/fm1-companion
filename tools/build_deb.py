#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Build the Debian package: dist/fm1-companion_<version>_all.deb

  python3 tools/build_deb.py [--maintainer "Name <mail>"] [--out DIR]

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
from build_common import DIST, ROOT, app_files, version  # noqa: E402

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
exit 0
"""
PRERM = """#!/bin/sh
set -e
find "${DPKG_ROOT:-}/usr/lib/fm1-companion" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
exit 0
"""


def build(out: Path = DIST, maintainer: str = "FM-1 Companion Suite <fm1-companion@localhost>") -> Path:
    ver = version()
    with tempfile.TemporaryDirectory(prefix="fm1c-deb-") as tmp:
        root = Path(tmp) / "root"
        lib = root / "usr/lib/fm1-companion"
        for f in app_files():
            dst = lib / f.relative_to(ROOT)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dst)
        (lib / "installed-by-deb").write_text("Dieses Verzeichnis gehört zum Debian-Paket fm1-companion.\n")
        b = root / "usr/bin/fm1-companion"
        b.parent.mkdir(parents=True)
        b.write_text(LAUNCHER)
        desk = (ROOT / "data/fm1-companion.desktop.in").read_text().replace("@EXEC@", "fm1-companion")
        desk += "Actions=demo;\n\n[Desktop Action demo]\nName=Demo-Modus (ohne Gerät)\nExec=fm1-companion --demo\n"
        (root / "usr/share/applications").mkdir(parents=True)
        (root / "usr/share/applications/fm1-companion.desktop").write_text(desk)
        for d in (ROOT / "data/icons/hicolor").iterdir():
            t = root / "usr/share/icons/hicolor" / d.name / "apps"
            t.mkdir(parents=True)
            shutil.copy2(d / "apps/fm1-companion.png", t)
        t = root / "usr/share/icons/hicolor/scalable/apps"
        t.mkdir(parents=True)
        shutil.copy2(ROOT / "data/fm1-companion.svg", t)
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
            "Files: *\nCopyright: FM-1 Companion contributors\nLicense: GPL-3.0-only\n On Debian systems see "
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
            "Files: usr/lib/fm1-companion/fm1companion/sims/*\nCopyright: see the NOTICE file in each folder\n"
            "License: GPL-3.0-only (Melodee); GPL-3.0-only WITH FM1 SDK linking exception (FM1 Open)\n Corresponding "
            "source: see NOTICE (written offer, tarballs attached to the GitHub release).\n")
        with gzip.GzipFile(doc / "changelog.gz", "wb", mtime=0) as g:
            g.write(f"fm1-companion ({ver}) stable; urgency=low\n\n  * Release {ver}, see "
                    "https://github.com/bit-wmike/fm1-companion/releases\n\n -- {maintainer}  "
                    "Mon, 02 Nov 2026 12:00:00 +0100\n".encode())
        for p in root.rglob("*"):
            p.chmod(0o755 if p.is_dir() or p == b else 0o644)
        size = sum(p.stat().st_size for p in root.rglob("*") if p.is_file()) // 1024 + 1
        deb = root / "DEBIAN"
        deb.mkdir()
        (deb / "control").write_text(
            f"Package: fm1-companion\nVersion: {ver}\nArchitecture: all\nMaintainer: {maintainer}\n"
            f"Installed-Size: {size}\nDepends: {diag.debian_field('required')}\n"
            f"Recommends: {diag.debian_field('recommended')}\nSuggests: {diag.debian_field('suggested')}\n"
            "Section: sound\nPriority: optional\nHomepage: https://github.com/bit-wmike/fm1-companion\n"
            "Description: Desktop-Suite für den M-VAVE FM-1\n"
            " Firmware installieren und wechseln, Backups, Projekte, Presets, Samples, MIDI,\n"
            " Simulator und Aufnahme für den Taschen-Synthesizer M-VAVE FM-1 mit Firmwares\n"
            " der Felucca-Familie (Melodee, Skiff, SLOOP). Keine Telemetrie.\n")
        for name, body in (("postinst", POSTINST), ("prerm", PRERM)):
            (deb / name).write_text(body)
            (deb / name).chmod(0o755)
        out.mkdir(parents=True, exist_ok=True)
        target = out / f"fm1-companion_{ver}_all.deb"
        env = dict(os.environ, SOURCE_DATE_EPOCH="1793350800")
        subprocess.run(["dpkg-deb", "--root-owner-group", "-Zxz", "--build", str(root), str(target)], check=True,
                       env=env, stdout=subprocess.DEVNULL)
    return target


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DIST))
    ap.add_argument("--maintainer", default="FM-1 Companion Suite <fm1-companion@localhost>")
    a = ap.parse_args()
    print(build(Path(a.out), a.maintainer))
