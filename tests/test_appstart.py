# SPDX-License-Identifier: GPL-3.0-only
"""Real start path as a subprocess (python -m fm1companion): splash, wizard, main window, quit.

Checks that the app starts in difficult environments AND ends cleanly (exit code 0, no signal 11 while
tearing down the QApplication, no traceback): fresh HOME, broken settings file, non-writable HOME,
missing QtWebEngine, missing rtmidi. FM1C_AUTOQUIT_MS makes the app close after n ms as if via "Quit".
Runs under flock /tmp/fm1c-ui.lock (UI tests share the machine)."""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LOCK = "/tmp/fm1c-ui.lock"
NOISE = ("QRhi", "GPUInfo", "propagateSizeHints", "dbus", "RHI for backend", "XDG_RUNTIME_DIR", "GL Type",
         "Surface ", "Using ", "Init Parameters", "  *  ", "No suitable graphics backend", "QStandardPaths",
         "Failed to connect", "offscreen", "ALSA", "rtmidi", "snd_", "QWindowsVista")

# bootstrap that makes selected modules unreachable before the start (simulates a missing Debian package)
BLOCKER = """
import builtins, runpy, sys
blocked = tuple(x for x in {blocked!r})
real = builtins.__import__
def fake(name, *a, **k):
    if blocked and name.startswith(blocked):
        raise ImportError(name + " fehlt (Test)")
    return real(name, *a, **k)
builtins.__import__ = fake
sys.argv = ["fm1-companion"] + {args!r}
runpy.run_module("fm1companion", run_name="__main__")
"""


def _env(home: Path, demo: bool = True) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("XDG_") and k != "FM1C_DEMO"}
    env.update(HOME=str(home), QT_QPA_PLATFORM="offscreen", FM1C_AUTOQUIT_MS="2500",
               PYTHONPATH=str(ROOT), LANG="de_DE.UTF-8")
    env.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu")
    env["HTTPS_PROXY"] = env["https_proxy"] = "http://127.0.0.1:9"      # no network: the update check fails at once
    if demo:
        env["FM1C_DEMO"] = "1"
    return env


def _run(home: Path, *args: str, blocked: tuple = (), demo: bool = True, timeout: int = 240):
    cmd = [sys.executable, "-c", BLOCKER.format(blocked=blocked, args=list(args))]
    if shutil.which("flock"):
        cmd = ["flock", LOCK] + cmd
    return subprocess.run(cmd, cwd=str(ROOT), env=_env(home, demo), capture_output=True, text=True, timeout=timeout)


def _clean(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if line.strip() and not any(n in line for n in NOISE))


def _assert_clean_exit(res, what: str):
    err = _clean(res.stderr)
    assert res.returncode == 0, f"{what}: exit code {res.returncode} (139 = crash on quit)\n{err[-3000:]}"
    assert "Traceback" not in res.stderr, f"{what}: Traceback\n{err[-3000:]}"
    assert "Segmentation" not in res.stderr, what


def _log_text(home: Path) -> str:
    out = ""
    for f in (home / ".local/state/fm1-companion/logs").glob("*.log*"):
        out += f.read_text("utf-8", errors="replace")
    for f in (home / ".cache/fm1-companion/demo-home/data").rglob("*.log*"):
        out += f.read_text("utf-8", errors="replace")
    return out


def test_fresh_start_and_quit(tmp_path):
    """First start: without config, without ~/FM-1 – splash, wizard, window, quit -> exit 0, config written."""
    home = tmp_path / "home"
    home.mkdir()
    res = _run(home)
    _assert_clean_exit(res, "Erster Start")
    cfg = list(home.rglob("config.json"))
    assert cfg, "no settings written"
    assert "wizard_done" in cfg[0].read_text("utf-8")
    assert "Start fm1-companion" in _log_text(home)


def test_second_start_no_splash(tmp_path):
    """Second start (wizard done) without splash – clean as well."""
    home = tmp_path / "home"
    home.mkdir()
    _assert_clean_exit(_run(home), "Erster Start")
    _assert_clean_exit(_run(home, "--no-splash"), "Zweiter Start")


def test_broken_config(tmp_path):
    """Broken config.json (real mode, no device): the app starts, puts the file aside and says so."""
    home = tmp_path / "home"
    cfgdir = home / ".config" / "fm1-companion"
    cfgdir.mkdir(parents=True)
    (cfgdir / "config.json").write_text("{ kaputt", "utf-8")
    res = _run(home, "--no-splash", demo=False)
    _assert_clean_exit(res, "kaputte Config")
    assert list(cfgdir.glob("config.json.kaputt-*")), "broken file not put aside"
    assert "beschädigt" in _log_text(home), "no note about the damaged settings file in the log"


def test_readonly_home(tmp_path):
    """HOME not writable: no crash, exit 0 (settings cannot be saved)."""
    if os.geteuid() == 0:
        pytest.skip("as root every directory is writable")
    home = tmp_path / "home"
    home.mkdir()
    home.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        res = _run(home, "--no-splash")
        _assert_clean_exit(res, "read-only HOME")
    finally:
        home.chmod(0o755)


def test_config_not_writable(tmp_path):
    """~/.config is a file (reproducible as root too; like a full disk/missing permissions): the app starts,
    quits cleanly and says once that the settings are not saved – before: traceback at start."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".config").write_text("keine Ordner hier")
    res = _run(home, "--no-splash", demo=False)
    _assert_clean_exit(res, "~/.config nicht beschreibbar")
    log = _log_text(home)
    assert "konnten nicht gespeichert werden" in log and "nicht lesbar" in log, log[-2000:]
    assert log.count("[Achtung] Die Einstellungen konnten nicht gespeichert werden") == 1


def test_without_webengine(tmp_path):
    """python3-pyqt6.qtwebengine missing: the app starts anyway, editor/simulator say what is missing."""
    home = tmp_path / "home"
    home.mkdir()
    res = _run(home, "--no-splash", blocked=("PyQt6.QtWebEngine",))
    _assert_clean_exit(res, "ohne QtWebEngine")
    assert "qtwebengine" in _log_text(home).lower(), "missing QtWebEngine is not reported"


def test_without_rtmidi(tmp_path):
    """python3-rtmidi missing: the app starts and reports the missing MIDI connection."""
    home = tmp_path / "home"
    home.mkdir()
    res = _run(home, "--no-splash", blocked=("rtmidi", "mido.backends.rtmidi"))
    _assert_clean_exit(res, "ohne rtmidi")
    assert "rtmidi" in _log_text(home).lower(), "missing rtmidi is not reported"


def test_cli_flags():
    env = _env(Path("/nonexistent"))
    out = subprocess.run([sys.executable, "-m", "fm1companion", "--version"], cwd=str(ROOT), env=env,
                         capture_output=True, text=True, timeout=60)
    assert out.stdout.strip() == "2.4.0"
    out = subprocess.run([sys.executable, "-m", "fm1companion", "--help"], cwd=str(ROOT), env=env,
                         capture_output=True, text=True, timeout=60)
    assert "--demo" in out.stdout and "--check" in out.stdout
