# SPDX-License-Identifier: GPL-3.0-only
"""SLOOP on Melodee's level, part B: live values for SLOOP 3 and SLOOP 2 on the FM-1 page, samples out of a flash copy,
SLOOP 3 backups (.s3b in and out, look inside, compare, automatic backup off by default), flash backups listed.

Everything towards a device is read-only; the tests check that only the read-only requests go out."""
from __future__ import annotations

import json
import math
import os
import struct
import sys
import time
import wave
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import fake_sloop3 as F                                                                    # noqa: E402
from fm1companion import backup, backup_inspect as BI, flashdump as FD, health, sloop3 as S3  # noqa: E402
from fm1companion import sloop3bk as B                                                     # noqa: E402
from fm1companion.demo_device import FakeFM1                                               # noqa: E402
from fm1companion.protocol import parse_info                                               # noqa: E402
from fm1companion.transport import Link                                                    # noqa: E402
from fm1companion.vendor import sampleio as sio                                            # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "sloop3" / "twin-3.3.7.json"
_APP = None                                   # (the QApplication, kept alive)


@pytest.fixture(autouse=True)
def english():
    from fm1companion import i18n
    i18n.set_language("en")                      # these tests read the English texts
    yield
    i18n.set_language(None)


def s3link(fake=None):
    fake = fake or F.Fake()
    t = F.InProcess(fake)
    return fake, Link(t), t


def felucca(version):
    from test_sloop_backup import LocalTransport
    dev = FakeFM1(0, version)
    t = LocalTransport(dev)
    return dev, Link(t), t


# ------------------------------------------------------------------ B4: live ---

def test_sloop3_live_reads_the_twins_own_answer():
    """the answer SLOOP 3's PC twin gave to LIVE op 0 (recorded fixture) parses as the editor's parseLive reads it"""
    fx = json.loads(FIX.read_text("utf-8"))
    key = next(k for k in fx["replay"] if bytes.fromhex(k)[4] == S3.Cmd.LIVE)
    assert bytes.fromhex(key) == bytes(S3.frame(S3.Cmd.LIVE, bytes([0, 0, 0, 0]))), "the twin was asked op 0"
    cmd, raw = S3.unframe(bytes.fromhex(fx["replay"][key]))
    a = health.parse_sloop3_live(raw)
    assert cmd == S3.Cmd.LIVE and a["source"] == "sloop3" and len(a["tracks"]) == 5
    assert 30 <= a["bpm"] <= 300 and 0 <= a["cpu_percent"] <= 100 and a["voices"] is not None
    assert [t["name"] for t in a["tracks"]] == ["1", "2", "3", "4", "DR"]     # (as the FM-1 shows them)
    with pytest.raises(ValueError):
        health.parse_sloop3_live(raw[:100])


def test_sloop3_live_from_the_fake_only_op_0_goes_out():
    fake, link, t = s3link()
    fake.live.update(playing=True, cpu=37, peak=55, voices=6, bar=3, beat=2, bpm10=1205)
    fake.live["meters"] = [10, 20, 30, 40, 50]           # wire order: index 3 is the drums
    fake.live["mute"] = [0, 1, 0, 0, 0]
    a = health.sloop3_live(link)
    assert (a["playing"], a["cpu_percent"], a["cpu_peak"], a["voices"], a["bar"], a["beat"], a["bpm"]) == \
        (True, 37, 55, 6, 3, 2, 120.5)
    assert [(x["name"], x["meter"]) for x in a["tracks"]] == [("1", 10), ("2", 20), ("3", 30), ("4", 50), ("DR", 40)]
    assert a["tracks"][1]["mute"] and a["tracks"][0]["step"] == 3
    assert fake.live_ops == [0]
    assert t.seen == [bytes(S3.frame(S3.Cmd.LIVE, bytes([0, 0, 0, 0])))]


