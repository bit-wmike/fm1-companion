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


# ------------------------------------------- CD-02: X0X / LOOM-8 / FM1 Open files are backups like the others ---

def x0x_file(day: int, kind="auto") -> dict:
    from fm1companion import x0xbk
    img = x0xbk.build([(i, rnd(100 + i, i)) for i in x0xbk.OBJ_IDS])
    return x0xbk.to_file(img, "X0X 1.2.1 BETA", AB.companion_block(None, "x0x", "X0X 1.2.1 BETA", "", kind),
                         created=f"2026-11-{day:02d}T10:00:00Z")


def test_x0x_backups_are_listed_pruned_and_can_be_pinned(isolated):
    import time
    folder = isolated / "Backups" / "X0X box"
    folder.mkdir(parents=True)
    for day in (1, 2, 3, 4):
        backup.write_file(folder / f"FM-1_x0x_2026-11-{day:02d}_10-00-00_auto.json", x0x_file(day))
    metas = AB.list_backups(folder)
    assert len(metas) == 4 and all(m.fmt == "x0x-backup" and m.fw_key == "x0x" and m.automatic for m in metas)
    assert metas[0].objects == 6 and metas[0].projects == 0
    now = metas[0].when.timestamp()
    assert not AB.needs_backup(folder, 1, now=now + 3600)          # (was: True on every connect)
    AB.set_meta(metas[-1].path, pinned=True, note="keep")          # (was: "not a backup of the app")
    assert AB.read_meta(metas[-1].path).pinned and backup.load_file(metas[-1].path).image == "x0x"
    gone = AB.prune(folder, 1)
    assert sorted(p.name for p in gone) == ["FM-1_x0x_2026-11-02_10-00-00_auto.json",
                                            "FM-1_x0x_2026-11-03_10-00-00_auto.json"]
    # an FM1 Open file can be pinned and annotated too
    fo = {"format": "fm1open-backup", "version": 1, "firmware": "FM1 Open V14", "objects": [obj(0, bytes(4264))]}
    p = folder / "open.json"
    p.write_text(json.dumps(fo))
    AB.set_meta(p, pinned=True)
    assert AB.read_meta(p).pinned
    with pytest.raises(backup.BackupError):
        (folder / "other.json").write_text('{"format": "something-else"}')
        AB.set_meta(folder / "other.json", pinned=True)


# ------------------------------- CD-08 / SEC-15 / CD-17: odd but valid JSON never stops the list or the reader ---

ODD = {
    "firmware null": lambda f: f.update(firmware=None),
    "firmware number": lambda f: f.update(firmware=12),
    "created number": lambda f: f.update(created=12),
    "created garbage": lambda f: f.update(created="9999-99-99"),
    "companion text": lambda f: f.update(companion="str"),
    "companion list": lambda f: f.update(companion=[]),
    "companion odd types": lambda f: f.update(companion={"labels": 5, "note": 7, "device": ["a"], "firmware": 3,
                                                         "kind": 9, "pinned": "yes"}),
    "objects numbers": lambda f: f.update(objects=[1, 2]),
    "objects null": lambda f: f.update(objects=[None]),
    "objects a text": lambda f: f.update(objects="x"),
    "id bool": lambda f: f["objects"][0].update(id=True),
    "id list": lambda f: f["objects"][0].update(id=[0]),
    "size text": lambda f: f["objects"][0].update(size="1"),
}


@pytest.mark.parametrize("fmt", ["felucca-backup", "sloop-backup", "x0x-backup", "loom8-backup", "fm1open-backup"])
@pytest.mark.parametrize("case", list(ODD))
def test_odd_backup_files_never_raise_anything_but_backuperror(isolated, fmt, case):
    f = felucca_file()
    f["format"] = fmt
    if fmt == "sloop-backup":
        f["objects"] = [{"id": o["id"], "len": o["size"], "crc": o["crc"], "data": o["data"]} for o in f["objects"]]
    ODD[case](f)
    folder = isolated / "Backups" / "Dev"
    folder.mkdir(parents=True)
    good = folder / "good_auto.json"
    good.write_text(json.dumps(felucca_file("2026-02-01T00:00:00Z")))
    (folder / "odd_auto.json").write_text(json.dumps(f))
    for reader in (backup.read, BI.inspect):
        try:
            reader(json.loads(json.dumps(f)))
        except backup.BackupError:
            pass
    # the list, the retention and "is a backup due?" keep working, whatever the odd file holds
    metas = AB.list_backups(folder, include_damaged=True)
    assert good in [m.path for m in metas]
    for m in metas:
        assert isinstance(m.when.timestamp(), float) and isinstance(m.automatic, bool) and isinstance(m.note, str)
    assert good in [m.path for m in AB.list_backups(folder)]
    AB.needs_backup(folder, 1)
    assert good not in AB.prune(folder, 1)
    json.dumps([m.labels for m in metas])


def test_a_backup_file_with_unusable_fields_is_shown_as_damaged_not_hidden(isolated):
    folder = isolated / "Backups" / "Dev"
    folder.mkdir(parents=True)
    f = felucca_file()
    f["companion"] = "str"
    (folder / "odd.json").write_text(json.dumps(f))
    (folder / "notes.json").write_text('{"my": "own file"}')        # (not a backup at all: ignored, as before)
    assert AB.list_backups(folder) == []
    shown = AB.list_backups(folder, include_damaged=True)
    assert [m.path.name for m in shown] == ["odd.json"] and shown[0].damaged and not shown[0].automatic
    assert AB.prune(folder, 1) == [] and AB.needs_backup(folder, 1)


