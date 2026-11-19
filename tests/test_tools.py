# SPDX-License-Identifier: GPL-3.0-only
"""The bundled tools (fm1companion/tools): pins, store, M-Flasher runner and parser (with tests/fake_mflasher.py,
synthetic values only), the update check and the "Update" download."""
from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import sys
import tarfile
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fm1companion.tools import manifest as mf  # noqa: E402
from fm1companion.tools import mflasher_parse as mp  # noqa: E402
from fm1companion.tools import runner, store as st, updates  # noqa: E402

FAKE = ROOT / "tests" / "fake_mflasher.py"
UID = "0123456789abcdef"                               # synthetic unit id (the fake's default)
ARCH = mf.arch() or "x86_64"


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def fake_binary(path: Path) -> bytes:
    """the fake as an executable for this Python"""
    body = f"#!{sys.executable}\n" + FAKE.read_text("utf-8").split("\n", 1)[1]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, "utf-8")
    path.chmod(0o755)
    return body.encode()


@pytest.fixture
def env(tmp_path, monkeypatch):
    """a Store whose bundled M-Flasher is the fake (manifest copy with the fake's SHA-256)"""
    data = copy.deepcopy(mf.load())
    ver = data["tools"]["m-flasher"]["version"]
    binp = tmp_path / "tools-bin" / mf.mflasher_relpath(ver, ARCH)
    b = fake_binary(binp)
    data["tools"]["m-flasher"]["arch"][ARCH].update(binary_sha256=sha(b), binary_size=len(b))
    state = tmp_path / "fake-state.json"
    state.write_text("{}")
    monkeypatch.setenv("FAKE_MFLASHER_STATE", str(state))
    monkeypatch.delenv("FM1C_NO_NET", raising=False)
    monkeypatch.setattr(updates, "cache_file", lambda: tmp_path / "tools-check.json")
    s = st.Store(bin_dir=tmp_path / "tools-bin", updates_dir=tmp_path / "upd", data=data, machine=ARCH)

    class E:
        pass
    e = E()
    e.store, e.data, e.state, e.bin, e.tmp = s, data, state, binp, tmp_path
    e.set = lambda **kw: state.write_text(json.dumps(kw))
    e.logs = tmp_path / "logs"
    e.journal = __import__("fm1companion.flash.journal", fromlist=["Journal"]).Journal(tmp_path / "journal")
    return e


def run(e, args, **kw):
    kw.setdefault("log_dir", e.logs)
    kw.setdefault("journal", e.journal)
    return runner.run_mflasher(args, store=e.store, **kw)


# ------------------------------------------------------------ pins

def test_manifest_pins_every_file_with_tag_commit_url_size_sha_licence():
    data = mf.load()
    assert set(data["tools"]) == set(mf.KEYS)
    for key, t in data["tools"].items():
        for f in ("title", "repo", "license", "version", "tag", "commit", "licence_files"):
            assert t.get(f), (key, f)
        assert len(t["commit"]) == 40 and int(t["commit"], 16) >= 0
        files = list(t["arch"].values()) if key == "m-flasher" else list(t["files"].values())
        assert files
        for e in files:
            assert e["url"].startswith(f"https://github.com/{t['repo']}/releases/download/{t['tag']}/"), e["url"]
            assert len(e["sha256"]) == 64 and int(e["sha256"], 16) >= 0 and e["size"] > 0
            assert e["verified"] in ("download", "SHA256SUMS", "source-tree")
        for name in t["licence_files"]:
            assert (mf.BUNDLED / key / name).is_file(), (key, name)
    m = data["tools"]["m-flasher"]
    assert set(m["arch"]) == {"aarch64", "x86_64"} and "armhf" not in m["arch"]
    assert m["version"] in m["write_verified"]
    for a, e in m["arch"].items():
        assert e["asset"] == f"m-flasher-{m['version']}-linux-{a}.tar.gz" and e["member"] == "m-flasher/m-flasher"
        assert len(e["binary_sha256"]) == 64 and e["binary_size"] > 1_000_000


