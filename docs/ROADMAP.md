# Roadmap: Studio twin

Guiding idea: the FM-1 is the instrument (sketching, trying things out), the suite is the studio. The suite has a
digital twin of the FM-1: the same firmware, with the complete device state from the backup, identical
sound through the computer's speakers, further editable in the suite, exportable as WAV and writable back to the
FM-1. The suite does not replace a DAW (Logic, Audacity), but it can do everything that belongs to the FM-1.

| Step | Firmware | Status |
|---|---|---|
| 1 | **Melodee** (priority 1), matching the version on the device (1.6.1, 1.6.0, 1.6.0-rc.7 and rc.8 bundled) | **done in 2.2.0**, 1.6.0 since 2.3.0, 1.6.1 since 2.3.2 (page "Studio", virtual device, playback target "Im Studio" – in the Studio) |
| 2 | **SLOOP**, the stable of its installer (2.8.5), plus the release before (2.8.4) | **done** (branch `studio-sloop`; same page, virtual device, backup ↔ twin in SLOOP's own format, write back) |
| 3 | **Skiff**, matching the version on the device (2.2 and 2.1.1 bundled) | **done** (branch `studio-skiff`; same page, virtual device, backup ↔ twin, write back) |
| 3b | **JIANT FM**, the public stable of its installer (V10.6z2), plus the earlier stable V10.60 and beta V10.61 | **done** (branch `studio-jiant`; same page, virtual device, backup ↔ twin, write back) |
| 3c | **CoralFlower**, the stable of its installer (0.7.1), plus 0.7 | **done** (branch `studio-coralflower`; same page, virtual device, backup ↔ twin, write back) |
| 3d | **KARMA**, the version its installer serves (3.1), plus 3.0.5 | **done** (branch `studio-karma`; same page, virtual device, backup ↔ twin through SLOOP's protocol, write back) |
| 4 | **Original firmware:** for now via **FM1 Open** (M-VAVE's open V14 code), with a note that V15/V16 differ | **done** (branch `w-fm1open`; the FM1 Open twin for FM1 Open devices – backup ↔ twin over its own file protocol, write back – and as the stand-in for V15/V16 with a note; V15's factory voice restore) |
| – | **Original V16** | **paused, not dropped** |
| – | **Original V15: own voices into a bank A–D** (the same commands 33/34 as M-VAVE's factory restore, other data; the bank's current voices cannot be read back first) | **needs a hardware test** – not offered in the app; only the factory restore is |

## Status 2.2.0 (Melodee)

Done: page **Studio** (Music group, first entry) – fetch from the active FM-1 (the backup also lands in the list),
open from a backup, automatically load the last Studio state per device profile; version banner (twin vs.
device/backup); playable FM-1 (screen, LEDs, keys, knobs; mouse, computer keyboard, MIDI keyboard, keys of the real
FM-1); load/play the twin's projects, song, space bar; WAV export (24 bit / 32 bit float, song to the end,
seconds or loops, separate tracks); Studio state saved automatically, snapshots with
undo/redo; writing back with confirmation, safety backup, settings only on request,
version lock (expert checkbox). The twin can be selected as a **virtual device** (projects, composing, presets,
backups, editor work through `StudioTransport`); it is never in `ctx.ports`, `ctx.is_virtual` marks it,
automatic backups and the firmware check skip it. Playback bar: "in the Studio (identical sound)"
instead of the approximation on the computer (which remains only if there is no twin). Building new
versions: `docs/studio-build.md`.

Open (Melodee): output selection for the Studio is "best effort" (pactl move-sink-input of the QtWebEngine audio);
measure tempo/load on the Pi 5; a CPU load model like on the device (overload shedding) does not exist in the twin.

## Status 2.3.0 (Melodee 1.6.0)

Done: twin for the final **Melodee 1.6.0** (tag v1.6.0, commit 8f0e251; glue layer unchanged), rc.7
and rc.8 remain for devices on those versions; `pick_build` takes exactly 1.6.0 for 1.6.0, the version warning
names the new levels between rc.x and 1.6.0 (CZ-1 −6 dB, limiter −2 dBFS). Simulator on 1.6.0. The project view reads
punch effects, song block and pattern presets (display only; they sound exact only in the Studio) – checked against a
1.6.0 project from Melodee's own code (`tools/melodee_fixtures/fixturegen16.c`, `tests/fixtures/musik16`).
Safety when switching to/from 1.6: `fm1companion/melodee16.py` (installation plan, backup lock).

Open: `fixturegen16.c` stores the song only as running music (object 0) – `project_save_as` with song presets
breaks the resource table in the host test setup (not investigated, host only).

## Status 2.3.2 (Melodee 1.6.1)

Done: twin **Melodee 1.6.1** (tag v1.6.1, commit 165d177; glue layer unchanged, identity FM-1_91061) next to
1.6.0, rc.7 and rc.8; `pick_build` takes exactly 1.6.1 for 1.6.1, and still 1.6.0 for 1.6.0. Simulator on 1.6.1.
Tool pin v1.6.1 (`KNOWN_TOOL_COMMITS`). 1.6.0 → 1.6.1 is a normal update in the installation plan.
Clock/sync of 1.6.1 (checked against `midi_clock.c`, `editor.c`, `project.c`): "Start the song
on the FM-1 itself" from a section (BANK_SONG op 2 with row → `chain_prepare_from`; with CLK INT the FM-1
then sends Song Position + Continue), the MIDI page's clock generator can start from a bar (Song Position +
Continue, `clock.song_position`) and warns when the FM-1 sends clock itself with CLK INT. Help page "Melodee 1.6.0"
extended with 1.6.1 (keep CLK when loading a project, the backup restores CLK).

