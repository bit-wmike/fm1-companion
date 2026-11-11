#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Build the FM-1 simulators that ship inside FM-1 Companion (fm1companion/sims/<name>/).

Every simulator is the firmware's own synth/UI C code compiled to WebAssembly (no CPU emulation), run in
an AudioWorklet by the upstream page. The result is architecture independent: the Raspberry Pi never
compiles anything. Run this in a development container, not on the user's machine.

  python3 tools/build_sims.py [--only fm1-open,melodee] [--work DIR] [--wasi-sdk DIR]
                              [--repo fm1-open=/tmp/fm1_open] [--repo melodee=/tmp/melodee]
  python3 tools/build_sims.py --target melodee-studio [--ref v1.6.0-rc.8 ...] [--repo melodee=/tmp/melodee]
  python3 tools/build_sims.py --target skiff-studio [--ref v2.2 ...] [--repo skiff=/tmp/skiff]
  python3 tools/build_sims.py --target jiant-studio [--ref <commit> ...] [--repo jiant=/tmp/jiant]
  python3 tools/build_sims.py --target sloop-studio [--ref v2.8.5 ...] [--repo sloop=/tmp/sloop]
  python3 tools/build_sims.py --target coralflower-studio [--ref v0.7.1 ...] [--repo coralflower=/tmp/coralflower]

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
  skiff-studio  (--target) the same for Skiff (tools/studio/skiff_studio.c): sims/skiff-studio/<version>/
            python3 tools/build_sims.py --target skiff-studio [--ref v2.2] --repo skiff=<clone>
  jiant-studio  (--target) the same for JIANT FM (tools/studio/jiant_studio.c): sims/jiant-studio/<version>/; JIANT has
            no tags, a ref is a commit (web/EDITIONS.json names them):
            python3 tools/build_sims.py --target jiant-studio [--ref <commit>] --repo jiant=<clone>
  sloop-studio  (--target) the same for SLOOP (tools/studio/sloop_studio.c): sims/sloop-studio/<version>/
            python3 tools/build_sims.py --target sloop-studio [--ref v2.8.5] --repo sloop=<clone>
  coralflower-studio  (--target) the same for CoralFlower (tools/studio/coralflower_studio.c, on CoralFlower's own
            browser emulator tools/emu/web/emu_web.c): sims/coralflower-studio/<version>/; a ref is a release tag
            (web/releases.json lists them, the installer offers the first):
            python3 tools/build_sims.py --target coralflower-studio [--ref v0.7.1] --repo coralflower=<clone>
  karma-studio  (--target) the same for KARMA (tools/studio/karma_studio.c, with firmware/mi's C++):
            sims/karma-studio/<version>/   python3 tools/build_sims.py --target karma-studio [--ref v3.1] --repo karma=<clone>
  choralroot-studio  (--target) the same for ChoralRoot FM-1 (tools/studio/choralroot_studio.c, on its own browser
            emulator): sims/choralroot-studio/<version>/; a ref is an FM-1 release tag (not -smk37):
            python3 tools/build_sims.py --target choralroot-studio [--ref v0.39] --repo choralroot=<clone>
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
        raise SystemExit("WASI SDK: SHA-256 does not match")
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
        raise SystemExit("fm1-open: placeholder left over")
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
#   python3 tools/build_sims.py --target skiff-studio --ref v2.3 --repo skiff=/tmp/skiff
#   python3 tools/build_sims.py --target jiant-studio --ref <commit> --repo jiant=/tmp/jiant
#   python3 tools/build_sims.py --target sloop-studio --ref v2.8.5 --repo sloop=/tmp/sloop
#   python3 tools/build_sims.py --target coralflower-studio --ref v0.7.1 --repo coralflower=/tmp/coralflower
# (needs: git, Pillow + fontTools for the firmwares' header generators, WASI SDK 27 as above). A further firmware
# (SLOOP, FM1 Open) adds an entry here with its own glue file and the few firmware-specific hooks below (header
# generators, where the version string lives, the release identity, the NOTICE); the JS / Python host stays the same.
STUDIO_ABI = 1


def melodee_release_identity(version: str) -> str:
    """tools/fm1pkg_make.py release_product: the package identity a release build answers (FM-1_9XY[Z])."""
    m = re.fullmatch(r"v?(\d)\.(\d{1,2})(?:\.(\d))?(?:-[A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*)?", version)
    if not m:
        return "FM-1_900"
    major, minor, patch = m.groups()
    return "FM-1_9" + major + (minor.zfill(2) + patch if patch else minor)


def skiff_release_identity(version: str) -> str:
    """Skiff's tools/build.py --release X.Y[.Z[.W]][-suffix]: identity FM-1_7XY (one digit each; X.Y.Z keeps X.Y's)."""
    m = re.fullmatch(r"v?(\d)\.(\d)(?:\.\d){0,2}(?:-[A-Za-z0-9]+)?", version)
    return "FM-1_7" + m[1] + m[2] if m else "FM-1_700"


def jiant_identity(src: Path) -> str:
    """JIANT's tools/build.py PRODUCT: the package identity of the build its web installer offers (a plain
    tools/build.py, no --release: FM-1_909 since JIANT 0.9.x; ota.c answers it, the installer checks it)"""
    m = re.search(r'^PRODUCT = "(FM-1_\d+)"', (src / "tools/build.py").read_text("utf-8"), re.M)
    return m[1] if m else "FM-1_909"


def _generate_melodee(src: Path):
    run([sys.executable, "-c", 'import sys; sys.path.insert(0, "tools"); sys.argv = ["build.py"]; import build; build.generate()'],
        cwd=src, stdout=subprocess.DEVNULL)
    run([sys.executable, "tools/gen_909_tables.py", "build/gen/x0x_drum_tables.h"], cwd=src, stdout=subprocess.DEVNULL)


def _generate_skiff(src: Path):
    """Skiff's tools/build.py generate(): fonts, icons, palettes, sprites, tables, FM6 patches, the CC0 samples."""
    run([sys.executable, "-c", 'import sys; sys.path.insert(0, "tools"); sys.argv = ["build.py"]; import build; build.generate()'],
        cwd=src, stdout=subprocess.DEVNULL)


def _melodee_notice(spec, version, ref, commit, source_url):
    return (
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
                                                         "tools/wasm_shim"))


