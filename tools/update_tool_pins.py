#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Check (and help renew) the tool pins in fm1companion/tools/manifest.json against upstream.

  python3 tools/update_tool_pins.py --check [--download]
      reads the three pinned GitHub releases and compares every pinned file's SHA-256 with GitHub's per-file
      digest (and m-bootloader's fm1-uboot.boot with the release's SHA256SUMS). --download also fetches each file
      and measures size and SHA-256 itself. Exit code 1 on any mismatch; a value GitHub does not publish is
      reported as "not published", never filled in.
  python3 tools/update_tool_pins.py --crates PATH/TO/Cargo.lock
      rewrites fm1companion/tools/bundled/m-flasher/THIRD-PARTY.md from M-Flasher's Cargo.lock at the pinned tag.

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
THIRD = ROOT / "fm1companion" / "tools" / "bundled" / "m-flasher" / "THIRD-PARTY.md"


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


def check(download: bool = False) -> int:
    data = json.loads(MANIFEST.read_text("utf-8"))
    bad = 0
    for key, t in data["tools"].items():
        try:
            rel = json.loads(_get(f"https://api.github.com/repos/{t['repo']}/releases/tags/{t['tag']}",
                                  "application/vnd.github+json"))
        except Exception as e:  # noqa: BLE001
            print(f"!! {key}: release {t['tag']} not readable: {e}")
            bad += 1
            continue
        assets = {a["name"]: a for a in rel.get("assets", [])}
        sums = ""
        if "SHA256SUMS" in assets:
            sums = _get(assets["SHA256SUMS"]["browser_download_url"]).decode("utf-8", "replace")
        for name, e in pinned_files(t).items():
            a = assets.get(name)
            if a is None:
                print(f"!! {key}: {name} is not in release {t['tag']}")
                bad += 1
                continue
            m = re.fullmatch(r"sha256:([0-9a-f]{64})", str(a.get("digest") or ""))
            if m is None:
                print(f"-- {key}: {name}: GitHub publishes no digest")
            elif m.group(1) != e["sha256"]:
                print(f"!! {key}: {name}: GitHub digest {m.group(1)} != pinned {e['sha256']}")
                bad += 1
            else:
                print(f"ok {key}: {name}: GitHub digest matches")
            if a.get("size") is not None and int(a["size"]) != int(e["size"]):
                print(f"!! {key}: {name}: size {a['size']} != pinned {e['size']}")
                bad += 1
            line = next((ln.split()[0] for ln in sums.splitlines() if ln.strip().endswith(" " + name)
                         or ln.strip().endswith("*" + name)), "")
            if line:
                ok = line.lower() == e["sha256"]
                print(f"{'ok' if ok else '!!'} {key}: {name}: SHA256SUMS {'matches' if ok else 'differs'}")
                bad += not ok
            if download:
                b = _get(a["browser_download_url"])
                ok = len(b) == e["size"] and hashlib.sha256(b).hexdigest() == e["sha256"]
                print(f"{'ok' if ok else '!!'} {key}: {name}: downloaded {len(b)} bytes, "
                      f"sha256 {hashlib.sha256(b).hexdigest()}")
                bad += not ok
    return 1 if bad else 0


def crates(lock: Path) -> int:
    data = json.loads(MANIFEST.read_text("utf-8"))["tools"]["m-flasher"]
    text = Path(lock).read_text("utf-8")
    found = re.findall(r'\[\[package\]\]\nname = "([^"]+)"\nversion = "([^"]+)"(?:\nsource = "([^"]+)")?', text)
    rows = [f"| {n} | {v} |" for n, v, src in found if src]
    head = THIRD.read_text("utf-8").split("| Crate | Version |")[0] if THIRD.exists() else ""
    if f"tag {data['tag']}" not in head:
        head = (f"# M-Flasher {data['version']} – Rust crates compiled into the binary\n\n"
                f"Generated from `Cargo.lock` at tag {data['tag']} (commit {data['commit']}).\n\n")
    THIRD.write_text(head + "| Crate | Version |\n| --- | --- |\n" + "\n".join(rows) + "\n", "utf-8")
    print(f"{THIRD.relative_to(ROOT)}: {len(rows)} crates")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--download", action="store_true")
    ap.add_argument("--crates", metavar="CARGO_LOCK")
    a = ap.parse_args(argv)
    if a.crates:
        return crates(Path(a.crates))
    if a.check:
        return check(a.download)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
