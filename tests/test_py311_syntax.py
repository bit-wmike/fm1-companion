# SPDX-License-Identifier: GPL-3.0-only
"""Raspberry Pi OS Bookworm runs Python 3.11: every source file must compile there. (Python 3.12 allows a backslash
inside an f-string's {…} – PEP 701 – 3.11 does not; one such line made a whole page module fail to import.)"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PY311 = shutil.which("python3.11")

SCRIPT = r"""
import pathlib, sys
bad = []
for p in sorted(pathlib.Path(sys.argv[1]).rglob("*.py")):
    if "vendor" in p.parts or "__pycache__" in p.parts:
        continue
    try:
        compile(p.read_text("utf-8"), str(p), "exec")
    except SyntaxError as e:
        bad.append(f"{p}:{e.lineno}: {e.msg}")
print("\n".join(bad))
sys.exit(1 if bad else 0)
"""


@pytest.mark.skipif(not PY311, reason="python3.11 not installed")
@pytest.mark.parametrize("folder", ["fm1companion", "tests", "tools"])
def test_every_file_compiles_on_python_311(folder):
    r = subprocess.run([PY311, "-I", "-c", SCRIPT, str(ROOT / folder)], capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
