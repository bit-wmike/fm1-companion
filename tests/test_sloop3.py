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
    assert S3.version_checked("3.4.0") and not S3.version_checked("3.5.0")
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
    # not in the Felucca family's table at all (fwfamily: firmwares by their Felucca INFO – SLOOP 3 has none)
    assert fwfamily.by_key("sloop3") is None and "sloop3" not in fwfamily.sloop_keys()
    # (a FELUCCA-style INFO text "SLOOP 3 …" would read as SLOOP there: the reason the device layer never builds a
    # protocol.Info for SLOOP 3 – ctx.info stays None, ctx.sloop3 holds its own INFO)
    assert "sloop3" not in backup.SLOOP_PROTOCOL
    # a Felucca INFO text never names SLOOP 3 (it answers none), and SLOOP 2's stays SLOOP 2
    assert protocol.Info("FELUCCA SLOOP 2.8.5").firmware_name == "sloop"
    rel = firmware.fetch_releases(sp)
    assert [r.tag for r in rel] == ["v3.4.0", "v3.3.7", "v3.3.6"] and all(r.prerelease and not r.assets for r in rel)


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

BUNDLES = {  # folder -> (upstream commit, sha256 of web/editor.html at that tag, firmware versions with that file)
    "sloop3": ("915b54f6780564cb486b4de8a518aabbea1503a0",
               "a60c4ea84dc4b9d9ea36c33b24be8ac4d351764031c9368ac41d6b31b6b5ff53", ["3.3.6", "3.3.7"]),
    "sloop3-3.4.0": ("2228313f3abe0ad6c8f28ef0cd9b9d8167c01330",
                     "00f1024b6d60e3d2f0d7e6c022be35a62bf3a1fe6994211dcb08ef917c43f821", ["3.4.0"]),
}


def test_bundled_editors_are_upstreams_files_with_notice_and_manifest():
    assert {d.name for d in S3.editor_dirs()} == set(BUNDLES)
    sys.path.insert(0, str(ROOT / "tools"))
    import make_source_tarballs as T
    listed = []
    for d in S3.editor_dirs():
        commit, sha, versions = BUNDLES[d.name]
        m = json.loads((d / "manifest.json").read_text("utf-8"))
        assert set(m["files"]) == {p.name for p in d.iterdir()} - {"manifest.json"}
        for name, info in m["files"].items():
            raw = (d / name).read_bytes()
            assert hashlib.sha256(raw).hexdigest() == info["sha256"] and len(raw) == info["size"], name
        assert m["files"]["editor.html"]["sha256"] == sha and m["commit"] == commit
        assert m["firmware_versions"] == versions
        listed += versions
        notice = (d / "NOTICE").read_text("utf-8")
        for needle in (commit, "isod89", "Corresponding Source", "Written offer", "compat-chromium102.js", "GPL-3.0"):
            assert needle in notice, needle
        assert "GNU GENERAL PUBLIC LICENSE" in (d / "LICENSE").read_text("utf-8")
        html = (d / "editor.html").read_text("utf-8")
        assert "<title>SLOOP 3 editor</title>" in html and "fm1c" not in html.lower()     # nothing of ours inside it
        # it never sends the M-VAVE update / boot keys, and finds its port by the name "SLOOP 3"
        assert "0x22, 0x24, 0x35" not in html and "/SLOOP 3/i.test(" in html
        # everything it sends: its SysEx frame, note on/off, CC 123 – what the gate lets through (see the gate's test)
        assert html.count("out.send(") == 3 and "out.send(frame(cmd, raw));" in html
        assert "out.send(on ? [0x90 | ch, k & 127, clamp(vel, 1, 127)] : [0x80 | ch, k & 127, 0]);" in html
        assert "out.send([0xB0 | c, 123, 0]);" in html
        assert (commit and ("sloop3-fm1", commit) in T.wanted())     # GPL: the corresponding source of each
    assert tuple(sorted(listed)) == S3.CHECKED_VERSIONS
    # each firmware version gets the editor of its own tag; an unknown newer one the newest editor – and is told so
    assert S3.editor_dir_for("3.3.7").name == "sloop3" and S3.editor_dir_for("v3.3.6").name == "sloop3"
    assert S3.editor_dir_for("3.4.0").name == "sloop3-3.4.0" and S3.editor_served("3.4.0") == "_editors/sloop3-3.4.0"
    assert S3.editor_dir_for("3.5.1").name == "sloop3-3.4.0" and not S3.editor_fits("3.5.1")
    assert S3.editor_dir_for("3.3.9").name == "sloop3" and not S3.editor_fits("3.3.9")
    assert S3.editor_dir_for("3.1.0").name == "sloop3" and S3.editor_fits("3.4.0") and S3.editor_fits("v3.3.6")
    for v in ("3.3.7", "3.4.0", "9.9"):
        assert S3.editor_problems(v) == []
    assert all(s.key != "sloop3-editor" for s in sims.bundled())     # not shown as a simulator


