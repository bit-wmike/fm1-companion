# SPDX-License-Identifier: GPL-3.0-only
"""Own samples as a SoundFont (.sf2) – so that FluidSynth plays them on a MIDI channel.

Writes SoundFont 2.01 (RIFF ``sfbk``): INFO, sdta/smpl (16-bit mono), pdta with
phdr/pbag/pmod/pgen/inst/ibag/imod/igen/shdr. One preset = one instrument; each zone has a
key range, a root key and plays its sample once (no loop).

* from one of the app's kits (``samples.Kit``): exactly as it would go onto the FM-1 (22,050 Hz,
  the same editing); drum kits: each key one sound, instruments: spread across the keyboard.
* from a folder of WAVs: drum map from C2 (36, General MIDI bass drum) in file name order.
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

from .i18n import _

# generator numbers (SoundFont 2.01, section 8.1.2)
G_INSTRUMENT, G_RELEASE_VOL, G_KEYRANGE, G_ATTENUATION, G_SAMPLE_ID, G_SAMPLE_MODES, G_ROOTKEY = 41, 38, 43, 48, 53, 54, 58
PAD = 46                                         # mandatory zero samples after each sample


@dataclass
class Sample:
    name: str
    data: list[int]          # int16 mono
    rate: int
    root: int = 60


@dataclass
class SfZone:
    sample: int              # Index in Bank.samples
    lo: int
    hi: int
    root: int


@dataclass
class SfPreset:
    name: str
    zones: list[SfZone]
    program: int = 0
    bank: int = 0
    one_shot: bool = True    # drums: note-off does not cut off


@dataclass
class Bank:
    name: str = "FM-1 Kit"
    samples: list[Sample] = field(default_factory=list)
    presets: list[SfPreset] = field(default_factory=list)


def _name20(s: str) -> bytes:
    s = re.sub(r"[^\x20-\x7E]", "_", s)[:19]
    return s.encode("ascii").ljust(20, b"\0")


def _chunk(cid: bytes, data: bytes) -> bytes:
    pad = b"\0" if len(data) % 2 else b""
    return cid + struct.pack("<I", len(data)) + data + pad


def _list(kind: bytes, *chunks: bytes) -> bytes:
    return _chunk(b"LIST", kind + b"".join(chunks))


def _zstr(s: str) -> bytes:
    b = re.sub(r"[^\x20-\x7E]", "_", s)[:255].encode("ascii") + b"\0"
    return b + (b"\0" if len(b) % 2 else b"")


def build(bank: Bank) -> bytes:
    if not bank.presets or not bank.samples:
        raise ValueError(_("The SoundFont needs at least one sound."))
    # ---- Samples
    smpl = bytearray()
    shdr = bytearray()
    for s in bank.samples:
        if not s.data:
            raise ValueError(_("{name}: empty sample").format(name=s.name))
        start = len(smpl) // 2
        smpl += struct.pack(f"<{len(s.data)}h", *[max(-32768, min(32767, int(v))) for v in s.data])
        end = len(smpl) // 2
        smpl += b"\0\0" * PAD
        m = 8 if end - start > 32 else 0                # loop points (unused) with a margin to the edges
        shdr += struct.pack("<20sIIIIIBbHH", _name20(s.name), start, end, start + m, end - m,
                            int(s.rate), max(0, min(127, s.root)), 0, 0, 1)   # 1 = monoSample
    shdr += struct.pack("<20sIIIIIBbHH", _name20("EOS"), 0, 0, 0, 0, 0, 0, 0, 0, 0)
    # ---- instruments (one per preset)
    inst, ibag, igen = bytearray(), bytearray(), bytearray()
    phdr, pbag, pgen = bytearray(), bytearray(), bytearray()
    gi = bi = 0
    pgi = pbi = 0
    for k, p in enumerate(bank.presets):
        inst += struct.pack("<20sH", _name20(p.name), bi)
        for z in p.zones:
            ibag += struct.pack("<HH", gi, 0)
            bi += 1
            gens = [(G_KEYRANGE, (max(0, min(127, z.hi)) << 8) | max(0, min(127, z.lo))),
                    (G_RELEASE_VOL, (1200 if p.one_shot else -2084) & 0xFFFF),
                    (G_SAMPLE_MODES, 0),
                    (G_ROOTKEY, max(0, min(127, z.root))),
                    (G_SAMPLE_ID, z.sample)]                     # sampleID always last
            for op, amt in gens:
                igen += struct.pack("<HH", op, amt)
                gi += 1
        phdr += struct.pack("<20sHHHIII", _name20(p.name), p.program & 127, p.bank, pbi, 0, 0, 0)
        pbag += struct.pack("<HH", pgi, 0)
        pbi += 1
        pgen += struct.pack("<HH", G_INSTRUMENT, k)              # instrument always last
        pgi += 1
    inst += struct.pack("<20sH", _name20("EOI"), bi)
    ibag += struct.pack("<HH", gi, 0)
    igen += struct.pack("<HH", 0, 0)
    phdr += struct.pack("<20sHHHIII", _name20("EOP"), 0, 0, pbi, 0, 0, 0)
    pbag += struct.pack("<HH", pgi, 0)
    pgen += struct.pack("<HH", 0, 0)
    mod_end = b"\0" * 10
    info = _list(b"INFO", _chunk(b"ifil", struct.pack("<HH", 2, 1)), _chunk(b"isng", _zstr("EMU8000")),
                 _chunk(b"INAM", _zstr(bank.name)), _chunk(b"ISFT", _zstr("FM-1 Companion")))
    sdta = _list(b"sdta", _chunk(b"smpl", bytes(smpl)))
    pdta = _list(b"pdta", _chunk(b"phdr", bytes(phdr)), _chunk(b"pbag", bytes(pbag)), _chunk(b"pmod", mod_end),
                 _chunk(b"pgen", bytes(pgen)), _chunk(b"inst", bytes(inst)), _chunk(b"ibag", bytes(ibag)),
                 _chunk(b"imod", mod_end), _chunk(b"igen", bytes(igen)), _chunk(b"shdr", bytes(shdr)))
    body = b"sfbk" + info + sdta + pdta
    return b"RIFF" + struct.pack("<I", len(body)) + body


def write(bank: Bank, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(build(bank))
    tmp.replace(path)
    return path


# ------------------------------------------------------------ sources ---
def _key_split(roots: list[int]) -> list[tuple[int, int]]:
    n = len(roots)
    return [(0 if j == 0 else (roots[j - 1] + r) // 2 + 1, 127 if j == n - 1 else (r + roots[j + 1]) // 2)
            for j, r in enumerate(roots)]


def bank_from_kit(kit, program: int = 0) -> Bank:
    """samples.Kit -> bank (uses samples.render_zone: the same editing as for the FM-1)."""
    from . import samples as S
    if not kit.zones:
        raise ValueError(_("The kit is empty."))
    b = Bank(name=kit.name or "Kit")
    drum = kit.mode == "drum"
    if drum:
        kit.assign_keys()
    zs = []
    for z in kit.zones:
        b.samples.append(Sample(z.name, S.render_zone(z), S.SLOT_RATE, z.root))
        zs.append(SfZone(len(b.samples) - 1, z.lo if z.lo is not None else -1, z.hi if z.hi is not None else -1, z.root))
    if any(z.lo < 0 for z in zs):
        order = sorted(range(len(zs)), key=lambda i: zs[i].root)
        for i, (lo, hi) in zip(order, _key_split([zs[i].root for i in order])):
            if zs[i].lo < 0:
                zs[i].lo, zs[i].hi = lo, hi
    b.presets.append(SfPreset(kit.name or "Kit", zs, program=program, one_shot=drum))
    return b


AUDIO = {".wav", ".wave", ".aif", ".aiff", ".flac", ".mp3", ".ogg"}


def bank_from_folder(folder: Path, base_note: int = 36, name: str = "") -> Bank:
    """Folder of sounds -> drum kit from base_note (C2 = 36), at most 128 − base_note sounds."""
    from . import samples as S
    folder = Path(folder)
    files = sorted((p for p in folder.iterdir() if p.suffix.lower() in AUDIO and not p.name.startswith(".")),
                   key=lambda p: p.name.lower())[:max(1, 128 - base_note)]
    if not files:
        raise ValueError(_("There are no audio files in “{folder}”.").format(folder=folder.name))
    b = Bank(name=name or folder.name)
    zs = []
    for i, f in enumerate(files):
        rate, x = S.read_mono(f)
        pk = max(1e-9, max((abs(v) for v in x), default=0.0))
        data = [int(v / pk * 30000) for v in x] if pk > 1.0 else [int(v * 32767) for v in x]
        key = base_note + i
        b.samples.append(Sample(f.stem, data, int(rate), key))
        zs.append(SfZone(i, key, key, key))
    b.presets.append(SfPreset(b.name, zs, one_shot=True))
    return b


def drum_map(bank: Bank) -> list[tuple[int, str]]:
    """(key, sound) – for display."""
    out = []
    for p in bank.presets:
        for z in p.zones:
            out.append((z.lo, bank.samples[z.sample].name) if z.lo == z.hi else (z.root, bank.samples[z.sample].name))
    return out
