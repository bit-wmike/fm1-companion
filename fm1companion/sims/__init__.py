# SPDX-License-Identifier: GPL-3.0-only
"""Mitgelieferte FM-1-Simulatoren (WebAssembly-Builds, siehe tools/build_sims.py) und Online-Simulatoren.

Jeder Ordner ``sims/<name>/`` enthält Seite, WASM, Lizenz, NOTICE und ``manifest.json``. Ausgeliefert
werden sie vom lokalen Server der App (editor_host.LocalServer, ``/sims/<name>/``) – in der App und
für „In Firefox öffnen“.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from ..i18n import _

DIR = Path(__file__).resolve().parent


@dataclass
class Sim:
    key: str                      # "fm1-open", "melodee" oder "url:<adresse>"
    title: str
    version: str = ""
    kind: str = "emulator"        # "emulator" | "workbench" | "editor"
    url: str = ""                 # nur online
    online: bool = False
    license: str = ""
    source_repo: str = ""
    commit: str = ""
    ref: str = ""
    toolchain: str = ""
    build_date: str = ""
    size: int = 0
    description: str = ""
    manifest: dict = field(default_factory=dict)

    @property
    def entry(self) -> str:
        return self.manifest.get("entry", "index.html")


# Display texts (English source). They are translated here at run time; because tools/i18n_extract.py does not
# scan sims/, ui/pages/simulator.py lists them again with N_() (SIM_TEXTS) – keep both in sync.
# display titles (the manifest.json files of the bundled builds carry a German title)
TITLES = {"fm1-open": "M-VAVE FM1 Open (built from the official source code)"}
DESCRIPTIONS = {
    "fm1-open": "Built from M-VAVE's official source code (not checked by M-VAVE): FM1 Open V14 with 8 tracks, "
                "DX7, 808, 303, PSG, sampler – playing, editing, projects, sounds and samples. User interface in "
                "English. The firmware update part is removed (installing works via “Firmware+” in the app).",
    "melodee": "Melodee 1.6.1 (stable) – user interface and sound of the firmware; nothing is saved. First press "
               "“Switch on” (or simply play a key on the FM-1).",
}

# Online-Simulatoren aus der Firmware-Liste (awesome-fm-1). Laufen nur mit Internet.
ONLINE = [
    ("fm1-emu – real firmware (slower)", "https://joakimeriksson.github.io/fm1-emu/",
     "CPU emulator by Joakim Eriksson (MIT): runs real .fwsc firmwares (Stock, Skiff, ORBIT …). Much slower than "
     "the WASM simulators – on the Pi 5 ORBIT is well playable, Skiff 2 just about, Stock V15 hardly. Best use "
     "“Open in Firefox”."),
    ("FM-1 Workbench (Community)", "https://fm1-workbench.web.app",
     "Community workbench by thegiantsnail (GPL-3.0) with a software FM-1."),
    ("Felucca (try)", "https://hugelton.github.io/Felucca/webapp/try/", "Felucca – the base of most firmwares"),
    ("ORBIT", "https://dspaudio.github.io/orbit-web-emu/", "SLOOP-based groovebox with event tape"),
    ("X0X", "https://charlesvestal.github.io/fm1-x0x/emu/", "909, 808, two 303s"),
    ("FoMni", "https://charlesvestal.github.io/fm1-fomni/", "Omnichord-like chord harp"),
    ("ChoralRoot FM-1", "https://quixotic7.github.io/ChoralRootFM1/emu/", "Chord instrument à la Orchid"),
    ("MONK-1", "https://charlesvestal.github.io/fm1-monk/emu/", "Formant vocal synth"),
]


def bundled() -> list[Sim]:
    out = []
    for d in sorted(DIR.iterdir()):
        mf = d / "manifest.json"
        if not d.is_dir() or d.name.startswith("_") or not mf.exists():
            continue
        try:
            m = json.loads(mf.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        title = _(TITLES[m["name"]]) if m["name"] in TITLES else m.get("title", m["name"])
        out.append(Sim(key=m["name"], title=title, version=m.get("version", ""),
                       kind=m.get("kind", "emulator"), license=m.get("license", ""),
                       source_repo=m.get("source_repo", ""), commit=m.get("commit", ""), ref=m.get("ref", ""),
                       toolchain=m.get("toolchain", ""), build_date=m.get("build_date", ""),
                       size=sum(f.get("size", 0) for f in m.get("files", {}).values()),
                       description=_(DESCRIPTIONS[m["name"]]) if m["name"] in DESCRIPTIONS else "", manifest=m))
    return out


def online(url: str, title: str = "", kind: str = "emulator", description: str = "") -> Sim:
    return Sim(key="url:" + url, title=title or url, kind=kind, url=url, online=True, description=description)


# nur als Verweis (läuft nicht sinnvoll eingebettet / auf dem Pi): öffnet im normalen Browser
LINKS = [
    ("RemoteFM1 (link only)", "https://github.com/Quixotic7/RemoteFM1",
     "Quixotic7, GPL-3.0: the real FM-1 as a USB control panel for an emulated firmware. Ran badly on the Pi – "
     "therefore only as a reference; opens the project page in the browser."),
]


def online_list() -> list[Sim]:
    return [online(u, _(t), description=_(d)) for t, u, d in ONLINE] + \
        [online(u, _(t), kind="link", description=_(d)) for t, u, d in LINKS]


def verify(sim: Sim) -> list[str]:
    """Dateien gegen das Manifest prüfen -> Liste der Probleme (leer = in Ordnung)."""
    problems = []
    for name, info in sim.manifest.get("files", {}).items():
        p = DIR / sim.key / name
        if not p.exists():
            problems.append(_("{name} missing").format(name=name))
        elif p.stat().st_size != info.get("size") or hashlib.sha256(p.read_bytes()).hexdigest() != info.get("sha256"):
            problems.append(_("{name} damaged").format(name=name))
    return problems


# ---------------------------------------------------------------- MIDI-Sicherheit
FM1_OPEN_LIBRARY = (0xF0, 0x7D, 0x46, 0x4D)     # Projekt-/Sound-Bibliothek der FM1-Open-Firmware


FELUCCA_FAMILY = (0xF0, 0x7D)                   # Felucca/Melodee/SLOOP-Editoren (Hersteller-ID 7D = nicht-kommerziell)
# feste Längen der Kanal- und System-Nachrichten (Statusbyte -> Gesamtlänge)
_FIXED_LEN = {0xC0: 2, 0xD0: 2, 0xF1: 2, 0xF2: 3, 0xF3: 2, 0xF6: 1, 0xF8: 1, 0xF9: 1, 0xFA: 1, 0xFB: 1, 0xFC: 1,
              0xFD: 1, 0xFE: 1, 0xFF: 1}


def _one_message(data: list[int]) -> bool:
    """genau EINE wohlgeformte MIDI-Nachricht – keine Datenbytes ohne Status, keine zweite Nachricht
    (sonst ließe sich hinter einer Note ein Update-SysEx verstecken), kein Running Status."""
    st = data[0]
    if st < 0x80 or st == 0xF7:
        return False
    if st == 0xF0:
        return len(data) >= 2 and data[-1] == 0xF7 and all(b < 0x80 for b in data[1:-1])
    want = _FIXED_LEN.get(st if st >= 0xF0 else st & 0xF0, 3)
    return len(data) == want and all(b < 0x80 for b in data[1:])


def sim_out_allowed(data) -> bool:
    """Was eine Simulator-/Online-Seite an echte Ports schicken darf: normale MIDI-Nachrichten und das
    Bibliotheks-Protokoll von FM1 Open. Jede andere SysEx wird verworfen – insbesondere der Update-Schlüssel
    ``F0 22 24 35 7F`` (Update-Modus), der Soft-Key ``F0 22 24 35 7D`` (ROM-Bootmodus), die Kennungsabfrage
    ``F0 00 32 45`` und der 7-Bit-gepackte Datenstrom des M-VAVE-Updates (beginnt mit ``F0 00 …``)."""
    data = [int(x) & 0xFF for x in data]
    if not data or not _one_message(data):
        return False
    if data[0] != 0xF0:
        return True
    return tuple(data[:4]) == FM1_OPEN_LIBRARY


def editor_out_allowed(data) -> bool:
    """Online-Editoren (Katalog-Link „Online-Editor“): zusätzlich die SysEx der Felucca-Familie (``F0 7D …``:
    Melodee/SLOOP/Skiff-Editorprotokoll und Preset-Dateien) und Universal-SysEx (``F0 7E/7F``, Identity).
    Weiterhin gesperrt: alles, was ein Update oder den Bootmodus auslösen kann."""
    data = [int(x) & 0xFF for x in data]
    if sim_out_allowed(data):
        return True
    return bool(data) and _one_message(data) and data[0] == 0xF0 and len(data) >= 3 and data[1] in (0x7D, 0x7E, 0x7F)


def out_filter_for(kind: str):
    """Sicherheitsfilter je Art der Seite: Editoren dürfen mit dem Gerät reden, Simulatoren nur Noten/Takt."""
    return editor_out_allowed if kind == "editor" else sim_out_allowed


def origin_of(url) -> str:
    """'https://example.org:8443/x?y' -> 'https://example.org:8443' (Kleinbuchstaben, Standard-Port weg)."""
    from urllib.parse import urlsplit
    try:
        u = urlsplit(str(url).strip())
        host = (u.hostname or "").lower()
        port = u.port
    except ValueError:
        return ""
    scheme = (u.scheme or "").lower()
    if not scheme or not host:
        return ""
    if port is None or (scheme, port) in (("http", 80), ("https", 443)):
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


SIM_CONFIG = "simulator"
TRUST_KEY = "device_origins"            # Herkünfte, die nach Rückfrage Editor-SysEx ans FM-1 senden dürfen


def origin_trusted(origin: str) -> bool:
    from .. import config
    return bool(origin) and origin in (config.module(SIM_CONFIG, {}).get(TRUST_KEY) or [])


def trust_origin(origin: str) -> None:
    """einmalige Zustimmung des Nutzers für diese Herkunft speichern."""
    from .. import config
    if not origin:
        return
    cfg = config.module(SIM_CONFIG, {})
    lst = [o for o in (cfg.get(TRUST_KEY) or []) if isinstance(o, str)]
    if origin not in lst:
        cfg[TRUST_KEY] = lst + [origin]
        config.save()


def forget_origin(origin: str) -> None:
    from .. import config
    cfg = config.module(SIM_CONFIG, {})
    cfg[TRUST_KEY] = [o for o in (cfg.get(TRUST_KEY) or []) if o != origin]
    config.save()


class OutGate:
    """Was eine geladene Seite ans echte FM-1 schicken darf – bei JEDER Nachricht neu entschieden.

    * Simulatoren/Emulatoren und alle nicht bestätigten Herkünfte: nur ``sim_out_allowed`` (Noten, Controller,
      Takt, FM1-Open-Bibliothek).
    * Online-Editor, dessen Herkunft der Nutzer einmal bestätigt hat (``trust_origin``): ``editor_out_allowed``
      (Felucca-/Universal-SysEx). Update- und Bootmodus-SysEx bleiben in jedem Fall gesperrt.
    * Hat die Seite inzwischen eine andere Herkunft (Navigation, Weiterleitung), gilt wieder der sichere Filter.
    """

    def __init__(self, kind: str = "", start_url: str = "", trusted=None):
        self.kind = kind or ""
        self.origin = origin_of(start_url)
        self._trusted = trusted if trusted is not None else origin_trusted

    def editor_access(self, current_url) -> bool:
        cur = origin_of(current_url)
        return self.kind == "editor" and bool(cur) and cur == self.origin and bool(self._trusted(cur))

    def allowed(self, data, current_url) -> bool:
        if self.editor_access(current_url):
            return editor_out_allowed(data)
        return sim_out_allowed(data)


def page_midi(data) -> bool:
    """Was aus FM-1/Keyboard in den Simulator geht: Kanal-Nachrichten und Takt/Start/Stop."""
    return bool(data) and (0x80 <= data[0] <= 0xEF or data[0] in (0xF8, 0xFA, 0xFB, 0xFC))


# ---------------------------------------------------------------- Firefox
def firefox_command(url: str) -> list[str] | None:
    for exe in ("firefox", "firefox-esr"):
        p = shutil.which(exe)
        if p:
            return [p, "--new-window", url]
    p = shutil.which("xdg-open")
    return [p, url] if p else None


# ---------------------------------------------------------------- weitere MIDI-Eingänge (ALSA über mido)
class SystemInputs:
    """öffnet MIDI-Eingänge des Systems (USB-Keyboard …) und ruft callback(name, bytes) aus dem MIDI-Thread."""

    def __init__(self, callback):
        self.callback = callback
        self.open: dict[str, object] = {}

    @staticmethod
    def available(exclude: tuple[str, ...] = ()) -> list[str]:
        try:
            import mido
            names = mido.get_input_names()
        except Exception:                      # mido/rtmidi fehlt oder kein ALSA
            return []
        skip = [e.lower() for e in exclude if e]
        return [n for n in dict.fromkeys(names) if not any(s and s in n.lower() for s in skip)
                and "midi through" not in n.lower() and "rtmidi" not in n.lower()]

    def set(self, names: list[str]) -> list[str]:
        """genau diese Eingänge offen halten -> Fehlermeldungen"""
        errors = []
        for n in list(self.open):
            if n not in names:
                self._close(n)
        for n in names:
            if n in self.open:
                continue
            try:
                import mido
                self.open[n] = mido.open_input(n, callback=lambda m, n=n: self._cb(n, m))
            except Exception as e:             # noqa: BLE001
                errors.append(f"{n}: {e}")
        return errors

    def _cb(self, name, msg):
        try:
            self.callback(name, list(msg.bytes()))
        except Exception:
            pass

    def _close(self, n):
        p = self.open.pop(n, None)
        try:
            p.close()
        except Exception:
            pass

    def close(self):
        for n in list(self.open):
            self._close(n)
