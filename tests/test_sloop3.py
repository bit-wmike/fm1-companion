# SPDX-License-Identifier: GPL-3.0-only
"""SLOOP 3 (isod89/sloop3-fm1): recognition, no foreign commands, its backups, the bundled editor, m-bootloader's MIDI
MODE (read only). The device is tests/fake_sloop3.py, which replays what SLOOP 3's PC twin answered
(tests/fixtures/sloop3/twin-3.3.7.json); with SLOOP3_TWIN_CMD (a command that speaks hex lines, e.g. a bridge to the
twin built from SLOOP 3's sources) the round trips run against that instead."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import random
import sys
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fm1companion import backup, backup_inspect, firmware, fwfamily, protocol, sims, sloop3 as S3, sloop3bk as BK  # noqa: E402
from fm1companion.flash import mboot  # noqa: E402
from fm1companion.transport import Link, SubprocessTransport  # noqa: E402
from fake_mboot import FakeMboot  # noqa: E402

FIX = json.loads((ROOT / "tests" / "fixtures" / "sloop3" / "twin-3.3.7.json").read_text("utf-8"))
TWIN = os.environ.get("SLOOP3_TWIN_CMD", "")


@pytest.fixture
def dev(tmp_path):
    """-> (link, received): a fake FM-1 with SLOOP 3; received() = every message it got"""
    log = tmp_path / "seen.log"
    argv = TWIN.split() if TWIN else [sys.executable, str(ROOT / "tests" / "fake_sloop3.py"), "--log", str(log)]
    t = SubprocessTransport(argv)
    assert t.wait_started(60)
    link = Link(t)
    yield link, (lambda: [bytes.fromhex(x) for x in log.read_text().split()] if log.exists() else [])
    link.close()


# ------------------------------------------------------------ the protocol ---

def test_frames_are_the_editor_protocols():
    # app/edproto.h: F0 7D 53 33, the command, the bytes packed 8 to 7 (LSB first), F7
    assert S3.frame(S3.Cmd.INFO) == [0xF0, 0x7D, 0x53, 0x33, 1, 0xF7]
    for n in (0, 1, 6, 7, 8, 13, 384):
        raw = bytes(random.Random(n).randrange(256) for _ in range(n))
        f = S3.frame(5, raw)
        assert all(b < 0x80 for b in f[1:-1]) and len(f) == 6 + (n * 8 + 6) // 7
        cmd, back = S3.unframe(f)
        assert cmd == 5 and back[:n] == raw and len(back) - n <= 1      # (a last partial byte may be left over)
    assert S3.unframe([0xF0, 0x7D, 0x46, 0x4C, 1, 0xF7]) is None         # Felucca's header is not SLOOP 3's
    assert protocol.unframe(S3.frame(1)) is None                         # … and the other way round
    # the same bytes as the bundled web editor sent to the PC twin (the fixture's requests)
    assert bytes(S3.frame(S3.Cmd.NAMES, bytes([0, 0, 0]))).hex() in FIX["replay"]
    assert bytes(S3.frame(S3.Cmd.LIVE, bytes(4))).hex() in FIX["replay"]
    assert all(S3.unframe(bytes.fromhex(v)) is not None for v in FIX["replay"].values())


def test_recorded_info_parses():
    q = next(k for k in FIX["replay"] if bytes.fromhex(k)[4] == 1)
    cmd, raw = S3.unframe(bytes.fromhex(FIX["replay"][q]))
    info = S3.parse_info(raw)
    assert cmd == 1 and info.version == "3.3.7" and info.proto == 2 and info.tracks == 5 and info.values == 48
    assert info.tag == "v3.3.7" and info.label == "SLOOP 3 3.3.7" and S3.version_checked(info.version)
    assert not S3.version_checked("3.4.0")
    with pytest.raises(S3.Sloop3Error):
        S3.parse_info(b"\x02\x05")


def test_recognised_by_its_own_info_and_never_as_sloop2(dev):
    link, seen = dev
    info = S3.probe(link)
    assert info is not None and info.version == "3.3.7"
    # the firmware key: its own, whatever the profile remembered and whatever "sloop" is found in the text
    assert firmware.guess_firmware("FM-1_900", info.label, S3.KEY) == ("sloop3", "v3.3.7")
    assert firmware.guess_firmware("", "SLOOP 3 3.3.7") == ("sloop3", "v3.3.7")
    assert firmware.guess_firmware("", "FELUCCA SLOOP 2.8.5")[0] == "sloop"
    sp = firmware.spec("sloop3")
    assert sp is not None and sp.name == "SLOOP 3" and sp.protocol == "sloop3" and not sp.asset and not sp.editor
    assert "beta" in sp.notes and firmware.spec("sloop").key == "sloop"
    fam = fwfamily.by_key("sloop3")
    assert fam is not None and fam.backup == "own" and "sloop3" not in fwfamily.sloop_keys()
    assert "sloop3" not in backup.SLOOP_PROTOCOL
    # a Felucca INFO text never names SLOOP 3 (it answers none), and SLOOP 2's stays SLOOP 2
    assert protocol.Info("FELUCCA SLOOP 2.8.5").firmware_name == "sloop"
    rel = firmware.fetch_releases(sp)
    assert [r.tag for r in rel] == ["v3.3.7", "v3.3.6"] and all(r.prerelease and not r.assets for r in rel)


def test_it_answers_no_foreign_command_and_none_is_needed(dev):
    """what the app asks every unknown device (Felucca INFO, SLOOP 2's and Felucca's backup LIST, Hortator's INFO, the
    update handshake) – the PC twin left each unanswered (fixture "silent"); the fake does the same"""
    link, seen = dev
    got = []
    link.add_listener(lambda d, way: got.append(bytes(d)) if way == "in" else None)
    for hexmsg in FIX["silent"]:
        link.send_midi(list(bytes.fromhex(hexmsg)), force=True)
    assert S3.probe(link) is not None                 # (it is alive – and that answer is the only one)
    assert [g[:5].hex() for g in got] == ["f07d533301"]
    assert {"f07d464c01f7", "f07d464c22f7", "f07d464c41f7"} <= set(FIX["silent"])


def test_backup_sends_only_sloop3s_read_commands(dev):
    link, seen = dev
    f = backup.capture(link, "SLOOP 3 3.3.7", companion={"firmware": "sloop3"})
    assert f["format"] == "sloop3-backup"
    if TWIN:
        return
    cmds = set()
    for m in seen():
        assert m[:4] == bytes([0xF0, 0x7D, 0x53, 0x33]), m.hex()          # nothing of Felucca's or SLOOP 2's
        raw = S3.unframe(m)[1]
        cmds.add((m[4], raw[0] if m[4] in (13, 14) and raw else None))
    assert cmds == {(1, None), (5, None), (13, 0), (14, 0), (14, 1), (19, None)}      # all of them only read


def test_felucca_paths_refuse_sloop3_without_asking(dev):
    link, seen = dev
    with pytest.raises(backup.BackupError) as e:
        backup.protocol(link, "sloop3")
    assert "SLOOP 3" in str(e.value)
    with pytest.raises(backup.BackupError):
        backup.device_inventory(link, "sloop3")
    fel = backup.read({"format": "felucca-backup", "version": 1, "firmware": "FELUCCA v1.8.1",
                       "objects": [{"id": i, "size": 1, "crc": zlib.crc32(b"\0") & 0xFFFFFFFF,
                                    "data": base64.b64encode(b"\0").decode()} for i in (0, 1)]})
    with pytest.raises(backup.BackupError):
        backup.restore(link, fel, firmware_key="sloop3")
    assert seen() == []                               # not one byte went to the device


# -------------------------------------------------------------- backups ---

def _file(link) -> dict:
    return BK.capture(link, companion={"firmware": "sloop3", "device": "T"})


def test_backup_file_and_registration(dev, tmp_path):
    link, _seen = dev
    f = _file(link)
    m = BK.objects(f)
    assert set(m) == set(BK.IDS) and m[BK.NOW] and m[BK.SOUNDS][:4] == b"S3US"
    assert m[BK.FM6][:4] == b"S3F6" and len(m[BK.FM6]) == S3.FM6_LEN and m[BK.SYN][:4] == b"S3SY"
    assert f["samples_not_included"] and len(f["samples_not_included"]) == 4
    a = backup.read(json.loads(backup.dumps(f)))
    assert a.sloop3 and not a.sloop and not a.fm1open and a.project_objects() == []
    p = tmp_path / "b.json"
    backup.write_file(p, f)
    from fm1companion import autobackup as AB
    meta = AB.read_meta(p, cached=False)
    assert meta.fmt == "sloop3-backup" and meta.fw_key == "sloop3" and meta.objects == 6 and meta.projects == 1
    rep = backup_inspect.inspect(p)
    assert rep.firmware_key == "sloop3" and not rep.restorable and any("USR1" in w for w in rep.warnings)
    # never mistaken for SLOOP 2's backup, never restored through the Felucca family's code
    assert backup.match_reason(a, backup.SLOOP) and backup.match_reason(a, backup.FELUCCA)
    for fn in (lambda: backup.restore(None, a), lambda: backup.restore_selected(None, a, [0])):
        with pytest.raises(backup.BackupError) as e:
            fn()
        assert "SLOOP 3" in str(e.value)
    # the web editor's own backup file out of it: "S3BK", 1, the count, then kind, place, length, bytes
    s3b = BK.to_s3b(f)
    assert s3b[:5] == b"S3BK\x01" and s3b[5] == 1 and s3b[8:10] == bytes([1, 0])
    assert S3.u32(s3b, 10) == len(m[BK.SOUNDS]) and s3b[14:] == m[BK.SOUNDS]


def test_damaged_backup_files_are_refused(dev):
    link, _seen = dev
    f = _file(link)
    for change in (lambda g: g["objects"][1].update(crc=1), lambda g: g["objects"].pop(2) and g["objects"].pop(0),
                   lambda g: g["objects"][3].update(data=base64.b64encode(b"x" * S3.FM6_LEN).decode(),
                                                    crc=zlib.crc32(b"x" * S3.FM6_LEN) & 0xFFFFFFFF),
                   lambda g: g.update(version=2), lambda g: g["objects"].append(dict(g["objects"][0])),
                   lambda g: g["objects"][0].update(id=9)):
        g = json.loads(json.dumps(f))
        change(g)
        with pytest.raises(BK.Sloop3BackupError):
            BK.objects(g)
        with pytest.raises(backup.BackupError):
            backup.read(g)


def test_round_trip_backup_change_restore(dev):
    """back up, change everything a restore writes (sounds, FM6 bank, SYN kits, project), restore, back up again: the
    FM-1 holds the first backup's contents again"""
    link, seen = dev
    with S3.Client(link) as c:
        # an FM-1 with a bank and kits of its own, and a saved project
        fm6 = bytearray(c.bank_read(S3.FM6))
        fm6[16:16 + 128] = bytes((i * 7) & 127 for i in range(128))
        fm6[8:12] = S3.w32(1)
        fm6[12:16] = S3.w32(zlib.crc32(bytes(fm6[16:])) & 0xFFFFFFFF)
        c.bank_write(S3.FM6, bytes(fm6))
        c.wait_idle()
        syn = bytearray(c.bank_read(S3.SYN))
        syn[12:16] = S3.w32(zlib.crc32(bytes(syn[16:])) & 0xFFFFFFFF)
        c.bank_write(S3.SYN, bytes(syn))
        c.wait_idle()
        c.write_file(S3.KIND_PROJECT, c.read_file(S3.KIND_NOW))
        c.wait_idle()
    first = _file(link)
    assert BK.bank_sources(first) == (2, 2) and BK.restore_plan(first) == [BK.SOUNDS, BK.FM6, BK.SYN, BK.PROJECT]
    m1 = BK.objects(first)
    with S3.Client(link) as c:                        # now change the FM-1
        fm6[16 + 5] ^= 1
        fm6[12:16] = S3.w32(zlib.crc32(bytes(fm6[16:])) & 0xFFFFFFFF)
        c.bank_write(S3.FM6, bytes(fm6))
        c.wait_idle()
        snd = bytearray(m1[BK.SOUNDS])
        if not TWIN:                                  # (the twin checks the sounds' own format: left as they are)
            snd[-1] ^= 1
            c.write_file(S3.KIND_SOUNDS, bytes(snd))
            c.wait_idle()
    changed = BK.objects(_file(link))
    assert changed[BK.FM6] != m1[BK.FM6]
    assert BK.restore(link, first) == [BK.SOUNDS, BK.FM6, BK.SYN, BK.PROJECT]
    m2 = BK.objects(_file(link))
    for i in (BK.SOUNDS, BK.SYN, BK.PROJECT):
        assert m2[i] == m1[i], BK.title(i)
    assert m2[BK.FM6][16:] == m1[BK.FM6][16:]
    if not TWIN:
        assert all(m[:4] == bytes([0xF0, 0x7D, 0x53, 0x33]) for m in seen())


