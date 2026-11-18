# SPDX-License-Identifier: GPL-3.0-only
"""Findings of the first run on real hardware (Raspberry Pi 5, Raspberry Pi OS Bookworm, an earlier FM-1,
2026-11-18): the original V15 reports one fixed USB serial number for every FM-1, so a serial number must
never identify a unit by itself; and the smaller findings of that run."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fm1companion import devices, hardware                                   # noqa: E402

V15_SERIAL = "4150354433333610"         # what the original V15 reports on every FM-1 ("AP5D336\x10" as hex)


def v15_port(usb_path="3-1.3.1", client=32, serial=V15_SERIAL):
    return devices.PortInfo("FM-1:FM-1 MIDI 1 32:0", "FM-1:FM-1 MIDI 1 32:0", "FM-1", client, serial=serial,
                            usb_id="4c4a:c755", usb_path=usb_path, product="FM-1")


def store_with(tmp_path, profiles):
    f = tmp_path / "devices.json"
    f.write_text(json.dumps({"profiles": profiles}), "utf-8")
    return devices.ProfileStore(f)


def owner_store(tmp_path):
    """the owner's two units as the app had stored them: A (newer chip) carries the V15 serial number"""
    return store_with(tmp_path, [
        {"id": "a", "name": "FM-1 A", "serial": V15_SERIAL, "firmware": "jiant-fm", "usb_path": "1-1",
         "hw_revision": "new", "hw_revision_source": "user"},
        {"id": "b", "name": "FM-1 Schwarz/Grün", "firmware": "karma", "usb_path": "3-1.3.1",
         "hw_revision": "old", "hw_revision_source": "user", "last_seen": time.time()}])


# ---------------------------------------------------------------- finding 8: the shared serial number

@pytest.mark.parametrize("serial", [V15_SERIAL, "", "0000", "FFFFFFFFFFFFFFFF", "0000000000000000", "0000FM1DISK1"])
def test_shared_or_empty_serials_never_identify(serial):
    p = v15_port(serial=serial)
    p.usb_id, p.product = "1209:0001", "Felucca"
    assert devices.identifying_serial(p, [p]) == "" and devices.usable_serial(p, [p]) == ""
    assert not devices.serial_identifies(serial)


def test_no_serial_of_a_real_fm1_identifies_until_a_firmware_is_known_to_make_it_unique():
    assert devices.UNIQUE_SERIAL_FIRMWARE == ()
    for usb_id, product, update in (("4c4a:c755", "FM-1", False), ("4c4a:4155", "FM-1", False),
                                    ("4d4a:4155", "FM-1", True), ("1209:0002", "Felucca Update", True),
                                    ("1209:0001", "TransitFM1", False), ("1209:0001", "Melodee", False)):
        p = devices.PortInfo("x 32:0", "x 32:0", product, 32, serial="A1B2C3D4E5F60718", usb_id=usb_id,
                             usb_path="1-1", product=product, update_mode=update)
        assert devices.identifying_serial(p, [p]) == "", (usb_id, product)
    # the identity alone is enough to refuse it: original firmware and update loaders
    plain = devices.PortInfo("sim-in", "sim-out", "Sim", None, serial="SIM-0001")
    assert devices.identifying_serial(plain, [plain]) == "SIM-0001"          # the app's own simulated device
    for ident in ("FM-1_015", "FM-1_016", "ota-FM-1_900", "ota-FM-1_015"):
        assert devices.identifying_serial(plain, [plain], ident) == "", ident
    plain.update_mode = True
    assert devices.identifying_serial(plain, [plain]) == ""


def test_stored_shared_serial_is_no_longer_used_for_matching(tmp_path):
    store = owner_store(tmp_path)
    a = store.profiles["a"]
    assert a.serial == "" and a.serial_seen == V15_SERIAL                    # kept for the record, not for matching
    assert store.by_serial(V15_SERIAL) is None and a in store.without_serial()
    assert hardware.revision(a) == "new"                                     # nothing else of the profile changes
    store.save()
    again = devices.ProfileStore(store.path)
    assert again.profiles["a"].serial == "" and again.profiles["a"].serial_seen == V15_SERIAL
    demo = store_with(tmp_path, [{"id": "d", "name": "Demo", "serial": "DEMO-1"}])
    assert demo.by_serial("DEMO-1") is not None                              # the simulated devices keep theirs


