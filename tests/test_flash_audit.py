# SPDX-License-Identifier: GPL-3.0-only
"""Regression tests for the flash audit after 2.6.0 (findings F-01 … F-19, SEC-02, SEC-10, SEC-12).

  python3 -m pytest tests/test_flash_audit.py -q
Everything in simulation (flash/sim.py) and against fake servers – no real FM-1, no network.
"""
from __future__ import annotations

import importlib
import io
import re
import sys
import threading
import time
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PATH = "1-1.2"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    for k, sub in (("XDG_CONFIG_HOME", "c"), ("XDG_DATA_HOME", "d"), ("XDG_CACHE_HOME", "k")):
        monkeypatch.setenv(k, str(tmp_path / sub))
    monkeypatch.setenv("HOME", str(tmp_path))    # the firmware folder (~/FM-1/Firmware) must not be the real one
    monkeypatch.delenv("FM1C_DEMO", raising=False)
    from fm1companion import config, i18n
    i18n.set_language("en")                      # these tests read the English texts
    importlib.reload(config)
    from fm1companion.flash import engine, journal, planner, robust, sim
    for m in (journal, engine, planner):
        importlib.reload(m)
    from fm1companion.vendor import fm1_install as fi
    monkeypatch.setattr(fi, "DELAY", dict(fi.DELAY, **sim.fast_delays()))
    monkeypatch.setattr(robust, "CAREFUL", dict(robust.CAREFUL, **sim.fast_careful_delays()))
    yield tmp_path
    i18n.set_language(None)
    for m in (engine, planner):
        importlib.reload(m)


def pkgfile(tmp, product, family="felucca", seed=1):
    from fm1companion.flash import package as pk
    p = Path(tmp) / f"{product}-{family}-{seed}.fwsc"
    p.write_bytes(pk.build_package(product, family, seed=seed))
    return p


def stock_pkg(tmp, product="FM-1_015", seed=7):
    from fm1companion.flash import package as pk
    p = pkgfile(tmp, product, "unknown", seed)
    info = pk.analyze(p, "stock")
    assert info.family == "stock"
    return p, info


def device(packages=(), stock=(), **kw):
    from fm1companion.flash import sim
    kw.setdefault("identity", "FM-1_91060")
    kw.setdefault("usb_path", PATH)
    dev = sim.SimFM1(**kw)
    for raw in packages:
        dev.register(raw)
    for raw in stock:
        dev.register(raw, stock=True)
    return dev


def engine_for(devices, **kw):
    from fm1companion.flash import engine, sim
    be = sim.SimBackend(list(devices))
    logs = []
    e = engine.Engine(backend=be, port_filter=lambda n: "Through" not in n, log=logs.append, **kw)
    return e, logs, be


def pin(path=PATH):
    from fm1companion.flash.engine import Pin
    return Pin(usb_path=path)


def upgrades(dev) -> int:
    from fm1companion.flash import sim
    return sum(1 for s in dev.sends if s == sim.UPGRADE)


def answers(dev, addr: int) -> int:
    """how often the host answered the request for this address"""
    from fm1companion.flash import sim
    n = 0
    for s in dev.sends:
        if s in (sim.UPGRADE, sim.HS):
            continue
        r = sim.parse_reply(s)
        n += bool(r and r[0] == addr)
    return n


def final_step1_reply(tmp, target) -> int:
    """number of the host's answer that ends step 1 (counted over the device's life)"""
    dev = device([target.read_bytes()])
    seen = {}
    orig = dev._restart

    def restart(state, *a, **k):
        if state == "loader" and "n" not in seen:
            seen["n"] = dev.replies
        return orig(state, *a, **k)
    dev._restart = restart
    e, _logs, _be = engine_for([dev])
    from fm1companion.flash import package as pk
    e.write(pk.analyze(target), pin=pin())
    return seen["n"]


# ------------------------------------------------------------ F-03: Cancel during step 1

def test_cancel_in_step1_is_honoured_on_a_felucca_firmware(env):
    """Cancel right after the first UPGRADE: the app stops answering before the FM-1 is told to go on – no loader
    is staged, nothing is written, the journal says "safe" and the message does not claim anything else"""
    from fm1companion.flash import engine, package as pk
    f = pkgfile(env, "FM-1_900", seed=3)
    dev = device([f.read_bytes()])
    cancel = threading.Event()
    e, logs, _be = engine_for([dev], cancel=cancel)
    orig = dev.receive

    def recv(data, link):
        from fm1companion.flash import sim
        if data == sim.UPGRADE:
            cancel.set()                        # the user clicks Cancel while the FM-1 checks the package
        return orig(data, link)
    dev.receive = recv
    with pytest.raises(engine.FlashError) as ei:
        e.write(pk.analyze(f), pin=pin(), cancel_step1=True)
    assert ei.value.code == "cancelled" and ei.value.state == engine.SAFE
    assert "step 1" in str(ei.value) and "Nothing was written" in str(ei.value)
    assert "before anything was sent" not in str(ei.value)          # an UPGRADE was sent: do not say otherwise
    assert answers(dev, 0xE0000000) == 0 and not any("loader staged" in x for x in dev.log)
    assert dev.state == "app" and dev.identity == "FM-1_91060" and not dev.flash
    assert e.journal.active is None and e.journal.history()[-1]["phase"] == "safe"
    assert any(x == engine.LOG_STEP1_CANCEL for x in logs) and not any(x == engine.LOG_NO_CANCEL for x in logs)


