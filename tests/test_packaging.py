# SPDX-License-Identifier: GPL-3.0-only
"""Packages: .deb (files, control, launcher), .run (extract, unattended install and removal),
install.sh/uninstall.sh – all in a fake HOME. The folder ~/FM-1 must stay. Also: the installers speak English,
German when the system language is German."""
from __future__ import annotations

import os
import re
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
    assert deb.name == "fm1-companion_2.6.2_all.deb"
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
              "./usr/share/man/man1/fm1-companion.1.gz", "./usr/share/doc/fm1-companion/copyright",
              "./usr/share/fm1-companion/udev/60-fm1-companion.rules",
              "./usr/lib/udev/rules.d/70-fm1-companion-download-mode.rules",
              "./usr/lib/modules-load.d/fm1-companion-sg.conf",
              "./usr/lib/fm1-companion/fm1companion/tools/manifest.json",
              "./usr/lib/fm1-companion/fm1companion/tools/bundled/m-bootloader/fm1-uboot.boot",
              "./usr/lib/fm1-companion/fm1companion/tools/bundled/sloop3/sloop3-3.4.0.area",
              "./usr/lib/fm1-companion/fm1companion/tools/bundled/m-flasher/LICENSE"):
        assert f in files, f
    # the power-saving rule stays inactive; only the download-mode rule is active (owner decision 2026-11-19)
    assert "./etc/udev" not in files and "./lib/udev" not in files
    assert [ln for ln in files.splitlines() if "/udev/rules.d/" in ln and ln.endswith(".rules")] == \
        [ln for ln in files.splitlines() if ln.endswith("/usr/lib/udev/rules.d/70-fm1-companion-download-mode.rules")]
    assert "__pycache__" not in files and "root/root" in files
    # the Studio twin: shared host, every bundled version, page, service and help
    lib = "./usr/lib/fm1-companion/fm1companion/"
    for f in ("twin-core.js", "twin-host.js", "studio-worker.js", "studio-worklet.js", "studio.html", "studio.js",
              "studio-panel.js"):
        assert lib + "sims/_studio/" + f in files, f
    from fm1companion import studio
    for fw in ("melodee", "skiff", "jiant", "sloop", "coralflower", "karma", "felucca", "fm1-open", "choralroot", "x0x",
               "fomni", "amb-1", "loom-8"):
        assert studio.builds(fw), fw
        for b in studio.builds(fw):
            for f in b.manifest["files"]:
                assert f"{lib}sims/{fw}-studio/{b.path.name}/{f}" in files, (b.version, f)
            assert f"{lib}sims/{fw}-studio/{b.path.name}/manifest.json" in files
    for b in studio.builds("choralroot"):                                            # (ChoralRoot FM-1's twin)
        for f in [*b.manifest["files"], "manifest.json"]:
            assert f"{lib}sims/choralroot-studio/{b.path.name}/{f}" in files, (b.version, f)
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
    assert out.stdout.strip() == "2.6.2"
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
    for fw in ("melodee", "skiff", "jiant", "sloop", "coralflower", "karma", "felucca", "fm1-open", "choralroot", "x0x",
               "fomni", "amb-1", "loom-8"):
        assert sorted(p.parent.name for p in (app / f"fm1companion/sims/{fw}-studio").glob("*/twin.wasm")) == \
            sorted(p.parent.name for p in (ROOT / f"fm1companion/sims/{fw}-studio").glob("*/twin.wasm")) != []
    assert "exec /usr/bin/python3 -m fm1companion" in launcher.read_text() or py in launcher.read_text()
    assert (home / ".local/share/applications/fm1-companion.desktop").exists()
    assert (home / ".local/share/applications/fm1-companion-uninstall.desktop").exists()
    assert (home / ".local/share/icons/hicolor/48x48/apps/fm1-companion.png").exists()
    assert len(list((home / "FM-1/Samples/Beispiele (Demo)").glob("*.wav"))) == 7
    out = subprocess.run([str(launcher), "--version"], env=env, capture_output=True, text=True)
    assert out.stdout.strip() == "2.6.2", out.stderr
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


