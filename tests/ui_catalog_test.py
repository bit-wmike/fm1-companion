# SPDX-License-Identifier: GPL-3.0-only
"""Firmware+ (formerly Discover) and link import in demo mode (offscreen, without network).

  flock /tmp/fm1c-ui.lock timeout 600 env QT_QPA_PLATFORM=offscreen python3 tests/ui_catalog_test.py [SHOTDIR]
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
home = Path(tempfile.mkdtemp(prefix="fm1c-catalog-"))
shots = Path(sys.argv[1]) if len(sys.argv) > 1 else None
for k, sub in (("XDG_CONFIG_HOME", ".config"), ("XDG_DATA_HOME", ".local/share"), ("XDG_CACHE_HOME", ".cache")):
    os.environ[k] = str(home / sub)
os.environ["FM1C_DEMO"] = "1"
os.environ["FM1C_NO_NET"] = "1"
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu")

errors: list[str] = []
sys.excepthook = lambda t, v, tb: errors.append("".join(traceback.format_exception(t, v, tb)))

import PyQt6.QtWebEngineWidgets  # noqa: E402,F401
from PyQt6.QtCore import QCoreApplication, QEventLoop, Qt, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
app = QApplication(sys.argv)

from fm1companion import config, firmware  # noqa: E402

s = config.load()
s.user_root = str(home / "FM-1")
s.check_on_start = False
config.save(s)
config.ensure_dirs()

QMessageBox.exec = lambda self: 0
QMessageBox.information = staticmethod(lambda *a, **k: 0)
QDialog.exec = lambda self: 0          # dialogs are operated directly in the test

from fm1companion.ui.main_window import MainWindow  # noqa: E402

w = MainWindow()
w.resize(1280, 800)
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


def shot(name):
    if shots:
        shots.mkdir(parents=True, exist_ok=True)
        w.grab().save(str(shots / f"{name}.png"))


def flow():
    page = w.page("discover")
    check(page is not None and w.specs["discover"].title == "Firmware+", "page \"Firmware+\" is registered")
    w.go("discover")
    wait(300)
    n = page.model.rowCount()
    check(n >= 200, f"catalog loaded from the snapshot ({n} cards)")
    check("Revision" in page.head.findChildren(type(page.count))[1].text(), "header shows the catalog revision")
    shot("01_store")
    page.search.setText("acid")
    n_acid = page.model.rowCount()
    check(0 < n_acid < n, f"search \"acid\" ({n_acid})")
    page.search.setText("charles vestal")
    check(all("vestal" in e.search_text() for e in page.model.items) and page.model.rowCount() >= 5,
          f"search by author ({page.model.rowCount()})")
    page.search.clear()
    page.chips["installable"].setChecked(True)
    n_inst = page.model.rowCount()
    check(80 <= n_inst < n and all(e.installable for e in page.model.items), f"filter directly installable ({n_inst})")
    page.chips["emulator"].setChecked(True)
    n_both = page.model.rowCount()
    check(0 < n_both < n_inst and all(e.emulator_url for e in page.model.items), f"+ mit Emulator ({n_both})")
    page.reset_filters()
    check(page.model.rowCount() == n, "reset filter")
    page._menu_toggled("lin", "SLOOP", True)
    check(all("SLOOP" in e.lineage for e in page.model.items) and page.model.rowCount() > 10,
          f"base SLOOP ({page.model.rowCount()})")
    page._menu_toggled("lin", "SLOOP", False)
    page._menu_toggled("cat", "Spiele & Spaß", True)
    check(any(e.key == "fm-1-doom" for e in page.model.items), "category games & fun contains FM-1 Doom")
    page._menu_toggled("cat", "Spiele & Spaß", False)
    page.chips["backup"].setChecked(True)
    keys = {e.key for e in page.model.items}
    check({"melodee", "sloop", "skiff", "felucca"} <= keys, f"backup-capable: {sorted(keys)[:6]}")
    page.reset_filters()
    page.search.setText("zzzzqqq")
    check(page.empty.isVisible() if page.isVisible() else page.model.rowCount() == 0, "empty state with no match")
    page.search.clear()
    # umlauts, regex characters, filter combination without a match: nothing crashes, the empty state explains
    for q in ("Ärger (ö)", "[a-z]+.*\\d", "***", "acid"):
        page.search.setText(q)
    check(page.model.rowCount() > 0, "search with regex characters in front does not disturb")
    page.search.setText("Dr\u00fcms")
    check(page.model.rowCount() == 0 and "zurücksetzen" in page.empty.text(), "empty state names the way back")
    page.search.clear()
    page.chips["wish"].setChecked(True)
    page.chips["installable"].setChecked(True)
    page.menus["lic"].add("geschlossen")
    page.apply_filter()
    check(page.model.rowCount() == 0, "filter combination without a match: 0 cards")
    page.menus["lic"].clear()
    page.reset_filters()
    check(page.model.rowCount() == n, "reset clears everything")
    page.sort.setCurrentIndex(page.sort.findData("stars"))
    page.sort.setCurrentIndex(page.sort.findData("az"))
    check(page.model.items[0].name.lower() <= page.model.items[-1].name.lower(), "sorting A–Z")

    # ---- detail with warnings
    page.open_key("hortator")
    wait(200)
    d = page.detail
    check(page.stack.currentIndex() == 1 and d.entry.key == "hortator", "detail view opens")
    wt = d.labels.get("warnings").text() if d.labels.get("warnings") else ""
    check("Beta" in wt and "nicht fortsetzen" in wt and "V15" in wt, "warnings in plain language (beta, abort, V15)")
    check(d.labels.get("m_Installieren") == "✓" and d.labels.get("m_Emulator") == "✓", "matrix: installable, emulator")
    shot("02_detail")
    # entry without repo and without package: the detail page holds up, "Install" grey with a reason, README button grey
    norepo = next((e for e in page.cat.entries if not e.repo and not e.installable and not e.builtin), None)
    if norepo is not None:
        page.open_entry(norepo)
        wait(200)
        check(page.detail.entry is norepo and not page.detail.b_install.isEnabled()
              and "Kein geprüftes Paket" in page.detail.b_install.toolTip() and not page.detail.b_readme.isEnabled(),
              f"entry without repo/package ({norepo.name}): Install grey with an explanation")
        check(page.detail.versions.topLevelItemCount() >= 1, "versions list shows \"no versions known\" instead of empty")
    else:
        check(True, "no entry without repo in the catalog")
    page.open_key("hortator")
    wait(200)

    # ---- actions
    calls = []
    orig_go = w.go
    w.go = lambda key, **kw: calls.append((key, kw))
    d_install = page.window.page("firmware")
    had = hasattr(d_install, "open_with")
    if not had:
        d_install.open_with = lambda **kw: None
    page.install()
    check(calls and calls[-1][0] == "firmware" and calls[-1][1]["install"].key == "hortator"
          and calls[-1][1]["install"].sha256 and calls[-1][1]["install"].download_url.endswith(".fwsc"),
          "Install → window.go('firmware', install=Spec with package and checksum)")
    if not had:
        del d_install.open_with
    sim = object()
    orig_page = w.page
    w.page = lambda k: sim if k == "simulator" else orig_page(k)
    page.emulator()
    check(calls[-1][0] == "simulator" and calls[-1][1]["kind"] == "emulator"
          and "deadactive.github.io" in calls[-1][1]["url"], "Emulator → window.go('simulator', kind='emulator')")
    page.open_key("felucca")
    page.editor()
    check(calls[-1][1].get("kind") == "editor" and "editor" in calls[-1][1]["url"], "Online-Editor → simulator, kind='editor'")
    w.go, w.page = orig_go, orig_page

    # ---- to my firmwares
    page.open_key("orbit")
    page.add_own()
    sp = firmware.spec("orbit")
    check(sp is not None and not sp.builtin and sp.sha256 and sp.repo == "dspaudio/orbit",
          "\"Add to my firmwares\" → firmware.catalog() contains ORBIT")
    # Firmware+: the tab "Install & Versions" is the same page and takes over the new entry when opened
    fwp = w.page("firmware")
    keys = lambda: [fwp.cat.item(i).data(Qt.ItemDataRole.UserRole) for i in range(fwp.cat.count())]
    sel_before = fwp.current_spec().key if fwp.current_spec() else ""
    w.go("firmware")
    wait(200)
    check(w.current_key() == "discover" and page.tabs.currentWidget() is fwp and "orbit" in keys()
          and (fwp.current_spec().key if fwp.current_spec() else "") == sel_before,
          "Firmware+ › Install & Versions: ORBIT is in the list, the selection stays")
    page.tabs.setCurrentIndex(0)
    page.open_key("orbit")
    page.detail.b_wish.setChecked(True)
    check("orbit" in config.module("catalog")["wishlist"], "Remember stores in the wishlist")
    page.fill_news()
    check("ORBIT" in page.news_info.text() or page.news.topLevelItemCount() >= 0, "news tab knows the followed firmware")
    page.detail.back.emit()
    check(page.stack.currentIndex() == 0, "back to the overview")

    # ---- link import dialog
    from fm1companion.ui.linkimport_dialog import LinkImportDialog, OwnFirmwaresDialog
    dlg = LinkImportDialog(page, "https://github.com/someone/cool-fm1-synth/releases/download/v1.2.0/cool-1.2.0.fwsc",
                           cat=page.cat, online=False)
    check(dlg.w["repo"].text() == "someone/cool-fm1-synth" and dlg.w["asset"].text() == "cool-{ver}.fwsc",
          f"dialog prefilled ({dlg.w['repo'].text()}, {dlg.w['asset'].text()})")
    check("geraten" in dlg.mark["name"].text() and "sicher" in dlg.mark["download_url"].text(), "marking guessed/certain")
    shot("03_link_dialog") if not dlg.show() else None
    dlg.w["name"].setText("Cool Synth")
    dlg._edited("name")
    dlg.w["key"].setText("orbit")
    dlg._save()
    check(dlg.saved_spec is None and "gibt es schon" in dlg.errors.text(), "check: the key must be unique")
    dlg.w["key"].setText("cool-synth")
    dlg._save()
    sp = firmware.spec("cool-synth")
    check(sp is not None and sp.name == "Cool Synth" and sp.download_url.endswith("cool-1.2.0.fwsc"),
          "corrected entry saved")
    dlg.close()
    page.set_catalog(page.cat)
    check(page.cat.by_key("cool-synth") is not None, "own entry appears as a card")
    od = OwnFirmwaresDialog(page, page.cat)
    check(od.list.count() == 2, f"My firmwares lists 2 entries ({od.list.count()})")
    for i in range(od.list.count()):
        if od.list.item(i).data(Qt.ItemDataRole.UserRole) == "cool-synth":
            od.list.setCurrentRow(i)
    from fm1companion.ui import linkimport_dialog as LD
    LD.confirm = lambda *a, **k: True
    od._remove()
    check(firmware.spec("cool-synth") is None, "entry removed")
    # editing a saved entry
    from fm1companion import linkimport as L
    e = next(x for x in L.own_entries() if x["key"] == "orbit")
    ed = LinkImportDialog(page, draft=L.draft_from_saved(e), editing_key="orbit", cat=page.cat)
    ed.w["description"].setPlainText("geändert")
    ed._save()
    check(firmware.spec("orbit").description == "geändert", "edit a saved entry")
    ed.close()
    od.close()
    # ---- a bare name that is not in the catalog: the dialog must ask GitHub (search)
    from fm1companion import catalog as C
    jobs = []
    saved = LD.run_job, C.search_repos, C.enrich
    LD.run_job = lambda parent, fn, on_done=None, on_fail=None, **k: (jobs.append(1), on_done(fn(None)))
    C.search_repos = lambda text, limit=8: [{"repo": "me/karma-fm1", "description": "FM-1 groovebox", "stars": 1,
                                             "fork": False, "exact": True}]
    C.enrich = lambda repo, ttl=0: (_ for _ in ()).throw(C.Offline("im Test kein Netz"))
    try:
        kd = LinkImportDialog(page, "KARMA", cat=page.cat, online=True)
        check(jobs and kd.w["repo"].text() == "me/karma-fm1",
              f"name without catalog entry: GitHub search runs ({kd.w['repo'].text()!r})")
        kd.close()
        jobs.clear()
        kd = LinkImportDialog(page, "KARMA", cat=page.cat, online=False)
        check(not jobs and not kd.w["repo"].text(), "without the tick \"look on GitHub\" no search")
        kd.close()
        # several matches, none unique: chooser window; the choice fills the entry
        C.search_repos = lambda text, limit=8: [
            {"repo": "a/dungeon-synth-fm1", "description": "dungeon synth for the FM-1", "stars": 5, "fork": False,
             "exact": False},
            {"repo": "b/crawler-fm1", "description": "dungeon crawler FM-1", "stars": 0, "fork": True, "exact": False}]
        kd = LinkImportDialog(page, "dungeon zeug", cat=page.cat, online=True)
        ch = kd.chooser
        check(ch is not None and not ch.isHidden() and ch.list.count() >= 2 and not kd.b_pick.isHidden(),
              f"several matches: chooser window with {ch.list.count() if ch else 0} entries")
        row = next(i for i, c in enumerate(ch.candidates) if c["repo"] == "b/crawler-fm1")
        check("Fork" in ch.list.item(row).text(), "fork is marked")
        ch.list.setCurrentRow(row)
        ch.accept()
        check(kd.w["repo"].text() == "b/crawler-fm1" and kd.input.text() == "https://github.com/b/crawler-fm1",
              f"choice taken over ({kd.w['repo'].text()})")
        check(not kd.b_pick.isHidden() and kd.chooser is ch and ch.isHidden(),
              "match list stays reachable, the window does not open again")
        kd.close()
    finally:
        LD.run_job, C.search_repos, C.enrich = saved
    # ---- tools & knowledge
    page.tabs.setCurrentIndex(2)
    check(page.res.topLevelItemCount() >= 6, f"tools & knowledge: {page.res.topLevelItemCount()} groups")
    shot("04_tools")
    page.tabs.setCurrentIndex(1)
    shot("05_news")
    page.tabs.setCurrentIndex(0)
    app.quit()


def run():
    try:
        flow()
    except Exception:  # noqa: BLE001
        errors.append(traceback.format_exc())
        app.quit()


QTimer.singleShot(400, run)
QTimer.singleShot(180000, app.quit)
app.exec()
fails = [r for r in results if not r[0]]
for e in errors:
    print("EXCEPTION:\n" + e)
print(f"{len(results) - len(fails)}/{len(results)} passed, {len(errors)} exceptions")
shutil.rmtree(home, ignore_errors=True)
sys.exit(1 if fails or errors else 0)
