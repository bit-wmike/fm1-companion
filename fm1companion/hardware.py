# SPDX-License-Identifier: GPL-3.0-only
"""The two hardware revisions of the FM-1 and which firmware version knows the newer one's flash chip.

FM-1s sold from late 2026 ("the ones with the M-VAVE logo on the front") carry a UCUN UC25HQ80 (JEDEC ID 0xB36014)
instead of the Puya P25Q80H (0x856014). A firmware that does not know the ID keeps nothing and – worse – can no longer
start an update: the unit is stuck until the original firmware is written back through the chip's ROM boot.

The knowledge is data: fm1companion/data/hardware.json (one row per firmware, with the source). The API:

    support(firmware_key, version="", flash=(), flash_tag="") -> Support(status, first_version, note)
        status  "yes"      this version accepts the newer chip (or: the original V15, which has no such check and is
                           the documented way back – see note)
                "no"       it does not: on a newer unit it would not save and could not be updated any more
                "risky"    its code accepts the chip, but it is unproven there or a unit did not start (m-bootloader,
                           SLOOP 3) – treat as "no" for a newer unit
                "unknown"  nobody knows (not in the table, no version, an own file, the original V16)
        first_version      the first version that accepts the chip ("" = none known)
        note               one English sentence from the table (source), for logs and docs – not translated
    revision(profile) -> "new" | "old" | "unknown"
    source(firmware_key) -> the table row (repo, commit, source, before) or {}

flash/planner.py turns this into blockers and confirmations; nothing here was run on a newer FM-1.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

from .i18n import N_, _

OLD_ID, NEW_ID = 0x856014, 0xB36014
OLD_CHIP, NEW_CHIP = "Puya P25Q80H", "UCUN UC25HQ80"
NEW, OLD, UNKNOWN = "new", "old", "unknown"
UNSURE = "unsure"                    # stored when the owner was asked and does not know (not asked again)
TABLE = Path(__file__).resolve().parent / "data" / "hardware.json"

YES, NO, RISKY = "yes", "no", "risky"


class Support(NamedTuple):
    status: str            # yes | no | risky | unknown
    first_version: str     # "" = none known
    note: str


@lru_cache(maxsize=1)
def table() -> dict:
    return json.loads(TABLE.read_text("utf-8"))


def canonical(key: str) -> str:
    """a firmware key as the table writes it ("jiant" -> "jiant-fm", "choralroot" -> "choralroot-fm-1")"""
    k = (key or "").strip().lower()
    if not k:
        return ""
    t = table()
    if k in t["firmwares"]:
        return k
    if k in t["aliases"]:
        return t["aliases"][k]
    from . import fwfamily
    f = fwfamily.by_key(k)
    return f.key if f is not None else k


def source(key: str) -> dict:
    return dict(table()["firmwares"].get(canonical(key)) or {})


# ------------------------------------------------------------------------------------------------ versions
_JIANT = re.compile(r"(?<![\w.])[vV]?(\d+)\.(\d+)(?:\.(\d+))?([a-zA-Z]+\d*)?")


def _jiant_at_least(version: str, minimum: str) -> bool | None:
    """JIANT's order (studio.jiant_version_key): … V10.61 < V10.6c < … < V10.6z < V10.6z2 … < V10.6z10, then the
    three-part V10.7.N (from 2026-11-10). A bare "V10.7" is two different firmwares (2026-10-31, before V10.6z, and
    the beta of 2026-11-08, after it): None."""
    from . import studio
    m = _JIANT.search(version or "")
    if not m:
        return None
    a, b, c, suf = int(m[1]), int(m[2]), m[3], m[4]
    if c is not None:                               # V10.7.16 … V10.7.48: all after V10.6z10
        return (a, b) >= (10, 7)
    if (a, b) == (10, 7) and not suf:
        return None
    if a > 10:
        return True
    return studio.jiant_version_key(version) >= studio.jiant_version_key(minimum)


def at_least(key: str, version: str, minimum: str) -> bool | None:
    """is version >= minimum in that firmware's own release order? None: the version cannot be read"""
    from . import studio
    row = table()["firmwares"].get(canonical(key)) or {}
    if row.get("version_style") == "jiant":
        return _jiant_at_least(version, minimum)
    if not studio._VER.search(version or ""):
        return None
    # a pre-release of the first version does not count as that version (Melodee 1.6.0-rc.N has no support)
    return studio.version_key(version) >= studio.version_key(minimum)