@pytest.mark.parametrize("fmt", [[], {}, 5, None, True])
def test_format_of_another_type_is_no_backup(fmt):
    """SEC-15: "format": [] / {} used to raise TypeError (unhashable) instead of BackupError"""
    for extra in ({}, {"objects": [obj(0, b"a")]}, {"image": "AAAA"}):
        with pytest.raises(backup.BackupError):
            backup.read({"format": fmt, "version": 1, **extra})
        with pytest.raises(backup.BackupError):
            BI.inspect({"format": fmt, "version": 1, **extra})


def test_sloop_file_with_nan_or_odd_numbers_is_a_backuperror():
    dev = FakeFM1(4, "SLOOP 2.8.3")
    good = backup.capture(link_for(dev)[0], "")
    for key, value in (("crc", float("nan")), ("crc", float("inf")), ("id", 0.5), ("id", "x"), ("len", float("nan")),
                       ("data", 5), ("crc", None)):
        f = json.loads(json.dumps(good))
        f["objects"][0][key] = value
        with pytest.raises(backup.BackupError):
            backup.read(f)
    f = json.loads(json.dumps(good))
    del f["objects"]
    with pytest.raises(backup.BackupError):
        backup.read(f)


def test_duplicate_and_impossible_object_ids_are_refused():
    """CD-17: [0, 0, 2 …] passed the "running music and settings present" check without any settings"""
    ids = backup.FELUCCA_IDS
    good = {"format": "felucca-backup", "version": 1, "firmware": "FELUCCA v1.8.1",
            "objects": [obj(i, rnd(100, i) if i < 3 else b"") for i in ids]}
    backup.read(good)
    for change in (lambda o: o.__setitem__(1, dict(o[0])), lambda o: o.append(dict(o[2])),
                   lambda o: o[2].update(id=True), lambda o: o[2].update(id=-5), lambda o: o[2].update(id=300),
                   lambda o: o[2].update(id=2.0)):
        f = json.loads(json.dumps(good))
        change(f["objects"])
        with pytest.raises(backup.BackupError):
            backup.read(f)


def test_running_music_is_written_last_whatever_the_file_order():
    """CD-17: the order was positional (objects[2:] + [1] + [0]); a file in another order broke the rule silently"""
    def o(i):
        return {"id": i}
    ids = lambda objs: [x["id"] for x in backup.restore_order([o(i) for i in objs])]       # noqa: E731
    assert ids([0, 1, 2, 3, 32]) == [2, 3, 32, 1, 0]                 # the editor's files: as before
    assert ids(backup.CORALFLOWER_IDS[:8]) == [8, 9, 10, 11, 30, 31, 7, 6]   # (no running music: by position, as before)
    assert ids(backup.CHORALROOT_IDS)[-2:] == [8, 1]
    assert ids([2, 3, 0, 1, 32]) == [2, 3, 32, 1, 0]
    assert ids([1, 0, 2]) == [2, 1, 0]
    assert ids([0, 2, 3]) == [3, 2, 0]                               # (no settings: by position, as before)
    # and on the wire
    dev = FeluccaFork("FELUCCA v1.8.1", backup.FELUCCA_IDS)
    link, _t = link_for(dev)
    f = backup.capture(link, "FELUCCA v1.8.1")
    f["objects"] = f["objects"][2:5] + f["objects"][:2] + f["objects"][5:]
    dev.log.clear()
    backup.restore(link, backup.read(f))
    wrote = [x for x in dev.log if isinstance(x, int)]
    assert wrote[-2:] == [1, 0]


# ----------------------------------------------------------- CD-15: the stored kind decides, not the file name ---

def test_retention_goes_by_the_stored_kind_not_by_the_file_name(isolated):
    folder = isolated / "Backups" / "M"
    folder.mkdir(parents=True)

    def put(name, created, kind=None, **extra):
        f = felucca_file(created, kind or "auto", **extra)
        if kind is None:
            del f["companion"]                     # (a web editor's file: no kind – then the name tells)
        (folder / name).write_text(json.dumps(f))
    put("Live set before tour_auto.json", "2026-03-01T00:00:00Z", "manuell")
    put("webeditor_auto.json", "2026-03-01T00:30:00Z", None)
    put("safety.json", "2026-03-01T02:00:00Z", "sicherheit")
    for k in range(4):
        put(f"M_melodee_2026-03-1{k}_auto.json", f"2026-03-1{k}T00:00:00Z", "auto")
    by = {m.path.name: m for m in AB.list_backups(folder)}
    assert not by["Live set before tour_auto.json"].automatic        # (was: counted as automatic, deleted)
    assert by["webeditor_auto.json"].automatic and by["safety.json"].automatic
    gone = sorted(p.name for p in AB.prune(folder, 2))
    assert "Live set before tour_auto.json" not in gone
    assert gone == ["M_melodee_2026-03-10_auto.json", "M_melodee_2026-03-11_auto.json", "safety.json",
                    "webeditor_auto.json"]
    assert (folder / "Live set before tour_auto.json").exists()


# ------------------------------------------------------- CD-10: the mirror after renaming / merging a device ---

