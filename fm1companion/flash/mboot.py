# SPDX-License-Identifier: GPL-3.0-only
"""m-bootloader's MIDI MODE (jvitkauskas/m-bootloader, MIT; docs/SPEC.md §11.6) – the READ-ONLY part.

m-bootloader is an open-source bootloader for the FM-1. Its recovery menu (hold PLAY at power-on) has a MIDI MODE in
which the FM-1 is a USB-MIDI device "FM-1 Update" (1209:0002) that answers the update handshake as ``ota-FM-1_900`` –
exactly like a Felucca-family update loader. Only one question tells them apart: command 0x12 ("who is it?"), which
m-bootloader answers with ``MBOOT``, its version, the chip key, the flash chip's JEDEC ID and its unique ID.

This module speaks only what reads:

* 0x12  identify
* 0x23  read up to 512 bytes of raw flash
* 0x24  the CRC-32 the chip computes of a range

and never builds a command number from data: the specification warns that M-VAVE's own update service uses the same
frame and that its 0x21 (one bit away from 0x23) erases a flash sector without any check. So

* nothing is read before 0x12 was answered with ``MBOOT`` (``Host.identify``; ``read``/``crc`` refuse otherwise),
* the three command bytes are the literals below, and ``_frame`` refuses every other command.

Nothing here writes, erases, restarts the unit or starts an install. Installing m-bootloader itself, and installing a
firmware through it, are not done by this module (see docs/firmware-support.md, “SLOOP 3”).

Frame: F0, the bytes 8-to-7 packed (LSB first), F7. Unpacked: 00 59, the command, the body's length (24 bit), the body,
the complement of the body's byte sum.

Not bundled, not copied: this is written from the specification; none of m-bootloader's code is in the app.
"""
from __future__ import annotations

import hashlib
import json
import queue
import re
import time
import zlib
from dataclasses import dataclass
from pathlib import Path

from ..i18n import _

CMD_IDENTIFY = 0x12
CMD_READ = 0x23
CMD_CRC = 0x24
_ALLOWED = (CMD_IDENTIFY, CMD_READ, CMD_CRC)
READ_MAX = 512
SECTOR = 0x1000
NAME_RE = re.compile(r"^FM-1 Update\b", re.I)      # the USB name of MIDI MODE (and of nothing else the app knows)
IDENTITY = "ota-FM-1_900"                          # its answer to the update handshake (from m-bootloader 2.3)
JEDEC_OLD, JEDEC_NEW = 0x856014, 0xB36014          # Puya P25Q80H · UCUN UC25HQ80 (FM-1s sold from late 2026)
JEDEC_NAMES = {JEDEC_OLD: "Puya P25Q80H", JEDEC_NEW: "UCUN UC25HQ80"}
BACKUP_FORMAT = "fm1-flash-backup"                 # the sidecar's format name (the .bin itself is the raw flash)


class MbootError(Exception):
    """a ready (translated) sentence"""


def _pack(raw: bytes) -> list[int]:
    out, acc, bits = [], 0, 0
    for b in raw:
        acc |= b << bits
        bits += 8
        while bits >= 7:
            out.append(acc & 0x7F)
            acc >>= 7
            bits -= 7
    if bits:
        out.append(acc & 0x7F)
    return out


def _unpack(data) -> bytes:
    out, acc, bits = bytearray(), 0, 0
    for b in data:
        acc |= (b & 0x7F) << bits
        bits += 7
        if bits >= 8:
            out.append(acc & 0xFF)
            acc >>= 8
            bits -= 8
    return bytes(out)


def _frame(cmd: int, body: bytes = b"") -> list[int]:
    if cmd not in _ALLOWED:                         # (never a command this module does not name: 0x21 / 0x22 write)
        raise MbootError("command not allowed")
    n = len(body)
    raw = bytes([0, 0x59, cmd, n & 255, n >> 8 & 255, n >> 16 & 255]) + body + bytes([~sum(body) & 255])
    return [0xF0, *_pack(raw), 0xF7]


def _parse(data) -> tuple[int, bytes] | None:
    """a received message -> (command, body), None if it is no frame of this protocol or its sum is wrong"""
    d = list(data)
    if len(d) < 3 or d[0] != 0xF0 or d[-1] != 0xF7:
        return None
    m = _unpack(d[1:-1])
    if len(m) < 7 or m[0] != 0 or m[1] != 0x59:
        return None
    n = m[3] | m[4] << 8 | m[5] << 16
    if len(m) < 7 + n:
        return None
    body = m[6:6 + n]
    if (sum(body) + m[6 + n]) & 255 != 255:
        return None
    return m[2], bytes(body)


