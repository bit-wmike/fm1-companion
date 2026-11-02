# SPDX-License-Identifier: GPL-3.0-only
"""Flash-Modell (fm1companion/flash/flashmodel.py): Pakete, Layouts, Installationsketten, Dump-Prüfung.

Teile mit echten Paketen brauchen FM1_TEST_PKGDIR (zip_v15.fwsc, v16.fwsc, melodee.fwsc, sloop.fwsc, skiff.fwsc,
jiant.fwsc) – ohne diese Variable werden sie mit klarer Meldung übersprungen. Die Pakete gehören nicht ins Repo.
"""
import hashlib
import json
import os
import struct
import sys
import zlib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fm1companion.flash import flashmodel as m  # noqa: E402

PKGDIR = os.environ.get("FM1_TEST_PKGDIR", "")
LABELS = {"v15": "zip_v15.fwsc", "v16": "v16.fwsc", "melodee_1.5.1": "melodee.fwsc",
          "sloop_2.8.3": "sloop.fwsc", "skiff_2.0.3": "skiff.fwsc", "jiant_0.9.9": "jiant.fwsc"}
need_pkgs = pytest.mark.skipif(not PKGDIR or not all((Path(PKGDIR) / f).is_file() for f in LABELS.values()),
                               reason="echte FM-1-Pakete fehlen: FM1_TEST_PKGDIR auf einen Ordner mit zip_v15.fwsc, "
                                      "v16.fwsc, melodee.fwsc, sloop.fwsc, skiff.fwsc, jiant.fwsc setzen")


@pytest.fixture(scope="module")
def pk():
    return {k: m.Package.load(Path(PKGDIR) / v) for k, v in LABELS.items()}


# ------------------------------------------------------------------ ohne Pakete
def test_known_packages_file_is_consistent():
    refs = m.load_known()
    ids = {r.identity for r in refs}
    assert {"FM-1_015", "FM-1_016", "FM-1_91051", "FM-1_91060", "FM-1_900", "FM-1_720", "FM-1_909"} <= ids
    v15 = m.ref_for("FM-1_015", refs=refs)
    v16 = m.ref_for("FM-1_016", refs=refs)
    rc7 = next(r for r in refs if r.version == "Melodee 1.6.0-rc.7")
    assert v15.file_sha256.startswith("db1642b2") and v15.flash_bytes == 0x93000 and v15.head_sha256 == m.FACTORY_HEAD_SHA256
    assert v16.file_sha256.startswith("d017fb65") and v16.flash_bytes == 0xA9000 and v16.family == "v16-layout"
    assert len(v16.sectors) == 22 and min(v16.sectors) == 0x93000 and m.V15_END in v16.app_prefix
    assert rc7.file_sha256.startswith("607f746a") and rc7.resources["crc"] == 0x2035D335
    assert all(r.family == "v15-layout" for r in refs if r.identity != "FM-1_016")
    # die App-Prüfsummen müssen zu den Werten der Original-Firmware-Spezifikation passen
    from fm1companion import firmware
    assert firmware.spec("stock-v15").sha256 == v15.file_sha256 and firmware.spec("stock-v16").sha256 == v16.file_sha256


def test_name_version_and_cleanup_rules():
    assert m.name_version("FM-1_v15.fwsc") == 15 and m.name_version("FM-1 V16.fwsc") == 16
    assert m.name_version("FM-1.fwsc") is None and m.name_version("melodee-1.6.0-rc.7.fwsc") is None
    assert m.cleanup_plan([{"sector": 0x93000}, {"sector": 0x94000}, {"sector": 0xA0000}]) == \
        [(0x93000, 0x2000), (0xA0000, 0x1000)]
    with pytest.raises(AssertionError):
        m.cleanup_plan([{"sector": 0x3000}])
    with pytest.raises(AssertionError):
        m.cleanup_plan([{"sector": 0xFF000}])


def test_resource_status_and_header():
    # Antwort auf Befehl 82: rc, installed, Länge (5×7 Bit), CRC (5×7 Bit)
    def r35(n):
        return [(n >> 7 * i) & 127 for i in range(5)]
    st = m.parse_resource_status([0, 1, *r35(53844), *r35(0x2035D335)])
    assert st == {"rc": 0, "installed": True, "length": 53844, "crc": 0x2035D335}
    with pytest.raises(ValueError):
        m.parse_resource_status([0, 1])
    dump = bytearray(b"\xff" * m.FLASH_SIZE)
    payload = bytes(range(256)) * 10
    hdr = struct.pack("<3I16x", m.RES_MAGIC, len(payload), zlib.crc32(payload))
    hdr += struct.pack("<I", zlib.crc32(hdr))
    dump[m.RES_BASE:m.RES_BASE + 32] = hdr
    dump[m.RES_BASE + 32:m.RES_BASE + 32 + len(payload)] = payload
    assert m.resource_header(bytes(dump))["payload_ok"]
    dump[m.RES_BASE + 100] ^= 1
    assert not m.resource_header(bytes(dump))["payload_ok"]
    assert m.resource_header(b"\xff" * m.FLASH_SIZE) == {"present": False}


