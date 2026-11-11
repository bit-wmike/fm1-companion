# SPDX-License-Identifier: GPL-3.0-only
"""Demo mode (FM1C_DEMO=1): simulated FM-1 for installation and rescue, in-process.

The UI's demo devices (DEMO-1 Melodee, DEMO-2 SLOOP, DEMO-3 ORBIT generic) each have a
SimFM1 here for the update protocol. Their port names are those of the demo PortInfos (demo-in, demo2-in, demo3-in).
"""
from __future__ import annotations

import threading

from ..vendor import fm1_install as fi
from . import sim

_LOCK = threading.Lock()
_BACKEND: sim.SimBackend | None = None

DEMO_DEVICES = {
    # port name (PortInfo.in_name) -> (identity, runtime policy)
    "demo-in": ("FM-1_91060", "felucca"),
    "demo2-in": ("FM-1_900", "felucca"),
    "demo3-in": ("FM-1_900", "felucca"),
}
# simulated USB connection per demo device (like sysfs "1-1.2"): this is how the installation pins its target
DEMO_USB_PATH = {"demo-in": "demo-usb-1", "demo2-in": "demo-usb-2", "demo3-in": "demo-usb-3"}


class DemoDevice(sim.SimFM1):
    """a demo device whose app port name stays fixed to the demo port"""

    def __init__(self, port: str, identity: str, policy: str):
        super().__init__(identity=identity, policy=policy, name=port, reboot_s=0.3,
                         usb_path=DEMO_USB_PATH.get(port, ""))
        self.demo_port = port

    @property
    def port_name(self) -> str:
        if self.state == "loader":
            return self.demo_port + " Update"
        return self.demo_port

    def _restart(self, state, ident=None, delay=None):
        super()._restart(state, ident, delay)
        self.app_name = self.demo_port


def backend() -> sim.SimBackend:
    global _BACKEND
    with _LOCK:
        if _BACKEND is None:
            _BACKEND = sim.SimBackend([DemoDevice(p, i, pol) for p, (i, pol) in DEMO_DEVICES.items()])
            fi.DELAY.update(sim.fast_delays())
            fi.DELAY.update({"wait_loader": 10.0, "wait_reboot": 10.0})
            from . import robust
            robust.CAREFUL.update(sim.fast_careful_delays())
            robust.CAREFUL.update({"wait_loader": 12.0, "wait_reboot": 12.0, "wait_resume": 12.0})
        return _BACKEND


def device(port: str) -> sim.SimFM1 | None:
    b = backend()
    return next((d for d in b.devices if getattr(d, "demo_port", "") == port), None)


def register(raw: bytes, product: str | None = None):
    for d in backend().devices:
        d.register(raw, product)


def reset():
    global _BACKEND
    with _LOCK:
        _BACKEND = None
