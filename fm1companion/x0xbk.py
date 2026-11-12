# SPDX-License-Identifier: GPL-3.0-only
"""X0X's own backup and restore over USB (charlesvestal/fm1-x0x firmware/src/backup.c, tools/x0x_backup.py), and
fm1-base's, which is the same protocol and image with an app's own magic and objects (fm1-base device/backup.c,
tools/fm1_backup.py; LOOM-8: "LM8B", its user presets and its three projects). Both come from Polly's.

They do not speak Felucca's backup commands (65–67) or SLOOP's (34–36): the saved state is one image, sent and taken
whole, in the update's SysEx framing (ota.c: F0 pack7(00 59 cmd len24 body ~sum(body)) F7, the bits LSB first):

* backup (cmd 0x42, body "X0XB"): the device streams the image in frames (cmd 0x42: u32 offset + data), then the end
  frame (cmd 0x43: u32 length, u32 CRC-32, u8 status, u8 flags; flag 1 = changes not saved yet left out: it played)
* restore (cmd 0x41, body u32 length, u32 CRC-32): refused while playing. The device pulls the image with read
  requests (cmd 0x30, the image's offsets), checks all of it, asks on its screen (RESTORE FROM USB? SEL yes, HOME no)
  and only then commits; it reports with a read of 0xC0000000 (done) or 0xCF000000 | code
* the image (X0X's ".x0b", little-endian): the magic ("X0XB"), u16 version 1, u16 count (X0X: 6), u32 flags, u32
  CRC-32 of those 12 bytes; the records in the firmware's object order (X0X: 0 1 2 3 5 6; u16 id, u16 length, u32
  CRC-32, the payload; length 0 = absent); u32 CRC-32 of everything before. Not in it: the panel's calibration (X0X's
  4), the break loops in X0X's sample slots, LOOM-8's sample bank.

The suite keeps such a backup as JSON (format "x0x-backup" or "loom8-backup": the image in Base64, the firmware's
name and version, the app's companion block) and reads the firmwares' own image files (X0X's .x0b, LOOM-8's .loom8)
too. The studio twin gets the image on its side of the cable first (cmd 0x7E, the twin's own frame, see
tools/studio/x0x_studio.c and fm1base_studio.c): a twin cannot wait for its host inside a call.
"""
from __future__ import annotations

import base64
import datetime as dt
import queue
import struct
import zlib
from typing import Callable

from dataclasses import dataclass

from .i18n import N_, _

FORMAT = "x0x-backup"
MAGIC, VERSION, NOBJ, HEAD, REC, PAYLOAD_MAX = b"X0XB", 1, 6, 16, 8, 3840
OBJ_IDS = [0, 1, 2, 3, 5, 6]          # plat.h OBJ_SET, OBJ_SOUND, OBJ_PAT0, OBJ_PAT1, OBJ_SONG, OBJ_MOTION
RS_REQ, BU_REQ, BU_END, READ, STAGE = 0x41, 0x42, 0x43, 0x30, 0x7E
OK_ADDR, ASK_ADDR, FAIL_ADDR = 0xC0000000, 0xC1000000, 0xCF000000
PLAYING, DECLINED = 10, 11
ERRORS = {1: N_("the file's size"), 2: N_("the transfer (a read request unanswered)"), 3: N_("the file's head"),
          4: N_("a part's header"), 5: N_("a part's checksum (a byte changed on the way)"),
          6: N_("the whole file's checksum"), 7: N_("a flash erase"), 8: N_("a flash write"),
          9: N_("the read-back (the flash does not hold what was written)"),
          10: N_("the FM-1 is playing: stop it first"), 11: N_("answered HOME (no) on the FM-1"),
          12: N_("writing the headers (the FM-1 finishes it when switched on again)")}
TITLES = {0: N_("Settings (tempo, MIDI, the options)"), 1: N_("Sound (every part's knobs, the effects)"),
          2: N_("Patterns (first part)"), 3: N_("Patterns (second part)"), 5: N_("Song"),
          6: N_("Knob motion")}