def assign(store, port, identity="FM-1_015", ask=None):
    from fm1companion.ui.device import DeviceContext
    asked = []

    def _ask(cands):
        asked.append([c.id for c in cands])
        return ask(cands) if ask else cands[0]
    ctx = SimpleNamespace(port=port, ports=[port], identity=identity, store=store, info=None, link=None,
                          profile=None, virtual_profile=None, _ask_profile=_ask)
    DeviceContext._assign_profile(ctx)
    return ctx.profile, asked


def test_old_unit_under_v15_is_not_taken_for_profile_a(tmp_path):
    """the scenario of the hardware test: the earlier unit runs the original V15 and so shows the very serial number
    that profile A (the newer unit) had stored"""
    store = owner_store(tmp_path)
    prof, asked = assign(store, v15_port("3-1.3.1"))
    assert prof.id == "b" and not asked                # recognised by its USB port, as before – not as A
    a, b = store.profiles["a"], store.profiles["b"]
    assert (a.hw_revision, a.usb_path, a.firmware) == ("new", "1-1", "jiant-fm")     # A is untouched and stays new
    assert (b.hw_revision, b.hw_revision_source) == ("old", "user")                  # the stored answer stays
    # at a USB port the app does not know, the owner is asked – the serial number decides nothing
    store = owner_store(tmp_path)
    prof, asked = assign(store, v15_port("5-2"), ask=lambda c: next(x for x in c if x.id == "b"))
    assert asked == [["a", "b"]] and prof.id == "b"
    assert store.profiles["a"].hw_revision == "new" and store.profiles["b"].hw_revision == "old"
    assert len(store.profiles) == 2                    # and no third profile "by serial number"


def waiting(store, port, active=None, act=None):
    from fm1companion.ui.device import DeviceContext
    from fm1companion.ui.pages.recovery import RecoveryPage
    ctx = SimpleNamespace(store=store, ports=[port], port=None, profile=active, virtual_profile=None)
    ctx.profile_for_port = lambda p: DeviceContext.profile_for_port(ctx, p)
    return RecoveryPage.waiting_unit(SimpleNamespace(ctx=ctx), port, act or {})


def test_waiting_unit_with_the_shared_serial_is_never_certain(tmp_path, monkeypatch):
    monkeypatch.delenv("FM1C_DEMO", raising=False)
    hardware.SESSION_READS.clear()
    # 1. the dangerous direction: an "earlier" profile stored the shared serial; a unit that shows the same number
    #    (any FM-1 under V15) must not be taken for it – that would switch the rule for the newer chip off
    store = store_with(tmp_path, [{"id": "o", "name": "Old", "serial": V15_SERIAL, "usb_path": "1-1",
                                   "hw_revision": "old", "hw_revision_source": "user"}])
    store.profiles["o"].serial = V15_SERIAL            # (even if a profile still carried it)
    prof, certain, rev = waiting(store, v15_port("7-7"))
    assert not certain and rev == ""                   # unknown: the plan asks, nothing is unlocked
    # 2. the owner's case: the earlier unit waits at its own port, A is a candidate nowhere -> not weakened, and
    #    the earlier unit is not "new" for good
    store = owner_store(tmp_path)
    prof, certain, rev = waiting(store, v15_port("3-1.3.1"))
    assert not certain and rev == "" and prof is store.profiles["b"]
    assert store.profiles["b"].hw_revision == "old" and store.profiles["a"].hw_revision == "new"
    # 3. strictest reading: as soon as a "new" profile may be the waiting unit, it counts as new
    prof, certain, rev = waiting(store, v15_port("1-1"))
    assert not certain and rev == "new"
    prof, certain, rev = waiting(store, v15_port("9-9"), act={"device_id": "a"})
    assert not certain and rev == "new"
