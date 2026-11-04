# SPDX-License-Identifier: GPL-3.0-only
"""QA fixes (page level) against the demo devices – with dialogs answered automatically.

Checked: undo belongs to the device (Compose, FM Voices, workshop, Presets, sample slots), song chain
with snapshot, partial errors keep undo, Presets state after a device switch, command search without
duplicates, help, device comparison A≠B, toast position, German buttons, small texts.

  flock /tmp/fm1c-ui.lock timeout 600 env QT_QPA_PLATFORM=offscreen python3 tests/ui_qafix_test.py
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
home = Path(tempfile.mkdtemp(prefix="fm1c-qafix-"))
for k, sub in (("XDG_CONFIG_HOME", ".config"), ("XDG_DATA_HOME", ".local/share"), ("XDG_CACHE_HOME", ".cache")):
    os.environ[k] = str(home / sub)
os.environ["FM1C_DEMO"] = "1"
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu")

errors: list[str] = []
sys.excepthook = lambda t, v, tb: errors.append("".join(traceback.format_exception(t, v, tb)))

import PyQt6.QtWebEngineWidgets  # noqa: E402,F401
from PyQt6.QtCore import QCoreApplication, QEventLoop, Qt, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QInputDialog, QMessageBox  # noqa: E402

QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
app = QApplication(sys.argv)

from fm1companion import config  # noqa: E402

s = config.load()
s.user_root = str(home / "FM-1")
s.check_on_start = False
samp = home / "samples"
samp.mkdir()
for n in range(2):
    with wave.open(str(samp / f"drum {n}.wav"), "wb") as wv:
        wv.setnchannels(1)
        wv.setsampwidth(2)
        wv.setframerate(44100)
        wv.writeframes(b"".join(struct.pack("<h", int(9000 * math.sin(t / (5 + n)))) for t in range(6000)))
s.sample_roots = [str(samp)]
config.save(s)
config.ensure_dirs()

boxes: list[str] = []
summaries: list[tuple] = []          # (title, ok, result line, heading) of every JobDialog
toasts: list[tuple] = []             # (text, level) of every notification


def fake_msg_exec(self):
    boxes.append(self.text())
    for b in self.buttons():
        if self.buttonRole(b) in (QMessageBox.ButtonRole.AcceptRole, QMessageBox.ButtonRole.YesRole):
            self._clicked = b
            return 0
    self._clicked = None
    return 0


QMessageBox.exec = fake_msg_exec
QMessageBox.clickedButton = lambda self: getattr(self, "_clicked", None)
QInputDialog.getText = staticmethod(lambda *a, **k: (k.get("text", "x") or "x", True))
QInputDialog.getInt = staticmethod(lambda *a, **k: (1, True))
_dialog_exec = QDialog.exec


def dialog_exec(self):
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
        summaries.append((self.windowTitle(), self.ok, self.summary, self.head.text()))
        return 1 if self.ok else 0
    return 1


QDialog.exec = dialog_exec

from fm1companion.ui import notify as _notify_mod  # noqa: E402

_orig_notify = _notify_mod.notify


def _capture_notify(text, level="info", action=None):
    toasts.append((text, level))
    return _orig_notify(text, level, action)


_notify_mod.notify = _capture_notify
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


def switch(serial: str):
    ctx = w.ctx
    ctx.connect_port(next(p for p in ctx.ports if p.serial == serial))
    if serial == "DEMO-3":
        return until(lambda: ctx.port and ctx.port.serial == serial and ctx.midi_connected and not ctx.connected, 15000)
    return until(lambda: ctx.connected and ctx.port and ctx.port.serial == serial, 15000)


def device(fn):
    """run fn(link) exclusively on the current demo device and return the result."""
    d = w.ctx.job(w, "Test", lambda wk, link: fn(link))
    return d.result_value if d.ok else None


def refused() -> bool:
    return any("gehört zu" in b and "wieder verbinden" in b for b in boxes[-2:])


def make_midi(path: Path, bars: int = 2):
    import mido
    mf = mido.MidiFile(ticks_per_beat=96)
    tr = mido.MidiTrack()
    mf.tracks.append(tr)
    tr.append(mido.MetaMessage("track_name", name="Lead", time=0))
    for i in range(bars * 16):
        tr.append(mido.Message("note_on", note=60 + i % 12, velocity=100, time=0))
        tr.append(mido.Message("note_off", note=60 + i % 12, velocity=0, time=24))
    mf.save(str(path))


def flow():
    from fm1companion import midi_import as MI, presetlib as pl, samples as S
    from fm1companion.ui import common
    ctx = w.ctx
    check(until(lambda: ctx.connected), "demo device connected")
    check(switch("DEMO-1"), "DEMO-1 (Melodee) active")

    # ================================================== 1. Compose: undo per device
    cp = w.page("compose")
    cp.track.setCurrentIndex(0)
    cp.pattern.setCurrentIndex(2)
    cp.write()
    check(len(cp.undo) == 1 and cp.undo.matches(ctx), "Compose: writing on DEMO-1 creates an undo")
    check(switch("DEMO-2"), "switched to DEMO-2")
    check(cp.undo.matches(ctx) is False and len(cp.undo) == 1, "undo stays, but belongs to DEMO-1")
    n_before = len(boxes)
    cp.undo_last()
    check(refused() and len(cp.undo) == 1 and len(boxes) > n_before,
          "Compose: undo on DEMO-2 refused (\"gehört zu … wieder verbinden\")")
    check("FM-1" in boxes[-1] or "DEMO" in boxes[-1] or "Melodee" in boxes[-1], f"the message names the device: {boxes[-1][:70]}")
    check(switch("DEMO-1"), "back on DEMO-1")
    cp.undo_last()
    check(len(cp.undo) == 0, "Compose: undone on DEMO-1 again")

    # ================================================== 2. workshop: song chain with snapshot
    wp = w.workshop
    boxes.clear()
    wp._send()
    check(any("noch keine MIDI-Datei" in b.lower() or "MIDI-Datei öffnen" in b for b in boxes),
          "workshop: without a file \"Play to the FM-1\" says why not")
    mid = home / "lead.mid"
    make_midi(mid, bars=2)
    wp._open(str(mid))
    until(lambda: not wp.file_label.text().startswith("lese"), 10000)   # reads in the background
    check("1 Spur mit Noten" in wp.file_label.text(), f"singular correct: {wp.file_label.text()}")
    wp.npat.setValue(2)
    wp.track.setCurrentIndex(0)
    wp.pattern.setCurrentIndex(wp.pattern.findData(4))
    wp.song.setChecked(True)
    old_song = [([3, 0, 0, 0], 2), ([4, 0, 0, 0], 1)]
    check(device(lambda link: MI.write_song(link, old_song)) == 0, "known song chain set beforehand")
    wp._send()
    e = wp.undo.peek()
    check(e is not None and len(e["patterns"]) == 2 and e["song"] is not None and e["song"].rows == old_song,
          "workshop: 2 patterns + old song chain read for undo")
    now = device(MI.read_song)
    check(now is not None and [r[0][0] for r in now.rows] == [4, 5], f"new song chain written ({now and now.rows})")
    wp._undo()
    back = device(MI.read_song)
    check(back is not None and back.rows == old_song and not wp.undo, "workshop: undo restores the song chain")

    # ================================================== 3. workshop: partial error -> undo stays
    orig_write = MI.write_device_pattern
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 2:
            raise MI.ProtocolError("simulierter Abbruch")
        return orig_write(*a, **k)
    MI.write_device_pattern = flaky
    wp.song.setChecked(False)
    try:
        wp._send()
    finally:
        MI.write_device_pattern = orig_write
    e = wp.undo.peek()
    check(e is not None and len(e["patterns"]) == 2, "workshop: after aborting at pattern 2 both snapshots stay")
    check(switch("DEMO-2"), "DEMO-2 for the workshop device test")
    boxes.clear()
    wp._undo()
    check(refused() and wp.undo, "workshop: undo on another device refused")
    check(switch("DEMO-1"), "back on DEMO-1")
    wp._undo()
    check(not wp.undo, "workshop: partial undo run on DEMO-1")

    # ================================================== 4. Presets: partial error, device switch
    pp = w.page("presets")
    w.go("presets")
    check(until(lambda: bool(pp.slots) and not pp.busy, 15000), "Presets: list read")
    used = [x["slot"] for x in pp.slots if x["used"]][:2]
    for i in range(pp.dev.topLevelItemCount()):
        it = pp.dev.topLevelItem(i)
        it.setSelected(it.data(0, Qt.ItemDataRole.UserRole) in used)
    orig_erase = pl.SharedBank.erase
    cnt = {"n": 0}

    def flaky_erase(self, k):
        cnt["n"] += 1
        if cnt["n"] == 2:
            raise pl.PresetError("simulierter Abbruch")
        return orig_erase(self, k)
    pl.SharedBank.erase = flaky_erase
    try:
        pp.erase()
        check(until(lambda: not pp.busy, 10000), "Presets: deleting finished (with an error)")
    finally:
        pl.SharedBank.erase = orig_erase
    check(len(pp.undo) == 2, f"Presets: a partial error keeps undo for both slots read ({len(pp.undo)})")
    check(until(lambda: not pp.busy, 10000), "Presets ready")
    check(switch("DEMO-2"), "DEMO-2 for the Presets device test")
    boxes.clear()
    pp.do_undo()
    check(refused() and len(pp.undo) == 2, "Presets: undo on another device refused")
    check(not pp.b_undo.isEnabled(), "Presets: undo button off on a foreign device")
    check(switch("DEMO-3"), "DEMO-3 (MIDI only) connected")
    check(until(lambda: "Plätzen belegt" not in pp.state.text(), 5000) and "keine Preset-Bibliothek" in pp.state.text(),
          f"Presets state renewed after DEMO-3: {pp.state.text()[:80]}")
    check(pp.empty.isVisible() or "keine Preset-Bibliothek" in pp.empty.text(), "Presets: empty state for firmware without a library")
    check(switch("DEMO-1"), "back on DEMO-1")
    w.go("presets")
    check(until(lambda: bool(pp.slots) and not pp.busy, 15000), "Presets: list read again")
    pp.do_undo()
    check(until(lambda: len(pp.undo) == 1 and not pp.busy, 10000), "Presets: undo run on DEMO-1")

    # ================================================== 5. FM Voices: undo per device
    vp = w.page("voices")
    from fm1companion import dx7
    vp.send_to_slot(dx7.random_voice(), slot=200)
    check(until(lambda: len(vp.undo) == 1, 15000), "FM Voices: FM6 preset written, undo created")
    check(switch("DEMO-2"), "DEMO-2 for the voices device test")
    boxes.clear()
    vp.do_undo()
    check(refused() and len(vp.undo) == 1, "FM Voices: undo on another device refused")
    widths = [b.width() >= b.fontMetrics().horizontalAdvance(b.text()) + 8 for b in vp.keys]
    w.go("voices")
    wait(200)
    widths = [b.width() >= b.fontMetrics().horizontalAdvance(b.text()) + 8 for b in vp.keys]
    check(all(widths) and vp.keys[-1].text() == "C5", "FM Voices: note buttons C4–C5 readably wide")

    # ================================================== 6. sample slots: backup + undo (DEMO-2 = SLOOP)
    sp = w.samples
    w.go("samples")
    check(until(lambda: sp.table.topLevelItemCount() >= 2, 15000), "samples read")
    sp.read_slots()
    check(until(lambda: sp.slot_list.count() == 4), "4 sample slots read")
    sp.table.selectAll()
    sp._add_to_kit()
    sp.slot_list.setCurrentRow(1)
    before = device(lambda link: S.read_slot(link, 1, ctx.firmware_key))
    boxes.clear()
    sp._upload()
    check(any("Rückgängig" in b and "USR2" in b for b in boxes), "kit into slot: the confirmation names slot and undo")
    check(len(sp.slot_undo) == 1 and sp.slot_undo.peek()[1] == before, "kit into slot: the old content read beforehand")
    after = device(lambda link: S.read_slot(link, 1, ctx.firmware_key))
    check(after and after != before, "slot rewritten")
    sp.slot_list.setCurrentRow(1)
    sp._erase_slot()
    check(len(sp.slot_undo) == 2 and device(lambda link: S.read_slot(link, 1, ctx.firmware_key)) == b"",
          "clear slot: backed up beforehand, then empty")
    sp._slot_undo()
    check(device(lambda link: S.read_slot(link, 1, ctx.firmware_key)) == after, "undo: slot content back")
    sp._slot_undo()
    check(device(lambda link: S.read_slot(link, 1, ctx.firmware_key)) == before and not sp.slot_undo,
          "undo: original slot content back")

    # ================================================== 6b. feedback: every action says what happened
    def summary(title_part):
        return next((x for x in reversed(summaries) if title_part in x[0] and x[1]), None)
    for part, must in (("Pattern schreiben", "geschrieben und nachgelesen"), ("MIDI → Pattern", "geschrieben"),
                       ("Pattern wiederherstellen", "alten Inhalt"), ("Kit in USR", "liegt jetzt in USR"),
                       ("Slot leeren", "ist leer"), ("wiederherstellen", "wieder den alten Inhalt")):
        x = summary(part)
        check(x is not None and x[1] and x[3] == "Fertig" and must in x[2],
              f"feedback \"{part}\": {x[2][:70] if x else '–'}")
    x = next((x for x in summaries if "MIDI → Pattern" in x[0] and not x[1]), None)
    check(x is not None and x[3] == "Fehlgeschlagen" and "Rückgängig" in x[2],
          f"partial error: \"Fehlgeschlagen\" (failed) + what stays ({x[2][:70] if x else '–'})")
    check(all(x[2] for x in summaries), "no dialog without a result line")
    toasts.clear()
    cp.save_midi(str(home / "idee.mid"))
    check(any("MIDI-Datei gespeichert" in t for t, lv in toasts), "Compose: toast after \"Save as MIDI\"")
    sp.kit.path = str(home / "test.fm1kit")
    sp._kit_save()
    check(any("gespeichert" in t and "Kit" in t for t, lv in toasts), "Samples: toast after \"Save kit\"")
    w.projects.read_slots()
    check(until(lambda: bool(w.projects.slots)), "Projects: slots read")
    if any(x.used for x in w.projects.slots):
        w.projects._export_all()
        x = summary("Projekte holen")
        check(x is not None and "in die Bibliothek geholt" in x[2] and "nichts geändert" in x[2],
              f"fetch projects: result + \"nichts geändert\" (nothing changed) ({x[2][:60] if x else '–'})")

    # ================================================== 7. small things
    from fm1companion.ui.palette import collect
    titles = [e.title for e in collect(w)]
    check(titles.count("Backups") == 1 and titles.count("Simulator") == 1,
          f"command search: Backups {titles.count('Backups')}×, Simulator {titles.count('Simulator')}×")
    dup = sorted({t for t in titles if titles.count(t) > 1})
    check(not dup, f"command search without duplicate titles ({dup})")
    from fm1companion.ui.pages.help import topics
    tps = {k: (t, md) for k, t, md in topics()}
    alltext = " ".join(md for _, md in tps.values())
    check(any(t == "V16 – Hinweis" for t, _ in tps.values()), "help: topic \"V16 – Hinweis\" (V16 – note)")
    v16 = next(md for t, md in tps.values() if t.startswith("V16"))
    check("Stufe 2" in v16 and "Haupt-FM-1" in v16 and "nicht bestätigt" in v16, "V16 note: way back unclear, level 2")
    check("zwei simulierte" not in alltext and "drei simulierte" in alltext, "help: three simulated FM-1")
    check("falls vorhanden" not in alltext and "falls in deiner" not in alltext, "help: no \"falls vorhanden\" (if present)")
    check("MvaveFM1Unbricker** – hilft" not in alltext and "eingebaut" in tps["10-links"][1], "help: Unbricker described as built in")
    check(common.fw_label("FELUCCA SLOOP 2.8.3") == "SLOOP 2.8.3"
          and common.fw_label("FM-1 B – FELUCCA SLOOP 2.8.3") == "FM-1 B – SLOOP 2.8.3"
          and common.fw_label("FELUCCA v1.2") == "FELUCCA v1.2", "shows \"SLOOP\" instead of \"FELUCCA SLOOP\"")
    check(pl.engine_display_name(12) == "FM6" and pl.engine_display_name(12, ["x"] * 5) == "FM6",
          "engine names: 12 → FM6 (also with a short INFO list)")
    dp = w.page("devices")
    profs = list(ctx.store.profiles)
    dp.refresh()
    dp.a.setCurrentIndex(dp.a.findData(profs[0]))
    dp.b.setCurrentIndex(dp.b.findData(profs[0]))
    check(not dp.b_cmp.isEnabled() and dp.compare(profs[0], profs[0]) is None, "Devices: comparison A = B not possible")
    dp.b.setCurrentIndex(dp.b.findData(profs[1]))
    check(dp.b_cmp.isEnabled(), "Devices: comparison A ≠ B possible")
    from fm1companion.ui.pages.discover import EN_LABEL
    from fm1companion.i18n import _ as _t
    check(_t(EN_LABEL) == "Beschreibung des Autors (englisch)", "Discover: English description marked")
    # toast at the top right, fixed lifetime
    from fm1companion.ui.notify import center
    nc = center()
    nc.post("Testhinweis", "info")
    t = nc.toasts[-1]
    top = w.centralWidget().geometry().top()
    pos = t.mapTo(w, t.rect().topLeft())        # floating or (small window) in the notification row
    check(pos.y() < top + 40 and pos.x() + t.width() > w.width() - 60, f"toast at the top right ({pos.x()},{pos.y()})")
    check(t.lifetime == 6000, "toast: info disappears after 6 s")
    from PyQt6.QtCore import QEvent
    QApplication.sendEvent(t, QEvent(QEvent.Type.Enter))
    check(t.timer.isActive(), "the toast timer keeps running under the mouse too")
    # German buttons
    common.install_translations(app)
    bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
                          | QDialogButtonBox.StandardButton.Close)
    texts = [b.text().replace("&", "") for b in common.german_buttons(bb).buttons()]
    check("Abbrechen" in texts and "Schließen" in texts and "Cancel" not in texts, f"German standard buttons: {texts}")
    check(common.plural(1, "Pattern", "Patterns") == "1 Pattern" and common.plural(3, "Pattern", "Patterns") == "3 Patterns",
          "singular/plural")
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
fails = [r for r in results if not r[0]]
for e in errors:
    print("EXCEPTION:\n" + e)
print(f"{len(results) - len(fails)}/{len(results)} passed, {len(errors)} exceptions")
shutil.rmtree(home, ignore_errors=True)
sys.stdout.flush()
os._exit(1 if fails or errors else 0)    # without Qt cleanup (WebEngine crashes on exit otherwise)
