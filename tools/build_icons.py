#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Lucide-Symbole für die App übernehmen und das App-Symbol in allen hicolor-Größen rendern.

  npm pack lucide-static@1.57.0 && tar xzf lucide-static-1.57.0.tgz
  python3 tools/build_icons.py path/to/package

Kopiert nur die benutzten Symbole (siehe fm1companion/icons/names.py) verkleinert nach
fm1companion/icons/lucide/ und rendert data/icons/hicolor/<n>x<n>/apps/fm1-companion.png.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def minify(svg: str) -> str:
    svg = re.sub(r"<!--.*?-->", "", svg, flags=re.S)
    svg = re.sub(r'\s*class="[^"]*"', "", svg)
    svg = re.sub(r"\s+", " ", svg).replace("> <", "><").strip()
    return svg


def copy_lucide(pkg: Path):
    from fm1companion.icons.names import EXTRA, MAP
    names = sorted({v.split(":")[0] for v in MAP.values()} | set(EXTRA))
    out = ROOT / "fm1companion" / "icons" / "lucide"
    out.mkdir(parents=True, exist_ok=True)
    missing = []
    for n in names:
        src = pkg / "icons" / f"{n}.svg"
        if not src.exists():
            missing.append(n)
            continue
        (out / f"{n}.svg").write_text(minify(src.read_text()) + "\n")
    (ROOT / "fm1companion" / "icons" / "LICENSE.lucide").write_text((pkg / "LICENSE").read_text())
    print(f"{len(names) - len(missing)} Symbole kopiert; fehlen: {missing}")
    return missing


def render_app_icon():
    from PyQt6.QtCore import QRectF
    from PyQt6.QtGui import QGuiApplication, QImage, QPainter
    from PyQt6.QtSvg import QSvgRenderer
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QGuiApplication.instance() or QGuiApplication(["x"])  # noqa: F841
    svg = ROOT / "data" / "fm1-companion.svg"
    r = QSvgRenderer(str(svg))
    for n in (16, 22, 24, 32, 48, 64, 128, 256, 512):
        img = QImage(n, n, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(0)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r.render(p, QRectF(0, 0, n, n))
        p.end()
        d = ROOT / "data" / "icons" / "hicolor" / f"{n}x{n}" / "apps"
        d.mkdir(parents=True, exist_ok=True)
        img.save(str(d / "fm1-companion.png"))
    print("App-Symbol gerendert")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        copy_lucide(Path(sys.argv[1]))
    render_app_icon()
