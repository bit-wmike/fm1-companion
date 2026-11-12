# SPDX-License-Identifier: GPL-3.0-only
"""Bundled FM-1 simulators (WebAssembly builds, see tools/build_sims.py) and online simulators.

Each folder ``sims/<name>/`` contains the page, WASM, licence, NOTICE and ``manifest.json``. They are
served by the app's local server (editor_host.LocalServer, ``/sims/<name>/``) – in the app and
for "Open in Firefox".
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
    key: str                      # "fm1-open", "melodee" or "url:<address>"
    title: str
    version: str = ""
    kind: str = "emulator"        # "emulator" | "workbench" | "editor"
    url: str = ""                 # online only
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

# Online simulators from the firmware list (awesome-fm-1). Only work with internet access.
ONLINE = [
    ("fm1-emu – real firmware (slower)", "https://joakimeriksson.github.io/fm1-emu/",
     "CPU emulator by Joakim Eriksson (MIT): runs real .fwsc firmwares (Stock, Skiff, ORBIT …). Much slower than "
     "the WASM simulators – on the Pi 5 ORBIT is well playable, Skiff 2 just about, Stock V15 hardly. Best use "
     "“Open in Firefox”."),
    ("FM-1 Workbench (Community)", "https://fm1-workbench.web.app",
     "Community workbench by thegiantsnail (GPL-3.0) with a software FM-1.", "workbench"),
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


# link only (does not work sensibly embedded / on the Pi): opens in the regular browser
LINKS = [
    ("RemoteFM1 (link only)", "https://github.com/Quixotic7/RemoteFM1",
     "Quixotic7, GPL-3.0: the real FM-1 as a USB control panel for an emulated firmware. Ran badly on the Pi – "
     "therefore only as a reference; opens the project page in the browser."),
]


def online_list() -> list[Sim]:
    return [online(e[1], _(e[0]), kind=e[3] if len(e) > 3 else "emulator", description=_(e[2])) for e in ONLINE] + \
        [online(u, _(t), kind="link", description=_(d)) for t, u, d in LINKS]


def verify(sim: Sim) -> list[str]:
    """Check files against the manifest -> list of problems (empty = OK)."""
    problems = []
    for name, info in sim.manifest.get("files", {}).items():
        p = DIR / sim.key / name
        if not p.exists():
            problems.append(_("{name} missing").format(name=name))
        elif p.stat().st_size != info.get("size") or hashlib.sha256(p.read_bytes()).hexdigest() != info.get("sha256"):
            problems.append(_("{name} damaged").format(name=name))
    return problems


# ---------------------------------------------------------------- MIDI safety
FM1_OPEN_LIBRARY = (0xF0, 0x7D, 0x46, 0x4D)     # project/sound library of the FM1 Open firmware


FELUCCA_FAMILY = (0xF0, 0x7D)                   # Felucca/Melodee/SLOOP editors (manufacturer ID 7D = non-commercial)
# fixed lengths of channel and system messages (status byte -> total length)
_FIXED_LEN = {0xC0: 2, 0xD0: 2, 0xF1: 2, 0xF2: 3, 0xF3: 2, 0xF6: 1, 0xF8: 1, 0xF9: 1, 0xFA: 1, 0xFB: 1, 0xFC: 1,
              0xFD: 1, 0xFE: 1, 0xFF: 1}


def _one_message(data: list[int]) -> bool:
    """exactly ONE well-formed MIDI message – no data bytes without status, no second message
    (otherwise an update SysEx could be hidden behind a note), no running status."""
    st = data[0]
    if st < 0x80 or st == 0xF7:
        return False
    if st == 0xF0:
        return len(data) >= 2 and data[-1] == 0xF7 and all(b < 0x80 for b in data[1:-1])
    want = _FIXED_LEN.get(st if st >= 0xF0 else st & 0xF0, 3)
    return len(data) == want and all(b < 0x80 for b in data[1:])


def sim_out_allowed(data) -> bool:
    """What EVERY page may send to real ports without any permission: single normal MIDI messages (notes,
    controllers, program change, clock/start/stop …). Every SysEx is dropped – in particular the update key
    ``F0 22 24 35 7F`` (update mode), the soft key ``F0 22 24 35 7D`` (ROM boot mode), the identity query
    ``F0 00 32 45``, the 7-bit-packed data stream of the M-VAVE update (starts with ``F0 00 …``) and the
    FM1 Open library protocol (it can erase samples and overwrite projects)."""
    data = [int(x) & 0xFF for x in data]
    if not data or not _one_message(data):
        return False
    return data[0] != 0xF0


def library_out_allowed(data) -> bool:
    """Bundled local simulators and confirmed origins: additionally the FM1 Open library protocol
    (``F0 7D 46 4D …``: read, write and delete projects, sounds and the sample bank of an FM-1 that runs FM1 Open)."""
    data = [int(x) & 0xFF for x in data]
    if sim_out_allowed(data):
        return True
    return bool(data) and _one_message(data) and tuple(data[:4]) == FM1_OPEN_LIBRARY


def editor_out_allowed(data) -> bool:
    """Online editors (catalog link "Online editor"): additionally the SysEx of the Felucca family (``F0 7D …``:
    Melodee/SLOOP/Skiff editor protocol and preset files, this includes the FM1 Open library) and universal
    SysEx (``F0 7E/7F``, Identity). Still blocked: anything that can trigger an update or boot mode."""
    data = [int(x) & 0xFF for x in data]
    if sim_out_allowed(data):
        return True
    return bool(data) and _one_message(data) and data[0] == 0xF0 and len(data) >= 3 and data[1] in (0x7D, 0x7E, 0x7F)


MVAVE_UPDATE_HEADER = (0x22, 0x24, 0x35)       # F0 22 24 35 7F = update mode, F0 22 24 35 7D = ROM boot mode


def builtin_editor_out_allowed(data) -> bool:
    """Editor view (the firmware's own web editor, served by the app): everything a web page can properly send
    with Web MIDI, except SysEx that starts with ``F0 22 24 35`` – the M-VAVE update-mode and boot-mode keys.
    None of the known editors sends them from the editor page (their separate web installers do; in the app
    all installing goes through “Firmware+”). Also refused, because the key could be hidden that way: data
    that starts in the middle of a message and SysEx that is not closed in the same call (Web MIDI itself
    does not permit either)."""
    data = [int(x) & 0xFF for x in data]
    if not data or data[0] < 0x80 or data[0] == 0xF7:
        return False
    i, n = 0, len(data)
    while i < n:
        if data[i] != 0xF0:
            i += 1
            continue
        j = i + 1
        while j < n and data[j] < 0x80:
            j += 1
        if j >= n or data[j] != 0xF7:                  # open SysEx, or another status byte inside it
            return False
        if tuple(data[i + 1:i + 4]) == MVAVE_UPDATE_HEADER:
            return False
        i = j + 1
    return True


def out_filter_for(kind: str):
    """Safety filter per page type: editors may talk to the device, simulators only notes/clock."""
    return editor_out_allowed if kind == "editor" else sim_out_allowed


def origin_of(url) -> str:
    """'https://example.org:8443/x?y' -> 'https://example.org:8443' (lower case, default port removed)."""
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
    if ":" in host:                                   # IPv6 literal
        host = f"[{host}]"
    if port is None or (scheme, port) in (("http", 80), ("https", 443)):
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


def origin_can_be_trusted(origin: str) -> bool:
    """Device access can only be granted to ``https://`` origins (nobody on the network can change such a page
    on its way) or to pages on this computer (loopback). Plain ``http://`` sites never get it."""
    from urllib.parse import urlsplit
    try:
        u = urlsplit(origin or "")
        host = (u.hostname or "").lower()
    except ValueError:
        return False
    if not host or u.username or u.password:
        return False
    if u.scheme == "https":
        return True
    if u.scheme != "http":
        return False
    if host == "localhost" or host == "::1":
        return True
    parts = host.split(".")
    return len(parts) == 4 and parts[0] == "127" and all(x.isdigit() and int(x) < 256 for x in parts)