def test_restore_plan_rules():
    def mk(proj: bytes, now: bytes, fm6_src=0, syn_src=0):
        fm6 = bytearray(S3.FM6_LEN)
        fm6[:8] = b"S3F6" + bytes([1, 0, 27, 0])
        fm6[12:16] = S3.w32(zlib.crc32(bytes(fm6[16:])) & 0xFFFFFFFF)
        syn = bytearray(S3.SYN_LEN)
        syn[:8] = b"S3SY" + bytes([1, 0, 4, 0])
        syn[12:16] = S3.w32(zlib.crc32(bytes(syn[16:])) & 0xFFFFFFFF)
        parts = {0: proj, 1: b"S3US" + bytes(60), 2: now, 3: bytes(fm6), 4: bytes(syn), 5: b"\1"}
        return {"format": "sloop3-backup", "version": 1, "firmware": "SLOOP 3 3.3.7",
                "bank": {"fm6_source": fm6_src, "syn_source": syn_src}, "objects": [BK._obj(i, parts[i]) for i in BK.IDS]}
    # the banks only if the FM-1 had some of its own (or SLOOP 2.8's); the project saved, else the one at that moment
    assert BK.restore_plan(mk(b"P" * 20, b"N" * 20)) == [BK.SOUNDS, BK.PROJECT]
    assert BK.restore_plan(mk(b"", b"N" * 20, 1, 2)) == [BK.SOUNDS, BK.FM6, BK.SYN, BK.NOW]
    assert BK.restore_plan(mk(b"P" * 20, b"N" * 20), "now") == [BK.SOUNDS, BK.NOW]
    assert BK.restore_plan(mk(b"P" * 20, b"N" * 20), "none") == [BK.SOUNDS]
    assert BK.SETTINGS not in BK.RESTORABLE and BK.RESTORABLE[-1] == BK.PROJECT


