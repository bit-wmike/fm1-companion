# SPDX-License-Identifier: GPL-3.0-only
"""The whole matrix "a backup of firmware X onto a device running firmware Y" (29 backups x 28 devices) with the
expected verdict of every cell: ACCEPT lists the pairs backup.restore() takes, every other pair must be refused with
a BackupError – and a refusal must come before anything is sent that writes (no PUT data or commit, no sample
command). The devices are fakes that accept any content: "accepted" means "the app sends it".

  python3 -m pytest tests/test_backup_matrix.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fm1companion import backup as B, backup_inspect as BI, x0xbk             # noqa: E402
from fm1companion.demo_device import rnd                                      # noqa: E402
from test_firmware_support import FeluccaFork, link_for, sloop_dev            # noqa: E402

MEL57 = list(range(29)) + list(range(64, 92))


class Fork(FeluccaFork):
    """a Felucca-protocol device with exactly this inventory (some objects filled), optionally naming its engines"""

    def __init__(self, version, ids, samples=3, no_put=(32, 33, 34), engines=None):
        super().__init__(version, ids, samples, no_put)
        self.engines = engines
        self.objs = {i: self.objs.get(i, b"") for i in ids}
        for k, i in enumerate(ids):
            if not self.objs[i] and i < 32 and k % 2 == 0:
                self.objs[i] = rnd(300 + i, i)

    def _handle(self, cmd, a):
        if cmd == 1 and self.engines:
            return [*self._s(self.version), len(self.engines), 91, 50, 64, 83,
                    *[x for e in self.engines for x in self._s(e)], 4]
        return super()._handle(cmd, a)


DEVICES = {
    "Felucca 1.8 (13)": lambda: Fork("FELUCCA v1.8.1", B.FELUCCA_IDS),
    "Felucca 1.0 (12)": lambda: Fork("FELUCCA v1.0.2", B.KNOWN_ID_SETS[12]),
    "Melodee 1.6 (57)": lambda: Fork("MELODEE v1.6.0", MEL57, samples=0, no_put=()),
    "Melodee 1.5 (29)": lambda: Fork("MELODEE v1.5.0", list(range(29)), samples=0, no_put=()),
    "Melodee 1.2 (12)": lambda: Fork("MELODEE v1.2.0", B.KNOWN_ID_SETS[12]),
    "Skiff 2 (29)": lambda: Fork("FELUCCA v2.0", B.SKIFF_IDS, engines=["FM6", "DRUM-X"]),
    "JIANT (43)": lambda: Fork("JIANT V10.60", B.JIANT_IDS, samples=0, no_put=()),
    "JIANT 6 songs (35)": lambda: Fork("JIANT V10.6p", B.JIANT_IDS_6, samples=0, no_put=()),
    "CoralFlower (26)": lambda: Fork("CoralFlower 0.7.1", B.CORALFLOWER_IDS, samples=0, no_put=()),
    "ChoralRoot (34)": lambda: Fork("ChoralRoot 0.39", B.CHORALROOT_IDS, samples=0, no_put=()),
    "ChoralRoot old (28)": lambda: Fork("ChoralRoot 0.20", B.CHORALROOT_IDS_OLD, samples=0, no_put=()),
    "Tessera (30)": lambda: Fork("TESSERA v2.0", list(range(30)), samples=0, no_put=()),
    "Saadet": lambda: Fork("SAADET v1.0", [1, 6, 7, *range(9, 29), *range(32, 41)], no_put=()),
    "DUELL": lambda: Fork("DUELL 0.2.1 beta", [0, 1, 2, 3, 4, 5, 8, *range(13, 27), 32, 33, 34]),
    "Red Mass Vampire OS": lambda: Fork("RED MASS VAMPIRE OS 1", [*range(15), 32, 33, 34]),
    "Ghoulbox (13)": lambda: Fork("FELUCCA WEB GB2.4", B.FELUCCA_IDS),
    "Switch Blade (8)": lambda: Fork("Switch Blade 1.0", list(range(8)), samples=0, no_put=()),
    "unnamed fork, own inventory (23)": lambda: Fork("FELUCCA v1.0", B.FELUCCA_IDS + list(range(10, 20))),
    "unknown name, Felucca's 13": lambda: Fork("FOOBAR v1.0", B.FELUCCA_IDS),
    "unknown name, Melodee's 57": lambda: Fork("FOOBAR v1.0", MEL57, samples=0, no_put=()),
    "SLOOP": lambda: sloop_dev("FELUCCA SLOOP 2.8.3"),
    "KARMA": lambda: sloop_dev("FELUCCA KARMA 3.1"),
    "ORBIT": lambda: sloop_dev("FELUCCA ORBIT 1.0.0"),
    "sloopDX": lambda: sloop_dev("FELUCCA sloopDX 4.0.1"),
    "Clipper": lambda: sloop_dev("FELUCCA CLIPPER 1.3.1", {12: rnd(300, 1), 13: rnd(200, 2), 14: rnd(100, 3)}),
    "Synthesthetic": lambda: sloop_dev("FELUCCA SYNTHESTHETIC 0.2", {12: rnd(300, 1)}),
    "Jangada": lambda: sloop_dev("JANGADA 1.0.1"),
    "unnamed SLOOP fork": lambda: sloop_dev("FELUCCA 0.9 BETA"),
}
X0X_IMAGE = "X0X (image)"

# groups whose members take each other's backups (the same places, the same contents as far as the app can tell)
FELUCCA_13 = ["Felucca 1.8 (13)", "Ghoulbox (13)", "unknown name, Felucca's 13"]
FELUCCA_12 = ["Felucca 1.0 (12)", "Melodee 1.2 (12)"]
# (an unknown name with Melodee 1.6's exact inventory is taken for a Melodee build)
MELODEE_NEW = ["Melodee 1.5 (29)", "Melodee 1.6 (57)", "unknown name, Melodee's 57"]
SLOOP_LIKE = ["SLOOP", "Jangada", "unnamed SLOOP fork"]             # (SLOOP's inventory, SLOOP's layout)
SLOOP_READERS = ["KARMA", "Clipper", "Synthesthetic"]               # (fwfamily reads=("sloop",))

# backup -> the devices that take it (itself is always among them); everything else: refused
ACCEPT: dict[str, list[str]] = {}
for _a in FELUCCA_13:
    # Felucca's 13 objects: every firmware with Felucca's places – but NOT Melodee 1.5/1.6, where object 9 is the
    # preset bank U145–U162 and not Felucca's FM6 patches (CD-04)
    ACCEPT[_a] = FELUCCA_13 + FELUCCA_12 + ["unnamed fork, own inventory (23)"]
for _a in FELUCCA_12:
    # the 12 objects of Felucca 1.0 / Melodee 1.2: also Melodee 1.5/1.6 (the firmware's own legacy import)
    ACCEPT[_a] = FELUCCA_13 + FELUCCA_12 + ["unnamed fork, own inventory (23)"] + MELODEE_NEW
ACCEPT["Melodee 1.6 (57)"] = ["Melodee 1.6 (57)", "unknown name, Melodee's 57"]
ACCEPT["unknown name, Melodee's 57"] = ["Melodee 1.6 (57)", "unknown name, Melodee's 57"]
ACCEPT["Melodee 1.5 (29)"] = MELODEE_NEW
ACCEPT["JIANT 6 songs (35)"] = ["JIANT 6 songs (35)", "JIANT (43)"]
ACCEPT["ChoralRoot (34)"] = ["ChoralRoot (34)", "ChoralRoot old (28)"]     # (only empty objects the old one lacks)
# Switch Blade keeps Felucca's 0..7 (no 9): Felucca's places, so Melodee takes it and judges the content itself (the
# fakes here take any); Felucca's and Melodee 1.2's smaller recording memory refuses its running music's size
ACCEPT["Switch Blade (8)"] = ["Switch Blade (8)"] + MELODEE_NEW
ACCEPT["unnamed fork, own inventory (23)"] = ["unnamed fork, own inventory (23)"]
for _a in SLOOP_LIKE:
    ACCEPT[_a] = SLOOP_LIKE + SLOOP_READERS
for _a in ("Skiff 2 (29)", "JIANT (43)", "CoralFlower (26)", "ChoralRoot old (28)", "Tessera (30)", "Saadet", "DUELL",
           "Red Mass Vampire OS", "KARMA", "ORBIT", "sloopDX", "Clipper", "Synthesthetic"):
    ACCEPT[_a] = [_a]
ACCEPT[X0X_IMAGE] = []                               # (an X0X image only goes onto X0X: none of these devices)


def _archives() -> dict:
    out = {}
    for name, make in DEVICES.items():
        dev = make()
        out[name] = B.read(B.capture(link_for(dev)[0], dev.version))
    img = x0xbk.build([(i, rnd(100 + i, i)) for i in x0xbk.OBJ_IDS])
    out[X0X_IMAGE] = B.read(x0xbk.to_file(img, "X0X 1.2.1 BETA"))
    return out


@pytest.fixture(scope="module")
def archives():
    return _archives()


@pytest.fixture(scope="module")
def facts():
    """what each device answers: (protocol, INFO version, inventory, protocol.Info.firmware_name)"""
    from fm1companion import sloopbk
    out = {}
    for name, make in DEVICES.items():
        link = link_for(make())[0]
        out[name] = (*B.device_inventory(link), sloopbk.read_info(link).firmware_name)
    return out


def _writes(t) -> list:
    """frames that change the device: PUT data/commit (Felucca's 67, SLOOP's 36) and the sample commands"""
    out = []
    for fr in t.sent:
        if len(fr) > 5 and fr[1:4] == [0x7D, 0x46, 0x4C]:
            if fr[4] in (67, 36) and fr[5] in (1, 2) or fr[4] in (11, 12, 13, 14):
                out.append(fr[4:7])
    return out


def test_accept_table_is_complete():
    assert set(ACCEPT) == set(DEVICES) | {X0X_IMAGE}
    assert all(set(v) <= set(DEVICES) for v in ACCEPT.values())
    assert all(a in ACCEPT[a] for a in DEVICES)                     # every firmware takes its own backup


@pytest.mark.parametrize("backup_of", list(DEVICES) + [X0X_IMAGE])
def test_matrix_row(archives, backup_of):
    arch = archives[backup_of]
    wrong = []
    for name, make in DEVICES.items():
        dev = make()
        link, t = link_for(dev)
        dev.log.clear()
        t.sent.clear()
        try:
            B.restore(link, arch)
            got = True
        except B.BackupError:
            got = False
        want = name in ACCEPT[backup_of]
        if got != want:
            wrong.append(f"{backup_of} -> {name}: {'accepted' if got else 'refused'}, expected "
                         f"{'accepted' if want else 'refused'}")
        elif not got and (_writes(t) or [x for x in dev.log if isinstance(x, int)]):
            wrong.append(f"{backup_of} -> {name}: refused, but writes were sent first: {_writes(t)[:3]} {dev.log[:5]}")
    assert not wrong, "\n".join(wrong)


def test_felucca_13_onto_melodee_with_bank_9_is_refused_before_any_write(archives):
    """CD-04: Felucca's object 9 (FM6 patches) is Melodee's preset bank U145–U162 – also when the backup's 9 is empty
    (it would clear the bank), also for a partial restore"""
    arch = archives["Felucca 1.8 (13)"]
    for target in ("Melodee 1.5 (29)", "Melodee 1.6 (57)"):
        dev = DEVICES[target]()
        link, t = link_for(dev)
        before = dict(dev.objs)
        with pytest.raises(B.BackupError, match="falschen Stellen"):
            B.restore(link, arch)
        with pytest.raises(B.BackupError, match="falschen Stellen"):
            B.restore_selected(link, arch, [2])
        assert dev.objs == before and not _writes(t)
        # the pages' check says the same before a restore is planned
        rep = BI.inspect(arch)
        comp = BI.compatibility(rep, "melodee", dev.version, list(dev.ids), proto=B.FELUCCA)
        assert not comp.ok and "falschen Stellen" in comp.reason
    assert B.layout_reason(arch, B.KNOWN_ID_SETS[12], "MELODEE v1.2.0") == ""


# cells the pages must refuse themselves, before a restore is planned (a sample of every rule)
GATE_REFUSES = [("Felucca 1.8 (13)", "Melodee 1.6 (57)"), ("Ghoulbox (13)", "Melodee 1.5 (29)"),
                ("Saadet", "Felucca 1.8 (13)"), ("Felucca 1.8 (13)", "Saadet"), ("Tessera (30)", "Melodee 1.6 (57)"),
                ("Melodee 1.6 (57)", "Tessera (30)"), ("Skiff 2 (29)", "Felucca 1.8 (13)"), ("DUELL", "Felucca 1.8 (13)"),
                ("JIANT (43)", "Melodee 1.6 (57)"), ("CoralFlower (26)", "ChoralRoot (34)"),
                ("SLOOP", "sloopDX"), ("sloopDX", "SLOOP"), ("Clipper", "SLOOP"), ("KARMA", "SLOOP"), ("ORBIT", "SLOOP"),
                ("SLOOP", "ORBIT"), ("SLOOP", "Felucca 1.8 (13)"), ("Felucca 1.8 (13)", "SLOOP"),
                (X0X_IMAGE, "Felucca 1.8 (13)"), (X0X_IMAGE, "SLOOP")]


@pytest.mark.parametrize("backup_of", list(DEVICES) + [X0X_IMAGE])
def test_the_pages_gate_decides_like_the_restore(archives, facts, backup_of):
    """CD-03: BI.compatibility with the device's protocol and inventory uses the restore's own rules (protocol,
    layout): it never refuses a cell the restore takes, whatever firmware key the file or the device carries, and
    it refuses the cells of GATE_REFUSES itself. (It may pass a cell the restore refuses later for a part the
    device does not know or a size: those copies work part by part.)"""
    arch = archives[backup_of]
    rep = BI.inspect(arch)
    wrong = []
    for name, (proto, version, ids, fw_name) in facts.items():
        for key in (BI.inspect(archives[name]).firmware_key, "a-key-the-app-never-heard-of", ""):
            got = BI.compatibility(rep, key, version, ids, proto=proto).ok
            if name in ACCEPT[backup_of] and not got:
                wrong.append(f"{backup_of} -> {name} (key {key!r}): the restore takes it, the gate refuses")
            if (backup_of, name) in GATE_REFUSES and got:
                wrong.append(f"{backup_of} -> {name} (key {key!r}): the gate must refuse")
            if got and name not in ACCEPT[backup_of] and (B.match_reason(arch, proto, version, fw_name) or (
                    proto == B.FELUCCA and B.layout_reason(arch, ids, version))):
                wrong.append(f"{backup_of} -> {name} (key {key!r}): the gate passes what the restore's rules refuse")
    assert not wrong, "\n".join(wrong)


