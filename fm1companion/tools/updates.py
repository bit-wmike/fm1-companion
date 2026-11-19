# SPDX-License-Identifier: GPL-3.0-only
"""Looking for new versions of the bundled tools (GitHub releases) and fetching one when the user asks.

* The same release query as for the firmwares (firmware._http_json, https only).
* Automatic checks: Settings.tools_check = "daily" (default) | "weekly" | "start" (once per app start) | "off".
  They also obey the firmware switch Settings.check_on_start ("look for firmware updates … (asks GitHub)"),
  FM1C_NO_NET=1, and never run while an installation, a recovery or a tool run holds the lock. "Check now" ignores
  check_on_start (the user clicked) but not FM1C_NO_NET.
* Nothing is ever installed automatically. "Update" downloads the release file into a staging folder, checks it
  (GitHub's per-file digest; for m-bootloader also the release's SHA256SUMS), and only then moves it next to the
  previous version, which stays as the fallback (tools/store.py).
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..i18n import _
from . import manifest as mf
from .store import META, Candidate, Store

MODES = ("daily", "weekly", "start", "off")
INTERVAL = {"daily": 24 * 3600, "weekly": 7 * 24 * 3600}
MAX_BOOT = 16128                    # m-bootloader container limit (SPEC §11.1)
MAX_AREA = 0x74000                  # SLOOP 3's application area must end before 0x74000 + 0x4000
MAX_BINARY = 64 * 1024 * 1024
_started_check_done = False         # "only at start": once per process


class UpdateError(RuntimeError):
    """a user-facing reason (translated); nothing was kept"""


@dataclass
class Offer:
    key: str
    tag: str
    version: str
    date: str = ""
    notes: str = ""
    prerelease: bool = False
    asset: str = ""                 # the file the app would fetch
    url: str = ""
    digest: str = ""                # GitHub's sha256 of that file ("" = none published)
    sums_url: str = ""              # m-bootloader: SHA256SUMS
    sums_digest: str = ""
    page: str = ""                  # the release page


def titles() -> list[tuple[str, str]]:
    """[(key, display name)] of the bundled tools"""
    return [(k, mf.tool(k)["title"]) for k in mf.KEYS]


def cache_file() -> Path:
    from .. import config
    return config.CACHE_DIR / "tools-check.json"


def load_cache() -> dict:
    try:
        d = json.loads(cache_file().read_text("utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_cache(d: dict):
    p = cache_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1), "utf-8")
    os.replace(tmp, p)


def mode(settings) -> str:
    m = str(getattr(settings, "tools_check", "daily") or "daily")
    return m if m in MODES else "daily"


def no_net() -> bool:
    return bool(os.environ.get("FM1C_NO_NET"))


def busy() -> bool:
    """an installation, a recovery or a tool run holds the lock (here or in another program window)"""
    from .. import storage
    return storage.install_running()


def auto_check_allowed(settings) -> tuple[bool, str]:
    """(may an automatic check go online now, why not – English, for the log)"""
    if no_net():
        return False, "FM1C_NO_NET is set"
    if not getattr(settings, "check_on_start", True):
        return False, "firmware update checks are switched off in the settings"
    if mode(settings) == "off":
        return False, "tool update checks are switched off"
    if busy():
        return False, "an installation or tool run is in progress"
    return True, ""


def due(settings, now: float | None = None, cache: dict | None = None) -> bool:
    m = mode(settings)
    if m == "off":
        return False
    if m == "start":
        return not _started_check_done
    last = float((cache if cache is not None else load_cache()).get("last") or 0)
    now = time.time() if now is None else now
    return now - last >= INTERVAL[m] or now < last


def _version_of(key: str, tag: str) -> str:
    v = tag[1:] if tag[:1] in "vV" else tag
    if key == "sloop3":                  # SLOOP 3's tags are all betas: v3.4.0-beta is version 3.4.0
        v = re.sub(r"-beta(\.?\d+)?$", "", v)
    return v


def _asset_for(key: str, ver: str, names: list[str], arch: str) -> str:
    if key == "m-flasher":
        n = f"m-flasher-{ver}-linux-{arch}.tar.gz" if arch else ""
        return n if n in names else ""
    if key == "m-bootloader":
        return "fm1-uboot.boot" if "fm1-uboot.boot" in names else ""
    found = sorted(n for n in names if re.fullmatch(r"sloop3-[0-9][0-9.]*\.area", n))
    return found[-1] if found else ""


def offers_from_releases(key: str, releases: list, local: str, arch: str, allow_pre: bool) -> Offer | None:
    """the newest release that is newer than `local` and has the file this computer needs"""
    from ..firmware import asset_digests, is_prerelease, version_key
    best = None
    for r in releases or []:
        if not isinstance(r, dict) or r.get("draft") or not isinstance(r.get("tag_name"), str):
            continue
        tag = r["tag_name"]
        pre = bool(r.get("prerelease")) or is_prerelease(tag)
        if pre and not allow_pre and key != "sloop3":
            continue
        ver = _version_of(key, tag)
        if version_key(ver) <= version_key(local):
            continue
        assets = [a for a in r.get("assets") or [] if isinstance(a, dict) and isinstance(a.get("name"), str)]
        names = [a["name"] for a in assets]
        asset = _asset_for(key, ver, names, arch)
        if not asset:
            continue
        urls = {a["name"]: str(a.get("browser_download_url") or "") for a in assets}
        dig = asset_digests(assets)
        o = Offer(key, tag, ver, (r.get("published_at") or "")[:10], str(r.get("body") or "")[:20000], pre, asset,
                  urls.get(asset, ""), dig.get(asset, ""), urls.get("SHA256SUMS", ""), dig.get("SHA256SUMS", ""),
                  str(r.get("html_url") or ""))
        if best is None or version_key(ver) > version_key(best.version):
            best = o
    return best


def local_version(key: str, store: Store) -> str:
    c = store.candidates(key)
    return c[0].version if c else mf.tool(key, store.data)["version"]


def check(settings=None, store: Store | None = None, fetch=None, automatic: bool = False) -> dict:
    """ask GitHub for the three tools' releases. -> {key: Offer | None}; saves the result (cache) and the time.
    fetch(url) -> parsed JSON (tests); default firmware._http_json."""
    global _started_check_done
    if no_net():
        raise UpdateError(_("The app is not allowed to go online (FM1C_NO_NET is set)."))
    if settings is None:
        from .. import config
        settings = config.load()
    store = store or Store()
    if fetch is None:
        from ..firmware import _http_json as fetch
    cache = load_cache()
    tools = cache.setdefault("tools", {})
    out: dict[str, Offer | None] = {}
    allow_pre = getattr(settings, "channel", "stable") == "rc"
    for key in mf.KEYS:
        t = mf.tool(key, store.data)
        entry = {"checked": time.time(), "error": "", "offer": None}
        try:
            rel = fetch(f"https://api.github.com/repos/{t['repo']}/releases?per_page=10")
            if not isinstance(rel, list):
                raise ValueError("unexpected API response")
            o = offers_from_releases(key, rel, local_version(key, store), store.arch, allow_pre)
            out[key] = o
            entry["offer"] = asdict(o) if o else None
        except Exception as e:  # noqa: BLE001 – offline, rate limit, proxy …: shown, never fatal
            from ..firmware import friendly_error
            entry["error"] = friendly_error(e)
            out[key] = None
        old = tools.get(key) or {}
        entry["notified"] = old.get("notified", "")
        tools[key] = entry
    cache["last"] = time.time()
    if automatic:
        _started_check_done = True
    _save_cache(cache)
    return out


def cached_offer(key: str, store: Store | None = None) -> Offer | None:
    """the last check's offer, if it is still newer than what this computer has"""
    from ..firmware import version_key
    e = (load_cache().get("tools") or {}).get(key) or {}
    o = e.get("offer")
    if not isinstance(o, dict):
        return None
    try:
        offer = Offer(**{k: o[k] for k in Offer.__dataclass_fields__ if k in o})
    except TypeError:
        return None
    store = store or Store()
    return offer if version_key(offer.version) > version_key(local_version(key, store)) else None


