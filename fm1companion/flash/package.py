# SPDX-License-Identifier: GPL-3.0-only
"""Inspect an FM-1 package (.fwsc): identity, checksum, loader family, integrity.

Everything here only reads files – no MIDI. Background: docs/flash-protocol.md.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..i18n import _
from ..vendor import fm1_install as fi

STOCK_V15_SHA256 = fi.STOCK_V15_SHA256
STOCK_V16_SHA256 = "d017fb65e70aca97ee29d944470c3515a74581df5c0576bf70de917c7b2b61d5"
STOCK_V15_URL = ("https://github.com/m-vave/fm1_open/raw/b178ac43c25f010c60a878056fb5331fe693fd62/"
                 "sdk/firmware/FM-1_015.fwsc")
STOCK_V16_URL = "https://yms-file-store.oss-cn-hongkong.aliyuncs.com/software/firmware/FM-1.fwsc"
OFFICIAL = {STOCK_V15_SHA256: "FM-1_015", STOCK_V16_SHA256: "FM-1_016"}
LOADER_MARK = fi.LOADER_MARK

STOCK_ID_RE = re.compile(r"^FM-1_0\d\d$")
STOCK_LOADER_RE = re.compile(r"^ota-FM-1_0\d\d$")
FELUCCA_LOADER_RE = re.compile(r"^ota-FM-1_9\d\d$")
# Update loader of the M-VAVE FM1 Open: "ota-FM1C_" + the first 16 hex characters of the build ID (m-vave/fm1_open
# sdk/WebUpdate.js, prepare → loaderIdentity). Only for recognising and explaining – the app sends nothing to this loader.
OPEN_LOADER_RE = re.compile(r"^ota-FM1C_[0-9a-f]{16}$")
OPEN_ID_RE = re.compile(r"^FM1_(OFFICIAL_OPEN|COMMUNITY_\d+)$")
OPEN_V14_ID = "FM-1_8000014"         # FM1 Open V14 in normal operation (m-vave/fm1_open app/version.h)

# families as in the Mooring catalog (loaders[].family) -> our two protocol families
FAMILY_OF_CATALOG = {"stock": "stock", "feluccaG1": "felucca", "feluccaG2": "felucca", "sloopG3": "felucca"}


class PackageError(Exception):
    pass


def is_stock_identity(ident: str) -> bool:
    return bool(STOCK_ID_RE.match(ident or "")) and ident != "FM-1_099"


class LoaderRule:
    """which update loader identities may receive this package"""

    def __init__(self, family: str, product: str):
        self.family, self.product = family, product
        if family == "stock":
            self.label = _("the original loader (ota-FM-1_015)")
        else:
            self.label = _("the Felucca loader (ota-FM-1_900 or ota-{product})").format(product=product)

    def __call__(self, text: str) -> bool:
        if self.family == "stock":
            return bool(STOCK_LOADER_RE.match(text))
        if self.family == "felucca":
            return bool(FELUCCA_LOADER_RE.match(text)) or text == f"ota-{self.product}"
        return False

    def __repr__(self):
        return f"LoaderRule({self.family})"


@dataclass
class PackageInfo:
    path: Path
    size: int
    sha256: str
    product: str
    family: str                 # "stock" | "felucca" | "unknown"
    official: str = ""          # "FM-1_015"/"FM-1_016" for unmodified stock firmware
    damage: str = ""            # empty = header, file list and CRCs OK
    entries: list[str] = field(default_factory=list)
    image: bytes = b""          # logical image (what the device reads)

    @property
    def model(self) -> str:
        return fi.model_of(self.product)

    @property
    def loader_rule(self) -> LoaderRule | None:
        return LoaderRule(self.family, self.product) if self.family in ("stock", "felucca") else None

    @property
    def flash_bytes(self) -> int:
        """Length of flash.bin = how far the update loader writes from 0x4000 (0 = unknown)"""
        return next((size for typ, _n, _off, size in ufw_check(self.image)[1] if typ == 0), 0) if self.image else 0

    @property
    def resumable_hint(self) -> str:
        return {"stock": "ota-FM-1_015", "felucca": "ota-FM-1_900"}.get(self.family, "")


def _crc16(data: bytes, crc: int = 0) -> int:
    for b in data:
        crc ^= b << 8
        for _bit in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def _jl(b: bytes, key: int = 0xFFFF) -> bytes:
    """JieLi ENC stream cipher (its own inverse)"""
    out = bytearray(b)
    for i in range(len(out)):
        out[i] ^= key & 0xFF
        key = ((key << 1) ^ (0x1021 if key & 0x8000 else 0)) & 0xFFFF
    return bytes(out)


def ufw_check(image: bytes) -> tuple[str, list[tuple[int, str, int, int]]]:
    """(error or "", [(type, name, offset, size)]) – like SLOOP's package_damage()"""
    if len(image) < 0x40:
        return _("too short"), []
    hdr = _jl(image[0:0x40])
    if _crc16(hdr[2:0x40]) != int.from_bytes(hdr[0:2], "little"):
        return _("header checksum wrong"), []
    count = int.from_bytes(hdr[8:10], "little")
    if not 1 <= count <= 12 or len(image) < 0x40 + 0x50 * count:
        return _("file list invalid"), []
    lst = image[0x40:0x40 + 0x50 * count]
    if _crc16(lst) != int.from_bytes(hdr[2:4], "little"):
        return _("checksum of the file list wrong"), []
    entries = []
    for i in range(count):
        e = _jl(lst[i * 0x50:(i + 1) * 0x50])
        typ = int.from_bytes(e[0:2], "little")
        dcrc = int.from_bytes(e[4:6], "little")
        off = int.from_bytes(e[8:12], "little")
        size = int.from_bytes(e[12:16], "little")
        name = e[0x40:0x50].split(b"\0")[0].decode("ascii", "replace")
        if off + size > len(image):
            return _("{name}: truncated").format(name=name), entries
        if typ in (0, 100) and _crc16(image[off:off + size]) != dcrc:
            return _("{name}: checksum wrong").format(name=name), entries
        entries.append((typ, name, off, size))
    if not any(t == 0 for t, *_ in entries):
        return _("no flash.bin in the package"), entries
    if not any(t == 100 for t, *_ in entries):
        return _("no update loader (ota.bin) in the package"), entries
    return "", entries


