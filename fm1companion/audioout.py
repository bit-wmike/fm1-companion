# SPDX-License-Identifier: GPL-3.0-only
"""Audio-Ausgabe des Rechners (ohne Qt): welche Abspielprogramme es gibt, welche Ausgänge PipeWire/Pulse kennt
und ob der Standard-Ausgang gerade das FM-1 selbst ist (USB-Audio) – dann hört man das Vorhören nur am FM-1.
Benutzt von ui/audio.py (Player) und syscheck.py (fm1-companion --check)."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from dataclasses import dataclass

BACKENDS = ("pw-play", "paplay", "aplay", "ffplay")
FM1_SINK = re.compile(r"fm-?1|melodee|m-?vave|felucca|sloop|skiff", re.I)
DUMMY_SINK = re.compile(r"auto_null|dummy|schein-?ausgabe", re.I)


def available_backends() -> list[str]:
    return [e for e in BACKENDS if shutil.which(e)]


@dataclass
class Output:
    name: str                 # PipeWire/Pulse-Knotenname (für --target / PULSE_SINK)
    description: str
    default: bool = False
    id: int = 0               # PipeWire-Objekt-ID (für wpctl), 0 = unbekannt

    @property
    def is_fm1(self) -> bool:
        return bool(FM1_SINK.search(self.name + " " + self.description))

    @property
    def is_dummy(self) -> bool:
        """PipeWires „Dummy Output“ / „Schein-Ausgabe“ (auto_null): kein echtes Gerät, der Ton geht ins Leere"""
        return bool(DUMMY_SINK.search(self.name + " " + self.description))


_outputs_cache: tuple[float, list[Output]] = (0.0, [])


def _run(args, timeout=2.0) -> str:
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def parse_pw_dump(text: str) -> list[Output]:
    """pw-dump (JSON) -> Audio-Ausgänge (media.class Audio/Sink) mit dem Standard-Ausgang."""
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
    """Audio-Ausgänge des Systems (PipeWire über pw-dump, sonst pactl). Kurz zwischengespeichert."""
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
    """deutscher Hinweis, wenn der Ton vermutlich nicht dort ankommt, wo man ihn erwartet."""
    if o is not None and o.is_dummy:
        return ("Es gibt gerade keinen echten Audio-Ausgang („Schein-Ausgabe“) – der Ton geht ins Leere. "
                "Lautsprecher/Soundkarte anschließen (nach einem USB-Absturz: Rechner neu starten) und in "
                "KDE-Systemeinstellungen › Audio auswählen.")
    if o is not None and o.is_fm1:
        return (f"Der Ton geht an „{o.description}“ – das ist das FM-1 selbst (USB-Audio). Zu hören ist er dann nur "
                "am Kopfhörer/Lautsprecher des FM-1 (MASTER-Lautstärke, bei Melodee USB AUDIO › Wiedergabe an). "
                "Anderen Ausgang wählen: Knopf „Ausgabe“ oder KDE-Systemeinstellungen › Audio.")
    return ""




# ------------------------------------------------------------ nie unbemerkt aufs FM-1
_KIND_ORDER = (re.compile(r"hdmi", re.I), re.compile(r"analog|headphone|kopfh|speaker|lautspr|line", re.I),
               re.compile(r"bluez|bluetooth", re.I))


def best_other(outs: list[Output]) -> Output | None:
    """der beste echte Ausgang, der NICHT das FM-1 ist (HDMI, dann analog/Kopfhörer, dann Bluetooth, dann
    irgendeiner) – None, wenn es keinen gibt."""
    real = [o for o in outs if not o.is_fm1 and not o.is_dummy]
    for rx in _KIND_ORDER:
        hit = next((o for o in real if rx.search(o.name + " " + o.description)), None)
        if hit:
            return hit
    return real[0] if real else None


def effective_output(chosen: str = "", outs: list[Output] | None = None) -> str:
    """wohin die App ihren eigenen Ton schickt: die ausdrückliche Wahl des Nutzers – sonst der Standard des
    Systems, außer der ist das FM-1 (oder die Schein-Ausgabe): dann der beste andere echte Ausgang.
    "" = Standard des Systems benutzen."""
    if chosen:
        return chosen
    outs = list_outputs() if outs is None else outs
    d = next((o for o in outs if o.default), None)
    if d is None or not (d.is_fm1 or d.is_dummy):
        return ""
    o = best_other(outs)
    return o.name if o else ""


def set_default_sink(o: Output) -> bool:
    """Standard-Ausgang des Systems setzen (pactl, sonst wpctl)."""
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
    """Ist das FM-1 (ungewollt, z. B. beim Anstecken) Standard-Ausgang geworden, den Standard auf den besten
    anderen echten Ausgang zurücksetzen. -> (FM-1, neuer Standard) oder None (nichts zu tun / nicht möglich).
    Das FM-1 bleibt als Ausgang wählbar – es wird nur nicht von selbst benutzt."""
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