def test_restore_stops_when_the_device_is_no_sloop3(tmp_path, dev):
    link, _seen = dev
    f = _file(link)
    link.close()
    quiet = Link(SubprocessTransport([sys.executable, "-c", "import sys\nprint('# up', flush=True)\n"
                                                                 "for l in sys.stdin: pass"]))
    try:
        with pytest.raises(BK.Sloop3BackupError) as e:
            BK.restore(quiet, f)
        assert getattr(e.value, "written", None) == []
    finally:
        quiet.close()


# ------------------------------------------------------ the bundled editor ---

def test_bundled_editor_is_upstreams_file_with_notice_and_manifest():
    d = S3.EDITOR_DIR
    m = S3.editor_manifest()
    assert set(m["files"]) == {p.name for p in d.iterdir()} - {"manifest.json"}
    assert S3.editor_problems() == []
    for name, info in m["files"].items():
        raw = (d / name).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == info["sha256"] and len(raw) == info["size"], name
    # the upstream file of the tag (web/editor.html, v3.3.7-beta = commit 915b54f; the same file in v3.3.6-beta)
    assert m["files"]["editor.html"]["sha256"] == "a60c4ea84dc4b9d9ea36c33b24be8ac4d351764031c9368ac41d6b31b6b5ff53"
    assert m["commit"] == "915b54f6780564cb486b4de8a518aabbea1503a0" and m["firmware_versions"] == ["3.3.6", "3.3.7"]
    assert tuple(m["firmware_versions"]) == S3.CHECKED_VERSIONS
    assert S3.editor_fits("3.3.7") and S3.editor_fits("v3.3.6") and not S3.editor_fits("3.4.0")
    notice = (d / "NOTICE").read_text("utf-8")
    for needle in (m["commit"], "isod89", "Corresponding Source", "Written offer", "compat-chromium102.js", "GPL-3.0"):
        assert needle in notice, needle
    assert "GNU GENERAL PUBLIC LICENSE" in (d / "LICENSE").read_text("utf-8")
    html = (d / "editor.html").read_text("utf-8")
    assert "<title>SLOOP 3 editor</title>" in html and "fm1c" not in html.lower()     # nothing of ours inside it
    # it never sends the M-VAVE update / boot keys, and finds its port by the name "SLOOP 3"
    assert "0x22, 0x24, 0x35" not in html and "/SLOOP 3/i.test(p.name)" in html
    # not shown as a simulator, but found by the source tarball tool (GPL: corresponding source)
    assert all(s.key != "sloop3-editor" for s in sims.bundled())
    sys.path.insert(0, str(ROOT / "tools"))
    import make_source_tarballs as T
    assert ("sloop3-fm1", m["commit"]) in T.wanted()


