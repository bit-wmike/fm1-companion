#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Bundle SLOOP 3's web editor (isod89/sloop3-fm1, GPL-3.0) for the Editor page: fm1companion/sims/_editors/sloop3/.

The editor is one file, web/editor.html, built upstream by tools/mkeditor.py from web/src/. This script takes the
file from a tag of a local clone, unchanged, checks that the upstream build script gives exactly that file from the
tag's sources, and writes LICENSE, NOTICE and manifest.json (commit, checksums, the firmware versions whose tag
carries the very same editor). The app's own addition, compat-chromium102.js, stays as it is and is listed too.

    python3 tools/bundle_sloop3_editor.py --repo /path/to/sloop3-fm1 --tag v3.3.7-beta [--also v3.3.6-beta]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "fm1companion" / "sims" / "_editors" / "sloop3"
REPO_URL = "https://github.com/isod89/sloop3-fm1"
COMPAT = "compat-chromium102.js"


def git(repo: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True).stdout


def firmware_version(repo: Path, tag: str) -> str:
    m = re.search(rb'#define VERSION "([^"]+)"', git(repo, "show", f"{tag}:fw/main.c"))
    if not m:
        raise SystemExit(f"{tag}: no VERSION in fw/main.c")
    return m.group(1).decode()


def rebuilt(repo: Path, tag: str) -> bytes:
    """web/editor.html as the tag's own tools/mkeditor.py builds it from the tag's web/src (in a temporary folder)"""
    with tempfile.TemporaryDirectory() as tmp:
        tar = git(repo, "archive", "--format=tar", tag, "tools/mkeditor.py", "web/src")
        subprocess.run(["tar", "-x", "-C", tmp], input=tar, check=True)
        out = Path(tmp) / "editor.html"
        subprocess.run([sys.executable, "-I", str(Path(tmp) / "tools" / "mkeditor.py"), str(out)], check=True,
                       capture_output=True)
        return out.read_bytes()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--also", action="append", default=[], help="another tag with the very same editor")
    ap.add_argument("--name", default="sloop3", help="folder under sims/_editors (one per editor file: \"sloop3\" = 3.3.x, \"sloop3-3.4.0\" …)")
    a = ap.parse_args(argv)
    repo = Path(a.repo)
    out_dir = OUT.parent / a.name
    commit = git(repo, "rev-parse", f"{a.tag}^{{commit}}").decode().strip()
    date = git(repo, "log", "-1", "--format=%cI", a.tag).decode().strip()
    editor = git(repo, "show", f"{a.tag}:web/editor.html")
    if rebuilt(repo, a.tag) != editor:
        raise SystemExit(f"{a.tag}: web/editor.html is not what tools/mkeditor.py builds from web/src")
    versions = [firmware_version(repo, a.tag)]
    for t in a.also:
        if git(repo, "show", f"{t}:web/editor.html") != editor:
            raise SystemExit(f"{t}: another editor – bundle it separately")
        versions.append(firmware_version(repo, t))
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "editor.html").write_bytes(editor)
    (out_dir / "LICENSE").write_bytes(git(repo, "show", f"{a.tag}:LICENSE"))
    (out_dir / "NOTICE").write_text(
        f"SLOOP 3 web editor (web/editor.html), from {REPO_URL} tag {a.tag} (commit {commit}).\n"
        "GPL-3.0 (LICENSE beside this file). SLOOP 3 is by isod89 (the SLOOP project); its README says: new code,\n"
        "written for this project; the values of its factory sounds, kits and presets are SLOOP 2.8.5's\n"
        "(https://github.com/isod89/sloop-fm1, GPL-3.0), whose earlier versions were based on Felucca by Leo Kuroshita\n"
        "(Hügelton Instruments).\n"
        "editor.html is the upstream file, byte for byte: FM-1 Companion's tools/bundle_sloop3_editor.py checks that\n"
        "the tag's own tools/mkeditor.py builds exactly this file from the tag's web/src. Nothing in it is changed.\n"
        f"{COMPAT} is NOT part of SLOOP 3: it is FM-1 Companion's own small addition (GPL-3.0-only), run by the\n"
        "app after the editor has loaded, for the older Chromium of Qt 6.4 (see the file).\n"
        "The firmware itself, m-bootloader and the installer page are not bundled.\n\n"
        "Corresponding Source (GNU GPL 3.0, section 6)\n"
        f"The complete corresponding source of this file is {REPO_URL} at commit {commit}\n"
        "(web/src/ and tools/mkeditor.py). A tarball of exactly this commit, made with tools/make_source_tarballs.py,\n"
        "is attached to every FM-1 Companion release that ships this file:\n"
        "https://github.com/bit-wmike/fm1-companion/releases\n"
        "Written offer: for at least three years after the last release that ships this file, the maintainers of\n"
        "FM-1 Companion will give anyone a copy of that corresponding source on request\n"
        "(https://github.com/bit-wmike/fm1-companion/issues), free of charge apart from the cost of physically\n"
        "performing the distribution.\n", "utf-8")
    if not (out_dir / COMPAT).is_file():              # (the app's own file: the same for every editor so far)
        (out_dir / COMPAT).write_bytes((OUT / COMPAT).read_bytes())
    files = {}
    for name in ("LICENSE", "NOTICE", COMPAT, "editor.html"):
        raw = (out_dir / name).read_bytes()
        files[name] = {"sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}
    manifest = {"schema": 1, "name": "sloop3-editor", "title": "SLOOP 3 web editor", "kind": "editor",
                "firmware": "sloop3", "firmware_versions": sorted(versions), "version": versions[0],
                "source_repo": REPO_URL, "ref": a.tag, "commit": commit, "license": "GPL-3.0",
                "build_date": date, "builder": "upstream tools/mkeditor.py (unchanged file); "
                                               "tools/bundle_sloop3_editor.py",
                "entry": "editor.html", "midi": "webmidi", "compat": COMPAT, "files": files}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", "utf-8")
    print(f"{out_dir}: SLOOP 3 editor of {', '.join(sorted(versions))} ({commit[:7]}), {len(editor)} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
