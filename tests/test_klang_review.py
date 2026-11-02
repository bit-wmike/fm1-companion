# SPDX-License-Identifier: GPL-3.0-only
"""Abnahme Klang: Regressionen ohne Qt (Kit-Dateinamen, Wellenform aus numpy, Umbenennen ohne Teilschaden,
FluidSynth-Stopp ohne Warten)."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fm1companion import presetlib as pl, samples as S, soundmodule as SM  # noqa: E402


def test_safe_kit_name_stays_in_folder():
    assert S.safe_kit_name("../../böse/name") == "böse_name"
    assert "/" not in S.safe_kit_name("a/b\\c")
    assert S.safe_kit_name("...") == ""
    assert S.safe_kit_name("Mein Kit (2)") == "Mein Kit (2)"


def test_kit_save_atomic_and_safe(tmp_path):
    k = S.Kit(name="A/B")
    p = k.save(tmp_path)
    assert p.parent == tmp_path and p.name == "A_B.fm1kit"
    assert json.loads(p.read_text("utf-8"))["name"] == "A/B"
    assert not list(tmp_path.glob(".*.tmp"))


def test_peaks_accepts_numpy():
    x = np.sin(np.linspace(0, 100, 50000))
    pk = S.peaks(x, 100)
    assert len(pk) == 100 and pk[0][0] <= pk[0][1]
    assert S.peaks(np.zeros(0)) == []


def test_library_rename_failure_keeps_name(tmp_path, monkeypatch):
    lib = pl.Library(tmp_path)
    p = pl.Preset(name="ALT")
    f, _ = lib.save(p, "Test", lib.hashes())

    def boom(_p, _f):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(pl.Library, "write", staticmethod(boom))
    with pytest.raises(OSError):
        lib.rename(p, "NEU")
    assert p.name == "ALT" and p.path == f and f.exists()


def test_fluidsynth_stop_nonblocking():
    class Pipe:
        def write(self, _s):
            pass

        def flush(self):
            pass

        def close(self):
            pass

    class Slow:
        stdin = Pipe()
        pid = 1

        def __init__(self):
            self.waited = False

        def poll(self):
            return None

        def wait(self, t=None):
            time.sleep(0.5)
            self.waited = True

        def terminate(self):
            pass

        def kill(self):
            pass
    fs = SM.FluidSynth()
    slow = Slow()
    fs.p = slow
    t0 = time.monotonic()
    fs.stop(block=False)
    assert time.monotonic() - t0 < 0.2 and fs.p is None and not fs.running
    for _ in range(40):
        if slow.waited:
            break
        time.sleep(0.05)
    assert slow.waited
