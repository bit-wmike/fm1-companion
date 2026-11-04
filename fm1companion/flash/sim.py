# SPDX-License-Identifier: GPL-3.0-only
"""Simulated FM-1 for the update protocol (tests, demo mode). Nothing is written anywhere.

Speaks the same protocol as felucca-bsp/chip/ota.c and the loaders (see docs/flash-protocol.md):
handshake 0x11, UPGRADE, read requests 0x30 on the logical image, 0xE0000000 / 0xF0000000.
Modelled on Melodee's test device (web/install/fakefm1.js, test_web.mjs FakeFM1) – here in Python
and synchronous: the device answers directly in send(), reboots go through a short offline period.

  SimFM1(identity, policy="felucca"|"stock"|"open", name=…, felucca_loader="ota-FM-1_900",
         resumable=True, power_loss_at=None, boot_matches_stock=True)
  SimBackend([devices]) -> input_names(), output_names(), open(in, out)   (like fm1_install.MidoBackend)
"""
from __future__ import annotations

import hashlib
import queue
import threading
import time

from ..i18n import _
from ..vendor import fm1_install as fi
from . import package as pk

UPGRADE = bytes(fi.UPGRADE)
HS = bytes(fi.HS_QUERY)
BLOCK = 512


def _frame(cmd: int, body: bytes) -> bytes:
    u = bytes([0x00, 0x59, cmd, len(body) & 0xFF, len(body) >> 8, 0]) + body
    return b"\xF0" + fi.pack7(u + bytes([~sum(body) & 0xFF])) + b"\xF7"


def identity_frame(text: str) -> bytes:
    t = text.encode("latin-1")[:26]
    return _frame(0x11, t + bytes(27 - len(t)))


def request_frame(addr: int, n: int) -> bytes:
    u = bytes([0x00, 0x59, 0x30, 8, 0, 0, 0]) + addr.to_bytes(4, "little") + n.to_bytes(3, "little")
    return b"\xF0" + fi.pack7(u + bytes([~sum(u[6:14]) & 0xFF])) + b"\xF7"


def parse_reply(pkt: bytes):
    u = fi.unpack7(pkt[1:-1])
    if len(u) < 15 or u[:3] != b"\x00\x59\x30":
        return None
    addr = int.from_bytes(u[7:11], "little")
    n = int.from_bytes(u[11:14], "little")
    return addr, u[14:14 + n]


