# SPDX-License-Identifier: GPL-3.0-only
"""Installation journal: what is being written right now – survives crashes, reboots and power loss.

DATA_DIR/flash/journal.json  {"active": {...} | null, "history": [...], "chain": {...} | null}
  "chain": a two-step switch (original V15 first, then the target) that has not reached its target yet –
  {"key", "name", "tag", "file", "device_id", "usb_path", "started"}; set before the V15 step, cleared once the
  target is written (also when Recovery finishes it), when the user dismisses it, and ignored after CHAIN_MAX_AGE.
  An entry that ended keeps where it stopped in "stopped_in" (its "phase" then says failed/safe/done).
DATA_DIR/flash/packages/<sha256>.fwsc  copy of the package currently being installed (for the rescue); removed
  once no active entry names it
DATA_DIR/flash/install.lock  PID (and start time) of the process currently installing (one installation at a time)
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path

from .. import config
from ..i18n import _

_LOCK = threading.Lock()
PHASES = ("prepared", "step1", "loader", "writing", "reboot", "resources", "done", "failed", "safe")
# from these phases on, the device may be waiting in update mode
STAGED = ("loader", "writing", "reboot")
CHAIN_MAX_AGE = 14 * 24 * 3600      # a two-step switch older than this is no longer offered ("Continue")


def root() -> Path:
    return config.DATA_DIR / "flash"


class Busy(Exception):
    pass


class Journal:
    def __init__(self, folder: Path | None = None):
        self.dir = Path(folder) if folder else root()
        self.file = self.dir / "journal.json"
        self.lockfile = self.dir / "install.lock"
        self._held = False

    # ------------------------------------------------------------ file
    def load(self) -> dict:
        try:
            d = json.loads(self.file.read_text("utf-8"))
            if isinstance(d, dict):
                d.setdefault("active", None)
                d.setdefault("history", [])
                d.setdefault("chain", None)
                return d
        except (OSError, ValueError):
            pass
        return {"active": None, "history": [], "chain": None}

    def _save(self, d: dict):
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, indent=1, ensure_ascii=False), "utf-8")
        os.replace(tmp, self.file)

    @property
    def active(self) -> dict | None:
        return self.load()["active"]

    def history(self, device: str = "") -> list[dict]:
        h = self.load()["history"]
        return [e for e in h if not device or e.get("device_id") == device]

    # ------------------------------------------------------------ lock
    def acquire(self):
        """one installation at a time – within the process and across processes"""
        if not _LOCK.acquire(blocking=False):
            raise Busy(_("An installation is already running."))
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            try:
                fd = os.open(self.lockfile, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                pid, born = _read_lock(self.lockfile)
                if pid and pid != os.getpid() and _alive(pid, born):
                    raise Busy(_("Another program window is installing a firmware right now."))
                self.lockfile.unlink(missing_ok=True)
                fd = os.open(self.lockfile, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"{os.getpid()} {_started(os.getpid())}".encode())
            os.close(fd)
            self._held = True
        except BaseException:
            _LOCK.release()
            raise

    def release(self):
        if self._held:
            self._held = False
            self.lockfile.unlink(missing_ok=True)
            _LOCK.release()

    @staticmethod
    def busy() -> bool:
        return _LOCK.locked()

    # ------------------------------------------------------------ entries
    def keep_package(self, path: Path, sha: str) -> Path:
        """copy of the package for the rescue; an existing copy is only trusted if its checksum still matches"""
        dst = self.dir / "packages" / f"{sha}.fwsc"
        if not dst.exists() or _sha256(dst) != sha.lower():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, dst.with_suffix(".part"))
            os.replace(dst.with_suffix(".part"), dst)
        return dst

    def _prune_packages(self, d: dict):
        """remove package copies no active entry names any more (they only exist for the rescue)"""
        keep = Path((d.get("active") or {}).get("package") or "").name
        try:
            for f in (self.dir / "packages").iterdir():
                if f.name != keep and f.suffix in (".fwsc", ".part"):
                    f.unlink(missing_ok=True)
        except OSError:
            pass

    def begin(self, **entry) -> dict:
        d = self.load()
        e = {"id": uuid.uuid4().hex[:10], "started": time.time(), "phase": "prepared", **entry}
        d["active"] = e
        self._save(d)
        return e

    def update(self, **fields):
        d = self.load()
        if d["active"]:
            d["active"].update(fields, updated=time.time())
            self._save(d)

    def finish(self, phase: str, error: str = "", keep_active: bool = False):
        """End: done/failed/safe. keep_active=True: device may be waiting in update mode -> entry stays active"""
        d = self.load()
        e = d["active"]
        if not e:
            return
        if e.get("phase") not in ("done", "failed", "safe"):
            e["stopped_in"] = e.get("phase", "")       # where it stopped (Recovery shows this, not "failed")
        e.update(phase=phase, error=error, ended=time.time())
        d["history"] = (d["history"] + [dict(e)])[-200:]
        if not keep_active:
            d["active"] = None
        self._save(d)
        if not keep_active:
            self._prune_packages(d)

    # ------------------------------------------------------------ two-step switch (V15, then the target)
    @property
    def chain(self) -> dict | None:
        """the pending two-step switch – None when there is none or it is older than CHAIN_MAX_AGE"""
        c = self.load().get("chain")
        if not (isinstance(c, dict) and c.get("key")):
            return None
        try:
            if time.time() - float(c.get("started") or 0) > CHAIN_MAX_AGE:
                return None
        except (TypeError, ValueError):
            return None
        return c

    def restore_chain(self, chain: dict | None):
        """put back what begin_chain replaced (the installation never started: another window is installing)"""
        d = self.load()
        d["chain"] = chain if isinstance(chain, dict) else None
        self._save(d)

    def begin_chain(self, **entry) -> dict:
        d = self.load()
        d["chain"] = {"started": time.time(), **entry}
        self._save(d)
        return d["chain"]

    def clear_chain(self):
        d = self.load()
        if d.get("chain") is not None:
            d["chain"] = None
            self._save(d)

    def clear_active(self):
        """forget the interrupted installation (the user's choice on the Recovery page) – nothing is sent"""
        d = self.load()
        d["active"] = None
        self._save(d)
        self._prune_packages(d)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for b in iter(lambda: f.read(1 << 16), b""):
                h.update(b)
    except OSError:
        return ""
    return h.hexdigest()


def _started(pid: int) -> str:
    """start time of a process (Linux: /proc/<pid>/stat, field 22) – "" where that is not available"""
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return ""


def _read_lock(lockfile: Path) -> tuple[int, str]:
    try:
        parts = lockfile.read_text().split()
        return int(parts[0]), (parts[1] if len(parts) > 1 else "")
    except (OSError, ValueError, IndexError):
        return 0, ""


def _alive(pid: int, born: str = "") -> bool:
    """is the process that wrote the lock still running? born: its start time from the lock – a PID that was
    reused by another program after a crash does not count"""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    now = _started(pid)
    return not (born and now and born != now)