def test_sloop2_live_77_only_to_sloop_27_and_later():
    dev, link, t = felucca("SLOOP 2.8.3")
    info = parse_info(link.request(1, []))
    assert health.sloop2_live_supported(info)
    dev.playing, dev.live_cpu = True, 23
    a = health.sloop2_live(link, info)
    assert a["source"] == "sloop" and a["playing"] and a["cpu_percent"] == 23 and a["bpm"] == 120.0
    assert [x["name"] for x in a["tracks"]] == ["1", "2", "3", "DR"] and a["tracks"][0]["meter"] == 96
    assert dev.live_ops == [0] and t.sent[-1][4:7] == [77, 0, 0]
    # Melodee, KARMA (a SLOOP fork), an older SLOOP without protocol 12: nothing is sent
    for version in ("Melodee 1.6.0", "FELUCCA KARMA 3.1"):
        dev, link, t = felucca(version)
        info = parse_info(link.request(1, []))
        n = len(t.sent)
        assert not health.sloop2_live_supported(info) and health.sloop2_live(link, info) is None
        assert len(t.sent) == n
    old = parse_info(list(b"FELUCCA SLOOP 2.6.0\0") + [1, 71, 50, 64, 63] + list(b"ANALOG\0") + [4, 11])
    assert not health.sloop2_live_supported(old) and not health.sloop2_live_supported(None)


def test_sloop2_live_layout_is_editor_lives():
    """byte by byte as firmware editor_live.c writes it (and the editor's parse[CMD.LIVE] reads it)"""
    bpm = 133 + 8192
    a = [0, 1, 0, bpm & 127, bpm >> 7, 2, 0, 0, 0, 0b0101, 0b0010, 0, 1, 0, 42, 0, 4, 0, 4, 1]
    a += [5, 16, 70, 6, 32, 0, 7, 16, 80, 8, 64, 90] + [0, 0] + [0x48] * 16
    assert len(a) == 50
    s = health.parse_sloop2_live(a)
    assert s["bpm"] == 133 and s["cpu_percent"] == 42 and s["playing"]
    assert [x["mute"] for x in s["tracks"]] == [True, False, True, False]
    assert [x["solo"] for x in s["tracks"]] == [False, True, False, False]
    assert [(x["step"], x["steps"], x["meter"]) for x in s["tracks"]] == [(5, 16, 70), (6, 32, 0), (7, 16, 80),
                                                                          (8, 64, 90)]
    with pytest.raises(ValueError):
        health.parse_sloop2_live(a[:31])


class _Sig:
    def connect(self, fn):
        pass


class _Ctx:
    def __init__(self, link, sloop3=None, info=None):
        self.link, self.sloop3, self.info = link, sloop3, info
        self.changed = _Sig()
        self.hold = False
        from fm1companion.devices import PortInfo
        self.port = PortInfo("in", "out", "FM-1 SLOOP 3", None, usb_id="1209:0001")
        self.profile = None
        self.firmware_key = "sloop3" if sloop3 else "sloop"
        self.identity = ""

    @property
    def connected(self):
        return self.info is not None

    @property
    def midi_connected(self):
        return not self.link.closed


def _page(ctx):
    pytest.importorskip("PyQt6.QtWidgets")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    global _APP
    _APP = QApplication.instance() or QApplication([])
    from fm1companion.ui.fm1_page import FM1Page
    return FM1Page(ctx)


def _wait(cond, ms=4000):
    from PyQt6.QtWidgets import QApplication
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        QApplication.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def test_fm1_page_shows_sloop3_live_and_waits_while_another_job_holds_the_device():
    fake, link, t = s3link()
    info = S3.Client(link).__enter__().info()
    ctx = _Ctx(link, sloop3=info)
    page = _page(ctx)
    assert page.content.isVisibleTo(page) or not page.none.isVisibleTo(page)
    page.active = True
    page._hold()
    fake.live.update(playing=True, cpu=41, voices=4)
    fake.live["meters"] = [0, 0, 0, 100, 0]
    page._live_tick()
    assert _wait(lambda: "41 %" in page.cpu.format())
    assert "4" in page.f["LStimmen"].text() and page.meters_box.isVisibleTo(page)
    assert page.meters[4].format() == "DR" and page.meters[4].value() == 100
    # a backup (or restore) takes the device: the page asks nothing until it is done
    n = len(t.seen)
    link.hold(settle=False)
    page._live_tick()
    assert len(t.seen) == n and page.live_state.text()
    link.release()
    ctx.hold = True                                   # (an installation)
    page._live_tick()
    assert len(t.seen) == n
    ctx.hold = False
    page._live_tick()
    assert _wait(lambda: len(t.seen) > n)
    assert set(fake.live_ops) == {0}
    # nothing but SLOOP 3's own read-only commands went out
    assert all(m[:4] == bytes([0xF0, *S3.HDR]) for m in t.seen)
    assert {m[4] for m in t.seen} <= {S3.Cmd.INFO, S3.Cmd.LIVE, S3.Cmd.SAMPLE}
    page._release()


