# SPDX-License-Identifier: GPL-3.0-only
"""Installer workshop: package, plan, safety rules, journal, simulated installations, rescue level 2.

  python3 -m pytest tests/test_flash.py -q
Everything in simulation – no real FM-1.
"""
from __future__ import annotations

import hashlib
import importlib
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
V15 = Path("/tmp/fm1_open/sdk/firmware/FM-1_015.fwsc")
need_v15 = pytest.mark.skipif(not V15.exists(), reason="original V15 missing")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    for k, sub in (("XDG_CONFIG_HOME", "c"), ("XDG_DATA_HOME", "d"), ("XDG_CACHE_HOME", "k")):
        monkeypatch.setenv(k, str(tmp_path / sub))
    monkeypatch.delenv("FM1C_DEMO", raising=False)
    from fm1companion import config
    importlib.reload(config)
    from fm1companion.flash import journal, package, planner, sim, engine
    for m in (journal, engine):
        importlib.reload(m)
    from fm1companion.vendor import fm1_install as fi
    old = dict(fi.DELAY)
    fi.DELAY.update(sim.fast_delays())
    yield tmp_path
    fi.DELAY.clear()
    fi.DELAY.update(old)


def pkgfile(tmp, product, family="felucca", seed=1, name=None):
    from fm1companion.flash import package as pk
    p = Path(tmp) / (name or f"{product}-{family}-{seed}.fwsc")
    p.write_bytes(pk.build_package(product, family, seed=seed))
    return p


def sim_setup(dev_kw=None, packages=()):
    from fm1companion.flash import sim
    dev = sim.SimFM1(**(dev_kw or {}))
    for raw in packages:
        dev.register(raw)
    return dev, sim.SimBackend([dev])


def eng(backend, **kw):
    from fm1companion.flash import engine
    logs = []
    return engine.Engine(backend=backend, port_filter=lambda n: "Through" not in n, log=logs.append, **kw), logs


# ---------------------------------------------------------------- package

def test_package_analysis(env):
    from fm1companion.flash import package as pk
    p = pkgfile(env, "FM-1_91060")
    i = pk.analyze(p)
    assert i.product == "FM-1_91060" and i.family == "felucca" and not i.damage and i.model == "FM-1"
    u = pk.analyze(pkgfile(env, "FM-1_950", "unknown"))
    assert u.family == "unknown" and u.loader_rule is None
    # damaged: one byte in flash.bin
    raw = bytearray(p.read_bytes())
    raw[2000] ^= 0xFF
    bad = env / "bad.fwsc"
    bad.write_bytes(bytes(raw))
    assert "Prüfsumme" in pk.analyze(bad).damage
    with pytest.raises(pk.PackageError):
        (env / "tiny.fwsc").write_bytes(b"x" * 10)
        pk.analyze(env / "tiny.fwsc")


@need_v15
def test_official_v15_and_app_region(env):
    from fm1companion.flash import package as pk
    i = pk.analyze(V15)
    assert i.official == "FM-1_015" and i.family == "stock" and not i.damage
    fl = next(e for e in pk.ufw_check(i.image)[1] if e[0] == 0)
    region = i.image[fl[2]:fl[2] + fl[3]][0x4000:0x93000]
    # the same checksum as in the Reddit guide / MvaveFM1Unbricker (V15_FLASH_FW_SHA256)
    assert hashlib.sha256(region).hexdigest() == "6edf3c37fb5bbbc33607c89375ee024d5477c17914d72221c8c68e58a8255686"


def test_loader_rules(env):
    from fm1companion.flash import package as pk
    st = pk.LoaderRule("stock", "FM-1_015")
    fe = pk.LoaderRule("felucca", "FM-1_712")
    assert st("ota-FM-1_015") and not st("ota-FM-1_900")
    assert fe("ota-FM-1_900") and fe("ota-FM-1_712") and not fe("ota-FM-1_015") and not fe("ota-FM-1C_abc")


# ---------------------------------------------------------------- Plan

def _spec(**kw):
    from fm1companion.firmware import FirmwareSpec
    base = dict(key="x", name="X", repo="o/x", asset="x-{ver}.fwsc")
    base.update(kw)
    return FirmwareSpec(**base)


def melodee_state(**kw):
    from fm1companion.flash.planner import DeviceState
    d = dict(identity="FM-1_91060", felucca=True, firmware_key="melodee", version="Melodee 1.6.0-rc.7")
    d.update(kw)
    return DeviceState(**d)


def test_plan_melodee_to_melodee(env):
    from fm1companion import firmware
    from fm1companion.flash.planner import Target, plan_install
    p = plan_install(melodee_state(), Target.from_spec(firmware.spec("melodee"), tag="v1.6.0"))
    assert p.ok and p.kinds() == ["backup", "fetch", "install", "resources", "verify"]
    assert not p.via_stock and "power" not in p.confirmations and not p.lost