def test_mirror_is_not_thinned_after_a_rename_and_the_new_folder_gets_mirrored(isolated, monkeypatch):
    root, mirror = isolated / "B", isolated / "NAS"
    (root / "Old").mkdir(parents=True)
    mirror.mkdir()
    for k in range(1, 7):
        (root / "Old" / f"Old_melodee_2026-04-0{k}_auto.json").write_text(json.dumps(felucca_file(f"2026-04-0{k}T00:00:00Z")))
    assert AB.mirror_all(root, mirror)[0] == 6
    devices.move_profile_folder(root, "Old", "New name")
    assert AB.prune_mirror(root, mirror, 2) == []                    # (was: 4 of the 6 copies deleted on the NAS)
    assert len(list((mirror / "Old").glob("*.json"))) == 6
    # the next backup of the renamed device brings the whole new folder into the mirror
    new = root / "New name" / "New_melodee_2026-04-07_auto.json"
    new.write_text(json.dumps(felucca_file("2026-04-07T00:00:00Z")))
    AB.mirror_file(new, root, mirror)
    assert AB.mirror_missing(new.parent, root, mirror) == 6
    assert len(list((mirror / "New name").glob("*.json"))) == 7
    calls, real = [], AB.mirror_file
    monkeypatch.setattr(AB, "mirror_file", lambda *a: (calls.append(a), real(*a))[1])
    assert AB.mirror_missing(new.parent, root, mirror) == 0 and not calls      # (nothing read again)
    # the retention in the mirror still works where the local retention really deleted
    gone_here = AB.prune(root / "New name", 2)
    assert len(gone_here) == 5
    assert sorted(p.name for p in AB.prune_mirror(root, mirror, 2)) == sorted(p.name for p in gone_here)
    assert len(list((mirror / "Old").glob("*.json"))) == 6           # (the old folder's copies: never touched)


# -------------------------------------------------------------- SEC-06 / SEC-07: importing a backup collection ---

def _zip(path, name, content: bytes, sha=None):
    import hashlib
    import zipfile
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(name, content)
        z.writestr("manifest.json", json.dumps({"format": "fm1companion-backupset", "version": 1, "backups": [
            {"file": name, "sha256": sha or hashlib.sha256(content).hexdigest()}]}))
    return path


def test_zip_entry_that_is_a_path_is_never_read_as_a_local_file(isolated):
    """SEC-06: content that is only a path string made the app inspect that local file instead"""
    local = isolated / "victims-local-file.json"
    local.write_text(json.dumps(felucca_file()))
    for content in (str(local).encode(), b"/nonexistent/x", b'"' + str(local).encode() + b'"', b"[]", b"5"):
        got, skipped = AB.import_zip(_zip(isolated / "c.zip", "backups/Dev/a.json", content), isolated / "root")
        assert got == [] and len(skipped) == 1
        assert "No such file" not in skipped[0] and "Errno" not in skipped[0]        # (no file-existence oracle)
        assert not (isolated / "root" / "Dev" / "a.json").exists()
    good = json.dumps(felucca_file()).encode()
    got, skipped = AB.import_zip(_zip(isolated / "g.zip", "backups/Dev/a.json", good), isolated / "root")
    assert [p.name for p in got] == ["a.json"] and not skipped


def test_zip_bomb_is_refused_by_its_declared_size_and_messages_stay_short(isolated, monkeypatch):
    """SEC-07: an entry was unpacked whole whatever its size, and the skip message embedded the content"""
    import hashlib
    import zipfile
    monkeypatch.setattr(AB, "MAX_ZIP_ENTRY", 1 << 20)
    big = b" " * (4 << 20)
    path = _zip(isolated / "bomb.zip", "backups/a.json", big)
    assert path.stat().st_size < 64 << 10
    read = []
    real = zipfile.ZipFile.read
    monkeypatch.setattr(zipfile.ZipFile, "read", lambda self, name, *a, **k: (read.append(name), real(self, name, *a, **k))[1])
    got, skipped = AB.import_zip(path, isolated / "root")
    assert got == [] and len(skipped) == 1 and len(skipped[0]) < 200
    assert "backups/a.json" not in read                              # (refused before it was unpacked)
    # an entry within the limit that is no backup: the message does not carry its content
    junk = b'{"format": "felucca-backup", "version": 1, "objects": "' + b"x" * 100000 + b'"}'
    got, skipped = AB.import_zip(_zip(isolated / "j.zip", "backups/b.json", junk), isolated / "root")
    assert got == [] and len(skipped[0]) < 300
    # a manifest of absurd size
    with zipfile.ZipFile(isolated / "m.zip", "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", b" " * (AB.MAX_ZIP_MANIFEST + 1))
    with pytest.raises(backup.BackupError):
        AB.import_zip(isolated / "m.zip", isolated / "root")
    assert hashlib  # (imported for _zip)


# ------------------------------------ CD-03: the pages' firmware gate decides by layout, not by the key in the file ---

def _fork(version, ids, **kw):
    from test_backup_matrix import Fork
    return Fork(version, ids, **kw)


UPGRADE_CASES = [
    # (name, device, INFO, the key app 2.5 wrote into the backup, the key app 2.6 gives the device)
    ("Ghoulbox", lambda: _fork("FELUCCA WEB GB2.4", backup.FELUCCA_IDS), "FELUCCA WEB GB2.4", "felucca", "ghoulbox"),
    ("sloopDX", lambda: sloop_dev("FELUCCA sloopDX 4.0.1"), "FELUCCA sloopDX 4.0.1", "sloop", "sloopdx"),
    ("Tessera", lambda: _fork("TESSERA v2.0", list(range(30)), samples=0, no_put=()), "TESSERA v2.0", "", "tessera"),
    ("Saadet", lambda: _fork("SAADET v1.0", [1, 6, 7, *range(9, 29), *range(32, 41)], no_put=()), "SAADET v1.0", "",
     "saadet"),
    ("Pigeon", lambda: _fork("PIGEON v0.5.0-beta.1", backup.FELUCCA_IDS), "PIGEON v0.5.0-beta.1", "", "pigeon"),
    # a studio twin's state (key "choralroot") onto the real device, whose key is the catalog's "choralroot-fm-1"
    ("ChoralRoot", lambda: _fork("ChoralRoot 0.39", backup.CHORALROOT_IDS, samples=0, no_put=()), "ChoralRoot 0.39",
     "choralroot", "choralroot-fm-1"),
    ("JIANT", lambda: _fork("JIANT V10.60", backup.JIANT_IDS, samples=0, no_put=()), "JIANT V10.60", "jiant", "jiant-fm"),
    ("Skiff", lambda: _fork("FELUCCA v2.2", backup.SKIFF_IDS, engines=["FM6", "DRUM-X"]), "FELUCCA v2.2", "skiff", "skiff"),
    ("Clipper", lambda: sloop_dev("FELUCCA CLIPPER 1.3.1", {12: rnd(30, 1)}), "FELUCCA CLIPPER 1.3.1", "sloop", "clipper"),
    ("Jangada", lambda: sloop_dev("JANGADA 1.0.1"), "JANGADA 1.0.1", "sloop", "jangada"),
]


