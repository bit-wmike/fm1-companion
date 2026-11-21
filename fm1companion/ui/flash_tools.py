# SPDX-License-Identifier: GPL-3.0-only
"""The buttons "Back up the whole flash", "Write flash back", "Into download mode", "Restart the FM-1", "Install
m-bootloader" and "Remove m-bootloader (original boot area)" (Recovery › Tools, Backups › Flash backups, the Device
menu), the installation of a firmware through download mode (an installation plan's route "download-mode"), SLOOP 3
over MIDI MODE and "Back to …": dialogs around tools/flashjob.py, which drives the bundled M-Flasher in USB download mode."""
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
        text = (_("m-bootloader is writing: this cannot be cancelled.") if FJ.WRITER.get("who") == "m-bootloader"
                else _("M-Flasher is writing: this cannot be cancelled."))
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
        logging.getLogger(__name__).warning("SIGTERM ignored: %s is writing", FJ.WRITER.get("who"))
        return
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    os.kill(os.getpid(), signal.SIGTERM)


def same_expectation(prof) -> dict:
    """what a unit shows when it comes back running the firmware it ran before (a backup, download mode and back,
    m-bootloader written or removed): SLOOP 3 or not, its identity, its USB name – as far as the profile knows them"""
    if prof is None:
        return {}
    fw = getattr(prof, "firmware", "") or ""
    out: dict = {}
    if fw and fw != "?":
        out["sloop3"] = fw == "sloop3"
    ident = getattr(prof, "identity", "") or ""
    if ident and not ident.lower().startswith("ota-") and fw != "sloop3":
        out["identity"] = ident
    if getattr(prof, "usb_product", ""):
        out["product"] = prof.usb_product
    return out


def remember_unit(ctx, job, expect: dict | None = None):
    """after a flash job: the unit that comes back at that USB port (after the app's own download mode / restart) is
    the job's unit – no "Which FM-1 is this?" for it (the rule of the app's own installation at a port; the write
    confirmation still applies) – but only when it comes back as the job left it: `expect` (or the job's own
    `job.expect`, set once a write started), else the firmware it ran before. Another FM-1 plugged in there is asked
    about. A unit that was in download mode is connected anew afterwards (DeviceContext.mark_restarted)."""
    prof = getattr(job, "profile", None)
    usb = getattr(job, "unit_usb", "") or getattr(job, "usb_path", "")
    if expect is None:
        expect = getattr(job, "expect", None)
    if expect is None:
        expect = same_expectation(prof)
    info = getattr(job, "info", None)
    if getattr(info, "unit_id", ""):
        expect = dict(expect, unit_id=info.unit_id)
    if prof is not None and usb and hasattr(ctx, "note_installed"):
        try:
            ctx.note_installed(prof, usb, expect)
        except Exception:  # noqa: BLE001
            pass
    if usb and (getattr(job, "device", "") or getattr(job, "sent_by_app", False)) and hasattr(ctx, "mark_restarted"):
        try:
            ctx.mark_restarted(usb)
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


# ------------------------------------------------------- importing a flash backup made elsewhere (M-Flasher)

def import_tip() -> str:
    return _("Takes a whole-flash copy made elsewhere – by M-Flasher itself (its .bin with its .json) – into this "
             "app's backups: checked against its .json, filed under the device whose unit ID it names (if none is "
             "known, you choose one). Nothing is sent to an FM-1.")


def choose_profile(parent, uid: str, candidates):
    """which of these profiles (none knows a unit id yet) is the unit with this id? -> the profile or None"""
    from PyQt6.QtWidgets import QInputDialog
    from ..tools import flashjob as FJ
    names = [_("— please choose —")] + [p.name for p in candidates]
    item, ok = QInputDialog.getItem(parent, _("Which FM-1 is this?"),
                                    _("This copy names the unit ID {uid}. None of your device profiles is known to be "
                                      "that unit yet. Which one is it? (It then keeps this unit ID.)").format(
                                          uid=FJ.mask(uid)), names, 0, False)
    if not ok or item == names[0]:
        return None
    return next((p for p in candidates if p.name == item), None)