def analyze(path: Path | str, catalog_family: str = "") -> PackageInfo:
    """Read and classify a package. catalog_family: loader family according to the catalog (if known)."""
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as e:
        raise PackageError(_("{name} cannot be read: {error}").format(name=path.name, error=e.strerror))
    if len(raw) > 4 * 1024 * 1024:
        raise PackageError(_("{name} is too large for an FM-1 package").format(name=path.name))
    try:
        product = fi.product_of(raw)
    except fi.InstallError:
        raise PackageError(_("{name} is not an FM-1 package (too short)").format(name=path.name))
    if not re.fullmatch(r"[^_]+_\d+", product):
        raise PackageError(_("{name} is not an FM-1 package (identity {identity})").format(name=path.name, identity=repr(product)))
    sha = hashlib.sha256(raw).hexdigest()
    image = fi.logical_image(raw)
    damage, entries = ufw_check(image)
    official = OFFICIAL.get(sha, "")
    if official:
        family = "stock"
    elif LOADER_MARK in raw:
        family = "felucca"
    else:
        family = FAMILY_OF_CATALOG.get(catalog_family, "unknown")
    return PackageInfo(path=path, size=len(raw), sha256=sha, product=product, family=family, official=official,
                       damage=damage, entries=[n for _, n, *_ in entries], image=image)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 16), b""):
            h.update(b)
    return h.hexdigest()


# ------------------------------------------------- synthetic packages (tests, demo) ---

def build_package(product: str, family: str = "felucca", app: bytes | None = None, size: int = 0x6000,
                  seed: int = 1) -> bytes:
    """a structurally valid .fwsc with UFW header, flash.bin and ota.bin – only for simulation/tests.
    family "felucca": ota.bin carries FELUCCA-LOADER-1; "unknown": foreign loader without marker."""
    if app is None:
        app = bytes(((i * 131 + seed * 17) ^ (i >> 5)) & 0xFF for i in range(size))
    ota = bytearray(b"\0" * 0x48) + (LOADER_MARK if family == "felucca" else b"OTHER-LOADER-XYZ") + bytes(0x100)
    head = 0x40 + 2 * 0x50
    off_fl = (head + 0x1F) & ~0x1F
    off_ota = (off_fl + len(app) + 0x1F) & ~0x1F
    total = off_ota + len(ota)
    img = bytearray(total)
    img[off_fl:off_fl + len(app)] = app
    img[off_ota:off_ota + len(ota)] = ota
    ents = b""
    for typ, name, off, data in ((0, "flash.bin", off_fl, app), (100, "ota.bin", off_ota, bytes(ota))):
        e = bytearray(0x50)
        e[0:2] = typ.to_bytes(2, "little")
        e[4:6] = _crc16(data).to_bytes(2, "little")
        e[8:12] = off.to_bytes(4, "little")
        e[12:16] = len(data).to_bytes(4, "little")
        e[0x40:0x40 + len(name)] = name.encode()
        ents += _jl(bytes(e))
    h = bytearray(0x40)
    h[2:4] = _crc16(ents).to_bytes(2, "little")
    h[4:8] = total.to_bytes(4, "little")
    h[8:10] = (2).to_bytes(2, "little")
    h[16:22] = b"AC791N"
    h[0:2] = _crc16(bytes(h[2:0x40])).to_bytes(2, "little")
    img[0:0x40] = _jl(bytes(h))
    img[0x40:head] = ents
    # weave in marker bytes: logical 940 header bytes -> 20 blocks of 0x2F + marker
    if len(product) > 20:
        raise ValueError("identity too long")
    raw = bytearray()
    for i in range(20):
        raw += img[i * 0x2F:(i + 1) * 0x2F]
        raw.append((ord(product[i]) + i + 1) & 0xFF if i < len(product) else 0x7D)
    raw += img[20 * 0x2F:]
    return bytes(raw)
