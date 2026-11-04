# SPDX-License-Identifier: GPL-3.0-only
"""Shared by build_deb.py and build_run.py: which files belong to the app."""
from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"


def version() -> str:
    for line in (ROOT / "fm1companion" / "__init__.py").read_text().splitlines():
        if line.startswith("__version__"):
            return line.split("=", 1)[1].strip().strip('"\'')
    raise SystemExit("no version found")


def app_files() -> list[Path]:
    """all files of the fm1companion package without bytecode (via git, otherwise the file system)."""
    try:
        out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "fm1companion"], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout.split("\n")
        files = [ROOT / f for f in out if f and (ROOT / f).is_file()]
    except (OSError, subprocess.CalledProcessError):
        files = [p for p in (ROOT / "fm1companion").rglob("*") if p.is_file()]
    return sorted(p for p in files if "__pycache__" not in p.parts and p.suffix != ".pyc")
