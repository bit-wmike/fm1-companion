# SPDX-License-Identifier: GPL-3.0-only
"""Web-MIDI-Brücke zwischen eingebetteten Web-Seiten (QtWebEngine) und MIDI-Ports.

Die JS-Seite (web/webmidi_polyfill.js) sieht ein normales MIDIAccess-Objekt.

* Editor (Standard): genau ein Ein- und Ausgang – das FM-1, das in der App gerade aktiv ist
  (``attach(link, label)``). Bei App-Operationen (Backup, Update …) meldet die Brücke den Port kurz
  als getrennt; der Editor verbindet sich danach von selbst neu.
* Simulator & Co.: zusätzlich beliebig viele weitere Ports (``set_extra_ports``), z. B. „FM-1 (Tasten)“
  und ein USB-Keyboard. Eingehendes schiebt die App mit ``inject(port_id, bytes)`` hinein (aus jedem
  Thread), Ausgehendes geht an ``extra_send(port_id, bytes)``. ``out_filter(bytes) -> bool`` entscheidet
  für JEDE ausgehende Nachricht (auch an das FM-1), ob sie raus darf – Simulator-Seiten bekommen so z. B.
  nie Firmware-Update-SysEx durch.
"""
from __future__ import annotations

import json
from typing import Callable

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot


class MidiBridge(QObject):
    midiIn = pyqtSignal(str, list)        # Port-ID, Bytes
    portsChanged = pyqtSignal(str)        # JSON-Liste der Ports
    blockedChanged = pyqtSignal(int)      # Anzahl der vom out_filter verworfenen Nachrichten (für einen Hinweis)
    _incoming = pyqtSignal(list)          # intern: aus dem MIDI-Thread in den GUI-Thread
    _injected = pyqtSignal(str, list)     # intern: inject() aus beliebigem Thread

    def __init__(self, parent=None):
        super().__init__(parent)
        self._link = None
        self._label = "FM-1"
        self._paused = False
        self.traffic = 0
        self.blocked = 0                  # vom out_filter verworfene Nachrichten
        self._extra: list[dict] = []
        self.extra_send: Callable[[str, list[int]], None] | None = None
        self.out_filter: Callable[[list[int]], bool] | None = None
        self._incoming.connect(self._deliver)
        self._injected.connect(self._deliver_extra)

    # ---------------- Python-Seite ----------------
    def attach(self, link, label: str):
        if self._link is not None:
            self._link.remove_passthrough(self._forward)
        self._link = link
        self._label = label or "FM-1"
        if link is not None:
            link.add_passthrough(self._forward)
        self._publish()

    def _forward(self, data):
        """aus dem MIDI-Thread: in den GUI-Thread weiterreichen."""
        self._incoming.emit(list(data))

    def detach(self):
        self.attach(None, "")

    def set_paused(self, paused: bool):
        if self._paused != paused:
            self._paused = paused
            self._publish()

    def set_extra_ports(self, ports: list[dict]):
        """weitere Ports: [{"id": "...", "name": "...", "type": "input"|"output"}, ...]"""
        self._extra = [{"id": p["id"], "name": p["name"], "type": p["type"], "state": "connected",
                        "manufacturer": p.get("manufacturer", "")} for p in ports]
        self._publish()

    def extra_ports(self) -> list[dict]:
        return list(self._extra)

    def inject(self, port_id: str, data):
        """Nachricht in einen zusätzlichen Eingang der Seite schieben (thread-sicher)."""
        self._injected.emit(port_id, [int(x) & 0xFF for x in data])

    @property
    def connected(self) -> bool:
        return self._link is not None and not self._paused

    def _ports(self) -> list[dict]:
        out: list[dict] = []
        if self._link is not None or self._paused:
            state = "connected" if self.connected else "disconnected"
            name = self._label if "melodee" in self._label.lower() or "felucca" in self._label.lower() \
                or "fm-1" in self._label.lower() else f"{self._label} FM-1"
            out = [{"id": "fm1-in", "name": name, "type": "input", "state": state},
                   {"id": "fm1-out", "name": name, "type": "output", "state": state}]
        return out + [dict(p) for p in self._extra]

    def _publish(self):
        self.portsChanged.emit(json.dumps(self._ports()))

    def _deliver(self, data: list):
        if self.connected:
            self.midiIn.emit("fm1-in", data)

    def _deliver_extra(self, port_id: str, data: list):
        if any(p["id"] == port_id and p["type"] == "input" for p in self._extra):
            self.midiIn.emit(port_id, data)

    # ---------------- JS-Seite ----------------
    @pyqtSlot(result=str)
    def ports(self) -> str:
        return json.dumps(self._ports())

    @pyqtSlot(str, list)
    def send(self, port_id: str, data: list):
        data = [int(x) & 0xFF for x in data]
        if self.out_filter is not None and not self.out_filter(data):
            self.blocked += 1
            self.blockedChanged.emit(self.blocked)
            return
        if self.extra_send and any(p["id"] == port_id and p["type"] == "output" for p in self._extra):
            try:
                self.extra_send(port_id, data)
                self.traffic += 1
            except Exception:
                pass
            return
        if port_id == "fm1-out":
            if not self.connected:
                return
            try:
                if self._link.send_raw(data):
                    self.traffic += 1
            except Exception:
                pass
            return
