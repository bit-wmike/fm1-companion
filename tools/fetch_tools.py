#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Fetch the pinned M-Flasher binaries for the packages (build time; standard library only).

  python3 tools/fetch_tools.py [--arch aarch64|x86_64 …] [--from DIR] [--cache DIR] [--check]

Reads fm1companion/tools/manifest.json, downloads each pinned release tarball (or takes it from --from DIR /
the download cache), checks its size and SHA-256, takes exactly the member ``m-flasher/m-flasher`` (a regular file),
checks its size and SHA-256 too and writes it to ``tools-bin/m-flasher/<version>/<arch>/m-flasher`` (mode 0755).
Nothing that fails a check is kept. --check only verifies what is in tools-bin/ already.
build_deb.py and build_run.py take the binaries from tools-bin/ (the release workflow runs this script first and
builds with --require-tools). tools-bin/ is not in git.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "fm1companion" / "tools" / "manifest.json"
BIN = ROOT / "tools-bin"
CACHE = ROOT / "build" / "tool-downloads"
MAX = 64 * 1024 * 1024


def manifest() -> dict:
    return json.loads(MANIFEST.read_text("utf-8"))


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def relpath(version: str, arch: str) -> str:
    return f"m-flasher/{version}/{arch}/m-flasher"          # = fm1companion.tools.manifest.mflasher_relpath


def expected_binaries(data: dict | None = None) -> dict[str, dict]:
    """{path below tools-bin: {"sha256", "size", "arch"}} for every pinned M-Flasher binary"""
    t = (data or manifest())["tools"]["m-flasher"]
    return {relpath(t["version"], a): {"sha256": e["binary_sha256"], "size": e["binary_size"], "arch": a}
            for a, e in t["arch"].items()}


def verify_tree(bin_dir: Path = BIN, data: dict | None = None) -> tuple[dict[str, Path], list[str]]:
    """({relpath: file} of the binaries present and correct, [problems])"""
    good, problems = {}, []
    for rel, e in expected_binaries(data).items():
        p = Path(bin_dir) / rel
        if not p.is_file():
            problems.append(f"{rel}: missing")
            continue
        b = p.read_bytes()
        if len(b) != e["size"] or sha256(b) != e["sha256"]:
            problems.append(f"{rel}: size/SHA-256 differ from the manifest")
            continue
        good[rel] = p
    return good, problems


def _download(url: str) -> bytes:
    if not url.startswith("https://"):
        raise SystemExit(f"refusing a non-https URL: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "fm1-companion-build"})
    with urllib.request.urlopen(req, timeout=120) as r:          # (redirects to GitHub's storage stay https)
        if not r.geturl().startswith("https://"):
            raise SystemExit(f"redirected to a non-https URL: {r.geturl()}")
        data = r.read(MAX + 1)
    if len(data) > MAX:
        raise SystemExit(f"{url}: larger than {MAX >> 20} MB")
    return data


def fetch(arches: list[str] | None = None, src: Path | None = None, cache: Path = CACHE, bin_dir: Path = BIN,
          log=print) -> int:
    data = manifest()
    t = data["tools"]["m-flasher"]
    bad = 0
    for arch, e in t["arch"].items():
        if arches and arch not in arches:
            continue
        name = e["asset"]
        blob = None
        for d in [x for x in (src, cache) if x]:
            p = Path(d) / name
            if p.is_file():
                b = p.read_bytes()
                if len(b) == e["size"] and sha256(b) == e["sha256"]:
                    blob = b
                    log(f"{name}: taken from {d}")
                    break
                log(f"{name} in {d}: wrong size or SHA-256 – ignored")
        if blob is None:
            log(f"{name}: downloading {e['url']}")
            blob = _download(e["url"])
            if len(blob) != e["size"] or sha256(blob) != e["sha256"]:
                log(f"ERROR {name}: size {len(blob)} / SHA-256 {sha256(blob)} differ from the manifest – not kept")
                bad += 1
                continue
            Path(cache).mkdir(parents=True, exist_ok=True)
            (Path(cache) / name).write_bytes(blob)
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
            try:
                m = tar.getmember(e["member"])
            except KeyError:
                m = None
            if m is None or not m.isreg():
                log(f"ERROR {name}: no regular file {e['member']} – not kept")
                bad += 1
                continue
            binary = tar.extractfile(m).read()
        if len(binary) != e["binary_size"] or sha256(binary) != e["binary_sha256"]:
            log(f"ERROR {name}: the binary's size/SHA-256 differ from the manifest – not kept")
            bad += 1
            continue
        out = Path(bin_dir) / relpath(t["version"], arch)
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".part")
        tmp.write_bytes(binary)
        tmp.chmod(0o755)
        os.replace(tmp, out)
        log(f"ok {out.relative_to(Path(bin_dir).parent) if Path(bin_dir).parent in out.parents else out} "
            f"(M-Flasher {t['version']}, {arch}, sha256 {e['binary_sha256'][:16]}…)")
    return 1 if bad else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--arch", action="append", choices=["aarch64", "x86_64"])
    ap.add_argument("--from", dest="src", help="folder with the release tarballs (no download)")
    ap.add_argument("--cache", default=str(CACHE))
    ap.add_argument("--bin", default=str(BIN), help="target folder (default: tools-bin/ in the checkout)")
    ap.add_argument("--check", action="store_true", help="only verify tools-bin/")
    a = ap.parse_args(argv)
    if a.check:
        good, problems = verify_tree(Path(a.bin))
        for rel in good:
            print(f"ok {rel}")
        for p in problems:
            print(f"!! {p}")
        return 1 if problems else 0
    return fetch(a.arch, Path(a.src) if a.src else None, Path(a.cache), Path(a.bin))


if __name__ == "__main__":
    sys.exit(main())