def import_flow(parent, ctx, path: Path | None = None):
    """"Import a flash backup …" -> the ImportResult, None when nothing was imported"""
    from ..tools import flashjob as FJ
    if path is None:
        from PyQt6.QtWidgets import QFileDialog
        f, _filter = QFileDialog.getOpenFileName(parent, _("Import a flash backup"), str(Path.home()),
                                                 _("Flash backups (*.bin)"))
        if not f:
            return None
        path = Path(f)
    try:
        res = FJ.import_backup(path, Path(ctx.settings.backups_dir), getattr(ctx, "store", None),
                               choose=lambda uid, cands: choose_profile(parent, uid, cands))
    except FJ.Refused as e:
        show_error(parent, _("Not imported"), e.reason)
        return None
    from .common import show_done
    text = (_("This copy is already among the backups of “{device}”: {name}.") if res.already else
            _("Imported as a flash backup of “{device}”: {name} (made {date}). It can be written back like the "
              "app's own backups – only onto that unit.")).format(device=res.profile.name, name=res.path.name,
                                                                  date=_date(res.file_date))
    show_done(parent, _("Import a flash backup"), text)
    if hasattr(ctx, "changed"):
        try:
            ctx.changed.emit()
        except Exception:  # noqa: BLE001
            pass
    return res


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
                 "they are.")
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
             html.escape(tested.tool_note(kind, new_chip=plan.revision == "new"))]
    return "<br><br>".join(parts)


def _boot_flow(parent, ctx, store, op: str, then_v15: bool = False) -> bool:
    from ..tools import flashjob as FJ
    from . import feedback
    store = store or getattr(ctx, "tools_store", None)
    if _not_here(parent, ctx, store):
        return False
    if op == "remove":                          # (a unit the profile knows as newer: refused before any question)
        prof = None if getattr(ctx, "is_virtual", False) else getattr(ctx, "profile", None)
        early = FJ.removal_refused_early(FJ.Job(backups_dir=Path(ctx.settings.backups_dir),
                                                profiles=getattr(ctx, "store", None), profile=prof))
        if early is not None:
            show_error(parent, _("Remove m-bootloader (original boot area)"), early.reason)
            offer = getattr(early, "offer", None)
            if offer is not None and confirm(parent, _("Write flash back"), OFFER_TEXT().format(
                    name=html.escape(offer.name)), _("Continue")):
                return restore_flow(parent, ctx, offer, store=store)
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
            res = FJ.install_bootloader(job, ask) if op == "install" else FJ.remove_bootloader(job, ask, package,
                                                                                               then_v15=then_v15)
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
    if offer is not None and confirm(parent, _("Write flash back"), OFFER_TEXT().format(name=html.escape(offer.name)),
                                     _("Continue")):
        return restore_flow(parent, ctx, offer, store=store)
    res = result.get("res")
    return bool(getattr(d, "ok", False) and res is not None and res.verified)


def OFFER_TEXT() -> str:
    return _("This unit has a whole-flash backup made before m-bootloader: {name}. Write it back? (The app checks it "
             "and asks once more before anything is written.)")


def install_boot_flow(parent, ctx, store=None) -> bool:
    return _boot_flow(parent, ctx, store, "install")


def remove_boot_flow(parent, ctx, store=None, then_v15: bool = False) -> bool:
    return _boot_flow(parent, ctx, store, "remove", then_v15)


# ------------------------------------------------------- a firmware through download mode (m-bootloader units)