SKIFF_THIRD_PARTY = ["LICENSES/MIT-DaisySP.txt", "LICENSES/MIT-Rings.txt", "LICENSES/Apache-2.0-msfa.txt",
                     "LICENSES/OFL-BarlowCondensed.txt", "LICENSES/MIT-Fukiai.txt", "assets/samples-cc0/ATTRIBUTION.txt",
                     "assets/breaks/ATTRIBUTION.txt"]


def _skiff_extra(src: Path, out: Path):
    """the licence texts of the parts compiled into the twin (Skiff's LICENSING.md lists them)"""
    shutil.copy(src / "LICENSING.md", out / "LICENSING.md")
    (out / "THIRD_PARTY.txt").write_text("".join(f"===== {f} =====\n" + (src / f).read_text("utf-8").rstrip() + "\n\n"
                                                 for f in SKIFF_THIRD_PARTY), "utf-8")


JIANT_THIRD_PARTY = ["LICENSES/Apache-2.0-msfa.txt", "LICENSES/MIT-Fukiai.txt", "LICENSES/OFL-ChakraPetch.txt",
                     "LICENSES/OFL-InterTight.txt", "LICENSES/OFL-Michroma.txt", "LICENSES/BSD-2-Clause-CMUdict.txt"]


def _jiant_extra(src: Path, out: Path):
    """the licence texts of the parts compiled into the twin (JIANT's LICENSING.md lists them: msfa in fm6_core.c, the
    Fukiai icons and the UI faces Chakra Petch, Inter Tight and Michroma rasterised in, VOICE's CMUdict subset)"""
    shutil.copy(src / "LICENSING.md", out / "LICENSING.md")
    (out / "THIRD_PARTY.txt").write_text("".join(f"===== {f} =====\n" + (src / f).read_text("utf-8").rstrip() + "\n\n"
                                                 for f in JIANT_THIRD_PARTY), "utf-8")


def _jiant_notice(spec, version, ref, commit, source_url):
    return (
        f"JIANT FM {version} – studio twin (firmware code for the host), built from {spec['repo']} commit {commit}\n"
        f"(source: {source_url}; JIANT has no tags: {version} is the version this commit reports).\n"
        "GPL-3.0-only. JIANT FM by juanjiant-bit, a fork of Felucca: Copyright (C) 2026 Leo Kuroshita (@kurogedelic), "
        "Hügelton Instruments (Felucca);\nportions Copyright (C) 2026 Juan Ignacio Antonucci (Juan Jiant) (JIANT's "
        "changes and additions, as its source files name it).\n"
        "Ported parts keep their licences (msfa in fm6_core.c: Apache-2.0; the UI faces Chakra Petch, Inter Tight and "
        "Michroma and the Fukiai icons are rasterised in: OFL 1.1, MIT; VOICE's English words are a subset of the CMU "
        "Pronouncing Dictionary: BSD-2-Clause):\nJIANT's LICENSING.md and the texts in THIRD_PARTY.txt beside this file. "
        "The Bluetooth stack, the USB host and the JieLi SDK files of JIANT's packages are not part of the twin.\n"
        f"The firmware sources are unmodified. The glue {spec['glue']} (GPL-3.0-only, in FM-1 Companion's source) "
        "includes them as JIANT's own browser emulator and host tests do (web/emu/felucca_web.c, tests/hostsim.c,\n"
        "editor_test.c, backup_test.c) and replaces only the hardware: keys/LEDs/LCD, ADC (MASTER; battery full), "
        "NOR flash = a 1 MiB RAM image, USB = always configured (SysEx and MIDI out through usb.c's rings),\none core, "
        "no 320 MHz switch, no supply setup, no Bluetooth, no USB host, no USB audio (the INPUT engine hears silence), "
        "no TRS MIDI, no serial console, no updater (ota.c answers the identity handshake only).\nCompiled with WASI SDK "
        "clang (include shim tools/wasm_shim/sys/wait.h).\n" + source_offer(spec["repo"], commit,
                                                                          f", the glue {spec['glue']} and the shim "
                                                                          "tools/wasm_shim"))