def test_cancel_at_the_last_request_of_step1_is_still_honoured(env):
    """the latest possible moment: the device already asks for the final confirmation – it is not given"""
    from fm1companion.flash import engine, package as pk
    f = pkgfile(env, "FM-1_900", seed=3)
    dev = device([f.read_bytes()])
    cancel = threading.Event()
    e, _logs, _be = engine_for([dev], cancel=cancel)
    orig = dev._emit

    def emit(link, want):
        if want[0] == 0xE0000000:
            cancel.set()
        return orig(link, want)
    dev._emit = emit
    with pytest.raises(engine.FlashError) as ei:
        e.write(pk.analyze(f), pin=pin(), cancel_step1=True)
    assert ei.value.code == "cancelled" and ei.value.state == engine.SAFE
    assert answers(dev, 0xE0000000) == 0 and dev.state == "app" and not dev.flash


@pytest.mark.parametrize("ask_for_it", [False, True])
def test_cancel_in_step1_is_never_claimed_where_it_cannot_happen(env, ask_for_it):
    """the original firmware as the running firmware (its step 1 is unknown): the engine keeps answering whatever the
    caller asked for, says before UPGRADE that Cancel is off, and says so again when a cancel arrives too late"""
    from fm1companion.flash import engine, package as pk
    f = pkgfile(env, "FM-1_900", seed=3)
    dev = device([f.read_bytes()], identity="FM-1_014", policy="stock", name="USB Composite Device")
    cancel = threading.Event()
    e, logs, _be = engine_for([dev], cancel=cancel)
    orig = dev.receive

    def recv(data, link):
        from fm1companion.flash import sim
        if data == sim.UPGRADE:
            cancel.set()
        return orig(data, link)
    dev.receive = recv
    r = e.write(pk.analyze(f), pin=pin(), cancel_step1=ask_for_it)
    assert r.identity == "FM-1_900"
    assert logs.index(engine.LOG_NO_CANCEL) < logs.index(engine.LOG_WRITING)
    assert engine.LOG_TOO_LATE in logs and engine.LOG_STEP1_CANCEL not in logs


def test_without_the_flag_a_felucca_step1_is_not_interrupted(env):
    """mutation guard: cancel_step1 defaults to off – the old behaviour, now announced honestly"""
    from fm1companion.flash import package as pk
    f = pkgfile(env, "FM-1_900", seed=3)
    dev = device([f.read_bytes()])
    cancel = threading.Event()
    e, logs, _be = engine_for([dev], cancel=cancel)
    orig = dev.receive

    def recv(data, link):
        from fm1companion.flash import sim
        if data == sim.UPGRADE:
            cancel.set()
        return orig(data, link)
    dev.receive = recv
    from fm1companion.flash import engine
    assert e.write(pk.analyze(f), pin=pin()).identity == "FM-1_900"
    assert engine.LOG_NO_CANCEL in logs and engine.LOG_TOO_LATE in logs


def test_resume_announces_that_cancel_is_off(env):
    """a device already in update mode: no step 1 – the log must still switch Cancel off before the write"""
    from fm1companion.flash import engine, package as pk
    f = pkgfile(env, "FM-1_900", seed=3)
    dev = device([f.read_bytes()])
    dev.state, dev.loader_kind, dev.loader_id = "loader", "felucca", "ota-FM-1_900"
    e, logs, _be = engine_for([dev])
    assert e.write(pk.analyze(f), pin=pin()).identity == "FM-1_900"
    assert logs.index(engine.LOG_NO_CANCEL) < logs.index(engine.LOG_WRITING)


# ------------------------------------------------------------ F-04: the last answer of step 1 is lost

@pytest.mark.parametrize("careful", [False, True])
def test_lost_final_answer_of_step1_is_answered_again(env, careful):
    from fm1companion.flash import package as pk, sim
    f = pkgfile(env, "FM-1_900", seed=3)
    n = final_step1_reply(env, f)
    dev = device([f.read_bytes()], faults=sim.SimFaults(drop_replies={n}))
    e, _logs, _be = engine_for([dev], careful=careful)
    assert e.write(pk.analyze(f), pin=pin()).identity == "FM-1_900"
    assert answers(dev, 0xE0000000) == 2 and e.journal.active is None


def test_step1_that_never_completes_is_not_called_update_mode(env):
    """every answer to the last request is lost: the device gives up and carries on. The app looks once for the
    normal identity and reports "nothing written" – not "waiting in update mode", and no stale journal entry"""
    from fm1companion.flash import engine, package as pk, sim
    f = pkgfile(env, "FM-1_900", seed=3)
    n = final_step1_reply(env, f)
    dev = device([f.read_bytes()], faults=sim.SimFaults(drop_replies=set(range(n, n + 20))))
    e, _logs, _be = engine_for([dev], careful=False)
    with pytest.raises(engine.FlashError) as ei:
        e.write(pk.analyze(f), pin=pin())
    assert ei.value.code == "notstaged" and ei.value.state == engine.SAFE
    text = str(ei.value)
    assert "FM-1_91060" in text and "Nothing was written" in text and "Resume" not in text
    assert dev.state == "app" and dev.identity == "FM-1_91060" and not dev.flash
    assert e.journal.active is None and e.journal.history()[-1]["phase"] == "safe"


