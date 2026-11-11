# SPDX-License-Identifier: GPL-3.0-only
"""The catalog audit (docs/firmware-support.md, fm1companion/fwfamily.py): every firmware of the Felucca family is
named by what it answers, its backup protocol is asked of the device when its INFO does not tell (never guessed for a
write), backup inventories the app does not know are kept as they are, and a backup never goes onto a firmware that
keeps other things under the same objects. The INFO strings and inventories are the ones the audit read in the
firmwares' sources (firmware/src/editor*.c ED_INFO, ED_BK_IDS)."""
from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fm1companion import backup, firmware, fwfamily, sloopbk, studio               # noqa: E402
from fm1companion.demo_device import SLOOP_IDS, FakeFM1, rnd                       # noqa: E402
from fm1companion.protocol import Info, r32                                        # noqa: E402
from fm1companion.transport import Link, Transport                                 # noqa: E402

# INFO strings as the audit found them (the firmware's ED_INFO; a release's version where its build.py sets one)
RECORDED_INFO = {
    "FELUCCA v1.8.1": ("felucca", None),                 # Felucca – and every fork built with --release X.Y
    "FELUCCA 0.9 BETA": ("felucca", None),               # a SLOOP fork's --release build (fm1-quest, …): no name
    "FELUCCA SLOOP 2.8.5": ("sloop", "sloop"),
    "FELUCCA KARMA 3.1": ("karma", "karma"),
    "FELUCCA ORBIT 1.0.0": ("orbit", "orbit"),
    "FELUCCA sloopDX 4.0.1": ("sloopdx", "sloopdx"),     # contains "sloop", but its 10 and 11 are its own
    "FELUCCA CLIPPER 1.3.1": ("clipper", "clipper"),
    "FELUCCA SYNTHESTHETIC 0.2": ("synthesthetic", "synthesthetic"),
    "JANGADA 1.0.1": ("jangada", "jangada"),
    "FELUCCA winampFM 0.9": ("winampfm", "winampfm"),
    "FELUCCA NMS FM 0.9": ("no-man-sky-fm", "no-man-sky-fm"),
    "FELUCCA FM1 MOVE 0.3.1": ("fm1-move", "fm1-move"),
    "FELUCCA OPTIMIST 0.3": ("optimist", "optimist"),
    "FELUCCA WEB GB2.4": ("ghoulbox", "ghoulbox"),
    "TESSERA v2.0": ("tessera", "tessera"),
    "SAADET v1.0": ("saadet", "saadet"),
    "DUELL 0.2.1 beta": ("duell", "duell"),
    "PIGEON v0.5.0-beta.1": ("pigeon", "pigeon"),
    "CARNAC OS DEV": ("carnac-os", "carnac-os"),
    "MELODEE v1.6.0-rc.7": ("melodee", "melodee"),
    "JIANT V10.60": ("jiant", "jiant-fm"),
    "CoralFlower 0.7.1": ("coralflower", "coralflower"),
    "ChoralRoot 0.39": ("choralroot", "choralroot-fm-1"),
    "X0X 1.2.1 BETA": ("x0x", "x0x"),
}


WRONG_PLACES = "falschen Stellen"          # (the tests run in German: tests/conftest.py)


class LocalTransport(Transport):
    def __init__(self, dev):
        self.dev = dev
        self.sent: list[list[int]] = []

    def send(self, data):
        self.sent.append(list(data))
        r = self.dev.handle(list(data))
        if r is not None and self.receiver:
            threading.Thread(target=self.receiver, args=(r,), daemon=True).start()


def link_for(dev):
    t = LocalTransport(dev)
    return Link(t), t


def cmds(t):
    return [fr[4] for fr in t.sent if len(fr) > 4 and fr[1:4] == [0x7D, 0x46, 0x4C]]


