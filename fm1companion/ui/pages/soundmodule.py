# SPDX-License-Identifier: GPL-3.0-only
"""Sound module: the Pi plays with FluidSynth what Melodee's MIDI tracks 5–8 send.

Flow: start FluidSynth (separate process), connect the FM-1 output via aconnect, choose a sound
per channel. Custom kits and sample folders become SoundFonts (sf2.py) and can be put on a
channel – so e.g. Pocket Operator samples play on a MIDI track.
"""
from __future__ import annotations

import time
from pathlib import Path

from PyQt6.QtCore import QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QGridLayout, QHBoxLayout, QLabel,
                             QPlainTextEdit, QScrollArea, QSlider, QSpinBox, QVBoxLayout, QWidget)

from ... import alsaseq, config, sf2, soundmodule as SM
from ...miditools import GM_PROGRAMS
from ...protocol import Cmd
from ..common import Card, badge, button, heading, label, notify, run_job, safe_folder, show_error, start_dir
from . import PageSpec
from ...i18n import _, pgettext

SETTINGS = {"soundfont": "", "driver": "auto", "latency": "ausgewogen", "gain": 0.6, "polyphony": 96,
            "autoconnect": True, "autostart": False,
            "channels": [{"number": n, "enabled": True, "program": p} for n, p in zip(SM.DEFAULT_CHANNELS, (0, 33, 48, 81))],
            "custom": {}}                       # channel -> {"path": sf2, "name": …}


def _settings() -> dict:
    return config.module("soundmodule", SETTINGS)


def _save():
    try:
        config.save()
    except OSError:
        pass


class ChannelRow:
    def __init__(self, page, grid, row, d):
        self.page = page
        self.on = QCheckBox()
        self.on.setChecked(d.get("enabled", True))
        self.on.setToolTip(_("Play channel"))
        self.num = QSpinBox(minimum=1, maximum=16, value=int(d.get("number", 5)))
        self.num.setPrefix(_("Channel "))
        self.sound = QComboBox()
        self.sound.setMinimumWidth(240)
        self.vol = QSlider(Qt.Orientation.Horizontal, minimum=0, maximum=127, value=int(d.get("volume", 100)))
        self.pan = QSlider(Qt.Orientation.Horizontal, minimum=0, maximum=127, value=int(d.get("pan", 64)))
        self.rev = QSlider(Qt.Orientation.Horizontal, minimum=0, maximum=127, value=int(d.get("reverb", 40)))
        self.vol.setToolTip(_("Volume"))
        self.pan.setToolTip(_("Pan (centre = 64)"))
        self.rev.setToolTip(_("Reverb amount"))
        self.test = button("", "media-playback-start", tooltip=_("Test tone on this channel"),
                           slot=lambda: page.probe(self))
        self.track = QLabel(_("Track {n}").format(n=row + 4) if row <= 4 else "")
        for c, w in enumerate((self.on, self.track, self.num, self.sound, self.vol, self.pan, self.rev, self.test)):
            grid.addWidget(w, row, c)
        self.fill(d)
        for w in (self.on, self.num, self.sound, self.vol, self.pan, self.rev):
            sig = w.toggled if isinstance(w, QCheckBox) else (w.currentIndexChanged if isinstance(w, QComboBox)
                                                              else w.valueChanged)
            sig.connect(page.changed)

    def fill(self, d=None):
        cur = self.sound.currentData() if d is None else f"gm:{int(d.get('program', 0))}"
        if d is not None and d.get("kind") == "drum":
            cur = "drum:0"
        if d is not None and d.get("kind") == "sf2" and d.get("path"):
            cur = "sf2:" + d["path"]
        self.sound.blockSignals(True)
        self.sound.clear()
        for k in sorted(_settings()["custom"].values(), key=lambda x: x["name"]):
            self.sound.addItem(_("Own sound: {name}").format(name=k['name']), "sf2:" + k["path"])
        self.sound.addItem(_("Drums (General MIDI)"), "drum:0")
        for i, n in enumerate(GM_PROGRAMS):
            self.sound.addItem(f"{i + 1:03d} {n}", f"gm:{i}")
        ix = self.sound.findData(cur) if cur else -1
        self.sound.setCurrentIndex(ix if ix >= 0 else self.sound.findData("gm:0"))
        self.sound.blockSignals(False)

    def data(self) -> dict:
        kind, _sep, v = (self.sound.currentData() or "gm:0").partition(":")
        return {"number": self.num.value(), "enabled": self.on.isChecked(), "program": int(v) if kind == "gm" else 0,
                "kind": kind, "path": v if kind == "sf2" else "", "volume": self.vol.value(),
                "pan": self.pan.value(), "reverb": self.rev.value()}


