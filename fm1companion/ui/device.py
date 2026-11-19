# SPDX-License-Identifier: GPL-3.0-only
"""The active FM-1: detection, connection, profile and safe device access."""
from __future__ import annotations

import logging
import os
import sys
import time

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QInputDialog, QLabel, QListWidget, QListWidgetItem,
                             QVBoxLayout, QWidget)

from .. import config, firmware
from ..i18n import N_, _
from ..devices import (PortInfo, Profile, ProfileStore, firmware_from_port, identifying_serial, profile_unit_ids,
                       scan_ports, serial_learnable, usable_serial)
from ..protocol import Cmd, Info, parse_info
from ..transport import Link, LinkError, MidoTransport, SubprocessTransport
from .common import JobDialog, run_job, show_error

DEMO = os.environ.get("FM1C_DEMO") == "1"
UNKNOWN_CHOICE = "?"        # "don't know" – remembered so the app only asks once


class FolderError(OSError):
    """Folder can't be created/written – the text is a ready (translated) message"""


def folder_error_text(path, e: OSError) -> str:
    import errno
    why = {errno.EACCES: _("no write permission"), errno.EPERM: _("no write permission"),
           errno.EROFS: _("the drive is write-protected"), errno.ENOSPC: _("the drive is full"),
           errno.ENAMETOOLONG: _("the name is too long"),
           errno.ENOENT: _("the folder is missing (drive disconnected?)")}.get(
        getattr(e, "errno", None), _("error while writing"))
    return _("The folder “{path}” cannot be created or written: {reason}. Please check the permissions or choose "
             "another main folder in the settings. (Technical: {error})").format(path=path, reason=why,
                                                                                error=e.strerror or e)


def handshake(link, tries: int = 2, timeout: float = 1.0) -> str:
    """Update handshake over an open link (background thread). -> identifier or ""."""
    import queue as _q
    from ..vendor import fm1_install as fi
    q: _q.Queue = _q.Queue()

    def listen(data, direction):
        if direction == "in" and data and data[0] == 0xF0:
            q.put(bytes(data))
    link.add_listener(listen)
    try:
        for _try in range(tries):
            if not link.send_midi(list(fi.HS_QUERY), force=True):
                return ""
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                try:
                    pkt = q.get(timeout=max(0.01, end - time.monotonic()))
                except _q.Empty:
                    break
                ident = fi.parse_identity(pkt)
                if ident:
                    if getattr(ident, "flash", 0):        # a firmware that tells its flash chip (fm1-base 19bf320)
                        try:
                            link.hs_flash = ident.flash
                        except AttributeError:
                            pass
                    return ident.text
        return ""
    finally:
        link.remove_listener(listen)


HORTATOR_INFO = [0xF0, 0x7D, 0x48, 0x54, 1, 0, 0xF7]   # Hortator's own editor protocol (header 7D 48 54, "HT"): INFO


def hortator_info(link, timeout: float = 0.6) -> str:
    """Hortator (DeadActive/hortator, from 1.0.0) answers no Felucca editor command, but its own INFO (web/
    EDITOR_PROTOCOL.md: rc 0, the magic "HORTATOR", the firmware "1.0.1", ...). -> its version, or "" """
    import queue as _q
    q: _q.Queue = _q.Queue()

    def listen(data, direction):
        if direction == "in" and len(data) > 7 and list(data[:5]) == HORTATOR_INFO[:5]:
            q.put(bytes(data))
    link.add_listener(listen)
    try:
        if not link.send_midi(list(HORTATOR_INFO), force=True):
            return ""
        try:
            pkt = q.get(timeout=timeout)
        except _q.Empty:
            return ""
        parts = pkt[7:-1].split(b"\0")
        if pkt[6] != 0 or len(parts) < 2 or parts[0] != b"HORTATOR":
            return ""
        return parts[1].decode("ascii", "replace")
    finally:
        link.remove_listener(listen)


def _probe_loader(port) -> str:
    from ..flash import engine
    try:
        ident = engine.Engine(log=lambda *_a: None).probe(port.in_name)
        return ident.text if ident else ""
    except Exception:  # noqa: BLE001
        return ""


def _probe_update_port(port):
    """an update-mode port: the loader's identity and – only on the port m-bootloader's MIDI MODE shows – who it is
    (flash/mboot.py, command 0x12: read only). -> (identity, mboot.Info | None)"""
    ident = _probe_loader(port)
    from ..flash import mboot
    mb = None
    if mboot.port_candidate(port, ident):
        mb = mboot.identify_port(port)
    return ident, mb


def _demo_update_ports():
    """Demo: devices currently waiting in (simulated) update mode appear as a separate port"""
    try:
        from ..flash import demo
        out = []
        for d in demo.backend().devices:
            if d.online and d.state == "loader":
                out.append(PortInfo(d.port_name, d.port_name, d.port_name, None, serial=d.demo_port + "-ota",
                                    usb_path=d.usb_path, update_mode=True))
        return out
    except Exception:  # noqa: BLE001
        return []


def firmware_candidates(identity: str) -> list[str]:
    """Firmware keys that match an identifier (own experience, catalog, guessing)"""
    out = []
    key, _tag = firmware.guess_firmware(identity)
    if key:
        out.append(key)
    try:
        from .. import catalog as cat          # catalog workshop (if present)
        for e in getattr(cat, "by_product", lambda _i: [])(identity):
            k = getattr(e, "key", "") or (e.get("key") if isinstance(e, dict) else "")
            if k and k not in out:
                out.append(k)
    except Exception:  # noqa: BLE001
        pass
    if identity == "FM-1_900" and len(out) <= 1:
        for k in ("sloop", "orbit"):
            if k not in out:
                out.append(k)
    return out


def firmware_name(key: str) -> str:
    """Display name for a firmware key (built-in, custom, catalog)"""
    if not key or key == UNKNOWN_CHOICE:
        return ""
    sp = firmware.spec(key)
    if sp:
        return sp.name
    try:
        from .. import catalog as cat
        e = cat.load().by_key(key)
        if e:
            return e.name
    except Exception:  # noqa: BLE001
        pass
    from ..studio import FIRMWARES                  # (a firmware known only by its studio twin, e.g. FoMni)
    return FIRMWARES.get(key, {}).get("name") or key


log = logging.getLogger("fm1companion.device")

# How the app knows which profile the connected FM-1 is (DeviceContext.identified_by), for the Overview and the
# header. Only "unit_id" and "flash" are checks by the app; "only" means no other known profile can be this unit.
IDENTIFIED = {
    "unit_id": N_("recognised by its unit ID"),
    "flash": N_("recognised by its flash chip"),
    "only": N_("recognised as the only FM-1 it can be (no unit ID seen)"),
    "new": N_("new to the app"),
    "answer": N_("recognised by your answer – not checked by the app"),
    "install": N_("recognised as the FM-1 the app has just installed on at this USB port – not checked by the app"),
    "port": N_("recognised by USB port only – not certain"),
}
# identified this way, a write first names the unit and asks the owner to check its front (DeviceContext.unit_check)
UNCHECKED = ("answer", "install", "port")


def revision_words(profile) -> str:
    """the unit's hardware revision in words a musician can check on the device itself"""
    from .. import hardware
    rev = hardware.revision(profile)
    if rev == hardware.NEW:
        return _("the newer FM-1 with the M-VAVE logo on the front")
    if rev == hardware.OLD:
        return _("the earlier FM-1 without the logo on the front")
    return _("the app has no record whether it has the logo on the front")


