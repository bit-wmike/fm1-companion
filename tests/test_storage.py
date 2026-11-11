# SPDX-License-Identifier: GPL-3.0-only
"""Storage page logic: what is measured, what "Delete" removes, what always stays (user data, the package of an
interrupted installation, everything while an installation runs) and the automatic cleanup."""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

import pytest


@pytest.fixture
def env(tmp_path, monkeypatch):
    from fm1companion import config, devices, firmware, storage
    for name, sub in (("DATA_DIR", "data"), ("CACHE_DIR", "cache"), ("CONFIG_DIR", "config")):
        monkeypatch.setattr(config, name, tmp_path / sub)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config" / "config.json")
    monkeypatch.setattr(config, "BACKUP_FILE", tmp_path / "config" / "config.json.bak")
    monkeypatch.setattr(firmware, "REPOS_DIR", tmp_path / "data" / "repos")
    monkeypatch.setattr(devices, "PROFILES_FILE", tmp_path / "data" / "devices.json")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    tmpd = tmp_path / "tmp"
    tmpd.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tmpd))
    monkeypatch.setattr(config, "_settings", config.Settings(user_root=str(tmp_path / "FM-1")))
    return tmp_path


def put(p: Path, size: int = 100, age_days: float = 0.0) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * size)
    t = time.time() - age_days * 86400
    os.utime(p, (t, t))
    return p


def fw_download(root: Path, key: str, tag: str, age_days: float) -> Path:
    d = root / "FM-1" / "Firmware" / key / tag
    put(d / f"{key}-{tag}.fwsc", 1000, age_days)
    return d


def test_tree_size_counts_links_without_following(env):
    from fm1companion import storage
    big = put(env / "outside" / "big.bin", 5000)
    d = env / "d"
    put(d / "a", 10)
    put(d / "sub" / "b", 20)
    (d / "link").symlink_to(big)
    (d / "dirlink").symlink_to(env / "outside")
    size, files = storage.tree_size(d)
    assert files == 4 and 30 <= size < 5000


def test_firmware_downloads_delete_keeps_reserve_and_own_files(env):
    from fm1companion import storage
    fw_download(env, "melodee", "v1.6.0", 30)
    fw_download(env, "melodee", "v1.6.1", 1)
    fw_download(env, "stock-v15", "V15", 100)
    own = put(env / "FM-1" / "Firmware" / "my-own.fwsc", 50)
    own_dir = put(env / "FM-1" / "Firmware" / "mine" / "x" / "y.fwsc", 50)
    cats = {c.key: c for c in storage.scan()}
    assert cats["firmware"].size >= 3000 and cats["firmware"].cleanable == 2000
    res = storage.clear("firmware")
    assert res.removed == 2 and res.freed == 2000
    fw = env / "FM-1" / "Firmware"
    assert not (fw / "melodee" / "v1.6.0").exists() and not (fw / "melodee" / "v1.6.1").exists()
    assert (fw / "stock-v15" / "V15").exists(), "the newest original V15 stays (offline rescue)"
    assert own.exists() and own_dir.exists(), "files the user put there are never touched"


def test_interrupted_installation_is_protected(env):
    from fm1companion import storage
    from fm1companion.flash import journal
    src = fw_download(env, "melodee", "v1.6.1", 5)
    old = fw_download(env, "melodee", "v1.5.1", 50)
    j = journal.Journal()
    kept = j.keep_package(next(src.glob("*.fwsc")), "a" * 64)
    other = put(j.dir / "packages" / ("b" * 64 + ".fwsc"), 300)
    put(env / "data" / "repos" / "melodee" / "installer.py", 10)
    j.begin(target="Melodee 1.6.1", package=str(kept), source=str(next(src.glob("*.fwsc"))), sha256="a" * 64)
    j.update(phase="writing")
    j.finish("safe", keep_active=True)
    assert storage.resume_possible()
    storage.clear("firmware")
    storage.clear("packages")
    storage.clear("repos")
    assert src.exists() and not old.exists()
    assert kept.exists() and not other.exists()
    assert (env / "data" / "repos" / "melodee").exists(), "tools stay while a resume is possible"
    assert j.file.exists(), "the journal itself is never deleted"
    j.clear_active()
    storage.clear("packages")
    storage.clear("repos")
    assert not kept.exists() and not (env / "data" / "repos" / "melodee").exists()


def test_nothing_deleted_while_installing(env):
    from fm1companion import storage
    from fm1companion.flash import journal
    d = fw_download(env, "melodee", "v1.6.1", 5)
    j = journal.Journal()
    j.acquire()
    try:
        assert storage.install_running()
        with pytest.raises(storage.Busy):
            storage.clear("firmware")
        assert storage.auto_clean().removed == 0
    finally:
        j.release()
    assert d.exists()
    j.dir.mkdir(parents=True, exist_ok=True)
    j.lockfile.write_text(str(os.getpid()))           # lock of another window (here: a living process)
    j2 = journal.Journal()
    assert storage.install_running()
    j2.lockfile.write_text("999999999")                 # a dead process: no installation
    assert not storage.install_running()


def test_logs_rotated_deleted_current_emptied(env):
    from fm1companion import applog, storage
    cur = put(applog.log_file(), 700)
    r1 = put(applog.log_dir() / (applog.log_file().name + ".1"), 1000, 40)
    r2 = put(applog.log_dir() / (applog.log_file().name + ".2"), 1000, 2)
    res = storage.auto_clean({"log_days": 30})
    assert not r1.exists() and r2.exists() and cur.stat().st_size == 700
    res = storage.clear("logs")
    assert not r2.exists() and cur.exists() and cur.stat().st_size == 0 and res.freed == 1700