def firmware_plan_text(plan) -> str:
    """the danger question before a firmware is installed through download mode"""
    from .. import hardware
    from ..flash import tested
    from ..tools import flashjob as FJ
    rev = _(hardware.LABEL.get(plan.revision, hardware.LABEL[hardware.UNKNOWN]))
    who = _("“{unit}” (unit ID {uid}, {revision})").format(unit=html.escape(plan.unit_name), uid=FJ.mask(plan.unit_id),
                                                           revision=html.escape(rev))
    parts = [
        _("Install {name} ({identity}) on {who} through download mode?").format(
            name=html.escape(plan.name), identity=html.escape(plan.identity), who=who),
        _("Boot area now (as M-Flasher reads it): {now}. Firmware (as the app knows it): {fw}.").format(
            now=html.escape(plan.current or "?"), fw=html.escape(plan.firmware or _("not known"))),
        "<b>" + _("M-Flasher writes only from 0x04000 to the end of the package (0x{end:05X}); the boot area "
                  "0x00000–0x03FFF – m-bootloader – is not written.").format(end=plan.end - 1) + "</b> " +
        ngettext("{n} sector of 4 KiB differs from what the FM-1 holds now.",
                 "{n} sectors of 4 KiB differ from what the FM-1 holds now.", plan.sectors).format(n=plan.sectors) +
        " " + _("What the current firmware keeps in that range (its program, and its projects, sounds and settings "
                "where it stores them there) is replaced; the factory data is not touched."),
        _("The whole flash was backed up just now (read twice): {name}. That is the way back.").format(
            name=html.escape(plan.backup.name)),
        _("M-Flasher checks the unit ID, the flash chip and the sectors once more right before it writes; the app "
          "answers its question only when they are exactly the ones it computed. Do not unplug the FM-1 or switch "
          "it off while it writes."),
        *[html.escape(n) for n in plan.notes],
        html.escape(tested.tool_note("mf_firmware", new_chip=plan.revision == "new"))]
    return "<br><br>".join(parts)


def firmware_data_text(plan, warning: str) -> str:
    """the second, specific question: M-Flasher's warning that the installed firmware keeps its data elsewhere"""
    return "<br><br>".join([
        _("M-Flasher warns: “{warning}”").format(warning=html.escape(warning)),
        _("What may be lost: the projects, sounds, presets and settings that {installed} saved on “{unit}”. {name} "
          "cannot use them, and what is left of them in the flash can confuse it – wrong presets, no sound – until "
          "it has written its own. Nothing of the factory data and nothing in the boot area is touched.").format(
            installed=html.escape(plan.mflasher_firmware or plan.firmware or _("the installed firmware")),
            unit=html.escape(plan.unit_name), name=html.escape(plan.name)),
        _("The way back to exactly the state of now, data included, is the whole-flash backup made just now: "
          "{name}.").format(name=html.escape(plan.backup.name)),
        _("Nothing has been written yet. Install anyway?")])


def firmware_backup_problem(plan, ctx) -> str:
    """the plan includes the device backup, but the connection to the FM-1 is gone: never skipped in silence"""
    if plan is None or "backup" not in plan.kinds():
        return ""
    link = getattr(ctx, "link", None)
    if not getattr(ctx, "connected", False) or link is None or getattr(link, "closed", True):
        return _("The plan includes a backup, but the connection to the FM-1 is gone in the meantime. Nothing was "
                 "written. Please reconnect and open the plan again.")
    return ""