def _skiff_notice(spec, version, ref, commit, source_url):
    return (
        f"Skiff {version} – studio twin (firmware code for the host), built from {spec['repo']} tag {ref}\n"
        f"(commit {commit}; source: {source_url}).\n"
        "GPL-3.0-only. Copyright (C) 2026 Leo Kuroshita (@kurogedelic), Hügelton Instruments (Felucca); "
        "Copyright (C) 2026 illuminus5 (Skiff's changes and additions).\n"
        "Ported parts keep their licences (DaisySP and Rings: MIT, msfa: Apache-2.0; the UI font Barlow Condensed and "
        "the Fukiai icons are rasterised in: OFL 1.1, MIT), the instrument samples are CC0 (Versilian Studios):\n"
        "Skiff's LICENSING.md and the texts in THIRD_PARTY.txt beside this file.\n"
        f"The firmware sources are unmodified. The glue {spec['glue']} (GPL-3.0-only, in FM-1 Companion's source) "
        "includes them as Skiff's own browser emulator and host tests do (web/emu/felucca_web.c, tests/hostsim.c,\n"
        "editor_test.c, backup_test.c) and replaces only the hardware: keys/LEDs/LCD, ADC (MASTER; battery full), "
        "NOR flash = a 1 MiB RAM image, USB = always configured (SysEx and MIDI out through usb.c's rings),\none core "
        "(MENU > CPU 2 CORES has no second core here), no 320 MHz switch, no USB audio, no TRS MIDI, no serial console, "
        "no updater (ota.c answers the identity handshake only).\nCompiled with WASI SDK clang (include shim "
        "tools/wasm_shim/sys/wait.h).\n" + source_offer(spec["repo"], commit, f", the glue {spec['glue']} and the shim "
                                                         "tools/wasm_shim"))


def sloop_identity(src: Path) -> str:
    """SLOOP's tools/build.py PRODUCT: the package identity of its releases (the .fwsc of its web installer is built
    without --release: FM-1_900; ota.c answers it)"""
    m = re.search(r'^PRODUCT = "(FM-1_\d+)"', (src / "tools/build.py").read_text("utf-8"), re.M)
    return m[1] if m else "FM-1_900"


def sloop_folder(version: str) -> str:
    """ui.c's FELUCCA_VERSION "SLOOP 2.8.5" -> the folder 2.8.5"""
    return version.split()[-1].lstrip("vV")


# the parts of SLOOP compiled into the twin that carry their own licence (SLOOP's LICENSING.md "Third-party material"):
# msfa in fm6_core.c, the Terminus font of the screen, the CC0 samples; DaisySP's and Rings' MIT notices are in the
# files themselves (phys_dsp.c, phys_symp.c: their header comment is taken over)
SLOOP_THIRD_PARTY = ["LICENSES/Apache-2.0-msfa.txt", "assets/fonts/Terminus-LICENSE.txt",
                     "assets/samples-cc0/ATTRIBUTION.txt", "assets/samples-cc0/CREDITS.txt"]
SLOOP_NOTICE_FILES = ["firmware/src/phys_dsp.c", "firmware/src/phys_symp.c"]


def _sloop_extra(src: Path, out: Path):
    """the licence texts of the parts compiled into the twin (SLOOP's LICENSING.md lists them)"""
    shutil.copy(src / "LICENSING.md", out / "LICENSING.md")
    texts = [f"===== {f} =====\n" + (src / f).read_text("utf-8").rstrip() + "\n\n" for f in SLOOP_THIRD_PARTY]
    for f in SLOOP_NOTICE_FILES:
        head = (src / f).read_text("utf-8")
        texts.append(f"===== {f} (its licence notice) =====\n" + head[:head.index("*/") + 2] + "\n\n")
    (out / "THIRD_PARTY.txt").write_text("".join(texts), "utf-8")


def _sloop_notice(spec, version, ref, commit, source_url):
    return (
        f"{version} – studio twin (firmware code for the host), built from {spec['repo']} tag {ref}\n"
        f"(commit {commit}; source: {source_url}).\n"
        "GPL-3.0-only. SLOOP by isod89, based on Felucca: Copyright (C) 2026 Leo Kuroshita (@kurogedelic), Hügelton "
        "Instruments (Felucca); SLOOP's changes and additions by its authors (SLOOP's README, Credits).\n"
        "Ported parts keep their licences (msfa in fm6_core.c: Apache-2.0; DaisySP and Rings in phys_dsp.c, "
        "phys_symp.c: MIT; the Terminus screen font is rasterised in: OFL 1.1), the instrument and drum samples are "
        "CC0\n(Versilian Studios, Sonic Pi): SLOOP's LICENSING.md and the texts in THIRD_PARTY.txt beside this file. "
        "The icon atlas is a Felucca Asset (Hügelton Instruments), combined under the additional permission\n(GPL-3.0 "
        "section 7) of LICENSING.md. The JieLi SDK files of SLOOP's packages are not part of the twin.\n"
        f"The firmware sources are unmodified. The glue {spec['glue']} (GPL-3.0-only, in FM-1 Companion's source) "
        "includes them as SLOOP's own host tests do (tests/hostsim.c, editor_host.c, boot_sound_test.c)\nand replaces "
        "only the hardware: keys/LEDs/LCD, ADC (MASTER; battery full), NOR flash = a 1 MiB RAM image, USB = always "
        "configured (SysEx and MIDI out through usb.c's rings),\none core (MENU > 2ND CORE has no second core here), no "
        "USB audio, no TRS MIDI, no serial console, no USB rescue, no updater (ota.c answers the identity handshake "
        "only).\nCompiled with WASI SDK clang (include shim tools/wasm_shim/sys/wait.h).\n"
        + source_offer(spec["repo"], commit, f", the glue {spec['glue']} and the shim tools/wasm_shim"))


