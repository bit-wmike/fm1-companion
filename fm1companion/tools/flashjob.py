# SPDX-License-Identifier: GPL-3.0-only
"""Whole-flash backup and restore through the bundled M-Flasher (no Qt; the pages are in ui/flash_tools.py).

**Back up the whole flash** (`backup`): the FM-1 in USB download mode (the app sends it there with M-Flasher's
``download-mode`` – the soft key – when exactly one FM-1 runs on USB MIDI; otherwise the user is guided), then
``m-flasher backup`` **twice**. Each run reads the whole flash and checks it against the CRC-32 the chip computes
("Checked: the chip's CRC32 of the flash matches the read" is required); both images must be byte for byte the same,
of the same unit id, flash ID and chip key, and each must match the SHA-256 M-Flasher wrote beside it. Only then one
image is kept under ``Backups/<device>/Flash/`` with the app's description (flashdump.write_backup: unit id, flash
ID, chip key, SHA-256, CRC-32, the boot area's SHA-256, M-Flasher's version and binary hash, the firmware the app
knew, the date). M-Flasher's staging files are removed.

**Write flash back** (`restore`) – the first write of the app through M-Flasher – only when all of this holds:

1. the M-Flasher binary is one this app release cleared for writing (manifest.json write_verified, by bytes);
2. the image matches its description, and the description's unit id **and** flash ID equal what M-Flasher's
   ``info`` reports for the connected unit **right before** (an image of another unit or chip is refused – no
   override), and the app knows which of its profiles this unit is (by that unit id);
3. a fresh whole-flash backup of the current state was made just before (`backup_before_boot_write`, the same
   double read) – without it nothing is written;
4. the user confirmed a danger dialog naming the unit, its revision, the image's date and firmware, and that the
   boot area is included;
5. M-Flasher's own question: it prints the unit id, the flash ID and the planned sector ranges of this very session
   and waits for "yes". The gate (`restore_gate`) parses that block and compares it with the plan – the unit id,
   flash ID and chip key read before, exactly the 4 KiB sectors where the image differs from the backup just made,
   "The whole flash is restored, the bootloader included." (M-Flasher sees it as this unit's backup) and no WARNING.
   Any difference: stdin is closed and M-Flasher writes nothing ("Nothing written.").

Afterwards: M-Flasher's "Done: everything written is verified" line is required, its own automatic backup is
compared with the one made before, and a fresh read (one more ``backup``) must equal the image. Then ``restart``.

Every step and every line of each M-Flasher run go into one flight log per job (``mflasher-backup-…`` /
``mflasher-restore-…`` beside the installation logs, unit ids masked), and the one-installation-at-a-time lock is held for the whole job.

**Step 3** (same rules, same write path `_Write`): "Into download mode" / "Restart the FM-1" on their own
(`download_mode`, `leave_download_mode`); "Install m-bootloader" (`install_bootloader`: the pinned bundled
fm1-uboot.boot through ``bootloader --file``) and "Remove m-bootloader (original boot area)" (`remove_bootloader`:
only ``bootloader --stock`` with M-VAVE's V15 package) – both only after the double-read backup, with a danger
dialog, a gate that accepts only ranges inside the boot area 0x00000-0x03FFF (`boot_gate`), and a fresh read
afterwards. What the app learns about the boot area goes into Profile.boot_area (tools/bootarea.py).

The unit id and flash ID M-Flasher reads go into the profile (hardware.note_flash_id, the unit id learned as on
the MIDI side, ui/device.py `_unit_by_id`): only into the profile whose unit the app itself sent into download mode
and only when no other profile has that id. An image's description never names or changes a profile by itself.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..i18n import _
from . import mflasher_parse as mp
from . import runner

log = logging.getLogger(__name__)

FLASH_SIZE = 0x100000
SECTOR = 0x1000
BOOT_AREA = 0x4000
FACTORY = (0xFF000, 0x100000)                   # the unit's factory data: chip key record, calibration, Bluetooth MAC
RECORD = "mflasher-write.json"                  # beside the installation journal: a write-back in progress
WRITING = threading.Event()                     # set from "yes" until M-Flasher has ended (blocks quitting)
WRITER = {"who": "M-Flasher"}                   # who writes while WRITING is set ("m-bootloader": SLOOP 3 over MIDI
#                                                 MODE – then the app itself hands it the data and must stay open)
INTENDED: dict = {}                             # USB port -> (why, time): a unit in download mode on purpose
GUIDE_SHOWN: dict = {}                          # {"at": time} – the app asked the user to put an FM-1 there by hand
INTENDED_SECS = 30 * 60
OVERDUE = threading.Event()                     # … and M-Flasher is far past its own worst case: quitting is allowed
UNKNOWN_FOLDER = "Unbekanntes Gerät"            # the app's folder for an FM-1 without profile  # i18n: ignore


class Refused(Exception):
    """a ready (translated) sentence; nothing was written to the FM-1 (unless `written`)"""

    def __init__(self, reason: str, kind: str = "", written: bool = False):
        super().__init__(reason)
        self.reason, self.kind, self.written = reason, kind, written


class WriteFailed(Refused):
    """the write was started and did not end verified: the FM-1 stays in download mode"""

    def __init__(self, reason: str, kind: str = "", backup: Path | None = None):
        super().__init__(reason, kind, written=True)
        self.backup = backup


@dataclass
class Refusal:
    """backup_before_boot_write's "no": why (translated) and a short code for tests and logs"""
    reason: str
    kind: str = ""


def guide_text() -> str:
    return _("Put the FM-1 into USB download mode by hand, then click again:\n"
             "• with m-bootloader: hold PLAY while switching the FM-1 on and choose DOWNLOAD MODE – or hold REC "
             "while switching it on, which goes there directly;\n"
             "• with the original firmware or a Felucca-family firmware (Skiff, SLOOP 2, Melodee …), the app sends "
             "it there by itself while it runs on USB MIDI.\n"
             "In download mode the display stays dark or shows M-Flasher's screen; the computer lists a USB device "
             "“WL80UBOOT1.00” (USB 4c4a:8057 – earlier and newer units alike; M-Flasher writes it “WL82 UBOOT1.00”).")


def mask(uid: str) -> str:
    """a unit id for messages and logs: its last four hex digits"""
    return ("…" + uid[-4:]) if uid else "–"


def chip_name(flash_id: int) -> str:
    from .. import hardware
    name = {hardware.NEW_ID: hardware.NEW_CHIP, hardware.OLD_ID: hardware.OLD_CHIP}.get(flash_id, "")
    return f"{name} (0x{flash_id:06X})" if name else f"0x{flash_id:06X}"


class _Held:
    """the job's lock handed to each M-Flasher run: acquired once for the whole job (journal.acquire is not
    re-entrant). A run that hangs in the kernel hands the release over to the runner's reaper."""

    def __init__(self, journal):
        self.j, self.handover, self.pid = journal, False, 0

    def acquire(self):
        pass

    def release(self):
        if self.handover:
            self.j.release()

    def set_tool(self, pid: int):
        self.pid = pid
        if hasattr(self.j, "set_tool"):
            self.j.set_tool(pid)

    def clear_tool(self):
        if hasattr(self.j, "clear_tool"):
            self.j.clear_tool()


@dataclass
class Saved:
    path: Path
    sha256: str
    image: bytes = b""


@dataclass
class Job:
    """what a flash job needs from the app – no Qt. `profile`: the device the user had active when clicking (None:
    the FM-1 may already be in download mode; then only its unit id can name a profile)."""
    backups_dir: Path
    profiles: object = None                    # devices.ProfileStore
    profile: object = None                     # devices.Profile the click was for
    store: object = None                       # tools.store.Store
    log_dir: Path | None = None
    journal: object = None                     # flash.journal.Journal (the lock)
    cancel: object = None                      # .is_set() – honoured until "yes" is sent
    say: object = None                         # fn(text): one line for the user
    softkey_for: str = ""                      # the app's own connected FM-1 that may get M-Flasher's soft key: its
    #                                            MIDI port name; "" = the app did not recognise one that accepts it
    on_writing: object = None                  # fn(): M-Flasher is about to get "yes" (the UI disables Cancel)
    on_overdue: object = None                  # fn(): long after "yes" M-Flasher has still not ended
    inhibit: object = None                     # fn(why, pid) -> handle with .release(): hold off shutdown/suspend
    #                                            while `pid` lives (the UI: systemd-inhibit, registered before return)
    usb_path: str = ""                         # the USB port where the app last saw `profile` running (just before)
    work_dir: Path | None = None               # private local folder for M-Flasher's files (default: private_dir())
    unit_usb: str = ""                         # the USB port of the unit in download mode (where it comes back)
    purpose: str = ""                          # why it is in download mode: button | backup | restore | bootloader
    progress: object = None                    # fn(done, total, checking) – SLOOP 3's install over MIDI MODE
    # filled in on the way
    device: str = ""                           # /dev/sgN
    info: mp.Info | None = None                # M-Flasher's info of this session
    sent_by_app: bool = False                  # the app sent the unit into download mode (soft key) in this job
    flight: object = None
    held: _Held | None = None
    steps: list = field(default_factory=list)  # (step, outcome) for the report
    pre: Saved | None = None                   # the automatic backup made before a write
    log_path: Path | None = None               # this job's flight log

    def tell(self, text: str):
        if self.say:
            try:
                self.say(text)
            except Exception:  # noqa: BLE001 – a UI callback never stops the job
                pass

    def ev(self, kind: str, text: str):
        if self.flight is not None:
            self.flight.ev(kind, runner.mask_ids(text))

    def cancelled(self) -> bool:
        return bool(self.cancel is not None and self.cancel.is_set())

    @property
    def folder(self) -> Path:
        name = self.profile.folder_name if self.profile is not None else UNKNOWN_FOLDER
        return Path(self.backups_dir) / name / "Flash"


# ------------------------------------------------------------------------------------------------ running M-Flasher

def _mf(job: Job, args: list[str], gate=None, out_dir=None) -> runner.RunResult:
    job.ev("step", "m-flasher " + " ".join(args))
    try:
        r = runner.run_mflasher(args, store=job.store, cancel=job.cancel, log_dir=job.log_dir, journal=job.held,
                                gate=gate, flight=job.flight, out_dir=out_dir,
                                on_line=lambda kind, ln: _note(job, ln) if kind == "note" else None)
    except runner.ToolStuck:
        job.held.handover = True                 # (the reaper releases the lock when it has really ended)
        raise
    except runner.ToolError as e:
        raise Refused(str(e), "tool") from e
    outcome = "ok" if r.ok else ("cancelled" if r.cancelled else ("timeout" if r.timed_out else f"exit {r.rc}"))
    job.ev("result", f"{args[0]}: {outcome}")
    if r.cancelled and not r.answered_yes:
        raise Refused(_("Cancelled. Nothing was written to the FM-1."), "cancelled")
    if r.timed_out and not r.answered_yes:
        raise Refused(_("M-Flasher did not finish within {s} s and was stopped. Nothing was written to the "
                        "FM-1.").format(s=int(runner.TIMEOUTS.get(args[0], 60))), "timeout")
    return r


def _note(job: Job, text: str):
    """the runner's note that M-Flasher still writes long after "yes" (runner.WRITE_SOFT_LIMIT, far beyond its own
    worst case): say so, and from now on let the user close the app – M-Flasher keeps running on its own"""
    job.tell(text)
    if WRITING.is_set():
        OVERDUE.set()
        job.ev("overdue", "M-Flasher still writes past the soft limit: closing the app is allowed now (never a kill)")
        if job.on_overdue:
            try:
                job.on_overdue()
            except Exception:  # noqa: BLE001
                pass


def _why(r: runner.RunResult) -> str:
    return mp.refusal(r.err) or (_("exit code {rc}").format(rc=r.rc) if r.rc is not None else _("it did not start"))


# ------------------------------------------------------------------------------------------------ download mode

def ensure_download_mode(job: Job) -> str:
    """exactly one FM-1 in USB download mode -> its /dev/sgN. Sends the soft key (`download-mode`) when exactly one
    FM-1 runs on USB MIDI and none is in download mode; otherwise Refused (kind "guide": the user is guided)."""
    r = _mf(job, ["find"])
    found = mp.parse_find(r.out, r.err) if r.ok else None
    if found is None:
        raise Refused(_("M-Flasher could not look for the FM-1: {reason}").format(reason=_why(r)), "find")
    if found.sg_missing:
        raise Refused(_("An FM-1 is in download mode, but the Linux driver “sg” is not loaded. The app's installer "
                        "sets it up; by hand: sudo modprobe sg – then try again. Nothing was written."), "sg")
    if len(found.download) > 1:
        raise Refused(_("More than one device is in download mode. Leave only the FM-1 you mean connected, then "
                        "try again. Nothing was written."), "several")
    if found.download:
        if found.midi:
            raise Refused(_("One FM-1 is in download mode and another one runs on USB MIDI. So that the app surely "
                            "works with the right unit, leave only one FM-1 connected. Nothing was written."),
                          "mixed")
        job.device = found.download[0].device
        job.ev("step", f"in download mode already: {job.device}")
        _intended(job.device, "used", job)
        return job.device
    if len(found.midi) > 1:
        raise Refused(_("More than one FM-1 is connected. M-Flasher's switch to download mode reaches only a single "
                        "unit: unplug the others, then try again. Nothing was written."), "several")
    if not found.midi:
        GUIDE_SHOWN["at"] = time.time()
        raise Refused(_("No FM-1 found, neither running on USB MIDI nor in download mode.") + "\n\n" + guide_text(),
                      "guide")
    if not softkey_target_ok(found.midi[0], job.softkey_for):
        job.ev("step", f"soft key not sent: M-Flasher sees {found.midi[0]!r}, the app's own FM-1 is "
                       f"{job.softkey_for or 'none it recognised'}")
        GUIDE_SHOWN["at"] = time.time()
        raise Refused(_("The app sends the switch to download mode only to an FM-1 it has recognised itself, with "
                        "the original firmware, a Felucca-family firmware or SLOOP 3, at the port it is connected "
                        "to.") + "\n\n" + guide_text(), "guide")
    job.tell(_("Sending the FM-1 into USB download mode (it restarts; nothing is written) …"))
    r = _mf(job, ["download-mode"])
    if not r.ok:
        GUIDE_SHOWN["at"] = time.time()
        raise Refused(_("The FM-1 did not switch to download mode: {reason}").format(reason=_why(r)) + "\n\n" +
                      guide_text(), "guide")
    job.sent_by_app = True
    dev = mp.download_device(r.out)
    if not dev:
        r = _mf(job, ["find"])
        found = mp.parse_find(r.out, r.err) if r.ok else None
        if found is None or len(found.download) != 1:
            raise Refused(_("The FM-1 did not show up in download mode.") + "\n\n" + guide_text(), "guide")
        dev = found.download[0].device
    job.device = dev
    _intended(dev, "sent", job)
    job.steps.append(("download-mode", "ok"))
    return dev


