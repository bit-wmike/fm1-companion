# SPDX-License-Identifier: GPL-3.0-only
"""Regressionstests der QA-Befunde (Installer, Geräte, Einstellungen, Rettung, Daten) – ohne Qt.

  python3 -m pytest tests/test_safety.py -q
"""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    for k, sub in (("XDG_CONFIG_HOME", "c"), ("XDG_DATA_HOME", "d"), ("XDG_CACHE_HOME", "k")):
        monkeypatch.setenv(k, str(tmp_path / sub))
    monkeypatch.delenv("FM1C_DEMO", raising=False)
    monkeypatch.delenv("FM1C_DEMO_ROOT", raising=False)
    from fm1companion import config
    importlib.reload(config)
    config.load().user_root = str(tmp_path / "FM-1")
    from fm1companion.flash import engine, journal, sim
    for m in (journal, engine):
        importlib.reload(m)
    from fm1companion.vendor import fm1_install as fi
    old = dict(fi.DELAY)
    fi.DELAY.update(sim.fast_delays())
    yield tmp_path
    fi.DELAY.clear()
    fi.DELAY.update(old)


# ------------------------------------------------------------------ Plan

def _state(**kw):
    from fm1companion.flash import planner
    d = dict(identity="FM-1_91060", felucca=True, firmware_key="melodee", version="Melodee 1.6.0", ports=1)
    d.update(kw)
    return planner.DeviceState(**d)


def _spec(key):
    from fm1companion import firmware
    return firmware.spec(key)


def test_plan_backup_unticked_needs_confirmation(env):
    from fm1companion.flash import planner
    t = planner.Target.from_spec(_spec("melodee"))
    p = planner.plan_install(_state(), t, backup=True)
    assert "backup" in p.kinds() and "no_backup" not in p.confirmations
    p = planner.plan_install(_state(), t, backup=False)
    assert "backup" not in p.kinds() and "no_backup" in p.confirmations
    # generische Firmware: kein Backup-Schritt, sondern ein ehrlicher Hinweis
    p = planner.plan_install(_state(felucca=False, identity="FM-1_900", firmware_key=""), t)
    assert "backup" not in p.kinds() and "no_backup" in p.confirmations
    assert any("Kein Backup möglich" in w for w in p.warnings)


def test_plan_multiple_devices_unpinned_is_blocker(env):
    from fm1companion.flash import planner
    t = planner.Target.from_spec(_spec("melodee"))
    p = planner.plan_install(_state(ports=2, pinned=False), t)
    assert not p.ok and any(planner.MULTI_DEVICE_TEXT in b for b in p.blockers)
    p = planner.plan_install(_state(ports=2, pinned=True), t)
    assert p.ok


def test_plan_no_checksum_and_v16(env):
    from fm1companion.flash import planner
    t = planner.Target.from_spec(_spec("sloop"))
    t.checksum = False
    p = planner.plan_install(_state(), t)
    assert "no_checksum" in p.confirmations and "Prüfsumme nicht verfügbar" in planner.CONFIRM_TEXT["no_checksum"]
    p = planner.plan_install(_state(), planner.Target.from_spec(_spec("stock-v16")))
    assert "v16" in p.confirmations and any(planner.V16_WARNING in w for w in p.warnings)
    p = planner.plan_install(_state(), planner.Target.from_spec(_spec("stock-v15")))
    assert "v16" not in p.confirmations


def test_unknown_firmware_counts_as_switch(env):
    from fm1companion.flash import planner
    st = _state(felucca=False, identity="FM-1_900", firmware_key="")
    assert planner.v15_option(st, planner.Target.from_spec(_spec("sloop"))) == "default_on"


def test_catalog_spec_keeps_loader_family(env):
    from fm1companion import catalog
    cat = catalog.load_snapshot()
    e = cat.by_key("orbit")
    sp = e.to_spec()
    assert sp.loader_family == e.latest.loader_family == "sloopG3"
    from fm1companion.flash import planner
    assert planner.Target.from_spec(sp).family == "felucca"
    # eingebaute Firmware über die Prüfsumme: genau dieses Paket wird festgehalten
    s2 = catalog.spec_by_sha256(cat.by_key("sloop").latest.sha256, cat)
    assert s2.key == "sloop" and s2.sha256 == cat.by_key("sloop").latest.sha256 and s2.loader_family
    keys = [x.key for x in catalog.by_product("FM-1_900", cat)]
    assert keys[0] == "sloop" and "orbit" in keys and len(keys) > 2


