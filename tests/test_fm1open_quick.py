# SPDX-License-Identifier: GPL-3.0-only
"""Pi hardware findings 44–48 (2026-11-19, one earlier FM-1 with M-VAVE's FM1 Open V14, one Raspberry Pi 5):
FM1 Open's own USB ID and serial number, its backup on the Overview and the Backups page, what of it ran on a real
FM-1, the labels of known devices, and the help on how to get to FM1 Open. Synthetic serial numbers only."""
from __future__ import annotations

import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fm1companion import devices                                             # noqa: E402
from test_pi_hardware import OLD_SERIAL, assign, fake_ctx, home, store_with  # noqa: E402,F401

pytest.importorskip("PyQt6.QtWidgets")


@pytest.fixture()
def en():
    """the English texts (the suite runs in German, conftest.py)"""
    from fm1companion import i18n
    before = i18n.current_language()
    i18n.set_language("en")
    yield
    i18n.set_language(before)


def open_port(serial=OLD_SERIAL, usb_path="3-1.3.1", usb_id="3654:7756", name="FM1 Community"):
    return devices.PortInfo(f"{name}:{name} MIDI 1 32:0", f"{name}:{name} MIDI 1 32:0", name, 32, serial=serial,
                            usb_id=usb_id, usb_path=usb_path, product=name, manufacturer="Jieli Technology",
                            bcd_device="0001")


# ---------------------------------------------------------------- 1: FM1 Open's USB ID, product name and serial

def test_fm1_open_usb_id_is_known_everywhere():
    from fm1companion import syscheck
    assert devices.FM1_USB_IDS["3654:7756"] == "fm1-open"
    assert devices.classify_port("FM1 Community:FM1 Community MIDI 1 32:0", "3654:7756") == "strong"
    assert devices.classify_port("USB Composite Device MIDI 1 32:0", "3654:7756") == "strong"
    assert syscheck.UsbDev("3-1", ids="3654:7756").is_fm1                      # (also without a product name)
    rule = (ROOT / "packaging" / "udev" / "60-fm1-companion.rules").read_text()
    assert re.search(r'ATTR\{idVendor\}=="3654", ATTR\{idProduct\}=="7756"', rule)


def test_fm1_open_reports_the_units_own_serial():
    p = open_port()
    assert devices.identifying_serial(p, [p]) == OLD_SERIAL and devices.serial_learnable(p, [p]) == OLD_SERIAL
    assert devices.unit_id_forms(OLD_SERIAL) & devices.unit_id_forms(devices.identifying_serial(p, [p]))
    twin = open_port(usb_path="1-2")                                     # the same number twice: identifies neither
    assert devices.identifying_serial(p, [p, twin]) == ""
    assert devices.firmware_from_port(p) == "fm1-open"
    assert devices.firmware_from_port(open_port(usb_id="")) == "fm1-open"      # by its product name alone
    for other in (open_port(usb_id="4c4a:c755", name="FM-1"), open_port(usb_id="1209:0001", name="Skiff"), None):
        assert devices.firmware_from_port(other) == ""


def test_fm1_open_unit_is_matched_by_serial_and_named_fm1_open(home):
    """finding 46: the profile made under the original V15 (which learned the unit's serial number) is the one – no
    question; and the first connect names FM1 Open instead of “unknown firmware”"""
    from fm1companion.ui.device import DeviceContext
    store = store_with(home, [{"id": "b", "name": "Black", "firmware": "stock-v15", "firmware_version": "V15",
                               "serial": OLD_SERIAL, "usb_path": "9-9"},
                              {"id": "o", "name": "Orange", "firmware": "skiff", "firmware_version": "v2.7",
                               "usb_path": "3-1.3.1"}])
    port = open_port()
    prof, asked = assign(store, port, identity="")
    assert prof is store.profiles["b"] and not asked and prof.usb_path == "3-1.3.1"
    # before (or without) the update handshake's answer: FM1 Open from the port's own USB data
    bare = SimpleNamespace(profile=None, info=None, firmware_guess=[], port=port)
    assert DeviceContext.firmware_key.fget(bare) == "fm1-open"
    for ident in ("", "FM-1_8000014"):
        p = store.profiles["b"]
        p.firmware, p.firmware_version = "stock-v15", "V15"
        ctx = fake_ctx(store, port, identity=ident, link=SimpleNamespace(closed=False, hs_flash=0), firmware_guess=[],
                       message=SimpleNamespace(emit=lambda *_a: None), changed=SimpleNamespace(emit=lambda: None),
                       firmware_ambiguous=False, protocol="generic")
        ctx._assign_profile = lambda c=ctx: DeviceContext._assign_profile(c)
        ctx._installed_here = DeviceContext._installed_here
        DeviceContext._generic(ctx, ident, ctx.link)
        assert ctx.profile is p and ctx.firmware_guess == ["fm1-open"] and p.firmware == "fm1-open", ident
        assert not ctx.asked, ident
    # a Felucca-family port at the same USB path gives no such hint
    assert devices.firmware_from_port(open_port(usb_id="1209:0001", name="Felucca")) == ""


