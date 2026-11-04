#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Generate source tarballs for the bundled WebAssembly builds (GPL-3.0 §6).

Reads the commits from fm1companion/sims/**/manifest.json and packs exactly these revisions from local git clones
(``git archive``) into dist/sources/. The files belong as attachments on every GitHub release that contains
the builds (see the simulators' NOTICE).

  python3 tools/make_source_tarballs.py --repo melodee=/path/to/melodee --repo fm1_open=/path/to/fm1_open
                                        [--out dist/sources]

Without --repo, /tmp/<name> is searched. If a clone or a commit is missing, the script reports it and
exits with code 1 (the remaining tarballs are still written).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIMS = ROOT / "fm1companion" / "sims"


def wanted() -> dict[tuple[str, str], list[str]]:
    """(project name, commit) -> list of builds that use it"""
    out: dict[tuple[str, str], list[str]] = {}
    for m in sorted(SIMS.rglob("manifest.json")):
        d = json.loads(m.read_text("utf-8"))
        repo, commit = d.get("source_repo", ""), d.get("commit", "")
        if repo and commit:
            out.setdefault((repo.rstrip("/").rsplit("/", 1)[-1], commit), []).append(
                str(m.parent.relative_to(SIMS)))
    return out


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", action="append", default=[], help="name=PATH (e.g. melodee=/tmp/melodee)")
    ap.add_argument("--out", default=str(ROOT / "dist" / "sources"))
    a = ap.parse_args(argv)
    repos = {}
    for r in a.repo:
        k, _, v = r.partition("=")
        repos[k] = Path(v).expanduser()
    out = Path(a.out).resolve()          # git -C changes the directory: -o needs an absolute path
    out.mkdir(parents=True, exist_ok=True)
    ok, sums = True, []
    for (name, commit), users in wanted().items():
        clone = repos.get(name, Path("/tmp") / name)
        dest = out / f"{name}-{commit}.tar.gz"
        if not (clone / ".git").exists() and not (clone / "HEAD").exists():
            print(f"MISSING: clone of {name} ({clone}) – for {', '.join(users)}", file=sys.stderr)
            ok = False
            continue
        r = subprocess.run(["git", "-C", str(clone), "cat-file", "-e", f"{commit}^{{commit}}"], capture_output=True)
        if r.returncode:
            print(f"MISSING: commit {commit} in {clone} – run 'git fetch' first (for {', '.join(users)})", file=sys.stderr)
            ok = False
            continue
        subprocess.run(["git", "-C", str(clone), "archive", "--format=tar.gz", f"--prefix={name}-{commit}/",
                        "-o", str(dest), commit], check=True)
        sums.append(f"{sha256(dest)}  {dest.name}")
        print(f"{dest.name}  {dest.stat().st_size} bytes  (for {', '.join(users)})")
    if sums:
        (out / "SHA256SUMS").write_text("\n".join(sums) + "\n", "utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
