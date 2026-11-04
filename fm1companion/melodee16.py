# SPDX-License-Identifier: GPL-3.0-only
"""Melodee 1.6: what the suite needs to know when switching to, from and between the 1.6 versions.

Source: keremimo/melodee v1.6.0 docs/RELEASE_1.6.0.md ("Upgrading", "Sound and levels", "MIDI and USB") and
firmware/src/res.c, firmware/loader/ldr_core.c (ldr_res). In short:

* Melodee 1.6 stores projects and presets in a shared storage pool and in new formats (FBKM–FBKQ,
  compact records). Older firmware cannot read that, may overwrite the sectors and rejects backups from
  1.6. The backup BEFORE the update is the way back.
* Backups from 1.6 have 57 objects (32 projects; IDs 64–91 = projects 5–32) – no older firmware has that.
* 1.6.0 (final) mixes differently from the pre-releases: CZ-1 6 dB quieter, limiter from −2 dBFS, TPDF dither; the
  USB stereo recording is taken before MASTER, the sound from the computer follows USB VOLUME instead of MASTER.
* Resources (font, factory sounds, Bluetooth program): from 1.6.0-rc.8 on, the update loader writes them itself from the
  package. Coming from ≤ 1.5.1, preset banks still live there – then the loader leaves the area alone, the new
  firmware moves the banks on first boot ("Setup unfinished"), and only after that can the resources be
  written (editor command 82; the Firmware Hub needs a second installation for this).

Pure (no Qt, no MIDI dependency).
"""
from __future__ import annotations

import re

from .i18n import _

_VER = re.compile(r"v?(\d+)\.(\d+)(?:\.(\d+))?(?:[- ]?(rc|beta|alpha)\.?(\d+))?", re.I)
LINE_16 = (1, 6, 0, 0, 0)          # every 1.6 version, including the pre-releases
FINAL_160 = (1, 6, 0, 3, 0)        # the final 1.6.0 (new levels)
OBJECTS_16 = 57                    # backup inventory from 1.6 (32 project slots)


def version_key(s: str) -> tuple | None:
    """'MELODEE v1.6.0-rc.8' -> (1, 6, 0, 2, 8); final version -> (1, 6, 0, 3, 0); no version -> None"""
    m = _VER.search(s or "")
    if not m:
        return None
    a, b, c, pre, n = m.groups()
    rank = {"alpha": 0, "beta": 1, "rc": 2}.get((pre or "").lower(), 3)
    return (int(a), int(b), int(c or 0), rank, int(n or 0))


def is_melodee(version: str = "", key: str = "") -> bool:
    return (key or "").lower() == "melodee" or "melodee" in (version or "").lower()


def is_16(version: str) -> bool:
    """Melodee version of the 1.6 series (or newer)?  '' / unknown -> False"""
    k = version_key(version)
    return k is not None and k >= LINE_16


def before_16(version: str) -> bool:
    """known Melodee version BEFORE 1.6 (1.5.1 and older)?  '' / unknown -> False"""
    k = version_key(version)
    return k is not None and k < LINE_16


def new_levels(old: str, new: str) -> bool:
    """does the level change of the final 1.6.0 lie between old and new (in this direction: old -> new)?"""
    a, b = version_key(old), version_key(new)
    return a is not None and b is not None and a < FINAL_160 <= b


# ------------------------------------------------------------------ texts
UPGRADE_16 = _("<b>Melodee 1.6:</b> Existing projects are loaded, but what 1.6 saves can no longer be read by older "
               "firmware – and older firmware refuses backups from 1.6. The backup the app now makes beforehand is "
               "your way back; that is why it is mandatory here.")
LEVELS_160 = _("<b>Melodee 1.6.0 sounds different:</b> CZ-1 tracks are 6 dB quieter, the limiter only kicks in at "
               "−2 dBFS (loud mixes get louder), TPDF dither everywhere. Existing projects may therefore sound "
               "different – check the balance. The USB recording now sits before MASTER (the volume knob no longer "
               "changes the recording level); the sound from the computer is set with “USB VOLUME” in the HOME menu.")
