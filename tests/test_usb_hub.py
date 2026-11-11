# SPDX-License-Identifier: GPL-3.0-only
"""Installation through a USB hub: hub detection (sysfs), careful mode, automatic resume, flash log, udev rule.

  python3 -m pytest tests/test_usb_hub.py -q
Everything in simulation (flash/sim.py SimFaults) and in a fake sysfs – no real FM-1.
"""
from __future__ import annotations

import importlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PATH = "1-1.2"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    for k, sub in (("XDG_CONFIG_HOME", "c"), ("XDG_DATA_HOME", "d"), ("XDG_CACHE_HOME", "k")):
        monkeypatch.setenv(k, str(tmp_path / sub))
    monkeypatch.delenv("FM1C_DEMO", raising=False)
    from fm1companion import config, i18n
    i18n.set_language("en")                      # these tests read the English texts
    importlib.reload(config)
    from fm1companion.flash import engine, journal, robust, sim
    for m in (journal, engine):
        importlib.reload(m)
    from fm1companion.vendor import fm1_install as fi
    monkeypatch.setattr(fi, "DELAY", dict(fi.DELAY, **sim.fast_delays()))
    monkeypatch.setattr(robust, "CAREFUL", dict(robust.CAREFUL, **sim.fast_careful_delays()))
    yield tmp_path
    i18n.set_language(None)
    importlib.reload(engine)


def pkgfile(tmp, product, family="felucca", seed=1):
    from fm1companion.flash import package as pk
    p = Path(tmp) / f"{product}-{family}-{seed}.fwsc"
    p.write_bytes(pk.build_package(product, family, seed=seed))
    return p


HUB = [{"name": "1-1", "usb_id": "05e3:0610", "product": "USB2.0 Hub", "self_powered": True}]


def setup(faults=None, hubs=HUB, packages=(), others=(), **kw):
    from fm1companion.flash import sim
    dev = sim.SimFM1(identity=kw.pop("identity", "FM-1_91060"), usb_path=PATH, faults=faults, hubs=hubs, **kw)
    for raw in packages:
        dev.register(raw)
    return dev, sim.SimBackend([dev, *others])


def engine_for(be, **kw):
    from fm1companion.flash import engine
    logs = []
    e = engine.Engine(backend=be, port_filter=lambda n: "Through" not in n, log=logs.append, **kw)
    return e, logs


def pin():
    from fm1companion.flash.engine import Pin
    return Pin(usb_path=PATH)


# ------------------------------------------------------------ hub detection

def fake_sysfs(root: Path, devices: dict) -> Path:
    base = root / "bus" / "usb" / "devices"
    for name, attrs in devices.items():
        d = base / name
        (d / "power").mkdir(parents=True, exist_ok=True)
        for k, v in attrs.items():
            if k.startswith("power/"):
                (d / k).write_text(v + "\n")
            elif k == "peer":
                port = d / f"{name}:1.0" / f"{name}-port1"
                port.mkdir(parents=True)
                (port / "peer").write_text("")
            else:
                (d / k).write_text(v + "\n")
    return root


SYS = {
    "usb1": {"bDeviceClass": "09", "idVendor": "1d6b", "idProduct": "0002", "speed": "480", "version": " 2.00",
             "power/control": "auto"},
    "1-1": {"bDeviceClass": "09", "idVendor": "05e3", "idProduct": "0610", "product": "USB2.1 Hub", "speed": "480",
            "version": " 2.10", "bmAttributes": "e0", "power/control": "auto", "peer": ""},
    "1-1.2": {"bDeviceClass": "00", "idVendor": "1209", "idProduct": "0001", "product": "Melodee FM-1", "speed": "12",
              "version": " 2.00", "bmAttributes": "80", "bMaxPower": "100mA", "serial": "ABCDEF123456",
              "power/control": "on", "power/autosuspend_delay_ms": "2000"},
    "1-2": {"bDeviceClass": "00", "idVendor": "1209", "idProduct": "0002", "product": "Melodee FM-1 Update",
            "speed": "12", "power/control": "auto"},
}


