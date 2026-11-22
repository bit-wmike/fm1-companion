# SPDX-License-Identifier: GPL-3.0-only
"""The screenshots of README.md and docs/TOUR.md: English UI, demo mode, 1280x800, offscreen
(needs PyQt6 with QtWebEngine; Pillow, if installed, makes the PNG files smaller).

  cd <repo> && python3 tools/fetch_tools.py        # once: the pinned M-Flasher, as in an installed package
  PYTHONPATH=. QT_QPA_PLATFORM=offscreen QTWEBENGINE_DISABLE_SANDBOX=1 \
      python3 tools/make_screenshots.py . docs/screenshots

Everything shown is synthetic: the three simulated FM-1 of demo mode in a throw-away home folder, generated sounds,
and – for the dialogs of the bundled tools (M-Flasher, m-bootloader, SLOOP 3) – plans built here with made-up units
("FM-1 Earlier", unit ID 3141592653589793) and the test stand-ins' flash image (tests/fake_mflasher.py). No FM-1 is
connected and nothing is sent to one. The home folder's path is shown as "~".

SHOT_ONLY=studio,tools limits the run to some sections (see SECTIONS at the end); SHOT_THEME=dark takes them in the
dark theme. The SLOOP 3 editor is taken in a second run of this script (--sloop3) against tests/fake_sloop3.py.
"""
import array
import datetime as dt
import math
import os
import random
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import traceback
import wave
from pathlib import Path
from types import SimpleNamespace as NS

REPO, OUT = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
SLOOP3_RUN = "--sloop3" in sys.argv[3:]
ONLY = {x.strip() for x in os.environ.get("SHOT_ONLY", "").split(",") if x.strip()}
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))            # the stand-ins' synthetic flash image
home = Path(tempfile.mkdtemp(prefix="fm1c-shots-"))
for k, sub in (("XDG_CONFIG_HOME", ".config"), ("XDG_DATA_HOME", ".local/share"), ("XDG_CACHE_HOME", ".cache"),
               ("XDG_STATE_HOME", ".local/state")):
    os.environ[k] = str(home / sub)
(home / "tmp").mkdir()                             # (Settings › Storage also measures leftovers in the temp folder)
tempfile.tempdir = str(home / "tmp")
os.environ.update(FM1C_DEMO="1", FM1C_LANG="en", FM1C_NO_NET="1", HOME=str(home), TMPDIR=str(home / "tmp"))
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu")
if SLOOP3_RUN:                                      # the demo device is the SLOOP 3 stand-in of the tests
    os.environ["FM1C_DEMO_CMD"] = "\x1f".join([sys.executable, str(REPO / "tests" / "fake_sloop3.py")])

# synthetic names and ids for the dialogs of the bundled tools
UNIT, UNIT_NEW, UID = "FM-1 Earlier", "FM-1 Newer", "3141592653589793"


def _tone(path: Path, kind: str, seconds: float, f0: float):
    """a generated sound for the sample library (no recording of anything)"""
    r = random.Random(path.name)
    n = int(44100 * seconds)
    out = []
    for t in range(n):
        x = t / 44100
        env = math.exp(-x * (5 / seconds))
        if kind == "kick":
            v = math.sin(2 * math.pi * (f0 + 90 * math.exp(-x * 30)) * x) * env
        elif kind == "noise":
            v = (r.random() * 2 - 1) * env
        elif kind == "snare":
            v = (0.5 * math.sin(2 * math.pi * f0 * x) + 0.6 * (r.random() * 2 - 1)) * env
        else:
            v = (math.sin(2 * math.pi * f0 * x) + 0.4 * math.sin(4 * math.pi * f0 * x)) * env * 0.7
        out.append(int(26000 * max(-1.0, min(1.0, v))))
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wv:
        wv.setnchannels(1)
        wv.setsampwidth(2)
        wv.setframerate(44100)
        wv.writeframes(struct.pack("<%dh" % n, *out))


def _midi_file(path: Path):
    """a small melody as a standard MIDI file (format 0) for Compose › From MIDI File"""
    def vlq(v):
        b = [v & 0x7F]
        while v > 0x7F:
            v >>= 7
            b.insert(0, (v & 0x7F) | 0x80)
        return bytes(b)
    ev = bytearray(b"\x00\xff\x51\x03" + (500000).to_bytes(3, "big"))
    notes = [60, 63, 67, 70, 72, 70, 67, 63, 58, 62, 65, 69, 70, 69, 65, 62] * 2
    gap = 0
    for i, n in enumerate(notes):                    # sixteenth notes: 120 ticks apart, 110 long
        ev += vlq(gap) + bytes([0x90, n, 100 if i % 4 == 0 else 80]) + vlq(110) + bytes([0x80, n, 0])
        gap = 10
    ev += b"\x00\xff\x2f\x00"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"MThd" + struct.pack(">IHHH", 6, 0, 1, 480) + b"MTrk" + struct.pack(">I", len(ev)) + bytes(ev))


