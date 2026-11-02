#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Grafischen Installer bauen: dist/FM-1-Companion-Suite-<version>.run (sh-Stub + tar.gz).

  python3 tools/build_run.py [--out DIR]
"""
from __future__ import annotations

import argparse
import hashlib
import io
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_common import DIST, ROOT, app_files, version  # noqa: E402

sys.path.insert(0, str(ROOT))
from fm1companion import diag  # noqa: E402

EXTRA = ["LICENSE", "README.md", "install.sh", "uninstall.sh", "data", "packaging/installer",
         "packaging/fm1-companion.metainfo.xml", "packaging/fm1-companion.1"]


def payload() -> bytes:
    buf = io.BytesIO()
    files = list(app_files())
    for e in EXTRA:
        p = ROOT / e
        files += [x for x in p.rglob("*") if x.is_file()] if p.is_dir() else ([p] if p.exists() else [])
    with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=9) as tar:
        for f in sorted(set(files)):
            if "__pycache__" in f.parts or f.suffix == ".pyc":
                continue
            ti = tar.gettarinfo(str(f), arcname="app/" + f.relative_to(ROOT).as_posix())
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = "root"
            ti.mtime = 1793350800
            ti.mode = 0o755 if f.suffix in (".sh",) or f.name in ("install.sh", "uninstall.sh") else 0o644
            with open(f, "rb") as fh:
                tar.addfile(ti, fh)
    return buf.getvalue()


def build(out: Path = DIST) -> Path:
    ver = version()
    data = payload()
    need = " ".join(p for p in diag.packages("required") if p != "python3")
    stub = (ROOT / "packaging/run-stub.sh").read_text().replace("@VERSION@", ver).replace(
        "@SHA256@", hashlib.sha256(data).hexdigest()).replace("@NEED@", need)
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"FM-1-Companion-Suite-{ver}.run"
    target.write_bytes(stub.encode() + data)
    target.chmod(0o755)
    return target


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DIST))
    print(build(Path(ap.parse_args().out)))