def firmware_flow(parent, ctx, spec, rel=None, local_pkg=None, tag: str = "", plan=None, *, known: bool = True,
                  local_sha: str = "", new_chip_ok: bool = False, store=None) -> bool:
    """"Install through download mode" (an installation plan with route "download-mode"): the package checked as for
    the usual way, a backup of the device's projects first where its firmware has an editor protocol, then
    tools/flashjob.install_firmware -> True when written and verified"""
    from .. import firmware
    from ..tools import flashjob as FJ
    from . import feedback
    from .firmware_page import validate_package
    store = store or getattr(ctx, "tools_store", None)
    if _not_here(parent, ctx, store):
        return False
    label = firmware.display_label(spec, rel.tag if rel else tag)
    title = _("Install {label} through download mode").format(label=label)
    init_gui_caller()
    result: dict = {}
    why = firmware_backup_problem(plan, ctx)
    if why:
        show_error(parent, _("Backup not possible"), why)
        return False
    do_backup = plan is not None and "backup" in plan.kinds()
    link = getattr(ctx, "link", None)
    profile = getattr(ctx, "profile", None)

    def ask(fplan) -> bool:
        return bool(call_in_gui(lambda: confirm(parent, title + "?", firmware_plan_text(fplan), _("Install"),
                                                danger=True)))

    def ask_data(fplan, warning) -> bool:
        return bool(call_in_gui(lambda: confirm(parent, _("The current firmware's data"),
                                                firmware_data_text(fplan, warning), _("Install anyway"),
                                                danger=True)))

    def work(w):
        w.log.emit(_("Step: load & check the package (before any writing) …"))
        if rel is not None:
            pkg = firmware.download(spec, rel, log=w.log.emit, progress=lambda a, b: w.progress.emit(a, b))
            path = Path(pkg.fwsc)
        else:
            path = Path(local_pkg)
        pi = validate_package(path, spec, known, label, expect_sha=local_sha, log=w.log.emit)
        raw = path.read_bytes()                  # (compared with the checked file's SHA-256 by the job)
        if do_backup:
            if link is None or getattr(link, "closed", True):
                raise RuntimeError(_("Backup not possible: the connection to the FM-1 is gone. Installation aborted – "
                                     "nothing was changed on the FM-1."))
            from .. import autobackup
            w.log.emit(_("Step: backup of the device …"))
            with link.exclusive():
                bpath = autobackup.capture_to_file(
                    link, ctx.device_folder(ctx.settings.backups_dir), profile, getattr(ctx, "firmware_key", ""),
                    ctx.info.version if getattr(ctx, "info", None) else "",
                    note=_("automatically before installing {label}").format(label=label), kind="vor-Update",
                    progress=lambda a, b: w.progress.emit(a, b))
            w.log.emit(_("Backup saved: {name}").format(name=bpath.name))
        job = _job(ctx, store, w, writing=True)
        try:
            res = FJ.install_firmware(job, raw, ask, ask_data, name=label, new_chip_ok=new_chip_ok,
                                      expected_sha256=pi.sha256)
        except FJ.WriteFailed as e:
            result["failed"] = e
            raise
        finally:
            remember_unit(ctx, job)
        result["res"] = res
        return res

    def said(res) -> str:
        again = (_("The FM-1 was restarted.") if res.restarted else _("Switch the FM-1 off and on to start it."))
        if not res.written:
            return _("The FM-1's flash already holds exactly this firmware – nothing was written.") + " " + again
        return _("{label} installed through download mode and checked: M-Flasher confirmed every sector, a fresh "
                 "read holds exactly the package from 0x04000 on, and the boot area (m-bootloader) is unchanged. "
                 "{started} The state before is kept as {name}.").format(
                     label=label, started=again, name=res.backup.name if res.backup else "?")
    was_hold = getattr(ctx, "hold", False)
    ctx.hold = True
    try:
        with write_guard():
            d = feedback.run(parent, title, work, done=said, cancellable=True,
                             cancel_tip=_("Cancels until M-Flasher starts writing; from then on it runs to its end."))
    finally:
        ctx.hold = was_hold
    res = result.get("res")
    ok = bool(getattr(d, "ok", False) and res is not None and res.verified)
    if ok and res.written and profile is not None:
        import time as _t
        profile.firmware = spec.key
        profile.firmware_version = rel.tag if rel else (tag or "")
        hist = getattr(profile, "firmware_history", None)
        if isinstance(hist, list):
            hist.append({"time": _t.time(), "firmware": spec.key, "name": spec.name,
                         "version": rel.tag if rel else (tag or ""), "identity": res.identity, "via_v15": False,
                         "route": "download-mode"})
        try:
            ctx.store.save()
        except Exception:  # noqa: BLE001
            pass
    if hasattr(ctx, "changed"):
        try:
            ctx.changed.emit()
        except Exception:  # noqa: BLE001
            pass
    return ok


