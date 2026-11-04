# SPDX-License-Identifier: GPL-3.0-only
"""Sound card blocked? – programs that hold a playback sound card directly (bypassing PipeWire).

Background (Pi 5, Bookworm): Debian's ``fluidsynth`` package (recommended by the app as a sound engine) ships a
user service that starts at every login and opens the first sound card directly via ALSA – on the Pi
that is the HDMI card. PipeWire then cannot get it any more: no HDMI output, only "Dummy Output".
The app's sound engine does not need this service (it starts FluidSynth itself, via PipeWire).
No Qt here (for the system check, installer and UI).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .i18n import _

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
        return _("{name} (PID {pid}) is using sound card {card}, device {device}").format(
            name=self.name, pid=self.pid, card=self.card, device=self.device)


def _systemctl_user(*args: str, timeout: float = 5) -> tuple[int, str]:
    if not shutil.which("systemctl"):
        return 127, ""
    try:
        r = subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "").strip()
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def fluidsynth_service() -> tuple[bool, bool]:
    """-> (enabled, running) for Debian's fluidsynth user service."""
    _rc, en = _systemctl_user("is-enabled", SERVICE)
    _rc, act = _systemctl_user("is-active", SERVICE)
    return en.splitlines()[0] == "enabled" if en else False, act.splitlines()[0] == "active" if act else False


def playback_holders(proc: Path = Path("/proc")) -> list[Holder]:
    """Processes (of the current user – others are not readable) that hold a playback PCM open directly,
    except the audio services themselves (PipeWire & co. are allowed to)."""
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
    """Disable the user service (FluidSynth stays installed) and let PipeWire rescan for the card.
    -> (ok, German message)"""
    rc, _out = _systemctl_user("disable", "--now", SERVICE, timeout=15)
    if rc != 0:
        return False, _("The service could not be disabled. In a terminal: {command}").format(
            command="systemctl --user disable --now fluidsynth")
    if restart_audio:
        _systemctl_user("restart", "wireplumber", "pipewire", "pipewire-pulse", timeout=20)
    return True, _("The automatic FluidSynth service is disabled and the sound restarted – HDMI/speakers "
                   "should show up as outputs again. FluidSynth stays installed; the app's sound module "
                   "starts it by itself when you use it.")
