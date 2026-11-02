# SPDX-License-Identifier: GPL-3.0-only
"""Aufnahme über den USB-Audio-Eingang des FM-1.

Melodee meldet sich am Rechner als Audio-Gerät "Melodee" (SLOOP/Skiff als
"Felucca"). Je nach Einstellung am FM-1 liefert es Stereo (die Summe) oder
mehrere Spuren getrennt (Melodee: "four-track capture"). Die App nimmt auf, was
das Gerät anbietet, teilt Mehrkanal-Aufnahmen in Stereo-Paare auf und öffnet
das Ergebnis auf Wunsch direkt in Audacity – jede Spur als eigene Spur.

Aufnahme-Wege: erst PipeWire (pw-record), sonst ALSA (arecord).
"""
from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import signal
import struct
import subprocess
import sys
import time
import wave
from dataclasses import dataclass
from pathlib import Path

import os

from .devices import alsa_card_for, PortInfo, PORT_RE

try:
    import numpy as np
except ImportError:      # pragma: no cover - numpy ist Pflicht
    np = None


@dataclass
class CaptureDevice:
    card_id: str               # ALSA-Kartenname, z. B. "Melodee"
    card_index: int | None
    channels: list[int]        # mögliche Kanalzahlen
    rates: list[int]
    pw_node: str = ""          # PipeWire-Knoten (falls vorhanden)
    label: str = ""            # Anzeige („FM-1 (USB)“, „USB-Mikrofon“, „Ausgang des Pi mithören“ …)
    kind: str = "fm1"          # "fm1", "input", "monitor", "demo"
    alsa_dev: str = ""         # z. B. "plughw:CARD=Device,DEV=0" (ALSA-Weg)

    @property
    def title(self) -> str:
        return self.label or self.card_id

    @property
    def max_channels(self) -> int:
        return max(self.channels) if self.channels else 2


def _proc(path: str) -> str:
    try:
        return Path(path).read_text()
    except OSError:
        return ""


def find_capture(port: PortInfo | None = None) -> CaptureDevice | None:
    card_id = alsa_card_for(port) if port else None
    idx = port.card if port else None
    if not card_id:
        for line in _proc("/proc/asound/cards").splitlines():
            m = re.match(r"\s*(\d+)\s+\[(\S+)\s*\]", line)
            if m and PORT_RE.search(line):
                idx, card_id = int(m.group(1)), m.group(2)
                break
    if not card_id:
        return None
    if idx is None:
        for line in _proc("/proc/asound/cards").splitlines():
            m = re.match(r"\s*(\d+)\s+\[(\S+)\s*\]", line)
            if m and m.group(2) == card_id:
                idx = int(m.group(1))
    chans, rates = set(), set()
    if idx is not None:
        txt = "".join(_proc(str(p)) for p in Path(f"/proc/asound/card{idx}").glob("stream*"))
        cap = txt.split("Capture:", 1)[1] if "Capture:" in txt else ""
        for m in re.finditer(r"Channels:\s*(\d+)", cap):
            chans.add(int(m.group(1)))
        for m in re.finditer(r"Rates:\s*([\d, ]+)", cap):
            rates.update(int(x) for x in re.findall(r"\d+", m.group(1)))
    return CaptureDevice(card_id, idx, sorted(chans) or [2], sorted(rates) or [44100], _pw_node(card_id))


def _pw_node(card_id: str) -> str:
    if not shutil.which("pw-dump"):
        return ""
    try:
        data = json.loads(subprocess.run(["pw-dump"], capture_output=True, text=True, timeout=5).stdout or "[]")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return ""
    for obj in data:
        props = (obj.get("info") or {}).get("props") or {}
        if props.get("media.class") != "Audio/Source":
            continue
        hay = " ".join(str(props.get(k, "")) for k in ("node.name", "node.description", "alsa.card_name", "alsa.id",
                                                      "api.alsa.card.name", "device.description"))
        if card_id.lower() in hay.lower() or PORT_RE.search(hay):
            return props.get("node.name", "")
    return ""