def test_careful_mode_repeats_step1_after_a_lost_final_answer(env):
    """careful mode may simply do step 1 again (it writes nothing): the second round gets through"""
    from fm1companion.flash import package as pk, sim
    f = pkgfile(env, "FM-1_900", seed=3)
    n = final_step1_reply(env, f)
    dev = device([f.read_bytes()], faults=sim.SimFaults(drop_replies=set(range(n, n + 3))))
    e, _logs, _be = engine_for([dev], careful=True)
    assert e.write(pk.analyze(f), pin=pin()).identity == "FM-1_900"
    assert e.flight.step1_retries == 1


def test_messages_name_buttons_that_exist(env):
    from fm1companion.flash import engine
    for code in ("noloader", "notstaged", "second", "changed"):
        assert code in engine.MESSAGES
    every = " ".join(engine.MESSAGES.values()) + engine.PAUSED_ADVICE
    assert "“Resume”" not in every and "Finish installation" in engine.MESSAGES["noloader"]
    assert "probably" in engine.MESSAGES["noloader"] and "probably" in engine.PAUSED_ADVICE


# ------------------------------------------------------------ F-06: no device state the app did not check

def test_dropout_in_step2_says_probably_and_keeps_where_it_stopped(env):
    from fm1companion.flash import engine, package as pk
    f = pkgfile(env, "FM-1_9010100")
    dev = device([f.read_bytes()], power_loss_at=0.5, resumable=False)      # ends in ROM boot mode, not update mode
    e, _logs, _be = engine_for([dev], careful=False)
    with pytest.raises(engine.FlashError) as ei:
        e.write(pk.analyze(f), pin=pin())
    assert ei.value.state == engine.PAUSED
    text = str(ei.value)
    assert "probably waiting in update mode" in text and "stage 2" in text
    assert "The FM-1 is waiting in update mode" not in text
    act = e.journal.active
    assert act and act["phase"] == "failed" and act["stopped_in"] == "writing"


def test_unexpected_error_while_writing_gives_the_same_advice(env):
    from fm1companion.flash import engine
    err = engine.Engine._unexpected(OSError(5, "Input/output error"), "writing")
    assert err.state == engine.PAUSED and "probably" in str(err) and "Finish installation" in str(err)


# ------------------------------------------------------------ F-05 / F-14: journal

def test_chain_carries_the_usb_path_and_expires(env):
    from fm1companion.flash import journal
    j = journal.Journal(env / "j")
    j.begin_chain(key="sloop", name="SLOOP", tag="v2.8.5", file="", device_id="p1", usb_path="1-1.2")
    assert j.chain["usb_path"] == "1-1.2" and j.chain["device_id"] == "p1"
    d = j.load()
    d["chain"]["started"] = time.time() - journal.CHAIN_MAX_AGE - 60
    j._save(d)
    assert j.chain is None                      # two weeks later it is no longer offered
    d["chain"]["started"] = time.time() - journal.CHAIN_MAX_AGE + 3600
    j._save(d)
    assert j.chain is not None
    assert journal.CHAIN_MAX_AGE == 14 * 24 * 3600


def test_chain_of_another_window_is_put_back(env):
    from fm1companion.flash import journal
    j = journal.Journal(env / "j")
    first = j.begin_chain(key="skiff", name="Skiff", tag="v2.2")
    j.begin_chain(key="sloop", name="SLOOP", tag="v2.8.5")
    j.restore_chain(first)
    assert j.chain["key"] == "skiff"
    j.restore_chain(None)
    assert j.chain is None


def test_package_copies_are_rehashed_and_removed(env):
    from fm1companion.flash import journal, package as pk
    j = journal.Journal(env / "j")
    f = pkgfile(env, "FM-1_91070")
    sha = pk.analyze(f).sha256
    kept = j.keep_package(f, sha)
    kept.write_bytes(b"damaged copy")
    assert j.keep_package(f, sha).read_bytes() == f.read_bytes()        # a copy with the right name is not trusted
    j.begin(target="X", package=str(kept), sha256=sha)
    other = j.dir / "packages" / ("0" * 64 + ".fwsc")
    other.write_bytes(b"old")
    j.finish("failed", "x", keep_active=True)
    assert kept.exists() and other.exists()                             # an entry kept for the rescue: nothing goes
    assert j.active["stopped_in"] == "prepared" and j.active["phase"] == "failed"
    j.clear_active()
    assert j.active is None and not kept.exists() and not other.exists()
    kept = j.keep_package(f, sha)
    j.begin(target="X", package=str(kept), sha256=sha)
    j.finish("done")
    assert not kept.exists()


