# SPDX-License-Identifier: GPL-3.0-only
"""Composition tools: everything produces Melodee steps (projectfmt.Step) for a pattern of up to 64 steps.

Euclidean rhythms, scales and chord progressions (degrees, inversions, voicings), arpeggios, bass,
rule-based random melody, Markov melody from a MIDI file, call/response, humanize and variations.
All randomness goes through random.Random(seed): same seed = same result.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from .i18n import N_, _, ngettext
from .projectfmt import NSTEP, ST_NOTE, ST_REST, ST_TIE, Step, LANES

MAX_NOTES = 4          # Melodee: up to 4 notes per step

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
# The German names below are keys (used by callers and tests); the UI shows the *_LABELS (translated with _()).
SCALES = {
    "Dur (Ionisch)": [0, 2, 4, 5, 7, 9, 11],
    "Moll (Äolisch)": [0, 2, 3, 5, 7, 8, 10],  # i18n: ignore – key
    "Dorisch": [0, 2, 3, 5, 7, 9, 10],
    "Phrygisch": [0, 1, 3, 5, 7, 8, 10],
    "Lydisch": [0, 2, 4, 6, 7, 9, 11],
    "Mixolydisch": [0, 2, 4, 5, 7, 9, 10],
    "Lokrisch": [0, 1, 3, 5, 6, 8, 10],
    "Harmonisch Moll": [0, 2, 3, 5, 7, 8, 11],
    "Melodisch Moll": [0, 2, 3, 5, 7, 9, 11],
    "Dur-Pentatonik": [0, 2, 4, 7, 9],
    "Moll-Pentatonik": [0, 3, 5, 7, 10],
    "Blues": [0, 3, 5, 6, 7, 10],
    "Chromatisch": list(range(12)),
}
PROGRESSIONS = {
    "I–V–vi–IV (Pop)": "I V vi IV",
    "ii–V–I (Jazz)": "ii7 V7 Imaj7 Imaj7",
    "I–vi–IV–V (50er)": "I vi IV V",
    "vi–IV–I–V": "vi IV I V",
    "Andalusisch (i–VII–VI–V)": "i VII VI V",
    "12-Takt-Blues": "I7 I7 I7 I7 IV7 IV7 I7 I7 V7 IV7 I7 V7",
    "Kanon (Pachelbel)": "I V vi iii IV I IV V",
    "i–iv–v (Moll)": "i iv v i",
    "I–IV (Gospel)": "I IV I IV",
}
SCALE_LABELS = {
    "Dur (Ionisch)": N_("Major (Ionian)"), "Moll (Äolisch)": N_("Minor (Aeolian)"), "Dorisch": N_("Dorian"),  # i18n: ignore – keys
    "Phrygisch": N_("Phrygian"), "Lydisch": N_("Lydian"), "Mixolydisch": N_("Mixolydian"), "Lokrisch": N_("Locrian"),
    "Harmonisch Moll": N_("Harmonic minor"), "Melodisch Moll": N_("Melodic minor"),
    "Dur-Pentatonik": N_("Major pentatonic"), "Moll-Pentatonik": N_("Minor pentatonic"), "Blues": N_("Blues"),
    "Chromatisch": N_("Chromatic"),
}
PROGRESSION_LABELS = {
    "I–vi–IV–V (50er)": N_("I–vi–IV–V (50s)"), "Andalusisch (i–VII–VI–V)": N_("Andalusian (i–VII–VI–V)"),
    "12-Takt-Blues": N_("12-bar blues"), "Kanon (Pachelbel)": N_("Canon (Pachelbel)"),
    "i–iv–v (Moll)": N_("i–iv–v (minor)"),
}
ROMAN = {"I": 0, "II": 1, "III": 2, "IV": 3, "V": 4, "VI": 5, "VII": 6}


def note_name(n: int) -> str:
    return f"{NOTE_NAMES[n % 12]}{n // 12 - 1}"


# ------------------------------------------------------------------------ rhythm ---
def euclid(steps: int, pulses: int, rotation: int = 0) -> list[bool]:
    """Bjorklund/Bresenham distribution: E(3,8) = x..x..x. ; rotation to the right."""
    steps = max(1, steps)
    pulses = max(0, min(steps, pulses))
    if pulses == 0:
        return [False] * steps
    pat = [((i * pulses) % steps) < pulses for i in range(steps)]
    # Bresenham starts with a hit on 0 and gives the same distribution as Bjorklund (rotated)
    rotation %= steps
    return pat[-rotation:] + pat[:-rotation] if rotation else pat


@dataclass
class LaneSpec:
    lane: int
    steps: int = 16
    pulses: int = 4
    rotation: int = 0
    accent_every: int = 0      # every n-th beat accented (0 = none)
    enabled: bool = True


def _len(length: int) -> int:
    """Pattern length always 0..64 – callers asking for more get 64, for less nothing."""
    return max(0, min(NSTEP, int(length)))


def drum_pattern(specs: list[LaneSpec], length: int = 16, vel: int = 100) -> list[Step]:
    """several lanes -> drum grid steps (hit/acc bits)."""
    steps = [Step() for _i in range(NSTEP)]
    length = _len(length)
    for sp in specs:
        if not sp.enabled or not 0 <= sp.lane < 8:
            continue
        pat = euclid(sp.steps, sp.pulses, sp.rotation)
        count = 0
        for i in range(length):
            if pat[i % len(pat)]:
                st = steps[i]
                st.time, st.vel = ST_NOTE, vel
                st.hit |= 1 << sp.lane
                if sp.accent_every and count % sp.accent_every == 0:
                    st.acc |= 1 << sp.lane
                count += 1
    return steps


# ---------------------------------------------------------------------- harmony ---
def scale_notes(root: int, scale: str, lo: int = 0, hi: int = 127) -> list[int]:
    iv = SCALES.get(scale, SCALES["Dur (Ionisch)"])
    return [n for n in range(lo, hi + 1) if (n - root) % 12 in iv]


def parse_roman(sym: str) -> tuple[int, str, int]:
    """'vi' -> (degree 5, 'min', alteration). Upper case major, lower case minor, °/dim, 7, maj7, b/#."""
    s = sym.strip()
    acc = 0
    while s and s[0] in "b#":
        acc += -1 if s[0] == "b" else 1
        s = s[1:]
    i = 0
    while i < len(s) and s[i].upper() in "IV":
        i += 1
    num, rest = s[:i], s[i:]
    if not num or num.upper() not in ROMAN:
        raise ValueError(_("Unknown degree: {symbol}").format(symbol=sym))
    minor = num.islower()
    quality = "min" if minor else "maj"
    rest_l = rest.lower()
    if "°" in rest or "dim" in rest_l or "o" == rest_l[:1]:
        quality = "dim"
    elif "+" in rest or "aug" in rest_l:
        quality = "aug"
    if "maj7" in rest_l:
        quality += "maj7"
    elif "7" in rest:
        quality += "7"
    elif "sus4" in rest_l:
        quality = "sus4"
    return ROMAN[num.upper()], quality, acc


