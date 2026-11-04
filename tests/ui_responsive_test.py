# SPDX-License-Identifier: GPL-3.0-only
"""Responsive Oberfläche (offscreen, Demo-Modus): die App auf der linken Hälfte eines 1080p-Bildschirms.

Prüft bei 950×1000 jede Seite und jeden Reiter (auch innere Reiter): Mindestbreite passt in den Seitenbereich,
kein waagrechtes Rollen (außer Simulator), keine rechts abgeschnittenen Knöpfe/Texte. Dazu: Seitenleiste klappt
automatisch auf Symbole und respektiert den manuellen Schalter, Hysterese ohne Flacker-Schleife, breit ↔ schmal ↔
breit ohne Ausnahme, Hinweise (Toasts) verdecken bei kleinem Fenster keine Knöpfe, Komponieren bei 1280×720.

  QT_QPA_PLATFORM=offscreen python3 tests/ui_responsive_test.py
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
home = Path(tempfile.mkdtemp(prefix="fm1c-resp-"))
for k, sub in (("XDG_CONFIG_HOME", ".config"), ("XDG_DATA_HOME", ".local/share"), ("XDG_CACHE_HOME", ".cache"),
               ("XDG_STATE_HOME", ".local/state")):
    os.environ[k] = str(home / sub)
os.environ["FM1C_DEMO"] = "1"
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu")

errors: list[str] = []
sys.excepthook = lambda t, v, tb: errors.append("".join(traceback.format_exception(t, v, tb)))

import PyQt6.QtWebEngineWidgets  # noqa: E402,F401
from PyQt6.QtCore import QCoreApplication, QEventLoop, QRect, Qt, QTimer  # noqa: E402
from PyQt6.QtWidgets import (QAbstractButton, QAbstractScrollArea, QApplication, QDialog, QLabel,  # noqa: E402
                             QMessageBox, QSplitter, QTabWidget, QWidget)

QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
app = QApplication(sys.argv)

from fm1companion import config  # noqa: E402
from fm1companion.ui import responsive, theme  # noqa: E402

theme.apply(app, "light")
s = config.load()
s.user_root = str(home / "FM-1")
s.check_on_start = False
config.save(s)
config.ensure_dirs()

QMessageBox.exec = lambda self: 0
QDialog.exec = lambda self: 1

from fm1companion.ui import main_window as MW  # noqa: E402
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


def size(wd, h):
    w.resize(wd, h)
    wait(250)


def watchers():
    return [o for o in w.findChildren(responsive.WidthWatcher)]


def clipped(page: QWidget) -> list[str]:
    """sichtbare Knöpfe/Texte, die rechts über ihren Eltern-Rand ragen oder schmaler als nötig sind
    (Inhalte von Rollbereichen dürfen rollen und zählen nicht)."""
    bad = []
    for c in page.findChildren(QWidget):
        if not isinstance(c, (QAbstractButton, QLabel)) or not c.isVisible() or c.width() <= 0:
            continue
        p, inside_scroll = c.parentWidget(), False
        r = QRect(c.mapTo(page, c.rect().topLeft()), c.size())
        ok = True
        while p is not None and p is not page:
            if isinstance(p, QAbstractScrollArea) or p.objectName() == "qt_scrollarea_viewport":
                inside_scroll = True
                break
            pr = QRect(p.mapTo(page, p.rect().topLeft()), p.size())
            if r.right() > pr.right() + 1:
                ok = False
            p = p.parentWidget()
        if inside_scroll:
            continue
        if r.right() > page.width() + 1:
            ok = False
        text = c.text() if hasattr(c, "text") else ""
        if isinstance(c, QAbstractButton) and text and c.width() + 2 < c.minimumSizeHint().width():
            ok = False
        if isinstance(c, QLabel) and text and not c.wordWrap() and c.width() + 2 < c.minimumSizeHint().width():
            ok = False
        if not ok:
            bad.append(f"{type(c).__name__}„{(text or c.objectName())[:24]}“")
    return bad


def card_cuts(page: QWidget) -> list[str]:
    """Karten (common.Card), die ein sichtbares Kind unten abschneiden: Kind ragt über den Kartenrand oder die
    Karte ist niedriger, als ihr Inhalt bei dieser Breite braucht (Höhe nach Breite)"""
    from fm1companion.ui.common import Card
    bad = []
    for card in page.findChildren(Card):
        if not card.isVisible() or card.width() <= 0 or card.height() <= 0:
            continue
        name = card.title_label.text()[:28] if hasattr(card, "title_label") else card.objectName()
        lay = card.layout()
        if lay is not None and lay.hasHeightForWidth() and card.height() + 1 < lay.totalHeightForWidth(card.width()):
            bad.append(f"„{name}“ {card.height()} < {lay.totalHeightForWidth(card.width())} px")
            continue
        bottom = card.contentsRect().bottom() + 1
        for c in card.findChildren(QWidget):
            if not c.isVisible() or c.height() <= 0 or QWidget.window(c) is not QWidget.window(card):
                continue
            p, inside = c.parentWidget(), False
            while p is not None and p is not card:
                if isinstance(p, QAbstractScrollArea) or p.objectName() == "qt_scrollarea_viewport":
                    inside = True
                    break
                p = p.parentWidget()
            if inside:
                continue
            r = QRect(c.mapTo(card, c.rect().topLeft()), c.size())
            if r.bottom() > bottom:
                txt = c.text() if hasattr(c, "text") and callable(c.text) else type(c).__name__
                bad.append(f"„{name}“ schneidet {str(txt)[:20]!r} ab ({r.bottom()} > {bottom})")
                break
    return bad


def tab_states(page: QWidget):
    """alle (inneren) Reiter einer Seite nacheinander zeigen: liefert Beschriftungen wie „Monitor“, „Inhalt“."""
    tws = [t for t in page.findChildren(QTabWidget) if t.isVisible()]
    if not tws:
        yield ""
        return
    top = tws[0]
    start = top.currentIndex()
    for i in range(top.count()):
        top.setCurrentIndex(i)
        wait(120)
        inner = [t for t in top.widget(i).findChildren(QTabWidget) if t.isVisible()]
        if inner:
            j0 = inner[0].currentIndex()
            for j in range(inner[0].count()):
                inner[0].setCurrentIndex(j)
                wait(80)
                yield f"{top.tabText(i)} › {inner[0].tabText(j)}".replace("&&", "&")
            inner[0].setCurrentIndex(j0)
        else:
            yield top.tabText(i).replace("&&", "&")
    top.setCurrentIndex(start)


def flow():
    ctx = w.ctx
    check(until(lambda: ctx.connected), "Demo-Gerät verbunden")
    # ---- halber 1080p-Bildschirm
    size(950, 1000)
    check(w.nav.compact and w.sidebar.width() == MW.SIDEBAR_COMPACT,
          f"Seitenleiste bei 950 px automatisch schmal ({w.sidebar.width()} px)")
    minw = max(w.minimumSize().width(), w.minimumSizeHint().width())
    check(minw <= 760, f"Fenster-Mindestbreite ≤ 760 ({minw})")
    sc = w.page_scroll
    table = []
    worst = []
    for key in list(w.specs):
        w.go(key)
        wait(300 if key not in ("editor", "discover") else 900)
        page = w.stack.currentWidget()
        for tab in tab_states(page):
            wait(60)
            label = f"{key}" + (f" [{tab}]" if tab else "")
            mw = page.minimumSizeHint().width()
            avail = sc.viewport().width()
            sim = key == "discover" and tab.startswith("Simulator")
            table.append((label, mw))
            hbar = sc.horizontalScrollBar().maximum() > 0
            if not sim:
                if mw > avail or hbar:
                    worst.append(f"{label}: braucht {mw} > {avail}")
                bad = clipped(page)
                if bad:
                    worst.append(f"{label}: abgeschnitten {', '.join(bad[:4])}")
    check(not worst, "950×1000: jede Seite/jeder Reiter passt ohne waagrechtes Rollen, nichts abgeschnitten"
          + ("" if not worst else " – " + "; ".join(worst[:12])))
    print("Mindestbreiten bei 950×1000 (Seitenbereich %d px):" % sc.viewport().width())
    for label, mw in table:
        print(f"    {label:48s} {mw:5d}")
    check(not errors, "keine Ausnahme beim Durchgehen aller Seiten" + (f": {errors[-1][-400:]}" if errors else ""))

    # ---- keine Karte schneidet Kinder ab (Card: Höhe nach Breite) – alle Seiten und Reiter, drei Fenstergrößen
    for wd, h in ((950, 1000), (1280, 720), (1920, 1080)):
        size(wd, h)
        cuts = []
        for key in list(w.specs):
            if key == "discover":
                continue
            w.go(key)
            wait(250 if key != "editor" else 700)
            page = w.stack.currentWidget()
            for tab in tab_states(page):
                wait(60)
                cuts += [f"{key}{' [' + tab + ']' if tab else ''}: {x}" for x in card_cuts(page)]
        check(not cuts, f"{wd}×{h}: keine Karte schneidet Kinder ab" + ("" if not cuts else " – " + "; ".join(cuts[:8])))
    size(950, 1000)

    # ---- zusammengelegte Seiten über alte Schlüssel
    for key in ALIASES:
        if key == "simulator":
            continue
        w.go(key)
        wait(200)
        page = w.stack.currentWidget()
        mw = page.minimumSizeHint().width()
        check(mw <= sc.viewport().width() and sc.horizontalScrollBar().maximum() == 0,
              f"{key}: passt bei 950 px ({mw} ≤ {sc.viewport().width()})")

    # ---- Simulator darf breiter sein, zwingt das Fenster aber nicht
    w.go("simulator")
    wait(600)
    size(700, 900)
    minw = max(w.minimumSize().width(), w.minimumSizeHint().width())
    check(w.width() == 700 and minw <= 760, f"Simulator: Fenster lässt sich schmal ziehen ({w.width()} px, min {minw})")
    size(950, 1000)

    # ---- Samples: drei Spalten breit, zwei schmal
    w.go("samples")
    wait(300)
    sp = w.page("samples")
    split = sp._lib_split
    check(sp._lib_inner is not None and sp._lib_inner.isVisible() and split.count() >= 2,
          "Samples bei 950 px: Filter über der Liste (zwei Spalten)")
    size(1700, 1000)
    wait(200)
    check(sp._lib_inner is None or not sp._lib_inner.isVisible(), "Samples bei 1700 px: drei Spalten")

    # ---- breit ↔ schmal ↔ breit, mit Hysterese
    n0 = len(errors)
    for wd in (1920, 950, 1280, 760, 1920, 950, 1600, 640, 1920):
        for key in ("compose", "backup_browser", "firmware", "samples", "projects"):
            w.go(key)
            size(wd, 1000 if wd != 1280 else 720)
    check(len(errors) == n0, "breit ↔ schmal ↔ breit auf fünf Seiten ohne Ausnahme")
    wait(300)
    before = [len(x.switches) for x in watchers()]
    wait(800)
    after = [len(x.switches) for x in watchers()]
    check(before == after and not any(x.frozen for x in watchers()),
          f"nach dem Ziehen ist Ruhe: keine Flacker-Schleife ({len(watchers())} Breiten-Wächter)")
    # Grenze: Hysterese der Seitenleiste
    size(1100, 900)
    c1 = w.nav.compact
    size(1160, 900)
    c2 = w.nav.compact
    size(1200, 900)
    c3 = w.nav.compact
    size(1160, 900)
    c4 = w.nav.compact
    check(c1 and c2 and not c3 and not c4,
          f"Seitenleiste mit Hysterese: 1100 schmal, 1160 bleibt schmal, 1200 breit, 1160 bleibt breit "
          f"({c1}, {c2}, {c3}, {c4})")
    # Splitter-Hysterese: knapp um die Grenze hin und her – höchstens ein Wechsel
    w.set_sidebar_mode("icons")                  # feste Leistenbreite: nur der Splitter soll umschalten
    w.go("backup_browser")
    size(1600, 1000)
    bb = w.page("backup_browser")
    spl = next(x for x in bb.findChildren(QSplitter) if hasattr(x, "width_watcher"))
    ww = spl.width_watcher
    bp = ww.breakpoint
    base = w.width() - responsive.available_width(spl)
    n_sw = len(ww.switches)
    for d in (-4, +4, -4, +4, -4, +4):
        size(bp + base + d, 1000)
    check(len(ww.switches) - n_sw <= 1, f"Splitter-Grenze ±4 px: höchstens ein Wechsel ({len(ww.switches) - n_sw})")
    size(bp + base - 60, 1000)
    check(spl.orientation() == Qt.Orientation.Vertical, "Backup-Browser schmal: Zeitleiste über den Reitern"
          f" (verfügbar {responsive.available_width(spl)}, Grenze {ww.breakpoint}, Fenster {w.width()})")
    size(bp + base + 120, 1000)
    check(spl.orientation() == Qt.Orientation.Horizontal, "Backup-Browser breit: wieder nebeneinander")

    # ---- manueller Schalter hat Vorrang
    w.set_sidebar_mode("auto")
    size(950, 1000)
    w.set_sidebar_mode("wide")
    wait(100)
    check(not w.nav.compact and w.sidebar.width() == MW.SIDEBAR_WIDE, "manuell „breit“: bleibt bei 950 px breit")
    size(900, 1000)
    check(not w.nav.compact, "manuell „breit“: auch nach Größenänderung")
    w.set_sidebar_mode("icons")
    size(1900, 1000)
    check(w.nav.compact, "manuell „nur Symbole“: bleibt bei 1900 px schmal")
    w.set_sidebar_mode("auto")
    wait(100)
    check(not w.nav.compact, "„automatisch“: bei 1900 px wieder breit")
    size(950, 1000)
    check(w.nav.compact, "„automatisch“: bei 950 px schmal")
    w.sidebar_button.click()
    wait(100)
    check(not w.nav.compact and w.sidebar_mode() == "wide", "Knopf unten in der Leiste: breit (manuell)")
    w.sidebar_button.click()
    wait(100)
    check(w.nav.compact and w.sidebar_mode() == "icons", "Knopf noch einmal: nur Symbole (manuell)")
    check(theme.prefs().get("sidebar_mode") == "icons", "Wahl wird gespeichert")
    tips = [w.nav.item(i).toolTip() for i in range(w.nav.count()) if w.nav.item(i).data(Qt.ItemDataRole.UserRole)]
    check(all(t and " – " in t for t in tips[:5]), f"schmale Leiste: Tooltips mit Seitennamen ({tips[0]!r})")
    w.set_sidebar_mode("auto")
    # Werkzeugleiste: „Backup jetzt“ und Glocke bleiben bei 950 px sichtbar
    a_bk = next(a for a in w.toolbar.actions() if a.text() == "Backup jetzt")
    btn = w.toolbar.widgetForAction(a_bk)
    check(btn is not None and btn.isVisible() and w.bell.isVisible(), "950 px: „Backup jetzt“ und Glocke sichtbar")

    # ---- Hinweise bei kleinem Fenster: eigene Zeile, nichts verdeckt
    size(1280, 720)
    w.go("overview")
    wait(300)
    from fm1companion.ui.notify import center
    nc = center()
    nc.post("Testhinweis für die Hinweis-Zeile", "info")
    wait(200)
    t = nc.toasts[-1]
    check(t.parentWidget() is w.toast_lane and w.toast_lane.isVisible(), "1280×720: Hinweis in eigener Zeile")
    tb = t.mapTo(w, t.rect().bottomLeft()).y()
    st = w.page_scroll.mapTo(w, w.page_scroll.rect().topLeft()).y()
    check(tb < st, f"Hinweis liegt über der Seite, verdeckt keine Knöpfe ({tb} < {st})")
    check(t.height() >= t.heightForWidth(t.width()) - 1, "Hinweis-Text nicht abgeschnitten")
    size(1920, 1080)
    wait(200)
    check(nc.toasts and nc.toasts[-1].parentWidget() is w and not w.toast_lane.isVisible(),
          "1920×1080: Hinweis schwebt oben rechts (wie bisher)")
    for x in list(nc.toasts):
        x.dismiss()

    # ---- Komponieren bei 1280×720
    size(1280, 720)
    w.go("compose")
    wait(400)
    cp = w.page("compose")
    check(cp.split.orientation() == Qt.Orientation.Horizontal, "Komponieren 1280×720: Einstellungen neben der Vorschau"
          f" (verfügbar {responsive.available_width(cp.split)}, Grenze {cp.split.width_watcher.breakpoint}, "
          f"Spalten {[cp.split.widget(i).minimumSizeHint().width() for i in range(2)]}, links min {cp.left.minimumWidth()})")
    check(not clipped(w.stack.currentWidget()), "Komponieren 1280×720: nichts abgeschnitten")
    cp.tabs.setCurrentIndex(0)
    wait(300)
    check(cp.split.orientation() == Qt.Orientation.Horizontal and not clipped(w.stack.currentWidget()),
          "Komponieren 1280×720, Reiter Rhythmus: Raster passt neben die Vorschau")
    cp.tabs.setCurrentIndex(1)
    wait(200)
    b = cp.b_write
    r = QRect(b.mapTo(w.stack, b.rect().topLeft()), b.size())
    check(b.isVisible() and w.stack.rect().contains(r), "„In Pattern schreiben“ ganz sichtbar")
    check(w.page_scroll.verticalScrollBar().maximum() == 0, "Komponieren 1280×720: Seite rollt nicht senkrecht")
    h0 = cp.preview_split.sizes()[0]
    cp.b_settings.click()
    wait(150)
    check(not cp.left.isVisible() and not cp.b_settings.isChecked(), "Einstellungen einklappbar")
    cp.b_settings.click()
    wait(150)
    check(cp.left.isVisible(), "Einstellungen wieder ausklappbar")
    cp.preview_split.setSizes([h0 + 60, 100])
    check(cp.preview_split.sizes()[0] >= h0, "Vorschau im Splitter größer ziehbar")
    w.go("workshop")
    wait(300)
    ws = w.page("workshop")
    ws.b_opts.click()
    wait(150)
    check(not ws.opts_box.isVisible() and ws.b_send.isVisible(), "Aus MIDI-Datei: Optionen einklappbar, Senden bleibt")
    ws.b_opts.click()
    wait(200)
    ws._split_sized = False
    ws._size_split()
    wait(100)
    sz = ws.preview_split.sizes()
    check(sz[1] >= 0.55 * sum(sz) - 2 and ws.roll_scroll.viewport().height() >= 160,
          f"Aus MIDI-Datei 1280×720: Vorschau bekommt den größeren Teil ({sz}, Rolle {ws.roll_scroll.viewport().height()} px)")

    # ---- MIDI-Monitor ändert ständig Texte: das darf den Seitenbereich nicht jedes Mal neu rechnen lassen
    w.go("midi")
    wait(300)
    mon = next(x for x in w.page("midi").findChildren(QWidget) if type(x).__name__ == "MonitorTab")
    n0 = w.page_host.syncs
    for i in range(150):
        mon.status.setText(f"{i} Nachrichten" + " ·" * (i % 7))
        app.processEvents()
    wait(100)
    check(w.page_host.syncs - n0 <= 2, f"MIDI-Monitor: 150 Textänderungen, Seitenbereich {w.page_host.syncs - n0}× neu "
                                        "gerechnet (höchstens 2)")

    # ---- Bausteine einzeln: reflow und form_wrap
    from PyQt6.QtWidgets import QFormLayout, QHBoxLayout, QLineEdit, QPushButton, QVBoxLayout
    host = QWidget()
    v = QVBoxLayout(host)
    v.addWidget(QLabel("oben"))
    row = QHBoxLayout()
    btns = [QPushButton(f"Knopf Nummer {i}") for i in range(6)]
    for b in btns:
        row.addWidget(b)
    row.addStretch(1)
    v.addLayout(row)
    v.addWidget(QLabel("unten"))
    cont = responsive.reflow(row)
    host.resize(300, 400)
    host.show()
    wait(100)
    check(v.indexOf(cont) == 1 and all(b.parentWidget() is cont for b in btns),
          "reflow: Reihe an derselben Stelle ersetzt, Knöpfe umgezogen")
    check(host.minimumSizeHint().width() < sum(b.sizeHint().width() for b in btns) // 2
          and len({b.y() for b in btns}) > 1, "reflow: Knöpfe brechen bei 300 px in mehrere Zeilen um")
    fw = QWidget()
    form = QFormLayout(fw)
    form.addRow("Eine lange Beschriftung:", QLineEdit())
    responsive.form_wrap(form, fw, 400)
    fw.resize(300, 200)
    fw.show()
    wait(100)
    narrow_policy = form.rowWrapPolicy()
    fw.resize(700, 200)
    wait(100)
    check(narrow_policy == QFormLayout.RowWrapPolicy.WrapAllRows
          and form.rowWrapPolicy() == QFormLayout.RowWrapPolicy.DontWrapRows,
          "form_wrap: schmal Beschriftung über dem Feld, breit daneben")
    host.close()
    fw.close()
    # Komponieren schmal: Beschreibung wandert unter die Knöpfe statt vierzeilig neben ihnen
    size(950, 1000)
    w.go("compose")
    wait(300)
    cp = w.page("compose")
    check(cp.desc.isVisible() and cp.desc.height() < 3 * cp.desc.fontMetrics().height() + 4,
          f"Komponieren 950 px: Beschreibung höchstens zwei Zeilen ({cp.desc.height()} px)")
    check(not errors, "keine Ausnahmen" + (f": {errors[-1][-400:]}" if errors else ""))


w = MainWindow()
w.resize(1280, 820)
w.show()


def run():
    try:
        flow()
    except Exception:  # noqa: BLE001
        errors.append(traceback.format_exc())
        check(False, "Ablauf ohne Ausnahme:\n" + traceback.format_exc())
    finally:
        app.quit()


QTimer.singleShot(300, run)
app.exec()
try:
    w.shutdown()
except Exception:  # noqa: BLE001
    pass
shutil.rmtree(home, ignore_errors=True)
ok = sum(1 for r in results if r[0])
print(f"{ok}/{len(results)} bestanden")
sys.exit(0 if ok == len(results) and results else 1)
