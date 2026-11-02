# SPDX-License-Identifier: GPL-3.0-only
"""Gemerkte Wahl „Zwischenschritt über Original-V15“ – nur pro Wechsel (z. B. „melodee>sloop“), keine globale
Abschaltung (Projektvorgabe: Schutz vor Flüchtigkeitsfehlern bleibt der Standard)."""
from __future__ import annotations

from .. import config


def _d() -> dict:
    return config.module("installer", {"v15_choice": {}})


def choice_for(key: str) -> bool | None:
    v = _d().get("v15_choice", {}).get(key)
    return v if isinstance(v, bool) else None


def remember(key: str, via_v15: bool):
    d = _d()
    d.setdefault("v15_choice", {})[key] = bool(via_v15)
    config.save()
