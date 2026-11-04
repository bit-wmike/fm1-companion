# SPDX-License-Identifier: GPL-3.0-only
# Derived from MSFA (Copyright 2012, 2013 Google Inc., Apache-2.0) and Dexed's msfa changes
# (Copyright 2016-2025 Pascal Gauthier, Apache-2.0) as adapted in M-VAVE FM1 Open web/synth/dx7*.c
# (Apache-2.0). Tables and envelope/scaling math ported to Python/numpy; Apache-2.0 is GPL-3.0 compatible.
"""Preview of a DX7 voice: approximate 6-operator FM renderer in pure numpy.

Not bit-exact, but with the original curves: 32 algorithms with feedback (including the loops across
several operators in 4 and 6), envelopes (DX7 timing like msfa), rate/level scaling,
ratio/fixed frequency/detune, level curve, velocity sensitivity, pitch envelope and a simple LFO.
Operators without feedback are computed for the whole note at once; only feedback operators
run sample by sample (that is the expensive part).
"""
from __future__ import annotations

import math

import numpy as np

from . import dx7

SR = 44100
N = 64                                   # block size like msfa
LVLLUT = [0, 5, 9, 13, 17, 20, 23, 25, 27, 29, 31, 33, 35, 37, 39, 41, 42, 43, 45, 46]
VELOCITY = [0, 70, 86, 97, 106, 114, 121, 126, 132, 138, 142, 148, 152, 156, 160, 163, 166, 170, 173, 174, 178, 181,
            184, 186, 189, 190, 194, 196, 198, 200, 202, 205, 206, 209, 211, 214, 216, 218, 220, 222, 224, 225, 227,
            229, 230, 232, 233, 235, 237, 238, 240, 241, 242, 243, 244, 246, 246, 248, 249, 250, 251, 252, 253, 254]
EXP_SCALE = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 14, 16, 19, 23, 27, 33, 39, 47, 56, 66, 80, 94, 110, 126, 142, 158,
             174, 190, 206, 222, 238, 250]
PITCH_RATE = [1, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 10, 11, 11, 12, 12, 13, 13, 14, 14, 15, 16, 16, 17,
              18, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 30, 31, 33, 34, 36, 37, 38, 39, 41, 42, 44, 46, 47, 49,
              51, 53, 54, 56, 58, 60, 62, 64, 66, 68, 70, 72, 74, 76, 79, 82, 85, 88, 91, 94, 98, 102, 106, 110, 115,
              120, 125, 130, 135, 141, 147, 153, 159, 165, 171, 178, 185, 193, 202, 211, 232, 243, 254, 255]
PITCH_LVL = [-128, -116, -104, -95, -85, -76, -68, -61, -56, -52, -49, -46, -43, -41, -39, -37, -35, -33, -32, -31,
             -30, -29, -28, -27, -26, -25, -24, -23, -22, -21, -20, -19, -18, -17, -16, -15, -14, -13, -12, -11, -10,
             -9, -8, -7, -6, -5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19,
             20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 38, 40, 43, 46, 49, 53, 58, 65, 73, 82,
             92, 103, 115, 127]
COARSE = [math.log2(0.5)] + [math.log2(c) if c else 0.0 for c in range(1, 32)]
PMS = [0, 10, 20, 33, 55, 92, 153, 255]
AMS = [0, 0.259, 0.427, 1.0]


def outlvl(x: int) -> int:
    return 28 + x if x >= 20 else LVLLUT[x]


def scale_curve(group, depth, curve):
    if curve in (0, 3):
        s = (group * depth * 329) >> 12
    else:
        s = (EXP_SCALE[min(group, len(EXP_SCALE) - 1)] * depth * 329) >> 15
    return -s if curve < 2 else s