def test_fm1_page_sloop2_live():
    dev, link, t = felucca("SLOOP 2.8.3")
    info = parse_info(link.request(1, []))
    ctx = _Ctx(link, info=info)
    page = _page(ctx)
    page.active = True
    page._hold()
    dev.playing, dev.live_cpu = True, 19
    page._live_tick()
    assert _wait(lambda: "19 %" in page.cpu.format())
    assert "120" in page.f["LTransport"].text() and page.f["LStimmen"].text() == "–"
    assert dev.live_ops == [0] and not any(m[4] == 72 for m in t.sent)
    page._release()


# ------------------------------------------------------- B5: samples from flash ---

def _sine(n, f=440, rate=22050, amp=20000):
    return [int(amp * math.sin(2 * math.pi * f * i / rate)) for i in range(n)]


def synthetic_dump(slots: dict) -> bytes:
    """a 1 MiB flash image with FSMP slots written as the SLOOP 3 editor does (header 480 B, data at +512, IMA)"""
    img = bytearray(b"\xFF" * FD.FLASH_SIZE)
    for k, (name, zones) in slots.items():
        h, d = sio.user_slot(name, zones)
        at = FD.SLOT_AT[k]
        img[at:at + len(h)] = h
        img[at + FD.DATA_OFF:at + FD.DATA_OFF + len(d)] = d
    return bytes(img)


def test_slot_addresses_are_sloop3s_and_sloop2s():
    # SLOOP 3 3.4.0 fw/main.c: SLOT_AT[SMP_NUSR] = { 0xA0000u, 0xB4000u, 0xC8000u, 0xE7000u }; SLOOP 2.8 eng_sample.c:
    # SMP_USER_BASE 0xA0000 (USR1..3, 80 KiB each), SMP_USER4_BASE 0xE7000
    assert FD.SLOT_AT == (0xA0000, 0xB4000, 0xC8000, 0xE7000) and FD.SLOT_SIZE == 0x14000
    assert sio.SLOT_SIZE == FD.SLOT_SIZE and sio.SLOT_DATA_OFF == FD.DATA_OFF   # (SLOOP 2's layout, the same)


def test_samples_out_of_a_flash_copy(tmp_path):
    kick, hat = _sine(3000, 60), _sine(800, 3000)
    img = synthetic_dump({0: ("break", [(kick, 36, None, None), (hat, 42, None, None)]), 3: ("pad", [(_sine(5000), 60, 0, 127)])})
    res = FD.extract(img, tmp_path / "out")
    assert [s.used for s in res.slots] == [True, False, False, True]
    assert res.slots[0].name == "BREAK" and len(res.slots[0].zones) == 2 and res.slots[1].problem
    names = sorted(p.name for p in res.files)
    assert names == ["SLOOP 3 USR1 BREAK 01.wav", "SLOOP 3 USR1 BREAK 02.wav", "SLOOP 3 USR4 PAD.wav"]
    with wave.open(str(tmp_path / "out" / "SLOOP 3 USR4 PAD.wav")) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()) == (22050, 1, 2, 5000)
        pcm = struct.unpack("<5000h", w.readframes(5000))
    want = _sine(5000)
    assert max(abs(a - b) for a, b in zip(pcm[400:], want[400:])) < 1500       # (IMA ADPCM: close, not exact)
    sr, x, loop = sio.read_wav(tmp_path / "out" / "SLOOP 3 USR1 BREAK 01.wav")[:3]
    assert sr == 22050 and len(x) == 3000
    # again: nothing is overwritten
    again = FD.extract(img, tmp_path / "out")
    assert all("(2)" in p.name for p in again.files)