CHORD_SHAPES = {"maj": [0, 4, 7], "min": [0, 3, 7], "dim": [0, 3, 6], "aug": [0, 4, 8], "sus4": [0, 5, 7],
                "maj7": [0, 4, 7, 10], "min7": [0, 3, 7, 10], "dim7": [0, 3, 6, 9], "aug7": [0, 4, 8, 10],
                "majmaj7": [0, 4, 7, 11], "minmaj7": [0, 3, 7, 11], "dimmaj7": [0, 3, 6, 10]}


def chord_from_roman(sym: str, key_root: int, scale: str = "Dur (Ionisch)", octave: int = 4) -> list[int]:
    degree, quality, acc = parse_roman(sym)
    major = SCALES["Dur (Ionisch)"]
    iv = SCALES.get(scale, major)
    base = iv[degree % len(iv)] if len(iv) == 7 else major[degree]
    root = key_root % 12 + base + acc + 12 * (octave + 1)
    shape = CHORD_SHAPES.get(quality, CHORD_SHAPES["maj"])
    if quality == "7" or quality.endswith("7") and quality.startswith("maj") and quality != "majmaj7":
        shape = CHORD_SHAPES["maj7"]
    return [root + x for x in shape]


def voice(chord: list[int], voicing: str = "eng", inversion: int = 0, max_notes: int = MAX_NOTES) -> list[int]:
    notes = sorted(chord)[:max_notes]
    for _i in range(inversion % max(1, len(notes))):
        notes = notes[1:] + [notes[0] + 12]
    if voicing == "weit" and len(notes) >= 3:          # open: middle note one octave up
        notes = [notes[0], notes[2], notes[1] + 12] + notes[3:]
    elif voicing == "drop-2" and len(notes) >= 4:
        n = sorted(notes)
        second = n[-2]
        notes = [second - 12] + [x for x in n if x != second]
    elif voicing == "Grundton tief":
        notes = [notes[0] - 12] + notes[1:]
    return sorted(notes)[:max_notes]