Open: the twin does not output the FM-1's clock (its own real-time ring in `usb.c`) – that would need a
new glue layer and thus new builds of all versions. Clock output untested on real hardware (also only checked
automatically in Melodee itself).

## Status Skiff (2.2, 2.1.1)

Done: twin **Skiff 2.2** (tag v2.2, commit 76b5f9c, identity FM-1_722) and **2.1.1** (tag v2.1.1, identity FM-1_721),
glue layer `tools/studio/skiff_studio.c` – Skiff's unmodified sources included as its own browser emulator
(`web/emu/felucca_web.c`) and host tests (`tests/editor_test.c`, `backup_test.c`) do, plus the editor SysEx path,
`ota.c`'s identity handshake and USB MIDI out (2.2: timed with the audio, `usb.c` `mo_take`). Same `st_*` ABI; one
optional export `st_transport_set` (Skiff's editor TRANSPORT is command 76, Melodee's stop is BANK_SONG 74 op 3:
`twin-host.js` uses the export when a twin has it, Melodee's builds are unchanged). `build_sims.py` has per-studio
hooks (header generators, version file/regex, release identity, defines, NOTICE, extra licence files).
Python: `studio.FIRMWARES` (name, INFO name, tracks, project slots), `backup_firmware()` (companion block, firmware
string or Skiff's 29-object inventory), `StudioService.firmware_for()` knows Skiff; the page starts the twin of the
device's or the backup's firmware, lists projects A–D, renders six stems, refuses writing a twin onto a device with a
different firmware. Backup layer: Skiff 2.x's inventory (29 objects: 0–9, pattern bank 16–31, samples 32–34) was
rejected as "unexpected object order" (same length as Melodee 1.5's 0–28) – `backup.ALT_ID_SETS` / `known_ids()`;
the inspector names the pattern bank chunks and the FM6 patches of the user presets.

Open (Skiff): the project view and `projectfmt` do not read Skiff's SKF4 projects (the export dialog therefore offers no
"passes of the pattern" for Skiff, and the project name comes from the slot only) – a separate parser; the Skiff twin
has one core (MENU › CPU 2 CORES never starts core 1), no USB audio, no TRS MIDI, no 320 MHz switch; "song to the end"
ends only with SONG ONCE (Skiff loops otherwise); the autosave (`autosave_poll`) writes the flash on its own after the
music changes, so the Studio's change detection also sees those writes. Checked only against the twin itself, never a
real Skiff FM-1 (docs/UNTESTED.md).

## Status JIANT FM (V10.6z2, V10.60, V10.61)

Done: twin **JIANT FM V10.6z2** (commit cac23c6, the public stable of JIANT's installer: `web/STABLE`,
`web/PUBLIC_RELEASE.json`; 6 songs, 35 backup objects), **V10.60** (commit 43b8ea8, the stable before, named in
`web/EDITIONS.json`) and **V10.61** (commit 6c48e2a, the beta then), glue layer `tools/studio/jiant_studio.c` – JIANT's unmodified
sources included as its own browser emulator (`web/emu/felucca_web.c`) and host tests (`tests/editor_test.c`,
`backup_test.c`) do, plus the editor SysEx path, `ota.c`'s identity handshake and USB MIDI out (held to its TIMER4
tick, `usb.c` `midi_out_due`). Same `st_*` ABI with `st_transport_set` (stop as `editor.c` `ed_flash_stop`, the song
as `song_main.c` `chain_prepare`). JIANT has no tags: `build_sims.py` builds commits; the folder and the manifest carry
the version the commit reports (`felucca.c` FELUCCA_VERSION "V10.60" -> `sims/jiant-studio/10.60/`), the identity is
JIANT's `tools/build.py` PRODUCT (FM-1_909: its installer builds without `--release`).
Python: `studio.FIRMWARES["jiant"]` ("JIANT FM", INFO "JIANT", 6 tracks, 4 slots = the current song's sections
A–D); `backup_firmware()` knows JIANT by the companion block, the firmware string ("JIANT V10.60", as JIANT's editor
writes it) or its 43-object (8 songs) or 35-object (6 songs, V10.6p on) inventory; `StudioService.firmware_for()` takes the device layer's "jiant" (INFO) and the
catalog's "jiant-fm"; `firmware.guess_firmware()` maps INFO "JIANT …" and identity FM-1_909 to "jiant-fm". Backup
layer: JIANT's inventories (0–10, the song index 10, every song's sections 40–71 or 40–63) are `KNOWN_ID_SETS[43]` and
`[35]`; 40–63 are
sections, not sample slots (`is_sample` / `is_project` take `jiant`), and song 7/8's sections 64–71 no longer trigger
the Melodee 1.6 refusal (`check_16`). The inspector names the songs' sections and the song index.