# ---------------------------------------------------------------- 2: the FM1 Open backup is offered

def _ctx(key="fm1-open", **kw):
    base = dict(connected=False, midi_connected=True, port=open_port(), protocol="generic", sloop3=None,
                is_virtual=False, firmware_key=key, profile=None, info=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_back_up_now_is_offered_for_fm1_open_and_says_what_it_holds(monkeypatch, en):
    from fm1companion.ui import backups_page as BP
    c = _ctx()
    assert BP.can_back_up(c) and BP.no_backup_reason(c) == "" and BP.fm1open_target(c)
    note = BP._(BP.FM1OPEN_BACKUP_NOTE)
    for part in ("16 project slots", "DX7 library", "sample bank", "working area", "only the transfer of the sample "
                 "bank", "only in the simulation"):
        assert part in note, part
    # not FM1 Open: still no backup (the original firmware has no protocol for it); update mode, virtual: neither
    for other in (_ctx("stock-v15"), _ctx("", ), _ctx(protocol="update"), _ctx(is_virtual=True),
                  _ctx(midi_connected=False)):
        assert not BP.fm1open_target(other)
    assert "no editor protocol" in BP.no_backup_reason(_ctx("stock-v15"))


def test_fm1_open_backup_asks_first_and_goes_through_fm1openbk(monkeypatch, en, tmp_path):
    from fm1companion.ui import backups_page as BP
    said = []
    monkeypatch.setattr(BP, "confirm", lambda parent, title, text, ok=None, danger=False: said.append(text) or False)
    jobs = []
    c = _ctx(job=lambda *a, **k: jobs.append(a) or SimpleNamespace(ok=False),
             settings=SimpleNamespace(backups_dir=tmp_path, keep_backups=5), device_folder=lambda base: tmp_path)
    assert BP.make_backup(c, None) is None and not jobs and "cannot be put back" in said[0]
    monkeypatch.setattr(BP, "confirm", lambda *a, **k: True)
    got = {}

    def capture(link, folder, prof, fw_key, version, note, kind, progress=None, abort=None):
        got["fw"] = fw_key
        raise RuntimeError("stop here")
    monkeypatch.setattr(BP.AB, "capture_to_file", capture)

    def job(parent, title, work, **kw):
        with pytest.raises(RuntimeError):
            work(SimpleNamespace(log=SimpleNamespace(emit=lambda *_a: None),
                                 progress=SimpleNamespace(emit=lambda *_a: None)), object())
        return SimpleNamespace(ok=False)
    c.job = job
    BP.make_backup(c, None)
    assert got["fw"] == "fm1-open"                    # -> backup.capture picks fm1openbk by this key


def test_fm1_open_restore_on_the_backups_page_stays_blocked(monkeypatch, en, tmp_path):
    """restore was not possible from the Backups page before (no_backup_reason) – now that the backup is, the
    restore still is not (no new write path): the Studio's “Write back” is named instead"""
    from fm1companion.ui import backups_page as BP, sloop3_ui
    errors = []
    monkeypatch.setattr(BP, "show_error", lambda parent, title, text: errors.append((title, text)))
    monkeypatch.setattr(sloop3_ui, "handles_restore", lambda *a: False)
    monkeypatch.setattr(BP.backup, "load_file", lambda *a: pytest.fail("the backup must not even be loaded"))
    page = SimpleNamespace(ctx=_ctx(), _sel_ok=lambda: tmp_path / "x.json", refresh=lambda: None)
    BP.BackupsPage._restore(page)
    assert errors and errors[0][0] == "Restore not possible" and "Write back to the FM-1" in errors[0][1]


def test_overview_backup_card_explains_the_fm1_open_backup(monkeypatch, en, tmp_path):
    from fm1companion.ui.overview import OverviewPage
    page = SimpleNamespace(ctx=_ctx(profile=object(), settings=SimpleNamespace(backups_dir=tmp_path),
                                    device_folder=lambda base: Path(base)),
                           bk_text=SimpleNamespace(setText=lambda t: setattr(page, "t", t)))
    OverviewPage._backup_text(page)
    assert "Best make one now" in page.t and "DX7 library" in page.t and "no editor protocol" not in page.t


# ---------------------------------------------------------------- 3: what of FM1 Open ran on a real FM-1

def test_fm1open_hardware_table_matches_untested_md():
    from fm1companion.flash import tested
    doc = (ROOT / "docs" / "UNTESTED.md").read_text("utf-8")
    sec = doc[doc.index("### FM1 Open\n"):doc.index("### SLOOP 3 and m-bootloader")]
    rows = dict(re.findall(r"^\| `(\w+)` – [^|]+\| (run twice|run once|not run)\b[^|]*\|$", sec, re.M))
    assert rows == {k: (("run once" if k == "fm1open_bank_restore" else "run twice") if ok else "not run")
                    for k, (ok, _l) in tested.FM1OPEN_HARDWARE.items()}
    for key, (_ok, label) in tested.FM1OPEN_HARDWARE.items():
        assert f"| `{key}` – {label} |" in sec, key
        assert key not in tested.STEPS
    assert [k for k, (ok, _l) in tested.FM1OPEN_HARDWARE.items() if ok] == [
        "fm1open_probe", "fm1open_bank_read", "fm1open_bank_write", "fm1open_bank_restore"]
    for needle in ("2026-11-19", "one Raspberry Pi 5", "fm1open.py", "Not through\nthe app's pages", "446464",
                   "version 4", "7 samples", "one of 8", "identical", "“ready”"):
        assert needle in sec, needle
    assert "only tested against our own simulator" not in doc


# ---------------------------------------------------------------- 4: labels

def test_firmware_labels_never_show_a_key_or_a_doubled_version():
    from fm1companion.ui.device import firmware_name, firmware_version_text, last_firmware_text
    P = SimpleNamespace
    assert last_firmware_text(P(firmware="fm1-open", firmware_version="V14")) == "FM1 Open V14"
    v15 = last_firmware_text(P(firmware="stock-v15", firmware_version="V15"))     # (its name carries the version)
    assert v15 == firmware_name("stock-v15") and v15.endswith("V15") and "15 15" not in v15 and "V15 V15" not in v15
    assert last_firmware_text(P(firmware="skiff", firmware_version="v2.7")) == "Skiff 2.7"
    assert last_firmware_text(P(firmware="sloop3", firmware_version="v3.4.0")) == "SLOOP 3 3.4.0"
    assert last_firmware_text(P(firmware="", firmware_version="")) == ""
    assert firmware_version_text("Original firmware V15", "15") == "Original firmware V15"
    assert firmware_version_text("Melodee", "v1.6.0-rc.7") == "Melodee 1.6.0-rc.7"
    assert firmware_version_text("SLOOP 3", "v3.4.0") == "SLOOP 3 3.4.0"         # (“3” in the name is no version)
    src = (ROOT / "fm1companion" / "ui" / "overview.py").read_text("utf-8")
    known = src[src.index("connected = {pr.id"):src.index("self.others_text.setText")]
    assert "last_firmware_text(prof)" in known and "prof.firmware}" not in known


# ---------------------------------------------------------------- 5: help

@pytest.mark.parametrize("lang,needles", [
    ("en", ("Getting to FM1 Open today", "Force flash", "original V15", "boot area", "full flash backup")),
    ("de", ("So kommst du heute zu FM1 Open", "Force flash", "Original-V15", "Startbereich", "Flash-Sicherung")),
    ("es", ("Cómo llegar hoy a FM1 Open", "Force flash", "V15 original", "zona de arranque", "copia completa"))])
def test_help_says_how_to_get_to_fm1_open_and_back(lang, needles):
    page = (ROOT / "fm1companion" / "data" / "help" / lang / "13-studio.md").read_text("utf-8")
    for n in needles:
        assert n in page, (lang, n)
