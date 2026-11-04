# SPDX-License-Identifier: GPL-3.0-only
"""System check: evaluation of realistic kernel logs (Raspberry Pi 5) – without Qt."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fm1companion import syscheck as sc  # noqa: E402

FX = ROOT / "tests" / "fixtures" / "syscheck"


def keys(found):
    return {f.key.split(":")[0]: f.level for f in found}


def test_healthy():
    f = sc.analyze_log((FX / "pi5_healthy_dmesg.txt").read_text())
    assert keys(f) == {"fm1-usb": "ok"}
    assert "1-1" in f[0].title and "1209:0001" in f[0].title


def test_reconnect_loop():
    f = sc.analyze_log((FX / "pi5_fm1_reconnect_loop.txt").read_text())
    k = keys(f)
    assert k["fm1-loop"] == "error"
    assert k["usb-enum"] == "error"           # -71 at the FM-1's port
    loop = next(x for x in f if x.key.startswith("fm1-loop"))
    assert "3-1.2" in loop.title and "4 Trennungen" in loop.detail and loop.evidence


def test_undervoltage_overcurrent_xhci():
    k = keys(sc.analyze_log((FX / "pi5_undervoltage_usb.txt").read_text()))
    assert k["undervoltage-log"] == "error" and k["usb-overcurrent"] == "error" and k["xhci-dead"] == "error"
    assert k["fm1-disc"] == "info"


def test_snd_usb_audio_and_xruns():
    k = keys(sc.analyze_log((FX / "pi5_snd_usb_audio.txt").read_text(),
                            user_journal=(FX / "pipewire_user_journal.txt").read_text()))
    assert k["snd-freq"] == "warn" and k["snd-urb"] == "warn" and k["xrun"] == "warn"


def test_journalctl_format():
    lines = sc.parse_log((FX / "journal_k_short_monotonic.txt").read_text())
    assert lines[0].ts == 0.0 and lines[0].msg.startswith("Booting")
    k = keys(sc.analyze_log((FX / "journal_k_short_monotonic.txt").read_text()))
    assert k["undervoltage-log"] == "error" and k["fm1-disc"] == "info"


def test_known_fm1_path_without_ids():
    text = "[ 5.0] usb 2-1: new full-speed USB device number 3 using xhci-hcd\n" \
           "[ 9.0] usb 2-1: USB disconnect, device number 3\n"
    assert keys(sc.analyze_log(text, fm1_paths={"2-1"}))["fm1-disc"] == "info"
    assert sc.analyze_log(text) == []


def test_throttled_bits():
    assert keys(sc.throttled_findings(0x50005)) == {"uv-now": "error", "temp-now": "warn"}
    assert keys(sc.throttled_findings(0x50000)) == {"uv-past": "warn", "temp-past": "info"}
    assert keys(sc.throttled_findings(0)) == {"power-ok": "ok"}
    assert sc.throttled_findings(None) == []


def test_pi5_power(tmp_path):
    (tmp_path / "max_current").write_bytes((3000).to_bytes(4, "big"))
    (tmp_path / "usb_max_current_enable").write_bytes((0).to_bytes(4, "big"))
    f = sc.pi5_power_findings(tmp_path)
    assert [x.key for x in f] == ["pi5-usb-budget"] and "3,0 A" in f[0].title.replace(".", ",")
    (tmp_path / "max_current").write_bytes((5000).to_bytes(4, "big"))
    assert sc.pi5_power_findings(tmp_path) == []
    assert sc.pi5_power_findings(tmp_path / "nope") == []


def test_alsa_cards():
    txt = " 0 [vc4hdmi0       ]: vc4-hdmi - vc4-hdmi-0\n 2 [FM1            ]: USB-Audio - Melodee FM-1\n" \
          "                      Ellic Studio Melodee FM-1 at usb-xhci-hcd.0-1, full speed\n"
    assert sc.fm1_alsa_cards(txt) == ["2 [FM1            ]: USB-Audio - Melodee FM-1"]


def test_unreadable_log(monkeypatch):
    monkeypatch.setattr(sc, "read_kernel_log", lambda: ("", "", "dmesg: read kernel buffer failed: Operation not permitted"))
    monkeypatch.setattr(sc, "get_throttled", lambda: None)
    f, meta = sc.run_all(None)
    la = next(x for x in f if x.key == "log-access")
    assert la.level == "unknown" and "usermod -aG adm" in la.fix
    assert "Systemcheck" in sc.report(f, meta)


def test_xhci_dead_during_fm1_audio_start():
    """Pi 5 (report 2.2.0): the controller dies, at the same time the FM-1's audio start fails → targeted advice."""
    f = {x.key: x for x in sc.analyze_log((FX / "pi5_xhci_dead_on_fm1_audio.txt").read_text())}
    x = f["xhci-dead"]
    assert x.level == "error" and "Audio-Start des FM-1" in x.title
    assert "rpi-eeprom-update" in x.fix and "Profil „Aus“" in x.fix and "unbind" in x.fix
    assert "full-upgrade" not in x.fix                 # check first, do not update blindly
    # without FM-1 relation: the general message
    plain = sc.analyze_log("[ 10.0] xhci-hcd xhci-hcd.1: HC died; cleaning up\n")
    assert next(y for y in plain if y.key == "xhci-dead").title == "USB-Controller ist abgestürzt"


def test_playback_dummy_output_warns():
    from fm1companion import audioout
    o = audioout.Output("auto_null", "Schein-Ausgabe", True)
    f = sc.playback_finding(["pw-play"], o)
    assert o.is_dummy and f.level == "warn" and "kein echter Audio-Ausgang" in f.title
    assert "ins Leere" in audioout.output_hint(o)
    assert sc.playback_finding(["pw-play"], audioout.Output("alsa_output.hdmi", "HDMI", True)).level == "ok"


def test_fluidsynth_service_blocking_hdmi(tmp_path):
    """Pi 5 (report after 2.2.2): Debian's fluidsynth.service held /dev/snd/pcmC0D0p → no HDMI output."""
    import os
    from fm1companion import audiofix as AF
    proc = tmp_path / "proc"
    for pid, comm, target in ((898, "fluidsynth", "/dev/snd/pcmC0D0p"), (1201, "pipewire", "/dev/snd/pcmC1D0p"),
                              (3257, "strawberry", "/dev/null")):
        d = proc / str(pid) / "fd"
        d.mkdir(parents=True)
        (proc / str(pid) / "comm").write_text(comm + "\n")
        os.symlink(target, d / "5")
    hs = AF.playback_holders(proc)
    assert [(h.pid, h.name, h.card) for h in hs] == [(898, "fluidsynth", 0)]       # PipeWire itself does not count
    f = {x.key: x for x in sc.blocked_findings(hs, (True, True))}
    assert f["fluidsynth-service"].level == "error" and "PID 898" in f["fluidsynth-service"].detail
    assert "disable --now fluidsynth" in f["fluidsynth-service"].fix
    assert sc.blocked_findings([], (False, False)) == []
    other = sc.blocked_findings([AF.Holder(77, "someplayer", 0, 0)], (False, False))
    assert [x.key for x in other] == ["snd-blocked"] and "someplayer" in other[0].detail
