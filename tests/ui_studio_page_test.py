# SPDX-License-Identifier: GPL-3.0-only
"""Studio page against the demo device (offscreen): the twin starts, fetch from the FM-1 (the backup also lands in
the list), version banner, open from a backup, play/stop, WAV export (short, with stems), save the Studio state
automatically and load it again, snapshot + undo/redo, writing back with confirmation
and safety backup, the twin as a virtual device (projects via StudioTransport, never automatic backups),
playback target "Im Studio" (in the Studio) in the project view. Optional screenshots (FM1C_SHOTS=<folder>).

  QT_QPA_PLATFORM=offscreen QTWEBENGINE_DISABLE_SANDBOX=1 python3 tests/ui_studio_page_test.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import traceback
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("FM1C_LANG", "de")      # these checks expect the German user interface
home = Path(tempfile.mkdtemp(prefix="fm1c-studiopage-"))
for k, sub in (("XDG_CONFIG_HOME", ".config"), ("XDG_DATA_HOME", ".local/share"), ("XDG_CACHE_HOME", ".cache")):
    os.environ[k] = str(home / sub)
os.environ["FM1C_DEMO"] = "1"
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu --autoplay-policy=no-user-gesture-required")
SHOTS = os.environ.get("FM1C_SHOTS", "")

errors: list[str] = []
sys.excepthook = lambda t, v, tb: errors.append("".join(traceback.format_exception(t, v, tb)))

import PyQt6.QtWebEngineWidgets  # noqa: E402,F401
from PyQt6.QtCore import QCoreApplication, QEventLoop, Qt, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
app = QApplication(sys.argv)

from fm1companion import config  # noqa: E402

s = config.load()
s.user_root = str(home / "FM-1")
s.check_on_start = False
config.save(s)
config.ensure_dirs()
boxes: list[str] = []
seen_dialogs: list[str] = []


def fake_msg_exec(self):
    boxes.append(self.text())
    for b in self.buttons():
        if self.buttonRole(b) in (QMessageBox.ButtonRole.AcceptRole, QMessageBox.ButtonRole.YesRole):
            self._clicked = b
            return 0
    self._clicked = None
    return 0


QMessageBox.exec = fake_msg_exec
QMessageBox.clickedButton = lambda self: getattr(self, "_clicked", None)
dialog_answer = {"accept": True}


def dialog_exec(self):
    from fm1companion.ui.common import JobDialog
    seen_dialogs.append(type(self).__name__)
    if isinstance(self, JobDialog):
        self.show()
        t0 = time.time()
        while time.time() - t0 < 120:
            app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
            if self.buttons.button(self.buttons.StandardButton.Close).isEnabled():
                break
        self.hide()
        self.log_text = self.logv.toPlainText()
        return 1 if self.ok else 0
    return 1 if dialog_answer["accept"] else 0


QDialog.exec = dialog_exec

from fm1companion.ui.main_window import MainWindow  # noqa: E402

w = MainWindow()
w.resize(1280, 800)
w.show()
ctx = w.ctx
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


def shot(name, width=None):
    if not SHOTS:
        return
    if width:
        w.resize(width, 800)
        wait(600)
    w.grab().save(str(Path(SHOTS) / name))


def flow():
    from fm1companion import autobackup as AB, backup, studio as S
    from fm1companion.ui.pages.studio import StudioPage
    check(until(lambda: ctx.connected), "demo device connected")
    svc = w.studio
    check(svc is not None and not svc.running, "Studio service present, the twin only starts when needed")
    keys = [w.nav.item(i).data(Qt.ItemDataRole.UserRole) for i in range(w.nav.count())]
    check("studio" in keys and keys.index("studio") < keys.index("projects"), "page Studio: first in MUSIC")
    w.go("studio")
    page = w.page("studio")
    check(isinstance(page, StudioPage), "Studio page opened")
    check(until(lambda: svc.running, 30000), f"twin running: Melodee {svc.version}")
    check(svc.version == "v1.6.0-rc.7", "version matching the demo FM-1 (Melodee 1.6.0-rc.7)")
    check(until(lambda: page.view.page() is svc.page), "control surface shows the twin")
    wait(800)
    # control surface: the screen is drawn (frame query in the worker)
    got = {}
    page.view.page().runJavaScript("!!document.getElementById('device') && document.querySelectorAll('.key').length",
                                   lambda v: got.setdefault("keys", v))
    check(until(lambda: "keys" in got) and got["keys"] == 27, f"control surface with 27 keys: {got}")

    # ---- fetch from the FM-1
    bdir = ctx.device_folder(ctx.settings.backups_dir)
    before = set(bdir.glob("*.json"))
    p = page.fetch_from_device()
    check(p is not None and Path(p).exists() and Path(p) not in before, f"fetched from the FM-1, backup in the list: {p}")
    check(svc.source.get("kind") == "device" and svc.source.get("device") == ctx.profile.name,
          "source: " + svc.source_text())
    check(0 in svc.st.projects(), f"project 1 of the demo FM-1 in the twin: {svc.st.projects()}")
    check(svc.st.rejected, f"rejected demo parts noted (demo data is not real Melodee): {svc.st.rejected}")
    check(page.banner.isVisible() and "dieselbe Firmware" in page.banner.text(), "banner: " + page.banner.text())
    st_file = svc.state_file()
    check(st_file.exists() and st_file.parent.name == ctx.profile.folder_name, f"Studio state saved: {st_file}")
    # version warning (the device reports a different version)
    old = ctx.info.version
    ctx.info.version = "MELODEE v1.6.0-rc.8"
    page.refresh()
    check("rc.8" in page.banner.text() and "abweichen" in page.banner.text(), "version warning: " + page.banner.text())
    ctx.info.version = old
    page.refresh()

    # ---- play / stop (space bar via the page)
    page.play("play")
    check(until(lambda: svc.transport().get("busy") or svc.transport().get("playing"), 3000), "PLAY: the twin plays")
    page.toggle_play()
    check(until(lambda: not svc.transport().get("busy"), 3000), "space bar/toggle: stopped")
    shot("studio-1280.png")

    # ---- WAV export (short) with stems. The demo device's music is an old test format (engines Melodee
    # does not know: silent) – hence a fresh device with the power-on sounds and a few steps per track
    from fm1companion.protocol import Cmd
    svc.st.set_flash(bytes([0xFF]) * 0x100000)
    for k in range(4):
        for i in range(0, 16, 2 + k):
            svc.link.request(Cmd.TRACK_STEP, [k, i, 1, 36 + 12 * (k % 3) + (i % 5), 0, 0, 0, 0, 0, 100])
    out = home / "export"
    r = page.export_wav({"path": out / "Test_2026.wav", "seconds": 3.0, "start": "play", "fmt": "s24", "stems": True})
    ok = r is not None and r.path.exists() and len(r.stems) == 4
    if ok:
        with wave.open(str(r.path)) as wf:
            ok = wf.getsampwidth() == 3 and wf.getframerate() == 44100 and wf.getnchannels() == 2
    check(ok and r.peak > 0.01, f"WAV 24 bit + 4 stems exported, audible: {r and r.path}, peak {r and r.peak:.3f}")
    from fm1companion.ui.studio_service import music_dir
    check("FM-1 Studio" in str(music_dir()), f"default folder in the music folder: {music_dir()}")

    # ---- snapshot, change, undo/redo, autosave
    page.snap()
    n0 = len(svc.history)
    from fm1companion.protocol import Cmd
    L = svc.link
    for i in range(0, 16, 4):
        L.request(Cmd.TRACK_STEP, [1, i, 1, 48 + i, 0, 0, 0, 0, 0, 100])
    check(L.request(Cmd.PROJECT, [1, 3])[2] == 1, "change in the twin: project 4 saved")
    sum0 = svc._sum
    wait(600)                                           # the firmware writes the flash in its main loop
    svc._tick()                                         # the autosave tick detects the change
    check(svc._sum != sum0 and len(svc.history) == n0 + 1 and 3 in svc.st.projects(),
          f"change detected, saved, as a snapshot: {len(svc.history)}")
    page.undo()
    check(3 not in svc.st.projects() and svc.can_redo(), "undo: project 4 empty again")
    page.redo()
    check(3 in svc.st.projects(), "redo: project 4 back")
    # load the Studio state again after restarting the twin
    svc.autosave(force=True)
    svc.st.power_cycle()
    svc.st.load_state(svc.state_file())
    check(3 in svc.st.projects(), "Studio state loaded from the file")

    # ---- open from a backup (a real Melodee backup: the twin's)
    arch = svc.st.backup()
    bfile = bdir / "Studio-Test_melodee.json"
    backup.write_file(bfile, arch.file)
    check(page.open_backup(bfile) == bfile and svc.source.get("kind") == "backup" and not svc.st.rejected,
          "opened from a backup (nothing rejected): " + svc.source_text())

    # ---- write back: confirmation + safety backup
    n_before = set(bdir.glob("*.json"))
    dialog_answer["accept"] = False
    check(page.write_back() is None, "write back cancelled: nothing happens")
    dialog_answer["accept"] = True
    seen_dialogs.clear()
    safety = page.write_back({"settings": False, "force": False})
    new = set(bdir.glob("*.json")) - n_before
    meta = AB.read_meta(safety) if safety else None
    check(safety is not None and safety in new and meta and meta.kind == "sicherheit",
          f"safety backup before writing: {safety and safety.name}")
    from fm1companion import projects as PJ
    slots = []
    d = ctx.job(w, "Test", lambda wk, link: PJ.list_slots(link))
    slots = [x.slot for x in (d.result_value or ([], 0))[0] if x.used]
    check(4 in slots, f"the demo FM-1 now has project 4 from the Studio: {slots}")
    page.write_back()
    check("WriteBackDialog" in seen_dialogs, "write back shows the confirmation dialog")

    # ---- virtual device
    page.use_as_device()
    check(until(lambda: ctx.connected and ctx.is_virtual, 10000), f"twin as the active device: {ctx.title()}")
    check(ctx.port not in ctx.ports and "virtuell" in ctx.status_text(), "never in ctx.ports, clearly marked as virtual")
    texts = [w.dev_combo.itemText(i) for i in range(w.dev_combo.count())]
    check(any("virtuell" in t for t in texts), f"device selection: {texts}")
    ab = getattr(w.backups, "auto", None) or getattr(w, "autobackup", None)
    if ab is not None:
        check(ab.busy_reason().startswith("Studio") and ab.run() is False, "no automatic backups of the twin")
    d = ctx.job(w, "Test", lambda wk, link: PJ.list_slots(link))
    vslots = [x.slot for x in (d.result_value or ([], 0))[0] if x.used]
    check(4 in vslots, f"the projects code reads the twin via StudioTransport: {vslots}")
    check(not page.b_fetch.isEnabled() and not page.b_write.isEnabled(), "fetch/write back off while the "
          "twin itself is active")
    # firmware/recovery: the twin is never a flash target
    from fm1companion.ui import firmware_page as FP
    from fm1companion.ui import common as UC
    errs = []
    saved_err = FP.show_error
    FP.show_error = lambda *a, **k: errs.append(a[1] if len(a) > 1 else "")
    try:
        check(FP.target_pin(ctx) is None, "twin: no installation target (target_pin)")
        sp = next(iter(__import__("fm1companion.firmware", fromlist=["catalog"]).catalog()))
        check(FP.install_flow(w, ctx, sp, dry_run=True) is False and any("kein echtes FM-1" in e or
              "Kein echtes" in e for e in errs), f"twin: installation refused ({errs[-1:] })")
    finally:
        FP.show_error = saved_err
    fwp = getattr(w, "firmware", None)
    if fwp is not None and hasattr(fwp, "b_install"):
        fwp._release_selected()
        check(not fwp.b_install.isEnabled(), "twin: button \"Install on the active FM-1\" off")
    # project view: playback target "Im Studio" (in the Studio)
    pv = w.page("project_view")
    if pv is not None and hasattr(pv, "player_bar"):
        items = [pv.player_bar.target.itemText(i) for i in range(pv.player_bar.target.count())]
        check(any("Studio" in t and "identisch" in t for t in items), f"playback targets: {items}")
    # back to the demo FM-1
    real = ctx.ports[0]
    ctx.connect_port(real)
    check(until(lambda: ctx.connected and not ctx.is_virtual, 15000), "the real (demo) FM-1 again")
    # compose: preview "im Studio (identischer Klang)" (in the Studio, identical sound) instead of the approximation on the computer
    w.go("compose")
    cp = w.page("compose")
    bar = cp.play_bar
    items = [bar.target.itemText(i) for i in range(bar.target.count())]
    check("im Studio (identischer Klang)" in items and not any("Annäherung" in t for t in items),
          f"compose: targets {items}")
    bar.target.setCurrentIndex(bar.target.findData("studio"))
    cp.preview()
    check(until(lambda: bar.playing and "Studio-Zwilling" in bar.status.text(), 10000) and svc.users == 1,
          "preview plays in the Studio twin: " + bar.status.text()[:120])
    bar.stop()
    check(not bar.playing and svc.users == 0, "stop releases the twin")
    w.go("studio")
    wait(500)
    shot("studio-640.png", 640)
    if SHOTS:
        sb = w.page_scroll.verticalScrollBar()
        sb.setValue(sb.maximum())
        wait(300)
        shot("studio-640-unten.png")
        sb.setValue(0)
    shot("studio-960.png", 960)
    w.resize(1280, 800)


try:
    flow()
except Exception:  # noqa: BLE001
    traceback.print_exc()
    results.append((False, "Ausnahme"))
check(not errors, "keine unbehandelten Ausnahmen" + ("".join(errors)[-3000:] if errors else ""))
w.close()
app.processEvents()
bad = [x for ok, x in results if not ok]
print(f"\n{len(results) - len(bad)}/{len(results)} ok")
os._exit(1 if bad else 0)
