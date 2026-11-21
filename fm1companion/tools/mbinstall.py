# SPDX-License-Identifier: GPL-3.0-only
"""SLOOP 3 onto the FM-1 over m-bootloader's MIDI MODE – the app as the host of m-bootloader's documented install
protocol (m-bootloader docs/SPEC.md §11.6, src/midi_update.c), offline from the bundled, pinned application area
(tools/bundled/sloop3/sloop3-3.4.0.area; manifest.json). No Qt here (the dialogs are in ui/sloop3_ui.py).

**The package** is built exactly as SLOOP 3's own installer page builds it – a port of its ``web/fwpkg.js`` at tag
v3.4.0-beta (isod89/sloop3-fm1, GPL-3.0, commit 2228313): the plain application area encrypted with the unit's chip
key (each 32-byte line from ``key ^ (offset / 4)``), placed at flash 0x4000 inside a ``flash.bin`` of
``0x4000 + ceil(len / 4096) · 4096`` bytes (0xFF elsewhere), wrapped in a one-entry package (header, entry list,
flash.bin at 0x400). tests/test_sloop3_install.py compares it byte for byte with what the page's JavaScript builds
(run with node) and installs it through m-bootloader's own MIDI MODE code compiled for the PC.

**What m-bootloader then does** (its midi_update.c, not the app): it reads the package with 0x30 requests (≤ 512
bytes each, answered with the same 8 bytes plus the data, FF past the end), checks all of it – header and entry
CRCs, the size, that it decrypts with this chip's key, flash.bin's CRC – **before anything is erased**, then reads it
again sector by sector and writes only from 0x4000 to the package's end (never the boot area, never the factory data
at 0xFF000), the first sector last, reading each sector back. A request for 0xF0000000 means "finished": the host
answers with zeros, the unit starts SLOOP 3.

**What the app adds** (`install`): the unit must answer 0x12 as m-bootloader (kind 1, not the RAM stage), with the
unit id and flash chip of the job's profile; on a newer unit only with an m-bootloader build and a SLOOP 3 the
hardware rule lets through; a whole-flash backup first, read twice in MIDI MODE and each read checked against the
chip's own CRC-32 (no skip); the package checked as the page checks it; a danger dialog; a write record on disk and
the app kept from quitting while m-bootloader writes; a refusal frame (command 0) or a unit that stops asking is
reported – and whether anything could have been erased (only after the whole package was read once).

The start key (F0 22 24 35 7F F7) and the 0x30 answer are literals here; flash/mboot.py stays read-only.
"""
from __future__ import annotations

import hashlib
import os
import queue
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..i18n import _
from ..flash import mboot

FIRST, SECTOR, AT = 0x4000, 0x1000, 0x400
FINISH = 0xF0000000
START = (0xF0, 0x22, 0x24, 0x35, 0x7F, 0xF7)          # "start an install" (SPEC §11.6) – the only key sent here
CMD_REQUEST = 0x30
REQUEST_TIMEOUT = 15.0        # m-bootloader asks each part up to 5 × 2 s: past this it has stopped asking
FLASH_SIZE = 0x100000
AREA_LIMIT = 0x74000          # SLOOP 3's tools/mkfw.py: the application area must end before this
SOURCE = "isod89/sloop3-fm1 web/fwpkg.js at v3.4.0-beta (commit 2228313)"


class InstallError(Exception):
    """a ready (translated) sentence; `kind` for tests and logs; `erased`: m-bootloader may have started erasing"""

    def __init__(self, reason: str, kind: str = "", erased: bool = False):
        super().__init__(reason)
        self.reason, self.kind, self.erased = reason, kind, erased


# ------------------------------------------------------------------------------------------------ fwpkg.js, ported

def crc16(data, c: int = 0) -> int:
    for b in data:
        c ^= b << 8
        for _i in range(8):
            c = ((c << 1) ^ 0x1021) & 0xFFFF if c & 0x8000 else (c << 1) & 0xFFFF
    return c