def smooth_inversions(chords: list[list[int]]) -> list[list[int]]:
    """Voice leading: choose each inversion so that as little as possible moves."""
    out = []
    prev = None
    for c in chords:
        best = c
        if prev:
            cands = []
            for inv in range(len(c)):
                for shift in (-12, 0, 12):
                    v = [x + shift for x in voice(c, "eng", inv)]
                    cost = sum(abs(a - b) for a, b in zip(sorted(v), sorted(prev)))
                    cands.append((cost, v))
            best = min(cands)[1]
        out.append(best)
        prev = best
    return out


RHYTHMS = {
    "ganze Akkorde": [1],
    "Halbe": [1, 0, 1, 0],
    "Viertel": [1, 1, 1, 1],
    "Offbeat": [0, 1, 0, 1],
    "Charleston": [1, 0, 0, 1, 0, 0, 0, 0],
    "Reggae-Skank": [0, 0, 1, 0, 0, 0, 1, 0],
}
RHYTHM_LABELS = {"ganze Akkorde": N_("whole chords"), "Halbe": N_("Halves"), "Viertel": N_("Quarters"),
                 "Reggae-Skank": N_("Reggae skank")}
VOICINGS = ["eng", "weit", "drop-2", "Grundton tief"]
VOICING_LABELS = {"eng": N_("close"), "weit": N_("open"), "Grundton tief": N_("low root")}
BASS_STYLES = ["Grundton", "Oktaven", "Achtel-Puls", "Walking"]
BASS_STYLE_LABELS = {"Grundton": N_("root"), "Oktaven": N_("octaves"), "Achtel-Puls": N_("eighth pulse")}


@dataclass
class ChordOptions:
    key: int = 0
    scale: str = "Dur (Ionisch)"
    progression: str = "I V vi IV"
    octave: int = 4
    voicing: str = "eng"
    inversion: int = 0
    smooth: bool = True
    rhythm: str = "ganze Akkorde"
    steps_per_chord: int = 4
    length: int = 16
    vel: int = 100
    sustain: bool = True        # TIE until the next hit


def chord_steps(o: ChordOptions) -> tuple[list[Step], list[str]]:
    syms = o.progression.split()
    chords = [voice(chord_from_roman(s, o.key, o.scale, o.octave), o.voicing, o.inversion) for s in syms]
    if o.smooth:
        chords = smooth_inversions(chords)
    steps = [Step() for _i in range(NSTEP)]
    rhythm = RHYTHMS.get(o.rhythm, [1])
    names = []
    i = 0
    ci = 0
    length = _len(o.length)
    while i < length and syms:
        chord = chords[ci % len(chords)]
        names.append(syms[ci % len(syms)])
        seg = max(1, o.steps_per_chord)
        slots = [rhythm[int(j * len(rhythm) / seg)] for j in range(seg)] if len(rhythm) > 1 else [1] + [0] * (seg - 1)
        for j in range(seg):
            if i + j >= length:
                break
            if slots[j]:
                steps[i + j] = Step(notes=chord[:MAX_NOTES], time=ST_NOTE, vel=o.vel)
            elif o.sustain and any(slots[:j]):
                steps[i + j] = Step(time=ST_TIE)
        i += seg
        ci += 1
    return steps, names