SAMPLES = home / "FM-1" / "Samples"
for _name, _kind, _sec, _f in (("Drums/kick 01", "kick", 0.45, 50), ("Drums/kick 02", "kick", 0.6, 42),
                               ("Drums/snare 01", "snare", 0.35, 190), ("Drums/snare 02", "snare", 0.5, 160),
                               ("Drums/hat closed", "noise", 0.12, 0), ("Drums/hat open", "noise", 0.7, 0),
                               ("Drums/clap", "noise", 0.3, 0), ("Drums/tom low", "kick", 0.5, 90),
                               ("Tones/bass C2", "tone", 1.2, 65.4), ("Tones/pluck C4", "tone", 0.8, 261.6),
                               ("Tones/bell A4", "tone", 2.0, 440.0), ("Tones/lead E4", "tone", 1.0, 329.6)):
    _tone(SAMPLES / (_name + ".wav"), _kind, _sec, _f)



def _loop(path: Path, parts: list[str], step: float = 0.25):
    """a two-bar drum loop from the generated drum sounds, one per eighth note at 120 BPM"""
    n = int(44100 * step)
    out = array.array("h")
    for name in parts:
        with wave.open(str(SAMPLES / "Drums" / (name + ".wav")), "rb") as wv:
            a = array.array("h", wv.readframes(wv.getnframes()))
        a = a[:n]
        a.extend([0] * (n - len(a)))
        out.extend(a)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wv:
        wv.setnchannels(1)
        wv.setsampwidth(2)
        wv.setframerate(44100)
        wv.writeframes(out.tobytes())


_loop(SAMPLES / "Loops" / "drum loop 120 bpm.wav",
      ["kick 01", "hat closed", "snare 01", "hat closed", "kick 01", "kick 01", "snare 01", "hat open"] * 2)
MIDI_FILE = home / "FM-1" / "Projekte" / "demo melody.mid"
_midi_file(MIDI_FILE)

import PyQt6.QtWebEngineWidgets  # noqa: F401,E402
from PyQt6.QtCore import QCoreApplication, QEventLoop, QPoint, QRect, Qt, QTimer  # noqa: E402
from PyQt6.QtGui import QColor, QFont, QPainter, QPixmap  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
app = QApplication(sys.argv[:1])
from fm1companion import config  # noqa: E402

s = config.load()
s.user_root = str(home / "FM-1")
s.check_on_start = False
config.save(s)
config.ensure_dirs()
from fm1companion.ui import theme  # noqa: E402

theme.apply(app, os.environ.get("SHOT_THEME", "light"))
from PyQt6.QtWidgets import (QDialog, QLabel, QLineEdit, QListWidget, QMessageBox,  # noqa: E402
                             QPlainTextEdit, QScrollArea, QTabWidget, QTextEdit, QTreeWidget, QTreeWidgetItemIterator)

CAPTURE: dict = {}                                  # {"name": file}: the next QMessageBox is saved instead of answered
SAVED: list[str] = []


def wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def until(cond, ms=20000):
    t = time.monotonic()
    while time.monotonic() - t < ms / 1000:
        try:
            if cond():
                return True
        except Exception:  # noqa: BLE001
            pass
        wait(100)
    return False


def tidy(root):
    """the throw-away home folder never appears in a picture: its path is shown as "~" """
    h = str(home)
    for lab in root.findChildren(QLabel):
        if h in lab.text():
            lab.setText(lab.text().replace(h, "~"))
    for ed in root.findChildren(QLineEdit):
        if h in ed.text():
            ed.setText(ed.text().replace(h, "~"))
    for ed in list(root.findChildren(QPlainTextEdit)) + list(root.findChildren(QTextEdit)):
        if h in ed.toPlainText():
            ed.setPlainText(ed.toPlainText().replace(h, "~"))
    for lst in root.findChildren(QListWidget):
        for i in range(lst.count()):
            if h in lst.item(i).text():
                lst.item(i).setText(lst.item(i).text().replace(h, "~"))
    for tree in root.findChildren(QTreeWidget):
        it = QTreeWidgetItemIterator(tree)
        while it.value():
            for c in range(tree.columnCount()):
                if h in it.value().text(c):
                    it.value().setText(c, it.value().text(c).replace(h, "~"))
            it += 1


def save(pm: QPixmap, name: str):
    path = OUT / name
    pm.save(str(path))
    try:                                            # smaller files: 256 colours are enough for a user interface
        from PIL import Image
        im = Image.open(path).convert("RGB")
        im.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).save(path, optimize=True)
    except Exception:  # noqa: BLE001 – Pillow missing: the plain PNG stays
        pass
    SAVED.append(name)
    print("saved", name, flush=True)


