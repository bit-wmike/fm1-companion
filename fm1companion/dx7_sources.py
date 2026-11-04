# SPDX-License-Identifier: GPL-3.0-only
"""Freie DX7-Sammlungen von GitHub laden (auf einen Commit festgelegt, über raw.githubusercontent.com –
keine GitHub-API nötig). Ablage: ~/FM-1/DX7/Sammlungen/<Sammlung>/. Jede Sammlung nennt Quelle und Lizenz;
bei unklarer Lizenz entscheidet der Nutzer."""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .i18n import _

UNCLEAR = _("Licence unclear – check before private use only")


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
    folder: str = ""           # Ordnername auf der Platte (bleibt in jeder Sprache gleich)

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
    Collection("fm1-factory", _("FM-1 factory presets (128 voices)"), "KingParamount/fm1-factory-presets",
               "04b34cabd69745cf3a549af588619108e2866c0d",
               [f"banks/FM-1_factory_bank{i}.syx" for i in range(1, 5)], "CC0 1.0",
               _("The DX7 voices of the original M-VAVE FM-1 firmware, from M-VAVE's recovery tool."),
               tags=["FM-1", "Werk"], folder="FM-1 Werks-Presets"),  # i18n: ignore
    Collection("mene311", _("mene311 FM-1 banks (26 × 32)"), "mene311/fm1-banks",
               "f47b11b9035be0dfe7f899aec90798dccbcde9ee",
               [f"banks/FM-1_{n}.syx" for n in _MENE], "MIT",
               _("Themed banks for the FM-1: bass, pads, organ, chiptune, effects …"), tags=["FM-1"],
               folder="mene311 FM-1 Banks"),
    Collection("yamaha-rom", _("Yamaha DX7 factory ROMs (ROM1A–ROM4B)"), _BSP[0], _BSP[1],
               [f"public/dx7-banks/factory/rom{n}.syx" for n in ("1a", "1b", "2a", "2b", "3a", "3b", "4a", "4b")],
               UNCLEAR, _("The original Yamaha cartridges, mirrored in FM1 Editor & Librarian (code: MIT; "
               "the voices themselves come from Yamaha)."), clear=False, tags=["Yamaha", "Werk"],
               folder="Yamaha DX7 Werks-ROMs"),  # i18n: ignore
    Collection("greymatter", _("Grey Matter E! (selection)"), _BSP[0], _BSP[1],
               [f"public/dx7-banks/greymatter/{n}.syx" for n in (2, 5, 7)], UNCLEAR,
               _("Banks of the E! expansion by Grey Matter Response, mirrored in FM1 Editor & Librarian."),
               clear=False, tags=["E!"], folder="Grey Matter E!"),
]


def by_key(key: str) -> Collection | None:
    return next((c for c in COLLECTIONS if c.key == key), None)


def _fetch(url: str, timeout: float = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "fm1-companion"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def folder_of(root: Path, c: Collection) -> Path:
    return Path(root) / (c.folder or c.title.split(" (")[0])


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
            raise RuntimeError(_("cancelled"))
        target = d / Path(path).name
        if not target.exists():
            data = fetch(c.raw_url(path))
            res = dx7.parse_syx(data)
            if not res.voices:
                raise RuntimeError(_("{name}: no DX7 data").format(name=Path(path).name))
            tmp = target.with_suffix(".part")
            tmp.write_bytes(data)
            tmp.replace(target)
            if log:
                log(f"{Path(path).name}: {len(res.voices)} Voices")
        if progress:
            progress(i + 1, len(c.files))
    (d / "QUELLE.txt").write_text(
        _("{title}\nSource: {url}\nLicence: {licence}\n\n{description}\n").format(
            title=c.title, url=c.url, licence=c.licence, description=c.description), "utf-8")
    (d / ".sammlung.json").write_text(json.dumps({"key": c.key, "commit": c.commit, "licence": c.licence}), "utf-8")
    return d


def net_error_text(msg: str) -> str:
    """Netzfehler (urllib/OSError-Text) in einen verständlichen Satz übersetzen."""
    m = str(msg or "")
    low = m.lower()
    if "name or service not known" in low or "nodename nor servname" in low or "temporary failure in name" in low \
            or "getaddrinfo" in low or "no address associated" in low:
        return _("No internet connection (GitHub not reachable).")
    if "timed out" in low or "timeout" in low:
        return _("Timeout – the connection to GitHub is too slow or was interrupted.")
    if "404" in low:
        return _("The file no longer exists on GitHub (404) – please update the app.")
    if "403" in low or "429" in low:
        return _("GitHub is refusing right now (too many requests) – try again in a few minutes.")
    if "certificate" in low or "ssl" in low:
        return _("Secure connection not possible (certificate) – are the computer's date and time correct?")
    if "network is unreachable" in low or "connection refused" in low or "unreachable" in low:
        return _("No network – check Wi-Fi/cable.")
    return m + "."