def test_editor_uses_nothing_newer_than_chromium_102_but_color_mix():
    """Qt 6.4 (Raspberry Pi OS Bookworm) is Chromium 102. The one newer thing in SLOOP 3's editor is CSS color-mix()
    (Chromium 111), once – a pad's tint, which compat-chromium102.js gives back. A new editor version that brings
    another such feature must be looked at: this list is what was checked for."""
    html = (S3.EDITOR_DIR / "editor.html").read_text("utf-8")
    css = html[html.index("<style>"):html.index("</style>")]
    js = html[html.index("<script>"):html.rindex("</script>")]
    assert css.count("color-mix(") == 1 and ".pad.has{background:color-mix(in srgb,var(--c,#888) 30%,var(--panel2))}" in css
    for newer in ("@container", "container-type", ":has(", "dvh", "svh", "lvh", "@layer", "@scope", "oklch", "oklab",
                  "light-dark(", "text-wrap", "subgrid", "@starting-style", "anchor-name", "popover", "&:hover", "& ."):
        assert newer not in css, newer
    for newer in (".toSorted(", ".toReversed(", ".toSpliced(", ".with(", "Object.groupBy", "Map.groupBy",
                  "Array.fromAsync", "Promise.withResolvers", ".findLastIndex(", "structuredClone", ".isWellFormed(",
                  "showOpenFilePicker", "showSaveFilePicker", "import(", "navigator.wakeLock", "Iterator.", ".at(-",
                  "setHTMLUnsafe", "getHTML(", "Temporal.", "using "):
        assert newer not in js, newer
    compat = (S3.EDITOR_DIR / "compat-chromium102.js").read_text("utf-8")
    assert "NOT part of SLOOP 3" in compat and "linear-gradient(#1f1b2bb3,#1f1b2bb3),var(--c,#888)" in compat
    assert "--panel2:#1f1b2b" in css                   # (the colour the addition relies on)


