# SPDX-License-Identifier: GPL-3.0-only
"""Projekte und Patterns hörbar machen – am liebsten mit den Klängen des FM-1 selbst.

Zwei Wege:

* **Auf dem verbundenen FM-1** (``MidiSequencer``): die Noten gehen als normale MIDI-Noten über USB ans Gerät,
  das sie mit seinen eigenen Klängen spielt – genau so, wie die Spuren gerade eingestellt sind. Melodee/Felucca
  (firmware/src/seq.c ``midi_track``): GLO › SYSTEM › ROUT legt fest, welche vier Kanäle die Spuren 1–4 spielen –
  CH1-4 (Standard; Kanal 1 → Spur 1 …), CH5-8, CH9-12, CH13-16; SEL schickt jeden Kanal an die gewählte Spur.
  Der DRUM-Kanal (Standard 10) geht – nur innerhalb des ROUT-Bereichs – an die erste DRUM-Spur. Spuren 5–8 sind
  reine MIDI-Ausgänge (kein Klang im FM-1) und werden übersprungen. Drum-Lanes kommen als GM-Noten (KICK 36 …),
  die die DRUM-Engine selbst zuordnet. Beim Stoppen gehen für jede gehaltene Note ein Note-Off und je Kanal
  Sustain aus + All Notes Off (CC 64/123) raus – auch während einer laufenden App-Anfrage (force).
  **Nur lesend gegenüber gespeicherten Daten:** es werden nur Noten gesendet, nie Projekte/Presets geschrieben.
  Gelesen werden (mit voller Verbindung) ROUT/DRUM/CLK (GET global 14/13/2) und die Spuren (TRACK 27: Engine,
  Preset, scharf geschaltete Aufnahme). Ist eine Spur zur Aufnahme scharf, wird NICHT gespielt – das FM-1 würde die
  Noten sonst aufnehmen (seq.c ``rec_note_start``) und das Projekt danach von selbst speichern.
* **Im Rechner** (``render_offline``): eine Annäherung. FM6-Spuren mit dem echten 6-Operator-Patch aus dem
  Projekt (fmrender), die anderen Engines mit einfachen Ersatzklängen, Drums synthetisch. Klingt ähnlich, nicht
  exakt – die Oberfläche sagt das dazu.

Zusätzlich kann das FM-1 seine **eigene laufende Musik** abspielen: BANK_SONG (74) op 2 startet den Song des
Geräts (optional ab einem Abschnitt: editor.c ``chain_prepare_from(a[1])``, seit 1.6.0-rc.7), op 3 stoppt
(EDITOR_PROTOCOL.md „Pattern banks“) – ohne Flash-Zugriff.

Melodee ab 1.6.1 (midi_clock.c ``midi_sync_out_block``): steht CLK auf INT, sendet das FM-1 selbst MIDI-Clock (auch
im Stand), Start bei PLAY und Stop bei STOP über USB und Bluetooth; startet der Song ab einem späteren Abschnitt,
statt Start Song Position + Continue (``midi_sync_out_position``). Auf USB/TRS/BLE folgt es und sendet keine Clock.
"""
from __future__ import annotations

import threading
import time
import weakref
from dataclasses import dataclass, field
from typing import Callable

from . import midiexport as ME, projectfmt as PF

ROUTE_NAMES = {0: "CH1-4", 1: "SEL", 2: "CH5-8", 3: "CH9-12", 4: "CH13-16"}
ENGINE_DRUM = 10
ENGINE_FM6 = 12
CMD_GET, CMD_TRACK, CMD_FM6_GET, CMD_BANK_SONG = 2, 27, 68, 74
G_CLOCK, G_DRUM, G_ROUTE = 2, 13, 14
CLOCK_NAMES = {0: "INT", 1: "USB", 2: "TRS", 3: "BLE"}        # params.c N_CLOCK (BLE nur mit Bluetooth-Build)
CLOCK_OUT_FROM = (1, 6, 1, 0, 0)    # ab Melodee 1.6.1 sendet CLK INT Clock/Start/Stop/Song Position (melodee16.version_key)
OFFLINE_LABEL = "Vorhören im Rechner – klingt ähnlich, nicht exakt wie das FM-1"


