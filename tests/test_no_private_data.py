# SPDX-License-Identifier: GPL-3.0-only
"""No personal or device-identifying data of the people who test on real hardware may end up in the repository.

The values themselves are not written here – only their SHA-256, so this file does not leak what it guards.
Every tracked text file is scanned for tokens that look like unit IDs, serial numbers, profile IDs, flash-dump
hashes, home folders and host names, and each token's hash is compared with the list. Tests and fixtures use
synthetic values instead (see tests/test_pi_hardware.py).
To add a value: append hashlib.sha256(value.lower().encode()).hexdigest() to FORBIDDEN – never the value.
"""
import hashlib
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

FORBIDDEN = {
    "10670dc9806bd66a1f83d93169d0756a3000960f36a1db8091b72f9a2e664ebc",   # unit ID
    "c824de0741eb190d32b5edcd5eb95d4d8b7801cb718ef3b3eb32632c12914f83",   # the same, other byte order
    "91a4e31fb1605a584fe135b309c13e1d849075b623fde0ea3f26e12f6766eb9f",   # unit ID
    "06a795cb7e7b92c7ac1d53d360a7afb3f6468dd40965fafb3f12b0ddf45c4feb",   # profile ID
    "5dc52776f18b6a5158c7c92f242ae71292d2be06d652300f88c448127daf46ba",   # profile ID
    "0a6efd7288354a6c0f6ceb1dc25c039a9093c4dfd0eaf92b95fb20e63b74e441",   # flash dump hash
    "ca16c2469d9ac1a0fbed16c4afef2fdd9beb275102c54842b01976fc2e4b47e9",   # flash dump hash
    "3ba025c4ebe7b682b312c29309ef2e00c5429e8ee262a6d2da792170218d402e",   # home folder
    "cf1b32745ed14e5b596eafeb62c440de027595d150676c7a41298d1b7d078d7f",   # host name
    "4807a37e2fe79b5b81e90391ddc75e6afa3beceab7f8366c3e25af960b58b62a",   # profile name
}

TOKENS = re.compile(r"\b[0-9A-Fa-f]{10}\b|\b[0-9A-Fa-f]{16}\b|\b[0-9A-Fa-f]{64}\b|/home/[\w.-]+|\b[\w-]+-\d+gb\b"
                    r"|FM-1 [^\s\"'<>(),]+/[^\s\"'<>(),]+", re.I)


def _tracked():
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return [ROOT / p for p in out.decode("utf-8", "replace").split("\0") if p]


def _hits(text: str):
    """line numbers of forbidden tokens (never the token: CI logs are public)"""
    for m in TOKENS.finditer(text):
        if hashlib.sha256(m.group(0).lower().encode("utf-8")).hexdigest() in FORBIDDEN:
            yield text.count("\n", 0, m.start()) + 1


def test_the_scanner_finds_what_it_is_meant_to_find():
    # synthetic stand-ins hashed on the fly: the scanner itself must work for every pattern
    for sample in ("ab12cd34ef", "0123456789abcdef", "/home/someone", "box-8gb", "FM-1 Red/Blue", "f" * 64):
        h = hashlib.sha256(sample.lower().encode()).hexdigest()
        FORBIDDEN.add(h)
        try:
            assert list(_hits(f"x {sample} y")), sample
        finally:
            FORBIDDEN.discard(h)


def test_no_private_data_in_tracked_files():
    found = []
    for p in _tracked():
        if not p.is_file() or p.stat().st_size > 5_000_000:
            continue
        try:
            text = p.read_text("utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for line in _hits(text):
            found.append(f"{p.relative_to(ROOT)}:{line}")
    assert not found, "private data in the repository (replace with synthetic values):\n" + "\n".join(found)