def test_plan_melodee_16_upgrade_and_downgrade(env):
    """Melodee 1.6.0 (RELEASE_1.6.0.md "Upgrading"): from ≤ 1.5.1 backup required + warning; from rc.x new levels;
    back from 1.6 to older Melodee or another firmware: clear warning + confirmation."""
    from fm1companion import firmware, melodee16 as M
    from fm1companion.flash.planner import Target, plan_install
    mel = firmware.spec("melodee")
    old = melodee_state(identity="FM-1_91051", version="MELODEE v1.5.1")
    p = plan_install(old, Target.from_spec(mel, tag="v1.6.0"))
    assert p.ok and p.kinds() == ["backup", "fetch", "install", "resources", "verify"]
    assert p.warnings[0] == M.UPGRADE_16 and M.LEVELS_160 in p.warnings and M.RESOURCES_FROM_15 in p.warnings
    assert "zweite Installation" in M.RESOURCES_FROM_15 and "Setup unfinished" in M.RESOURCES_FROM_15
    p = plan_install(old, Target.from_spec(mel, tag="v1.6.0"), backup=False)
    assert not p.ok and M.BACKUP_REQUIRED_16 in p.blockers
    assert plan_install(old, Target.from_spec(mel, tag="v1.5.1"), backup=False).ok        # 1.5 -> 1.5: free
    rc = melodee_state(version="MELODEE v1.6.0-rc.8")
    p = plan_install(rc, Target.from_spec(mel, tag="v1.6.0"))
    assert p.ok and M.LEVELS_160 in p.warnings and M.UPGRADE_16 not in p.warnings
    assert plan_install(rc, Target.from_spec(mel, tag="v1.6.0"), backup=False).ok          # already 1.6: no obligation
    new = melodee_state(version="MELODEE v1.6.0")
    p = plan_install(new, Target.from_spec(mel, tag="v1.6.0"))
    assert p.ok and not any("1.6" in w for w in p.warnings) and not p.confirmations
    p = plan_install(new, Target.from_spec(mel, tag="v1.5.1"))
    assert p.ok and "Zurück von Melodee 1.6" in p.warnings[0] and "Melodee v1.5.1" in p.warnings[0]
    assert "melodee16_down" in p.confirmations and M.DOWNGRADE_LOST in p.lost
    assert p.missing(["power"]) == ["melodee16_down"]
    p = plan_install(new, Target.from_spec(firmware.spec("sloop"), tag="v2.8.3"))
    assert "Zurück von Melodee 1.6" in p.warnings[0] and "SLOOP" in p.warnings[0] and "melodee16_down" in p.confirmations
    # unknown version on the device: no guess
    p = plan_install(melodee_state(version=""), Target.from_spec(mel, tag="v1.6.0"))
    assert p.ok and not any("1.6" in w for w in p.warnings)


def test_plan_melodee_161_is_a_normal_update(env):
    """Melodee 1.6.1 (RELEASE_1.6.1.md "Upgrading": from 1.6.0 install as usual, 1.6.0 still reads the data):
    1.6.0 -> 1.6.1 and back without extra warnings; rc.x -> 1.6.1 names the new levels; 1.5.1 -> 1.6.1 like 1.6.0."""
    from fm1companion import firmware, melodee16 as M
    from fm1companion.flash.planner import Target, melodee_change, plan_install
    mel = firmware.spec("melodee")
    t161 = Target.from_spec(mel, tag="v1.6.1")
    for dev in ("MELODEE v1.6.0", "MELODEE v1.6.1"):
        st = melodee_state(version=dev)
        p = plan_install(st, t161)
        assert melodee_change(st, t161) == "within"
        assert p.ok and not any("1.6" in w for w in p.warnings) and not p.confirmations, (dev, p.warnings)
    back = melodee_state(version="MELODEE v1.6.1")
    p = plan_install(back, Target.from_spec(mel, tag="v1.6.0"))
    assert p.ok and not any("Zurück von Melodee 1.6" in w for w in p.warnings) and "melodee16_down" not in p.confirmations
    p = plan_install(melodee_state(version="MELODEE v1.6.0-rc.8"), t161)
    assert p.ok and M.LEVELS_160 in p.warnings and M.UPGRADE_16 not in p.warnings
    p = plan_install(melodee_state(identity="FM-1_91051", version="MELODEE v1.5.1"), t161)
    assert p.ok and p.warnings[0] == M.UPGRADE_16 and M.LEVELS_160 in p.warnings
    assert not plan_install(melodee_state(identity="FM-1_91051", version="MELODEE v1.5.1"), t161, backup=False).ok
    assert M.is_16("MELODEE v1.6.1") and not M.before_16("v1.6.1") and not M.new_levels("v1.6.0", "v1.6.1")


