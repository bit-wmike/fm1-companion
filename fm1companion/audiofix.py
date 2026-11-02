# SPDX-License-Identifier: GPL-3.0-only
"""Soundkarte blockiert? – Programme, die eine Wiedergabe-Soundkarte direkt (an PipeWire vorbei) belegen.

Anlass (Pi 5, Bookworm): Debians Paket ``fluidsynth`` (von der App als Klangerzeuger empfohlen) bringt einen
Benutzer-Dienst mit, der bei jeder Anmeldung startet und per ALSA direkt die erste Soundkarte öffnet – auf dem Pi
die HDMI-Karte. PipeWire bekommt sie dann nicht mehr: kein HDMI-Ausgang, nur noch „Schein-Ausgabe“.
Der Klangerzeuger der App braucht diesen Dienst nicht (er startet FluidSynth selbst, über PipeWire).
Kein Qt hier (für Systemcheck, Installer und Oberfläche).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

SERVICE = "fluidsynth.service"
PCM_RE = re.compile(r"^/dev/snd/pcmC(\d+)D(\d+)p$")
AUDIO_DAEMONS = {"pipewire", "wireplumber", "pipewire-pulse", "pulseaudio", "jackd", "jackdbus"}


@dataclass
class Holder:
    pid: int
    name: str
    card: int
    device: int

    @property
    def text(self) -> str:
        return f"{self.name} (PID {self.pid}) belegt Soundkarte {self.card}, Gerät {self.device}"


def _systemctl_user(*args: str, timeout: float = 5) -> tuple[int, str]:
    if not shutil.which("systemctl"):
        return 127, ""
    try:
        r = subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "").strip()
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def fluidsynth_service() -> tuple[bool, bool]:
    """-> (aktiviert, läuft) für Debians fluidsynth-Benutzerdienst."""
    _rc, en = _systemctl_user("is-enabled", SERVICE)
    _rc, act = _systemctl_user("is-active", SERVICE)
    return en.splitlines()[0] == "enabled" if en else False, act.splitlines()[0] == "active" if act else False


def playback_holders(proc: Path = Path("/proc")) -> list[Holder]:
    """Prozesse (des eigenen Benutzers – fremde sind nicht lesbar), die eine Wiedergabe-PCM direkt offen haben,
    außer den Audio-Diensten selbst (PipeWire & Co. dürfen das)."""
    out: list[Holder] = []
    try:
        pids = [p for p in proc.iterdir() if p.name.isdigit()]
    except OSError:
        return out
    for p in pids:
        try:
            name = (p / "comm").read_text().strip()
        except OSError:
            continue
        if name in AUDIO_DAEMONS:
            continue
        try:
            fds = list((p / "fd").iterdir())
        except OSError:
            continue
        for fd in fds:
            try:
                m = PCM_RE.match(os.readlink(fd))
            except OSError:
                continue
            if m:
                out.append(Holder(int(p.name), name, int(m.group(1)), int(m.group(2))))
                break
    return out


def disable_fluidsynth_service(restart_audio: bool = True) -> tuple[bool, str]:
    """Benutzerdienst abschalten (FluidSynth bleibt installiert) und PipeWire die Karte neu suchen lassen.
    -> (ok, deutsche Meldung)"""
    rc, _ = _systemctl_user("disable", "--now", SERVICE, timeout=15)
    if rc != 0:
        return False, ("Der Dienst ließ sich nicht abschalten. Im Terminal: systemctl --user disable --now "
                       "fluidsynth")
    if restart_audio:
        _systemctl_user("restart", "wireplumber", "pipewire", "pipewire-pulse", timeout=20)
    return True, ("Der automatische FluidSynth-Dienst ist abgeschaltet und der Ton neu gestartet – HDMI/Lautsprecher "
                  "sollten wieder als Ausgang erscheinen. FluidSynth bleibt installiert; der Klangerzeuger der App "
                  "startet es selbst, wenn du ihn benutzt.")
