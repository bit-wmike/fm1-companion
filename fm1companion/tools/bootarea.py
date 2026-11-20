# SPDX-License-Identifier: GPL-3.0-only
"""The FM-1's boot area (flash 0x00000-0x03FFF: the flash header, the file table, the bootloader and its
configuration) – what the app knows about it per unit, and the facts it checks against. No I/O to a device, no Qt.

Why it matters (docs/flash-protocol.md §16, "CHECK HEAD"): Felucca-family firmwares with the old check (Melodee
1.6.0-rc.7, SLOOP 2.8.3 and older) hand a package with M-VAVE's loader – the original V15 – on only when its first
0x4000 bytes equal the unit's boot area byte for byte; otherwise the FM-1 shows "FAILED -5" and nothing is written.
So once the boot area is not the one of M-VAVE's V15 package, the usual way back (V15 first, then the target) is a
trap. Firmwares that change the boot area are listed in CHANGERS.

The reference is the boot area of the public V15 package (M-VAVE, FM-1_015.fwsc, SHA-256 db1642b2…, the one the app
already downloads by pin): its first 0x4000 bytes of flash.bin, SHA-256 V15_BOOT_AREA_SHA256. "original" in this
module means exactly "equals that". (A unit sold with V16 may have had another one from the factory; the app does not
know it and does not claim it.)

How the app learns a unit's boot area (`note`, kept in Profile.boot_area):
* a whole-flash backup (M-Flasher's double read): its first 0x4000 bytes are compared with V15's – exact;
* M-Flasher's `info` line "bootloader m-bootloader <version> (fm1)" and m-bootloader's MIDI MODE answer (0x12);
* the app's own m-bootloader install / removal (verified by a fresh read);
* the firmware history: FM1 Open (V12/V14) seen after the last exact reading means "changed by FM1 Open" – M-Flasher
  reports "stock" for it (measured: V14 differs in about 12.6 KB at 0x20-0x3C2A); SLOOP 3 runs only from
  m-bootloader.
"""
from __future__ import annotations

import hashlib
import struct
import time

from ..i18n import N_, _

BOOT_AREA = 0x4000
BOOT_AT = 0xA0                       # where the bootloader container is stored (ENC 0xFFFF, header and payload apart)
# the first 0x4000 bytes of flash.bin in M-VAVE's FM-1_015.fwsc (sha256 db1642b2…), measured from the public file
V15_BOOT_AREA_SHA256 = "d9f43191777656de01c7bed4f9e9ba2e34e94832c5d2ed02c3fc7ab7a6d9bd67"

ORIGINAL, MBOOT, OPEN, OTHER = "original", "m-bootloader", "fm1-open", "other"
NOT_ORIGINAL = (MBOOT, OPEN, OTHER)

# firmwares that change the boot area (the planner's table; docs/flash-protocol.md §16)
CHANGERS = {
    "m-bootloader": {"kind": MBOOT, "what": N_("m-bootloader replaces the bootloader in the boot area (4 sectors, "
                                               "0x00000–0x03FFF)")},
    "fm1-open": {"kind": OPEN, "what": N_("M-VAVE's FM1 Open (V12, V14) changes the boot area – measured on V14: "
                                          "about 12.6 KB in 0x00020–0x03C2A, although M-Flasher reports “stock”")},
}
CHANGER_ALIASES = {"mbootloader": "m-bootloader", "fm1open": "fm1-open", "open": "fm1-open"}

HOW = {"backup": N_("from a whole-flash backup"), "m-flasher": N_("reported by M-Flasher"),
       "midi-mode": N_("reported by m-bootloader's MIDI MODE"), "installed": N_("written and checked by this app"),
       "history": N_("from the firmware history"), "sloop3": N_("SLOOP 3 runs only from m-bootloader")}
LABEL = {ORIGINAL: N_("the original boot area (as in M-VAVE's V15 package)"), MBOOT: N_("m-bootloader"),
         OPEN: N_("changed by FM1 Open"), OTHER: N_("not the original boot area")}


class BootAreaError(ValueError):
    """a ready (translated) sentence"""


def changer(key: str) -> dict | None:
    k = (key or "").strip().lower()
    return CHANGERS.get(CHANGER_ALIASES.get(k, k))


# ------------------------------------------------------------------------------------------------ JieLi formats

