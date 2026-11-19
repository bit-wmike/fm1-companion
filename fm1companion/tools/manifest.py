# SPDX-License-Identifier: GPL-3.0-only
"""The pinned tool manifest (fm1companion/tools/manifest.json): which version of M-Flasher, m-bootloader and SLOOP 3
the app bundles, and the SHA-256 of every file. Pure data access, no I/O beyond reading the manifest."""
from __future__ import annotations

import hashlib
import json
import platform
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "manifest.json"
BUNDLED = HERE / "bundled"                    # licence files, m-bootloader and SLOOP 3 files (in git)
APP_ROOT = HERE.parents[1]                    # the folder that holds fm1companion/ (repo, /usr/lib/fm1-companion …)
BIN_DIR = APP_ROOT / "tools-bin"              # M-Flasher binaries (not in git: placed by the packages)

KEYS = ("m-flasher", "m-bootloader", "sloop3")
ARCHES = ("aarch64", "x86_64")
_MACHINE = {"aarch64": "aarch64", "arm64": "aarch64", "armv8l": "aarch64",
            "x86_64": "x86_64", "amd64": "x86_64"}


@lru_cache(maxsize=1)
def load() -> dict:
    return json.loads(MANIFEST.read_text("utf-8"))


def tool(key: str, data: dict | None = None) -> dict:
    tools = (data or load())["tools"]
    if key not in tools:
        raise KeyError(key)
    return tools[key]


def arch(machine: str | None = None) -> str:
    """'aarch64' / 'x86_64' for this computer, '' where M-Flasher has no build (armhf = 32-bit Raspberry Pi OS …)"""
    m = (machine if machine is not None else platform.machine()).strip().lower()
    return _MACHINE.get(m, "")


def mflasher_relpath(version: str, a: str) -> str:
    """where a bundled M-Flasher binary lives below tools-bin/ (the same in the repo, the .deb and the .run)"""
    return f"m-flasher/{version}/{a}/m-flasher"


def bundled_files(key: str, data: dict | None = None) -> dict[str, dict]:
    """{file name: entry} of the files bundled in git (m-bootloader, SLOOP 3)"""
    return dict(tool(key, data).get("files") or {})


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def write_verified(key: str, version: str, data: dict | None = None) -> bool:
    """an app release has checked this version's command line and output for writing (owner rule: a newer M-Flasher
    that came in through an update is used for read-only commands until an app update lists it here)"""
    return version in (tool(key, data).get("write_verified") or [])