def test_bundled_files_match_the_pins_and_upstream_sums():
    for key in ("m-bootloader", "sloop3"):
        for name, e in mf.bundled_files(key).items():
            b = (mf.BUNDLED / key / name).read_bytes()
            assert len(b) == e["size"] and sha(b) == e["sha256"], (key, name)
    sums = (mf.BUNDLED / "m-bootloader" / "SHA256SUMS").read_text()
    boot = mf.bundled_files("m-bootloader")["fm1-uboot.boot"]["sha256"]
    assert f"{boot}  fm1-uboot.boot" in sums.splitlines()
    assert boot.startswith("677ae52a") and boot.endswith("49e4")          # the owner's check (PROMPT.md)
    assert len((mf.BUNDLED / "m-bootloader" / "fm1-uboot.boot").read_bytes()) <= updates.MAX_BOOT
    assert mf.tool("sloop3")["prerelease"] is True and mf.tool("sloop3")["tag"].endswith("-beta")


def test_sloop3_area_source_is_offered_with_every_release():
    """GPL-3.0: the area's corresponding source is the tarball of the same commit, which the release workflow
    already attaches for the bundled SLOOP 3 editor (tools/make_source_tarballs.py reads the sims manifests)"""
    sys.path.insert(0, str(ROOT / "tools"))
    import make_source_tarballs
    commit = mf.tool("sloop3")["commit"]
    assert ("sloop3-fm1", commit) in make_source_tarballs.wanted()
    assert commit in (mf.BUNDLED / "sloop3" / "NOTICE").read_text()


def test_help_contract_matches_the_fake():
    """the pinned --help hash (from M-Flasher's source at the tag) equals what the fake prints, so the fake speaks
    the pinned command line"""
    import subprocess
    out = subprocess.run([sys.executable, str(FAKE), "--help"], capture_output=True).stdout
    assert sha(out) == mf.tool("m-flasher")["help_sha256"]


def test_no_binary_in_git_and_tools_bin_ignored():
    assert "tools-bin/" in (ROOT / ".gitignore").read_text().splitlines()
    import subprocess
    r = subprocess.run(["git", "ls-files", "tools-bin"], cwd=ROOT, capture_output=True, text=True)
    if r.returncode == 0:
        assert r.stdout.strip() == ""


# ------------------------------------------------------------ architecture

@pytest.mark.parametrize("machine,arch", [("aarch64", "aarch64"), ("arm64", "aarch64"), ("x86_64", "x86_64"),
                                          ("AMD64", "x86_64"), ("armv7l", ""), ("armv6l", ""), ("i686", "")])
def test_arch(machine, arch):
    assert mf.arch(machine) == arch


def test_armhf_gets_a_clear_not_available(tmp_path):
    s = st.Store(bin_dir=tmp_path, updates_dir=tmp_path / "u", machine="armv7l")
    assert s.bundled("m-flasher") is None
    why = runner.unavailable_reason(s)
    assert "armv7l" in why and "64" in why                          # (any language)
    with pytest.raises(runner.ToolUnavailable):
        runner.run_mflasher(["find"], store=s, log_dir=tmp_path)


def test_source_checkout_without_binaries_says_so(tmp_path):
    s = st.Store(bin_dir=tmp_path / "empty", updates_dir=tmp_path / "u", machine="x86_64")
    assert "fetch_tools.py" in runner.unavailable_reason(s)


# ------------------------------------------------------------ parser

def test_parse_find_all_forms():
    r = mp.parse_find("/dev/sg1\tWL82 UBOOT1.00 (/dev/sg1)\nmidi\tFM-1 (running; download-mode switches it)\n")
    assert [u.device for u in r.download] == ["/dev/sg1"] and r.midi == ["FM-1"] and not r.sg_missing
    assert mp.parse_find("", "a unit is in download mode, but Linux's SCSI generic driver … not loaded").sg_missing
    assert mp.parse_find("weird line\n").other == ["weird line"]


