# SPDX-License-Identifier: GPL-3.0-only
"""Studio: der digitale Zwilling des FM-1 – vom Gerät holen, spielen, verfeinern, als WAV ausgeben, zurückschreiben.

Die Arbeit macht ``window.studio`` (ui/studio_service.py) mit fm1companion/studio.py; diese Seite ist der
Arbeitsplatz dafür: Quelle, Versionen, das spielbare FM-1 (Bildschirm, LEDs, Tasten, Regler), Projekte des
Zwillings, WAV-Export (auch Einzelspuren), Studio-Stand mit Rückgängig, Zurückschreiben aufs FM-1.
"""
from __future__ import annotations

import datetime as dt
import html
import re
import time
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QKeySequence, QPalette, QShortcut
from PyQt6.QtWidgets import (QBoxLayout, QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                             QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                             QProgressDialog, QPushButton, QRadioButton, QSizePolicy, QSpinBox, QTreeWidget,
                             QTreeWidgetItem, QVBoxLayout, QWidget)

from ... import autobackup as AB, backup, config, studio as S
from .. import web
from ..common import button, confirm, flow_row, fw_label, heading, icon, label, run_job, show_error
from . import PageSpec
from ...i18n import _

STEMS_TIP = ("Einzelspuren (Stems): je Spur 1–4 eine eigene WAV-Datei, gleich lang wie der Mix. Jede Spur wird "
             "allein gerendert – mit ihrem Anteil an den gemeinsamen Effekten (Hall, Delay, Master). Darum ist die "
             "Summe der Stems nicht bitgenau der Mix (Master-Dynamik), passt in Logic oder Audacity aber übereinander.")


def _safe_name(s: str) -> str:
    return re.sub(r"[^\w\-]+", "_", s or "").strip("_") or "FM-1"


# ============================================================================ Dialoge
class BackupPicker(QDialog):
    """Backup aus der Backup-Liste wählen (alle Geräte), neueste zuerst; „Andere Datei …“ für alles andere."""

    def __init__(self, parent, root: Path):
        super().__init__(parent)
        self.setWindowTitle("Backup ins Studio öffnen")
        self.resize(640, 420)
        self.path: Path | None = None
        lay = QVBoxLayout(self)
        lay.addWidget(label("Welches Backup soll der Zwilling laden? Melodee-Backups aller Geräte, neueste zuerst. "
                            "Am FM-1 ändert sich dadurch nichts.", "muted"))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Datum", "Gerät", "Firmware", "Notiz"])
        self.tree.setRootIsDecorated(False)
        self.tree.itemDoubleClicked.connect(lambda *_: self.accept())
        lay.addWidget(self.tree, 1)
        try:
            metas = AB.list_backups(root)
        except OSError:
            metas = []
        for m in metas:
            if m.fmt != "felucca-backup" or "sloop" in (m.firmware or "").lower():
                continue
            it = QTreeWidgetItem([m.when.astimezone().strftime("%d.%m.%Y %H:%M"), m.device or m.path.parent.name,
                                  fw_label(m.firmware) if m.firmware else (m.fw_key or "?"), m.note])
            it.setData(0, Qt.ItemDataRole.UserRole, str(m.path))
            it.setToolTip(0, str(m.path))
            self.tree.addTopLevelItem(it)
        if self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
        for c in range(3):
            self.tree.resizeColumnToContents(c)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Open).setText("Öffnen")
        bb.button(QDialogButtonBox.StandardButton.Cancel).setText("Abbrechen")
        other = bb.addButton("Andere Datei …", QDialogButtonBox.ButtonRole.ActionRole)
        other.clicked.connect(lambda: self._other(root))
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _other(self, root):
        p, _ = QFileDialog.getOpenFileName(self, "Backup-Datei wählen", str(root), "FM-1-Backup (*.json)")
        if p:
            self.path = Path(p)
            super().accept()

    def accept(self):
        it = self.tree.currentItem()
        if it is not None:
            self.path = Path(it.data(0, Qt.ItemDataRole.UserRole))
        super().accept()


