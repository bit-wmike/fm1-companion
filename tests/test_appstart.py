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
import runpy, sys
blocked = tuple(x for x in {blocked!r})
class Block:                      # first on sys.meta_path: stops "import x" and importlib.import_module("x") alike
    def find_spec(self, name, path=None, target=None):
        if blocked and name.startswith(blocked):
            raise ImportError(name + " fehlt (Test)")
        return None
sys.meta_path.insert(0, Block())
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


@pytest.mark.parametrize("demo", [True, False])
def test_home_folders_out_of_reach(tmp_path, demo):
    """~/.cache, ~/.local, ~/.config and ~/FM-1 are files: what a read-only HOME looks like to the app, and
    reproducible as root too (test_readonly_home skips there). Before: PermissionError/NotADirectoryError from
    the first mkdir (ui/theme.py _asset_dir, then editor_host, samples) -> exit 1."""
    home = tmp_path / "home"
    home.mkdir()
    for name in (".cache", ".local", ".config", "FM-1"):
        (home / name).write_text("no folder here")
    res = _run(home, "--no-splash", demo=demo)
    _assert_clean_exit(res, "HOME-Ordner nicht erreichbar")
    assert "temporären Ordner" in res.stderr, res.stderr[-2000:]
    assert all((home / name).is_file() for name in (".cache", ".local", ".config", "FM-1"))


def test_fallback_dirs_only_when_needed(tmp_path):
    """app.fallback_dirs(): leaves usable folders alone and creates nothing; replaces the ones out of reach."""
    sys.path.insert(0, str(ROOT))
    from fm1companion import app
    home = tmp_path / "home"
    home.mkdir()
    env = {}
    assert app.fallback_dirs(env, home) == [] and env == {}
    assert list(home.iterdir()) == [], "the check itself must not create folders"
    (home / ".cache").write_text("x")
    env = {}
    lost = app.fallback_dirs(env, home)
    assert lost == [str(home / ".cache" / "fm1-companion")]
    assert set(env) == {"XDG_CACHE_HOME"} and Path(env["XDG_CACHE_HOME"]).parent.is_dir()
    assert not env["XDG_CACHE_HOME"].startswith(str(home))
    env = {"XDG_DATA_HOME": str(home / ".cache" / "data"), "XDG_CACHE_HOME": str(tmp_path / "ok")}
    assert app.fallback_dirs(env, home) == [str(home / ".cache" / "data" / "fm1-companion")]
    assert env["XDG_CACHE_HOME"] == str(tmp_path / "ok")
    if os.geteuid() != 0:
        ro = tmp_path / "ro"
        ro.mkdir()
        ro.chmod(stat.S_IRUSR | stat.S_IXUSR)
        try:
            assert not app._creatable(ro / "a" / "b")
        finally:
            ro.chmod(0o755)


def test_theme_assets_fall_back_to_temp(tmp_path, monkeypatch):
    """ui/theme.py: the cache folder cannot be created -> a temporary folder instead of an exception."""
    pytest.importorskip("PyQt6.QtWidgets")
    sys.path.insert(0, str(ROOT))
    from fm1companion import config
    from fm1companion.ui import theme
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setattr(config, "CACHE_DIR", blocker / "fm1-companion")
    monkeypatch.setattr(theme, "_asset_fallback", None)
    d = theme._asset_dir()
    assert d.is_dir() and blocker.is_file() and d == theme._asset_dir()
    shutil.rmtree(d, ignore_errors=True)


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
    assert out.stdout.strip() == "2.6.2"
    out = subprocess.run([sys.executable, "-m", "fm1companion", "--help"], cwd=str(ROOT), env=env,
                         capture_output=True, text=True, timeout=60)
    assert "--demo" in out.stdout and "--check" in out.stdout


def test_the_suite_never_uses_the_real_home():
    """tests/conftest.py points HOME and the XDG folders at a temporary folder before the app is imported"""
    import os
    from pathlib import Path
    from fm1companion import config
    import conftest
    home = Path(os.path.expanduser("~"))
    assert conftest.TEST_HOME in (home, *home.parents) and conftest.REAL_HOME != conftest.TEST_HOME
    for p in (config.CONFIG_DIR, config.DATA_DIR, config.CACHE_DIR, config.Settings().user_root):
        assert conftest.REAL_HOME not in (Path(p), *Path(p).parents) or conftest.TEST_HOME in Path(p).parents, p
