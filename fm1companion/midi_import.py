# SPDX-License-Identifier: GPL-3.0-only
"""Werkstatt: eine MIDI-Datei (aus einer DAW, MuseScore, LMMS …) als Pattern auf
das FM-1 spielen.

Eine Spur der MIDI-Datei wird auf das Step-Raster gelegt (1/16 Standard), bis
zu 4 gleichzeitige Noten werden zum Akkord, gehaltene Noten zu TIE-Steps. Das
Ergebnis landet über TRACK_STEP (Befehl 30) in einem Pattern einer FM-1-Spur.

Grenzen ehrlich: maximal 64 Steps je Pattern, 4 Noten je Step, keine Automation;
Schlagzeug (GM-Kanal 10) wird auf die Drum-Lanes gelegt (Abschnitt „Suite 2.0“ unten).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .i18n import _, decimal, ngettext
from .protocol import Cmd, Step, parse_desc, parse_info, parse_pattern, r14, v14, ProtocolError

NSTEP = 64


@dataclass
class Note:
    start: float      # in Vierteln
    length: float
    pitch: int
    vel: int


@dataclass
class MidiTrackInfo:
    index: int
    name: str
    notes: list[Note] = field(default_factory=list)
    channel: int | None = None

    @property
    def summary(self) -> str:
        if not self.notes:
            return _("no notes")
        lo, hi = min(n.pitch for n in self.notes), max(n.pitch for n in self.notes)
        bars = max(n.start + n.length for n in self.notes) / 4
        return _("{n} notes, range {low}–{high}, about {bars} bars").format(
            n=len(self.notes), low=lo, high=hi, bars=decimal(bars, 1))


def read_midi(path: Path, progress=None) -> tuple[list[MidiTrackInfo], float]:
    """-> (Spuren mit Noten, Tempo in BPM). Eine Spur, die auf mehreren Kanälen spielt (typisch bei Format-0-
    Dateien: alles in einer Spur), wird je Kanal zu einem Eintrag – sonst landete das Schlagzeug (Kanal 10)
    mit dem Bass in einem Pattern. progress(spur, spuren) meldet den Fortschritt bei großen Dateien."""
    import mido
    mf = mido.MidiFile(str(path))
    tpb = mf.ticks_per_beat or 480
    bpm = 120.0
    out = []
    for ti, tr in enumerate(mf.tracks):
        if progress:
            progress(ti, len(mf.tracks))
        t = 0
        on: dict[tuple[int, int], tuple[int, int]] = {}
        name = tr.name or _("Track {n}").format(n=ti + 1)
        per_ch: dict[int, MidiTrackInfo] = {}
        for msg in tr:
            t += msg.time
            if msg.type == "set_tempo" and bpm == 120.0:
                bpm = mido.tempo2bpm(msg.tempo)
            if msg.type in ("note_off", "note_on"):
                k = (msg.channel, msg.note)
                is_on = msg.type == "note_on" and msg.velocity > 0
                if is_on and k in on and on[k][0] == t:
                    continue                    # doppeltes Note-an im selben Tick: eine Note
                if k in on:                     # Note aus – oder dieselbe Taste erneut an: die alte Note endet hier
                    st, v = on.pop(k)
                    info = per_ch.get(msg.channel)
                    if info is None:
                        info = per_ch[msg.channel] = MidiTrackInfo(ti, name, channel=msg.channel)
                    info.notes.append(Note(st / tpb, max(1, t - st) / tpb, msg.note, v))
                if is_on:
                    on[k] = (t, msg.velocity)
        for _ch, info in sorted(per_ch.items()):          # Spur-Name bleibt; die Liste zeigt den Kanal dazu
            info.notes.sort(key=lambda n: (n.start, n.pitch))
            out.append(info)
    return out, bpm


@dataclass
class Options:
    steps_per_beat: int = 4       # 4 = Sechzehntel
    start_beat: float = 0.0       # ab welchem Viertel der MIDI-Datei
    length: int = 16              # Anzahl Steps (1..64)
    transpose: int = 0
    velocity: str = "original"    # "original", "fest"
    fixed_vel: int = 100
    accent_from: int = 112        # ab dieser Anschlagstärke ein Akzent


def to_steps(notes: list[Note], o: Options) -> list[Step]:
    steps = [Step() for _i in range(NSTEP)]
    held_until = [-1] * NSTEP
    grid = 1.0 / o.steps_per_beat
    for n in notes:
        pos = (n.start - o.start_beat) / grid
        i = int(round(pos))
        if i < 0 or i >= o.length:
            continue
        st = steps[i]
        pitch = max(0, min(127, n.pitch + o.transpose))
        if st.time != 0:
            st.notes, st.time = [], 0
        if len(st.notes) < 4 and pitch not in st.notes:
            st.notes.append(pitch)
        vel = n.vel if o.velocity == "original" else o.fixed_vel
        st.vel = max(st.vel if len(st.notes) > 1 else 0, vel)
        if vel >= o.accent_from:
            st.flags |= 1
        end = i + max(1, int(round(n.length / grid)))
        held_until[i] = max(held_until[i], end)
    # Haltedauer als TIE, solange kein neuer Step beginnt
    for i in range(o.length):
        if steps[i].time == 0:
            for k in range(i + 1, min(held_until[i], o.length)):
                if steps[k].time == 0:
                    break
                steps[k].time = 1
    for i in range(o.length, NSTEP):
        steps[i] = Step()
    return steps


def find_param(link, label_options=("LEN", "LENGTH", "STEPS")) -> int | None:
    info = parse_info(link.request(Cmd.INFO, []))
    for pid in range(min(info.p_count or 128, 128)):
        try:
            d = parse_desc(link.request(Cmd.DESC, [0, pid], timeout=0.8, retries=0))
        except Exception:
            continue
        if d.label.upper() in label_options:
            return pid
    return None


def write_pattern(link, track: int, pattern: int | None, steps: list[Step], length: int,
                  progress=None) -> str:
    """Steps in ein Pattern schreiben. pattern None = das aktuelle Pattern der Spur."""
    note = ""
    if pattern is not None:
        try:
            ps = parse_pattern(link.request(Cmd.PATTERN, [track, 1, pattern], timeout=1.5))
            if ps.rc:
                raise ProtocolError(_("Pattern change refused – please stop SONG/playback"))
            if ps.queued != 127:
                raise ProtocolError(_("The FM-1 is playing – please stop it so the pattern changes right away"))
        except ProtocolError:
            raise
        except Exception:
            note = _("This firmware has no pattern banks – the current pattern was written.")
    for i, st in enumerate(steps):
        a = link.request(Cmd.TRACK_STEP, [track, i, *st.encode()], timeout=1.0, retries=1)
        if a[0] != track or a[1] != i:
            raise ProtocolError(_("unexpected answer while writing the steps"))
        if progress:
            progress(i + 1, len(steps))
    pid = find_param(link)
    if pid is not None:
        link.request(Cmd.TRACK_PARAM, [track, pid, *v14(length)], timeout=1.0)
    else:
        note = (note + " " if note else "") + _("Please set the pattern length on the FM-1.")
    return note


# =============================================================== Suite 2.0: Musik-Bereich ===
# Steps als projectfmt.Step (mit Drum-Lanes und Chance), Lesen vor dem Schreiben (Rückgängig),
# Nachlesen zur Kontrolle, Drum-Lanes aus MIDI, lange Dateien in mehrere Patterns.
from . import projectfmt as PF   # noqa: E402

P_SLEN = 29
P_SSWING = 31

# GM-Drumnoten -> Melodee-Lane (0 KICK … 7 BELL)
GM_TO_LANE = {35: 0, 36: 0, 37: 6, 38: 1, 40: 1, 39: 2, 42: 3, 44: 3, 46: 4, 41: 5, 43: 5, 45: 5, 47: 5, 48: 5,
              50: 5, 49: 7, 51: 7, 52: 7, 53: 7, 55: 7, 56: 7, 57: 7, 59: 7, 54: 7, 75: 6, 76: 6, 77: 6}


def wire_step(s: "PF.Step") -> list[int]:
    """12-Byte-Step (v7) für TRACK_STEP: n, 4 Noten, time, flags, vel, hit, acc, hohe Bits, chance."""
    notes = (list(s.notes[:4]) + [0, 0, 0, 0])[:4]
    n = len(s.notes[:4]) if s.time == PF.ST_NOTE else 0
    acc = s.acc & s.hit if s.hit else 0
    return [n, *[x & 127 for x in notes], s.time if s.time in (0, 1, 2) else 2, s.flags & 3, s.vel & 127,
            s.hit & 127, acc & 127, (s.hit >> 7) | (acc >> 7) << 1, max(0, min(100, s.chance))]


def unwire_step(a) -> "PF.Step":
    n = min(4, a[0])
    hit = a[8] | (a[10] & 1) << 7 if len(a) >= 11 else 0
    acc = a[9] | (a[10] & 2) << 6 if len(a) >= 11 else 0
    return PF.Step(notes=list(a[1:5])[:n] if a[5] == PF.ST_NOTE else [], time=a[5], accent=bool(a[6] & 1),
                   slide=bool(a[6] & 2), vel=a[7], hit=hit, acc=acc, chance=a[11] if len(a) >= 12 else 100)


def same_step(a: "PF.Step", b: "PF.Step") -> bool:
    return wire_step(a) == wire_step(b)


@dataclass
class PatternSnapshot:
    """was vor dem Schreiben auf dem FM-1 stand (für Rückgängig)."""
    track: int
    pattern: int | None
    steps: list
    length: int
    previous_active: int | None


def _select(link, track: int, pattern: int | None) -> tuple[int | None, str]:
    if pattern is None:
        return None, ""
    try:
        ps = parse_pattern(link.request(Cmd.PATTERN, [track, 0], timeout=1.5))
        prev = ps.active
        if pattern >= ps.count:
            raise ProtocolError(ngettext("This track only has {n} pattern", "This track only has {n} patterns",
                                             ps.count).format(n=ps.count))
        ps = parse_pattern(link.request(Cmd.PATTERN, [track, 1, pattern], timeout=1.5))
    except ProtocolError:
        raise
    except Exception:
        return None, _("This firmware has no pattern banks – the current pattern was used.")
    if ps.rc:
        raise ProtocolError(_("Pattern change refused – please stop SONG/playback"))
    if ps.queued != 127:
        raise ProtocolError(_("The FM-1 is playing – please stop it so the pattern changes right away"))
    return prev, ""


def _len_param(link) -> int | None:
    try:
        if parse_desc(link.request(Cmd.DESC, [0, P_SLEN], timeout=0.8, retries=0)).label.upper() in ("LEN", "LENGTH"):
            return P_SLEN
    except Exception:  # noqa: BLE001
        pass
    return find_param(link)


def read_device_pattern(link, track: int, pattern: int | None, progress=None) -> PatternSnapshot:
    prev, _note = _select(link, track, pattern)
    steps = []
    for i in range(NSTEP):
        a = link.request(Cmd.TRACK_STEP, [track, i], timeout=1.0, retries=1)
        if a[0] != track or a[1] != i:
            raise ProtocolError(_("unexpected answer while reading the steps"))
        steps.append(unwire_step(a[2:]))
        if progress:
            progress(i + 1, NSTEP * 2)
    length = 16
    pid = _len_param(link)
    if pid is not None:
        length = r14(link.request(Cmd.TRACK_PARAM, [track, pid], timeout=1.0), 2)
    return PatternSnapshot(track, pattern, steps, length, prev)


def write_device_pattern(link, track: int, pattern: int | None, steps: list, length: int, progress=None,
                         verify: bool = True, swing: int | None = None) -> str:
    """schreibt 64 Steps + LEN, liest zur Kontrolle nach. -> Hinweistext ("" = alles gut)."""
    _prev, note = _select(link, track, pattern)
    steps = list(steps[:NSTEP]) + [PF.Step() for _i in range(NSTEP - len(steps))]
    for i, st in enumerate(steps):
        a = link.request(Cmd.TRACK_STEP, [track, i, *wire_step(st)], timeout=1.0, retries=1)
        if a[0] != track or a[1] != i:
            raise ProtocolError(_("unexpected answer while writing the steps"))
        if verify and not same_step(unwire_step(a[2:]), st):
            raise ProtocolError(_("Step {step} came back different from what was written").format(step=i + 1))
        if progress:
            progress(i + 1, NSTEP)
    pid = _len_param(link)
    if pid is not None:
        a = link.request(Cmd.TRACK_PARAM, [track, pid, *v14(length)], timeout=1.0)
        if verify and r14(a, 2) != length:
            raise ProtocolError(_("The pattern length was not accepted"))
    else:
        note = (note + " " if note else "") + _("Please set the pattern length on the FM-1.")
    if swing is not None:
        try:
            link.request(Cmd.TRACK_PARAM, [track, P_SSWING, *v14(swing)], timeout=1.0)
        except Exception:  # noqa: BLE001
            note = (note + " " if note else "") + _("Swing could not be set.")
    if verify:
        for i in sorted({0, max(0, min(NSTEP, length) - 1), NSTEP - 1}):    # LEN 0/>64 vom Gerät: kein Index -1/64
            a = link.request(Cmd.TRACK_STEP, [track, i], timeout=1.0, retries=1)
            if not same_step(unwire_step(a[2:]), steps[i]):
                raise ProtocolError(_("Verification read: step {step} does not match").format(step=i + 1))
    return note


def restore_snapshot(link, snap: PatternSnapshot, progress=None) -> str:
    note = write_device_pattern(link, snap.track, snap.pattern, snap.steps, snap.length, progress)
    if snap.previous_active is not None and snap.pattern is not None and snap.previous_active != snap.pattern:
        try:
            link.request(Cmd.PATTERN, [snap.track, 1, snap.previous_active], timeout=1.5)
        except Exception:  # noqa: BLE001
            pass
    return note


@dataclass
class ImportOptions(Options):
    drums: bool = False               # Noten auf Drum-Lanes legen (GM-Belegung)
    lane_map: dict = field(default_factory=lambda: dict(GM_TO_LANE))
    quantize: int = 100               # % Richtung Raster (100 = hart)
    patterns: int = 1                 # in so viele aufeinanderfolgende Patterns aufteilen


@dataclass
class LossReport:
    total: int = 0
    used: int = 0
    outside: int = 0                  # außerhalb des gewählten Bereichs
    chord_overflow: int = 0           # mehr als 4 Noten auf einem Step
    no_lane: int = 0                  # Drum-Noten ohne Lane
    merged: int = 0                   # auf denselben Step gefallen (Doppelte)

    def text(self) -> str:
        if not self.total:
            return _("No notes in the track.")
        parts = [ngettext("{used} of {n} note will be transferred.", "{used} of {n} notes will be transferred.",
                          self.total).format(used=self.used, n=self.total)]
        if self.outside:
            parts.append(ngettext("{n} is outside the range.", "{n} are outside the range.",
                                  self.outside).format(n=self.outside))
        if self.chord_overflow:
            parts.append(ngettext("{n} is dropped (more than 4 notes per step).",
                                  "{n} are dropped (more than 4 notes per step).",
                                  self.chord_overflow).format(n=self.chord_overflow))
        if self.no_lane:
            parts.append(ngettext("{n} drum note has no lane.", "{n} drum notes have no lane.",
                                  self.no_lane).format(n=self.no_lane))
        if self.merged:
            parts.append(ngettext("{n} duplicate note merged.", "{n} duplicate notes merged.",
                                  self.merged).format(n=self.merged))
        return " ".join(parts)


def to_pf_steps(notes: list[Note], o: ImportOptions, chunk: int = 0, report: LossReport | None = None) -> list:
    """MIDI-Noten -> projectfmt.Steps für Pattern `chunk` (bei Aufteilung)."""
    rep = report or LossReport()
    steps = [PF.Step() for _i in range(NSTEP)]
    held_until = [-1] * NSTEP
    grid = 1.0 / o.steps_per_beat
    first = chunk * o.length
    for n in notes:
        pos = (n.start - o.start_beat) / grid
        q = round(pos)
        pos = q if o.quantize >= 100 else pos + (q - pos) * o.quantize / 100
        i = int(round(pos)) - first
        rep.total += chunk == 0
        if i < 0 or i >= o.length:
            if chunk == 0 and not (0 <= int(round(pos)) < o.length * max(1, o.patterns)):
                rep.outside += 1
            continue
        st = steps[i]
        vel = n.vel if o.velocity == "original" else o.fixed_vel
        if o.drums:
            lane = o.lane_map.get(n.pitch)
            if lane is None:
                rep.no_lane += 1
                continue
            if st.hit >> lane & 1:
                rep.merged += 1
            st.time = PF.ST_NOTE
            st.hit |= 1 << lane
            st.vel = max(st.vel, vel)
            if vel >= o.accent_from:
                st.acc |= 1 << lane
            rep.used += 1
            continue
        pitch = max(0, min(127, n.pitch + o.transpose))
        if st.time != PF.ST_NOTE:
            st.notes, st.time = [], PF.ST_NOTE
        if pitch in st.notes:
            rep.merged += 1
            continue
        if len(st.notes) >= 4:
            rep.chord_overflow += 1
            continue
        st.notes.append(pitch)
        st.vel = max(st.vel, vel)
        if vel >= o.accent_from:
            st.accent = True
        rep.used += 1
        held_until[i] = max(held_until[i], i + max(1, int(round(n.length / grid))))
    if not o.drums:
        for i in range(o.length):
            if steps[i].time == PF.ST_NOTE and steps[i].notes:
                for k in range(i + 1, min(held_until[i], o.length)):
                    if steps[k].time == PF.ST_NOTE:
                        break
                    steps[k].time = PF.ST_TIE
    return steps


def import_patterns(notes: list[Note], o: ImportOptions) -> tuple[list[list], LossReport]:
    """Teilt die Noten auf `o.patterns` Patterns auf. Pattern 0 läuft über alle Noten (zählt den Verlustbericht),
    jedes weitere nur über sein Zeitfenster – sonst wächst der Aufwand mit Noten × Patterns (20 000 Noten × 16
    Patterns ≈ 0,5 s auf dem Pi bei jedem Klick auf eine Option)."""
    rep = LossReport()
    pats = [to_pf_steps(notes, o, 0, rep)]
    if o.patterns > 1:
        import bisect
        order = sorted(range(len(notes)), key=lambda k: notes[k].start)
        starts = [notes[k].start for k in order]
        grid = 1.0 / o.steps_per_beat
        for c in range(1, o.patterns):
            # Rasterung verschiebt eine Note um höchstens einen Step – ein Step Rand je Seite reicht
            lo = o.start_beat + (c * o.length - 1) * grid
            hi = o.start_beat + ((c + 1) * o.length + 1) * grid
            idx = sorted(order[bisect.bisect_left(starts, lo):bisect.bisect_right(starts, hi)])   # Original-Reihenfolge
            win = [notes[k] for k in idx]                    # (entscheidet, welche 4 Noten eines Akkords bleiben)
            pats.append(to_pf_steps(win, o, c, rep))
    if rep.total == 0:
        rep.total = len(notes)
    return pats, rep


def patterns_needed(notes: list[Note], o: Options) -> int:
    if not notes:
        return 1
    end = max(n.start for n in notes) - o.start_beat
    return max(1, int(end * o.steps_per_beat // max(1, o.length)) + 1)


def write_song(link, rows: list[tuple[list[int], int]]) -> int:
    """BANK_SONG op 1: Zeilen (Pattern je Spur 1..4, Wiederholung) -> rc."""
    args = [1, len(rows)]
    for banks, rep in rows:
        args += list(banks[:4]) + [rep]
    a = link.request(Cmd.BANK_SONG, args, timeout=2.0)
    return a[1]


@dataclass
class SongSnapshot:
    """BANK_SONG-Stand vor dem Überschreiben (für Rückgängig)."""
    rows: list           # [([Bank je Spur 1..4], Wiederholung), …]


def read_song(link) -> SongSnapshot:
    """BANK_SONG op 0: aktuelle Song-Kette lesen. Antwort: op, rc, Anzahl, läuft, Zeile, Rest, Anzahl × 5 Bytes
    (Version 4 hängt noch das Loop-Byte an). Wirft ProtocolError, wenn die Firmware das nicht kann."""
    a = link.request(Cmd.BANK_SONG, [0], timeout=2.0)
    if len(a) < 6 or a[0] != 0 or a[1] != 0:
        raise ProtocolError(_("Song chain not readable"))
    n = a[2]
    if len(a) < 6 + 5 * n:
        raise ProtocolError(_("incomplete song answer"))
    rows = [(list(a[6 + 5 * i: 10 + 5 * i]), a[10 + 5 * i]) for i in range(n)]
    return SongSnapshot(rows)


def restore_song(link, snap: SongSnapshot) -> int:
    """alte Song-Kette zurückschreiben -> rc (0 = gut)."""
    return write_song(link, snap.rows)
