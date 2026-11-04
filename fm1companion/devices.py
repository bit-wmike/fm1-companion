# SPDX-License-Identifier: GPL-3.0-only
"""Find connected FM-1, tell them apart and remember them as profiles.

Each device gets a profile (name, colour, notes, last known firmware).
A device is recognised – if possible – by its USB serial number. If it has none
(or the firmware does not report it), the user decides once which
profile is meant; with only one matching profile this happens automatically.
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path

from .config import DATA_DIR

PORT_RE = re.compile(r"melodee|felucca|\bfm-1\b|fm1|sloop|skiff|m-vave", re.I)
# Suite 2.0 (installer): further names under which an FM-1 shows up (docs/flash-protocol.md, section 6)
FM1_NAME_RE = re.compile(r"melodee|felucca|\bfm-?1\b|fm1|sloop|skiff|m-vave|ota-fm|transit|orbit|jangada|"
                         r"vector37|fmini|mycelium|ghoulbox|hortator", re.I)
GENERIC_NAME_RE = re.compile(r"usb composite device|composite|usb-?midi|sinco", re.I)
UPDATE_NAME_RE = re.compile(r"\bupdate\b|^ota-", re.I)
NOT_FM1_RE = re.compile(r"midi through|timidity|fluid|pipewire|virtual|rtmidi|mido|\bseq\b", re.I)
# known USB IDs (VID:PID): stock firmware, its update loader, Felucca family and their loaders
FM1_USB_IDS = {"4c4a:c755": "stock", "4c4a:4155": "stock", "4d4a:4155": "stock-update",
               "1209:0001": "felucca", "1209:0002": "felucca-update"}
UBOOT_USB_ID = "4c4a:8057"     # ROM boot mode (no MIDI)
CLIENT_RE = re.compile(r"(\d+):(\d+)\s*$")
PROFILES_FILE = DATA_DIR / "devices.json"
PROFILE_COLORS = ["#3daee9", "#f67400", "#27ae60", "#da4453", "#9b59b6", "#fdbc4b"]


@dataclass
class PortInfo:
    """A found FM-1 (input and output of the same ALSA client)."""
    in_name: str
    out_name: str
    label: str              # "Melodee" or similar (port name without ALSA suffix)
    client: int | None
    card: int | None = None
    serial: str = ""
    usb_id: str = ""
    usb_path: str = ""
    update_mode: bool = False
    weak: bool = False      # only recognised by the generic name (e.g. "USB Composite Device")
    product: str = ""
    manufacturer: str = ""
    speed: str = ""          # Mbit/s according to sysfs
    virtual: bool = False    # not a real device: the suite's studio twin (never flash, never rescue)

    @property
    def key(self) -> str:
        """unique among the currently connected devices (even with the same serial number)"""
        return f"{self.usb_path or self.client or self.out_name}|{self.serial}"


@dataclass
class Profile:
    id: str
    name: str
    color: str = "#3daee9"
    serial: str = ""
    notes: str = ""
    firmware: str = ""          # e.g. "melodee"
    firmware_version: str = ""  # e.g. "v1.6.0-rc.7"
    version_string: str = ""    # what INFO reports
    identity: str = ""          # e.g. "FM-1_91060"
    usb_path: str = ""          # last used USB connection (helps matching without a serial number)
    firmware_history: list = field(default_factory=list)   # [{"time", "firmware", "version", "identity", "how"}]
    firmware_choice: dict = field(default_factory=dict)    # identity -> firmware key chosen by the user
    last_seen: float = 0.0
    created: float = field(default_factory=time.time)

    @property
    def folder_name(self) -> str:
        return safe_folder_name(self.name, self.id)


MAX_FOLDER_BYTES = 100


def safe_folder_name(name: str, fallback: str = "FM-1") -> str:
    """Folder name from a device name: no path separators, not "."/"..", at most 100 bytes of UTF-8."""
    n = re.sub(r"[^\w\-. ]+", "_", name or "").strip().strip(".").strip()
    raw = n.encode("utf-8")[:MAX_FOLDER_BYTES]
    n = raw.decode("utf-8", "ignore").strip().rstrip(".").strip()
    if not n or set(n) <= {".", " "}:
        n = re.sub(r"[^\w\-]+", "_", fallback or "FM-1")[:MAX_FOLDER_BYTES] or "FM-1"
    return n


def move_profile_folder(base: Path, old_name: str, new_name: str) -> Path:
    """Backups move along when renaming (shared by the overview and the device page).
    If the new folder already exists, the files are moved into it one by one (never overwritten).
    -> new folder. OSError on write errors."""
    import shutil
    old, new = Path(base) / old_name, Path(base) / new_name
    if old == new or not old.exists():
        return new
    if not new.exists():
        old.rename(new)
        return new
    for f in sorted(old.rglob("*")):
        if f.is_dir():
            continue
        target = new / f.relative_to(old)
        if target.exists():
            target = target.with_name(f"{target.stem}_{old_name}{target.suffix}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(f), target)
    shutil.rmtree(old, ignore_errors=True)
    return new


# ------------------------------------------------------------- profiles ---

class ProfileStore:
    def __init__(self, path: Path = PROFILES_FILE):
        self.path = path
        self._lock = threading.Lock()
        self.profiles: dict[str, Profile] = {}
        self.load()

    def load(self):
        try:
            raw = json.loads(self.path.read_text("utf-8"))
            self.profiles = {p["id"]: Profile(**{k: v for k, v in p.items() if k in Profile.__dataclass_fields__})
                             for p in raw.get("profiles", [])}
        except (OSError, ValueError, TypeError):
            self.profiles = {}

    def save(self):
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"profiles": [asdict(p) for p in self.profiles.values()]}, indent=2,
                                      ensure_ascii=False), "utf-8")
            tmp.replace(self.path)

    def create(self, name: str = "", serial: str = "") -> Profile:
        n = len(self.profiles)
        if not name:                              # first free letter – after "Forget" no second "FM-1 B"
            taken = {p.name for p in self.profiles.values()}
            name = next((f"FM-1 {chr(65 + i)}" for i in range(26) if f"FM-1 {chr(65 + i)}" not in taken),
                        f"FM-1 {n + 1}")
        name = self.unique_name(name)
        p = Profile(id=uuid.uuid4().hex[:10], name=name, color=PROFILE_COLORS[n % len(PROFILE_COLORS)], serial=serial)
        self.profiles[p.id] = p
        self.save()
        return p

    def name_taken(self, name: str, except_id: str = "") -> Profile | None:
        """another profile with this name or that would get the same backup folder (e.g. "FM-1/A" and "FM-1_A")"""
        folder = safe_folder_name(name, "\0")       # name without usable characters: the folder would be the profile ID
        for p in self.profiles.values():
            if p.id != except_id and (p.name.strip().lower() == name.strip().lower() or p.folder_name == folder):
                return p
        return None

    def unique_name(self, name: str, except_id: str = "") -> str:
        base, n = name, 2
        while self.name_taken(name, except_id):
            name = f"{base} ({n})"
            n += 1
        return name

    def delete(self, pid: str):
        self.profiles.pop(pid, None)
        self.save()

    def by_serial(self, serial: str) -> Profile | None:
        if not serial:
            return None
        return next((p for p in self.profiles.values() if p.serial == serial), None)

    def without_serial(self) -> list[Profile]:
        return [p for p in self.profiles.values() if not p.serial]


BOGUS_SERIAL = re.compile(r"^(.)\1*$|^0?123456789(0|abcdef)?$|^0+1?$", re.I)


def usable_serial(port: PortInfo, all_ports: list[PortInfo]) -> str:
    """Use the serial number only if it looks genuine and is unique among the connected devices."""
    s = (port.serial or "").strip()
    if len(s) < 4 or BOGUS_SERIAL.match(s):
        return ""
    if sum(1 for p in all_ports if (p.serial or "").strip() == s) > 1:
        return ""
    return s


# ------------------------------------------------------------ detection ---

def _read(path: Path) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return ""


def usb_details(client: int | None) -> dict:
    """ALSA sequencer client -> sound card -> USB device (serial number, ID, path).
    Kernel clients of a card have the numbers 16 + 4 × card."""
    if client is None or client < 16:
        return {}
    card = (client - 16) // 4
    base = Path(f"/sys/class/sound/card{card}/device")
    out = {"card": card}
    try:
        dev = base.resolve()
    except OSError:
        return out
    # the USB device is the first ancestor with an idVendor file
    for d in [dev, *dev.parents]:
        if (d / "idVendor").exists():
            out["serial"] = _read(d / "serial")
            out["usb_id"] = f"{_read(d / 'idVendor')}:{_read(d / 'idProduct')}"
            out["usb_path"] = d.name
            out["product"] = _read(d / "product")
            out["manufacturer"] = _read(d / "manufacturer")
            out["speed"] = _read(d / "speed")
            break
    return out


def _label(name: str) -> str:
    base = CLIENT_RE.sub("", name).strip()
    return base.split(":")[0].strip() or base


def scan_ports() -> list[PortInfo]:
    """all connected FM-1 (by port names of the Felucca family)."""
    try:
        import mido
        ins = mido.get_input_names()
        outs = mido.get_output_names()
    except Exception:
        return []
    found: dict[str, PortInfo] = {}
    for o in outs:
        m = CLIENT_RE.search(o)
        client = int(m.group(1)) if m else None
        kind = classify_port(o, usb_details(client).get("usb_id", ""))
        if not kind:
            continue
        prefix = o.rsplit(" ", 1)[0] if m else o
        inp = next((i for i in ins if (m and CLIENT_RE.search(i) and CLIENT_RE.search(i).group(1) == m.group(1))
                    or (not m and i == o)), None)
        if inp is None:
            inp = next((i for i in ins if i.startswith(prefix)), None)
        if inp is None:
            continue
        d = usb_details(client)
        uid = d.get("usb_id", "")
        info = PortInfo(in_name=inp, out_name=o, label=_label(o), client=client,
                        update_mode=bool(UPDATE_NAME_RE.search(_label(o))) or FM1_USB_IDS.get(uid, "").endswith("update"),
                        weak=kind == "weak")
        info.card = d.get("card")
        info.serial = d.get("serial", "")
        info.usb_id = d.get("usb_id", "")
        info.usb_path = d.get("usb_path", "")
        info.product = d.get("product", "")
        info.manufacturer = d.get("manufacturer", "")
        info.speed = d.get("speed", "")
        found[info.key] = info
    return list(found.values())


def classify_port(name: str, usb_id: str = "") -> str:
    """"strong" (definitely an FM-1), "weak" (generic name without USB info) or "" (not an FM-1).
    Foreign devices with a generic name and a known, foreign USB ID are never touched."""
    if NOT_FM1_RE.search(name):
        return ""
    uid = (usb_id or "").lower()
    if uid in FM1_USB_IDS:
        return "strong"
    if FM1_NAME_RE.search(name):
        return "strong"
    if GENERIC_NAME_RE.search(name):
        return "" if uid else "weak"
    return ""


def install_candidate(name: str) -> bool:
    """may the installer send the handshake on this MIDI port? (mido port name)"""
    m = CLIENT_RE.search(name)
    uid = usb_details(int(m.group(1))).get("usb_id", "") if m else ""
    if classify_port(name, uid):
        return True
    # a fork's update loader is called "<Name> Update" – recognisable by the Felucca loader ID 1209:0002
    return bool(UPDATE_NAME_RE.search(name) and uid in FM1_USB_IDS)


def uboot_present(sysroot: str = "/sys") -> list[str]:
    """USB paths of FM-1 in ROM boot mode (4c4a:8057) – they do not appear as MIDI devices"""
    base = Path(sysroot) / "bus" / "usb" / "devices"
    out = []
    if base.is_dir():
        for d in sorted(base.iterdir()):
            if f"{_read(d / 'idVendor')}:{_read(d / 'idProduct')}".lower() == UBOOT_USB_ID:
                out.append(d.name)
    return out


def alsa_card_for(port: PortInfo) -> str | None:
    """ALSA card name for audio recording (e.g. 'Melodee')."""
    if port.card is not None:
        cid = _read(Path(f"/proc/asound/card{port.card}/id"))
        if cid:
            return cid
    cards = _read(Path("/proc/asound/cards"))
    for line in cards.splitlines():
        m = re.match(r"\s*(\d+)\s+\[(\S+)\s*\]", line)
        if m and (PORT_RE.search(line) or FM1_NAME_RE.search(line)):
            return m.group(2)
    return None
