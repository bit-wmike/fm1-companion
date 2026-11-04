# SPDX-License-Identifier: GPL-3.0-only
"""Firmware catalog, snapshot, link import, GitHub cache – without network.

  python3 -m pytest tests/test_catalog.py -q
"""
from __future__ import annotations

import email.message
import importlib
import io
import json
import re
import sys
import time
import urllib.error
import urllib.parse
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
AWESOME = Path("/tmp/awesome-fm-1")
FIX = ROOT / "tests" / "fixtures" / "catalog"
need_awesome = pytest.mark.skipif(not (AWESOME / "README.md").exists(), reason="awesome-fm-1 clone missing")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    for k, sub in (("XDG_CONFIG_HOME", "c"), ("XDG_DATA_HOME", "d"), ("XDG_CACHE_HOME", "k")):
        monkeypatch.setenv(k, str(tmp_path / sub))
    from fm1companion import config
    importlib.reload(config)
    from fm1companion import catalog, firmware, linkimport
    importlib.reload(firmware)
    importlib.reload(catalog)
    importlib.reload(linkimport)
    config.load().user_root = str(tmp_path / "FM-1")
    monkeypatch.setattr(catalog, "urlopen", _no_net)
    return catalog, linkimport, firmware, config


def _no_net(*a, **k):
    raise urllib.error.URLError("kein Netz im Test")


@pytest.fixture(scope="module")
def real():
    from fm1companion import catalog
    moor = catalog.parse_mooring(json.loads((AWESOME / "mooring/catalog.json").read_text()))
    return catalog.merge((AWESOME / "README.md").read_text(), moor), moor


# ------------------------------------------------------------------ Mooring
@need_awesome
def test_mooring_parse(real):
    _cat, moor = real
    pk = moor["packages"]
    assert moor["revision"] >= 6 and len(pk) >= 145
    assert sum(1 for p in pk if p.downloadable) >= 121
    orbit = next(p for p in pk if p.family == "orbit")
    assert orbit.product == "FM-1_900" and orbit.url.endswith("orbit.fwsc") and len(orbit.sha256) == 64
    assert orbit.requires_stock_v15 and "beta" in orbit.notes and orbit.loader_family == "sloopG3"
    v15 = next(p for p in pk if p.product == "FM-1_015")
    assert v15.stock and v15.loader_family == "stock" and not v15.requires_stock_v15


def test_split_label(env):
    c = env[0]
    assert c._split_label("Felucca 8 Patterns 1.5.1 (felucca-1.51.fwsc)") == ("Felucca 8 Patterns", "1.5.1")
    assert c._split_label("CTL-1 0.9.2 (USB build) (ctl-usb-0.9.2.fwsc)") == ("CTL-1", "0.9.2")
    assert c._split_label("sloopDX 4.0, Hub and repository copy (x.fwsc)")[0] == "sloopDX"


# ------------------------------------------------------------------ README
@need_awesome
def test_readme_parse(real):
    from fm1companion import catalog
    items, res = catalog.parse_readme((AWESOME / "README.md").read_text())
    assert len(items) >= 240 and len(res) >= 100
    assert {"open", "closed", "notsynth", "official"} <= {i["section"] for i in items}
    cat, _ = real
    fel = cat.by_key("felucca")
    assert fel.author.startswith("Leo Kuroshita") and fel.license == "GPL-3.0" and fel.repo == "hugelton/Felucca"
    assert fel.links["installer"] == "https://hugelton.github.io/Felucca/"
    assert fel.links["editor"].endswith("/webapp/editor/") and fel.links["emulator"].endswith("/webapp/try/")
    assert "[" not in fel.description and "GPL" not in fel.description
    mel = cat.by_key("melodee")
    assert mel.builtin and mel.protocol == "felucca" and mel.links["installer"].startswith("https://keremimo")
    sl = cat.by_key("sloop")
    assert sl.author == "3dSam" and sl.lineage == ["SLOOP"] and sl.builtin
    orbit = cat.by_key("orbit")
    assert orbit.emulator_url == "https://dspaudio.github.io/orbit-web-emu/" and "SLOOP" in orbit.lineage
    hort = cat.by_key("hortator")
    assert hort.author == "DEADACTIVE" and hort.emulator_url.startswith("https://deadactive.github.io")
    fomni = cat.by_key("fomni")
    assert fomni.author == "Charles Vestal" and "Felucca" in fomni.lineage
    assert set(fomni.links) >= {"installer", "emulator"}
    fumi = cat.by_key("fumi-1")
    assert "FoMni" in fumi.lineage
    assert cat.by_key("groove-os").section == "closed" and "kostenpflichtig" in cat.by_key("groove-os").flags
    assert cat.by_key("fm-1-doom").categories[0] == "Spiele & Spaß"
    x0x = cat.by_key("x0x")
    assert x0x.links["manual"].endswith("/manual/") and x0x.links["emulator"].endswith("/emu/")