def test_a_damaged_or_foreign_slot_is_named_not_extracted(tmp_path):
    img = bytearray(synthetic_dump({1: ("kick", [(_sine(2000), 36, None, None)])}))
    at = FD.SLOT_AT[1] + FD.DATA_OFF + 100
    img[at] ^= 0xFF                                       # (the data no longer matches its CRC-32)
    img[FD.SLOT_AT[2]:FD.SLOT_AT[2] + 8] = b"MELODEE!"    # (something else there)
    res = FD.extract(bytes(img), tmp_path)
    assert not res.files and "checksum" in res.slots[1].problem and res.slots[2].problem
    with pytest.raises(FD.FlashDumpError):
        FD.extract(bytes(img[:0xF0000]), tmp_path)        # (not the whole flash)
    p = tmp_path / "x.bin"
    p.write_bytes(b"\0" * 1000)
    with pytest.raises(FD.FlashDumpError):
        FD.read_image(p)


def test_flash_backups_listed_with_their_check(tmp_path):
    from fm1companion.flash import mboot
    info = mboot.Info(version="2.4-395e6cc", kind=1, key=0, capacity=FD.FLASH_SIZE, jedec=0x856014, uid="00112233")
    img = synthetic_dump({0: ("x", [(_sine(500), 60, None, None)])})
    folder = tmp_path / "Backups" / "Dev" / "Flash"
    p = mboot.save_backup(folder, "Dev", info, img)
    (folder / "m-flasher.bin").write_bytes(img)           # (one without a description: listed, not checked)
    got = FD.list_backups(tmp_path / "Backups")
    assert {b.path.name for b in got} == {p.name, "m-flasher.bin"}
    mine = next(b for b in got if b.path == p)
    assert mine.ok is True and mine.chip == "Puya P25Q80H" and mine.crc32 == f"{zlib.crc32(img) & 0xFFFFFFFF:08x}"
    assert next(b for b in got if b.path.name == "m-flasher.bin").ok is None
    raw = bytearray(img)
    raw[5] ^= 1
    p.write_bytes(bytes(raw))
    assert FD.describe(p).ok is False


# ------------------------------------------------------------ B6: backups ---

def _backup(fake=None):
    fake, link, _t = s3link(fake)
    return B.capture(link)


def test_s3b_round_trip_matches_the_editors_layout():
    f = _backup()
    m = B.objects(f)
    raw = B.to_s3b(f)
    # "S3BK", 1, the count, 0, 0; each: kind, place, length u32, bytes (web/src/files.js backupAll)
    assert raw[:8] == b"S3BK" + bytes([1, 2, 0, 0])
    proj = m[B.PROJECT] or m[B.NOW]
    assert raw[8:14] == bytes([0, 0]) + len(proj).to_bytes(4, "little") and raw[14:14 + len(proj)] == proj
    assert B.s3b_parts(raw) == [(0, 0, proj), (1, 0, m[B.SOUNDS])]
    g = B.from_s3b(raw, name="x.s3b")
    gm = B.objects(g)
    assert gm[B.PROJECT] == proj and gm[B.SOUNDS] == m[B.SOUNDS] and not gm[B.FM6] and not gm[B.SYN]
    assert B.restore_plan(g) == [B.SOUNDS, B.PROJECT]        # (no banks: an .s3b has none)
    assert backup.read(g).sloop3 and BI.inspect(g).warnings
    with pytest.raises(B.Sloop3BackupError):
        B.s3b_parts(b"S3BK" + bytes([1, 1, 0, 0]) + bytes([0, 0, 50, 0, 0, 0]) + b"short")
    with pytest.raises(B.Sloop3BackupError):
        B.s3b_parts(b"XXXX1234")


def test_s3b_of_older_sloop3_with_several_projects():
    """before 3.3.5 SLOOP 3 had 8 project places: its .s3b holds several; one is taken"""
    projs = {k: b"GBPJ" + bytes([10]) + bytes(20) + bytes([k]) for k in (0, 3, 5)}
    raw = bytearray(b"S3BK" + bytes([1, 3, 0, 0]))
    for k, d in projs.items():
        raw += bytes([0, k]) + len(d).to_bytes(4, "little") + d
    assert B.s3b_projects(bytes(raw)) == [0, 3, 5]
    assert B.objects(B.from_s3b(bytes(raw)))[B.PROJECT] == projs[5]          # (as the editor proposes: the last)
    assert B.objects(B.from_s3b(bytes(raw), 3))[B.PROJECT] == projs[3]
    with pytest.raises(B.Sloop3BackupError):
        B.from_s3b(bytes(raw), 7)


