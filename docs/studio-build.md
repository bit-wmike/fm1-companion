# Studio twin: adding a new Melodee, Skiff, JIANT FM, SLOOP, CoralFlower, KARMA or FM1 Open version

The Studio twin is the firmware's own code (Melodee's, Skiff's, JIANT FM's, SLOOP's, CoralFlower's or KARMA's), compiled
for the computer as WebAssembly – together with a small glue layer (`tools/studio/melodee_studio.c`,
`tools/studio/skiff_studio.c`, `tools/studio/jiant_studio.c`, `tools/studio/sloop_studio.c`,
`tools/studio/coralflower_studio.c`, `tools/studio/karma_studio.c`) that exposes the
NOR flash, the USB SysEx path (editor protocol), keys/knobs/LEDs, the screen and rendering (ABI `st_*`, see
`fm1companion/sims/_studio/twin-core.js`). Several versions sit side by side:

```
fm1companion/sims/melodee-studio/<version>/   manifest.json, twin.wasm, LICENSE, NOTICE
fm1companion/sims/skiff-studio/<version>/     manifest.json, twin.wasm, LICENSE, NOTICE, LICENSING.md, THIRD_PARTY.txt
fm1companion/sims/jiant-studio/<version>/     manifest.json, twin.wasm, LICENSE, NOTICE, LICENSING.md, THIRD_PARTY.txt
fm1companion/sims/sloop-studio/<version>/     manifest.json, twin.wasm, LICENSE, NOTICE, LICENSING.md, THIRD_PARTY.txt
fm1companion/sims/coralflower-studio/<version>/  manifest.json, twin.wasm, LICENSE, NOTICE, LICENSING.md,
                                              LICENSING-choralroot.md, THIRD_PARTY.txt
fm1companion/sims/karma-studio/<version>/     manifest.json, twin.wasm, LICENSE, NOTICE, LICENSING.md, THIRD_PARTY.txt
fm1companion/sims/felucca-studio/<version>/   manifest.json, twin.wasm, LICENSE, NOTICE, LICENSING.md, THIRD_PARTY.txt
fm1companion/sims/fm1-open-studio/<version>/  manifest.json, twin.wasm, LICENSE (FM1 Open's LICENSE.md), NOTICE, THIRD_PARTY.md
fm1companion/sims/_studio/                    shared host (page, worker, AudioWorklet, control surface)
```

