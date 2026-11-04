# SPDX-License-Identifier: GPL-3.0-only
"""MIDI connection to an FM-1 and the request/reply logic.

A ``Link`` belongs to exactly one device. It has two modes:

* **App**: the app makes requests (backup, projects, info …) – one after another,
  with a timeout, exactly one reply per request.
* **Editor**: the embedded web editor talks directly to the device via the MIDI bridge
  (bridge.py); the link only passes raw data through.

Both at the same time would steal each other's replies. That is why
every app operation takes the line with ``exclusive()``: the editor is
detached for that long (it sees a disconnected device) and attached again afterwards.
"""
from __future__ import annotations

import contextlib
import queue
import subprocess
import threading
import time
from typing import Callable

from .i18n import _
from .protocol import frame, unframe, ProtocolError


class LinkError(Exception):
    pass


class Transport:
    """Byte transport: send() sends a complete MIDI message, receiver
    gets every incoming complete message (list of bytes)."""

    receiver: Callable[[list[int]], None] | None = None

    def send(self, data: list[int]) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass

    @property
    def alive(self) -> bool:
        return True


class MidoTransport(Transport):
    def __init__(self, in_name: str, out_name: str):
        import mido
        self.mido = mido
        self.in_name, self.out_name = in_name, out_name
        self.out = mido.open_output(out_name)
        try:
            self.inp = mido.open_input(in_name, callback=self._cb)
        except Exception:
            with contextlib.suppress(Exception):
                self.out.close()           # otherwise the output stays open (port busy)
            raise
        self._closed = False

    def _cb(self, msg):
        r = self.receiver
        if r is not None:
            try:
                r(list(msg.bytes()))
            except Exception:                       # never kill the MIDI thread
                pass

    def send(self, data):
        if self._closed:
            raise LinkError(_("connection closed"))
        msg = self.mido.Message.from_bytes(list(data))
        self.out.send(msg)

    def close(self):
        if self._closed:
            return
        self._closed = True
        for p in (self.inp, self.out):
            with contextlib.suppress(Exception):
                p.close()

    @property
    def alive(self):
        return not self._closed


