# SPDX-License-Identifier: GPL-3.0-only
"""The newer FM-1 (logo on the front, flash UC25HQ80, JEDEC 0xB36014): the table of minimum versions
(fm1companion/data/hardware.json), the version comparisons, the device profile's revision, the installation plan's
rule (flash/planner._new_chip) and the flash ID the app can read (identity answer, ROM boot)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fm1companion import devices, firmware, hardware as H, studio            # noqa: E402
from fm1companion.flash import package as pk, planner as P, tested           # noqa: E402
from fm1companion.vendor import fm1_install as fi                            # noqa: E402

# firmware -> (first version with the chip, a version before it, a later one); read in the firmwares' repositories
ROWS = {
    "melodee": ("v1.6.0", "v1.5.1", "v1.6.1"),
    "skiff": ("v2.2", "v2.1.1", "v2.5.1"),
    "sloop": ("v2.8.5", "v2.8.4", "v2.8.6"),
    "karma": ("v3.0.4", "v3.0.3", "v3.1"),
    "jiant-fm": ("V10.6z", "V10.61", "V10.6z2"),
    "felucca": ("v1.8.2", "v1.8.1", "v1.9.0"),
    "choralroot-fm-1": ("v0.29.1", "v0.29", "v0.41"),
    "coralflower": ("v0.7", "v0.6.1", "v0.7.1"),
    "x0x": ("v1.2.1", "v1.2.0", "v1.2.2"),
    "fomni": ("v0.4.1", "v0.4", "v0.5"),
    "amb-1": ("v1.3.1", "v1.3", "v1.3.2"),
    "loom-8": ("v0.13", "v0.12.1", "v0.16.2"),
    "hortator": ("v1.0.1", "drum-v1.0.0", "drum-v1.0.2"),
    "transitfm1": ("v0.1.4", "v0.1.3", "v0.2"),
}


def test_table_is_whole_and_every_row_names_its_source():
    t = H.table()
    assert t["chips"]["B36014"]["revision"] == "new" and t["chips"]["856014"]["revision"] == "old"
    for key, row in t["firmwares"].items():
        assert row["status"] in ("min", "none", "risky", "documented", "unverified"), key
        assert row["source"].strip(), key
        assert (row["status"] == "min") == bool(row["min"]), key
        if row["status"] in ("min", "none", "risky"):
            assert row["repo"].count("/") == 1 and len(row["commit"]) >= 7, key
    assert set(ROWS) == {k for k, r in t["firmwares"].items() if r["status"] == "min"}
    for alias, key in t["aliases"].items():
        assert key in t["firmwares"], alias


@pytest.mark.parametrize("key", sorted(ROWS))
def test_minimum_version_per_firmware(key):
    first, before, after = ROWS[key]
    assert H.source(key)["min"] == first
    assert H.support(key, first) == H.Support(H.YES, first, H.source(key)["source"])
    assert H.support(key, before).status == H.NO and H.support(key, before).first_version == first
    assert H.support(key, after).status == H.YES
    assert H.support(key, "").status == H.UNKNOWN            # no version: never a guess
    assert H.support(key, "latest").status == H.UNKNOWN


def test_version_styles():
    # what the devices and the releases call their versions
    assert H.support("melodee", "MELODEE v1.6.0-rc.7").status == H.NO      # no release candidate of 1.6.0 has the fix
    assert H.support("melodee", "MELODEE v1.6.1").status == H.YES
    assert H.support("felucca", "FELUCCA v1.8.1").status == H.NO
    assert H.support("felucca", "v1.8.2-beta").status == H.YES             # the beta tag contains the fix
    assert H.support("sloop", "FELUCCA SLOOP 2.8.5").status == H.YES
    assert H.support("choralroot", "ChoralRoot 0.39").status == H.YES and H.canonical("choralroot") == "choralroot-fm-1"
    assert H.support("choralroot-fm-1", "v0.29.2-smk37").status == H.YES
    assert H.support("x0x", "X0X 1.2.0 BETA").status == H.NO
    assert H.support("hortator", "drum-v1.0.1").status == H.YES
    # JIANT: letters after V10.61, then the three-part V10.7.N; a bare V10.7 is two different builds
    for v, want in (("V10.60", H.NO), ("V10.61", H.NO), ("V10.6c", H.NO), ("V10.6y", H.NO), ("V10.6z", H.YES),
                    ("V10.6z2", H.YES), ("V10.6z10", H.YES), ("JIANT V10.7.16", H.YES), ("V10.7.48", H.YES),
                    ("V10.7", H.UNKNOWN), ("V10.10", H.NO), ("V11.0", H.YES), ("", H.UNKNOWN),
                    ("v10.60.0", H.NO), ("v10.61.0", H.NO), ("10.60.1", H.NO), ("10.6.1", H.UNKNOWN),
                    ("V10.6z-rc1", H.UNKNOWN), ("V10.9.1", H.YES)):
        assert H.support("jiant", v).status == want, v
        assert H.support("jiant-fm", v).status == want, v
    # the app's own ordering of JIANT's versions agrees where both can read the version
    assert studio.jiant_version_key("V10.6z") > studio.jiant_version_key("V10.61")
    assert H.first_version_text("jiant", "V10.6z") == "V10.6z" and H.first_version_text("melodee", "v1.6.0") == "1.6.0"


def test_rows_without_a_supporting_version():
    assert H.support("orbit", "v1.0.1B") == H.Support(H.NO, "", H.source("orbit")["source"])
    assert H.support("grimm", "v2.0.2").status == H.NO
    assert H.support("sloop3", "v3.3.7-beta").status == H.RISKY and H.support("m-bootloader", "2.4").status == H.RISKY
    assert H.support("mbootloader").status == H.RISKY
    assert H.support("sloop", "v3.3.7").status == H.RISKY        # SLOOP 3 is its own firmware, not a newer SLOOP 2
    assert H.support("stock-v15").status == H.YES and H.documented_only("stock-v15")
    assert H.support("stock-v16").status == H.UNKNOWN and H.support("fm1-open", "V14").status == H.UNKNOWN
    assert H.support("some-catalog-firmware", "v1.0") == H.Support(H.UNKNOWN, "", "")
    assert H.support("", "v1.0").status == H.UNKNOWN


def test_every_builtin_firmware_and_twin_has_a_row():
    """a firmware cannot be added to the app without deciding what it does on the newer chip"""
    rows = H.table()["firmwares"]
    for s in firmware.BUILTIN + firmware.STOCK:
        assert H.canonical(s.key) in rows, f"{s.key}: add a row to fm1companion/data/hardware.json"
    for key in studio.FIRMWARES:
        assert H.canonical(key) in rows, f"twin {key}: add a row to fm1companion/data/hardware.json"
    for d in sorted((ROOT / "fm1companion" / "sims").glob("*-studio")):
        assert H.canonical(d.name[:-len("-studio")]) in rows, d.name


def test_firmware_json_of_fm1_base():
    fj = json.dumps({"schema": 1, "id": "ctl", "hardware": {"models": ["FM-1"], "flash": ["856014", "b36014"]}})
    assert H.flash_ids(fj) == ["856014", "B36014"]
    assert H.flash_ids('{"hardware": {"flash": "B36014"}}') == [] and H.flash_ids("no json") == [] and H.flash_ids("{}") == []
    assert H.support("ctl-1", "v1.1", ["856014", "B36014"], "v1.1").status == H.YES
    assert H.support("ctl-1", "v1.2", ["856014", "B36014"], "v1.1").status == H.YES        # later: still
    assert H.support("ctl-1", "v1.0", ["856014", "B36014"], "v1.1").status == H.UNKNOWN    # older: not said
    assert H.support("ctl-1", "v1.1", ["856014"], "v1.1").status == H.NO
    assert H.support("ctl-1", "", ["856014", "B36014"], "v1.1").status == H.UNKNOWN
    assert H.support("melodee", "v1.5.0", ["B36014"], "v1.5.0").status == H.NO             # the table goes first


# ------------------------------------------------------------------------------------------------ the profile
def test_profile_keeps_the_revision(tmp_path):
    st = devices.ProfileStore(tmp_path / "devices.json")
    p = st.create("Neu")
    assert H.revision(p) == "unknown" and not H.asked(p) and H.revision(None) == "unknown"
    assert H.set_revision(p, H.NEW, "user") and H.revision(p) == "new" and H.asked(p)
    st.save()
    q = devices.ProfileStore(tmp_path / "devices.json").profiles[p.id]
    assert (q.hw_revision, q.hw_revision_source, q.flash_id) == ("new", "user", "")
    assert not H.set_revision(q, H.UNSURE, "user")                             # "new" only goes away deliberately
    assert H.set_revision(q, H.UNSURE, "user", force=True) and H.revision(q) == "unknown" and H.asked(q)
    assert H.note_flash_id(q, 0xB36014, "rom") and (q.hw_revision, q.hw_revision_source, q.flash_id) == ("new", "rom", "B36014")
    assert not H.set_revision(q, H.OLD, "user") and H.revision(q) == "new"     # an ID that was read stays
    # "new" is sticky: an earlier chip read under the same profile is kept as a conflict, the profile stays new
    assert H.note_flash_id(q, 0x856014, "identity") and H.revision(q) == "new" and q.flash_id == "B36014"
    assert q.hw_revision_conflict == "856014 identity" and "0x856014" in H.label(q)
    assert not H.note_flash_id(q, 0xEB6014, "rom") and H.revision(q) == "new"  # another chip: not one of the two
    assert not H.set_revision(q, "newest", "user")
    # an old profile file without the fields, and one with a wrong type
    (tmp_path / "old.json").write_text(json.dumps({"profiles": [{"id": "a", "name": "A"},
                                                                {"id": "b", "name": "B", "hw_revision": 5}]}))
    old = devices.ProfileStore(tmp_path / "old.json").profiles
    assert H.revision(old["a"]) == "unknown" and old["b"].hw_revision == ""
    assert H.revision_of_id("0xB36014") == "new" and H.revision_of_id("856014") == "old" and H.revision_of_id("zz") == "unknown"


# ------------------------------------------------------------------------------------------------ the plan
def state(rev, identity="FM-1_015", **kw):
    return P.DeviceState(identity=identity, revision=rev, **kw)


def target(key, tag):
    return P.Target.from_spec(firmware.spec(key), tag=tag)


def cat_target(key="some-firmware", tag="v1.0", **kw):
    kw.setdefault("repo", H.source(key).get("repo") or "a/b")      # (a table row only applies to its own repository)
    sp = firmware.FirmwareSpec(key=key, name="Some Firmware", asset="x-{ver}.fwsc", builtin=False,
                               loader_family="feluccaG1", **kw)
    return P.Target.from_spec(sp, tag=tag)


def own_target(official=""):
    info = pk.PackageInfo.__new__(pk.PackageInfo)
    info.__dict__.update(path=Path("eigen.fwsc"), product="FM-1_015" if official == "FM-1_015" else
                         "FM-1_016" if official else "FM-1_777", sha256="ab" * 32, family="stock" if official else "felucca",
                         official=official, model="FM-1", damage="", flash_bytes=0x93000 if official != "FM-1_016" else 0xA9000)
    return P.Target.from_file(info)


CASES = [   # revision, target, -> blocked?, confirmations it must add
    ("new", lambda: target("melodee", "v1.6.1"), False, set()),
    ("old", lambda: target("melodee", "v1.5.1"), False, set()),
    ("", lambda: target("melodee", "v1.6.1"), False, set()),
    ("new", lambda: target("melodee", "v1.5.1"), True, set()),
    ("", lambda: target("melodee", "v1.5.1"), False, {"no_logo"}),
    ("new", lambda: target("melodee", "v1.6.0-rc.7"), True, set()),
    ("new", lambda: target("sloop", "v2.8.4"), True, set()),
    ("new", lambda: target("sloop", "v2.8.5"), False, set()),
    ("new", lambda: target("karma", "v3.0.3"), True, set()),
    ("new", lambda: target("skiff", ""), True, set()),                       # version not known
    ("", lambda: target("skiff", ""), False, {"no_logo"}),
    ("old", lambda: target("skiff", ""), False, set()),
    ("new", lambda: cat_target(), True, set()),                              # catalog firmware outside the table
    ("", lambda: cat_target(), False, {"no_logo"}),
    ("old", lambda: cat_target(), False, set()),
    ("new", lambda: cat_target("orbit", "v1.0.1"), True, set()),
    ("new", lambda: cat_target("sloop3", "v3.3.7-beta"), True, set()),
    ("", lambda: cat_target("m-bootloader", "v2.4"), False, {"no_logo"}),
    ("new", lambda: cat_target(flash=["856014", "B36014"], flash_tag="v1.0"), False, set()),
    ("new", lambda: cat_target(flash=["856014"], flash_tag="v1.0"), True, set()),
    ("new", lambda: target("stock-v15", "V15"), False, set()),
    ("new", lambda: target("stock-v16", "V16"), False, {"newchip_unverified"}),
    ("", lambda: target("stock-v16", "V16"), False, {"newchip_unverified"}),
    ("old", lambda: target("stock-v16", "V16"), False, set()),
]


@pytest.mark.parametrize("n", range(len(CASES)))
def test_plan_rule(n):
    rev, make, blocked, confirm = CASES[n]
    p = P.plan_install(state(rev), make())
    chip_block = [b for b in p.blockers if "UC25HQ80" in b]
    assert bool(chip_block) == blocked, p.blockers
    assert {c for c in p.confirmations if c in ("no_logo", "newchip_unverified")} == confirm
    assert P.chip_matters(state(rev), make()) == (blocked or bool(confirm))
    if blocked:
        assert not p.ok and "no_logo" not in p.confirmations
    assert bool({"new_chip", "new_chip_skiff"} & set(tested.plan_kinds(p))) == (rev == "new")


def test_plan_names_the_first_version_and_has_no_override():
    p = P.plan_install(state("new"), target("melodee", "v1.5.1"), via_v15=False, backup=False)
    b = [x for x in p.blockers if "UC25HQ80" in x]
    assert len(b) == 1 and "1.6.0" in b[0] and "Melodee 1.5.1" in b[0]
    b = [x for x in P.plan_install(state("new"), target("jiant-fm", "V10.61") if firmware.spec("jiant-fm") else
                                   cat_target("jiant-fm", "V10.61")).blockers if "UC25HQ80" in x]
    assert b and "V10.6z" in b[0]
    b = [x for x in P.plan_install(state("new"), cat_target("orbit", "v1.0.1")).blockers if "UC25HQ80" in x]
    assert b and H.table()["checked"] in b[0]
    # unknown revision: the warning comes first, the plan stays possible only with the confirmation
    p = P.plan_install(state(""), target("melodee", "v1.5.1"))
    assert p.ok and "no_logo" in p.missing([]) and any("1.6.0" in w and "<b>" in w for w in p.warnings) and "no_logo" not in p.missing(["no_logo", "power", "no_backup"])
    assert "no_logo" in P.CONFIRM_TEXT and "newchip_unverified" in P.CONFIRM_TEXT


def test_plan_switch_v15_step_downgrade_chain_and_resume():
    mel15 = dict(identity="FM-1_900", felucca=True, firmware_key="melodee", firmware_name="Melodee", version="MELODEE v1.6.1")
    # downgrade within Melodee on a newer unit: blocked, although it is "the same firmware"
    p = P.plan_install(state("new", **mel15), target("melodee", "v1.5.1"))
    assert p.v15_option == "none" and any("UC25HQ80" in b for b in p.blockers)
    # switch Melodee -> SLOOP 2.8.5 on a newer unit: allowed, and the V15 step carries the honest note
    p = P.plan_install(state("new", **mel15), target("sloop", "v2.8.5"))
    assert p.via_stock and p.ok and P.NEWCHIP_V15 in p.warnings
    p = P.plan_install(state("old", **mel15), target("sloop", "v2.8.5"))
    assert p.via_stock and P.NEWCHIP_V15 not in p.warnings
    # … to a SLOOP that does not know the chip: blocked, with or without the V15 step
    for via in (None, False):
        p = P.plan_install(state("new", **mel15), target("sloop", "v2.8.4"), via_v15=via)
        assert any("2.8.5" in b for b in p.blockers)
    # back to the original V15 on a newer unit: possible, with the note; unknown unit: no extra step
    p = P.plan_install(state("new", **mel15), target("stock-v15", "V15"))
    assert p.ok and P.NEWCHIP_V15 in p.warnings and "newchip_unverified" not in p.confirmations
    p = P.plan_install(state("", **mel15), target("stock-v15", "V15"))
    assert P.NEWCHIP_V15 not in p.warnings and not {"no_logo", "newchip_unverified"} & set(p.confirmations)
    # "Continue" after the V15 step
    p = P.plan_install(state("new"), target("sloop", "v2.8.4"), chain={"key": "sloop"})
    assert p.chain_resume and any("UC25HQ80" in b for b in p.blockers)
    p = P.plan_install(state("new"), target("sloop", "v2.8.5"), chain={"key": "sloop"})
    assert p.chain_resume and p.ok
    # finishing an interrupted installation (update mode)
    p = P.plan_install(state("new", identity="ota-FM-1_900"), target("melodee", "v1.5.1"))
    assert p.resume and any("UC25HQ80" in b for b in p.blockers)
    p = P.plan_install(state("", identity="ota-FM-1_900"), target("melodee", "v1.5.1"))
    assert p.resume and "no_logo" in p.confirmations
    p = P.plan_install(state("new", identity="ota-FM-1_900"), target("melodee", "v1.6.1"))
    assert p.resume and not any("UC25HQ80" in b for b in p.blockers)


def test_plan_own_file():
    for rev, want in (("new", True), ("", True), ("old", False)):
        p = P.plan_install(state(rev), own_target())
        assert ("newchip_unverified" in p.confirmations) == want and not any("UC25HQ80" in b for b in p.blockers)
        assert "unknown_file" in p.confirmations
    assert "newchip_unverified" not in P.plan_install(state("new"), own_target("FM-1_015")).confirmations
    assert "newchip_unverified" in P.plan_install(state("new", identity="FM-1_015"), own_target("FM-1_016")).confirmations
    # a file that is a checked release of a table firmware follows the table
    t = target("melodee", "v1.5.1")
    t.known = True
    assert any("UC25HQ80" in b for b in P.plan_install(state("new"), t).blockers)


def test_running_version_without_support_is_explained():
    old = dict(identity="FM-1_900", felucca=True, firmware_key="melodee", firmware_name="Melodee", version="MELODEE v1.5.1")
    text = H.running_warning("Melodee", H.support("melodee", "v1.5.1"), "melodee")
    assert "1.6.0" in text and "V15" in text and "Melodee" in text and "ROM" in text
    p = P.plan_install(state("new", **old), target("melodee", "v1.6.1"))
    assert p.ok and p.warnings[0] == text
    for rev in ("", "old"):
        assert text not in P.plan_install(state(rev, **old), target("melodee", "v1.6.1")).warnings
    new = dict(old, version="MELODEE v1.6.1")
    assert not any(w.startswith("<b>") and "ROM" in w
                   for w in P.plan_install(state("new", **new), target("melodee", "v1.6.1")).warnings)
    # Skiff answers Felucca's INFO with Felucca's number: no statement about the running version
    assert H.running_support("skiff", "FELUCCA v1.8.1") is None and H.running_support("felucca", "FELUCCA v1.8.1") is None
    assert H.running_support("sloop", "FELUCCA SLOOP 2.8.4").status == H.NO and H.running_support("sloop", "") is None
    assert H.running_support("jiant", "JIANT V10.60").status == H.NO and H.running_support("karma", "FELUCCA KARMA 3.1").status == H.YES
    skiff = dict(identity="FM-1_712", felucca=True, firmware_key="skiff", firmware_name="Skiff", version="FELUCCA v1.8.1")
    assert not any(x.startswith("<b>") and "ROM" in x for x in P.plan_install(state("new", **skiff), target("skiff", "v2.5.1")).warnings)
    w = H.running_warning("ORBIT", H.support("orbit", "1.0"), "orbit")
    assert "ORBIT" in w and "V15" in w


def test_chip_check_for_the_recovery():
    """Recovery › finish installation has no plan dialog: the same rule, alone"""
    c = P.chip_check("new", target("melodee", "v1.5.1"))
    assert c.blockers and not c.confirmations
    c = P.chip_check("", target("melodee", "melodee-1.5.1.fwsc"))            # the version from a file name or label
    assert not c.blockers and c.confirmations == ["no_logo"] and c.warnings
    c = P.chip_check("new", target("melodee", "Melodee v1.6.1"))
    assert not c.blockers and not c.confirmations
    c = P.chip_check("new", target("stock-v15", "V15"))
    assert not c.blockers and not c.confirmations and c.warnings == [P.NEWCHIP_V15]
    assert P.chip_check("old", own_target()).confirmations == [] and P.chip_check("", own_target()).confirmations == ["newchip_unverified"]


# ------------------------------------------------------------------------------------------------ reading the ID
def ident_packet(text: str, flash: int = 0, junk: bytes = b"") -> bytes:
    """the identity answer as felucca-bsp's ota.c builds it: 27 text bytes, bytes 24..26 the JEDEC ID"""
    t = bytearray(text.encode("latin-1").ljust(27, b"\0"))
    if flash:
        t[24:27] = flash.to_bytes(3, "big")
    if junk:
        t[len(text) + 1:len(text) + 1 + len(junk)] = junk
    d = b"\x00\x59\x11" + bytes(3) + bytes(t) + b"\0"
    return bytes([0xF0]) + fi.pack7(d) + bytes([0xF7])


