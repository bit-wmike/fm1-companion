# SPDX-License-Identifier: GPL-3.0-only
"""Rahmen der App: Navigation (alte Schlüssel, Tastatur, Strg+1…9), Gerätezeile, Hilfe/Glossar, Dialoge
(Einstellungen, Über, Tastenkürzel, Assistent, Was ist neu, Systemcheck, Benachrichtigungen, Befehlssuche)
bei 1280×720 in hell und dunkel, Speicherfehler der Einstellungen (einmal melden, nie abstürzen).

  QT_QPA_PLATFORM=offscreen python3 tests/ui_frame_test.py [SCREENSHOT-ORDNER]
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
home = Path(tempfile.mkdtemp(prefix="fm1c-frame-"))
for k, sub in (("XDG_CONFIG_HOME", ".config"), ("XDG_DATA_HOME", ".local/share"), ("XDG_CACHE_HOME", ".cache")):
    os.environ[k] = str(home / sub)
os.environ["FM1C_DEMO"] = "1"
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu")
shots = Path(sys.argv[1]) if len(sys.argv) > 1 else None

errors: list[str] = []
sys.excepthook = lambda t, v, tb: errors.append("".join(traceback.format_exception(t, v, tb)))
import threading  # noqa: E402
threading.excepthook = lambda a: errors.append("".join(traceback.format_exception(a.exc_type, a.exc_value,
                                                                                  a.exc_traceback)))

import PyQt6.QtWebEngineWidgets  # noqa: E402,F401
from PyQt6.QtCore import QCoreApplication, QEventLoop, Qt, QTimer  # noqa: E402
from PyQt6.QtGui import QKeySequence  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog, QLabel, QMessageBox  # noqa: E402

QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
app = QApplication(sys.argv)
from fm1companion import __version__, config  # noqa: E402
from fm1companion.ui import theme  # noqa: E402

s = config.load()
s.user_root = str(home / "FM-1")
s.check_on_start = False
config.save(s)
config.ensure_dirs()
QMessageBox.exec = lambda self: 0

from fm1companion.ui.main_window import ALIASES, COMPOSITES, HOTKEY_ORDER, MainWindow, needs_maximize  # noqa: E402

passed = total = 0


def check(name, cond, detail=""):
    global passed, total
    total += 1
    if cond:
        passed += 1
        print(f"  ok    {name}")
    else:
        print(f"  FEHLT {name}  {detail}")


def wait(ms):
    end = time.time() + ms / 1000
    while time.time() < end:
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
        time.sleep(0.01)


def until(fn, ms=8000):
    end = time.time() + ms / 1000
    while time.time() < end:
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
        try:
            if fn():
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.02)
    return False


theme.apply(app, "light")
w = MainWindow()
w.resize(1280, 720)
w.show()
until(lambda: w.ctx.connected, 15000)
wait(500)

print("Fenstergröße")
check("Laptop 1280×720 (Leiste 40 px) -> maximieren", needs_maximize(1280, 680, 1280, 820))
check("Desktop 1920×1080 -> Standardgröße", not needs_maximize(1920, 1040, 1280, 820))

print("Navigation: alte Schlüssel")
for old, (main, tab) in ALIASES.items():
    w.go("overview")
    w.go(old)
    wait(150)
    page = w.pages.get(main)
    cur_tab = page.tab_key() if hasattr(page, "tab_key") else None
    if cur_tab is None and hasattr(page, "tabs"):
        sub = w.page(old)
        ok_tab = page.tabs.currentWidget() is sub
    else:
        ok_tab = cur_tab == tab
    check(f"go({old!r}) -> {main} › {tab}", w.current_key() == main and ok_tab and w.page(old) is not None,
          f"{w.current_key()} {cur_tab}")
w.go("fm1")
w.go("overview")
check("go('overview') landet wieder auf „Start“", w.pages["overview"].tab_key() == "start")
for main, tabs in COMPOSITES.items():
    check(f"go({main!r}, tab=…) wählt den letzten Reiter", (w.go(main, tab=tabs[-1][0]) or True)
          and w.pages[main].tab_key() == tabs[-1][0])
w.go("gibt-es-nicht")
check("unbekannter Schlüssel: keine Ausnahme, Seite bleibt", w.current_key() in w.pages)

print("Navigation: Tastatur")
w.go("overview")
w.nav.setFocus()
wait(100)
seen, headers = ["overview"], 0
for _ in range(w.nav.count() + 2):
    QTest.keyClick(w.nav, Qt.Key.Key_Down)
    app.processEvents()
    it = w.nav.currentItem()
    if it is not None and it.data(Qt.ItemDataRole.UserRole) is None:
        headers += 1
    seen.append(w.current_key())
check("Pfeil runter landet nie auf einer Gruppenüberschrift", headers == 0, f"{headers}×")
check("Pfeil runter erreicht jede Seite", set(seen) >= set(w.pages), str(set(w.pages) - set(seen)))
QTest.keyClick(w.nav, Qt.Key.Key_Home)
app.processEvents()
check("Pos1 springt zur ersten Seite (nicht auf die Überschrift)", w.current_key() == "overview"
      and w.nav.currentItem().data(Qt.ItemDataRole.UserRole) == "overview")
QTest.keyClick(w.nav, Qt.Key.Key_Up)
app.processEvents()
check("Pfeil hoch auf der ersten Seite bleibt dort", w.nav.currentItem().data(Qt.ItemDataRole.UserRole) == "overview")
QTest.keyClick(w.nav, Qt.Key.Key_End)
app.processEvents()
check("Ende springt zur letzten Seite", w.nav.currentItem().data(Qt.ItemDataRole.UserRole) is not None
      and w.current_key() == w.nav.item(w.nav.count() - 1).data(Qt.ItemDataRole.UserRole))
keys = [k for k in HOTKEY_ORDER if k in w.specs]
for i, key in enumerate(keys[:9]):
    QTest.keyClick(w, Qt.Key(Qt.Key.Key_1.value + i), Qt.KeyboardModifier.ControlModifier)
    app.processEvents()
check("Strg+9 öffnet die letzte Kurzwahl-Seite", w.current_key() == keys[min(8, len(keys) - 1)])
w.go("overview")
w.nav.setFocus()
QTest.keyClick(w.nav, Qt.Key.Key_Tab)
app.processEvents()
fw = app.focusWidget()
check("Tab aus der Seitenleiste führt in die Seite", fw is not None and fw is not w.nav and w.stack.isAncestorOf(fw)
      or (fw is not None and fw.window() is w and fw is not w.nav), type(fw).__name__)
acts = {a.text(): a for a in w.findChildren(type(w.a_sidebar))}
pref = next((a for t, a in acts.items() if t.startswith("Einstellungen")), None)
check("Einstellungen: Strg+, (wie in der Hilfe)", pref is not None and pref.shortcut() == QKeySequence("Ctrl+,"))

print("Gerätezeile")
check("verbunden: Name und Zustand", "verbunden" in w.dev_state.text() and "FM-1 A" in w.dev_state.text())
w.ctx.disconnect(None)
w._dev_changed()
txt = w.dev_state.text()
check("ohne Gerät nicht doppelt („Kein FM-1 verbunden – nicht verbunden“)",
      "Kein FM-1 verbunden" in txt and "nicht verbunden" not in txt, txt)
w._reconnect()
until(lambda: w.ctx.connected, 15000)
check("Neu verbinden (F5) stellt die Verbindung wieder her", w.ctx.connected)

print("Hilfe und Glossar")
w.go("help")
wait(400)
hp = w.page("help")
check("Hilfe beginnt oben (Überschrift sichtbar)", hp.browser.verticalScrollBar().value() == 0,
      str(hp.browser.verticalScrollBar().value()))
check("Glossar ist ein Hilfe-Thema", hp.show_topic("12-glossar") and "Felucca" in hp.browser.toPlainText())
check("Glossar beginnt oben", hp.browser.verticalScrollBar().value() == 0)
hp.search.setText("Update-Modus")
wait(200)
check("Suche findet das Glossar", not hp.list.item([k for k, _t, _m in hp.topics].index("12-glossar")).isHidden())
hp.search.clear()
bad = []
import re  # noqa: E402
for key, _t, md in hp.topics:
    for link in re.findall(r"\]\((app://page/[^)]+|help:[^)]+)\)", md):
        if link.startswith("app://page/") and w.page(link[len("app://page/"):]) is None:
            bad.append(f"{key}: {link}")
        if link.startswith("help:") and link[5:] not in [k for k, _t, _m in hp.topics]:
            bad.append(f"{key}: {link}")
check("alle App-Links der Hilfe führen auf vorhandene Seiten/Themen", not bad, "; ".join(bad))

print("Einstellungen: Speichern scheitert")
real_dir = config.CONFIG_DIR
blocker = home / "kein-ordner"
blocker.write_text("x")
config.CONFIG_DIR = blocker / "fm1-companion"
config.CONFIG_FILE = config.CONFIG_DIR / "config.json"
n_before = len(w.notifications.history)
ok1 = config.save()
w.set_theme("dark")
w.toggle_sidebar(True)
until(lambda: len(w.notifications.history) > n_before, 3000)
wait(300)
warns = [n for n in w.notifications.history[: len(w.notifications.history) - n_before]
         if "nicht gespeichert" in n.text]
check("config.save meldet False statt Ausnahme", ok1 is False and "Datei statt eines Ordners" in config.save_error,
      config.save_error)
check("Design wechseln trotz Fehler (keine Ausnahme)", not errors, errors[-1][-300:] if errors else "")
check("genau EIN Hinweis, nicht bei jedem Klick", len(warns) == 1, str(len(warns)))
config.CONFIG_DIR = real_dir
config.CONFIG_FILE = real_dir / "config.json"
check("danach wieder speicherbar", config.save() and config.save_error == "")


# ---------------------------------------------------------------- Dialoge
def dialogs():
    from fm1companion.ui.about import AboutDialog, ShortcutsDialog
    from fm1companion.ui.notify import HistoryDialog
    from fm1companion.ui.settings_dialog import SettingsDialog
    from fm1companion.ui.statusbar import SystemCheckDialog
    from fm1companion.ui.wizard import FirstRunWizard, WhatsNewDialog
    return [("einstellungen", lambda: SettingsDialog(w.ctx, w)), ("ueber", lambda: AboutDialog(w)),
            ("kuerzel", lambda: ShortcutsDialog(w)), ("assistent", lambda: FirstRunWizard(w.ctx, w)),
            ("neu", lambda: WhatsNewDialog(w)), ("systemcheck", lambda: SystemCheckDialog(w.ctx, w)),
            ("hinweise", lambda: HistoryDialog(w.notifications, w))]


def english(widget) -> list[str]:
    words = re.compile(r"\b(Cancel|Close|Settings|Error|Warning|Loading|Save|Open|Delete|Yes|No|Back|Next|Finish|"
                       r"Apply|Help|Search|Unknown|None)\b")
    out = []
    for lab in widget.findChildren(QLabel):
        t = re.sub(r"<[^>]+>", " ", lab.text())
        if lab.isVisible() and words.search(t):
            out.append(t[:60])
    from PyQt6.QtWidgets import QAbstractButton
    for b in widget.findChildren(QAbstractButton):
        if b.isVisible() and words.fullmatch(b.text().replace("&", "").strip() or "-"):
            out.append(b.text())
    return out


for mode in ("light", "dark"):
    print(f"Dialoge ({mode})")
    w.set_theme(mode)
    wait(200)
    for name, make in dialogs():
        try:
            d = make()
        except Exception:  # noqa: BLE001
            check(f"{name}: öffnet", False, traceback.format_exc()[-300:])
            continue
        d.show()
        wait(600 if name != "systemcheck" else 1500)
        hint = d.sizeHint().expandedTo(d.minimumSizeHint())
        check(f"{name}: passt in 1280×720 ({d.width()}×{d.height()}, min {d.minimumSizeHint().width()}×"
              f"{d.minimumSizeHint().height()})",
              d.minimumSizeHint().width() <= 1280 and d.minimumSizeHint().height() <= 680
              and d.width() <= 1280 and d.height() <= 680)
        check(f"{name}: Titel deutsch und gesetzt", bool(d.windowTitle()) and not english(d), str(english(d)))
        if shots:
            shots.mkdir(parents=True, exist_ok=True)
            d.grab().save(str(shots / f"{mode}_{name}.png"))
        d.reject() if hasattr(d, "reject") else d.close()
        d.deleteLater()
        wait(100)
    pal = w.open_palette()
    wait(300)
    check("Befehlssuche öffnet", pal is not None and pal.isVisible())
    check("Befehlssuche ohne waagerechte Bildlaufleiste", not pal.list.horizontalScrollBar().isVisible())
    if shots:
        w.grab().save(str(shots / f"{mode}_fenster_palette.png"))
    pal.close()
    w.go("help")
    hp.show_topic("12-glossar")
    wait(300)
    if shots:
        w.grab().save(str(shots / f"{mode}_glossar.png"))

print("Assistent")
from fm1companion.ui.wizard import FirstRunWizard  # noqa: E402
from PyQt6.QtWidgets import QWizard  # noqa: E402
wz = FirstRunWizard(w.ctx, w)
wz.show()
wait(300)
wz.next()
wait(400)
check("Assistent: „Weiter“ auf der Ordner-Seite bedienbar", wz.button(QWizard.WizardButton.NextButton).isEnabled())
pages = 1
while wz.nextId() != -1 and pages < 12:
    wz.next()
    wait(300)
    pages += 1
check("Assistent: bis zur letzten Seite durchklickbar", wz.nextId() == -1, str(pages))
wz.reject()
wz.deleteLater()

print("Version überall gleich")
from fm1companion.ui.about import AboutDialog  # noqa: E402
ab = AboutDialog(w)
check("Über-Dialog nennt die Version", any(__version__ in lab.text() for lab in ab.findChildren(QLabel)))
brand = [lab.text() for lab in w.sidebar.findChildren(QLabel) if "Suite" in lab.text()]
check("Seitenleiste nennt die Version", any(__version__ in t for t in brand), str(brand))
check("QApplication-Version (falls gesetzt) gleich", app.applicationVersion() in ("", __version__))
ab.deleteLater()

n_hist = len(w.notifications.history)
w.close()
wait(300)
check("Beenden meldet kein „FM-1 getrennt“", len(w.notifications.history) == n_hist,
      w.notifications.history[0].text if w.notifications.history else "")
print(f"{passed}/{total} bestanden, {len(errors)} Ausnahmen")
for e in errors:
    print(e)
shutil.rmtree(home, ignore_errors=True)
# ohne Qt-Abbau beenden: sonst schreibt QtWebEngine beim Zerstören des Profils seinen Cache-Index erneut in den
# gerade gelöschten Testordner (Reste in /tmp). Sauberes Beenden prüft tests/test_appstart.py.
sys.stdout.flush()
sys.stderr.flush()
os._exit(0 if passed == total and not errors else 1)
