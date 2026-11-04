# SPDX-License-Identifier: GPL-3.0-only
"""Fenstergröße: Jede Seite muss in ein 1280×720-Fenster passen (14"-Laptop, kleine Monitore).

Erstellt das Hauptfenster im Demo-Modus, stellt 1280×720 ein, besucht jede Seite (verbunden mit DEMO-1, dann
DEMO-2) und prüft minimumSizeHint() des Fensters. Optional: Bildschirmfotos aller Seiten in OUTDIR.

  flock /tmp/fm1c-ui.lock timeout 600 env QT_QPA_PLATFORM=offscreen python3 tests/ui_layout_test.py [OUTDIR]
  (FM1C_THEME=dark|light wählt das Design, Standard hell)
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
out = Path(sys.argv[1]) if len(sys.argv) > 1 else None
home = Path(tempfile.mkdtemp(prefix="fm1c-layout-"))
for k, sub in (("XDG_CONFIG_HOME", ".config"), ("XDG_DATA_HOME", ".local/share"), ("XDG_CACHE_HOME", ".cache")):
    os.environ[k] = str(home / sub)
os.environ["FM1C_DEMO"] = "1"
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu")

errors: list[str] = []
sys.excepthook = lambda t, v, tb: errors.append("".join(traceback.format_exception(t, v, tb)))

import PyQt6.QtWebEngineWidgets  # noqa: E402,F401
from PyQt6.QtCore import QCoreApplication, QEventLoop, Qt, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
app = QApplication(sys.argv)
from fm1companion.ui import theme  # noqa: E402

theme.apply(app, os.environ.get("FM1C_THEME", "light"))
from fm1companion import config  # noqa: E402

s = config.load()
s.user_root = str(home / "FM-1")
s.check_on_start = False
config.save(s)
config.ensure_dirs()
QMessageBox.exec = lambda self: 0
QDialog.exec = lambda self: 0

from fm1companion.ui.main_window import MainWindow  # noqa: E402

W, H = 1280, 720
w = MainWindow()
w.resize(W, H)
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


def inside(wd) -> bool:
    """liegt das Widget vollständig in allen Eltern (nicht abgeschnitten)?"""
    from PyQt6.QtCore import QRect
    r = QRect(wd.mapTo(w, wd.rect().topLeft()), wd.size())
    p = wd.parentWidget()
    while p is not None and p is not w:
        pr = QRect(p.mapTo(w, p.rect().topLeft()), p.size())
        if not pr.contains(r):
            return False
        p = p.parentWidget()
    return True


def visit(tag: str):
    worst = []
    for key in list(w.specs):
        w.go(key)
        wait(250 if key not in ("editor", "simulator", "discover") else 900)
        p = w.pages[key]
        m = p.minimumSizeHint().expandedTo(p.minimumSize())
        mw = w.minimumSizeHint()
        if mw.width() > W or mw.height() > H:
            worst.append(f"{key}: Seite {m.width()}×{m.height()}, Fenster {mw.width()}×{mw.height()}")
        if out:
            w.grab().save(str(out / f"{tag}_{key}.png"))
    return worst


def flow():
    ctx = w.ctx
    check(until(lambda: ctx.connected), "Demo-Gerät verbunden")
    wait(300)
    check(w.width() == W and w.height() == H, f"Fenster lässt sich auf {W}×{H} stellen ({w.width()}×{w.height()})")
    bad = visit("demo1")
    check(not bad, "DEMO-1: jede Seite passt in 1280×720" + ("" if not bad else " – zu groß: " + "; ".join(bad)))
    ctx.connect_port(next(p for p in ctx.ports if p.serial == "DEMO-2"))
    check(until(lambda: ctx.connected and ctx.port.serial == "DEMO-2", 15000), "DEMO-2 verbunden")
    bad = visit("demo2")
    check(not bad, "DEMO-2: jede Seite passt in 1280×720" + ("" if not bad else " – zu groß: " + "; ".join(bad)))
    m = w.minimumSizeHint()
    check(m.width() <= W and m.height() <= H, f"Fenster-Mindestgröße {m.width()}×{m.height()} ≤ {W}×{H}")
    # Kit-Baukasten bei 1280 ganz sichtbar (rechte Spalte breit genug, keine abgeschnittenen Knöpfe)
    w.go("samples")
    wait(300)
    sp = w.samples
    right = sp.kit_name.parentWidget()
    while right is not None and right.objectName() != "qt_scrollarea_viewport":
        right = right.parentWidget()
    sc = right.parentWidget() if right is not None else None
    inner = sc.widget() if sc is not None else None
    check(inner is not None and inner.width() <= sc.viewport().width() + 1 and sc.width() >= 300,
          f"Kit-Baukasten ganz sichtbar (Spalte {sc.width() if sc else '?'} px, ohne waagrechtes Rollen)")
    w.go("compose")
    wait(200)
    from PyQt6.QtWidgets import QPushButton
    cp = w.page("compose").sub("erzeugen")          # Reiter „Erzeugen“ (der Reiter „Aus MIDI-Datei“ hat eigene Knöpfe)
    for text in ("Als MIDI speichern", "Rückgängig", "In Pattern schreiben"):
        b = next(x for x in cp.findChildren(QPushButton) if x.text().startswith(text))
        check(b.isVisible() and b.width() >= b.sizeHint().width() - 2 and inside(b),
              f"Komponieren: „{text} …“ ganz sichtbar, nicht abgeschnitten")
    app.quit()


def run():
    try:
        flow()
    except Exception:  # noqa: BLE001
        errors.append(traceback.format_exc())
        app.quit()


if out:
    out.mkdir(parents=True, exist_ok=True)
QTimer.singleShot(500, run)
QTimer.singleShot(300000, app.quit)
app.exec()
fails = [r for r in results if not r[0]]
for e in errors:
    print("AUSNAHME:\n" + e)
print(f"{len(results) - len(fails)}/{len(results)} bestanden, {len(errors)} Ausnahmen")
shutil.rmtree(home, ignore_errors=True)
sys.stdout.flush()
os._exit(1 if fails or errors else 0)    # ohne Qt-Aufräumen (WebEngine stürzt sonst beim Beenden ab)