When opening, `studio.pick_build()` picks the firmware and version of the device or backup, otherwise the next older
version (with a warning). Bundled: Melodee 1.6.0-rc.7, rc.8, 1.6.0, 1.6.1; Skiff 2.1.1, 2.2; JIANT FM V10.60, V10.61, V10.6z2 (its stable);
SLOOP 2.8.4, 2.8.5 (its stable) (about 0.9–1.4 MB of wasm each);
CoralFlower 0.7, 0.7.1 (its stable) (about 1.2–1.4 MB of wasm each, CoralFlower's 0.7 MB);
KARMA 3.0.5, 3.1 (its stable) (about 0.9–1.4 MB of wasm each);
Felucca 1.8.0, 1.8.1 (its stable) (about 0.9 MB of wasm each);
M-VAVE's FM1 Open V14 (0.3 MB of wasm; also the stand-in for the original firmware V15/V16).

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

Felucca (the base of the family) has release tags `vX.Y[.Z]`; its release workflow builds them with
`tools/build.py --release X.Y.Z` (the identity FM-1_9XY, the version string `vX.Y.Z` = `felucca.c` FELUCCA_VERSION at the
tag). Its hardware layer and six engines are **git submodules** (`firmware/bsp` = felucca-bsp, MPL-2.0;
`firmware/engines` = Felucca-Engines, MIT): `git archive` leaves them empty, so the build puts each in at the commit the
tag records (`STUDIOS["felucca-studio"]["submodules"]`, `_submodules()`; a clone from `--repo felucca-bsp=…` /
`--repo Felucca-Engines=…`, else `/tmp/felucca-bsp`, `/tmp/felucca-engines`, else a bare clone into `--work`):

```sh
git clone https://github.com/hugelton/Felucca /path/to/felucca
python3 tools/build_sims.py --target felucca-studio --ref v1.8.2 --repo felucca=/path/to/felucca \
    --repo felucca-bsp=/path/to/felucca-bsp --repo Felucca-Engines=/path/to/Felucca-Engines
```

→ `fm1companion/sims/felucca-studio/<version>/` (the folder is the version without its "v", `1.8.1`), identity from the
version as `tools/build.py --release` makes it (`felucca_release_identity`: v1.8.1 → FM-1_918), `tracks` 4,
`projects` 4 (SAVE's PROJECT A–D), the include paths of Felucca's own emulator build (`cflags`: `firmware/hal`,
`firmware/bsp/chip`, `firmware/bsp/boards/fm1`). The manifest names the submodules' commits (`sources`);
`tools/make_source_tarballs.py` packs them beside Felucca's own tarball. The list is
`STUDIOS["felucca-studio"]["refs"]`, the status `STUDIOS["felucca-studio"]["release_status"]`.

| Felucca version | Tag / commit | Status (manifest `release_status`, 2026-11-11) | Reports (INFO / identity) | Backup objects | Folder |
|---|---|---|---|---|---|
| 1.8.1 | `v1.8.1` f4df2964649f301ba5abd0ec6000110068672f25 | **stable** – what its web installer installs (gh-pages `firmware/felucca-1.8.1.fwsc`, README "Stable release: 1.8.1") | `FELUCCA v1.8.1` / `FM-1_918` | 13 | `felucca-studio/1.8.1` |
| 1.8.0 | `v1.8.0` 9f62297c2db627bbcb20d7ce896683a17318674c | previous stable | `FELUCCA v1.8.0` / `FM-1_918` | 13 | `felucca-studio/1.8.0` |

Both pin felucca-bsp 5942e68b89ab9ccd23f2c541d712ca38fe635f11 and Felucca-Engines d0260074ce100a47675814b7f38d61f11176c926.
`v1.8.2-beta` (only the recognition of a newer flash chip) is a beta and not bundled: a 1.8.2 FM-1 gets the 1.8.1 twin
with a warning. Felucca's versions sort with the generic `studio.version_key`.

A Felucca FM-1 answers INFO "FELUCCA v1.8.1" – no other name (`protocol.Info.firmware_name` "felucca", the catalog's key
`felucca`; `firmware.guess_firmware` "felucca" for such an INFO). The identity alone does not tell Felucca: its releases
are FM-1_9XY, which Felucca forks built the same way share (Jangada, Golana …: FM-1_910 … 913), and a plain
`tools/build.py` build is FM-1_900 – SLOOP's (and 31 other firmwares') identity. SLOOP always names itself
("FELUCCA SLOOP 2.8.5"), so the INFO decides: `backup.protocol()` asks INFO for the hint "felucca" too (before, that hint
skipped INFO), and `guess_firmware` no longer takes what this machine remembered for FM-1_900 (e.g. "sloop") over a plain
Felucca INFO. **Felucca forks** that answer INFO "FELUCCA v…" without a name of their own, or a fork chosen for the
device from the catalog (`felucca-mpe`, `felucca-salt` …: `StudioService.firmware_for` takes the prefix), get the
**Felucca twin with a warning** (`studio.felucca_fork_note`: the fork's key, or a package identity other than the
twin's; the version check of `pick_build` / `check_versions` warns too and writing back needs the expert tick for
another version). Their backup protocol and inventory are Felucca's, the device checks every object written back, and
a safety backup comes first – a fork that reports exactly Felucca's version string and identity cannot be told apart.

Felucca's backup (editor commands 65–67, `editor_backup.c` ED_BK_IDS, `web/fm1backup.js` BACKUP_IDS; file format
"felucca-backup", `firmware` = the INFO string): 0 the running music (FUN10, 3840 bytes), 1 the settings (572), 2–5 the
projects A–D, 6–7 the user preset banks (UPB1, 3080), 8 the FM6 patch bank of 1.0–1.0.2 (listed empty since 1.0.3; an
older archive's bank goes into the user presets), 9 the user presets' FM6 patches (3728), 32–34 the sample slots
USR1–USR3 (restored through SMP_BEGIN / WRITE / END). `backup.FELUCCA_IDS` / `KNOWN_ID_SETS[13]` (before, 13 objects were
an unknown inventory: the inspector warned "unknown to the web editor", named object 9 "CZ-1 bank 1", knew no firmware
for a backup of Felucca's own editor and refused to copy it), `backup.is_felucca()` (13 objects; the 12 and 11 of
1.0–1.0.2 and before FM6 only with "felucca" in the name: they are Melodee's too). The twin's tolerant load takes the
sample slots too (`FIRMWARES["felucca"]["samples"]`: the twin keeps them), so writing back after "Fetch from FM-1" keeps
the device's samples. Skiff's entry has the flag as well: before, the tolerant load left a Skiff FM-1's sample slots out of
the twin, and writing back then erased them on the device (SMP_ERASE of the empty slot;
`tests/test_studio.py::test_fetch_and_write_back_keep_the_sample_slots`).
FM1 Open (M-VAVE's open firmware, GPL-3.0 with the FM1 SDK linking exception) has no tags; a version is a commit, the
one the bundled simulator `sims/fm1-open` is built from (`SIMS["fm1-open"]["ref"]`):

```sh
git clone https://github.com/m-vave/fm1_open /path/to/fm1_open
python3 tools/build_sims.py --target fm1-open-studio --repo fm1-open=/path/to/fm1_open
```

→ `fm1companion/sims/fm1-open-studio/V14/` (the folder is `app/version.h` FM1_RELEASE_VERSION), identity from
`app/version.h` FM1_UPDATE_PRODUCT (FM-1_8000014), `tracks` 8, `projects` 16. The build has its own compile step
(`STUDIOS["fm1-open-studio"]["compile"]`, `build_studio_own`): the sources and flags of FM1 Open's own simulator build
(`tools/Build-Web.ps1` Build-Simulator, as `build_fm1_open` does: app/ without app/board/, web/, third_party/synth/,
`-DFM1_HOST_TEST -DFM1_OPEN_WEB`, C11, `-Werror`), with the glue in place of `web/engine/Simulator.c`.

| FM1 Open version | Commit | Status (2026-11-11) | Reports (INFO / identity) | Backup objects (fm1openbk.py) | Folder |
|---|---|---|---|---|---|
| V14 | 4c7496daac04a95ff10d1afdcc8f9ead2dbabc41 | **current** – `main` of m-vave/fm1_open, the source of its workbench | no INFO / `FM-1_8000014` | 22: 0 the working project, 1–16 the slots, 20–23 the DX7 library banks A–D, 32 the sample bank | `fm1-open-studio/V14` |
| (original V15 / V16) | – (closed) | the firmware most FM-1 run | no INFO / `FM-1_015` / `FM-1_016` | none: no backup protocol | the V14 twin as stand-in |

How close FM1 Open is to the original V15 (from its sources): it is **not** V15 – a new APP (`app/`, GPL) on M-VAVE's
binary engine SDK (`sdk/lib/libfm1_engine.a`), numbered V13, V14 (`FM1_RELEASE_VERSION`), package identity
FM-1_8xxxxxx; 8 tracks (DX7, 303, 808, PSG, pluck, sampler), 16 project slots, 64 steps, 8 sample pads, its own flash
format ("Firmware switching does not make project and sample formats interchangeable", its README). Shared with V15:
the **128 factory DX7 voices** in their order (`app/fm_factory.inc`, extracted from `sdk/firmware/FM-1_015.fwsc`; the
twin's library is byte-identical to the V15 package's USR voices, `tests/test_studio_fm1open.py`), the msfa-derived FM
model and the update protocol (00 59 system commands). The twin runs the **open web engine** (`web/`, as M-VAVE's
browser simulator), not the device engine; V16 in addition corrected the detune. So the studio opens the FM1 Open twin
for a V15/V16 device with a note (`studio.STAND_IN_NOTE`, `FIRMWARES["fm1-open"]["stand_in"]`) and refuses fetch and
write back there (the original firmware has no backup protocol).

FM1 Open has no editor INFO and no backup command: the device is recognised by its identity (`firmware.guess_firmware`
FM-1_8xxxxxx → "fm1-open"), the backup (`fm1openbk.py`, file format `fm1open-backup`) is made of its workbench's file
transfer commands (app/transfer.c: 0 library, 22 capabilities, 9 slot name, 8 load, 7 save, 1/2 download, 3/4/5
upload, 11/15 sample bank). A slot is read by loading it; the backup restores the working area afterwards. `backup.py`
picks it by the firmware key (`FM1OPEN_KEY`) or the file's format (`Archive.fm1open`).

### The FM1 Open glue in short

`fm1open_studio.c` includes FM1 Open's browser simulator `web/engine/Simulator.c` whole (its `sim_*` exports stay) and
adds the `st_*` ABI: the user partition (536576 bytes) at the start of a 1 MiB image (`st_flash`; on the device the SDK
places it, its address is not in the sources), USB MIDI in through `sim_midi_from` (the APP's `fm1_app_midi_received`
and `fm1_app_sysex`, so app/transfer.c answers), the SDK's identity request (00 59 17) answered with
FM1_UPDATE_PRODUCT, other update commands dropped; the APP's USB output (`fm1_midi_send_port` USB) collected for
`st_out` (Simulator.c's test capture renamed away); `sim_render` as the clock (a 128-frame block, the control work
every 10 ms). `st_transport_set` posts MIDI Start / Stop into the instrument's own input, `st_track_mute_set` toggles
MUTE (groove_mix_toggle) for the stems, `st_project_*` are the 16 slots. A blank image boots as a new device
(Simulator.c `sim_boot(1)`: the factory sample bank).
ChoralRoot FM-1's FM-1 releases are tags `vX.Y[.Z]` (the `-smk37` tags are its SMK-37 builds, not the FM-1's:
`choralroot_version` refuses them); its web installer offers the first entry of `web/releases.json`, built with
`tools/build.py --release X.Y` (the INFO text "ChoralRoot X.Y", the identity `PRODUCT` FM-1_920 for every FM-1
release):

```sh
git clone https://github.com/Quixotic7/ChoralRootFM1 /path/to/choralroot
python3 tools/build_sims.py --target choralroot-studio --ref v0.40 --repo choralroot=/path/to/choralroot
```

→ `fm1companion/sims/choralroot-studio/0.40/`, the version from the tag (`version_ref`, as CoralFlower: `--release` sets
the version string), identity read from the commit's `tools/build.py` (`PRODUCT`), `tracks` 2 (the chord part and the
bass part; parts 3 and 4 are unused), `projects` 0. The list is `STUDIOS["choralroot-studio"]["refs"]`, the status
`STUDIOS["choralroot-studio"]["release_status"]`; the build takes ChoralRoot's own emulator flags (`cflags`: gnu11 and
its single-thread `<os/lock.h>`, `tools/emu/web/compat`).

