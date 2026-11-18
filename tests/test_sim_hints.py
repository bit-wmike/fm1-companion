# SPDX-License-Identifier: GPL-3.0-only
"""Findings of the simulator sweep on a Raspberry Pi 5 (Qt 6.4 against the Chromium of the same Pi, 2026-11-18):
JIANT FM's "emulator" link of the list is the project's start page; firmwares without a published browser emulator
get no simulator entry; online pages wait for their own start button and have their own keys; fm1-emu is too slow
for the older built-in view; the app's recorder node is only created when a recording starts."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fm1companion import catalog, i18n, sims                                 # noqa: E402

NODE = shutil.which("node") or ("/opt/node22/bin/node" if Path("/opt/node22/bin/node").exists() else None)
JIANT_START = "https://juanjiant-bit.github.io/JIANT/"
JIANT_EMU = "https://juanjiant-bit.github.io/JIANT/emu/"
README = """## Custom firmware

### Open source

- [JIANT FM](https://github.com/juanjiant-bit/JIANT) - Alternative firmware. [Browser emulator]({emu}), \
[web installer](https://juanjiant-bit.github.io/JIANT/webapp/installer/) and \
[web editor](https://juanjiant-bit.github.io/JIANT/webapp/editor/). GPL-3.0.
- [NoteSorcery](https://github.com/catacombius/NoteSorcery) - OP-1-style groovebox. Web installer and browser \
emulator. GPL-3.0.
- [BBL JAM BOX](https://github.com/UnkleBlairy/BBL-JAM-BOX) - Six-track groovebox with an emulator in the \
source tree. Beta. GPL-3.0.
- [Other](https://github.com/someone/other) - Something. [Browser emulator](https://someone.github.io/other/). MIT.
"""


@pytest.fixture
def english():
    """the texts are checked in their English source form (the suite itself runs in German)"""
    before = i18n.current_language()
    i18n.set_language("en")
    yield
    i18n.set_language(before)


def by_repo(cat, repo):
    return next(e for e in cat.entries if e.repo.lower() == repo.lower())


# ---------------------------------------------------------------- 1. JIANT FM: the emulator link
def test_jiant_emulator_link_is_corrected_everywhere():
    # fresh from the list
    cat = catalog.merge(README.format(emu=JIANT_START), None)
    assert by_repo(cat, "juanjiant-bit/JIANT").emulator_url == JIANT_EMU
    # the snapshot that ships with the app (still carries the list's address) and a cache written by an older app
    snap = json.loads(catalog.SNAPSHOT_FILE.read_text("utf-8"))
    raw = next(e for e in snap["entries"] if e.get("repo") == "juanjiant-bit/JIANT")
    assert by_repo(catalog.load_snapshot(), "juanjiant-bit/JIANT").emulator_url == JIANT_EMU
    for listed in (JIANT_START, JIANT_START.rstrip("/"), JIANT_START.upper().replace("HTTPS", "https")):
        e = catalog.Entry.from_dict(dict(raw, links=dict(raw["links"], emulator=listed)))
        assert e.emulator_url == JIANT_EMU, listed
    # the other links of the entry are untouched
    e = catalog.Entry.from_dict(raw)
    assert e.links["installer"].endswith("/webapp/installer/") and e.links["editor"].endswith("/webapp/editor/")


def test_link_correction_steps_aside_once_the_list_is_right():
    # the list fixed (or pointing somewhere new): the list's address is used as it is
    for listed in (JIANT_EMU, "https://juanjiant-bit.github.io/JIANT/play/"):
        cat = catalog.merge(README.format(emu=listed), None)
        assert by_repo(cat, "juanjiant-bit/JIANT").emulator_url == listed
    # an entry without an emulator link gets none; another project's start page is no business of the correction
    cat = catalog.merge(README.format(emu=JIANT_START), None)
    assert by_repo(cat, "someone/other").emulator_url == "https://someone.github.io/other/"
    e = catalog.Entry(key="jiant-fm", name="JIANT FM", repo="juanjiant-bit/JIANT", links={"site": JIANT_START})
    assert catalog.correct_links(e).links == {"site": JIANT_START}
    for repo, role, listed, right in catalog.LINK_CORRECTIONS:
        assert right.startswith("https://") and listed != right and role in ("emulator", "editor", "installer", "manual")


# ---------------------------------------------------------------- 2. no simulator where none is published
def test_no_simulator_for_firmwares_without_a_published_emulator():
    """NoteSorcery (the list's text once said "browser emulator"; there is only an installer/editor page) and
    BBL JAM BOX (emulator only as source code): the free text alone never makes a simulator entry or button."""
    cats = [catalog.merge(README.format(emu=JIANT_START), None), catalog.load_snapshot()]
    for cat in cats:
        for repo in ("catacombius/NoteSorcery", "UnkleBlairy/BBL-JAM-BOX"):
            e = by_repo(cat, repo)
            assert e.emulator_url == "" and "emulator" not in e.links, (repo, e.links)
            assert not e.to_spec().emulator_url
    listed = " ".join(f"{t} {u}".lower() for t, u, *_rest in sims.ONLINE + sims.LINKS)
    for word in ("sorcery", "bbl", "jam-box", "jam box"):
        assert word not in listed
    assert not any("jiant" in u.lower() for _t, u, *_rest in sims.ONLINE)       # (JIANT comes from the catalog)


# ---------------------------------------------------------------- 3.–5. hints for online pages
def test_online_status_hint_names_the_start_button_and_the_keys(english):
    h = sims.online_hint("emulator")
    assert "Switch on" in h and "Start" in h and "scroll" in h and "own computer-keyboard layout" in h
    assert sims.online_hint("") == h
    # editors and the workbench are not instruments waiting to be switched on
    assert sims.online_hint("editor") == "" and sims.online_hint("workbench") == ""
    # no layout is promised for pages the app does not know
    for text in (sims.ONLINE_HINT, sims.ONLINE_KEYS_HINT):
        assert "Z S X" not in text


def test_heavy_emulator_note_is_data_driven_and_honest_about_the_view(english):
    url = "https://joakimeriksson.github.io/fm1-emu/?fw=felucca&mhz=100"
    old = sims.online_notes(url, "6.4.2")
    assert len(old) == 1 and "Qt 6.4.2" in old[0] and "Open in Firefox" in old[0] and "computing power" in old[0]
    for version in ("6.5.0", "6.11.1", ""):
        new = sims.online_notes(url, version)
        assert len(new) == 1 and "Qt" not in new[0] and "Open in Firefox" in new[0] and "may be too slow" in new[0]
    # the entry of the list itself, and only pages marked as heavy
    assert sims.online_notes("https://joakimeriksson.github.io/fm1-emu/", "6.4.2") == old
    assert sims.online_notes("https://hugelton.github.io/Felucca/webapp/try/", "6.4.2") == []
    assert sims.online_notes("", "6.4.2") == [] and sims.online_notes("https://example.org/fm1-emu/") == []
    heavy = [p for p, info in sims.ONLINE_NOTES if info.get("heavy")]
    assert heavy == ["https://joakimeriksson.github.io/fm1-emu/"]
    assert all(any(u.startswith(p) for _t, u, *_r in sims.ONLINE) for p, _i in sims.ONLINE_NOTES)


def test_x0x_note_names_its_own_keys(english):
    for url in ("https://charlesvestal.github.io/fm1-x0x/emu/", "https://charlesvestal.github.io/fm1-x0x/emu/index.html"):
        notes = sims.online_notes(url, "6.4.2")
        assert len(notes) == 1, url
        n = notes[0]
        assert "Q–I" in n and "A–K" in n and "Space is PLAY" in n and "OCT−" in n and "do not play notes" in n
    assert sims.online_notes("https://charlesvestal.github.io/fm1-x0x/manual/") == []
    assert sims.online_notes("https://charlesvestal.github.io/fm1-monk/emu/") == []


def test_hints_are_translated():
    for lang, word in (("de", "In Firefox öffnen"), ("es", "Abrir en Firefox")):
        before = i18n.current_language()
        i18n.set_language(lang)
        try:
            notes = sims.online_notes("https://joakimeriksson.github.io/fm1-emu/", "6.4.2")
            assert word in notes[0] and "Qt 6.4.2" in notes[0]
            assert sims.online_hint() != sims.ONLINE_HINT and "Switch on" in sims.online_hint()
            assert sims.online_keys_hint() != sims.ONLINE_KEYS_HINT
            assert "Q–I" in sims.online_notes("https://charlesvestal.github.io/fm1-x0x/emu/")[0]
        finally:
            i18n.set_language(before)


def test_hint_texts_are_listed_for_translation():
    """tools/i18n_extract.py does not scan sims/: every text of this kind must be listed in SIM_TEXTS"""
    src = (ROOT / "fm1companion" / "ui" / "pages" / "simulator.py").read_text("utf-8")
    import ast
    tree = ast.parse(src)
    node = next(n for n in tree.body if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "SIM_TEXTS")
    listed = {c.args[0].value for c in node.value.elts}
    for text in (sims.ONLINE_HINT, sims.ONLINE_KEYS_HINT, sims.HEAVY_NOTE, sims.HEAVY_NOTE_OLD_QT, sims.X0X_KEYS,
                 *(info["note"] for _p, info in sims.ONLINE_NOTES if info.get("note"))):
        assert text in listed, text[:50]


# ---------------------------------------------------------------- 6. the recorder node only when recording
HOST_PROBE = r"""
const fs = require("fs");
const made = [];
class Node_ { constructor(ctx, kind) { this.context = ctx; this.kind = kind; this.to = []; }
  connect(d) { this.to.push(d); return d; } }
class AudioDestinationNode extends Node_ {}
class AudioContext { constructor() { this.state = "running"; this.sampleRate = 48000; this.currentTime = 0;
    this.destination = new AudioDestinationNode(this, "destination"); }
  createGain() { made.push("gain"); return new Node_(this, "gain"); }
  createScriptProcessor(n, i, o) { made.push("script"); const s = new Node_(this, "script"); s.size = n; return s; } }
const win = { AudioContext, WebAssembly, addEventListener() {}, location: { search: "", hostname: "example.org" } };
const sandbox = { window: win, WebAssembly, AudioNode: Node_, AudioDestinationNode, location: win.location,
  URLSearchParams, addEventListener() {}, setInterval() {}, setTimeout() {}, performance,
  document: { querySelector() { return null; } } };
new Function(...Object.keys(sandbox), fs.readFileSync(process.argv[2], "utf8"))(...Object.values(sandbox));
const api = win.__fm1sim, out = {};
const ctx = new win.AudioContext(), osc = new Node_(ctx, "osc"), osc2 = new Node_(ctx, "osc");
osc.connect(ctx.destination); osc2.connect(ctx.destination);
out.afterConnect = made.slice();
out.tapped = osc.to.length === 2 && osc.to[1].kind === "gain" && osc2.to[1] === osc.to[1];
out.stateIdle = api.record.state();
out.start = api.record.start();
out.afterStart = made.slice();
const gain = osc.to[1], sp = gain.to[0];
out.chain = !!sp && sp.kind === "script" && sp.to[0] === ctx.destination;
const buf = (v) => ({ numberOfChannels: 2, getChannelData: () => new Float32Array(4096).fill(v) });
sp.onaudioprocess({ inputBuffer: buf(0.5), outputBuffer: buf(0) });
out.stateRec = api.record.state();
api.record.start(); osc.connect(ctx.destination);
out.afterSecondStart = made.slice();
out.errors = api.errors;
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(not NODE, reason="node missing")
def test_recorder_node_is_created_only_when_a_recording_starts(tmp_path):
    probe = tmp_path / "probe.js"
    probe.write_text(HOST_PROBE, "utf-8")
    r = subprocess.run([NODE, str(probe), str(sims.DIR / "_host" / "fm1sim-host.js")], capture_output=True, text=True,
                       timeout=60)
    assert r.returncode == 0, r.stderr[-2000:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["errors"] == []
    # a page with sound: its output is collected, but no ScriptProcessorNode (deprecation warning) exists yet
    assert out["afterConnect"] == ["gain"] and out["tapped"] and out["stateIdle"] == {"recording": False}
    # "Record": now there is one, between the collecting point and the speakers, and it records
    assert out["start"] == {"ok": True, "rate": 48000} and out["afterStart"] == ["gain", "script"] and out["chain"]
    assert out["stateRec"]["recording"] and out["stateRec"]["peak"] == 0.5 and out["stateRec"]["seconds"] > 0
    # and only one per AudioContext, however often a recording starts
    assert out["afterSecondStart"] == ["gain", "script"]


def test_host_script_makes_the_recorder_in_one_place_only():
    js = (sims.DIR / "_host" / "fm1sim-host.js").read_text("utf-8")
    assert js.count("createScriptProcessor(") == 1
    connect_hook = js[js.index("AudioNode.prototype.connect = function"):js.index("function wav(")]
    assert "tap(dest.context).input" in connect_hook and "recorder(" not in connect_hook
    start = js[js.index("api.record = {"):js.index("state() {")]
    assert "recorder(ctx)" in start
