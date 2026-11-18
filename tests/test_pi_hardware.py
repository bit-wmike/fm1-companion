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

OLD_SERIAL = "4150354433333610"         # the earlier (black/green) unit under the original V15
NEW_SERIAL = "550B515AA1CA0037"         # the newer (orange) unit under its factory firmware – both measured 2026-11-18


def stock_port(serial, usb_path="3-1.3.1", client=32, usb_id="4c4a:c755"):
    return devices.PortInfo("FM-1:FM-1 MIDI 1 32:0", "FM-1:FM-1 MIDI 1 32:0", "FM-1", client, serial=serial,
                            usb_id=usb_id, usb_path=usb_path, product="FM-1")


def felucca_port(name="Felucca", usb_path="3-1.3.1", serial="", client=32):
    return devices.PortInfo(f"{name} 32:0", f"{name} 32:0", name, client, serial=serial, usb_id="1209:0001",
                            usb_path=usb_path, product=name)


def store_with(tmp_path, profiles):
    f = tmp_path / "devices.json"
    f.write_text(json.dumps({"profiles": profiles}), "utf-8")
    return devices.ProfileStore(f)


def fake_ctx(store, port=None, identity="", **kw):
    from fm1companion.ui.device import DeviceContext
    asked = []
    ctx = SimpleNamespace(port=port, ports=[port] if port else [], identity=identity, store=store, info=None,
                          link=None, profile=None, virtual_profile=None, session_units={}, unit_candidates=[],
                          settings=SimpleNamespace(last_device="", backups_dir=store.path.parent / "Backups"),
                          asked=asked, answer=None, SESSION_UNIT_SECS=DeviceContext.SESSION_UNIT_SECS,
                          is_virtual=False, port_answers={}, pending_restore_check="", pending_restore_profile="")

    def _ask(cands, guess=None):
        asked.append(([c.id for c in cands], guess.id if guess else None))
        return ctx.answer(cands) if ctx.answer else None
    ctx._ask_profile = _ask
    ctx._match_profile = lambda p: DeviceContext._match_profile(ctx, p)
    ctx._new_profile = lambda name="": DeviceContext._new_profile(ctx, name)
    ctx.note_installed = lambda prof, usb: DeviceContext.note_installed(ctx, prof, usb)
    ctx._note_found = DeviceContext._note_found
    ctx.firmware_key, ctx.protocol = "", ""
    ctx._log_connected = lambda: DeviceContext._log_connected(ctx)
    for k, v in kw.items():
        setattr(ctx, k, v)
    return ctx


def assign(store, port, identity="FM-1_015", ask=None, ctx=None):
    from fm1companion.ui.device import DeviceContext
    ctx = ctx or fake_ctx(store)
    ctx.port, ctx.ports, ctx.identity, ctx.answer = port, [port], identity, ask
    del ctx.asked[:]
    DeviceContext._assign_profile(ctx)
    return ctx.profile, list(ctx.asked)


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))       # (the journal of the test, not the real one)
    monkeypatch.delenv("FM1C_DEMO", raising=False)
    hardware.SESSION_READS.clear()
    return tmp_path


# ---------------------------------------------------------------- findings 8, 15, 17: which serial number says what

@pytest.mark.parametrize("serial", ["", "0000", "FFFFFFFFFFFFFFFF", "0000000000000000", "0000FM1DISK1",
                                    "TransitFM1 0.1.4"])
def test_empty_and_constant_serials_never_identify(serial):
    for p in (stock_port(serial), felucca_port(serial=serial),
              devices.PortInfo("sim-in", "sim-out", "Sim", None, serial=serial)):
        assert devices.identifying_serial(p, [p]) == "" and devices.usable_serial(p, [p]) == ""
        assert devices.serial_learnable(p, [p]) == ""


def test_which_firmware_serial_counts():
    # the original firmware reports the unit's own number: the two measured ones differ and both count
    for s in (OLD_SERIAL, NEW_SERIAL):
        for uid in ("4c4a:c755", "4c4a:4155"):
            p = stock_port(s, usb_id=uid)
            assert devices.identifying_serial(p, [p]) == s and devices.serial_learnable(p, [p]) == s
    # M-VAVE's update loader: may confirm a profile that has the number, but nothing is learned from it
    ld = stock_port(OLD_SERIAL, usb_id="4d4a:4155")
    ld.update_mode = True
    assert devices.identifying_serial(ld, [ld]) == OLD_SERIAL and devices.serial_learnable(ld, [ld]) == ""
    assert devices.serial_learnable(stock_port(OLD_SERIAL), [], "ota-FM-1_015") == ""
    # the Felucca family reports none or a constant: whatever shows up there identifies nobody
    for uid in ("1209:0001", "1209:0002", "dead:beef", ""):
        p = felucca_port(serial="A1B2C3D4E5F60718")
        p.usb_id = uid
        assert devices.identifying_serial(p, [p]) == "", uid
    # the same number on two connected devices identifies neither
    a, b = stock_port(OLD_SERIAL, "1-1"), stock_port(OLD_SERIAL, "1-2", client=36)
    assert devices.identifying_serial(a, [a, b]) == ""
    # the app's own simulated devices (no kernel client, no USB ID)
    sim = devices.PortInfo("demo-in", "demo-out", "Melodee (Demo)", None, serial="DEMO-1", usb_path="demo-usb-1")
    assert devices.identifying_serial(sim, [sim]) == "DEMO-1"


def test_a_constant_serial_stored_by_an_earlier_version_is_dropped_on_load(tmp_path):
    store = store_with(tmp_path, [{"id": "t", "name": "T", "serial": "TransitFM1 0.1.4"},
                                  {"id": "o", "name": "O", "serial": OLD_SERIAL}, {"id": "d", "name": "D", "serial": "DEMO-1"}])
    assert [store.profiles[k].serial for k in "tod"] == ["", OLD_SERIAL, "DEMO-1"]


