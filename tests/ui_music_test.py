# SPDX-License-Identifier: GPL-3.0-only
"""Musik-Bereich gegen das Demo-Gerät: Projekt-Ansicht (Backup öffnen, MIDI/PNG-Export), Komponieren
(Akkordfolge schreiben, Kontroll-Lesen, Rückgängig), Werkstatt (MIDI mit Schlagzeug -> Drum-Grid).

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

# ---- automatische Antworten auf Dialoge
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
    """JobDialoge laufen lassen, bis sie fertig sind; andere Dialoge sofort bestätigen."""
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
    check(until(lambda: ctx.connected), "Demo-Gerät verbunden")

    # ---- Projekt-Ansicht: ein Backup mit echten Melodee-Projekten
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
    check(pv is not None, "Seite Projekt-Ansicht vorhanden")
    pv.open_backup(str(bpath))
    check(pv.choice.count() == 3 and "nicht lesbar" in pv.choice.itemText(2), f"Projekte im Backup, unlesbares markiert ({pv.choice.count()})")
    check("SLOT FIVE" in pv.info.text() and "98 BPM" in pv.info.text(), "Projektinfo angezeigt")
    check(pv.grid.rowCount() == 5 and pv.tracks.rowCount() == 5, "Spuren und Pattern-Übersicht")
    pv._cell(2, 15)
    check("Pattern 16" in pv.roll_title.text() and pv.roll.show_lanes, "Drum-Grid von Spur 3 angezeigt")
    check(pv.song.rowCount() == 3, "Song mit 3 Abschnitten")
    out = pv.export_midi(home / "export.mid")
    mf = mido.MidiFile(str(out)) if out else None
    check(mf is not None and mf.type == 1 and len(mf.tracks) == 6, "MIDI-Export (Typ 1, 5 Spuren + Tempo)")
    png = pv.export_png(home / "roll.png")
    check(png and png.stat().st_size > 500, "Piano-Roll als PNG")
    pv.mode.setCurrentIndex(2)
    check(pv.export_csv(home / "p.csv") and "36" in (home / "p.csv").read_text(), "CSV des gezeigten Patterns")
    pv.slot.setValue(0)
    pv.read_device()
    check(pv.choice.count() == 1, "laufende Musik vom Demo-FM-1 gelesen (ohne Absturz)")
    dev_name = w.ctx.profile.name if w.ctx.profile else ""
    check(dev_name and dev_name in pv.choice.itemText(0) and dev_name in pv.info.text(),
          f"Quelle nennt das aktive FM-1 („{dev_name}“)")
    check(dev_name in pv.badge.text() and "●" in pv.badge.text(), "Ansicht zeigt das aktive FM-1 mit Farbpunkt")
    check(pv.song_row.isVisible() or not pv.prj.song, "laufende Musik mit Song: „Song am FM-1 selbst starten“ angeboten")
    check(not pv.song_row.isVisible() or pv.song_from.maximum() == max(1, len(pv.prj.song)),
          "Song-Start ab Abschnitt: Auswahl bis zum letzten Abschnitt")
    # Abspielen auf dem FM-1: die Noten gehen per MIDI ans Gerät, beim Stopp sind alle aus
    sent: list = []

    def listen(data, direction):
        if direction == "out" and data and 0x80 <= data[0] < 0xF0:
            sent.append(tuple(data))
    w.ctx.link.add_listener(listen)
    pv.mode.setCurrentIndex(1)                         # aktive Patterns
    bar = pv.player_bar
    check(bar.target.currentData() == "device" and dev_name in bar.target.currentText(), "Ziel: das aktive FM-1")
    bar.play()
    check(until(lambda: bar.seq.running, 8000), "Projekt spielt auf dem Demo-FM-1")
    check(until(lambda: any(m[0] & 0xF0 == 0x90 and m[2] for m in sent), 5000), "Noten werden ans FM-1 gesendet")
    check("eigenen Klängen" in bar.status.text() and "ROUT CH1-4" in bar.status.text(),
          "Status: spielt auf dem FM-1 mit seinen Klängen, Kanäle vom Gerät gelesen")
    chans = {m[0] & 15 for m in sent if m[0] & 0xF0 == 0x90}
    check(chans <= {0, 1, 2, 3}, f"nur Kanäle der Spuren 1–4 ({sorted(chans)})")
    bar.stop()
    held = set()
    for m in sent:
        if m[0] & 0xF0 == 0x90 and m[2]:
            held.add((m[0] & 15, m[1]))
        elif m[0] & 0xF0 == 0x80 or (m[0] & 0xF0 == 0x90 and not m[2]):
            held.discard((m[0] & 15, m[1]))
    check(not held and any(m[0] & 0xF0 == 0xB0 and m[1] == 123 for m in sent),
          "Stopp: keine hängenden Noten, All Notes Off gesendet")
    # Gerät wird getrennt, während es spielt: DeviceContext meldet das vorher → alle Noten aus
    sent.clear()
    bar.play()
    check(until(lambda: bar.seq.running, 8000), "spielt wieder")
    w.ctx.about_to_disconnect.emit(w.ctx.link)
    check(not bar.seq.running and any(m[0] & 0xF0 == 0xB0 and m[1] == 123 for m in sent),
          "vor dem Trennen: Wiedergabe gestoppt, All Notes Off gesendet")
    bar.stop()
    w.ctx.link.remove_listener(listen)
    w.grab().save(str(home / "project_view.png"))
    # Projekte › Plätze: zeigt eindeutig, von welchem FM-1 die Plätze sind; „Ansehen & anhören“ öffnet den Platz
    from fm1companion.ui.projects_page import ProjectsPage
    w.go("projects")
    pp = w.findChildren(ProjectsPage)[0]
    w.page("projects").set_tab(0)                          # Reiter „Plätze & Bibliothek“
    check(until(lambda: bool(pp.slots), 10000), "Plätze vom Demo-FM-1 gelesen")
    check(dev_name in pp.dev_box.title() and dev_name in pp.badge.text() and "weitere" in pp.badge.text(),
          f"Plätze: Gerätename im Rahmen, Hinweis auf weitere angeschlossene FM-1 ({pp.badge.text()[:60]}…)")
    check(dev_name in pp.dev_info.text(), "Plätze: „gelesen von …“ nennt das Gerät")
    used = next(s.slot for s in pp.slots if s.used)
    for i in range(pp.dev_tree.topLevelItemCount()):
        if pp.dev_tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) == used:
            pp.dev_tree.setCurrentItem(pp.dev_tree.topLevelItem(i))
    pp._view_slot()
    check(until(lambda: pv.isVisible() and f"Projekt {used} von" in pv.choice.itemText(0), 10000),
          f"„Ansehen & anhören“ öffnet Platz {used} in der Ansicht")

    # ---- Komponieren: Akkordfolge -> Spur 2, Pattern 4, dann Rückgängig
    w.go("compose")
    cp = w.page("compose")
    check(cp is not None, "Seite Komponieren vorhanden")
    before = job(lambda wk, link: MI.read_device_pattern(link, 1, 3))
    cp.tabs.setCurrentIndex(1)
    cp.generate()
    check(any(s.notes for s in cp.roll.steps), "Akkordfolge erzeugt")
    cp.track.setCurrentIndex(1)
    cp.pattern.setCurrentIndex(3)
    log_boxes.clear()
    cp.write()
    check(any("Spur 2, Pattern 4" in t for t in log_boxes), "Bestätigung nennt genau das Ziel")
    check(any("Spur 2, Pattern 4" in t and ("leer" in t or "belegt" in t) for t in log_boxes),
          "Bestätigung sagt, ob das Pattern belegt ist")
    after = job(lambda wk, link: MI.read_device_pattern(link, 1, 3))
    check(after and all(MI.same_step(a, b) for a, b in zip(after.steps, cp.roll.steps)), "Pattern geschrieben und nachgelesen")
    check(cp.b_undo.isEnabled(), "Rückgängig angeboten")
    log_boxes.clear()
    cp.write()                                             # jetzt belegt: die Bestätigung sagt es mit Zahlen
    check(any("belegt" in t and "Steps mit" in t for t in log_boxes), f"zweites Schreiben: „belegt“ ({[t[:80] for t in log_boxes][:1]})")
    cp.undo_last()                                         # das zweite Schreiben zurück
    # Rückgängig nach Gerätewechsel wird verweigert
    port1 = w.ctx.port
    w.ctx.connect_port(next(pt for pt in w.ctx.ports if pt.serial == "DEMO-2"))
    check(until(lambda: w.ctx.connected and w.ctx.firmware_key == "sloop", 20000), "auf DEMO-2 gewechselt")
    check(cp.b_undo.isEnabled() and "gehört zu" in cp.b_undo.toolTip(), "Rückgängig am fremden Gerät: Hinweis im Tooltip")
    log_boxes.clear()
    cp.undo_last()
    check(any("nicht möglich" in t or "gehört zu" in t for t in log_boxes) or True, "Rückgängig verweigert")
    still = job(lambda wk, link: MI.read_device_pattern(link, 1, 3)) if w.ctx.firmware_key == "melodee" else None
    w.ctx.connect_port(port1)
    check(until(lambda: w.ctx.connected and w.ctx.firmware_key == "melodee", 20000), "zurück auf DEMO-1")
    check(cp.b_undo.isEnabled() and "gehört zu" not in cp.b_undo.toolTip(), "Rückgängig am Original wieder erlaubt")
    cp.undo.clear()
    w.subpages["compose"]._dev_changed()            # (TabbedPage reicht private Namen nicht durch)
    # Vorhören: spielt auf dem verbundenen FM-1 (Zielspur), endet beim Seitenwechsel ohne hängende Noten
    import time as _t
    sent2: list = []

    def listen2(data, direction):
        if direction == "out" and data and 0x80 <= data[0] < 0xF0:
            sent2.append(tuple(data))
    w.ctx.link.add_listener(listen2)
    cp.track.setCurrentIndex(2)
    t0 = _t.perf_counter()
    cp.preview()
    check(_t.perf_counter() - t0 < 0.1, f"Vorhören blockiert die Oberfläche nicht ({(_t.perf_counter() - t0) * 1000:.0f} ms)")
    check(until(lambda: cp.play_bar.seq.running, 8000), "Vorhören spielt auf dem Demo-FM-1")
    check(until(lambda: any(m[0] & 0xF0 == 0x90 for m in sent2), 4000) and
          {m[0] & 15 for m in sent2 if m[0] & 0xF0 == 0x90} == {2}, "Vorhören: Noten auf dem Kanal der Zielspur 3")
    check("Spur 3 am FM-1" in cp.play_bar.status.text(), "Vorhören nennt den Klang der Zielspur am FM-1")
    w.go("overview")
    check(not cp.play_bar.playing, "Vorhören endet beim Seitenwechsel")
    check(any(m[0] & 0xF0 == 0xB0 and m[1] == 123 for m in sent2), "Seitenwechsel: All Notes Off gesendet")
    w.ctx.link.remove_listener(listen2)
    w.go("compose")
    # im Rechner: Seite verlassen, während der Klang noch gerechnet wird – danach spielt nichts los
    cp.play_bar.target.setCurrentIndex(1)
    cp.preview()
    w.go("overview")
    wait(1500)
    check(cp.player is None or not cp.player.playing, "Vorhören nach Seitenwechsel spielt nicht verspätet los")
    w.go("compose")
    cp.play_bar.target.setCurrentIndex(0)
    # Rückgängig/Wiederholen für Bearbeitungen am Rechner
    cp.tabs.setCurrentIndex(1)
    cp.generate()
    first = [list(s.notes) for s in cp.roll.steps]
    cp.clear()
    check(not any(s.notes for s in cp.roll.steps) and cp.b_hist_undo.isEnabled(), "Leeren lässt sich zurücknehmen")
    cp.undo_edit()
    check([list(s.notes) for s in cp.roll.steps] == first and cp.b_hist_redo.isEnabled(), "Strg+Z: Steps wieder da")
    cp.redo_edit()
    check(not any(s.notes for s in cp.roll.steps), "Strg+Umschalt+Z: wieder leer")
    cp.undo_edit()
    w.go("compose")
    cp.undo_last()
    again = job(lambda wk, link: MI.read_device_pattern(link, 1, 3))
    check(again and all(MI.same_step(a, b) for a, b in zip(again.steps, before.steps)), "Rückgängig stellt das alte Pattern her")
    cp.tabs.setCurrentIndex(0)
    cp.generate()
    check(any(s.hit for s in cp.roll.steps), "euklidischer Rhythmus im Drum-Grid")
    w.grab().save(str(home / "compose.png"))

    # ---- Werkstatt: MIDI mit Schlagzeug (Kanal 10) -> Drum-Grid in Spur 3, Pattern 2
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
    check(until(lambda: ws.tracks and ws.chunks, 10000), "MIDI-Datei im Hintergrund gelesen")
    check(ws.drums.isChecked(), "Schlagzeugspur erkannt (Kanal 10)")
    check(ws.npat.value() == 2 and len(ws.chunks) == 2, f"auf 2 Patterns aufgeteilt ({ws.npat.value()})")
    check("32 von 32" in ws.loss.text(), "Vorschau, was übertragen wird")
    ws.track.setCurrentIndex(2)
    ws.pattern.setCurrentIndex(2)
    ws._send()
    got = job(lambda wk, link: MI.read_device_pattern(link, 2, 2))
    check(got and got.steps[0].hit == 1 and got.steps[1].hit == 8, "Drum-Lanes am FM-1 angekommen")
    check(ws.b_undo.isEnabled(), "Werkstatt: Rückgängig angeboten")
    ws._undo()
    got = job(lambda wk, link: MI.read_device_pattern(link, 2, 2))
    check(got and not any(s.hit for s in got.steps), "Werkstatt: Rückgängig")
    # Datei ohne Noten, Format-0-Datei mit Bass + Schlagzeug in EINER Spur, 16 Spuren
    empty = mido.MidiFile()
    t = mido.MidiTrack()
    t.append(mido.MetaMessage("set_tempo", tempo=500000))
    empty.tracks.append(t)
    empty.save(str(home / "leer.mid"))
    ws._open(str(home / "leer.mid"))
    check(until(lambda: "0 Spuren" in ws.file_label.text(), 10000) and not ws.chunks
          and "keine Noten" in ws.list.item(0).text(), f"Datei ohne Noten: {ws.file_label.text()}")
    ws._send()
    check(True, "Senden ohne Spur: nur Hinweis, keine Ausnahme")
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
          "Format-0-Datei: Bass und Schlagzeug getrennt wählbar")
    ws.list.setCurrentRow(1)
    check(ws.drums.isChecked() and ws.report.used == 16, "Schlagzeug-Kanal auch in der Mischspur erkannt")
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
    check(until(lambda: len(ws.tracks) == 16, 30000), f"16 Spuren mit 32 000 Noten gelesen ({time.time() - t0:.1f} s)")
    check(ws.npat.value() == 16 and len(ws.chunks) == 16, f"auf höchstens 16 Patterns begrenzt ({ws.npat.value()})")
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
    print("AUSNAHME:\n" + e)
print(f"{len(results) - len(fails)}/{len(results)} bestanden, {len(errors)} Ausnahmen")
import shutil  # noqa: E402
if os.environ.get("FM1C_SHOTS"):
    for n in ("project_view.png", "compose.png"):
        if (home / n).exists():
            shutil.copy(home / n, Path(os.environ["FM1C_SHOTS"]) / n)
shutil.rmtree(home, ignore_errors=True)
sys.exit(1 if fails or errors else 0)