def sloop_dev(version, extra=None):
    """SLOOP's demo device answering another INFO (a fork), optionally with objects of its own (Clipper's 12..14)"""
    dev = FakeFM1(4, "SLOOP 2.8.3")
    dev.version = version
    for oid, data in (extra or {}).items():
        dev.ids = sorted(set(dev.ids) | {oid})
        dev.objs[oid] = data
    if extra:
        base_obj = dev._sloop_obj
        dev._sloop_obj = lambda oid: dev.objs.get(oid) if oid in extra else base_obj(oid)
        base_bk = dev._sloop_backup

        def bk(cmd, a):                                # (PUT of its own objects: kept as they come)
            if cmd == 36 and len(a) >= 2 and a[1] in extra:
                if a[0] == 0:
                    dev.bk_put = {"id": a[1], "len": r32(a, 2), "crc": r32(a, 7), "buf": bytearray()}
                    return [0, a[1], 0]
                if a[0] == 2:
                    dev.objs[a[1]] = bytes(dev.bk_put["buf"])
                    dev.log.append(a[1])
                    dev.bk_put = None
                    return [2, a[1], 0]
            return base_bk(cmd, a)
        dev._sloop_backup = bk
    return dev


class FeluccaFork(FakeFM1):
    """a Felucca-protocol device: PUT is refused for its sample slots (Felucca's ed_bk_write: ids above 9 -> rc 1)"""

    def __init__(self, version, ids, samples=3, no_put=(32, 33, 34)):
        super().__init__(samples, version, ids=list(ids))
        self.no_put = set(no_put)

    def _handle(self, cmd, a):
        if cmd == 67 and len(a) >= 2 and a[1] in self.no_put:
            return [a[0], a[1], 1]
        return super()._handle(cmd, a)


# ------------------------------------------------------------ names ---

def test_recorded_info_strings_name_their_firmware():
    for version, (name, key) in RECORDED_INFO.items():
        assert Info(version).firmware_name == name, version
        fam = fwfamily.by_info(version)
        assert (fam.key if fam else None) == key, version
    assert fwfamily.info_name("FELUCCA v1.1-rc1") == "" and fwfamily.info_name("FELUCCA GNOMAD V1 1bd443") == "gnomad"
    assert Info("FELUCCA v1.8.1", engines=["DRUM-X"]).firmware_name == "skiff"


def test_guess_firmware_trusts_the_info_name():
    """a fork that names itself is its catalog key, whatever this machine remembered for the shared FM-1_900; X0X's
    forks answer X0X's INFO unchanged – an identity only one of them carries in the catalog names it"""
    assert firmware.guess_firmware("FM-1_900", "FELUCCA CLIPPER 1.3.1", "clipper")[0] == "clipper"
    assert firmware.guess_firmware("FM-1_909", "JANGADA 1.0.1", "jangada")[0] == "jangada"
    assert firmware.guess_firmware("FM-1_900", "FELUCCA v1.8.1", "felucca")[0] == "felucca"
    assert firmware.guess_firmware("FM-1_9010200", "X0X 1.2.0 BETA", "x0x")[0] == "x0x"
    assert firmware.guess_firmware("FM-1_9000300", "X0X 0.3.0 BETA", "x0x")[0] == "tribe"
    for key in ("clipper", "saadet", "sloopdx"):
        assert key in firmware.SELF_NAMING
    # SLOOP's build.py --release writes "FELUCCA X.Y BETA" (Felucca's: "vX.Y"): sloopDX 2.6's release is FM-1_926
    assert firmware.guess_firmware("FM-1_926", "FELUCCA 2.6 BETA", "felucca")[0] == "sloopdx"
    assert firmware.guess_firmware("FM-1_915", "FELUCCA v1.5", "felucca")[0] == "felucca"
    # Skiff's FM-1_7XY range – but TONITOR's FM-1_700 and Fart Machine One's FM-1_701 are theirs
    assert firmware.guess_firmware("FM-1_712")[0] == "skiff"
    assert firmware.guess_firmware("FM-1_700")[0] == "tonitor" and firmware.guess_firmware("FM-1_701")[0] != "skiff"


