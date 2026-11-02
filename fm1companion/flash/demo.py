# SPDX-License-Identifier: GPL-3.0-only
"""Demo-Modus (FM1C_DEMO=1): simulierte FM-1 für Installation und Rettung, im Prozess.

Die Demo-Geräte der Oberfläche (DEMO-1 Melodee, DEMO-2 SLOOP, DEMO-3 ORBIT generisch) haben hier je ein
SimFM1 für das Update-Protokoll. Ihre Port-Namen sind die der Demo-PortInfos (demo-in, demo2-in, demo3-in).
"""
from __future__ import annotations

import threading

from ..vendor import fm1_install as fi
from . import sim

_LOCK = threading.Lock()
_BACKEND: sim.SimBackend | None = None

DEMO_DEVICES = {
    # Port-Name (PortInfo.in_name) -> (Kennung, Laufzeit-Politik)
    "demo-in": ("FM-1_91060", "felucca"),
    "demo2-in": ("FM-1_900", "felucca"),
    "demo3-in": ("FM-1_900", "felucca"),
}
# simulierter USB-Anschluss je Demo-Gerät (wie sysfs "1-1.2"): so hält die Installation das Ziel fest
DEMO_USB_PATH = {"demo-in": "demo-usb-1", "demo2-in": "demo-usb-2", "demo3-in": "demo-usb-3"}


class DemoDevice(sim.SimFM1):
    """ein Demo-Gerät, dessen App-Port-Name fest der Demo-Port bleibt"""

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