def test_quick_check_sentences():
    refs = m.load_known()
    rc7 = next(r for r in refs if r.version == "Melodee 1.6.0-rc.7")
    ok = m.quick_check("FM-1_91060", "Melodee 1.6.0-rc.7",
                       {"installed": True, "length": rc7.resources["length"], "crc": rc7.resources["crc"]}, refs)
    assert ok.level == m.GREEN and any("genau diesem Build" in t for _, t in ok.findings)
    assert any("Reste" in t for t in ok.not_covered)                         # ehrlich: was ohne Dump fehlt
    miss = m.quick_check("FM-1_91060", "Melodee 1.6.0-rc.7", {"installed": False, "length": 1, "crc": 2}, refs)
    assert miss.level == m.YELLOW and any("Schrift & Werkssounds nachschreiben" in t for _, t in miss.findings)
    other = m.quick_check("FM-1_91060", "Melodee 1.6.0-rc.7", {"installed": True, "length": 5, "crc": 6}, refs)
    assert other.level == m.YELLOW and any("anderer Build" in t for _, t in other.findings)
    unk = m.quick_check("FM-1_777", "Irgendwas 1.0", None, refs)
    assert unk.level == m.YELLOW and any("Melodee ab 1.6" in t for t in unk.not_covered)


def test_demo_dumps_cover_three_verdicts():
    refs, heads = m.load_known() + m.demo_refs(), m.demo_heads()     # wie in der App: echte + Demo-Steckbriefe
    d = m.demo_dumps()
    clean = m.check_dump(d["demo-sauber.bin"], refs, factory_heads=heads)
    assert clean.level == m.GREEN and clean.firmware.startswith("Demo-Melodee")
    res = m.check_dump(d["demo-v16-reste.bin"], refs, factory_heads=heads)
    assert res.level == m.YELLOW and res.residue == [(0x97000, 0x12000)]
    assert any("V16-Reste aufräumen" in t and "ungetestet" in t for _, t in res.findings)  # Aufräumen: ehrlich „ungetestet“
    tr = m.check_dump(d["demo-v16-abgeschnitten.bin"], refs, factory_heads=heads)
    assert tr.level == m.RED and "abgeschnitten" in tr.firmware
    with pytest.raises(m.DumpError):
        m.check_dump(b"\x00" * 1000, refs)
    assert m.check_dump(b"\xff" * m.FLASH_SIZE, refs).level == m.RED


def test_describe_loss_names_melodee_regions():
    to16 = m.describe_loss("FM-1_91060", "melodee", "FM-1_016")
    assert "0x93000–0xA8FFF" in to16[0] and "Prophet-Bänke" in to16[0]
    assert "Ressourcen" in to16[1] and "Emulator" in to16[1]
    back = m.describe_loss("FM-1_016", "stock-v16", "FM-1_91060")
    assert any("Reste von V16" in x for x in back)
    assert m.describe_loss("FM-1_91060", "melodee", "FM-1_900") == []


# ------------------------------------------------------------------ mit echten Paketen
@need_pkgs
def test_identities_hashes_layouts(pk):
    refs = {r.label: r for r in m.load_known()}
    for label, p in pk.items():
        r = refs[label]
        assert (p.identity, p.file_sha256, p.write_end, p.head_sha256, p.app_sha256) == \
            (r.identity, r.file_sha256, r.flash_bytes, r.head_sha256, r.app_sha256), label
    assert pk["v15"].layout["VM"] == (0x93000, 0xE9000)
    assert pk["v16"].layout["VM"] == (0xBA000, 0xDA000) and pk["v16"].layout["SEQ64"] == (0xDA000, 0xEC000)
    assert pk["v16"].layout["USER"] == (0xED000, 0xFF000)
    assert m.classify_family(pk["v16"]) == "v16-layout" and m.classify_family(pk["skiff_2.0.3"]) == "v15-layout"


@need_pkgs
def test_same_loader_in_v15_and_v16(pk):
    def ota(p):
        raw = Path(p.path).read_bytes()
        img = b"".join(raw[i * 0x30:i * 0x30 + 0x2F] for i in range(20)) + raw[0x30 * 20:]
        e = next(x for x in p.entries if x["name"] == "ota.bin")
        return hashlib.sha256(img[e["offset"]:e["offset"] + e["length"]]).hexdigest()
    known = json.loads(m.DATA_FILE.read_text("utf-8"))
    assert ota(pk["v15"]) == ota(pk["v16"]) == known["loader_sha256"]


