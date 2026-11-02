# SPDX-License-Identifier: GPL-3.0-only
"""Die Installation selbst: Melodees Installer (vendor/fm1_install.py) als Bibliothek, mit Journal,
Loader-Regeln, Fortschritt, Probelauf und deutschen Fehlermeldungen.

Backend: fm1_install.MidoBackend (echte Ports) oder flash.sim.SimBackend (Tests, Demo).
"""
from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass
from typing import Callable

from ..vendor import fm1_install as fi
from . import package as pk
from .journal import Journal

STEP_PHASE = {"start": "step1", "check": "step1", "loader": "loader", "write": "writing", "reboot": "reboot",
              "done": "done"}

# Protokollzeilen, an denen die Oberfläche den gefährlichen Abschnitt erkennt (InstallDialog: „Jetzt nicht abstecken“)
LOG_WRITING = "FM-1 wechselt in den Update-Modus …"
LOG_REBOOT = "Geschrieben – das FM-1 startet neu …"

# was ein Fehler hinterlässt (wie Melodees progress.js afterFailure)
SAFE, PAUSED, RESTART = "safe", "paused", "restart"


# Codes, die Felucca-Firmwares in Schritt 1 auf dem FM-1-Bildschirm zeigen (felucca-bsp/melodee/sloop ota.c,
# fm-1.drewr.dev/bootloader-check). Der Rechner sieht nur, dass das Gerät aufhört zu lesen.
DEVICE_CODES = {
    -2: "Paketkopf beschädigt (CRC)",
    -3: "Paket ohne Update-Loader bzw. ohne flash.bin (z. B. m-bootloader-Installer)",
    -5: "CHECK HEAD FAILED: Bootbereich des Pakets ≠ Bootbereich des Geräts (nichts geschrieben)",
    -7: "unbekannter Update-Loader im Paket – abgelehnt",
    -11: "Loader beim Zwischenspeichern nicht korrekt zurückgelesen",
    -13: "Rechner hat das Ende von Schritt 1 nicht bestätigt",
    -15: "Felucca 1.6.1: flash.bin nicht 0x93000 Bytes (Open V12/V14)",
}


def device_code_text(code: int) -> str:
    return f"FAILED {code}: {DEVICE_CODES.get(code, 'unbekannter Fehler')}"


class FlashError(Exception):
    def __init__(self, code: str, message: str, state: str = SAFE, detail: str = ""):
        super().__init__(message)
        self.code, self.state, self.detail = code, state, detail


MESSAGES = {
    "notfound": "Kein FM-1 hat geantwortet. USB-Datenkabel prüfen (kein reines Ladekabel), andere Musik-Apps "
                "schließen und erneut versuchen.",
    "model": "Das angeschlossene Gerät ist kein FM-1 oder das Paket ist für ein anderes Gerät.",
    "lost": "Die USB-Verbindung ist abgerissen.",
    "stopped": "Das FM-1 hat aufgehört, Daten anzufordern.",
    "noloader": "Das FM-1 im Update-Modus wurde nicht gefunden. Kabel neu einstecken und „Fortsetzen“ wählen – "
                "es bleibt im Update-Modus, bis die Installation fertig ist.",
    "noreturn": "Das FM-1 ist nach dem Schreiben nicht zurückgekommen. Aus- und wieder einschalten.",
    "mismatch": "Geschrieben, aber das FM-1 meldet eine andere Firmware.",
    "badreq": "Das FM-1 hat Daten außerhalb des Pakets angefordert – abgebrochen.",
    "loader": "Das FM-1 wartet in einem Update-Modus, der nicht zu diesem Paket passt. Es wurde nichts gesendet.",
    "cancelled": "Abgebrochen, bevor etwas gesendet wurde. Am FM-1 hat sich nichts geändert.",
    "checkhead": "Die laufende Firmware hat die Original-Firmware abgelehnt (CHECK HEAD, am FM-1 „FAILED -5“): "
                 "Ihr Bootbereich weicht ab. Es wurde nichts geschrieben, die jetzige Firmware läuft weiter. "
                 "Bitte keinen anderen Weg erzwingen. Ursache ist meist ein geänderter Bootbereich (m-bootloader, "
                 "Open V12/V14). Ausweg: Firmware mit neuer Prüfung oder Download-Modus (Rettung Stufe 2).",
    "stopped1": "Das FM-1 hat das Paket in Schritt 1 abgelehnt. Steht auf dem Display „FAILED <Zahl>“? "
                "-3/-7: unbekannter Loader, -5: Bootbereich weicht ab, -15: Felucca-1.6.1-Prüfung (siehe Hilfe).",
    "busy": "Es läuft schon eine Installation.",
    "ambiguous": "Mehrere FM-1 antworten. Bitte alle anderen abziehen.",
}