@pytest.mark.parametrize("case", UPGRADE_CASES, ids=[c[0] for c in UPGRADE_CASES])
def test_backups_labelled_by_an_older_app_or_by_no_app_restore_onto_their_own_firmware(case):
    _name, make, version, old_key, new_key = case
    dev = make()
    link, _t = link_for(dev)
    labelled = backup.capture(link, version, companion={"firmware": old_key, "version_string": version,
                                                        "kind": "manuell"})        # as app 2.5 labelled it
    editor = backup.capture(link, version)                                           # the firmware's own web editor
    proto, v, ids = backup.device_inventory(link)
    for f in (labelled, editor):
        rep = BI.inspect(f)
        # Backups > Restore and Devices > Copy (inventory read), Browse & Compare (first by the names alone)
        assert BI.compatibility(rep, new_key, v, ids, proto=proto).ok
        assert BI.compatibility(rep, new_key, version, None).ok
        dev2 = make()
        link2, _t2 = link_for(dev2)
        dev2.log.clear()
        backup.restore(link2, rep.archive, firmware_key=new_key)                    # … and the restore itself agrees
        assert [x for x in dev2.log if isinstance(x, int)]
    # two states of one firmware compare, whatever key they carry
    assert BI.comparable(BI.inspect(labelled), BI.inspect(editor)) == ""


def test_gate_by_names_alone_still_refuses_what_never_fits():
    fel = BI.inspect(backup.capture(link_for(_fork("FELUCCA v1.8.1", backup.FELUCCA_IDS))[0], "FELUCCA v1.8.1"))
    slo = BI.inspect(backup.capture(link_for(sloop_dev("FELUCCA SLOOP 2.8.3"))[0], ""))
    for rep, key, version in ((fel, "sloop", "FELUCCA SLOOP 2.8.3"), (slo, "melodee", "MELODEE v1.6.0"),
                              (fel, "saadet", "SAADET v1.0"), (fel, "tessera", "TESSERA v2.0"),
                              (slo, "sloopdx", "FELUCCA sloopDX 4.0.1"), (fel, "fm1-open", ""), (slo, "x0x", "X0X 1.2.1"),
                              (fel, "jiant-fm", "JIANT V10.60"), (fel, "choralroot-fm-1", "ChoralRoot 0.39")):
        c = BI.compatibility(rep, key, version, None)
        assert not c.ok and c.reason and c.possible, (key, version)
    assert BI.compatibility(slo, "karma", "FELUCCA KARMA 3.1", None).ok          # (fwfamily: KARMA reads SLOOP's)
    assert BI.compatibility(slo, "clipper", "FELUCCA CLIPPER 1.3.1", None).ok
    assert not BI.compatibility(fel, "", "", None).ok                              # nothing known of the target
    # a target the app knows only by a key it cannot place, no INFO: the old key rule, to be safe
    assert not BI.compatibility(fel, "some-unknown-key", "", None).ok


# ---------------------------------------------- CD-05: a lost answer to the sample-slot question is no answer ---

def test_lost_probe_answer_never_becomes_a_sample_write(monkeypatch):
    ids = backup.FELUCCA_IDS + list(range(10, 20))
    make = lambda: FeluccaFork("FELUCCA v1.0", ids, samples=3, no_put=())      # noqa: E731 – its 32..34 are PUT objects
    dev = make()
    dev.objs[32] = rnd(700, 1)
    arch = backup.read(backup.capture(link_for(dev)[0], "FELUCCA v1.0"))
    put = backup._put
    monkeypatch.setattr(backup, "_put", lambda link, args, timeout=4.0: put(link, args, timeout=0.2))

    def run(lost: int):
        dev = make()
        dev.objs[32], dev.objs[33] = rnd(111, 5), rnd(50, 2)
        link, t = link_for(dev)
        orig, n = dev.handle, {"n": 0}

        def handle(data):
            if len(data) > 7 and data[4] == 67 and data[5] == 0 and data[6] in (32, 33, 34) and n["n"] < lost:
                n["n"] += 1
                return None                        # the answer gets lost
            return orig(data)
        dev.handle = handle
        dev.log.clear()
        err = None
        try:
            backup.restore(link, arch)
        except backup.BackupError as e:
            err = e
        return err, [c for c in cmds(t) if c in (11, 12, 13, 14)], [x for x in dev.log if isinstance(x, int)]

    err, smp, wrote = run(lost=1)                  # one lost answer: asked again, restored as PUT objects
    assert err is None and smp == [] and 32 in wrote
    err, smp, wrote = run(lost=2)                  # no answer twice: refused before anything is written
    assert err is not None and smp == [] and wrote == []
    assert "Nichts wurde geschrieben" in str(err)


# ------------------------------------------- CD-06 / CD-18: what is on the device after an interrupted restore ---

