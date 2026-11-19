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


# ------------------------------------------------------------ backup, restore, download mode (contract mflasher-0.12)
# backup    stdout  the info block, then "Backup saved: PATH"
# restore   stdout  the info block, a blank line, the plan: its title ("Restore a backup"), "Writes N sectors (K KiB):
#                   0xA-0xB, …" or "Nothing to write: …", notes, "WARNING: …" lines; then – without a newline –
#                   'Type "yes" to write: ' and it waits on stdin. Anything but "yes" (or EOF): "Nothing written.",
#                   exit 0. After "yes": "Backup saved: PATH" (its own backup before the first erase), "Writing
#                   0xA-0xB" per run (" (again)" on a retry), "Done: everything written is verified (the chip's CRC32
#                   matches)" or "Done: everything written was read back and matches"; a failed write: "Refused: …
#                   does not read back as written …", exit 1
# download-mode     "Sent the <name> the download-mode message over USB MIDI; waiting for it", "In download mode:
#                   /dev/sgN<TAB>…" or "A unit is already in download mode"; refusals on stderr
# restart           "Sent the restart (…); waiting for the unit to reset", "Running: the <name> is on USB MIDI" or
#                   "Left download mode; …"; refusals on stderr

PROMPT = 'Type "yes" to write: '
NOTHING_WRITTEN = "Nothing written."
WHOLE_FLASH_NOTE = "The whole flash is restored, the bootloader included."
RESTORE_TITLE = "Restore a backup"
CRC_CHECKED = "Checked: the chip's CRC32 of the flash matches the read"
_SAVED_RE = re.compile(r"^Backup saved: (.+?)\s*$")
_WRITES_RE = re.compile(r"^Writes (\d+) sectors \((\d+) KiB\): (.+?)\s*$")
_RANGE_RE = re.compile(r"^0x([0-9A-Fa-f]{5,8})-0x([0-9A-Fa-f]{5,8})$")
_DONE_RE = re.compile(r"^Done: everything written (is verified \(the chip's CRC32 matches\)|was read back and matches)"
                      r"\s*$")
_WRITING_RE = re.compile(r"^Writing 0x([0-9A-Fa-f]+)-0x([0-9A-Fa-f]+)( \(again\))?\s*$")
_DM_RE = re.compile(r"^In download mode: (/dev/sg\d+)\t")


@dataclass
class Plan:
    title: str = ""
    sectors: int = 0
    kib: int = 0
    runs: list[tuple[int, int]] = field(default_factory=list)   # [start, end) per run, as listed
    nothing: bool = False             # "Nothing to write: …"
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _lines(stdout: str) -> list[str]:
    """the output's lines – the question has no newline, so what follows the answer starts on its line"""
    return (stdout or "").replace(PROMPT, PROMPT + "\n").splitlines()


def saved_paths(stdout: str) -> list[str]:
    """every "Backup saved: PATH" (backup: one; restore: M-Flasher's own backup before the first erase)"""
    return [m.group(1) for m in (_SAVED_RE.match(ln) for ln in _lines(stdout)) if m]


def parse_ranges(text: str) -> list[tuple[int, int]]:
    """"0x00000-0x77FFF, 0x7A000-0x92FFF" -> [(0, 0x78000), (0x7A000, 0x93000)]; ParseError on anything else"""
    out = []
    for part in (text or "").split(","):
        m = _RANGE_RE.match(part.strip())
        if not m:
            raise ParseError(f"unexpected range {part.strip()[:40]!r}")
        a, b = int(m.group(1), 16), int(m.group(2), 16) + 1
        if b <= a:
            raise ParseError("empty range")
        out.append((a, b))
    return out


def parse_plan(stdout: str) -> Plan:
    """the plan M-Flasher prints before it asks (or before it says there is nothing to write)"""
    lines = (stdout or "").replace(PROMPT, "\n").splitlines()
    at = next((i for i, ln in enumerate(lines) if _WRITES_RE.match(ln) or ln.startswith("Nothing to write:")), None)
    if at is None:
        raise ParseError("no plan in M-Flasher's output")
    title = next((lines[i].strip() for i in range(at - 1, -1, -1) if lines[i].strip()), "")
    p = Plan(title=title)
    m = _WRITES_RE.match(lines[at])
    if m:
        p.sectors, p.kib, p.runs = int(m.group(1)), int(m.group(2)), parse_ranges(m.group(3))
    else:
        p.nothing = True
    for ln in lines[at + 1:]:
        s = ln.strip()
        if not s or s == NOTHING_WRITTEN or _SAVED_RE.match(s) or _WRITING_RE.match(s) or _DONE_RE.match(s):
            if _SAVED_RE.match(s) or _WRITING_RE.match(s):
                break
            continue
        if s.startswith("WARNING: "):
            p.warnings.append(s[len("WARNING: "):])
        else:
            p.notes.append(s)
    return p


def done_verified(stdout: str) -> bool:
    """M-Flasher's own verification line after a write"""
    return any(_DONE_RE.match(ln) for ln in _lines(stdout))


def written_runs(stdout: str) -> list[tuple[int, int]]:
    """the "Writing 0xA-0xB" lines (erase + program of that run has started)"""
    return [(int(m.group(1), 16), int(m.group(2), 16) + 1)
            for m in (_WRITING_RE.match(ln) for ln in _lines(stdout)) if m]


def download_device(stdout: str) -> str:
    """the /dev/sgN `download-mode` reports ("" when it only said a unit is already in download mode)"""
    for ln in (stdout or "").splitlines():
        m = _DM_RE.match(ln)
        if m:
            return m.group(1)
    return ""
