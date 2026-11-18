# SPDX-License-Identifier: GPL-3.0-only
"""Free sample packs from GitHub: browse, download fully or per folder, into the library.

Without the GitHub API (its rate limit is small): the content comes via "partial clone" – git only fetches
the folder structure (a few hundred KiB), audio files only when downloading and only for
the selected folders (sparse checkout). Only needs `git`.

Licences: every pack carries the licence that is actually stated in the repository. Where no clear
licence can be found, it says "licence unclear" – such packs are only meant for trying out,
not for publishing.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .config import CACHE_DIR
from .i18n import _

AUDIO = {".wav", ".wave", ".aif", ".aiff", ".flac", ".mp3", ".ogg"}


@dataclass
class Pack:
    id: str
    name: str
    repo: str                       # owner/name on GitHub
    licence: str                    # SPDX or "unklar" (unclear)
    description: str
    licence_note: str = ""          # where the licence is stated / why it is unclear
    tags: tuple[str, ...] = ()
    size_hint: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def url(self) -> str:
        return f"https://github.com/{self.repo}"

    @property
    def free(self) -> bool:
        return self.licence in FREE

    @property
    def tag(self) -> str:
        return "quelle:" + self.id


FREE = {"CC0-1.0": _("CC0 – public domain, free to use (also commercially, no attribution)"),
        "CC-BY-4.0": _("CC BY 4.0 – free to use with attribution"),
        "CC-BY-3.0": _("CC BY 3.0 – free to use with attribution")}

# Checked on 30.10.2026 (LICENSE file or README in the repository).
PACKS = [
    Pack("vcsl", "VCSL – Versilian Community Sample Library", "sgossner/VCSL", "CC0-1.0",
         _("About 4,000 samples of real instruments: drums (membranophones), cymbals, bells and percussion "
           "(idiophones), winds, strings, electronics. Ideal for your own drum kits."),
         _("LICENSE in the repository: CC0 1.0 Universal."), ("akustisch", "percussion", "instrumente"), _("≈ 1.3 GB")),
    Pack("vsco2ce", _("VSCO 2 Community Edition (orchestra)"), "sgossner/VSCO-2-CE", "CC0-1.0",
         _("Orchestra samples: strings, woodwinds, brass, timpani and orchestral percussion."),
         _("LICENSE in the repository: CC0 1.0 Universal."), ("orchester", "instrumente"), "≈ 1 GB"),
    Pack("dirt", "Dirt-Samples (TidalCycles)", "tidalcycles/Dirt-Samples", "unklar",
         _("The sound collection of TidalCycles/SuperDirt: bass drums, breaks, drum machine hits, voices, FX "
           "(about 2,000 short sounds)."),
         _("The repository has no LICENSE file and the README names no licence; the sounds come from "
           "many sources. For trying out only – not for publishing."), ("drums", "breaks", "fx"), "≈ 230 MB"),
    Pack("drummachines", "Tidal Drum Machines", "ritchse/tidal-drum-machines", "unklar",
         _("Samples of classic drum machines (TR-808, TR-909, LinnDrum …), sorted by device."),
         _("No licence information in the repository; recordings of commercial devices of unknown origin. "
           "For trying out only."), ("drums", "808", "909"), "≈ 300 MB"),
]


def licence_text(p: Pack) -> str:
    return FREE.get(p.licence, _("Licence unclear – for trying out only, do not publish"))


def pack(pid: str) -> Pack:
    return next(p for p in PACKS if p.id == pid)


# ------------------------------------------------------------ contents ---

@dataclass
class Entry:
    path: str
    size: int

    @property
    def folder(self) -> str:
        return self.path.rsplit("/", 1)[0] if "/" in self.path else ""


def parse_tree(text: str) -> list[Entry]:
    """Output of `git ls-tree -r HEAD` (with or without -l) -> audio files.
    (Without -l the sizes are missing, but git does not have to fetch a single audio file.)"""
    out = []
    for line in text.splitlines():
        meta, _sep, path = line.partition("\t")
        parts = meta.split()
        if len(parts) < 3 or parts[1] != "blob" or not path:
            continue
        if path.startswith('"'):                      # git quotes special characters
            try:
                path = path[1:-1].encode("latin-1").decode("unicode_escape").encode("latin-1").decode("utf-8")
            except (UnicodeError, ValueError):
                continue
        if parts[0] == "120000" or not safe_rel_path(path):   # symbolic link / path that leaves the pack folder
            continue
        if Path(path).suffix.lower() in AUDIO:
            out.append(Entry(path, int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0))
    return out


def folders(entries: list[Entry]) -> dict[str, tuple[int, int]]:
    """Folder -> (number of files, bytes), only folders that directly contain audio."""
    d: dict[str, list[int]] = {}
    for e in entries:
        c = d.setdefault(e.folder, [0, 0])
        c[0] += 1
        c[1] += e.size
    return {k: (v[0], v[1]) for k, v in sorted(d.items())}


def _cache(p: Pack) -> Path:
    return CACHE_DIR / "samplepacks" / p.id


def git_url(p: Pack) -> str:
    base = os.environ.get("FM1C_PACKS_GITBASE")          # tests: local repositories
    return f"{base.rstrip('/')}/{p.id}" if base else f"{p.url}.git"


class Cancelled(RuntimeError):
    pass


def git_error_text(line: str) -> str:
    """last git line -> understandable sentence."""
    low = line.lower()
    if "could not resolve host" in low or "name or service not known" in low or "temporary failure" in low:
        return _("No internet connection (GitHub not reachable).")
    if "timed out" in low or "operation too slow" in low:
        return _("Timeout – the connection to GitHub is too slow or was cut off.")
    if "repository not found" in low or "404" in low:
        return _("The pack no longer exists on GitHub – please update the app.")
    if "rate limit" in low or "403" in low or "429" in low:
        return _("GitHub is refusing right now (too many requests) – try again in a few minutes.")
    if "no space left" in low:
        return _("No free disk space left.")
    if "ssl" in low or "certificate" in low:
        return _("Secure connection not possible (certificate) – are the computer's date and time correct?")
    return "git: " + (line or _("unknown error"))


def _git(*args, cwd=None, timeout=600, cancel=None) -> str:
    """run git; cancel() is checked every 0.2 s and ends the operation (Cancelled)."""
    exe = shutil.which("git")
    if not exe:
        raise RuntimeError(_("Sample packs need git (sudo apt install git)"))
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
    if cancel is None:
        r = subprocess.run([exe, *args], cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
        out, err, rc = r.stdout, r.stderr, r.returncode
    else:
        proc = subprocess.Popen([exe, *args], cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        t0 = time.time()
        while True:
            try:
                out, err = proc.communicate(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                if cancel():
                    proc.kill()
                    proc.communicate()
                    raise Cancelled(_("cancelled"))
                if time.time() - t0 > timeout:
                    proc.kill()
                    proc.communicate()
                    raise RuntimeError(_("Timeout – the connection to GitHub is too slow or was cut off."))
        rc = proc.returncode
    if rc:
        msg = (err or out).strip().splitlines()
        raise RuntimeError(git_error_text(msg[-1] if msg else ""))
    return out


def _repo(p: Pack) -> Path:
    """partial clone (structure only) in the cache, created the first time."""
    d = _cache(p) / "repo"
    if not (d / ".git").exists():
        shutil.rmtree(d, ignore_errors=True)
        d.parent.mkdir(parents=True, exist_ok=True)
        _git("clone", "--quiet", "--filter=blob:none", "--no-checkout", "--depth", "1", git_url(p), str(d))
    return d


def index(p: Pack, refresh: bool = False, max_age: float = 7 * 86400) -> list[Entry]:
    """Contents of a pack (from the cache, at most one week old)."""
    fx = os.environ.get("FM1C_PACKS_FIXTURES")
    if fx and (Path(fx) / f"{p.id}.tree").exists():
        return parse_tree((Path(fx) / f"{p.id}.tree").read_text("utf-8"))
    cf = _cache(p) / "index.json"
    if not refresh and cf.exists() and time.time() - cf.stat().st_mtime < max_age:
        try:
            return [Entry(**e) for e in json.loads(cf.read_text("utf-8"))["files"]]
        except (ValueError, KeyError, TypeError):
            pass
    repo = _repo(p)
    if refresh:
        _git("fetch", "--quiet", "--filter=blob:none", "--depth", "1", "origin", cwd=repo)
        _git("reset", "--quiet", "--soft", "FETCH_HEAD", cwd=repo)
    entries = parse_tree(_git("ls-tree", "-r", "HEAD", cwd=repo))
    rev = _git("rev-parse", "HEAD", cwd=repo).strip()
    cf.write_text(json.dumps({"rev": rev, "files": [e.__dict__ for e in entries]}), "utf-8")
    return entries


def drop_checkout(p: Pack) -> bool:
    """Delete the git copy of a pack (work tree and the fetched audio in .git) – the samples are in the library
    by then. The contents list (index.json) stays; the next download fetches the structure again (a few hundred
    KiB) and then only the chosen folders. -> True if something was deleted."""
    d = _cache(p) / "repo"
    if not d.exists():
        return False
    shutil.rmtree(d, ignore_errors=True)
    return not d.exists()


def download(p: Pack, dest_root: Path, folder_list: list[str] | None = None,
             log: Callable[[str], None] = lambda s: None, cancel: Callable[[], bool] = lambda: False,
             keep_checkout: bool = False) -> list[Path]:
    """Download folders (or everything) and copy them to dest_root/<pack name>/…. -> new audio files.
    Afterwards (also after a cancel or an error) the git copy in the cache is deleted again (drop_checkout) –
    otherwise every pack would be on the disk twice. keep_checkout=True keeps it."""
    try:
        return _download(p, dest_root, folder_list, log, cancel)
    finally:
        if not keep_checkout:
            drop_checkout(p)


def _download(p: Pack, dest_root: Path, folder_list, log, cancel) -> list[Path]:
    repo = _repo(p)                                  # (cloned again after drop_checkout: structure only)
    entries = index(p)
    try:
        rev = json.loads((_cache(p) / "index.json").read_text("utf-8")).get("rev", "")
    except (OSError, ValueError, AttributeError):
        rev = ""
    if rev and rev != _git("rev-parse", "HEAD", cwd=repo).strip():
        entries = index(p, max_age=0)                # the pack changed on GitHub since the list was made
    want = [f for f in (folder_list or [])]
    pats = [f"/{f}/*" if f else "/*" for f in want] if want else ["/*"]
    _git("sparse-checkout", "set", "--no-cone", *pats, cwd=repo)
    sel = [e for e in entries if not want or e.folder in want]
    size = sum(e.size for e in sel)
    if size:
        log(_("loading {n} audio files ({mb} MB) … (a few minutes depending on size)").format(n=len(sel), mb=f"{size / 1e6:.0f}"))
    else:
        log(_("loading {n} audio files … (a few minutes depending on size)").format(n=len(sel)))
    _git("read-tree", "-mu", "HEAD", cwd=repo, timeout=3600, cancel=cancel)   # fetches only the selected files
    target = Path(dest_root) / folder_name(p)
    out = []
    for i, e in enumerate(sel):
        if cancel():
            raise Cancelled(_("cancelled"))
        if not safe_rel_path(e.path):                  # "..", absolute paths: never outside the pack folder
            continue
        src = repo / e.path
        if _has_symlink(repo, e.path) or not src.is_file():
            continue                                   # a link in the pack could point at any file of this computer
        dst = target / e.path
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists() or dst.stat().st_size != src.stat().st_size:
            shutil.copyfile(src, dst)
        out.append(dst)
        if i % 50 == 0:
            log(f"{i + 1}/{len(sel)} {Path(e.path).name}")
    write_source_note(p, target)
    return out


def safe_rel_path(path: str) -> bool:
    """relative path of a pack file: no empty parts, no ``.``/``..``, not absolute, no backslashes."""
    if not isinstance(path, str) or not path or "\\" in path or "\x00" in path:
        return False
    return all(part not in ("", ".", "..") for part in path.split("/"))


def _has_symlink(root: Path, rel: str) -> bool:
    """is the file or one of its parent folders (below root) a symbolic link?"""
    cur = Path(root)
    for part in rel.split("/"):
        cur = cur / part
        if cur.is_symlink():
            return True
    return False


def folder_name(p: Pack) -> str:
    return _safe_dir(p.name.split(" – ")[0].split(" (")[0])


def write_source_note(p: Pack, target: Path):
    target.mkdir(parents=True, exist_ok=True)
    (target / "QUELLE.txt").write_text(
        _("{name}\nSource: {url}\nLicence: {licence} – {licence_text}\n{note}\n"
          "Downloaded on {date} with FM-1 Companion.\n").format(
            name=p.name, url=p.url, licence=p.licence, licence_text=licence_text(p), note=p.licence_note,
            date=time.strftime('%d.%m.%Y')), "utf-8")


def note_for(p: Pack) -> str:
    return _("Source: {name} – {url} · Licence: {licence}").format(
        name=p.name, url=p.url, licence=p.licence if p.free else _("unclear"))


def _safe_dir(name: str) -> str:
    import re
    return re.sub(r"[^\w\-. ]+", "_", name).strip() or "Paket"