def test_editor_gate_lets_only_sloop3s_protocol_out():
    ok = sims.sloop3_editor_out_allowed
    assert ok(S3.frame(S3.Cmd.INFO)) and ok(S3.frame(S3.Cmd.WDATA, bytes(range(200))))
    for bad in ([0xF0, 0x22, 0x24, 0x35, 0x7F, 0xF7], [0xF0, 0x22, 0x24, 0x35, 0x7D, 0xF7],        # update / boot keys
                [0xF0, 0x7D, 0x46, 0x4C, 67, 0xF7],                                               # Felucca's BACKUP_PUT
                [0x90, 60, 100], [0xF0, 0x7D, 0x53, 0x33, 1], [0xF0, 0x7D, 0x53, 0x33, 1, 0xF7, 0xF0, 0x22, 0x24, 0x35, 0x7F, 0xF7],
                [0xF0, 0x7D, 0x53, 0x33, 1, 0x90, 0xF7], [], [0xF0, 0x00, 0x59, 0x21, 0xF7]):
        assert not ok(bad), bad
    assert not sims.builtin_editor_out_allowed([0xF0, 0x22, 0x24, 0x35, 0x7F, 0xF7])


# -------------------------------------------- m-bootloader's MIDI MODE ---

FLASH = bytes(random.Random(7).randrange(256) for _ in range(0x100000))


def test_mboot_identify_and_revision():
    t = FakeMboot(FLASH, jedec=mboot.JEDEC_NEW, version="2.4-395e6cc")
    h = mboot.Host(t)
    info = h.identify()
    assert info is not None and info.version == "2.4-395e6cc" and info.capacity == 0x100000 and info.kind == 1
    assert info.revision == "new" and info.chip == "UCUN UC25HQ80" and info.uid == "a0a1a2a3a4a5a6a7"
    assert mboot.Host(FakeMboot(FLASH, jedec=mboot.JEDEC_OLD)).identify().revision == "old"
    assert mboot.Host(FakeMboot(FLASH, jedec=0x123456)).identify().revision == "unknown"
    assert S3.revision_of_jedec(0xB36014) == "new" and S3.revision_of_jedec(0x856014) == "old"
    assert t.commands == [0x12] and not (t.writes or t.installs or t.restarts or t.downloads)