def framed(widget, title: str = "") -> QPixmap:
    """a dialog as a picture: offscreen there is no window manager, so its title bar is drawn here (plain, with the
    dialog's own window title)"""
    tidy(widget)
    body = widget.grab()
    bar, edge = 34, 1
    pm = QPixmap(body.width() + 2 * edge, body.height() + bar + 2 * edge)
    dark = app.palette().window().color().lightness() < 128
    pm.fill(QColor("#5c6066" if dark else "#9aa0a6"))
    p = QPainter(pm)
    p.fillRect(edge, edge, body.width(), bar, QColor("#2b2f33" if dark else "#e4e6ea"))
    f = QFont(app.font())
    f.setBold(True)
    p.setFont(f)
    p.setPen(QColor("#e8e8e8" if dark else "#24282c"))
    p.drawText(QRect(edge + 14, edge, body.width() - 60, bar), Qt.AlignmentFlag.AlignVCenter,
               title or widget.windowTitle())
    p.setPen(QColor("#a0a4a8" if dark else "#6a6e72"))
    p.drawText(QRect(edge + body.width() - 40, edge, 30, bar), Qt.AlignmentFlag.AlignCenter, "✕")
    p.drawPixmap(edge, edge + bar, body)
    p.end()
    return pm


def _msg_exec(self):
    if CAPTURE:
        name = CAPTURE.pop("name")
        width = CAPTURE.pop("width", 0)
        CAPTURE.clear()
        if width:                                   # (a QMessageBox takes its width from its label)
            for lab in self.findChildren(QLabel):
                if lab.objectName() == "qt_msgbox_label":
                    lab.setMinimumWidth(width)
                    lab.setWordWrap(True)
        self.show()
        wait(400)
        self.adjustSize()
        wait(200)
        save(framed(self), name)
        self.hide()
        self._clicked = None
        return 0
    for b in self.buttons():
        if self.buttonRole(b) in (QMessageBox.ButtonRole.AcceptRole, QMessageBox.ButtonRole.YesRole):
            self._clicked = b
            return 0
    self._clicked = None
    return 0


QMessageBox.exec = _msg_exec
QMessageBox.clickedButton = lambda self: getattr(self, "_clicked", None)


def _dlg_exec(self):
    from fm1companion.ui.common import JobDialog
    if isinstance(self, JobDialog):
        self.show()
        t0 = time.time()
        while time.time() - t0 < 120:
            app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
            if self.buttons.button(self.buttons.StandardButton.Close).isEnabled():
                break
        self.hide()
        return 1 if self.ok else 0
    return 1


QDialog.exec = _dlg_exec
from fm1companion.ui.main_window import MainWindow  # noqa: E402

w = MainWindow()
w.resize(1280, 800)
w.show()
ctx = w.ctx


def close_toasts():
    from fm1companion.ui.notify import Toast
    for t in w.findChildren(Toast):
        t.hide()
    lane = getattr(w, "toast_lane", None)
    if lane is not None:
        lane.hide()


def shot(name, wait_ms=900):
    wait(wait_ms)
    close_toasts()
    tidy(w)
    wait(200)
    save(w.grab(), name)


def dialog_shot(dlg, name, size=None, wait_ms=600, title=""):
    if size:
        dlg.resize(*size)
    dlg.show()
    wait(wait_ms)
    save(framed(dlg, title), name)
    dlg.hide()
    dlg.deleteLater()


def question_shot(name, title, text, ok, danger=False, width=620):
    """one of the app's own questions (ui/common.confirm) with the given text, saved instead of answered"""
    from fm1companion.ui.common import confirm
    CAPTURE.update(name=name, width=width)
    confirm(w, title, text, ok, danger=danger)


def tab(page, text):
    for tw in page.findChildren(QTabWidget):
        for i in range(tw.count()):
            if tw.tabText(i).replace("&", "").startswith(text):
                tw.setCurrentIndex(i)
                return True
    return False


def port(serial):
    return next((x for x in ctx.ports if x.serial == serial), None)


def use(serial, key=None):
    """make one of the demo's FM-1 the active device"""
    p = port(serial)
    if p is None or (ctx.port is p and ctx.connected):
        return p is not None
    ctx.connect_port(p)
    ok = until(lambda: ctx.connected and ctx.port is not None and ctx.port.serial == serial and
               (key is None or ctx.firmware_key == key), 20000)
    wait(1200)
    return ok


_BACKUP: list = []