def test_identity_answer_with_the_flash_id():
    """fm1-base 19bf320 (AMB-1 1.3.1, LOOM-8 0.13 …): before, such an answer was taken for no FM-1 at all"""
    for text in ("FM-1_701", "FM-1_015", "ota-FM-1_900", "FM1_OFFICIAL_OPEN"):
        plain = fi.parse_identity(ident_packet(text))
        assert plain is not None and plain.text == text and plain.flash == 0
        for fid in (0xB36014, 0x856014):
            i = fi.parse_identity(ident_packet(text, fid))
            assert i is not None and i.text == text and i.flash == fid and i.version == plain.version
    assert fi.parse_identity(ident_packet("FM-1_701", 0xB36014)).loader is False
    assert fi.parse_identity(ident_packet("FM-1_701", junk=b"x")) is None          # something else than zeros
    long = "FM-1_" + "9" * 22                                                       # 27 characters: no room for an ID
    assert fi.parse_identity(ident_packet(long)).text == long and fi.parse_identity(ident_packet(long)).flash == 0


def test_rescue_reads_the_flash_id(tmp_path):
    from fm1companion.flash import uboot, uboot_manifest
    from fm1companion.vendor.unbricker import fm1_unbrick as fu
    assert fu.FLASH_IDS_OK == (0x856014, 0xB36014) == uboot.KNOWN_FLASH_IDS and fu.TOOL_VERSION == "1.2.1"
    import hashlib
    for name, want in uboot_manifest.UNBRICKER.items():
        assert hashlib.sha256((uboot.VENDOR / name).read_bytes()).hexdigest() == want, name
    assert H.flash_id_in("Flash ID    0xB36014 (expected 0x856014 or 0xB36014, 1 MiB), type 0x03") == 0xB36014
    assert H.flash_id_in("OK key=980F type=3 id=856014") == 0x856014 and H.flash_id_in("nothing") == 0
    for line, fid, real in (("Flash ID    0xB36014 (expected 0x856014 or 0xB36014, 1 MiB), type 0x03", 0xB36014, True),
                            ("Info        OK key=980F type=3 id=856014", 0x856014, True),
                            ("Flash ID    0xEF4014 (expected 0x856014 or 0xB36014, 1 MiB), type 0x03", 0xEF4014, True),
                            ("this is not an id line: 0xB36014", 0, True),
                            ("Flash ID    0xB36014 (expected 0x856014 or 0xB36014, 1 MiB), type 0x03", 0xB36014, False)):
        tool = tmp_path / "tool.py"
        tool.write_text(f"print({line!r})\n")
        seen, logs = [], []
        r = uboot.Runner(dry_run=True, log=logs.append, cwd=tmp_path, on_flash=seen.append)
        r.dry_run = not real                       # (dry_run=True only keeps pkexec out of the command line)
        argv = r.argv
        r.argv = lambda *a, **k: [sys.executable, "-I", str(tool)]
        assert r.run("info", script=tool) == 0 and r.flash_id == fid
        assert seen == ([fid] if fid and real else [])          # a simulation's chip is made up: never recorded
        assert bool(r.hint) == (fid == 0xB36014)
        del argv


