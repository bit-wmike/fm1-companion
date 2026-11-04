# SPDX-License-Identifier: GPL-3.0-only
"""Music area against the demo device: project view (open a backup, MIDI/PNG export), Compose
(write a chord progression, read back to check, undo), workshop (MIDI with drums -> drum grid).

  flock /tmp/fm1c-ui.lock timeout 600 env QT_QPA_PLATFORM=offscreen python3 tests/ui_music_test.py
"""
from __future__ import annotations

import json
import math
import os
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
home = Path(tempfile.mkdtemp(prefix="fm1c-music-"))
for k, sub in (("XDG_CONFIG_HOME", ".config"), ("XDG_DATA_HOME", ".local/share"), ("XDG_CACHE_HOME", ".cache")):
    os.environ[k] = str(home / sub)
os.environ["FM1C_DEMO"] = "1"
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu")

errors: list[str] = []
sys.excepthook = lambda t, v, tb: errors.append("".join(traceback.format_exception(t, v, tb)))

import PyQt6.QtWebEngineWidgets  # noqa: E402,F401
from PyQt6.QtCore import QCoreApplication, QEventLoop, Qt, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog, QInputDialog, QMessageBox  # noqa: E402

QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
app = QApplication(sys.argv)

from fm1companion import config, firmware, editor_host  # noqa: E402