# ------------------------------------------------------------ Engine: zwei FM-1

def test_two_devices_pinned_install_touches_only_target(env):
    from fm1companion.flash import engine, package as pk, sim
    a = sim.SimFM1(identity="FM-1_91060", name="Melodee FM-1", usb_path="1-1.1", serial="AAAA1111")
    b = sim.SimFM1(identity="FM-1_91060", name="Melodee FM-1", usb_path="1-1.2", serial="BBBB2222")
    raw = pk.build_package("FM-1_91070")
    for d in (a, b):
        d.register(raw)
    f = env / "m.fwsc"
    f.write_bytes(raw)
    be = sim.SimBackend([a, b])
    e = engine.Engine(backend=be, port_filter=lambda n: "Through" not in n, log=lambda *_: None)
    # ohne USB-Angaben lässt sich das Ziel nicht festhalten: nichts senden
    e0 = engine.Engine(backend=be, port_filter=lambda n: "Through" not in n, log=lambda *_: None,
                       details=lambda n: {})
    with pytest.raises(engine.FlashError) as ei:
        e0.write(pk.analyze(f), port="Melodee FM-1")
    assert ei.value.code == "ambiguous" and "nur das FM-1" in str(ei.value)
    assert not any(x == bytes(sim.UPGRADE) for x in a.sends + b.sends)
    # gleicher Name, zweites Gerät festgehalten: nur b wird beschrieben, Prüfung am selben Anschluss
    r = e.write(pk.analyze(f), pin=engine.Pin(usb_path="1-1.2"))
    assert r.identity == "FM-1_91070" and b.identity == "FM-1_91070" and a.identity == "FM-1_91060"
    assert not any(x == bytes(sim.UPGRADE) for x in a.sends)
    assert r.port and be.details(r.port)["usb_path"] == "1-1.2"
    assert e.current_port(engine.Pin(usb_path="1-1.2")) == r.port


def test_pin_from_port_name(env):
    from fm1companion.flash import engine, sim
    a = sim.SimFM1(name="Melodee FM-1", usb_path="1-1.1")
    b = sim.SimFM1(name="Skiff FM-1", usb_path="1-1.2")
    e = engine.Engine(backend=sim.SimBackend([a, b]), log=lambda *_: None)
    assert e.pin_for("Skiff FM-1").usb_path == "1-1.2"
    assert e.pin_for("FM-1") is None                    # nicht eindeutig


def test_resources_get_exact_port(env, monkeypatch):
    from fm1companion import firmware
    calls = []

    class R:
        returncode, stdout, stderr = 0, "resources: written\n", ""
    monkeypatch.setattr(firmware.subprocess, "run", lambda args, **k: calls.append(args) or R())
    monkeypatch.setattr(firmware.time, "sleep", lambda s: None)
    monkeypatch.setattr(firmware, "check_tool", lambda s, repo, tag: "0" * 40)   # Werkzeug-Vertrauen: eigener Test
    sp = firmware.spec("melodee")
    firmware.write_resources(sp, env, env / "r.bin", "Melodee FM-1:Melodee FM-1 MIDI 1 28:0", exact=True)
    assert calls[-1][-1] == "Melodee FM-1:Melodee FM-1 MIDI 1 28:0"
    firmware.write_resources(sp, env, env / "r.bin", "Melodee FM-1:Melodee FM-1 MIDI 1 28:0")
    assert calls[-1][-1] == "Melodee FM-1"


def test_resources_already_written_by_loader(env, monkeypatch):
    """Melodee ab 1.6.0-rc.8 schreibt der Update-Loader die Ressourcen selbst; fm1_resources.py meldet dann
    „current“ (Befehl 82, nichts geschrieben) – die Suite nennt das so und schreibt nichts nach."""
    from fm1companion import firmware

    class R:
        returncode, stdout, stderr = 0, "resources: current\n", ""
    calls = []
    monkeypatch.setattr(firmware.subprocess, "run", lambda args, **k: calls.append(args) or R())
    monkeypatch.setattr(firmware.time, "sleep", lambda s: None)
    monkeypatch.setattr(firmware, "check_tool", lambda s, repo, tag: "0" * 40)
    logs = []
    assert firmware.write_resources(firmware.spec("melodee"), env, env / "r.bin", "FM-1", logs.append) == "current"
    logs = [x for x in logs if not x.startswith("Ressourcen-Werkzeug:")]
    assert len(calls) == 1 and logs == [firmware.RESOURCES_DONE["current"]] and "Update-Loader" in logs[0]
    R.stdout = "resources: written\n"
    assert firmware.write_resources(firmware.spec("melodee"), env, env / "r.bin", "FM-1", logs.append) == "written"


