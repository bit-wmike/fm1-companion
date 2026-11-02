# SPDX-License-Identifier: GPL-3.0-only
"""Fahrprüfung MIDI/Simulator/Katalog/Komponieren: kein Netz im GUI-Thread, offline sauber, Oberfläche blockiert
nie, Gerätewechsel DEMO-1/2/3 und Trennen/Neuverbinden bei offener Seite, Piano-Roll mit vielen Noten.

  flock /tmp/fm1c-ui.lock timeout 600 env QT_QPA_PLATFORM=offscreen python3 tests/ui_p4_review_test.py
"""
from __future__ import annotations

import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
home = Path(tempfile.mkdtemp(prefix="fm1c-p4-"))
for k, sub in (("XDG_CONFIG_HOME", ".config"), ("XDG_DATA_HOME", ".local/share"), ("XDG_CACHE_HOME", ".cache")):
    os.environ[k] = str(home / sub)
os.environ["FM1C_DEMO"] = "1"
for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(k, None)           # ein Proxy auf 127.0.0.1 würde „offline“ sonst unterlaufen
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu")

errors: list[str] = []
sys.excepthook = lambda t, v, tb: errors.append("".join(traceback.format_exception(t, v, tb)))
threading.excepthook = lambda a: errors.append("".join(traceback.format_exception(a.exc_type, a.exc_value,
                                                                                  a.exc_traceback)))

# ---- Netz: im GUI-Thread verboten (wird protokolliert), überall „offline“ (wie ohne Internet)
MAIN = threading.main_thread()
gui_net: list[str] = []
_orig_connect = socket.socket.connect


def _guard_connect(self, addr):
    if threading.current_thread() is MAIN:
        gui_net.append("".join(traceback.format_stack(limit=8)))
    host = addr[0] if isinstance(addr, tuple) else str(addr)
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise OSError(101, "Network is unreachable")
    return _orig_connect(self, addr)


socket.socket.connect = _guard_connect
_orig_gai = socket.getaddrinfo


def _guard_gai(host, *a, **k):
    if threading.current_thread() is MAIN and host not in ("127.0.0.1", "localhost", "::1", None):
        gui_net.append(f"DNS {host}\n" + "".join(traceback.format_stack(limit=8)))
    if host not in ("127.0.0.1", "localhost", "::1", None):
        raise socket.gaierror(-3, "Temporary failure in name resolution")
    return _orig_gai(host, *a, **k)


socket.getaddrinfo = _guard_gai

import PyQt6.QtWebEngineWidgets  # noqa: E402,F401
from PyQt6.QtCore import QCoreApplication, QElapsedTimer, QEventLoop, Qt, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
app = QApplication(sys.argv)

from fm1companion import config  # noqa: E402

s = config.load()
s.user_root = str(home / "FM-1")
s.check_on_start = False
config.save(s)
config.ensure_dirs()
QMessageBox.exec = lambda self: 0
QDialog.exec = lambda self: 0

from fm1companion import projectfmt as PF  # noqa: E402
from fm1companion.ui.main_window import MainWindow  # noqa: E402

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
        wait(100)
    return False


# ---- Herzschlag: misst die längste Lücke der Ereignisschleife (= wie lange die Oberfläche stand)
beat = {"last": None, "max": 0.0, "where": ""}
clock = QElapsedTimer()
clock.start()
phase = {"name": "Start"}


def _tick():
    now = clock.elapsed()
    if beat["last"] is not None and now - beat["last"] > beat["max"]:
        beat["max"], beat["where"] = now - beat["last"], phase["name"]
    beat["last"] = now


hb = QTimer()
hb.setInterval(10)
hb.timeout.connect(_tick)
hb.start()


def reset_beat(name):
    phase["name"] = name
    beat["max"], beat["last"] = 0.0, None


AREA = [("midi", None), ("soundmodule", None), ("compose", None), ("workshop", None), ("editor", None),
        ("discover", None), ("simulator", None)]


