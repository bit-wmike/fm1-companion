# SPDX-License-Identifier: GPL-3.0-only
"""System check: finds problems affecting the FM-1 in the kernel log and in the system.

Sources: ``dmesg`` (or ``journalctl -k -b`` as a fallback), ``vcgencmd get_throttled``, sysfs/procfs,
programs in PATH. Each rule returns a ``Finding`` with a traffic light (ok/info/warn/error), an
explanation in simple German and a suggested fix. Without Qt – testable with sample logs.

  python3 -m fm1companion.syscheck            # report in the terminal
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from .i18n import N_, _, decimal, ngettext

LEVELS = ("ok", "info", "warn", "error", "unknown")
FM1_RE = re.compile(r"fm-?1|melodee|felucca|sloop|skiff|m-vave|ellic|wl82|uboot", re.I)
FM1_IDS = {"1209:0001"}                       # Melodee/Felucca (pid.codes) – "Ellic Studio Melodee FM-1"
UPDATE_IDS = {"4c4a:8057"}                    # JieLi rescue mode "WL82 UBOOT1.00"
LINE_RE = re.compile(r"^\s*(?:\[\s*(?P<ts>\d+\.\d+)\])?\s*(?:(?:\S+\s+)?kernel:\s*)?(?P<msg>.*)$")
USBDEV_RE = re.compile(r"\busb (?P<path>\d+-[\d.]+)(?::[\d.]+)?:\s*(?P<rest>.*)")
PORT_RE = re.compile(r"\busb (?:usb)?(?P<path>\d+-port[\d.]+|\d+-[\d.]+-port\d+):\s*(?P<rest>.*)")


@dataclass
class Finding:
    key: str
    level: str                 # ok | info | warn | error | unknown
    title: str
    detail: str = ""           # what does this mean?
    fix: str = ""              # what to do?
    evidence: list[str] = field(default_factory=list)   # matching log lines

    def text(self) -> str:
        mark = {"ok": "OK  ", "info": "INFO", "warn": "WARN", "error": _("FAIL"), "unknown": "?   "}[self.level]
        out = [f"[{mark}] {self.title}"]
        if self.detail:
            out.append("       " + self.detail)
        if self.fix:
            out.append("       " + _("Fix: {fix}").format(fix=self.fix))
        for e in self.evidence[:6]:
            out.append("         | " + e)
        return "\n".join(out)


@dataclass
class LogLine:
    ts: float | None
    msg: str
    raw: str


def parse_log(text: str) -> list[LogLine]:
    """Split dmesg or journalctl output (short-monotonic) into lines with timestamps."""
    out = []
    for raw in text.splitlines():
        if not raw.strip() or raw.startswith("-- "):
            continue
        m = LINE_RE.match(raw)
        ts = float(m.group("ts")) if m and m.group("ts") else None
        out.append(LogLine(ts, (m.group("msg") if m else raw).strip(), raw.rstrip()))
    return out


# ----------------------------------------------------------- read log ---
def _run(args, timeout=6) -> tuple[int, str, str]:
    if not shutil.which(args[0]):
        return 127, "", _("{program} not found").format(program=args[0])
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, errors="replace")
        return p.returncode, p.stdout, p.stderr
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, "", str(e)


def read_kernel_log() -> tuple[str, str, str]:
    """(text, source, problem). Source "dmesg" or "journalctl"; problem empty if it worked."""
    rc, out, err = _run(["dmesg"])
    if rc == 0 and out.strip():
        return out, "dmesg", ""
    rc2, out2, err2 = _run(["journalctl", "-k", "-b", "--no-pager", "-o", "short-monotonic"])
    lines = [ln for ln in out2.splitlines() if ln.strip() and not ln.startswith(("Hint:", "-- "))]
    if rc2 == 0 and len(lines) > 3:
        return out2, "journalctl", ""
    why = (err or "").strip() or (err2 or "").strip() or _("no output")
    return "", "", why


def read_user_journal() -> str:
    """Messages from PipeWire/WirePlumber in this session (for XRuns)."""
    rc, out, _err = _run(["journalctl", "--user", "-b", "--no-pager", "-o", "short-monotonic",
                       "-u", "pipewire", "-u", "wireplumber", "-u", "pipewire-pulse"])
    return out if rc == 0 else ""


# ------------------------------------------------------------ evaluate ---
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

    # --- FM-1 keeps reconnecting
    for path, d in fm1.items():
        name = d.product or "FM-1"
        ev = [ln for ln in lines if re.search(rf"\busb {re.escape(path)}:\s*(USB disconnect|new \S+-speed)", ln.msg)]
        burst = _max_in_window(d.disconnects, 600)
        if burst >= 3:
            found.append(Finding(
                f"fm1-loop:{path}", "error", _("{name} keeps disconnecting (port {path})").format(name=name, path=path),
                _("{count} disconnects since boot, {burst} of them within 10 minutes. This is almost "
                  "always caused by the cable, the power supply or a hub – rarely by the firmware.").format(
                      count=len(d.disconnects), burst=burst),
                _("Try a different (short) USB cable, plug the FM-1 directly into the Pi instead of a "
                  "hub, with a hub use one with its own power supply. Also check the Pi's power supply "
                  "(see under-voltage)."),
                _ev(ev, 8)))
        elif d.disconnects:
            found.append(Finding(
                f"fm1-disc:{path}", "info", _("{name} was disconnected {count}× (port {path})").format(
                    name=name, count=len(d.disconnects), path=path),
                _("Single disconnects are normal if you unplugged the FM-1, switched it off or installed "
                  "a firmware."), _("Only if you did nothing: check the cable and the port."), _ev(ev, 4)))
    if fm1 and not any(f.key.startswith(("fm1-loop", "fm1-disc")) for f in found):
        d = next(iter(fm1.values()))
        found.append(Finding("fm1-usb", "ok", _("{name} stable on USB ({path}, {ids})").format(
            name=d.product or 'FM-1', path=d.path, ids=d.ids or 'ID ?')))

    # --- errors while enumerating a USB device (-71/-110 …)
    enum = _grep(lines, r"device descriptor read/\d+, error|device not accepting address|unable to enumerate USB device|"
                        r"cannot enable\. Maybe the USB cable is bad|can't set config #\d+, error")
    if enum:
        paths = sorted({m.group("path") for ln in enum for m in [USBDEV_RE.search(ln.msg) or PORT_RE.search(ln.msg)] if m})
        on_fm1 = any(p in fm1 or any(p.startswith((f + ".", f + "-")) for f in fm1) for p in paths)
        found.append(Finding(
            "usb-enum", "error" if on_fm1 or len(enum) >= 3 else "warn",
            _("USB device could not be recognized properly ({ports})").format(ports=", ".join(paths)) if paths
            else _("USB device could not be recognized properly"),
            _("The Pi got no clean answer from the device when it was plugged in (error -71/-110). "
              "Typical for a bad or too long cable, a charge-only cable without data lines or too little "
              "power."),
            _("Use a different data cable, plug it directly into the Pi, check the power supply. For the "
              "FM-1: switch it off and on once."), _ev(enum)))

    # --- overcurrent
    oc = _grep(lines, r"over-?current")
    if oc:
        found.append(Finding(
            "usb-overcurrent", "error", _("Over-current on a USB port"),
            _("A device drew more current than the port may deliver – the Pi then switches the port off."),
            _("Connect power-hungry devices (hard disks, chargers) to a USB hub with its own power "
              "supply; check the Pi's power supply (Pi 5: official 27 W power supply)."), _ev(oc)))
    power = _grep(lines, r"insufficient available bus power|rejected \d+ configuration")
    if power:
        found.append(Finding(
            "usb-power", "error", _("USB device rejected: not enough power on the bus"),
            _("The USB port (or hub) cannot power the device."),
            _("Plug the device directly into the Pi or into a hub with its own power supply."), _ev(power)))

    # --- xHCI (USB controller)
    xdead = _grep(lines, r"xhci.*(not responding|assume dead|HC died|host system error|Host halt failed)")
    xwarn = _grep(lines, r"xhci.*(WARN|ERROR (?!unknown event type 37)|Event TRB for slot \d+ ep \d+ with no TDs queued)")
    if xdead:
        # narrow down the trigger: audio start of the FM-1 (usb_set_interface on its port) shortly before the crash?
        t_dead = next((ln.ts for ln in xdead if ln.ts is not None), None)
        trig = [ln for ln in lines if t_dead is not None and ln.ts is not None and abs(ln.ts - t_dead) <= 5
                and re.search(r"usb_set_interface failed|cannot submit urb|Unable to change format", ln.msg)
                and any(re.search(rf"\busb {re.escape(pth)}:", ln.msg) for pth in fm1)]
        if trig:
            found.append(Finding(
                "xhci-dead", "error", _("USB controller crashed – when the FM-1 audio started"),
                _("The computer's USB controller gave up; all USB devices were gone afterwards. At the "
                  "same time, switching on the FM-1 audio stream failed (usb_set_interface). The FM-1 is "
                  "also a USB sound card; PipeWire opens it by itself. On the Pi 5, such an audio start "
                  "of some USB devices can crash the controller – this is a problem of the "
                  "kernel/controller, not of the FM-1 or the app."),
                _("1) Without a reboot: re-register only this controller (echo xhci-hcd.N | sudo tee "
                  "/sys/bus/platform/drivers/xhci-hcd/unbind, then …/bind; N from the message above) – "
                  "never the controller with the system disk; otherwise restart the computer. 2) Check "
                  "whether updates are available (sudo rpi-eeprom-update, apt list --upgradable) and only "
                  "then update. 3) Plug the FM-1 into the other kind of socket (blue USB 3 / black USB 2) "
                  "– without a hub. 4) If you don't need the FM-1 sound on the computer (Studio and MIDI "
                  "don't need it), choose the profile “Off” for the FM-1 in KDE System Settings › Audio; "
                  "then PipeWire no longer opens the audio stream."), _ev(xdead + trig)))
        else:
            found.append(Finding(
                "xhci-dead", "error", _("USB controller crashed"),
                _("The computer's USB controller gave up; all USB devices were gone afterwards."),
                _("Restart the computer. If it happens more often: use a hub with its own power supply; "
                  "check whether kernel or bootloader updates are available (sudo rpi-eeprom-update, apt "
                  "list --upgradable), and only then update."), _ev(xdead)))
    elif len(xwarn) >= 3:
        found.append(Finding(
            "xhci-warn", "warn", _("USB controller reports warnings ({count}×)").format(count=len(xwarn)),
            _("Mostly harmless, but can cause dropouts in USB audio/MIDI."),
            _("If the FM-1 stutters: try a different USB port (on the Pi 5 the blue USB 3 sockets and the "
              "black USB 2 sockets are on separate controllers)."), _ev(xwarn)))

    # --- snd-usb-audio
    freq = _grep(lines, r"cannot set freq|clock source \d+ is not valid|uac_clock_source_is_valid")
    urb = _grep(lines, r"cannot submit urb|retire_capture_urb|urb status|Unable to change format on ep|"
                       r"usb_set_interface failed|cannot get freq|delay: estimated \d+, actual \d+|"
                       r"snd-usb-audio.*error|snd_usb_audio.*error|ep \S+: no free urbs")
    if freq:
        found.append(Finding(
            "snd-freq", "warn", _("USB audio: sample rate could not be set"),
            _("An audio device rejected the requested sample rate. The FM-1 works at 48 kHz; if a program "
              "requests 44.1 kHz, it may crackle or stay silent."),
            _("Start recording at 48000 Hz (the recording page does this automatically). Leave the "
              "default rate in PipeWire at 48000."), _ev(freq)))
    if urb:
        found.append(Finding(
            "snd-urb", "warn" if len(urb) < 20 else "error", _("USB audio: transfer errors ({count}×)").format(
                count=len(urb)),
            _("Audio packets between the FM-1 and the computer were lost. Result: clicks or dropouts in "
              "recordings."),
            _("Plug the FM-1 directly into the Pi (no hub), unplug other USB devices on the same port, "
              "reduce the CPU load; with many messages replace the cable."), _ev(urb)))
    xr = [ln for ln in parse_log(user_journal) if re.search(r"xrun|XRun|underrun|overrun", ln.msg)]
    if xr:
        found.append(Finding(
            "xrun", "warn" if len(xr) < 30 else "error", _("Audio dropouts (XRuns) in PipeWire ({count}×)").format(
                count=len(xr)),
            _("The audio server could not keep up – audible as clicks."),
            _("Close other programs, use larger buffers (PIPEWIRE_QUANTUM=1024/48000), don't record via a "
              "USB hub."), _ev(xr)))

    # --- undervoltage / temperature in the log
    uv = _grep(lines, r"under-?voltage detected")
    if uv:
        found.append(Finding(
            "undervoltage-log", "error", _("Under-voltage detected ({count}× since boot)").format(count=len(uv)),
            _("The power supply delivers too little voltage. Then the Pi throttles, and USB devices like "
              "the FM-1 can disconnect or have dropouts."),
            _("Use the official Raspberry Pi power supply (Pi 5: 27 W / 5 A), a short thick USB-C cable, "
              "no power-hungry devices directly on the Pi."), _ev(uv)))
    hot = _grep(lines, r"temperature above threshold|thermal.*(critical|throttl)|cpu clock throttled")
    if hot:
        found.append(Finding(
            "thermal-log", "warn", _("Overheating in the kernel log"),
            _("The processor was slowed down because of heat – audio may then drop out."),
            _("Check the cooler/fan (Pi 5: Active Cooler), ventilate the case."), _ev(hot)))

    # --- memory card
    io = _grep(lines, r"I/O error, dev (mmcblk|sd|nvme)|EXT4-fs error|mmc\d+: (error|timeout)|Buffer I/O error")
    if io:
        found.append(Finding(
            "disk-io", "warn", _("Storage device reports read errors"),
            _("The memory card or SSD had errors while reading/writing."),
            _("Also save important data (backups in the FM-1 folder) somewhere else; replace the card if "
              "necessary."), _ev(io)))
    return found


# ------------------------------------------------------------ system values ---
THROTTLE = {0: N_("under-voltage now"), 1: N_("frequency capped now"), 2: N_("throttled now"),
            3: N_("temperature limit now"), 16: N_("under-voltage since boot"), 17: N_("frequency capped since boot"),
            18: N_("throttled since boot"), 19: N_("temperature limit since boot")}


def throttled_findings(raw: int | None) -> list[Finding]:
    if raw is None:
        return []
    names = [_(t) for b, t in THROTTLE.items() if raw >> b & 1]
    ev = [f"vcgencmd get_throttled → throttled={hex(raw)} ({', '.join(names) or _('all fine')})"]
    out = []
    if raw & 0x1:
        out.append(Finding("uv-now", "error", _("Under-voltage – right now"),
                           _("The power supply is dropping right now. USB devices may fail."),
                           _("Replace the power supply (Pi 5: official 27 W power supply), check the cable."), ev))
    elif raw & 0x10000:
        out.append(Finding("uv-past", "warn", _("Under-voltage occurred since boot"),
                           _("At some point since switching on, the power supply delivered too little."),
                           _("Check the power supply and cable, especially if the FM-1 sometimes "
                             "disconnects."), ev))
    if raw & 0x8 or raw & 0x4:
        out.append(Finding("temp-now", "warn", _("Pi is being throttled because of heat right now"),
                           _("High temperature slows the processor down; the simulator and recording may "
                             "stutter."),
                           _("Provide cooling (fan, open the case)."), ev))
    elif raw & 0xC0000:
        out.append(Finding("temp-past", "info", _("Throttled once because of heat since boot"),
                           _("It was too warm for a short time."), _("If there are dropouts, check the cooling."), ev))
    if not out:
        out.append(Finding("power-ok", "ok", _("Power supply and temperature fine"), evidence=ev))
    return out


def get_throttled() -> int | None:
    rc, out, _err = _run(["vcgencmd", "get_throttled"], timeout=3)
    m = re.search(r"0x([0-9a-fA-F]+)", out) if rc == 0 else None
    return int(m.group(1), 16) if m else None


def _dt_u32(p: Path) -> int | None:
    try:
        b = p.read_bytes()
        return int.from_bytes(b[:4], "big") if len(b) >= 4 else None
    except OSError:
        return None


def pi5_power_findings(root: Path = Path("/proc/device-tree/chosen/power")) -> list[Finding]:
    """Pi 5: has the power supply announced 5 A? Otherwise the firmware limits USB to 600 mA in total."""
    if not root.exists():
        return []
    cur = _dt_u32(root / "max_current")
    usb_en = _dt_u32(root / "usb_max_current_enable")
    oc = _dt_u32(root / "usb_over_current_detected")
    out = []
    if oc:
        out.append(Finding("pi5-oc", "error", _("Pi 5 detected over-current on USB"),
                           _("The firmware detected a USB over-current."),
                           _("Unplug the devices one by one and check them; power-hungry devices to an "
                             "active hub.")))
    if cur is not None and cur < 5000 and not usb_en:
        out.append(Finding("pi5-usb-budget", "warn", _("USB current limited (power supply reports {amps} A)").format(
            amps=decimal(cur / 1000, 1)),
                           _("Without a 5 A power supply the Pi 5 delivers only 600 mA on all USB sockets "
                             "together. The FM-1 alone fits; with a hard disk or more devices it can get "
                             "tight."),
                           _("Use the official 27 W power supply or a USB hub with its own power supply. "
                             "(Advanced: usb_max_current_enable=1 in /boot/firmware/config.txt – only "
                             "with a good power supply.)")))
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
    except Exception:  # noqa: BLE001 – without rtmidi
        rc, out, _err = _run(["aconnect", "-l"])
        if rc != 0:
            return None
        return re.findall(r"client \d+: '([^']+)'", out)


def pipewire_running() -> bool | None:
    rc, _out, _err = _run(["pw-cli", "info", "0"], timeout=3)
    if rc == 0:
        return True
    rc2, out, _err = _run(["systemctl", "--user", "is-active", "pipewire"], timeout=3)
    if rc2 == 127 and rc == 127:
        return None
    return out.strip() == "active"


def blocked_findings(holders=None, service=None) -> list[Finding]:
    """Sound card held directly by a program (bypassing PipeWire) – e.g. Debian's fluidsynth service."""
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
            _("The FluidSynth service is occupying the sound card") if fs
            else _("The FluidSynth service starts at every login"),
            _("Debian's package “fluidsynth” comes with a service that starts at login and opens the "
              "first sound card (on the Pi: HDMI) directly. PipeWire then doesn't get it – no HDMI sound, "
              "only “Dummy Output”. The app's sound generator doesn't need the service.")
            + (" " + "; ".join(h.text for h in fs) + "." if fs else ""),
            _("In the app: notice “Disable service” – or in the terminal: systemctl --user disable --now "
              "fluidsynth && systemctl --user restart wireplumber pipewire pipewire-pulse")))
    others = [h for h in holders if not h.name.startswith("fluidsynth")]
    if others:
        out.append(Finding(
            "snd-blocked", "warn", _("Sound card occupied directly by a program"),
            _("{programs}. As long as this is the case, PipeWire cannot use this card (it is then missing as an "
              "output).").format(programs="; ".join(h.text for h in others)),
            _("Quit the program or switch it from ALSA to PipeWire/PulseAudio in its settings.")))
    return out