def test_tested_table_says_nothing_ran_on_a_newer_unit():
    assert tested.STEPS["new_chip"][0] is False
    text = (ROOT / "docs" / "UNTESTED.md").read_text("utf-8")
    assert "`new_chip`" in text and "one installation ran on one newer unit" in text
    assert "**not** a test of the public V15 package" in text          # the unit as delivered was only looked at
    assert tested.STEPS["new_chip_skiff"][0] is True                   # one run: Skiff 2.7, one unit (2026-11-18)


# ================================================================================================ review fixes
# (audit/review-newrev.md A1–A10, B1–B3: the revision state and the inputs of the rule)
def prof(**kw):
    p = devices.Profile(id=kw.pop("id", "p1"), name=kw.pop("name", "P"))
    for k, v in kw.items():
        setattr(p, k, v)
    return p


def test_new_is_sticky_and_the_strictest_reading_wins():                       # A1, A2
    p = prof()
    assert H.set_revision(p, H.NEW, "user")
    # a read "old" (another FM-1 under this profile) never replaces "new": kept as a conflict, shown
    assert H.note_flash_id(p, 0x856014, "rom") and H.revision(p) == "new" and p.hw_revision_source == "user"
    assert p.hw_revision_conflict == "856014 rom" and "0x856014" in H.label(p)
    assert H.note_flash_id(p, 0x856014, "identity") and H.revision(p) == "new"
    assert not H.set_revision(p, H.OLD, "user") and not H.set_revision(p, H.UNSURE, "user")
    assert not H.set_revision(p, "", "user") and H.revision(p) == "new"
    # rom NEW, then identity OLD
    p = prof()
    assert H.note_flash_id(p, 0xB36014, "rom") and H.note_flash_id(p, 0x856014, "identity")
    assert (p.hw_revision, p.hw_revision_source, p.flash_id) == ("new", "rom", "B36014")
    # a read "old", then anything "new": new wins, the old statement is kept as the conflict
    p = prof()
    assert H.note_flash_id(p, 0x856014, "rom") and H.revision(p) == "old"
    assert not H.set_revision(p, H.UNSURE, "user")                              # an answer does not replace a read …
    assert H.set_revision(p, H.NEW, "user") and H.revision(p) == "new"          # … except "newer unit"
    assert p.hw_revision_conflict == "856014 rom"
    p = prof()
    H.note_flash_id(p, 0x856014, "identity")
    assert H.note_flash_id(p, 0xB36014, "rom") and (p.hw_revision, p.flash_id) == ("new", "B36014")
    # the deliberate way out: only with force (the UI asks for a confirmation), also for a read value
    assert H.set_revision(p, H.OLD, "user", force=True) and H.revision(p) == "old" and not p.hw_revision_conflict
    assert H.note_flash_id(p, 0xB36014, "identity") and H.forget(p)
    assert (p.hw_revision, p.hw_revision_source, p.flash_id, p.hw_revision_conflict) == ("", "", "", "")
    assert H.strictest("old", "new") == "new" and H.strictest("old", "unknown") == "unknown"
    assert H.strictest("old", "old") == "old" and H.strictest() == "unknown" and H.strictest("old", "") == "unknown"


