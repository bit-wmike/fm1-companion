# SPDX-License-Identifier: GPL-3.0-only
"""Firmware catalog, releases, downloads and installation.

Every firmware of the Felucca family ships its own tools in its git repo
(tools/fm1_install.py, for Melodee also tools/fm1_resources.py). The app
always uses the tools of the *target* firmware in exactly the installed
version – that way the installer is guaranteed to match the package (that was the trap with the
INIT sounds in Melodee 1.6).
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .config import CACHE_DIR, DATA_DIR, load as load_settings
from .i18n import N_, _

UA = "fm1-companion"
LAST_ERROR: dict[str, str] = {}          # key -> why the release list stayed empty (for the UI)
REPOS_DIR = DATA_DIR / "repos"
IDENT_FILE = DATA_DIR / "identities.json"


@dataclass
class FirmwareSpec:
    key: str
    name: str
    repo: str                       # "owner/name" on GitHub
    asset: str                      # package name, {ver} = version without "v"
    resources: str = ""             # resource file (Melodee 1.6+), empty = none
    sums: str = "SHA256SUMS"
    installer: str = "tools/fm1_install.py"
    resource_tool: str = ""
    editor: str = "web/editor.html"
    description: str = ""
    samples: str = ""               # note about sample slots
    homepage: str = ""
    manual_online: str = ""         # online manual (source for images that are not in the repo)
    builtin: bool = True
    # ---- Suite 2.0: details from the firmware catalog / link import ----
    protocol: str = "felucca"       # "felucca" = app can do backup/projects/editor bridge; "none" = install only
    family: str = ""                # catalog family (e.g. "sloop", "orbit", "stock")
    product: str = ""               # package identity, e.g. "FM-1_900"
    author: str = ""
    license: str = ""
    editor_url: str = ""            # online editor (Web MIDI), if there is no editor in the repo
    emulator_url: str = ""          # browser emulator to try it out
    manual_url: str = ""            # manual online
    installer_url: str = ""         # the author's web installer page
    download_url: str = ""          # fixed package address (instead of a GitHub release), e.g. stock firmware
    sha256: str = ""                # expected checksum of the package (from the catalog)
    requires_stock_v15: bool = False   # only installable from stock V15 (catalog)
    notes: list[str] = field(default_factory=list)   # catalog notes, e.g. "beta", "powerLossNotResumable"
    category: str = ""              # Synth, Groovebox, Sampler …
    catalog_id: str = ""            # key in the firmware catalog
    loader_family: str = ""         # loader family according to the catalog: stock | feluccaG1 | feluccaG2 | sloopG3
    release_tag: str = ""           # fixed version for download_url packages (e.g. "V15")

    @property
    def url(self) -> str:
        return self.homepage or f"https://github.com/{self.repo}"


BUILTIN = [
    FirmwareSpec("melodee", "Melodee", "keremimo/melodee", "melodee-{ver}.fwsc",
                 manual_online="https://keremimo.github.io/melodee/webapp/manual/",
                 resources="melodee-{ver}-res.bin", resource_tool="tools/fm1_resources.py",
                 description=_("Multi-engine synth and sequencer: 4 synth tracks + 4 MIDI tracks, 32 projects, "
                               "song mode with 64 sections."),
                 samples=_("no own samples (removed since 1.5)"),
                 family="melodee", author="Kerem Kiliç (Ellic Studio)", license="GPL-3.0", category="Synth",
                 installer_url="https://keremimo.github.io/melodee/"),
    FirmwareSpec("skiff", "Skiff", "illuminus5/skiff", "skiff-{ver}.fwsc",
                 description=_("Six-track groovebox: 14 engines, pattern pool, songs, performance layer."),
                 samples=_("3 sample slots (USR1–3)"),
                 family="skiff", author="illuminus5", license="GPL-3.0", category="Groovebox"),
    FirmwareSpec("sloop", "SLOOP", "isod89/sloop-fm1", "sloop-{ver}.fwsc",
                 description=_("Live groovebox: 3 synths + drums, looping, punch-in effects, 4 song sections."),
                 samples=_("4 sample slots (USR1–4), also as a drum kit"),
                 family="sloop", author="isod89", license="GPL-3.0", category="Groovebox"),
]


# Stock firmware from M-VAVE (way back). V16 lives at an address whose content M-VAVE replaces:
# the checksum is always compared; if it differs, nothing is installed.
STOCK = [
    FirmwareSpec("stock-v15", _("Original firmware V15"), "m-vave/fm1_open", "FM-1_015.fwsc", sums="", installer="",
                 editor="", description=_("M-VAVE's original firmware V15 – the proven way back and starting point for "
                                          "almost all community firmwares."), samples=_("8 sample slots (original)"),
                 protocol="none", family="stock",
                 product="FM-1_015", author="M-VAVE", license="Apache-2.0", category="Original",
                 download_url="https://github.com/m-vave/fm1_open/raw/b178ac43c25f010c60a878056fb5331fe693fd62/"
                              "sdk/firmware/FM-1_015.fwsc",
                 sha256="db1642b2b6fa5c2cccb11ffd13878068bb28601678d3644049f99dc40e7edb8a", loader_family="stock",
                 release_tag="V15", homepage="https://github.com/m-vave/fm1_open"),
    FirmwareSpec("stock-v16", _("Original firmware V16"), "m-vave/fm1_open", "FM-1.fwsc", sums="", installer="",
                 editor="", description=_("M-VAVE's current firmware V16 (October 2026): eight voices, 20 notes "
                                          "polyphony, 64-step patterns."), protocol="none", family="stock", product="FM-1_016",
                 author="M-VAVE", license="Apache-2.0", category="Original",
                 download_url="https://yms-file-store.oss-cn-hongkong.aliyuncs.com/software/firmware/FM-1.fwsc",
                 sha256="d017fb65e70aca97ee29d944470c3515a74581df5c0576bf70de917c7b2b61d5", loader_family="stock",
                 release_tag="V16", homepage="https://www.m-vave.com/download"),
]


def catalog() -> list[FirmwareSpec]:
    specs = list(BUILTIN)
    for e in load_settings().extra_firmwares:
        try:
            specs.append(FirmwareSpec(builtin=False, **{k: v for k, v in e.items()
                                                        if k in FirmwareSpec.__dataclass_fields__ and k != "builtin"}))
        except TypeError:
            pass
    return specs


def spec(key: str) -> FirmwareSpec | None:
    return next((s for s in catalog() + STOCK if s.key == key), None)


# -------------------------------------------------------------- Releases ---

@dataclass
class Release:
    tag: str
    prerelease: bool
    date: str = ""
    notes: str = ""
    assets: dict[str, str] = field(default_factory=dict)    # Name -> URL

    @property
    def version(self) -> str:
        return self.tag[1:] if self.tag.startswith("v") else self.tag


def version_key(tag: str):
    """sortable: 1.6.0-rc.7 < 1.6.0 < 1.6.1"""
    t = tag.lstrip("vV")
    m = re.match(r"(\d+(?:\.\d+)*)(?:-?(rc|beta|alpha|b|a)\.?(\d+))?", t)
    if not m:
        return ((0,), 0, 0, t)
    nums = tuple(int(x) for x in m.group(1).split("."))
    pre = {"alpha": 1, "a": 1, "beta": 2, "b": 2, "rc": 3}.get((m.group(2) or "").lower(), 9)
    return (nums, pre, int(m.group(3) or 0), t)


def is_prerelease(tag: str) -> bool:
    return bool(re.search(r"-(rc|beta|alpha|a|b)\.?\d", tag, re.I))


def _http_json(url: str, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _head_ok(url: str, timeout=15) -> bool:
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status < 400
    except (urllib.error.URLError, OSError):
        return False


def _download_url(s: FirmwareSpec, tag: str, name: str) -> str:
    return f"https://github.com/{s.repo}/releases/download/{tag}/{name}"


def fetch_releases(s: FirmwareSpec, use_cache_secs: int = 600) -> list[Release]:
    """via the GitHub API; if that does not work (rate limit, proxy), via git tags."""
    if s.download_url:                       # fixed address: exactly one version, no network needed
        name = s.asset.format(ver=s.release_tag.lstrip("vV")) if "{ver}" in s.asset else s.asset
        return [Release(tag=s.release_tag or "fest", prerelease="beta" in (s.notes or []),
                        notes=s.description, assets={name: s.download_url})]
    cache = CACHE_DIR / f"releases-{s.key}.json"
    if use_cache_secs and cache.exists() and time.time() - cache.stat().st_mtime < use_cache_secs:
        try:
            return [Release(**r) for r in json.loads(cache.read_text())]
        except (ValueError, TypeError):
            pass
    out: list[Release] = []
    LAST_ERROR.pop(s.key, None)
    try:
        data = _http_json(f"https://api.github.com/repos/{s.repo}/releases?per_page=40")
        if not isinstance(data, list):
            raise ValueError(_("unexpected API response"))
        for r in data:
            if r.get("draft"):
                continue
            names = {a.get("name") for a in r.get("assets", [])}
            ver = r["tag_name"][1:] if r["tag_name"].startswith("v") else r["tag_name"]     # like Release.version
            if names and s.asset and s.asset.format(ver=ver) not in names:
                continue                     # release without our package (e.g. only orbit-ble.fwsc instead of orbit.fwsc)
            out.append(Release(tag=r["tag_name"], prerelease=bool(r.get("prerelease")) or is_prerelease(r["tag_name"]),
                               date=(r.get("published_at") or "")[:10], notes=r.get("body") or "",
                               assets={a["name"]: a["browser_download_url"] for a in r.get("assets", [])}))
    except Exception as e:                  # API rate limit, proxy, offline …
        LAST_ERROR[s.key] = friendly_error(e)
        out = []
    if not out:
        out = _releases_from_tags(s)
        if out:
            LAST_ERROR.pop(s.key, None)
    out.sort(key=lambda r: version_key(r.tag), reverse=True)
    if out:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps([r.__dict__ for r in out]))
    return out


def _releases_from_tags(s: FirmwareSpec) -> list[Release]:
    try:
        txt = subprocess.run(["git", "ls-remote", "--tags", f"https://github.com/{s.repo}.git"],
                             capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    tags = sorted({ln.split("refs/tags/")[1].removesuffix("^{}") for ln in txt.splitlines() if "refs/tags/" in ln},
                  key=version_key, reverse=True)
    out = []
    for tag in tags[:12]:
        ver = tag.lstrip("v")
        name = s.asset.format(ver=ver)
        if not _head_ok(_download_url(s, tag, name)):
            continue
        assets = {name: _download_url(s, tag, name)}
        for extra in filter(None, [s.resources.format(ver=ver) if s.resources else "", s.sums]):
            if _head_ok(_download_url(s, tag, extra)):
                assets[extra] = _download_url(s, tag, extra)
        out.append(Release(tag=tag, prerelease=is_prerelease(tag), assets=assets))
    return out


def latest(releases: list[Release], channel: str) -> Release | None:
    for r in releases:
        if channel == "rc" or not r.prerelease:
            return r
    return None


# ------------------------------------------------------------- Download ---

@dataclass
class Package:
    spec: FirmwareSpec
    release: Release
    fwsc: Path
    res: Path | None
    verified: bool              # checksum verified


def _fetch(url: str, dest: Path, progress: Callable[[int, int], None] | None = None):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            b = r.read(65536)
            if not b:
                break
            f.write(b)
            done += len(b)
            if progress:
                progress(done, total)
    tmp.replace(dest)


def download(s: FirmwareSpec, rel: Release, log: Callable[[str], None] = print,
             progress: Callable[[int, int], None] | None = None) -> Package:
    if s.download_url:
        return _download_fixed(s, rel, log, progress)
    ver = rel.version
    folder = load_settings().firmware_dir / s.key / rel.tag
    folder.mkdir(parents=True, exist_ok=True)
    names = [s.asset.format(ver=ver)]
    res_name = s.resources.format(ver=ver) if s.resources else ""
    if res_name and (res_name in rel.assets or not rel.assets):
        names.append(res_name)
    sums_ok = s.sums and (s.sums in rel.assets)
    if sums_ok:
        names.append(s.sums)
    for n in names:
        url = rel.assets.get(n) or _download_url(s, rel.tag, n)
        dest = folder / n
        if dest.exists() and dest.stat().st_size and n != s.sums:
            log(_("{name} is already there").format(name=n))
            continue
        log(_("downloading {name} …").format(name=n))
        _fetch(url, dest, progress)
    verified = False
    if sums_ok:
        sums = {}
        for line in (folder / s.sums).read_text().splitlines():
            parts = line.split()
            if len(parts) == 2:
                sums[parts[1].lstrip("*")] = parts[0].lower()
        for n in names:
            if n == s.sums:
                continue
            if n not in sums:
                raise RuntimeError(_("{name} is not listed in {sums}").format(name=n, sums=s.sums))
            h = hashlib.sha256((folder / n).read_bytes()).hexdigest()
            if h != sums[n]:
                (folder / n).unlink(missing_ok=True)
                raise RuntimeError(_("Checksum of {name} does not match – file deleted, please download again").format(name=n))
        verified = True
        log(_("Checksums match"))
    res = folder / res_name if res_name and (folder / res_name).exists() else None
    return Package(s, rel, folder / names[0], res, verified)


class ChecksumError(RuntimeError):
    pass


def _download_fixed(s: FirmwareSpec, rel: Release, log, progress) -> Package:
    """Package from a fixed address; only with a matching checksum (otherwise deleted, nothing installed)"""
    folder = load_settings().firmware_dir / s.key / rel.tag
    folder.mkdir(parents=True, exist_ok=True)
    name = next(iter(rel.assets), s.asset)
    dest = folder / name
    if dest.exists() and s.sha256 and _sha(dest) == s.sha256:
        log(_("{name} is already there (checksum matches)").format(name=name))
        return Package(s, rel, dest, None, True)
    log(_("downloading {name} …").format(name=name))
    tmp = folder / (name + ".neu")  # i18n: ignore (file name)
    _fetch(rel.assets.get(name) or s.download_url, tmp, progress)
    if s.sha256:
        h = _sha(tmp)
        if h != s.sha256.lower():
            tmp.unlink(missing_ok=True)
            raise ChecksumError(_("The file at {url} does not have the expected checksum ({actual}… instead of "
                                  "{expected}…). The manufacturer has probably replaced it – it is not "
                                  "installed.").format(url=s.download_url, actual=h[:12], expected=s.sha256[:12]))
    tmp.replace(dest)
    log(_("Checksum matches") if s.sha256 else _("Note: no checksum known"))
    return Package(s, rel, dest, None, bool(s.sha256))


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 16), b""):
            h.update(b)
    return h.hexdigest()


def fetch_stock(key: str = "stock-v15", log: Callable[[str], None] = print,
                progress: Callable[[int, int], None] | None = None) -> Path:
    """Fetch the stock firmware (cache, otherwise download) – always hash-checked"""
    s = spec(key)
    rel = fetch_releases(s)[0]
    return download(s, rel, log=log, progress=progress).fwsc


def local_packages() -> list[Path]:
    root = load_settings().firmware_dir
    return sorted(root.rglob("*.fwsc"), key=lambda p: p.stat().st_mtime, reverse=True)


# ---------------------------------------------------------------- Repos ---

def _git(args, cwd=None, timeout=600):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if r.returncode:
        raise RuntimeError((r.stderr or r.stdout).strip().splitlines()[-1] if (r.stderr or r.stdout) else _("git error"))
    return r.stdout


def ensure_tools(s: FirmwareSpec, tag: str, log: Callable[[str], None] = print) -> Path:
    """Clone the firmware's repo (only once) and check out the tag."""
    if not shutil.which("git"):
        raise RuntimeError(_("git is missing (sudo apt install git)"))
    d = REPOS_DIR / s.key
    if not (d / ".git").exists():
        log(_("fetching the tools from {repo} (once) …").format(repo=s.repo))
        d.parent.mkdir(parents=True, exist_ok=True)
        _git(["clone", "--filter=blob:none", "--no-checkout", f"https://github.com/{s.repo}.git", str(d)])
    else:
        log(_("updating the tools …"))
        try:
            _git(["fetch", "--tags", "--force", "--quiet"], cwd=d, timeout=180)
        except (RuntimeError, subprocess.TimeoutExpired) as e:
            log(_("Updating not possible ({error}), using the existing state").format(error=e))
    _git(["checkout", "--force", "--quiet", tag], cwd=d)
    if not (d / s.installer).exists():
        raise RuntimeError(_("{name} {tag} has no installer at {path}").format(name=s.name, tag=tag, path=s.installer))
    return d