def test_usb_link_detects_hub_and_power(tmp_path):
    from fm1companion import devices
    root = fake_sysfs(tmp_path, SYS)
    assert devices.usb_parents("1-1.2.4") == ["1-1.2", "1-1"] and devices.usb_parents("1-2") == []
    ln = devices.usb_link(PATH, str(root))
    assert ln.found and ln.behind_hub and ln.depth == 1
    hub = ln.hubs[0]
    assert hub.hub and hub.usb_id == "05e3:0610" and hub.speed == "480" and hub.self_powered is True and hub.usb3
    assert ln.device.speed == "12" and ln.device.usb_id == "1209:0001" and not ln.device.hub
    assert ln.root is not None and ln.root.root
    assert [n.name for n in ln.autosuspend_nodes] == ["1-1"]          # the root hub is not reported
    assert any("hub(s) in between" in x for x in ln.lines())
    direct = devices.usb_link("1-2", str(root))
    assert direct.found and not direct.behind_hub and [n.name for n in direct.autosuspend_nodes] == ["1-2"]
    assert not devices.usb_link("3-9", str(root)).found
    assert devices.usb_path_for_serial("ABCDEF123456", str(root)) == PATH
    assert devices.fm1_usb_paths(str(root)) == ["1-1.2", "1-2"]


def test_careful_mode_resolution(env):
    from fm1companion import devices
    from fm1companion.flash import robust
    hub = devices.UsbLink(PATH, devices.UsbNode("1-1.2"), [devices.UsbNode("1-1", hub=True)])
    direct = devices.UsbLink("1-2", devices.UsbNode("1-2"))
    assert robust.resolve_mode("auto", hub) and not robust.resolve_mode("auto", direct)
    assert robust.resolve_mode("always", direct) and not robust.resolve_mode("never", hub)
    assert not robust.resolve_mode("auto", None)
    d = robust.delays(True)
    assert d["gap"] > 0 and d["wait_loader"] >= robust.CAREFUL["wait_loader"]
    notes = robust.plan_notes(hub)
    assert notes and "careful mode" in notes[0].lower()
    assert robust.plan_notes(direct) == []


def test_syscheck_reports_hub_and_autosuspend(tmp_path):
    from fm1companion import syscheck
    root = fake_sysfs(tmp_path, SYS)
    found = syscheck.usb_link_findings({PATH}, sysroot=str(root))
    keys = {f.key: f for f in found}
    assert keys[f"usb-hub:{PATH}"].level == "warn" and "05e3:0610" in keys[f"usb-hub:{PATH}"].detail
    assert keys[f"usb-autosuspend:{PATH}"].level == "warn"
    found2 = syscheck.usb_link_findings({"1-2"}, sysroot=str(root))
    assert any(f.key == "usb-direct:1-2" and f.level == "ok" for f in found2)


# ------------------------------------------------------------ fault injection

def test_hub_plan_and_log_without_faults(env):
    from fm1companion.flash import package as pk
    f = pkgfile(env, "FM-1_91070")
    dev, be = setup(packages=[f.read_bytes()])
    e, logs = engine_for(be)
    r = e.write(pk.analyze(f), pin=pin())
    assert r.identity == "FM-1_91070" and dev.flash == pk.analyze(f).image
    assert any("USB hub" in m for m in logs) and any("Careful USB mode" in m for m in logs)
    log = e.flight.path.read_text()
    assert "careful mode: on" in log and "05e3:0610" in log and "outcome: done" in log
    assert e.journal.history()[-1]["flash_log"] == e.flight.path.name


def test_lost_responses_are_repeated_by_the_device(env):
    """lost responses in step 1 and step 2: the device asks again, the installer answers again"""
    from fm1companion.flash import package as pk
    f = pkgfile(env, "FM-1_91070")
    dev, be = setup(dict(drop_replies={2, 5, 40, 41}), packages=[f.read_bytes()])
    e, _ = engine_for(be)
    assert e.write(pk.analyze(f), pin=pin()).identity == "FM-1_91070"
    assert dev.flash == pk.analyze(f).image and e.flight.resends >= 4
    assert "RESEND" in e.flight.path.read_text()


