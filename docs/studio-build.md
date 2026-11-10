# Studio twin: adding a new Melodee, Skiff, JIANT FM or SLOOP version

The Studio twin is the firmware's own code (Melodee's, Skiff's, JIANT FM's or SLOOP's), compiled for the computer as
WebAssembly – together with a small glue layer (`tools/studio/melodee_studio.c`, `tools/studio/skiff_studio.c`,
`tools/studio/jiant_studio.c`, `tools/studio/sloop_studio.c`) that exposes the
NOR flash, the USB SysEx path (editor protocol), keys/knobs/LEDs, the screen and rendering (ABI `st_*`, see
`fm1companion/sims/_studio/twin-core.js`). Several versions sit side by side:

```
fm1companion/sims/melodee-studio/<version>/   manifest.json, twin.wasm, LICENSE, NOTICE
fm1companion/sims/skiff-studio/<version>/     manifest.json, twin.wasm, LICENSE, NOTICE, LICENSING.md, THIRD_PARTY.txt
fm1companion/sims/jiant-studio/<version>/     manifest.json, twin.wasm, LICENSE, NOTICE, LICENSING.md, THIRD_PARTY.txt
fm1companion/sims/sloop-studio/<version>/     manifest.json, twin.wasm, LICENSE, NOTICE, LICENSING.md, THIRD_PARTY.txt
fm1companion/sims/_studio/                    shared host (page, worker, AudioWorklet, control surface)
```

When opening, `studio.pick_build()` picks the firmware and version of the device or backup, otherwise the next older
version (with a warning). Bundled: Melodee 1.6.0-rc.7, rc.8, 1.6.0, 1.6.1; Skiff 2.1.1, 2.2; JIANT FM V10.60, V10.61, V10.6z2 (its stable);
SLOOP 2.8.4, 2.8.5 (its stable) (about 0.9–1.4 MB of wasm each).

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

SLOOP the same way, by its release tags (SLOOP's web installer ships the current stable, `docs/firmware/sloop-X.Y.Z.fwsc`;
its clone read only):

```sh
git clone https://github.com/isod89/sloop-fm1 /path/to/sloop
python3 tools/build_sims.py --target sloop-studio --ref v2.8.6 --repo sloop=/path/to/sloop
```

→ `fm1companion/sims/sloop-studio/<version>/`, the folder named after the number of the version the tag reports
(`firmware/src/ui.c` `FELUCCA_VERSION`, "SLOOP 2.8.5" → `2.8.5/`; the manifest's `firmware_version` keeps "SLOOP 2.8.5",
`studio.builds("sloop")` calls the build "2.8.5"), identity read from the tag's `tools/build.py` (`PRODUCT`, FM-1_900:
SLOOP's releases are built without `--release`, whose X.Y has no room for 2.8.5; FM-1_900 is also Felucca's own, so
the identity alone does not tell SLOOP – its INFO does), `tracks` 4, `projects` 4 (the sections A–D). The list is
`STUDIOS["sloop-studio"]["refs"]`. SLOOP's versions sort with the generic `studio.version_key` (2.8.4 < 2.8.5 < 2.10).

| SLOOP version | Tag / commit | Status (2026-11-10) | Reports (INFO / identity) | Backup objects (BK_LIST) | Folder |
|---|---|---|---|---|---|
| 2.8.5 | v2.8.5 / bac9c05ea7627449405aa10e48da21d75b6c8c56 | **stable** – the one SLOOP's web installer ships (`docs/firmware/sloop-2.8.5.fwsc`, README "Status") | `FELUCCA SLOOP 2.8.5` (protocol byte 12) / `FM-1_900` | 14 (+ 10, 11 when set) | `sloop-studio/2.8.5` |
| 2.8.4 | v2.8.4 / 91f30182d9095e0ecdb43396554f2ce5a67df179 | previous release | `FELUCCA SLOOP 2.8.4` (protocol byte 12) / `FM-1_900` | 14 (+ 10, 11 when set) | `sloop-studio/2.8.4` |