def test_editors_use_nothing_newer_than_chromium_102_but_color_mix():
    """Qt 6.4 (Raspberry Pi OS Bookworm) is Chromium 102. The one newer thing in SLOOP 3's editors is CSS color-mix()
    (Chromium 111), once – a pad's tint, which compat-chromium102.js gives back. A new editor version that brings
    another such feature must be looked at: this list is what was checked for."""
    for d in S3.editor_dirs():
        html = (d / "editor.html").read_text("utf-8")
        css = html[html.index("<style>"):html.index("</style>")]
        js = html[html.index("<script>"):html.rindex("</script>")]
        assert css.count("color-mix(") == 1 and \
            ".pad.has{background:color-mix(in srgb,var(--c,#888) 30%,var(--panel2))}" in css, d.name
        for newer in ("@container", "container-type", ":has(", "dvh", "svh", "lvh", "@layer", "@scope", "oklch", "oklab",
                      "light-dark(", "text-wrap", "subgrid", "@starting-style", "anchor-name", "popover", "&:hover", "& ."):
            assert newer not in css, (d.name, newer)
        for newer in (".toSorted(", ".toReversed(", ".toSpliced(", ".with(", "Object.groupBy", "Map.groupBy",
                      "Array.fromAsync", "Promise.withResolvers", ".findLastIndex(", "structuredClone", ".isWellFormed(",
                      "showOpenFilePicker", "showSaveFilePicker", "import(", "navigator.wakeLock", "Iterator.", ".at(-",
                      "setHTMLUnsafe", "getHTML(", "Temporal.", "using ", ".findLast(", "Object.hasOwn", ".replaceAll(",
                      "??=", "||=", "&&="):
            assert newer not in js, (d.name, newer)
        compat = (d / "compat-chromium102.js").read_text("utf-8")
        assert "NOT part of SLOOP 3" in compat and "linear-gradient(#1f1b2bb3,#1f1b2bb3),var(--c,#888)" in compat
        assert "--panel2:#1f1b2b" in css               # (the colour the addition relies on)


def test_editor_gate_lets_only_sloop3s_protocol_out():
    ok = sims.sloop3_editor_out_allowed
    assert ok(S3.frame(S3.Cmd.INFO)) and ok(S3.frame(S3.Cmd.WDATA, bytes(range(200))))
    for bad in ([0xF0, 0x22, 0x24, 0x35, 0x7F, 0xF7], [0xF0, 0x22, 0x24, 0x35, 0x7D, 0xF7],        # update / boot keys
                [0xF0, 0x7D, 0x46, 0x4C, 67, 0xF7],                                               # Felucca's BACKUP_PUT
                [0xF0, 0x7D, 0x53, 0x33, 1], [0xF0, 0x7D, 0x53, 0x33, 1, 0xF7, 0xF0, 0x22, 0x24, 0x35, 0x7F, 0xF7],
                [0xF0, 0x22, 0x24, 0x35, 0x7E, 0xF7], [0xF0, 0x00, 0x32, 0x45, 0x00, 0x00, 0x00, 0x40, 0x7F, 0xF7],
                [0x90, 60, 100, 0xF0, 0x22, 0x24, 0x35, 0x7F, 0xF7], [0x90, 60], [0x90, 60, 100, 60], [0x90, 0x80, 1],
                [0xC0, 5], [0xE0, 0, 64], [0xF8], [0xFF], [60, 100], [0xF7], [0xB0, 123, 0, 0xB1, 123, 0],
                [0xF0, 0x7D, 0x53, 0x33, 1, 0x90, 0xF7], [], [0xF0, 0x00, 0x59, 0x21, 0xF7]):
        assert not ok(bad), bad
    assert not sims.builtin_editor_out_allowed([0xF0, 0x22, 0x24, 0x35, 0x7F, 0xF7])
    # what the bundled editors themselves send besides their SysEx (web/src/core.js noteOut, allNotesOff)
    for good in ([0x90, 60, 100], [0x99, 36, 1], [0x80, 60, 0], [0x8F, 127, 0], *[[0xB0 | c, 123, 0] for c in range(16)]):
        assert ok(good), good


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