def test_ghost_profile_sequence_of_2026_11_05(home):
    """devices.json of the owner: one FM-1, profile "FM-1 Schwarz/Grün", made under a Felucca-family firmware (no
    serial number). The app installs V16 -> the unit re-appears with the original firmware's serial number -> the
    app made a second profile "FM-1 A" for the same unit and booked the next installations there.
    Replayed: it stays one profile, which learns the serial number."""
    store = store_with(home, [{"id": "b", "name": "FM-1 Schwarz/Grün", "firmware": "melodee",
                               "identity": "FM-1_91061", "usb_path": "3-1"}])
    b = store.profiles["b"]
    ctx = fake_ctx(store)
    ctx.note_installed(b, "3-1")                       # install_flow: V16 was written at USB port 3-1
    prof, asked = assign(store, stock_port(OLD_SERIAL, "3-1", usb_id="4c4a:4155"), identity="FM-1_016", ctx=ctx)
    assert prof is b and not asked and len(store.profiles) == 1
    assert b.serial == OLD_SERIAL                      # learned, like the USB port
    assert json.loads(store.path.read_text("utf-8"))["profiles"][0]["serial"] == OLD_SERIAL
    # then V15 (same serial number: recognised by it, at any USB port, after a restart of the app)
    prof, asked = assign(store, stock_port(OLD_SERIAL, "5-2"), identity="FM-1_015")
    assert prof is b and not asked and b.usb_path == "5-2"
    # then JIANT by the app (Felucca family: no serial number again) – a sighting without a serial number does not
    # un-match the profile that has one
    ctx = fake_ctx(store)
    ctx.note_installed(b, "5-2")
    prof, asked = assign(store, felucca_port("JIANT FM", "5-2"), identity="FM-1_9360", ctx=ctx)
    assert prof is b and not asked and len(store.profiles) == 1 and b.serial == OLD_SERIAL
    assert ctx.session_units == {}                     # "just installed here" holds for one appearance
    # later starts of the app: same USB port, the identity the profile knows -> the same profile
    b.identity = "FM-1_9360"
    prof, asked = assign(store, felucca_port("JIANT FM", "5-2"), identity="FM-1_9360")
    assert prof is b and not asked and len(store.profiles) == 1


def test_interrupted_switch_keeps_the_profile_by_the_journal(home):
    """the V15 step is done, the app was closed: the journal's chain names device and USB port"""
    from fm1companion.flash import journal
    store = store_with(home, [{"id": "b", "name": "B", "firmware": "karma", "identity": "FM-1_926", "usb_path": "3-1"},
                              {"id": "c", "name": "C", "firmware": "melodee", "identity": "FM-1_91061", "usb_path": "9-9"}])
    journal.Journal().begin_chain(key="skiff", name="Skiff", tag="v2.7", file="", device_id="b", usb_path="3-1")
    prof, asked = assign(store, stock_port(OLD_SERIAL, "3-1"), identity="FM-1_015")
    assert prof is store.profiles["b"] and not asked and prof.serial == OLD_SERIAL and len(store.profiles) == 2


def test_two_real_units_with_different_stock_serials_stay_two_profiles(home):
    store = store_with(home, [])
    old, asked = assign(store, stock_port(OLD_SERIAL, "3-1.3.1"))
    assert not asked and old.serial == OLD_SERIAL      # the very first FM-1 needs no question
    new, asked2 = assign(store, stock_port(NEW_SERIAL, "3-1.3.1"))                # the same cable, the same hub port
    assert not asked2 and new is not old and new.serial == NEW_SERIAL and len(store.profiles) == 2
    hardware.set_revision(old, "old", "user")
    hardware.set_revision(new, "new", "user")
    for serial, prof in ((OLD_SERIAL, old), (NEW_SERIAL, new), (OLD_SERIAL, old)):
        got, asked = assign(store, stock_port(serial, "3-1.3.1"))
        assert got is prof and not asked
    assert (hardware.revision(old), hardware.revision(new)) == ("old", "new") and len(store.profiles) == 2
    # "just installed at this port" never overrides another serial number: it is another FM-1
    ctx = fake_ctx(store)
    ctx.note_installed(old, "3-1.3.1")
    got, asked = assign(store, stock_port(NEW_SERIAL, "3-1.3.1"), ctx=ctx)
    assert got is new


def test_unknown_serial_beside_a_profile_without_one_is_asked_about(home):
    """the owner's data today: "FM-1 Schwarz/Grün" (earlier unit) has no serial number yet. An FM-1 on the original
    firmware appears with a serial number no profile has: that unit after a return to V15 – or the new orange
    one? The app asks; until answered nothing is assumed, and nothing weakens "new"."""
    from fm1companion.ui.firmware_page import device_revision

    def owner():
        return store_with(home, [{"id": "b", "name": "FM-1 Schwarz/Grün", "firmware": "skiff", "identity": "FM-1_727",
                                  "usb_path": "3-1.3.1", "hw_revision": "old", "hw_revision_source": "user"}])
    for serial in (NEW_SERIAL, OLD_SERIAL):
        store = owner()
        ctx = fake_ctx(store)
        prof, asked = assign(store, stock_port(serial), ctx=ctx)                 # (even at that profile's USB port)
        assert asked == [(["b"], "b")] and prof is None and len(store.profiles) == 1
        assert store.profiles["b"].serial == "" and device_revision(ctx) == ""    # unknown: confirmation in the plan
        store.profiles["b"].hw_revision = "new"
        assert device_revision(ctx) == "new"                                     # strictest candidate
    # answered "a new FM-1": its own profile with its own serial number; the earlier unit's answer stays
    store = owner()
    (home / "Backups" / "FM-1 A").mkdir(parents=True)                            # left over from a forgotten profile
    ctx = fake_ctx(store)
    orange, asked = assign(store, stock_port(NEW_SERIAL), ctx=ctx, ask=lambda c: ctx._new_profile())
    assert orange.serial == NEW_SERIAL and orange.name == "FM-1 B" and hardware.revision(orange) == "unknown"
    assert store.profiles["b"].hw_revision == "old" and store.profiles["b"].serial == ""
    # answered "it is my earlier unit": that profile learns the number, no second profile
    store = owner()
    prof, asked = assign(store, stock_port(OLD_SERIAL), ask=lambda c: c[0])
    assert prof.id == "b" and prof.serial == OLD_SERIAL and len(store.profiles) == 1
    # and from then on the other unit is told apart without a question
    orange, asked = assign(store, stock_port(NEW_SERIAL))
    assert not asked and orange.id != "b" and orange.serial == NEW_SERIAL


def test_without_serial_the_app_asks_instead_of_guessing(home):
    store = store_with(home, [{"id": "b", "name": "B", "firmware": "skiff", "identity": "FM-1_727",
                               "usb_path": "3-1.3.1", "serial": OLD_SERIAL, "hw_revision": "old"}])
    # same USB port, same identity: the same unit
    prof, asked = assign(store, felucca_port(), identity="FM-1_727")
    assert prof.id == "b" and not asked
    # another USB port, or another identity than the profile knows: asked, the likely one preselected
    for port, ident in ((felucca_port(usb_path="7-1"), "FM-1_727"), (felucca_port("Melodee"), "FM-1_91061")):
        prof, asked = assign(store, port, identity=ident)
        assert asked == [(["b"], "b")] and prof is None
        prof, asked = assign(store, port, identity=ident, ask=lambda c: c[0])
        assert prof.id == "b"
    assert len(store.profiles) == 1


