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


def _dump(p: Path, device: bytes, age_days: float) -> Path:
    from fm1companion import storage
    data = bytearray(b"\xff" * storage.FULL_DUMP_SIZE)
    if device:
        data[storage.KEY_AREA[0]:storage.KEY_AREA[0] + len(device)] = device
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(bytes(data))
    t = time.time() - age_days * 86400
    os.utime(p, (t, t))
    os.utime(p.parent, (t, t))
    return p


def test_rescue_items_mark_newest_full_dump_per_device(env):
    from fm1companion import storage
    base = env / "FM-1" / "Rettung"
    old_a = _dump(base / "Komplettsicherungen" / "fm1-recovery-20260101-100000" / "full-flash-before.bin", b"AAAA", 30)
    new_a = _dump(base / "Komplettsicherungen" / "fm1-recovery-20260201-100000" / "full-flash-before.bin", b"AAAA", 10)
    only_b = _dump(base / "fm1-transporter-dump-20260105.bin", b"BBBB", 25)
    _dump(base / "simulation" / "simulierter-flash-rettung.bin", b"AAAA", 1)
    put(base / "logs" / "unbrick.log", 500, 2)
    put(base / "notes.txt", 10, 2)                               # not a dump: not listed
    items = storage.rescue_items()
    by = {i.path: i for i in items}
    assert set(by) == {old_a.parent, new_a.parent, only_b, base / "simulation" / "simulierter-flash-rettung.bin",
                       base / "logs"}
    assert by[new_a.parent].newest and not by[old_a.parent].newest and by[only_b].newest
    assert by[new_a.parent].device == by[old_a.parent].device != by[only_b].device
    sim = by[base / "simulation" / "simulierter-flash-rettung.bin"]
    assert sim.simulated and sim.full and not sim.newest
    assert by[base / "logs"].kind == "logs" and not by[base / "logs"].full
    assert items[0].mtime >= items[-1].mtime
    # never automatic: auto_clean and "Delete" of the app categories leave the folder alone
    storage.auto_clean(now=time.time() + 400 * 86400)
    assert old_a.exists() and only_b.exists()
    cat = next(c for c in storage.categories() if c.key == "rescuedumps")
    assert cat.kind == "data" and not cat.deletable and storage.candidates("rescuedumps") == []
    # one by one, only inside the folder, not while installing, not while just written
    assert storage.delete_rescue_item(old_a.parent) >= storage.FULL_DUMP_SIZE and not old_a.parent.exists()
    with pytest.raises(FileNotFoundError):
        storage.delete_rescue_item(env / "FM-1" / "Backups")
    with pytest.raises(FileNotFoundError):
        storage.delete_rescue_item(base)
    fresh = _dump(base / "fm1-transporter-dump-now.bin", b"CCCC", 0)
    with pytest.raises(storage.Busy):
        storage.delete_rescue_item(fresh)
    assert fresh.exists()
    # the app total does not count the rescue folder (it is your data in the main folder)
    cats = storage.scan()
    rd = next(c for c in cats if c.key == "rescuedumps")
    assert rd.size > 0 and storage.app_total(cats)[0] < rd.size


def test_rescue_delete_refused_while_installing(env, monkeypatch):
    from fm1companion import storage
    d = _dump(env / "FM-1" / "Rettung" / "x.bin", b"AAAA", 30)
    monkeypatch.setattr(storage, "install_running", lambda: True)
    with pytest.raises(storage.Busy):
        storage.delete_rescue_item(d)
    assert d.exists()


def test_webdata_cleared_now_or_at_next_start(env):
    from fm1companion import storage
    put(env / "data" / "webengine" / "Local Storage" / "leveldb" / "000003.log", 400)
    put(env / "data" / "webengine" / "Cookies", 100)
    assert next(c for c in storage.categories() if c.key == "webdata").kind == "data"
    assert storage.apply_pending_webdata_clear() is None              # nothing requested: nothing happens
    assert (env / "data" / "webengine" / "Cookies").exists()
    storage.request_webdata_clear()
    assert storage.webdata_clear_pending()
    res = storage.apply_pending_webdata_clear()
    assert res.freed >= 500 and not list((env / "data" / "webengine").iterdir())
    assert not storage.webdata_clear_pending()


def test_studio_state_in_use():
    from fm1companion import storage

    class Svc:
        st = object()

        def profile_folder(self):
            return "Mein_FM-1"
    assert storage.studio_state_in_use("Mein_FM-1", Svc())
    assert not storage.studio_state_in_use("Zweites", Svc()) and not storage.studio_state_in_use("Mein_FM-1", None)


def test_reset_module_in_place_keeps_own_keys(env):
    from fm1companion import config
    d = config.module("catalog", {"sort": "az", "thumbnails": True})
    d.update(sort="stars", thumbnails=False, github_token="ghp_x", wishlist=["a"], extra=1)
    held = config.module("catalog")
    config.reset_module("catalog", {"sort": "az", "thumbnails": True, "wishlist": []}, ("github_token", "wishlist"))
    assert held is config.module("catalog")                         # pages keep their reference
    assert held == {"sort": "az", "thumbnails": True, "wishlist": ["a"], "github_token": "ghp_x"}


def test_page_settings_registry_has_defaults():
    pytest.importorskip("PyQt6.QtWidgets")
    from fm1companion.ui.settings_dialog import PAGE_SETTINGS
    keys = [k for k, *_r in PAGE_SETTINGS]
    assert len(keys) == len(set(keys)) and {"catalog", "studio", "midi", "recorder"} <= set(keys)
    for key, _title, defaults, keep in PAGE_SETTINGS:
        d = defaults()
        assert isinstance(d, dict) and d and isinstance(keep, tuple), key
        assert d is not defaults()                                   # a fresh copy every time
    assert "github_token" in dict((k, kp) for k, _t, _d, kp in PAGE_SETTINGS)["catalog"]


def test_token_state_never_shows_the_token():
    pytest.importorskip("PyQt6.QtWidgets")
    from fm1companion.ui.pages.discover import token_state
    assert "••••" not in token_state("")
    assert token_state("ghp_abcdefghijkl1234").endswith("(••••1234)")
    assert "short" not in token_state("short") and "••••" in token_state("short")