@need_awesome
def test_merge_both_directions(real):
    cat, moor = real
    assert len(cat.entries) >= 240
    matched = [e for e in cat.entries if e.packages and e.section not in ("mooring", "official")]
    assert len(matched) >= 60
    fams = {p.family for p in moor["packages"] if p.family}
    covered = {p.family for e in cat.entries for p in e.packages if p.family}
    assert fams == covered                      # every Mooring family lands in an entry
    assert sum(1 for e in cat.entries if not e.packages) >= 100   # pure link entries stay
    stock = cat.by_key("stock")
    assert [p.version for p in stock.packages][:2] == ["V16", "V15"] and stock.lineage == ["Original M-VAVE"]
    fel = cat.by_key("felucca")
    assert fel.latest.version == "v1.6.1" and any(not p.downloadable for p in fel.packages)
    e, p = cat.by_sha256(cat.by_key("orbit").latest.sha256)
    assert e.key == "orbit" and p.version == "v0.7.1"
    assert len({e.key for e in cat.entries}) == len(cat.entries)
    keys_with_lineage = sum(1 for e in cat.entries if e.lineage)
    assert keys_with_lineage >= 200


@need_awesome
def test_spec_and_warnings(real):
    cat, _ = real
    h = cat.by_key("hortator")
    s = h.to_spec()
    # live clone of awesome-fm-1: the Hortator version moves along (0.25.0 → 0.26.0 …), only the pattern is fixed
    assert re.fullmatch(r"hortator-\d+\.\d+\.\d+\.fwsc", s.asset) and s.download_url.endswith("/" + s.asset)
    assert s.requires_stock_v15 and s.protocol == "none" and s.product == "FM-1_900"
    assert cat.by_key("x0x").to_spec().asset == "x0x-{ver}.fwsc"
    w = " ".join(h.warnings())
    assert "Beta" in w and "nicht fortsetzen" in w and "V15" in w


@need_awesome
def test_snapshot_builder_and_shipped_snapshot(tmp_path):
    sys.path.insert(0, str(ROOT / "tools"))
    import build_catalog_snapshot as b
    out = tmp_path / "snap.json"
    assert b.main(["--mooring", str(AWESOME / "mooring/catalog.json"), "--readme", str(AWESOME / "README.md"),
                   "--out", str(out)]) == 0
    from fm1companion import catalog
    snap = catalog.load_snapshot(out)
    assert len(snap.entries) >= 240 and snap.revision >= 6
    shipped = catalog.load_snapshot()
    assert len(shipped.entries) >= 240 and shipped.by_key("orbit").installable
    assert sum(1 for e in shipped.entries if e.screenshot) >= 50


def test_signature_status(env):
    c = env[0]
    if not (AWESOME / "mooring/catalog.json.intoto.jsonl").exists():
        pytest.skip("no clone")
    data = (AWESOME / "mooring/catalog.json").read_bytes()
    line = (AWESOME / "mooring/catalog.json.intoto.jsonl").read_bytes()
    assert c.mooring_signature_status(data, line) == "Prüfsumme passt, Schlüssel unbekannt"
    assert c.mooring_signature_status(data + b" ", line) == "Prüfsumme passt nicht"


def test_product_of(env, tmp_path):
    c = env[0]
    ident = "FM-1_900"
    raw = bytearray(20 * 0x30 + 100)
    for i in range(20):
        raw[i * 0x30 + 0x2F] = (ord(ident[i]) + i + 1) & 0xFF if i < len(ident) else 0x7D
    assert c.product_of(bytes(raw)) == ident
    f = tmp_path / "x.fwsc"
    f.write_bytes(bytes(raw))
    info = c.identify_file(f)
    assert info["product"] == ident and len(info["sha256"]) == 64