def _usb_path_of(device: str) -> str:
    """the USB port ("1-1.2") of the unit in ROM download mode at /dev/sgN ("" unknown)"""
    try:
        from ..flash import uboot
        return next((d.usb_path for d in uboot.scan() if d.sg == device), "")
    except Exception:  # noqa: BLE001 – only a hint
        return ""


def _intended(device: str, why: str, job: Job | None = None):
    usb = _usb_path_of(device)
    if job is not None and usb:
        job.unit_usb = usb
    if usb:
        INTENDED[usb] = (why, time.time(), getattr(job, "purpose", "") if job is not None else "")


def intended_download_mode(usb_path: str) -> str:
    """is the unit in ROM download mode at this USB port there on purpose? "sent" (the app's flash job sent it),
    "used" (a flash job worked with it there), "guide" (the app just asked the user to put an FM-1 there by hand –
    with the keys or m-bootloader's menu); "" not known – then it may be the emergency"""
    now = time.time()
    why, at = INTENDED.get(usb_path or "", ("", 0.0, ""))[:2]
    if why and now - at < INTENDED_SECS:
        return why
    if GUIDE_SHOWN.get("at", 0) > now - INTENDED_SECS:
        return "guide"
    return ""


def intended_reason(usb_path: str) -> str:
    """what put the unit there on purpose: "button" ("Into download mode"), "backup", "restore", "bootloader"
    (installing / removing m-bootloader), "guide" (the user, with the keys or m-bootloader's menu), "" not known"""
    why = intended_download_mode(usb_path)
    if why == "guide":
        return "guide"
    entry = INTENDED.get(usb_path or "", ("", 0.0, ""))
    return (entry[2] if len(entry) > 2 else "") if why else ""


def softkey_target_ok(card: str, own_port: str) -> bool:
    """may M-Flasher's soft key go to the unit it sees on USB MIDI (its ALSA card name)? Only when that is the FM-1
    the app itself recognised (`own_port`, its MIDI port name, starts with the card name) and the name is an FM-1
    spelling – never an SMK-37, an update loader or m-bootloader's MIDI MODE ("FM-1 Update")."""
    c = (card or "").strip()
    if not c or not own_port or re.search(r"smk|update|ota-", c, re.I):
        return False
    if not re.search(r"fm-?1|minifm|felucca", c, re.I):
        return False
    return own_port.strip().lower().startswith(c.lower())


def restart(job: Job) -> bool:
    """leave download mode (the unit starts its firmware); False: it did not (the user switches it off and on)"""
    if not job.device:
        return False
    try:
        r = _mf(job, ["restart", "--device", job.device])
    except Refused:
        return False
    job.steps.append(("restart", "ok" if r.ok else "failed"))
    return r.ok


# ------------------------------------------------------------------------------------------------ identity

def identify(job: Job, *, need_profile: bool = False) -> mp.Info:
    """`info` of the unit in download mode, and which profile it is. The profile learns the unit id and the flash
    ID only when the app sent that very unit into download mode in this job and no other profile has that id."""
    from .. import hardware
    from ..devices import profile_unit_ids, unit_id_forms
    r = _mf(job, ["info", "--device", job.device])
    if not r.ok:
        raise Refused(_("M-Flasher could not read the FM-1: {reason} Nothing was written.").format(reason=_why(r)),
                      "info")
    try:
        info = mp.parse_info(r.out)
    except mp.ParseError as e:
        raise Refused(_("M-Flasher's answer has a form this app version does not know ({detail}). Nothing was "
                        "written.").format(detail=e), "parse") from e
    if "smk" in info.model.lower():
        raise Refused(_("M-Flasher sees a {model}, not an FM-1. Nothing was written.").format(model=info.model),
                      "model")
    job.info = info
    uid = info.unit_id if unit_id_forms(info.unit_id) else ""     # (a placeholder id names nothing, is learned nowhere)
    job.ev("info", f"flash 0x{info.flash_id:06X}, chip key 0x{info.chip_key:04X}, unit id {mask(uid)}, "
                   f"bootloader {info.bootloader}")
    store = job.profiles
    hits = store.by_unit_id(uid) if (store is not None and uid) else []
    by_port = bool(job.usb_path) and _usb_path_of(job.device) == job.usb_path
    if job.profile is not None and not job.sent_by_app and not by_port:
        job.ev("profile", "the unit was in download mode already, not at the active profile's port: only its unit "
                          "id names a profile")
    # the profile of the click counts when the app sent its unit into download mode in this job, or when the unit
    # in download mode sits at the very USB port where the app just saw that profile running (the guided path);
    # otherwise only the unit id M-Flasher reads names a profile
    prof = job.profile if (job.sent_by_app or by_port) else None
    if prof is not None:
        known = {f for f in profile_unit_ids(prof) if len(f) == 16}
        if uid and known and not (unit_id_forms(uid) & known):
            raise Refused(_("This FM-1 (unit ID {uid}) is not “{name}”: that profile belongs to another unit. "
                            "Nothing was written.").format(uid=mask(uid), name=prof.name), "other-profile")
        other = [h for h in hits if h is not prof]
        if other:
            raise Refused(_("This FM-1 is “{other}” (by its unit ID {uid}), not “{name}”. Choose the right device "
                            "at the top. Nothing was written.").format(other=other[0].name, uid=mask(uid),
                                                                        name=prof.name), "other-profile")
        trusted = job.sent_by_app or by_port
    else:
        if len(hits) > 1:
            raise Refused(_("Two of your device profiles have this unit ID ({uid}): merge them on the page "
                            "“Devices” first. Nothing was written.").format(uid=mask(uid)), "two-profiles")
        prof = hits[0] if hits else None
        trusted = prof is not None              # (named by its unit id: the strongest evidence there is)
    if prof is None and need_profile:
        raise Refused(_("The app does not know which of your FM-1 this is (unit ID {uid} is in none of your device "
                        "profiles). A flash backup is written back only onto a unit the app knows. To teach it: "
                        "connect the FM-1 while it runs, choose it at the top, click “Back up the whole flash” – and "
                        "if the app asks you to put it into download mode by hand, do it at the same USB port. "
                        "Nothing was written.").format(uid=mask(uid)), "unknown-unit")
    rec = getattr(prof, "flash_id", "") if prof is not None else ""
    if prof is not None and rec and hardware.was_read(prof):
        try:
            if int(rec, 16) != info.flash_id:
                raise Refused(_("The flash chip M-Flasher reads ({chip}) is not the one recorded for “{name}” "
                                "({rec}). Is this another FM-1? Nothing was written.").format(
                                    chip=chip_name(info.flash_id), name=prof.name, rec=rec), "other-chip")
        except ValueError:
            pass
    if prof is not None and trusted:
        changed = False
        if info.m_bootloader:                    # (M-Flasher reads m-bootloader's container: its version too)
            from . import bootarea
            cur = bootarea.current(prof)
            if cur.get("kind") != bootarea.MBOOT or cur.get("bootloader") != bootarea.mboot_name(info.bootloader):
                changed |= bootarea.note(prof, bootarea.MBOOT, "m-flasher", bootloader=info.bootloader)
                job.ev("profile", f"boot area: {info.bootloader}")
        if uid and not {f for f in profile_unit_ids(prof) if len(f) == 16}:
            prof.unit_id = uid
            changed = True
            job.ev("profile", f"learned unit id {mask(uid)}")
        if hardware.note_flash_id(prof, info.flash_id, "rom"):
            changed = True
            job.ev("profile", f"flash 0x{info.flash_id:06X} recorded")
        if changed and store is not None:
            try:
                store.save()
            except OSError as e:
                log.warning("profile not saved: %s", e)
    job.profile = prof
    job.steps.append(("info", "ok"))
    return info


# ------------------------------------------------------------------------------------------------ the double read

def _one_read(job: Job, staging: Path, n: int) -> tuple[bytes, mp.Info]:
    job.tell(_("Reading the whole flash ({n} of 2; nothing is written) …").format(n=n))
    r = _mf(job, ["backup", "--device", job.device, "--backup-dir", str(staging)])
    if not r.ok:
        raise Refused(_("M-Flasher could not read the flash: {reason} Nothing was written.").format(reason=_why(r)),
                      "read")
    try:
        info = mp.parse_info(r.out)
    except mp.ParseError as e:
        raise Refused(_("M-Flasher's answer has a form this app version does not know ({detail}). Nothing was "
                        "written.").format(detail=e), "parse") from e
    if not info.crc_checked:
        raise Refused(_("The flash read does not match the CRC-32 the chip computed (M-Flasher did not confirm it). "
                        "The USB connection may be unreliable: try another cable or port, without a hub. Nothing "
                        "was kept, nothing was written."), "crc")
    paths = mp.saved_paths(r.out)
    if len(paths) != 1:
        raise Refused(_("M-Flasher did not say where it saved the read. Nothing was kept."), "saved")
    p = Path(paths[0])
    if p.parent.resolve() != staging.resolve() or p.suffix != ".bin":
        raise Refused(_("M-Flasher saved the read in an unexpected place. Nothing was kept."), "saved")
    try:
        img = p.read_bytes()
        import json
        side = json.loads(p.with_suffix(".json").read_text("utf-8"))
    except (OSError, ValueError) as e:
        raise Refused(_("The read cannot be opened: {error}. Nothing was kept.").format(
            error=getattr(e, "strerror", None) or e), "saved") from e
    if len(img) != FLASH_SIZE:
        raise Refused(_("The read has {size} bytes, not the 1 MiB of the whole flash. Nothing was kept.").format(
            size=len(img)), "size")
    if str(side.get("sha256", "")).lower() != hashlib.sha256(img).hexdigest():
        raise Refused(_("The read does not match the SHA-256 M-Flasher wrote beside it. Nothing was kept."), "sha")
    want = job.info
    if want is not None and (info.unit_id, info.flash_id, info.chip_key) != (want.unit_id, want.flash_id,
                                                                            want.chip_key):
        raise Refused(_("Between two reads M-Flasher reported another unit or chip. Leave only one FM-1 connected. "
                        "Nothing was kept, nothing was written."), "unit-changed")
    job.ev("read", f"read {n}: sha256 {hashlib.sha256(img).hexdigest()}, chip CRC-32 confirmed")
    return img, info


REMOTE_FS = {"nfs", "nfs4", "cifs", "smb3", "smbfs", "sshfs", "fuse.sshfs", "fuse.rclone", "9p", "afs", "ceph",
             "glusterfs", "fuse.gvfsd-fuse", "davfs", "fuse.davfs2"}


def _fs_type(path: Path) -> str:
    """the file system type of the mount `path` lives on (/proc/self/mounts); "" unknown"""
    try:
        best, kind = "", ""
        real = str(Path(path).resolve())
        for line in Path("/proc/self/mounts").read_text().splitlines():
            parts = line.split()
            if len(parts) >= 3:
                mnt = parts[1].replace("\\040", " ")
                if (real == mnt or real.startswith(mnt.rstrip("/") + "/")) and len(mnt) >= len(best):
                    best, kind = mnt, parts[2]
        return kind
    except OSError:
        return ""


def private_dir() -> Path:
    """where M-Flasher's own files go (its output, its backups, the image copy it writes from): a private 0700
    folder on a LOCAL file system – $XDG_RUNTIME_DIR (in RAM), else the app's cache in the home folder. Never the
    user's backup folder, which may be a USB stick or a network share (a write error there would abort M-Flasher)."""
    import os as _os
    from .. import config
    cands = []
    rt = _os.environ.get("XDG_RUNTIME_DIR", "")
    if rt and Path(rt).is_dir():
        cands.append(Path(rt) / "fm1-companion" / "m-flasher")
    cands.append(Path(config.CACHE_DIR) / "m-flasher-work")
    for c in cands:
        try:
            c.mkdir(parents=True, exist_ok=True)
            c.chmod(0o700)
            if _fs_type(c) in REMOTE_FS or not _os.access(c, _os.W_OK):
                continue
            return c
        except OSError:
            continue
    raise OSError(0, "no private local folder for M-Flasher's files")


def _staging(job: Job, what: str) -> Path:
    base = job.work_dir if job.work_dir is not None else private_dir()
    base = Path(base)
    base.mkdir(parents=True, exist_ok=True)
    d = base / f"{what}-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}-{time.monotonic_ns() % 1000000}"
    d.mkdir(parents=False, exist_ok=False)
    d.chmod(0o700)
    return d


