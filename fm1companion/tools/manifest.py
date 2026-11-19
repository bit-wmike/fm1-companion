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


def write_verified(key: str, version: str, sha256: str, arch: str = "", data: dict | None = None) -> bool:
    """may these exact bytes be used for writing? For M-Flasher only a binary whose SHA-256 an app release lists in
    manifest.json ``write_verified`` {version: {arch: sha256}} (owner rule: a newer M-Flasher that came in through an
    update is used for reading only, until an app update has reviewed exactly those bytes). For m-bootloader and
    SLOOP 3: only the bundled file's bytes."""
    t = tool(key, data)
    sha256 = (sha256 or "").lower()
    if not sha256:
        return False
    if key == "m-flasher":
        wv = t.get("write_verified") or {}
        return isinstance(wv, dict) and str((wv.get(version) or {}).get(arch) or "").lower() == sha256
    return version == t["version"] and any(str(e.get("sha256") or "").lower() == sha256
                                           for n, e in (t.get("files") or {}).items() if n != "SHA256SUMS")


def safe_component(name: str) -> bool:
    """one plain path component (no separators, no '..', no hidden or empty name)"""
    import re
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,120}", name or "")) and ".." not in name


def safe_relpath(rel: str, parts: int = 2) -> bool:
    """a relative path of at most `parts` safe components ("x86_64/m-flasher", "fm1-uboot.boot")"""
    bits = (rel or "").split("/")
    return 0 < len(bits) <= parts and all(safe_component(b) for b in bits)
