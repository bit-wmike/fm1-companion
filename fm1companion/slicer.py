# SPDX-License-Identifier: GPL-3.0-only
"""Slice loops: detect transients, equal-length pieces, BPM grid.

* Transients: spectral flux (positive change of the log magnitude spectrum) with
  an adaptive threshold (moving median + offset), minimum distance and
  sensitivity; afterwards every hit is refined in the time signal to the start of the
  rise (envelope) and optionally moved to a zero crossing.
* The result is cut points in seconds. From these come pieces (start, end),
  WAV files for the library or directly a kit (non-destructive: every
  zone points to the original file with start/end).
"""
from __future__ import annotations

import re
import struct
import wave
from pathlib import Path

import numpy as np

from . import samples as S


# ------------------------------------------------------------ detection ---

def _frames(x: np.ndarray, n: int, hop: int) -> np.ndarray:
    if len(x) < n:
        x = np.pad(x, (0, n - len(x)))
    count = 1 + (len(x) - n) // hop
    idx = np.arange(n)[None, :] + hop * np.arange(count)[:, None]
    return x[idx]


def onset_strength(x, sr: int, hop: int = 0, n_fft: int = 0) -> tuple[np.ndarray, int, int]:
    """-> (flux per frame, normalised to 0..1, hop, n_fft). Frame k starts at k*hop."""
    x = np.asarray(x, dtype=np.float64)
    hop = hop or max(64, int(round(sr * 0.005)))           # ~5 ms grid
    n_fft = n_fft or (1024 if sr <= 50000 else 2048)
    fr = _frames(np.pad(x, (n_fft // 2, 0)), n_fft, hop) * np.hanning(n_fft)[None, :]
    mag = np.abs(np.fft.rfft(fr, axis=1))
    lm = np.log1p(100.0 * mag)
    flux = np.zeros(len(lm))
    flux[1:] = np.maximum(lm[1:] - lm[:-1], 0).sum(axis=1)
    m = flux.max()
    flux = flux / m if m > 0 else flux
    # energy rise in dB (catches narrow-band hits such as kicks that change little in the spectrum)
    # energy in 20 ms windows, each ending at the end of the 5 ms grid (smooths low notes)
    w = max(hop, int(0.02 * sr))
    c = np.concatenate([[0.0], np.cumsum(np.pad(x, (w, 0)) ** 2)])
    ends = w + hop * np.arange(1, len(x) // hop + 1)
    e = 10 * np.log10((c[ends] - c[ends - w]) / w + 1e-10)
    rise = np.zeros(len(flux))
    n = min(len(e) - 1, len(rise))
    rise[:n] = np.clip((e[1:n + 1] - e[:n]) / 20.0, 0, 1)   # aligned to the flux grid
    rise[:n][e[1:n + 1] < e.max() - 50] = 0               # ignore the noise floor
    return 0.5 * flux + 0.5 * rise, hop, n_fft


def _moving_median(a: np.ndarray, half: int) -> np.ndarray:
    pad = np.pad(a, half, mode="edge")
    win = _frames(pad, 2 * half + 1, 1)[:len(a)]
    return np.median(win, axis=1)


def _refine(x: np.ndarray, sr: int, center: int, radius: int) -> int:
    """Start of the rise around `center`: first point at which the envelope reaches 15 % of its
    maximum in the window (moved back to the last minimum before it)."""
    a = max(0, center - radius)
    b = min(len(x), center + radius)
    if b - a < 8:
        return center
    seg = np.abs(x[a:b])
    k = max(1, int(sr * 0.0007))
    env = np.convolve(seg, np.ones(k) / k, mode="same")
    imax = int(np.argmax(env))
    pk = env[imax]
    if pk <= 0:
        return center
    j = int(np.argmin(env[:imax + 1]))                 # quietest point before the peak (decay before it)
    base = env[j]
    i = j + int(np.argmax(env[j:imax + 1] >= base + 0.15 * (pk - base)))
    j0 = max(j, i - int(sr * 0.003))                   # up to 3 ms back to the minimum
    if i > j0:
        i = j0 + int(np.argmin(env[j0:i + 1]))
    return a + i


def detect_onsets(x, sr: int, sensitivity: float = 0.5, min_gap: float = 0.06,
                  refine: bool = True) -> list[float]:
    """Transients in seconds. sensitivity 0..1 (1 = also finds quiet hits)."""
    x = np.asarray(x, dtype=np.float64)
    if len(x) < 32 or not np.any(x):
        return []
    flux, hop, n_fft = onset_strength(x, sr)
    sens = min(1.0, max(0.0, float(sensitivity)))
    half = max(3, int(0.05 * sr / hop))                  # ±50 ms for the median
    thr = _moving_median(flux, half) + (0.32 - 0.29 * sens)
    thr = np.maximum(thr, 0.02)
    gap = max(1, int(min_gap * sr / hop))
    peaks = []
    for k in range(1, len(flux) - 1):
        if flux[k] >= thr[k] and flux[k] >= flux[k - 1] and flux[k] > flux[k + 1]:
            if peaks and k - peaks[-1] < gap:
                if flux[k] > flux[peaks[-1]]:
                    peaks[-1] = k
                continue
            peaks.append(k)
    out = []
    for k in peaks:
        # frame k (with n_fft/2 lead-in) centred at k*hop; the rise lies shortly before it
        c = k * hop
        pos = _refine(x, sr, c, n_fft // 2 + hop) if refine else c
        w = max(16, int(0.02 * sr))
        before = np.sqrt(np.mean(x[max(0, pos - w):pos] ** 2)) if pos > 0 else 0.0
        after = np.sqrt(np.mean(x[pos:pos + w] ** 2)) if pos < len(x) else 0.0
        if after < 1.4 * before + 1e-9:                    # no rise (e.g. a hard cut into silence)
            continue
        t = pos / sr
        if out and t - out[-1] < min_gap * 0.5:
            continue
        out.append(t)
    return out


def equal_points(duration: float, n: int) -> list[float]:
    n = max(1, int(n))
    return [duration * i / n for i in range(n)]


def grid_points(duration: float, bpm: float, division: int = 16, offset: float = 0.0) -> list[float]:
    """Grid: division = 4 (quarters), 8 (eighths), 16 (sixteenths) – in 4/4."""
    step = 60.0 / max(1e-6, bpm) * 4 / max(1, division)
    out, t = [], max(0.0, offset)
    while t < duration - 1e-6 and len(out) < 512:
        out.append(round(t, 6))
        t += step
    return out


def guess_bpm(duration: float, lo: float = 70, hi: float = 180) -> float:
    """from the length of a loop: a whole number of bars (1, 2, 4, 8 …) within a sensible range."""
    if duration <= 0:
        return 120.0
    for bars in (1, 2, 4, 8, 16, 0.5):
        bpm = 60.0 * 4 * bars / duration
        if lo <= bpm <= hi:
            return round(bpm, 2)
    return 120.0


def snap_zero(x, sr: int, t: float, radius_ms: float = 2.0) -> float:
    """Cut point to the nearest zero crossing (avoids clicks)."""
    x = np.asarray(x)
    i = int(t * sr)
    r = int(sr * radius_ms / 1000)
    a, b = max(1, i - r), min(len(x) - 1, i + r)
    if b <= a:
        return t
    seg = x[a - 1:b]
    zc = np.nonzero(np.signbit(seg[:-1]) != np.signbit(seg[1:]))[0]
    if not len(zc):
        return t
    j = a + zc[np.argmin(np.abs(a + zc - i))]
    return j / sr


def ranges(points: list[float], duration: float, min_len: float = 0.01) -> list[tuple[float, float]]:
    pts = sorted(p for p in set(round(p, 6) for p in points) if 0 <= p < duration)
    out = []
    for i, a in enumerate(pts):
        b = pts[i + 1] if i + 1 < len(pts) else duration
        if b - a >= min_len:
            out.append((a, b))
    return out


# ------------------------------------------------------------ result ---

def _safe(name: str) -> str:
    return re.sub(r"[^\w\-. ]+", "_", name).strip() or "Slice"


def write_wav(path: Path, x, sr: int):
    a = np.clip(np.asarray(x, dtype=np.float64), -1, 1)
    pcm = (a * 32767).astype("<i2")
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def export_slices(x, sr: int, rngs: list[tuple[float, float]], folder: Path, base: str,
                  fade_ms: float = 3.0) -> list[Path]:
    """Pieces as 16-bit WAV (mono, original rate) with a short fade-out."""
    x = np.asarray(x, dtype=np.float64)
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    fo = int(sr * fade_ms / 1000)
    for i, (a, b) in enumerate(rngs):
        y = x[int(a * sr):int(b * sr)].copy()
        if fo and len(y) > fo:
            y[-fo:] *= np.linspace(1, 0, fo)
        p = folder / f"{_safe(base)} {i + 1:02d}.wav"
        write_wav(p, y, sr)
        out.append(p)
    return out


def kit_from_slices(path: str, rngs: list[tuple[float, float]], name: str = "SLICES",
                    base_note: int = 36, max_zones: int = 16, normalize: bool = True,
                    fit: bool = True) -> S.Kit:
    """Kit whose zones point to the original file with start/end (non-destructive).
    Pieces beyond max_zones are left out; with fit=True they are shortened to fit."""
    kit = S.Kit(name=re.sub(r"[^\x20-\x7E]", "", name.upper())[:8] or "SLICES", mode="drum", base_note=base_note)
    stem = Path(path).stem
    for i, (a, b) in enumerate(rngs[:max_zones]):
        kit.zones.append(S.Zone(path=str(path), label=f"{stem} {i + 1:02d}",
                                edit=S.Edit(start=round(a, 5), end=round(b, 5), trim_silence=False,
                                            normalize=normalize, fade_out_ms=3.0)))
    kit.assign_keys()
    if fit and kit.zones:
        total, _ = S.kit_budget(kit)
        if total > S.SLOT_BYTES:
            S.fit_to_slot(kit)
    return kit