# ------------------------------------------------------- "Back to …": the way back with one button

def _backup_when(p: Path, side: dict) -> str:
    try:
        created = dt.datetime.strptime(str(side.get("created") or ""), "%Y-%m-%dT%H:%M:%S%z")
        return created.strftime("%d.%m.%Y %H:%M")
    except ValueError:
        pass
    try:
        return dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%d.%m.%Y %H:%M")
    except OSError:
        return "?"


def unit_flash_backups(folder: Path | None, uid: str) -> list[dict]:
    """this unit's intact whole-flash backups, newest first: [{"path", "kind" (its boot area, read from the image:
    tools/bootarea.py), "pre_mboot" (not m-bootloader in its boot area – from before m-bootloader), "when", "firmware"
    (what its description says ran then)}]"""
    from .. import flashdump
    from ..tools import bootarea, flashjob as FJ
    if folder is None or not uid:
        return []
    out = []
    for p, kind in FJ.unit_backups(folder, uid):
        try:
            side = flashdump.read_any_side(p)
            mtime = p.stat().st_mtime
        except (OSError, flashdump.FlashDumpError):
            continue
        fw = side.get("firmware") if isinstance(side.get("firmware"), dict) else {}
        out.append({"path": p, "kind": kind, "pre_mboot": kind != bootarea.MBOOT, "when": _backup_when(p, side),
                    "firmware": str(fw.get("app") or fw.get("m_flasher_guess") or ""), "mtime": mtime})
    out.sort(key=lambda b: b["mtime"], reverse=True)
    return out


def recommended_backup(backups: list[dict]) -> dict | None:
    """the backup "Back to …" pre-selects: the newest one with the original boot area (V15's), else the newest one from
    before m-bootloader that FM1 Open did not change either (a newer unit's own factory boot area is not V15's), else
    none – the newest backup is not recommended just for being the newest"""
    from ..tools import bootarea
    return (next((b for b in backups if b["kind"] == bootarea.ORIGINAL), None) or
            next((b for b in backups if b["pre_mboot"] and b["kind"] != bootarea.OPEN), None))


def backup_label(b: dict) -> str:
    """one line per backup in the "Back to …" list"""
    from ..tools import bootarea
    parts = [b["when"]]
    if b.get("firmware"):
        parts.append(b["firmware"])
    parts.append(_(bootarea.LABEL.get(b["kind"], bootarea.LABEL[bootarea.OTHER])))
    parts.append(_("from before m-bootloader") if b["pre_mboot"] else _("with m-bootloader"))
    return " – ".join(parts)


def pre_mboot_way(backups: list[dict]) -> str:
    """the sentence that names the way back to the state before m-bootloader – honest about whether the app has one"""
    rec = recommended_backup(backups)
    if rec is not None:
        return _("or write back the whole-flash backup of {date} from before m-bootloader (pre-selected in the list "
                 "above).").format(date=rec["when"])
    return _("The app has no whole-flash backup of this unit from before m-bootloader. If you made one earlier with "
             "M-Flasher, take it in with “Import a flash backup …” (Recovery › Tools) – then it is offered here.")


