# SPDX-License-Identifier: GPL-3.0-only
"""Melodee-Projekt -> Standard-MIDI-Datei (Typ 1), CSV und JSON.

Zeitmodell wie im Sequencer der Firmware (seq.c, mix.c, song_chain.c):
* Step-Länge = DIV (1/4 … 4BAR) in Vierteln; SWING (Pattern + global, max. 100) verlängert gerade Steps um
  swing/250 und kürzt ungerade um dasselbe.
* Notenlänge = Step × GATE/128, über TIE-Steps verlängert; SLIDE bindet bis zum nächsten Step.
  Aufgenommene Gates (acc ohne Hits) gelten als acc/255.
* Drum-Lanes spielen ihre GM-Note (KICK 36 … BELL 56); Akzent = 127, sonst Step-Anschlag (0 = 96).
* Song: jeder Abschnitt dauert repeat × Länge des Patterns von Spur 1 (bei REST/HOLD auf Spur 1: ein 4/4-Takt
  je Wiederholung); jede Spur spielt ihr Pattern ab Abschnittsbeginn in Schleife; HOLD setzt die Phase fort.
* Aufgenommene Noten mit Originaltiming ersetzen die Übersichts-Steps (Flag "recorded").
Nicht nachgerechnet: ARP, Akkord-Tasten (CHRD), Strum, Chance (alle Steps werden exportiert), Motion.
"""
from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from pathlib import Path

from . import projectfmt as F
from .i18n import _, pgettext

TPQ = 480


@dataclass
class NoteEvent:
    track: int          # 0-basiert
    start: float        # Viertel
    length: float
    note: int
    vel: int
    channel: int
    pattern: int
    step: int
    drum: bool = False


@dataclass
class Options:
    mode: str = "song"                  # "song", "active" (aktive Patterns), "pattern"
    loops: int = 4                      # Wiederholungen für "active"
    lane_notes: tuple = tuple(F.LANE_GM)
    drum_channel: int | None = None     # None = Kanal der Spur, sonst 0..15 (9 = GM-Drums)
    include_muted: bool = True
    tracks: tuple | None = None         # nur diese Spuren (0-basiert)


def step_times(p: F.Pattern, global_swing: int = 0) -> list[tuple[float, float]]:
    """(Beginn, Länge) jedes Steps 0..LEN-1 in Vierteln, geswingt."""
    base = p.step_quarters
    sw = max(0, min(100, p.swing + global_swing)) * base / 250
    out, t = [], 0.0
    for i in range(max(1, min(64, p.length))):
        ln = base - sw if i & 1 else base + sw
        out.append((t, ln))
        t += ln
    return out


def pattern_quarters(p: F.Pattern, global_swing: int = 0) -> float:
    st = step_times(p, global_swing)
    return st[-1][0] + st[-1][1]


def pattern_events(p: F.Pattern, track: int, channel: int, start: float = 0.0, global_swing: int = 0,
                   o: Options | None = None, until: float | None = None, phase: float = 0.0) -> list[NoteEvent]:
    """eine Schleife des Patterns ab `start` (mit Phase `phase` Viertel bereits gespielt)."""
    o = o or Options()
    times = step_times(p, global_swing)
    n = len(times)
    gate = max(1, p.gate) / 128
    out = []
    rec_steps = {nt.step for nt in p.recorded}
    for i in range(n):
        s = p.steps[i]
        t0, ln = times[i]
        if s.time != F.ST_NOTE or (s.recorded and i in rec_steps):
            continue
        span = ln
        j = i + 1
        while j < n and p.steps[j].time == F.ST_TIE:
            span += times[j][1]
            j += 1
        if s.slide and j < n:
            dur = span
        elif not s.hit and s.acc:
            dur = span - ln + ln * s.acc / 255
        else:
            dur = span - ln + ln * gate
        vel = s.vel or 96
        for note in s.notes:
            out.append(NoteEvent(track, t0, max(0.01, dur), note, vel, channel, p.index, i))
        for lane in range(8):
            if s.hit >> lane & 1:
                ch = channel if o.drum_channel is None else o.drum_channel
                v = 127 if s.acc >> lane & 1 else vel
                out.append(NoteEvent(track, t0, min(ln * 0.5, 0.25), o.lane_notes[lane], v, ch, p.index, i, True))
    for nt in p.recorded:
        if nt.step < n:
            t0, ln = times[nt.step]
            out.append(NoteEvent(track, t0 + nt.onset * ln, max(0.01, nt.duration * p.step_quarters), nt.note,
                                 nt.vel, channel, p.index, nt.step))
    res = []
    for e in out:
        t = e.start - phase
        if t < -1e-9:
            continue
        e.start = start + t
        if until is not None and e.start >= until - 1e-9:
            continue
        if until is not None:
            e.length = min(e.length, until - e.start)
        res.append(e)
    return res


