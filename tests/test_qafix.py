# SPDX-License-Identifier: GPL-3.0-only
"""QA fixes without UI: back up/restore a sample slot, read/write back the song chain,
engine names, read-only preset library.
  python3 -m pytest tests/test_qafix.py -q
"""
from __future__ import annotations

import errno
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fm1companion import midi_import as MI, presetlib as pl, samples as S  # noqa: E402
from fm1companion.demo_device import FakeFM1                                # noqa: E402
from fm1companion.transport import Link                                     # noqa: E402
from test_sloop_backup import LocalTransport, put_sample, sloop             # noqa: E402


def test_sample_slot_read_restore_sloop():
    _, link, _ = sloop()
    assert S.read_slot(link, 1, "sloop") == b""
    put_sample(link, 1, b"KICK")
    old = S.read_slot(link, 1, "sloop")
    assert len(old) > 512 and old[8:12] == b"KICK"
    put_sample(link, 1, b"SNARE")
    assert S.read_slot(link, 1, "sloop")[8:13] == b"SNARE"
    S.restore_slot(link, 1, old)
    assert S.read_slot(link, 1, "sloop") == old
    S.restore_slot(link, 1, b"")
    assert S.read_slot(link, 1, "sloop") == b""


def test_song_read_restore_melodee():
    link = Link(LocalTransport(FakeFM1()))
    assert MI.read_song(link).rows == []
    rows = [([1, 0, 0, 0], 2), ([2, 3, 0, 0], 1)]
    assert MI.write_song(link, rows) == 0
    snap = MI.read_song(link)
    assert snap.rows == rows
    MI.write_song(link, [([5, 5, 5, 5], 4)])
    assert MI.restore_song(link, snap) == 0
    assert MI.read_song(link).rows == rows


def test_engine_names_shared():
    assert pl.engine_display_name(12) == "FM6"
    assert pl.engine_display_name(12, ["A", "B"]) == "FM6"          # short INFO list: fixed names
    long = [f"E{i}" for i in range(16)]
    assert pl.engine_display_name(3, long) == "E3"
    assert pl.engine_display_name(99) == "Engine 99"
    assert pl.Caps(engines=long).engine_name(3) == pl.engine_display_name(3, long)


def test_library_on_readonly_root(tmp_path, monkeypatch):
    root = tmp_path / "ro" / "Presets"
    real = os.mkdir

    def deny(p, *a, **k):
        if str(p).startswith(str(tmp_path / "ro")):
            raise PermissionError(errno.EACCES, "Permission denied", str(p))
        return real(p, *a, **k)
    monkeypatch.setattr(os, "mkdir", deny)
    lib = pl.Library(root)                       # must not raise
    assert lib.all() == []


# ================= Pi hardware test 2.6.1, finding 7: what the app shows after an installation =================
def test_firmware_is_shown_under_its_own_name():
    """Skiff answers INFO with "FELUCCA v2.7" (the family's name): the overview's version, the title and the
    "connected" notice showed that raw text. With the recognised firmware it reads "Skiff 2.7"; everything else
    stays as fw_label showed it."""
    pytest.importorskip("PyQt6.QtWidgets")
    from fm1companion.ui.common import device_title, fw_label
    assert fw_label("FELUCCA v2.7", "skiff") == "Skiff 2.7"
    assert fw_label("FELUCCA v2.7") == "FELUCCA v2.7"                       # no key: as reported
    assert fw_label("FELUCCA v2.1.1", "felucca") == "FELUCCA v2.1.1"        # Felucca itself
    assert fw_label("FELUCCA v2.7", "no-such-firmware") == "FELUCCA v2.7"
    assert fw_label("FELUCCA SLOOP 2.8.3", "sloop") == fw_label("FELUCCA SLOOP 2.8.3") == "SLOOP 2.8.3"
    assert fw_label("FELUCCA KARMA 3.0.5", "karma") == "KARMA 3.0.5"
    assert fw_label("MELODEE v1.6.1", "melodee") == "MELODEE v1.6.1"
    assert fw_label("", "skiff") == ""

    class Ctx:
        firmware_key = "skiff"
        info = type("I", (), {"version": "FELUCCA v2.7"})()

        def title(self):
            return "FM-1 Earlier – FELUCCA v2.7"
    assert device_title(Ctx()) == "FM-1 Earlier – Skiff 2.7"
    Ctx.firmware_key = ""
    assert device_title(Ctx()) == "FM-1 Earlier – FELUCCA v2.7"
    src = (ROOT / "fm1companion/ui/overview.py").read_text("utf-8") + (ROOT / "fm1companion/ui/main_window.py").read_text("utf-8")
    assert "fw_label(c.info.version)" not in src, "overview/main window: the recognised firmware belongs to the label"


def test_device_selection_names_the_firmware_not_the_usb_port():
    """The selection said "<profile> · <USB port name>": still "FM-1 KARMA" after Skiff was installed, later
    "Felucca" (Skiff's USB name)."""
    pytest.importorskip("PyQt6.QtWidgets")
    from fm1companion.ui.main_window import MainWindow

    def port(label, key, update=False):
        return type("P", (), {"label": label, "key": key, "update_mode": update})()

    def prof(fw):
        return type("Pr", (), {"firmware": fw})()
    active = port("FM-1 KARMA", "a")
    win = type("W", (), {"ctx": type("C", (), {"port": active, "firmware_key": "skiff"})()})()
    f = MainWindow._port_firmware
    assert f(win, active, prof("karma")) == "Skiff"                 # the active device: what runs now, not the port's name
    win.ctx.firmware_key = "karma"
    assert f(win, active, prof("karma")) == "KARMA"
    assert f(win, port("Felucca", "b"), prof("skiff")) == "Skiff"   # another device: what its profile knows
    assert f(win, port("Felucca", "b"), None) == "Felucca"          # nothing known: the USB name, as before
    assert f(win, port("FM-1 update", "c", update=True), prof("skiff")) == "FM-1 update"
    assert f(win, port("Melodee (Demo)", "d"), prof("melodee")) == "Melodee (Demo)"
    src = (ROOT / "fm1companion/ui/main_window.py").read_text("utf-8")
    dev_changed = src.split("def _dev_changed", 1)[1].split("\n    def ", 1)[0]
    assert "self._fill_devices()" in dev_changed                    # … and it is filled again when the device changes


def test_splash_has_a_title_of_its_own():
    """Qt gives a window without a title the application's name; the main window has the same title and appears
    while the splash is still shown – KWin then calls the main window "FM-1 Companion <2>"."""
    pytest.importorskip("PyQt6.QtWidgets")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])  # noqa: F841
    from fm1companion import APP_NAME
    from fm1companion.ui.splash import Splash
    s = Splash()
    try:
        assert s.windowTitle() and s.windowTitle() != APP_NAME and s.windowTitle().startswith(APP_NAME)
    finally:
        s.deleteLater()
