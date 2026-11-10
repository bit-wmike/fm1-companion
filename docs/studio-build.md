# Studio twin: adding a new Melodee, Skiff or JIANT FM version

The Studio twin is the firmware's own code (Melodee's, Skiff's or JIANT FM's), compiled for the computer as
WebAssembly – together with a small glue layer (`tools/studio/melodee_studio.c`, `tools/studio/skiff_studio.c`,
`tools/studio/jiant_studio.c`) that exposes the
NOR flash, the USB SysEx path (editor protocol), keys/knobs/LEDs, the screen and rendering (ABI `st_*`, see
`fm1companion/sims/_studio/twin-core.js`). Several versions sit side by side:

```
fm1companion/sims/melodee-studio/<version>/   manifest.json, twin.wasm, LICENSE, NOTICE
fm1companion/sims/skiff-studio/<version>/     manifest.json, twin.wasm, LICENSE, NOTICE, LICENSING.md, THIRD_PARTY.txt
fm1companion/sims/jiant-studio/<version>/     manifest.json, twin.wasm, LICENSE, NOTICE, LICENSING.md, THIRD_PARTY.txt
fm1companion/sims/karma-studio/<version>/     manifest.json, twin.wasm, LICENSE, NOTICE, LICENSING.md, THIRD_PARTY.txt
fm1companion/sims/_studio/                    shared host (page, worker, AudioWorklet, control surface)
```

When opening, `studio.pick_build()` picks the firmware and version of the device or backup, otherwise the next older
version (with a warning). Bundled: Melodee 1.6.0-rc.7, rc.8, 1.6.0, 1.6.1; Skiff 2.1.1, 2.2; JIANT FM V10.60, V10.61, V10.6z2 (its stable);
KARMA 3.0.5, 3.1 (its stable) (about 0.9–1.4 MB of wasm each).

## Requirements (only on the build machine, not on the Pi)

* `git`, Python 3
* **Pillow** and **fontTools** – the firmwares' header generators (fonts, images) need them:
  `sudo apt install python3-pil python3-fonttools`
* **WASI SDK 27** (clang for wasm32-wasi). Without `--wasi-sdk DIR` the script downloads it to `--work` (~125 MB).
* for checking: `node` (≥ 18) for `tests/test_studio.py`, QtWebEngine for `tests/ui_studio_test.py`

## Building

```sh
git clone https://github.com/keremimo/melodee /path/to/melodee   # or use an existing clone (read only)
python3 tools/build_sims.py --target melodee-studio --ref v1.6.0-rc.9 --repo melodee=/path/to/melodee
```

This creates `fm1companion/sims/melodee-studio/1.6.0-rc.9/` (manifest with commit, toolchain, checksums,
`firmware_version`, `identity`, `abi`). If the version should permanently belong to the list, add it in
`tools/build_sims.py` under `STUDIOS["melodee-studio"]["refs"]` (then `--target melodee-studio` without `--ref`
builds all of them).

Skiff the same way (its clone read only; the CC0 samples are part of its tree):

```sh
git clone https://github.com/illuminus5/skiff /path/to/skiff
python3 tools/build_sims.py --target skiff-studio --ref v2.3 --repo skiff=/path/to/skiff
```

→ `fm1companion/sims/skiff-studio/2.3/`, identity `FM-1_7XY` (Skiff's `tools/build.py --release X.Y`), manifest with
`tracks` 6 and `projects` 4. The list is `STUDIOS["skiff-studio"]["refs"]`.

JIANT FM has no git tags; a version is a commit (JIANT's `web/STABLE` / `web/PUBLIC_RELEASE.json`, `web/EDITIONS.json`
and `web/BETA_VERSIONS` name the commits its installer offers):

```sh
git clone https://github.com/juanjiant-bit/JIANT /path/to/jiant
python3 tools/build_sims.py --target jiant-studio --ref <commit> --repo jiant=/path/to/jiant
```

→ `fm1companion/sims/jiant-studio/<version>/`, the folder named after the version the commit reports (`felucca.c`
`FELUCCA_VERSION`, e.g. "V10.6z2" → `10.6z2/`), identity read from the commit's `tools/build.py` (`PRODUCT`, FM-1_909:
JIANT's web installer builds without `--release`), `tracks` 6, `projects` 4 (the current song's sections A–D). The
list is `STUDIOS["jiant-studio"]["refs"]`; `STUDIOS["jiant-studio"]["release_status"]` writes each build's status
into its manifest (`release_status`).

