# SPDX-License-Identifier: GPL-3.0-only
# Protocol and package format: after sdk/WebUpdate.js of M-VAVE's FM1 Open (github.com/m-vave/fm1_open, commit
#   4c7496daac04a95ff10d1afdcc8f9ead2dbabc41; GPL-3.0-only WITH GPL-3.0-linking-exception, (c) 2026 M-VAVE):
#   inspectPackage (the USR voice entry of the V15 package), factoryCheck, frame/parse, Updater.restoreFactory.
"""The voice library of M-VAVE's original firmware V15 (identity FM-1_015).

The original firmware has no backup protocol: nothing of it can be read back over USB except its identity (and,
per community reports, the current edit buffer). What M-VAVE's own workbench does with it, and so what this module
does exactly as its sdk/WebUpdate.js does:

* **Restore the 128 factory voices** ("恢复原厂音色"): the four banks of 32 DX7 voices (16 KiB, the DX7 VMEM
  format) out of the hash-pinned V15 package FM-1_015.fwsc (its USR entry, decoded and checked by CRC-16 and by
  SHA-256 ecad0b86…), written with the V15 system commands of the update protocol (frame ``F0 <7-bit stream of 00 59
  cmd len24 body ~sum> F7``): per bank 33 (``05 00 bank·16 00 00``, prepares the 4 KiB bank), then eight times 34
  (``05 <offset, 32 bit> 00 02 00`` + 512 bytes); each answered by command 0 with status 0. The identity (17) is checked
  before and after: only an FM-1 that answers ``FM-1_015``.
* Nothing else: the current voices cannot be read back first (V15 reads give the edit buffer, not the bank:
  WebUpdate.js), so the restore overwrites all 128 voices without a backup. Writing other voices into a bank with the
  same commands is not offered: M-VAVE's code never does it and it needs a hardware test (docs/ROADMAP.md).

V16 (FM-1_016) is refused: M-VAVE's workbench restores voices only on V15, and V16's layout differs.
"""
from __future__ import annotations

import hashlib
import queue
import time

from .i18n import _

STOCK_V15 = "FM-1_015"
PACKAGE_SHA256 = "db1642b2b6fa5c2cccb11ffd13878068bb28601678d3644049f99dc40e7edb8a"     # FM-1_015.fwsc
FACTORY_SHA256 = "ecad0b8620f9d784d46c8da1e3c5de02c40214649ed8dec218026431046aad25"     # its 128 voices (16 KiB)
BANK = 4096
BANKS = 4
CHUNK = 512
CMD_ACK, CMD_PREPARE, CMD_WRITE, CMD_IDENTITY = 0, 33, 34, 17
OBJECT_VOICES = 5


class StockError(Exception):
    pass


# ------------------------------------------------------------------ the package (WebUpdate.js inspectPackage)
_CRC16_T = [0, 0x1021, 0x2042, 0x3063, 0x4084, 0x50a5, 0x60c6, 0x70e7,
            0x8108, 0x9129, 0xa14a, 0xb16b, 0xc18c, 0xd1ad, 0xe1ce, 0xf1ef]


def crc16(data) -> int:
    c = 0
    for b in data:
        c ^= b << 8
        c = ((c << 4) ^ _CRC16_T[c >> 12]) & 0xFFFF
        c = ((c << 4) ^ _CRC16_T[c >> 12]) & 0xFFFF
    return c


def _decode(data, key: int = 0xFFFF) -> bytearray:
    p = bytearray(data)
    k = key
    for i in range(len(p)):
        p[i] ^= k & 255
        k = ((k << 1) ^ (0x1021 if k & 0x8000 else 0)) & 0xFFFF
    return p


def _le(p, at: int, n: int) -> int:
    return int.from_bytes(bytes(p[at:at + n]), "little")


def factory_voices(package: bytes) -> bytes:
    """The 128 factory voices (4 × 4096 bytes VMEM) out of the V15 package -> 16384 bytes. StockError if the package is
    not M-VAVE's V15 (SHA-256) or its voice entry does not check out."""
    if hashlib.sha256(package).hexdigest() != PACKAGE_SHA256:
        raise StockError(_("This is not M-VAVE's original V15 package (checksum)."))
    b = package
    image = bytearray(len(b) - 20)
    for i in range(20):
        image[i * 47:i * 47 + 47] = b[i * 48:i * 48 + 47]
    image[940:] = b[960:]
    head = _decode(image[:64])
    count = _le(head, 8, 2)
    if _le(head, 0, 2) != crc16(head[2:]) or _le(head, 4, 4) != len(image) or not 0 < count <= 12:
        raise StockError(_("The V15 package's header does not check out."))
    table = image[64:64 + count * 80]
    if crc16(table) != _le(head, 2, 2):
        raise StockError(_("The V15 package's table does not check out."))
    for i in range(count):
        e = _decode(table[i * 80:i * 80 + 80])
        etype, offset, size = _le(e, 0, 2), _le(e, 8, 4), _le(e, 12, 4)
        stored, encrypted = _le(e, 16, 4), _le(e, 24, 4)
        name = bytes(e[64:]).split(b"\0", 1)[0].decode("latin-1")
        if etype != 50 or name != "USR":
            continue
        if size != 73226 or encrypted != 73248:
            break
        out = bytearray(stored)
        for at in range(0, stored, 32):
            out[at:at + 32] = _decode(image[offset + at:offset + at + 32], 0x980F ^ ((offset + at) >> 2))
        if crc16(out[:size]) != _le(e, 4, 2):
            break
        voices = bytes(out[:BANK * BANKS])
        if hashlib.sha256(voices).hexdigest() != FACTORY_SHA256:
            break
        return voices
    raise StockError(_("The V15 package's factory voices do not check out."))