def new_to_notify(offers: dict) -> list[Offer]:
    """offers the user has not been told about yet (each version once); marks them as told"""
    cache = load_cache()
    tools = cache.setdefault("tools", {})
    out = []
    for key, o in offers.items():
        if o is None:
            continue
        e = tools.setdefault(key, {})
        if e.get("notified") != o.tag:
            e["notified"] = o.tag
            out.append(o)
    if out:
        _save_cache(cache)
    return out


# ------------------------------------------------------------ download, verify, stage

def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _download(url: str, dest: Path, fetch_file=None):
    if not url.startswith("https://"):
        raise UpdateError(_("The release file has no secure (https) address. Nothing was kept."))
    if fetch_file is None:
        from ..firmware import _fetch as fetch_file
    fetch_file(url, dest)


def _sums_line(text: str, name: str) -> str:
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) == 2 and parts[1].lstrip("*") == name and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            return parts[0].lower()
    return ""


def _probe(binary: Path, args: list[str]) -> str:
    try:
        r = subprocess.run([str(binary), *args], stdin=subprocess.DEVNULL, capture_output=True, timeout=10,
                           env=dict(os.environ, LANG="C", LC_ALL="C"))
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else ""


def download_update(offer: Offer, store: Store | None = None, fetch_file=None) -> Candidate:
    """fetch, check and stage the offered version; the previous version stays. -> the new candidate (not proven
    yet). Raises UpdateError (translated) and keeps nothing when a check fails."""
    store = store or Store()
    if no_net():
        raise UpdateError(_("The app is not allowed to go online (FM1C_NO_NET is set)."))
    if busy():
        raise UpdateError(_("An installation or recovery is running. Tools are not updated meanwhile – try again "
                            "when it has finished."))
    key, ver = offer.key, offer.version
    if not re.fullmatch(r"[0-9][0-9A-Za-z.+-]{0,40}", ver):
        raise UpdateError(_("The release has an unexpected version number. Nothing was kept."))
    if not offer.digest:
        raise UpdateError(_("GitHub publishes no checksum for {file}, so the app cannot check the download. "
                            "Nothing was kept.").format(file=offer.asset))
    base = store.updates_dir / key
    final = base / ver
    if final.exists():
        raise UpdateError(_("Version {version} is already on this computer.").format(version=ver))
    stage = base / f".staging-{ver}"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    try:
        dl = stage / ("download-" + offer.asset)
        _download(offer.url, dl, fetch_file)
        data = dl.read_bytes()
        got = _sha(data)
        if got != offer.digest.lower():
            raise UpdateError(_("The downloaded {file} does not match the checksum GitHub publishes for it. "
                                "Nothing was kept.").format(file=offer.asset))
        meta = {"version": ver, "tag": offer.tag, "key": key, "installed": time.time(),
                "download": {"asset": offer.asset, "url": offer.url, "sha256": got,
                             "checked_with": "GitHub digest"}, "files": {}}
        if key == "m-flasher":
            _stage_mflasher(store, offer, data, stage, meta)
        elif key == "m-bootloader":
            _stage_boot(offer, data, stage, meta, fetch_file)
        else:
            _stage_area(offer, data, stage, meta)
        dl.unlink()
        (stage / META).write_text(json.dumps(meta, indent=1), "utf-8")
        os.replace(stage, final)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    st = store.state()
    st.setdefault(key, {})[ver] = {"proven": False, "failed": False, "installed": time.time()}
    store._save_state(st)
    for c in store.updates(key):
        if c.version == ver:
            return c
    raise UpdateError(_("The new version could not be stored."))


