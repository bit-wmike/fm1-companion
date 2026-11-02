# SPDX-License-Identifier: GPL-3.0-only
"""Systemcheck: findet im Kernel-Protokoll und im System Probleme, die das FM-1 betreffen.

Quellen: ``dmesg`` (bzw. ``journalctl -k -b`` als Ersatz), ``vcgencmd get_throttled``, sysfs/procfs,
Programme im PATH. Jede Regel liefert ein ``Finding`` mit Ampel (ok/info/warn/error), einer
Erklärung in einfachem Deutsch und einem Lösungsvorschlag. Ohne Qt – testbar mit Beispiel-Logs.

  python3 -m fm1companion.syscheck            # Bericht im Terminal
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

LEVELS = ("ok", "info", "warn", "error", "unknown")
FM1_RE = re.compile(r"fm-?1|melodee|felucca|sloop|skiff|m-vave|ellic|wl82|uboot", re.I)
FM1_IDS = {"1209:0001"}                       # Melodee/Felucca (pid.codes) – "Ellic Studio Melodee FM-1"
UPDATE_IDS = {"4c4a:8057"}                    # JieLi-Rettungsmodus "WL82 UBOOT1.00"
LINE_RE = re.compile(r"^\s*(?:\[\s*(?P<ts>\d+\.\d+)\])?\s*(?:(?:\S+\s+)?kernel:\s*)?(?P<msg>.*)$")
USBDEV_RE = re.compile(r"\busb (?P<path>\d+-[\d.]+)(?::[\d.]+)?:\s*(?P<rest>.*)")
PORT_RE = re.compile(r"\busb (?:usb)?(?P<path>\d+-port[\d.]+|\d+-[\d.]+-port\d+):\s*(?P<rest>.*)")


@dataclass
class Finding:
    key: str
    level: str                 # ok | info | warn | error | unknown
    title: str
    detail: str = ""           # was bedeutet das?
    fix: str = ""              # was tun?
    evidence: list[str] = field(default_factory=list)   # passende Log-Zeilen

    def text(self) -> str:
        mark = {"ok": "OK  ", "info": "INFO", "warn": "WARN", "error": "FEHL", "unknown": "?   "}[self.level]
        out = [f"[{mark}] {self.title}"]
        if self.detail:
            out.append("       " + self.detail)
        if self.fix:
            out.append("       Lösung: " + self.fix)
        for e in self.evidence[:6]:
            out.append("         | " + e)
        return "\n".join(out)


@dataclass
class LogLine:
    ts: float | None
    msg: str
    raw: str


def parse_log(text: str) -> list[LogLine]:
    """dmesg- oder journalctl-Ausgabe (short-monotonic) in Zeilen mit Zeitstempel zerlegen."""
    out = []
    for raw in text.splitlines():
        if not raw.strip() or raw.startswith("-- "):
            continue
        m = LINE_RE.match(raw)
        ts = float(m.group("ts")) if m and m.group("ts") else None
        out.append(LogLine(ts, (m.group("msg") if m else raw).strip(), raw.rstrip()))
    return out


# ----------------------------------------------------------- Log lesen ---
def _run(args, timeout=6) -> tuple[int, str, str]:
    if not shutil.which(args[0]):
        return 127, "", f"{args[0]} nicht gefunden"
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, errors="replace")
        return p.returncode, p.stdout, p.stderr
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, "", str(e)


def read_kernel_log() -> tuple[str, str, str]:
    """(Text, Quelle, Problem). Quelle "dmesg" oder "journalctl"; Problem leer, wenn es geklappt hat."""
    rc, out, err = _run(["dmesg"])
    if rc == 0 and out.strip():
        return out, "dmesg", ""
    rc2, out2, err2 = _run(["journalctl", "-k", "-b", "--no-pager", "-o", "short-monotonic"])
    lines = [ln for ln in out2.splitlines() if ln.strip() and not ln.startswith(("Hint:", "-- "))]
    if rc2 == 0 and len(lines) > 3:
        return out2, "journalctl", ""
    why = (err or "").strip() or (err2 or "").strip() or "keine Ausgabe"
    return "", "", why


def read_user_journal() -> str:
    """Meldungen von PipeWire/WirePlumber dieser Sitzung (für XRuns)."""
    rc, out, _ = _run(["journalctl", "--user", "-b", "--no-pager", "-o", "short-monotonic",
                       "-u", "pipewire", "-u", "wireplumber", "-u", "pipewire-pulse"])
    return out if rc == 0 else ""


# ------------------------------------------------------------ Auswerten ---
@dataclass
class UsbDev:
    path: str
    ids: str = ""
    product: str = ""
    manufacturer: str = ""
    connects: list[float] = field(default_factory=list)
    disconnects: list[float] = field(default_factory=list)

    @property
    def is_fm1(self) -> bool:
        return self.ids in FM1_IDS or self.ids in UPDATE_IDS or bool(FM1_RE.search(f"{self.product} {self.manufacturer}"))


def usb_devices_from_log(lines: list[LogLine]) -> dict[str, UsbDev]:
    devs: dict[str, UsbDev] = {}
    for ln in lines:
        m = USBDEV_RE.search(ln.msg)
        if not m:
            continue
        path, rest = m.group("path"), m.group("rest")
        d = devs.setdefault(path, UsbDev(path))
        if rest.startswith("new ") and "USB device number" in rest:
            d.connects.append(ln.ts or 0.0)
        elif rest.startswith("USB disconnect"):
            d.disconnects.append(ln.ts or 0.0)
        elif rest.startswith("New USB device found"):
            mv = re.search(r"idVendor=([0-9a-f]{4}), idProduct=([0-9a-f]{4})", rest)
            if mv:
                d.ids = f"{mv.group(1)}:{mv.group(2)}"
        elif rest.startswith("Product:"):
            d.product = rest.split(":", 1)[1].strip()
        elif rest.startswith("Manufacturer:"):
            d.manufacturer = rest.split(":", 1)[1].strip()
    return devs


def _grep(lines, pattern, flags=re.I) -> list[LogLine]:
    rx = re.compile(pattern, flags)
    return [ln for ln in lines if rx.search(ln.msg)]


def _ev(lines: list[LogLine], n=6) -> list[str]:
    return [ln.raw.strip() for ln in lines[-n:]]


def _max_in_window(times: list[float], window: float) -> int:
    best, j = 0, 0
    ts = sorted(times)
    for i in range(len(ts)):
        while ts[i] - ts[j] > window:
            j += 1
        best = max(best, i - j + 1)
    return best


def analyze_log(text: str, fm1_paths: set[str] | None = None, user_journal: str = "") -> list[Finding]:
    lines = parse_log(text)
    found: list[Finding] = []
    devs = usb_devices_from_log(lines)
    fm1 = {p: d for p, d in devs.items() if d.is_fm1 or p in (fm1_paths or set())}

    # --- FM-1 verbindet sich immer wieder neu
    for path, d in fm1.items():
        name = d.product or "FM-1"
        ev = [ln for ln in lines if re.search(rf"\busb {re.escape(path)}:\s*(USB disconnect|new \S+-speed)", ln.msg)]
        burst = _max_in_window(d.disconnects, 600)
        if burst >= 3:
            found.append(Finding(
                f"fm1-loop:{path}", "error", f"{name} trennt sich immer wieder (Anschluss {path})",
                f"{len(d.disconnects)} Trennungen seit dem Start, davon {burst} innerhalb von 10 Minuten. Das passiert "
                "fast immer wegen Kabel, Stromversorgung oder Hub – selten wegen der Firmware.",
                "Anderes (kurzes) USB-Kabel probieren, das FM-1 direkt an den Pi stecken statt an einen Hub, "
                "bei einem Hub einen mit eigenem Netzteil nehmen. Prüfe außerdem das Pi-Netzteil (siehe Unterspannung).",
                _ev(ev, 8)))
        elif d.disconnects:
            found.append(Finding(
                f"fm1-disc:{path}", "info", f"{name} wurde {len(d.disconnects)}× getrennt (Anschluss {path})",
                "Einzelne Trennungen sind normal, wenn du das FM-1 ausgesteckt, ausgeschaltet oder eine Firmware "
                "installiert hast.", "Nur wenn du nichts gemacht hast: Kabel und Anschluss prüfen.", _ev(ev, 4)))
    if fm1 and not any(f.key.startswith(("fm1-loop", "fm1-disc")) for f in found):
        d = next(iter(fm1.values()))
        found.append(Finding("fm1-usb", "ok", f"{d.product or 'FM-1'} stabil am USB ({d.path}, {d.ids or 'ID ?'})"))

    # --- Fehler beim Erkennen eines USB-Geräts (-71/-110 …)
    enum = _grep(lines, r"device descriptor read/\d+, error|device not accepting address|unable to enumerate USB device|"
                        r"cannot enable\. Maybe the USB cable is bad|can't set config #\d+, error")
    if enum:
        paths = sorted({m.group("path") for ln in enum for m in [USBDEV_RE.search(ln.msg) or PORT_RE.search(ln.msg)] if m})
        on_fm1 = any(p in fm1 or any(p.startswith((f + ".", f + "-")) for f in fm1) for p in paths)
        found.append(Finding(
            "usb-enum", "error" if on_fm1 or len(enum) >= 3 else "warn",
            "USB-Gerät ließ sich nicht richtig erkennen" + (f" ({', '.join(paths)})" if paths else ""),
            "Der Pi bekam vom Gerät beim Anstecken keine saubere Antwort (Fehler -71/-110). Typisch für ein "
            "schlechtes oder zu langes Kabel, ein reines Ladekabel ohne Datenleitungen oder zu wenig Strom.",
            "Ein anderes Datenkabel nehmen, direkt am Pi anstecken, Netzteil prüfen. Beim FM-1: einmal aus- und "
            "wieder einschalten.", _ev(enum)))

    # --- Überstrom
    oc = _grep(lines, r"over-?current")
    if oc:
        found.append(Finding(
            "usb-overcurrent", "error", "Überstrom an einem USB-Anschluss",
            "Ein Gerät hat mehr Strom gezogen, als der Anschluss liefern darf – der Pi schaltet den Anschluss dann ab.",
            "Stromhungrige Geräte (Festplatten, Ladegeräte) an einen USB-Hub mit eigenem Netzteil hängen; das "
            "Netzteil des Pi prüfen (Pi 5: offizielles 27-W-Netzteil).", _ev(oc)))
    power = _grep(lines, r"insufficient available bus power|rejected \d+ configuration")
    if power:
        found.append(Finding(
            "usb-power", "error", "USB-Gerät abgewiesen: zu wenig Strom am Bus",
            "Der USB-Anschluss (oder Hub) kann das Gerät nicht versorgen.",
            "Gerät direkt an den Pi oder an einen Hub mit eigenem Netzteil stecken.", _ev(power)))

    # --- xHCI (USB-Controller)
    xdead = _grep(lines, r"xhci.*(not responding|assume dead|HC died|host system error|Host halt failed)")
    xwarn = _grep(lines, r"xhci.*(WARN|ERROR (?!unknown event type 37)|Event TRB for slot \d+ ep \d+ with no TDs queued)")
    if xdead:
        # Auslöser eingrenzen: Audio-Start des FM-1 (usb_set_interface an seinem Anschluss) kurz vor dem Absturz?
        t_dead = next((ln.ts for ln in xdead if ln.ts is not None), None)
        trig = [ln for ln in lines if t_dead is not None and ln.ts is not None and abs(ln.ts - t_dead) <= 5
                and re.search(r"usb_set_interface failed|cannot submit urb|Unable to change format", ln.msg)
                and any(re.search(rf"\busb {re.escape(pth)}:", ln.msg) for pth in fm1)]
        if trig:
            found.append(Finding(
                "xhci-dead", "error", "USB-Controller ist abgestürzt – beim Audio-Start des FM-1",
                "Der USB-Controller des Rechners hat aufgegeben; alle USB-Geräte waren danach weg. Gleichzeitig "
                "scheiterte das Einschalten des Audio-Datenstroms des FM-1 (usb_set_interface). Das FM-1 ist auch "
                "eine USB-Soundkarte; PipeWire öffnet sie von selbst. Am Pi 5 kann ein solcher Audio-Start mancher "
                "USB-Geräte den Controller zum Absturz bringen – das ist ein Problem von Kernel/Controller, nicht "
                "des FM-1 oder der App.",
                "1) Ohne Neustart: nur diesen Controller neu anmelden (echo xhci-hcd.N | sudo tee "
                "/sys/bus/platform/drivers/xhci-hcd/unbind, dann …/bind; N aus der Meldung oben) – nie den Controller "
                "mit der System-Festplatte; sonst Rechner neu starten. 2) Prüfen, ob Updates bereitliegen "
                "(sudo rpi-eeprom-update, apt list --upgradable) und nur dann aktualisieren. "
                "3) FM-1 an die andere Buchsenart stecken (blau USB 3 / schwarz USB 2) – ohne Hub. "
                "4) Brauchst du den Ton des FM-1 nicht im Rechner (Studio und MIDI brauchen ihn nicht), in "
                "KDE-Systemeinstellungen › Audio beim FM-1 das Profil „Aus“ wählen; dann öffnet PipeWire den "
                "Audio-Datenstrom nicht mehr.", _ev(xdead + trig)))
        else:
            found.append(Finding(
                "xhci-dead", "error", "USB-Controller ist abgestürzt",
                "Der USB-Controller des Rechners hat aufgegeben; alle USB-Geräte waren danach weg.",
                "Rechner neu starten. Passiert es öfter: Hub mit eigenem Netzteil verwenden; prüfen, ob Kernel- oder "
                "Bootloader-Updates bereitliegen (sudo rpi-eeprom-update, apt list --upgradable), und nur dann "
                "aktualisieren.", _ev(xdead)))
    elif len(xwarn) >= 3:
        found.append(Finding(
            "xhci-warn", "warn", f"USB-Controller meldet Warnungen ({len(xwarn)}×)",
            "Meist harmlos, kann aber zu Aussetzern bei USB-Audio/MIDI führen.",
            "Wenn das FM-1 dabei hakt: anderen USB-Anschluss probieren (die blauen USB-3-Buchsen und die "
            "schwarzen USB-2-Buchsen hängen am Pi 5 an getrennten Controllern).", _ev(xwarn)))

    # --- snd-usb-audio
    freq = _grep(lines, r"cannot set freq|clock source \d+ is not valid|uac_clock_source_is_valid")
    urb = _grep(lines, r"cannot submit urb|retire_capture_urb|urb status|Unable to change format on ep|"
                       r"usb_set_interface failed|cannot get freq|delay: estimated \d+, actual \d+|"
                       r"snd-usb-audio.*error|snd_usb_audio.*error|ep \S+: no free urbs")
    if freq:
        found.append(Finding(
            "snd-freq", "warn", "USB-Audio: Abtastrate ließ sich nicht einstellen",
            "Ein Audio-Gerät hat die gewünschte Abtastrate abgelehnt. Das FM-1 arbeitet mit 48 kHz; fordert ein "
            "Programm 44,1 kHz an, kann es knacksen oder stumm bleiben.",
            "Aufnahme mit 48000 Hz starten (die Aufnahme-Seite macht das automatisch). In PipeWire die Standardrate "
            "auf 48000 lassen.", _ev(freq)))
    if urb:
        found.append(Finding(
            "snd-urb", "warn" if len(urb) < 20 else "error", f"USB-Audio: Übertragungsfehler ({len(urb)}×)",
            "Audio-Pakete zwischen FM-1 und Rechner gingen verloren. Folge: Knackser oder Aussetzer in Aufnahmen.",
            "FM-1 direkt am Pi anstecken (kein Hub), andere USB-Geräte am selben Anschluss abziehen, CPU-Last "
            "senken; bei vielen Meldungen Kabel tauschen.", _ev(urb)))
    xr = [ln for ln in parse_log(user_journal) if re.search(r"xrun|XRun|underrun|overrun", ln.msg)]
    if xr:
        found.append(Finding(
            "xrun", "warn" if len(xr) < 30 else "error", f"Audio-Aussetzer (XRuns) in PipeWire ({len(xr)}×)",
            "Der Audio-Server kam nicht hinterher – hörbar als Knackser.",
            "Andere Programme schließen, größere Puffer verwenden (PIPEWIRE_QUANTUM=1024/48000), Aufnahme nicht "
            "über einen USB-Hub.", _ev(xr)))

    # --- Unterspannung / Temperatur im Log
    uv = _grep(lines, r"under-?voltage detected")
    if uv:
        found.append(Finding(
            "undervoltage-log", "error", f"Unterspannung erkannt ({len(uv)}× seit dem Start)",
            "Das Netzteil liefert zu wenig Spannung. Dann drosselt der Pi, und USB-Geräte wie das FM-1 können sich "
            "trennen oder Aussetzer haben.",
            "Offizielles Raspberry-Pi-Netzteil verwenden (Pi 5: 27 W / 5 A), kurzes dickes USB-C-Kabel, keine "
            "stromhungrigen Geräte direkt am Pi.", _ev(uv)))
    hot = _grep(lines, r"temperature above threshold|thermal.*(critical|throttl)|cpu clock throttled")
    if hot:
        found.append(Finding(
            "thermal-log", "warn", "Überhitzung im Kernel-Protokoll",
            "Der Prozessor wurde wegen Hitze gebremst – Audio kann dann aussetzen.",
            "Kühler/Lüfter prüfen (Pi 5: Active Cooler), Gehäuse belüften.", _ev(hot)))

    # --- Speicherkarte
    io = _grep(lines, r"I/O error, dev (mmcblk|sd|nvme)|EXT4-fs error|mmc\d+: (error|timeout)|Buffer I/O error")
    if io:
        found.append(Finding(
            "disk-io", "warn", "Datenträger meldet Lesefehler",
            "Die Speicherkarte oder SSD hatte Fehler beim Lesen/Schreiben.",
            "Wichtige Daten (Backups im FM-1-Ordner) zusätzlich woanders sichern; Karte ggf. tauschen.", _ev(io)))
    return found


# ------------------------------------------------------------ Systemwerte ---
THROTTLE = {0: "Unterspannung jetzt", 1: "Takt begrenzt jetzt", 2: "gedrosselt jetzt", 3: "Temperaturgrenze jetzt",
            16: "Unterspannung seit dem Start", 17: "Takt begrenzt seit dem Start",
            18: "gedrosselt seit dem Start", 19: "Temperaturgrenze seit dem Start"}


def throttled_findings(raw: int | None) -> list[Finding]:
    if raw is None:
        return []
    names = [t for b, t in THROTTLE.items() if raw >> b & 1]
    ev = [f"vcgencmd get_throttled → throttled={hex(raw)} ({', '.join(names) or 'alles in Ordnung'})"]
    out = []
    if raw & 0x1:
        out.append(Finding("uv-now", "error", "Unterspannung – gerade jetzt",
                           "Das Netzteil bricht gerade ein. USB-Geräte können ausfallen.",
                           "Netzteil tauschen (Pi 5: offizielles 27-W-Netzteil), Kabel prüfen.", ev))
    elif raw & 0x10000:
        out.append(Finding("uv-past", "warn", "Unterspannung seit dem Start aufgetreten",
                           "Irgendwann seit dem Einschalten hat das Netzteil zu wenig geliefert.",
                           "Netzteil und Kabel prüfen, besonders wenn das FM-1 sich manchmal trennt.", ev))
    if raw & 0x8 or raw & 0x4:
        out.append(Finding("temp-now", "warn", "Pi wird gerade wegen Hitze gedrosselt",
                           "Hohe Temperatur bremst den Prozessor; Simulator und Aufnahme können stocken.",
                           "Für Kühlung sorgen (Lüfter, Gehäuse öffnen).", ev))
    elif raw & 0xC0000:
        out.append(Finding("temp-past", "info", "Seit dem Start einmal wegen Hitze gedrosselt",
                           "War kurz zu warm.", "Bei Aussetzern Kühlung prüfen.", ev))
    if not out:
        out.append(Finding("power-ok", "ok", "Stromversorgung und Temperatur in Ordnung", evidence=ev))
    return out


def get_throttled() -> int | None:
    rc, out, _ = _run(["vcgencmd", "get_throttled"], timeout=3)
    m = re.search(r"0x([0-9a-fA-F]+)", out) if rc == 0 else None
    return int(m.group(1), 16) if m else None


def _dt_u32(p: Path) -> int | None:
    try:
        b = p.read_bytes()
        return int.from_bytes(b[:4], "big") if len(b) >= 4 else None
    except OSError:
        return None


def pi5_power_findings(root: Path = Path("/proc/device-tree/chosen/power")) -> list[Finding]:
    """Pi 5: hat das Netzteil 5 A angemeldet? Sonst begrenzt die Firmware USB auf 600 mA gesamt."""
    if not root.exists():
        return []
    cur = _dt_u32(root / "max_current")
    usb_en = _dt_u32(root / "usb_max_current_enable")
    oc = _dt_u32(root / "usb_over_current_detected")
    out = []
    if oc:
        out.append(Finding("pi5-oc", "error", "Pi 5 hat Überstrom an USB erkannt",
                           "Die Firmware hat einen USB-Überstrom festgestellt.",
                           "Geräte einzeln abziehen und prüfen; stromhungrige Geräte an einen aktiven Hub."))
    if cur is not None and cur < 5000 and not usb_en:
        out.append(Finding("pi5-usb-budget", "warn", f"USB-Strom begrenzt (Netzteil meldet {cur / 1000:.1f} A)",
                           "Ohne 5-A-Netzteil gibt der Pi 5 an allen USB-Buchsen zusammen nur 600 mA ab. Das FM-1 "
                           "allein passt, mit Festplatte oder weiteren Geräten kann es knapp werden.",
                           "Offizielles 27-W-Netzteil benutzen oder einen USB-Hub mit eigenem Netzteil. (Fortgeschritten: "
                           "usb_max_current_enable=1 in /boot/firmware/config.txt – nur mit gutem Netzteil.)"))
    return out


def fm1_alsa_cards(cards_text: str | None = None) -> list[str]:
    if cards_text is None:
        try:
            cards_text = Path("/proc/asound/cards").read_text()
        except OSError:
            cards_text = ""
    return [ln.strip() for ln in cards_text.splitlines() if re.match(r"\s*\d+ \[", ln) and FM1_RE.search(ln)]


def midi_ports() -> list[str] | None:
    try:
        import mido
        return sorted(set(mido.get_input_names()) | set(mido.get_output_names()))
    except Exception:  # noqa: BLE001 – ohne rtmidi
        rc, out, _ = _run(["aconnect", "-l"])
        if rc != 0:
            return None
        return re.findall(r"client \d+: '([^']+)'", out)


def pipewire_running() -> bool | None:
    rc, _, _ = _run(["pw-cli", "info", "0"], timeout=3)
    if rc == 0:
        return True
    rc2, out, _ = _run(["systemctl", "--user", "is-active", "pipewire"], timeout=3)
    if rc2 == 127 and rc == 127:
        return None
    return out.strip() == "active"


def blocked_findings(holders=None, service=None) -> list[Finding]:
    """Soundkarte von einem Programm direkt belegt (an PipeWire vorbei) – z. B. Debians fluidsynth-Dienst."""
    from . import audiofix as AF
    try:
        holders = AF.playback_holders() if holders is None else holders
        enabled, active = AF.fluidsynth_service() if service is None else service
    except Exception:  # noqa: BLE001
        return []
    out = []
    fs = [h for h in holders if h.name.startswith("fluidsynth")]
    if active or (enabled and fs):
        out.append(Finding(
            "fluidsynth-service", "error" if fs else "warn",
            "Der FluidSynth-Dienst " + ("belegt die Soundkarte" if fs else "startet bei jeder Anmeldung"),
            "Debians Paket „fluidsynth“ bringt einen Dienst mit, der bei der Anmeldung startet und die erste "
            "Soundkarte (am Pi: HDMI) direkt öffnet. PipeWire bekommt sie dann nicht – kein HDMI-Ton, nur noch "
            "„Schein-Ausgabe“. Der Klangerzeuger der App braucht den Dienst nicht."
            + (" " + "; ".join(h.text for h in fs) + "." if fs else ""),
            "In der App: Hinweis „Dienst abschalten“ – oder im Terminal: systemctl --user disable --now fluidsynth "
            "&& systemctl --user restart wireplumber pipewire pipewire-pulse"))
    others = [h for h in holders if not h.name.startswith("fluidsynth")]
    if others:
        out.append(Finding(
            "snd-blocked", "warn", "Soundkarte von einem Programm direkt belegt",
            "; ".join(h.text for h in others) + ". Solange das so ist, kann PipeWire diese Karte nicht benutzen "
            "(fehlt dann als Ausgang).",
            "Das Programm beenden oder in seinen Einstellungen auf PipeWire/PulseAudio statt ALSA umstellen."))
    return out


def playback_finding(backends: list[str] | None = None, default=None) -> Finding:
    """Vorhören im Rechner: gibt es ein Abspielprogramm, und wohin geht der Ton (Standard-Ausgang)?"""
    from . import audioout
    if backends is None:
        backends = audioout.available_backends()
    if default is None:
        try:
            default = next((o for o in audioout.list_outputs(0) if o.default), None)
        except Exception:  # noqa: BLE001
            default = None
    if not backends:
        return Finding("playback", "warn", "Kein Abspielprogramm fürs Vorhören (pw-play, paplay, aplay, ffplay)",
                       "Ohne eines bleiben „Anhören“/„Vorhören“ im Rechner stumm (auf dem FM-1 abspielen geht trotzdem).",
                       "sudo apt install pipewire-bin")
    where = f" – Ton geht an „{default.description}“" if default else ""
    if default is not None and default.is_dummy:
        return Finding("playback", "warn", f"Vorhören: kein echter Audio-Ausgang{where}",
                       "PipeWire hat keinen Lautsprecher/Kopfhörer/HDMI-Ausgang gefunden und spielt ins Leere – "
                       "„Anhören“ und das Studio bleiben stumm. Häufig nach einem Absturz des USB-Controllers "
                       "(USB-Lautsprecher/-Soundkarte weg) oder ohne angeschlossenes HDMI-Gerät.",
                       "Lautsprecher/Soundkarte anschließen bzw. Rechner neu starten; dann in den "
                       "KDE-Systemeinstellungen › Audio den Ausgang wählen.")
    if default is not None and default.is_fm1:
        return Finding("playback", "warn", f"Vorhören: {backends[0]}{where}", audioout.output_hint(default),
                       "KDE-Systemeinstellungen › Audio: Lautsprecher/HDMI/Kopfhörer als Standard wählen – oder in der "
                       "App beim Vorhören „Ausgabe“ umstellen.")
    return Finding("playback", "ok", f"Vorhören: {backends[0]}{where}")


def status_findings(folder: Path | None = None) -> list[Finding]:
    out = []
    cards = fm1_alsa_cards()
    out.append(Finding("alsa", "ok", f"Soundkarte des FM-1 gefunden: {cards[0]}") if cards else
               Finding("alsa", "info", "Keine Soundkarte des FM-1 gefunden",
                       "Ohne angeschlossenes FM-1 ist das normal. Mit FM-1: es meldet sich als USB-Audio-Gerät.",
                       "FM-1 einschalten und per USB-Datenkabel anschließen."))
    ports = midi_ports()
    if ports is None:
        out.append(Finding("midi", "warn", "MIDI nicht nutzbar",
                           "Weder python3-rtmidi noch aconnect sind verfügbar.",
                           "sudo apt install python3-rtmidi alsa-utils"))
    else:
        fm = [p for p in ports if FM1_RE.search(p)]
        out.append(Finding("midi", "ok" if fm else "info",
                           f"MIDI-Port des FM-1: {fm[0]}" if fm else f"Kein FM-1-MIDI-Port ({len(ports)} andere Ports)"))
    pw = pipewire_running()
    out.append(Finding("pipewire", {True: "ok", False: "warn", None: "unknown"}[pw],
                       {True: "PipeWire läuft", False: "PipeWire läuft nicht",
                        None: "PipeWire-Status unbekannt"}[pw],
                       "" if pw else "Für die Aufnahme über PipeWire nötig (ALSA geht auch ohne).",
                       "" if pw else "systemctl --user enable --now pipewire pipewire-pulse wireplumber"))
    out.extend(blocked_findings())
    out.append(playback_finding())
    for exe, pkg, why in (("audacity", "audacity", "Aufnahmen bearbeiten"), ("ffmpeg", "ffmpeg", "MP3/FLAC-Samples"),
                          ("fluidsynth", "fluidsynth", "Klangerzeuger für MIDI-Spuren 5–8")):
        ok = shutil.which(exe) is not None
        out.append(Finding(f"tool-{exe}", "ok" if ok else "info", f"{exe} {'vorhanden' if ok else 'fehlt'} ({why})",
                           fix="" if ok else f"sudo apt install {pkg}"))
    if folder is not None:
        try:
            u = shutil.disk_usage(folder)
            gib = u.free / 2 ** 30
            lvl = "error" if u.free < 200 * 2 ** 20 else "warn" if u.free < 2 * 2 ** 30 else "ok"
            out.append(Finding("disk", lvl, f"Freier Platz im FM-1-Ordner: {gib:.1f} GiB".replace(".", ","),
                               "" if lvl == "ok" else "Backups, Aufnahmen und Firmware-Downloads brauchen Platz.",
                               "" if lvl == "ok" else "Alte Aufnahmen/Downloads löschen oder den Hauptordner verlegen."))
        except OSError:
            pass
    return out


def run_all(folder: Path | None = None, fm1_paths: set[str] | None = None) -> tuple[list[Finding], dict]:
    """kompletter Check (im Hintergrund-Thread aufrufen!). Liefert (Befunde, Meta)."""
    text, source, problem = read_kernel_log()
    meta = {"source": source, "time": time.time(), "lines": text.count("\n")}
    found: list[Finding] = []
    if problem:
        found.append(Finding(
            "log-access", "unknown", "Kernel-Protokoll nicht lesbar",
            f"Ohne Zugriff auf das Kernel-Protokoll lassen sich USB- und Stromprobleme nicht erkennen ({problem}).",
            "Deinen Benutzer in die Gruppe 'adm' aufnehmen: sudo usermod -aG adm $USER – danach ab- und wieder "
            "anmelden. Einmalig geht auch: sudo dmesg"))
    else:
        found += analyze_log(text, fm1_paths, read_user_journal())
    found += throttled_findings(get_throttled())
    found += pi5_power_findings()
    found += status_findings(folder)
    order = {"error": 0, "warn": 1, "unknown": 2, "info": 3, "ok": 4}
    found.sort(key=lambda f: order[f.level])
    return found, meta


def report(found: list[Finding], meta: dict | None = None) -> str:
    from . import __version__
    head = [f"FM-1 Companion {__version__} – Systemcheck {time.strftime('%d.%m.%Y %H:%M')}",
            f"Rechner: {Path('/proc/device-tree/model').read_text(errors='replace').strip(chr(0)) if Path('/proc/device-tree/model').exists() else os.uname().machine}",
            f"Quelle: {(meta or {}).get('source') or 'kein Kernel-Protokoll'}", ""]
    from .redact import redact_text             # Rechnername, Home, Tokens nie im geteilten Bericht
    return redact_text("\n".join(head + [f.text() for f in found]) + "\n")


if __name__ == "__main__":
    f, m = run_all(Path.home())
    print(report(f, m))