def test_parse_info_block():
    out = ("Connecting to /dev/sg1\nChecked: the chip's CRC32 of the flash matches the read\n"
           "  model       FM-1\n  bootloader  m-bootloader 2.4-395e6cc (fm1)\n  firmware    SLOOP 2.8\n"
           "  app         OK\n  chip key    0x980F\n  flash       0xB36014\n  unit id     " + UID + "\n"
           "  loader      M-Flasher 1.3, CPU at 96 MHz\n    * strong reason\n    - weak reason\n")
    i = mp.parse_info(out)
    assert (i.model, i.chip_key, i.flash_id, i.unit_id) == ("FM-1", 0x980F, 0xB36014, UID)
    assert i.m_bootloader and i.crc_checked and i.evidence == [(True, "strong reason"), (False, "weak reason")]
    assert mp.parse_info(out.replace(UID, "–")).unit_id == ""
    with pytest.raises(mp.ParseError):
        mp.parse_info(out.replace("  flash       0xB36014\n", ""))
    with pytest.raises(mp.ParseError):
        mp.parse_info(out.replace(UID, "not-an-id"))
    assert mp.refusal("\r  10%\r 100%\nRefused: more than one unit\n") == "more than one unit"


# ------------------------------------------------------------ runner with the fake

def test_find_and_info_through_the_fake(env):
    r, found = runner.find(store=env.store, log_dir=env.logs, journal=env.journal)
    assert r.ok and [u.device for u in found.download] == ["/dev/sg1"] and r.source == "bundled"
    r, info = runner.info("/dev/sg1", store=env.store, log_dir=env.logs, journal=env.journal)
    assert r.ok and info.unit_id == UID and info.flash_id == 0x856014 and info.chip_key == 0x980F
    env.set(mode="midi")
    _r, found = runner.find(store=env.store, log_dir=env.logs, journal=env.journal)
    assert found.download == [] and found.midi == ["FM-1"]
    env.set(mode="sg-missing")
    assert runner.find(store=env.store, log_dir=env.logs, journal=env.journal)[1].sg_missing
    env.set(fault="refuse")
    r, info = runner.info("/dev/sg1", store=env.store, log_dir=env.logs, journal=env.journal)
    assert r.rc == 1 and info is None and mp.refusal(r.err) == "the unit did not answer"


def test_flight_log_masks_the_unit_id(env):
    r, info = runner.info("/dev/sg1", store=env.store, log_dir=env.logs, journal=env.journal)
    assert info.unit_id == UID and r.log_path and r.log_path.name.startswith("tool-")
    text = r.log_path.read_text()
    assert UID not in text and "…" + UID[-4:] in text and "M-Flasher 0.12.1 (bundled)" in text
    assert runner.mask_ids(f"unit id     {UID}") == "unit id     …" + UID[-4:]


@pytest.mark.parametrize("args", [["backup"], ["restore", "x.bin"], ["firmware", "p.fwsc"], ["bootloader"],
                                  ["download-mode"], ["restart"], ["run-ram", "x"], ["info", "--yes"],
                                  ["info", "--device", "/dev/sda"], ["info", "--device", "/dev/sg1", "--yes"],
                                  ["find", "--yes"], []])
def test_write_and_unknown_commands_are_refused(env, args):
    with pytest.raises(runner.ToolError):
        run(env, args)
    assert "calls" not in json.loads(env.state.read_text())           # the binary never ran


def test_changed_binary_is_never_run(env):
    env.bin.write_text(env.bin.read_text() + "\n# changed\n")
    with pytest.raises(runner.ToolUnavailable):
        run(env, ["find"])
    assert "calls" not in json.loads(env.state.read_text())


def test_timeout_and_cancel(env):
    env.set(fault="hang")
    t0 = time.monotonic()
    r = run(env, ["find"], timeout=1.0)
    assert r.timed_out and not r.ok and time.monotonic() - t0 < 10
    ev = threading.Event()
    threading.Timer(0.5, ev.set).start()
    r = run(env, ["find"], cancel=ev)
    assert r.cancelled and not r.ok


def test_not_while_another_job_holds_the_lock(env):
    env.journal.acquire()
    try:
        from fm1companion.flash import journal as fj
        with pytest.raises(runner.ToolBusy):
            run(env, ["find"], journal=fj.Journal(env.tmp / "journal"))
    finally:
        env.journal.release()
    assert run(env, ["find"]).ok                                     # free again afterwards
    assert run(env, ["--version"]).out.strip() == "0.12.1"           # (no lock needed)


# ------------------------------------------------------------ updates in the store