class SubprocessTransport(Transport):
    """For tests and the demo: a simulated FM-1 as a child process, one
    message per line as hex (see tests/fake_fm1.py, tests/mock_fm1.mjs)."""

    def __init__(self, argv: list[str]):
        self.p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
        self.started = threading.Event()        # first line from the child process (e.g. "# fake FM-1 bereit")
        self._t = threading.Thread(target=self._reader, daemon=True)
        self._t.start()

    def wait_started(self, timeout: float) -> bool:
        """wait until the child process is running (Python start-up with imports can take several seconds on a
        busy computer). False if it has exited or has said nothing after `timeout`."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.started.wait(0.1):
                return True
            if self.p.poll() is not None:
                return False
        return self.started.is_set()

    def _reader(self):
        for line in self.p.stdout:
            self.started.set()
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                data = list(bytes.fromhex(line))
            except ValueError:
                continue
            r = self.receiver
            if r:
                r(data)

    def send(self, data):
        if self.p.poll() is not None:
            raise LinkError(_("simulation ended"))
        self.p.stdin.write(bytes(data).hex() + "\n")
        self.p.stdin.flush()

    def close(self):
        with contextlib.suppress(Exception):
            self.p.stdin.close()
            self.p.terminate()
            self.p.wait(2)

    @property
    def alive(self):
        return self.p.poll() is None


class Link:
    def __init__(self, transport: Transport):
        self.t = transport
        self.t.receiver = self._receive
        self._req_lock = threading.RLock()        # one app request after another
        self._pending: tuple[int, queue.Queue] | None = None
        self._passthrough: Callable[[list[int]], None] | None = None
        # further web pages (simulator, online editors …) next to the editor
        self._passthroughs: list[Callable[[list[int]], None]] = []
        # listeners (MIDI monitor, clock detection …): see EVERYTHING, direction "in" or "out"
        self._listeners: list[Callable[[list[int], str], None]] = []
        self._exclusive_depth = 0
        self._ex_lock = threading.Lock()
        self.on_exclusive_change: Callable[[bool], None] | None = None   # True = editor must pause
        self.closed = False

    # -------- incoming --------
    def _receive(self, data: list[int]):
        self._notify(data, "in")
        p = self._pending
        if p is not None:
            f = unframe(data)
            if f and f[0] == p[0]:
                p[1].put(f[1])
                return
        if self._exclusive_depth == 0:
            pt = self._passthrough
            if pt is not None:
                pt(data)
            for fn in list(self._passthroughs):
                try:
                    fn(data)
                except Exception:               # a broken page must not disturb the MIDI thread
                    pass

    def _notify(self, data, direction: str):
        for fn in list(self._listeners):
            try:
                fn(data, direction)
            except Exception:
                pass

    # -------- listeners (read only) --------
    def add_listener(self, fn: Callable[[list[int], str], None]):
        """fn(bytes, "in"|"out") for every message – from the MIDI thread! Keep it fast, block nothing."""
        if fn not in self._listeners:
            self._listeners.append(fn)

    def remove_listener(self, fn):
        if fn in self._listeners:
            self._listeners.remove(fn)

    def add_passthrough(self, fn: Callable[[list[int]], None]):
        """additional forwarding of incoming messages (paused like the editor during app operations)."""
        if fn not in self._passthroughs:
            self._passthroughs.append(fn)

    def remove_passthrough(self, fn):
        if fn in self._passthroughs:
            self._passthroughs.remove(fn)

    # -------- editor forwarding --------
    def set_passthrough(self, fn: Callable[[list[int]], None] | None):
        self._passthrough = fn

    def send_raw(self, data: list[int]) -> bool:
        """from the editor: only send when the app does not currently need the line."""
        if self._exclusive_depth or self.closed:
            return False
        self._send(data)
        return True

    def send_midi(self, data: list[int], force: bool = False) -> bool:
        """normal MIDI message from the app (notes, CC, clock …). During an app operation
        (backup, update …) nothing is sent, unless force=True."""
        if self.closed or (self._exclusive_depth and not force):
            return False
        self._send(list(data))
        return True

    def _send(self, data):
        self.t.send(data)
        self._notify(list(data), "out")

    @contextlib.contextmanager
    def exclusive(self):
        """App operation: the editor pauses for that long."""
        self.hold()
        try:
            yield self
        finally:
            self.release()

    def hold(self, settle: bool = True):
        """Reserve the device for the app (editor paused) until release() – even for a longer time."""
        with self._ex_lock:
            self._exclusive_depth += 1
            first = self._exclusive_depth == 1
        if first and self.on_exclusive_change:
            self.on_exclusive_change(True)
            if settle:
                time.sleep(0.15)                    # let open editor requests run out

    def release(self):
        with self._ex_lock:
            if self._exclusive_depth == 0:
                return
            self._exclusive_depth -= 1
            last = self._exclusive_depth == 0
        if last and self.on_exclusive_change:
            self.on_exclusive_change(False)

    # -------- requests --------
    def request(self, cmd: int, args=(), timeout: float = 1.5, retries: int = 1) -> list[int]:
        if self.closed:
            raise LinkError(_("device not connected"))
        with self._req_lock:
            last = None
            for attempt in range(retries + 1):
                q: queue.Queue = queue.Queue()
                self._pending = (cmd, q)
                try:
                    self._send(frame(cmd, args))
                    return q.get(timeout=timeout)
                except queue.Empty:
                    last = LinkError(_("no answer to command {cmd}").format(cmd=cmd))
                except (OSError, ProtocolError) as e:
                    last = LinkError(str(e))
                    break
                finally:
                    self._pending = None
            raise last

    def ping(self) -> bool:
        try:
            self.request(25, [], timeout=0.6, retries=1)
            return True
        except LinkError:
            return False

    def close(self):
        self.closed = True
        self._passthrough = None
        self._passthroughs.clear()
        self._listeners.clear()
        self.t.close()
