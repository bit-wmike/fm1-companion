# SPDX-License-Identifier: GPL-3.0-only
"""Komponieren: Rhythmen, Akkordfolgen, Bass, Arpeggios und Melodien erzeugen, am Rechner vorhören und bearbeiten,
dann in ein Pattern einer FM-1-Spur schreiben (mit Kontroll-Lesen und Rückgängig)."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QSizePolicy, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
                             QLineEdit, QScrollArea, QSlider, QSpinBox, QSplitter, QTabWidget, QVBoxLayout, QWidget,
                             QGridLayout)

from ... import generators as G, midi_import as MI, midiexport as ME, projectfmt as PF
from ..common import (Card, DeviceUndo, Disclosure, button, confirm, device_tag, flow_row, fs_error_text, heading,
                      label, notify, run_job, safe_folder, show_error)
from ..musik_widgets import EditHistory, StepRoll
from ..playback_bar import PlaybackBar, Source, device_name
from ..responsive import adaptive_splitter, fit_height, on_narrow
from . import PageSpec
from ...i18n import _


def _spin(lo, hi, val, suffix="", tip=""):
    s = QSpinBox()
    s.setRange(lo, hi)
    s.setValue(val)
    if suffix:
        s.setSuffix(suffix)
    if tip:
        s.setToolTip(tip)
    return s


class ComposePage(QWidget):
    def __init__(self, ctx, window=None):
        super().__init__()
        self.ctx, self.window = ctx, window
        self.undo = DeviceUndo()                  # MI.PatternSnapshot je Gerät
        self.markov: G.Markov | None = None
        self.history = EditHistory()              # Rückgängig/Wiederholen für alle Bearbeitungen am Rechner
        self._restoring = False
        lay = QVBoxLayout(self)
        lay.addWidget(heading("Komponieren", "Ideen erzeugen statt Steps tippen: euklidische Rhythmen, Akkordfolgen, "
                              "Bass, Arpeggios und Melodien. Vorhören – direkt auf dem FM-1 mit dem Klang der Zielspur –, "
                              "in der Piano-Roll anpassen (Strg+Z / Strg+Umschalt+Z), dann in ein Pattern am FM-1 "
                              "schreiben – vorher wird gelesen, damit du es rückgängig machen kannst."))
        split = QSplitter(Qt.Orientation.Horizontal)
        self.split = split
        self.left = self._build_left()
        split.addWidget(self.left)
        split.addWidget(self._build_center())
        split.setStretchFactor(1, 3)
        lay.addWidget(split, 1)
        # schmal: Einstellungen über der Vorschau (Splitter, beliebig verschiebbar); breit: nebeneinander
        adaptive_splitter(split, ratios=(2, 3), slack=16)
        QShortcut(QKeySequence("Ctrl+G"), self, activated=self.generate)
        QShortcut(QKeySequence("Space"), self, activated=self._toggle_preview)
        for seq, fn in (("Ctrl+Z", self.undo_edit), ("Ctrl+Shift+Z", self.redo_edit), ("Ctrl+Y", self.redo_edit)):
            QShortcut(QKeySequence(seq), self, activated=fn)     # Fenster-weit, solange die Seite sichtbar ist;
            #                                                    Textfelder behalten ihr eigenes Strg+Z
        self.generate()
        self.history.clear()                      # der Startzustand ist nichts, wohin man zurück will
        self._history_buttons()
        ctx.changed.connect(self._dev_changed)
        self._dev_changed()

    def _dev_changed(self):
        """anderes Gerät: Rückgängig bleibt gespeichert, ist aber nur am ursprünglichen FM-1 erlaubt."""
        name = device_name(self.ctx)
        self.dev_card_title(f"Auf das FM-1 „{name}“" if name else "Auf das FM-1 (keins verbunden)")
        ok = self.undo.matches(self.ctx)
        self.b_undo.setEnabled(bool(self.undo))
        self.b_undo.setToolTip("Das zuletzt überschriebene Pattern wiederherstellen" if ok or not self.undo
                               else self.undo.refusal())

    # ------------------------------------------------------------------------------------- Aufbau
    def _build_left(self) -> QWidget:
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        common = Card("Grundlagen")
        f = QFormLayout()
        self.key = QComboBox()
        self.key.addItems(G.NOTE_NAMES)
        self.key.setToolTip("Grundton der Tonart")
        self.scale = QComboBox()
        self.scale.addItems(list(G.SCALES))
        self.length = _spin(1, 64, 16, " Steps", "Länge des Patterns (LEN am FM-1)")
        self.bpm = _spin(40, 240, 110, " BPM", "Tempo fürs Vorhören (das FM-1 behält sein eigenes)")
        self.seed = _spin(1, 99999, 1, "", "Zufalls-Startwert: gleicher Wert = gleiches Ergebnis")
        seedrow = QHBoxLayout()
        seedrow.addWidget(self.seed, 1)
        seedrow.addWidget(button("Würfeln", ("roll", "media-playlist-shuffle"), slot=self._reseed,
                                 tooltip="neuen Startwert ziehen und neu erzeugen"))
        f.addRow("Tonart:", self.key)
        f.addRow("Skala:", self.scale)
        f.addRow("Länge:", self.length)
        f.addRow("Tempo:", self.bpm)
        f.addRow("Seed:", seedrow)
        common.lay.addLayout(f)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._tab_rhythm(), "Rhythmus")
        self.tabs.addTab(self._tab_chords(), "Akkorde")
        self.tabs.addTab(self._tab_bass(), "Bass/Arp")
        self.tabs.addTab(self._tab_melody(), "Melodie")
        self.tabs.setCurrentIndex(1)
        # Grundlagen und Reiter rollen gemeinsam – auch bei 720 px Höhe bleibt alles erreichbar
        inner = QWidget()
        iv = QVBoxLayout(inner)
        iv.setContentsMargins(0, 0, 0, 0)
        iv.addWidget(common)
        iv.addWidget(self.tabs, 1)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QScrollArea.Shape.NoFrame)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sc.setWidget(inner)
        self.left_scroll = sc
        v.addWidget(sc, 1)
        self.tabs.currentChanged.connect(lambda _i: QTimer.singleShot(0, self._fit_left))   # z. B. nach Zoom
        for cb in box.findChildren(QComboBox):          # lange Einträge dürfen die Spalte nicht verbreitern
            cb.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            cb.setMinimumContentsLength(10)
        v.addWidget(button("Erzeugen", ("system-run", "media-playback-start"), "", self.generate,
                           "Mit den Einstellungen des offenen Reiters neu erzeugen (Strg+G) – ändert nichts am FM-1"))
        box.setMinimumWidth(330)
        QTimer.singleShot(0, self._fit_left)
        return box

    def _fit_left(self):
        """linke Spalte so breit wie der breiteste Reiter es braucht (Rhythmus-Raster), mindestens 330 px –
        sonst würde das Raster ohne waagrechte Rollleiste abgeschnitten. Fest (nicht je Reiter), damit ein
        Reiterwechsel nie das ganze Layout umklappt."""
        sc = self.left_scroll
        tabs = max((self.tabs.widget(i).minimumSizeHint().width() for i in range(self.tabs.count())), default=0)
        need = max(tabs + 8, sc.widget().minimumSizeHint().width()) + sc.verticalScrollBar().sizeHint().width() + 4
        self.left.setMinimumWidth(max(330, need))

    def _place_desc(self, narrow: bool):
        bar, below = self._bar, self._desc_below
        in_bar = bar.indexOf(self.desc) >= 0
        if narrow and in_bar:
            i = bar.indexOf(self.desc)
            bar.removeWidget(self.desc)
            bar.insertStretch(i, 1)
            below.addWidget(self.desc)
        elif not narrow and not in_bar:
            below.removeWidget(self.desc)
            item = bar.takeAt(1)                      # Platzhalter-Dehnung hinter dem Einstellungen-Knopf
            del item
            bar.insertWidget(1, self.desc, 1)

    def toggle_settings(self, on: bool):
        """Einstellungen ein-/ausklappen – mehr Platz für die Piano-Roll (z. B. bei 1280×720)."""
        self.left.setVisible(on)
        self.b_settings.setChecked(on)

    def _tab_rhythm(self) -> QWidget:
        w = QWidget()
        g = QGridLayout(w)
        g.addWidget(QLabel("<b>Lane</b>"), 0, 0)
        for c, t in enumerate(("Steps", "Schläge", "Versatz", "Akzent")):
            g.addWidget(QLabel(f"<b>{t}</b>"), 0, c + 1)
        defaults = {0: (16, 4, 0, 1), 1: (16, 2, 4, 0), 3: (16, 8, 0, 2), 4: (16, 2, 2, 0)}
        self.lane_rows = []
        for lane in range(8):
            on = QCheckBox(PF.LANES[lane])
            st, pu, ro, ac = defaults.get(lane, (16, 3, 0, 0))
            on.setChecked(lane in defaults)
            row = (on, _spin(1, 64, st, "", "Zykluslänge"), _spin(0, 64, pu, "", "Anzahl Schläge, gleichmäßig verteilt"),
                   _spin(0, 63, ro, "", "Verschiebung nach rechts"), _spin(0, 16, ac, "", "jeder n-te Schlag mit Akzent (0 = keiner)"))
            for c, wdg in enumerate(row):
                if c:
                    wdg.setMinimumWidth(58)          # schmale Zahlenfelder: Raster passt in die Einstellungs-Spalte
                g.addWidget(wdg, lane + 1, c)
            self.lane_rows.append(row)
        g.addWidget(label("Euklidische Rhythmen verteilen n Schläge so gleichmäßig wie möglich – E(3,8) ist der "
                          "Tresillo. Das Ergebnis landet im Drum-Grid (für die DRUM-Engine oder jede andere Spur).",
                          "muted"), 10, 0, 1, 5)
        return w

    def _tab_chords(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        self.prog_preset = QComboBox()
        self.prog_preset.addItems(list(G.PROGRESSIONS))
        self.prog = QLineEdit(G.PROGRESSIONS[self.prog_preset.currentText()])
        self.prog.setToolTip("Stufen in römischen Zahlen: groß = Dur, klein = Moll, 7 / maj7 / ° / sus4, b/# davor")
        self.prog_preset.currentTextChanged.connect(lambda t: self.prog.setText(G.PROGRESSIONS[t]))
        self.voicing = QComboBox()
        self.voicing.addItems(["eng", "weit", "drop-2", "Grundton tief"])
        self.inversion = _spin(0, 3, 0, "", "Umkehrung des ersten Akkords")
        self.smooth = QCheckBox("Stimmführung glätten")
        self.smooth.setChecked(True)
        self.smooth.setToolTip("Umkehrungen so wählen, dass sich die Stimmen möglichst wenig bewegen")
        self.octave = _spin(2, 6, 4, "", "Oktave der Akkorde")
        self.rhythm = QComboBox()
        self.rhythm.addItems(list(G.RHYTHMS))
        self.per_chord = _spin(1, 32, 4, " Steps", "Dauer eines Akkords")
        f.addRow("Vorlage:", self.prog_preset)
        f.addRow("Folge:", self.prog)
        f.addRow("Voicing:", self.voicing)
        f.addRow("Umkehrung:", self.inversion)
        f.addRow("", self.smooth)
        f.addRow("Oktave:", self.octave)
        f.addRow("Rhythmus:", self.rhythm)
        f.addRow("je Akkord:", self.per_chord)
        f.addRow(label("Das FM-1 spielt bis zu 4 Noten pro Step – Akkorde werden darauf begrenzt.", "muted"))
        return w

    def _tab_bass(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        self.bass_kind = QComboBox()
        self.bass_kind.addItems(["Bass: Grundton", "Bass: Oktaven", "Bass: Achtel-Puls", "Bass: Walking"] +
                                [f"Arp: {m}" for m in G.ARP_MODES])
        self.arp_oct = _spin(1, 3, 1, " Oktaven", "Umfang des Arpeggios")
        f.addRow("Art:", self.bass_kind)
        f.addRow("Arp-Umfang:", self.arp_oct)
        f.addRow(label("Bass und Arpeggio folgen der Akkordfolge aus dem Reiter „Akkorde“.", "muted"))
        return w

    def _tab_melody(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        self.mel_kind = QComboBox()
        self.mel_kind.addItems(["Zufallsmelodie mit Regeln", "Markov (aus MIDI-Datei gelernt)", "Frage und Antwort"])
        self.low = _spin(24, 108, 60, "", "tiefste Note")
        self.high = _spin(24, 108, 79, "", "höchste Note")
        self.leap = _spin(1, 7, 2, " Stufen", "größter Sprung in Skalenstufen")
        self.rest = _spin(0, 90, 20, " %", "Wahrscheinlichkeit einer Pause")
        self.repeat = _spin(0, 90, 10, " %", "Wahrscheinlichkeit, die Note zu wiederholen")
        self.tie = _spin(0, 90, 10, " %", "Wahrscheinlichkeit, eine Note zu halten")
        self.markov_order = _spin(1, 2, 1, "", "1 = nur letzter Ton, 2 = letzte zwei Töne")
        self.markov_label = QLabel("noch nichts gelernt")
        mrow = QHBoxLayout()
        mrow.addWidget(button("MIDI-Datei lernen …", ("document-open",), slot=self.learn_markov,
                              tooltip="Tonfolgen und Rhythmen aus einer MIDI-Datei lernen"))
        mrow.addWidget(self.markov_label, 1)
        f.addRow("Art:", self.mel_kind)
        f.addRow("Tonumfang von:", self.low)
        f.addRow("bis:", self.high)
        f.addRow("Sprünge bis:", self.leap)
        f.addRow("Pausen:", self.rest)
        f.addRow("Wiederholung:", self.repeat)
        f.addRow("Halten:", self.tie)
        f.addRow("Markov-Ordnung:", self.markov_order)
        f.addRow(mrow)
        return w

    def _build_center(self) -> QWidget:
        w = QWidget()
        top = QWidget()                                 # oben: Vorschau (Piano-Roll) – im Splitter größer ziehbar
        v = QVBoxLayout(top)
        v.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        self.b_settings = button("", ("configure", "preferences-system"), slot=None,
                                 tooltip="Einstellungen ein- oder ausklappen – mehr Platz für die Piano-Roll")
        self.b_settings.setCheckable(True)
        self.b_settings.setChecked(True)
        self.b_settings.toggled.connect(self.toggle_settings)
        bar.addWidget(self.b_settings)
        self.desc = QLabel("")
        self.desc.setWordWrap(True)
        self.desc.setMinimumWidth(80)
        bar.addWidget(self.desc, 1)
        # nur Symbole: die Kopfzeile darf nicht breiter werden (sonst kippt breit/schmal bei ~900 px hin und her)
        self.b_hist_undo = button("", ("edit-undo",), slot=self.undo_edit,
                                  tooltip="Letzte Bearbeitung zurücknehmen (Strg+Z) – Piano-Roll, Erzeugen, Feinschliff")
        self.b_hist_redo = button("", ("edit-redo",), slot=self.redo_edit,
                                  tooltip="Zurückgenommene Bearbeitung wiederholen (Strg+Umschalt+Z oder Strg+Y)")
        for b in (self.b_hist_undo, self.b_hist_redo):
            b.setAccessibleName("Rückgängig" if b is self.b_hist_undo else "Wiederholen")
            bar.addWidget(b)
        bar.addWidget(button("Leeren", ("edit-clear",), slot=self.clear, tooltip="alle Steps löschen"))
        v.addLayout(bar)
        self.play_bar = PlaybackBar(self.ctx, self.play_source, self, loop=True, play_text="Vorhören",
                                    shortcut_hint="Leertaste", compact=True)
        v.addWidget(self.play_bar)
        self._bar, self._desc_below = bar, QHBoxLayout()   # schmale Vorschau: Beschreibung unter die Knöpfe
        v.addLayout(self._desc_below)
        on_narrow(top, 600, self._place_desc)
        self.roll = StepRoll(editable=True)
        self.roll.about_to_change.connect(lambda: self._remember("Piano-Roll"))
        self.roll.changed.connect(self._roll_changed)
        sc = QScrollArea()
        sc.setWidget(self.roll)
        v.addWidget(sc, 1)
        zrow = QHBoxLayout()
        zrow.addWidget(QLabel("Zoom:"))
        self.zoom = QSlider(Qt.Orientation.Horizontal)
        self.zoom.setRange(10, 48)
        self.zoom.setValue(24)
        self.zoom.setMaximumWidth(180)
        self.zoom.valueChanged.connect(self._zoom)
        zrow.addWidget(self.zoom)
        zrow.addStretch(1)
        v.addLayout(zrow)
        bottom = QWidget()                              # unten: Feinschliff und „Auf das FM-1“
        v = QVBoxLayout(bottom)
        v.setContentsMargins(0, 0, 0, 0)
        hum = QWidget()                                 # eingeklappt, damit die Piano-Roll auch auf 1280×720 Platz hat
        h = QGridLayout(hum)
        h.setContentsMargins(0, 0, 0, 0)
        self.curve = QComboBox()
        self.curve.addItems(G.VEL_CURVES)
        self.amount = _spin(0, 60, 12, "", "Stärke der Anschlag-Änderung")
        self.chance = _spin(10, 100, 100, " %", "Wahrscheinlichkeit, mit der Steps spielen (Melodee-Chance)")
        self.offbeat = QCheckBox("nur auf Offbeats")
        self.offbeat.setChecked(True)
        h.addWidget(QLabel("Anschlag:"), 0, 0)
        h.addWidget(self.curve, 0, 1)
        h.addWidget(self.amount, 0, 2)
        h.addWidget(button("Anwenden", ("tools-wizard",), slot=self.humanize), 0, 3)
        h.addWidget(QLabel("Chance:"), 1, 0)
        h.addWidget(self.chance, 1, 1)
        h.addWidget(self.offbeat, 1, 2)
        h.addWidget(QLabel("Varianten:"), 2, 0)
        h.addWidget(button("Variante B", slot=lambda: self.vary("B"), tooltip="Schluss mit anderen Tönen"), 2, 1)
        h.addWidget(button("Variante C", slot=lambda: self.vary("C"), tooltip="ausgedünnt, mit Oktavsprüngen"), 2, 2)
        disc = Disclosure("Feinschliff: Anschlag, Chance, Varianten", hum)
        disc.button.setToolTip("Anschlagskurven, Melodee-Chance und Varianten B/C auf das erzeugte Pattern anwenden")
        v.addWidget(disc)
        dev = Card("Auf das FM-1")
        dev.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)   # Knopfreihe darf umbrechen
        self.track = QComboBox()
        for k in range(PF.NTRK):
            self.track.addItem(f"Spur {k + 1}" + (" (MIDI)" if k >= PF.NPART else ""), k)
        self.track.currentIndexChanged.connect(self._fill_patterns)
        self.pattern = QComboBox()
        self._fill_patterns()
        self.b_write = button("In Pattern schreiben", ("go-up", "document-send"), "primary", self.write,
                              "Liest das Pattern erst (für Rückgängig), schreibt dann und prüft durch Nachlesen")
        self.b_undo = button("Rückgängig am FM-1", ("edit-undo",), slot=self.undo_last,
                             tooltip="Das zuletzt überschriebene Pattern wiederherstellen")
        self.b_undo.setEnabled(False)
        dev.lay.addWidget(flow_row(QLabel("Ziel:"), self.track, self.pattern, self.b_write, self.b_undo,
                                   button("Als MIDI speichern …", ("document-save",), slot=self.save_midi,
                                          tooltip="Das Pattern als Standard-MIDI-Datei speichern (für DAW, MuseScore …)")))
        self.status = label("", "muted")
        dev.lay.addWidget(self.status)
        self._dev_card = dev
        v.addWidget(dev)
        v.addStretch(1)
        vs = QSplitter(Qt.Orientation.Vertical)
        vs.setChildrenCollapsible(False)
        vs.addWidget(top)
        vs.addWidget(fit_height(bottom))                 # umbrechende Knopfreihe nie abschneiden
        vs.setStretchFactor(0, 1)
        vs.setStretchFactor(1, 0)
        self.preview_split = vs
        outer = QVBoxLayout(w)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(vs)
        return w

    def dev_card_title(self, text: str):
        """Titel der Karte „Auf das FM-1“ – nennt das aktive Gerät (bei mehreren FM-1 eindeutig)."""
        card = getattr(self, "_dev_card", None)
        if card is not None and getattr(card, "title_label", None) is not None:
            card.title_label.setText(text)

    def _fill_patterns(self, *_):
        k = self.track.currentData() or 0
        n = PF.NPAT if k < PF.NPART else PF.NPAT_MIDI
        cur = self.pattern.currentIndex()
        self.pattern.clear()
        for b in range(n):
            self.pattern.addItem(f"Pattern {b + 1}", b)
        self.pattern.setCurrentIndex(max(0, min(cur, n - 1)))

    def _zoom(self, v):
        self.roll.set_zoom(v)
        self.roll.resize(self.roll.sizeHint())

    # ------------------------------------------------------------------------------------- Erzeugen
    def _reseed(self):
        import random
        self.seed.setValue(random.randint(1, 99999))
        self.generate()

    def chord_options(self) -> G.ChordOptions:
        return G.ChordOptions(key=self.key.currentIndex(), scale=self.scale.currentText(), progression=self.prog.text(),
                              octave=self.octave.value(), voicing=self.voicing.currentText(),
                              inversion=self.inversion.value(), smooth=self.smooth.isChecked(),
                              rhythm=self.rhythm.currentText(), steps_per_chord=self.per_chord.value(),
                              length=self.length.value())

    def generate(self):
        n = self.length.value()
        seed = self.seed.value()
        tab = self.tabs.currentIndex()
        try:
            if tab == 0:
                specs = [G.LaneSpec(lane, r[1].value(), r[2].value(), r[3].value(), r[4].value(), r[0].isChecked())
                         for lane, r in enumerate(self.lane_rows)]
                steps = G.drum_pattern(specs, n)
                what = "Rhythmus"
            elif tab == 1:
                steps, names = G.chord_steps(self.chord_options())
                what = "Akkorde " + " ".join(names[:8]) + (" …" if len(names) > 8 else "")
            elif tab == 2:
                o = self.chord_options()
                chords = [G.chord_from_roman(s, o.key, o.scale, o.octave) for s in o.progression.split()]
                kind = self.bass_kind.currentText()
                if kind.startswith("Bass"):
                    steps = G.bass_steps(chords, o.steps_per_chord, n, kind.split(": ")[1], seed=seed)
                else:
                    steps = G.arp_steps(chords[0], kind.split(": ")[1], n, self.arp_oct.value(), seed=seed)
                    if len(chords) > 1:      # Arp folgt den Akkorden
                        for i in range(n):
                            c = chords[(i // max(1, o.steps_per_chord)) % len(chords)]
                            seg = G.arp_steps(c, kind.split(": ")[1], o.steps_per_chord, self.arp_oct.value(), seed=seed + i)
                            steps[i] = seg[i % o.steps_per_chord]
                what = kind
            else:
                kind = self.mel_kind.currentIndex()
                wo = G.WalkOptions(key=self.key.currentIndex(), scale=self.scale.currentText(), low=self.low.value(),
                                   high=max(self.low.value() + 1, self.high.value()), max_leap=self.leap.value(),
                                   rest_prob=self.rest.value() / 100, repeat_prob=self.repeat.value() / 100,
                                   tie_prob=self.tie.value() / 100, length=n, seed=seed)
                if kind == 1:
                    if not self.markov or not self.markov.trained:
                        show_error(self, "Noch nichts gelernt", "Bitte zuerst eine MIDI-Datei lernen lassen "
                                   "(Knopf „MIDI-Datei lernen …“).")
                        return
                    steps = self.markov.generate(n, seed)
                    what = "Markov-Melodie"
                elif kind == 2:
                    half = G.random_walk(G.WalkOptions(**{**wo.__dict__, "length": max(1, n // 2)}))
                    steps = G.call_response(half, n, wo.key, wo.scale, seed)
                    what = "Frage und Antwort"
                else:
                    steps = G.random_walk(wo)
                    what = "Zufallsmelodie"
        except ValueError as e:
            show_error(self, "Eingabe prüfen", str(e))
            return
        self._remember(what)
        self.set_steps(steps, n)
        self.desc.setText(f"<b>{what}</b> · {G.describe(steps, n)} · Seed {seed}")

    def set_steps(self, steps, n: int | None = None):
        self.roll.set_steps(steps, n or self.length.value())
        self.roll.set_zoom(self.zoom.value())
        self.roll.resize(self.roll.sizeHint())

    def steps(self):
        return self.roll.steps

    def _roll_changed(self):
        self.desc.setText(f"<b>bearbeitet</b> · {G.describe(self.roll.steps, self.roll.length)}")
        self._history_buttons()

    # ---- Rückgängig / Wiederholen (am Rechner; das Zurückholen am FM-1 macht „Rückgängig am FM-1“)
    def _remember(self, what: str = ""):
        if self._restoring:
            return
        self.history.push(self.roll.steps, self.roll.length, what)
        self._history_buttons()

    def _history_buttons(self):
        if not hasattr(self, "b_hist_undo"):
            return
        h = self.history
        self.b_hist_undo.setEnabled(h.can_undo())
        self.b_hist_redo.setEnabled(h.can_redo())
        self.b_hist_undo.setToolTip("Letzte Bearbeitung zurücknehmen (Strg+Z)" +
                                    (f": {h.undo_stack[-1][2]}" if h.can_undo() and h.undo_stack[-1][2] else ""))
        self.b_hist_redo.setToolTip("Wiederholen (Strg+Umschalt+Z oder Strg+Y)" +
                                    (f": {h.redo_stack[-1][2]}" if h.can_redo() and h.redo_stack[-1][2] else ""))

    def _apply_state(self, st, verb: str):
        if st is None:
            return
        steps, n, what = st
        self._restoring = True
        try:
            self.set_steps(steps, n)
            if self.length.value() != n:
                self.length.blockSignals(True)
                self.length.setValue(n)
                self.length.blockSignals(False)
        finally:
            self._restoring = False
        self.desc.setText(f"<b>{verb}</b>{': ' + what if what else ''} · {G.describe(self.roll.steps, self.roll.length)}")
        self._history_buttons()

    def undo_edit(self):
        self._apply_state(self.history.undo(self.roll.steps, self.roll.length), "Rückgängig")

    def redo_edit(self):
        self._apply_state(self.history.redo(self.roll.steps, self.roll.length), "Wiederholt")

    def clear(self):
        self._remember("Leeren")
        self.set_steps([PF.Step() for _ in range(PF.NSTEP)])
        self._history_buttons()

    def humanize(self):
        st = G.humanize(self.roll.steps, self.roll.length, self.curve.currentText(), self.amount.value(),
                        self.chance.value(), self.offbeat.isChecked(), self.seed.value())
        self._remember("Anschlag")
        self.set_steps(st, self.roll.length)
        self._history_buttons()

    def vary(self, kind: str):
        st = G.variation(self.roll.steps, self.roll.length, kind, self.key.currentIndex(), self.scale.currentText(),
                         self.seed.value())
        self._remember(f"Variante {kind}")
        self.set_steps(st, self.roll.length)
        self._history_buttons()

    def learn_markov(self, path=None):
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "MIDI-Datei lernen", str(Path.home()), "MIDI (*.mid *.midi)")
            if not path:
                return
        order = self.markov_order.value()
        self.markov_label.setText(f"lerne {Path(path).name} …")

        def done(m):
            self.markov = m
            self.markov_label.setText(f"gelernt: {Path(path).name} ({len(m.table)} Zustände)"
                                      if m.trained else "zu wenige Noten in der Datei")

        def fail(msg, tb):
            self.markov_label.setText("nichts gelernt")
            show_error(self, "MIDI-Datei nicht lesbar", msg)
        run_job(self, lambda w: G.markov_from_midi(Path(path), order), on_done=done, on_fail=fail)

    # ------------------------------------------------------------------------------------- Hören
    def pattern_obj(self) -> PF.Pattern:
        return PF.Pattern(0, steps=list(self.roll.steps), length=self.roll.length)

    @property
    def player(self):
        """der Rechner-Player des Vorhörens (None, solange nur auf dem FM-1 gespielt wurde)."""
        return self.play_bar.player

    def play_source(self) -> Source:
        pat = self.pattern_obj()
        evs = ME.pattern_events(pat, 0, 0)
        k = self.track.currentData() or 0
        return Source(evs, self.bpm.value(), "Komponieren", None, track_override=k,
                      loop_quarters=ME.pattern_quarters(pat))

    def preview(self):
        """Vorhören: auf dem verbundenen FM-1 mit dem Klang der Zielspur (Noten per MIDI, im Hintergrund) –
        ohne FM-1 als Annäherung im Rechner (Berechnung im Hintergrund, die Oberfläche bleibt bedienbar)."""
        self.play_bar.play()

    def _toggle_preview(self):
        if self.play_bar.playing:
            self.stop()
        else:
            self.preview()

    def stop(self):
        self.play_bar.stop()

    def save_midi(self, path=None):
        if not path:
            folder, _err = safe_folder("MIDI-Export")
            start = folder if folder.is_dir() else Path.home()
            path, _ = QFileDialog.getSaveFileName(self, "MIDI speichern", str(start / "Idee.mid"), "MIDI (*.mid)")
            if not path:
                return None
        prj = PF.Project(format="Komponieren", bpm=self.bpm.value(), ok=True)
        t = PF.Track(0, patterns=[self.pattern_obj()])
        prj.tracks = [t]
        try:
            ME.save_midi(prj, Path(path), ME.Options(mode="active", loops=1))
        except OSError as e:
            msg = fs_error_text(e, "MIDI-Datei nicht gespeichert")
            self.status.setText(msg)
            show_error(self, "Als MIDI speichern", msg)
            return None
        self.status.setText(f"Gespeichert: {path} – nur als Datei, am FM-1 nichts geändert.")
        notify(f"MIDI-Datei gespeichert: {Path(path).name}", "success")
        return Path(path)

    # ------------------------------------------------------------------------------------- FM-1
    def write(self):
        """Erst lesen (für Rückgängig und um zu sagen, ob das Pattern belegt ist), dann bestätigen, dann schreiben."""
        if not self.ctx.require(self):
            return
        k, b = self.track.currentData(), self.pattern.currentData()
        n = self.roll.length
        steps = list(self.roll.steps)
        if not any(not st.empty for st in steps[:n]):
            show_error(self, "Nichts zu schreiben", "Das Pattern ist leer – erst „Erzeugen“ oder Steps in der "
                                                     "Piano-Roll setzen.")
            return
        tag = device_tag(self.ctx)

        def read(w, link):
            return MI.read_device_pattern(link, k, b, progress=lambda a, t: w.progress.emit(a, t))

        def after_read(snap):
            if snap is None:
                return
            used = [st for st in snap.steps[:max(1, snap.length)] if not st.empty]
            notes = sum(len(st.notes) for st in used) + sum(bin(st.hit).count("1") for st in used)
            occupied = (f"ist <b>belegt</b>: {len(used)} von {snap.length} Steps mit {notes} Noten/Schlägen"
                        if used else "ist <b>leer</b>")
            if not confirm(self, "Pattern überschreiben",
                           f"<b>Spur {k + 1}, Pattern {b + 1}</b> auf dem FM-1 {occupied} und wird mit {n} Steps "
                           f"({G.describe(steps, n)}) überschrieben. Das Pattern wird dabei am FM-1 ausgewählt.<br><br>"
                           "Der alte Inhalt ist gelesen – „Rückgängig“ holt ihn zurück. Am FM-1 bitte vorher stoppen. "
                           "Dauerhaft wird es erst mit SAVE am FM-1.", "Überschreiben", danger=True):
                self.status.setText(f"Abgebrochen – Spur {k + 1}, Pattern {b + 1} ist unverändert.")
                return

            def work(w, link):
                w.log.emit("Schreibe und prüfe …")
                return MI.write_device_pattern(link, k, b, steps, n, progress=lambda a, t: w.progress.emit(a, t))

            state = {"ok": False}

            def done(note):
                state["ok"] = True
                self.status.setText(f"Geschrieben und nachgelesen: Spur {k + 1}, Pattern {b + 1}. " + (note or "") +
                                    " Am FM-1 anhören und mit SAVE speichern.")
            self.undo.push(self.ctx, snap, f"Spur {k + 1}, Pattern {b + 1}", tag)   # vor dem Schreiben: auch halb
            self._dev_changed()                                                    # Geschriebenes ist zurückholbar
            self.ctx.job(self, "Pattern schreiben", work, on_done=done,
                         done_text=f"Spur {k + 1}, Pattern {b + 1}: {n} Steps geschrieben und nachgelesen. Der alte "
                         "Inhalt ist für „Rückgängig“ gesichert. Dauerhaft wird es erst mit SAVE am FM-1.",
                         fail_text=lambda m: f"Nicht geschrieben: {m}. „Rückgängig“ stellt den gelesenen alten "
                                             f"Stand von Spur {k + 1}, Pattern {b + 1} wieder her.")
            if not state["ok"]:
                self.status.setText(f"Nicht geschrieben – „Rückgängig“ stellt Spur {k + 1}, Pattern {b + 1} "
                                    "auf den gelesenen Stand zurück.")

        self.ctx.job(self, "Pattern lesen", read, on_done=after_read,
                     fail_text=lambda m: f"Nicht gelesen: {m}. Am FM-1 wurde nichts geändert.")

    def undo_last(self):
        if not self.undo or not self.ctx.require(self) or not self.undo.check(self.ctx, self):
            return
        snap = self.undo.peek()
        if not confirm(self, "Rückgängig", f"Spur {snap.track + 1}, Pattern {(snap.pattern or 0) + 1} wieder auf den "
                       "Stand vor dem Schreiben setzen?", "Wiederherstellen"):
            return

        def work(w, link):
            return MI.restore_snapshot(link, snap, progress=lambda a, t: w.progress.emit(a, t))

        def done(_res):
            self.undo.pop()
            self._dev_changed()
            self.status.setText(f"Wiederhergestellt: Spur {snap.track + 1}, Pattern {(snap.pattern or 0) + 1}.")
        self.ctx.job(self, "Pattern wiederherstellen", work, on_done=done,
                     done_text=f"Spur {snap.track + 1}, Pattern {(snap.pattern or 0) + 1} hat wieder den alten Inhalt.")

    def deactivate(self):
        self.play_bar.stop(quiet=True)              # Vorhören ist an die Seite gebunden, nicht an die App

    def shutdown(self):
        self.play_bar.shutdown()


def _make(ctx, window):
    return ComposePage(ctx, window)


PAGE = PageSpec(key="compose", title=_("Compose"), group="musik", factory=_make,
                icons=("music-note-16th", "audio-x-generic", "media-playlist-shuffle"), order=30,
                description=_("generate rhythms, chords and melodies and write them to the FM-1"),
                keywords=("euklid", "akkord", "melodie", "arpeggio", "bass", "markov", "generator", "pattern",
                          "euclid", "chord", "melody", "rhythm", "compose"))