class SimFM1:
    def __init__(self, identity="FM-1_91060", policy="felucca", name="Melodee FM-1",
                 felucca_loader="ota-FM-1_900", resumable=True, power_loss_at=None,
                 boot_matches_stock=True, reboot_s=0.05, usb_id="1209:0001", serial="", usb_path=""):
        self.identity = identity
        self.policy = policy                 # step-1 rules of the running firmware
        self.app_name = name
        self.felucca_loader = felucca_loader
        self.resumable = resumable           # Felucca G2/G3: back in update mode after power loss
        self.power_loss_at = power_loss_at   # 0..1: step 2 aborts at this fraction (once)
        self.boot_matches_stock = boot_matches_stock   # False: CHECK HEAD fails (FAILED -5)
        self.reboot_s = reboot_s
        self.serial = serial
        self.usb_path = usb_path             # physical USB connection (stays the same across reboots)
        self.app_usb_id = usb_id
        self.state = "app"                   # app | loader | uboot
        self.loader_kind = ""                # "stock" | "felucca" in update mode
        self.loader_id = ""
        self.online_at = 0.0
        self.pending_state = None
        self.session = None
        self.waiting = None
        self.links: list["SimLink"] = []
        self.products: dict[str, str] = {}   # sha256(logical image) -> identity after writing
        self.stock_heads: set[bytes] = set()
        self.stock_products: dict[bytes, str] = {}   # header of the logical image -> identity of the stock package
        self.flash: bytes = b""              # last completely written image
        self.partial = 0                     # bytes already received in the current step 2
        self.log: list[str] = []
        self.bricked = ""                    # set if something dangerous would have happened
        self.sends: list[bytes] = []         # everything the host has sent
        self._lock = threading.RLock()

    # -------------------------------------------------- registration
    def register(self, raw: bytes, product: str | None = None, stock: bool | None = None):
        """register a package (which identity it reports after writing)"""
        img = fi.logical_image(raw)
        self.products[hashlib.sha256(img).hexdigest()] = product or fi.product_of(raw)
        is_stock = stock if stock is not None else hashlib.sha256(raw).hexdigest() in pk.OFFICIAL
        if is_stock:
            self.stock_heads.add(img[:0x400])
            self.stock_products[img[:0x400]] = product or fi.product_of(raw)

    # -------------------------------------------------- state / ports
    @property
    def online(self) -> bool:
        self._tick()
        return self.state != "uboot" and time.monotonic() >= self.online_at

    def _tick(self):
        if self.pending_state and time.monotonic() >= self.online_at:
            st, ident = self.pending_state
            self.pending_state = None
            self.state = st
            if ident:
                self.identity = ident

    @property
    def text(self) -> str:
        return self.loader_id if self.state == "loader" else self.identity

    @property
    def port_name(self) -> str:
        if self.state == "loader":
            return "USB Composite Device" if self.loader_kind == "stock" else self.app_name + " Update"
        return self.app_name

    @property
    def usb_id(self) -> str:
        if self.state == "uboot":
            return "4c4a:8057"
        if self.state == "loader":
            return "4d4a:4155" if self.loader_kind == "stock" else "1209:0002"
        return self.app_usb_id

    def _restart(self, state: str, ident: str | None = None, delay: float | None = None):
        for ln in self.links:
            ln.dead = True
        self.links.clear()
        self.session = None
        self.waiting = None
        self.online_at = time.monotonic() + (self.reboot_s if delay is None else delay)
        self.pending_state = (state, ident)
        self.state = "rebooting"

    def power_cycle(self, hold_oct_minus: bool = False):
        """power off and on (rescue: hold OCT− -> update mode, if the firmware supports it)"""
        if self.state == "uboot":
            return
        if self.state == "loader" or hold_oct_minus and self.loader_kind:
            self._restart("loader")
        else:
            self._restart("app")

    # -------------------------------------------------- receiving
    def receive(self, data: bytes, link: "SimLink"):
        with self._lock:
            self.sends.append(data)
            if not self.online:
                return
            if data == HS:
                link.emit(identity_frame(self.text))
                return
            if data == UPGRADE:
                if self.session is None and self.policy != "open":
                    self.session = self._step1() if self.state == "app" else self._step2()
                    self._advance(link, None)
                return
            if self.waiting is not None and data[:1] == b"\xF0":
                r = parse_reply(data)
                if r and r[0] == self.waiting[0]:
                    self._advance(link, r[1])

    def _advance(self, link, got):
        try:
            want = self.session.send(got) if got is not None else next(self.session)
        except StopIteration:
            self.session = None
            self.waiting = None
            return
        if want is None:
            self.session = None
            self.waiting = None
            return
        self.waiting = want
        link.emit(request_frame(*want))

    # -------------------------------------------------- step 1: running firmware
    def _step1(self):
        hdr = (yield (0, BLOCK)) + (yield (BLOCK, BLOCK))
        dec = pk._jl(hdr[:0x40])
        nent = int.from_bytes(dec[8:10], "little")
        if pk._crc16(dec[2:0x40]) != int.from_bytes(dec[0:2], "little") or not 1 <= nent <= 11:
            self.log.append("refused: header")
            return
        ota_off = ota_len = fl_off = 0
        for i in range(nent):
            e = pk._jl(hdr[0x40 + i * 0x50:0x90 + i * 0x50])
            typ = int.from_bytes(e[0:2], "little")
            if typ == 100:
                ota_off, ota_len = int.from_bytes(e[8:12], "little"), int.from_bytes(e[12:16], "little")
            if typ == 0:
                fl_off = int.from_bytes(e[8:12], "little")
        if not ota_off:
            self.log.append("refused: no loader")
            return
        first = yield (ota_off, min(BLOCK, ota_len))
        own = fi.LOADER_MARK in first[:0x48 + 80]
        official = hdr[:0x400] in self.stock_heads
        if self.policy == "felucca":
            if not own and not official:
                self.log.append("refused: foreign loader (-7)")
                return
            if official:
                # Melodee ota.c: package boot header == device boot header? The device carries the factory header (V13–V15);
                # V16 brings a different one -> FAILED -5 (nothing written)
                v16 = self.stock_products.get(hdr[:0x400]) == "FM-1_016"
                for a in range(0, 0x4000, BLOCK):
                    yield (fl_off + a, BLOCK)
                    if not self.boot_matches_stock or v16:
                        self.log.append("refused: CHECK HEAD (-5)")
                        return
        for a in range(ota_off, ota_off + ota_len, BLOCK):
            yield (a, min(BLOCK, ota_off + ota_len - a))
        ok = yield (0xE0000000, 8)
        if ok[:7] != b"success":
            self.log.append("no success")
            return
        self.loader_kind = "felucca" if own else "stock"
        self.loader_id = self.felucca_loader if own else "ota-FM-1_015"
        self.log.append(f"loader staged: {self.loader_id}")
        self._restart("loader")

    # -------------------------------------------------- step 2: loader
    def _step2(self):
        hdr = yield (0, 0x40)
        dec = pk._jl(hdr)
        size = int.from_bytes(dec[4:8], "little")
        if size < 0x40 or size > 4 << 20:
            self.log.append("loader: bad header")
            return
        buf = bytearray(size)
        buf[:0x40] = hdr
        a = 0x40
        while a < size:
            n = min(BLOCK, size - a)
            buf[a:a + n] = yield (a, n)
            self.partial = a + n
            if self.power_loss_at is not None and a >= size * self.power_loss_at:
                self.power_loss_at = None
                self.log.append("power lost while writing")
                if self.resumable:
                    self._restart("loader", delay=self.reboot_s * 2)
                else:
                    self.state = "uboot"
                    for ln in self.links:
                        ln.dead = True
                    self.links.clear()
                    self.session = None
                    self.bricked = "power loss with a non-resumable loader: ROM-UBOOT"
                return
            a += n
        img = bytes(buf)
        is_stock_img = img[:0x400] in self.stock_heads
        if self.loader_kind == "stock" and not is_stock_img:
            self.bricked = "foreign package in the original loader: boot area overwritten"
        if self.loader_kind == "felucca" and is_stock_img:
            self.bricked = "original firmware in the Felucca loader: only the app area written"
        ok = yield (0xF0000000, 8)
        if ok[:7] != b"success":
            return
        self.flash = img
        after = self.products.get(hashlib.sha256(img).hexdigest(), "FM-1_900")
        self.log.append(f"written: {after}")
        self.loader_kind = ""
        if pk.is_stock_identity(after):
            self.policy, self.app_name, self.app_usb_id = "stock", "USB Composite Device", "4c4a:c755"
        else:
            self.policy = "felucca"
            if self.app_name == "USB Composite Device":
                self.app_name, self.app_usb_id = "Felucca", "1209:0001"
        self._restart("app", after)


