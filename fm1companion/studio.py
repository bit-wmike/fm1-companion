# SPDX-License-Identifier: GPL-3.0-only
"""Studio: the FM-1's digital twin in the suite.

The FM-1 is the instrument, the suite is the studio. The twin is **the same firmware** as on the device
(Melodee's own C code, compiled to WebAssembly for the computer, see tools/build_sims.py --target
melodee-studio and tools/studio/melodee_studio.c): the same engines, effects, the same mixer and sequencer, plus
the NOR flash (settings, projects, user presets) and the editor SysEx protocol over the firmware's USB path.
It runs in QtWebEngine (web worker; sound via AudioWorklet -> Chromium -> PipeWire = the Pi's speakers) and
talks to the outside exactly like a real FM-1: ``Studio.link`` is a ``transport.Link`` – backup.read/restore,
presets, project writing, playback.py etc. work unchanged.

Layers (kept open for further firmwares/emulators with little effort):

* ``TwinBuild``   – a bundled twin: ``sims/<firmware>-studio/<version>/`` with manifest.json
                    (several versions side by side; ``builds()``, ``pick_build()`` picks a match or the nearest)
* ``TwinBackend`` – where the twin runs. ``WebEngineBackend`` (runtime: QtWebEngine), ``NodeBackend`` (only
                    tests/development, needs node). A later backend (e.g. fm1-emu: CPU emulation of real
                    .fwsc packages) only has to provide the same methods.
* ``StudioTransport`` – ``transport.Transport`` over a backend (for ``Link``)
* ``Studio``      – the facade for the UI and tools

API (``Studio``)::

    st = Studio.create(version="v1.6.0-rc.8")           # or Studio.for_version(device_version)
    st.open_from_backup(path | bytes | dict | Archive)  # load a complete backup via the editor protocol
    st.open_from_device(link)                           # read a backup from the real FM-1, then load it
    st.link                                             # link to the twin (as to a real FM-1)
    st.play(how="play"|"song") / st.stop()              # transport (PLAY key or song chain, editor stop)
    st.load_project(slot)                               # load project 1..32 (slot 0..31) like the PROJECT page
    st.start_audio() / st.stop_audio()                  # real-time sound (WebEngine: default output)
    st.render_wav(path, seconds=None, until_end=False, stems=False, start="play", source="live", fmt="f32")
                                                        # offline, faster than real time -> RenderResult
    st.save_state(path=None, profile=None) / st.load_state(path=None, profile=None)
                                                        # the whole flash (= everything the device keeps)
    st.backup() -> Archive                              # backup OF THE TWIN (e.g. back onto the FM-1)
    st.write_to_device(link)                            # write this backup to the real FM-1
    st.version_info() / st.check_versions(device=..., backup=...) -> VersionCheck (warning in German)
    st.set_clock(run)                                   # pause/run the twin's clock (worker 'clock')
    st.flash_sum() / st.set_flash(bytes)                # detect changes, restore an intermediate state
    st.play_midi(data)                                  # channel message without waiting (MIDI keyboard)
    open_from_backup(..., tolerant=True)                # skip rejected parts -> st.rejected

UI: ui/studio_service.py (the app's service, virtual device) and ui/pages/studio.py ("Studio" page).

Limits: see the build's NOTICE and ``LIMITS`` below.
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

# panel.c: the keys by label (PANEL_DEFAULT; the twin resolves a learned panel itself)
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


# ======================================================================= versions and builds
_VER = re.compile(r"v?(\d+)\.(\d+)(?:\.(\d+))?(?:[- ]?(rc|beta|alpha)\.?(\d+))?", re.I)


def norm_version(s: str) -> str:
    """'MELODEE v1.6.0-rc.7' / '1.6.0-rc.7' / 'Melodee 1.6.0 rc7' -> 'v1.6.0-rc.7' ('' if no version)."""
    m = _VER.search(s or "")
    if not m:
        return ""
    a, b, c, pre, n = m.groups()
    return f"v{a}.{b}.{c or 0}" + (f"-{pre.lower()}.{n}" if pre else "")


def version_key(s: str) -> tuple:
    """sortable: pre-releases (rc) before the final version."""
    m = _VER.search(s or "")
    if not m:
        return (0, 0, 0, 0, 0)
    a, b, c, pre, n = m.groups()
    rank = {"alpha": 0, "beta": 1, "rc": 2}.get((pre or "").lower(), 3)
    return (int(a), int(b), int(c or 0), rank, int(n or 0))


@dataclass
class TwinBuild:
    firmware: str                 # "melodee"
    version: str                  # "v1.6.0-rc.8" (as the firmware reports it)
    path: Path                    # folder with manifest.json and the WASM
    manifest: dict = field(default_factory=dict)

    @property
    def wasm(self) -> Path:
        return self.path / self.manifest.get("wasm", "twin.wasm")

    @property
    def identity(self) -> str:
        return self.manifest.get("identity", "")

    @property
    def url_path(self) -> str:    # under the local server (editor_host.LocalServer: /sims/...)
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
    """all bundled twins of a firmware, oldest first."""
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
    """-> (build, exact?, warning). Without a request: the newest. Otherwise the same version, otherwise the nearest
    (preferably the next older one: a newer firmware reads its data safely, not always the other way round)."""
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
    """Compares the twin's firmware with the device's (INFO) and the backup's -> warning."""
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