NEED_FREE = 4 * 1024 * 1024                   # the image copy + M-Flasher's own backup (1 MiB each) + its output


def _free_enough(d: Path) -> bool:
    try:
        return shutil.disk_usage(d).free >= NEED_FREE
    except OSError:
        return False


def double_read(job: Job, purpose: str) -> Saved:
    """two `backup` runs, compared byte for byte and each against the chip's CRC-32 -> one image kept in the
    device's Flash folder with the app's description"""
    from .. import flashdump
    from ..flash import mboot
    try:
        job.folder.mkdir(parents=True, exist_ok=True)
        staging = _staging(job, "read")
    except OSError as e:
        raise Refused(_("The backup folder cannot be created: {error}").format(
            error=getattr(e, "strerror", None) or e), "folder") from e
    try:
        a, info = _one_read(job, staging, 1)
        b, _info2 = _one_read(job, staging, 2)
        if a != b:
            diff = next(i for i in range(len(a)) if a[i] != b[i])
            job.ev("compare", f"the two reads differ (first at 0x{diff:05X})")
            raise Refused(_("The two reads of the flash differ (first at 0x{at:05X}). The USB connection is "
                            "unreliable: try another cable or port, without a hub. Nothing was kept, nothing was "
                            "written.").format(at=diff), "read-mismatch")
        job.ev("compare", "both reads identical")
        prof = job.profile
        device = prof.name if prof is not None else "FM-1"
        fw = {"app": _firmware_text(prof), "m_flasher_guess": info.firmware, "bootloader": info.bootloader}
        cand = job.store.select("m-flasher")[0] if job.store is not None else None
        tool = {"name": "M-Flasher", "version": cand.version if cand else "", "source": cand.source if cand else "",
                "binary_sha256": cand.sha256 if cand else ""}
        try:
            path = flashdump.write_backup(
                job.folder, mboot.backup_name(device), a, device=device,
                read_through=f"M-Flasher {tool['version']} (USB download mode), read twice and compared",
                jedec=info.flash_id, unit_id=info.unit_id, chip_key=info.chip_key, capacity=FLASH_SIZE,
                profile=getattr(prof, "id", "") if prof is not None else "", firmware=fw, tool=tool,
                double_read=True, note=purpose, during_open_write=_open_record_for(job, info))
            flashdump.read_side(path)
        except (OSError, flashdump.FlashDumpError) as e:
            raise Refused(_("The flash backup could not be saved: {error}. Nothing was written.").format(
                error=getattr(e, "strerror", None) or e), "save") from e
        sha = hashlib.sha256(a).hexdigest()
        job.ev("saved", f"{path.name}: sha256 {sha}, boot area sha256 {hashlib.sha256(a[:BOOT_AREA]).hexdigest()}")
        _note_boot_area(job, a, info, "backup")
        job.steps.append(("double-read", "ok"))
        return Saved(path, sha, a)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _note_boot_area(job: Job, image: bytes, info: mp.Info, how: str):
    """what a whole read says about the unit's boot area goes into its profile (only the profile identify() named)"""
    from . import bootarea
    prof = job.profile
    if prof is None or job.info is None:
        return
    kind = bootarea.classify(image[:BOOT_AREA], info.bootloader)
    if bootarea.note(prof, kind, how, bootloader=info.bootloader if kind == bootarea.MBOOT else "",
                     sha256=hashlib.sha256(image[:BOOT_AREA]).hexdigest()):
        job.ev("profile", f"boot area: {kind}")
        if job.profiles is not None:
            try:
                job.profiles.save()
            except OSError as e:
                log.warning("profile not saved: %s", e)


def _firmware_text(prof) -> str:
    if prof is None or not getattr(prof, "firmware", ""):
        return ""
    try:
        from ..ui.device import last_firmware_text
        return last_firmware_text(prof)
    except Exception:  # noqa: BLE001 – only a label
        return " ".join(x for x in (prof.firmware, getattr(prof, "firmware_version", "")) if x)


def backup_before_boot_write(job: Job):
    """the helper every write calls first (restore here; m-bootloader, firmware and SLOOP 3 later): a fresh
    whole-flash backup of the connected unit, read twice -> the image's Path, or a Refusal (then nothing may be
    written). Needs the unit in download mode and identified in this job (`job.device`, `job.info`)."""
    if not job.device or job.info is None:
        return Refusal(_("The FM-1 was not identified in download mode first."), "not-identified")
    if not job.info.unit_id:
        return Refusal(_("M-Flasher reports no unit ID for this FM-1, so the backup could not be tied to this unit. "
                         "Nothing was written."), "no-unit-id")
    try:
        saved = double_read(job, "automatic backup before a write")
    except Refused as e:
        return Refusal(e.reason, e.kind)
    job.pre = saved                                       # (kept for the comparison after the write)
    return saved.path


# ------------------------------------------------------------------------------------------------ the jobs

class _Lock:
    def __init__(self, job: Job, what: str):
        self.job, self.what = job, what

    def __enter__(self):
        from ..flash import journal as fj
        from ..flash import robust
        j = self.job
        j.journal = j.journal or fj.Journal()
        try:
            j.journal.acquire()
        except fj.Busy as e:
            raise Refused(_("M-Flasher waits: {reason}").format(reason=e), "busy") from e
        j.held = _Held(j.journal)
        if j.store is None:
            from .store import Store
            j.store = Store()
        if j.log_dir is None:
            j.log_dir = fj.root() / "logs"
        j.flight = robust.FlightLog(j.log_dir)
        j.flight.prefix = self.what
        return j

    def __exit__(self, et, ev, tb):
        j = self.job
        outcome = "ok" if et is None else (getattr(ev, "kind", "") or (et.__name__ if et else "error"))
        if et is not None:
            j.ev("refused" if isinstance(ev, Refused) and not ev.written else "error",
                 str(getattr(ev, "reason", ev)))
        try:
            j.log_path = j.flight.finish(outcome, header=[f"job: {self.what}",
                                                         f"device: {getattr(j.profile, 'name', '') or '?'}"])
        except Exception:  # noqa: BLE001 – the log never hides the result
            j.log_path = None
        if not (j.held is not None and j.held.handover):
            j.journal.release()
        return False


class _back_on_refusal:
    """a refusal after the app had sent the unit into download mode (nothing written): start it again"""

    def __init__(self, job: Job):
        self.job = job

    def __enter__(self):
        return self

    def __exit__(self, et, ev, tb):
        if isinstance(ev, Refused) and not ev.written and self.job.sent_by_app and self.job.device:
            self.job.ev("step", "refused after the app's own download-mode: restarting the unit")
            restart(self.job)
        return False


@dataclass
class BackupResult:
    path: Path
    sha256: str
    info: mp.Info
    profile: object
    restarted: bool
    log_path: Path | None = None


def backup(job: Job, restart_after: bool | None = None) -> BackupResult:
    """"Back up the whole flash": download mode (soft key or guide), identify, double read, keep one image; then
    the unit is restarted when the app itself had sent it into download mode (or `restart_after`)."""
    job.purpose = job.purpose or "backup"
    with _Lock(job, "mflasher-backup"), _back_on_refusal(job):
        ensure_download_mode(job)
        info = identify(job)
        if not info.unit_id:
            job.tell(_("M-Flasher reports no unit ID for this FM-1: the backup is kept, but the app cannot write it "
                       "back (it could not prove which unit it belongs to)."))
        saved = double_read(job, "backup")
        again = job.sent_by_app if restart_after is None else restart_after
        restarted = restart(job) if again else False
    return BackupResult(saved.path, saved.sha256, info, job.profile, restarted, job.log_path)


@dataclass
class RestorePlan:
    """what the danger dialog shows before anything is written"""
    image: Path
    side: dict
    unit_name: str
    unit_id: str
    flash_id: int
    revision: str
    image_date: str
    image_firmware: str
    runs: list[tuple[int, int]]
    sectors: int
    boot_area: bool                    # the boot area 0x00000-0x03FFF differs and is written
    backup: Path                       # the automatic backup just made
    factory_blank: bool = False        # the unit's factory sector is blank (all FF): the image's is written there


@dataclass
class RestoreResult:
    written: bool
    verified: bool
    restarted: bool
    backup: Path | None
    mflasher_backup: Path | None = None
    sectors: int = 0
    log_path: Path | None = None
    note: str = ""


def diff_runs(a: bytes, b: bytes) -> list[tuple[int, int]]:
    """the 4 KiB sectors where two images differ, merged into runs [start, end)"""
    out: list[list[int]] = []
    for s in range(0, min(len(a), len(b)), SECTOR):
        if a[s:s + SECTOR] != b[s:s + SECTOR]:
            if out and out[-1][1] == s:
                out[-1][1] = s + SECTOR
            else:
                out.append([s, s + SECTOR])
    return [(x, y) for x, y in out]