def test_to_s3b_takes_the_project_now_when_asked():
    fake = F.Fake()
    fake.saved = fake.now[:-1] + bytes([fake.now[-1] ^ 1])
    f = _backup(fake)
    m = B.objects(f)
    assert m[B.PROJECT] != m[B.NOW]
    assert B.s3b_parts(B.to_s3b(f))[0][2] == m[B.PROJECT]
    assert B.s3b_parts(B.to_s3b(f, "now"))[0][2] == m[B.NOW]


def _with_sound(fake, k, name, engine=0, fav=False):
    s = bytearray(fake.sounds)
    r = 8 + 80 * k
    s[r:r + 80] = bytes([0xA5, engine, 4, 0]) + name.ljust(12, b"\0") + bytes(64)
    if fav and len(s) >= B.US_LEN + 34:
        s[B.US_LEN:B.US_LEN + 4] = b"FV" + (B.FAV_BASE_USER + k).to_bytes(2, "little")
    fake.sounds = bytes(s)


def test_browse_sloop3_backup_and_compare_place_by_place():
    fake = F.Fake()
    a = BI.inspect(_backup(fake))
    _with_sound(fake, 4, b"MYLEAD", engine=0, fav=True)
    b = BI.inspect(_backup(fake))
    _with_sound(fake, 4, b"MYLEAD2", engine=2)
    c = BI.inspect(_backup(fake))
    assert not a.restorable and a.summary().startswith("1 ")
    snd = b.obj(B.SOUNDS)
    assert snd.format == "S3US" and "1 of 32" in snd.detail
    p = next(x for x in b.used_presets)
    assert (p.label, p.name, p.engine_name, p.favorite) == ("S05", "MYLEAD", "ANALOG", True)
    assert b.obj(B.NOW).detail.startswith("SLOOP 3 project") and b.settings.get("KNOB ACCEL") == 1
    d = BI.diff(a, b)
    assert [ch.id for ch in d.objects] == [B.SOUNDS] and d.presets[0].kind == BI.K_NEW
    assert d.summary().startswith(B.title(B.SOUNDS)) and B.title(B.PROJECT) not in d.summary()
    d2 = BI.diff(b, c)
    assert set(d2.presets[0].fields) == {BI.F_NAME, BI.F_ENGINE}
    assert BI.diff(a, a).identical
    other = BI.inspect({"format": "felucca-backup", "version": 1, "firmware": "MELODEE v1.6.0",
                        "objects": [{"id": 0, "size": 1, "crc": zlib.crc32(b"\0"), "data": "AA=="}]})
    assert "SLOOP 3" in BI.comparable(a, other)


def test_fm6_bank_voices_listed():
    fake = F.Fake()
    img = bytearray(fake.fm6)
    voice = bytearray(128)
    voice[118:128] = b"E.PIANO 1 "
    img[16:16 + 128] = voice
    img[8:12] = (1).to_bytes(4, "little")
    img[12:16] = (zlib.crc32(bytes(img[16:])) & 0xFFFFFFFF).to_bytes(4, "little")
    fake.fm6, fake.fm6_src = bytes(img), 2
    rep = BI.inspect(_backup(fake))
    v = [p for p in rep.used_presets if p.prefix == "B"]
    assert [(p.label, p.name, p.engine_name) for p in v] == [("B01", "E.PIANO 1", "FM6")]
    assert "1 of 27" in rep.obj(B.FM6).detail


def test_sloop3_auto_backup_is_off_by_default():
    from fm1companion import autobackup as AB
    assert AB.DEFAULTS["sloop3"] is False
    src = (ROOT / "fm1companion" / "ui" / "backups_page.py").read_text("utf-8")
    assert 'AB.settings().get("sloop3")' in src


