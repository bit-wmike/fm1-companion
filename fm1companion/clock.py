# SPDX-License-Identifier: GPL-3.0-only
"""MIDI-Clock senden: 24 Pulse pro Viertel aus einem eigenen Thread, driftfrei.

Jeder Puls hat einen festen Sollzeitpunkt ``t0 + n × Abstand`` (nicht "letzter Puls + Abstand"),
so summieren sich Verspätungen nicht auf. Gewartet wird grob mit sleep() und die letzte
Millisekunde aktiv – das hält die Abweichung auf einem Pi 5 im Bereich weniger Zehntel-ms.
Tempowechsel setzen den Zeitbezug am letzten Puls neu, damit nichts springt.
Swing verschiebt jeden zweiten 16tel-Block (6 Pulse) nach hinten.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable

CLOCK, START, CONTINUE, STOP = 0xF8, 0xFA, 0xFB, 0xFC
BPM_MIN, BPM_MAX = 30.0, 300.0


def pulse_interval(bpm: float) -> float:
    return 60.0 / (max(BPM_MIN, min(BPM_MAX, bpm)) * 24)


def swing_offset(pulse: int, bpm: float, swing: float) -> float:
    """Verschiebung (s) für Puls n: die Pulse der zweiten 16tel jedes Achtels kommen um
    swing × halbe 16tel später (swing 0..0.5 → 50 %..75 % „Swing“)."""
    if swing <= 0:
        return 0.0
    within = pulse % 12                       # 12 Pulse = eine Achtel
    if within < 6:
        return 0.0
    # gestaucht, damit der nächste Achtel-Anfang wieder genau sitzt
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
    """send(bytes) wird aus dem Clock-Thread aufgerufen (schnell bleiben!)."""

    def __init__(self, send: Callable[[list[int]], None], bpm: float = 120.0):
        self.send = send
        self._bpm = float(bpm)
        self.swing = 0.0
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.running = False
        self.pulses = 0
        self.errors: deque[float] = deque(maxlen=2000)   # |Ist − Soll| in s
        self.send_errors = 0

    # ---- Tempo
    @property
    def bpm(self) -> float:
        return self._bpm

    @bpm.setter
    def bpm(self, v: float):
        with self._lock:
            self._bpm = max(BPM_MIN, min(BPM_MAX, float(v)))
            self._rebase = True

    # ---- Transport
    def start(self, cont: bool = False):
        self.stop_thread()
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
        except Exception:                           # noqa: BLE001 – Port weg: zählen, weiterlaufen
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
                    if n:                                   # ab dem letzten Sollzeitpunkt mit neuem Abstand
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
            # zu weit hinten (System hing)? nicht nachholen, neu einrasten
            if pc() - target > 0.25:
                base_t, base_n = pc(), n

    # ---- Messung
    def jitter_stats(self) -> dict:
        e = sorted(self.errors)
        if not e:
            return {"n": 0}
        def q(p):
            return e[min(len(e) - 1, int(p * len(e)))] * 1000
        return {"n": len(e), "mean_ms": sum(e) / len(e) * 1000, "p95_ms": q(0.95), "max_ms": e[-1] * 1000}
