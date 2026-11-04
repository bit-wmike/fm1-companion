# SPDX-License-Identifier: GPL-3.0-only
"""2.3.1 in der Oberfläche (offscreen, Demo-Modus):

* einmaliger Risiko-Hinweis „Ich habe verstanden“ vor der ersten Installation/Rettung (gespeichert, danach nie
  wieder; ohne Zustimmung beginnt nichts),
* neue Fenster aus Webseiten (createWindow): nur http/https/mailto gehen an den Standard-Browser,
* Datenschutz: mit abgeschalteten automatischen Abfragen baut die App beim Start keine Verbindung ins Internet auf.

  QT_QPA_PLATFORM=offscreen python3 tests/ui_release_test.py
"""
from __future__ import annotations

import os
import socket
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("FM1C_LANG", "de")      # these checks expect the German user interface
home = Path(tempfile.mkdtemp(prefix="fm1c-release-"))
for k, sub in (("XDG_CONFIG_HOME", ".config"), ("XDG_DATA_HOME", ".local/share"), ("XDG_CACHE_HOME", ".cache")):
    os.environ[k] = str(home / sub)
os.environ["FM1C_DEMO"] = "1"
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu")

errors: list[str] = []
sys.excepthook = lambda t, v, tb: errors.append("".join(traceback.format_exception(t, v, tb)))

# ---- jede Verbindung aus Python mitschreiben (vor allen App-Importen)
connections: list[str] = []
_orig_connect = socket.socket.connect


def _connect(self, addr):
    host = addr[0] if isinstance(addr, tuple) else str(addr)
    if host not in ("127.0.0.1", "::1", "localhost") and not str(host).startswith("/"):
        connections.append(f"{host}:{addr[1] if isinstance(addr, tuple) and len(addr) > 1 else ''}")
        raise OSError("Test: kein Netz")
    return _orig_connect(self, addr)


socket.socket.connect = _connect
_orig_gai = socket.getaddrinfo


def _gai(host, *a, **k):
    if host not in (None, "127.0.0.1", "::1", "localhost"):
        connections.append(f"DNS {host}")
        raise socket.gaierror("Test: kein Netz")
    return _orig_gai(host, *a, **k)


socket.getaddrinfo = _gai

import PyQt6.QtWebEngineWidgets  # noqa: E402,F401
from PyQt6.QtCore import QCoreApplication, QEventLoop, Qt, QTimer, QUrl  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
app = QApplication(sys.argv)

from fm1companion import config, firmware, risk  # noqa: E402

s = config.load()
s.user_root = str(home / "FM-1")
s.check_on_start = False                       # automatische Abfrage 1 aus
config.module("discover", {}).update({"auto_refresh": False, "thumbnails": False})   # automatische Abfrage 2 aus
config.save(s)