def test_plan_melodee_to_sloop_builtin(env):
    from fm1companion import firmware
    from fm1companion.flash.planner import Target, plan_install
    p = plan_install(melodee_state(), Target.from_spec(firmware.spec("sloop"), tag="v2.8.3"))
    # firmware switch: intermediate step V15 on by default, backup before it
    assert p.ok and p.v15_option == "default_on" and p.via_stock and p.lost
    assert p.kinds() == ["backup", "fetch", "stock", "install", "verify"]
    assert any("sichert sie vorher" in w for w in p.warnings)
    off = plan_install(melodee_state(), Target.from_spec(firmware.spec("sloop")), via_v15=False)
    assert off.ok and off.kinds() == ["backup", "fetch", "install", "verify"]


def test_v15_option_cases(env):
    from fm1companion import firmware
    from fm1companion.flash.planner import DeviceState, Target, plan_install, switch_key
    mel = Target.from_spec(firmware.spec("melodee"), tag="v1.6.0")
    upd = plan_install(melodee_state(), mel)                                   # update of the same firmware
    assert upd.v15_option == "default_off" and not upd.via_stock
    assert plan_install(melodee_state(), mel, via_v15=True).via_stock           # offered option
    unknown = plan_install(DeviceState(identity="FM-1_900"), mel)               # current firmware unknown
    assert unknown.v15_option == "default_on" and unknown.via_stock and "no_backup" in unknown.confirmations
    orbit = _spec(key="orbit", name="ORBIT", requires_stock_v15=True, loader_family="sloopG3")
    lk = plan_install(melodee_state(), Target.from_spec(orbit), via_v15=False)  # locked: deselecting has no effect
    assert lk.v15_option == "locked" and lk.via_stock
    st = plan_install(melodee_state(), Target.from_spec(firmware.spec("stock-v15")), via_v15=True)
    assert st.v15_option == "none" and "stock" not in st.kinds()
    for p in (plan_install(melodee_state(), Target.from_spec(firmware.spec("sloop"))), lk):
        k = p.kinds()
        assert k.index("backup") < k.index("stock") < k.index("install")
    assert switch_key(melodee_state(), Target.from_spec(firmware.spec("sloop"))) == "melodee>sloop"


def test_v15_choice_persisted_per_switch(env):
    from fm1companion.flash import prefs
    assert prefs.choice_for("melodee>sloop") is None
    prefs.remember("melodee>sloop", False)
    assert prefs.choice_for("melodee>sloop") is False and prefs.choice_for("sloop>melodee") is None


def test_plan_melodee_to_orbit_goes_via_v15(env):
    from fm1companion.flash.planner import Target, plan_install
    orbit = _spec(key="orbit", name="ORBIT", requires_stock_v15=True, notes=["beta"], loader_family="sloopG3",
                  product="FM-1_900", protocol="none")
    p = plan_install(melodee_state(), Target.from_spec(orbit, tag="v0.7.1"))
    assert p.ok and p.via_stock
    assert p.kinds() == ["backup", "fetch", "stock", "install", "verify"]
    assert p.kinds().index("stock") < p.kinds().index("install")
    assert {"beta", "power"} <= set(p.confirmations)
    assert any("CHECK HEAD" in w for w in p.warnings)
    # already on V15: no detour
    from fm1companion.flash.planner import DeviceState
    p2 = plan_install(DeviceState(identity="FM-1_015"), Target.from_spec(orbit))
    assert "stock" not in p2.kinds() and "no_backup" in p2.confirmations


def test_plan_back_to_stock(env):
    from fm1companion import firmware
    from fm1companion.flash.planner import Target, plan_install
    for key in ("stock-v15", "stock-v16"):
        p = plan_install(melodee_state(), Target.from_spec(firmware.spec(key)))
        assert p.ok and "install" in p.kinds() and "power" in p.confirmations
        assert any("Reset Patches" in w for w in p.warnings), p.warnings
    assert "stock" not in plan_install(melodee_state(), Target.from_spec(firmware.spec("stock-v15"))).kinds()
    # V16 from Melodee: Melodee checks the boot head before the original loader (ota.c) -> only via V15, fixed
    p16 = plan_install(melodee_state(), Target.from_spec(firmware.spec("stock-v16")))
    assert any("V16" in w for w in p16.warnings)
    assert p16.v15_option == "locked" and p16.kinds().index("stock") < p16.kinds().index("install")
    assert any("0x93000–0xA8FFF" in x for x in p16.lost) and any("Emulator" in x for x in p16.lost)
    assert not plan_install(melodee_state(), Target.from_spec(firmware.spec("stock-v16")), backup=False).ok


