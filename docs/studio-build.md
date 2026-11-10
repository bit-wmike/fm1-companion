# Studio twin: adding a new Melodee, Skiff, JIANT FM or CoralFlower version

The Studio twin is the firmware's own code (Melodee's, Skiff's, JIANT FM's or CoralFlower's), compiled for the computer
as WebAssembly – together with a small glue layer (`tools/studio/melodee_studio.c`, `tools/studio/skiff_studio.c`,
`tools/studio/jiant_studio.c`, `tools/studio/coralflower_studio.c`) that exposes the
NOR flash, the USB SysEx path (editor protocol), keys/knobs/LEDs, the screen and rendering (ABI `st_*`, see
`fm1companion/sims/_studio/twin-core.js`). Several versions sit side by side:

```
fm1companion/sims/melodee-studio/<version>/   manifest.json, twin.wasm, LICENSE, NOTICE
fm1companion/sims/skiff-studio/<version>/     manifest.json, twin.wasm, LICENSE, NOTICE, LICENSING.md, THIRD_PARTY.txt
fm1companion/sims/jiant-studio/<version>/     manifest.json, twin.wasm, LICENSE, NOTICE, LICENSING.md, THIRD_PARTY.txt
fm1companion/sims/coralflower-studio/<version>/  manifest.json, twin.wasm, LICENSE, NOTICE, LICENSING.md,
                                              LICENSING-choralroot.md, THIRD_PARTY.txt
fm1companion/sims/_studio/                    shared host (page, worker, AudioWorklet, control surface)
```

When opening, `studio.pick_build()` picks the firmware and version of the device or backup, otherwise the next older
version (with a warning). Bundled: Melodee 1.6.0-rc.7, rc.8, 1.6.0, 1.6.1; Skiff 2.1.1, 2.2; JIANT FM V10.60, V10.61, V10.6z2 (its stable);
CoralFlower 0.7, 0.7.1 (its stable) (about 1.2–1.4 MB of wasm each, CoralFlower's 0.7 MB).

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

CoralFlower's releases are tags `vX.Y[.Z]`; its web installer offers the first entry of `web/releases.json`, built by
its release workflow with `tools/build.py --release X.Y` (the INFO text "CoralFlower X.Y", the identity `PRODUCT`
FM-1_930 for every release):

```sh
git clone https://github.com/FicusFicusov/CoralFlower /path/to/coralflower
python3 tools/build_sims.py --target coralflower-studio --ref v0.8 --repo coralflower=/path/to/coralflower
```

→ `fm1companion/sims/coralflower-studio/0.8/`, the version from the tag (`version_ref`: CoralFlower's version string is
not in its source, `--release` sets it), identity read from the commit's `tools/build.py` (`PRODUCT`), `tracks` 4 (its
parts: chords, bass, the LAYER, drums), `projects` 0. The list is `STUDIOS["coralflower-studio"]["refs"]`; the build
takes CoralFlower's own emulator flags (`cflags`: gnu11 and its single-thread `<os/lock.h>`, `tools/emu/web/compat`).

| CoralFlower version | Tag / commit | Status (manifest `release_status`, 2026-11-10) | Reports (INFO / identity) | Backup objects | Folder |
|---|---|---|---|---|---|
| 0.7.1 | v0.7.1 / 46790b943bdb5e4143212d83dc495c832eefdbb8 | **stable** – the first entry of `web/releases.json`, what its installer offers | `CoralFlower 0.7.1` / `FM-1_930` | 26 | `coralflower-studio/0.7.1` |
| 0.7 | v0.7 / 65c6b767cc446b1c2b85c32b0812d50719fd10a4 | previous stable (`web/releases.json`) | `CoralFlower 0.7` / `FM-1_930` | 26 | `coralflower-studio/0.7` |

A CoralFlower device answers INFO "CoralFlower 0.7.1" (`protocol.Info.firmware_name` "coralflower", the catalog's key;
`firmware.guess_firmware` also maps the identity FM-1_930); CoralFlower's web installer writes the INFO string as a
backup's `firmware`, and its inventory (26 objects since 0.5: ChoralRoot's sounds 6–11, the settings 30, the loop 31,
the sequencer's store 50–55, the sounds' edits 56–58, the pattern sounds 59–67; 8, 14 and 17 objects in 0.1–0.4.2)
gives it away too (`backup.is_coralflower`). Its versions sort with the generic key (0.6.1 < 0.6.2-rc1 < 0.7 < 0.7.1).
CoralFlower speaks only the backup part of the protocol (INFO, LIST, GET, PUT, RESTART 72; no editor): a restore ends
in RESTART (`backup.restart_coralflower`), and the studio power-cycles its twin after loading a backup.