boxes: list[str] = []
QMessageBox.exec = lambda self: boxes.append(self.text()) or 0
QMessageBox.clickedButton = lambda self: None

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
    check(until(lambda: ctx.connected, 15000), "Demo-FM-1 verbunden")
    wait(4000)                                 # Zeit für alles, was die App beim Start „von selbst“ tut
    check(connections == [], f"Start ohne automatische Abfragen: keine Verbindung ins Internet ({connections[:5]})")

    # ---- Risiko-Hinweis
    from fm1companion.ui import firmware_page, risk_dialog
    from fm1companion.ui.pages import recovery as recovery_mod
    check(not risk.acknowledged() and config.load().risk_ack == 0, "frisch: Hinweis noch nicht bestätigt")
    dlg = risk_dialog.RiskDialog(w)
    check(not dlg.ok.isEnabled() and dlg.check.text() == "Ich habe verstanden", "Weiter erst nach dem Häkchen")
    dlg.check.setChecked(True)
    check(dlg.ok.isEnabled(), "Häkchen gesetzt: Weiter aktiv")
    from PyQt6.QtWidgets import QLabel
    body = " ".join(lab.text() for lab in dlg.findChildren(QLabel))
    check("M-VAVE" in body and "eigenes Risiko" in body and "Backup" in body and "15 und 16" in body,
          "Text: inoffiziell, Risiko, Backup, keine Gewährleistung (GPL §15/16)")
    dlg.deleteLater()

    shown = []
    orig_exec = QDialog.exec

    def reject_risk(self):
        if isinstance(self, risk_dialog.RiskDialog):
            shown.append("risk")
            return QDialog.DialogCode.Rejected
        if isinstance(self, firmware_page.PlanDialog):
            shown.append("plan")
            return QDialog.DialogCode.Rejected
        return orig_exec(self)
    QDialog.exec = reject_risk
    try:
        spec = firmware.spec("melodee")
        rel = firmware.Release(tag="v1.6.0", prerelease=False)
        r = firmware_page.install_flow(w, ctx, spec, rel)
        check(r is False and shown == ["risk"], f"abgelehnt: keine Installation, kein Plan ({shown})")
        check(not risk.acknowledged(), "Ablehnung wird nicht gespeichert")
        rec = w.page("recovery")
        if rec is not None and hasattr(rec, "uboot_restore"):
            confirms = []
            orig_confirm = recovery_mod.confirm
            recovery_mod.confirm = lambda *a, **k: confirms.append(a[1]) or False
            try:
                rec.chk_sim.setChecked(True)
                shown.clear()
                check(rec.uboot_restore() is False and shown == ["risk"] and confirms == [],
                      f"Rettung Stufe 2: erst der Hinweis, ohne Zustimmung keine weitere Frage ({shown}, {confirms})")
            finally:
                recovery_mod.confirm = orig_confirm
        else:
            check(False, "Rettungsseite gefunden")

        # zustimmen: wird gespeichert, danach kommt der Hinweis nie wieder
        def accept_risk(self):
            if isinstance(self, risk_dialog.RiskDialog):
                shown.append("risk")
                self.check.setChecked(True)
                return QDialog.DialogCode.Accepted
            if isinstance(self, firmware_page.PlanDialog):
                shown.append("plan")
                return QDialog.DialogCode.Rejected     # Plan abbrechen: es wird nichts installiert
            return orig_exec(self)
        QDialog.exec = accept_risk
        shown.clear()
        r = firmware_page.install_flow(w, ctx, spec, rel)
        check(shown == ["risk", "plan"] and r is False, f"zugestimmt: danach der normale Plan-Dialog ({shown})")
        check(risk.acknowledged() and config.load().risk_ack == risk.RISK_VERSION, "Zustimmung in config.json gespeichert")
        shown.clear()
        firmware_page.install_flow(w, ctx, spec, rel)
        check(shown == ["plan"], f"zweites Mal: kein Hinweis mehr, Bestätigungen bleiben ({shown})")
    finally:
        QDialog.exec = orig_exec

    # ---- N3: neue Fenster aus Webseiten
    from fm1companion.ui import web
    opened = []
    orig_open = web.QDesktopServices.openUrl
    web.QDesktopServices.openUrl = staticmethod(lambda u: opened.append(u.toString()) or True)
    try:
        page = web.RoutingPage(w)
        for u in ("file:///etc/passwd", "javascript:alert(1)", "smb://server/share", "https://example.org/x",
                  "mailto:a@example.org", "http://example.org/"):
            tmp = page.createWindow(None)
            tmp.urlChanged.emit(QUrl(u))
        app.processEvents()
        check(opened == ["https://example.org/x", "mailto:a@example.org", "http://example.org/"],
              f"createWindow: nur http/https/mailto im Browser ({opened})")
    finally:
        web.QDesktopServices.openUrl = orig_open
    check(connections == [], f"auch danach keine Verbindung ins Internet ({connections[:5]})")
    app.quit()


def run():
    try:
        flow()
    except Exception:  # noqa: BLE001
        errors.append(traceback.format_exc())
        app.quit()


QTimer.singleShot(500, run)
QTimer.singleShot(180000, app.quit)
app.exec()
fails = [r for r in results if not r[0]]
for e in errors:
    print("AUSNAHME:\n" + e)
print(f"{len(results) - len(fails)}/{len(results)} bestanden, {len(errors)} Ausnahmen")
sys.exit(1 if fails or errors else 0)