def test_sloop3_auto_backup_service(tmp_path, monkeypatch):
    """switched off: a SLOOP 3 unit gets no automatic backup; switched on, it is announced and runs as the others"""
    pytest.importorskip("PyQt6.QtWidgets")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    global _APP
    _APP = QApplication.instance() or QApplication([])
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    from fm1companion import autobackup as AB, config
    from fm1companion.ui import backups_page as BP
    st = dict(AB.DEFAULTS)
    monkeypatch.setattr(AB, "settings", lambda: st)
    fake, link, t = s3link()
    info = S3.Client(link).__enter__().info()

    class Prof:
        id, name, serial, firmware_version, folder_name = "p1", "Dev", "", "v3.4.0", "Dev"

    ctx = _Ctx(link, sloop3=info)
    ctx.profile = Prof()
    ctx.settings = type("S", (), {"backups_dir": tmp_path / "Backups", "keep_backups": 20})()
    ctx.message = type("M", (), {"emit": lambda self, m: None})()
    ctx.window = None
    ctx.is_virtual = False

    def device_folder(base):
        d = base / "Dev"
        d.mkdir(parents=True, exist_ok=True)
        return d
    ctx.device_folder = device_folder
    jobs = []
    ctx.job = lambda *a, **k: jobs.append((a, k))
    svc = BP.AutoBackupService(ctx)
    svc._changed()
    assert svc._pending is None and not jobs and not svc._target_ok()
    st["sloop3"] = True
    st["delay_s"] = 0
    svc._seen = ""
    svc._changed()
    assert svc._target_ok() and len(jobs) == 1, "switched on: it runs (announced first, here without delay)"
    svc.tick.stop()


# --------------------------------------------- the newer unit (owner report 2026-11-19) ---

def _unit(rev, mboot_version=None, jedec=0xB36014):
    from fm1companion.flash import mboot

    class Prof:
        hw_revision = rev
    ctx = type("C", (), {})()
    ctx.profile = Prof()
    ctx.mboot = mboot.Info(mboot_version, 1, 0, FD.FLASH_SIZE, jedec, "00" * 8) if mboot_version else None
    return ctx


def test_flash_copy_on_a_newer_unit_follows_the_m_bootloader_version():
    from fm1companion.ui import sloop3_ui as U
    assert U.flash_copy_refusal(_unit("new", "2.4-395e6cc")) == ""                  # the reported build
    assert U.flash_copy_refusal(_unit("new", "2.5-abcdef0")) == ""                  # a later version
    for v in ("2.0", "1.2", "2.4-other", "?"):                                      # older, another build, unreadable
        assert "M-Flasher" in U.flash_copy_refusal(_unit("new", v)), v
    assert U.flash_copy_refusal(_unit("old", "2.0", 0x856014)) == ""                # the earlier unit: as before
    assert U.flash_copy_refusal(_unit("", "1.3", 0x856014)) == ""
    assert "MIDI MODE" in U.flash_copy_refusal(_unit("new"))                        # not in MIDI MODE: nothing to read


def test_installer_page_on_a_newer_unit():
    from fm1companion.ui import sloop3_ui as U
    level, text = U.revision_text(_unit("new", "2.4-395e6cc"))
    assert level == "ok" and "owner's report" in text and "2026-11-19" in text
    assert U.revision_text(_unit("new", "2.0"))[0] == "bad"
    assert U.revision_text(_unit("new"))[0] == "bad"                                 # (version not known outside MIDI MODE)
    assert U.revision_text(_unit("", "2.4-395e6cc", 0x856014))[0] == "ok"           # the chip says: earlier unit
    assert U.revision_text(type("C", (), {"profile": None, "mboot": None})())[0] == "warn"   # unknown: confirm first


def test_sloop3_340_on_a_newer_unit_and_337_not():
    from fm1companion import hardware as H
    from fm1companion.flash import tested
    assert H.support("sloop3", "3.4.0").status == H.YES and H.support("sloop3", "v3.4.0-beta").status == H.YES
    assert H.support("sloop3", "v3.3.7-beta").status == H.RISKY
    assert "owner report 2026-11-19" in H.source("sloop3")["source"]
    assert tested.SLOOP3_HARDWARE["sloop3_new_chip"][0] is False                    # (not run by this app)
    assert "newer_unit_mboot_sloop3" in tested.OWNER_REPORTS
