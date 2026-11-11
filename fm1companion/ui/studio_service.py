# SPDX-License-Identifier: GPL-3.0-only
"""The studio twin as a service of the whole app (one twin per window).

Belongs to the main window (``window.studio``) and lives independently of the studio page:

* starts the twin (studio.Studio, QtWebEngine) in the matching firmware version, loads it from a backup
  or the last saved studio state (per device profile) and remembers where it came from
* saves its flash automatically (studio.Studio.save_state) as soon as something has changed, and keeps a
  limited list of snapshots for undo/restore
* stops the twin's clock when the studio is not visible and nothing is playing
* offers it as a **virtual device** “Studio twin (Melodee …)” / “(Skiff …)” / “(JIANT FM …)” / “(SLOOP …)” /
  “(CoralFlower …)” / “(KARMA …)” in the device selection: projects, compose, presets, backups and the editor then work with it via
  studio.StudioTransport. It is never ``ctx.ports`` (real devices), only ``ctx.vports``; ``ctx.is_virtual`` tells all pages.
* routes MIDI keyboards (and optionally the keys of the real FM-1) into the twin
"""
from __future__ import annotations

import contextlib
import datetime as dt
import json
import shutil
import subprocess
import threading
import time
import zlib
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from .. import config, studio as S
from ..i18n import _
from ..devices import PortInfo, Profile

SETTINGS = {"last_profile": "", "inputs": [], "fm1_keys": True, "audio": True, "export_dir": "",
            "export_fmt": "s24", "export_stems": False, "export_mode": "song", "export_seconds": 30,
            "export_loops": 2}
HISTORY_MAX = 30
VIRTUAL_KEY = "studio-twin|STUDIO"
VIRTUAL_PROFILE = Profile(id="studio-zwilling", name=_("Studio twin"), color="#a06cd5", notes=_("virtual"))
INFO_NAME = "studio-info.json"


def music_dir() -> Path:
    """The user's music folder (XDG), with “FM-1 Studio” inside."""
    base = None
    try:
        out = subprocess.run(["xdg-user-dir", "MUSIC"], capture_output=True, text=True, timeout=2).stdout.strip()
        if out and Path(out) != Path.home():
            base = Path(out)
    except (OSError, subprocess.SubprocessError):
        pass
    if base is None:
        for name in ("Musik", "Music"):
            if (Path.home() / name).is_dir():
                base = Path.home() / name
                break
    return (base or Path.home() / "Musik") / "FM-1 Studio"


def route_webengine_audio(sink: str) -> str:
    """Route QtWebEngine's (Chromium's) sound to a specific output (pactl move-sink-input). -> message
    ("" = done or nothing to do)."""
    if not sink:
        return ""
    if not shutil.which("pactl"):
        return _("The chosen output cannot be set for the studio (pactl is missing: {command}) – the sound comes "
                 "through the system's default output.").format(command="sudo apt install pulseaudio-utils")
    try:
        out = subprocess.run(["pactl", "-f", "json", "list", "sink-inputs"], capture_output=True, text=True,
                             timeout=3).stdout
        items = json.loads(out or "[]")
    except (OSError, subprocess.SubprocessError, ValueError):
        return _("The chosen output could not be set for the studio – system default output.")
    moved = 0
    for it in items:
        p = it.get("properties", {}) or {}
        who = " ".join(str(p.get(k, "")) for k in ("application.process.binary", "application.name",
                                                    "application.id")).lower()
        if "qtwebengine" in who or "chromium" in who or "fm1-companion" in who:
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                subprocess.run(["pactl", "move-sink-input", str(it.get("index")), sink], timeout=3)
                moved += 1
    return "" if moved else _("The studio sound was not found yet – it plays through the default output.")