@dataclass
class Info:
    version: str                # "2.4-395e6cc"
    kind: int                   # 1 the bootloader's MIDI MODE, 2 the RAM stage
    key: int                    # the chip key (needed to build a package for this unit; never shown as a secret)
    capacity: int               # flash size in bytes
    jedec: int                  # the flash chip's JEDEC ID
    uid: str                    # the flash chip's unique ID, hex
    proto: int = 1

    @property
    def revision(self) -> str:
        """"new" (UCUN UC25HQ80), "old" (Puya P25Q80H) or "unknown" – read from the chip, not guessed"""
        from .. import hardware
        return hardware.revision_of_id(self.jedec & 0xFFFFFF)

    @property
    def chip(self) -> str:
        return JEDEC_NAMES.get(self.jedec & 0xFFFFFF, "") or f"0x{self.jedec & 0xFFFFFF:06X}"


def parse_info(body: bytes) -> Info | None:
    if len(body) < 29 or body[:5] != b"MBOOT":
        return None
    ver = body[28:].split(b"\0")[0].decode("ascii", "replace")
    cap = int.from_bytes(body[10:14], "little")
    if body[5] != 1 or not 0x10000 <= cap <= 0x1000000 or cap % SECTOR:
        return None
    return Info(version=ver, kind=body[6], key=body[8] | body[9] << 8, capacity=cap,
                jedec=int.from_bytes(body[14:18], "little"), uid=body[18:26].hex(), proto=body[5])


class Host:
    """the host's side over a transport (transport.Transport: ``send(list)``, ``receiver``) the caller opened on the
    "FM-1 Update" port. One request at a time."""

    def __init__(self, transport, timeout: float = 2.0):
        self.t = transport
        self.timeout = timeout
        self.info: Info | None = None
        self._q: queue.Queue = queue.Queue()
        self.t.receiver = self._rx

    def _rx(self, data):
        if data and data[0] == 0xF0:
            self._q.put(list(data))

    def _ask(self, cmd: int, body: bytes = b"", timeout: float | None = None) -> bytes:
        while not self._q.empty():
            self._q.get_nowait()
        self.t.send(_frame(cmd, body))
        end = time.monotonic() + (timeout or self.timeout)
        while True:
            try:
                f = _parse(self._q.get(timeout=max(0.01, end - time.monotonic())))
            except queue.Empty:
                raise MbootError(_("The FM-1 does not answer. Is it still in MIDI MODE and connected?"))
            if f is None:
                continue
            if f[0] == 0:
                raise MbootError(_("The FM-1 refused the request."))
            if f[0] == cmd:
                return f[1]

    def identify(self, tries: int = 2, timeout: float = 0.8) -> Info | None:
        """ask who is there (0x12). -> Info for m-bootloader, None for anything else (no answer, another answer).
        Never raises."""
        self.info = None
        for _try in range(tries):
            try:
                self.info = parse_info(self._ask(CMD_IDENTIFY, timeout=timeout))
                return self.info
            except MbootError:
                continue
            except Exception:  # noqa: BLE001 – port gone
                return None
        return None

    def _need(self) -> Info:
        if self.info is None:
            raise MbootError(_("This is not m-bootloader's MIDI MODE – nothing was read."))
        return self.info

    def read(self, address: int, count: int) -> bytes:
        info = self._need()
        if not 0 < count <= READ_MAX or address < 0 or address + count > info.capacity:
            raise MbootError("range")
        head = bytes([0]) + address.to_bytes(4, "little") + count.to_bytes(3, "little")
        b = self._ask(CMD_READ, head)
        if len(b) != 8 + count or b[:8] != head:
            raise MbootError(_("unexpected answer while reading"))
        return b[8:]

    def crc(self, address: int, count: int) -> int:
        """the CRC-32 the chip itself computes of the range (takes a moment for the whole flash)"""
        info = self._need()
        if count <= 0 or address < 0 or address + count > info.capacity:
            raise MbootError("range")
        head = bytes([0]) + address.to_bytes(4, "little") + count.to_bytes(3, "little")
        b = self._ask(CMD_CRC, head, timeout=max(self.timeout, 5.0 + count / 100_000))
        if len(b) != 12 or b[:8] != head:
            raise MbootError(_("unexpected answer while reading"))
        return int.from_bytes(b[8:12], "little")

    def read_flash(self, progress=None, abort=None) -> bytes:
        """the whole flash (boot area and key area included), checked against the CRC-32 the chip itself computes
        of the whole flash (one more request): a mismatch is an error, never a file."""
        info = self._need()
        out = bytearray()
        while len(out) < info.capacity:
            if abort and abort():
                raise MbootError(_("Cancelled – no file was saved, nothing was changed on the FM-1."))
            n = min(READ_MAX, info.capacity - len(out))
            last = None
            for _try in range(3):
                try:
                    part = self.read(len(out), n)
                    break
                except MbootError as e:
                    last = e
            else:
                raise last
            out += part
            if progress:
                progress(len(out), info.capacity)
        want = self.crc(0, info.capacity)
        if zlib.crc32(bytes(out)) & 0xFFFFFFFF != want:
            raise MbootError(_("The copy does not match what the FM-1's flash chip says about itself (checksum). "
                               "No file was saved. Please try again, with the cable directly at the computer."))
        return bytes(out)


