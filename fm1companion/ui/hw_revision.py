# SPDX-License-Identifier: GPL-3.0-only
"""The one question about the hardware revision of an FM-1 ("logo on the front?") – asked once per device when an
installation depends on it, and changeable on the pages "Devices" and "Overview". Rules: fm1companion/hardware.py."""
from __future__ import annotations

from PyQt6.QtWidgets import QMessageBox

from .. import hardware
from ..i18n import _


def choose(parent, name: str, current: str = "") -> str | None:
    """the question as a dialog -> "new" | "old" | "unsure", None = closed without an answer"""
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
    box.addButton(QMessageBox.StandardButton.Cancel)
    box.exec()
    return buttons.get(box.clickedButton())


def record(ctx, profile, rev: str) -> bool:
    """store the owner's answer in the device profile. False: a chip ID that was read stays (it is the better source)"""
    if profile is None:
        return False
    if not hardware.set_revision(profile, rev, "user"):
        return hardware.revision(profile) == rev or getattr(profile, "hw_revision", "") == rev
    ctx.store.save()
    ctx.changed.emit()
    return True


def ask(parent, ctx, profile) -> str | None:
    """ask and store; -> the answer, None = no answer. When the chip was read (ROM boot, firmware), say so instead."""
    if profile is None:
        return None
    if getattr(profile, "hw_revision_source", "") in ("rom", "identity") and profile.flash_id:
        QMessageBox.information(parent, _("Which FM-1 is this?"),
                                _("The flash chip of this FM-1 was read: {state}. That is more reliable than an "
                                  "answer, so it is not changed here.").format(state=hardware.label(profile)))
        return hardware.revision(profile)
    rev = choose(parent, profile.name, hardware.label(profile) if hardware.asked(profile) else "")
    if rev is not None:
        record(ctx, profile, rev)
    return rev