# ---- Tool trust: the app runs the resource tool (Python) from the cloned firmware repo.
# Built-in firmwares: the commit of each tag is recorded (known revisions below, otherwise the first time);
# if a tag later points to a different commit ("moved tag"), nothing runs without asking.
# Firmwares added by the user: always ask before running a commit for the first time (repo + commit).
KNOWN_TOOL_COMMITS: dict[str, dict[str, str]] = {
    "keremimo/melodee": {        # verified against github.com/keremimo/melodee (git rev-parse <tag>^{commit})
        "v1.5.0": "29ab096a6dce1ce4ed7524ad5a67c26d9e118fe9",
        "v1.5.1": "6a608ba122ac42293908d394e26713e31ec52fb0",
        "v1.6.0-rc.1": "30ea6e4fee157bcb55e56743eea22724d8fd497a",
        "v1.6.0-rc.2": "08673f3c1e62f6c10a8c26ccfb907a53fdb5aeb0",
        "v1.6.0-rc.3": "59b37b7b6889f9d2f6eb95b0937578261df0b86a",
        "v1.6.0-rc.4": "f40dd70fd8b497b5372ee5de886e4dfae1cd8062",
        "v1.6.0-rc.5": "7d2dfa33da8bc8835248be5475e890624d53802e",
        "v1.6.0-rc.6": "14b67a671859579cd2bef4d7672a0a199b7060db",
        "v1.6.0-rc.7": "022bab7294d523fe0b4f48581c8afecc39fc4446",
        "v1.6.0-rc.8": "1150ef2f722721f16af0d279931d40b9edd0a4bf",
        "v1.6.0": "8f0e251b85beb3bb79db9aeb377e665a45c0caec",
        "v1.6.1": "165d177a425944f3d30b0b669255a6ed0050c3a5",
    },
}
TRUST_FILE = DATA_DIR / "tool_trust.json"


