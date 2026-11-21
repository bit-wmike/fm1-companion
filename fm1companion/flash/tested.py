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

# M-VAVE's FM1 Open on a real FM-1 – not a writing step of the installer either. As of 2026-11-19: ONE earlier unit
# (FM1 Open V14, installed outside the app), ONE Raspberry Pi 5, and NOT through the app's pages: the owner ran the
# app's own module fm1companion/fm1open.py from a script, with the app closed. docs/UNTESTED.md ("FM1 Open") repeats
# this table; tests/test_fm1open_quick.py keeps the two in sync.
FM1OPEN_HARDWARE: dict[str, tuple[bool, str]] = {
    "fm1open_probe": (True, "FM1 Open's sample protocol: the probe"),
    "fm1open_bank_read": (True, "reading the sample bank"),
    "fm1open_bank_write": (True, "writing a new sample bank and reading it back"),
    "fm1open_bank_restore": (True, "restoring a saved .fm1s sample bank and reading it back"),
    "fm1open_samples_page": (False, "the FM1 Open box on the page “Samples & Kits”"),
    "fm1open_backup": (False, "an FM1 Open backup (projects, DX7 library banks, sample bank)"),
    "fm1open_restore": (False, "restoring an FM1 Open backup / writing back from the Studio"),
}

# The app's own M-Flasher flows (Recovery › Tools, Backups › Flash backups; tools/flashjob.py) on a real FM-1.
# As of 2026-11-20 (owner's run of PR #27 on ONE Raspberry Pi 5 with TWO units, one after the other at the same USB
# port): download mode by the app's soft key from Melodee 1.6.1 (earlier unit) and from SLOOP 3 3.4.0 (newer unit,
# m-bootloader); the whole-flash backup on both units – by soft key, by REC at power-on and by m-bootloader's menu –
# each byte-identical with a backup M-Flasher made by hand; the write-back ONLY on the earlier unit (0x856014,
# original boot area, Melodee 1.6.1, an image made by the app): three runs of 1 sector (once "nothing to write"),
# the foreign-unit refusal, SIGTERM and closing the window blocked while writing. NOT run: a large difference (the
# boot area, many sectors), a real logout, an image with M-Flasher's own .json, a write-back on the newer unit.
# docs/UNTESTED.md ("M-Flasher through the app") repeats this table; tests/test_tools_flash.py keeps the two in sync.
TOOLS_HARDWARE: dict[str, tuple[bool, str]] = {
    "mf_download_mode": (True, N_("sending the FM-1 into download mode through M-Flasher")),
    "mf_backup": (True, N_("the whole-flash backup through M-Flasher (read twice)")),
    "mf_restore": (True, N_("writing a whole-flash backup back through M-Flasher – so far only small changes on an "
                            "earlier unit (not yet the boot area or a large difference)")),
    "mf_new_chip_backup": (True, N_("the whole-flash backup on a newer FM-1 (logo on the front, flash UC25HQ80)")),
    "mf_new_chip_restore": (False, N_("writing a whole-flash backup back on a newer FM-1 (logo on the front, flash "
                                      "UC25HQ80)")),
    # (owner's run of PR #28 on 2026-11-21, ONE Raspberry Pi 5, both units at the same port – report "Werkzeug-Paket
    # Schritt 3": OWNER_REPORTS["app_step3_pi"])
    "mf_download_button": (True, N_("the button “Into download mode” and “Restart the FM-1” on their own (both "
                                    "units)")),
    "mf_bootloader_install": (True, N_("installing m-bootloader through M-Flasher (bootloader --file) – so far once, "
                                       "on an earlier unit with the original boot area")),
    "mf_bootloader_remove": (True, N_("putting back the original boot area through M-Flasher (bootloader --stock "
                                      "with M-VAVE's V15 package) – so far once, on an earlier unit; the whole flash "
                                      "was byte-identical with the state before the install afterwards")),
    "mf_newer_removal_refused": (True, N_("refusing the removal on a newer FM-1 (no M-Flasher call)")),
    "plan_boot_block": (True, N_("the installation plan blocked by the boot-area rule (m-bootloader, CHECK HEAD)")),
    "mf_new_chip_bootloader": (False, N_("installing or removing m-bootloader on a newer FM-1 (logo on the front, flash "
                                         "UC25HQ80) through this app")),
}
# the boot-area kinds above: what the owner did with M-Flasher by hand (OWNER_REPORTS["mboot_by_hand"])
TOOLS_BY_HAND = {"mf_new_chip_bootloader"}

# Reports by the owner of what ran OUTSIDE this app (his own tools: M-Flasher, the authors' installer pages) – not runs
# of this app, so none of it counts as "tested" above. hardware.json cites the same report for its rows.
OWNER_REPORTS: dict[str, str] = {
    "newer_unit_mboot_sloop3": "2026-11-19, one newer FM-1 (flash 0xB36014): m-bootloader 2.4-395e6cc installed with "
                               "M-Flasher, it boots and its menu shows FLASH B36014; SLOOP 3 v3.4.0 installed with its "
                               "author's installer page; a change saved in SLOOP 3 survived a power cycle",
    "app_step3_pi": "2026-11-21, owner: the app's download mode, restart (both units), m-bootloader install and "
                    "removal on the earlier unit, the plan block and the newer-unit refusal, one Raspberry Pi 5 "
                    "(report 'Werkzeug-Paket Schritt 3') – see TOOLS_HARDWARE",
    "app_flows_pi": "2026-11-20, owner: the app's own flash backup and write-back flows on both units, one "
                    "Raspberry Pi 5 (report 'Werkzeug-Paket Schritt 2') – see TOOLS_HARDWARE",
    "mboot_by_hand": "2026-11-18/19, owner, outside the app: `m-flasher bootloader --file fm1-uboot.boot` "
                     "(m-bootloader 2.4-395e6cc, 4 sectors 0x00000-0x03FFF, verified) on both units, and the way "
                     "back to the original boot area by hand with M-Flasher (`bootloader --stock` with the V15 "
                     "package, and whole-flash restores)",
    "mflasher_by_hand": "2026-11-18/19, owner, outside the app: M-Flasher 0.12.1 by hand in a terminal on a "
                        "Raspberry Pi 5 – download-mode, info, backup (twice, same SHA-256) and restore of a whole "
                        "flash backup onto the same unit (147 and 174 sectors, verified by the chip's CRC32, then "
                        "--restart), on the earlier unit; backup and m-bootloader install on the newer unit",
}


def tool_note(kind: str, new_chip: bool = False) -> str:
    """the honest line for the M-Flasher dialogs: run on a real FM-1 through this app, or not (on a newer unit the
    write-back has its own key)"""
    if new_chip and kind == "mf_restore":
        kind = "mf_new_chip_restore"
    if new_chip and kind in ("mf_bootloader_install", "mf_bootloader_remove"):
        kind = "mf_new_chip_bootloader"
    ok, label = TOOLS_HARDWARE.get(kind, (False, ""))
    if ok:
        return _("Tested on a real FM-1 through this app: {what}.").format(what=_(label))
    if kind in TOOLS_BY_HAND:
        return _("Not tested on a real FM-1 through this app yet: {what} ran only against a stand-in for M-Flasher. "
                 "The owner did the same with M-Flasher by hand, outside the app (2026-11-18/19).").format(
                     what=_(label))
    return _("Not tested on a real FM-1 through this app yet: {what} ran only against a stand-in for M-Flasher. "
             "M-Flasher itself was used this way by hand (backup and restore of the whole flash, 2026-11-18/19)."
             ).format(what=_(label))


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
