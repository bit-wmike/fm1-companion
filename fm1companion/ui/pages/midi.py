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
        return [], f"Weitere MIDI-Anschlüsse brauchen python3-rtmidi ({type(e).__name__})."



def _de(x: float, nd: int) -> str:
    """Zahl mit deutschem Dezimalkomma (die Oberfläche ist deutsch: „120,0 BPM“, nicht „120.0 BPM“)."""
    return f"{x:.{nd}f}".replace(".", ",")

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
FILTERS = [("note", "Noten"), ("cc", "Controller"), ("prog", "Programm"), ("bend", "Pitch-Bend"),
           ("at", "Aftertouch"), ("sysex", "SysEx"), ("transport", "Start/Stop"), ("clock", "Clock"),
           ("sense", "Active Sensing"), ("other", "Sonstiges")]
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
        self.search.setPlaceholderText("Suchen (z. B. „C4“, „CC 74“, „TRACK_PARAM“) …")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refilter)
        bar.addWidget(self.search, 1)
        self.dir = QComboBox()
        for k, t in (("", "beide Richtungen"), ("in", "nur vom Gerät ←"), ("out", "nur zum Gerät →")):
            self.dir.addItem(t, k)
        self.dir.currentIndexChanged.connect(self.refilter)
        bar.addWidget(self.dir)
        self.b_pause = button("Pause", "media-playback-pause", tooltip="Anzeige anhalten (Leertaste)")
        self.b_pause.setCheckable(True)
        self.b_pause.toggled.connect(self._pause)
        bar.addWidget(self.b_pause)
        bar.addWidget(button("Leeren", "edit-clear", slot=self.clear, tooltip="Protokoll verwerfen (nicht nur die Anzeige)"))
        bar.addWidget(button("Exportieren …", "document-save", slot=self.export,
                             tooltip="das gefilterte Protokoll als Text oder CSV speichern (bis 20 000 Nachrichten)"))
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
        self.table.setHorizontalHeaderLabels(["Zeit", "Richtung", "Port", "Typ", "Kanal", "Daten", "Bedeutung"])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setWordWrap(False)
        hh = self.table.horizontalHeader()
        for i, w in enumerate((80, 100, 110, 110, 50, 200)):
            self.table.setColumnWidth(i, w)
        hh.setStretchLastSection(True)
        lay.addWidget(self.table, 1)
        self.empty = empty_state("activity", "Spiel etwas am FM-1 – hier erscheinen die Nachrichten",
                                 "Noten, Regler, Clock und SysEx in beide Richtungen. Mit „Mithören“ unten kommen "
                                 "weitere MIDI-Eingänge (Controller, Klangerzeuger) dazu.")
        lay.addWidget(self.empty, 1)
        self.table.hide()
        st = QHBoxLayout()
        self.status = label("", "muted")
        st.addWidget(self.status, 1)
        st.addWidget(QLabel("Weiterer Eingang:"))
        self.in_combo = QComboBox()
        self.in_combo.setMinimumWidth(220)
        st.addWidget(self.in_combo)
        self.b_listen = button("Mithören", "list-add", slot=self.add_input,
                               tooltip="auch diesen MIDI-Eingang anzeigen (z. B. Controller, Klangerzeuger)")
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
            self.in_combo.addItem("kein weiterer MIDI-Eingang gefunden")
        self.in_combo.setEnabled(bool(names))
        self.b_listen.setEnabled(bool(names))
        self.in_combo.setToolTip(hint or ("MIDI-Eingänge des Systems (USB-Controller, Klangerzeuger)" if names else
                                          "Kein weiterer MIDI-Eingang angeschlossen – Controller anstecken, dann die "
                                          "Seite neu öffnen."))

    def add_input(self):
        name = self.in_combo.currentText()
        if not name or name in self.inputs:
            return
        try:
            import mido
            self.inputs[name] = mido.open_input(name, callback=self._on_input(name))
        except Exception as e:                             # noqa: BLE001
            show_error(self, "Eingang lässt sich nicht öffnen", f"{name}: {e}")
            return
        self.in_list.setText("hört mit: " + ", ".join(n.split(":")[0] for n in self.inputs))

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
            vals = (_de(e.t, 3).rjust(9), M.DIR_DE.get(e.direction, e.direction), e.port, e.dec.type,
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
        parts = [f"{n} Nachrichten" + (f" (Tabelle zeigt die letzten {MAX_ROWS})" if n > MAX_ROWS else "")]
        if self.skipped:
            parts.append(f"{self.skipped} bei Sturzflut nicht angezeigt (im Export enthalten)")
        if bpm:
            j = self.meter.jitter_ms
            parts.append(f"eingehende Clock: {_de(bpm, 1)} BPM" + (f" (±{_de(j, 2)} ms)" if j is not None else ""))
        if self.paused:
            parts.append("angehalten")
        self.status.setText(" · ".join(parts))

    def refilter(self, *_):
        self.table.setRowCount(0)
        self._append([e for e in list(self.log.events)[-MAX_ROWS:] if self.visible_kind(e)])

    def _filter_changed(self, *_):
        s = _settings()
        s["hide_clock"] = not self.cbs["clock"].isChecked()
        s["hide_sense"] = not self.cbs["sense"].isChecked()
        _save()
        self.refilter()

    def _pause(self, on):
        self.paused = on
        self.b_pause.setText("Weiter" if on else "Pause")
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
        fn, filt = QFileDialog.getSaveFileName(self, "MIDI-Protokoll speichern",
                                               str(start_dir("MIDI") / "midi-monitor.txt"),
                                               "Text (*.txt);;CSV-Tabelle (*.csv)")
        if not fn:
            return
        fmt = "csv" if fn.lower().endswith(".csv") or "CSV" in filt else "txt"
        events = [e for e in self.log.events if self.visible_kind(e)]
        try:
            Path(fn).write_text(M.export_text(events, fmt), "utf-8")
        except OSError as e:
            show_error(self, "MIDI-Protokoll speichern", fs_error_text(e, "Nicht gespeichert"))
            return
        notify(f"{len(events)} Nachrichten gespeichert: {Path(fn).name}", "success")


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
        self.setToolTip("Mit der Maus spielen oder mit der Tastatur: untere Reihe Y S X D C V … = C bis H, "
                        "obere Reihe Q 2 W 3 E … eine Oktave höher.")

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
        g1, g2, g3, g4 = (QHBoxLayout() for _ in range(4))
        g1.addWidget(QLabel("Ziel:"))
        self.out = QComboBox()
        self.out.setMinimumWidth(260)
        g1.addWidget(self.out)
        g2.addWidget(QLabel("Kanal:"))
        self.channel = QSpinBox(minimum=1, maximum=16, value=int(s["kb_channel"]))
        self.channel.setToolTip("Melodee: Kanal 1–4 = Spur 1–4 (bei ROUT CH1-4), SEL = gewählte Spur")
        g2.addWidget(self.channel)
        g3.addWidget(QLabel("Anschlag:"))
        self.vel = QSlider(Qt.Orientation.Horizontal, minimum=1, maximum=127, value=int(s["kb_velocity"]))
        self.vel.setMaximumWidth(140)
        self.vel.setMinimumWidth(100)
        self.vel_l = QLabel(str(self.vel.value()))
        self.vel.valueChanged.connect(lambda v: self.vel_l.setText(str(v)))
        g3.addWidget(self.vel)
        g3.addWidget(self.vel_l)
        g4.addWidget(button("", "go-down", slot=lambda: self.shift(-1), tooltip="Oktave tiefer (Bild ↓)"))
        self.oct_l = QLabel()
        g4.addWidget(self.oct_l)
        g4.addWidget(button("", "go-up", slot=lambda: self.shift(1), tooltip="Oktave höher (Bild ↑)"))
        for g in (g1, g2, g3, g4):
            row.addLayout(g)
        lay.addLayout(row)
        reflow(row, 12)
        mid = QHBoxLayout()
        wheels = QVBoxLayout()
        self.bend = QSlider(Qt.Orientation.Vertical, minimum=-8192, maximum=8191, value=0)
        self.bend.setToolTip("Pitch-Bend (springt beim Loslassen zurück)")
        self.bend.valueChanged.connect(lambda v: self.send([0xE0 | self.ch(), (v + 8192) & 127, (v + 8192) >> 7]))
        self.bend.sliderReleased.connect(lambda: self.bend.setValue(0))
        self.mod = QSlider(Qt.Orientation.Vertical, minimum=0, maximum=127, value=0)
        self.mod.setToolTip("Modulationsrad (CC 1)")
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
        self.sustain = QCheckBox("Sustain (CC 64)")
        self.sustain.setToolTip("Haltepedal: Noten klingen nach dem Loslassen weiter. Wird beim Verlassen der Seite gelöst.")
        self.sustain.toggled.connect(lambda on: self.send([0xB0 | self.ch(), 64, 127 if on else 0]))
        opts.addWidget(self.sustain)
        self.hold = QCheckBox("Akkord halten")
        self.hold.setToolTip("Tasten rasten ein: noch einmal drücken lässt los. Beim Verlassen der Seite wird alles gelöst.")
        self.hold.toggled.connect(lambda on: None if on else self.all_off())
        opts.addWidget(self.hold)
        opts.addWidget(button("Alle Noten aus", "media-playback-stop", slot=self.all_off,
                              tooltip="sendet Note aus für alles, was klingt, und CC 123"))
        opts.addStretch(1)
        self.info = label("", "muted")
        opts.addWidget(self.info)
        lay.addLayout(opts)
        lay.addWidget(label("Tipp: Klick in die Tastatur, dann spielen mit Y S X D C … und Q 2 W 3 E … "
                            "Für Melodee-Spur 5–8 (MIDI-Spuren) den Kanal der Spur wählen und als Ziel "
                            "„FM-1 Klangerzeuger“ – oder direkt das FM-1 auf Kanal 1–4.", "muted"))
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
        self.out.addItem(icon("audio-midi"), "FM-1 (App-Verbindung)", FM1)
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
        self.info.setText("" if ok else "nicht gesendet – FM-1 beschäftigt oder nicht verbunden")
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
        self.oct_l.setText(f"ab {M.note_name(self.piano.base)}")
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
        c = Card("Taktgeber (Clock-Master)", icon="metronome",
                 subtitle="Die App gibt das Tempo vor: 24 Pulse pro Viertel, Start/Stop/Weiter. Melodee folgt, "
                          "wenn am FM-1 GLO › CLK auf „USB“ steht (40–240 BPM). Der Takt läuft weiter, wenn du die "
                          "Seite wechselst – „Stopp“ hier oder beim Beenden der App.")
        g = QGridLayout()
        g.addWidget(QLabel("Tempo:"), 0, 0)
        self.bpm = QDoubleSpinBox(minimum=30, maximum=300, decimals=1, value=float(s["bpm"]), suffix=" BPM")
        self.bpm.setSingleStep(1)
        self.bpm.valueChanged.connect(self._bpm)
        g.addWidget(self.bpm, 0, 1)
        g.addWidget(button("Tap", "hand", slot=self._tap, tooltip="im Takt klicken (oder T drücken)"), 0, 2)
        g.addWidget(QLabel("Swing:"), 1, 0)
        self.swing = QSlider(Qt.Orientation.Horizontal, minimum=0, maximum=50, value=int(s["swing"]))
        self.swing_l = QLabel()
        self.swing.valueChanged.connect(self._swing)
        g.addWidget(self.swing, 1, 1)
        g.addWidget(self.swing_l, 1, 2)
        g.addWidget(QLabel("Ziel:"), 2, 0)
        self.out = QComboBox()
        g.addWidget(self.out, 2, 1, 1, 2)
        c.lay.addLayout(g)
        row = QHBoxLayout()
        self.b_start = button("Start", "media-playback-start", "primary", self.start,
                              "sendet MIDI Start (von vorn) und dann 24 Pulse je Viertel")
        self.b_cont = button("Weiter", "media-skip-forward", slot=lambda: self.start(True),
                             tooltip="sendet MIDI Continue: läuft an der Stelle weiter, an der gestoppt wurde")
        self.b_stop = button("Stopp", "media-playback-stop", slot=self.stop, tooltip="sendet MIDI Stop und beendet den Takt")
        for b in (self.b_start, self.b_cont, self.b_stop):
            row.addWidget(b)
        row.addStretch(1)
        c.lay.addLayout(row)
        self.state = label("gestoppt", "muted")
        c.lay.addWidget(self.state)
        lay.addWidget(c)
        f = Card("Eingehende Clock (Follower)", icon="activity",
                 subtitle="Misst Tempo und Gleichmäßigkeit einer Clock, die vom FM-1 oder einem mitgehörten "
                          "Eingang (Reiter Monitor) kommt.")
        self.follow = label("keine Clock empfangen", "")
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
        self.out.addItem("FM-1 (App-Verbindung)", FM1)
        for n in mido_names(True)[0]:
            self.out.addItem(n, n)
        self.out.setCurrentIndex(max(0, self.out.findData(cur)))

    def _send(self, data):                       # aus dem Clock-Thread
        if not self.page.send_to(self._target, data, realtime=True):
            raise RuntimeError("nicht gesendet")

    def start(self, cont=False):
        self._target = self.out.currentData() or FM1
        if self._target == FM1 and (self.page.ctx.link is None or self.page.ctx.link.closed):
            self.state.setText("Nicht gestartet: kein FM-1 verbunden. FM-1 anstecken oder oben ein anderes Ziel wählen.")
            return
        s = _settings()
        s.update(bpm=self.bpm.value(), swing=self.swing.value(), clock_out=self._target)
        _save()
        self.master.start(cont)
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
        self.swing_l.setText(f"{50 + v // 2} %" if v else "aus")

    def refresh(self):
        m = self.master
        if m.running:
            st = m.jitter_stats()
            j = (f" · Genauigkeit: Pulse im Mittel {_de(st['mean_ms'], 2)} ms neben der Sollzeit, 95 % unter "
                 f"{_de(st['p95_ms'], 2)} ms (unter 1 ms ist unhörbar)") if st.get("n") else ""
            err = f" · {m.send_errors} Pulse nicht gesendet (Ziel weg?)" if m.send_errors else ""
            self.state.setText(f"läuft: {_de(m.bpm, 1)} BPM, {m.pulses} Pulse ({m.pulses // 96 + 1}. Takt){j}{err}")
        else:
            self.state.setText("gestoppt")
        mt = self.page.monitor.meter
        if mt.alive():
            j = mt.jitter_ms
            self.follow.setText(f"{_de(mt.bpm, 1)} BPM" + (f", Schwankung ±{_de(j, 2)} ms zwischen den Pulsen" if j is not None else "")
                                if mt.bpm else "Clock erkannt, messe …")
        else:
            self.follow.setText("keine Clock empfangen")


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
        self.b_read = button("Vom FM-1 lesen", "view-refresh", "primary", self.read)
        top.addWidget(self.b_read)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Parameter suchen …")
        self.filter.textChanged.connect(self._filter)
        top.addWidget(self.filter, 1)
        top.addWidget(QLabel("Lern-Eingang:"))
        self.learn_in = QComboBox()
        self.learn_in.setMinimumWidth(200)
        top.addWidget(self.learn_in)
        top.addWidget(button("Zuordnungen löschen", "edit-clear", slot=self.clear_learn))
        lay.addLayout(top)
        self.state = label("Noch nichts gelesen – so geht's: „Vom FM-1 lesen“ holt Namen, Bereiche und Werte der "
                           "gewählten Spur. Dann Regler bewegen; mit „Lernen“ einen Knopf deines Controllers zuordnen.",
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
        self.state.setText("lese Parameter …")

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
                on_progress=lambda a, t: self.state.setText(f"lese Parameter {a + 1} von {t} … (Namen, Bereiche, Werte)"),
                on_fail=lambda m, tb: (self.b_read.setEnabled(True), self.state.setText(f"Lesen fehlgeschlagen: {m}")))

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
            name.setToolTip(f"Parameter {p.id}, {p.d.min} … {p.d.max}")
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
            lb = QPushButton("Lernen")
            lb.setCheckable(True)
            lb.setToolTip("danach einen Knopf am Lern-Eingang bewegen – noch einmal klicken bricht ab")
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
            self.state.setText(f"Spur {sel + 1}: {len(plist)} Parameter. Änderungen gehen sofort ans FM-1 "
                               "(der eingebettete Editor pausiert, solange dieser Reiter offen ist, und läuft "
                               "beim Verlassen von selbst weiter).")
        else:
            self.state.setText("Keine Parameter gelesen – diese Firmware beschreibt ihre Parameter nicht (DESC). "
                               "Regeln geht dann über den Reiter „Tastatur“ (Mod-Rad) oder den Editor.")

    def forget(self, why: str):
        """anderes Gerät oder getrennt: keine veralteten Regler zeigen."""
        if not self.params and not getattr(self, "rows", None):
            return
        self.release()
        self._show((0, M.v14_dec, []))
        self.state.setText(f"{why} – „Vom FM-1 lesen“ holt die Parameter des jetzt verbundenen Geräts.")

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
        self.learn_in.addItem("– kein –", "")
        for n in names:
            self.learn_in.addItem(n, n)
        cur = _settings()["learn_port"]
        self.learn_in.setCurrentIndex(max(0, self.learn_in.findData(cur)))
        self.learn_in.currentIndexChanged.connect(self._learn_port)
        if hint:
            self.learn_in.setToolTip(hint)
        self._learn_port()

    def _learn_port(self, *_):
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
                self.state.setText(f"Lern-Eingang nicht verfügbar: {e}")

    def mapping_for(self, pid):
        return next((m for m in _settings()["learn"] if m["id"] == pid), None)

    def start_learn(self, pid, btn, on):
        if on:
            for _p, row in getattr(self, "rows", {}).items():
                if row[3] is not btn and row[3].isChecked():
                    row[3].setChecked(False)
            self.learning = pid
            btn.setText("bewegen …")
        else:
            if self.learning == pid:
                self.learning = None
            m = self.mapping_for(pid)
            btn.setText(f"CC {m['cc']}" if m else "Lernen")

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
            row[3].setText("Lernen")


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
    for _ in range(3):
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
        lay.addWidget(label("Verbindet MIDI-Anschlüsse direkt im ALSA-Sequencer (wie „aconnect“): Haken = der Sender "
                            "(Zeile) schickt an den Empfänger (Spalte). Die FM-1-Verbindung der App selbst läuft über "
                            "eigene rtmidi-Anschlüsse („RtMidi…“) – die zeigt die Tabelle grau, sie gehören der App.",
                            "muted"))
        row = QHBoxLayout()
        row.addWidget(button("Aktualisieren", "view-refresh", slot=self.refresh))
        self.show_sys = QCheckBox("System-Ports zeigen")
        self.show_sys.toggled.connect(lambda _: self._fill())
        row.addWidget(self.show_sys)
        row.addStretch(1)
        row.addWidget(button("Controller → FM-1", "cable", slot=self.quick_controller,
                             tooltip="ersten gefundenen MIDI-Controller (z. B. MiniLab) mit dem FM-1 verbinden"))
        row.addWidget(button("FM-1 → Klangerzeuger", "cable", slot=self.quick_fluid,
                             tooltip="MIDI-Ausgang des FM-1 an FluidSynth (Seite Klangerzeuger)"))
        lay.addLayout(row)
        reflow(row)
        self.table = QTableWidget()
        self.table.cellClicked.connect(self._clicked)
        lay.addWidget(self.table, 1)
        self.empty = empty_state("cable", "Keine MIDI-Anschlüsse gefunden",
                                 "Der ALSA-Sequencer ist nicht erreichbar oder „aconnect“ fehlt. "
                                 "So geht's: sudo apt install alsa-utils – und ein MIDI-Gerät anstecken.")
        lay.addWidget(self.empty)
        pr = QHBoxLayout()
        pr.addWidget(QLabel("Vorlagen:"))
        self.presets = QComboBox()
        self.presets.setMinimumWidth(220)
        pr.addWidget(self.presets)
        pr.addWidget(button("Anwenden", "media-playback-start", slot=self.apply_preset))
        pr.addWidget(button("Aktuelle speichern …", "document-save", slot=self.save_preset))
        pr.addWidget(button("Löschen", "edit-delete", slot=self.delete_preset))
        pr.addStretch(1)
        lay.addLayout(pr)
        reflow(pr)
        self.msg = label("", "muted")
        lay.addWidget(self.msg)
        self.ports_ready.connect(self._got)
        self._fill_presets()

    def refresh(self):
        run_job(self, lambda w: alsaseq.list_ports(), on_done=self.ports_ready.emit,
                on_fail=lambda m, tb: self.msg.setText(f"Lesen fehlgeschlagen: {m}"))

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
                    it.setToolTip("gehört der App (rtmidi)" if (s.app or d.app) else "")
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
            self.msg.setText("Fehler: " + "; ".join(bad) if bad else
                             ("getrennt" if disconnect else f"{len(pairs)} Verbindung(en) hergestellt"))
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
            self.msg.setText("Kein " + ("FM-1" if not fm else "Controller") + " im Sequencer gefunden.")
            return
        self._do([(ctrl.addr, fm.addr)])

    def quick_fluid(self):
        fm = self._fm1(True)
        fl = alsaseq.find(self.ports, r"klangerzeuger|fluid", False)
        if not fm or not fl:
            self.msg.setText("Erst den Klangerzeuger starten (Reiter „Klangerzeuger“ auf dieser Seite)." if not fl
                             else "Kein FM-1 im Sequencer gefunden.")
            return
        self._do([(fm.addr, fl.addr)])

    # ---- Vorlagen
    def _fill_presets(self):
        self.presets.clear()
        for d in _settings()["routing_presets"]:
            self.presets.addItem(d.get("name", "Vorlage"), d)

    def save_preset(self):
        name, ok = QInputDialog.getText(self, "Vorlage speichern", "Name (z. B. „MiniLab → FM-1“):")
        if not ok or not name.strip():
            return
        pr = alsaseq.capture(name.strip(), self.ports)
        if not pr.pairs:
            self.msg.setText("Keine eigenen Verbindungen zum Speichern.")
            return
        s = _settings()
        s["routing_presets"] = [d for d in s["routing_presets"] if d.get("name") != pr.name] + [pr.to_json()]
        _save()
        self._fill_presets()
        self.msg.setText(f"Vorlage „{pr.name}“ mit {len(pr.pairs)} Verbindung(en) gespeichert.")

    def apply_preset(self):
        d = self.presets.currentData()
        if not d:
            return
        pairs, missing = alsaseq.resolve(alsaseq.Preset.from_json(d), self.ports)
        if missing:
            self.msg.setText("Nicht angeschlossen: " + ", ".join(missing))
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
        lay.addWidget(heading("MIDI", "Alles, was zwischen App, FM-1 und anderen MIDI-Geräten läuft: mitlesen, "
                                      "spielen, Tempo vorgeben, Parameter regeln und Anschlüsse verbinden."))
        self.tabs = QTabWidget()
        self.monitor = MonitorTab(self)
        self.keyboard = KeyboardTab(self)
        self.clock = ClockTab(self)
        self.controls = ControlsTab(self)
        self.routing = RoutingTab(self)
        for w, t, ic in ((self.monitor, "Monitor", "activity"), (self.keyboard, "Tastatur", "piano"),
                         (self.clock, "Takt", "metronome"), (self.controls, "Regler", "sliders-horizontal"),
                         (self.routing, "Verbindungen", "cable")):
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
            self.controls.forget("Gerät gewechselt" if self.ctx.link is not None else "FM-1 getrennt")

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

    def open_with(self, tab: str = "", **_):
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


PAGE = PageSpec(key="midi", title="MIDI", group="daten", factory=_make,
                icons=("audio-midi", "piano"), order=20,
                description="MIDI-Monitor, Bildschirm-Tastatur, Clock, Regler mit MIDI-Learn, Verbindungen",
                keywords=("monitor", "clock", "takt", "tastatur", "keyboard", "aconnect", "routing", "cc", "learn"))
