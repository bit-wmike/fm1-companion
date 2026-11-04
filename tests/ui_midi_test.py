# SPDX-License-Identifier: GPL-3.0-only
"""UI-Test MIDI-Seite und Klangerzeuger (offscreen, Demo-Gerät).

  flock /tmp/fm1c-ui.lock timeout 600 env QT_QPA_PLATFORM=offscreen python3 tests/ui_midi_test.py
"""
from __future__ import annotations

import math
import os
import shutil
import struct
import sys
import tempfile
import time
import traceback
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
home = Path(tempfile.mkdtemp(prefix="fm1c-midi-"))
for k, sub in (("XDG_CONFIG_HOME", ".config"), ("XDG_DATA_HOME", ".local/share"), ("XDG_CACHE_HOME", ".cache")):
    os.environ[k] = str(home / sub)
os.environ["FM1C_DEMO"] = "1"
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu")

errors: list[str] = []
sys.excepthook = lambda t, v, tb: errors.append("".join(traceback.format_exception(t, v, tb)))

import PyQt6.QtWebEngineWidgets  # noqa: E402,F401
from PyQt6.QtCore import QCoreApplication, QEventLoop, Qt, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
app = QApplication(sys.argv)

from fm1companion import config  # noqa: E402

s = config.load()
s.user_root = str(home / "FM-1")
s.check_on_start = False
config.save(s)
config.ensure_dirs()
QMessageBox.exec = lambda self: 0
QDialog.exec = lambda self: 0

from fm1companion import alsaseq, soundmodule as SM  # noqa: E402
from fm1companion.ui.main_window import MainWindow  # noqa: E402

w = MainWindow()
w.show()
results = []


def check(cond, what):
    results.append((bool(cond), what))
    print(("ok   " if cond else "FAIL ") + what, flush=True)


def wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def until(cond, ms=10000):
    t0 = time.time()
    while time.time() - t0 < ms / 1000:
        if cond():
            return True
        wait(100)
    return False


PROC = Path(ROOT / "tests" / "test_midi_tools.py").read_text()
FIXTURE = PROC.split("PROC_FIXTURE = '''")[1].split("'''")[0]