def test_auto_clean_keeps_newest_and_device_versions(env):
    from fm1companion import storage
    for i, tag in enumerate(("v1.6.1", "v1.6.0", "v1.5.1", "v1.5.0")):
        fw_download(env, "melodee", tag, 1 + i * 10)
    (env / "data").mkdir(exist_ok=True)
    (env / "data" / "devices.json").write_text(json.dumps(
        {"profiles": [{"id": "x", "name": "A", "firmware": "melodee", "firmware_version": "v1.5.0"}]}))
    pk = [put(env / "data" / "flash" / "packages" / f"{c * 64}.fwsc", 10, i) for i, c in enumerate("abcde")]
    for i, tag in enumerate(("v1.6.1", "v1.6.0", "v1.5.1")):
        put(env / "data" / "editor" / "melodee" / tag / "web" / "editor.html", 10, i * 5)
    thumb_old = put(env / "cache" / "catalog" / "thumbs" / "old.img", 10, 60)
    thumb_new = put(env / "cache" / "catalog" / "thumbs" / "new.img", 10, 1)
    catalog_json = put(env / "cache" / "catalog" / "catalog.json", 10, 90)
    news = put(env / "cache" / "catalog" / "changes.json", 10, 90)
    pack_old = put(env / "cache" / "samplepacks" / "p1" / "repo" / "a.wav", 10, 60)
    tmp_old = put(env / "tmp" / "fm1c-preview-1.wav", 10, 3)
    tmp_new = put(env / "tmp" / "fm1c-preview-2.wav", 10, 0)
    foreign = put(env / "tmp" / "other-app.wav", 10, 30)
    res = storage.auto_clean({"firmware_keep": 2, "packages_keep": 3, "editor_keep": 2, "cache_days": 30})
    fw = env / "FM-1" / "Firmware" / "melodee"
    assert sorted(p.name for p in fw.iterdir()) == ["v1.5.0", "v1.6.0", "v1.6.1"], "newest 2 + device version"
    assert [p.exists() for p in pk] == [True, True, True, False, False]
    assert sorted(p.name for p in (env / "data" / "editor" / "melodee").iterdir()) == ["v1.6.0", "v1.6.1"]
    assert not thumb_old.exists() and thumb_new.exists() and catalog_json.exists() and news.exists()
    assert not pack_old.parent.parent.exists()
    assert not tmp_old.exists() and tmp_new.exists() and foreign.exists()
    assert res.removed >= 7 and not res.errors


def test_zero_settings_switch_rules_off(env):
    from fm1companion import storage
    for i, tag in enumerate(("v3", "v2", "v1")):
        fw_download(env, "melodee", tag, i * 100)
    old_log = put(env / "state" / "fm1-companion" / "logs" / "fm1-companion.log.3", 10, 400)
    storage.auto_clean({"firmware_keep": 0, "log_days": 0, "packages_keep": 0, "editor_keep": 0, "cache_days": 0})
    assert len(list((env / "FM-1" / "Firmware" / "melodee").iterdir())) == 3 and old_log.exists()


def test_run_auto_once_a_day(env):
    from fm1companion import config, storage
    old = put(env / "state" / "fm1-companion" / "logs" / "fm1-companion.log.2", 10, 60)
    assert storage.run_auto() is not None and not old.exists()
    assert storage.run_auto() is None, "not twice a day"
    config.module("storage", storage.DEFAULTS)["auto"] = False
    assert not storage.auto_due(time.time() + 3 * 86400)


def test_reports_temp_and_user_data(env):
    from fm1companion import config, storage
    rep = put(env / "home" / "FM-1-Companion-Fehlerbericht-2026-01-01_10-00-00.zip", 400)
    other = put(env / "home" / "my-notes.zip", 10)
    broken = put(env / "config" / "config.json.kaputt-2026", 20)
    backup = put(env / "FM-1" / "Backups" / "A" / "a.json", 5000)
    rec = put(env / "FM-1" / "Aufnahmen" / "take.wav", 5000)
    cats = {c.key: c for c in storage.scan()}
    assert cats["reports"].cleanable == 420
    assert not cats["userdata"].deletable and cats["userdata"].size >= 10000
    storage.clear("reports")
    assert not rep.exists() and not broken.exists() and other.exists()
    for key in [c.key for c in storage.categories() if c.deletable]:
        storage.clear(key)
    assert backup.exists() and rec.exists(), "the user's own files are never deleted"
    total, cleanable = storage.app_total()
    assert cleanable == 0 or cleanable < total
    assert config.load().root.exists()


def test_studio_states_deleted_one_by_one(env):
    from fm1companion import storage
    a = put(env / "data" / "studio" / "Mein_FM-1" / "Studio-Stand.fm1studio", 300)
    put(env / "data" / "studio" / "Zweites" / "Studio-Stand.fm1studio", 300)
    assert [s[0] for s in storage.studio_states()] == ["Mein_FM-1", "Zweites"]
    assert "studio" not in [p for c in storage.categories() if c.deletable for p in [c.key]]
    assert storage.delete_studio_state("Mein_FM-1") >= 300 and not a.exists()
    with pytest.raises(FileNotFoundError):
        storage.delete_studio_state("../data")
    assert [s[0] for s in storage.studio_states()] == ["Zweites"]


def test_syscheck_reports_app_storage(env):
    from fm1companion import storage, syscheck
    put(env / "cache" / "samplepacks" / "p" / "a.wav", 3000)
    f = syscheck.storage_finding()
    assert f.key == "storage" and f.level in ("ok", "info") and storage.human(3000) in f.title
