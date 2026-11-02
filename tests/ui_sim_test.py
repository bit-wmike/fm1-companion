# SPDX-License-Identifier: GPL-3.0-only
"""Simulator-Seite im Demo-Modus (offscreen): Bibliothek, Laden der mitgelieferten WASM-Simulatoren ohne
JS-Fehler, MIDI vom Demo-FM-1 bis in den Simulator, Aufnahme als WAV, Online-Sprung (open_with),
„In Firefox öffnen“ (Befehl, ohne Firefox zu starten), MIDI-Relais für Firefox (SSE).

  flock /tmp/fm1c-ui.lock timeout 600 env QT_QPA_PLATFORM=offscreen python3 tests/ui_sim_test.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import traceback
import urllib.request
import wave
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
home = Path(tempfile.mkdtemp(prefix="fm1c-sim-"))
for k, sub in (("XDG_CONFIG_HOME", ".config"), ("XDG_DATA_HOME", ".local/share"), ("XDG_CACHE_HOME", ".cache")):
    os.environ[k] = str(home / sub)
os.environ["FM1C_DEMO"] = "1"
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu")

errors: list[str] = []
sys.excepthook = lambda t, v, tb: errors.append("".join(traceback.format_exception(t, v, tb)))

import PyQt6.QtWebEngineWidgets  # noqa: E402,F401
from PyQt6.QtCore import QCoreApplication, QEventLoop, Qt, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
app = QApplication(sys.argv)
from fm1companion import config  # noqa: E402

s = config.load()
s.user_root = str(home / "FM-1")
s.check_on_start = False
config.save(s)
config.ensure_dirs()
QMessageBox.exec = lambda self: 0

from fm1companion.ui.main_window import MainWindow  # noqa: E402

results = []


def check(cond, what):
    results.append((bool(cond), what))
    print(("ok   " if cond else "FAIL ") + what, flush=True)


def wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def until(cond, ms=20000, step=200):
    t0 = time.time()
    while time.time() - t0 < ms / 1000:
        try:
            if cond():
                return True
        except Exception:
            pass
        wait(step)
    return False


def js(code, timeout=5000):
    box = {}
    w.page("simulator").page.runJavaScript(code, lambda v: box.setdefault("v", v))
    until(lambda: "v" in box, timeout, 50)
    return box.get("v")


def js_errors(p):
    return [m for m in p.page.messages if m[0] >= 2 and "favicon" not in m[1].lower()]


def select(p, key):
    for i in range(p.list.count()):
        if p.list.item(i).data(Qt.ItemDataRole.UserRole) == key:
            p.list.setCurrentRow(i)
            return True
    return False


# kleine "Online"-Seite mit Web-MIDI
site = home / "site"
site.mkdir()
(site / "index.html").write_text("""<!doctype html><title>Test-Emu</title><body>emu<script>
window.got=[];window.names=[];
navigator.requestMIDIAccess().then(a=>{for(const i of a.inputs.values()){window.names.push(i.name);
i.onmidimessage=e=>window.got.push(Array.from(e.data));}});</script>""")


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


srv = ThreadingHTTPServer(("127.0.0.1", 0), lambda *a: Quiet(*a, directory=str(site)))
threading.Thread(target=srv.serve_forever, daemon=True).start()

w = MainWindow()
w.show()


def flow():
    p = w.page("simulator")
    check(p is not None and "simulator" in w.absorbed, "Seite 'Simulator' angemeldet (Reiter von Entdecken)")
    from fm1companion.ui.main_window import ALIASES
    check(ALIASES.get("simulator") == ("discover", "simulator") and w.resolve("simulator")[0] == "discover",
          "simulator leitet auf Entdecken › Simulator weiter")
    keys = list(p.entries)
    check("fm1-open" in keys and "melodee" in keys, f"Bibliothek listet die mitgelieferten Simulatoren ({keys[:4]})")
    check(any(k.startswith("url:") for k in keys), "Bibliothek listet Online-Emulatoren")
    check(until(lambda: w.ctx.connected, 15000), "Demo-FM-1 verbunden")
    w.go("simulator")

    # ---- FM1 Open (aus dem offiziellen Quellcode gebaut)
    select(p, "fm1-open")
    check("V14" in p.details.text() and "4c7496daac" in p.details.text(), "Details: Version, Quelle, Commit")
    p.start()
    check(until(lambda: js("document.readyState") == "complete", 30000), "FM1 Open: Seite geladen")
    ready = until(lambda: js("document.getElementById('sim-placeholder')?.hidden===true"), 30000)
    check(ready, "FM1 Open: WASM aus simulator.wasm geladen, SHA-256 geprüft, Panel bereit")
    check(js("getComputedStyle(document.getElementById('mode-update')).display") == "none",
          "FM1 Open: Firmware-Update-Reiter ausgeblendet")
    check(js("typeof FM1_PACKAGE") == "object" and js("FM1_PACKAGE===null"), "FM1 Open: keine Firmware im Paket")
    check(js("document.documentElement.lang") == "en", "FM1 Open: Oberfläche englisch")
    wait(1500)
    errs = js_errors(p)
    check(not errs, f"FM1 Open: keine JS-Fehler ({errs[:3]})")
    # MIDI vom Demo-FM-1: so, als käme eine Note von seinen Tasten
    w.ctx.link._receive([0x90, 60, 100])
    check(until(lambda: (js("__fm1sim.notesIn") or 0) >= 1, 5000), "MIDI vom FM-1 kommt im Simulator an")
    w.ctx.link._receive([0x80, 60, 0])
    audio = until(lambda: (js("__fm1sim.status().nodes") or 0) >= 1, 8000)
    st = js("JSON.stringify(__fm1sim.status())")
    print("   Audio-Status:", st)
    if audio:
        check(True, "FM1 Open: Audio-Thread (AudioWorklet) gestartet durch MIDI")
        w.ctx.link._receive([0x90, 64, 110])
        p.b_rec.click()
        check(until(lambda: p.rec_running, 3000), "Aufnahme läuft")
        wait(1500)
        w.ctx.link._receive([0x80, 64, 0])
        p.b_rec.click()
        check(until(lambda: p.last_recording is not None and p.last_recording.exists()
                    and p.last_recording.stat().st_size > 44, 10000), f"WAV in Aufnahmen ({p.last_recording})")
        if p.last_recording and p.last_recording.exists():
            with wave.open(str(p.last_recording)) as wf:
                n = wf.getnframes()
                frames = wf.readframes(n)
                import array
                a = array.array("h", frames)
                peak = max((abs(x) for x in a), default=0)
            print(f"   WAV: {n} Frames, {wf.getframerate()} Hz, Spitze {peak}")
            check(n > 22050, "WAV länger als 0,5 s")
            check(peak > 100, f"WAV nicht still (Spitze {peak})")
    else:
        print("   (offscreen ohne Audio-Gerät: AudioContext läuft nicht – Aufnahme-Test übersprungen)")

    # ---- Melodee
    select(p, "melodee")
    p.start()
    check(until(lambda: js("document.title") == "Melodee in the browser", 30000), "Melodee: Seite geladen")
    check(until(lambda: js("!!document.getElementById('power')"), 5000), "Melodee: Einschalt-Knopf da")
    w.ctx.link._receive([0x90, 60, 100])
    check(until(lambda: (js("__fm1sim.notesIn") or 0) >= 1, 5000), "Melodee: MIDI vom FM-1 kommt an")
    mel_audio = until(lambda: (js("__fm1sim.status().nodes") or 0) >= 1, 8000)
    print("   Melodee-Status:", js("JSON.stringify(__fm1sim.status())"), js("document.getElementById('status').textContent"))
    if mel_audio:
        check(until(lambda: js("document.getElementById('poweron').hidden") is True, 8000),
              "Melodee: per MIDI eingeschaltet (WASM im AudioWorklet)")
    else:                          # ohne Audio-Gerät: Einschalt-Fläche wie nach „Switch on“ ausblenden
        js("document.getElementById('poweron').hidden=true")
    p.speed_timer.setInterval(500)
    check(until(lambda: "eingeschaltet" in p.state.text(), 6000) and "Switch on" not in p.state.text(),
          f"Statuszeile nach dem Einschalten ohne „Switch on drücken“ ({p.state.text()[:60]})")
    p.speed_timer.setInterval(2000)
    wait(1000)
    errs = js_errors(p)
    check(not errs, f"Melodee: keine JS-Fehler ({errs[:3]})")
    if mel_audio:
        # Echtzeit-Anzeige: erst „Echtzeit“; dann bremst ein absichtlich langsamer Zusatz-Knoten im selben
        # Audio-Graphen (wie ein zu schwacher Pi) – die Anzeige muss ehrlich „zu langsam“ melden.
        p.speed_timer.setInterval(700)
        check(until(lambda: p.speed_label.text() == "Echtzeit", 12000), f"Tempo-Anzeige: Echtzeit ({p.speed_label.text()!r})")
        js("(async()=>{const c=__fm1sim.contexts.find(x=>x.state==='running');"
           "const src='class B extends AudioWorkletProcessor{process(){const t=Date.now()+5;while(Date.now()<t){}return true}}"
           "registerProcessor(\"bremse\",B)';await c.audioWorklet.addModule('data:text/javascript,'+encodeURIComponent(src));"
           "const n=new AudioWorkletNode(c,'bremse');n.connect(c.destination);window.__bremse=n})()")
        check(until(lambda: "zu langsam" in p.speed_label.text(), 20000),
              f"Tempo-Anzeige: zu langsam ({p.speed_label.text()!r}, {js('JSON.stringify(__fm1sim.speed())')})")
        check("Firefox" in p.speed_label.toolTip(), "Tooltip nennt Abhilfe")
        js("window.__bremse&&window.__bremse.disconnect()")
        p.speed_timer.setInterval(2000)

    # ---- Online-Sprung (lokaler Teststand statt Internet)
    url = f"http://127.0.0.1:{srv.server_address[1]}/index.html"
    w.go("simulator", url=url, title="Test-Emu", kind="emulator")
    check(until(lambda: js("document.title") == "Test-Emu", 15000), "open_with(url) lädt die Seite")
    check("ONLINE" in p.badge.text(), "Online-Abzeichen sichtbar")
    check(any(r.get("url") == url for r in config.module("simulator")["recent"]), "unter 'zuletzt geöffnet' gemerkt")
    check(until(lambda: "FM-1 (Tasten)" in (js("window.names") or []), 8000),
          f"Web-MIDI der Seite sieht 'FM-1 (Tasten)' ({js('window.names')})")
    w.ctx.link._receive([0x90, 62, 90])
    check(until(lambda: [0x90, 62, 90] in (js("window.got") or []), 5000), "Note vom FM-1 über Web-MIDI in der Seite")
    check(js("!!window.__fm1sim"), "Host-Skript auch in Online-Seiten")
    # Ausgang: Update-SysEx wird verworfen, Noten gehen raus
    sent = []
    orig = w.ctx.link.send_midi
    w.ctx.link.send_midi = lambda d, force=False: sent.append(list(d)) or True
    js("navigator.requestMIDIAccess({sysex:true}).then(a=>{const o=[...a.outputs.values()].find(x=>x.name==='FM-1');"
       "o.send([0xF0,0x22,0x24,0x35,0x7F,0xF7]);o.send([0x90,60,1]);})")
    check(until(lambda: [0x90, 60, 1] in sent, 5000), "Seite kann Noten an das FM-1 senden")
    check(not any(x[:1] == [0xF0] for x in sent) and p.bridge.blocked >= 1, "Update-SysEx aus der Seite blockiert")
    check(until(lambda: "zurückgehalten" in p.state.text(), 3000), f"Hinweis auf den Sicherheitsfilter ({p.state.text()[:70]})")
    # versteckt hinter einer Note, in Stücken, Soft-Key: alles bleibt draußen
    js("navigator.requestMIDIAccess({sysex:true}).then(a=>{const o=[...a.outputs.values()].find(x=>x.name==='FM-1');"
       "o.send([0x90,61,1,0xF0,0x22,0x24,0x35,0x7D,0xF7]);o.send([0x22,0x24,0x35,0x7D,0xF7]);"
       "o.send([0xF0,0x22,0x24,0x35,0x7D,0xF7]);o.send([0xF0,0x00,0x32,0x45,0,0,0,0x40,0x7F,0xF7]);o.send([0xB0,1,2]);})")
    check(until(lambda: [0xB0, 1, 2] in sent, 5000) and sent == [[0x90, 60, 1], [0xB0, 1, 2]] and p.bridge.blocked >= 5,
          f"versteckte/zerlegte Update-SysEx bleiben draußen ({sent}, {p.bridge.blocked} blockiert)")
    # als Online-EDITOR geöffnet, Herkunft NICHT bestätigt: nur Noten, Felucca-SysEx bleibt draußen
    from fm1companion import sims as _sims
    asked = []
    p.ask_origin = lambda o, t="": asked.append(o) or False
    sent.clear()
    w.go("simulator", url=url, title="Test-Editor", kind="editor")
    check(until(lambda: js("document.title") == "Test-Emu" and p.current is not None and p.current.kind == "editor", 15000),
          "Seite als Online-Editor geöffnet (ohne Erlaubnis)")
    check(asked == [_sims.origin_of(url)], f"einmalige Rückfrage für die Herkunft ({asked})")
    js("navigator.requestMIDIAccess({sysex:true}).then(a=>{const o=[...a.outputs.values()].find(x=>x.name==='FM-1');"
       "o.send([0xF0,0x7D,0x46,0x4C,1,0xF7]);o.send([0x90,63,3]);})")
    check(until(lambda: [0x90, 63, 3] in sent, 5000) and not any(x[:1] == [0xF0] for x in sent),
          f"ohne Erlaubnis: nur Noten ans FM-1 ({sent})")
    check(not _sims.origin_trusted(_sims.origin_of(url)), "Ablehnung wird nicht als Erlaubnis gespeichert")
    # jetzt erlauben: wird gespeichert, beim nächsten Öffnen nicht erneut gefragt
    p.ask_origin = lambda o, t="": asked.append(o) or True
    sent.clear()
    w.go("simulator", url=url, title="Test-Editor", kind="editor")
    check(until(lambda: js("document.title") == "Test-Emu" and p.current is not None and p.current.kind == "editor", 15000),
          "Seite als Online-Editor geöffnet")
    js("navigator.requestMIDIAccess({sysex:true}).then(a=>{const o=[...a.outputs.values()].find(x=>x.name==='FM-1');"
       "o.send([0xF0,0x7D,0x46,0x4C,1,0xF7]);o.send([0xF0,0x22,0x24,0x35,0x7F,0xF7]);o.send([0x90,64,3]);})")
    check(until(lambda: [0x90, 64, 3] in sent, 5000) and [0xF0, 0x7D, 0x46, 0x4C, 1, 0xF7] in sent
          and not any(x[:2] == [0xF0, 0x22] for x in sent), f"Editor: Felucca durch, Update-Schlüssel nicht ({sent})")
    check(_sims.origin_trusted(_sims.origin_of(url)), "Erlaubnis für die Herkunft gespeichert")
    n_asked = len(asked)
    w.go("simulator", url=url, title="Test-Editor", kind="editor")
    check(until(lambda: js("document.title") == "Test-Emu", 15000) and len(asked) == n_asked,
          "bestätigte Herkunft: keine erneute Rückfrage")
    # die Seite wechselt die Herkunft (localhost statt 127.0.0.1): sofort wieder der sichere Filter
    other = url.replace("127.0.0.1", "localhost")
    p.page.runJavaScript(f"location.href={json.dumps(other)}")
    check(until(lambda: p.page.url().host() == "localhost" and js("document.title") == "Test-Emu", 15000),
          f"Seite hat die Herkunft gewechselt ({p.page.url().toString()})")
    sent.clear()
    js("navigator.requestMIDIAccess({sysex:true}).then(a=>{const o=[...a.outputs.values()].find(x=>x.name==='FM-1');"
       "o.send([0xF0,0x7D,0x46,0x4C,1,0xF7]);o.send([0x90,65,3]);})")
    check(until(lambda: [0x90, 65, 3] in sent, 5000) and not any(x[:1] == [0xF0] for x in sent),
          f"nach Herkunftswechsel: Felucca-SysEx wieder gesperrt ({sent})")
    _sims.forget_origin(_sims.origin_of(url))
    w.ctx.link.send_midi = orig

    # ---- Offline-Meldung
    w.go("simulator", url="http://127.0.0.1:9/nichts", title="Weg")
    check(until(lambda: "nicht erreichbar" in p.state.text(), 15000), f"Offline-Hinweis ({p.state.text()})")

    # ---- Firefox fehlt: Meldung statt stillem Nichts
    select(p, "melodee")
    orig_which = shutil.which
    shutil.which = lambda n: None
    try:
        p.b_ff.click()
    finally:
        shutil.which = orig_which
    check("Firefox nicht gefunden" in p.state.text() and "apt install" in p.state.text(), f"ohne Firefox: {p.state.text()}")
    # ---- Firefox
    calls = []
    p.popen = lambda cmd, **k: calls.append(cmd)
    import fm1companion.sims as sims_mod
    orig_which = shutil.which
    shutil.which = lambda n: "/usr/bin/firefox" if n == "firefox" else None
    try:
        p.b_ff.click()
    finally:
        shutil.which = orig_which
    exp = f"http://127.0.0.1:{w.server.port}/sims/melodee/index.html?fm1latency="
    check(calls and calls[0][0] == "/usr/bin/firefox" and calls[0][-1].startswith(exp), f"Firefox-Befehl {calls}")
    # die Firefox-URL liefert wirklich den Simulator
    body = urllib.request.urlopen(calls[0][-1], timeout=5).read()
    check(b"Melodee in the browser" in body and b"fm1sim-host.js" in body, "lokaler Server liefert /sims/melodee/")
    wasm = urllib.request.urlopen(f"http://127.0.0.1:{w.server.port}/sims/melodee/melodee.wasm", timeout=5)
    check(wasm.headers.get("Content-Type") == "application/wasm", "WASM mit richtigem Typ")
    check(urllib.request.urlopen(f"http://127.0.0.1:{w.server.port}/sims/_host/fm1sim-host.js", timeout=5).status == 200,
          "Host-Skript erreichbar")
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{w.server.port}/sims/../../config.py", timeout=5)
        check(False, "kein Zugriff außerhalb von sims/")
    except Exception:
        check(True, "kein Zugriff außerhalb von sims/")
    # MIDI-Relais (Firefox): SSE-Client empfängt eine FM-1-Note
    got = []

    def sse():
        r = urllib.request.urlopen(f"http://127.0.0.1:{w.server.port}/_fm1/midi", timeout=10)
        for line in r:
            if line.startswith(b"data:"):
                got.append(json.loads(line[5:]))
                return
    threading.Thread(target=sse, daemon=True).start()
    until(lambda: w.server.relay.clients > 0, 5000)
    w.ctx.link._receive([0x90, 65, 77])
    check(until(lambda: got == [[0x90, 65, 77]], 5000), f"MIDI-Relais für Firefox ({got})")
    p.stop()
    check(until(lambda: "gestoppt" in (js("document.body.innerText") or ""), 5000), "Stoppen entlädt den Simulator")
    check(not p.b_stop.isEnabled() and not p.b_rec.isChecked(), "nach Stopp: Stoppen grau, keine Aufnahme")
    check(p.speed_label.text() == "" and not p.speed_timer.isActive(), "nach Stopp: keine Tempo-Anzeige mehr")
    check(not p.b_rec.isEnabled(), "nach Stopp: Aufnehmen grau (nichts aufzunehmen)")
    # erneut starten, Seite wechseln, zurück: läuft weiter (gewollt – wie ein Instrument), kein Fehler
    select(p, "melodee")
    p.start()
    check(until(lambda: js("document.title") == "Melodee in the browser", 30000), "Melodee erneut gestartet")
    w.go("midi")
    wait(500)
    w.go("simulator")
    check(p.current is not None and p.current.key == "melodee" and js("document.title") == "Melodee in the browser",
          "Seitenwechsel lässt den Simulator laufen")
    errs = [m for m in p.page.messages if m[0] >= 2]
    check(not errs, f"keine JS-Fehler nach Neustart ({errs[:3]})")
    p.stop()
    app.quit()


def run():
    try:
        flow()
    except Exception:  # noqa: BLE001
        errors.append(traceback.format_exc())
        app.quit()


QTimer.singleShot(500, run)
QTimer.singleShot(300000, app.quit)
app.exec()
w.close()
fails = [r for r in results if not r[0]]
for e in errors:
    print("AUSNAHME:\n" + e)
print(f"{len(results) - len(fails)}/{len(results)} bestanden, {len(errors)} Ausnahmen")
shutil.rmtree(home, ignore_errors=True)
sys.exit(1 if fails or errors else 0)
