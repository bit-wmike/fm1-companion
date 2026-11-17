# SPDX-License-Identifier: GPL-3.0-only
"""SLOOP 3 in the app, end to end in the real window (offscreen):

  a device that speaks SLOOP 3's editor protocol (tests/fake_sloop3.py: answers recorded from the author's PC twin of
  3.3.7, plus its files and banks; or the twin itself, see below)
     → the app recognises it as SLOOP 3 – its own firmware key, never SLOOP 2.x – and sends it no Felucca command
     → the bundled web editor (fm1companion/sims/_editors/sloop3) in the Editor page: Web MIDI polyfill → QWebChannel →
       MidiBridge (only SLOOP 3's own protocol may go out) → the device; it connects, reads the device's state, every
       tab opens, and the page reports no JavaScript error
     → a backup on the Backups page ("sloop3-backup"), listed there; the editor carries on afterwards
     → restore of that backup; a Felucca backup is refused without a single command
     → Firmware+: the SLOOP 3 entry, its dialog, the blocker for the newer hardware revision

  QT_QPA_PLATFORM=offscreen python3 tests/ui_sloop3_test.py
  SLOOP3_DEVICE_CMD="python3 /path/twin_bridge.py …" runs the same against another device (fields separated by \\x1f
  or spaces) – e.g. the PC twin built from SLOOP 3's sources (make build/twin; host/twin.c "serve").
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("FM1C_LANG", "de")      # the UI scripts run the German user interface
home = Path(tempfile.mkdtemp(prefix="fm1c-sloop3-"))
for k, sub in (("XDG_CONFIG_HOME", ".config"), ("XDG_DATA_HOME", ".local/share"), ("XDG_CACHE_HOME", ".cache")):
    os.environ[k] = str(home / sub)
os.environ["HOME"] = str(home)
SEEN = home / "seen.log"                      # every message the device received (the fake writes it)
cmd = os.environ.get("SLOOP3_DEVICE_CMD", "")
argv = cmd.split("\x1f") if "\x1f" in cmd else cmd.split() if cmd else \
    [sys.executable, str(ROOT / "tests" / "fake_sloop3.py"), "--log", str(SEEN)]
REAL = bool(cmd)
os.environ["FM1C_DEMO"] = "1"
os.environ["FM1C_DEMO_CMD"] = "\x1f".join(argv)
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu")

devfile = home / ".local/share/fm1-companion/devices.json"
devfile.parent.mkdir(parents=True, exist_ok=True)
# the profile remembers SLOOP 2.8 on this device (identity FM-1_900): SLOOP 3 must replace that, not inherit it
devfile.write_text(json.dumps({"profiles": [{"id": "test1", "name": "Test-FM-1", "serial": "DEMO-1",
                                             "firmware": "sloop", "firmware_version": "v2.8.5",
                                             "identity": "FM-1_900"}]}))

import PyQt6.QtWebEngineWidgets  # noqa: E402,F401
from PyQt6.QtCore import QCoreApplication, QEventLoop, Qt, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog, QInputDialog, QMessageBox  # noqa: E402

QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
app = QApplication(sys.argv)
from fm1companion import backup, config, sloop3 as S3  # noqa: E402
from fm1companion.ui import web  # noqa: E402

s = config.load()
s.user_root = str(home / "FM-1")
s.check_on_start = False
config.save(s)
boxes: list[str] = []


def _box_exec(self):
    boxes.append(self.text())
    acc = [b for b in self.buttons() if self.buttonRole(b) == QMessageBox.ButtonRole.AcceptRole]
    if acc:
        QTimer.singleShot(0, acc[0].click)
        return QDialog.exec(self)
    return 0


QMessageBox.exec = _box_exec
_dialog_exec = QDialog.exec
QInputDialog.getText = staticmethod(lambda *a, **k: ("ui test", True))
console: list[str] = []
web.RoutingPage.javaScriptConsoleMessage = lambda self, level, msg, line, src: console.append(
    f"{getattr(level, 'name', level)}: {msg} ({str(src).rsplit('/', 1)[-1]}:{line})")

from fm1companion.ui.main_window import MainWindow  # noqa: E402
from fm1companion.ui import common as _common  # noqa: E402

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


def js(code, timeout=8000):
    box = {}
    loop = QEventLoop()
    w.editor.page.runJavaScript(code, lambda v: (box.setdefault("v", v), loop.quit()))
    QTimer.singleShot(timeout, loop.quit)
    loop.exec()
    return box.get("v")


def until(cond, ms=20000, step=250):
    t0 = time.time()
    while time.time() - t0 < ms / 1000:
        try:
            if cond():
                return True
        except Exception:
            pass
        wait(step)
    return False


def seen() -> list[bytes]:
    try:
        return [bytes.fromhex(x) for x in SEEN.read_text().split()]
    except OSError:
        return []


def errors() -> list[str]:
    return [c for c in console if c.lower().startswith(("error", "javascriptconsolemessagelevel.error"))
            or "Uncaught" in c]


def main():
    c = w.ctx
    check(until(lambda: c.sloop3 is not None, 40000), "the app recognises SLOOP 3 by its own INFO")
    if c.sloop3 is None:
        return finish()
    check(c.info is None and not c.connected and c.protocol == "generic" and c.midi_connected,
          "no Felucca protocol: the Felucca pages treat it as a firmware without theirs")
    check(c.firmware_key == S3.KEY and c.profile.firmware == "sloop3" and c.profile.identity == "",
          f"its own firmware key, not SLOOP 2.x's ({c.firmware_key}, {c.profile.firmware_version})")
    check(c.sloop3.version in S3.CHECKED_VERSIONS and c.sloop3.proto == 2 and c.sloop3.tracks == 5,
          f"INFO read: {c.sloop3}")
    check("SLOOP 3" in c.title() and "beta" in c.title().lower(), f"title: {c.title()}")
    w.go("overview")
    wait(300)
    ov = w.overview
    check(ov.lbl["Firmware"].text() == "SLOOP 3" and c.sloop3.version in ov.lbl["Version"].text(),
          f"overview: {ov.lbl['Firmware'].text()} / {ov.lbl['Version'].text()} / {ov.lbl['Verbindung'].text()[:60]}")
    if not REAL:
        foreign = [m for m in seen() if not (len(m) > 4 and m[:4] == bytes([0xF0, 0x7D, 0x53, 0x33]))]
        probes = {m.hex() for m in foreign}
        # connecting asks what every unknown device is asked (none of it changes anything, SLOOP 3 answers none):
        # Felucca's INFO, the update handshake, Hortator's INFO – and nothing else, in particular no backup command
        allowed = {"f07d464c01f7", "f07d485401" + "00f7"}
        from fm1companion.vendor import fm1_install as fi
        allowed.add(bytes(fi.HS_QUERY).hex())
        check(probes <= allowed, f"only the harmless probes reached it besides SLOOP 3's own protocol ({sorted(probes - allowed)})")
    # ---------------------------------------------------------------- the editor
    w.go("editor")
    check(until(lambda: js("!!(window.__fm1Companion && navigator.requestMIDIAccess)"), 30000),
          "the bundled editor loads with the Web MIDI polyfill")
    until(lambda: "editor.html" in w.editor.view.url().toString(), 10000)
    url = w.editor.view.url().toString()
    check("/sims/_editors/sloop3/editor.html" in url and url.startswith("http://127.0.0.1:"), f"served locally: {url}")
    check(until(lambda: js("typeof info !== 'undefined' && !!info && info.proto === 2"), 30000),
          "the editor connects through the bridge (protocol 2)")
    ver = js("info && info.ver")
    check(ver == c.sloop3.version and js("document.getElementById('s_ver').textContent") == ver,
          f"the editor shows the device's version ({ver})")
    check(until(lambda: js("NAMES.presets.length > 50 && NAMES.kits.length > 0 && NAMES.user.length === 32"), 15000),
          "the editor read the lists of sounds and kits")
    check(until(lambda: js("!!LV && LV.t.length === 5"), 10000), "the editor read the LIVE state (5 tracks)")
    check(js("document.getElementById('fm1c-compat') === document.head.firstElementChild"),
          "the app's Chromium-102 addition sits before the editor's own style sheet")
    tint = js("""(() => { const sh = [...document.styleSheets].find(x => x.ownerNode.id !== 'fm1c-compat');
      const i = [...sh.cssRules].findIndex(r => r.selectorText === '.pad.has'); const had = i >= 0; if (had) sh.deleteRule(i);
      const d = document.createElement('div'); d.className = 'pad has'; d.style.setProperty('--c', '#ff0000');
      document.body.appendChild(d); const v = getComputedStyle(d).backgroundImage + ' ' + getComputedStyle(d).backgroundColor;
      d.remove(); return (had ? '' : 'NO-UPSTREAM-RULE ') + v; })()""") or ""
    check("linear-gradient" in tint and "255, 0, 0" in tint and "NO-UPSTREAM" not in tint,
          f"without color-mix() a used pattern pad is still tinted ({tint[:90]})")
    tabs = js("[...document.querySelectorAll('#tabs button[data-t]')].map(b => b.dataset.t)") or []
    check(len(tabs) >= 11, f"the editor's tabs: {', '.join(tabs)}")
    for t in tabs:
        js(f"document.querySelector('#tabs button[data-t=\"{t}\"]').click()")
        wait(900)
        n = js(f"(document.getElementById('tab_{t}') || {{children: []}}).children.length") or 0
        check(n > 0, f"tab “{t}” opens ({n} blocks)")
    log_bad = js("[...document.querySelectorAll('#log .bad')].map(d => d.textContent)") or []
    check(not log_bad, f"the editor's own log reports no failure ({log_bad[:3]})")
    check(not errors(), f"no JavaScript error in the page ({errors()[:3]})")
    check(w.editor.bridge.blocked == 0 and w.editor.bridge.traffic > 20,
          f"everything the editor sent was its own protocol ({w.editor.bridge.traffic} messages, "
          f"{w.editor.bridge.blocked} held back)")
    from fm1companion import sims
    check(w.editor.bridge.out_filter is sims.sloop3_editor_out_allowed, "the bridge lets only SLOOP 3's protocol out")
    w.grab().save(str(home / "sloop3_editor.png"))
    # ---------------------------------------------------------------- backup
    _common.JobDialog.exec = lambda self: _job_exec(self)
    w.go("backups")
    wait(300)
    from fm1companion.ui import backups_page as BP
    check(BP.no_backup_reason(c) == "", "a backup is offered for SLOOP 3")
    path = BP.make_backup(c, w, "ui test")
    check(path is not None and path.exists(), f"backup written: {path.name if path else '-'}")
    if path is None:
        return finish()
    f = json.loads(path.read_text())
    a = backup.read(f)
    check(f["format"] == "sloop3-backup" and a.sloop3 and f["companion"]["firmware"] == "sloop3",
          f"a sloop3-backup file ({[(o['id'], o['size']) for o in a.objects]})")
    w.backups.refresh()
    wait(300)
    rows = [w.backups.tree.topLevelItem(i) for i in range(w.backups.tree.topLevelItemCount())]
    check(len(rows) == 1, f"listed on the Backups page ({len(rows)} row)")
    from fm1companion import backup_inspect as BI
    rep = BI.inspect(path)
    check(any("USR1" in x for x in rep.warnings), "the backup says that the samples are not in it")
    # the editor carries on after the app operation
    w.go("editor")
    check(until(lambda: w.editor.bridge.connected, 5000), "the bridge is connected again after the backup")
    js("window.__again = null; refreshInfo().then(i => { window.__again = i.ver; }).catch(e => { window.__again = 'ERR ' + e.message; })")
    if not until(lambda: js("window.__again") == c.sloop3.version, 8000):
        js("document.getElementById('con').click()")
    check(until(lambda: js("window.__again") == c.sloop3.version, 15000) or
          until(lambda: js("refreshInfo().then(i => { window.__again = i.ver; }), window.__again") == c.sloop3.version, 15000),
          "the editor talks to the FM-1 again after the backup")
    # ---------------------------------------------------------------- restore
    w.go("backups")
    from fm1companion.ui import sloop3_ui
    before = len(seen())
    w.backups.tree.setCurrentItem(w.backups.tree.topLevelItem(0))
    boxes.clear()
    w.backups._restore()
    wait(500)
    check(any("SLOOP 3" in b or "ersetzt" in b for b in boxes), f"restore asks first ({len(boxes)} dialogs)")
    after = backup.read(json.loads(sorted(path.parent.glob("*.json"))[-1].read_text()))
    check(len(list(path.parent.glob("*.json"))) >= 2, "a safety backup was made before the restore")
    g = BP.make_backup(c, w, "after restore")
    ga = backup.read(json.loads(g.read_text()))
    same = {o["id"]: o["crc"] for o in ga.objects}
    want = {o["id"]: o["crc"] for o in a.objects}
    check(same[1] == want[1] and same[3] == want[3] and same[4] == want[4] and same[2] == want[2],
          "after the restore the FM-1 holds the backup's sounds, banks and project")
    if not REAL:
        new = seen()[before:]
        check(all(m[:4] == bytes([0xF0, 0x7D, 0x53, 0x33]) for m in new) and
              any(m[4] == S3.Cmd.WEND for m in new), "the restore sent SLOOP 3's own commands only")
    # a Felucca backup onto SLOOP 3: refused, and nothing is sent
    fel = path.parent / "Felucca.json"
    fel.write_text(json.dumps({"format": "felucca-backup", "version": 1, "firmware": "FELUCCA v1.8.1",
                               "objects": [{"id": 0, "size": 1, "crc": 3523407757, "data": [0, 0]}]}))
    before = len(seen())
    boxes.clear()
    check(sloop3_ui.handles_restore(w, c, fel) and any("SLOOP 3" in b for b in boxes),
          "a backup of another firmware is refused for SLOOP 3")
    wait(300)
    if not REAL:
        check(len(seen()) == before, "… without a single command to the FM-1")
    # ---------------------------------------------------------------- Firmware+
    w.go("firmware")
    inst = w.firmware
    keys = [inst.cat.item(i).data(Qt.ItemDataRole.UserRole) for i in range(inst.cat.count())]
    check("sloop3" in keys and "sloop" in keys, "Firmware+ lists SLOOP 3 beside SLOOP")
    inst.cat.setCurrentRow(keys.index("sloop3"))
    wait(600)
    notes = inst.notes.toPlainText()
    check("m-bootloader" in notes and "beta" in notes.lower() and "M-Flasher" in notes, "its entry names what it needs")
    check(inst.b_install.isEnabled() and not inst.b_dl.isEnabled(), "no download, no package installation")
    state = {}

    def dialog_exec(self):
        if isinstance(self, sloop3_ui.Sloop3Dialog):
            state["level"] = self.level
            state["installer0"] = self.b_installer.isEnabled()
            state["check_visible"] = not self.chk.isHidden()
            self.chk.setChecked(True)
            state["installer1"] = self.b_installer.isEnabled()
            state["backup"] = self.b_backup.isEnabled()
            return 0
        return _dialog_exec(self)
    QDialog.exec = dialog_exec
    inst._install()
    check(state.get("level") == "warn" and state.get("installer0") is False and state.get("installer1") is True,
          f"unknown hardware revision: the installer page only after the explicit confirmation ({state})")
    check(state.get("backup") is False, "the flash backup is offered only in MIDI MODE")
    # the newer revision, read from the chip in MIDI MODE: blocked
    from fm1companion.flash import mboot
    c.mboot = mboot.Info("2.4-test", 1, 0x980F, 0x100000, mboot.JEDEC_NEW, "00" * 8)
    state.clear()
    inst._install()
    check(state.get("level") == "bad" and state.get("installer0") is False and state.get("installer1") is False,
          f"newer hardware revision (UCUN chip): the app does not offer the installer page ({state})")
    c.mboot = mboot.Info("2.4-test", 1, 0x980F, 0x100000, mboot.JEDEC_OLD, "00" * 8)
    state.clear()
    inst._install()
    check(state.get("level") == "ok" and state.get("installer0") is True and state.get("backup") is True,
          f"older revision in MIDI MODE: installer page and flash backup offered ({state})")
    c.mboot = None
    QDialog.exec = _dialog_exec
    check(not errors(), f"still no JavaScript error ({errors()[:3]})")
    finish()


def _job_exec(self):
    """a job dialog: wait for the job, then close"""
    loop = QEventLoop()
    t = QTimer()
    t.timeout.connect(lambda: loop.quit() if self.ok or self.error else None)
    t.start(100)
    QTimer.singleShot(120000, loop.quit)
    loop.exec()
    t.stop()
    wait(200)
    return 0


def finish():
    print("Home:", home)
    for line in console[:12]:
        print("console:", line)
    app.quit()


QTimer.singleShot(300, main)
app.exec()
fails = [r for r in results if not r[0]]
print(f"{len(results) - len(fails)}/{len(results)} passed")
sys.exit(1 if fails else 0)