def test_late_responses_and_slow_in_are_answered_once(env):
    """a response that arrives late: the device repeated its request in the meantime; the stale copy is not
    answered a second time (the device keeps only one frame)"""
    from fm1companion.flash import package as pk
    f = pkgfile(env, "FM-1_91070")
    dev, be = setup(dict(late_replies={3, 30}, late_s=0.2, resend_s=0.15, frame_s=0.05, in_delay=0.01),
                    packages=[f.read_bytes()])
    e, _ = engine_for(be)
    assert e.write(pk.analyze(f), pin=pin()).identity == "FM-1_91070"
    assert dev.flash == pk.analyze(f).image


def test_loader_drops_off_mid_write_and_is_resumed(env):
    from fm1companion.flash import package as pk
    f = pkgfile(env, "FM-1_91070")
    dev, be = setup(dict(glitches=[0.3, 0.7], glitch_s=0.4), packages=[f.read_bytes()])
    e, logs = engine_for(be)
    r = e.write(pk.analyze(f), pin=pin())
    assert r.identity == "FM-1_91070" and dev.flash == pk.analyze(f).image
    assert e.flight.resumes == 2 and e.flight.vanished >= 2 and e.journal.active is None
    assert sum("starting the write again" in m for m in logs) == 2
    txt = e.flight.path.read_text()
    assert "resume" in txt and "port-" in txt and "back" in txt


def test_reenumeration_under_the_same_port_name(env):
    """ALSA reuses the port name after the dropout: the USB device number shows the re-enumeration"""
    from fm1companion.flash import package as pk
    f = pkgfile(env, "FM-1_91070")
    dev, be = setup(dict(glitches=[0.5], glitch_s=0.02, same_name=True), packages=[f.read_bytes()])
    e, _ = engine_for(be)
    assert e.write(pk.analyze(f), pin=pin()).identity == "FM-1_91070"
    assert e.flight.resumes == 1 and dev.flash == pk.analyze(f).image


def test_silent_loader_is_restarted(env):
    """the loader gave up (3 × 5 s) but stays connected: careful mode starts the write again"""
    from fm1companion.flash import package as pk
    f = pkgfile(env, "FM-1_91070")
    dev, be = setup(dict(silent_at=[0.4]), packages=[f.read_bytes()])
    e, _ = engine_for(be)
    assert e.write(pk.analyze(f), pin=pin()).identity == "FM-1_91070"
    assert e.flight.resumes == 1 and dev.sessions == 3          # step 1, step 2, step 2 again


def test_step1_dropout_is_repeated_safely(env):
    from fm1companion.flash import package as pk
    f = pkgfile(env, "FM-1_91070")
    dev, be = setup(dict(step1_glitch_at=2, glitch_s=0.2), packages=[f.read_bytes()])
    e, logs = engine_for(be)
    assert e.write(pk.analyze(f), pin=pin()).identity == "FM-1_91070"
    assert e.flight.step1_retries == 1 and any("Trying step 1 again" in m for m in logs)


def test_slow_reenumeration_needs_careful_waits(env, monkeypatch):
    from fm1companion.flash import engine, package as pk
    from fm1companion.vendor import fm1_install as fi
    f = pkgfile(env, "FM-1_91070")
    monkeypatch.setitem(fi.DELAY, "wait_loader", 0.5)
    dev, be = setup(dict(loader_delay=1.2), packages=[f.read_bytes()])
    e, _ = engine_for(be, careful="never")
    with pytest.raises(engine.FlashError) as ei:
        e.write(pk.analyze(f), pin=pin())
    assert ei.value.code == "noloader" and ei.value.state == engine.PAUSED
    assert "USB hub" in str(ei.value) and "directly" in str(ei.value)          # plain-language cause + next step
    # careful mode: waits long enough (the journal resumes the same package)
    e2, _ = engine_for(be)
    t0 = __import__("time").time()
    while dev.state != "loader" and __import__("time").time() - t0 < 3:
        __import__("time").sleep(0.05)
    assert e2.write(pk.analyze(Path(e.journal.active["package"])), pin=pin()).identity == "FM-1_91070"


def test_without_careful_mode_a_dropout_still_pauses(env):
    """careful mode off (setting "never"): unchanged behaviour – the dropout is reported, the device waits"""
    from fm1companion.flash import engine, package as pk
    f = pkgfile(env, "FM-1_91070")
    dev, be = setup(dict(glitches=[0.5]), packages=[f.read_bytes()])
    e, _ = engine_for(be, careful="never")
    with pytest.raises(engine.FlashError) as ei:
        e.write(pk.analyze(f), pin=pin())
    assert ei.value.state == engine.PAUSED and e.flight.resumes == 0 and e.journal.active
    assert "flash log" in str(ei.value).lower()


