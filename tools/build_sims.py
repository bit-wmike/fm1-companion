#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Build the FM-1 simulators that ship inside FM-1 Companion (fm1companion/sims/<name>/).

Every simulator is the firmware's own synth/UI C code compiled to WebAssembly (no CPU emulation), run in
an AudioWorklet by the upstream page. The result is architecture independent: the Raspberry Pi never
compiles anything. Run this in a development container, not on the user's machine.

  python3 tools/build_sims.py [--only fm1-open,melodee] [--work DIR] [--wasi-sdk DIR]
                              [--repo fm1-open=/tmp/fm1_open] [--repo melodee=/tmp/melodee]
  python3 tools/build_sims.py --target melodee-studio [--ref v1.6.0-rc.8 ...] [--repo melodee=/tmp/melodee]

Needs: git, Python with Pillow + fontTools (Melodee's header generators), and WASI SDK 27
(downloaded from GitHub into --work when --wasi-sdk is not given; ~125 MB).

Simulators:
  fm1-open  M-VAVE FM1 Open V14 workbench (simulator + projects/voices/samples + manual). Port of the
            Build-Simulator step of tools/Build-Web.ps1: same sources, same flags, same WASI SDK 27.
            The wasm is served next to the page (FM1_SIMULATOR.url) instead of base64 inside it.
            The firmware-update tab is removed: FM1_PACKAGE = null (nothing to flash) and the tab is hidden.
  melodee   Melodee v1.6.1 browser emulator (web/emu). Upstream builds it with Emscripten; emsdk cannot
            download its LLVM here, so it is compiled with WASI SDK clang in reactor mode. The only change is
            an include shim (tools/wasm_shim/sys/wait.h) for the host-test harness' fork/pipe helpers, which
            the emulator never calls. Upstream files stay untouched.
  melodee-studio  (--target) the studio twin: Melodee's firmware for the host WITH NOR flash and the editor SysEx
            (tools/studio/melodee_studio.c), one folder per firmware version under sims/melodee-studio/<version>/.
            New version: python3 tools/build_sims.py --target melodee-studio --ref <tag> --repo melodee=<clone>
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "fm1companion" / "sims"
SHIM = ROOT / "tools" / "wasm_shim"
WASI_URL = "https://github.com/WebAssembly/wasi-sdk/releases/download/wasi-sdk-27/wasi-sdk-27.0-x86_64-linux.tar.gz"
WASI_SHA256 = "b7d4d944c88503e4f21d84af07ac293e3440b1b6210bfd7fe78e0afd92c23bc2"   # recorded 2026-10-30
HOST_TAG = '<script src="../_host/fm1sim-host.js"></script>'

SIMS = {
    "fm1-open": {"repo": "https://github.com/m-vave/fm1_open", "ref": "4c7496daac04a95ff10d1afdcc8f9ead2dbabc41",
                 "title": "M-VAVE FM1 Open (aus dem offiziellen Quellcode gebaut)", "version": "V14",
                 "license": "GPL-3.0-only WITH GPL-3.0-linking-exception (APP); see THIRD_PARTY.md"},
    "melodee": {"repo": "https://github.com/keremimo/melodee", "ref": "v1.6.1",
                "title": "Melodee", "version": "1.6.1", "license": "GPL-3.0-only"},
}


SUITE_REPO = "https://github.com/bit-wmike/fm1-companion"


def source_offer(project: str, commit: str, extra: str = "") -> str:
    """GPL-3.0 §6: written offer + exact revision of the sources (appended to every NOTICE)."""
    return (
        "\nCorresponding Source (GNU GPL 3.0, section 6)\n"
        f"The complete corresponding source of this build is {project} at commit {commit}\n"
        f"together with FM-1 Companion's build script tools/build_sims.py{extra}. A tarball of exactly this commit\n"
        f"({project.rsplit('/', 1)[-1]}-{commit}.tar.gz, made with tools/make_source_tarballs.py) is attached to every\n"
        f"FM-1 Companion release that ships this build: {SUITE_REPO}/releases\n"
        "Written offer: for at least three years after the last release that ships this build, the maintainers of\n"
        f"FM-1 Companion will give anyone a copy of that corresponding source on request ({SUITE_REPO}/issues),\n"
        "free of charge apart from the cost of physically performing the distribution.\n")


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def run(cmd, cwd=None, **kw):
    print("  $", " ".join(str(c) for c in cmd)[:200], flush=True)
    return subprocess.run([str(c) for c in cmd], cwd=cwd, check=True, **kw)


def wasi_sdk(work: Path, given: str | None) -> Path:
    if given:
        return Path(given)
    d = work / "wasi-sdk-27.0-x86_64-linux"
    if (d / "bin" / "clang").exists():
        return d
    arc = work / "wasi-sdk-27.tar.gz"
    if not arc.exists() or sha(arc) != WASI_SHA256:
        print("lade WASI SDK 27 …")
        urllib.request.urlretrieve(WASI_URL, arc)
    if sha(arc) != WASI_SHA256:
        raise SystemExit("WASI SDK: SHA-256 stimmt nicht")
    with tarfile.open(arc) as tf:
        tf.extractall(work)
    return d


def source(name: str, work: Path, local: str | None, ref: str | None = None, repo_url: str | None = None,
           tree_name: str | None = None) -> tuple[Path, str]:
    """-> (extracted tree at the pinned ref, full commit). Reads local repos only (git archive)."""
    spec = SIMS.get(name, {})
    ref = ref or spec["ref"]
    repo_url = repo_url or spec["repo"]
    repo = Path(local) if local else work / f"{name}.git"
    if not local and not repo.exists():
        run(["git", "clone", "--bare", "--filter=blob:none", repo_url + ".git", repo])
    commit = subprocess.run(["git", "rev-parse", ref + "^{commit}"], cwd=repo, capture_output=True,
                            text=True).stdout.strip()
    if not commit:
        run(["git", "fetch", "--tags", "origin"], cwd=repo)
        commit = subprocess.run(["git", "rev-parse", ref + "^{commit}"], cwd=repo, capture_output=True,
                                text=True, check=True).stdout.strip()
    tree = work / (tree_name or f"{name}-src")
    if tree.exists():
        shutil.rmtree(tree)
    tree.mkdir(parents=True)
    data = subprocess.run(["git", "archive", "--format=tar", commit], cwd=repo, capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(data)) as tf:
        try:
            tf.extractall(tree, filter="data")
        except TypeError:
            tf.extractall(tree)
    return tree, commit


def clang_version(cc: Path) -> str:
    return subprocess.run([cc, "--version"], capture_output=True, text=True).stdout.splitlines()[0]


# ------------------------------------------------------------------ FM1 Open
def build_fm1_open(work, sdk, local):
    src, commit = source("fm1-open", work, local)
    out = OUT / "fm1-open"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    names = sorted(str(p.relative_to(src)).replace(os.sep, "/") for d in ("app", "web", "third_party/synth")
                   for p in (src / d).rglob("*") if p.suffix in (".c", ".h", ".inc") and p.is_file()
                   and not str(p.relative_to(src)).startswith("app/board/"))
    cfiles = [n for n in names if n.endswith(".c")]
    cc = sdk / "bin" / "clang"
    opts = [f"--sysroot={sdk / 'share' / 'wasi-sysroot'}", "-std=c11", "-O2", "-fwrapv", "-fno-fast-math",
            "-ffp-contract=off", "-DFM1_HOST_TEST", "-DFM1_OPEN_WEB", "-Wall", "-Wextra", "-Werror",
            "-Wno-unused-function", "-Wno-array-parameter", "-mexec-model=reactor"]
    opts += ["-I" + str(src / d) for d in ("web/engine", "web/synth", "third_party/synth", "sdk/include", "app/synth", "app")]
    wasm = out / "simulator.wasm"
    run([cc, *opts, *[src / n for n in cfiles], "-Wl,--strip-all", "-Wl,-z,stack-size=131072",
         "-Wl,--initial-memory=4194304", "-Wl,--max-memory=33554432", "-o", wasm])
    version = re.search(r'FM1_RELEASE_VERSION "(V\d+)"', (src / "app/version.h").read_text()).group(1)
    source_url = f"https://github.com/m-vave/fm1_open/archive/{commit}.tar.gz"
    sim = {"schema": 1, "release_version": version, "sha256": sha(wasm), "source_sha256": "",
           "sample_bank": "synthesized-demo", "url": "simulator.wasm", "bytes": wasm.stat().st_size}
    page = (src / "sdk/Update.html.in").read_text("utf-8")
    notices = [f"APP: GPLv3 with the FM1 SDK linking exception; no warranty. Source: {source_url}",
               "Built for FM-1 Companion by tools/build_sims.py (firmware update tab removed, wasm served "
               "next to the page, host script added). No device firmware is included."]
    for f in ("THIRD_PARTY.md", "LICENSE.md"):
        notices.append(f"\n===== {f} =====\n" + (src / f).read_text("utf-8"))
        shutil.copy(src / f, out / f)
    rep = {"/*FM1_SOURCE_URL*/": html.escape(source_url),
           "/*FM1_PAYLOAD*/": "const FM1_PACKAGE = null;",
           "/*FM1_SIMULATOR*/": "const FM1_SIMULATOR = " + json.dumps(sim, separators=(",", ":")) + ";",
           "/*FM1_I18N*/": (src / "sdk/I18n.js").read_text("utf-8"),
           "/*FM1_SCRIPT*/": (src / "sdk/WebUpdate.js").read_text("utf-8"),
           "/*FM1_SIM_SCRIPT*/": (src / "sdk/Simulator.js").read_text("utf-8"),
           "/*FM1_SAMPLE_SCRIPT*/": (src / "sdk/Samples.js").read_text("utf-8"),
           "/*FM1_NOTICES*/": html.escape("\n".join(notices))}
    for k, v in rep.items():
        page = page.replace(k, v)
    if re.search(r"/\*FM1_[A-Z_]+\*/", page):
        raise SystemExit("fm1-open: Platzhalter übrig")
    # Firmware updates belong to the app's installer: hide the tab (it has no package anyway).
    guard = ("<style>#mode-update,#tab-update,#stock-restore,#open-firmware,#force-update{display:none!important}</style>"
             "<script>window.__FM1SIM_ID='fm1-open';document.addEventListener('fm1-view-change',()=>{"
             "const u=document.getElementById('tab-update');if(u&&!u.hidden)document.getElementById('mode-sim').click();});"
             "</script>" + HOST_TAG)
    page = page.replace('<meta charset="utf-8">', '<meta charset="utf-8">\n' + guard, 1)
    (out / "index.html").write_text(page, "utf-8")
    (out / "NOTICE").write_text(
        "M-VAVE FM1 Open V14 – browser workbench, built from the unmodified sources at\n"
        f"{SIMS['fm1-open']['repo']} commit {commit} ({source_url}).\n"
        "Licence of the APP and web sources: GPL-3.0-only WITH the FM1 SDK linking exception (LICENSE.md);\n"
        "sample_metadata.inc MIT; DX7 engine derived from msfa/Dexed (Apache-2.0); further notices in THIRD_PARTY.md.\n"
        "Changes for FM-1 Companion (tools/build_sims.py): the wasm is a separate file, the firmware update tab\n"
        "is hidden and has no firmware package, ../_host/fm1sim-host.js is loaded (recording, MIDI, latency).\n"
        + source_offer(SIMS["fm1-open"]["repo"], commit), "utf-8")
    return out, commit, clang_version(cc), {"entry": "index.html", "midi": "webmidi+inject", "kind": "workbench"}


# ------------------------------------------------------------------ Melodee
def build_melodee(work, sdk, local):
    src, commit = source("melodee", work, local)
    out = OUT / "melodee"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    run([sys.executable, "-c", 'import sys; sys.path.insert(0, "tools"); sys.argv = ["build.py"]; import build; build.generate()'],
        cwd=src, stdout=subprocess.DEVNULL)
    run([sys.executable, "tools/gen_909_tables.py", "build/gen/x0x_drum_tables.h"], cwd=src, stdout=subprocess.DEVNULL)
    version = re.search(r'#define MELODEE_VERSION "([^"]*)"', (src / "firmware/src/melodee.c").read_text()).group(1)
    cc = sdk / "bin" / "clang"
    wasm = out / "melodee.wasm"
    run([cc, f"--sysroot={sdk / 'share' / 'wasi-sysroot'}", "-O2", "-ffp-contract=off", "-std=gnu99", "-w",
         f"-I{SHIM}", "-Ibuild/gen", "-Ifirmware/src", "-Itests", "-mexec-model=reactor",
         "-Wl,-z,stack-size=1048576", "-Wl,--initial-memory=67108864", "-Wl,--strip-all",
         "-o", wasm, "web/emu/melodee_web.c"], cwd=src)
    page = (src / "web/emu/index.html").read_text("utf-8").replace("@VERSION@", version)
    page = page.replace('<meta charset="utf-8">', '<meta charset="utf-8">\n<script>window.__FM1SIM_ID=\'melodee\';</script>'
                        + HOST_TAG, 1)
    page = page.replace('href="../webapp/installer/"', 'href="https://keremimo.github.io/melodee/"')
    (out / "index.html").write_text(page, "utf-8")
    shutil.copy(src / "web/emu/worklet.js", out / "worklet.js")
    shutil.copy(src / "LICENSE", out / "LICENSE")
    (out / "NOTICE").write_text(
        f"Melodee {version} – browser emulator (web/emu), built from {SIMS['melodee']['repo']} tag "
        f"{SIMS['melodee']['ref']} (commit {commit}).\nGPL-3.0-only. Copyright (C) 2026 Kerem Kilic (Ellic Studio); "
        "portions Copyright (C) 2026 Leo Kuroshita, Hügelton Instruments (Felucca).\n"
        "Compiled with WASI SDK clang instead of Emscripten; include shim tools/wasm_shim/sys/wait.h (fork/pipe stubs "
        "for unused host-test helpers). Page: version filled in, installer link points to the official site, "
        "../_host/fm1sim-host.js is loaded.\n" + source_offer(SIMS["melodee"]["repo"], commit,
                                                               " and the shim tools/wasm_shim"), "utf-8")
    return out, commit, clang_version(cc), {"entry": "index.html", "midi": "webmidi+inject", "kind": "emulator",
                                             "start_button": "#power"}


BUILDERS = {"fm1-open": build_fm1_open, "melodee": build_melodee}


# ------------------------------------------------------------------ studio twins
# A studio twin is the firmware's own code for the host plus flash and the USB SysEx path (the editor protocol),
# exporting the twin ABI (st_*, see tools/studio/<firmware>_studio.c and fm1companion/sims/_studio/twin-core.js).
# Several versions live side by side: fm1companion/sims/<firmware>-studio/<version>/ (studio.py picks the one
# that matches the device / backup). Adding a version is one line, e.g.:
#   python3 tools/build_sims.py --target melodee-studio --ref v1.6.0-rc.9 --repo melodee=/tmp/melodee
# (needs: git, Pillow + fontTools for Melodee's generators, WASI SDK 27 as above). Later firmwares (SLOOP, Skiff)
# add an entry here with their own glue file; the JS / Python host stays the same.
STUDIO_ABI = 1
STUDIOS = {
    "melodee-studio": {"firmware": "melodee", "repo": SIMS["melodee"]["repo"], "glue": "tools/studio/melodee_studio.c",
                       "refs": ["v1.6.0-rc.7", "v1.6.0-rc.8", "v1.6.0", "v1.6.1"], "title": "Melodee Studio-Zwilling",
                       "license": "GPL-3.0-only"},
}


def melodee_release_identity(version: str) -> str:
    """tools/fm1pkg_make.py release_product: the package identity a release build answers (FM-1_9XY[Z])."""
    m = re.fullmatch(r"v?(\d)\.(\d{1,2})(?:\.(\d))?(?:-[A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*)?", version)
    if not m:
        return "FM-1_900"
    major, minor, patch = m.groups()
    return "FM-1_9" + major + (minor.zfill(2) + patch if patch else minor)


def build_melodee_studio(work, sdk, local, ref, name="melodee-studio"):
    spec = STUDIOS[name]
    src, commit = source(spec["firmware"], work, local, ref=ref, repo_url=spec["repo"],
                         tree_name=f"{name}-{ref}-src")
    run([sys.executable, "-c", 'import sys; sys.path.insert(0, "tools"); sys.argv = ["build.py"]; import build; build.generate()'],
        cwd=src, stdout=subprocess.DEVNULL)
    run([sys.executable, "tools/gen_909_tables.py", "build/gen/x0x_drum_tables.h"], cwd=src, stdout=subprocess.DEVNULL)
    version = re.search(r'#define MELODEE_VERSION "([^"]*)"', (src / "firmware/src/melodee.c").read_text()).group(1)
    identity = melodee_release_identity(version)
    glue = ROOT / spec["glue"]
    shutil.copy(glue, src / "tests" / "fm1c_studio.c")    # beside hostsim.c: its includes resolve as upstream's tests
    out = OUT / name / version.lstrip("v")
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    cc = sdk / "bin" / "clang"
    wasm = out / "twin.wasm"
    run([cc, f"--sysroot={sdk / 'share' / 'wasi-sysroot'}", "-O2", "-ffp-contract=off", "-std=gnu99", "-w",
         f"-I{SHIM}", "-Ibuild/gen", "-Ifirmware/src", "-Itests", "-mexec-model=reactor",
         f'-DMELODEE_VERSION="{version}"', f'-DMELODEE_ID="{identity}"',
         "-Wl,-z,stack-size=1048576", "-Wl,--initial-memory=33554432", "-Wl,--strip-all",
         "-o", wasm, "tests/fm1c_studio.c"], cwd=src)
    shutil.copy(src / "LICENSE", out / "LICENSE")
    source_url = f"{spec['repo']}/archive/{commit}.tar.gz"
    (out / "NOTICE").write_text(
        f"Melodee {version} – studio twin (firmware code for the host), built from {spec['repo']} tag {ref}\n"
        f"(commit {commit}; source: {source_url}).\n"
        "GPL-3.0-only. Copyright (C) 2026 Kerem Kilic (Ellic Studio); portions Copyright (C) 2026 Leo Kuroshita, "
        "Hügelton Instruments (Felucca).\n"
        f"The firmware sources are unmodified. The glue {spec['glue']} (GPL-3.0-only, in FM-1 Companion's source) "
        "includes them as Melodee's own host tests do (tests/hostsim.c, ui_test.c, backup_test.c, editor_test.c)\n"
        "and replaces only the hardware: keys/LEDs/LCD, NOR flash = a 1 MiB RAM image, USB = always configured (SysEx "
        "in/out through usb.c's rings), CPU1 jobs run serially,\nno BLE, no USB host, no updater (ota.c answers the "
        "identity handshake only), no overload shedding. Compiled with WASI SDK clang (include shim "
        "tools/wasm_shim/sys/wait.h).\n" + source_offer(spec["repo"], commit, f", the glue {spec['glue']} and the shim "
                                                         "tools/wasm_shim"), "utf-8")
    extra = {"kind": "studio", "firmware": spec["firmware"], "firmware_version": version, "identity": identity,
             "abi": STUDIO_ABI, "wasm": "twin.wasm", "fs": 44100, "flash_size": 0x100000,
             "glue": spec["glue"], "glue_sha256": sha(glue), "source_url": source_url}
    return out, commit, clang_version(cc), extra


def studio_manifest(name, out: Path, ref, commit, toolchain, extra):
    spec = STUDIOS[name]
    files = {p.name: {"sha256": sha(p), "size": p.stat().st_size} for p in sorted(out.iterdir()) if p.is_file()
             and p.name != "manifest.json"}
    m = {"schema": 1, "name": name, "title": spec["title"], "version": extra["firmware_version"].lstrip("v"),
         "source_repo": spec["repo"], "ref": ref, "commit": commit, "license": spec["license"],
         "build_date": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
         "toolchain": toolchain, "builder": "tools/build_sims.py", "files": files, **extra}
    (out / "manifest.json").write_text(json.dumps(m, indent=2, ensure_ascii=False) + "\n", "utf-8")
    return m


def manifest(name, out: Path, commit, toolchain, extra):
    spec = SIMS[name]
    files = {p.name: {"sha256": sha(p), "size": p.stat().st_size} for p in sorted(out.iterdir()) if p.is_file()
             and p.name != "manifest.json"}
    m = {"schema": 1, "name": name, "title": spec["title"], "version": spec["version"], "source_repo": spec["repo"],
         "ref": spec["ref"], "commit": commit, "license": spec["license"],
         "build_date": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
         "toolchain": toolchain, "builder": "tools/build_sims.py", "files": files, **extra}
    (out / "manifest.json").write_text(json.dumps(m, indent=2, ensure_ascii=False) + "\n", "utf-8")
    return m


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", default=",".join(BUILDERS), help="simulators (fm1-open, melodee) and/or studio twins")
    ap.add_argument("--target", help="a studio twin (melodee-studio): with --ref one version, else all in STUDIOS")
    ap.add_argument("--ref", action="append", default=[], help="firmware tag(s) for --target (repeatable)")
    ap.add_argument("--work", default="/tmp/fm1c-simbuild")
    ap.add_argument("--wasi-sdk", default=os.environ.get("FM1_WASI_SDK"))
    ap.add_argument("--repo", action="append", default=[], help="name=/local/git/repo (read only)")
    a = ap.parse_args()
    work = Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    repos = dict(r.split("=", 1) for r in a.repo)
    sdk = wasi_sdk(work, a.wasi_sdk)
    if a.target:
        if a.target not in STUDIOS:
            raise SystemExit(f"unbekanntes Ziel {a.target} (bekannt: {', '.join(STUDIOS)})")
        spec = STUDIOS[a.target]
        for ref in a.ref or spec["refs"]:
            print(f"== {a.target} {ref}")
            out, commit, tc, extra = build_melodee_studio(work, sdk, repos.get(spec["firmware"]), ref, a.target)
            m = studio_manifest(a.target, out, ref, commit, tc, extra)
            print(f"   {out.relative_to(ROOT)}: " + ", ".join(f"{k} {v['size']} B" for k, v in m["files"].items()))
        return
    for name in a.only.split(","):
        print(f"== {name}")
        out, commit, tc, extra = BUILDERS[name](work, sdk, repos.get(name))
        m = manifest(name, out, commit, tc, extra)
        print(f"   {out.relative_to(ROOT)}: " + ", ".join(f"{k} {v['size']} B" for k, v in m["files"].items()))


if __name__ == "__main__":
    main()
