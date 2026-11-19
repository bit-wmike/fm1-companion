# SPDX-License-Identifier: GPL-3.0-only
"""Runs the bundled M-Flasher as a separate program – read-only commands only in this version.

* The binary comes from tools/store.py (bundled or updated) and its SHA-256 is checked right before every run.
* No shell; a fixed argument list; stdin is closed (a command that would ask "Type "yes" to write" reads EOF and
  writes nothing); LANG=C so its texts are always English.
* A time limit per command and a cancel event: the process is asked to stop (SIGTERM), then killed after 2 s.
* Commands that talk to a unit take the one-installation-at-a-time lock (flash/journal.py): never while an
  installation, a recovery or another tool run owns the device.
* Every line goes into a flight log ("tool-<time>.txt" next to the installation logs) with unit ids masked.
* Write commands (backup, restore, firmware, bootloader, download-mode, restart, run-ram) are refused here.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..i18n import _
from . import mflasher_parse as mp
from .store import Candidate, Store

log = logging.getLogger(__name__)

TIMEOUTS = {"find": 10.0, "info": 60.0, "--version": 10.0, "--help": 10.0}
TALKS_TO_UNIT = ("find", "info")              # take the installation lock
_DEVICE_RE = re.compile(r"^/dev/sg\d{1,3}$")
_HEX16_RE = re.compile(r"\b[0-9A-Fa-f]{16}\b")


def mask_ids(text: str) -> str:
    """unit ids (16 hex digits) shortened to their last four, like USB serial numbers in the flight log"""
    return _HEX16_RE.sub(lambda m: "…" + m.group(0)[-4:], text)


class ToolError(Exception):
    """a user-facing reason (translated) why the tool did not run"""


class ToolUnavailable(ToolError):
    pass


class ToolBusy(ToolError):
    pass


@dataclass
class RunResult:
    version: str
    source: str
    args: list[str]
    rc: int | None
    out: str = ""
    err: str = ""
    timed_out: bool = False
    cancelled: bool = False
    seconds: float = 0.0
    log_path: Path | None = None
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.rc == 0 and not self.timed_out and not self.cancelled


def unavailable_reason(store: Store) -> str:
    """why there is no M-Flasher to run on this computer ("" when there is one)"""
    if not store.arch:
        import platform
        return _("M-Flasher is not available for this computer ({machine}): it is built for 64-bit ARM (Raspberry "
                 "Pi OS 64-bit) and for x86-64 PCs only. On a Raspberry Pi with the 32-bit system the recovery tools "
                 "on this page still work.").format(machine=platform.machine() or "?")
    c, _problems = store.select("m-flasher")
    if c is None:
        b = store.bundled("m-flasher")
        if b is not None and not b.present:
            return _("M-Flasher is not bundled with this installation (a source checkout?). Run "
                     "tools/fetch_tools.py once, or install the app from its .deb or .run package.")
        return _("The bundled M-Flasher did not pass its checksum test, so the app does not run it. Reinstall "
                 "the app.")
    return ""


def _check_args(args: list[str]) -> None:
    if not args or args[0] not in TIMEOUTS:
        raise ToolError(_("This M-Flasher command is not available in the app."))
    if args[0] == "info":
        if len(args) != 3 or args[1] != "--device" or not _DEVICE_RE.match(args[2]):
            raise ToolError(_("This M-Flasher command is not available in the app."))
    elif len(args) != 1:
        raise ToolError(_("This M-Flasher command is not available in the app."))


def _flight(folder: Path | None):
    from ..flash import robust
    f = robust.FlightLog(folder)
    f.prefix = "tool"
    return f


def run_mflasher(args: list[str], *, store: Store | None = None, cancel: threading.Event | None = None,
                 timeout: float | None = None, log_dir: Path | None = None, journal=None,
                 on_line=None) -> RunResult:
    """run one read-only M-Flasher command; raises ToolError when it cannot run at all"""
    _check_args(args)
    store = store or Store()
    why = unavailable_reason(store)
    if why:
        raise ToolUnavailable(why)
    cand, problems = store.select("m-flasher")
    if cand is None:                             # (unavailable_reason said there is one: changed in between)
        raise ToolUnavailable(_("The bundled M-Flasher did not pass its checksum test, so the app does not run it. "
                                "Reinstall the app."))
    lock = None
    if args[0] in TALKS_TO_UNIT:
        from ..flash import journal as fj
        lock = journal or fj.Journal()
        try:
            lock.acquire()
        except fj.Busy as e:
            raise ToolBusy(_("M-Flasher waits: {reason}").format(reason=e)) from e
    if log_dir is None:
        from ..flash import journal as fj
        log_dir = fj.root() / "logs"
    flight = _flight(log_dir)
    try:
        res = _run(cand, args, timeout if timeout is not None else TIMEOUTS[args[0]], cancel, flight, on_line)
        res.problems = problems
    finally:
        if lock is not None:
            lock.release()
    _judge(store, cand, res)
    outcome = "ok" if res.ok else ("cancelled" if res.cancelled else ("timeout" if res.timed_out else f"exit {res.rc}"))
    header = [f"tool: M-Flasher {cand.version} ({cand.source}), command: {' '.join(args)}",
              *(f"note: {p}" for p in problems)]
    res.log_path = flight.finish(outcome, header=header) if args[0] in TALKS_TO_UNIT else None
    log.info("M-Flasher %s (%s) %s: %s in %.1f s", cand.version, cand.source, args[0], outcome, res.seconds)
    return res


def _run(cand: Candidate, args: list[str], timeout: float, cancel, flight, on_line) -> RunResult:
    ok, why = cand.verify()                       # right before the start: never run a changed binary
    if not ok:
        flight.ev("refused", f"M-Flasher {cand.version}: {why}")
        raise ToolUnavailable(_("The bundled M-Flasher did not pass its checksum test, so the app does not run it. "
                                "Reinstall the app."))
    env = dict(os.environ, LANG="C", LC_ALL="C")
    env.pop("LANGUAGE", None)
    flight.ev("exec", f"m-flasher {' '.join(args)} (version {cand.version}, {cand.source}, sha256 {cand.sha256[:12]}…)")
    t0 = time.monotonic()
    try:
        p = subprocess.Popen([str(cand.path), *args], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, env=env, start_new_session=True, cwd=str(Path.home()))
    except OSError as e:
        flight.ev("error", f"could not start: {e.strerror or e}")
        return RunResult(cand.version, cand.source, list(args), None, err=str(e.strerror or e))
    chunks: dict[str, list[str]] = {"out": [], "err": []}

    def reader(stream, kind):
        buf = ""
        for raw in iter(lambda: stream.read1(4096) if hasattr(stream, "read1") else stream.read(4096), b""):
            text = raw.decode("utf-8", "replace")
            chunks[kind].append(text)
            buf += text
            *lines, buf = re.split(r"[\r\n]", buf)
            for ln in lines:
                if ln.strip():
                    flight.ev(kind, mask_ids(ln))
                    if on_line:
                        try:
                            on_line(kind, mask_ids(ln))
                        except Exception:  # noqa: BLE001 – a UI callback never stops the run
                            pass
        if buf.strip():
            flight.ev(kind, mask_ids(buf))
    threads = [threading.Thread(target=reader, args=(p.stdout, "out"), daemon=True),
               threading.Thread(target=reader, args=(p.stderr, "err"), daemon=True)]
    for t in threads:
        t.start()
    timed_out = cancelled = False
    while True:
        try:
            p.wait(0.1)
            break
        except subprocess.TimeoutExpired:
            pass
        if cancel is not None and cancel.is_set():
            cancelled = True
        elif time.monotonic() - t0 > timeout:
            timed_out = True
        if cancelled or timed_out:
            flight.ev("stop", "cancelled" if cancelled else f"no answer within {timeout:.0f} s")
            p.terminate()
            try:
                p.wait(2.0)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
            break
    for t in threads:
        t.join(2.0)
    seconds = time.monotonic() - t0
    flight.ev("exit", f"{p.returncode} after {seconds:.1f}s")
    return RunResult(cand.version, cand.source, list(args), p.returncode, "".join(chunks["out"]),
                     "".join(chunks["err"]), timed_out, cancelled, seconds)


def _judge(store: Store, cand: Candidate, res: RunResult):
    """an update proves itself with a clean `info` run; it counts as broken when it crashes, cannot start or its
    output no longer has the known form – then the previous version is used again"""
    if cand.source != "update" or res.cancelled:
        return
    broken = res.rc is None or (res.rc not in (0, 1)) or res.timed_out
    proven = False
    if res.args[0] == "info" and res.rc == 0:
        try:
            mp.parse_info(res.out)
            proven = True
        except mp.ParseError:
            broken = True
    if broken:
        store.mark(cand.key, cand.version, failed=True)
    elif proven and not cand.proven:
        store.mark(cand.key, cand.version, ok=True)


# ------------------------------------------------------------ read-only commands with parsed results

def find(**kw) -> tuple[RunResult, mp.FindResult | None]:
    """`m-flasher find`: units in ROM download mode (/dev/sgN) and running units on USB MIDI"""
    r = run_mflasher(["find"], **kw)
    return r, (mp.parse_find(r.out, r.err) if r.ok else None)


def info(device: str, **kw) -> tuple[RunResult, mp.Info | None]:
    """`m-flasher info --device /dev/sgN`: model, bootloader, chip key, flash ID and unit id of the unit in download
    mode. Reads the flash once (writes nothing); the unit stays in download mode afterwards."""
    r = run_mflasher(["info", "--device", device], **kw)
    if not r.ok:
        return r, None
    try:
        return r, mp.parse_info(r.out)
    except mp.ParseError:
        return r, None


def version(**kw) -> str:
    r = run_mflasher(["--version"], **kw)
    return r.out.strip() if r.ok else ""
