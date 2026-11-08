# What is tested and what is not – an honest list (as of 2.3.1)

The suite was mostly built and tested in a development environment **without an FM-1**: against simulated
FM-1 (Melodee, SLOOP, generic), against the test devices from the Melodee and SLOOP editors, against self-built
loader simulations, against the Unbricker's mock and with firmware source code compiled for this purpose. These
tests show that the app follows the documented protocols – **not** that every device behaves exactly as
documented.

**If you try a function on real hardware, please write a test report** (issue template
"Hardware test report"). Only with a backup and at your own risk.

UI names in this list are the English labels of the app (the German UI uses the translations from
`fm1companion/locale/de/`).

## Used on real hardware (as of 2.3.1)

So far on **one** FM-1 with Melodee, on a Raspberry Pi 5:

| Function | Status |
|---|---|
| Connecting, detecting Melodee, editor protocol | used, works |
| **Firmware update Melodee 1.6.0-rc.8 → 1.6.0 through the suite** (Firmware+ › Install) | done once, worked |
| Full backup (read-only) | in use |
| MIDI (monitor, keyboard, connections) | basic functions in use |
| Studio (fetch backup, play on screen) | basic functions in use |
| System check | detects patterns taken from real Pi 5 logs (USB controller crash at the FM-1's audio start, fluidsynth.service occupies the HDMI sound card) |

That is **one** device, **one** firmware family and **one** update path. Everything else in this list counts as
untested, even if it runs cleanly in the simulation.

## Not tested on real hardware

### Flashing and rescue (highest risk)

| Function | Status |
|---|---|
| Installing a different firmware (switch Melodee ↔ SLOOP ↔ Skiff …) | simulation only |
| V15 intermediate step when switching firmware | simulation only |
| Back to the original (V15, V16) | simulation only |
| Way back from V16 (via V15) | simulation only; whether a real V16 accepts a step back to V15 is unconfirmed |
| Resuming after a power cut / aborted installation, CHECK HEAD detection | simulation only |
| Finishing an interrupted installation (update mode, rescue level 1) | simulation only |
| Rewriting font & factory sounds (Melodee's resource tool) | only against the demo and source code; with 1.6.0 the update loader reports the resources as complete by itself |
| Installing your own `.fwsc` file | simulation only |
| Pinning the target device via the USB path after the restart, two FM-1 at the same time | simulation only |
| **Rescue level 2** (ROM boot mode: write back V15, repair settings, full backup) | only with the Unbricker's mock; pkexec, kernel module `sg`, jl-uboot-tool and handing the files over to the user never on a device. The Unbricker itself has been used on real devices by its community, its embedding in the app has not |
| **"Is my FM-1 clean?"** | quick check (command 82) only against the demo and the Melodee source code; dump check only with model dumps built from real packages, never with a real 1 MiB dump; reading in ROM boot mode only with the mock |
| **Clean up leftovers** (erase V16 leftovers in 0x93000–0xA8FFF sector by sector, expert mode, `flash/restzone.py`) | only with the Unbricker's mock. That the WL82 loader really limits "erase sector" to 4 KiB is an assumption – otherwise the block check after each sector stops |
| Rescue level 3 (Transporter with XIAO RP2040) | instructions only; the Pico-instead-of-XIAO variant is unverified |
| V16 specifics (first-start formatting, boot head with the original loader) | only in the emulator or open; test plan: help page "V16 – note" and docs/flash-protocol.md §17 |

### Studio

| Function | Status |
|---|---|
| **Writing back to the FM-1** (Studio › Write back to the FM-1) | simulation only (safety backup first, version check) |
| Sound fidelity of the twin compared with the device | firmware code identical, timing and overload behaviour not |
| **Skiff twin** (2.2, 2.1.1: fetch from a Skiff FM-1, open a Skiff backup, write back) | only against the twin itself and the suite's own code (simulation); never against a real Skiff FM-1 or a backup made by a real device or Skiff's web editor |
| Skiff 2.x backups in the backup layer (29 objects with the pattern bank) | only with backups of the Skiff twin |
| **JIANT FM twin** (V10.60, V10.61: fetch from a JIANT FM-1, open a JIANT backup, write back) | only against the twin itself and the suite's own code (simulation); never against a real JIANT FM-1 or a backup made by a real device or JIANT's web editor |
| JIANT backups in the backup layer (43 objects: 0–10, the songs' sections 40–71) | only with backups of the JIANT twin; the device detection by INFO "JIANT …" and identity FM-1_909 only in tests |

### Backups, presets, projects, samples
- Restoring a backup to the device (full and partial).
- Real SLOOP and Skiff: backup (commands 34–36), sample upload, "stop song" behaviour.
- Writing, renaming, deleting a preset and loading it onto a track on real Melodee; FM6_PUT and DX7 SysEx to the
  original firmware.
- Writing a pattern/song (Compose, From MIDI File) on a real device.
- FM1 Open sample protocol: reconstructed from the source code, only tested against our own simulator.

### Audio, desktop, installation
- Recording via PipeWire/ALSA from the FM-1, clock-accurate recording with a real MIDI clock.
- FluidSynth sound module: command line and router rules only tested against a dummy.
- Plasma 5/6, Wayland, Breeze: mostly offscreen rendering.
- Graphical installer (.run) only in unattended mode; pkexec/kdialog/zenity paths not.
- `.deb`: built and installed with `dpkg --root` into a test directory.

### Network
- Catalog enrichment via the GitHub REST API and release lists run in the tests against recorded
  responses; online emulators and editors only against a local test page.

## Recommended order for a first test of your own

1. Plug in the FM-1 → is the firmware detected correctly?
2. **Back up now** (read-only) → open it in the backup browser: do presets and projects look plausible?
3. Open the editor, read presets (read-only).
4. Only then writing actions, start small: one preset into an empty slot (check undo).
5. Firmware: first an update **within the same** firmware family (e.g. a newer Melodee version).
6. Firmware switch, back to the original and anything in ROM boot mode only after 1–5 went cleanly – ideally
   with a device whose loss you can live with, and with the rescue tools at hand.

## Known gaps (deliberately not built)
- The project view only understands Melodee projects (SLOOP/Skiff only detected).
- On Melodee there are no sample slots (removed by the developer) – kits can only be built, not uploaded.
- MIDI export does not compute ARP, strum, chance and motion.
- Clean up leftovers only erases V16 leftovers in 0x93000–0xA8FFF if a known firmware with V15 layout is in the
  app area; other foreign leftovers are only shown by the dump check.
