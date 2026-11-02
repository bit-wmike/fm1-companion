# SPDX-License-Identifier: GPL-3.0-only
"""Abnahme Klang: Gerätewechsel DEMO-1/2/3 bei offenen Klang-Seiten, ehrliche Hinweise ohne Sample-Slots,
Rückmeldungen nach Abbrechen, Kit-Datei-Kollision, Schnell-Editor im Hintergrund, Größen deutsch.

  QT_QPA_PLATFORM=offscreen python3 tests/ui_klang_review_test.py
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
home = Path(tempfile.mkdtemp(prefix="fm1c-klangrev-"))
for k, sub in (("XDG_CONFIG_HOME", ".config"), ("XDG_DATA_HOME", ".local/share"), ("XDG_CACHE_HOME", ".cache")):
    os.environ[k] = str(home / sub)
os.environ["FM1C_DEMO"] = "1"
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu")

import threading  # noqa: E402

errors: list[str] = []
sys.excepthook = lambda t, v, tb: errors.append("".join(traceback.format_exception(t, v, tb)))
threading.excepthook = lambda a: errors.append("".join(traceback.format_exception(a.exc_type, a.exc_value,
                                                                                  a.exc_traceback)))

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

answers = {"text": [], "msg": []}
boxes: list[str] = []


def fake_msg_exec(self):
    boxes.append(self.text())
    want = answers["msg"].pop(0) if answers["msg"] else True
    for b in self.buttons():
        role = self.buttonRole(b)
        if want and role in (QMessageBox.ButtonRole.AcceptRole, QMessageBox.ButtonRole.YesRole,
                             QMessageBox.ButtonRole.DestructiveRole):
            self._clicked = b
            return 0
        if not want and role in (QMessageBox.ButtonRole.RejectRole, QMessageBox.ButtonRole.NoRole):
            self._clicked = b
            return 0
    self._clicked = None
    return 0


QMessageBox.exec = fake_msg_exec
QMessageBox.clickedButton = lambda self: getattr(self, "_clicked", None)
QInputDialog.getText = staticmethod(lambda *a, **k: (answers["text"].pop(0) if answers["text"] else k.get("text", "x"), True))
_dialog_exec = QDialog.exec
QDialog.exec = lambda self: 1

from fm1companion.ui.main_window import MainWindow  # noqa: E402
from fm1companion.ui.common import fmt_size  # noqa: E402

w = MainWindow()
w.resize(1280, 720)
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


def port(serial):
    return next((p for p in w.ctx.ports if p.serial == serial), None)


def switch(serial, key, ms=15000):
    w.ctx.connect_port(port(serial))
    return until(lambda: getattr(w.ctx.port, "serial", "") == serial and key(), ms)


def tone(path: Path, secs: float, sr: int = 44100, ch: int = 2):
    with wave.open(str(path), "wb") as f:
        f.setnchannels(ch)
        f.setsampwidth(2)
        f.setframerate(sr)
        n = int(secs * sr)
        f.writeframes(b"".join(struct.pack("<" + "h" * ch, *[int(8000 * math.sin(2 * math.pi * 220 * t / sr))] * ch)
                               for t in range(n)))


def flow():
    ctx = w.ctx
    check(until(lambda: ctx.connected and ctx.firmware_key == "melodee", 20000), "DEMO-1 Melodee verbunden")
    check(fmt_size(440832) == "430,5 KiB", f"Größen mit deutschem Komma ({fmt_size(440832)})")

    # ---------------------------------------------------------------- Presets: Abbrechen vor dem ersten Platz
    pp = w.page("presets")
    pp = getattr(pp, "_subs", [pp])[0]              # die Unterseite selbst (TabbedPage reicht _private nicht durch)
    w.go("presets")
    check(until(lambda: len(pp.slots) == 384 and not pp.busy), "Presets: 384 Plätze gelesen")
    pp.save_all()
    check(until(lambda: len(pp.lib_items) >= 26 and not pp.busy, 20000), "Presets: Bibliothek gefüllt")
    pp.only_used.setChecked(False)
    pp.select_slot(100)
    pp.lib.clearSelection()
    for i in range(3):
        pp.lib.topLevelItem(i).setSelected(True)
    pp.write_selected()
    pp.cancel_job()                                 # sofort: der Auftrag hält vor dem ersten Platz an
    check(until(lambda: not pp.busy, 15000), "Presets: Schreiben abgebrochen")
    txt = pp.state.text()
    check("U101–U100" not in txt and ("abgebrochen" in txt or "geschrieben" in txt),
          f"Rückmeldung nach Abbruch ohne Unsinns-Bereich ({txt[:90]})")
    until(lambda: not pp.busy)
    while pp.undo:                                  # Demo-Gerät wieder wie vorher
        pp.do_undo()
        until(lambda: not pp.busy)
    pp.only_used.setChecked(True)
    pp.slots_before = None
    # Sichern ohne Auswahl: klarer Satz statt „0 von 0 gelesen“
    pp.dev.clearSelection()
    pp.fetch_selected()
    check("nichts zu sichern" in pp.state.text() and not pp.busy, f"„In Bibliothek“ ohne Auswahl erklärt ({pp.state.text()[:60]})")

    # ---------------------------------------------------------------- Samples › Auf dem FM-1 bei Melodee
    sp = w.page("samples")
    sp = getattr(sp, "_subs", [sp])[0]
    w.go("samples")
    sp.tabs.setCurrentIndex(sp.dev_tab_index)
    check(until(lambda: "keine Sample-Slots" in sp.slot_info.text(), 10000),
          f"Melodee: „keine Sample-Slots“ ({sp.slot_info.text()[:60]})")
    check(not sp.b_upload.isEnabled() and not sp.b_serase.isEnabled() and sp.slot_list.isHidden(),
          "Melodee: Hochladen/Leeren gesperrt, keine leere Liste")
    check(not sp.b_open_read.isEnabled() and "FM1 Open" in sp.open_info.text() and "Melodee" in sp.open_info.text(),
          f"FM1-Open-Kasten bei Melodee gesperrt und erklärt ({sp.open_info.text()[:70]})")
    check(sp.open_list.isHidden() and not sp.b_sread.isEnabled(), "Melodee: keine leeren Listen, „Lesen“ gesperrt")

    # ---------------------------------------------------------------- Gerätewechsel DEMO-2 (SLOOP) mit offener Seite
    check(switch("DEMO-2", lambda: ctx.connected and ctx.firmware_key == "sloop"), "DEMO-2 SLOOP verbunden")
    check(until(lambda: sp.slot_list.count() == 4, 15000), f"SLOOP: 4 Sample-Slots gelesen ({sp.slot_list.count()})")
    check(sp.slot_list.isVisible() and sp.b_serase.isEnabled() and sp.b_upload.isEnabled(),
          "SLOOP: Liste sichtbar, Leeren/Hochladen möglich")
    check("keine Sample-Slots" not in sp.slot_info.text(), "SLOOP: kein veralteter Melodee-Hinweis")
    w.go("presets")
    check(until(lambda: not pp.busy and "Melodee" not in pp.state.text() and "SLOOP" in pp.state.text(), 15000),
          f"Presets nach Wechsel: SLOOP statt Melodee ({pp.state.text()[:90]})")
    check("fehlgeschlagen" not in pp.state.text(), "SLOOP ohne gemeinsame Presets: Erklärung statt „fehlgeschlagen“")
    check(all(s.get("name", "") != "DEMO 1" for s in pp.slots) or not pp.slots,
          "Presets: keine Melodee-Plätze mehr in der Liste")

    # FM-Voices: „als FM6-Preset speichern“ auf SLOOP – ruhig erklärt, nichts geschrieben
    vp = w.page("voices")
    w.go("voices")
    dst = home / "FM-1" / "DX7" / "Test"
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "tests/fixtures/klang/fm1_bank1.syx", dst / "fm1_bank1.syx")
    vp.scan()
    vp.table.setCurrentItem(vp.table.topLevelItem(0))
    n = len(boxes)
    vp.send_to_slot(vp.current.voice)
    check(until(lambda: "nicht möglich" in vp.state.text(), 10000) and "fehlgeschlagen" not in vp.state.text(),
          f"FM-Voices auf SLOOP: „nicht möglich“ statt „fehlgeschlagen“ ({vp.state.text()[:60]})")
    check(any("nichts geändert" in b for b in boxes[n:]), "FM-Voices auf SLOOP: Meldung sagt, dass nichts geändert wurde")

    # ---------------------------------------------------------------- DEMO-3 (generisch, nur MIDI)
    w.go("samples")
    sp.tabs.setCurrentIndex(sp.dev_tab_index)
    check(switch("DEMO-3", lambda: not ctx.connected and ctx.link is not None), "DEMO-3 (nur MIDI) verbunden")
    check(until(lambda: sp.slot_list.count() == 0), "Samples: SLOOP-Slots nach Wechsel weg")
    check("Felucca" in sp.slot_info.text() or "FM1 Open" in sp.slot_info.text(),
          f"Samples: generisches Gerät erklärt ({sp.slot_info.text()[:70]})")
    check(sp.b_open_read.isEnabled() and not sp.open_list.isHidden(), "FM1-Open-Kasten bei Nicht-Felucca-Gerät offen")
    w.go("presets")
    check("nur MIDI" in pp.state.text() or "keine Preset" in pp.state.text(), f"Presets bei DEMO-3 erklärt ({pp.state.text()[:70]})")
    w.go("voices")
    w.go("recorder")
    w.go("soundmodule") if w.page("soundmodule") else None

    # ---------------------------------------------------------------- zurück zu DEMO-1, dann trennen
    check(switch("DEMO-1", lambda: ctx.connected and ctx.firmware_key == "melodee"), "zurück zu DEMO-1")
    w.go("presets")
    check(until(lambda: len(pp.slots) == 384 and not pp.busy, 15000), "Presets lesen DEMO-1 neu")
    w.go("samples")
    sp.tabs.setCurrentIndex(sp.dev_tab_index)
    check(until(lambda: "keine Sample-Slots" in sp.slot_info.text(), 10000), "Samples: Melodee-Hinweis wieder da")
    ctx.disconnect("FM-1 getrennt")
    check(until(lambda: not ctx.connected), "getrennt")
    check("Kein FM-1" in sp.slot_info.text() and not sp.b_upload.isEnabled() and not sp.b_sread.isEnabled(),
          f"Samples ohne Gerät: Hinweis, Knöpfe gesperrt ({sp.slot_info.text()[:50]})")
    check("Kein FM-1" in pp.state.text(), "Presets ohne Gerät: Hinweis")

    # ---------------------------------------------------------------- Kit speichern: Namenskollision
    kits = home / "FM-1" / "Kits"
    kits.mkdir(parents=True, exist_ok=True)
    w.go("samples")
    sp.tabs.setCurrentIndex(0)
    snd = home / "snd.wav"
    tone(snd, 0.3, ch=1)
    import fm1companion.samples as S
    sp.kit = S.Kit(name="ALT")
    sp.kit.zones.append(S.Zone(path=str(snd)))
    answers["text"].append("Drums")
    sp._kit_save()
    first = kits / "Drums.fm1kit"
    check(first.exists(), "Kit „Drums“ gespeichert")
    sp.kit = S.Kit(name="NEU")
    answers["text"].append("Drums")
    answers["msg"].append(False)                    # „Ersetzen?“ → Nein
    n = len(boxes)
    sp._kit_save()
    check(any("schon ein Kit" in b for b in boxes[n:]), "zweites „Drums“: Rückfrage vor dem Überschreiben")
    check('"ALT"' in first.read_text("utf-8") and not sp.kit.path,
          f"Nein: altes Kit bleibt unverändert ({first.read_text('utf-8')[:40]!r}, {sp.kit.path})")
    answers["text"].append("../../böse/name")
    sp._kit_save()
    check((kits / "böse_name.fm1kit").exists() or any(p.name.endswith(".fm1kit") and "/" not in p.name
                                                      for p in kits.iterdir()),
          f"Schrägstriche im Namen landen nicht außerhalb von Kits ({[p.name for p in kits.iterdir()]})")
    check(not (home / "FM-1" / "böse").exists() and not (home / "böse").exists(), "kein Ordner außerhalb angelegt")
    (kits / "kaputt.fm1kit").write_text("{kein json", "utf-8")
    sp._fill_kits()
    n = len(boxes)
    sp.kit_dirty = False
    sp.kit_combo.setCurrentIndex(sp.kit_combo.findText("kaputt"))
    sp._kit_chosen(sp.kit_combo.currentIndex())
    check(any("keine gültige Kit-Datei" in b for b in boxes[n:]), "kaputte Kit-Datei: deutscher Satz")

    # ---------------------------------------------------------------- Schnell-Editor: lädt und speichert im Hintergrund
    from fm1companion.ui.recorder_page import QuickEditDialog
    rec = home / "FM-1" / "Aufnahmen" / "lang.wav"
    rec.parent.mkdir(parents=True, exist_ok=True)
    tone(rec, 6.0)
    t0 = time.monotonic()
    d = QuickEditDialog(rec, w)
    built = time.monotonic() - t0
    check(built < 0.5 and not d.b_ok.isEnabled(), f"Schnell-Editor öffnet sofort ({built:.2f} s), lädt im Hintergrund")
    check(until(lambda: d.a is not None and d.b_ok.isEnabled(), 10000), "Schnell-Editor: Wellenform geladen")
    d.wave.start, d.wave.end = 1.0, 3.0
    d.save()
    check(until(lambda: d.saved is not None, 10000) and d.saved.exists(), "Schnell-Editor: Auswahl gespeichert")
    with wave.open(str(d.saved)) as f:
        secs = f.getnframes() / f.getframerate()
    check(abs(secs - 2.0) < 0.05, f"geschnittene Länge 2 s ({secs:.2f} s)")
    d.deleteLater()

    # ---------------------------------------------------------------- Bibliothek umbenennen schlägt fehl → Name bleibt
    from fm1companion import presetlib as pl
    lib = pl.Library(home / "lib")
    p0 = pl.Preset(name="ALT")
    f, _ = lib.save(p0, "Test", lib.hashes())
    orig_write = pl.Library.write
    pl.Library.write = staticmethod(lambda p, f: (_ for _ in ()).throw(OSError(28, "No space left on device")))
    try:
        lib.rename(p0, "NEU")
    except OSError:
        pass
    finally:
        pl.Library.write = orig_write
    check(p0.name == "ALT" and p0.path == f and f.exists(), "Umbenennen fehlgeschlagen: Name und Datei bleiben")
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
    print("AUSNAHME:\n" + e)
print(f"{len(results) - len(fails)}/{len(results)} bestanden, {len(errors)} Ausnahmen")
shutil.rmtree(home, ignore_errors=True)
sys.exit(1 if fails or errors else 0)