# Melodee 1.6.0 (docs/RELEASE_1.6.0.md "Sound and levels"): the mix sounds different than up to 1.6.0-rc.8
LEVELS_1_6_0 = _("From Melodee 1.6.0 on the mix differs from the pre-releases: CZ-1 6 dB quieter, limiter only "
                  "at −2 dBFS, dither – levels differ.")
_V160 = (1, 6, 0, 3, 0)


def levels_changed(a: str, b: str) -> bool:
    """does the level change of Melodee 1.6.0 lie between the two versions?"""
    ka, kb = version_key(a), version_key(b)
    return ka != (0, 0, 0, 0, 0) and kb != (0, 0, 0, 0, 0) and (ka < _V160) != (kb < _V160)


def device_version(link) -> str:
    """Firmware version of the connected device (editor INFO, e.g. 'MELODEE v1.6.0-rc.7' -> 'v1.6.0-rc.7')."""
    return norm_version(parse_info(link.request(Cmd.INFO, [], timeout=1.5, retries=1)).version)


def backup_version(archive) -> str:
    """Firmware version a backup was made with (the app's companion block, otherwise the 'firmware' field)."""
    c = archive.companion or {}
    return norm_version(c.get("firmware_version") or c.get("version_string") or archive.firmware or "")


# ======================================================================= Backends
class TwinBackend:
    """Where a twin runs. All methods can be called from any thread and block until the result.

    Required for a backend (including a future fm1-emu backend):
      start(flash|None) -> info dict {version, fs, flash_size, ...}   (power on, with this flash)
      call(op, **args) -> dict     commands of twin-host.js: midi(data), press(label), transport_start(how),
                                   project_load(slot), project_used, transport, info, frame, run(ms), reset
      get_flash() -> bytes / set_flash(bytes)    (power on with new flash)
      render(path, params) -> dict              (offline, in a separate instance; writes PCM to path)
      set_audio(on)                              (real-time sound)
      on_midi: Callable[[list[int]], None]       (everything the device sends over USB MIDI)
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
        """The twin's clock (worker command 'clock'): False stops device time as long as no sound drives it
        (saves CPU time when the studio is not visible and nothing is playing)."""
        self.call("clock", run=bool(run))

    def post(self, op: str, **args) -> None:
        """Command without waiting for the reply (notes from the MIDI keyboard). Default: like call."""
        self.call(op, **args)

    def close(self) -> None:
        pass

    def _midi_out(self, msgs):
        fn = self.on_midi
        if fn:
            for m in msgs:
                try:
                    fn([int(x) & 0xFF for x in m])
                except Exception:                    # noqa: BLE001 – never kill the reader thread
                    pass


def _b64(b: bytes) -> dict:
    return {"b64": base64.b64encode(bytes(b)).decode()}


def _unb64(v):
    return base64.b64decode(v["b64"]) if isinstance(v, dict) and "b64" in v else v


class NodeBackend(TwinBackend):
    """Twin in node (tests/studio_node_host.mjs) – for tests and development without QtWebEngine. Not for the
    app's runtime (there is no node there)."""

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