def test_default_name_avoids_profiles_and_backup_folders(tmp_path):
    store = store_with(tmp_path, [{"id": "x", "name": "FM-1 B"}])
    assert store.create(folders=["FM-1 A", "fm-1 c", "FM-1 Orange"]).name == "FM-1 D"
    assert store.create().name == "FM-1 A"             # (without the folder list: as before)


def test_possible_duplicate_is_only_a_hint(tmp_path):
    store = store_with(tmp_path, [
        {"id": "b", "name": "FM-1 Schwarz/Grün", "usb_path": "3-1"},
        {"id": "a", "name": "FM-1 A", "serial": OLD_SERIAL, "usb_path": "3-1"},
        {"id": "c", "name": "Other", "serial": NEW_SERIAL, "usb_path": "8-8"},
        {"id": "d", "name": "No port"}])
    P = store.profiles
    assert devices.possible_duplicate(store, P["b"]) is P["a"] and devices.possible_duplicate(store, P["a"]) is P["b"]
    assert devices.possible_duplicate(store, P["c"]) is None and devices.possible_duplicate(store, P["d"]) is None
    assert len(store.profiles) == 4                    # nothing is merged or removed by looking


def waiting(store, port, active=None, act=None):
    from fm1companion.ui.device import DeviceContext
    from fm1companion.ui.pages.recovery import RecoveryPage
    ctx = SimpleNamespace(store=store, ports=[port], port=None, profile=active, virtual_profile=None)
    ctx.profile_for_port = lambda p: DeviceContext.profile_for_port(ctx, p)
    return RecoveryPage.waiting_unit(SimpleNamespace(ctx=ctx), port, act or {})


def test_waiting_unit_is_certain_only_by_its_own_serial(home):
    store = store_with(home, [
        {"id": "o", "name": "Old", "serial": OLD_SERIAL, "usb_path": "3-1.3.1", "hw_revision": "old"},
        {"id": "n", "name": "New", "serial": NEW_SERIAL, "usb_path": "1-1", "hw_revision": "new"},
        {"id": "x", "name": "No serial", "usb_path": "4-4", "hw_revision": "old"}])
    loader = stock_port(NEW_SERIAL, "3-1.3.1", usb_id="4d4a:4155")               # at the OLD unit's USB port
    loader.update_mode = True
    prof, certain, rev = waiting(store, loader)
    assert prof.id == "n" and certain and rev == "new"                           # the number wins over the port
    # a Felucca loader shows no serial number: by USB port only, so never certain and never "old"
    fl = felucca_port("Felucca Update", "4-4")
    fl.usb_id, fl.update_mode = "1209:0002", True
    prof, certain, rev = waiting(store, fl)
    assert not certain and rev == ""
    fl.usb_path = "1-1"
    assert waiting(store, fl)[1:] == (False, "new")
    # a serial number no profile has, at the port of an "old" profile without one: not that profile for certain
    unknown = stock_port("77AA55CC33EE1199", "4-4", usb_id="4d4a:4155")
    unknown.update_mode = True
    prof, certain, rev = waiting(store, unknown)
    assert not certain and rev == ""
    assert waiting(store, unknown, act={"device_id": "n"})[1:] == (False, "new")


# ---------------------------------------------------------------- finding 5: the power confirmation behind a hub

def switch_plan(hubs):
    from fm1companion import firmware
    from fm1companion.flash import planner as P
    st = P.DeviceState(identity="FM-1_926", felucca=True, firmware_key="karma", firmware_name="KARMA",
                       version="FELUCCA KARMA 3.0.5", pinned=True, revision="old", hubs=hubs)
    return P, P.plan_install(st, P.Target.from_spec(firmware.spec("skiff"), tag="v2.7"), via_v15=None, backup=True)


def test_power_confirmation_does_not_claim_a_direct_cable_behind_a_hub():
    P, direct = switch_plan(0)
    assert "power" in direct.confirmations and "power_hub" not in direct.confirmations
    P, hub = switch_plan(2)
    assert "power_hub" in hub.confirmations and "power" not in hub.confirmations
    text = P.CONFIRM_TEXT["power_hub"]                 # (the tests run in German)
    assert "steckt direkt am Rechner" in P.CONFIRM_TEXT["power"]
    assert "hub" in text.lower() and "steckt direkt" not in text and "plugged directly" not in text


# ---------------------------------------------------------------- finding 6: GitHub's digest as the checksum

SKIFF_SHA = "82e9068a" + "0" * 52 + "5d75"        # (shape of the real one: 82e9068a…5d75)


def test_asset_digests_are_read_strictly():
    from fm1companion import firmware
    assets = [{"name": "skiff-2.7.fwsc", "size": 610429, "digest": "sha256:" + SKIFF_SHA.upper()},
              {"name": "old.fwsc", "digest": None}, {"name": "x.fwsc", "digest": "sha512:" + "a" * 128},
              {"name": "y.fwsc", "digest": "sha256:abc"}, {"name": "z.fwsc"}, "junk", None]
    assert firmware.asset_digests(assets) == {"skiff-2.7.fwsc": SKIFF_SHA}
    assert firmware.asset_digests(None) == {}


