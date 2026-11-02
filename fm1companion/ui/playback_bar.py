# SPDX-License-Identifier: GPL-3.0-only
"""Abspielen von Projekten und Patterns – auf dem verbundenen FM-1 (mit seinen eigenen Klängen), im Studio-Zwilling
(dieselbe Firmware im Rechner: identischer Klang) oder – nur wenn es keinen Zwilling gibt – als Annäherung im
Rechner. Gemeinsam für Projekt-Ansicht und Komponieren (fm1companion/playback.py macht die Arbeit).

Dazu ``DeviceBadge``: zeigt unmissverständlich, WELCHES FM-1 gerade aktiv ist (Name und Farbe wie oben in der
Leiste) – wichtig, wenn mehrere angeschlossen sind."""
from __future__ import annotations

import html
from dataclasses import dataclass, field

from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtWidgets import QCheckBox, QComboBox, QLabel, QVBoxLayout, QWidget

from .. import midiexport as ME, playback as P, projectfmt as PF
from .audio import OutputButton, Player
from .common import button, flow_row, run_job

DEVICE, COMPUTER, STUDIO = "device", "computer", "studio"
STUDIO_LABEL = "im Studio (identischer Klang)"
COMPUTER_LABEL = "im Rechner (Annäherung – klingt nur ähnlich)"


def studio_for(ctx):
    """der Studio-Dienst, wenn es einen Zwilling für die Firmware gibt (ohne Gerät: Melodee) – sonst None."""
    win = getattr(ctx, "window", None)
    svc = getattr(win, "studio", None)
    if svc is None or not svc.available():
        return None
    key = getattr(ctx, "firmware_key", "") if getattr(ctx, "midi_connected", False) else "melodee"
    if getattr(ctx, "is_virtual", False):
        return svc
    return svc if svc.firmware_for(key or "melodee") else None


def device_name(ctx) -> str:
    prof = getattr(ctx, "profile", None)
    port = getattr(ctx, "port", None)
    return prof.name if prof else (port.label if port else "")


def device_color(ctx) -> str:
    prof = getattr(ctx, "profile", None)
    return prof.color if prof else "#888888"


def device_badge_html(ctx, verb: str = "Aktives FM-1") -> str:
    """„● Name – Melodee 1.6 – voller Zugriff (+ 1 weiteres angeschlossen)“ mit der Profilfarbe."""
    port = getattr(ctx, "port", None)
    if not port:
        return "<b style='color:#888'>●</b> Kein FM-1 verbunden"
    if getattr(port, "virtual", False):
        verb = "Aktives Gerät"
    col = html.escape(device_color(ctx))
    try:
        title = ctx.title()
    except Exception:  # noqa: BLE001
        title = device_name(ctx)
    try:
        state = ctx.status_text()
    except Exception:  # noqa: BLE001
        state = ""
    others = max(0, len(getattr(ctx, "ports", []) or []) - (0 if getattr(port, "virtual", False) else 1))
    more = (f" <span style='opacity:.75'>· {others} weitere{'s' if others == 1 else ''} FM-1 angeschlossen – "
            "umschalten oben in der Leiste</span>") if others else ""
    return (f"{verb}: <b style='color:{col}'>●</b> <b>{html.escape(title)}</b>"
            + (f" – {html.escape(state)}" if state else "") + more)


class DeviceBadge(QLabel):
    """Zeile „Aktives FM-1: ● Name …“, folgt Gerätewechseln."""

    def __init__(self, ctx, verb: str = "Aktives FM-1", parent=None):
        super().__init__(parent)
        self.ctx, self.verb = ctx, verb
        self.setWordWrap(True)
        self.setProperty("role", "badge")
        for sig in ("changed", "ports_changed"):
            s = getattr(ctx, sig, None)
            if s is not None:
                s.connect(self.refresh)
        self.refresh()

    def refresh(self):
        try:
            self.setText(device_badge_html(self.ctx, self.verb))
        except RuntimeError:
            pass


@dataclass
class Source:
    """Was abgespielt werden soll."""
    events: list
    bpm: float
    title: str = ""
    prj: PF.Project | None = None
    track_override: int | None = None      # alle Noten auf diese FM-1-Spur (Komponieren)
    loop_quarters: float | None = None
    live_tag: tuple | None = None          # die laufende Musik genau dieses Geräts (Klänge passen sicher)
    engines: dict = field(default_factory=dict)


