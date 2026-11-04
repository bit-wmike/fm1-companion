# SPDX-License-Identifier: GPL-3.0-only
"""Das aktive FM-1: Erkennung, Verbindung, Profil und gesicherte Gerätezugriffe."""
from __future__ import annotations

import os
import sys
import time

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QInputDialog, QLabel, QListWidget, QListWidgetItem,
                             QVBoxLayout, QWidget)

from .. import config, firmware
from ..i18n import _
from ..devices import PortInfo, Profile, ProfileStore, scan_ports, usable_serial
from ..protocol import Cmd, Info, parse_info
from ..transport import Link, LinkError, MidoTransport, SubprocessTransport
from .common import JobDialog, run_job, show_error

DEMO = os.environ.get("FM1C_DEMO") == "1"
UNKNOWN_CHOICE = "?"        # "don't know" – gemerkt, damit die App nur einmal fragt


class FolderError(OSError):
    """Ordner lässt sich nicht anlegen/beschreiben – Text ist eine fertige (übersetzte) Meldung"""


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
    """Update-Handshake über einen offenen Link (Hintergrund-Thread). -> Kennung oder ""."""
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
                    return ident.text
        return ""
    finally:
        link.remove_listener(listen)


def _probe_loader(port) -> str:
    from ..flash import engine
    try:
        ident = engine.Engine(log=lambda *_a: None).probe(port.in_name)
        return ident.text if ident else ""
    except Exception:  # noqa: BLE001
        return ""


def _demo_update_ports():
    """Demo: Geräte, die gerade im (simulierten) Update-Modus warten, erscheinen als eigener Port"""
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
    """Firmware-Schlüssel, die zu einer Kennung passen (eigene Erfahrung, Katalog, Raten)"""
    out = []
    key, _tag = firmware.guess_firmware(identity)
    if key:
        out.append(key)
    try:
        from .. import catalog as cat          # Katalog-Werkstatt (falls vorhanden)
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
    """Anzeigename zu einem Firmware-Schlüssel (eingebaut, eigene, Katalog)"""
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
    return key


class FirmwareChoiceDialog(QDialog):
    """„Welche Firmware läuft auf diesem FM-1?“ – Kandidaten aus dem Katalog plus „weiß nicht“."""

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