def test_first_image(env):
    c = env[0]
    md = "[![b](https://img.shields.io/x.svg)](y)\n![Panel](docs/panel.jpg)\n"
    assert c.first_image(md, "a/b") == "https://raw.githubusercontent.com/a/b/HEAD/docs/panel.jpg"
    assert c.first_image('<img src="https://github.com/a/b/blob/main/s.png">', "a/b") == \
        "https://raw.githubusercontent.com/a/b/main/s.png"
    assert c.first_image("kein Bild", "a/b") == ""


# ------------------------------------------------------------------ GitHub cache
class FakeResp(io.BytesIO):
    def __init__(self, data: bytes, headers=None):
        super().__init__(data)
        self.headers = headers or {}
        self.status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def http_error(code, headers=None):
    h = email.message.Message()
    for k, v in (headers or {}).items():
        h[k] = v
    return urllib.error.HTTPError("https://api.github.com/x", code, "err", h, io.BytesIO(b"{}"))


def test_github_enrich_with_fixtures(env, monkeypatch):
    c = env[0]
    seen = []

    def fake(req, timeout=0):
        seen.append(req.full_url)
        name = "github_releases_dspaudio_orbit.json" if "releases" in req.full_url else "github_repo_dspaudio_orbit.json"
        return FakeResp((FIX / name).read_bytes(), {"ETag": '"abc"'})
    monkeypatch.setattr(c, "urlopen", fake)
    info = c.enrich("dspaudio/orbit")
    assert info["stars"] == 42 and info["releases"][0]["tag"] == "v0.7.1"
    assert info["releases"][0]["assets"][0]["size"] == 610019
    assert len(info["releases"]) == 2 and info["releases"][1]["prerelease"]       # draft left out
    n = len(seen)
    c.enrich("dspaudio/orbit")
    assert len(seen) == n                         # from the cache (24 h)
    assert c.cached_enrichment("dspaudio/orbit")["stars"] == 42


def test_github_rate_limit_keeps_cache(env, monkeypatch):
    c = env[0]
    monkeypatch.setattr(c, "urlopen", lambda req, timeout=0: FakeResp((FIX / "github_repo_dspaudio_orbit.json").read_bytes()))
    c.gh_api("repos/dspaudio/orbit")
    reset = int(time.time()) + 1800
    monkeypatch.setattr(c, "urlopen", lambda req, timeout=0: (_ for _ in ()).throw(
        http_error(403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(reset)})))
    data, msg = c.gh_api("repos/dspaudio/orbit", ttl=0)        # expired -> ask again -> limit
    assert data["stargazers_count"] == 42 and "GitHub-Limit erreicht" in msg
    with pytest.raises(c.RateLimited) as e:
        c.gh_api("repos/other/repo")
    assert e.value.reset == reset and "später erneut" in str(e.value)
    monkeypatch.setattr(c, "urlopen", lambda req, timeout=0: (_ for _ in ()).throw(http_error(403)))
    _d, msg = c.gh_api("repos/dspaudio/orbit", ttl=0)
    assert "verweigert" in msg
    monkeypatch.setattr(c, "urlopen", lambda req, timeout=0: (_ for _ in ()).throw(http_error(429)))
    with pytest.raises(c.RateLimited):
        c.gh_api("repos/x/y")


def test_github_etag_304_and_token(env, monkeypatch):
    c, _L, _fw, config = env
    monkeypatch.setattr(c, "urlopen", lambda req, timeout=0: FakeResp(b'{"stargazers_count": 7}', {"ETag": '"e1"'}))
    c.gh_api("repos/a/b")
    config.module("catalog")["github_token"] = "tok123"
    got = {}

    def fake(req, timeout=0):
        got.update(req.headers)
        raise http_error(304)
    monkeypatch.setattr(c, "urlopen", fake)
    data, msg = c.gh_api("repos/a/b", ttl=0)
    assert data == {"stargazers_count": 7} and not msg
    assert got.get("If-none-match") == '"e1"' and got.get("Authorization") == "Bearer tok123"


def test_github_404_and_offline(env, monkeypatch):
    c = env[0]
    monkeypatch.setattr(c, "urlopen", lambda req, timeout=0: (_ for _ in ()).throw(http_error(404)))
    with pytest.raises(c.Offline, match="nicht gefunden"):
        c.gh_api("repos/gone/away")
    with pytest.raises(c.Offline):
        monkeypatch.setattr(c, "urlopen", _no_net)
        c.gh_api("repos/x/z")