def test_every_table_entry_is_a_catalog_firmware():
    from fm1companion import catalog
    keys = {e.key for e in catalog.load(prefer_cache=False).entries} | set(studio.FIRMWARES)   # (KARMA: a twin only)
    missing = [f.key for f in fwfamily.FAMILIES if f.key not in keys]
    assert not missing, missing
    assert all(f.backup in ("felucca", "sloop", "x0x", "own", "none") for f in fwfamily.FAMILIES)
    assert len({n for f in fwfamily.FAMILIES for n in f.names}) == sum(len(f.names) for f in fwfamily.FAMILIES)


# ------------------------------------------------------------ protocol ---

def test_unnamed_sloop_fork_is_asked_not_guessed():
    """fm1-quest answers "FELUCCA 0.9" (SLOOP's build.py --release: no name): the app asked Felucca's LIST (65) and
    timed out before. Now a LIST 65 (SLOOP: no such command), then SLOOP's 34 – read-only both."""
    dev = sloop_dev("FELUCCA 0.9 BETA")
    link, t = link_for(dev)
    proto, info = backup.protocol(link, "felucca")
    assert proto == "sloop" and info.version == "FELUCCA 0.9 BETA"
    assert cmds(t) == [1, 65, 34]
    assert backup.protocol(link)[0] == "sloop" and cmds(t)[3:] == [1]       # (the answer is kept with the link)
    f = backup.capture(link, "")
    assert f["format"] == "sloop-backup" and all(c in (1, 34, 35, 65) for c in cmds(t))


def test_plain_felucca_answers_its_own_list():
    dev = FeluccaFork("FELUCCA v1.8.1", backup.FELUCCA_IDS)
    link, t = link_for(dev)
    assert backup.protocol(link)[0] == "felucca" and cmds(t) == [1, 65]


def test_named_sloop_forks_never_get_felucca_commands():
    for version in ("JANGADA 1.0.1", "FELUCCA CLIPPER 1.3.1", "FELUCCA winampFM 0.9", "FELUCCA sloopDX 4.0.1"):
        dev = sloop_dev(version)
        link, t = link_for(dev)
        assert backup.protocol(link)[0] == "sloop", version
        assert 65 not in cmds(t), version


def test_own_protocol_and_none_are_explained():
    """Optimist answers INFO "FELUCCA OPTIMIST 0.3" but backs up over its own commands 43–48; CARNAC OS has no backup:
    a clear message, before any backup request goes out"""
    for version, words in (("FELUCCA OPTIMIST 0.3", "eigenes Protokoll"), ("CARNAC OS 0.4", "kein Backup über USB")):
        dev = FeluccaFork(version, [])
        link, t = link_for(dev)
        with pytest.raises(backup.BackupError, match=words):
            backup.protocol(link)
        assert cmds(t) == [1]


def test_neither_protocol_answers():
    """a plain-named firmware without backup commands (Felucca SALT, modular-one's release): no timeout, a message"""
    dev = FeluccaFork("FELUCCA v0.1", [])
    dev._handle = lambda cmd, a: [*dev._s(dev.version), 0, 0, 0, 16, 0] if cmd == 1 else None
    link, t = link_for(dev)
    with pytest.raises(backup.BackupError, match="keine der Backup-Anfragen"):
        backup.protocol(link)
    assert cmds(t) == [1, 65, 34]


# ------------------------------------------------------------ inventories ---

def test_unknown_inventory_of_a_known_length_is_backed_up():
    """WaveLoop (ELI7VH/Felucca): Felucca's 13 objects, then 10..19 – 23 objects like Melodee's 23 (0..22), which the
    app refused as "unexpected object order"; DUELL lists objects larger than Melodee's 3840 bytes below 32"""
    ids = backup.FELUCCA_IDS + list(range(10, 20))
    dev = FeluccaFork("FELUCCA v1.0", ids)
    dev.objs[12] = rnd(5000, 7)
    link, _t = link_for(dev)
    man = backup.list_objects(link)
    assert [o.id for o in man] == ids and len(ids) == 23
    arch = backup.read(backup.capture(link, "FELUCCA v1.0"))
    assert [o["id"] for o in arch.objects] == ids and not arch.known


