#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Build the graphical installer: dist/FM-1-Companion-Suite-<version>.run (sh stub + tar.gz).

  python3 tools/build_run.py [--out DIR]
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_common import DIST, EPOCH, ROOT, app_files, tracked_files, version  # noqa: E402

sys.path.insert(0, str(ROOT))
from fm1companion import diag  # noqa: E402

EXTRA = ["LICENSE", "README.md", "install.sh", "uninstall.sh", "data", "packaging/installer", "packaging/udev",
         "packaging/fm1-companion.metainfo.xml", "packaging/fm1-companion.1"]


def payload() -> bytes:
    """tar.gz of the app – reproducible: tracked files only, sorted, fixed owner/time/modes, and no build time
    in the gzip header."""
    files = sorted(set(app_files()) | set(tracked_files(*EXTRA)), key=lambda f: f.relative_to(ROOT).as_posix())
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.GNU_FORMAT) as tar:
        for f in files:
            ti = tarfile.TarInfo("app/" + f.relative_to(ROOT).as_posix())
            ti.size = f.stat().st_size
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = "root"
            ti.mtime = EPOCH
            ti.mode = 0o755 if f.suffix in (".sh",) or f.name in ("install.sh", "uninstall.sh") else 0o644
            with open(f, "rb") as fh:
                tar.addfile(ti, fh)
    buf = io.BytesIO()
    with gzip.GzipFile(filename="", fileobj=buf, mode="wb", compresslevel=9, mtime=EPOCH) as gz:
        gz.write(raw.getvalue())
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
