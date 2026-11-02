# SPDX-License-Identifier: GPL-3.0-only
"""Firmware-Katalog, Versionen, Download mit Prüfsumme, Paket-Identität, Installer-Aufruf.
Braucht Internet (GitHub) und den Melodee-Klon unter /tmp/melodee.

  python3 -m pytest tests/test_firmware.py -q
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MELODEE = Path("/tmp/melodee")


@pytest.fixture()
def fw(tmp_path, monkeypatch):
    for k, sub in (("XDG_CONFIG_HOME", "c"), ("XDG_DATA_HOME", "d"), ("XDG_CACHE_HOME", "k")):
        monkeypatch.setenv(k, str(tmp_path / sub))
    import importlib
    from fm1companion import config
    importlib.reload(config)
    from fm1companion import firmware
    importlib.reload(firmware)
    s = config.load()
    s.user_root = str(tmp_path / "FM-1")
    if MELODEE.exists():
        firmware.REPOS_DIR.mkdir(parents=True, exist_ok=True)
        os.symlink(MELODEE, firmware.REPOS_DIR / "melodee")
    return firmware


def test_version_order(fw):
    tags = ["v1.5.1", "v1.6.0-rc.7", "v1.6.0", "v1.6.0-rc.10", "v1.6.0-rc.2", "v1.4.5"]
    assert sorted(tags, key=fw.version_key) == ["v1.4.5", "v1.5.1", "v1.6.0-rc.2", "v1.6.0-rc.7", "v1.6.0-rc.10", "v1.6.0"]
    assert fw.is_prerelease("v1.6.0-rc.7") and not fw.is_prerelease("v2.8.3")


def test_latest_by_channel(fw):
    R = fw.Release
    rels = [R("v1.6.0-rc.7", True), R("v1.5.1", False)]
    assert fw.latest(rels, "rc").tag == "v1.6.0-rc.7"
    assert fw.latest(rels, "stable").tag == "v1.5.1"


def test_guess(fw):
    assert fw.guess_firmware("FM-1_91060")[0] == "melodee"
    assert fw.guess_firmware("FM-1_720")[0] == "skiff"
    assert fw.guess_firmware("", "Melodee 1.6.0-rc.7") == ("melodee", "v1.6.0-rc.7")
    fw.remember_identity("FM-1_720", "skiff", "v2.0.3")
    assert fw.guess_firmware("FM-1_720") == ("skiff", "v2.0.3")


@pytest.mark.skipif(not MELODEE.exists(), reason="Melodee-Klon fehlt")
def test_download_verify_identity(fw):
    s = fw.spec("melodee")
    rels = fw.fetch_releases(s, 0)
    rel = next(r for r in rels if r.tag == "v1.6.0-rc.7")
    log = []
    pkg = fw.download(s, rel, log=log.append)
    assert pkg.verified and pkg.res and pkg.fwsc.stat().st_size > 500000
    assert any("Prüfsummen stimmen" in x for x in log)
    # zweiter Download nutzt die vorhandenen Dateien
    pkg2 = fw.download(s, rel, log=log.append)
    assert pkg2.fwsc == pkg.fwsc
    # beschädigte Datei wird erkannt und gelöscht
    data = bytearray(pkg.fwsc.read_bytes())
    data[len(data) // 2] ^= 0xFF                 # bleibt "vorhanden", Prüfsumme falsch
    pkg.fwsc.write_bytes(bytes(data))
    with pytest.raises(RuntimeError, match="Prüfsumme"):
        fw.download(s, rel, log=log.append)
    assert not pkg.fwsc.exists()
    pkg = fw.download(s, rel, log=log.append)
    repo = fw.ensure_tools(s, "v1.6.0-rc.7", log.append)
    assert (repo / "tools/fm1_install.py").exists() and fw.tools_tag("melodee") == "v1.6.0-rc.7"
    assert fw.package_identity(pkg.fwsc) == "FM-1_91060"


@pytest.mark.skipif(not MELODEE.exists(), reason="Melodee-Klon fehlt")
def test_install_without_device_fails_cleanly(fw):
    s = fw.spec("melodee")
    rel = next(r for r in fw.fetch_releases(s) if r.tag == "v1.6.0-rc.7")
    pkg = fw.download(s, rel, log=lambda *_: None)
    log = []
    with pytest.raises(fw.InstallError) as e:
        fw.install(s, pkg.fwsc, rel.tag, port_name="kein-fm1-hier", res=pkg.res, log=log.append)
    assert "Code" in str(e.value)
    assert any("starte den Installer" in x for x in log)


def test_skiff_sloop_assets(fw):
    for key in ("skiff", "sloop"):
        rels = fw.fetch_releases(fw.spec(key), 0)
        assert rels, key
        newest = rels[0]
        assert any(n.endswith(".fwsc") for n in newest.assets), (key, newest.assets)