def test_merge_keeps_the_strictest_revision():                                  # A8
    src, dst = prof(id="s"), prof(id="d")
    H.note_flash_id(src, 0xB36014, "rom")
    H.set_revision(dst, H.OLD, "user")
    assert H.merge_revision(dst, src)
    assert (dst.hw_revision, dst.hw_revision_source, dst.flash_id) == ("new", "rom", "B36014")
    assert dst.hw_revision_conflict == "old user"
    src, dst = prof(id="s"), prof(id="d")
    H.set_revision(src, H.NEW, "user")
    assert H.merge_revision(dst, src) and H.revision(dst) == "new" and H.asked(dst)
    src, dst = prof(id="s"), prof(id="d")
    H.set_revision(src, H.OLD, "user")
    H.note_flash_id(dst, 0xB36014, "identity")
    H.merge_revision(dst, src)
    assert H.revision(dst) == "new" and dst.flash_id == "B36014" and dst.hw_revision_conflict == "old user"
    src, dst = prof(id="s"), prof(id="d")
    H.note_flash_id(src, 0x856014, "rom")
    H.set_revision(dst, H.UNSURE, "user")
    H.merge_revision(dst, src)
    assert (dst.hw_revision, dst.hw_revision_source, dst.flash_id) == ("old", "rom", "856014")
    src, dst = prof(id="s"), prof(id="d")
    assert not H.merge_revision(dst, src) and not H.asked(dst)