def test_newer_unit_rule_is_the_hardware_modules():
    """no check of its own: the revision comes from hardware.py, the rule from planner.chip_check with hardware.json's
    rows (SLOOP 3 and m-bootloader: risky) – blocked on a newer unit, a confirmation when the unit is not known"""
    from fm1companion import hardware
    from fm1companion.flash import planner

    class P:
        hw_revision = ""
    assert S3.revision_of(P()) == "unknown" and S3.revision_of(None) == "unknown"
    P.hw_revision = "new"
    assert S3.revision_of(P()) == "new"
    assert hardware.support("sloop3", "v3.3.7").status == hardware.RISKY
    assert hardware.support("m-bootloader").status == hardware.RISKY
    assert hardware.support("sloop", "v3.3.7").status == hardware.RISKY      # (asked as SLOOP: redirected to SLOOP 3)
    t = planner.Target.from_spec(firmware.spec("sloop3"), tag="v3.3.7")
    assert planner.chip_check("new", t).blockers and not planner.chip_check("old", t).blockers
    unknown = planner.chip_check("", t)
    assert not unknown.blockers and "no_logo" in unknown.confirmations


# ------------------------------------------------- after the review ---

from fake_sloop3 import Fake, InProcess  # noqa: E402


def test_probe_is_one_short_question_and_honours_its_timeout():
    import time
    silent = Link(InProcess(silent=True))
    t0 = time.monotonic()
    assert S3.probe(silent) is None
    took = time.monotonic() - t0
    assert took < 0.6 and len(silent.t.seen) == 1, took           # (was 2.0 s and two frames)
    t0 = time.monotonic()
    assert S3.probe(silent, timeout=0.1, tries=1) is None
    assert time.monotonic() - t0 < 0.3
    assert S3.probe(Link(InProcess())).version == "3.3.7"
    # the connect chain asks it only when the handshake and Hortator's INFO both stayed unanswered
    src = (ROOT / "fm1companion" / "ui" / "device.py").read_text("utf-8")
    assert "s3 = None if ident or hort else S3.probe(link)" in src


def test_0x12_is_asked_only_on_the_exact_port_and_never_after_another_identity():
    ok = mboot.is_candidate
    assert ok("FM-1 Update", "1209:0002", "ota-FM-1_900", "0200", "FM-1 Update")      # as seen on a real FM-1
    assert not ok("FM-1 Update", "1209:0002", "", "0100") and not ok("FM-1 Update", "1209:0002", "", "0310")
    assert not ok("FM-1 Update", "1209:0002", "", "0200", "Melodee FM-1 Update")
    assert ok("FM-1 Update", "1209:0002", "ota-FM-1_900") and ok("FM-1 Update", "", "") and ok("FM-1 Update", "1209:0002")
    for label, uid, ident in (("FM-1 Update", "1209:0002", "ota-FM-1_015"), ("FM-1 Update", "", "ota-FM-1_016"),
                              ("FM-1 Update 2", "1209:0002", ""), ("FM-1 Updater", "", ""), ("My FM-1 Update", "", ""),
                              ("FM-1 Update", "4c4a:4155", "ota-FM-1_900"), ("", "", "ota-FM-1_900")):
        assert not ok(label, uid, ident), (label, uid, ident)


def test_mboot_range_and_capacity_limits():
    h = mboot.Host(FakeMboot(FLASH))
    h.identify()
    for fn in (lambda: h.crc(0, 0x1000000), lambda: h.crc(0, 0), lambda: h.read(0, 513), lambda: h.read(0x100000, 1)):
        with pytest.raises(mboot.MbootError):
            fn()
    body = b"MBOOT" + bytes([1, 1, 1, 0x0F, 0x98]) + (0x1000000).to_bytes(4, "little") + bytes(4) + bytes(8) + b"\0\0v\0"
    assert mboot.parse_info(body) is None             # a 16 MiB chip: its size does not fit a request's 24 bits


class _Ctx:
    def __init__(self, hw="", jedec=None):
        self.profile = type("P", (), {"hw_revision": hw, "name": "T"})()
        self.mboot = None if jedec is None else mboot.Info("2.4", 1, 0x980F, 0x100000, jedec, "00" * 8)
        self.port = None


def test_hardware_verdict_strictest_reading_and_no_installer_link_unless_ok():
    from fm1companion.ui import sloop3_ui as U
    cases = [("", None, "unknown", "warn"), ("old", None, "old", "ok"), ("new", None, "new", "bad"),
             ("", 0x856014, "old", "ok"), ("old", 0xB36014, "new", "bad"), ("", 0xB36014, "new", "bad"),
             ("old", 0xC84014, "unknown", "warn"),     # an unknown chip never loses to the owner's "old"
             ("new", 0x856014, "new", "bad"),          # "new" is sticky
             ("old", 0x01B36014, "new", "bad"), ("old", 0x01856014, "old", "ok")]       # (the ID is 24 bits)
    for hw, jedec, rev, level in cases:
        c = _Ctx(hw, jedec)
        assert U.revision(c)[0] == rev, (hw, jedec)
        assert U.revision_text(c)[0] == level, (hw, jedec)
        notes = U.notes_html(c)
        assert (S3.INSTALLER_URL + '"' in notes) == (level == "ok"), (hw, jedec)
        assert S3.GUIDE_URL in notes and "m-bootloader" in notes