def test_mboot_reads_nothing_from_what_is_not_m_bootloader():
    """a Felucca-family update loader answers the same handshake name; it does not answer 0x12 – nothing is read"""
    t = FakeMboot(FLASH, answer_0x12=False)
    h = mboot.Host(t)
    assert h.identify(tries=1, timeout=0.2) is None
    for fn in (lambda: h.read(0, 16), lambda: h.crc(0, 16), lambda: h.read_flash()):
        with pytest.raises(mboot.MbootError):
            fn()
    assert t.commands == [0x12]                       # the question, and then nothing
    # only the port MIDI MODE shows is asked at all
    assert mboot.is_candidate("FM-1 Update", "1209:0002") and mboot.is_candidate("FM-1 Update")
    for name, uid in (("Melodee FM-1 Update", "1209:0002"), ("Felucca Update", "1209:0002"), ("SLOOP Update", ""),
                      ("FM-1 Update", "4d4a:4155"), ("FM-1 SLOOP 3", "1209:0001"), ("ota-FM-1_015", "")):
        assert not mboot.is_candidate(name, uid), name


def test_mboot_never_frames_a_writing_command():
    for cmd in range(256):
        if cmd in (0x12, 0x23, 0x24):
            assert mboot._frame(cmd)[0] == 0xF0
        else:
            with pytest.raises(mboot.MbootError):
                mboot._frame(cmd)
    src = (ROOT / "fm1companion" / "flash" / "mboot.py").read_text("utf-8")
    import re
    code = re.sub(r"#.*", "", src.split('"""', 2)[2])   # (the code itself, without the module text and comments)
    for key in ("0x7F, 0xF7", "0x7E, 0xF7", "0x7D, 0xF7", "0x21", "0x22", "0x25", "0x30", "0x35"):
        assert key not in code, key                     # no install, restart, download mode, erase, write, run


def test_mboot_flash_backup_verified_against_the_chips_crc(tmp_path):
    t = FakeMboot(FLASH)
    h = mboot.Host(t)
    info = h.identify()
    steps = []
    image = h.read_flash(progress=lambda a, b: steps.append((a, b)))
    assert image == FLASH and steps[-1] == (0x100000, 0x100000)
    assert set(t.commands) == {0x12, 0x23, 0x24} and not (t.writes or t.installs or t.restarts or t.downloads)
    p = mboot.save_backup(tmp_path, "FM-1 A", info, image)
    side = mboot.check_backup(p)
    assert p.read_bytes() == FLASH and p.suffix == ".bin" and side["sha256"] == hashlib.sha256(FLASH).hexdigest()
    assert side["flash_chip"]["jedec"] == "0x856014" and "M-Flasher" in side["restore"]
    assert "key" not in json.dumps(side).lower().replace("monkey", "")        # the chip key is not written down
    p.write_bytes(FLASH[:-1] + b"\0")
    with pytest.raises(mboot.MbootError):
        mboot.check_backup(p)
    # the sidecar is no backup of the Backups page (it must not appear there as a damaged one)
    from fm1companion import autobackup as AB
    assert AB.read_meta(p.with_suffix(".json"), cached=False, damaged=True) is None


def test_mboot_a_wrong_byte_on_the_way_is_never_saved():
    t = FakeMboot(FLASH, corrupt_at=0x4321)
    h = mboot.Host(t)
    h.identify()
    with pytest.raises(mboot.MbootError):
        h.read_flash()
    with pytest.raises(mboot.MbootError):               # cancelled: no file either
        mboot.Host(FakeMboot(FLASH)).read_flash()
    h2 = mboot.Host(FakeMboot(FLASH))
    h2.identify()
    with pytest.raises(mboot.MbootError):
        h2.read_flash(abort=lambda: True)


def test_revision_adapter_without_and_with_a_hardware_module(monkeypatch):
    class P:
        pass
    p = P()
    assert S3.revision_of(p) == "unknown"             # no module, no field: unknown – never "old"
    p.hardware_revision = "new"
    assert S3.revision_of(p) in ("new", "unknown")    # (a hardware module, once there, decides; the field else)
    import types
    mod = types.ModuleType("fm1companion.hardware")
    mod.revision = lambda prof: "new"
    monkeypatch.setitem(sys.modules, "fm1companion.hardware", mod)
    import fm1companion
    monkeypatch.setattr(fm1companion, "hardware", mod, raising=False)
    assert S3.revision_of(P()) == "new"
    mod.revision = lambda prof: 1 / 0
    assert S3.revision_of(P()) == "unknown"