class ExportDialog(QDialog):
    """„Als WAV exportieren“: Länge, Einzelspuren, Format, Ordner."""

    def __init__(self, parent, cfg: dict, name: str, loop_seconds: float | None):
        super().__init__(parent)
        self.setWindowTitle("Als WAV exportieren")
        self.cfg = cfg
        self.name = name
        lay = QVBoxLayout(self)
        lay.addWidget(label(f"Gerendert wird „{html.escape(name)}“ aus dem aktuellen Stand des Zwillings – schneller "
                            "als Echtzeit, mit genau dem Klang der Firmware. 44,1 kHz Stereo wie das FM-1.", "muted"))
        box = QGroupBox("Länge")
        bl = QVBoxLayout(box)
        self.r_song = QRadioButton("Ganzer Song bis zum Ende (Song-Kette ab Zeile 1, plus 2 s Ausklang)")
        self.r_secs = QRadioButton("Feste Länge (Pattern von vorn, PLAY):")
        self.r_loops = QRadioButton("Durchläufe des Patterns:")
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
            self.r_loops.setToolTip("Die Pattern-Länge ließ sich nicht lesen.")
        else:
            self.r_loops.setToolTip(f"Ein Durchlauf ≈ {loop_seconds:.1f} s")
        mode = cfg.get("export_mode", "song")
        {"secs": self.r_secs, "loops": self.r_loops if loop_seconds else self.r_song}.get(mode, self.r_song).setChecked(True)
        lay.addWidget(box)
        form = QFormLayout()
        self.fmt = QComboBox()
        self.fmt.addItem("WAV 24 bit (Standard)", "s24")
        self.fmt.addItem("WAV 32 bit Fließkomma", "f32")
        self.fmt.setCurrentIndex(max(0, self.fmt.findData(cfg.get("export_fmt", "s24"))))
        form.addRow("Format:", self.fmt)
        self.stems = QCheckBox("Einzelspuren (Stems) zusätzlich")
        self.stems.setChecked(bool(cfg.get("export_stems", False)))
        self.stems.setToolTip(STEMS_TIP)
        form.addRow("", self.stems)
        from ..studio_service import music_dir
        self.folder = QLineEdit(cfg.get("export_dir") or str(music_dir()))
        b = QPushButton(icon("folder-open"), "…")
        b.setToolTip("Ordner wählen")
        b.clicked.connect(self._pick)
        form.addRow("Ordner:", _row(self.folder, b))
        self.fname = QLineEdit(f"{_safe_name(name)}_{dt.datetime.now():%Y-%m-%d_%H-%M}.wav")
        form.addRow("Datei:", self.fname)
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("Exportieren")
        bb.button(QDialogButtonBox.StandardButton.Cancel).setText("Abbrechen")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _pick(self):
        d = QFileDialog.getExistingDirectory(self, "Export-Ordner", self.folder.text())
        if d:
            self.folder.setText(d)

    def params(self) -> dict:
        mode = "song" if self.r_song.isChecked() else "secs" if self.r_secs.isChecked() else "loops"
        self.cfg.update(export_mode=mode, export_seconds=self.secs.value(), export_loops=self.loops.value(),
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
    """Bestätigung fürs Zurückschreiben – nennt das Gerät, Sicherheits-Backup, Optionen."""

    def __init__(self, parent, device: str, dev_version: str, twin_version: str, mismatch: str, rejected: list[int]):
        super().__init__(parent)
        self.setWindowTitle("Aufs FM-1 zurückschreiben")
        lay = QVBoxLayout(self)
        text = (f"Den Stand des Studio-Zwillings (Melodee {twin_version}) auf <b>„{html.escape(device)}“</b> "
                f"({html.escape(fw_label(dev_version))}) schreiben?<br><br>"
                "Vorher macht die App automatisch ein <b>Sicherheits-Backup</b> dieses FM-1 (Backups › Liste). "
                "Danach ersetzt der Zwilling am Gerät: alle 32 Projektplätze, die laufende Musik und die "
                "User-Presets. Die Wiedergabe am FM-1 stoppt dabei.")
        if rejected:
            text += (f"<br><br>{len(rejected)} Teil(e) hatte der Zwilling beim Laden nicht übernommen – sie bleiben am "
                     "FM-1 unverändert.")
        lab = label(text)
        lab.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(lab)
        self.settings = QCheckBox("Auch die Geräte-Einstellungen schreiben (GLO › SYSTEM, MIDI-Kanäle, Panel …)")
        self.settings.setToolTip("Standard: aus – die Einstellungen am FM-1 bleiben, wie sie sind.")
        lay.addWidget(self.settings)
        self.force = QCheckBox("Experte: trotz anderer Firmware-Version schreiben")
        self.force.setVisible(bool(mismatch))
        if mismatch:
            warn = label("⚠ " + mismatch + " Die Daten des Zwillings könnten zu dieser Version nicht passen; ohne "
                         "Haken schreibt die App nichts.")
            warn.setProperty("role", "warning")
            lay.addWidget(warn)
            lay.addWidget(self.force)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok = bb.button(QDialogButtonBox.StandardButton.Ok)
        self.ok.setText("Sicherheits-Backup machen und zurückschreiben")
        self.ok.setProperty("variant", "danger")
        bb.button(QDialogButtonBox.StandardButton.Cancel).setText("Abbrechen")
        bb.button(QDialogButtonBox.StandardButton.Cancel).setDefault(True)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        if mismatch:
            self.ok.setEnabled(False)
            self.force.toggled.connect(self.ok.setEnabled)


def _row(*widgets) -> QHBoxLayout:
    h = QHBoxLayout()
    h.setContentsMargins(0, 0, 0, 0)
    for w in widgets:
        h.addWidget(w)
    h.addStretch(0)
    return h


# ============================================================================ Seite
class StudioPage(QWidget):
    WIDE = 980                                   # darunter: Bedienfläche oben, Werkzeuge darunter

    def __init__(self, ctx, window):
        super().__init__()
        self.ctx, self.window = ctx, window
        self.svc = getattr(window, "studio", None)
        self.view = None
        self._blank = None
        self._ran_restore = False
        self.last_export: Path | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(heading("Studio", "Der digitale Zwilling deines FM-1: dieselbe Firmware, derselbe Klang – hier "
                                        "verfeinern, als WAV ausgeben und zurück aufs FM-1 schreiben."))
        # ---- Quelle
        self.b_fetch = button("Vom FM-1 holen", ("go-down", "document-import"), "primary", self.fetch_from_device)
        self.b_backup = button("Aus Backup öffnen …", ("document-open",), "", self.open_backup,
                               "Ein gespeichertes Backup in den Zwilling laden (am FM-1 ändert sich nichts)")
        self.b_last = button("Letzten Studio-Stand öffnen", ("document-open-recent", "view-history"), "",
                             self.open_last, "Den automatisch gespeicherten Stand des Studios wieder laden")
        self.b_virtual = button("Als Gerät benutzen", ("computer", "audio-card"), "", self.use_as_device,
                                "Den Zwilling oben als (virtuelles) Gerät wählen: Projekte, Komponieren, Presets, "
                                "Backups und der Editor arbeiten dann mit ihm statt mit einem echten FM-1.")
        lay.addWidget(flow_row(self.b_fetch, self.b_backup, self.b_last, self.b_virtual))
        self.src = label("", "muted")
        self.src.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.src)
        self.banner = label("")
        self.banner.setTextFormat(Qt.TextFormat.RichText)
        self.banner.setProperty("role", "badge")
        lay.addWidget(self.banner)

        # ---- Körper: Bedienfläche + Werkzeuge
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
            pl.addWidget(label("Für das Studio fehlt Qt WebEngine: sudo apt install python3-pyqt6.qtwebengine"))
        self.keys_hint = label("Spielen: Maus, Rechner-Tastatur (Z S X D C … und Q 2 W 3 E …), MIDI-Keyboard. "
                               "Leertaste: Start/Stopp. Regler: ziehen oder Mausrad.", "muted")
        pl.addWidget(self.keys_hint)
        self.body.addWidget(self.panel_box, 3)

        side = QWidget()
        side.setMinimumWidth(300)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 0, 0, 0)
        # Wiedergabe + Projekte
        g = QGroupBox("Wiedergabe und Projekte")
        gl = QVBoxLayout(g)
        self.b_play = button("Abspielen", ("media-playback-start",), "primary", lambda: self.play("play"),
                             "PLAY am Zwilling: das laufende Pattern von vorn (Leertaste)")
        self.b_song = button("Song", ("media-seek-forward", "media-playback-start"), "", lambda: self.play("song"),
                             "Die Song-Kette ab Zeile 1 abspielen")
        self.b_stop = button("Stopp", ("media-playback-stop",), "", self.stop, "Anhalten (Leertaste)")
        gl.addLayout(_row(self.b_play, self.b_song, self.b_stop))
        self.t_state = label("", "muted")
        gl.addWidget(self.t_state)
        self.projects = QListWidget()
        self.projects.setMinimumHeight(140)
        self.projects.setToolTip("Die 32 Projektplätze des Zwillings. Doppelklick lädt ein Projekt (wie die "
                                 "PROJECT-Seite am FM-1).")
        self.projects.itemDoubleClicked.connect(lambda *_: self.load_project())
        gl.addWidget(self.projects, 1)
        self.b_load = button("Projekt laden", ("document-open",), "", self.load_project,
                             "Gewähltes Projekt in die laufende Musik laden (vorher stoppen)")
        self.b_refresh = button("", ("view-refresh",), "flat", self.refresh_projects, "Liste neu lesen")
        gl.addLayout(_row(self.b_load, self.b_refresh))
        sl.addWidget(g, 1)
        # Ton und MIDI
        g = QGroupBox("Ton und MIDI")
        gl = QVBoxLayout(g)
        self.cb_audio = QCheckBox("Ton über den Rechner")
        self.cb_audio.toggled.connect(self._audio_toggled)
        gl.addWidget(self.cb_audio)
        self.out_label = label("", "muted")
        gl.addWidget(self.out_label)
        self.midi = QComboBox()
        self.midi.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.midi.setMinimumContentsLength(10)
        self.midi.setToolTip("MIDI-Keyboard, das im Zwilling spielt")
        self.midi.activated.connect(self._midi_chosen)
        b_scan = button("", ("view-refresh",), "flat", self.refresh_midi, "MIDI-Eingänge neu suchen")
        row = QHBoxLayout()
        row.addWidget(QLabel("MIDI-Eingang:"))
        row.addWidget(self.midi, 1)
        row.addWidget(b_scan)
        gl.addLayout(row)
        self.cb_fm1 = QCheckBox("Tasten des echten FM-1 spielen mit")
        self.cb_fm1.setToolTip("Was du am angeschlossenen FM-1 spielst, klingt auch im Zwilling (nur Noten/Regler).")
        self.cb_fm1.toggled.connect(lambda on: self.svc and self.svc.set_fm1_keys(on))
        gl.addWidget(self.cb_fm1)
        sl.addWidget(g)
        # Export
        g = QGroupBox("Ausgeben")
        gl = QVBoxLayout(g)
        self.b_export = button("Als WAV exportieren …", ("document-save", "media-record"), "primary",
                               self.export_wav, "Song oder Pattern als WAV (24 bit) – optional Einzelspuren. " + STEMS_TIP)
        self.export_state = label("", "muted")
        gl.addWidget(self.b_export)
        gl.addWidget(self.export_state)
        sl.addWidget(g)
        # Stand
        g = QGroupBox("Studio-Stand")
        gl = QVBoxLayout(g)
        self.saved = label("", "muted")
        gl.addWidget(self.saved)
        self.b_snap = button("Zwischenstand merken", ("bookmark-new", "document-save"), "", self.snap,
                             "Den jetzigen Stand in die Rückgängig-Liste aufnehmen")
        self.b_undo = button("Rückgängig", ("edit-undo",), "", self.undo, "Zum vorigen Zwischenstand (Strg+Z)")
        self.b_redo = button("Wiederherstellen", ("edit-redo",), "", self.redo, "Strg+Umschalt+Z")
        gl.addWidget(self.b_snap)
        gl.addLayout(_row(self.b_undo, self.b_redo))
        sl.addWidget(g)
        # Zurückschreiben
        self.b_write = button("Aufs FM-1 zurückschreiben …", ("go-up", "document-export"), "danger", self.write_back)
        sl.addWidget(self.b_write)
        self.side = side
        from PyQt6.QtWidgets import QFrame, QScrollArea
        self.side_scroll = QScrollArea()          # breit: Werkzeuge rollen neben der Bedienfläche
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
            self.cb_audio.setChecked(bool(self.svc.cfg.get("audio", True)))
            self.cb_fm1.setChecked(bool(self.svc.cfg.get("fm1_keys", True)))
        ctx.changed.connect(self.refresh)
        self.refresh_midi()
        self.refresh()

    # ------------------------------------------------------------------ Aufbau/Größe
    def resizeEvent(self, e):
        super().resizeEvent(e)
        wide = self.width() >= self.WIDE
        d = QBoxLayout.Direction.LeftToRight if wide else QBoxLayout.Direction.TopToBottom
        if self.body.direction() != d:
            self.body.setDirection(d)
        # schmal: Werkzeuge untereinander in voller Höhe (die ganze Seite rollt), breit: eigener Rollbereich
        self.side_scroll.setMinimumHeight(0 if wide else self.side.sizeHint().height())
        self.side_scroll.setMinimumWidth(self.side.minimumSizeHint().width() + (14 if wide else 0))
        if self.view is not None:                 # ganze Bedienfläche sichtbar: Höhe aus der Breite
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
                                "padding:2em'><h3>Der Zwilling startet …</h3><p>Oder oben eine Quelle wählen.</p>"
                                "</body></html>")

    # ------------------------------------------------------------------ Sichtbarkeit
    def activate(self):
        if self.svc is None:
            return
        self.svc.set_visible(True)
        if not self.svc.running and not self._ran_restore:
            self._ran_restore = True
            self._auto_restore()
        self._attach_page()
        self.timer.start()
        self.refresh()

    def deactivate(self):
        self.timer.stop()
        if self.svc is not None:
            self.svc.show_panel(False)
            self.svc.autosave()
            self.svc.set_visible(False)

    def shutdown(self):
        self.timer.stop()

    def _auto_restore(self):
        """Studio-Stand des aktiven Geräts (sonst des zuletzt benutzten) laden – sonst ein leerer Zwilling."""
        try:
            self.svc.auto_start()
        except Exception as e:  # noqa: BLE001
            self.src.setText(f"Der Zwilling ließ sich nicht starten: {html.escape(str(e))}")

    # ------------------------------------------------------------------ Anzeige
    def _device_ok(self) -> tuple[bool, str]:
        """darf vom aktiven Gerät gelesen/geschrieben werden? -> (ja, Grund wenn nein)"""
        c = self.ctx
        if c.is_virtual:
            return False, "Aktiv ist gerade der Zwilling selbst – oben in der Geräteauswahl ein echtes FM-1 wählen."
        if not c.connected:
            if c.port is not None and c.protocol == "generic":
                return False, "Diese Firmware hat kein Editor-Protokoll – das Studio braucht Melodee."
            return False, "Kein FM-1 verbunden."
        if not self.svc.firmware_for(c.firmware_key):
            return False, (f"Auf dem FM-1 läuft {html.escape(fw_label(c.info.version) if c.info else 'eine andere Firmware')}"
                           " – einen Studio-Zwilling gibt es bisher nur für Melodee.")
        return True, ""

    def refresh(self):
        svc = self.svc
        if svc is None:
            self.src.setText("Das Studio ist nicht verfügbar (Studio-Dienst fehlt).")
            for b in (self.b_fetch, self.b_backup, self.b_last, self.b_write, self.b_export):
                b.setEnabled(False)
            return
        c = self.ctx
        name = c.profile.name if (c.profile and not c.is_virtual) else ""
        ok, why = self._device_ok()
        self.b_fetch.setText(f"Vom FM-1 „{name}“ holen" if name else "Vom FM-1 holen")
        self.b_fetch.setEnabled(ok)
        self.b_fetch.setToolTip("Komplettes Backup vom aktiven FM-1 lesen (landet auch in Backups › Liste) und in "
                                "den Zwilling laden. Die Wiedergabe am FM-1 stoppt kurz; geschrieben wird dort nichts."
                                if ok else why)
        self.b_write.setEnabled(ok and svc.running)
        self.b_write.setToolTip("Den Stand des Zwillings auf das aktive FM-1 schreiben – mit Bestätigung und "
                                "Sicherheits-Backup vorher." if ok else why)
        self.b_last.setEnabled(bool(svc.stored_states()))
        run = svc.running
        for b in (self.b_play, self.b_song, self.b_stop, self.b_load, self.b_export, self.b_snap, self.b_virtual,
                  self.projects):
            b.setEnabled(run)
        self.b_virtual.setText("Ist aktives Gerät" if c.is_virtual else "Als Gerät benutzen")
        self.b_virtual.setEnabled(run and not c.is_virtual)
        if run:
            self.src.setText(f"<b>Zwilling:</b> Melodee {html.escape(svc.version)} · {html.escape(svc.source_text())}")
        else:
            self.src.setText("Der Zwilling läuft noch nicht. Wähle eine Quelle: vom FM-1, aus einem Backup oder den "
                             "letzten Studio-Stand.")
        self._banner()
        self._update_history()
        self.refresh_projects()
        self._out_text()

    def _banner(self):
        svc, c = self.svc, self.ctx
        text, level = "", ""
        if c.connected and not c.is_virtual and not svc.firmware_for(c.firmware_key):
            text = (f"Für {html.escape(fw_label(c.info.version) if c.info else 'diese Firmware')} gibt es noch keinen "
                    "Studio-Zwilling. Das Studio kann bisher Melodee; SLOOP, Skiff und die Original-Firmware (über "
                    "FM1 Open) folgen später – siehe Hilfe › Studio. Backups dieses Geräts lassen sich hier nicht "
                    "öffnen; ein Melodee-Backup schon.")
            level = "warning"
        elif svc.running:
            dev = c.info.version if (c.connected and not c.is_virtual and svc.firmware_for(c.firmware_key)
                                     and c.info) else ""
            vc = S.check_versions(svc.version, device=dev, backup=svc.source.get("backup_version", ""))
            msgs = [vc.message] if vc.message else []
            if svc.st is not None and svc.st.warning:
                msgs.append(svc.st.warning)
            if msgs:
                text, level = "⚠ " + html.escape(" ".join(msgs)), "warning"
            elif dev:
                text, level = (f"✓ Zwilling und FM-1 haben dieselbe Firmware (Melodee {html.escape(svc.version)}) – "
                               "identischer Klang."), "success"
            else:
                text, level = f"Zwilling: Melodee {html.escape(svc.version)}", ""
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
            self.b_undo.setToolTip(f"Zurück zu: {svc.history[svc.pos - 1]['label']} (Strg+Z)")
        if svc.can_redo():
            self.b_redo.setToolTip(f"Vor zu: {svc.history[svc.pos + 1]['label']} (Strg+Umschalt+Z)")
        if svc.running:
            when = time.strftime("%H:%M:%S", time.localtime(svc.saved_at)) if svc.saved_at else "noch nicht"
            self.saved.setText(f"Automatisch gespeichert: {when} · beim Profil „{html.escape(svc.profile_folder())}“ · "
                               f"{len(svc.history)} Zwischenstände" + (f"<br>{html.escape(svc.last_error)}"
                                                                      if svc.last_error else ""))
            self.saved.setToolTip(str(svc.state_file()) + "\nGespeichert wird der Flash – alles, was das FM-1 nach dem "
                                  "Ausschalten noch hat. Ungespeichertes im laufenden Projekt gehört nicht dazu (wie "
                                  "am Gerät: SAVE drücken).")
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
        for k in range(32):
            mark = "  ◀ geladen" if k == cur else ""
            it = QListWidgetItem(f"Projekt {k + 1}" + ("" if k in used else " – leer") + mark)
            it.setData(Qt.ItemDataRole.UserRole, k)
            if k not in used:
                it.setForeground(self.palette().color(QPalette.ColorRole.PlaceholderText))
            self.projects.addItem(it)
        if 0 <= cur < 32:
            self.projects.setCurrentRow(cur)

    def _out_text(self):
        from ..audio import chosen_output, current_output
        want = chosen_output()
        if not want:
            t = "Ausgabe: Standard-Ausgang des Systems (PipeWire)."
        else:
            o = None
            try:
                o = current_output()
            except Exception:  # noqa: BLE001
                pass
            t = (f"Ausgabe: „{o.description if o else want}“ (wie unter „Ausgabe“ beim Vorhören gewählt; das Studio "
                 "verschiebt seinen Ton dorthin, sobald er läuft).")
        if self.svc is not None and self.svc.audio_note:
            t += " " + self.svc.audio_note
        self.out_label.setText(t)

    def _tick(self):
        svc = self.svc
        if svc is None or not svc.running:
            return
        t = svc.transport()
        if not t:
            return
        state = "spielt (Song)" if t.get("chain") else "spielt" if t.get("playing") else "gestoppt"
        if t.get("rec"):
            state += " · REC"
        self.t_state.setText(f"Zwilling {state}")
        self._playing = bool(t.get("busy") or t.get("playing"))

    # ------------------------------------------------------------------ Wiedergabe
    def play(self, how: str = "play"):
        if self.svc is None or not self.svc.running:
            return
        try:
            self.svc.st.play(how)
        except S.StudioError as e:
            self.t_state.setText(f"Abspielen nicht möglich: {e}")
        self._tick()

    def stop(self):
        if self.svc is None or not self.svc.running:
            return
        try:
            self.svc.st.stop()
        except S.StudioError as e:
            self.t_state.setText(f"Stopp nicht möglich: {e}")
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
        """Leertaste/Strg+Z – kommen je nach Fokus von Qt oder aus der Bedienfläche; nie doppelt ausführen."""
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
        try:
            rc = self.svc.st.load_project(slot)
        except S.StudioError as e:
            self.t_state.setText(f"Laden nicht möglich: {e}")
            return
        self.t_state.setText({0: f"Projekt {slot + 1} geladen.", 1: f"Projekt {slot + 1} ist leer.",
                              2: "Erst stoppen, dann laden."}.get(rc, f"Fehler {rc}"))
        self.refresh_projects()

    # ------------------------------------------------------------------ Ton/MIDI
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
        self.midi.addItem("kein MIDI-Keyboard", "")
        for n in names:
            self.midi.addItem(n, n)
        if cur and cur[0] in names:
            self.midi.setCurrentIndex(self.midi.findData(cur[0]))
            self.svc.set_inputs(cur[:1])

    def _midi_chosen(self, *_):
        n = self.midi.currentData() or ""
        errs = self.svc.set_inputs([n] if n else [])
        if errs:
            show_error(self, "MIDI-Eingang", "\n".join(errs))

    # ------------------------------------------------------------------ Quellen
    def fetch_from_device(self):
        svc, c = self.svc, self.ctx
        ok, why = self._device_ok()
        if not ok:
            show_error(self, "Vom FM-1 holen", why)
            return None
        try:
            folder = c.device_folder(c.settings.backups_dir)
        except OSError as e:
            show_error(self, "Vom FM-1 holen", str(e))
            return None
        prof, fw, version = c.profile, c.firmware_key, c.info.version
        try:
            st = svc.ensure(S.norm_version(version))
        except Exception as e:  # noqa: BLE001
            show_error(self, "Studio", f"Der Zwilling startet nicht: {e}")
            return None
        svc.snapshot("vor dem Holen vom FM-1", only_if_changed=True)
        svc.busy += 1
        box = {}

        def work(w, link):
            w.log.emit(f"Lese das komplette Backup von „{prof.name}“ …")
            path = AB.capture_to_file(link, folder, prof, fw, version, "fürs Studio geholt", "manuell",
                                      progress=lambda a, b: w.progress.emit(a, b * 2))
            w.log.emit(f"Gesichert in Backups › Liste: {path.name}")
            arch = backup.load_file(path)
            w.log.emit("Spiele es in den Zwilling ein …")
            st.open_from_backup(arch, progress=lambda a, b: w.progress.emit(b + a, b * 2), tolerant=True)
            box["arch"] = arch
            return path

        def done(path):
            box["path"] = path

        try:
            d = c.job(self, "Vom FM-1 ins Studio", work, on_done=done,
                      done=lambda p: f"„{prof.name}“ ist jetzt im Studio. Das Backup liegt auch in Backups › Liste: "
                                     f"{Path(p).name}. Am FM-1 wurde nichts verändert.")
        finally:
            svc.busy -= 1
        if "arch" in box:
            svc.loaded_backup(box["arch"], "device", device=prof.name, profile_folder=prof.folder_name,
                              backup=str(box.get("path", "")), device_version=version)
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
            show_error(self, "Backup öffnen", f"Das Backup lässt sich nicht lesen: {e}")
            return None
        if arch.sloop:
            show_error(self, "Backup öffnen", "Das ist ein SLOOP-Backup. Einen Studio-Zwilling gibt es bisher nur für "
                                              "Melodee (SLOOP folgt später).")
            return None
        try:
            st = svc.ensure(S.backup_version(arch))
        except Exception as e:  # noqa: BLE001
            show_error(self, "Studio", f"Der Zwilling startet nicht: {e}")
            return None
        svc.snapshot("vor dem Öffnen eines Backups", only_if_changed=True)
        comp = arch.companion or {}
        device = comp.get("device") or path.parent.name
        folder = comp.get("device") and path.parent.name or "Studio"
        svc.busy += 1
        try:
            from ..common import JobDialog
            d = JobDialog(self, "Backup ins Studio", lambda w: st.open_from_backup(
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
            show_error(self, "Studio-Stand", "Es gibt noch keinen gespeicherten Studio-Stand.")
            return
        folder = svc.profile_folder()
        names = [s[0] for s in states]
        if len(states) > 1:
            from PyQt6.QtWidgets import QInputDialog
            cur = names.index(folder) if folder in names else 0
            item, ok = QInputDialog.getItem(self, "Studio-Stand öffnen", "Stand welches Geräts?", names, cur, False)
            if not ok:
                return
            folder = item
        elif folder not in names:
            folder = names[0]
        try:
            meta = svc.open_state(folder)
        except Exception as e:  # noqa: BLE001
            show_error(self, "Studio-Stand", str(e))
            return
        if meta.get("warning"):
            self.window.notify(meta["warning"], "warning")

    def use_as_device(self):
        if self.svc is not None and self.svc.activate_virtual():
            self.window.notify("Aktives Gerät: der Studio-Zwilling (virtuell). Projekte, Komponieren, Presets und "
                               "Backups arbeiten jetzt mit ihm – oben wieder ein echtes FM-1 wählen, um zurückzukehren.",
                               "info")

    # ------------------------------------------------------------------ Stand
    def snap(self):
        if self.svc is not None and self.svc.snapshot("Zwischenstand " + time.strftime("%H:%M:%S")):
            self.svc.autosave(force=True)
            self.window.statusBar().showMessage("Zwischenstand gemerkt.", 4000)

    def undo(self):
        if self.svc is not None:
            lab = self.svc.undo()
            if lab:
                self.window.statusBar().showMessage(f"Rückgängig: zurück zu „{lab}“.", 5000)

    def redo(self):
        if self.svc is not None:
            lab = self.svc.redo()
            if lab:
                self.window.statusBar().showMessage(f"Wiederhergestellt: „{lab}“.", 5000)

    # ------------------------------------------------------------------ Export
    def _running_music(self) -> tuple[str, float | None]:
        """Name und Länge eines Pattern-Durchlaufs (s) der laufenden Musik des Zwillings."""
        from ... import projectfmt as PF
        svc = self.svc
        name = ""
        try:
            info = svc.st.backend.call("info")
            cur = info.get("project", 255)
            if 0 <= cur < 32:
                name = f"Projekt {cur + 1}"
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
        except Exception:  # noqa: BLE001 – nur Zugabe für Namen und Durchläufe
            return name or "Studio", None

    def export_wav(self, params: dict | None = None):
        svc = self.svc
        if svc is None or not svc.running:
            return None
        if params is None:
            name, loop = self._running_music()
            dlg = ExportDialog(self, svc.cfg, name, loop)
            if not dlg.exec():
                return None
            params = dlg.params()
        path = Path(params.pop("path"))
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            show_error(self, "Export", f"Der Ordner lässt sich nicht anlegen: {e.strerror or e}")
            return None
        stems = bool(params.get("stems"))
        prog = QProgressDialog("Rendere …", "", 0, 5 if stems else 1, self)
        prog.setWindowTitle("Als WAV exportieren")
        prog.setCancelButton(None)
        prog.setMinimumDuration(0)
        prog.setWindowModality(Qt.WindowModality.WindowModal)
        prog.setValue(0)
        res = {}
        st = svc.st

        def work(w):
            return st.render_wav(path, progress=lambda a, b: w.progress.emit(a, b), **params)

        def step(a, b):
            prog.setMaximum(b)
            prog.setValue(a)
            prog.setLabelText("Mix …" if a == 0 else f"Einzelspur {a} von 4 …" if a < b else "Fertig")

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
        del w
        if "err" in res:
            show_error(self, "Export", f"Rendern fehlgeschlagen: {res['err']}")
            return None
        r = res["r"]
        self.last_export = r.path
        extra = f" + {len(r.stems)} Einzelspuren" if r.stems else ""
        self.export_state.setText(f"Gespeichert: {html.escape(r.path.name)}{extra} ({r.seconds:.1f} s)"
                                  + ("" if r.peak > 0.0005 else " – Achtung: still (spielt etwas?)"))
        self.export_state.setToolTip(str(r.path))
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        self.window.notify(f"WAV gespeichert: {r.path.name}{extra}", "success",
                           ("Ordner öffnen", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(r.path.parent)))))
        return r

    # ------------------------------------------------------------------ Zurückschreiben
    def write_back(self, confirmed: dict | None = None):
        svc, c = self.svc, self.ctx
        ok, why = self._device_ok()
        if not ok or svc is None or not svc.running:
            show_error(self, "Aufs FM-1 zurückschreiben", why or "Der Zwilling läuft nicht.")
            return None
        dev_v = c.info.version
        vc = S.check_versions(svc.version, device=dev_v)
        from ..risk_dialog import ensure_risk_ack
        if not ensure_risk_ack(self):
            return None
        if confirmed is None:
            dlg = WriteBackDialog(self, c.profile.name, dev_v, svc.version, "" if vc.ok else vc.message,
                                  list(svc.st.rejected))
            if not dlg.exec():
                return None
            confirmed = {"settings": dlg.settings.isChecked(), "force": dlg.force.isChecked()}
        if not vc.ok and not confirmed.get("force"):
            show_error(self, "Nicht geschrieben", vc.message + " Es wurde nichts geschrieben.")
            return None
        try:
            folder = c.device_folder(c.settings.backups_dir)
        except OSError as e:
            show_error(self, "Aufs FM-1 zurückschreiben", str(e))
            return None
        prof, fw = c.profile, c.firmware_key
        st = svc.st
        svc.autosave(force=True)
        svc.busy += 1
        box = {}

        def work(w, link):
            w.log.emit(f"Sicherheits-Backup von „{prof.name}“ …")
            safety = AB.capture_to_file(link, folder, prof, fw, dev_v, "Sicherheit vor dem Zurückschreiben aus dem "
                                        "Studio", "sicherheit", progress=lambda a, b: w.progress.emit(a, b * 2))
            box["safety"] = safety
            w.log.emit(f"Gesichert: {safety.name}")
            w.log.emit("Schreibe den Stand des Zwillings …")
            st.write_to_device(link, progress=lambda a, b: w.progress.emit(b + a, b * 2),
                               force=bool(confirmed.get("force")), settings=bool(confirmed.get("settings")),
                               exclude=st.rejected)
            return safety

        try:
            c.job(self, "Studio → FM-1", work,
                  done=lambda p: f"Der Stand des Studios ist jetzt auf „{prof.name}“. Sicherheits-Backup vorher: "
                                 f"{Path(p).name} (Backups › Liste).")
        finally:
            svc.busy -= 1
        bp = getattr(self.window, "backups", None)
        if bp is not None and hasattr(bp, "refresh"):
            bp.refresh()
        return box.get("safety")


def _make(ctx, window):
    return StudioPage(ctx, window)


PAGE = PageSpec(key="studio", title=_("Studio"), group="musik", factory=_make,
                icons=("audio-x-generic", "applications-multimedia", "media-playback-start"), order=5,
                description=_("the digital twin of your FM-1: fetch, play, export as WAV, write back"),
                keywords=("zwilling", "twin", "wav", "export", "stems", "einzelspuren", "rendern", "logic",
                          "audacity", "studio", "zurückschreiben", "digital twin", "render", "write back",
                          "single tracks"))
