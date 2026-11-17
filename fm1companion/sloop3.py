# SPDX-License-Identifier: GPL-3.0-only
"""SLOOP 3 (isod89/sloop3-fm1, GPL-3.0; a public beta): what it answers, and its editor protocol.

SLOOP 3 is a rewrite, not SLOOP 2.x: it answers none of the Felucca family's commands (``F0 7D 46 4C …``: INFO 1,
SLOOP 2's backup 34–36, Felucca's 65–67), not the update handshake and not Hortator's INFO – it leaves them
unanswered and unchanged (app/edproto.c ed_parse: any frame that does not start ``F0 7D 53 33`` is "not ours").
Checked on the author's PC twin (host/twin.c, fw/main.c 3.3.7 on a simulated FM-1): tests/test_sloop3.py keeps what it
answered.

Its own protocol (app/edproto.h, the web editor web/src/core.js)::

    F0 7D 53 33 <cmd> <the bytes, packed 8 to 7, LSB first> F7

Every request gets an answer with the same command; command 0 with one byte means refused (1 bad request, 2 busy –
a save under way –, 3 nothing there, 4 check failed, 5 not a SLOOP 3 file, 6 flash error).

What the app itself sends, and only to a device that answered SLOOP 3's INFO:
* INFO (1) – read only: protocol, tracks, the version text;
* READ (5), BANK 0/1 (14), SAMPLE 0 (13), HOME without arguments (19) – read only (backups, sloop3bk.py);
* WBEGIN/WDATA/WEND (6–8) and BANK 2–4 – only when the user restores a backup.
Everything else is the bundled web editor's business (fm1companion/sims/_editors/sloop3).

On USB it is "FM-1 SLOOP 3", 1209:0001 (hal/fm1/usb.c) – the ID Felucca's family uses too, so only INFO tells it.
"""
from __future__ import annotations

import json
import queue
import re
import threading
import time
import zlib
from dataclasses import dataclass
from pathlib import Path

from .i18n import N_, _

KEY = "sloop3"                       # the firmware key – never "sloop" (SLOOP 2.x: another protocol, other backups)
NAME = "SLOOP 3"
HDR = (0x7D, 0x53, 0x33)
PORT_RE = re.compile(r"sloop\s*3", re.I)
REPO = "https://github.com/isod89/sloop3-fm1"
GUIDE_URL = REPO + "/blob/main/docs/GUIDE.md"
INSTALLER_URL = "https://isod89.github.io/sloop3-fm1/"
EDITOR_URL = "https://isod89.github.io/sloop3-fm1/editor.html"
ISSUES_URL = REPO + "/issues"
MBOOT_URL = "https://github.com/jvitkauskas/m-bootloader"
MFLASHER_URL = "https://github.com/jvitkauskas/m-flasher"
# the bundled web editor (identical in both tags; sims/_editors/sloop3/manifest.json)
EDITOR_DIR = Path(__file__).resolve().parent / "sims" / "_editors" / "sloop3"
EDITOR_SERVED = "_editors/sloop3"    # under the local server's /sims/


class Cmd:
    INFO = 1
    SOUND = 2
    SET = 3
    DESC = 4
    READ = 5
    WBEGIN = 6
    WDATA = 7
    WEND = 8
    LOAD = 9
    PATTERN = 10
    THEME = 11
    NOTES = 12
    SAMPLE = 13
    BANK = 14
    LIVE = 15
    NOTES2 = 16
    LOCKS = 17
    SONG = 18
    HOME = 19
    NAMES = 20


ERR_BUSY = 2
ERRORS = {1: N_("bad request"), 2: N_("busy (a save is under way)"), 3: N_("nothing there"),
          4: N_("check failed"), 5: N_("not a SLOOP 3 file"), 6: N_("flash error")}