def way_back_options(ctx) -> list[dict]:
    """the targets "Back to …" offers for the active unit, each with its route (the existing gated actions, in their
    order) – [{"key", "title", "route", "ok", "why", "path"}]; "ok" False: shown with the reason, not started.
    a) a whole-flash backup of this unit – each of them ("backups", newest first, marked from before m-bootloader or
    not; "path" = the recommended one: the newest from before m-bootloader, else the newest); b) SLOOP 3 over MIDI
    MODE (m-bootloader first where it is missing); c) a Felucca-family firmware (Firmware+: through download mode with
    m-bootloader, else the usual way); d) the original V15 with the original boot area (removing m-bootloader – an
    earlier unit only – then the original V15 the usual way)."""
    from .. import hardware
    from ..devices import profile_unit_ids
    from ..tools import bootarea
    from .device import firmware_name
    prof = None if getattr(ctx, "is_virtual", False) else getattr(ctx, "profile", None)
    out: list[dict] = []
    uid = next((f for f in profile_unit_ids(prof) if len(f) == 16), "") if prof is not None else ""
    folder = Path(ctx.settings.backups_dir) / prof.folder_name / "Flash" if prof is not None else None
    backups = unit_flash_backups(folder, uid)
    rec = recommended_backup(backups)
    last = (rec or backups[0])["path"] if backups else None
    out.append({"key": "backup", "path": last, "ok": last is not None, "backups": backups,
                "recommended": rec["path"] if rec is not None else None,
                "title": _("Back to a whole-flash backup of this unit"),
                "route": _("Write flash back: download mode, a fresh backup of the state now (read twice), your "
                           "confirmation, M-Flasher writes the differing sectors, a fresh read checks them. Choose the "
                           "backup below – the ones from before m-bootloader are marked.") if last is not None else "",
                "why": "" if last is not None else
                (_("The app has no intact whole-flash backup of this unit (by its unit ID). A backup made earlier with "
                   "M-Flasher can be taken in with “Import a flash backup …” (Recovery › Tools).") if uid else
                 _("The app does not know this unit's ID yet: a whole-flash backup (Recovery › Tools) teaches it."))})
    cur = bootarea.current(prof) if prof is not None else {}
    mboot = cur.get("kind") == bootarea.MBOOT or getattr(ctx, "mboot", None) is not None
    rev = hardware.revision(prof) if prof is not None else hardware.UNKNOWN
    out.append({"key": "sloop3", "path": None, "ok": prof is not None, "title": _("Back to SLOOP 3"),
                "route": (_("m-bootloader's MIDI MODE (hold PLAY while switching on → MIDI MODE): the app reads the "
                            "whole flash twice there, asks, and writes the bundled SLOOP 3.") if mboot else
                          _("First m-bootloader (download mode, a whole-flash backup, your confirmation), then "
                            "m-bootloader's MIDI MODE: the flash read twice once more, your confirmation, SLOOP 3.")),
                "why": "" if prof is not None else _("Choose the device at the top first.")})
    out.append({"key": "felucca", "path": None, "ok": True,
                "title": _("Back to a Felucca-family firmware (Melodee, SLOOP 2, Skiff …)"),
                "route": (_("Firmware+ › Install & Versions: the plan installs it through download mode with "
                            "M-Flasher and keeps m-bootloader (a whole-flash backup first).") if mboot else
                          _("Firmware+ › Install & Versions: the usual way, the plan says which steps."))})
    v15_ok = rev != hardware.NEW
    fw = (getattr(prof, "firmware", "") or "").lower() if prof is not None else ""
    from .. import fwfamily
    runs_usual = fw.startswith("stock") or (bool(fwfamily.by_key(fw)) and fw not in ("sloop3", "sloop-3") and
                                            bootarea.changer(fw) is None)
    s3_runs = getattr(ctx, "sloop3", None) is not None or fw in ("sloop3", "sloop-3")
    if s3_runs:
        runs_usual = False
    fw_text = "SLOOP 3" if s3_runs else (firmware_name(fw) or fw or _("not known"))
    v15_why = ("" if not v15_ok or runs_usual or not (cur.get("kind") in bootarea.NOT_ORIGINAL or mboot) else
               (_("From the firmware on this FM-1 ({fw}) the original V15 cannot be installed the usual way after the "
                  "boot area was put back: SLOOP 3 has no update mode.") if s3_runs else
                _("From the firmware on this FM-1 ({fw}) the app cannot install the original V15 the usual way after "
                  "the boot area was put back.")).format(fw=fw_text) + " " +
               _("First go to a Felucca-family firmware (the choice above), then come back here –") + " " +
               pre_mboot_way(backups))
    v15_ok = v15_ok and not v15_why
    out.append({"key": "v15", "path": None, "ok": v15_ok and prof is not None,
                "title": _("Back to the original V15 with the original boot area"),
                "route": ((_("First “Remove m-bootloader (original boot area)” – download mode, a whole-flash "
                             "backup, your confirmation, only the boot area is written – then the original V15 the "
                             "usual way on Firmware+.") if cur.get("kind") in bootarea.NOT_ORIGINAL or mboot else
                           _("The original V15 the usual way on Firmware+ (the boot area is the original one or not "
                             "known; the plan checks it)."))),
                "why": "" if v15_ok else v15_why or (
                    _("A newer unit (logo on the front): the app never writes V15's boot area there. The way back to "
                      "the factory state is a whole-flash backup of this unit from before m-bootloader.") + " " +
                    (_("The app has one: {date} (pre-selected in the list above).").format(date=rec["when"])
                     if rec is not None else pre_mboot_way(backups)))})
    return out