# ------------------------------------------------------------------ the frame (WebUpdate.js frame / parse)
def pack7(data) -> list[int]:
    out, bits, value = [], 0, 0
    for b in data:
        value |= (b & 255) << bits
        bits += 8
        while bits >= 7:
            out.append(value & 127)
            value >>= 7
            bits -= 7
    if bits:
        out.append(value & 127)
    return out


def frame(command: int, payload=b"") -> list[int]:
    payload = bytes(payload)
    if len(payload) > 520:
        raise StockError(_("protocol data too long"))
    s = sum(payload) & 255
    return [0xF0, *pack7(bytes([0, 89, command, len(payload) & 255, len(payload) >> 8, 0]) + payload +
                         bytes([~s & 255])), 0xF7]


def parse(msg) -> tuple[int, bytes] | None:
    m = list(msg)
    if len(m) < 10 or len(m) > 608 or m[0] != 0xF0 or m[-1] != 0xF7:
        return None
    raw, bits, value = bytearray(), 0, 0
    for b in m[1:-1]:
        if b > 127:
            return None
        value |= b << bits
        bits += 7
        if bits >= 8:
            raw.append(value & 255)
            value >>= 8
            bits -= 8
    if bits >= 7 or value or len(raw) < 7 or raw[0] != 0 or raw[1] != 89 or _le(raw, 3, 3) != len(raw) - 7:
        return None
    if sum(raw[6:]) & 255 != 255:
        return None
    return raw[2], bytes(raw[6:-1])


# ------------------------------------------------------------------ the device (Updater.restoreFactory)
class Client:
    """The V15 system commands over a link (transport.Link). From a background thread, link reserved (ctx.job)."""

    def __init__(self, link, timeout: float = 10.0):
        self.link = link
        self.timeout = timeout
        self.q: queue.Queue = queue.Queue()

    def __enter__(self):
        self.link.add_listener(self._listen)
        return self

    def __exit__(self, *a):
        self.link.remove_listener(self._listen)

    def _listen(self, data, direction):
        if direction == "in" and data and data[0] == 0xF0:
            p = parse(data)
            if p:
                self.q.put(p)

    def _send(self, msg):
        if not self.link.send_midi(list(msg), force=True):
            raise StockError(_("Device not connected"))

    def _wait(self, command: int, timeout: float):
        end = time.monotonic() + timeout
        while True:
            left = end - time.monotonic()
            if left <= 0:
                return None
            try:
                cmd, data = self.q.get(timeout=left)
            except queue.Empty:
                return None
            if cmd == command:
                return data

    def _drain(self):
        while not self.q.empty():
            self.q.get_nowait()

    def identity(self, timeout: float = 1.5) -> str:
        self._drain()
        self._send(frame(CMD_IDENTITY))
        d = self._wait(CMD_IDENTITY, timeout)
        if d is None or len(d) != 27:
            return ""
        return d.split(b"\0", 1)[0].decode("latin-1")

    def acknowledge(self, command: int, payload: bytes):
        self._drain()
        self._send(frame(command, payload))
        d = self._wait(CMD_ACK, self.timeout)
        if d is None:
            raise StockError(_("The FM-1 did not confirm – the voices are not written completely. Connect the original "
                               "V15 again and repeat."))
        if d != b"\0":
            raise StockError(_("The FM-1 refused to write the voices."))

    def write_banks(self, data: bytes, first_bank: int = 0, progress=None):
        """data: whole banks (4096 bytes each) from bank first_bank (0 = A) on. Checks the identity before and after."""
        if not data or len(data) % BANK or not 0 <= first_bank or first_bank + len(data) // BANK > BANKS:
            raise StockError(_("invalid voice data"))
        if self.identity() != STOCK_V15:
            raise StockError(_("Only for the original firmware V15 (identity FM-1_015) – nothing was written."))
        total = len(data)
        for k in range(len(data) // BANK):
            bank = (first_bank + k) * BANK
            self.acknowledge(CMD_PREPARE, bytes([OBJECT_VOICES, 0, bank >> 8, 0, 0]))
            for off in range(0, BANK, CHUNK):
                at = bank + off
                # object, offset (32 bit), length (24 bit: 512), the bytes – as WebUpdate.js sends them
                self.acknowledge(CMD_WRITE, bytes([OBJECT_VOICES, at & 255, at >> 8, 0, 0, 0, 2, 0])
                                 + data[k * BANK + off:k * BANK + off + CHUNK])
                if progress:
                    progress(k * BANK + off + CHUNK, total)
        if self.identity() != STOCK_V15:
            raise StockError(_("The FM-1 no longer answers as V15 after writing – please check it."))


def restore_factory(link, package: bytes, progress=None):
    """M-VAVE's "restore factory voices" on an FM-1 with the original V15: the 128 voices of the V15 package."""
    voices = factory_voices(package)
    with Client(link) as c:
        c.write_banks(voices, 0, progress)
