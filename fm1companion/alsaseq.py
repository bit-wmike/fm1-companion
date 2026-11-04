# SPDX-License-Identifier: GPL-3.0-only
"""ALSA sequencer: list and connect MIDI ports (like ``aconnect``).

Reads ``/proc/asound/seq/clients`` (which also says whether a port can send/receive);
if that file is missing, the output of ``aconnect -l`` (+ ``-i``/``-o`` for the direction).
Connecting/disconnecting uses ``aconnect SENDER RECEIVER`` or ``aconnect -d …`` (alsa-utils).

The app's own FM-1 connection runs over rtmidi: rtmidi creates its own clients for it
("RtMidiIn Client"/"RtMidiOut Client" or similar) and connects them to the FM-1. These
connections belong to the app and are only displayed here.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .i18n import _

PROC = Path("/proc/asound/seq/clients")
APP_CLIENT_RE = re.compile(r"rtmidi|fm-1 companion|fm1 companion", re.I)


@dataclass
class SeqPort:
    client: int
    port: int
    name: str
    client_name: str
    kernel: bool = False
    readable: bool = True          # can send (source)
    writable: bool = True          # can receive (destination)
    to: list[str] = field(default_factory=list)       # "c:p" this port sends to
    frm: list[str] = field(default_factory=list)      # "c:p" that sends to this port

    @property
    def addr(self) -> str:
        return f"{self.client}:{self.port}"

    @property
    def label(self) -> str:
        n = self.name if self.name.startswith(self.client_name) else f"{self.client_name}: {self.name}"
        return n

    @property
    def system(self) -> bool:
        return self.client == 0 or self.client_name.lower().startswith("midi through")

    @property
    def app(self) -> bool:
        return bool(APP_CLIENT_RE.search(self.client_name))


def _addrs(text: str) -> list[str]:
    return re.findall(r"(\d+:\d+)", re.sub(r"\[[^\]]*\]", "", text))


def parse_proc(text: str) -> list[SeqPort]:
    ports: list[SeqPort] = []
    cid, cname, kernel = None, "", False
    cur: SeqPort | None = None
    for line in text.splitlines():
        m = re.match(r'Client\s+(\d+)\s*:\s*"(.*)"\s*\[(.*?)\]', line)
        if m:
            cid, cname, kernel = int(m.group(1)), m.group(2), m.group(3).lower().startswith("kernel")
            cur = None
            continue
        m = re.match(r'\s+Port\s+(\d+)\s*:\s*"(.*)"\s*\(([^)]*)\)', line)
        if m and cid is not None:
            flags = m.group(3)
            cur = SeqPort(cid, int(m.group(1)), m.group(2), cname, kernel,
                          readable=flags[:1] in ("R", "r"), writable=flags[1:2] in ("W", "w"))
            ports.append(cur)
            continue
        m = re.match(r"\s+(Connecting To|Connected From):\s*(.*)$", line)
        if m and cur is not None:
            (cur.to if m.group(1) == "Connecting To" else cur.frm).extend(_addrs(m.group(2)))
    return ports


def parse_aconnect(text: str, readable: set[str] | None = None, writable: set[str] | None = None) -> list[SeqPort]:
    """``aconnect -l``; readable/writable: addresses from ``aconnect -i`` / ``-o`` (otherwise: both)."""
    ports: list[SeqPort] = []
    cid, cname, kernel = None, "", False
    cur: SeqPort | None = None
    for line in text.splitlines():
        m = re.match(r"client\s+(\d+):\s*'(.*)'\s*\[(.*?)\]", line)
        if m:
            cid, cname = int(m.group(1)), m.group(2)
            kernel = "type=kernel" in m.group(3)
            cur = None
            continue
        m = re.match(r"\s+(Connecting To|Connected From):\s*(.*)$", line)
        if m and cur is not None:
            (cur.to if m.group(1) == "Connecting To" else cur.frm).extend(_addrs(m.group(2)))
            continue
        m = re.match(r"\s+(\d+)\s+'(.*?)\s*'", line)
        if m and cid is not None:
            a = f"{cid}:{m.group(1)}"
            cur = SeqPort(cid, int(m.group(1)), m.group(2).rstrip(), cname, kernel,
                          readable=a in readable if readable is not None else True,
                          writable=a in writable if writable is not None else True)
            ports.append(cur)
    return ports


def _run(args: list[str], timeout: float = 5.0) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def aconnect_exe() -> str | None:
    return shutil.which("aconnect")


def list_ports() -> list[SeqPort]:
    """all sequencer ports (empty if there is no ALSA)."""
    try:
        return parse_proc(PROC.read_text("utf-8", "replace"))
    except OSError:
        pass
    exe = aconnect_exe()
    if not exe:
        return []
    try:
        full = _run([exe, "-l"]).stdout
        rd = {p.addr for p in parse_aconnect(_run([exe, "-i"]).stdout)}
        wr = {p.addr for p in parse_aconnect(_run([exe, "-o"]).stdout)}
    except (OSError, subprocess.SubprocessError):
        return []
    return parse_aconnect(full, rd, wr)


def connect_cmd(src: str, dst: str, disconnect: bool = False) -> list[str]:
    for a in (src, dst):
        if not re.fullmatch(r"\d+:\d+", a):
            raise ValueError(_("invalid port address: {address}").format(address=a))
    return [aconnect_exe() or "aconnect", *(["-d"] if disconnect else []), src, dst]


def connect(src: str, dst: str, disconnect: bool = False) -> tuple[bool, str]:
    if not aconnect_exe():
        return False, _("“aconnect” is missing – sudo apt install alsa-utils")
    try:
        r = _run(connect_cmd(src, dst, disconnect))
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)
    msg = (r.stderr or r.stdout).strip()
    if r.returncode and "already subscribed" in msg.lower():
        return True, _("was already connected")
    if r.returncode and disconnect and "no subscription" in msg.lower():
        return True, _("was not connected")
    return r.returncode == 0, msg


def connections(ports: list[SeqPort]) -> set[tuple[str, str]]:
    out = set()
    for p in ports:
        for d in p.to:
            out.add((p.addr, d))
        for s in p.frm:
            out.add((s, p.addr))
    return out


def find(ports: list[SeqPort], pattern: str, readable: bool | None = None) -> SeqPort | None:
    rx = re.compile(pattern, re.I)
    for p in ports:
        if (rx.search(p.client_name) or rx.search(p.name)) and not p.app and not p.system:
            if readable is None or (p.readable if readable else p.writable):
                return p
    return None


# ------------------------------------------------------------- presets ---
@dataclass
class Preset:
    """Connections by name (client numbers change when devices are plugged in)."""
    name: str
    pairs: list[tuple[str, str]]          # (sender name, receiver name) as "Client: Port"

    def to_json(self) -> dict:
        return {"name": self.name, "pairs": [list(p) for p in self.pairs]}

    @staticmethod
    def from_json(d: dict) -> "Preset":
        return Preset(d.get("name", _("Template")), [tuple(p) for p in d.get("pairs", []) if len(p) == 2])


def port_key(p: SeqPort) -> str:
    return f"{p.client_name}: {p.name}"


def capture(name: str, ports: list[SeqPort], pairs: set[tuple[str, str]] | None = None) -> Preset:
    by = {p.addr: p for p in ports}
    pairs = connections(ports) if pairs is None else pairs
    out = []
    for s, d in sorted(pairs):
        if s in by and d in by and not (by[s].app or by[d].app or by[s].system or by[d].system):
            out.append((port_key(by[s]), port_key(by[d])))
    return Preset(name, out)


def resolve(preset: Preset, ports: list[SeqPort]) -> tuple[list[tuple[str, str]], list[str]]:
    """-> (address pairs, missing ports)."""
    by = {port_key(p): p.addr for p in ports}
    pairs, missing = [], []
    for s, d in preset.pairs:
        if s in by and d in by:
            pairs.append((by[s], by[d]))
        else:
            missing += [x for x in (s, d) if x not in by]
    return pairs, sorted(set(missing))


def presets_to_json(ps: list[Preset]) -> str:
    return json.dumps([p.to_json() for p in ps], ensure_ascii=False)