def test_stale_lock_with_a_reused_pid_does_not_block(env):
    """a crashed installation left its lock; another program now has that PID – the lock must not hold"""
    import os
    from fm1companion.flash import journal
    j = journal.Journal(env / "j")
    j.dir.mkdir(parents=True)
    me = os.getppid()                           # a live process that is not this one
    if not journal._started(me):
        pytest.skip("no /proc start times on this system")
    j.lockfile.write_text(f"{me} 1")            # same PID, another start time: not the process that wrote the lock
    j.acquire()
    j.release()
    j.lockfile.write_text(f"{me} {journal._started(me)}")           # really that process: busy
    with pytest.raises(journal.Busy):
        j.acquire()
    j.lockfile.write_text(str(me))              # a lock of an older app version (PID only): as before, busy
    with pytest.raises(journal.Busy):
        j.acquire()
    j.lockfile.unlink()


def test_planner_has_no_dead_journal_lock(env):
    from fm1companion.flash.planner import DeviceState, Target, plan_install
    t = Target(name="X", product="FM-1_900", family="felucca", key="x")
    act = {"phase": "writing", "pid_alive": True}
    assert plan_install(DeviceState(identity="FM-1_900", felucca=True, firmware_key="x"), t, journal_active=act).ok
    assert not plan_install(DeviceState(identity="FM-1_900", felucca=True, firmware_key="x", busy=True), t).ok


# ------------------------------------------------------------ F-08: the plan's device state is checked again

def test_engine_refuses_when_the_fm1_is_not_what_the_plan_was_made_for(env):
    from fm1companion.flash import engine, package as pk
    f = pkgfile(env, "FM-1_900", seed=3)
    dev = device([f.read_bytes()])              # answers FM-1_91060
    e, _logs, _be = engine_for([dev])
    for dry in (True, False):
        with pytest.raises(engine.FlashError) as ei:
            e.write(pk.analyze(f), pin=pin(), expect_identity="FM-1_015", dry_run=dry)
        assert ei.value.code == "changed" and ei.value.state == engine.SAFE
        assert "FM-1_91060" in str(ei.value) and "FM-1_015" in str(ei.value) and "Nothing was sent" in str(ei.value)
    assert upgrades(dev) == 0 and e.journal.active is None
    assert e.write(pk.analyze(f), pin=pin(), expect_identity="FM-1_91060").identity == "FM-1_900"


def test_firmware_install_passes_the_expectation_on(env, monkeypatch):
    from fm1companion import firmware
    from fm1companion.flash import engine
    importlib.reload(firmware)
    f = pkgfile(env, "FM-1_91070")
    dev = device([f.read_bytes()])
    from fm1companion.flash import sim
    be = sim.SimBackend([dev])
    monkeypatch.setattr(engine, "default_backend", lambda: be)
    monkeypatch.setattr(engine, "default_port_filter", lambda n: "Through" not in n)
    s = firmware.spec("sloop")
    with pytest.raises(firmware.InstallError) as ei:
        firmware.install(s, f, "v1", log=lambda *_: None, expect_identity="FM-1_015")
    assert ei.value.code == "changed" and upgrades(dev) == 0
    assert firmware.install(s, f, "v1", log=lambda *_: None, expect_identity="FM-1_91060") == "FM-1_91070"


# ------------------------------------------------------------ F-09: FM1 Open

def test_fm1_open_v14_identity_is_blocked(env):
    from fm1companion.flash.planner import DeviceState, Target, plan_install
    t = Target(name="Melodee", product="FM-1_91060", family="felucca", key="melodee")
    for st in (DeviceState(identity="FM-1_8000014"),
               DeviceState(identity="FM-1_8000014", firmware_key="fm1-open"),
               DeviceState(identity="FM-1_8123456", firmware_key="fm1-open"),
               DeviceState(identity="FM1_OFFICIAL_OPEN"), DeviceState(identity="FM1_COMMUNITY_3")):
        p = plan_install(st, t)
        assert st.open_firmware and not p.ok and any("Open firmware" in b for b in p.blockers), st
        assert not any("not an FM-1" in b for b in p.blockers)
    # FoMni lives in the same number range and is not FM1 Open
    st = DeviceState(identity="FM-1_8000401", firmware_key="fomni")
    assert not st.open_firmware and plan_install(st, t).ok


# ------------------------------------------------------------ F-11: unpinned, a second device appears

def test_unpinned_install_stops_when_a_second_fm1_answers_next_to_the_loader(env):
    from fm1companion.flash import engine, package as pk, sim
    f = pkgfile(env, "FM-1_900", seed=3)
    a = device([f.read_bytes()], faults=sim.SimFaults(loader_delay=0.15))
    b = device([f.read_bytes()], usb_path="1-1.3", name="Other FM-1")
    b.state, b.loader_kind, b.loader_id = "loader", "felucca", "ota-FM-1_900"
    e, _logs, be = engine_for([a], details=lambda n: {})
    be.details = lambda n: {}
    orig = a._restart

    def restart(state, *args, **kw):            # B is plugged in at the moment A leaves the bus to restart: for a
        if state == "loader" and b not in be.devices:      # while only B's loader answers
            be.devices.insert(0, b)
        return orig(state, *args, **kw)
    a._restart = restart
    with pytest.raises(engine.FlashError) as ei:
        e.write(pk.analyze(f), port="Melodee FM-1")
    assert ei.value.code == "second" and ei.value.state == engine.PAUSED
    assert "second FM-1" in str(ei.value) and "Nothing was sent to the update loader" in str(ei.value)
    assert upgrades(b) == 0 and not b.flash and not a.flash
    assert upgrades(a) == 1                     # step 1 only; the write (a second UPGRADE) never started


