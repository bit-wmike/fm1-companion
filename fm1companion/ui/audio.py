# SPDX-License-Identifier: GPL-3.0-only
"""Preview playback and waveform display."""
from __future__ import annotations

import os
import struct
import time
import subprocess
import tempfile
import weakref
import wave
from pathlib import Path

from PyQt6.QtCore import QPointF, QRectF, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPalette, QPen
from PyQt6.QtWidgets import QMenu, QSizePolicy, QToolButton, QWidget

from ..i18n import _, ngettext

HAVE_MM: bool | None = None          # Qt Multimedia: only loaded when first needed (None = not checked yet)
NO_PLAYER_HINT = _("No player program found (pw-play, paplay, aplay or ffplay). "
                   "On the Pi: sudo apt install pipewire-bin – or alsa-utils.")


def _have_mm() -> bool:
    """Is Qt Multimedia available? (python3-pyqt6.qtmultimedia is only “recommended” and often missing.)"""
    global HAVE_MM
    if HAVE_MM is None:
        try:
            from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer  # noqa: F401
            HAVE_MM = True
        except ImportError:
            HAVE_MM = False
    return HAVE_MM


class PlayerError(Exception):
    """Playback not possible – the message is user-readable and names the solution."""


from ..audioout import (BACKENDS, FM1_SINK, Output, available_backends, list_outputs, output_hint,  # noqa: E402,F401
                        parse_pactl, parse_pw_dump)
from .. import audioout as _ao  # noqa: E402


def chosen_output() -> str:
    """output chosen by the user ("" = system default)."""
    try:
        from .. import config
        return str(config.module("audio", {"output": ""}).get("output") or "")
    except Exception:  # noqa: BLE001
        return ""


def set_output(name: str):
    try:
        from .. import config
        config.module("audio", {"output": ""})["output"] = name or ""
        config.save()
    except Exception:  # noqa: BLE001
        pass


def current_output() -> Output | None:
    """the output the preview currently goes to (chosen, otherwise default – but never unnoticed the FM-1)."""
    outs = list_outputs()
    want = _ao.effective_output(chosen_output(), outs)
    return next((o for o in outs if o.name == want), None) or next((o for o in outs if o.default), None)


def keep_off_fm1() -> bool:
    """Setting: never let the FM-1 become the default output by itself (default: on)."""
    try:
        from .. import config
        return bool(config.module("audio", {"output": "", "keep_off_fm1": True}).get("keep_off_fm1", True))
    except Exception:  # noqa: BLE001
        return True


def set_keep_off_fm1(on: bool):
    try:
        from .. import config
        config.module("audio", {"output": "", "keep_off_fm1": True})["keep_off_fm1"] = bool(on)
        config.save()
    except Exception:  # noqa: BLE001
        pass


def guard_default_output(delays=(0.0, 2.5, 6.0)):
    """in the background: if the FM-1 becomes the system's default output when plugged in, immediately set the default back to
    speakers/HDMI – otherwise every sound ends up on the FM-1, and PipeWire opens its audio stream (on the Pi 5 exactly
    that crashed the USB controller). Checked several times because PipeWire registers the FM-1's sound card only
    a few seconds after the MIDI port. Can be turned off in the “Output” menu."""
    if not keep_off_fm1():
        return

    def work():
        for d in delays:
            time.sleep(d)
            try:
                r = _ao.keep_default_off_fm1()
            except Exception:  # noqa: BLE001
                r = None
            if r:
                from .common import notify
                notify(_("The FM-1 (“{fm1}”) had become the default output – the computer's sound plays through "
                         "“{output}” again. The FM-1 can still be chosen under “Output”; this can be switched off "
                         "there.").format(fm1=r[0].description, output=r[1].description), "info")
                return
    import threading
    threading.Thread(target=work, daemon=True, name="fm1c-audio-guard").start()


_all_players: "weakref.WeakSet" = weakref.WeakSet()   # all players of the process – never two at once; weak,
#                                                     so that closed dialogs release their player
_hinted: set[str] = set()


