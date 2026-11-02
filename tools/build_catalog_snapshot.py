#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Baut fm1companion/data/catalog_snapshot.json – den mitgelieferten Firmware-Katalog.

  python3 tools/build_catalog_snapshot.py                       # Quellen live von GitHub (raw)
  python3 tools/build_catalog_snapshot.py --mooring /tmp/awesome-fm-1/mooring/catalog.json \\
          --readme /tmp/awesome-fm-1/README.md [--screenshots] [--out DATEI]

--screenshots liest die README jedes Firmware-Repos über raw.githubusercontent.com (keine
GitHub-API, kein Limit) und merkt sich das erste echte Bild als Vorschaubild.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fm1companion import catalog  # noqa: E402


def read(src: str) -> bytes:
    if src.startswith("https://"):
        req = urllib.request.Request(src, headers={"User-Agent": catalog.UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read()
    return Path(src).read_bytes()


def readme_of(repo: str) -> str:
    for name in ("README.md", "readme.md", "Readme.md", "README.MD"):
        try:
            return read(f"https://raw.githubusercontent.com/{repo}/HEAD/{name}").decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            continue
    return ""


def build(mooring_src: str, readme_src: str, screenshots: bool = False, log=print) -> catalog.Catalog:
    moor = catalog.parse_mooring(json.loads(read(mooring_src)))
    cat = catalog.merge(read(readme_src).decode("utf-8"), moor)
    if screenshots:
        repos = sorted({e.repo for e in cat.entries if e.repo})

        def one(r):
            return r, catalog.first_image(readme_of(r), r)
        with ThreadPoolExecutor(6) as ex:
            shots = dict(ex.map(one, repos))
        for e in cat.entries:
            if e.repo and shots.get(e.repo) and "source" not in e.links:
                e.screenshot = shots[e.repo]
        log(f"Vorschaubilder: {sum(1 for e in cat.entries if e.screenshot)} von {len(cat.entries)}")
    cat.source = "snapshot"
    return cat


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mooring", default=catalog.MOORING_URL)
    ap.add_argument("--readme", default=catalog.README_URL)
    ap.add_argument("--screenshots", action="store_true")
    ap.add_argument("--out", default=str(catalog.SNAPSHOT_FILE))
    a = ap.parse_args(argv)
    cat = build(a.mooring, a.readme, a.screenshots)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(cat.to_dict(), ensure_ascii=False, indent=0, sort_keys=False) + "\n", "utf-8")
    print(f"{out}: {len(cat.entries)} Firmwares ({sum(e.installable for e in cat.entries)} direkt installierbar), "
          f"{len(cat.resources)} Werkzeuge & Links, Mooring-Revision {cat.revision}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