# ------------------------------------------------------------------ quality pass after 2.6.0 (PKG-01 … PKG-08)
def test_run_is_reproducible(tmp_path):
    """PKG-01: same commit -> the same bytes, also when built at another time (no clock in the gzip header)."""
    import gzip
    import io
    import tarfile
    import time
    import build_run
    a = build_run.payload()
    real = time.time
    time.time = lambda: real() + 86400 * 400                       # "another day"
    try:
        b = build_run.payload()
    finally:
        time.time = real
    assert a == b
    assert a[4:8] == build_run.EPOCH.to_bytes(4, "little")         # gzip MTIME field is the fixed stamp
    tar = tarfile.open(fileobj=io.BytesIO(gzip.decompress(a)))
    members = tar.getmembers()
    names = [m.name for m in members]
    assert names == sorted(names) and len(names) == len(set(names)) and "app/fm1companion/__init__.py" in names
    assert all(m.mtime == build_run.EPOCH and m.uid == 0 and m.gid == 0 and m.uname == "root" and m.gname == "root"
               for m in members)
    assert all(m.mode == (0o755 if m.name.endswith(".sh") else 0o644) for m in members)
    r1, r2 = build_run.build(tmp_path / "a"), build_run.build(tmp_path / "b")
    assert r1.read_bytes() == r2.read_bytes()


def _git_clean_checkout() -> bool:
    try:
        return subprocess.run(["git", "ls-files", "--error-unmatch", "tools/build_common.py"], cwd=ROOT,
                              capture_output=True).returncode == 0
    except OSError:
        return False


@pytest.mark.skipif(not _git_clean_checkout(), reason="not a git checkout")
def test_packages_take_tracked_files_only(tmp_path):
    """PKG-3 (github-docs audit): a stray, untracked file in the checkout never ends up in a package."""
    import build_common
    import build_run
    strays = [ROOT / "fm1companion" / "zz_stray_audit_note.json", ROOT / "data" / "zz_stray.txt",
              ROOT / "packaging" / "installer" / "zz_stray.py"]
    try:
        for s in strays:
            s.write_text("stray")
        files = {p.relative_to(ROOT).as_posix() for p in build_common.app_files()}
        assert "fm1companion/zz_stray_audit_note.json" not in files and "fm1companion/__init__.py" in files
        import gzip
        import io
        import tarfile
        names = tarfile.open(fileobj=io.BytesIO(gzip.decompress(build_run.payload()))).getnames()
        assert not [n for n in names if "zz_stray" in n]
        assert "app/data/fm1-companion.desktop.in" in names and "app/packaging/installer/core.py" in names
        assert "app/packaging/udev/60-fm1-companion.rules" in names and "app/LICENSE" in names
        if shutil.which("dpkg-deb"):
            import build_deb
            deb = build_deb.build(tmp_path)
            listing = subprocess.run(["dpkg-deb", "-c", str(deb)], capture_output=True, text=True, check=True).stdout
            assert "zz_stray" not in listing
    finally:
        for s in strays:
            s.unlink(missing_ok=True)


