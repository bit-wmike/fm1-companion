# SPDX-License-Identifier: GPL-3.0-only
"""Audio output of the computer (without Qt): which playback programs exist, which outputs PipeWire/Pulse knows
and whether the default output is currently the FM-1 itself (USB audio) – then the preview is only audible on the FM-1.
Used by ui/audio.py (player) and syscheck.py (fm1-companion --check)."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from dataclasses import dataclass

from .i18n import _

BACKENDS = ("pw-play", "paplay", "aplay", "ffplay")
FM1_SINK = re.compile(r"fm-?1|melodee|m-?vave|felucca|sloop|skiff", re.I)
DUMMY_SINK = re.compile(r"auto_null|dummy|schein-?ausgabe", re.I)


def available_backends() -> list[str]:
    return [e for e in BACKENDS if shutil.which(e)]


@dataclass
class Output:
    name: str                 # PipeWire/Pulse node name (for --target / PULSE_SINK)
    description: str
    default: bool = False
    id: int = 0               # PipeWire object ID (for wpctl), 0 = unknown

    @property
    def is_fm1(self) -> bool:
        return bool(FM1_SINK.search(self.name + " " + self.description))

    @property
    def is_dummy(self) -> bool:
        """PipeWire's "Dummy Output" (auto_null): not a real device, the sound goes nowhere"""
        return bool(DUMMY_SINK.search(self.name + " " + self.description))


_outputs_cache: tuple[float, list[Output]] = (0.0, [])


def _run(args, timeout=2.0) -> str:
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def parse_pw_dump(text: str) -> list[Output]:
    """pw-dump (JSON) -> audio outputs (media.class Audio/Sink) with the default output."""
    try:
        objs = json.loads(text or "[]")
    except ValueError:
        return []
    default = ""
    outs = []
    for o in objs if isinstance(objs, list) else []:
        typ = o.get("type", "")
        if typ.endswith("Metadata") and (o.get("props") or {}).get("metadata.name") == "default":
            for m in o.get("metadata") or []:
                if m.get("key") == "default.audio.sink":
                    v = m.get("value")
                    if isinstance(v, str):
                        try:
                            v = json.loads(v)
                        except ValueError:
                            v = {"name": v}
                    default = (v or {}).get("name", "") if isinstance(v, dict) else ""
        elif typ.endswith("Node"):
            props = ((o.get("info") or {}).get("props") or {})
            if props.get("media.class") == "Audio/Sink" and props.get("node.name"):
                outs.append(Output(props["node.name"], props.get("node.description") or props.get("node.nick")
                                   or props["node.name"], id=int(o.get("id") or 0)))
    for x in outs:
        x.default = x.name == default
    return outs


def parse_pactl(short: str, default: str) -> list[Output]:
    outs = []
    for line in (short or "").splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[1]:
            outs.append(Output(parts[1], parts[1], parts[1] == default.strip()))
    return outs


def list_outputs(max_age: float = 8.0) -> list[Output]:
    """Audio outputs of the system (PipeWire via pw-dump, otherwise pactl). Cached briefly."""
    global _outputs_cache
    t, outs = _outputs_cache
    if time.monotonic() - t < max_age:
        return outs
    outs = []
    if shutil.which("pw-dump"):
        outs = parse_pw_dump(_run(["pw-dump"], 3.0))
    if not outs and shutil.which("pactl"):
        outs = parse_pactl(_run(["pactl", "list", "short", "sinks"]), _run(["pactl", "get-default-sink"]))
    _outputs_cache = (time.monotonic(), outs)
    return outs


def output_hint(o: Output | None) -> str:
    """German hint when the sound probably does not arrive where one expects it."""
    if o is not None and o.is_dummy:
        return _("There is no real audio output right now (“Dummy Output”) – the sound goes nowhere. "
                 "Connect speakers/a sound card (after a USB crash: restart the computer) and select it in "
                 "KDE System Settings › Audio.")
    if o is not None and o.is_fm1:
        return _("The sound goes to “{output}” – that is the FM-1 itself (USB audio). You will only hear it on "
                 "the FM-1's headphones/speaker (MASTER volume, on the Melodee USB AUDIO › playback on). "
                 "Choose another output: the “Output” button or KDE System Settings › Audio.").format(
            output=o.description)
    return ""




# ------------------------------------------------------------ never onto the FM-1 unnoticed
_KIND_ORDER = (re.compile(r"hdmi", re.I), re.compile(r"analog|headphone|kopfh|speaker|lautspr|line", re.I),
               re.compile(r"bluez|bluetooth", re.I))


def best_other(outs: list[Output]) -> Output | None:
    """the best real output that is NOT the FM-1 (HDMI, then analogue/headphones, then Bluetooth, then
    any) – None if there is none."""
    real = [o for o in outs if not o.is_fm1 and not o.is_dummy]
    for rx in _KIND_ORDER:
        hit = next((o for o in real if rx.search(o.name + " " + o.description)), None)
        if hit:
            return hit
    return real[0] if real else None


def effective_output(chosen: str = "", outs: list[Output] | None = None) -> str:
    """where the app sends its own sound: the user's explicit choice – otherwise the system
    default, unless that is the FM-1 (or the dummy output): then the best other real output.
    "" = use the system default."""
    if chosen:
        return chosen
    outs = list_outputs() if outs is None else outs
    d = next((o for o in outs if o.default), None)
    if d is None or not (d.is_fm1 or d.is_dummy):
        return ""
    o = best_other(outs)
    return o.name if o else ""


def set_default_sink(o: Output) -> bool:
    """Set the system's default output (pactl, otherwise wpctl)."""
    if shutil.which("pactl"):
        try:
            if subprocess.run(["pactl", "set-default-sink", o.name], capture_output=True, timeout=3).returncode == 0:
                return True
        except (OSError, subprocess.SubprocessError):
            pass
    if o.id and shutil.which("wpctl"):
        try:
            return subprocess.run(["wpctl", "set-default", str(o.id)], capture_output=True, timeout=3).returncode == 0
        except (OSError, subprocess.SubprocessError):
            pass
    return False


def keep_default_off_fm1(outs: list[Output] | None = None, setter=set_default_sink) -> tuple[Output, Output] | None:
    """If the FM-1 has (unintentionally, e.g. when plugged in) become the default output, reset the default to the best
    other real output. -> (FM-1, new default) or None (nothing to do / not possible).
    The FM-1 stays selectable as an output – it is just not used automatically."""
    global _outputs_cache
    outs = list_outputs(0) if outs is None else outs
    d = next((o for o in outs if o.default), None)
    if d is None or not d.is_fm1:
        return None
    o = best_other(outs)
    if o is None or not setter(o):
        return None
    _outputs_cache = (0.0, [])
    return d, o
