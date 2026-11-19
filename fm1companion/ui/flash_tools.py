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


def softkey_port(ctx) -> str:
    """the app's own connected FM-1 that may get M-Flasher's soft key (its MIDI port name), "" for none: only a real
    FM-1 the app recognised itself, running the original firmware (USB 4c4a:c755/4155), a Felucca-family firmware
    (it answered the editor protocol) or SLOOP 3 – never in update mode, m-bootloader's MIDI MODE, FM1 Open, an
    unknown firmware or the Studio twin"""
    from ..devices import FM1_USB_IDS
    port = getattr(ctx, "port", None)
    if port is None or getattr(ctx, "is_virtual", False) or getattr(port, "virtual", False):
        return ""
    if getattr(port, "update_mode", False) or getattr(ctx, "mboot", None) is not None or \
            getattr(ctx, "protocol", "") == "update":
        return ""
    kind = FM1_USB_IDS.get((getattr(port, "usb_id", "") or "").lower(), "")
    if kind in ("fm1-open", "stock-update", "felucca-update"):
        return ""
    ok = kind == "stock" or getattr(ctx, "protocol", "") == "felucca" or getattr(ctx, "sloop3", None) is not None
    return (getattr(port, "label", "") or getattr(port, "product", "") or "") if ok else ""


def _disable_cancel(text: str):
    """the job dialog's Cancel button off: from "yes" on M-Flasher writes to its end"""
    from PyQt6.QtWidgets import QApplication
    w = QApplication.activeModalWidget()
    b = getattr(w, "cancel_button", None)
    if b is not None:
        b.setEnabled(False)
        b.setToolTip(text)


def _inhibit(pid: int):
    """ask the system not to shut down or suspend while M-Flasher (pid) writes – for exactly as long as that
    process lives, even if the app itself ends (systemd-inhibit + tail --pid; nothing happens where they are
    missing)"""
    import shutil
    import subprocess
    if not pid or not shutil.which("systemd-inhibit") or not shutil.which("tail"):
        return None
    try:
        return subprocess.Popen(["systemd-inhibit", "--what=shutdown:sleep:idle:handle-power-key:handle-suspend-key",
                                 "--who=FM-1 Companion", "--why=M-Flasher is writing the FM-1's flash",
                                 "--mode=block", "tail", f"--pid={pid}", "-f", "/dev/null"],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True)
    except OSError:
        return None


def _job(ctx, store, worker, writing: bool = False):
    from ..tools import flashjob as FJ
    prof = None if getattr(ctx, "is_virtual", False) else getattr(ctx, "profile", None)

    def on_writing(record):
        text = _("M-Flasher is writing: this cannot be cancelled.")
        try:
            call_in_gui(lambda: _disable_cancel(text))
        except Exception:  # noqa: BLE001 – never stops the write
            pass
        _inhibit(int(record.get("pid") or 0))
    return FJ.Job(backups_dir=Path(ctx.settings.backups_dir), profiles=getattr(ctx, "store", None), profile=prof,
                  store=store, cancel=_Cancel(worker), say=worker.log.emit, softkey_for=softkey_port(ctx),
                  on_writing=on_writing if writing else None)


class write_guard:
    """while a write-back runs: the app ignores SIGTERM (logout, `kill`) and cancels a session-manager logout – once
    M-Flasher writes, it must not be left alone half-way by the app's own end"""

    def __enter__(self):
        import signal
        import threading
        from PyQt6.QtWidgets import QApplication
        self.old = None
        if threading.current_thread() is threading.main_thread():
            try:
                self.old = signal.signal(signal.SIGTERM, signal.SIG_IGN)
            except (ValueError, OSError):
                self.old = None
        self.app = QApplication.instance()
        if self.app is not None:
            try:
                self.app.commitDataRequest.connect(_refuse_logout)
            except (AttributeError, TypeError):
                pass
        return self

    def __exit__(self, *exc):
        import signal
        if self.old is not None:
            try:
                signal.signal(signal.SIGTERM, self.old)
            except (ValueError, OSError):
                pass
        if self.app is not None:
            try:
                self.app.commitDataRequest.disconnect(_refuse_logout)
            except (TypeError, RuntimeError):
                pass
        return False


