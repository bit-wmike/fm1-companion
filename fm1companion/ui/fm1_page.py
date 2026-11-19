# SPDX-License-Identifier: GPL-3.0-only
"""FM-1: everything the device reveals about itself – connection, live load, memory, user presets.

While the page is open, the app reserves the device (the editor pauses and reconnects by itself
when you switch back). That way the live queries and the editor don't interfere with each other.
All queries are read-only. The live values come from Melodee's / FM1 Open's AUDIO_STATS (72), from SLOOP 3's LIVE
op 0 or from SLOOP 2.7+'s LIVE 77 op 0 (health.py) – each only to the firmware that has it; never while another
job (backup, restore, installation) holds the device.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (QCheckBox, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                             QProgressBar, QPushButton, QScrollArea, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                             QWidget)

from .. import health
from ..devices import alsa_card_for
from ..i18n import N_, _, ngettext, pgettext
from .common import fmt_size, heading, icon, run_job

LIVE_MS = 2000
# SLOOP 3: every request it gets sets its "editor active" time (fw/main.c ed_request: ed_t_last), which keeps a transfer
# that was left halfway alive (it is dropped after 10 s without a request, main.c ~4489) and pauses its visualiser after a
# READ (vis_busy, 2 s). So the app asks less often (5 s: past the visualiser's 2 s), and not at all while SLOOP 3's own
# editor is loaded (page "Editor"): that one asks the FM-1 itself
SLOOP3_LIVE_MS = 5000
# row keys of self.f are identifiers (also used by the tests) -> translated row titles
DEVICE_ROWS = ("Profil", "Firmware", "Engines", "Spuren", "Seriennummer")                    # i18n: ignore
CONNECTION_ROWS = ("USB-Gerät", "USB-ID", "Tempo", "Anschluss", "MIDI-Port", "Soundkarte", "USB-Audio")  # i18n: ignore
LIVE_ROWS = ("Stimmen", "Überlast", "Pro Spur", "Zweiter Kern", "Rechenzeit", "USB-Audio-Fehler", "Speichertest",  # i18n: ignore
             "Transport")  # i18n: ignore
MEMORY_ROWS = ("Projekte", "User-Presets", "Laufende Musik", "Sample-Slots", "Komplett-Backup")  # i18n: ignore
ROW_TITLES = {
    "Profil": N_("Profile:"), "Firmware": N_("Firmware:"), "Engines": N_("Engines:"), "Spuren": N_("Tracks:"),  # i18n: ignore
    "Seriennummer": N_("Serial number:"), "USB-Gerät": N_("USB device:"), "USB-ID": N_("USB ID:"),  # i18n: ignore
    "Tempo": N_("Speed:"), "Anschluss": N_("Port:"), "MIDI-Port": N_("MIDI port:"),  # i18n: ignore
    "Soundkarte": N_("Sound card:"), "USB-Audio": N_("USB audio:"), "Stimmen": N_("Voices:"),  # i18n: ignore
    "Überlast": N_("Overload:"), "Pro Spur": N_("Per track:"), "Zweiter Kern": N_("Second core:"),  # i18n: ignore
    "Rechenzeit": N_("Processing time:"), "USB-Audio-Fehler": N_("USB audio errors:"),  # i18n: ignore
    "Speichertest": N_("Memory test:"), "Projekte": N_("Projects:"), "User-Presets": N_("User presets:"),  # i18n: ignore
    "Laufende Musik": N_("Running music:"), "Sample-Slots": N_("Sample slots:"),  # i18n: ignore
    "Komplett-Backup": N_("Full backup:"), "Transport": N_("Transport:")}  # i18n: ignore


def _bar(fmt: str = "") -> QProgressBar:
    b = QProgressBar()
    b.setRange(0, 1000)
    b.setFormat(fmt)
    b.setTextVisible(True)
    return b


def _set_bar(b: QProgressBar, v, total, text: str):
    b.setValue(int(min(1.0, v / total) * 1000) if total else 0)
    b.setFormat(text)


def _lab(text="–") -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lab


class FM1Page(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.active = False
        self.held_link = None
        self.live_busy = False
        self.presets: list = []
        self.engines: list[str] = []

        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        lay = QVBoxLayout(body)
        lay.addWidget(heading("FM-1", _("What the connected device reports about itself. While this page is open, "
                                        "the editor pauses; when you switch back it reconnects by itself.")))
        self.none = QLabel(_("No FM-1 connected."))
        self.none.setWordWrap(True)
        self.none.setTextFormat(Qt.TextFormat.RichText)
        self.none.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self.none.linkActivated.connect(self._link)
        lay.addWidget(self.none)
        self.content = QWidget()
        grid = QGridLayout(self.content)
        grid.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.content)

        # --- Device
        g = QGroupBox(_("Device"))
        self.dev = QFormLayout(g)
        self.f = {}
        for k in DEVICE_ROWS:
            self.f[k] = _lab()
            self.dev.addRow(_(ROW_TITLES[k]), self.f[k])
        grid.addWidget(g, 0, 0)

        # --- Connection
        c = QGroupBox(_("Connection"))
        self.con = QFormLayout(c)
        for k in CONNECTION_ROWS:
            self.f[k] = _lab()
            self.con.addRow(_(ROW_TITLES[k]), self.f[k])
        grid.addWidget(c, 0, 1)

        # --- Live
        lv = QGroupBox(_("Live"))
        ll = QVBoxLayout(lv)
        top = QHBoxLayout()
        self.cb_live = QCheckBox(_("refresh every {n} s").format(n=LIVE_MS // 1000))
        self.cb_live.setChecked(True)
        self.cb_live.toggled.connect(self._live_toggled)
        top.addWidget(self.cb_live)
        top.addStretch(1)
        self.live_state = QLabel("")
        top.addWidget(self.live_state)
        ll.addLayout(top)
        lf = self.live_form = QFormLayout()
        ll.addLayout(lf)
        self.cpu = _bar()
        lf.addRow(_("Audio CPU:"), self.cpu)
        self.ram = _bar()
        lf.addRow(_("Memory:"), self.ram)
        self.cache = _bar()
        lf.addRow(_("Cache RAM:"), self.cache)
        for k in LIVE_ROWS:
            self.f["L" + k] = _lab()
            lf.addRow(_(ROW_TITLES[k]), self.f["L" + k])
        # SLOOP 3 / SLOOP 2: a level meter per track (from their LIVE state)
        self.meters_box = QWidget()
        self.meters_lay = QHBoxLayout(self.meters_box)
        self.meters_lay.setContentsMargins(0, 0, 0, 0)
        self.meters: list[QProgressBar] = []
        lf.addRow(_("Track levels:"), self.meters_box)
        self.meters_title = lf.labelForField(self.meters_box)
        self._meters_visible(False)
        grid.addWidget(lv, 1, 0, 1, 2)

        # --- Memory
        sp = QGroupBox(_("Storage"))
        sl = QVBoxLayout(sp)
        sf = QFormLayout()
        sl.addLayout(sf)
        self.pool = _bar()
        sf.addRow(_("Storage pool:"), self.pool)
        for k in MEMORY_ROWS:
            self.f["S" + k] = _lab()
            sf.addRow(_(ROW_TITLES[k]), self.f["S" + k])
        h = QHBoxLayout()
        b = QPushButton(icon("view-refresh"), _("Read again"))
        b.clicked.connect(self.refresh)
        h.addWidget(b)
        h.addStretch(1)
        sl.addLayout(h)
        grid.addWidget(sp, 2, 0)

        # --- User presets
        up = QGroupBox(_("User presets"))
        ul = QVBoxLayout(up)
        fl = QHBoxLayout()
        self.filter = QLineEdit()
        self.filter.setPlaceholderText(_("Search name or engine …"))
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self._fill_presets)
        fl.addWidget(self.filter, 1)
        self.cb_used = QCheckBox(_("used only"))
        self.cb_used.setChecked(True)
        self.cb_used.toggled.connect(self._fill_presets)
        fl.addWidget(self.cb_used)
        ul.addLayout(fl)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([_("Slot"), _("Engine"), _("Name"), "★"])
        self.tree.setRootIsDecorated(False)
        self.tree.setSortingEnabled(True)
        self.tree.setMinimumHeight(260)
        ul.addWidget(self.tree, 1)
        self.preset_note = QLabel("")
        self.preset_note.setWordWrap(True)
        ul.addWidget(self.preset_note)
        grid.addWidget(up, 2, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        lay.addStretch(1)

        self.timer = QTimer(self, interval=LIVE_MS)
        self.timer.timeout.connect(self._live_tick)
        ctx.changed.connect(self._dev_changed)
        self._dev_changed()

    # ------------------------------------------------------------ Page
    def activate(self):
        self.active = True
        self._dev_changed()

    def deactivate(self):
        self.active = False
        self.timer.stop()
        self._release()

    def _hold(self):
        link = self.ctx.link
        if self.held_link is link or link is None or link.closed:
            return
        self._release()
        link.hold(settle=False)
        self.held_link = link

    def _release(self):
        if self.held_link is not None:
            if not self.held_link.closed:
                self.held_link.release()
            self.held_link = None

    def _link(self, href: str):
        if href == "#firmware" and callable(getattr(self.ctx, "ask_firmware", None)):
            self.ctx.ask_firmware(force=True)

    def _generic_text(self) -> str:
        """what is known about an FM-1 without the editor protocol (or in update mode)"""
        c = self.ctx
        p, prof = c.port, c.profile
        from .device import firmware_name
        if getattr(c, "firmware_ambiguous", False):
            fw = _("unknown – please choose (the ID is used by several firmwares)")
        else:
            fw = firmware_name(c.firmware_key) or _("unknown")
            if c.firmware_key and not (prof and prof.firmware_choice.get(c.identity)) and not c.info:
                fw += _(" (guessed from the ID)") if c.firmware_guess else ""
        from .common import firmware_changeable
        if firmware_changeable(c):                  # (the question of the first connection, again)
            fw += " · <a href='#firmware'>" + _("change") + "</a>"
        usb = " · ".join(x for x in (" ".join(y for y in (p.manufacturer, p.product) if y), p.usb_id,
                                      pgettext("device", "USB port {path}").format(path=p.usb_path) if p.usb_path else "",
                                      f"{p.speed} Mbit/s" if p.speed else "") if x) or "–"
        mode = getattr(c, "protocol", "")
        rows = [(_("Profile:"), prof.name if prof else "–"),
                (_("State:"), c.status_text() if hasattr(c, "status_text") else _("connected")),
                (_("ID:"), c.identity or _("not read yet")),
                (_("Firmware:"), fw),
                (_("Serial number:"), p.serial or _("none")),
                (_("USB:"), usb),
                (_("MIDI port:"), p.out_name)]
        tail = (_("The FM-1 is in update mode – the page “Recovery” finishes the installation.")
                if mode == "update" else
                _("Live values, storage and user presets are only provided by Melodee or M-VAVE FM1 Open (editor "
                  "protocol). The app can install this firmware and talk to it via MIDI, but cannot read it out."))
        return ("<b>" + _("FM-1 connected – without editor protocol") + "</b><table cellspacing=4>" +
                "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in rows) + f"</table><p>{tail}</p>")

    def _sloop3(self):
        """SLOOP 3 is running (its own INFO answered; no Felucca protocol) -> its sloop3.Info, else None"""
        c = self.ctx
        return getattr(c, "sloop3", None) if getattr(c, "midi_connected", False) and not c.connected else None

    def _shown(self) -> bool:
        return self.ctx.connected or self._sloop3() is not None

    def _busy_reason(self) -> str:
        """another job holds the device (backup, restore, installation, flash copy): then nothing is asked"""
        c = self.ctx
        link = c.link
        if getattr(c, "hold", False):
            return _("installation in progress")
        try:
            from ..flash import journal
            if journal.Journal.busy():
                return _("installation in progress")
        except Exception:  # noqa: BLE001
            pass
        own = 1 if (link is not None and self.held_link is link) else 0
        if link is not None and getattr(link, "_exclusive_depth", 0) > own:
            return _("device in use right now")
        return ""

    def _dev_changed(self):
        ok = self._shown()
        self.none.setVisible(not ok)
        self.content.setVisible(ok)
        if not ok:
            self.none.setText(self._generic_text() if self.ctx.port is not None else _("No FM-1 connected."))
            self.timer.stop()
            self._release()
            self.presets = []
            return
        self._static()
        key = self.ctx.port.key if self.ctx.port else ""
        if key != getattr(self, "_shown_key", ""):        # different device: don't show the previous one's values
            self._shown_key = key
            self._clear_dynamic()
        if self.active:
            self._hold()
            self.refresh()

    def _clear_dynamic(self):
        self.presets = []
        self._fill_presets()
        self._show_live(None, _("not read yet"))
        for k in MEMORY_ROWS:
            self.f["S" + k].setText("–")
        _set_bar(self.pool, 0, 0, _("not read yet"))
        self.preset_note.setText("")
        self.live_state.setText("")

    # ------------------------------------------------------------ fixed
    def _static(self):
        p, info, prof = self.ctx.port, self.ctx.info, self.ctx.profile
        s3 = self._sloop3()
        self.engines = list(info.engines) if info else []
        self.f["Profil"].setText(prof.name if prof else "–")
        self.f["Firmware"].setText(info.version if info else
                                   _("{firmware} (public beta)").format(firmware=s3.label) if s3 else "–")
        self.f["Engines"].setText(", ".join(self.engines) or "–")
        self.f["Spuren"].setText(_("{tracks} tracks × {steps} steps").format(tracks=info.ntrk, steps=info.nstep)  # i18n: ignore
                                 if info else ngettext("{n} track", "{n} tracks", s3.tracks).format(n=s3.tracks)
                                 if s3 else "–")
        self.f["Seriennummer"].setText(p.serial or _("none"))  # i18n: ignore
        self.f["USB-Gerät"].setText(  # i18n: ignore
            " ".join(x for x in (p.manufacturer, p.product) if x) or p.label)
        self.f["USB-ID"].setText(p.usb_id or "–")
        self.f["Tempo"].setText(f"{p.speed} Mbit/s" + (" (Full Speed)" if p.speed == "12" else "") if p.speed else "–")
        self.f["Anschluss"].setText(pgettext("device", "USB port {path}").format(path=p.usb_path) if p.usb_path else "–")
        self.f["MIDI-Port"].setText(p.out_name)
        card = alsa_card_for(p)
        self.f["Soundkarte"].setText(_("{name} (card {n})").format(name=card, n=p.card) if card and p.card is not None
                                     else (card or "–"))

    # ------------------------------------------------------------ Queries
    def refresh(self):
        if not self.ctx.connected:
            if self._sloop3() is not None:
                self._refresh_sloop3()
            return
        self._hold()
        link = self.ctx.link
        self.live_state.setText(_("reading …"))

        def work(w):
            rep = health.fm1_report(link)       # sends AUDIO_STATS (72) only to Melodee / FM1 Open
            try:
                rep["preset_list"] = health.fm1_presets(link)
            except Exception:
                rep["preset_list"] = None
            return rep
        run_job(self, work, on_done=self._report, on_fail=lambda msg, tb: self.live_state.setText(_("Error: {error}").format(error=msg)))

    def _refresh_sloop3(self):
        """SLOOP 3: what its own read-only commands tell (INFO, the sample slots' list); then the live values"""
        self._hold()
        link = self.ctx.link
        self.live_state.setText(_("reading …"))

        def work(w):
            from .. import sloop3 as S3
            with S3.Client(link, timeout=1.5) as c:
                info = c.info()
                try:
                    slots = c.samples()
                except S3.Sloop3Error:
                    slots = None
            return info, slots

        def done(r):
            import time as _t
            info, slots = r
            self.live_state.setText(_("read at {time} (only read, nothing changed)").format(time=_t.strftime('%H:%M:%S')))
            _set_bar(self.pool, 0, 0, _("not known for this firmware"))
            self.f["SProjekte"].setText(_("one project place: a project is saved") if info.projects_used & 1  # i18n: ignore
                                        else _("one project place: nothing saved yet"))
            used = bin(info.sounds_used & 0xFFFFFFFF).count("1")
            self.f["SUser-Presets"].setText(_("{used} of {total} of your sounds used").format(used=used, total=32))  # i18n: ignore
            self.f["SLaufende Musik"].setText("–")  # i18n: ignore
            self.f["SKomplett-Backup"].setText(_("on the page “Backups” (SLOOP 3's own file)"))  # i18n: ignore
            self.f["SSample-Slots"].setText(  # i18n: ignore
                "–" if slots is None else ", ".join(
                    f"USR{i + 1}: " + (f"{s['name'] or '?'} ({s['kib']} KiB)" if s.get("zones") else _("empty"))
                    for i, s in enumerate(slots)))
            self.presets = []
            self._fill_presets()
            self.preset_note.setText(_("SLOOP 3: your sounds are listed and edited in its own editor (page “Editor”, "
                                       "tab LIBRARY)."))
            if self.active and self.cb_live.isChecked():
                self.timer.start()
                self._live_tick()
        run_job(self, work, on_done=done,
                on_fail=lambda msg, tb: self.live_state.setText(_("Error: {error}").format(error=msg)))

    def _report(self, r: dict):
        import time as _t
        self.live_state.setText(_("read at {time} (only read, nothing changed)").format(time=_t.strftime('%H:%M:%S')))
        if r.get("error"):
            self.live_state.setText(_("Error: {error}").format(error=r['error']))
            return
        self._show_live(r.get("audio"))
        if r.get("pool_total"):
            _set_bar(self.pool, r["pool_used"], r["pool_total"],
                     _("{used} of {total} (projects + presets)").format(used=fmt_size(r['pool_used']),
                                                                       total=fmt_size(r['pool_total'])))
        else:
            _set_bar(self.pool, 0, 0, _("not known for this firmware"))
        if r.get("projects_total"):
            self.f["SProjekte"].setText(_("{used} of {total} slots used ({size})").format(  # i18n: ignore
                used=r['projects_used'], total=r['projects_total'], size=fmt_size(r['projects_bytes'])))
            self.f["SLaufende Musik"].setText(fmt_size(r.get("live_bytes")))  # i18n: ignore
        else:
            self.f["SProjekte"].setText("–")
            self.f["SLaufende Musik"].setText("–")  # i18n: ignore
        self.f["SKomplett-Backup"].setText(  # i18n: ignore
            ngettext("{size} in {n} object", "{size} in {n} objects", r['objects']).format(
                size=fmt_size(r['backup_bytes']), n=r['objects']) if r.get("backup_bytes") else "–")
        slots = r.get("sample_slots")
        self.f["SSample-Slots"].setText(
            _("none (removed in Melodee 1.5 and later)") if not slots else
            ", ".join(f"USR{s.index + 1}: {_('empty') if s.empty else s.name}" for s in slots))
        self.presets = r.get("preset_list") or []
        if r.get("preset_list") is None:
            self.f["SUser-Presets"].setText("–")
            self.preset_note.setText(_("This firmware has no shared preset library (available from Melodee 1.5)."))
        else:
            used = sum(s.used for s in self.presets)
            fav = sum(s.favorite for s in self.presets)
            self.f["SUser-Presets"].setText(  # i18n: ignore
                _("{used} of {total} used").format(used=used, total=len(self.presets)) +
                (", " + ngettext("{n} favourite", "{n} favourites", fav).format(n=fav) if fav else ""))
            self.preset_note.setText(_("Edit, load and save: in the editor under “Library”."))
        self._fill_presets()
        if self.active and self.cb_live.isChecked() and (r.get("audio_supported", True) or
                                                         health.sloop2_live_supported(self.ctx.info)):
            self.timer.start()

    def _live_toggled(self, on: bool):
        if on and self.active and self._shown():
            self.timer.start()
            self._live_tick()
        else:
            self.timer.stop()

    def _live_ms(self) -> int:
        return SLOOP3_LIVE_MS if self._sloop3() is not None else LIVE_MS

    def _sloop3_editor_open(self) -> bool:
        """SLOOP 3's own editor is loaded on the page "Editor" (it polls the FM-1 itself, even while hidden)"""
        win = getattr(self.ctx, "window", None)
        ed = getattr(win, "editor", None) if win is not None else None
        return bool(ed is not None and str(getattr(ed, "loaded_for", "") or "").startswith("sloop3/"))

    def _live_tick(self):
        if self.live_busy or not self._shown() or not self.active:
            return
        if self.timer.interval() != self._live_ms():
            self.timer.setInterval(self._live_ms())
            self.cb_live.setText(_("refresh every {n} s").format(n=self._live_ms() // 1000))
        if self._sloop3() is not None and self._sloop3_editor_open():
            self.live_state.setText(_("paused – SLOOP 3's editor is open (page “Editor”) and asks the FM-1 itself"))
            return
        why = self._busy_reason()
        if why:                                 # (a backup, restore or installation has the device: wait, ask nothing)
            self.live_state.setText(_("paused – {reason}").format(reason=why))
            return
        version = self.ctx.info.version if self.ctx.info else ""
        link = self.ctx.link
        info = self.ctx.info
        if self._sloop3() is not None:
            ask = health.sloop3_live            # SLOOP 3: LIVE op 0 (only the state)
        elif health.audio_stats_supported(version):
            def ask(lk):
                return health.fm1_live(lk, version)
        elif health.sloop2_live_supported(info):
            def ask(lk):
                return health.sloop2_live(lk, info)     # SLOOP 2.7+: LIVE 77 op 0 (only the state)
        else:
            self._show_live(None)               # command 72 means something else on other firmwares
            self.timer.stop()
            return
        self.live_busy = True

        def done(v):
            self.live_busy = False
            self._show_live(v)

        def fail(msg, tb):
            self.live_busy = False
            self.live_state.setText(_("no live values"))
        run_job(self, lambda w: ask(link), on_done=done, on_fail=fail)

    def _meters_visible(self, on: bool):
        self.meters_box.setVisible(on)
        if self.meters_title is not None:
            self.meters_title.setVisible(on)

    MELODEE_ONLY = ("Überlast", "Zweiter Kern", "Rechenzeit", "USB-Audio-Fehler", "Speichertest")  # i18n: ignore

    def _rows_for(self, sloop: bool, voices: bool = True):
        """SLOOP's LIVE state has no memory, cache or audio-block values (Melodee's AUDIO_STATS has no transport):
        rows that cannot be filled are hidden instead of showing “–”"""
        def show(field, on):
            field.setVisible(on)
            lab = self.live_form.labelForField(field)
            if lab is not None:
                lab.setVisible(on)
        for w in (self.ram, self.cache, *(self.f["L" + k] for k in self.MELODEE_ONLY)):
            show(w, not sloop)
        show(self.f["LTransport"], sloop)
        show(self.f["LStimmen"], not sloop or voices)

    def _show_sloop_live(self, a: dict):
        """SLOOP 3's or SLOOP 2's LIVE state in the same box (what they do not report stays “–”)"""
        cpu, peak = a.get("cpu_percent") or 0, a.get("cpu_peak")
        _set_bar(self.cpu, cpu, 100, f"{cpu:.0f} %" + (" · " + _("peak {n} %").format(n=peak) if peak is not None else ""))
        _set_bar(self.ram, 0, 0, "–")
        _set_bar(self.cache, 0, 0, "–")
        for k in LIVE_ROWS:
            self.f["L" + k].setText("–")
        self.f["USB-Audio"].setText("–")
        if a.get("voices") is not None:
            self.f["LStimmen"].setText(ngettext("{n} playing", "{n} playing", a["voices"]).format(n=a["voices"]))  # i18n: ignore
        pos = ""
        if a.get("bar") is not None:
            pos = " · " + _("bar {bar}.{beat}").format(bar=a["bar"] + 1, beat=(a.get("beat") or 0) + 1)
        self.f["LTransport"].setText((_("playing") if a.get("playing") else _("stopped")) + " · " +
                                     _("{bpm} BPM").format(bpm=f"{a.get('bpm', 0):g}") + pos)
        self._rows_for(True, a.get("voices") is not None)
        tracks = a.get("tracks") or []
        self.f["LPro Spur"].setText(" · ".join(  # i18n: ignore
            f"{t['name']}: " + ", ".join(x for x in (
                _("pattern {n}").format(n=t["pattern"]) if t.get("pattern") else "",
                _("muted") if t.get("mute") else "", _("solo") if t.get("solo") else "") if x)
            for t in tracks if t.get("pattern") or t.get("mute") or t.get("solo")) or "–")
        while len(self.meters) < len(tracks):
            b = QProgressBar()
            b.setRange(0, 127)
            b.setTextVisible(True)
            b.setMinimumWidth(40)
            self.meters_lay.addWidget(b)
            self.meters.append(b)
        for i, b in enumerate(self.meters):
            b.setVisible(i < len(tracks))
            if i < len(tracks):
                b.setValue(int(tracks[i].get("meter") or 0))
                b.setFormat(tracks[i]["name"])
        self._meters_visible(bool(tracks))
        name = "SLOOP 3" if a.get("source") == "sloop3" else "SLOOP"
        self.live_state.setText((_("live") if self.timer.isActive() else _("read")) + " · " +
                                _("{firmware}'s LIVE state (read only)").format(firmware=name))

    def _show_live(self, a: dict | None, empty_text: str = ""):
        if a and a.get("source") in ("sloop3", "sloop"):
            self._show_sloop_live(a)
            return
        self._meters_visible(False)
        self._rows_for(False)
        if not a:
            empty_text = empty_text or _("no audio values with this firmware")
            for k in LIVE_ROWS:
                self.f["L" + k].setText("–")
            _set_bar(self.cpu, 0, 0, empty_text)
            self.f["USB-Audio"].setText("–")
            return
        cpu = a.get("cpu_percent", 0)
        _set_bar(self.cpu, cpu, 100, f"{cpu:.0f} %")
        self.f["USB-Audio"].setText(
            (_("playback {rate} Hz").format(rate=a['play_rate']) if a.get("play_alt") else _("playback off")) + " · " +
            (_("recording {rate} Hz").format(rate=a['capture_rate']) if a.get("capture_alt") else _("recording off")))
        self.f["LUSB-Audio-Fehler"].setText(  # i18n: ignore
            _("dropouts playback {underruns} / recording {overruns} · faulty packets {bad} · missed USB "
              "frames {missed}").format(underruns=a.get('play_underruns', 0), overruns=a.get('capture_overruns', 0),
                                        bad=a.get('bad_packets', 0), missed=a.get('missed_usb_frames', 0)))
        self.f["LRechenzeit"].setText(
            _("longest audio block {us} µs · late blocks {late}").format(us=a.get('longest_render_us', 0),
                                                                          late=a.get('late_renders', 0)))
        if "voices_active" in a:
            self.f["LStimmen"].setText(_("{active} active, {held} held").format(  # i18n: ignore
                active=a['voices_active'], held=a['voices_held']))
            self.f["LÜberlast"].setText(_("{shed} voices cut off (CPU), {given} given up to the voice budget").format(  # i18n: ignore
                shed=a['voices_shed'], given=max(0, a['voices_given_up'] - a['voices_shed'])))
        else:
            self.f["LStimmen"].setText("–")  # i18n: ignore
            self.f["LÜberlast"].setText("–")  # i18n: ignore
        if "track_1_voices_shed" in a:
            self.f["LPro Spur"].setText(" · ".join(  # i18n: ignore
                _("track {n}: {shed}/{given}").format(n=i, shed=a[f'track_{i}_voices_shed'],
                                                      given=a[f'track_{i}_voices_given_up']) for i in range(1, 5))
                + "  " + _("(cut off/given up)"))
        else:
            self.f["LPro Spur"].setText("–")  # i18n: ignore
        if "core1_online" in a:
            faults = a.get("core1_faults", 0) + a.get("core1_rejected", 0)
            self.f["LZweiter Kern"].setText(  # i18n: ignore
                (_("active") if a["core1_online"] else _("off")) + " · " +
                _("{jobs} tasks, longest {us} µs, timeouts {timeouts}").format(
                    jobs=a['core1_jobs'], us=a['core1_max_job_us'], timeouts=a['core1_timeouts']) +
                (" · " + _("{n} errors!").format(n=faults) if faults else ""))
        else:
            self.f["LZweiter Kern"].setText("–")  # i18n: ignore
        if a.get("resource_capacity"):
            _set_bar(self.ram, a["resource_used"], a["resource_capacity"],
                     _("{used} of {total} (peak {peak})").format(used=fmt_size(a['resource_used']),
                                                                  total=fmt_size(a['resource_capacity']),
                                                                  peak=fmt_size(a['resource_peak'])) +
                     (", " + _("{n} shortages").format(n=a['resource_failures']) if a.get("resource_failures") else ""))
            _set_bar(self.cache, a.get("cache_used", 0), a.get("cache_capacity", 0),
                     _("{used} of {total}").format(used=fmt_size(a.get('cache_used')),
                                                   total=fmt_size(a.get('cache_capacity'))))
            st = a.get("cache_status", 0)
            self.f["LSpeichertest"].setText(_("passed at start-up") if st == 1 else
                                            (_("not run") if st == 0 else _("status {n} – suspicious").format(n=st)))
        else:
            _set_bar(self.ram, 0, 0, "–")
            _set_bar(self.cache, 0, 0, "–")
            self.f["LSpeichertest"].setText("–")  # i18n: ignore
        if self.timer.isActive():
            self.live_state.setText(_("live"))

    # ------------------------------------------------------------ Presets
    def _engine(self, e: int) -> str:
        from .. import presetlib
        return presetlib.engine_display_name(e, self.engines)

    def _fill_presets(self):
        q = self.filter.text().strip().lower()
        only = self.cb_used.isChecked()
        self.tree.setSortingEnabled(False)
        self.tree.clear()
        for s in self.presets:
            if only and not s.used:
                continue
            eng = self._engine(s.engine) if s.used else ""
            if q and q not in s.name.lower() and q not in eng.lower() and q not in s.label.lower():
                continue
            it = QTreeWidgetItem([s.label, eng, s.name if s.used else _("(empty)"), "★" if s.favorite else ""])
            self.tree.addTopLevelItem(it)
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        for c in range(4):
            self.tree.resizeColumnToContents(c)
