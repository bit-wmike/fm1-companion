# SPDX-License-Identifier: GPL-3.0-only
"""Parser for M-Flasher's text output (no I/O). M-Flasher has no machine-readable output, so the app reads the
lines its command line prints (src/cli.rs and src/ops.rs at the pinned tag, contract "mflasher-0.12"):

find      stdout  "/dev/sgN<TAB>WL82 UBOOT1.00 (/dev/sgN)"            one line per unit in ROM download mode
                  "midi<TAB><ALSA card name> (running; download-mode switches it)"
          stderr  the "SCSI generic driver … is not loaded" hint when a unit is there but `sg` is missing
info      stdout  log lines ("Connecting to …", "Flash chip …", "Checked: the chip's CRC32 …"), then the info block
                  "  {key:11} {value}" for model, bootloader, firmware, app, chip key, flash, unit id, loader and the
                  evidence lines "    * …" (strong) / "    - …"
errors    stderr  "Refused: …" or an I/O error text; exit code 1
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

CONTRACT = "mflasher-0.12"
INFO_KEYS = ("model", "bootloader", "firmware", "app", "chip key", "flash", "unit id", "loader")
_INFO_RE = re.compile(r"^  (model|bootloader|firmware|app|chip key|flash|unit id|loader)\s+(.*?)\s*$")
_EVIDENCE_RE = re.compile(r"^    ([*-]) (.*?)\s*$")
_FIND_SG_RE = re.compile(r"^(/dev/sg\d+)\t(.*?)\s*$")
_FIND_MIDI_RE = re.compile(r"^midi\t(.*?)(?: \(running; download-mode switches it\))?\s*$")
_UNIT_ID_RE = re.compile(r"^[0-9a-f]{16}$")
SG_MISSING_HINT = "SCSI generic driver"


class ParseError(ValueError):
    """the output does not have the form this app version knows (a changed M-Flasher?)"""


@dataclass
class DownloadUnit:
    device: str                 # /dev/sgN
    description: str            # "WL82 UBOOT1.00 (/dev/sgN)"


@dataclass
class FindResult:
    download: list[DownloadUnit] = field(default_factory=list)
    midi: list[str] = field(default_factory=list)       # ALSA card names of running units
    sg_missing: bool = False
    other: list[str] = field(default_factory=list)      # lines the parser did not recognise


@dataclass
class Info:
    model: str
    bootloader: str
    firmware: str               # M-Flasher's guess from the flash layout – not trusted by the app
    app: str
    chip_key: int | None
    flash_id: int | None
    unit_id: str                # 16 lowercase hex digits, "" when M-Flasher shows "–" (no id from this loader)
    loader: str
    evidence: list[tuple[bool, str]] = field(default_factory=list)
    crc_checked: bool = False   # "Checked: the chip's CRC32 of the flash matches the read"
    fields: dict[str, str] = field(default_factory=dict)

    @property
    def m_bootloader(self) -> bool:
        return self.bootloader.lower().startswith("m-bootloader")


def parse_find(stdout: str, stderr: str = "") -> FindResult:
    r = FindResult(sg_missing=SG_MISSING_HINT in (stderr or ""))
    for line in (stdout or "").splitlines():
        if not line.strip():
            continue
        m = _FIND_SG_RE.match(line)
        if m:
            r.download.append(DownloadUnit(m.group(1), m.group(2)))
            continue
        m = _FIND_MIDI_RE.match(line)
        if m:
            r.midi.append(m.group(1))
            continue
        r.other.append(line)
    return r


def _hex(v: str) -> int | None:
    try:
        return int(v, 16) if v.lower().startswith("0x") else None
    except ValueError:
        return None


def parse_info(stdout: str) -> Info:
    """the info block of `info` (also printed by backup/restore/firmware/bootloader before their own lines)"""
    fields: dict[str, str] = {}
    evidence: list[tuple[bool, str]] = []
    crc = False
    for line in (stdout or "").splitlines():
        if line.startswith("Checked: the chip's CRC32"):
            crc = True
        m = _INFO_RE.match(line)
        if m and m.group(1) not in fields:
            fields[m.group(1)] = m.group(2)
            continue
        m = _EVIDENCE_RE.match(line)
        if m and fields:
            evidence.append((m.group(1) == "*", m.group(2)))
    missing = [k for k in INFO_KEYS if k not in fields]
    if missing:
        raise ParseError("M-Flasher's info block lacks: " + ", ".join(missing))
    uid = fields["unit id"].strip().lower()
    if uid in ("–", "-", ""):
        uid = ""
    elif not _UNIT_ID_RE.match(uid):
        raise ParseError("unexpected unit id format")
    chip, flash = _hex(fields["chip key"]), _hex(fields["flash"])
    if chip is None or flash is None:
        raise ParseError("chip key or flash ID is not a hex number")
    return Info(model=fields["model"], bootloader=fields["bootloader"], firmware=fields["firmware"], app=fields["app"],
                chip_key=chip, flash_id=flash, unit_id=uid, loader=fields["loader"], evidence=evidence,
                crc_checked=crc, fields=fields)


def refusal(stderr: str) -> str:
    """M-Flasher's reason when it refused or failed (first "Refused: …" line, else the last non-progress line)"""
    lines = [ln.strip() for ln in (stderr or "").replace("\r", "\n").splitlines() if ln.strip()]
    lines = [ln for ln in lines if not re.fullmatch(r"\d+%", ln)]
    for ln in lines:
        if ln.startswith("Refused: "):
            return ln[len("Refused: "):]
    return lines[-1] if lines else ""