# ---- CoralFlower (FicusFicusov/CoralFlower): a release is a tag vX.Y[.Z]; tools/build.py --release X.Y sets the version
# string "CoralFlower X.Y" (the INFO text) and keeps the identity PRODUCT (FM-1_930 for every release)
def coralflower_version(src: Path, ref: str) -> str:
    """the release version of a tag (v0.7.1 -> 0.7.1), as CoralFlower's release workflow passes it to --release"""
    m = re.fullmatch(r"v?(\d+\.\d+(?:\.\d+)?(?:-[A-Za-z0-9.]+)?)", ref)
    if not m:
        raise SystemExit(f"coralflower-studio: {ref} is not a release tag (vX.Y[.Z])")
    return m[1].lower()


def coralflower_identity(src: Path) -> str:
    """CoralFlower's tools/build.py PRODUCT: the package identity its releases answer (ota.c, the installer)"""
    m = re.search(r'^PRODUCT = "(FM-1_\d+)"', (src / "tools/build.py").read_text("utf-8"), re.M)
    return m[1] if m else "FM-1_930"


CORALFLOWER_THIRD_PARTY = ["LICENSES/Apache-2.0-msfa.txt", "LICENSES/OFL-InterTight.txt",
                           "LICENSES/OFL-DepartureMono.txt", "LICENSES/MIT-Fukiai.txt"]


def _coralflower_extra(src: Path, out: Path):
    """CoralFlower's licensing records (LICENSING.md, and docs/LICENSING-choralroot.md for the parts it inherited) and
    the licence texts of the parts compiled into the twin: msfa through Melodee's FM6 engine (fm6_core.c), the UI faces
    Inter Tight and Departure Mono rasterised in, the Fukiai icons"""
    shutil.copy(src / "LICENSING.md", out / "LICENSING.md")
    shutil.copy(src / "docs/LICENSING-choralroot.md", out / "LICENSING-choralroot.md")
    (out / "THIRD_PARTY.txt").write_text("".join(f"===== {f} =====\n" + (src / f).read_text("utf-8").rstrip() + "\n\n"
                                                 for f in CORALFLOWER_THIRD_PARTY), "utf-8")


def _coralflower_notice(spec, version, ref, commit, source_url):
    return (
        f"CoralFlower {version} – studio twin (firmware code for the host), built from {spec['repo']} tag {ref}\n"
        f"(commit {commit}; source: {source_url}).\n"
        "GPL-3.0-only. CoralFlower by FicusFicusov (CoralFlower contributors), a fork of ChoralRoot FM-1 (Quixotic7, "
        "ChoralRoot FM-1 contributors), a fork of Felucca:\nCopyright (C) 2026 Leo Kuroshita (@kurogedelic), Hügelton "
        "Instruments (Felucca); the FM6 engine Melodee's, Copyright (C) 2026 Kerem Kilic (Ellic Studio).\n"
        "Ported parts keep their licences (msfa in fm6_core.c: Apache-2.0, Dexed: GPL-3.0-or-later; the UI faces Inter "
        "Tight and Departure Mono and the Fukiai icons are rasterised in: OFL 1.1, MIT):\nCoralFlower's LICENSING.md, "
        "LICENSING-choralroot.md (its docs/LICENSING-choralroot.md) and the texts in THIRD_PARTY.txt beside this file. "
        "The JieLi SDK files of CoralFlower's packages are not part of the twin.\n"
        f"The firmware sources are unmodified. The glue {spec['glue']} (GPL-3.0-only, in FM-1 Companion's source) "
        "includes them as CoralFlower's own browser emulator and host tests do (tools/emu/web/emu_web.c,\n"
        "tools/emu/emu_fw.c, tools/emu/emu_firmware.h, tests/cf_backup_test.c) and adds the USB SysEx path (usb.c, "
        "cf_backup.c's backup protocol, ota.c's identity handshake); the hardware stand-ins are the emulator's:\n"
        "keys/LEDs/LCD, the MASTER pot, NOR flash = a 1 MiB RAM image, one core (the bass part's job runs inline), USB = "
        "always configured (SysEx and MIDI out through usb.c's rings),\nno USB audio, no TRS MIDI, no serial console, "
        "no updater and no USB boot (ota.c answers the identity handshake only).\nCompiled with WASI SDK clang (include "
        "shim tools/wasm_shim/sys/wait.h; tools/emu/web/compat from CoralFlower's tree).\n" +
        source_offer(spec["repo"], commit, f", the glue {spec['glue']} and the shim tools/wasm_shim"))



def karma_identity(src: Path) -> str:
    """KARMA's tools/build.py PRODUCT: its own package identity, FM-1_926 in every build (release builds keep it; ota.c
    answers it, the installer checks it)"""
    m = re.search(r'^PRODUCT = "(FM-1_\d+)"', (src / "tools/build.py").read_text("utf-8"), re.M)
    return m[1] if m else "FM-1_926"


def _karma_objects(src: Path, sdk: Path) -> list:
    """firmware/mi (Mutable Instruments' Plaits, Elements and stmlib in their own C++, plus KARMA's plx.cc / elx.cc), compiled
    as KARMA's tools/plx_host.sh does for its host tests and its browser simulator; linked into the twin with libc++"""
    out = src / "build" / "wasm" / "plx"
    out.mkdir(parents=True, exist_ok=True)
    objs = []
    for f in sorted((src / "firmware" / "mi").rglob("*.cc")):
        rel = f.relative_to(src).as_posix()
        o = out / (rel.replace("/", "_")[:-3] + ".o")
        run([sdk / "bin" / "clang++", f"--sysroot={sdk / 'share' / 'wasi-sysroot'}", "-O2", "-w", "-std=c++11", "-DTEST",
             "-ffp-contract=off", "-fno-exceptions", "-fno-rtti", "-Ifirmware/mi", "-c", rel, "-o", o], cwd=src)
        objs.append(str(o.relative_to(src)))
    return objs + ["-lc++", "-lc++abi"]


