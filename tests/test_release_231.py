# SPDX-License-Identifier: GPL-3.0-only
"""Sicherheits- und Datenschutz-Korrekturen für 2.3.1 (ohne Qt-Widgets).

  python3 -m pytest tests/test_release_231.py -q

H1  Fehlerbericht/Diagnose ohne Tokens, Passwörter, Rechnernamen
N1  config.json nur für den Benutzer lesbar (0600)
N2  lokaler Server: nur Host 127.0.0.1:<port>/localhost:<port>, sonst 421
N3  neue Fenster aus Webseiten: nur http/https/mailto
N4  Katalog: nur https
M3  Online-Seiten: Editor-SysEx nur nach Zustimmung je Herkunft und nur, solange die Seite dort bleibt
M4  Ressourcen-Werkzeug aus Firmware-Repos: gepinnte Commits, Rückfrage bei fremden Repos/verschobenen Tags
     und der einmalige Risiko-Hinweis (Zustimmung in den Einstellungen)
"""
from __future__ import annotations

import http.client
import importlib
import json
import os
import platform
import stat
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    for k, sub in (("XDG_CONFIG_HOME", "c"), ("XDG_DATA_HOME", "d"), ("XDG_CACHE_HOME", "k")):
        monkeypatch.setenv(k, str(tmp_path / sub))
    monkeypatch.delenv("FM1C_DEMO_ROOT", raising=False)
    from fm1companion import config
    importlib.reload(config)
    config.load().user_root = str(tmp_path / "FM-1")
    yield config
    importlib.reload(config)


# ------------------------------------------------------------------ H1
def test_redact_nested_secrets_and_identity():
    from fm1companion import redact as R
    data = {"modules": {"catalog": {"github_token": TOKEN, "thumbnails": True},
                        "x": [{"Password": "geheim", "API-Key": "k", "api_key": "k2", "client_secret": "s",
                               "note": "ok"}]},
            "empty_token": "", "Rechnername": "my-box", "path": "/home/someone/FM-1/a.syx"}
    out = R.redact(data, home="/home/someone", host="my-box", user="someone")
    flat = json.dumps(out)
    assert TOKEN not in flat and "geheim" not in flat and '"k"' not in flat and '"s"' not in flat
    assert out["modules"]["catalog"]["github_token"] == R.REDACTED and out["modules"]["catalog"]["thumbnails"] is True
    assert out["modules"]["x"][0]["note"] == "ok" and out["empty_token"] == ""
    assert out["path"] == "~/FM-1/a.syx"
    assert R.is_secret_key("GITHUB_TOKEN") and R.is_secret_key("passwd") and not R.is_secret_key("notes")
    txt = R.redact_text(f"Authorization: Bearer abc123\nkernel on my-box: usb\ntoken={TOKEN} {TOKEN}",
                        home="/home/someone", host="my-box", user="someone")
    assert "abc123" not in txt and TOKEN not in txt and "my-box" not in txt and R.HOST_PLACEHOLDER in txt


def test_crash_report_has_no_token_and_no_hostname(cfg, tmp_path, monkeypatch):
    pytest.importorskip("PyQt6.QtWidgets")
    s = cfg.load()
    cfg.module("catalog", {})["github_token"] = TOKEN
    cfg.module("other", {})["nested"] = {"deep": [{"smtp_password": "pw-123"}]}
    cfg.save(s)
    from fm1companion import health
    host = platform.node() or "testhost"
    monkeypatch.setattr(health.platform, "node", lambda: host)
    from fm1companion.ui import crash
    p = crash.build_report(tmp_path / "bericht.zip", exc_text=f"Traceback …\nValueError: {TOKEN}")
    with zipfile.ZipFile(p) as z:
        allt = "\n".join(z.read(n).decode("utf-8", "replace") for n in z.namelist())
        settings = json.loads(z.read("einstellungen.json"))
        names = z.namelist()
    assert TOKEN not in allt and "pw-123" not in allt
    assert settings["modules"]["catalog"]["github_token"] == "(entfernt)"
    if "system.json" in names:
        sysinfo = json.loads(zipfile.ZipFile(p).read("system.json"))
        assert sysinfo["statisch"].get("Rechnername", "<rechner>") == "<rechner>"
    if len(host) >= 3:
        assert f'"{host}"' not in allt


def test_syscheck_report_hides_hostname(monkeypatch):
    from fm1companion import syscheck, redact
    monkeypatch.setattr(redact.platform, "node", lambda: "box-xyz")
    f = syscheck.Finding("k", "warn", "Titel", "Text", "Rat", ["[ 1.0] box-xyz kernel: usb 1-1: reset"])
    out = syscheck.report([f], {"source": "dmesg"})
    assert "box-xyz" not in out and "<rechner>" in out


def test_diag_versions_redacted(monkeypatch):
    from fm1companion import diag
    v = diag.versions()
    assert str(Path.home()) not in json.dumps(v) or str(Path.home()) in ("/", "/root")