def test_unnamed_fork_samples_are_asked_without_writing():
    """an inventory of its own: sample slots are the objects Felucca's PUT refuses (begin + abort, nothing written)"""
    ids = backup.FELUCCA_IDS + list(range(10, 20))
    dev = FeluccaFork("FELUCCA v1.0", ids)
    dev.objs[12] = rnd(500, 7)
    link, _t = link_for(dev)
    arch = backup.read(backup.capture(link, "FELUCCA v1.0"))
    assert backup.sample_slots(link, backup.layout_of(ids, "FELUCCA v1.0"), "FELUCCA v1.0", ids) == {32, 33, 34}
    dev.log.clear()
    backup.restore(link, arch)
    assert 12 in dev.log and not any(i in dev.log for i in (32, 33, 34))


def test_saadet_objects_above_32_are_no_samples():
    """Saadet (keremimo/saadet editor_backup.c): 32 its state, 33..38 the performances, 39 40 HYDRA's user waves –
    restored with PUT like the rest, never through the sample commands (an empty one would have erased a slot)"""
    ids = [1, 6, 7, *range(9, 29), *range(32, 41)]
    dev = FeluccaFork("SAADET v1.0", ids, samples=3, no_put=())
    dev.objs[32] = rnd(900, 3)
    dev.objs[33] = rnd(700, 4)
    link, t = link_for(dev)
    f = backup.capture(link, "SAADET v1.0")
    arch = backup.read(f)                                   # (no running music 0: its inventory starts at 1)
    assert [o["id"] for o in arch.objects] == ids
    dev.log.clear()
    t.sent.clear()
    backup.restore(link, arch)
    assert {32, 33} <= set(dev.log) and not any(c in (11, 12, 13, 14) for c in cmds(t))
    assert studio.backup_firmware(arch) == "saadet"


def test_sloop_fork_objects_of_its_own_are_kept_and_restored_in_its_order():
    """Clipper (isod89/clipper-fm1): SLOOP's objects and 12..14 (CLIPPER_SYNTH), restored as its editor does
    (BK.RESTORE 6 7 12 13 8 9 2 3 4 5 0 14 1); SLOOP's file format refused them as damaged"""
    extra = {12: rnd(300, 1), 13: rnd(200, 2), 14: rnd(100, 3)}
    dev = sloop_dev("FELUCCA CLIPPER 1.3.1", extra)
    link, _t = link_for(dev)
    f = backup.capture(link, "")
    arch = backup.read(f)
    assert {12, 13, 14} <= {o["id"] for o in arch.objects}
    info = Info("FELUCCA CLIPPER 1.3.1")
    want = [i for i in (6, 7, 12, 13, 8, 9, 2, 3, 4, 5, 0, 14, 1, 32, 33, 34, 35) if i in sloopbk.objects(f)]
    assert sloopbk.order(info, set(sloopbk.objects(f))) == want
    dev.log.clear()
    backup.restore(link, arch)
    written = [i for i in dev.log if isinstance(i, int)]
    assert written.index(12) < written.index(2) and written.index(14) < written.index(1)
    assert studio.backup_firmware(arch) == "clipper"