| ChoralRoot version | Tag / commit | Status (manifest `release_status`, 2026-11-11) | Reports (INFO / identity) | Backup objects | Folder |
|---|---|---|---|---|---|
| 0.39 | v0.39 / 36867e5c76fa1ecc02cba57102ee870695e1de10 | **stable** – the first entry of `web/releases.json`, what its installer offers | `ChoralRoot 0.39` / `FM-1_920` | 34 | `choralroot-studio/0.39` |
| 0.38 | v0.38 / 3cb2e8fd25d3d4292cb5c66c2623589d7a2ba8e3 | previous stable (`web/releases.json`) | `ChoralRoot 0.38` / `FM-1_920` | 34 | `choralroot-studio/0.38` |

A ChoralRoot device answers INFO "ChoralRoot 0.39" (its single-core package "ChoralRoot 0.39 1-core"; both pick the 0.39
twin); `protocol.Info.firmware_name` is "choralroot", `firmware.guess_firmware` maps it and the identity FM-1_920 to
the catalog's key "choralroot-fm-1" (`StudioService.firmware_for` takes it to the twin). Its versions sort with the
generic key (0.22-beta4 < 0.22 < 0.29.2 < 0.39). ChoralRoot speaks only the backup part of Felucca's protocol (INFO,
LIST 65, GET 66, PUT 67, RESTART 72; no editor, `cr_backup.c`) and writes `felucca-backup` files with its INFO string as
`firmware`. Its inventory (`CRB_IDS`, `backup.CHORALROOT_IDS`): from 0.22, 34 objects – 1 the settings (PER5), 8 the FM6
bank, 14..21 the CZ-1 banks A..H, 50..62 the sound pool's 13 banks (user sounds 001..128), 23 the favourites, 40..49 the
ten loop slots; LIST names every one, an empty one with size 0 (a new unit lists all 34 empty: nothing is written before
SAVE held). 0.14 .. 0.21: 1, 6 7 the user sound banks, 8, 9 the VA patches, 10 11 the FM6 patches, 12 13 the CZ-1
tones, 14..21, 22 the FM TONE patches, 40..49 (28 objects; 27 without 22, 17 without the CZ-1 objects;
`backup.CHORALROOT_ID_SETS`). No running music, no projects and no sample slots: before this twin `backup.py` read
40..62 as Melodee's sample slots (a restore would have sent the loops and sound banks with SMP_BEGIN / WRITE / END),
refused a 28-object archive ("unexpected object order": Felucca's `range(28)` has that length) and refused any archive
whose FM6 bank (its second object) was empty. `backup.is_choralroot` tells the inventory (the loop slots, nothing of
Felucca's music); a restore ends in RESTART as CoralFlower's (`backup.restart_coralflower`), and the studio power-cycles
its twin after loading a backup (`FIRMWARES["choralroot"]["restart"]`). A 0.21 archive's sound objects (6, 7, 9..13,
22) are refused by 0.22 and later: `backup.restore` stops before writing anything, the studio's tolerant load leaves
them out and names them (ChoralRoot's own installer reports them skipped).

Each `STUDIOS` entry names what differs between firmwares: `generate` (the header generators), `version_file` /
`version_re` (where the version string lives), `identity` (the release identity from the version) or `identity_src`
(read from the source tree: JIANT, SLOOP, CoralFlower, KARMA), `defines` (version and identity for the glue), `notice`,
optionally `extra` (further licence files), `compile` and `license_file` (FM1 Open: its own compile step,
`build_studio_own`), `folder` (the folder from the version string: SLOOP's "SLOOP 2.8.5" →
`2.8.5`, KARMA's "KARMA 3.1" → `3.1`), `objects` (further link inputs, KARMA's C++ built with clang++) and, for the
Python side, `tracks` and `projects`.
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

### The Felucca glue in short

`felucca_studio.c` follows Felucca's own browser emulator (`web/emu/felucca_web.c`, the X0X design: its HAL stand-ins,
`main.c`'s boot `web_power_on`, the power-on LED sweep and 1.2's idle animation, the clock driven by the audio,
`audio.c`'s real ISR) and adds what the emulator leaves out: `FELUCCA_OTA` 1 (felucca-bsp's `usb.c` SysEx frames →
`editor.c` `ed_service` → `ota_wire_send`; `chip/ota.c` answers the identity handshake, an update request is dropped;
`ota_hw.c` is the device's and stays out), the user sample slots' flash hooks (`fl_erase4k` / `fl_write` on nor[]),
USB = configured, MIDI out drained from usb.c's rings. `FELUCCA_CDC` 1 as the release (the MENU row; nothing behind it),
one core (`FELUCCA_CPU2`'s stubs as the emulator's: MENU › 2ND CORE has no second core), no AFTERTOUCH key pressure
(`keytouch.c` is not in the emulator either), no USB audio, no TRS MIDI. `st_boot` runs the splash's 430 ms (device time).
`st_transport_set(how)`: 0 stop (`ed_flash_stop`), 1 PLAY, 2 the SONG chain from the top (editor SONG op 2,
`chain_prepare`; no rows: as 1), 3 restart.
### The ChoralRoot glue in short

`choralroot_studio.c` is the CoralFlower glue on ChoralRoot's own browser emulator (CoralFlower is a fork of ChoralRoot;
`tools/emu/web/emu_web.c` with `tools/emu/emu_fw.c`, `emu_firmware.h`, `emu_hal_fw.h`), with `FELUCCA_OTA` 1 as
`firmware/src/choralroot.c` builds the device (usb.c's SysEx frame and ring; `cr_backup.c`'s replies go out through
`ota_wire_send`, `ota.c` answers the identity handshake; a short frame that usb.c also keeps in `usb.sysex` is taken so
the emulator's frame does not answer it twice) and the `st_*` ABI. `CRB_REBOOT` is a no-op: RESTART replies, the host
restarts the twin. The clock is the emulator's (tick, a UI frame every 15 ms or the input scan with GRAIN's render
slice, the audio blocks due), the audio kept at 24 bits; a flash erase holds the audio 45 ms. `st_boot` runs the splash
(`cr_ui.c` CR_SPLASH_MS, 1.5 s). Optional exports: `st_transport_set` (the looper: PLAY's event `LP_PLAY`, play / stop;
2 has no song and plays the loop) and `st_track_mute_set` (the stems: the chord and the bass part; a muted part starts no
note, voice.c `P_MUTE`, and the click is held silent in a stem). The ten loop slots are the panel's (PLAY held,
PRESETS): `st_project_*` answer "none".