Open (JIANT): the project view and `projectfmt` do not read JIANT's sections (the export dialog offers no "passes of
the pattern", the project name comes from the slot), and the Projects page still lists JIANT's slots with Melodee's
numbering – a separate parser; T5 and T6 are asleep with JIANT's default MENU › TRACKS 4 (their stems are silent);
one core, no Bluetooth / PROX, no USB host, no USB audio (INPUT hears silence), no TRS MIDI; versions between the
builds (V10.6c … V10.6z10) get the next older twin with a warning (`studio.jiant_version_key`: JIANT's release order);
the 2026-11-08 beta "V10.7" cannot be told from the V10.7 of 2026-10-31 by its string and gets V10.60; an 8-song
backup restored onto V10.6p or later loses songs 7 and 8 (the studio's tolerant load says so, `backup.restore` refuses
before writing; JIANT's own editor skips them); the catalog entry
"jiant-fm" still says protocol "none" (the device answers the editor protocol all the same). Checked only against the
twin itself, never a real JIANT FM-1 (docs/UNTESTED.md).

## Status SLOOP (2.8.5, 2.8.4)

Done: twin **SLOOP 2.8.5** (tag v2.8.5, commit bac9c05, the stable SLOOP's web installer ships:
`docs/firmware/sloop-2.8.5.fwsc`) and **2.8.4** (tag v2.8.4, commit 91f3018, the release before), glue layer
`tools/studio/sloop_studio.c` – SLOOP's unmodified sources included as its own host harness of the whole firmware does
(`tests/editor_host.c`, `tests/hostsim.c`, `boot_sound_test.c`: SLOOP has no browser emulator of the whole firmware),
plus `main.c`'s boot, `audio.c`'s ISR, `ota.c`'s identity handshake, USB MIDI out, LEDs and screen. Same `st_*` ABI with
`st_transport_set` (stop as seq.c's STOP, the song as the SAVE layer's SONG MODE + PLAY). The folder is the number of the
version (`ui.c` FELUCCA_VERSION "SLOOP 2.8.5" -> `sims/sloop-studio/2.8.5/`), the identity SLOOP's `tools/build.py`
PRODUCT (FM-1_900, Felucca's too: INFO tells SLOOP).
Python: `studio.FIRMWARES["sloop"]` ("SLOOP", INFO "FELUCCA SLOOP", 4 tracks, 4 slots = the sections A–D; the build's
version "2.8.5" without the "SLOOP " of its version string); `backup_firmware()` knows SLOOP by its file format
("sloop-backup", also the web editor's own files); `StudioService.firmware_for()` takes the device layer's "sloop"
(INFO "FELUCCA SLOOP …"); the twin loads and writes through SLOOP's own protocol (`sloopbk.py`, commands 34–36, the
web editor's order); `studio.restore_tolerant` has a SLOOP path (each object on its own, a project format newer than the
twin reads or a refused object left out and named; the default MIDI LEARN object as the editor adds it). Backup layer:
SLOOP's inventory (0–11, 32–35) checked against `editor.c` `ED_BK_IDS` / `ed_bk_obj` and the protocol document – it
matched (no fix needed); `Studio.write_to_device` no longer passes the twin's firmware as the protocol hint (another
firmware's FM-1 now refuses at once instead of timing out). The demo's SLOOP device reports 2.8.5 (its twin).

Open (SLOOP): the project view and `projectfmt` do not read SLOOP's sections (FUN6; the export dialog offers no "passes
of the pattern", the name comes from the slot) – a separate parser; one core (MENU › 2ND CORE), no USB audio, no TRS
MIDI, no USB rescue; the whole song needs a playable song order (an empty section in it: the loop plays to the export's
limit); versions before 2.8.4 get the 2.8.4 twin with a warning. Checked only against the twin itself and the demo
device, never a real SLOOP FM-1 (docs/UNTESTED.md).

## Status CoralFlower (0.7.1, 0.7)

Done: twin **CoralFlower 0.7.1** (tag v0.7.1, commit 46790b9: the first entry of `web/releases.json`, what its web
installer offers) and **0.7** (tag v0.7, commit 65c6b76, the release before), glue layer
`tools/studio/coralflower_studio.c` – CoralFlower's own browser emulator (`tools/emu/web/emu_web.c`, the firmware through
`tools/emu/emu_firmware.h`, as `tests/cf_backup_test.c` builds it) with the USB SysEx path added (`cf_backup.c`'s
backup protocol, `ota.c`'s identity handshake). INFO "CoralFlower 0.7.1", identity FM-1_930 (constant). Optional exports
`st_transport_set` (PLAY's events) and `st_track_mute_set` (stems: no editor TRACK_MIX in CoralFlower).
Python: `studio.FIRMWARES["coralflower"]` ("CoralFlower", 4 parts, no project slots, settings object 30, restart after a
restore); `backup_firmware()` knows it by the companion block, the INFO string or the inventory; the device layer by INFO
and identity. Backup layer: CoralFlower's inventory (26 objects; 8, 14, 17 in older releases) is known
(`backup.is_coralflower`, `KNOWN_ID_SETS[26]`, an alternative for 17); bugs fixed on the way: a fresh CoralFlower
backup was refused (its first objects, the sound banks, are empty: "does not contain the running music or the
settings"), 50–63 would have been restored as sample slots, the pattern sounds 64–67 counted as Melodee projects (the
restore's "larger recording memory" refusal, the Melodee 1.6 refusal, the Projects page's slots, health, autobackup),
8 (CoralFlower's FM6 bank) was left out of partial restores as Melodee's retired bank; a restore now ends with
RESTART, as CoralFlower's installer does, so the device reads what was restored.

Open (CoralFlower): no project view or `projectfmt` for its patterns (the export dialog offers no "passes of the
pattern"); music is made on the panel only (no editor protocol: the editor pages and Compose do not work on it); the
catalog entry "coralflower" still says protocol "none"; one core, no USB audio, no TRS MIDI; checked only against the
twin itself, never a real CoralFlower FM-1 (docs/UNTESTED.md).

## Status KARMA (3.1, 3.0.5)

Done: twin **KARMA 3.1** (tag `v3.1`, commit 718de45, what KARMA's web installer serves) and **3.0.5** (tag `v3.0.5`,
commit 23304e9, the release before), glue layer `tools/studio/karma_studio.c` – KARMA's unmodified sources in
`felucca.c`'s order on its own host-test base, Plaits / Elements (`firmware/mi`, C++) compiled with WASI SDK clang++
and linked in. Identity FM-1_926 (`tools/build.py` PRODUCT, every build), INFO "FELUCCA KARMA 3.1". Python:
`studio.FIRMWARES["karma"]` ("KARMA", INFO "FELUCCA", 4 tracks, 4 slots = the song's parts A–D, backups in SLOOP's
protocol); `protocol.Info.firmware_name` "karma", `firmware.guess_firmware()` (INFO, FM-1_926), `backup.protocol()`
(`SLOOP_PROTOCOL`: commands 34–36 for SLOOP and KARMA – before, a KARMA FM-1 was taken for plain Felucca and its
backup asked with Melodee's commands 65–67, which KARMA does not answer), `backup_firmware()` (the `sloop-backup`
file's `firmware` "FELUCCA KARMA …"), `backup_inspect` / `autobackup` firmware keys, `sloopbk.dev_format` (KARMA's
projects are format 7: SLOOP's check refused them as "newer"), `sloopbk.with_defaults` (SLOOP 2.5's DSU1 kits left out
on KARMA, as KARMA's editor does), `sloopbk.restore_tolerant` (the studio's tolerant load in SLOOP's format – also the SLOOP twin's since the
merge: `studio.restore_tolerant` takes it for every twin whose `FIRMWARES` entry says `"backup": "sloop"`), a KARMA
backup refused onto SLOOP before anything is written.

Open (KARMA): the project view and `projectfmt` do not read KARMA's FUN7 projects; the Projects page lists KARMA's
parts by number, not as A–D (it checks for "sloop" only), and `projects.family` does not know KARMA (its FUN7 projects
read as SLOOP's); KARMA's three saved songs (SAVE on the SONG screen) are in
no backup (KARMA's own neither); no USB audio / STEMS, no TRS MIDI, one core in the twin; KARMA is not in the bundled
catalog, so installing it from the app needs a built-in firmware entry (its packages are on its GitHub Pages,
`docs/firmware/karma-<version>.fwsc`, checksum in `docs/webapp/installer/index.html`), not only the twin. Checked only
against the twin itself, never a real KARMA FM-1 or a backup of KARMA's own web editor (docs/UNTESTED.md).

## Second route: fm1-emu (processor emulation)

[joakimeriksson/fm1-emu](https://github.com/joakimeriksson/fm1-emu) (MIT) emulates the FM-1 at processor level
and runs **unmodified .fwsc packages** – any firmware, any version, according to the project also **original
V13/V15/V16**. It stores the flash as sector files (`.fm1save`), emulates USB-MIDI for firmwares that poll USB
(Felucca family: Melodee, SLOOP …, so also the suite's editor protocol) and can record USB audio.
Drawback: slower (Melodee in the browser ~2.5× real time on a laptop; on the Pi 5 probably around real time
or below – to be measured). WAV export works regardless of the tempo.

Plan: the Studio twin gets a replaceable engine. First engine: Melodee compiled from source
(fast, real time on the Pi). Second engine: fm1-emu for all other firmwares/versions and as a fallback when there
is no matching source build. More finds in the directory
[aroum/fm1-custom-fw](https://github.com/aroum/fm1-custom-fw) (section "Emulators"): Quixotic7/FM1VST
(Felucca family as a DAW plugin from source), weavejam/Felucca (renders Felucca projects offline as mix +
4 stems), hdavid/fm1-emulator (desktop fork with USB-MIDI).

## V16 (paused)

V16 support is explicitly wanted. Blocker: the V16 source code is not open; M-VAVE has only published V14 as
`m-vave/fm1_open`. Routes, once there is time again:

- With every new state of `m-vave/fm1_open`, check whether V15/V16 have been added (then build like Melodee).
- Ask M-VAVE for the V16 code (forum: forum.m-vave.com).
- **New and most promising:** fm1-emu (see above) already runs V16. Open: M-VAVE's firmware drives
  USB via interrupts, which fm1-emu does not emulate yet – so a backup via USB does not work yet; sound,
  operation and flash do. Measure the tempo on the Pi.
