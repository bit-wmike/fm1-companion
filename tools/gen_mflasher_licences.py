#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Generate the third-party licence file for a pinned M-Flasher version from its Cargo.lock (standard library only).

  python3 tools/gen_mflasher_licences.py CARGO_LOCK [--out FILE] [--cache DIR]
  python3 tools/gen_mflasher_licences.py CARGO_LOCK --check      exit 1 if the committed file differs

For every crate from crates.io named in M-Flasher's Cargo.lock it downloads the published crate
(https://static.crates.io/crates/<name>/<name>-<version>.crate), checks it against the SHA-256 that Cargo.lock
records, and writes: crate name, version, the licence it declares (Cargo.toml `license`), and the text of every
licence, copyright and notice file in it – including the font licences of crates that embed fonts (egui's
epaint_default_fonts: Ubuntu Font Licence, Hack / Bitstream Vera, the emoji icon font). A text that appears more than
once is printed in full once and referred to afterwards. The list is a superset: Cargo.lock also names crates that
only the Windows and macOS builds use.

Default output: fm1companion/tools/bundled/m-flasher/THIRD-PARTY-LICENSES.txt (shipped with the app and next to the
binary in the packages; shown by Recovery › Tools › Show licence). The release workflow runs --check.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "fm1companion" / "tools" / "manifest.json"
OUT = ROOT / "fm1companion" / "tools" / "bundled" / "m-flasher" / "THIRD-PARTY-LICENSES.txt"
CACHE = ROOT / "build" / "crates"
LICENCE_FILE = re.compile(r"(^|[-_.])(licen[cs]e|copying|copyright|notice|authors|ofl|ufl|unlicense)([-_.]|$)", re.I)
TEXT_SUFFIX = ("", ".txt", ".md", ".mit", ".apache", ".apache2", ".bsd", ".zlib", ".rst", ".markdown")


def lock_packages(lock_text: str) -> list[dict]:
    out = []
    for block in lock_text.split("[[package]]")[1:]:
        d = dict(re.findall(r'^(\w+) = "([^"]*)"', block, re.M))
        if d.get("source", "").startswith("registry+") and d.get("checksum"):
            out.append(d)
    return sorted(out, key=lambda d: (d["name"], d["version"]))


def fetch_crate(name: str, version: str, checksum: str, cache: Path) -> bytes:
    p = cache / f"{name}-{version}.crate"
    if p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest() == checksum:
        return p.read_bytes()
    url = f"https://static.crates.io/crates/{name}/{name}-{version}.crate"
    req = urllib.request.Request(url, headers={"User-Agent": "fm1-companion-licence-tool"})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = r.read(64 * 1024 * 1024)
    if hashlib.sha256(data).hexdigest() != checksum:
        raise SystemExit(f"{name} {version}: the download does not match Cargo.lock's checksum")
    cache.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return data


def crate_licences(data: bytes) -> tuple[str, str, list[tuple[str, str]]]:
    """(licence expression, authors, [(file path inside the crate, text)])"""
    expr, authors, files = "", "", []
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for m in sorted(tar.getmembers(), key=lambda m: m.name):
            if not m.isreg() or m.size > 512 * 1024:
                continue
            parts = m.name.split("/", 1)
            rel = parts[1] if len(parts) == 2 else m.name
            base = rel.rsplit("/", 1)[-1]
            if rel == "Cargo.toml":
                toml = tar.extractfile(m).read().decode("utf-8", "replace")
                pkg = toml.split("[package]", 1)[-1].split("\n[", 1)[0]
                mm = re.search(r'^license\s*=\s*"([^"]*)"', pkg, re.M)
                expr = mm.group(1) if mm else ""
                mm = re.search(r'^license-file\s*=\s*"([^"]*)"', pkg, re.M)
                if mm and not expr:
                    expr = f"see {mm.group(1)}"
                mm = re.search(r'^authors\s*=\s*\[(.*?)\]', pkg, re.M | re.S)
                if mm:
                    authors = ", ".join(re.findall(r'"([^"]*)"', mm.group(1)))
                continue
            if "/src/" in "/" + rel or rel.startswith(("tests/", "benches/", "examples/")):
                continue
            stem, dot, suf = base.partition(".")
            font_note = ("/fonts/" in "/" + rel or "/assets/" in "/" + rel) and base.lower().endswith(".txt")
            if font_note or (LICENCE_FILE.search(base) and ("." + suf.lower() if dot else "") in TEXT_SUFFIX):
                text = tar.extractfile(m).read().decode("utf-8", "replace").replace("\r\n", "\n").strip()
                if text:
                    files.append((rel, text))
    return expr, authors, files


def generate(lock: Path, cache: Path = CACHE, log=print) -> str:
    t = json.loads(MANIFEST.read_text("utf-8"))["tools"]["m-flasher"]
    pkgs = lock_packages(Path(lock).read_text("utf-8"))
    seen: dict[str, str] = {}
    out = [f"M-Flasher {t['version']} – licences of the third-party code compiled into the program",
           "=" * 78, "",
           f"M-Flasher (MIT, (c) {t['copyright']}, https://github.com/{t['repo']}) is built with Cargo. Its Cargo.lock",
           f"at tag {t['tag']} (commit {t['commit']}) names the {len(pkgs)} crates below; each was taken from crates.io",
           "and checked against the SHA-256 in Cargo.lock. For each crate: its declared licence and the full text of",
           "every licence, copyright and notice file it publishes. The list is a superset (it also names crates that",
           "only M-Flasher's Windows and macOS builds use). Fonts compiled into the program come with their crate",
           "(epaint_default_fonts: Ubuntu Light – Ubuntu Font Licence 1.0, Hack – MIT and Bitstream Vera licence, the",
           "emoji icon font and Noto Emoji – SIL OFL 1.1) or with M-Flasher itself (its OFL-*.txt files).",
           f"Generated by tools/gen_mflasher_licences.py.", ""]
    missing = []
    std: dict[str, str] = {}          # SPDX id -> where its standard text is printed in full
    for d in pkgs:
        expr, authors, files = crate_licences(fetch_crate(d["name"], d["version"], d["checksum"], cache))
        out += ["", "-" * 78, f"{d['name']} {d['version']}", f"Licence: {expr or 'not declared'}"]
        if authors:
            out.append(f"Authors: {authors}")
        if not files:
            missing.append(f"{d['name']} {d['version']}")
            ref = [f"{sid}: {std[sid]}" for sid in re.findall(r"[A-Za-z0-9.+-]+", expr) if sid in std]
            out.append("(the published crate contains no licence file. Copyright: its authors"
                       + (f" ({authors})" if authors else "") + "; licence as declared, standard text: "
                       + ("; ".join(ref) if ref else "see the texts above") + ")")
        for rel, text in files:
            h = hashlib.sha256(text.encode()).hexdigest()
            if h in seen:
                out.append(f"--- {rel}: the same text as {seen[h]} above")
                continue
            seen[h] = f"{d['name']} {d['version']} {rel}"
            out += [f"--- {rel}", "", text, ""]
            for sid, pat in (("Apache-2.0", "Apache License"), ("MIT", "Permission is hereby granted, free of charge"),
                             ("ISC", "Permission to use, copy, modify, and/or distribute"),
                             ("Zlib", "This software is provided 'as-is'"), ("BSL-1.0", "Boost Software License")):
                if pat in text and sid not in std:
                    std[sid] = seen[h]
    if missing:
        out += ["", "-" * 78, "Crates without a licence file of their own (declared licence applies, standard texts above):",
                *missing]
    log(f"{len(pkgs)} crates, {len(seen)} distinct texts, {len(missing)} without a licence file")
    return "\n".join(out).rstrip() + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("lock")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--cache", default=str(CACHE))
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    text = generate(Path(a.lock), Path(a.cache))
    out = Path(a.out)
    if a.check:
        ok = out.is_file() and out.read_text("utf-8") == text
        print(f"{out.name}: {'up to date' if ok else 'MISSING OR OUT OF DATE'}")
        return 0 if ok else 1
    out.write_text(text, "utf-8")
    print(f"{out}: {len(text) // 1024} KiB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