def test_plan_hash_mismatch_and_unknown_file(env):
    from fm1companion import firmware
    from fm1companion.flash import package as pk
    from fm1companion.flash.planner import Target, plan_install
    f = pkgfile(env, "FM-1_016", "felucca", name="FM-1.fwsc")
    info = pk.analyze(f)
    t = Target.from_spec(firmware.spec("stock-v16"), file=f, package=info)
    p = plan_install(melodee_state(), t)
    assert not p.ok and any("Prüfsumme" in b for b in p.blockers)
    own = pk.analyze(pkgfile(env, "FM-1_777"))
    p = plan_install(melodee_state(), Target.from_file(own))
    assert p.ok and "unknown_file" in p.confirmations and any(own.sha256 in w for w in p.warnings)
    assert "unknown_file" in p.missing(["power"]) and not p.missing(p.confirmations)


def test_plan_refusals(env):
    from fm1companion.flash import package as pk
    from fm1companion.flash.planner import DeviceState, Target, plan_install
    unk = pk.analyze(pkgfile(env, "FM-1_950", "unknown"))
    assert not plan_install(melodee_state(), Target.from_file(unk)).ok        # unknown loader
    good = pk.analyze(pkgfile(env, "FM-1_91060"))
    # the device waits in the original loader -> the Felucca package is not sent
    p = plan_install(DeviceState(identity="ota-FM-1_015"), Target.from_file(good))
    assert not p.ok and any("nichts gesendet" in b for b in p.blockers)
    # matching loader -> resume
    p = plan_install(DeviceState(identity="ota-FM-1_900"), Target.from_file(good))
    assert p.ok and p.resume and p.kinds() == ["fetch", "resume", "verify"]
    # a second installation at the same time
    assert not plan_install(melodee_state(busy=True), Target.from_file(good)).ok
    # open firmware, foreign model
    assert not plan_install(DeviceState(identity="FM1_OFFICIAL_OPEN"), Target.from_file(good)).ok
    assert not plan_install(DeviceState(identity="SMK-37_015"), Target.from_file(good)).ok


def test_plan_felucca09_warning(env):
    from fm1companion.flash.planner import DeviceState, Target, plan_install
    fel = _spec(key="felucca", name="Felucca", repo="hugelton/Felucca")
    p = plan_install(DeviceState(identity="FM-1_909", felucca=True, version="Felucca 0.9-beta"),
                     Target.from_spec(fel, tag="v1.0.1"))
    assert "felucca09" in p.confirmations and any("0x97000" in w for w in p.warnings)


def test_plan_nonresumable_needs_power_confirmation(env):
    from fm1companion.flash.planner import Target, plan_install
    x0x = _spec(key="x0x", name="X0X", notes=["beta", "powerLossNotResumable"], loader_family="feluccaG1")
    p = plan_install(melodee_state(), Target.from_spec(x0x))
    assert "power" in p.confirmations and any("NICHT fortsetzen" in w for w in p.warnings)


# ---------------------------------------------------------------- engine against simulated loaders

def test_install_felucca_end_to_end(env):
    from fm1companion.flash import package as pk
    f = pkgfile(env, "FM-1_91070")
    dev, be = sim_setup(dict(identity="FM-1_91060"), [f.read_bytes()])
    e, logs = eng(be)
    r = e.write(pk.analyze(f), port="Melodee FM-1")
    assert r.identity == "FM-1_91070" and dev.identity == "FM-1_91070"
    assert dev.flash == pk.analyze(f).image          # the written image is the package
    assert not dev.bricked and e.journal.active is None
    assert e.journal.history()[-1]["phase"] == "done"
    assert be.opened_other == []                       # foreign ports never opened


@need_v15
def test_return_to_stock_v15_end_to_end(env):
    from fm1companion.flash import package as pk
    dev, be = sim_setup(dict(identity="FM-1_91060"), [V15.read_bytes()])
    e, _ = eng(be)
    r = e.write(pk.analyze(V15), port="Melodee FM-1")
    assert r.identity == "FM-1_015" and dev.flash == pk.analyze(V15).image and not dev.bricked
    assert dev.port_name == "USB Composite Device"     # what the original firmware is called on USB


@need_v15
def test_check_head_refusal(env):
    from fm1companion.flash import engine, package as pk
    dev, be = sim_setup(dict(identity="FM-1_91060", boot_matches_stock=False), [V15.read_bytes()])
    e, _ = eng(be)
    with pytest.raises(engine.FlashError) as ei:
        e.write(pk.analyze(V15), port="Melodee FM-1")
    assert ei.value.code == "checkhead" and ei.value.state == engine.SAFE
    assert dev.identity == "FM-1_91060" and dev.state == "app" and not dev.flash


