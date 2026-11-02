# SPDX-License-Identifier: GPL-3.0-only
"""Seiten der Suite, die sich selbst anmelden.

Jede Datei in diesem Paket kann eine oder mehrere Seiten bereitstellen, indem sie
``PAGE = PageSpec(...)`` oder ``PAGES = [PageSpec(...), ...]`` definiert. Das Hauptfenster
findet sie beim Start von selbst und sortiert sie in die Gruppen der Navigation ein.
Eine Seite, die beim Laden scheitert, wird übersprungen (und in Hilfe › Diagnose
gemeldet) – sie darf die App nie mitreißen.

Konventionen für Seiten (QWidget):
  * Konstruktor: ``factory(ctx, window)`` – ctx ist der DeviceContext, window das Hauptfenster.
  * optional ``activate()`` / ``deactivate()``: Seite wird sichtbar / verlassen.
  * optional ``open_with(**kwargs)``: Sprung von anderen Seiten mit Daten
    (``window.go("simulator", url=...)``).
  * optional ``shutdown()``: beim Beenden der App (Prozesse stoppen, Web-Seiten abbauen).
  * optional Signal ``navigate = pyqtSignal(str)``: andere Seite öffnen.
"""
from __future__ import annotations

import importlib
import pkgutil
import traceback
from dataclasses import dataclass, field
from typing import Callable

# Gruppen der Navigation in dieser Reihenfolge
GROUPS: list[tuple[str, str]] = [
    ("geraet", "Gerät"),
    ("klang", "Klang"),
    ("musik", "Musik"),
    ("daten", "Daten & Werkzeuge"),
    ("entdecken", "Entdecken"),             # (weiter gültig; eingebaute Seiten nutzen "daten")
    ("werkzeuge", "Werkzeuge"),             # (weiter gültig; eingebaute Seiten nutzen "daten")
    ("hilfe", "Hilfe"),
]
GROUP_TITLES = dict(GROUPS)


@dataclass
class PageSpec:
    key: str                                   # eindeutig, z. B. "simulator"
    title: str                                 # in der Navigation, z. B. "Simulator"
    group: str                                 # Schlüssel aus GROUPS
    factory: Callable                          # factory(ctx, window) -> QWidget
    icons: tuple[str, ...] = ()                # Symbolnamen (Desktop-Thema), erstes vorhandenes gewinnt
    order: int = 50                            # Reihenfolge innerhalb der Gruppe (klein = oben)
    description: str = ""                      # ein Satz – für Befehlssuche und Tooltip
    keywords: tuple[str, ...] = ()             # zusätzliche Suchbegriffe für die Befehlssuche
    needs_device: bool = False                 # nur informativ (Hinweis in der Navigation)
    extra: dict = field(default_factory=dict)


load_errors: list[tuple[str, str]] = []        # (Modul, Traceback) – für Hilfe › Diagnose


def discover() -> list[PageSpec]:
    """alle Seiten aus diesem Paket (Reihenfolge: Gruppe, order, Titel)."""
    specs: list[PageSpec] = []
    load_errors.clear()
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda i: i.name):
        if info.name.startswith("_"):
            continue
        name = f"{__name__}.{info.name}"
        try:
            mod = importlib.import_module(name)
        except Exception:                      # noqa: BLE001 – kaputte Seite überspringen
            load_errors.append((name, traceback.format_exc()))
            continue
        found = getattr(mod, "PAGES", None) or ([mod.PAGE] if hasattr(mod, "PAGE") else [])
        for s in found:
            if isinstance(s, PageSpec):
                specs.append(s)
    return specs


def sort_specs(specs: list[PageSpec]) -> list[PageSpec]:
    order = {k: i for i, (k, _) in enumerate(GROUPS)}
    return sorted(specs, key=lambda s: (order.get(s.group, len(order)), s.order, s.title.lower()))
