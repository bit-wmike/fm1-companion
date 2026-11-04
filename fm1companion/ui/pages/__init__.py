# SPDX-License-Identifier: GPL-3.0-only
"""Suite pages that register themselves.

Each file in this package can provide one or more pages by defining
``PAGE = PageSpec(...)`` or ``PAGES = [PageSpec(...), ...]``. The main window
finds them by itself at startup and sorts them into the navigation groups.
A page that fails to load is skipped (and reported in Help › Diagnostics)
– it must never take the app down with it.

Conventions for pages (QWidget):
  * constructor: ``factory(ctx, window)`` – ctx is the DeviceContext, window the main window.
  * optional ``activate()`` / ``deactivate()``: page becomes visible / is left.
  * optional ``open_with(**kwargs)``: jump from other pages with data
    (``window.go("simulator", url=...)``).
  * optional ``shutdown()``: on app exit (stop processes, tear down web pages).
  * optional signal ``navigate = pyqtSignal(str)``: open another page.
"""
from __future__ import annotations

import importlib
import pkgutil
import traceback
from dataclasses import dataclass, field
from typing import Callable
from ...i18n import _

# navigation groups in this order
# (the keys are internal identifiers and stay German; the titles are translated)
GROUPS: list[tuple[str, str]] = [
    ("geraet", _("Device")),
    ("klang", _("Sound")),
    ("musik", _("Music")),
    ("daten", _("Data & Tools")),
    ("entdecken", _("Discover")),           # (still valid; built-in pages use "daten")
    ("werkzeuge", _("Tools")),              # (still valid; built-in pages use "daten")
    ("hilfe", _("Help")),
]
GROUP_TITLES = dict(GROUPS)


@dataclass
class PageSpec:
    key: str                                   # unique, e.g. "simulator"
    title: str                                 # in the navigation, e.g. "Simulator"
    group: str                                 # key from GROUPS
    factory: Callable                          # factory(ctx, window) -> QWidget
    icons: tuple[str, ...] = ()                # icon names (desktop theme), first available wins
    order: int = 50                            # order within the group (small = top)
    description: str = ""                      # one sentence – for command search and tooltip
    keywords: tuple[str, ...] = ()             # additional search terms for command search
    needs_device: bool = False                 # informational only (hint in the navigation)
    extra: dict = field(default_factory=dict)


load_errors: list[tuple[str, str]] = []        # (module, traceback) – for Help › Diagnostics


def discover() -> list[PageSpec]:
    """all pages from this package (order: group, order, title)."""
    specs: list[PageSpec] = []
    load_errors.clear()
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda i: i.name):
        if info.name.startswith("_"):
            continue
        name = f"{__name__}.{info.name}"
        try:
            mod = importlib.import_module(name)
        except Exception:                      # noqa: BLE001 – skip broken page
            load_errors.append((name, traceback.format_exc()))
            continue
        found = getattr(mod, "PAGES", None) or ([mod.PAGE] if hasattr(mod, "PAGE") else [])
        for s in found:
            if isinstance(s, PageSpec):
                specs.append(s)
    return specs


def sort_specs(specs: list[PageSpec]) -> list[PageSpec]:
    order = {k: i for i, (k, _t) in enumerate(GROUPS)}
    return sorted(specs, key=lambda s: (order.get(s.group, len(order)), s.order, s.title.lower()))
