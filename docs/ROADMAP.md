# Roadmap: Studio twin

Guiding idea: the FM-1 is the instrument (sketching, trying things out), the suite is the studio. The suite has a
digital twin of the FM-1: the same firmware, with the complete device state from the backup, identical
sound through the computer's speakers, further editable in the suite, exportable as WAV and writable back to the
FM-1. The suite does not replace a DAW (Logic, Audacity), but it can do everything that belongs to the FM-1.

| Step | Firmware | Status |
|---|---|---|
| 1 | **Melodee** (priority 1), matching the version on the device (1.6.1, 1.6.0, 1.6.0-rc.7 and rc.8 bundled) | **done in 2.2.0**, 1.6.0 since 2.3.0, 1.6.1 since 2.3.2 (page "Studio", virtual device, playback target "Im Studio" – in the Studio) |
| 2 | **SLOOP** (latest stable, currently 2.8.4) | only once Melodee runs well |
| 3 | **Skiff**, matching the version on the device (2.2 and 2.1.1 bundled) | **done** (branch `studio-skiff`; same page, virtual device, backup ↔ twin, write back) |
| 3b | **JIANT FM**, the stable version of its installer (V10.60) and the beta V10.61 | **done** (branch `studio-jiant`; same page, virtual device, backup ↔ twin, write back) |
| 4 | **Original firmware:** for now via **FM1 Open** (M-VAVE's open V14 code), with a note that V16 may differ | after that |
| – | **Original V16** | **paused, not dropped** |

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

## Status JIANT FM (V10.60, V10.61)

Done: twin **JIANT FM V10.60** (commit 43b8ea8, the stable version JIANT's `web/EDITIONS.json` names for its official
installer) and **V10.61** (commit 6c48e2a, its beta), glue layer `tools/studio/jiant_studio.c` – JIANT's unmodified
sources included as its own browser emulator (`web/emu/felucca_web.c`) and host tests (`tests/editor_test.c`,
`backup_test.c`) do, plus the editor SysEx path, `ota.c`'s identity handshake and USB MIDI out (held to its TIMER4
tick, `usb.c` `midi_out_due`). Same `st_*` ABI with `st_transport_set` (stop as `editor.c` `ed_flash_stop`, the song
as `song_main.c` `chain_prepare`). JIANT has no tags: `build_sims.py` builds commits; the folder and the manifest carry
the version the commit reports (`felucca.c` FELUCCA_VERSION "V10.60" -> `sims/jiant-studio/10.60/`), the identity is
JIANT's `tools/build.py` PRODUCT (FM-1_909: its installer builds without `--release`).
Python: `studio.FIRMWARES["jiant"]` ("JIANT FM", INFO "JIANT", 6 tracks, 4 slots = the current song's sections
A–D); `backup_firmware()` knows JIANT by the companion block, the firmware string ("JIANT V10.60", as JIANT's editor
writes it) or the 43-object inventory; `StudioService.firmware_for()` takes the device layer's "jiant" (INFO) and the
catalog's "jiant-fm"; `firmware.guess_firmware()` maps INFO "JIANT …" and identity FM-1_909 to "jiant-fm". Backup
layer: JIANT's inventory (0–10, the song index 10, every song's sections 40–71) is `KNOWN_ID_SETS[43]`; 40–63 are
sections, not sample slots (`is_sample` / `is_project` take `jiant`), and song 7/8's sections 64–71 no longer trigger
the Melodee 1.6 refusal (`check_16`). The inspector names the songs' sections and the song index.

Open (JIANT): the project view and `projectfmt` do not read JIANT's sections (the export dialog offers no "passes of
the pattern", the project name comes from the slot), and the Projects page still lists JIANT's slots with Melodee's
numbering – a separate parser; T5 and T6 are asleep with JIANT's default MENU › TRACKS 4 (their stems are silent);
one core, no Bluetooth / PROX, no USB host, no USB audio (INPUT hears silence), no TRS MIDI; JIANT's later versions
(V10.6c … V10.6z10, V10.7; the private-preview line's `web/PUBLIC_RELEASE.json` names V10.6z2) have no twin – their
letter suffixes sort before V10.60 in `studio.version_key`, so the studio warns and runs V10.60; the catalog entry
"jiant-fm" still says protocol "none" (the device answers the editor protocol all the same). Checked only against the
twin itself, never a real JIANT FM-1 (docs/UNTESTED.md).

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