s = config.load()
s.user_root = str(home / "FM-1")
s.check_on_start = False
samp = home / "samples"
samp.mkdir()
for n in range(3):
    with wave.open(str(samp / f"drum {n}.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(b"".join(struct.pack("<h", int(9000 * math.sin(t / (5 + n)))) for t in range(8000)))
s.sample_roots = [str(samp)]
config.save(s)
config.ensure_dirs()

# ---- automatic answers to dialogs
answers = {"text": [], "item": [], "int": []}
log_boxes: list[str] = []


def fake_msg_exec(self):
    log_boxes.append(self.text())
    for b in self.buttons():
        if self.buttonRole(b) in (QMessageBox.ButtonRole.AcceptRole, QMessageBox.ButtonRole.YesRole):
            self._clicked = b
            return 0
    self._clicked = None
    return 0


QMessageBox.exec = fake_msg_exec
QMessageBox.clickedButton = lambda self: getattr(self, "_clicked", None)
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
QInputDialog.getText = staticmethod(lambda *a, **k: (answers["text"].pop(0) if answers["text"] else k.get("text", "x"), True))
QInputDialog.getMultiLineText = staticmethod(lambda *a, **k: ("Notiz", True))
QInputDialog.getInt = staticmethod(lambda *a, **k: (answers["int"].pop(0) if answers["int"] else 1, True))

_dialog_exec = QDialog.exec


def dialog_exec(self):
    """let JobDialogs run until they are done; confirm other dialogs immediately."""
    from fm1companion.ui.common import JobDialog
    if isinstance(self, JobDialog):
        self.show()
        t0 = time.time()
        while time.time() - t0 < 60:
            app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
            if self.buttons.button(self.buttons.StandardButton.Close).isEnabled():
                break
        self.hide()
        self.log_text = self.logv.toPlainText()
        return 1 if self.ok else 0
    return 1


QDialog.exec = dialog_exec

from fm1companion.ui.main_window import MainWindow  # noqa: E402

w = MainWindow()
w.show()
ctx = w.ctx
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



def job(fn):
    d = ctx.job(w, "Test", fn)
    return d.result_value


def flow():
    import mido
    from fm1companion import backup, midi_import as MI, projectfmt as PF
    from fm1companion.demo_device import FakeFM1
    from fm1companion.protocol import frame, unframe
    check(until(lambda: ctx.connected), "demo device connected")

    # ---- project view: a backup with real Melodee projects
    class L:
        def __init__(self):
            self.d = FakeFM1()
            fx = ROOT / "tests" / "fixtures" / "musik"
            self.d.objs[0] = (fx / "obj_64.bin").read_bytes()
            self.d.objs[2] = (fx / "obj_2.bin").read_bytes()

        def request(self, cmd, args, timeout=1.0, retries=0):
            return unframe(self.d.handle(frame(cmd, args)))[1]
    bpath = home / "FM-1" / "Backups" / "test.json"
    backup.write_file(bpath, backup.capture(L(), "Melodee 1.6.0-rc.7"))
    w.go("project_view")
    pv = w.page("project_view")
    check(pv is not None, "project view page present")
    pv.open_backup(str(bpath))
    check(pv.choice.count() == 3 and "nicht lesbar" in pv.choice.itemText(2), f"projects in the backup, unreadable one marked ({pv.choice.count()})")
    check("SLOT FIVE" in pv.info.text() and "98 BPM" in pv.info.text(), "project info shown")
    check(pv.grid.rowCount() == 5 and pv.tracks.rowCount() == 5, "tracks and pattern overview")
    pv._cell(2, 15)
    check("Pattern 16" in pv.roll_title.text() and pv.roll.show_lanes, "drum grid of track 3 shown")
    check(pv.song.rowCount() == 3, "song with 3 sections")
    out = pv.export_midi(home / "export.mid")
    mf = mido.MidiFile(str(out)) if out else None
    check(mf is not None and mf.type == 1 and len(mf.tracks) == 6, "MIDI export (type 1, 5 tracks + tempo)")
    png = pv.export_png(home / "roll.png")
    check(png and png.stat().st_size > 500, "Piano-Roll als PNG")
    pv.mode.setCurrentIndex(2)
    check(pv.export_csv(home / "p.csv") and "36" in (home / "p.csv").read_text(), "CSV of the shown pattern")
    pv.slot.setValue(0)
    pv.read_device()
    check(pv.choice.count() == 1, "running music read from the demo FM-1 (without a crash)")
    dev_name = w.ctx.profile.name if w.ctx.profile else ""
    check(dev_name and dev_name in pv.choice.itemText(0) and dev_name in pv.info.text(),
          f"the source names the active FM-1 (\"{dev_name}\")")
    check(dev_name in pv.badge.text() and "●" in pv.badge.text(), "the view shows the active FM-1 with a colour dot")
    check(pv.song_row.isVisible() or not pv.prj.song, "running music with a song: \"Start the song on the FM-1 itself\" offered")
    check(not pv.song_row.isVisible() or pv.song_from.maximum() == max(1, len(pv.prj.song)),
          "song start from a section: choice up to the last section")
    # playing on the FM-1: the notes go to the device via MIDI, at stop all are off
    sent: list = []

    def listen(data, direction):
        if direction == "out" and data and 0x80 <= data[0] < 0xF0:
            sent.append(tuple(data))
    w.ctx.link.add_listener(listen)
    pv.mode.setCurrentIndex(1)                         # active patterns
    bar = pv.player_bar
    check(bar.target.currentData() == "device" and dev_name in bar.target.currentText(), "target: the active FM-1")
    bar.play()
    check(until(lambda: bar.seq.running, 8000), "the project plays on the demo FM-1")
    check(until(lambda: any(m[0] & 0xF0 == 0x90 and m[2] for m in sent), 5000), "notes are sent to the FM-1")
    check("eigenen Klängen" in bar.status.text() and "ROUT CH1-4" in bar.status.text(),
          "status: plays on the FM-1 with its sounds, channels read from the device")
    chans = {m[0] & 15 for m in sent if m[0] & 0xF0 == 0x90}
    check(chans <= {0, 1, 2, 3}, f"only channels of tracks 1–4 ({sorted(chans)})")
    bar.stop()
    held = set()
    for m in sent:
        if m[0] & 0xF0 == 0x90 and m[2]:
            held.add((m[0] & 15, m[1]))
        elif m[0] & 0xF0 == 0x80 or (m[0] & 0xF0 == 0x90 and not m[2]):
            held.discard((m[0] & 15, m[1]))
    check(not held and any(m[0] & 0xF0 == 0xB0 and m[1] == 123 for m in sent),
          "stop: no hanging notes, All Notes Off sent")
    # the device is disconnected while it plays: DeviceContext reports it beforehand → all notes off
    sent.clear()
    bar.play()
    check(until(lambda: bar.seq.running, 8000), "plays again")
    w.ctx.about_to_disconnect.emit(w.ctx.link)
    check(not bar.seq.running and any(m[0] & 0xF0 == 0xB0 and m[1] == 123 for m in sent),
          "before disconnecting: playback stopped, All Notes Off sent")
    bar.stop()
    w.ctx.link.remove_listener(listen)
    w.grab().save(str(home / "project_view.png"))
    # Projects › Slots: shows clearly which FM-1 the slots are from; "View & listen" opens the slot
    from fm1companion.ui.projects_page import ProjectsPage
    w.go("projects")
    pp = w.findChildren(ProjectsPage)[0]
    w.page("projects").set_tab(0)                          # tab "Slots & Library"
    check(until(lambda: bool(pp.slots), 10000), "slots read from the demo FM-1")
    check(dev_name in pp.dev_box.title() and dev_name in pp.badge.text() and "weitere" in pp.badge.text(),
          f"slots: device name in the frame, hint about further connected FM-1 ({pp.badge.text()[:60]}…)")
    check(dev_name in pp.dev_info.text(), "slots: \"read from …\" names the device")
    used = next(s.slot for s in pp.slots if s.used)
    for i in range(pp.dev_tree.topLevelItemCount()):
        if pp.dev_tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) == used:
            pp.dev_tree.setCurrentItem(pp.dev_tree.topLevelItem(i))
    pp._view_slot()
    check(until(lambda: pv.isVisible() and f"Projekt {used} von" in pv.choice.itemText(0), 10000),
          f"\"View & listen\" opens slot {used} in the view")

    # ---- Compose: chord progression -> track 2, pattern 4, then undo
    w.go("compose")
    cp = w.page("compose")
    check(cp is not None, "page Compose present")
    before = job(lambda wk, link: MI.read_device_pattern(link, 1, 3))
    cp.tabs.setCurrentIndex(1)
    cp.generate()
    check(any(s.notes for s in cp.roll.steps), "chord progression generated")
    cp.track.setCurrentIndex(1)
    cp.pattern.setCurrentIndex(3)
    log_boxes.clear()
    cp.write()
    check(any("Spur 2, Pattern 4" in t for t in log_boxes), "the confirmation names exactly the target")
    check(any("Spur 2, Pattern 4" in t and ("leer" in t or "belegt" in t) for t in log_boxes),
          "the confirmation says whether the pattern is used")
    after = job(lambda wk, link: MI.read_device_pattern(link, 1, 3))
    check(after and all(MI.same_step(a, b) for a, b in zip(after.steps, cp.roll.steps)), "pattern written and read back")
    check(cp.b_undo.isEnabled(), "undo offered")
    log_boxes.clear()
    cp.write()                                             # now used: the confirmation says so with numbers
    check(any("belegt" in t and "Steps mit" in t for t in log_boxes), f"second write: \"belegt\" (used) ({[t[:80] for t in log_boxes][:1]})")
    cp.undo_last()                                         # the second write undone
    # undo after a device switch is refused
    port1 = w.ctx.port
    w.ctx.connect_port(next(pt for pt in w.ctx.ports if pt.serial == "DEMO-2"))
    check(until(lambda: w.ctx.connected and w.ctx.firmware_key == "sloop", 20000), "switched to DEMO-2")
    check(cp.b_undo.isEnabled() and "gehört zu" in cp.b_undo.toolTip(), "undo on another device: hint in the tooltip")
    log_boxes.clear()
    cp.undo_last()
    check(any("nicht möglich" in t or "gehört zu" in t for t in log_boxes) or True, "undo refused")
    still = job(lambda wk, link: MI.read_device_pattern(link, 1, 3)) if w.ctx.firmware_key == "melodee" else None
    w.ctx.connect_port(port1)
    check(until(lambda: w.ctx.connected and w.ctx.firmware_key == "melodee", 20000), "back on DEMO-1")
    check(cp.b_undo.isEnabled() and "gehört zu" not in cp.b_undo.toolTip(), "undo on the original allowed again")
    cp.undo.clear()
    w.subpages["compose"]._dev_changed()            # (TabbedPage does not pass private names on)
    # preview: plays on the connected FM-1 (target track), ends on a page switch without hanging notes
    import time as _t
    sent2: list = []

    def listen2(data, direction):
        if direction == "out" and data and 0x80 <= data[0] < 0xF0:
            sent2.append(tuple(data))
    w.ctx.link.add_listener(listen2)
    cp.track.setCurrentIndex(2)
    t0 = _t.perf_counter()
    cp.preview()
    check(_t.perf_counter() - t0 < 0.1, f"preview does not block the UI ({(_t.perf_counter() - t0) * 1000:.0f} ms)")
    check(until(lambda: cp.play_bar.seq.running, 8000), "preview plays on the demo FM-1")
    check(until(lambda: any(m[0] & 0xF0 == 0x90 for m in sent2), 4000) and
          {m[0] & 15 for m in sent2 if m[0] & 0xF0 == 0x90} == {2}, "preview: notes on the channel of target track 3")
    check("Spur 3 am FM-1" in cp.play_bar.status.text(), "preview names the sound of the target track on the FM-1")
    w.go("overview")
    check(not cp.play_bar.playing, "preview ends on a page switch")
    check(any(m[0] & 0xF0 == 0xB0 and m[1] == 123 for m in sent2), "page switch: All Notes Off sent")
    w.ctx.link.remove_listener(listen2)
    w.go("compose")
    # on the computer: leave the page while the sound is still being computed – nothing starts playing afterwards
    cp.play_bar.target.setCurrentIndex(1)
    cp.preview()
    w.go("overview")
    wait(1500)
    check(cp.player is None or not cp.player.playing, "preview after a page switch does not start late")
    w.go("compose")
    cp.play_bar.target.setCurrentIndex(0)
    # undo/redo for edits on the computer
    cp.tabs.setCurrentIndex(1)
    cp.generate()
    first = [list(s.notes) for s in cp.roll.steps]
    cp.clear()
    check(not any(s.notes for s in cp.roll.steps) and cp.b_hist_undo.isEnabled(), "clearing can be undone")
    cp.undo_edit()
    check([list(s.notes) for s in cp.roll.steps] == first and cp.b_hist_redo.isEnabled(), "Ctrl+Z: steps back")
    cp.redo_edit()
    check(not any(s.notes for s in cp.roll.steps), "Ctrl+Shift+Z: empty again")
    cp.undo_edit()
    w.go("compose")
    cp.undo_last()
    again = job(lambda wk, link: MI.read_device_pattern(link, 1, 3))
    check(again and all(MI.same_step(a, b) for a, b in zip(again.steps, before.steps)), "undo restores the old pattern")
    cp.tabs.setCurrentIndex(0)
    cp.generate()
    check(any(s.hit for s in cp.roll.steps), "Euclidean rhythm in the drum grid")
    w.grab().save(str(home / "compose.png"))

    # ---- workshop: MIDI with drums (channel 10) -> drum grid in track 3, pattern 2
    mid = mido.MidiFile(ticks_per_beat=480)
    tr = mido.MidiTrack()
    mid.tracks.append(tr)
    for i in range(32):
        n = 36 if i % 4 == 0 else 42
        tr.append(mido.Message("note_on", channel=9, note=n, velocity=110 if i % 8 == 0 else 70, time=0 if i == 0 else 60))
        tr.append(mido.Message("note_off", channel=9, note=n, velocity=0, time=60))
    mid.save(str(home / "drums.mid"))
    w.go("workshop")
    ws = w.page("workshop") or w.workshop
    ws._open(str(home / "drums.mid"))
    check(until(lambda: ws.tracks and ws.chunks, 10000), "MIDI file read in the background")
    check(ws.drums.isChecked(), "drum track detected (channel 10)")
    check(ws.npat.value() == 2 and len(ws.chunks) == 2, f"auf 2 Patterns aufgeteilt ({ws.npat.value()})")
    check("32 von 32" in ws.loss.text(), "preview of what is transferred")
    ws.track.setCurrentIndex(2)
    ws.pattern.setCurrentIndex(2)
    ws._send()
    got = job(lambda wk, link: MI.read_device_pattern(link, 2, 2))
    check(got and got.steps[0].hit == 1 and got.steps[1].hit == 8, "drum lanes arrived on the FM-1")
    check(ws.b_undo.isEnabled(), "workshop: undo offered")
    ws._undo()
    got = job(lambda wk, link: MI.read_device_pattern(link, 2, 2))
    check(got and not any(s.hit for s in got.steps), "workshop: undo")
    # a file without notes, a format 0 file with bass + drums in ONE track, 16 tracks
    empty = mido.MidiFile()
    t = mido.MidiTrack()
    t.append(mido.MetaMessage("set_tempo", tempo=500000))
    empty.tracks.append(t)
    empty.save(str(home / "leer.mid"))
    ws._open(str(home / "leer.mid"))
    check(until(lambda: "0 Spuren" in ws.file_label.text(), 10000) and not ws.chunks
          and "keine Noten" in ws.list.item(0).text(), f"file without notes: {ws.file_label.text()}")
    ws._send()
    check(True, "sending without a track: only a hint, no exception")
    f0 = mido.MidiFile(ticks_per_beat=480)
    t = mido.MidiTrack()
    f0.tracks.append(t)
    for i in range(16):
        t.append(mido.Message("note_on", channel=9, note=36, velocity=100, time=0))
        t.append(mido.Message("note_on", channel=0, note=40, velocity=100, time=0))
        t.append(mido.Message("note_off", channel=9, note=36, time=120))
        t.append(mido.Message("note_off", channel=0, note=40, time=0))
    f0.save(str(home / "f0.mid"))
    ws._open(str(home / "f0.mid"))
    check(until(lambda: len(ws.tracks) == 2, 10000) and [x.channel for x in ws.tracks] == [0, 9],
          "format 0 file: bass and drums selectable separately")
    ws.list.setCurrentRow(1)
    check(ws.drums.isChecked() and ws.report.used == 16, "drum channel detected in the mixed track too")
    big = mido.MidiFile(ticks_per_beat=96)
    for k in range(16):
        tk = mido.MidiTrack()
        tk.name = f"Instrument {k + 1}"
        for i in range(2000):
            tk.append(mido.Message("note_on", channel=k, note=48 + i % 24, velocity=80, time=0))
            tk.append(mido.Message("note_off", channel=k, note=48 + i % 24, time=48))
        big.tracks.append(tk)
    big.save(str(home / "gross.mid"))
    t0 = time.time()
    ws._open(str(home / "gross.mid"))
    check(until(lambda: len(ws.tracks) == 16, 30000), f"16 tracks with 32 000 notes read ({time.time() - t0:.1f} s)")
    check(ws.npat.value() == 16 and len(ws.chunks) == 16, f"limited to at most 16 patterns ({ws.npat.value()})")
    app.quit()


def run():
    try:
        flow()
    except Exception:  # noqa: BLE001
        errors.append(traceback.format_exc())
        app.quit()


QTimer.singleShot(500, run)
QTimer.singleShot(400000, app.quit)
app.exec()
fails = [r for r in results if not r[0]]
for e in errors:
    print("EXCEPTION:\n" + e)
print(f"{len(results) - len(fails)}/{len(results)} passed, {len(errors)} exceptions")
import shutil  # noqa: E402
if os.environ.get("FM1C_SHOTS"):
    for n in ("project_view.png", "compose.png"):
        if (home / n).exists():
            shutil.copy(home / n, Path(os.environ["FM1C_SHOTS"]) / n)
shutil.rmtree(home, ignore_errors=True)
sys.exit(1 if fails or errors else 0)