class ToolNotTrusted(RuntimeError):
    """The tool from the repo may not be run (yet) – only after asking."""

    def __init__(self, msg: str, repo: str, tag: str, commit: str, moved_from: str = ""):
        super().__init__(msg)
        self.repo, self.tag, self.commit, self.moved_from = repo, tag, commit, moved_from


def _trust() -> dict:
    try:
        d = json.loads(TRUST_FILE.read_text("utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_trust(d: dict) -> None:
    try:
        TRUST_FILE.parent.mkdir(parents=True, exist_ok=True)
        TRUST_FILE.write_text(json.dumps(d, indent=2, ensure_ascii=False), "utf-8")
    except OSError:
        pass


def repo_commit(d: Path) -> str:
    return _git(["rev-parse", "HEAD"], cwd=d).strip()


def tool_trust(s: FirmwareSpec, tag: str, commit: str) -> tuple[bool, str]:
    """-> (may run, reason for asking). For built-in firmwares, remembers the first revision of a tag."""
    known = KNOWN_TOOL_COMMITS.get(s.repo, {}).get(tag) if s.builtin else None
    seen = _trust().get(s.repo, {}).get(tag, "") if isinstance(_trust().get(s.repo), dict) else ""
    if commit and commit in (_trust().get(s.repo + "#approved") or []):
        return True, ""
    if known:
        if commit == known:
            return True, ""
        return False, _("The tag {tag} of {repo} now points to commit {commit}, the known one is {known} – the tag "
                        "was moved.").format(tag=tag, repo=s.repo, commit=commit[:12], known=known[:12])
    if s.builtin:
        if not seen:
            approve_tool(s, tag, commit, remember_only=True)      # first contact: record the revision
            return True, ""
        if seen == commit:
            return True, ""
        return False, _("The tag {tag} of {repo} now points to commit {commit}, last time it was {seen} – the tag "
                        "was moved.").format(tag=tag, repo=s.repo, commit=commit[:12], seen=seen[:12])
    return False, _("{name} was added by you. The resource tool ({tool}) comes from github.com/{repo} (tag {tag}, "
                    "commit {commit}) and runs with your user rights.").format(
                        name=s.name, tool=s.resource_tool, repo=s.repo, tag=tag, commit=commit[:12])


def approve_tool(s: FirmwareSpec, tag: str, commit: str, remember_only: bool = False) -> None:
    """Store consent (or, for built-in firmwares: the first revision)."""
    d = _trust()
    tags = d.get(s.repo) if isinstance(d.get(s.repo), dict) else {}
    if not tags.get(tag) or not remember_only:
        tags[tag] = commit
    d[s.repo] = tags
    if not remember_only:
        appr = [c for c in (d.get(s.repo + "#approved") or []) if isinstance(c, str)]
        if commit not in appr:
            d[s.repo + "#approved"] = appr + [commit]
    _save_trust(d)


def check_tool(s: FirmwareSpec, repo_dir: Path, tag: str) -> str:
    """before running: check the commit -> commit; raises ToolNotTrusted if the user must be asked first."""
    commit = repo_commit(repo_dir)
    tag = tag or _tag_of(repo_dir)
    ok, why = tool_trust(s, tag, commit)
    if not ok:
        moved = KNOWN_TOOL_COMMITS.get(s.repo, {}).get(tag, "") or _trust().get(s.repo, {}).get(tag, "")
        raise ToolNotTrusted(why, s.repo, tag, commit, moved if moved != commit else "")
    return commit


def tools_dir(key: str) -> Path | None:
    d = REPOS_DIR / key
    return d if (d / ".git").exists() else None


def _tag_of(d: Path) -> str:
    try:
        return _git(["describe", "--tags", "--exact-match"], cwd=d).strip()
    except (RuntimeError, OSError, subprocess.TimeoutExpired):
        return ""


def tools_tag(key: str) -> str:
    d = tools_dir(key)
    if not d:
        return ""
    try:
        return _git(["describe", "--tags", "--exact-match"], cwd=d).strip()
    except RuntimeError:
        return ""


def any_installer() -> Path | None:
    """any available Felucca installer (for --info)."""
    for key in ("melodee", "skiff", "sloop"):
        d = tools_dir(key)
        if d and (d / "tools/fm1_install.py").exists():
            return d / "tools/fm1_install.py"
    return None


def _load_module(path: Path, name: str):
    sp = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(sp)
    sys.path.insert(0, str(path.parent))
    try:
        sp.loader.exec_module(mod)
    finally:
        sys.path.remove(str(path.parent))
    return mod


def package_identity(fwsc: Path) -> str:
    """Identity of a package (e.g. 'FM-1_91060'), using the family's installer code."""
    inst = any_installer()
    if not inst:
        return ""
    try:
        mod = _load_module(inst, "_fm1c_install")
        return mod.product_of(fwsc.read_bytes())
    except Exception:
        return ""


def remember_identity(identity: str, key: str, tag: str):
    if not identity:
        return
    try:
        table = json.loads(IDENT_FILE.read_text())
    except (OSError, ValueError):
        table = {}
    table[identity] = {"firmware": key, "tag": tag, "seen": time.time()}
    IDENT_FILE.parent.mkdir(parents=True, exist_ok=True)
    IDENT_FILE.write_text(json.dumps(table, indent=1))


def guess_firmware(identity: str, version_string: str = "", family: str = "") -> tuple[str, str]:
    """(key, tag) for a device identity – first from own experience, then guessed."""
    try:
        table = json.loads(IDENT_FILE.read_text())
        if identity in table:
            return table[identity]["firmware"], table[identity]["tag"]
    except (OSError, ValueError, KeyError):
        pass
    v = version_string.lower()
    m = re.search(r"(\d+\.\d+(?:\.\d+)?(?:-rc\.?\d+)?)", version_string)
    tag = ("v" + m.group(1)) if m else ""
    if "karma" in v or family == "karma":              # KARMA (a SLOOP fork) answers INFO "FELUCCA KARMA 3.1"
        return "karma", tag
    if "orbit" in v or family == "orbit":              # ORBIT (a SLOOP fork) answers INFO "FELUCCA ORBIT 1.0.0" (the
        return "orbit", tag                            # catalog's key; its identity is Felucca's FM-1_900)
    for key in ("melodee", "sloop", "skiff"):
        if key in v or family == key:
            return key, tag
    if "jiant" in v or family.startswith("jiant"):      # JIANT answers INFO "JIANT V10.60" (the catalog's key)
        return "jiant-fm", tag
    if "coralflower" in v or family == "coralflower":   # CoralFlower answers INFO "CoralFlower 0.7.1" (the catalog's key)
        return "coralflower", tag
    if "choralroot" in v or family.startswith("choralroot"):   # ChoralRoot answers INFO "ChoralRoot 0.39" (the catalog's
        return "choralroot-fm-1", tag                            # key "choralroot-fm-1")
    m = re.match(r"FM-1_(\d+)", identity or "")
    if m:
        n = int(m.group(1))
        if 91000 <= n < 92000:
            return "melodee", ""
        if 700 <= n < 800:
            return "skiff", ""
        if n == 909:                                    # JIANT's tools/build.py PRODUCT (its web installer's builds)
            return "jiant-fm", ""
        if n == 930:                                    # CoralFlower's tools/build.py PRODUCT (every release)
            return "coralflower", ""
        if n == 920:                                    # ChoralRoot FM-1's tools/build.py PRODUCT (every FM-1 release)
            return "choralroot-fm-1", ""
        if n == 966:                                    # GR!MM's tools/build.py PRODUCT (every release; the catalog's key)
            return "gr-mm", ""
        if n == 926:                                    # KARMA's own identity in every build (tools/build.py PRODUCT)
            return "karma", ""
        if n in (15, 16):
            return f"stock-v{n}", f"V{n}"
    return "", ""


def query_identity(port_name: str = "", timeout=20) -> tuple[str, str]:
    """-> (identity, state) via the installer (--info). Empty if there is no installer."""
    inst = any_installer()
    if not inst:
        return "", ""
    args = [sys.executable, "-u", str(inst), "--info"]
    if port_name:
        args += ["--port", port_name]
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, cwd=inst.parent.parent)
    except (OSError, subprocess.TimeoutExpired):
        return "", ""
    m = re.search(r"(\S+_\d+)\s+\[([^\]]+)\]", r.stdout)
    return (m.group(1), m.group(2)) if m else ("", "")


# -------------------------------------------------------------- Install ---

INSTALLER_ABORTED = N_("Installer aborted (code {code}): {error}")


class InstallError(Exception):
    state = "safe"
    code = ""


def display_label(s: FirmwareSpec, tag: str) -> str:
    """'Melodee v1.6.0', but 'Original firmware V15' (not '… V15 V15')"""
    tag = (tag or "").strip()
    return s.name if not tag or tag.lower() in s.name.lower() else f"{s.name} {tag}"


def install(s: FirmwareSpec, pkg_path: Path, tag: str, port_name: str = "", res: Path | None = None,
            log: Callable[[str], None] = print, progress: Callable[[int], None] | None = None,
            device_id: str = "", confirmed_unknown: bool = False, cancel=None, pin=None,
            expect_sha: str = "", report: dict | None = None) -> str:
    """Write a package – with the built-in installer (Melodee's fm1_install.py as a library, journal,
    loader rules; flash/engine.py). Afterwards, for Melodee, the resources with the firmware's tool.
    -> new identity. Errors: InstallError (with .state "safe" | "paused" | "restart")."""
    from .flash import engine as fe, package as fp
    try:
        info = fp.analyze(pkg_path, s.loader_family)
    except fp.PackageError as e:
        raise InstallError(str(e))
    if s.sha256 and info.sha256 != s.sha256.lower():
        raise InstallError(_("The checksum of {name} does not match – nothing installed.").format(name=Path(pkg_path).name))
    if expect_sha and info.sha256 != expect_sha.lower():
        raise InstallError(_("{name} has changed since the check – nothing installed.").format(name=Path(pkg_path).name))
    if not s.sha256 and not s.builtin and not info.official and not confirmed_unknown and not s.repo:
        raise InstallError(_("Unknown file: please confirm explicitly in the installation plan."))
    if os.environ.get("FM1C_DEMO") == "1":
        from .flash import demo
        demo.register(Path(pkg_path).read_bytes())
    log(_("starting the installer …"))
    try:
        eng = fe.Engine(log=log, progress=lambda a, b: progress(a) if progress and b else None, cancel=cancel)
    except fe.FlashError as e:
        err = InstallError(_(INSTALLER_ABORTED).format(code=e.code, error=e))
        err.state, err.code = e.state, e.code
        raise err
    log(_("Package: {product} ({family}), SHA-256 {sha256}…").format(product=info.product, family=info.family,
                                                                     sha256=info.sha256[:16]))
    try:
        result = eng.write(info, port=port_name, label=display_label(s, tag), device_id=device_id, pin=pin)
    except fe.FlashError as e:
        err = InstallError(str(e))
        err.state, err.code, err.detail = e.state, e.code, getattr(e, "detail", "")
        raise err
    ident = result.identity
    if s.resources and res is not None and s.resource_tool:
        log(_("writing the resources …"))
        try:
            repo = ensure_tools(s, tag, log)
            # exactly the device that was just installed (with several FM-1, never guess by name prefix)
            target = (eng.current_port(pin) if pin is not None else "") or result.port
            how = write_resources(s, repo, res, target or port_name, log, exact=bool(target), tag=tag)
            if report is not None:
                report["resources"] = RESOURCES_DONE.get(how if isinstance(how, str) else "", RESOURCES_DONE["written"])
        except ToolNotTrusted as e:            # foreign/moved tool: never without asking in the middle of the installation
            log(_("Resources not written: {error} The app asks you when you choose “Rewrite font & factory sounds” "
                  "(Firmware+ › Install & Versions).").format(error=e))
            if report is not None:
                report["resources"] = _("Resources NOT written (the tool state must be confirmed first) – please "
                                        "catch up via “Rewrite font & factory sounds” (Firmware+ › Install & Versions)")
        except Exception as e:  # noqa: BLE001 – the firmware runs anyway
            log(_("Resources not written ({error}) – catch up later via “Rewrite font & factory sounds” "
                  "(Firmware+ › Install & Versions).").format(error=e))
            if report is not None:
                report["resources"] = _("Resources NOT written – please catch up via “Rewrite font & factory sounds” "
                                        "(Firmware+ › Install & Versions)")
    remember_identity(ident or info.product, s.key, tag)
    return ident


# From Melodee 1.6.0-rc.8 on, the update loader writes the resources itself from the package (ldr_core.c ldr_res);
# fm1_resources.py first asks for the status (command 82) and then reports "current" without writing anything.
RESOURCES_DONE = {"written": _("Resources written"),
                  "current": _("Resources already complete (written along by the update loader)")}


def write_resources(s: FirmwareSpec, repo: Path, res: Path, port_name: str = "",
                    log: Callable[[str], None] = print, tries: int = 3, exact: bool = False, tag: str = "") -> str:
    """exact=True: port_name is the complete port name of exactly this device (not just the name prefix).
    -> "written" (written and verified) | "current" (were already there, nothing written) | "" (no tool)"""
    if not s.resource_tool:
        return ""
    commit = check_tool(s, repo, tag)          # never run foreign/moved code without asking
    log(_("Resource tool: {repo} @ {commit}").format(repo=s.repo, commit=commit[:12]))
    last = ""
    for k in range(tries):
        time.sleep(5)
        args = [sys.executable, "-u", str(repo / s.resource_tool), str(res)]
        if port_name:
            args += ["--port", port_name if exact else port_name.split(":")[0]]
        r = subprocess.run(args, cwd=repo, capture_output=True, text=True, timeout=300)
        out = (r.stdout + r.stderr).strip()
        last = out.splitlines()[-1] if out else ""
        m = re.search(r"^resources: (written|current)", out, re.M)
        if m:
            log(RESOURCES_DONE[m.group(1)])
            return m.group(1)
        log(_("Attempt {n}: {output}").format(n=k + 1, output=last))
    raise InstallError(_("Resources could not be written: {output}. Stop playback and switch Bluetooth off, "
                         "then in the app “Rewrite font & factory sounds” (Firmware+ › Install & Versions).").format(
                             output=last))


# ------------------------------------------------------- error texts ---

def friendly_error(e: BaseException) -> str:
    """technical errors (HTTP 403, Errno 13, installer codes …) as an understandable German sentence.
    The technical details belong in the log (see explain())."""
    import errno as _errno
    if isinstance(e, urllib.error.HTTPError):
        return {403: _("GitHub refused the download (HTTP 403). Usually the hourly request limit has been reached "
                       "(60 requests per hour without signing in). Please try again in an hour."),
                404: _("The file does not exist (any more) at this address (HTTP 404). Perhaps the release was "
                       "withdrawn – please reload the list."),
                429: _("Too many requests to the server (HTTP 429). Please wait a little and try again.")}.get(
            e.code, _("The server answered with an error (HTTP {code}). Try again later.").format(code=e.code))
    if isinstance(e, urllib.error.URLError) or isinstance(e, TimeoutError) or \
            type(e).__name__ in ("RemoteDisconnected", "IncompleteRead"):
        return _("No connection to the server – are you online? (Check the network; downloaded packages "
                 "also work offline.)")
    if isinstance(e, OSError) and getattr(e, "errno", None) in (_errno.EACCES, _errno.EPERM, _errno.EROFS):
        if getattr(e, "filename", None):
            return _("No write permission for “{path}”. Please check the permissions of the folder or choose another "
                     "main folder in the settings.").format(path=e.filename)
        return _("No write permission for the folder. Please check the permissions of the folder or choose another "
                 "main folder in the settings.")
    if isinstance(e, OSError) and getattr(e, "errno", None) == _errno.ENOSPC:
        return _("The disk is full. Please free up space and try again.")
    msg = str(e) or type(e).__name__
    for t in {INSTALLER_ABORTED, _(INSTALLER_ABORTED)}:        # source text and UI language
        head, _sep, tail = t.partition("{code}")
        msg = re.sub("^" + re.escape(head) + r"\w+" + re.escape(tail.partition("{error}")[0].rstrip()) + r"\s*", "", msg)
    return msg


def explain(e: BaseException) -> str:
    """short technical description for the log"""
    if isinstance(e, urllib.error.HTTPError):
        return f"HTTP {e.code} {e.reason} – {e.url}"
    code = getattr(e, "code", "")
    detail = getattr(e, "detail", "")
    return f"{type(e).__name__}{f' [{code}]' if code else ''}: {e}" + (f" ({detail})" if detail else "")


def spec_by_release_sha(sha: str) -> FirmwareSpec | None:
    """Package that the app itself downloaded from a release with SHA256SUMS and verified (Firmware/<key>/<tag>)"""
    import dataclasses
    sha = (sha or "").lower()
    if not sha:
        return None
    try:
        root = load_settings().firmware_dir
        sums_files = list(root.glob("*/*/*SUMS*"))
    except OSError:
        return None
    for f in sums_files:
        try:
            lines = f.read_text("utf-8", "replace").splitlines()
        except OSError:
            continue
        for line in lines:
            parts = line.split()
            if len(parts) == 2 and parts[0].lower() == sha and parts[1].lstrip("*").endswith(".fwsc"):
                sp = spec(f.parent.parent.name)
                if sp is not None:
                    return dataclasses.replace(sp, sha256=sha, release_tag=sp.release_tag or f.parent.name)
    return None
