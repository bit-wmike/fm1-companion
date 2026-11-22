# SPDX-License-Identifier: GPL-3.0-only
"""Findings of the owner's hardware run of the tools package, step 4 (2026-11-21, report "Werkzeug-Paket Schritt 4"),
each with the fix it got – against stand-ins and with synthetic ids and names only:

* F  the port memory after the app's own job ("the FM-1 the app has just installed on at this USB port") named
     another FM-1 plugged in at the same port: it holds now only when the unit shows what the job left;
* B  after the app's own job restarted the unit into another firmware, recognition stayed stale (same port key);
     "Reconnect" connected the stale port;
* A  "Check SLOOP 3 in the flash" was offered before the app had recognised SLOOP 3 and could not be started later;
* C  the SLOOP 3 install texts said "everything after it stays" – SLOOP 3's first start sets up its own storage;
* D  plan texts while SLOOP 3 runs (backup note, the tested line of the download-mode route, the name twice, the
     unit question asked again);
* E/G "Back to …" offered only the newest whole-flash backup and promised a "first choice" it did not have."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fm1companion import devices  # noqa: E402
from fm1companion.tools import bootarea, flashjob as FJ  # noqa: E402
from test_pi_hardware import NEW_SERIAL, OLD_SERIAL, assign, fake_ctx, felucca_port, stock_port, store_with  # noqa: E402
from test_tools_flash import UID, env  # noqa: E402,F401

PORT = "3-1.3.1"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    return tmp_path


@pytest.fixture
def english(monkeypatch):
    from fm1companion import i18n
    # (modules with translated constants are imported first, in the language the other tests expect – only the
    # calls during this test are English, whatever an earlier test set)
    for mod in ("fm1companion.flash.planner", "fm1companion.flash.tested", "fm1companion.ui.device",
                "fm1companion.ui.flash_tools", "fm1companion.ui.sloop3_ui", "fm1companion.ui.firmware_page",
                "fm1companion.ui.overview"):
        __import__(mod)
    monkeypatch.setattr(i18n, "_lang", "en")
    yield


_APP = []


def qapp():
    pytest.importorskip("PyQt6.QtWidgets")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    if QApplication.instance() is None:
        _APP.append(QApplication([]))            # (kept: a collected QApplication takes every widget with it)
    return QApplication.instance()


def spin(ms: int):
    from PyQt6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def sloop3_port(usb=PORT, client=32):
    return devices.PortInfo(f"FM-1 SLOOP 3:FM-1 SLOOP 3 MIDI 1 {client}:0", f"FM-1 SLOOP 3:FM-1 SLOOP 3 MIDI 1 {client}:0",
                            "FM-1 SLOOP 3", client, usb_id="1209:0001", usb_path=usb, product="FM-1 SLOOP 3")


def midi_mode_port(usb=PORT, client=32):
    return devices.PortInfo(f"FM-1 Update:FM-1 Update MIDI 1 {client}:0", f"FM-1 Update:FM-1 Update MIDI 1 {client}:0",
                            "FM-1 Update", client, usb_id="1209:0002", usb_path=usb, update_mode=True,
                            product="FM-1 Update")


def two_units(home):
    """the earlier unit (Melodee, its unit ID known from M-Flasher) and the newer one (SLOOP 3) – neither shows a
    serial number under these firmwares"""
    return store_with(home, [
        {"id": "e1e1e1e1e1", "name": "FM-1 Earlier", "firmware": "melodee", "firmware_version": "v1.6.1",
         "identity": "FM-1_91061", "usb_path": PORT, "usb_product": "Felucca", "hw_revision": "old",
         "hw_revision_source": "user", "unit_id": OLD_SERIAL},
        {"id": "n2n2n2n2n2", "name": "FM-1 Newer", "firmware": "sloop3", "firmware_version": "v3.4.0",
         "usb_path": PORT, "usb_product": "FM-1 SLOOP 3", "hw_revision": "new", "hw_revision_source": "user",
         "unit_id": NEW_SERIAL}])


# ------------------------------------------------------------------------------------------------ F: port memory

def test_f_another_unit_at_the_same_port_after_a_job_is_asked_about(home):
    """the Pi run: the earlier unit's backup written back (Melodee), then the newer unit (SLOOP 3) plugged in at the
    same USB port within minutes – it was taken for the earlier one without a question"""
    store = two_units(home)
    old = store.profiles["e1e1e1e1e1"]
    ctx = fake_ctx(store, sloop3=object())
    ctx.note_installed(old, PORT, {"sloop3": False})          # (the image the job wrote back is not SLOOP 3)
    prof, asked = assign(store, sloop3_port(), identity="", ctx=ctx, ask=lambda c: None)
    assert prof is None and len(asked) == 1 and ctx.identified_by != "install"
    assert ctx.session_units == {}                             # (and the memory is used up)


def test_f_the_expected_continuation_is_still_recognised_without_a_question(home):
    store = two_units(home)
    old = store.profiles["e1e1e1e1e1"]
    # SLOOP 3 written by the app over MIDI MODE: SLOOP 3 comes back at that port
    ctx = fake_ctx(store, sloop3=object())
    ctx.note_installed(old, PORT, {"sloop3": True})
    prof, asked = assign(store, sloop3_port(), identity="", ctx=ctx)
    assert prof is old and not asked and ctx.identified_by == "install"
    # a firmware through download mode: the identity the package names comes back
    ctx = fake_ctx(store)
    ctx.note_installed(old, PORT, {"sloop3": False, "identity": "FM-1_727"})
    prof, asked = assign(store, felucca_port("Felucca"), identity="FM-1_727", ctx=ctx)
    assert prof is old and not asked and ctx.identified_by == "install"
    # … another identity at that port: another unit (or not what the job left) – asked
    ctx = fake_ctx(store)
    ctx.note_installed(old, PORT, {"sloop3": False, "identity": "FM-1_727"})
    prof, asked = assign(store, felucca_port("Felucca"), identity="FM-1_91061", ctx=ctx)
    assert prof is None and len(asked) == 1


def test_f_m_bootloaders_midi_mode_in_between_keeps_the_memory(home):
    store = two_units(home)
    old = store.profiles["e1e1e1e1e1"]
    ctx = fake_ctx(store)
    ctx.note_installed(old, PORT, {"sloop3": True})
    assign(store, midi_mode_port(), identity="", ctx=ctx)      # (update mode: nothing compared, nothing asked)
    assert PORT in ctx.session_units


def test_f_the_unit_id_the_job_read_must_match_what_the_original_firmware_shows(home):
    store = store_with(home, [{"id": "e1e1e1e1e1", "name": "FM-1 Earlier", "firmware": "skiff", "usb_path": PORT},
                              {"id": "n2n2n2n2n2", "name": "FM-1 Newer", "firmware": "skiff", "usb_path": "1-2"}])
    old = store.profiles["e1e1e1e1e1"]
    ctx = fake_ctx(store)
    ctx.note_installed(old, PORT, {"unit_id": OLD_SERIAL})     # (M-Flasher read this unit id in the job)
    prof, _asked = assign(store, stock_port(NEW_SERIAL, PORT), identity="FM-1_015", ctx=ctx, ask=lambda c: None)
    assert ctx.identified_by != "install" and prof is not old
    ctx = fake_ctx(store)
    ctx.note_installed(old, PORT, {"unit_id": OLD_SERIAL})
    prof, asked = assign(store, stock_port(OLD_SERIAL, PORT), identity="FM-1_015", ctx=ctx)
    assert prof is old and not asked


def test_f_remember_unit_expects_what_the_job_left(home):
    from fm1companion.ui.flash_tools import remember_unit, same_expectation
    store = two_units(home)
    old = store.profiles["e1e1e1e1e1"]
    noted, restarted = [], []
    ctx = NS(note_installed=lambda p, usb, expect=None: noted.append(expect), mark_restarted=restarted.append)
    # a job that wrote nothing (a backup, download mode and back): the unit comes back as it was
    job = FJ.Job(backups_dir=home, profile=old, usb_path=PORT)
    remember_unit(ctx, job)
    assert noted[-1] == same_expectation(old) == {"sloop3": False, "identity": "FM-1_91061", "product": "Felucca"}
    assert not restarted                                       # (never in download mode: nothing to reconnect)
    # a write started: what the job says it left, plus the unit id M-Flasher read
    job = FJ.Job(backups_dir=home, profile=old, usb_path=PORT, device="/dev/sg9", expect={"sloop3": True})
    job.info = NS(unit_id=OLD_SERIAL)
    remember_unit(ctx, job)
    assert noted[-1] == {"sloop3": True, "unit_id": OLD_SERIAL} and restarted == [PORT]


def test_f_the_overview_offers_another_fm1(home, english):
    qapp()
    from fm1companion.ui.overview import OverviewPage
    store = two_units(home)
    old = store.profiles["e1e1e1e1e1"]
    asked = []
    from PyQt6.QtCore import QObject, pyqtSignal

    class Ctx(QObject):
        changed = pyqtSignal()
        ports_changed = pyqtSignal()
    c = Ctx()
    port = felucca_port("Felucca")
    for k, v in dict(profile=old, port=port, ports=[port], identified_by="install", is_virtual=False, info=None,
                     unit_candidates=[], firmware_key="melodee", firmware_ambiguous=False, store=store, mboot=None,
                     settings=NS(backups_dir=home / "Backups"), ask_which=lambda: asked.append(1),
                     identified_text=lambda: "x", connected=False, protocol="generic", sloop3=None,
                     firmware_tag="", identity="FM-1_91061", device_folder=lambda base: base,
                     profile_for_port=lambda p: None, status_text=lambda: "", rename_profile=lambda *a: None).items():
        setattr(c, k, v)
    page = OverviewPage(c)
    text = page.lbl["Name"].text()
    assert "another FM-1?" in text and "#which" in text
    page.lbl["Name"].linkActivated.emit("#which")
    assert asked == [1]
    c.identified_by = "unit_id"
    page.refresh()
    assert "another FM-1?" not in page.lbl["Name"].text()     # (told by its unit ID: no doubt to offer)


# ------------------------------------------------------------------------------------------------ B: stale recognition

def _ctx_with(monkeypatch, ports):
    qapp()
    from fm1companion.ui import device as D
    seen = {"ports": list(ports)}
    monkeypatch.setattr(D, "DEMO", False)
    monkeypatch.setattr(D, "scan_ports", lambda: list(seen["ports"]))
    monkeypatch.setattr(devices, "uboot_present", lambda *a: [])
    monkeypatch.setattr(D.config, "save", lambda s: None)
    ctx = D.DeviceContext()
    ctx.timer.stop()
    connected = []
    ctx.connect_port = lambda p: (connected.append(p), setattr(ctx, "port", p))
    ctx.port, ctx.ports = ports[0], list(ports)
    return ctx, seen, connected


def test_b_a_firmware_change_during_the_hold_is_picked_up_when_the_hold_ends(monkeypatch):
    """MIDI MODE -> SLOOP 3 while the app's own job held the scan: same USB path, no serial number – the same port
    key. The scan ignored it ("Neu verbinden" even showed the update port); now the hold's end looks again."""
    mm = midi_mode_port()
    ctx, seen, connected = _ctx_with(monkeypatch, [mm])
    s3 = sloop3_port()
    assert s3.key == mm.key                                    # (the root cause: the key does not tell them apart)
    ctx.hold = True
    seen["ports"] = [s3]
    ctx.scan()
    assert connected == []                                     # (on hold: nothing)
    ctx.hold = False                                           # (the job ends)
    spin(700)
    assert connected and connected[-1] is s3 and ctx.ports == [s3]