# ------------------------------------------------------------------ N1
def test_config_written_0600(cfg):
    old = os.umask(0o022)
    try:
        s = cfg.load()
        cfg.module("catalog", {})["github_token"] = TOKEN
        assert cfg.save(s)
        assert stat.S_IMODE(cfg.CONFIG_FILE.stat().st_mode) == 0o600
        assert cfg.save(s)                              # zweites Mal: auch die Sicherung ist privat
        assert stat.S_IMODE(cfg.CONFIG_FILE.stat().st_mode) == 0o600
        assert stat.S_IMODE(cfg.BACKUP_FILE.stat().st_mode) == 0o600
        os.chmod(cfg.CONFIG_FILE, 0o644)                # alte Datei mit 0644 wird beim Speichern privat
        assert cfg.save(s) and stat.S_IMODE(cfg.CONFIG_FILE.stat().st_mode) == 0o600
        assert json.loads(cfg.CONFIG_FILE.read_text())["modules"]["catalog"]["github_token"] == TOKEN
    finally:
        os.umask(old)


# ------------------------------------------------------------------ N2
def test_local_server_rejects_foreign_host(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from fm1companion import editor_host
    monkeypatch.setattr(editor_host, "SITES", tmp_path / "editor")
    srv = editor_host.LocalServer()

    def get(host, path="/sims/fm1-open/index.html", method="GET"):
        c = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=5)
        c.putrequest(method, path, skip_host=True)
        if host is not None:
            c.putheader("Host", host)
        c.endheaders()
        r = c.getresponse()
        r.read()
        c.close()
        return r.status

    try:
        assert get(f"127.0.0.1:{srv.port}") == 200
        assert get(f"localhost:{srv.port}") == 200
        assert get(f"LOCALHOST:{srv.port}") == 200
        for bad in ("evil.example", f"evil.example:{srv.port}", "127.0.0.1", f"127.0.0.1:{srv.port + 1}",
                    f"attacker.test.127.0.0.1.nip.io:{srv.port}", "", None):
            assert get(bad) == 421, bad
        assert get("evil.example", "/_fm1/midi") == 421
        assert get("evil.example", "/_fm1/sink/x", method="POST") == 421
        assert get("evil.example", method="HEAD") == 421
    finally:
        srv.stop()


# ------------------------------------------------------------------ N3
def test_new_window_only_http_https_mailto():
    pytest.importorskip("PyQt6.QtGui")
    from fm1companion.ui import web
    ok = ["https://example.org/x", "http://example.org", "mailto:a@b.c", "HTTPS://EXAMPLE.ORG"]
    bad = ["file:///etc/passwd", "javascript:alert(1)", "ftp://x/y", "smb://host/share", "data:text/html,x",
           "fm1://x", "", "about:blank", "vbscript:x"]
    assert all(web.external_link_allowed(u) for u in ok)
    assert not any(web.external_link_allowed(u) for u in bad)
    opened = []
    orig = web.QDesktopServices.openUrl
    web.QDesktopServices.openUrl = staticmethod(lambda u: opened.append(u.toString()) or True)
    try:
        assert not web.open_new_window_url("file:///etc/passwd") and opened == []
        assert web.open_new_window_url("https://example.org/") and opened == ["https://example.org/"]
    finally:
        web.QDesktopServices.openUrl = orig


# ------------------------------------------------------------------ N4
def test_catalog_https_only(monkeypatch):
    import urllib.error
    import urllib.request
    from fm1companion import catalog
    calls = []
    monkeypatch.setattr(catalog, "_raw_urlopen", lambda req, timeout=0, **k: calls.append(req) or "ok")
    for u in ("http://example.org/x.json", "file:///etc/passwd", "ftp://x/y"):
        with pytest.raises(urllib.error.URLError):
            catalog._https_urlopen(urllib.request.Request(u))
        with pytest.raises(urllib.error.URLError):
            catalog._https_urlopen(u)
    assert calls == []
    assert catalog._https_urlopen(urllib.request.Request("https://example.org/x")) == "ok"
    h = catalog._HttpsOnlyRedirect()
    with pytest.raises(urllib.error.URLError):
        h.redirect_request(urllib.request.Request("https://a/"), None, 302, "Found", {}, "http://a/")