## Checking

```sh
python3 -m pytest tests/test_studio.py -q                      # builds, firmware core in node, backup round trip
QT_QPA_PLATFORM=offscreen QTWEBENGINE_DISABLE_SANDBOX=1 python3 tests/ui_studio_test.py
QT_QPA_PLATFORM=offscreen QTWEBENGINE_DISABLE_SANDBOX=1 python3 tests/ui_studio_page_test.py
```

`tests/test_studio.py::test_builds_side_by_side` (Melodee), `test_skiff_builds_side_by_side`,
`test_jiant_builds_side_by_side`, `test_sloop_builds_side_by_side`, `test_coralflower_builds_side_by_side`,
`test_karma_builds_side_by_side`, `test_felucca_builds_side_by_side` and `test_choralroot_builds_side_by_side` expect the bundled versions – add the new version there. `tests/studio_twin_skiff.mjs`,
`tests/studio_twin_jiant.mjs`, `tests/studio_twin_sloop.mjs` and `tests/studio_twin_karma.mjs` are the Skiff, JIANT,
SLOOP and KARMA core checks in node (editor, identity, backup GET/PUT round trip – SLOOP's and KARMA's with SLOOP's
commands 34–36 –, flash persistence, PLAY, audio, fork, USB MIDI out, stop); `tests/studio_twin_felucca.mjs` is Felucca's (INFO, identity FM-1_918, the 13-object round trip with a sample in USR1 through
SMP_*, flash persistence, PLAY, audio, fork, transport, the editor's SONG stop); `tests/studio_twin_fm1open.mjs` the FM1
Open one (identity, its file protocol, slots, library banks, samples, PLAY, fork, transport, mutes; Python side
`tests/test_studio_fm1open.py`), `tests/studio_twin_coralflower.mjs` the
CoralFlower one (INFO, identity, a take recorded on the panel, the 26-object GET/PUT round trip with RESTART, the power
cycle, PLAY, audio, fork, MIDI out, transport, mutes).
The packages (.deb, .run) include everything under `fm1companion/` (`tools/build_common.app_files`);
`tests/test_packaging.py` checks that the host files and every `twin.wasm` end up in the package.

