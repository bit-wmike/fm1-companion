# SPDX-License-Identifier: GPL-3.0-only
"""Usability review 2.0 (offscreen, demo mode): merged navigation with redirection of old keys,
Overview without "Bereiche" (areas) and without scrolling at 1280×720, Recovery with details disclosures, default
channel stable, quiet device switch, plan dialog size, empty states, terms.

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
os.environ.setdefault("FM1C_LANG", "de")      # these checks expect the German user interface
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
    """the widget lies completely in the visible area of the container (without scrolling)."""
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
    check(until(lambda: ctx.connected), "demo device connected")

    # ---- 1. Navigation
    entries = [w.nav.item(i).text() for i in range(w.nav.count()) if w.nav.item(i).data(Qt.ItemDataRole.UserRole)]
    check(len(entries) <= 14, f"navigation has {len(entries)} entries (≤ 14): {entries}")
    for old in ("backup_browser", "project_view", "workshop", "fm1", "voices", "soundmodule", "simulator", "firmware"):
        check(old in ALIASES, f"old key \"{old}\" is registered as a redirect")
    # Firmware+ (formerly Discover + Firmware): ONE entry, together with Recovery under "Data & Tools"
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
          f"Firmware+ and Recovery under \"Data & Tools\", only one firmware entry ({groups})")
    w.go("firmware")
    wait(300)
    disc = w.pages["discover"]
    check(w.current_key() == "discover" and disc.tabs.currentWidget() is w.firmware and w.page("firmware") is w.firmware
          and w.firmware.isVisible(), "go('firmware') → Firmware+ › Install & Versions")
    labels = [disc.tabs.tabText(i).replace("&&", "&") for i in range(disc.tabs.count())]
    check(labels[:2] == ["Alle Firmwares", "Installieren & Versionen"] and "Simulator" in labels,
          f"tabs of Firmware+: catalog, Install & Versions, … ({labels})")
    w.go("backup_browser")
    wait(300)
    check(w.current_key() == "backups" and w.pages["backups"].tab_key() == "browser"
          and w.page("backup_browser").isVisible(), "go('backup_browser') → Backups › Browse & Compare")
    w.go("project_view")
    wait(300)
    check(w.current_key() == "projects" and w.page("project_view").isVisible(), "go('project_view') → Projects › View & Export")
    w.go("workshop")
    wait(300)
    check(w.current_key() == "compose" and w.page("workshop").isVisible(), "go('workshop') → Compose › From MIDI File")
    w.go("fm1")
    wait(800)
    check(w.current_key() == "overview" and w.page("fm1").isVisible() and ctx.link._exclusive_depth == 1,
          "go('fm1') → Overview › Status (the page holds the device)")
    w.go("overview")
    wait(300)
    check(w.pages["overview"].tab_key() == "start" and ctx.link._exclusive_depth == 0,
          "go('overview') → tab Start, Status releases the device")
    w.go("soundmodule")
    wait(300)
    check(w.current_key() == "midi" and w.page("soundmodule").isVisible(), "go('soundmodule') → MIDI › Sound Module")
    w.go("voices")
    wait(300)
    check(w.current_key() == "presets" and w.page("voices").isVisible(), "go('voices') → Presets › FM-Voices")
    check(w.page("compose").b_write is not None and w.page("backups").tree is not None,
          "page(<main key>) passes attributes on to the main sub-page")
    shortcuts = {a.shortcut().toString(): a.text() for a in w.actions() if a.shortcut().toString().startswith("Ctrl+")}
    check(shortcuts.get("Ctrl+1") == "Übersicht" and shortcuts.get("Ctrl+2") == "Firmware+"
          and "Samples" in shortcuts.get("Ctrl+5", "") and len([k for k in shortcuts if k[-1].isdigit()]) == 9,
          f"Ctrl+1–9 in a fixed order ({[shortcuts.get(f'Ctrl+{i}') for i in range(1, 10)]})")
    from fm1companion.ui.about import collect_shortcuts
    names = [t for _a, t, _k in collect_shortcuts(w)]
    check(any(t == "Samples & Kits" for t in names) and not any("Samples  Kits" in t for t in names),
          "shortcuts dialog shows \"Samples & Kits\"")
    from fm1companion.ui.palette import collect
    titles = {e.title for e in collect(w)}
    check({"Simulator", "FM-Voices (DX7)", "Klangerzeuger", "Zustand", "Aus MIDI-Datei"} <= titles,
          "command search finds the tabs of the merged pages")

    # ---- 2. Overview at 1280×720
    w.go("overview")
    wait(500)
    ov = w.overview
    from PyQt6.QtWidgets import QLabel, QScrollArea
    titles = [lab.text() for lab in ov.findChildren(QLabel) if lab.property("role") == "cardTitle"]
    check("Bereiche" not in titles, f"Overview without the card \"Bereiche\" (areas) ({titles})")
    scroll = ov.findChild(QScrollArea)
    check(visible_in(ov.bk_card, scroll.viewport()), "\"Backup\" card visible at 1280×720 without scrolling")
    check(visible_in(ov.up_card, scroll.viewport()) and visible_in(ov.dev_card, scroll.viewport()),
          "\"Device\" and \"Firmware & Updates\" visible at 1280×720 without scrolling")
    check(set(ov.quick_buttons) == {"Backup jetzt", "Editor öffnen", "Firmware prüfen", "Presets sichern"},
          "quick access: four real actions")
    check(w.nav.verticalScrollBar().maximum() == 0, "sidebar fits at 720p without scrolling")
    check("voller Zugriff" in ov.lbl["Verbindung"].text(), f"Overview uses status_text ({ov.lbl['Verbindung'].text()})")

    # ---- 3. Recovery
    w.go("recovery")
    wait(300)
    rp = w.page("recovery")
    from fm1companion.ui.common import Disclosure
    discl = rp.findChildren(Disclosure)
    check(len(discl) >= 4 and all(not d.is_open() for d in discl), f"Recovery: {len(discl)} details disclosures, collapsed")
    check(rp.chk_sim.isChecked(), "simulation pre-ticked in demo mode")
    check("Live erkannt" in rp.status.text() or "Nicht erkannt" in rp.status.text(), "Recovery: status line live/not detected")
    discl[1].set_open(True)
    check(discl[1].content.isVisible(), "details can be expanded")
    discl[1].set_open(False)
    check(any("0x4000" in d.content.text() for d in discl if isinstance(d.content, QLabel)),
          "background (hex addresses) is in the details")

    # ---- 4. update channel
    from fm1companion.config import Settings
    check(Settings().channel == "stable", "default update channel is stable")
    from fm1companion.ui.wizard import FirstRunWizard
    wz = FirstRunWizard(ctx, w)
    check(wz.p_channel.stable.isChecked() and hasattr(wz, "p_autobackup"), "wizard: stable preselected, auto backup step present")
    wz.p_deps.initializePage()
    check(wz.p_deps.cmd_label.wordWrap(), "wizard: apt command wraps")
    wz.deleteLater()

    # ---- 6. device switch without a warning toast
    w.notifications.clear()
    before = len(w.notifications.history)
    i = w.dev_combo.findData(next(p.key for p in ctx.ports if p.serial == "DEMO-2"))
    w.dev_combo.setCurrentIndex(i)
    w._choose_device(i)
    check(until(lambda: ctx.connected and ctx.firmware_key == "sloop"), "switched to DEMO-2 (SLOOP)")
    wait(500)
    new_notes = w.notifications.history[:len(w.notifications.history) - before]
    check(not any(n.level == "warning" for n in new_notes), f"no warning toast on device switch ({[n.text for n in new_notes]})")
    check(any(n.text.startswith("Aktiv:") for n in new_notes), "info \"Aktiv: …\" (active) on device switch")
    check(w.notifications.unread() == 0, "the bell does not count the device switch")

    # ---- 5. generic device: Overview/editor
    i = w.dev_combo.findData(next(p.key for p in ctx.ports if p.serial == "DEMO-3"))
    w._choose_device(i)
    check(until(lambda: ctx.port and ctx.port.serial == "DEMO-3" and getattr(ctx, "protocol", "") == "generic", 15000),
          "DEMO-3 (without editor protocol) connected")
    wait(400)
    check("nur MIDI" in ov.lbl["Verbindung"].text() and "antwortet nicht" not in ov.lbl["Verbindung"].text(),
          f"Overview: generic device without \"antwortet nicht\" (not responding) ({ov.lbl['Verbindung'].text()})")
    w.go("editor")
    wait(1500)
    check("kein eingebauter Editor" in w.editor.state.text() or "keinen" in w.editor.state.text(),
          f"editor: empty state instead of \"Bereite … vor\" (preparing) ({w.editor.state.text()})")
    fw_key = ctx.firmware_key
    check(not fw_key or w.firmware.current_spec() is None or w.firmware.current_spec().key == fw_key,
          f"firmware page follows the active device ({fw_key} / {w.firmware.current_spec().key if w.firmware.current_spec() else '-'})")

    # ---- 7. plan dialog
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
    check(d.minimumWidth() >= 820 and d.minimumHeight() >= 680, f"plan dialog ≥ 820×680 ({d.minimumWidth()}×{d.minimumHeight()})")
    check(d.lost_label.isVisible() and "Projekte" in d.lost_label.text(), "\"What is no longer on the device afterwards\" visible")
    check(not d.ok_btn.isEnabled() and d.ok_hint.isVisible() and "bestätigen" in d.ok_hint.text(),
          "plan dialog: Install off, hint \"Bitte oben bestätigen\" (please confirm above)")
    for cb in d.confirm_checks.values():
        cb.setChecked(True)
    check(d.ok_btn.isEnabled() and not d.ok_hint.isVisible(), "plan dialog: active after confirming, hint gone")
    d.close()
    d.deleteLater()

    # ---- 11. empty states
    vp = w.page("voices")
    check(vp.stack.currentIndex() == 0 if not vp.banks else True, "FM Voices: entry point with an empty library")
    w.go("midi", tab="monitor")                  # (before, the tab was still on "Sound Module")
    wait(300)
    mon = w.page("midi").monitor
    check(w.page("midi").tabs.currentWidget() is mon, "go('midi', tab='monitor') selects the monitor tab")
    check((mon.empty.isVisible() and mon.table.isHidden()) or mon.table.rowCount() > 0,
          "MIDI monitor: empty state without messages")
    sp = w.samples
    check(sp.tabs.tabText(sp.dev_tab_index) == "Auf dem FM-1", "Samples: own tab \"Auf dem FM-1\" (On the FM-1)")

    # ---- 8. terms
    import re
    src = "\n".join(p.read_text("utf-8") for p in (ROOT / "fm1companion/ui").rglob("*.py"))
    check(not re.search(r'"[^"\\n]*\\bEtikett', src), "no \"Etiketten\" (labels) in the UI any more")
    check('"Tags …"' not in src and '"Tags"' not in src, "no \"Tags\" in the UI any more")
    check(not re.search(r'"[^"\\n]*\\bEmulator-Seite', src), "\"Emulator\" in texts replaced by \"Simulator\"")

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
    print("EXCEPTION:\n" + e)
print(f"{len(results) - len(fails)}/{len(results)} passed, {len(errors)} exceptions")
sys.exit(1 if fails or errors else 0)
