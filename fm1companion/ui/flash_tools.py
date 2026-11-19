# SPDX-License-Identifier: GPL-3.0-only
"""The buttons "Back up the whole flash" and "Write flash back" (Recovery › Tools and Backups › Flash backups):
dialogs around tools/flashjob.py, which drives the bundled M-Flasher in USB download mode."""
from __future__ import annotations

import datetime as dt
import html
from pathlib import Path

from ..i18n import _
from .common import call_in_gui, confirm, fmt_size, init_gui_caller, show_error


class _Cancel:
    """the dialog's Cancel button as the event the runner checks (honoured until "yes" is sent)"""

    def __init__(self, worker):
        self.w = worker

    def is_set(self) -> bool:
        return bool(getattr(self.w, "cancelled", False))


def _job(ctx, store, worker):
    from ..tools import flashjob as FJ
    prof = None if getattr(ctx, "is_virtual", False) else getattr(ctx, "profile", None)
    return FJ.Job(backups_dir=Path(ctx.settings.backups_dir), profiles=getattr(ctx, "store", None), profile=prof,
                  store=store, cancel=_Cancel(worker), say=worker.log.emit)


def _not_here(parent, ctx, store=None) -> bool:
    if getattr(ctx, "is_virtual", False):
        show_error(parent, _("Not a real FM-1"), _("The Studio twin is not a real FM-1. Choose a real FM-1 at the top, "
                                                   "or disconnect it and put the FM-1 into download mode."))
        return True
    if store is not None:
        return False
    from ..tools import runner
    from ..tools.store import Store
    why = runner.unavailable_reason(Store())
    if why:
        show_error(parent, _("M-Flasher is not available"), why)
        return True
    return False


def _date(text: str) -> str:
    try:
        t = dt.datetime.fromisoformat(text)
        return (t if t.tzinfo else t.astimezone()).strftime("%d.%m.%Y %H:%M")
    except (TypeError, ValueError):
        return text or "?"


def backup_flow(parent, ctx, store=None) -> Path | None:
    """"Back up the whole flash" -> the image kept, None when it did not happen"""
    from ..tools import flashjob as FJ
    from . import feedback
    store = store or getattr(ctx, "tools_store", None)       # (a test's stand-in; normally None: the bundled one)
    if _not_here(parent, ctx, store):
        return None
    result: dict = {}

    def work(w):
        job = _job(ctx, store, w)
        res = FJ.backup(job)
        result["res"] = res
        return res

    def said(res) -> str:
        text = _("Whole flash backed up: read twice, both reads identical and checked against the chip's CRC-32 – "
                 "{name} ({size}). Nothing was written to the FM-1.").format(name=res.path.name,
                                                                              size=fmt_size(res.path.stat().st_size))
        if not res.info.unit_id:
            text += " " + _("M-Flasher reported no unit ID, so this copy cannot be written back by the app.")
        text += " " + (_("The FM-1 was restarted.") if res.restarted else
                       _("The FM-1 is still in download mode: switch it off and on to start it again."))
        return text
    was_hold = getattr(ctx, "hold", False)
    ctx.hold = True                              # (no device search while M-Flasher has the FM-1)
    try:
        feedback.run(parent, _("Back up the whole flash"), work, done=said,
                     actions=[feedback.open_folder_action(lambda r: r.path.parent)], cancellable=True)
    finally:
        ctx.hold = was_hold
    res = result.get("res")
    if res is not None and hasattr(ctx, "changed"):
        try:
            ctx.changed.emit()                   # (the profile may have learned its unit id and flash chip)
        except Exception:  # noqa: BLE001
            pass
    return res.path if res is not None else None