def test_catalog_cache_expiry(env, monkeypatch):
    import os
    c = env[0]
    assert c.needs_refresh()
    snap = c.load_snapshot()
    snap.built = "2099-01-01T00:00:00Z"
    snap.entries = snap.entries[:5]
    c.cached_file().write_text(json.dumps(snap.to_dict()))
    assert not c.needs_refresh()
    assert len(c.load().entries) == 5 and c.load().source == "cache"
    old = time.time() - 25 * 3600
    os.utime(c.cached_file(), (old, old))
    assert c.needs_refresh()


def test_refresh_offline_and_changes(env, monkeypatch):
    c = env[0]
    with pytest.raises(c.Offline):
        c.refresh()
    if not (AWESOME / "README.md").exists():
        return
    files = {c.MOORING_URL: (AWESOME / "mooring/catalog.json").read_bytes(), c.README_URL: (AWESOME / "README.md").read_bytes()}
    monkeypatch.setattr(c, "_get", lambda url, ef=None, timeout=20: (files[url], '"x"'))
    old = c.load_snapshot()
    victim = old.by_key("orbit")
    victim.packages = victim.packages[1:]
    c.cached_file().write_text(json.dumps(dict(old.to_dict(), built="2099")))
    cat = c.refresh()
    assert cat.source == "live" and c.cached_file().exists()
    ch = c.recorded_changes()
    assert any(x["key"] == "orbit" and x["kind"] == "version" for x in ch)


# ------------------------------------------------------------------ link import
CASES = [
    ("https://github.com/dspaudio/orbit", "orbit", "dspaudio/orbit"),
    ("https://github.com/dspaudio/orbit/releases", "orbit", "dspaudio/orbit"),
    ("https://github.com/dspaudio/orbit/releases/tag/v0.7.1", "orbit", "dspaudio/orbit"),
    ("github.com/hugelton/Felucca", "felucca", "hugelton/Felucca"),
    ("https://www.github.com/charlesvestal/fm1-x0x/", "x0x", "charlesvestal/fm1-x0x"),
    ("https://github.com/DeadActive/hortator/releases/download/drum-v0.25.0/hortator-0.25.0.fwsc", "hortator",
     "DeadActive/hortator"),
    ("Hier der Link: <https://github.com/isod89/sloop-fm1>, viel Spaß!", "sloop", "isod89/sloop-fm1"),
    ("https://charlesvestal.github.io/fm1-x0x/emu/", "x0x", "charlesvestal/fm1-x0x"),
    ("https://hugelton.github.io/Felucca/webapp/try/", "felucca", "hugelton/Felucca"),
    ("https://keremimo.github.io/melodee/", "melodee", "keremimo/melodee"),
    ("https://fm1-editor.com/firmware/#felucca", "felucca", "hugelton/Felucca"),
    ("https://thefirmwarehub.com/#fm1open", "fm1-open", "m-vave/fm1_open"),
    ("https://thefirmwarehub.com/#orbit", "orbit", "dspaudio/orbit"),
    ("orbit", "orbit", "dspaudio/orbit"),
    ("  Hortator ", "hortator", "DeadActive/hortator"),
    ("ghoulbox", "ghoulbox", "jasonpersinger/ghoulbox-fm1-dungeon-synth"),
    ("https://github.com/illuminus5/skiff/releases/tag/v1.2", "skiff", "illuminus5/skiff"),
    ("https://github.com/m-vave/fm1_open", "fm1-open", "m-vave/fm1_open"),
    ("https://deadactive.github.io/hortator/", "hortator", "DeadActive/hortator"),
    ("https://dspaudio.github.io/orbit-web-emu/", "orbit", "dspaudio/orbit"),
]


@pytest.mark.parametrize("text,key,repo", CASES)
def test_resolve_catalog_links(env, text, key, repo):
    L = env[1]
    d = L.resolve(text)
    assert d.key == key and d.repo.lower() == repo.lower(), (d.key, d.repo, d.hints)
    assert d.confidence.get("repo") == L.SURE


def test_resolve_download_sets_sha_and_asset(env):
    L = env[1]
    d = L.resolve("https://github.com/DeadActive/hortator/releases/download/drum-v0.25.0/hortator-0.25.0.fwsc")
    assert d.sha256 and d.download_url.endswith("hortator-0.25.0.fwsc") and d.tag == "drum-v0.25.0"
    assert d.requires_stock_v15 and "beta" in d.notes