class Recording:
    def __init__(self, dev: CaptureDevice, folder: Path, channels: int, rate: int = 44100, name: str = ""):
        self.dev = dev
        self.channels = channels
        self.rate = rate
        stamp = dt.datetime.now().strftime("%Y-%m-%d %H-%M-%S")
        folder.mkdir(parents=True, exist_ok=True)
        self.path = folder / f"{name or 'FM-1'} {stamp}.wav"
        self.proc: subprocess.Popen | None = None
        self.started = 0.0
        self.backend = ""
        self.error = ""

    def start(self):
        cmds = []
        if self.dev.kind == "demo":
            cmds.append(("Demo", [sys.executable, "-m", "fm1companion.recorder", "--demo-signal", str(self.path),
                                  "--rate", str(self.rate), "--channels", str(self.channels)]))
        elif self.dev.kind == "monitor" and shutil.which("pw-record"):
            cmds.append(("PipeWire", ["pw-record", "-P", "{ stream.capture.sink = true }", "--target",
                                      self.dev.pw_node, "--rate", str(self.rate), "--channels", str(self.channels),
                                      "--format", "s16", str(self.path)]))
        if self.dev.kind in ("demo", "monitor"):
            pass
        elif self.dev.pw_node and shutil.which("pw-record"):
            cmds.append(("PipeWire", ["pw-record", "--target", self.dev.pw_node, "--rate", str(self.rate),
                                      "--channels", str(self.channels), "--format", "s16", str(self.path)]))
        if shutil.which("arecord") and self.dev.kind not in ("demo", "monitor") and self.dev.card_id:
            devs = [self.dev.alsa_dev] if self.dev.alsa_dev else []
            devs += [f"hw:CARD={self.dev.card_id},DEV=0", f"plughw:CARD={self.dev.card_id},DEV=0"]
            for dev in devs:
                cmds.append(("ALSA", ["arecord", "-q", "-D", dev, "-f", "S16_LE", "-r", str(self.rate),
                                      "-c", str(self.channels), "-t", "wav", str(self.path)]))
        if not cmds and self.dev.kind == "monitor":
            raise RuntimeError("Den Ausgang mithören geht nur mit PipeWire (sudo apt install pipewire-bin)")
        if not cmds:
            raise RuntimeError("Weder pw-record noch arecord gefunden (sudo apt install pipewire-bin alsa-utils)")
        last = ""
        for backend, args in cmds:
            self.path.unlink(missing_ok=True)
            p = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            time.sleep(0.6)
            if p.poll() is None:
                self.proc, self.backend, self.started = p, backend, time.time()
                return
            last = (p.stderr.read() or "").strip()
        raise RuntimeError(f"Aufnahme startet nicht: {last or 'unbekannter Fehler'}")

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    @property
    def elapsed(self) -> float:
        return time.time() - self.started if self.started else 0.0

    def levels(self) -> list[float]:
        """Spitzenpegel je Kanal aus dem Ende der wachsenden Datei (0..1)."""
        try:
            size = self.path.stat().st_size
            frame = 2 * self.channels
            n = 4096 * frame
            if size < 44 + frame:
                return [0.0] * self.channels
            with open(self.path, "rb") as f:
                start = max(44, size - n)
                start -= (start - 44) % frame
                f.seek(start)
                raw = f.read(n)
            raw = raw[:len(raw) // frame * frame]
            vals = struct.unpack(f"<{len(raw) // 2}h", raw)
            return [max((abs(v) for v in vals[c::self.channels]), default=0) / 32768 for c in range(self.channels)]
        except (OSError, struct.error):
            return [0.0] * self.channels

    def stop(self) -> list[Path]:
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGINT)
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.terminate()
                self.proc.wait(3)
        self.proc = None
        _fix_header(self.path)
        if self.channels > 2:
            return split_pairs(self.path)
        return [self.path]


def _fix_header(path: Path):
    """falls die Aufnahme hart beendet wurde: Längen im WAV-Kopf korrigieren."""
    try:
        with open(path, "r+b") as f:
            d = f.read(4096)
            if d[:4] != b"RIFF":
                return
            size = path.stat().st_size
            i = d.find(b"data")
            if i < 0:
                return
            f.seek(4)
            f.write(struct.pack("<I", min(0xFFFFFFFF, size - 8)))          # > 4 GiB: struct.error sonst
            f.seek(i + 4)
            f.write(struct.pack("<I", min(0xFFFFFFFF, size - i - 8)))
    except OSError:
        pass


