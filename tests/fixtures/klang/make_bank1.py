# SPDX-License-Identifier: GPL-3.0-only
"""Creates fm1_bank1.syx: a DX7 bank (32 voices) from the app's random generator (fixed seed).

Since 2.3.1 it replaces the former test bank, which came from the FM-1's factory presets (copyright unclear).
The names are plain generic names; the sounds are generated, not taken from a firmware.

  python3 tests/fixtures/klang/make_bank1.py
"""
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from fm1companion import dx7  # noqa: E402

KINDS = ("PIANO", "ORGAN", "SYN LEAD", "SYN PAD")


def bank() -> bytes:
    rng = random.Random(20261102)
    voices = []
    for i in range(32):
        v = dx7.random_voice(rng)
        voices.append(dx7.set_name(v, f"{KINDS[i % 4]} {i // 4 + 1}"))
    return dx7.bank_syx(voices, channel=0)


if __name__ == "__main__":
    out = Path(__file__).with_name("fm1_bank1.syx")
    out.write_bytes(bank())
    print(out, out.stat().st_size)
