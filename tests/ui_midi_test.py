# SPDX-License-Identifier: GPL-3.0-only
"""UI test of the MIDI page and the sound module (offscreen, demo device).

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
os.environ.setdefault("FM1C_LANG", "de")      # these checks expect the German user interface
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
    check(until(lambda: ctx.connected), "demo device connected")
    page = w.page("midi")
    check(page is not None and w.page("soundmodule") is not None, "pages MIDI and sound module found")
    w.go("midi")
    mon = page.monitor
    # ---- monitor: traffic app <-> demo FM-1
    ctx.job(w, "Ping", lambda wk, link: link.request(25, []), dialog=False)
    check(until(lambda: any("PING" in mon.table.item(r, 6).text() for r in range(mon.table.rowCount()))),
          f"monitor shows PING ({mon.table.rowCount()} rows)")
    dirs = {mon.table.item(r, 1).text() for r in range(mon.table.rowCount())}
    check(any("vom" in d for d in dirs) and any("zum" in d for d in dirs), f"both directions ({dirs})")
    mon.search.setText("PING")
    check(mon.table.rowCount() >= 2 and all("PING" in mon.table.item(r, 6).text() for r in range(mon.table.rowCount())),
          "search filters")
    mon.search.setText("")
    out = home / "mon.csv"
    from fm1companion import miditools as M
    out.write_text(M.export_text(mon.log.events, "csv"))
    check("Felucca PING" in out.read_text(), "export CSV")

    # ---- keyboard
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
          f"keyboard sends note on/off on channel 5 ({sent[:2]})")
    from PyQt6.QtTest import QTest
    sent.clear()
    kb.piano.setFocus()
    QTest.keyClick(kb.piano, Qt.Key.Key_Q)
    check(any(d[0] == 0x94 and d[1] == kb.piano.base + 12 for d, _ in sent), "PC key Q plays C an octave higher")
    sent.clear()
    kb.hold.setChecked(True)
    kb.piano.note_on.emit(64)
    kb.piano.note_off.emit(64)
    check(64 in kb.sounding, "chord hold: the note stays")
    kb.all_off()
    check(not kb.sounding and [0xB4, 123, 0] in [d for d, _ in sent], "all notes off")
    kb.hold.setChecked(False)
    # losing focus with a pressed key: note-off guaranteed
    sent.clear()
    kb.piano.setFocus()
    QTest.keyPress(kb.piano, Qt.Key.Key_Y)
    check(kb.piano.base in kb.sounding, "PC key Y sounds")
    kb.channel.setFocus()                                   # focus away, without keyRelease
    check(not kb.sounding and [0x84, kb.piano.base, 0] in [d for d, _ in sent], "focus lost: note-off sent")
    QTest.keyRelease(kb.piano, Qt.Key.Key_Y)
    # sustain + tab switch: the pedal is released, notes off
    sent.clear()
    kb.sustain.setChecked(True)
    kb.piano.note_on.emit(67)
    page.tabs.setCurrentWidget(page.monitor)
    check(not kb.sustain.isChecked() and [0xB4, 64, 0] in [d for d, _ in sent] and [0x84, 67, 0] in [d for d, _ in sent],
          "tab switch releases sustain and ends the note")
    page.tabs.setCurrentWidget(page.keyboard)
    # leaving the page with a sounding note
    sent.clear()
    kb.piano.note_on.emit(69)
    w.go("overview")
    check([0x84, 69, 0] in [d for d, _ in sent] and not kb.sounding, "page switch: note-off sent")
    w.go("midi")
    # monitor flood: 10 000 messages/s for 1 s – the UI stays usable, memory limited
    import threading
    t0 = time.perf_counter()
    for i in range(10000):
        mon.on_link([0x90, 60 + i % 12, 100] if i % 2 else [0xF8], "in")
    check(time.perf_counter() - t0 < 1.0, "10 000 messages into the listener in under 1 s (decoder fast enough)")
    wait(400)
    check(mon.table.rowCount() <= 2000 and len(mon.log.events) <= 20000 and "Sturzflut" in mon.status.text(),
          f"flood: table {mon.table.rowCount()} rows, note \"{mon.status.text()[:60]}\"")
    mon.clear()
    check(mon.table.rowCount() == 0 and not mon.empty.isHidden(), "clearing shows the empty state again")
    kb.piano.note_on.emit(60)
    kb.piano.note_off.emit(60)
    check(until(lambda: any("Note an" in mon.table.item(r, 3).text() for r in range(mon.table.rowCount())), 3000),
          "monitor shows sent notes")

    # ---- clock
    sent.clear()
    page.tabs.setCurrentWidget(page.clock)
    ck = page.clock
    ck.out.setCurrentIndex(ck.out.findData("fm1"))
    ck.bpm.setValue(150)
    saved_link = ctx.link
    ctx.link = None
    ck.start()
    check(not ck.master.running and "kein FM-1" in ck.state.text(), f"clock without device: {ck.state.text()[:60]}")
    ctx.link = saved_link
    ck.start()
    wait(300)
    ck.bpm.setValue(90)                                     # change the tempo while running: no jump, no error
    wait(300)
    check(ck.master.running and ck.master.bpm == 90 and ck.master.send_errors == 0, "tempo change while running")
    ck.stop()
    b = [d[0] for d, _ in sent]
    check(b and b[0] == 0xFA and b[-1] == 0xFC and b.count(0xF8) >= 20,
          f"clock: start, {b.count(0xF8)} pulses, stop")
    check("gestoppt" in ck.state.text(), "clock status")
    # from bar 5: Song Position (64 16ths) + Continue instead of Start (Melodee from 1.6.1 follows it with CLK USB)
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
        check(not ck.hint.isHidden() and "CLK" in ck.hint.text(), "Melodee 1.6.1: hint CLK to USB")
        ctx.info.version = "MELODEE v1.6.0"
        ck.fill_outputs()
        check(ck.hint.isHidden(), "Melodee 1.6.0: no CLK hint")
        ctx.info.version = saved_info
        ck.fill_outputs()

    # ---- controls (demo device)
    page.tabs.setCurrentWidget(page.controls)
    page.controls.read()
    check(until(lambda: len(page.controls.params) > 5, 30000), f"controls: {len(page.controls.params)} parameters read")
    check(ctx.link._exclusive_depth == 1, "controls hold the device (editor paused)")
    if 29 in page.controls.params:
        page.controls.widgets[29].setValue(32)
        check(until(lambda: ctx.link.request and page.controls.params[29].value == 32, 5000),
              "controls write LEN = 32 (TRACK_PARAM)")
    page.controls.learning = 29
    page.controls.on_cc(1, 74, 0)
    check(any(m["cc"] == 74 and m["id"] == 29 for m in config.module("midi")["learn"]), "MIDI learn stores CC 74")
    page.controls.on_cc(1, 74, 127)
    check(page.controls.widgets[29].value() == page.controls.params[29].d.max, "learned CC moves the control")
    page.tabs.setCurrentWidget(page.monitor)
    check(ctx.link._exclusive_depth == 0, "tab left: device free again")
    # device switch: no stale controls
    page.tabs.setCurrentWidget(page.controls)
    check(len(page.controls.params) > 5, "controls still there before the switch")
    port1 = ctx.port
    ctx.connect_port(next(pt for pt in ctx.ports if pt.serial == "DEMO-2"))
    check(until(lambda: ctx.connected and ctx.firmware_key == "sloop", 20000), "switched to DEMO-2")
    check(not page.controls.params and "Vom FM-1 lesen" in page.controls.state.text(),
          f"device switch clears the controls ({page.controls.state.text()[:50]})")
    ctx.connect_port(port1)
    check(until(lambda: ctx.connected and ctx.firmware_key == "melodee", 20000), "back to DEMO-1")
    page.tabs.setCurrentWidget(page.monitor)

    # ---- connections with emulated ALSA
    calls = []
    alsaseq.list_ports = lambda: alsaseq.parse_proc(FIXTURE)
    alsaseq.connect = lambda a, b, d=False: (calls.append((a, b, d)), (True, ""))[1]
    page.tabs.setCurrentWidget(page.routing)
    rt = page.routing
    check(until(lambda: rt.table.rowCount() > 0), f"connections: {rt.table.rowCount()} senders × {rt.table.columnCount()} receivers")
    labels = [rt.table.verticalHeaderItem(r).text() for r in range(rt.table.rowCount())]
    check(any("Melodee" in x for x in labels) and not any(x.startswith("System") for x in labels), "system ports hidden")
    rt.quick_controller()
    check(until(lambda: ("24:0", "20:0", False) in calls), "controller → FM-1 connects 24:0 → 20:0")
    r = next(i for i, p in enumerate(rt.src) if p.addr == "20:0")
    c = next(i for i, p in enumerate(rt.dst) if p.addr == "130:0")
    rt._clicked(r, c)
    check(until(lambda: ("20:0", "130:0", True) in calls), "click on an existing connection disconnects")

    # ---- sound module
    w.go("soundmodule")
    sm = w.page("soundmodule")
    SM.find_binary = lambda: None
    sm.recheck()
    check(not sm.missing.isHidden() and not sm.b_start.isEnabled(), "hint: FluidSynth missing")
    SM.find_binary = lambda: "/usr/bin/fluidsynth"
    SM.audio_drivers = lambda exe=None: ["alsa", "pipewire", "pulseaudio"]
    sm.recheck()
    sf = home / "GM.sf2"
    sf.write_bytes(b"RIFF")
    sm.sf.addItem("GM.sf2", str(sf))
    sm.sf.setCurrentIndex(sm.sf.count() - 1)
    cmd = sm.command()
    check(sm.missing.isHidden() and cmd[:5] == ["/usr/bin/fluidsynth", "-a", "pipewire", "-m", "alsa_seq"]
          and cmd[-1] == str(sf), f"command: {' '.join(cmd)}")
    # dry run with an emulated process
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
          and sm.rows[1].sound.currentText().startswith("034"), f"sounds chosen ({sm.rows[1].sound.currentText()})")
    check(sm.fs.running and "läuft" in sm.state_badge.text(), "sound module running (emulated)")
    check(until(lambda: "router_clear" in fakes[0].stdin.getvalue(), 3000), "channels + router sent")
    txt = fakes[0].stdin.getvalue()
    check("select 4 1 0 0" in txt and "router_chan 7 7 1 0" in txt and "router_chan 0 0 1 0" not in txt,
          "only channels 5–8 (tracks 5–8) sound")
    # kit -> SoundFont
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
    check(until(lambda: target.exists() and "Kanal 6" in sm.kit_msg.text(), 8000), f"folder → SoundFont ({sm.kit_msg.text()[:60]})")
    check(until(lambda: f'load "{target}" 0' in fakes[0].stdin.getvalue(), 3000), "SoundFont loaded in FluidSynth")
    check(until(lambda: "select 5 2 0 0" in fakes[0].stdin.getvalue(), 3000), "channel 6 plays its own SoundFont")
    sm.stop()
    check(not sm.fs.running, "sound module stopped")
    # start fails (audio busy): an explanation instead of a raw log line
    class Dead(Fake):
        def __init__(self, argv, **kw):
            super().__init__(argv, **kw)
            self.stdout = io.StringIO("fluidsynth: error: Failed to create the audio driver\n")
            self.rc = 1
    sm.fs._popen = Dead
    errs_before = len(errors)
    sm.start()
    check(until(lambda: "Audio-Ausgang" in sm.status.text(), 3000), f"start error explained: {sm.status.text()[:70]}")
    check(len(errors) == errs_before and not sm.fs.running, "no crash, state \"off\"")
    # sound bank file gone
    sm.fs._popen = Fake
    sf.unlink()
    sm.start()
    check(not sm.fs.running, "missing sound bank: FluidSynth is not started blindly")
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
    print("EXCEPTION:\n" + e)
print(f"{len(results) - len(fails)}/{len(results)} passed, {len(errors)} exceptions")
shutil.rmtree(home, ignore_errors=True)
sys.exit(1 if fails or errors else 0)