def test_object_in_flight_is_reported_as_unclear_not_as_old_state():
    from test_backup_matrix import MEL57
    make = lambda: _fork("MELODEE v1.6.0", MEL57, samples=0, no_put=())                 # noqa: E731
    arch = backup.read(backup.capture(link_for(make())[0], "MELODEE v1.6.0"))
    dev = make()
    link, _t = link_for(dev)
    dev.log.clear()
    orig, n = dev.handle, {"c": 0}

    def handle(data):
        r = orig(data)
        if len(data) > 6 and data[4] == 67 and data[5] == 2:
            n["c"] += 1
            if n["c"] >= 2:
                return None                        # the 2nd commit was executed, its answer is lost; then silence
        return r
    dev.handle = handle
    with pytest.raises(Exception) as ei:
        backup.restore(link, arch)
    e = ei.value
    committed = [x for x in dev.log if isinstance(x, int)]
    assert e.written == committed[:1] and e.unclear == committed[1] and not e.unclear_sample
    planned = [o["id"] for o in arch.objects]
    txt = backup.written_text(e, planned)
    assert "Unklar" in txt and backup.object_title(e.unclear) in txt
    old = txt.split("Noch im alten Zustand")[1]
    assert backup.object_title(e.unclear) + "," not in old and "weitere" in old   # CD-18: "… und N weitere"
    assert txt.count(",") < 25
    full = backup.written_text(e, planned, full=True)                              # the log has every name
    assert backup.object_title(planned[-3]) in full and len(full) > len(txt)

    # an answer that refuses (rc) is an answer: that object stays in its old state, nothing is unclear
    err = backup.BackupError("rc 2")
    backup._with_written(err, [2], 3, False)
    assert err.written == [2] and not hasattr(err, "unclear")
    assert "Unklar" not in backup.written_text(err, [2, 3, 4])
    # nothing written and nothing in flight
    assert backup.written_text(backup.BackupError("x"), [2, 3]) == "Auf dem FM-1 wurde nichts verändert."


def test_sample_that_fails_between_begin_and_end_is_reported_as_possibly_empty():
    import struct
    import zlib
    make = lambda: FeluccaFork("FELUCCA v1.8.1", backup.FELUCCA_IDS)                # noqa: E731
    hdr, data = bytearray(480), rnd(2000, 9)
    struct.pack_into("<IHB", hdr, 0, 0x504D5346, 1, 1)
    hdr[8:12] = b"KICK"
    struct.pack_into("<II", hdr, 16, len(data), zlib.crc32(data))
    f = backup.capture(link_for(make())[0], "FELUCCA v1.8.1")
    f["objects"] = [obj(32, bytes(hdr) + b"\xff" * 32 + data) if o["id"] == 32 else o for o in f["objects"]]
    arch = backup.read(f)
    dev = make()
    link, _t = link_for(dev)
    orig = dev.handle
    dev.handle = lambda d: None if len(d) > 5 and d[4] == 12 else orig(d)          # SMP_WRITE: no answer any more
    with pytest.raises(Exception) as ei:
        backup.restore(link, arch)
    e = ei.value
    assert e.unclear == 32 and e.unclear_sample
    txt = backup.written_text(e, [o["id"] for o in arch.objects])
    assert "leer" in txt and backup.object_title(32) in txt


def test_sloop_restore_reports_the_object_in_flight():
    make = lambda: sloop_dev("FELUCCA SLOOP 2.8.3")                                  # noqa: E731
    arch = backup.read(backup.capture(link_for(make())[0], ""))
    dev = make()
    link, _t = link_for(dev)
    dev.log.clear()
    orig, n = dev.handle, {"c": 0}

    def handle(data):
        r = orig(data)
        if len(data) > 6 and data[4] == 36 and data[5] == 2:
            n["c"] += 1
            if n["c"] >= 3:
                return None
        return r
    dev.handle = handle
    with pytest.raises(Exception) as ei:
        backup.restore(link, arch)
    e = ei.value
    committed = [x for x in dev.log if isinstance(x, int)]
    assert e.written == committed[:2] and e.unclear == committed[2]
    assert "Unklar" in backup.written_text(e, [o["id"] for o in arch.objects], True)


# ------------------------------------------------------------------ CD-07: FM1 Open (its own file protocol) ---

class _OpenDev:
    def __init__(self):
        self.work = bytes([9]) * 4264
        self.slots = {0: bytes([1]) * 4264, 2: bytes([3]) * 4264}
        self.cur, self.fail_after, self.n, self.log = 0, None, 0, []
        self.fail_cmd = None
        self.n_keep = 1 + 1 + 67                   # queries until the working project is read (capabilities, size,
                                                   # 4264 bytes in chunks of 64)


@pytest.fixture
def open_dev(monkeypatch):
    from fm1companion import fm1open as F, fm1openbk as FB
    dev = _OpenDev()

    class Client:
        def __init__(self, link, timeout=1.0):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def probe(self):
            return {"project": dev.cur}

        def sample_status(self):
            return (0, 0)

        def unchanged(self, b):
            return True

        def query(self, cmd, payload=b"", empty_ok=False):
            dev.n += 1
            if (dev.fail_after is not None and dev.n > dev.fail_after) or cmd == dev.fail_cmd:
                raise F.FM1OpenError("no answer")
            self.last_code = 0
            if cmd == 22:
                return bytes(4)
            if cmd == 9:
                if payload[0] in dev.slots:
                    return b"NAME        "
                self.last_code = 1
                return b""
            if cmd == 1:
                dev.dl = dev.work
                return bytes([len(dev.work) & 255, len(dev.work) >> 8])
            if cmd == 2:
                at = payload[0] | payload[1] << 8
                return dev.dl[at:at + payload[2]]
            if cmd == 3:
                dev.up = bytearray()
                return b""
            if cmd == 4:
                dev.up += payload[2:]
                return b""
            if cmd == 5:
                dev.work = bytes(dev.up)
                dev.log.append("work<-upload")
                return b""
            if cmd == 7:
                dev.slots[payload[0]] = dev.work
                dev.log.append(f"save slot{payload[0] + 1}")
                return b""
            if cmd == 8:
                dev.work, dev.cur = dev.slots[payload[0]], payload[0]
                dev.log.append(f"load slot{payload[0] + 1}")
                return b""
            raise AssertionError(cmd)
    monkeypatch.setattr(FB.F, "Client", Client)
    monkeypatch.setattr(FB, "identity", lambda link, timeout=1.0: "FM-1_8000014")
    link = types.SimpleNamespace(send_midi=lambda *a, **k: None)
    return dev, link