# deutsche Fassung von fi.LOADER_HINT (welches Paket der wartende Loader annimmt)
LOADER_HINT_DE = {
    "ota-FM-1_900": "Er wartet auf ein Paket der Felucca-Familie (Melodee, SLOOP, Skiff …) – am besten genau das, dessen "
                    "Installation unterbrochen wurde.",
    "ota-FM-1_015": "Er wartet auf die unveränderte Original-Firmware V15 (FM-1_015.fwsc).",
}


def loader_hint(text: str) -> str:
    if text in LOADER_HINT_DE:
        return LOADER_HINT_DE[text]
    if re.match(r"^ota-FM-1_9\d\d$", text or ""):
        return "Er wartet auf das Felucca-Paket, dessen Installation unterbrochen wurde."
    if re.match(r"^ota-FM-1_0\d\d$", text or ""):
        return "Er wartet auf die Original-Firmware von M-VAVE."
    if re.match(r"^ota-FM1C_[0-9a-f]{16}$", text or ""):
        return ("Das ist der Update-Loader der M-VAVE FM1 Open (Build " + text[9:] + "…). Er nimmt nur das Paket "
                "genau dieses Builds an; die App schickt ihm nichts – bitte den FM1-Open-Updater von M-VAVE benutzen.")
    return f"Diesen Loader ({text}) kennt die App nicht."


MESSAGES["pinned_lost"] = ("Das Zielgerät ist an seinem USB-Anschluss nicht (mehr) zu finden. Bitte nur das FM-1 "
                           "angeschlossen lassen, das du aktualisieren willst, und kein Kabel umstecken.")


@dataclass
class Result:
    identity: str
    dry_run: bool = False
    log: tuple = ()
    port: str = ""                # Port-Name des Geräts nach der Installation (für die Ressourcen)


@dataclass
class Pin:
    """hält EIN Gerät über Neustarts und Update-Modus hinweg fest: USB-Anschluss (sysfs-Pfad, ändert sich beim
    Neu-Anmelden nicht), sonst Seriennummer"""
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
        return f"USB-Anschluss {self.usb_path}" if self.usb_path else f"Seriennummer {self.serial}"