# KARMA's LICENSING.md: the licence texts of the parts compiled into the twin (Plaits / Elements / stmlib, PRISM: MIT; the
# LXR-02 drum voices: their section 7 notice; the screen reader's announce.c (3.1): MPL-2.0; the Terminus screen font:
# OFL 1.1) and the MIT notices KARMA keeps in the ported files themselves (DaisySP, Rings, Elements, Clouds, Plaits)
KARMA_THIRD_PARTY = ["LICENSES/MIT-plaits.txt", "LICENSES/LXR-NOTICE.md", "LICENSES/MPL-2.0.txt",
                     "assets/fonts/Terminus-LICENSE.txt"]
KARMA_MIT_FILES = ["firmware/src/phys_dsp.c", "firmware/src/phys_symp.c", "firmware/src/strike_dsp.c",
                   "firmware/src/bow_dsp.c", "firmware/src/cloud_dsp.c", "firmware/src/prism_dsp.c"]


def _karma_extra(src: Path, out: Path):
    shutil.copy(src / "LICENSING.md", out / "LICENSING.md")
    parts = [f"===== {f} =====\n" + (src / f).read_text("utf-8").rstrip() + "\n\n" for f in KARMA_THIRD_PARTY
             if (src / f).exists()]                       # (MPL-2.0: from 3.1, the screen reader)
    for f in KARMA_MIT_FILES:
        if (src / f).exists():
            head = (src / f).read_text("utf-8").split("*/", 1)[0] + "*/"
            parts.append(f"===== {f} (its notice) =====\n{head}\n\n")
    (out / "THIRD_PARTY.txt").write_text("".join(parts), "utf-8")


def _karma_notice(spec, version, ref, commit, source_url):
    return (
        f"{version} – studio twin (firmware code for the host), built from {spec['repo']} tag {ref}\n"
        f"(commit {commit}; source: {source_url}).\n"
        "GPL-3.0-only. KARMA by Ariel Muchnik, a fork of SLOOP (isod89), based on Felucca: Copyright (C) 2026 Leo "
        "Kuroshita (@kurogedelic), Hügelton Instruments;\nportions Copyright (C) 2026 Ariel Muchnik, Juan Ignacio "
        "Antonucci (Juan Jiant), Kerem Kilic, Charles Vestal, PatttF, Erica Synths and the others its LICENSING.md "
        "names.\n"
        "Ported parts keep their licences (Mutable Instruments' Plaits, Elements, Clouds and stmlib, DaisySP, Rings: "
        "MIT; Grids: GPL-3.0-or-later; the LXR-02 drum voices: GPL-3.0-only with Erica Synths' section 7 terms;\n"
        "the screen reader's announce.c: MPL-2.0; the Terminus screen font is rasterised in: OFL 1.1): KARMA's "
        "LICENSING.md and the texts in THIRD_PARTY.txt beside this file.\n"
        "Not affiliated with or endorsed by Mutable Instruments or Erica Synths. The JieLi SDK files of KARMA's packages "
        "are not part of the twin.\n"
        f"The firmware sources are unmodified. The glue {spec['glue']} (GPL-3.0-only, in FM-1 Companion's source) "
        "includes them in firmware/src/felucca.c's order on KARMA's own host-test base (tests/hostsim.c, as\n"
        "tests/ui_pages_test.c and web/emu/sloop_web.c do) and links firmware/mi's C++ as tools/plx_host.sh builds "
        "it. It replaces only the hardware: keys/LEDs/LCD, ADC (MASTER; battery full),\nNOR flash = a 1 MiB RAM image, "
        "USB = always configured (SysEx and MIDI out through usb.c's rings), one core (KARMA_DUAL_CORE 0), no USB "
        "audio, no TRS MIDI, no serial console,\nno USB rescue, no updater (ota.c answers the identity handshake "
        "only). Compiled with WASI SDK clang (include shim tools/wasm_shim/sys/wait.h).\n"
        + source_offer(spec["repo"], commit, f", the glue {spec['glue']} and the shim tools/wasm_shim"))



# ---- ChoralRoot FM-1 (Quixotic7/ChoralRootFM1): a release is a tag vX.Y[.Z] (the -smk37 tags are the SMK-37's builds,
# not the FM-1's); tools/build.py --release X.Y sets the version string "ChoralRoot X.Y" (the INFO text) and keeps the
# identity PRODUCT (FM-1_920 for every FM-1 release)
def choralroot_version(src: Path, ref: str) -> str:
    """the release version of an FM-1 tag (v0.39 -> 0.39), as ChoralRoot's release passes it to --release"""
    m = re.fullmatch(r"v?(\d+\.\d+(?:\.\d+)?(?:-[A-Za-z0-9.]+)?)", ref)
    if not m or "smk" in ref.lower():
        raise SystemExit(f"choralroot-studio: {ref} is not an FM-1 release tag (vX.Y[.Z], not -smk37)")
    return m[1].lower()


