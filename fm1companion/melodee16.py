# SPDX-License-Identifier: GPL-3.0-only
"""Melodee 1.6: was die Suite beim Wechsel auf, ab und zwischen den 1.6-Versionen wissen muss.

Quelle: keremimo/melodee v1.6.0 docs/RELEASE_1.6.0.md („Upgrading“, „Sound and levels“, „MIDI and USB“) und
firmware/src/res.c, firmware/loader/ldr_core.c (ldr_res). Kurz:

* Melodee 1.6 speichert Projekte und Presets in einem gemeinsamen Speicher-Pool und neuen Formaten (FBKM–FBKQ,
  kompakte Datensätze). Ältere Firmware kann das nicht lesen, überschreibt die Sektoren evtl. und lehnt Backups von
  1.6 ab. Das Backup VOR dem Update ist der Rückweg.
* Backups von 1.6 haben 57 Objekte (32 Projekte; IDs 64–91 = Projekte 5–32) – das hat keine ältere Firmware.
* 1.6.0 (fertig) mischt anders als die Vorabversionen: CZ-1 6 dB leiser, Limiter ab −2 dBFS, TPDF-Dither; die
  USB-Stereo-Aufnahme liegt vor MASTER, der Ton vom Rechner folgt USB VOLUME statt MASTER.
* Ressourcen (Schrift, Werkssounds, Bluetooth-Programm): ab 1.6.0-rc.8 schreibt der Update-Loader sie selbst aus dem
  Paket. Kommt man von ≤ 1.5.1, liegen dort noch Preset-Banken – dann lässt der Loader den Bereich in Ruhe, die neue
  Firmware zieht die Banken beim ersten Start um („Setup unfinished“), und erst danach lassen sich die Ressourcen
  schreiben (Editor-Befehl 82; der Firmware Hub braucht dafür eine zweite Installation).

Rein (keine Qt-, keine MIDI-Abhängigkeit).
"""
from __future__ import annotations

import re

_VER = re.compile(r"v?(\d+)\.(\d+)(?:\.(\d+))?(?:[- ]?(rc|beta|alpha)\.?(\d+))?", re.I)
LINE_16 = (1, 6, 0, 0, 0)          # jede 1.6-Version, auch die Vorabversionen
FINAL_160 = (1, 6, 0, 3, 0)        # die fertige 1.6.0 (neue Pegel)
OBJECTS_16 = 57                    # Backup-Inventar ab 1.6 (32 Projektplätze)


def version_key(s: str) -> tuple | None:
    """'MELODEE v1.6.0-rc.8' -> (1, 6, 0, 2, 8); fertige Version -> (1, 6, 0, 3, 0); keine Version -> None"""
    m = _VER.search(s or "")
    if not m:
        return None
    a, b, c, pre, n = m.groups()
    rank = {"alpha": 0, "beta": 1, "rc": 2}.get((pre or "").lower(), 3)
    return (int(a), int(b), int(c or 0), rank, int(n or 0))


def is_melodee(version: str = "", key: str = "") -> bool:
    return (key or "").lower() == "melodee" or "melodee" in (version or "").lower()


def is_16(version: str) -> bool:
    """Melodee-Version der 1.6-Reihe (oder neuer)?  '' / unbekannt -> False"""
    k = version_key(version)
    return k is not None and k >= LINE_16


def before_16(version: str) -> bool:
    """bekannte Melodee-Version VOR 1.6 (1.5.1 und älter)?  '' / unbekannt -> False"""
    k = version_key(version)
    return k is not None and k < LINE_16


def new_levels(old: str, new: str) -> bool:
    """liegt die Pegeländerung der fertigen 1.6.0 zwischen old und new (in dieser Richtung: alt -> neu)?"""
    a, b = version_key(old), version_key(new)
    return a is not None and b is not None and a < FINAL_160 <= b


# ------------------------------------------------------------------ Texte
UPGRADE_16 = ("<b>Melodee 1.6:</b> Bestehende Projekte werden geladen, aber was 1.6 speichert, kann ältere Firmware "
              "nicht mehr lesen – und ältere Firmware lehnt Backups von 1.6 ab. Das Backup, das die App jetzt vorher "
              "macht, ist dein Rückweg; darum ist es hier Pflicht.")
LEVELS_160 = ("<b>Melodee 1.6.0 klingt anders:</b> CZ-1-Spuren sind 6 dB leiser, der Limiter greift erst bei "
              "−2 dBFS (laute Mixe werden lauter), überall TPDF-Dither. Bestehende Projekte können darum anders "
              "klingen – Balance prüfen. Die USB-Aufnahme liegt jetzt vor MASTER (der Lautstärkeregler ändert den "
              "Aufnahmepegel nicht mehr); den Ton vom Rechner stellt „USB VOLUME“ im HOME-Menü ein.")
