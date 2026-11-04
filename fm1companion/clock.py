# SPDX-License-Identifier: GPL-3.0-only
"""Send MIDI clock: 24 pulses per quarter note from a dedicated thread, drift-free.

Each pulse has a fixed target time ``t0 + n × interval`` (not "last pulse + interval"),
so delays do not add up. Waiting is coarse with sleep() and busy for the last
millisecond – that keeps the deviation on a Pi 5 within a few tenths of a ms.
Tempo changes reset the time reference at the last pulse so nothing jumps.
Swing shifts every second 16th block (6 pulses) later.
Song Position (F2, in 16ths from the start of the song) + Continue lets a follower join at a specific point –
Melodee 1.6.1+ follows this on every input (midi_clock.c ``midi_clock_position``: only while stopped, the jump
happens with the first pulse after Continue).
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable

CLOCK, START, CONTINUE, STOP, SONG_POSITION = 0xF8, 0xFA, 0xFB, 0xFC, 0xF2
SPP_MAX = 16383                                # 14 bits, in 16ths
BPM_MIN, BPM_MAX = 30.0, 300.0


def pulse_interval(bpm: float) -> float:
    return 60.0 / (max(BPM_MIN, min(BPM_MAX, bpm)) * 24)


def song_position(sixteenths: int) -> list[int]:
    """MIDI Song Position Pointer: 16ths from the start of the song (0..16383)."""
    v = max(0, min(SPP_MAX, int(sixteenths)))
    return [SONG_POSITION, v & 0x7F, v >> 7]


def bar_position(bar: int, beats_per_bar: int = 4) -> int:
    """Bar (1-based, 4/4) -> Song Position in 16ths."""
    return max(0, int(bar) - 1) * beats_per_bar * 4


def swing_offset(pulse: int, bpm: float, swing: float) -> float:
    """Offset (s) for pulse n: the pulses of the second 16th of each eighth come
    swing × half a 16th later (swing 0..0.5 → 50 %..75 % "swing")."""
    if swing <= 0:
        return 0.0
    within = pulse % 12                       # 12 pulses = one eighth
    if within < 6:
        return 0.0
    # compressed so that the next eighth starts exactly on time again
    sixteenth = 6 * pulse_interval(bpm)
    return swing * sixteenth * (12 - within) / 6


class TapTempo:
    def __init__(self, keep: int = 6, timeout: float = 2.0):
        self.taps: deque[float] = deque(maxlen=keep)
        self.timeout = timeout

    def tap(self, t: float | None = None) -> float | None:
        t = time.perf_counter() if t is None else t
        if self.taps and t - self.taps[-1] > self.timeout:
            self.taps.clear()
        self.taps.append(t)
        if len(self.taps) < 2:
            return None
        iv = (self.taps[-1] - self.taps[0]) / (len(self.taps) - 1)
        return max(BPM_MIN, min(BPM_MAX, 60.0 / iv))


class ClockMaster:
    """send(bytes) is called from the clock thread (keep it fast!)."""

    def __init__(self, send: Callable[[list[int]], None], bpm: float = 120.0):
        self.send = send
        self._bpm = float(bpm)
        self.swing = 0.0
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.running = False
        self.pulses = 0
        self.errors: deque[float] = deque(maxlen=2000)   # |actual − target| in s
        self.send_errors = 0

    # ---- tempo
    @property
    def bpm(self) -> float:
        return self._bpm

    @bpm.setter
    def bpm(self, v: float):
        with self._lock:
            self._bpm = max(BPM_MIN, min(BPM_MAX, float(v)))
            self._rebase = True

    # ---- transport
    def start(self, cont: bool = False, position: int | None = None):
        """Start (from the beginning), Continue (resume) or – with position (16ths) – Song Position + Continue."""
        self.stop_thread()
        if position is not None:
            msg = song_position(position)
            self._emit(msg)
            self._emit([CONTINUE])
            self.pulses = (msg[1] | msg[2] << 7) * 6
        else:
            self._emit([CONTINUE if cont else START])
            if not cont:
                self.pulses = 0
        self.errors.clear()
        self._stop.clear()
        self.running = True
        self._thread = threading.Thread(target=self._run, name="fm1c-clock", daemon=True)
        self._thread.start()

    def stop(self):
        was = self.running
        self.stop_thread()
        if was:
            self._emit([STOP])

    def stop_thread(self):
        self._stop.set()
        t = self._thread
        if t and t.is_alive() and t is not threading.current_thread():
            t.join(1.0)
        self._thread = None
        self.running = False

    def _emit(self, msg):
        try:
            self.send(msg)
        except Exception:                           # noqa: BLE001 – port gone: count, keep running
            self.send_errors += 1

    def _run(self):
        pc = time.perf_counter
        self._rebase = False
        base_t, base_n = pc(), 0
        n = 0
        target_plain = base_t
        while not self._stop.is_set():
            with self._lock:
                bpm, swing = self._bpm, self.swing
                if self._rebase:
                    if n:                                   # from the last target time with the new interval
                        base_t, base_n = target_plain, n - 1
                    self._rebase = False
            iv = pulse_interval(bpm)
            target_plain = base_t + (n - base_n) * iv
            target = target_plain + swing_offset(self.pulses, bpm, swing)
            while True:
                now = pc()
                left = target - now
                if left <= 0 or self._stop.is_set():
                    break
                if left > 0.002:
                    time.sleep(left - 0.0015)
            if self._stop.is_set():
                break
            self._emit([CLOCK])
            self.errors.append(abs(pc() - target))
            self.pulses += 1
            n += 1
            # too far behind (system stalled)? do not catch up, re-lock
            if pc() - target > 0.25:
                base_t, base_n = pc(), n

    # ---- measurement
    def jitter_stats(self) -> dict:
        e = sorted(self.errors)
        if not e:
            return {"n": 0}
        def q(p):
            return e[min(len(e) - 1, int(p * len(e)))] * 1000
        return {"n": len(e), "mean_ms": sum(e) / len(e) * 1000, "p95_ms": q(0.95), "max_ms": e[-1] * 1000}