| JIANT version | Commit | Status (manifest `release_status`, 2026-11-08) | Reports (INFO / identity) | Backup objects | Folder |
|---|---|---|---|---|---|
| V10.6z2 | cac23c6d0bb9a7f6986eb79bbe42ae710805ce98 | **stable** – the public installer's (`web/STABLE`, `web/PUBLIC_RELEASE.json`) | `JIANT V10.6z2` / `FM-1_909` | 35 (6 songs) | `jiant-studio/10.6z2` |
| V10.61 | 6c48e2a8e7049c31ab77c57b97c3ef6790d24090 | previous beta (`web/EDITIONS.json`, `web/BETA_VERSIONS`) | `JIANT V10.61` / `FM-1_909` | 43 (8 songs) | `jiant-studio/10.61` |
| V10.60 | 43b8ea8111949c990bf76523c7d4f4e77b715114 | previous stable (`web/EDITIONS.json`) | `JIANT V10.60` / `FM-1_909` | 43 (8 songs) | `jiant-studio/10.60` |

V10.60 and V10.61 stay for devices that still run them (and for their 8-song backups). A JIANT device answers INFO
"JIANT V10.6z2" (`protocol.Info.firmware_name` "jiant"); `studio.pick_build("jiant", …)` matches the version as JIANT
writes it; a backup of JIANT's own editor carries the INFO string as its `firmware` (and its 35- or 43-object
inventory). JIANT's release order (its git history): V10, V10.1 … V10.9, V10.10 … V10.61 (the number after the dot is a whole number), then V10.6c … V10.6z, V10.6z2 … V10.6z10; `studio.jiant_version_key` sorts exactly so, `studio.jiant_version` keeps JIANT's spelling ("V10.6z2") for matching and for the warnings. A version between two builds gets the next older one, with a warning that names it
("JIANT FM V10.6z10"). JIANT's later beta "V10.7" (2026-11-08, after V10.6z10) has the same string as the V10.7 of
2026-10-31; a version string cannot tell them apart and is read as the earlier one (it gets V10.60, with a warning).
From V10.6p JIANT keeps 6 songs (35 objects); an 8-song backup's songs 7 and 8 are refused by the device – the studio's
tolerant load leaves them out and names them, `backup.restore` stops before writing anything.

