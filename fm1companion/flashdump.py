# SPDX-License-Identifier: GPL-3.0-only
"""Copies of the whole flash (m-bootloader's MIDI MODE: flash/mboot.py, or a file M-Flasher saved): list them, check
them, and take the user sample slots out of them. Everything here is offline – no FM-1 is involved.

The flash backups the app makes sit in ``Backups/<device>/Flash/`` as the raw image (``…_flash_….bin``) with a small
description beside it (``….json``, format "fm1-flash-backup": size, SHA-256, CRC-32, the chip). When the app made
the copy it compared it with the CRC-32 the flash chip itself computed of the whole flash (mboot.Host.read_flash: a
mismatch is never saved), and the description keeps that CRC-32. ``check`` compares the file with its description
again.

A flash image is not restored by the app: writing it back is M-Flasher's job, in USB download mode, onto the same
FM-1 it came from.

The user sample slots USR1–USR4 (SLOOP 3 3.4.0: fw/main.c ``SLOT_AT``, engine/sample.h): 80 KiB each at flash
0xA0000, 0xB4000, 0xC8000 and 0xE7000, an "FSMP" header (version 1, up to 16 zones of 28 bytes, a name of 8 letters,
the data's length and CRC-32) and the IMA-ADPCM data from +512. These are the **same places and the same format SLOOP
2.8 uses** for its own four sample slots (SLOOP 2's eng_sample.c SMP_USER_BASE / SMP_USER4_BASE): SLOOP 3 writes its
samples over SLOOP 2.8's. So a flash copy made while SLOOP 2.8 ran holds SLOOP 2.8's samples there, one made under SLOOP
3 holds SLOOP 3's – the file names say "SLOOP 3 USR1 …" either way, the format is the same. That region of the chip is
not enciphered (SLOOP 3 opens the window in clear from 0x93000, fw/main.c ``flash_plain_from``), so the bytes of a raw
copy are the slots as written.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import struct
import wave
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from .i18n import _

SLOT_AT = (0xA0000, 0xB4000, 0xC8000, 0xE7000)      # USR1..USR4 (SLOOP 3 3.4.0 fw/main.c; SLOOP 2.8 eng_sample.c)
SLOT_SIZE = 0x14000                                 # 80 KiB
DATA_OFF = 512
MAGIC = 0x504D5346                                  # "FSMP"
MAX_ZONES = 16
FLASH_SIZE = 0x100000                               # both FM-1 flash chips hold 1 MiB (Puya P25Q80H, UC25HQ80)
RATE_BASE = 44100                                   # a zone's rate is its step at 44.1 kHz, 16.16 (files.js rate16)
SIDE_FORMAT = "fm1-flash-backup"
IMA_STEP = (7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45, 50, 55, 60, 66, 73, 80, 88,
            97, 107, 118, 130, 143, 157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658,
            724, 796, 876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327, 3660,
            4026, 4428, 4871, 5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487, 12635, 13899, 15289, 16818,
            18500, 20350, 22385, 24623, 27086, 29794, 32767)
IMA_IDX = (-1, -1, -1, -1, 2, 4, 6, 8)


class FlashDumpError(Exception):
    """a ready (translated) sentence"""


# ------------------------------------------------------------- the slots ---

@dataclass
class Zone:
    off: int
    n: int                    # samples
    ls: int
    le: int
    rate: int                 # 16.16 step at 44.1 kHz
    root16: int
    pred: int
    idx: int
    lo: int
    hi: int
    looped: int

    @property
    def sample_rate(self) -> int:
        return max(1, round(self.rate * RATE_BASE / 65536))

    @property
    def root(self) -> int:
        return max(0, min(127, round(self.root16 / 16)))


@dataclass
class Slot:
    index: int                # 0..3 (USR1..USR4)
    address: int
    name: str = ""
    zones: list[Zone] = field(default_factory=list)
    data: bytes = b""
    problem: str = ""         # not empty: no usable sample there (why)
    crc_ok: bool = False

    @property
    def label(self) -> str:
        return f"USR{self.index + 1}"

    @property
    def used(self) -> bool:
        return bool(self.zones) and not self.problem


def parse_slot(image: bytes, k: int) -> Slot:
    """slot USRk+1 of a flash image, checked as SLOOP 3 checks it before it plays one (engine/sample.c
    smp_user_head_ok), plus the data's CRC-32 from the header"""
    at = SLOT_AT[k]
    s = Slot(k, at)
    head = image[at:at + DATA_OFF]
    if len(head) < DATA_OFF:
        s.problem = _("the flash copy is too short for this slot")
        return s
    if all(b == 0xFF for b in head):
        s.problem = _("empty (erased)")
        return s
    magic, ver, nz = struct.unpack_from("<IHB", head, 0)
    length, crc = struct.unpack_from("<II", head, 16)
    if magic != MAGIC or ver != 1:
        s.problem = _("no sample header there")
        return s
    if not 1 <= nz <= MAX_ZONES or length > SLOT_SIZE - DATA_OFF:
        s.problem = _("the sample header is not valid")
        return s
    s.name = "".join(chr(b) for b in head[8:16] if 32 <= b < 127).strip()
    zones = []
    for i in range(nz):
        z = Zone(*struct.unpack_from("<5I2h4B", head, 32 + 28 * i))
        if z.off > length or z.n > 2 * SLOT_SIZE or z.off + (z.n + 1) // 2 > length or z.idx > 88 or not z.rate or \
                z.rate > 4 << 16 or z.ls > z.le or z.le >= z.n or z.lo > z.hi:
            s.problem = _("the sample header is not valid")
            return s
        zones.append(z)
    s.zones = zones
    s.data = bytes(image[at + DATA_OFF:at + DATA_OFF + length])
    s.crc_ok = len(s.data) == length and zlib.crc32(s.data) & 0xFFFFFFFF == crc
    if not s.crc_ok:
        s.problem = _("the sample data does not match its checksum (damaged or half written)")
    return s