@need_v15
def test_orbit_via_v15_two_installs(env):
    """Melodee -> V15 -> ORBIT-like package (Felucca loader); the port changes in between"""
    from fm1companion.flash import package as pk
    orbit = pkgfile(env, "FM-1_900", seed=7)
    dev, be = sim_setup(dict(identity="FM-1_91060"), [V15.read_bytes(), orbit.read_bytes()])
    e, _ = eng(be)
    assert e.write(pk.analyze(V15), port="Melodee FM-1").identity == "FM-1_015"
    assert e.probe().text == "FM-1_015"
    assert e.write(pk.analyze(orbit), port="").identity == "FM-1_900"
    assert dev.flash == pk.analyze(orbit).image and not dev.bricked


def test_wrong_loader_gets_nothing(env):
    """a device in the original update mode gets no Felucca package – not a single byte"""
    from fm1companion.flash import engine, package as pk
    f = pkgfile(env, "FM-1_91060")
    dev, be = sim_setup(dict(identity="FM-1_015", policy="stock"))
    dev.state, dev.loader_kind, dev.loader_id = "loader", "stock", "ota-FM-1_015"
    e, _ = eng(be)
    with pytest.raises(engine.FlashError) as ei:
        e.write(pk.analyze(f))
    assert ei.value.code == "loader"
    assert not any(s == bytes([0xF0, 0x22, 0x24, 0x35, 0x7F, 0xF7]) for s in dev.sends)
    assert dev.partial == 0 and not dev.flash


def test_step1_starts_foreign_loader_refused(env):
    """step 1 starts a different loader than the package's -> step 2 gets nothing"""
    from fm1companion.flash import engine, package as pk
    f = pkgfile(env, "FM-1_91060")
    dev, be = sim_setup(dict(identity="FM-1_91050", felucca_loader="ota-FM-1_777"), [f.read_bytes()])
    e, _ = eng(be)
    with pytest.raises(engine.FlashError) as ei:
        e.write(pk.analyze(f))
    assert ei.value.code == "loader" and dev.state == "loader" and dev.partial == 0


def test_power_loss_then_resume_with_right_package(env):
    from fm1companion.flash import engine, package as pk
    from fm1companion.flash.planner import DeviceState, Target, plan_install
    f = pkgfile(env, "FM-1_91070")
    other = pkgfile(env, "FM-1_91080", seed=3)
    dev, be = sim_setup(dict(identity="FM-1_91060", power_loss_at=0.5), [f.read_bytes(), other.read_bytes()])
    e, _ = eng(be)
    with pytest.raises(engine.FlashError) as ei:
        e.write(pk.analyze(f))
    assert ei.value.state == engine.PAUSED
    act = e.journal.active
    assert act and act["sha256"] == pk.analyze(f).sha256 and Path(act["package"]).exists()
    # after "restarting the app": the journal knows the right package, the device waits in the Felucca loader
    import time
    t0 = time.time()
    ident = e.probe()
    while ident is None and time.time() - t0 < 3:
        time.sleep(0.05)
        ident = e.probe()
    assert ident.text == "ota-FM-1_900"
    kept = pk.analyze(Path(act["package"]))
    plan = plan_install(DeviceState(identity=ident.text), Target.from_file(kept), journal_active=act)
    assert plan.ok and plan.resume
    plan2 = plan_install(DeviceState(identity=ident.text), Target.from_file(pk.analyze(other)), journal_active=act)
    assert any("Unterbrochen" in w for w in plan2.warnings)
    r = e.write(kept)
    assert r.identity == "FM-1_91070" and dev.flash == kept.image and e.journal.active is None


def test_nonresumable_power_loss_is_reported(env):
    from fm1companion.flash import engine, package as pk
    f = pkgfile(env, "FM-1_9010100")
    dev, be = sim_setup(dict(identity="FM-1_91060", power_loss_at=0.3, resumable=False), [f.read_bytes()])
    e, _ = eng(be)
    with pytest.raises(engine.FlashError) as ei:
        e.write(pk.analyze(f))
    assert ei.value.state == engine.PAUSED and dev.state == "uboot"


def test_concurrent_install_refused(env):
    from fm1companion.flash import engine, journal, package as pk
    f = pkgfile(env, "FM-1_91070")
    dev, be = sim_setup(dict(identity="FM-1_91060"), [f.read_bytes()])
    e, _ = eng(be)
    j = journal.Journal()
    j.acquire()
    try:
        with pytest.raises(engine.FlashError) as ei:
            e.write(pk.analyze(f))
        assert ei.value.code == "busy" and dev.identity == "FM-1_91060"
    finally:
        j.release()
    # a lock file of a dead process does not block
    j.dir.mkdir(parents=True, exist_ok=True)
    j.lockfile.write_text("999999")
    assert e.write(pk.analyze(f)).identity == "FM-1_91070"