class PlaybackError(Exception):
    """Abspielen nicht möglich – deutsche Meldung mit Lösung."""


# ------------------------------------------------------------------------------------------------ Gerät lesen ---
@dataclass
class TrackSound:
    index: int
    engine: int
    preset: int
    level: int = 100
    mute: bool = False
    armed: bool = False
    fm6: bytes | None = None

    @property
    def label(self) -> str:
        name = PF.preset_name(self.engine, self.preset)
        return PF.engine_name(self.engine) + (f" „{name}“" if name else "")


@dataclass
class Routing:
    """Wie das FM-1 eingehende MIDI-Noten verteilt (GLO › SYSTEM)."""
    route: int = 0              # G_ROUTE: 0 CH1-4, 1 SEL, 2 CH5-8, 3 CH9-12, 4 CH13-16
    drum_channel: int = 10      # G_DRUM: 1..16, 0 = aus
    clock: int = 0              # G_CLOCK: 0 INT, 1 USB, 2 TRS, 3 BLE
    known: bool = False         # vom Gerät gelesen (sonst Standardwerte angenommen)
    tracks: list[TrackSound] = field(default_factory=list)
    selected: int = 0
    note: str = ""              # warum nicht gelesen

    @property
    def first(self) -> int:
        return (self.route - 1) * 4 if self.route > 1 else 0

    def channel(self, track: int) -> int | None:
        """MIDI-Kanal (0-basiert), auf dem das FM-1 Spur `track` (0-basiert) spielt – None: nicht erreichbar."""
        if track >= PF.NPART or self.route == 1:
            return None
        return self.first + track

    def describe(self) -> str:
        r = ROUTE_NAMES.get(self.route, str(self.route))
        if self.route == 1:
            ch = "jeder Kanal → gewählte Spur"
        else:
            ch = f"Kanal {self.first + 1}–{self.first + 4} → Spur 1–4"
        src = "vom FM-1 gelesen" if self.known else "angenommen (Standard)"
        return f"ROUT {r}: {ch} ({src})"

    def armed_tracks(self) -> list[int]:
        return [t.index for t in self.tracks if t.armed]


def read_routing(link, with_fm6: bool = False, timeout: float = 0.8) -> Routing:
    """ROUT/DRUM/CLK und die Spuren lesen (nur lesend). Geht etwas nicht, bleiben Standardwerte (known=False)."""
    r = Routing()
    try:
        vals = {}
        for gid in (G_ROUTE, G_DRUM, G_CLOCK):
            a = link.request(CMD_GET, [1, gid], timeout=timeout, retries=0)
            if len(a) >= 4 and a[0] == 1 and a[1] == gid:
                vals[gid] = (a[2] | a[3] << 7) - 8192
        if G_ROUTE in vals:
            r.route = max(0, min(4, vals[G_ROUTE]))
            r.drum_channel = max(0, min(16, vals.get(G_DRUM, 10)))
            r.clock = max(0, min(3, vals.get(G_CLOCK, 0)))
            r.known = True
    except Exception as e:  # noqa: BLE001 – ältere Firmware/keine Antwort: Standard annehmen
        r.note = str(e)
    try:
        r.tracks, r.selected = parse_tracks(link.request(CMD_TRACK, [], timeout=timeout, retries=0))
    except Exception as e:  # noqa: BLE001
        r.note = r.note or str(e)
    if with_fm6:
        for t in r.tracks:
            if t.engine == ENGINE_FM6:
                try:
                    a = link.request(CMD_FM6_GET, [0, t.index], timeout=timeout, retries=0)
                    if len(a) >= 3 + 128 and a[2] == 0:
                        t.fm6 = bytes(a[3:3 + 128])
                except Exception:  # noqa: BLE001
                    pass
    return r


def parse_tracks(a) -> tuple[list[TrackSound], int]:
    """Antwort auf TRACK (27): gewählte Spur, NTRK, je Spur Engine, Preset, Pegel v14, stumm, scharf; danach je
    Spur Preset >> 7 (neuere Firmware)."""
    if len(a) < 2:
        return [], 0
    sel, n = a[0], a[1]
    out = []
    pos = 2
    for k in range(n):
        if pos + 6 > len(a):
            break
        eng, pre, lv, mute, armed = a[pos], a[pos + 1], (a[pos + 2] | a[pos + 3] << 7) - 8192, a[pos + 4], a[pos + 5]
        out.append(TrackSound(k, eng, pre, lv, bool(mute), bool(armed)))
        pos += 6
    for t in out:
        if pos < len(a):
            t.preset |= a[pos] << 7
            pos += 1
    return out, sel