def split_pairs(path: Path, labels: list[str] | None = None) -> list[Path]:
    """Mehrkanal-WAV -> Stereo-Dateien je Kanalpaar (Spur 1, Spur 2 …). Stückweise, damit auch eine
    halbstündige Vierspur-Aufnahme nicht den ganzen Speicher braucht."""
    with wave.open(str(path)) as w:
        nch, sw, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        if nch <= 2:
            return [path]
        pairs = nch // 2
        outs = []
        for k in range(pairs):
            label = (labels[k] if labels and k < len(labels) else f"Spur {k + 1}")
            dst = path.with_name(f"{path.stem} – {label}.wav")
            o = wave.open(str(dst), "wb")
            o.setnchannels(2)
            o.setsampwidth(sw)
            o.setframerate(sr)
            outs.append((dst, o))
        try:
            step = 1 << 18
            done = 0
            while done < n:
                raw = w.readframes(min(step, n - done))
                if not raw:
                    break
                got = len(raw) // (nch * sw)
                done += got
                a = np.frombuffer(raw[:got * nch * sw], dtype=np.uint8).reshape(got, nch, sw)
                for k, (_, o) in enumerate(outs):
                    o.writeframes(a[:, 2 * k:2 * k + 2, :].tobytes())
        finally:
            for _, o in outs:
                o.close()
    return [d for d, _ in outs]