def playback_finding(backends: list[str] | None = None, default=None) -> Finding:
    """Preview on the computer: is there a playback program, and where does the sound go (default output)?"""
    from . import audioout
    if backends is None:
        backends = audioout.available_backends()
    if default is None:
        try:
            default = next((o for o in audioout.list_outputs(0) if o.default), None)
        except Exception:  # noqa: BLE001
            default = None
    if not backends:
        return Finding("playback", "warn", _("No player for previewing (pw-play, paplay, aplay, ffplay)"),
                       _("Without one, “Listen”/“Preview” stay silent on the computer (playing on the "
                         "FM-1 still works)."),
                       "sudo apt install pipewire-bin")
    def where(title: str) -> str:
        return _("{title} – sound goes to “{output}”").format(title=title, output=default.description) \
            if default else title
    if default is not None and default.is_dummy:
        return Finding("playback", "warn", where(_("Preview: no real audio output")),
                       _("PipeWire found no speaker/headphone/HDMI output and plays into the void – "
                         "“Listen” and the Studio stay silent. Often after a crash of the USB controller "
                         "(USB speaker/sound card gone) or without a connected HDMI device."),
                       _("Connect a speaker/sound card or restart the computer; then choose the output in "
                         "KDE System Settings › Audio."))
    if default is not None and default.is_fm1:
        return Finding("playback", "warn", where(_("Preview: {player}").format(player=backends[0])),
                       audioout.output_hint(default),
                       _("KDE System Settings › Audio: choose speakers/HDMI/headphones as default – or "
                         "switch “Output” in the app when previewing."))
    return Finding("playback", "ok", where(_("Preview: {player}").format(player=backends[0])))