def compare_sounds(prj: PF.Project, routing: Routing) -> list[str]:
    """Unterschiede zwischen den Klängen im Projekt und dem, was gerade auf den Spuren des FM-1 eingestellt ist."""
    if not routing.tracks or not prj or not prj.ok:
        return []
    out = []
    for t in prj.used_tracks:
        if t.index >= PF.NPART or t.index >= len(routing.tracks):
            continue
        d = routing.tracks[t.index]
        if d.engine != t.engine or (t.preset < 0xFE and d.preset != t.preset):
            want = t.engine_name + (f" „{t.preset_name}“" if t.preset_name else "")
            out.append(f"Spur {t.index + 1}: im Projekt {want}, am FM-1 gerade {d.label}")
    return out


# ------------------------------------------------------------------------------------------------ Planen ---
@dataclass
class Plan:
    events: list[ME.NoteEvent]
    skipped: list[str] = field(default_factory=list)      # deutsche Hinweise
    channels: set[int] = field(default_factory=set)
    loop_quarters: float | None = None


def plan_for_device(events: list[ME.NoteEvent], routing: Routing, prj: PF.Project | None = None,
                    track_override: int | None = None) -> Plan:
    """Kanäle so setzen, dass das FM-1 jede Note auf der richtigen Spur spielt.
    track_override: alle Noten auf diese Spur (Komponieren: „Ziel: Spur k“)."""
    out, skipped, chans = [], [], set()
    drum_tracks = {t.index for t in (prj.tracks if prj else []) if t.engine == ENGINE_DRUM}
    if routing.tracks:
        drum_tracks = {t.index for t in routing.tracks if t.engine == ENGINE_DRUM} or drum_tracks
    first_drum = min(drum_tracks) if drum_tracks else None
    dch = routing.drum_channel - 1 if routing.drum_channel else None
    gone: set[int] = set()
    for e in events:
        k = track_override if track_override is not None else e.track
        if routing.route == 1:                       # SEL: alles geht an die gewählte Spur
            ch = 0
        else:
            ch = routing.channel(k)
        if ch is None:
            gone.add(k)
            continue
        if dch is not None and ch == dch and routing.route != 1 and first_drum is not None and k != first_drum:
            gone.add(-(k + 1))                       # Kanal kollidiert mit dem DRUM-Kanal → landete auf der Drum-Spur
            continue
        out.append(ME.NoteEvent(k, e.start, e.length, e.note, e.vel, ch, e.pattern, e.step, e.drum))
        chans.add(ch)
    for k in sorted(gone):
        if k < 0:
            skipped.append(f"Spur {-k}: ihr Kanal ist am FM-1 der DRUM-Kanal ({routing.drum_channel}) – die Noten "
                           "würden auf der Drum-Spur landen und werden deshalb nicht gesendet.")
        elif k >= PF.NPART:
            skipped.append(f"Spur {k + 1} ist eine reine MIDI-Spur (spielt externe Geräte, keinen Klang im FM-1) – "
                           "wird nicht gesendet.")
        else:
            skipped.append(f"Spur {k + 1} ist bei ROUT {ROUTE_NAMES.get(routing.route)} nicht erreichbar.")
    if routing.route == 1:
        skipped.append("ROUT steht am FM-1 auf SEL: alle Noten spielen mit dem Klang der gerade gewählten Spur. "
                       "Für getrennte Spuren am FM-1 GLO › SYSTEM › ROUT auf CH1-4 stellen.")
    return Plan(out, skipped, chans)


# ------------------------------------------------------------------------------------------------ Senden ---
_sequencers: "weakref.WeakSet[MidiSequencer]" = weakref.WeakSet()