def test_installer_refuses_m_bootloaders_midi_mode(monkeypatch):
    from fm1companion.devices import PortInfo
    from fm1companion.ui import sloop3_ui as U
    said = []
    monkeypatch.setattr(U, "show_error", lambda parent, title, text, *a: said.append(title + " " + text))
    port = PortInfo("i", "o", "FM-1 Update", None, usb_id="1209:0002", update_mode=True)
    c = _Ctx("old", 0x856014)
    c.port = port
    assert U.refuse_in_midi_mode(None, c) and "m-bootloader" in said[0]
    # the same USB ID as a Felucca update loader (seen on a real unit: 1209:0002, "FM-1 Update", bcdDevice 2.00):
    # told apart by name, device version, handshake answer – and by the answer to 0x12
    units = []
    monkeypatch.setattr(mboot, "open_port", lambda p: units.append(FakeMboot(FLASH)) or units[-1])
    c.mboot = None
    c.identity = "ota-FM-1_900"
    port.bcd_device, port.product = "0200", "FM-1 Update"
    assert U.midi_mode_state(c) == "yes" and units[-1].commands == [0x12]       # (asked once more)
    other = PortInfo("i2", "o2", "FM-1 Update", None, usb_id="1209:0002", update_mode=True)
    assert U.refuse_in_midi_mode(None, c, other) and units[-1].commands == [0x12]
    n = len(units)
    for label, kw in (("Melodee FM-1 Update", {}), ("Felucca Update", {}), ("FM-1 Update", {"bcd_device": "0100"}),
                      ("FM-1 Update", {"product": "Felucca Update"}), ("FM-1 Update", {"usb_id": "4d4a:4155"})):
        loader = PortInfo("i3", "o3", label, None, update_mode=True, **{"usb_id": "1209:0002", **kw})
        assert U.midi_mode_state(c, loader) == "no" and not U.refuse_in_midi_mode(None, c, loader), (label, kw)
    assert len(units) == n                            # (not even asked)
    c.identity = "ota-FM-1_015"                       # the active port answered as M-VAVE's loader: no MIDI MODE
    assert U.midi_mode_state(c) == "no"
    # looks exactly like MIDI MODE but does not answer 0x12: cannot be told apart → nothing is sent either
    c.identity = ""
    monkeypatch.setattr(mboot, "open_port", lambda p: FakeMboot(FLASH, answer_0x12=False))
    monkeypatch.setattr(mboot.Host, "identify", lambda self, tries=1, timeout=0.1: None)
    said.clear()
    assert U.midi_mode_state(c) == "unclear" and U.refuse_in_midi_mode(None, c) and "FM-1 Update" in said[0]
    running = PortInfo("i4", "o4", "FM-1 SLOOP 3", None, usb_id="1209:0001")
    assert U.midi_mode_state(c, running) == "no"
    for f, needle in (("ui/firmware_page.py", "_s3ui.refuse_in_midi_mode(parent, ctx)"),
                      ("ui/pages/recovery.py", "sloop3_ui.refuse_in_midi_mode(self, self.ctx, port)")):
        assert needle in (ROOT / "fm1companion" / f).read_text("utf-8"), f
    fw = (ROOT / "fm1companion" / "ui" / "firmware_page.py").read_text("utf-8")
    assert "firmware goes onto it from m-bootloader" not in fw      # (the text that sent users into MIDI MODE)


def _restorable(link) -> dict:
    with S3.Client(link) as c:
        c.write_file(S3.KIND_PROJECT, c.read_file(S3.KIND_NOW))
    return BK.capture(link)


