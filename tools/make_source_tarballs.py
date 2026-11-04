#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Quell-Tarballs für die mitgelieferten WebAssembly-Builds (GPL-3.0 §6) erzeugen.

Liest die Commits aus fm1companion/sims/**/manifest.json und packt aus lokalen Git-Klonen genau diese Stände
(``git archive``) nach dist/sources/. Die Dateien gehören als Anhang an jedes GitHub-Release, das die Builds
enthält (siehe NOTICE der Simulatoren).

  python3 tools/make_source_tarballs.py --repo melodee=/pfad/zu/melodee --repo fm1_open=/pfad/zu/fm1_open
                                        [--out dist/sources]

Ohne --repo wird unter /tmp/<name> gesucht. Fehlt ein Klon oder ein Commit, meldet das Skript es und
endet mit Code 1 (die übrigen Tarballs werden trotzdem geschrieben).
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
    """(Projektname, Commit) -> Liste der Builds, die ihn nutzen"""
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
    ap.add_argument("--repo", action="append", default=[], help="name=PFAD (z. B. melodee=/tmp/melodee)")
    ap.add_argument("--out", default=str(ROOT / "dist" / "sources"))
    a = ap.parse_args(argv)
    repos = {}
    for r in a.repo:
        k, _, v = r.partition("=")
        repos[k] = Path(v).expanduser()
    out = Path(a.out).resolve()          # git -C wechselt das Verzeichnis: -o braucht einen absoluten Pfad
    out.mkdir(parents=True, exist_ok=True)
    ok, sums = True, []
    for (name, commit), users in wanted().items():
        clone = repos.get(name, Path("/tmp") / name)
        dest = out / f"{name}-{commit}.tar.gz"
        if not (clone / ".git").exists() and not (clone / "HEAD").exists():
            print(f"FEHLT: Klon von {name} ({clone}) – für {', '.join(users)}", file=sys.stderr)
            ok = False
            continue
        r = subprocess.run(["git", "-C", str(clone), "cat-file", "-e", f"{commit}^{{commit}}"], capture_output=True)
        if r.returncode:
            print(f"FEHLT: Commit {commit} in {clone} – erst 'git fetch' (für {', '.join(users)})", file=sys.stderr)
            ok = False
            continue
        subprocess.run(["git", "-C", str(clone), "archive", "--format=tar.gz", f"--prefix={name}-{commit}/",
                        "-o", str(dest), commit], check=True)
        sums.append(f"{sha256(dest)}  {dest.name}")
        print(f"{dest.name}  {dest.stat().st_size} Bytes  (für {', '.join(users)})")
    if sums:
        (out / "SHA256SUMS").write_text("\n".join(sums) + "\n", "utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