def stop_all():
    """alle laufenden Wiedergaben anhalten (Beenden, Gerätewechsel) – mit All Notes Off."""
    for s in list(_sequencers):
        try:
            s.stop()
        except Exception:  # noqa: BLE001
            pass


def panic_messages(channels) -> list[list[int]]:
    out = []
    for ch in sorted(set(channels)):
        out += [[0xB0 | ch, 64, 0], [0xB0 | ch, 123, 0]]
    return out


class MidiSequencer:
    """Spielt NoteEvents in Echtzeit über send(msg, force) -> bool. Eigener Thread, driftfrei (Sollzeiten ab Start).

    * Gleiche Note auf gleichem Kanal überlappend: vor dem neuen Anschlag ein Note-Off, das alte Note-Off entfällt
      (sonst würde es die neue Note abschneiden).
    * Note-Offs und das Aufräumen beim Stoppen gehen mit force=True – auch während einer App-Anfrage.
    * Geht ein Note-On nicht raus (Gerät gerade belegt), wird die Note übersprungen, nicht nachgeholt.
    """

    def __init__(self, send: Callable[[list[int], bool], bool]):
        self.send = send
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.held: dict[tuple[int, int], int] = {}
        self.channels: set[int] = set()
        self.running = False
        self.position = 0.0          # Viertel
        self.total = 0.0
        self.loops = 0
        self.sent = 0
        self.dropped = 0
        self.error = ""
        self.bpm = 120.0
        self.on_finish: Callable[[], None] | None = None
        _sequencers.add(self)

    def start(self, events: list[ME.NoteEvent], bpm: float, loop_quarters: float | None = None,
              lead_in: float = 0.05):
        self.stop()
        evs = sorted(events, key=lambda e: (e.start, e.track, e.note))
        self.bpm = max(20.0, min(300.0, float(bpm or 120)))
        spq = 60.0 / self.bpm
        msgs = []                                         # (Zeit s, Reihenfolge, art, kanal, note, anschlag)
        for e in evs:
            msgs.append((e.start * spq, 1, "on", e.channel, e.note, max(1, min(127, e.vel))))
            msgs.append(((e.start + max(0.01, e.length)) * spq, 0, "off", e.channel, e.note, 0))
        msgs.sort(key=lambda m: (m[0], m[1]))
        end_q = max([e.start + e.length for e in evs], default=0.0)
        self.total = loop_quarters or end_q
        self.channels = {e.channel for e in evs}
        self.held = {}
        self.sent = self.dropped = self.loops = 0
        self.position = 0.0
        self.error = ""
        self._stop.clear()
        self.running = True
        loop_secs = (loop_quarters * spq) if loop_quarters else None
        self._thread = threading.Thread(target=self._run, args=(msgs, loop_secs, end_q * spq, lead_in),
                                        name="fm1c-playback", daemon=True)
        self._thread.start()

    # -- Thread
    def _out(self, msg, force=False) -> bool:
        try:
            ok = bool(self.send(msg, force))
        except Exception as e:  # noqa: BLE001 – Port weg: merken, weiterlaufen bis Stopp
            self.error = str(e) or "Senden fehlgeschlagen"
            ok = False
        if ok:
            self.sent += 1
        return ok

    def _run(self, msgs, loop_secs, end_secs, lead_in):
        pc = time.perf_counter
        t0 = pc() + lead_in
        gen = 0
        try:
            while not self._stop.is_set():
                pending: dict[tuple[int, int], int] = {}
                for t, _o, kind, ch, note, vel in msgs:
                    target = t0 + t
                    while not self._stop.is_set():
                        now = pc()
                        self.position = max(0.0, (now - t0) / (60.0 / self.bpm))
                        left = target - now
                        if left <= 0:
                            break
                        if left > 0.004:
                            self._stop.wait(min(left - 0.002, 0.02))
                    if self._stop.is_set():
                        return
                    key = (ch, note)
                    with self._lock:
                        if kind == "on":
                            if key in self.held:              # überlappend: alte Note beenden, ihr Off entfällt
                                self._out([0x80 | ch, note, 0], True)
                                del self.held[key]
                            gen += 1
                            if self._out([0x90 | ch, note, vel]):
                                self.held[key] = gen
                                pending[key] = gen
                            else:
                                self.dropped += 1
                        else:
                            g = pending.get(key)
                            if g is not None and self.held.get(key) == g:
                                self._out([0x80 | ch, note, 0], True)
                                del self.held[key]
                            pending.pop(key, None)
                if loop_secs is None:
                    # Ausklingen bis zum Ende abwarten (letzte Offs sind schon gesendet)
                    rest = t0 + end_secs - pc()
                    if rest > 0:
                        self._stop.wait(rest)
                    break
                self.loops += 1
                t0 += loop_secs
        finally:
            self._cleanup()
            self.running = False
            cb = self.on_finish
            if cb and not self._stop.is_set():
                try:
                    cb()
                except Exception:  # noqa: BLE001
                    pass

    def _cleanup(self):
        with self._lock:
            for (ch, note) in list(self.held):
                self._out([0x80 | ch, note, 0], True)
            self.held.clear()
            for m in panic_messages(self.channels):
                self._out(m, True)

    def stop(self, wait: float = 1.0):
        """anhalten; danach sind alle Noten aus (Note-Off + CC 64/123 je benutztem Kanal)."""
        self._stop.set()
        t = self._thread
        if t and t.is_alive() and t is not threading.current_thread():
            t.join(wait)
        self._thread = None
        if self.held:                                    # Thread hing? trotzdem aufräumen
            self._cleanup()
        self.running = False