def test_restore_reads_your_sounds_back_and_says_what_state_each_part_is_in():
    from fm1companion.ui import sloop3_ui as U
    f = _restorable(Link(InProcess()))
    plan = BK.restore_plan(f)
    assert plan == [BK.SOUNDS, BK.PROJECT]
    done = BK.restore(Link(InProcess()), f)
    assert list(done) == plan and done.unverified == []           # both read back and identical
    # the FM-1 acknowledges your sounds but keeps its old ones: noticed, nothing claimed
    changed = json.loads(json.dumps(f))
    snd = bytearray(BK.objects(f)[BK.SOUNDS])
    snd[-1] ^= 1
    changed["objects"][1] = BK._obj(BK.SOUNDS, bytes(snd))
    with pytest.raises(BK.Sloop3BackupError) as e:
        BK.restore(Link(InProcess(Fake(fault="sounds_dropped"))), changed)
    assert (e.value.written, e.value.unclear, e.value.untouched) == ([], [BK.SOUNDS], [BK.PROJECT])
    lines = U.state_lines(e.value)
    assert len(lines) == 2 and lines[0].startswith("?") and lines[1].startswith("–")
    # the project was replaced but reads back differently: it is "unclear", never "as it was"
    with pytest.raises(BK.Sloop3BackupError) as e:
        BK.restore(Link(InProcess(Fake(fault="project_differs"))), f)
    assert (e.value.written, e.value.unclear, e.value.untouched) == ([BK.SOUNDS], [BK.PROJECT], [])
    lines = U.state_lines(e.value)
    assert lines[0].startswith("✓") and lines[1].startswith("?") and not any("as they were" in x for x in lines)
    src = (ROOT / "fm1companion" / "ui" / "sloop3_ui.py").read_text("utf-8")
    assert "the others are as they were" not in src and "written and read back." not in src


def test_odd_backup_files_are_refused_cleanly(tmp_path):
    f = _restorable(Link(InProcess()))
    g = json.loads(json.dumps(f))
    for bad in (5, "x", {"a": 1}, None, [5, "x", None]):
        g["samples_not_included"] = bad
        rep = backup_inspect.inspect(json.loads(json.dumps(g)))
        assert rep.warnings and not rep.restorable
    for bad in (float("inf"), 1e308, "2", True, [2], -1, 7, None):
        g["bank"] = {"fm6_source": bad, "syn_source": bad}
        assert BK.bank_sources(g) == (0, 0), bad
        assert BK.restore_plan(g) == [BK.SOUNDS, BK.PROJECT]
    g["bank"] = [1, 2]
    assert BK.bank_sources(g) == (0, 0)
    assert not hasattr(BK, "to_s3b")                  # (had no caller; the editor writes .s3b itself)


def test_mirror_takes_a_flash_backup_whole_or_not_at_all(tmp_path):
    from fm1companion import autobackup as AB
    root, target = tmp_path / "Backups", tmp_path / "mirror"
    target.mkdir()
    h = mboot.Host(FakeMboot(FLASH[:0x10000]))
    info = h.identify()
    p = mboot.save_backup(root / "FM-1 A" / "Flash", "FM-1 A", info, h.read_flash())
    lone = root / "FM-1 B" / "Flash" / "B_flash_x.json"       # a description whose image is gone
    lone.parent.mkdir(parents=True)
    lone.write_text(json.dumps({"format": "fm1-flash-backup", "version": 1, "file": "B_flash_x.bin"}))
    ok, bad, msgs = AB.mirror_all(root, target)
    assert bad == 0, msgs
    rel = p.relative_to(root)
    assert (target / rel).read_bytes() == p.read_bytes() and (target / rel.with_suffix(".json")).exists()
    assert not (target / "FM-1 B").exists()
    again = mboot.save_backup(root / "FM-1 A" / "Flash", "FM-1 A", info, FLASH[:0x10000])
    assert " (2)" in again.name or again.name != p.name


def test_credits_name_sloop3_and_m_bootloader():
    from fm1companion import diag
    names = " ".join(str(e.get("name")) for e in diag.credits()) if hasattr(diag, "credits") else \
        (ROOT / "fm1companion" / "data" / "credits.d" / "sloop3.json").read_text("utf-8")
    assert "SLOOP 3" in names and "m-bootloader" in names
    readme = (ROOT / "README.md").read_text("utf-8")
    assert "**SLOOP 3** (isod89" in readme and "m-bootloader" in readme and "Julius" in readme


