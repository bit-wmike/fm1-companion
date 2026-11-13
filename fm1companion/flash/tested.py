# SPDX-License-Identifier: GPL-3.0-only
"""What has been run on a real FM-1 – per kind of writing step, for the line the user sees before the click.

The human-readable list is docs/UNTESTED.md; its section "Per step" repeats this table and
tests/test_flash.py keeps the two in sync. Change both together, and only after a hardware test report.
"""
from __future__ import annotations

from ..i18n import N_, _

# kind of step -> (run on a real FM-1 by this app?, what the user reads)
STEPS: dict[str, tuple[bool, str]] = {
    "update_melodee": (True, N_("an update within Melodee")),
    "update_same": (False, N_("an update within another firmware than Melodee")),
    "switch": (False, N_("a switch to another firmware")),
    "v15_step": (False, N_("the intermediate step via the original V15")),
    "chain_continue": (False, N_("continuing a switch after the V15 step")),
    "to_stock": (False, N_("going back to the original firmware")),
    "v16": (False, N_("the original firmware V16")),
    "own_file": (False, N_("installing your own .fwsc file")),
    "resume": (False, N_("finishing an interrupted installation")),
    "resources": (False, N_("rewriting font and factory sounds")),
    "two_devices": (False, N_("several FM-1 connected at once")),
    "usb_hub": (False, N_("installing through a USB hub (careful mode)")),
    "factory_voices": (False, N_("restoring the factory voices of the original V15")),
    "rom_restore": (False, N_("writing in ROM boot mode (recovery stage 2)")),
}


def tested(kind: str) -> bool:
    return bool(STEPS.get(kind, (False, ""))[0])


def note(kinds) -> str:
    """the one honest line for a dialog: what was run on a real FM-1 so far, and whether this step was"""
    kinds = [k for k in dict.fromkeys(kinds or ()) if k in STEPS]
    done = _(STEPS["update_melodee"][1])
    open_ = [_(STEPS[k][1]) for k in kinds if not STEPS[k][0]]
    if kinds and not open_:
        return _("Tested on a real FM-1: this kind of step ({what}) has been run on a real device with this "
                 "app.").format(what=done)
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
    return out