def device_backup():
    """a backup of the demo FM-1 (made once): a file for Projects › View & Export and the Backups page"""
    if not _BACKUP:
        use("DEMO-1")
        w._backup_now()
        wait(1500)
        folder = Path(ctx.settings.backups_dir)
        files = sorted(folder.rglob("*.json"), key=lambda p: p.stat().st_mtime)
        files = [f for f in files if "Flash" not in f.parts]
        _BACKUP.append(files[-1] if files else None)
    return _BACKUP[0]


# ------------------------------------------------------------------------------------------------ the sections

def sec_overview():
    use("DEMO-1")
    w.go("overview")
    shot("overview.png", 1500)


def sec_devices():
    for serial in ("DEMO-2", "DEMO-3", "DEMO-1"):          # every demo FM-1 once: three device profiles
        use(serial)
    w.go("devices")
    shot("devices.png", 1500)


def sec_menu():
    """the Device menu, opened: the menu is its own window, so it is drawn onto the window's picture where it opens"""
    use("DEMO-1")
    w.go("overview")
    wait(800)
    close_toasts()
    bar = w.menuBar()
    act = next(a for a in bar.actions() if a.text().replace("&", "") == "Device")
    menu = act.menu()
    geo = bar.actionGeometry(act)
    pos = bar.mapTo(w, geo.bottomLeft())
    menu.popup(w.mapToGlobal(pos))
    wait(500)
    base, top = w.grab(), menu.grab()
    p = QPainter(base)
    p.fillRect(QRect(bar.mapTo(w, geo.topLeft()), geo.size()), QColor(0, 0, 0, 24))
    p.fillRect(QRect(pos + QPoint(2, 3), top.size()), QColor(0, 0, 0, 40))
    p.drawPixmap(pos, top)
    p.setPen(QColor("#9aa0a6"))
    p.drawRect(QRect(pos, top.size()).adjusted(0, 0, -1, -1))
    p.end()
    menu.hide()
    save(base, "device-menu.png")
    pal = w.open_palette()                           # Ctrl+K
    wait(400)
    pal.input.setText("backup")
    shot("command-search.png", 500)
    pal.hide()
    pal.deleteLater()


def sec_editor():
    use("DEMO-1")
    w.go("editor")
    until(lambda: w.editor.bridge.connected, 20000)
    shot("editor.png", 9000)


def sec_presets():
    use("DEMO-1")
    w.go("presets")
    page = w.page("presets")
    for name in ("backup_all", "_backup_all"):
        fn = getattr(page, name, None)
        if callable(fn):
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                print("presets backup:", e)
            break
    shot("presets.png", 2500)


def sec_samples():
    use("DEMO-1")
    w.go("samples")
    sp = w.samples
    sp.tabs.setCurrentIndex(0)
    sp.scan()
    until(lambda: sp.table.rowCount() >= 8 if hasattr(sp.table, "rowCount") else sp.table.model().rowCount() >= 8,
          15000)
    wait(800)
    t = sp.table
    drums = [t.topLevelItem(i) for i in range(t.topLevelItemCount())
             if any(k in t.topLevelItem(i).text(1) for k in ("kick", "snare", "hat", "clap", "tom"))]
    t.clearSelection()
    for it in drums:
        it.setSelected(True)
    sp._add_to_kit()                                  # a drum kit from the generated drum sounds
    wait(2500)
    t.clearSelection()
    loop = next((t.topLevelItem(i) for i in range(t.topLevelItemCount()) if "loop" in t.topLevelItem(i).text(1)),
                None)
    if loop is not None:
        t.setCurrentItem(loop)
    shot("samples.png", 2500)
    # the slicer with one of the generated sounds
    try:
        from fm1companion.ui.slicer_dialog import SlicerDialog
        d = SlicerDialog(str(SAMPLES / "Loops" / "drum loop 120 bpm.wav"), sp)
        dialog_shot(d, "sample-slicer.png", size=(980, 620), wait_ms=2500)
    except Exception as e:  # noqa: BLE001
        print("slicer:", e)


def sec_projects():
    use("DEMO-1")
    w.go("projects")
    pr = w.page("projects")
    w.projects.read_slots()
    wait(2500)
    try:
        w.projects._export_all()
        wait(3000)
        w.projects.refresh_library()
    except Exception as e:  # noqa: BLE001
        print("fetch all:", e)
    shot("projects.png", 1500)
    pv = w.page("project_view")
    bfile = device_backup()
    if pv is not None and bfile:
        tab(pr, "View")
        pv.open_backup(str(bfile))
        w.go("project_view")
    shot("project-view.png", 2000)


def sec_compose():
    use("DEMO-1")
    w.go("compose")
    page = w.page("compose")
    try:
        page.generate()
    except Exception as e:  # noqa: BLE001
        print("generate:", e)
    shot("compose.png", 1200)
    w.go("workshop")
    ws = w.workshop
    ws._open(str(MIDI_FILE))
    until(lambda: "demo melody" in " ".join(x.text() for x in ws.findChildren(QLabel)), 8000)
    shot("compose-midi-file.png", 2000)