def _looped(p, track, ch, start, end, gsw, o, phase=0.0):
    """das Pattern in Schleife von start bis end; phase: schon gespielte Viertel (HOLD)."""
    ln = pattern_quarters(p, gsw)
    out, n = [], 0
    while ln > 0:
        ls = start - phase + n * ln
        if ls >= end - 1e-9:
            break
        out += [e for e in pattern_events(p, track, ch, ls, gsw, o, until=end) if e.start >= start - 1e-9]
        n += 1
    return out


def section_quarters(prj: F.Project, row: F.SongRow) -> float:
    b0 = row.banks[0] if row.banks else F.CHAIN_REST
    t0 = prj.tracks[0]
    if b0 >= F.NPAT or b0 >= len(t0.patterns):
        return 4.0 * row.repeat
    return pattern_quarters(t0.patterns[b0], prj.swing) * row.repeat


def events(prj: F.Project, o: Options | None = None, pattern: tuple[int, int] | None = None) -> list[NoteEvent]:
    o = o or Options()
    tracks = [t for t in prj.tracks if t.in_use and (o.tracks is None or t.index in o.tracks)
              and (o.include_muted or not t.mute)]
    out: list[NoteEvent] = []
    if pattern is not None:
        k, b = pattern
        t = prj.tracks[k]
        return pattern_events(t.patterns[b], k, t.midi_channel, 0.0, prj.swing, o)
    if o.mode == "song" and prj.song:
        start = 0.0
        phase: dict[int, tuple[int, float]] = {}
        for row in prj.song:
            end = start + section_quarters(prj, row)
            for t in tracks:
                v = row.banks[t.index] if t.index < len(row.banks) else 0
                if v == F.CHAIN_REST:
                    phase.pop(t.index, None)
                    continue
                if v == F.CHAIN_HOLD and t.index in phase:
                    b, ph = phase[t.index]
                else:
                    b, ph = v, 0.0
                if b >= len(t.patterns):
                    continue
                p = t.patterns[b]
                out += _looped(p, t.index, t.midi_channel, start, end, prj.swing, o, ph)
                plen = pattern_quarters(p, prj.swing)
                phase[t.index] = (b, (ph + end - start) % plen if plen else 0.0)
            start = end
    else:
        if not tracks:
            return []
        lens = [pattern_quarters(t.patterns[t.active], prj.swing) for t in tracks if t.active < len(t.patterns)]
        total = (max(lens) if lens else 4.0) * max(1, o.loops)
        for t in tracks:
            if t.active < len(t.patterns) and t.patterns[t.active].used:
                out += _looped(t.patterns[t.active], t.index, t.midi_channel, 0.0, total, prj.swing, o)
    out.sort(key=lambda e: (e.start, e.track, e.note))
    return out


def to_midi(prj: F.Project, o: Options | None = None, pattern: tuple[int, int] | None = None):
    import mido
    evs = events(prj, o, pattern)
    mf = mido.MidiFile(type=1, ticks_per_beat=TPQ)
    meta = mido.MidiTrack()
    meta.append(mido.MetaMessage("track_name", name=prj.name or "FM-1 Projekt", time=0))
    meta.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(max(20, prj.bpm or 120)), time=0))
    meta.append(mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0))
    mf.tracks.append(meta)
    for t in prj.tracks:
        mine = [e for e in evs if e.track == t.index]
        if not mine:
            continue
        tr = mido.MidiTrack()
        label = _("Track {n}").format(n=t.index + 1) + f" {t.engine_name}"
        if t.preset_name:
            label += f" {t.preset_name}"
        if t.mute:
            label += " (stumm)"
        tr.append(mido.MetaMessage("track_name", name=label, time=0))
        msgs = []
        for e in mine:
            on = int(round(e.start * TPQ))
            off = max(on + 1, int(round((e.start + e.length) * TPQ)))
            msgs.append((on, 1, mido.Message("note_on", channel=e.channel, note=e.note, velocity=max(1, min(127, e.vel)))))
            msgs.append((off, 0, mido.Message("note_off", channel=e.channel, note=e.note, velocity=0)))
        msgs.sort(key=lambda x: (x[0], x[1]))
        now = 0
        for tick, _prio, m in msgs:
            tr.append(m.copy(time=tick - now))
            now = tick
        tr.append(mido.MetaMessage("end_of_track", time=0))
        mf.tracks.append(tr)
    return mf


def save_midi(prj: F.Project, path: Path, o: Options | None = None, pattern=None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    to_midi(prj, o, pattern).save(str(path))
    return path


def to_csv(prj: F.Project, o: Options | None = None, pattern=None) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    # Translators: column headers of the CSV export
    w.writerow([_("Track"), "Pattern", "Step", _("Start (quarters)"), _("Length (quarters)"), "Note", "Name",
                _("Velocity"), _("Channel"), "Drum"])
    for e in events(prj, o, pattern):
        w.writerow([e.track + 1, e.pattern + 1, e.step + 1, f"{e.start:.4f}", f"{e.length:.4f}", e.note,
                    F.note_name(e.note), e.vel, e.channel + 1, pgettext("csv", "yes") if e.drum else ""])
    return buf.getvalue()


def to_json(prj: F.Project) -> str:
    return json.dumps(prj.to_dict(), indent=1, ensure_ascii=False)
