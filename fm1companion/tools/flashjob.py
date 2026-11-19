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

The unit id and flash ID M-Flasher reads go into the profile (hardware.note_flash_id, the unit id learned as on
the MIDI side, ui/device.py `_unit_by_id`): only into the profile whose unit the app itself sent into download mode
and only when no other profile has that id. An image's description never names or changes a profile by itself.
"""
from __future__ import annotations

import hashlib
import logging
import shutil
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
             "In download mode the display stays dark or shows M-Flasher's screen; the computer sees "
             "“WL82 UBOOT1.00”.")


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
        self.j, self.handover = journal, False

    def acquire(self):
        pass

    def release(self):
        if self.handover:
            self.j.release()

    def set_tool(self, pid: int):
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

def _mf(job: Job, args: list[str], gate=None) -> runner.RunResult:
    job.ev("step", "m-flasher " + " ".join(args))
    try:
        r = runner.run_mflasher(args, store=job.store, cancel=job.cancel, log_dir=job.log_dir, journal=job.held,
                                gate=gate, flight=job.flight)
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
        return job.device
    if len(found.midi) > 1:
        raise Refused(_("More than one FM-1 is connected. M-Flasher's switch to download mode reaches only a single "
                        "unit: unplug the others, then try again. Nothing was written."), "several")
    if not found.midi:
        raise Refused(_("No FM-1 found, neither running on USB MIDI nor in download mode.") + "\n\n" + guide_text(),
                      "guide")
    job.tell(_("Sending the FM-1 into USB download mode (it restarts; nothing is written) …"))
    r = _mf(job, ["download-mode"])
    if not r.ok:
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
    job.steps.append(("download-mode", "ok"))
    return dev


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
    prof = job.profile
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
        trusted = job.sent_by_app
    else:
        if len(hits) > 1:
            raise Refused(_("Two of your device profiles have this unit ID ({uid}): merge them on the page "
                            "“Devices” first. Nothing was written.").format(uid=mask(uid)), "two-profiles")
        prof = hits[0] if hits else None
        trusted = prof is not None              # (named by its unit id: the strongest evidence there is)
    if prof is None and need_profile:
        raise Refused(_("The app does not know which of your FM-1 this is (unit ID {uid} is in none of your device "
                        "profiles). A flash backup is written back only onto a unit the app knows: connect it once "
                        "while it runs, or make a flash backup with it first. Nothing was written.").format(
                            uid=mask(uid)), "unknown-unit")
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


def _staging(job: Job, what: str) -> Path:
    d = job.folder / f".m-flasher-{what}-{time.strftime('%Y%m%d-%H%M%S')}-{time.monotonic_ns() % 100000}"
    d.mkdir(parents=True, exist_ok=False)
    d.chmod(0o700)
    return d


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
                double_read=True, note=purpose)
            flashdump.read_side(path)
        except (OSError, flashdump.FlashDumpError) as e:
            raise Refused(_("The flash backup could not be saved: {error}. Nothing was written.").format(
                error=getattr(e, "strerror", None) or e), "save") from e
        sha = hashlib.sha256(a).hexdigest()
        job.ev("saved", f"{path.name}: sha256 {sha}, boot area sha256 {hashlib.sha256(a[:BOOT_AREA]).hexdigest()}")
        job.steps.append(("double-read", "ok"))
        return Saved(path, sha, a)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


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
    with _Lock(job, "mflasher-backup"):
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
        runs = expect["runs"]
        n = sum((b - a) // SECTOR for a, b in runs)
        if plan.nothing or plan.runs != runs or plan.sectors != n or plan.kib != 4 * n:
            return "the planned sector ranges are not the ones the app computed"
        if any(a < 0 or b > FLASH_SIZE or a % SECTOR or b % SECTOR for a, b in plan.runs):
            return "a planned range lies outside the flash"
        if mp.WHOLE_FLASH_NOTE not in plan.notes:
            return "M-Flasher does not treat the image as this unit's backup"
        if plan.warnings:
            return "M-Flasher warns: " + plan.warnings[0][:120]
        return ""
    return gate


def check_image(path: Path) -> dict:
    """the image and its description, before anything is connected -> the description; Refused otherwise"""
    from .. import flashdump
    try:
        side = flashdump.read_side(path)
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
    data = image.read_bytes()
    if hashlib.sha256(data).hexdigest() != side.get("sha256"):     # (changed since the check a moment ago)
        raise Refused(_("The flash backup does not match its description (changed or damaged).") + " " +
                      _("Nothing was written."), "image-damaged")
    with _Lock(job, "mflasher-restore"):
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
    runs = diff_runs(data, saved.image)
    if not runs:
        job.tell(_("The FM-1's flash already holds exactly this image: nothing to write."))
        job.ev("plan", "nothing to write: identical")
        again = restart(job) if job.sent_by_app else False
        return RestoreResult(False, True, again, pre, log_path=None, note="identical")
    plan = RestorePlan(image=image, side=side, unit_name=job.profile.name, unit_id=info.unit_id,
                       flash_id=info.flash_id, revision=hardware.revision_of_id(info.flash_id),
                       image_date=str(side.get("created") or ""),
                       image_firmware=str((side.get("firmware") or {}).get("app") or "")
                       if isinstance(side.get("firmware"), dict) else "",
                       runs=runs, sectors=sum((b - a) // SECTOR for a, b in runs),
                       boot_area=runs[0][0] < BOOT_AREA, backup=pre)
    job.ev("plan", f"{plan.sectors} sectors differ: " + ", ".join(f"0x{a:05X}-0x{b - 1:05X}" for a, b in runs))
    if job.cancelled():
        raise Refused(_("Cancelled. Nothing was written to the FM-1."), "cancelled")
    if not confirm(plan):
        job.ev("confirm", "declined")
        raise Refused(_("Not written back. The FM-1 is unchanged; the backup made just now is kept."),
                      "declined")
    job.ev("confirm", "confirmed by the user")
    expect = {"unit_id": info.unit_id, "flash_id": info.flash_id, "chip_key": info.chip_key, "runs": runs}
    try:
        staging = _staging(job, "auto")
    except OSError as e:
        raise Refused(_("The backup folder cannot be created: {error}").format(
            error=getattr(e, "strerror", None) or e), "folder") from e
    job.tell(_("Writing the flash back (do not unplug the FM-1 or switch it off) …"))
    try:
        r = _mf(job, ["restore", str(image), "--device", job.device, "--backup-dir", str(staging)],
                gate=restore_gate(expect))
    except Refused:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    if not r.answered_yes:
        shutil.rmtree(staging, ignore_errors=True)
        if r.prompted:
            raise Refused(_("M-Flasher's plan did not match what the app checked ({why}), so the app did not "
                            "answer “yes”: nothing was written. The backup made just now is kept.").format(
                                why=r.gate_reason), "gate")
        raise Refused(_("M-Flasher did not get to writing: {reason} Nothing was written.").format(
            reason=_why(r)), "mflasher")
    job.steps.append(("restore", "yes sent"))
    job.cancel = None                          # (from here on the job runs to its end: verify, restart)
    auto = None
    for sp in mp.saved_paths(r.out):
        q = Path(sp)
        if q.is_file() and q.parent.resolve() == staging.resolve():
            same = hashlib.sha256(q.read_bytes()).hexdigest() == saved.sha256
            job.ev("auto-backup", f"M-Flasher's own backup {'equals' if same else 'DIFFERS from'} the one made "
                                  f"before")
            if not same:
                auto = q                          # (kept: the flash changed in between – should never happen)
    if auto is None:
        shutil.rmtree(staging, ignore_errors=True)
    failed = _("The write did not end verified ({reason}). Do not switch the FM-1 off: it stays in download "
               "mode, and writing the flash back again works from there. The backup made just before is {name} "
               "– it is this unit's state before the write.")
    if r.rc != 0 or not mp.done_verified(r.out):
        why = mp.refusal(r.err) if r.rc not in (0, None) else ""
        why = why or (_("M-Flasher was interrupted") if r.rc is None or r.rc < 0 else
                      _("M-Flasher did not confirm the write"))
        raise WriteFailed(failed.format(reason=why, name=pre.name), "write-failed", pre)
    job.tell(_("Written; reading the flash once more to compare …"))
    job.ev("verify", "M-Flasher: everything written is verified")
    vs = _staging(job, "verify")
    try:
        got, _vi = _one_read(job, vs, 1)
    except Refused as e:
        raise WriteFailed(failed.format(reason=e.reason, name=pre.name), "verify-read", pre) from e
    finally:
        shutil.rmtree(vs, ignore_errors=True)
    if hashlib.sha256(got).hexdigest() != hashlib.sha256(data).hexdigest():
        raise WriteFailed(failed.format(reason=_("the fresh read differs from the image"), name=pre.name),
                          "verify-mismatch", pre)
    job.ev("verify", "fresh read equals the image")
    job.steps.append(("verify", "ok"))
    again = restart(job)
    return RestoreResult(True, True, again, pre, auto, plan.sectors, None)
