# SPDX-License-Identifier: GPL-3.0-only
"""Studio: der digitale Zwilling des FM-1 in der Suite.

Das FM-1 ist das Instrument, die Suite das Studio. Der Zwilling ist **dieselbe Firmware** wie auf dem Gerät
(Melodees eigener C-Code, für den Rechner als WebAssembly übersetzt, siehe tools/build_sims.py --target
melodee-studio und tools/studio/melodee_studio.c): dieselben Engines, Effekte, derselbe Mixer und Sequencer, dazu
der NOR-Flash (Einstellungen, Projekte, User-Presets) und das Editor-SysEx-Protokoll über den USB-Pfad der Firmware.
Er läuft in QtWebEngine (Web Worker; Ton über AudioWorklet -> Chromium -> PipeWire = die Lautsprecher des Pi) und
spricht nach außen genau wie ein echtes FM-1: ``Studio.link`` ist ein ``transport.Link`` – backup.read/restore,
Presets, Projekt-Schreiben, playback.py usw. funktionieren unverändert.

Schichten (mit wenig Aufwand für weitere Firmwares/Emulatoren offen gehalten):

* ``TwinBuild``   – ein mitgelieferter Zwilling: ``sims/<firmware>-studio/<version>/`` mit manifest.json
                    (mehrere Versionen nebeneinander; ``builds()``, ``pick_build()`` wählt passend oder die nächste)
* ``TwinBackend`` – wo der Zwilling läuft. ``WebEngineBackend`` (Laufzeit: QtWebEngine), ``NodeBackend`` (nur
                    Tests/Entwicklung, braucht node). Ein späteres Backend (z. B. fm1-emu: CPU-Emulation echter
                    .fwsc-Pakete) muss nur dieselben Methoden liefern.
* ``StudioTransport`` – ``transport.Transport`` über ein Backend (für ``Link``)
* ``Studio``      – die Fassade für Oberfläche und Werkzeuge

API (``Studio``)::

    st = Studio.create(version="v1.6.0-rc.8")           # oder Studio.for_version(device_version)
    st.open_from_backup(path | bytes | dict | Archive)  # komplettes Backup über das Editor-Protokoll einspielen
    st.open_from_device(link)                           # Backup vom echten FM-1 lesen, dann einspielen
    st.link                                             # Link zum Zwilling (wie zu einem echten FM-1)
    st.play(how="play"|"song") / st.stop()              # Transport (PLAY-Taste bzw. Song-Kette, Editor-Stopp)
    st.load_project(slot)                               # Projekt 1..32 (slot 0..31) laden wie die PROJECT-Seite
    st.start_audio() / st.stop_audio()                  # Echtzeit-Ton (WebEngine: Standard-Ausgang)
    st.render_wav(path, seconds=None, until_end=False, stems=False, start="play", source="live", fmt="f32")
                                                        # offline, schneller als Echtzeit -> RenderResult
    st.save_state(path=None, profile=None) / st.load_state(path=None, profile=None)
                                                        # der ganze Flash (= alles, was das Gerät behält)
    st.backup() -> Archive                              # Backup DES ZWILLINGS (z. B. zurück aufs FM-1)
    st.write_to_device(link)                            # dieses Backup auf das echte FM-1 spielen
    st.version_info() / st.check_versions(device=..., backup=...) -> VersionCheck (Warnung auf Deutsch)
    st.set_clock(run)                                   # Uhr des Zwillings anhalten/laufen lassen (Worker 'clock')
    st.flash_sum() / st.set_flash(bytes)                # Änderungen erkennen, Zwischenstand zurückholen
    st.play_midi(data)                                  # Kanal-Nachricht ohne Warten (MIDI-Keyboard)
    open_from_backup(..., tolerant=True)                # abgelehnte Teile überspringen -> st.rejected

Oberfläche: ui/studio_service.py (Dienst der App, virtuelles Gerät) und ui/pages/studio.py (Seite „Studio“).

Grenzen: siehe NOTICE des Builds und ``LIMITS`` unten.
"""
from __future__ import annotations

import base64
import contextlib
import datetime as dt
import io
import json
import queue
import re
import shutil
import struct
import subprocess
import threading
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .i18n import _
from .transport import Transport, Link, LinkError
from .protocol import Cmd, parse_info, v14

SIMS_DIR = Path(__file__).resolve().parent / "sims"
HOST_DIR = SIMS_DIR / "_studio"
STATE_NAME = "Studio-Stand.fm1studio"
STUDIO_ABI = 1
FS = 44100

