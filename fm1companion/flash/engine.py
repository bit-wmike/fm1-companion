# SPDX-License-Identifier: GPL-3.0-only
"""The installation itself: Melodee's installer (vendor/fm1_install.py) as a library, with journal,
loader rules, progress, dry run and German error messages.

Backend: fm1_install.MidoBackend (real ports) or flash.sim.SimBackend (tests, demo).
"""
from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass
from typing import Callable

from ..i18n import _
from ..vendor import fm1_install as fi
from . import package as pk
from .journal import Journal

STEP_PHASE = {"start": "step1", "check": "step1", "loader": "loader", "write": "writing", "reboot": "reboot",
              "done": "done"}

# Log lines by which the UI recognises the dangerous section (InstallDialog: "Do not unplug now")
LOG_WRITING = _("FM-1 is switching to update mode …")
LOG_REBOOT = _("Written – the FM-1 is restarting …")

# what a failure leaves behind (like Melodee's progress.js afterFailure)
SAFE, PAUSED, RESTART = "safe", "paused", "restart"


# Codes that Felucca firmwares show on the FM-1 screen in step 1 (felucca-bsp/melodee/sloop ota.c,
# fm-1.drewr.dev/bootloader-check). The computer only sees that the device stops reading.
DEVICE_CODES = {
    -2: _("package header damaged (CRC)"),
    -3: _("package without update loader or without flash.bin (e.g. m-bootloader installer)"),
    -5: _("CHECK HEAD FAILED: boot area of the package ≠ boot area of the device (nothing written)"),
    -7: _("unknown update loader in the package – refused"),
    -11: _("loader not read back correctly while staging"),
    -13: _("computer did not confirm the end of step 1"),
    -15: _("Felucca 1.6.1: flash.bin not 0x93000 bytes (Open V12/V14)"),
}


def device_code_text(code: int) -> str:
    return "FAILED {code}: {text}".format(code=code, text=DEVICE_CODES.get(code, _("unknown error")))


class FlashError(Exception):
    def __init__(self, code: str, message: str, state: str = SAFE, detail: str = ""):
        super().__init__(message)
        self.code, self.state, self.detail = code, state, detail


MESSAGES = {
    "notfound": _("No FM-1 answered. Check the USB data cable (not a charge-only cable), close other music apps "
                  "and try again."),
    "model": _("The connected device is not an FM-1, or the package is for a different device."),
    "lost": _("The USB connection was lost."),
    "stopped": _("The FM-1 stopped requesting data."),
    "noloader": _("The FM-1 in update mode was not found. Plug the cable in again and choose “Resume” – "
                  "it stays in update mode until the installation is finished."),
    "noreturn": _("The FM-1 did not come back after writing. Switch it off and on again."),
    "mismatch": _("Written, but the FM-1 reports a different firmware."),
    "badreq": _("The FM-1 requested data outside the package – aborted."),
    "loader": _("The FM-1 is waiting in an update mode that does not match this package. Nothing was sent."),
    "cancelled": _("Cancelled before anything was sent. Nothing has changed on the FM-1."),
    "checkhead": _("The running firmware refused the original firmware (CHECK HEAD, “FAILED -5” on the FM-1): "
                   "its boot area differs. Nothing was written, the current firmware keeps running. "
                   "Please do not force another path. The cause is usually a changed boot area (m-bootloader, "
                   "Open V12/V14). Way out: firmware with the new check, or download mode (recovery stage 2)."),
    "stopped1": _("The FM-1 refused the package in step 1. Does the display show “FAILED <number>”? "
                  "-3/-7: unknown loader, -5: boot area differs, -15: Felucca 1.6.1 check (see help)."),
    "busy": _("An installation is already running."),
    "ambiguous": _("Several FM-1 are answering. Please unplug all the others."),
}


# German version of fi.LOADER_HINT (which package the waiting loader accepts)
LOADER_HINT_DE = {
    "ota-FM-1_900": _("It is waiting for a package of the Felucca family (Melodee, SLOOP, Skiff …) – ideally exactly "
                      "the one whose installation was interrupted."),
    "ota-FM-1_015": _("It is waiting for the unmodified original firmware V15 (FM-1_015.fwsc)."),
}