def test_quick_check_melodee_160_resources(env):
    """1.6.0 hat dieselben Ressourcen wie rc.7 (melodee-1.6.0-res.bin: SHA-256 a952ed33…, wie rc.7): die
    Schnellprüfung meldet sie als vollständig, nicht als „fehlt“ oder „anderer Build“."""
    from fm1companion.flash import flashmodel as fm
    r = fm.quick_check("FM-1_91060", "MELODEE v1.6.0", {"rc": 0, "installed": True, "length": 53844, "crc": 540398389})
    assert r.level == fm.GREEN
    r = fm.quick_check("FM-1_91060", "MELODEE v1.6.0", {"rc": 0, "installed": False, "length": 53844, "crc": 540398389})
    assert r.level == fm.YELLOW and any("fehlen" in t for _l, t in r.findings)


def test_label_without_duplicate_version(env):
    from fm1companion import firmware
    assert firmware.display_label(firmware.spec("stock-v15"), "V15") == "Original-Firmware V15"
    assert firmware.display_label(firmware.spec("melodee"), "v1.6.0") == "Melodee v1.6.0"


def test_friendly_errors_are_german(env):
    import errno
    import urllib.error
    from fm1companion import firmware
    e403 = urllib.error.HTTPError("https://api.github.com/x", 403, "rate limit", {}, None)
    assert "Abruf-Limit" in firmware.friendly_error(e403) and "403" in firmware.explain(e403)
    assert "Schreibrechte" in firmware.friendly_error(PermissionError(errno.EACCES, "Permission denied", "/x"))
    assert firmware.friendly_error(RuntimeError("Installer abgebrochen (Code lost): Die USB-Verbindung ist "
                                                "abgerissen.")) == "Die USB-Verbindung ist abgerissen."


def test_release_sha_match(env):
    from fm1companion import config, firmware
    d = config.load().firmware_dir / "melodee" / "v1.6.0"
    d.mkdir(parents=True)
    (d / "SHA256SUMS").write_text("ab" * 32 + "  melodee-1.6.0.fwsc\n")
    sp = firmware.spec_by_release_sha("ab" * 32)
    assert sp.key == "melodee" and sp.sha256 == "ab" * 32
    assert firmware.spec_by_release_sha("cd" * 32) is None


# ------------------------------------------------------------ Einstellungen

def test_config_per_field_validation(env):
    from fm1companion import config
    importlib.reload(config)
    config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    config.CONFIG_FILE.write_text(json.dumps({"channel": 5, "keep_backups": "viele", "theme": "dark",
                                              "extra_firmwares": {"key": "x"}, "sample_roots": ["/a", 3],
                                              "auto_backup_before_update": "ja", "modules": {"a": [], "b": {}}}))
    s = config.load()
    assert s.theme == "dark" and s.channel == "stable" and s.keep_backups == 20
    assert s.extra_firmwares == [] and s.sample_roots == ["/a"] and s.auto_backup_before_update is True
    assert s.modules == {"b": {}}
    assert "extra_firmwares" in config.problems_text()
    from fm1companion import firmware
    assert [x.key for x in firmware.catalog()][:3] == ["melodee", "skiff", "sloop"]


def test_config_broken_file_kept_and_backup_used(env):
    from fm1companion import config
    importlib.reload(config)
    config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    config.CONFIG_FILE.write_text('{"theme": "dark"}')
    s = config.load()
    config.save(s)                                         # legt config.json.bak an
    s.theme = "light"
    config.save(s)
    assert json.loads(config.BACKUP_FILE.read_text())["theme"] == "dark"
    config.CONFIG_FILE.write_text('{"theme": "li')         # abgeschnitten
    importlib.reload(config)
    s = config.load()
    kept = list(config.CONFIG_DIR.glob("config.json.kaputt-*"))
    assert kept and kept[0].read_text() == '{"theme": "li'
    assert s.theme == "dark" and "beiseitegelegt" in config.problems_text()


