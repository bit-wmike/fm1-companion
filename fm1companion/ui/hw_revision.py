# SPDX-License-Identifier: GPL-3.0-only
"""The one question about the hardware revision of an FM-1 ("logo on the front?") – asked once per device when an
installation depends on it, and changeable on the pages "Devices" and "Overview". Rules: fm1companion/hardware.py.

"Newer unit" is sticky: whatever says so (the owner, a chip ID read in ROM boot mode or told by a firmware) stays
until the owner takes it away here, after an explicit confirmation (logged). A contradicting "earlier chip" that
was read under the same profile is shown, not applied."""
from __future__ import annotations

from PyQt6.QtWidgets import QMessageBox

from .. import hardware
from ..i18n import _

FORGET = "forget"


def choose(parent, name: str, current: str = "", can_forget: bool = False) -> str | None:
    """the question as a dialog -> "new" | "old" | "unsure" | "forget", None = closed without an answer"""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle(_("Which FM-1 is this?"))
    box.setText("<b>" + _(hardware.QUESTION) + "</b>")
    info = _(hardware.QUESTION_WHY)
    if name:
        info = _("Device: {device}.").format(device=name) + " " + info
    if current:
        info += "\n\n" + _("Recorded so far: {state}.").format(state=current)
    box.setInformativeText(info)
    buttons = {}
    for rev in (hardware.NEW, hardware.OLD, hardware.UNSURE):
        buttons[box.addButton(_(hardware.ANSWER[rev]), QMessageBox.ButtonRole.ActionRole)] = rev
    if can_forget:
        buttons[box.addButton(_("Forget what is recorded"), QMessageBox.ButtonRole.ActionRole)] = FORGET
    box.addButton(QMessageBox.StandardButton.Cancel)
    box.exec()
    return buttons.get(box.clickedButton())


def confirm_change(parent, profile, rev: str) -> bool:
    """the owner takes "newer unit" (or a chip ID that was read) away: only after this explicit confirmation"""
    from .common import confirm
    to = _("nothing (the app will ask again)") if rev in ("", FORGET) else _(hardware.ANSWER[rev])
    return confirm(parent, _("Which FM-1 is this?"),
                   _("Recorded for “{device}”: <b>{state}</b>.<br><br>You are about to change this to: <b>{to}</b>."
                     "<br><br>If this FM-1 is a newer unit after all (M-VAVE logo on the front), the app will then no "
                     "longer stop firmware versions that do not know its flash chip – such a version saves nothing "
                     "and cannot be updated or replaced any more, except through the recovery in ROM boot mode. "
                     "Change it only if you are sure (M-Flasher shows the chip under “Device info”).").format(
                         device=profile.name, state=hardware.label(profile), to=to),
                   _("Yes, change it"), danger=True)


def record(ctx, profile, rev: str, parent=None) -> bool:
    """store the owner's statement in the device profile ("new" | "old" | "unsure" | "forget"). "Newer unit" is
    always taken. Taking "newer unit" or a read chip ID away needs confirm_change(). -> is the profile now as asked?"""
    if profile is None:
        return False
    want = "" if rev == FORGET else rev
    if getattr(profile, "hw_revision", "") == want and (want or not hardware.conflict(profile)):
        return True
    changed = hardware.set_revision(profile, want, "user") if rev != FORGET else False
    if not changed and getattr(profile, "hw_revision", "") != want:
        if not confirm_change(parent, profile, rev):
            return False
        changed = hardware.set_revision(profile, want, "user", force=True)
    if changed:
        ctx.store.save()
        ctx.changed.emit()
    return getattr(profile, "hw_revision", "") == want


def ask(parent, ctx, profile) -> str | None:
    """ask and store; -> the answer ("forget" included), None = no answer or not confirmed"""
    if profile is None:
        return None
    rev = choose(parent, profile.name, hardware.label(profile) if hardware.asked(profile) else "",
                 can_forget=hardware.asked(profile))
    if rev is None:
        return None
    return rev if record(ctx, profile, rev, parent) else None