def link_sender(link) -> Callable[[list[int], bool], bool]:
    """send(msg, force) für einen transport.Link (Noten fallen während App-Anfragen weg, Offs nicht)."""
    def send(msg, force=False):
        if link is None or link.closed:
            return False
        return link.send_midi(list(msg), force=force)
    return send


def song_start(link, row: int = 0) -> tuple[int, int]:
    """den Song des FM-1 selbst starten (BANK_SONG op 2), ab Abschnitt `row` (0-basiert). -> (rc, Zeilen).
    rc 0 = läuft, 1 = kein Song / Abschnitt gibt es nicht, 2 = läuft schon (erst stoppen), 67 = kein Klangspeicher."""
    a = link.request(CMD_BANK_SONG, [2, row] if row else [2], timeout=1.5, retries=0)
    return (a[1] if len(a) > 1 else 1), (a[2] if len(a) > 2 else 0)


def read_clock(link, timeout: float = 0.8) -> int | None:
    """GLO › CLK des Geräts (GET global 2): 0 INT, 1 USB, 2 TRS, 3 BLE – None, wenn nicht lesbar."""
    try:
        a = link.request(CMD_GET, [1, G_CLOCK], timeout=timeout, retries=0)
    except Exception:  # noqa: BLE001
        return None
    if len(a) >= 4 and a[0] == 1 and a[1] == G_CLOCK:
        return max(0, min(3, (a[2] | a[3] << 7) - 8192))
    return None


def sends_clock(version: str) -> bool:
    """sendet diese Firmware mit CLK INT selbst MIDI-Clock und Transport (Melodee ab 1.6.1)?"""
    from . import melodee16 as m16
    k = m16.version_key(version)
    return m16.is_melodee(version) and k is not None and k >= CLOCK_OUT_FROM


def song_sync_note(version: str, clock: int | None, row: int = 0) -> str:
    """was mitlaufende Geräte beim Song-Start am FM-1 bekommen (für die Statuszeile; '' = nichts zu sagen)."""
    if not sends_clock(version):
        return ""
    if clock is None:
        return "Steht am FM-1 CLK auf INT, gibt es den Takt an mitlaufende Geräte weiter (USB, Bluetooth)."
    if clock != 0:
        return (f"CLK steht auf {CLOCK_NAMES.get(clock, clock)}: das FM-1 folgt einer fremden Clock und sendet "
                "selbst keine.")
    if row:
        return ("CLK INT: mitlaufende Geräte bekommen Song Position und Continue und steigen an derselben Stelle "
                "ein (Song Position hat keinen Vorlauf – Geräte, die erst suchen müssen, verpassen evtl. die ersten "
                "Pulse).")
    return "CLK INT: mitlaufende Geräte bekommen Start und den Takt des FM-1."


