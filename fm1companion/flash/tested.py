# SPDX-License-Identifier: GPL-3.0-only
"""What has been run on a real FM-1 – per kind of writing step, for the line the user sees before the click.

The human-readable list is docs/UNTESTED.md; its section "Per step" repeats this table and
tests/test_flash.py keeps the two in sync. Change both together, and only after a hardware test report.
"""
from __future__ import annotations

from ..i18n import N_, _

# kind of step -> (run on a real FM-1 by this app?, what the user reads)
# "yes" needs a hardware test report with logs. As of 2026-11-18 that is: one update within Melodee, and ONE run on
# ONE earlier FM-1 (flash 0x856014) at ONE Raspberry Pi 5: KARMA 3.0.5 -> original V15 -> Skiff 2.7 through two
# self-powered USB hubs in careful mode, with flight logs; and ONE run on ONE newer FM-1 (see "new_chip_skiff").
# Not counted (docs/UNTESTED.md says why): "to_stock" and
# "v16" (the device profile's history of 2026-11-05 records such installations as finished, but there is no log and
# no report of how the FM-1 behaved) and "chain_continue" (in that run the switch went on by itself after the V15
# step; the separate "Continue" after an interruption was not used).
STEPS: dict[str, tuple[bool, str]] = {
    "update_melodee": (True, N_("an update within Melodee")),
    "update_same": (False, N_("an update within another firmware than Melodee")),
    "switch": (True, N_("a switch to another firmware")),
    "v15_step": (True, N_("the intermediate step via the original V15")),
    "chain_continue": (False, N_("continuing a switch after the V15 step")),
    "to_stock": (False, N_("going back to the original firmware")),
    "v16": (False, N_("the original firmware V16")),
    "own_file": (False, N_("installing your own .fwsc file")),
    "resume": (False, N_("finishing an interrupted installation")),
    "resources": (False, N_("rewriting font and factory sounds")),
    "two_devices": (False, N_("several FM-1 connected at once")),
    "usb_hub": (True, N_("installing through a USB hub (careful mode)")),
    "factory_voices": (False, N_("restoring the factory voices of the original V15")),
    "rom_restore": (False, N_("writing in ROM boot mode (recovery stage 2)")),
    # the newer chip: ONE run counts (2026-11-18: one newer unit, factory firmware -> Skiff 2.7, directly at a Pi 5,
    # flight log, saving checked) – and only for that: Skiff, without a V15 step. Everything else on such a unit
    # (any other firmware, the public V15 package, the recovery, m-bootloader) is "new_chip" and untested.
    "new_chip_skiff": (True, N_("installing Skiff on a newer FM-1 (logo on the front, flash UC25HQ80)")),
    "new_chip": (False, N_("anything else on a newer FM-1 (logo on the front, flash UC25HQ80)")),
}

# SLOOP 3 and m-bootloader on a real FM-1 – nothing of it is a writing step of the app's installer, so it is not in
# STEPS (and not in the plans' line). As of 2026-11-18: ONE earlier unit (flash 0x856014, SLOOP 3 3.4.0 and
# m-bootloader 2.4 installed outside the app), ONE Raspberry Pi 5 (Qt 6.4.2), read only. docs/UNTESTED.md ("SLOOP 3 and
# m-bootloader") repeats this table; tests/test_flash_audit.py keeps the two in sync.
SLOOP3_HARDWARE: dict[str, tuple[bool, str]] = {
    "sloop3_connect": (True, "recognising SLOOP 3 3.4.0 and connecting its bundled editor (3.4.0)"),
    "sloop3_backup": (True, "a SLOOP 3 backup (read only)"),
    "mboot_recognise": (True, "recognising m-bootloader 2.4's MIDI MODE (not as a Felucca update loader)"),
    "sloop3_refusal": (True, "refusing an installation under a running SLOOP 3"),
    "sloop3_restore": (False, "restoring a SLOOP 3 backup"),
    "mboot_flash_copy": (False, "the flash backup in MIDI MODE"),
    "sloop3_writing": (False, "anything that writes to a SLOOP 3 or m-bootloader unit"),
    "sloop3_new_chip": (False, "SLOOP 3 or m-bootloader on a newer FM-1 (logo on the front)"),
}


def tested(kind: str) -> bool:
    return bool(STEPS.get(kind, (False, ""))[0])


def note(kinds) -> str:
    """the one honest line for a dialog: what was run on a real FM-1 so far, and whether this step was"""
    kinds = [k for k in dict.fromkeys(kinds or ()) if k in STEPS]
    done = ", ".join(_(label) for ok, label in STEPS.values() if ok)
    open_ = [_(STEPS[k][1]) for k in kinds if not STEPS[k][0]]
    if kinds and not open_:
        return _("Tested on a real FM-1: this kind of step ({what}) has been run on a real device with this "
                 "app.").format(what=", ".join(_(STEPS[k][1]) for k in kinds))
    if not open_:
        return _("Tested on a real FM-1 so far: {done}. This step: only in the simulation.").format(done=done)
    return _("Tested on a real FM-1 so far: {done}. This step ({what}): only in the simulation.").format(
        done=done, what=", ".join(open_))


def plan_kinds(plan) -> list[str]:
    """the kinds of step an installation plan (flash/planner.InstallPlan) contains"""
    from . import planner
    t, st = plan.target, plan.state
    out = []
    if plan.resume:
        out.append("resume")
    elif plan.chain_resume:
        out.append("chain_continue")
    elif t.is_stock:
        out.append("to_stock")
    elif planner.same_firmware(st, t):
        melodee = planner.running_firmware(st) == "melodee"
        out.append("update_melodee" if melodee else "update_same")
    else:
        out.append("switch")
    if plan.via_stock:
        out.append("v15_step")
    if t.is_v16 or planner.runs_v16(st):
        out.append("v16")
    if not t.known:
        out.append("own_file")
    if st.ports > 1:
        out.append("two_devices")
    if getattr(st, "revision", "") == "new":      # the unit is a newer one (logo on the front)
        skiff = planner.fw_identity(t.key) == "skiff" and t.known and not plan.via_stock and not plan.resume
        out.append("new_chip_skiff" if skiff else "new_chip")
    return out