def plan_text(plan) -> str:
    from .. import hardware
    from ..flash import tested
    from ..tools import flashjob as FJ
    rev = _(hardware.LABEL.get(plan.revision, hardware.LABEL[hardware.UNKNOWN]))
    parts = [
        _("Write the flash backup of {date} back onto “{unit}” (unit ID {uid}, {revision})?").format(
            date=html.escape(_date(plan.image_date)), unit=html.escape(plan.unit_name), uid=FJ.mask(plan.unit_id),
            revision=html.escape(rev)),
        _("The image: {name}, made {date}; firmware then (as the app knew it): {fw}.").format(
            name=html.escape(plan.image.name), date=html.escape(_date(plan.image_date)),
            fw=html.escape(plan.image_firmware or _("not known"))),
        "<b>" + _("This writes the whole flash back, the boot area (bootloader and its configuration, "
                  "0x00000–0x03FFF) included.") + "</b> " +
        _("{n} sectors of 4 KiB differ from what the FM-1 holds now{boot}. Everything on the FM-1 since that day – "
          "firmware, bootloader, projects, sounds, settings – goes back to the state of the image.").format(
            n=plan.sectors, boot=_(", the boot area among them") if plan.boot_area else ""),
        _("The current state was backed up just now (read twice): {name}.").format(name=html.escape(plan.backup.name)),
        _("M-Flasher checks the unit ID, the flash chip and these sectors once more right before it writes; the app "
          "answers its question only when they match. Do not unplug the FM-1 or switch it off while it writes."),
        html.escape(tested.tool_note("mf_restore")),
    ]
    return "<br><br>".join(parts)


def restore_flow(parent, ctx, path: Path | None = None, store=None) -> bool:
    """"Write flash back": the chosen image back onto the unit it came from -> True when written and verified"""
    from .. import flashdump
    from ..tools import flashjob as FJ
    from . import feedback
    if path is None:
        from PyQt6.QtWidgets import QFileDialog
        try:
            start = str(Path(ctx.settings.backups_dir))
        except Exception:  # noqa: BLE001
            start = str(Path.home())
        f, _filter = QFileDialog.getOpenFileName(parent, _("Flash backup to write back"), start,
                                                 _("Flash backups (*.bin)"))
        if not f:
            return False
        path = Path(f)
    store = store or getattr(ctx, "tools_store", None)
    if _not_here(parent, ctx, store):
        return False
    try:
        side = FJ.check_image(path)
    except FJ.Refused as e:
        show_error(parent, _("Not written back"), e.reason)
        return False
    if not confirm(parent, _("Write flash back"),
                   _("The app first puts the FM-1 into download mode, reads it and backs up its current state, and "
                     "checks that {name} comes from this very unit (unit ID {uid}). Only then it asks you once more "
                     "before anything is written.").format(name=html.escape(path.name),
                                                          uid=FJ.mask(flashdump.side_unit_id(side))),
                   _("Continue")):
        return False
    init_gui_caller()
    result: dict = {}

    def ask(plan) -> bool:
        return bool(call_in_gui(lambda: confirm(parent, _("Write the whole flash back?"), plan_text(plan),
                                                _("Write back"), danger=True)))

    def work(w):
        job = _job(ctx, store, w)
        try:
            res = FJ.restore(job, path, ask)
        except FJ.WriteFailed as e:
            result["failed"] = e
            raise
        result["res"] = res
        return res

    def said(res) -> str:
        if not res.written:
            return _("The FM-1's flash already holds exactly this image – nothing was written.")
        return _("Written back and verified: M-Flasher confirmed every sector, and a fresh read equals the image. "
                 "{started} The state before is kept as {name}.").format(
                     started=_("The FM-1 was restarted.") if res.restarted else
                     _("Switch the FM-1 off and on to start it."), name=res.backup.name if res.backup else "?")
    was_hold = getattr(ctx, "hold", False)
    ctx.hold = True
    try:
        d = feedback.run(parent, _("Write flash back"), work, done=said,
                         actions=[feedback.open_folder_action(lambda r: Path(path).parent)], cancellable=True,
                         cancel_tip=_("Cancels until M-Flasher starts writing; from then on it runs to its end."))
    finally:
        ctx.hold = was_hold
    if hasattr(ctx, "changed"):
        try:
            ctx.changed.emit()
        except Exception:  # noqa: BLE001
            pass
    res = result.get("res")
    return bool(getattr(d, "ok", False) and res is not None and res.written)