CHUNK = 384                          # the most bytes one READ / WDATA carries (fw/main.c)
BANK_CHUNK = 380                     # (the editor's bankWrite)
FM6, SYN = 0, 1                      # BANK's images: your FM6 bank, your SYN kits
FM6_LEN = 16 + 27 * 128              # app/xbank.h XB_FM6_LEN
SYN_LEN = 16 + 4 * (4 + 16 * 22)     # XB_SYN_LEN
KIND_PROJECT, KIND_SOUNDS, KIND_NOW = 0, 1, 2      # READ's kinds: the saved project, your sounds, the project now


class Sloop3Error(Exception):
    """a ready (translated) sentence; ``code``: SLOOP 3's refusal (0 = none: no answer, a broken answer)"""

    def __init__(self, text: str, code: int = 0):
        super().__init__(text)
        self.code = code


# ---------------------------------------------------------------- frames ---

def pack(raw) -> list[int]:
    out, acc, bits = [], 0, 0
    for b in bytes(raw):
        acc |= b << bits
        bits += 8
        while bits >= 7:
            out.append(acc & 0x7F)
            acc >>= 7
            bits -= 7
    if bits:
        out.append(acc & 0x7F)
    return out


def unpack(data) -> bytes:
    out, acc, bits = bytearray(), 0, 0
    for b in data:
        acc |= (b & 0x7F) << bits
        bits += 7
        if bits >= 8:
            out.append(acc & 0xFF)
            acc >>= 8
            bits -= 8
    return bytes(out)


def frame(cmd: int, raw=b"") -> list[int]:
    if not 0 <= cmd <= 127:
        raise ValueError("command")
    return [0xF0, *HDR, cmd, *pack(raw), 0xF7]


def unframe(data) -> tuple[int, bytes] | None:
    d = list(data)
    if len(d) < 6 or d[0] != 0xF0 or d[-1] != 0xF7 or tuple(d[1:4]) != HDR or any(x > 127 for x in d[4:-1]):
        return None
    return d[4], unpack(d[5:-1])


def is_frame(data) -> bool:
    d = list(data[:4])
    return len(d) == 4 and d[0] == 0xF0 and tuple(d[1:4]) == HDR


def w16(v: int) -> bytes:
    return bytes([v & 255, v >> 8 & 255])


def w32(v: int) -> bytes:
    return bytes([v & 255, v >> 8 & 255, v >> 16 & 255, v >> 24 & 255])


def u16(a, i: int) -> int:
    return a[i] | a[i + 1] << 8


def u32(a, i: int) -> int:
    return a[i] | a[i + 1] << 8 | a[i + 2] << 16 | a[i + 3] << 24


# ------------------------------------------------------------------ INFO ---

@dataclass
class Info:
    proto: int
    tracks: int
    values: int               # values a sound has
    project: int
    projects_used: int        # bit 0: a project is saved
    sounds_used: int          # bit k: your sound k+1 is used
    version: str              # "3.3.7"

    @property
    def tag(self) -> str:
        return "v" + self.version.lstrip("vV") if self.version else ""

    @property
    def label(self) -> str:
        return f"{NAME} {self.version}".strip()


def parse_info(raw: bytes) -> Info:
    if len(raw) < 11:
        raise Sloop3Error(_("SLOOP 3's answer is too short"))
    ver = bytes(raw[10:42]).split(b"\0")[0].decode("ascii", "replace")
    if not re.fullmatch(r"[\w.\- +]{1,31}", ver):
        raise Sloop3Error(_("SLOOP 3's answer names no version"))
    return Info(raw[0], raw[1], raw[2], raw[3], u16(raw, 4), u32(raw, 6), ver)


# the versions the app was checked with (the PC twin of each tag; never on a real FM-1 by this app)
CHECKED_VERSIONS = ("3.3.6", "3.3.7")
VERSION_DATES = {"3.3.6": "2026-11-15", "3.3.7": "2026-11-15"}      # the tags' commit dates