Each `STUDIOS` entry names what differs between firmwares: `generate` (the header generators), `version_file` /
`version_re` (where the version string lives), `identity` (the release identity from the version) or `identity_src`
(read from the source tree, JIANT), `defines` (version and identity for the glue), `notice`, optionally `extra`
(further licence files) and, for the Python side, `tracks` and `projects`.
`build_studio()` is the same for all; a new firmware adds an entry, a glue file, and its key in
`fm1companion/studio.py` `FIRMWARES`. Optional: `version_ref` (the version from the ref, CoralFlower), `cflags`
(further compiler flags), `release_status`.

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

### The CoralFlower glue in short

`coralflower_studio.c` includes CoralFlower's own browser emulator whole (`tools/emu/web/emu_web.c`: the firmware side
`tools/emu/emu_fw.c` with its include list `emu_firmware.h`, the HAL `emu_hal_fw.h`, the flash image staged before
power-on), as `tests/cf_backup_test.c` builds the firmware, and adds what the emulator leaves out: `FELUCCA_OTA` 1
(usb.c's SysEx frame and ring; `cf_backup.c`'s replies go out through `ota_wire_send`, `ota.c` answers the identity
handshake) and the `st_*` ABI. The clock is the emulator's (tick, a UI frame every 15 ms or the input scan, the audio
blocks due), the audio kept at 24 bits; a flash erase holds the audio 45 ms as on the device. `st_boot` runs the
emulator's boot time (1.6 s) and the power-on sound to its end. RESTART only replies (the host restarts the twin).
Optional exports: `st_transport_set` (PLAY's events: stop, the pattern of PLAY on HOME, the song) and
`st_track_mute_set` (the stems; CoralFlower has no editor TRACK_MIX; a muted part starts no note, voice.c `P_MUTE`, and
the click and the power-on sound are held silent in a stem). The update gesture does nothing (`exit` is a no-op).

## Checking

```sh
python3 -m pytest tests/test_studio.py -q                      # builds, firmware core in node, backup round trip
QT_QPA_PLATFORM=offscreen QTWEBENGINE_DISABLE_SANDBOX=1 python3 tests/ui_studio_test.py
QT_QPA_PLATFORM=offscreen QTWEBENGINE_DISABLE_SANDBOX=1 python3 tests/ui_studio_page_test.py
```

`tests/test_studio.py::test_builds_side_by_side` (Melodee), `test_skiff_builds_side_by_side` and
`test_jiant_builds_side_by_side` and `test_coralflower_builds_side_by_side` expect the bundled versions – add the new version there. `tests/studio_twin_skiff.mjs`
and `tests/studio_twin_jiant.mjs` are the Skiff and JIANT core checks in node (editor, identity, backup GET/PUT round
trip, flash persistence, PLAY, audio, fork, USB MIDI out, stop); `tests/studio_twin_coralflower.mjs` the CoralFlower
one (INFO, identity, a take recorded on the panel, the 26-object GET/PUT round trip with RESTART, the power cycle,
PLAY, audio, fork, MIDI out, transport, mutes).
The packages (.deb, .run) include everything under `fm1companion/` (`tools/build_common.app_files`);
`tests/test_packaging.py` checks that the host files and every `twin.wasm` end up in the package.

## Other firmwares

SLOOP and FM1 Open follow the same pattern: their own entry in `STUDIOS`, their own glue layer
`tools/studio/<firmware>_studio.c` with the same `st_*` ABI, and an entry in `studio.FIRMWARES` (then
`StudioService.firmware_for()` knows it). The host (JS) and the rest of the Python side stay the same. SLOOP has its own
backup protocol and format (`sloopbk.py`); the twin's load and write back would go through it. See `docs/ROADMAP.md`.

Source tarballs for a release: `python3 tools/make_source_tarballs.py --repo melodee=… --repo skiff=… --repo JIANT=…
--repo CoralFlower=…` (without `--repo` it looks in `/tmp/<name>`, then in lower case: `/tmp/jiant`, `/tmp/coralflower`)
packs every commit named in a manifest.