def way_back_flow(parent, ctx, store=None) -> str:
    """"Back to …": one dialog with the targets and their routes – for a whole-flash backup, the list of this unit's
    backups (the newest from before m-bootloader pre-selected); the chosen one runs the existing gated actions in their
    order, each with its own confirmation (never a skipped backup). -> the key chosen, "" none"""
    opts = way_back_options(ctx)
    dlg = way_back_dialog(parent, opts)
    if not dlg.exec():
        return ""
    o, path = dlg.choice()
    if o is None:
        return ""
    if o["key"] == "backup":
        restore_flow(parent, ctx, path, store=store)
    elif o["key"] == "sloop3":
        from . import sloop3_ui
        sloop3_ui.install_flow(parent, ctx, store=store)
    elif o["key"] == "felucca":
        _go_firmware(parent)
    elif o["key"] == "v15":
        from ..tools import bootarea
        cur = bootarea.current(getattr(ctx, "profile", None))
        if cur.get("kind") in bootarea.NOT_ORIGINAL or getattr(ctx, "mboot", None) is not None:
            if not remove_boot_flow(parent, ctx, store=store, then_v15=True):
                return o["key"]
        from .common import show_done
        show_done(parent, _("Back to …"), _("Next: Firmware+ › Install & Versions › the original V15 – the plan "
                                            "checks the boot area once more."))
        _go_firmware(parent)
    return o["key"]


