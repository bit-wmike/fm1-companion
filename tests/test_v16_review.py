# SPDX-License-Identifier: GPL-3.0-only
"""Acceptance V16/flash safety: regression tests for the bugs found in the review.

Parts with real packages need FM1_TEST_PKGDIR (like tests/test_flashmodel.py), otherwise skipped.
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from fm1companion.flash import flashmodel as m, package as pk, planner  # noqa: E402

PKGDIR = Path(os.environ.get("FM1_TEST_PKGDIR", "/nonexistent"))
need_pkgs = pytest.mark.skipif(not (PKGDIR / "v16.fwsc").is_file() or not (PKGDIR / "zip_v15.fwsc").is_file(),
                               reason="real packages missing (FM1_TEST_PKGDIR with v16.fwsc, zip_v15.fwsc)")


def _r35(n):
    return [(n >> 7 * i) & 127 for i in range(5)]


# ---------------------------------------------------------------- resources: button name and meaning of command 82
def test_resource_hint_names_the_real_button():
    """Before: "Ressourcen erneut schreiben" – this button does not exist; "Zur Firmware-Seite" never appeared."""
    from fm1companion import i18n
    from fm1companion.flash import texts
    src = (ROOT / "fm1companion/ui/firmware_page.py").read_text("utf-8")
    assert '_("Rewrite font && factory sounds")' in src
    button = i18n.catalog("de").get(None, "Rewrite font && factory sounds").replace("&&", "&")
    assert button == "Schrift & Werkssounds nachschreiben"
    miss = m.quick_check("FM-1_91060", "Melodee 1.6.0-rc.7", {"installed": False, "length": 1, "crc": 2})
    assert any(button in t for _, t in miss.findings)
    old = i18n.current_language()
    i18n.set_language("en")                          # the English UI shows the hint with the English label
    try:
        assert any("Rewrite font & factory sounds" in texts.tr(t) for _, t in miss.findings)
    finally:
        i18n.set_language(old)
    for p in ("fm1companion/flash/flashmodel.py", "fm1companion/firmware.py"):
        assert "Ressourcen erneut schreiben" not in (ROOT / p).read_text("utf-8"), p


def test_quick_check_other_build_is_about_the_firmware():
    """Command 82 reports length/CRC of the running build (res_want), not the stored ones: installed = matches."""
    rep = m.quick_check("FM-1_91060", "Melodee 1.6.0-rc.7", {"installed": True, "length": 5, "crc": 6})
    text = " ".join(t for _, t in rep.findings)
    assert rep.level == m.YELLOW and "passen zur laufenden Firmware" in text and "anderer Build" in text
    assert "gehören aber zu einem anderen Build" not in text


def test_report_dialog_offers_firmware_page_for_resources(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])  # noqa: F841
    from fm1companion.ui.clean_check import ReportDialog

    class W:
        def page(self, k):
            return object()

        def go(self, k):
            pass
    rep = m.quick_check("FM-1_91060", "Melodee 1.6.0-rc.7", {"installed": False, "length": 1, "crc": 2})
    dlg = ReportDialog(None, rep, "", W())
    assert dlg.b_res is not None


# ---------------------------------------------------------------- boot head: no overclaiming
def test_v16_head_is_not_called_rom_only():
    refs = m.demo_refs()
    for k in refs:
        if k.family == "v16-layout":
            v16 = k
    d = bytearray(m.demo_dumps()["demo-sauber.bin"])
    d[:m.HEAD_END] = m._demo_bytes("demo-v16", m.HEAD_END)
    v16.head_sha256 = m.sha(bytes(d[:m.HEAD_END]))
    rep = m.check_dump(bytes(d), refs, factory_heads=m.demo_heads())
    head = next(t for _, t in rep.findings if "Bootkopf" in t)
    assert "noch nicht geklärt" in head and "entsteht nur" not in head
    help_md = (ROOT / "fm1companion/data/help/de/11-v16-hinweis.md").read_text("utf-8")
    assert "schreiben den Bootkopf (0x0–0x3FFF) nie" not in help_md and "**offen**" in help_md


# ---------------------------------------------------------------- half sectors without a package file
def test_partial_residue_sector_found_from_profile_alone():
    refs, heads = m.load_known() + m.demo_refs(), m.demo_heads()
    v16 = m._demo_bytes("demo-v16", 0xA9000)
    d = bytearray(m.demo_dumps()["demo-sauber.bin"])
    d[0x93000:0xA9000] = b"\xff" * (0xA9000 - 0x93000)
    d[0xA0C00:0xA9000] = v16[0xA0C00:0xA9000]          # abort at ~5 %: sector 0xA0000 only a quarter
    rep = m.check_dump(bytes(d), refs, factory_heads=heads)
    assert rep.residue == [(0xA0000, 0x9000)]
    d[0xA0C00] ^= 1                                     # foreign byte in the half sector: no longer a V16 leftover
    assert m.check_dump(bytes(d), refs, factory_heads=heads).residue == [(0xA1000, 0x8000)]


@need_pkgs
def test_partial_residue_real_v16_and_known_blocks():
    v16 = m.Package.load(PKGDIR / "v16.fwsc")
    ref = m.ref_for("FM-1_016")
    assert len(ref.blocks) == (0xA9000 - 0x93000) // 512
    assert all(ref.blocks[a] == m.block_hash(v16.flash[a:a + 512]) for a in ref.blocks)


# ---------------------------------------------------------------- planner: content instead of identity
def _pkg_info(tmp_path, product, size, name="x.fwsc"):
    raw = pk.build_package(product, "felucca", size=size)
    f = tmp_path / name
    f.write_bytes(raw)
    return pk.analyze(f)




def test_v16_sized_package_counts_as_layout_change(tmp_path):
    """Felucca loader, identity like Melodee, but flash.bin up to 0xA8FFF: backup required and loss notice"""
    info = _pkg_info(tmp_path, "FM-1_91060", 0xA9000)
    assert info.flash_bytes == 0xA9000
    st = planner.DeviceState(identity="FM-1_91060", felucca=True, firmware_key="melodee")
    t = planner.Target.from_file(info)
    assert planner.layout_change(st, t)
    p = planner.plan_install(st, t, backup=False)
    assert any("Backup Pflicht" in b for b in p.blockers)
    p = planner.plan_install(st, planner.Target.from_file(info), backup=True)
    assert any("0xA8FFF" in x for x in p.lost)
    small = _pkg_info(tmp_path, "FM-1_91060", 0x93000, "y.fwsc")
    assert not planner.layout_change(st, planner.Target.from_file(small))


def test_package_reaching_key_mac_is_refused(tmp_path):
    info = _pkg_info(tmp_path, "FM-1_91060", 0x100000)
    p = planner.plan_install(planner.DeviceState(identity="FM-1_91060", felucca=True, firmware_key="melodee"),
                             planner.Target.from_file(info))
    assert not p.ok and any("0xFF000" in b for b in p.blockers)


@need_pkgs
def test_v16_paths_in_the_planner(tmp_path):
    v16 = pk.analyze(PKGDIR / "v16.fwsc")
    assert v16.official == "FM-1_016" and v16.flash_bytes == 0xA9000
    mel = planner.DeviceState(identity="FM-1_91060", felucca=True, firmware_key="melodee")
    # Melodee -> V16: fixed via V15, backup required
    p = planner.plan_install(mel, planner.Target.from_file(v16), via_v15=False)
    assert p.v15_option == "locked" and p.via_stock
    assert not planner.plan_install(mel, planner.Target.from_file(v16), backup=False).ok
    # V16 -> Melodee (Felucca package): fixed via V15
    on16 = planner.DeviceState(identity="FM-1_016", felucca=False)
    mp = _pkg_info(tmp_path, "FM-1_91060", 0x93000)
    p = planner.plan_install(on16, planner.Target.from_file(mp), via_v15=False)
    assert p.v15_option == "locked" and p.via_stock and any("Reste von V16" in x for x in p.lost)
    # the file name lies: real V16 as "FM-1_v15.fwsc"
    lie = tmp_path / "FM-1_v15.fwsc"
    lie.write_bytes((PKGDIR / "v16.fwsc").read_bytes())
    p = planner.plan_install(planner.DeviceState(identity="FM-1_015"), planner.Target.from_file(pk.analyze(lie)))
    assert not p.ok and any("sagt V15" in b for b in p.blockers)


# ---------------------------------------------------------------- abort in the middle of V16 (stage 2) and resume
@need_pkgs
def test_v16_power_loss_resume_only_with_matching_packages(tmp_path, monkeypatch):
    import importlib
    import time
    for k, sub in (("XDG_CONFIG_HOME", "c"), ("XDG_DATA_HOME", "d"), ("XDG_CACHE_HOME", "k")):
        monkeypatch.setenv(k, str(tmp_path / sub))
    monkeypatch.delenv("FM1C_DEMO", raising=False)
    from fm1companion import config
    importlib.reload(config)
    from fm1companion.flash import engine, journal, sim
    for mod in (journal, engine):
        importlib.reload(mod)
    from fm1companion.vendor import fm1_install as fi
    monkeypatch.setattr(fi, "DELAY", dict(fi.DELAY, **sim.fast_delays()))
    v16, v15 = pk.analyze(PKGDIR / "v16.fwsc"), pk.analyze(PKGDIR / "zip_v15.fwsc")
    mel = _pkg_info(tmp_path, "FM-1_91060", 0x93000)
    dev = sim.SimFM1(identity="FM-1_015", policy="stock", name="USB Composite Device", power_loss_at=0.5)
    for p in (v16, v15, mel):
        dev.register(p.path.read_bytes())
    e = engine.Engine(backend=sim.SimBackend([dev]), port_filter=lambda n: "Through" not in n, log=lambda *_: None)
    with pytest.raises(engine.FlashError) as ei:
        e.write(v16)
    assert ei.value.state == engine.PAUSED
    act = e.journal.active
    assert act and act["sha256"] == v16.sha256
    t0, ident = time.time(), None
    while ident is None and time.time() - t0 < 3:
        ident = e.probe()
    st = planner.DeviceState(identity=ident.text)
    assert st.update_mode and ident.text.startswith("ota-FM-1_0")
    assert not planner.plan_install(st, planner.Target.from_file(mel), journal_active=act).ok   # wrong loader
    p15 = planner.plan_install(st, planner.Target.from_file(v15), journal_active=act)
    assert p15.ok and any("Unterbrochen" in w for w in p15.warnings)                          # would work, with a note
    kept = pk.analyze(Path(act["package"]))
    assert planner.plan_install(st, planner.Target.from_file(kept), journal_active=act).ok
    assert e.write(kept).identity == "FM-1_016" and dev.flash == v16.image and e.journal.active is None


def test_v16_needs_a_recognised_firmware():
    """detection still running (no identity, editor protocol still unknown): no V16 with "no backup possible\""""
    t = planner.Target(name="V16", product="FM-1_016", family="stock", key="stock-v16")
    p = planner.plan_install(planner.DeviceState(identity=""), t)
    assert not p.ok and any("nicht erkannt" in b for b in p.blockers)
    assert planner.plan_install(planner.DeviceState(identity="FM-1_015"), t).ok