def sec_recording():
    use("DEMO-1")
    w.go("recorder")
    rp = w.recorder
    wait(1500)
    try:
        rp.name.setText("song idea 3")
        rp._toggle()
        until(lambda: rp.rec is not None and rp.rec.running, 8000)
        shot("recording.png", 3500)
        rp._toggle()
        wait(2500)
    except Exception as e:  # noqa: BLE001
        print("recording:", e)
        shot("recording.png")


def flash_image(boot: bytes | None = None, seed="fm1c-fake-flash") -> bytes:
    """the stand-ins' 1 MiB flash image (random bytes with a valid boot area), optionally with m-bootloader in it"""
    import fake_mflasher
    img = bytearray(fake_mflasher.synthetic_image(seed))
    if boot is not None:
        img[:0x4000] = fake_mflasher.short_boot_area(bytes(img), boot)
    return bytes(img)


def mboot_file() -> bytes:
    return (REPO / "fm1companion" / "tools" / "bundled" / "m-bootloader" / "fm1-uboot.boot").read_bytes()


def write_flash_backups(folder: Path, device: str, profile_id: str = "", newest_first=None):
    """three whole-flash backups of a made-up unit: one from before m-bootloader, two with it"""
    import hashlib
    from fm1companion import flashdump
    from fm1companion.flash import mboot
    from fm1companion.tools import bootarea
    plain, with_mb = flash_image(), flash_image(mboot_file())
    # the stand-in's boot area plays the part of M-VAVE's original one (as in the tests)
    bootarea.V15_BOOT_AREA_SHA256 = hashlib.sha256(plain[:0x4000]).hexdigest()
    out = []
    for when, img, fw, mb in ((dt.datetime(2026, 11, 14, 18, 5), plain, "Melodee 1.6.1", ""),
                              (dt.datetime(2026, 11, 14, 18, 22), with_mb, "Melodee 1.6.1", "m-bootloader 2.4-395e6cc"),
                              (dt.datetime(2026, 11, 20, 9, 40), with_mb, "SLOOP 3 3.4.0", "m-bootloader 2.4-395e6cc")):
        local = when.astimezone()
        p = flashdump.write_backup(
            folder, mboot.backup_name(device, local.timetuple()), img, device=device,
            read_through="M-Flasher 0.12.1 (USB download mode), read twice and compared", jedec=0x856014,
            unit_id=UID, chip_key=0x1234, capacity=len(img), profile=profile_id,
            firmware={"app": fw, "bootloader": mb}, double_read=True,
            extra={"created": local.strftime("%Y-%m-%dT%H:%M:%S%z")})
        os.utime(p, (local.timestamp(), local.timestamp()))
        out.append(p)
    return out


def sec_backups():
    bfile = device_backup()
    use("DEMO-1")
    try:                                              # a second, pinned backup with a note
        w._backup_now()
        wait(1500)
    except Exception as e:  # noqa: BLE001
        print("backup 2:", e)
    prof = ctx.profile
    write_flash_backups(Path(ctx.settings.backups_dir) / prof.folder_name / "Flash", prof.name, prof.id)
    w.go("backups")
    bp = w.page("backups")
    w.backups.refresh()
    until(lambda: w.backups.flash_tree.topLevelItemCount() == 3 and
          "✓" in w.backups.flash_tree.topLevelItem(0).text(4), 15000)
    for c in range(5):
        w.backups.flash_tree.resizeColumnToContents(c)
    shot("backups.png", 1500)
    tab(bp, "Browse")
    bb = w.page("backup_browser")
    try:
        if bfile and hasattr(bb, "open_backup"):
            bb.open_backup(str(bfile))
    except Exception as e:  # noqa: BLE001
        print("browse:", e)
    shot("backups-browse.png", 1500)
    tab(bp, "List")