def test_resume_gives_up_after_the_limit(env):
    from fm1companion.flash import engine, package as pk, robust
    f = pkgfile(env, "FM-1_91070")
    dev, be = setup(dict(glitches=[0.1, 0.2, 0.3, 0.4, 0.5], glitch_s=0.05), packages=[f.read_bytes()])
    e, _ = engine_for(be)
    with pytest.raises(engine.FlashError) as ei:
        e.write(pk.analyze(f), pin=pin())
    assert ei.value.state == engine.PAUSED and e.flight.resumes == robust.RESUME_TRIES


def test_resume_never_goes_to_a_foreign_loader(env):
    """after the dropout the device comes back with ANOTHER loader: nothing more is sent"""
    from fm1companion.flash import engine, package as pk
    f = pkgfile(env, "FM-1_91070")
    dev, be = setup(dict(glitches=[0.5], glitch_s=0.3), packages=[f.read_bytes()])
    orig = dev.glitch

    def swap(state=None, secs=None):
        orig(state, secs)
        dev.loader_id = "ota-FM-1_015"          # the original loader (another family)
        dev.loader_kind = "stock"
    dev.glitch = swap
    e, _ = engine_for(be)
    with pytest.raises(engine.FlashError) as ei:
        e.write(pk.analyze(f), pin=pin())
    assert ei.value.state == engine.PAUSED and not dev.bricked
    n_upgrades = sum(1 for s in dev.sends if s == bytes(engine.fi.UPGRADE))
    assert n_upgrades == 2                      # step 1 and the first step 2 – never to the foreign loader


def test_resume_only_on_the_pinned_device(env):
    """a second FM-1 in update mode with the same loader: never touched, also not by the resume"""
    from fm1companion.flash import package as pk, sim
    f = pkgfile(env, "FM-1_91070")
    other = sim.SimFM1(identity="FM-1_91060", name="Melodee FM-1", usb_path="1-1.3")
    other.state, other.loader_kind, other.loader_id = "loader", "felucca", "ota-FM-1_900"
    dev, be = setup(dict(glitches=[0.5], glitch_s=0.4), packages=[f.read_bytes()], others=[other])
    e, _ = engine_for(be)
    assert e.write(pk.analyze(f), pin=pin()).identity == "FM-1_91070"
    assert other.sends == [] and other.state == "loader" and e.flight.resumes == 1


def test_wrong_model_and_loader_still_refused_in_careful_mode(env):
    from fm1companion.flash import engine, package as pk
    f = pkgfile(env, "FM-1_91070")
    dev, be = setup(packages=[f.read_bytes()])
    dev.state, dev.loader_kind, dev.loader_id = "loader", "stock", "ota-FM-1_015"
    e, _ = engine_for(be, careful="always")
    with pytest.raises(engine.FlashError) as ei:
        e.write(pk.analyze(f), pin=pin())
    assert ei.value.code == "loader" and dev.sends.count(bytes(engine.fi.UPGRADE)) == 0
    dev2, be2 = setup(identity="FX-9_100", packages=[f.read_bytes()])
    e2, _ = engine_for(be2, careful="always")
    with pytest.raises(engine.FlashError) as ei2:
        e2.write(pk.analyze(f), pin=pin())
    assert ei2.value.code == "model" and bytes(engine.fi.UPGRADE) not in dev2.sends


def test_guard_refuses_unpinned_port(env):
    from fm1companion.flash import robust
    from fm1companion.flash import sim
    a = sim.SimFM1(usb_path=PATH)
    b = sim.SimFM1(usb_path="1-1.3", name="Felucca")
    be = sim.SimBackend([a, b])
    w = robust.WatchBackend(be, be.details, robust.FlightLog(), guard=lambda n: be.details(n).get("usb_path") == PATH)
    w.open("Melodee FM-1", "Melodee FM-1").close()
    with pytest.raises(OSError):
        w.open("Felucca", "Felucca")