def restore_gate(expect: dict):
    """the check right before "yes": fn(M-Flasher's output so far) -> "" (answer yes) or the reason (English)"""
    def gate(out: str) -> str:
        try:
            info = mp.parse_info(out)
        except mp.ParseError as e:
            return f"its info block is not readable ({e})"
        if not info.unit_id or info.unit_id != expect["unit_id"]:
            return f"unit id {mask(info.unit_id)} is not the planned {mask(expect['unit_id'])}"
        if info.flash_id != expect["flash_id"]:
            return f"flash 0x{info.flash_id or 0:06X} is not the planned 0x{expect['flash_id']:06X}"
        if info.chip_key != expect["chip_key"]:
            return "the chip key differs from the one read before"
        try:
            plan = mp.parse_plan(out)
        except mp.ParseError as e:
            return f"no plan readable ({e})"
        if plan.title != mp.RESTORE_TITLE:
            return f"unexpected plan {plan.title[:40]!r}"
        if plan.plan_lines != 1:
            return "more than one plan line"
        if plan.stray:
            return f"an unknown line before the plan: {plan.stray[0][:60]!r}"
        runs = expect["runs"]
        n = sum((b - a) // SECTOR for a, b in runs)
        if plan.nothing or plan.runs != runs or plan.sectors != n or plan.kib != 4 * n:
            return "the planned sector ranges are not the ones the app computed"
        if any(a < 0 or b > FLASH_SIZE or a % SECTOR or b % SECTOR for a, b in plan.runs):
            return "a planned range lies outside the flash"
        if plan.notes != [mp.WHOLE_FLASH_NOTE]:
            return ("M-Flasher does not treat the image as this unit's backup" if mp.WHOLE_FLASH_NOTE not in plan.notes
                    else "an unknown note in the plan: " + next(n for n in plan.notes if n != mp.WHOLE_FLASH_NOTE)[:80])
        allowed = [mp.FACTORY_WARNING] if expect.get("factory_blank") else []
        if plan.warnings != allowed:
            return "M-Flasher warns: " + (plan.warnings[0][:120] if plan.warnings else "(the expected warning is missing)")
        return ""
    return gate


def check_image(path: Path) -> dict:
    """the image and its description, before anything is connected -> the description; Refused otherwise"""
    from .. import flashdump
    try:
        side = flashdump.read_any_side(path)        # (the app's description or M-Flasher's own .json)
    except flashdump.FlashDumpError as e:
        raise Refused(str(e) + " " + _("Nothing was written."), "image-damaged") from e
    if side.get("size") != FLASH_SIZE:
        raise Refused(_("This file is not a copy of the whole flash (1 MiB). Nothing was written."), "image-size")
    if not flashdump.side_unit_id(side):
        raise Refused(_("This flash backup does not say which unit it comes from (no unit ID in its description), "
                        "so the app does not write it back. Nothing was written."), "no-unit-id")
    if not flashdump.side_flash_id(side):
        raise Refused(_("This flash backup does not say which flash chip it was read from, so the app does not write "
                        "it back. Nothing was written."), "no-flash-id")
    return side


def _known_other_unit(job: Job, side: dict):
    """the active profile already knows its unit id, and the image names another: refused at once – the FM-1 is not
    even sent into download mode. (The check against M-Flasher's own reading in the session stays as it is.)"""
    from .. import flashdump
    from ..devices import profile_unit_ids, unit_id_forms
    prof = job.profile
    known = {f for f in profile_unit_ids(prof) if len(f) == 16} if prof is not None else set()
    img = flashdump.side_unit_id(side)
    if known and img and not unit_id_forms(img) & known:
        job.ev("refused", f"the image's unit id {mask(img)} is not the active profile's")
        raise Refused(_("This flash backup comes from another FM-1 (unit ID {img}) than “{name}” (unit ID {uid}). "
                        "A flash image holds its unit's boot area and factory data, so the app writes it only onto "
                        "the unit it came from. Nothing was done – the FM-1 was not even switched to download "
                        "mode.").format(img=mask(img), name=prof.name, uid=mask(sorted(known)[0])), "other-unit")


def restore(job: Job, image: Path, confirm) -> RestoreResult:
    """"Write flash back": see the module text. confirm(RestorePlan) -> bool is asked after the automatic backup
    and before M-Flasher is started for the write."""
    image = Path(image)
    store = job.store
    if store is None:
        from .store import Store
        store = job.store = Store()
    cand, _problems = store.select("m-flasher", write=True)
    if cand is None:
        raise Refused(_("No M-Flasher on this computer is cleared for writing by this app version (only the exact "
                        "build this app was reviewed with is). Reinstall the app. Nothing was written."),
                      "not-write-verified")
    side = check_image(image)
    _known_other_unit(job, side)                    # (before the unit is sent anywhere)
    data = image.read_bytes()
    if hashlib.sha256(data).hexdigest() != side.get("sha256"):     # (changed since the check a moment ago)
        raise Refused(_("The flash backup does not match its description (changed or damaged).") + " " +
                      _("Nothing was written."), "image-damaged")
    from . import bootarea
    if side.get("taken_during_open_write"):
        raise Refused(_("This flash backup was saved while a write onto the FM-1 had not ended verified, so it may "
                        "hold a half-written state. The app does not write it back. Use the backup made before that "
                        "write. Nothing was written."), "image-during-write")
    if not bootarea.intact(data[:BOOT_AREA]):
        raise Refused(_("The boot area in this flash backup is not intact (its file table, its bootloader's checksum "
                        "or its chip-key record is damaged or erased – as after an interrupted write). Written back, "
                        "the FM-1 could not start, so the app does not write it back. Nothing was written."),
                      "image-boot-broken")
    job.purpose = "restore"
    with _Lock(job, "mflasher-restore"), _back_on_refusal(job):
        res = _restore_locked(job, image, side, data, confirm)
    res.log_path = job.log_path
    return res


def _restore_locked(job: Job, image: Path, side: dict, data: bytes, confirm) -> RestoreResult:
    from .. import flashdump, hardware
    img_uid, img_flash = flashdump.side_unit_id(side), flashdump.side_flash_id(side)
    job.ev("step", f"image {image.name}: unit id {mask(img_uid)}, flash 0x{img_flash:06X}, "
                   f"sha256 {side.get('sha256')}")
    ensure_download_mode(job)
    info = identify(job, need_profile=True)
    if not info.unit_id:
        raise Refused(_("M-Flasher reports no unit ID for the connected FM-1, so the app cannot check that the "
                        "image is this unit's. Nothing was written."), "no-unit-id")
    if img_uid != info.unit_id:
        raise Refused(_("This flash backup comes from another FM-1 (unit ID {img}); the connected one is {uid}. "
                        "A flash image holds its unit's boot area and factory data (calibration, Bluetooth "
                        "address), so the app writes it only onto the unit it came from. Nothing was "
                        "written.").format(img=mask(img_uid), uid=mask(info.unit_id)), "other-unit")
    if img_flash != info.flash_id:
        raise Refused(_("This flash backup was read from another flash chip ({img}); the connected FM-1 has "
                        "{chip}. Nothing was written.").format(img=chip_name(img_flash),
                                                                chip=chip_name(info.flash_id)), "other-chip")
    pre = backup_before_boot_write(job)
    if isinstance(pre, Refusal):
        raise Refused(pre.reason, pre.kind or "no-backup")
    saved = job.pre
    cur_f, img_f = saved.image[FACTORY[0]:FACTORY[1]], data[FACTORY[0]:FACTORY[1]]
    factory_blank = all(b == 0xFF for b in cur_f)
    if img_f != cur_f and not factory_blank:
        # the unit's factory data (chip key record, calibration, Bluetooth address) is never written over: not with
        # another unit's (an edited description), and not with this unit's own older copy either – the app cannot
        # tell those apart, and repairing factory data is not its job. No override.
        partial = all(c == i or c == 0xFF for c, i in zip(cur_f, img_f))
        job.ev("refused", "the image's factory sector 0xFF000-0xFFFFF differs from the unit's"
                          + (" (the unit's looks partly erased)" if partial else ""))
        if partial:
            raise Refused(_("The FM-1's factory data (0xFF000–0xFFFFF: calibration, Bluetooth address) looks partly "
                            "erased – as after an interrupted write – and the rest equals this backup. The app does "
                            "not repair factory data. Keep the FM-1 as it is (it may still start), keep this backup "
                            "and the one made just now, and ask for help (the app's issue tracker or M-Flasher's "
                            "author) before anything else is written. Nothing was written."), "factory-partial")
        raise Refused(_("The FM-1's factory data (0xFF000–0xFFFFF: calibration, Bluetooth address) differs from the "
                        "one in this backup, although both name the same unit ID. Either the backup's description "
                        "was changed (another unit's copy), or the unit's factory data has changed since (an "
                        "interrupted write, a damaged flash). The app never writes over factory data. Keep both "
                        "copies and ask for help (the app's issue tracker or M-Flasher's author) before anything "
                        "else is written. Nothing was written."), "factory")
    runs = diff_runs(data, saved.image)
    if not runs:
        job.tell(_("The FM-1's flash already holds exactly this image: nothing to write."))
        job.ev("plan", "nothing to write: identical")
        _close_record_if(job, hashlib.sha256(data).hexdigest())
        again = restart(job) if job.sent_by_app else False
        return RestoreResult(False, True, again, pre, log_path=None, note="identical")
    plan = RestorePlan(image=image, side=side, unit_name=job.profile.name, unit_id=info.unit_id,
                       flash_id=info.flash_id, revision=hardware.revision_of_id(info.flash_id),
                       image_date=str(side.get("created") or ""),
                       image_firmware=str((side.get("firmware") or {}).get("app") or "")
                       if isinstance(side.get("firmware"), dict) else "",
                       runs=runs, sectors=sum((b - a) // SECTOR for a, b in runs),
                       boot_area=runs[0][0] < BOOT_AREA, backup=pre, factory_blank=factory_blank and img_f != cur_f)
    job.ev("plan", f"{plan.sectors} sectors differ: " + ", ".join(f"0x{a:05X}-0x{b - 1:05X}" for a, b in runs))
    if job.cancelled():
        raise Refused(_("Cancelled. Nothing was written to the FM-1."), "cancelled")
    if not confirm(plan):
        job.ev("confirm", "declined")
        raise Refused(_("Not written back. The FM-1 is unchanged; the backup made just now is kept."),
                      "declined")
    job.ev("confirm", "confirmed by the user")
    planned = _inhibit(job, "planned", os.getpid())  # (active before "yes" – released after the verify read)
    try:
        return _write(job, image, side, data, info, saved, pre, plan, runs)
    finally:
        if planned is not None:
            try:
                planned.release()
            except Exception:  # noqa: BLE001
                pass


def _inhibit(job: Job, why: str, pid: int):
    if not job.inhibit or not pid:
        return None
    try:
        h = job.inhibit(why, pid)
        job.ev("inhibit", f"shutdown/suspend held off while {why} (pid {pid})")
        return h
    except Exception:  # noqa: BLE001 – never in the way of the job
        return None


def _work_folder(job: Job) -> Path:
    """a fresh private staging folder for one write, with room for M-Flasher's files -> Path; Refused otherwise"""
    try:
        staging = _staging(job, "write")
    except OSError as e:
        raise Refused(_("The app's private working folder cannot be created: {error}. Nothing was written.").format(
            error=getattr(e, "strerror", None) or e), "folder") from e
    if not _free_enough(staging):
        shutil.rmtree(staging, ignore_errors=True)
        raise Refused(_("Too little free space for M-Flasher's working files ({need} MiB are needed in {where}). Free "
                        "some space and try again. Nothing was written.").format(need=NEED_FREE // (1024 * 1024),
                                                                                 where=staging.parent), "space")
    return staging


class _Write:
    """one gated M-Flasher write (restore, bootloader): the record on disk before "yes", the inhibitor bound to
    M-Flasher, WRITING set from "yes" until it ended, no cancel and no kill after "yes"; afterwards its "Done" line
    is required and its own automatic backup is compared with the one the app made just before"""

    def __init__(self, job: Job, staging: Path, record: dict, saved: Saved, pre: Path):
        self.job, self.staging, self.saved, self.pre = job, staging, saved, pre
        uid = job.info.unit_id if job.info is not None else ""
        self.record = {"state": "writing", "started": time.time(), "pid": 0, "profile": getattr(job.profile, "id", ""),
                       "unit_sha256": hashlib.sha256(uid.encode()).hexdigest() if uid else "",
                       "device": job.profile.name, "pre_backup": str(pre), "pre_sha256": saved.sha256,
                       "staging": str(staging), **record}
        self.rec_path = record_file(job.journal.dir if job.journal is not None else None)
        self.auto: Path | None = None

    def run(self, args: list[str], base_gate, *, nothing_ok: bool = False):
        """-> M-Flasher's RunResult once "yes" was sent and it ended with "Done … verified"; None when its plan was
        "Nothing to write" (only with nothing_ok); Refused / WriteFailed otherwise"""
        job, record, staging = self.job, self.record, self.staging

        def gate(out: str) -> str:
            why = base_gate(out)
            if why:
                return why
            try:                                     # the record is on disk BEFORE "yes" (fsync'd)
                from ..flash import journal as fj
                pid = job.held.pid if job.held else 0
                record.update(pid=pid, pid_start=fj._started(pid) if pid else "",
                              out=str(staging / "m-flasher.out"), err=str(staging / "m-flasher.err"))
                _save_record(self.rec_path, record)
                _save_flight(job, staging / "app-log-so-far.txt")
            except OSError as e:
                return f"the journal record could not be written ({e.strerror or e})"
            _inhibit(job, "writing", record["pid"])     # (bound to M-Flasher itself: holds even if the app ends)
            WRITING.set()
            job.tell(_("M-Flasher is writing now. This cannot be cancelled – do not unplug the FM-1 or switch it "
                       "off."))
            if job.on_writing:
                try:
                    job.on_writing(record)
                except Exception:  # noqa: BLE001 – the UI never stops the write
                    pass
            return ""
        try:
            r = _mf(job, args, gate=gate, out_dir=staging)
        except Refused:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        finally:
            WRITING.clear()
            OVERDUE.clear()
        if not r.answered_yes:
            shutil.rmtree(staging, ignore_errors=True)
            if self.rec_path.exists() and _load_record(self.rec_path).get("staging") == str(staging):
                self.rec_path.unlink(missing_ok=True)   # (written before an answer that then failed: nothing written)
            if nothing_ok and r.ok and not r.prompted:
                try:
                    if mp.parse_plan(r.out).nothing:
                        return None
                except mp.ParseError:
                    pass
            if r.prompted:
                raise Refused(_("M-Flasher's plan did not match what the app checked ({why}), so the app did not "
                                "answer “yes”: nothing was written. The backup made just now is kept.").format(
                                    why=r.gate_reason), "gate")
            raise Refused(_("M-Flasher did not get to writing: {reason} Nothing was written.").format(
                reason=_why(r)), "mflasher")
        job.steps.append((args[0], "yes sent"))
        job.cancel = None                          # (from here on the job runs to its end: verify, restart)
        for sp in mp.saved_paths(r.out):
            q = Path(sp)
            if q.is_file() and q.parent.resolve() == staging.resolve():
                same = hashlib.sha256(q.read_bytes()).hexdigest() == self.saved.sha256
                job.ev("auto-backup", f"M-Flasher's own backup {'equals' if same else 'DIFFERS from'} the one made "
                                      f"before")
                if not same:                          # (the flash changed in between – should never happen): kept
                    try:
                        self.auto = job.folder / f"m-flasher-auto-{q.name}"
                        shutil.move(str(q), str(self.auto))
                        if q.with_suffix(".json").exists():
                            shutil.move(str(q.with_suffix(".json")), str(self.auto.with_suffix(".json")))
                    except OSError:
                        self.auto = q
        if r.rc != 0 or not mp.done_verified(r.out):
            why = mp.refusal(r.err) if r.rc not in (0, None) else ""
            if not mp.written_runs(r.out):
                # (M-Flasher logs "Writing …" before each erase: without one nothing was erased – its own backup failed)
                self.rec_path.unlink(missing_ok=True)
                shutil.rmtree(staging, ignore_errors=True)
                raise Refused(_("M-Flasher stopped before it erased anything ({reason}). Nothing was written; the FM-1 "
                                "is unchanged.").format(reason=why or _("M-Flasher was interrupted")), "not-started")
            why = why or (_("M-Flasher was interrupted") if r.rc is None or r.rc < 0 else
                          _("M-Flasher did not confirm the write"))
            raise self.failed(why, "write-failed")
        return r

    def failed(self, reason: str, kind: str) -> WriteFailed:
        self.record.update(state="failed", ended=time.time(), reason=reason)
        try:
            _save_record(self.rec_path, self.record)
        except OSError:
            pass
        return WriteFailed(_("The write did not end verified ({reason}). Do not switch the FM-1 off: it stays in "
                             "download mode, and writing the flash back again works from there. The backup made "
                             "just before is {name} – it is this unit's state before the write.").format(
                                 reason=reason, name=self.pre.name), kind, self.pre)

    def fresh_read(self) -> tuple[bytes, mp.Info]:
        """one more read after the write (its own staging folder) -> (image, info); WriteFailed when it fails"""
        self.job.tell(_("Written; reading the flash once more to compare …"))
        self.job.ev("verify", "M-Flasher: everything written is verified")
        vs = _staging(self.job, "verify")
        try:
            return _one_read(self.job, vs, 1)
        except Refused as e:
            raise self.failed(e.reason, "verify-read") from e
        finally:
            shutil.rmtree(vs, ignore_errors=True)

    def verified(self):
        self.job.steps.append(("verify", "ok"))
        self.record.update(state="verified", ended=time.time())
        try:
            _save_record(self.rec_path, self.record)
        except OSError:
            pass
        if self.auto is None or self.auto.parent != self.staging:
            shutil.rmtree(self.staging, ignore_errors=True)


def _write(job: Job, image: Path, side: dict, data: bytes, info, saved, pre, plan, runs) -> RestoreResult:
    expect = {"unit_id": info.unit_id, "flash_id": info.flash_id, "chip_key": info.chip_key, "runs": runs,
              "factory_blank": plan.factory_blank}
    staging = _work_folder(job)
    try:
        copy = _private_copy(staging, data, info.unit_id)
    except OSError as e:
        shutil.rmtree(staging, ignore_errors=True)
        raise Refused(_("The app's private working folder cannot be created: {error}. Nothing was written.").format(
            error=getattr(e, "strerror", None) or e), "folder") from e
    sha = hashlib.sha256(data).hexdigest()
    w = _Write(job, staging, {"op": "restore", "unit": mask(info.unit_id), "flash": f"0x{info.flash_id:06X}",
                              "image": str(image), "image_sha256": sha, "runs": [[a, b] for a, b in runs]},
               saved, pre)
    job.tell(_("Writing the flash back (do not unplug the FM-1 or switch it off) …"))
    w.run(["restore", str(copy), "--device", job.device, "--backup-dir", str(staging)], restore_gate(expect))
    got, _vi = w.fresh_read()
    if hashlib.sha256(got).hexdigest() != sha:
        raise w.failed(_("the fresh read differs from the image"), "verify-mismatch")
    job.ev("verify", "fresh read equals the image")
    w.verified()
    _note_boot_area(job, got, _vi, "installed")
    again = restart(job)
    return RestoreResult(True, True, again, pre, w.auto, plan.sectors, None)


# ------------------------------------------------------------------------------------------------ download mode as a button

@dataclass
class ModeResult:
    device: str
    sent: bool                         # the app sent it there (soft key); False: it was in download mode already
    log_path: Path | None = None


def download_mode(job: Job) -> ModeResult:
    """"Into download mode": ensure_download_mode as its own action (the soft key only to the FM-1 the app recognised
    itself, otherwise the guide – Refused kind "guide"). The unit stays in download mode, marked as there on purpose."""
    job.purpose = "button"
    with _Lock(job, "mflasher-download"):
        dev = ensure_download_mode(job)
    return ModeResult(dev, job.sent_by_app, job.log_path)


def leave_download_mode(job: Job, confirm=None) -> str:
    """"Restart the FM-1": exactly one unit in download mode and none running beside it -> M-Flasher `restart`
    (writes nothing) -> its /dev/sgN; Refused otherwise.
    * Never while a write record is open or failed: such a unit must stay in download mode, where the way back (the
      backup made before the write) still works – the app says so instead.
    * Which unit: M-Flasher's `info` reads its unit id. It must be the job's profile's unit (when that profile knows
      its id) – another one is refused; when the id names no profile or cannot be read, confirm(text) -> bool asks."""
    from ..devices import profile_unit_ids, unit_id_forms
    with _Lock(job, "mflasher-download"):
        rec = unfinished_write(job.journal.dir if job.journal is not None else None)
        if rec is not None:
            job.ev("refused", f"restart: a write record is open ({rec.get('state')}, {rec.get('op', 'restore')})")
            raise Refused(_("Not restarted: a write onto “{device}” did not end verified (or the app has not checked "
                            "it yet). Keep the FM-1 in download mode – from there the backup made before the write "
                            "can be written back. Open the question about it (it comes at the next start of the app) "
                            "or write the backup back on Recovery › Tools; once it is settled, restart the FM-1.").format(
                                device=str(rec.get("device") or "?")), "open-record")
        r = _mf(job, ["find"])
        found = mp.parse_find(r.out, r.err) if r.ok else None
        if found is None:
            raise Refused(_("M-Flasher could not look for the FM-1: {reason}").format(reason=_why(r)), "find")
        if found.sg_missing:
            raise Refused(_("An FM-1 is in download mode, but the Linux driver “sg” is not loaded. The app's "
                            "installer sets it up; by hand: sudo modprobe sg – then try again. Nothing was written."),
                          "sg")
        if not found.download:
            raise Refused(_("No FM-1 is in download mode."), "none")
        if len(found.download) > 1 or found.midi:
            raise Refused(_("More than one FM-1 is connected. So that the right one restarts, leave only that one "
                            "connected – or switch it off and on."), "several")
        job.device = found.download[0].device
        _check_restart_target(job, confirm, profile_unit_ids, unit_id_forms)
        if not restart(job):
            raise Refused(_("The FM-1 did not restart: switch it off and on."), "restart")
    return job.device


def _check_restart_target(job: Job, confirm, profile_unit_ids, unit_id_forms):
    """the unit in download mode is the one meant (its unit id against the job's profile), else ask or refuse"""
    r = _mf(job, ["info", "--device", job.device])
    uid = ""
    if r.ok:
        try:
            uid = mp.parse_info(r.out).unit_id
        except mp.ParseError:
            uid = ""
    uid = uid if unit_id_forms(uid) else ""
    prof = job.profile
    known = {f for f in profile_unit_ids(prof) if len(f) == 16} if prof is not None else set()
    hits = job.profiles.by_unit_id(uid) if (job.profiles is not None and uid) else []
    if uid and known:
        if unit_id_forms(uid) & known:
            return
        raise Refused(_("The FM-1 in download mode (unit ID {uid}) is not “{name}”. Nothing was done – choose "
                        "that device at the top, or switch the FM-1 off and on.").format(uid=mask(uid),
                                                                                      name=prof.name), "other-unit")
    if uid and len(hits) == 1 and prof is None:
        return                                      # (named by its own unit id)
    who = hits[0].name if len(hits) == 1 else _("an FM-1 the app does not know")
    text = _("Restart the FM-1 in download mode ({device}, unit ID {uid}: {who})? Restarting writes nothing.").format(
        device=job.device, uid=mask(uid), who=who)
    if confirm is None or not confirm(text):
        raise Refused(_("Not restarted: the app could not tell which FM-1 this is, and nobody confirmed it. Nothing "
                        "was done."), "not-confirmed")


# ------------------------------------------------------------------------------------------------ m-bootloader

@dataclass
class BootPlan:
    """what the danger dialog of "Install m-bootloader" / "Remove m-bootloader" shows before anything is written"""
    op: str                            # "install" | "remove"
    unit_name: str
    unit_id: str
    flash_id: int
    revision: str                      # hardware revision as this session read it ("new" / "old")
    version: str                       # the m-bootloader that is installed ("" for remove)
    current: str                       # M-Flasher's "bootloader" line for the unit now
    firmware: str                      # the firmware the app knows on the unit ("" not known)
    backup: Path                       # the automatic backup just made
    source: str = ""                   # where the new boot area comes from (file name, package identity)
    notes: list[str] = field(default_factory=list)


@dataclass
class BootResult:
    written: bool
    verified: bool
    restarted: bool
    backup: Path | None
    version: str = ""
    mflasher_backup: Path | None = None
    sectors: int = 0
    log_path: Path | None = None
    note: str = ""


def bundled_bootloader(store) -> tuple[bytes, str]:
    """the pinned m-bootloader this app release bundles (fm1-uboot.boot, manifest SHA-256 checked on the bytes read)
    -> (bytes, version "2.4-395e6cc"); Refused otherwise. Never an updated copy: writing uses the reviewed one."""
    from . import bootarea
    c = store.bundled("m-bootloader") if store is not None else None
    if c is None:
        raise Refused(_("The bundled m-bootloader is missing. Reinstall the app. Nothing was written."), "no-boot")
    try:
        data = c.path.read_bytes() if c.path is not None else b""
    except OSError:
        data = b""
    if not data or hashlib.sha256(data).hexdigest() != c.sha256.lower():
        raise Refused(_("The bundled m-bootloader did not pass its checksum test, so the app does not write it. "
                        "Reinstall the app. Nothing was written."), "boot-checksum")
    ident = bootarea.boot_identity(data)
    if ident is None or ident[0] != "fm1":
        raise Refused(_("The bundled m-bootloader is not a build for the FM-1. Nothing was written."), "boot-board")
    return data, ident[1]


def _write_verified(store) -> None:
    cand, _problems = store.select("m-flasher", write=True)
    if cand is None:
        raise Refused(_("No M-Flasher on this computer is cleared for writing by this app version (only the exact "
                        "build this app was reviewed with is). Reinstall the app. Nothing was written."),
                      "not-write-verified")


def _store_of(job: Job):
    if job.store is None:
        from .store import Store
        job.store = Store()
    return job.store


def boot_gate(expect: dict):
    """the check right before "yes" for `bootloader --file` / `--stock`: fn(M-Flasher's output so far) -> "" (yes)
    or the reason (English). Unit id, flash ID and chip key as read before; the expected title and the one note;
    exactly one plan line; every announced range inside the boot area 0x00000-0x03FFF and nothing else (with
    expect["runs"]: exactly those runs); no WARNING."""
    def gate(out: str) -> str:
        try:
            info = mp.parse_info(out)
        except mp.ParseError as e:
            return f"its info block is not readable ({e})"
        if not info.unit_id or info.unit_id != expect["unit_id"]:
            return f"unit id {mask(info.unit_id)} is not the planned {mask(expect['unit_id'])}"
        if info.flash_id != expect["flash_id"]:
            return f"flash 0x{info.flash_id or 0:06X} is not the planned 0x{expect['flash_id']:06X}"
        if info.chip_key != expect["chip_key"]:
            return "the chip key differs from the one read before"
        try:
            plan = mp.parse_plan(out)
        except mp.ParseError as e:
            return f"no plan readable ({e})"
        if plan.title != expect["title"]:
            return f"unexpected plan {plan.title[:60]!r}"
        if plan.plan_lines != 1:
            return "more than one plan line"
        if plan.stray:
            return f"an unknown line before the plan: {plan.stray[0][:60]!r}"
        if plan.nothing or not plan.runs:
            return "the plan writes nothing"
        if any(a < 0 or b > BOOT_AREA or a >= b or a % SECTOR or b % SECTOR for a, b in plan.runs):
            return "a planned range lies outside the boot area 0x00000-0x03FFF"
        n = sum((b - a) // SECTOR for a, b in plan.runs)
        if plan.sectors != n or plan.kib != 4 * n:
            return "the plan's sector count does not match its ranges"
        if expect.get("runs") is not None and plan.runs != expect["runs"]:
            return "the planned sector ranges are not the ones the app computed"
        if plan.notes != [expect["note"]]:
            return "an unexpected note in the plan: " + ((plan.notes or ["(none)"])[0])[:80]
        if plan.warnings:
            return "M-Flasher warns: " + plan.warnings[0][:120]
        return ""
    return gate


def _session_revision(job: Job, info: mp.Info) -> str:
    from .. import hardware
    seen = hardware.revision_of_id(info.flash_id)
    return hardware.NEW if hardware.NEW in (seen, hardware.revision(job.profile)) else seen


def _private_file(staging: Path, name: str, data: bytes) -> Path:
    """a file as M-Flasher gets it: a copy of the bytes the app checked, in the private 0700 staging folder, read
    back and hashed again"""
    p = staging / name
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    if hashlib.sha256(p.read_bytes()).hexdigest() != hashlib.sha256(data).hexdigest():
        raise OSError(0, "the private copy does not read back as written")
    _sync_dir(staging)
    return p


def install_bootloader(job: Job, confirm) -> BootResult:
    """"Install m-bootloader": the pinned bundled fm1-uboot.boot through `m-flasher bootloader --file`, only with a
    write-verified M-Flasher; on a newer unit only an m-bootloader build hardware.json marks "yes"; a whole-flash
    backup first (no skip); the danger dialog (confirm(BootPlan) -> bool); the gate (boot area only); M-Flasher's
    verify, then a fresh read: nothing outside the boot area changed, the boot area holds exactly this bootloader
    and M-Flasher reads its version. The profile records it."""
    from .. import hardware
    store = _store_of(job)
    _write_verified(store)
    boot, version = bundled_bootloader(store)
    sup = hardware.support("m-bootloader", version)
    if hardware.revision(job.profile) == hardware.NEW and sup.status != hardware.YES:
        raise Refused(_newer_unit_refusal(version), "newer-chip")      # (before the unit is sent anywhere)
    job.purpose = "bootloader"
    with _Lock(job, "mflasher-bootloader"), _back_on_refusal(job):
        res = _install_locked(job, boot, version, sup, confirm)
    res.log_path = job.log_path
    return res


def _newer_unit_refusal(version: str) -> str:
    return _("This FM-1 is a newer unit (logo on the front, flash chip UC25HQ80). On such a unit the app installs "
             "only an m-bootloader build that is known to run there (data/hardware.json), and m-bootloader {version} "
             "is not one of them. Nothing was written.").format(version=version)


def _install_locked(job: Job, boot: bytes, version: str, sup, confirm) -> BootResult:
    from .. import hardware
    from . import bootarea
    ensure_download_mode(job)
    info = identify(job, need_profile=True)
    if not info.unit_id:
        raise Refused(_("M-Flasher reports no unit ID for the connected FM-1, so the app cannot tie the write to this "
                        "unit. Nothing was written."), "no-unit-id")
    if info.flash_id not in (hardware.OLD_ID, hardware.NEW_ID):
        raise Refused(_("M-Flasher reads a flash chip the app does not know ({chip}). Nothing was written.").format(
            chip=chip_name(info.flash_id)), "unknown-chip")
    rev = _session_revision(job, info)
    if rev == hardware.NEW and sup.status != hardware.YES:
        raise Refused(_newer_unit_refusal(version), "newer-chip")
    pre = backup_before_boot_write(job)
    if isinstance(pre, Refusal):
        raise Refused(pre.reason, pre.kind or "no-backup")
    saved = job.pre
    expected = bootarea.compose_short(saved.image[:BOOT_AREA], boot) if not mp.reads_config(version) else None
    if expected is None:
        raise Refused(_("The app cannot work out the boot area M-Flasher would write onto this FM-1 (its boot area "
                        "has no intact file table or chip-key record, or the bootloader is older than 1.4). Nothing "
                        "was written; the backup made just now is kept."), "compose")
    runs = diff_runs(expected, saved.image[:BOOT_AREA])
    if not runs:
        job.tell(_("m-bootloader {version} is installed on this FM-1 already: nothing to write.").format(
            version=version))
        job.ev("plan", "nothing to write: this m-bootloader is there already")
        _settle_boot_record(job, "install")
        again = restart(job) if job.sent_by_app else False
        return BootResult(False, True, again, pre, version, note="already")
    plan = BootPlan("install", job.profile.name, info.unit_id, info.flash_id, rev, version, info.bootloader,
                    _firmware_text(job.profile), pre, source="fm1-uboot.boot")
    if rev == hardware.NEW:
        plan.notes.append(_("On a newer unit: m-bootloader {version} ran on one such unit (owner's report, outside "
                            "this app).").format(version=version))
    return _confirm_and_write(job, plan, info, saved, pre, confirm, "fm1-uboot.boot", boot, {
        "flag": "--file", "title": mp.bootloader_title(version),
        "note": mp.BOOT_SHORT_NOTE, "runs": runs, "expected": expected, "version": version})


def remove_bootloader(job: Job, confirm, package: bytes) -> BootResult:
    """"Remove m-bootloader (original boot area)": ONLY `m-flasher bootloader --stock` with the boot area of M-VAVE's
    public V15 package (owner decision) – `package` is that file, checked here by its pinned SHA-256 and by the
    measured SHA-256 of its boot area. The same gates as the install; the gate requires exactly the sectors where
    the unit's boot area differs from V15's; afterwards a fresh read must hold V15's boot area and nothing else
    changed. The profile records the original boot area."""
    from . import bootarea
    store = _store_of(job)
    _write_verified(store)
    try:
        area = bootarea.v15_area(package)
    except bootarea.BootAreaError as e:
        raise Refused(str(e) + " " + _("Nothing was written."), "package") from e
    early = removal_refused_early(job)
    if early is not None:
        raise early                                        # (before the unit is sent anywhere)
    job.purpose = "bootloader"
    with _Lock(job, "mflasher-bootloader"), _back_on_refusal(job):
        res = _remove_locked(job, package, area, confirm)
    res.log_path = job.log_path
    return res


def _remove_locked(job: Job, package: bytes, area: bytes, confirm) -> BootResult:
    from .. import hardware
    from . import bootarea
    ensure_download_mode(job)
    info = identify(job, need_profile=True)
    if not info.unit_id:
        raise Refused(_("M-Flasher reports no unit ID for the connected FM-1, so the app cannot tie the write to this "
                        "unit. Nothing was written."), "no-unit-id")
    if info.flash_id not in (hardware.OLD_ID, hardware.NEW_ID):
        raise Refused(_("M-Flasher reads a flash chip the app does not know ({chip}). Nothing was written.").format(
            chip=chip_name(info.flash_id)), "unknown-chip")
    rev = _session_revision(job, info)
    if rev != hardware.OLD:
        raise _newer_unit_stock_refusal(job, info.unit_id)
    pre = backup_before_boot_write(job)
    if isinstance(pre, Refusal):
        raise Refused(pre.reason, pre.kind or "no-backup")
    saved = job.pre
    runs = diff_runs(area, saved.image[:BOOT_AREA])
    if not runs:
        job.tell(_("This FM-1 has the original boot area already (the one of M-VAVE's V15 package): nothing to "
                   "write."))
        job.ev("plan", "nothing to write: the boot area equals V15's")
        _settle_boot_record(job, "remove")
        again = restart(job) if job.sent_by_app else False
        return BootResult(False, True, again, pre, note="already")
    plan = BootPlan("remove", job.profile.name, info.unit_id, info.flash_id, rev, "", info.bootloader,
                    _firmware_text(job.profile), pre, source="FM-1_015")
    fw = (getattr(job.profile, "firmware", "") or "").lower()
    if fw in ("sloop3", "sloop-3") or bootarea.current(job.profile).get("how") == "sloop3":
        plan.notes.append(_("SLOOP 3 is installed only through m-bootloader. Whether it starts with the original "
                            "bootloader is not known. If the FM-1 does not start afterwards, the way back is the "
                            "backup made just now, written back from USB download mode (Recovery, stage 2)."))
    if bootarea.changer(fw) is bootarea.CHANGERS["fm1-open"]:
        plan.notes.append(_("FM1 Open is the firmware on this FM-1. Its app area expects FM1 Open's own boot area: "
                            "after this, an FM1 Open app sits on V15's boot area, and whether it starts is not "
                            "known. Recommended order: first go back to the original V15 with M-VAVE's FM1 "
                            "Workbench (“Restore original V15”), then put back the original boot area here, then "
                            "install the next firmware."))
    backups = [(p, k) for p, k in unit_backups(job.folder, info.unit_id) if p != pre]
    orig = next((p for p, k in backups if k == bootarea.ORIGINAL), None)
    own = next((p for p, k in backups if k not in (bootarea.MBOOT, bootarea.ORIGINAL)), None)
    if orig is not None:
        plan.notes.append(_("An earlier backup of this unit ({name}) shows exactly V15's boot area: the removal puts "
                            "back this unit's own original boot area.").format(name=orig.name))
    elif own is not None:
        plan.notes.append(_("An earlier backup of this unit ({name}) shows a boot area that differs from V15's: the "
                            "removal writes V15's, not this unit's own. The exact way back to that state is writing "
                            "{name} back (Recovery › Tools › “Write flash back …”).").format(name=own.name))
    else:
        plan.notes.append(_("The app has no earlier backup of this unit that shows its boot area before m-bootloader, "
                            "so it cannot tell whether this FM-1's own original boot area was V15's (units sold with "
                            "V16, for example, may have come with another one)."))
    return _confirm_and_write(job, plan, info, saved, pre, confirm, "FM-1_015.fwsc", package, {
        "flag": "--stock", "title": mp.stock_title("FM-1_015"), "note": mp.BOOT_STOCK_NOTE, "runs": runs,
        "area": area})


def _confirm_and_write(job: Job, plan: BootPlan, info, saved, pre, confirm, name: str, data: bytes,
                       how: dict) -> BootResult:
    if job.cancelled():
        raise Refused(_("Cancelled. Nothing was written to the FM-1."), "cancelled")
    job.ev("plan", f"{plan.op} m-bootloader {plan.version or '(original boot area from ' + plan.source + ')'}; "
                   f"the boot area only")
    if not confirm(plan):
        job.ev("confirm", "declined")
        raise Refused(_("Nothing was written. The FM-1 is unchanged; the backup made just now is kept."), "declined")
    job.ev("confirm", "confirmed by the user")
    planned = _inhibit(job, "planned", os.getpid())  # (active before "yes" – released after the verify read)
    try:
        return _boot_write(job, plan, info, saved, pre, name, data, how)
    finally:
        if planned is not None:
            try:
                planned.release()
            except Exception:  # noqa: BLE001
                pass


def _boot_write(job: Job, plan: BootPlan, info, saved, pre, name: str, data: bytes, how: dict) -> BootResult:
    from . import bootarea
    staging = _work_folder(job)
    try:
        copy = _private_file(staging, name, data)
    except OSError as e:
        shutil.rmtree(staging, ignore_errors=True)
        raise Refused(_("The app's private working folder cannot be created: {error}. Nothing was written.").format(
            error=getattr(e, "strerror", None) or e), "folder") from e
    expect = {"unit_id": info.unit_id, "flash_id": info.flash_id, "chip_key": info.chip_key, "title": how["title"],
              "note": how["note"], "runs": how["runs"]}
    w = _Write(job, staging, {"op": f"bootloader-{plan.op}", "unit": mask(info.unit_id),
                              "flash": f"0x{info.flash_id:06X}", "image": "", "image_sha256": "",
                              "file": name, "file_sha256": hashlib.sha256(data).hexdigest()}, saved, pre)
    job.tell(_("Writing the boot area (do not unplug the FM-1 or switch it off) …"))
    r = w.run(["bootloader", how["flag"], str(copy), "--device", job.device, "--backup-dir", str(staging)],
              boot_gate(expect), nothing_ok=True)
    announced = mp.parse_plan(r.out).runs if r is not None else []
    if r is None:
        job.ev("plan", "M-Flasher: nothing to write")
    got, vi = w.fresh_read() if r is not None else _fresh_read_only(job)
    problem = ""
    if got[BOOT_AREA:] != saved.image[BOOT_AREA:]:
        problem = _("the flash changed outside the boot area")
    elif any(got[a:a + SECTOR] != saved.image[a:a + SECTOR] for a in range(0, BOOT_AREA, SECTOR)
             if not any(s <= a < e for s, e in announced)):
        problem = _("a sector of the boot area changed that M-Flasher did not announce")
    elif plan.op == "install" and not (got[:BOOT_AREA] == how["expected"] and
                                       vi.bootloader.startswith(f"m-bootloader {how['version']}")):
        problem = _("the boot area does not hold this m-bootloader")
    elif plan.op == "remove" and not (got[:BOOT_AREA] == how["area"] and not vi.m_bootloader):
        problem = _("the boot area is not the one of the V15 package")
    if problem:
        if r is None:
            raise Refused(_("M-Flasher said there is nothing to write, but the fresh read does not show the expected "
                            "boot area ({why}). Nothing was written.").format(why=problem), "verify")
        raise w.failed(problem, "verify-mismatch")
    job.ev("verify", "fresh read: boot area as expected, the rest unchanged")
    if r is not None:
        w.verified()
    kind = bootarea.MBOOT if plan.op == "install" else bootarea.ORIGINAL
    if bootarea.note(job.profile, kind, "installed", bootloader=vi.bootloader if kind == bootarea.MBOOT else "",
                     sha256=hashlib.sha256(got[:BOOT_AREA]).hexdigest()) and job.profiles is not None:
        try:
            job.profiles.save()
        except OSError as e:
            log.warning("profile not saved: %s", e)
    again = restart(job)
    return BootResult(r is not None, True, again, pre, plan.version, w.auto,
                      sum((b - a) // SECTOR for a, b in announced), None, "" if r is not None else "nothing")


def _record_is_this_unit(job: Job, rec: dict) -> bool:
    """does an open write record belong to the unit of this job (its profile AND its unit id)? A record without
    the unit's SHA-256 (written by an older version) is never closed by another job – only by its own question"""
    uid = job.info.unit_id if job.info is not None else ""
    want = hashlib.sha256(uid.encode()).hexdigest() if uid else ""
    return bool(want) and job.profile is not None and rec.get("profile") == getattr(job.profile, "id", None) and \
        rec.get("unit_sha256") == want


def _open_record_for(job: Job, info) -> bool:
    rec = unfinished_write(job.journal.dir if job.journal is not None else None)
    if rec is None:
        return False
    if not rec.get("unit_sha256"):
        return True                                # (an older record: it may be this unit's – the safe side)
    uid = info.unit_id if info is not None else ""
    return bool(uid) and rec.get("unit_sha256") == hashlib.sha256(uid.encode()).hexdigest()


def _settle_boot_record(job: Job, op: str):
    """an open record of the same boot write whose result is on the unit now (checked by the fresh backup just made):
    settled – it ends verified"""
    path = record_file(job.journal.dir if job.journal is not None else None)
    rec = _load_record(path)
    if rec.get("state") in ("writing", "failed") and rec.get("op") == f"bootloader-{op}" and _record_is_this_unit(
            job, rec):
        rec.update(state="verified", ended=time.time(), note="checked later: the unit holds the expected boot area")
        try:
            _save_record(path, rec)
            job.ev("record", "the open boot-area record is settled: the unit holds the expected boot area")
        except OSError:
            pass


def unit_backups(folder: Path, unit_id: str) -> list[tuple[Path, str]]:
    """this unit's whole-flash backups in its Flash folder, newest first, each with the kind of its boot area
    (bootarea.classify, plus: a stored m-bootloader container counts as MBOOT)"""
    from .. import flashdump
    from . import bootarea
    from ..devices import unit_id_forms
    out = []
    want = unit_id_forms(unit_id) if unit_id else set()
    try:
        files = sorted(Path(folder).glob("*.bin"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return []
    earliest = None
    for p in files:
        try:
            side = flashdump.read_any_side(p)
            if not want or not (unit_id_forms(flashdump.side_unit_id(side)) & want):
                continue
            if side.get("taken_during_open_write"):
                continue                           # (saved while a write onto it had not ended verified)
            with open(p, "rb") as f:
                area = f.read(BOOT_AREA)
        except (OSError, flashdump.FlashDumpError):
            continue
        if not bootarea.intact(area):
            continue                               # (half-written or erased boot area: never a way back)
        kind = bootarea.classify(area)
        if kind != bootarea.ORIGINAL and bootarea.stored_mbootloader(area):
            kind = bootarea.MBOOT
        out.append((p, kind, hashlib.sha256(area).hexdigest()))
        earliest = out[-1][2]                      # (files are newest first: the last one is the earliest)
    # preferred: V15's boot area, then the unit's own earliest seen one, then the rest (newest first)
    out.sort(key=lambda x: (x[1] != bootarea.ORIGINAL, x[2] != earliest))
    return [(p, k) for p, k, _s in out]


def removal_refused_early(job: Job) -> Refused | None:
    """"Remove m-bootloader" on a unit the profile already knows as newer: refused before anything is asked or sent"""
    from .. import hardware
    return _newer_unit_stock_refusal(job, None) if hardware.revision(job.profile) == hardware.NEW else None


def _newer_unit_stock_refusal(job: Job, unit_id: str | None) -> Refused:
    """V15's boot area has never been written to a newer unit: refused; the way back there is a whole-flash restore
    of a backup made before m-bootloader – named (and offered) when one of this unit exists"""
    from ..devices import profile_unit_ids
    from . import bootarea
    uid = unit_id or ""
    if not uid and job.profile is not None:
        uid = next((f for f in profile_unit_ids(job.profile) if len(f) == 16), "")
    offer = next((p for p, k in unit_backups(job.folder, uid) if k != bootarea.MBOOT), None) if uid else None
    text = _("This FM-1 is a newer unit (logo on the front, flash chip UC25HQ80), or the app cannot rule that out. "
             "M-VAVE's V15 boot area has never been written onto such a unit, so the app does not put it there. The "
             "way back is a whole-flash restore of a backup of this unit made before m-bootloader (Recovery › Tools "
             "› “Write flash back …”).")
    text += " " + (_("This unit has one: {name}.").format(name=offer.name) if offer is not None else
                   _("The app has no such backup of this unit. If M-Flasher itself made one (its .bin with its .json), "
                     "import it first – “Import a flash backup …” on Backups › Flash backups or Recovery › Tools – "
                     "then the app offers it here. Without one there is no way back from the app."))
    e = Refused(text + " " + _("Nothing was written."), "newer-chip-stock")
    e.offer = offer
    return e


def _fresh_read_only(job: Job) -> tuple[bytes, mp.Info]:
    vs = _staging(job, "verify")
    try:
        return _one_read(job, vs, 1)
    finally:
        shutil.rmtree(vs, ignore_errors=True)


# ------------------------------------------------------------------------------------------------ firmware (step 4)
# "Install through download mode": a Felucca-family firmware onto a unit with m-bootloader (also one that runs
# SLOOP 3, which needs m-bootloader) with `m-flasher firmware PACKAGE.fwsc`. M-Flasher 0.12.1 (ops.rs plan_firmware)
# writes only [0x4000, end of the package's flash.bin) – the boot area, and with it m-bootloader, stays as it is, so
# the CHECK HEAD trap (a running Felucca firmware handing on the original V15) does not arise on this route.

DATA_WARNING = "the data-places warning needs its own confirmation"     # (the gate's reason for the second question)


@dataclass
class FirmwarePlan:
    """what the danger dialog of "Install through download mode" shows before anything is written"""
    unit_name: str
    unit_id: str
    flash_id: int
    revision: str                      # hardware revision as this session read it ("new" / "old")
    identity: str                      # the package's identity ("FM-1_727")
    name: str                          # what the user chose ("Skiff 2.7"), "" = the identity
    end: int                           # the end of the package's flash.bin: at most [0x4000, end) is written
    runs: list[tuple[int, int]]
    sectors: int
    current: str                       # M-Flasher's "bootloader" line for the unit now
    firmware: str                      # the firmware the app knows on the unit ("" not known)
    mflasher_firmware: str             # M-Flasher's guess of the installed firmware (its info block)
    backup: Path                       # the automatic backup just made
    notes: list[str] = field(default_factory=list)


@dataclass
class FirmwareResult:
    written: bool
    verified: bool
    restarted: bool
    backup: Path | None
    identity: str = ""
    mflasher_backup: Path | None = None
    sectors: int = 0
    log_path: Path | None = None
    note: str = ""
    data_warning: str = ""             # M-Flasher's data-places warning the user confirmed ("" none)


def firmware_package(raw: bytes) -> tuple[str, bytes]:
    """(identity, flash.bin) of the package M-Flasher is to install – an FM-1 package whose flash image fits the
    flash; Refused otherwise (nothing written)"""
    from . import bootarea
    try:
        ident, flash = bootarea.package_flash(raw)
    except bootarea.BootAreaError as e:
        raise Refused(str(e) + " " + _("Nothing was written."), "package") from e
    model = ident.rsplit("_", 1)[0] if "_" in ident else ident
    if model != "FM-1":
        raise Refused(_("This package is for the {model}, not for the FM-1. Nothing was written.").format(
            model=model or "?"), "package-model")
    if len(flash) > FACTORY[0]:
        # (M-Flasher's plan_firmware writes up to the package's end with no factory warning; the app never lets it
        # reach the factory data at 0xFF000-0xFFFFF – no real FM-1 package does, they end at 0x93000 at most)
        raise Refused(_("The package's flash image reaches into the FM-1's factory data (0xFF000–0xFFFFF: "
                        "calibration, Bluetooth address), which the app never writes. Nothing was written."),
                      "package-size")
    return ident, flash


def firmware_target(pre: bytes, flash: bytes) -> bytes:
    """the flash M-Flasher makes of `pre` with this package (ops.rs plan_firmware): the package's flash image from
    0x4000 to its end, the boot area and everything after the package as they are"""
    return bytes(pre[:BOOT_AREA]) + bytes(flash[BOOT_AREA:]) + bytes(pre[len(flash):])


def firmware_gate(expect: dict):
    """the check right before "yes" for `firmware P.fwsc`: unit id, flash ID and chip key as read before; the title
    "Install firmware <identity>" and its one note "Installs <identity> (0x04000-0x…)."; exactly one plan line; every
    announced range at or after 0x04000 and before the package's end, and exactly the runs the app computed; no
    WARNING – except M-Flasher's data-places warning, which needs the user's own second confirmation: the first time
    it is seen the gate notes it (expect["seen_warning"]) and closes stdin; with expect["accept"] equal to that very
    text it lets it through."""
    def gate(out: str) -> str:
        try:
            info = mp.parse_info(out)
        except mp.ParseError as e:
            return f"its info block is not readable ({e})"
        if not info.unit_id or info.unit_id != expect["unit_id"]:
            return f"unit id {mask(info.unit_id)} is not the planned {mask(expect['unit_id'])}"
        if info.flash_id != expect["flash_id"]:
            return f"flash 0x{info.flash_id or 0:06X} is not the planned 0x{expect['flash_id']:06X}"
        if info.chip_key != expect["chip_key"]:
            return "the chip key differs from the one read before"
        try:
            plan = mp.parse_plan(out)
        except mp.ParseError as e:
            return f"no plan readable ({e})"
        if plan.title != mp.firmware_title(expect["identity"]):
            return f"unexpected plan {plan.title[:60]!r}"
        if plan.plan_lines != 1:
            return "more than one plan line"
        if plan.stray:
            return f"an unknown line before the plan: {plan.stray[0][:60]!r}"
        if plan.nothing or not plan.runs:
            return "the plan writes nothing"
        end = min(expect["end"], FACTORY[0])            # (never the factory data, whatever the package says)
        if any(a < BOOT_AREA or b > end or a >= b or a % SECTOR or b % SECTOR for a, b in plan.runs):
            return f"a planned range lies outside 0x04000-0x{end - 1:05X} (the boot area is never written here)"
        n = sum((b - a) // SECTOR for a, b in plan.runs)
        if plan.sectors != n or plan.kib != 4 * n:
            return "the plan's sector count does not match its ranges"
        if plan.runs != expect["runs"]:
            return "the planned sector ranges are not the ones the app computed"
        if plan.notes != [mp.firmware_note(expect["identity"], expect["end"])]:
            return "an unexpected note in the plan: " + ((plan.notes or ["(none)"])[0])[:80]
        if plan.warnings:
            warn = mp.data_places_warning(info.firmware, expect["identity"])
            if plan.warnings != [warn]:
                return "M-Flasher warns: " + plan.warnings[0][:120]
            if expect.get("accept") != warn:
                expect["seen_warning"] = warn
                return DATA_WARNING
        return ""
    return gate


def _newer_firmware_refusal(name: str) -> str:
    return _("This FM-1 is a newer unit (logo on the front, flash chip UC25HQ80), and {name} is not known to run on "
             "such a unit (data/hardware.json). A firmware that does not know this chip saves nothing there and "
             "cannot be updated any more. Nothing was written.").format(name=name)


def install_firmware(job: Job, package: bytes, confirm, confirm_data, *, name: str = "",
                     new_chip_ok: bool = False, expected_sha256: str = "") -> FirmwareResult:
    """"Install through download mode": `package` (an FM-1 .fwsc the caller checked – catalog, checksum) through
    `m-flasher firmware` onto a unit with m-bootloader, only with a write-verified M-Flasher; on a newer unit only
    when the hardware rule lets the target through (`new_chip_ok`: planner.chip_support says yes); a whole-flash
    backup first (no skip); the danger dialog (confirm(FirmwarePlan) -> bool); the gate (firmware_gate: only
    [0x4000, package end), exactly the app's own diff); M-Flasher's data-places warning only after
    confirm_data(FirmwarePlan, warning) -> bool; afterwards a fresh read must equal the computed flash exactly – the
    boot area (m-bootloader) unchanged."""
    from .. import hardware
    store = _store_of(job)
    _write_verified(store)
    if expected_sha256 and hashlib.sha256(package).hexdigest() != expected_sha256.lower():
        raise Refused(_("The package is no longer the file that was checked (its SHA-256 differs). Nothing was "
                        "written."), "package-changed")
    ident, flash = firmware_package(package)
    if hardware.revision(job.profile) == hardware.NEW and not new_chip_ok:
        raise Refused(_newer_firmware_refusal(name or ident), "newer-chip")     # (before the unit is sent anywhere)
    job.purpose = "firmware"
    with _Lock(job, "mflasher-firmware"), _back_on_refusal(job):
        res = _firmware_locked(job, package, ident, flash, name or ident, new_chip_ok, confirm, confirm_data)
    res.log_path = job.log_path
    return res


def _firmware_locked(job: Job, package: bytes, ident: str, flash: bytes, name: str, new_chip_ok: bool, confirm,
                     confirm_data) -> FirmwareResult:
    from .. import hardware
    from . import bootarea
    ensure_download_mode(job)
    info = identify(job, need_profile=True)
    if not info.unit_id:
        raise Refused(_("M-Flasher reports no unit ID for the connected FM-1, so the app cannot tie the write to this "
                        "unit. Nothing was written."), "no-unit-id")
    if info.flash_id not in (hardware.OLD_ID, hardware.NEW_ID):
        raise Refused(_("M-Flasher reads a flash chip the app does not know ({chip}). Nothing was written.").format(
            chip=chip_name(info.flash_id)), "unknown-chip")
    rev = _session_revision(job, info)
    if rev == hardware.NEW and not new_chip_ok:
        raise Refused(_newer_firmware_refusal(name), "newer-chip")
    if not info.m_bootloader:
        raise Refused(_("This way – installing through download mode, the boot area kept – is for an FM-1 with "
                        "m-bootloader. M-Flasher reads another bootloader on this one ({bootloader}): install the "
                        "firmware the usual way from the running firmware. Nothing was written.").format(
                            bootloader=info.bootloader or "?"), "not-mboot")
    pre = backup_before_boot_write(job)
    if isinstance(pre, Refusal):
        raise Refused(pre.reason, pre.kind or "no-backup")
    saved = job.pre
    if not bootarea.intact(saved.image[:BOOT_AREA]) or not bootarea.stored_mbootloader(saved.image[:BOOT_AREA]):
        raise Refused(_("The backup made just now shows no intact m-bootloader in the boot area, so the app does not "
                        "install a firmware this way. Nothing was written; the backup is kept."), "not-mboot")
    target = firmware_target(saved.image, flash)
    runs = diff_runs(target, saved.image)
    if not runs:
        job.tell(_("The FM-1's flash already holds exactly this firmware: nothing to write."))
        job.ev("plan", "nothing to write: the firmware is there already")
        _settle_op_record(job, "firmware")
        again = restart(job) if job.sent_by_app else False
        return FirmwareResult(False, True, again, pre, ident, note="already")
    end = len(flash)
    plan = FirmwarePlan(job.profile.name, info.unit_id, info.flash_id, rev, ident, name, end, runs,
                        sum((b - a) // SECTOR for a, b in runs), info.bootloader, _firmware_text(job.profile),
                        info.firmware, pre)
    if rev == hardware.NEW:
        plan.notes.append(_("On a newer unit: the hardware rule (data/hardware.json) lets {name} through; installing "
                            "through download mode has not been run on such a unit by this app.").format(name=name))
    job.ev("plan", f"install firmware {ident} through download mode: {plan.sectors} sectors in 0x04000-0x{end - 1:05X}"
                   f" ({', '.join(f'0x{a:05X}-0x{b - 1:05X}' for a, b in runs)}); the boot area stays")
    if job.cancelled():
        raise Refused(_("Cancelled. Nothing was written to the FM-1."), "cancelled")
    if not confirm(plan):
        job.ev("confirm", "declined")
        raise Refused(_("Nothing was written. The FM-1 is unchanged; the backup made just now is kept."), "declined")
    job.ev("confirm", "confirmed by the user")
    planned = _inhibit(job, "planned", os.getpid())  # (active before "yes" – released after the verify read)
    try:
        return _firmware_write(job, plan, info, saved, pre, package, ident, target, runs, confirm_data)
    finally:
        if planned is not None:
            try:
                planned.release()
            except Exception:  # noqa: BLE001
                pass


def _firmware_write(job: Job, plan: FirmwarePlan, info, saved, pre, package: bytes, ident: str, target: bytes,
                    runs, confirm_data) -> FirmwareResult:
    expect = {"unit_id": info.unit_id, "flash_id": info.flash_id, "chip_key": info.chip_key, "identity": ident,
              "end": plan.end, "runs": runs}
    w = None
    for attempt in (1, 2):
        staging = _work_folder(job)
        try:
            copy = _private_file(staging, "package.fwsc", package)
        except OSError as e:
            shutil.rmtree(staging, ignore_errors=True)
            raise Refused(_("The app's private working folder cannot be created: {error}. Nothing was written.").format(
                error=getattr(e, "strerror", None) or e), "folder") from e
        w = _Write(job, staging, {"op": "firmware", "unit": mask(info.unit_id), "flash": f"0x{info.flash_id:06X}",
                                  "image": "", "image_sha256": "", "file": "package.fwsc", "identity": ident,
                                  "file_sha256": hashlib.sha256(package).hexdigest(),
                                  "runs": [[a, b] for a, b in runs]}, saved, pre)
        job.tell(_("Writing the firmware (do not unplug the FM-1 or switch it off) …"))
        try:
            w.run(["firmware", str(copy), "--device", job.device, "--backup-dir", str(staging)],
                  firmware_gate(expect))
            break
        except Refused as e:
            seen = expect.get("seen_warning")
            if e.kind != "gate" or attempt != 1 or not seen or expect.get("accept"):
                raise
            job.ev("plan", "M-Flasher warns that the installed firmware keeps its data in other places: stdin closed, "
                           "nothing written; the user is asked")
            if job.cancelled():
                raise Refused(_("Cancelled. Nothing was written to the FM-1."), "cancelled") from e
            if not confirm_data(plan, seen):
                job.ev("confirm", "the data-places warning was declined")
                raise Refused(_("Nothing was written. The FM-1 is unchanged; the backup made just now is kept."),
                              "declined-data") from e
            job.ev("confirm", "the data-places warning confirmed by the user")
            expect["accept"] = seen
    got, vi = w.fresh_read()
    problem = ""
    if got[:BOOT_AREA] != saved.image[:BOOT_AREA]:
        problem = _("the boot area changed")
    elif got[plan.end:] != saved.image[plan.end:]:
        problem = _("the flash changed after the package's end")
    elif got != target:
        problem = _("the firmware area does not hold the package")
    if problem:
        raise w.failed(problem, "verify-mismatch")
    job.ev("verify", "fresh read: the package from 0x04000 to its end, the boot area and the rest unchanged")
    w.verified()
    _note_boot_area(job, got, vi, "backup")
    again = restart(job)
    return FirmwareResult(True, True, again, pre, ident, w.auto, plan.sectors, None,
                          data_warning=expect.get("accept", ""))


def _settle_op_record(job: Job, op: str):
    """an open record of this very write (op) on this unit, whose result is on the unit now: settled"""
    path = record_file(job.journal.dir if job.journal is not None else None)
    rec = _load_record(path)
    if rec.get("state") in ("writing", "failed") and rec.get("op") == op and _record_is_this_unit(job, rec):
        rec.update(state="verified", ended=time.time(), note="checked later: the unit holds the expected state")
        try:
            _save_record(path, rec)
            job.ev("record", f"the open {op} record is settled")
        except OSError:
            pass


# ------------------------------------------------------------------------------------------------ checking a write later

@dataclass
class VerifyResult:
    same: bool
    first_difference: int              # -1 when the same
    restarted: bool
    log_path: Path | None = None


def verify_install(job: Job, expected: bytes, end: int, what: str) -> VerifyResult:
    """after a write the app did not make through M-Flasher (SLOOP 3 over MIDI MODE): a fresh whole-flash read in
    download mode (one `backup`, checked against the chip's CRC-32; nothing is written) compared with what the flash
    should hold from 0x00000 to `end` – the boot area and the written firmware. Beyond `end` the new firmware may
    already have written its own data on its first start, so that is not compared. Then the unit is restarted."""
    job.purpose = job.purpose or "backup"
    with _Lock(job, "mflasher-verify"), _back_on_refusal(job):
        ensure_download_mode(job)
        identify(job, need_profile=True)
        job.tell(_("Reading the whole flash to check {what} (nothing is written) …").format(what=what))
        got, _vi = _fresh_read_only(job)
        diff = next((i for i in range(min(end, len(got), len(expected))) if got[i] != expected[i]), -1)
        job.ev("verify", f"fresh read against {what}: " + ("the same in 0x00000-0x" + f"{end - 1:05X}" if diff < 0
                                                           else f"differs first at 0x{diff:05X}"))
        again = restart(job)
    return VerifyResult(diff < 0, diff, again, job.log_path)


def latest_backup(folder: Path, unit_id: str) -> Path | None:
    """the newest intact whole-flash backup of this unit in its Flash folder (not one taken while a write onto it had
    not ended verified) – "Back to the last backup" """
    best = None
    for p, _k in unit_backups(folder, unit_id):
        try:
            if best is None or p.stat().st_mtime > best.stat().st_mtime:
                best = p
        except OSError:
            continue
    return best


# ------------------------------------------------------------------------------------------------ importing a backup

@dataclass
class ImportResult:
    path: Path
    profile: object
    unit_id: str
    file_date: str
    already: bool = False


def import_backup(source: Path, backups_dir: Path, profiles, choose=None) -> ImportResult:
    """"Import a flash backup …": a whole-flash image made by M-Flasher itself (its .bin and its own .json) or by the
    app, kept elsewhere, becomes a backup of the app – only when it is the whole flash (1 MiB), matches the SHA-256 in
    its .json, names its unit id and flash chip there (the image itself does not carry them: the unit id is the flash
    chip's own number, read by command) and has an intact boot area. It goes to the profile whose unit id it names;
    with no such profile, choose(unit_id, candidates) -> profile asks (only profiles that know no unit id yet are
    offered, and the chosen one learns this id) – never a guess. The copy keeps its own date."""
    from .. import flashdump
    from ..devices import profile_unit_ids, unit_id_forms
    from ..flash import mboot
    from . import bootarea
    source = Path(source)
    try:
        raw = source.read_bytes()
    except OSError as e:
        raise Refused(_("The file cannot be read: {error}").format(error=e.strerror or e), "import-read") from e
    if len(raw) != FLASH_SIZE:
        raise Refused(_("This file is not a copy of the whole flash (1 MiB) – nothing was imported."), "import-size")
    if not source.with_suffix(".json").exists():
        raise Refused(_("There is no .json beside this file. M-Flasher writes the unit ID and the flash chip into the "
                        ".json beside its backup – the image itself does not contain them – so without it the app "
                        "cannot tell which FM-1 the copy belongs to. Nothing was imported."), "import-no-json")
    try:
        side = flashdump.read_any_side(source)
    except flashdump.FlashDumpError as e:
        raise Refused(str(e) + " " + _("Nothing was imported."), "import-damaged") from e
    uid, fid = flashdump.side_unit_id(side), flashdump.side_flash_id(side)
    if not uid or not fid:
        raise Refused(_("The .json beside this file names no unit ID or no flash chip, so the app cannot tell which "
                        "FM-1 the copy belongs to. Nothing was imported."), "import-no-unit-id")
    if not bootarea.intact(raw[:BOOT_AREA]):
        raise Refused(_("The boot area in this copy is not intact (as after an interrupted write), so it can never "
                        "be a way back. Nothing was imported."), "import-boot-broken")
    hits = profiles.by_unit_id(uid) if profiles is not None else []
    if len(hits) > 1:
        raise Refused(_("Two of your device profiles have this unit ID ({uid}): merge them on the page “Devices” "
                        "first. Nothing was imported.").format(uid=mask(uid)), "two-profiles")
    prof = hits[0] if hits else None
    if prof is None:
        cands = [p for p in (profiles.profiles.values() if profiles is not None else [])
                 if not {f for f in profile_unit_ids(p) if len(f) == 16}]
        prof = choose(uid, cands) if (choose is not None and cands) else None
        if prof is None or prof not in cands:
            raise Refused(_("This copy belongs to an FM-1 with unit ID {uid}, and none of your device profiles is "
                            "known to be that unit. Connect it once (or choose its profile when the app asks), then "
                            "import again. Nothing was imported.").format(uid=mask(uid)), "import-unassigned")
    from .. import hardware
    rec = getattr(prof, "flash_id", "") or ""
    if rec and hardware.was_read(prof):
        try:
            if int(rec, 16) != fid:
                raise Refused(_("This copy was read from another flash chip ({chip}) than the one recorded for "
                                "“{name}”. Nothing was imported.").format(chip=chip_name(fid), name=prof.name),
                              "import-other-chip")
        except ValueError:
            pass
    sha = hashlib.sha256(raw).hexdigest()
    folder = Path(backups_dir) / prof.folder_name / "Flash"
    for p in folder.glob("*.bin") if folder.is_dir() else []:
        try:
            if hashlib.sha256(p.read_bytes()).hexdigest() == sha:
                return ImportResult(p, prof, uid, str(side.get("created") or ""), already=True)
        except OSError:
            continue
    when = str(side.get("created") or "")
    if not when:
        import datetime as _dt
        when = _dt.datetime.fromtimestamp(source.stat().st_mtime).astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
    origin = "M-Flasher" if side.get("format") == flashdump.MFLASHER_FORMAT else "FM-1 Companion"
    try:
        key = int(str(side.get("chip key") or ""), 16)
    except ValueError:
        key = None
    path = flashdump.write_backup(
        folder, mboot.backup_name(prof.name), raw, device=prof.name, read_through=f"imported from {origin} "
        f"({side.get('read_through') or ''})".replace(" ()", ""), jedec=fid, unit_id=uid, chip_key=key,
        capacity=FLASH_SIZE, profile=getattr(prof, "id", ""), firmware=side.get("firmware") or {},
        note=f"imported from {origin}, {when}",
        extra={"created": when, "imported": {"from": origin, "file_date": when, "file": source.name}})
    try:
        import datetime as _dt
        t = _dt.datetime.strptime(when, "%Y-%m-%dT%H:%M:%S%z").timestamp()
        os.utime(path, (t, t))                     # (its place among the unit's backups is its own date)
    except (ValueError, OSError):
        pass
    if not {f for f in profile_unit_ids(prof) if len(f) == 16} and unit_id_forms(uid):
        prof.unit_id = uid                         # (the user said which unit it is)
    try:
        if profiles is not None:
            profiles.save()
    except OSError as e:
        log.warning("profile not saved: %s", e)
    return ImportResult(path, prof, uid, when)


# ------------------------------------------------------------------------------------------------ the write record

def _private_copy(staging: Path, data: bytes, unit_id: str) -> Path:
    """the image as M-Flasher gets it: a copy in the private 0700 staging folder (the user's file may change while
    the dialog is open) with a description holding M-Flasher's two keys; read back and hashed again"""
    copy = staging / "image.bin"
    sha = hashlib.sha256(data).hexdigest()
    fd = os.open(copy, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    side = staging / "image.json"
    fd = os.open(side, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps({"sha256": sha, "unit id": unit_id}, indent=2) + "\n")
    if hashlib.sha256(copy.read_bytes()).hexdigest() != sha:
        raise OSError(0, "the private copy does not read back as written")
    _sync_dir(staging)
    return copy


def _sync_dir(d: Path):
    try:
        fd = os.open(d, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def record_file(root: Path | None = None) -> Path:
    if root is None:
        from ..flash import journal as fj
        root = fj.root()
    return Path(root) / RECORD


def _save_record(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, indent=1))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    _sync_dir(path.parent)


def _load_record(path: Path) -> dict:
    try:
        d = json.loads(Path(path).read_text("utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_flight(job: Job, path: Path):
    """the job's log so far (masked), on disk before "yes" – the in-memory log is lost if the app dies"""
    fl = job.flight
    if fl is None:
        return
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(getattr(fl, "lines", [])) + "\n")
        f.flush()
        os.fsync(f.fileno())


def unfinished_write(root: Path | None = None) -> dict | None:
    """a write-back that did not end verified (the app died, or the write failed): its record, with "alive" (the
    M-Flasher process still runs) and "done" (its output has the "Done: … verified" line). None: nothing open."""
    rec = _load_record(record_file(root))
    if rec.get("state") not in ("writing", "failed"):
        return None
    from ..flash import journal as fj
    pid = int(rec.get("pid") or 0)
    # (pid + its start time: a pid the system reused for another program does not count)
    rec["alive"] = bool(pid) and rec.get("state") == "writing" and bool(rec.get("pid_start")) and \
        fj._alive(pid, str(rec.get("pid_start")))
    try:
        out = Path(rec.get("out") or "").read_text("utf-8", errors="replace") if rec.get("out") else ""
    except OSError:
        out = ""
    rec["done"] = mp.done_verified(out)
    rec["started_writing"] = bool(mp.written_runs(out))
    return rec


def _close_record_if(job: Job, sha: str):
    """an open record whose image (or state before) is exactly what the unit holds now: settled – it ends"""
    path = record_file(job.journal.dir if job.journal is not None else None)
    rec = _load_record(path)
    if rec.get("state") in ("writing", "failed") and sha in (rec.get("image_sha256"), rec.get("pre_sha256")) and \
            _record_is_this_unit(job, rec):
        rec.update(state="verified", ended=time.time(), note="checked later: the unit holds it")
        try:
            _save_record(path, rec)
            job.ev("record", "the open write-back record is settled: the unit holds that image")
        except OSError:
            pass


def rerun_problem(rec: dict, which: str) -> str:
    """before "write back again" from a record: the file must still be the one that was being written (its
    SHA-256 in the record) -> "" or the reason (translated)"""
    path = Path(str(rec.get("image" if which == "image" else "pre_backup") or ""))
    want = str(rec.get("image_sha256" if which == "image" else "pre_sha256") or "")
    try:
        got = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return _("The file {name} is no longer there.").format(name=path.name)
    if not want or got != want:
        return _("The file {name} is no longer the one that was being written (its SHA-256 differs). Nothing is "
                 "written from it.").format(name=path.name)
    return ""


def forget_write(root: Path | None = None):
    """the owner settled an interrupted write-back: the record goes"""
    record_file(root).unlink(missing_ok=True)