class DeviceContext(QObject):
    changed = pyqtSignal()                 # Gerät/Profil/Verbindung geändert
    ports_changed = pyqtSignal()
    editor_pause = pyqtSignal(bool)        # True: Editor muss pausieren
    message = pyqtSignal(str)
    about_to_disconnect = pyqtSignal(object)  # (Link) kurz vor dem Schließen – z. B. hängende Noten beenden

    def __init__(self, parent=None):
        super().__init__(parent)
        self.settings = config.load()
        self.store = ProfileStore()
        self.ports: list[PortInfo] = []
        self.vports: list[PortInfo] = []   # virtuelle Geräte (Studio-Zwilling) – nie in self.ports
        self.port: PortInfo | None = None
        self.link: Link | None = None
        self.info: Info | None = None
        self.profile: Profile | None = None
        self.hold = False                  # während eines Updates: nicht automatisch verbinden
        self.identity = ""                 # Handshake-Kennung (z. B. "FM-1_015", "ota-FM-1_900")
        self.firmware_guess: list[str] = []  # mögliche Firmware-Schlüssel zur Kennung
        self.uboot: list[str] = []         # USB-Pfade von FM-1 im ROM-Bootmodus (Rettung Stufe 2)
        self.pending_restore_check = ""    # nach Firmware-Wechsel: altes Backup anbieten
        self._asking = False
        self.window: QWidget | None = None
        # virtuelle Geräte (Studio-Zwilling): Liste und Leitung liefert der Dienst (ui/studio_service.py)
        self.virtual_ports = None          # () -> list[PortInfo]
        self.virtual_transport = None      # (PortInfo) -> Transport
        self.virtual_profile = None        # (PortInfo) -> Profile (nicht im Geräte-Speicher)
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
        """das aktive Gerät ist der Studio-Zwilling (kein echtes FM-1): Firmware, Rettung, Update-Modus, USB-Audio
        und automatische Backups gelten für ihn nicht."""
        return bool(self.port is not None and getattr(self.port, "virtual", False))

    @property
    def midi_connected(self) -> bool:
        """MIDI-Verbindung offen – egal welche Firmware (Noten, CC, DX7-SysEx gehen)"""
        return self.link is not None and not self.link.closed

    @property
    def protocol(self) -> str:
        """"felucca" (Editor-Protokoll), "generic" (nur MIDI + Handshake), "update" (Update-Modus) oder """""
        if self.port and (self.port.update_mode or self.identity.lower().startswith("ota-")):
            return "update"
        if self.connected:
            return "felucca"
        if self.midi_connected:
            return "generic"
        return ""

    @property
    def firmware_key(self) -> str:
        """Schlüssel der laufenden Firmware – "" wenn unbekannt oder mehrdeutig (nie geraten)"""
        if self.profile and self.profile.firmware and self.profile.firmware != UNKNOWN_CHOICE:
            return self.profile.firmware
        if self.info:
            return self.info.firmware_name
        if len(self.firmware_guess) == 1 and self.firmware_guess[0] != UNKNOWN_CHOICE:
            return self.firmware_guess[0]
        return ""

    @property
    def firmware_ambiguous(self) -> bool:
        """generisches Gerät, dessen Kennung mehrere Firmwares benutzen, und keine Wahl des Nutzers"""
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
        name = self.profile.name if self.profile else self.port.label
        if self.info:
            from .common import fw_label
            fw = fw_label(self.info.version)
        elif self.protocol == "update":
            fw = _("update mode ({id})").format(id=self.identity) if self.identity else _("update mode")
        elif self.midi_connected:
            name = firmware_name(self.firmware_key)
            fw = (name or (_("unknown – please choose") if self.firmware_ambiguous else _("unknown firmware"))) + \
                (f" ({self.identity})" if self.identity else "")
        else:
            fw = "…"
        return f"{name} – {fw}"

    def status_text(self) -> str:
        """kurzer Zustand für Werkzeugleiste/Übersicht"""
        if self.is_virtual:
            return (_("virtual – the twin in the Studio, not a real FM-1") if self.connected
                    else _("virtual – twin is starting …"))
        p = self.protocol
        return {"felucca": _("connected – full access"),
                "generic": _("connected – MIDI only (firmware without editor protocol)"),
                "update": _("in update mode – finish the installation (page “Rescue”)")}.get(p, _("not connected"))

    def device_folder(self, base) -> "os.PathLike":
        """Ordner dieses Geräts unter `base` (angelegt). FolderError (fertige Meldung), wenn das nicht geht."""
        sub = self.profile.folder_name if self.profile else "Unbekanntes Gerät"   # folder name on disk  # i18n: ignore
        d = base / sub
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise FolderError(folder_error_text(d, e)) from e
        return d

    # ------------------------------------------------------------ Suche
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
            except Exception:  # noqa: BLE001 – ein Fehler im Studio darf die Gerätesuche nicht stören
                vports = []
        keys = [p.key for p in ports]
        vkeys = [p.key for p in vports]
        if keys != [p.key for p in self.ports] or vkeys != [p.key for p in self.vports]:
            self.ports = ports           # nur echte Geräte (Firmware, Rettung, Zählen mehrerer FM-1 …)
            self.vports = vports         # virtuelle Geräte stehen getrennt (nur Geräteauswahl oben)
            self.ports_changed.emit()
        if self.port and self.port.key not in keys + vkeys:
            self.disconnect(_("FM-1 disconnected"))
        if self.link and not self.link.t.alive:
            self.disconnect(_("Connection lost"))
        if not self.port and ports:         # nie von selbst auf ein virtuelles Gerät verbinden
            last = self.settings.last_device
            def is_last(p):
                prof = self.store.by_serial(usable_serial(p, ports)) if usable_serial(p, ports) else None
                return bool(prof and prof.id == last) or bool(
                    last in self.store.profiles and p.usb_path and self.store.profiles[last].usb_path == p.usb_path)
            pick = next((p for p in ports if is_last(p)), ports[0])
            self.connect_port(pick)

    def connect_port(self, port: PortInfo):
        self.disconnect(None)
        self.port = port
        if port.update_mode:
            self.message.emit(_("The FM-1 is in update mode. The page “Rescue” finishes the installation."))
            self.identity = ""
            self._assign_profile()
            self.changed.emit()
            if not DEMO:      # Kennung des Loaders: Handshake ohne offenen Link (stört nichts)
                run_job(self, lambda w: _probe_loader(port), on_done=self._loader_probed, on_fail=lambda *a: None)
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
                elif port.serial == "DEMO-2":      # zweites Demo-Gerät: SLOOP mit 4 Sample-Slots
                    argv = [sys.executable, "-m", "fm1companion.demo_device", "--samples", "4", "--version", "SLOOP 2.8.3"]
                elif port.serial == "DEMO-3":      # drittes: Firmware ohne Editor-Protokoll (nur Handshake)
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
        # Ergebnisse gehören zu GENAU diesem Link: Ein langsamer Probe-Job des vorigen Geräts (Startwartezeit bis
        # STARTUP_GRACE) darf nach einem Gerätewechsel weder Kennung noch Firmware des neuen Geräts überschreiben
        link = self.link
        run_job(self, lambda w: self._probe(w, link), on_done=lambda info: self._probed(info, link),
                on_fail=lambda m, tb: self._probe_failed(m, link))

    STARTUP_GRACE = 20.0            # s: so lange darf ein Geräte-Prozess (Demo/Simulation) zum Starten brauchen

    def _probe(self, w, link=None):
        last = None
        link = link or self.link
        wait_started = getattr(link.t, "wait_started", None)
        for _try in range(4):       # das erste Paket nach dem Öffnen geht manchmal verloren
            try:
                info = parse_info(link.request(Cmd.INFO, [], timeout=1.0, retries=0))
                self._hs_identity = link.hs_identity = handshake(link, tries=1, timeout=0.6)
                return info
            except LinkError as e:
                last = e
                # Ein Geräte-Prozess, der noch startet, ist kein Gerät ohne Editor-Protokoll: erst warten, bis er
                # läuft, statt nach ~5 s aufzugeben (sonst bliebe es dauerhaft bei „nur MIDI“)
                if wait_started is not None and not wait_started(self.STARTUP_GRACE):
                    break
                time.sleep(0.3)
        raise last or LinkError(_("no answer"))

    def _probed(self, info: Info, link=None):
        if link is not None and link is not self.link:
            return                          # veraltet: inzwischen ein anderes Gerät (oder neu verbunden)
        self.info = info
        self.identity = (getattr(link, "hs_identity", "") if link is not None else getattr(self, "_hs_identity", "")) \
            or self.identity
        self._assign_profile()
        p = self.profile
        if p:
            p.version_string = info.version
            key, tag = firmware.guess_firmware(p.identity, info.version, info.firmware_name)
            if key and (not p.firmware or p.firmware != key):
                p.firmware = key
                if tag:
                    p.firmware_version = tag
            elif key and tag and not p.firmware_version:
                p.firmware_version = tag
            p.last_seen = time.time()
            if not self.is_virtual:          # der Zwilling ist nie „das zuletzt benutzte FM-1“
                self.store.save()
                self.settings.last_device = p.id
                config.save(self.settings)
        self.changed.emit()

    def _probe_failed(self, msg, link=None):
        """kein Felucca-INFO: Handshake versuchen (stört nicht), MIDI-Verbindung offen lassen"""
        if link is not None and link is not self.link:
            return                          # veraltet: inzwischen ein anderes Gerät
        self.info = None
        link = self.link
        if link is None or link.closed:
            return
        run_job(self, lambda w: handshake(link), on_done=lambda ident: self._generic(ident, link),
                on_fail=lambda m, tb: self._generic("", link))

    def _generic(self, ident: str, link=None):
        if self.link is None or (link is not None and link is not self.link):
            return
        self.identity = ident or ""
        self._assign_profile()
        p = self.profile
        choice = p.firmware_choice.get(ident, "") if p and ident else ""
        cands = firmware_candidates(ident) if ident else []
        if choice and choice != UNKNOWN_CHOICE:
            guess = [choice]
        else:
            guess = cands
        self.firmware_guess = guess
        if p:
            if choice and choice != UNKNOWN_CHOICE:
                p.firmware = choice
            elif len(guess) == 1:
                if p.firmware != guess[0]:
                    p.firmware, p.firmware_version = guess[0], firmware.guess_firmware(ident)[1] or p.firmware_version
            elif not self._installed_here(p, ident):
                # mehrdeutig: nichts raten (eine frühere Felucca-Firmware dieses Geräts gilt nicht mehr)
                p.firmware, p.firmware_version = "", ""
            p.identity = ident or p.identity
            p.last_seen = time.time()
            self.store.save()
        if ident:
            self.message.emit(_("FM-1 connected ({id}) – this firmware has no editor protocol: MIDI, presets via "
                                "SysEx and firmware installation work, backups and projects do not.").format(id=ident))
        else:
            self.message.emit(_("The device answers neither editor commands nor the update ID. The "
                                "MIDI connection stays open."))
        self.changed.emit()
        if self.firmware_ambiguous and p and ident and not choice:
            QTimer.singleShot(300, lambda port=self.port: self.ask_firmware(port))

    @staticmethod
    def _installed_here(p, ident: str) -> bool:
        """hat die App selbst diese Firmware mit genau dieser Kennung zuletzt installiert?"""
        hist = [h for h in (p.firmware_history or []) if isinstance(h, dict)]
        return bool(p.firmware and hist and hist[-1].get("firmware") == p.firmware
                    and hist[-1].get("identity") == ident)

    def ask_firmware(self, port=None) -> str:
        """einmal fragen, welche Firmware hinter einer mehrdeutigen Kennung steckt (pro Gerät gemerkt)"""
        if port is not None and self.port is not port:
            return ""
        if self._asking or not self.identity or not self.firmware_ambiguous:
            return ""
        self._asking = True
        try:
            dlg = FirmwareChoiceDialog(self.window, self.identity, list(self.firmware_guess),
                                       self.profile.name if self.profile else "")
            ok = bool(dlg.exec())
            key = dlg.choice() if ok else UNKNOWN_CHOICE
        finally:
            self._asking = False
        if port is not None and self.port is not port:
            return ""
        self.choose_firmware(key)
        return key

    def choose_firmware(self, key: str):
        """der Nutzer legt fest, welche Firmware hinter dieser Kennung steckt (pro Gerät gemerkt).
        UNKNOWN_CHOICE ("?") = „weiß nicht“: wird gemerkt (keine weitere Frage), die Firmware bleibt unbekannt."""
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
            self.message.emit(_("Remembered: firmware unknown – installations count as a firmware change (with "
                                "V15 intermediate step)."))
        self.changed.emit()

    def _loader_probed(self, ident):
        if ident and self.port and self.port.update_mode:
            self.identity = ident
            self.changed.emit()

    def _assign_profile(self):
        """Profil zum Gerät finden: Seriennummer, sonst Firmware und USB-Anschluss, sonst fragen."""
        port = self.port
        if not port:
            return
        if getattr(port, "virtual", False) and self.virtual_profile is not None:
            self.profile = self.virtual_profile(port)      # eigenes Profil, nie im Geräte-Speicher
            return
        serial = usable_serial(port, self.ports or [port])
        if serial:
            p = self.store.by_serial(serial) or self.store.create(serial=serial)
        else:
            cands = self.store.without_serial()
            fw = self.info.firmware_name if self.info else ""
            if not cands:
                p = self.store.create()
            elif len(cands) == 1:
                p = cands[0]
            else:
                pick = None
                for narrow in ([c for c in cands if fw and c.firmware == fw],
                               [c for c in cands if port.usb_path and c.usb_path == port.usb_path],
                               [c for c in cands if port.usb_path and c.usb_path == port.usb_path and fw and c.firmware == fw]):
                    if len(narrow) == 1:
                        pick = narrow[0]
                        break
                p = pick or self._ask_profile(cands)
        if port.usb_path and p.usb_path != port.usb_path:
            p.usb_path = port.usb_path
            self.store.save()
        self.profile = p

    def profile_for_port(self, port: PortInfo) -> Profile | None:
        """ohne Rückfrage: welches Profil gehört (vermutlich) zu diesem Anschluss?"""
        if self.port and port.key == self.port.key and self.profile:
            return self.profile
        if getattr(port, "virtual", False):
            return self.virtual_profile(port) if self.virtual_profile is not None else None
        serial = usable_serial(port, self.ports)
        if serial:
            return self.store.by_serial(serial)
        return next((p for p in self.store.without_serial() if port.usb_path and p.usb_path == port.usb_path), None)

    def _ask_profile(self, cands: list[Profile]) -> Profile:
        if self._asking:
            return cands[0]
        self._asking = True
        try:
            names = [c.name + (f" ({c.firmware} {c.firmware_version})" if c.firmware else "") for c in cands]
            names.append(_("Create new device …"))
            last = next((i for i, c in enumerate(cands) if c.id == self.settings.last_device), 0)
            item, ok = QInputDialog.getItem(self.window, _("Which FM-1 is this?"),
                                            _("This FM-1 has no serial number. Which of your devices is it?"),
                                            names, last, False)
            if ok and item == names[-1]:
                name, ok2 = QInputDialog.getText(self.window, _("New device"), _("Name for this FM-1:"))
                return self.store.create(name if ok2 else "")
            return cands[names.index(item)] if ok and item in names[:-1] else cands[last]
        finally:
            self._asking = False

    def disconnect(self, reason: str | None = ""):
        if reason == "":
            reason = _("disconnected")
        if self.link:
            try:
                self.about_to_disconnect.emit(self.link)
            except Exception:  # noqa: BLE001 – Trennen muss immer klappen
                pass
            self.link.close()
        had = self.port is not None
        self.link = None
        self.info = None
        self.port = None
        self.profile = None
        self.identity = ""
        self.firmware_guess = []
        if had:
            if reason:
                self.message.emit(reason)
            self.changed.emit()

    def reconnect_soon(self, ms: int = 1500):
        self.hold = False
        QTimer.singleShot(ms, self.scan)

    # ------------------------------------------------------- Zugriffe
    def require(self, parent) -> bool:
        if not self.connected:
            show_error(parent, _("No device"), _("Please connect an FM-1 with Melodee, Skiff or SLOOP via USB."))
            return False
        return True

    def job(self, parent, title: str, fn, *args, dialog=True, on_done=None, done=None, actions=None, **kw):
        """fn(worker, link, …) exklusiv am Gerät ausführen. done: Ergebnis-Satz (Text oder fn(res) -> Text)."""
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
        """Gerät umbenennen – sein Backup-Ordner wandert mit (wie auf der Geräte-Seite).
        FolderError, wenn der Ordner nicht verschoben werden kann (dann bleibt der alte Name)."""
        from ..devices import move_profile_folder
        p = profile or self.profile
        name = (name or "").strip()
        if not p or not name or name == p.name:
            return
        other = self.store.name_taken(name, except_id=p.id) if hasattr(self.store, "name_taken") else None
        if other is not None:
            # sonst landeten die Backups zweier Geräte im selben Ordner
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