def choralroot_identity(src: Path) -> str:
    """ChoralRoot's tools/build.py PRODUCT: the FM-1 package identity its releases answer (ota.c, the installer)"""
    m = re.search(r'^PRODUCT = "(FM-1_\d+)"', (src / "tools/build.py").read_text("utf-8"), re.M)
    return m[1] if m else "FM-1_920"


# ChoralRoot's LICENSING.md "Third-party material": the licence texts of the parts compiled into the twin (msfa through
# Melodee's FM6 engine, the uPD933 model in cz_native.c, felucca-bsp's flash wait, the UI face Inter Tight and the
# Fukiai icons rasterised in, the second core's bring-up after JieLi's SDK)
CHORALROOT_THIRD_PARTY = ["LICENSES/Apache-2.0-msfa.txt", "LICENSES/BSD-3-Clause-uPD933.txt", "LICENSES/MPL-2.0.txt",
                          "LICENSES/OFL-InterTight.txt", "LICENSES/MIT-Fukiai.txt", "LICENSES/Apache-2.0.txt"]


def _choralroot_extra(src: Path, out: Path):
    shutil.copy(src / "LICENSING.md", out / "LICENSING.md")
    (out / "THIRD_PARTY.txt").write_text("".join(f"===== {f} =====\n" + (src / f).read_text("utf-8").rstrip() + "\n\n"
                                                 for f in CHORALROOT_THIRD_PARTY if (src / f).exists()), "utf-8")


def _choralroot_notice(spec, version, ref, commit, source_url):
    return (
        f"ChoralRoot FM-1 {version} – studio twin (firmware code for the host), built from {spec['repo']} tag {ref}\n"
        f"(commit {commit}; source: {source_url}).\n"
        "GPL-3.0-only. ChoralRoot FM-1 by Quixotic7 (ChoralRoot FM-1 contributors), a fork of Felucca: Copyright (C) 2026 "
        "Leo Kuroshita (@kurogedelic), Hügelton Instruments (Felucca);\nthe FM6, CZ-1 and Prophet-5 engines Melodee's, "
        "Copyright (C) 2026 Kerem Kilic (Ellic Studio); the others ChoralRoot's LICENSING.md names.\n"
        "Ported parts keep their licences (msfa in fm6_core.c: Apache-2.0, Dexed: GPL-3.0-or-later; MAME's uPD933 in "
        "cz_native.c: BSD-3-Clause; felucca-bsp's fm1_flash_wait.h: MPL-2.0;\nthe UI face Inter Tight and the Fukiai "
        "icons are rasterised in: OFL 1.1, MIT): ChoralRoot's LICENSING.md and the texts in THIRD_PARTY.txt beside this "
        "file.\nCasio's 64 CZ-1 preset tones and the Sequential Prophet-5 factory programs ChoralRoot ships are sound "
        "data, included as in ChoralRoot's own releases and browser emulator; their rights stay with Casio and "
        "Sequential.\nThe JieLi SDK files of ChoralRoot's packages and its Bluetooth stack are not part of the twin.\n"
        f"The firmware sources are unmodified. The glue {spec['glue']} (GPL-3.0-only, in FM-1 Companion's source) "
        "includes them as ChoralRoot's own browser emulator and host tests do (tools/emu/web/emu_web.c,\n"
        "tools/emu/emu_fw.c, tools/emu/emu_firmware.h, tests/cr_backup_test.c) and adds the USB SysEx path (usb.c, "
        "cr_backup.c's backup protocol, ota.c's identity handshake); the hardware stand-ins are the emulator's:\n"
        "keys/LEDs/LCD, the key sensing, the MASTER pot, NOR flash = a 1 MiB RAM image, one core (the bass part's job "
        "runs inline), USB = always configured (SysEx and MIDI out through usb.c's rings),\nno USB audio, no TRS MIDI, "
        "no Bluetooth, no serial console, no updater and no USB boot (ota.c answers the identity handshake only).\n"
        "Compiled with WASI SDK clang (include shim tools/wasm_shim/sys/wait.h; tools/emu/web/compat from ChoralRoot's "
        "tree).\n" + source_offer(spec["repo"], commit, f", the glue {spec['glue']} and the shim tools/wasm_shim"))