A SLOOP device answers INFO "FELUCCA SLOOP 2.8.5" (`protocol.Info.firmware_name` "sloop", the catalog's key `sloop`,
`firmware.guess_firmware` "sloop"); `studio.pick_build("sloop", …)` matches the version number; an older SLOOP (2.8.3)
gets 2.8.4 with a warning, a newer one 2.8.5. SLOOP has its own backup protocol and file format (commands 34–36,
"sloop-backup", `sloopbk.py`; `backup.py` picks it by INFO): objects 0 the running project, 1 the settings (PER3, with
the song order), 2–5 the sections A–D (FUN6, 3816 bytes; empty = length 0), 6–7 the user preset banks, 8 the FM6 bank,
9 the SYN drum kits, 10 MIDI LEARN (listed only when not the default), 11 the theme (only when there is one), 32–35 the
sample slots USR1–4 (restored through SMP_BEGIN / WRITE / END). The twin loads and writes it as SLOOP's web editor does
(`sloopbk.restore`, its order: banks, sections, the running project, the settings, the samples); the studio's tolerant
load (`studio.restore_tolerant`) takes each object on its own and names the ones the firmware refuses. A backup of the
app, of the twin and of SLOOP's own web editor (no companion block: its `format` tells) all open the SLOOP twin.

Each `STUDIOS` entry names what differs between firmwares: `generate` (the header generators), `version_file` /
`version_re` (where the version string lives), `identity` (the release identity from the version) or `identity_src`
(read from the source tree, JIANT, SLOOP), `defines` (version and identity for the glue), `notice`, optionally `extra`
(further licence files), `folder` (the folder from the version, SLOOP's "SLOOP 2.8.5" → `2.8.5`) and, for the Python
side, `tracks` and `projects`.
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

### The SLOOP glue in short

SLOOP has no browser emulator of the whole firmware (its web preview is the DSP and the sequencer only), so
`sloop_studio.c` follows SLOOP's own host harness of the whole firmware, `tests/editor_host.c` (the editor's protocol with
the real `storage.c` / `upreset.c` / `project.c` on a RAM image of the NOR, the user sample slots, the FM6 bank and the
user presets read in place as the XIP window, the flash ranges of `hal/fm1_flash.h`), and adds `main.c`'s boot
(`felucca_init`, the logo, the project as it was left), `audio.c`'s real ISR, `ota.c`'s identity handshake, the MIDI out
ring, the LEDs and the screen. `FELUCCA_CPU1` 0 as the host tests (one core: MENU › 2ND CORE has no second core here),
`FELUCCA_CDC` 0 as the release, no USB audio, no TRS MIDI, no USB rescue (`recovery.c`). `st_boot` runs the logo's 930 ms
(device time). `st_transport_set(how)`: 0 stop (seq.c's STOP at the next audio block, the clock runs until then), 1 PLAY
in the twin's mode, 2 the song from the top (SAVE layer › SONG MODE: `arrangement_enabled` when the settings' song order
is playable, else the loop as 1), 3 restart. `st_project_current` is SLOOP's `live_sec` (the section playing; 255: the
working project). 2.8.5's `hal/fm1_flash_id.h` (`FL_JEDEC_OK`) is included when the tag has it.

## Checking

```sh
python3 -m pytest tests/test_studio.py -q                      # builds, firmware core in node, backup round trip
QT_QPA_PLATFORM=offscreen QTWEBENGINE_DISABLE_SANDBOX=1 python3 tests/ui_studio_test.py
QT_QPA_PLATFORM=offscreen QTWEBENGINE_DISABLE_SANDBOX=1 python3 tests/ui_studio_page_test.py
```

`tests/test_studio.py::test_builds_side_by_side` (Melodee), `test_skiff_builds_side_by_side`,
`test_jiant_builds_side_by_side` and `test_sloop_builds_side_by_side` expect the bundled versions – add the new version
there. `tests/studio_twin_skiff.mjs`, `tests/studio_twin_jiant.mjs` and `tests/studio_twin_sloop.mjs` are the Skiff,
JIANT and SLOOP core checks in node (editor, identity, backup GET/PUT round trip – SLOOP's with its commands 34–36 –,
flash persistence, PLAY, audio, fork, stop).
The packages (.deb, .run) include everything under `fm1companion/` (`tools/build_common.app_files`);
`tests/test_packaging.py` checks that the host files and every `twin.wasm` end up in the package.

## Other firmwares

FM1 Open follows the same pattern: its own entry in `STUDIOS`, its own glue layer `tools/studio/<firmware>_studio.c`
with the same `st_*` ABI, and an entry in `studio.FIRMWARES` (then `StudioService.firmware_for()` knows it). The host
(JS) and the rest of the Python side stay the same. See `docs/ROADMAP.md`.

Source tarballs for a release: `python3 tools/make_source_tarballs.py --repo melodee=… --repo skiff=… --repo JIANT=…
--repo sloop-fm1=…` (without `--repo` it looks in `/tmp/<name>`, then in lower case: `/tmp/jiant`, then an alias:
`/tmp/sloop` for sloop-fm1) packs every commit named in a manifest.
