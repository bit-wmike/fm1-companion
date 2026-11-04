# SPDX-License-Identifier: GPL-3.0-only
"""Sound: DX7 SysEx, renderer, generators, collections."""
from __future__ import annotations

import random
import sys
import time
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fm1companion import dx7, dx7_sources, fmrender  # noqa: E402

FIX = ROOT / "tests/fixtures/klang/fm1_bank1.syx"


def rnd_voice(rng):
    return [rng.randint(0, dx7.vmax(i)) if i < dx7.NAME else rng.randint(32, 126) for i in range(dx7.SIZE)]


def test_pack_unpack_many_random():
    rng = random.Random(1)
    for _ in range(500):
        v = rnd_voice(rng)
        assert dx7.unpack(dx7.pack(v)) == v


def test_single_and_bank_roundtrip():
    rng = random.Random(2)
    vs = [rnd_voice(rng) for _ in range(32)]
    b = dx7.bank_syx(vs, channel=3)
    assert len(b) == 4104 and b[:6] == bytes([0xF0, 0x43, 3, 9, 0x20, 0]) and b[-1] == 0xF7
    assert sum(b[6:4103]) % 128 == 0
    r = dx7.parse_syx(b)
    assert r.voices == vs and not r.warnings and r.bank
    s = dx7.single_syx(vs[0])
    assert len(s) == 163 and dx7.parse_syx(s).voices == [vs[0]]


def test_tolerant_parsing():
    v = dx7.init_voice()
    s = bytearray(dx7.single_syx(v))
    s[-2] ^= 1                                       # wrong checksum
    r = dx7.parse_syx(bytes(s) + b"\x00\x01" + bytes(dx7.single_syx(v))[:-1])   # garbage, then without F7
    assert len(r.voices) == 2 and any("Prüfsumme" in w for w in r.warnings)
    raw = bytes(x for _ in range(32) for x in dx7.pack(v))
    assert len(dx7.parse_syx(raw).voices) == 32
    assert dx7.parse_syx(b"\xf0\x41\x10\xf7").voices == []
    real = dx7.parse_syx(FIX.read_bytes())
    assert len(real.voices) == 32 and dx7.name(real.voices[0]) == "PIANO 1" and not real.warnings


def test_param_changes_and_plan():
    m = dx7.param_changes(dx7.init_voice(), channel=2)
    assert len(m) == 155 and m[150][:5] == bytes([0xF0, 0x43, 0x12, 1, 150 - 128])
    assert dx7.send_plan("", "FM-1_015") == "single" and dx7.send_plan("", "FM-1_089") == "params"
    assert dx7.send_plan("melodee") == "single"


def test_algorithms_graph():
    g = dx7.graph(0)
    assert g["carriers"] == [1, 3] and g["feedback"] == (6, 6)
    assert dx7.graph(31)["carriers"] == [1, 2, 3, 4, 5, 6]
    assert dx7.graph(3)["feedback"] == (4, 6)


def test_renderer_all_algorithms_and_speed():
    rng = random.Random(4)
    worst = 0.0
    for alg in range(32):
        v = dx7.random_voice(rng, alg=alg)
        v[dx7.VI["FB"]] = 7
        t = time.time()
        x = fmrender.render(v, seconds=2.0)
        worst = max(worst, time.time() - t)
        assert x.dtype == np.float32 and np.isfinite(x).all()
        assert 0.3 < np.abs(x).max() <= 0.9
    assert worst < 1.0, worst


def test_renderer_envelope_decays():
    v = dx7.init_voice()
    v[dx7.at(1, "R4")] = 40
    x = fmrender.render(v, seconds=3.0, hold=1.0)
    n = len(x)
    assert np.abs(x[: n // 3]).max() > 10 * np.abs(x[-n // 10:]).max()


def test_generators_constraints():
    rng = random.Random(5)
    for _ in range(50):
        v = dx7.random_voice(rng)
        assert dx7.sanitize(v) == v
        assert all(v[dx7.at(op, "OL")] >= 80 for op in dx7.carriers(v[dx7.VI["ALG"]]))
        m = dx7.mutate(v, 0.6, keep_alg=True, rng=rng)
        assert m[dx7.VI["ALG"]] == v[dx7.VI["ALG"]] and dx7.sanitize(m) == m
    a, b = dx7.random_voice(rng), dx7.random_voice(rng)
    assert dx7.morph(a, b, 0.0)[:dx7.NAME] == a[:dx7.NAME] and dx7.morph(a, b, 1.0)[:dx7.NAME] == b[:dx7.NAME]
    vs = dx7.variations(a, 8, seed=1)
    assert len(vs) == 8 and len({dx7.voice_key(x) for x in vs}) == 8


def test_sources_download_mocked(tmp_path):
    c = dx7_sources.by_key("fm1-factory")
    data = FIX.read_bytes()
    urls = []
    d = dx7_sources.download(c, tmp_path, fetch=lambda u: urls.append(u) or data)
    assert len(urls) == 4 and all(u.startswith("https://raw.githubusercontent.com/KingParamount/") for u in urls)
    assert dx7_sources.installed(tmp_path, c) and "CC0" in (d / "QUELLE.txt").read_text()
    with pytest.raises(RuntimeError):
        dx7_sources.download(dx7_sources.by_key("mene311"), tmp_path, fetch=lambda u: b"kaputt")
    assert all(x.licence for x in dx7_sources.COLLECTIONS)
    assert any(not x.clear and "unklar" in x.licence for x in dx7_sources.COLLECTIONS)
