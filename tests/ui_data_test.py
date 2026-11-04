# SPDX-License-Identifier: GPL-3.0-only
"""Data area in demo mode: auto backup on connecting, backup browser (presets, projects, comparison,
partial restore), Devices page (comparison, copy Melodee→SLOOP refused, Melodee→Melodee allowed).

  QT_QPA_PLATFORM=offscreen python3 tests/ui_data_test.py
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import struct
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("FM1C_LANG", "de")      # these checks expect the German user interface
home = Path(tempfile.mkdtemp(prefix="fm1c-data-"))
for k, sub in (("XDG_CONFIG_HOME", ".config"), ("XDG_DATA_HOME", ".local/share"), ("XDG_CACHE_HOME", ".cache")):
    os.environ[k] = str(home / sub)
os.environ["FM1C_DEMO"] = "1"
os.environ.pop("FM1C_DEMO_CMD", None)
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu")

errors: list[str] = []
sys.excepthook = lambda t, v, tb: errors.append("".join(traceback.format_exception(t, v, tb)))

import PyQt6.QtWebEngineWidgets  # noqa: E402,F401
from PyQt6.QtCore import QCoreApplication, QEventLoop, Qt, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog, QInputDialog, QMessageBox  # noqa: E402

QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
app = QApplication(sys.argv)

from fm1companion import config  # noqa: E402

s = config.load()
s.user_root = str(home / "FM-1")
s.check_on_start = False
config.module("daten", {})["delay_s"] = 1
config.save(s)
config.ensure_dirs()

log_boxes: list[str] = []
decline_next = []


def fake_msg_exec(self):
    log_boxes.append(self.text())
    if decline_next:
        decline_next.pop()
        self._clicked = None
        return 0
    for b in self.buttons():
        if self.buttonRole(b) in (QMessageBox.ButtonRole.AcceptRole, QMessageBox.ButtonRole.YesRole):
            self._clicked = b
            return 0
    self._clicked = None
    return 0


QMessageBox.exec = fake_msg_exec
QMessageBox.clickedButton = lambda self: getattr(self, "_clicked", None)
QInputDialog.getText = staticmethod(lambda *a, **k: (k.get("text", "x"), True))
QInputDialog.getInt = staticmethod(lambda *a, **k: (a[3] if len(a) > 3 else 1, True))


def dialog_exec(self):
    from fm1companion.ui.common import JobDialog
    if isinstance(self, JobDialog):
        self.show()
        t0 = time.time()
        while time.time() - t0 < 60:
            app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
            if self.buttons.button(self.buttons.StandardButton.Close).isEnabled():
                break
        self.hide()
        self.log_text = self.logv.toPlainText()
        return 1 if self.ok else 0
    return 1


QDialog.exec = dialog_exec

from fm1companion import autobackup as AB, backup, backup_inspect as BI, projects  # noqa: E402
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


def rec(name, engine=3, fill=64):
    r = bytearray(206)
    r[0], r[1], r[2], r[3] = 0xA5, 4, engine, 91
    r[4:4 + len(name)] = name.encode()
    r[16:16 + 91] = bytes([fill]) * 91
    return bytes(r)


def bank(records):
    out = bytearray(struct.pack("<IHH", BI.UPB2, 206, 18))
    for i in range(18):
        out += records.get(i, bytes(206))
    return bytes(out)


def device_write(oid, data):
    d = w.ctx.job(w, "Test schreiben", lambda wk, link: backup.write_object(link, oid, data))
    return d.ok


def device_read(oid):
    def fn(wk, link):
        o = next(x for x in backup.list_objects(link) if x.id == oid)
        return backup.read_object(link, o)
    d = w.ctx.job(w, "Test lesen", fn)
    return d.result_value


def autos(folder):
    return [m for m in AB.list_backups(folder, recursive=False) if m.kind == "auto"]


def flow():
    ctx = w.ctx
    check(until(lambda: ctx.connected and ctx.firmware_key == "melodee"), "demo Melodee connected")
    folder = ctx.device_folder(ctx.settings.backups_dir)
    check(until(lambda: len(autos(folder)) == 1, 15000), "automatic backup on connecting")
    check(until(lambda: "gespeichert" in AB.STATUS.text), f"Status: {AB.STATUS.text}")
    wait(300)
    check(len(autos(folder)) == 1 and not w.backups.auto.run(), "second connect: backup current → none")
    m = autos(folder)[0]
    check(m.fw_key == "melodee" and "Melodee" in m.firmware, "backup labelled with the firmware")
    # never during another device access
    ctx.link.hold(settle=False)
    check(not w.backups.auto.run(force=True) and "wartet" in AB.STATUS.text, "waits while the device is busy")
    ctx.link.release()
    w.backups.auto._retries = 99

    # ---- presets onto the demo device, make a backup
    check(device_write(6, bank({0: rec("BASS ONE"), 4: rec("SID LEAD"), 9: rec("FM KEYS", 12)})), "presets written")
    from fm1companion.ui.backups_page import make_backup
    p = make_backup(ctx, w, "mit Presets")
    check(p is not None, "backup with presets")
    w.go("backup_browser")
    br = w.page("backup_browser")
    check(br is not None, "backup browser present")
    br.open_path(p)
    check(br.rep and len(br.rep.used_presets) == 3, f"browser: 3 presets ({len(br.rep.used_presets) if br.rep else '-'})")
    check(br.ptable.topLevelItemCount() == 3 and br.ptable.topLevelItem(0).text(0) == "U001", "preset table")
    br.p_search.setText("lead")
    check(br.ptable.topLevelItemCount() == 1 and br.ptable.topLevelItem(0).text(1) == "SID LEAD", "preset search")
    br.p_search.setText("")
    titles = []
    for i in range(br.content.topLevelItemCount()):
        g = br.content.topLevelItem(i)
        titles += [g.child(j).text(0) for j in range(g.childCount())]
    check("Projekt 7" in titles and "Preset-Bank U001–U018 (allgemein)" in titles, "content tree: projects and banks")
    css = app.styleSheet()                    # draw the tick in the tree only once (otherwise double boxes with Breeze/Qt 6.8)
    item_rule = css.split("QTreeView::item, QListView::item, QTableView::item {", 1)[-1].split("}", 1)[0]
    check("QTreeView::indicator" not in css or "background" in item_rule,
          "stylesheet: list items with a background → ticks drawn by the stylesheet only")
    check(br.timeline.count() >= 2, f"timeline ({br.timeline.count()})")

    # ---- change the device, compare with live
    check(device_write(6, bank({0: rec("BASS TWO"), 4: rec("SID LEAD")})), "preset renamed, one deleted")
    check(device_write(68, b"P" * 1200), "project 9 used")
    br._fill_timeline()
    br.cmp_with.setCurrentIndex(br.cmp_with.findData("__live__"))
    br.compare()
    summ = br.cmp_sum.text()
    check("1 Preset geändert" in summ and "1 Preset gelöscht" in summ and "Projekt 9 neu" in summ, f"Diff: {summ}")
    check(br.cmp_tree.topLevelItemCount() == 2, f"only changes ({br.cmp_tree.topLevelItemCount()})")

    # ---- restore only the preset bank
    br._select_changed()
    check(set(br.selected_ids()) == {6, 68}, f"changes selected {br.selected_ids()}")
    br.select_ids([6])
    n_before = len(list(folder.glob("*.json")))
    log_boxes.clear()
    ok = br.restore_selected()
    check(ok, "selected ones restored")
    check(any("Preset-Bank U001–U018" in b and "BASS TWO" in b for b in log_boxes), "confirmation names bank and preset")
    check(len(list(folder.glob("*.json"))) == n_before + 1, "safety backup created")
    check(BI.decode_up_bank(device_read(6), 1, 6)[9].name == "FM KEYS", "bank as in the backup again")
    check(len(device_read(68)) == 1200, "project 9 untouched")
    decline_next.append(1)
    br.select_ids([68])
    check(not br.restore_selected() and len(device_read(68)) == 1200, "cancelling writes nothing")

    # ---- label, pinning
    AB.set_meta(p, labels=["Live-Set Oktober"], pinned=True)
    w.backups.refresh()
    check(any("#Live-Set Oktober" in w.backups.tree.topLevelItem(i).text(6)
              for i in range(w.backups.tree.topLevelItemCount())), "label in the backup list")

    # ---- acceptance p2: note of the same length visible, damaged file, broken zip, export errors
    AB.set_meta(p, note="abc")
    w.backups.refresh()
    w.backups._set_meta(p, note="abd")
    check(any("abd" in w.backups.tree.topLevelItem(i).text(6) for i in range(w.backups.tree.topLevelItemCount())),
          "note of the same length visible at once (index)")
    broken = folder / "FM-1_A_melodee_2020-01-01_00-00-00.json"
    broken.write_text(p.read_text()[:2000])
    w.backups.refresh()
    rows = [w.backups.tree.topLevelItem(i) for i in range(w.backups.tree.topLevelItemCount())]
    bad = next((r for r in rows if r.data(0, Qt.ItemDataRole.UserRole) == str(broken)), None)
    check(bad is not None and bad.text(3) == "beschädigt" and "beschädigt" in w.backups.info.text(),
          "truncated file is shown as damaged")
    w.backups.tree.setCurrentItem(bad)
    n_boxes = len(log_boxes)
    w.backups._restore()
    check(len(log_boxes) == n_boxes + 1 and "lässt sich nicht" in log_boxes[-1], "damaged: restoring refused, explained")
    w.backups._browse()
    check(br.rep is not None and br.rep.path != broken, "damaged: the browser does not open it")
    broken.unlink()
    from PyQt6.QtWidgets import QFileDialog
    badzip = home / "kaputt.zip"
    badzip.write_bytes(b"kein zip")
    QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (str(badzip), ""))
    n_boxes = len(log_boxes)
    br._zip_import()
    check(len(log_boxes) == n_boxes + 1 and "keine lesbare Zip-Datei" in log_boxes[-1], f"kaputtes Zip: {log_boxes[-1][:60]}")
    blocker = home / "datei.txt"
    blocker.write_text("x")
    QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: str(blocker / "unter"))
    br.open_path(p)
    br.select_ids([6])
    n_boxes = len(log_boxes)
    br._export()
    check(len(log_boxes) == n_boxes + 1 and "Nicht exportiert" not in log_boxes[-1] and "Errno" not in log_boxes[-1]
          and ("Ordner" in log_boxes[-1] or "Datei" in log_boxes[-1]), f"export into an impossible folder: {log_boxes[-1][:70]}")
    # comparison Melodee ↔ Melodee with "Changed parts" without a comparison: a hint instead of nothing
    n_boxes = len(log_boxes)
    br.other = None
    br.last_diff = None
    br._select_changed()
    check(len(log_boxes) == n_boxes + 1 and "Vergleich" in log_boxes[-1], "\"Changed parts\" without a comparison explains itself")

    # ---- auto backup with manipulated timestamps
    newest = AB.list_backups(folder, recursive=False)[0]
    f_json = json.loads(newest.path.read_text())
    old_created = f_json["created"]
    f_json["created"] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=3)).isoformat().replace("+00:00", "Z")
    newest.path.write_text(json.dumps(f_json))
    for m_ in AB.list_backups(folder, recursive=False):
        if m_.path != newest.path:
            j = json.loads(m_.path.read_text())
            j["created"] = f_json["created"]
            m_.path.write_text(json.dumps(j))
    check(AB.needs_backup(folder, 1) and not AB.needs_backup(folder, 7), "needs_backup counts with the date in the backup")
    n_auto = len(autos(folder))
    check(w.backups.auto.run(), "auto backup starts when the last one is too old")
    check(until(lambda: not w.backups.auto.running and len(autos(folder)) == n_auto + 1, 20000), "auto backup created")
    check(not w.backups.auto.run(), "freshly backed up → no further one")
    # retention: manual and pinned ones stay, even with keep=1
    AB.set_meta(p, pinned=True)
    pinned_auto = autos(folder)[0].path
    AB.set_meta(pinned_auto, pinned=True)
    from fm1companion.ui.backups_page import prune
    prune(folder, 1)
    names = {x.name for x in folder.glob("*.json")}
    check(p.name in names and pinned_auto.name in names, "retention deletes neither manual nor pinned ones")
    AB.set_meta(p, pinned=False)
    # mirror folder not mounted
    AB.settings()["mirror"] = str(home / "nas-nicht-da")
    from fm1companion.ui.backups_page import _mirror_quiet
    _mirror_quiet(ctx, p)
    check("eingehängt" in AB.STATUS.mirror and "nicht möglich" in AB.STATUS.mirror, f"mirror missing: {AB.STATUS.mirror}")
    AB.settings()["mirror"] = ""
    AB.STATUS.mirror = ""

    # ---- projects: clear a slot with a copy (undo), foreign .fm1proj refused
    w.go("projects")
    pp = w.projects
    check(until(lambda: pp.slots and not pp.reading, 10000), "project slots read automatically")
    n_read = [0]
    orig_read = pp.read_slots
    pp.read_slots = lambda *a, **k: (n_read.__setitem__(0, n_read[0] + 1), orig_read(*a, **k))
    w.go("backups")
    w.go("projects")
    check(n_read[0] == 0 and pp.slots, "a tab switch does not read the slots again")
    pp.read_slots = orig_read
    slot1 = device_read(2)
    for i in range(pp.dev_tree.topLevelItemCount()):
        if pp.dev_tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) == 1:
            pp.dev_tree.setCurrentItem(pp.dev_tree.topLevelItem(i))
    n_boxes = len(log_boxes)
    pp._erase()
    check("Platz" in log_boxes[n_boxes] and "Kopie" in log_boxes[n_boxes], "clear: the confirmation names slot and copy")
    lib = list(ctx.settings.projects_dir.glob("*vor dem Leeren*.fm1proj"))
    check(len(lib) == 1 and device_read(2) == b"" and projects.load_project(lib[0]).data == slot1,
          "slot 1 cleared, copy in the library")
    pp.refresh_library()
    for i in range(pp.lib.topLevelItemCount()):
        if pp.lib.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) == str(lib[0]):
            pp.lib.setCurrentItem(pp.lib.topLevelItem(i))
    pp._import()
    check(device_read(2) == slot1, f"copy played back (undo) {log_boxes[-1][:80]}")
    fremd = projects.save_project(ctx.settings.projects_dir, "SLOOP Section A", b"6NUF" + bytes(200), "FELUCCA SLOOP 2.8.3", "X")
    pp.refresh_library()
    for i in range(pp.lib.topLevelItemCount()):
        if pp.lib.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) == str(fremd):
            pp.lib.setCurrentItem(pp.lib.topLevelItem(i))
    n_boxes = len(log_boxes)
    pp._import()
    check(len(log_boxes) == n_boxes + 1 and "SLOOP" in log_boxes[-1] and "nichts geschrieben" in log_boxes[-1]
          and device_read(2) == slot1, "SLOOP project on Melodee: refused, nothing written")
    fremd.unlink()

    # ---- Devices page: SLOOP as target refused
    melodee_prof = ctx.profile
    port2 = next(x for x in ctx.ports if x.serial == "DEMO-2")
    ctx.connect_port(port2)
    check(until(lambda: ctx.connected and ctx.firmware_key == "sloop"), "SLOOP demo connected")
    sloop_prof = ctx.profile
    check(until(lambda: len(autos(ctx.device_folder(ctx.settings.backups_dir))) == 1, 15000), "Auto-Backup SLOOP")
    # ---- acceptance p2: compare a Melodee backup with the SLOOP state → "not comparable"
    sp0 = AB.list_backups(ctx.device_folder(ctx.settings.backups_dir), recursive=False)[0].path
    w.go("backup_browser")
    br.open_path(p)
    br.compare_with(BI.inspect(sp0), "SLOOP")
    check("Nicht vergleichbar" in br.cmp_sum.text() and br.cmp_tree.topLevelItemCount() == 0,
          "Melodee ↔ SLOOP: not comparable instead of nonsense")
    br.cmp_with.setCurrentIndex(0)
    br.compare()                                      # live state (SLOOP) against a Melodee backup
    check("Nicht vergleichbar" in br.cmp_sum.text(), "live comparison with another firmware refused")
    # project view: the SLOOP project is explained, reading from the SLOOP device refused without device access
    pv = w.page("project_view")
    w.go("project_view")
    pv.open_backup(str(sp0))
    check(pv.prj is not None and not pv.prj.ok and "SLOOP-Web-Editor" in pv.info.text(), "SLOOP project: clear explanation")
    n_boxes = len(log_boxes)
    pv.slot.setValue(0)
    pv.read_device()
    check(len(log_boxes) == n_boxes + 1 and "SLOOP" in log_boxes[-1], "read from the SLOOP FM-1: a hint instead of an error")
    w.go("projects")
    check(until(lambda: pp.slots and not pp.reading, 10000) and pp.dev_tree.headerItem().text(0) == "Section"
          and "Section" in pp.dev_info.text(), "Projects page names SLOOP sections")
    w.go("devices")
    dv = w.page("devices")
    check(dv.cards.count() == 2, "two device cards")
    res = dv.compare(melodee_prof.id, sloop_prof.id)
    check(res and res.rows[0][1].startswith("Melodee") and "SLOOP" in res.rows[0][2] and "FM KEYS" in res.only_a,
          f"devices compared {res.rows[0] if res else ''}")
    before = device_read(6)
    comp, ok = dv.copy(melodee_prof.id)
    check(comp is not None and not comp.ok and not ok and "sloop" in comp.reason.lower(), "copy Melodee → SLOOP refused")
    check(device_read(6) == before, "SLOOP unchanged")

    # ---- SLOOP: own backup protocol (34–36) and file format of the SLOOP editor
    sp = make_backup(ctx, w, "SLOOP von Hand")
    sf = json.loads(sp.read_text()) if sp else {}
    check(sf.get("format") == "sloop-backup" and sf.get("firmware", "").startswith("FELUCCA SLOOP")
          and sp.read_text() == json.dumps(sf, indent=1, ensure_ascii=False), "SLOOP-Backup im Format des SLOOP-Editors")
    sec_a = device_read(2)
    check(sec_a[:4] == b"6NUF", "section A is a SLOOP project (FUN6)")
    check(device_write(2, b""), "section A cleared")
    br.open_path(sp)
    check(br.rep.restorable and br.rep.format == "sloop-backup", "browser: SLOOP backup restorable")
    br.select_ids([2])
    check(br.restore_selected() and device_read(2) == sec_a, "SLOOP: section A restored on its own")
    check(device_write(2, b"") and device_write(6, b""), "SLOOP: section A and preset bank cleared")
    w.go("backups")
    w.backups.refresh()
    for i in range(w.backups.tree.topLevelItemCount()):
        if w.backups.tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) == str(sp):
            w.backups.tree.setCurrentItem(w.backups.tree.topLevelItem(i))
    w.backups._restore()
    check(device_read(2) == sec_a and BI.decode_up_bank(device_read(6), 1, 6)[0].name == "ACID LINE",
          "SLOOP: complete backup restored")
    w.go("backup_browser")
    br.open_path(sp)
    for i in range(br.content.topLevelItemCount()):
        g = br.content.topLevelItem(i)
        for j in range(g.childCount()):
            if g.child(j).data(0, Qt.ItemDataRole.UserRole) == 2:
                br.content.setCurrentItem(g.child(j))
    check(br.b_view.isEnabled() and br.view_project() and w.page("project_view").isVisible(),
          "\"View project\" opens the project view")

    # ---- the second FM-1 gets Melodee: copy allowed
    os.environ["FM1C_DEMO_CMD"] = f"{sys.executable}\x1f-m\x1ffm1companion.demo_device"
    ctx.connect_port(port2)
    check(until(lambda: ctx.connected and ctx.firmware_key == "melodee" and ctx.profile.id == sloop_prof.id),
          "second FM-1 now with Melodee")
    wait(2500)
    until(lambda: not w.backups.auto.running, 10000)
    dv.rename(sloop_prof.id, "Zweites FM-1")
    check(ctx.profile.name == "Zweites FM-1" and ctx.device_folder(ctx.settings.backups_dir).exists(), "renamed")
    comp, ok = dv.copy(melodee_prof.id)
    check(comp is not None and comp.ok and ok, f"Kopie Melodee → Melodee ({comp.reason if comp else '-'})")
    got = BI.decode_up_bank(device_read(6), 1, 6)
    check([x.name for x in got if x.used] == ["BASS ONE", "SID LEAD", "FM KEYS"], "presets arrived")
    check(len(device_read(66)) == 2500, "project 7 arrived")
    os.environ.pop("FM1C_DEMO_CMD", None)
    app.quit()


def run():
    try:
        flow()
    except Exception:  # noqa: BLE001
        errors.append(traceback.format_exc())
        app.quit()


QTimer.singleShot(500, run)
QTimer.singleShot(240000, app.quit)
app.exec()
fails = [r for r in results if not r[0]]
for e in errors:
    print("EXCEPTION:\n" + e)
print(f"{len(results) - len(fails)}/{len(results)} passed, {len(errors)} exceptions")
shutil.rmtree(home, ignore_errors=True)
sys.exit(1 if fails or errors else 0)