def open_in_audacity(files: list[Path], cmd: str = "audacity") -> bool:
    exe = shutil.which(cmd) or shutil.which("audacity")
    if exe:
        subprocess.Popen([exe, *map(str, files)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        return True
    if shutil.which("flatpak"):
        r = subprocess.run(["flatpak", "info", "org.audacityteam.Audacity"], capture_output=True)
        if r.returncode == 0:
            subprocess.Popen(["flatpak", "run", "--file-forwarding", "org.audacityteam.Audacity", "@@",
                              *map(str, files), "@@"], start_new_session=True)
            return True
    return False


# =================================================================== Suite 2.0
# Alle Aufnahme-Quellen, Mitlesen der wachsenden Datei, Nachbearbeitung, Takt-Sync,
# Export. (Alles zusätzlich – das Bisherige bleibt, wie es ist.)

DEMO = os.environ.get("FM1C_DEMO") == "1"


def _alsa_cards() -> list[tuple[int, str, str]]:
    out = []
    for line in _proc("/proc/asound/cards").splitlines():
        m = re.match(r"\s*(\d+)\s+\[(\S+)\s*\]:\s*(.*)", line)
        if m:
            out.append((int(m.group(1)), m.group(2), m.group(3).split(" - ")[-1].strip()))
    return out


def _capture_caps(idx: int) -> tuple[list[int], list[int]]:
    txt = "".join(_proc(str(p)) for p in Path(f"/proc/asound/card{idx}").glob("stream*"))
    cap = txt.split("Capture:", 1)[1] if "Capture:" in txt else ""
    ch = sorted({int(m.group(1)) for m in re.finditer(r"Channels:\s*(\d+)", cap)})
    rates = set()
    for m in re.finditer(r"Rates:\s*([\d, ]+)", cap):
        rates.update(int(x) for x in re.findall(r"\d+", m.group(1)))
    return ch, sorted(rates)


def _has_alsa_capture(idx: int) -> bool:
    return any(Path(f"/proc/asound/card{idx}").glob("pcm*c"))


def _pw_nodes() -> list[dict]:
    if not shutil.which("pw-dump"):
        return []
    try:
        data = json.loads(subprocess.run(["pw-dump"], capture_output=True, text=True, timeout=5).stdout or "[]")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []
    return [((o.get("info") or {}).get("props") or {}) for o in data if o.get("type", "").endswith("Node")]


def list_sources(port: PortInfo | None = None) -> list[CaptureDevice]:
    """alle Aufnahme-Quellen: FM-1 (USB-Audio), andere Eingänge (Line-in, USB-Mikrofon),
    der Ausgang des Rechners zum Mithören (PipeWire) und im Demo-Modus ein Testsignal."""
    out: list[CaptureDevice] = []
    fm1 = find_capture(port)
    if fm1:
        fm1.label = f"FM-1 über USB ({fm1.card_id})"
        out.append(fm1)
    nodes = _pw_nodes()
    seen_cards = {fm1.card_id.lower()} if fm1 else set()
    for props in nodes:
        mc = props.get("media.class", "")
        name = props.get("node.name", "")
        desc = props.get("node.description") or props.get("node.nick") or name
        if not name or (fm1 and fm1.pw_node == name):
            continue
        if mc == "Audio/Source":
            out.append(CaptureDevice(props.get("alsa.card_name", "") or name, None, [1, 2], [44100, 48000],
                                     name, desc, "input"))
            seen_cards.add(str(props.get("alsa.id", props.get("api.alsa.card.name", ""))).lower())
        elif mc == "Audio/Sink":
            out.append(CaptureDevice("", None, [2], [44100, 48000], name, f"Ausgang mithören: {desc}", "monitor"))
    if not nodes:                                 # ohne PipeWire: ALSA-Karten mit Aufnahme
        for idx, cid, longname in _alsa_cards():
            if cid.lower() in seen_cards or not _has_alsa_capture(idx):
                continue
            ch, rates = _capture_caps(idx)
            out.append(CaptureDevice(cid, idx, ch or [1, 2], rates or [44100, 48000], "", longname or cid, "input",
                                     f"plughw:CARD={cid},DEV=0"))
    if DEMO:
        out.append(CaptureDevice("demo", None, [1, 2, 8], [44100, 48000], "",
                                 "Demo-Signal (Drum-Loop, nur im Demo-Modus)", "demo"))
    return out


class TailReader:
    """liest neue Frames aus einer wachsenden 16-bit-WAV-Datei (pw-record/arecord schreiben laufend)."""

    def __init__(self, path: Path, channels: int):
        self.path = Path(path)
        self.channels = channels
        self.pos = 0                 # Byte-Position im Datenteil
        self.data_off = 0
        self.frames = 0              # bisher gelesene Frames

    def _find_data(self) -> int:
        try:
            with open(self.path, "rb") as f:
                head = f.read(4096)
        except OSError:
            return 0
        i = head.find(b"data")
        return i + 8 if i >= 0 and head[:4] == b"RIFF" else 0

    def read(self, max_frames: int = 1 << 22):
        """-> neue Frames als float-Array (n, Kanäle) in [-1, 1)."""
        if not self.data_off:
            self.data_off = self._find_data()
            if not self.data_off:
                return np.zeros((0, self.channels))
        frame = 2 * self.channels
        try:
            size = self.path.stat().st_size
            avail = (size - self.data_off - self.pos) // frame
            if avail <= 0:
                return np.zeros((0, self.channels))
            n = min(avail, max_frames)
            with open(self.path, "rb") as f:
                f.seek(self.data_off + self.pos)
                raw = f.read(n * frame)
        except OSError:
            return np.zeros((0, self.channels))
        n = len(raw) // frame
        self.pos += n * frame
        self.frames += n
        return np.frombuffer(raw[:n * frame], dtype="<i2").reshape(-1, self.channels) / 32768.0


def read_wav_array(path: Path):
    """-> (rate, float-Array (n, Kanäle)); 16/24/32 bit PCM oder float."""
    from .vendor import sampleio as sio
    d = Path(path).read_bytes()
    ch = sio.wav_chunks(d)
    tag, nch, sr, bits = sio.wav_format(d, ch)
    o, n = ch.get(b"data", (0, 0))
    bps = bits // 8
    nfr = min(n, len(d) - o) // (bps * nch)
    raw = d[o:o + nfr * bps * nch]
    if tag == 3:
        x = np.frombuffer(raw, dtype="<f4" if bps == 4 else "<f8").astype(np.float64)
    elif bps == 2:
        x = np.frombuffer(raw, dtype="<i2") / 32768.0
    elif bps == 3:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        x = ((b[:, 0] << 8 | b[:, 1] << 16 | b[:, 2] << 24) / 2147483648.0)
    elif bps == 4:
        x = np.frombuffer(raw, dtype="<i4") / 2147483648.0
    else:
        x = (np.frombuffer(raw, dtype=np.uint8).astype(np.float64) - 128) / 128.0   # uint8 - 128 liefe über
    return sr, x.reshape(-1, nch)


def write_wav_array(path: Path, x, sr: int):
    a = np.asarray(x, dtype=np.float64)
    if a.ndim == 1:
        a = a[:, None]
    pcm = (np.clip(a, -1, 1) * 32767).round().astype("<i2")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(a.shape[1])
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return Path(path)


def trim_silence(x, sr: int, threshold_db: float = -48.0, keep_ms: float = 8.0):
    """Stille vorn/hinten weg (Schwelle relativ zum Spitzenpegel), mit etwas Luft vor dem Anschlag."""
    a = np.asarray(x)
    mono = np.abs(a).max(axis=1) if a.ndim == 2 else np.abs(a)
    pk = mono.max(initial=0)
    if pk <= 0:
        return a[:0]
    thr = pk * 10 ** (threshold_db / 20)
    idx = np.nonzero(mono > thr)[0]
    keep = int(sr * keep_ms / 1000)
    s = max(0, idx[0] - keep)
    e = min(len(a), idx[-1] + 1 + keep * 4)
    return a[s:e]


def normalize(x, peak: float = 0.97):
    a = np.asarray(x, dtype=np.float64)
    pk = np.abs(a).max(initial=0)
    return a * (peak / pk) if pk > 0 else a


def fade(x, sr: int, in_ms: float = 0.0, out_ms: float = 0.0):
    a = np.array(x, dtype=np.float64)
    n = len(a)
    fi, fo = min(n, int(sr * in_ms / 1000)), min(n, int(sr * out_ms / 1000))
    shape = (-1, 1) if a.ndim == 2 else (-1,)
    if fi:
        a[:fi] *= np.linspace(0, 1, fi).reshape(shape)
    if fo:
        a[n - fo:] *= np.linspace(1, 0, fo).reshape(shape)
    return a


def process_take(x, sr: int, trim: bool = True, norm: bool = True, fade_ms: float = 4.0):
    y = trim_silence(x, sr) if trim else np.asarray(x)
    if len(y) and norm:
        y = normalize(y)
    if len(y):
        y = fade(y, sr, 1.0 if trim else 0.0, fade_ms)
    return y


def split_tracks(path: Path, mode: str = "pairs", labels: list[str] | None = None) -> list[Path]:
    """Mehrkanal-Aufnahme aufteilen: "pairs" = Stereo-Paare (wie split_pairs), "mono" = jeder Kanal einzeln."""
    if mode == "pairs":
        return split_pairs(path, labels)
    sr, a = read_wav_array(path)
    if a.shape[1] <= 1:
        return [path]
    out = []
    for c in range(a.shape[1]):
        label = labels[c] if labels and c < len(labels) else f"Kanal {c + 1}"
        out.append(write_wav_array(path.with_name(f"{path.stem} – {label}.wav"), a[:, c], sr))
    return out


EXPORT_FORMATS = {"wav": ("WAV (16 bit)", []), "flac": ("FLAC (verlustfrei)", ["-c:a", "flac"]),
                  "mp3": ("MP3 (320 kbit/s)", ["-c:a", "libmp3lame", "-b:a", "320k"])}


def export_audio(src: Path, fmt: str, dst: Path | None = None) -> Path:
    """WAV -> WAV/FLAC/MP3 über ffmpeg (WAV ohne ffmpeg: einfache Kopie)."""
    dst = Path(dst) if dst else Path(src).with_suffix("." + fmt)
    if fmt == "wav":
        if Path(src) != dst:
            shutil.copyfile(src, dst)
        return dst
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("Für FLAC/MP3 wird ffmpeg gebraucht (sudo apt install ffmpeg)")
    r = subprocess.run([exe, "-v", "error", "-y", "-i", str(src), *EXPORT_FORMATS[fmt][1], str(dst)],
                       capture_output=True, text=True, timeout=600)
    if r.returncode:
        raise RuntimeError(f"ffmpeg: {r.stderr.strip()[:200]}")
    return dst


def cut_wav(src: Path, dst: Path, start: float, end: float, norm: bool = False, fade_in_ms: float = 0.0,
            fade_out_ms: float = 0.0) -> Path:
    """Ausschnitt (Sekunden) mit optionalem Normalisieren/Ein-/Ausblenden als neue Datei."""
    sr, a = read_wav_array(src)
    y = a[int(max(0, start) * sr):int(end * sr) if end > 0 else len(a)]
    if norm:
        y = normalize(y)
    y = fade(y, sr, fade_in_ms, fade_out_ms)
    return write_wav_array(dst, y, sr)


# ------------------------------------------------------------ Takt-Sync ---

class BarSync:
    """Startet/stoppt eine Aufnahme taktgenau anhand der MIDI-Clock des FM-1.

    feed(status, t) mit jedem Echtzeit-Byte (F8 Clock, FA Start, FB Continue, FC Stop,
    F2 Song Position) und seiner Ankunftszeit. Nach arm(bars) liefert sie start_time
    (erster Clock-Tick nach Start bzw. am nächsten Taktanfang) und end_time (nach
    `bars` Takten). 24 Ticks je Viertel, 4/4.
    """
    PPQN = 24

    def __init__(self, beats_per_bar: int = 4):
        self.beats = beats_per_bar
        self.running = False
        self.tick = -1                  # Ticks seit Songanfang (0 = erster Tick nach Start)
        self.pending_first = False
        self.times: list[float] = []    # letzte Tick-Zeiten (Tempo)
        self.disarm()

    @property
    def ticks_per_bar(self) -> int:
        return self.PPQN * self.beats

    def disarm(self):
        self.armed = False
        self.mode = "start"
        self.bars = 0
        self.start_tick = None
        self.start_time = None
        self.end_time = None
        self.stopped_early = False

    def arm(self, bars: int = 4, mode: str = "start"):
        """mode "start": auf das nächste MIDI-Start warten; "bar": nächster Taktanfang (läuft schon)."""
        self.disarm()
        self.armed = True
        self.bars = max(0, int(bars))       # 0 = bis Stopp
        self.mode = "bar" if mode == "bar" and self.running and self.tick >= 0 else "start"

    @property
    def bpm(self) -> float:
        if len(self.times) < 7:
            return 0.0
        d = np.diff(self.times[-25:])
        med = float(np.median(d))
        return 60.0 / (med * self.PPQN) if med > 0 else 0.0

    @property
    def state(self) -> str:
        if not self.armed:
            return "aus"
        if self.start_time is None:
            return "wartet"
        if self.end_time is None:
            return "nimmt auf"
        return "fertig"

    @property
    def position(self) -> tuple[int, int]:
        """(Takt, Schlag) ab 1 – für die Anzeige."""
        if self.tick < 0:
            return 0, 0
        return self.tick // self.ticks_per_bar + 1, self.tick % self.ticks_per_bar // self.PPQN + 1

    def _tick_event(self, t: float):
        if self.armed and self.start_time is None:
            if self.mode == "start" and self.tick == 0 and self.pending_first:
                self.start_tick, self.start_time = 0, t
            elif self.mode == "bar" and self.tick % self.ticks_per_bar == 0:
                self.start_tick, self.start_time = self.tick, t
        elif self.armed and self.end_time is None and self.bars:
            if self.tick - self.start_tick >= self.bars * self.ticks_per_bar:
                self.end_time = t

    def feed(self, status: int, t: float):
        if status == 0xFA:                       # Start
            self.running = True
            self.tick = -1
            self.pending_first = True
            self.times.clear()
        elif status == 0xFB:                     # Continue
            self.running = True
            self.pending_first = False
        elif status == 0xFC:                     # Stop
            self.running = False
            if self.armed and self.start_time is not None and self.end_time is None:
                self.end_time = t
                self.stopped_early = bool(self.bars)
        elif status == 0xF8 and self.running:
            self.tick += 1
            self.times.append(t)
            if len(self.times) > 200:
                del self.times[:100]
            self._tick_event(t)
            self.pending_first = False

    def feed_spp(self, beats16: int):
        """Song Position Pointer (in Sechzehnteln) setzt die Position."""
        self.tick = beats16 * 6 - 1
        self.pending_first = False


def take_window(start_time: float, end_time: float, audio_t0: float, rate: int, latency_ms: float = 0.0):
    """Wall-Clock-Zeiten -> Frame-Bereich in der Aufnahme. audio_t0 = Zeit des ersten Frames,
    latency_ms verschiebt (positive Werte: Audio kommt später an als die MIDI-Clock)."""
    lat = latency_ms / 1000
    a = int(round((start_time - audio_t0 + lat) * rate))
    b = int(round((end_time - audio_t0 + lat) * rate))
    return max(0, a), max(0, b)


class StartEstimator:
    """schätzt, wann der erste Frame einer laufenden Aufnahme aufgenommen wurde:
    t0 <= Beobachtungszeit - Frames/Rate; die engste Schranke über viele Beobachtungen gewinnt."""

    def __init__(self, rate: int):
        self.rate = rate
        self.t0 = None

    def observe(self, t: float, frames: int):
        est = t - frames / self.rate
        if self.t0 is None or est < self.t0:
            self.t0 = est


# ------------------------------------------------------------ Demo-Signal ---

def _demo_block(start: int, n: int, rate: int, ch: int, bpm: float = 120.0):
    t = (start + np.arange(n)) / rate
    beat = 60.0 / bpm
    pos = t % beat
    eighth = t % (beat / 2)
    kick = 0.8 * np.sin(2 * np.pi * (50 + 90 * np.exp(-pos * 40)) * pos) * np.exp(-pos * 9) * \
        ((t // beat) % 2 == 0)
    snare_pos = (t - beat) % (2 * beat)
    rng = np.random.default_rng(start)
    noise = rng.uniform(-1, 1, n)
    snare = 0.45 * noise * np.exp(-snare_pos * 22) * (snare_pos < 0.3)
    hat = 0.12 * noise * np.exp(-eighth * 90)
    bass = 0.25 * np.sin(2 * np.pi * 55 * t) * np.exp(-pos * 3)
    tracks = [kick + snare, hat, bass, 0.3 * np.sin(2 * np.pi * 440 * t) * np.exp(-pos * 6)]
    out = np.zeros((n, ch))
    for c in range(ch):
        out[:, c] = (kick + snare + hat + bass) * 0.8 if ch <= 2 else tracks[(c // 2) % 4]
    return out


def demo_signal(path: Path, rate: int = 44100, channels: int = 2, seconds: float = 0.0):
    """schreibt in Echtzeit einen Drum-Loop in eine wachsende WAV-Datei (wie pw-record)."""
    import signal as _sig
    stop = {"now": False}
    _sig.signal(_sig.SIGINT, lambda *_: stop.update(now=True))
    _sig.signal(_sig.SIGTERM, lambda *_: stop.update(now=True))
    with open(path, "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", 0xFFFFFFFF) + b"WAVEfmt " + struct.pack(
            "<IHHIIHH", 16, 1, channels, rate, rate * channels * 2, channels * 2, 16) + b"data" +
            struct.pack("<I", 0xFFFFFFFF))
        f.flush()
        t0 = time.time()
        written = 0
        while not stop["now"]:
            due = int((time.time() - t0) * rate)
            if due > written:
                blk = _demo_block(written, due - written, rate, channels)
                f.write((np.clip(blk, -1, 1) * 32767).astype("<i2").tobytes())
                f.flush()
                written = due
            if seconds and written >= seconds * rate:
                break
            time.sleep(0.02)
    _fix_header(path)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo-signal")
    ap.add_argument("--rate", type=int, default=44100)
    ap.add_argument("--channels", type=int, default=2)
    ap.add_argument("--seconds", type=float, default=0.0)
    a = ap.parse_args()
    if a.demo_signal:
        demo_signal(Path(a.demo_signal), a.rate, a.channels, a.seconds)
