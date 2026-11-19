# SPDX-License-Identifier: GPL-3.0-only
"""Starting and stopping a tool process so that it never outlives the app.

* Linux: PR_SET_PDEATHSIG(SIGTERM) in the child – if the app dies (crash, kill), the kernel sends M-Flasher SIGTERM.
  The child also exits at once if the app is already gone between fork and exec.
* Its own process group (start_new_session): stopping kills the whole group, SIGTERM first, then SIGKILL; every wait
  is bounded. A process that does not stop even then is reported (it may hang in the kernel, inside a USB call).
* Every live child is remembered and stopped when the app exits normally (atexit).
* tools/runner.py writes the child's PID into the lock folder (journal.TOOL_PID) while it runs, so another program
  window never takes a lock whose owner died while its M-Flasher is still alive.
* The one exception: a WRITING run (`restore`, `survive=True`) gets no death signal and is not stopped at exit. Once
  it has been answered "yes" it may be erasing the flash; killing it then is the worst outcome, so it must outlive
  even a crashed app. Before "yes" the app's death is safe anyway: its stdin is a pipe from the app, M-Flasher reads
  EOF and writes nothing. Its output goes to files, not pipes (a print to a pipe without a reader would abort it).
"""
from __future__ import annotations

import atexit
import ctypes
import ctypes.util
import os
import signal
import subprocess
import threading

PR_SET_PDEATHSIG = 1
_LIVE: set[subprocess.Popen] = set()
_LIVE_LOCK = threading.Lock()
_LIBC = None


def _libc():
    global _LIBC
    if _LIBC is None:
        try:
            _LIBC = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
        except OSError:
            _LIBC = False
    return _LIBC or None


def _preexec(parent: int):
    def fn():
        libc = _libc()
        if libc is not None:
            try:
                libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM, 0, 0, 0)
            except (AttributeError, OSError):
                pass
        if os.getppid() != parent:          # the app died between fork and prctl
            os._exit(1)
    return fn


def popen(argv: list[str], survive: bool = False, **kw) -> subprocess.Popen:
    """subprocess.Popen in its own session, with the death signal set; remembered until it has exited.
    survive: no death signal, not stopped at the app's exit (a writing run, see above)"""
    _libc()                                  # (load libc before fork, not in the child)
    kw.setdefault("start_new_session", True)
    if os.name == "posix" and not survive:
        kw["preexec_fn"] = _preexec(os.getpid())
    p = subprocess.Popen(argv, **kw)
    if not survive:
        with _LIVE_LOCK:
            _LIVE.add(p)
    return p


def forget(p: subprocess.Popen):
    with _LIVE_LOCK:
        _LIVE.discard(p)


def _signal_group(p: subprocess.Popen, sig) -> None:
    try:
        os.killpg(p.pid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            p.send_signal(sig)
        except (ProcessLookupError, OSError):
            pass


def stop(p: subprocess.Popen, grace: float = 2.0, final: float = 5.0) -> bool:
    """SIGTERM to the group, after `grace` s SIGKILL, then wait at most `final` s. -> True when it has exited"""
    if p.poll() is not None:
        forget(p)
        return True
    _signal_group(p, signal.SIGTERM)
    try:
        p.wait(grace)
    except subprocess.TimeoutExpired:
        _signal_group(p, signal.SIGKILL)
        try:
            p.wait(final)
        except subprocess.TimeoutExpired:
            return False
    forget(p)
    return True


def stop_all() -> None:
    with _LIVE_LOCK:
        live = list(_LIVE)
    for p in live:
        stop(p, 1.0, 2.0)


atexit.register(stop_all)


def run_probe(argv: list[str], cwd: str, timeout: float = 10.0) -> tuple[int | None, bytes]:
    """run a just downloaded binary with --version / --help only: no stdin, its staging folder as working and home
    folder, a minimal environment, its own process group, bounded. -> (exit code or None, stdout)"""
    env = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C", "HOME": cwd}
    try:
        p = popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=cwd, env=env)
    except OSError:
        return None, b""
    try:
        out, _err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        stop(p)
        return None, b""
    forget(p)
    return p.returncode, out