KARMA (a SLOOP fork with Mutable Instruments' Plaits / Elements in C++) has tags; a version is a tag (`v3.1`):

```sh
git clone https://github.com/eatyourgreensstudio-dotcom/karma-fm1 /path/to/karma
python3 tools/build_sims.py --target karma-studio --ref v3.1 --repo karma=/path/to/karma
```

→ `fm1companion/sims/karma-studio/3.1/` (the folder is the number of the version string `ui.c` FELUCCA_VERSION,
"KARMA 3.1"), identity read from the tag's `tools/build.py` (`PRODUCT`, FM-1_926: KARMA keeps it in every build),
`tracks` 4, `projects` 4 (the song's parts A–D). `firmware/mi/*.cc` is compiled with WASI SDK clang++ as KARMA's
`tools/plx_host.sh` builds it for its host tests (`-std=c++11 -DTEST -fno-exceptions -fno-rtti`) and linked in with
libc++ (`STUDIOS["karma-studio"]["objects"]`). The list is `STUDIOS["karma-studio"]["refs"]`, the status
`STUDIOS["karma-studio"]["release_status"]`.

| KARMA version | Tag / commit | Status (manifest `release_status`, 2026-11-10) | Reports (INFO / identity) | Backup objects | Folder |
|---|---|---|---|---|---|
| 3.1 | `v3.1` 718de454ee8ce0e5c6d12ec5fe5e0ba85d0cb4ba | **stable** – what its web installer serves (`docs/webapp/installer`: `karma-3.1.fwsc`, `tools/make_pages.sh` KARMA_VERSION) | `FELUCCA KARMA 3.1` / `FM-1_926` | 14 (15 with MIDI LEARN) | `karma-studio/3.1` |
| 3.0.5 | `v3.0.5` 23304e93d17d12a79546879d60d58c5c3421aac5 | previous stable (the installer's until 3.1) | `FELUCCA KARMA 3.0.5` / `FM-1_926` | 14 (15 with MIDI LEARN) | `karma-studio/3.0.5` |

The installer's package is the plain `build.sh` build (`build/karma.fwsc`, no `--release`), so the version string is
`ui.c`'s "KARMA 3.1" and the editor's INFO "FELUCCA KARMA 3.1" (`editor.c` ED_INFO: "FELUCCA " FELUCCA_VERSION);
`protocol.Info.firmware_name` is "karma", `firmware.guess_firmware` maps it and the identity FM-1_926 to "karma" (KARMA
is not in the bundled catalog: the twin and the device recognition work from INFO, identity and backup alone).
KARMA's versions sort with the generic `studio.version_key` (3.0.5 < 3.1). Before 3.0 (tag `v.3.0` = `v3.0`) KARMA
answered Felucca's FM-1_900.

KARMA speaks **SLOOP's backup protocol** (commands 34–36, `sloopbk.py`) and writes SLOOP's `sloop-backup` files
(`firmware` = its INFO string): `backup.SLOOP_PROTOCOL`. Its inventory (`editor.c` ED_BK_IDS): 0 the working project,
1 the settings (PER3, with the song order), 2..5 the song's parts A..D, 6..7 the user preset banks, 8 the FM6 bank
(always empty: FM6 is gone; a SLOOP bank is accepted and dropped), 9 the HEX drum kits (`HEX1`; SLOOP 2.5's `DSU1` SYN
kits do not fit and are left out, as KARMA's editor does), 10 MIDI LEARN (listed only when not the default), 32..35 the
sample slots (restored with SMP_BEGIN / WRITE / END). Its projects are format 7 (`FUN7`, `project.c` PROJ_MAGIC) with
SLOOP 2.6's 71 parameters: `sloopbk.dev_format` answers 7 for KARMA (KARMA's own editor answers 6 there and refuses its
own backups with `bkNewer`). A KARMA backup onto SLOOP is refused before anything is written; a SLOOP backup (up to
2.6) onto KARMA is allowed, as KARMA's editor allows it. Not in any backup: the three saved songs of SAVE on the SONG
screen (FM6's two sectors and 0xFB000) – KARMA's own backup leaves them out too.

Each `STUDIOS` entry names what differs between firmwares: `generate` (the header generators), `version_file` /
`version_re` (where the version string lives), `identity` (the release identity from the version) or `identity_src`
(read from the source tree, JIANT), `defines` (version and identity for the glue), `notice`, optionally `extra`
(further licence files), optionally `folder` (the folder from the version string, KARMA) and `objects` (further link
inputs, KARMA's C++), and, for the Python side, `tracks` and `projects`.
`build_studio()` is the same for all; a new firmware adds an entry, a glue file, and its key in
`fm1companion/studio.py` `FIRMWARES`.

If something in the firmware's interfaces changes between versions (names in `hostsim.c`, flash layout,
project storage), the glue must be adapted; the manifest records the checksum of the glue layer (`glue_sha256`),
`tests/test_studio.py` checks it – so after a change of a glue file, rebuild all versions of that firmware.
`skiff_studio.c` already covers 2.1.1 and 2.2 (2.2's timed MIDI out ring `mo` is detected by its `MQO` macro).

### The Skiff glue in short

`skiff_studio.c` follows Skiff's browser emulator (`web/emu/felucca_web.c`: the same HAL stand-ins, `main.c`'s boot
`web_power_on`, the clock driven by the audio, `audio.c`'s real ISR) and adds what the emulator leaves out:
`FELUCCA_OTA` 1 (usb.c's SysEx frames → `editor.c` `ed_service` → `ota_wire_send`; `ota.c` answers the identity
handshake, an update request is dropped), the user sample slots' flash hooks (`fl_erase4k` / `fl_write` on the RAM
NOR), USB = configured, and MIDI out drained from usb.c's rings. `st_boot` runs the splash's 430 ms (device time)
itself, so the panel answers at once. Skiff's editor TRANSPORT is command 76 (Melodee's stop is BANK_SONG 74 op 3), so
the glue exports `st_transport_set(how)` (0 stop, 1 play, 2 song, 3 restart); `twin-host.js` uses it when present and
falls back to Melodee's editor commands otherwise – the Melodee builds stay as they are.

### The JIANT glue in short

`jiant_studio.c` follows JIANT's browser emulator the same way (its HAL stand-ins for the V10 hardware: the supply
setup, the RAM code sections, the LED high level; `main.c`'s boot with `prefs_update`, the signature splash and
`session_dice` seeded as on the device), with `FELUCCA_OTA` 1 (the editor and `editor_backup.c`), `FELUCCA_CDC` 1 as the
release (the MENU row; nothing behind it), no sample-slot flash hooks (JIANT has none), USB MIDI out held to its tick
(`usb.c` `midi_out_due`). Bluetooth, the USB host and the second core stay out, as in the emulator (`FELUCCA_BT`,
`FELUCCA_USB_HOST`, `FELUCCA_DUAL_CORE` are off without `felucca.c`). `st_boot` runs the splash (1.73 s device time).

### The KARMA glue in short

KARMA's host tests and its browser simulator (`web/emu/sloop_web.c`) run the UI and the mix on doubles (no flash, no
editor); `karma_studio.c` therefore composes the firmware as `firmware/src/felucca.c` does (its include order: the UI,
the screen reader's `announce.c` / `ui_speak.c` from 3.1, `storage.c`, `upreset.c`, `project.c`, `ota.c`, `editor.c`,
`main.c`) on KARMA's own `tests/hostsim.c` base, with the HAL replaced as in the other twins (the flash hooks of
`hal/fm1_flash.h` on nor[], the sample slots USR1..USR4 read as XIP from nor[]). `KARMA_DUAL_CORE` 0 (KARMA's own
single-core build), `FELUCCA_UAC` 0, `FELUCCA_CDC` 0, no TRS MIDI, no `recovery.c`. `st_boot` runs the boot animation
(`ui_vis.c` boot_t, about 1.8 s device time). KARMA's editor has no transport command, so the glue exports
`st_transport_set` (0 stop, 1 PLAY, 2 the song from the top: song mode when the song order's parts exist, 3 restart)
and lets the audio ISR take the request at once.

## Checking

```sh
python3 -m pytest tests/test_studio.py -q                      # builds, firmware core in node, backup round trip
QT_QPA_PLATFORM=offscreen QTWEBENGINE_DISABLE_SANDBOX=1 python3 tests/ui_studio_test.py
QT_QPA_PLATFORM=offscreen QTWEBENGINE_DISABLE_SANDBOX=1 python3 tests/ui_studio_page_test.py
```

`tests/test_studio.py::test_builds_side_by_side` (Melodee), `test_skiff_builds_side_by_side` and
`test_jiant_builds_side_by_side` and `test_karma_builds_side_by_side` expect the bundled versions – add the new version
there. `tests/studio_twin_skiff.mjs`, `tests/studio_twin_jiant.mjs` and `tests/studio_twin_karma.mjs` are the Skiff,
JIANT and KARMA core checks in node (editor, identity, backup GET/PUT round trip, flash persistence, PLAY, audio, fork,
USB MIDI out, stop).
The packages (.deb, .run) include everything under `fm1companion/` (`tools/build_common.app_files`);
`tests/test_packaging.py` checks that the host files and every `twin.wasm` end up in the package.

## Other firmwares

SLOOP and FM1 Open follow the same pattern: their own entry in `STUDIOS`, their own glue layer
`tools/studio/<firmware>_studio.c` with the same `st_*` ABI, and an entry in `studio.FIRMWARES` (then
`StudioService.firmware_for()` knows it). The host (JS) and the rest of the Python side stay the same. SLOOP has its own
backup protocol and format (`sloopbk.py`); the twin's load and write back go through it, as the KARMA twin's already do
(`studio.FIRMWARES[...]["backup"] = "sloop"`, `sloopbk.restore_tolerant`; KARMA's glue composes a felucca.c-style
firmware on the host tests' base, a starting point for SLOOP's). See `docs/ROADMAP.md`.

Source tarballs for a release: `python3 tools/make_source_tarballs.py --repo melodee=… --repo skiff=… --repo JIANT=…`
(without `--repo` it looks in `/tmp/<name>`, then in lower case: `/tmp/jiant`, then the manifests' firmware key:
`/tmp/karma` for `karma-fm1`) packs every commit named in a manifest.