def test_b_a_restart_under_the_same_names_is_connected_anew(monkeypatch):
    """a written-back image starts the same firmware under the same MIDI names: the job's restart is told by
    mark_restarted (flash_tools.remember_unit) – the old connection is stale"""
    a = felucca_port("Felucca")
    ctx, seen, connected = _ctx_with(monkeypatch, [a])
    b = felucca_port("Felucca")
    seen["ports"] = [b]
    ctx.scan()
    assert connected == []                                     # (nothing happened: nothing reconnected)
    ctx.mark_restarted(PORT)
    ctx.scan()
    assert connected == [b]
    ctx._restarted.discard(PORT)                               # (connect_port clears it; the stand-in here does not)
    ctx.scan()
    assert connected == [b]                                    # (once)


def test_b_reconnect_reads_the_ports_afresh(monkeypatch):
    mm = midi_mode_port()
    ctx, seen, connected = _ctx_with(monkeypatch, [mm])
    s3 = sloop3_port()
    seen["ports"] = [s3]
    ctx.reconnect()
    assert connected == [s3]                                   # (not the stale "FM-1 Update")


def test_b_the_hold_flag_still_holds(monkeypatch):
    mm = midi_mode_port()
    ctx, seen, connected = _ctx_with(monkeypatch, [mm])
    ctx.hold = True
    assert ctx.hold is True
    seen["ports"] = [sloop3_port()]
    spin(500)
    ctx.scan()
    assert connected == []
    ctx.reconnect_soon(10_000)                                 # (releases without the quick look: the caller waits)
    assert ctx.hold is False