def status_findings(folder: Path | None = None) -> list[Finding]:
    out = []
    cards = fm1_alsa_cards()
    out.append(Finding("alsa", "ok", _("FM-1 sound card found: {card}").format(card=cards[0])) if cards else
               Finding("alsa", "info", _("No FM-1 sound card found"),
                       _("This is normal without a connected FM-1. With an FM-1: it shows up as a USB "
                         "audio device."),
                       _("Switch the FM-1 on and connect it with a USB data cable.")))
    ports = midi_ports()
    if ports is None:
        out.append(Finding("midi", "warn", _("MIDI not usable"),
                           _("Neither python3-rtmidi nor aconnect is available."),
                           "sudo apt install python3-rtmidi alsa-utils"))
    else:
        fm = [p for p in ports if FM1_RE.search(p)]
        title = _("FM-1 MIDI port: {port}").format(port=fm[0]) if fm else \
            ngettext("No FM-1 MIDI port ({n} other port)", "No FM-1 MIDI port ({n} other ports)", len(ports)).format(
                n=len(ports))
        out.append(Finding("midi", "ok" if fm else "info", title))
    pw = pipewire_running()
    out.append(Finding("pipewire", {True: "ok", False: "warn", None: "unknown"}[pw],
                       {True: _("PipeWire is running"), False: _("PipeWire is not running"),
                        None: _("PipeWire status unknown")}[pw],
                       "" if pw else _("Needed for recording via PipeWire (ALSA works without it)."),
                       "" if pw else "systemctl --user enable --now pipewire pipewire-pulse wireplumber"))
    out.extend(blocked_findings())
    out.append(playback_finding())
    for exe, pkg, why in (("audacity", "audacity", N_("editing recordings")), ("ffmpeg", "ffmpeg", N_("MP3/FLAC samples")),
                          ("fluidsynth", "fluidsynth", N_("sound generator for MIDI tracks 5–8"))):
        ok = shutil.which(exe) is not None
        title = (_("{program} available ({purpose})") if ok else _("{program} missing ({purpose})")).format(
            program=exe, purpose=_(why))
        out.append(Finding(f"tool-{exe}", "ok" if ok else "info", title,
                           fix="" if ok else f"sudo apt install {pkg}"))
    if folder is not None:
        try:
            u = shutil.disk_usage(folder)
            gib = u.free / 2 ** 30
            lvl = "error" if u.free < 200 * 2 ** 20 else "warn" if u.free < 2 * 2 ** 30 else "ok"
            out.append(Finding("disk", lvl, _("Free space in the FM-1 folder: {size} GiB").format(size=decimal(gib, 1)),
                               "" if lvl == "ok" else _("Backups, recordings and firmware downloads need "
                                                        "space."),
                               "" if lvl == "ok" else _("Delete old recordings/downloads or move the main "
                                                        "folder.")))
        except OSError:
            pass
    return out