def test_resolve_unknown_repo_guesses(env):
    L = env[1]
    d = L.resolve("https://github.com/someone/new-synth-fm1/releases/tag/v1.0")
    assert d.repo == "someone/new-synth-fm1" and d.name == "new synth fm1" and d.confidence["name"] == L.GUESS
    assert d.author == "someone" and d.protocol == "none" and d.key == "new-synth-fm1"
    assert any("beim Verbinden geprüft" in h for h in d.hints)


def test_resolve_unknown_download_asset_pattern(env):
    L = env[1]
    d = L.resolve("https://github.com/someone/cool/releases/download/v1.2.0/cool-1.2.0.fwsc")
    assert d.asset == "cool-{ver}.fwsc" and d.download_url.endswith("cool-1.2.0.fwsc") and not d.sha256


def test_resolve_blob_and_raw(env):
    L = env[1]
    d = L.resolve("https://github.com/Kashiwayo-593/uta-box/blob/main/fw/uta-teto.fwsc")
    assert d.download_url == "https://raw.githubusercontent.com/Kashiwayo-593/uta-box/main/fw/uta-teto.fwsc"
    d2 = L.resolve("https://raw.githubusercontent.com/a/b/main/x/thing-0.1.fwsc")
    assert d2.repo == "a/b" and d2.download_url.endswith("thing-0.1.fwsc")


def test_resolve_pages_unknown(env):
    L = env[1]
    d = L.resolve("https://somebody.github.io/weird-fw/emu/")
    assert d.repo == "somebody/weird-fw" and d.confidence["repo"] == L.GUESS and d.emulator_url


def test_resolve_direct_fwsc_and_stock(env):
    L = env[1]
    d = L.resolve("https://example.org/files/mysynth-1.2.fwsc")
    assert d.download_url and d.name == "mysynth" and not d.sha256
    d2 = L.resolve("https://yms-file-store.oss-cn-hongkong.aliyuncs.com/software/firmware/FM-1.fwsc")
    assert d2.key == "stock" and d2.sha256 and d2.product == "FM-1_016"


def test_resolve_local_file(env, tmp_path):
    L = env[1]
    raw = bytearray(20 * 0x30 + 10)
    for i, ch in enumerate("FM-1_977"):
        raw[i * 0x30 + 0x2F] = (ord(ch) + i + 1) & 0xFF
    for i in range(8, 20):
        raw[i * 0x30 + 0x2F] = 0x7D
    f = tmp_path / "arctic-scream-9.9.fwsc"
    f.write_bytes(bytes(raw))
    d = L.resolve(str(f))
    assert d.local_file == str(f) and d.product == "FM-1_977" and len(d.sha256) == 64
    assert d.repo.lower() == "polarpatch/arctic-scream-fm-1" and d.confidence["repo"] == L.GUESS
    d2 = L.resolve(f"file://{f}")
    assert d2.sha256 == d.sha256


def test_resolve_known_local_package(env, tmp_path):
    c, L = env[0], env[1]
    cat = c.load_snapshot()
    e = cat.by_key("orbit")
    fake = tmp_path / "x.fwsc"
    fake.write_bytes(b"data")
    import hashlib
    e.latest.sha256 = hashlib.sha256(b"data").hexdigest()
    d = L.resolve(str(fake), cat)
    assert d.key == "orbit" and any("Bekanntes Paket" in h for h in d.hints)


def test_resolve_nothing_and_unknown_name(env):
    L = env[1]
    assert L.resolve("   ").name == ""
    d = L.resolve("Gibtsnichtsynth 3000")
    assert d.confidence.get("name") == L.GUESS and not d.repo


def test_resolve_online_with_fixture(env, monkeypatch):
    c, L = env[0], env[1]

    def fake(req, timeout=0):
        u = req.full_url
        if "api.github.com" in u and "releases" in u:
            return FakeResp((FIX / "github_releases_someone_newsynth.json").read_bytes())
        if "api.github.com" in u:
            return FakeResp(b'{"description": "A new synth", "stargazers_count": 3}')
        if u.endswith("tools/fm1_install.py"):
            return FakeResp(b"")
        raise http_error(404)
    monkeypatch.setattr(c, "urlopen", fake)
    d = L.resolve("https://github.com/someone/newsynth", online=True)
    assert d.asset == "newsynth-{ver}.fwsc" and d.download_url.endswith("newsynth-2.1.0.fwsc")
    assert d.description == "A new synth" and d.protocol == "felucca" and d.installer == "tools/fm1_install.py"


