# SPDX-License-Identifier: GPL-3.0-only
"""MIDI-Verbindung zu einem FM-1 und die Anfrage/Antwort-Logik.

Ein ``Link`` gehört genau einem Gerät. Er hat zwei Betriebsarten:

* **App**: die App stellt Anfragen (Backup, Projekte, Infos …) – nacheinander,
  mit Zeitlimit, genau eine Antwort pro Anfrage.
* **Editor**: der eingebettete Web-Editor spricht über die MIDI-Brücke
  (bridge.py) direkt mit dem Gerät; der Link reicht Rohdaten nur weiter.

Beides gleichzeitig würde sich gegenseitig die Antworten wegnehmen. Deshalb
holt sich jede App-Operation mit ``exclusive()`` die Leitung: der Editor wird so
lange abgemeldet (er sieht ein getrenntes Gerät) und danach wieder angemeldet.
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
    """Byte-Transport: send() schickt eine komplette MIDI-Nachricht, receiver
    bekommt jede eingehende komplette Nachricht (Liste von Bytes)."""

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
                self.out.close()           # sonst bleibt der Ausgang offen (Port belegt)
            raise
        self._closed = False

    def _cb(self, msg):
        r = self.receiver
        if r is not None:
            try:
                r(list(msg.bytes()))
            except Exception:                       # nie den MIDI-Thread abschießen
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
    """Für Tests und die Demo: ein simuliertes FM-1 als Kindprozess, eine
    Nachricht pro Zeile als Hex (siehe tests/fake_fm1.py, tests/mock_fm1.mjs)."""

    def __init__(self, argv: list[str]):
        self.p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
        self.started = threading.Event()        # erste Zeile vom Kindprozess (z. B. „# fake FM-1 bereit“)
        self._t = threading.Thread(target=self._reader, daemon=True)
        self._t.start()

    def wait_started(self, timeout: float) -> bool:
        """warten, bis der Kindprozess läuft (Python-Start mit Imports kann auf einem ausgelasteten Rechner mehrere
        Sekunden dauern). False, wenn er beendet ist oder nach `timeout` noch nichts gesagt hat."""
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
        self._req_lock = threading.RLock()        # eine App-Anfrage nach der anderen
        self._pending: tuple[int, queue.Queue] | None = None
        self._passthrough: Callable[[list[int]], None] | None = None
        # weitere Web-Seiten (Simulator, Online-Editoren …) neben dem Editor
        self._passthroughs: list[Callable[[list[int]], None]] = []
        # Mithörer (MIDI-Monitor, Takt-Erkennung …): sehen ALLES, Richtung "in" oder "out"
        self._listeners: list[Callable[[list[int], str], None]] = []
        self._exclusive_depth = 0
        self._ex_lock = threading.Lock()
        self.on_exclusive_change: Callable[[bool], None] | None = None   # True = Editor muss pausieren
        self.closed = False

    # -------- eingehend --------
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
                except Exception:               # eine kaputte Seite darf den MIDI-Thread nicht stören
                    pass

    def _notify(self, data, direction: str):
        for fn in list(self._listeners):
            try:
                fn(data, direction)
            except Exception:
                pass

    # -------- Mithörer (nur lesen) --------
    def add_listener(self, fn: Callable[[list[int], str], None]):
        """fn(bytes, "in"|"out") für jede Nachricht – aus dem MIDI-Thread! Schnell bleiben, nichts blockieren."""
        if fn not in self._listeners:
            self._listeners.append(fn)

    def remove_listener(self, fn):
        if fn in self._listeners:
            self._listeners.remove(fn)

    def add_passthrough(self, fn: Callable[[list[int]], None]):
        """zusätzliche Weiterleitung eingehender Nachrichten (pausiert wie der Editor bei App-Operationen)."""
        if fn not in self._passthroughs:
            self._passthroughs.append(fn)

    def remove_passthrough(self, fn):
        if fn in self._passthroughs:
            self._passthroughs.remove(fn)

    # -------- Editor-Weiterleitung --------
    def set_passthrough(self, fn: Callable[[list[int]], None] | None):
        self._passthrough = fn

    def send_raw(self, data: list[int]) -> bool:
        """vom Editor: nur senden, wenn die App die Leitung gerade nicht braucht."""
        if self._exclusive_depth or self.closed:
            return False
        self._send(data)
        return True

    def send_midi(self, data: list[int], force: bool = False) -> bool:
        """normale MIDI-Nachricht der App (Noten, CC, Takt …). Während einer App-Operation
        (Backup, Update …) wird nichts gesendet, außer force=True."""
        if self.closed or (self._exclusive_depth and not force):
            return False
        self._send(list(data))
        return True

    def _send(self, data):
        self.t.send(data)
        self._notify(list(data), "out")

    @contextlib.contextmanager
    def exclusive(self):
        """App-Operation: der Editor pausiert so lange."""
        self.hold()
        try:
            yield self
        finally:
            self.release()

    def hold(self, settle: bool = True):
        """Gerät für die App reservieren (Editor pausiert), bis release() – auch über längere Zeit."""
        with self._ex_lock:
            self._exclusive_depth += 1
            first = self._exclusive_depth == 1
        if first and self.on_exclusive_change:
            self.on_exclusive_change(True)
            if settle:
                time.sleep(0.15)                    # offene Editor-Anfragen auslaufen lassen

    def release(self):
        with self._ex_lock:
            if self._exclusive_depth == 0:
                return
            self._exclusive_depth -= 1
            last = self._exclusive_depth == 0
        if last and self.on_exclusive_change:
            self.on_exclusive_change(False)

    # -------- Anfragen --------
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
