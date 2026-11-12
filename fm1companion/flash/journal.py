# SPDX-License-Identifier: GPL-3.0-only
"""Installation journal: what is being written right now – survives crashes, reboots and power loss.

DATA_DIR/flash/journal.json  {"active": {...} | null, "history": [...], "chain": {...} | null}
  "chain": a two-step switch (original V15 first, then the target) that has not reached its target yet –
  {"key", "name", "tag", "file", "device_id", "started"}; set before the V15 step, cleared once the target is written.
DATA_DIR/flash/packages/<sha256>.fwsc  copy of every package currently being installed (for the rescue)
DATA_DIR/flash/install.lock  PID of the process currently installing (one installation at a time)
"""
from __future__ import annotations

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
                try:
                    pid = int(self.lockfile.read_text().strip() or 0)
                except (OSError, ValueError):
                    pid = 0
                if pid and pid != os.getpid() and _alive(pid):
                    raise Busy(_("Another program window is installing a firmware right now."))
                self.lockfile.unlink(missing_ok=True)
                fd = os.open(self.lockfile, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
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
        dst = self.dir / "packages" / f"{sha}.fwsc"
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, dst.with_suffix(".part"))
            os.replace(dst.with_suffix(".part"), dst)
        return dst

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
        e.update(phase=phase, error=error, ended=time.time())
        d["history"] = (d["history"] + [dict(e)])[-200:]
        if not keep_active:
            d["active"] = None
        self._save(d)

    # ------------------------------------------------------------ two-step switch (V15, then the target)
    @property
    def chain(self) -> dict | None:
        c = self.load().get("chain")
        return c if isinstance(c, dict) and c.get("key") else None

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
        d = self.load()
        d["active"] = None
        self._save(d)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
