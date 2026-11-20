# SPDX-License-Identifier: GPL-3.0-only
"""The buttons "Back up the whole flash", "Write flash back", "Into download mode", "Restart the FM-1", "Install
m-bootloader" and "Remove m-bootloader (original boot area)" (Recovery › Tools, Backups › Flash backups, the Device
menu): dialogs around tools/flashjob.py, which drives the bundled M-Flasher in USB download mode."""
from __future__ import annotations

import datetime as dt
import html
import time
from pathlib import Path

from ..i18n import _, ngettext
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
    if kind not in ("stock", "felucca"):      # (only a USB-connected FM-1 with one of these USB IDs – not BLE, a
        return ""                             #  virtual cable, FM1 Open, an update loader)
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


class _Inhibitor:
    """`systemd-inhibit … tail --pid=PID -f /dev/null`: shutdown and suspend are held off for exactly as long as
    process PID lives (also if the app ends); release() ends it earlier"""

    def __init__(self, proc):
        self.proc = proc

    def release(self):
        try:
            self.proc.terminate()
        except OSError:
            pass


def hold_off(why: str, pid: int, wait: float = 3.0):
    """start an inhibitor bound to `pid` and wait until the system lists it (so it is active BEFORE the caller goes
    on – e.g. before M-Flasher gets "yes"); None where systemd-inhibit is missing"""
    import shutil
    import subprocess
    if not pid or not shutil.which("systemd-inhibit") or not shutil.which("tail"):
        return None
    try:
        proc = subprocess.Popen(["systemd-inhibit", "--what=shutdown:sleep:idle:handle-power-key:handle-suspend-key",
                                 "--who=FM-1 Companion", f"--why=M-Flasher writes the FM-1's flash ({why})",
                                 "--mode=block", "tail", f"--pid={pid}", "-f", "/dev/null"],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True)
    except OSError:
        return None
    t0 = time.monotonic()
    while time.monotonic() - t0 < wait and proc.poll() is None:
        try:
            out = subprocess.run(["systemd-inhibit", "--list", "--no-legend"], capture_output=True, text=True,
                                 timeout=2).stdout
        except (OSError, subprocess.SubprocessError):
            break
        if f"({why})" in out:
            break
        time.sleep(0.1)
    return _Inhibitor(proc)


def _job(ctx, store, worker, writing: bool = False):
    from ..tools import flashjob as FJ
    prof = None if getattr(ctx, "is_virtual", False) else getattr(ctx, "profile", None)

    def on_writing(record):
        text = _("M-Flasher is writing: this cannot be cancelled.")
        try:
            call_in_gui(lambda: _disable_cancel(text))
        except Exception:  # noqa: BLE001 – never stops the write
            pass
    def on_overdue():
        try:
            call_in_gui(_offer_leave)
        except Exception:  # noqa: BLE001
            pass
    port = getattr(ctx, "port", None)
    usb = getattr(port, "usb_path", "") if port is not None and not getattr(ctx, "is_virtual", False) else ""
    if prof is not None and usb:
        _SEEN.update(profile=prof.id, usb=usb, at=time.time())
    elif prof is None and _SEEN.get("at", 0) > time.time() - GUIDE_SECS:
        # (the FM-1 was put into download mode by hand just now: it left USB MIDI, so no device is active –
        # the profile and USB port the app saw last are kept; the job uses them only if the unit in download mode
        # is at that very port)
        store_ = getattr(ctx, "store", None)
        prof = getattr(store_, "profiles", {}).get(_SEEN.get("profile")) if store_ is not None else None
        usb = _SEEN.get("usb", "") if prof is not None else ""
    return FJ.Job(backups_dir=Path(ctx.settings.backups_dir), profiles=getattr(ctx, "store", None), profile=prof,
                  store=store, cancel=_Cancel(worker), say=worker.log.emit, softkey_for=softkey_port(ctx),
                  on_writing=on_writing if writing else None, on_overdue=on_overdue if writing else None,
                  usb_path=usb, inhibit=hold_off if writing else None)


_SEEN: dict = {}            # the last real device a flash job was clicked for: {"profile", "usb", "at"}
GUIDE_SECS = 15 * 60        # how long "the FM-1 I just put into download mode by hand" counts


def _offer_leave():
    """long after "yes" M-Flasher still writes: the dialog's button becomes an honest way out – close the app and
    let M-Flasher finish on its own (never a kill)"""
    from PyQt6.QtWidgets import QApplication
    w = QApplication.activeModalWidget()
    b = getattr(w, "cancel_button", None)
    if b is None:
        return
    try:
        b.clicked.disconnect()
    except TypeError:
        pass
    b.setText(_("Close the app, let M-Flasher finish"))
    b.setToolTip(LEAVE_TEXT())
    b.setEnabled(True)
    b.clicked.connect(lambda: leave_while_writing(w))


