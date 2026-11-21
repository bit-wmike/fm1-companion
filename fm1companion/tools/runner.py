# SPDX-License-Identifier: GPL-3.0-only
"""Runs the bundled M-Flasher as a separate program.

* The binary comes from tools/store.py (bundled or updated) and its SHA-256 is checked right before every run.
* No shell; a fixed argument list (checked command by command below); LANG=C so its texts are always English.
* Never `--yes`, never `--restart`, `--board`, `--keep-bootloader`, the bundled-bootloader form of
  `bootloader` (without --file/--stock) or `run-ram`.
* Reading commands (find, info, backup) and the two that only change the unit's mode (download-mode, restart) get no
  stdin: a command that would ask "Type "yes" to write" reads EOF and writes nothing.
* The writing commands of this version, `restore`, `bootloader --file X.boot` / `bootloader --stock X.fwsc` and
  `firmware X.fwsc` (writes from 0x4000 to the package's end, never the boot area), run
  only with a *gate*: M-Flasher prints the unit id, the
  flash ID and the planned sector ranges of this very session and asks; the gate (tools/flashjob.py) compares them
  with what the app planned and the runner sends "yes" only when the gate says so – otherwise it closes stdin, and
  M-Flasher writes nothing. Only a binary whose bytes this app release lists in manifest.json write_verified is
  used for it (Store.select(write=True)).
* A time limit per command and a cancel event: the process is asked to stop (SIGTERM), then killed after 2 s – but
  once "yes" was sent there is no cancel and no kill any more (an erase may be running): the runner waits.
* Commands that talk to a unit take the one-installation-at-a-time lock (flash/journal.py): never while an
  installation, a recovery or another tool run owns the device.
* Every line goes into a flight log ("tool-<time>.txt" next to the installation logs) with unit ids masked.
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

TIMEOUTS = {"find": 10.0, "info": 60.0, "backup": 60.0, "download-mode": 40.0, "restart": 90.0,
            "restore": 90.0,                  # until its question (the session reads the whole flash first)
            "bootloader": 90.0,               # (the same)
            "firmware": 90.0,                 # (the same)
            "--version": 10.0, "--help": 10.0}
WRITE_SOFT_LIMIT = 600.0                      # after "yes": past this the log says "still writing" – no kill
TALKS_TO_UNIT = ("find", "info", "backup", "download-mode", "restart", "restore", "bootloader",
                 "firmware")                  # take the lock
WRITES = ("restore", "bootloader", "firmware")
_DEVICE_RE = re.compile(r"/dev/sg\d{1,3}")
# a unit id: 16 hex digits, also with 0x, or as 8 bytes separated by spaces, colons or dashes; not inside a longer
# hex run (SHA-256 values stay readable) – underscores and dots around it do not protect it
_ID_RE = re.compile(r"(?<![0-9A-Fa-f])(?:0[xX])?(?:[0-9A-Fa-f]{16}|(?:[0-9A-Fa-f]{2}[ :-]){7}[0-9A-Fa-f]{2})"
                    r"(?![0-9A-Fa-f])")


def mask_ids(text: str) -> str:
    """unit ids shortened to their last four hex digits, like USB serial numbers in the flight log"""
    return _ID_RE.sub(lambda m: "…" + re.sub(r"[^0-9A-Fa-f]", "", m.group(0)[-5:])[-4:], text)


class ToolError(Exception):
    """a user-facing reason (translated) why the tool did not run"""


class ToolUnavailable(ToolError):
    pass


class ToolBusy(ToolError):
    pass


class ToolStuck(ToolError):
    """M-Flasher did not stop even after SIGKILL: the lock stays held until it has ended (released by a reaper)"""


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
    stuck: bool = False             # did not stop even after SIGKILL (hangs in the kernel): the lock stays held
    seconds: float = 0.0
    log_path: Path | None = None
    problems: list[str] = field(default_factory=list)
    prompted: bool = False          # M-Flasher asked 'Type "yes" to write'
    answered_yes: bool = False      # … and the gate let the runner send "yes"
    gate_reason: str = ""           # why the gate closed stdin instead (English, for the log)
    binary_sha256: str = ""
    out_files: tuple | None = None  # a writing run: (stdout file, stderr file) in its private folder

    @property
    def ok(self) -> bool:
        return self.rc == 0 and not self.timed_out and not self.cancelled


def unavailable_reason(store: Store) -> str:
    """why there is no M-Flasher to run on this computer ("" when there is one)"""
    if not store.arch:
        return _("M-Flasher is not available for this computer ({machine}): it is built for 64-bit ARM (Raspberry "
                 "Pi OS 64-bit) and for x86-64 PCs only. On a Raspberry Pi with the 32-bit system the recovery tools "
                 "on this page still work.").format(machine=store.machine or "?")
    c, _problems = store.select("m-flasher")
    if c is None:
        b = store.bundled("m-flasher")
        if b is not None and not b.present:
            return _("M-Flasher is not bundled with this installation (a source checkout?). Run "
                     "tools/fetch_tools.py once, or install the app from its .deb or .run package.")
        return _("The bundled M-Flasher did not pass its checksum test, so the app does not run it. Reinstall "
                 "the app.")
    return ""


def _plain_path(v: str) -> bool:
    """an absolute path without control characters, not an option"""
    return bool(v) and v.startswith("/") and not any(ord(c) < 32 for c in v) and ".." not in Path(v).parts


def _check_args(args: list[str]) -> None:
    """exactly these forms, nothing else:
    find · download-mode · --version · --help · info --device /dev/sgN · restart --device /dev/sgN ·
    backup --device /dev/sgN --backup-dir DIR · restore FILE.bin --device /dev/sgN --backup-dir DIR ·
    bootloader --file FILE.boot --device /dev/sgN --backup-dir DIR ·
    bootloader --stock FILE.fwsc --device /dev/sgN --backup-dir DIR ·
    firmware FILE.fwsc --device /dev/sgN --backup-dir DIR"""
    def no():
        raise ToolError(_("This M-Flasher command is not available in the app."))
    if not args or args[0] not in TIMEOUTS:
        no()
    cmd = args[0]
    if cmd in ("info", "restart"):
        if len(args) != 3 or args[1] != "--device" or not _DEVICE_RE.fullmatch(args[2]):
            no()
    elif cmd == "backup":
        if len(args) != 5 or args[1] != "--device" or not _DEVICE_RE.fullmatch(args[2]) or \
                args[3] != "--backup-dir" or not _plain_path(args[4]):
            no()
    elif cmd == "restore":
        if len(args) != 6 or not _plain_path(args[1]) or not args[1].endswith(".bin") or args[2] != "--device" or \
                not _DEVICE_RE.fullmatch(args[3]) or args[4] != "--backup-dir" or not _plain_path(args[5]):
            no()
    elif cmd == "firmware":
        if len(args) != 6 or not _plain_path(args[1]) or not args[1].endswith(".fwsc") or args[2] != "--device" or \
                not _DEVICE_RE.fullmatch(args[3]) or args[4] != "--backup-dir" or not _plain_path(args[5]):
            no()
    elif cmd == "bootloader":
        ext = {"--file": ".boot", "--stock": ".fwsc"}.get(args[1] if len(args) > 1 else "")
        if len(args) != 7 or ext is None or not _plain_path(args[2]) or not args[2].endswith(ext) or \
                args[3] != "--device" or not _DEVICE_RE.fullmatch(args[4]) or args[5] != "--backup-dir" or \
                not _plain_path(args[6]):
            no()
    elif len(args) != 1:
        no()


def _flight(folder: Path | None):
    from ..flash import robust
    f = robust.FlightLog(folder)
    f.prefix = "tool"
    return f


def run_mflasher(args: list[str], *, store: Store | None = None, cancel: threading.Event | None = None,
                 timeout: float | None = None, log_dir: Path | None = None, journal=None,
                 on_line=None, gate=None, flight=None, out_dir: Path | None = None) -> RunResult:
    """run one M-Flasher command; raises ToolError when it cannot run at all.
    gate (restore, bootloader and firmware only, required there): fn(stdout so far) -> "" to send "yes", or the reason (English) to close
    stdin instead – called once, when M-Flasher asks.
    flight: a job's flight log (tools/flashjob.py) to write into instead of this run's own "tool-…" log.
    out_dir (restore, bootloader and firmware only, required there): a private folder for M-Flasher's stdout/stderr files – a writing run
    writes into files, not pipes, and outlives the app (tools/proc.py)."""
    _check_args(args)
    writes = args[0] in WRITES
    if writes and (gate is None or out_dir is None):
        raise ToolError(_("This M-Flasher command is not available in the app."))
    store = store or Store()
    why = unavailable_reason(store)
    if why:
        raise ToolUnavailable(why)
    cand, problems = store.select("m-flasher", write=writes)
    if cand is None and writes:
        raise ToolUnavailable(_("No M-Flasher on this computer is cleared for writing by this app version (only the "
                                "exact build this app was reviewed with is). Reinstall the app; reading still works."))
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
    own_log = flight is None
    flight = _flight(log_dir) if own_log else flight
    res = None
    try:
        res = _run(cand, args, timeout if timeout is not None else TIMEOUTS[args[0]], cancel, flight, on_line, lock,
                   gate if writes else None, Path(out_dir) if writes else None)
        res.problems = problems
        res.binary_sha256 = cand.sha256
    finally:
        if lock is not None and not (res is not None and res.stuck):
            lock.release()
    if res.stuck:
        raise ToolStuck(_("M-Flasher does not stop. Unplug the FM-1 (or switch it off); the app starts no other "
                          "installation or tool until M-Flasher has ended."))
    _judge(store, cand, res)
    outcome = "ok" if res.ok else ("cancelled" if res.cancelled else ("timeout" if res.timed_out else f"exit {res.rc}"))
    header = [f"tool: M-Flasher {cand.version} ({cand.source}), command: {' '.join(args)}",
              *(f"note: {p}" for p in problems)]
    if own_log:
        res.log_path = flight.finish(outcome, header=header) if args[0] in TALKS_TO_UNIT else None
    else:
        flight.ev("tool", header[0] + f" – {outcome}")
    log.info("M-Flasher %s (%s) %s: %s in %.1f s", cand.version, cand.source, args[0], outcome, res.seconds)
    return res


def _protected(path: Path) -> bool:
    """can nobody but root change the file between our hash and the exec? (not writable for this user, the file and
    its folder not writable for group or others) – e.g. /usr/lib/fm1-companion/tools-bin of the .deb"""
    try:
        for q in (path, path.parent):
            if os.access(q, os.W_OK) or q.stat().st_mode & 0o022:
                return False
    except OSError:
        return False
    return True


def _exec_path(cand: Candidate, flight) -> tuple[Path, str | None]:
    """(file to execute, private temp folder to remove afterwards). Hash and exec must see the same bytes: a binary
    in a folder this user (or anyone) can write is copied once into a private 0700 folder, the COPY is hashed and
    run; a root-owned one is hashed in place."""
    import shutil
    import tempfile
    def refuse(why):
        flight.ev("refused", f"M-Flasher {cand.version}: {why}")
        raise ToolUnavailable(_("The bundled M-Flasher did not pass its checksum test, so the app does not run it. "
                                "Reinstall the app."))
    if _protected(cand.path):
        ok, why = cand.verify()
        if not ok:
            refuse(why)
        return cand.path, None
    tmp = tempfile.mkdtemp(prefix="fm1c-tool-")
    os.chmod(tmp, 0o700)
    try:
        data = cand.path.read_bytes()
        import hashlib
        if hashlib.sha256(data).hexdigest() != cand.sha256.lower():
            refuse("SHA-256 differs from the expected value")
        exe = Path(tmp) / "m-flasher"
        fd = os.open(exe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return exe, tmp


def _run(cand: Candidate, args: list[str], timeout: float, cancel, flight, on_line, lock=None,
         gate=None, out_dir: Path | None = None) -> RunResult:
    import shutil
    from . import proc
    try:
        exe, tmp = _exec_path(cand, flight)            # right before the start: never run a changed binary
    except OSError as e:
        flight.ev("refused", f"M-Flasher {cand.version}: {e.strerror or e}")
        raise ToolUnavailable(_("The bundled M-Flasher did not pass its checksum test, so the app does not run it. "
                                "Reinstall the app.")) from e
    env = dict(os.environ, LANG="C", LC_ALL="C")
    env.pop("LANGUAGE", None)
    flight.ev("exec", f"m-flasher {' '.join(args)} (version {cand.version}, {cand.source}, sha256 {cand.sha256[:12]}…)")
    t0 = time.monotonic()
    files = None
    try:
        if gate is not None:                   # a writing run: output into files, no death signal (tools/proc.py)
            out_dir.mkdir(parents=True, exist_ok=True)
            files = (out_dir / "m-flasher.out", out_dir / "m-flasher.err")
            fo, fe = (open(f, "wb") for f in files)
            try:
                p = proc.popen([str(exe), *args], survive=True, stdin=subprocess.PIPE, stdout=fo, stderr=fe,
                               env=env, cwd=str(Path.home()))
            finally:
                fo.close()
                fe.close()
        else:
            p = proc.popen([str(exe), *args], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, env=env, cwd=str(Path.home()))
    except OSError as e:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
        flight.ev("error", f"could not start: {e.strerror or e}")
        return RunResult(cand.version, cand.source, list(args), None, err=str(e.strerror or e))
    if lock is not None and hasattr(lock, "set_tool"):
        lock.set_tool(p.pid)
    chunks: dict[str, list[str]] = {"out": [], "err": []}
    answer = {"asked": False, "yes": False, "why": "", "at": 0.0, "stopping": False}
    decide = threading.Lock()                  # "yes" and "stop the process" exclude each other

    def ask():
        """M-Flasher waits for "yes": the gate decides on what this session printed. Only its "" sends "yes"."""
        answer["asked"] = True
        so_far = "".join(chunks["out"])
        try:
            why = gate(so_far)
        except Exception as e:  # noqa: BLE001 – a broken check never answers "yes"
            why = f"the check failed: {type(e).__name__}"
        why = why if isinstance(why, str) else "the check gave no answer"
        with decide:
            if why == "" and (answer["stopping"] or (cancel is not None and cancel.is_set())):
                why = "cancelled before the write"
            _answer(why)

    def _answer(why):
        try:
            if why == "":
                p.stdin.write(b"yes\n")
                p.stdin.flush()
                answer["yes"], answer["at"] = True, time.monotonic()
                flight.ev("gate", "checked: unit id, flash ID and planned ranges match – answered yes")
            else:
                answer["why"] = why
                flight.ev("gate", f"refused: {mask_ids(why)} – stdin closed, nothing is written")
        except OSError as e:
            answer["yes"] = False
            answer["why"] = answer["why"] or f"stdin: {e.strerror or e}"
        finally:
            try:
                p.stdin.close()
            except OSError:
                pass

    def tail(path: Path):
        """the next bytes M-Flasher wrote into its output file (b"" once it has ended and all is read)"""
        f = open(path, "rb")

        def nxt() -> bytes:
            while True:
                b = f.read(4096)
                if b:
                    return b
                if p.poll() is not None:
                    b = f.read(4096)
                    if not b:
                        f.close()
                    return b
                time.sleep(0.03)
        return nxt

    def reader(stream, kind):
        import codecs
        dec = codecs.getincrementaldecoder("utf-8")("replace")      # (a character split across two reads)
        buf = ""
        read = stream if callable(stream) else (lambda: stream.read1(4096) if hasattr(stream, "read1")
                                                 else stream.read(4096))
        for raw in iter(read, b""):
            text = dec.decode(raw)
            chunks[kind].append(text)
            buf += text
            if gate is not None and kind == "out" and not answer["asked"] and buf.endswith(mp.PROMPT):
                flight.ev(kind, buf)
                buf = ""
                ask()
                continue
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
    srcs = (tail(files[0]), tail(files[1])) if files else (p.stdout, p.stderr)
    threads = [threading.Thread(target=reader, args=(srcs[0], "out"), daemon=True),
               threading.Thread(target=reader, args=(srcs[1], "err"), daemon=True)]
    for t in threads:
        t.start()
    timed_out = cancelled = False
    warned = False
    while True:
        try:
            p.wait(0.1)
            break
        except subprocess.TimeoutExpired:
            pass
        if answer["yes"]:
            # writing: no cancel, no kill – an interrupted erase is worse than waiting
            if not warned and time.monotonic() - answer["at"] > WRITE_SOFT_LIMIT:
                warned = True
                flight.ev("wait", f"still writing after {WRITE_SOFT_LIMIT:.0f} s – not stopped (do not unplug)")
                if on_line:
                    try:
                        on_line("note", _("M-Flasher is still writing after {s} minutes. It is not stopped – do not "
                                          "unplug the FM-1 or switch it off.").format(s=int(WRITE_SOFT_LIMIT // 60)))
                    except Exception:  # noqa: BLE001
                        pass
            continue
        if cancel is not None and cancel.is_set():
            cancelled = True
        elif time.monotonic() - t0 > timeout:
            timed_out = True
        if cancelled or timed_out:
            with decide:
                if answer["yes"]:                  # (the gate answered in this very moment: writing now)
                    cancelled = timed_out = False
                    continue
                answer["stopping"] = True
            flight.ev("stop", "cancelled" if cancelled else f"no answer within {timeout:.0f} s")
            break
    stuck = False
    if cancelled or timed_out:
        stuck = not proc.stop(p)                   # SIGTERM to its group, SIGKILL after 2 s, wait bounded
    else:
        proc.forget(p)
    for t in threads:
        t.join(2.0)
    if p.stdin is not None:
        try:
            p.stdin.close()
        except OSError:
            pass
    seconds = time.monotonic() - t0
    if stuck:
        flight.ev("stuck", "M-Flasher did not stop after SIGKILL – the lock stays held until it ends")
        _reap_later(p, lock, tmp)
    else:
        if lock is not None and hasattr(lock, "clear_tool"):
            lock.clear_tool()
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
    flight.ev("exit", f"{p.returncode} after {seconds:.1f}s")
    return RunResult(cand.version, cand.source, list(args), p.returncode, "".join(chunks["out"]),
                     "".join(chunks["err"]), timed_out, cancelled, stuck=stuck, seconds=seconds,
                     prompted=answer["asked"], answered_yes=answer["yes"], gate_reason=answer["why"],
                     out_files=files)


def _reap_later(p, lock, tmp):
    """a process that hangs in the kernel: keep the lock (no second job meanwhile) until it has really ended"""
    import shutil

    def wait():
        p.wait()
        from . import proc
        proc.forget(p)
        if lock is not None:
            if hasattr(lock, "clear_tool"):
                lock.clear_tool()
            lock.release()
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
    threading.Thread(target=wait, daemon=True, name="m-flasher-reaper").start()


def _judge(store: Store, cand: Candidate, res: RunResult):
    """an update proves itself with a clean `info` run; it counts as broken when it crashes, cannot start or its
    output no longer has the known form – then the previous version is used again"""
    if cand.source != "update" or res.cancelled or res.args[0] in WRITES:
        return                     # (a writing run ended by the outside – killed, unplugged – says nothing about it)
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