@pytest.mark.skipif(not shutil.which("dpkg-deb"), reason="dpkg-deb missing")
def test_deb_reproducible_changelog_maintainer(tmp_path):
    """PKG-03/04 and the .deb twice: identical; file times of the checkout do not matter."""
    import gzip
    import build_common
    import build_deb
    d1 = build_deb.build(tmp_path / "a")
    probe = ROOT / "fm1companion" / "__init__.py"
    st = probe.stat()
    try:
        os.utime(probe, (1, 1))                                     # an old file time in the checkout
        d2 = build_deb.build(tmp_path / "b")
    finally:
        os.utime(probe, (st.st_atime, st.st_mtime))
    assert d1.read_bytes() == d2.read_bytes()
    ex = tmp_path / "x"
    subprocess.run(["dpkg-deb", "-x", str(d1), str(ex)], check=True)
    log = gzip.decompress((ex / "usr/share/doc/fm1-companion/changelog.gz").read_bytes()).decode()
    assert "{maintainer}" not in log and "{" not in log
    ver = build_common.version()
    assert log.startswith(f"fm1-companion ({ver}) stable; urgency=low\n\n  * ")
    # (the date is the release date of this version in the metainfo – it may be corrected on release day)
    assert log.rstrip("\n").endswith(" -- bit-wmike <295828+bit-wmike@users.noreply.github.com>  "
                                     + build_common.changelog_date(ver))
    assert re.fullmatch(r"(Mon|Tue|Wed|Thu|Fri|Sat|Sun), \d\d [A-Z][a-z]{2} \d{4} 12:00:00 \+0000",
                        build_common.changelog_date(ver))
    info = subprocess.run(["dpkg-deb", "-f", str(d1)], capture_output=True, text=True, check=True).stdout
    assert "Maintainer: bit-wmike <295828+bit-wmike@users.noreply.github.com>" in info and "localhost" not in info
    assert build_common.MAINTAINER == "bit-wmike <295828+bit-wmike@users.noreply.github.com>"
    cp = (ex / "usr/share/doc/fm1-companion/copyright").read_text()
    assert "Copyright: 2026 bit-wmike and the FM-1 Companion contributors" in cp
    sug = info.split("Suggests:")[1].split("\n")[0]
    for p in ("python3-serial", "pkexec | policykit-1", "pulseaudio-utils", "python3-tqdm"):       # PKG-07
        assert p in sug, p
    rec = info.split("Recommends:")[1].split("\n")[0]
    # the rescue in ROM boot mode must be ready before an emergency: what it imports is installed by default
    assert "audacity" in rec and "python3-crcmod" in rec and "python3-yaml" in rec and "python3-crcmod" not in sug


def test_metainfo_release_date_is_real():
    """PKG-2: 2.6.0 was merged to main on 2026-11-12 (commit 74e797f) – not a date in the future."""
    import build_common
    assert build_common.release_date("2.6.0") == "2026-11-12"
    assert build_common.changelog_date("2.6.0") == "Thu, 12 Nov 2026 12:00:00 +0000"
    with pytest.raises(SystemExit):
        build_common.release_date("0.0.1")


def test_deps_json_names_the_feature_of_every_suggested_package():
    import json
    d = json.loads((ROOT / "fm1companion/data/deps.json").read_text("utf-8"))
    assert all(x.get("why") for kind in ("required", "recommended", "suggested") for x in d[kind])
    from fm1companion import diag
    whys = {x["why"] for kind in ("required", "recommended", "suggested") for x in d[kind]}
    assert whys <= set(diag.DEP_WHY)                                # every text is marked for translation


def test_man_page_and_help_are_current():
    """PKG-05"""
    man = (ROOT / "packaging/fm1-companion.1").read_text()
    assert "currently German" not in man and "English, German and Spanish" in man
    assert "three simulated FM-1" in man and "two simulated" not in man
    assert "en, de or es" in man and "FM1C_LANG=en|de|es" in man
    env = {k: v for k, v in os.environ.items() if k not in ("FM1C_LANG", "LC_ALL", "LC_MESSAGES", "LANGUAGE")}
    env.update(LANG="C.UTF-8", PYTHONPATH=str(ROOT))
    out = subprocess.run([sys.executable, "-m", "fm1companion", "--help"], env=env, capture_output=True, text=True,
                         cwd=ROOT).stdout
    assert "en, de, es" in out and "three simulated FM-1" in out
    from fm1companion import i18n
    assert set(i18n.available_languages()) >= {"en", "de", "es"}


def test_check_explains_the_language_package(monkeypatch, capsys):
    """PKG-06: the Qt translation package is a hard dependency of the packages but not needed in English –
    `--check` says why it calls it optional."""
    from fm1companion import app, diag, i18n
    old = i18n.current_language()
    i18n.set_language("en")
    try:
        monkeypatch.setattr(diag, "check_deps", lambda: [diag.DepState("qt6-translations-l10n", False, False, "Qt dialogs"),
                                                        diag.DepState("ffmpeg", False, False, "MP3")])
        monkeypatch.setattr(diag, "midi_ports", lambda: ([], [], "x"))
        app.check()
        out = capsys.readouterr().out
    finally:
        i18n.set_language(old)
    assert "(optional) qt6-translations-l10n  (only needed when the user interface is not English: Qt dialogs)" in out
    assert "(optional) ffmpeg  (MP3)" in out