STUDIOS = {
    "melodee-studio": {"firmware": "melodee", "repo": SIMS["melodee"]["repo"], "glue": "tools/studio/melodee_studio.c",
                       "refs": ["v1.6.0-rc.7", "v1.6.0-rc.8", "v1.6.0", "v1.6.1"], "title": "Melodee Studio-Zwilling",
                       "license": "GPL-3.0-only", "generate": _generate_melodee,
                       "version_file": "firmware/src/melodee.c", "version_re": r'#define MELODEE_VERSION "([^"]*)"',
                       "identity": melodee_release_identity,
                       "defines": lambda v, i: [f'-DMELODEE_VERSION="{v}"', f'-DMELODEE_ID="{i}"'],
                       "notice": _melodee_notice, "tracks": 4, "projects": 32},
    "skiff-studio": {"firmware": "skiff", "repo": "https://github.com/illuminus5/skiff",
                     "glue": "tools/studio/skiff_studio.c", "refs": ["v2.1.1", "v2.2"], "title": "Skiff studio twin",
                     "license": "GPL-3.0-only", "generate": _generate_skiff,
                     "version_file": "firmware/src/felucca.c", "version_re": r'#define FELUCCA_VERSION "([^"]*)"',
                     "identity": skiff_release_identity,
                     "defines": lambda v, i: [f'-DFELUCCA_VERSION="{v}"', f'-DFELUCCA_ID="{i}"'],
                     "notice": _skiff_notice, "extra": _skiff_extra, "tracks": 6, "projects": 4},
    # JIANT FM: no git tags; the refs are commits its installer offered: V10.6z2 (web/STABLE, web/PUBLIC_RELEASE.json:
    # the public stable now), V10.60 (the stable before, web/EDITIONS.json) and V10.61 (its beta then). The folder is the
    # version the commit reports (felucca.c FELUCCA_VERSION, "V10.6z2" -> 10.6z2).
    "jiant-studio": {"firmware": "jiant", "repo": "https://github.com/juanjiant-bit/JIANT",
                     "glue": "tools/studio/jiant_studio.c",
                     "refs": ["43b8ea8111949c990bf76523c7d4f4e77b715114", "6c48e2a8e7049c31ab77c57b97c3ef6790d24090",
                              "cac23c6d0bb9a7f6986eb79bbe42ae710805ce98"],
                     # what JIANT's installer called each commit (web/STABLE, web/PUBLIC_RELEASE.json, web/EDITIONS.json,
                     # web/BETA_VERSIONS); "stable" is the one its public installer offers now (2026-11-08)
                     "release_status": {"cac23c6d0bb9a7f6986eb79bbe42ae710805ce98": "stable",
                                        "43b8ea8111949c990bf76523c7d4f4e77b715114": "previous stable",
                                        "6c48e2a8e7049c31ab77c57b97c3ef6790d24090": "previous beta"},
                     "title": "JIANT FM studio twin", "license": "GPL-3.0-only", "generate": _generate_skiff,
                     "version_file": "firmware/src/felucca.c", "version_re": r'#define FELUCCA_VERSION "([^"]*)"',
                     "identity_src": jiant_identity,
                     "defines": lambda v, i: [f'-DFELUCCA_VERSION="{v}"', f'-DFELUCCA_ID="{i}"'],
                     "notice": _jiant_notice, "extra": _jiant_extra, "tracks": 6, "projects": 4},
    # SLOOP: its release tags; v2.8.5 is the stable its web installer ships (docs/firmware/sloop-2.8.5.fwsc), v2.8.4 the
    # one before. The folder is the number of the version ui.c reports ("SLOOP 2.8.5" -> 2.8.5).
    "sloop-studio": {"firmware": "sloop", "repo": "https://github.com/isod89/sloop-fm1",
                     "glue": "tools/studio/sloop_studio.c", "refs": ["v2.8.4", "v2.8.5"], "title": "SLOOP studio twin",
                     "license": "GPL-3.0-only", "generate": _generate_skiff,
                     "version_file": "firmware/src/ui.c", "version_re": r'#define FELUCCA_VERSION "([^"]*)"',
                     "identity_src": sloop_identity, "folder": sloop_folder,
                     "defines": lambda v, i: [f'-DFELUCCA_VERSION="{v}"', f'-DFELUCCA_ID="{i}"'],
                     "notice": _sloop_notice, "extra": _sloop_extra, "tracks": 4, "projects": 4},
    # CoralFlower: release tags; 0.7.1 is what its web installer offers (web/releases.json, the first entry), 0.7 the
    # release before (same day). The folder is the release version (v0.7.1 -> 0.7.1); INFO says "CoralFlower 0.7.1".
    # Its own browser emulator's flags (tools/emu/web/build_web.sh: gnu11, the single-thread <os/lock.h>)
    "coralflower-studio": {"firmware": "coralflower", "repo": "https://github.com/FicusFicusov/CoralFlower",
                           "glue": "tools/studio/coralflower_studio.c", "refs": ["v0.7", "v0.7.1"],
                           "release_status": {"46790b943bdb5e4143212d83dc495c832eefdbb8": "stable",
                                              "65c6b767cc446b1c2b85c32b0812d50719fd10a4": "previous stable"},
                           "title": "CoralFlower studio twin", "license": "GPL-3.0-only", "generate": _generate_skiff,
                           "version_ref": coralflower_version, "identity_src": coralflower_identity,
                           "defines": lambda v, i: [f'-DFELUCCA_VERSION="CoralFlower {v}"', f'-DFELUCCA_ID="{i}"'],
                           "cflags": ["-std=gnu11", "-Itools/emu/web/compat"],
                           "notice": _coralflower_notice, "extra": _coralflower_extra, "tracks": 4, "projects": 0},
    # KARMA (a SLOOP fork): v3.1 is what its web installer serves now (docs/webapp/installer, 2026-11-10), v3.0.5 the
    # release before. It reports "KARMA 3.1" (ui.c FELUCCA_VERSION; INFO "FELUCCA KARMA 3.1"), the folder is the number.
    "karma-studio": {"firmware": "karma", "repo": "https://github.com/eatyourgreensstudio-dotcom/karma-fm1",
                     "glue": "tools/studio/karma_studio.c", "refs": ["v3.0.5", "v3.1"],
                     "release_status": {"718de454ee8ce0e5c6d12ec5fe5e0ba85d0cb4ba": "stable",
                                        "23304e93d17d12a79546879d60d58c5c3421aac5": "previous stable"},
                     "title": "KARMA studio twin", "license": "GPL-3.0-only", "generate": _generate_skiff,
                     "version_file": "firmware/src/ui.c", "version_re": r'#define FELUCCA_VERSION "([^"]*)"',
                     "identity_src": karma_identity, "folder": sloop_folder,
                     "objects": _karma_objects,
                     "defines": lambda v, i: [f'-DFELUCCA_VERSION="{v}"', f'-DFELUCCA_ID="{i}"'],
                     "notice": _karma_notice, "extra": _karma_extra, "tracks": 4, "projects": 4},
    # ChoralRoot FM-1: its FM-1 release tags; 0.39 is what its web installer offers (web/releases.json, the first entry,
    # 2026-11-10), 0.38 the release before. The folder is the release version (v0.39 -> 0.39); INFO says
    # "ChoralRoot 0.39". Its own browser emulator's flags (tools/emu/web/build_web.sh: gnu11, the single-thread lock)
    "choralroot-studio": {"firmware": "choralroot", "repo": "https://github.com/Quixotic7/ChoralRootFM1",
                          "glue": "tools/studio/choralroot_studio.c", "refs": ["v0.38", "v0.39"],
                          "release_status": {"36867e5c76fa1ecc02cba57102ee870695e1de10": "stable",
                                             "3cb2e8fd25d3d4292cb5c66c2623589d7a2ba8e3": "previous stable"},
                          "title": "ChoralRoot FM-1 studio twin", "license": "GPL-3.0-only", "generate": _generate_skiff,
                          "version_ref": choralroot_version, "identity_src": choralroot_identity,
                          "defines": lambda v, i: [f'-DFELUCCA_VERSION="ChoralRoot {v}"', f'-DFELUCCA_ID="{i}"'],
                          "cflags": ["-std=gnu11", "-Itools/emu/web/compat"],
                          "notice": _choralroot_notice, "extra": _choralroot_extra, "tracks": 2, "projects": 0},
}