def stage_update(e, version: str, binary: bytes | None = None, contract_known=True):
    d = e.store.updates_dir / "m-flasher" / version / ARCH
    b = fake_binary(d / "m-flasher") if binary is None else binary
    if binary is not None:
        (d / "m-flasher").write_bytes(binary)
    meta = {"version": version, "main": f"{ARCH}/m-flasher", "contract_known": contract_known,
            "files": {f"{ARCH}/m-flasher": {"sha256": sha(b), "size": len(b)}}}
    (d.parent / "meta.json").write_text(json.dumps(meta))


def test_new_version_read_only_until_proven_and_fallback_when_broken(env):
    stage_update(env, "0.13.0")
    c, _ = env.store.select("m-flasher")
    assert (c.version, c.source, c.proven) == ("0.13.0", "update", False)
    w, _ = env.store.select("m-flasher", write=True)
    assert w.version == "0.12.1" and w.source == "bundled"          # writes stay on the verified version
    r, info = runner.info("/dev/sg1", store=env.store, log_dir=env.logs, journal=env.journal)
    assert r.version == "0.13.0" and info is not None
    c, _ = env.store.select("m-flasher")
    assert c.proven                                                  # one clean info run proves it
    assert env.store.select("m-flasher", write=True)[0].version == "0.12.1"   # still not for writes (owner rule)
    # a later, broken update: used once, then the previous one again
    stage_update(env, "0.14.0")
    env.set(fault="crash")
    r = run(env, ["find"])
    assert r.version == "0.14.0" and not r.ok
    c, problems = env.store.select("m-flasher")
    assert c.version == "0.13.0" and any("0.14.0" in p for p in problems)


def test_write_verified_needs_manifest_entry_and_known_contract(env):
    env.data["tools"]["m-flasher"]["write_verified"].append("0.13.0")
    stage_update(env, "0.13.0", contract_known=False)
    env.store.mark("m-flasher", "0.13.0", ok=True)
    assert env.store.select("m-flasher", write=True)[0].version == "0.12.1"
    stage_update(env, "0.13.0", contract_known=True)
    assert env.store.select("m-flasher", write=True)[0].version == "0.13.0"


# ------------------------------------------------------------ update check

def release(tag, assets, pre=False, body="Release notes"):
    return {"tag_name": tag, "prerelease": pre, "draft": False, "published_at": "2026-12-01T00:00:00Z",
            "body": body, "html_url": f"https://github.com/x/y/releases/tag/{tag}",
            "assets": [{"name": n, "browser_download_url": f"https://github.com/x/y/releases/download/{tag}/{n}",
                        "digest": f"sha256:{d}" if d else None} for n, d in assets.items()]}


def test_offers_pick_the_newest_release_with_the_right_file():
    d = "a" * 64
    rels = [release("v0.14.0-rc.1", {"m-flasher-0.14.0-rc.1-linux-aarch64.tar.gz": d}, pre=True),
            release("v0.13.0", {"m-flasher-0.13.0-linux-aarch64.tar.gz": d, "m-flasher-0.13.0-linux-x86_64.tar.gz": d}),
            release("v0.12.1", {"m-flasher-0.12.1-linux-aarch64.tar.gz": d})]
    o = updates.offers_from_releases("m-flasher", rels, "0.12.1", "aarch64", allow_pre=False)
    assert (o.version, o.asset, o.digest, o.notes) == ("0.13.0", "m-flasher-0.13.0-linux-aarch64.tar.gz", d,
                                                       "Release notes")
    assert updates.offers_from_releases("m-flasher", rels, "0.12.1", "aarch64", True).version == "0.14.0-rc.1"
    assert updates.offers_from_releases("m-flasher", rels, "0.13.0", "aarch64", False) is None
    assert updates.offers_from_releases("m-flasher", rels, "0.12.1", "", False) is None        # armhf: nothing
    s3 = [release("v3.5.0-beta", {"sloop3-3.5.0.area": d, "installer-sloop3-3.5.0.html": d}, pre=True)]
    o = updates.offers_from_releases("sloop3", s3, "3.4.0", "aarch64", allow_pre=False)
    assert (o.version, o.asset) == ("3.5.0", "sloop3-3.5.0.area")                              # all betas
    assert updates.offers_from_releases("sloop3", [release("v3.4.0-beta", {"sloop3-3.4.0.area": d})], "3.4.0",
                                        "x86_64", False) is None
    mb = [release("v2.5", {"fm1-uboot.boot": d, "SHA256SUMS": d})]
    o = updates.offers_from_releases("m-bootloader", mb, "2.4", "x86_64", False)
    assert o.asset == "fm1-uboot.boot" and o.sums_url.endswith("/SHA256SUMS")


