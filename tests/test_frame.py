# SPDX-License-Identifier: GPL-3.0-only
"""Frame and packaging: save/load settings without crashing, the same version everywhere, package lists,
.desktop Exec with spaces, .run help and no leftovers in /tmp."""
from __future__ import annotations

import importlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "packaging/installer"))

from fm1companion import __version__, diag  # noqa: E402


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """fresh config module with its own XDG folders"""
    for var, sub in (("XDG_CONFIG_HOME", "c"), ("XDG_DATA_HOME", "d"), ("XDG_CACHE_HOME", "k")):
        monkeypatch.setenv(var, str(tmp_path / sub))
    monkeypatch.delenv("FM1C_DEMO_ROOT", raising=False)
    import fm1companion.config as config
    config = importlib.reload(config)
    yield config
    importlib.reload(config)


def test_save_never_raises(cfg, tmp_path):
    (tmp_path / "c").write_text("ich bin eine Datei")             # ~/.config is a file
    got = []
    cfg.save_listeners.append(got.append)
    s = cfg.load()
    assert cfg.problems_text().startswith("Die Einstellungsdatei ist nicht lesbar"), cfg.problems_text()
    assert "beschädigt" not in cfg.problems_text()
    assert cfg.save(s) is False
    assert "Datei statt eines Ordners" in cfg.save_error and "beendest" in cfg.save_error
    assert got == [cfg.save_error]
    (tmp_path / "c").unlink()
    assert cfg.save(s) is True and cfg.save_error == ""
    assert (tmp_path / "c/fm1-companion/config.json").exists()
    assert not list((tmp_path / "c/fm1-companion").glob("*.tmp"))


def test_save_threads(cfg):
    import threading
    s = cfg.load()
    errs = []

    def run():
        for _ in range(30):
            if not cfg.save(s):
                errs.append(cfg.save_error)
    ts = [threading.Thread(target=run) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errs
    import json
    json.loads(cfg.CONFIG_FILE.read_text("utf-8"))


def test_versions_match():
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__)
    meta = (ROOT / "packaging/fm1-companion.metainfo.xml").read_text("utf-8")
    assert re.search(r'<release version="([^"]+)"', meta).group(1) == __version__, "metainfo: newest version"
    man = (ROOT / "packaging/fm1-companion.1").read_text("utf-8").splitlines()[0]
    assert f"Suite {__version__}" in man
    readme = (ROOT / "README.md").read_text("utf-8")
    for v in set(re.findall(r"FM-1-Companion-Suite-(\d+\.\d+\.\d+)\.run|fm1-companion_(\d+\.\d+\.\d+)_all", readme)):
        assert __version__ in v, f"README mentions {v}"


def test_deps_lists():
    req = diag.packages("required")
    for p in ("python3", "python3-pyqt6", "python3-pyqt6.qtsvg", "python3-pyqt6.qtwebengine",
              "python3-pyqt6.qtwebchannel", "python3-mido", "python3-rtmidi", "python3-numpy", "git",
              "qt6-translations-l10n"):                 # required: otherwise shortcuts say "Ctrl" instead of "Strg"
        assert p in req
    assert "qt6-translations-l10n" not in diag.packages("recommended")
    # everything the app lets you install via apt is in the one list (Depends/Recommends/Suggests)
    from fm1companion.flash import uboot
    allp = set(req) | set(diag.packages("recommended")) | set(diag.packages("suggested"))
    assert set(uboot.DEPS.values()) <= allp
    # the hint for missing PyQt6 comes from the same list
    from fm1companion import app
    assert app.missing_apt_hint().startswith("sudo apt install")


@pytest.mark.skipif(not shutil.which("dpkg-deb"), reason="dpkg-deb missing")
def test_deb_suggests(tmp_path):
    import build_deb
    deb = build_deb.build(tmp_path)
    info = subprocess.run(["dpkg-deb", "-f", str(deb)], capture_output=True, text=True, check=True).stdout
    assert "Suggests: python3-crcmod" in info
    assert "Depends: python3 (>= 3.11), python3-pyqt6, python3-pyqt6.qtsvg" in info
    files = subprocess.run(["dpkg-deb", "-c", str(deb)], capture_output=True, text=True, check=True).stdout
    assert "fm1companion/data/help/de/12-glossar.md" in files


def test_exec_quote():
    import core
    assert core.exec_quote("/home/user/.local/bin/fm1-companion") == "/home/user/.local/bin/fm1-companion"
    q = core.exec_quote("/home/Max Mustermann/.local/bin/fm1-companion")
    assert q == '"/home/Max Mustermann/.local/bin/fm1-companion"'
    assert core.exec_quote('/a "b"/$x') == '"/a \\\\"b\\\\"/\\\\$x"'


def test_launcher_with_space(tmp_path):
    """.run/install.sh installation into a path with spaces: launcher and start menu work."""
    import core
    home = tmp_path / "Max Müller"
    env_home = os.environ.get("HOME")
    os.environ["HOME"] = str(home)
    try:
        core.install(ROOT, home / ".local", menu=True, log=lambda *a: None)
    finally:
        os.environ["HOME"] = env_home or ""
    launcher = home / ".local/bin/fm1-companion"
    out = subprocess.run([str(launcher), "--version"], capture_output=True, text=True,
                         env=dict(os.environ, HOME=str(home)))
    assert out.stdout.strip() == __version__, out.stderr
    desk = (home / ".local/share/applications/fm1-companion.desktop").read_text("utf-8")
    assert f'Exec="{launcher}"' in desk and f'Exec="{launcher}" --demo' in desk


def test_run_help_and_no_leftovers(tmp_path):
    import build_run
    run = build_run.build(tmp_path)
    out = subprocess.run(["sh", str(run), "--help"], capture_output=True, text=True).stdout
    assert "PAYLOAD_SHA256" not in out and "--unattended" in out
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("XDG_")}
    env.update(HOME=str(home), TMPDIR=str(tmpdir), PATH="/usr/bin:/bin")
    res = subprocess.run(["sh", str(run), "--unattended", "--no-deps", "--prefix", str(home / ".local")],
                         env=env, capture_output=True, text=True)
    assert res.returncode == 0, res.stdout + res.stderr
    assert not list(tmpdir.iterdir()), "extracted installer stays behind in /tmp"
