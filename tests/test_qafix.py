# SPDX-License-Identifier: GPL-3.0-only
"""QA fixes without UI: back up/restore a sample slot, read/write back the song chain,
engine names, read-only preset library.
  python3 -m pytest tests/test_qafix.py -q
"""
from __future__ import annotations

import errno
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fm1companion import midi_import as MI, presetlib as pl, samples as S  # noqa: E402
from fm1companion.demo_device import FakeFM1                                # noqa: E402
from fm1companion.transport import Link                                     # noqa: E402
from test_sloop_backup import LocalTransport, put_sample, sloop             # noqa: E402


def test_sample_slot_read_restore_sloop():
    _, link, _ = sloop()
    assert S.read_slot(link, 1, "sloop") == b""
    put_sample(link, 1, b"KICK")
    old = S.read_slot(link, 1, "sloop")
    assert len(old) > 512 and old[8:12] == b"KICK"
    put_sample(link, 1, b"SNARE")
    assert S.read_slot(link, 1, "sloop")[8:13] == b"SNARE"
    S.restore_slot(link, 1, old)
    assert S.read_slot(link, 1, "sloop") == old
    S.restore_slot(link, 1, b"")
    assert S.read_slot(link, 1, "sloop") == b""


def test_song_read_restore_melodee():
    link = Link(LocalTransport(FakeFM1()))
    assert MI.read_song(link).rows == []
    rows = [([1, 0, 0, 0], 2), ([2, 3, 0, 0], 1)]
    assert MI.write_song(link, rows) == 0
    snap = MI.read_song(link)
    assert snap.rows == rows
    MI.write_song(link, [([5, 5, 5, 5], 4)])
    assert MI.restore_song(link, snap) == 0
    assert MI.read_song(link).rows == rows


def test_engine_names_shared():
    assert pl.engine_display_name(12) == "FM6"
    assert pl.engine_display_name(12, ["A", "B"]) == "FM6"          # short INFO list: fixed names
    long = [f"E{i}" for i in range(16)]
    assert pl.engine_display_name(3, long) == "E3"
    assert pl.engine_display_name(99) == "Engine 99"
    assert pl.Caps(engines=long).engine_name(3) == pl.engine_display_name(3, long)


def test_library_on_readonly_root(tmp_path, monkeypatch):
    root = tmp_path / "ro" / "Presets"
    real = os.mkdir

    def deny(p, *a, **k):
        if str(p).startswith(str(tmp_path / "ro")):
            raise PermissionError(errno.EACCES, "Permission denied", str(p))
        return real(p, *a, **k)
    monkeypatch.setattr(os, "mkdir", deny)
    lib = pl.Library(root)                       # must not raise
    assert lib.all() == []