def test_flash_log_is_private_and_in_the_report(env, monkeypatch):
    from fm1companion.flash import robust
    monkeypatch.setenv("HOME", str(env / "home" / "alice"))
    home = str(Path.home())
    fl = robust.FlightLog(env / "logs", kernel=lambda: (
        f"[{__import__('time').monotonic():.6f}] usb 1-1.2: SerialNumber: ABCDEF123456\n"
        f"[{__import__('time').monotonic():.6f}] usb 1-1-port2: over-current condition\n"
        f"[1.000000] unrelated early boot line usb\n", "dmesg", ""))
    fl.ev("note", f"package {home}/FM-1/x.fwsc")
    p = fl.finish("failed (lost)", "x")
    txt = p.read_text()
    assert home not in txt and "ABCDEF123456" not in txt and "…3456" in txt
    assert "over-current" in txt and "early boot" not in txt and "overcurrent" in fl.kernel_hints
    # error report ZIP contains the latest flash logs
    pytest.importorskip("PyQt6")
    from fm1companion.flash import journal
    logs = journal.root() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    shutil.copy(p, logs / p.name)
    from fm1companion.ui import crash
    import zipfile
    z = crash.build_report(env / "r.zip")
    names = zipfile.ZipFile(z).namelist()
    assert f"flash/{p.name}" in names


# ------------------------------------------------------------ udev rule

RULE = ROOT / "packaging" / "udev" / "60-fm1-companion.rules"


def test_udev_rule_covers_all_fm1_ids():
    from fm1companion import devices
    txt = RULE.read_text()
    rules = [ln for ln in txt.splitlines() if ln.strip() and not ln.startswith("#")]
    pairs = set()
    for ln in rules:
        v = re.search(r'ATTR\{idVendor\}=="([0-9a-f]{4})"', ln)
        p = re.search(r'ATTR\{idProduct\}=="([0-9a-f|]+)"', ln)
        if v and p:
            pairs |= {f"{v.group(1)}:{x}" for x in p.group(1).split("|")}
        assert 'ATTR{power/control}="on"' in ln
    assert pairs == set(devices.FM1_USB_IDS)


def _run_hub_script(rule_line: str, sysroot: Path, devpath: str):
    """execute the RUN+= part of the rule like udev would (%p, $$ and %% substitution) against a fake sysfs"""
    m = re.search(r'RUN\+="(.*)"', rule_line)
    cmd = m.group(1).replace("/sys%p", str(sysroot) + devpath).replace("$$", "$").replace("%%", "%")
    assert cmd.startswith("/bin/sh -c '")
    subprocess.run(["/bin/sh", "-c", cmd[len("/bin/sh -c '"):-1]], check=True, timeout=10)


@pytest.mark.skipif(not Path("/bin/sh").exists(), reason="no /bin/sh")
def test_udev_rule_wakes_the_hubs_on_the_path(tmp_path):
    line = next(ln for ln in RULE.read_text().splitlines() if "RUN+=" in ln and not ln.startswith("#"))
    dev = tmp_path / "devices/platform/xhci-hcd.0/usb1/1-1/1-1.2"
    for d, cls, ctl in ((dev.parent.parent, "09", "auto"), (dev.parent, "09", "auto"), (dev, "00", "auto")):
        (d / "power").mkdir(parents=True, exist_ok=True)
        (d / "bDeviceClass").write_text(cls + "\n")
        (d / "power" / "control").write_text(ctl + "\n")
    _run_hub_script(line, tmp_path, "/devices/platform/xhci-hcd.0/usb1/1-1/1-1.2")
    assert (dev.parent / "power/control").read_text().strip() == "on"           # the hub
    assert (dev.parent.parent / "power/control").read_text().strip() == "on"    # the root hub
    assert (dev / "power/control").read_text().strip() == "auto"                # the device itself: ATTR{} does it


