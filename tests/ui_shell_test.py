# SPDX-License-Identifier: GPL-3.0-only
"""Rahmen der Suite (offscreen, Demo-Modus): Design, Symbole, Befehlssuche, Assistent, Hinweise, Absturz-Dialog,
Statusleiste, Systemcheck, Hilfe-Center, Diagnose (mit kaputter Seite in einer Kopie des Pakets).

  QT_QPA_PLATFORM=offscreen python3 tests/ui_shell_test.py
"""
from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("FM1C_LANG", "de")      # these checks expect the German user interface
home = Path(tempfile.mkdtemp(prefix="fm1c-shell-"))
for k, sub in (("XDG_CONFIG_HOME", ".config"), ("XDG_DATA_HOME", ".local/share"), ("XDG_CACHE_HOME", ".cache"),
               ("XDG_STATE_HOME", ".local/state")):
    os.environ[k] = str(home / sub)
os.environ["FM1C_DEMO"] = "1"
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu")

errors: list[str] = []
sys.excepthook = lambda t, v, tb: errors.append("".join(traceback.format_exception(t, v, tb)))
qt_msgs: list[str] = []

import PyQt6.QtWebEngineWidgets  # noqa: E402,F401
from PyQt6.QtCore import QCoreApplication, QEventLoop, Qt, QTimer, QUrl, qInstallMessageHandler  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox, QWizard  # noqa: E402

qInstallMessageHandler(lambda mode, ctx, msg: qt_msgs.append(msg))
QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
app = QApplication(sys.argv)

from fm1companion import config  # noqa: E402

s = config.load()
s.user_root = str(home / "FM-1")
s.check_on_start = False
config.save(s)
config.ensure_dirs()

QMessageBox.exec = lambda self: 0
_dialog_exec = QDialog.exec
QDialog.exec = lambda self: 1 if not isinstance(self, QWizard) else _dialog_exec(self)

from fm1companion.ui import common, theme  # noqa: E402
from fm1companion.ui.main_window import MainWindow  # noqa: E402

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


def icon_calls() -> list[tuple[str, ...]]:
    """alle icon("…", …)-Aufrufe mit festen Namen im Paket + Symbole aller PageSpecs."""
    calls = set()
    for f in (ROOT / "fm1companion").rglob("*.py"):
        for node in ast.walk(ast.parse(f.read_text("utf-8"))):
            if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", "")) == "icon":
                names = tuple(a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str))
                if names and len(names) == len(node.args):
                    calls.add(names)
    from fm1companion.ui.pages import discover
    from fm1companion.ui.main_window import PAGES
    calls |= {s.icons for s in discover() if s.icons} | {p[2] for p in PAGES}
    return sorted(calls)


w = None