RESOURCES_FROM_15 = ("Von Melodee 1.5.1 oder älter: Der Update-Loader kann die Werkssounds beim ersten Mal nicht "
                     "schreiben, weil dort noch deine Preset-Banken liegen; die neue Firmware zieht sie beim Start "
                     "in den gemeinsamen Speicher um. Die App schreibt die Werkssounds danach selbst (Schritt "
                     "„Ressourcen schreiben“) – eine zweite Installation wie beim Firmware Hub ist nicht nötig. Zeigt "
                     "das FM-1 danach „Setup unfinished“: „Schrift & Werkssounds nachschreiben“ oder dieselbe Version "
                     "noch einmal installieren.")
BACKUP_REQUIRED_16 = ("Für den Wechsel auf Melodee 1.6 ist ein Backup Pflicht: Ältere Firmware kann die Projekte von "
                      "1.6 nicht lesen, das Backup von vorher ist der einzige Rückweg. Bitte „Vorher ein Backup "
                      "machen“ anhaken.")


def downgrade_text(target: str) -> str:
    return (f"<b>Zurück von Melodee 1.6:</b> {target} kann die Projekte und Presets, die Melodee 1.6 gespeichert hat, "
            "nicht lesen und überschreibt deren Speicher evtl. Auch Backups von Melodee 1.6 (auch das, das die App "
            "jetzt macht) lassen sich dort nicht zurückspielen – nur wieder auf Melodee 1.6 oder neuer. Für den "
            "Rückweg das Backup von vor dem Update auf 1.6 verwenden.")


DOWNGRADE_LOST = ("Projekte, Presets und Einstellungen von Melodee 1.6 sind mit der älteren Firmware nicht nutzbar "
                  "(Backups von 1.6 lassen sich nur auf Melodee 1.6 oder neuer zurückspielen).")


def restore_refusal(backup_version: str = "", device_version: str = "") -> str:
    b = f" ({backup_version})" if version_key(backup_version) else ""
    d = device_version or "eine Firmware ohne die 32 Projektplätze von 1.6 (z. B. Melodee 1.5.1 oder älter)"
    return (f"Dieses Backup stammt von Melodee 1.6{b}, auf dem FM-1 läuft {d}. Ältere Firmware kann die Projekte "
            "und Presets von 1.6 nicht lesen und lehnt solche Backups ab. Es wurde nichts geschrieben. "
            "Abhilfe: das FM-1 auf Melodee 1.6 oder neuer bringen (Firmware+) und dann zurückspielen – oder ein "
            "Backup von vor dem Update auf 1.6 nehmen.")


# ------------------------------------------------------------------ Backups
def backup_is_16(firmware: str = "", companion: dict | None = None, n_objects: int = 0,
                 ids=()) -> bool:
    """stammt ein Backup von Melodee 1.6 (oder neuer)? Inventar zählt vor dem Namen: 57 Objekte bzw. Projekt-IDs
    64–91 gibt es nur ab 1.6."""
    if n_objects == OBJECTS_16 or any(64 <= int(i) <= 91 for i in ids):
        return True
    c = companion or {}
    v = c.get("firmware_version") or c.get("version_string") or ""
    if is_melodee(firmware, c.get("firmware", "")):
        return is_16(v) or is_16(firmware)
    return False


def device_before_16(device_version: str = "", n_objects: int | None = None, key: str = "") -> bool:
    """läuft auf dem FM-1 eine Felucca-Firmware, die Backups von Melodee 1.6 NICHT lesen kann?
    Inventar (BACKUP_LIST) zählt vor dem Namen."""
    if n_objects is not None:
        return n_objects != OBJECTS_16
    return is_melodee(device_version, key) and before_16(device_version)


def usb_record_hint(version: str) -> str:
    """Hinweis zum Aufnahmepegel über USB (Seite „Aufnahme“, Samples › Aufnehmen) – je nach Melodee-Version."""
    if not is_melodee(version):
        return ""
    k = version_key(version)
    if k is not None and k >= FINAL_160:
        return ("Melodee ab 1.6.0: Die USB-Aufnahme liegt vor MASTER – der Lautstärkeregler am FM-1 ändert den "
                "Aufnahmepegel nicht. USB VOLUME (HOME-Menü) regelt nur den Ton vom Rechner zum FM-1.")
    if k is not None:
        return ("Bei dieser Melodee-Version folgt die Stereo-Aufnahme über USB dem MASTER-Regler: für einen guten "
                "Pegel MASTER aufdrehen (ab Melodee 1.6.0 liegt die Aufnahme vor MASTER).")
    return ""
