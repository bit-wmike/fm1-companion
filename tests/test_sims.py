# SPDX-License-Identifier: GPL-3.0-only
"""Bundled simulators: manifests, WASM sounds (headless in Node), safety filter, MIDI bridge with
several ports, MIDI relay of the local server.

  python3 -m pytest tests/test_sims.py -q
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fm1companion import sims  # noqa: E402

NODE = shutil.which("node") or ("/opt/node22/bin/node" if Path("/opt/node22/bin/node").exists() else None)


def test_manifests_valid():
    found = {s.key: s for s in sims.bundled()}
    assert {"fm1-open", "melodee"} <= set(found)
    total = 0
    for s in found.values():
        m = s.manifest
        for k in ("schema", "name", "title", "version", "source_repo", "ref", "commit", "license", "build_date",
                  "toolchain", "files", "entry"):
            assert m.get(k), (s.key, k)
        assert len(m["commit"]) == 40 and m["source_repo"].startswith("https://github.com/")
        assert m["entry"] in m["files"]
        assert any(n.endswith(".wasm") for n in m["files"])
        assert any(n.startswith(("LICENSE", "NOTICE")) for n in m["files"])
        assert sims.verify(s) == []
        for n, info in m["files"].items():
            p = sims.DIR / s.key / n
            assert hashlib.sha256(p.read_bytes()).hexdigest() == info["sha256"]
            total += info["size"]
        html = (sims.DIR / s.key / m["entry"]).read_text("utf-8")
        assert "../_host/fm1sim-host.js" in html
    assert total < 15 * 1024 * 1024
    assert found["melodee"].version == "1.6.1" and found["melodee"].ref == "v1.6.1"


def test_fm1_open_has_no_firmware_and_no_update_tab():
    html = (sims.DIR / "fm1-open" / "index.html").read_text("utf-8")
    assert "const FM1_PACKAGE = null;" in html
    assert "#mode-update,#tab-update" in html
    assert '"url":"simulator.wasm"' in html
    assert not __import__("re").search(r"/\*FM1_[A-Z_]+\*/", html)


@pytest.mark.skipif(not NODE, reason="node missing")
def test_wasm_renders_audio_headless():
    r = subprocess.run([NODE, str(ROOT / "tests" / "sim_render.mjs"), str(sims.DIR), "2"], capture_output=True,
                       text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    res = {x["name"]: x for x in json.loads(r.stdout)}
    for name in ("fm1-open", "melodee"):
        x = res[name]
        print(f"{name}: peak {x['peak']:.3f} rms {x['rms']:.4f} realtime x{x['realtimeFactor']:.1f}")
        assert x["nonFinite"] == 0
        assert x["peakBeforeNote"] < 1e-3            # silent until the note comes
        assert 0.01 < x["peak"] <= 1.0 and x["rms"] > 1e-3
        assert x["realtimeFactor"] > 2                 # container (x86); Pi 5 roughly 3–5x slower


def test_out_filter_blocks_update_sysex():
    ok = sims.sim_out_allowed
    assert ok([0x90, 60, 100]) and ok([0xB0, 7, 100]) and ok([0xF8])
    lib = [0xF0, 0x7D, 0x46, 0x4D, 2, 0, 0, 0, 0xF7]                 # FM1 Open library (cmd 12 erases samples)
    assert not ok(lib)                                               # SEC-01: not for pages without permission
    assert sims.library_out_allowed(lib) and sims.library_out_allowed([0x90, 60, 100])
    for bad in ([0xF0, 0x22, 0x24, 0x35, 0x7F, 0xF7], [0xF0, 0x7D, 0x46, 0x4C, 1, 0xF7], [0xF0, 0x7D, 0x46, 0x4D, 2]):
        assert not sims.library_out_allowed(bad), bad
    for bad in ([0xF0, 0x22, 0x24, 0x35, 0x7F, 0xF7], [0xF0, 0x22, 0x24, 0x35, 0x7D, 0xF7],
                [0xF0, 0x7D, 0x46, 0x4C, 1, 0xF7], [0xF0, 0x43, 0, 9, 0xF7], []):
        assert not ok(bad), bad
    assert sims.page_midi([0x90, 1, 2]) and sims.page_midi([0xF8]) and not sims.page_midi([0xF0, 1, 0xF7])


def test_out_filter_is_gapless():
    """Audit task: no gap – neither hidden behind a note, nor in pieces, nor via other headers."""
    ok = sims.sim_out_allowed
    soft = [0xF0, 0x22, 0x24, 0x35, 0x7D, 0xF7]
    # a second message hidden behind a valid one
    assert not ok([0x90, 60, 100] + soft)
    assert not ok([0xF8] + soft)
    # in pieces: rest without status byte / only the end
    assert not ok([0x22, 0x24, 0x35, 0x7F, 0xF7]) and not ok([0xF7]) and not ok([0x7F])
    # library SysEx of FM1 Open without termination (could stay "open") or with a status byte in the content
    lib = sims.library_out_allowed
    assert not lib([0xF0, 0x7D, 0x46, 0x4D, 2]) and not lib([0xF0, 0x7D, 0x46, 0x4D, 2, 0xF0, 0x22, 0xF7])
    assert not lib([0x90, 60, 100] + soft) and not lib([0x90, 60, 100, 0xF0, 0x7D, 0x46, 0x4D, 2, 0xF7])
    # handshake of the update protocol, 7-bit data stream, Universal, Yamaha, Felucca
    for bad in ([0xF0, 0x00, 0x32, 0x45, 0, 0, 0, 0x40, 0x7F, 0xF7], [0xF0, 0x00, 0x59, 0x30, 1, 2, 0xF7],
                [0xF0, 0x7E, 0x7F, 0x06, 0x01, 0xF7], [0xF0, 0x7D, 0x46, 0x4C, 67, 1, 0xF7]):
        assert not ok(bad), bad
    # running status / wrong lengths do not get through (rtmidi would reject them anyway)
    assert not ok([0x90, 60, 100, 62, 100]) and not ok([0xC0, 5, 0]) and ok([0xC0, 5]) and ok([0xD0, 9])
    assert ok([0xE0, 0, 64]) and ok([0xF2, 0, 0]) and ok([0xFA]) and ok([0xFC])
    # 8-bit garbage from JS is masked, but stays blocked
    assert not ok([0x1F0, 0x22, 0x24, 0x35, 0x7F, 0xF7])


def test_editor_filter_allows_felucca_but_never_update():
    ed = sims.editor_out_allowed
    assert ed([0xF0, 0x7D, 0x46, 0x4C, 1, 0xF7])             # Felucca INFO
    assert ed([0xF0, 0x7D, 0x4D, 0x4C, 0, 0xF7])             # Melodee preset file
    assert ed([0xF0, 0x7E, 0x7F, 0x06, 0x01, 0xF7])          # Identity Request
    assert ed([0x90, 60, 100]) and ed([0xF0, 0x7D, 0x46, 0x4D, 2, 0, 0xF7])
    for bad in ([0xF0, 0x22, 0x24, 0x35, 0x7F, 0xF7], [0xF0, 0x22, 0x24, 0x35, 0x7D, 0xF7],
                [0xF0, 0x00, 0x32, 0x45, 0, 0, 0, 0x40, 0x7F, 0xF7], [0xF0, 0x00, 0x59, 0x30, 1, 0xF7],
                [0xF0, 0x43, 0, 9, 0xF7], [0xF0, 0x7D, 0x46, 0x4C, 1, 0xF0, 0x22, 0x24, 0x35, 0x7F, 0xF7],
                [0xF0, 0x7D], [0x90, 60, 100, 0xF0, 0x22, 0x24, 0x35, 0x7D, 0xF7]):
        assert not ed(bad), bad
    assert sims.out_filter_for("editor") is ed and sims.out_filter_for("emulator") is sims.sim_out_allowed
    assert sims.out_filter_for("") is sims.sim_out_allowed


def test_firefox_command(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda n: "/usr/bin/firefox-esr" if n == "firefox-esr" else None)
    assert sims.firefox_command("http://127.0.0.1:1/x") == ["/usr/bin/firefox-esr", "--new-window", "http://127.0.0.1:1/x"]
    monkeypatch.setattr(shutil, "which", lambda n: "/usr/bin/xdg-open" if n == "xdg-open" else None)
    assert sims.firefox_command("u") == ["/usr/bin/xdg-open", "u"]
    monkeypatch.setattr(shutil, "which", lambda n: None)
    assert sims.firefox_command("u") is None


class FakeLink:
    def __init__(self):
        self.sent, self.pt = [], []

    def add_passthrough(self, fn):
        self.pt.append(fn)

    def remove_passthrough(self, fn):
        self.pt.remove(fn)

    def send_raw(self, data):
        self.sent.append(data)
        return True


def test_bridge_editor_sees_only_fm1():
    from fm1companion.bridge import MidiBridge
    b = MidiBridge()
    link = FakeLink()
    b.attach(link, "Melodee (Demo)")
    ports = json.loads(b.ports())
    assert [p["id"] for p in ports] == ["fm1-in", "fm1-out"]
    got = []
    b.midiIn.connect(lambda pid, d: got.append((pid, d)))
    link.pt[0]([0x90, 60, 1])
    assert got == [("fm1-in", [0x90, 60, 1])]
    b.send("fm1-out", [0xF0, 0x7D, 0x46, 0x4C, 1, 0xF7])       # editor: everything allowed (no filter)
    assert link.sent == [[0xF0, 0x7D, 0x46, 0x4C, 1, 0xF7]]


def test_bridge_multi_port_and_filter():
    from fm1companion.bridge import MidiBridge
    b = MidiBridge()
    out = []
    b.extra_send = lambda pid, d: out.append((pid, d))
    b.out_filter = sims.sim_out_allowed
    b.set_extra_ports([{"id": "fm1-keys", "name": "FM-1 (Tasten)", "type": "input"},
                       {"id": "sys:Keyboard", "name": "Keyboard", "type": "input"},
                       {"id": "fm1-out", "name": "FM-1", "type": "output"}])
    ports = json.loads(b.ports())
    assert {p["name"] for p in ports} == {"FM-1 (Tasten)", "Keyboard", "FM-1"}
    got = []
    b.midiIn.connect(lambda pid, d: got.append((pid, d)))
    t = threading.Thread(target=lambda: b.inject("sys:Keyboard", [0x90, 61, 99]))
    t.start()
    t.join()
    # from a foreign thread: goes through the Qt queue; without an event loop deliver directly
    from PyQt6.QtCore import QCoreApplication
    app = QCoreApplication.instance() or QCoreApplication([])
    t0 = time.time()
    while not got and time.time() - t0 < 2:
        app.processEvents()
    assert got == [("sys:Keyboard", [0x90, 61, 99])]
    b.inject("unbekannt", [0x90, 1, 1])
    app.processEvents()
    assert len(got) == 1
    b.send("fm1-out", [0x90, 60, 1])
    b.send("fm1-out", [0xF0, 0x22, 0x24, 0x35, 0x7F, 0xF7])
    assert out == [("fm1-out", [0x90, 60, 1])] and b.blocked == 1


def test_local_server_sims_and_relay(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from fm1companion import editor_host
    monkeypatch.setattr(editor_host, "SITES", tmp_path / "editor")
    srv = editor_host.LocalServer()
    try:
        r = urllib.request.urlopen(srv.sim_url("fm1-open"), timeout=5)
        assert b"FM1_SIMULATOR" in r.read()
        w = urllib.request.urlopen(f"http://127.0.0.1:{srv.port}/sims/fm1-open/simulator.wasm", timeout=5)
        assert w.headers["Content-Type"] == "application/wasm" and w.read()[:4] == b"\0asm"
        with pytest.raises(Exception):
            urllib.request.urlopen(f"http://127.0.0.1:{srv.port}/sims/%2e%2e/bridge.py", timeout=5)
        got = []

        def sse():
            for line in urllib.request.urlopen(srv.relay_url(), timeout=10):
                if line.startswith(b"data:"):
                    got.append(json.loads(line[5:]))
                    return
        threading.Thread(target=sse, daemon=True).start()
        t0 = time.time()
        while srv.relay.clients == 0 and time.time() - t0 < 5:
            time.sleep(0.05)
        srv.relay.publish([0x90, 60, 100])
        t0 = time.time()
        while not got and time.time() - t0 < 5:
            time.sleep(0.05)
        assert got == [[0x90, 60, 100]]
    finally:
        srv.stop()