class _Status(QLabel):
    """Statuszeile, die nur Platz braucht, wenn sie etwas zu sagen hat."""

    def setText(self, text):  # noqa: N802 – Qt-Name
        super().setText(text)
        self.setVisible(bool(text))


class PlaybackBar(QWidget):
    """[▶ Abspielen] [■ Stopp]  Ziel: auf dem FM-1 „Name“ / im Rechner   [Schleife]   [Ausgabe …]
    Darunter eine Statuszeile. source() liefert eine Source (oder None)."""
    state_changed = pyqtSignal(bool)

    def __init__(self, ctx, source=None, parent=None, loop: bool = False, play_text: str = "Abspielen",
                 shortcut_hint: str = "", compact: bool = False, lead: QWidget | None = None):
        """compact: der Ruhe-Hinweis steht nur im Tooltip (spart eine Zeile über einer knappen Piano-Roll);
        lead: Widget, das vorne in die Knopfreihe kommt."""
        super().__init__(parent)
        self.ctx = ctx
        self.compact = compact
        self.source = source or (lambda: None)
        self.seq = P.MidiSequencer(lambda m, f=False: False)
        self.player: Player | None = None
        self.mode = ""
        self._gen = 0
        self._link = None
        self._sig = None
        self._label = ""
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        self.b_play = button(play_text, ("media-playback-start",), "primary", self.play,
                             "Auf dem verbundenen FM-1 mit dessen eigenen Klängen abspielen – ohne FM-1 im "
                             "Studio-Zwilling (identischer Klang)" + (f" ({shortcut_hint})" if shortcut_hint else ""))
        self.b_stop = button("Stopp", ("media-playback-stop",), "", self.stop,
                             "Anhalten – alle Noten am FM-1 werden beendet (All Notes Off)")
        self.b_stop.setEnabled(False)
        self.target = QComboBox()
        self.target.setToolTip("Wo es klingt: auf dem FM-1 selbst (seine Klänge, wie gerade eingestellt) oder im "
                               "Studio-Zwilling (dieselbe Firmware im Rechner)")
        self.target.currentIndexChanged.connect(self._target_changed)
        # lange Gerätenamen dürfen die Spalte nicht verbreitern (sonst kippen schmal/breit-Umschalter hin und her)
        self.target.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.target.setMinimumContentsLength(16)
        self.loop = QCheckBox("Schleife")
        self.loop.setChecked(loop)
        self.loop.setToolTip("Immer wieder von vorn, bis „Stopp“")
        self.out_btn = OutputButton(self)
        self.row = flow_row(lead, self.b_play, self.b_stop, QLabel("Ziel:"), self.target, self.loop, self.out_btn)
        v.addWidget(self.row)
        self.status = _Status("")
        self.status.setWordWrap(True)
        self.status.setMinimumWidth(120)
        self.status.setProperty("role", "muted")
        v.addWidget(self.status)
        self.timer = QTimer(self, interval=200)
        self.timer.timeout.connect(self._tick)
        for sig in ("changed", "ports_changed"):
            s = getattr(ctx, sig, None)
            if s is not None:
                s.connect(self._dev_changed)
        self._fill_targets()

    # ------------------------------------------------------------------ Ziel
    def _fill_targets(self):
        cur = self.target.currentData() or DEVICE
        self.target.blockSignals(True)
        self.target.clear()
        name = device_name(self.ctx)
        virtual = getattr(self.ctx, "is_virtual", False)
        if virtual and getattr(self.ctx, "midi_connected", False):
            self.target.addItem(f"im Studio-Zwilling – aktives Gerät (identischer Klang)", DEVICE)
        elif getattr(self.ctx, "midi_connected", False):
            self.target.addItem(f"auf dem FM-1 „{name}“ (seine Klänge)", DEVICE)
        else:
            self.target.addItem("auf dem FM-1 (keins verbunden)", DEVICE)
        if studio_for(self.ctx) is not None:
            if not virtual:
                self.target.addItem(STUDIO_LABEL, STUDIO)
        else:
            self.target.addItem(COMPUTER_LABEL, COMPUTER)
        if cur == COMPUTER and self.target.findData(COMPUTER) < 0:
            cur = STUDIO
        self.target.setCurrentIndex(max(0, self.target.findData(cur)))
        self.target.blockSignals(False)
        self._target_changed()

    def _target_changed(self, *_):
        data = self.target.currentData()
        dev = data == DEVICE and getattr(self.ctx, "midi_connected", False)
        self.out_btn.setVisible(data == COMPUTER or (data == DEVICE and not dev and studio_for(self.ctx) is None))
        if not self.seq.running and not (self.player and self.player.playing):
            if dev and getattr(self.ctx, "is_virtual", False):
                hint = ("Spielt im Studio-Zwilling (aktives Gerät) – dieselbe Firmware wie am FM-1, mit den Klängen, "
                        "die dort gerade auf den Spuren eingestellt sind.")
            elif dev:
                hint = (f"Spielt auf „{html.escape(device_name(self.ctx))}“ mit den Klängen, die dort gerade auf den "
                        "Spuren eingestellt sind. Am FM-1 wird nichts gespeichert.")
            elif data == STUDIO or (data == DEVICE and studio_for(self.ctx) is not None):
                hint = ("Spielt im Studio-Zwilling: dieselbe Firmware wie am FM-1, Ton über den Rechner – mit den "
                        "Klängen, die im Studio gerade auf den Spuren eingestellt sind (Seite „Studio“).")
                if data == DEVICE:
                    hint = "Kein FM-1 verbunden – „Abspielen“ spielt dann im Studio. " + hint
            elif data == DEVICE:
                hint = "Kein FM-1 verbunden – „Abspielen“ spielt dann im Rechner: " + P.OFFLINE_LABEL + "."
            else:
                hint = P.OFFLINE_LABEL + "."
            self.target.setToolTip(hint)
            self.status.setText("" if self.compact else hint)

    def _dev_changed(self):
        if self.seq.running and not getattr(self, "_studio_mode", False) and (self.ctx.link is not self._link):
            self.stop("Wiedergabe beendet – das FM-1 wurde getrennt oder gewechselt.")
        self._fill_targets()

    @property
    def playing(self) -> bool:
        return self.seq.running or bool(self.player and self.player.playing)

    # ------------------------------------------------------------------ Abspielen
    def play(self):
        self.stop(quiet=True)
        try:
            src = self.source()
        except Exception as e:  # noqa: BLE001
            self.status.setText(f"Nicht abspielbar: {e}")
            return
        if not src or not src.events:
            self.status.setText("Nichts abzuspielen – in der Auswahl gibt es keine Noten.")
            return
        want = self.target.currentData()
        want_dev = want == DEVICE
        if want_dev and getattr(self.ctx, "midi_connected", False) and getattr(self.ctx, "protocol", "") != "update":
            self._play_device(src)
        elif want in (STUDIO, DEVICE) and studio_for(self.ctx) is not None and self._play_studio(src, fallback=want_dev):
            pass
        else:
            self._play_computer(src, fallback=want_dev)

    def _play_device(self, src: Source):
        self._gen += 1
        gen = self._gen
        link = self.ctx.link
        name = device_name(self.ctx)
        tag = (getattr(self.ctx.profile, "id", "") if self.ctx.profile else "",
               self.ctx.port.key if self.ctx.port else "")
        self.status.setText(f"Lese die Spuren von „{html.escape(name)}“ …")

        def go(routing: P.Routing):
            if gen != self._gen or self.ctx.link is not link or link is None or link.closed:
                return
            armed = routing.armed_tracks()
            if armed:
                self.status.setText(
                    f"<b>Nicht gestartet:</b> Spur {', '.join(str(k + 1) for k in armed)} ist auf „{html.escape(name)}“ "
                    "zur Aufnahme scharf geschaltet (REC) – das FM-1 würde die Noten aufnehmen und das Projekt "
                    "verändern. Bitte REC am FM-1 ausschalten, dann erneut abspielen.")
                return
            plan = P.plan_for_device(src.events, routing, src.prj, src.track_override)
            if not plan.events:
                self.status.setText("Nichts zu senden: " + " ".join(plan.skipped))
                return
            notes = list(plan.skipped)
            if src.prj is not None and not (src.live_tag and tuple(src.live_tag[:2]) == tag):
                diff = P.compare_sounds(src.prj, routing)
                if diff:
                    notes.append("Klänge weichen ab (es spielt, was am FM-1 eingestellt ist): " + "; ".join(diff[:4]))
            if src.track_override is not None and routing.tracks and src.track_override < len(routing.tracks):
                notes.insert(0, f"Spur {src.track_override + 1} am FM-1: {routing.tracks[src.track_override].label}.")
            self.seq = P.MidiSequencer(P.link_sender(link))
            self._hook(link)
            self.seq.start(plan.events, src.bpm, src.loop_quarters if self.loop.isChecked() else None)
            self.mode = DEVICE
            self._label = (f"Spielt auf <b>„{html.escape(name)}“</b> mit dessen eigenen Klängen · "
                           f"{html.escape(routing.describe())}")
            self._notes = notes
            self._running(True)
            self._tick()

        if getattr(self.ctx, "connected", False):
            def work(w, lk):
                return P.read_routing(lk)
            self.ctx.job(self, "Spuren lesen", work, dialog=False, on_done=go,
                         on_fail=lambda m, tb: go(P.Routing(note=m)))
        else:                                   # nur MIDI (Firmware ohne Editor-Protokoll): Standard annehmen
            go(P.Routing(note="Diese Firmware lässt sich nicht abfragen – Kanäle 1–4 angenommen."))

    def _play_studio(self, src: Source, fallback: bool = False) -> bool:
        """Noten in den Studio-Zwilling (dieselbe Firmware, Ton über den Rechner). False: Zwilling nicht startbar."""
        svc = studio_for(self.ctx)
        try:
            if svc is None or not svc.auto_start():
                return False
        except Exception as e:  # noqa: BLE001
            self.status.setText(f"Studio nicht startbar ({e}) – spiele die Annäherung im Rechner.")
            return False
        self._gen += 1
        gen = self._gen
        link = svc.link
        pre = "Kein FM-1 verbunden – " if fallback else ""
        self.status.setText(pre + "Lese die Spuren des Studio-Zwillings …")
        svc.use(True)
        self._studio_user = svc

        def go(routing: P.Routing):
            if gen != self._gen or link is None or link.closed or svc.st is None:
                self._release_studio()
                return
            armed = routing.armed_tracks()
            if armed:
                with_rec = ", ".join(str(k + 1) for k in armed)
                self.status.setText(f"<b>Nicht gestartet:</b> Spur {with_rec} ist im Studio-Zwilling zur Aufnahme "
                                    "scharf geschaltet (REC) – bitte dort ausschalten (Seite „Studio“).")
                self._release_studio()
                return
            plan = P.plan_for_device(src.events, routing, src.prj, src.track_override)
            if not plan.events:
                self.status.setText("Nichts zu senden: " + " ".join(plan.skipped))
                self._release_studio()
                return
            notes = list(plan.skipped)
            if src.prj is not None:
                diff = P.compare_sounds(src.prj, routing)
                if diff:
                    notes.append("Klänge weichen ab (es spielt, was im Studio eingestellt ist): " + "; ".join(diff[:4]))
            self.seq = P.MidiSequencer(P.link_sender(link))
            self.seq.start(plan.events, src.bpm, src.loop_quarters if self.loop.isChecked() else None)
            self.mode = DEVICE
            self._studio_mode = True
            self._label = (pre + f"Spielt <b>im Studio-Zwilling</b> (Melodee {html.escape(svc.version)}, identischer "
                           f"Klang) · {html.escape(routing.describe())}")
            self._notes = notes
            self._running(True)
            self._tick()

        run_job(self, lambda w: P.read_routing(link), on_done=go, on_fail=lambda m, tb: go(P.Routing(note=m)))
        return True

    def _release_studio(self):
        svc = getattr(self, "_studio_user", None)
        self._studio_user = None
        self._studio_mode = False
        if svc is not None:
            try:
                svc.use(False)
            except Exception:  # noqa: BLE001
                pass

    def _play_computer(self, src: Source, fallback: bool = False):
        self._gen += 1
        gen = self._gen
        loops = 2 if (self.loop.isChecked() and src.loop_quarters) else 1
        evs = list(src.events)
        if loops > 1:
            evs += [ME.NoteEvent(e.track, e.start + src.loop_quarters, e.length, e.note, e.vel, e.channel, e.pattern,
                                 e.step, e.drum) for e in src.events]
        if src.track_override is not None:
            evs = [ME.NoteEvent(src.track_override, e.start, e.length, e.note, e.vel, e.channel, e.pattern, e.step,
                                e.drum) for e in evs]
        pre = ("Kein FM-1 verbunden – " if fallback else "")
        self.status.setText(pre + "Klang wird im Rechner berechnet …")
        self._running(True, computing=True)

        def done(x):
            if gen != self._gen:
                return
            if self.player is None:
                self.player = Player(self)
            self.mode = COMPUTER
            if self.player.play_samples(P.to_int16(x), 22050, status=self.status.setText):
                self._label = pre + "<b>" + P.OFFLINE_LABEL + "</b>"
                self._notes = [] if src.prj is not None or src.engines else \
                    ["Ersatzklang, weil die Spur-Engine unbekannt ist."]
                self._running(True)
                self._tick()
            else:
                self._running(False)

        def fail(msg, _tb):
            if gen == self._gen:
                self._running(False)
                self.status.setText(f"Vorhören nicht möglich: {msg}")
        run_job(self, lambda w: P.render_offline(evs, src.bpm, src.prj, engines=src.engines), on_done=done,
                on_fail=fail)

    # ------------------------------------------------------------------ Stopp
    def stop(self, reason: str = "", quiet: bool = False):
        self._gen += 1                                   # eine laufende Berechnung/Abfrage spielt danach nicht mehr los
        was = self.playing
        self.seq.stop()
        self._unhook()
        if self.player is not None:
            self.player.stop()
        self._release_studio()
        self._running(False)
        if not quiet and (was or reason):
            self.status.setText(reason or ("Gestoppt – alle Noten am FM-1 sind aus." if self.mode == DEVICE
                                           else "Gestoppt."))

    def _hook(self, link):
        """Gerät wird getrennt/gewechselt, während es spielt: vor dem Schließen der Verbindung noch alle Noten
        beenden (sonst hingen sie am FM-1 – das merkt den Wechsel nicht). DeviceContext meldet das über
        ``about_to_disconnect``."""
        self._unhook()
        self._link = link
        sig = getattr(self.ctx, "about_to_disconnect", None)
        if link is not None and sig is not None:
            sig.connect(self._before_disconnect)
            self._sig = sig

    def _before_disconnect(self, link):
        if link is self._link:
            try:
                self.seq.stop(0.5)
            except Exception:  # noqa: BLE001
                pass

    def _unhook(self):
        sig = self._sig
        if sig is not None:
            try:
                sig.disconnect(self._before_disconnect)
            except (TypeError, RuntimeError):
                pass
        self._link, self._sig = None, None

    def _running(self, on: bool, computing: bool = False):
        self.b_stop.setEnabled(on)
        self.b_play.setEnabled(not computing)
        if on:
            self.timer.start()
        else:
            self.timer.stop()
        self.state_changed.emit(on)

    def _tick(self):
        if self.mode == DEVICE:
            s = self.seq
            if not s.running:
                self._running(False)
                self._unhook()
                err = f" ({s.error})" if s.error else ""
                where = "den Studio-Zwilling" if getattr(self, "_studio_mode", False) else \
                    f"„{html.escape(device_name(self.ctx))}“"
                self._release_studio()
                self.status.setText(f"Fertig – {s.sent} MIDI-Nachrichten an {where} gesendet, alle Noten aus{err}.")
                return
            bar = int(s.position // 4) + 1
            total = max(1, int((s.total + 3.999) // 4))
            loop = f", Durchlauf {s.loops + 1}" if self.loop.isChecked() else ""
            drop = f" · {s.dropped} Noten übersprungen (FM-1 gerade belegt)" if s.dropped else ""
            extra = "<br>" + "<br>".join(html.escape(n) for n in self._notes) if getattr(self, "_notes", None) else ""
            self.status.setText(f"{self._label} · Takt {min(bar, total)}/{total}{loop}{drop}{extra}")
        elif self.mode == COMPUTER:
            if self.player is None or not self.player.playing:
                self._running(False)
                return
            extra = "<br>" + "<br>".join(html.escape(n) for n in self._notes) if getattr(self, "_notes", None) else ""
            self.status.setText(self._label + extra)

    def shutdown(self):
        self.stop(quiet=True)