def test_installer_offers_the_rule_only_on_request(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "packaging" / "installer"))
    import core
    importlib.reload(core)
    calls = []
    monkeypatch.setattr(core.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or
                        subprocess.CompletedProcess(cmd, 0, "", ""))
    assert core.udev_rule_source(ROOT) == RULE
    ok = core.install_udev_rule(RULE, log=lambda *_: None, sudo=["sudo"])
    assert ok and calls and calls[0][0] == "sudo" and "/etc/udev/rules.d/60-fm1-companion.rules" in " ".join(calls[0])
    sh = (ROOT / "install.sh").read_text()
    assert "--usb-power-rule" in sh and "--no-usb-power-rule" in sh
    gui = (ROOT / "packaging" / "installer" / "gui.py").read_text()
    assert "--usb-power-rule" in gui


def test_deb_ships_the_rule_inactive():
    txt = (ROOT / "tools" / "build_deb.py").read_text()
    assert "60-fm1-companion.rules" in txt and "usr/share/fm1-companion/udev" in txt
    assert "rules.d" not in txt.split("POSTINST")[1].split('"""')[1]       # never activated by the package itself


def test_queued_copies_are_answered_once(env):
    """requests that piled up while the computer was busy: [A, A, B] -> A once, then B (one frame on the device)"""
    import queue
    from fm1companion.flash import robust, sim
    from fm1companion.vendor import fm1_install as fi

    class Link:
        lost = False

        def __init__(self, pkts):
            self.q = queue.Queue()
            for p in pkts:
                self.q.put(p)
            self.sent = []

        def read(self, timeout):
            try:
                return self.q.get_nowait()
            except queue.Empty:
                return None

        def send(self, pkt):
            self.sent.append(sim.parse_reply(pkt)[0])
            return True

    image = bytes(range(256)) * 8
    a, b = sim.request_frame(0, 64), sim.request_frame(64, 64)
    link = Link([a, a, b, sim.request_frame(fi.FINISH_WRITE, 8)])
    up = robust.RobustUpdater(sim.SimBackend([]), careful=True, flight=robust.FlightLog())
    served, finished, lost = up.serve(link, image, fi.FINISH_WRITE, 1.0, lambda *a: None)
    assert finished and served == 2 and link.sent == [0, 64, fi.FINISH_WRITE] and up.flight.duplicates == 1
    # a repeat of an answered request with a newer one behind it (the device has moved on): skipped
    link2 = Link([a])
    link2.later = [a, b, sim.request_frame(fi.FINISH_WRITE, 8)]
    orig = link2.read

    def staged(timeout):
        p = orig(timeout)
        if p is None and link2.later:          # the next batch arrives while we wait
            for x in link2.later:
                link2.q.put(x)
            link2.later = []
        return p
    link2.read = staged
    up2 = robust.RobustUpdater(sim.SimBackend([]), flight=robust.FlightLog())
    served, finished, _lost = up2.serve(link2, image, fi.FINISH_WRITE, 1.0, lambda *a: None)
    assert finished and link2.sent == [0, 64, fi.FINISH_WRITE] and up2.flight.duplicates == 1


def test_plan_shows_the_hub_note(env):
    from fm1companion import devices, firmware
    from fm1companion.flash import robust
    from fm1companion.flash.planner import DeviceState, Target, plan_install
    link = devices.UsbLink(PATH, devices.UsbNode(PATH, power_control="on"),
                           [devices.UsbNode("1-1", hub=True, self_powered=False, power_control="auto")])
    notes = robust.plan_notes(link, "auto")
    st = DeviceState(identity="FM-1_91060", felucca=True, firmware_key="melodee", usb_notes=notes)
    p = plan_install(st, Target.from_spec(firmware.spec("melodee"), tag="v1.6.0"))
    w = " ".join(p.warnings)
    assert "careful mode" in w and "bus-powered" in w and "autosuspend" in w and "1-1" in w
    assert "switched off" in robust.plan_notes(link, "never")[0]


def test_lost_final_confirmation_is_recognised(env):
    """the answer to the loader's last request is lost, it restarts into the new firmware anyway: careful mode
    notices the new identity instead of waiting for a loader that does not come back"""
    from fm1companion.flash import package as pk
    f = pkgfile(env, "FM-1_91070")
    dev, be = setup(dict(drop_finish=True), packages=[f.read_bytes()])
    e, _ = engine_for(be)
    assert e.write(pk.analyze(f), pin=pin()).identity == "FM-1_91070" and e.journal.active is None
    assert "already runs FM-1_91070" in e.flight.path.read_text()