def scale_level(note, bp, ld, rd, lc, rc):
    off = note - bp - 17
    if off >= 0:
        return scale_curve((off + 1) // 3, rd, rc)
    return scale_curve(-(off - 1) // 3, ld, lc)


def scale_rate(note, rs):
    x = min(31, max(0, note // 3 - 7))
    d = (rs * x) >> 3
    if rs == 3 and (x & 7) == 3:
        d -= 1
    elif rs == 7 and 0 < (x & 7) < 4:
        d += 1
    return d


class Env:
    """msfa Env: level in Q24 octaves (2^24 = doubling), one step per 64-sample block."""

    def __init__(self, rates, levels, out, rate_scaling):
        self.r, self.l, self.out, self.rs = rates, levels, out, rate_scaling
        self.lvl, self.down = 0, True
        self._adv(0)

    def _adv(self, ix):
        self.ix = ix
        if ix < 4:
            a = outlvl(self.l[ix]) >> 1
            a = max(16, (a << 6) + self.out - 4256)
            self.target = a << 16
            self.rising = self.target > self.lvl
            q = min(63, ((self.r[ix] * 41) >> 6) + self.rs)
            self.inc = ((4 + (q & 3)) << (2 + (q >> 2))) * N

    def step(self):
        if self.ix < 3 or (self.ix < 4 and not self.down):
            if self.rising:
                self.lvl = max(1716 << 16, self.lvl)
                self.lvl += (((17 << 24) - self.lvl) >> 24) * self.inc
                if self.lvl >= self.target:
                    self.lvl = self.target
                    self._adv(self.ix + 1)
            else:
                self.lvl -= self.inc
                if self.lvl <= self.target:
                    self.lvl = self.target
                    self._adv(self.ix + 1)
        return self.lvl

    def keyup(self):
        if self.down:
            self.down = False
            self._adv(3)


class PitchEnv:
    UNIT = (N * (1 << 24) * 10 + 213 * SR // 2) // (213 * SR)

    def __init__(self, rates, levels):
        self.r, self.l = rates, levels
        self.lvl = PITCH_LVL[levels[3]] << 19
        self.down = True
        self._adv(0)

    def _adv(self, ix):
        self.ix = ix
        if ix < 4:
            self.target = PITCH_LVL[self.l[ix]] << 19
            self.rising = self.target > self.lvl
            self.inc = PITCH_RATE[self.r[ix]] * self.UNIT

    def step(self):
        if self.ix < 3 or (self.ix < 4 and not self.down):
            if self.rising:
                self.lvl += self.inc
                if self.lvl >= self.target:
                    self.lvl = self.target
                    self._adv(self.ix + 1)
            else:
                self.lvl -= self.inc
                if self.lvl <= self.target:
                    self.lvl = self.target
                    self._adv(self.ix + 1)
        return self.lvl

    def keyup(self):
        if self.down:
            self.down = False
            self._adv(3)


def lfo_shape(wave: int, phase: np.ndarray, rng) -> np.ndarray:
    """LFO values −1..1 per block."""
    ph = phase % 1.0
    if wave == 0:
        return 1 - 4 * np.abs(ph - 0.5)
    if wave == 1:
        return 1 - 2 * ph
    if wave == 2:
        return 2 * ph - 1
    if wave == 3:
        return np.where(ph < 0.5, 1.0, -1.0)
    if wave == 4:
        return np.sin(2 * np.pi * ph)
    cyc = np.floor(phase).astype(int)
    vals = rng.uniform(-1, 1, cyc.max() + 2 if len(cyc) else 1)
    return vals[cyc]


def render(voice, note: int = 60, velocity: int = 100, seconds: float = 2.0, hold: float | None = None,
           sr: int = SR, normalize: float = 0.89) -> np.ndarray:
    """-> float32 mono −1..1. hold: seconds until release (default 70 % of the length)."""
    v = dx7.sanitize(voice)
    nb = max(1, int(seconds * sr) // N)
    ns = nb * N
    hold_b = int((seconds * 0.7 if hold is None else hold) * sr) // N
    note = max(0, min(127, note + v[dx7.VI["TRNSP"]] - 24))
    base = math.log2(440.0) + (note - 69) / 12.0                     # log2(Hz)
    alg = v[dx7.VI["ALG"]]
    fb = v[dx7.VI["FB"]]
    fb_shift = 8 - fb if fb else 16
    vel = max(1, min(127, velocity))
    # ---- prepare operators (k = 0 is operator 6, like msfa)
    envs, logf, fixed, ams = [], [], [], []
    for k in range(6):
        p = v[k * 21:k * 21 + 21]
        lev = outlvl(p[16]) + scale_level(note, p[8], p[9], p[10], p[11], p[12])
        lev = min(127, lev) << 5
        lev += ((p[15] * (VELOCITY[vel >> 1] - 239) + 7) >> 3) * 16
        lev = max(0, lev)
        envs.append(Env(p[0:4], p[4:8], lev, scale_rate(note, p[13])))
        if p[17] == 0:
            det = p[20] - 7
            lf = base + COARSE[p[18]] + (math.log2(1 + 0.01 * p[19]) if p[19] else 0.0)
            lf += 0.0209 * math.exp(-0.396 * base) / 7 * det * base
            fixed.append(False)
        else:
            lf = ((p[18] & 3) * 100 + p[19]) * math.log2(10) / 100 + (13457 * (p[20] - 7) / (1 << 24) if p[20] > 7 else 0)
            fixed.append(True)
        logf.append(lf)
        ams.append(AMS[p[14] & 3])
    penv = PitchEnv(v[126:130], v[130:134])
    # ---- envelopes block by block
    lev = np.zeros((6, nb + 1))
    pit = np.zeros(nb + 1)
    for b in range(nb + 1):
        if b == hold_b:
            for e in envs:
                e.keyup()
            penv.keyup()
        for k in range(6):
            lev[k, b] = envs[k].step()
        pit[b] = penv.step() / (1 << 24)
    # ---- LFO (simple): rate, delay, pitch and amp depth
    lfs, lfd, pmd, amd = v[137], v[138], v[139], v[140]
    if pmd or amd:
        sr_ = 1 if lfs == 0 else (165 * lfs) >> 6
        sr_ *= 11 if sr_ < 160 else 11 + ((sr_ - 160) >> 4)
        t = np.arange(nb + 1) * N / sr
        lfo_hz = sr_ * 25190424 / 2 ** 32                   # msfa: lfo_unit * sr_ per block -> Hz
        ph = t * lfo_hz
        lv = lfo_shape(v[142], ph, np.random.default_rng(1))
        delay = np.clip((t - lfd * 0.03) / max(0.05, lfd * 0.03 + 0.05), 0, 1) if lfd else np.ones_like(t)
        pit = pit + lv * delay * (pmd / 99) * (PMS[v[143]] / 255) * 0.5
        amod = (0.5 - 0.5 * lv) * delay * (amd / 99)
    else:
        amod = None
    # ---- gain per operator: 2^(level - 14) (Q24 octaves), linear between blocks
    xs = np.arange(ns) / N
    xb = np.arange(nb + 1)
    gains, incs = [], []
    for k in range(6):
        g = np.exp2(lev[k] / (1 << 24) - 14.0)
        if amod is not None and ams[k]:
            g = g * (1 - amod * ams[k] * 0.9)
        gains.append(np.interp(xs, xb, g))
        lfk = logf[k] + (0 if fixed[k] else pit)
        f = np.exp2(lfk) / sr if not fixed[k] else np.full(nb + 1, 2.0 ** logf[k] / sr)
        incs.append(np.repeat(np.minimum(f[:nb], 0.5), N))
    phases = [np.cumsum(i) - i for i in incs]
    # ---- algorithm
    flags = dx7.ALGORITHMS[alg]
    buses = {1: np.zeros(ns), 2: np.zeros(ns)}
    has = {1: False, 2: False}
    out = np.zeros(ns)
    tau = 2 * np.pi
    k = 0
    while k < 6:
        f = flags[k]
        if k == 0 and fb_shift < 16 and alg in (3, 5):
            f = 0xC4
        inbus, outbus, add = (f >> 4) & 3, f & 3, bool(f & 4)
        if (f & 0xC0) == 0xC0 and fb_shift < 16:
            chain = 3 if alg == 3 else 2 if alg == 5 else 1
            shift = min(fb_shift + 2, 16) if alg in (3, 5, 31) else fb_shift
            y = _feedback(phases[k:k + chain], gains[k:k + chain], shift)
            k += chain - 1
        else:
            mod = buses[inbus] if inbus and has[inbus] else 0.0
            y = gains[k] * np.sin(tau * (phases[k] + mod))
        if outbus == 0:
            out += y
        elif add and has[outbus]:
            buses[outbus] = buses[outbus] + y
            has[outbus] = True
        else:
            buses[outbus] = y
            has[outbus] = True
        k += 1
    # ---- output
    out[-min(len(out), 441):] *= np.linspace(1, 0, min(len(out), 441))
    out = np.nan_to_num(out)
    peak = float(np.max(np.abs(out))) if len(out) else 0.0
    if peak > 1e-9 and normalize:
        out *= normalize / peak
    return out.astype(np.float32)


def _feedback(phases, gains, shift: int) -> np.ndarray:
    """Feedback chain (1 to 3 operators) sample by sample; feedback from the end of the chain."""
    n = len(phases[0])
    scale = 2.0 ** (-shift - 1)
    tau = 2 * math.pi
    sin = math.sin
    ph = [p.tolist() for p in phases]
    gn = [g.tolist() for g in gains]
    out = [0.0] * n
    y0 = y = 0.0
    if len(ph) == 1:
        p0, g0 = ph[0], gn[0]
        for i in range(n):
            s = g0[i] * sin(tau * (p0[i] + (y0 + y) * scale))
            y0, y = y, s
            out[i] = s
    elif len(ph) == 2:
        p0, g0, p1, g1 = ph[0], gn[0], ph[1], gn[1]
        for i in range(n):
            s = g0[i] * sin(tau * (p0[i] + (y0 + y) * scale))
            s = g1[i] * sin(tau * (p1[i] + s))
            y0, y = y, s
            out[i] = s
    else:
        p0, g0, p1, g1, p2, g2 = ph[0], gn[0], ph[1], gn[1], ph[2], gn[2]
        for i in range(n):
            s = g0[i] * sin(tau * (p0[i] + (y0 + y) * scale))
            s = g1[i] * sin(tau * (p1[i] + s))
            s = g2[i] * sin(tau * (p2[i] + s))
            y0, y = y, s
            out[i] = s
    return np.asarray(out)


def to_int16(x: np.ndarray) -> list[int]:
    return np.clip(np.round(x * 32767), -32768, 32767).astype(np.int16).tolist()


def write_wav(x: np.ndarray, path, sr: int = SR):
    import wave
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(np.clip(np.round(x * 32767), -32768, 32767).astype("<i2").tobytes())