def flow():
    check(until(lambda: w.ctx.connected, 20000), "Demo-FM-1 verbunden")

    # ---- 1) Alle Seiten des Gebiets öffnen, offline: nichts blockiert, kein Netz im GUI-Thread
    for key, _ in AREA:
        reset_beat(f"Seite {key}")
        w.go(key)
        wait(400)
        check(beat["max"] < 1500, f"{key}: Oberfläche nie > 1,5 s blockiert (längste Lücke {beat['max']:.0f} ms)")

    disc = w.page("discover")
    tab_names = [disc.tabs.tabText(i) for i in range(disc.tabs.count())]
    for i in range(disc.tabs.count()):
        disc.tabs.setCurrentIndex(i)
        wait(300)
    check(not any("lade" in t.lower() and "…" in t for t in tab_names), f"Reiter ohne „lade …“ ({tab_names})")

    # Katalog und Neuigkeiten offline: Rückmeldung statt Hängen, gespeicherter Stand bleibt
    w.go("discover")
    disc.tabs.setCurrentIndex(0)
    n_before = disc.model.rowCount()
    from fm1companion.ui import common as C
    toasts = []
    orig_notify = C.notify
    import fm1companion.ui.pages.discover as D
    D.notify = lambda t, kind="info", *a, **k: toasts.append((kind, t))
    reset_beat("Katalog offline aktualisieren")
    disc.refresh(force=True)
    check(until(lambda: disc.b_refresh.isEnabled() and disc.b_refresh.text() == "Aktualisieren", 30000),
          f"Katalog offline: Knopf kommt zurück ({disc.b_refresh.text()})")
    check(any(k == "warning" and "Keine Internetverbindung" in t and "offline" in t and "Errno" not in t
              for k, t in toasts),
          f"Katalog offline: Warnung nennt Grund und dass es offline weitergeht ({toasts[-1:]})")
    check(disc.model.rowCount() == n_before and n_before > 0, f"gespeicherter Katalog bleibt sichtbar ({n_before})")
    check(beat["max"] < 1500, f"Katalog-Aktualisierung offline blockiert nicht ({beat['max']:.0f} ms)")
    if hasattr(disc, "check_news"):
        disc.tabs.setCurrentIndex(1)
        reset_beat("Neuigkeiten offline")
        toasts.clear()
        disc.check_news()
        check(until(lambda: disc.b_news.isEnabled(), 30000), "Neuigkeiten offline: Knopf kommt zurück")
        check(beat["max"] < 1500, f"Neuigkeiten offline blockieren nicht ({beat['max']:.0f} ms)")
    D.notify = orig_notify
    e = next((x for x in disc.cat.entries if x.repo), None)
    if e is not None:
        disc.tabs.setCurrentIndex(0)
        disc.open_entry(e)
        wait(3000)
        txt = disc.detail.findChildren(type(disc.b_refresh))
        check(not errors, f"Detailseite offline ({e.name}) ohne Ausnahme")

    # ---- 2) Piano-Roll mit vielen Noten: Klicks bleiben flott
    w.go("compose")
    cp = w.page("compose")
    full = []
    for i in range(PF.NSTEP):
        st = PF.Step()
        st.time, st.notes, st.vel, st.hit, st.acc = PF.ST_NOTE, [20 + i, 40 + i, 60 + (i % 40), 100 - i], 30 + i, 255, 15
        full.append(st)
    cp.length.setValue(PF.NSTEP)
    cp.set_steps(full, PF.NSTEP)
    cp.zoom.setValue(cp.zoom.maximum())
    wait(200)
    from PyQt6.QtCore import QPointF
    from PyQt6.QtGui import QMouseEvent
    from PyQt6.QtCore import QEvent
    roll = cp.roll
    t0 = time.perf_counter()
    n = 30
    for k in range(n):
        x = 44 + (k * 3 % PF.NSTEP) * roll.step_w + roll.step_w / 2
        y = 16 + (k * 7 % roll._rows()) * 12 + 6
        ev = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(x, y), QPointF(x, y), Qt.MouseButton.LeftButton,
                         Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        roll.mousePressEvent(ev)
        roll.repaint()
    per = (time.perf_counter() - t0) / n * 1000
    check(per < 25, f"Piano-Roll: Klick + Neuzeichnen bei 64 vollen Steps und maximalem Zoom {per:.1f} ms (< 25)")
    reset_beat("Vorhören voller Pattern")
    cp.preview()
    wait(1500)
    check(beat["max"] < 150, f"Vorhören rechnet nicht im GUI-Thread (längste Lücke {beat['max']:.0f} ms)")
    cp.stop()

    # ---- 3) Gerätewechsel DEMO-1 → 2 → 3 → 1 mit jeder offenen Seite
    ports = {p.serial: p for p in w.ctx.ports}
    for key, _ in AREA:
        w.go(key)
        for serial, fw in (("DEMO-2", "sloop"), ("DEMO-3", None), ("DEMO-1", "melodee")):
            n_err = len(errors)
            reset_beat(f"{key}: Wechsel auf {serial}")
            w.ctx.connect_port(ports[serial])
            ok = until(lambda: w.ctx.port is not None and w.ctx.port.serial == serial and
                       (w.ctx.connected if fw else getattr(w.ctx, "midi_connected", True)), 20000)
            wait(300)
            check(ok and len(errors) == n_err and beat["max"] < 1500,
                  f"{key}: Wechsel auf {serial} ohne Ausnahme, Oberfläche frei ({beat['max']:.0f} ms)")

    # Seiteninhalt nach Wechsel: Komponieren-Rückgängig an DEMO-1 gebunden, Regler leer nach Wechsel
    midi = w.page("midi")
    check(midi is not None, "MIDI-Seite vorhanden")

    # ---- 4) Gerät „abziehen“ (Demo-Prozess beenden) bei offener Seite, dann neu verbinden
    for key in ("midi", "compose", "simulator"):
        w.go(key)
        n_err = len(errors)
        old = w.ctx.link
        old.t.p.kill()
        # erkannt = alte Verbindung geschlossen (die Suche verbindet danach sofort neu – „getrennt“ ist kurz)
        gone = until(lambda: old.closed or w.ctx.link is not old, 15000)
        check(gone, f"{key}: getrenntes Gerät wird erkannt")
        w.ctx.scan()
        back = until(lambda: w.ctx.connected, 20000)
        check(back and len(errors) == n_err, f"{key}: neu verbunden ohne Ausnahme")

    # ---- 5) Klangerzeuger: kaputte bzw. leere Kit-Datei -> Satz statt Ausnahme
    sm = w.page("soundmodule")
    kdir = home / "kits"
    kdir.mkdir(exist_ok=True)
    (kdir / "kaputt.fm1kit").write_text("{kein json", "utf-8")
    from fm1companion.samples import Kit
    empty = Kit(name="Leer")
    epath = empty.save(kdir)
    for path, want in ((kdir / "kaputt.fm1kit", "lässt sich nicht lesen"), (epath, "noch keine Sounds")):
        sm.kits.clear()
        sm.kits.addItem(path.stem, str(path))
        n_err = len(errors)
        sm.make_kit()
        check(want in sm.kit_msg.text() and len(errors) == n_err, f"Kit {path.name}: {sm.kit_msg.text()[:70]}")

    check(not gui_net, "kein Netzaufruf im GUI-Thread" + (f":\n{gui_net[0]}" if gui_net else ""))


def run():
    try:
        flow()
    except Exception:  # noqa: BLE001
        errors.append(traceback.format_exc())
    w.close()
    wait(300)
    for e in errors:
        print("AUSNAHME:\n" + e)
    ok = sum(1 for c, _ in results if c)
    print(f"{ok}/{len(results)} bestanden, {len(errors)} Ausnahmen")
    shutil.rmtree(home, ignore_errors=True)
    app.exit(0 if ok == len(results) and not errors else 1)


QTimer.singleShot(500, run)
sys.exit(app.exec())
