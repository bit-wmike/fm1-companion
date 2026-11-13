# SPDX-License-Identifier: GPL-3.0-only
"""KARMA as a built-in firmware: pinned site packages, recognition, catalog entry, install plan.

  python3 -m pytest tests/test_karma_builtin.py -q
No network: the web installer page and the downloads are replaced.
"""
from __future__ import annotations

import importlib
import io
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
KARMA_31 = Path("/tmp/karma/docs/firmware/karma-3.1.fwsc")
SHA_31 = "f1f28096a2e1a64c0ca0fe626b38818d96bb4950025b2e6d14db40df369eea1f"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    for k, sub in (("XDG_CONFIG_HOME", "c"), ("XDG_DATA_HOME", "d"), ("XDG_CACHE_HOME", "k")):
        monkeypatch.setenv(k, str(tmp_path / sub))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))       # (the firmware folder lives under ~/FM-1)
    from fm1companion import config, firmware
    importlib.reload(config)
    importlib.reload(firmware)
    monkeypatch.setattr(firmware, "pages_release", lambda s, use_cache_secs=600: None)    # offline
    yield tmp_path
    importlib.reload(firmware)


def test_spec(env):
    from fm1companion import firmware
    from fm1companion.flash import planner
    sp = firmware.spec("karma")
    assert sp is not None and sp.builtin and sp in firmware.BUILTIN and sp.name == "KARMA"
    assert sp.repo == "eatyourgreensstudio-dotcom/karma-fm1" and sp.license == "GPL-3.0"
    assert sp.product == "FM-1_926" and sp.loader_family == "sloopG3" and sp.protocol == "felucca"
    assert sp.editor_url.endswith("/webapp/editor/") and sp.emulator_url.endswith("/emu/")
    assert firmware.pinned_sha(sp, "v3.1") == SHA_31 and firmware.pinned_sha(sp, "v9.9") == ""
    for tag, v in sp.versions.items():
        assert v["url"].startswith("https://github.com/eatyourgreensstudio-dotcom/karma-fm1/raw/")
        assert v["url"].endswith(f"/docs/firmware/karma-{tag[1:]}.fwsc") and len(v["sha256"]) == 64
        assert len(v["url"].split("/raw/")[1].split("/")[0]) == 40           # pinned to a commit, not a branch
    t = planner.Target.from_spec(sp, tag="v3.1")
    assert t.family == "felucca" and t.product == "FM-1_926" and not t.is_stock


def test_releases_pinned_newest_first(env):
    from fm1companion import firmware
    rels = firmware.fetch_releases(firmware.spec("karma"))
    assert [r.tag for r in rels][:2] == ["v3.1", "v3.0.5"] and rels[-1].tag == "v3.0"
    assert all(r.sha256 and r.assets for r in rels) and not any(r.prerelease for r in rels)
    assert firmware.latest(rels, "stable").tag == "v3.1"


def test_web_installer_names_a_newer_version(env, monkeypatch):
    from fm1companion import firmware
    importlib.reload(firmware)                       # the real pages_release
    page = ('<script>const meta = {"version": "3.2", "product": "FM-1_926", "pkg": "../../firmware/karma-3.2.fwsc", '
            '"sha256": "' + "ab" * 32 + '"};</script>')

    class R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(firmware, "urlopen", lambda req, timeout=0: R(page.encode()))
    sp = firmware.spec("karma")
    rels = firmware.fetch_releases(sp, use_cache_secs=0)
    assert rels[0].tag == "v3.2" and rels[0].sha256 == "ab" * 32
    assert next(iter(rels[0].assets.values())) == \
        "https://eatyourgreensstudio-dotcom.github.io/karma-fm1/firmware/karma-3.2.fwsc"
    # another identity on the page: not offered
    page = page.replace("FM-1_926", "FM-1_900").replace("3.2", "3.3")
    assert firmware.pages_release(sp, use_cache_secs=0) is None
    # the page names another checksum for a pinned version: the app keeps its own
    page = ('<script>const meta = {"version": "3.1", "product": "FM-1_926", "pkg": "../../firmware/karma-3.1.fwsc", '
            '"sha256": "' + "cd" * 32 + '"};</script>')
    rels = firmware.fetch_releases(sp, use_cache_secs=0)
    assert next(r for r in rels if r.tag == "v3.1").sha256 == SHA_31 and len(rels) == 7


def test_download_checks_the_pinned_checksum(env, monkeypatch):
    from fm1companion import firmware
    from fm1companion.flash import package as pk
    sp = firmware.spec("karma")
    rel = firmware.fetch_releases(sp)[0]
    raw = pk.build_package("FM-1_926", "felucca", seed=3)
    monkeypatch.setattr(firmware, "_fetch", lambda url, dest, progress=None: Path(dest).write_bytes(raw))
    with pytest.raises(firmware.ChecksumError):
        firmware.download(sp, rel, log=lambda *_a: None)
    assert not list((firmware.load_settings().firmware_dir / "karma" / "v3.1").glob("*.fwsc"))
    if KARMA_31.exists():
        real = KARMA_31.read_bytes()
        monkeypatch.setattr(firmware, "_fetch", lambda url, dest, progress=None: Path(dest).write_bytes(real))
        got = firmware.download(sp, rel, log=lambda *_a: None)
        assert got.verified and got.fwsc.name == "karma-3.1.fwsc"


def test_recognition_lines_up(env):
    from fm1companion import firmware, fwfamily, studio
    from fm1companion.ui.firmware_page import match_by_sha
    assert firmware.guess_firmware("FM-1_926", "FELUCCA KARMA 3.1", "felucca")[0] == "karma"
    assert fwfamily.by_info("FELUCCA KARMA 3.1").key == "karma" and fwfamily.by_key("karma").backup == "sloop"
    assert "karma" in studio.FIRMWARES
    m = match_by_sha(SHA_31)
    assert m is not None and m.key == "karma" and m.release_tag == "v3.1" and m.sha256 == SHA_31
    from fm1companion.ui.device import firmware_name
    assert firmware_name("karma") == "KARMA"


@pytest.mark.skipif(not KARMA_31.exists(), reason="KARMA clone missing")
def test_real_package_and_loader(env):
    from fm1companion import firmware
    from fm1companion.flash import package as pk, planner
    info = pk.analyze(KARMA_31, "sloopG3")
    assert info.sha256 == SHA_31 and info.product == "FM-1_926" and info.family == "felucca" and not info.damage
    assert pk.LoaderRule("felucca", "FM-1_926")("ota-FM-1_926")
    sp = firmware.spec("karma")
    t = planner.Target.from_spec(sp, tag="v3.1", file=KARMA_31, package=info)
    t.sha256 = SHA_31
    p = planner.plan_install(planner.DeviceState(identity="FM-1_015"), t)
    assert p.ok, p.blockers


def test_catalog_shows_the_builtin(env):
    from fm1companion import catalog, firmware
    cat = catalog.load()
    e = cat.by_key("karma")
    assert e is not None and e.builtin and e.installable and e.latest.sha256 == SHA_31
    assert e.links["emulator"].endswith("/emu/") and e.lineage == ["SLOOP"] and e.protocol == "felucca"
    assert e.to_spec() is firmware.spec("karma") or e.to_spec().key == "karma"
    assert catalog.spec_by_sha256(SHA_31).key == "karma"
    assert [x.key for x in cat.entries].count("karma") == 1
    assert [x.key for x in catalog.with_builtins(cat).entries].count("karma") == 1      # idempotent