SIM_CONFIG = "simulator"
TRUST_KEY = "device_origins"            # origins that may send editor SysEx to the FM-1 after confirmation
LIBRARY_KEY = "library_origins"         # origins that may only use the FM1 Open library protocol (workbench)
_LEVEL_KEYS = {"editor": TRUST_KEY, "library": LIBRARY_KEY}


def consent_level(kind: str) -> str:
    """which permission a page of this kind can ask for: "editor", "library" or "" (none – notes only)."""
    return {"editor": "editor", "workbench": "library"}.get(kind or "", "")


def _stored(level: str) -> list[str]:
    from .. import config
    return [o for o in (config.module(SIM_CONFIG, {}).get(_LEVEL_KEYS[level]) or []) if isinstance(o, str)]


def origin_trusted(origin: str, level: str = "editor") -> bool:
    """did the user confirm this origin? ``level="library"`` is also covered by an "editor" permission
    (editor SysEx includes the library protocol), not the other way round."""
    if not origin or not origin_can_be_trusted(origin):
        return False
    if origin in _stored("editor"):
        return True
    return level == "library" and origin in _stored("library")


def trust_origin(origin: str, level: str = "editor") -> None:
    """store the user's one-time consent for this origin."""
    from .. import config
    if not origin or not origin_can_be_trusted(origin):
        return
    lst = _stored(level)
    if origin not in lst:
        config.module(SIM_CONFIG, {})[_LEVEL_KEYS[level]] = lst + [origin]
        config.save()