def test_system_install_is_readable_for_everyone(tmp_path):
    """PKG-02: installing for all users under umask 077 must not produce folders only root can enter."""
    sys.path.insert(0, str(ROOT / "packaging/installer"))
    import core
    src = tmp_path / "src"
    old = os.umask(0o077)
    try:
        (src / "fm1companion" / "ui").mkdir(parents=True)
        (src / "fm1companion" / "__init__.py").write_text('__version__ = "9.9.9"\n')
        (src / "fm1companion" / "ui" / "x.py").write_text("x = 1\n")
        (src / "packaging" / "installer").mkdir(parents=True)
        shutil.copy(ROOT / "packaging/installer/core.py", src / "packaging/installer/core.py")
        (src / "uninstall.sh").write_text("#!/bin/sh\n")
        (src / "data").mkdir()
        shutil.copy(ROOT / "data/fm1-companion.desktop.in", src / "data/fm1-companion.desktop.in")
        prefix = tmp_path / "sysprefix"
        core.install(src, prefix, system=True, log=lambda *_a: None, fluidsynth_fix=False)
    finally:
        os.umask(old)
    L = core.layout(prefix, True)
    bad = [(oct(p.stat().st_mode & 0o777), str(p.relative_to(prefix))) for p in [L["app"], *L["app"].rglob("*")]
           if (p.stat().st_mode & 0o777) not in ((0o755,) if p.is_dir() else (0o644, 0o755))]
    assert not bad, bad[:5]
    assert (L["app"] / "uninstall.sh").stat().st_mode & 0o777 == 0o755
    assert (L["app"] / "fm1companion" / "ui").stat().st_mode & 0o777 == 0o755
    for f in (L["bin"] / "fm1-companion", L["apps"] / "fm1-companion.desktop"):
        assert f.stat().st_mode & 0o044 == 0o044, f
    un = (L["apps"] / "fm1-companion-uninstall.desktop").read_text()
    assert "GenericName=Uninstaller" in un and "FM-1 manager" not in un                 # PKG-08
    assert "GenericName=FM-1 manager" in (L["apps"] / "fm1-companion.desktop").read_text()


@pytest.mark.parametrize("answer,removed", [("yes\n", True), ("ja\n", True), ("Y\n", True), ("j\n", True),
                                             ("JA \n", True), ("n\n", False), ("\n", False), ("", False),
                                             ("yep\n", False)])
def test_uninstall_all_confirmation(tmp_path, answer, removed):
    """PKG-08: yes/ja/y/j are accepted; no answer or end of input keeps settings and still removes the app."""
    home = tmp_path / "home"
    env = fake_env(home)
    res = subprocess.run(["bash", str(ROOT / "install.sh"), "--no-apt"], env=env, capture_output=True, text=True)
    assert (home / ".local/bin/fm1-companion").exists(), res.stdout + res.stderr
    cfg = home / ".config/fm1-companion"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "config.json").write_text("{}")
    res = subprocess.run(["bash", str(home / ".local/share/fm1-companion/app/uninstall.sh"), "--all"],
                         env=lang_env(env, "C.UTF-8"), input=answer, capture_output=True, text=True)
    assert res.returncode == 0, res.stdout + res.stderr
    assert not (home / ".local/bin/fm1-companion").exists()               # the app goes in every case
    assert (cfg / "config.json").exists() is (not removed), res.stdout
    assert ("Removing the app, settings and internal data." in res.stdout) is removed
    assert ("Settings and internal data are kept" in res.stdout) is (not removed)
    assert (home / "FM-1/Backups/wichtig.json").exists()


def test_install_sh_refuses_unknown_options(tmp_path):
    """HYG-6: './install.sh --no-atp' (a typo) went straight on to 'sudo apt-get'. An unknown option now stops the
    script before anything happens, in the user's language."""
    for lang, word in (("C.UTF-8", "Unknown option: --no-atp"), ("de_DE.UTF-8", "Unbekannte Option: --no-atp")):
        home = tmp_path / lang
        home.mkdir()
        env = lang_env(dict(os.environ, HOME=str(home), XDG_DATA_HOME=str(home / "d"),
                            XDG_CONFIG_HOME=str(home / "c")), lang)
        res = subprocess.run(["bash", str(ROOT / "install.sh"), "--no-atp"], env=env, capture_output=True, text=True)
        assert res.returncode == 2 and word in res.stderr and "--help" in res.stderr, res.stderr
        assert not res.stdout.strip() and list(home.iterdir()) == []          # nothing started, nothing written