def LEAVE_TEXT() -> str:
    return _("M-Flasher has been writing for much longer than it ever should. The app can close now: M-Flasher "
             "keeps running in the background and is not stopped. Keep the FM-1 connected and switched on. At the "
             "next start the app shows what happened.")


def leave_while_writing(parent) -> bool:
    from PyQt6.QtWidgets import QApplication
    if not confirm(parent, _("Close the app?"), LEAVE_TEXT(), _("Close the app"), danger=True):
        return False
    app = QApplication.instance()
    if app is not None:
        app.quit()
    return True


def _sigterm(signum, frame):
    """SIGTERM during a write-back job: ignored only while M-Flasher writes ("yes" until it ended), otherwise the
    default action (the app ends) – a Python handler, not SIG_IGN, so no child process inherits an ignore"""
    import os
    import signal
    from ..tools import flashjob as FJ
    if FJ.WRITING.is_set():
        import logging
        logging.getLogger(__name__).warning("SIGTERM ignored: M-Flasher is writing")
        return
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    os.kill(os.getpid(), signal.SIGTERM)


def remember_unit(ctx, job):
    """after a flash job: the unit that comes back at that USB port (after the app's own download mode / restart) is
    the job's unit – no "Which FM-1 is this?" for it (the rule of the app's own installation at a port; the write
    confirmation still applies)"""
    prof = getattr(job, "profile", None)
    usb = getattr(job, "unit_usb", "") or getattr(job, "usb_path", "")
    if prof is not None and usb and hasattr(ctx, "note_installed"):
        try:
            ctx.note_installed(prof, usb)
        except Exception:  # noqa: BLE001
            pass


class write_guard:
    """while a write-back job runs: SIGTERM (logout, `kill`) is ignored from "yes" until M-Flasher has ended, and a
    session-manager logout is cancelled – once M-Flasher writes, the app's own end must not leave it half-way"""

    def __enter__(self):
        import signal
        import threading
        from PyQt6.QtWidgets import QApplication
        self.old = None
        if threading.current_thread() is threading.main_thread():
            try:
                self.old = signal.signal(signal.SIGTERM, _sigterm)
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
        try:
            res = FJ.backup(job)
        finally:
            remember_unit(ctx, job)
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
        ngettext("{n} sector of 4 KiB differs from what the FM-1 holds now{boot}.",
                 "{n} sectors of 4 KiB differ from what the FM-1 holds now{boot}.", plan.sectors).format(
            n=plan.sectors, boot=_(", the boot area among them") if plan.boot_area else "") + " " +
        _("Everything on the FM-1 since that day – firmware, bootloader, projects, sounds, settings – goes back to "
          "the state of the image."),
        _("The current state was backed up just now (read twice): {name}.").format(name=html.escape(plan.backup.name)),
        _("M-Flasher checks the unit ID, the flash chip and these sectors once more right before it writes; the app "
          "answers its question only when they match. Do not unplug the FM-1 or switch it off while it writes."),
        html.escape(tested.tool_note("mf_restore", new_chip=plan.revision == "new")),
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
        finally:
            remember_unit(ctx, job)
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


# ------------------------------------------------------- download mode, restart, m-bootloader

def download_flow(parent, ctx, store=None) -> str:
    """"Into download mode": the soft key to the FM-1 the app recognised itself, otherwise the guide -> /dev/sgN or ""
    """
    from ..tools import flashjob as FJ
    from . import feedback
    store = store or getattr(ctx, "tools_store", None)
    if _not_here(parent, ctx, store):
        return ""
    result: dict = {}

    def work(w):
        job = _job(ctx, store, w)
        try:
            res = FJ.download_mode(job)
        finally:
            remember_unit(ctx, job)
        result["res"] = res
        return res

    def said(res) -> str:
        return (_("The FM-1 is in USB download mode now ({device}). Nothing was written. “Restart the FM-1” (or "
                  "switching it off and on) starts its firmware again.") if res.sent else
                _("An FM-1 is in USB download mode already ({device}). Nothing was written.")).format(device=res.device)
    was_hold = getattr(ctx, "hold", False)
    ctx.hold = True
    try:
        feedback.run(parent, _("Into download mode"), work, done=said, cancellable=True)
    finally:
        ctx.hold = was_hold
    res = result.get("res")
    return res.device if res is not None else ""