def forget_origin(origin: str) -> None:
    from .. import config
    cfg = config.module(SIM_CONFIG, {})
    for key in _LEVEL_KEYS.values():
        if key in cfg or key == TRUST_KEY:
            cfg[key] = [o for o in (cfg.get(key) or []) if o != origin]
    config.save()


class OutGate:
    """What a loaded page may send to the real FM-1 – decided anew for EVERY message.

    * Every page without permission (online simulators/emulators, typed addresses, unconfirmed origins):
      only ``sim_out_allowed`` – notes, controllers, clock. No SysEx at all.
    * Bundled simulators served by the app itself (``local=True``) while the page is still on the app's own
      address: additionally the FM1 Open library protocol (``library_out_allowed``).
    * Online page of kind "workbench" whose origin the user has confirmed: ``library_out_allowed``.
    * Online editor (kind "editor") whose origin the user has confirmed once (``trust_origin``):
      ``editor_out_allowed`` (Felucca/universal SysEx). Update and boot mode SysEx stay blocked in every case.
    * If the page has meanwhile got a different origin (navigation, redirect), the safe filter applies again.
    """

    def __init__(self, kind: str = "", start_url: str = "", trusted=None, local: bool = False):
        self.kind = kind or ""
        self.origin = origin_of(start_url)
        self.local = bool(local) and bool(self.origin)
        self._trusted = trusted if trusted is not None else origin_trusted

    def _same(self, current_url) -> str:
        cur = origin_of(current_url)
        return cur if cur and cur == self.origin else ""

    def _confirmed(self, cur: str, level: str) -> bool:
        if not origin_can_be_trusted(cur):
            return False
        try:
            return bool(self._trusted(cur, level))
        except TypeError:                              # simple callables in tests: trusted(origin)
            return bool(self._trusted(cur))

    def editor_access(self, current_url) -> bool:
        cur = self._same(current_url)
        return self.kind == "editor" and not self.local and bool(cur) and self._confirmed(cur, "editor")

    def library_access(self, current_url) -> bool:
        cur = self._same(current_url)
        if not cur:
            return False
        if self.local:
            return True
        return consent_level(self.kind) != "" and self._confirmed(cur, "library")

    def allowed(self, data, current_url) -> bool:
        if self.editor_access(current_url):
            return editor_out_allowed(data)
        if self.library_access(current_url):
            return library_out_allowed(data)
        return sim_out_allowed(data)


def page_midi(data) -> bool:
    """What goes from the FM-1/keyboard into the simulator: channel messages and clock/start/stop."""
    return bool(data) and (0x80 <= data[0] <= 0xEF or data[0] in (0xF8, 0xFA, 0xFB, 0xFC))


# ---------------------------------------------------------------- Firefox
def firefox_command(url: str) -> list[str] | None:
    for exe in ("firefox", "firefox-esr"):
        p = shutil.which(exe)
        if p:
            return [p, "--new-window", url]
    p = shutil.which("xdg-open")
    return [p, url] if p else None


# ---------------------------------------------------------------- further MIDI inputs (ALSA via mido)
class SystemInputs:
    """opens the system's MIDI inputs (USB keyboard …) and calls callback(name, bytes) from the MIDI thread."""

    def __init__(self, callback):
        self.callback = callback
        self.open: dict[str, object] = {}

    @staticmethod
    def available(exclude: tuple[str, ...] = ()) -> list[str]:
        try:
            import mido
            names = mido.get_input_names()
        except Exception:                      # mido/rtmidi missing or no ALSA
            return []
        skip = [e.lower() for e in exclude if e]
        return [n for n in dict.fromkeys(names) if not any(s and s in n.lower() for s in skip)
                and "midi through" not in n.lower() and "rtmidi" not in n.lower()]

    def set(self, names: list[str]) -> list[str]:
        """keep exactly these inputs open -> error messages"""
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