class StudioService(QObject):
    changed = pyqtSignal()                 # twin started/loaded/closed, source changed
    history_changed = pyqtSignal()         # undo/restore, saved automatically
    page_changed = pyqtSignal()            # new web page (different build): studio page re-attaches it
    ui_event = pyqtSignal(str)             # Space/Ctrl+Z from the control surface
    message = pyqtSignal(str)

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.ctx = window.ctx
        self.cfg = config.module("studio", SETTINGS)
        self.st: S.Studio | None = None
        self.page = None
        self.source: dict = {}             # where the twin came from (see _set_source)
        self.history: list[dict] = []      # {label, time, flash (zlib)}
        self.pos = -1
        self._sum = None
        self.saved_at = 0.0
        self.busy = 0                      # running work (backup, loading …): don't interfere
        self.page_visible = False
        self.users = 0                     # other users that need sound/clock (playback from other pages)
        self._clock = None
        self._audio = False
        self.audio_note = ""
        self.last_error = ""
        self._inputs = None
        self._fm1_link = None
        self.timer = QTimer(self, interval=4000)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        self.ctx.virtual_ports = self._virtual_ports
        self.ctx.virtual_transport = self._virtual_transport
        self.ctx.virtual_profile = lambda port: VIRTUAL_PROFILE
        self.ctx.changed.connect(self._dev_changed)

    # ------------------------------------------------------------ Builds
    @staticmethod
    def available(firmware: str = "melodee") -> bool:
        try:
            return bool(S.builds(firmware))
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def firmware_for(key: str) -> str:
        """Device firmware key -> twin firmware ("" = doesn't exist (yet)): Melodee, Skiff, JIANT FM (the catalog's
        "jiant-fm" as the device layer's "jiant"), SLOOP, CoralFlower, KARMA, Felucca (also a Felucca fork the catalog
        names "felucca-…": Felucca's twin, with a warning, studio.felucca_fork_note)."""
        key = (key or "").lower()
        for fw in S.FIRMWARES:
            if key.startswith(fw) and S.builds(fw):
                return fw
        fw = S.stand_in_for(key)              # (the original V15/V16: the FM1 Open twin stands in, with a note)
        return fw if fw and S.builds(fw) else ""

    @property
    def firmware(self) -> str:
        """the running twin's firmware key ("melodee", "skiff", "jiant", "sloop", "coralflower", "karma"; "" = not running)"""
        return self.st.build.firmware if self.st else ""

    @property
    def firmware_name(self) -> str:
        """the running twin's firmware as shown ("Melodee", "Skiff", "JIANT FM", "SLOOP", "CoralFlower", "KARMA")"""
        return self.st.firmware_name if self.st else ""

    @property
    def running(self) -> bool:
        return self.st is not None

    @property
    def version(self) -> str:
        return self.st.build.version if self.st else ""

    @property
    def link(self):
        """Line to the twin: if it is the active device, the same one as the pages' (one request after
        the other), otherwise its own."""
        if self.ctx.is_virtual and self.ctx.link is not None and not self.ctx.link.closed:
            return self.ctx.link
        return self.st.link if self.st else None

    # ------------------------------------------------------------ Start/stop
    def ensure(self, version: str = "", firmware: str = "melodee") -> S.Studio:
        """Provide the twin in this version (or the next one) – call in the GUI thread."""
        want = S.pick_build(firmware, version)[0]
        if self.st is not None and self.st.build.path == want.path:
            return self.st
        was_virtual = self.ctx.is_virtual
        if self.st is not None:
            self._close_twin()
        from PyQt6.QtWebEngineCore import QWebEnginePage
        from . import web
        console = []

        class _Page(QWebEnginePage):
            def javaScriptConsoleMessage(self, level, msg, line, src):
                console.append(f"{src}:{line}: {msg}")
                del console[:-200]

        self.page = _Page(web.profile(), None)
        self.page.console = console
        web.track(self.page)
        self.st = S.Studio.create(firmware, version, backend="webengine", server=self.window.server,
                                  page=self.page, on_event=self._on_event)
        self.history, self.pos, self._sum = [], -1, None
        self._clock = None
        self._audio = False
        self.page_changed.emit()
        self._update_clock()
        self.ctx.scan()                    # virtual device appears in the selection
        if was_virtual:
            self.ctx.connect_port(self._virtual_ports()[0])
        self.changed.emit()
        return self.st

    def _close_twin(self):
        if self.ctx.is_virtual:
            self.ctx.disconnect(_("Studio twin restarted"))
        st, self.st = self.st, None
        page, self.page = self.page, None
        self.page_changed.emit()           # studio page releases the page before it disappears
        if st is not None:
            with contextlib.suppress(Exception):
                st.close()
        del page

    def shutdown(self):
        self.timer.stop()
        with contextlib.suppress(Exception):
            self.autosave(force=False)
        with contextlib.suppress(Exception):
            if self._inputs:
                self._inputs.close()
        self._unhook_fm1()
        if self.st is not None:
            self._close_twin()

    # ------------------------------------------------------------ Source and state
    def profile_folder(self) -> str:
        return self.source.get("profile_folder") or self.cfg.get("last_profile") or "Studio"

    def state_file(self) -> Path:
        return S.state_path(self.profile_folder())

    def _set_source(self, kind: str, **kw):
        self.source = {"kind": kind, "time": dt.datetime.now().isoformat(timespec="seconds"), **kw}
        if self.st is not None:
            self.source["rejected"] = list(self.st.rejected)
        folder = self.source.get("profile_folder")
        if folder:
            self.cfg["last_profile"] = folder
            with contextlib.suppress(OSError):
                config.save()

    def _write_info(self):
        p = self.state_file().with_name(INFO_NAME)
        with contextlib.suppress(OSError):
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(self.source, ensure_ascii=False, indent=1), "utf-8")

    def stored_states(self) -> list[tuple[str, Path, dict]]:
        """all saved studio states: (profile folder, file, origin)"""
        base = config.DATA_DIR / "studio"
        out = []
        for f in sorted(base.glob(f"*/{S.STATE_NAME}")) if base.is_dir() else []:
            info = {}
            with contextlib.suppress(OSError, ValueError):
                info = json.loads((f.parent / INFO_NAME).read_text("utf-8"))
            out.append((f.parent.name, f, info))
        return out

    def open_state(self, folder: str | None = None) -> dict:
        """Load the studio state of a device profile (twin in its version). -> metadata"""
        folder = folder or self.profile_folder()
        path = S.state_path(folder)
        meta, _flash = S.read_state(path)
        self.ensure(meta.get("firmware_version", ""), meta.get("firmware", "melodee"))
        self.snapshot(_("before loading the studio state"), only_if_changed=True)
        self.busy += 1
        try:
            meta = self.st.load_state(path)
        finally:
            self.busy -= 1
        info = {}
        with contextlib.suppress(OSError, ValueError):
            info = json.loads(path.with_name(INFO_NAME).read_text("utf-8"))
        self.source = info or {"kind": "state", "profile_folder": folder, "time": meta.get("created", "")}
        self.source["profile_folder"] = folder
        self.st.rejected = list(self.source.get("rejected", []))
        self.cfg["last_profile"] = folder
        self._sum = None
        self.snapshot(_("Studio state loaded"))
        self.saved_at = time.time()
        self.changed.emit()
        return meta

    def loaded_backup(self, archive, kind: str, **kw):
        """after loading a backup (finished from a worker thread): remember the origin, save."""
        self._set_source(kind, backup_version=S.backup_version(archive), **kw)
        self._sum = None
        self.snapshot(_("loaded from the FM-1") if kind == "device" else _("loaded from the backup"))
        self.autosave(force=True)
        self.changed.emit()

    def auto_start(self) -> bool:
        """Provide the twin if it isn't running: studio state of the active device (otherwise the last used one),
        otherwise empty in the device's version. -> is it running?"""
        if self.st is not None:
            return True
        c = self.ctx
        folders = []
        if c.profile is not None and not c.is_virtual:
            folders.append(c.profile.folder_name)
        if self.cfg.get("last_profile"):
            folders.append(self.cfg["last_profile"])
        for f in folders:
            if S.state_path(f).exists():
                try:
                    self.open_state(f)
                    return True
                except Exception as e:  # noqa: BLE001 – broken state: start empty
                    self.last_error = _("Studio state “{name}” not readable: {error}").format(name=f, error=e)
        fw = self.firmware_for(c.firmware_key) if ((c.info or c.midi_connected) and not c.is_virtual) else ""
        dev = (c.info.version if c.info else (c.profile.firmware_version if c.profile else "")) if fw else ""
        if S.stand_in_for(c.firmware_key):      # (the original V15/V16: its version is no FM1 Open version)
            dev = ""
        if c.profile is not None and not c.is_virtual and dev:
            self.source = {"kind": "new", "time": dt.datetime.now().isoformat(timespec="seconds"),
                           "profile_folder": c.profile.folder_name}
        self.new_empty(S.fw_version(fw, dev) if fw else "", fw or "melodee")
        return self.st is not None

    def new_empty(self, version: str = "", firmware: str = "melodee"):
        """fresh twin (empty flash) – when there is no state yet."""
        self.ensure(version, firmware)
        if not self.source:
            self.source = {"kind": "new", "time": dt.datetime.now().isoformat(timespec="seconds"),
                           "profile_folder": self.profile_folder()}
            self.snapshot(_("started empty"))
            self.changed.emit()

    # ------------------------------------------------------------ Autosave and history
    def snapshot(self, label: str | None = None, only_if_changed: bool = False) -> bool:
        if label is None:
            label = _("Intermediate state")
        if self.st is None:
            return False
        try:
            flash = self.st.flash()
        except S.StudioError as e:
            self.last_error = str(e)
            return False
        z = zlib.compress(flash, 6)
        if only_if_changed and self.history and 0 <= self.pos < len(self.history) \
                and self.history[self.pos]["flash"] == z:
            return False
        if self.history and 0 <= self.pos < len(self.history) and self.history[self.pos]["flash"] == z:
            self.history[self.pos]["label"] = label
            self.history_changed.emit()
            return True
        del self.history[self.pos + 1:]
        self.history.append({"label": label, "time": time.time(), "flash": z})
        if len(self.history) > HISTORY_MAX:
            del self.history[:len(self.history) - HISTORY_MAX]
        self.pos = len(self.history) - 1
        self._sum = None
        self.history_changed.emit()
        return True

    def can_undo(self) -> bool:
        return self.st is not None and self.pos > 0

    def can_redo(self) -> bool:
        return self.st is not None and 0 <= self.pos < len(self.history) - 1

    def _goto(self, pos: int) -> str:
        if self.st is None or not 0 <= pos < len(self.history):
            return ""
        self.busy += 1
        try:
            self.st.set_flash(zlib.decompress(self.history[pos]["flash"]))
        finally:
            self.busy -= 1
        self.pos = pos
        self._sum = None
        self._clock = None
        self._update_clock()
        self.autosave(force=True)
        self.history_changed.emit()
        self.changed.emit()
        return self.history[pos]["label"]

    def undo(self) -> str:
        return self._goto(self.pos - 1) if self.can_undo() else ""

    def redo(self) -> str:
        return self._goto(self.pos + 1) if self.can_redo() else ""

    def autosave(self, force: bool = False) -> Path | None:
        """Save the twin's flash with the device profile (only if something has changed, unless force)."""
        if self.st is None or self.busy:
            return None
        try:
            s = self.st.flash_sum()
        except S.StudioError:
            return None
        if not force and s == self._sum:
            return None
        try:
            path = self.st.save_state(self.state_file(), note=self.source.get("device", ""))
        except (OSError, S.StudioError) as e:
            self.last_error = _("Studio state not saved: {error}").format(error=e)
            self.message.emit(self.last_error)
            return None
        if self._sum is not None and s != self._sum and not force:
            self.snapshot(_("Change at {time}").format(time=time.strftime("%H:%M")))
        self._sum = s
        self.saved_at = time.time()
        self._write_info()
        self.history_changed.emit()
        return path

    def _tick(self):
        if self.st is None or self.busy:
            return
        self.autosave()
        self._update_clock()

    # ------------------------------------------------------------ Clock and sound
    def transport(self) -> dict:
        if self.st is None:
            return {}
        try:
            return self.st.transport_state()
        except S.StudioError:
            return {}

    def wants_clock(self) -> bool:
        if self.st is None:
            return False
        if self.page_visible or self.users > 0 or self.busy or self.ctx.is_virtual:
            return True
        return bool(self.transport().get("busy"))

    def _update_clock(self):
        if self.st is None:
            return
        want = self.wants_clock()
        if want == self._clock:
            return
        try:
            self.st.set_clock(want)
            if not want and self._audio:
                self.set_audio(False, remember=False)
            elif want and self.cfg.get("audio", True) and not self._audio:
                self.set_audio(True, remember=False)
        except S.StudioError as e:
            self.last_error = str(e)
            return
        self._clock = want

    def set_visible(self, on: bool):
        self.page_visible = on
        self._update_clock()

    def use(self, on: bool):
        """a user outside the studio page needs sound and clock (playback in projects/compose)."""
        self.users = max(0, self.users + (1 if on else -1))
        self._clock = None if on else self._clock
        self._update_clock()

    @property
    def audio_on(self) -> bool:
        return self._audio

    def set_audio(self, on: bool, remember: bool = True) -> str:
        if remember:
            self.cfg["audio"] = bool(on)
            with contextlib.suppress(OSError):
                config.save()
        if self.st is None:
            return ""
        try:
            if on:
                self.st.start_audio()
            else:
                self.st.stop_audio()
        except S.StudioError as e:
            self.audio_note = _("Sound not available: {error}").format(error=e)
            self._audio = False
            return self.audio_note
        self._audio = on
        self.audio_note = ""
        if on:
            from .audio import chosen_output
            from .. import audioout
            chosen = chosen_output()

            def work():
                # selected output – or, if the default is the FM-1, speakers/HDMI (never unnoticed to the FM-1)
                sink = audioout.effective_output(chosen)
                if not sink:
                    return
                for _i in range(20):            # Chromium's stream appears with a short delay: move it early
                    time.sleep(0.1)
                    note = route_webengine_audio(sink)
                    if not note:
                        self.audio_note = ""
                        return
                self.audio_note = note
            threading.Thread(target=work, daemon=True, name="fm1c-studio-route").start()
        return ""

    # ------------------------------------------------------------ Control surface, MIDI
    def _on_event(self, m: dict):
        if m.get("ev") == "ui" and m.get("action"):
            self.ui_event.emit(str(m["action"]))

    def show_panel(self, on: bool, bg: str = "", fg: str = ""):
        if self.st is None:
            return
        with contextlib.suppress(S.StudioError):
            self.st.backend.call("panel", on=bool(on), bg=bg, fg=fg, timeout=10)

    def set_inputs(self, names: list[str]) -> list[str]:
        """Route the system's MIDI inputs (USB keyboard …) into the twin -> error messages"""
        from .. import sims
        if self._inputs is None:
            self._inputs = sims.SystemInputs(lambda name, data: self._midi_in(data))
        self.cfg["inputs"] = list(names)
        with contextlib.suppress(OSError):
            config.save()
        return self._inputs.set(list(names))

    def input_names(self) -> list[str]:
        from .. import sims
        exclude = ()
        if self.ctx.port is not None and not self.ctx.is_virtual:
            exclude = (getattr(self.ctx.port, "in_name", "") or "",)
        return sims.SystemInputs.available(exclude)

    def _midi_in(self, data):
        st = self.st
        if st is None or not data or data[0] >= 0xF0:     # channel messages only (no SysEx, no clock)
            return
        with contextlib.suppress(Exception):
            st.play_midi(data)

    def set_fm1_keys(self, on: bool):
        self.cfg["fm1_keys"] = bool(on)
        with contextlib.suppress(OSError):
            config.save()
        self._dev_changed()

    def _fm1_listener(self, data, direction):
        if direction == "in":
            self._midi_in(data)

    def _unhook_fm1(self):
        if self._fm1_link is not None:
            with contextlib.suppress(Exception):
                self._fm1_link.remove_listener(self._fm1_listener)
        self._fm1_link = None

    def _dev_changed(self):
        """Keys of the real FM-1 play along in the twin (never those of the twin itself)."""
        link = self.ctx.link if (self.ctx.midi_connected and not self.ctx.is_virtual) else None
        if not self.cfg.get("fm1_keys", True) or self.st is None:
            link = None
        if link is self._fm1_link:
            return
        self._unhook_fm1()
        if link is not None:
            link.add_listener(self._fm1_listener)
            self._fm1_link = link

    # ------------------------------------------------------------ Virtual device
    def _virtual_ports(self) -> list[PortInfo]:
        if self.st is None:
            return []
        label = _("Studio twin ({firmware} {version})").format(firmware=self.st.firmware_name,
                                                                version=self.st.build.version.lstrip('v'))
        return [PortInfo(in_name=label, out_name=label, label=label, client=None, serial="STUDIO",
                         usb_path="studio-twin", product=_("Studio twin"), virtual=True)]

    def _virtual_transport(self, port):
        if self.st is None:
            raise S.StudioError(_("The studio twin is not running – open the page “Studio” first."))
        self._clock = None
        QTimer.singleShot(0, self._update_clock)
        return S.StudioTransport(self.st.backend)

    def activate_virtual(self) -> bool:
        """Select the twin as the active device (device selection at the top)."""
        ports = self._virtual_ports()
        if not ports:
            return False
        self.ctx.scan()
        self.window._switching = True      # deliberate switch: no “FM-1 disconnected” notice
        QTimer.singleShot(8000, lambda: setattr(self.window, "_switching", False))
        self.ctx.connect_port(ports[0])
        return True

    # ------------------------------------------------------------ Description
    def source_text(self) -> str:
        s = self.source
        if self.st is None:
            return _("The twin is not running yet.")
        when = s.get("time", "")
        with contextlib.suppress(ValueError):
            # Translators: date and time format (strftime)
            when = dt.datetime.fromisoformat(when).strftime(_("%Y-%m-%d %H:%M"))
        kind = s.get("kind")
        if kind == "device":
            txt = _("fetched from the FM-1 “{device}” on {time}").format(device=s.get('device', '?'), time=when)
        elif kind == "backup":
            txt = _("from the backup “{backup}” ({device}), opened on {time}").format(
                backup=Path(s.get('backup', '')).name, device=s.get('device') or _('unknown device'), time=when)
        elif kind == "state":
            txt = _("Studio state of “{name}” ({time})").format(name=s.get('profile_folder', '?'), time=when)
        elif kind == "new":
            txt = _("started empty – nothing loaded yet")
        else:
            txt = _("unknown origin")
        rej = s.get("rejected") or []
        if rej:
            from .. import backup
            fw = self.st.build.firmware if self.st is not None else ""
            sloop = S.FIRMWARES.get(fw, {}).get("backup") == "sloop"          # (SLOOP and KARMA: SLOOP's objects)
            names = ", ".join(backup.object_title(i, sloop, jiant=fw == "jiant", coral=fw == "coralflower",
                                                  felucca=fw == "felucca")
                              for i in rej[:4]) + (" …" if len(rej) > 4 else "")
            txt += _(". Not taken over ({n}): {names} – these parts do not fit {firmware} {version} and are left out "
                     "when writing back").format(n=len(rej), names=names, firmware=self.st.firmware_name,
                                                 version=self.st.build.version)
        return txt
