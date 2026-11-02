# SPDX-License-Identifier: GPL-3.0-only
"""klang: Preset-Bibliothek (Befehl 80) – byte-genau wie der Melodee-Editor, gegen FakeFM1 und den Editor-Mock."""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fm1companion import dx7, presetlib as pl  # noqa: E402
from fm1companion.demo_device import FakeFM1  # noqa: E402
from fm1companion.protocol import frame, parse_info, unframe  # noqa: E402

EDITOR = Path("/tmp/melodee/web/editor.html")


class FakeLink:
    def __init__(self, dev=None):
        self.dev = dev or FakeFM1()
        self.sent = []

    def request(self, cmd, args=(), timeout=1.5, retries=1):
        self.sent.append((cmd, list(args)))
        r = self.dev.handle(frame(cmd, args))
        if r is None:
            raise TimeoutError("keine Antwort")
        return unframe(r)[1]


@pytest.fixture
def bank():
    link = FakeLink()
    caps = pl.probe(link, parse_info(link.request(1, [])))
    return pl.SharedBank(link, caps)


def test_v14_offset_encoding():
    assert pl.v14enc(0) == [0, 64] and pl.v14enc(-64) == [64, 63] and pl.v14enc(127) == [127, 64]
    for v in range(-8192, 8192, 37):
        assert pl.v14dec(*pl.v14enc(v)) == v


def test_pack7_like_editor():
    raw = bytes(range(250, 256)) + bytes(range(0, 200))
    assert pl.unpack7(pl.pack7(raw)) == list(raw)
    assert pl.pack7([0x80, 1]) == [1, 0, 1]


def test_request_bytes():
    assert pl.SharedBank.request(0, 300, 16) == [0, 300 & 127, 2, 16]
    with pytest.raises(pl.PresetError):
        pl.SharedBank.request(1, 384)


def test_put_args_byte_exact(bank):
    p = pl.Preset(name="Bass x", engine=3, params=list(range(-10, 81)))
    a = bank.put_args(p)
    exp = [3, *b"Bass x", 0] + [b for v in range(-10, 81) for b in pl.v14enc(v)] + [0] * 32 + [0]
    assert a == exp
    v = dx7.init_voice()
    f = pl.preset_from_voice(v, p_count=91)
    a = bank.put_args(f)
    raw = bytes(dx7.pack(v))
    assert a[0] == 12 and a[1:12] == [*b"INIT VOICE", 0]
    end = 12 + 2 * 91 + 32
    assert a[end] == 1 | 64 | 32                 # Art 1, Standard-Bedienwerte, nativer Name bleibt
    assert a[end + 1:end + 3] == [128 & 127, 1] and a[end + 3:] == pl.pack7(raw)
    with pytest.raises(pl.PresetError):
        bank.put_args(pl.Preset(name="x", engine=12, params=[0] * 91))   # FM6 ohne Voice


def test_list_get_and_probe(bank):
    assert bank.caps.shared and bank.caps.total == 384 and bank.caps.favorites
    slots = bank.list_all()
    assert len(slots) == 384 and sum(s["used"] for s in slots) == 26 and sum(s["favorite"] for s in slots) == 2
    p = bank.get(0)
    assert p.used and p.engine == 12 and p.native_kind == 1 and len(p.native) == 128 and len(p.params) == 91
    assert bank.get(30).used is False


def test_write_with_undo_and_ops(bank):
    src = bank.get(1)
    old = bank.write_with_undo(30, src)
    assert not old.used and bank.get(30).same_sound(src)
    bank.restore(30, old)
    assert not bank.get(30).used
    old = bank.write_with_undo(2, src)
    assert old.used
    bank.restore(2, old)
    assert bank.get(2).same_sound(old) and bank.get(2).name == old.name
    bank.rename(5, "NEUER NAME")
    assert bank.get(5).name == "NEUER NAME"
    bank.erase(5)
    assert not bank.get(5).used
    bank.store(5, 0, "VON SPUR")
    assert bank.get(5).name == "VON SPUR"
    bank.load(0, 1)
    with pytest.raises(pl.PresetError):
        bank.load(99, 0)                          # leerer Platz: rc 1
    assert bank.set_favorite(0, True) == 0
    bank.link.dev.playing = True
    assert bank.set_favorite(1, True) == 4        # während der Wiedergabe: nach STOP


def test_file_roundtrip_and_dedupe(tmp_path, bank):
    lib = pl.Library(tmp_path)
    p = bank.get(0)
    p.tags = ["Bass"]
    f, new = lib.save(p, "DEMO")
    assert new and f.name.startswith("U001 ")
    _, new2 = lib.save(bank.get(0), "DEMO")
    assert not new2
    q = pl.Preset.from_json(json.loads(f.read_text()))
    assert q.same_sound(p) and q.tags == ["Bass"] and q.native == p.native
    z = tmp_path / "set.zip"
    pl.export_zip([p, bank.get(1)], z)
    assert len(pl.read_any(z.read_bytes(), "set.zip")) == 2
    fel = pl.to_felucca([p])
    back = pl.from_felucca(json.loads(json.dumps(fel)))
    assert back[0].native == p.native and back[0].params == p.params
    syx = dx7.bank_syx([dx7.init_voice()] * 3)
    assert len(pl.read_any(syx, "x.syx")) == 32


def test_map_params():
    caps = pl.Caps(p_count=10, p_e0=2)
    p = pl.Preset(params=[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12])   # 12 Werte, letzte 8 = Engine
    assert pl.map_params(p, caps) == [1, 2, 5, 6, 7, 8, 9, 10, 11, 12]


def test_legacy_refusal():
    c = pl.Caps(legacy=True)
    assert "1.5" in pl.refusal_text(c)


@pytest.mark.skipif(not (EDITOR.exists() and shutil.which("node")), reason="Editor-Mock nicht verfügbar")
def test_against_editor_mock():
    """der Referenz-Mock des Melodee-Editors (makeMockDevice mit shared)."""
    from fm1companion.transport import Link, SubprocessTransport
    link = Link(SubprocessTransport(["node", str(ROOT / "tests/mock_fm1_shared.mjs"), str(EDITOR)]))
    try:
        info = None
        for _ in range(20):
            try:
                info = parse_info(link.request(1, [], timeout=1.0, retries=0))
                break
            except Exception:                      # noqa: BLE001
                time.sleep(0.2)
        assert info
        caps = pl.probe(link, info)
        assert caps.shared and caps.total == 384 and caps.p_count == info.p_count
        b = pl.SharedBank(link, caps)
        assert len(b.list_all()) == 384
        v = dx7.set_name(dx7.random_voice(), "MOCKTEST")
        p = pl.preset_from_voice(v, p_count=caps.p_count)
        old = b.write_with_undo(7, p)
        assert not old.used
        got = b.get(7)
        assert got.used and got.native_kind == 1 and got.native == p.native and got.name.startswith("MOCKTEST")
        gen = pl.Preset(name="SIDTEST", engine=3, params=[5] * caps.p_count)
        b.put(8, gen)
        assert b.get(8).params == [5] * caps.p_count
        b.rename(8, "UMBENANNT")
        assert b.get(8).name == "UMBENANNT"
        b.load(8, 0)
        b.store(9, 0, "GESPEICHERT")
        assert b.get(9).used
        b.erase(8)
        assert not b.get(8).used
        b.restore(7, old)
        assert not b.get(7).used
    finally:
        link.close()