def test_unpinned_install_alone_still_works(env):
    from fm1companion.flash import package as pk
    f = pkgfile(env, "FM-1_900", seed=3)
    a = device([f.read_bytes()])
    e, _logs, be = engine_for([a], details=lambda n: {})
    be.details = lambda n: {}
    assert e.write(pk.analyze(f), port="Melodee FM-1").identity == "FM-1_900"


# ------------------------------------------------------------ F-13: family of a firmware the user added

def test_added_firmware_is_not_assumed_to_be_felucca(env):
    from fm1companion import firmware
    from fm1companion.flash import package as pk, planner
    added = firmware.FirmwareSpec("xfw", "XFW", "someone/xfw", "x-{ver}.fwsc", builtin=False)
    assert added.installer and planner._family(added) == ""
    assert planner._family(firmware.spec("melodee")) == "felucca" and planner._family(firmware.spec("sloop")) == "felucca"
    assert planner._family(firmware.spec("karma")) == "felucca" and planner._family(firmware.spec("stock-v15")) == "stock"
    st = planner.DeviceState(identity="FM-1_91060", felucca=True, firmware_key="melodee")
    # before the download: the plan says the loader is not known yet – it does not claim "cannot be resumed"
    p = planner.plan_install(st, planner.Target.from_spec(added))
    assert p.ok and any("does not know the update loader of this package yet" in w for w in p.warnings)
    assert not any("can NOT be resumed" in w for w in p.warnings) and "power" in p.confirmations
    # with the file at hand: a loader the app does not know blocks in the plan, not only when writing starts
    unknown = pk.analyze(pkgfile(env, "FM-1_950", "unknown"))
    p = planner.plan_install(st, planner.Target.from_spec(added, file=unknown.path, package=unknown))
    assert not p.ok and any("does not know the update loader" in b for b in p.blockers)
    fel = pk.analyze(pkgfile(env, "FM-1_950", "felucca", seed=2))
    assert planner.plan_install(st, planner.Target.from_spec(added, file=fel.path, package=fel)).ok


# ------------------------------------------------------------ F-15 / F-16: texts, demo device

def test_start_line_names_the_target_not_the_current_identity(env):
    from fm1companion.flash import package as pk
    sp, sinfo = stock_pkg(env)
    dev = device(stock=[sp.read_bytes()])
    e, logs, _be = engine_for([dev])
    e.write(sinfo, pin=pin(), label="Original firmware V15")
    line = next(x for x in logs if x.startswith("Starting"))
    assert "Original firmware V15" in line and "reports FM-1_91060" in line
    assert "update to FM-1_91060" not in line


def test_stock_v13_is_called_v13(env):
    from fm1companion.flash.planner import DeviceState, Target, current_name, plan_install
    assert "V13" in current_name(DeviceState(identity="FM-1_009")) and "V9" not in current_name(DeviceState(identity="FM-1_009"))
    assert "V14" in current_name(DeviceState(identity="FM-1_014"))
    p = plan_install(DeviceState(identity="FM-1_009"), Target(name="Melodee", product="FM-1_91060", family="felucca",
                                                             key="melodee"))
    assert "V13" in p.v15_reason and "V9 " not in p.v15_reason


def test_demo_device_survives_a_usb_glitch(env):
    from fm1companion.flash import demo, sim
    d = demo.DemoDevice("demo-in", "FM-1_91060", "felucca")
    d.faults = sim.SimFaults(same_name=True, glitch_s=0.01)
    d.glitch()                                  # TypeError before: _restart() lacked the "stale" parameter
    assert d.pending_state[0] == "app" and d.app_name == "demo-in"


# ------------------------------------------------------------ F-01 / F-10: the V15 step, honestly

def _switch_plan(**kw):
    from fm1companion.flash.planner import DeviceState, Target, plan_install
    st = DeviceState(identity="FM-1_91060", felucca=True, firmware_key="melodee", firmware_name="Melodee")
    t = Target(name="SLOOP", product="FM-1_900", family="felucca", key="sloop",
               requires_stock_v15=kw.pop("locked", False))
    return plan_install(st, t, **kw)


def test_v15_step_claims_only_what_is_known(env):
    for locked in (False, True):
        p = _switch_plan(locked=locked)
        assert p.via_stock and p.v15_option == ("locked" if locked else "default_on")
        whole = " ".join([p.v15_reason, *p.warnings, *(s.text for s in p.steps)])
        low = whole.lower()
        assert "recommended" not in low and "no leftovers" not in low and "leftovers of" not in low
        assert "expect a start from the original V15" in whole and "bricked a device" in whole
        assert "no start protection" in whole and "keep power and USB connected" in whole
        assert "can remain in the flash" in whole and "garbled until the target firmware is installed" in whole
        assert "has not been run on a real FM-1 by this app yet" in whole
    direct = _switch_plan(via_v15=False)
    assert direct.direct and "bricked a device" in direct.warnings[0] and "V15 step" in direct.warnings[0]
    assert not any("has not been run on a real FM-1" in w for w in direct.warnings)      # no V15 step: no V15 note


