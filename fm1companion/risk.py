# SPDX-License-Identifier: GPL-3.0-only
"""Der einmalige Risiko-Hinweis vor der ersten Firmware-Installation bzw. Rettungs-/ROM-Aktion.

Derselbe Text steht im grafischen Installer und in install.sh (ohne Qt nutzbar). Die Zustimmung wird in den
Einstellungen gespeichert (``Settings.risk_ack``); erhöht sich ``RISK_VERSION`` (Text wesentlich geändert),
fragt die App erneut. Die Bestätigungen der einzelnen Aktionen bleiben davon unberührt.
"""
from __future__ import annotations

RISK_VERSION = 1
TITLE = "Bevor du dein FM-1 veränderst"
TEXT_LINES = [
    "Die FM-1 Companion Suite ist ein inoffizielles Community-Projekt und nicht mit M-VAVE verbunden.",
    "Firmware installieren, Rettung (Update-Modus, ROM-Bootmodus), Reste aufräumen und das Zurückschreiben "
    "aus dem Studio verändern dein Gerät. Das geschieht auf eigenes Risiko – im schlimmsten Fall startet "
    "das FM-1 nicht mehr und muss mit den Rettungswerkzeugen wiederbelebt werden.",
    "Mach vorher ein Backup (die App bietet es vor jeder Installation an) und trenne das Gerät während eines "
    "Vorgangs nicht.",
    "Nicht jede Funktion ist auf echter Hardware getestet, und schon gar nicht auf allen Geräten, "
    "Flash-Chips und Firmware-Versionen. Was getestet ist und was nicht, steht in docs/UNTESTED.md.",
    "Es gibt keine Gewährleistung und keine Haftung, soweit gesetzlich zulässig "
    "(GNU GPL 3.0, Abschnitte 15 und 16).",
]
CHECK_TEXT = "Ich habe verstanden"


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