def unit_confirm_text(profile, how: str = "answer") -> str:
    """the mandatory question before a write to a unit that the app did not recognise by itself"""
    why = (_("The app knows this only as the FM-1 it has just installed on at this USB port.") if how == "install"
           else _("The app knows this only from the USB port.") if how == "port"
           else _("The app knows this only from your answer to “Which FM-1 is this?” – the unit reports no unit ID "
                  "and no flash chip it could check."))
    return (_("This is “{name}” – {revision}.").format(name=html_escape(profile.name), revision=revision_words(profile))
            + "<br><br>" + why + " " +
            _("<b>Check the front of the FM-1 you have connected.</b> If it is another one, cancel: nothing is written, "
              "and you can say which FM-1 it is on the Overview."))


def html_escape(text: str) -> str:
    import html
    return html.escape(text or "")


class FirmwareChoiceDialog(QDialog):
    """“Which firmware is running on this FM-1?” – candidates from the catalog plus “don't know”."""

    def __init__(self, parent, identity: str, candidates: list[str], device: str = ""):
        super().__init__(parent)
        self.setWindowTitle(_("Which firmware is running here?"))
        lay = QVBoxLayout(self)
        intro = (_("{device} reports the ID <b>{id}</b>.").format(device=device, id=identity) if device
                 else _("This FM-1 reports the ID <b>{id}</b>.").format(id=identity))
        lab = QLabel(intro + " " + _("Several firmwares use this ID, and the running firmware has no editor "
                                     "protocol the app could ask. Which one is it? The answer is remembered for this "
                                     "device.") + "<br><br><small>" +
                     _("If in doubt, “don't know”: then the app treats every installation as a firmware change "
                       "(with an intermediate step via the original V15).") + "</small>")
        lab.setWordWrap(True)
        lay.addWidget(lab)
        self.list = QListWidget()
        for k in candidates:
            it = QListWidgetItem(firmware_name(k) or k)
            it.setData(Qt.ItemDataRole.UserRole, k)
            self.list.addItem(it)
        it = QListWidgetItem(_("don't know"))
        it.setData(Qt.ItemDataRole.UserRole, UNKNOWN_CHOICE)
        self.list.addItem(it)
        self.list.setCurrentRow(self.list.count() - 1)
        self.list.itemDoubleClicked.connect(lambda *_a: self.accept())
        lay.addWidget(self.list, 1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def choice(self) -> str:
        it = self.list.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else UNKNOWN_CHOICE


def last_firmware_text(p) -> str:
    """what a profile last ran, for display: “Skiff 2.7”, “SLOOP 3 3.4.0”, “FM1 Open V14”, “Original firmware V15” – the
    firmware's name, not its key, and its version once (see firmware_version_text). "" when the profile knows no
    firmware. (What the unit runs NOW may differ: the texts that use this say “last seen”.)"""
    key = getattr(p, "firmware", "") or ""
    name = firmware_name(key) or ("" if key == UNKNOWN_CHOICE else key)
    if not name:
        return ""
    return firmware_version_text(name, getattr(p, "firmware_version", "") or "")


def firmware_version_text(name: str, tag: str) -> str:
    """a firmware's name with its version tag, the version never twice: a Felucca-style tag without its leading “v”
    (“v2.7” -> “Skiff 2.7”), an M-VAVE release number as M-VAVE writes it (“V14” -> “FM1 Open V14”), and nothing
    when the name already carries that version (“Original firmware V15” + “V15”)."""
    import re as _re
    ver = (tag or "").strip()
    m = _re.fullmatch(r"[vV](\d+)", ver)
    ver = f"V{m.group(1)}" if m else _re.sub(r"^[vV](?=\d)", "", ver)
    bare = _re.sub(r"^[vV](?=\d)", "", ver)
    if bare and _re.search(r"(?<![\w.])[vV]?" + _re.escape(bare) + r"(?![\w.])", name):
        ver = ""
    return f"{name} {ver}".strip()


class DeviceContext(QObject):
    changed = pyqtSignal()                 # device/profile/connection changed
    ports_changed = pyqtSignal()
    editor_pause = pyqtSignal(bool)        # True: editor must pause
    message = pyqtSignal(str)
    about_to_disconnect = pyqtSignal(object)  # (link) just before closing – e.g. end hanging notes

    def __init__(self, parent=None):
        super().__init__(parent)
        self.settings = config.load()
        self.store = ProfileStore()
        self.ports: list[PortInfo] = []
        self.vports: list[PortInfo] = []   # virtual devices (studio twin) – never in self.ports
        self.port: PortInfo | None = None
        self.link: Link | None = None
        self.info: Info | None = None
        self.profile: Profile | None = None
        self.hold = False                  # during an update: don't connect automatically
        self.identity = ""                 # handshake identifier (e.g. "FM-1_015", "ota-FM-1_900")
        self.firmware_guess: list[str] = []  # possible firmware keys for the identifier
        self.uboot: list[str] = []         # USB paths of FM-1s in ROM boot mode (rescue level 2)
        self.pending_restore_check = ""    # after a firmware change: offer the old backup
        self.sloop3 = None                 # SLOOP 3 answered its own INFO (sloop3.Info) – no Felucca protocol
        self.mboot = None                  # the port is m-bootloader's MIDI MODE (flash/mboot.Info)
        self._asking = False
        # USB path -> (profile id, time): the unit this app has just installed on there (ui/firmware_page.install_flow)
        self.session_units: dict[str, tuple[str, float]] = {}
        self.unit_candidates: list[Profile] = []  # asked "which FM-1 is this?" and not answered: what it may be
        self.port_answers: dict[str, str] = {}    # port key -> profile id the owner named; holds while the port stays
        self.pending_restore_profile = ""         # the profile the restore offer (pending_restore_check) is meant for
        self.identified_by = ""                   # how the active profile was found: a key of IDENTIFIED ("" = none)
        self._ask_next = ""                       # port key: on the next connection there, ask "which FM-1?" again
        self.window: QWidget | None = None
        # virtual devices (studio twin): list and line are provided by the service (ui/studio_service.py)
        self.virtual_ports = None          # () -> list[PortInfo]
        self.virtual_transport = None      # (PortInfo) -> Transport
        self.virtual_profile = None        # (PortInfo) -> Profile (not in the device store)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.scan)
        self.timer.start(2000)
        QTimer.singleShot(200, self.scan)

    # ------------------------------------------------------------ Status
    @property
    def connected(self) -> bool:
        return self.link is not None and not self.link.closed and self.info is not None

    @property
    def is_virtual(self) -> bool:
        """the active device is the studio twin (not a real FM-1): firmware, rescue, update mode, USB audio
        and automatic backups don't apply to it."""
        return bool(self.port is not None and getattr(self.port, "virtual", False))

    @property
    def midi_connected(self) -> bool:
        """MIDI connection open – no matter which firmware (notes, CC, DX7 SysEx work)"""
        return self.link is not None and not self.link.closed

    @property
    def protocol(self) -> str:
        """"felucca" (editor protocol), "generic" (MIDI + handshake only), "update" (update mode) or """""
        if self.port and (self.port.update_mode or self.identity.lower().startswith("ota-")):
            return "update"
        if self.connected:
            return "felucca"
        if self.midi_connected:
            return "generic"
        return ""

    @property
    def firmware_key(self) -> str:
        """Key of the running firmware – "" if unknown or ambiguous (never guessed)"""
        if self.profile and self.profile.firmware and self.profile.firmware != UNKNOWN_CHOICE:
            return self.profile.firmware
        if self.info:
            return self.info.firmware_name
        if len(self.firmware_guess) == 1 and self.firmware_guess[0] != UNKNOWN_CHOICE:
            return self.firmware_guess[0]
        return firmware_from_port(self.port)            # (FM1 Open's own USB ID / product name: no guess)

    @property
    def device_firmware(self) -> str:
        """the running firmware as the device itself names it (protocol.Info.firmware_name, "sloop3", "" unknown) –
        what decides which commands the app may send (protocol.supports), never a profile's or the user's choice"""
        if self.info:
            from ..protocol import gate_name
            return gate_name(self.info)
        return "sloop3" if getattr(self, "sloop3", None) is not None else ""

    def unsupported(self, feature: str) -> str:
        """"" if the connected firmware does this (or nothing is connected: require() says so), else the tooltip"""
        from ..protocol import supports, unsupported_text
        if not self.midi_connected or supports(self.device_firmware, feature):
            return ""
        return unsupported_text(feature)

    @property
    def firmware_ambiguous(self) -> bool:
        """generic device whose identifier is used by several firmwares, and no choice by the user"""
        return not self.firmware_key and len(self.firmware_guess) > 1

    @property
    def firmware_tag(self) -> str:
        return self.profile.firmware_version if self.profile else ""

    def title(self) -> str:
        if not self.port:
            return _("No FM-1 connected")
        if self.is_virtual:
            from .common import fw_label
            return _("{device} – virtual").format(device=self.port.label) + (f" ({fw_label(self.info.version)})" if self.info else "")
        name = self.profile.name if self.profile else self.port.label      # always the device first, then the firmware
        if self.info:
            from .common import fw_label
            fw = fw_label(self.info.version, self.firmware_key)
        elif self.protocol == "update":
            fw = _("update mode ({id})").format(id=self.identity) if self.identity else _("update mode")
            if getattr(self, "mboot", None) is not None:
                fw = _("m-bootloader {version} – MIDI MODE").format(version=self.mboot.version)
        elif getattr(self, "sloop3", None) is not None and self.midi_connected:
            fw = _("{firmware} (public beta)").format(firmware=self.sloop3.label)
        elif self.midi_connected:
            fw_name = firmware_name(self.firmware_key)
            fw = (fw_name or (_("unknown – please choose") if self.firmware_ambiguous else _("unknown firmware"))) + \
                (f" ({self.identity})" if self.identity else "")
        else:
            fw = "…"
        return f"{name} – {fw}"

    def status_text(self) -> str:
        """short state for toolbar/overview"""
        if self.is_virtual:
            return (_("virtual – the twin in the Studio, not a real FM-1") if self.connected
                    else _("virtual – twin is starting …"))
        p = self.protocol
        if p == "update" and getattr(self, "mboot", None) is not None:
            return _("in m-bootloader's MIDI MODE – no firmware is running (“Firmware+” › SLOOP 3 explains what is "
                     "possible from here)")
        if p == "generic" and getattr(self, "sloop3", None) is not None:
            return _("connected – SLOOP 3: its own editor and backups (pages “Editor” and “Backups”)")
        return {"felucca": _("connected – full access"),
                "generic": _("connected – MIDI only (firmware without editor protocol)"),
                "update": _("in update mode – finish the installation (page “Recovery”)")}.get(p, _("not connected"))

    def device_folder(self, base) -> "os.PathLike":
        """This device's folder under `base` (created). FolderError (ready message) if that isn't possible."""
        sub = self.profile.folder_name if self.profile else "Unbekanntes Gerät"   # folder name on disk  # i18n: ignore
        d = base / sub
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise FolderError(folder_error_text(d, e)) from e
        return d

    # ------------------------------------------------------------ Search
    def scan(self):
        if self.hold:
            return
        if DEMO:
            ports = [PortInfo("demo-in", "demo-out", "Melodee (Demo)", None, serial="DEMO-1", usb_path="demo-usb-1"),
                     PortInfo("demo2-in", "demo2-out", "SLOOP (Demo)", None, serial="DEMO-2", usb_path="demo-usb-2"),
                     PortInfo("demo3-in", "demo3-out", "ORBIT (Demo)", None, serial="DEMO-3", usb_path="demo-usb-3")]
            ports += _demo_update_ports()
        else:
            ports = scan_ports()
            try:
                from ..devices import uboot_present
                self.uboot = uboot_present()
            except Exception:  # noqa: BLE001
                self.uboot = []
        vports = []
        if self.virtual_ports is not None:
            try:
                vports = list(self.virtual_ports())
            except Exception:  # noqa: BLE001 – an error in the studio must not disturb the device search
                vports = []
        keys = [p.key for p in ports]
        vkeys = [p.key for p in vports]
        for k in [k for k in self.port_answers if k not in keys]:
            del self.port_answers[k]     # the port was away: what the owner said about it no longer holds
        if keys != [p.key for p in self.ports] or vkeys != [p.key for p in self.vports]:
            self.ports = ports           # real devices only (firmware, rescue, counting several FM-1s …)
            self.vports = vports         # virtual devices are kept separately (device selection at the top only)
            self.ports_changed.emit()
        if self.port and not self._port_present(ports + vports):
            # One scan without the port is not a disconnect yet: on a Raspberry Pi the port list was seen to miss the
            # FM-1 for a moment without any USB event. The warning comes when the port stays away (or the link is
            # dead). During an installation nothing is delayed: the scan is on hold then, the engine watches itself.
            now = time.monotonic()
            if self._gone_since is None:
                self._gone_since = now
                log.debug("port %s missing in the scan (seen: %s)", self.port.key, keys + vkeys)
            dead = self.link is not None and not self.link.t.alive
            if dead or now - self._gone_since >= self.GONE_GRACE:
                self._gone_since = None
                self.disconnect(_("FM-1 disconnected"))
            else:
                QTimer.singleShot(int(self.GONE_RESCAN * 1000), self.scan)
                return
        elif self._gone_since is not None:
            log.debug("port back after %.1f s – no disconnect", time.monotonic() - self._gone_since)
            self._gone_since = None
        if self.link and not self.link.t.alive:
            self.disconnect(_("Connection lost"))
        if not self.port and ports:         # never connect to a virtual device by itself
            last = self.settings.last_device
            def is_last(p):
                prof = self.store.by_serial(usable_serial(p, ports)) if usable_serial(p, ports) else None
                return bool(prof and prof.id == last) or bool(
                    last in self.store.profiles and p.usb_path and self.store.profiles[last].usb_path == p.usb_path)
            pick = next((p for p in ports if is_last(p)), ports[0])
            self.connect_port(pick)

    GONE_GRACE = 1.5                # s a port may be missing from the scans before “FM-1 disconnected”
    GONE_RESCAN = 0.4               # s until the next look while it is missing
    _gone_since: float | None = None

    def _port_present(self, ports) -> bool:
        """is the active port still in the scan? By its key, or – when the USB details were read differently this
        time (the key holds USB path and serial number) – by the very same MIDI port name."""
        port = self.port
        return any(p.key == port.key or (p.out_name == port.out_name and p.in_name == port.in_name) for p in ports)

    def connect_port(self, port: PortInfo):
        self.disconnect(None)
        self.port = port
        if port.update_mode:
            self.message.emit(_("The FM-1 is in update mode. The page “Recovery” finishes the installation."))
            self.identity = ""
            self._assign_profile()
            self.changed.emit()
            if not DEMO:      # loader identifier: handshake without an open link (doesn't disturb anything)
                run_job(self, lambda w: _probe_update_port(port), on_done=lambda r: self._loader_probed(r, port),
                        on_fail=lambda *a: None)
            else:
                from ..flash import demo
                d = demo.device(port.in_name.replace(" Update", ""))
                self.identity = d.text if d else "ota-FM-1_900"
                self.changed.emit()
            return
        try:
            if getattr(port, "virtual", False):
                if self.virtual_transport is None:
                    raise RuntimeError(_("Studio not available"))
                t = self.virtual_transport(port)
            elif DEMO:
                cmd = os.environ.get("FM1C_DEMO_CMD")
                if cmd:
                    argv = cmd.split("\x1f")
                elif port.serial == "DEMO-2":      # second demo device: SLOOP with 4 sample slots, in the version
                    argv = [sys.executable, "-m", "fm1companion.demo_device", "--samples", "4",   # of the Studio twin
                            "--version", "SLOOP 2.8.5"]
                elif port.serial == "DEMO-3":      # third: firmware without editor protocol (handshake only)
                    argv = [sys.executable, "-m", "fm1companion.demo_device", "--generic", "FM-1_900"]
                else:
                    argv = [sys.executable, "-m", "fm1companion.demo_device"]
                t = SubprocessTransport(argv)
            else:
                t = MidoTransport(port.in_name, port.out_name)
        except Exception as e:  # noqa: BLE001
            self.message.emit(_("The MIDI port cannot be opened: {error}").format(error=e))
            self.port = None
            return
        self.link = Link(t)
        self.link.on_exclusive_change = lambda on: self.editor_pause.emit(on)
        self.changed.emit()
        # results belong to EXACTLY this link: a slow probe job of the previous device (start wait up to
        # STARTUP_GRACE) must not overwrite the new device's identifier or firmware after a device change
        link = self.link
        run_job(self, lambda w: self._probe(w, link), on_done=lambda info: self._probed(info, link),
                on_fail=lambda m, tb: self._probe_failed(m, link))

    STARTUP_GRACE = 20.0            # s: how long a device process (demo/simulation) may take to start

    def _probe(self, w, link=None):
        last = None
        link = link or self.link
        from .. import sloop3 as S3
        port = getattr(self, "port", None)
        if port is not None and S3.PORT_RE.search(getattr(port, "label", "") or ""):
            # "FM-1 SLOOP 3": ask its own INFO first (read only) – it answers no Felucca command, and waiting for
            # those first would cost several seconds
            link.s3info = S3.probe(link, timeout=0.7, tries=2)
            if link.s3info is not None:
                raise LinkError("SLOOP 3")
        wait_started = getattr(link.t, "wait_started", None)
        for _try in range(4):       # the first packet after opening sometimes gets lost
            try:
                info = parse_info(link.request(Cmd.INFO, [], timeout=1.0, retries=0))
                self._hs_identity = link.hs_identity = handshake(link, tries=1, timeout=0.6)
                return info
            except LinkError as e:
                last = e
                # a device process that is still starting is not a device without editor protocol: wait until it
                # runs instead of giving up after ~5 s (otherwise it would stay at “MIDI only” permanently)
                if wait_started is not None and not wait_started(self.STARTUP_GRACE):
                    break
                time.sleep(0.3)
        raise last or LinkError(_("no answer"))

    def _probed(self, info: Info, link=None):
        if link is not None and link is not self.link:
            return                          # outdated: a different device by now (or reconnected)
        self.info = info
        if self.link is not None:
            from ..protocol import gate_name
            self.link.firmware = gate_name(info)        # (what the link may send: transport.Link.request)
        self.identity = (getattr(link, "hs_identity", "") if link is not None else getattr(self, "_hs_identity", "")) \
            or self.identity
        self._assign_profile()
        p = self.profile
        if p:
            p.version_string = info.version
            if self.identity and not self.identity.lower().startswith("ota-") and not self.is_virtual:
                p.identity = self.identity              # what the FM-1 reports now, not what the last install left
            key, tag = firmware.guess_firmware(p.identity, info.version, info.firmware_name)
            if key == "felucca" and p.firmware.startswith("felucca-"):
                key = ""                     # (a Felucca fork chosen for this device, "felucca-mpe": its INFO is Felucca's)
            if key and (not p.firmware or p.firmware != key):
                p.firmware = key
                if tag:
                    p.firmware_version = tag
            elif key and tag and not p.firmware_version:
                p.firmware_version = tag
            p.last_seen = time.time()
            if not self.is_virtual:
                self._note_found(p)
            if not self.is_virtual:          # the twin is never “the last used FM-1”
                self.store.save()
                self.settings.last_device = p.id
                config.save(self.settings)
        self._log_connected()
        self.changed.emit()

    def _log_connected(self):
        """one line in the app log for every FM-1 that was recognised – also one without editor protocol (the
        original firmware), which the log did not mention before. No serial number, no name of the owner's choosing."""
        port, p = self.port, self.profile
        if port is None or getattr(port, "virtual", False):
            return
        log.info("connected: identity %s, firmware %s%s, protocol %s, USB %s at %s, profile %s",
                 self.identity or "-", self.firmware_key or "unknown",
                 f" ({self.info.version})" if self.info else "", self.protocol or "-", port.usb_id or "-",
                 port.usb_path or "-", p.id if p else "none (not said which FM-1 this is)")

    def _probe_failed(self, msg, link=None):
        """no Felucca INFO: try the handshake (doesn't disturb), keep the MIDI connection open"""
        if link is not None and link is not self.link:
            return                          # outdated: a different device by now
        self.info = None
        link = self.link
        if link is None or link.closed:
            return
        if getattr(link, "s3info", None) is not None:       # (SLOOP 3 already answered: nothing else is asked)
            self._sloop3(link.s3info, link)
            return
        from .. import sloop3 as S3
        # none of these changes anything on the device: the update handshake, Hortator's INFO, SLOOP 3's INFO
        def ask(w):
            ident = handshake(link)
            hort = hortator_info(link)
            # SLOOP 3 answers neither of the two: asked only when both stayed unanswered (so a firmware that told its
            # identity – M-VAVE's too – never sees this frame), once, briefly
            s3 = None if ident or hort else S3.probe(link)
            return ident, hort, s3
        run_job(self, ask, on_done=lambda r: self._generic(r[0], link, r[1], r[2]),
                on_fail=lambda m, tb: self._generic("", link))

    def _sloop3(self, s3, link=None, ident: str = ""):
        """SLOOP 3 answered its own INFO: its own firmware key (never SLOOP 2.x's), MIDI stays open. The Felucca
        pages keep treating it as a firmware without their protocol; the Editor and Backups pages look at
        ``ctx.sloop3``."""
        if self.link is None or (link is not None and link is not self.link):
            return
        from .. import sloop3 as S3
        self.info = None
        self.sloop3 = s3
        self.link.firmware = S3.KEY                     # (no Felucca command means anything to it)
        self.identity = ident or ""
        self.firmware_guess = [S3.KEY]
        self._assign_profile()
        p = self.profile
        if p:
            p.firmware, p.firmware_version, p.version_string = S3.KEY, s3.tag, s3.label
            p.identity = ident or ""                # (SLOOP 3 answers no update handshake: an older ID no longer holds)
            p.last_seen = time.time()
            self._note_found(p)                     # (installed outside the app: the history says so)
            self.store.save()
            self.settings.last_device = p.id
            config.save(self.settings)
        self.message.emit(
            _("FM-1 connected – {firmware} (public beta). Its own web editor is on the page “Editor”, backups of "
              "its project, sounds and banks on the page “Backups”; the other device pages need the Felucca editor "
              "protocol, which SLOOP 3 does not speak.").format(firmware=s3.label)
            if S3.version_checked(s3.version) else
            _("FM-1 connected – {firmware} (public beta). The app was checked with SLOOP 3 {versions}, not with this "
              "version: the editor and backups may not fit it.").format(
                firmware=s3.label, versions=", ".join(S3.CHECKED_VERSIONS)))
        self._log_connected()
        self.changed.emit()

    def _generic(self, ident: str, link=None, hortator: str = "", s3=None):
        if self.link is None or (link is not None and link is not self.link):
            return
        if s3 is not None:
            self._sloop3(s3, link, ident)
            return
        self.link.firmware = ""                         # (no editor protocol: none of its commands is sent)
        self.identity = ident or ""
        self._assign_profile()
        p = self.profile
        choice = p.firmware_choice.get(ident, "") if p and ident else ""
        cands = firmware_candidates(ident) if ident else []
        if not cands and firmware_from_port(self.port):
            cands = [firmware_from_port(self.port)]             # (FM1 Open's own USB ID / product name)
        if hortator:                                # (Hortator told its name over its own protocol: no choice needed)
            guess = ["hortator"]
            if p:
                p.firmware, p.firmware_version = "hortator", "v" + hortator.lstrip("vV")
        elif choice and choice != UNKNOWN_CHOICE:
            guess = [choice]
        else:
            guess = cands
        self.firmware_guess = guess
        if p and hortator:
            p.identity = ident or p.identity
            p.last_seen = time.time()
            self.store.save()
        elif p:
            if choice and choice != UNKNOWN_CHOICE:
                p.firmware = choice
            elif len(guess) == 1:
                if p.firmware != guess[0]:
                    p.firmware, p.firmware_version = guess[0], firmware.guess_firmware(ident)[1] or p.firmware_version
            elif ident and not self._installed_here(p, ident):
                # ambiguous: guess nothing (an earlier Felucca firmware of this device no longer applies). A unit that
                # answered nothing at all tells nothing about its firmware: then the profile keeps what it knows
                p.firmware, p.firmware_version = "", ""
            p.identity = ident or p.identity
            p.last_seen = time.time()
            self._note_found(p)
            self.store.save()
        if ident and self.firmware_key == "fm1-open":   # (M-VAVE's FM1 Open: its own file protocol)
            self.message.emit(_("FM-1 connected ({id}) – FM1 Open: MIDI, presets via SysEx, its sample bank (page "
                                "“Samples”) and the Studio (fetch, write back) work; the other pages need the editor "
                                "protocol.").format(id=ident))
        elif ident:
            self.message.emit(_("FM-1 connected ({id}) – this firmware has no editor protocol: MIDI, presets via "
                                "SysEx and firmware installation work, backups and projects do not.").format(id=ident))
        else:
            self.message.emit(_("The device answers neither editor commands nor the update ID. The "
                                "MIDI connection stays open."))
        self._log_connected()
        self.changed.emit()
        if self.firmware_ambiguous and p and ident and not choice:
            QTimer.singleShot(300, lambda port=self.port: self.ask_firmware(port))

    @staticmethod
    def _installed_here(p, ident: str) -> bool:
        """did the app itself last install this firmware with exactly this identifier?"""
        hist = [h for h in (p.firmware_history or []) if isinstance(h, dict)]
        return bool(p.firmware and hist and hist[-1].get("firmware") == p.firmware
                    and hist[-1].get("identity") == ident and hist[-1].get("how") != "found")

    @staticmethod
    def _note_found(p):
        """the FM-1 runs another firmware than the device's history ends with – it was installed outside the app
        (or before the app knew the device): the history gets an entry "found", so it does not keep telling the
        old story until the next installation. Only a recognised firmware, and only when it changed."""
        key = p.firmware if p.firmware and p.firmware != UNKNOWN_CHOICE else ""
        hist = p.firmware_history if isinstance(p.firmware_history, list) else None
        if not key or hist is None:
            return False
        last = next((h for h in reversed(hist) if isinstance(h, dict)), None)
        if last is not None and last.get("firmware") == key:
            return False
        hist.append({"time": time.time(), "firmware": key, "name": firmware_name(key), "version": p.firmware_version,
                     "identity": p.identity, "how": "found"})
        return True

    def ask_firmware(self, port=None, force: bool = False) -> str:
        """ask once which firmware is behind an ambiguous identifier (remembered per device).
        force=True (Overview › Firmware: “change”): ask again although an answer is stored – all candidates of the
        identifier, the stored answer preselected; closing the dialog changes nothing (-> "")."""
        if port is not None and self.port is not port:
            return ""
        cands, current = list(self.firmware_guess), ""
        if force:
            cands = firmware_candidates(self.identity) if self.identity else []
            current = self.profile.firmware_choice.get(self.identity, "") if self.profile else ""
            if current and current != UNKNOWN_CHOICE and current not in cands:
                cands.append(current)
        if self._asking or not self.identity or not (cands if force else self.firmware_ambiguous):
            return ""
        self._asking = True
        try:
            dlg = FirmwareChoiceDialog(self.window, self.identity, cands,
                                       self.profile.name if self.profile else "")
            for i in range(dlg.list.count() if current else 0):
                if dlg.list.item(i).data(Qt.ItemDataRole.UserRole) == current:
                    dlg.list.setCurrentRow(i)
            ok = bool(dlg.exec())
            key = dlg.choice() if ok else UNKNOWN_CHOICE
        finally:
            self._asking = False
        if port is not None and self.port is not port:
            return ""
        if force and not ok:
            return ""
        self.choose_firmware(key)
        return key

    def choose_firmware(self, key: str):
        """the user specifies which firmware is behind this identifier (remembered per device).
        UNKNOWN_CHOICE ("?") = “don't know”: is remembered (no further question), the firmware stays unknown."""
        if self.profile and self.identity:
            self.profile.firmware_choice[self.identity] = key
            self.profile.firmware = "" if key == UNKNOWN_CHOICE else key
            if key == UNKNOWN_CHOICE:
                self.profile.firmware_version = ""
            self.store.save()
        if key != UNKNOWN_CHOICE:
            self.firmware_guess = [key]
            if self.profile:
                self.message.emit(_("Remembered: {device} runs {firmware} (ID {id}).").format(
                    device=self.profile.name, firmware=firmware_name(key), id=self.identity))
            else:
                self.message.emit(_("Remembered: this FM-1 runs {firmware} (ID {id}).").format(
                    firmware=firmware_name(key), id=self.identity))
        else:
            if self.identity:                        # (after "change" from a firmware back to "don't know")
                self.firmware_guess = firmware_candidates(self.identity)
            self.message.emit(_("Remembered: firmware unknown – installations count as a firmware change (with "
                                "V15 intermediate step)."))
        self.changed.emit()

    def _loader_probed(self, res, port=None):
        ident, mb = res if isinstance(res, tuple) else (res, None)
        if port is not None and self.port is not port:
            return                          # outdated: another device by now
        if self.port and self.port.update_mode and (ident or mb is not None):
            self.identity = ident or self.identity
            self.mboot = mb
            if mb is not None:
                self._unit_by_id(mb.uid)
                from .. import hardware             # the flash chip m-bootloader read: kept with the device
                seen, rec = mb.revision, hardware.revision(self.profile)
                if self.profile is not None and (getattr(self, "identified_by", "") not in UNCHECKED or hardware.UNKNOWN in (seen, rec)
                                                 or seen == rec) \
                        and hardware.note_flash_id(self.profile, mb.jedec & 0xFFFFFF, "identity"):
                    self.store.save()
                self.message.emit(
                    _("This FM-1 is in m-bootloader's MIDI MODE (m-bootloader {version}, flash chip {chip}): no "
                      "firmware is running. “Firmware+” › SLOOP 3 explains what is possible from here; OCT−, OCT+, "
                      "PLAY or REC on the FM-1 go back to its menu.").format(version=mb.version, chip=mb.chip))
            self.changed.emit()

    def _assign_profile(self):
        """Find the profile for the device (devices.identifying_serial, _match_profile): by its own serial number,
        by what the app itself just did at this USB port, by USB port and identity only when no other profile can be
        this unit – otherwise ask, never guess."""
        port = self.port
        if not port:
            return
        if getattr(port, "virtual", False) and self.virtual_profile is not None:
            self.profile = self.virtual_profile(port)      # own profile, never in the device store
            return
        self.unit_candidates = []
        p = self._match_profile(port)
        if self.pending_restore_check and (p is None or p.id != self.pending_restore_profile):
            # "Back to your data?" is only for the very device the firmware was installed on: this is another profile,
            # or nobody said which one it is – no backup of one FM-1 is offered to another
            self.pending_restore_check = ""
        if p is None:                          # the owner did not say which unit this is: no profile, nothing guessed
            self.profile = None
            return
        learn = serial_learnable(port, self.ports or [port], self.identity)
        if learn and not p.serial and not self.store.by_unit_id(learn):
            # the same FM-1 shows a serial number under the original firmware and none under a Felucca-family
            # firmware: the profile learns it (as it learns the USB port) – no second profile for the same unit
            p.serial = learn
            self.store.save()
        if port.usb_path and p.usb_path != port.usb_path:
            p.usb_path = port.usb_path
            self.store.save()
        product = "" if port.update_mode or getattr(port, "virtual", False) else (port.product or port.label or "")
        if product and getattr(p, "usb_product", "") != product:
            p.usb_product = product            # (what this unit shows here: the next match is judged against it)
            self.store.save()
        flash = getattr(self.link, "hs_flash", 0) if self.link is not None else 0
        if flash and not getattr(port, "virtual", False):      # the firmware told the flash chip: keep it (hardware.py)
            from .. import hardware
            seen, rec = hardware.revision_of_id(flash), hardware.revision(p)
            if getattr(self, "identified_by", "") in UNCHECKED and hardware.UNKNOWN not in (seen, rec) and seen != rec:
                # the chip contradicts the profile that only an answer (or the USB port) named: probably the wrong
                # profile – nothing is recorded on it, and every write is blocked until it is settled (unit_check)
                log.warning("flash chip %06X contradicts profile %s (%s, %s): not recorded", flash, p.id, rec,
                            self.identified_by)
            elif hardware.note_flash_id(p, flash, "identity"):
                self.store.save()
        self.profile = p

    def _unit_by_id(self, uid: str):
        """m-bootloader's MIDI MODE told the flash chip's unique ID (8 bytes) – the one thing about a unit that no
        firmware changes. A profile that has this ID (or whose serial number under the original firmware is this
        ID, in either byte order: M-Flasher shows the unit ID that the original V15 uses as USB serial number) is
        this unit, whatever USB port it is at. A profile found another way learns the ID – unless it knows another
        unit ID: then it is another FM-1, and the device stays without a profile."""
        from ..devices import unit_id_forms
        uid = (uid or "").lower()
        if len(uid) != 16 or not unit_id_forms(uid) or uid in ("0" * 16, "f" * 16):
            return
        hit = self.store.by_unit_id(uid)
        if len(hit) > 1:
            return                              # (two profiles of one unit: the owner merges them on "Devices")
        if hit:
            if self.profile is not hit[0]:
                self.profile = hit[0]
                self.unit_candidates = []
            self.identified_by = "unit_id"
        p = self.profile
        if p is None:
            return
        # (only a number of the same kind can contradict: 16 hex digits, as the original firmware and m-bootloader
        # report it – the app's simulated devices have other serial numbers)
        if not hit and any(len(f) == 16 and all(ch in "0123456789abcdef" for ch in f) for f in profile_unit_ids(p)):
            if getattr(self, "identified_by", "") == "unit_id":
                # (the port's own number named the profile in this very sighting – two IDs that disagree: nothing is
                # learned, nothing dropped; seen only with the app's simulated devices)
                log.warning("unit ID seen in MIDI MODE differs from the port's serial number (profile %s)", p.id)
                return
            log.warning("unit ID seen in MIDI MODE is not the one of profile %s: not that unit", p.id)
            self.unit_candidates = [c for c in self.store.profiles.values() if not profile_unit_ids(c)]
            self.profile = None
            self.identified_by = ""
            return
        if (p.unit_id or "").lower() != uid:      # (kept as m-bootloader reads it)
            p.unit_id = uid
            self.store.save()

    SESSION_UNIT_SECS = 600.0       # how long "the app has just installed on the unit at this USB port" holds

    def note_installed(self, profile, usb_path: str):
        """the app has just written a firmware to `profile`'s unit at this USB port: when it re-appears there (other
        identity, other serial number, other name), it is still this unit – never a new profile"""
        if profile is not None and usb_path:
            self.session_units[usb_path] = (profile.id, time.time())

    def _new_profile(self, name: str = "") -> Profile:
        """a new profile whose default name is neither a profile's nor a folder's in the backup folder (the backups
        of a forgotten device – "FM-1 A" – must not become this device's)"""
        try:
            folders = [d.name for d in self.settings.backups_dir.iterdir() if d.is_dir()]
        except (OSError, AttributeError):
            folders = []
        return self.store.create(name, folders=folders)

    def _match_profile(self, port: PortInfo, ask_again: bool = False) -> Profile | None:
        """the profile of the FM-1 at `port`; sets self.identified_by (IDENTIFIED). In this order:
        1. its unit ID (the original firmware's own serial number) is in a profile: that one ("unit_id").
        2. which profiles can it be? Every profile, except those that positive evidence rules out:
           * it shows its own unit ID and the profile knows another one;
           * a flash ID was read for this sighting (a firmware's identity answer, ROM boot mode at this USB port in
             this session) and the profile's chip was READ as the other one;
           * the profile's unit is connected at another USB port right now, recognised there by its unit ID.
           Not evidence: the firmware, the identity, the USB port, an answer given at an earlier plug-in – a unit's
           firmware changes outside the app (M-Flasher, a firmware's own updater), and two units can run the same
           one. A profile WITH a unit ID stays a candidate for a sighting without one: the number disappears with
           the original firmware.
        3. the app itself has just installed on a unit at this USB port (this session, or the journal's unfinished
           two-step switch) and it is a candidate: that unit ("install").
        4. no candidate: a new profile ("new").
        5. the owner already said which one it is for this port, and the port has not been away since ("answer").
        6. exactly one candidate, it shows no unit ID, and nothing contradicts that profile (same USB port, identity,
           product name, SLOOP 3 or not, flash chip): that one ("only", or "flash" when a read flash ID ruled out
           the others).
        7. everything else is asked ("Which FM-1 is this?") – with two or more candidates EVERY time, never guessed
           by port. None: not answered (unit_candidates says what it may be; the strictest of them counts for the
           hardware rule). In update mode nothing is asked: no profile then.
        ask_again: the owner asked to be asked (Overview), or a read flash ID contradicted the answer: only a unit ID
        decides without the question."""
        from .. import hardware
        from ..devices import unit_id_forms
        store, usb, ident = self.store, port.usb_path or "", self.identity or ""
        self.identified_by = ""
        if getattr(self, "_ask_next", "") == port.key:
            ask_again, self._ask_next = True, ""
        serial = identifying_serial(port, self.ports or [port], ident)
        hits = store.by_unit_id(serial) if serial else []
        if len(hits) == 1:
            self.identified_by = "unit_id"
            return hits[0]
        own = serial_learnable(port, self.ports or [port], ident)
        flash_rev = DeviceContext._sighting_revision(self, port)
        elsewhere = DeviceContext._units_elsewhere(self, port)
        by_flash = []

        def excluded(c):
            if c in elsewhere or (own and profile_unit_ids(c) and not unit_id_forms(own) & profile_unit_ids(c)):
                return True
            if flash_rev != hardware.UNKNOWN and hardware.was_read(c) and hardware.revision(c) != flash_rev:
                by_flash.append(c)
                return True
            return False
        cands = hits if hits else [c for c in store.profiles.values() if not excluded(c)]
        pid = None
        if usb and usb in self.session_units:
            pid, when = self.session_units[usb]
            if time.time() - when > self.SESSION_UNIT_SECS:
                pid = None
            if pid is None or not port.update_mode:
                del self.session_units[usb]    # holds for the first appearance after the installation only
        if pid is None and usb and not getattr(port, "virtual", False):
            try:
                from ..flash import journal as fj
                ch = fj.Journal().chain or {}
                if ch.get("usb_path") == usb:
                    pid = ch.get("device_id")
            except Exception:  # noqa: BLE001
                pid = None
        if not ask_again and pid and pid in store.profiles and store.profiles[pid] in cands:
            self.identified_by = "install"
            return store.profiles[pid]
        if not cands or (own and port.client is None and not port.usb_id):
            # (the second case: one of the app's own simulated devices with a number no profile has – a new
            # simulated device, there is nothing to ask)
            self.identified_by = "new"
            return self._new_profile()
        said = store.profiles.get(self.port_answers.get(port.key, ""))
        if not ask_again and said is not None and said in cands:
            # the owner said so for this port during this plug-in (the answer ends when the port goes away)
            self.identified_by = "answer"
            return said
        here = [c for c in cands if usb and c.usb_path == usb]
        if not own and not ask_again and len(cands) == 1:
            unknown = not ident or ident.lower().startswith("ota-")
            # The USB port alone is no proof, not even for the only candidate. What the unit shows now must not
            # contradict what the profile last was – and no installation by the app explains a difference (that
            # case was settled above):
            # * it runs SLOOP 3 (its own INFO) and the profile last ran something else, or the other way round
            #   (it answers the Felucca editor or an update identity, which SLOOP 3 never does);
            # * another USB product name than the profile last showed while its firmware ran;
            # * another identity;
            # * a flash chip was read that is not the one recorded for the profile.
            # Then it may be another FM-1, or this one with a firmware installed outside the app: asked.
            product = "" if port.update_mode else (port.product or port.label or "")
            # (SLOOP 3 names itself "FM-1 SLOOP 3" on USB: that counts even when its INFO answer got lost – a unit
            # that has just come up may not answer at once, and then it must not be taken by its USB port alone)
            from .. import sloop3 as S3
            s3 = getattr(self, "sloop3", None) is not None or bool(product and S3.PORT_RE.search(product))
            felucca = getattr(self, "info", None) is not None or not unknown
            c = cands[0]
            contradicts = bool((s3 and c.firmware and c.firmware != "sloop3") or (felucca and c.firmware == "sloop3")
                               or (product and c.usb_product and c.usb_product != product)
                               or (not unknown and c.identity and c.identity != ident)
                               or (flash_rev != hardware.UNKNOWN and hardware.revision(c) not in
                                   (hardware.UNKNOWN, flash_rev)))
            if (c in here or not usb) and not contradicts:
                self.identified_by = "flash" if by_flash else "only"
                return c
        self.unit_candidates = list(cands)
        if port.update_mode:
            # an FM-1 waiting in update mode is not the moment for a question: no profile is assumed; the Recovery
            # page judges the waiting unit by itself (strictest candidate, RecoveryPage.waiting_unit)
            return None
        fits = [c for c in cands if getattr(self, "sloop3", None) is not None and c.firmware == "sloop3"]
        guess = (fits or here or [c for c in cands if ident and c.identity == ident]
                 or [c for c in cands if c.id == self.settings.last_device] or cands)[0]
        p = self._ask_profile(cands, guess)
        if p is not None:
            self.unit_candidates = []
            self.port_answers[port.key] = p.id
            self.identified_by = "answer" if p in cands else "new"
        return p

    def _sighting_revision(self, port) -> str:
        """the flash chip read for the unit at this port right now: told in this connection's identity answer
        (fm1-base builds), or read in ROM boot mode at this USB port in this session -> "new" | "old" | "unknown" """
        from .. import hardware
        flash = getattr(getattr(self, "link", None), "hs_flash", 0) or 0
        rev = hardware.revision_of_id(flash) if flash and not getattr(port, "virtual", False) else hardware.UNKNOWN
        if rev == hardware.UNKNOWN:
            rev = hardware.session_revision(getattr(port, "usb_path", "") or "")
        return rev

    def _units_elsewhere(self, port) -> list:
        """profiles whose unit is connected at ANOTHER port right now and recognised there by its unit ID"""
        out = []
        for q in self.ports or []:
            if q is port or q.key == port.key:
                continue
            s = identifying_serial(q, self.ports)
            hits = self.store.by_unit_id(s) if s else []
            if len(hits) == 1 and hits[0] not in out:
                out.append(hits[0])
        return out

    def profile_for_port(self, port: PortInfo) -> Profile | None:
        """without asking: which profile (probably) belongs to this port?"""
        if self.port and port.key == self.port.key and self.profile:
            return self.profile
        if getattr(port, "virtual", False):
            return self.virtual_profile(port) if self.virtual_profile is not None else None
        serial = usable_serial(port, self.ports)
        if serial and self.store.by_serial(serial) is not None:
            return self.store.by_serial(serial)
        # (a profile with another serial number is another FM-1; one without may be this unit)
        return next((p for p in self.store.profiles.values()
                     if port.usb_path and p.usb_path == port.usb_path and not (serial and p.serial)), None)

    def _ask_profile(self, cands: list[Profile], guess: Profile | None = None) -> Profile | None:
        """ask which of the known devices this FM-1 is (or a new one). None: not answered – nothing is assumed."""
        if self._asking:
            return None
        self._asking = True
        try:
            names = [c.name + (" " + _("(last seen: {firmware})").format(firmware=last_firmware_text(c))
                               if last_firmware_text(c) else "") for c in cands]
            names.append(_("A new FM-1 that I have not used with this app before …"))
            last = cands.index(guess) if guess in cands else \
                next((i for i, c in enumerate(cands) if c.id == self.settings.last_device), 0)
            item, ok = QInputDialog.getItem(
                self.window, _("Which FM-1 is this?"),
                _("Is this an FM-1 you have used with this app before? The app cannot tell by itself: an FM-1 only "
                  "reports a serial number while it runs the original firmware. Please choose:"), names, last, False)
            if ok and item == names[-1]:
                name, ok2 = QInputDialog.getText(self.window, _("New device"), _("Name for this FM-1:"))
                return self._new_profile(name if ok2 else "")
            return cands[names.index(item)] if ok and item in names[:-1] else None
        finally:
            self._asking = False

    # ------------------------------------------------------- which unit is it (before a write)
    def identified_text(self) -> str:
        """how the app knows which profile the connected FM-1 is – for the Overview and the header ("" = no profile)"""
        how = getattr(self, "identified_by", "") or ""
        if self.profile is None or self.is_virtual or how not in IDENTIFIED:
            return ""
        return _(IDENTIFIED[how])

    def session_revision(self) -> str:
        """the flash chip read for the connected unit in this session ("new" | "old" | "unknown"): its firmware's
        identity answer, m-bootloader's answer, or ROM boot mode at its USB port"""
        from .. import hardware
        port = self.port
        if port is None or getattr(port, "virtual", False):
            return hardware.UNKNOWN
        mb = getattr(self, "mboot", None)
        if mb is not None and mb.revision != hardware.UNKNOWN:
            return mb.revision
        return DeviceContext._sighting_revision(self, port)

    def unit_check(self) -> tuple[str, str]:
        """before anything is written to the connected FM-1 (installation, finishing one, restore, the Studio's write
        back): does the app know which unit it is? -> (kind, text):
          ""        known well enough: by its unit ID or flash chip, the only one it can be, or a new profile
          "ask"     nobody said yet which FM-1 it is (several are possible): nothing is written
          "block"   a flash chip read in this session contradicts the profile an answer named: nothing is written
          "confirm" named only by an answer (or the app's own installation at this USB port): the owner checks the
                    unit's front first (unit_confirm_text)"""
        from .. import hardware
        port, p = self.port, self.profile
        if port is None or self.is_virtual:
            return "", ""
        if p is None:
            if self.unit_candidates and not port.update_mode:
                return "ask", _("The app does not know yet which of your FM-1 this is – it reports no unit ID, and "
                                "more than one of your devices can be this one. Nothing is written before you say "
                                "which one it is.")
            return "", ""
        how = getattr(self, "identified_by", "") or ""
        if how not in UNCHECKED:
            return "", ""
        seen, rec = DeviceContext.session_revision(self), hardware.revision(p)
        if hardware.UNKNOWN not in (seen, rec) and seen != rec:
            return "block", _("The connected FM-1 reports {chip}, but “{name}” is recorded as {revision}. Either this is "
                              "another FM-1, or the record is wrong. Nothing is written. Say again which FM-1 this "
                              "is – if it is “{name}”, correct its unit on the page “Devices” first.").format(
                chip=_(hardware.LABEL[seen]), name=p.name, revision=revision_words(p))
        if seen != hardware.UNKNOWN or len(self.store.profiles) <= 1:
            return "", ""
        return "confirm", unit_confirm_text(p, how)

    def confirm_unit(self, parent) -> bool:
        """the gate in front of every write (see unit_check). False: nothing may be written (the reason was shown)."""
        kind, text = DeviceContext.unit_check(self)
        if not kind:
            return True
        from .common import confirm
        if kind == "confirm":
            return confirm(parent, _("Which FM-1 is this?"), text,
                           _("Yes, it is “{name}”").format(name=self.profile.name), danger=True)
        if confirm(parent, _("Which FM-1 is this?"), html_escape(text) + "<br><br>" +
                   _("Ask now? (Then start what you wanted to do again.)"), _("Say which FM-1 it is …")):
            self.ask_which()
        return False

    def ask_which(self):
        """ask "Which FM-1 is this?" again for the connected unit (Overview, or a write that needs it): the device
        is connected anew and the question is asked whatever was answered before"""
        port = self.port
        if port is None or self.is_virtual or port.update_mode:
            return
        self.port_answers.pop(port.key, None)
        self._ask_next = port.key
        self.connect_port(port)

    def disconnect(self, reason: str | None = ""):
        if reason == "":
            reason = _("disconnected")
        if self.link:
            try:
                self.about_to_disconnect.emit(self.link)
            except Exception:  # noqa: BLE001 – disconnecting must always work
                pass
            self.link.close()
        had = self.port is not None
        self._gone_since = None
        self.link = None
        self.info = None
        self.port = None
        self.profile = None
        self.identified_by = ""
        self.identity = ""
        self.firmware_guess = []
        self.sloop3 = None
        self.mboot = None
        if had:
            if reason:
                self.message.emit(reason)
            self.changed.emit()

    def reconnect_soon(self, ms: int = 1500):
        self.hold = False
        QTimer.singleShot(ms, self.scan)

    # ------------------------------------------------------- Access
    def require(self, parent) -> bool:
        if not self.connected:
            show_error(parent, _("No device"), _("Please connect an FM-1 with Melodee, Skiff or SLOOP via USB."))
            return False
        return True

    def job(self, parent, title: str, fn, *args, dialog=True, on_done=None, done=None, actions=None, **kw):
        """Run fn(worker, link, …) exclusively on the device. done: result sentence (text or fn(res) -> text)."""
        link = self.link

        def wrapped(w, *a, **k):
            if link is None or link.closed:
                raise LinkError(_("device not connected"))
            with link.exclusive():
                return fn(w, link, *a, **k)
        if dialog:
            if done or actions:
                from . import feedback
                return feedback.run(parent, title, wrapped, *args, on_done=on_done, done=done or "",
                                    actions=actions, **kw)
            d = JobDialog(parent, title, wrapped, *args, on_done=on_done, **kw)
            d.exec()
            return d
        return run_job(parent, wrapped, *args, on_done=on_done, **kw)

    def rename_profile(self, name: str, profile=None):
        """Rename the device – its backup folder moves along (as on the devices page).
        FolderError if the folder can't be moved (then the old name stays)."""
        from ..devices import move_profile_folder
        p = profile or self.profile
        name = (name or "").strip()
        if not p or not name or name == p.name:
            return
        other = self.store.name_taken(name, except_id=p.id) if hasattr(self.store, "name_taken") else None
        if other is not None:
            # otherwise the backups of two devices would end up in the same folder
            raise FolderError(_("The name “{name}” is already taken (device “{device}”) – otherwise the backups of both "
                                "devices would end up in the same folder. Please choose another name.").format(
                                    name=name, device=other.name))
        old_folder = p.folder_name
        new_folder = Profile(id=p.id, name=name).folder_name
        if old_folder != new_folder:
            try:
                move_profile_folder(self.settings.backups_dir, old_folder, new_folder)
            except OSError as e:
                raise FolderError(folder_error_text(self.settings.backups_dir / old_folder, e)) from e
        p.name = name
        self.store.save()
        self.message.emit(_("Renamed to “{name}” – backups are now in “{folder}”.").format(name=name, folder=new_folder)
                          if old_folder != new_folder else _("Renamed to “{name}”.").format(name=name))
        self.changed.emit()