def test_release_list_keeps_the_digest_and_download_checks_it(tmp_path, monkeypatch):
    import hashlib
    from fm1companion import firmware
    from fm1companion.flash import package as pk
    data = pk.build_package("FM-1_727", seed=5)
    sha = hashlib.sha256(data).hexdigest()
    spec = firmware.spec("skiff")
    monkeypatch.setattr(firmware, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(firmware, "_package_folder", lambda s, rel: tmp_path / "fw" / rel.tag)
    api = [{"tag_name": "v2.7", "published_at": "2026-11-10T00:00:00Z", "body": "",
            "assets": [{"name": "skiff-2.7.fwsc", "browser_download_url": "https://example.invalid/skiff-2.7.fwsc",
                        "size": len(data), "digest": "sha256:" + sha}]}]
    monkeypatch.setattr(firmware, "_http_json", lambda url: api)
    rel = firmware.fetch_releases(spec, use_cache_secs=0)[0]
    assert rel.digests == {"skiff-2.7.fwsc": sha} and "SHA256SUMS" not in rel.assets
    assert firmware.fetch_releases(spec, use_cache_secs=600)[0].digests == rel.digests       # survives the cache
    served = {"data": data}

    def fetch(url, dest, progress=None):
        Path(dest).write_bytes(served["data"])
    monkeypatch.setattr(firmware, "_fetch", fetch)
    logs = []
    pkg = firmware.download(spec, rel, log=logs.append)
    assert pkg.verified and pkg.source == "digest" and any("GitHub" in x for x in logs)
    # a download that does not match the digest is deleted, not installed
    pkg.fwsc.unlink()
    served["data"] = data[:-9] + b"tampered!"
    with pytest.raises(RuntimeError):
        firmware.download(spec, rel, log=logs.append)
    assert not pkg.fwsc.exists()
    # so is an intact-looking package of another content that was lying there from before
    other = pk.build_package("FM-1_727", seed=6)
    pkg.fwsc.write_bytes(other)
    with pytest.raises(RuntimeError):
        firmware.download(spec, rel, log=logs.append)
    assert not pkg.fwsc.exists()
    # without a digest and without SHA256SUMS nothing is claimed
    rel.digests = {}
    served["data"] = data
    plain = firmware.download(spec, rel, log=logs.append)
    assert not plain.verified and plain.source == ""


def test_plan_says_where_the_checksum_comes_from():
    from fm1companion import firmware
    from fm1companion.flash import planner as P
    st = P.DeviceState(identity="FM-1_727", felucca=True, firmware_key="skiff", firmware_name="Skiff", pinned=True,
                       revision="old")

    def fetch_text(source, checksum=True):
        t = P.Target.from_spec(firmware.spec("skiff"), tag="v2.7")
        t.checksum, t.checksum_source = checksum, source
        p = P.plan_install(st, t, backup=True)
        return next(s.text for s in p.steps if s.kind == "fetch"), p
    text, p = fetch_text("digest")
    assert "GitHub" in text and "no_checksum" not in p.confirmations
    text, p = fetch_text("sums")
    assert "SHA256SUMS" in text and "GitHub" not in text
    text, p = fetch_text("", checksum=False)
    assert "GitHub" not in text and "SHA256SUMS" not in text and "no_checksum" in p.confirmations
    assert any("GitHub" in w and "SHA256SUMS" in w for w in p.warnings)


def test_dry_run_leaves_a_marked_log_and_an_app_log_line(tmp_path, monkeypatch, caplog):
    import importlib
    import logging
    for k, sub in (("XDG_CONFIG_HOME", "c"), ("XDG_DATA_HOME", "d"), ("XDG_CACHE_HOME", "k")):
        monkeypatch.setenv(k, str(tmp_path / sub))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("FM1C_DEMO", raising=False)
    from fm1companion import config
    importlib.reload(config)
    from fm1companion.flash import journal, engine, package as pk, sim
    for m in (journal, engine):
        importlib.reload(m)
    f = tmp_path / "skiff.fwsc"
    f.write_bytes(pk.build_package("FM-1_727", seed=2))
    dev = sim.SimFM1(identity="FM-1_727")
    dev.register(f.read_bytes())
    e = engine.Engine(backend=sim.SimBackend([dev]), port_filter=lambda n: "Through" not in n, log=lambda *_a: None)
    with caplog.at_level(logging.INFO, logger="fm1companion.flash"):
        r = e.write(pk.analyze(f), dry_run=True)
    assert r.dry_run and bytes(engine.fi.UPGRADE) not in dev.sends
    logs = sorted((e.journal.dir / "logs").glob("*.txt"))
    assert [p.name.split("-")[0] for p in logs] == ["dryrun"]                # never a flash-….txt of an installation
    text = logs[0].read_text("utf-8")
    assert "DRY RUN – nothing was sent to the FM-1" in text.splitlines()[1] and "FM-1_727" in text
    from fm1companion.flash import robust
    assert robust.latest_logs(e.journal.dir / "logs") == []                  # the error report does not take it for one
    assert e.journal.active is None and not e.journal.history()              # and the journal knows no installation
    assert any("dry run" in rec.getMessage() and "nothing sent" in rec.getMessage() for rec in caplog.records)


# ---------------------------------------------------------------- finding 9: "FM-1 disconnected" without a USB event

def test_one_scan_without_the_port_is_not_a_disconnect(monkeypatch):
    from fm1companion.ui import device as D
    port = stock_port(OLD_SERIAL)
    seen = {"ports": [port]}
    shots, said = [], []
    clock = {"t": 100.0}
    monkeypatch.setattr(D, "DEMO", False)
    monkeypatch.setattr(D, "scan_ports", lambda: list(seen["ports"]))
    monkeypatch.setattr(D, "QTimer", SimpleNamespace(singleShot=lambda ms, fn: shots.append(ms)))
    monkeypatch.setattr(D.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(devices, "uboot_present", lambda: [])
    link = SimpleNamespace(t=SimpleNamespace(alive=True))
    ctx = SimpleNamespace(hold=False, virtual_ports=None, ports=[port], vports=[], port=port, link=link, uboot=[],
                          ports_changed=SimpleNamespace(emit=lambda: None), _gone_since=None, port_answers={},
                          GONE_GRACE=D.DeviceContext.GONE_GRACE, GONE_RESCAN=D.DeviceContext.GONE_RESCAN)
    ctx._port_present = lambda ports: D.DeviceContext._port_present(ctx, ports)
    ctx.scan = lambda: D.DeviceContext.scan(ctx)

    def disconnect(reason=""):
        said.append(reason)
        ctx.port = ctx.link = None
        ctx._gone_since = None
    ctx.disconnect = disconnect
    # the port list misses the FM-1 once (seen on the Pi: back 0.3–0.8 s later, no USB event)
    seen["ports"] = []
    ctx.scan()
    assert not said and ctx.port is port and shots and shots[-1] <= 500      # no warning, looks again soon
    clock["t"] += 0.5
    seen["ports"] = [port]
    ctx.scan()
    assert not said and ctx.port is port and ctx._gone_since is None
    # the same MIDI port, USB details read differently this time (other key): not a disconnect either
    blurred = stock_port("", usb_path="")
    assert blurred.key != port.key
    seen["ports"] = [blurred]
    ctx.scan()
    assert not said and ctx._gone_since is None
    # really unplugged: reported once it stays away for longer than the grace time
    seen["ports"] = []
    ctx.scan()
    clock["t"] += 1.0
    ctx.scan()
    assert not said
    clock["t"] += 0.6
    ctx.scan()
    assert len(said) == 1 and ctx.port is None
    # … and at once when the link itself is dead
    ctx.port, ctx.link, said[:] = port, SimpleNamespace(t=SimpleNamespace(alive=False)), []
    ctx.scan()
    assert len(said) == 1


# ---------------------------------------------------------------- finding 10: a firmware installed outside the app

def test_firmware_found_on_connecting_is_booked_and_the_identity_refreshed(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    from fm1companion import firmware
    from fm1companion.protocol import Info
    from fm1companion.ui.device import DeviceContext
    store = store_with(tmp_path, [{"id": "b", "name": "FM-1 Schwarz/Grün", "firmware": "melodee",
                                   "firmware_version": "v1.6.1", "identity": "FM-1_91061", "usb_path": "3-1.3.1",
                                   "firmware_history": [{"time": 1.0, "firmware": "melodee", "name": "Melodee",
                                                         "version": "v1.6.1", "identity": "FM-1_91061",
                                                         "via_v15": True}]}])
    b = store.profiles["b"]
    port = devices.PortInfo("FM-1 KARMA 32:0", "FM-1 KARMA 32:0", "FM-1 KARMA", 32, usb_id="1209:0001",
                            usb_path="3-1.3.1", product="FM-1 KARMA")
    info = Info.__new__(Info)
    info.version = "FELUCCA KARMA 3.0.5"
    assert info.firmware_name == "karma"
    assert firmware.guess_firmware("FM-1_926", info.version, "karma")[0] == "karma"
    link = SimpleNamespace(hs_identity="FM-1_926", hs_flash=0)
    ctx = fake_ctx(store, port, link=link, answer=lambda c: c[0], changed=SimpleNamespace(emit=lambda: None))
    ctx._assign_profile = lambda: DeviceContext._assign_profile(ctx)
    monkeypatch.setattr("fm1companion.ui.device.config.save", lambda s: None)
    t0 = time.time()
    DeviceContext._probed(ctx, info, link)
    assert ctx.profile is b and (b.firmware, b.identity) == ("karma", "FM-1_926")
    last = b.firmware_history[-1]
    assert len(b.firmware_history) == 2 and last["how"] == "found" and last["firmware"] == "karma"
    assert last["identity"] == "FM-1_926" and last["time"] >= t0 and "via_v15" not in last
    DeviceContext._probed(ctx, info, link)                                   # connecting again adds nothing
    assert len(b.firmware_history) == 2
    assert not DeviceContext._installed_here(b, "FM-1_926")                  # "found" is not "installed by the app"
    assert json.loads(store.path.read_text("utf-8"))["profiles"][0]["firmware_history"][-1]["how"] == "found"


def test_every_recognised_fm1_leaves_a_line_in_the_app_log(home, caplog):
    """an FM-1 on the original firmware (no editor protocol) was not mentioned in the app log at all"""
    import logging
    from fm1companion.ui.device import DeviceContext
    store = store_with(home, [])
    port = stock_port(NEW_SERIAL)
    ctx = fake_ctx(store, port, identity="", link=SimpleNamespace(closed=False, hs_flash=0), firmware_guess=[],
                   message=SimpleNamespace(emit=lambda *_a: None), changed=SimpleNamespace(emit=lambda: None),
                   firmware_ambiguous=False, protocol="generic")
    ctx._assign_profile = lambda: DeviceContext._assign_profile(ctx)
    ctx._installed_here = DeviceContext._installed_here
    with caplog.at_level(logging.INFO, logger="fm1companion.device"):
        DeviceContext._generic(ctx, "FM-1_015", ctx.link)
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("connected:"))
    assert "FM-1_015" in line and "4c4a:c755" in line and "3-1.3.1" in line and ctx.profile.id in line
    assert NEW_SERIAL not in line and ctx.profile.name not in line           # no serial number, no chosen name


# ---------------------------------------------------------------- finding 3: fluidsynth.service enabled for all users

class FakeSystemd:
    """`systemctl --user` for a unit that is enabled under /etc/systemd/user (for all users): `disable` succeeds
    and changes nothing, `mask` works, `unmask` puts it back"""

    def __init__(self, global_unit=True):
        self.global_unit, self.user_enabled, self.masked, self.running, self.calls = global_unit, not global_unit, False, True, []

    def state(self):
        return "masked" if self.masked else "enabled" if (self.global_unit or self.user_enabled) else "disabled"

    def run(self, cmd, **kw):
        import subprocess
        out = ""
        if list(cmd[:2]) == ["systemctl", "--user"]:
            verb = cmd[2]
            self.calls.append([c for c in cmd[2:] if c != "fluidsynth.service"])
            if verb == "is-enabled":
                out = self.state() + "\n"
            elif verb == "is-active":
                out = "active\n" if self.running else "inactive\n"
            elif verb == "disable":
                self.user_enabled = False
            elif verb == "mask":
                self.masked = True
            elif verb == "unmask":
                self.masked = False
            if "--now" in cmd:
                self.running = False
        return subprocess.CompletedProcess(cmd, 0, out, "")


@pytest.fixture()
def installer(tmp_path, monkeypatch):
    import importlib
    sys.path.insert(0, str(ROOT / "packaging" / "installer"))
    import core
    importlib.reload(core)
    home = tmp_path / "h"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    monkeypatch.setenv("FM1C_LANG", "en")
    for k in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME", "LC_ALL", "LC_MESSAGES"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(core, "_desktop_dir", lambda: str(home / "Desktop"))
    monkeypatch.setattr(core, "refresh_menus", lambda L: None)
    monkeypatch.setattr(core.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(core.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(core, "udev_rule_installed", lambda: False)
    return core, home


def test_installer_masks_a_globally_enabled_fluidsynth_service_and_says_so(installer, monkeypatch):
    core, home = installer
    sd = FakeSystemd(global_unit=True)
    monkeypatch.setattr(core.subprocess, "run", sd.run)
    logs = []
    prefix = home / ".local"
    core.install(ROOT, prefix, log=logs.append)
    text = " ".join(logs)
    assert sd.state() == "masked" and not sd.running                 # stopped now and not started at the next login
    assert ["disable", "--now"] in sd.calls and ["mask", "--now"] in sd.calls
    assert "masked" in text and "unmask" in text and "I disabled the system" not in text
    info = json.loads((prefix / "share/fm1-companion/app/install-info.json").read_text())
    assert info["fluidsynth_masked"] is True
    # an update keeps the note (the service is no longer "enabled", so nothing is done – but it must stay recorded)
    core.install(ROOT, prefix, log=logs.append)
    assert json.loads((prefix / "share/fm1-companion/app/install-info.json").read_text())["fluidsynth_masked"] is True
    # uninstalling takes our mask away again
    out = []
    core.uninstall(prefix, log=out.append)
    assert sd.state() == "enabled" and any("no longer masked" in x for x in out)


def test_installer_reports_the_plain_disable_and_a_failure_honestly(installer, monkeypatch):
    core, home = installer
    sd = FakeSystemd(global_unit=False)                              # enabled by the user: `disable` is enough
    monkeypatch.setattr(core.subprocess, "run", sd.run)
    logs = []
    core.install(ROOT, home / ".local", log=logs.append)
    assert sd.state() == "disabled" and ["mask", "--now"] not in sd.calls
    assert any("I disabled the system" in x for x in logs)
    assert "fluidsynth_masked" not in json.loads((home / ".local/share/fm1-companion/app/install-info.json").read_text())
    out = []
    core.uninstall(home / ".local", log=out.append)
    assert ["unmask"] not in sd.calls                                # never unmask what we did not mask
    stuck = FakeSystemd(global_unit=True)
    stuck_run = stuck.run

    def run(cmd, **kw):                                              # neither disable nor mask has any effect
        r = stuck_run(cmd, **kw)
        stuck.masked = False
        return r
    monkeypatch.setattr(core.subprocess, "run", run)
    logs = []
    core.install(ROOT, home / ".local", log=logs.append)
    assert any("could NOT be switched off" in x for x in logs) and not any("I disabled" in x for x in logs)


def test_app_side_fix_checks_the_result_too(monkeypatch):
    from fm1companion import audiofix
    sd = FakeSystemd(global_unit=True)
    monkeypatch.setattr(audiofix.shutil, "which", lambda n: "/usr/bin/" + n)
    monkeypatch.setattr(audiofix.subprocess, "run", sd.run)
    ok, msg = audiofix.disable_fluidsynth_service(restart_audio=False)
    assert ok and sd.state() == "masked" and "unmask" in msg
    assert audiofix.fluidsynth_service() == (False, False)           # so the start-up warning stops


# ---------------------------------------------------------------- finding 12: the "Optional: a udev rule" hint

def test_udev_hint_is_not_printed_when_the_caller_handles_the_rule(installer, monkeypatch):
    core, home = installer
    monkeypatch.setattr(core.subprocess, "run", FakeSystemd(global_unit=False).run)
    monkeypatch.setattr(core, "fluidsynth_service_enabled", lambda: False)
    logs = []
    core.install(ROOT, home / ".local", log=logs.append)
    assert any(x.startswith("Optional: a udev rule") for x in logs)                  # the graphical installer's hint
    logs = []
    core.install(ROOT, home / ".local", log=logs.append, udev_hint=False)
    assert not any("Optional" in x for x in logs)
    sh = (ROOT / "install.sh").read_text()
    call = sh.index('core.py" install')
    assert "--no-udev-hint" in sh[:call] and '"$UDEV" != ask || -t 0' in sh[:call]
    assert '"--no-udev-hint"' in (ROOT / "packaging/installer/core.py").read_text()


# ---------------------------------------------------------------- finding 20: the rescue tool's packages

def test_rescue_packages_are_installed_by_default_and_named_together(monkeypatch):
    from fm1companion import diag
    from fm1companion.flash import uboot
    d = json.loads((ROOT / "fm1companion/data/deps.json").read_text("utf-8"))
    rec = {x["package"]: x for x in d["recommended"]}
    sug = {x["package"] for x in d["suggested"]}
    # what the rescue in ROM boot mode imports is "recommended" (install.sh and the .deb install it by default) …
    assert set(uboot.DEPS.values()) == {"python3-crcmod", "python3-yaml"} <= set(rec)
    assert [rec[p]["check"]["module"] for p in ("python3-crcmod", "python3-yaml")] == ["crcmod", "yaml"]
    assert not set(uboot.DEPS.values()) & sug and {"python3-pycryptodome", "python3-tqdm"} <= sug
    # … which is what the bundled tool really imports (its loader function, and its own test's skip condition)
    src = (ROOT / "fm1companion/vendor/unbricker/fm1_unbrick.py").read_text("utf-8")
    body = src[src.index("def load_jlub"):src.index("def ", src.index("def load_jlub") + 10)]
    assert "import yaml" in body and "jltech.uboot" in body
    assert not __import__("re").search(r"^\s*(import|from)\s+(Cryptodome|Crypto|tqdm)\b", src, __import__("re").M)
    # every missing one is named in ONE apt command: on the Recovery page and in `fm1-companion --check`
    monkeypatch.setattr(uboot.importlib.util, "find_spec", lambda name: None)
    assert uboot.missing_deps() == ["python3-crcmod", "python3-yaml"]
    states = [diag.DepState(p, False, False, "x") for p in ("python3-crcmod", "python3-yaml")]
    assert diag.apt_command(states) == "sudo apt install python3-crcmod python3-yaml"
    assert diag.apt_command(states, include_optional=False) == ""    # a recommendation, not an error at start-up
    assert "python3-crcmod python3-yaml" in (ROOT / ".github/workflows/tests.yml").read_text("utf-8")


# ---------------------------------------------------------------- finding 21: no needless V15 step on a unit on V15

def v15_plan(**kw):
    from fm1companion import firmware
    from fm1companion.flash import planner as P
    st = P.DeviceState(identity="FM-1_015", pinned=True, revision="old", **kw)
    return P, P.plan_install(st, P.Target.from_spec(firmware.spec("skiff"), tag="v2.7"), backup=False)


def test_a_unit_on_the_original_v15_gets_no_second_v15_write():
    # the owner's orange unit: identity FM-1_015, USB 4c4a:c755, candidates "stock-v15" and "stock", choice "?"
    for key in ("", "stock-v15", "stock"):
        P, p = v15_plan(firmware_key=key, candidates=["stock-v15", "stock"], usb_id="4c4a:c755")
        assert p.v15_option == "none" and not p.via_stock and "stock" not in p.kinds(), key
        assert sum(1 for s in p.steps if s.writes) == 1 and not any("TransitFM1" in w for w in p.warnings)
    P, p = v15_plan()                                   # nothing known beyond the identity: as before
    assert p.v15_option == "none" and not p.via_stock


def test_an_fm1_015_that_may_be_another_firmware_keeps_the_v15_step_and_says_why():
    # the identity is also known for a custom firmware, and nobody said which one runs: nothing is skipped
    P, p = v15_plan(candidates=["stock-v15", "stock", "transitfm1"], usb_id="")
    assert p.v15_option == "default_on" and p.via_stock and p.kinds().count("stock") == 1
    why = next(w for w in p.warnings if "TransitFM1" in w)
    assert "FM-1_015" in why and "transitfm1" in why and "4c4a:c755" in why and "1209:0001" in why
    # the Felucca family's USB ID under the identity FM-1_015 is not the original firmware, whatever was answered
    for key in ("", "stock-v15"):
        P, p = v15_plan(firmware_key=key, candidates=["stock-v15", "stock"], usb_id="1209:0001")
        assert p.v15_option == "default_on" and p.via_stock and any("1209:0001" in w for w in p.warnings), key
        assert P.running_firmware(p.state) == "" and P.current_name(p.state) == ""


def test_the_plan_follows_the_answer_to_which_firmware_runs():
    cands = ["stock-v15", "stock", "transitfm1"]
    P, p = v15_plan(firmware_key="stock-v15", candidates=cands, usb_id="4c4a:c755")     # answered: the original
    assert p.v15_option == "none" and not p.via_stock and not any("TransitFM1" in w for w in p.warnings)
    P, p = v15_plan(firmware_key="transitfm1", candidates=cands, usb_id="1209:0001")    # answered: TransitFM1
    assert p.v15_option == "default_on" and p.via_stock and P.running_firmware(p.state) == "transitfm1"
    assert not any("may be another firmware" in w or "andere Firmware sein" in w for w in p.warnings)
    # a stale record is no evidence: after the app's own V15 step the profile still says "melodee"
    P, p = v15_plan(firmware_key="melodee", candidates=["stock-v15", "stock"], usb_id="4c4a:c755")
    assert p.v15_option == "none" and not p.via_stock
    # an update within that firmware stays an update (no V15 step), as for every other firmware
    st = P.DeviceState(identity="FM-1_015", firmware_key="transitfm1", pinned=True, revision="old", usb_id="1209:0001")
    same = P.plan_install(st, P.Target(name="TransitFM1", product="FM-1_015", family="felucca", key="transitfm1"),
                          backup=False)
    assert same.v15_option == "none"


# ---------------------------------------------------------------- finding 24: the newer unit booked on the other profile

def two_units(home, new_port="1-2"):
    """devices.json as it was on the Pi: the earlier unit without a serial number, the newer one with its own"""
    return store_with(home, [
        {"id": "old", "name": "FM-1 Schwarz/Grün", "firmware": "skiff", "firmware_version": "v2.7",
         "identity": "FM-1_727", "usb_path": "3-1.3.1", "hw_revision": "old", "hw_revision_source": "user"},
        {"id": "2039b2a6a5", "name": "FM-1 A", "serial": NEW_SERIAL, "firmware": "stock-v15", "identity": "FM-1_015",
         "usb_path": new_port, "hw_revision": "new", "hw_revision_source": "user"}])


def test_after_the_install_the_unit_stays_its_own_profile(home):
    """Skiff 2.7 installed on "FM-1 A" (newer chip, stock serial) at port 1-2. Skiff reports no serial number: the
    app took the only profile without one – the OTHER unit's – wrote port 1-2 into it and offered its backup."""
    store = two_units(home)
    new, old = store.profiles["2039b2a6a5"], store.profiles["old"]
    ctx = fake_ctx(store, pending_restore_check="skiff", pending_restore_profile=new.id)
    ctx.note_installed(new, "1-2")                     # install_flow, in its finally
    new.firmware, new.identity = "skiff", "FM-1_727"   # install_flow done(): profile updated
    prof, asked = assign(store, felucca_port("Felucca", "1-2"), identity="FM-1_727", ctx=ctx)
    assert prof is new and not asked                   # (a) the profile the app installed on, at that port
    assert old.usb_path == "3-1.3.1"                   # (e) the other profile's USB port is not touched
    assert ctx.pending_restore_check == "skiff"        # (d) the offer is for this profile: stays
    from fm1companion.ui.firmware_page import device_revision
    ctx.profile = prof
    assert device_revision(ctx) == "new"


def test_two_profiles_one_identity_without_serial_is_asked_never_the_old_one(home):
    """the persisted state after that installation, app restarted: both units run Skiff 2.7 (FM-1_727), neither
    shows a serial number"""
    from fm1companion.ui.firmware_page import device_revision
    store = two_units(home)
    new, old = store.profiles["2039b2a6a5"], store.profiles["old"]
    new.firmware, new.identity = "skiff", "FM-1_727"
    for path, guess in (("1-2", new.id), ("3-1.3.1", "old"), ("7-7", None)):
        ctx = fake_ctx(store, pending_restore_check="skiff", pending_restore_profile=new.id)
        prof, asked = assign(store, felucca_port("Felucca", path), identity="FM-1_727", ctx=ctx)
        assert prof is None and len(asked) == 1, path  # (c) asked – the USB port alone is no proof
        assert set(asked[0][0]) == {"old", new.id}     # (b) the profile WITH a serial number is a candidate
        assert guess is None or asked[0][1] == guess   # the profile of that port is only the preselection
        assert device_revision(ctx) == "new"           # unanswered: the strictest candidate
        assert ctx.pending_restore_check == ""         # (d) no restore offer while nobody said which unit it is
        assert (old.usb_path, new.usb_path) == ("3-1.3.1", "1-2")           # (e)
    # answered: that profile, its port learned, and not asked again while the port stays
    ctx = fake_ctx(store)
    port = felucca_port("Felucca", "3-1.3.1")
    prof, asked = assign(store, port, identity="FM-1_727", ctx=ctx, ask=lambda c: new)
    assert prof is new and new.usb_path == "3-1.3.1"
    prof, asked = assign(store, port, identity="FM-1_727", ctx=ctx)
    assert prof is new and not asked
    ctx.port_answers.clear()                           # (scan() does this when the port was away)
    prof, asked = assign(store, port, identity="FM-1_727", ctx=ctx)
    assert prof is None and asked
    # a restore offer meant for one profile is dropped when the unit turns out to be the other
    ctx = fake_ctx(store, pending_restore_check="skiff", pending_restore_profile=new.id)
    prof, asked = assign(store, port, identity="FM-1_727", ctx=ctx, ask=lambda c: old)
    assert prof is old and ctx.pending_restore_check == ""


def test_felucca_firmware_at_the_new_profiles_port_is_the_new_profile(home):
    """persisted state, the identities differ (the earlier unit runs KARMA): port and identity name the newer one"""
    store = two_units(home)
    new, old = store.profiles["2039b2a6a5"], store.profiles["old"]
    old.firmware, old.identity = "karma", "FM-1_926"
    new.firmware, new.identity = "skiff", "FM-1_727"
    prof, asked = assign(store, felucca_port("Felucca", "1-2"), identity="FM-1_727")
    assert prof is new and not asked and old.usb_path == "3-1.3.1"
    # the very case of the report without the app's own bookkeeping: the profile still says "stock-v15 / FM-1_015"
    new.firmware, new.identity = "stock-v15", "FM-1_015"
    prof, asked = assign(store, felucca_port("Felucca", "1-2"), identity="FM-1_727")
    assert prof is None and asked and asked[0][1] == new.id and old.usb_path == "3-1.3.1"


def test_port_answers_end_when_the_port_goes_away(monkeypatch):
    from fm1companion.ui import device as D
    port = felucca_port()
    monkeypatch.setattr(D, "DEMO", False)
    monkeypatch.setattr(D, "scan_ports", lambda: [])
    monkeypatch.setattr(devices, "uboot_present", lambda: [])
    ctx = SimpleNamespace(hold=False, virtual_ports=None, ports=[port], vports=[], port=None, link=None, uboot=[],
                          ports_changed=SimpleNamespace(emit=lambda: None), _gone_since=None,
                          port_answers={port.key: "x"}, settings=SimpleNamespace(last_device=""))
    D.DeviceContext.scan(ctx)
    assert ctx.port_answers == {}


# ---------------------------------------------------------------- finding 22: the header line

def test_title_names_the_device_first(monkeypatch):
    from fm1companion.ui import device as D
    prof = devices.Profile(id="a", name="FM-1 A", firmware="stock-v15")
    ctx = SimpleNamespace(port=stock_port(NEW_SERIAL), is_virtual=False, profile=prof, info=None, protocol="generic",
                          midi_connected=True, identity="FM-1_015", firmware_key="stock-v15", firmware_ambiguous=False)
    t = D.DeviceContext.title(ctx)
    assert t.startswith("FM-1 A – ") and t.endswith("(FM-1_015)") and t.count("V15") == 1
    ctx.firmware_key, ctx.firmware_ambiguous = "", True
    assert D.DeviceContext.title(ctx).startswith("FM-1 A – ")                # never an empty name in front
    ctx.profile = None
    assert D.DeviceContext.title(ctx).startswith("FM-1 – ")                  # no profile: the port's name
    # with editor protocol: the firmware under its own name (Skiff reports "FELUCCA v2.7")
    ctx.profile, ctx.info, ctx.firmware_key = prof, SimpleNamespace(version="FELUCCA v2.7"), "skiff"
    assert D.DeviceContext.title(ctx) == "FM-1 A – Skiff 2.7"


# ---------------------------------------------------------------- finding 25: the first run on the newer chip

def test_new_chip_run_is_recorded_in_its_exact_scope():
    from fm1companion import firmware
    from fm1companion.flash import planner as P, tested
    assert tested.tested("new_chip_skiff") and not tested.tested("new_chip")
    st = P.DeviceState(identity="FM-1_015", firmware_key="stock-v15", pinned=True, revision="new", usb_id="4c4a:c755")

    def kinds(key, tag, state=st):
        return tested.plan_kinds(P.plan_install(state, P.Target.from_spec(firmware.spec(key), tag=tag), backup=False))
    assert "new_chip_skiff" in kinds("skiff", "v2.7") and "new_chip" not in kinds("skiff", "v2.7")
    for key, tag in (("melodee", "v1.6.1"), ("karma", "v3.1"), ("stock-v15", "V15")):
        assert "new_chip" in kinds(key, tag) and "new_chip_skiff" not in kinds(key, tag), key
    via = P.DeviceState(identity="FM-1_91061", firmware_key="melodee", felucca=True, pinned=True, revision="new")
    assert "new_chip" in kinds("skiff", "v2.7", via)                         # with a V15 step: not what ran
    doc = (ROOT / "docs" / "UNTESTED.md").read_text("utf-8")
    assert "Scope: one unit, one firmware (Skiff 2.7), one computer." in doc and "m-bootloader" in doc
    assert "that package was never written to one" in doc


def test_plan_on_a_newer_unit_says_why_the_target_is_let_through_and_asks_for_power():
    from fm1companion import firmware
    from fm1companion.flash import planner as P
    st = P.DeviceState(identity="FM-1_015", firmware_key="stock-v15", pinned=True, revision="new", usb_id="4c4a:c755")
    p = P.plan_install(st, P.Target.from_spec(firmware.spec("skiff"), tag="v2.7"), backup=False)
    assert p.ok and not p.via_stock
    note = next(w for w in p.warnings if "2.2" in w and "UC25HQ80" in w)
    assert "Skiff 2.7" in note                         # Skiff: ran once – said, nothing more
    mel = P.plan_install(st, P.Target.from_spec(firmware.spec("melodee"), tag="v1.6.1"), backup=False)
    note = next(w for w in mel.warnings if "1.6.0" in w and "UC25HQ80" in w)
    assert "Skiff 2.7" not in note
    v15 = P.plan_install(st, P.Target.from_spec(firmware.spec("stock-v15"), tag="V15"), backup=False)
    assert not any("nicht gemessen" in w or "not measured" in w for w in v15.warnings)   # V15 keeps its own note only
    assert any(w == P.NEWCHIP_V15 for w in v15.warnings)
    old = P.DeviceState(identity="FM-1_015", firmware_key="stock-v15", pinned=True, revision="old")
    assert not any("UC25HQ80" in w for w in P.plan_install(
        old, P.Target.from_spec(firmware.spec("skiff"), tag="v2.7"), backup=False).warnings)
    # the power confirmation is part of every plan that writes – this one had none
    assert "power" in p.confirmations and "power" in mel.confirmations
    hub = P.DeviceState(identity="FM-1_015", firmware_key="stock-v15", pinned=True, revision="new", hubs=1)
    assert "power_hub" in P.plan_install(hub, P.Target.from_spec(firmware.spec("skiff"), tag="v2.7"),
                                         backup=False).confirmations


def test_v15_is_not_offered_as_an_update_to_a_unit_that_reports_version_15():
    from fm1companion import firmware
    from fm1companion.ui.firmware_page import FirmwarePage
    rel = firmware.Release(tag="V15", prerelease=False)
    page = SimpleNamespace(ctx=SimpleNamespace(firmware_key="stock-v15", firmware_tag="", identity="FM-1_015",
                                               settings=SimpleNamespace(channel="stable")),
                           releases={"stock-v15": [rel]})
    st = FirmwarePage.update_status(page)
    assert st["current"] == st["latest"] == "V15"
    page.ctx.identity = "FM-1_016"                     # (another original version: V15 stays what it is)
    assert FirmwarePage.update_status(page)["current"] == ""