def test_plan_says_whether_the_original_v15_is_on_this_computer(env):
    def step(p):
        return next(s for s in p.steps if s.kind == "stock").text
    assert "already on this computer" in step(_switch_plan(stock_cached=True))
    assert "will be downloaded" in step(_switch_plan(stock_cached=False)) and "0.9 MB" in step(_switch_plan(stock_cached=False))
    assert "this computer" not in step(_switch_plan()) and "downloaded" not in step(_switch_plan())


def test_stock_cached_only_counts_a_file_with_the_right_checksum(env, monkeypatch):
    from fm1companion import firmware
    importlib.reload(firmware)
    assert not firmware.stock_cached("stock-v15")
    s = firmware.spec("stock-v15")
    folder = firmware.load_settings().firmware_dir / s.key / "V15"
    folder.mkdir(parents=True)
    (folder / "FM-1_015.fwsc").write_bytes(b"something else")
    assert not firmware.stock_cached("stock-v15")
    monkeypatch.setattr(firmware, "_sha", lambda p: s.sha256)
    assert firmware.stock_cached("stock-v15")


def test_help_and_docs_do_not_repeat_the_leftovers_claim():
    for lang, words in (("en", ("no leftovers", "recommended, so")), ("de", ("keine Reste",)), ("es", ("no quedan restos",))):
        text = (ROOT / "fm1companion" / "data" / "help" / lang / "02-firmware.md").read_text("utf-8")
        assert not any(w in text for w in words), lang
        assert "FAILED -5" in text
    doc = (ROOT / "docs" / "flash-protocol.md").read_text("utf-8")
    assert "recommended, so no leftovers" not in doc and "has not been run on a real FM-1" in doc


def test_checkhead_message_names_the_way_out(env):
    from fm1companion.flash import engine
    m = engine.MESSAGES["checkhead"]
    assert "Nothing was written" in m and "Recovery" in m and "M-Flasher" in m


# ------------------------------------------------------------ F-02 / F-19: what was run on a real FM-1

def test_tested_table_matches_untested_md():
    from fm1companion import __version__
    from fm1companion.flash import tested
    doc = (ROOT / "docs" / "UNTESTED.md").read_text("utf-8")
    rows = dict(re.findall(r"^\| `(\w+)` – [^|]+\| (yes|no) \|$", doc, re.M))
    assert rows == {k: ("yes" if ok else "no") for k, (ok, _label) in tested.STEPS.items()}
    for key, (_ok, label) in tested.STEPS.items():
        assert f"`{key}` – {label} |" in doc, key
    ver = ".".join(__version__.split(".")[:2])
    assert f"(as of {ver}" in doc.splitlines()[0] and "2.3.1" not in doc
    assert [k for k, (ok, _l) in tested.STEPS.items() if ok] == ["update_melodee"]      # one hardware report so far


def test_tested_note_per_plan(env):
    from fm1companion.flash import tested
    from fm1companion.flash.planner import DeviceState, Target, plan_install
    mel = DeviceState(identity="FM-1_91060", felucca=True, firmware_key="melodee")
    update = plan_install(mel, Target(name="Melodee", product="FM-1_91060", family="felucca", key="melodee"))
    assert tested.plan_kinds(update) == ["update_melodee"]
    assert "has been run on a real device" in tested.note(tested.plan_kinds(update))
    switch = _switch_plan()
    assert tested.plan_kinds(switch) == ["switch", "v15_step"]
    n = tested.note(tested.plan_kinds(switch))
    assert n.startswith("Tested on a real FM-1 so far: an update within Melodee.") and "only in the simulation" in n
    assert "a switch to another firmware" in n and "the original V15" in n
    slo = DeviceState(identity="FM-1_900", felucca=True, firmware_key="sloop", ports=2, pinned=True)
    same = plan_install(slo, Target(name="SLOOP", product="FM-1_900", family="felucca", key="sloop"))
    assert tested.plan_kinds(same) == ["update_same", "two_devices"] and "only in the simulation" in tested.note(
        tested.plan_kinds(same))
    back = plan_install(mel, Target(name="V15", product="FM-1_015", family="stock", key="stock-v15"))
    assert tested.plan_kinds(back) == ["to_stock"]
    resume = plan_install(DeviceState(identity="ota-FM-1_900"), Target(name="X", product="FM-1_900", family="felucca",
                                                                       key="x"))
    assert tested.plan_kinds(resume) == ["resume"]
    for kinds in (["resume"], ["factory_voices"], ["own_file"], ["rom_restore"], ["to_stock"], []):
        assert "only in the simulation" in tested.note(kinds)


def test_careful_mode_note_says_it_is_simulation_only(env):
    from fm1companion.flash import robust, sim
    dev = device(hubs=[{"name": "1-1", "usb_id": "05e3:0610", "product": "USB2.0 Hub", "self_powered": True}])
    be = sim.SimBackend([dev])
    notes = robust.plan_notes(be.topology(pin()), "auto")
    assert any("only been run in the simulation" in n for n in notes)
    assert not any("only been run in the simulation" in n for n in robust.plan_notes(be.topology(pin()), "never"))