PRE = ["v1.6.0-rc", "v1.6.0-beta", "v1.6.0-pre", "v1.6.0-dev", "v1.6.0-nightly", "v1.6.0-preview.2", "v1.6.0_rc1",
       "v1.6.0~rc1", "1.6.0b1", "v1.6.0-rc.8", "v1.6.0rc8", "Melodee 1.6.0 rc7", "v1.6.0+build5", "v1.6.0.dev1"]


@pytest.mark.parametrize("tag", PRE)
def test_a_prerelease_of_the_minimum_is_not_the_minimum(tag):                   # A5
    assert H.support("melodee", tag).status == H.NO, tag


def test_version_suffixes():                                                    # A5
    assert H.support("melodee", "v1.6.0").status == H.YES and H.support("melodee", "v1.6.1-rc.1").status == H.YES
    assert H.support("felucca", "v1.8.2-beta").status == H.YES                  # listed in the row ("also")
    assert H.support("felucca", "v1.8.2-rc1").status == H.NO and H.support("felucca", "v1.8.2-BETA").status == H.YES
    assert H.support("x0x", "X0X 1.2.1 BETA").status == H.YES                   # the release's INFO: a name, no suffix
    assert H.support("choralroot-fm-1", "v0.29.1-smk37").status == H.NO         # a variant of the minimum: not it
    assert H.support("sloop", "v3.0-beta").status == H.RISKY and H.support("sloop", "v3.0.0-rc.1").status == H.RISKY
    assert H.parse_version("drum-v1.0.1") == ((1, 0, 1), "") and H.parse_version("v1.6.0-rc.7") == ((1, 6, 0), "-rc.7")
    assert H.parse_version("nightly") is None


