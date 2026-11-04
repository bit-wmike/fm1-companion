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
from ..i18n import _, ngettext

DEVICE, COMPUTER, STUDIO = "device", "computer", "studio"
STUDIO_LABEL = _("in the studio (identical sound)")
COMPUTER_LABEL = _("on the computer (approximation – only sounds similar)")


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


def device_badge_html(ctx, verb: str | None = None) -> str:
    """„● Name – Melodee 1.6 – voller Zugriff (+ 1 weiteres angeschlossen)“ mit der Profilfarbe."""
    port = getattr(ctx, "port", None)
    if not port:
        return "<b style='color:#888'>●</b> " + _("No FM-1 connected")
    if verb is None:
        verb = _("Active FM-1")
    if getattr(port, "virtual", False):
        verb = _("Active device")
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
    more = (" <span style='opacity:.75'>· " + ngettext(
        "{n} more FM-1 connected – switch at the top of the bar", "{n} more FM-1 connected – switch at the top of the bar",
        others).format(n=others) + "</span>") if others else ""
    return (_("{label}:").format(label=verb) + f" <b style='color:{col}'>●</b> <b>{html.escape(title)}</b>"
            + (f" – {html.escape(state)}" if state else "") + more)


class DeviceBadge(QLabel):
    """Zeile „Aktives FM-1: ● Name …“, folgt Gerätewechseln."""

    def __init__(self, ctx, verb: str | None = None, parent=None):
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

    def __init__(self, ctx, source=None, parent=None, loop: bool = False, play_text: str | None = None,
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
        self.b_play = button(_("Play") if play_text is None else play_text, ("media-playback-start",), "primary",
                             self.play, _("Play on the connected FM-1 with its own sounds – without an FM-1 in the "
                                          "studio twin (identical sound)") + (f" ({shortcut_hint})" if shortcut_hint else ""))
        self.b_stop = button(_("Stop"), ("media-playback-stop",), "", self.stop,
                             _("Stop – all notes on the FM-1 are ended (All Notes Off)"))
        self.b_stop.setEnabled(False)
        self.target = QComboBox()
        self.target.setToolTip(_("Where it sounds: on the FM-1 itself (its sounds, as currently set) or in the "
                                 "studio twin (the same firmware on the computer)"))
        self.target.currentIndexChanged.connect(self._target_changed)
        # lange Gerätenamen dürfen die Spalte nicht verbreitern (sonst kippen schmal/breit-Umschalter hin und her)
        self.target.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.target.setMinimumContentsLength(16)
        self.loop = QCheckBox(_("Loop"))
        self.loop.setChecked(loop)
        self.loop.setToolTip(_("Again and again from the start, until “Stop”"))
        self.out_btn = OutputButton(self)
        self.row = flow_row(lead, self.b_play, self.b_stop, QLabel(_("Target:")), self.target, self.loop, self.out_btn)
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
            self.target.addItem(_("in the studio twin – active device (identical sound)"), DEVICE)
        elif getattr(self.ctx, "midi_connected", False):
            self.target.addItem(_("on the FM-1 “{name}” (its sounds)").format(name=name), DEVICE)
        else:
            self.target.addItem(_("on the FM-1 (none connected)"), DEVICE)
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

    def _target_changed(self, *_args):
        data = self.target.currentData()
        dev = data == DEVICE and getattr(self.ctx, "midi_connected", False)
        self.out_btn.setVisible(data == COMPUTER or (data == DEVICE and not dev and studio_for(self.ctx) is None))
        if not self.seq.running and not (self.player and self.player.playing):
            if dev and getattr(self.ctx, "is_virtual", False):
                hint = _("Plays in the studio twin (active device) – the same firmware as on the FM-1, with the sounds "
                         "currently set on its tracks.")
            elif dev:
                hint = _("Plays on “{name}” with the sounds currently set on its tracks. Nothing is saved on the "
                         "FM-1.").format(name=html.escape(device_name(self.ctx)))
            elif data == STUDIO or (data == DEVICE and studio_for(self.ctx) is not None):
                hint = _("Plays in the studio twin: the same firmware as on the FM-1, sound through the computer – "
                         "with the sounds currently set on the tracks in the studio (page “Studio”).")
                if data == DEVICE:
                    hint = _("No FM-1 connected – “Play” then plays in the studio.") + " " + hint
            elif data == DEVICE:
                hint = _("No FM-1 connected – “Play” then plays on the computer: {label}.").format(label=P.OFFLINE_LABEL)
            else:
                hint = P.OFFLINE_LABEL + "."
            self.target.setToolTip(hint)
            self.status.setText("" if self.compact else hint)

    def _dev_changed(self):
        if self.seq.running and not getattr(self, "_studio_mode", False) and (self.ctx.link is not self._link):
            self.stop(_("Playback ended – the FM-1 was disconnected or changed."))
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
            self.status.setText(_("Cannot be played: {error}").format(error=e))
            return
        if not src or not src.events:
            self.status.setText(_("Nothing to play – there are no notes in the selection."))
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
        self.status.setText(_("Reading the tracks of “{name}” …").format(name=html.escape(name)))

        def go(routing: P.Routing):
            if gen != self._gen or self.ctx.link is not link or link is None or link.closed:
                return
            armed = routing.armed_tracks()
            if armed:
                self.status.setText(ngettext(
                    "<b>Not started:</b> track {tracks} is armed for recording (REC) on “{name}” – the FM-1 would "
                    "record the notes and change the project. Please switch REC off on the FM-1, then play again.",
                    "<b>Not started:</b> tracks {tracks} are armed for recording (REC) on “{name}” – the FM-1 would "
                    "record the notes and change the project. Please switch REC off on the FM-1, then play again.",
                    len(armed)).format(tracks=", ".join(str(k + 1) for k in armed), name=html.escape(name)))
                return
            plan = P.plan_for_device(src.events, routing, src.prj, src.track_override)
            if not plan.events:
                self.status.setText(_("Nothing to send: {reasons}").format(reasons=" ".join(plan.skipped)))
                return
            notes = list(plan.skipped)
            if src.prj is not None and not (src.live_tag and tuple(src.live_tag[:2]) == tag):
                diff = P.compare_sounds(src.prj, routing)
                if diff:
                    notes.append(_("Sounds differ (what is set on the FM-1 plays): {differences}").format(
                        differences="; ".join(diff[:4])))
            if src.track_override is not None and routing.tracks and src.track_override < len(routing.tracks):
                notes.insert(0, _("Track {n} on the FM-1: {sound}.").format(
                    n=src.track_override + 1, sound=routing.tracks[src.track_override].label))
            self.seq = P.MidiSequencer(P.link_sender(link))
            self._hook(link)
            self.seq.start(plan.events, src.bpm, src.loop_quarters if self.loop.isChecked() else None)
            self.mode = DEVICE
            self._label = _("Plays on <b>“{name}”</b> with its own sounds · {routing}").format(
                name=html.escape(name), routing=html.escape(routing.describe()))
            self._notes = notes
            self._running(True)
            self._tick()

        if getattr(self.ctx, "connected", False):
            def work(w, lk):
                return P.read_routing(lk)
            self.ctx.job(self, _("Read tracks"), work, dialog=False, on_done=go,
                         on_fail=lambda m, tb: go(P.Routing(note=m)))
        else:                                   # nur MIDI (Firmware ohne Editor-Protokoll): Standard annehmen
            go(P.Routing(note=_("This firmware cannot be queried – channels 1–4 assumed.")))

    def _play_studio(self, src: Source, fallback: bool = False) -> bool:
        """Noten in den Studio-Zwilling (dieselbe Firmware, Ton über den Rechner). False: Zwilling nicht startbar."""
        svc = studio_for(self.ctx)
        try:
            if svc is None or not svc.auto_start():
                return False
        except Exception as e:  # noqa: BLE001
            self.status.setText(_("Studio cannot be started ({error}) – playing the approximation on the computer.").format(error=e))
            return False
        self._gen += 1
        gen = self._gen
        link = svc.link
        pre = _("No FM-1 connected – ") if fallback else ""
        self.status.setText(pre + _("Reading the tracks of the studio twin …"))
        svc.use(True)
        self._studio_user = svc

        def go(routing: P.Routing):
            if gen != self._gen or link is None or link.closed or svc.st is None:
                self._release_studio()
                return
            armed = routing.armed_tracks()
            if armed:
                with_rec = ", ".join(str(k + 1) for k in armed)
                self.status.setText(ngettext(
                    "<b>Not started:</b> track {tracks} is armed for recording (REC) in the studio twin – please switch "
                    "it off there (page “Studio”).",
                    "<b>Not started:</b> tracks {tracks} are armed for recording (REC) in the studio twin – please "
                    "switch it off there (page “Studio”).", len(armed)).format(tracks=with_rec))
                self._release_studio()
                return
            plan = P.plan_for_device(src.events, routing, src.prj, src.track_override)
            if not plan.events:
                self.status.setText(_("Nothing to send: {reasons}").format(reasons=" ".join(plan.skipped)))
                self._release_studio()
                return
            notes = list(plan.skipped)
            if src.prj is not None:
                diff = P.compare_sounds(src.prj, routing)
                if diff:
                    notes.append(_("Sounds differ (what is set in the studio plays): {differences}").format(
                        differences="; ".join(diff[:4])))
            self.seq = P.MidiSequencer(P.link_sender(link))
            self.seq.start(plan.events, src.bpm, src.loop_quarters if self.loop.isChecked() else None)
            self.mode = DEVICE
            self._studio_mode = True
            self._label = pre + _("Plays <b>in the studio twin</b> (Melodee {version}, identical sound) · {routing}").format(
                version=html.escape(svc.version), routing=html.escape(routing.describe()))
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
        pre = (_("No FM-1 connected – ") if fallback else "")
        self.status.setText(pre + _("Sound is being computed on the computer …"))
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
                    [_("Substitute sound because the track engine is unknown.")]
                self._running(True)
                self._tick()
            else:
                self._running(False)

        def fail(msg, _tb):
            if gen == self._gen:
                self._running(False)
                self.status.setText(_("Preview not possible: {error}").format(error=msg))
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
            self.status.setText(reason or (_("Stopped – all notes on the FM-1 are off.") if self.mode == DEVICE
                                           else _("Stopped.")))

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
                studio = getattr(self, "_studio_mode", False)
                name = html.escape(device_name(self.ctx))
                self._release_studio()
                if studio:
                    msg = ngettext("Done – {n} MIDI message sent to the studio twin, all notes off{error}.",
                                   "Done – {n} MIDI messages sent to the studio twin, all notes off{error}.", s.sent)
                else:
                    msg = ngettext("Done – {n} MIDI message sent to “{name}”, all notes off{error}.",
                                   "Done – {n} MIDI messages sent to “{name}”, all notes off{error}.", s.sent)
                self.status.setText(msg.format(n=s.sent, name=name, error=err))
                return
            bar = int(s.position // 4) + 1
            total = max(1, int((s.total + 3.999) // 4))
            loop = _(", pass {n}").format(n=s.loops + 1) if self.loop.isChecked() else ""
            drop = " · " + ngettext("{n} note skipped (FM-1 busy right now)", "{n} notes skipped (FM-1 busy right now)",
                                    s.dropped).format(n=s.dropped) if s.dropped else ""
            extra = "<br>" + "<br>".join(html.escape(n) for n in self._notes) if getattr(self, "_notes", None) else ""
            self.status.setText(self._label + " · " + _("bar {bar}/{total}").format(bar=min(bar, total), total=total)
                                + f"{loop}{drop}{extra}")
        elif self.mode == COMPUTER:
            if self.player is None or not self.player.playing:
                self._running(False)
                return
            extra = "<br>" + "<br>".join(html.escape(n) for n in self._notes) if getattr(self, "_notes", None) else ""
            self.status.setText(self._label + extra)

    def shutdown(self):
        self.stop(quiet=True)
