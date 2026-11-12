# SPDX-License-Identifier: GPL-3.0-only
"""Regression tests for the security findings of the quality pass after 2.6.0 (SEC-01 … SEC-18).
Every test here failed before the fix it belongs to."""
import importlib
import json
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from fm1companion import catalog, sims

ROOT = Path(__file__).resolve().parents[1]
LIB_ERASE = [0xF0, 0x7D, 0x46, 0x4D, 0x02, 0x0C, 0x01, 0x00, 0x68, 0x07, 0x00, 0x00, 0x00, 0x72, 0xF7]  # cmd 12
FELUCCA = [0xF0, 0x7D, 0x46, 0x4C, 1, 0xF7]
UPDATE = [0xF0, 0x22, 0x24, 0x35, 0x7F, 0xF7]
BOOT = [0xF0, 0x22, 0x24, 0x35, 0x7D, 0xF7]
NOTE = [0x90, 60, 100]


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


# ------------------------------------------------------------------ SEC-01
def test_sec01_library_protocol_only_local_or_confirmed(cfg):
    remote = "https://sim.example/emu/"
    # an online page of any kind without permission: notes only – the sample-erase command does not get out
    for kind in ("emulator", "workbench", "editor", "link", ""):
        g = sims.OutGate(kind, remote)
        assert g.allowed(NOTE, remote) and g.allowed([0xF8], remote)
        assert not g.allowed(LIB_ERASE, remote), kind
        assert not g.allowed(FELUCCA, remote) and not g.allowed(UPDATE, remote)
    # no page at all (placeholder) and the default gate
    assert not sims.OutGate().allowed(LIB_ERASE, "") and not sims.OutGate().allowed(LIB_ERASE, remote)
    # bundled simulator on the app's own server: library yes, editor SysEx and update keys no
    local = "http://127.0.0.1:40001/sims/fm1-open/index.html?fm1latency=interactive"
    g = sims.OutGate("", local, local=True)
    assert g.allowed(LIB_ERASE, local) and g.allowed(NOTE, local)
    assert not g.allowed(FELUCCA, local) and not g.allowed(UPDATE, local) and not g.allowed(BOOT, local)
    # … but only while the page is still on that address
    for other in (remote, "http://127.0.0.1:40002/x", "http://127.0.0.2:40001/x", ""):
        assert not g.allowed(LIB_ERASE, other), other
    # workbench: after the user confirmed the origin, the library protocol – nothing more
    w = sims.OutGate("workbench", "https://fm1-workbench.web.app")
    assert not w.allowed(LIB_ERASE, "https://fm1-workbench.web.app/")
    sims.trust_origin("https://fm1-workbench.web.app", "library")
    assert w.allowed(LIB_ERASE, "https://fm1-workbench.web.app/x")
    assert not w.allowed(FELUCCA, "https://fm1-workbench.web.app/") and not w.allowed(UPDATE, "https://fm1-workbench.web.app/")
    assert not w.allowed(LIB_ERASE, "https://evil.example/")
    # a "library" permission is not an editor permission …
    assert not sims.origin_trusted("https://fm1-workbench.web.app", "editor")
    assert not sims.OutGate("editor", "https://fm1-workbench.web.app").allowed(FELUCCA, "https://fm1-workbench.web.app/")
    # … and a simulator on a confirmed origin still gets notes only (nobody asked for that page)
    assert not sims.OutGate("emulator", "https://fm1-workbench.web.app").allowed(LIB_ERASE, "https://fm1-workbench.web.app/")
    # an editor permission includes the library (the dialog says so)
    sims.trust_origin("https://ed.example", "editor")
    e = sims.OutGate("editor", "https://ed.example/e")
    assert e.allowed(LIB_ERASE, "https://ed.example/e") and e.allowed(FELUCCA, "https://ed.example/e")
    assert not e.allowed(UPDATE, "https://ed.example/e")
    assert sims.origin_trusted("https://ed.example", "library")
    sims.forget_origin("https://fm1-workbench.web.app")
    assert not w.allowed(LIB_ERASE, "https://fm1-workbench.web.app/")
    stored = json.loads(cfg.CONFIG_FILE.read_text())["modules"]["simulator"]
    assert stored["device_origins"] == ["https://ed.example"] and stored["library_origins"] == []