def test_sloop3_unit_is_not_mixed_up_with_the_owners_other_fm1(tmp_path, monkeypatch):
    """Seen on the owner's Pi: "FM-1 Schwarz/Grün" (earlier unit, last Skiff 2.7 at USB 3-1.3.1) got SLOOP 3 outside
    the app and was plugged into port 1-2, where "FM-1 Orange" (newer unit, Skiff 2.7, no serial number) was last
    seen. The app took it for Orange. SLOOP 3 shows no USB serial number (hal/fm1/usb.c: serial index 0) and no
    identity – the USB port alone is no proof when what the unit shows contradicts the profile: asked, and nothing
    is written to any profile before the answer."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.setattr("fm1companion.ui.device.config.save", lambda s: None)
    from test_pi_hardware import fake_ctx, store_with
    from types import SimpleNamespace
    from fm1companion import devices
    from fm1companion.ui import sloop3_ui as U
    from fm1companion.ui.device import DeviceContext
    two = [{"id": "o", "name": "FM-1 Orange", "firmware": "skiff", "firmware_version": "v2.7", "identity": "FM-1_900",
            "usb_path": "1-2", "hw_revision": "new", "version_string": "FELUCCA v2.7"},
           {"id": "g", "name": "FM-1 Schwarz/Grün", "firmware": "skiff", "firmware_version": "v2.7",
            "identity": "FM-1_900", "usb_path": "3-1.3.1", "hw_revision": "old", "version_string": "FELUCCA v2.7"}]
    s3port = devices.PortInfo("FM-1 SLOOP 3 32:0", "FM-1 SLOOP 3 32:0", "FM-1 SLOOP 3", 32, usb_id="1209:0001",
                              usb_path="1-2", product="FM-1 SLOOP 3", bcd_device="0310")
    info = S3.Info(2, 5, 48, 0, 0, 0, "3.4.0")

    def connect(store, port, answer, s3=info, ident=""):
        link = SimpleNamespace(hs_identity=ident, hs_flash=0, closed=False)
        ctx = fake_ctx(store, port, link=link, answer=answer, changed=SimpleNamespace(emit=lambda: None),
                       message=SimpleNamespace(emit=lambda m: None), firmware_guess=[], sloop3=None, identity=ident)
        ctx._assign_profile = lambda: DeviceContext._assign_profile(ctx)
        if s3 is not None:
            DeviceContext._sloop3(ctx, s3, link)
        else:
            DeviceContext._assign_profile(ctx)
        return ctx
    # 1. the unit appears at Orange's port: a question – unanswered, no profile, and no profile was touched
    store = store_with(tmp_path, two)
    before = json.dumps([vars(p) for p in store.profiles.values()], sort_keys=True, default=str)
    ctx = connect(store, s3port, None)
    assert ctx.asked == [(["o", "g"], "o")] and ctx.profile is None
    assert json.dumps([vars(p) for p in store.profiles.values()], sort_keys=True, default=str) == before
    assert [c.id for c in ctx.unit_candidates] == ["o", "g"]
    # … while unanswered: the strictest reading of what it may be (Orange is a newer unit), nothing is restored
    ctx.midi_connected, ctx.mboot = True, None
    assert U.revision(ctx)[0] == "new" and U.revision_text(ctx)[0] == "bad"
    said = []
    monkeypatch.setattr(U, "show_error", lambda parent, title, text, *a: said.append(text))
    assert U.handles_restore(None, ctx, tmp_path / "x.json") and said
    # 2. the owner says "Schwarz/Grün": that profile is SLOOP 3 now and remembers this port; Orange is unchanged
    ctx = connect(store, s3port, lambda cands: next(c for c in cands if c.id == "g"))
    o, g = store.profiles["o"], store.profiles["g"]
    assert ctx.profile is g and (g.firmware, g.firmware_version, g.usb_path) == ("sloop3", "v3.4.0", "1-2")
    assert g.usb_product == "FM-1 SLOOP 3" and g.hw_revision == "old" and g.firmware_history[-1]["how"] == "found"
    assert (o.firmware, o.firmware_version, o.usb_path, o.hw_revision, o.usb_product) == ("skiff", "v2.7", "1-2", "new", "")
    assert U.revision(ctx)[0] == "old"
    # 3. the same unit at the same port again: remembered, no question (two profiles know this port now)
    ctx = connect(store, s3port, None)
    assert not ctx.asked and ctx.profile is store.profiles["g"]
    # 4. Orange itself comes back to its port (Skiff: it answers the update identity): Orange, no question – and
    #    never the SLOOP 3 profile
    skiff = devices.PortInfo("Felucca 32:0", "Felucca 32:0", "Felucca", 32, usb_id="1209:0001", usb_path="1-2",
                             product="Felucca")
    ctx = connect(store, skiff, None, s3=None, ident="FM-1_900")
    assert not ctx.asked and ctx.profile is store.profiles["o"] and store.profiles["g"].firmware == "sloop3"
    # 5. a single known FM-1 that last ran Skiff: SLOOP 3 at its port still contradicts – asked, not taken over
    one = store_with(tmp_path, [two[0]])
    ctx = connect(one, s3port, None)
    assert ctx.asked and ctx.profile is None and one.profiles["o"].firmware == "skiff"
    # 6. a profile that already is the SLOOP 3 unit at this port: taken without a question
    known = store_with(tmp_path, [dict(two[0], firmware="sloop3", firmware_version="v3.3.7", identity="")])
    ctx = connect(known, s3port, None)
    assert not ctx.asked and ctx.profile is known.profiles["o"] and known.profiles["o"].firmware_version == "v3.4.0"


def test_m_bootloaders_unit_id_names_the_unit(tmp_path, monkeypatch):
    """the flash chip's unique ID from m-bootloader's 0x12 answer: a profile whose serial number under the original
    firmware is that ID (M-Flasher's "unit id"; either byte order) is this unit, at whatever port; it is kept"""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    from test_pi_hardware import fake_ctx, store_with
    from fm1companion.ui.device import DeviceContext
    store = store_with(tmp_path, [{"id": "o", "name": "FM-1 Orange", "usb_path": "1-2"},
                                  {"id": "g", "name": "FM-1 Schwarz/Grün", "serial": "4150354433333610"}])
    for uid in ("4150354433333610", "1036333344355041"):
        ctx = fake_ctx(store, None, profile=store.profiles["o"])
        DeviceContext._unit_by_id(ctx, uid)
        assert ctx.profile is store.profiles["g"] and store.profiles["o"].unit_id == ""
    assert store.profiles["g"].unit_id == "1036333344355041"         # (learnt as read)
    ctx = fake_ctx(store, None, profile=store.profiles["o"])
    DeviceContext._unit_by_id(ctx, "aabbccddeeff0011")                # no profile has it: the assigned one learns it
    assert ctx.profile is store.profiles["o"] and store.profiles["o"].unit_id == "aabbccddeeff0011"
    for bad in ("", "0" * 16, "f" * 16, "abcd"):
        ctx = fake_ctx(store, None, profile=None)
        DeviceContext._unit_by_id(ctx, bad)
        assert ctx.profile is None


# ---- the owner's Pi, 2026-11-18 (pi findings 28): "Which FM-1 is this?" answered, the other profile lost its firmware

_TWO_UNITS = [
    {"id": "o", "name": "FM-1 Orange", "firmware": "skiff", "firmware_version": "v2.7", "identity": "FM-1_900",
     "usb_path": "1-2", "hw_revision": "new", "hw_revision_source": "user", "version_string": "FELUCCA v2.7",
     "firmware_choice": {"FM-1_900": "skiff"}, "color": "#f67400",
     "firmware_history": [{"time": 1.0, "firmware": "skiff", "name": "Skiff", "version": "v2.7",
                           "identity": "FM-1_900", "via_v15": True}]},
    {"id": "g", "name": "FM-1 Schwarz/Grün", "firmware": "skiff", "firmware_version": "v2.7", "identity": "FM-1_900",
     "usb_path": "3-1.3.1", "hw_revision": "old", "hw_revision_source": "user", "version_string": "FELUCCA v2.7",
     "firmware_choice": {"FM-1_900": "skiff"}, "color": "#27ae60",
     "firmware_history": [{"time": 1.0, "firmware": "skiff", "name": "Skiff", "version": "v2.7",
                           "identity": "FM-1_900"}]}]


class _Deaf(InProcess):
    """the fake SLOOP 3, but its first `drop` INFO answers get lost (a unit that has just come up on the USB bus)"""
    def __init__(self, drop: int):
        super().__init__(Fake(version="3.4.0"))
        self.drop = drop

    def send(self, data):
        if self.drop and bytes(data) == bytes([0xF0, 0x7D, 0x53, 0x33, 0x01, 0xF7]):
            self.drop -= 1
            self.seen.append(bytes(data))
            return
        super().send(data)


def _plug_in(tmp_path, monkeypatch, usb: str, drop: int = 0, answer: str = "g"):
    """the real DeviceContext: the SLOOP 3 unit appears at `usb`, the owner answers the question with `answer`.
    -> (devices.json before, after, the items the question listed, the app log lines, the context)"""
    pytest.importorskip("PyQt6.QtWidgets")
    import logging
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtCore import QEventLoop, QTimer
    from PyQt6.QtWidgets import QApplication, QInputDialog
    app = QApplication.instance() or QApplication([])  # noqa: F841
    from fm1companion import devices
    from fm1companion.ui import device as D
    f = tmp_path / f"devices-{usb}-{drop}.json"
    f.write_text(json.dumps({"profiles": _TWO_UNITS}), "utf-8")
    devices.ProfileStore(f).save()                      # (as the app writes it: every field)
    before = f.read_text("utf-8")
    port = devices.PortInfo("FM-1 SLOOP 3 MIDI 1 32:0", "FM-1 SLOOP 3 MIDI 1 32:0", "FM-1 SLOOP 3", 32,
                            usb_id="1209:0001", usb_path=usb, product="FM-1 SLOOP 3", bcd_device="0310")
    monkeypatch.setattr(D, "DEMO", False)
    monkeypatch.setattr(D, "scan_ports", lambda: [port])
    monkeypatch.setattr("fm1companion.devices.uboot_present", lambda *a: [])
    monkeypatch.setattr(D, "MidoTransport", lambda i, o: _Deaf(drop))
    monkeypatch.setattr(D.config, "save", lambda s: None)
    items = []

    def get_item(parent, title, text, names, cur, editable):
        items.append(list(names))
        return next(n for n in names if n.startswith(dict(o="FM-1 Orange", g="FM-1 Schwarz")[answer])), True
    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(get_item))
    lines = []

    class H(logging.Handler):
        def emit(self, rec):
            lines.append(rec.getMessage())
    h = H()
    logging.getLogger("fm1companion.device").addHandler(h)
    logging.getLogger("fm1companion.device").setLevel(logging.INFO)
    ctx = D.DeviceContext()
    ctx.store = devices.ProfileStore(f)
    ctx.settings.last_device = ""
    msgs = []
    ctx.message.connect(msgs.append)
    try:
        import time as _t
        t0 = _t.time()
        while _t.time() - t0 < 30 and not any("connected" in m.lower() or "verbunden" in m.lower()
                                               or "neither" in m or "weder" in m for m in msgs):
            loop = QEventLoop()
            QTimer.singleShot(100, loop.quit)
            loop.exec()
        ctx.timer.stop()
    finally:
        logging.getLogger("fm1companion.device").removeHandler(h)
    return before, f.read_text("utf-8"), items, lines, ctx


