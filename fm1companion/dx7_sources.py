# SPDX-License-Identifier: GPL-3.0-only
"""Freie DX7-Sammlungen von GitHub laden (auf einen Commit festgelegt, über raw.githubusercontent.com –
keine GitHub-API nötig). Ablage: ~/FM-1/DX7/Sammlungen/<Sammlung>/. Jede Sammlung nennt Quelle und Lizenz;
bei unklarer Lizenz entscheidet der Nutzer."""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

UNCLEAR = "Lizenz unklar – nur für privaten Gebrauch prüfen"


@dataclass
class Collection:
    key: str
    title: str
    repo: str                 # owner/name
    commit: str
    files: list[str]
    licence: str
    description: str = ""
    clear: bool = True        # Lizenz eindeutig frei?
    tags: list = field(default_factory=list)

    @property
    def url(self) -> str:
        return f"https://github.com/{self.repo}/tree/{self.commit}"

    def raw_url(self, path: str) -> str:
        return f"https://raw.githubusercontent.com/{self.repo}/{self.commit}/{path}"


_BSP = ("benny-sparra/fm1-dx7-patch-importer", "fa736548bcdd10a5e0385bf4d1917e991850c53a")
_MENE = ["acid_techno", "ambient_texture", "bass_funk_slap", "bells_marimba", "brass_section", "chiptune",
         "church_organ", "clav_funk", "drums_perc", "electronic_bass", "guitar", "industrial", "lead_solo", "misc_fx",
         "orchestral_hits", "organ", "percussion_tribal", "piano_ep", "plucked_world", "scifi_horror",
         "strings_orchestral", "synthwave_bass", "synthwave_pad", "vibes_bells", "vocal_choir", "woodwinds_sax"]

COLLECTIONS = [
    Collection("fm1-factory", "FM-1 Werks-Presets (128 Voices)", "KingParamount/fm1-factory-presets",
               "04b34cabd69745cf3a549af588619108e2866c0d",
               [f"banks/FM-1_factory_bank{i}.syx" for i in range(1, 5)], "CC0 1.0",
               "Die DX7-Voices der Original-Firmware des M-VAVE FM-1, aus M-VAVEs Wiederherstellungs-Tool.",
               tags=["FM-1", "Werk"]),
    Collection("mene311", "mene311 FM-1 Banks (26 × 32)", "mene311/fm1-banks",
               "f47b11b9035be0dfe7f899aec90798dccbcde9ee",
               [f"banks/FM-1_{n}.syx" for n in _MENE], "MIT",
               "Thematische Bänke für das FM-1: Bass, Pads, Orgel, Chiptune, Effekte …", tags=["FM-1"]),
    Collection("yamaha-rom", "Yamaha DX7 Werks-ROMs (ROM1A–ROM4B)", _BSP[0], _BSP[1],
               [f"public/dx7-banks/factory/rom{n}.syx" for n in ("1a", "1b", "2a", "2b", "3a", "3b", "4a", "4b")],
               UNCLEAR, "Die originalen Yamaha-Cartridges, gespiegelt im FM1 Editor & Librarian (Code: MIT; "
               "die Voices selbst stammen von Yamaha).", clear=False, tags=["Yamaha", "Werk"]),
    Collection("greymatter", "Grey Matter E! (Auswahl)", _BSP[0], _BSP[1],
               [f"public/dx7-banks/greymatter/{n}.syx" for n in (2, 5, 7)], UNCLEAR,
               "Bänke der E!-Erweiterung von Grey Matter Response, gespiegelt im FM1 Editor & Librarian.",
               clear=False, tags=["E!"]),
]


def by_key(key: str) -> Collection | None:
    return next((c for c in COLLECTIONS if c.key == key), None)


def _fetch(url: str, timeout: float = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "fm1-companion"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def folder_of(root: Path, c: Collection) -> Path:
    return Path(root) / c.title.split(" (")[0]


def installed(root: Path, c: Collection) -> bool:
    d = folder_of(root, c)
    return all((d / Path(f).name).exists() for f in c.files)


def download(c: Collection, root: Path, fetch=None, log=None, progress=None, cancel=None) -> Path:
    """lädt alle Dateien (vorhandene werden übersprungen), prüft, ob es DX7-SysEx ist, und legt
    eine QUELLE.txt mit URL und Lizenz daneben."""
    from . import dx7
    fetch = fetch or _fetch
    d = folder_of(root, c)
    d.mkdir(parents=True, exist_ok=True)
    for i, path in enumerate(c.files):
        if cancel and cancel():
            raise RuntimeError("abgebrochen")
        target = d / Path(path).name
        if not target.exists():
            data = fetch(c.raw_url(path))
            res = dx7.parse_syx(data)
            if not res.voices:
                raise RuntimeError(f"{Path(path).name}: keine DX7-Daten")
            tmp = target.with_suffix(".part")
            tmp.write_bytes(data)
            tmp.replace(target)
            if log:
                log(f"{Path(path).name}: {len(res.voices)} Voices")
        if progress:
            progress(i + 1, len(c.files))
    (d / "QUELLE.txt").write_text(
        f"{c.title}\nQuelle: {c.url}\nLizenz: {c.licence}\n\n{c.description}\n", "utf-8")
    (d / ".sammlung.json").write_text(json.dumps({"key": c.key, "commit": c.commit, "licence": c.licence}), "utf-8")
    return d


def net_error_text(msg: str) -> str:
    """Netzfehler (urllib/OSError-Text) in einen deutschen Satz übersetzen."""
    m = str(msg or "")
    low = m.lower()
    if "name or service not known" in low or "nodename nor servname" in low or "temporary failure in name" in low \
            or "getaddrinfo" in low or "no address associated" in low:
        return "Keine Internetverbindung (GitHub nicht erreichbar)."
    if "timed out" in low or "timeout" in low:
        return "Zeitüberschreitung – die Verbindung zu GitHub ist zu langsam oder unterbrochen."
    if "404" in low:
        return "Die Datei gibt es auf GitHub nicht mehr (404) – bitte die App aktualisieren."
    if "403" in low or "429" in low:
        return "GitHub lehnt gerade ab (zu viele Anfragen) – in ein paar Minuten noch einmal."
    if "certificate" in low or "ssl" in low:
        return "Sichere Verbindung nicht möglich (Zertifikat) – stimmen Datum und Uhrzeit des Rechners?"
    if "network is unreachable" in low or "connection refused" in low or "unreachable" in low:
        return "Kein Netz – WLAN/Kabel prüfen."
    return m + "."