def leave_flow(parent, ctx, store=None) -> bool:
    """"Restart the FM-1": the one FM-1 in download mode starts its firmware again (M-Flasher `restart`)"""
    from ..tools import flashjob as FJ
    from . import feedback
    store = store or getattr(ctx, "tools_store", None)
    if _not_here(parent, ctx, store):
        return False
    result: dict = {}

    init_gui_caller()

    def ask(text) -> bool:
        return bool(call_in_gui(lambda: confirm(parent, _("Restart the FM-1"), html.escape(text), _("Restart"))))

    def work(w):
        job = _job(ctx, store, w)
        res = FJ.leave_download_mode(job, ask)
        result["res"] = res
        return res
    feedback.run(parent, _("Restart the FM-1"), work, done=_("The FM-1 was restarted. Nothing was written."),
                 cancellable=True)
    return bool(result.get("res"))


def boot_plan_text(plan) -> str:
    """the danger question before m-bootloader is installed or removed: unit, revision, what is written and why it
    is safe to try, what was tested"""
    from .. import hardware
    from ..flash import tested
    from ..tools import flashjob as FJ
    rev = _(hardware.LABEL.get(plan.revision, hardware.LABEL[hardware.UNKNOWN]))
    who = _("“{unit}” (unit ID {uid}, {revision})").format(unit=html.escape(plan.unit_name), uid=FJ.mask(plan.unit_id),
                                                           revision=html.escape(rev))
    if plan.op == "install":
        head = _("Install m-bootloader {version} on {who}?").format(version=html.escape(plan.version), who=who)
        what = _("The app writes the bundled m-bootloader {version} (fm1-uboot.boot, checksum checked) with "
                 "M-Flasher. <b>Only the boot area 0x00000–0x03FFF is written</b> (four sectors at most); the "
                 "firmware, projects, sounds and factory data stay as they are. Afterwards the FM-1 starts through "
                 "m-bootloader: hold PLAY while switching it on for its menu (DOWNLOAD MODE, MIDI MODE), REC for USB "
                 "download mode directly.").format(version=html.escape(plan.version))
        kind = "mf_bootloader_install"
    else:
        head = _("Put back the original boot area on {who}?").format(who=who)
        what = _("The app writes the boot area of M-VAVE's original firmware V15 (from the V15 package, checksum "
                 "checked) with M-Flasher – that removes m-bootloader and what FM1 Open changed there. <b>Only the "
                 "boot area 0x00000–0x03FFF is written</b>; the firmware, projects, sounds and factory data stay as "
                 "they are. On an FM-1 that came with V16 this is V15's boot area, not necessarily the one it "
                 "came with.")
        kind = "mf_bootloader_remove"
    parts = [head,
             _("Boot area now (as M-Flasher reads it): {now}. Firmware (as the app knows it): {fw}.").format(
                 now=html.escape(plan.current or "?"), fw=html.escape(plan.firmware or _("not known"))),
             what,
             _("The whole flash was backed up just now (read twice): {name}. That is the way back.").format(
                 name=html.escape(plan.backup.name)),
             _("M-Flasher checks the unit ID, the flash chip and the sectors once more right before it writes; the app "
               "answers its question only when every planned range lies inside the boot area. Do not unplug the "
               "FM-1 or switch it off while it writes."),
             *[html.escape(n) for n in plan.notes],
             html.escape(tested.tool_note(kind))]
    return "<br><br>".join(parts)


