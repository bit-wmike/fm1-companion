# SPDX-License-Identifier: GPL-3.0-only
"""klang: Seiten „Presets“ und „FM-Voices“ im Demo-Modus, Dialoge automatisch beantwortet.

  QT_QPA_PLATFORM=offscreen python3 tests/ui_sounds_test.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
home = Path(tempfile.mkdtemp(prefix="fm1c-klang-"))
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

from fm1companion import config  # noqa: E402

s = config.load()
s.user_root = str(home / "FM-1")
s.check_on_start = False
config.save(s)
config.ensure_dirs()

answers = {"text": [], "item": [], "int": []}
boxes: list[str] = []


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
QInputDialog.getText = staticmethod(lambda *a, **k: (answers["text"].pop(0) if answers["text"] else k.get("text", "x"), True))
QInputDialog.getInt = staticmethod(lambda *a, **k: (answers["int"].pop(0) if answers["int"] else a[3], True))
QInputDialog.getItem = staticmethod(lambda *a, **k: (answers["item"].pop(0) if answers["item"] else a[3][0], True))
QDialog.exec = lambda self: 1

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
        wait(80)
    return False


class NoPlayer:
    played = []

    def play(self, path, status=None):
        self.played.append(path)
        return True

    def stop(self):
        pass


def flow():
    ctx = w.ctx
    check(until(lambda: ctx.connected and ctx.firmware_key == "melodee"), "Demo-Melodee verbunden")
    pp = w.page("presets")
    vp = w.page("voices")
    check(pp is not None and vp is not None, "Seiten Presets und FM-Voices registriert")
    w.go("presets")
    check(until(lambda: len(pp.slots) == 384 and not pp.busy), "384 Plätze gelesen")
    check(pp.dev.topLevelItemCount() == 26, f"26 belegte angezeigt ({pp.dev.topLevelItemCount()})")
    pp.save_all()
    check(until(lambda: len(pp.lib_items) == 26 and not pp.busy, 20000), f"Alle sichern: {len(pp.lib_items)} in der Bibliothek")
    files = list((home / "FM-1" / "Presets").rglob("U001 *.fm1preset"))
    check(len(files) == 1, f"Datei U001 … .fm1preset angelegt ({[f.name for f in files]})")
    pp.save_all()
    check(until(lambda: "0 neu" in pp.state.text() and not pp.busy, 20000), "zweites Sichern: keine Duplikate")
    pp.engine.setCurrentIndex(pp.engine.findText("FM6"))
    check(0 < pp.dev.topLevelItemCount() < 26, f"Filter Engine FM6 ({pp.dev.topLevelItemCount()})")
    pp.engine.setCurrentIndex(0)
    pp.search.setText("demo 201")
    check(pp.dev.topLevelItemCount() == 1, "Suche findet U201")
    pp.search.setText("")
    # schreiben + rückgängig
    pp.only_used.setChecked(False)
    pp.select_slot(29)
    lib0 = next(i for i in range(pp.lib.topLevelItemCount()) if pp.lib.topLevelItem(i).text(0) == "DEMO 2")
    pp.lib.setCurrentItem(pp.lib.topLevelItem(lib0))
    n = len(boxes)
    pp.write_selected()
    check(any("U030" in b for b in boxes[n:]), "Bestätigung nennt den Platz U030")
    check(until(lambda: pp.slots[29]["used"] and not pp.busy), "Preset in U030 geschrieben")
    check(pp.undo and pp.undo[-1][0] == 29, "Rückgängig vorgemerkt")
    pp.do_undo()
    check(until(lambda: not pp.slots[29]["used"] and not pp.busy), "Rückgängig: U030 wieder leer")
    # überschreiben eines belegten Platzes nennt den alten Inhalt
    pp.select_slot(2)
    pp.lib.setCurrentItem(pp.lib.topLevelItem(lib0))
    n = len(boxes)
    pp.write_selected()
    check(any("DEMO 3" in b and "Überschrieben" in b for b in boxes[n:]), "Überschreiben nennt „DEMO 3“")
    until(lambda: not pp.busy)
    pp.do_undo()
    check(until(lambda: pp.slots[2]["name"] == "DEMO 3" and not pp.busy), "Rückgängig stellt DEMO 3 wieder her")
    # umbenennen, Favorit, anhören
    pp.only_used.setChecked(True)
    pp.select_slot(4)
    answers["text"].append("MEIN BASS")
    pp.rename()
    check(until(lambda: pp.slots[4]["name"] == "MEIN BASS" and not pp.busy), "Umbenennen (op 6)")
    pp.select_slot(4)
    pp.toggle_fav()
    check(until(lambda: pp.slots[4]["favorite"] and not pp.busy), "Favorit gesetzt")
    pp.select_slot(4)
    pp.load_track()
    check(until(lambda: "Spur 1" in pp.state.text()), "Auf Spur laden (op 4)")
    # Export + Import
    pp.lib.selectAll()
    z = home / "satz.zip"
    pp.export(str(z))
    check(z.exists(), "Satz als .zip exportiert")
    pp.import_files([str(z)])
    check(until(lambda: "Presets importiert" in pp.state.text()), "Satz wieder importiert (ohne Duplikate)")

    # ---- FM-Voices
    w.go("voices")
    vp.player = NoPlayer()
    dst = home / "FM-1" / "DX7" / "Test"
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "tests/fixtures/klang/fm1_bank1.syx", dst / "fm1_bank1.syx")
    vp.scan()
    check(vp.table.topLevelItemCount() == 32, f"Bank importiert: {vp.table.topLevelItemCount()} Voices")
    vp.search.setText("piano")
    check(vp.table.topLevelItemCount() >= 1, f"Suche „piano“ ({vp.table.topLevelItemCount()})")
    vp.table.setCurrentItem(vp.table.topLevelItem(0))
    check(vp.current is not None and "PIANO" in vp.current.name.upper(), "Voice gewählt")
    check(len(vp.algo.positions) == 6 or vp.algo.grab() is not None, "Algorithmus gezeichnet")
    img = vp.algo.grab()
    check(not img.isNull() and len(vp.algo.positions) == 6, "alle 6 Operatoren im Diagramm")
    vp.search.setText("")
    vp.preview()
    check(until(lambda: vp.last_render is not None, 15000), "Vorschau berechnet")
    check(vp.last_render and vp.last_render[2] < 3 and NoPlayer.played, f"Vorschau abgespielt ({vp.last_render[2]:.2f} s)")
    vp.gen_variations()
    check(vp.children.count() == 8, "8 Variationen erzeugt")
    vp.children.setCurrentRow(2)
    vp.keep_child()
    check(vp.own.count() == 1, "Variation behalten")
    vp.add_to_own()
    out = home / "voice.syx"
    vp.export_voice(str(out))
    check(out.exists() and out.stat().st_size == 163, ".syx (Einzel-Voice) exportiert")
    vp.save_own(home / "FM-1" / "DX7" / "Eigene Bänke" / "Meine.syx")
    check((home / "FM-1" / "DX7" / "Eigene Bänke" / "Meine.syx").stat().st_size == 4104, "eigene Bank gespeichert")
    vp.gen_random()
    check(vp.current and vp.current.bank == "Erzeugt", "Zufalls-Voice")
    # ans FM-1: in User-Platz 40 (leer)
    vp.table.setCurrentItem(vp.table.topLevelItem(0))
    name = vp.current.name
    answers["item"].append("In einen User-Platz speichern (FM6-Preset) …")
    answers["int"].append(40)
    n = len(boxes)
    vp.send()
    check(until(lambda: vp.undo, 10000), "Voice als FM6-Preset in U040 gespeichert")
    check(any("U040" in b for b in boxes[n:]), "Bestätigung nennt U040")
    w.go("presets")
    pp.read_list()
    check(until(lambda: len(pp.slots) == 384 and pp.slots[39]["used"] and pp.slots[39]["engine"] == 12 and not pp.busy),
          f"U040 ist jetzt FM6 ({pp.slots[39]['name'] if pp.slots else ''} / {name})")
    w.go("voices")
    vp.do_undo()
    check(until(lambda: not vp.undo, 10000), "Rückgängig in FM-Voices")
    answers["item"].append("Live auf eine Spur laden (nicht gespeichert)")
    answers["int"].append(2)
    vp.send()
    check(until(lambda: "Spur 2" in vp.state.text()), "Voice live auf Spur 2 (FM6_PUT)")
    answers["item"].append("Als DX7-SysEx senden")
    vp.send()
    check(until(lambda: "gesendet" in vp.state.text()), f"DX7-SysEx gesendet ({vp.state.text()})")
    # Sammlungen (Download simuliert)
    from fm1companion import dx7_sources
    from fm1companion.ui.pages.voices import CollectionsDialog
    d = CollectionsDialog(vp)
    data = (ROOT / "tests/fixtures/klang/fm1_bank1.syx").read_bytes()
    d.download(collection=dx7_sources.by_key("fm1-factory"), fetch=lambda u: data)
    check(until(lambda: "heruntergeladen" in d.info.text()), "Sammlung geladen (simuliert)")
    check(until(lambda: vp.bank_list.count() >= 6), f"Sammlung erscheint als Bänke ({vp.bank_list.count()})")
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