def build_studio(work, sdk, local, ref, name="melodee-studio"):
    spec = STUDIOS[name]
    src, commit = source(spec["firmware"], work, local, ref=ref, repo_url=spec["repo"],
                         tree_name=f"{name}-{ref[:12]}-src")
    spec["generate"](src)
    version = (spec["version_ref"](src, ref) if spec.get("version_ref") else      # (CoralFlower: the release tag)
               re.search(spec["version_re"], (src / spec["version_file"]).read_text()).group(1))
    identity = spec["identity_src"](src) if spec.get("identity_src") else spec["identity"](version)
    glue = ROOT / spec["glue"]
    shutil.copy(glue, src / "tests" / "fm1c_studio.c")    # beside hostsim.c: its includes resolve as upstream's tests
    out = OUT / name / (spec["folder"](version) if spec.get("folder") else version.lstrip("vV"))
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    cc = sdk / "bin" / "clang"
    wasm = out / "twin.wasm"
    link = spec["objects"](src, sdk) if spec.get("objects") else []   # (KARMA: firmware/mi's C++, linked in)
    run([cc, f"--sysroot={sdk / 'share' / 'wasi-sysroot'}", "-O2", "-ffp-contract=off", "-std=gnu99", "-w",
         f"-I{SHIM}", "-Ibuild/gen", "-Ifirmware/src", "-Itests", "-mexec-model=reactor",
         *spec["defines"](version, identity), *spec.get("cflags", []),
         "-Wl,-z,stack-size=1048576", "-Wl,--initial-memory=33554432", "-Wl,--strip-all",
         "-o", wasm, "tests/fm1c_studio.c", *link], cwd=src)
    shutil.copy(src / "LICENSE", out / "LICENSE")
    if spec.get("extra"):
        spec["extra"](src, out)
    source_url = f"{spec['repo']}/archive/{commit}.tar.gz"
    (out / "NOTICE").write_text(spec["notice"](spec, version, ref, commit, source_url), "utf-8")
    extra = {"kind": "studio", "firmware": spec["firmware"], "firmware_version": version, "identity": identity,
             "abi": STUDIO_ABI, "wasm": "twin.wasm", "fs": 44100, "flash_size": 0x100000,
             "glue": spec["glue"], "glue_sha256": sha(glue), "source_url": source_url}
    if spec["firmware"] != "melodee":                     # (Melodee's manifests predate these: their defaults)
        extra.update(tracks=spec["tracks"], projects=spec["projects"])
    if commit in spec.get("release_status", {}):         # (JIANT: which build its installer calls stable)
        extra["release_status"] = spec["release_status"][commit]
    return out, commit, clang_version(cc), extra


build_melodee_studio = build_studio                       # (the name of before)


def studio_manifest(name, out: Path, ref, commit, toolchain, extra):
    spec = STUDIOS[name]
    files = {p.name: {"sha256": sha(p), "size": p.stat().st_size} for p in sorted(out.iterdir()) if p.is_file()
             and p.name != "manifest.json"}
    m = {"schema": 1, "name": name, "title": spec["title"], "version": out.name,
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
    ap.add_argument("--target", help="a studio twin (melodee-studio, skiff-studio, jiant-studio, sloop-studio, "
                    "coralflower-studio, karma-studio): with --ref one version, "
                    "else all in STUDIOS")
    ap.add_argument("--ref", action="append", default=[], help="firmware tag(s) or commit(s) for --target (repeatable)")
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
            raise SystemExit(f"unknown target {a.target} (known: {', '.join(STUDIOS)})")
        spec = STUDIOS[a.target]
        for ref in a.ref or spec["refs"]:
            print(f"== {a.target} {ref}")
            out, commit, tc, extra = build_studio(work, sdk, repos.get(spec["firmware"]), ref, a.target)
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
