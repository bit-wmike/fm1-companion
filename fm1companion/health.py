# SPDX-License-Identifier: GPL-3.0-only
"""Hardware info: the computer (Raspberry Pi or any Linux) and the FM-1.

Everything is read from /proc, /sys and – if present – vcgencmd; if something
is missing (different computer, not a Pi), the field simply stays empty.
"""
from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import time
from pathlib import Path

from . import backup
from .i18n import N_, _
from .protocol import Cmd, Reader, parse_audio_stats, parse_info, ProtocolError


def _read(p: str) -> str:
    try:
        return Path(p).read_text(errors="replace").strip().strip("\0")
    except OSError:
        return ""


def _run(args, timeout=3) -> str:
    if not shutil.which(args[0]):
        return ""
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


# vcgencmd get_throttled bits -> text (translated where used)
THROTTLE_BITS = {0: N_("under-voltage now"), 1: N_("clock throttled (ARM) now"), 2: N_("throttled now"),
                 3: N_("temperature limit now"), 16: N_("under-voltage since start-up"),
                 17: N_("clock throttled since start-up"), 18: N_("throttled since start-up"),
                 19: N_("temperature limit since start-up")}


class PiMonitor:
    def __init__(self):
        self._last_cpu = None

    def static(self) -> dict:
        osr = {}
        for line in _read("/etc/os-release").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                osr[k] = v.strip('"')
        mem = self._meminfo()
        return {
            "Modell": _read("/proc/device-tree/model") or platform.machine(),
            "System": osr.get("PRETTY_NAME", platform.system()),
            "Kernel": platform.release(),
            "Architektur": platform.machine(),
            "CPU-Kerne": str(os.cpu_count() or ""),
            "Arbeitsspeicher": f"{mem.get('MemTotal', 0) / 1048576:.1f} GiB" if mem else "",
            "Desktop": os.environ.get("XDG_CURRENT_DESKTOP", "") + (" (Wayland)" if os.environ.get("WAYLAND_DISPLAY") else ""),
            "Python": platform.python_version(),
        }

    def _meminfo(self) -> dict:
        out = {}
        for line in _read("/proc/meminfo").splitlines():
            m = re.match(r"(\w+):\s+(\d+)", line)
            if m:
                out[m.group(1)] = int(m.group(2))
        return out

    def _cpu_percent(self) -> float | None:
        line = _read("/proc/stat").splitlines()[:1]
        if not line:
            return None
        v = [int(x) for x in line[0].split()[1:]]
        idle, total = v[3] + (v[4] if len(v) > 4 else 0), sum(v)
        last, self._last_cpu = self._last_cpu, (idle, total)
        if not last or total == last[1]:
            return None
        return 100.0 * (1 - (idle - last[0]) / (total - last[1]))

    def sample(self, folders: list[Path] | None = None) -> dict:
        d: dict = {}
        t = _read("/sys/class/thermal/thermal_zone0/temp")
        d["temp_c"] = int(t) / 1000 if t.isdigit() else None
        f = _read("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq")
        d["freq_mhz"] = int(f) / 1000 if f.isdigit() else None
        fmax = _read("/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq")
        d["freq_max_mhz"] = int(fmax) / 1000 if fmax.isdigit() else None
        d["cpu_percent"] = self._cpu_percent()
        try:
            d["load"] = os.getloadavg()
        except OSError:
            d["load"] = None
        m = self._meminfo()
        if m:
            total, avail = m.get("MemTotal", 0), m.get("MemAvailable", 0)
            d["mem_total"], d["mem_used"] = total * 1024, (total - avail) * 1024
            st, sf = m.get("SwapTotal", 0), m.get("SwapFree", 0)
            d["swap_total"], d["swap_used"] = st * 1024, (st - sf) * 1024
        up = _read("/proc/uptime").split()
        d["uptime_s"] = float(up[0]) if up else None
        # fan (Pi 5 / Argon & co.)
        fan = None
        for p in Path("/sys/devices/platform").glob("cooling_fan*/hwmon/*/fan1_input"):
            v = _read(str(p))
            if v.isdigit():
                fan = int(v)
        d["fan_rpm"] = fan
        # vcgencmd (Raspberry Pi only)
        thr = _run(["vcgencmd", "get_throttled"])
        if thr:
            m2 = re.search(r"0x([0-9a-fA-F]+)", thr)
            bits = int(m2.group(1), 16) if m2 else 0
            d["throttled"] = [_(txt) for b, txt in THROTTLE_BITS.items() if bits >> b & 1]
            d["throttled_raw"] = hex(bits)
        volts = _run(["vcgencmd", "measure_volts", "core"])
        d["core_volts"] = volts.split("=")[-1] if volts else ""
        ext5 = _run(["vcgencmd", "pmic_read_adc", "EXT5V_V"])
        m3 = re.search(r"=([\d.]+)V", ext5)
        d["ext5v"] = float(m3.group(1)) if m3 else None
        # memory
        disks = []
        for folder in folders or [Path.home()]:
            try:
                u = shutil.disk_usage(folder)
                disks.append((str(folder), u.total, u.used, u.free))
            except OSError:
                pass
        d["disks"] = disks
        d["time"] = time.time()
        return d

    def quick(self, folder: Path | None = None) -> dict:
        """cheap values for the status bar (only /proc and /sys, no subprocess)."""
        d: dict = {"cpu_percent": self._cpu_percent()}
        t = _read("/sys/class/thermal/thermal_zone0/temp")
        d["temp_c"] = int(t) / 1000 if t.lstrip("-").isdigit() else None
        m = self._meminfo()
        if m.get("MemTotal"):
            d["mem_percent"] = 100.0 * (m["MemTotal"] - m.get("MemAvailable", 0)) / m["MemTotal"]
            d["mem_total"] = m["MemTotal"] * 1024
        f = _read("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq")
        d["freq_mhz"] = int(f) / 1000 if f.isdigit() else None
        try:
            d["load"] = os.getloadavg()
        except OSError:
            d["load"] = None
        if folder is not None:
            try:
                u = shutil.disk_usage(folder)
                d["disk_free"], d["disk_total"] = u.free, u.total
            except OSError:
                d["disk_free"] = None
        d["undervoltage"] = self.undervoltage_alarm()
        return d

    @staticmethod
    def undervoltage_alarm() -> bool | None:
        """Pi: hwmon 'rpi_volt' reports undervoltage via in0_lcrit_alarm (without vcgencmd)."""
        for h in Path("/sys/class/hwmon").glob("hwmon*"):
            if _read(str(h / "name")) == "rpi_volt":
                v = _read(str(h / "in0_lcrit_alarm"))
                return v == "1" if v else None
        return None

    @staticmethod
    def throttled() -> tuple[int | None, list[str]]:
        """vcgencmd get_throttled (subprocess – do not call in the GUI thread)."""
        thr = _run(["vcgencmd", "get_throttled"])
        m = re.search(r"0x([0-9a-fA-F]+)", thr)
        if not m:
            return None, []
        bits = int(m.group(1), 16)
        return bits, [_(txt) for b, txt in THROTTLE_BITS.items() if bits >> b & 1]

    @staticmethod
    def usb_devices() -> list[dict]:
        out = []
        for dev in sorted(Path("/sys/bus/usb/devices").glob("*")):
            if not (dev / "idVendor").exists() or ":" in dev.name:
                continue
            out.append({"bus": dev.name, "id": f"{_read(str(dev / 'idVendor'))}:{_read(str(dev / 'idProduct'))}",
                        "name": " ".join(filter(None, [_read(str(dev / "manufacturer")), _read(str(dev / "product"))])),
                        "speed": _read(str(dev / "speed")), "serial": _read(str(dev / "serial"))})
        return out

    @staticmethod
    def audio_cards() -> list[str]:
        return [ln.strip() for ln in _read("/proc/asound/cards").splitlines() if re.match(r"\s*\d+ \[", ln)]