def test_own_files_carry_the_licence_line_and_sane_modes():
    """HYG-2/3: every own script names its licence; a bundled twin is data, not a program."""
    for rel in ("install.sh", "uninstall.sh", "packaging/run-stub.sh", "fm1companion/web/webmidi_polyfill.js",
                "tests/mock_fm1.mjs", "tests/mock_fm1_shared.mjs", "tests/mock_sloop.mjs"):
        assert "SPDX-License-Identifier: GPL-3.0-only" in "\n".join((ROOT / rel).read_text("utf-8").splitlines()[:2]), rel
    modes = subprocess.run(["git", "ls-files", "-s"], cwd=ROOT, capture_output=True, text=True)
    if modes.returncode or not modes.stdout:
        pytest.skip("not a git checkout")
    for line in modes.stdout.splitlines():
        mode, path = line.split()[0], line.split("\t", 1)[1]
        if path.endswith((".wasm", ".png", ".json", ".md", ".po")):
            assert mode == "100644", path


def test_release_261_metadata():
    """2.6.1 is the first package since 2.4.0: one version everywhere (what the workflows check), the change list
    keeps 2.6.0 and 2.5.0 and says they were source only."""
    import re
    import build_common
    ver = build_common.version()
    assert (ROOT / "README.md").read_text("utf-8").splitlines()[0] == f"# FM-1 Companion Suite {ver}"
    assert f'"FM-1 Companion Suite {ver}"' in (ROOT / "packaging/fm1-companion.1").read_text("utf-8").splitlines()[0]
    for form in ("bug_report.yml", "hardware_test.yml"):
        assert f'placeholder: "{ver}"' in (ROOT / ".github/ISSUE_TEMPLATE" / form).read_text("utf-8"), form
    meta = (ROOT / "packaging/fm1-companion.metainfo.xml").read_text("utf-8")
    versions = re.findall(r'<release version="([^"]+)"', meta)
    assert versions[0] == ver                                       # the newest entry is this version
    older = [v for v in versions if v in ("2.6.1", "2.6.0", "2.5.0", "2.4.0")]
    assert older == ["2.6.1", "2.6.0", "2.5.0", "2.4.0"]            # nothing dropped from the change list
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", build_common.release_date(ver))
    for old in ("2.6.0", "2.5.0"):
        entry = meta.split(f'<release version="{old}"')[1].split("</release>")[0]
        assert "Source code only" in entry, old
    # the README never names a package file of a fixed version (it was wrong for two releases)
    readme = (ROOT / "README.md").read_text("utf-8")
    assert not re.search(r"fm1-companion_\d+\.\d+\.\d+_all\.deb|FM-1-Companion-Suite-\d+\.\d+\.\d+\.run", readme)
    assert "docs/UNTESTED.md" in readme and "three simulated FM-1" in readme and "git clone --depth 1" in readme


# ------------------------------------------------------------ bundled tools: download-mode rule, M-Flasher binaries

def test_download_mode_rule_is_uaccess_for_4c4a_8057_only():
    rule = (ROOT / "packaging/udev/70-fm1-companion-download-mode.rules").read_text("utf-8")
    lines = [ln for ln in rule.splitlines() if ln.strip() and not ln.startswith("#")]
    assert lines == ['SUBSYSTEM=="scsi_generic", ATTRS{idVendor}=="4c4a", ATTRS{idProduct}=="8057", TAG+="uaccess"',
                     'SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTR{idVendor}=="4c4a", ATTR{idProduct}=="8057", '
                     'TAG+="uaccess"']                    # (review L4: the usb line only for the device itself)
    upstream = (ROOT / "fm1companion/tools/bundled/m-flasher/70-m-flasher.rules").read_text("utf-8")
    assert lines[0] in upstream.splitlines()                         # the sg line is M-Flasher's own
    assert "MODE=" not in rule and "RUN+=" not in rule and "GROUP=" not in rule
    # (70 < 73: uaccess is applied by systemd's 73-seat-late.rules)
    assert [ln.strip() for ln in (ROOT / "packaging/udev/fm1-companion-sg.conf").read_text().splitlines()
            if ln.strip() and not ln.startswith("#")] == ["sg"]
    # the power rule keeps its own name and never touches download mode
    assert "8057" not in (ROOT / "packaging/udev/60-fm1-companion.rules").read_text()