def test_a_table_row_only_applies_to_its_own_repository():                      # A6
    assert H.support("felucca", "v2.0", repo="someone/Felucca").status == H.UNKNOWN
    assert H.support("felucca", "v2.0", repo="Hugelton/felucca").status == H.YES
    assert H.support("felucca", "v2.0", repo="").status == H.UNKNOWN
    assert H.support("felucca", "v2.0").status == H.YES                         # repo None: the caller vouches
    assert H.support("orbit", "v1.0", repo="x/orbit").status == H.UNKNOWN       # (not "no" either: not that firmware)
    # a fork's firmware.json is not believed under a table key either
    assert H.support("felucca", "v2.0", ["B36014"], "v2.0", repo="someone/Felucca").status == H.UNKNOWN
    for alias in ("amb", "loom", "transit"):
        assert not H.source(alias), alias
    fork = firmware.FirmwareSpec(key="felucca", name="Felucca", repo="someone/Felucca", asset="f-{ver}.fwsc",
                                 builtin=False, loader_family="feluccaG1")
    p = P.plan_install(state("new"), P.Target.from_spec(fork, tag="v2.0"))
    assert any("UC25HQ80" in b for b in p.blockers)
    real = firmware.FirmwareSpec(key="felucca", name="Felucca", repo="hugelton/Felucca", asset="f-{ver}.fwsc",
                                 builtin=False, loader_family="feluccaG1")
    assert not any("UC25HQ80" in b for b in P.plan_install(state("new"), P.Target.from_spec(real, tag="v1.8.2")).blockers)
    for s in firmware.BUILTIN:                                                  # the built-in specs are the rows' own
        assert H.source(s.key)["repo"].lower() == s.repo.lower(), s.key


