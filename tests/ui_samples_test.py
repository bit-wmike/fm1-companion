# SPDX-License-Identifier: GPL-3.0-only
"""Sample-Seite im Demo-Modus: Slicer baut ein Kit, Aufnahme in die Bibliothek (Demo-Signal),
Sample-Pakete (Fixtures), FM1-Open-Upload gegen das simulierte Gerät, Aufnahme-Seite mit Takt-Sync.

  flock /tmp/fm1c-ui.lock timeout 600 env QT_QPA_PLATFORM=offscreen python3 tests/ui_samples_test.py
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
sys.path.insert(0, str(ROOT / "tests"))
home = Path(tempfile.mkdtemp(prefix="fm1c-samples-"))
for k, sub in (("XDG_CONFIG_HOME", ".config"), ("XDG_DATA_HOME", ".local/share"), ("XDG_CACHE_HOME", ".cache")):
    os.environ[k] = str(home / sub)
os.environ["FM1C_DEMO"] = "1"
# beide Demo-Anschlüsse sprechen mit dem simulierten FM1 Open (kein Felucca-Protokoll)
os.environ["FM1C_DEMO_CMD"] = "\x1f".join([sys.executable, str(ROOT / "tests" / "fm1open_sim.py")])
os.environ["FM1C_PACKS_GITBASE"] = "file://" + str(home / "packs")
os.environ.pop("FM1C_PACKS_FIXTURES", None)
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu")

errors: list[str] = []
sys.excepthook = lambda t, v, tb: errors.append("".join(traceback.format_exception(t, v, tb)))

import PyQt6.QtWebEngineWidgets  # noqa: E402,F401
from PyQt6.QtCore import QCoreApplication, QEventLoop, Qt, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog, QInputDialog, QMessageBox  # noqa: E402

QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
app = QApplication(sys.argv)

from fm1companion import config  # noqa: E402

s = config.load()
s.user_root = str(home / "FM-1")
s.check_on_start = False
samp = home / "samples"
samp.mkdir()


def write(path, frames, rate=44100):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(max(-1, min(1, v)) * 30000)) for v in frames))


HITS = [0.05, 0.3, 0.55, 0.8, 1.05, 1.3]
loop = [0.0] * int(1.6 * 44100)
for t0 in HITS:
    i0 = int(t0 * 44100)
    for i in range(int(0.2 * 44100)):
        tt = i / 44100
        loop[i0 + i] += 0.8 * math.sin(2 * math.pi * (60 + 90 * math.exp(-tt * 30)) * tt) * math.exp(-tt * 14)
write(samp / "drum loop 120.wav", loop)
for n in range(2):
    write(samp / f"hit {n}.wav", [0.6 * math.sin(t / (4 + n)) * math.exp(-t / 3000) for t in range(6000)])
s.sample_roots = [str(samp)]
config.save(s)
# lokales „GitHub“-Repository für das Paket VCSL
import subprocess  # noqa: E402
prepo = home / "packs" / "vcsl"
(prepo / "Membranophones" / "Bongos").mkdir(parents=True)
(prepo / "Idiophones" / "Bells").mkdir(parents=True)
write(prepo / "Membranophones" / "Bongos" / "bongo hi.wav", [0.5 * math.sin(t / 3) for t in range(3000)])
write(prepo / "Idiophones" / "Bells" / "bell.wav", [0.5 * math.sin(t / 9) for t in range(3000)])
_g = ["git", "-c", "user.email=a@b", "-c", "user.name=t", "-C", str(prepo)]
subprocess.run(["git", "init", "-q", str(prepo)], check=True)
subprocess.run([*_g, "add", "-A"], check=True)
subprocess.run([*_g, "commit", "-qm", "x"], check=True)
subprocess.run([*_g, "config", "uploadpack.allowfilter", "true"], check=True)
config.ensure_dirs()

QMessageBox.exec = lambda self: (setattr(self, "_clicked", next((b for b in self.buttons() if self.buttonRole(b) in (
    QMessageBox.ButtonRole.AcceptRole, QMessageBox.ButtonRole.YesRole)), None)), 0)[1]
QMessageBox.clickedButton = lambda self: getattr(self, "_clicked", None)
QInputDialog.getText = staticmethod(lambda *a, **k: (k.get("text", "x"), True))
dialog_hooks = []
_dialog_exec = QDialog.exec


def dialog_exec(self):
    from fm1companion.ui.common import JobDialog
    if isinstance(self, JobDialog):
        self.show()
        t0 = time.time()
        while time.time() - t0 < 120:
            app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
            if self.buttons.button(self.buttons.StandardButton.Close).isEnabled():
                break
        self.hide()
        self.log_text = self.logv.toPlainText()
        return 1 if self.ok else 0
    if dialog_hooks:
        return dialog_hooks.pop(0)(self)
    return 1


QDialog.exec = dialog_exec

from fm1companion.ui.main_window import MainWindow  # noqa: E402

w = MainWindow()
w.show()
results = []


def check(cond, what):
    results.append((bool(cond), what))
    print(("ok   " if cond else "FAIL ") + what, flush=True)


def wait(ms):
    loop_ = QEventLoop()
    QTimer.singleShot(ms, loop_.quit)
    loop_.exec()


def until(cond, ms=10000):
    t0 = time.time()
    while time.time() - t0 < ms / 1000:
        if cond():
            return True
        wait(100)
    return False


def flow():
    sp = w.samples
    w.go("samples")
    check(until(lambda: sp.table.topLevelItemCount() == 3, 15000), "Bibliothek eingelesen (3 Dateien)")

    # ---- Slicer -> Kit
    def run_slicer(d):
        check(until(lambda: d.sr > 0 and len(d.view.points) > 0, 15000), "Slicer: Datei geladen, Schnitte gefunden")
        pts = list(d.view.points)
        check(len(pts) == len(HITS) and all(abs(a - b) < 0.012 for a, b in zip(pts, HITS)),
              f"Slicer: Transienten richtig ({[round(p, 3) for p in pts]})")
        d.mode.setCurrentIndex(1)
        d.count.setValue(4)
        check(until(lambda: len(d.view.points) == 4, 5000), "Slicer: 4 gleich lange Stücke (im Hintergrund berechnet)")
        check("4 Stücke" in d.info.text(), f"Slicer: Info-Zeile mit Mehrzahl ({d.info.text()!r})")
        d.mode.setCurrentIndex(0)
        check(until(lambda: len(d.view.points) == len(HITS), 5000), "Slicer: zurück zu Transienten")
        d.view.current = 1
        d.remove_current()
        check(len(d.view.points) == len(HITS) - 1, "Slicer: Entf entfernt den Schnitt des gewählten Stücks")
        d.view.set_points(list(pts))
        d.play_slice(1)
        d.build_kit()
        check(until(lambda: d.kit is not None, 15000), "Slicer: Kit im Hintergrund gebaut")
        return 1
    dialog_hooks.append(run_slicer)
    loop_path = str(samp / "drum loop 120.wav")
    d = sp.open_slicer(loop_path)
    check(d is not None and d.kit is not None and len(sp.kit.zones) == len(HITS), f"Kit aus Slices ({len(sp.kit.zones)} Zonen)")
    check([z.root for z in sp.kit.zones] == list(range(36, 36 + len(HITS))), "Slices auf Tasten ab C2 verteilt")
    check(all(z.path == loop_path and z.edit.end > z.edit.start for z in sp.kit.zones), "Zonen zeigen nicht-destruktiv auf den Loop")
    total, _ = __import__("fm1companion.samples", fromlist=["x"]).kit_budget(sp.kit)
    check(total <= __import__("fm1companion.samples", fromlist=["x"]).SLOT_BYTES, "Kit passt in einen Slot")

    # ---- Aufnahme in die Bibliothek
    rec = sp.recorder
    sp.tabs.setCurrentWidget(sp.rec_tab)
    until(lambda: rec.source.count() > 0, 15000)
    check(rec.select_kind("demo"), "Demo-Signal als Quelle wählbar")
    rec.preroll.setValue(0.2)
    check(rec.start_monitor(), "Mithören gestartet")
    wait(600)
    check(rec.record(), "Aufnahme gestartet")
    wait(1500)
    rec.stop()
    check(rec.result is not None and len(rec.result) > 44100, f"Aufnahme vorhanden ({0 if rec.result is None else len(rec.result)} Frames)")
    rec.name.setText("Demo Beat")
    rec.tags.setText("eigen")
    p = rec.save()
    check(p is not None and p.exists() and p.parent.name == "Aufnahmen", "Aufnahme gespeichert in Samples/Aufnahmen")
    row = sp.lib.get(str(p))
    check(row is not None and "aufnahme" in row["tags"] and "eigen" in row["tags"], "Aufnahme in der Bibliothek mit Schlagwörtern")
    # ---- Aufnahme-Seite: taktgenau (MIDI-Clock simuliert), 1 Takt bei 120 BPM = 2 s
    rp = w.recorder
    rp.no_audacity = True
    w.go("recorder")
    check(until(lambda: rp.source.findData("demo") >= 0, 15000), "Aufnahme-Seite: Quellen im Hintergrund gefunden")
    i = rp.source.findData("demo")
    rp.source.setCurrentIndex(i)
    rp._source_chosen()
    rp.mode.setCurrentIndex(1)
    rp.cb_sync.setChecked(True)
    rp.sync_mode.setCurrentIndex(0)
    rp.bars.setValue(1)
    rp.latency.setValue(0)
    rp._toggle()
    check(until(lambda: rp.rec is not None and rp.rec.running, 10000), "Aufnahme-Seite: Demo-Quelle nimmt auf, wartet auf Start")
    check(not rp.b_rec.text().startswith("Öffne"), f"Aufnahme-Knopf nach dem Start: {rp.b_rec.text()!r}")
    wait(700)
    ticks = {"n": 0}
    rp._listener([0xFA], "in")

    def tick():
        rp._listener([0xF8], "in")
        ticks["n"] += 1
        if ticks["n"] > 130:
            clk.stop()
    clk = QTimer(interval=20)                 # ~125 BPM (24 Ticks je Viertel, Timer-Raster)
    clk.timeout.connect(tick)
    clk.start()
    check(until(lambda: rp.rec is None and hasattr(rp, "last_files"), 15000), "Takt-Sync: stoppt nach 1 Takt von selbst")
    clk.stop()
    files = getattr(rp, "last_files", [])
    if files:
        from fm1companion import recorder as RR
        sr_, arr = RR.read_wav_array(files[0])
        dur = len(arr) / sr_
        expect = rp.sync.ticks_per_bar * 0.02
        check(abs(dur - expect) < 0.25, f"Takt-Sync: Länge {dur:.2f} s ≈ 1 Takt ({expect:.2f} s)")
    else:
        check(False, "Takt-Sync: keine Datei")
    check(rp.list.topLevelItemCount() >= 1, "Aufnahme in der Liste")
    rp.list.setCurrentItem(rp.list.topLevelItem(0))
    rp.list.topLevelItem(0).setSelected(True)
    out = rp._as_sample()
    check(out and sp.lib.get(str(out[0])) is not None, "Als Sample übernommen (Bibliothek)")
    d = rp._edit()
    if d is not None:
        check(until(lambda: d.a is not None, 10000), "Schnell-Editor lädt im Hintergrund")
        d.wave.start, d.wave.end = 0.1, 0.6
        d.save()                                     # speichert im Hintergrund
        until(lambda: d.saved is not None, 10000)
    check(d is not None and d.saved and d.saved.exists(), "Schnell-Editor speichert neue Datei")

    # ---- Sample-Pakete
    pt = sp.packs
    sp.tabs.setCurrentWidget(pt)
    pt.packs.setCurrentRow(next(i for i in range(pt.packs.count()) if "Dirt" in pt.packs.item(i).text()))
    check("Lizenz unklar" in pt.lic.text(), "Dirt-Samples: „Lizenz unklar“ angezeigt")
    pt.packs.setCurrentRow(0)
    check("CC0" in pt.lic.text() and "unklar" not in pt.lic.text(), "VCSL: CC0 angezeigt")
    pt.load_index()
    check(until(lambda: "vcsl" in pt.entries, 20000), "Paket-Inhalt gelesen (git, ohne API)")
    check(pt.tree.topLevelItemCount() == 2, f"2 Ordner angezeigt ({pt.tree.topLevelItemCount()})")
    pt.tree.topLevelItem(1).setCheckState(0, Qt.CheckState.Checked)
    pt.download(selected=True)
    check(until(lambda: not pt.busy, 30000) and "1 Dateien" in pt.status.text(), f"Ordner geladen ({pt.status.text()})")
    rows = [r for r in sp.lib.query("bongo")]
    check(len(rows) == 1 and "quelle:vcsl" in rows[0]["tags"] and "CC0" in rows[0]["notes"],
          "Paket-Sample in der Bibliothek mit Quelle und Lizenz")

    # ---- FM1 Open (simuliertes Gerät statt Demo-Melodee)
    ctx = w.ctx
    check(until(lambda: ctx.link is not None and getattr(ctx, "protocol", "generic") == "generic", 20000),
          "FM1-Open-Simulation verbunden (kein Felucca-Protokoll)")
    sp.tabs.setCurrentIndex(0)
    sp.read_slots()
    check(sp.open_entries is not None and [e.name for e in sp.open_entries][:3] == ["BWOOM", "PAH", "HEY"],
          "FM1 Open: Bank gelesen (8 Slots)")
    bk = list(Path(s.user_root, "Backups").rglob("*.fm1s"))
    check(len(bk) == 1, f"FM1 Open: Backup .fm1s angelegt ({[b.name for b in bk]})")
    check("von 19,55 s" in sp.open_budget.format(), f"Budget-Anzeige ({sp.open_budget.format()})")
    ok = sp.open_upload("kit")
    names = [e.name for e in sp.open_entries]
    check(ok and names[:6] == [z.name[:15] for z in sp.kit.zones[:6]] and names[6:] == ["TSI", "SHAA"],
          f"FM1 Open: Kit in Slots 1–6, 7–8 unverändert ({names})")
    check(len(list(Path(s.user_root, "Backups").rglob("*vor-Schreiben.fm1s"))) == 1, "Backup vor dem Schreiben")
    sp.open_list.setCurrentRow(7)
    check(sp.open_upload("clear") and sp.open_entries[7].empty, "FM1 Open: Slot 8 geleert")
    sp.open_read()
    check(sp.open_entries[7].empty and sp.open_entries[0].name == names[0], "FM1 Open: neu gelesen = geschrieben")

    for extra in EXTRA:
        extra(sp)
    app.quit()


EXTRA = []
try:
    import ui_samples_extra  # noqa: F401  (weitere Schritte, falls vorhanden)
    EXTRA = ui_samples_extra.STEPS
except ImportError:
    pass


def run():
    try:
        flow()
    except Exception:  # noqa: BLE001
        errors.append(traceback.format_exc())
        app.quit()


QTimer.singleShot(500, run)
QTimer.singleShot(400000, app.quit)
app.exec()
try:
    w.close()
except Exception:  # noqa: BLE001
    pass
fails = [r for r in results if not r[0]]
for e in errors:
    print("AUSNAHME:\n" + e)
print(f"{len(results) - len(fails)}/{len(results)} bestanden, {len(errors)} Ausnahmen")
shutil.rmtree(home, ignore_errors=True)
sys.exit(1 if fails or errors else 0)