CLOCK_CONFLICT = ("Melodee ab 1.6.1 sendet mit CLK INT selbst MIDI-Clock. Soll die App den Takt vorgeben, am FM-1 "
                  "GLO › GLOBAL › CLK auf USB stellen – auf INT hört es nicht auf die App und es laufen zwei Takte.")


def transport_stop(link) -> bool:
    """das FM-1 stoppen (BANK_SONG op 3 = Transport-Stopp, wie die STOP-Taste)."""
    try:
        a = link.request(CMD_BANK_SONG, [3], timeout=1.5, retries=0)
        return len(a) > 1 and a[1] == 0
    except Exception:  # noqa: BLE001
        return False


# ------------------------------------------------------------------------------------------------ Im Rechner ---
def _engine_kind(engine: int) -> str:
    name = PF.engine_name(engine).upper()
    for key in ("PROPHET", "FM6", "CZ", "SID", "DRUM", "NOISE", "PHASE", "VOICE"):
        if key in name:
            return key
    return "SYNTH"


def _voice_from_patch(patch: bytes | None):
    if not patch or len(patch) < 128 or any(b > 127 for b in patch[:128]) or patch[110] > 31:
        return None
    try:
        from . import dx7
        return dx7.unpack(list(patch[:128]))
    except Exception:  # noqa: BLE001
        return None


def _drum(np, note: int, sr: int, rng):
    n = int(0.35 * sr)
    t = np.arange(n) / sr
    if note in (35, 36):                                   # Kick
        return np.sin(2 * np.pi * (48 + 110 * np.exp(-t * 28)) * t) * np.exp(-t * 9) * 1.6
    if note in (38, 40):                                   # Snare
        return (rng.standard_normal(n) * 0.7 * np.exp(-t * 20) + np.sin(2 * np.pi * 185 * t) * np.exp(-t * 30))
    if note in (39,):                                      # Clap
        env = sum(np.exp(-np.clip(t - d, 0, None) * 60) * (t >= d) for d in (0, 0.01, 0.02)) * np.exp(-t * 12)
        return rng.standard_normal(n) * env * 0.6
    if note in (42, 44, 46, 49, 51, 56):                   # Becken/Hats/Glocke
        hp = rng.standard_normal(n)
        hp = np.diff(hp, prepend=0.0)
        dec = 9 if note in (46, 49) else (14 if note in (51, 56) else 45)
        sig = hp * np.exp(-t * dec) * 0.35
        if note == 56:
            sig += (np.sign(np.sin(2 * np.pi * 540 * t)) + np.sign(np.sin(2 * np.pi * 800 * t))) * 0.15 * np.exp(-t * 10)
        return sig
    if note in (37,):                                      # Rim
        return np.sin(2 * np.pi * 1700 * t) * np.exp(-t * 90) * 0.8
    f = 80 + (note - 40) * 9                               # Toms/Congas
    return np.sin(2 * np.pi * f * (1 + 0.3 * np.exp(-t * 20)) * t) * np.exp(-t * 11)


def _tone(np, kind: str, note: int, dur: float, sr: int, rng):
    rel = 0.25 if kind in ("PROPHET", "CZ", "PHASE", "VOICE") else 0.12
    n = int((dur + rel) * sr)
    t = np.arange(n) / sr
    f = 440.0 * 2 ** ((note - 69) / 12)
    if kind == "PROPHET":                                  # zwei leicht verstimmte Sägezähne, weich gefiltert
        s = (2 * ((t * f) % 1.0) - 1) + (2 * ((t * f * 1.006) % 1.0) - 1)
        kern = np.ones(6) / 6                              # weich: gleitender Mittelwert statt Filterschleife
        y = np.convolve(s, kern, mode="same")
        s = y * 0.6
        att = 0.01
    elif kind == "CZ":                                     # Phasenverzerrung (Resonanz-artig)
        ph = (t * f) % 1.0
        d = 0.15
        pd = np.where(ph < d, ph / d * 0.5, 0.5 + (ph - d) / (1 - d) * 0.5)
        s = np.cos(2 * np.pi * pd) * 0.8
        att = 0.005
    elif kind == "SID":                                    # Puls 40 %
        s = np.where((t * f) % 1.0 < 0.4, 0.5, -0.5)
        att = 0.003
    elif kind == "NOISE":
        s = rng.standard_normal(n) * 0.3 + np.sin(2 * np.pi * f * t) * 0.3
        att = 0.01
    else:                                                  # PHASE/VOICE/sonst: Sinus mit etwas Oberton
        s = np.sin(2 * np.pi * f * t) * 0.7 + np.sin(4 * np.pi * f * t) * 0.15
        att = 0.008
    env = np.minimum(1.0, t / att) * np.where(t < dur, 1.0 - 0.25 * np.minimum(1.0, t / max(dur, 0.05)),
                                              0.75 * np.exp(-(t - dur) / max(0.02, rel / 4)))
    return s * env