class SoundModulePage(QWidget):
    navigate = pyqtSignal(str)
    channels_read = pyqtSignal(object)

    def __init__(self, ctx, window=None):
        super().__init__()
        self.ctx = ctx
        self.fs = SM.FluidSynth()
        self.exe = SM.find_binary()
        self.connected_to = ""
        s = _settings()
        outer = QVBoxLayout(self)
        outer.addWidget(heading(_("Sound Module"), _("Melodee's MIDI tracks 5–8 make no sound on the FM-1 – they send "
                                                     "via USB MIDI (track N starts on channel N). Here the Pi plays them "
                                                     "with FluidSynth: General MIDI sounds or your own samples.")))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

        # ---- FluidSynth missing?
        self.missing = Card(_("FluidSynth is not installed"), icon="dialog-warning",
                            subtitle=_("The sound module needs FluidSynth and a General MIDI sound bank. "
                                       "Run this once in the terminal, then open this page again:"))
        cmdrow = QHBoxLayout()
        cmdl = QLabel(f"<code>{SM.INSTALL_HINT}</code>")
        cmdl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        cmdrow.addWidget(cmdl, 1)
        cmdrow.addWidget(button(_("Copy"), "edit-copy",
                                slot=lambda: QGuiApplication.clipboard().setText(SM.INSTALL_HINT)))
        cmdrow.addWidget(button(_("Check again"), "view-refresh", slot=self.recheck))
        self.missing.lay.addLayout(cmdrow)
        lay.addWidget(self.missing)

        # ---- Engine
        eng = Card(_("Sound Module"), icon="speaker")
        g = QGridLayout()
        g.addWidget(QLabel(_("Sound bank:")), 0, 0)
        self.sf = QComboBox()
        self.sf.setMinimumWidth(320)
        g.addWidget(self.sf, 0, 1, 1, 2)
        g.addWidget(button(_("Other …"), "document-open", slot=self.pick_sf), 0, 3)
        g.addWidget(QLabel(_("Latency:")), 1, 0)
        self.lat = QComboBox()
        for k, t in SM.LATENCY_DE.items():
            self.lat.addItem(t, k)
        self.lat.setCurrentIndex(max(0, self.lat.findData(s["latency"])))
        g.addWidget(self.lat, 1, 1)
        g.addWidget(QLabel(_("Audio:")), 1, 2)
        self.drv = QComboBox()
        g.addWidget(self.drv, 1, 3)
        g.addWidget(QLabel(_("Volume:")), 2, 0)
        self.gain = QDoubleSpinBox(minimum=0.0, maximum=5.0, singleStep=0.1, decimals=2, value=float(s["gain"]))
        self.gain.setToolTip(_("Overall volume of FluidSynth (0.6 = default; above 1 it may distort)"))
        self.gain.valueChanged.connect(lambda v: self.fs.send(f"gain {v:.2f}"))
        g.addWidget(self.gain, 2, 1)
        g.addWidget(QLabel(_("Voices:")), 2, 2)
        self.poly = QSpinBox(minimum=16, maximum=512, value=int(s["polyphony"]))
        g.addWidget(self.poly, 2, 3)
        eng.lay.addLayout(g)
        row = QHBoxLayout()
        self.b_start = button(pgettext("button", "Start"), "media-playback-start", "primary", self.toggle)
        row.addWidget(self.b_start)
        self.b_connect = button(_("Connect FM-1"), "cable", slot=self.connect_fm1,
                                tooltip=_("Connect the MIDI output of the FM-1 to the sound module (aconnect)"))
        row.addWidget(self.b_connect)
        self.b_panic = button(_("All notes off"), "media-playback-stop", slot=self.fs.panic,
                              tooltip=_("CC 123 on all 16 channels of FluidSynth – against hanging notes"))
        row.addWidget(self.b_panic)
        self.auto = QCheckBox(_("Connect FM-1 automatically"))
        self.auto.setChecked(bool(s["autoconnect"]))
        row.addWidget(self.auto)
        self.autostart = QCheckBox(_("start with the app"))
        self.autostart.setChecked(bool(s["autostart"]))
        row.addWidget(self.autostart)
        row.addStretch(1)
        self.state_badge = badge(_("off"))
        row.addWidget(self.state_badge)
        eng.lay.addLayout(row)
        self.status = label("", "muted")
        eng.lay.addWidget(self.status)
        lay.addWidget(eng)

        # ---- Channels
        ch = Card(_("Channels"), icon="piano",
                  subtitle=_("Only the ticked channels sound – the keys of the FM-1 send on channel 1–4 and would "
                             "otherwise sound twice. On the FM-1 you set the channel of a MIDI track with E1 (CH)."))
        self.grid = QGridLayout()
        for c, t in enumerate(("", "Melodee", _("Channel"), _("Sound"), _("Volume"), _("Pan"), _("Reverb"), "")):
            self.grid.addWidget(label(t, "small"), 0, c)
        self.rows: list[ChannelRow] = []
        for i, d in enumerate(s["channels"]):
            self.rows.append(ChannelRow(self, self.grid, i + 1, d))
        ch.lay.addLayout(self.grid)
        r2 = QHBoxLayout()
        r2.addWidget(button(_("Read channels from the FM-1"), "view-refresh", slot=self.read_channels,
                            tooltip=_("reads the channel (CH) of the Melodee tracks 5–8")))
        r2.addWidget(button(_("Add channel"), "list-add", slot=self.add_channel))
        r2.addStretch(1)
        ch.lay.addLayout(r2)
        self.ch_msg = label("", "muted")
        ch.lay.addWidget(self.ch_msg)
        lay.addWidget(ch)

        # ---- Own samples
        kit = Card(_("Own samples as a sound"), icon="drum",
                   subtitle=_("A kit from “Samples & Kits” or a folder with WAVs becomes a SoundFont "
                              "(in ~/FM-1/SoundFonts) and plays on the chosen channel: drum kits from C2 one sound "
                              "per key, instrument kits spread over the keyboard."))
        kr = QHBoxLayout()
        self.kits = QComboBox()
        self.kits.setMinimumWidth(240)
        kr.addWidget(self.kits, 1)
        kr.addWidget(QLabel(_("on")))
        self.kit_ch = QSpinBox(minimum=1, maximum=16, value=5, prefix=_("Channel "))
        kr.addWidget(self.kit_ch)
        kr.addWidget(button(_("Use as sound"), "document-save", "primary", self.make_kit))
        kr.addWidget(button(_("Choose folder …"), "folder-open", slot=self.make_folder))
        kit.lay.addLayout(kr)
        self.kit_msg = label("", "muted")
        kit.lay.addWidget(self.kit_msg)
        lay.addWidget(kit)

        # ---- Details
        det = Card(_("Details"), icon="dialog-information")
        self.cmd_view = QPlainTextEdit()
        self.cmd_view.setReadOnly(True)
        self.cmd_view.setMaximumHeight(140)
        det.lay.addWidget(self.cmd_view)
        lay.addWidget(det)
        lay.addStretch(1)

        self.channels_read.connect(self._channels_read)
        self.timer = QTimer(self, interval=1000)
        self.timer.timeout.connect(self.tick)
        self._connect_tries = 0
        self.recheck()
        if s["autostart"] and self.exe:
            QTimer.singleShot(1500, self.start)

    # ---- State
    def recheck(self):
        self.exe = SM.find_binary()
        self.missing.setVisible(not self.exe)
        self.b_start.setEnabled(bool(self.exe))
        cur = self.sf.currentData() or _settings()["soundfont"]
        self.sf.clear()
        try:
            fonts = SM.find_soundfonts(safe_folder("SoundFonts")[0])
        except OSError:
            fonts = []
        for p in fonts:
            self.sf.addItem(p.name, str(p))
        if cur and self.sf.findData(cur) < 0 and Path(cur).exists():
            self.sf.addItem(Path(cur).name, cur)
        if not fonts and not cur:
            self.sf.addItem(_("no sound bank found (sudo apt install fluid-soundfont-gm)"), "")
        self.sf.setCurrentIndex(max(0, self.sf.findData(cur)))
        drivers = self._drivers = SM.audio_drivers(self.exe) if self.exe else []   # ask once, not on every start
        self.drv.clear()
        self.drv.addItem(_("automatic"), "auto")
        for d in drivers:
            if d in ("pipewire", "pulseaudio", "alsa", "jack"):
                self.drv.addItem(d, d)
        self.drv.setCurrentIndex(max(0, self.drv.findData(_settings()["driver"])))
        self.kits.clear()
        try:
            kits = sorted(safe_folder("Kits")[0].glob("*.fm1kit"))
        except OSError:
            kits = []
        for p in kits:
            self.kits.addItem(p.stem, str(p))
        if not self.kits.count():
            self.kits.addItem(_("no kits yet – build one under “Samples & Kits”"), "")
        self._show_state()

    def config_now(self) -> SM.Config:
        return SM.Config(soundfont=self.sf.currentData() or "", latency=self.lat.currentData(),
                         gain=self.gain.value(), polyphony=self.poly.value())

    def command(self) -> list[str]:
        driver = self.drv.currentData() or "auto"
        if driver == "auto":
            driver = SM.pick_driver(getattr(self, "_drivers", None) or [])
        return SM.build_command(self.exe or "fluidsynth", self.config_now(), driver)

    def store(self):
        s = _settings()
        s.update(soundfont=self.sf.currentData() or "", latency=self.lat.currentData(), gain=self.gain.value(),
                 polyphony=self.poly.value(), driver=self.drv.currentData() or "auto",
                 autoconnect=self.auto.isChecked(), autostart=self.autostart.isChecked(),
                 channels=[r.data() for r in self.rows])
        _save()

    # ---- Start/stop
    def toggle(self):
        if self.fs.running:
            self.stop()
        else:
            self.start()

    def start(self):
        if not self.exe:
            self.recheck()
            return
        if not self.sf.currentData():
            show_error(self, _("No sound bank"), _("Please choose a SoundFont file (.sf2) or run "
                                                   "“sudo apt install fluid-soundfont-gm”."))
            return
        if not Path(self.sf.currentData()).is_file():
            show_error(self, _("Sound bank missing"), _("The file {path} no longer exists. "
                                                        "Choose another one under “Sound bank” – without a sound bank "
                                                        "FluidSynth would stay silent.").format(path=self.sf.currentData()))
            self.recheck()
            return
        self.store()
        cmd = self.command()
        try:
            self.fs.start(cmd, [self.sf.currentData()])
        except OSError as e:
            show_error(self, _("FluidSynth does not start"), str(e))
            return
        self.cmd_view.setPlainText(" ".join(cmd))
        self.connected_to = ""
        self._connect_tries = 0
        self.timer.start()
        QTimer.singleShot(600, self._started)
        self._show_state()
        self.status.setText(_("starting …"))

    def _started(self):
        """0.6 s after start: is FluidSynth still running? Otherwise explain in plain language what went wrong."""
        if self.fs.running:
            self.apply()
            notify(_("Sound module started"), "success")
            return
        why = SM.explain_failure(self.fs.log)
        self.timer.stop()
        self._show_state()
        self.status.setText(_("Start failed – {reason}").format(reason=why))
        show_error(self, _("Sound module does not start"), why)

    def stop(self):
        was = self.fs.running
        self.fs.stop(block=False)                 # quitting can take up to 3 s – don't wait in the GUI thread
        self.timer.stop()
        self.connected_to = ""
        self._show_state()
        if was:
            notify(_("Sound module stopped"), "info")

    def apply(self, *_args):
        if not self.fs.running:
            return
        chans = []
        for r in self.rows:
            d = r.data()
            c = SM.Channel(d["number"], d["enabled"], 1, 0, d["program"], d["volume"], d["pan"], d["reverb"])
            if d["kind"] == "drum":
                c.bank, c.program = 128, 0
            elif d["kind"] == "sf2":
                if not Path(d["path"]).exists():
                    continue
                c.sfont = self.fs.load_font(d["path"])
            chans.append(c)
        self.fs.apply(chans, self.gain.value())

    def changed(self, *_args):
        self.store()
        self.apply()

    def tick(self):
        if not self.fs.running:
            if self.timer.isActive() and self.fs.p is None:
                self.timer.stop()
            self._show_state()
            return
        if self.auto.isChecked() and not self.connected_to and self._connect_tries < 8:
            self._connect_tries += 1
            self.connect_fm1(quiet=True)
        self._show_state()

    def _show_state(self):
        if not self.exe:
            self.state_badge.setText(_("not installed"))
            self.status.setText("")
            return
        run = self.fs.running
        self.b_connect.setEnabled(run)
        self.b_panic.setEnabled(run)
        self.b_start.setText(pgettext("button", "Stop") if run else pgettext("button", "Start"))
        self.b_start.setProperty("variant", "danger" if run else "primary")
        self.b_start.style().unpolish(self.b_start)
        self.b_start.style().polish(self.b_start)
        self.state_badge.setText(_("running") if run else _("off"))
        self.state_badge.setProperty("level", "success" if run else "")
        self.state_badge.style().unpolish(self.state_badge)
        self.state_badge.style().polish(self.state_badge)
        if run:
            cpu = self.fs.cpu_percent()
            up = int(time.time() - self.fs.started)
            parts = [_("running for {time} min").format(time=f"{up // 60}:{up % 60:02d}")]
            if cpu is not None:
                parts.append(f"CPU {cpu:.0f} %")
            parts.append(_("on the FM-1: {port}").format(port=self.connected_to) if self.connected_to else _("FM-1 not connected yet"))
            self.status.setText(" · ".join(parts))
            if self.fs.log:
                self.cmd_view.setPlainText(" ".join(self.fs.cmd) + "\n\n" + "\n".join(list(self.fs.log)[-40:]))
        elif self.fs.p is not None and self.fs.p.poll() is not None:
            self.status.setText(_("FluidSynth has quit – {reason}").format(reason=SM.explain_failure(self.fs.log)))
        else:
            self.status.setText(_("off"))

    # ---- Connect
    def connect_fm1(self, quiet=False):
        port = self.ctx.port

        def work(w):
            ports = alsaseq.list_ports()
            fl = alsaseq.find(ports, re_escape(SM.CLIENT_NAME), readable=False)
            src = None
            if port is not None and port.client is not None:
                src = next((p for p in ports if p.client == port.client and p.readable), None)
            src = src or alsaseq.find(ports, r"melodee|fm-1|fm1|felucca|sloop|skiff", readable=True)
            if not fl or not src:
                return False, (_("Sound module not in the sequencer") if not fl else _("no FM-1 found in the sequencer")), ""
            ok, msg = alsaseq.connect(src.addr, fl.addr)
            return ok, msg, src.label

        def done(res):
            ok, msg, name = res
            if ok:
                self.connected_to = name
                self.ch_msg.setText("")
            elif not quiet:
                self.ch_msg.setText(msg)
            self._show_state()
        run_job(self, work, on_done=done, on_fail=lambda m, tb: None if quiet else self.ch_msg.setText(m))

    # ---- Channels
    def add_channel(self):
        used = {r.num.value() for r in self.rows}
        n = next((c for c in range(1, 17) if c not in used), 16)
        self.rows.append(ChannelRow(self, self.grid, len(self.rows) + 1, {"number": n, "program": 0}))
        self.changed()

    def read_channels(self):
        ctx = self.ctx
        if not ctx.require(self):
            return
        p_e0 = getattr(ctx.info, "p_e0", 0)
        if not p_e0:
            self.ch_msg.setText(_("This firmware reports no track parameters."))
            return

        def work(w, link):
            out = {}
            for tr in range(4, 8):
                try:
                    a = link.request(Cmd.TRACK_PARAM, [tr, p_e0], timeout=0.6, retries=0)
                    v = (a[2] | a[3] << 7) - 8192
                    if 1 <= v <= 16:
                        out[tr + 1] = v
                except Exception:                          # noqa: BLE001
                    pass
            return out
        ctx.job(self, _("Read channels"), work, dialog=False, on_done=self.channels_read.emit)

    def _channels_read(self, res: dict):
        if not res:
            self.ch_msg.setText(_("The FM-1 reports no MIDI tracks 5–8 (older firmware?) – default: track N = channel N."))
            return
        for i, (tr, ch) in enumerate(sorted(res.items())):
            if i < len(self.rows):
                self.rows[i].num.setValue(ch)
                self.rows[i].track.setText(_("Track {n}").format(n=tr))
        self.ch_msg.setText(_("From the FM-1: {list}").format(
            list=", ".join(_("Track {track} → channel {channel}").format(track=t, channel=c) for t, c in sorted(res.items()))))
        self.changed()

    def probe(self, row: ChannelRow):
        if not self.fs.running:
            self.ch_msg.setText(_("Start the sound module first."))
            return
        c = row.num.value() - 1
        note = 36 if (row.sound.currentData() or "").split(":")[0] in ("drum", "sf2") else 60
        self.fs.send(f"noteon {c} {note} 100")
        QTimer.singleShot(500, lambda: self.fs.send(f"noteoff {c} {note}"))

    # ---- Own samples
    def make_kit(self):
        path = self.kits.currentData()
        if not path:
            return
        from ...samples import Kit
        try:
            kit = Kit.load(Path(path))
        except Exception as e:                                # noqa: BLE001 – broken/foreign kit file
            self.kit_msg.setText(_("Kit {name} cannot be read – the file is damaged or "
                                   "not a kit. Save it again under “Samples & Kits”.").format(name=Path(path).name))
            self.kit_msg.setToolTip(_("Technical: {error}").format(error=e))
            return
        self.kit_msg.setToolTip("")
        if not kit.zones:
            self.kit_msg.setText(_("“{name}” has no sounds yet – fill it under “Samples & Kits” first.").format(name=kit.name))
            return
        self._make(lambda: sf2.bank_from_kit(kit), kit.name)

    def make_folder(self):
        d = QFileDialog.getExistingDirectory(self, _("Folder with sounds"), str(start_dir("Samples")))
        if d:
            self._make(lambda: sf2.bank_from_folder(Path(d)), Path(d).name)

    def _make(self, build, name):
        ch = self.kit_ch.value()
        safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in name).strip() or "Kit"
        folder, err = safe_folder("SoundFonts")
        if err:
            self.kit_msg.setText(err)
            return
        target = folder / f"{safe}.sf2"
        self.kit_msg.setText(_("creating SoundFont …"))
        self.kit_msg.setToolTip("")

        def work(w):
            bank = build()
            sf2.write(bank, target)
            return bank

        def done(bank):
            s = _settings()
            s["custom"][str(ch)] = {"path": str(target), "name": bank.name}
            for r in self.rows:
                r.fill()
            row = next((r for r in self.rows if r.num.value() == ch), None)
            if row is None:
                self.rows.append(ChannelRow(self, self.grid, len(self.rows) + 1, {"number": ch}))
                row = self.rows[-1]
            row.sound.setCurrentIndex(max(0, row.sound.findData("sf2:" + str(target))))
            keys = sf2.drum_map(bank)
            from ...miditools import note_name
            self.kit_msg.setText(_("“{name}” is on channel {channel} ({n} sounds: {keys}). File: {path}").format(
                name=bank.name, channel=ch, n=len(bank.samples), path=target,
                keys=", ".join(f"{note_name(k)} {n}" for k, n in keys[:8]) + (" …" if len(keys) > 8 else "")))
            self.changed()
        run_job(self, work, on_done=done, on_fail=lambda m, tb: self.kit_msg.setText(_("SoundFont not created: {error}").format(error=m)))

    def pick_sf(self):
        fn, _filter = QFileDialog.getOpenFileName(self, _("Choose SoundFont"), str(start_dir("SoundFonts")),
                                            "SoundFonts (*.sf2 *.sf3)")
        if fn:
            self.sf.addItem(Path(fn).name, fn)
            self.sf.setCurrentIndex(self.sf.count() - 1)
            self.store()

    # ---- Page
    def activate(self):
        self.recheck() if not self.fs.running else self._show_state()

    def shutdown(self):
        self.store()
        self.fs.stop(timeout=1.5)


def re_escape(s):
    import re
    return re.escape(s)


def _make(ctx, window):
    return SoundModulePage(ctx, window)


PAGE = PageSpec(key="soundmodule", title=_("Sound Module"), group="klang", factory=_make,
                icons=("audio-speakers", "speaker"), order=50,
                description=_("The Pi plays Melodee's MIDI tracks 5–8 with FluidSynth – also with your own samples"),
                keywords=("fluidsynth", "soundfont", "sf2", "gm", "midi-spur", "sound module", "kit", "klangerzeuger",
                          "midi track", "synth"))
