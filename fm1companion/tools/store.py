# SPDX-License-Identifier: GPL-3.0-only
"""Where each tool lives and which version the app uses.

Two sources per tool:
* **bundled** – shipped with the app and pinned in manifest.json: M-Flasher in ``tools-bin/`` (placed by the .deb,
  the .run, install.sh or tools/fetch_tools.py), m-bootloader and SLOOP 3 in ``fm1companion/tools/bundled``;
* **update** – a newer release fetched with "Update" (tools/updates.py) into ``DATA_DIR/tools/<key>/<version>/``
  together with a ``meta.json`` that records the SHA-256 checked at download time.

Rules (owner decisions, 2026-11-19):
* the previous version stays as the fallback until the new one has run successfully once ("proven");
* a new M-Flasher is used for read-only commands only; writes only ever use a binary whose SHA-256 an app release
  lists in manifest.json ``write_verified`` (by bytes, not by version number);
* a new M-Flasher whose command line (``--help``) differs from the one this app knows is not used at all – the
  bundled one is used instead and the Tools view says so;
* the SHA-256 of a binary or file is checked before every use (``Candidate.verify``) – a changed file is never run.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import manifest as mf

META = "meta.json"


def _vkey(v: str):
    from ..firmware import version_key
    return version_key(v)


@dataclass
class Candidate:
    key: str
    version: str
    source: str                      # "bundled" | "update"
    path: Path | None                # the binary (M-Flasher) or the main file (m-bootloader, SLOOP 3)
    sha256: str                      # what the file must hash to
    size: int = 0
    proven: bool = True              # has run successfully once (bundled: always)
    failed: bool = False             # an update that did not work: the previous version is used again
    write_ok: bool = False           # may be used for writes (manifest.json write_verified)
    contract_known: bool = True      # its --help matches a command line this app release knows (bundled: by review)
    extra: dict = field(default_factory=dict)

    @property
    def present(self) -> bool:
        return self.path is not None and self.path.is_file()

    def verify(self) -> tuple[bool, str]:
        """(ok, problem in English for the log) – the file is there and has the pinned SHA-256"""
        if not self.present:
            return False, "missing"
        try:
            got = mf.sha256_file(self.path)
        except OSError as e:
            return False, f"unreadable: {e.strerror or e}"
        if got != self.sha256.lower():
            return False, f"SHA-256 {got[:12]}… is not the expected {self.sha256[:12]}…"
        return True, ""


class Store:
    def __init__(self, bin_dir: Path | None = None, updates_dir: Path | None = None, data: dict | None = None,
                 machine: str | None = None, bundled_dir: Path | None = None):
        self.bin_dir = Path(bin_dir) if bin_dir else mf.BIN_DIR
        if updates_dir is None:
            from .. import config
            updates_dir = config.DATA_DIR / "tools"
        self.updates_dir = Path(updates_dir)
        self.bundled_dir = Path(bundled_dir) if bundled_dir else mf.BUNDLED
        self.data = data or mf.load()
        self.arch = mf.arch(machine)
        import platform
        self.machine = machine if machine is not None else platform.machine()

    # ------------------------------------------------------------ state (proven / failed per update)
    @property
    def state_file(self) -> Path:
        return self.updates_dir / "state.json"

    def state(self) -> dict:
        try:
            d = json.loads(self.state_file.read_text("utf-8"))
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_state(self, d: dict):
        self.updates_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, indent=1), "utf-8")
        os.replace(tmp, self.state_file)

    def mark(self, key: str, version: str, *, ok: bool | None = None, failed: bool | None = None):
        """record that an updated version ran successfully (ok) or did not work (failed)"""
        d = self.state()
        e = d.setdefault(key, {}).setdefault(version, {})
        if ok:
            e.update(proven=True, failed=False, proven_at=time.time())
        if failed:
            e.update(failed=True, failed_at=time.time())
        self._save_state(d)

    # ------------------------------------------------------------ candidates
    def bundled(self, key: str) -> Candidate | None:
        t = mf.tool(key, self.data)
        ver = t["version"]
        if key == "m-flasher":
            if not self.arch:
                return None
            a = t["arch"].get(self.arch)
            if not a:
                return None
            return Candidate(key, ver, "bundled", self.bin_dir / mf.mflasher_relpath(ver, self.arch),
                             a["binary_sha256"], a["binary_size"],
                             write_ok=mf.write_verified(key, ver, a["binary_sha256"], self.arch, self.data))
        name, e = self._main_file(t)
        return Candidate(key, ver, "bundled", self.bundled_dir / key / name, e["sha256"], e["size"],
                         write_ok=mf.write_verified(key, ver, e["sha256"], data=self.data))

    @staticmethod
    def _main_file(t: dict) -> tuple[str, dict]:
        files = {k: v for k, v in (t.get("files") or {}).items() if k != "SHA256SUMS"}
        name = sorted(files)[0]
        return name, files[name]

    def updates(self, key: str) -> list[Candidate]:
        out = []
        st = self.state().get(key, {})
        base = self.updates_dir / key
        try:
            dirs = [d for d in base.iterdir() if d.is_dir() and not d.name.startswith(".")]
        except OSError:
            return []
        for d in dirs:
            try:
                meta = json.loads((d / META).read_text("utf-8"))
            except (OSError, ValueError):
                continue
            ver = str(meta.get("version") or "")
            if ver != d.name or not mf.safe_component(ver):
                continue
            files = meta.get("files") or {}
            if key == "m-flasher":
                rel = f"{self.arch}/m-flasher" if self.arch else ""
                e = files.get(rel)
            else:
                rel = str(meta.get("main") or "")
                e = files.get(rel)
            if not rel or not mf.safe_relpath(rel) or not isinstance(e, dict) or not e.get("sha256"):
                continue                     # (a path from meta.json must stay inside this version's folder)
            s = st.get(ver, {})
            out.append(Candidate(key, ver, "update", d / rel, str(e["sha256"]), int(e.get("size") or 0),
                                 proven=bool(s.get("proven")), failed=bool(s.get("failed")),
                                 write_ok=mf.write_verified(key, ver, str(e["sha256"]), self.arch, self.data),
                                 contract_known=(meta.get("contract_known") is True) if key == "m-flasher" else True,
                                 extra=meta))
        return out

    def candidates(self, key: str) -> list[Candidate]:
        """every version on this computer, newest first (a bundled and an updated copy of the same version: the
        bundled one first)"""
        out = list(self.updates(key))
        b = self.bundled(key)
        if b is not None:
            out.append(b)
        out.sort(key=lambda c: (_vkey(c.version), c.source == "bundled"), reverse=True)
        return out

    def select(self, key: str, write: bool = False) -> tuple[Candidate | None, list[str]]:
        """the version to use and the problems met on the way (English, for the log).
        read-only: the newest version that did not fail and whose file checks out – a new, not yet proven update
        is tried (its run proves it); write: only a version an app release verified for writing, and only once it
        has run successfully."""
        problems = []
        for c in self.candidates(key):
            if c.failed:
                problems.append(f"{c.version} ({c.source}): did not work before – skipped")
                continue
            if not c.contract_known:
                problems.append(f"{c.version} ({c.source}): its command line is not the one this app knows – not used")
                continue
            if write and not (c.write_ok and c.proven):
                problems.append(f"{c.version} ({c.source}): not verified for writing by this app version")
                continue
            ok, why = c.verify()
            if not ok:
                problems.append(f"{c.version} ({c.source}): {why}")
                continue
            return c, problems
        return None, problems

    def unknown_contract(self, key: str) -> list[str]:
        """versions on this computer that are not used because their command line is new to this app"""
        return [c.version for c in self.updates(key) if not c.contract_known and not c.failed]