def render_offline(events: list[ME.NoteEvent], bpm: float, prj: PF.Project | None = None, sr: int = 22050,
                   max_secs: float = 90.0, engines: dict[int, int] | None = None,
                   patches: dict[int, bytes] | None = None, progress=None):
    """NoteEvents -> float32-Mono (−1..1). Klang je Spur nach ihrer Engine (aus dem Projekt oder `engines`),
    FM6 mit dem Patch der Spur. Annäherung, nicht der Klang des FM-1."""
    import numpy as np
    spq = 60.0 / max(20.0, float(bpm or 120))
    if not events:
        return np.zeros(sr // 4, dtype=np.float32)
    end = min(max_secs, max((e.start + e.length) * spq for e in events) + 0.6)
    out = np.zeros(int(end * sr) + 1, dtype=np.float32)
    rng = np.random.default_rng(1)
    tracks = {t.index: t for t in (prj.tracks if prj else [])}
    cache: dict = {}
    voices: dict[int, object] = {}
    for k, t in tracks.items():
        patch = (patches or {}).get(k) or getattr(t, "fm6", None)
        if t.engine == ENGINE_FM6:
            voices[k] = _voice_from_patch(patch)
    for k, patch in (patches or {}).items():
        if k not in voices:
            voices[k] = _voice_from_patch(patch)
    fm_budget = [12.0]                                     # Sekunden Rechenzeit für FM6, danach Ersatzklang
    n_ev = len(events)
    for i, e in enumerate(events):
        t0 = int(e.start * spq * sr)
        if t0 >= len(out):
            continue
        tr = tracks.get(e.track)
        eng = (engines or {}).get(e.track, tr.engine if tr else -1)
        level = (tr.level / 100) if tr else 0.8
        if tr is not None and tr.mute:
            level *= 0.0
        amp = 0.22 * (e.vel / 127) * max(0.0, min(1.2, level))
        if not amp:
            continue
        kind = "DRUM" if (e.drum or eng == ENGINE_DRUM) else _engine_kind(eng) if eng >= 0 else "SYNTH"
        dur = max(0.03, e.length * spq)
        key = (e.track, kind, e.note, round(dur, 2), e.vel // 16)
        sig = cache.get(key)
        if sig is None:
            if kind == "DRUM":
                sig = _drum(np, e.note, sr, rng)
            elif kind == "FM6" and voices.get(e.track) is not None and fm_budget[0] > 0:
                from . import fmrender
                t_start = time.perf_counter()
                try:
                    sig = fmrender.render(voices[e.track], e.note, e.vel, seconds=dur + 0.3, hold=dur, sr=sr,
                                          normalize=0.7)
                except Exception:  # noqa: BLE001 – kaputter Patch: Ersatzklang
                    sig = _tone(np, "SYNTH", e.note, dur, sr, rng)
                fm_budget[0] -= time.perf_counter() - t_start
            else:
                sig = _tone(np, kind if kind != "FM6" else "SYNTH", e.note, dur, sr, rng)
            sig = np.asarray(sig, dtype=np.float32)
            cache[key] = sig
        seg = out[t0:t0 + len(sig)]
        seg += sig[:len(seg)] * amp
        if progress and i % 32 == 0:
            progress(i, n_ev)
    peak = float(np.max(np.abs(out))) or 1.0
    if peak > 0.95:
        out *= 0.95 / peak
    return out


def to_int16(x) -> list[int]:
    import numpy as np
    return (np.clip(np.asarray(x, dtype=np.float32), -1, 1) * 32767).astype(np.int16).tolist()