def flash_ids(firmware_json) -> list[str]:
    """`hardware.flash` of a firmware.json (fm1-base docs/FIRMWARE-JSON.md): the JEDEC IDs the firmware can save to
    and update on, upper-case hex. [] = not said."""
    try:
        d = json.loads(firmware_json) if isinstance(firmware_json, (str, bytes)) else firmware_json
        ids = d["hardware"]["flash"]
    except (ValueError, KeyError, TypeError):
        return []
    if not isinstance(ids, list):
        return []
    return [x.upper() for x in ids if isinstance(x, str) and re.fullmatch(r"[0-9A-Fa-f]{6}", x)]


def support(firmware_key: str, version: str = "", flash=(), flash_tag: str = "") -> Support:
    """does this firmware version accept the newer FM-1's flash chip? See the module text.
    flash / flash_tag: the JEDEC IDs a firmware's own firmware.json names and the version it was read at – used for a
    firmware outside the table, and only for that version or a later one."""
    row = table()["firmwares"].get(canonical(firmware_key))
    if row is None:
        ids = [str(x).upper() for x in (flash or ())]
        if ids and flash_tag and version:
            from . import firmware
            if firmware.version_key(version)[:3] >= firmware.version_key(flash_tag)[:3]:
                note = "firmware.json hardware.flash at " + flash_tag + ": " + ", ".join(ids)
                return Support(YES if f"{NEW_ID:06X}" in ids else NO, "", note)
        return Support(UNKNOWN, "", "")
    if row.get("until") and version and at_least(firmware_key, version, row["until"]):
        return support(row["then"], version)        # SLOOP 3 is another firmware than SLOOP 2 (its own row)
    st, first, note = row["status"], row.get("min", ""), row.get("source", "")
    if st == "min":
        ok = at_least(firmware_key, version, first) if version else None
        return Support(UNKNOWN if ok is None else YES if ok else NO, first, note)
    if st == "none":
        return Support(NO, "", note)
    if st == "risky":
        return Support(RISKY, "", note)
    if st == "documented":
        return Support(YES, "", note)
    return Support(UNKNOWN, "", note)


def running_support(firmware_key: str, info_version: str) -> Support | None:
    """the same for the firmware that runs on the device, from what its INFO answers – only when the INFO names that
    firmware itself ("MELODEE v1.5.1", "FELUCCA SLOOP 2.8.4"). Skiff and Felucca's forks answer "FELUCCA vX.Y" with
    Felucca's number, not their own: None (no statement)."""
    from . import fwfamily
    named = fwfamily.by_info(info_version) if info_version else None
    if named is None or named.key != canonical(firmware_key):
        return None
    return support(firmware_key, info_version)


def documented_only(firmware_key: str) -> bool:
    """"yes" only because other projects name it as the way back (the original V15) – not read in its code"""
    return (table()["firmwares"].get(canonical(firmware_key)) or {}).get("status") == "documented"


# ------------------------------------------------------------------------------------------------ the unit
def revision_of_id(flash_id) -> str:
    """a JEDEC ID (int or hex text) -> "new" | "old" | "unknown" """
    try:
        n = flash_id if isinstance(flash_id, int) else int(str(flash_id).lower().removeprefix("0x"), 16)
    except (TypeError, ValueError):
        return UNKNOWN
    return NEW if n == NEW_ID else OLD if n == OLD_ID else UNKNOWN


def revision(profile) -> str:
    """the hardware revision recorded for a device profile: "new" | "old" | "unknown" """
    r = getattr(profile, "hw_revision", "") if profile is not None else ""
    return r if r in (NEW, OLD) else UNKNOWN


def asked(profile) -> bool:
    """has the owner answered the question for this device (also with "I don't know"), or was the chip read?"""
    return bool(getattr(profile, "hw_revision", "")) if profile is not None else False