def default_details(name: str) -> dict:
    """USB-Angaben (sysfs) zu einem mido-Portnamen"""
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
        raise FlashError("usage", "Für die Installation fehlt mido/rtmidi (sudo apt install python3-mido "
                                  "python3-rtmidi).")


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

    # ------------------------------------------------------------ Suche
    def updater(self, port: str = "", pin: Pin | None = None) -> fi.Updater:
        if pin is not None and pin.usable:
            # nur Ports genau dieses USB-Geräts (auch nach Neustarts, egal unter welchem Namen es erscheint)
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
        """alle Ports, die ein FM-1 (auch im Update-Modus) sein können"""
        return [n for n in self.backend.input_names() if self.port_filter(n)]

    def pin_for(self, port: str) -> Pin | None:
        """Pin aus einem Portnamen (oder Teil davon), wenn die USB-Angaben eindeutig sind"""
        all_names = self.backend.input_names()
        names = [n for n in all_names if n == port] or [n for n in all_names if port and port.lower() in n.lower()]
        if len(names) != 1:
            return None
        d = self._details(names[0])
        pin = Pin(d.get("usb_path", ""), d.get("serial", ""))
        return pin if pin.usable else None

    def current_port(self, pin: Pin) -> str:
        """Port-Name, unter dem das festgehaltene Gerät gerade erscheint ("" = nicht da / nicht eindeutig)"""
        names = [n for n in self.backend.input_names() if pin.matches(self._details(n))]
        return names[0] if len(names) == 1 else ""

    def probe(self, port: str = "", want=None):
        """Handshake (stört das Gerät nicht) -> Identity oder None"""
        up = self.updater(port)
        dev = up.find(want, only_port=bool(port))
        if dev:
            dev.link.close()
            return dev.id
        return None

    def find_device(self, port: str = ""):
        """das Gerät am angegebenen Port; ohne Port: das einzige antwortende FM-1"""
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

    # ------------------------------------------------------------ Schreiben
    def write(self, info: pk.PackageInfo, port: str = "", label: str = "", device_id: str = "",
              dry_run: bool = False, expect_sha: str = "", allow_resume: bool = True, pin: Pin | None = None) -> Result:
        """ein Paket auf das FM-1 schreiben (oder im Probelauf nur alles prüfen).

        pin: Zielgerät über USB-Anschluss/Seriennummer festhalten (sonst aus `port` abgeleitet, wenn möglich).
        Sind mehrere FM-1 angeschlossen und lässt sich das Ziel nicht festhalten, wird nichts gesendet."""
        rule = info.loader_rule
        if rule is None:
            raise FlashError("badpkg", "Die Loader-Familie dieses Pakets ist unbekannt – es wird nicht gesendet.")
        if expect_sha and info.sha256 != expect_sha.lower():
            raise FlashError("badpkg", "Die Prüfsumme des Pakets stimmt nicht – es wird nicht gesendet.")
        if info.damage:
            raise FlashError("badpkg", f"Das Paket ist beschädigt ({info.damage}).")
        label = label or info.product
        if pin is None or not pin.usable:
            pin = self.pin_for(port) if port else None
        if pin is not None:
            up = self.updater(pin=pin)
            dev = up.find(only_port=False)
            if dev is None:
                raise FlashError("notfound", MESSAGES["pinned_lost"])
            self.log(f"Zielgerät festgehalten: {pin}")
        else:
            if len(self.candidate_ports()) > 1:
                raise FlashError("ambiguous", "Bitte nur das FM-1 angeschlossen lassen, das du aktualisieren willst. "
                                              "Mehrere FM-1 sind angeschlossen, und die App kann das Zielgerät nicht "
                                              "eindeutig festhalten – es wurde nichts gesendet.")
            up, dev = self.find_device(port)
            # nach den Neustarts zuerst Ports desselben Geräts fragen ("Melodee FM-1 Update" -> "Melodee FM-1")
            up.port = re.split(r"\s+update\b|:", dev.name, maxsplit=1, flags=re.I)[0].strip() or dev.name
        self.log(f"Gerät: {dev.id.text} an „{dev.name}“")
        if fi.model_of(dev.id.text) != info.model:
            dev.link.close()
            raise FlashError("model", f"Das Gerät meldet {dev.id.text}, das Paket ist für {info.product}.")
        if dev.id.loader:
            if not rule(dev.id.text):
                dev.link.close()
                raise FlashError("loader", f"Das FM-1 wartet im Update-Modus als „{dev.id.text}“ – dieses Paket "
                                           f"({info.product}) gehört aber zu {rule.label}. Es wurde nichts gesendet. "
                                           + loader_hint(dev.id.text), detail=dev.id.text)
            if not allow_resume:
                dev.link.close()
                raise FlashError("loader", "Das FM-1 wartet im Update-Modus – bitte über die Rettung fortsetzen.")
        if dry_run:
            dev.link.close()
            self.log(f"Probelauf: {label} ({info.product}, {info.family}) würde an {dev.id.text} gehen – "
                     f"{'Fortsetzen im Update-Modus' if dev.id.loader else 'Schritt 1 + Schritt 2'}. Nichts gesendet.")
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
                dev.link.close()            # nichts gesendet: Port nicht offen lassen
                raise
            phase = {"p": "loader" if dev.id.loader else "prepared"}
            total = max(1, len(info.image))

            def step(k, a=None):
                ph = STEP_PHASE.get(k)
                if ph and ph != phase["p"]:
                    phase["p"] = ph
                    self.journal.update(phase=ph)
                if k == "start":
                    self.log(f"Starte das Update auf {a} …")
                elif k == "check":
                    self.progress(min(int(a or 0), 100), 0)
                elif k == "loader":
                    self.log(LOG_WRITING)
                elif k == "write":
                    self.progress(int(a or 0), 100)
                elif k == "reboot":
                    self.log(LOG_REBOOT)
                elif k == "done":
                    self.log(f"Fertig: das FM-1 meldet {a}")
            try:
                ident = up.install(dev, info.image, info.product, step, rule)
            except fi.InstallError as e:
                raise self._translate(e, phase["p"], up, info)
            except FlashError:
                raise
            except Exception as e:  # noqa: BLE001 – unerwarteter Fehler (MIDI-Treiber, Programmfehler): Journal
                # muss trotzdem stimmen, damit die Rettung danach das Richtige anbietet
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
            return FlashError("internal", "Unerwarteter Fehler nach dem Schreiben. Das FM-1 startet vermutlich normal – "
                                          "Kabel neu einstecken und prüfen, welche Firmware es meldet.", RESTART, detail)
        if phase in ("loader", "writing"):
            return FlashError("internal", "Unerwarteter Fehler während des Schreibens. Das FM-1 wartet vermutlich im "
                                          "Update-Modus: Kabel prüfen und auf der Seite „Rettung“ mit demselben Paket "
                                          "fortsetzen.", PAUSED, detail)
        return FlashError("internal", "Unerwarteter Fehler vor dem Schreiben. Es wurde nichts geschrieben, die bisherige "
                                      "Firmware läuft weiter.", SAFE, detail)

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
            msg += " Es wurde nichts geschrieben."
        if state == PAUSED and code == "loader":
            msg = ("Das FM-1 hat einen Update-Loader gestartet, der nicht zu diesem Paket passt. Es wurde "
                   "nichts geschrieben; das FM-1 wartet jetzt im Update-Modus. Auf der Seite „Rettung“ wird nur ein "
                   "Paket gesendet, das zu diesem Loader gehört.")
        elif state == PAUSED and code != "noloader":
            msg += (" Das FM-1 wartet im Update-Modus: Kabel prüfen und mit demselben Paket fortsetzen "
                    "(Seite „Rettung“).")
        if code == "mismatch":
            msg += f" ({e})"
        return FlashError(code, msg, state, str(e))