def _boot_flow(parent, ctx, store, op: str) -> bool:
    from ..tools import flashjob as FJ
    from . import feedback
    store = store or getattr(ctx, "tools_store", None)
    if _not_here(parent, ctx, store):
        return False
    if op == "install":
        title = _("Install m-bootloader")
        intro = _("The app first puts the FM-1 into download mode, reads it and backs up the whole flash (read "
                  "twice). Then it asks you once more before m-bootloader is written into the boot area. Nothing is "
                  "written without that backup.")
    else:
        title = _("Remove m-bootloader (original boot area)")
        intro = _("The app takes the boot area from M-VAVE's original V15 package (downloaded once from M-VAVE's "
                  "public repository if it is not on this computer yet, checked against its checksum). Then it puts "
                  "the FM-1 into download mode, backs up the whole flash (read twice) and asks you once more before "
                  "the boot area is written.")
    if not confirm(parent, title, intro, _("Continue")):
        return False
    init_gui_caller()
    result: dict = {}

    def ask(plan) -> bool:
        return bool(call_in_gui(lambda: confirm(parent, title + "?", boot_plan_text(plan), _("Write the boot area"),
                                                danger=True)))

    def work(w):
        package = b""
        if op == "remove":
            from .. import firmware
            w.log.emit(_("Getting M-VAVE's original V15 package …"))
            package = Path(firmware.fetch_stock("stock-v15", log=w.log.emit)).read_bytes()
        job = _job(ctx, store, w, writing=True)
        try:
            res = FJ.install_bootloader(job, ask) if op == "install" else FJ.remove_bootloader(job, ask, package)
        except FJ.WriteFailed as e:
            result["failed"] = e
            raise
        except FJ.Refused as e:
            result["offer"] = getattr(e, "offer", None)
            raise
        finally:
            remember_unit(ctx, job)
        result["res"] = res
        return res

    def said(res) -> str:
        again = (_("The FM-1 was restarted.") if res.restarted else _("Switch the FM-1 off and on to start it."))
        if not res.written:
            text = (_("m-bootloader {version} is on this FM-1 already – nothing was written.").format(
                version=res.version) if op == "install" else
                _("This FM-1 has the original boot area already – nothing was written."))
            return text + " " + again
        if op == "install":
            return _("m-bootloader {version} installed and checked: M-Flasher confirmed every sector, a fresh read "
                     "holds exactly this bootloader, and nothing outside the boot area changed. {started} The state "
                     "before is kept as {name}.").format(version=res.version, started=again,
                                                         name=res.backup.name if res.backup else "?")
        return _("Original boot area put back and checked: M-Flasher confirmed every sector, a fresh read holds the "
                 "boot area of the V15 package, and nothing outside it changed. {started} The state before is kept "
                 "as {name}.").format(started=again, name=res.backup.name if res.backup else "?")
    was_hold = getattr(ctx, "hold", False)
    ctx.hold = True
    try:
        with write_guard():
            d = feedback.run(parent, title, work, done=said, cancellable=True,
                             cancel_tip=_("Cancels until M-Flasher starts writing; from then on it runs to its end."))
    finally:
        ctx.hold = was_hold
    if hasattr(ctx, "changed"):
        try:
            ctx.changed.emit()
        except Exception:  # noqa: BLE001
            pass
    offer = result.get("offer")
    if offer is not None and confirm(parent, _("Write flash back"),
                                     _("This unit has a whole-flash backup made before m-bootloader: {name}. Write "
                                       "it back? (The app checks it and asks once more before anything is "
                                       "written.)").format(name=html.escape(offer.name)), _("Continue")):
        return restore_flow(parent, ctx, offer, store=store)
    res = result.get("res")
    return bool(getattr(d, "ok", False) and res is not None and res.verified)


def install_boot_flow(parent, ctx, store=None) -> bool:
    return _boot_flow(parent, ctx, store, "install")


def remove_boot_flow(parent, ctx, store=None) -> bool:
    return _boot_flow(parent, ctx, store, "remove")


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
    if str(rec.get("op") or "").startswith("bootloader"):
        return "<br><br>".join([
            _("Writing the boot area of “{device}” (unit ID {uid}, m-bootloader) started on {when} did not end "
              "verified – the app was closed or ended while M-Flasher wrote, or the write failed.").format(
                device=html.escape(str(rec.get("device") or "?")), uid=html.escape(str(rec.get("unit") or "–")),
                when=when),
            what,
            _("The FM-1 may be in download mode now. Nothing is done automatically. Its state before the write: "
              "{pre} – writing it back goes through all the usual checks.").format(
                pre=html.escape(Path(str(rec.get("pre_backup") or "?")).name))])
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
    try:                                       # (another window runs a job – maybe this very write: not now)
        from .. import storage
        if FJ.WRITING.is_set() or storage.install_running():
            return "busy"
    except Exception:  # noqa: BLE001
        pass
    if rec.get("alive"):
        show_error(parent, _("M-Flasher is still writing"),
                   _("M-Flasher is still writing the flash of “{device}” (started by an earlier run of the app). Do "
                     "not unplug the FM-1 or switch it off. Look again on Recovery › Tools once it has ended.").format(
                         device=html.escape(str(rec.get("device") or "?"))))
        return "running"
    boot = str(rec.get("op") or "").startswith("bootloader")
    buttons = [_("Write the image back again …"), _("Write the state before back …"), _("Later"), _("Forget this")]
    if boot:                                   # (an m-bootloader write: there is no image, only the state before)
        buttons[0] = ""
    i = _choice(parent, _("Interrupted flash write-back"), interrupted_text(rec), [b for b in buttons if b])
    if boot and i >= 0:
        i += 1
    if i in (0, 1):
        which = "image" if i == 0 else "before"
        why = FJ.rerun_problem(rec, which)
        if why:
            show_error(parent, _("Not written back"), why)
            return "changed"
        restore_flow(parent, ctx, Path(str(rec.get("image" if i == 0 else "pre_backup"))), store=store)
        return which
    if i == 3 and confirm(parent, _("Forget this"), _("Forget the interrupted write-back? Do this only when the FM-1 "
                                                      "works again or you have written it back yourself."),
                          _("Forget"), danger=True):
        FJ.forget_write(root)
        return "forget"
    return "later"
