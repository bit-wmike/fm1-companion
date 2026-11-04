# SPDX-License-Identifier: GPL-3.0-only
"""Remembered choice "intermediate step via stock V15" – only per switch (e.g. "melodee>sloop"), no global
opt-out (project rule: protection against careless mistakes stays the default)."""
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