def loader_hint(text: str) -> str:
    if text in LOADER_HINT_DE:
        return LOADER_HINT_DE[text]
    if re.match(r"^ota-FM-1_9\d\d$", text or ""):
        return _("It is waiting for the Felucca package whose installation was interrupted.")
    if re.match(r"^ota-FM-1_0\d\d$", text or ""):
        return _("It is waiting for the original firmware from M-VAVE.")
    if re.match(r"^ota-FM1C_[0-9a-f]{16}$", text or ""):
        return _("This is the update loader of the M-VAVE FM1 Open (build {build}…). It only accepts the package of "
                 "exactly this build; the app sends it nothing – please use the FM1 Open updater from M-VAVE.").format(
                     build=text[9:])
    return _("The app does not know this loader ({loader}).").format(loader=text)


MESSAGES["pinned_lost"] = _("The target device can no longer be found at its USB port. Please leave only the FM-1 "
                             "connected that you want to update, and do not move any cable.")


@dataclass
class Result:
    identity: str
    dry_run: bool = False
    log: tuple = ()
    port: str = ""                # port name of the device after installation (for the resources)


@dataclass
class Pin:
    """pins ONE device across reboots and update mode: USB connection (sysfs path, does not change on
    re-enumeration), otherwise serial number"""
    usb_path: str = ""
    serial: str = ""

    @property
    def usable(self) -> bool:
        return bool(self.usb_path or self.serial)

    def matches(self, d: dict) -> bool:
        if self.usb_path:
            return (d or {}).get("usb_path") == self.usb_path
        if self.serial:
            return (d or {}).get("serial") == self.serial
        return False

    def __str__(self):
        return (_("USB port {path}").format(path=self.usb_path) if self.usb_path
                else _("serial number {serial}").format(serial=self.serial))


def default_details(name: str) -> dict:
    """USB details (sysfs) for a mido port name"""
    from .. import devices
    m = devices.CLIENT_RE.search(name)
    return devices.usb_details(int(m.group(1))) if m else {}


def default_port_filter(name: str) -> bool:
    from .. import devices
    return devices.install_candidate(name)


def default_backend():
    if os.environ.get("FM1C_DEMO") == "1":
        from . import demo
        return demo.backend()
    try:
        b = fi.MidoBackend()
        b.input_names()
        return b
    except ImportError:
        raise FlashError("usage", _("mido/rtmidi is missing for the installation (sudo apt install python3-mido "
                                    "python3-rtmidi)."))