# panel.c: die Tasten nach Beschriftung (PANEL_DEFAULT; ein angelerntes Panel löst der Zwilling selbst auf)
B = {"FX": 0, "SCL": 1, "ENV": 2, "LFO": 3, "EDIT": 4, "GLO": 5, "HOME": 6, "SAVE": 7, "ARP": 8, "SEQ": 9,
     "PLAY": 10, "REC": 11}

LIMITS = _(
    "The twin computes without a CPU limit: under overload the device drops voices (overload shedding), the "
    "twin never does. Floating-point parts (FM6 voice pairs, which CPU1 computes on the device, effect filters) "
    "may differ in the last bits (inaudible). No BLE, no USB host, no firmware update in the twin. The resource "
    "arena has the size of the host tests (at least that of the device). Stems come from several runs with muted "
    "tracks (track solo, with its share of the shared effects); because of the master dynamics the sum of the "
    "stems does not exactly match the mix.")


class StudioError(Exception):
    pass


# ======================================================================= Versionen und Builds
_VER = re.compile(r"v?(\d+)\.(\d+)(?:\.(\d+))?(?:[- ]?(rc|beta|alpha)\.?(\d+))?", re.I)


def norm_version(s: str) -> str:
    """'MELODEE v1.6.0-rc.7' / '1.6.0-rc.7' / 'Melodee 1.6.0 rc7' -> 'v1.6.0-rc.7' ('' wenn keine Version)."""
    m = _VER.search(s or "")
    if not m:
        return ""
    a, b, c, pre, n = m.groups()
    return f"v{a}.{b}.{c or 0}" + (f"-{pre.lower()}.{n}" if pre else "")


def version_key(s: str) -> tuple:
    """sortierbar: Vorabversionen (rc) vor der fertigen Version."""
    m = _VER.search(s or "")
    if not m:
        return (0, 0, 0, 0, 0)
    a, b, c, pre, n = m.groups()
    rank = {"alpha": 0, "beta": 1, "rc": 2}.get((pre or "").lower(), 3)
    return (int(a), int(b), int(c or 0), rank, int(n or 0))


@dataclass
class TwinBuild:
    firmware: str                 # "melodee"
    version: str                  # "v1.6.0-rc.8" (wie die Firmware sie meldet)
    path: Path                    # Ordner mit manifest.json und dem WASM
    manifest: dict = field(default_factory=dict)

    @property
    def wasm(self) -> Path:
        return self.path / self.manifest.get("wasm", "twin.wasm")

    @property
    def identity(self) -> str:
        return self.manifest.get("identity", "")

    @property
    def url_path(self) -> str:    # unter dem lokalen Server (editor_host.LocalServer: /sims/...)
        return f"/sims/{self.path.parent.name}/{self.path.name}/{self.wasm.name}"

    def verify(self) -> list[str]:
        import hashlib
        bad = []
        for name, info in self.manifest.get("files", {}).items():
            p = self.path / name
            if not p.exists():
                bad.append(_("{name} missing").format(name=name))
            elif p.stat().st_size != info.get("size") or hashlib.sha256(p.read_bytes()).hexdigest() != info.get("sha256"):
                bad.append(_("{name} damaged").format(name=name))
        return bad