def test_sec01_workbench_entry_asks_and_others_do_not():
    kinds = {s.url: s.kind for s in sims.online_list()}
    assert kinds["https://fm1-workbench.web.app"] == "workbench"
    assert sims.consent_level("workbench") == "library" and sims.consent_level("editor") == "editor"
    assert all(sims.consent_level(k) == "" for u, k in kinds.items() if u != "https://fm1-workbench.web.app")
    assert sims.consent_level("emulator") == "" and sims.consent_level("") == ""


# ------------------------------------------------------------------ SEC-05
def test_sec05_trust_only_https_or_loopback(cfg):
    ok = sims.origin_can_be_trusted
    assert ok("https://ed.example") and ok("https://ed.example:8443")
    assert ok("http://127.0.0.1:8000") and ok("http://localhost:3000") and ok("http://127.0.0.2:1") and ok("http://[::1]:8000")
    for bad in ("http://ed.example", "http://192.168.1.5:8000", "http://127.0.0.1.evil.example", "ftp://ed.example",
                "file://x", "", "https://", "http://1270.0.0.1", "http://127.1"):
        assert not ok(bad), bad
    sims.trust_origin("http://ed.example")                       # refused: nothing stored
    assert "http://ed.example" not in (cfg.module("simulator", {}).get("device_origins") or [])
    # a permission stored by an older version for an http origin no longer counts
    cfg.module("simulator", {})["device_origins"] = ["http://old.example"]
    assert not sims.origin_trusted("http://old.example")
    g = sims.OutGate("editor", "http://old.example/e")
    assert not g.allowed(FELUCCA, "http://old.example/e") and not g.editor_access("http://old.example/e")
    # even a trust callback that says yes does not help an http origin
    g = sims.OutGate("editor", "http://old.example/e", trusted=lambda o, level="editor": True)
    assert not g.allowed(FELUCCA, "http://old.example/e")
    assert sims.OutGate("editor", "https://x.example/e", trusted=lambda o: True).allowed(FELUCCA, "https://x.example/")


# ------------------------------------------------------------------ SEC-11
def test_sec11_editor_view_never_sends_update_or_boot_key():
    ok = sims.builtin_editor_out_allowed
    assert ok(NOTE) and ok(FELUCCA) and ok(LIB_ERASE) and ok([0xF0, 0x7E, 0x7F, 0x06, 0x01, 0xF7])
    assert ok([0xF0, 0x43, 0, 9, 0x20, 0, 1, 2, 0xF7])                       # DX7 voice, any manufacturer
    assert ok(NOTE + FELUCCA) and ok([0xF8])
    assert ok([0xF0, 0x7D, 0x22, 0x24, 0x35, 0x7F, 0xF7])                    # the bytes as DATA of another message
    for bad in (UPDATE, BOOT, NOTE + UPDATE, FELUCCA + BOOT, [0xF0, 0x22, 0x24, 0x35, 0x01, 0xF7], [],
                [0xF0, 0x22, 0x24, 0x35, 0x7F],                               # not closed
                [0xF0, 0x22], [0x24, 0x35, 0x7F, 0xF7],                        # in pieces
                [0xF0, 0x7D, 0x01, 0xF0, 0x22, 0x24, 0x35, 0x7F, 0xF7],        # hidden in an open SysEx
                [0xF7], [0x1F0, 0x22, 0x24, 0x35, 0x7F, 0xF7]):
        assert not ok(bad), bad


def test_sec11_editor_page_installs_the_filter():
    src = (ROOT / "fm1companion" / "ui" / "editor_page.py").read_text("utf-8")
    assert "self.bridge.out_filter = sims.builtin_editor_out_allowed" in src


def test_sec11_bridge_blocks_update_key_with_editor_filter():
    from fm1companion.bridge import MidiBridge

    class Link:
        sent: list = []

        def send_raw(self, d):
            self.sent.append(list(d))
            return True
    b = MidiBridge()
    b.out_filter = sims.builtin_editor_out_allowed
    b._link, link = Link(), None
    link = b._link
    try:
        b.connected = True
    except AttributeError:
        pass
    b.send("fm1-out", UPDATE)
    b.send("fm1-out", BOOT)
    assert b.blocked == 2 and link.sent == []