def run_all(folder: Path | None = None, fm1_paths: set[str] | None = None) -> tuple[list[Finding], dict]:
    """complete check (call in a background thread!). Returns (findings, meta)."""
    text, source, problem = read_kernel_log()
    meta = {"source": source, "time": time.time(), "lines": text.count("\n")}
    found: list[Finding] = []
    if problem:
        found.append(Finding(
            "log-access", "unknown", _("Kernel log not readable"),
            _("Without access to the kernel log, USB and power problems cannot be detected ({problem}).").format(
                problem=problem),
            _("Add your user to the group 'adm': sudo usermod -aG adm $USER – then log out and in again. "
              "For once also: sudo dmesg")))
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
    model = Path("/proc/device-tree/model")
    head = [_("FM-1 Companion {version} – system check {time}").format(version=__version__,
                                                                     time=time.strftime("%d.%m.%Y %H:%M")),
            _("Computer: {model}").format(model=model.read_text(errors="replace").strip(chr(0)) if model.exists()
                                          else os.uname().machine),
            _("Source: {source}").format(source=(meta or {}).get("source") or _("no kernel log")), ""]
    from .redact import redact_text             # host name, home, tokens never in the shared report
    return redact_text("\n".join(head + [f.text() for f in found]) + "\n")


if __name__ == "__main__":
    f, m = run_all(Path.home())
    print(report(f, m))