## Other firmwares

A further firmware follows the same pattern: its own entry in `STUDIOS`, its own glue layer
`tools/studio/<firmware>_studio.c` with the same `st_*` ABI, and an entry in `studio.FIRMWARES` (then
`StudioService.firmware_for()` knows it; `"stand_in"` names device firmwares without a twin of their own that open it). The host
(JS) and the rest of the Python side stay the same. A firmware with SLOOP's backup protocol and format (`sloopbk.py`)
says so in its `FIRMWARES` entry (`"backup": "sloop"`, SLOOP and KARMA): the twin's tolerant load then goes through
`sloopbk.restore_tolerant`. See `docs/ROADMAP.md`.

### Firmwares checked without a twin (2026-11-11)

* **ORBIT** (dspaudio/orbit, a SLOOP fork, GPL-3.0; stable 1.0.0 `orbit.fwsc`, the Bluetooth build 1.0.1B
  `orbit-ble.fwsc`, both INFO "FELUCCA ORBIT …" / FM-1_900): technically SLOOP's twin would carry over
  (`tests/editor_host.c`, `hal/fm1_flash_map.h`), but every ORBIT build compiles its CLUSTER engine, a C translation of
  Teenage Engineering's OP-1 #246 oscillator with TE's own tables (`assets/op1-cluster/`), which ORBIT's LICENSING.md
  says remains TE's material, not relicensed – no twin is bundled. ORBIT speaks SLOOP's backup protocol (34–36,
  `ED_BK_IDS` 0..10, 32..35, "sloop-backup" files with product "ORBIT"): `backup.SLOOP_PROTOCOL` includes it, an ORBIT
  backup is refused on SLOOP / KARMA and a SLOOP backup on ORBIT before anything is written, as ORBIT's editor does;
  `studio.backup_firmware` names it "orbit" (the studio page says there is no twin for it).