def version_checked(version: str) -> bool:
    return (version or "").lstrip("vV") in CHECKED_VERSIONS


# ---------------------------------------------------------------- client ---

class Client:
    """one request at a time over an open link (transport.Link); answers by a listener, so the Felucca request
    machinery (Link.request) is not involved. Use inside ``link.exclusive()`` when the web editor may be open."""

    _locks: dict[int, threading.Lock] = {}

    def __init__(self, link, timeout: float = 3.0):
        self.link = link
        self.timeout = timeout
        self._q: queue.Queue = queue.Queue()
        self._lock = Client._locks.setdefault(id(link), threading.Lock())

    def _listen(self, data, direction):
        if direction == "in" and is_frame(data):
            self._q.put(list(data))

    def __enter__(self):
        self.link.add_listener(self._listen)
        return self

    def __exit__(self, *exc):
        self.link.remove_listener(self._listen)
        return False

    def ask(self, cmd: int, raw=b"", timeout: float | None = None, busy_tries: int = 0) -> bytes:
        """-> the answer's bytes. Sloop3Error: refused (``code``), or no answer. busy_tries: how often a "busy"
        refusal is asked again (every 250 ms, as the web editor does while the FM-1 saves)."""
        for attempt in range(busy_tries + 1):
            try:
                return self._ask(cmd, raw, timeout)
            except Sloop3Error as e:
                if e.code != ERR_BUSY or attempt >= busy_tries:
                    raise
                time.sleep(0.25)
        raise AssertionError

    def _ask(self, cmd: int, raw, timeout) -> bytes:
        with self._lock:
            while not self._q.empty():
                self._q.get_nowait()
            if not self.link.send_midi(frame(cmd, raw), force=True):
                raise Sloop3Error(_("device not connected"))
            end = time.monotonic() + (timeout or self.timeout)
            while True:
                try:
                    f = unframe(self._q.get(timeout=max(0.01, end - time.monotonic())))
                except queue.Empty:
                    raise Sloop3Error(_("SLOOP 3 does not answer (command {cmd})").format(cmd=cmd))
                if f is None:
                    continue
                if f[0] == 0:
                    code = f[1][0] if f[1] else 0
                    raise Sloop3Error(_("SLOOP 3 refused: {reason}").format(
                        reason=_(ERRORS[code]) if code in ERRORS else code), code or 1)
                if f[0] == cmd:
                    return f[1]
                if time.monotonic() >= end:
                    raise Sloop3Error(_("SLOOP 3 does not answer (command {cmd})").format(cmd=cmd))

    # ---- read only
    def info(self) -> Info:
        return parse_info(self.ask(Cmd.INFO, timeout=1.0))

    def read_file(self, kind: int, slot: int = 0, progress=None) -> bytes:
        """READ: kind 0 the saved project, 1 your sounds, 2 the project as it is now; checked with the CRC-32 the
        FM-1 names. Sloop3Error with code 3: nothing there."""
        buf = bytearray()
        total, crc = 1, 0
        while len(buf) < total:
            b = self.ask(Cmd.READ, bytes([kind, slot]) + w32(len(buf)) + w16(CHUNK), busy_tries=80)
            if len(b) < 14 or b[0] != kind or u32(b, 2) != len(buf):
                raise Sloop3Error(_("unexpected answer while reading"))
            if buf and (u32(b, 6) != total or u32(b, 10) != crc):
                raise Sloop3Error(_("The FM-1 changed during the backup. Please try again with playback stopped."))
            total, crc = u32(b, 6), u32(b, 10)
            if total > 4 * 1024 * 1024:
                raise Sloop3Error(_("unexpected answer while reading"))
            data = b[14:]
            if not data and len(buf) < total:
                raise Sloop3Error(_("unexpected answer while reading"))
            buf += data
            if progress:
                progress(len(data))
        if zlib.crc32(bytes(buf)) & 0xFFFFFFFF != crc:
            raise Sloop3Error(_("The FM-1 changed during the backup. Please try again with playback stopped."))
        return bytes(buf)

    def bank_state(self) -> tuple[int, int, int]:
        """-> (FM6 bank's source: 0 none, 1 SLOOP 2.8's, 2 yours; its slots used; SYN kits' source: 0 defaults …)"""
        b = self.ask(Cmd.BANK, bytes([0]))
        if len(b) < 7:
            raise Sloop3Error(_("unexpected answer while reading"))
        return b[1], u32(b, 2), b[6]

    def bank_read(self, which: int, progress=None) -> bytes:
        """the FM6 bank (0) or the SYN kits (1) as the image that plays now (app/xbank.h), header and CRC checked"""
        want = SYN_LEN if which else FM6_LEN
        img = bytearray()
        while len(img) < want:
            b = self.ask(Cmd.BANK, bytes([1, which]) + w16(len(img)) + w16(CHUNK), busy_tries=80)
            if len(b) < 6 or b[1] != which or u16(b, 2) != len(img) or u16(b, 4) != want or len(b) == 6:
                raise Sloop3Error(_("unexpected answer while reading"))
            img += b[6:]
            if progress:
                progress(len(b) - 6)
        check_image(bytes(img), which)
        return bytes(img)

    def samples(self) -> list[dict]:
        """SAMPLE 0: what each user slot holds -> [{"zones", "name", "kib"}] (read only; the samples themselves
        cannot be read over this protocol)"""
        b = self.ask(Cmd.SAMPLE, bytes([0]))
        n = b[1] if len(b) > 2 else 0
        out = []
        for i in range(n):
            o = b[3 + 11 * i:3 + 11 * i + 11]
            if len(o) < 11:
                break
            out.append({"zones": o[0], "name": bytes(o[1:9]).split(b"\0")[0].decode("ascii", "replace"),
                        "kib": u16(o, 9)})
        return out

    def home(self) -> bytes:
        """HOME without arguments: the device's settings as the editor reads them (nothing is set)"""
        return self.ask(Cmd.HOME)

    # ---- writing (restore only)
    def write_file(self, kind: int, data: bytes, progress=None):
        """WBEGIN / WDATA / WEND as the web editor's fileWrite: kind 0 the project (written to the flash and
        opened), 1 your sounds. The FM-1 checks the CRC-32 and the file itself before it keeps it."""
        self.ask(Cmd.WBEGIN, bytes([kind, 0]) + w32(len(data)), busy_tries=80)
        for off in range(0, len(data), CHUNK):
            part = data[off:off + CHUNK]
            self.ask(Cmd.WDATA, w32(off) + part, busy_tries=80)
            if progress:
                progress(len(part))
        self.ask(Cmd.WEND, w32(zlib.crc32(data) & 0xFFFFFFFF), timeout=10.0, busy_tries=80)

    def bank_write(self, which: int, img: bytes, progress=None):
        """BANK 2 / 3 / 4 as the web editor's bankWrite; the image must be whole (check_image)"""
        check_image(img, which)
        self.ask(Cmd.BANK, bytes([2, which]) + w16(len(img)), busy_tries=80)
        for off in range(0, len(img), BANK_CHUNK):
            part = img[off:off + BANK_CHUNK]
            self.ask(Cmd.BANK, bytes([3, which]) + w16(off) + part, busy_tries=80)
            if progress:
                progress(len(part))
        self.ask(Cmd.BANK, bytes([4, which]), timeout=15.0, busy_tries=80)

    def wait_idle(self, seconds: float = 30.0):
        """after a write: until the FM-1 has finished saving (a READ of the project now is refused "busy" while it
        saves)"""
        end = time.monotonic() + seconds
        while True:
            try:
                self.ask(Cmd.READ, bytes([KIND_NOW, 0]) + w32(0) + w16(0))
                return
            except Sloop3Error as e:
                if e.code != ERR_BUSY or time.monotonic() >= end:
                    if e.code == ERR_BUSY:
                        raise Sloop3Error(_("The FM-1 is still saving – wait a moment and check on its screen."))
                    return
                time.sleep(0.25)