# ------------------------------------------------------------------ SEC-03 / SEC-16
def test_sec03_placeholder_helpers_escape():
    pytest.importorskip("PyQt6.QtWidgets")
    from fm1companion.ui import web
    evil = 'https://nice.example/"><svg/onload="alert(1)">'
    link = web.link_html(evil)
    assert "<svg" not in link and '"><' not in link and link.count("<a ") == 1
    for bad in ("javascript:alert(1)", "file:///etc/passwd", "data:text/html,<script>x</script>", "smb://x/y"):
        out = web.link_html(bad)
        assert "<a" not in out and "<script" not in out, bad
    assert web.esc('<img src=x onerror="y">') == "&lt;img src=x onerror=&quot;y&quot;&gt;"
    page = web.placeholder_html("<script>t</script>", "ok <b>bold</b>")
    assert "<script>" not in page and "<b>bold</b>" in page
    assert web.external_link_allowed("https://x.example") and not web.external_link_allowed("file:///etc/passwd")
    assert not web.open_new_window_url("file:///etc/passwd") and not web.open_new_window_url("javascript:1")


def test_sec03_sec16_sources_use_the_helpers():
    ed = (ROOT / "fm1companion" / "ui" / "editor_page.py").read_text("utf-8")
    assert "web.link_html(spec.editor_url)" in ed and 'href="{url}"' not in ed
    assert "name=web.esc(name)" in ed and "error=web.esc(msg)" in ed and "name=web.esc(spec.name)" in ed
    assert "QDesktopServices.openUrl" not in ed
    disc = (ROOT / "fm1companion" / "ui" / "pages" / "discover.py").read_text("utf-8")
    # only the three fixed addresses of the app itself may bypass the scheme check
    direct = [l for l in disc.splitlines() if "QDesktopServices.openUrl" in l]
    assert len(direct) == 3 and all("catmod." in l for l in direct), direct
    assert "setOpenExternalLinks(True)\n        self.readme" not in disc and "self.readme.setOpenLinks(False)" in disc
    sim = (ROOT / "fm1companion" / "ui" / "pages" / "simulator.py").read_text("utf-8")
    assert "<b>{html.escape(s.title)}</b>" in sim and "html.escape(s.description)" in sim


# ------------------------------------------------------------------ SEC-04
def test_sec04_free_name_never_overwrites(tmp_path):
    pytest.importorskip("PyQt6.QtWidgets")
    from fm1companion.ui import web
    assert web.free_name(tmp_path, "Take.wav") == "Take.wav"
    (tmp_path / "Take.wav").write_bytes(b"1")
    assert web.free_name(tmp_path, "Take.wav") == "Take (2).wav"
    (tmp_path / "Take (2).wav").write_bytes(b"2")
    assert web.free_name(tmp_path, "Take.wav") == "Take (3).wav"
    assert web.free_name(tmp_path, "noext") == "noext"


# ------------------------------------------------------------------ SEC-02
def _mooring(family="nice", url="https://dl.example/pkg/nice-1.0.fwsc", tag="v1.0"):
    return {"format": "mooring-catalog", "revision": 99, "loaders": {"loaders": []}, "allowlist": {"entries": [
        {"label": "Nice Synth v1.0", "product": "FM-1_777", "sha256": "ab" * 32, "rawSize": 10,
         "download": {"url": url, "family": family, "releaseTag": tag, "license": "MIT",
                      "sourceRepo": "https://github.com/someone/nice"}}]}}


def test_sec02_safe_component():
    ok = catalog.safe_component
    for good in ("melodee", "fm1-open", "v1.6.1-rc.7", "omni-0.4.fwsc", "My Synth (beta)", "Größe", "a+b_c"):
        assert ok(good), good
    for bad in ("", ".", "..", "../x", "a/b", "a\\b", "/abs", ".hidden", "..x", "~", "~x", "-rf", "a\nb", "a\x00b",
                " lead", "trail ", "dot.", "x" * 121, None, 5, "../../.local/share/applications"):
        assert not ok(bad), bad