# ======================================================================= transport for Link
class StudioTransport(Transport):
    """``transport.Transport`` over a twin: send() feeds the message(s) in via the firmware's USB MIDI path;
    everything the twin sends goes to ``receiver`` – like a real FM-1 on USB."""

    def __init__(self, backend: TwinBackend):
        self.backend = backend
        self._closed = False
        # several lines on one twin (the studio itself and the suite's virtual device): each gets everything
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
    """RIFF header: fmt "f32" = IEEE float 32 bit (without WAVE_FORMAT_EXTENSIBLE, format 3), "s24" = PCM 24 bit."""
    bits, tag = (32, 3) if fmt == "f32" else (24, 1)
    block = channels * bits // 8
    data = frames * block
    fmt_chunk = struct.pack("<HHIIHH", tag, channels, fs, fs * block, block, bits)
    extra = b""
    if tag == 3:                                     # float: fact chunk (mandatory according to the specification)
        fmt_chunk += struct.pack("<H", 0)
        extra = b"fact" + struct.pack("<II", 4, frames)
    return (b"RIFF" + struct.pack("<I", 4 + 8 + len(fmt_chunk) + len(extra) + 8 + data) + b"WAVE" +
            b"fmt " + struct.pack("<I", len(fmt_chunk)) + fmt_chunk + extra + b"data" + struct.pack("<I", data))


def finish_wav(raw: Path, out: Path, frames: int, fmt: str, fs: int = FS):
    """raw PCM data (raw) -> WAV file out (atomic)."""
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
    ended: bool                      # until_end: the song has played to the end
    peak: float
    stems: dict[int, Path] = field(default_factory=dict)   # track (0..3) -> WAV