def stop_all():
    """stop all players (page change, exit)."""
    for p in list(_all_players):
        try:
            p.stop()
        except Exception:  # noqa: BLE001 – nothing may interfere on exit
            pass


def _to_int16_bytes(samples) -> bytes:
    """List/array of ints or floats (−1..1) -> 16-bit PCM. Floats used to be rejected by struct.pack."""
    try:
        import numpy as np
        a = np.asarray(samples)
        if a.dtype.kind == "f":
            a = np.clip(a, -1.0, 1.0) * 32767          # floating point always means −1..1 (beyond: clamped)
        return np.clip(np.round(a), -32768, 32767).astype("<i2").tobytes()
    except ImportError:
        vals = [max(-32768, min(32767, int(round(max(-1.0, min(1.0, v)) * 32767 if isinstance(v, float) else v))))
                for v in samples]
        return struct.pack(f"<{len(vals)}h", *vals)


def write_wav(path: Path, samples, rate: int, channels: int = 1) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(int(rate))
        w.writeframes(_to_int16_bytes(samples))
    return path


class Player:
    """plays a file – robust against missing parts:

    1. players pw-play / paplay / aplay / ffplay (the first one that really starts; if one aborts immediately
       with an error – e.g. PipeWire unreachable –, the next one is tried),
    2. otherwise Qt Multimedia (only loaded then – on systems without an audio device it can crash on creation),
    3. otherwise a clear message instead of silence.

    The chosen audio output (“Output”) is passed as --target/--device and PULSE_SINK. Errors that only occur after
    the start are reported later by a watchdog (Qt timer). When a player starts, all others stop.
    play()/play_samples() report errors as a toast and return False; the *_strict variants raise PlayerError."""

    START_CHECK = 0.03            # how long to wait for an immediate abort (s) – more blocks the UI

    def __init__(self, parent=None):
        self.parent = parent
        self.proc = None
        self.mp = None
        self.out = None
        self.exe = next(iter(available_backends()), None)
        self.backend = ""
        self.last_error = ""
        self.errors: list[str] = []
        self._status = None
        self._err_file = None
        self._tmp_n = 0
        self._fallbacks: list[str] = []
        self._path, self._target = None, ""
        _all_players.add(self)

    def __del__(self):                         # dialog closed: a still running preview ends with it
        try:
            if self.proc is not None and self.proc.poll() is None:
                self.proc.terminate()
        except Exception:  # noqa: BLE001 – nothing may interfere during cleanup
            pass

    @property
    def available(self) -> bool:
        if available_backends():
            return True
        import importlib.util
        try:
            return importlib.util.find_spec("PyQt6.QtMultimedia") is not None
        except (ImportError, ValueError):
            return False

    @property
    def playing(self) -> bool:
        if self.proc is not None:
            return self.proc.poll() is None
        if self.mp is not None:
            try:
                from PyQt6.QtMultimedia import QMediaPlayer
                return self.mp.playbackState() == QMediaPlayer.PlaybackState.PlayingState
            except Exception:  # noqa: BLE001
                return False
        return False

    # ---- Players
    @staticmethod
    def _args(exe: str, path: Path, target: str) -> list[str]:
        if exe == "pw-play":
            return [exe] + ([f"--target={target}"] if target else []) + [str(path)]
        if exe == "paplay":
            return [exe] + ([f"--device={target}"] if target else []) + [str(path)]
        if exe == "aplay":
            return [exe, "-q", str(path)]
        return [exe, "-nodisp", "-autoexit", "-loglevel", "error", str(path)]

    def _try_exe(self, exe: str, path: Path, target: str) -> str:
        """-> "" if it is running (or already finished), otherwise the error message."""
        if exe == "aplay" and path.suffix.lower() not in (".wav", ".wave"):
            return _("aplay only plays WAV files ({suffix})").format(suffix=path.suffix)
        env = dict(os.environ)
        if target:
            env["PULSE_SINK"] = target
        err = tempfile.TemporaryFile()
        try:
            proc = subprocess.Popen(self._args(exe, path, target), stdout=subprocess.DEVNULL, stderr=err,
                                    stdin=subprocess.DEVNULL, env=env)
        except OSError as e:
            err.close()
            return _("cannot be started: {error}").format(error=e.strerror or e)
        try:
            rc = proc.wait(self.START_CHECK)
        except subprocess.TimeoutExpired:
            rc = None
        if rc not in (None, 0):
            err.seek(0)
            msg = err.read().decode("utf-8", "replace").strip().splitlines()
            err.close()
            return (msg[-1] if msg else _("aborted with code {code}").format(code=rc))[:200]
        self.proc, self._err_file, self.backend = proc, err, exe
        return ""

    def play_strict(self, path: Path) -> bool:
        """-> True if playback was started; PlayerError if it is fundamentally not possible."""
        stop_all()
        path = Path(path)
        if not path.exists():
            raise PlayerError(_("The file {name} no longer exists.").format(name=path.name))
        target = _ao.effective_output(chosen_output())   # never unnoticed to the FM-1 (a default that is the FM-1)
        self.errors = []
        self._path, self._target = path, target
        backends = available_backends()
        for i, exe in enumerate(backends):
            msg = self._try_exe(exe, path, target)
            if not msg:
                self.exe = exe
                self._fallbacks = backends[i + 1:]
                self._start_watch()
                return True
            self.errors.append(f"{exe}: {msg}")
        if _have_mm():
            try:
                self._play_qt(path)
                self.backend = "Qt Multimedia"
                return True
            except PlayerError as e:
                self.errors.append(f"Qt Multimedia: {e}")
        if not self.errors:
            raise PlayerError(NO_PLAYER_HINT + " " + _("(Qt Multimedia is missing too: {command})").format(
                command="sudo apt install python3-pyqt6.qtmultimedia"))
        raise PlayerError(_("No sound possible – {errors}. Is PipeWire running? (fm1-companion --check checks it) – "
                            "otherwise sudo apt install pipewire-bin alsa-utils.").format(errors="; ".join(self.errors)))

    def _play_qt(self, path: Path):
        from PyQt6.QtCore import QCoreApplication
        if QCoreApplication.instance() is None:
            raise PlayerError(_("not available without a running user interface"))
        from PyQt6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer
        if not QMediaDevices.audioOutputs():
            raise PlayerError(_("no audio output found"))
        if self.mp is None:
            self.mp = QMediaPlayer(self.parent)
            self.out = QAudioOutput(self.parent)
            self.out.setVolume(0.9)
            self.mp.setAudioOutput(self.out)
            self.mp.errorOccurred.connect(lambda _e, s: self._report(f"Qt Multimedia: {s}", self._status))
        self.mp.setSource(QUrl.fromLocalFile(str(path)))
        self.mp.play()

    def _start_watch(self):
        """Watchdog: if the player aborts with an error right after starting (PipeWire unreachable,
        format rejected …), the next one is tried, finally Qt Multimedia – and otherwise clearly reported."""
        try:
            from PyQt6.QtCore import QCoreApplication, QTimer
            if QCoreApplication.instance() is None:
                return
        except Exception:  # noqa: BLE001
            return
        proc = self.proc

        def check(n=[0]):
            if proc is not self.proc or proc is None:
                return
            rc = proc.poll()
            if rc is None:
                n[0] += 1
                if n[0] < 8:
                    QTimer.singleShot(200, check)
                return
            if rc == 0:
                return
            msg = ""
            if self._err_file is not None:
                try:
                    self._err_file.seek(0)
                    lines = self._err_file.read().decode("utf-8", "replace").strip().splitlines()
                    msg = lines[-1][:200] if lines else ""
                except (OSError, ValueError):
                    pass
            self.errors.append(f"{self.backend}: {msg or f'Abbruch mit Code {rc}'}")
            self.proc = None
            while self._fallbacks:
                exe = self._fallbacks.pop(0)
                m = self._try_exe(exe, self._path, self._target)
                if not m:
                    self.exe = exe
                    self._start_watch()
                    return
                self.errors.append(f"{exe}: {m}")
            if _have_mm():
                try:
                    self._play_qt(self._path)
                    self.backend = "Qt Multimedia"
                    return
                except PlayerError as e:
                    self.errors.append(f"Qt Multimedia: {e}")
            self._report(_("No sound – {errors}. Is PipeWire running? (fm1-companion --check) – otherwise "
                           "sudo apt install pipewire-bin alsa-utils.").format(errors="; ".join(self.errors)),
                         self._status)
        QTimer.singleShot(200, check)

    def play(self, path: Path, status=None) -> bool:
        """Play a file. If it doesn't work (no player, file gone), returns False, a toast
        (once per message) and – if given – status(text) with the explanation."""
        self._status = status
        try:
            ok = self.play_strict(path)
        except PlayerError as e:
            self._report(str(e), status)
            return False
        self._hint_output()
        return ok

    def play_samples(self, samples, rate: int, status=None) -> bool:
        self._status = status
        try:
            ok = self.play_samples_strict(samples, rate)
        except (PlayerError, OSError, ValueError, struct.error) as e:
            self._report(str(e), status)
            return False
        self._hint_output()
        return ok

    def _hint_output(self):
        """once per output: notice when the sound goes to the FM-1 (USB audio) (determined in the background –
        pw-dump must not block the UI)."""
        def work():
            try:
                o = current_output()
                h = output_hint(o)
                if h and o.name not in _hinted:
                    _hinted.add(o.name)
                    from .common import notify
                    notify(h, "warning")
            except Exception:  # noqa: BLE001
                pass
        import threading
        threading.Thread(target=work, daemon=True, name="fm1c-audio-out").start()

    def _report(self, msg: str, status=None):
        if status:
            try:
                status(_("Playback not possible: {error}").format(error=msg))
            except RuntimeError:                    # label already deleted
                pass
        if msg != self.last_error:
            self.last_error = msg
            from .common import notify
            notify(_("Playback not possible: {error}").format(error=msg), "error")

    def play_samples_strict(self, samples, rate: int) -> bool:
        self._tmp_n = (self._tmp_n + 1) % 2
        # a new, safely created file every time (mkstemp: random name, 0600 – no predictable name in
        # /tmp that another user could create beforehand as a symlink); the second-to-last is cleaned up, the
        # one just being read is never overwritten
        fd, name = tempfile.mkstemp(prefix="fm1c-preview-", suffix=".wav")
        os.close(fd)
        tmp = Path(name)
        if not getattr(Player, "_preview_cleanup", None):     # clean up on exit (otherwise stayed in /tmp)
            import atexit
            Player._preview_cleanup = set()
            atexit.register(lambda s=Player._preview_cleanup: [p.unlink(missing_ok=True) for p in list(s)])
        Player._preview_cleanup.add(tmp)
        mine = self.__dict__.setdefault("_tmp_files", [])
        mine.append(tmp)
        stop_all()                                   # stop first, then write the file
        while len(mine) > 2:
            old = mine.pop(0)
            Player._preview_cleanup.discard(old)
            old.unlink(missing_ok=True)
        write_wav(tmp, samples, rate)
        return self.play_strict(tmp)

    def stop(self):
        if self.mp is not None:
            self.mp.stop()
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(0.3)             # no zombie; pw-play ends immediately
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None
        if self._err_file is not None:
            try:
                self._err_file.close()
            except OSError:
                pass
            self._err_file = None