def _crc16(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b << 8
        for _bit in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def _enc(data: bytes, key: int = 0xFFFF) -> bytes:
    out = bytearray(data)
    for i in range(len(out)):
        out[i] ^= key & 0xFF
        key = ((key << 1) ^ (0x1021 if key & 0x8000 else 0)) & 0xFFFF
    return bytes(out)


def boot_identity(boot: bytes) -> tuple[str, str] | None:
    """(board tag, version) of an m-bootloader container (BANKCB, its "AC79BLID" block) – None for anything else"""
    if len(boot) < 16:
        return None
    bank, size, offset, pcrc, hcrc = struct.unpack_from("<HH4xIHH", boot, 0)
    if bank != 1 or _crc16(boot[:14]) != hcrc or offset + size > len(boot) or _crc16(boot[offset:offset + size]) != pcrc:
        return None
    p = boot[offset:offset + size]
    if len(p) < 0x70 or p[0x40:0x48] != b"AC79BLID":
        return None

    def cstr(b: bytes) -> str:
        return b.split(b"\0", 1)[0].decode("ascii", "replace")
    return cstr(p[0x48:0x50]), cstr(p[0x50:0x70])


def stored_form(boot: bytes) -> bytes:
    """how a bootloader container is stored in the boot area at 0xA0: its 16-byte header and its payload, each
    ENC 0xFFFF on its own (as JieLi's packager writes it)"""
    return _enc(boot[:16]) + _enc(boot[16:])


def holds(area: bytes, boot: bytes) -> bool:
    """does this boot area carry exactly this bootloader container at 0xA0?"""
    s = stored_form(boot)
    return len(area) >= BOOT_AT + len(s) and area[BOOT_AT:BOOT_AT + len(s)] == s


def package_flash(raw: bytes) -> tuple[str, bytes]:
    """(identity, flash.bin) of an M-VAVE package (.fwsc) – BootAreaError when it is not one"""
    from ..flash import package as pk
    from ..vendor import fm1_install as fi
    try:
        ident = fi.product_of(raw)
    except Exception as e:  # noqa: BLE001
        raise BootAreaError(_("This is not a firmware package (.fwsc).")) from e
    img = fi.logical_image(raw)
    err, entries = pk.ufw_check(img)
    flash = next(((off, size) for t, n, off, size in entries if t == 0 and n == "flash.bin"), None)
    if err and flash is None:
        raise BootAreaError(_("The package is damaged ({damage}).").format(damage=err))
    if flash is None:
        raise BootAreaError(_("The package has no flash image (flash.bin)."))
    off, size = flash
    data = img[off:off + size]
    if len(data) <= BOOT_AREA or len(data) % 0x1000:
        raise BootAreaError(_("The package's flash image has an unexpected size."))
    return ident, data


def v15_area(raw: bytes) -> bytes:
    """the boot area of M-VAVE's V15 package – only from the package with the pinned SHA-256, and only when its boot
    area is the one measured for it"""
    from ..flash import package as pk
    if hashlib.sha256(raw).hexdigest() != pk.STOCK_V15_SHA256:
        raise BootAreaError(_("This is not M-VAVE's original V15 package (checksum)."))
    ident, flash = package_flash(raw)
    area = flash[:BOOT_AREA]
    if ident != "FM-1_015" or hashlib.sha256(area).hexdigest() != V15_BOOT_AREA_SHA256:
        raise BootAreaError(_("The V15 package's boot area is not the expected one."))
    return area


def classify(area: bytes, mf_bootloader: str = "") -> str:
    """the kind of a boot area as read (whole-flash backup): ORIGINAL (= V15's), MBOOT (M-Flasher saw m-bootloader in
    this very read) or OTHER"""
    if hashlib.sha256(bytes(area[:BOOT_AREA])).hexdigest() == V15_BOOT_AREA_SHA256:
        return ORIGINAL
    if (mf_bootloader or "").lower().startswith("m-bootloader"):
        return MBOOT
    return OTHER


# ------------------------------------------------------------------------------------------------ the profile

def mboot_name(text: str) -> str:
    """"m-bootloader 2.4-395e6cc (fm1)" (M-Flasher) / "m-bootloader 2.4-395e6cc" (MIDI MODE) -> the latter"""
    import re
    return re.sub(r"\s*\([^()]*\)\s*$", "", (text or "").strip())


def note(profile, kind: str, how: str, bootloader: str = "", sha256: str = "", when: float | None = None) -> bool:
    """remember what the app now knows of this unit's boot area -> True when the profile changed (save it)"""
    if profile is None or kind not in (ORIGINAL, MBOOT, OPEN, OTHER):
        return False
    rec = dict(getattr(profile, "boot_area", None) or {})
    new = {"kind": kind, "how": how, "bootloader": mboot_name(bootloader) if kind == MBOOT else "",
           "sha256": sha256 or "",
           "time": float(when if when is not None else time.time())}
    if {k: rec.get(k) for k in ("kind", "how", "bootloader", "sha256")} == {k: new[k] for k in
                                                                           ("kind", "how", "bootloader", "sha256")}:
        return False
    profile.boot_area = new
    return True


def _history_open_after(profile, since: float) -> bool:
    """FM1 Open in the firmware history at or after `since` (it changes the boot area; M-Flasher calls it "stock")"""
    for h in getattr(profile, "firmware_history", None) or []:
        if not isinstance(h, dict):
            continue
        if changer(str(h.get("firmware") or "")) is CHANGERS["fm1-open"]:
            try:
                if float(h.get("time") or 0) >= since:
                    return True
            except (TypeError, ValueError):
                return True
    return False


def current(profile) -> dict:
    """what the app knows of the unit's boot area now: {"kind", "how", "bootloader", "time"}; kind "" = not known"""
    if profile is None:
        return {"kind": "", "how": "", "bootloader": "", "time": 0.0}
    rec = dict(getattr(profile, "boot_area", None) or {})
    try:
        since = float(rec.get("time") or 0)
    except (TypeError, ValueError):
        since = 0.0
    if rec.get("kind") != OPEN and _history_open_after(profile, since if rec.get("kind") else 0.0):
        return {"kind": OPEN, "how": "history", "bootloader": "", "time": since}
    if not rec.get("kind") and (getattr(profile, "firmware", "") or "").lower() in ("sloop3", "sloop-3"):
        return {"kind": MBOOT, "how": "sloop3", "bootloader": "", "time": 0.0}
    kind = str(rec.get("kind") or "")
    return {"kind": kind if kind in (ORIGINAL, MBOOT, OPEN, OTHER) else "", "how": str(rec.get("how") or ""),
            "bootloader": str(rec.get("bootloader") or ""), "time": since}


def describe(state: dict) -> str:
    kind = state.get("kind") or ""
    if not kind:
        return _("not known")
    text = _(LABEL[kind])
    if kind == MBOOT and state.get("bootloader"):
        text = state["bootloader"]
    how = HOW.get(state.get("how") or "")
    return f"{text} ({_(how)})" if how else text