def _entry(text: str, pid: str) -> str:
    return json.dumps(next(p for p in json.loads(text)["profiles"] if p["id"] == pid), sort_keys=True,
                      ensure_ascii=False)


@pytest.mark.parametrize("usb,drop", [("3-1.3.1", 0), ("1-2", 0), ("3-1.3.1", 3), ("1-2", 3)])
def test_answering_which_fm1_changes_only_the_chosen_profile(tmp_path, monkeypatch, usb, drop):
    """Seen on the Pi: the SLOOP 3 unit ("FM-1 Schwarz/Grün") was asked about and the owner chose it – afterwards the
    profile of the unit that was NOT plugged in, "FM-1 Orange", had lost its firmware (`firmware: ""`) although its
    history says Skiff v2.7. The path: a unit whose first SLOOP 3 INFO answers got lost (drop=3: it has just come
    up) answers nothing at all – the profile was taken by its USB port alone (the product name "FM-1 SLOOP 3" was
    not counted as SLOOP 3), and "answers nothing" wiped that profile's firmware. Now: the product name counts, the
    owner is asked, and an answer of nothing changes no firmware. Orange stays byte for byte as it was."""
    before, after, items, lines, ctx = _plug_in(tmp_path, monkeypatch, usb, drop)
    assert _entry(after, "o") == _entry(before, "o")
    assert items, "the unit contradicts both profiles (SLOOP 3 vs. Skiff): asked"
    # the question names the firmware by its name and says it is what the unit LAST ran (pi finding 28)
    from fm1companion.i18n import _
    assert items[0][:2] == ["FM-1 Orange " + _("(last seen: {firmware})").format(firmware="Skiff 2.7"),
                            "FM-1 Schwarz/Grün " + _("(last seen: {firmware})").format(firmware="Skiff 2.7")]
    assert not any("skiff v2.7" in x for x in items[0])
    g0, g1 = json.loads(_entry(before, "g")), json.loads(_entry(after, "g"))
    assert ctx.profile is not None and ctx.profile.id == "g"
    changed = {k for k in g1 if g1[k] != g0.get(k)}
    assert changed <= {"usb_path", "usb_product", "firmware", "firmware_version", "version_string", "identity",
                       "firmware_history", "last_seen"}, changed
    assert g1["usb_path"] == usb and g1["usb_product"] == "FM-1 SLOOP 3"
    if drop:
        # it answered nothing: nothing is known about its firmware – nothing is changed (not "unknown")
        assert (g1["firmware"], g1["firmware_version"], g1["firmware_history"]) == \
            (g0["firmware"], g0["firmware_version"], g0["firmware_history"])
    else:
        assert (g1["firmware"], g1["firmware_version"]) == ("sloop3", "v3.4.0")
        assert g1["firmware_history"][:-1] == g0["firmware_history"] and g1["firmware_history"][-1]["how"] == "found"
    # pi finding 28: the pick leaves a "connected" line in the app log, like any other connection
    assert any(x.startswith("connected:") and "profile g" in x for x in lines), lines