def _core(monkeypatch, tmp_path):
    sys.path.insert(0, str(ROOT / "packaging/installer"))
    import core
    monkeypatch.setattr(core, "UDEV_DIR", tmp_path / "etc/udev/rules.d")
    monkeypatch.setattr(core, "MODULES_DIR", tmp_path / "etc/modules-load.d")
    monkeypatch.setattr(core, "DL_ACTIVATE", "true")
    monkeypatch.setattr(core, "DL_RELOAD", "true")
    monkeypatch.setattr(core, "_sudo", lambda: [])
    return core


def test_install_sh_and_uninstall_install_and_remove_the_download_rule(tmp_path, monkeypatch):
    core = _core(monkeypatch, tmp_path)
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    for k in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME"):
        monkeypatch.delenv(k, raising=False)
    msgs = []
    info = core.install(ROOT, home / ".local", menu=False, download_mode_rule=True, log=msgs.append,
                        fluidsynth_fix=False)
    rule = core.UDEV_DIR / core.DL_RULE
    assert rule.read_text() == (ROOT / "packaging/udev" / core.DL_RULE).read_text()
    assert (core.MODULES_DIR / core.SG_CONF).read_text().strip().endswith("sg")
    assert info["download_mode_rule"] == sorted([str(rule), str(core.MODULES_DIR / core.SG_CONF)])
    assert any(str(rule) in m for m in msgs)
    app = Path(info["app"])
    assert (app / "udev" / core.DL_RULE).exists() and (app / "udev" / core.SG_CONF).exists()
    # an update without the flag keeps knowing that the rule is ours
    assert core.install(ROOT, home / ".local", menu=False, log=lambda *_: None,
                        fluidsynth_fix=False)["download_mode_rule"] == info["download_mode_rule"]
    core.uninstall(home / ".local", log=msgs.append)
    assert not rule.exists() and not (core.MODULES_DIR / core.SG_CONF).exists()


def test_without_the_flag_nothing_is_installed_as_root(tmp_path, monkeypatch):
    core = _core(monkeypatch, tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "h"))
    info = core.install(ROOT, tmp_path / "h/.local", menu=False, log=lambda *_: None, fluidsynth_fix=False)
    assert "download_mode_rule" not in info and not (core.UDEV_DIR / core.DL_RULE).exists()
    core.uninstall(tmp_path / "h/.local", log=lambda *_: None)          # (and nothing to take out)