# ---------------------------------------------------------------- FM-1 ---

MELODEE_POOL_BYTES = 79 * 4096          # Melodee 1.6: projects + presets share 79 sectors


def audio_stats_supported(version: str) -> bool:
    """Command 72 is AUDIO_STATS only on Melodee and M-VAVE FM1 Open – on SLOOP, e.g., it is DSYN_LIST.
    Other firmwares therefore never get it."""
    v = (version or "").lower()
    return "melodee" in v or bool(re.search(r"fm-?1[ _-]?open|official[ _-]open", v))


def fm1_report(link) -> dict:
    """everything that can be queried via the protocol – errors in individual parts do not abort."""
    rep: dict = {}
    try:
        info = parse_info(link.request(Cmd.INFO, [], timeout=1.5))
        rep["version"] = info.version
        rep["engines"] = info.engines
        rep["tracks"] = info.ntrk
        rep["steps"] = info.nstep
    except Exception as e:
        rep["error"] = str(e)
        return rep
    rep["audio_supported"] = audio_stats_supported(info.version)
    rep["audio"] = None
    if rep["audio_supported"]:
        try:
            rep["audio"] = parse_audio_stats(link.request(Cmd.AUDIO_STATS, [], timeout=1.0, retries=0))
        except Exception:
            rep["audio"] = None
    try:
        man = backup.list_objects(link)
        jiant = backup.is_jiant(o.id for o in man)
        projects = [o for o in man if backup.is_project(o.id, jiant) and o.id != 0]
        rep["projects_total"] = len(projects)
        rep["projects_used"] = sum(1 for o in projects if o.size)
        rep["projects_bytes"] = sum(o.size for o in projects)
        rep["live_bytes"] = next((o.size for o in man if o.id == 0), 0)
        rep["objects"] = len(man)
        rep["backup_bytes"] = sum(o.size for o in man)
        if len(man) == 57:
            presets = sum(o.size for o in man if 6 <= o.id <= 28)
            rep["pool_used"] = rep["projects_bytes"] + presets
            rep["pool_total"] = MELODEE_POOL_BYTES
    except Exception:
        rep["projects_total"] = None
    try:
        a = link.request(Cmd.SMP_INFO, [], timeout=1.0, retries=0)
        from .protocol import parse_smp_info
        kib, slots = parse_smp_info(a)
        rep["sample_slots"] = slots
        rep["sample_slot_kib"] = kib
    except Exception:
        rep["sample_slots"] = None
    try:
        total, used = 0, 0
        for s in fm1_presets(link):
            total += 1
            used += s.used
        rep["presets_total"], rep["presets_used"] = total, used
    except Exception:
        rep["presets_total"] = None
    return rep


def fm1_live(link, version: str | None = None) -> dict | None:
    """current load values (AUDIO_STATS with all reading extensions). With `version`: None if the firmware
    does not know AUDIO_STATS (then nothing is sent at all)."""
    from .protocol import AUDIO_FLAGS_ALL
    if version is not None and not audio_stats_supported(version):
        return None
    a = link.request(Cmd.AUDIO_STATS, [AUDIO_FLAGS_ALL], timeout=1.0, retries=0)
    return parse_audio_stats(a)


def fm1_presets(link, log=None) -> list:
    """all user preset slots (command 80, 16 entries each)."""
    from .protocol import CMD_LIBRARY, parse_library_list
    out = []
    start, total = 0, 1
    while start < total:
        a = link.request(CMD_LIBRARY, [0, start & 0x7F, (start >> 7) & 0x7F, 16], timeout=1.5, retries=1)
        total, chunk = parse_library_list(a)
        if not chunk:
            break
        out += chunk
        start = chunk[-1].slot + 1
        if log:
            log(start, total)
    return out
