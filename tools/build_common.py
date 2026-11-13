# SPDX-License-Identifier: GPL-3.0-only
"""Shared by build_deb.py and build_run.py: which files belong to the app, and the fixed values that make
the packages reproducible (same commit -> bit-identical .deb and .run)."""
from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
EPOCH = 1793350800                 # fixed time stamp of every file inside the packages
MAINTAINER = "bit-wmike <295828+bit-wmike@users.noreply.github.com>"


def version() -> str:
    for line in (ROOT / "fm1companion" / "__init__.py").read_text().splitlines():
        if line.startswith("__version__"):
            return line.split("=", 1)[1].strip().strip('"\'')
    raise SystemExit("no version found")


def tracked_files(*paths: str) -> list[Path]:
    """The files below the given paths that belong to the project: only files TRACKED by git (never untracked
    strays such as test output or notes lying in the checkout). Without git (a source archive) every file below
    the paths is taken. No bytecode."""
    try:
        out = subprocess.run(["git", "ls-files", "-z", "--", *paths], cwd=ROOT, capture_output=True, text=True,
                             check=True).stdout.split("\0")
        files = [ROOT / f for f in out if f and (ROOT / f).is_file()]
        if not files:
            raise OSError("not a git checkout")
    except (OSError, subprocess.CalledProcessError):
        files = []
        for e in paths:
            p = ROOT / e
            files += [x for x in p.rglob("*") if x.is_file()] if p.is_dir() else ([p] if p.is_file() else [])
    return sorted(p for p in set(files) if "__pycache__" not in p.parts and p.suffix != ".pyc")


def app_files() -> list[Path]:
    """all files of the fm1companion package without bytecode (tracked files only, see tracked_files)."""
    return tracked_files("fm1companion")


def release_date(ver: str | None = None) -> str:
    """'YYYY-MM-DD' of this version from the AppStream metadata (packaging/fm1-companion.metainfo.xml)."""
    ver = ver or version()
    text = (ROOT / "packaging" / "fm1-companion.metainfo.xml").read_text("utf-8")
    m = re.search(rf'<release version="{re.escape(ver)}" date="(\d{{4}}-\d{{2}}-\d{{2}})"', text)
    if not m:
        raise SystemExit(f"no release {ver} with a date in packaging/fm1-companion.metainfo.xml")
    return m.group(1)


def changelog_date(ver: str | None = None) -> str:
    """the release date in the form a Debian changelog wants: 'Thu, 12 Nov 2026 12:00:00 +0000'"""
    t = time.strptime(release_date(ver), "%Y-%m-%d")
    days = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    return f"{days[t.tm_wday]}, {t.tm_mday:02d} {months[t.tm_mon - 1]} {t.tm_year} 12:00:00 +0000"