def test_fm1open_partial_restore_keeps_the_unsaved_working_project(open_dev):
    from fm1companion import fm1openbk as FB
    dev, link = open_dev
    arch = backup.read(FB.capture(link, "FM1 Open V14"))
    dev.work = bytes([0x77]) * 4264                # new, unsaved work on the device
    dev.slots[0] = bytes([0x55]) * 4264
    dev.log.clear()
    assert backup.restore_selected(link, arch, [3]) == [3]
    assert dev.slots[2] == bytes([3]) * 4264       # project 3 is the backup's …
    assert dev.work == bytes([0x77]) * 4264        # … and the working project is still the user's (was: slot 1's)
    assert "load slot1" not in dev.log and dev.slots[0] == bytes([0x55]) * 4264
    # the backup's current slot selected, but not the working project: still not loaded over the user's work
    dev.log.clear()
    backup.restore_selected(link, arch, [1])
    assert dev.work == bytes([0x77]) * 4264 and not [x for x in dev.log if x.startswith("load")]
    # a full restore: as before – every slot, the current slot, then the backup's working project
    dev.log.clear()
    backup.restore(link, arch)
    assert dev.log[-2:] == ["load slot1", "work<-upload"] and dev.work == bytes([9]) * 4264


def test_fm1open_failed_restore_says_what_was_written(open_dev):
    from fm1companion import fm1openbk as FB
    dev, link = open_dev
    arch = backup.read(FB.capture(link, "FM1 Open V14"))
    dev.log.clear()
    dev.n, dev.fail_after = 0, 1 + 80              # the capabilities, then part of the uploads
    with pytest.raises(Exception) as ei:
        backup.restore(link, arch)
    e = ei.value
    assert dev.log[:2] == ["work<-upload", "save slot1"]
    assert e.written == [1] and e.unclear == 3
    txt = backup.written_text(e, [o["id"] for o in arch.objects])
    assert txt != "Auf dem FM-1 wurde nichts verändert." and "Unklar" in txt             # (was: "Nothing was changed")
    # a partial restore that breaks: the working project could not be put back – said, not hidden
    # a partial restore whose "save into the slot" fails: the working project is put back all the same
    dev.work = bytes([0x77]) * 4264
    dev.log.clear()
    dev.fail_after, dev.fail_cmd = None, 7
    with pytest.raises(Exception) as ei:
        backup.restore_selected(link, arch, [3])
    assert ei.value.unclear == 3 and ei.value.written == [] and dev.work == bytes([0x77]) * 4264
    # … and when the device is gone, the error says that the working project is in an unknown state
    dev.fail_cmd = None
    dev.n, dev.fail_after = 0, dev.n_keep + 10     # (the working project was read, the slot is on its way)
    with pytest.raises(Exception) as ei:
        backup.restore_selected(link, arch, [3])
    assert ei.value.unclear == 0


# ------------------------------------------------------------------------------ CD-09: devices.json ---

GOOD_PROFILES = {"profiles": [{"id": "abc", "name": "Stage FM-1", "serial": "S1", "firmware": "melodee",
                               "firmware_history": [{"firmware": "melodee"}], "firmware_choice": {"FM-1_900": "sloop"}},
                              {"id": "def", "name": "Studio"}]}


@pytest.fixture
def notices(monkeypatch):
    monkeypatch.setattr(config, "extra_notices", [])
    return config.extra_notices


@pytest.mark.parametrize("content", [json.dumps(GOOD_PROFILES)[:40], "", "[]", "null", '{"profiles": null}', "5",
                                     "￾ not json"])
def test_damaged_devices_file_is_put_aside_and_the_previous_state_loaded(tmp_path, notices, content):
    p = tmp_path / "devices.json"
    p.write_text(json.dumps(GOOD_PROFILES))
    st = devices.ProfileStore(p)
    st.profiles["abc"].notes = "x"
    st.save()                                      # (keeps the previous state as devices.json.bak)
    assert json.loads((tmp_path / "devices.json.bak").read_text()) == GOOD_PROFILES
    p.write_text(content)
    st = devices.ProfileStore(p)                   # (was: 0 profiles silently, or a crash at program start)
    assert sorted(st.profiles) == ["abc", "def"] and st.profiles["abc"].name == "Stage FM-1"
    aside = [x for x in tmp_path.iterdir() if ".kaputt-" in x.name]
    assert len(aside) == 1 and aside[0].read_text() == content
    assert len(notices) == 1 and aside[0].name in str(notices[0]) and "beschädigt" in str(notices[0])
    assert "beschädigt" in config.problems_text()
    st.create("New")                               # the damaged file is never taken as the "previous state"
    assert sorted(json.loads((tmp_path / "devices.json.bak").read_text())) == ["profiles"]
    assert len(json.loads(p.read_text())["profiles"]) == 3


