# SPDX-License-Identifier: GPL-3.0-only
"""Studio: the digital twin of the FM-1 – fetch from the device, play, refine, render as WAV, write back.

The work is done by ``window.studio`` (ui/studio_service.py) with fm1companion/studio.py; this page is the
workspace for it: source, versions, the playable FM-1 (screen, LEDs, keys, knobs), projects of the
twin, WAV export (also individual tracks), studio state with undo, writing back to the FM-1.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import html
import re
import time
from pathlib import Path

from PyQt6.QtCore import QEvent, Qt, QTimer
from PyQt6.QtGui import QKeySequence, QPalette, QShortcut
from PyQt6.QtWidgets import (QBoxLayout, QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                             QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                             QProgressDialog, QPushButton, QRadioButton, QSizePolicy, QSpinBox, QTreeWidget,
                             QTreeWidgetItem, QVBoxLayout, QWidget)

from ... import autobackup as AB, backup, config, studio as S
from .. import web
from ..common import button, confirm, flow_row, fw_label, heading, icon, label, run_job, show_error
from . import PageSpec
from ...i18n import _, ngettext, pgettext

STEMS_TIP = _("Single tracks (stems): one WAV file per track (Melodee, SLOOP and KARMA 1–4, Skiff and JIANT FM 1–6, "
               "CoralFlower's chords, bass, layer and drums), as long as the mix. Each "
               "track is rendered on its own – with its share of the shared effects (reverb, delay, master) – from the "
               "same starting point as the mix, so the files line up in time when you put them on top of each other in "
               "Logic or Audacity. Their sum is not the mix: every stem passes the master section alone, so levels and "
               "dynamics differ.")


def _safe_name(s: str) -> str:
    return re.sub(r"[^\w\-]+", "_", s or "").strip("_") or "FM-1"


# ============================================================================ Dialogs
class BackupPicker(QDialog):
    """Choose a backup from the backup list (all devices), newest first; “Other file …” for everything else."""

    def __init__(self, parent, root: Path):
        super().__init__(parent)
        self.setWindowTitle(_("Open backup in the studio"))
        self.resize(640, 420)
        self.path: Path | None = None
        lay = QVBoxLayout(self)
        lay.addWidget(label(_("Which backup should the twin load? {firmwares} backups of all devices, newest first – "
                              "the twin of the backup's firmware starts. Nothing changes on the FM-1.").format(
                                  firmwares=S.firmware_names()), "muted"))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([_("Date"), _("Device"), "Firmware", pgettext("library", "Note")])
        self.tree.setRootIsDecorated(False)
        self.tree.itemDoubleClicked.connect(lambda *_: self.accept())
        lay.addWidget(self.tree, 1)
        try:
            metas = AB.list_backups(root)
        except OSError:
            metas = []
        for m in metas:
            if m.fmt not in ("felucca-backup", "sloop-backup"):
                continue
            it = QTreeWidgetItem([m.when.astimezone().strftime(_("%Y-%m-%d %H:%M")), m.device or m.path.parent.name,
                                  fw_label(m.firmware) if m.firmware else (m.fw_key or "?"), m.note])
            it.setData(0, Qt.ItemDataRole.UserRole, str(m.path))
            it.setToolTip(0, str(m.path))
            self.tree.addTopLevelItem(it)
        if self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
        for c in range(3):
            self.tree.resizeColumnToContents(c)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Open).setText(_("Open"))
        bb.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Cancel"))
        other = bb.addButton(_("Other file …"), QDialogButtonBox.ButtonRole.ActionRole)
        other.clicked.connect(lambda: self._other(root))
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _other(self, root):
        p, _filter = QFileDialog.getOpenFileName(self, _("Choose backup file"), str(root),
                                             _("FM-1 backup (*.json);;X0X backup (*.x0b);;LOOM-8 backup (*.loom8)"))
        if p:
            self.path = Path(p)
            super().accept()

    def accept(self):
        it = self.tree.currentItem()
        if it is not None:
            self.path = Path(it.data(0, Qt.ItemDataRole.UserRole))
        super().accept()


class ExportDialog(QDialog):
    """“Export as WAV”: length, individual tracks, format, folder."""

    def __init__(self, parent, cfg: dict, name: str, loop_seconds: float | None, firmware: str = "melodee"):
        super().__init__(parent)
        self.setWindowTitle(_("Export as WAV"))
        self.cfg = cfg
        self.song_ends = S.song_ends(firmware)
        self.name = name
        lay = QVBoxLayout(self)
        lay.addWidget(label(_("“{name}” is rendered from the current state of the twin – faster than real time, with "
                              "exactly the sound of the firmware. 44.1 kHz stereo like the FM-1.").format(
            name=html.escape(name)), "muted"))
        box = QGroupBox(_("Length"))
        bl = QVBoxLayout(box)
        self.r_song = QRadioButton(_("Whole song to the end (song chain from row 1, plus 2 s fade-out)"))
        self.r_secs = QRadioButton(_("Fixed length (pattern from the start, PLAY):"))
        self.r_loops = QRadioButton(_("Passes of the pattern:"))
        self.secs = QSpinBox()
        self.secs.setRange(1, 900)
        self.secs.setSuffix(" s")
        self.secs.setValue(int(cfg.get("export_seconds", 30)))
        self.loops = QSpinBox()
        self.loops.setRange(1, 64)
        self.loops.setValue(int(cfg.get("export_loops", 2)))
        self.loop_seconds = loop_seconds
        grp = QButtonGroup(self)
        for r in (self.r_song, self.r_secs, self.r_loops):
            grp.addButton(r)
        bl.addWidget(self.r_song)
        bl.addLayout(_row(self.r_secs, self.secs))
        bl.addLayout(_row(self.r_loops, self.loops))
        if not loop_seconds:
            self.r_loops.setEnabled(False)
            self.loops.setEnabled(False)
            self.r_loops.setToolTip(_("The pattern length could not be read."))
        else:
            self.r_loops.setToolTip(_("One pass ≈ {seconds} s").format(seconds=f"{loop_seconds:.1f}"))
        fw_name = S.firmware_name(firmware)
        if not S.has_song(firmware):                    # (a loop, a pattern, a twin that plays itself: nothing ends)
            self.r_song.setEnabled(False)
            self.r_song.setToolTip(_("{firmware} has no song the twin could play to an end, so this export would not "
                                     "stop by itself. Choose a fixed length.").format(firmware=fw_name))
        elif not self.song_ends:                        # (Skiff's SONG ONCE, SLOOP's song order …: only if set so)
            self.r_song.setToolTip(_("With {firmware} this ends only if the twin's song is set to play once and stop. "
                                     "A song that loops never ends: the export then runs to the limit of 15 minutes "
                                     "(you can cancel it). A fixed length is the safe choice.").format(firmware=fw_name))
        else:
            self.r_song.setToolTip(_("Plays the song chain from row 1 until it stops. Without a song chain nothing "
                                     "starts, and the export ends after a few seconds."))
        # the default: the whole song only where a song always ends (Melodee), a fixed length everywhere else; the
        # user's own last choice is kept where it is possible at all
        mode = cfg.get("export_mode") or ("song" if self.song_ends else "secs")
        if mode == "song" and (not S.has_song(firmware) or (not self.song_ends and not cfg.get("export_mode_chosen"))):
            mode = "secs"
        {"song": self.r_song, "loops": self.r_loops if loop_seconds else self.r_secs}.get(mode, self.r_secs).setChecked(True)
        lay.addWidget(box)
        form = QFormLayout()
        self.fmt = QComboBox()
        self.fmt.addItem(_("WAV 24 bit (default)"), "s24")
        self.fmt.addItem(_("WAV 32 bit floating point"), "f32")
        self.fmt.setCurrentIndex(max(0, self.fmt.findData(cfg.get("export_fmt", "s24"))))
        form.addRow(_("Format:"), self.fmt)
        self.stems = QCheckBox(_("Single tracks (stems) as well"))
        self.stems.setChecked(bool(cfg.get("export_stems", False)))
        self.stems.setToolTip(STEMS_TIP)
        form.addRow("", self.stems)
        from ..studio_service import music_dir
        self.folder = QLineEdit(cfg.get("export_dir") or str(music_dir()))
        b = QPushButton(icon("folder-open"), "…")
        b.setToolTip(_("Choose folder"))
        b.clicked.connect(self._pick)
        form.addRow(_("Folder:"), _row(self.folder, b))
        self.fname = QLineEdit(f"{_safe_name(name)}_{dt.datetime.now():%Y-%m-%d_%H-%M}.wav")
        form.addRow(_("File:"), self.fname)
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText(_("Export"))
        bb.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Cancel"))
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _pick(self):
        d = QFileDialog.getExistingDirectory(self, _("Export folder"), self.folder.text())
        if d:
            self.folder.setText(d)

    def params(self) -> dict:
        mode = "song" if self.r_song.isChecked() else "secs" if self.r_secs.isChecked() else "loops"
        self.cfg.update(export_mode=mode, export_mode_chosen=True, export_seconds=self.secs.value(),
                        export_loops=self.loops.value(),
                        export_fmt=self.fmt.currentData(), export_stems=self.stems.isChecked(),
                        export_dir=self.folder.text().strip())
        try:
            config.save()
        except OSError:
            pass
        name = self.fname.text().strip() or "Studio.wav"
        if not name.lower().endswith(".wav"):
            name += ".wav"
        p = {"path": Path(self.folder.text().strip() or ".").expanduser() / name, "fmt": self.fmt.currentData(),
             "stems": self.stems.isChecked()}
        if mode == "song":
            p.update(until_end=True, start="song")
        elif mode == "secs":
            p.update(seconds=float(self.secs.value()), start="play")
        else:
            p.update(seconds=round(self.loops.value() * (self.loop_seconds or 2.0), 3), start="play",
                     tail_seconds=0)
        return p


class WriteBackDialog(QDialog):
    """Confirmation for writing back – names the device, safety backup, options."""

    def __init__(self, parent, device: str, dev_version: str, twin_version: str, mismatch: str, rejected: list[int],
                 firmware: str = "melodee", origin: str = "", origin_kind: str = "device", same_device: bool = True,
                 refused: str = ""):
        """origin: where the twin's content came from (StudioService.source_text); origin_kind: the source's kind
        ("new" = started empty); same_device: it was fetched from exactly this FM-1 – otherwise a red warning and a
        second tick; refused: a reason why this write back is not possible at all (no expert override)."""
        super().__init__(parent)
        self.setWindowTitle(_("Write back to the FM-1"))
        lay = QVBoxLayout(self)
        what = (_("First the app automatically makes a <b>safety backup</b> of this FM-1 (Backups › List). Then the "
                  "twin replaces on the device: the four projects A–D, the pattern bank, the running music, the user "
                  "presets and the sample slots. Playback on the FM-1 stops in the process.") if firmware == "skiff" else
                _("First the app automatically makes a <b>safety backup</b> of this FM-1 (Backups › List). Then the "
                  "twin replaces on the device: every song's sections A–D and the song index, the running music and "
                  "the user presets. Playback on the FM-1 stops in the process.") if firmware == "jiant" else
                _("First the app automatically makes a <b>safety backup</b> of this FM-1 (Backups › List). Then the "
                  "twin replaces on the device, in SLOOP's order: the user preset banks, the FM6 patch bank, the SYN drum "
                  "kits, the sections A–D, the running project and the sample slots. Playback on the FM-1 must be "
                  "stopped (STOP) – SLOOP refuses otherwise.") if firmware == "sloop" else
                _("First the app automatically makes a <b>safety backup</b> of this FM-1 (Backups › List). Then the "
                  "twin replaces on the device: the sequencer's patterns and song, the patterns' sounds, the user "
                  "sounds and the sounds' unsaved edits. Playback on the FM-1 stops, and the FM-1 restarts at the end "
                  "to load them.") if firmware == "coralflower" else
                _("First the app automatically makes a <b>safety backup</b> of this FM-1 (Backups › List). Then the "
                  "twin replaces on the device: the ten loop slots, the user sounds and the CZ-1 banks, the favorites "
                  "and the settings. A loop playing on the FM-1 must be stopped, and the FM-1 restarts at the end to "
                  "load them.") if firmware == "choralroot" else
                _("First the app automatically makes a <b>safety backup</b> of this FM-1 (Backups › List). Then the "
                  "twin replaces on the device: the song's parts A–D, the running music, the song order, the user "
                  "presets, the HEX drum kits and the sample slots. Playback on the FM-1 must be stopped (STOP) – "
                  "KARMA refuses otherwise, and nothing is written.")
                if firmware == "karma" else
                _("First the app automatically makes a <b>safety backup</b> of this FM-1 (Backups › List). Then the "
                  "twin replaces on the device: the four projects A–D, the running music, the user presets with their "
                  "FM6 patches and the sample slots USR1–USR3. Playback on the FM-1 stops in the process.")
                if firmware == "felucca" else
                _("First the app automatically makes a <b>safety backup</b> of this FM-1 (Backups › List). Then the "
                  "twin replaces on the device, over FM1 Open's file protocol: the four DX7 library banks, every project "
                  "slot the twin has (empty slots stay as they are on the FM-1), the working project and – if it "
                  "differs – the sample bank. Playback on the FM-1 stops in the process.")
                if firmware == "fm1-open" else
                _("First the app automatically makes a <b>safety backup</b> of this FM-1 (Backups › List). Then the "
                  "twin replaces X0X's project on the device, whole: the sound, the 32 patterns, the song, the knob "
                  "motion and the settings (the break loops in the sample slots stay). X0X must be stopped, and it "
                  "asks on its screen: press <b>SEL</b> on the FM-1 to confirm.") if firmware == "x0x" else
                _("First the app automatically makes a <b>safety backup</b> of this FM-1 (Backups › List). Then the "
                  "twin replaces LOOM-8's user presets and its three projects on the device, all of them (its sample "
                  "bank stays). LOOM-8 must be stopped, and it asks on its screen: press <b>SEL</b> on the FM-1 to "
                  "confirm.") if firmware == "loom-8" else
                _("First the app automatically makes a <b>safety backup</b> of this FM-1 (Backups › List). Then the "
                  "twin replaces on the device: all 32 project slots, the running music and the user presets. "
                  "Playback on the FM-1 stops in the process."))
        text = (_("Write the state of the studio twin ({twin}) to <b>“{device}”</b> ({firmware})?").format(
                    twin=html.escape(f"{S.firmware_name(firmware)} {twin_version}"), device=html.escape(device),
                    firmware=html.escape(fw_label(dev_version)))
                + "<br><br>" + what)
        if rejected:
            text += "<br><br>" + ngettext(
                "{n} part was not taken over by the twin when loading – it stays unchanged on the FM-1.",
                "{n} parts were not taken over by the twin when loading – they stay unchanged on the FM-1.",
                len(rejected)).format(n=len(rejected))
        lab = label(text)
        lab.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(lab)
        if origin:                                      # where what is about to go onto the FM-1 came from
            self.origin = label(_("The twin's content: {source}.").format(source=html.escape(origin)))
            lay.addWidget(self.origin)
        self.replace = QCheckBox(_("Yes, replace the FM-1's content with this twin"))
        self.need_replace = not same_device
        self.foreign = None
        if same_device:
            lay.addWidget(label(_("Parts you have not changed in the studio since fetching them from this FM-1 are not "
                                  "written again – they stay as they are on the device."), "muted"))
        else:
            self.foreign = label("⚠ " + (
                _("This twin was started empty – nothing in it was fetched from “{device}”. Everything listed above is "
                  "replaced on the FM-1 by the twin's content: what you have there now will be gone from the device "
                  "(the safety backup keeps a copy).") if origin_kind == "new" else
                _("The twin's content was not fetched from “{device}” – it comes from somewhere else (see above). "
                  "Everything listed above is replaced on the FM-1 by it: what you have there now will be gone from "
                  "the device (the safety backup keeps a copy).")).format(device=html.escape(device)))
            self.foreign.setProperty("role", "error")
            self.foreign.setStyleSheet("color: #d93025; font-weight: 600;")
            lay.addWidget(self.foreign)
            lay.addWidget(self.replace)
        self.settings = QCheckBox(_("Also write the device settings (GLO › SYSTEM, MIDI channels, panel …)"))
        self.settings.setToolTip(_("Default: off – the settings on the FM-1 stay as they are."))
        self.settings.setVisible(firmware != "fm1-open")     # (FM1 Open's backup has no settings object)
        lay.addWidget(self.settings)
        self.force = QCheckBox(_("Expert: write despite a different firmware version"))
        self.force.setVisible(bool(mismatch) and not refused)
        self.refused = refused
        if refused:                                     # (JIANT FM across its 8-song/6-song change: no override)
            warn = label("⚠ " + refused)
            warn.setProperty("role", "warning")
            lay.addWidget(warn)
        elif mismatch:
            warn = label("⚠ " + mismatch + " " + _("The twin's data might not fit this version; without the tick the "
                                                   "app writes nothing."))
            warn.setProperty("role", "warning")
            lay.addWidget(warn)
            lay.addWidget(self.force)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok = bb.button(QDialogButtonBox.StandardButton.Ok)
        self.ok.setText(_("Make safety backup and write back"))
        self.ok.setProperty("variant", "danger")
        bb.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Cancel"))
        bb.button(QDialogButtonBox.StandardButton.Cancel).setDefault(True)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.mismatch = bool(mismatch)
        self.force.toggled.connect(lambda _on: self._update_ok())
        self.replace.toggled.connect(lambda _on: self._update_ok())
        self._update_ok()

    def _update_ok(self):
        """the red button only when every required tick is set (expert override, replacing foreign content)"""
        self.ok.setEnabled(not self.refused and (not self.mismatch or self.force.isChecked())
                           and (not self.need_replace or self.replace.isChecked()))


def _row(*widgets) -> QHBoxLayout:
    h = QHBoxLayout()
    h.setContentsMargins(0, 0, 0, 0)
    for w in widgets:
        h.addWidget(w)
    h.addStretch(0)
    return h


# ============================================================================ Page
class StudioPage(QWidget):
    WIDE = 980                                   # below: control surface at the top, tools below it

    def __init__(self, ctx, window):
        super().__init__()
        self.ctx, self.window = ctx, window
        self.svc = getattr(window, "studio", None)
        self.view = None
        self._blank = None
        self._ran_restore = False
        self._active = False                         # this page is the one shown (activate/deactivate)
        self._win_hidden = False                     # … but the window is minimised or hidden
        self.last_export: Path | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(heading(_("Studio"), _("The digital twin of your FM-1: the same firmware, the same sound – refine "
                                             "here, output as WAV and write back to the FM-1.")))
        # ---- Source
        self.b_fetch = button(_("Fetch from FM-1"), ("go-down", "document-import"), "primary", self.fetch_from_device)
        self.b_backup = button(_("Open from backup …"), ("document-open",), "", lambda: self.open_backup(),
                               _("Load a saved backup into the twin (nothing changes on the FM-1)"))
        self.b_last = button(_("Open last studio state"), ("document-open-recent", "view-history"), "",
                             self.open_last, _("Load the automatically saved state of the studio again"))
        self.b_virtual = button(_("Use as device"), ("computer", "audio-card"), "", self.use_as_device,
                                _("Choose the twin as a (virtual) device at the top: projects, compose, presets, "
                                  "backups and the editor then work with it instead of a real FM-1."))
        # (in the top row, so it is in view without scrolling – also at 1024×600; it was at the bottom of the tools)
        self.b_export = button(_("Export as WAV …"), ("document-save", "media-record"), "primary",
                               lambda: self.export_wav(), _("Song or pattern as WAV (24 bit) – optionally single tracks.") + " " + STEMS_TIP)
        lay.addWidget(flow_row(self.b_fetch, self.b_backup, self.b_last, self.b_export, self.b_virtual))
        self.export_state = label("", "muted")
        self.export_state.setVisible(False)
        lay.addWidget(self.export_state)
        self.src = label("", "muted")
        self.src.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.src)
        self.banner = label("")
        self.banner.setTextFormat(Qt.TextFormat.RichText)
        self.banner.setProperty("role", "badge")
        lay.addWidget(self.banner)

        # ---- Body: control surface + tools
        self.body = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.body.setSpacing(12)
        lay.addLayout(self.body, 1)
        self.panel_box = QWidget()
        pl = QVBoxLayout(self.panel_box)
        pl.setContentsMargins(0, 0, 0, 0)
        if web.HAVE_WEBENGINE:
            from PyQt6.QtWebEngineCore import QWebEnginePage
            from PyQt6.QtWebEngineWidgets import QWebEngineView
            self.view = QWebEngineView(self.panel_box)
            self._blank = QWebEnginePage(web.profile(), self.view)
            self.view.setPage(self._blank)
            self.view.setMinimumSize(280, 200)
            self.view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            self.view.setToolTip("")
            web.track(self.view)
            pl.addWidget(self.view, 1)
        else:
            pl.addWidget(label(_("The studio needs Qt WebEngine: {command}").format(
                command="sudo apt install python3-pyqt6.qtwebengine")))
        self.keys_hint = label(_("Play: mouse, computer keyboard (Z S X D C … and Q 2 W 3 E …), MIDI keyboard. "
                                 "Space: start/stop. Knobs: drag or mouse wheel."), "muted")
        pl.addWidget(self.keys_hint)
        self.body.addWidget(self.panel_box, 3)

        side = QWidget()
        side.setMinimumWidth(300)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 0, 0, 0)
        # playback + projects
        g = QGroupBox(_("Playback and projects"))
        gl = QVBoxLayout(g)
        self.b_play = button(_("Play"), ("media-playback-start",), "primary", lambda: self.play("play"),
                             _("PLAY on the twin: the running pattern from the start (Space)"))
        self.b_song = button(pgettext("play the song chain", "Song"), ("media-seek-forward", "media-playback-start"), "", lambda: self.play("song"),
                             _("Play the song chain from row 1"))
        self.b_stop = button(_("Stop"), ("media-playback-stop",), "", self.stop, _("Stop (Space)"))
        gl.addLayout(_row(self.b_play, self.b_song, self.b_stop))
        self.t_state = label("", "muted")
        gl.addWidget(self.t_state)
        self.projects = QListWidget()
        self.projects.setMinimumHeight(140)
        self.projects.setToolTip(_("The twin's 32 project slots. A double-click loads a project (like the PROJECT "
                                   "page on the FM-1)."))
        self.projects.itemDoubleClicked.connect(lambda *_: self.load_project())
        gl.addWidget(self.projects, 1)
        self.b_load = button(_("Load project"), ("document-open",), "", self.load_project,
                             _("Load the chosen project into the running music (stop first)"))
        self.b_refresh = button("", ("view-refresh",), "flat", self.refresh_projects, _("Read the list again"))
        gl.addLayout(_row(self.b_load, self.b_refresh))
        sl.addWidget(g, 1)
        # sound and MIDI
        g = QGroupBox(_("Sound and MIDI"))
        gl = QVBoxLayout(g)
        self.cb_audio = QCheckBox(_("Sound through the computer"))
        self.cb_audio.toggled.connect(self._audio_toggled)
        gl.addWidget(self.cb_audio)
        self.out_label = label("", "muted")
        gl.addWidget(self.out_label)
        self.audio_hint = label("", "muted")              # dropouts (studio-worklet.js), only when there were any
        self.audio_hint.setVisible(False)
        gl.addWidget(self.audio_hint)
        self.midi = QComboBox()
        self.midi.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.midi.setMinimumContentsLength(10)
        self.midi.setToolTip(_("MIDI keyboard that plays in the twin"))
        self.midi.activated.connect(self._midi_chosen)
        b_scan = button("", ("view-refresh",), "flat", self.refresh_midi, _("Search for MIDI inputs again"))
        row = QHBoxLayout()
        row.addWidget(QLabel(_("MIDI input:")))
        row.addWidget(self.midi, 1)
        row.addWidget(b_scan)
        gl.addLayout(row)
        self.cb_fm1 = QCheckBox(_("Keys of the real FM-1 play along"))
        self.cb_fm1.setToolTip(_("What you play on the connected FM-1 also sounds in the twin (notes/knobs only)."))
        self.cb_fm1.toggled.connect(lambda on: self.svc and self.svc.set_fm1_keys(on))
        gl.addWidget(self.cb_fm1)
        sl.addWidget(g)
        # State
        g = QGroupBox(_("Studio state"))
        gl = QVBoxLayout(g)
        self.saved = label("", "muted")
        gl.addWidget(self.saved)
        self.b_snap = button(_("Remember intermediate state"), ("bookmark-new", "document-save"), "", self.snap,
                             _("Add the current state to the undo list"))
        self.b_undo = button(_("Undo"), ("edit-undo",), "", self.undo, _("To the previous intermediate state (Ctrl+Z)"))
        self.b_redo = button(pgettext("undo", "Redo"), ("edit-redo",), "", self.redo, _("Ctrl+Shift+Z"))
        gl.addWidget(self.b_snap)
        gl.addLayout(_row(self.b_undo, self.b_redo))
        sl.addWidget(g)
        # write back
        self.b_write = button(_("Write back to the FM-1 …"), ("go-up", "document-export"), "danger", lambda: self.write_back())
        sl.addWidget(self.b_write)
        self.side = side
        from PyQt6.QtWidgets import QFrame, QScrollArea
        self.side_scroll = QScrollArea()          # wide: tools scroll next to the control surface
        self.side_scroll.setWidgetResizable(True)
        self.side_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.side_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.side_scroll.setWidget(side)
        self.body.addWidget(self.side_scroll, 2)

        self._last_key = ("", 0.0)
        for seq, act in (("Space", "toggle_play"), ("Ctrl+Z", "undo"), ("Ctrl+Shift+Z", "redo"), ("Ctrl+Y", "redo")):
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(lambda a=act: self._key_action(a))
        self.timer = QTimer(self, interval=700)
        self.timer.timeout.connect(self._tick)
        if self.svc is not None:
            self.svc.changed.connect(self.refresh)
            self.svc.history_changed.connect(self._update_history)
            self.svc.page_changed.connect(self._attach_page)
            self.svc.ui_event.connect(self._ui_event)
            self.svc.crashed.connect(self._twin_crashed)
            self.svc.audio_changed.connect(self._audio_hint)
            self.cb_audio.setChecked(bool(self.svc.cfg.get("audio", True)))
            self.cb_fm1.setChecked(bool(self.svc.cfg.get("fm1_keys", True)))
        window.installEventFilter(self)              # minimised / hidden: pause what nothing needs (eventFilter)
        ctx.changed.connect(self.refresh)
        self.refresh_midi()
        self.refresh()

    # ------------------------------------------------------------------ Layout/size
    def resizeEvent(self, e):
        super().resizeEvent(e)
        wide = self.width() >= self.WIDE
        d = QBoxLayout.Direction.LeftToRight if wide else QBoxLayout.Direction.TopToBottom
        if self.body.direction() != d:
            self.body.setDirection(d)
        # narrow: tools stacked at full height (the whole page scrolls), wide: own scroll area
        self.side_scroll.setMinimumHeight(0 if wide else self.side.sizeHint().height())
        self.side_scroll.setMinimumWidth(self.side.minimumSizeHint().width() + (14 if wide else 0))
        if self.view is not None:                 # whole control surface visible: height from the width
            w = self.panel_box.width() if wide else self.width()
            self.view.setMinimumHeight(max(200, min(560, int(w * 0.64) + 10)))

    def _colors(self) -> tuple[str, str]:
        pal = self.palette()
        return pal.color(QPalette.ColorRole.Window).name(), pal.color(QPalette.ColorRole.WindowText).name()

    def _attach_page(self):
        if self.view is None:
            return
        page = self.svc.page if self.svc is not None else None
        if page is not None:
            if self.view.page() is not page:
                self.view.setPage(page)
            bg, fg = self._colors()
            self.svc.show_panel(self.isVisible(), bg, fg)
        elif self.view.page() is not self._blank:
            self.view.setPage(self._blank)
        if page is None:
            bg, fg = self._colors()
            self._blank.setHtml(f"<html><body style='background:{bg};color:{fg};font-family:sans-serif;"
                                "padding:2em'><h3>" + html.escape(_("The twin is starting …")) + "</h3><p>"
                                + html.escape(_("Or choose a source above.")) + "</p></body></html>")

    # ------------------------------------------------------------------ Visibility
    def activate(self):
        self._active = True
        if self.svc is None:
            return
        self._win_hidden = self._window_hidden()
        if not self.svc.running and not self._ran_restore:
            self._ran_restore = True
            self.svc.set_visible(True)
            self._auto_restore()
        if self._win_hidden:                         # (shown while the window is minimised: stay paused)
            self.refresh()
            return
        self._resume()
        self.refresh()

    def deactivate(self):
        self._active = False
        self._pause()

    def _resume(self):
        self.svc.set_visible(True)
        self._attach_page()
        self.timer.start()

    def _pause(self):
        """the page is not on screen (another page, or the window minimised/hidden): stop the panel's 30 pictures a
        second and this page's polling, save, and let the service stop the twin's clock and sound – it keeps both
        while the twin plays, an export or another job runs, or another page uses the twin."""
        self.timer.stop()
        if self.svc is not None:
            self.svc.show_panel(False)
            self.svc.autosave()
            self.svc.set_visible(False)

    def _window_hidden(self) -> bool:
        w = self.window
        return bool(w.isMinimized() or not w.isVisible())

    def eventFilter(self, obj, ev):
        """the Studio used 9–13 % of a processor core while its page was merely open – also with the window
        minimised. Minimised or hidden counts like "another page is shown" now."""
        if obj is self.window and ev.type() in (QEvent.Type.WindowStateChange, QEvent.Type.Hide, QEvent.Type.Show):
            QTimer.singleShot(0, self._window_state)
        return False

    def _window_state(self):
        hidden = self._window_hidden()
        if hidden == self._win_hidden:
            return
        self._win_hidden = hidden
        if not self._active or self.svc is None:
            return
        if hidden:
            self._pause()
        else:
            self._resume()
            self.refresh()

    def shutdown(self):
        self.timer.stop()

    def _auto_restore(self):
        """Load the studio state of the active device (otherwise of the last used one) – otherwise an empty twin."""
        try:
            self.svc.auto_start()
        except Exception as e:  # noqa: BLE001
            self.src.setText(_("The twin could not be started: {error}").format(error=html.escape(str(e))))

    # ------------------------------------------------------------------ Display
    def _device_ok(self) -> tuple[bool, str]:
        """may the active device be read/written? -> (yes, reason if no)"""
        c = self.ctx
        if c.is_virtual:
            return False, _("The twin itself is active right now – choose a real FM-1 in the device selection at the top.")
        if c.midi_connected and S.stand_in_for(c.firmware_key):
            return False, _("The original firmware has no backup protocol: nothing can be fetched from this FM-1 or "
                            "written to it. The studio plays the FM1 Open twin instead (see the note above).")
        if not c.connected and c.midi_connected and self.svc.firmware_for(c.firmware_key) == "fm1-open":
            return True, ""                             # (FM1 Open: no editor INFO, its own file protocol)
        if not c.connected:
            if c.port is not None and c.protocol == "generic":
                return False, _("This firmware has no editor protocol – the studio needs {firmwares}.").format(
                    firmwares=S.firmware_names("or"))
            return False, _("No FM-1 connected.")
        if not self.svc.firmware_for(c.firmware_key):
            return False, _("The FM-1 runs {firmware} – so far there is a studio twin only for {firmwares}.").format(
                firmware=html.escape(fw_label(c.info.version) if c.info else _('another firmware')),
                firmwares=S.firmware_names())
        return True, ""

    def _dev_version(self) -> str:
        """the active device's firmware version: its editor INFO, else (FM1 Open, the original firmware) the profile's
        version from the package identity ("V14")"""
        c = self.ctx
        if c.info:
            return c.info.version
        return c.profile.firmware_version if c.profile else ""

    def _project_label(self, k: int) -> str:
        """project slot k as the firmware names it: Melodee 1..32, Skiff A..D, JIANT the current song's sections A..D,
        SLOOP its sections A..D, KARMA the song's parts A..D"""
        st = self.svc.st if self.svc is not None else None
        if st is not None and st.build.firmware in ("skiff", "felucca"):      # (Felucca: SAVE's PROJECT A..D)
            return _("Project {n}").format(n="ABCD"[k] if 0 <= k < 4 else k + 1)
        if st is not None and st.build.firmware in ("jiant", "sloop"):
            return _("Section {n}").format(n="ABCD"[k] if 0 <= k < 4 else k + 1)
        if st is not None and st.build.firmware == "karma":           # (KARMA: the song's parts, SAVE + 5..8)
            return _("Part {n}").format(n="ABCD"[k] if 0 <= k < 4 else k + 1)
        return _("Project {n}").format(n=k + 1)

    def refresh(self):
        svc = self.svc
        if svc is None:
            self.src.setText(_("The studio is not available (studio service missing)."))
            for b in (self.b_fetch, self.b_backup, self.b_last, self.b_write, self.b_export):
                b.setEnabled(False)
            return
        c = self.ctx
        name = c.profile.name if (c.profile and not c.is_virtual) else ""
        ok, why = self._device_ok()
        self.b_fetch.setText(_("Fetch from FM-1 “{name}”").format(name=name) if name else _("Fetch from FM-1"))
        self.b_fetch.setEnabled(ok)
        fetch_tip = (_("Read a complete backup from the active FM-1 (also lands in Backups › List) and load it into the "
                       "twin. Playback on the FM-1 stops. FM1 Open has no command to read a saved project directly: the "
                       "FM-1 loads each saved project into its working area in turn, and at the end the app puts your "
                       "working project back. Save unsaved changes on the FM-1 first: if the cable is pulled in "
                       "between, the working project cannot be put back. The saved projects are not written.")
                     if svc.firmware_for(c.firmware_key) == "fm1-open" and not S.stand_in_for(c.firmware_key) else
                     _("Read a complete backup from the active FM-1 (also lands in Backups › List) and load "
                       "it into the twin. Playback on the FM-1 stops briefly; nothing is written there."))
        self.b_fetch.setToolTip(fetch_tip if ok else why)
        self.b_write.setEnabled(ok and svc.running)
        self.b_write.setToolTip(_("Write the state of the twin to the active FM-1 – with confirmation and a safety "
                                  "backup first.") if ok else why)
        self.b_last.setEnabled(bool(svc.stored_states()))
        run = svc.running
        for b in (self.b_play, self.b_song, self.b_stop, self.b_load, self.b_export, self.b_snap, self.b_virtual,
                  self.projects):
            b.setEnabled(run)
        self.b_virtual.setText(_("Is the active device") if c.is_virtual else _("Use as device"))
        self.b_virtual.setEnabled(run and not c.is_virtual)
        if run:
            self.src.setText(_("<b>Twin:</b> {firmware} {version} · {source}").format(
                firmware=html.escape(svc.firmware_name), version=html.escape(svc.version),
                source=html.escape(svc.source_text())))
        else:
            self.src.setText(_("The twin is not running yet. Choose a source: from the FM-1, from a backup or the last "
                               "studio state."))
        self._banner()
        self._update_history()
        self.refresh_projects()
        self._out_text()

    def _banner(self):
        svc, c = self.svc, self.ctx
        text, level = "", ""
        dev_fw = svc.firmware_for(c.firmware_key) if (c.midi_connected and not c.is_virtual) else ""
        stand_in = bool(dev_fw) and S.stand_in_for(c.firmware_key) == dev_fw
        if c.connected and not c.is_virtual and not dev_fw:
            text = _("There is no studio twin for {firmware} yet. So far the studio can do {firmwares} – see Help › "
                     "Studio. Backups of this device cannot be opened here; a {firmwares_or} backup can.").format(
                firmware=html.escape(fw_label(c.info.version) if c.info else _('this firmware')),
                firmwares=S.firmware_names(), firmwares_or=S.firmware_names("or"))
            level = "warning"
        elif stand_in and (not svc.running or svc.firmware == "fm1-open"):
            # (the original V15/V16: the FM1 Open twin stands in – said only while it is that twin which runs)
            text = "⚠ " + html.escape(_(S.STAND_IN_NOTE).format(
                device=self._dev_version() or c.firmware_key, twin=svc.version if svc.running else "V14"))
            level = "warning"
        elif stand_in:                                  # (… and another twin runs, e.g. the last used Melodee state)
            text = "⚠ " + _("The twin runs {twin}. Your FM-1 runs M-VAVE's original firmware {device}: it has no twin "
                            "and no backup protocol, so nothing can be fetched from this FM-1 or written back to "
                            "it.").format(twin=html.escape(f"{svc.firmware_name} {svc.version}"),
                                          device=html.escape(self._dev_version() or c.firmware_key))
            level = "warning"
        elif svc.running and dev_fw and dev_fw != svc.firmware:
            text = "⚠ " + _("The twin runs {twin}, your FM-1 runs {device} – “Fetch from FM-1” starts the {device} twin; "
                            "writing back is not possible like this.").format(
                twin=html.escape(svc.firmware_name), device=html.escape(S.firmware_name(dev_fw)))
            level = "warning"
        elif svc.running:
            dev = self._dev_version() if dev_fw else ""
            vc = S.check_versions(svc.version, device=dev, backup=svc.source.get("backup_version", ""),
                                  firmware=svc.firmware_name)
            msgs = [vc.message] if vc.message else []
            if svc.st is not None and svc.st.warning:
                msgs.append(svc.st.warning)
            if dev and svc.firmware == "felucca":          # (a Felucca fork, or another build: Felucca's twin all the same)
                note = S.felucca_fork_note(c.firmware_key, getattr(c, "identity", ""), svc.st.build.identity)
                if note:
                    msgs.append(note)
            if msgs:
                text, level = "⚠ " + html.escape(" ".join(msgs)), "warning"
            elif dev and svc.firmware == "fm1-open":
                # (same version, but not the same engine: the device's is a closed binary, the twin runs the open
                # engine of M-VAVE's browser simulator – the twin's NOTICE says so; never "identical sound" here)
                text, level = _("Twin and FM-1 have the same firmware version ({firmware} {version}). The twin plays "
                                "with the open engine of M-VAVE's simulator, the FM-1 with its own – the sound may "
                                "differ.").format(firmware=html.escape(svc.firmware_name),
                                                  version=html.escape(svc.version)), ""
            elif dev:
                text, level = "✓ " + _("Twin and FM-1 have the same firmware ({firmware} {version}) – identical "
                                       "sound.").format(firmware=html.escape(svc.firmware_name),
                                                        version=html.escape(svc.version)), "success"
            else:
                text, level = _("Twin: {firmware} {version}").format(firmware=html.escape(svc.firmware_name),
                                                                    version=html.escape(svc.version)), ""
        self.banner.setText(text)
        self.banner.setVisible(bool(text))
        if self.banner.property("level") != level:
            self.banner.setProperty("level", level)
            self.banner.style().unpolish(self.banner)
            self.banner.style().polish(self.banner)

    def _update_history(self):
        svc = self.svc
        if svc is None:
            return
        self.b_undo.setEnabled(svc.can_undo())
        self.b_redo.setEnabled(svc.can_redo())
        if svc.can_undo():
            self.b_undo.setToolTip(_("Back to: {label} (Ctrl+Z)").format(label=svc.history[svc.pos - 1]['label']))
        if svc.can_redo():
            self.b_redo.setToolTip(_("Forward to: {label} (Ctrl+Shift+Z)").format(label=svc.history[svc.pos + 1]['label']))
        if svc.running:
            when = time.strftime("%H:%M:%S", time.localtime(svc.saved_at)) if svc.saved_at else _("not yet")
            self.saved.setText(ngettext("Saved automatically: {time} · with the profile “{profile}” · {n} intermediate state",
                                        "Saved automatically: {time} · with the profile “{profile}” · {n} intermediate states",
                                        len(svc.history)).format(time=when, profile=html.escape(svc.profile_folder()),
                                                                 n=len(svc.history))
                               + (f"<br>{html.escape(svc.last_error)}" if svc.last_error else ""))
            self.saved.setToolTip(str(svc.state_file()) + "\n" + _("The flash is saved – everything the FM-1 still has "
                                                                   "after switching off. Unsaved changes in the running "
                                                                   "project are not included (like on the device: press "
                                                                   "SAVE)."))
        else:
            self.saved.setText("")

    def refresh_projects(self):
        self.projects.clear()
        svc = self.svc
        if svc is None or not svc.running:
            return
        try:
            used = set(svc.st.projects())
            info = svc.st.backend.call("info")
        except S.StudioError:
            return
        cur = info.get("project", 255)
        slots = svc.st.project_slots
        if not slots:                                   # (CoralFlower: its patterns and song, no project slots;
            it = QListWidgetItem(                       # ChoralRoot: its ten loop slots, chosen on the panel)
                _("{firmware} has no project slots: its ten loop slots are chosen on the twin's panel (PLAY held, "
                  "PRESETS); Play plays the loop.").format(firmware=svc.firmware_name) if svc.firmware == "choralroot"
                else _("{firmware} has no project slots: its patterns and the song play from the twin's "
                       "panel (SEQ) or with Play.").format(firmware=svc.firmware_name))
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            self.projects.addItem(it)
        for k in range(slots):
            mark = "  ◀ " + _("loaded") if k == cur else ""
            it = QListWidgetItem(self._project_label(k) + ("" if k in used else " – " + _("empty")) + mark)
            it.setData(Qt.ItemDataRole.UserRole, k)
            if k not in used:
                it.setForeground(self.palette().color(QPalette.ColorRole.PlaceholderText))
            self.projects.addItem(it)
        if 0 <= cur < slots:
            self.projects.setCurrentRow(cur)

    def _out_text(self):
        from ..audio import chosen_output, current_output
        want = chosen_output()
        if not want:
            t = _("Output: the system's default output (PipeWire).")
        else:
            o = None
            try:
                o = current_output()
            except Exception:  # noqa: BLE001
                pass
            t = _("Output: “{name}” (as chosen under “Output” for the preview; the studio moves its sound there as "
                  "soon as it runs).").format(name=o.description if o else want)
        if self.svc is not None and self.svc.audio_note:
            t += " " + self.svc.audio_note
        self.out_label.setText(t)
        self._audio_hint()

    def _audio_hint(self):
        t = self.svc.audio_hint() if self.svc is not None else ""
        self.audio_hint.setText(t)
        self.audio_hint.setVisible(bool(t))

    def _twin_crashed(self, text: str):
        """the twin's process ended (StudioService.crashed): say so, and offer the automatically saved state"""
        svc = self.svc
        self.refresh()
        if not svc.has_autosave():
            self.window.notify(text, "error")
            return
        if self.isVisible() and not self._win_hidden:
            if confirm(self, _("The twin has stopped"), text + "\n\n" + _("Open the automatically saved studio state "
                                                                          "again?"), ok=_("Open saved state")):
                self._reopen_autosave()
        else:
            self.window.notify(text, "error", (_("Open saved state"), lambda: self._reopen_autosave()))

    def _reopen_autosave(self):
        try:
            self.svc.open_state()
        except Exception as e:  # noqa: BLE001
            show_error(self, _("Studio state"), str(e))
            return
        if self._active and not self._win_hidden:
            self._resume()
        self.refresh()

    def _tick(self):
        svc = self.svc
        if svc is None or not svc.running:
            return
        t = svc.transport()
        if not t:
            return
        state = _("plays (song)") if t.get("chain") else _("plays") if t.get("playing") else _("stopped")
        if t.get("rec"):
            state += " · REC"
        self.t_state.setText(_("Twin {state}").format(state=state))
        self._playing = bool(t.get("busy") or t.get("playing"))

    # ------------------------------------------------------------------ Playback
    def play(self, how: str = "play"):
        if self.svc is None or not self.svc.running:
            return
        try:
            self.svc.st.play(how)
        except S.StudioError as e:
            self.t_state.setText(_("Playback not possible: {error}").format(error=e))
        self._tick()

    def stop(self):
        if self.svc is None or not self.svc.running:
            return
        try:
            self.svc.st.stop()
        except S.StudioError as e:
            self.t_state.setText(_("Stop not possible: {error}").format(error=e))
        self._tick()

    def toggle_play(self):
        if self.svc is None or not self.svc.running:
            return
        t = self.svc.transport()
        if t.get("busy") or t.get("playing"):
            self.stop()
        else:
            self.play("play")

    def _key_action(self, action: str):
        """Space/Ctrl+Z – come from Qt or from the control surface depending on focus; never execute twice."""
        last, t = self._last_key
        now = time.monotonic()
        if last == action and now - t < 0.25:
            return
        self._last_key = (action, now)
        {"toggle_play": self.toggle_play, "undo": self.undo, "redo": self.redo}.get(action, lambda: None)()

    def _ui_event(self, action: str):
        if self.isVisible():
            self._key_action(action)

    def load_project(self):
        it = self.projects.currentItem()
        if it is None or self.svc is None or not self.svc.running:
            return
        slot = it.data(Qt.ItemDataRole.UserRole)
        if slot is None:                                # (the note of a twin without project slots)
            return
        try:
            rc = self.svc.st.load_project(slot)
        except S.StudioError as e:
            self.t_state.setText(_("Loading not possible: {error}").format(error=e))
            return
        n = self._project_label(slot).rsplit(" ", 1)[-1]
        if self.svc.firmware in ("jiant", "sloop"):     # (JIANT: the current song's sections; SLOOP's sections)
            done = {0: _("Section {n} loaded.").format(n=n), 1: _("Section {n} is empty.").format(n=n)}
        elif self.svc.firmware == "karma":              # (KARMA: the song's parts)
            done = {0: _("Part {n} loaded.").format(n=n), 1: _("Part {n} is empty.").format(n=n)}
        else:
            done = {0: _("Project {n} loaded.").format(n=n), 1: _("Project {n} is empty.").format(n=n)}
        self.t_state.setText({**done, 2: _("Stop first, then load.")}.get(rc, _("Error {code}").format(code=rc)))
        self.refresh_projects()

    # ------------------------------------------------------------------ Sound/MIDI
    def _audio_toggled(self, on: bool):
        if self.svc is None:
            return
        msg = self.svc.set_audio(on) if self.svc.running else ""
        if not self.svc.running:
            self.svc.cfg["audio"] = on
        self._out_text()
        if msg:
            self.out_label.setText(msg)
        QTimer.singleShot(1500, self._out_text)

    def refresh_midi(self):
        if self.svc is None:
            return
        cur = list(self.svc.cfg.get("inputs") or [])
        names = self.svc.input_names()
        self.midi.clear()
        self.midi.addItem(_("no MIDI keyboard"), "")
        for n in names:
            self.midi.addItem(n, n)
        if cur and cur[0] in names:
            self.midi.setCurrentIndex(self.midi.findData(cur[0]))
            self.svc.set_inputs(cur[:1])

    def _midi_chosen(self, *_args):
        n = self.midi.currentData() or ""
        errs = self.svc.set_inputs([n] if n else [])
        if errs:
            show_error(self, _("MIDI input"), "\n".join(errs))

    # ------------------------------------------------------------------ Sources
    def fetch_from_device(self):
        svc, c = self.svc, self.ctx
        ok, why = self._device_ok()
        if not ok:
            show_error(self, _("Fetch from FM-1"), why)
            return None
        try:
            folder = c.device_folder(c.settings.backups_dir)
        except OSError as e:
            show_error(self, _("Fetch from FM-1"), str(e))
            return None
        prof, fw, version = c.profile, c.firmware_key, self._dev_version()
        tw = svc.firmware_for(fw)
        if tw == "fm1-open" and not confirm(self, _("Fetch from FM-1"), _(
                "Have you saved your changes on the FM-1? FM1 Open cannot hand over a saved project directly: while "
                "fetching, the FM-1 loads each saved project into its working area in turn, and the app puts your "
                "working project back at the end. If the cable is pulled in between, the working project cannot be put "
                "back – unsaved changes would be lost."), ok=_("Fetch")):
            return None
        try:
            st = svc.ensure(S.fw_version(tw, version) if tw == "fm1-open" else S.norm_version(version), tw)
        except Exception as e:  # noqa: BLE001
            show_error(self, _("Studio"), _("The twin does not start: {error}").format(error=e))
            return None
        svc.snapshot(_("before fetching from the FM-1"), only_if_changed=True)
        svc.busy += 1
        box = {}

        def work(w, link):
            if tw == "fm1-open":                        # (said in the dialog too, not only in the tooltip)
                w.log.emit(_("FM1 Open: keep the cable connected. The FM-1 loads each saved project into its working "
                             "area in turn; your working project is put back at the end."))
            w.log.emit(_("Reading the complete backup of “{name}” …").format(name=prof.name))
            path = AB.capture_to_file(link, folder, prof, fw, version, _("fetched for the studio"), "manuell",
                                      progress=lambda a, b: w.progress.emit(a, b * 2))
            w.log.emit(_("Saved in Backups › List: {name}").format(name=path.name))
            arch = backup.load_file(path)
            w.log.emit(_("Loading it into the twin …"))
            st.open_from_backup(arch, progress=lambda a, b: w.progress.emit(b + a, b * 2), tolerant=True)
            box["arch"] = arch
            try:                                        # what the twin holds right after loading: write back later
                box["baseline"] = st.object_digests()   # leaves alone whatever still looks exactly like this
            except Exception:  # noqa: BLE001 – then write back writes everything, as before
                box["baseline"] = {}
            return path

        def done(path):
            box["path"] = path

        try:
            d = c.job(self, _("From the FM-1 into the studio"), work, on_done=done,
                      done=lambda p: _("“{name}” is now in the studio. The backup is also in Backups › List: {file}. "
                                       "Nothing was changed on the FM-1.").format(name=prof.name, file=Path(p).name))
        finally:
            svc.busy -= 1
        if "arch" in box:
            svc.loaded_backup(box["arch"], "device", device=prof.name, profile_folder=prof.folder_name,
                              backup=str(box.get("path", "")), device_version=version,
                              baseline=box.get("baseline") or {})
            bp = getattr(self.window, "backups", None)
            if bp is not None and hasattr(bp, "refresh"):
                bp.refresh()
            return box.get("path")
        return None

    def open_backup(self, path: str | Path | None = None):
        svc = self.svc
        if path is None:
            dlg = BackupPicker(self, self.ctx.settings.backups_dir)
            if not dlg.exec() or dlg.path is None:
                return None
            path = dlg.path
        path = Path(path)
        try:
            arch = backup.load_file(path)
        except Exception as e:  # noqa: BLE001
            show_error(self, _("Open backup"), _("The backup cannot be read: {error}").format(error=e))
            return None
        fw = S.backup_firmware(arch)                    # (SLOOP's file format: the SLOOP or the KARMA twin loads it)
        if fw and fw not in S.FIRMWARES and fw != "felucca":   # (ORBIT: SLOOP's format, but no twin reads it)
            show_error(self, _("Open backup"), _("This backup comes from {firmware} – so far there is a studio twin only "
                                                 "for {firmwares}.").format(firmware=arch.firmware or fw,
                                                                            firmwares=S.firmware_names()))
            return None
        try:
            st = svc.ensure(S.backup_version(arch), fw if fw in S.FIRMWARES else "melodee")
        except Exception as e:  # noqa: BLE001
            show_error(self, _("Studio"), _("The twin does not start: {error}").format(error=e))
            return None
        svc.snapshot(_("before opening a backup"), only_if_changed=True)
        comp = arch.companion or {}
        device = comp.get("device") or path.parent.name
        folder = comp.get("device") and path.parent.name or "Studio"
        svc.busy += 1
        try:
            from ..common import JobDialog
            d = JobDialog(self, _("Backup into the studio"), lambda w: st.open_from_backup(
                arch, progress=lambda a, b: w.progress.emit(a, b), tolerant=True))
            d.exec()
            ok = getattr(d, "ok", True)
        finally:
            svc.busy -= 1
        if not ok:
            return None
        svc.loaded_backup(arch, "backup", device=device, profile_folder=folder, backup=str(path))
        return path

    def open_last(self):
        svc = self.svc
        states = svc.stored_states()
        if not states:
            show_error(self, _("Studio state"), _("There is no saved studio state yet."))
            return
        folder = svc.profile_folder()
        names = [s[0] for s in states]
        if len(states) > 1:
            from PyQt6.QtWidgets import QInputDialog
            cur = names.index(folder) if folder in names else 0
            item, ok = QInputDialog.getItem(self, _("Open studio state"), _("State of which device?"), names, cur, False)
            if not ok:
                return
            folder = item
        elif folder not in names:
            folder = names[0]
        try:
            meta = svc.open_state(folder)
        except Exception as e:  # noqa: BLE001
            show_error(self, _("Studio state"), str(e))
            return
        if meta.get("warning"):
            self.window.notify(meta["warning"], "warning")

    def use_as_device(self):
        if self.svc is not None and self.svc.activate_virtual():
            self.window.notify(_("Active device: the studio twin (virtual). Projects, compose, presets and backups now "
                                 "work with it – choose a real FM-1 at the top again to go back."), "info")

    # ------------------------------------------------------------------ State
    def snap(self):
        if self.svc is not None and self.svc.snapshot(_("Intermediate state {time}").format(time=time.strftime("%H:%M:%S"))):
            self.svc.autosave(force=True)
            self.window.statusBar().showMessage(_("Intermediate state remembered."), 4000)

    def undo(self):
        if self.svc is not None:
            lab = self.svc.undo()
            if lab:
                self.window.statusBar().showMessage(_("Undo: back to “{label}”.").format(label=lab), 5000)

    def redo(self):
        if self.svc is not None:
            lab = self.svc.redo()
            if lab:
                self.window.statusBar().showMessage(_("Restored: “{label}”.").format(label=lab), 5000)

    # ------------------------------------------------------------------ Export
    def _running_music(self) -> tuple[str, float | None]:
        """Name and length of one pattern pass (s) of the twin's running music."""
        from ... import projectfmt as PF
        svc = self.svc
        name = ""
        try:
            info = svc.st.backend.call("info")
            cur = info.get("project", 255)
            if 0 <= cur < svc.st.project_slots:
                name = self._project_label(cur)
            if svc.st.build.firmware != "melodee":     # (projectfmt reads Melodee's projects, not Skiff's / JIANT's)
                return name or "Studio", None
            link = svc.link
            objs = backup.list_objects(link)
            o = next((x for x in objs if x.id == 0 and x.size), None)
            if o is None:
                return name or "Studio", None
            prj = PF.decode(backup.read_object(link, o), "melodee")
            if prj.name:
                name = prj.name
            q = 0.0
            for t in prj.used_tracks:
                if 0 <= t.active < len(t.patterns):
                    p = t.patterns[t.active]
                    q = max(q, p.length * p.step_quarters)
            return name or "Studio", (q * 60.0 / max(20, prj.bpm)) if q else None
        except Exception:  # noqa: BLE001 – only an extra for names and passes
            return name or "Studio", None

    def export_wav(self, params: dict | None = None):
        svc = self.svc
        if svc is None or not svc.running:
            return None
        if params is None:
            name, loop = self._running_music()
            dlg = ExportDialog(self, svc.cfg, name, loop, svc.firmware)
            if not dlg.exec():
                return None
            params = dlg.params()
        stems = bool(params.get("stems"))
        tracks = svc.st.tracks
        path = Path(params.pop("path"))
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # never over an existing file without asking: the next free number (the name carries only the minute)
            path = S.free_export_path(path, tracks if stems else 0)
        except (OSError, S.StudioError) as e:
            show_error(self, pgettext("dialog title", "Export"), _("The folder cannot be created: {error}").format(
                error=getattr(e, "strerror", None) or e))
            return None
        prog = QProgressDialog(_("Rendering …"), _("Cancel"), 0, 1 + tracks if stems else 1, self)
        prog.setWindowTitle(_("Export as WAV"))
        prog.setAutoClose(False)
        prog.setAutoReset(False)
        prog.setMinimumDuration(0)
        prog.setWindowModality(Qt.WindowModality.WindowModal)
        prog.setValue(0)
        res = {}
        st = svc.st
        state = {"cancelled": False}

        def cancel():                                   # really stops the render; its partial files are removed
            if res or state["cancelled"]:
                return
            state["cancelled"] = True
            prog.setLabelText(_("Cancelling …"))
            prog.show()
            st.cancel_render()
        prog.canceled.connect(lambda: cancel())
        self._export_cancel = cancel                    # (for tests)

        def work(w):
            return st.render_wav(path, progress=lambda a, b: w.progress.emit(a, b), **params)

        def step(a, b):
            if state["cancelled"]:
                return
            prog.setMaximum(b)
            prog.setValue(a)
            prog.setLabelText(_("Mix …") if a == 0 else _("Single track {n} of {total} …").format(n=a, total=tracks)
                              if a < b else _("Finished"))

        def done(r):
            res["r"] = r
            prog.close()

        def fail(m, tb):
            res["err"] = m
            prog.close()
        svc.busy += 1
        try:
            w = run_job(self, work, on_done=done, on_fail=fail, on_progress=step)
            while not res:
                from PyQt6.QtWidgets import QApplication
                QApplication.processEvents()
                time.sleep(0.005)
        finally:
            svc.busy -= 1
            self._export_cancel = None
        del w
        if state["cancelled"] and "err" in res:
            self._export_note(_("Export cancelled – no file was written."))
            return None
        if "err" in res:
            show_error(self, pgettext("dialog title", "Export"), _("Rendering failed: {error}").format(error=res['err']))
            return None
        r = res["r"]
        self.last_export = r.path
        extra = " + " + ngettext("{n} single track", "{n} single tracks", len(r.stems)).format(n=len(r.stems)) if r.stems else ""
        note = ""
        if params.get("until_end") and not r.started:
            note = " – " + _("nothing started playing: the twin has no song chain to play. Choose a fixed length instead.")
        elif params.get("until_end") and not r.ended:
            note = " – " + _("the song did not end by itself: the export stopped at the limit of {minutes} minutes."
                             ).format(minutes=round(params.get("max_seconds", 900) / 60))
        elif r.peak <= 0.0005:
            note = " – " + _("Attention: silent (is anything playing?)")
        self._export_note(_("Saved: {file}").format(file=html.escape(r.path.name)) + f"{extra} ({r.seconds:.1f} s)" + note,
                          str(r.path))
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        self.window.notify(_("WAV saved: {name}").format(name=r.path.name) + extra, "success",
                           (_("Open folder"), lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(r.path.parent)))))
        return r

    def _export_note(self, text: str, tip: str = ""):
        self.export_state.setText(text)
        self.export_state.setToolTip(tip)
        self.export_state.setVisible(bool(text))

    # ------------------------------------------------------------------ Write back
    def write_back(self, confirmed: dict | None = None):
        svc, c = self.svc, self.ctx
        ok, why = self._device_ok()
        if not ok or svc is None or not svc.running:
            show_error(self, _("Write back to the FM-1"), why or _("The twin is not running."))
            return None
        dev_v = self._dev_version()
        if svc.firmware_for(c.firmware_key) != svc.firmware:
            show_error(self, _("Write back to the FM-1"), _(
                "The twin runs {twin}, the FM-1 runs {device} – their data do not fit each other. Fetch from the FM-1 "
                "first (that starts the {device} twin). Nothing was written.").format(
                twin=svc.firmware_name, device=S.firmware_name(svc.firmware_for(c.firmware_key))))
            return None
        vc = S.check_versions(svc.version, device=dev_v, firmware=svc.firmware_name)
        refused = _(S.JIANT_SONGS_REFUSAL) if svc.firmware == "jiant" and S.jiant_songs_differ(svc.version, dev_v) else ""
        same = svc.same_device(c.profile)               # fetched from exactly this FM-1?
        from ..risk_dialog import ensure_risk_ack
        if not ensure_risk_ack(self):
            return None
        if confirmed is None:
            dlg = WriteBackDialog(self, c.profile.name, dev_v, svc.version, "" if vc.ok else vc.message,
                                  list(svc.st.rejected), svc.firmware, origin=svc.source_text(),
                                  origin_kind=svc.source.get("kind", ""), same_device=same, refused=refused)
            if not dlg.exec():
                return None
            confirmed = {"settings": dlg.settings.isChecked(), "force": dlg.force.isChecked(),
                         "replace": dlg.replace.isChecked()}
        if refused:
            show_error(self, _("Not written"), refused + " " + _("Nothing was written."))
            return None
        if not vc.ok and not confirmed.get("force"):
            show_error(self, _("Not written"), vc.message + " " + _("Nothing was written."))
            return None
        if not same and not confirmed.get("replace"):   # (an empty or foreign twin never goes over without this tick)
            show_error(self, _("Not written"), _(
                "The twin's content was not fetched from “{device}”. Writing it back replaces what is on the FM-1 – "
                "that needs the tick “Yes, replace the FM-1's content with this twin”. Nothing was written.").format(
                device=c.profile.name))
            return None
        try:
            folder = c.device_folder(c.settings.backups_dir)
        except OSError as e:
            show_error(self, _("Write back to the FM-1"), str(e))
            return None
        prof, fw = c.profile, c.firmware_key
        st = svc.st
        unchanged = dict(svc.source.get("baseline") or {}) if same else None
        svc.autosave(force=True)
        svc.busy += 1
        box = {}

        def work(w, link):
            w.log.emit(_("Safety backup of “{name}” …").format(name=prof.name))
            safety = AB.capture_to_file(link, folder, prof, fw, dev_v, _("Safety copy before writing back from the studio"),
                                        "sicherheit", progress=lambda a, b: w.progress.emit(a, b * 2))
            backup.load_file(safety)                    # read back from disk before the first write
            box["safety"] = safety
            w.log.emit(_("Backed up and read back from disk: {name}").format(name=safety.name))
            w.log.emit(_("Writing the state of the twin …"))
            try:
                st.write_to_device(link, progress=lambda a, b: w.progress.emit(b + a, b * 2),
                                   force=bool(confirmed.get("force")), settings=bool(confirmed.get("settings")),
                                   exclude=st.rejected, unchanged=unchanged)
            except Exception as e:  # noqa: BLE001 – say what is on the FM-1 now and where the safety backup is
                raise S.StudioError(self._write_failed_text(e, safety)) from e
            box["write"] = dict(st.last_write)
            return safety

        def done_text(p):
            wrote, kept = box.get("write", {}).get("written", []), box.get("write", {}).get("unchanged", [])
            if not wrote:
                return _("Nothing in the twin differs from what was fetched from “{name}” – nothing was written. "
                         "Safety backup: {file} (Backups › List).").format(name=prof.name, file=Path(p).name)
            if kept:
                return ngettext(
                    "{n} changed part of the studio is now on “{name}”; the parts you did not change were left as "
                    "they are. Safety backup before: {file} (Backups › List).",
                    "{n} changed parts of the studio are now on “{name}”; the parts you did not change were left as "
                    "they are. Safety backup before: {file} (Backups › List).",
                    len(wrote)).format(n=len(wrote), name=prof.name, file=Path(p).name)
            return _("The state of the studio is now on “{name}”. Safety backup before: {file} (Backups › "
                     "List).").format(name=prof.name, file=Path(p).name)

        try:
            c.job(self, "Studio → FM-1", work, done=done_text)
        finally:
            svc.busy -= 1
        bp = getattr(self.window, "backups", None)
        if bp is not None and hasattr(bp, "refresh"):
            bp.refresh()
        return box.get("safety")

    def _write_failed_text(self, e: BaseException, safety: Path) -> str:
        """a write back that failed: the error, what is already on the FM-1 and which part is unclear (where the
        backup layer knows: its errors carry ``written`` and ``unclear``) and the safety backup made just before"""
        text = str(e)
        src = e if (getattr(e, "written", None) or getattr(e, "unclear", None)) else getattr(e, "__cause__", None)
        if src is not None and (getattr(src, "written", None) or getattr(src, "unclear", None)):
            st = self.svc.st if self.svc is not None else None
            fw = st.build.firmware if st is not None else ""
            planned = list((st.last_write.get("planned") if st is not None else None) or getattr(src, "written", None) or [])
            with contextlib.suppress(Exception):
                text += " " + backup.written_text(src, planned, S.FIRMWARES.get(fw, {}).get("backup") == "sloop")
            text += " " + _("The FM-1 now holds a mix of old and new parts.")
        return text + " " + _("The safety backup made just before is “{file}” (Backups › List), in case you "
                              "need the FM-1's previous state back.").format(file=safety.name)


def _make(ctx, window):
    return StudioPage(ctx, window)


PAGE = PageSpec(key="studio", title=_("Studio"), group="musik", factory=_make,
                icons=("audio-x-generic", "applications-multimedia", "media-playback-start"), order=5,
                description=_("the digital twin of your FM-1: fetch, play, export as WAV, write back"),
                keywords=("zwilling", "twin", "wav", "export", "stems", "einzelspuren", "rendern", "logic",
                          "audacity", "studio", "zurückschreiben", "digital twin", "render", "write back",
                          "single tracks"))
