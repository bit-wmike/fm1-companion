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
last_dialog: dict = {}


def dialog_exec(self):
    from fm1companion.ui.common import JobDialog
    seen_dialogs.append(type(self).__name__)
    last_dialog[type(self).__name__] = self
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

    # ---- ST-14: the panel's sizes follow the device's width without container-query units (Qt 6.4 = Chromium 102)
    got = {}
    page.view.page().runJavaScript(
        "(function(){const d=document.getElementById('device'),l=document.querySelector('.label');"
        "return [d.getBoundingClientRect().width, parseFloat(getComputedStyle(l).fontSize),"
        " d.style.getPropertyValue('--u'), document.querySelector('.knob').title,"
        " getComputedStyle(document.querySelector('.knob')).boxShadow !== 'none']})()",
        lambda v: got.setdefault("css", v))
    check(until(lambda: "css" in got) and abs(got["css"][1] - got["css"][0] * 0.017) < 0.6 and got["css"][2]
          and got["css"][4], f"panel labels scale with the device (--u, no cqw): {got}")
    check("ziehen" in got["css"][3], f"ST-22: knob tooltip in the UI language: {got['css'][3]}")
    src = (ROOT / "fm1companion" / "sims" / "_studio" / "studio-panel.js").read_text("utf-8")
    check("cqw" not in src.split("const css = `")[1] and "container-type" not in src.split("const css = `")[1],
          "no container-query units in the panel's CSS")

    # ---- UX-09: "Export as WAV …" is in the top row, in view without scrolling – also at 1024×600
    from PyQt6.QtCore import QPoint
    w.resize(1024, 600)
    wait(500)
    y = page.b_export.mapTo(w, QPoint(0, 0)).y()
    check(page.b_export.isVisible() and 0 < y and y + page.b_export.height() < 600
          and not page.side.isAncestorOf(page.b_export), f"export button visible at 1024×600 (y={y})")
    w.resize(1280, 800)
    wait(300)

    # ---- ST-05: the twin was started empty – writing it back needs the second tick, and the dialog says so
    bdir = ctx.device_folder(ctx.settings.backups_dir)
    check(svc.source.get("kind") == "new" and not svc.same_device(ctx.profile), "the twin was started empty")
    n0 = set(bdir.glob("*.json"))
    boxes.clear()
    check(page.write_back() is None and set(bdir.glob("*.json")) == n0, "empty twin: dialog accepted without the "
          "second tick -> nothing written, no backup made")
    dlg = last_dialog.get("WriteBackDialog")
    check(dlg is not None and dlg.need_replace and not dlg.ok.isEnabled() and dlg.foreign is not None
          and "leer gestartet" in dlg.foreign.text() and "leer gestartet" in dlg.origin.text(),
          f"write-back dialog warns about the empty twin: {dlg and dlg.foreign and dlg.foreign.text()[:90]}")
    dlg.replace.setChecked(True)
    check(dlg.ok.isEnabled(), "… and the red button needs the tick")
    check(any("Haken" in b for b in boxes), f"refusal names the tick: {boxes[-1:]}")

    # ---- fetch from the FM-1
    before = set(bdir.glob("*.json"))
    p = page.fetch_from_device()
    check(p is not None and Path(p).exists() and Path(p) not in before, f"fetched from the FM-1, backup in the list: {p}")
    check(svc.source.get("kind") == "device" and svc.source.get("device") == ctx.profile.name,
          "source: " + svc.source_text())
    check(0 in svc.st.projects(), f"project 1 of the demo FM-1 in the twin: {svc.st.projects()}")
    check(not svc.st.rejected and "Nicht übernommen" not in page.src.text(),
          f"nothing rejected: the demo FM-1's data is real Melodee data ({svc.st.rejected})")
    check(svc.same_device(ctx.profile) and len(svc.source.get("baseline") or {}) > 20,
          "the twin's parts right after the fetch are remembered (write back leaves untouched parts alone)")
    # ST-16: nothing changed in the studio -> nothing is written (the dialog says which parts stay)
    n1 = set(bdir.glob("*.json"))
    seen_dialogs.clear()
    safety = page.write_back()
    dlg = last_dialog.get("WriteBackDialog")
    check(safety is not None and not dlg.need_replace and dlg.ok.isEnabled() and svc.st.last_write["written"] == []
          and len(svc.st.last_write["unchanged"]) > 20,
          f"write back of an unchanged twin: safety backup, nothing written ({svc.st.last_write['written']})")
    check(len(set(bdir.glob("*.json")) - n1) == 1, "… one safety backup made")
    # ST-06: Undo goes back to the state before the fetch – and says so (it kept saying "fetched from the FM-1")
    page.undo()
    check(svc.source.get("kind") == "new" and "leer gestartet" in page.src.text() and 0 not in svc.st.projects()
          and not svc.same_device(ctx.profile), "undo: the empty twin again, and the page says so: " + svc.source_text())
    page.redo()
    check(svc.source.get("kind") == "device" and ctx.profile.name in page.src.text() and 0 in svc.st.projects(),
          "redo: fetched from the FM-1 again")
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
    check(ok and sorted(x.name for x in out.iterdir()) == ["Test_2026.wav"] + [f"Test_2026_track{k}.wav" for k in
          range(1, 5)] and page.export_state.isVisible(), "mix + English stem names, nothing else left in the folder")
    # ST-17: the same name again never overwrites
    first = r.path.read_bytes()
    r2 = page.export_wav({"path": out / "Test_2026.wav", "seconds": 1.0, "start": "play", "fmt": "s24"})
    check(r2 is not None and r2.path.name == "Test_2026_2.wav" and r.path.read_bytes() == first,
          f"second export of that name gets a number: {r2 and r2.path.name}")
    # ST-03: a running export can be cancelled; nothing of it stays
    names = sorted(x.name for x in out.iterdir())
    QTimer.singleShot(1200, lambda: page._export_cancel and page._export_cancel())
    t0 = time.time()
    r3 = page.export_wav({"path": out / "Lang.wav", "seconds": 900.0, "start": "play", "fmt": "s24", "stems": True})
    check(r3 is None and time.time() - t0 < 60 and sorted(x.name for x in out.iterdir()) == names
          and "abgebrochen" in page.export_state.text(),
          f"export cancelled after {time.time() - t0:.1f} s, no file left: {page.export_state.text()}")
    r4 = page.export_wav({"path": out / "Danach.wav", "seconds": 1.0, "start": "play", "fmt": "s24"})
    check(r4 is not None and r4.frames == 44100, "the next export works")
    # ST-03: only Melodee's twin has a song that ends – elsewhere "whole song" is off and a fixed length the default
    from fm1companion.ui.pages.studio import ExportDialog
    cfg = dict(svc.cfg, export_mode="song", export_mode_chosen=False)
    d1, d2, d3 = (ExportDialog(page, dict(cfg), "x", None, fw) for fw in ("melodee", "skiff", "loom-8"))
    check(d1.r_song.isEnabled() and d1.r_song.isChecked() and d2.r_song.isEnabled() and d2.r_secs.isChecked()
          and "Skiff" in d2.r_song.toolTip() and not d3.r_song.isEnabled() and d3.r_secs.isChecked()
          and "LOOM-8" in d3.r_song.toolTip(),
          "export dialog: whole song is the default only for Melodee, off where there is no song: " + d3.r_song.toolTip()[:70])
    d4 = ExportDialog(page, dict(cfg, export_mode_chosen=True), "x", None, "skiff")
    d5 = ExportDialog(page, dict(cfg, export_mode_chosen=True), "x", None, "loom-8")
    check(d4.r_song.isChecked() and d5.r_secs.isChecked(), "… the user's own choice of the whole song is kept where possible")
    r5 = page.export_wav({"path": out / "Ohne.wav", "until_end": True, "start": "song", "fmt": "s24"})
    check(r5 is not None and not r5.started and r5.seconds < 5 and "Song-Kette" in page.export_state.text(),
          f"whole song without a song chain ends early and says so: {page.export_state.text()}")
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
    boxes.clear()
    check(page.write_back({"settings": False, "force": False}) is None and set(bdir.glob("*.json")) == n_before
          and any("Haken" in b for b in boxes), "a twin loaded from a backup: not written without the second tick")
    safety = page.write_back({"settings": False, "force": False, "replace": True})
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
    dlg = last_dialog.get("WriteBackDialog")
    check("WriteBackDialog" in seen_dialogs and dlg.need_replace and "Backup" in dlg.origin.text()
          and "woanders" in dlg.foreign.text(), "write back shows the confirmation dialog, with the twin's origin")
    # ST-09: dropouts are reported and shown
    was = svc._audio
    svc._audio = True
    svc._on_event({"ev": "audio", "underruns": 3, "target": 4096})
    check(page.audio_hint.isVisible() and "3" in page.audio_hint.text() and "93 ms" in page.audio_hint.text(),
          "dropout hint: " + page.audio_hint.text())
    svc._audio = was
    svc.audio_stats = {}
    page._audio_hint()
    # PERF-06: window minimised -> panel polling, page timer, clock and sound stop; playing music is never stopped
    w.showMinimized()
    check(until(lambda: svc._clock is False and not page.timer.isActive() and not svc.page_visible, 8000),
          "minimised and nothing plays: the twin's clock and the page's polling are paused")
    w.showNormal()
    check(until(lambda: svc._clock is True and page.timer.isActive(), 8000), "restored: running again")
    page.play("play")
    until(lambda: svc.transport().get("busy"), 3000)
    w.showMinimized()
    wait(1500)
    svc._tick()
    check(svc._clock is True and svc.transport().get("busy"), "minimised while playing: the music plays on")
    w.showNormal()
    wait(300)
    page.stop()

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

    # ---- a Skiff backup: the page starts the Skiff twin (projects A–D, six stems); the demo FM-1 runs Melodee, so
    # writing back is refused
    sk = S.Studio.create(firmware="skiff", backend="webengine", server=w.server)
    for k in range(6):
        for i in range(0, 16, 4):
            sk.link.request(Cmd.TRACK_STEP, [k, i, 1, 40 + 3 * k, 0, 0, 0, 0, 0, 100])
    sk.link.request(Cmd.PROJECT, [1, 1])                # project B
    sfile = bdir / "Studio-Test_skiff.json"
    backup.write_file(sfile, sk.backup().file)
    sk.close()
    check(page.open_backup(sfile) == sfile and svc.firmware == "skiff" and not svc.st.rejected,
          f"Skiff backup opened: the Skiff twin {svc.version} (nothing rejected)")
    page.refresh()
    labels = [page.projects.item(i).text() for i in range(page.projects.count())]
    check(len(labels) == 4 and labels[0].startswith("Projekt A") and "leer" not in labels[1],
          f"Skiff project list A–D: {labels}")
    check("Skiff" in page.src.text() and "Skiff" in page.banner.text() and "Melodee" in page.banner.text(),
          "banner names both firmwares: " + page.banner.text())
    check("Studio-Zwilling (Skiff 2.2)" in [x.label for x in svc._virtual_ports()], "virtual device: Skiff twin")
    boxes.clear()
    check(page.write_back({"settings": False, "force": True}) is None and any("Skiff" in b for b in boxes),
          f"write back of the Skiff twin onto the Melodee FM-1 refused: {boxes[-1:]}")
    r = page.export_wav({"path": out / "Skiff.wav", "seconds": 2.0, "start": "play", "fmt": "s24", "stems": True})
    check(r is not None and len(r.stems) == 6, f"Skiff export: mix + {r and len(r.stems)} stems")

    # ---- a JIANT FM backup: the page starts the JIANT twin (the current song's sections A–D); writing it back onto the
    # demo FM-1 (Melodee) is refused as well
    jt = S.Studio.create(firmware="jiant", backend="webengine", server=w.server)
    for k in range(4):
        for i in range(0, 16, 4):
            jt.link.request(Cmd.TRACK_STEP, [k, i, 1, 40 + 3 * k, 0, 0, 0, 0, 0, 100])
    jt.link.request(Cmd.PROJECT, [1, 1])                # section B
    jfile = bdir / "Studio-Test_jiant.json"
    backup.write_file(jfile, jt.backup().file)
    jver = jt.build.version.lstrip("v")                 # (JIANT names its versions "V10.61")
    jt.close()
    check(page.open_backup(jfile) == jfile and svc.firmware == "jiant" and not svc.st.rejected,
          f"JIANT backup opened: the JIANT twin {svc.version} (nothing rejected)")
    page.refresh()
    labels = [page.projects.item(i).text() for i in range(page.projects.count())]
    check(len(labels) == 4 and labels[0].startswith("Section A") and "leer" in labels[0] and "leer" not in labels[1],
          f"JIANT section list A–D: {labels}")
    check("JIANT FM" in page.src.text() and "JIANT FM" in page.banner.text() and "Melodee" in page.banner.text(),
          "banner names both firmwares: " + page.banner.text())
    check(f"Studio-Zwilling (JIANT FM {jver})" in [x.label for x in svc._virtual_ports()], "virtual device: JIANT twin")
    boxes.clear()
    check(page.write_back({"settings": False, "force": True}) is None and any("JIANT" in b for b in boxes),
          f"write back of the JIANT twin onto the Melodee FM-1 refused: {boxes[-1:]}")
    r = page.export_wav({"path": out / "JIANT.wav", "seconds": 2.0, "start": "play", "fmt": "s24", "stems": True})
    check(r is not None and len(r.stems) == 6, f"JIANT export: mix + {r and len(r.stems)} stems")

    # ---- a SLOOP backup (SLOOP's own format, sloopbk.py): the page starts the SLOOP twin (its sections A–D, four
    # stems); writing it back onto the demo FM-1 (Melodee) is refused
    sl = S.Studio.create(firmware="sloop", backend="webengine", server=w.server)
    for k in range(3):
        for i in range(0, 16, 4):
            sl.link.request(Cmd.TRACK_STEP, [k, i, 1, 48 + 5 * k, 0, 0, 0, 0, 0, 100])
    sl.link.request(Cmd.PROJECT, [1, 1])                # section B
    ofile = bdir / "Studio-Test_sloop.json"
    backup.write_file(ofile, sl.backup().file)
    over = sl.build.version
    sl.close()
    from fm1companion.ui.pages.studio import BackupPicker
    check(page.open_backup(ofile) == ofile and svc.firmware == "sloop" and not svc.st.rejected,
          f"SLOOP backup opened: the SLOOP twin {svc.version} (nothing rejected)")
    page.refresh()
    labels = [page.projects.item(i).text() for i in range(page.projects.count())]
    check(len(labels) == 4 and labels[0].startswith("Section A") and "leer" in labels[0] and "leer" not in labels[1],
          f"SLOOP section list A–D: {labels}")
    check("SLOOP" in page.src.text() and "SLOOP" in page.banner.text() and "Melodee" in page.banner.text(),
          "banner names both firmwares: " + page.banner.text())
    check(f"Studio-Zwilling (SLOOP {over})" in [x.label for x in svc._virtual_ports()], "virtual device: SLOOP twin")
    boxes.clear()
    check(page.write_back({"settings": False, "force": True}) is None and any("SLOOP" in b for b in boxes),
          f"write back of the SLOOP twin onto the Melodee FM-1 refused: {boxes[-1:]}")
    r = page.export_wav({"path": out / "SLOOP.wav", "seconds": 2.0, "start": "play", "fmt": "s24", "stems": True})
    check(r is not None and len(r.stems) == 4, f"SLOOP export: mix + {r and len(r.stems)} stems")
    picker = BackupPicker(page, ctx.settings.backups_dir)
    found = [picker.tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) for i in range(picker.tree.topLevelItemCount())]
    picker.deleteLater()
    check(str(ofile) in found, f"the backup list offers SLOOP backups too ({len(found)} entries)")
    # ---- the demo's SLOOP device (DEMO-2, "SLOOP 2.8.5"): fetched into its twin, written back with a safety backup
    port2 = next((x for x in ctx.ports if x.serial == "DEMO-2"), None)
    if port2 is not None:
        ctx.connect_port(port2)
        check(until(lambda: ctx.connected and ctx.firmware_key == "sloop", 20000),
              f"the demo's SLOOP FM-1 connected ({ctx.firmware_key})")
        page.refresh()
        ok_dev, why = page._device_ok()
        check(ok_dev, f"the studio accepts the SLOOP FM-1 {why}")
        got_path = page.fetch_from_device()
        check(got_path is not None and svc.firmware == "sloop" and svc.version == "2.8.5",
              f"fetched from the SLOOP FM-1 into the SLOOP twin {svc.version} (rejected: {svc.st.rejected})")
        safety = page.write_back({"settings": False, "force": False})
        check(safety is not None and Path(safety).exists() and backup.load_file(safety).sloop,
              f"written back onto the SLOOP FM-1, safety backup first: {safety and Path(safety).name}")
        ctx.connect_port(ctx.ports[0])
        check(until(lambda: ctx.connected and ctx.firmware_key == "melodee", 20000), "the Melodee demo FM-1 again")
    else:
        check(False, "demo port DEMO-2 (SLOOP) missing")
    # ---- a CoralFlower backup: the page starts the CoralFlower twin (no project slots: a note instead); writing it back
    # onto the demo FM-1 (Melodee) is refused as well
    cf = S.Studio.create(firmware="coralflower", backend="webengine", server=w.server)
    cf.set_clock(False)                                 # REC on HOME, a bar counted in, two chords, REC: pattern A
    cf.press("REC")
    cf.backend.call("run", ms=2100)
    for k in (7, 11):
        cf.backend.call("keys", mask=1 << k)
        cf.backend.call("run", ms=900)
        cf.backend.call("keys", mask=0)
        cf.backend.call("run", ms=100)
    cf.press("REC")
    cf.backend.call("run", ms=2500)
    cf.stop()
    cf.set_clock(True)
    cfile = bdir / "Studio-Test_coralflower.json"
    backup.write_file(cfile, cf.backup().file)
    cver = cf.build.version
    cf.close()
    check(page.open_backup(cfile) == cfile and svc.firmware == "coralflower" and not svc.st.rejected,
          f"CoralFlower backup opened: the CoralFlower twin {svc.version} (nothing rejected)")
    page.refresh()
    labels = [page.projects.item(i).text() for i in range(page.projects.count())]
    check(len(labels) == 1 and "CoralFlower" in labels[0] and page.projects.item(0).data(Qt.ItemDataRole.UserRole) is None,
          f"CoralFlower: no project slots, a note: {labels}")
    page.projects.setCurrentRow(0)
    page.load_project()                                 # (the note loads nothing)
    check("CoralFlower" in page.src.text() and "CoralFlower" in page.banner.text() and "Melodee" in page.banner.text(),
          "banner names both firmwares: " + page.banner.text())
    check(f"Studio-Zwilling (CoralFlower {cver})" in [x.label for x in svc._virtual_ports()],
          "virtual device: CoralFlower twin")
    boxes.clear()
    check(page.write_back({"settings": False, "force": True}) is None and any("CoralFlower" in b for b in boxes),
          f"write back of the CoralFlower twin onto the Melodee FM-1 refused: {boxes[-1:]}")
    r = page.export_wav({"path": out / "CoralFlower.wav", "seconds": 2.0, "start": "play", "fmt": "s24", "stems": True})
    check(r is not None and len(r.stems) == 4 and r.peak > 0.05, f"CoralFlower export: mix + {r and len(r.stems)} stems")
    # ---- a ChoralRoot FM-1 backup: the page starts the ChoralRoot twin (no project slots: its loop slots, a note);
    # writing it back onto the demo FM-1 (Melodee) is refused as well
    cr = S.Studio.create(firmware="choralroot", backend="webengine", server=w.server)
    cr.set_clock(False)                                 # REC, two chords, REC: the loop plays; stop, SAVE held
    cr.press("REC")
    cr.backend.call("run", ms=200)
    for k in (7, 12):
        cr.backend.call("keys", mask=1 << k)
        cr.backend.call("run", ms=900)
        cr.backend.call("keys", mask=0)
        cr.backend.call("run", ms=100)
    cr.press("REC")
    cr.backend.call("run", ms=300)
    cr.stop()
    cr.backend.call("press", label=S.B["SAVE"], ms=1500)
    cr.backend.call("run", ms=1500)
    cr.set_clock(True)
    rfile = bdir / "Studio-Test_choralroot.json"
    backup.write_file(rfile, cr.backup().file)
    rver = cr.build.version
    cr.close()
    check(page.open_backup(rfile) == rfile and svc.firmware == "choralroot" and not svc.st.rejected,
          f"ChoralRoot backup opened: the ChoralRoot twin {svc.version} (nothing rejected)")
    page.refresh()
    labels = [page.projects.item(i).text() for i in range(page.projects.count())]
    check(len(labels) == 1 and "ChoralRoot" in labels[0] and page.projects.item(0).data(Qt.ItemDataRole.UserRole) is None,
          f"ChoralRoot: no project slots, a note: {labels}")
    check("ChoralRoot" in page.src.text() and "ChoralRoot" in page.banner.text() and "Melodee" in page.banner.text(),
          "banner names both firmwares: " + page.banner.text())
    check(f"Studio-Zwilling (ChoralRoot {rver})" in [x.label for x in svc._virtual_ports()],
          "virtual device: ChoralRoot twin")
    boxes.clear()
    check(page.write_back({"settings": False, "force": True}) is None and any("ChoralRoot" in b for b in boxes),
          f"write back of the ChoralRoot twin onto the Melodee FM-1 refused: {boxes[-1:]}")
    r = page.export_wav({"path": out / "ChoralRoot.wav", "seconds": 2.0, "start": "play", "fmt": "s24", "stems": True})
    check(r is not None and len(r.stems) == 2 and r.peak > 0.05, f"ChoralRoot export: mix + {r and len(r.stems)} stems")
    # ---- a KARMA backup (SLOOP's file format): the page starts the KARMA twin (the song's parts A–D); writing it back
    # onto the demo FM-1 (Melodee) is refused as well
    kt = S.Studio.create(firmware="karma", backend="webengine", server=w.server)
    for k in range(4):
        for i in range(0, 16, 4):
            kt.link.request(Cmd.TRACK_STEP, [k, i, 1, 45 + 3 * k, 0, 0, 0, 0, 0, 100])
    kt.link.request(Cmd.PROJECT, [1, 1])                # part B
    kfile = bdir / "Studio-Test_karma.json"
    backup.write_file(kfile, kt.backup().file)
    kver = kt.build.version
    kt.close()
    check(page.open_backup(kfile) == kfile and svc.firmware == "karma" and not svc.st.rejected,
          f"KARMA backup opened: the KARMA twin {svc.version} (nothing rejected)")
    page.refresh()
    labels = [page.projects.item(i).text() for i in range(page.projects.count())]
    check(len(labels) == 4 and labels[0].startswith("Teil A") and "leer" in labels[0] and "leer" not in labels[1],
          f"KARMA part list A–D: {labels}")
    check("KARMA" in page.src.text() and "KARMA" in page.banner.text() and "Melodee" in page.banner.text(),
          "banner names both firmwares: " + page.banner.text())
    check(f"Studio-Zwilling (KARMA {kver})" in [x.label for x in svc._virtual_ports()], "virtual device: KARMA twin")
    boxes.clear()
    check(page.write_back({"settings": False, "force": True}) is None and any("KARMA" in b for b in boxes),
          f"write back of the KARMA twin onto the Melodee FM-1 refused: {boxes[-1:]}")
    r = page.export_wav({"path": out / "KARMA.wav", "seconds": 2.0, "start": "play", "fmt": "s24", "stems": True})
    check(r is not None and len(r.stems) == 4, f"KARMA export: mix + {r and len(r.stems)} stems")
    # ---- a backup of Felucca's own web editor (no companion block, 'firmware' its INFO): the page starts the Felucca twin
    # (projects A–D); writing it back onto the demo FM-1 (Melodee) is refused as well
    ft = S.Studio.create(firmware="felucca", backend="webengine", server=w.server)
    for k in range(4):
        for i in range(0, 16, 4):
            ft.link.request(Cmd.TRACK_STEP, [k, i, 1, 45 + 3 * k, 0, 0, 0, 0, 0, 100])
    ft.link.request(Cmd.PROJECT, [1, 1])                # project B
    ffile = bdir / "Studio-Test_felucca.json"
    fplain = dict(ft.backup().file)
    fplain.pop("companion", None)
    fplain["firmware"] = "FELUCCA " + ft.build.version
    backup.write_file(ffile, fplain)
    fver = ft.build.version
    ft.close()
    check(page.open_backup(ffile) == ffile and svc.firmware == "felucca" and not svc.st.rejected,
          f"Felucca backup opened: the Felucca twin {svc.version} (nothing rejected)")
    page.refresh()
    labels = [page.projects.item(i).text() for i in range(page.projects.count())]
    check(len(labels) == 4 and labels[0].startswith("Projekt A") and "leer" in labels[0] and "leer" not in labels[1],
          f"Felucca project list A–D: {labels}")
    check("Felucca" in page.src.text() and "Felucca" in page.banner.text() and "Melodee" in page.banner.text(),
          "banner names both firmwares: " + page.banner.text())
    check(f"Studio-Zwilling (Felucca {fver.lstrip('v')})" in [x.label for x in svc._virtual_ports()], "virtual device: Felucca twin")
    boxes.clear()
    check(page.write_back({"settings": False, "force": True}) is None and any("Felucca" in b for b in boxes),
          f"write back of the Felucca twin onto the Melodee FM-1 refused: {boxes[-1:]}")
    r = page.export_wav({"path": out / "Felucca.wav", "seconds": 2.0, "start": "play", "fmt": "s24", "stems": True})
    check(r is not None and len(r.stems) == 4, f"Felucca export: mix + {r and len(r.stems)} stems")
    # ---- an FM1 Open backup (its own file, fm1openbk.py): the page starts the FM1 Open twin (16 project slots);
    # writing it back onto the demo FM-1 (Melodee) is refused
    from fm1companion import fm1open as F, fm1openbk
    ot = S.Studio.create(firmware="fm1-open", backend="webengine", server=w.server)
    with F.Client(ot.link) as c:
        c.query(7, bytes([1]))                          # the working project into slot 2
    ofile = bdir / "Studio-Test_fm1open.json"
    backup.write_file(ofile, ot.backup().file)
    over = ot.build.version
    ot.close()
    check(AB.read_meta(ofile) is not None and AB.read_meta(ofile).fw_key == "fm1-open", "FM1 Open backup in the list")
    check(page.open_backup(ofile) == ofile and svc.firmware == "fm1-open" and not svc.st.rejected,
          f"FM1 Open backup opened: the FM1 Open twin {svc.version} (nothing rejected)")
    page.refresh()
    labels = [page.projects.item(i).text() for i in range(page.projects.count())]
    check(len(labels) == 16 and "leer" in labels[0] and "leer" not in labels[1], f"FM1 Open projects 1–16: {labels[:3]}")
    check("FM1 Open" in page.src.text() and "FM1 Open" in page.banner.text(), "banner names FM1 Open: " + page.banner.text())
    check(f"Studio-Zwilling (FM1 Open {over})" in [x.label for x in svc._virtual_ports()], "virtual device: FM1 Open twin")
    boxes.clear()
    check(page.write_back({"settings": False, "force": True}) is None and any("FM1 Open" in b for b in boxes),
          f"write back of the FM1 Open twin onto the Melodee FM-1 refused: {boxes[-1:]}")
    # an FM-1 with the original V15 (no editor, no backup protocol: identity FM-1_015) or with FM1 Open (FM-1_8000014),
    # as the page sees them: the FM1 Open twin stands in for V15 with the note, fetch and write are refused; an FM1 Open
    # FM-1 may be fetched
    from types import SimpleNamespace
    real = page.ctx
    for key, version, may in (("stock-v15", "V15", False), ("fm1-open", "V14", True)):
        page.ctx = SimpleNamespace(is_virtual=False, midi_connected=True, connected=False, info=None, port=object(),
                                   protocol="generic", firmware_key=key, identity="",
                                   profile=SimpleNamespace(name="Alt", firmware_version=version, folder_name="Alt"))
        try:
            ok, why = page._device_ok()
            page._banner()
            text = page.banner.text()
        finally:
            page.ctx = real
        check(ok == may and (may or "V15" in text and "FM1 Open" in text and why),
              f"{key}: fetch {'allowed' if ok else 'refused'}, banner: {text[:120]}")
        if may:                                         # ST-04: same version, but never "identical sound" for FM1 Open
            check("identisch" not in text and "abweichen" in text and "V14" in text, "FM1 Open banner is honest: " + text)
    check(svc.firmware_for("stock-v15") == "fm1-open" and svc.firmware_for("stock-v16") == "fm1-open",
          "the original V15/V16 opens the FM1 Open twin")
    page.refresh()
    # ---- an X0X backup (its own image, x0xbk.py) and X0X's own .x0b file: the page starts the X0X twin (no project
    # slots); writing it back onto the demo FM-1 (Melodee) is refused as well
    from fm1companion import x0xbk
    xt = S.Studio.create(firmware="x0x", backend="webengine", server=w.server)
    xt.set_clock(False)
    for k in (0, 7, 14, 21):
        xt.backend.call("keys", mask=1 << k)
        xt.backend.call("run", ms=40)
        xt.backend.call("keys", mask=0)
        xt.backend.call("run", ms=40)
    xt.set_clock(True)
    xfile = bdir / "Studio-Test_x0x.x0b"
    xfile.write_bytes(x0xbk.image_of(xt.backup().file))
    xver = xt.build.version
    xt.close()
    check(page.open_backup(xfile) == xfile and svc.firmware == "x0x" and not svc.st.rejected,
          f"X0X's own backup file opened: the X0X twin {svc.version} (nothing rejected)")
    page.ctx = SimpleNamespace(is_virtual=False, midi_connected=True, connected=False, info=None, port=object(),
                               protocol="generic", firmware_key="stock-v15", identity="",
                               profile=SimpleNamespace(name="Alt", firmware_version="V15", folder_name="Alt"))
    try:                                                # ST-13: the stand-in note names the twin that really runs
        page._banner()
        text = page.banner.text()
    finally:
        page.ctx = real
    check("X0X" in text and "V15" in text and "FM1 Open" not in text, "original firmware + another twin: " + text[:140])
    page.refresh()
    labels = [page.projects.item(i).text() for i in range(page.projects.count())]
    check(len(labels) == 1 and "X0X" in labels[0], f"X0X: no project slots, a note: {labels}")
    check("X0X" in page.src.text() and "X0X" in page.banner.text() and "Melodee" in page.banner.text(),
          "banner names both firmwares: " + page.banner.text())
    check(f"Studio-Zwilling (X0X {xver})" in [x.label for x in svc._virtual_ports()], "virtual device: X0X twin")
    boxes.clear()
    check(page.write_back({"settings": False, "force": True}) is None and any("X0X" in b for b in boxes),
          f"write back of the X0X twin onto the Melodee FM-1 refused: {boxes[-1:]}")
    r = page.export_wav({"path": out / "X0X.wav", "seconds": 2.0, "start": "play", "fmt": "s24", "stems": True})
    check(r is not None and len(r.stems) == 5 and r.peak > 0.05, f"X0X export: mix + {r and len(r.stems)} stems")
    # ---- LOOM-8's own backup file (.loom8, fm1-base's image) and an empty AMB-1 twin (no backup over USB)
    lt = S.Studio.create(firmware="loom-8", backend="webengine", server=w.server)
    lt.set_clock(False)
    for k in (0, 7, 14, 21):
        lt.backend.call("keys", mask=1 << k)
        lt.backend.call("run", ms=60)
        lt.backend.call("keys", mask=0)
        lt.backend.call("run", ms=60)
    lt.press("SAVE")
    lt.backend.call("run", ms=3000)
    lt.set_clock(True)
    lfile = bdir / "Studio-Test_loom.loom8"
    lfile.write_bytes(x0xbk.image_of(lt.backup().file))
    lt.close()
    check(page.open_backup(lfile) == lfile and svc.firmware == "loom-8" and not svc.st.rejected,
          f"LOOM-8's own backup file opened: the LOOM-8 twin {svc.version}")
    boxes.clear()
    check(page.write_back({"settings": False, "force": True}) is None and any("LOOM-8" in b for b in boxes),
          f"write back of the LOOM-8 twin onto the Melodee FM-1 refused: {boxes[-1:]}")
    svc.new_empty(firmware="amb-1")
    page.refresh()
    check(svc.firmware == "amb-1" and "AMB-1" in page.banner.text(), "an empty AMB-1 twin: " + page.banner.text())
    r = page.export_wav({"path": out / "AMB-1.wav", "seconds": 2.0, "start": "none", "fmt": "s24", "stems": True})
    check(r is not None and len(r.stems) == 4 and r.peak > 0.02, f"AMB-1 export: mix + {r and len(r.stems)} stems")
    # ST-08: the twin's render process dies -> the twin is closed, the user is told and gets the saved state back
    import signal
    svc.new_empty(firmware="melodee")
    svc.source = {}
    svc.new_empty(firmware="melodee")
    svc.autosave(force=True)
    pid = svc.page.renderProcessPid()
    boxes.clear()
    t0 = time.time()
    os.kill(pid, signal.SIGKILL)
    check(until(lambda: any("unerwartet" in b for b in boxes), 20000) and time.time() - t0 < 25,
          f"twin process killed: message after {time.time() - t0:.1f} s: {boxes[-1:]}")
    check(until(lambda: svc.running, 30000) and svc.page.renderProcessPid() not in (0, pid)
          and svc.st.backend.call("info").get("version"), "… and the automatically saved state is open again")
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