def test_resolve_online_rate_limited(env, monkeypatch):
    c, L = env[0], env[1]
    monkeypatch.setattr(c, "urlopen", lambda req, timeout=0: (_ for _ in ()).throw(
        http_error(403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "0"})))
    d = L.resolve("https://github.com/someone/newsynth", online=True)
    assert d.repo == "someone/newsynth" and any("GitHub-Limit" in h for h in d.hints)


def test_validate_save_edit_remove(env):
    c, L, fw, config = env
    d = L.resolve("https://github.com/someone/cool/releases/download/v1.2.0/cool-1.2.0.fwsc")
    d.key = "melodee"
    assert any("gibt es schon" in e for e in L.validate(d))
    d.key, d.homepage, d.sha256 = "Böse Taste", "ftp://x", "123"
    errs = L.validate(d)
    assert len(errs) == 3
    d.key, d.homepage, d.sha256 = "cool", "", ""
    assert L.validate(d) == []
    L.save(d)
    s = fw.spec("cool")
    assert s and not s.builtin and s.download_url.endswith("cool-1.2.0.fwsc")
    e = L.draft_from_saved(L.own_entries()[0])
    e.name = "Cool 2"
    assert L.validate(e, editing_key="cool") == []
    L.save(e, editing_key="cool")
    assert fw.spec("cool").name == "Cool 2" and len(L.own_entries()) == 1
    assert L.remove("cool") and fw.spec("cool") is None


def test_extract(env):
    L = env[1]
    assert L.extract("schau: https://github.com/a/b.") == ("url", "https://github.com/a/b")
    assert L.extract("(https://x.org/y.fwsc)") == ("url", "https://x.org/y.fwsc")
    assert L.extract("~/Downloads/a.fwsc")[0] == "file"
    assert L.extract("orbit") == ("name", "orbit")


@pytest.mark.parametrize("text", ["x" * 5000, "/etc/passwd", "/dev/null", "/root", "\0abc", "javascript:alert(1)",
                                  "file:///" + "y" * 3000 + ".fwsc", "https://", "Ärger ü", "   ", "ftp://x/y.fwsc"])
def test_resolve_garbage_never_raises(env, text):
    _cat, L, _fw, _cfg = env
    d = L.resolve(text, online=False)
    assert d.hints or d.source == ""


# ------------------------------------------------------- more sources: Pages, fmhub.json, repo, search
INSTALLER_HTML = ('<html><script>const STOCK = [{version: "V15", product: "FM-1_015"}];\n'
                  'const meta = {"version": "3.0.3", "product": "FM-1_926", "pkg": "../../firmware/karma-3.0.3.fwsc", '
                  '"sha256": "' + "ab" * 32 + '"};</script></html>')


def _gh(routes: dict):
    """urlopen replacement: address -> answer (dict/list = JSON, str = text); everything else 404."""
    def fake(req, timeout=0):
        v = routes.get(req.full_url)
        if v is None:
            raise http_error(404)
        return FakeResp(v.encode() if isinstance(v, str) else json.dumps(v).encode())
    return fake


def test_installer_meta_parsing(env):
    c = env[0]
    base = "https://me.github.io/karma-fm1/webapp/installer/"
    m = c.installer_meta(INSTALLER_HTML, base)
    assert m == {"url": "https://me.github.io/karma-fm1/firmware/karma-3.0.3.fwsc", "version": "3.0.3",
                 "product": "FM-1_926", "sha256": "ab" * 32}
    # without embedded data: first reference to a package, but never the original FM-1.fwsc
    m = c.installer_meta('<a href="../FM-1.fwsc">o</a> <a href="pkg/cool-1.2.fwsc">x</a>', base)
    assert m["url"] == "https://me.github.io/karma-fm1/webapp/installer/pkg/cool-1.2.fwsc" and not m["sha256"]
    assert c.installer_meta("<html>nichts</html>", base) == {}


def test_resolve_online_installer_page_and_fmhub(env, monkeypatch):
    c, L = env[0], env[1]
    api = "https://api.github.com/repos/me/karma-fm1"
    monkeypatch.setattr(c, "urlopen", _gh({
        api: {"description": ""}, api + "/releases?per_page=15": [{"tag_name": "v3.0.3", "assets": []}],
        "https://raw.githubusercontent.com/me/karma-fm1/HEAD/fmhub.json":
            json.dumps({"what": "Groovebox", "author": "Ariel", "play": "https://me.github.io/karma-fm1/emu/"}),
        "https://me.github.io/karma-fm1/webapp/installer/": INSTALLER_HTML}))
    d = L.resolve("https://github.com/me/karma-fm1", online=True)
    assert d.download_url == "https://me.github.io/karma-fm1/firmware/karma-3.0.3.fwsc"
    assert d.sha256 == "ab" * 32 and d.product == "FM-1_926" and d.release_tag == "3.0.3"
    assert d.description == "Groovebox" and d.author == "Ariel" and d.emulator_url.endswith("/emu/")
    assert d.installer_url.endswith("/webapp/installer/") and not L.validate(d)


