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
fm1companion/sims/_studio/                    shared host (page, worker, AudioWorklet, control surface)
```

When opening, `studio.pick_build()` picks the firmware and version of the device or backup, otherwise the next older
version (with a warning). Bundled: Melodee 1.6.0-rc.7, rc.8, 1.6.0, 1.6.1; Skiff 2.1.1, 2.2; JIANT FM V10.60, V10.61
(about 1.2–1.4 MB of wasm each).

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

JIANT FM has no git tags; a version is a commit (JIANT's `web/EDITIONS.json` names the commits its installer offers):

```sh
git clone https://github.com/juanjiant-bit/JIANT /path/to/jiant
python3 tools/build_sims.py --target jiant-studio --ref <commit> --repo jiant=/path/to/jiant
```

→ `fm1companion/sims/jiant-studio/<version>/`, the folder named after the version the commit reports (`felucca.c`
`FELUCCA_VERSION`, e.g. "V10.60" → `10.60/`), identity read from the commit's `tools/build.py` (`PRODUCT`, FM-1_909:
JIANT's web installer builds without `--release`), `tracks` 6, `projects` 4 (the current song's sections A–D). The
list is `STUDIOS["jiant-studio"]["refs"]`.

| JIANT version | Commit | Status in `web/EDITIONS.json` | Reports (INFO / identity) | Folder |
|---|---|---|---|---|
| V10.60 | 43b8ea8111949c990bf76523c7d4f4e77b715114 | stable (the official installer) | `JIANT V10.60` / `FM-1_909` | `jiant-studio/10.60` |
| V10.61 | 6c48e2a8e7049c31ab77c57b97c3ef6790d24090 | beta | `JIANT V10.61` / `FM-1_909` | `jiant-studio/10.61` |

A JIANT device answers INFO "JIANT V10.60" (`protocol.Info.firmware_name` "jiant"); `studio.pick_build` matches the
version ("v10.60.0"); a backup of JIANT's own editor carries the INFO string as its `firmware` (and the 43-object
inventory). JIANT's later versions use letter suffixes (V10.6c … V10.6z10, V10.7), which `studio.version_key` reads
as 10.6 / 10.7, before 10.60: they get V10.60 with a warning.

Each `STUDIOS` entry names what differs between firmwares: `generate` (the header generators), `version_file` /
`version_re` (where the version string lives), `identity` (the release identity from the version) or `identity_src`
(read from the source tree, JIANT), `defines` (version and identity for the glue), `notice`, optionally `extra`
(further licence files) and, for the Python side, `tracks` and `projects`.
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

## Checking

```sh
python3 -m pytest tests/test_studio.py -q                      # builds, firmware core in node, backup round trip
QT_QPA_PLATFORM=offscreen QTWEBENGINE_DISABLE_SANDBOX=1 python3 tests/ui_studio_test.py
QT_QPA_PLATFORM=offscreen QTWEBENGINE_DISABLE_SANDBOX=1 python3 tests/ui_studio_page_test.py
```

`tests/test_studio.py::test_builds_side_by_side` (Melodee), `test_skiff_builds_side_by_side` and
`test_jiant_builds_side_by_side` expect the bundled versions – add the new version there. `tests/studio_twin_skiff.mjs`
and `tests/studio_twin_jiant.mjs` are the Skiff and JIANT core checks in node (editor, identity, backup GET/PUT round
trip, flash persistence, PLAY, audio, fork, USB MIDI out, stop).
The packages (.deb, .run) include everything under `fm1companion/` (`tools/build_common.app_files`);
`tests/test_packaging.py` checks that the host files and every `twin.wasm` end up in the package.

## Other firmwares

SLOOP and FM1 Open follow the same pattern: their own entry in `STUDIOS`, their own glue layer
`tools/studio/<firmware>_studio.c` with the same `st_*` ABI, and an entry in `studio.FIRMWARES` (then
`StudioService.firmware_for()` knows it). The host (JS) and the rest of the Python side stay the same. SLOOP has its own
backup protocol and format (`sloopbk.py`); the twin's load and write back would go through it. See `docs/ROADMAP.md`.

Source tarballs for a release: `python3 tools/make_source_tarballs.py --repo melodee=… --repo skiff=… --repo JIANT=…`
(without `--repo` it looks in `/tmp/<name>`, then in lower case: `/tmp/jiant`) packs every commit named in a manifest.