class Engine:
    def __init__(self, backend=None, journal: Journal | None = None, port_filter: Callable[[str], bool] | None = None,
                 log: Callable[[str], None] = print, progress: Callable[[int, int], None] | None = None,
                 cancel: threading.Event | None = None, details: Callable[[str], dict] | None = None):
        self.backend = backend if backend is not None else default_backend()
        self.journal = journal or Journal()
        self.port_filter = port_filter or default_port_filter
        self.details = details or getattr(self.backend, "details", None) or default_details
        self.log = log
        self.progress = progress or (lambda a, b: None)
        self.cancel = cancel or threading.Event()

    # ------------------------------------------------------------ search
    def updater(self, port: str = "", pin: Pin | None = None) -> fi.Updater:
        if pin is not None and pin.usable:
            # only ports of exactly this USB device (also after reboots, whatever name it appears under)
            def flt(name):
                return pin.matches(self._details(name))
            return fi.Updater(self.backend, None, port_filter=flt, cancelled=self.cancel.is_set)
        return fi.Updater(self.backend, port or None, port_filter=self.port_filter, cancelled=self.cancel.is_set)

    def _details(self, name: str) -> dict:
        try:
            return self.details(name) or {}
        except Exception:  # noqa: BLE001
            return {}

    def candidate_ports(self) -> list[str]:
        """all ports that can be an FM-1 (including in update mode)"""
        return [n for n in self.backend.input_names() if self.port_filter(n)]

    def pin_for(self, port: str) -> Pin | None:
        """Pin from a port name (or part of one), if the USB details are unambiguous"""
        all_names = self.backend.input_names()
        names = [n for n in all_names if n == port] or [n for n in all_names if port and port.lower() in n.lower()]
        if len(names) != 1:
            return None
        d = self._details(names[0])
        pin = Pin(d.get("usb_path", ""), d.get("serial", ""))
        return pin if pin.usable else None

    def current_port(self, pin: Pin) -> str:
        """Port name under which the pinned device currently appears ("" = not present / ambiguous)"""
        names = [n for n in self.backend.input_names() if pin.matches(self._details(n))]
        return names[0] if len(names) == 1 else ""

    def probe(self, port: str = "", want=None):
        """Handshake (does not disturb the device) -> Identity or None"""
        up = self.updater(port)
        dev = up.find(want, only_port=bool(port))
        if dev:
            dev.link.close()
            return dev.id
        return None

    def find_device(self, port: str = ""):
        """the device on the given port; without a port: the only responding FM-1"""
        up = self.updater(port)
        if port:
            dev = up.find(only_port=True)
            if dev:
                return up, dev
        found = []
        for name in up.candidates(False):
            out = fi.pair_output(name, self.backend.output_names())
            if not out:
                continue
            try:
                link = self.backend.open(name, out)
            except Exception:
                continue
            ident = fi.handshake(link, 2)
            if ident:
                found.append(fi.Device(link, ident, name))
            else:
                link.close()
        if len(found) > 1:
            for d in found:
                d.link.close()
            raise FlashError("ambiguous", MESSAGES["ambiguous"])
        if not found:
            raise FlashError("notfound", MESSAGES["notfound"])
        return up, found[0]

    # ------------------------------------------------------------ writing
    def write(self, info: pk.PackageInfo, port: str = "", label: str = "", device_id: str = "",
              dry_run: bool = False, expect_sha: str = "", allow_resume: bool = True, pin: Pin | None = None) -> Result:
        """write a package to the FM-1 (or, in a dry run, only check everything).

        pin: pin the target device by USB connection/serial number (otherwise derived from `port`, if possible).
        If several FM-1 are connected and the target cannot be pinned, nothing is sent."""
        rule = info.loader_rule
        if rule is None:
            raise FlashError("badpkg", _("The loader family of this package is unknown – it is not sent."))
        if expect_sha and info.sha256 != expect_sha.lower():
            raise FlashError("badpkg", _("The checksum of the package does not match – it is not sent."))
        if info.damage:
            raise FlashError("badpkg", _("The package is damaged ({damage}).").format(damage=info.damage))
        label = label or info.product
        if pin is None or not pin.usable:
            pin = self.pin_for(port) if port else None
        if pin is not None:
            up = self.updater(pin=pin)
            dev = up.find(only_port=False)
            if dev is None:
                raise FlashError("notfound", MESSAGES["pinned_lost"])
            self.log(_("Target device pinned: {pin}").format(pin=pin))
        else:
            if len(self.candidate_ports()) > 1:
                raise FlashError("ambiguous", _("Please leave only the FM-1 connected that you want to update. "
                                                "Several FM-1 are connected, and the app cannot pin the target device "
                                                "unambiguously – nothing was sent."))
            up, dev = self.find_device(port)
            # after the reboots, ask ports of the same device first ("Melodee FM-1 Update" -> "Melodee FM-1")
            up.port = re.split(r"\s+update\b|:", dev.name, maxsplit=1, flags=re.I)[0].strip() or dev.name
        self.log(_("Device: {identity} at “{port}”").format(identity=dev.id.text, port=dev.name))
        if fi.model_of(dev.id.text) != info.model:
            dev.link.close()
            raise FlashError("model", _("The device reports {identity}, the package is for {product}.").format(
                identity=dev.id.text, product=info.product))
        if dev.id.loader:
            if not rule(dev.id.text):
                dev.link.close()
                raise FlashError("loader", _("The FM-1 is waiting in update mode as “{identity}” – but this package "
                                             "({product}) belongs to {loader}. Nothing was sent. {hint}").format(
                                                 identity=dev.id.text, product=info.product, loader=rule.label,
                                                 hint=loader_hint(dev.id.text)), detail=dev.id.text)
            if not allow_resume:
                dev.link.close()
                raise FlashError("loader", _("The FM-1 is waiting in update mode – please resume via recovery."))
        if dry_run:
            dev.link.close()
            how = _("resume in update mode") if dev.id.loader else _("step 1 + step 2")
            self.log(_("Dry run: {label} ({product}, {family}) would go to {identity} – {how}. Nothing sent.").format(
                label=label, product=info.product, family=info.family, identity=dev.id.text, how=how))
            return Result(dev.id.text, dry_run=True)
        try:
            self.journal.acquire()
        except Exception as e:
            dev.link.close()
            raise FlashError("busy", str(e))
        try:
            try:
                kept = self.journal.keep_package(info.path, info.sha256)
                self.journal.begin(target=label, product=info.product, sha256=info.sha256, family=info.family,
                                   package=str(kept), source=str(info.path), port=dev.name, device_id=device_id,
                                   start_identity=dev.id.text, loader_hint=info.resumable_hint,
                                   resume=dev.id.loader, pid=os.getpid())
            except BaseException:
                dev.link.close()            # nothing sent: do not leave the port open
                raise
            phase = {"p": "loader" if dev.id.loader else "prepared"}
            total = max(1, len(info.image))

            def step(k, a=None):
                ph = STEP_PHASE.get(k)
                if ph and ph != phase["p"]:
                    phase["p"] = ph
                    self.journal.update(phase=ph)
                if k == "start":
                    self.log(_("Starting the update to {target} …").format(target=a))
                elif k == "check":
                    self.progress(min(int(a or 0), 100), 0)
                elif k == "loader":
                    self.log(LOG_WRITING)
                elif k == "write":
                    self.progress(int(a or 0), 100)
                elif k == "reboot":
                    self.log(LOG_REBOOT)
                elif k == "done":
                    self.log(_("Done: the FM-1 reports {identity}").format(identity=a))
            try:
                ident = up.install(dev, info.image, info.product, step, rule)
            except fi.InstallError as e:
                raise self._translate(e, phase["p"], up, info)
            except FlashError:
                raise
            except Exception as e:  # noqa: BLE001 – unexpected error (MIDI driver, program bug): journal
                # must still be correct so that the rescue afterwards offers the right thing
                raise self._unexpected(e, phase["p"])
            self.journal.finish("done")
            return Result(ident, port=getattr(up, "last_port", "") or "")
        except FlashError as e:
            self.journal.finish("failed" if e.state != SAFE else "safe", str(e), keep_active=e.state == PAUSED)
            raise
        finally:
            self.journal.release()

    @staticmethod
    def _unexpected(e: BaseException, phase: str) -> FlashError:
        detail = f"{type(e).__name__}: {e}"
        if phase == "reboot":
            return FlashError("internal", _("Unexpected error after writing. The FM-1 probably starts normally – "
                                            "plug the cable in again and check which firmware it reports."), RESTART, detail)
        if phase in ("loader", "writing"):
            return FlashError("internal", _("Unexpected error while writing. The FM-1 is probably waiting in "
                                            "update mode: check the cable and resume with the same package on the "
                                            "“Recovery” page."), PAUSED, detail)
        return FlashError("internal", _("Unexpected error before writing. Nothing was written, the previous "
                                        "firmware keeps running."), SAFE, detail)

    def _translate(self, e: fi.InstallError, phase: str, up: fi.Updater, info: pk.PackageInfo) -> FlashError:
        code = e.code
        staged = phase in ("loader", "writing")
        if code == "stopped" and phase in ("prepared", "step1") and info.family == "stock":
            fl = next((off for typ, _n, off, _s in pk.ufw_check(info.image)[1] if typ == 0), None)
            if fl is not None and fl <= up.last_read < fl + 0x4000:
                return FlashError("checkhead", MESSAGES["checkhead"], SAFE, str(e))
        if code in ("noreturn", "mismatch") or phase == "reboot":
            state = RESTART
        elif staged or code == "noloader":
            state = PAUSED
        else:
            state = SAFE
        msg = MESSAGES.get(code, str(e))
        if code == "stopped" and phase in ("prepared", "step1"):
            msg = MESSAGES["stopped1"]
        if state == SAFE and code not in ("cancelled", "loader"):
            msg = _("{message} Nothing was written.").format(message=msg)
        if state == PAUSED and code == "loader":
            msg = _("The FM-1 started an update loader that does not match this package. Nothing was "
                    "written; the FM-1 is now waiting in update mode. On the “Recovery” page only a "
                    "package that belongs to this loader is sent.")
        elif state == PAUSED and code != "noloader":
            msg = _("{message} The FM-1 is waiting in update mode: check the cable and resume with the same "
                    "package (“Recovery” page).").format(message=msg)
        if code == "mismatch":
            msg += f" ({e})"
        return FlashError(code, msg, state, str(e))