# ======================================================================= Studio
class Studio:
    def __init__(self, backend: TwinBackend, warning: str = ""):
        self.backend = backend
        self.build = backend.build
        self.warning = warning                       # the chosen build does not match exactly (pick_build)
        self.info: dict = {}
        self.rejected: list[int] = []                # backup parts the twin rejected while loading
        self.transport = StudioTransport(backend)
        self.link = Link(self.transport)

    # ------------------------------------------------------------- creating
    @classmethod
    def create(cls, firmware: str = "melodee", version: str = "", backend: str | type = "webengine",
               flash: bytes | None = None, **kw) -> "Studio":
        """Start a twin of the requested (or nearest) version. backend: "webengine" | "node" | class."""
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
        """Twin in the version the backup was made with (otherwise the nearest, with a warning)."""
        archive = _archive(archive)
        st = cls.create(version=backup_version(archive), **kw)
        st.open_from_backup(archive)
        return st

    def close(self):
        with contextlib.suppress(Exception):
            self.link.close()
        self.backend.close()

    # ------------------------------------------------------------- state
    def open_from_backup(self, src, progress: Callable[[int, int], None] | None = None, tolerant: bool = False):
        """Load a complete backup (file, JSON text/bytes, dict or backup.Archive) into the twin – via the
        firmware's backup protocol (backup.restore), i.e. exactly as onto a real FM-1. -> Archive
        tolerant=True: parts the firmware rejects (different version, damaged) are skipped instead of
        aborting; their IDs are then in ``self.rejected`` (the twin keeps its own state there)."""
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
        """Read a backup from the real FM-1 and load it into the twin. -> (Archive, VersionCheck)"""
        from . import backup
        dev = device_version(link)
        f = backup.capture(link, dev, progress, companion={"firmware": self.build.firmware, "firmware_version": dev,
                                                           "version_string": dev, "note": note, "kind": "studio",
                                                           "app": "FM-1 Companion"})
        archive = backup.read(f)
        self.open_from_backup(archive, tolerant=tolerant)
        return archive, self.check_versions(device=dev)

    def backup(self, progress=None):
        """Backup of the twin (web editor format) -> backup.Archive"""
        from . import backup
        self.stop()
        f = backup.capture(self.link, self.build.version, progress,
                           companion={"firmware": self.build.firmware, "firmware_version": self.build.version,
                                      "version_string": "MELODEE " + self.build.version, "kind": "studio",
                                      "device": "Studio", "app": "FM-1 Companion"})
        return backup.read(f)

    def write_to_device(self, link, progress=None, force: bool = False, settings: bool = True,
                        exclude=()):
        """Write the twin's state to the real FM-1 (backup.restore). Different firmware version on the device:
        StudioError, unless force=True. settings=False: the "settings" object (1) stays on the device as it is;
        exclude: further object IDs that are not written (e.g. ``self.rejected``). -> Archive"""
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
        """short checksum of the flash (detects changes without transferring 1 MB)."""
        return int(self.backend.call("flash_sum")["sum"])

    def set_flash(self, flash: bytes) -> dict:
        """power on again with this flash (undo/restore)."""
        self.info = self.backend.set_flash(bytes(flash))
        return self.info

    def set_clock(self, run: bool):
        self.backend.set_clock(run)

    def save_state(self, path: Path | None = None, profile=None, note: str = "") -> Path:
        """The twin's whole flash (everything an FM-1 keeps) as a file. Without path: in the app's data folder
        with the device profile (``state_path(profile)``). Unsaved changes in RAM (the running project)
        are not included – as on the device; save first if needed (editor PROJECT save)."""
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
        """Load a saved state: the twin restarts with this flash. -> metadata (with warning)"""
        meta, flash = read_state(Path(path) if path else state_path(profile))
        self.info = self.backend.set_flash(flash)
        vc = check_versions(self.build.version, backup=meta.get("firmware_version", ""))
        meta["warning"] = vc.message
        return meta

    def power_cycle(self) -> dict:
        """Power off and on (RAM gone, flash stays) – like on the device."""
        self.info = self.backend.call("reset")
        return self.info

    # ------------------------------------------------------------- playing
    def play(self, how: str = "play") -> dict:
        """how "play": the PLAY key (from the start); "song": the song chain from row 1."""
        return self.backend.call("transport_start", how=how)["transport"]

    def stop(self) -> dict:
        return self.backend.call("transport_start", how="stop")["transport"]

    def transport_state(self) -> dict:
        return self.backend.call("transport")

    def press(self, label: str):
        return self.backend.call("press", label=B[label.upper()])

    def load_project(self, slot: int) -> int:
        """Load project slot (0..31 = project 1..32), like the PROJECT page. -> 0 ok, 1 empty, 2 stop first"""
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
        """Channel message (MIDI keyboard) into the twin, without waiting for it."""
        self.backend.post("midi", data=[int(x) & 0xFF for x in data])

    # ------------------------------------------------------------- rendering
    def render_wav(self, path, seconds: float | None = None, until_end: bool = False, stems: bool = False,
                   start: str = "play", source: str = "live", fmt: str = "f32", max_seconds: float = 900,
                   tail_seconds: float = 2.0, project: int | None = None,
                   progress: Callable[[int, int], None] | None = None) -> RenderResult:
        """Render offline (separate instance, faster than real time; the running twin stays untouched).
        source "live": from the current RAM state (including unsaved), "saved": freshly powered on from the flash.
        start "play" (PLAY from the start) | "song" (song chain) | "none" (as it is currently running).
        seconds OR until_end (until the transport stops, plus tail_seconds; at most max_seconds).
        stems=True: additionally one WAV per track 1..4 (<name>_spur1.wav …): the same playback with all other
        tracks muted (TRACK_MIX), i.e. the track with its share of the shared effects.
        fmt "f32" (32-bit float) or "s24" (24-bit PCM). 44.1 kHz stereo like the device.
        progress(finished passes, all passes): one pass for the mix and one per stem."""
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
            if until_end:                                  # all stems as long as the mix
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

    # ------------------------------------------------------------- versions
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
    """Load a backup part by part (projects/banks, then settings, the running music last – like
    backup.restore); skip parts the firmware rejects. -> rejected object IDs"""
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
    """app data folder/studio/<device profile>/Studio-Stand.fm1studio"""
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
    """Twin in QtWebEngine: page sims/_studio/studio.html (the app's local server), twin in a web worker,
    sound via AudioWorklet (Chromium -> PipeWire default output), offline rendering in a second worker; PCM and
    flash go to Python via the local server (POST). Needs a running QApplication; calls from the GUI
    thread wait with a local event loop, from other threads they block."""

    def __init__(self, build: TwinBuild, server=None, page=None, clock: bool = True, on_event=None):
        self.build = build
        self.on_event = on_event                     # events of the visible control surface (key, undo …)
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
