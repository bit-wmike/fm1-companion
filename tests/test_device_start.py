# SPDX-License-Identifier: GPL-3.0-only
"""Detect the device while the device process is still starting (demo/simulation on a busy computer).

Previously the device check gave up after ~5 s and the FM-1 stayed "MIDI only" for good (ui_p4_review_test
"reconnected" was flaky on Python 3.11). Now it waits as long as the process lives and has not said anything yet."""
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SLOW = ("import time, runpy, sys; time.sleep({d}); sys.argv = ['demo']; "
        "runpy.run_module('fm1companion.demo_device', run_name='__main__')")


def _probe(argv):
    from fm1companion.transport import Link, SubprocessTransport
    from fm1companion.ui.device import DeviceContext
    link = Link(SubprocessTransport(argv))
    try:
        ctx = SimpleNamespace(link=link, STARTUP_GRACE=DeviceContext.STARTUP_GRACE)
        t0 = time.monotonic()
        info = DeviceContext._probe(ctx, None)
        return info, ctx._hs_identity, time.monotonic() - t0
    finally:
        link.close()


def test_slow_starting_device_is_still_recognised():
    info, ident, secs = _probe([sys.executable, "-c", SLOW.format(d=7)])
    assert info.version.startswith("Melodee") and ident == "FM-1_91060" and secs >= 6.5


def test_dead_device_process_fails_fast():
    from fm1companion.transport import LinkError
    t0 = time.monotonic()
    try:
        _probe([sys.executable, "-c", "import sys; sys.exit(0)"])
        raised = False
    except LinkError:
        raised = True
    assert raised and time.monotonic() - t0 < 6


def test_late_probe_of_previous_device_is_ignored():
    """device switch: the result of a still running probe job of the previous device must not overwrite the new
    device (before: DEMO-1 showed "sloop" and FM-1_900 after switching from DEMO-2)."""
    from fm1companion.protocol import Info
    from fm1companion.ui.device import DeviceContext
    old, new = SimpleNamespace(hs_identity="FM-1_900"), SimpleNamespace(hs_identity="FM-1_91060")
    calls = []
    ctx = SimpleNamespace(link=new, info="neu", identity="FM-1_91060", _assign_profile=lambda: calls.append(1))
    DeviceContext._probed(ctx, Info.__new__(Info), old)
    DeviceContext._probe_failed(ctx, "x", old)
    DeviceContext._generic(ctx, "FM-1_900", old)
    assert ctx.info == "neu" and ctx.identity == "FM-1_91060" and not calls