def slots(image: bytes) -> list[Slot]:
    return [parse_slot(image, k) for k in range(len(SLOT_AT))]


def ima_decode(data: bytes, off: int, n: int) -> list[int]:
    """n samples of IMA ADPCM from byte off (low nibble first), from predictor 0 / index 0 – as SLOOP 3 starts every
    zone (engine/sample.c smp_start / next)"""
    pred, idx, out = 0, 0, []
    for pos in range(n):
        b = data[off + (pos >> 1)]
        code = b >> 4 if pos & 1 else b & 15
        step = IMA_STEP[idx]
        vd = step >> 3
        if code & 4:
            vd += step
        if code & 2:
            vd += step >> 1
        if code & 1:
            vd += step >> 2
        pred = pred - vd if code & 8 else pred + vd
        pred = -32768 if pred < -32768 else 32767 if pred > 32767 else pred
        idx += IMA_IDX[code & 7]
        idx = 0 if idx < 0 else 88 if idx > 88 else idx
        out.append(pred)
    return out


def _wav(path: Path, rate: int, pcm: list[int], root: int, loop: tuple[int, int] | None):
    """16-bit mono WAV, with a "smpl" chunk (root note, the loop if the zone loops)"""
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack("<%dh" % len(pcm), *pcm))
    loops = [loop] if loop else []
    smpl = struct.pack("<9I", 0, 0, round(1e9 / rate), root, 0, 0, 0, len(loops), 0)
    for a, b in loops:
        smpl += struct.pack("<6I", 0, 0, a, b, 0, 0)
    chunk = b"smpl" + struct.pack("<I", len(smpl)) + smpl
    raw = bytearray(path.read_bytes())
    raw += chunk
    struct.pack_into("<I", raw, 4, len(raw) - 8)
    path.write_bytes(bytes(raw))


def _safe(name: str) -> str:
    return re.sub(r"[^\w\- ]+", "_", name).strip(" _") or "SAMPLE"


@dataclass
class Extracted:
    files: list[Path]
    slots: list[Slot]
    folder: Path


def extract(image: bytes, folder: Path, prefix: str = "SLOOP 3") -> Extracted:
    """every zone of every usable slot as a WAV file in folder ("SLOOP 3 USR1 BREAK 03.wav"; a slot with one zone:
    "SLOOP 3 USR1 BREAK.wav"). Existing files are not overwritten (a number is added). -> Extracted"""
    check_image(image)
    folder = Path(folder)
    found = slots(image)
    files = []
    for s in found:
        if not s.used:
            continue
        folder.mkdir(parents=True, exist_ok=True)
        for i, z in enumerate(s.zones):
            pcm = ima_decode(s.data, z.off, z.n)
            stem = f"{prefix} {s.label} {_safe(s.name)}" + (f" {i + 1:02d}" if len(s.zones) > 1 else "")
            p, n = folder / f"{stem}.wav", 2
            while p.exists():
                p = folder / f"{stem} ({n}).wav"
                n += 1
            _wav(p, z.sample_rate, pcm, z.root, (z.ls, z.le) if z.looped else None)
            files.append(p)
    return Extracted(files, found, folder)


