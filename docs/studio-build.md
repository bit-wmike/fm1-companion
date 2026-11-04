# Studio twin: adding a new Melodee version

The Studio twin is Melodee's own firmware code, compiled for the computer as WebAssembly – together with
a small glue layer (`tools/studio/melodee_studio.c`) that exposes the NOR flash, the USB SysEx path (editor
protocol), keys/knobs/LEDs, the screen and rendering (ABI `st_*`, see
`fm1companion/sims/_studio/twin-core.js`). Several versions sit side by side:

```
fm1companion/sims/melodee-studio/<version>/   manifest.json, twin.wasm, LICENSE, NOTICE
fm1companion/sims/_studio/                    shared host (page, worker, AudioWorklet, control surface)
```

When opening, `studio.pick_build()` picks the version of the device or backup, otherwise the next older one (with
a warning).

## Requirements (only on the build machine, not on the Pi)

* `git`, Python 3
* **Pillow** and **fontTools** – Melodee's header generators (fonts, images) need them:
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

If something in Melodee's interfaces changes between versions (names in `hostsim.c`, flash layout,
project storage), `tools/studio/melodee_studio.c` must be adapted; the manifest records the checksum of the
glue layer (`glue_sha256`), `tests/test_studio.py` checks it.

## Checking

```sh
python3 -m pytest tests/test_studio.py -q                      # builds, firmware core in node, backup round trip
QT_QPA_PLATFORM=offscreen QTWEBENGINE_DISABLE_SANDBOX=1 python3 tests/ui_studio_test.py
QT_QPA_PLATFORM=offscreen QTWEBENGINE_DISABLE_SANDBOX=1 python3 tests/ui_studio_page_test.py
```

`tests/test_studio.py::test_builds_side_by_side` expects the bundled versions – add the new version there.
The packages (.deb, .run) include everything under `fm1companion/` (`tools/build_common.app_files`);
`tests/test_packaging.py` checks that the host files and every `twin.wasm` end up in the package.

## Other firmwares

SLOOP, Skiff and FM1 Open follow the same pattern: their own entry in `STUDIOS`, their own glue layer
`tools/studio/<firmware>_studio.c` with the same `st_*` ABI. The host (JS) and Python (`fm1companion/studio.py`,
`ui/studio_service.py`, page `ui/pages/studio.py`) stay the same; `StudioService.firmware_for()` must know the new
firmware. See `docs/ROADMAP.md`.
