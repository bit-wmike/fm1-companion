# Contributing to the FM-1 Companion Suite

Thanks for wanting to help! The suite is an unofficial community project for the M-VAVE FM-1
(GPL-3.0-only). Contributions are welcome: bug reports, **test reports from real hardware**,
translations, code and documentation.

Please read [SECURITY.md](SECURITY.md) first (do not report security vulnerabilities publicly) and the
[Code of Conduct](CODE_OF_CONDUCT.md). How the code is structured is described in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Language

* **Everything public is in English:** commit messages, pull requests, issues, review comments and the
  documentation in this repository.
* **UI texts go through the translation system.** The app's UI is currently German; English is in progress.
  Do not hard-code new UI strings in either language outside that system.
* **Translations:** see [docs/TRANSLATING.md](docs/TRANSLATING.md).

## Development environment

The app runs with Debian/Ubuntu packages, without pip and without a compiler on the target machine.

```bash
git clone https://github.com/bit-wmike/fm1-companion.git
cd fm1-companion
# System packages (the list is in fm1companion/data/deps.json):
sudo apt install python3 python3-pyqt6 python3-pyqt6.qtsvg python3-pyqt6.qtwebengine \
  python3-pyqt6.qtwebchannel python3-mido python3-rtmidi python3-numpy git qt6-translations-l10n
# recommended: python3-pyqt6.qtmultimedia ffmpeg alsa-utils pipewire-bin fluidsynth fluid-soundfont-gm
python3 -m fm1companion --check        # anything missing?
python3 -m fm1companion --demo         # without an FM-1: two simulated devices
```

For tests you also need `python3-pytest`. Target versions: Python ≥ 3.11, PyQt6 ≥ 6.4, NumPy ≥ 1.24 (Debian
Bookworm). Please do not use language features or APIs that came later (e.g. PEP 695, NumPy 2 functions).

## Tests

```bash
# Unit tests (no display)
QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_*.py fm1companion/vendor/unbricker/tests -q
# UI tests: each script on its own, no display, in demo mode with its own temporary HOME
QT_QPA_PLATFORM=offscreen python3 tests/ui_flow_test.py
QT_QPA_PLATFORM=offscreen python3 tests/ui_smoke.py screenshots   # screenshots of all pages
```

As root (e.g. in a container) Qt WebEngine needs `QTWEBENGINE_DISABLE_SANDBOX=1`. The UI scripts print
`N/M passed` at the end and exit with code 0 only if everything passed. New features get a
test; you test device access against the simulation (`fm1companion/demo_device.py`, `FM1C_DEMO=1`).

## Code style

* UI: short and clear, consistent terms ("FM-1", "Firmware", "Backup", "Preset", "Projekt"/project, "Sample").
  The German UI addresses the user informally ("du"). No "TODO"s in the UI. All UI texts go through the
  translation system (see above).
* Every Python file starts with `# SPDX-License-Identifier: GPL-3.0-only`.
* Lines up to 120 characters. `ruff check --select E9,F63,F7 .` must be clean; please do not add new
  unused imports.
* Never block the GUI thread (no network, no `sleep`, no device requests) – use `run_job`
  and `ctx.job` for that (see docs/ARCHITECTURE.md).
* Import optional dependencies lazily only, with a user-facing hint ("sudo apt install …");
  every new package dependency goes into `fm1companion/data/deps.json`.

## Safety rules (device and user data)

These rules are more important than any feature:

1. **Never write to the FM-1 without being asked.** Every writing action needs an explicit user action and
   a confirmation that says exactly what will be overwritten. Read first so that undo is possible;
   offer backups before installations.
2. **Firmware update and boot mode SysEx** (`F0 22 24 35 7F`, `F0 22 24 35 7D`, identity query, update
   data stream) must never reach the device from web pages, simulators or online editors
   (`sims.sim_out_allowed`, `sims.OutGate`).
3. New flash, rescue or write paths stay marked as **untested** (in the app and in
   [docs/UNTESTED.md](docs/UNTESTED.md)) until someone has checked them on real hardware.
4. **No personal data** in code, tests, fixtures or docs: no real names, host names,
   home paths, serial numbers, tokens. Examples use `/home/user`, `pi`, "Max Mustermann".
5. Everything the app hands out (error report, diagnostics, copied reports) goes through
   `fm1companion/redact.py`. Network access only over https and only on click – the two automatic requests
   that can be switched off are listed in the README under "Privacy".
6. Code from third-party repos is not run without asking (see `firmware.check_tool`).

## Building the simulators and the Studio twin

The bundled WebAssembly files under `fm1companion/sims/` are built in a development environment,
never on the users' machines:

```bash
python3 tools/build_sims.py --repo fm1-open=/path/to/fm1_open --repo melodee=/path/to/melodee
python3 tools/build_sims.py --target melodee-studio --ref v1.6.0 --repo melodee=/path/to/melodee
```

Requires git, WASI SDK 27 (the script downloads it if needed) and, for Melodee, Python with Pillow and fontTools.
Every build writes `manifest.json` (checksums, commit) and a `NOTICE` with attribution and
GPL offer. For a release, create the source tarballs and attach them:

```bash
python3 tools/make_source_tarballs.py --repo melodee=/path/to/melodee --repo fm1_open=/path/to/fm1_open
```

## Building packages

```bash
python3 tools/build_deb.py     # dist/fm1-companion_<version>_all.deb
python3 tools/build_run.py     # dist/FM-1-Companion-Suite-<version>.run
```

## Hardware tests

What helps most is an honest test report from real hardware (template "Hardware test report" in the
issues): device, firmware before/after, what exactly was done, result. Please only with a backup and at
your own risk – see the warning in the README.

## Pull requests

* One topic per pull request, a short description of what and why – in English.
* Run the tests and put the result in the PR description.
* By contributing you agree that your contribution is published under GPL-3.0-only.