def test_install_sh_offers_the_download_rule_only_interactively(tmp_path):
    text = (ROOT / "install.sh").read_text("utf-8")
    assert "--download-mode-rule" in text and "--no-download-mode-rule" in text
    home = tmp_path / "home"
    env = fake_env(home)
    res = subprocess.run(["bash", str(ROOT / "install.sh"), "--no-apt", "--no-usb-power-rule"], env=env,
                         capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "/etc/udev/rules.d/70-fm1-companion-download-mode.rules" not in res.stdout   # no terminal: no sudo
    info = __import__("json").loads((home / ".local/share/fm1-companion/app/install-info.json").read_text())
    assert "download_mode_rule" not in info


def _tool_tree(tmp_path, binaries: dict[str, bytes], arch="x86_64"):
    """a source tree with a manifest pinning `binaries` (relpath -> content)"""
    import copy
    import hashlib
    import json
    src = tmp_path / "src"
    shutil.copytree(ROOT / "fm1companion" / "tools", src / "fm1companion" / "tools")
    data = json.loads((src / "fm1companion/tools/manifest.json").read_text())
    t = data["tools"]["m-flasher"]
    for a in list(t["arch"]):
        rel = f"m-flasher/{t['version']}/{a}/m-flasher"
        b = binaries.get(rel, b"")
        t["arch"][a] = dict(copy.deepcopy(t["arch"][a]), binary_sha256=hashlib.sha256(b).hexdigest(),
                            binary_size=len(b))
    (src / "fm1companion/tools/manifest.json").write_text(json.dumps(data))
    for rel, b in binaries.items():
        (src / "tools-bin" / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / "tools-bin" / rel).write_bytes(b)
    return src, t["version"]


def test_installer_copies_only_this_computers_m_flasher_after_the_checksum(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "packaging/installer"))
    import core
    ver = __import__("json").loads((ROOT / "fm1companion/tools/manifest.json").read_text())["tools"]["m-flasher"]["version"]
    bins = {f"m-flasher/{ver}/x86_64/m-flasher": b"x86 binary", f"m-flasher/{ver}/aarch64/m-flasher": b"arm binary"}
    src, _ = _tool_tree(tmp_path, bins)
    app = tmp_path / "app"
    assert core.install_tool_binaries(src, app, log=lambda *_: None, machine="aarch64") == \
        [f"m-flasher/{ver}/aarch64/m-flasher"]
    f = app / "tools-bin" / f"m-flasher/{ver}/aarch64/m-flasher"
    assert f.read_bytes() == b"arm binary" and os.access(f, os.X_OK)
    assert (app / "tools-bin/m-flasher/THIRD-PARTY-LICENSES.txt").is_file()
    assert not (app / "tools-bin" / f"m-flasher/{ver}/x86_64").exists()
    msgs = []
    assert core.install_tool_binaries(src, app, log=msgs.append, machine="armv7l") == []   # 32-bit Pi OS
    assert "M-Flasher" in msgs[-1] and not (app / "tools-bin").exists()
    (src / "tools-bin" / f"m-flasher/{ver}/x86_64/m-flasher").write_bytes(b"changed")
    assert core.install_tool_binaries(src, app, log=msgs.append, machine="x86_64") == []
    assert f"m-flasher/{ver}/x86_64/m-flasher" in msgs[-1]


@pytest.mark.skipif(not shutil.which("dpkg-deb"), reason="dpkg-deb missing")
def test_deb_with_bundled_m_flasher_stays_architecture_all(tmp_path, monkeypatch):
    import hashlib
    import build_deb
    ver = __import__("json").loads((ROOT / "fm1companion/tools/manifest.json").read_text())["tools"]["m-flasher"]["version"]
    fakes = []
    for a in ("aarch64", "x86_64"):
        f = tmp_path / a
        f.write_bytes(f"binary for {a}".encode())
        fakes.append((f"m-flasher/{ver}/{a}/m-flasher", f))
    monkeypatch.setattr(build_deb, "tool_binaries", lambda require=False: fakes)
    deb = build_deb.build(tmp_path / "dist")
    info = subprocess.run(["dpkg-deb", "-f", str(deb), "Architecture"], capture_output=True, text=True).stdout
    assert info.strip() == "all"
    listing = subprocess.run(["dpkg-deb", "-c", str(deb)], capture_output=True, text=True, check=True).stdout
    for rel, _f in fakes:
        line = next(ln for ln in listing.splitlines() if ln.endswith("/usr/lib/fm1-companion/tools-bin/" + rel))
        assert line.startswith("-rwxr-xr-x"), line
    x = tmp_path / "x"
    subprocess.run(["dpkg-deb", "-R", str(deb), str(x)], check=True)
    sums = (x / "usr/lib/fm1-companion/tools-bin/SHA256SUMS").read_text()
    for rel, f in fakes:
        assert f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {rel}" in sums
    for name in ("LICENSE", "NOTICE.md", "THIRD-PARTY-LICENSES.txt"):     # the licences travel with the binary
        assert (x / "usr/lib/fm1-companion/tools-bin/m-flasher" / name).is_file(), name
    post = (x / "DEBIAN/postinst").read_text()
    assert "sha256sum --quiet -c SHA256SUMS" in post and "modprobe sg" in post and 'DPKG_ROOT' in post
    assert (x / "DEBIAN/postrm").exists()
    cr = (x / "usr/share/doc/fm1-companion/copyright").read_text()
    assert "M-Flasher" in cr and "m-bootloader" in cr and "SLOOP 3" in cr and "OFL-1.1" in cr