def test_dry_run_and_cancel_send_nothing(env):
    from fm1companion.flash import engine, package as pk
    f = pkgfile(env, "FM-1_91070")
    dev, be = sim_setup(dict(identity="FM-1_91060"), [f.read_bytes()])
    e, logs = eng(be)
    r = e.write(pk.analyze(f), dry_run=True)
    assert r.dry_run and dev.identity == "FM-1_91060" and bytes(engine.fi.UPGRADE) not in dev.sends
    assert any("Probelauf" in x for x in logs)
    ev = threading.Event()
    ev.set()
    e2, _ = eng(be, cancel=ev)
    with pytest.raises(engine.FlashError) as ei:
        e2.write(pk.analyze(f))
    assert ei.value.code == "cancelled" and bytes(engine.fi.UPGRADE) not in dev.sends


def test_two_devices_ambiguous_without_port(env):
    from fm1companion.flash import engine, package as pk, sim
    f = pkgfile(env, "FM-1_91070")
    a, b = sim.SimFM1(identity="FM-1_91060"), sim.SimFM1(identity="FM-1_900", name="SLOOP")
    be = sim.SimBackend([a, b])
    e, _ = eng(be)
    with pytest.raises(engine.FlashError) as ei:
        e.write(pk.analyze(f))
    assert ei.value.code == "ambiguous"


def test_generic_identity_probe(env):
    from fm1companion.flash import sim
    dev = sim.SimFM1(identity="FM-1_015", policy="stock", name="USB Composite Device")
    e, _ = eng(sim.SimBackend([dev]))
    assert e.probe().text == "FM-1_015"
    assert e.probe(want=lambda i: i.loader) is None
    o = sim.SimFM1(identity="FM1_OFFICIAL_OPEN", policy="open", name="FM-1")
    e2, _ = eng(sim.SimBackend([o]))
    assert e2.probe().model == "FM-1-OPEN"


def test_devices_classify():
    from fm1companion import devices as d
    assert d.classify_port("USB Composite Device:USB Composite Device MIDI 1 24:0", "4c4a:c755") == "strong"
    assert d.classify_port("USB Composite Device:USB Composite Device MIDI 1 24:0", "046d:0a44") == ""
    assert d.classify_port("Melodee FM-1:Melodee FM-1 MIDI 1 20:0") == "strong"
    assert d.classify_port("Midi Through:Midi Through Port-0 14:0") == ""
    assert d.classify_port("Jangada Update", "1209:0002") == "strong"


# ---------------------------------------------------------------- firmware.py

def test_firmware_install_uses_engine_and_checks_hash(env, monkeypatch):
    from fm1companion import firmware
    from fm1companion.flash import engine, sim
    importlib.reload(firmware)
    f = pkgfile(env, "FM-1_91070")
    dev, be = sim_setup(dict(identity="FM-1_91060"), [f.read_bytes()])
    monkeypatch.setattr(engine, "default_backend", lambda: be)
    monkeypatch.setattr(engine, "default_port_filter", lambda n: "Through" not in n)
    s = firmware.spec("sloop")
    assert firmware.install(s, f, "v1", log=lambda *_: None) == "FM-1_91070"
    bad = firmware.FirmwareSpec(**{**s.__dict__, "sha256": "0" * 64})
    with pytest.raises(firmware.InstallError, match="Prüfsumme"):
        firmware.install(bad, f, "v1", log=lambda *_: None)


def test_fixed_download_hash(env, monkeypatch):
    from fm1companion import firmware
    importlib.reload(firmware)
    s = firmware.spec("stock-v16")
    rel = firmware.fetch_releases(s)[0]
    monkeypatch.setattr(firmware, "_fetch", lambda url, dest, progress=None: Path(dest).write_bytes(b"neu"))
    with pytest.raises(firmware.ChecksumError):
        firmware.download(s, rel, log=lambda *_: None)
    assert not list((firmware.load_settings().firmware_dir / "stock-v16").rglob("*.fwsc"))


# ---------------------------------------------------------------- rescue level 2 (WL82 UBOOT)

