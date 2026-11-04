# SPDX-License-Identifier: GPL-3.0-only
"""MIDI-Zentrale: Monitor, Bildschirm-Tastatur, Takt (Clock), Regler mit MIDI-Learn, Verbindungen.

* Monitor: hört über ``link.add_listener`` alles mit, was zwischen App und FM-1 läuft, und auf
  Wunsch weitere MIDI-Eingänge (mido/rtmidi). Ein Ringpuffer sammelt im MIDI-Thread, die Tabelle
  wird zehnmal pro Sekunde gesammelt aktualisiert.
* Regler: hält das FM-1 (wie die FM-1-Seite), solange der Reiter offen ist – Anfrage/Antwort mit
  DESC/DUMP/TRACK_PARAM darf nicht mit dem eingebetteten Editor kollidieren (das Gerät hält nur
  einen SysEx-Rahmen gleichzeitig, Antworten werden nach Befehl zugeordnet). Schreiben läuft in
  einem eigenen Thread, Knopfbewegungen werden pro Parameter zusammengefasst (nur der letzte Wert).
"""
from __future__ import annotations

import threading
from pathlib import Path

from PyQt6.QtCore import QRect, QSize, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QKeySequence, QPainter, QShortcut
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QGridLayout,
                             QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QPushButton,
                             QScrollArea, QSlider, QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget,
                             QVBoxLayout, QWidget)

from ... import alsaseq, config, miditools as M
from ...clock import ClockMaster, TapTempo
from ...protocol import Cmd, ProtocolError, parse_desc, r14
from ..common import (Card, button, empty_state, fs_error_text, heading, icon, label, notify, run_job, show_error,
                      start_dir)
from ..responsive import reflow
from . import PageSpec
from ...i18n import _, decimal, ngettext, pgettext

SETTINGS = {"learn": [], "learn_port": "", "routing_presets": [], "bpm": 120.0, "swing": 0, "kb_channel": 1,
            "kb_velocity": 100, "kb_octave": 4, "kb_out": "fm1", "clock_out": "fm1", "hide_clock": True,
            "hide_sense": True}
FM1 = "fm1"


def _settings() -> dict:
    return config.module("midi", SETTINGS)


def _save():
    try:
        config.save()
    except OSError:
        pass


def mido_names(output: bool) -> tuple[list[str], str]:
    """(Portnamen, Hinweis) – ohne rtmidi gibt es keine weiteren Ports."""
    try:
        import mido
        names = mido.get_output_names() if output else mido.get_input_names()
        return [n for n in names if "rtmidi" not in n.lower()], ""
    except Exception as e:                                 # noqa: BLE001
        return [], _("More MIDI ports need python3-rtmidi ({error}).").format(error=type(e).__name__)



def _de(x: float, nd: int) -> str:
    """Zahl mit dem Dezimaltrennzeichen der Oberflächensprache („120,0 BPM“ / „120.0 BPM“)."""
    return decimal(x, nd)

class Outputs:
    """offene mido-Ausgänge (lazy, eine Instanz je Name)."""

    def __init__(self):
        self.ports: dict = {}
        self.lock = threading.Lock()

    def send(self, name: str, data):
        with self.lock:
            p = self.ports.get(name)
            if p is None:
                import mido
                p = self.ports[name] = mido.open_output(name)
        p.send(__import__("mido").Message.from_bytes(list(data)))

    def close(self):
        with self.lock:
            for p in self.ports.values():
                try:
                    p.close()
                except Exception:                          # noqa: BLE001
                    pass
            self.ports.clear()


# ============================================================== Monitor ===
FILTERS = [("note", pgettext("MIDI", "Notes")), ("cc", _("Controller")), ("prog", _("Program")), ("bend", _("Pitch bend")),
           ("at", "Aftertouch"), ("sysex", "SysEx"), ("transport", "Start/Stop"), ("clock", "Clock"),
           ("sense", "Active Sensing"), ("other", _("Other"))]
MAX_ROWS = 2000           # Zeilen in der Tabelle (das Protokoll dahinter hält 20 000 für den Export)
PER_TICK = 400            # mehr neue Zeilen je Aktualisierung (10×/s) zeigt die Tabelle nicht – sonst stockt die Oberfläche


