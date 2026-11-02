# SPDX-License-Identifier: GPL-3.0-only
"""Daten: Backups erklären, vergleichen, gezielt zurückspielen, Aufbewahrung, Spiegel, Geräte-Kopie.

  python3 -m pytest tests/test_data.py -q
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import os
import struct
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fm1companion import backup, backup_inspect as BI                 # noqa: E402
from fm1companion.demo_device import FakeFM1                          # noqa: E402
from fm1companion.protocol import crc32                               # noqa: E402
from fm1companion.transport import Link                               # noqa: E402
from test_core import LocalTransport                                  # noqa: E402


def rec(name: str, engine: int = 3, fav: bool = False, ver: int = 4, fill: int = 64) -> bytes:
    r = bytearray(206)
    r[0] = 0xA5 | (2 if fav else 0)
    r[1], r[2], r[3] = ver, engine, 91
    r[4:4 + len(name)] = name.encode()
    for i in range(91):
        r[16 + i] = fill
    return bytes(r)


def bank(records: dict[int, bytes]) -> bytes:
    out = bytearray(struct.pack("<IHH", BI.UPB2, 206, 18))
    for i in range(18):
        out += records.get(i, bytes(206))
    return bytes(out)


def melodee(presets=True):
    dev = FakeFM1()
    if presets:
        dev.objs[6] = bank({0: rec("BASS ONE"), 4: rec("SID LEAD", fav=True), 17: rec("PAD", 12)})
        dev.objs[21] = bank({2: rec("DX PIANO", 12)})        # Bank 4 -> U073..U090
        dev.objs[28] = bank({0: rec("LAST", 10), 17: rec("WIPE-PENDING", 0)})  # Bank 21: nur U379..U384
    return dev, Link(LocalTransport(dev))


def test_inspect_melodee_backup():
    dev, link = melodee()
    rep = BI.inspect(backup.capture(link, "Melodee 1.6.0-rc.7", companion={"device": "A", "firmware": "melodee"}))
    assert rep.known and rep.restorable and len(rep.objects) == 57
    assert rep.obj(0).title == "Laufende Musik" and rep.obj(1).title == "Einstellungen"
    assert rep.obj(66).title == "Projekt 7" and rep.obj(66).used and rep.obj(3).slot == 2 and not rep.obj(3).used
    assert rep.obj(6).title == "Preset-Bank U001–U018 (allgemein)"
    assert rep.obj(21).title == "Preset-Bank U073–U090 (früher FM6-Voices)"
    assert rep.obj(28).title == "Preset-Bank U379–U384 (Zusatzplätze)"
    labels = {p.label: p for p in rep.used_presets}
    assert set(labels) == {"U001", "U005", "U018", "U075", "U379"}       # WIPE-PENDING liegt außerhalb
    assert labels["U005"].favorite and labels["U005"].engine_name == "SID" and labels["U018"].engine_name == "FM6"
    assert "3 von 18 belegt, 1 Favoriten" in rep.obj(6).detail
    assert len(rep.presets) == 384
    assert rep.firmware_key == "melodee" and "2 Projekte" in rep.summary()


def test_inspect_generic_and_damaged():
    # 12er-Inventar (Felucca/Skiff): generisch, kein Absturz bei Zufallsdaten
    dev = FakeFM1(samples=3)
    rep = BI.inspect(backup.capture(Link(LocalTransport(dev)), "FELUCCA v1.0"))
    assert len(rep.objects) == 12 and rep.obj(6).title == "User-Presets U01–U16"
    assert rep.obj(6).format == "unbekanntes Format" and rep.obj(32).group == "Samples"
    # 29er-Inventar (shared library ohne Projektpool)
    dev = FakeFM1(ids=list(range(29)))
    dev.objs[6] = bank({1: rec("X")})
    rep = BI.inspect(backup.capture(Link(LocalTransport(dev)), "Melodee 1.5"))
    assert rep.used_presets[0].label == "U002"
    f = backup.capture(Link(LocalTransport(dev)), "x")
    f["objects"][2]["crc"] ^= 1
    with pytest.raises(backup.BackupError):
        BI.inspect(f)


def test_inspect_sloop_file():
    data = b"\x01\x02\x03"
    f = {"format": "sloop-backup", "version": 1, "firmware": "SLOOP 2.8.3", "date": "2026-10-01T10:00:00Z",
         "objects": [{"id": 0, "len": 3, "crc": crc32(data), "data": base64.b64encode(data).decode()},
                     {"id": 1, "len": 0, "crc": 0, "data": ""},
                     {"id": 2, "len": 3, "crc": crc32(data), "data": base64.b64encode(data).decode()},
                     {"id": 9, "len": 0, "crc": 0, "data": ""}]}
    rep = BI.inspect(json.dumps(f))
    # seit ws/sloop: SLOOP-Backups lassen sich auch zurückspielen (Befehle 34–36)
    assert rep.restorable and rep.firmware_key == "sloop"
    assert rep.obj(2).title == "Song-Section A" and rep.obj(9).title == "SYN-Drum-Kits"
    assert BI.plan_restore(rep, [2], []).ok
    assert not BI.plan_restore(rep, [1], []).ok           # leere Einstellungen nimmt SLOOP nicht an


def test_old_formats_decoded():
    nfm = bytearray(2060)
    struct.pack_into("<IHHI", nfm, 0, BI.NFM1, 1, 16, 0b101)
    nfm[12 + 118:12 + 128] = b"E.PIANO 1 "
    v = BI.describe(21, bytes(nfm), crc32(bytes(nfm)), 23)
    assert v.title == "FM6-Voices F001–F016" and [p.label for p in v.presets if p.used] == ["F001", "F003"]
    assert v.presets[0].name == "E.PIANO 1"
    s = bytearray(604)
    struct.pack_into("<I", s, 0, BI.PER5)
    struct.pack_into("<I", s, 576, 7)
    st = BI.decode_settings(bytes(s))
    assert st["Projekt beim Einschalten"] == "Platz 7" and st["Vorlage gespeichert"] == "nein"


def test_diff_and_summary():
    dev, link = melodee()
    a = BI.inspect(backup.capture(link, "fw"))
    dev.objs[6] = bank({0: rec("BASS TWO"), 4: rec("SID LEAD", fav=False), 17: rec("PAD", 12, fill=70),
                        9: rec("NEW ONE", 10)})
    dev.objs[68] = b"P" * 900                                      # Projekt 9 neu
    dev.objs[66] = b""                                             # Projekt 7 geleert
    b = BI.inspect(backup.capture(link, "fw"))
    d = BI.diff(a, b)
    kinds = {p.label: (p.kind, p.fields) for p in d.presets}
    assert kinds["U001"] == ("geändert", ["Name"])
    assert kinds["U005"] == ("geändert", ["Favorit"])
    assert kinds["U018"] == ("geändert", ["Klang"])
    assert kinds["U010"][0] == "neu"
    s = d.summary()
    assert "3 Presets geändert" in s and "1 Preset neu" in s and "Projekt 9 neu" in s and "Projekt 7 geleert" in s
    assert "Einstellungen gleich" in s
    assert BI.diff(a, a).identical


def test_selective_restore_order_and_skip():
    dev, link = melodee()
    arch = backup.read(backup.capture(link, "fw"))
    rep = BI.inspect(arch)
    dev.objs[6] = bank({0: rec("CHANGED")})
    dev.objs[66] = b"z" * 50
    dev.objs[0] = b"live" * 10
    dev.objs[1] = bytes(604)
    cur = BI.inspect(backup.capture(link, "fw"))
    man = backup.list_objects(link)
    plan = BI.plan_restore(rep, [0, 66, 6, 1, 7, 8], man, cur)
    assert plan.ok and plan.ids() == [6, 66, 1, 0]                 # live music last, settings before
    assert [s.id for s in plan.skipped] == [7]                     # schon identisch
    assert "BASS ONE" in plan.items[0].detail and "laufende Musik" in plan.html()
    dev.log.clear()
    written = backup.restore_selected(link, arch, plan.ids(), expect=plan.expect)
    assert written == [6, 66, 1, 0] and dev.log == [6, 66, 1, 0]
    assert dev.objs[66] == arch.objects[[o["id"] for o in arch.objects].index(66)]["bytes"]


def test_selective_restore_refuses_changed_device_and_bad_format():
    dev, link = melodee()
    arch = backup.read(backup.capture(link, "fw"))
    rep = BI.inspect(arch)
    dev.objs[66] = b"other"
    plan = BI.plan_restore(rep, [66], backup.list_objects(link))
    dev.objs[66] = b"changed again"
    dev.log.clear()
    with pytest.raises(backup.BackupError, match="verändert"):
        backup.restore_selected(link, arch, plan.ids(), expect=plan.expect)
    assert dev.log == []
    # Gerät lehnt in der Vorabprüfung ab -> nichts geschrieben
    orig = dev._handle

    def refuse(cmd, a):
        if cmd == 67 and a[0] == 0 and a[1] == 0:
            return [0, 0, 1]
        return orig(cmd, a)
    dev._handle = refuse
    with pytest.raises(backup.BackupError, match="nichts geschrieben"):
        backup.restore_selected(link, arch, [66, 0])
    assert not [x for x in dev.log if isinstance(x, int)]


def test_plan_errors_unknown_object_and_old_banks():
    dev, link = melodee()
    rep = BI.inspect(backup.capture(link, "fw"))
    small = FakeFM1(ids=list(range(29)))
    man = backup.list_objects(Link(LocalTransport(small)))
    plan = BI.plan_restore(rep, [66], man)                          # 1.6-Backup (57 Objekte) auf 29er-Firmware
    assert not plan.ok and "Melodee 1.6" in plan.errors[0] and "nichts geschrieben" in plan.errors[0]
    man12 = backup.list_objects(Link(LocalTransport(FakeFM1(samples=3))))
    small.objs[20] = b"\x07" * 500
    rep29 = BI.inspect(backup.capture(Link(LocalTransport(small)), "Melodee 1.5.1"))
    plan = BI.plan_restore(rep29, [20], man12)                      # Objekt 20 gibt es dort nicht
    assert not plan.ok and "kennt das Objekt nicht" in plan.errors[0]
    # Bank in altem Format -> alle alten Banken zusammen
    old = FakeFM1(ids=list(range(28)))
    old.objs[6] = b"\x01" * 3816
    old.objs[7] = b"\x02" * 3816
    orep = BI.inspect(backup.capture(Link(LocalTransport(old)), "old"))
    plan = BI.plan_restore(orep, [6], backup.list_objects(link))
    assert set(plan.ids()) >= {6, 7} and plan.clear_extension and plan.notes


def test_compatibility():
    dev, link = melodee()
    rep = BI.inspect(backup.capture(link, "Melodee 1.6.0-rc.7", companion={"firmware": "melodee"}))
    c = BI.compatibility(rep, "sloop", "SLOOP 2.8.3", list(range(9)) + [32, 33, 34])
    assert not c.ok and "SLOOP" in c.reason and "Melodee" in c.reason and c.possible
    c = BI.compatibility(rep, "melodee", "Melodee 1.6.0-rc.7", [o.id for o in backup.list_objects(link)])
    assert c.ok and 66 in c.allowed_ids and 8 not in c.allowed_ids
    c = BI.compatibility(rep, "melodee", "Melodee 1.5.0", list(range(29)))   # 1.6-Backup auf 1.5: vorher ablehnen
    assert not c.ok and "Melodee 1.6" in c.reason and "nichts geschrieben" in c.reason and c.possible
    c = BI.compatibility(rep, "melodee", "MELODEE v1.5.1", None)              # Seite „Backups“: nur die Version
    assert not c.ok and "MELODEE v1.5.1" in c.reason
    assert BI.compatibility(rep, "melodee", "MELODEE v1.6.0", None).ok
    small = FakeFM1(ids=list(range(29)))
    rep29 = BI.inspect(backup.capture(Link(LocalTransport(small)), "Melodee 1.5.0", companion={"firmware": "melodee"}))
    c = BI.compatibility(rep29, "melodee", "Melodee 1.4.0", list(range(23)))
    assert c.ok and "neuer" in c.reason
    cmp = BI.compare_devices(rep, None, "", "SLOOP")
    assert cmp.rows[0] == ("Firmware", "Melodee 1.6.0-rc.7", "SLOOP") and "BASS ONE" in cmp.only_a


# ------------------------------------------------------- Aufbewahrung / Spiegel ---

def _fake_backup(folder: Path, name: str, created: dt.datetime, kind="auto", fw="melodee", pinned=False):
    from fm1companion import autobackup as AB
    dev = FakeFM1()
    f = backup.capture(Link(LocalTransport(dev)), "v")
    f["created"] = created.isoformat().replace("+00:00", "Z")
    f["companion"] = {"firmware": fw, "kind": kind, "pinned": pinned, "device": "A"}
    p = folder / name
    backup.write_file(p, f)
    return p


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    from fm1companion import autobackup as AB, config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(AB, "_cache", None)
    return tmp_path


def test_retention_keeps_pinned_monthly_and_per_firmware(isolated):
    from fm1companion import autobackup as AB
    folder = isolated / "B"
    now = dt.datetime(2026, 10, 30, 12, tzinfo=dt.timezone.utc)
    paths = {}
    for i in range(8):                                   # 8 automatische, täglich, Oktober
        paths[i] = _fake_backup(folder, f"a{i}_auto.json", now - dt.timedelta(days=i))
    paths["sept"] = _fake_backup(folder, "sept_auto.json", now - dt.timedelta(days=40))
    paths["sept_old"] = _fake_backup(folder, "sept2_auto.json", now - dt.timedelta(days=45))
    paths["pin"] = _fake_backup(folder, "pin_auto.json", now - dt.timedelta(days=50), pinned=True)
    paths["sloop"] = _fake_backup(folder, "s_vor-Update.json", now - dt.timedelta(days=60), kind="vor-Update", fw="sloop")
    paths["man"] = _fake_backup(folder, "manual.json", now - dt.timedelta(days=90), kind="manuell")
    gone = AB.prune(folder, 3)
    left = {p.name for p in folder.glob("*.json")}
    assert {"a0_auto.json", "a1_auto.json", "a2_auto.json"} <= left
    assert "sept_auto.json" in left and "sept2_auto.json" not in left          # je Monat das neueste
    assert "pin_auto.json" in left and "manual.json" in left and "s_vor-Update.json" in left
    assert "a5_auto.json" not in left and len(gone) == 6


def test_meta_labels_pin_keep_mtime(isolated):
    from fm1companion import autobackup as AB
    p = _fake_backup(isolated, "x.json", dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc), kind="manuell")
    mt = p.stat().st_mtime_ns
    AB.set_meta(p, labels=["Live-Set Oktober"], pinned=True, note="vor Firmware-Wechsel")
    m = AB.read_meta(p)
    assert m.pinned and m.labels == ["Live-Set Oktober"] and m.note == "vor Firmware-Wechsel"
    assert p.stat().st_mtime_ns == mt and backup.load_file(p)          # Datei bleibt gültig
    assert AB.needs_backup(isolated, 1) and not AB.needs_backup(isolated, 10000)


def test_mirror_copy_and_verify(isolated, monkeypatch):
    from fm1companion import autobackup as AB
    root = isolated / "Backups"
    p = _fake_backup(root / "Gerät A", "m.json", dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc))
    nas = isolated / "nas"
    with pytest.raises(OSError):
        AB.mirror_file(p, root, nas)                       # nicht eingehängt
    nas.mkdir()
    d = AB.mirror_file(p, root, nas)
    assert d == nas / "Gerät A" / "m.json" and d.read_bytes() == p.read_bytes()
    ok, bad, _ = AB.mirror_all(root, nas)
    assert (ok, bad) == (1, 0)
    # kaputte Kopie wird erkannt
    import shutil as sh
    real = sh.copyfile
    monkeypatch.setattr(AB.shutil, "copyfile", lambda a, b: (real(a, b), open(b, "ab").write(b"x")))
    d.unlink()
    with pytest.raises(OSError, match="Prüfung"):
        AB.mirror_file(p, root, nas)
    assert not d.exists()


def test_companion_labels_firmware():
    from fm1companion import autobackup as AB
    from fm1companion.devices import Profile
    prof = Profile("id1", "Studio", firmware="melodee", firmware_version="v1.6.0-rc.7", serial="S1")
    c = AB.companion_block(prof, "melodee", "Melodee 1.6.0-rc.7", kind="vor-Update")
    assert c["firmware"] == "melodee" and c["firmware_version"] == "v1.6.0-rc.7" and c["kind"] == "vor-Update"


def test_zip_roundtrip_and_export_parts(isolated):
    from fm1companion import autobackup as AB
    from fm1companion.ui.pages.backup_browser import export_parts
    root = isolated / "Backups"
    p1 = _fake_backup(root / "A", "one.json", dt.datetime(2026, 2, 1, tzinfo=dt.timezone.utc), pinned=True)
    p2 = _fake_backup(root / "B", "two.json", dt.datetime(2026, 3, 1, tzinfo=dt.timezone.utc))
    z = isolated / "set.zip"
    assert AB.export_zip([p1, p2], z, root) == 2
    other = isolated / "Other"
    got, skipped = AB.import_zip(z, other)
    assert sorted(x.relative_to(other).as_posix() for x in got) == ["A/one.json", "B/two.json"] and not skipped
    assert AB.read_meta(other / "A" / "one.json").pinned
    got, skipped = AB.import_zip(z, other)
    assert not got and len(skipped) == 2
    dev, link = melodee()
    rep = BI.inspect(backup.capture(link, "fw", companion={"device": "A"}))
    n, names = export_parts(rep, [66, 1, 3], {"U001", "U005"}, isolated / "exp", isolated / "lib")
    assert n == 3 and len(names) == 3
    names = sorted(x.name for x in (isolated / "exp").iterdir())
    assert any(x.endswith(".fm1proj") for x in names) and any("Presets_2" in x for x in names)
    from fm1companion import projects
    assert projects.load_project(next((isolated / "lib").glob("*.fm1proj"))).data == dev.objs[66]


# ------------------------------------------------------------ Abnahme-Runde (p2): Regressionstests ---

def test_set_meta_same_size_is_visible(isolated):
    """Notiz „abc“ → „abd“ (gleiche Größe, gleiche mtime): der Index darf keinen alten Wert liefern."""
    from fm1companion import autobackup as AB
    p = _fake_backup(isolated / "Backups" / "A", "x.json", dt.datetime(2026, 2, 1, tzinfo=dt.timezone.utc))
    AB.set_meta(p, note="abc")
    assert AB.read_meta(p).note == "abc"
    st = p.stat()
    AB.set_meta(p, note="abd")
    m = AB.read_meta(p)
    assert m.note == "abd"
    assert p.stat().st_mtime_ns == st.st_mtime_ns          # Datum bleibt
    assert not list(p.parent.glob("*.tmp")) and not list(p.parent.glob(".*.tmp"))


def test_atomic_write_disk_full_and_interrupt(isolated, monkeypatch):
    """Platte voll / Prozess stirbt mitten im Schreiben: alte Datei bleibt, keine halbe Datei, keine .tmp-Reste."""
    p = isolated / "Backups" / "b.json"
    backup.write_text_atomic(p, '{"a": 1}')
    assert json.loads(p.read_text()) == {"a": 1}

    def full(fd):                                   # fsync der .tmp-Datei: Platte voll
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(os, "fsync", full)
    with pytest.raises(OSError):
        backup.write_text_atomic(p, '{"a": 2}')
    monkeypatch.undo()
    assert json.loads(p.read_text()) == {"a": 1}
    assert [x.name for x in p.parent.iterdir()] == ["b.json"]

    def killed(fd):                                 # Prozess wird beendet
        raise KeyboardInterrupt
    monkeypatch.setattr(os, "fsync", killed)
    with pytest.raises(KeyboardInterrupt):
        backup.write_text_atomic(p, '{"a": 3}')
    monkeypatch.undo()
    assert json.loads(p.read_text()) == {"a": 1} and [x.name for x in p.parent.iterdir()] == ["b.json"]
    # Reste eines abgebrochenen Vorgangs (älter als 10 Minuten) werden beim nächsten Schreiben entfernt
    stale = p.parent / ".b.json.abc.tmp"
    stale.write_text("x")
    os.utime(stale, (time.time() - 3600, time.time() - 3600))
    backup.write_text_atomic(p, '{"a": 4}')
    assert not stale.exists() and json.loads(p.read_text()) == {"a": 4}
    # Backup-Listen sehen .tmp-Dateien nie
    from fm1companion import autobackup as AB
    assert all(m.path.suffix == ".json" for m in AB.list_backups(isolated / "Backups", include_damaged=True))


def test_zip_import_broken_files_german(isolated):
    from fm1companion import autobackup as AB
    import zipfile
    bad = isolated / "kaputt.zip"
    bad.write_bytes(b"das ist kein zip")
    with pytest.raises(backup.BackupError) as e:
        AB.import_zip(bad, isolated / "B")
    assert "keine lesbare Zip-Datei" in str(e.value)
    with zipfile.ZipFile(isolated / "m.zip", "w") as z:
        z.writestr("manifest.json", "{kaputt")
    with pytest.raises(backup.BackupError) as e:
        AB.import_zip(isolated / "m.zip", isolated / "B")
    assert "Inhaltsverzeichnis" in str(e.value) and "beschädigt" in str(e.value)
    with zipfile.ZipFile(isolated / "o.zip", "w") as z:
        z.writestr("readme.txt", "hallo")
    with pytest.raises(backup.BackupError) as e:
        AB.import_zip(isolated / "o.zip", isolated / "B")
    assert "manifest.json" in str(e.value)
    assert not (isolated / "B").exists()


def test_comparable_refuses_other_firmware():
    dev, link = melodee()
    a = BI.inspect(backup.capture(link, "Melodee 1.6.0-rc.7", companion={"firmware": "melodee"}))
    data = b"\x01\x02\x03"
    f = {"format": "sloop-backup", "version": 1, "firmware": "FELUCCA SLOOP 2.8.3", "date": "2026-10-01T10:00:00Z",
         "objects": [{"id": 0, "len": 3, "crc": crc32(data), "data": base64.b64encode(data).decode()},
                     {"id": 1, "len": 3, "crc": crc32(data), "data": base64.b64encode(data).decode()}]}
    b = BI.inspect(json.dumps(f))
    why = BI.comparable(a, b)
    assert "Nicht vergleichbar" in why and "SLOOP" in why
    assert BI.comparable(a, a) == ""
    c = BI.inspect(backup.capture(link, "Skiff 1.0", companion={"firmware": "skiff"}))
    assert "Skiff" in BI.comparable(a, c) and "Melodee" in BI.comparable(a, c)


def test_object_titles_in_errors_and_written_state():
    """Fehlertexte nennen Objekte in Alltagssprache; nach einem Abbruch steht fest, was schon geschrieben ist."""
    assert backup.object_title(21) == "Preset-Bank U073–U090 (früher FM6-Voices)"
    assert backup.object_title(66) == "Projekt 7" and backup.object_title(0) == "Laufende Musik"
    assert backup.object_title(2, sloop=True) == "Song-Section A"
    dev, link = melodee()
    arch = backup.read(backup.capture(link, "fw"))
    dev.objs[6] = bank({0: rec("CHANGED")})
    dev.objs[66] = b"z" * 50
    dev.objs[0] = b"live" * 10
    plan = BI.plan_restore(BI.inspect(arch), [0, 66, 6], backup.list_objects(link))
    assert plan.ids() == [6, 66, 0]
    calls = {"n": 0}
    real = dev._handle

    def flaky(cmd, a):
        if cmd == 67 and a[0] == 2 and a[1] == 66:         # Commit von Projekt 7 schlägt fehl (Flash-Fehler)
            dev.staged = None
            return [2, 66, 4]
        return real(cmd, a)
    dev._handle = flaky
    with pytest.raises(backup.BackupError) as e:
        backup.restore_selected(link, arch, plan.ids(), expect=plan.expect)
    assert e.value.written == [6]
    txt = backup.written_text(e.value, plan.ids())
    assert "Preset-Bank U001–U018" in txt and "Projekt 7" in txt and "Laufende Musik" in txt
    assert "Noch im alten Zustand" in txt and dev.objs[0] == b"live" * 10
    dev._handle = real
    # ohne Abbruch: nichts geschrieben
    e2 = backup.BackupError("x")
    assert backup.written_text(e2, [6]) == "Auf dem FM-1 wurde nichts verändert."
    # unbekanntes Objekt: Name statt Nummer, nichts geschrieben
    dev2 = FakeFM1(ids=list(range(29)))
    link2 = Link(LocalTransport(dev2))
    with pytest.raises(backup.BackupError) as e3:
        backup.restore_selected(link2, arch, [66])                 # 1.6-Backup auf 29er-Firmware: vorher abgelehnt
    assert "Melodee 1.6" in str(e3.value) and "nichts geschrieben" in str(e3.value)


def test_sloop_project_in_project_view_is_explained():
    from fm1companion import projectfmt as PF
    prj = PF.decode(b"6NUF" + bytes(2000), "FELUCCA SLOOP 2.8.3")
    assert not prj.ok and prj.format == "SLOOP-Projekt (FUN6)"
    assert "SLOOP-Web-Editor" in prj.warnings[0] and "Melodee-Format" in prj.warnings[0]
    prj = PF.decode(b"\x00\x01\x02\x03" + bytes(100), "ORBIT 1.0")
    assert not prj.ok and "andere Firmware" in prj.format


def test_project_names_and_firmware_family(isolated):
    from fm1companion import projects
    assert projects._safe("Mein/Track: Live <2026>") == "Mein_Track_ Live _2026_"
    assert len(projects._safe("A" * 300)) == 80 and projects._safe("...") == "Projekt" and projects._safe("") == "Projekt"
    p = projects.save_project(isolated / "lib", "A" * 300, b"HPJ1" + bytes(40), "Melodee 1.6.0-rc.7")
    assert p.exists() and projects.load_project(p).name == "A" * 300
    p2 = projects.save_project(isolated / "lib", "A" * 300, b"HPJ1" + bytes(40), "Melodee 1.6.0-rc.7")
    assert p2 != p and p2.name.endswith(" (2).fm1proj")
    assert projects.family("FELUCCA SLOOP 2.8.3") == "sloop" and projects.family("Melodee 1.6.0-rc.7") == "melodee"
    assert projects.family_of_data(b"6NUF" + bytes(10)) == "sloop" and projects.family_of_data(b"HPJ1") == "melodee"
    # .fm1proj von SLOOP auf ein Melodee-Gerät: verweigert, bevor das Gerät angefasst wird
    dev, link = melodee()
    pf = projects.ProjectFile(p, {**projects.load_project(p).meta, "firmware": "FELUCCA SLOOP 2.8.3",
                                  "object": {"size": 14, "crc": crc32(b"6NUF" + bytes(10)),
                                             "data": base64.b64encode(b"6NUF" + bytes(10)).decode()}})
    dev.log.clear()
    with pytest.raises(backup.BackupError) as e:
        projects.import_to_slot(link, pf, 3, device_firmware="Melodee 1.6.0-rc.7")
    assert "SLOOP" in str(e.value) and "Melodee" in str(e.value) and dev.log == []


def test_mirror_errors_are_german(isolated):
    from fm1companion import autobackup as AB
    p = _fake_backup(isolated / "Backups" / "A", "x.json", dt.datetime(2026, 2, 1, tzinfo=dt.timezone.utc))
    with pytest.raises(OSError) as e:
        AB.mirror_file(p, isolated / "Backups", isolated / "nas-nicht-da")
    assert "eingehängt" in str(e.value)
    assert "voll" in AB.os_error_text(OSError(28, "No space left on device"))
    assert "Schreibrechte" in AB.os_error_text(PermissionError(13, "Permission denied", "/x"))


def test_melodee16_backup_refused_on_older_firmware_before_writing():
    """Melodee 1.6: ältere Firmware lehnt Backups von 1.6 ab – die Suite sagt das vorher (deutsch), statt eines
    Gerätefehlers („älteres Format“), und schreibt nichts. Umgekehrt (1.5 -> 1.6) bleibt erlaubt."""
    from fm1companion import melodee16 as M
    dev, link = melodee()
    arch = backup.read(backup.capture(link, "MELODEE v1.6.0", companion={"firmware": "melodee",
                                                                         "firmware_version": "v1.6.0"}))
    old = FakeFM1(version="Melodee 1.5.1", ids=list(range(29)))
    before = dict(old.objs)
    with pytest.raises(backup.BackupError) as e:
        backup.restore(Link(LocalTransport(old)), arch, firmware_key="melodee")
    assert "Melodee 1.6 (v1.6.0)" in str(e.value) and "nichts geschrieben" in str(e.value)
    assert old.objs == before and not getattr(e.value, "written", None)
    with pytest.raises(backup.BackupError):
        backup.restore_selected(Link(LocalTransport(old)), arch, [1])
    assert old.objs == before
    # 1.5-Backup (29 Objekte) auf 1.6: wie bisher
    a15 = backup.read(backup.capture(Link(LocalTransport(old)), "MELODEE v1.5.1", companion={"firmware": "melodee"}))
    backup.restore(link, a15, firmware_key="melodee")
    # Erkennung
    assert M.backup_is_16(n_objects=57) and M.backup_is_16("MELODEE v1.6.0-rc.8")
    assert not M.backup_is_16("FELUCCA v1.6.1", {"firmware": "felucca"}, 12)
    assert M.device_before_16("MELODEE v1.5.1") and not M.device_before_16("MELODEE v1.6.0-rc.7")
    assert not M.device_before_16("FELUCCA v1.0") and M.device_before_16(n_objects=29)