@dataclass(frozen=True)
class Profile:
    """a firmware's image: its key (studio.FIRMWARES), name, magic, version, objects in order, the suite's format"""
    key: str
    name: str
    magic: bytes
    version: int
    ids: tuple
    format: str
    ext: str

    def title(self, oid: int) -> str:
        if self.key == "x0x":
            return _(TITLES[oid]) if oid in TITLES else _("object {id}").format(id=oid)
        if self.key == "loom-8":                    # (app.h: OBJ_PRESETS 1, then 3 projects of 7 objects from 2)
            if oid == 1:
                return _("User presets")
            if 2 <= oid < 23:
                return _("Project {n} (part {k} of 7)").format(n=(oid - 2) // 7 + 1, k=(oid - 2) % 7 + 1)
        return _("object {id}").format(id=oid)

    def settings(self, oid: int) -> bool:
        return self.key == "x0x" and oid == 0


X0X_PROFILE = Profile("x0x", "X0X", MAGIC, VERSION, tuple(OBJ_IDS), FORMAT, "x0b")
# LOOM-8 (charlesvestal/fm1-loom, fm1-base's backup.c): app.h APP_BACKUP_MAGIC 0x42384D4C "LM8B", APP_BACKUP_VERSION 1,
# APP_BACKUP_OBJS OBJ_PRESETS (1) and the three projects' 21 objects (OBJ_PROJ0 2 .. 22); fm1.toml backup_ext "loom8"
LOOM_PROFILE = Profile("loom-8", "LOOM-8", b"LM8B", 1, (1, *range(2, 23)), "loom8-backup", "loom8")
PROFILES = {p.key: p for p in (X0X_PROFILE, LOOM_PROFILE)}
FORMATS = {p.format: p for p in PROFILES.values()}


def profile_of(x) -> Profile:
    """a firmware key, a suite format, an image (by its magic) or a file dict -> its profile (X0X's when unknown)"""
    if isinstance(x, dict):
        return FORMATS.get(x.get("format", ""), X0X_PROFILE)
    if isinstance(x, (bytes, bytearray)):
        return next((p for p in PROFILES.values() if bytes(x[:4]) == p.magic), X0X_PROFILE)
    return PROFILES.get(x, FORMATS.get(x, X0X_PROFILE))


def _err(msg: str):
    from .backup import BackupError
    return BackupError(msg)


def title(oid: int, profile: Profile = X0X_PROFILE) -> str:
    return profile.title(oid)


def is_x0x(text: str) -> bool:
    """an INFO string / firmware field of X0X ("X0X 1.2.1 BETA")"""
    return "x0x" in (text or "").lower()


# ------------------------------------------------------------- the image ---
def parse(image: bytes, profile: Profile | None = None) -> tuple[int, list[tuple[int, bytes]]]:
    """check a backup image whole (as backup.c and tools/x0x_backup.py do) -> (flags, [(object, payload)]); the
    profile by the image's magic unless given"""
    image = bytes(image)
    p = profile or profile_of(image)
    fw = p.name
    if image[:4] == b"PLYB":
        raise _err(_("This is a Polly backup, not {firmware}'s.").format(firmware=fw))
    n_ids = len(p.ids)
    if len(image) < HEAD + n_ids * REC + 4:
        raise _err(_("Too short for a {firmware} backup.").format(firmware=fw))
    magic, ver, n, flags, hcrc = struct.unpack_from("<4sHHII", image)
    if magic != p.magic:
        raise _err(_("Not a {firmware} backup.").format(firmware=fw))
    if ver != p.version or n != n_ids:
        raise _err(_("A backup from another version of {firmware} (version {version}, {n} parts): this {firmware} cannot "
                     "restore it.").format(firmware=fw, version=ver, n=n))
    if hcrc != zlib.crc32(image[:12]):
        raise _err(_("The {firmware} backup's head is damaged.").format(firmware=fw))
    objs, pos = [], HEAD
    for want in p.ids:
        if pos + REC > len(image):
            raise _err(_("The {firmware} backup is cut short.").format(firmware=fw))
        obj, ln, crc = struct.unpack_from("<HHI", image, pos)
        pos += REC
        if obj != want or ln > PAYLOAD_MAX or pos + ln + 4 > len(image):
            raise _err(_("“{part}”: a wrong header in the {firmware} backup.").format(part=p.title(want), firmware=fw))
        data = image[pos:pos + ln]
        if zlib.crc32(data) != crc:
            raise _err(_("“{part}”: damaged (checksum).").format(part=p.title(want)))
        objs.append((obj, data))
        pos += ln
    if pos + 4 != len(image) or struct.unpack_from("<I", image, pos)[0] != zlib.crc32(image[:pos]):
        raise _err(_("The {firmware} backup is damaged (the whole file's checksum).").format(firmware=fw))
    return flags, objs


def build(objs, flags: int = 0, profile: Profile = X0X_PROFILE) -> bytes:
    """an image from [(object, payload)] in the profile's order"""
    objs = list(objs)
    head = profile.magic + struct.pack("<HHI", profile.version, len(objs), flags)
    out = bytearray(head + struct.pack("<I", zlib.crc32(head)))
    for obj, data in objs:
        out += struct.pack("<HHI", obj, len(data), zlib.crc32(data)) + bytes(data)
    out += struct.pack("<I", zlib.crc32(out))
    return bytes(out)


def to_file(image: bytes, firmware: str = "", companion: dict | None = None, created: str = "") -> dict:
    """the suite's JSON file of an image (checked; the format by its magic)"""
    parse(image)
    f = {"format": profile_of(bytes(image)).format, "version": 1, "firmware": firmware,
         "created": created or dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
         "image": base64.b64encode(bytes(image)).decode()}
    if companion:
        f["companion"] = companion
    return f


def image_of(file: dict) -> bytes:
    try:
        return base64.b64decode(file["image"], validate=True)
    except (KeyError, TypeError, ValueError):
        raise _err(_("Backup damaged (Base64)"))


def objects(file: dict) -> tuple[int, list[dict]]:
    """the suite's JSON file -> (flags, objects with id, size, crc, bytes)"""
    flags, objs = parse(image_of(file), profile_of(file))
    return flags, [{"id": o, "size": len(d), "crc": zlib.crc32(d), "bytes": d} for o, d in objs]


def from_bytes(raw: bytes) -> dict | None:
    """a firmware's own image file (X0X's .x0b, LOOM-8's .loom8; raw bytes) -> the suite's file, or None"""
    p = next((p for p in PROFILES.values() if bytes(raw[:4]) == p.magic), None)
    return to_file(bytes(raw), p.name) if p else None


def is_image(raw) -> bool:
    return any(bytes(raw[:4]) == p.magic for p in PROFILES.values())


# ------------------------------------------------------------- the wire ---
def _pack7(data) -> list[int]:
    out, acc, nb = [], 0, 0
    for b in data:
        acc |= b << nb
        nb += 8
        while nb >= 7:
            out.append(acc & 0x7F)
            acc >>= 7
            nb -= 7
    if nb:
        out.append(acc & 0x7F)
    return out


def _unpack7(s) -> bytes:
    out, acc, nb = bytearray(), 0, 0
    for b in s:
        acc |= (b & 0x7F) << nb
        nb += 7
        while nb >= 8:
            out.append(acc & 0xFF)
            acc >>= 8
            nb -= 8
    return bytes(out)


def frame(cmd: int, body: bytes) -> list[int]:
    """a host message in the update's framing (ota.c ota_take)"""
    n = len(body)
    msg = bytes([0x00, 0x59, cmd, n & 0xFF, (n >> 8) & 0xFF, (n >> 16) & 0xFF]) + bytes(body) + bytes([~sum(body) & 0xFF])
    return [0xF0, *_pack7(msg), 0xF7]


def parse_frame(pkt) -> tuple[int, bytes] | None:
    """a device message -> (cmd, body) or None"""
    if len(pkt) < 2 or pkt[0] != 0xF0 or pkt[-1] != 0xF7:
        return None
    u = _unpack7(pkt[1:-1])
    if len(u) < 7 or u[:2] != b"\x00\x59":
        return None
    n = u[3] | u[4] << 8 | u[5] << 16
    if n + 7 != len(u) or ~sum(u[6:6 + n]) & 0xFF != u[6 + n]:
        return None
    return u[2], u[6:6 + n]


def response(addr: int, data: bytes, fl: int = 0) -> list[int]:
    """the host's answer to a read request (tools/fm1_install.py response)"""
    n = len(data)
    body = bytes([fl]) + addr.to_bytes(4, "little") + bytes([n & 0xFF, (n >> 8) & 0xFF, 0]) + bytes(data)
    return frame(READ, body)


class _Wire:
    """the device's SysEx frames in the update's framing, while the link is held for the app"""

    def __init__(self, link):
        self.link, self.q = link, queue.Queue()

    def __enter__(self):
        self.link.hold()
        self.link.add_listener(self._in)
        return self

    def __exit__(self, *exc):
        self.link.remove_listener(self._in)
        self.link.release()

    def _in(self, data, direction):
        if direction == "in" and data and data[0] == 0xF0:
            f = parse_frame(data)
            if f:
                self.q.put(f)

    def send(self, data):
        self.link.send_midi(list(data), force=True)

    def get(self, timeout: float):
        try:
            return self.q.get(timeout=timeout)
        except queue.Empty:
            return None


def _is_twin(link) -> bool:
    return bool(getattr(getattr(link, "t", None), "twin", False))


def capture(link, firmware: str, progress: Callable[[int, int], None] | None = None,
            companion: dict | None = None, first: float = 3.0, idle: float = 10.0, key: str = "x0x") -> dict:
    """read the saved state from the firmware (cmd 0x42 with its magic) -> the suite's file (checked)"""
    p = profile_of(key)
    fw = p.name
    got, total = bytearray(), len(p.ids) * (REC + PAYLOAD_MAX)
    with _Wire(link) as w:
        w.send(frame(BU_REQ, p.magic))
        start = True
        while True:
            f = w.get(first if start else idle)
            if f is None:
                raise _err((_("{firmware} does not answer the backup request (an older version, without backup?).")
                            if start else _("{firmware} stopped sending the backup – please try again.")).format(
                                firmware=fw))
            cmd, body = f
            if cmd == BU_REQ and len(body) >= 4:
                start = False
                if struct.unpack_from("<I", body)[0] != len(got):
                    raise _err(_("A piece of the backup went missing – please try again."))
                got += body[4:]
                if progress:
                    progress(min(len(got), total), total)
            elif cmd == BU_END and len(body) >= 10:
                n, crc, status, _flags = struct.unpack_from("<IIBB", body)
                if status:
                    raise _err(_("{firmware} could not read its flash (status {code}).").format(firmware=fw,
                                                                                                code=status))
                if n != len(got) or crc != zlib.crc32(got):
                    raise _err(_("The backup arrived damaged – please try again."))
                break
    image = bytes(got)
    if progress:
        progress(total, total)
    return to_file(image, firmware, companion)


def restore(link, file: dict, progress: Callable[[int, int], None] | None = None, ask: float = 75.0,
            on_ask: Callable[[], None] | None = None) -> int:
    """put a backup onto the firmware (cmd 0x41): it pulls the image, checks it whole, asks on its screen (SEL yes, HOME no),
    then commits. A studio twin gets the image on its side of the cable first (it answers yes itself). -> 0; a refusal
    or failure raises BackupError (nothing changed, except code 12: the FM-1 finishes at its next start)"""
    image = image_of(file)
    p = profile_of(file)
    fw = p.name
    parse(image, p)
    twin = _is_twin(link)
    with _Wire(link) as w:
        if twin:
            for off in range(0, len(image), 512):
                w.send(frame(STAGE, struct.pack("<I", off) + image[off:off + 512]))
        w.send(frame(RS_REQ, struct.pack("<II", len(image), zlib.crc32(image))))
        wait, sent = 3.0, 0
        while True:
            f = w.get(wait)
            if f is None:
                raise _err((_("{firmware} stopped asking for the backup – nothing was changed.") if sent else
                            _("{firmware} does not answer the restore request (an older version, without restore?) – "
                              "nothing was changed.")).format(firmware=fw))
            cmd, body = f
            if cmd != READ or len(body) != 8:
                continue
            fl, addr, n = body[0], int.from_bytes(body[1:5], "little"), int.from_bytes(body[5:8], "little")
            wait = 10.0
            if addr & 0xFF000000 in (OK_ADDR, FAIL_ADDR, ASK_ADDR):
                if not twin:
                    w.send(response(addr, b"success\0", fl))
                if addr == ASK_ADDR:
                    wait = ask
                    if on_ask:
                        on_ask()
                    continue
                code = 0 if addr == OK_ADDR else (addr & 0xFFFF) or 99
                break
            if twin:
                continue
            if n > 512 or addr >= len(image):
                raise _err(_("{firmware} asked for a part outside the backup – nothing was changed.").format(firmware=fw))
            data = image[addr:addr + n]
            w.send(response(addr, data + bytes(n - len(data)), fl))
            sent = max(sent, min(addr + n, len(image)))
            if progress:
                progress(sent, len(image))
    if code:
        msg = _(ERRORS[code]) if code in ERRORS else _("error {code}").format(code=code)
        if code == PLAYING:
            raise _err(_("Please stop playback on the FM-1 first – nothing was written."))
        raise _err(_("{firmware} did not restore the backup: {reason}.").format(firmware=fw, reason=msg) +
                   ("" if code == 12 else " " + _("Nothing was changed.")))
    if progress:
        progress(len(image), len(image))
    return 0
