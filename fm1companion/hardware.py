# SPDX-License-Identifier: GPL-3.0-only
"""The two hardware revisions of the FM-1 and which firmware version knows the newer one's flash chip.

FM-1s sold from late 2026 ("the ones with the M-VAVE logo on the front") carry a UCUN UC25HQ80 (JEDEC ID 0xB36014)
instead of the Puya P25Q80H (0x856014). A firmware that does not know the ID keeps nothing and – worse – can no longer
start an update: the unit is stuck until the original firmware is written back through the chip's ROM boot.

The knowledge is data: fm1companion/data/hardware.json (one row per firmware, with the source). The API:

    support(firmware_key, version="", flash=(), flash_tag="", repo=None) -> Support(status, first_version, note)
        status  "yes"      this version accepts the newer chip (or: the original V15, which has no such check and is
                           the documented way back – see note)
                "no"       it does not: on a newer unit it would not save and could not be updated any more
                "risky"    its code accepts the chip, but it is unproven there or a unit did not start (m-bootloader
                           before 2.4-395e6cc, SLOOP 3 before 3.4.0: rows with "below": "risky") – treat as "no" for a
                           newer unit. (m-bootloader's build 2.4-395e6cc – not a bare "2.4", another build – and 2.5+,
                           and SLOOP 3 3.4.0 are "yes" by one owner's report, owner_report())
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


class TableError(RuntimeError):
    """data/hardware.json is missing or unreadable: nothing may be judged from it (the plan then installs nothing)"""


@lru_cache(maxsize=1)
def table() -> dict:
    """the table; TableError when it cannot be read or has not the expected shape"""
    try:
        t = json.loads(TABLE.read_text("utf-8"))
        if not (isinstance(t, dict) and isinstance(t["firmwares"], dict) and isinstance(t["aliases"], dict)
                and isinstance(t["checked"], str) and all(isinstance(r, dict) and isinstance(r.get("status"), str)
                                                           for r in t["firmwares"].values())):
            raise ValueError("unexpected shape")
        return t
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise TableError(f"{TABLE.name}: {e}") from e


def table_problem() -> str:
    """"" when the table can be read, else what is wrong with it"""
    try:
        table()
        return ""
    except TableError as e:
        return str(e)


def _rows() -> dict:
    try:
        return table()["firmwares"]
    except TableError:
        return {}


def canonical(key: str) -> str:
    """a firmware key as the table writes it ("jiant" -> "jiant-fm", "choralroot" -> "choralroot-fm-1")"""
    k = (key or "").strip().lower()
    if not k:
        return ""
    if k in _rows():
        return k
    try:
        if k in table()["aliases"]:
            return table()["aliases"][k]
    except TableError:
        pass
    from . import fwfamily
    f = fwfamily.by_key(k)
    return f.key if f is not None else k


def source(key: str) -> dict:
    return dict(_rows().get(canonical(key)) or {})


# ------------------------------------------------------------------------------------------------ versions
# X.Y[.Z], then what is attached to it. A suffix that hangs on the number ("-rc.8", "-beta", "_rc1", "~rc1", "b1",
# "-pre", "-dev", "-smk37", "+build") makes it a pre-release or a variant of X.Y.Z – never the final X.Y.Z itself.
# A separate word without a number ("X0X 1.2.1 BETA", the INFO of the release) is part of the name; a separate
# "rc 7" / "beta 2" is a pre-release again.
_NUM = re.compile(r"(?<![\w.])[vV]?(\d+)\.(\d+)(?:\.(\d+))?(?![\d])")
_ATTACHED = re.compile(r"[-_~+.]?[A-Za-z][\w.+~-]*|[-_~+][\w.+~-]+")
_SPACED_PRE = re.compile(r"\s+(?:rc|beta|alpha|pre|preview|dev|nightly|test)[\s.-]*\d+", re.I)


def parse_version(version: str) -> tuple[tuple[int, int, int], str] | None:
    """'drum-v1.0.1' -> ((1, 0, 1), ''), 'v1.6.0-rc.7' -> ((1, 6, 0), '-rc.7'), 'X0X 1.2.1 BETA' -> ((1, 2, 1), '');
    None: no version in it"""
    m = _NUM.search(version or "")
    if not m:
        return None
    rest = version[m.end():]
    x = _ATTACHED.match(rest) or _SPACED_PRE.match(rest)
    return (int(m[1]), int(m[2]), int(m[3] or 0)), (x.group(0).strip() if x else "")


def _norm(version: str) -> str:
    return (version or "").strip().lower().lstrip("v")


_JIANT = re.compile(r"(?<![\w.])[vV]?(\d+)\.(\d+)(?:\.(\d+))?([a-zA-Z]+\d*)?(.?)")


def _jiant_at_least(version: str, minimum: str) -> bool | None:
    """JIANT's order (studio.jiant_version_key): … V10.60 < V10.61 < V10.6c < … < V10.6z < V10.6z2 … < V10.6z10, then
    the three-part V10.7.N … V10.9.N (from 2026-11-10; a one-digit number after the dot). A bare "V10.7" is two
    different firmwares (2026-10-31, before V10.6z, and the beta of 2026-11-08, after it): None. A two-digit number
    with a patch ("v10.60.0", how the app normalises "V10.60") is that two-digit version."""
    from . import studio
    m = _JIANT.search(version or "")
    if not m:
        return None
    a, minor, c, suf, more = int(m[1]), m[2], m[3], m[4], m[5]
    if more and more in "-_~+" or (c is not None and suf):
        return None                                 # something hangs on it that this order does not know
    b = int(minor)
    if a > 10:
        return True
    if a < 10:
        return False
    if c is not None:
        if len(minor) == 1:
            return True if 7 <= b <= 9 else None    # V10.7.16 … V10.9.N: all after V10.6z10; "10.6.1" is unknown
        return studio.jiant_version_key(f"V{a}.{minor}") >= studio.jiant_version_key(minimum)
    if (a, b) == (10, 7) and not suf and len(minor) == 1:
        return None
    return studio.jiant_version_key(f"V{a}.{minor}{suf or ''}") >= studio.jiant_version_key(minimum)


def at_least(key: str, version: str, minimum: str, also=()) -> bool | None:
    """is version >= minimum in that firmware's own release order? None: the version cannot be read.
    A pre-release or variant of the minimum itself ("1.6.0-rc", "1.6.0-beta", "1.6.0_rc1") is NOT the minimum, unless
    the row lists it in `also` (Felucca's tag 1.8.2-beta contains the fix)."""
    if _norm(version) in {_norm(x) for x in also or ()}:
        return True
    row = _rows().get(canonical(key)) or {}
    if row.get("version_style") == "jiant":
        return _jiant_at_least(version, minimum)
    v, m = parse_version(version), parse_version(minimum)
    if v is None or m is None:
        return None
    if v[0] != m[0]:
        return v[0] > m[0]
    return not v[1]


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


def _tag_prefix(tag: str) -> str:
    m = _NUM.search(tag or "")
    return (tag[:m.start()] if m else "").lower().rstrip("v")


def support(firmware_key: str, version: str = "", flash=(), flash_tag: str = "", repo: str | None = None) -> Support:
    """does this firmware version accept the newer FM-1's flash chip? See the module text.
    repo: the "owner/name" the firmware comes from. When given, a table row only applies if it is the row's own
    repository – a fork that was added under the same name is not the firmware the row was read in (unknown).
    None: the caller vouches for the key (a built-in spec, the firmware a device names in its INFO).
    flash / flash_tag: the JEDEC IDs a firmware's own firmware.json names and the version it was read at – used for a
    firmware outside the table, and only for that version or a later one of the same tag series."""
    row = _rows().get(canonical(firmware_key)) if not table_problem() else None
    if row is not None and repo is not None and (repo or "").strip().lower() != (row.get("repo") or "").lower():
        row, flash = None, ()                        # another repository under a table key: nothing is known
    if row is None:
        ids = [str(x).upper() for x in (flash or ())]
        v, f = parse_version(version), parse_version(flash_tag)
        if ids and v is not None and f is not None and _tag_prefix(version) == _tag_prefix(flash_tag) \
                and (v[0] > f[0] or (v[0] == f[0] and v[1] == f[1])):
            note = "firmware.json hardware.flash at " + flash_tag + ": " + ", ".join(ids)
            return Support(YES if f"{NEW_ID:06X}" in ids else NO, "", note)
        return Support(UNKNOWN, "", "")
    st, first, note = row["status"], row.get("min", "") or "", row.get("source", "") or ""
    pv, pu = parse_version(version), parse_version(row.get("until") or "")
    if pv is not None and pu is not None and pv[0] >= pu[0]:      # (by the numbers alone: "3.0-beta" is SLOOP 3 too)
        return support(row.get("then", ""), version)   # SLOOP 3 is another firmware than SLOOP 2 (its own row)
    if st == "min":
        if not first:                                # a row without its minimum says nothing (never "yes")
            return Support(UNKNOWN, "", note)
        ok = at_least(firmware_key, version, first, row.get("also")) if version else None
        below = RISKY if row.get("below") == "risky" else NO     # (an owner's report from a version on: the ones
        return Support(UNKNOWN if ok is None else YES if ok else below, first, note)   # before stay risky, not "no")
    if st == "none":
        return Support(NO, "", note)
    if st == "risky":
        return Support(RISKY, "", note)
    if st == "documented":
        return Support(YES, "", note)
    return Support(UNKNOWN, "", note)


def owner_report(firmware_key: str) -> bool:
    """the row's "yes" rests on an owner's report of one newer unit, outside this app – not on the firmware's source"""
    return (_rows().get(canonical(firmware_key)) or {}).get("evidence") == "owner-report"


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
    return (_rows().get(canonical(firmware_key)) or {}).get("status") == "documented"


# ------------------------------------------------------------------------------------------------ the unit
# What is recorded per device profile (devices.Profile): hw_revision "new" | "old" | "unsure" | "", where it comes
# from (hw_revision_source "user" | "rom" | "identity"), the flash ID when it was read (flash_id) and, when two
# statements contradict each other, the other one (hw_revision_conflict, e.g. "856014 identity" / "old user").
# The rule: "new" is sticky and the strictest reading wins. A profile is matched to a device loosely (FM-1s have no
# reliable serial number), so a second device can show up under the same profile – one "earlier chip" must never
# open the door for the newer unit. Only the owner takes "new" away, deliberately (force=True, logged).
READ = ("rom", "identity")


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


def was_read(profile) -> bool:
    return profile is not None and getattr(profile, "hw_revision_source", "") in READ and bool(profile.flash_id)


def conflict(profile) -> str:
    return getattr(profile, "hw_revision_conflict", "") if profile is not None else ""


def _log(profile, text: str) -> None:
    try:
        import logging
        logging.getLogger("fm1companion.hardware").info("unit of %s: %s", getattr(profile, "id", "?"), text)
    except Exception:  # noqa: BLE001 – a log line only
        pass


def set_revision(profile, rev: str, how: str, flash_id: int = 0, force: bool = False) -> bool:
    """record a statement about the unit ("new" | "old" | "unsure" | "" = forget). how: "user" | "rom" | "identity".
    -> did the profile change?
      * "new" always counts, whoever says it.
      * against a recorded "new", "old" / "unsure" / forgetting do nothing – a read "old" is kept as a conflict
        (hw_revision_conflict) and the profile stays "new". Only force=True (the owner, after an explicit
        confirmation in the UI) takes "new" away.
      * an answer does not replace an "old" that was read, except with "new" or with force."""
    if profile is None or rev not in (NEW, OLD, UNSURE, ""):
        return False
    cur = getattr(profile, "hw_revision", "")
    before = (cur, profile.hw_revision_source, profile.flash_id, profile.hw_revision_conflict)
    fid = f"{flash_id:06X}" if flash_id else ""
    if force:
        new = (rev, how if rev else "", fid, "")
    elif rev == NEW:
        clash = ""
        if cur == OLD:                               # the other statement is kept, so the UI can say why
            clash = f"{profile.flash_id or OLD} {profile.hw_revision_source or 'user'}"
        new = (NEW, how, fid, clash or (profile.hw_revision_conflict if cur == NEW else ""))
        if cur == NEW and profile.hw_revision_source in READ and how == "user":
            new = (NEW, profile.hw_revision_source, profile.flash_id, profile.hw_revision_conflict)   # read stays
    elif cur == NEW:
        if rev == OLD and how in READ:
            new = (cur, profile.hw_revision_source, profile.flash_id, f"{fid or OLD} {how}")
        else:
            return False
    elif how == "user" and was_read(profile):
        return False
    else:
        new = (rev, how if rev else "", fid, "")
    if before == new:
        return False
    profile.hw_revision, profile.hw_revision_source, profile.flash_id, profile.hw_revision_conflict = new
    _log(profile, f"{before} -> {new} ({how}{', forced' if force else ''})")
    return True


def forget(profile) -> bool:
    """the owner discards what is recorded (also a read value, also "new") – after an explicit confirmation"""
    return set_revision(profile, "", "user", force=True)


def note_flash_id(profile, flash_id: int, how: str) -> bool:
    """a flash ID that was read (ROM boot info, a firmware's identity answer): record it if it is one of the two"""
    rev = revision_of_id(flash_id)
    return set_revision(profile, rev, how, flash_id) if rev != UNKNOWN else False


def merge_revision(dst, src) -> bool:
    """two profiles of the same device become one: the strictest statement stays – "new" beats "old", a read beats
    an answer – and a contradiction is kept as a conflict"""
    if dst is None or src is None:
        return False
    before = (dst.hw_revision, dst.hw_revision_source, dst.flash_id, dst.hw_revision_conflict)

    def rank(p):
        return ({NEW: 3, OLD: 2, UNSURE: 1}.get(p.hw_revision, 0), 1 if was_read(p) else 0)
    win, lose = (src, dst) if rank(src) > rank(dst) else (dst, src)
    clash = win.hw_revision_conflict or lose.hw_revision_conflict
    if win.hw_revision == NEW and lose.hw_revision == OLD:
        clash = f"{lose.flash_id or OLD} {lose.hw_revision_source or 'user'}"
    new = (win.hw_revision, win.hw_revision_source, win.flash_id, clash if win.hw_revision == NEW else "")
    dst.hw_revision, dst.hw_revision_source, dst.flash_id, dst.hw_revision_conflict = new
    if before != new:
        _log(dst, f"{before} -> {new} (merged)")
    return before != new


# flash IDs read in ROM boot mode in this session that could not be tied to a profile for sure: USB port -> ID.
# Used for this session's checks only (a "new" at that port makes the device there count as a newer unit).
SESSION_READS: dict[str, int] = {}


def session_revision(usb_path: str) -> str:
    return revision_of_id(SESSION_READS.get(usb_path or "", 0)) if usb_path else UNKNOWN


def strictest(*revisions: str) -> str:
    """several statements about what may be one unit: "new" if any says so; "old" only if all say so"""
    rs = [r if r in (NEW, OLD) else UNKNOWN for r in revisions]
    if NEW in rs:
        return NEW
    return OLD if rs and all(r == OLD for r in rs) else UNKNOWN


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


CONFLICT = N_("counted as a newer unit: an earlier chip was also seen under this profile ({what}) – two devices under "
              "one profile?")


def label(profile) -> str:
    """the revision of a profile for the UI, with where it comes from (and a contradiction, if one was seen)"""
    rev = revision(profile)
    text = _(LABEL[rev])
    how = getattr(profile, "hw_revision_source", "") if profile is not None else ""
    if rev == UNKNOWN and getattr(profile, "hw_revision", "") == UNSURE:
        return _("not known (you answered “I don't know”)")
    out = f"{text} – {_(HOW[how])}" if rev != UNKNOWN and how in HOW else text
    c = conflict(profile).split()
    if rev == NEW and c:
        what = ("0x" + c[0] if c[0] != OLD else _("earlier unit")) + ", " + _(HOW.get(c[-1], HOW["user"]))
        out += " · " + _(CONFLICT).format(what=what)
    return out


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