# ------------------------------------------------------------------ M3
def test_out_gate_per_origin(cfg):
    from fm1companion import sims
    felucca, update, note = [0xF0, 0x7D, 0x46, 0x4C, 1, 0xF7], [0xF0, 0x22, 0x24, 0x35, 0x7F, 0xF7], [0x90, 60, 1]
    assert sims.origin_of("https://Ed.Example:443/a?b") == "https://ed.example"
    assert sims.origin_of("http://127.0.0.1:8080/x") == "http://127.0.0.1:8080"
    assert sims.origin_of("about:blank") == "" and sims.origin_of("") == ""
    url = "https://ed.example/editor/"
    g = sims.OutGate("editor", url)
    # nicht bestätigt: nur der sichere Filter
    assert g.allowed(note, url) and not g.allowed(felucca, url) and not g.allowed(update, url)
    assert not sims.origin_trusted("https://ed.example")
    sims.trust_origin("https://ed.example")
    assert sims.origin_trusted("https://ed.example")
    assert "https://ed.example" in json.loads(cfg.CONFIG_FILE.read_text())["modules"]["simulator"]["device_origins"]
    assert g.allowed(felucca, url) and g.allowed(felucca, "https://ed.example/other") and not g.allowed(update, url)
    # Seite hat die Herkunft gewechselt -> sicherer Filter
    for other in ("https://evil.example/", "http://ed.example/", "https://ed.example:8443/", "https://sub.ed.example/", ""):
        assert not g.allowed(felucca, other) and g.allowed(note, other) and not g.allowed(update, other), other
    # Simulatoren/Emulatoren: nie Editor-SysEx, auch bei bestätigter Herkunft
    assert not sims.OutGate("emulator", url).allowed(felucca, url)
    assert not sims.OutGate("", url).allowed(felucca, url)
    sims.forget_origin("https://ed.example")
    assert not sims.origin_trusted("https://ed.example") and not g.allowed(felucca, url)


# ------------------------------------------------------------------ M4
def test_tool_trust_pins_and_asks(cfg, tmp_path, monkeypatch):
    from fm1companion import firmware as fw
    monkeypatch.setattr(fw, "TRUST_FILE", tmp_path / "trust.json")
    mel = fw.spec("melodee")
    known = fw.KNOWN_TOOL_COMMITS["keremimo/melodee"]["v1.6.0"]
    assert fw.tool_trust(mel, "v1.6.0", known) == (True, "")
    ok, why = fw.tool_trust(mel, "v1.6.0", "f" * 40)
    assert not ok and "verschoben" in why
    # unbekannter Tag einer eingebauten Firmware: erster Stand wird festgehalten, Abweichung später gemeldet
    assert fw.tool_trust(mel, "v9.9.9", "a" * 40)[0]
    assert fw.tool_trust(mel, "v9.9.9", "a" * 40)[0]
    assert not fw.tool_trust(mel, "v9.9.9", "b" * 40)[0]
    fw.approve_tool(mel, "v9.9.9", "b" * 40)
    assert fw.tool_trust(mel, "v9.9.9", "b" * 40)[0]
    # vom Nutzer hinzugefügte Firmware: immer erst fragen, Zustimmung gilt für genau diesen Commit
    own = fw.FirmwareSpec("eigene", "Eigene", "someone/fw", "fw-{ver}.fwsc", resource_tool="tools/r.py",
                          builtin=False)
    ok, why = fw.tool_trust(own, "v1", "c" * 40)
    assert not ok and "someone/fw" in why and "cccccccccccc" in why
    fw.approve_tool(own, "v1", "c" * 40)
    assert fw.tool_trust(own, "v1", "c" * 40)[0] and not fw.tool_trust(own, "v1", "d" * 40)[0]
    # write_resources führt nichts aus, solange nicht bestätigt
    ran = []
    monkeypatch.setattr(fw, "repo_commit", lambda d: "e" * 40)
    monkeypatch.setattr(fw.subprocess, "run", lambda *a, **k: ran.append(a))
    with pytest.raises(fw.ToolNotTrusted) as e:
        fw.write_resources(own, tmp_path, tmp_path / "r.bin", "FM-1", tag="v2")
    assert ran == [] and e.value.repo == "someone/fw" and e.value.commit == "e" * 40
    with pytest.raises(fw.ToolNotTrusted) as e:
        fw.write_resources(mel, tmp_path, tmp_path / "r.bin", "FM-1", tag="v1.6.0")
    assert ran == [] and e.value.moved_from == known


def test_risk_ack_stored(cfg):
    from fm1companion import risk
    assert not risk.acknowledged()
    risk.acknowledge()
    assert risk.acknowledged()
    assert json.loads(cfg.CONFIG_FILE.read_text())["risk_ack"] == risk.RISK_VERSION
    text = risk.text_plain()
    assert "M-VAVE" in text and "GPL" in text and "15 und 16" in text and "Backup" in text
    assert risk.CHECK_TEXT == "Ich habe verstanden"
    # derselbe Hinweis steht in den Installern
    assert "Ich habe verstanden" in (ROOT / "packaging/installer/gui.py").read_text("utf-8")
    sh = (ROOT / "install.sh").read_text("utf-8")
    assert "inoffizielles" in sh and "15 und 16" in sh


def test_tempfiles_not_predictable():
    for f in ("fm1companion/ui/audio.py", "fm1companion/ui/recorder_page.py"):
        src = (ROOT / f).read_text("utf-8")
        assert "mkstemp" in src and "gettempdir()) / f\"fm1c-" not in src, f