class S:
    check_on_start = True
    tools_check = "daily"
    channel = "stable"


def test_automatic_checks_obey_the_switches(monkeypatch, tmp_path):
    monkeypatch.setattr(updates, "busy", lambda: False)
    monkeypatch.delenv("FM1C_NO_NET", raising=False)
    s = S()
    assert updates.auto_check_allowed(s)[0]
    s.check_on_start = False
    assert not updates.auto_check_allowed(s)[0]
    s.check_on_start, s.tools_check = True, "off"
    assert not updates.auto_check_allowed(s)[0] and not updates.due(s, cache={})
    s.tools_check = "daily"
    monkeypatch.setenv("FM1C_NO_NET", "1")
    assert not updates.auto_check_allowed(s)[0]
    with pytest.raises(updates.UpdateError):
        updates.check(s, fetch=lambda url: pytest.fail("went online"))
    monkeypatch.delenv("FM1C_NO_NET")
    monkeypatch.setattr(updates, "busy", lambda: True)
    assert not updates.auto_check_allowed(s)[0]


def test_due_daily_weekly_start(monkeypatch):
    s = S()
    now = 1_900_000_000.0
    assert updates.due(s, now, {"last": now - 25 * 3600}) and not updates.due(s, now, {"last": now - 3600})
    s.tools_check = "weekly"
    assert not updates.due(s, now, {"last": now - 3 * 86400}) and updates.due(s, now, {"last": now - 8 * 86400})
    s.tools_check = "bogus"                                          # unknown value: the default (daily)
    assert updates.mode(s) == "daily"
    s.tools_check = "start"
    monkeypatch.setattr(updates, "_started_check_done", False)
    assert updates.due(s, now, {"last": now})
    monkeypatch.setattr(updates, "_started_check_done", True)
    assert not updates.due(s, now, {"last": 0})


def test_check_stores_offers_and_notifies_each_version_once(env, monkeypatch):
    calls = []

    def fetch(url):
        calls.append(url)
        if "m-flasher" in url:
            return [release("v0.13.0", {f"m-flasher-0.13.0-linux-{ARCH}.tar.gz": "b" * 64})]
        return []
    out = updates.check(S(), store=env.store, fetch=fetch)
    assert len(calls) == 3 and all(u.startswith("https://api.github.com/repos/") for u in calls)
    assert out["m-flasher"].version == "0.13.0" and out["sloop3"] is None
    assert updates.cached_offer("m-flasher", env.store).version == "0.13.0"
    assert [o.version for o in updates.new_to_notify(out)] == ["0.13.0"]
    assert updates.new_to_notify(out) == []
    assert updates.load_cache()["last"] > 0


def tarball(binary: bytes, version: str, extra=None) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in [("m-flasher/m-flasher", binary), ("m-flasher/LICENSE", b"MIT"), *(extra or [])]:
            ti = tarfile.TarInfo(name)
            ti.size, ti.mode = len(data), 0o755
            tar.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def offer_for(data: bytes, version="0.13.0", digest=None) -> updates.Offer:
    name = f"m-flasher-{version}-linux-{ARCH}.tar.gz"
    return updates.Offer("m-flasher", "v" + version, version, asset=name, url=f"https://example.invalid/{name}",
                         digest=sha(data) if digest is None else digest)


def test_update_download_is_verified_staged_and_keeps_the_previous(env, monkeypatch):
    monkeypatch.setattr(updates, "busy", lambda: False)
    env.set(version="0.13.0")                                        # the fake reports the new version
    binary = (env.bin.read_bytes())
    blob = tarball(binary, "0.13.0")
    fetch = lambda url, dest: dest.write_bytes(blob)                 # noqa: E731
    c = updates.download_update(offer_for(blob), env.store, fetch)
    assert (c.version, c.source, c.proven, c.contract_known) == ("0.13.0", "update", False, True)
    assert env.bin.exists()                                           # the bundled version stays
    assert env.store.select("m-flasher")[0].version == "0.13.0"
    assert env.store.select("m-flasher", write=True)[0].version == "0.12.1"
    assert not list((env.store.updates_dir / "m-flasher").glob(".staging-*"))
    with pytest.raises(updates.UpdateError):                          # the same version twice
        updates.download_update(offer_for(blob), env.store, fetch)