def set_revision(profile, rev: str, how: str, flash_id: int = 0) -> bool:
    """record the revision ("new" | "old" | "unsure" | "" = forget). how: "user" | "rom" | "identity".
    A chip ID that was read is never overwritten by an answer. -> changed?"""
    if profile is None or rev not in (NEW, OLD, UNSURE, ""):
        return False
    if how == "user" and getattr(profile, "hw_revision_source", "") in ("rom", "identity") and profile.flash_id:
        return False
    new = (rev, how if rev else "", f"{flash_id:06X}" if flash_id else "")
    if (profile.hw_revision, profile.hw_revision_source, profile.flash_id) == new:
        return False
    profile.hw_revision, profile.hw_revision_source, profile.flash_id = new
    return True


def note_flash_id(profile, flash_id: int, how: str) -> bool:
    """a flash ID that was read (ROM boot info, a firmware's identity answer): record it if it is one of the two"""
    rev = revision_of_id(flash_id)
    return set_revision(profile, rev, how, flash_id) if rev != UNKNOWN else False


_FLASH_ID_RE = re.compile(r"(?:flash[ _-]?id|jedec|\bid=)\D{0,12}(?:0x)?([0-9a-f]{6})\b", re.I)


def flash_id_in(line: str) -> int:
    """the flash ID a line of the rescue tools names ("Flash ID    0xB36014 (expected …)", "key=980F id=b36014"); 0 none"""
    m = _FLASH_ID_RE.search(line or "")
    return int(m.group(1), 16) if m else 0


# ------------------------------------------------------------------------------------------------ texts
QUESTION = N_("Does your FM-1 have the M-VAVE logo on the front?")
QUESTION_WHY = N_("FM-1s sold from late 2026 – the ones with the M-VAVE logo printed on the front – have a different "
                  "memory chip (UCUN UC25HQ80) than the earlier ones. Firmware versions that do not know this chip "
                  "save nothing on such a unit and cannot be updated or replaced any more, except through the "
                  "recovery in ROM boot mode. The app cannot read the chip over MIDI, so it asks you once per "
                  "device. You can change the answer on the page “Devices”. To be sure: M-Flasher shows the chip "
                  "under “Device info” (FLASH CHIP), m-bootloader in its menu, and the recovery (stage 2) reads it "
                  "in ROM boot mode.")
ANSWER = {NEW: N_("Yes – newer unit (UC25HQ80 flash)"), OLD: N_("No – earlier unit"), UNSURE: N_("I don't know")}
LABEL = {NEW: N_("newer unit, logo on the front (flash UC25HQ80, 0xB36014)"),
         OLD: N_("earlier unit, no logo on the front (flash Puya, 0x856014)"),
         UNKNOWN: N_("not known yet")}
HOW = {"user": N_("your answer"), "rom": N_("read in ROM boot mode"), "identity": N_("reported by the firmware")}


def label(profile) -> str:
    """the revision of a profile for the UI, with where it comes from"""
    rev = revision(profile)
    text = _(LABEL[rev])
    how = getattr(profile, "hw_revision_source", "") if profile is not None else ""
    if rev == UNKNOWN and getattr(profile, "hw_revision", "") == UNSURE:
        return _("not known (you answered “I don't know”)")
    return f"{text} – {_(HOW[how])}" if rev != UNKNOWN and how in HOW else text


def first_version_text(firmware_key: str, first: str) -> str:
    """"1.6.0" / "V10.6z" as the firmware writes it"""
    return first if canonical(firmware_key) == "jiant-fm" else first.lstrip("vV")


def running_warning(name: str, sup: Support, firmware_key: str) -> str:
    """the unit is a newer one and runs a version that does not know its chip: why saving and updating fail, and the
    way out"""
    first = (_("The first version that knows this chip is {version}.").format(
        version=first_version_text(firmware_key, sup.first_version)) if sup.first_version else
        _("No version of this firmware is known to support this chip."))
    return _("<b>This FM-1 is a newer unit (logo on the front), and {name} in the version that runs on it does not "
             "know its flash chip.</b> It cannot save anything, and by what its author writes it usually cannot "
             "start an update either – so an installation from here may simply not begin (nothing is written then). "
             "{first} The way out: bring the FM-1 into ROM boot mode and write the original V15 back with the "
             "recovery (stage 2), then install a version that knows the chip.").format(name=name, first=first)
