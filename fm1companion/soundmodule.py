# SPDX-License-Identifier: GPL-3.0-only
"""Der Pi als Klangerzeuger: FluidSynth spielt, was die MIDI-Spuren 5–8 von Melodee senden.

Melodee (firmware/src/eng_midi.c, midi_track.c, ui.c): Spuren 5–8 sind reine MIDI-Spuren
("MIDI Track"-Engine, kein Klang am FM-1). Sie senden ihre Noten und Controller über **USB-MIDI**
(den Port „Melodee“) auf dem Kanal ihres Parameters CH (E1, 1–16); eine neu angelegte Spur N
startet mit Kanal N, also 5–8. Die Tasten des FM-1 senden außerdem auf Kanal 1–4 (Spur 1–4) –
die spielt das FM-1 selbst; der Klangerzeuger lässt deshalb nur die gewählten Kanäle durch
(FluidSynth-MIDI-Router), sonst klänge alles doppelt.

FluidSynth läuft als eigener Prozess: ``fluidsynth -a <treiber> -m alsa_seq -p <name> -g … -z … -c …
-o synth.polyphony=… <soundfont>``. Bewusst ohne ``-i``/``-s``: ``-i`` schaltet die Befehlszeile auf
stdin ab, ``-s`` öffnet einen TCP-Server (Port 9800) für das ganze Netz. Befehle gehen als Textzeilen
über stdin (``select``, ``prog``, ``gain``, ``cc``, ``reverb``, ``router_*``); Schließen von stdin bzw.
``quit`` beendet ihn. Der FM-1-Ausgang wird per ``aconnect`` mit FluidSynths Sequencer-Port verbunden.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from .i18n import _
from .miditools import GM_PROGRAMS

SYSTEM_SF2 = [Path("/usr/share/sounds/sf2/FluidR3_GM.sf2"), Path("/usr/share/sounds/sf2/TimGM6mb.sf2"),
              Path("/usr/share/sounds/sf2/default-GM.sf2"), Path("/usr/share/sounds/sf3/MuseScore_General.sf3")]
INSTALL_HINT = "sudo apt install fluidsynth fluid-soundfont-gm"
CLIENT_NAME = "FM-1 Klangerzeuger"                   # ALSA-Clientname (Kennung, bleibt gleich)
DEFAULT_CHANNELS = [5, 6, 7, 8]          # Melodee: Spur 5–8 starten auf Kanal 5–8

# (Perioden-Größe, Anzahl) – Latenz ≈ Größe × Anzahl / Rate
LATENCY = {"niedrig": (64, 2), "ausgewogen": (128, 2), "sicher": (256, 3)}
# Anzeige je Einstellungswert (die Schlüssel sind gespeicherte Werte und bleiben deutsch)
LATENCY_DE = {"niedrig": _("low (~3 ms, needs a quiet Pi)"), "ausgewogen": _("balanced (~5 ms)"),
              "sicher": _("safe (~16 ms, no dropouts)")}
ROUTER_TYPES = ("note", "cc", "prog", "pbend", "cpress", "kpress")


def find_binary() -> str | None:
    return shutil.which("fluidsynth")


def find_soundfonts(user_dir: Path | None = None) -> list[Path]:
    out = [p for p in SYSTEM_SF2 if p.exists()]
    if user_dir and Path(user_dir).is_dir():
        out += sorted(p for p in Path(user_dir).iterdir() if p.suffix.lower() in (".sf2", ".sf3"))
    return out


_DRIVERS: dict[str, list[str]] = {}


def audio_drivers(exe: str | None = None) -> list[str]:
    """welche Audio-Treiber diese FluidSynth-Version kennt (``fluidsynth -a help``) – je Binary einmal gefragt,
    damit die Oberfläche nicht bei jedem Öffnen der Seite einen Prozess startet."""
    exe = exe or find_binary()
    if not exe:
        return []
    if exe in _DRIVERS:
        return _DRIVERS[exe]
    try:
        r = subprocess.run([exe, "-a", "help"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return []
    _DRIVERS[exe] = parse_drivers(r.stdout + r.stderr)
    return _DRIVERS[exe]


# typische Meldungen von FluidSynth -> was der Nutzer tun kann
FAIL_HINTS = [
    ("audio driver", _("The audio output could not be opened – is another program using it exclusively right now "
                       "(e.g. Audacity in recording mode)? Choose another driver under “Audio” "
                       "(pipewire/pulseaudio/alsa) or latency “safe”.")),
    ("alsa_seq", _("The ALSA sequencer is not reachable (snd-seq missing?). Restarting the Pi usually helps.")),
    ("soundfont", _("The sound bank could not be loaded – file damaged or not a .sf2/.sf3? Choose another one "
                    "under “Sound bank” (sudo apt install fluid-soundfont-gm).")),
    ("sfont", _("The sound bank could not be loaded – choose another one under “Sound bank”.")),
    ("permission", _("No permission for the audio device – is your user in the group “audio”?")),
    ("busy", _("The audio device is busy – another program holds it. Stop it there or choose driver “pipewire”.")),
]


def explain_failure(log) -> str:
    """die letzten Logzeilen von FluidSynth in einen verständlichen Hinweis übersetzen."""
    text = " ".join(log).lower()
    for key, hint in FAIL_HINTS:
        if key in text:
            return hint
    last = next((ln for ln in reversed(list(log)) if ln.strip()), "")
    if last:
        return _("FluidSynth quit right away: “{line}”. “Details” shows the whole output.").format(line=last[:160])
    return _("FluidSynth quit right away – without a message. “Details” shows the whole output.")


def parse_drivers(text: str) -> list[str]:
    m = re.search(r"options \(audio driver\):\s*(.*)", text)
    if not m:
        return []
    return [d.strip() for d in m.group(1).split(",") if d.strip()]


def pick_driver(available: list[str]) -> str:
    for d in ("pipewire", "pulseaudio", "alsa"):
        if d in available:
            return d
    return available[0] if available else "alsa"


@dataclass
class Channel:
    number: int                       # 1..16
    enabled: bool = True
    sfont: int = 1                    # SoundFont-ID in FluidSynth (Ladereihenfolge, ab 1)
    bank: int = 0
    program: int = 0
    volume: int = 100                 # CC7
    pan: int = 64                     # CC10
    reverb: int = 40                  # CC91
    chorus: int = 0                   # CC93


@dataclass
class Config:
    soundfont: str = ""
    extra_fonts: list[str] = field(default_factory=list)   # z. B. eigene Kits als .sf2
    driver: str = "auto"
    latency: str = "ausgewogen"
    gain: float = 0.6
    polyphony: int = 96
    rate: int = 48000
    reverb: bool = True
    chorus: bool = False


def build_command(exe: str, cfg: Config, driver: str) -> list[str]:
    size, count = LATENCY.get(cfg.latency, LATENCY["ausgewogen"])
    cmd = [exe, "-a", driver, "-m", "alsa_seq", "-p", CLIENT_NAME,
           "-g", f"{max(0.0, min(5.0, cfg.gain)):.2f}", "-z", str(size), "-c", str(count),
           "-r", str(cfg.rate), "-o", f"synth.polyphony={max(16, min(512, cfg.polyphony))}",
           "-o", "midi.autoconnect=0", "-o", f"synth.reverb.active={int(cfg.reverb)}",
           "-o", f"synth.chorus.active={int(cfg.chorus)}"]
    if driver == "alsa":
        cmd += ["-o", "audio.alsa.device=default"]
    if cfg.soundfont:
        cmd.append(cfg.soundfont)
    return cmd


def channel_commands(ch: Channel) -> list[str]:
    c = ch.number - 1
    return [f"select {c} {ch.sfont} {ch.bank} {ch.program}", f"cc {c} 7 {ch.volume}", f"cc {c} 10 {ch.pan}",
            f"cc {c} 91 {ch.reverb}", f"cc {c} 93 {ch.chorus}"]


def router_commands(channels: list[int]) -> list[str]:
    """nur diese Kanäle (1..16) durchlassen. Ohne Regeln lässt der Router nichts durch."""
    out = ["router_clear"]
    for typ in ROUTER_TYPES:
        for c in sorted(set(channels)):
            out += [f"router_begin {typ}", f"router_chan {c - 1} {c - 1} 1 0", "router_end"]
    return out


def program_label(bank: int, program: int) -> str:
    if bank == 128:
        return _("Drums {n}").format(n=program + 1)
    return f"{program + 1:03d} {GM_PROGRAMS[program % 128]}"


def _die_with_parent():
    """im Kindprozess (Linux): stirbt die App hart (Absturz, kill -9), bekommt FluidSynth SIGTERM –
    kein Zombie, der weiter die Audio-Hardware hält. Auf anderen Systemen ohne Wirkung."""
    try:
        import ctypes
        import signal
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGTERM, 0, 0, 0)      # PR_SET_PDEATHSIG
    except Exception:                                                                 # noqa: BLE001
        pass


class FluidSynth:
    """ein laufender FluidSynth-Prozess (alle Methoden aus dem GUI-Thread aufrufbar)."""

    def __init__(self, popen=subprocess.Popen):
        self._popen = popen
        self.p = None
        self.log: deque[str] = deque(maxlen=300)
        self.cmd: list[str] = []
        self.started = 0.0
        self.fonts: list[str] = []            # geladene SoundFonts, Index + 1 = ID
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self.p is not None and self.p.poll() is None

    def start(self, cmd: list[str], fonts: list[str]):
        self.stop()
        self.cmd = list(cmd)
        self.fonts = list(fonts)
        self.log.clear()
        self.p = self._popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, bufsize=1, start_new_session=True, preexec_fn=_die_with_parent)
        self.started = time.time()
        threading.Thread(target=self._reader, args=(self.p,), daemon=True, name="fm1c-fluid").start()

    def _reader(self, p):
        try:
            for line in p.stdout:
                line = line.strip().lstrip(">").strip()
                if line:
                    self.log.append(line)
        except (OSError, ValueError):
            pass

    def send(self, *lines: str) -> bool:
        if not self.running:
            return False
        with self._lock:
            try:
                for ln in lines:
                    self.p.stdin.write(ln + "\n")
                self.p.stdin.flush()
                return True
            except (OSError, ValueError):
                return False

    def load_font(self, path: str) -> int:
        """lädt einen weiteren SoundFont, gibt seine ID zurück (FluidSynth zählt ab 1 hoch)."""
        if path in self.fonts:
            return self.fonts.index(path) + 1
        self.send(f'load "{path}" 0')                 # FluidSynth 2 zerlegt Zeilen wie die Shell
        self.fonts.append(path)
        return len(self.fonts)

    def apply(self, channels: list[Channel], gain: float | None = None):
        cmds = []
        if gain is not None:
            cmds.append(f"gain {gain:.2f}")
        for ch in channels:
            if ch.enabled:
                cmds += channel_commands(ch)
        cmds += router_commands([c.number for c in channels if c.enabled])
        self.send(*cmds)

    def panic(self):
        self.send(*[f"cc {c} 123 0" for c in range(16)])

    def stop(self, timeout: float = 2.0, block: bool = True):
        """FluidSynth beenden. block=False: der Prozess wird sofort abgekoppelt (running ist gleich False) und
        im Hintergrund zu Ende gewartet – für den GUI-Thread, der nie warten darf."""
        p = self.p
        self.p = None
        if p is None:
            return
        if not block:
            import threading
            threading.Thread(target=self._finish, args=(p, timeout), daemon=True, name="fm1c-fluid-stop").start()
            return
        self._finish(p, timeout)

    @staticmethod
    def _finish(p, timeout: float):
        try:
            if p.poll() is None:
                try:
                    p.stdin.write("quit\n")
                    p.stdin.flush()
                    p.stdin.close()
                except (OSError, ValueError):
                    pass
                try:
                    p.wait(timeout)
                except subprocess.TimeoutExpired:
                    p.terminate()
                    try:
                        p.wait(1.5)
                    except subprocess.TimeoutExpired:
                        p.kill()
        except Exception:                                  # noqa: BLE001 – Beenden darf nie hängen
            pass

    @property
    def pid(self) -> int | None:
        return self.p.pid if self.p is not None else None

    def cpu_percent(self, _state={}) -> float | None:      # noqa: B006 – Messstand je Prozess
        """CPU-Last des Prozesses seit dem letzten Aufruf (aus /proc, Linux)."""
        pid = self.pid
        if pid is None:
            return None
        try:
            f = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            ticks = int(f[11]) + int(f[12])
        except (OSError, IndexError, ValueError):
            return None
        now = time.monotonic()
        hz = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
        last = _state.get(pid)
        _state[pid] = (now, ticks)
        if not last or now <= last[0]:
            return None
        return (ticks - last[1]) / hz / (now - last[0]) * 100

    def client_name(self) -> str:
        return CLIENT_NAME