class SimLink:
    def __init__(self, backend: "SimBackend", dev: SimFM1, name: str):
        self.backend, self.dev, self.name = backend, dev, name
        self.q: queue.Queue = queue.Queue()
        self.dead = False
        dev.links.append(self)

    def emit(self, pkt: bytes):
        if not self.dead:
            self.q.put(bytes(pkt))

    @property
    def lost(self):
        return self.dead or self.name not in self.backend.input_names()

    def send(self, pkt) -> bool:
        if self.lost:
            return False
        self.dev.receive(bytes(pkt), self)
        return True

    def read(self, timeout):
        try:
            return self.q.get(timeout=max(min(timeout, 0.05) if self.dead else timeout, 0))
        except queue.Empty:
            return None

    def drain(self):
        while not self.q.empty():
            self.q.get_nowait()

    def close(self):
        if self in self.dev.links:
            self.dev.links.remove(self)


class SimBackend:
    """Replacement for fm1_install.MidoBackend: ports of the simulated devices (plus foreign devices)."""

    def __init__(self, devices=(), other_ports=("Midi Through Port-0",)):
        self.devices: list[SimFM1] = list(devices)
        self.other = list(other_ports)
        self.opened_other: list[str] = []

    def _names(self):
        names = []
        for d in self.devices:
            if d.online:
                n = d.port_name
                k = 2
                while n in names:
                    n = f"{d.port_name} {k}"
                    k += 1
                names.append(n)
        return names

    def input_names(self):
        return self._names() + list(self.other)

    def output_names(self):
        return self.input_names()

    def device_for(self, name: str) -> SimFM1 | None:
        for d, n in zip([d for d in self.devices if d.online], self._names()):
            if n == name:
                return d
        return None

    def details(self, name: str) -> dict:
        """like devices.usb_details: USB connection and serial number of the device behind a port"""
        dev = self.device_for(name)
        if dev is None:
            return {}
        return {"usb_path": dev.usb_path, "serial": dev.serial, "usb_id": dev.usb_id}

    def open(self, in_name, out_name):
        dev = self.device_for(in_name)
        if dev is None:
            self.opened_other.append(in_name)
            raise OSError(_("no simulated FM-1 at {port}").format(port=in_name))
        return SimLink(self, dev, in_name)


def fast_delays():
    """short wait times for the simulation (values shortened as in Melodee's tests)"""
    return {"open": 0.0, "start": 0.01, "reply": 0.0, "loader": 0.02, "reboot": 0.02, "retry": 0.02,
            "poll": 0.05, "hs": 0.1, "idle_check": 1.0, "idle_write": 2.0, "wait_loader": 3.0, "wait_reboot": 3.0}