@pytest.mark.parametrize("case", ["digest", "nodigest", "member", "version", "busy", "nonet"])
def test_update_download_refusals_keep_nothing(env, monkeypatch, case):
    monkeypatch.setattr(updates, "busy", lambda: case == "busy")
    if case == "nonet":
        monkeypatch.setenv("FM1C_NO_NET", "1")
    env.set(version="0.13.0" if case != "version" else "0.99.0")
    blob = tarball(env.bin.read_bytes(), "0.13.0")
    if case == "member":
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            ti = tarfile.TarInfo("m-flasher/m-flasher")
            ti.type, ti.linkname = tarfile.SYMTYPE, "/bin/sh"
            tar.addfile(ti)
        blob = buf.getvalue()
    o = offer_for(blob, digest={"digest": "c" * 64, "nodigest": ""}.get(case))
    with pytest.raises(updates.UpdateError):
        updates.download_update(o, env.store, lambda url, dest: dest.write_bytes(blob))
    assert [c.source for c in env.store.candidates("m-flasher")] == ["bundled"]
    assert not list((env.store.updates_dir / "m-flasher").glob("*")) if (env.store.updates_dir / "m-flasher").exists() \
        else True


def test_changed_command_line_is_read_only(env, monkeypatch):
    monkeypatch.setattr(updates, "busy", lambda: False)
    env.set(version="0.13.0")
    changed = env.bin.read_text().replace("--board fm1|smk37", "--board fm1|smk37|new")
    blob = tarball(changed.encode(), "0.13.0")
    c = updates.download_update(offer_for(blob), env.store, lambda url, dest: dest.write_bytes(blob))
    assert c.contract_known is False
    env.data["tools"]["m-flasher"]["write_verified"].append("0.13.0")
    env.store.mark("m-flasher", "0.13.0", ok=True)
    assert env.store.select("m-flasher", write=True)[0].version == "0.12.1"


def test_m_bootloader_update_needs_sha256sums(env, monkeypatch):
    monkeypatch.setattr(updates, "busy", lambda: False)
    boot = b"\x01" * 1000
    sums = f"{sha(boot)}  fm1-uboot.boot\n".encode()
    files = {"fm1-uboot.boot": boot, "SHA256SUMS": sums}
    fetch = lambda url, dest: dest.write_bytes(files[url.rsplit("/", 1)[1]])    # noqa: E731
    o = updates.Offer("m-bootloader", "v2.5", "2.5", asset="fm1-uboot.boot", url="https://x/fm1-uboot.boot",
                      digest=sha(boot), sums_url="https://x/SHA256SUMS", sums_digest=sha(sums))
    c = updates.download_update(o, env.store, fetch)
    assert c.version == "2.5" and c.verify()[0]
    assert env.store.select("m-bootloader")[0].version == "2.5"
    files["SHA256SUMS"] = f"{'d' * 64}  fm1-uboot.boot\n".encode()
    o2 = updates.Offer("m-bootloader", "v2.6", "2.6", asset="fm1-uboot.boot", url="https://x/fm1-uboot.boot",
                       digest=sha(boot), sums_url="https://x/SHA256SUMS")
    with pytest.raises(updates.UpdateError):
        updates.download_update(o2, env.store, fetch)


def test_status_and_licence(env):
    rows = {r.key: r for r in updates.status(env.store)}
    assert rows["m-flasher"].active == "0.12.1" and rows["m-flasher"].verified
    assert rows["m-bootloader"].active == "2.4" and rows["sloop3"].active == "3.4.0"
    assert "Permission is hereby granted" in updates.licence_text("m-flasher", env.store)
    assert "GNU GENERAL PUBLIC LICENSE" in updates.licence_text("sloop3", env.store)
    env.bin.unlink()
    rows = {r.key: r for r in updates.status(env.store)}
    assert not rows["m-flasher"].verified and rows["m-flasher"].unavailable