def test_damaged_devices_file_without_a_previous_state(tmp_path, notices):
    p = tmp_path / "devices.json"
    p.write_text("{")
    st = devices.ProfileStore(p)
    assert st.profiles == {} and len(notices) == 1 and "Backup-Ordner" in str(notices[0])
    assert [x.name for x in tmp_path.iterdir() if ".kaputt-" in x.name]
    assert devices.ProfileStore(tmp_path / "none.json").profiles == {} and len(notices) == 1    # no file: no notice


def test_unusable_profile_entries_are_left_out_one_by_one(tmp_path, notices):
    p = tmp_path / "devices.json"
    p.write_text(json.dumps({"profiles": [
        {"name": "no id"}, "x", None, {"id": ""}, {"id": 5, "name": "y"},
        {"id": "a", "name": 5, "firmware_choice": None, "firmware_history": None, "firmware": None, "last_seen": "x",
         "color": [], "unknown_future_field": 1},
        {"id": "b", "name": "Good", "last_seen": 3},
        {"id": "b", "name": "Twice"}]}))
    st = devices.ProfileStore(p)                   # (was: KeyError / TypeError at program start)
    assert sorted(st.profiles) == ["a", "b"]
    a = st.profiles["a"]
    assert a.name == "a" and a.firmware == "" and a.firmware_choice == {} and a.firmware_history == []
    assert a.folder_name == "a" and st.name_taken("x") is None and st.profiles["b"].name == "Good"
    assert len(notices) == 1 and "6" in str(notices[0])
    assert not [x for x in tmp_path.iterdir() if ".kaputt-" in x.name]            # (the file itself is fine)


def test_devices_and_identities_are_written_whole_or_not_at_all(tmp_path, monkeypatch, notices):
    from fm1companion import firmware
    p = tmp_path / "devices.json"
    st = devices.ProfileStore(p)
    st.create("One")
    before = p.read_text()
    ident = tmp_path / "identities.json"
    monkeypatch.setattr(firmware, "IDENT_FILE", ident)
    firmware.remember_identity("FM-1_900", "sloop", "v2.8")
    kept = ident.read_text()

    def full(_fd):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(os, "fsync", full)
    with pytest.raises(OSError):
        st.create("Two")
    with pytest.raises(OSError):
        firmware.remember_identity("FM-1_91060", "melodee", "v1.6")
    assert p.read_text() == before and ident.read_text() == kept                    # the old files are untouched
    assert not [x.name for x in tmp_path.iterdir() if x.name.endswith(".tmp")]
    monkeypatch.undo()
    ident.write_text("[]")                         # (a table that is no table: started again, no crash)
    monkeypatch.setattr(firmware, "IDENT_FILE", ident)
    firmware.remember_identity("FM-1_900", "sloop", "v2.8")
    assert json.loads(ident.read_text())["FM-1_900"]["firmware"] == "sloop"


# ------------------------------------------------------------------------------ CD-16: config.json ---

@pytest.fixture
def cfg(tmp_path, monkeypatch):
    for name, value in (("CONFIG_DIR", tmp_path), ("CONFIG_FILE", tmp_path / "config.json"),
                        ("BACKUP_FILE", tmp_path / "config.json.bak"), ("_settings", None), ("load_problems", []),
                        ("broken_file", None), ("read_error", ""), ("_read_exc", None), ("extra_notices", []),
                        ("save_error", ""), ("save_listeners", [])):
        monkeypatch.setattr(config, name, value)
    return tmp_path


@pytest.mark.parametrize("content", ["null", "[]", "5", '"text"'])
def test_config_that_is_json_but_no_object_is_treated_as_damaged(cfg, monkeypatch, content):
    good = json.dumps({"user_root": str(cfg / "MyMusic"), "keep_backups": 7})
    config.BACKUP_FILE.write_text(good)
    config.CONFIG_FILE.write_text(content)
    s = config.load()
    assert s.user_root.endswith("MyMusic") and s.keep_backups == 7                  # (was: the defaults)
    assert config.problems_text()                                                   # (was: no notice for "null")
    assert [x for x in cfg.iterdir() if ".kaputt-" in x.name]
    assert config.save()
    assert "MyMusic" in config.BACKUP_FILE.read_text() or not config.BACKUP_FILE.exists() or \
        json.loads(config.BACKUP_FILE.read_text())["keep_backups"] == 7
    # a broken file is never copied over the good .bak
    config.BACKUP_FILE.write_text(good)
    config.CONFIG_FILE.write_text(content)
    assert config.save(s)
    assert config.BACKUP_FILE.read_text() == good and "MyMusic" in config.CONFIG_FILE.read_text()


def test_config_module_values_of_the_wrong_type_get_their_defaults(cfg):
    s, probs = config.from_dict({"modules": {"daten": {"days": "x", "keep_monthly": "no", "mirror": 5, "delay_s": "soon",
                                                       "hours": True, "on_connect": 1, "periodic": False,
                                                       "skip_day": {"a": "2026-01-01"}}}})
    d = s.modules["daten"]
    assert set(d) == {"periodic", "skip_day"} and len(probs) == 6
    config._settings = s
    full = AB.settings()                           # (config.module fills the defaults in)
    assert full["days"] == 1 and full["delay_s"] == 20 and full["mirror"] == "" and full["on_connect"] is True
    AB.needs_backup(cfg / "none", full["days"])
    s, probs = config.from_dict({"modules": {"daten": dict(AB.DEFAULTS, days=2.5, delay_s=0)}})
    assert not probs and s.modules["daten"]["days"] == 2.5