# ---------------------------------------------------------------------- melody ---
ARP_MODES = ["auf", "ab", "auf/ab", "Zufall", "Akkord-Muster 1-3-5-8"]  # i18n: ignore – keys
ARP_MODE_LABELS = {"auf": N_("up"), "ab": N_("down"), "auf/ab": N_("up/down"), "Zufall": N_("random"),  # i18n: ignore – keys
                   "Akkord-Muster 1-3-5-8": N_("chord pattern 1-3-5-8")}


def arp_steps(chord: list[int], mode: str = "auf", length: int = 16, octaves: int = 1,  # i18n: ignore
              gate_tie: bool = False, vel: int = 100, seed: int = 1) -> list[Step]:
    rnd = random.Random(seed)
    pool = sorted(min(127, n + 12 * o) for o in range(max(1, octaves)) for n in chord)
    length = _len(length)
    if not pool:
        return [Step() for _i in range(NSTEP)]
    if mode == "ab":
        seq = pool[::-1]
    elif mode == "auf/ab":  # i18n: ignore
        seq = pool + pool[-2:0:-1]
    elif mode.startswith("Akkord"):
        c = sorted(chord)
        seq = [c[0], c[min(1, len(c) - 1)], c[min(2, len(c) - 1)], min(127, c[0] + 12)]
    else:
        seq = pool
    steps = [Step() for _i in range(NSTEP)]
    for i in range(length):
        n = rnd.choice(pool) if mode == "Zufall" else seq[i % len(seq)]
        steps[i] = Step(notes=[n], time=ST_NOTE, vel=vel)
    return steps