def check_image(img: bytes, which: int):
    """an FM6 bank / SYN kits image as app/xbank.c xb_check takes it: "S3F6" / "S3SY", version 1, CRC-32 of the body"""
    want = SYN_LEN if which else FM6_LEN
    magic = b"S3SY" if which else b"S3F6"
    if len(img) != want or img[:4] != magic or img[4] != 1 or \
            zlib.crc32(img[16:]) & 0xFFFFFFFF != u32(img, 12):
        raise Sloop3Error(_("The FM6 bank image is damaged.") if not which else _("The SYN kits image is damaged."))


def probe(link, timeout: float = 0.7, tries: int = 2) -> Info | None:
    """ask SLOOP 3's INFO (read only) -> Info, or None: no SLOOP 3. Never raises."""
    try:
        with Client(link, timeout=timeout) as c:
            for _try in range(tries):
                try:
                    return c.info()
                except Sloop3Error as e:
                    if e.code:              # (it answered, but refused INFO: no SLOOP 3 the app knows)
                        return None
    except Exception:  # noqa: BLE001 – a probe never disturbs connecting
        return None
    return None


# ------------------------------------------------------- the bundled editor ---

def editor_manifest() -> dict:
    try:
        m = json.loads((EDITOR_DIR / "manifest.json").read_text("utf-8"))
        return m if isinstance(m, dict) else {}
    except (OSError, ValueError):
        return {}