def test_run_payload_carries_both_binaries(tmp_path, monkeypatch):
    import io
    import tarfile
    import build_run
    f = ROOT / "tools/fetch_tools.py"                      # any tracked file stands in for a binary
    monkeypatch.setattr(build_run, "tool_binaries", lambda require=False: [("m-flasher/x/aarch64/m-flasher", f)])
    with tarfile.open(fileobj=io.BytesIO(build_run.payload()), mode="r:gz") as tar:
        names = tar.getnames()
    assert "app/tools/fetch_tools.py" in names and "app/packaging/udev/70-fm1-companion-download-mode.rules" in names


def test_release_workflow_fetches_and_requires_the_tools():
    wf = (ROOT / ".github/workflows/release.yml").read_text("utf-8")
    assert "python3 tools/fetch_tools.py" in wf and "--require-tools" in wf
    assert wf.index("tools/fetch_tools.py") < wf.index("tools/build_deb.py")


def test_uninstall_removes_only_the_rule_files_this_installation_added(tmp_path, monkeypatch):
    """review L2: a rule that was there before (system-wide install, by hand) stays after uninstalling"""
    core = _core(monkeypatch, tmp_path)
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    for k in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME"):
        monkeypatch.delenv(k, raising=False)
    core.UDEV_DIR.mkdir(parents=True)
    (core.UDEV_DIR / core.DL_RULE).write_text("# put there by someone else\n")
    info = core.install(ROOT, home / ".local", menu=False, download_mode_rule=True, log=lambda *_: None,
                        fluidsynth_fix=False)
    assert info["download_mode_rule"] == [str(core.MODULES_DIR / core.SG_CONF)]     # only what it added
    core.uninstall(home / ".local", log=lambda *_: None)
    assert (core.UDEV_DIR / core.DL_RULE).read_text() == "# put there by someone else\n"
    assert not (core.MODULES_DIR / core.SG_CONF).exists()
    # both already there: the installation owns nothing and removes nothing
    (core.MODULES_DIR / core.SG_CONF).write_text("sg\n")
    info = core.install(ROOT, home / ".local", menu=False, download_mode_rule=True, log=lambda *_: None,
                        fluidsynth_fix=False)
    assert "download_mode_rule" not in info
    core.uninstall(home / ".local", log=lambda *_: None)
    assert (core.UDEV_DIR / core.DL_RULE).exists() and (core.MODULES_DIR / core.SG_CONF).exists()


def test_udev_trigger_only_for_the_fm1_in_download_mode():
    """review L3: no `--action=add` replay for every scsi_generic device or every 4c4a device"""
    sys.path.insert(0, str(ROOT / "packaging/installer"))
    import build_deb
    import core
    for script in (core.DL_ACTIVATE, build_deb.POSTINST):
        triggers = [ln for ln in script.replace(";", "\n").splitlines() if "udevadm trigger" in ln]
        assert triggers and all("--parent-match" in ln and "--action=change" in ln for ln in triggers), triggers
        assert "8057" in script and "--subsystem-match=scsi_generic" not in script


def test_no_m_flasher_without_its_licences(tmp_path, monkeypatch):
    """review M4: the packages never carry the binaries without THIRD-PARTY-LICENSES.txt"""
    import build_common
    lic = ROOT / "fm1companion/tools/bundled/m-flasher/THIRD-PARTY-LICENSES.txt"
    text = lic.read_text("utf-8")
    for needle in ("epaint_default_fonts", "UFL.txt", "Hack-Regular.txt", "Bitstream Vera",
                   "emoji-icon-font-mit-license.txt", "Apache License", "Permission is hereby granted"):
        assert needle in text, needle
    import json
    lock_names = json.loads((ROOT / "fm1companion/tools/manifest.json").read_text())["tools"]["m-flasher"]
    assert lock_names["cargo_lock_sha256"] and "THIRD-PARTY-LICENSES.txt" in lock_names["licence_files"]
    monkeypatch.setattr(build_common, "ROOT", tmp_path)            # (a tree without the licence file)
    import fetch_tools
    monkeypatch.setattr(fetch_tools, "verify_tree", lambda *a, **k: ({"m-flasher/x/x86_64/m-flasher": lic}, []))
    assert build_common.tool_binaries() == []
    with pytest.raises(SystemExit):
        build_common.tool_binaries(require=True)