def test_resolve_online_package_in_repo_tree(env, monkeypatch):
    c, L = env[0], env[1]
    api = "https://api.github.com/repos/me/box"
    monkeypatch.setattr(c, "urlopen", _gh({
        api: {}, api + "/releases?per_page=15": [],
        api + "/git/trees/HEAD?recursive=1": {"tree": [
            {"path": "docs/firmware/box-1.2.fwsc", "type": "blob"}, {"path": "docs/firmware/box-1.10.fwsc", "type": "blob"},
            {"path": "stock/FM-1.fwsc", "type": "blob"}, {"path": "README.md", "type": "blob"}]}}))
    d = L.resolve("https://github.com/me/box", online=True)
    assert d.download_url == "https://raw.githubusercontent.com/me/box/HEAD/docs/firmware/box-1.10.fwsc"
    assert d.asset == "box-1.10.fwsc" and d.release_tag == "1.10" and not d.sha256
    assert any("box-1.2.fwsc" in h for h in d.hints)          # further packages named, FM-1.fwsc not
    assert not any("FM-1.fwsc" in h for h in d.hints)


def _search_url(query):
    return ("https://api.github.com/search/repositories?per_page=30&q="
            + urllib.parse.quote(query + " fork:true"))


def test_resolve_name_via_github_search(env, monkeypatch):
    c, L = env[0], env[1]
    api = "https://api.github.com/"
    monkeypatch.setattr(c, "urlopen", _gh({
        _search_url("karma fm-1"): {"items": [
            {"full_name": "x/karma-chameleon", "description": "a song"},
            {"full_name": "other/karmic-fm1", "description": "FM-1 firmware", "stargazers_count": 1},
            {"full_name": "me/karma-fm1", "description": "groovebox for the M-VAVE FM-1", "stargazers_count": 9},
            {"full_name": "copycat/karma-fm1", "description": None, "fork": True}]},
        api + "repos/me/karma-fm1": {}, api + "repos/me/karma-fm1/releases?per_page=15": [],
        "https://me.github.io/karma-fm1/webapp/installer/": INSTALLER_HTML}))
    for text in ("karma", "Karma FM1 Firmware"):
        d = L.resolve(text, c.load(), online=True)
        assert d.repo == "me/karma-fm1" and d.confidence["repo"] == L.GUESS and d.exact, text
        assert d.download_url.endswith("karma-3.0.3.fwsc") and d.source == "name"
        repos = [x["repo"] for x in d.candidates]
        assert "other/karmic-fm1" in repos and "x/karma-chameleon" not in repos      # no FM-1 relation
        assert "copycat/karma-fm1" not in repos                                      # a mere copy (fork)


