# SPDX-License-Identifier: GPL-3.0-only
"""Installer-Werkstatt: Plan-Dialog, Installation, Rückkehr zum Original, Rettung, generisches Gerät (Demo).

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
home = Path(tempfile.mkdtemp(prefix="fm1c-inst-"))
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
QInputDialog.getText = staticmethod(lambda *a, **k: (answers["text"].pop(0) if answers["text"] else "SCHREIBEN", True))
QInputDialog.getMultiLineText = staticmethod(lambda *a, **k: ("Notiz", True))
QInputDialog.getInt = staticmethod(lambda *a, **k: (answers["int"].pop(0) if answers["int"] else 1, True))

_dialog_exec = QDialog.exec


plan_ctl = {"confirm": True, "backup": None, "plans": [], "accept": True, "asked_fw": 0, "last_log": ""}


def dialog_exec(self):
    """JobDialoge laufen lassen, bis sie fertig sind; andere Dialoge sofort bestätigen.
    Installationsplan: alle Bestätigungen ankreuzen (plan_ctl), Backup-Haken nach Wunsch."""
    from fm1companion.ui.common import JobDialog
    from fm1companion.ui.device import FirmwareChoiceDialog
    from fm1companion.ui.firmware_page import PlanDialog
    if isinstance(self, FirmwareChoiceDialog):
        plan_ctl["asked_fw"] += 1
        return 1                                   # Vorauswahl „weiß nicht“
    if isinstance(self, PlanDialog):
        if plan_ctl["backup"] is not None:
            self.chk_backup.setChecked(plan_ctl["backup"])
        if plan_ctl["confirm"]:
            for cb in self.confirm_checks.values():
                cb.setChecked(True)
        plan_ctl["plans"].append(self.plan)
        return 1 if plan_ctl["accept"] else 0
    if isinstance(self, JobDialog):
        self.show()
        t0 = time.time()
        while time.time() - t0 < 60:
            app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
            if self.buttons.button(self.buttons.StandardButton.Close).isEnabled():
                break
        self.hide()
        self.log_text = self.logv.toPlainText()
        plan_ctl["last_log"] = self.log_text
        if not self.ok or os.environ.get("FM1C_TEST_VERBOSE"):
            print("---- Protokoll " + self.windowTitle() + ":\n" + self.log_text[-1500:], flush=True)
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
    import shutil
    from fm1companion.flash import demo, journal, package as fpk
    from fm1companion.ui import firmware_page as fp
    from fm1companion.vendor import fm1_install as fi
    editor_host.prepare = lambda *a, **k: None
    fp.editor_host.prepare = editor_host.prepare
    ctx = w.ctx
    check(until(lambda: ctx.connected and ctx.port.serial == "DEMO-1"), "Demo-Melodee verbunden")
    check(until(lambda: ctx.identity == "FM-1_91060"), f"Handshake-Kennung gelesen ({ctx.identity})")
    check(ctx.protocol == "felucca" and ctx.midi_connected, "Protokoll: felucca")

    # ---- generisches Gerät (Firmware ohne Editor-Protokoll)
    ctx.connect_port(next(p for p in ctx.ports if p.serial == "DEMO-3"))
    check(until(lambda: ctx.identity == "FM-1_900", 15000), f"DEMO-3 meldet FM-1_900 ({ctx.identity})")
    check(not ctx.connected and ctx.midi_connected and ctx.protocol == "generic", "DEMO-3: nur MIDI, kein Felucca")
    check("FM-1_900" in ctx.title() and "nur MIDI" in ctx.status_text(), f"Titel/Status: {ctx.title()} / {ctx.status_text()}")
    check(ctx.firmware_guess[:1] == ["sloop"] or "sloop" in ctx.firmware_guess, f"Kandidaten {ctx.firmware_guess}")
    check(ctx.firmware_key == "" and "unbekannt – bitte wählen" in ctx.title(),
          f"DEMO-3 (ORBIT) wird nicht als SLOOP geraten ({ctx.firmware_key!r}, {ctx.title()})")
    check(until(lambda: ctx.profile.firmware_choice.get("FM-1_900") == "?", 5000) and plan_ctl["asked_fw"] == 1,
          f"einmal gefragt, „weiß nicht“ gemerkt ({plan_ctl['asked_fw']})")
    from fm1companion.flash import planner
    st3 = fp.device_state(ctx)
    check(st3.firmware_key == "" and planner.v15_option(st3, planner.Target.from_spec(firmware.spec("sloop")))
          == "default_on", "unbekannte Firmware gilt als Wechsel: V15-Zwischenschritt vorgewählt")
    ctx.connect_port(next(p for p in ctx.ports if p.serial == "DEMO-3"))
    check(until(lambda: ctx.identity == "FM-1_900", 15000), "DEMO-3 neu verbunden")
    wait(800)
    check(plan_ctl["asked_fw"] == 1, "nicht noch einmal gefragt")
    ctx.choose_firmware("orbit")
    check(ctx.profile.firmware_choice.get("FM-1_900") == "orbit" and ctx.firmware_key == "orbit",
          "Wahl der Firmware gemerkt")

    # ---- eigene Datei auf Melodee (Update derselben Firmware: kein V15-Schritt)
    ctx.connect_port(next(p for p in ctx.ports if p.serial == "DEMO-1"))
    check(until(lambda: ctx.connected and ctx.port.serial == "DEMO-1"), "zurück auf DEMO-1")
    pkg = home / "melodee-1.7.0.fwsc"
    pkg.write_bytes(fpk.build_package("FM-1_91070"))
    ok = fp.install_flow(w, ctx, firmware.spec("melodee"), None, pkg, "v1.7.0")
    dev = demo.device("demo-in")
    check(ok and dev.identity == "FM-1_91070" and dev.flash == fpk.analyze(pkg).image,
          f"Installation über Plan + Fortschritt ({dev.identity})")
    hist = journal.Journal().history()
    check(hist and hist[-1]["phase"] == "done" and hist[-1]["product"] == "FM-1_91070", "Journal: erledigt")
    prof = ctx.store.by_serial("DEMO-1")
    check(prof.firmware_history and prof.firmware_history[-1]["version"] == "v1.7.0", "Firmware-Verlauf im Profil")

    p0 = plan_ctl["plans"][-1]
    check("unknown_file" in p0.confirmations and "backup" in p0.kinds(),
          "eigene Datei ohne bekannte Prüfsumme: Bestätigung „unbekannte Datei“, Backup-Schritt im Plan")

    # ---- Probelauf schreibt nichts
    check(until(lambda: ctx.connected, 10000), "nach der Installation wieder verbunden")
    before = len(dev.sends)
    ok = fp.install_flow(w, ctx, firmware.spec("melodee"), None, pkg, "v1.7.0", dry_run=True)
    check(ok and bytes(fi.UPGRADE) not in dev.sends[before:], "Probelauf sendet kein UPGRADE")

    # ---- Backup gewünscht, aber gescheitert: immer Abbruch, nichts gesendet
    from fm1companion import autobackup
    check(until(lambda: ctx.connected, 10000), "verbunden")
    real_capture = autobackup.capture_to_file

    def broken_capture(*a, **k):
        raise PermissionError(13, "Permission denied", str(home / "FM-1" / "Backups"))
    autobackup.capture_to_file = broken_capture
    before, ident0 = len(dev.sends), dev.identity
    try:
        ok = fp.install_flow(w, ctx, firmware.spec("melodee"), None, pkg, "v1.7.0")
    finally:
        autobackup.capture_to_file = real_capture
    check(not ok and bytes(fi.UPGRADE) not in dev.sends[before:] and dev.identity == ident0
          and "Backup fehlgeschlagen" in plan_ctl["last_log"] and "Keine Schreibrechte" in plan_ctl["last_log"],
          "gescheitertes Backup bricht die Installation ab (deutsche Meldung, nichts gesendet)")

    # ---- Backup abgewählt: Bestätigung „no_backup“ im Plan, kein Backup-Schritt
    check(until(lambda: ctx.connected, 10000), "verbunden")
    plan_ctl["backup"], plan_ctl["confirm"] = False, False
    before = len(dev.sends)
    ok = fp.install_flow(w, ctx, firmware.spec("melodee"), None, pkg, "v1.7.0")
    p1 = plan_ctl["plans"][-1]
    check(not ok and "no_backup" in p1.confirmations and "backup" not in p1.kinds()
          and bytes(fi.UPGRADE) not in dev.sends[before:],
          "ohne Backup: Pflicht-Bestätigung im Plan, ohne sie wird nichts installiert")
    plan_ctl["backup"], plan_ctl["confirm"] = None, True
    s.auto_backup_before_update = False
    check(until(lambda: ctx.connected, 10000), "verbunden")
    ok = fp.install_flow(w, ctx, firmware.spec("melodee"), None, pkg, "v1.7.0", dry_run=True)
    p2 = plan_ctl["plans"][-1]
    s.auto_backup_before_update = True
    check("no_backup" in p2.confirmations and "backup" not in p2.kinds(),
          "Einstellung „kein Auto-Backup“: Plan verlangt die Bestätigung „no_backup“")

    # ---- umbenannte fremde Datei: nicht als Melodee erkannt (nur Prüfsumme zählt)
    check(until(lambda: ctx.connected, 10000), "verbunden")
    fake = home / "melodee-1.6.0-rc.7.fwsc"
    fake.write_bytes(fpk.build_package("FM-1_906", seed=9))
    plan_ctl["accept"] = False
    log_boxes.clear()
    w.firmware._install_local(str(fake))
    plan_ctl["accept"] = True
    p3 = plan_ctl["plans"][-1]
    check(p3.target.key == "eigene" and not p3.target.known and "unknown_file" in p3.confirmations
          and any("unbekannt" in b for b in log_boxes),
          f"umbenannte fremde Datei bleibt unbekannt ({p3.target.key}, {p3.confirmations})")

    # ---- mehrere FM-1, Ziel nicht festzuhalten: Sperre
    real_pin = fp.target_pin
    fp.target_pin = lambda c: None
    try:
        before = len(dev.sends)
        ok = fp.install_flow(w, ctx, firmware.spec("melodee"), None, pkg, "v1.7.0")
        p4 = plan_ctl["plans"][-1]
    finally:
        fp.target_pin = real_pin
    check(not ok and any(planner.MULTI_DEVICE_TEXT in b for b in p4.blockers)
          and bytes(fi.UPGRADE) not in dev.sends[before:], "mehrere FM-1 ohne eindeutiges Ziel: Sperre")

    # ---- Gerät verschwindet, während der Plan offen ist: keine Ausnahme, nichts gesendet
    check(until(lambda: ctx.connected, 10000), "verbunden")
    plan_ctl["accept"] = True
    _orig_exec = QDialog.exec

    def exec_and_unplug(self):
        if isinstance(self, fp.PlanDialog):
            ctx.disconnect("FM-1 getrennt")       # wie ein gezogenes Kabel während des Dialogs
            ctx.hold = True                       # Demo-Scan soll nicht sofort neu verbinden
        return _orig_exec(self)
    QDialog.exec = exec_and_unplug
    log_boxes.clear()
    before, n_err = len(dev.sends), len(errors)
    try:
        ok = fp.install_flow(w, ctx, firmware.spec("melodee"), None, pkg, "v1.7.0")
    finally:
        QDialog.exec = _orig_exec
        ctx.hold = False
    check(not ok and len(errors) == n_err and bytes(fi.UPGRADE) not in dev.sends[before:]
          and any("Gerät hat sich geändert" in b or "getrennt" in b for b in log_boxes),
          f"Gerät weg während des Plans: sauber abgebrochen ({log_boxes[-1][:60] if log_boxes else '-'})")

    # ---- Umbenennen: Name eines anderen Geräts wird abgelehnt (sonst gemeinsamer Backup-Ordner)
    check(until(lambda: ctx.connected and ctx.port.serial == "DEMO-1", 10000), "verbunden")
    other = ctx.store.create("Zweites Gerät", serial="DEMO-9")
    try:
        ctx.rename_profile("zweites gerät")
        check(False, "Umbenennen auf fremden Namen abgelehnt")
    except OSError as e:
        check("schon vergeben" in str(e) and ctx.profile.name != "zweites gerät", "Umbenennen auf fremden Namen abgelehnt")
    ctx.rename_profile("Mein FM-1 ÄÖÜ – Studio  ")
    check(ctx.profile.name == "Mein FM-1 ÄÖÜ – Studio" and (Path(s.user_root) / "Backups" / ctx.profile.folder_name).exists(),
          f"Umlaute/Leerzeichen im Namen, Ordner mitgewandert ({ctx.profile.folder_name})")
    ctx.store.delete(other.id)

    # ---- Zurück zum Original (V15, hash-geprüft, aus dem Zwischenspeicher)
    v15 = Path("/tmp/fm1_open/sdk/firmware/FM-1_015.fwsc")
    if v15.exists():
        d = s.firmware_dir / "stock-v15" / "V15"
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy(v15, d / "FM-1_015.fwsc")
        # ---- Zielpaket kaputt: wird VOR dem V15-Zwischenschritt erkannt, nichts geschrieben
        check(until(lambda: ctx.connected, 10000), "wieder verbunden")
        bad = home / "dl" / "sloop-9.9.9.fwsc"
        bad.parent.mkdir(exist_ok=True)
        raw = bytearray(fpk.build_package("FM-1_900", seed=3))
        raw[-300] ^= 0xFF                                    # flash.bin/ota.bin-Prüfsumme kaputt
        bad.write_bytes(bytes(raw))
        real_dl = firmware.download
        rel = firmware.Release(tag="v9.9.9", prerelease=False, assets={"sloop-9.9.9.fwsc": "https://example.invalid/x"})

        def fake_dl(sp, r, **k):
            if sp.key == "sloop":
                return firmware.Package(sp, r, bad, None, False)
            return real_dl(sp, r, **k)
        firmware.download = fake_dl
        before, ident0 = len(dev.sends), dev.identity
        try:
            ok = fp.install_flow(w, ctx, firmware.spec("sloop"), rel)
        finally:
            firmware.download = real_dl
        p5 = plan_ctl["plans"][-1]
        check(p5.via_stock and "no_checksum" in p5.confirmations, f"SLOOP ohne SHA256SUMS: Bestätigung "
              f"„Prüfsumme nicht verfügbar“, V15-Zwischenschritt ({p5.confirmations})")
        check(not ok and bytes(fi.UPGRADE) not in dev.sends[before:] and dev.identity == ident0
              and "wird nicht installiert" in plan_ctl["last_log"],
              f"kaputtes Zielpaket vor jedem Schreiben erkannt – V15 nicht geschrieben ({dev.identity})")

        # ---- Abbruch nach dem V15-Schritt: Meldung sagt, was geschrieben wurde
        check(until(lambda: ctx.connected, 10000), "wieder verbunden")
        good = home / "dl" / "sloop-9.9.8.fwsc"
        good.write_bytes(fpk.build_package("FM-1_900", seed=4))
        rel2 = firmware.Release(tag="v9.9.8", prerelease=False, assets={"sloop-9.9.8.fwsc": "https://example.invalid/y"})
        real_inst = firmware.install

        def inst_then_cancel(sp, *a, **k):
            r = real_inst(sp, *a, **k)
            if sp.key == "stock-v15" and k.get("cancel") is not None:
                k["cancel"].set()
            return r
        firmware.download = lambda sp, r, **k: firmware.Package(sp, r, good, None, False) if sp.key == "sloop" \
            else real_dl(sp, r, **k)
        firmware.install = inst_then_cancel
        try:
            ok = fp.install_flow(w, ctx, firmware.spec("sloop"), rel2)
        finally:
            firmware.download, firmware.install = real_dl, real_inst
        log = plan_ctl["last_log"]
        check(not ok and dev.identity == "FM-1_015" and "Original-Firmware V15" in log.split("FEHLER:")[-1]
              and "nichts geändert" not in log.split("FEHLER:")[-1],
              f"Abbruch nach V15 nennt, was geschrieben wurde ({log.split('FEHLER:')[-1].strip()[:120]})")
        hist = journal.Journal().history()
        check(hist and "V15 V15" not in hist[-1].get("target", "") and hist[-1].get("target") ==
              "Original-Firmware V15", f"Journal-Name ohne doppelte Version ({hist[-1].get('target')})")

        check(until(lambda: ctx.connected, 10000), "wieder verbunden")
        w.go("firmware")
        ok = w.firmware.return_to_original()
        check(ok and dev.identity == "FM-1_015" and not dev.bricked, f"Zurück zu V15 ({dev.identity})")
    else:
        check(True, "V15-Datei fehlt – Rückkehr übersprungen")

    # ---- Rettung: unterbrochene Installation auf DEMO-2
    page = w.page("recovery") if hasattr(w, "page") else None
    check(page is not None, "Seite Rettung registriert")
    d2 = demo.device("demo2-in")
    pkg2 = home / "sloop-9.9.fwsc"
    pkg2.write_bytes(fpk.build_package("FM-1_901", seed=5))
    demo.register(pkg2.read_bytes())
    d2.power_loss_at = 0.4
    from fm1companion.flash import engine
    try:
        engine.Engine(log=lambda *_: None).write(fpk.analyze(pkg2), port="demo2-in", label="SLOOP 9.9")
        check(False, "Stromausfall simuliert")
    except engine.FlashError as e:
        check(e.state == engine.PAUSED, f"Stromausfall → Update-Modus ({e.code})")
    check(until(lambda: any(p.update_mode for p in ctx.ports), 8000), "Update-Modus-Port erscheint live")
    w.go("recovery")
    page.refresh()
    check("wartet im Update-Modus" in page.status.text() and "SLOOP 9.9" in page.status.text(),
          "Rettung zeigt Gerät und Journal")
    # falsches Paket (Original-V15) für den Felucca-Loader: Knopf aus, Aufruf lehnt ab, nichts gesendet
    ctx.connect_port(next(p for p in ctx.ports if p.update_mode))      # wie bei nur einem Gerät: der Loader wird aktiv
    page.refresh()
    check(until(lambda: ctx.identity.startswith("ota-"), 8000) and not page.b_v15.isEnabled()
          and "Felucca-Loader" in page.b_v15.toolTip(), f"„Original-V15“ bei Felucca-Loader gesperrt ({ctx.identity})")
    n_before = len(d2.sends)
    log_boxes.clear()
    check(not page.finish_install(stock=True) and len(d2.sends) == n_before
          and any("Passt nicht" in b or "nimmt die Original-V15 nicht an" in b for b in log_boxes),
          "V15 an Felucca-Loader: abgelehnt, nichts gesendet")
    ok = page.finish_install()
    check(ok and d2.identity == "FM-1_901" and d2.flash == fpk.analyze(pkg2).image,
          f"Rettung abgeschlossen ({ok}, {d2.identity}, {d2.flash == fpk.analyze(pkg2).image}, {d2.log[-3:]})")
    check(journal.Journal().active is None, "Journal wieder leer")

    # ---- Stufe 2 in der Simulation (Mock-FM-1 von MvaveFM1Unbricker)
    if v15.exists():
        page.chk_sim.setChecked(True)
        ok = page.uboot_restore()
        check(ok and list((Path(s.user_root) / "Rettung" / "backups").glob("*/full-flash-before.bin")),
              "Stufe 2 (Simulation): Doppelsicherung, Schreiben, Rücklesen")
    w.go("overview")
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
import shutil as _sh
_sh.rmtree(home, ignore_errors=True)
sys.exit(1 if fails or errors else 0)
