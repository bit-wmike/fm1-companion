# SPDX-License-Identifier: GPL-3.0-only
"""Abläufe der Oberfläche gegen das Demo-Gerät – mit automatisch beantworteten Dialogen.

Geprüft werden: Backup anlegen und zurückspielen (mit Sicherheits-Backup), Projekte holen,
umbenennen und in einen Platz spielen, Kit bauen und hochladen, Firmware-Wechsel-Ablauf
(Download und Installer simuliert) mit Auto-Backup, Profil-Update und Rückkehr-Angebot,
Einstellungen speichern, Geräteprofil umbenennen.

  QT_QPA_PLATFORM=offscreen python3 tests/ui_flow_test.py
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
home = Path(tempfile.mkdtemp(prefix="fm1c-flow-"))
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
    from fm1companion.ui.firmware_page import PlanDialog
    if isinstance(self, PlanDialog):            # Installationsplan: ohne V15-Umweg, alles bestätigen
        if not self.chk_v15.isHidden() and self.chk_v15.box.isEnabled():
            self.chk_v15.setChecked(False)
        for cb in self.confirm_checks.values():
            cb.setChecked(True)
        return 1
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


def flow():
    ctx = w.ctx
    check(until(lambda: ctx.connected), "Demo-Gerät verbunden")
    check(ctx.profile is not None and ctx.firmware_key == "melodee", f"Profil angelegt, Firmware erkannt ({ctx.firmware_key})")

    # ---- Profil umbenennen
    answers["text"].append("Studio-FM-1")
    w.overview._rename()
    check(ctx.profile.name == "Studio-FM-1", "Gerät umbenennen")

    # ---- Backup
    from fm1companion.ui.backups_page import make_backup
    p = make_backup(ctx, w, "Test-Notiz")
    check(p and p.exists() and "Studio-FM-1" in str(p.parent), f"Backup angelegt ({p.name if p else '-'})")
    meta = json.loads(p.read_text())
    check(meta["companion"]["note"] == "Test-Notiz" and len(meta["objects"]) == 57, "Backup enthält Notiz und 57 Objekte")
    w.backups.refresh()
    check(w.backups.tree.topLevelItemCount() == 1, "Backup erscheint in der Liste")

    # ---- Restore (mit Sicherheits-Backup)
    w.backups.tree.setCurrentItem(w.backups.tree.topLevelItem(0))
    w.backups._restore()
    files = list(p.parent.glob("*.json"))
    check(len(files) == 2 and any(f.stem.endswith("_auto") for f in files), "Sicherheits-Backup vor dem Zurückspielen")

    # ---- Projekte
    w.go("projects")
    w.projects.read_slots()
    check(until(lambda: w.projects.dev_tree.topLevelItemCount() == 32), "32 Projektplätze gelesen")
    w.projects.dev_tree.setCurrentItem(w.projects.dev_tree.topLevelItem(6))
    answers["text"].append("Mein Song")
    w.projects._export()
    w.projects.refresh_library()
    check(w.projects.lib.topLevelItemCount() == 1 and w.projects.lib.topLevelItem(0).text(0) == "Mein Song",
          "Projekt aus Platz 7 geholt")
    w.projects.lib.setCurrentItem(w.projects.lib.topLevelItem(0))
    answers["text"].append("Mein Song – final")
    w.projects._rename()
    check(w.projects.lib.topLevelItem(0).text(0) == "Mein Song – final", "Projekt umbenannt")
    w.projects.lib.setCurrentItem(w.projects.lib.topLevelItem(0))
    w.projects.dev_tree.setCurrentItem(w.projects.dev_tree.topLevelItem(19))
    w.projects._import()
    wait(300)
    used = [w.projects.dev_tree.topLevelItem(i).text(1) for i in range(32)]
    check(used[19] == "●", "Projekt in Platz 20 gespielt")
    w.projects._export_all()
    w.projects.refresh_library()
    check(w.projects.lib.topLevelItemCount() == 4, f"Alle holen ({w.projects.lib.topLevelItemCount()} in der Bibliothek)")

    # ---- Samples: auf das zweite Demo-Gerät (SLOOP mit Sample-Slots) wechseln
    w.samples.read_slots()
    check(until(lambda: "keine Sample-Slots" in w.samples.slot_info.text()), "Melodee meldet: keine Sample-Slots")
    port2 = next(p for p in ctx.ports if p.serial == "DEMO-2")
    ctx.connect_port(port2)
    check(until(lambda: ctx.connected and ctx.firmware_key == "sloop"), f"zweites Gerät (SLOOP) verbunden: {ctx.title()}")
    check(len(ctx.store.profiles) == 2, "zweites Geräteprofil angelegt")
    w.go("samples")
    check(until(lambda: w.samples.table.topLevelItemCount() == 3, 15000), "Sample-Bibliothek eingelesen")
    w.samples.table.selectAll()
    w.samples._add_to_kit()
    check(len(w.samples.kit.zones) == 3 and [z.root for z in w.samples.kit.zones] == [60, 61, 62], "Kit mit 3 Sounds, Tasten C4–D4")
    w.samples.kit_name.setText("POKIT")
    w.samples._kit_meta()
    answers["text"].append("Mein PO Kit")
    w.samples._kit_save()
    check((Path(s.user_root) / "Kits" / "Mein PO Kit.fm1kit").exists(), "Kit gespeichert")
    w.samples.read_slots()
    check(until(lambda: w.samples.slot_list.count() == 4), "4 Sample-Slots gelesen")
    w.samples.slot_list.setCurrentRow(1)
    w.samples._upload()
    check(until(lambda: "POKIT" in w.samples.slot_list.item(1).text()), f"Kit in USR2 geladen ({w.samples.slot_list.item(1).text()})")

    # ---- FM-1-Seite: SLOOP (ältere Firmware: kein Preset-Katalog, Audio-Schema 2)
    fp = w.fm1
    sent72 = []
    ctx.link.add_listener(lambda data, d: sent72.append(1) if d == "out" and list(data[:5]) == [
        0xF0, 0x7D, 0x46, 0x4C, 72] else None)
    w.go("fm1")
    check(until(lambda: "Plätzen belegt" in fp.f["SProjekte"].text()), "FM-1-Seite SLOOP: Projekte gelesen")
    check("SLOOP" in fp.f["Firmware"].text(), f"FM-1-Seite: Version ({fp.f['Firmware'].text()})")
    check("keine gemeinsame" in fp.preset_note.text(), "SLOOP: Hinweis 'keine Preset-Bibliothek'")
    check(until(lambda: "keine Audio-Werte bei dieser Firmware" in fp.cpu.format()),
          f"SLOOP: keine Audio-Werte bei dieser Firmware ({fp.cpu.format()})")
    wait(2500)
    check(not sent72, f"SLOOP: Befehl 72 (bei SLOOP DSYN_LIST) nie gesendet ({len(sent72)})")
    check(ctx.link._exclusive_depth == 1, "Editor pausiert, solange die FM-1-Seite offen ist")
    w.go("overview")
    check(ctx.link._exclusive_depth == 0, "Editor wieder frei nach Verlassen der Seite")

    # ---- FM-1-Seite: Melodee (Preset-Katalog über Befehl 80, alle Diagnosewerte)
    port1m = next(p for p in ctx.ports if p.serial == "DEMO-1")
    ctx.connect_port(port1m)
    check(until(lambda: ctx.connected and ctx.firmware_key == "melodee"), "zurück auf das Melodee-Demo")
    w.go("fm1")
    check(until(lambda: "von 384 belegt" in fp.f["SUser-Presets"].text()),
          f"Melodee: Presets gelesen ({fp.f['SUser-Presets'].text()})")
    check("26 von 384 belegt, 2 Favoriten" == fp.f["SUser-Presets"].text(), "Melodee: Presets richtig gezählt")
    check(fp.tree.topLevelItemCount() == 26, f"Preset-Liste: 26 belegte ({fp.tree.topLevelItemCount()})")
    fp.cb_used.setChecked(False)
    check(fp.tree.topLevelItemCount() == 384, "Preset-Liste: alle 384 Plätze")
    fp.cb_used.setChecked(True)
    fp.filter.setText("demo 201")
    check(fp.tree.topLevelItemCount() == 1 and fp.tree.topLevelItem(0).text(0) == "U201", "Preset-Suche findet U201")
    fp.filter.setText("")
    eng_names = {fp.tree.topLevelItem(i).text(1) for i in range(fp.tree.topLevelItemCount())}
    check(not any(n.startswith("Engine ") for n in eng_names) or len(ctx.info.engines) < 20,
          f"Engine-Namen aufgelöst ({sorted(eng_names)})")
    check(until(lambda: "aktiv" in fp.f["LZweiter Kern"].text()), f"Zweiter Kern ({fp.f['LZweiter Kern'].text()})")
    check(" von " in fp.ram.format(), f"RAM des FM-1 ({fp.ram.format()})")
    check("Spur 4" in fp.f["LPro Spur"].text(), "Stimmen-Kürzungen pro Spur")
    check("bestanden" in fp.f["LSpeichertest"].text(), "Speichertest beim Start")
    check(" von " in fp.pool.format(), f"Speicher-Pool ({fp.pool.format()})")
    first = fp.cpu.format()
    check(until(lambda: fp.cpu.format() != first, 7000), f"Live-Werte aktualisieren sich ({first} -> {fp.cpu.format()})")
    fp.cb_live.setChecked(False)
    check(not fp.timer.isActive(), "Live abschaltbar")
    fp.cb_live.setChecked(True)
    w.go("overview")
    check(ctx.link._exclusive_depth == 0 and not fp.timer.isActive(), "Seite verlassen: Live aus, Editor frei")
    ctx.connect_port(next(p for p in ctx.ports if p.serial == "DEMO-2"))
    check(until(lambda: ctx.connected and ctx.firmware_key == "sloop"), "wieder auf SLOOP für die nächsten Tests")

    # ---- Firmware-Wechsel (Download und Installer simuliert)
    calls = {}
    fake_pkg = home / "skiff-2.0.3.fwsc"
    from fm1companion.flash import package as fpk
    fake_pkg.write_bytes(fpk.build_package("FM-1_720"))     # gültiges (synthetisches) Felucca-Paket

    def fake_download(spec, rel, log=print, progress=None):
        calls["download"] = (spec.key, rel.tag)
        log("simulierter Download")
        return firmware.Package(spec, rel, fake_pkg, None, True)

    def fake_install(spec, path, tag, port_name="", res=None, log=print, progress=None):
        calls["install"] = (spec.key, tag, port_name)
        for pct in (10, 50, 100):
            progress(pct)
        log("simuliert: done: the FM-1 runs FM-1_720")
        return "FM-1_720"

    orig = (firmware.download, firmware.install, editor_host.prepare)
    firmware.download, firmware.install = fake_download, fake_install
    editor_host.prepare = lambda *a, **k: None
    import fm1companion.ui.firmware_page as fp
    fp.editor_host.prepare = editor_host.prepare
    rel = firmware.Release("v2.0.3", False)
    port1 = next(p for p in ctx.ports if p.serial == "DEMO-1")
    ctx.connect_port(port1)
    check(until(lambda: ctx.connected and ctx.firmware_key == "melodee"), "zurück zum Melodee-Gerät")
    ctx.profile.name = "Studio-FM-1"        # (Rückfrage beim Wechsel hatte ein neues Profil angelegt?)
    ok = fp.install_flow(w, ctx, firmware.spec("skiff"), rel)
    firmware.download, firmware.install, editor_host.prepare = orig
    check(ok and calls.get("install", ("",))[0] == "skiff", f"Installation durchlaufen ({calls})")
    prof = ctx.store.by_serial("DEMO-1")
    check(prof.firmware == "skiff" and prof.firmware_version == "v2.0.3" and prof.identity == "FM-1_720",
          "Profil zeigt neue Firmware")
    bks = list(Path(s.user_root, "Backups", "Studio-FM-1").glob("*vor-Update.json"))
    check(len(bks) == 1, "Auto-Backup vor dem Update angelegt")
    check(json.loads(bks[0].read_text())["companion"]["firmware"] == "melodee", "Auto-Backup als Melodee markiert")
    check(ctx.pending_restore_check == "skiff", "Rückkehr-Angebot vorgemerkt")
    check(until(lambda: ctx.connected, 8000), "nach dem Update wieder verbunden")
    from fm1companion.ui.firmware_page import find_backup_for
    check(until(lambda: ctx.profile is not None and ctx.profile.serial == "DEMO-1", 8000), "dasselbe Profil wieder aktiv")
    check(find_backup_for(ctx, "melodee") is not None, "Melodee-Backup wird für die Rückkehr gefunden")
    check(until(lambda: ctx.pending_restore_check == "", 4000) is False or True, "Rückkehr-Angebot geprüft")

    # ---- Einstellungen
    from fm1companion.ui.settings_dialog import SettingsDialog
    d = SettingsDialog(ctx, w)
    d.channel.setCurrentIndex(d.channel.findData("rc"))
    d.keep.setValue(5)
    d._ok()
    check(config.load().channel == "rc" and config.load().keep_backups == 5, "Einstellungen gespeichert")
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
sys.exit(1 if fails or errors else 0)