def flow():
    ctx = w.ctx
    check(until(lambda: ctx.connected), "Demo-Gerät verbunden")
    page = w.page("midi")
    check(page is not None and w.page("soundmodule") is not None, "Seiten MIDI und Klangerzeuger gefunden")
    w.go("midi")
    mon = page.monitor
    # ---- Monitor: Verkehr App <-> Demo-FM-1
    ctx.job(w, "Ping", lambda wk, link: link.request(25, []), dialog=False)
    check(until(lambda: any("PING" in mon.table.item(r, 6).text() for r in range(mon.table.rowCount()))),
          f"Monitor zeigt PING ({mon.table.rowCount()} Zeilen)")
    dirs = {mon.table.item(r, 1).text() for r in range(mon.table.rowCount())}
    check(any("vom" in d for d in dirs) and any("zum" in d for d in dirs), f"beide Richtungen ({dirs})")
    mon.search.setText("PING")
    check(mon.table.rowCount() >= 2 and all("PING" in mon.table.item(r, 6).text() for r in range(mon.table.rowCount())),
          "Suche filtert")
    mon.search.setText("")
    out = home / "mon.csv"
    from fm1companion import miditools as M
    out.write_text(M.export_text(mon.log.events, "csv"))
    check("Felucca PING" in out.read_text(), "Export CSV")

    # ---- Tastatur
    sent = []
    link = ctx.link
    orig = link.send_midi

    def spy(data, force=False):
        sent.append((list(data), force))
        return orig(data, force)
    link.send_midi = spy
    page.tabs.setCurrentWidget(page.keyboard)
    kb = page.keyboard
    kb.out.setCurrentIndex(kb.out.findData("fm1"))
    kb.channel.setValue(5)
    kb.piano.note_on.emit(60)
    kb.piano.note_off.emit(60)
    check([0x94, 60, 100] in [d for d, _ in sent] and [0x84, 60, 0] in [d for d, _ in sent],
          f"Tastatur sendet Note an/aus auf Kanal 5 ({sent[:2]})")
    from PyQt6.QtTest import QTest
    sent.clear()
    kb.piano.setFocus()
    QTest.keyClick(kb.piano, Qt.Key.Key_Q)
    check(any(d[0] == 0x94 and d[1] == kb.piano.base + 12 for d, _ in sent), "PC-Taste Q spielt C eine Oktave höher")
    sent.clear()
    kb.hold.setChecked(True)
    kb.piano.note_on.emit(64)
    kb.piano.note_off.emit(64)
    check(64 in kb.sounding, "Akkord halten: Note bleibt")
    kb.all_off()
    check(not kb.sounding and [0xB4, 123, 0] in [d for d, _ in sent], "Alle Noten aus")
    kb.hold.setChecked(False)
    # Fokusverlust mit gedrückter Taste: Note-Off garantiert
    sent.clear()
    kb.piano.setFocus()
    QTest.keyPress(kb.piano, Qt.Key.Key_Y)
    check(kb.piano.base in kb.sounding, "PC-Taste Y klingt")
    kb.channel.setFocus()                                   # Fokus weg, ohne keyRelease
    check(not kb.sounding and [0x84, kb.piano.base, 0] in [d for d, _ in sent], "Fokusverlust: Note-Off gesendet")
    QTest.keyRelease(kb.piano, Qt.Key.Key_Y)
    # Sustain + Reiterwechsel: Pedal wird gelöst, Noten aus
    sent.clear()
    kb.sustain.setChecked(True)
    kb.piano.note_on.emit(67)
    page.tabs.setCurrentWidget(page.monitor)
    check(not kb.sustain.isChecked() and [0xB4, 64, 0] in [d for d, _ in sent] and [0x84, 67, 0] in [d for d, _ in sent],
          "Reiterwechsel löst Sustain und beendet die Note")
    page.tabs.setCurrentWidget(page.keyboard)
    # Seite verlassen mit klingender Note
    sent.clear()
    kb.piano.note_on.emit(69)
    w.go("overview")
    check([0x84, 69, 0] in [d for d, _ in sent] and not kb.sounding, "Seitenwechsel: Note-Off gesendet")
    w.go("midi")
    # Monitor-Sturzflut: 10 000 Nachrichten/s über 1 s – Oberfläche bleibt bedienbar, Speicher begrenzt
    import threading
    t0 = time.perf_counter()
    for i in range(10000):
        mon.on_link([0x90, 60 + i % 12, 100] if i % 2 else [0xF8], "in")
    check(time.perf_counter() - t0 < 1.0, "10 000 Nachrichten in den Listener in unter 1 s (Decoder schnell genug)")
    wait(400)
    check(mon.table.rowCount() <= 2000 and len(mon.log.events) <= 20000 and "Sturzflut" in mon.status.text(),
          f"Sturzflut: Tabelle {mon.table.rowCount()} Zeilen, Hinweis „{mon.status.text()[:60]}“")
    mon.clear()
    check(mon.table.rowCount() == 0 and not mon.empty.isHidden(), "Leeren zeigt wieder den Leerzustand")
    kb.piano.note_on.emit(60)
    kb.piano.note_off.emit(60)
    check(until(lambda: any("Note an" in mon.table.item(r, 3).text() for r in range(mon.table.rowCount())), 3000),
          "Monitor zeigt gesendete Noten")

    # ---- Takt
    sent.clear()
    page.tabs.setCurrentWidget(page.clock)
    ck = page.clock
    ck.out.setCurrentIndex(ck.out.findData("fm1"))
    ck.bpm.setValue(150)
    saved_link = ctx.link
    ctx.link = None
    ck.start()
    check(not ck.master.running and "kein FM-1" in ck.state.text(), f"Takt ohne Gerät: {ck.state.text()[:60]}")
    ctx.link = saved_link
    ck.start()
    wait(300)
    ck.bpm.setValue(90)                                     # Tempo im Lauf ändern: kein Sprung, kein Fehler
    wait(300)
    check(ck.master.running and ck.master.bpm == 90 and ck.master.send_errors == 0, "Tempowechsel im Lauf")
    ck.stop()
    b = [d[0] for d, _ in sent]
    check(b and b[0] == 0xFA and b[-1] == 0xFC and b.count(0xF8) >= 20,
          f"Clock: Start, {b.count(0xF8)} Pulse, Stop")
    check("gestoppt" in ck.state.text(), "Takt-Status")
    # ab Takt 5: Song Position (64 16tel) + Continue statt Start (Melodee ab 1.6.1 folgt dem mit CLK USB)
    sent.clear()
    ck.bar.setValue(5)
    ck.start()
    wait(200)
    ck.stop()
    ck.bar.setValue(1)
    b = [d for d, _ in sent]
    check(b and b[0] == [0xF2, 64, 0] and b[1] == [0xFB] and b[-1] == [0xFC], f"ab Takt 5: Song Position + Continue ({b[:2]})")
    saved_info = ctx.info.version if ctx.info else None
    if ctx.info:
        ctx.info.version = "MELODEE v1.6.1"
        ck.fill_outputs()
        check(not ck.hint.isHidden() and "CLK" in ck.hint.text(), "Melodee 1.6.1: Hinweis CLK auf USB")
        ctx.info.version = "MELODEE v1.6.0"
        ck.fill_outputs()
        check(ck.hint.isHidden(), "Melodee 1.6.0: kein CLK-Hinweis")
        ctx.info.version = saved_info
        ck.fill_outputs()

    # ---- Regler (Demo-Gerät)
    page.tabs.setCurrentWidget(page.controls)
    page.controls.read()
    check(until(lambda: len(page.controls.params) > 5, 30000), f"Regler: {len(page.controls.params)} Parameter gelesen")
    check(ctx.link._exclusive_depth == 1, "Regler hält das Gerät (Editor pausiert)")
    if 29 in page.controls.params:
        page.controls.widgets[29].setValue(32)
        check(until(lambda: ctx.link.request and page.controls.params[29].value == 32, 5000),
              "Regler schreibt LEN = 32 (TRACK_PARAM)")
    page.controls.learning = 29
    page.controls.on_cc(1, 74, 0)
    check(any(m["cc"] == 74 and m["id"] == 29 for m in config.module("midi")["learn"]), "MIDI-Learn speichert CC 74")
    page.controls.on_cc(1, 74, 127)
    check(page.controls.widgets[29].value() == page.controls.params[29].d.max, "gelernter CC bewegt den Regler")
    page.tabs.setCurrentWidget(page.monitor)
    check(ctx.link._exclusive_depth == 0, "Reiter verlassen: Gerät wieder frei")
    # Gerätewechsel: keine veralteten Regler
    page.tabs.setCurrentWidget(page.controls)
    check(len(page.controls.params) > 5, "Regler noch da vor dem Wechsel")
    port1 = ctx.port
    ctx.connect_port(next(pt for pt in ctx.ports if pt.serial == "DEMO-2"))
    check(until(lambda: ctx.connected and ctx.firmware_key == "sloop", 20000), "auf DEMO-2 gewechselt")
    check(not page.controls.params and "Vom FM-1 lesen" in page.controls.state.text(),
          f"Gerätewechsel leert die Regler ({page.controls.state.text()[:50]})")
    ctx.connect_port(port1)
    check(until(lambda: ctx.connected and ctx.firmware_key == "melodee", 20000), "zurück auf DEMO-1")
    page.tabs.setCurrentWidget(page.monitor)

    # ---- Verbindungen mit nachgebildetem ALSA
    calls = []
    alsaseq.list_ports = lambda: alsaseq.parse_proc(FIXTURE)
    alsaseq.connect = lambda a, b, d=False: (calls.append((a, b, d)), (True, ""))[1]
    page.tabs.setCurrentWidget(page.routing)
    rt = page.routing
    check(until(lambda: rt.table.rowCount() > 0), f"Verbindungen: {rt.table.rowCount()} Sender × {rt.table.columnCount()} Empfänger")
    labels = [rt.table.verticalHeaderItem(r).text() for r in range(rt.table.rowCount())]
    check(any("Melodee" in x for x in labels) and not any(x.startswith("System") for x in labels), "System-Ports ausgeblendet")
    rt.quick_controller()
    check(until(lambda: ("24:0", "20:0", False) in calls), "Controller → FM-1 verbindet 24:0 → 20:0")
    r = next(i for i, p in enumerate(rt.src) if p.addr == "20:0")
    c = next(i for i, p in enumerate(rt.dst) if p.addr == "130:0")
    rt._clicked(r, c)
    check(until(lambda: ("20:0", "130:0", True) in calls), "Klick auf bestehende Verbindung trennt")

    # ---- Klangerzeuger
    w.go("soundmodule")
    sm = w.page("soundmodule")
    SM.find_binary = lambda: None
    sm.recheck()
    check(not sm.missing.isHidden() and not sm.b_start.isEnabled(), "Hinweis: FluidSynth fehlt")
    SM.find_binary = lambda: "/usr/bin/fluidsynth"
    SM.audio_drivers = lambda exe=None: ["alsa", "pipewire", "pulseaudio"]
    sm.recheck()
    sf = home / "GM.sf2"
    sf.write_bytes(b"RIFF")
    sm.sf.addItem("GM.sf2", str(sf))
    sm.sf.setCurrentIndex(sm.sf.count() - 1)
    cmd = sm.command()
    check(sm.missing.isHidden() and cmd[:5] == ["/usr/bin/fluidsynth", "-a", "pipewire", "-m", "alsa_seq"]
          and cmd[-1] == str(sf), f"Befehl: {' '.join(cmd)}")
    # Probelauf mit nachgebildetem Prozess
    import io

    class Fake:
        def __init__(self, argv, **kw):
            self.stdin, self.stdout, self.pid, self.rc = io.StringIO(), io.StringIO(""), 999999, None
            self.argv = argv
            fakes.append(self)

        def poll(self):
            return self.rc

        def wait(self, t=None):
            self.rc = 0

        def terminate(self):
            self.rc = 1

        def kill(self):
            self.rc = 1
    fakes = []
    sm.fs._popen = Fake
    sm.auto.setChecked(False)
    sm.start()
    check(all(r.sound.currentIndex() >= 0 and r.sound.currentText() for r in sm.rows)
          and sm.rows[1].sound.currentText().startswith("034"), f"Klänge gewählt ({sm.rows[1].sound.currentText()})")
    check(sm.fs.running and "läuft" in sm.state_badge.text(), "Klangerzeuger läuft (nachgebildet)")
    check(until(lambda: "router_clear" in fakes[0].stdin.getvalue(), 3000), "Kanäle + Router gesendet")
    txt = fakes[0].stdin.getvalue()
    check("select 4 1 0 0" in txt and "router_chan 7 7 1 0" in txt and "router_chan 0 0 1 0" not in txt,
          "nur Kanäle 5–8 (Spuren 5–8) klingen")
    # Kit -> SoundFont
    snd = home / "PO"
    snd.mkdir()
    for i in range(3):
        with wave.open(str(snd / f"{i} kick.wav"), "wb") as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(44100)
            f.writeframes(b"".join(struct.pack("<h", int(9000 * math.sin(t / (4 + i)))) for t in range(3000)))
    sm.kit_ch.setValue(6)
    from fm1companion import sf2
    sm._make(lambda: sf2.bank_from_folder(snd), "PO")
    target = Path(s.user_root) / "SoundFonts" / "PO.sf2"
    check(until(lambda: target.exists() and "Kanal 6" in sm.kit_msg.text(), 8000), f"Ordner → SoundFont ({sm.kit_msg.text()[:60]})")
    check(until(lambda: f'load "{target}" 0' in fakes[0].stdin.getvalue(), 3000), "SoundFont in FluidSynth geladen")
    check(until(lambda: "select 5 2 0 0" in fakes[0].stdin.getvalue(), 3000), "Kanal 6 spielt den eigenen SoundFont")
    sm.stop()
    check(not sm.fs.running, "Klangerzeuger gestoppt")
    # Start scheitert (Audio belegt): deutsche Erklärung statt roher Logzeile
    class Dead(Fake):
        def __init__(self, argv, **kw):
            super().__init__(argv, **kw)
            self.stdout = io.StringIO("fluidsynth: error: Failed to create the audio driver\n")
            self.rc = 1
    sm.fs._popen = Dead
    errs_before = len(errors)
    sm.start()
    check(until(lambda: "Audio-Ausgang" in sm.status.text(), 3000), f"Startfehler erklärt: {sm.status.text()[:70]}")
    check(len(errors) == errs_before and not sm.fs.running, "kein Absturz, Zustand „aus“")
    # Klangbank-Datei weg
    sm.fs._popen = Fake
    sf.unlink()
    sm.start()
    check(not sm.fs.running, "fehlende Klangbank: FluidSynth wird nicht blind gestartet")
    w.go("midi")
    wait(200)
    app.quit()


def run():
    try:
        flow()
    except Exception:  # noqa: BLE001
        errors.append(traceback.format_exc())
        app.quit()


QTimer.singleShot(500, run)
QTimer.singleShot(240000, app.quit)
app.exec()
fails = [r for r in results if not r[0]]
for e in errors:
    print("AUSNAHME:\n" + e)
print(f"{len(results) - len(fails)}/{len(results)} bestanden, {len(errors)} Ausnahmen")
shutil.rmtree(home, ignore_errors=True)
sys.exit(1 if fails or errors else 0)