def test_cross_firmware_restores_that_would_misplace_objects_are_refused():
    """sloopDX's 10 and 11 are not SLOOP's MIDI LEARN and theme; Clipper's 12..14 mean nothing to SLOOP; a Saadet
    backup is no Felucca one: refused before anything is written. A SLOOP backup onto Clipper (SLOOP's own objects),
    a Felucca backup onto a Felucca fork that keeps its places: allowed."""
    s_dev = sloop_dev("FELUCCA SLOOP 2.8.3")
    s_link, _ = link_for(s_dev)
    s_arch = backup.read(backup.capture(s_link, ""))
    dx = sloop_dev("FELUCCA sloopDX 4.0.1")
    dx_link, _ = link_for(dx)
    dx_arch = backup.read(backup.capture(dx_link, ""))
    for lk, d, a in ((s_link, s_dev, dx_arch), (dx_link, dx, s_arch)):
        d.log.clear()
        with pytest.raises(backup.BackupError, match=WRONG_PLACES):
            backup.restore(lk, a)
        with pytest.raises(backup.BackupError, match=WRONG_PLACES):
            backup.restore_selected(lk, a, [2])
        assert d.log == []
    cl = sloop_dev("FELUCCA CLIPPER 1.3.1", {12: rnd(300, 1)})
    cl_link, _ = link_for(cl)
    cl_arch = backup.read(backup.capture(cl_link, ""))
    with pytest.raises(backup.BackupError, match=WRONG_PLACES):
        backup.restore(s_link, cl_arch)
    cl.log.clear()
    backup.restore(cl_link, s_arch)                         # (Clipper reads SLOOP's backups, as its editor does)
    assert cl.log[-1] == 1
    # the Felucca protocol: Saadet <-> Felucca refused, Felucca -> a fork with Felucca's places allowed
    fel = FeluccaFork("FELUCCA v1.8.1", backup.FELUCCA_IDS)
    fel_link, _ = link_for(fel)
    fel_arch = backup.read(backup.capture(fel_link, "FELUCCA v1.8.1"))
    sa = FeluccaFork("SAADET v1.0", [1, 6, 7, *range(9, 29), *range(32, 41)], no_put=())
    sa_link, _ = link_for(sa)
    sa_arch = backup.read(backup.capture(sa_link, "SAADET v1.0"))
    for lk, d, a in ((fel_link, fel, sa_arch), (sa_link, sa, fel_arch)):
        d.log.clear()
        with pytest.raises(backup.BackupError, match=WRONG_PLACES):
            backup.restore(lk, a)
        assert d.log == []
    gb = FeluccaFork("FELUCCA WEB GB2.4", backup.FELUCCA_IDS)
    gb_link, _ = link_for(gb)
    gb.log.clear()
    backup.restore(gb_link, fel_arch)
    assert gb.log[-1] == 0


def test_layouts():
    assert backup.layout_of(backup.FELUCCA_IDS, "FELUCCA v1.8.1") == "felucca"
    assert backup.layout_of(list(range(10)), "FELUCCA WEB") == "felucca"            # (fmini-1: no sample slots)
    assert backup.layout_of(list(range(29)) + list(range(64, 92)), "MELODEE v1.6") == "melodee"
    assert backup.layout_of(list(range(10)) + list(range(16, 35)), "FELUCCA v2.0") == "skiff"
    assert backup.layout_of(list(range(30)), "TESSERA v2.0") == "tessera"
    assert backup.layout_of(backup.FELUCCA_IDS + [10, 11], "FELUCCA v1.0") == "own:"
    assert backup.layout_of(list(range(23)), "") == "melodee"                        # (an old Melodee backup)
    assert backup.layouts_fit("felucca", "melodee") and backup.layouts_fit("felucca", "own:")
    assert not backup.layouts_fit("saadet", "felucca") and not backup.layouts_fit("own:foo", "felucca")
    assert backup.layouts_fit("sloop", "karma", fwfamily.by_key("karma").reads)


def test_studio_twins_for_forks_with_objects_of_their_own():
    from fm1companion.ui.studio_service import StudioService
    for key in ("sloopdx", "clipper", "saadet", "tessera", "duell"):
        assert StudioService.firmware_for(key) == "", key
    assert all(k in SLOOP_IDS for k in sloopbk.RESTORE)