def fake_sysfs(root: Path, vendor="WL82", model="UBOOT1.00", usb="4c4a:8057", sg="sg2"):
    usbdev = root / "devices" / "usb1" / "1-1"
    scsi = usbdev / "1-1:1.0" / "host0" / "target0:0:0" / "0:0:0:0"
    (scsi / "block" / "sdb").mkdir(parents=True)
    (scsi / "block" / "sdb" / "size").write_text("2048\n")
    (scsi / "vendor").write_text(vendor + "\n")
    (scsi / "model").write_text(model + "\n")
    v, p = usb.split(":")
    (usbdev / "idVendor").write_text(v)
    (usbdev / "idProduct").write_text(p)
    (root / "class" / "scsi_generic" / sg).mkdir(parents=True)
    os.symlink(scsi, root / "class" / "scsi_generic" / sg / "device")
    (root / "bus" / "usb" / "devices").mkdir(parents=True)
    os.symlink(usbdev, root / "bus" / "usb" / "devices" / "1-1")


def test_uboot_ucun_flash_message(tmp_path):
    """UCUN flash (0xB36014): the ROM rescue stops (unchanged) – the suite says why (in the UI language)."""
    from fm1companion.flash import uboot
    assert uboot.flash_hint("Flash ID    0xB36014 (expected 0x856014, 1 MiB), type 0x00") == uboot.UCUN_TEXT
    assert uboot.flash_hint("this does not look like an FM-1: flash ID 0xB36014 is not 0x856014. Stopping")
    assert uboot.flash_hint("key=980F id=b36014") and not uboot.flash_hint("key=980F id=856014")
    assert not uboot.flash_hint("Flash ID    0x856014 (expected 0x856014, 1 MiB)")
    assert "nicht" in uboot.UCUN_TEXT and "nichts geschrieben" in uboot.UCUN_TEXT
    tool = tmp_path / "tool.py"                       # stands in for the Unbricker: reports the flash, stops
    tool.write_text("print('Flash ID    0xB36014 (expected 0x856014, 1 MiB), type 0x00')\n"
                    "print('ERROR: flash ID 0xB36014 is not 0x856014. Stopping; nothing was written')\n"
                    "raise SystemExit(1)\n")
    logs = []
    r = uboot.Runner(dry_run=True, log=logs.append, cwd=tmp_path)
    rc = r.run("info", script=tool)
    assert rc == 1 and r.hint == uboot.UCUN_TEXT and logs.count("HINWEIS: " + uboot.UCUN_TEXT) == 1


def test_uboot_scan_strict(tmp_path):
    from fm1companion.flash import uboot
    ok = tmp_path / "ok"
    fake_sysfs(ok)
    devs = uboot.scan(str(ok))
    assert len(devs) == 1 and devs[0].sg == "/dev/sg2" and devs[0].block == "/dev/sdb" and devs[0].size == 1 << 20
    assert uboot.usb_present(str(ok)) == ["1-1"]
    other = tmp_path / "other"
    fake_sysfs(other, usb="0781:5583")                   # USB stick that calls itself WL82: never
    assert uboot.scan(str(other)) == []
    named = tmp_path / "named"
    fake_sysfs(named, model="SanDisk")                   # right ID, wrong model: never
    assert uboot.scan(str(named)) == []


@need_v15
def test_unbricker_dry_run_restore(env, tmp_path):
    """MvaveFM1Unbricker against its built-in mock FM-1: double backup, writing 0x4000.., reading back"""
    from fm1companion.flash import uboot
    logs = []
    r = uboot.Runner(dry_run=True, log=logs.append, cwd=tmp_path / "r")
    assert r.restore(str(V15)) == 0
    text = "\n".join(logs)
    assert "6edf3c37fb5bbbc33607c89375ee024d5477c17914d72221c8c68e58a8255686" in text
    assert "0x4000 .. 0x92FFF" in text and "byte-for-byte identical" in text
    assert list((tmp_path / "r" / "backups").glob("*/full-flash-before.bin"))
    assert "pkexec" not in logs[0] and "--dry-run" in logs[0]
    real = uboot.Runner(dry_run=False, cwd=tmp_path, root_cmd=["pkexec"]).argv("restore", "x.fwsc")
    assert real[0] == "pkexec" and "--dry-run" not in real


def test_vendored_unbricker_own_tests():
    """the original project's tests run unchanged against the bundled copy"""
    d = ROOT / "fm1companion" / "vendor" / "unbricker"
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"], cwd=d,
                       capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]


# ------------------------------------------- acceptance round (p1): unexpected errors, journal, profiles