def bass_steps(chords: list[list[int]], steps_per_chord: int = 4, length: int = 16, style: str = "Grundton",
               vel: int = 110, seed: int = 1) -> list[Step]:
    rnd = random.Random(seed)
    steps = [Step() for _i in range(NSTEP)]
    chords = [c for c in chords if c]
    steps_per_chord = max(1, steps_per_chord)
    if not chords:
        return steps
    for i in range(_len(length)):
        c = sorted(chords[(i // steps_per_chord) % len(chords)])
        root = c[0] - 24
        while root > 52:
            root -= 12
        pos = i % steps_per_chord
        if style == "Oktaven":
            n = root + (12 if i % 2 else 0)
        elif style == "Walking":
            n = [root, c[1] - 24 if len(c) > 1 else root + 4, c[2] - 24 if len(c) > 2 else root + 7, root + rnd.choice([-1, 1, 2])][pos % 4]
        elif style == "Achtel-Puls":
            n = root
        else:
            if pos != 0:
                steps[i] = Step(time=ST_TIE)
                continue
            n = root
        steps[i] = Step(notes=[max(0, n)], time=ST_NOTE, vel=vel, accent=pos == 0 and style == "Achtel-Puls")
    return steps


@dataclass
class WalkOptions:
    key: int = 0
    scale: str = "Moll-Pentatonik"
    low: int = 60
    high: int = 79
    max_leap: int = 3          # scale degrees
    rest_prob: float = 0.2
    repeat_prob: float = 0.1
    tie_prob: float = 0.1
    length: int = 16
    vel: int = 100
    seed: int = 1
    start_on_root: bool = True


def random_walk(o: WalkOptions) -> list[Step]:
    rnd = random.Random(o.seed)
    pool = scale_notes(o.key, o.scale, o.low, o.high) or [60]
    idx = 0
    if o.start_on_root:
        roots = [i for i, n in enumerate(pool) if (n - o.key) % 12 == 0]
        idx = roots[0] if roots else 0
    else:
        idx = rnd.randrange(len(pool))
    steps = [Step() for _i in range(NSTEP)]
    last_note = False
    for i in range(_len(o.length)):
        r = rnd.random()
        if i and r < o.rest_prob:
            last_note = False
            continue
        if i and last_note and r < o.rest_prob + o.tie_prob:
            steps[i] = Step(time=ST_TIE)
            continue
        if not (i and rnd.random() < o.repeat_prob):
            idx = max(0, min(len(pool) - 1, idx + rnd.randint(-o.max_leap, o.max_leap)))
        steps[i] = Step(notes=[pool[idx]], time=ST_NOTE, vel=o.vel)
        last_note = True
    return steps


class Markov:
    """Markov chain (order 1 or 2) over pitch intervals and durations from a MIDI track."""

    def __init__(self, order: int = 1):
        self.order = max(1, min(2, order))
        self.table: dict[tuple, list] = {}
        self.starts: list[tuple] = []

    def train(self, pitches: list[int], durs: list[int]):
        seq = list(zip(pitches, durs))
        if len(seq) <= self.order:
            return
        self.starts.append(tuple(seq[:self.order]))
        for i in range(len(seq) - self.order):
            key = tuple(seq[i:i + self.order])
            self.table.setdefault(key, []).append(seq[i + self.order])

    @property
    def trained(self) -> bool:
        return bool(self.table)

    def generate(self, length: int = 16, seed: int = 1, transpose: int = 0, vel: int = 100) -> list[Step]:
        rnd = random.Random(seed)
        steps = [Step() for _i in range(NSTEP)]
        length = _len(length)
        if not self.trained or not length:
            return steps
        state = list(rnd.choice(self.starts))
        out = list(state)
        while sum(max(1, d) for _n, d in out) < length:
            nxt = self.table.get(tuple(state))
            if not nxt:
                state = list(rnd.choice(self.starts))
                out += state
                continue
            item = rnd.choice(nxt)
            out.append(item)
            state = (state + [item])[-self.order:]
        i = 0
        for p, d in out:
            if i >= length:
                break
            steps[i] = Step(notes=[max(0, min(127, p + transpose))], time=ST_NOTE, vel=vel)
            for j in range(1, max(1, d)):
                if i + j < length:
                    steps[i + j] = Step(time=ST_TIE)
            i += max(1, d)
        return steps


def markov_from_midi(path, order: int = 1, steps_per_beat: int = 4) -> Markov:
    from . import midi_import as MI
    tracks, _bpm = MI.read_midi(path)
    m = Markov(order)
    for t in tracks:
        mono = {}
        for n in t.notes:
            k = round(n.start * steps_per_beat)
            if k not in mono or n.pitch > mono[k][0]:
                mono[k] = (n.pitch, max(1, round(n.length * steps_per_beat)))
        keys = sorted(mono)
        m.train([mono[k][0] for k in keys], [min(8, mono[k][1]) for k in keys])
    return m


def call_response(call: list[Step], length: int, key: int = 0, scale: str = "Dur (Ionisch)", seed: int = 1) -> list[Step]:
    """first half = call, second = response: same rhythm, varied notes, ending on the root."""
    rnd = random.Random(seed)
    length = _len(length)
    half = max(1, length // 2)
    steps = [Step() for _i in range(NSTEP)]
    call = list(call) + [Step() for _i in range(NSTEP - len(call))]
    pool = scale_notes(key, scale, 36, 96)
    for i in range(half):
        steps[i] = _copy(call[i])
    last_note = None
    for i in range(half):
        s = _copy(call[i])
        if s.time == ST_NOTE and s.notes and pool:
            n = s.notes[0]
            near = min(range(len(pool)), key=lambda j: abs(pool[j] - n))
            j = max(0, min(len(pool) - 1, near + rnd.choice([-2, -1, 1, 2])))
            s.notes = [pool[j]]
            if half + i < length:
                last_note = half + i
        if half + i < length:
            steps[half + i] = s
    if last_note is not None:
        n = steps[last_note].notes[0]
        roots = [x for x in pool if (x - key) % 12 == 0]
        if roots:
            steps[last_note].notes = [min(roots, key=lambda r: abs(r - n))]
    return steps


# ---------------------------------------------------------------- humanize / variations ---
def _copy(s: Step) -> Step:
    return Step(list(s.notes), s.time, s.accent, s.slide, s.recorded, s.vel, s.hit, s.acc, s.chance)


VEL_CURVES = ["gleich", "Betonung 1 und 3", "Crescendo", "Decrescendo", "Zufall ±"]  # i18n: ignore – keys
VEL_CURVE_LABELS = {"gleich": N_("even"), "Betonung 1 und 3": N_("accent on 1 and 3"), "Zufall ±": N_("random ±")}  # i18n: ignore – keys


def humanize(steps: list[Step], length: int, curve: str = "Zufall ±", amount: int = 12, chance: int = 100,
             chance_offbeats_only: bool = True, seed: int = 1) -> list[Step]:
    rnd = random.Random(seed)
    out = [_copy(s) for s in steps] + [Step() for _i in range(NSTEP - len(steps))]
    length = _len(length)
    for i in range(length):
        s = out[i]
        if s.time != ST_NOTE:
            continue
        v = s.vel or 96
        if curve == "Betonung 1 und 3":  # i18n: ignore
            v += amount if i % 8 == 0 else amount // 2 if i % 4 == 0 else -amount // 2
        elif curve == "Crescendo":
            v += int(amount * (2 * i / max(1, length - 1) - 1))
        elif curve == "Decrescendo":
            v -= int(amount * (2 * i / max(1, length - 1) - 1))
        elif curve == "Zufall ±":
            v += rnd.randint(-amount, amount)
        s.vel = max(1, min(127, v))
        if chance < 100 and (not chance_offbeats_only or i % 2):
            s.chance = chance
    return out


def variation(steps: list[Step], length: int, kind: str, key: int = 0, scale: str = "Chromatisch",
              seed: int = 1) -> list[Step]:
    """B/C variations of a pattern: 'B' = different notes in the last quarter, 'C' = thinned out + octave jumps."""
    rnd = random.Random(seed)
    out = [_copy(s) for s in steps] + [Step() for _i in range(NSTEP - len(steps))]
    length = _len(length)
    pool = scale_notes(key, scale, 0, 127)
    if kind == "B":
        for i in range(length * 3 // 4, length):
            s = out[i]
            if s.time == ST_NOTE and s.notes and pool:
                n = s.notes[0]
                j = min(range(len(pool)), key=lambda k: abs(pool[k] - n))
                s.notes = [pool[max(0, min(len(pool) - 1, j + rnd.choice([-1, 1, 2])))]] + s.notes[1:]
    elif kind == "C":
        for i in range(length):
            s = out[i]
            if s.time == ST_NOTE and i % 2 and rnd.random() < 0.5:
                out[i] = Step()
            elif s.time == ST_NOTE and s.notes and rnd.random() < 0.25:
                s.notes = [min(127, s.notes[0] + 12)] + s.notes[1:]
    return out


def describe(steps: list[Step], length: int) -> str:
    notes = sum(1 for s in steps[:length] if s.time == ST_NOTE and s.notes)
    hits = sum(bin(s.hit).count("1") for s in steps[:length])
    parts = [ngettext("{n} step", "{n} steps", length).format(n=length)]
    if notes:
        parts.append(ngettext("{n} note step", "{n} note steps", notes).format(n=notes))
    if hits:
        parts.append(ngettext("{n} drum hit", "{n} drum hits", hits).format(n=hits))
    return ", ".join(parts)


def label(key: str, labels: dict) -> str:
    """translated display name of a generator key (scale, progression, rhythm, mode …)"""
    return _(labels[key]) if key in labels else key


__all__ = ["euclid", "drum_pattern", "LaneSpec", "chord_steps", "ChordOptions", "arp_steps", "bass_steps",
           "random_walk", "WalkOptions", "Markov", "markov_from_midi", "call_response", "humanize", "variation",
           "SCALES", "PROGRESSIONS", "LANES", "label", "scale_notes", "chord_from_roman", "voice", "describe"]
