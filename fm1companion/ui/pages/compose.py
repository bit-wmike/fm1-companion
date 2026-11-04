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
from ...i18n import _, ngettext, pgettext


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
        lay.addWidget(heading(_("Compose"), _("Generate ideas instead of typing steps: Euclidean rhythms, chord "
                                              "progressions, bass, arpeggios and melodies. Preview – directly on the "
                                              "FM-1 with the sound of the target track –, adjust in the piano roll "
                                              "(Ctrl+Z / Ctrl+Shift+Z), then write into a pattern on the FM-1 – it is "
                                              "read first so you can undo it.")))
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
        self.dev_card_title(_("To the FM-1 “{name}”").format(name=name) if name else _("To the FM-1 (none connected)"))
        ok = self.undo.matches(self.ctx)
        self.b_undo.setEnabled(bool(self.undo))
        self.b_undo.setToolTip(_("Restore the pattern that was overwritten last") if ok or not self.undo
                               else self.undo.refusal())

    # ------------------------------------------------------------------------------------- Aufbau
    def _build_left(self) -> QWidget:
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        common = Card(_("Basics"))
        f = QFormLayout()
        self.key = QComboBox()
        self.key.addItems(G.NOTE_NAMES)
        self.key.setToolTip(_("Root note of the key"))
        self.scale = QComboBox()
        for k in G.SCALES:
            self.scale.addItem(G.label(k, G.SCALE_LABELS), k)
        self.length = _spin(1, 64, 16, _(" steps"), _("Length of the pattern (LEN on the FM-1)"))
        self.bpm = _spin(40, 240, 110, " BPM", _("Tempo for the preview (the FM-1 keeps its own)"))
        self.seed = _spin(1, 99999, 1, "", _("Random seed: same value = same result"))
        seedrow = QHBoxLayout()
        seedrow.addWidget(self.seed, 1)
        seedrow.addWidget(button(_("Roll dice"), ("roll", "media-playlist-shuffle"), slot=self._reseed,
                                 tooltip=_("draw a new seed and generate again")))
        f.addRow(_("Key:"), self.key)
        f.addRow(_("Scale:"), self.scale)
        f.addRow(_("Length:"), self.length)
        f.addRow(_("Tempo:"), self.bpm)
        f.addRow(_("Seed:"), seedrow)
        common.lay.addLayout(f)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._tab_rhythm(), _("Rhythm"))
        self.tabs.addTab(self._tab_chords(), _("Chords"))
        self.tabs.addTab(self._tab_bass(), _("Bass/Arp"))
        self.tabs.addTab(self._tab_melody(), _("Melody"))
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
        v.addWidget(button(_("Generate"), ("system-run", "media-playback-start"), "", self.generate,
                           _("Generate again with the settings of the open tab (Ctrl+G) – changes nothing on the FM-1")))
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
        for c, t in enumerate(("Steps", _("Hits"), _("Offset"), _("Accent"))):
            g.addWidget(QLabel(f"<b>{t}</b>"), 0, c + 1)
        defaults = {0: (16, 4, 0, 1), 1: (16, 2, 4, 0), 3: (16, 8, 0, 2), 4: (16, 2, 2, 0)}
        self.lane_rows = []
        for lane in range(8):
            on = QCheckBox(PF.LANES[lane])
            st, pu, ro, ac = defaults.get(lane, (16, 3, 0, 0))
            on.setChecked(lane in defaults)
            row = (on, _spin(1, 64, st, "", _("Cycle length")), _spin(0, 64, pu, "", _("Number of hits, evenly spread")),
                   _spin(0, 63, ro, "", _("Shift to the right")), _spin(0, 16, ac, "", _("every n-th hit accented (0 = none)")))
            for c, wdg in enumerate(row):
                if c:
                    wdg.setMinimumWidth(58)          # schmale Zahlenfelder: Raster passt in die Einstellungs-Spalte
                g.addWidget(wdg, lane + 1, c)
            self.lane_rows.append(row)
        g.addWidget(label(_("Euclidean rhythms spread n hits as evenly as possible – E(3,8) is the tresillo. The "
                            "result lands in the drum grid (for the DRUM engine or any other track)."),
                          "muted"), 10, 0, 1, 5)
        return w

    def _tab_chords(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        self.prog_preset = QComboBox()
        for k in G.PROGRESSIONS:
            self.prog_preset.addItem(G.label(k, G.PROGRESSION_LABELS), k)
        self.prog = QLineEdit(G.PROGRESSIONS[self.prog_preset.currentData()])
        self.prog.setToolTip(_("Degrees in Roman numerals: upper case = major, lower case = minor, 7 / maj7 / ° / sus4, "
                               "b/# in front"))
        self.prog_preset.currentIndexChanged.connect(
            lambda _i: self.prog.setText(G.PROGRESSIONS[self.prog_preset.currentData()]))
        self.voicing = QComboBox()
        for k in G.VOICINGS:
            self.voicing.addItem(G.label(k, G.VOICING_LABELS), k)
        self.inversion = _spin(0, 3, 0, "", _("Inversion of the first chord"))
        self.smooth = QCheckBox(_("Smooth voice leading"))
        self.smooth.setChecked(True)
        self.smooth.setToolTip(_("Choose inversions so that the voices move as little as possible"))
        self.octave = _spin(2, 6, 4, "", _("Octave of the chords"))
        self.rhythm = QComboBox()
        for k in G.RHYTHMS:
            self.rhythm.addItem(G.label(k, G.RHYTHM_LABELS), k)
        self.per_chord = _spin(1, 32, 4, _(" steps"), _("Duration of one chord"))
        f.addRow(_("Preset:"), self.prog_preset)
        f.addRow(_("Progression:"), self.prog)
        f.addRow(_("Voicing:"), self.voicing)
        f.addRow(_("Inversion:"), self.inversion)
        f.addRow("", self.smooth)
        f.addRow(_("Octave:"), self.octave)
        f.addRow(_("Rhythm:"), self.rhythm)
        f.addRow(_("per chord:"), self.per_chord)
        f.addRow(label(_("The FM-1 plays up to 4 notes per step – chords are limited to that."), "muted"))
        return w

    def _tab_bass(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        self.bass_kind = QComboBox()
        for s in G.BASS_STYLES:
            self.bass_kind.addItem(_("Bass: {style}").format(style=G.label(s, G.BASS_STYLE_LABELS)), ("bass", s))
        for m in G.ARP_MODES:
            self.bass_kind.addItem(_("Arp: {mode}").format(mode=G.label(m, G.ARP_MODE_LABELS)), ("arp", m))
        self.arp_oct = _spin(1, 3, 1, _(" octaves"), _("Range of the arpeggio"))
        f.addRow(_("Type:"), self.bass_kind)
        f.addRow(_("Arp range:"), self.arp_oct)
        f.addRow(label(_("Bass and arpeggio follow the chord progression from the tab “Chords”."), "muted"))
        return w

    def _tab_melody(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        self.mel_kind = QComboBox()
        self.mel_kind.addItems([_("Random melody with rules"), _("Markov (learned from a MIDI file)"),
                                _("Question and answer")])
        self.low = _spin(24, 108, 60, "", _("lowest note"))
        self.high = _spin(24, 108, 79, "", _("highest note"))
        self.leap = _spin(1, 7, 2, _(" degrees"), _("largest leap in scale degrees"))
        self.rest = _spin(0, 90, 20, " %", _("Probability of a rest"))
        self.repeat = _spin(0, 90, 10, " %", _("Probability of repeating the note"))
        self.tie = _spin(0, 90, 10, " %", _("Probability of holding a note"))
        self.markov_order = _spin(1, 2, 1, "", _("1 = only the last note, 2 = the last two notes"))
        self.markov_label = QLabel(_("nothing learned yet"))
        mrow = QHBoxLayout()
        mrow.addWidget(button(_("Learn MIDI file …"), ("document-open",), slot=self.learn_markov,
                              tooltip=_("Learn note sequences and rhythms from a MIDI file")))
        mrow.addWidget(self.markov_label, 1)
        f.addRow(_("Type:"), self.mel_kind)
        f.addRow(_("Range from:"), self.low)
        f.addRow(_("to:"), self.high)
        f.addRow(_("Leaps up to:"), self.leap)
        f.addRow(_("Rests:"), self.rest)
        f.addRow(_("Repetition:"), self.repeat)
        f.addRow(_("Hold:"), self.tie)
        f.addRow(_("Markov order:"), self.markov_order)
        f.addRow(mrow)
        return w

    def _build_center(self) -> QWidget:
        w = QWidget()
        top = QWidget()                                 # oben: Vorschau (Piano-Roll) – im Splitter größer ziehbar
        v = QVBoxLayout(top)
        v.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        self.b_settings = button("", ("configure", "preferences-system"), slot=None,
                                 tooltip=_("Show or hide the settings – more room for the piano roll"))
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
                                  tooltip=_("Undo the last edit (Ctrl+Z) – piano roll, generate, fine-tuning"))
        self.b_hist_redo = button("", ("edit-redo",), slot=self.redo_edit,
                                  tooltip=_("Redo the undone edit (Ctrl+Shift+Z or Ctrl+Y)"))
        for b in (self.b_hist_undo, self.b_hist_redo):
            b.setAccessibleName(_("Undo") if b is self.b_hist_undo else _("Redo"))
            bar.addWidget(b)
        bar.addWidget(button(_("Clear"), ("edit-clear",), slot=self.clear, tooltip=_("delete all steps")))
        v.addLayout(bar)
        self.play_bar = PlaybackBar(self.ctx, self.play_source, self, loop=True, play_text=_("Preview"),
                                    shortcut_hint=_("Space"), compact=True)
        v.addWidget(self.play_bar)
        self._bar, self._desc_below = bar, QHBoxLayout()   # schmale Vorschau: Beschreibung unter die Knöpfe
        v.addLayout(self._desc_below)
        on_narrow(top, 600, self._place_desc)
        self.roll = StepRoll(editable=True)
        self.roll.about_to_change.connect(lambda: self._remember(_("Piano roll")))
        self.roll.changed.connect(self._roll_changed)
        sc = QScrollArea()
        sc.setWidget(self.roll)
        v.addWidget(sc, 1)
        zrow = QHBoxLayout()
        zrow.addWidget(QLabel(_("Zoom:")))
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
        for k in G.VEL_CURVES:
            self.curve.addItem(G.label(k, G.VEL_CURVE_LABELS), k)
        self.amount = _spin(0, 60, 12, "", _("Strength of the velocity change"))
        self.chance = _spin(10, 100, 100, " %", _("Probability with which steps play (Melodee chance)"))
        self.offbeat = QCheckBox(_("only on offbeats"))
        self.offbeat.setChecked(True)
        h.addWidget(QLabel(_("Velocity:")), 0, 0)
        h.addWidget(self.curve, 0, 1)
        h.addWidget(self.amount, 0, 2)
        h.addWidget(button(_("Apply"), ("tools-wizard",), slot=self.humanize), 0, 3)
        h.addWidget(QLabel(_("Chance:")), 1, 0)
        h.addWidget(self.chance, 1, 1)
        h.addWidget(self.offbeat, 1, 2)
        h.addWidget(QLabel(_("Variations:")), 2, 0)
        h.addWidget(button(_("Variation B"), slot=lambda: self.vary("B"), tooltip=_("ending with other notes")), 2, 1)
        h.addWidget(button(_("Variation C"), slot=lambda: self.vary("C"), tooltip=_("thinned out, with octave jumps")), 2, 2)
        disc = Disclosure(_("Fine-tuning: velocity, chance, variations"), hum)
        disc.button.setToolTip(_("Apply velocity curves, Melodee chance and variations B/C to the generated pattern"))
        v.addWidget(disc)
        dev = Card(_("Onto the FM-1"))
        dev.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)   # Knopfreihe darf umbrechen
        self.track = QComboBox()
        for k in range(PF.NTRK):
            self.track.addItem(_("Track {n}").format(n=k + 1) + (" (MIDI)" if k >= PF.NPART else ""), k)
        self.track.currentIndexChanged.connect(self._fill_patterns)
        self.pattern = QComboBox()
        self._fill_patterns()
        self.b_write = button(_("Write to pattern"), ("go-up", "document-send"), "primary", self.write,
                              _("Reads the pattern first (for undo), then writes and checks by reading back"))
        self.b_undo = button(_("Undo on the FM-1"), ("edit-undo",), slot=self.undo_last,
                             tooltip=_("Restore the pattern that was overwritten last"))
        self.b_undo.setEnabled(False)
        dev.lay.addWidget(flow_row(QLabel(_("Target:")), self.track, self.pattern, self.b_write, self.b_undo,
                                   button(_("Save as MIDI …"), ("document-save",), slot=self.save_midi,
                                          tooltip=_("Save the pattern as a Standard MIDI File (for a DAW, MuseScore …)"))))
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

    def _fill_patterns(self, *_args):
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
        return G.ChordOptions(key=self.key.currentIndex(), scale=self.scale.currentData(), progression=self.prog.text(),
                              octave=self.octave.value(), voicing=self.voicing.currentData(),
                              inversion=self.inversion.value(), smooth=self.smooth.isChecked(),
                              rhythm=self.rhythm.currentData(), steps_per_chord=self.per_chord.value(),
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
                what = _("Rhythm")
            elif tab == 1:
                steps, names = G.chord_steps(self.chord_options())
                what = _("Chords {names}").format(names=" ".join(names[:8]) + (" …" if len(names) > 8 else ""))
            elif tab == 2:
                o = self.chord_options()
                chords = [G.chord_from_roman(s, o.key, o.scale, o.octave) for s in o.progression.split()]
                group, style = self.bass_kind.currentData()
                if group == "bass":
                    steps = G.bass_steps(chords, o.steps_per_chord, n, style, seed=seed)
                else:
                    steps = G.arp_steps(chords[0], style, n, self.arp_oct.value(), seed=seed)
                    if len(chords) > 1:      # Arp folgt den Akkorden
                        for i in range(n):
                            c = chords[(i // max(1, o.steps_per_chord)) % len(chords)]
                            seg = G.arp_steps(c, style, o.steps_per_chord, self.arp_oct.value(), seed=seed + i)
                            steps[i] = seg[i % o.steps_per_chord]
                what = self.bass_kind.currentText()
            else:
                kind = self.mel_kind.currentIndex()
                wo = G.WalkOptions(key=self.key.currentIndex(), scale=self.scale.currentData(), low=self.low.value(),
                                   high=max(self.low.value() + 1, self.high.value()), max_leap=self.leap.value(),
                                   rest_prob=self.rest.value() / 100, repeat_prob=self.repeat.value() / 100,
                                   tie_prob=self.tie.value() / 100, length=n, seed=seed)
                if kind == 1:
                    if not self.markov or not self.markov.trained:
                        show_error(self, _("Nothing learned yet"), _("Please let it learn a MIDI file first "
                                                                    "(button “Learn MIDI file …”)."))
                        return
                    steps = self.markov.generate(n, seed)
                    what = _("Markov melody")
                elif kind == 2:
                    half = G.random_walk(G.WalkOptions(**{**wo.__dict__, "length": max(1, n // 2)}))
                    steps = G.call_response(half, n, wo.key, wo.scale, seed)
                    what = _("Question and answer")
                else:
                    steps = G.random_walk(wo)
                    what = _("Random melody")
        except ValueError as e:
            show_error(self, _("Check the input"), str(e))
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
        self.desc.setText(_("<b>edited</b> · {description}").format(description=G.describe(self.roll.steps, self.roll.length)))
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
        self.b_hist_undo.setToolTip(_("Undo the last edit (Ctrl+Z)") +
                                    (f": {h.undo_stack[-1][2]}" if h.can_undo() and h.undo_stack[-1][2] else ""))
        self.b_hist_redo.setToolTip(_("Redo (Ctrl+Shift+Z or Ctrl+Y)") +
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
        self._apply_state(self.history.undo(self.roll.steps, self.roll.length), _("Undo"))

    def redo_edit(self):
        self._apply_state(self.history.redo(self.roll.steps, self.roll.length), _("Redone"))

    def clear(self):
        self._remember(_("Clear"))
        self.set_steps([PF.Step() for _i in range(PF.NSTEP)])
        self._history_buttons()

    def humanize(self):
        st = G.humanize(self.roll.steps, self.roll.length, self.curve.currentData(), self.amount.value(),
                        self.chance.value(), self.offbeat.isChecked(), self.seed.value())
        self._remember(_("Velocity"))
        self.set_steps(st, self.roll.length)
        self._history_buttons()

    def vary(self, kind: str):
        st = G.variation(self.roll.steps, self.roll.length, kind, self.key.currentIndex(), self.scale.currentData(),
                         self.seed.value())
        self._remember(_("Variation {name}").format(name=kind))
        self.set_steps(st, self.roll.length)
        self._history_buttons()

    def learn_markov(self, path=None):
        if not path:
            path, _filter = QFileDialog.getOpenFileName(self, _("Learn MIDI file"), str(Path.home()), "MIDI (*.mid *.midi)")
            if not path:
                return
        order = self.markov_order.value()
        self.markov_label.setText(_("learning {name} …").format(name=Path(path).name))

        def done(m):
            self.markov = m
            self.markov_label.setText(ngettext("learned: {name} ({n} state)", "learned: {name} ({n} states)",
                                               len(m.table)).format(name=Path(path).name, n=len(m.table))
                                      if m.trained else _("too few notes in the file"))

        def fail(msg, tb):
            self.markov_label.setText(_("nothing learned"))
            show_error(self, _("MIDI file not readable"), msg)
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
        return Source(evs, self.bpm.value(), _("Compose"), None, track_override=k,
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
            path, _filter = QFileDialog.getSaveFileName(self, _("Save MIDI"), str(start / "Idee.mid"), "MIDI (*.mid)")  # i18n: ignore
            if not path:
                return None
        prj = PF.Project(format="Komponieren", bpm=self.bpm.value(), ok=True)
        t = PF.Track(0, patterns=[self.pattern_obj()])
        prj.tracks = [t]
        try:
            ME.save_midi(prj, Path(path), ME.Options(mode="active", loops=1))
        except OSError as e:
            msg = fs_error_text(e, _("MIDI file not saved"))
            self.status.setText(msg)
            show_error(self, _("Save as MIDI"), msg)
            return None
        self.status.setText(_("Saved: {path} – only as a file, nothing changed on the FM-1.").format(path=path))
        notify(_("MIDI file saved: {name}").format(name=Path(path).name), "success")
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
            show_error(self, _("Nothing to write"), _("The pattern is empty – first “Generate” or set steps in the "
                                                      "piano roll."))
            return
        tag = device_tag(self.ctx)

        def read(w, link):
            return MI.read_device_pattern(link, k, b, progress=lambda a, t: w.progress.emit(a, t))

        def after_read(snap):
            if snap is None:
                return
            used = [st for st in snap.steps[:max(1, snap.length)] if not st.empty]
            notes = sum(len(st.notes) for st in used) + sum(bin(st.hit).count("1") for st in used)
            if used:
                text = _("<b>Track {track}, pattern {pattern}</b> on the FM-1 is <b>in use</b>: {used} of {length} steps "
                         "with {notes} notes/hits, and will be overwritten with {n} steps ({description}).")
            else:
                text = _("<b>Track {track}, pattern {pattern}</b> on the FM-1 is <b>empty</b> and will be overwritten "
                         "with {n} steps ({description}).")
            text = text.format(track=k + 1, pattern=b + 1, used=len(used), length=snap.length, notes=notes, n=n,
                               description=G.describe(steps, n))
            if not confirm(self, _("Overwrite pattern"),
                           text + " " + _("The pattern is selected on the FM-1 in the process.") + "<br><br>" +
                           _("The old content has been read – “Undo” brings it back. Please stop the FM-1 first. "
                             "It only becomes permanent with SAVE on the FM-1."), _("Overwrite"), danger=True):
                self.status.setText(_("Cancelled – track {track}, pattern {pattern} is unchanged.").format(
                    track=k + 1, pattern=b + 1))
                return

            def work(w, link):
                w.log.emit(_("Writing and checking …"))
                return MI.write_device_pattern(link, k, b, steps, n, progress=lambda a, t: w.progress.emit(a, t))

            state = {"ok": False}

            def done(note):
                state["ok"] = True
                self.status.setText(_("Written and read back: track {track}, pattern {pattern}.").format(
                    track=k + 1, pattern=b + 1) + " " + (note or "") + " " +
                    _("Listen on the FM-1 and store it with SAVE."))
            self.undo.push(self.ctx, snap, _("Track {track}, pattern {pattern}").format(track=k + 1, pattern=b + 1),
                           tag)                                                    # vor dem Schreiben: auch halb
            self._dev_changed()                                                    # Geschriebenes ist zurückholbar
            self.ctx.job(self, _("Write pattern"), work, on_done=done,
                         done_text=_("Track {track}, pattern {pattern}: {n} steps written and read back. The old "
                                     "content is kept for “Undo”. It only becomes permanent with SAVE on the "
                                     "FM-1.").format(track=k + 1, pattern=b + 1, n=n),
                         fail_text=lambda m: _("Not written: {error}. “Undo” restores the old state of track "
                                               "{track}, pattern {pattern} that was read.").format(
                             error=m, track=k + 1, pattern=b + 1))
            if not state["ok"]:
                self.status.setText(_("Not written – “Undo” sets track {track}, pattern {pattern} back to the state "
                                      "that was read.").format(track=k + 1, pattern=b + 1))

        self.ctx.job(self, _("Read pattern"), read, on_done=after_read,
                     fail_text=lambda m: _("Not read: {error}. Nothing was changed on the FM-1.").format(error=m))

    def undo_last(self):
        if not self.undo or not self.ctx.require(self) or not self.undo.check(self.ctx, self):
            return
        snap = self.undo.peek()
        if not confirm(self, _("Undo"), _("Set track {track}, pattern {pattern} back to the state before writing?").format(
                track=snap.track + 1, pattern=(snap.pattern or 0) + 1), pgettext("undo", "Restore")):
            return

        def work(w, link):
            return MI.restore_snapshot(link, snap, progress=lambda a, t: w.progress.emit(a, t))

        def done(_res):
            self.undo.pop()
            self._dev_changed()
            self.status.setText(_("Restored: track {track}, pattern {pattern}.").format(
                track=snap.track + 1, pattern=(snap.pattern or 0) + 1))
        self.ctx.job(self, _("Restore pattern"), work, on_done=done,
                     done_text=_("Track {track}, pattern {pattern} has its old content again.").format(
                         track=snap.track + 1, pattern=(snap.pattern or 0) + 1))

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