def check_image(image: bytes):
    """a raw image of the FM-1's whole flash? FlashDumpError if it cannot be one"""
    if len(image) < SLOT_AT[-1] + SLOT_SIZE:
        raise FlashDumpError(_("This file is too small to be a copy of the FM-1's whole flash ({size} bytes; a copy "
                               "has {want}).").format(size=len(image), want=FLASH_SIZE))
    if len(image) != FLASH_SIZE:
        raise FlashDumpError(_("This file is not a copy of the FM-1's whole flash: it has {size} bytes, a copy has "
                               "exactly {want}.").format(size=len(image), want=FLASH_SIZE))


def read_image(path: Path) -> bytes:
    """a flash copy from disk (the app's or one M-Flasher saved): the raw 1 MiB image"""
    p = Path(path)
    try:
        if p.stat().st_size > 4 * FLASH_SIZE:
            raise FlashDumpError(_("This file is not a copy of the FM-1's whole flash: it has {size} bytes, a copy "
                                   "has exactly {want}.").format(size=p.stat().st_size, want=FLASH_SIZE))
        raw = p.read_bytes()
    except OSError as e:
        raise FlashDumpError(_("The file cannot be read: {error}").format(error=e.strerror or e))
    check_image(raw)
    return raw


# --------------------------------------------------------- the listing ---

@dataclass
class FlashBackup:
    path: Path                # the .bin
    size: int
    mtime: float
    created: str = ""
    device: str = ""
    chip: str = ""
    jedec: str = ""
    read_through: str = ""
    crc32: str = ""
    ok: bool | None = None    # True: the file matches its description (and the chip's CRC-32 kept there);
    #                           False: it does not; None: not checked (no description)
    problem: str = ""

    @property
    def when(self) -> dt.datetime:
        try:
            t = dt.datetime.fromisoformat(self.created)
            return t if t.tzinfo else t.astimezone()
        except (TypeError, ValueError):
            return dt.datetime.fromtimestamp(self.mtime).astimezone()


def describe(path: Path, verify: bool = True) -> FlashBackup:
    """one flash backup (.bin) with its description beside it; verify: compare the file with it (reads 1 MiB)"""
    p = Path(path)
    st = p.stat()
    fb = FlashBackup(p, st.st_size, st.st_mtime)
    try:
        side = json.loads(p.with_suffix(".json").read_text("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        side = None
    if not isinstance(side, dict) or side.get("format") != SIDE_FORMAT:
        fb.problem = _("no description beside it – not checked")
        return fb
    chip = side.get("flash_chip") if isinstance(side.get("flash_chip"), dict) else {}
    fb.created, fb.device = str(side.get("created") or ""), str(side.get("device") or "")
    fb.chip, fb.jedec = str(chip.get("name") or ""), str(chip.get("jedec") or "")
    fb.read_through, fb.crc32 = str(side.get("read_through") or ""), str(side.get("crc32") or "")
    try:
        key = (str(p), st.st_size, st.st_mtime_ns, p.with_suffix(".json").stat().st_mtime_ns)
    except OSError:
        key = None
    if key is not None and key in _CHECKED:                # (the same file, unchanged since: not read again)
        fb.ok, fb.problem = _CHECKED[key]
        return fb
    if not verify:
        return fb
    try:
        raw = p.read_bytes()
    except OSError as e:
        fb.ok, fb.problem = False, str(e.strerror or e)
        return fb
    crc = f"{zlib.crc32(raw) & 0xFFFFFFFF:08x}"
    if side.get("size") != len(raw) or side.get("sha256") != hashlib.sha256(raw).hexdigest() or \
            (fb.crc32 and fb.crc32.lower() != crc):
        fb.ok, fb.problem = False, _("the file does not match its description (changed or damaged)")
    else:
        fb.ok = True
    if key is not None:
        _CHECKED[key] = (fb.ok, fb.problem)
    return fb


_CHECKED: dict = {}          # (path, size, mtime, description's mtime) -> (ok, problem): a verified file is not hashed again


def list_backups(folder: Path, verify: bool = True) -> list[FlashBackup]:
    """the flash backups under folder (every …/Flash/*.bin with or without a description), newest first"""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    out = []
    for p in folder.rglob("*.bin"):
        if p.parent.name != "Flash" or p.name.startswith("."):
            continue
        try:
            out.append(describe(p, verify))
        except OSError:
            continue
    out.sort(key=lambda b: b.when, reverse=True)
    return out
