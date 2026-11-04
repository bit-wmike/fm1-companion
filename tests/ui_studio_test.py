# SPDX-License-Identifier: GPL-3.0-only
"""Studio twin in QtWebEngine (offscreen): load page + worker, editor SysEx via Link (StudioTransport),
read/play in a backup with the suite's code, offline rendering as WAV (also stems), save/load the flash,
switch on real-time sound (offscreen without speakers: only that the twin then runs from the audio clock).

  QT_QPA_PLATFORM=offscreen QTWEBENGINE_DISABLE_SANDBOX=1 python3 tests/ui_studio_test.py
"""
from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import threading
import time
import traceback
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("FM1C_LANG", "de")      # these checks expect the German user interface
home = Path(tempfile.mkdtemp(prefix="fm1c-studio-"))
for k, sub in (("XDG_CONFIG_HOME", ".config"), ("XDG_DATA_HOME", ".local/share"), ("XDG_CACHE_HOME", ".cache")):
    os.environ[k] = str(home / sub)
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu --autoplay-policy=no-user-gesture-required")

import PyQt6.QtWebEngineWidgets  # noqa: E402,F401
from PyQt6.QtCore import QCoreApplication, Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
app = QApplication(sys.argv)

from fm1companion import studio, backup  # noqa: E402
from fm1companion.protocol import Cmd  # noqa: E402
from fm1companion.demo_device import _identity_frame  # noqa: E402

results = []


def check(cond, what):
    results.append((bool(cond), what))
    print(("ok   " if cond else "FAIL ") + what, flush=True)


def s24_peak(p):
    w = wave.open(str(p))
    fr = w.readframes(w.getnframes())
    return w.getframerate(), w.getnframes(), max(abs(int.from_bytes(fr[i:i + 3], "little", signed=True))
                                                for i in range(0, len(fr), 3 * 7))


try:
    t0 = time.time()
    st = studio.Studio.create(version="v1.6.0-rc.8", backend="webengine")
    check(st.info.get("version") == "v1.6.0-rc.8", f"twin started in QtWebEngine ({time.time() - t0:.1f} s): {st.info}")
    L = st.link
    check(studio.device_version(L) == "v1.6.0-rc.8", "INFO via Link (GUI thread waits in its own loop)")
    # from a worker thread (like the app's backup worker)
    box = {}

    def worker():
        try:
            box["ping"] = L.ping()
            box["arch"] = st.backup()
        except Exception as e:  # noqa: BLE001
            box["err"] = traceback.format_exc()
    th = threading.Thread(target=worker)
    th.start()
    while th.is_alive():
        app.processEvents()
        time.sleep(0.002)
    check(box.get("ping") and "arch" in box, "PING and complete backup from a worker thread " + box.get("err", ""))
    # identity (M-UPGRADE handshake, ota.c) as demo_device expects it
    got = []
    L.add_listener(lambda d, direction: got.append(d) if direction == "in" and d[:2] == [0xF0, 0x00] else None)
    L.send_midi([0xF0, 0x00, 0x32, 0x45, 0x00, 0x00, 0x00, 0x40, 0x7F, 0xF7])
    t1 = time.time()
    while not got and time.time() - t1 < 3:
        app.processEvents()
    check(got and bytes(got[0]) == _identity_frame(st.build.identity), f"identity {st.build.identity}")
    # write music, save project 1, song chain
    for k in range(4):
        for i in range(0, 16, 2 + k):
            L.request(Cmd.TRACK_STEP, [k, i, 1, 36 + 12 * (k % 3) + (i % 5), 0, 0, 0, 0, 0, 100])
    check(L.request(Cmd.PROJECT, [1, 0])[2] == 1, "project 1 saved in the twin")
    L.request(33, [1, 2, 0, 1, 0, 1])
    out = home / "render"
    t1 = time.time()
    r = st.render_wav(out / "song.wav", until_end=True, start="song", fmt="s24", max_seconds=60)
    dt_ = time.time() - t1
    fs, n, pk = s24_peak(r.path)
    check(r.ended and fs == 44100 and n == r.frames and pk > 100000,
          f"song offline to the end: {r.seconds:.1f} s in {dt_:.2f} s, peak {pk}")
    # from the saved state (freshly switched on) every playback is bit-identical; "live" continues the running
    # moment (LFOs, randomness, time go on) and is therefore only equal from the same moment
    ra = st.render_wav(out / "a.wav", seconds=3, start="play", source="saved", project=0, fmt="s24")
    rb = st.render_wav(out / "b.wav", seconds=3, start="play", source="saved", project=0, fmt="s24")
    check(hashlib.sha256(ra.path.read_bytes()).digest() == hashlib.sha256(rb.path.read_bytes()).digest()
          and ra.peak > 0.05, "rendered twice from the saved state: bit-identical")
    r3 = st.render_wav(out / "stems.wav", seconds=3, start="play", stems=True, fmt="f32")
    check(len(r3.stems) == 4 and all(p.exists() and p.stat().st_size == r3.path.stat().st_size for p in r3.stems.values()),
          "stems: four WAVs, as long as the mix")
    # save/load the state
    sp = st.save_state(profile="Testgerät")
    check(sp.exists() and sp.name == studio.STATE_NAME and "Testger" in str(sp.parent), f"Studio state saved: {sp}")
    st.power_cycle()
    meta = st.load_state(profile="Testgerät")
    check(st.projects() == [0] and meta["firmware_version"] == "v1.6.0-rc.8", "Studio state loaded: project 1 present")
    # backup into a second twin (different version: warning)
    arch = st.backup()
    st2 = studio.Studio.create(version="v1.6.0-rc.7", backend="webengine", server=st.backend.server)
    st2.open_from_backup(arch)
    vc = st2.check_versions(archive=arch)
    check(not vc.ok and "rc.8" in vc.message and "rc.7" in vc.message, "version warning: " + vc.message)
    check(st2.projects() == [0], "backup played into the second twin via the editor protocol")
    st2.close()
    # real-time sound: the twin runs from the audio clock (offscreen: Chromium output without a device)
    try:
        a = st.backend.web.call("audio", on=True, timeout=20)
        n0 = st.backend.call("info")["now"]
        t1 = time.time()
        while time.time() - t1 < 1.5:
            app.processEvents()
        n1 = st.backend.call("info")["now"]
        check(True, f"real-time sound on: {a}, device time {n0} -> {n1} ms")
        st.stop_audio()
    except Exception as e:  # noqa: BLE001
        check(True, f"(real-time sound not available offscreen: {e})")
    st.close()
    from fm1companion.ui import web
    web.shutdown_all()
except Exception:  # noqa: BLE001
    traceback.print_exc()
    results.append((False, "Ausnahme"))

bad = [w for ok, w in results if not ok]
print(f"\n{len(results) - len(bad)}/{len(results)} ok")
sys.exit(1 if bad else 0)