def test_firmware_json_needs_readable_versions_of_one_series():                 # A7
    ids = ["856014", "B36014"]
    assert H.support("fw", "drum-v0.5.0", ids, "drum-v1.0.1").status == H.UNKNOWN
    assert H.support("fw", "v0.5.0", ids, "drum-v1.0.1").status == H.UNKNOWN
    assert H.support("fw", "build-7", ids, "build-9").status == H.UNKNOWN
    assert H.support("fw", "nightly", ids, "release-2").status == H.UNKNOWN
    assert H.support("fw", "v1.1", ids, "drum-v1.0.1").status == H.UNKNOWN      # another tag series
    assert H.support("fw", "drum-v1.0.1", ids, "drum-v1.0.1").status == H.YES
    assert H.support("fw", "drum-v1.1.0", ids, "drum-v1.0.1").status == H.YES
    assert H.support("fw", "v1.1.0-rc1", ids, "v1.1.0").status == H.UNKNOWN     # a pre-release of that version


def test_the_version_never_comes_from_a_name():                                 # A4, B1
    t = target("melodee", "")
    t.name = "melodee-1.6.1.fwsc"
    assert P.chip_support(t).status == H.UNKNOWN
    assert any("UC25HQ80" in b for b in P.plan_install(state("new"), t).blockers)
    sp = firmware.FirmwareSpec(key="x0x", name="X0X 2.0", repo="charlesvestal/fm1-x0x", asset="x.fwsc", builtin=False,
                               loader_family="feluccaG1")
    assert P.chip_support(P.Target.from_spec(sp)).status == H.UNKNOWN
    src = (ROOT / "fm1companion" / "ui" / "firmware_page.py").read_text("utf-8")
    body = src[src.index("def _install_local"):src.index("def _show_v15_cached")]
    assert "tag = sp.release_tag or \"\"" in body and "re.search" not in body
    rec = (ROOT / "fm1companion" / "ui" / "pages" / "recovery.py").read_text("utf-8")
    gate = rec[rec.index("def _chip_gate"):rec.index("def _runner")]
    assert "Path(src).name" not in gate