@pytest.mark.parametrize("family,url,tag", [
    ("../../.local/share/applications", "https://evil.example/pkg/evil.desktop", "v1.0"),      # the audit's exploit
    ("nice", "https://evil.example/pkg/..", "v1.0"),
    ("nice", "https://evil.example/pkg/", "v1.0"),
    ("nice", "https://evil.example/pkg/.bashrc", "v1.0"),
    ("nice", "https://evil.example/pkg/a.fwsc?x=/..", "v1.0"),
    ("nice", "https://evil.example/pkg/nice.fwsc", "../../x"),
    ("nice", "https://evil.example/pkg/nice.fwsc", "a/b"),
    ("a/b", "https://evil.example/pkg/nice.fwsc", "v1"),
    ("..", "https://evil.example/pkg/nice.fwsc", "v1"),
])
def test_sec02_live_catalog_rejects_unsafe_names(family, url, tag):
    m = catalog.parse_mooring(_mooring(family, url, tag))
    assert m["packages"] == []
    cat = catalog.merge("", m)
    assert all(catalog.safe_component(e.key) for e in cat.entries)
    assert not any(p.sha256 == "ab" * 32 for e in cat.entries for p in e.packages)


def test_sec02_good_entry_keeps_its_key_unchanged():
    cat = catalog.merge("", catalog.parse_mooring(_mooring("Nice_Synth.v2")))
    e = [x for x in cat.entries if any(p.sha256 == "ab" * 32 for p in x.packages)]
    assert len(e) == 1 and e[0].key == "Nice_Synth.v2"            # validated, not slugged (stored keys keep matching)
    assert e[0].to_spec().key == "Nice_Synth.v2"


def test_sec02_family_without_download_cannot_become_a_key():
    m = _mooring("../../x", url="")
    cat = catalog.merge("", catalog.parse_mooring(m))
    assert all(catalog.safe_component(e.key) for e in cat.entries)


def test_sec02_cache_and_snapshot_are_checked_too():
    good = {"key": "nice", "name": "Nice", "packages": [
        {"label": "a", "version": "v1", "url": "https://x.example/a.fwsc", "sha256": "ab" * 32},
        {"label": "b", "version": "../v", "url": "https://x.example/a.fwsc", "sha256": "ab" * 32},
        {"label": "c", "version": "v1", "url": "https://x.example/..", "sha256": "ab" * 32},
        {"label": "d", "version": "", "url": ""}]}
    e = catalog.Entry.from_dict(good)
    assert [p.label for p in e.packages] == ["a", "d"]
    with pytest.raises(ValueError):
        catalog.Entry.from_dict({"key": "../../.local/share/applications", "name": "x"})
    cat = catalog.Catalog.from_dict({"entries": [good, {"key": "../up", "name": "bad"}, {"key": "a/b", "name": "bad"}]})
    assert [x.key for x in cat.entries] == ["nice"]
    # the bundled snapshot is not affected
    snap = json.loads(catalog.SNAPSHOT_FILE.read_text("utf-8"))
    loaded = catalog.Catalog.from_dict(snap)
    assert len(loaded.entries) == len(snap["entries"])
    assert sum(len(x.packages) for x in loaded.entries) == sum(len(x["packages"]) for x in snap["entries"])


# ------------------------------------------------------------------ SEC-18
def test_sec18_no_token_for_another_host():
    h = catalog._HttpsOnlyRedirect()
    req = urllib.request.Request("https://api.github.com/x", headers={"Authorization": "Bearer ghp_secret", "User-Agent": "t"})
    new = h.redirect_request(req, None, 302, "Found", {}, "https://objects.example/file")
    assert not any(k.lower() == "authorization" for k in list(new.headers) + list(new.unredirected_hdrs))
    assert new.get_header("User-agent") == "t"
    same = h.redirect_request(req, None, 302, "Found", {}, "https://api.github.com/y")
    assert same.get_header("Authorization") == "Bearer ghp_secret"
    with pytest.raises(urllib.error.URLError):
        h.redirect_request(req, None, 302, "Found", {}, "http://api.github.com/y")


# ------------------------------------------------------------------ SEC-08
def test_sec08_ffmpeg_temp_file_does_not_leak_a_descriptor(tmp_path, monkeypatch):
    from fm1companion import samples
    if not os.path.isdir("/proc/self/fd"):
        pytest.skip("needs /proc")
    monkeypatch.setattr(samples.shutil, "which", lambda n: "/usr/bin/ffmpeg")

    class R:
        returncode, stderr = 0, ""
    monkeypatch.setattr(samples.subprocess, "run", lambda *a, **k: R())
    before = len(os.listdir("/proc/self/fd"))
    made = [samples._ffmpeg_to_wav(tmp_path / "x.flac") for _i in range(25)]
    after = len(os.listdir("/proc/self/fd"))
    for f in made:
        f.unlink(missing_ok=True)
    assert after <= before + 1, (before, after)


