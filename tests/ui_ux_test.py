# SPDX-License-Identifier: GPL-3.0-only
"""Usability-Prüfung 2.0 (offscreen, Demo-Modus): zusammengelegte Navigation mit Weiterleitung alter Schlüssel,
Übersicht ohne „Bereiche“ und bei 1280×720 ohne Rollen, Rettung mit Details-Aufklappern, Standardkanal stabil,
leiser Gerätewechsel, Plan-Dialog-Größe, Leerzustände, Begriffe.

  QT_QPA_PLATFORM=offscreen python3 tests/ui_ux_test.py
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
home = Path(tempfile.mkdtemp(prefix="fm1c-ux-"))
for k, sub in (("XDG_CONFIG_HOME", ".config"), ("XDG_DATA_HOME", ".local/share"), ("XDG_CACHE_HOME", ".cache"),
               ("XDG_STATE_HOME", ".local/state")):
    os.environ[k] = str(home / sub)
os.environ["FM1C_DEMO"] = "1"
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu")

errors: list[str] = []
sys.excepthook = lambda t, v, tb: errors.append("".join(traceback.format_exception(t, v, tb)))

import PyQt6.QtWebEngineWidgets  # noqa: E402,F401
from PyQt6.QtCore import QCoreApplication, QEventLoop, Qt, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox, QToolButton  # noqa: E402

QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
app = QApplication(sys.argv)

from fm1companion import config  # noqa: E402
from fm1companion.ui import theme  # noqa: E402

theme.apply(app, "light")
s = config.load()
s.user_root = str(home / "FM-1")
s.check_on_start = False
config.save(s)
config.ensure_dirs()

QMessageBox.exec = lambda self: 0
QDialog.exec = lambda self: 1

from fm1companion.ui.main_window import ALIASES, MainWindow  # noqa: E402

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


def visible_in(widget, container) -> bool:
    """Widget liegt vollständig im sichtbaren Bereich des Containers (ohne Rollen)."""
    if not widget.isVisible():
        return False
    top_left = widget.mapTo(container, widget.rect().topLeft())
    bottom = top_left.y() + widget.height()
    return top_left.y() >= 0 and bottom <= container.height()


def flow():
    w = MainWindow()
    w.resize(1280, 720)
    w.show()
    ctx = w.ctx
    check(until(lambda: ctx.connected), "Demo-Gerät verbunden")

    # ---- 1. Navigation
    entries = [w.nav.item(i).text() for i in range(w.nav.count()) if w.nav.item(i).data(Qt.ItemDataRole.UserRole)]
    check(len(entries) <= 14, f"Navigation hat {len(entries)} Einträge (≤ 14): {entries}")
    for old in ("backup_browser", "project_view", "workshop", "fm1", "voices", "soundmodule", "simulator", "firmware"):
        check(old in ALIASES, f"alter Schlüssel „{old}“ ist als Weiterleitung registriert")
    # Firmware+ (früher Entdecken + Firmware): EIN Eintrag, zusammen mit Rettung unter „Daten & Werkzeuge“
    groups, grp = {}, ""
    for i in range(w.nav.count()):
        it = w.nav.item(i)
        k = it.data(Qt.ItemDataRole.UserRole)
        if k is None:
            grp = it.text()
        else:
            groups[k] = grp
    check(groups.get("discover") == groups.get("recovery") == "DATEN & WERKZEUGE" and "firmware" not in groups
          and w.specs["discover"].title == "Firmware+" and sum("Firmware" in e for e in entries) == 1,
          f"Firmware+ und Rettung unter „Daten & Werkzeuge“, nur ein Firmware-Eintrag ({groups})")
    w.go("firmware")
    wait(300)
    disc = w.pages["discover"]
    check(w.current_key() == "discover" and disc.tabs.currentWidget() is w.firmware and w.page("firmware") is w.firmware
          and w.firmware.isVisible(), "go('firmware') → Firmware+ › Installieren & Versionen")
    labels = [disc.tabs.tabText(i).replace("&&", "&") for i in range(disc.tabs.count())]
    check(labels[:2] == ["Alle Firmwares", "Installieren & Versionen"] and "Simulator" in labels,
          f"Reiter von Firmware+: Katalog, Installieren & Versionen, … ({labels})")
    w.go("backup_browser")
    wait(300)
    check(w.current_key() == "backups" and w.pages["backups"].tab_key() == "browser"
          and w.page("backup_browser").isVisible(), "go('backup_browser') → Backups › Durchsuchen & Vergleichen")
    w.go("project_view")
    wait(300)
    check(w.current_key() == "projects" and w.page("project_view").isVisible(), "go('project_view') → Projekte › Ansicht & Export")
    w.go("workshop")
    wait(300)
    check(w.current_key() == "compose" and w.page("workshop").isVisible(), "go('workshop') → Komponieren › Aus MIDI-Datei")
    w.go("fm1")
    wait(800)
    check(w.current_key() == "overview" and w.page("fm1").isVisible() and ctx.link._exclusive_depth == 1,
          "go('fm1') → Übersicht › Zustand (Seite hält das Gerät)")
    w.go("overview")
    wait(300)
    check(w.pages["overview"].tab_key() == "start" and ctx.link._exclusive_depth == 0,
          "go('overview') → Reiter Start, Zustand gibt das Gerät frei")
    w.go("soundmodule")
    wait(300)
    check(w.current_key() == "midi" and w.page("soundmodule").isVisible(), "go('soundmodule') → MIDI › Klangerzeuger")
    w.go("voices")
    wait(300)
    check(w.current_key() == "presets" and w.page("voices").isVisible(), "go('voices') → Presets › FM-Voices")
    check(w.page("compose").b_write is not None and w.page("backups").tree is not None,
          "page(<Hauptschlüssel>) reicht Attribute an die Hauptunterseite durch")
    shortcuts = {a.shortcut().toString(): a.text() for a in w.actions() if a.shortcut().toString().startswith("Ctrl+")}
    check(shortcuts.get("Ctrl+1") == "Übersicht" and shortcuts.get("Ctrl+2") == "Firmware+"
          and "Samples" in shortcuts.get("Ctrl+5", "") and len([k for k in shortcuts if k[-1].isdigit()]) == 9,
          f"Strg+1–9 in fester Reihenfolge ({[shortcuts.get(f'Ctrl+{i}') for i in range(1, 10)]})")
    from fm1companion.ui.about import collect_shortcuts
    names = [t for _a, t, _k in collect_shortcuts(w)]
    check(any(t == "Samples & Kits" for t in names) and not any("Samples  Kits" in t for t in names),
          "Tastenkürzel-Dialog zeigt „Samples & Kits“")
    from fm1companion.ui.palette import collect
    titles = {e.title for e in collect(w)}
    check({"Simulator", "FM-Voices (DX7)", "Klangerzeuger", "Zustand", "Aus MIDI-Datei"} <= titles,
          "Befehlssuche findet die Reiter der zusammengelegten Seiten")

    # ---- 2. Übersicht bei 1280×720
    w.go("overview")
    wait(500)
    ov = w.overview
    from PyQt6.QtWidgets import QLabel, QScrollArea
    titles = [lab.text() for lab in ov.findChildren(QLabel) if lab.property("role") == "cardTitle"]
    check("Bereiche" not in titles, f"Übersicht ohne Karte „Bereiche“ ({titles})")
    scroll = ov.findChild(QScrollArea)
    check(visible_in(ov.bk_card, scroll.viewport()), "„Sicherung“ bei 1280×720 ohne Rollen sichtbar")
    check(visible_in(ov.up_card, scroll.viewport()) and visible_in(ov.dev_card, scroll.viewport()),
          "„Gerät“ und „Firmware & Updates“ bei 1280×720 ohne Rollen sichtbar")
    check(set(ov.quick_buttons) == {"Backup jetzt", "Editor öffnen", "Firmware prüfen", "Presets sichern"},
          "Schnellzugriff: vier echte Aktionen")
    check(w.nav.verticalScrollBar().maximum() == 0, "Seitenleiste passt bei 720p ohne Rollen")
    check("voller Zugriff" in ov.lbl["Verbindung"].text(), f"Übersicht nutzt status_text ({ov.lbl['Verbindung'].text()})")

    # ---- 3. Rettung
    w.go("recovery")
    wait(300)
    rp = w.page("recovery")
    from fm1companion.ui.common import Disclosure
    discl = rp.findChildren(Disclosure)
    check(len(discl) >= 4 and all(not d.is_open() for d in discl), f"Rettung: {len(discl)} Details-Aufklapper, eingeklappt")
    check(rp.chk_sim.isChecked(), "Simulation im Demo-Modus vorangehakt")
    check("Live erkannt" in rp.status.text() or "Nicht erkannt" in rp.status.text(), "Rettung: Statuszeile live/nicht erkannt")
    discl[1].set_open(True)
    check(discl[1].content.isVisible(), "Details lassen sich aufklappen")
    discl[1].set_open(False)
    check(any("0x4000" in d.content.text() for d in discl if isinstance(d.content, QLabel)),
          "Hintergründe (Hex-Adressen) stecken in den Details")

    # ---- 4. Update-Kanal
    from fm1companion.config import Settings
    check(Settings().channel == "stable", "Standard-Update-Kanal ist stabil")
    from fm1companion.ui.wizard import FirstRunWizard
    wz = FirstRunWizard(ctx, w)
    check(wz.p_channel.stable.isChecked() and hasattr(wz, "p_autobackup"), "Assistent: stabil vorgewählt, Auto-Backup-Schritt vorhanden")
    wz.p_deps.initializePage()
    check(wz.p_deps.cmd_label.wordWrap(), "Assistent: apt-Befehl bricht um")
    wz.deleteLater()

    # ---- 6. Gerätewechsel ohne Warn-Toast
    w.notifications.clear()
    before = len(w.notifications.history)
    i = w.dev_combo.findData(next(p.key for p in ctx.ports if p.serial == "DEMO-2"))
    w.dev_combo.setCurrentIndex(i)
    w._choose_device(i)
    check(until(lambda: ctx.connected and ctx.firmware_key == "sloop"), "auf DEMO-2 (SLOOP) gewechselt")
    wait(500)
    new_notes = w.notifications.history[:len(w.notifications.history) - before]
    check(not any(n.level == "warning" for n in new_notes), f"kein Warn-Toast beim Gerätewechsel ({[n.text for n in new_notes]})")
    check(any(n.text.startswith("Aktiv:") for n in new_notes), "Info „Aktiv: …“ beim Gerätewechsel")
    check(w.notifications.unread() == 0, "Glocke zählt den Gerätewechsel nicht")

    # ---- 5. generisches Gerät: Übersicht/Editor
    i = w.dev_combo.findData(next(p.key for p in ctx.ports if p.serial == "DEMO-3"))
    w._choose_device(i)
    check(until(lambda: ctx.port and ctx.port.serial == "DEMO-3" and getattr(ctx, "protocol", "") == "generic", 15000),
          "DEMO-3 (ohne Editor-Protokoll) verbunden")
    wait(400)
    check("nur MIDI" in ov.lbl["Verbindung"].text() and "antwortet nicht" not in ov.lbl["Verbindung"].text(),
          f"Übersicht: generisches Gerät ohne „antwortet nicht“ ({ov.lbl['Verbindung'].text()})")
    w.go("editor")
    wait(1500)
    check("kein eingebauter Editor" in w.editor.state.text() or "keinen" in w.editor.state.text(),
          f"Editor: Leerzustand statt „Bereite … vor“ ({w.editor.state.text()})")
    fw_key = ctx.firmware_key
    check(not fw_key or w.firmware.current_spec() is None or w.firmware.current_spec().key == fw_key,
          f"Firmware-Seite folgt dem aktiven Gerät ({fw_key} / {w.firmware.current_spec().key if w.firmware.current_spec() else '-'})")

    # ---- 7. Plan-Dialog
    from fm1companion.ui.firmware_page import PlanDialog
    from fm1companion.flash import planner

    def plan_fn(via, backup):
        p = planner.InstallPlan(planner.Target("Test"), planner.DeviceState(felucca=True), lost=["Projekte"],
                                confirmations=["no_backup"])
        p.steps = [planner.Step("install", "Test aufspielen", "mittel", "schreibt", writes=True)]
        return p
    d = PlanDialog(w, plan_fn, "Test", False, None)
    d.show()
    wait(200)
    check(d.minimumWidth() >= 820 and d.minimumHeight() >= 680, f"Plan-Dialog ≥ 820×680 ({d.minimumWidth()}×{d.minimumHeight()})")
    check(d.lost_label.isVisible() and "Projekte" in d.lost_label.text(), "„Was danach nicht mehr auf dem Gerät ist“ sichtbar")
    check(not d.ok_btn.isEnabled() and d.ok_hint.isVisible() and "bestätigen" in d.ok_hint.text(),
          "Plan-Dialog: Installieren aus, Hinweis „Bitte oben bestätigen“")
    for cb in d.confirm_checks.values():
        cb.setChecked(True)
    check(d.ok_btn.isEnabled() and not d.ok_hint.isVisible(), "Plan-Dialog: nach Bestätigung aktiv, Hinweis weg")
    d.close()
    d.deleteLater()

    # ---- 11. Leerzustände
    vp = w.page("voices")
    check(vp.stack.currentIndex() == 0 if not vp.banks else True, "FM-Voices: Einstieg bei leerer Bibliothek")
    w.go("midi", tab="monitor")                  # (vorher stand der Reiter noch auf „Klangerzeuger“)
    wait(300)
    mon = w.page("midi").monitor
    check(w.page("midi").tabs.currentWidget() is mon, "go('midi', tab='monitor') wählt den Monitor-Reiter")
    check((mon.empty.isVisible() and mon.table.isHidden()) or mon.table.rowCount() > 0,
          "MIDI-Monitor: Leerzustand ohne Nachrichten")
    sp = w.samples
    check(sp.tabs.tabText(sp.dev_tab_index) == "Auf dem FM-1", "Samples: eigener Reiter „Auf dem FM-1“")

    # ---- 8. Begriffe
    import re
    src = "\n".join(p.read_text("utf-8") for p in (ROOT / "fm1companion/ui").rglob("*.py"))
    check(not re.search(r'"[^"\\n]*\\bEtikett', src), "Keine „Etiketten“ mehr in der Oberfläche")
    check('"Tags …"' not in src and '"Tags"' not in src, "Keine „Tags“ mehr in der Oberfläche")
    check(not re.search(r'"[^"\\n]*\\bEmulator-Seite', src), "„Emulator“ in Texten durch „Simulator“ ersetzt")

    w.close()
    app.quit()


def run():
    try:
        flow()
    except Exception:  # noqa: BLE001
        errors.append(traceback.format_exc())
        app.quit()


QTimer.singleShot(300, run)
QTimer.singleShot(300000, app.quit)
app.exec()
shutil.rmtree(home, ignore_errors=True)
fails = [r for r in results if not r[0]]
for e in errors:
    print("AUSNAHME:\n" + e)
print(f"{len(results) - len(fails)}/{len(results)} bestanden, {len(errors)} Ausnahmen")
sys.exit(1 if fails or errors else 0)