def sec_firmware():
    use("DEMO-1")
    w.go("discover")
    dp = w.page("discover")
    shot("firmware-catalog.png", 2500)
    try:
        dp.open_key("skiff")
        shot("firmware-detail.png", 2500)
        dp.stack.setCurrentIndex(0)
    except Exception as e:  # noqa: BLE001
        print("detail:", e)
    w.go("firmware")
    fp = w.firmware
    fp.load_releases(force=True)
    until(lambda: fp.tree.topLevelItemCount() > 1, 15000)
    shot("firmware-install.png", 1500)
    # the installation plan of the route through download mode: SLOOP 3 with m-bootloader -> Skiff 2.7
    from fm1companion import devices, firmware
    from fm1companion.flash import planner
    from fm1companion.tools import bootarea as BA
    from fm1companion.ui import firmware_page
    prof = devices.Profile(id="e1e1e1e1e1", name=UNIT, firmware="sloop3",
                           firmware_history=[{"time": 1.0, "firmware": "sloop3"}])
    BA.note(prof, BA.MBOOT, "midi-mode", bootloader="m-bootloader 2.4-395e6cc")
    text = BA.describe(BA.current(prof))
    st = planner.DeviceState(identity="", felucca=False, firmware_key="sloop3", boot_area=BA.MBOOT,
                             boot_area_text=text, revision="old")
    plan = planner.plan_install(st, planner.Target.from_spec(firmware.spec("skiff"), tag="v2.7"))
    dlg = firmware_page.PlanDialog(w, lambda via, b=True: plan, f"Skiff v2.7 on {UNIT}", True)
    dialog_shot(dlg, "install-plan.png", size=(820, 680))
    # the simulator: the bundled Melodee build
    w.go("simulator")
    sim = w.page("simulator")
    try:
        from fm1companion import sims
        pick = next((x for x in sims.bundled() if "melodee" in x.key.lower()), None) or sims.bundled()[0]
        for i in range(sim.list.count()):
            if sim.list.item(i).data(Qt.ItemDataRole.UserRole) == pick.key:
                sim.list.setCurrentRow(i)
        sim.start(pick)
        until(lambda: "Loading" not in sim.state.text(), 30000)
        wait(3000)
        sim.js("var b = document.getElementById('power'); if (b) b.click();", lambda _v: None)
        shot("simulator.png", 6000)
        sim.stop()
    except Exception as e:  # noqa: BLE001
        print("simulator:", e)


def cropped(pm: QPixmap) -> QPixmap:
    """a picture without the plain margin around its content (the margin's colour: the top left pixel)"""
    im = pm.toImage()
    bg = im.pixel(0, 0)
    xs = [x for x in range(im.width()) if any(im.pixel(x, y) != bg for y in range(0, im.height(), 5))]
    ys = [y for y in range(im.height()) if any(im.pixel(x, y) != bg for x in range(0, im.width(), 5))]
    if not xs or not ys:
        return pm
    return pm.copy(QRect(QPoint(xs[0], ys[0]), QPoint(xs[-1], ys[-1])))