# ------------------------------------------------------------------ SEC-13
def test_sec13_pack_tree_skips_links_and_dotdot():
    from fm1companion import samplepacks as P
    t = ("100644 blob a 10\tDrums/k.wav\n"
         "120000 blob b 14\tDrums/leak.wav\n"
         "100644 blob c 10\t../escape.wav\n"
         "100644 blob d 10\tA/../../b.wav\n"
         "100644 blob e 10\t/abs/c.wav\n")
    assert [e.path for e in P.parse_tree(t)] == ["Drums/k.wav"]
    assert P.safe_rel_path("A/b.wav") and not P.safe_rel_path("../b.wav") and not P.safe_rel_path("/b.wav")
    assert not P.safe_rel_path("A//b.wav") and not P.safe_rel_path("A/./b.wav") and not P.safe_rel_path("")


def test_sec13_pack_download_does_not_follow_symlinks(tmp_path, monkeypatch):
    from fm1companion import samplepacks as P
    secret = tmp_path / "secret.key"
    secret.write_bytes(b"TOP-SECRET-KEY\n")
    src = tmp_path / "base" / "vcsl"
    (src / "Drums").mkdir(parents=True)
    (src / "Drums" / "k1.wav").write_bytes(b"RIFF" + b"\0" * 60)
    os.symlink(secret, src / "Drums" / "leak.wav")
    os.symlink(tmp_path, src / "Linked")                              # a linked folder
    g = ["git", "-c", "user.email=a@b", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q", str(src)], check=True)
    subprocess.run([*g, "-C", str(src), "add", "-A"], check=True)
    subprocess.run([*g, "-C", str(src), "commit", "-qm", "x"], check=True)
    subprocess.run(["git", "-C", str(src), "config", "uploadpack.allowfilter", "true"], check=True)
    monkeypatch.setenv("FM1C_PACKS_GITBASE", "file://" + str(tmp_path / "base"))
    monkeypatch.delenv("FM1C_PACKS_FIXTURES", raising=False)
    monkeypatch.setattr(P, "CACHE_DIR", tmp_path / "cache")
    p = P.pack("vcsl")
    assert [e.path for e in P.index(p)] == ["Drums/k1.wav"]
    # even if an old contents list still names the link, the copy step refuses it
    monkeypatch.setattr(P, "index", lambda *a, **k: [P.Entry("Drums/k1.wav", 64), P.Entry("Drums/leak.wav", 15),
                                                     P.Entry("../../secret.key", 15)])
    out = P.download(p, tmp_path / "dest", None)
    assert [f.name for f in out] == ["k1.wav"]
    assert not list((tmp_path / "dest").rglob("leak.wav")) and not list((tmp_path / "dest").rglob("secret.key"))


# ------------------------------------------------------------------ SEC-14
def test_sec14_relay_needs_the_key_and_no_listing(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from fm1companion import editor_host
    monkeypatch.setattr(editor_host, "SITES", tmp_path / "editor")
    srv = editor_host.LocalServer()
    try:
        (tmp_path / "editor" / "melodee").mkdir(parents=True)
        (tmp_path / "editor" / "melodee" / "x.txt").write_text("x")
        base = f"http://127.0.0.1:{srv.port}"

        def status(path):
            try:
                return urllib.request.urlopen(base + path, timeout=5).status
            except urllib.error.HTTPError as e:
                return e.code
        assert status("/_fm1/midi") == 404 and status("/_fm1/midi/") == 404 and status("/_fm1/midi/wrong") == 404
        assert status("/") == 404 and status("/melodee/") == 404           # no directory listing
        assert status("/sims/") == 404 and status("/sims/fm1-open/") == 200   # index.html is still served
        assert status("/melodee/x.txt") == 200
        assert len(srv.relay_token) >= 16 and srv.relay_url().endswith("/_fm1/midi/" + srv.relay_token)
        assert editor_host.LocalServer.relay_url is not None
        r = urllib.request.urlopen(srv.relay_url(), timeout=5)
        assert r.status == 200 and r.headers["Content-Type"].startswith("text/event-stream")
        r.close()
    finally:
        srv.stop()
    js = (ROOT / "fm1companion" / "sims" / "_host" / "fm1sim-host.js").read_text("utf-8")
    assert 'q.get("fm1relay")' in js and 'new EventSource("/_fm1/midi")' not in js