def editor_problems() -> list[str]:
    """the bundled editor's files against its manifest -> problems (empty = as shipped)"""
    import hashlib
    m = editor_manifest()
    if not m.get("files"):
        return [_("the bundled SLOOP 3 editor is missing")]
    out = []
    for name, info in m["files"].items():
        p = EDITOR_DIR / name
        if not p.is_file():
            out.append(_("{name} missing").format(name=name))
        elif hashlib.sha256(p.read_bytes()).hexdigest() != info.get("sha256"):
            out.append(_("{name} damaged").format(name=name))
    return out


def editor_fits(version: str) -> bool:
    """is the bundled editor the one of this firmware version? (the same file in every tag listed in the manifest)"""
    return (version or "").lstrip("vV") in [str(v) for v in editor_manifest().get("firmware_versions", [])]


# ------------------------------------------------------ hardware revision ---
# FM-1s sold from late 2026 have another flash chip (UCUN UC25HQ80, JEDEC 0xB36014; the M-VAVE logo on the front).
# SLOOP 3 takes the chip's ID in its code but was not tried on it, and m-bootloader – the only way to install
# SLOOP 3 – is little tested there: one such unit did not start after m-bootloader was installed (m-bootloader's
# README, SLOOP 3's installer page).
JEDEC_OLD, JEDEC_NEW = 0x856014, 0xB36014


def revision_of(profile) -> str:
    """"new" | "old" | "unknown" for a device profile – from the app's hardware module if it is there
    (fm1companion/hardware.py: revision(profile)), else from the profile's own field, else unknown."""
    try:
        from . import hardware  # type: ignore[attr-defined]
        r = hardware.revision(profile)
        if r in ("new", "old", "unknown"):
            return r
    except Exception:  # noqa: BLE001 – no such module (yet), or it could not tell
        pass
    for field in ("hardware_revision", "revision", "hardware"):
        v = str(getattr(profile, field, "") or "").lower()
        if v in ("new", "old"):
            return v
    return "unknown"


def revision_of_jedec(jedec: int) -> str:
    return {JEDEC_NEW: "new", JEDEC_OLD: "old"}.get(jedec & 0xFFFFFF, "unknown")