def flow():
    global w
    # ---- Design
    for mode in ("light", "dark", "auto", "system"):
        qt_msgs.clear()
        theme.apply(app, mode)
        bad = [m for m in qt_msgs if "style" in m.lower() or "parse" in m.lower()]
        check(not bad, f"Design '{mode}' ohne Qt-Warnungen {bad[:2]}")
    for dark in (False, True):
        t = theme.make_tokens(dark)
        check(theme.contrast(t.text, t.bg) >= 7 and theme.contrast(t.muted, t.bg) >= 4.5
              and theme.contrast(t.on_accent, t.accent) >= 4.5 and theme.contrast(t.accent_fg, t.bg) >= 4.5,
              f"Kontraste {'dunkel' if dark else 'hell'} (Text, Nebentext, Akzent)")
        for col in ("#3daee9", "#f5b82e", "#9b6bff"):
            t2 = theme.make_tokens(dark, col)
            if theme.contrast(t2.on_accent, t2.accent) < 4.5:
                check(False, f"Akzent {col} lesbar")
    theme.apply(app, "dark")
    check(theme.is_dark(), "dunkles Design aktiv")

    # ---- Symbole
    calls = icon_calls()
    missing = [c for c in calls if common.icon(*c).isNull()]
    check(len(calls) > 40 and not missing, f"{len(calls)} Symbol-Aufrufe aufgelöst, fehlend: {missing}")
    pm = common.icon("document-save").pixmap(24, 24).toImage()
    px = [pm.pixelColor(x, y) for x in range(24) for y in range(24) if pm.pixelColor(x, y).alpha() > 200]
    check(px and min(c.lightness() for c in px) > 150, "Symbol im dunklen Design hell eingefärbt")
    theme.apply(app, "light")
    pm = common.icon("document-save").pixmap(24, 24).toImage()
    px = [pm.pixelColor(x, y) for x in range(24) for y in range(24) if pm.pixelColor(x, y).alpha() > 200]
    check(px and max(c.lightness() for c in px) < 100, "dasselbe Symbol im hellen Design dunkel (Umfärbung live)")
    check(not common.icon("gibt-es-nicht-xyz").isNull() is False, "unbekannter Name bleibt leer")

    # ---- Hauptfenster
    w = MainWindow()
    w.resize(1280, 800)
    w.show()
    ctx = w.ctx
    check(until(lambda: ctx.connected), "Demo-Gerät verbunden")
    check(w.page("help") is not None, "Hilfe-Seite angemeldet")
    from fm1companion import __version__
    check(__version__ == "2.4.0", "Version 2.4.0")

    # ---- Befehlssuche
    pal = w.open_palette()
    pal.input.setText("backups")
    res = pal.results()
    check(res and res[0].title == "Backups" and res[0].kind == "Seite", f"Befehlssuche findet 'Backups' ({res[0].title if res else '-'})")
    pal.input.setText("smpl")
    check(any(e.title.startswith("Samples") for e in pal.results()[:3]), "unscharfe Suche 'smpl' → Samples & Kits")
    pal.input.setText("backups")
    pal.execute()
    check(w.stack.currentWidget() is w.page("backups"), "Enter öffnet die Seite")
    pal = w.open_palette()
    pal.input.setText("vollbild")
    check(any(e.kind == "Aktion" and "Vollbild" in e.title for e in pal.results()), "Menü-Aktionen in der Suche")
    hit = []
    w.register_command("Testbefehl XY", lambda: hit.append(1), keywords=("zzq",))
    pal.close()
    pal = w.open_palette()
    pal.input.setText("zzq")
    pal.execute()
    check(hit == [1], "eigener Befehl ausgeführt")

    # ---- Hinweise
    n0 = len(w.notifications.history)
    acted = []
    w.notify("Test-Hinweis", "success", ("Los", lambda: acted.append(1)))
    from fm1companion.ui.notify import notify as mod_notify
    import threading
    threading.Thread(target=lambda: mod_notify("aus dem Thread", "warning")).start()
    check(until(lambda: len(w.notifications.history) == n0 + 2, 3000), "Toast + Verlauf (auch aus einem Thread)")
    toast = next(t for t in w.notifications.toasts if t.note.text == "Test-Hinweis")
    check(toast.isVisible() and toast.action_button is not None, "Toast sichtbar mit Aktion")
    toast.action_button.click()
    check(acted == [1], "Toast-Aktion ausgeführt")
    check(w.notifications.unread() >= 2 and w.bell.text() != "", "Glocke zeigt ungelesene")
    w.show_notifications()
    check(w.notifications.unread() == 0, "Verlauf geöffnet → gelesen")

    # ---- Absturz-Dialog
    from fm1companion.ui.crash import CrashHandler
    prev = sys.excepthook
    ch = CrashHandler(lambda: w).install()
    try:
        raise RuntimeError("absichtlicher Testfehler")
    except RuntimeError:
        sys.excepthook(*sys.exc_info())
    check(until(lambda: ch.dialog is not None and ch.dialog.isVisible(), 3000), "Absturz-Dialog erscheint")
    threading.Thread(target=lambda: 1 / 0).start()
    check(until(lambda: "ZeroDivisionError" in ch.dialog.exc_text, 3000), "Fehler aus Thread landet im selben Dialog")
    zp = home / "bericht.zip"
    ch.dialog.save(str(zp))
    names = zipfile.ZipFile(zp).namelist() if zp.exists() else []
    check({"info.json", "fehler.txt", "einstellungen.json", "seiten-ladefehler.txt", "abhaengigkeiten.json"} <= set(names),
          f"Fehlerbericht-ZIP ({len(names)} Dateien)")
    cfg = zipfile.ZipFile(zp).read("einstellungen.json").decode()
    check(str(Path.home()) not in cfg and "absichtlicher Testfehler" in zipfile.ZipFile(zp).read("fehler.txt").decode(),
          "Bericht ohne Home-Pfad, mit Fehlertext")
    ch.dialog.accept()
    ch.uninstall()
    sys.excepthook = prev

    # ---- Statusleiste (PiMonitor ersetzt)
    class FakeMon:
        def quick(self, folder):
            return {"cpu_percent": 12.0, "mem_percent": 41.5, "temp_c": 52.3, "disk_free": 23 * 2 ** 30,
                    "disk_total": 64 * 2 ** 30, "undervoltage": True, "load": (0.5, 0.4, 0.3), "mem_total": 8 * 2 ** 30}

        def throttled(self):
            return 0x50005, ["Unterspannung jetzt"]
    w.pi_status.monitor = FakeMon()
    w.pi_status._thr_at = 0
    w.pi_status.refresh()
    txt = w.pi_status.button.text()
    check("CPU 12 %" in txt and "RAM 42 %" in txt and "52 °C" in txt and "23,0 GiB frei" in txt, f"Statusleiste: {txt}")
    check(until(lambda: w.pi_status.thr_bits == 0x50005, 3000) and w.pi_status.warning, "Warnung bei Unterspannung")
    check("Unterspannung" in w.pi_status.button.toolTip(), "Tooltip mit Details")

    # ---- Systemcheck mit Beispielprotokollen
    from fm1companion import syscheck
    fx = ROOT / "tests/fixtures/syscheck"

    def runner(folder, paths):
        f = syscheck.analyze_log((fx / "pi5_fm1_reconnect_loop.txt").read_text(), paths)
        f += syscheck.throttled_findings(0x50000)
        return f, {"source": "dmesg"}
    from fm1companion.ui.statusbar import SystemCheckDialog
    d = SystemCheckDialog(ctx, w, runner=runner)
    check(until(lambda: d.tree.topLevelItemCount() > 0, 5000), "Systemcheck-Dialog füllt sich")
    titles = [d.tree.topLevelItem(i).text(0) for i in range(d.tree.topLevelItemCount())]
    check(any("trennt sich immer wieder" in t for t in titles) and any("Unterspannung" in t for t in titles),
          f"Befunde: Trenn-Schleife und Unterspannung ({len(titles)})")
    check("Problem" in d.summary.text(), "Zusammenfassung nennt Probleme")
    rp = d.save(str(home / "syscheck.txt"))
    check(rp and "FEHL" in Path(rp).read_text(), "Bericht gespeichert")
    d.copy()
    check("Systemcheck" in QApplication.clipboard().text(), "Bericht in die Zwischenablage")
    d.close()

    # ---- Hilfe-Center
    w.go("help")
    hp = w.page("help")
    check(hp.list.count() >= 10, f"{hp.list.count()} Hilfe-Themen")
    check("FM-1 anschließen" in hp.browser.toPlainText(), "Erste Schritte gerendert")
    for i in range(hp.list.count()):
        hp.list.setCurrentRow(i)
        if len(hp.browser.toPlainText()) < 200:
            check(False, f"Hilfe-Thema {hp.list.item(i).text()} leer")
    hp.link(QUrl("app://page/backups"))
    check(w.stack.currentWidget() is w.page("backups"), "Link app://page/backups navigiert")
    w.go("help", topic="03-update-modus")
    check("OCT" in hp.browser.toPlainText() and "drei kurze" in hp.browser.toPlainText().lower(), "Thema per go(topic=…)")
    hp.link(QUrl("help:04-backups"))
    check(hp.list.currentItem().text() == "Backups", "Link help:… öffnet Thema")
    n = len(w.notifications.history)
    hp.link(QUrl("app://page/gibtsnicht"))
    check(len(w.notifications.history) == n + 1, "fehlende Seite → freundlicher Hinweis")
    hp.search.setText("Unterspannung")
    vis = [hp.list.item(i).text() for i in range(hp.list.count()) if not hp.list.item(i).isHidden()]
    check(vis and all("(" in v for v in vis), f"Suche filtert Themen: {vis}")
    hp.search.setText("")

    # ---- Diagnose mit kaputter Seite (Kopie des Pakets, eigener Prozess)
    tmp = Path(tempfile.mkdtemp(prefix="fm1c-broken-"))
    shutil.copytree(ROOT / "fm1companion", tmp / "fm1companion", ignore=shutil.ignore_patterns("__pycache__"))
    (tmp / "fm1companion/ui/pages/zz_kaputt.py").write_text("raise ImportError('absichtlich kaputt')\n")
    script = ("import sys\nfrom PyQt6.QtWidgets import QApplication\na = QApplication([])\n"
              "from fm1companion.ui.pages import discover, load_errors\ndiscover()\n"
              "from fm1companion.ui.pages.help import DiagnoseView\n"
              "class W:\n    open_syscheck = save_report = lambda s: None\n"
              "print(DiagnoseView(W()).html())\n")
    r = subprocess.run([sys.executable, "-c", script], cwd=tmp, capture_output=True, text=True, timeout=120,
                       env=dict(os.environ, PYTHONPATH=str(tmp), QT_QPA_PLATFORM="offscreen"))
    check("zz_kaputt" in r.stdout and "absichtlich kaputt" in r.stdout, f"Diagnose zeigt Ladefehler {r.stderr[-300:]}")
    shutil.rmtree(tmp, ignore_errors=True)
    w.open_help("diagnose")
    check(hp.tabs.currentWidget() is hp.diag and "Versionen" in hp.diag.info.toPlainText(), "Diagnose-Tab im Fenster")

    # ---- Einrichtungs-Assistent
    from fm1companion.ui.wizard import FirstRunWizard, WhatsNewDialog
    wz = FirstRunWizard(ctx, w)
    wz.show()
    newroot = home / "NeuerOrdner"
    wz.p_welcome.theme.setCurrentIndex(wz.p_welcome.theme.findData("dark"))
    steps = 0
    while wz.currentPage() is not wz.p_done and steps < 10:
        if wz.currentPage() is wz.p_folder:
            wz.p_folder.edit.setText(str(newroot))
        if wz.currentPage() is wz.p_samples:
            wz.p_samples.list.addItem(str(home / "po"))
        if wz.currentPage() is wz.p_device:
            check(until(lambda: "Gefunden" in wz.p_device.state.text(), 5000), "Assistent erkennt das FM-1 live")
            wz.p_device.name.setText("Studio-FM-1")
        if wz.currentPage() is wz.p_channel:
            wz.p_channel.stable.setChecked(True)
        if wz.currentPage() is wz.p_deps:
            check("<table" in wz.p_deps.text.text(), "Abhängigkeiten-Seite gefüllt")
        wz.next()
        steps += 1
    wz.accept()
    s2 = config.load()
    check(s2.user_root == str(newroot) and s2.channel == "stable" and s2.theme == "dark" and str(home / "po") in s2.sample_roots,
          "Assistent speichert Ordner, Kanal, Design")
    check(ctx.profile.name == "Studio-FM-1" and theme.prefs()["wizard_done"], "Gerät benannt, Assistent erledigt")
    wn = WhatsNewDialog(w)
    check(wn.cards["Hilfe-Center"] is not None and wn.cards["Befehlssuche"] is not None, "Was-ist-neu: vorhandene Bereiche")
    check(all(v is None for k, v in wn.cards.items() if k in ("Simulator",)) or w.page("simulator") is not None,
          "Was-ist-neu: fehlende Seiten toleriert")
    wn.cards["Hilfe-Center"].click()
    check(w.stack.currentWidget() is w.page("help"), "Karte öffnet die Seite")

    # ---- Ansicht, Über, Tastenkürzel
    w.toggle_sidebar(False)
    check(not w.sidebar.isVisible(), "Seitenleiste aus")
    w.toggle_sidebar(True)
    f0 = app.font().pointSizeF()
    w.zoom(+0.1)
    check(app.font().pointSizeF() > f0, "Zoom vergrößert die Schrift")
    w.zoom(0)
    from fm1companion.ui.about import AboutDialog, collect_shortcuts
    ab = AboutDialog(w)
    txt = ab.credits.toPlainText()
    check(all(x in txt for x in ("Melodee", "Felucca", "Lucide", "SLOOP", "awesome-fm-1", "FM1 Open")), "Über: Lizenzliste")
    sc = collect_shortcuts(w)
    check(any(k == "Ctrl+K" or "K" in k for _a, t, k in sc if "Befehlssuche" in t) and len(sc) > 10,
          f"{len(sc)} Tastenkürzel gesammelt")
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