* **Hortator** (DeadActive/hortator, releases are the tags `drum-vX.Y.Z`, "Hortator X.Y.Z" with
  `hortator-X.Y.Z.fwsc`; the `frozen-base-N` tags are no releases): its own editor protocol (header 7D 48 54, sequence
  numbers, backup by kinds and parts), none of Felucca's commands, so the Suite's Studio (which rides on 7D 46 4C) could
  neither load nor read it without a new protocol layer. The device layer asks its own INFO (`ui/device.py`
  `hortator_info`, from 1.0.0) to name it instead of asking the user (its identity is Felucca's FM-1_900).
* **GR!MM** (GRiMMBIENT/grimm, v2.0.2): its own protocol (7D 47 52, commands 20..25) and a blocking main loop its
  browser build runs only through Asyncify: no twin. Its identity FM-1_966 (every release) names it
  (`firmware.guess_firmware` → the catalog's "gr-mm").

Source tarballs for a release: `python3 tools/make_source_tarballs.py --repo melodee=… --repo skiff=… --repo JIANT=…
--repo sloop-fm1=… --repo CoralFlower=… --repo karma-fm1=… --repo Felucca=… --repo felucca-bsp=… --repo Felucca-Engines=…`
(a manifest's `sources`, Felucca's submodules, are packed too; without `--repo` it looks in `/tmp/<name>`, then in lower
case: `/tmp/jiant`, `/tmp/coralflower`, then the manifests' firmware key: `/tmp/sloop` for `sloop-fm1`, `/tmp/karma`
for `karma-fm1`; FM1 Open's is `/tmp/fm1_open`, shared with the simulator) packs every commit named in a manifest.