def _refuse_logout(manager):
    try:
        if manager.allowsInteraction():
            manager.cancel()
    except Exception:  # noqa: BLE001
        pass


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
        job = _job(ctx, store, w, writing=True)
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
        with write_guard():
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


# ------------------------------------------------------- an interrupted write-back (the app ended while writing)

def _choice(parent, title: str, text: str, buttons: list[str]) -> int:
    """a question with these buttons -> the index clicked (-1: closed)"""
    from PyQt6.QtWidgets import QMessageBox
    box = QMessageBox(QMessageBox.Icon.Warning, title, text, parent=parent)
    bs = [box.addButton(b, QMessageBox.ButtonRole.ActionRole) for b in buttons]
    box.exec()
    return bs.index(box.clickedButton()) if box.clickedButton() in bs else -1


def interrupted_text(rec: dict) -> str:
    import time as _t
    when = _t.strftime("%d.%m.%Y %H:%M", _t.localtime(float(rec.get("started") or 0)))
    what = (_("M-Flasher's own output ends with “everything written is verified”, but the app did not check it "
              "afterwards.") if rec.get("done") else
            _("M-Flasher had started erasing and writing; its output does not say it finished.")
            if rec.get("started_writing") else _("It is not known how far M-Flasher got."))
    if rec.get("state") == "failed":
        what = _("The write did not end verified: {reason}").format(reason=html.escape(str(rec.get("reason") or "?")))
    return "<br><br>".join([
        _("A flash write-back onto “{device}” (unit ID {uid}) started on {when} did not end verified – the app was "
          "closed or ended while M-Flasher wrote, or the write failed.").format(
            device=html.escape(str(rec.get("device") or "?")), uid=html.escape(str(rec.get("unit") or "–")),
            when=when),
        what,
        _("The FM-1 may be in download mode now. Nothing is done automatically. Writing back again goes through all "
          "the usual checks (M-Flasher reads the unit first, unit ID and flash chip must match)."),
        _("The image: {image}<br>Its state before the write: {pre}").format(
            image=html.escape(Path(str(rec.get("image") or "?")).name),
            pre=html.escape(Path(str(rec.get("pre_backup") or "?")).name))])


def check_interrupted_write(parent, ctx, root=None, store=None) -> str:
    """at start: an unfinished write-back record -> ask what to do (never automatic).
    -> "" nothing open, "running" M-Flasher still writes, "image" / "before" / "later" / "forget" the choice"""
    from ..tools import flashjob as FJ
    rec = FJ.unfinished_write(root)
    if rec is None:
        return ""
    if rec.get("alive"):
        show_error(parent, _("M-Flasher is still writing"),
                   _("M-Flasher is still writing the flash of “{device}” (started by an earlier run of the app). Do "
                     "not unplug the FM-1 or switch it off. Look again on Recovery › Tools once it has ended.").format(
                         device=html.escape(str(rec.get("device") or "?"))))
        return "running"
    i = _choice(parent, _("Interrupted flash write-back"), interrupted_text(rec),
                [_("Write the image back again …"), _("Write the state before back …"), _("Later"), _("Forget this")])
    if i == 0:
        restore_flow(parent, ctx, Path(str(rec.get("image"))), store=store)
        return "image"
    if i == 1:
        restore_flow(parent, ctx, Path(str(rec.get("pre_backup"))), store=store)
        return "before"
    if i == 3 and confirm(parent, _("Forget this"), _("Forget the interrupted write-back? Do this only when the FM-1 "
                                                      "works again or you have written it back yourself."),
                          _("Forget"), danger=True):
        FJ.forget_write(root)
        return "forget"
    return "later"