def test_resolve_name_finds_fork_and_offers_choice(env, monkeypatch):
    """"dungeon tracker": the repo is a fork (only found with fork:true) and must come before GR!MM,
    which only shows up because of "dungeon synth" in the description."""
    c, L = env[0], env[1]
    api = "https://api.github.com/"
    rel = "https://github.com/BourbonWX/Dungeon-Tracker/releases/download/v1.0.0/dungeon-tracker-1.0.fwsc"
    monkeypatch.setattr(c, "urlopen", _gh({
        _search_url("dungeon tracker fm-1"): {"items": [
            {"full_name": "GRiMMBIENT/grimm", "description": "Dark ambient, dungeon synth firmware for the M-VAVE FM-1",
             "stargazers_count": 29},
            {"full_name": "BourbonWX/Dungeon-Tracker", "description": "Custom firmware for M-VAVE FM-1", "fork": True},
            {"full_name": "someone/grimm", "description": "Dark ambient, dungeon synth firmware for the M-VAVE FM-1",
             "fork": True}]},
        api + "repos/BourbonWX/Dungeon-Tracker": {},
        api + "repos/BourbonWX/Dungeon-Tracker/releases?per_page=15": [
            {"tag_name": "v1.0.0", "assets": [{"name": "dungeon-tracker-1.0.fwsc", "browser_download_url": rel}]}]}))
    cat = c.Catalog(entries=[c.Entry(key="grimm", name="GR!MM", repo="GRiMMBIENT/grimm",
                                     description="Dark ambient, dungeon synth")])
    d = L.resolve("dungeon tracker", cat, online=True)
    assert d.repo == "BourbonWX/Dungeon-Tracker" and d.download_url == rel and d.exact
    assert [x["repo"] for x in d.candidates] == ["BourbonWX/Dungeon-Tracker", "GRiMMBIENT/grimm"]
    assert d.candidates[0]["fork"] and d.candidates[0]["exact"] and not d.candidates[1]["exact"]
    # without network: not in the catalog, only the name
    d = L.resolve("dungeon tracker", cat, online=False)
    assert not d.repo and not d.candidates and not d.exact
    # ambiguous name: several candidates, none unique → hint to choose
    cat2 = c.Catalog(entries=[c.Entry(key="drift-a", name="DRIFT (A)", repo="a/drift-fm1"),
                              c.Entry(key="drift-b", name="DRIFT (B)", repo="b/drift-fm1")])
    d = L.resolve("drift", cat2, online=False)
    assert len(d.candidates) == 2 and not d.exact and any("auswählen" in h for h in d.hints)


def test_resolve_newer_release_than_catalog_and_multiple_builds(env, monkeypatch):
    c, L = env[0], env[1]
    cat = c.Catalog(entries=[c.Entry(key="orbit", name="ORBIT", repo="dspaudio/orbit", packages=[c.Package(
        label="ORBIT 0.7.1 (orbit.fwsc)", version="v0.7.1", sha256="cd" * 32,
        url="https://github.com/dspaudio/orbit/releases/download/v0.7.1/orbit.fwsc")])])
    dl = "https://github.com/dspaudio/orbit/releases/download/"
    api = "https://api.github.com/repos/dspaudio/orbit"
    monkeypatch.setattr(c, "urlopen", _gh({
        api: {}, api + "/releases?per_page=15": [
            {"tag_name": "v0.10.B", "assets": [{"name": "orbit-ble.fwsc", "browser_download_url": dl + "v0.10.B/orbit-ble.fwsc"}]},
            {"tag_name": "v0.10.0", "assets": [{"name": "orbit.fwsc", "browser_download_url": dl + "v0.10.0/orbit.fwsc"},
                                               {"name": "SHA256SUMS", "browser_download_url": dl + "v0.10.0/SHA256SUMS"}]}],
        dl + "v0.10.0/SHA256SUMS": "ef" * 32 + "  orbit.fwsc\n" + "12" * 32 + "  other.fwsc\n"}))
    d = L.resolve("https://github.com/dspaudio/orbit", cat, online=True)
    assert d.download_url == dl + "v0.10.0/orbit.fwsc" and d.asset == "orbit.fwsc"     # not the BLE build
    assert d.sha256 == "ef" * 32 and d.release_tag == "v0.10.0"
    assert any("neuere Version" in h for h in d.hints)


def test_by_name_ignores_filler_words(env):
    c = env[0]
    cat = c.Catalog(entries=[c.Entry(key="orbit", name="ORBIT", repo="dspaudio/orbit"),
                             c.Entry(key="fm1-chord", name="fm1-chord", repo="math0ne/fm1-chord")])
    assert cat.by_name("Orbit FM-1 Firmware").key == "orbit"
    assert cat.by_name("chord firmware").key == "fm1-chord"
    assert cat.by_name("fm1 chord").key == "fm1-chord"


def test_fetch_releases_skips_releases_without_our_package(env, monkeypatch):
    fw = env[2]
    s = fw.FirmwareSpec(key="orbit", name="ORBIT", repo="dspaudio/orbit", asset="orbit.fwsc", installer="", editor="")
    monkeypatch.setattr(fw, "_http_json", lambda url, timeout=15: [
        {"tag_name": "v0.10.B", "assets": [{"name": "orbit-ble.fwsc", "browser_download_url": "b"}]},
        {"tag_name": "v0.10.0", "assets": [{"name": "orbit.fwsc", "browser_download_url": "a"}]}])
    rels = fw.fetch_releases(s, 0)
    assert [r.tag for r in rels] == ["v0.10.0"] and fw.latest(rels, "stable").assets == {"orbit.fwsc": "a"}