# ------------------------------------------------------------------------------------------------ A: the SLOOP 3 check

def _res(n=0x78000):
    exp = bytes(range(256)) * (0x100000 // 256)
    return NS(expected=exp, package_flash=b"\0" * n, version="3.4.0", backup=Path("pre.bin"))


def test_a_the_check_is_kept_with_the_unit_and_read_back(home):
    from fm1companion.ui import sloop3_ui
    prof = devices.Profile(id="e1e1e1e1e1", name="FM-1 Earlier")
    ctx = NS(settings=NS(backups_dir=home / "Backups"))
    d = sloop3_ui.save_check(ctx, prof, _res())
    assert d is not None and (d / "expected.bin").stat().st_mode & 0o777 == 0o600
    got = sloop3_ui.load_check(ctx, prof)
    assert got["end"] == 0x78000 and got["expected"] == _res().expected[:0x78000] and got["version"] == "3.4.0"
    assert FJ.unit_backups(d.parent, UID) == []                # (not a whole-flash backup: never offered as one)
    other = devices.Profile(id="o0o0o0o0o0", name="FM-1 Earlier")      # (another profile with that folder name)
    assert sloop3_ui.load_check(ctx, other) is None
    (d / "expected.bin").write_bytes(b"\1" * 0x78000)          # (changed on disk: not trusted)
    assert sloop3_ui.load_check(ctx, prof) is None


def test_a_the_check_waits_for_sloop3_and_never_starts_before(home, monkeypatch, english):
    qapp()
    from fm1companion.ui import feedback, flash_tools as ft, sloop3_ui
    prof = devices.Profile(id="e1e1e1e1e1", name="FM-1 Earlier")
    ctx = NS(settings=NS(backups_dir=home / "Backups"), profile=prof, is_virtual=False, sloop3=None,
             port=midi_mode_port(), hold=False)
    errors, ran = [], []
    monkeypatch.setattr(sloop3_ui, "confirm", lambda *a, **k: True)
    monkeypatch.setattr(sloop3_ui, "show_error", lambda parent, title, text: errors.append(text))
    monkeypatch.setattr(ft, "_not_here", lambda *a: False)
    monkeypatch.setattr(feedback, "run", lambda parent, title, work, **kw: ran.append(work(NS(log=NS(emit=print)))))
    monkeypatch.setattr(ft, "_job", lambda *a, **k: FJ.Job(backups_dir=home))
    monkeypatch.setattr(FJ, "verify_install",
                        lambda job, exp, end, what: NS(same=True, first_difference=-1, restarted=True))
    # not recognised yet, the owner cancels the wait: nothing is sent, and it says where to start it later
    monkeypatch.setattr(sloop3_ui, "wait_for_sloop3", lambda *a: False)
    assert sloop3_ui.verify_flow(None, ctx, _res()) is None
    assert not ran and "Recovery › Tools" in errors[-1]
    # later, from Recovery › Tools (no result at hand: the kept check), once SLOOP 3 runs
    monkeypatch.setattr(sloop3_ui, "wait_for_sloop3", lambda *a: True)
    v = sloop3_ui.verify_flow(None, ctx, None)
    assert v is not None and v.same and len(ran) == 1
    import json
    meta = json.loads((home / "Backups" / prof.folder_name / "Flash" / sloop3_ui.CHECK_DIR / "check.json").read_text())
    assert meta["checked"]["same"] is True
    # another unit without a kept check: refused with the reason
    ctx.profile = devices.Profile(id="n2n2n2n2n2", name="FM-1 Newer")
    assert sloop3_ui.verify_flow(None, ctx, None) is None and "knows no SLOOP 3 install" in errors[-1]


def test_a_wait_for_sloop3_ends_when_the_app_shows_it(home, english):
    qapp()
    from PyQt6.QtCore import QObject, QTimer, pyqtSignal
    from fm1companion.ui import sloop3_ui
    prof = devices.Profile(id="e1e1e1e1e1", name="FM-1 Earlier")

    class Ctx(QObject):
        changed = pyqtSignal()
    c = Ctx()
    c.sloop3, c.port, c.profile = None, midi_mode_port(), None

    def arrive():                                              # (the restart: SLOOP 3 is recognised on this unit)
        c.sloop3, c.port, c.profile = object(), sloop3_port(), prof
        c.changed.emit()
    QTimer.singleShot(300, arrive)
    t0 = time.monotonic()
    assert sloop3_ui.wait_for_sloop3(None, c, prof, "t") is True and time.monotonic() - t0 < 5
    # another unit's SLOOP 3 does not count; Cancel ends the wait
    from PyQt6.QtWidgets import QApplication, QProgressDialog
    c.profile = devices.Profile(id="o0o0o0o0o0", name="FM-1 Newer")

    def cancel():
        for w in QApplication.topLevelWidgets():
            if isinstance(w, QProgressDialog) and w.isVisible():
                w.cancel()
    QTimer.singleShot(400, cancel)
    assert sloop3_ui.wait_for_sloop3(None, c, prof, "t") is False


# ------------------------------------------------------------------------------------------------ C: SLOOP 3's storage

def test_c_the_install_texts_say_the_old_firmwares_data_is_gone(english):
    from fm1companion.ui import sloop3_ui
    plan = NS(version="3.4.0", unit_name="FM-1 Earlier", unit_id=UID, revision="old", mboot_version="2.4-395e6cc",
              firmware="Melodee 1.6.1", size=0x78000, sectors=116, area_sha256="ab" * 32,
              backup=Path("pre.bin"), notes=[])
    text = sloop3_ui.install_plan_text(plan)
    assert "everything after it stay as they are" not in text and "are not written" in text
    assert "sets up its own storage on its first start" in text and "(Melodee 1.6.1)" in text and \
        "are gone afterwards" in text and "page “Backups”" in text
    hint = sloop3_ui.backup_hint(NS(firmware_key="melodee", connected=True))
    assert "Melodee" in hint and "Back up now" in hint


# ------------------------------------------------------------------------------------------------ D: plan texts

def _sloop3_plan(target="skiff", revision="old"):
    from fm1companion import firmware
    from fm1companion.flash import planner
    from fm1companion.tools import bootarea as BA
    prof = devices.Profile(id="e1e1e1e1e1", name="FM-1 Earlier", firmware="sloop3",
                           firmware_history=[{"time": 1.0, "firmware": "sloop3"}])
    text = BA.describe(BA.current(prof))
    st = planner.DeviceState(identity="", felucca=False, firmware_key="sloop3", boot_area=BA.MBOOT,
                             boot_area_text=text, revision=revision)
    return planner.plan_install(st, planner.Target.from_spec(firmware.spec(target), tag="v2.7")), text


def test_d_the_route_names_m_bootloader_once_and_the_tested_line_is_the_routes(english):
    from fm1companion.flash import tested
    plan, text = _sloop3_plan()
    assert plan.route == "download-mode"
    assert text.startswith("m-bootloader (")
    from fm1companion.flash import planner
    route = next(w for w in plan.warnings if w == planner.DOWNLOAD_ROUTE_TEXT.format(what=text))
    assert "m-bootloader (m-bootloader" not in route and "has m-bootloader (m-bootloader" not in route
    note = tested.plan_note(plan)
    assert note == tested.tool_note("mf_firmware") and "SLOOP 3 to Skiff 2.7" in note and \
        "have not appeared on a real FM-1 yet" in note
    assert "a switch to another firmware" not in note
    plan_new, _t = _sloop3_plan(revision="new")
    assert tested.plan_note(plan_new) == tested.tool_note("mf_new_chip_firmware")
    assert not tested.TOOLS_HARDWARE["mf_new_chip_firmware"][0]


def test_d_the_plan_dialog_does_not_deny_the_sloop3_backup(english):
    qapp()
    from fm1companion.ui import firmware_page as fp
    plan, _t = _sloop3_plan()
    dlg = fp.PlanDialog(None, lambda via, b=True: plan, "Skiff", True)
    note = dlg.backup_note.text()
    assert "cannot make a backup" not in note and "page “Backups”" in note and \
        "whole-flash backup in the plan keeps everything" in note
    assert "SLOOP 3 to Skiff 2.7" in dlg.tested_note
    dlg.deleteLater()


def test_d_the_unit_question_says_why_it_comes_again(english):
    from fm1companion.ui.device import unit_confirm_text
    text = unit_confirm_text(devices.Profile(id="e1e1e1e1e1", name="FM-1 Earlier"), "answer")
    assert "before every write" in text and "stays connected" in text


# ------------------------------------------------------------------------------------------------ E/G: "Back to …"

def _backup_with_boot(env, boot: bytes | None):
    """a whole-flash backup of the fake's unit, its boot area as given (None: the synthetic factory one)"""
    import fake_mflasher
    img = bytearray(fake_mflasher.synthetic_image())
    if boot is not None:
        img[:0x4000] = fake_mflasher.short_boot_area(bytes(img), boot)
    Path(str(env.state) + ".flash").write_bytes(bytes(img))
    return FJ.backup(env.job()).path


def _opts(env, **kw):
    from fm1companion.ui import flash_tools
    ctx = NS(settings=NS(backups_dir=str(env.backups)), profile=env.prof, mboot=None, is_virtual=False, **kw)
    return {o["key"]: o for o in flash_tools.way_back_options(ctx)}


BOOT = (ROOT / "fm1companion" / "tools" / "bundled" / "m-bootloader" / "fm1-uboot.boot").read_bytes()


def test_e_every_backup_is_offered_the_one_from_before_m_bootloader_recommended(env, english):
    env.prof.unit_id = UID
    env.prof.firmware = "skiff"
    first = _backup_with_boot(env, None)                       # (the morning: before m-bootloader)
    os.utime(first, (time.time() - 3600, time.time() - 3600))
    later = _backup_with_boot(env, BOOT)                       # (the evening: m-bootloader in the boot area)
    o = _opts(env)["backup"]
    assert [b["path"] for b in o["backups"]] == [later, first]                # newest first, both offered
    assert [b["pre_mboot"] for b in o["backups"]] == [False, True]
    assert o["recommended"] == first and o["path"] == first
    from fm1companion.ui.flash_tools import backup_label
    assert "from before m-bootloader" in backup_label(o["backups"][1])
    assert "with m-bootloader" in backup_label(o["backups"][0])
    v15 = _opts(env, sloop3=object())["v15"]
    assert not v15["ok"] and "SLOOP 3 has no update mode" in v15["why"] and "pre-selected" in v15["why"]


def test_g_without_a_backup_from_before_m_bootloader_it_says_so_and_points_to_the_import(env, english):
    from fm1companion import hardware
    env.prof.unit_id = UID
    env.prof.firmware = "sloop3"
    only = _backup_with_boot(env, BOOT)
    hardware.note_flash_id(env.prof, 0xB36014, "rom")          # (the newer unit: V15 never)
    opts = _opts(env)
    assert opts["backup"]["ok"] and opts["backup"]["recommended"] is None and opts["backup"]["path"] == only
    why = opts["v15"]["why"]
    assert not opts["v15"]["ok"] and "first choice" not in why
    assert "no whole-flash backup of this unit from before m-bootloader" in why and "Import a flash backup" in why


def test_e_the_v15_note_names_the_running_firmware(env, english):
    env.prof.unit_id = UID
    env.prof.firmware = "fm1-open"                             # (not a Felucca-family firmware: no usual way)
    bootarea.note(env.prof, bootarea.MBOOT, "midi-mode", bootloader="m-bootloader 2.4-395e6cc")
    why = _opts(env)["v15"]["why"]
    assert "SLOOP 3" not in why and "FM1 Open" in why


def test_e_the_dialog_lists_the_backups_and_preselects_the_recommended_one(env, english):
    qapp()
    from fm1companion.ui import flash_tools
    env.prof.unit_id = UID
    env.prof.firmware = "skiff"
    first = _backup_with_boot(env, None)
    os.utime(first, (time.time() - 3600, time.time() - 3600))
    _backup_with_boot(env, BOOT)
    opts = list(_opts(env).values())
    dlg = flash_tools.way_back_dialog(None, opts)
    from PyQt6.QtWidgets import QLabel, QListWidget, QRadioButton
    lst = dlg.findChild(QListWidget, "wayBackBackups")
    assert lst.count() == 2 and "recommended" in lst.currentItem().text() and \
        "from before m-bootloader" in lst.currentItem().text()
    assert not dlg.ok.isEnabled()                              # (an explicit choice)
    lst.setCurrentRow(1)
    lst.setCurrentRow(0)                                       # (choosing a backup chooses that way)
    o, path = dlg.choice()
    assert o["key"] == "backup" and path == Path(lst.item(0).data(256)) and dlg.ok.isEnabled()
    # the long titles are radio buttons with their route wrapped below – no cut-off push-button labels
    radios = {r.objectName(): r for r in dlg.findChildren(QRadioButton)}
    assert radios["wayBack-felucca"].text().startswith("Back to a Felucca-family firmware")
    assert all(lab.wordWrap() for lab in dlg.findChildren(QLabel) if lab.objectName().startswith("wayBackText-"))
    # (retest: "Install &amp; Versions" was shown literally – the route texts are plain text)
    from PyQt6.QtCore import Qt
    assert all(lab.textFormat() == Qt.TextFormat.PlainText and "&amp;" not in lab.text()
               for lab in dlg.findChildren(QLabel) if lab.objectName().startswith("wayBackText-"))
    dlg.deleteLater()
