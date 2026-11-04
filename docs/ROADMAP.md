# Roadmap: Studio twin

Guiding idea: the FM-1 is the instrument (sketching, trying things out), the suite is the studio. The suite has a
digital twin of the FM-1: the same firmware, with the complete device state from the backup, identical
sound through the computer's speakers, further editable in the suite, exportable as WAV and writable back to the
FM-1. The suite does not replace a DAW (Logic, Audacity), but it can do everything that belongs to the FM-1.

| Step | Firmware | Status |
|---|---|---|
| 1 | **Melodee** (priority 1), matching the version on the device (1.6.1, 1.6.0, 1.6.0-rc.7 and rc.8 bundled) | **done in 2.2.0**, 1.6.0 since 2.3.0, 1.6.1 since 2.3.2 (page "Studio", virtual device, playback target "Im Studio" – in the Studio) |
| 2 | **SLOOP** (latest stable, currently 2.8.4) | only once Melodee runs well |
| 3 | **Skiff** (latest stable, currently 2.1.1) | only once Melodee runs well |
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