@need_pkgs
def test_chain_melodee_v16_back_with_factory_head(pk):
    """echtes Gerät: Bootkopf ab Werk; normaler Update schreibt ihn nie -> bleibt Werk, 22 Sektoren V16-Reste"""
    known = list(pk.values())
    mel = pk["melodee_1.5.1"]
    start = m.installed_state(mel, head=pk["v15"].flash[:m.HEAD_END])
    r = m.replay(start, [("ota", pk["v16"], 1.0), ("boot", pk["v16"]), ("ota", pk["v15"], 1.0),
                         ("ota", mel, 1.0, True)], mel, known)
    assert r["head_owner"] == "Werk (V13–V15)" and r["final_app_intact"]
    assert len(r["foreign_residue"]) == 22 and m.cleanup_plan(r["foreign_residue"]) == [(0x93000, 0x16000)]
    rep = m.check_dump(bytes(r["flash"].data), packages=known, expected="FM-1_91051")
    assert rep.level == m.YELLOW and rep.residue == [(0x93000, 0x16000)] and rep.firmware == "Melodee 1.5.1"
    # mit V15-Erststart verschwinden die ersten vier Reste-Sektoren
    r2 = m.replay(start, [("ota", pk["v16"], 1.0), ("ota", pk["v15"], 1.0), ("boot", pk["v15"]), ("ota", mel, 1.0)],
                  mel, known)
    assert min(x["sector"] for x in r2["foreign_residue"]) == 0x97000


@need_pkgs
def test_truncated_v16_is_detected(pk):
    """Hypothese aus einem Nutzerbericht: V16 nur bis 0x92FFF geschrieben (nur ein ROM-Bootmodus-Werkzeug für V15-große Pakete kann das)"""
    known = list(pk.values())
    start = m.installed_state(pk["melodee_1.5.1"], head=pk["v15"].flash[:m.HEAD_END])
    r = m.replay(start, [(pk["v16"], "from4000", 0x93000)], pk["v16"], known)
    assert not r["final_app_intact"]
    rep = m.check_dump(bytes(r["flash"].data))
    assert rep.level == m.RED and "abgeschnitten" in rep.firmware
    w = m.preflight(pk["v16"], current=pk["melodee_1.5.1"], have_backup=True, mode="from4000")
    assert ("block", "TOOL_TRUNCATES") in {(a, b) for a, b, _ in w}


@need_pkgs
def test_clean_unit_is_green_and_damage_is_red(pk):
    known = list(pk.values())
    f = m.installed_state(pk["melodee_1.5.1"], head=pk["v15"].flash[:m.HEAD_END])
    assert m.check_dump(bytes(f.data), packages=known).level == m.GREEN
    f.data[0x50000] ^= 1
    rep = m.check_dump(bytes(f.data), packages=known)
    assert rep.level == m.RED and "beschädigt" in rep.firmware
    assert m.verify_dump(bytes(f.data), pk["melodee_1.5.1"]) == (False, 0x50000, 1)


@need_pkgs
def test_preflight_codes(pk):
    codes = lambda lst: {c for _, c, _ in lst}  # noqa: E731
    w = m.preflight(pk["v16"], current=pk["melodee_1.5.1"], filename="FM-1_v15.fwsc", have_backup=False,
                    known_hashes=set())
    assert {"NAME_IDENTITY_MISMATCH", "NO_BACKUP", "LAYOUT_CHANGE", "OVERWRITES_DATA", "HASH_UNKNOWN"} <= codes(w)
    w = m.preflight(pk["v15"], current=pk["v16"], have_backup=True, known_hashes=m.known_hashes())
    assert "NO_BACKUP" not in codes(w) and "LAYOUT_CHANGE" in codes(w) and "HASH_UNKNOWN" not in codes(w)


@need_pkgs
def test_ota_update_never_touches_head_or_above_image(pk):
    known = list(pk.values())
    start = m.installed_state(pk["melodee_1.5.1"])
    f = m.replay(start, [("ota", pk["v15"], 1.0)], pk["v15"], known)["flash"]
    assert bytes(f.data[:0x4000]) == bytes(start.data[:0x4000])
    assert bytes(f.data[0x93000:]) == bytes(start.data[0x93000:])
    ab = m.replay(start, [("ota", pk["v16"], 0.10), (pk["v15"], "from4000", None), ("ota", pk["melodee_1.5.1"], 1.0)],
                  pk["melodee_1.5.1"], known)
    assert len(ab["foreign_residue"]) == 17 and m.cleanup_plan(ab["foreign_residue"]) == [(0x98000, 0x11000)]