class OutputButton(QToolButton):
    """“Output: …” – where the preview on the computer goes (system default or a specific output)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        from PyQt6.QtGui import QIcon
        self.setIcon(QIcon.fromTheme("audio-speakers", QIcon.fromTheme("audio-card")))
        self.setMenu(QMenu(self))
        self.menu().aboutToShow.connect(self._fill)
        self.refresh(cached_only=True)

    def refresh(self, cached_only: bool = False):
        want = chosen_output()
        outs = _ao._outputs_cache[1] if cached_only else list_outputs()
        o = next((x for x in outs if x.name == want), None) if want else next((x for x in outs if x.default), None)
        name = o.description if o else (_("chosen") if want else _("Default"))
        self.setText(_("Output: {name}").format(name=name)[:48])
        tip = _("Where the preview on the computer goes.")
        h = output_hint(o) if o else ""
        import importlib.util
        try:
            mm = importlib.util.find_spec("PyQt6.QtMultimedia") is not None   # only check, don't load
        except (ImportError, ValueError):
            mm = False
        self.setToolTip(tip + (" " + h if h else "") + "\n" + _("Player programs: {names}").format(
            names=(", ".join(available_backends()) or _("none found")) + (", Qt Multimedia" if mm else "")))

    def _fill(self):
        m = self.menu()
        m.clear()
        want = chosen_output()
        outs = list_outputs(max_age=0)
        a = m.addAction(_("System default (never the FM-1 unnoticed)"))
        a.setCheckable(True)
        a.setChecked(not want)
        a.triggered.connect(lambda: self._set(""))
        for o in outs:
            label = o.description + (" " + _("(default)") if o.default else "") + (" – " + _("the FM-1") if o.is_fm1 else "")
            a = m.addAction(label)
            a.setCheckable(True)
            a.setChecked(o.name == want)
            a.triggered.connect(lambda _c=False, n=o.name: self._set(n))
        if not outs:
            m.addAction(_("(no outputs found – pw-dump/pactl missing?)")).setEnabled(False)
        m.addSeparator()
        k = m.addAction(_("Never let the FM-1 become the default output by itself"))
        k.setCheckable(True)
        k.setChecked(keep_off_fm1())
        k.setToolTip(_("When the FM-1 is plugged in, the computer often makes it the default output – then every sound "
                       "would come out of the FM-1. The app then sets the default straight back to speakers/HDMI."))
        k.triggered.connect(lambda on: (set_keep_off_fm1(on), guard_default_output() if on else None))

    def _set(self, name: str):
        set_output(name)
        stop_all()
        self.refresh()


class Waveform(QWidget):
    """Waveform with movable start/end markers (seconds)."""
    markers_changed = pyqtSignal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(90)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.peaks: list[tuple[float, float]] = []
        self.duration = 0.0
        self.start = 0.0
        self.end = 0.0
        self.editable = False
        self._drag = None
        self.text = _("no sound selected")

    def set_data(self, peaks, duration: float, start=0.0, end=0.0, editable=False):
        self.peaks, self.duration = peaks, duration
        self.start, self.end, self.editable = start, end or duration, editable
        self.text = ""
        self.update()

    def clear(self, text=None):
        self.peaks, self.duration, self.text = [], 0.0, _("no sound selected") if text is None else text
        self.update()

    def _x(self, t: float) -> float:
        return 0 if not self.duration else t / self.duration * self.width()

    def _t(self, x: float) -> float:
        return max(0.0, min(self.duration, x / max(1, self.width()) * self.duration))

    def paintEvent(self, _event):
        p = QPainter(self)
        pal = self.palette()
        p.fillRect(self.rect(), pal.color(QPalette.ColorRole.Base))
        hl = pal.color(QPalette.ColorRole.Highlight)
        txt = pal.color(QPalette.ColorRole.Text)
        if not self.peaks:
            p.setPen(pal.color(QPalette.ColorRole.PlaceholderText))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.text)
            return
        w, h = self.width(), self.height()
        mid = h / 2
        if self.editable:
            shade = QColor(txt)
            shade.setAlpha(25)
            p.fillRect(QRectF(0, 0, self._x(self.start), h), shade)
            p.fillRect(QRectF(self._x(self.end), 0, w - self._x(self.end), h), shade)
        path = QPainterPath()
        n = len(self.peaks)
        for i, (lo, hi) in enumerate(self.peaks):
            x = i / n * w
            path.moveTo(QPointF(x, mid - hi * mid * 0.95))
            path.lineTo(QPointF(x, mid - lo * mid * 0.95))
        p.setPen(QPen(hl, max(1.0, w / n)))
        p.drawPath(path)
        if self.editable:
            p.setPen(QPen(txt, 2))
            for t in (self.start, self.end):
                x = self._x(t)
                p.drawLine(QPointF(x, 0), QPointF(x, h))
        p.setPen(txt)
        p.drawText(6, 14, f"{self.duration:.2f} s" + ("  ·  " + _("selection {seconds} s").format(
            seconds=f"{self.end - self.start:.2f}") if self.editable else ""))

    def mousePressEvent(self, e):
        if not self.editable or not self.duration:
            return
        x = e.position().x()
        self._drag = "start" if abs(x - self._x(self.start)) < abs(x - self._x(self.end)) else "end"
        self.mouseMoveEvent(e)

    def mouseMoveEvent(self, e):
        if not self._drag:
            return
        t = self._t(e.position().x())
        if self._drag == "start":
            self.start = min(t, self.end - 0.01)
        else:
            self.end = max(t, self.start + 0.01)
        self.update()

    def mouseReleaseEvent(self, _event):
        if self._drag:
            self._drag = None
            self.markers_changed.emit(self.start, self.end)


class SliceView(QWidget):
    """Waveform with slice markers: drag = move, double-click = set marker,
    right-click = remove marker, click = select piece (and preview it)."""
    points_changed = pyqtSignal()
    slice_clicked = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(140)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.peaks: list[tuple[float, float]] = []
        self.duration = 0.0
        self.points: list[float] = []
        self.current = -1
        self._drag = -1
        self.text = _("no file loaded")

    def set_data(self, peaks, duration: float):
        self.peaks, self.duration, self.text = peaks, duration, ""
        self.update()

    def set_points(self, pts: list[float]):
        self.points = sorted(p for p in pts if 0 <= p < self.duration or not self.duration)
        self.current = min(self.current, len(self.points) - 1)
        self.update()

    def ranges(self) -> list[tuple[float, float]]:
        pts = sorted(self.points)
        return [(a, pts[i + 1] if i + 1 < len(pts) else self.duration) for i, a in enumerate(pts)]

    def _x(self, t):
        return 0 if not self.duration else t / self.duration * self.width()

    def _t(self, x):
        return max(0.0, min(self.duration, x / max(1, self.width()) * self.duration))

    def _near(self, x) -> int:
        best, bi = 6.0, -1
        for i, p in enumerate(self.points):
            d = abs(self._x(p) - x)
            if d <= best:
                best, bi = d, i
        return bi

    def slice_at(self, t: float) -> int:
        idx = -1
        for i, p in enumerate(sorted(self.points)):
            if p <= t:
                idx = i
        return idx

    def paintEvent(self, _event):
        p = QPainter(self)
        pal = self.palette()
        p.fillRect(self.rect(), pal.color(QPalette.ColorRole.Base))
        if not self.peaks:
            p.setPen(pal.color(QPalette.ColorRole.PlaceholderText))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.text)
            return
        w, h = self.width(), self.height()
        mid = h / 2
        hl = pal.color(QPalette.ColorRole.Highlight)
        txt = pal.color(QPalette.ColorRole.Text)
        rs = self.ranges()
        for i, (a, b) in enumerate(rs):
            c = QColor(hl)
            c.setAlpha(60 if i == self.current else (18 if i % 2 else 8))
            p.fillRect(QRectF(self._x(a), 0, self._x(b) - self._x(a), h), c)
        path = QPainterPath()
        n = len(self.peaks)
        for i, (lo, hi) in enumerate(self.peaks):
            x = i / n * w
            path.moveTo(QPointF(x, mid - hi * mid * 0.92))
            path.lineTo(QPointF(x, mid - lo * mid * 0.92))
        p.setPen(QPen(hl, max(1.0, w / n)))
        p.drawPath(path)
        p.setPen(QPen(txt, 1.5))
        for i, t in enumerate(sorted(self.points)):
            x = self._x(t)
            p.drawLine(QPointF(x, 0), QPointF(x, h))
            p.drawText(QPointF(x + 3, 12), str(i + 1))
        p.drawText(6, h - 6, ngettext("{seconds} s · {n} piece", "{seconds} s · {n} pieces", len(self.points)).format(
            seconds=f"{self.duration:.2f}", n=len(self.points)))

    def mousePressEvent(self, e):
        if not self.duration:
            return
        x = e.position().x()
        if e.button() == Qt.MouseButton.RightButton:
            i = self._near(x)
            if i >= 0:
                del self.points[i]
                self.points_changed.emit()
                self.update()
            return
        i = self._near(x)
        if i >= 0:
            self._drag = i
            return
        s = self.slice_at(self._t(x))
        if s >= 0:
            self.current = s
            self.update()
            self.slice_clicked.emit(s)

    def mouseDoubleClickEvent(self, e):
        if not self.duration:
            return
        t = self._t(e.position().x())
        if self._near(e.position().x()) < 0:
            self.points.append(round(t, 5))
            self.points.sort()
            self.points_changed.emit()
            self.update()

    def mouseMoveEvent(self, e):
        x = e.position().x()
        if self._drag >= 0:
            self.points[self._drag] = round(self._t(x), 5)
            self.update()
        else:
            self.setCursor(Qt.CursorShape.SizeHorCursor if self._near(x) >= 0 else Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, _event):
        if self._drag >= 0:
            self._drag = -1
            self.points.sort()
            self.points_changed.emit()
            self.update()


class LiveWave(QWidget):
    """running waveform (new on the right, old on the left) for recordings."""

    def __init__(self, seconds: float = 6.0, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(70)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.seconds = seconds
        self.cols: list[tuple[float, float]] = []
        self.col_frames = 0
        self._acc = None
        self.rate = 44100
        self.marker_cols: list[int] = []
        self.text = _("No signal yet")

    def reset(self, rate: int = 44100):
        self.rate = rate
        self.cols, self._acc, self.marker_cols = [], None, []
        self.update()

    def push(self, block):
        """block: float array (n, channels) or (n,)"""
        import numpy as np
        a = np.asarray(block)
        if a.ndim == 2:
            a = a.mean(axis=1) if a.shape[1] else a[:, 0]
        if not len(a):
            return
        per = max(1, int(self.rate * self.seconds / 600))
        if self._acc is not None and len(self._acc):
            a = np.concatenate([self._acc, a])
        n = len(a) // per * per
        if n:
            r = a[:n].reshape(-1, per)
            self.cols.extend(zip(r.min(axis=1).tolist(), r.max(axis=1).tolist()))
            del self.cols[:-600]
        self._acc = a[n:]
        self.update()

    def mark(self):
        self.marker_cols.append(len(self.cols))

    def paintEvent(self, _event):
        p = QPainter(self)
        pal = self.palette()
        p.fillRect(self.rect(), pal.color(QPalette.ColorRole.Base))
        if not self.cols:
            p.setPen(pal.color(QPalette.ColorRole.PlaceholderText))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.text)
            return
        w, h = self.width(), self.height()
        mid = h / 2
        path = QPainterPath()
        n = 600
        off = n - len(self.cols)
        for i, (lo, hi) in enumerate(self.cols):
            x = (i + off) / n * w
            path.moveTo(QPointF(x, mid - hi * mid * 0.95))
            path.lineTo(QPointF(x, mid - lo * mid * 0.95 + 0.5))
        p.setPen(QPen(pal.color(QPalette.ColorRole.Highlight), max(1.0, w / n)))
        p.drawPath(path)