def is_candidate(label: str, usb_id: str = "") -> bool:
    """may the app ask this update-mode port who it is (0x12)? Only the port MIDI MODE shows: USB name "FM-1 Update"
    (Felucca-family update loaders are "<Name> Update": "Melodee FM-1 Update", "Felucca Update" – they are not
    asked) and, where the system tells the USB ID, 1209:0002."""
    if usb_id and usb_id.lower() != "1209:0002":
        return False
    return bool(NAME_RE.search((label or "").strip()))


def open_port(port):
    """the MIDI port of a devices.PortInfo as a transport (the caller closes it)"""
    from ..transport import MidoTransport
    return MidoTransport(port.in_name, port.out_name)


def identify_port(port, opener=None) -> Info | None:
    """open the port, ask 0x12, close. Never raises."""
    try:
        t = (opener or open_port)(port)
    except Exception:  # noqa: BLE001
        return None
    try:
        return Host(t).identify()
    finally:
        try:
            t.close()
        except Exception:  # noqa: BLE001
            pass


# ------------------------------------------------------------ the backup ---

def backup_name(device: str, when: time.struct_time | None = None) -> str:
    from ..devices import safe_folder_name
    stamp = time.strftime("%Y-%m-%d_%H%M%S", when or time.localtime())
    return f"{safe_folder_name(device or 'FM-1')}_flash_{stamp}.bin"


def save_backup(folder: Path, device: str, info: Info, image: bytes, note: str = "") -> Path:
    """write the flash image and a small description beside it (what it is, its SHA-256, the chip) – the .bin first,
    complete or not at all; it is read back and compared before the description is written."""
    from ..backup import write_text_atomic
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / backup_name(device)
    n = 2
    while path.exists():
        path = path.with_name(f"{path.stem} ({n}).bin")
        n += 1
    tmp = path.with_name("." + path.name + ".tmp")
    try:
        with open(tmp, "wb") as f:
            f.write(image)
            f.flush()
            import os
            os.fsync(f.fileno())
        tmp.replace(path)
    finally:
        if tmp.exists():
            tmp.unlink()
    if path.read_bytes() != image:
        path.unlink()
        raise MbootError(_("The file could not be written correctly – nothing was kept."))
    side = {"format": BACKUP_FORMAT, "version": 1, "file": path.name, "size": len(image),
            "sha256": hashlib.sha256(image).hexdigest(), "crc32": f"{zlib.crc32(image) & 0xFFFFFFFF:08x}",
            "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "device": device,
            "read_through": f"m-bootloader {info.version} (MIDI MODE)" + (" – RAM stage" if info.kind == 2 else ""),
            "flash_chip": {"jedec": f"0x{info.jedec & 0xFFFFFF:06X}", "name": JEDEC_NAMES.get(info.jedec & 0xFFFFFF, ""),
                           "unique_id": info.uid, "capacity": info.capacity},
            "restore": "With M-Flasher (https://github.com/jvitkauskas/m-flasher) in USB download mode: write this "
                       "file back whole. It is this one FM-1's flash: do not write it to another unit.",
            "note": note, "app": "FM-1 Companion"}
    write_text_atomic(path.with_suffix(".json"), json.dumps(side, indent=2, ensure_ascii=False))
    return path


def check_backup(path: Path) -> dict:
    """a saved flash backup against its description -> the description. MbootError: missing or changed."""
    path = Path(path)
    try:
        side = json.loads(path.with_suffix(".json").read_text("utf-8"))
        raw = path.read_bytes()
    except (OSError, ValueError):
        raise MbootError(_("The flash backup or its description cannot be read."))
    if not isinstance(side, dict) or side.get("format") != BACKUP_FORMAT or side.get("size") != len(raw) or \
            side.get("sha256") != hashlib.sha256(raw).hexdigest():
        raise MbootError(_("The flash backup does not match its description (changed or damaged)."))
    return side