def _stage_mflasher(store: Store, offer: Offer, data: bytes, stage: Path, meta: dict):
    want = "m-flasher/m-flasher"
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            members = {m.name: m for m in tar.getmembers()}
            m = members.get(want)
            if m is None or not m.isreg() or m.size > MAX_BINARY:
                raise UpdateError(_("The M-Flasher download does not contain the expected program. Nothing was "
                                    "kept."))
            binary = tar.extractfile(m).read()
            lic = members.get("m-flasher/LICENSE")
            licence = tar.extractfile(lic).read() if lic is not None and lic.isreg() and lic.size < 65536 else b""
    except (tarfile.TarError, OSError, EOFError) as e:
        raise UpdateError(_("The M-Flasher download is damaged. Nothing was kept.")) from e
    d = stage / store.arch
    d.mkdir()
    exe = d / "m-flasher"
    exe.write_bytes(binary)
    exe.chmod(0o755)
    if licence:
        (stage / "LICENSE").write_bytes(licence)
    meta["files"][f"{store.arch}/m-flasher"] = {"sha256": _sha(binary), "size": len(binary)}
    meta["main"] = f"{store.arch}/m-flasher"
    if _probe(exe, ["--version"]).strip() != offer.version:
        raise UpdateError(_("The downloaded M-Flasher does not start or reports another version. Nothing was "
                            "kept."))
    help_sha = _sha(_probe(exe, ["--help"]).encode())
    known = help_sha == mf.tool("m-flasher", store.data).get("help_sha256")
    meta["help_sha256"] = help_sha
    meta["contract_known"] = known       # a changed command line: used read-only until an app update knows it


