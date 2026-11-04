# Sound – presets and FM voices (notes)

## Melodee preset library (command 80)
* Source: `web/EDITOR_PROTOCOL.md` "Shared user library", editor `sharedBank` (web/editor.html), firmware
  `firmware/src/editor_library.c`. `presetlib.SharedBank` builds the requests byte-exactly like the editor
  (`tests/test_presets.py::test_put_args_byte_exact`) and runs against the editor mock (`tests/mock_fm1_shared.mjs`,
  `makeMockDevice({shared:true})`).
* **v14 is offset-8192** (firmware `ed_rv`, editor `v14enc`). `protocol.v14/r14` use two's complement –
  wrong for real devices (affects DESC/GET/SET); presetlib has its own encoding.
* The firmware's PUT rules: name 1–12 (native 1–20) printable characters, values −64…127, FM6/PROPHET only with
  native data (kind 1/3), bit 6 = Felucca default controls (for DX7 imports), bit 5 = keep native name.
* Write/rename/delete/save call `ed_flash_stop()` → **they stop playback**; favourites
  (FAV_SET 38, category 16 or NENGINES) answer rc 4 = "saved after STOP". The page shows both.
* Older Melodee (UP_* 16–21, 64 slots) is detected (UP_LIST answers) and clearly refused (hint to 1.5+).

## Sending DX7 (without Felucca)
Following "FM1 Editor & Librarian" (benny-sparra/fm1-dx7-patch-importer, MIT, docs/fm1-research.md):
M-VAVE firmware (identity FM-1_0xx ≤ 019) accepts the single-voice dump (edit buffer, SAVE on the device);
FM-1+VA and unknown devices get 155 parameter changes `F0 43 1n gg pp dd F7` (they overwrite nothing).
Melodee and FM1 Open accept the single dump (Melodee: FM6 track; FM1 Open: tracks with a matching input channel).

## Renderer
`fmrender.py` ports msfa/Dexed maths (Apache-2.0) from FM1 Open `web/synth/dx7*.c`. Measured in the container:
≈0.03–0.06 s for 2 s of sound (feedback chain in pure Python). The LFO is simplified.

## Collections
Pinned to commits, downloaded via raw.githubusercontent.com (no API): KingParamount/fm1-factory-presets (CC0),
mene311/fm1-banks (MIT), Yamaha ROMs and Grey Matter E! from benny-sparra/fm1-dx7-patch-importer (licence unclear).
