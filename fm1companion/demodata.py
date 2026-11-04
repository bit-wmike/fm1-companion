# SPDX-License-Identifier: GPL-3.0-only
"""Beispieldaten für Demo und erste Schritte: ein paar synthetische Drum-Samples (WAV) – nur stdlib."""
from __future__ import annotations

import math
import random
import struct
import wave
from pathlib import Path

from .i18n import _

RATE = 44100


def _write(path: Path, samples: list[float]):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"".join(struct.pack("<h", int(max(-1, min(1, s)) * 30000)) for s in samples))


def _kick(n=int(RATE * 0.45)):
    out, ph = [], 0.0
    for i in range(n):
        t = i / RATE
        ph += 2 * math.pi * (45 + 110 * math.exp(-t * 30)) / RATE
        out.append(math.sin(ph) * math.exp(-t * 7))
    return out


def _noise(n, decay, tone=0.0, f=180, seed=1):
    r = random.Random(seed)
    return [((r.uniform(-1, 1) * (1 - tone)) + tone * math.sin(2 * math.pi * f * i / RATE)) * math.exp(-i / RATE * decay)
            for i in range(n)]


# the keys are file names (stay as they are)
SOUNDS = {"Kick 01": _kick, "Snare 01": lambda: _noise(int(RATE * 0.3), 18, 0.35, 190, 2),
          "Hat geschlossen": lambda: _noise(int(RATE * 0.08), 60, 0, seed=3),  # i18n: ignore – file name
          "Hat offen": lambda: _noise(int(RATE * 0.4), 9, 0, seed=4),
          "Clap": lambda: [s * (1 if (i // 900) % 2 == 0 or i > 3600 else 0.3) for i, s in enumerate(_noise(int(RATE * 0.25), 16, 0, seed=5))],
          "Tom tief": lambda: [math.sin(2 * math.pi * (90 + 40 * math.exp(-i / RATE * 20)) * i / RATE) * math.exp(-i / RATE * 9)  # i18n: ignore – file name
                               for i in range(int(RATE * 0.4))],
          "Bass C2": lambda: [(math.sin(2 * math.pi * 65.41 * i / RATE) + 0.3 * math.sin(4 * math.pi * 65.41 * i / RATE))
                              * 0.7 * math.exp(-i / RATE * 3) for i in range(int(RATE * 0.8))]}


def create(folder: Path) -> list[Path]:
    """legt die Beispiel-Samples an (vorhandene Dateien bleiben unangetastet)."""
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for name, fn in SOUNDS.items():
        p = folder / f"{name}.wav"
        if not p.exists():
            _write(p, fn())
        out.append(p)
    (folder / "LIESMICH.txt").write_text(_("Example samples of the FM-1 Companion Suite (generated synthetically, free "  # i18n: ignore
                                           "to use). Can be deleted at any time.") + "\n", "utf-8")
    return out