def sec_studio():
    use("DEMO-1")
    w.go("studio")
    page = w.page("studio")
    svc = w.studio
    until(lambda: svc.running, 40000)
    until(lambda: page.view.page() is svc.page, 10000)
    wait(1500)
    try:
        page.fetch_from_device()
    except Exception as e:  # noqa: BLE001
        print("fetch failed", e)
    wait(1500)
    shot("studio.png", 1500)
    panels = [(f"{svc.firmware_name} {svc.version}".strip(), page.view.grab())]
    try:
        from fm1companion.ui.pages.studio import ExportDialog
        name, loop = page._running_music()
        dialog_shot(ExportDialog(page, svc.cfg, name, loop, svc.firmware), "studio-export.png")
    except Exception as e:  # noqa: BLE001
        print("export dialog:", e)
    # another firmware: the demo's SLOOP FM-1 in its SLOOP twin
    if use("DEMO-2", "sloop"):
        try:
            page.refresh()
            wait(800)
            page.fetch_from_device()
            until(lambda: svc.running and svc.firmware == "sloop", 30000)
            wait(2000)
            shot("studio-sloop.png", 1500)
            panels.append((f"{svc.firmware_name} {svc.version}".strip(), page.view.grab()))
        except Exception as e:  # noqa: BLE001
            print("sloop studio", e)
    use("DEMO-1")
    until(lambda: ctx.firmware_key != "sloop", 20000)
    # twins of firmwares that are not on the demo's FM-1, started empty: only their panels, side by side
    for fw in ("skiff", "karma", "jiant", "x0x"):
        try:
            svc.source = {}
            svc.new_empty("", fw)
            until(lambda: svc.running and svc.firmware == fw, 30000)
            page.refresh()
            wait(4000)
            panels.append((f"{svc.firmware_name} {svc.version}".strip(), page.view.grab()))
        except Exception as e:  # noqa: BLE001
            print("twin", fw, e)
    cols, gap, cap = 3, 16, 30
    panels = [(label, cropped(pm).scaledToWidth((1280 - (cols + 1) * gap) // cols,
                                                Qt.TransformationMode.SmoothTransformation))
              for label, pm in panels]
    pw, ph = panels[0][1].width(), max(pm.height() for _l, pm in panels)
    rows = -(-len(panels) // cols)
    sheet = QPixmap(cols * pw + (cols + 1) * gap, rows * (ph + cap) + (rows + 1) * gap)
    sheet.fill(app.palette().window().color())
    p = QPainter(sheet)
    f = QFont(app.font())
    f.setBold(True)
    p.setFont(f)
    p.setPen(app.palette().windowText().color())
    for i, (label, pm) in enumerate(panels):
        x, y = gap + (i % cols) * (pw + gap), gap + (i // cols) * (ph + cap + gap)
        p.drawPixmap(x, y, pm)
        p.drawText(QRect(x, y + ph, pw, cap), Qt.AlignmentFlag.AlignCenter, label)
    p.end()
    save(sheet, "studio-twins.png")


def sec_midi():
    use("DEMO-1")
    w.go("midi")
    mp = w.page("midi")
    tab(mp, "Keyboard")
    wait(600)
    kb = mp.keyboard
    try:
        for n in (60, 64, 67, 72, 67, 64):
            kb.press(n)
            wait(160)
            kb.release(n)
            wait(60)
        kb.press(60)
        kb.press(64)
        kb.press(67)
        for piano in kb.findChildren(type(kb).__mro__[1]):
            if hasattr(piano, "down"):
                piano.update()
    except Exception as e:  # noqa: BLE001
        print("keyboard:", e)
    shot("midi-keyboard.png", 500)
    try:
        for n in (60, 64, 67):
            kb.release(n)
    except Exception:  # noqa: BLE001
        pass
    tab(mp, "Monitor")
    shot("midi-monitor.png", 1200)


def scroll_to(widget):
    p = widget.parent()
    while p is not None and not isinstance(p, QScrollArea):
        p = p.parent()
    if p is not None:
        p.verticalScrollBar().setValue(max(0, widget.mapTo(p.widget(), QPoint(0, 0)).y() - 8))
    return p


def sec_recovery():
    use("DEMO-1")
    w.go("recovery")
    rec = w.page("recovery")
    area = scroll_to(rec.tools)
    if area is not None:
        area.verticalScrollBar().setValue(0)
    shot("recovery.png", 1500)
    rec.tools.refresh()
    scroll_to(rec.tools)
    shot("recovery-tools.png", 1200)
    if area is not None:
        area.verticalScrollBar().setValue(0)


def sec_tools():
    """the questions of the bundled tools, each with the text the app builds for a plan – the plans are made up here
    (no M-Flasher runs, no FM-1 is there)"""
    from fm1companion import devices, firmware, hardware
    from fm1companion.flash import mboot
    from fm1companion.tools import bootarea, flashjob as FJ, mbinstall
    from fm1companion.ui import flash_tools, sloop3_ui
    from fm1companion.ui.device import unit_confirm_text
    from fm1companion.ui import hw_revision
    tmp = home / "tools-demo"
    prof = devices.Profile(id="e1e1e1e1e1", name=UNIT, firmware="sloop3", unit_id=UID,
                           firmware_history=[{"time": 1.0, "firmware": "sloop3"}])
    bootarea.note(prof, bootarea.MBOOT, "midi-mode", bootloader="m-bootloader 2.4-395e6cc")
    files = write_flash_backups(tmp / prof.folder_name / "Flash", UNIT, prof.id)
    now = Path(mboot.backup_name(UNIT, dt.datetime(2026, 11, 21, 10, 12).timetuple()))
    # write the whole flash back
    plan = FJ.RestorePlan(image=files[0], side={}, unit_name=UNIT, unit_id=UID, flash_id=0x856014, revision="old",
                          image_date="2026-11-14T18:05:00+0100", image_firmware="Melodee 1.6.1",
                          runs=[(0, 0x4000), (0x4000, 0x7C000)], sectors=124, boot_area=True, backup=now)
    question_shot("dlg-flash-write-back.png", "Write the whole flash back?", flash_tools.plan_text(plan),
                  "Write back", danger=True)
    # install m-bootloader
    bplan = FJ.BootPlan(op="install", unit_name=UNIT, unit_id=UID, flash_id=0x856014, revision="old",
                        version="2.4-395e6cc", current="stock bootloader", firmware="Melodee 1.6.1", backup=now)
    question_shot("dlg-install-mbootloader.png", "Install m-bootloader?", flash_tools.boot_plan_text(bplan),
                  "Write the boot area", danger=True)
    # a firmware through download mode
    label = firmware.display_label(firmware.spec("skiff"), "v2.7")
    fplan = FJ.FirmwarePlan(unit_name=UNIT, unit_id=UID, flash_id=0x856014, revision="old", identity="FM-1_727",
                            name=label, end=0x7A000, runs=[(0x4000, 0x7A000)], sectors=118,
                            current="m-bootloader 2.4-395e6cc", firmware="SLOOP 3", mflasher_firmware="SLOOP 3",
                            backup=now)
    question_shot("dlg-firmware-download-mode.png", f"Install {label} through download mode?",
                  flash_tools.firmware_plan_text(fplan), "Install", danger=True)
    # SLOOP 3 over m-bootloader's MIDI MODE
    area = (REPO / "fm1companion" / "tools" / "bundled" / "sloop3" / "sloop3-3.4.0.area").read_bytes()
    import hashlib
    size = 0x4000 + -(-len(area) // 0x1000) * 0x1000
    iplan = mbinstall.InstallPlan(unit_name=UNIT, unit_id=UID, flash_id=0x856014, revision="old",
                                  mboot_version="2.4-395e6cc", version="3.4.0",
                                  area_sha256=hashlib.sha256(area).hexdigest(), size=size,
                                  runs=[(0x4000, size)], sectors=(size - 0x4000) // 0x1000, firmware="Melodee 1.6.1",
                                  backup=now)
    question_shot("dlg-install-sloop3.png", "Install SLOOP 3 (MIDI MODE)?", sloop3_ui.install_plan_text(iplan),
                  "Install SLOOP 3", danger=True, width=700)
    # "Back to …"
    fake = NS(settings=NS(backups_dir=str(tmp)), profile=prof, mboot=None, is_virtual=False, sloop3=object())
    dlg = flash_tools.way_back_dialog(w, flash_tools.way_back_options(fake))
    lst = dlg.findChild(QListWidget, "wayBackBackups")
    if lst is not None:
        lst.setMaximumHeight(96)
        lst.setCurrentRow(lst.currentRow())
        dlg.radios[0][0].setChecked(True)
    dialog_shot(dlg, "dlg-back-to.png", size=(700, 640))
    hardware.set_revision(prof, "old", "user")
    # "Which FM-1 is this?" – before a write to a unit that reports no unit ID
    question_shot("dlg-which-fm1.png", "Which FM-1 is this?", unit_confirm_text(prof, "answer"),
                  f"Yes, it is “{UNIT}”", danger=True, width=560)
    # … and the question about the logo on the front (earlier or newer unit)
    CAPTURE.update(name="dlg-logo-question.png")
    hw_revision.choose(w, "My FM-1")


def sec_settings():
    use("DEMO-1")
    from fm1companion.ui.settings_dialog import SettingsDialog
    d = SettingsDialog(ctx, w)
    dialog_shot(d, "settings.png", size=(980, 720), wait_ms=800)
    d = SettingsDialog(ctx, w, tab="storage")
    d.resize(980, 720)
    d.show()
    for tw in d.findChildren(QTabWidget):
        if tw.indexOf(d.storage) >= 0:
            tw.setCurrentIndex(tw.indexOf(d.storage))
    until(lambda: d.storage.cats, 60000)             # the sizes are measured in the background
    dialog_shot(d, "storage.png", wait_ms=2500)


def sec_help():
    use("DEMO-1")
    w.go("help")
    shot("help.png", 2500)
    try:
        d = w.open_syscheck()
        until(lambda: "Checking" not in d.summary.text(), 30000)
        dialog_shot(d, "system-check.png", wait_ms=800)
    except Exception as e:  # noqa: BLE001
        print("syscheck:", e)


def sec_sloop3():
    """second run only (--sloop3): SLOOP 3's own bundled editor against the tests' stand-in"""
    until(lambda: ctx.connected and getattr(ctx, "sloop3", None) is not None, 30000)
    wait(1500)
    w.go("editor")
    until(lambda: "editor.html" in w.editor.view.url().toString(), 15000)
    until(lambda: w.editor.bridge.connected, 20000)
    shot("sloop3-editor.png", 6000)


SECTIONS = [("overview", sec_overview), ("devices", sec_devices), ("menu", sec_menu), ("editor", sec_editor),
            ("presets", sec_presets), ("samples", sec_samples), ("projects", sec_projects), ("compose", sec_compose),
            ("recording", sec_recording), ("backups", sec_backups), ("firmware", sec_firmware), ("studio", sec_studio),
            ("midi", sec_midi), ("recovery", sec_recovery), ("tools", sec_tools), ("settings", sec_settings),
            ("help", sec_help)]
if SLOOP3_RUN:
    SECTIONS = [("sloop3", sec_sloop3)]
else:
    until(lambda: ctx.connected, 30000)
    wait(2500)
failed = []
for key, fn in SECTIONS:
    if ONLY and key not in ONLY:
        continue
    print("==", key, flush=True)
    try:
        fn()
    except Exception:  # noqa: BLE001
        failed.append(key)
        traceback.print_exc()
w.close()
wait(500)
if not SLOOP3_RUN and (not ONLY or "sloop3" in ONLY):
    sys.stdout.flush()
    subprocess.run([sys.executable, __file__, str(REPO), str(OUT), "--sloop3"], check=False)
print("failed sections:", failed or "none", flush=True)
shutil.rmtree(home, ignore_errors=True)            # the throw-away home folder
os._exit(1 if failed else 0)
