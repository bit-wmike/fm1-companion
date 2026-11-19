#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Check (and help renew) the tool pins in fm1companion/tools/manifest.json against upstream.

  python3 tools/update_tool_pins.py --check [--download]
      reads the three pinned GitHub releases and compares every pinned file's SHA-256 with GitHub's per-file
      digest (and m-bootloader's fm1-uboot.boot with the release's SHA256SUMS). --download also fetches each file
      and measures size and SHA-256 itself. Exit code 1 on any mismatch, and also when a pinned file is
      confirmed neither by GitHub's digest nor by a SHA256SUMS line of its release.
  (M-Flasher's third-party licence file: tools/gen_mflasher_licences.py)

The manifest itself is edited by hand in a pull request (a new pin is a review decision); this script only says
whether the values match upstream. Needs network access to api.github.com and github.com.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "fm1companion" / "tools" / "manifest.json"


def _get(url: str, accept: str = "application/octet-stream") -> bytes:
    if not url.startswith("https://"):
        raise SystemExit(f"refusing a non-https URL: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "fm1-companion-pins", "Accept": accept})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read(64 * 1024 * 1024)


def pinned_files(t: dict) -> dict[str, dict]:
    """{asset name: {"sha256", "size"}} of a tool's pinned release files"""
    if "arch" in t:
        return {e["asset"]: e for e in t["arch"].values()}
    return dict(t.get("files") or {})


def judge(key: str, name: str, e: dict, a: dict | None, sums: str) -> list[tuple[bool, str]]:
    """the checks of one pinned file against its release asset: [(ok, message)]. A file is confirmed only by GitHub's
    digest or by a SHA256SUMS line of the same release; with neither it FAILS (never "not published, fine")."""
    if a is None:
        return [(False, f"{key}: {name} is not in the release")]
    out = []
    m = re.fullmatch(r"sha256:([0-9a-f]{64})", str(a.get("digest") or ""))
    line = next((ln.split()[0].lower() for ln in sums.splitlines()
                 if len(ln.split()) == 2 and ln.split()[1].lstrip("*") == name), "")
    if m is not None:
        out.append((m.group(1) == e["sha256"], f"{key}: {name}: GitHub digest "
                    + ("matches" if m.group(1) == e["sha256"] else f"{m.group(1)} != pinned {e['sha256']}")))
    if line:
        out.append((line == e["sha256"], f"{key}: {name}: SHA256SUMS " + ("matches" if line == e["sha256"]
                                                                          else "differs")))
    if m is None and not line:
        out.append((False, f"{key}: {name}: GitHub publishes no digest and no SHA256SUMS line confirms it"))
    if a.get("size") is not None and int(a["size"]) != int(e["size"]):
        out.append((False, f"{key}: {name}: size {a['size']} != pinned {e['size']}"))
    return out


def check(download: bool = False, get=None) -> int:
    get = get or _get
    data = json.loads(MANIFEST.read_text("utf-8"))
    bad = 0
    for key, t in data["tools"].items():
        try:
            rel = json.loads(get(f"https://api.github.com/repos/{t['repo']}/releases/tags/{t['tag']}",
                                 "application/vnd.github+json"))
        except Exception as e:  # noqa: BLE001
            print(f"!! {key}: release {t['tag']} not readable: {e}")
            bad += 1
            continue
        assets = {a["name"]: a for a in rel.get("assets", [])}
        sums = ""
        if "SHA256SUMS" in assets:
            sums = get(assets["SHA256SUMS"]["browser_download_url"]).decode("utf-8", "replace")
        for name, e in pinned_files(t).items():
            for ok, msg in judge(key, name, e, assets.get(name), sums if name != "SHA256SUMS" else ""):
                print(("ok " if ok else "!! ") + msg)
                bad += not ok
            if download and name in assets:
                b = get(assets[name]["browser_download_url"])
                ok = len(b) == e["size"] and hashlib.sha256(b).hexdigest() == e["sha256"]
                print(f"{'ok' if ok else '!!'} {key}: {name}: downloaded {len(b)} bytes, "
                      f"sha256 {hashlib.sha256(b).hexdigest()}")
                bad += not ok
    return 1 if bad else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--download", action="store_true")
    a = ap.parse_args(argv)
    if a.check:
        return check(a.download)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