RESOURCES_FROM_15 = _("From Melodee 1.5.1 or older: the update loader cannot write the factory sounds the first "
                      "time, because your preset banks are still stored there; the new firmware moves them into the "
                      "shared memory at start-up. The app then writes the factory sounds itself (step “Write "
                      "resources”) – a second installation as with the Firmware Hub is not needed. If the FM-1 then "
                      "shows “Setup unfinished”: “Rewrite font & factory sounds” or install the same version "
                      "once more.")
BACKUP_REQUIRED_16 = _("A backup is mandatory for the switch to Melodee 1.6: older firmware cannot read the projects "
                       "of 1.6, the backup from before is the only way back. Please tick “Make a backup first”.")


def downgrade_text(target: str) -> str:
    return _("<b>Back from Melodee 1.6:</b> {target} cannot read the projects and presets that Melodee 1.6 saved, "
             "and may overwrite their memory. Backups from Melodee 1.6 (including the one the app makes now) cannot "
             "be restored there either – only on Melodee 1.6 or newer again. For the way back, use the backup from "
             "before the update to 1.6.").format(target=target)


DOWNGRADE_LOST = _("Projects, presets and settings from Melodee 1.6 cannot be used with the older firmware "
                   "(backups from 1.6 can only be restored on Melodee 1.6 or newer).")


def restore_refusal(backup_version: str = "", device_version: str = "") -> str:
    b = f" ({backup_version})" if version_key(backup_version) else ""
    d = device_version or _("a firmware without the 32 project slots of 1.6 (e.g. Melodee 1.5.1 or older)")
    return _("This backup comes from Melodee 1.6{backup_version}, the FM-1 runs {device}. Older firmware cannot read "
             "the projects and presets of 1.6 and refuses such backups. Nothing was written. "
             "Remedy: bring the FM-1 to Melodee 1.6 or newer (Firmware+) and then restore – or use a "
             "backup from before the update to 1.6.").format(backup_version=b, device=d)


# ------------------------------------------------------------------ Backups
def backup_is_16(firmware: str = "", companion: dict | None = None, n_objects: int = 0,
                 ids=()) -> bool:
    """does a backup come from Melodee 1.6 (or newer)? The inventory counts before the name: 57 objects or project IDs
    64–91 exist only from 1.6 on."""
    if n_objects == OBJECTS_16 or any(64 <= int(i) <= 91 for i in ids):
        return True
    c = companion or {}
    v = c.get("firmware_version") or c.get("version_string") or ""
    if is_melodee(firmware, c.get("firmware", "")):
        return is_16(v) or is_16(firmware)
    return False


def device_before_16(device_version: str = "", n_objects: int | None = None, key: str = "") -> bool:
    """is the FM-1 running a Felucca firmware that can NOT read backups from Melodee 1.6?
    The inventory (BACKUP_LIST) counts before the name."""
    if n_objects is not None:
        return n_objects != OBJECTS_16
    return is_melodee(device_version, key) and before_16(device_version)


def usb_record_hint(version: str) -> str:
    """Note about the recording level over USB (page "Recording", Samples › Record) – depending on the Melodee version."""
    if not is_melodee(version):
        return ""
    k = version_key(version)
    if k is not None and k >= FINAL_160:
        return _("Melodee 1.6.0 and later: the USB recording sits before MASTER – the volume knob on the FM-1 does "
                 "not change the recording level. USB VOLUME (HOME menu) only controls the sound from the computer "
                 "to the FM-1.")
    if k is not None:
        return _("With this Melodee version the stereo recording over USB follows the MASTER knob: for a good "
                 "level, turn MASTER up (from Melodee 1.6.0 on, the recording sits before MASTER).")
    return ""