def test_config_save_never_raises(cfg):
    config.CONFIG_FILE.write_text("{}")
    s = config.load()
    heard = []
    config.save_listeners.append(heard.append)
    s.modules["x"] = {"p": Path("/a")}             # a value JSON cannot hold (was: TypeError out of save())
    assert config.save() is False and config.save_error and heard
    assert config.CONFIG_FILE.read_text() == "{}" and not (cfg / "config.tmp").exists()
    del s.modules["x"]
    assert config.save() is True and config.save_error == ""


# ---------------------------------------------------------- CD-11: the Projects page on other firmwares ---

def test_projects_jiant_slots_and_foreign_projects(tmp_path):
    from fm1companion import projects
    jiant = _fork("JIANT V10.60", backup.JIANT_IDS, samples=0, no_put=())
    slots, _live = projects.list_slots(link_for(jiant)[0])
    assert [(s.slot, s.oid) for s in slots] == [(1, 2), (2, 3), (3, 4), (4, 5)]    # (was: also 64..71 as "Project 5..12")
    mel = projects.load_project(projects.save_project(tmp_path, "mel", rnd(2500, 4), "MELODEE v1.6.0"))
    for version, ids, kw in (("JIANT V10.60", backup.JIANT_IDS, dict(samples=0, no_put=())),
                             ("TESSERA v2.0", list(range(30)), dict(samples=0, no_put=())),
                             ("DUELL 0.2.1 beta", [0, 1, 2, 3, 4, 5, 8, *range(13, 27), 32, 33, 34], {})):
        dev = _fork(version, ids, **kw)
        link, t = link_for(dev)
        dev.log.clear()
        with pytest.raises(backup.BackupError, match="nichts geschrieben"):
            projects.import_to_slot(link, mel, 2, device_firmware=dev.version)     # (was: sent to the device)
        assert dev.log == [] and 67 not in cmds(t)
        own = projects.load_project(projects.save_project(tmp_path, "own", rnd(900, 5), version))
        projects.import_to_slot(link, own, 2, device_firmware=dev.version)         # its own project: as before
        assert dev.log == [3]
    link, t = link_for(jiant)
    own = projects.load_project(projects.save_project(tmp_path, "j", rnd(900, 5), "JIANT V10.60"))
    with pytest.raises(backup.BackupError):
        projects.import_to_slot(link, own, 12, device_firmware=jiant.version)      # (object 71: another song's section)
    # nothing changes for Melodee and SLOOP
    assert projects.foreign_reason("MELODEE v1.6.0", "MELODEE v1.5.0") == ""
    assert projects.foreign_reason("", "FELUCCA SLOOP 2.8.3") == "" and projects.foreign_reason("x", "") == ""
    assert projects.foreign_reason("FELUCCA SLOOP 2.8.3", "FELUCCA KARMA 3.1") == ""


def test_project_library_survives_odd_files(tmp_path):
    from fm1companion import projects
    good = projects.save_project(tmp_path, "good", rnd(100, 1), "MELODEE v1.6.0")
    for i, content in enumerate(("[]", "null", "5", '{"format":"fm1companion-project","object":{}}',
                                 '{"format":"fm1companion-project","object":5}', '{"name": 5, "firmware": []}',
                                 '{"format":"fm1companion-project","object":{"data":"!!","size":1,"crc":1}}')):
        p = tmp_path / f"odd{i}.fm1proj"
        p.write_text(content)
        with pytest.raises(backup.BackupError):
            projects.load_project(p)
    lib = projects.library(tmp_path)               # (was: AttributeError in the page for "[]")
    assert good in [pf.path for pf in lib]
    for pf in lib:
        assert isinstance(pf.name, str) and isinstance(pf.meta.get("firmware", ""), str)


# ------------------------------------- CD-14: a restore never skips the device's own name (INFO) for its rules ---

def test_restore_reads_info_when_the_protocol_came_from_a_hint():
    dx = sloop_dev("FELUCCA sloopDX 4.0.1")
    dx_arch = backup.read(backup.capture(link_for(dx)[0], ""))
    # a SLOOP device whose first INFO answer gets lost, profile says "sloop": the sloopDX backup must still be refused
    dev = sloop_dev("FELUCCA SLOOP 2.8.3")
    link, t = link_for(dev)
    orig, n = dev.handle, {"n": 0}

    def handle(data, lose=3):
        if len(data) > 4 and data[4] == 1 and n["n"] < lose:
            n["n"] += 1
            return None
        return orig(data)
    dev.handle = handle
    dev.log.clear()
    with pytest.raises(backup.BackupError, match="falschen Stellen"):
        backup.restore(link, dx_arch, firmware_key="sloop")
    assert dev.log == []
    # INFO never answers: refused with a clear message, nothing written
    dev = sloop_dev("FELUCCA SLOOP 2.8.3")
    link, t = link_for(dev)
    orig2 = dev.handle
    dev.handle = lambda d: None if len(d) > 4 and d[4] == 1 else orig2(d)
    dev.log.clear()
    s_arch = backup.read(backup.capture(link_for(sloop_dev("FELUCCA SLOOP 2.8.3"))[0], ""))
    with pytest.raises(backup.BackupError, match="welche Firmware"):
        backup.restore(link, s_arch, firmware_key="sloop")
    assert dev.log == [] and 36 not in cmds(t)
    # the hint "melodee" used to skip INFO: a Tessera device behind it still refuses a Melodee backup
    from test_backup_matrix import MEL57
    mel_arch = backup.read(backup.capture(link_for(_fork("MELODEE v1.6.0", MEL57, samples=0, no_put=()))[0],
                                          "MELODEE v1.6.0"))
    tess = _fork("TESSERA v2.0", list(range(30)), samples=0, no_put=())
    link, t = link_for(tess)
    tess.log.clear()
    with pytest.raises(backup.BackupError):
        backup.restore(link, mel_arch, firmware_key="melodee")
    assert tess.log == []