# ------------------------------------------------------------ F-07 / F-18 / SEC-12: downloads

class FakeResponse(io.BytesIO):
    def __init__(self, data: bytes, length=None):
        super().__init__(data)
        self.headers = {} if length is None else {"Content-Length": str(length)}
        self.status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture()
def fw(env, monkeypatch):
    from fm1companion import catalog, firmware
    importlib.reload(firmware)
    calls = []
    served = {}

    def raw(req, timeout=0, **k):
        calls.append(req.full_url)
        return served["answer"](req.full_url)
    monkeypatch.setattr(catalog, "_raw_urlopen", raw)
    monkeypatch.setattr(catalog, "urlopen", catalog._https_urlopen)
    firmware.calls, firmware.served = calls, served
    return firmware


def _added(fw):
    return fw.FirmwareSpec("xfw", "XFW", "someone/xfw", "x-{ver}.fwsc", builtin=False)


def test_truncated_download_is_not_kept(fw, env):
    from fm1companion.flash import package as pk
    raw = pk.build_package("FM-1_950", "felucca", seed=5)
    rel = fw.Release(tag="v1.0", prerelease=False, assets={"x-1.0.fwsc": "https://example.org/x-1.0.fwsc"})
    fw.served["answer"] = lambda url: FakeResponse(raw[:len(raw) // 2], len(raw))     # the connection dies half-way
    with pytest.raises(fw.DownloadError, match="broke off"):
        fw.download(_added(fw), rel, log=lambda *_: None)
    folder = fw.load_settings().firmware_dir / "xfw" / "v1.0"
    assert not list(folder.glob("*"))                                   # no half file, no .part
    fw.served["answer"] = lambda url: FakeResponse(raw, len(raw))
    got = fw.download(_added(fw), rel, log=lambda *_: None)
    assert got.fwsc.read_bytes() == raw and not pk.analyze(got.fwsc).damage


def test_damaged_cached_package_without_checksum_is_fetched_again(fw, env):
    """what an older version of the app left behind: a half file under the final name"""
    from fm1companion.flash import package as pk
    raw = pk.build_package("FM-1_950", "felucca", seed=5)
    rel = fw.Release(tag="v1.0", prerelease=False, assets={"x-1.0.fwsc": "https://example.org/x-1.0.fwsc"})
    folder = fw.load_settings().firmware_dir / "xfw" / "v1.0"
    folder.mkdir(parents=True)
    (folder / "x-1.0.fwsc").write_bytes(raw[:len(raw) // 2])
    fw.served["answer"] = lambda url: FakeResponse(raw, len(raw))
    logs = []
    got = fw.download(_added(fw), rel, log=logs.append)
    assert got.fwsc.read_bytes() == raw and any("damaged" in x for x in logs) and len(fw.calls) == 1
    got = fw.download(_added(fw), rel, log=logs.append)                 # intact now: not fetched again
    assert len(fw.calls) == 1 and any("already there" in x for x in logs)


def test_download_size_is_capped(fw, env, monkeypatch):
    rel = fw.Release(tag="v1.0", prerelease=False, assets={"x-1.0.fwsc": "https://example.org/x-1.0.fwsc"})
    assert fw.MAX_DOWNLOAD == 16 * 1024 * 1024
    fw.served["answer"] = lambda url: FakeResponse(b"x" * 10, fw.MAX_DOWNLOAD + 1)    # announced too big
    with pytest.raises(fw.DownloadError, match="larger than 16 MB"):
        fw.download(_added(fw), rel, log=lambda *_: None)
    monkeypatch.setattr(fw, "MAX_DOWNLOAD", 1000)
    fw.served["answer"] = lambda url: FakeResponse(b"x" * 5000)                      # no length announced, keeps coming
    with pytest.raises(fw.DownloadError, match="larger than"):
        fw.download(_added(fw), rel, log=lambda *_: None)
    assert not list((fw.load_settings().firmware_dir / "xfw" / "v1.0").glob("*"))


@pytest.mark.parametrize("url", ["http://example.org/x-1.0.fwsc", "file:///etc/passwd", "ftp://example.org/x-1.0.fwsc"])
def test_firmware_downloads_are_https_only(fw, env, url):
    rel = fw.Release(tag="v1.0", prerelease=False, assets={"x-1.0.fwsc": url})
    fw.served["answer"] = lambda u: FakeResponse(b"never", 5)
    with pytest.raises(urllib.error.URLError):
        fw.download(_added(fw), rel, log=lambda *_: None)
    assert fw.calls == [] and not list((fw.load_settings().firmware_dir / "xfw").rglob("*.fwsc"))
    assert not fw._head_ok(url) and fw.calls == []
    with pytest.raises(urllib.error.URLError):
        fw._http_json(url)
    assert "refused" in fw.friendly_error(urllib.error.URLError("only https addresses allowed: " + url))


def test_firmware_module_has_no_plain_urlopen():
    """every request goes through firmware.urlopen (https only, redirects too – catalog's guard)"""
    src = (ROOT / "fm1companion" / "firmware.py").read_text("utf-8")
    assert "urllib.request.urlopen" not in src
    from fm1companion import catalog
    h = catalog._HttpsOnlyRedirect()
    with pytest.raises(urllib.error.URLError):
        h.redirect_request(urllib.request.Request("https://example.org/a"), None, 302, "Found", {},
                           "http://example.org/a")


# ------------------------------------------------------------ SEC-02: nothing lands outside the firmware folder

@pytest.mark.parametrize("key,tag,name", [
    ("../../.local/share/applications", "fest", "evil.fwsc"),
    ("xfw", "../..", "evil.fwsc"),
    ("xfw", "v1.0", "../evil.fwsc"),
    ("xfw", "v1.0", "evil.desktop"),
    ("xfw", "v1/0", "evil.fwsc"),
    ("..", "v1.0", "evil.fwsc"),
    ("", "v1.0", "evil.fwsc"),
])
def test_download_refuses_unsafe_path_parts(fw, env, key, tag, name):
    fw.served["answer"] = lambda url: FakeResponse(b"[Desktop Entry]\nExec=evil\n", 26)
    home = env
    root = fw.load_settings().firmware_dir       # (the empty firmware folder itself may be created)
    before = sorted(str(p) for p in home.rglob("*"))
    # a catalog entry with a fixed address and "matching" checksum (the attacker controls both)
    import hashlib
    sha = hashlib.sha256(b"[Desktop Entry]\nExec=evil\n").hexdigest()
    fixed = fw.FirmwareSpec(key, "Evil", "", name, sums="", builtin=False, download_url="https://example.org/" + name,
                            sha256=sha, release_tag=tag)
    rel = fw.Release(tag=tag, prerelease=False, assets={name: "https://example.org/" + name})
    with pytest.raises(fw.DownloadError, match="Refused"):
        fw.download(fixed, rel, log=lambda *_: None)
    # the same through a release download (no fixed address)
    plain = fw.FirmwareSpec(key, "Evil", "someone/evil", name, sums="", builtin=False)
    with pytest.raises(fw.DownloadError, match="Refused"):
        fw.download(plain, rel, log=lambda *_: None)
    assert fw.calls == [] and sorted(str(p) for p in home.rglob("*")) == before and not list(root.iterdir())


def test_normal_names_still_download(fw, env):
    from fm1companion.flash import package as pk
    raw = pk.build_package("FM-1_950", "felucca", seed=5)
    fw.served["answer"] = lambda url: FakeResponse(raw, len(raw))
    for key, tag, name in (("gr-mm", "v1.0.0-rc.1", "gr!mm".replace("!", "-") + "-1.0.0-rc.1.fwsc"),
                           ("choralroot-fm-1", "0.39", "ChoralRoot_FM-1+v0.39.fwsc"), ("stock-v15", "V15", "FM-1_015.fwsc")):
        sp = fw.FirmwareSpec(key, "N", "someone/n", name, sums="", builtin=False)
        rel = fw.Release(tag=tag, prerelease=False, assets={name: "https://example.org/" + name})
        assert fw.download(sp, rel, log=lambda *_: None).fwsc == fw.load_settings().firmware_dir / key / tag / name
    # keys are validated, not rewritten
    assert fw._safe_component("JIANT-fm", "key") == "JIANT-fm"


# ------------------------------------------------------------ SEC-10 / F-17: no repo code without the trust check

def test_identity_helpers_run_no_repo_code(env, monkeypatch):
    from fm1companion import firmware
    importlib.reload(firmware)
    for gone in ("any_installer", "_load_module", "query_identity"):
        assert not hasattr(firmware, gone), gone
    src = (ROOT / "fm1companion" / "firmware.py").read_text("utf-8")
    assert "exec_module" not in src and "importlib.util" not in src
    # a repo that would be picked up by the old code: its installer must never be imported or started
    repo = firmware.REPOS_DIR / "melodee"
    (repo / ".git").mkdir(parents=True)
    (repo / "tools").mkdir()
    marker = env / "ran"
    (repo / "tools" / "fm1_install.py").write_text(f"open({str(marker)!r}, 'w').write('x')\ndef product_of(raw):\n    return 'EVIL'\n")
    f = pkgfile(env, "FM-1_91070")
    assert firmware.package_identity(f) == "FM-1_91070" and not marker.exists()
    assert firmware.package_identity(env / "missing.fwsc") == ""


# ------------------------------------------------------------ F-12: factory voices only on a live original V15

def test_factory_voices_need_the_live_identity():
    from types import SimpleNamespace as NS
    from fm1companion.ui.pages.voices import VoicesPage
    prof = NS(identity="FM-1_015")

    def is_v15(**kw):
        base = dict(identity="", profile=prof, midi_connected=True, is_virtual=False, firmware_key="stock-v15",
                    connected=False)
        base.update(kw)
        return VoicesPage._is_v15(NS(ctx=NS(**base)))
    assert is_v15(identity="FM-1_015")
    assert not is_v15()                                     # only the profile and the recorded key say V15
    assert not is_v15(identity="FM-1_91060")
    assert not is_v15(identity="FM-1_015", connected=True)  # answers the editor protocol: not the original firmware
    assert not is_v15(identity="FM-1_015", is_virtual=True)
    assert not is_v15(identity="FM-1_015", midi_connected=False)