class MonitorTab(QWidget):
    def __init__(self, page):
        super().__init__()
        self.page = page
        self.log = M.EventLog(20000)
        self.meter = M.ClockMeter()
        self.paused = False
        self.inputs: dict = {}                             # Name -> mido-Port
        s = _settings()
        lay = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(_("Search (e.g. “C4”, “CC 74”, “TRACK_PARAM”) …"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refilter)
        bar.addWidget(self.search, 1)
        self.dir = QComboBox()
        for k, t in (("", _("both directions")), ("in", _("only from the device ←")), ("out", _("only to the device →"))):
            self.dir.addItem(t, k)
        self.dir.currentIndexChanged.connect(self.refilter)
        bar.addWidget(self.dir)
        self.b_pause = button(_("Pause"), "media-playback-pause", tooltip=_("Pause the display (space bar)"))
        self.b_pause.setCheckable(True)
        self.b_pause.toggled.connect(self._pause)
        bar.addWidget(self.b_pause)
        bar.addWidget(button(_("Clear"), "edit-clear", slot=self.clear, tooltip=_("Discard the log (not "
                                                                                "just the display)")))
        bar.addWidget(button(_("Export …"), "document-save", slot=self.export,
                             tooltip=_("save the filtered log as text or CSV (up to 20 000 messages)")))
        lay.addLayout(bar)
        fl = QHBoxLayout()
        self.cbs: dict[str, QCheckBox] = {}
        for k, t in FILTERS:
            cb = QCheckBox(t)
            cb.setChecked(not ((k == "clock" and s["hide_clock"]) or (k == "sense" and s["hide_sense"])))
            cb.toggled.connect(self._filter_changed)
            self.cbs[k] = cb
            fl.addWidget(cb)
        fl.addStretch(1)
        lay.addLayout(fl)
        reflow(fl)                                         # Filter brechen bei schmalem Fenster um
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels([_("Time"), _("Direction"), "Port", _("Type"), _("Channel"), _("Data"), _("Meaning")])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setWordWrap(False)
        hh = self.table.horizontalHeader()
        for i, w in enumerate((80, 100, 110, 110, 50, 200)):
            self.table.setColumnWidth(i, w)
        hh.setStretchLastSection(True)
        lay.addWidget(self.table, 1)
        self.empty = empty_state("activity", _("Play something on the FM-1 – the messages appear here"),
                                 _("Notes, controls, clock and SysEx in both directions. With “Listen in” "
                                   "below, more MIDI inputs (controllers, sound generators) are added."))
        lay.addWidget(self.empty, 1)
        self.table.hide()
        st = QHBoxLayout()
        self.status = label("", "muted")
        st.addWidget(self.status, 1)
        st.addWidget(QLabel(_("Additional input:")))
        self.in_combo = QComboBox()
        self.in_combo.setMinimumWidth(220)
        st.addWidget(self.in_combo)
        self.b_listen = button(_("Listen in"), "list-add", slot=self.add_input,
                               tooltip=_("also show this MIDI input (e.g. controller, sound generator)"))
        st.addWidget(self.b_listen)
        self.in_list = QLabel("")
        self.in_list.setProperty("role", "muted")
        st.addWidget(self.in_list)
        lay.addLayout(st)
        self.hidden = 0
        self.skipped = 0
        self.timer = QTimer(self, interval=100)
        self.timer.timeout.connect(self.pull)
        QShortcut(QKeySequence("Space"), self.table, activated=self.b_pause.toggle)
        self.fill_inputs()

    # ---- Zuführung (MIDI-Threads)
    def on_link(self, data, direction):
        st = data[0] if data else 0
        if direction == "in" and st >= 0xF8:
            self.meter.feed(st)
        self.log.add(data, direction, "FM-1", self.page.firmware)

    def _on_input(self, name):
        def cb(msg):
            b = msg.bytes()
            self.log.add(b, "in", name.split(":")[0][:24], self.page.firmware)
            self.page.learn_feed(name, b)
        return cb

    def fill_inputs(self):
        names, hint = mido_names(False)
        self.in_combo.clear()
        for n in names:
            self.in_combo.addItem(n)
        if not names:
            self.in_combo.addItem(_("no additional MIDI input found"))
        self.in_combo.setEnabled(bool(names))
        self.b_listen.setEnabled(bool(names))
        self.in_combo.setToolTip(hint or (_("MIDI inputs of the system (USB controllers, sound generators)") if names else
                                          _("No additional MIDI input connected – plug in a controller, "
                                            "then open the page again.")))

    def add_input(self):
        name = self.in_combo.currentText()
        if not name or name in self.inputs:
            return
        try:
            import mido
            self.inputs[name] = mido.open_input(name, callback=self._on_input(name))
        except Exception as e:                             # noqa: BLE001
            show_error(self, _("Input cannot be opened"), f"{name}: {e}")
            return
        self.in_list.setText(_("listening to: {ports}").format(ports=", ".join(n.split(":")[0] for n in self.inputs)))

    def close_inputs(self):
        for p in self.inputs.values():
            try:
                p.close()
            except Exception:                              # noqa: BLE001
                pass
        self.inputs.clear()
        self.in_list.setText("")

    # ---- Anzeige
    def visible_kind(self, ev) -> bool:
        cb = self.cbs.get(ev.dec.kind)
        if cb is not None and not cb.isChecked():
            return False
        d = self.dir.currentData()
        if d and ev.direction != d:
            return False
        q = self.search.text().strip().lower()
        if q:
            hay = f"{ev.dec.type} {ev.dec.meaning} {ev.data.hex(' ')} {ev.port}".lower()
            return all(w in hay for w in q.split())
        return True

    def pull(self):
        new = self.log.take()
        if self.paused or not new:
            self._status()
            return
        rows = [e for e in new if self.visible_kind(e)]
        self.hidden += len(new) - len(rows)
        if len(rows) > PER_TICK:                           # Sturzflut (z. B. dichte Clock): Anzeige kürzen, Protokoll behält alles
            self.skipped += len(rows) - PER_TICK
            rows = rows[-PER_TICK:]
        self._append(rows)
        self._status()

    def _append(self, rows):
        if not rows:
            return
        if self.empty.isVisible() or self.table.isHidden():
            self.empty.hide()
            self.table.show()
        t = self.table
        at_end = t.verticalScrollBar().value() >= t.verticalScrollBar().maximum() - 2
        t.setUpdatesEnabled(False)
        rows = rows[-MAX_ROWS:]
        extra = t.rowCount() + len(rows) - MAX_ROWS
        if extra > 0:                                     # älteste Zeilen in einem Rutsch weg (ein Modell-Signal)
            t.model().removeRows(0, min(extra, t.rowCount()))
        r0 = t.rowCount()
        t.setRowCount(r0 + len(rows))
        for i, e in enumerate(rows):
            r = r0 + i
            direction = _(M.DIR_TEXT[e.direction]) if e.direction in M.DIR_TEXT else e.direction
            vals = (_de(e.t, 3).rjust(9), direction, e.port, e.dec.type,
                    str(e.dec.channel or ""), e.dec.data, e.dec.meaning)
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 1:
                    it.setForeground(QColor("#3daee9" if e.direction == "in" else "#f67400"))
                t.setItem(r, c, it)
        t.setUpdatesEnabled(True)
        if at_end:
            t.scrollToBottom()

    def _status(self):
        bpm = self.meter.bpm if self.meter.alive() else None
        n = len(self.log.events)
        parts = [ngettext("{n} message (the table shows the last {rows})", "{n} messages (the table shows the last {rows})",
                          n).format(n=n, rows=MAX_ROWS) if n > MAX_ROWS else
                 ngettext("{n} message", "{n} messages", n).format(n=n)]
        if self.skipped:
            parts.append(_("{n} not shown during a flood (included in the export)").format(n=self.skipped))
        if bpm:
            j = self.meter.jitter_ms
            parts.append(_("incoming clock: {bpm} BPM (±{jitter} ms)").format(bpm=_de(bpm, 1), jitter=_de(j, 2))
                         if j is not None else _("incoming clock: {bpm} BPM").format(bpm=_de(bpm, 1)))
        if self.paused:
            parts.append(_("paused"))
        self.status.setText(" · ".join(parts))

    def refilter(self, *_args):
        self.table.setRowCount(0)
        self._append([e for e in list(self.log.events)[-MAX_ROWS:] if self.visible_kind(e)])

    def _filter_changed(self, *_args):
        s = _settings()
        s["hide_clock"] = not self.cbs["clock"].isChecked()
        s["hide_sense"] = not self.cbs["sense"].isChecked()
        _save()
        self.refilter()

    def _pause(self, on):
        self.paused = on
        self.b_pause.setText(_("Continue") if on else _("Pause"))
        if not on:
            self.refilter()

    def clear(self):
        self.log.clear()
        self.table.setRowCount(0)
        self.hidden = 0
        self.skipped = 0
        self.table.hide()
        self.empty.show()
        self._status()

    def export(self):
        fn, filt = QFileDialog.getSaveFileName(self, _("Save MIDI log"),
                                               str(start_dir("MIDI") / "midi-monitor.txt"),
                                               _("Text (*.txt);;CSV table (*.csv)"))
        if not fn:
            return
        fmt = "csv" if fn.lower().endswith(".csv") or "CSV" in filt else "txt"
        events = [e for e in self.log.events if self.visible_kind(e)]
        try:
            Path(fn).write_text(M.export_text(events, fmt), "utf-8")
        except OSError as e:
            show_error(self, _("Save MIDI log"), fs_error_text(e, _("Not saved")))
            return
        notify(ngettext("{n} message saved: {file}", "{n} messages saved: {file}", len(events)).format(
            n=len(events), file=Path(fn).name), "success")


# ============================================================= Tastatur ===
# deutsche Tastatur (QWERTZ), wie in Trackern: untere Reihe ab Y = C, obere ab Q = C eine Oktave höher
KEYMAP = {}
for i, ch in enumerate("YSXDCVGBHNJM"):
    KEYMAP[ord(ch)] = i
KEYMAP[ord(",")] = 12
for i, ch in enumerate("Q2W3ER5T6Z7UI"):
    KEYMAP[ord(ch)] = 12 + i
BLACK = {1, 3, 6, 8, 10}


class Piano(QWidget):
    note_on = pyqtSignal(int)
    note_off = pyqtSignal(int)

    def __init__(self, octaves=3):
        super().__init__()
        self.octaves = octaves
        self.base = 48
        self.down: set[int] = set()
        self.key_down: set[int] = set()                    # Qt-Tasten, die gerade gedrückt sind
        self.mouse_note: int | None = None
        self.setMinimumHeight(140)
        self.setMinimumWidth(500)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setToolTip(_("Play with the mouse or with the keyboard: bottom row Y S X D C V … = C to B, "
                          "top row Q 2 W 3 E … one octave higher."))

    def sizeHint(self):
        return QSize(800, 160)

    def _keys(self):
        whites = [n for n in range(self.base, self.base + 12 * self.octaves + 1) if n % 12 not in BLACK]
        w = self.width() / len(whites)
        h = self.height()
        rects = {}
        for i, n in enumerate(whites):
            rects[n] = QRect(int(i * w), 0, int(w) - 1, h - 1)
        blacks = {}
        for i, n in enumerate(whites):
            if (n + 1) % 12 in BLACK and n + 1 < self.base + 12 * self.octaves:
                blacks[n + 1] = QRect(int(i * w + w * 0.65), 0, int(w * 0.7), int(h * 0.6))
        return rects, blacks

    def note_at(self, pos) -> int | None:
        rects, blacks = self._keys()
        for n, r in blacks.items():
            if r.contains(pos):
                return n
        for n, r in rects.items():
            if r.contains(pos):
                return n
        return None

    def paintEvent(self, _e):
        p = QPainter(self)
        rects, blacks = self._keys()
        hl = self.palette().highlight().color()
        for n, r in rects.items():
            p.fillRect(r, hl if n in self.down else QColor("#f4f4f4"))
            p.setPen(QColor("#666"))
            p.drawRect(r)
            if n % 12 == 0:
                p.drawText(r.adjusted(0, 0, 0, -6), int(Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter),
                           M.note_name(n))
        for n, r in blacks.items():
            p.fillRect(r, hl.darker(130) if n in self.down else QColor("#222"))

    def mousePressEvent(self, e):
        n = self.note_at(e.position().toPoint())
        if n is not None:
            self.mouse_note = n
            self.note_on.emit(n)

    def mouseMoveEvent(self, e):
        n = self.note_at(e.position().toPoint())
        if self.mouse_note is not None and n is not None and n != self.mouse_note:
            self.note_off.emit(self.mouse_note)
            self.mouse_note = n
            self.note_on.emit(n)

    def mouseReleaseEvent(self, _e):
        if self.mouse_note is not None:
            self.note_off.emit(self.mouse_note)
            self.mouse_note = None

    def keyPressEvent(self, e):
        k = KEYMAP.get(e.key())
        if k is None or e.isAutoRepeat():
            return super().keyPressEvent(e)
        self.key_down.add(e.key())
        self.note_on.emit(self.base + k)

    def keyReleaseEvent(self, e):
        k = KEYMAP.get(e.key())
        if k is None or e.isAutoRepeat():
            return super().keyReleaseEvent(e)
        self.key_down.discard(e.key())
        self.note_off.emit(self.base + k)

    def release_all_keys(self):
        """Note-Off für alles, was über Tastatur oder Maus gedrückt ist – Fokusverlust und Seitenwechsel
        liefern sonst nie ein keyRelease, und die Note hinge am FM-1."""
        for key in list(self.key_down):
            k = KEYMAP.get(key)
            if k is not None:
                self.note_off.emit(self.base + k)
        self.key_down.clear()
        if self.mouse_note is not None:
            self.note_off.emit(self.mouse_note)
            self.mouse_note = None

    def focusOutEvent(self, e):
        self.release_all_keys()
        super().focusOutEvent(e)

    def hideEvent(self, e):
        self.release_all_keys()
        super().hideEvent(e)


class KeyboardTab(QWidget):
    def __init__(self, page):
        super().__init__()
        self.page = page
        s = _settings()
        self.held: set[int] = set()
        self.sounding: set[int] = set()
        lay = QVBoxLayout(self)
        row = QHBoxLayout()                     # Gruppen (Beschriftung + Feld) bleiben beim Umbrechen zusammen
        g1, g2, g3, g4 = (QHBoxLayout() for _i in range(4))
        g1.addWidget(QLabel(_("Target:")))
        self.out = QComboBox()
        self.out.setMinimumWidth(260)
        g1.addWidget(self.out)
        g2.addWidget(QLabel(_("Channel:")))
        self.channel = QSpinBox(minimum=1, maximum=16, value=int(s["kb_channel"]))
        self.channel.setToolTip(_("Melodee: channel 1–4 = track 1–4 (with ROUT CH1-4), SEL = selected track"))
        g2.addWidget(self.channel)
        g3.addWidget(QLabel(_("Velocity:")))
        self.vel = QSlider(Qt.Orientation.Horizontal, minimum=1, maximum=127, value=int(s["kb_velocity"]))
        self.vel.setMaximumWidth(140)
        self.vel.setMinimumWidth(100)
        self.vel_l = QLabel(str(self.vel.value()))
        self.vel.valueChanged.connect(lambda v: self.vel_l.setText(str(v)))
        g3.addWidget(self.vel)
        g3.addWidget(self.vel_l)
        g4.addWidget(button("", "go-down", slot=lambda: self.shift(-1), tooltip=_("Octave down (Page ↓)")))
        self.oct_l = QLabel()
        g4.addWidget(self.oct_l)
        g4.addWidget(button("", "go-up", slot=lambda: self.shift(1), tooltip=_("Octave up (Page ↑)")))
        for g in (g1, g2, g3, g4):
            row.addLayout(g)
        lay.addLayout(row)
        reflow(row, 12)
        mid = QHBoxLayout()
        wheels = QVBoxLayout()
        self.bend = QSlider(Qt.Orientation.Vertical, minimum=-8192, maximum=8191, value=0)
        self.bend.setToolTip(_("Pitch bend (springs back when released)"))
        self.bend.valueChanged.connect(lambda v: self.send([0xE0 | self.ch(), (v + 8192) & 127, (v + 8192) >> 7]))
        self.bend.sliderReleased.connect(lambda: self.bend.setValue(0))
        self.mod = QSlider(Qt.Orientation.Vertical, minimum=0, maximum=127, value=0)
        self.mod.setToolTip(_("Modulation wheel (CC 1)"))
        self.mod.valueChanged.connect(lambda v: self.send([0xB0 | self.ch(), 1, v]))
        wl = QHBoxLayout()
        wl.addWidget(self.bend)
        wl.addWidget(self.mod)
        wheels.addLayout(wl)
        wheels.addWidget(label("Bend  Mod", "small"))
        mid.addLayout(wheels)
        self.piano = Piano(3)
        self.piano.note_on.connect(self.press)
        self.piano.note_off.connect(self.release)
        mid.addWidget(self.piano, 1)
        lay.addLayout(mid)
        opts = QHBoxLayout()
        self.sustain = QCheckBox(_("Sustain (CC 64)"))
        self.sustain.setToolTip(_("Sustain pedal: notes keep sounding after release. Released when "
                                  "leaving the page."))
        self.sustain.toggled.connect(lambda on: self.send([0xB0 | self.ch(), 64, 127 if on else 0]))
        opts.addWidget(self.sustain)
        self.hold = QCheckBox(_("Hold chord"))
        self.hold.setToolTip(_("Keys latch: pressing again releases. Everything is released when leaving "
                               "the page."))
        self.hold.toggled.connect(lambda on: None if on else self.all_off())
        opts.addWidget(self.hold)
        opts.addWidget(button(_("All notes off"), "media-playback-stop", slot=self.all_off,
                              tooltip=_("sends note off for everything that sounds, and CC 123")))
        opts.addStretch(1)
        self.info = label("", "muted")
        opts.addWidget(self.info)
        lay.addLayout(opts)
        lay.addWidget(label(_("Tip: click into the keyboard, then play with Y S X D C … and Q 2 W 3 E … "
                              "For Melodee tracks 5–8 (MIDI tracks) choose the track's channel and “FM-1 "
                              "sound generator” as target – or the FM-1 directly on channel 1–4."), "muted"))
        lay.addStretch(1)
        QShortcut(QKeySequence("PgDown"), self, activated=lambda: self.shift(-1))
        QShortcut(QKeySequence("PgUp"), self, activated=lambda: self.shift(1))
        self.piano.base = 12 * (int(s["kb_octave"]) + 1)
        self._oct_label()
        self.fill_outputs()

    def ch(self) -> int:
        return self.channel.value() - 1

    def fill_outputs(self):
        cur = self.out.currentData() or _settings()["kb_out"]
        self.out.blockSignals(True)
        self.out.clear()
        self.out.addItem(icon("audio-midi"), _("FM-1 (app connection)"), FM1)
        names, hint = mido_names(True)
        for n in names:
            self.out.addItem(n, n)
        i = self.out.findData(cur)
        self.out.setCurrentIndex(max(0, i))
        self.out.blockSignals(False)
        if hint:
            self.out.setToolTip(hint)

    def send(self, data) -> bool:
        ok = self.page.send_to(self.out.currentData() or FM1, data)
        self.info.setText("" if ok else _("not sent – FM-1 busy or not connected"))
        return ok

    def press(self, n):
        n = max(0, min(127, n))
        if self.hold.isChecked() and n in self.held:
            self.held.discard(n)
            self._off(n)
            return
        if n in self.sounding:
            self._off(n)
        self.send([0x90 | self.ch(), n, self.vel.value()])
        self.sounding.add(n)
        if self.hold.isChecked():
            self.held.add(n)
        self.piano.down = set(self.sounding)
        self.piano.update()

    def release(self, n):
        if self.hold.isChecked() and n in self.held:
            return
        self._off(n)

    def _off(self, n):
        if n in self.sounding:
            self.send([0x80 | self.ch(), n, 0])
            self.sounding.discard(n)
        self.piano.down = set(self.sounding)
        self.piano.update()

    def all_off(self):
        """alles still: Note-Off für jede klingende Note, Sustain-Pedal hoch, dann CC 123."""
        self.piano.release_all_keys()
        for n in list(self.sounding):
            self._off(n)
        self.held.clear()
        if self.sustain.isChecked():
            self.sustain.setChecked(False)                 # sendet CC 64 = 0
        self.send([0xB0 | self.ch(), 123, 0])

    def quiet(self) -> bool:
        """True, wenn nichts klingt und kein Pedal gedrückt ist."""
        return not self.sounding and not self.sustain.isChecked() and not self.held

    def shift(self, d):
        self.all_off()
        self.piano.base = max(0, min(84, self.piano.base + 12 * d))
        self._oct_label()

    def _oct_label(self):
        self.oct_l.setText(_("from {note}").format(note=M.note_name(self.piano.base)))
        s = _settings()
        s["kb_octave"] = self.piano.base // 12 - 1

    def store(self):
        s = _settings()
        s.update(kb_channel=self.channel.value(), kb_velocity=self.vel.value(), kb_out=self.out.currentData() or FM1)


# ================================================================ Takt ===
class ClockTab(QWidget):
    def __init__(self, page):
        super().__init__()
        self.page = page
        s = _settings()
        self.tap = TapTempo()
        self.master = ClockMaster(self._send, float(s["bpm"]))
        self.master.swing = int(s["swing"]) / 100
        lay = QVBoxLayout(self)
        c = Card(_("Clock master"), icon="metronome",
                 subtitle=_("The app sets the tempo: 24 pulses per quarter, Start/Stop/Continue. Melodee "
                            "follows when GLO › CLK on the FM-1 is set to “USB” (40–240 BPM). On “INT” "
                            "the FM-1 leads itself – from Melodee 1.6.1 it then sends its own clock. The "
                            "clock keeps running when you change the page – “Stop” here or when quitting "
                            "the app."))
        g = QGridLayout()
        g.addWidget(QLabel(_("Tempo:")), 0, 0)
        self.bpm = QDoubleSpinBox(minimum=30, maximum=300, decimals=1, value=float(s["bpm"]), suffix=" BPM")
        self.bpm.setSingleStep(1)
        self.bpm.valueChanged.connect(self._bpm)
        g.addWidget(self.bpm, 0, 1)
        g.addWidget(button("Tap", "hand", slot=self._tap, tooltip=_("click in time (or press T)")), 0, 2)
        g.addWidget(QLabel(_("Swing:")), 1, 0)
        self.swing = QSlider(Qt.Orientation.Horizontal, minimum=0, maximum=50, value=int(s["swing"]))
        self.swing_l = QLabel()
        self.swing.valueChanged.connect(self._swing)
        g.addWidget(self.swing, 1, 1)
        g.addWidget(self.swing_l, 1, 2)
        g.addWidget(QLabel(_("Target:")), 2, 0)
        self.out = QComboBox()
        self.out.currentIndexChanged.connect(lambda _i: self._update_hint())
        g.addWidget(self.out, 2, 1, 1, 2)
        g.addWidget(QLabel(_("Start at bar:")), 3, 0)
        self.bar = QSpinBox(minimum=1, maximum=1024, value=1)
        self.bar.setToolTip(_("1 = from the beginning (MIDI Start). From bar 2: Song Position + Continue "
                              "– the follower joins at this point (Melodee from 1.6.1 with CLK USB; older "
                              "firmware simply continues)."))
        g.addWidget(self.bar, 3, 1)
        c.lay.addLayout(g)
        row = QHBoxLayout()
        self.b_start = button(_("Start"), "media-playback-start", "primary", self.start,
                              _("sends MIDI Start (from the beginning) and then 24 pulses per quarter"))
        self.b_cont = button(_("Continue"), "media-skip-forward", slot=lambda: self.start(True),
                             tooltip=_("sends MIDI Continue: continues where it was stopped"))
        self.b_stop = button(_("Stop"), "media-playback-stop", slot=self.stop, tooltip=_("sends MIDI Stop "
                                                                                       "and ends the "
                                                                                       "clock"))
        for b in (self.b_start, self.b_cont, self.b_stop):
            row.addWidget(b)
        row.addStretch(1)
        c.lay.addLayout(row)
        self.state = label(_("stopped"), "muted")
        c.lay.addWidget(self.state)
        self.hint = label("", "muted")
        self.hint.setWordWrap(True)
        self.hint.hide()
        c.lay.addWidget(self.hint)
        lay.addWidget(c)
        f = Card(_("Incoming clock (follower)"), icon="activity",
                 subtitle=_("Measures tempo and steadiness of a clock coming from the FM-1 or a monitored "
                            "input (tab Monitor)."))
        self.follow = label(_("no clock received"), "")
        f.lay.addWidget(self.follow)
        lay.addWidget(f)
        lay.addStretch(1)
        QShortcut(QKeySequence("T"), self, activated=self._tap)
        self._swing(self.swing.value())
        self.timer = QTimer(self, interval=250)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.fill_outputs()

    def fill_outputs(self):
        cur = self.out.currentData() or _settings()["clock_out"]
        self.out.clear()
        self.out.addItem(_("FM-1 (app connection)"), FM1)
        for n in mido_names(True)[0]:
            self.out.addItem(n, n)
        self.out.setCurrentIndex(max(0, self.out.findData(cur)))
        self._update_hint()

    def _device_sends_clock(self) -> bool:
        from ... import playback as P
        info = getattr(self.page.ctx, "info", None)
        return P.sends_clock(getattr(info, "version", "") or "")

    def _update_hint(self):
        """Melodee ab 1.6.1 sendet mit CLK INT selbst Clock: daran erinnern, wenn die App das FM-1 takten soll."""
        from ... import playback as P
        show = (self.out.currentData() or FM1) == FM1 and self._device_sends_clock()
        self.hint.setText(P.CLOCK_CONFLICT if show else "")
        self.hint.setVisible(show)

    def _send(self, data):                       # aus dem Clock-Thread
        if not self.page.send_to(self._target, data, realtime=True):
            raise RuntimeError(_("not sent"))

    def start(self, cont=False):
        self._target = self.out.currentData() or FM1
        if self._target == FM1 and (self.page.ctx.link is None or self.page.ctx.link.closed):
            self.state.setText(_("Not started: no FM-1 connected. Plug in the FM-1 or choose a different "
                                 "target above."))
            return
        s = _settings()
        s.update(bpm=self.bpm.value(), swing=self.swing.value(), clock_out=self._target)
        _save()
        bar = self.bar.value()
        if not cont and bar > 1:
            from ...clock import bar_position
            self.master.start(position=bar_position(bar))
        else:
            self.master.start(cont)
        self._update_hint()
        self.refresh()

    def stop(self):
        self.master.stop()
        self.refresh()

    def _bpm(self, v):
        self.master.bpm = v

    def _tap(self):
        b = self.tap.tap()
        if b:
            self.bpm.setValue(round(b, 1))

    def _swing(self, v):
        self.master.swing = v / 100
        self.swing_l.setText(f"{50 + v // 2} %" if v else pgettext("state", "off"))

    def refresh(self):
        m = self.master
        if m.running:
            st = m.jitter_stats()
            parts = [_("running: {bpm} BPM, {pulses} pulses (bar {bar})").format(
                bpm=_de(m.bpm, 1), pulses=m.pulses, bar=m.pulses // 96 + 1)]
            if st.get("n"):
                parts.append(_("Accuracy: pulses on average {mean} ms off the target time, 95 % under {p95} ms (under "
                               "1 ms is inaudible)").format(mean=_de(st["mean_ms"], 2), p95=_de(st["p95_ms"], 2)))
            if m.send_errors:
                parts.append(ngettext("{n} pulse not sent (target gone?)", "{n} pulses not sent (target gone?)",
                                      m.send_errors).format(n=m.send_errors))
            self.state.setText(" · ".join(parts))
        else:
            self.state.setText(_("stopped"))
        mt = self.page.monitor.meter
        if m.running and mt.alive() and getattr(self, "_target", None) == FM1:
            # zwei Takte zugleich: das FM-1 führt selbst (CLK INT, ab Melodee 1.6.1) und hört nicht auf die App
            self.hint.setText(_("A clock is coming in at the same time – probably the FM-1 is sending "
                                "itself (CLK INT). On the FM-1 set GLO › GLOBAL › CLK to USB, then it "
                                "follows the app."))
            self.hint.show()
        if mt.alive():
            j = mt.jitter_ms
            if not mt.bpm:
                self.follow.setText(_("Clock detected, measuring …"))
            elif j is not None:
                self.follow.setText(_("{bpm} BPM, fluctuation ±{jitter} ms between the pulses").format(
                    bpm=_de(mt.bpm, 1), jitter=_de(j, 2)))
            else:
                self.follow.setText(_("{bpm} BPM").format(bpm=_de(mt.bpm, 1)))
        else:
            self.follow.setText(_("no clock received"))


# ============================================================== Regler ===
class Param:
    def __init__(self, pid, d):
        self.id, self.d = pid, d
        self.value = d.default


class ControlsTab(QWidget):
    values_ready = pyqtSignal(object)

    def __init__(self, page):
        super().__init__()
        self.page = page
        self.params: dict[int, Param] = {}
        self.widgets: dict[int, QWidget] = {}
        self.track = 0
        self.dec14 = M.v14_dec
        self.enc14 = M.v14_enc
        self.pending: dict[int, int] = {}
        self.plock = threading.Lock()
        self.wake = threading.Event()
        self.held_link = None
        self.learning: int | None = None
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.b_read = button(_("Read from FM-1"), "view-refresh", "primary", self.read)
        top.addWidget(self.b_read)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText(_("Search parameters …"))
        self.filter.textChanged.connect(self._filter)
        top.addWidget(self.filter, 1)
        top.addWidget(QLabel(_("Learn input:")))
        self.learn_in = QComboBox()
        self.learn_in.setMinimumWidth(200)
        top.addWidget(self.learn_in)
        top.addWidget(button(_("Delete assignments"), "edit-clear", slot=self.clear_learn))
        lay.addLayout(top)
        self.state = label(_("Nothing read yet – how it works: “Read from FM-1” fetches names, ranges and "
                             "values of the selected track. Then move the controls; with “Learn” assign a "
                             "knob of your controller."),
                           "muted")
        lay.addWidget(self.state)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.inner = QWidget()
        self.grid = QGridLayout(self.inner)
        self.scroll.setWidget(self.inner)
        lay.addWidget(self.scroll, 1)
        self.values_ready.connect(self._show)
        self.learn_port = None
        self.fill_learn()

    # ---- Halten wie die FM-1-Seite
    def hold(self):
        link = self.page.ctx.link
        if self.held_link is link or link is None or link.closed:
            return
        self.release()
        link.hold(settle=False)
        self.held_link = link
        self.page.own_holds = 1
        self.stop_writer = threading.Event()
        threading.Thread(target=self._writer, args=(link, self.stop_writer), daemon=True, name="fm1c-regler").start()

    def release(self):
        if self.held_link is not None:
            self.stop_writer.set()
            self.wake.set()
            if not self.held_link.closed:
                self.held_link.release()
            self.held_link = None
            self.page.own_holds = 0

    def _writer(self, link, stop):
        while not stop.is_set():
            self.wake.wait(0.5)
            self.wake.clear()
            while True:
                with self.plock:
                    if not self.pending:
                        break
                    pid, v = self.pending.popitem()
                try:
                    a = link.request(Cmd.TRACK_PARAM, [self.track, pid, *self.enc14(v)], timeout=0.8, retries=0)
                    if len(a) >= 4:
                        self.params[pid].value = self.dec14(a[2], a[3])
                except Exception:                          # noqa: BLE001 – nächster Versuch beim nächsten Dreh
                    pass
                if stop.is_set():
                    return

    # ---- Lesen
    def read(self):
        ctx = self.page.ctx
        if not ctx.require(self):
            return
        self.hold()
        link, info = ctx.link, ctx.info
        self.b_read.setEnabled(False)
        self.state.setText(_("reading parameters …"))

        def work(w):
            sel = 0
            try:
                sel = link.request(Cmd.TRACK, [], timeout=1.0)[0]
            except Exception:                              # noqa: BLE001 – ältere Firmware: eine Spur
                pass
            n = max(1, getattr(info, "p_count", 0) or 64)
            out, dec = [], M.v14_dec
            for pid in range(n):
                w.progress.emit(pid, n)
                try:
                    a = link.request(Cmd.DESC, [0, pid], timeout=0.6, retries=0)
                except Exception:                          # noqa: BLE001
                    continue
                if pid == 0 and len(a) >= 9 and M.v14_dec(a[3], a[4]) <= -4096 and abs(r14(a, 3)) < 4096:
                    dec = r14_pair                         # Demo-Gerät: Zweierkomplement statt +8192
                try:
                    d = _parse_desc(a, dec)
                except (ProtocolError, IndexError):
                    continue
                if d.label.strip() in ("", "-", "--") or d.max <= d.min:
                    continue
                p = Param(pid, d)
                try:
                    r = link.request(Cmd.TRACK_PARAM, [sel, pid], timeout=0.6, retries=0)
                    p.value = dec(r[2], r[3])
                except Exception:                          # noqa: BLE001
                    pass
                out.append(p)
            return sel, dec, out
        run_job(self, work, on_done=self.values_ready.emit,
                on_progress=lambda a, t: self.state.setText(
                    _("reading parameter {n} of {total} … (names, ranges, values)").format(n=a + 1, total=t)),
                on_fail=lambda m, tb: (self.b_read.setEnabled(True),
                                       self.state.setText(_("Reading failed: {error}").format(error=m))))

    def _show(self, res):
        sel, dec, plist = res
        self.track = sel
        self.dec14 = dec
        self.enc14 = M.v14_enc if dec is M.v14_dec else (lambda v: [v & 127, (v >> 7) & 127])
        self.b_read.setEnabled(True)
        while self.grid.count():
            it = self.grid.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self.params = {p.id: p for p in plist}
        self.widgets.clear()
        self.rows = {}
        self.learning = None                               # die alten „Lernen“-Knöpfe sind weg (sonst KeyError in on_cc)
        for i, p in enumerate(plist):
            name = QLabel(f"{p.d.label}" + (f" ({p.d.unit})" if p.d.unit else ""))
            name.setToolTip(_("Parameter {id}, {min} … {max}").format(id=p.id, min=p.d.min, max=p.d.max))
            if p.d.fmt == 8 and p.d.names:
                w = QComboBox()
                w.addItems(p.d.names)
                w.setCurrentIndex(max(0, min(len(p.d.names) - 1, p.value - p.d.min)))
                w.currentIndexChanged.connect(lambda ix, pid=p.id: self.change(pid, self.params[pid].d.min + ix))
                val = QLabel("")
            else:
                w = QSlider(Qt.Orientation.Horizontal, minimum=p.d.min, maximum=p.d.max, value=p.value)
                val = QLabel(str(p.value))
                w.valueChanged.connect(lambda v, pid=p.id, lab=val: (lab.setText(str(v)), self.change(pid, v)))
            lb = QPushButton(_("Learn"))
            lb.setCheckable(True)
            lb.setToolTip(_("then move a knob on the learn input – clicking again cancels"))
            lb.toggled.connect(lambda on, pid=p.id, b=lb: self.start_learn(pid, b, on))
            m = self.mapping_for(p.id)
            if m:
                lb.setText(f"CC {m['cc']}")
            self.widgets[p.id] = w
            r, c = divmod(i, 2)
            for k, x in enumerate((name, w, val, lb)):
                self.grid.addWidget(x, r, c * 4 + k)
            self.rows[p.id] = (name, w, val, lb)
        self.grid.setColumnStretch(1, 1)
        self.grid.setColumnStretch(5, 1)
        if plist:
            self.state.setText(ngettext(
                "Track {track}: {n} parameter. Changes go to the FM-1 immediately (the embedded editor pauses while "
                "this tab is open and resumes by itself when you leave it).",
                "Track {track}: {n} parameters. Changes go to the FM-1 immediately (the embedded editor pauses while "
                "this tab is open and resumes by itself when you leave it).", len(plist)).format(
                track=sel + 1, n=len(plist)))
        else:
            self.state.setText(_("No parameters read – this firmware does not describe its parameters "
                                 "(DESC). Then control via the tab “Keyboard” (mod wheel) or the editor."))

    def forget(self, why: str):
        """anderes Gerät oder getrennt: keine veralteten Regler zeigen."""
        if not self.params and not getattr(self, "rows", None):
            return
        self.release()
        self._show((0, M.v14_dec, []))
        self.state.setText(_("{reason} – “Read from FM-1” fetches the parameters of the device connected "
                             "now.").format(reason=why))

    def _filter(self, txt):
        q = txt.lower().strip()
        for pid, row in getattr(self, "rows", {}).items():
            vis = not q or q in self.params[pid].d.label.lower()
            for x in row:
                x.setVisible(vis)

    def change(self, pid, v):
        if self.held_link is None:
            self.hold()
        with self.plock:
            self.pending[pid] = int(v)
        self.wake.set()

    # ---- MIDI-Learn
    def fill_learn(self):
        names, hint = mido_names(False)
        self.learn_in.clear()
        self.learn_in.addItem(_("– none –"), "")
        for n in names:
            self.learn_in.addItem(n, n)
        cur = _settings()["learn_port"]
        self.learn_in.setCurrentIndex(max(0, self.learn_in.findData(cur)))
        self.learn_in.currentIndexChanged.connect(self._learn_port)
        if hint:
            self.learn_in.setToolTip(hint)
        self._learn_port()

    def _learn_port(self, *_args):
        name = self.learn_in.currentData() or ""
        self.learn_name = name                             # für learn_feed (MIDI-Thread liest kein Widget)
        _settings()["learn_port"] = name
        _save()
        if self.learn_port is not None:
            try:
                self.learn_port.close()
            except Exception:                              # noqa: BLE001
                pass
            self.learn_port = None
        if name:
            try:
                import mido
                self.learn_port = mido.open_input(name, callback=lambda m: self.page.learn_feed(name, m.bytes()))
            except Exception as e:                         # noqa: BLE001
                self.state.setText(_("Learn input not available: {error}").format(error=e))

    def mapping_for(self, pid):
        return next((m for m in _settings()["learn"] if m["id"] == pid), None)

    def start_learn(self, pid, btn, on):
        if on:
            for _p, row in getattr(self, "rows", {}).items():
                if row[3] is not btn and row[3].isChecked():
                    row[3].setChecked(False)
            self.learning = pid
            btn.setText(_("move …"))
        else:
            if self.learning == pid:
                self.learning = None
            m = self.mapping_for(pid)
            btn.setText(f"CC {m['cc']}" if m else _("Learn"))

    def on_cc(self, ch, cc, val):
        """im GUI-Thread: CC vom Lern-Eingang."""
        if self.learning is not None:
            pid = self.learning
            lst = [m for m in _settings()["learn"] if m["id"] != pid and not (m["cc"] == cc and m["ch"] == ch)]
            lst.append({"ch": ch, "cc": cc, "id": pid, "label": self.params[pid].d.label})
            _settings()["learn"] = lst
            _save()
            self.learning = None
            self.rows[pid][3].setChecked(False)
            self.rows[pid][3].setText(f"CC {cc}")
            return
        for m in _settings()["learn"]:
            if m["cc"] == cc and m["ch"] == ch and m["id"] in self.params:
                d = self.params[m["id"]].d
                v = d.min + round(val * (d.max - d.min) / 127)
                w = self.widgets[m["id"]]
                if isinstance(w, QComboBox):
                    w.setCurrentIndex(v - d.min)
                else:
                    w.setValue(v)

    def clear_learn(self):
        _settings()["learn"] = []
        _save()
        for pid, row in getattr(self, "rows", {}).items():
            row[3].setText(_("Learn"))


def r14_pair(lo, hi):
    return r14([lo, hi])


def _parse_desc(a, dec):
    if dec is r14_pair:
        return parse_desc(a)
    # wie protocol.parse_desc, aber mit Melodees +8192-Werten
    from ...protocol import Desc, Reader
    r = Reader(a)
    scope, pid, fmt = r.b(), r.b(), r.b()
    vals = []
    for _i in range(3):
        lo, hi = r.b(), r.b()
        vals.append(M.v14_dec(lo, hi))
    lab, unit = r.s(), r.s()
    d = Desc(scope, pid, fmt, vals[0], vals[1], vals[2], lab, unit)
    if fmt == 8:
        try:
            while r.more() and len(d.names) <= d.max - d.min:
                d.names.append(r.s())
        except ProtocolError:
            pass
    return d


# ========================================================= Verbindungen ===
class RoutingTab(QWidget):
    ports_ready = pyqtSignal(object)

    def __init__(self, page):
        super().__init__()
        self.page = page
        self.ports: list[alsaseq.SeqPort] = []
        lay = QVBoxLayout(self)
        lay.addWidget(label(_("Connects MIDI ports directly in the ALSA sequencer (like “aconnect”): "
                              "check mark = the sender (row) sends to the receiver (column). The app's "
                              "own FM-1 connection runs via its own rtmidi ports (“RtMidi…”) – the table "
                              "shows them grey, they belong to the app."),
                            "muted"))
        row = QHBoxLayout()
        row.addWidget(button(_("Refresh"), "view-refresh", slot=self.refresh))
        self.show_sys = QCheckBox(_("Show system ports"))
        self.show_sys.toggled.connect(lambda _on: self._fill())
        row.addWidget(self.show_sys)
        row.addStretch(1)
        row.addWidget(button(_("Controller → FM-1"), "cable", slot=self.quick_controller,
                             tooltip=_("connect the first MIDI controller found (e.g. MiniLab) to the FM-1")))
        row.addWidget(button(_("FM-1 → sound generator"), "cable", slot=self.quick_fluid,
                             tooltip=_("MIDI output of the FM-1 to FluidSynth (page Sound Module)")))
        lay.addLayout(row)
        reflow(row)
        self.table = QTableWidget()
        self.table.cellClicked.connect(self._clicked)
        lay.addWidget(self.table, 1)
        self.empty = empty_state("cable", _("No MIDI ports found"),
                                 _("The ALSA sequencer is not reachable or “aconnect” is missing. How to "
                                   "fix: sudo apt install alsa-utils – and plug in a MIDI device."))
        lay.addWidget(self.empty)
        pr = QHBoxLayout()
        pr.addWidget(QLabel(_("Templates:")))
        self.presets = QComboBox()
        self.presets.setMinimumWidth(220)
        pr.addWidget(self.presets)
        pr.addWidget(button(_("Apply"), "media-playback-start", slot=self.apply_preset))
        pr.addWidget(button(_("Save current …"), "document-save", slot=self.save_preset))
        pr.addWidget(button(_("Delete"), "edit-delete", slot=self.delete_preset))
        pr.addStretch(1)
        lay.addLayout(pr)
        reflow(pr)
        self.msg = label("", "muted")
        lay.addWidget(self.msg)
        self.ports_ready.connect(self._got)
        self._fill_presets()

    def refresh(self):
        run_job(self, lambda w: alsaseq.list_ports(), on_done=self.ports_ready.emit,
                on_fail=lambda m, tb: self.msg.setText(_("Reading failed: {error}").format(error=m)))

    def _got(self, ports):
        self.ports = ports
        self._fill()

    def _fill(self):
        sysok = self.show_sys.isChecked()
        ports = [p for p in self.ports if sysok or not p.system]
        self.src = [p for p in ports if p.readable]
        self.dst = [p for p in ports if p.writable]
        has = bool(self.src and self.dst)
        self.table.setVisible(has)
        self.empty.setVisible(not has)
        con = alsaseq.connections(self.ports)
        t = self.table
        t.clear()
        t.setRowCount(len(self.src))
        t.setColumnCount(len(self.dst))
        t.setHorizontalHeaderLabels([f"{p.label}\n{p.addr}" for p in self.dst])
        t.setVerticalHeaderLabels([f"{p.label} ({p.addr})" for p in self.src])
        for r, s in enumerate(self.src):
            for c, d in enumerate(self.dst):
                it = QTableWidgetItem("✔" if (s.addr, d.addr) in con else "")
                it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                it.setFlags(Qt.ItemFlag.ItemIsEnabled)
                if s.app or d.app or s.client == d.client:
                    it.setFlags(Qt.ItemFlag.NoItemFlags)
                    it.setToolTip(_("belongs to the app (rtmidi)") if (s.app or d.app) else "")
                t.setItem(r, c, it)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)

    def _clicked(self, r, c):
        if r >= len(self.src) or c >= len(self.dst):
            return
        s, d = self.src[r], self.dst[c]
        if s.app or d.app or s.client == d.client:
            return
        on = (s.addr, d.addr) in alsaseq.connections(self.ports)
        self._do([(s.addr, d.addr)], disconnect=on)

    def _do(self, pairs, disconnect=False):
        def work(w):
            return [alsaseq.connect(a, b, disconnect) for a, b in pairs]

        def done(res):
            bad = [m for ok, m in res if not ok]
            self.msg.setText(_("Error: {errors}").format(errors="; ".join(bad)) if bad else
                             (_("disconnected") if disconnect else
                              ngettext("{n} connection made", "{n} connections made", len(pairs)).format(n=len(pairs))))
            self.refresh()
        run_job(self, work, on_done=done)

    def _fm1(self, readable):
        port = self.page.ctx.port
        if port and port.client is not None:
            p = next((p for p in self.ports if p.client == port.client and (p.readable if readable else p.writable)), None)
            if p:
                return p
        return alsaseq.find(self.ports, r"melodee|fm-1|fm1|felucca|sloop|skiff", readable)

    def quick_controller(self):
        fm = self._fm1(False)
        ctrl = alsaseq.find(self.ports, r"minilab|arturia|keystep|launchkey|keylab|akai|mpk|nanokey|controller|"
                                        r"keyboard|smk|novation", True)
        if not fm or not ctrl:
            self.msg.setText(_("No FM-1 found in the sequencer.") if not fm else
                             _("No controller found in the sequencer."))
            return
        self._do([(ctrl.addr, fm.addr)])

    def quick_fluid(self):
        fm = self._fm1(True)
        fl = alsaseq.find(self.ports, r"klangerzeuger|fluid", False)
        if not fm or not fl:
            self.msg.setText(_("Start the sound generator first (tab “Sound Module” on this page).") if not fl
                             else _("No FM-1 found in the sequencer."))
            return
        self._do([(fm.addr, fl.addr)])

    # ---- Vorlagen
    def _fill_presets(self):
        self.presets.clear()
        for d in _settings()["routing_presets"]:
            self.presets.addItem(d.get("name", _("Template")), d)

    def save_preset(self):
        name, ok = QInputDialog.getText(self, _("Save template"), _("Name (e.g. “MiniLab → FM-1”):"))
        if not ok or not name.strip():
            return
        pr = alsaseq.capture(name.strip(), self.ports)
        if not pr.pairs:
            self.msg.setText(_("No own connections to save."))
            return
        s = _settings()
        s["routing_presets"] = [d for d in s["routing_presets"] if d.get("name") != pr.name] + [pr.to_json()]
        _save()
        self._fill_presets()
        self.msg.setText(ngettext("Template “{name}” saved with {n} connection.",
                                  "Template “{name}” saved with {n} connections.", len(pr.pairs)).format(
            name=pr.name, n=len(pr.pairs)))

    def apply_preset(self):
        d = self.presets.currentData()
        if not d:
            return
        pairs, missing = alsaseq.resolve(alsaseq.Preset.from_json(d), self.ports)
        if missing:
            self.msg.setText(_("Not connected: {ports}").format(ports=", ".join(missing)))
        if pairs:
            self._do(pairs)

    def delete_preset(self):
        d = self.presets.currentData()
        if d:
            s = _settings()
            s["routing_presets"] = [x for x in s["routing_presets"] if x.get("name") != d.get("name")]
            _save()
            self._fill_presets()


# ================================================================ Seite ===
class MidiPage(QWidget):
    navigate = pyqtSignal(str)
    cc_in = pyqtSignal(int, int, int)

    def __init__(self, ctx, window=None):
        super().__init__()
        self.ctx = ctx
        self.window = window
        self.own_holds = 0
        self.outputs = Outputs()
        self.listening = None
        lay = QVBoxLayout(self)
        lay.addWidget(heading("MIDI", _("Everything that runs between the app, the FM-1 and other MIDI "
                                        "devices: monitor, play, set the tempo, adjust parameters and "
                                        "connect ports.")))
        self.tabs = QTabWidget()
        self.monitor = MonitorTab(self)
        self.keyboard = KeyboardTab(self)
        self.clock = ClockTab(self)
        self.controls = ControlsTab(self)
        self.routing = RoutingTab(self)
        for w, t, ic in ((self.monitor, _("Monitor"), "activity"), (self.keyboard, _("Keyboard"), "piano"),
                         (self.clock, _("Clock"), "metronome"), (self.controls, _("Controls"), "sliders-horizontal"),
                         (self.routing, _("Connections"), "cable")):
            self.tabs.addTab(w, icon(ic), t)
        self.tabs.currentChanged.connect(self._tab)
        lay.addWidget(self.tabs, 1)
        self.cc_in.connect(self.controls.on_cc)
        self._dev_tag = self._tag()
        ctx.changed.connect(self._dev_changed)
        self.active = False

    def _tag(self):
        port = getattr(self.ctx, "port", None)
        return (port.key if port is not None else None, self.firmware)

    @property
    def firmware(self) -> str:
        try:
            return self.ctx.firmware_key
        except Exception:                                  # noqa: BLE001
            return ""

    # ---- Senden
    def send_to(self, target, data, realtime=False) -> bool:
        if target == FM1 or not target:
            link = self.ctx.link
            if link is None or link.closed:
                return False
            # Echtzeit-Bytes dürfen laut Protokoll zwischen SysEx-Rahmen; nur wenn WIR das Gerät halten
            force = realtime and getattr(link, "_exclusive_depth", 0) <= self.own_holds
            try:
                return link.send_midi(list(data), force=force)
            except Exception:                              # noqa: BLE001
                return False
        try:
            self.outputs.send(target, data)
            return True
        except Exception:                                  # noqa: BLE001
            return False

    def learn_feed(self, port, b):
        """aus einem MIDI-Thread: CCs an Regler/MIDI-Learn."""
        if len(b) == 3 and b[0] & 0xF0 == 0xB0 and port == getattr(self.controls, "learn_name", ""):
            self.cc_in.emit((b[0] & 15) + 1, b[1], b[2])

    # ---- Mithören
    def _listen(self):
        link = self.ctx.link
        if link is self.listening:
            return
        self._unlisten()
        if link is not None:
            link.add_listener(self.monitor.on_link)
            self.listening = link

    def _unlisten(self):
        if self.listening is not None:
            self.listening.remove_listener(self.monitor.on_link)
            self.listening = None

    def _dev_changed(self):
        if self.active:
            self._listen()
        if self.controls.held_link is not None and self.controls.held_link is not self.ctx.link:
            self.controls.release()
        tag = self._tag()
        if tag != self._dev_tag:
            self._dev_tag = tag
            self.controls.forget(_("Device changed") if self.ctx.link is not None else _("FM-1 disconnected"))

    def _tab(self, i):
        if self.tabs.widget(i) is not self.keyboard and not self.keyboard.quiet():
            self.keyboard.all_off()                        # kein Hänger am FM-1, wenn der Reiter wechselt
        if self.tabs.widget(i) is not self.controls:
            self.controls.release()
        elif self.controls.params:
            self.controls.hold()
        if self.tabs.widget(i) is self.routing:
            self.routing.refresh()
        if self.tabs.widget(i) is self.keyboard:
            self.keyboard.piano.setFocus()

    def activate(self):
        self.active = True
        self._listen()
        self.monitor.timer.start()
        self.monitor.fill_inputs()
        self.keyboard.fill_outputs()
        self.clock.fill_outputs()
        self._tab(self.tabs.currentIndex())

    def deactivate(self):
        self.active = False
        if not self.keyboard.quiet():
            self.keyboard.all_off()
        self.keyboard.store()
        _save()
        self.controls.release()
        if not self.clock.master.running:              # läuft die Clock, hört der Monitor weiter mit
            self.monitor.timer.stop()
            self._unlisten()

    def open_with(self, tab: str = "", **_kw):
        names = {"monitor": self.monitor, "tastatur": self.keyboard, "takt": self.clock, "regler": self.controls,
                 "verbindungen": self.routing}
        if tab in names:
            self.tabs.setCurrentWidget(names[tab])

    def shutdown(self):
        self.clock.master.stop()
        self.controls.release()
        self._unlisten()
        self.monitor.close_inputs()
        if self.controls.learn_port is not None:
            try:
                self.controls.learn_port.close()
            except Exception:                              # noqa: BLE001
                pass
        self.outputs.close()


def _make(ctx, window):
    return MidiPage(ctx, window)


PAGE = PageSpec(key="midi", title=_("MIDI"), group="daten", factory=_make,
                icons=("audio-midi", "piano"), order=20,
                description=_("MIDI monitor, on-screen keyboard, clock, controls with MIDI learn, connections"),
                keywords=("monitor", "clock", "takt", "tastatur", "keyboard", "aconnect", "routing", "cc", "learn",
                          "tempo", "connections", "controller"))
