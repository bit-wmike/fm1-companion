# SPDX-License-Identifier: GPL-3.0-only
"""Packages: .deb (files, control, launcher), .run (extract, unattended install and removal),
install.sh/uninstall.sh – all in a fake HOME. The folder ~/FM-1 must stay. Also: the installers speak English,
German when the system language is German."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="module")
def dist(tmp_path_factory):
    return tmp_path_factory.mktemp("dist")


def fake_env(home: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("XDG_")}
    env.update(HOME=str(home), PATH="/usr/bin:/bin", LANG="C.UTF-8", FM1C_NO_PKILL="1")
    (home / "FM-1" / "Backups").mkdir(parents=True, exist_ok=True)
    (home / "FM-1" / "Backups" / "wichtig.json").write_text("{}")
    return env


def lang_env(env: dict, lang: str) -> dict:
    """env with only LANG deciding the language (no FM1C_LANG from conftest, no LC_*)"""
    e = {k: v for k, v in env.items() if k not in ("FM1C_LANG", "LC_ALL", "LC_MESSAGES", "LANGUAGE")}
    e["LANG"] = lang
    return e


@pytest.mark.skipif(not shutil.which("dpkg-deb"), reason="dpkg-deb missing")
def test_deb(dist):
    import build_deb
    deb = build_deb.build(dist)
    assert deb.name == "fm1-companion_2.5.0_all.deb"
    info = subprocess.run(["dpkg-deb", "-f", str(deb)], capture_output=True, text=True, check=True).stdout
    assert "Package: fm1-companion" in info and "Architecture: all" in info
    for dep in ("python3 (>= 3.11)", "python3-pyqt6", "python3-pyqt6.qtwebengine", "python3-mido", "python3-rtmidi",
                "python3-numpy", "git"):
        assert dep in info.split("Depends:")[1].split("\n")[0]
    assert "fluid-soundfont-gm" in info and "audacity" in info.split("Recommends:")[1].split("\n")[0]
    files = subprocess.run(["dpkg-deb", "-c", str(deb)], capture_output=True, text=True, check=True).stdout
    for f in ("./usr/bin/fm1-companion", "./usr/lib/fm1-companion/fm1companion/__main__.py",
              "./usr/lib/fm1-companion/fm1companion/data/deps.json", "./usr/lib/fm1-companion/fm1companion/icons/lucide/save.svg",
              "./usr/share/applications/fm1-companion.desktop", "./usr/share/metainfo/fm1-companion.metainfo.xml",
              "./usr/share/icons/hicolor/512x512/apps/fm1-companion.png", "./usr/share/icons/hicolor/scalable/apps/fm1-companion.svg",
              "./usr/share/man/man1/fm1-companion.1.gz", "./usr/share/doc/fm1-companion/copyright"):
        assert f in files, f
    assert "__pycache__" not in files and "root/root" in files
    # the Studio twin: shared host, every bundled version, page, service and help
    lib = "./usr/lib/fm1-companion/fm1companion/"
    for f in ("twin-core.js", "twin-host.js", "studio-worker.js", "studio-worklet.js", "studio.html", "studio.js",
              "studio-panel.js"):
        assert lib + "sims/_studio/" + f in files, f
    from fm1companion import studio
    for fw in ("melodee", "skiff", "jiant", "sloop", "coralflower", "karma") + ("fm1-open",):
        assert studio.builds(fw), fw
        for b in studio.builds(fw):
            for f in b.manifest["files"]:
                assert f"{lib}sims/{fw}-studio/{b.path.name}/{f}" in files, (b.version, f)
            assert f"{lib}sims/{fw}-studio/{b.path.name}/manifest.json" in files
    for f in ("studio.py", "studio_web.py", "ui/studio_service.py", "ui/pages/studio.py", "data/help/de/13-studio.md"):
        assert lib + f in files, f
    # translations (.po, read directly – no .mo build step) and the help pages per language
    for f in ("i18n.py", "locale/messages.pot", "locale/de/frame.po", "data/help/de/01-erste-schritte.md"):
        assert lib + f in files, f
    for po in (ROOT / "fm1companion/locale").rglob("*.po"):
        assert lib + po.relative_to(ROOT / "fm1companion").as_posix() in files, po
    x = dist / "x"
    subprocess.run(["dpkg-deb", "-x", str(deb), str(x)], check=True)
    launcher = (x / "usr/bin/fm1-companion").read_text()
    assert "exec /usr/bin/python3 -m fm1companion" in launcher and "PYTHONPATH=/usr/lib/fm1-companion" in launcher
    assert os.access(x / "usr/bin/fm1-companion", os.X_OK)
    out = subprocess.run([sys.executable, "-m", "fm1companion", "--version"], cwd=x,
                         env=dict(os.environ, PYTHONPATH=str(x / "usr/lib/fm1-companion")), capture_output=True, text=True)
    assert out.stdout.strip() == "2.5.0"
    import xml.etree.ElementTree as ET
    meta = ET.parse(x / "usr/share/metainfo/fm1-companion.metainfo.xml").getroot()
    assert meta.findtext("id") == "fm1-companion" and meta.find("launchable").text == "fm1-companion.desktop"


@pytest.mark.skipif(not shutil.which("dpkg") or os.geteuid() != 0, reason="dpkg/root missing")
def test_deb_dpkg_root(dist, tmp_path):
    import build_deb
    deb = build_deb.build(dist)
    r = tmp_path / "root"
    (r / "var/lib/dpkg/info").mkdir(parents=True)
    (r / "var/lib/dpkg/updates").mkdir()
    (r / "var/lib/dpkg/status").touch()
    base = ["dpkg", f"--root={r}", "--force-script-chrootless", "--force-not-root"]
    subprocess.run(base + ["--force-depends", "-i", str(deb)], check=True, capture_output=True)
    assert list((r / "usr/lib/fm1-companion").rglob("__pycache__")), "postinst compiles bytecode"
    subprocess.run(base + ["-r", "fm1-companion"], check=True, capture_output=True)
    assert not (r / "usr/lib/fm1-companion").exists() and not (r / "usr/bin/fm1-companion").exists()


def test_run_installer(dist, tmp_path):
    import build_run
    run = build_run.build(dist)
    assert run.read_bytes().startswith(b"#!/bin/sh")
    home = tmp_path / "home"
    env = fake_env(home)
    assert subprocess.run(["sh", str(run), "--check"], env=env, capture_output=True, text=True).returncode == 0
    ex = tmp_path / "ex"
    subprocess.run(["sh", str(run), "--extract", str(ex)], env=env, check=True, capture_output=True)
    assert (ex / "app/fm1companion/__init__.py").exists() and (ex / "app/packaging/installer/gui.py").exists()
    py = "/usr/bin/python3" if Path("/usr/bin/python3").exists() else sys.executable
    res = subprocess.run(["sh", str(run), "--unattended", "--no-deps", "--prefix", str(home / ".local"), "--desktop",
                          "--demo-data"], env=env, capture_output=True, text=True)
    assert res.returncode == 0, res.stdout + res.stderr
    app = home / ".local/share/fm1-companion/app"
    launcher = home / ".local/bin/fm1-companion"
    assert (app / "fm1companion/__main__.py").exists() and (app / "install-info.json").exists()
    assert (app / "fm1companion/sims/_studio/studio-panel.js").exists()               # Studio twin in the .run
    for po in (ROOT / "fm1companion/locale").rglob("*.po"):                          # translations and help per language
        assert (app / po.relative_to(ROOT).as_posix()).exists(), po
    assert (app / "fm1companion/locale/messages.pot").exists()
    assert (app / "fm1companion/data/help/de/01-erste-schritte.md").exists()
    for fw in ("melodee", "skiff", "jiant", "sloop", "coralflower", "karma", "fm1-open"):
        assert sorted(p.parent.name for p in (app / f"fm1companion/sims/{fw}-studio").glob("*/twin.wasm")) == \
            sorted(p.parent.name for p in (ROOT / f"fm1companion/sims/{fw}-studio").glob("*/twin.wasm")) != []
    assert "exec /usr/bin/python3 -m fm1companion" in launcher.read_text() or py in launcher.read_text()
    assert (home / ".local/share/applications/fm1-companion.desktop").exists()
    assert (home / ".local/share/applications/fm1-companion-uninstall.desktop").exists()
    assert (home / ".local/share/icons/hicolor/48x48/apps/fm1-companion.png").exists()
    assert len(list((home / "FM-1/Samples/Beispiele (Demo)").glob("*.wav"))) == 7
    out = subprocess.run([str(launcher), "--version"], env=env, capture_output=True, text=True)
    assert out.stdout.strip() == "2.5.0", out.stderr
    # update: settings are kept
    cfg = home / ".config/fm1-companion"
    cfg.mkdir(parents=True)
    (cfg / "config.json").write_text('{"theme": "dark"}')
    subprocess.run(["sh", str(run), "--unattended", "--no-deps", "--prefix", str(home / ".local")], env=env, check=True,
                   capture_output=True)
    assert (cfg / "config.json").read_text() == '{"theme": "dark"}'
    # uninstall via the installed command, with settings and data
    res = subprocess.run([str(home / ".local/bin/fm1-companion-uninstall"), "--unattended", "--settings", "--data"],
                         env=env, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    assert not app.exists() and not launcher.exists() and not cfg.exists()
    assert not (home / ".local/share/applications/fm1-companion.desktop").exists()
    assert (home / "FM-1/Backups/wichtig.json").exists(), "~/FM-1 must stay"
    assert (home / "FM-1/Samples/Beispiele (Demo)").exists()
    # --help of the .run in both languages
    for lang, text in (("de_DE.UTF-8", "selbstentpackender Installer"), ("C.UTF-8", "self-extracting installer")):
        out = subprocess.run(["sh", str(run), "--help"], env=lang_env(env, lang), capture_output=True, text=True).stdout
        assert text in out and "--unattended" in out, (lang, out)


def test_install_sh(tmp_path):
    home = tmp_path / "home"
    env = fake_env(home)
    res = subprocess.run(["bash", str(ROOT / "install.sh"), "--no-apt"], env=env, capture_output=True, text=True)
    assert (home / ".local/bin/fm1-companion").exists(), res.stdout + res.stderr
    assert (home / ".local/share/fm1-companion/app/fm1companion/ui/theme.py").exists()
    assert (home / ".local/share/applications/fm1-companion.desktop").exists()
    assert "/usr/bin/python3" in (home / ".local/bin/fm1-companion").read_text() or not Path("/usr/bin/python3").exists()
    res = subprocess.run(["bash", str(home / ".local/share/fm1-companion/app/uninstall.sh"), "--all", "--yes"],
                         env=env, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    assert not (home / ".local/bin/fm1-companion").exists() and not (home / ".local/share/fm1-companion").exists()
    assert (home / "FM-1/Backups/wichtig.json").exists()


def test_never_deletes_user_root(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "packaging/installer"))
    import core
    home = tmp_path / "h"
    monkeypatch.setenv("HOME", str(home))
    for k in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME"):
        monkeypatch.delenv(k, raising=False)
    data = home / ".local/share/fm1-companion"
    (data / "meinFM1").mkdir(parents=True)
    (home / ".config/fm1-companion").mkdir(parents=True)
    (home / ".config/fm1-companion/config.json").write_text('{"user_root": "%s"}' % (data / "meinFM1"))
    core.uninstall(home / ".local", settings=True, data=True, log=lambda *_: None)
    assert (data / "meinFM1").exists(), "main folder inside the app data stays"


# ------------------------------------------------------------ installer language (English, German on German systems)

def test_installer_language_rule():
    sys.path.insert(0, str(ROOT / "packaging/installer"))
    import core
    assert core.language({"LANG": "de_DE.UTF-8"}) == "de" and core.language({"LANG": "C"}) == "en"
    assert core.language({}) == "en" and core.language({"LANG": "en_GB.UTF-8"}) == "en"
    assert core.language({"LC_ALL": "C.UTF-8", "LANG": "de_DE.UTF-8"}) == "en"          # LC_ALL wins
    assert core.language({"LC_MESSAGES": "de_AT.UTF-8", "LANG": "C"}) == "de"
    assert core.language({"LC_ALL": "", "LC_MESSAGES": "", "LANG": "de_CH"}) == "de"    # empty ones do not count
    assert core.language({"FM1C_LANG": "en", "LANG": "de_DE.UTF-8"}) == "en"            # the app's override
    assert core.language({"FM1C_LANG": "system", "LANG": "de_DE.UTF-8"}) == "de"


@pytest.mark.parametrize("lang,de", [("de_DE.UTF-8", True), ("C", False)])
def test_shell_scripts_bilingual(tmp_path, lang, de):
    env = lang_env(fake_env(tmp_path / "home"), lang)
    out = subprocess.run(["bash", str(ROOT / "install.sh"), "--help"], env=env, capture_output=True, text=True)
    assert out.returncode == 0 and "--no-apt" in out.stdout
    assert ("Systempakete prüfen" in out.stdout) == de and ("check/install system packages" in out.stdout) != de
    assert not (tmp_path / "home/.local").exists(), "--help installs nothing"
    out = subprocess.run(["bash", str(ROOT / "uninstall.sh"), "--help"], env=env, capture_output=True, text=True)
    assert out.returncode == 0 and "--all" in out.stdout
    assert ("wird nie gelöscht" in out.stdout) == de and ("is never deleted" in out.stdout) != de
    # the risk notice in install.sh: both languages, same content (docs/UNTESTED.md, GPL sections 15 and 16)
    sh = (ROOT / "install.sh").read_text("utf-8")
    assert "inoffizielles Community-Projekt" in sh and "unofficial community project" in sh
    assert "Abschnitte 15 und 16" in sh and "sections 15 and 16" in sh and sh.count("docs/UNTESTED.md") == 2
    # the core's messages (here: uninstalling from an empty prefix)
    res = subprocess.run([sys.executable, str(ROOT / "packaging/installer/core.py"), "uninstall", "--prefix",
                          str(tmp_path / "prefix")], env=env, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    assert ("bleibt unangetastet" in res.stdout) == de and ("is left untouched" in res.stdout) != de


def test_gui_risk_notice_both_languages(monkeypatch):
    """the graphical installer shows the app's own risk notice: English source text, German from the catalog"""
    sys.path.insert(0, str(ROOT / "packaging/installer"))
    import gui
    from fm1companion import i18n
    en = gui.risk_lines(ROOT, "en")
    de = gui.risk_lines(ROOT, "de")
    assert en == gui._risk_source_lines(ROOT) and len(en) == len(de) == 5
    cat = i18n.Catalog("de")
    assert de == [cat.get(None, x) for x in en]
    assert any("docs/UNTESTED.md" in x for x in en) and any("docs/UNTESTED.md" in x for x in de)
    assert "sections 15 and 16" in en[-1] and "Abschnitte 15 und 16" in de[-1]
    assert gui.risk_lines(Path("/nonexistent"), "de") == gui.RISK_FALLBACK
    assert gui.risk_lines(Path("/nonexistent"), "en") == gui.RISK_FALLBACK_EN
    monkeypatch.setenv("FM1C_LANG", "en")
    assert gui.risk_title() == "Important notice" and gui.understood_text() == "I understand"
    monkeypatch.setenv("FM1C_LANG", "de")
    assert gui.risk_title() == "Wichtiger Hinweis" and gui.understood_text() == "Ich habe verstanden"