def way_back_dialog(parent, opts: list[dict]):
    """the "Back to …" question: a radio button per reachable target (its route below it, wrapped – no cut-off
    button labels), the list of this unit's whole-flash backups, and the targets that are not reachable with the
    reason. .choice() -> (option, backup path) after exec()"""
    from PyQt6.QtWidgets import (QButtonGroup, QDialog, QDialogButtonBox, QLabel, QListWidget, QListWidgetItem,
                                 QRadioButton, QScrollArea, QVBoxLayout, QWidget)
    from PyQt6.QtCore import Qt
    from ..flash import tested

    class Dlg(QDialog):
        def __init__(self):
            super().__init__(parent)
            self.setWindowTitle(_("Back to …"))
            self.setMinimumWidth(560)
            outer = QVBoxLayout(self)
            intro = QLabel(_("Where should this FM-1 go back to? Each way runs the app's own steps one after the "
                             "other, each with its own question; none skips a backup."))
            intro.setWordWrap(True)
            outer.addWidget(intro)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            body = QWidget()
            lay = QVBoxLayout(body)
            scroll.setWidget(body)
            outer.addWidget(scroll, 1)
            self.group = QButtonGroup(self)
            self.radios: list[tuple[QRadioButton, dict]] = []
            self.backups: QListWidget | None = None
            for o in opts:
                if o["ok"]:
                    rb = QRadioButton(o["title"])
                    rb.setObjectName(f"wayBack-{o['key']}")
                    self.group.addButton(rb)
                    self.radios.append((rb, o))
                    lay.addWidget(rb)
                    text = o["route"]
                else:
                    head = QLabel("<b>" + html.escape(o["title"]) + "</b>")
                    head.setWordWrap(True)
                    lay.addWidget(head)
                    text = o["why"] or o["route"]
                lab = QLabel(text)
                lab.setTextFormat(Qt.TextFormat.PlainText)   # (plain: "&" must show as "&", not as "&amp;")
                lab.setWordWrap(True)
                lab.setContentsMargins(24, 0, 0, 6)
                lab.setObjectName(f"wayBackText-{o['key']}")
                lay.addWidget(lab)
                if o["key"] == "backup" and o["ok"]:
                    lst = QListWidget()
                    lst.setObjectName("wayBackBackups")
                    lst.setWordWrap(True)
                    for b in o.get("backups") or []:
                        label = backup_label(b)
                        if o.get("recommended") == b["path"]:
                            label += " – " + _("recommended")
                        it = QListWidgetItem(label)
                        it.setData(Qt.ItemDataRole.UserRole, str(b["path"]))
                        it.setToolTip(b["path"].name)
                        lst.addItem(it)
                        if b["path"] == o["path"]:
                            lst.setCurrentItem(it)
                    lst.setMaximumHeight(140)
                    lst.itemSelectionChanged.connect(lambda r=rb: r.setChecked(True))
                    lay.addWidget(lst)
                    self.backups = lst
            note = QLabel("<small>" + html.escape(tested.tool_note("way_back")) + "</small>")
            note.setWordWrap(True)
            lay.addWidget(note)
            lay.addStretch(1)
            bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
            self.ok = bb.addButton(_("Continue"), QDialogButtonBox.ButtonRole.AcceptRole)
            self.ok.setEnabled(False)
            bb.accepted.connect(self.accept)
            bb.rejected.connect(self.reject)
            outer.addWidget(bb)
            self.group.buttonToggled.connect(lambda *_a: self.ok.setEnabled(self.group.checkedButton() is not None))
            self.resize(640, 560)

        def choice(self):
            rb = self.group.checkedButton()
            o = next((o for r, o in self.radios if r is rb), None)
            if o is None:
                return None, None
            path = o.get("path")
            if o["key"] == "backup" and self.backups is not None and self.backups.currentItem() is not None:
                path = Path(self.backups.currentItem().data(Qt.ItemDataRole.UserRole))
            return o, path
    return Dlg()


def _go_firmware(parent):
    w = parent.window() if hasattr(parent, "window") else None
    go = getattr(w, "go", None)
    if callable(go):
        try:
            go("firmware")
        except Exception:  # noqa: BLE001
            pass


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
    if str(rec.get("op") or "") in ("firmware", "sloop3-install"):
        what_op = (_("Installing {identity} through download mode").format(
            identity=html.escape(str(rec.get("identity") or "?"))) if rec.get("op") == "firmware" else
            _("Installing SLOOP 3 over m-bootloader's MIDI MODE"))
        return "<br><br>".join([
            _("{what} onto “{device}” (unit ID {uid}) started on {when} did not end verified – the app was closed or "
              "ended while it wrote, or the write failed.").format(
                what=what_op, device=html.escape(str(rec.get("device") or "?")),
                uid=html.escape(str(rec.get("unit") or "–")), when=when),
            what,
            _("The boot area (m-bootloader) was not written, so the FM-1 still reaches its menu (hold PLAY while "
              "switching it on) and download mode. Nothing is done automatically. Its state before the write: {pre} "
              "– writing it back goes through all the usual checks; installing again works as well.").format(
                pre=html.escape(Path(str(rec.get("pre_backup") or "?")).name))])
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
    boot = str(rec.get("op") or "").startswith("bootloader") or rec.get("op") in ("firmware", "sloop3-install")
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