@pytest.mark.parametrize("stage,expect_state,active_after", [
    ("start", "safe", False),        # nothing sent yet
    ("write", "paused", True),       # in the middle of writing: the device waits in the loader, the journal stays active
    ("reboot", "restart", False),    # after writing
])
def test_unexpected_exception_keeps_journal_consistent(env, monkeypatch, stage, expect_state, active_after):
    """A program/driver error (no InstallError) must not leave the journal in limbo."""
    from fm1companion.flash import engine, package as pk
    from fm1companion.vendor import fm1_install as fi
    f = pkgfile(env, "FM-1_91070")
    dev, be = sim_setup(dict(identity="FM-1_91060"), [f.read_bytes()])
    e, logs = eng(be)
    orig_install = fi.Updater.install

    def boom_install(self, dev_, image, product, step=lambda *a: None, loader=None):
        def step2(k, a=None):
            step(k, a)
            if k == stage and (k != "write" or (a or 0) >= 40):
                raise OSError(5, "Input/output error")
        return orig_install(self, dev_, image, product, step2, loader)
    monkeypatch.setattr(fi.Updater, "install", boom_install)
    with pytest.raises(engine.FlashError) as ei:
        e.write(pk.analyze(f))
    assert ei.value.code == "internal" and ei.value.state == expect_state
    assert "Input/output error" in ei.value.detail and "Unerwarteter Fehler" in str(ei.value)
    act = e.journal.active
    assert bool(act) == active_after
    hist = e.journal.history()
    assert hist and hist[-1]["phase"] in ("failed", "safe") and "Unerwarteter" in hist[-1]["error"]
    assert not e.journal.busy() and not e.journal.lockfile.exists()     # the lock is always released
    if active_after:
        assert act["sha256"] == pk.analyze(f).sha256 and act["phase"] == "failed"


def test_recovery_refuses_swapped_package(env):
    """Rescue: the package from the journal must have the journal's checksum (the file could have been swapped)."""
    from fm1companion.flash import engine, package as pk
    f = pkgfile(env, "FM-1_91070")
    other = pkgfile(env, "FM-1_91080", seed=3)
    dev, be = sim_setup(dict(identity="FM-1_91060", power_loss_at=0.5), [f.read_bytes(), other.read_bytes()])
    e, _ = eng(be)
    with pytest.raises(engine.FlashError):
        e.write(pk.analyze(f))
    act = e.journal.active
    Path(act["package"]).write_bytes(other.read_bytes())         # swapped
    before = list(dev.sends)
    with pytest.raises(engine.FlashError) as ei:
        e.write(pk.analyze(Path(act["package"])), expect_sha=act["sha256"])
    assert ei.value.code == "badpkg" and dev.sends == before and dev.state != "app"


def test_profile_names_unique(tmp_path):
    from fm1companion.devices import ProfileStore
    st = ProfileStore(tmp_path / "devices.json")
    a, b = st.create(), st.create()
    assert (a.name, b.name) == ("FM-1 A", "FM-1 B")
    st.delete(a.id)
    c = st.create()
    assert c.name == "FM-1 A"                                     # not "FM-1 B" again
    d = st.create("FM-1 B")
    assert d.name == "FM-1 B (2)" and d.folder_name != b.folder_name
    assert st.name_taken("fm-1 b", except_id=d.id) is b           # case does not matter
    assert st.name_taken("FM-1 B.") is b                          # the same backup folder "FM-1 B"
    assert st.name_taken("FM-1/B") is None                        # another folder "FM-1_B"
    assert st.name_taken("Mein Gerät") is None


def test_plan_unknown_firmware_names_loss(env):
    from fm1companion import firmware
    from fm1companion.flash.planner import DeviceState, Target, plan_install
    st = DeviceState(identity="FM-1_900", felucca=False, firmware_key="")
    p = plan_install(st, Target.from_spec(firmware.spec("melodee"), tag="v1.6.0"))
    assert p.lost and "unbekannt" in p.lost[0]


V16 = Path(os.environ.get("FM1_TEST_PKGDIR", "/nonexistent")) / "v16.fwsc"
need_v16 = pytest.mark.skipif(not (V15.exists() and V16.exists()),
                              reason="original V15 (/tmp/fm1_open) or V16 (FM1_TEST_PKGDIR/v16.fwsc) missing")


@need_v16
def test_v16_only_via_v15_and_back(env):
    """Melodee -> V16 directly: CHECK HEAD (-5, nothing written); Melodee -> V15 -> V16 -> V15 works in the simulation"""
    from fm1companion.flash import engine, package as pk
    dev, be = sim_setup(dict(identity="FM-1_91060"), [V15.read_bytes(), V16.read_bytes()])
    e, _ = eng(be)
    with pytest.raises(engine.FlashError) as ei:
        e.write(pk.analyze(V16), port="Melodee FM-1")
    assert ei.value.code == "checkhead" and dev.identity == "FM-1_91060" and not dev.flash
    assert e.write(pk.analyze(V15), port="Melodee FM-1").identity == "FM-1_015"
    assert e.write(pk.analyze(V16)).identity == "FM-1_016"
    assert e.write(pk.analyze(V15)).identity == "FM-1_015"