def enc(buf: bytearray, off: int, n: int, key: int):
    """ENC (SPEC 5.1): XOR with the state's low byte, then one step of the state – in place"""
    k = key & 0xFFFF
    for i in range(n):
        buf[off + i] ^= k & 0xFF
        k = ((k << 1) ^ 0x1021) & 0xFFFF if k & 0x8000 else (k << 1) & 0xFFFF


def enc_area(area: bytes, key: int) -> bytes:
    """the application area: each 32-byte line from key XOR (its offset / 4)"""
    a = bytearray(area)
    for q in range(0, len(a), 32):
        enc(a, q, min(32, len(a) - q), key ^ ((q >> 2) & 0xFFFF))
    return bytes(a)


@dataclass
class Package:
    pkg: bytes                 # the whole package m-bootloader reads
    size: int                  # flash.bin's size: m-bootloader writes [0x4000, size)
    flash_at: int = AT         # where flash.bin starts in the package

    @property
    def flash(self) -> bytes:
        """flash.bin: what the flash holds from 0x4000 to `size` afterwards (its first 0x4000 bytes are FF and never
        written)"""
        return self.pkg[self.flash_at:self.flash_at + self.size]


def build_package(area: bytes, key: int, ota: bytes | None = None) -> Package:
    """fwpkg.js buildPackage: header (64 bytes), the entries (80 each) from 0x40, flash.bin at 0x400; with `ota` a
    second entry after flash.bin, type 100 (the app never passes one: m-bootloader does not use it)"""
    size = FIRST + -(-len(area) // SECTOR) * SECTOR
    flash = bytearray(b"\xff" * size)
    flash[FIRST:FIRST + len(area)] = enc_area(area, key)
    ota_at, n = AT + size, 2 if ota else 1
    pkg = bytearray(ota_at + (len(ota) if ota else 0))
    pkg[AT:AT + size] = flash
    if ota:
        pkg[ota_at:] = ota
    lst = bytearray(80 * n)

    def entry(i, typ, crc, at, ln, name):
        e = bytearray(80)
        e[0:2] = typ.to_bytes(2, "little")
        e[4:6] = crc.to_bytes(2, "little")
        e[8:12] = at.to_bytes(4, "little")
        e[12:16] = ln.to_bytes(4, "little")
        e[64:64 + len(name)] = name.encode("ascii")
        enc(e, 0, 80, 0xFFFF)
        lst[80 * i:80 * (i + 1)] = e
    entry(0, 0, crc16(flash), AT, size, "flash.bin")
    if ota:
        entry(1, 100, crc16(ota), ota_at, len(ota), "ota.bin")
    h = bytearray(64)
    h[2:4] = crc16(lst).to_bytes(2, "little")
    h[4:8] = len(pkg).to_bytes(4, "little")
    h[8:10] = n.to_bytes(2, "little")
    h[10:12] = (4).to_bytes(2, "little")
    h[12:16] = (0x200).to_bytes(4, "little")
    h[16:22] = b"AC791N"
    h[0:2] = crc16(h[2:64]).to_bytes(2, "little")
    enc(h, 0, 64, 0xFFFF)
    pkg[0:64] = h
    pkg[0x40:0x40 + len(lst)] = lst
    return Package(bytes(pkg), size, AT)


def check_package(p: Package, key: int) -> str:
    """fwpkg.js checkPackage – the checks m-bootloader makes before writing: "" or what is wrong ("header", "list",
    "entry", "crc", "key")"""
    g16 = lambda a, i: a[i] | a[i + 1] << 8          # noqa: E731
    h = bytearray(p.pkg[:64])
    enc(h, 0, 64, 0xFFFF)
    if crc16(h[2:64]) != g16(h, 0):
        return "header"
    n = g16(h, 8)
    if n < 1 or n > 11 or crc16(p.pkg[0x40:0x40 + 80 * n]) != g16(h, 2):
        return "list"
    e = bytearray(p.pkg[0x40:0x40 + 80])
    enc(e, 0, 80, 0xFFFF)
    off, size = int.from_bytes(e[8:12], "little"), int.from_bytes(e[12:16], "little")
    if g16(e, 0) != 0 or size % 0x1000 or size <= 0x5000 or size > 0x7A000:
        return "entry"
    if crc16(p.pkg[off:off + size]) != g16(e, 4):
        return "crc"
    r = bytearray(p.pkg[off + 0x4000:off + 0x4000 + 32])
    enc(r, 0, 32, key)
    if crc16(r[2:32]) != g16(r, 0):
        return "key"
    return ""


def check_area(area: bytes) -> str:
    """the plain application area as SLOOP 3's tools/mkfw.py makes it: "" or what is wrong – its two records (the
    area head at +0, app.bin at +0x20) each with a valid CRC-16, and it ends before 0x74000"""
    if not 0x1000 < len(area) < AREA_LIMIT:
        return "size"
    for at in (0, 0x20):
        r = area[at:at + 32]
        if crc16(r[2:32]) != (r[0] | r[1] << 8):
            return f"record at +0x{at:02X}"
    return ""


def bundled_area(store=None) -> tuple[bytes, str]:
    """the pinned SLOOP 3 application area this app release bundles (manifest SHA-256 checked on the bytes read) ->
    (bytes, version "3.4.0"); InstallError otherwise. Never an updated copy: installing uses the reviewed one."""
    if store is None:
        from .store import Store
        store = Store()
    c = store.bundled("sloop3")
    if c is None:
        raise InstallError(_("The bundled SLOOP 3 is missing. Reinstall the app. Nothing was written."), "no-area")
    try:
        data = c.path.read_bytes() if c.path is not None else b""
    except OSError:
        data = b""
    if not data or hashlib.sha256(data).hexdigest() != (c.sha256 or "").lower():
        raise InstallError(_("The bundled SLOOP 3 did not pass its checksum test, so the app does not install it. "
                             "Reinstall the app. Nothing was written."), "area-checksum")
    why = check_area(data)
    if why:
        raise InstallError(_("The bundled SLOOP 3 is not an application area the app knows ({why}). Nothing was "
                             "written.").format(why=why), "area")
    return data, c.version


def runs_to_write(pkg: Package, current: bytes) -> list[tuple[int, int]]:
    """the 4 KiB sectors in [0x4000, size) where flash.bin differs from the flash now (m-bootloader skips the
    others), merged into runs"""
    out: list[list[int]] = []
    fl = pkg.flash
    for a in range(FIRST, pkg.size, SECTOR):
        if fl[a:a + SECTOR] != current[a:a + SECTOR]:
            if out and out[-1][1] == a:
                out[-1][1] = a + SECTOR
            else:
                out.append([a, a + SECTOR])
    return [(x, y) for x, y in out]


def expected_flash(pkg: Package, current: bytes) -> bytes:
    """the whole flash after the install: flash.bin from 0x4000 to its end, the rest as it is"""
    return bytes(current[:FIRST]) + pkg.flash[FIRST:] + bytes(current[pkg.size:])


# ------------------------------------------------------------------------------------------------ the 0x30 server

def _answer_frame(body8: bytes, data: bytes) -> list[int]:
    """the answer to one 0x30 request: the request's 8 bytes, then the data (literal command 0x30)"""
    body = bytes(body8) + bytes(data)
    n = len(body)
    raw = bytes([0, 0x59, CMD_REQUEST, n & 255, n >> 8 & 255, n >> 16 & 255]) + body + bytes([~sum(body) & 255])
    return [0xF0, *mboot._pack(raw), 0xF7]


@dataclass
class Served:
    requests: int = 0
    flash_bytes: int = 0          # bytes of flash.bin and later served (the page's "served")
    finished: bool = False
    seconds: float = 0.0
    log: list[str] = field(default_factory=list)

    def erasing_possible(self, pkg: Package) -> bool:
        """m-bootloader reads all of flash.bin (and 32 bytes for the key check) before it erases anything"""
        return self.flash_bytes > pkg.size + 32


def serve(transport, pkg: Package, *, progress=None, timeout: float = REQUEST_TIMEOUT, on_started=None) -> Served:
    """send the start key and answer m-bootloader's 0x30 requests until it asks for FINISH. InstallError on a
    refusal frame (command 0) or when it stops asking (`erased` says whether it may have started erasing)"""
    q: queue.Queue = queue.Queue()

    def rx(data):
        if data and data[0] == 0xF0:
            q.put(list(data))
    transport.receiver = rx
    st = Served()
    t0 = time.monotonic()
    transport.send(list(START))
    if on_started:
        on_started()
    total = pkg.size + 32 + pkg.size - FIRST          # (the check pass, then the write pass)
    while True:
        try:
            d = q.get(timeout=timeout)
        except queue.Empty:
            st.seconds = time.monotonic() - t0
            erased = st.erasing_possible(pkg)
            raise InstallError(
                _("The FM-1 stopped asking for SLOOP 3 while it was writing. The firmware area is incomplete; the "
                  "boot area (m-bootloader) was not written, so its menu still opens (hold PLAY while switching on) "
                  "and MIDI MODE can install again, or the backup made before can be written back.") if erased else
                _("The FM-1 stopped asking for SLOOP 3 before it erased anything – m-bootloader checks the whole "
                  "package first and refuses one that does not match (checksum, chip key). Nothing was written; the "
                  "FM-1 still has its firmware."), "stopped-writing" if erased else "stopped", erased)
        f = mboot._parse(d)
        if f is None:
            continue
        cmd, body = f
        if cmd == 0:
            st.seconds = time.monotonic() - t0
            erased = st.erasing_possible(pkg)
            raise InstallError(
                (_("m-bootloader refused the package (code {code}).").format(code=body[0] if body else "?") + " " +
                 (_("It had read it all and may have started writing: the firmware area may be incomplete. Its menu "
                    "still opens (hold PLAY while switching on); install again from MIDI MODE, or write the backup "
                    "made before back.") if erased else
                  _("Nothing was erased; the FM-1 still has its firmware."))), "refused", erased)
        if cmd != CMD_REQUEST or len(body) != 8:
            continue
        addr = int.from_bytes(body[1:5], "little")
        ln = body[5] | body[6] << 8 | body[7] << 16
        if not 0 < ln <= mboot.READ_MAX:
            continue
        st.requests += 1
        if addr == FINISH:
            transport.send(_answer_frame(body, bytes(ln)))
            st.finished = True
            st.seconds = time.monotonic() - t0
            return st
        part = (pkg.pkg[addr:addr + ln] + b"\xff" * ln)[:ln] if addr < len(pkg.pkg) else b"\xff" * ln
        transport.send(_answer_frame(body, part))
        if addr >= pkg.flash_at:
            st.flash_bytes += ln
            if progress:
                try:
                    progress(min(st.flash_bytes, total), total, st.flash_bytes <= pkg.size + 32)
                except Exception:  # noqa: BLE001 – a UI callback never stops the install
                    pass


# ------------------------------------------------------------------------------------------------ the job

@dataclass
class InstallPlan:
    """what the danger dialog of "Install SLOOP 3" shows before anything is written"""
    unit_name: str
    unit_id: str
    flash_id: int
    revision: str
    mboot_version: str
    version: str                  # the SLOOP 3 version installed (the bundled area's)
    area_sha256: str
    size: int                     # m-bootloader writes [0x4000, size)
    runs: list[tuple[int, int]]
    sectors: int
    firmware: str                 # the firmware the app knows on the unit
    backup: Path
    notes: list[str] = field(default_factory=list)


@dataclass
class InstallResult:
    written: bool
    finished: bool
    backup: Path | None
    version: str = ""
    package_flash: bytes = b""
    expected: bytes = b""         # the whole flash as it should be afterwards (for the verify read)
    sectors: int = 0
    note: str = ""
    log_path: Path | None = None


def _double_read(host, job, info, say) -> tuple[Path, bytes]:
    """the whole flash read twice in MIDI MODE, each read checked against the chip's own CRC-32 (mboot.read_flash),
    both identical -> one image kept in the device's Flash folder with the app's description"""
    from .. import flashdump
    from . import flashjob as FJ
    reads = []
    for n in (1, 2):
        say(_("Reading the whole flash ({n} of 2; nothing is written) …").format(n=n))
        try:
            reads.append(host.read_flash(abort=job.cancelled))
        except mboot.MbootError as e:
            raise FJ.Refused(str(e) + " " + _("Nothing was written."), "read") from e
        job.ev("read", f"MIDI MODE read {n}: sha256 {hashlib.sha256(reads[-1]).hexdigest()}, chip CRC-32 confirmed")
    if reads[0] != reads[1]:
        at = next(i for i in range(len(reads[0])) if reads[0][i] != reads[1][i])
        raise FJ.Refused(_("The two reads of the flash differ (first at 0x{at:05X}). The USB connection is "
                           "unreliable: try another cable or port, without a hub. Nothing was kept, nothing was "
                           "written.").format(at=at), "read-mismatch")
    image = reads[0]
    prof = job.profile
    try:
        path = flashdump.write_backup(
            job.folder, mboot.backup_name(prof.name), image, device=prof.name,
            read_through=f"m-bootloader {info.version} (MIDI MODE), read twice and compared",
            jedec=info.jedec & 0xFFFFFF, unit_id=info.uid, chip_key=info.key, capacity=info.capacity,
            profile=getattr(prof, "id", ""), firmware={"app": FJ._firmware_text(prof), "bootloader":
                                                         f"m-bootloader {info.version}"},
            double_read=True, note="automatic backup before installing SLOOP 3",
            during_open_write=FJ.unfinished_write(job.journal.dir if job.journal is not None else None) is not None)
        flashdump.read_side(path)
    except (OSError, flashdump.FlashDumpError) as e:
        raise FJ.Refused(_("The flash backup could not be saved: {error}. Nothing was written.").format(
            error=getattr(e, "strerror", None) or e), "save") from e
    job.ev("saved", f"{path.name}: sha256 {hashlib.sha256(image).hexdigest()}")
    return path, image


def install(job, transport, confirm, *, new_chip_ok: bool = False, area: bytes | None = None,
            version: str = "") -> InstallResult:
    """"Install SLOOP 3" over MIDI MODE on `transport` (the FM-1's "FM-1 Update" port, opened by the caller).
    `job` is a tools/flashjob.Job (profile, profiles, backups_dir, journal, log_dir, cancel, say, inhibit,
    on_writing). confirm(InstallPlan) -> bool is asked after the backup. Refused (flashjob) when nothing was written;
    WriteFailed when m-bootloader may have erased something."""
    from . import flashjob as FJ
    if area is None:
        try:
            area, version = bundled_area(job.store)
        except InstallError as e:
            raise FJ.Refused(e.reason, e.kind) from e
    elif check_area(area):
        raise FJ.Refused(_("This is not a SLOOP 3 application area the app knows. Nothing was written."), "area")
    job.purpose = "sloop3"
    with FJ._Lock(job, "mboot-sloop3"):
        res = _install_locked(job, transport, confirm, area, version, new_chip_ok)
    res.log_path = job.log_path
    return res


def _install_locked(job, transport, confirm, area: bytes, version: str, new_chip_ok: bool) -> InstallResult:
    from .. import hardware
    from ..devices import profile_unit_ids, unit_id_forms
    from . import flashjob as FJ
    say = job.tell
    say(_("Asking who answers …"))
    host = mboot.Host(transport)
    info = host.identify()
    if info is None:
        raise FJ.Refused(_("This is not m-bootloader's MIDI MODE (no answer to its question 0x12). Nothing was "
                           "written."), "not-mboot")
    jedec = info.jedec & 0xFFFFFF
    job.ev("info", f"m-bootloader {info.version}, kind {info.kind}, flash 0x{jedec:06X}, chip key "
                   f"0x{info.key:04X}, unit id {FJ.mask(info.uid)}")
    if info.kind != 1:
        raise FJ.Refused(_("This is m-bootloader's RAM stage, not m-bootloader installed in the boot area. The app "
                           "installs SLOOP 3 only on an FM-1 with m-bootloader itself. Nothing was written."),
                         "ram-stage")
    prof = job.profile
    hits = job.profiles.by_unit_id(info.uid) if job.profiles is not None and unit_id_forms(info.uid) else []
    known = {f for f in profile_unit_ids(prof) if len(f) == 16} if prof is not None else set()
    if prof is None or not known or not (unit_id_forms(info.uid) & known) or any(h is not prof for h in hits):
        raise FJ.Refused(_("The app does not know this FM-1 as “{name}” by its unit ID ({uid}), so it does not "
                           "write to it. Choose the right device at the top; a whole-flash backup in download mode "
                           "(Recovery › Tools) teaches the app a unit's ID. Nothing was written.").format(
                               name=getattr(prof, "name", "?"), uid=FJ.mask(info.uid)), "unknown-unit")
    rec = getattr(prof, "flash_id", "") or ""
    if rec and hardware.was_read(prof):
        try:
            if int(rec, 16) != jedec:
                raise FJ.Refused(_("The flash chip m-bootloader reads ({chip}) is not the one recorded for “{name}”. "
                                   "Is this another FM-1? Nothing was written.").format(
                                       chip=FJ.chip_name(jedec), name=prof.name), "other-chip")
        except ValueError:
            pass
    if jedec not in (hardware.OLD_ID, hardware.NEW_ID) or info.capacity != FLASH_SIZE:
        raise FJ.Refused(_("m-bootloader reads a flash chip the app does not know ({chip}). Nothing was "
                           "written.").format(chip=FJ.chip_name(jedec)), "unknown-chip")
    rev = hardware.revision_of_id(jedec)
    if hardware.NEW in (rev, hardware.revision(prof)):
        rev = hardware.NEW
        if hardware.support("m-bootloader", info.version).status != hardware.YES or not new_chip_ok:
            raise FJ.Refused(_("This FM-1 is a newer unit (logo on the front, flash chip UC25HQ80). The app installs "
                               "SLOOP 3 there only with an m-bootloader build and a SLOOP 3 version known to run on "
                               "such a unit (data/hardware.json); m-bootloader {version} or this SLOOP 3 is not one "
                               "of them. Nothing was written.").format(version=info.version), "newer-chip")
    if hardware.note_flash_id(prof, jedec, "identity") and job.profiles is not None:
        try:
            job.profiles.save()
        except OSError:
            pass
    pkg = build_package(area, info.key)
    bad = check_package(pkg, info.key)
    if bad:
        raise FJ.Refused(_("The SLOOP 3 package built for this FM-1 does not pass the checks m-bootloader makes "
                           "({why}). Nothing was written.").format(why=bad), "package")
    if job.cancelled():
        raise FJ.Refused(_("Cancelled. Nothing was written to the FM-1."), "cancelled")
    pre, image = _double_read(host, job, info, say)
    if bytes(image[:FIRST]) and not _is_mboot_area(image[:FIRST]):
        raise FJ.Refused(_("The backup made just now shows no intact m-bootloader in the boot area, although "
                           "m-bootloader answered. Nothing was written; the backup is kept."), "not-mboot")
    runs = runs_to_write(pkg, image)
    if not runs:
        say(_("SLOOP 3 {version} is on this FM-1 already: nothing to write.").format(version=version))
        job.ev("plan", "nothing to write: SLOOP 3 is there already")
        FJ._settle_op_record(job, "sloop3-install")
        return InstallResult(False, False, pre, version, pkg.flash, expected_flash(pkg, image), note="already")
    plan = InstallPlan(prof.name, info.uid, jedec, rev, info.version, version, hashlib.sha256(area).hexdigest(),
                       pkg.size, runs, sum((b - a) // SECTOR for a, b in runs), FJ._firmware_text(prof), pre)
    if rev == hardware.NEW:
        plan.notes.append(_("On a newer unit: m-bootloader 2.4 and SLOOP 3 3.4.0 ran on one such unit by its owner's "
                            "report (outside this app); this app has not installed SLOOP 3 on one."))
    job.ev("plan", f"install SLOOP 3 {version}: {plan.sectors} sectors in 0x04000-0x{pkg.size - 1:05X}; the boot "
                   f"area and everything from 0x{pkg.size:05X} stay")
    if job.cancelled():
        raise FJ.Refused(_("Cancelled. Nothing was written to the FM-1."), "cancelled")
    if not confirm(plan):
        job.ev("confirm", "declined")
        raise FJ.Refused(_("Nothing was written. The FM-1 is unchanged; the backup made just now is kept."), "declined")
    job.ev("confirm", "confirmed by the user")
    return _write(job, transport, pkg, plan, info, pre, image, version)


def _is_mboot_area(area: bytes) -> bool:
    from . import bootarea
    return bootarea.intact(area) and bootarea.stored_mbootloader(area) is not None


def _write(job, transport, pkg: Package, plan: InstallPlan, info, pre: Path, image: bytes, version: str):
    from ..flash import journal as fj
    from . import flashjob as FJ
    rec_path = FJ.record_file(job.journal.dir if job.journal is not None else None)
    pid = os.getpid()
    record = {"state": "writing", "op": "sloop3-install", "started": time.time(), "pid": pid,
              "pid_start": fj._started(pid), "profile": getattr(job.profile, "id", ""),
              "unit_sha256": hashlib.sha256(info.uid.encode()).hexdigest(), "device": job.profile.name,
              "unit": FJ.mask(info.uid), "flash": f"0x{plan.flash_id:06X}", "pre_backup": str(pre),
              "pre_sha256": hashlib.sha256(image).hexdigest(), "image": "", "image_sha256": "",
              "identity": f"SLOOP 3 {version}", "area_sha256": plan.area_sha256,
              "runs": [[a, b] for a, b in plan.runs]}
    try:
        FJ._save_record(rec_path, record)
    except OSError as e:
        raise FJ.Refused(_("The app's journal record could not be written ({error}), so it does not start writing. "
                           "Nothing was written.").format(error=e.strerror or e), "record") from e
    held = FJ._inhibit(job, "writing", pid)
    FJ.WRITING.set()
    job.tell(_("m-bootloader is reading SLOOP 3 now and then writes it. This cannot be cancelled – do not unplug the "
               "FM-1 or switch it off."))
    if job.on_writing:
        try:
            job.on_writing(record)
        except Exception:  # noqa: BLE001
            pass
    job.cancel = None
    try:
        st = serve(transport, pkg, progress=getattr(job, "progress", None))
    except InstallError as e:
        job.ev("install", f"{e.kind}: {e.reason}")
        if not e.erased:
            rec_path.unlink(missing_ok=True)
            raise FJ.Refused(e.reason + " " + _("The backup made just now is kept."), e.kind) from e
        record.update(state="failed", ended=time.time(), reason=e.kind)
        try:
            FJ._save_record(rec_path, record)
        except OSError:
            pass
        raise FJ.WriteFailed(e.reason + " " + _("The backup made just before is {name} – it is this unit's state "
                                                "before the install.").format(name=pre.name), e.kind, pre) from e
    finally:
        FJ.WRITING.clear()
        if held is not None:
            try:
                held.release()
            except Exception:  # noqa: BLE001
                pass
    job.ev("install", f"m-bootloader asked for FINISH after {st.requests} requests in {st.seconds:.1f} s: every sector "
                      f"written and read back by m-bootloader")
    record.update(state="verified", ended=time.time(), note="m-bootloader finished: every sector written and read "
                                                            "back by it")
    try:
        FJ._save_record(rec_path, record)
    except OSError:
        pass
    from . import bootarea
    bootarea.note(job.profile, bootarea.MBOOT, "midi-mode", bootloader=f"m-bootloader {info.version}")
    if job.profiles is not None:
        try:
            job.profiles.save()
        except OSError:
            pass
    return InstallResult(True, True, pre, version, pkg.flash, expected_flash(pkg, image), plan.sectors)