def test_stock_family_targets_other_than_the_two_originals_are_blocked():       # A9
    for key, fam, lf in (("stockholm", "", ""), ("fm1-open-community", "stock", ""), ("some-build", "", "stock")):
        sp = firmware.FirmwareSpec(key=key, name=key, repo="a/b", asset="x.fwsc", builtin=False, family=fam,
                                   loader_family=lf, product="FM-1_950")
        t = P.Target.from_spec(sp, tag="v1.0")
        p = P.plan_install(state("new"), t)
        assert any("UC25HQ80" in b for b in p.blockers) and "newchip_unverified" not in p.confirmations, key
        assert "no_logo" in P.plan_install(state(""), P.Target.from_spec(sp, tag="v1.0")).confirmations
    # a spec that only calls itself stock-v16 / carries its product is not M-VAVE's package
    fake = firmware.FirmwareSpec(key="stock-v16", name="V16", repo="x/y", asset="x.fwsc", builtin=False,
                                 family="stock", product="FM-1_016")
    assert any("UC25HQ80" in b for b in P.plan_install(state("new"), P.Target.from_spec(fake, tag="V16")).blockers)
    assert P.official_stock(target("stock-v16", "V16")) == "FM-1_016" and P.official_stock(own_target("FM-1_015")) == "FM-1_015"
    assert "newchip_unverified" in P.plan_install(state("new"), target("stock-v16", "V16")).confirmations


def test_catalog_specs_carry_the_catalog_version():                             # A10
    from fm1companion import catalog
    cat = catalog.load()
    e = cat.by_key("x0x")
    sp = e.to_spec()
    assert sp.release_tag == e.latest.version and sp.release_tag
    rel = firmware.fetch_releases(sp)[0]
    assert rel.tag == sp.release_tag != "fest"
    t = P.Target.from_spec(sp, tag=rel.tag)
    assert P.chip_support(t).status in (H.YES, H.NO)                            # judged by its version, not "unknown"
    assert P.chip_support(t).status == H.support("x0x", e.latest.version).status
    # a catalog package of a supporting version installs on a newer unit; without a version it stays blocked
    import dataclasses
    ok = dataclasses.replace(sp, release_tag="v1.2.1")
    assert not any("UC25HQ80" in b for b in P.plan_install(state("new"), P.Target.from_spec(ok, tag="v1.2.1")).blockers)
    assert any("UC25HQ80" in b for b in P.plan_install(state("new"), P.Target.from_spec(sp, tag="")).blockers)


def test_table_errors_fail_closed(tmp_path, monkeypatch):                       # B2, B3
    def use(text):
        f = tmp_path / "hardware.json"
        f.write_text(text) if text is not None else f.unlink(missing_ok=True)
        monkeypatch.setattr(H, "TABLE", f)
        H.table.cache_clear()
    try:
        good = json.loads((ROOT / "fm1companion" / "data" / "hardware.json").read_text("utf-8"))
        bad = json.loads(json.dumps(good))
        bad["firmwares"]["melodee"]["min"] = ""
        use(json.dumps(bad))
        assert H.support("melodee", "v9.9").status == H.UNKNOWN                 # a row without its minimum: never yes
        assert any("UC25HQ80" in b for b in P.plan_install(state("new"), target("melodee", "v9.9")).blockers)
        for text in (None, "{not json", "[]", '{"firmwares": 5}', '{"firmwares": {"melodee": 1}, "aliases": {}, "checked": ""}'):
            use(text)
            assert H.table_problem()
            with pytest.raises(H.TableError):
                H.table()
            assert H.support("melodee", "v1.6.1") == H.Support(H.UNKNOWN, "", "") and H.source("melodee") == {}
            assert H.running_support("melodee", "MELODEE v1.5.1").status == H.UNKNOWN
            for rev in ("new", ""):
                p = P.plan_install(state(rev), target("melodee", "v1.6.1"))
                assert not p.ok and any("hardware.json" in b for b in p.blockers), (text, rev)
            assert P.plan_install(state("old"), target("melodee", "v1.6.1")).ok  # an earlier unit needs no table
            assert P.chip_matters(state("new"), target("melodee", "v1.6.1"))
    finally:
        monkeypatch.undo()
        H.table.cache_clear()
    assert not H.table_problem() and H.support("melodee", "v1.6.1").status == H.YES
