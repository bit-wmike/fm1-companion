# SPDX-License-Identifier: GPL-3.0-only
"""Data layer, quality pass after 2.6.0: regression tests for the audit's findings (CD-01 … CD-18).

  python3 -m pytest tests/test_data_audit.py -q
"""
from __future__ import annotations

import base64
import json
import os
import sys
import types
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fm1companion import autobackup as AB, backup, backup_inspect as BI, config, devices   # noqa: E402
from fm1companion.demo_device import FakeFM1, rnd                                           # noqa: E402
from test_firmware_support import FeluccaFork, cmds, link_for, sloop_dev                    # noqa: E402


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "_settings", config.Settings(user_root=str(tmp_path / "FM-1")))
    monkeypatch.setattr(AB, "_cache", None)
    return tmp_path


def obj(i: int, data: bytes) -> dict:
    return {"id": i, "size": len(data), "crc": zlib.crc32(data), "data": base64.b64encode(data).decode()}


def felucca_file(created="2026-01-01T00:00:00Z", kind="auto", firmware="MELODEE v1.6", key="melodee", **extra) -> dict:
    return {"format": "felucca-backup", "version": 1, "firmware": firmware, "created": created,
            "objects": [obj(0, b"a"), obj(1, b"b")], "companion": {"kind": kind, "firmware": key, **extra}}


# ---------------------------------------------------------------- CD-01: merging two profiles loses nothing ---

def test_move_profile_folder_never_overwrites_and_keeps_everything(tmp_path):
    a, b = tmp_path / "Old name", tmp_path / "Studio FM-1"
    (a / "sub").mkdir(parents=True)
    b.mkdir()
    (a / "felucca-backup.json").write_text("A")
    (b / "felucca-backup.json").write_text("B")
    (b / "felucca-backup_Old name.json").write_text("B2")          # (even the collision name is taken)
    (a / "my-x0x-project.x0b").write_bytes(b"X0XB")
    (a / "sub" / "old.json").write_text("{}")
    devices.move_profile_folder(tmp_path, "Old name", "Studio FM-1")
    assert not a.exists()
    got = {str(p.relative_to(b)): p.read_bytes() for p in b.rglob("*") if p.is_file()}
    assert got == {"felucca-backup.json": b"B", "felucca-backup_Old name.json": b"B2",
                   "felucca-backup_Old name (2).json": b"A", "my-x0x-project.x0b": b"X0XB", "sub/old.json": b"{}"}


def test_merge_of_two_profiles_keeps_every_file(isolated, monkeypatch):
    from fm1companion.ui.pages import devices as DP
    store = devices.ProfileStore(isolated / "devices.json")
    a, b = store.create("Old name"), store.create("Studio FM-1")
    root = config.load().backups_dir
    fa, fb = root / a.folder_name, root / b.folder_name
    (fa / "sub").mkdir(parents=True)
    fb.mkdir(parents=True)
    (fa / "felucca-backup.json").write_text('{"which": "A"}')      # an imported web-editor file keeps its name
    (fb / "felucca-backup.json").write_text('{"which": "B"}')
    (fa / "my-x0x-project.x0b").write_bytes(b"X0XB....")
    (fa / "sub" / "old.json").write_text("{}")
    (fa / "unique.json").write_text("{}")
    ctx = types.SimpleNamespace(store=store, profile=None, changed=types.SimpleNamespace(emit=lambda: None))
    page = types.SimpleNamespace(ctx=ctx, refresh=lambda: None)
    assert DP.DevicesPage.merge(page, a.id, b.id) is True
    assert a.id not in store.profiles and not fa.exists()
    got = {str(p.relative_to(fb)): p.read_text() for p in fb.rglob("*") if p.is_file()}
    assert got == {"felucca-backup.json": '{"which": "B"}', "felucca-backup_Old name.json": '{"which": "A"}',
                   "my-x0x-project.x0b": "X0XB....", "sub/old.json": "{}", "unique.json": "{}"}

    # a file that cannot be moved: nothing is deleted, both profiles stay
    c = store.create("Third")
    fc = root / c.folder_name
    fc.mkdir()
    (fc / "keep.json").write_text("{}")
    (fc / "felucca-backup.json").write_text("C")
    errors = []
    monkeypatch.setattr(DP, "show_error", lambda *a_, **k: errors.append(a_))

    def fail(*_a, **_k):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr("shutil.move", fail)
    assert DP.DevicesPage.merge(page, c.id, b.id) is False
    assert errors and c.id in store.profiles
    assert sorted(p.name for p in fc.iterdir()) == ["felucca-backup.json", "keep.json"]