def test_demo_isolation(tmp_path):
    from fm1companion import app
    env = {}
    out = app.isolate_demo(env, home=tmp_path)
    assert env["XDG_CONFIG_HOME"].startswith(str(tmp_path / ".cache/fm1-companion/demo-home"))
    assert env["FM1C_DEMO_ROOT"] == str(tmp_path / ".cache/fm1-companion/demo-FM-1") and out
    env = {"FM1C_DEMO_KEEP_XDG": "1", "XDG_CONFIG_HOME": "/t/c", "XDG_DATA_HOME": "/t/d", "XDG_CACHE_HOME": "/t/k"}
    app.isolate_demo(env, home=tmp_path)
    assert env["XDG_CONFIG_HOME"] == "/t/c" and env["FM1C_DEMO_ROOT"] == "/t/k/fm1-companion/demo-FM-1"
    # im echten Start: config sieht die getrennten Ordner und den Demo-Hauptordner
    code = ("import os;from fm1companion import app;app.isolate_demo();from fm1companion import config;"
            "print(config.CONFIG_DIR);print(config.load().root)")
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,
                       env={**os.environ, "HOME": str(tmp_path), "XDG_CONFIG_HOME": "", "XDG_DATA_HOME": "",
                            "XDG_CACHE_HOME": "", "FM1C_DEMO_ROOT": ""})
    cfg, root = r.stdout.split()
    assert "demo-home" in cfg and root.endswith("demo-FM-1"), r.stderr


# ------------------------------------------------------------ Geräte, Ordner

def test_folder_names_safe():
    from fm1companion.devices import Profile, safe_folder_name
    assert safe_folder_name("..", "id1") == "id1" and safe_folder_name(".", "id1") == "id1"
    assert "/" not in safe_folder_name("a/../b") and safe_folder_name("FM-1 A") == "FM-1 A"
    assert len(safe_folder_name("ä" * 300).encode()) <= 100
    assert Profile(id="x1", name="  ").folder_name == "x1"


def test_move_profile_folder(tmp_path):
    from fm1companion.devices import move_profile_folder
    (tmp_path / "Alt").mkdir()
    (tmp_path / "Alt" / "a.json").write_text("{}")
    move_profile_folder(tmp_path, "Alt", "Neu")
    assert (tmp_path / "Neu" / "a.json").exists() and not (tmp_path / "Alt").exists()
    (tmp_path / "Alt").mkdir()
    (tmp_path / "Alt" / "a.json").write_text('{"x": 1}')
    move_profile_folder(tmp_path, "Alt", "Neu")         # Ziel existiert: nichts überschreiben
    assert (tmp_path / "Neu" / "a.json").read_text() == "{}" and (tmp_path / "Neu" / "a_Alt.json").exists()


# ------------------------------------------------------------ FM-1-Werte, Daten

def test_audio_stats_only_for_melodee_and_open():
    from fm1companion import health

    class L:
        sent = []

        def request(self, cmd, args, **k):
            self.sent.append(cmd)
            raise TimeoutError
    assert health.audio_stats_supported("MELODEE v1.6.0") and health.audio_stats_supported("FM1 Open V14")
    assert not health.audio_stats_supported("FELUCCA SLOOP 2.8.3")
    link = L()
    assert health.fm1_live(link, "FELUCCA SLOOP 2.8.3") is None and not link.sent


def test_damaged_backups_listed(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    from fm1companion import autobackup
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "kaputt.json").write_text('{"format": "felucca-bac')
    assert autobackup.list_backups(tmp_path / "b") == []
    ms = autobackup.list_backups(tmp_path / "b", include_damaged=True)
    assert len(ms) == 1 and ms[0].damaged and ms[0].kind == "beschädigt"


# ------------------------------------------------------------ Rettung: Root-Teil