def builds(firmware: str = "melodee") -> list[TwinBuild]:
    """alle mitgelieferten Zwillinge einer Firmware, älteste zuerst."""
    out = []
    d = SIMS_DIR / f"{firmware}-studio"
    for sub in sorted(d.iterdir()) if d.is_dir() else []:
        mf = sub / "manifest.json"
        try:
            m = json.loads(mf.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        if m.get("abi") != STUDIO_ABI or m.get("kind") != "studio":
            continue
        out.append(TwinBuild(m.get("firmware", firmware), m.get("firmware_version") or norm_version(sub.name), sub, m))
    return sorted(out, key=lambda b: version_key(b.version))


def pick_build(firmware: str = "melodee", wanted: str = "") -> tuple[TwinBuild, bool, str]:
    """-> (Build, genau?, Warnung). Ohne Wunsch: der neueste. Sonst die gleiche Version, sonst die nächste
    (bevorzugt die nächstältere: deren Daten liest eine neuere Firmware sicher, umgekehrt nicht immer)."""
    bs = builds(firmware)
    if not bs:
        raise StudioError(_("No studio twin for {firmware} included – installation incomplete?").format(firmware=firmware))
    want = norm_version(wanted)
    if not want:
        return bs[-1], True, ""
    for b in bs:
        if b.version == want:
            return b, True, ""
    wk = version_key(want)
    older = [b for b in bs if version_key(b.version) < wk]
    best = older[-1] if older else bs[0]
    return best, False, _("There is no studio twin for {firmware} {version}; the studio runs with {twin} – sound "
                          "and functions may differ.").format(firmware=firmware.capitalize(), version=want,
                                                              twin=best.version)


@dataclass
class VersionCheck:
    twin: str
    device: str = ""
    backup: str = ""
    ok: bool = True
    message: str = ""


def check_versions(twin: str, device: str = "", backup: str = "", firmware: str = "Melodee") -> VersionCheck:
    """Vergleicht die Firmware des Zwillings mit der des Geräts (INFO) und der des Backups -> Warnung."""
    t, d, b = norm_version(twin), norm_version(device), norm_version(backup)
    msgs = []
    if d and d != t:
        msgs.append(_("The studio runs with {firmware} {twin}, your FM-1 has {device} – sound may differ.").format(
            firmware=firmware, twin=t, device=d))
    if b and b != t:
        msgs.append(_("The backup comes from {firmware} {backup}, the studio runs with {twin} – sound may differ.").format(
            firmware=firmware, backup=b, twin=t))
    if firmware.lower() == "melodee" and any(levels_changed(t, x) for x in (d, b) if x and x != t):
        msgs.append(LEVELS_1_6_0)
    return VersionCheck(t, d, b, not msgs, " ".join(msgs))


# Melodee 1.6.0 (docs/RELEASE_1.6.0.md „Sound and levels“): der Mix klingt anders als bis 1.6.0-rc.8
LEVELS_1_6_0 = _("From Melodee 1.6.0 on the mix differs from the pre-releases: CZ-1 6 dB quieter, limiter only "
                  "at −2 dBFS, dither – levels differ.")
_V160 = (1, 6, 0, 3, 0)


def levels_changed(a: str, b: str) -> bool:
    """liegt die Pegeländerung von Melodee 1.6.0 zwischen den beiden Versionen?"""
    ka, kb = version_key(a), version_key(b)
    return ka != (0, 0, 0, 0, 0) and kb != (0, 0, 0, 0, 0) and (ka < _V160) != (kb < _V160)


def device_version(link) -> str:
    """Firmware-Version des angeschlossenen Geräts (Editor INFO, z. B. 'MELODEE v1.6.0-rc.7' -> 'v1.6.0-rc.7')."""
    return norm_version(parse_info(link.request(Cmd.INFO, [], timeout=1.5, retries=1)).version)


def backup_version(archive) -> str:
    """Firmware-Version, mit der ein Backup gemacht wurde (Begleitblock der App, sonst das Feld 'firmware')."""
    c = archive.companion or {}
    return norm_version(c.get("firmware_version") or c.get("version_string") or archive.firmware or "")


# ======================================================================= Backends
class TwinBackend:
    """Wo ein Zwilling läuft. Alle Methoden sind aus jedem Thread aufrufbar und blockieren bis zum Ergebnis.

    Pflicht für ein Backend (auch ein künftiges fm1-emu-Backend):
      start(flash|None) -> info dict {version, fs, flash_size, ...}   (Einschalten, mit diesem Flash)
      call(op, **args) -> dict     Befehle von twin-host.js: midi(data), press(label), transport_start(how),
                                   project_load(slot), project_used, transport, info, frame, run(ms), reset
      get_flash() -> bytes / set_flash(bytes)    (Einschalten mit neuem Flash)
      render(path, params) -> dict              (offline, in einer eigenen Instanz; schreibt PCM nach path)
      set_audio(on)                              (Echtzeit-Ton)
      on_midi: Callable[[list[int]], None]       (alles, was das Gerät über USB-MIDI sendet)
      close()
    """
    build: TwinBuild
    on_midi: Callable[[list[int]], None] | None = None

    def start(self, flash: bytes | None = None) -> dict:
        raise NotImplementedError

    def call(self, op: str, **args) -> dict:
        raise NotImplementedError

    def get_flash(self) -> bytes:
        return self.call("flash_get")["flash"]

    def set_flash(self, flash: bytes) -> dict:
        return self.call("flash_set", flash=flash)

    def render(self, path: Path, params: dict) -> dict:
        raise NotImplementedError

    def set_audio(self, on: bool) -> None:
        raise NotImplementedError

    def set_clock(self, run: bool) -> None:
        """Uhr des Zwillings (Worker-Befehl 'clock'): False hält die Gerätezeit an, solange kein Ton sie antreibt
        (spart Rechenzeit, wenn das Studio nicht sichtbar ist und nichts spielt)."""
        self.call("clock", run=bool(run))

    def post(self, op: str, **args) -> None:
        """Befehl ohne auf die Antwort zu warten (Noten vom MIDI-Keyboard). Standard: wie call."""
        self.call(op, **args)

    def close(self) -> None:
        pass

    def _midi_out(self, msgs):
        fn = self.on_midi
        if fn:
            for m in msgs:
                try:
                    fn([int(x) & 0xFF for x in m])
                except Exception:                    # noqa: BLE001 – nie den Lese-Thread abschießen
                    pass


def _b64(b: bytes) -> dict:
    return {"b64": base64.b64encode(bytes(b)).decode()}


def _unb64(v):
    return base64.b64decode(v["b64"]) if isinstance(v, dict) and "b64" in v else v


class NodeBackend(TwinBackend):
    """Zwilling in node (tests/studio_node_host.mjs) – für Tests und Entwicklung ohne QtWebEngine. Nicht zur
    Laufzeit der App (dort gibt es kein node)."""

    def __init__(self, build: TwinBuild, node: str | None = None, script: Path | None = None):
        self.build = build
        self.node = node or shutil.which("node") or "/opt/node22/bin/node"
        self.script = script or Path(__file__).resolve().parents[1] / "tests" / "studio_node_host.mjs"
        self.p = None
        self._id = 0
        self._lock = threading.Lock()
        self._wait: dict[int, queue.Queue] = {}
        self._ready = threading.Event()

    def start(self, flash: bytes | None = None) -> dict:
        self.p = subprocess.Popen([self.node, str(self.script)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  text=True, bufsize=1)
        threading.Thread(target=self._reader, daemon=True).start()
        if not self._ready.wait(20):
            raise StudioError(_("node twin does not start"))
        args = {"wasm_path": str(self.build.wasm)}
        if flash:
            args["flash"] = _b64(flash)
        return self.call("boot", **args)

    def _reader(self):
        for line in self.p.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("ev") == "ready":
                self._ready.set()
            elif msg.get("ev") == "midi":
                self._midi_out(msg["data"])
            elif "id" in msg:
                q = self._wait.get(msg["id"])
                if q:
                    q.put(msg)

    def call(self, op: str, timeout: float = 60, **args) -> dict:
        if not self.p or self.p.poll() is not None:
            raise StudioError(_("Twin is not running"))
        for k in ("flash", "memory"):
            if isinstance(args.get(k), (bytes, bytearray)):
                args[k] = _b64(args[k])
        with self._lock:
            self._id += 1
            i = self._id
            q: queue.Queue = queue.Queue()
            self._wait[i] = q
            self.p.stdin.write(json.dumps({"id": i, "op": op, **args}) + "\n")
            self.p.stdin.flush()
        try:
            r = q.get(timeout=timeout)
        except queue.Empty:
            raise StudioError(_("Twin does not respond ({operation})").format(operation=op))
        finally:
            self._wait.pop(i, None)
        if not r.get("ok"):
            raise StudioError(r.get("error", _("Error in the twin")))
        return {k: _unb64(v) for k, v in r.items()}

    def render(self, path: Path, params: dict) -> dict:
        return self.call("render", timeout=3600, path=str(path), **params)

    def set_audio(self, on: bool) -> None:
        if on:
            raise StudioError(_("node twin has no audio output"))

    def close(self):
        if self.p:
            with contextlib.suppress(Exception):
                self.p.stdin.close()
                self.p.terminate()
                self.p.wait(3)
            self.p = None


# ======================================================================= Transport für Link
class StudioTransport(Transport):
    """``transport.Transport`` über einen Zwilling: send() gibt die Nachricht(en) über den USB-MIDI-Pfad der
    Firmware hinein; alles, was der Zwilling sendet, geht an ``receiver`` – wie ein echtes FM-1 am USB."""

    def __init__(self, backend: TwinBackend):
        self.backend = backend
        self._closed = False
        # mehrere Leitungen an einem Zwilling (Studio selbst und das virtuelle Gerät der Suite): jede bekommt alles
        sinks = getattr(backend, "_sinks", None)
        if sinks is None:
            sinks = backend._sinks = []
            backend.on_midi = lambda d, s=sinks: [fn(d) for fn in list(s)]
        sinks.append(self._from_twin)

    def _from_twin(self, data):
        r = self.receiver
        if r is not None:
            r(data)

    def send(self, data):
        if self._closed:
            raise LinkError(_("Studio closed"))
        try:
            self.backend.call("midi", data=[int(x) & 0xFF for x in data])
        except StudioError as e:
            raise LinkError(str(e))

    def close(self):
        self._closed = True
        sinks = getattr(self.backend, "_sinks", None)
        if sinks and self._from_twin in sinks:
            sinks.remove(self._from_twin)

    @property
    def alive(self):
        return not self._closed


# ======================================================================= WAV
def wav_header(frames: int, fmt: str = "f32", fs: int = FS, channels: int = 2) -> bytes:
    """RIFF-Kopf: fmt "f32" = IEEE float 32 bit (WAVE_FORMAT_EXTENSIBLE-frei, Format 3), "s24" = PCM 24 bit."""
    bits, tag = (32, 3) if fmt == "f32" else (24, 1)
    block = channels * bits // 8
    data = frames * block
    fmt_chunk = struct.pack("<HHIIHH", tag, channels, fs, fs * block, block, bits)
    extra = b""
    if tag == 3:                                     # float: fact-Chunk (Pflicht laut Spezifikation)
        fmt_chunk += struct.pack("<H", 0)
        extra = b"fact" + struct.pack("<II", 4, frames)
    return (b"RIFF" + struct.pack("<I", 4 + 8 + len(fmt_chunk) + len(extra) + 8 + data) + b"WAVE" +
            b"fmt " + struct.pack("<I", len(fmt_chunk)) + fmt_chunk + extra + b"data" + struct.pack("<I", data))


def finish_wav(raw: Path, out: Path, frames: int, fmt: str, fs: int = FS):
    """rohe PCM-Daten (raw) -> WAV-Datei out (atomar)."""
    tmp = out.with_suffix(out.suffix + ".part")
    with open(tmp, "wb") as f, open(raw, "rb") as r:
        f.write(wav_header(frames, fmt, fs))
        shutil.copyfileobj(r, f, 1 << 20)
    tmp.replace(out)
    raw.unlink(missing_ok=True)


@dataclass
class RenderResult:
    path: Path
    seconds: float
    frames: int
    ended: bool                      # until_end: der Song ist zu Ende gelaufen
    peak: float
    stems: dict[int, Path] = field(default_factory=dict)   # Spur (0..3) -> WAV


# ======================================================================= Studio
class Studio:
    def __init__(self, backend: TwinBackend, warning: str = ""):
        self.backend = backend
        self.build = backend.build
        self.warning = warning                       # gewählter Build passt nicht genau (pick_build)
        self.info: dict = {}
        self.rejected: list[int] = []                # Backup-Teile, die der Zwilling beim Einspielen abgelehnt hat
        self.transport = StudioTransport(backend)
        self.link = Link(self.transport)

    # ------------------------------------------------------------- Erzeugen
    @classmethod
    def create(cls, firmware: str = "melodee", version: str = "", backend: str | type = "webengine",
               flash: bytes | None = None, **kw) -> "Studio":
        """Zwilling der gewünschten (oder nächsten) Version starten. backend: "webengine" | "node" | Klasse."""
        build, _exact, warning = pick_build(firmware, version)
        if backend == "node":
            be = NodeBackend(build, **kw)
        elif backend == "webengine":
            be = WebEngineBackend(build, **kw)
        else:
            be = backend(build, **kw)
        st = cls(be, warning)
        st.info = be.start(flash)
        return st

    @classmethod
    def for_backup(cls, archive, **kw) -> "Studio":
        """Zwilling in der Version, mit der das Backup gemacht wurde (sonst die nächste, mit Warnung)."""
        archive = _archive(archive)
        st = cls.create(version=backup_version(archive), **kw)
        st.open_from_backup(archive)
        return st

    def close(self):
        with contextlib.suppress(Exception):
            self.link.close()
        self.backend.close()

    # ------------------------------------------------------------- Zustand
    def open_from_backup(self, src, progress: Callable[[int, int], None] | None = None, tolerant: bool = False):
        """Komplettes Backup (Datei, JSON-Text/-Bytes, dict oder backup.Archive) in den Zwilling spielen – über das
        Backup-Protokoll der Firmware (backup.restore), also genau wie auf ein echtes FM-1. -> Archive
        tolerant=True: Teile, die die Firmware ablehnt (andere Version, beschädigt), werden übersprungen statt
        abzubrechen; ihre IDs stehen danach in ``self.rejected`` (der Zwilling behält dort seinen eigenen Stand)."""
        from . import backup
        archive = _archive(src)
        self.stop()
        self.rejected = []
        if tolerant:
            self.rejected = restore_tolerant(self.link, archive, progress, self.build.firmware)
        else:
            backup.restore(self.link, archive, progress, firmware_key=self.build.firmware)
        self.info = self.backend.call("info")
        return archive

    def open_from_device(self, link, progress: Callable[[int, int], None] | None = None, note: str = "",
                         tolerant: bool = False):
        """Backup vom echten FM-1 lesen und in den Zwilling spielen. -> (Archive, VersionCheck)"""
        from . import backup
        dev = device_version(link)
        f = backup.capture(link, dev, progress, companion={"firmware": self.build.firmware, "firmware_version": dev,
                                                           "version_string": dev, "note": note, "kind": "studio",
                                                           "app": "FM-1 Companion"})
        archive = backup.read(f)
        self.open_from_backup(archive, tolerant=tolerant)
        return archive, self.check_versions(device=dev)

    def backup(self, progress=None):
        """Backup des Zwillings (Format des Web-Editors) -> backup.Archive"""
        from . import backup
        self.stop()
        f = backup.capture(self.link, self.build.version, progress,
                           companion={"firmware": self.build.firmware, "firmware_version": self.build.version,
                                      "version_string": "MELODEE " + self.build.version, "kind": "studio",
                                      "device": "Studio", "app": "FM-1 Companion"})
        return backup.read(f)

    def write_to_device(self, link, progress=None, force: bool = False, settings: bool = True,
                        exclude=()):
        """Den Stand des Zwillings auf das echte FM-1 schreiben (backup.restore). Andere Firmware-Version am Gerät:
        StudioError, außer force=True. settings=False: das Objekt „Einstellungen“ (1) bleibt am Gerät, wie es ist;
        exclude: weitere Objekt-IDs, die nicht geschrieben werden (z. B. ``self.rejected``). -> Archive"""
        from . import backup
        vc = self.check_versions(device=device_version(link))
        if not vc.ok and not force:
            raise StudioError(_("{message} Nothing written.").format(message=vc.message))
        archive = self.backup()
        skip = set(exclude) | (set() if settings else {1})
        if skip:
            ids = [o["id"] for o in archive.objects if o["id"] not in skip]
            backup.restore_selected(link, archive, ids, progress)
        else:
            backup.restore(link, archive, progress, firmware_key=self.build.firmware)
        return archive

    def flash(self) -> bytes:
        return bytes(self.backend.get_flash())

    def flash_sum(self) -> int:
        """kurze Prüfsumme des Flashs (erkennt Änderungen, ohne 1 MB zu übertragen)."""
        return int(self.backend.call("flash_sum")["sum"])

    def set_flash(self, flash: bytes) -> dict:
        """mit diesem Flash neu einschalten (Rückgängig/Wiederherstellen)."""
        self.info = self.backend.set_flash(bytes(flash))
        return self.info

    def set_clock(self, run: bool):
        self.backend.set_clock(run)

    def save_state(self, path: Path | None = None, profile=None, note: str = "") -> Path:
        """Den ganzen Flash des Zwillings (alles, was ein FM-1 behält) als Datei. Ohne path: im Datenordner der App
        beim Geräteprofil (``state_path(profile)``). Nicht gespeicherte Änderungen im RAM (das laufende Projekt)
        gehören nicht dazu – wie beim Gerät; vorher ggf. speichern (Editor PROJECT save)."""
        path = Path(path) if path else state_path(profile)
        path.parent.mkdir(parents=True, exist_ok=True)
        meta = {"format": "fm1-studio-state", "version": 1, "firmware": self.build.firmware,
                "firmware_version": self.build.version, "identity": self.build.identity,
                "created": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"), "note": note,
                "abi": STUDIO_ABI}
        tmp = path.with_suffix(path.suffix + ".part")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("studio.json", json.dumps(meta, indent=1, ensure_ascii=False))
            z.writestr("flash.bin", self.flash())
        tmp.replace(path)
        return path

    def load_state(self, path: Path | None = None, profile=None) -> dict:
        """Gespeicherten Stand laden: der Zwilling startet neu mit diesem Flash. -> Metadaten (mit Warnung)"""
        meta, flash = read_state(Path(path) if path else state_path(profile))
        self.info = self.backend.set_flash(flash)
        vc = check_versions(self.build.version, backup=meta.get("firmware_version", ""))
        meta["warning"] = vc.message
        return meta

    def power_cycle(self) -> dict:
        """Aus- und einschalten (RAM weg, Flash bleibt) – wie am Gerät."""
        self.info = self.backend.call("reset")
        return self.info

    # ------------------------------------------------------------- Spielen
    def play(self, how: str = "play") -> dict:
        """how "play": die PLAY-Taste (von vorn); "song": die Song-Kette ab Zeile 1."""
        return self.backend.call("transport_start", how=how)["transport"]

    def stop(self) -> dict:
        return self.backend.call("transport_start", how="stop")["transport"]

    def transport_state(self) -> dict:
        return self.backend.call("transport")

    def press(self, label: str):
        return self.backend.call("press", label=B[label.upper()])

    def load_project(self, slot: int) -> int:
        """Projekt slot (0..31 = Projekt 1..32) laden, wie die PROJECT-Seite. -> 0 ok, 1 leer, 2 erst stoppen"""
        return self.backend.call("project_load", slot=int(slot))["rc"]

    def projects(self) -> list[int]:
        return self.backend.call("project_used")["used"]

    def start_audio(self):
        self.backend.set_audio(True)

    def stop_audio(self):
        self.backend.set_audio(False)

    def note(self, note: int, velocity: int = 100, channel: int = 0):
        self.link.send_midi([(0x90 if velocity else 0x80) | channel, note & 127, velocity & 127])

    def play_midi(self, data):
        """Kanal-Nachricht (MIDI-Keyboard) in den Zwilling, ohne auf ihn zu warten."""
        self.backend.post("midi", data=[int(x) & 0xFF for x in data])

    # ------------------------------------------------------------- Rendern
    def render_wav(self, path, seconds: float | None = None, until_end: bool = False, stems: bool = False,
                   start: str = "play", source: str = "live", fmt: str = "f32", max_seconds: float = 900,
                   tail_seconds: float = 2.0, project: int | None = None,
                   progress: Callable[[int, int], None] | None = None) -> RenderResult:
        """Offline rendern (eigene Instanz, schneller als Echtzeit; der laufende Zwilling bleibt unberührt).
        source "live": vom aktuellen RAM-Stand (auch Ungespeichertes), "saved": frisch eingeschaltet aus dem Flash.
        start "play" (PLAY von vorn) | "song" (Song-Kette) | "none" (wie es gerade läuft).
        seconds ODER until_end (bis der Transport steht, plus tail_seconds; höchstens max_seconds).
        stems=True: zusätzlich je Spur 1..4 eine WAV (<name>_spur1.wav …): dieselbe Wiedergabe mit allen anderen
        Spuren stumm (TRACK_MIX), also die Spur mit ihrem Anteil an den gemeinsamen Effekten.
        fmt "f32" (32-bit float) oder "s24" (24-bit PCM). 44,1 kHz Stereo wie das Gerät.
        progress(fertige Durchläufe, alle Durchläufe): Mix und je Stem ein Durchlauf."""
        if not seconds and not until_end:
            raise StudioError(_("Duration missing (seconds or until_end)"))
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        params = {"seconds": seconds or 0, "until_end": bool(until_end), "max_seconds": max_seconds,
                  "tail_seconds": tail_seconds, "start": start, "source": source, "format": fmt}
        if project is not None:
            params["project"] = int(project)
        if source == "live" and project is None:
            params["settle_ms"] = 0
        passes = 5 if stems else 1
        if progress:
            progress(0, passes)
        r = self._render_one(path, params, None)
        res = RenderResult(path, r["seconds"], r["frames"], bool(r.get("ended")), r.get("peak", 0.0))
        if progress:
            progress(1, passes)
        if stems:
            if until_end:                                  # alle Stems gleich lang wie der Mix
                params.update(until_end=False, seconds=r["frames"] / FS)
            for k in range(4):
                p = path.with_name(f"{path.stem}_spur{k + 1}{path.suffix}")
                self._render_one(p, {**params, "mutes": [j != k for j in range(4)]}, None)
                res.stems[k] = p
                if progress:
                    progress(2 + k, passes)
        return res

    def _render_one(self, path: Path, params: dict, progress) -> dict:
        raw = path.with_suffix(path.suffix + ".raw")
        r = self.backend.render(raw, params)
        finish_wav(raw, path, int(r["frames"]), params["format"], int(r.get("fs", FS)))
        return r

    # ------------------------------------------------------------- Versionen
    def version_info(self) -> dict:
        return {"firmware": self.build.firmware, "version": self.build.version, "identity": self.build.identity,
                "reported": self.info.get("version", ""), "commit": self.build.manifest.get("commit", ""),
                "built": self.build.manifest.get("build_date", ""), "warning": self.warning}

    def check_versions(self, device: str = "", backup: str = "", link=None, archive=None) -> VersionCheck:
        if link is not None and not device:
            device = device_version(link)
        if archive is not None and not backup:
            backup = backup_version(_archive(archive))
        return check_versions(self.build.version, device, backup, self.build.firmware.capitalize())


def restore_tolerant(link, archive, progress=None, firmware_key: str = "melodee") -> list[int]:
    """Backup Teil für Teil einspielen (Projekte/Banken, dann Einstellungen, die laufende Musik zuletzt – wie
    backup.restore); Teile, die die Firmware ablehnt, überspringen. -> abgelehnte Objekt-IDs"""
    from . import backup
    backup._match(link, archive, firmware_key)
    if archive.sloop:
        raise StudioError(_("The Melodee twin cannot load SLOOP backups."))
    sup = {o.id for o in backup.parse_manifest(link.request(Cmd.BACKUP_LIST, [], timeout=3.0, retries=0))}
    objs = archive.objects
    order = objs[2:] + objs[1:2] + objs[0:1]
    todo = [o for o in order if o["id"] in sup and not backup.is_sample(o["id"])]
    total = sum(o["size"] for o in todo) or 1
    done = 0
    rejected = [o["id"] for o in objs if o["id"] not in sup and o["size"]]
    for o in todo:
        try:
            backup.write_object(link, o["id"], o["bytes"], None, backup.FELUCCA)
        except backup.BackupError:
            rejected.append(o["id"])
        done += o["size"]
        if progress:
            progress(done, total)
    return rejected


def _archive(src):
    from . import backup
    if isinstance(src, backup.Archive):
        return src
    if isinstance(src, (str, Path)) and not str(src).lstrip().startswith("{"):
        return backup.load_file(Path(src))
    return backup.read(src)


def state_path(profile=None) -> Path:
    """Datenordner der App/studio/<Geräteprofil>/Studio-Stand.fm1studio"""
    from . import config
    from .devices import safe_folder_name
    name = getattr(profile, "folder_name", None) or (safe_folder_name(profile) if isinstance(profile, str) else "Studio")
    return config.DATA_DIR / "studio" / name / STATE_NAME


def read_state(path: Path) -> tuple[dict, bytes]:
    try:
        with zipfile.ZipFile(path) as z:
            meta = json.loads(z.read("studio.json"))
            flash = z.read("flash.bin")
    except (OSError, KeyError, ValueError, zipfile.BadZipFile) as e:
        raise StudioError(_("Not a studio state: {name} ({error})").format(name=path.name, error=e))
    if meta.get("format") != "fm1-studio-state" or len(flash) != 0x100000:
        raise StudioError(_("{name} is not a valid studio state").format(name=path.name))
    return meta, flash


# ======================================================================= QtWebEngine
class WebEngineBackend(TwinBackend):
    """Zwilling in QtWebEngine: Seite sims/_studio/studio.html (lokaler Server der App), Zwilling im Web Worker,
    Ton über AudioWorklet (Chromium -> PipeWire-Standardausgang), Offline-Rendern in einem zweiten Worker; PCM und
    Flash gehen über den lokalen Server (POST) zu Python. Braucht eine laufende QApplication; Aufrufe aus dem GUI-
    Thread warten mit einer lokalen Ereignisschleife, aus anderen Threads blockierend."""

    def __init__(self, build: TwinBuild, server=None, page=None, clock: bool = True, on_event=None):
        self.build = build
        self.on_event = on_event                     # Ereignisse der sichtbaren Bedienfläche (Taste, Rückgängig …)
        self.server = server
        self._own_server = server is None
        self.page = page
        self.clock = clock
        self.bridge = None

    def start(self, flash: bytes | None = None) -> dict:
        from .studio_web import WebTwin
        if self.server is None:
            from .editor_host import LocalServer
            self.server = LocalServer()
        self.web = WebTwin(self.server, self.build, on_midi=self._midi_out, page=self.page)
        self.web.on_event = self.on_event
        self.web.load()
        return self.web.call("boot", wasm=self.build.url_path, flash=flash, clock=self.clock, timeout=60)

    def call(self, op: str, timeout: float = 60, **args) -> dict:
        return self.web.call(op, timeout=timeout, **args)

    def render(self, path: Path, params: dict) -> dict:
        return self.web.render(path, params)

    def set_audio(self, on: bool) -> None:
        self.web.call("audio", on=bool(on), timeout=20)

    def post(self, op: str, **args) -> None:
        self.web.post(op, **args)

    def close(self):
        with contextlib.suppress(Exception):
            self.web.close()
        if self._own_server and self.server is not None:
            with contextlib.suppress(Exception):
                self.server.stop()