def _stage_boot(offer: Offer, data: bytes, stage: Path, meta: dict, fetch_file):
    if not offer.sums_url:
        raise UpdateError(_("The m-bootloader release has no SHA256SUMS file. Nothing was kept."))
    sums = stage / "SHA256SUMS"
    _download(offer.sums_url, sums, fetch_file)
    text = sums.read_text("utf-8", "replace")
    if offer.sums_digest and _sha(sums.read_bytes()) != offer.sums_digest.lower():
        raise UpdateError(_("The release's SHA256SUMS does not match the checksum GitHub publishes for it. Nothing "
                            "was kept."))
    if _sums_line(text, "fm1-uboot.boot") != _sha(data):
        raise UpdateError(_("fm1-uboot.boot does not match the release's SHA256SUMS. Nothing was kept."))
    if not 0 < len(data) <= MAX_BOOT:
        raise UpdateError(_("fm1-uboot.boot has an unexpected size. Nothing was kept."))
    (stage / "fm1-uboot.boot").write_bytes(data)
    meta["files"]["fm1-uboot.boot"] = {"sha256": _sha(data), "size": len(data)}
    meta["main"] = "fm1-uboot.boot"
    meta["download"]["checked_with"] = "GitHub digest and SHA256SUMS"


def _stage_area(offer: Offer, data: bytes, stage: Path, meta: dict):
    if not 0x120 < len(data) < MAX_AREA:
        raise UpdateError(_("The SLOOP 3 file has an unexpected size. Nothing was kept."))
    (stage / offer.asset).write_bytes(data)
    meta["files"][offer.asset] = {"sha256": _sha(data), "size": len(data)}
    meta["main"] = offer.asset


@dataclass
class ToolStatus:
    key: str
    title: str
    license: str
    bundled: str                      # pinned version
    bundled_present: bool
    active: str                       # the version the app uses for read-only work ("" = none)
    active_source: str
    verified: bool                    # the active version's SHA-256 checks out right now
    write_version: str                # the version used for writes (later steps)
    last_check: float
    offer: Offer | None
    error: str
    problems: list[str] = field(default_factory=list)
    unavailable: str = ""


def status(store: Store | None = None) -> list[ToolStatus]:
    store = store or Store()
    cache = load_cache()
    out = []
    for key in mf.KEYS:
        t = mf.tool(key, store.data)
        b = store.bundled(key)
        c, problems = store.select(key)
        w, _p = store.select(key, write=True)
        e = (cache.get("tools") or {}).get(key) or {}
        why = ""
        if key == "m-flasher":
            from .runner import unavailable_reason
            why = unavailable_reason(store)
        out.append(ToolStatus(key, t["title"], t["license"], t["version"], bool(b and b.present),
                              c.version if c else "", c.source if c else "", c is not None,
                              w.version if w else "", float(e.get("checked") or 0), cached_offer(key, store),
                              str(e.get("error") or ""), problems, why))
    return out


def licence_text(key: str, store: Store | None = None) -> str:
    """the licence and notice files shipped with the tool, one after the other"""
    store = store or Store()
    t = mf.tool(key, store.data)
    parts = []
    for name in t.get("licence_files") or []:
        p = store.bundled_dir / key / name
        try:
            parts.append(f"==== {name} ====\n\n" + p.read_text("utf-8", "replace").strip() + "\n")
        except OSError:
            continue
    return "\n".join(parts)