def test_root_helper_verifies_tools(tmp_path):
    from fm1companion.flash import uboot
    vendor, jl, cwd = tmp_path / "v", tmp_path / "jl", tmp_path / "Rettung"
    for d in (vendor, jl, cwd):
        d.mkdir()
    (vendor / "tool.py").write_text("import sys, pathlib\npathlib.Path('out.txt').write_text(' '.join(sys.argv[1:]))\n")
    (jl / "lib.py").write_text("x = 1\n")
    import hashlib
    spec = {"vendor": str(vendor), "jl_dir": str(jl), "jl_commit": "abc", "script": "tool.py",
            "unbricker": {"tool.py": hashlib.sha256((vendor / "tool.py").read_bytes()).hexdigest()},
            "jl": {"lib.py": hashlib.sha256((jl / "lib.py").read_bytes()).hexdigest()},
            "argv": ["restore", "x.fwsc"], "cwd": str(cwd), "log_dir": str(cwd / "logs"), "chown": [str(cwd)],
            "uid": os.getuid(), "gid": os.getgid()}
    run = [sys.executable, "-I", "-c", uboot.ROOT_HELPER]
    r = subprocess.run(run + [json.dumps(spec)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (cwd / "out.txt").read_text().startswith("restore x.fwsc --jl-dir ")
    (jl / "lib.py").write_text("x = 2  # verändert\n")
    (cwd / "out.txt").unlink()
    r = subprocess.run(run + [json.dumps(spec)], capture_output=True, text=True)
    assert r.returncode == 97 and "Prüfsumme" in r.stdout and not (cwd / "out.txt").exists()
    # Runner baut den root-Aufruf nur noch mit dem Helfer (kein Skript aus beschreibbaren Ordnern)
    argv = uboot.Runner(dry_run=False, cwd=tmp_path, root_cmd=["pkexec"]).argv("restore", "x.fwsc")
    assert argv[0] == "pkexec" and uboot.ROOT_HELPER in argv and str(uboot.SCRIPT) not in argv
    sp = json.loads(argv[-1])
    assert sp["unbricker"]["fm1_unbrick.py"] and len(sp["jl"]) > 10


def test_manifest_matches_vendored_unbricker():
    import hashlib
    from fm1companion.flash import uboot, uboot_manifest as M
    for name, sha in M.UNBRICKER.items():
        assert hashlib.sha256((uboot.VENDOR / name).read_bytes()).hexdigest() == sha
    assert M.JL["data/loaderblobs/usb/wl82loader.bin"].startswith("d41da612")


def test_deps_list_has_webchannel():
    """Debian Bookworm hat python3-pyqt6.qtwebchannel als eigenes Binärpaket (packages.debian.org)"""
    d = json.loads((ROOT / "fm1companion" / "data" / "deps.json").read_text())
    req = {x["package"]: x for x in d["required"]}
    assert "python3-pyqt6.qtwebengine" in req and "python3-pyqt6.qtwebchannel" in req
    assert req["python3-pyqt6.qtwebchannel"]["check"] == {"module": "PyQt6.QtWebChannel"}


# ------------------------------------------------------------ Pakete

def _desktop_fields(text: str) -> dict:
    return {k: v for k, v in (ln.split("=", 1) for ln in text.splitlines() if "=" in ln)
            if k in ("GenericName", "Comment")}


def test_desktop_entries_identical_and_uninstall_cleans_dirs(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "packaging/installer"))
    import core
    importlib.reload(core)
    home = tmp_path / "h"
    monkeypatch.setenv("HOME", str(home))
    for k in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(core, "_desktop_dir", lambda: str(home / "Desktop"))
    monkeypatch.setattr(core, "refresh_menus", lambda L: None)
    monkeypatch.setattr(core.subprocess, "run", lambda *a, **k: None)
    prefix = home / ".local"
    core.install(ROOT, prefix, log=lambda *_: None)
    run_entry = (prefix / "share/applications/fm1-companion.desktop").read_text()
    deb_entry = (ROOT / "data/fm1-companion.desktop.in").read_text()
    assert _desktop_fields(run_entry) == _desktop_fields(deb_entry) and _desktop_fields(run_entry)
    (home / "FM-1").mkdir()
    core.uninstall(prefix, settings=True, data=True, log=lambda *_: None)
    assert not (prefix / "share/fm1-companion").exists(), "leerer App-Ordner bleibt nicht zurück"
    assert not (prefix / "share/icons/hicolor/48x48/apps").exists()
    assert not (prefix / "bin").exists() and (home / "FM-1").exists()


def test_folder_properties_tolerate_readonly_root(env, monkeypatch):
    """~/FM-1 schreibgeschützt, Unterordner fehlen: backups_dir & Co. dürfen den Start nicht abbrechen"""
    from fm1companion import config
    s = config.load()
    s.user_root = str(env / "ro-FM-1")
    real = Path.mkdir

    def ro(self, *a, **k):
        if str(self).startswith(s.user_root):
            raise PermissionError(13, "Permission denied", str(self))
        return real(self, *a, **k)
    monkeypatch.setattr(Path, "mkdir", ro)
    assert s.backups_dir == Path(s.user_root) / "Backups" and "Backups" in config.folder_problems
    with pytest.raises(OSError):
        config.folder("Backups")                           # die strenge Variante meldet den Fehler
