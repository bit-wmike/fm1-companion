# SPDX-License-Identifier: GPL-3.0-only
"""The one-time risk notice before the first firmware installation or rescue/ROM action.

The same text appears in the graphical installer and in install.sh (usable without Qt). Consent is stored in the
settings (``Settings.risk_ack``); if ``RISK_VERSION`` is raised (text changed substantially),
the app asks again. The confirmations of the individual actions are not affected by this.
"""
from __future__ import annotations

from .i18n import _

RISK_VERSION = 1
TITLE = _("Before you change your FM-1")
TEXT_LINES = [
    _("FM-1 Companion Suite is an unofficial community project and not affiliated with M-VAVE."),
    _("Installing firmware, recovery (update mode, ROM boot mode), cleaning up leftovers and writing back "
      "from the Studio change your device. You do this at your own risk – in the worst case the FM-1 no "
      "longer starts and has to be revived with the recovery tools."),
    _("Make a backup first (the app offers one before every installation) and do not disconnect the device "
      "during an operation."),
    _("Not every function has been tested on real hardware, let alone on all devices, flash chips and "
      "firmware versions. What is tested and what is not is listed in docs/UNTESTED.md."),
    _("There is no warranty and no liability, to the extent permitted by law "
      "(GNU GPL 3.0, sections 15 and 16)."),
]
CHECK_TEXT = _("I understand")


def text_plain() -> str:
    return "\n\n".join(TEXT_LINES)


def acknowledged() -> bool:
    from . import config
    try:
        return int(config.load().risk_ack or 0) >= RISK_VERSION
    except (TypeError, ValueError):
        return False


def acknowledge() -> None:
    from . import config
    s = config.load()
    s.risk_ack = RISK_VERSION
    config.save(s)


__all__ = ["RISK_VERSION", "TITLE", "TEXT_LINES", "CHECK_TEXT", "text_plain", "acknowledged", "acknowledge"]
