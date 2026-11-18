# What is tested and what is not – an honest list (as of 2.6.1)

The suite was mostly built and tested in a development environment **without an FM-1**: against simulated
FM-1 (Melodee, SLOOP, generic), against the test devices from the Melodee and SLOOP editors, against self-built
loader simulations, against the Unbricker's mock and with firmware source code compiled for this purpose. These
tests show that the app follows the documented protocols – **not** that every device behaves exactly as
documented.

**If you try a function on real hardware, please write a test report** (issue template
"Hardware test report"). Only with a backup and at your own risk.

UI names in this list are the English labels of the app (the German UI uses the translations from
`fm1companion/locale/de/`).

## Used on real hardware (as of 2.6.1)

So far on **two** FM-1 of one owner – an earlier unit (flash chip 0x856014) and, for one installation, a newer one
(logo on the front, UC25HQ80 0xB36014) – on **one** Raspberry Pi 5 (Raspberry Pi OS Bookworm):

| Function | Status |
|---|---|
| Connecting, detecting Melodee, editor protocol | used, works |
| **Firmware update Melodee 1.6.0-rc.8 → 1.6.0 through the suite** (Firmware+ › Install) | done once, worked |
| **Firmware switch KARMA 3.0.5 → original V15 → Skiff 2.7** through two self-powered USB hubs, careful mode (2026-11-18) | done once, worked; flight logs of both steps: 1244 and 2348 requests, no repeated request, no send failure, the FM-1 was back at the same USB path after every restart (update loader of the original firmware, V15, Felucca loader, Skiff). The kernel reported one `device descriptor read/64, error -71` at one re-connection and recovered by itself. The switch went on by itself after the V15 step |
| Earlier switches on the same unit, directly at the Pi (2026-11-05): KARMA 3.0.3 and Melodee 1.6.1 via V15, V16 via V15, V15, JIANT V10.6c | known only from the device profile's history, which the app writes when an installation ends with the expected identity. No flight log, and nobody wrote down how the FM-1 behaved – so these do not count as a test report (in that session the app also split the one FM-1 into two profiles; fixed in 2.6.1) |
| Detecting KARMA 3.0.5 (INFO "FELUCCA KARMA 3.0.5") and Skiff 2.7 (FM-1_727, INFO "FELUCCA v2.7") | seen once each, 2026-11-18 |
| Full backup (read-only): Melodee; **KARMA 3.0.5** (14 objects, 0–9 and 32–35, twice, identical); **Skiff 2.7** (29 objects, 0–9 and 16–34, commands 34–36, twice, identical; on a freshly installed Skiff, so only three objects had content) | Melodee in use; KARMA and Skiff once each, 2026-11-18 |
| MIDI (monitor, keyboard, connections) | basic functions in use |
| Studio (fetch backup, play on screen) | basic functions in use; **fetch from a Skiff 2.7 FM-1 with the Skiff 2.2 twin** once (2026-11-18: opens with the version warning, projects empty on the fresh firmware) |
| USB: the same path on every re-enumeration; the original firmware reports a serial number of the unit's own (two units: two different numbers), the Felucca-family firmwares none | seen on this one Pi (through the hubs and directly) on 2026-11-18. Other computers, hubs and USB controllers: not known |
| A newer FM-1 (logo on the front) **as delivered**, read-only (2026-11-18) | one unit seen: USB 4c4a:c755, product "FM-1", it answers the update handshake as `FM-1_015`. So a factory build that calls itself version 15 runs on a newer unit. That is **not** a test of the public V15 package on such a unit: that package was never written to one |
| **First installation on a newer FM-1** (2026-11-18): that unit, from its factory firmware (identity `FM-1_015`) to **Skiff 2.7**, directly at the Pi, careful mode off, no V15 step | done once, worked; flight log: 2348 requests, no repeated request, no send failure, writing 53 s, `FM-1_727` back after 8.5 s. Saving works on the chip: backup → a change on the unit, SAVE, power off and on → the next backup differs in objects 0, 1 and 6, all with a valid CRC32. **Scope: one unit, one firmware (Skiff 2.7), one computer.** Says nothing about any other firmware or version on this chip, about writing the public V15 package to such a unit, about the recovery in ROM boot mode there, or about m-bootloader. In that session the app afterwards took the unit for the owner's other FM-1 (fixed in 2.6.1: profiles) |
| SLOOP 3 3.4.0 (installed outside the app) and m-bootloader 2.4's MIDI MODE on the earlier unit (2026-11-18), **read only** | seen once: recognition, the bundled editor connected, one SLOOP 3 backup, the installation refused – see “SLOOP 3 and m-bootloader” below. Nothing written |
| System check | detects patterns taken from real Pi 5 logs (USB controller crash at the FM-1's audio start, fluidsynth.service occupies the HDMI sound card) |

That is **two** devices of one owner, **three** installations with logs, **one** computer. **Not** run on hardware:
writing back from the Studio, restoring a backup, and on a newer FM-1 anything but that one Skiff installation. Everything else in this list counts as
untested, even if it runs cleanly in the simulation.

### Per step – what the app shows before the click

Every installation plan and the confirmations of the recovery, the factory voices and "own file" carry one line:
"Tested on a real FM-1 so far: … . This step (…): only in the simulation." It comes from this
table, which exists a second time in the code (`fm1companion/flash/tested.py`); `tests/test_flash_audit.py` fails
when the two differ. Change a row only after a hardware test report, and change both. "yes" means: run **once**, on
one FM-1 at one Raspberry Pi 5, with logs – not "proven". (`new_chip_skiff`: Skiff 2.7 once, on one newer unit,
without a V15 step.)

| Step (`tested.py` key) | Run on a real FM-1 by this app |
|---|---|
| `update_melodee` – an update within Melodee | yes |
| `update_same` – an update within another firmware than Melodee | no |
| `switch` – a switch to another firmware | yes |
| `v15_step` – the intermediate step via the original V15 | yes |
| `chain_continue` – continuing a switch after the V15 step | no |
| `to_stock` – going back to the original firmware | no |
| `v16` – the original firmware V16 | no |
| `own_file` – installing your own .fwsc file | no |
| `resume` – finishing an interrupted installation | no |
| `resources` – rewriting font and factory sounds | no |
| `two_devices` – several FM-1 connected at once | no |
| `usb_hub` – installing through a USB hub (careful mode) | yes |
| `factory_voices` – restoring the factory voices of the original V15 | no |
| `rom_restore` – writing in ROM boot mode (recovery stage 2) | no |
| `new_chip_skiff` – installing Skiff on a newer FM-1 (logo on the front, flash UC25HQ80) | yes |
| `new_chip` – anything else on a newer FM-1 (logo on the front, flash UC25HQ80) | no |

Why three rows stay "no" although something happened: `chain_continue` is the separate *Continue* after an
interruption – in the run of 2026-11-18 the switch went on by itself after the V15 step, *Continue* was not used.
`to_stock` and `v16`: the profile history of 2026-11-05 records a V16 and a V15 installation as finished, but
without a log or a report. `usb_hub`: the run had no dropout, so careful mode's automatic resume never had to act –
that part is still simulation only.

## Not tested on real hardware

### Flashing and rescue (highest risk)

| Function | Status |
|---|---|
| Installing a different firmware (switch Melodee ↔ SLOOP ↔ Skiff …) | one switch on hardware (KARMA 3.0.5 → Skiff 2.7 via V15, see above); every other pair: simulation only |
| V15 intermediate step when switching firmware (always, except updates within the same firmware), *Continue* / *Dismiss* after the V15 step | the V15 step ran once on hardware as part of that switch; *Continue* / *Dismiss*: simulation only. The policy follows what firmware authors ask for (start from V15); that the two-step path itself is safer than a direct switch between two Felucca-family firmwares is **not** shown by any test – the V15 step is a write without start protection, and it does not remove the old firmware's saved data |
| Installing KARMA (built-in, packages pinned from its GitHub Pages site) | simulation only; the pinned checksums were compared with the files in a clone of KARMA's repository (all seven versions match), the download URLs themselves (commit-pinned raw files) are not fetched in CI |
| Cancel during the check of the package (step 1) on a Felucca-family firmware; a lost last answer of step 1 (answered again, or reported as "nothing written") | simulation only, behaviour of the device taken from Melodee's `ota.c`. With the original firmware as the running firmware Cancel is switched off once the update has started – its step 1 is not known |
| The start identity is compared with the plan when writing starts (`expect_identity`) | simulation only |
| Back to the original (V15, V16) | simulation only as a tested step. The device profile's history of 2026-11-05 records a V16 and a V15 installation on the owner's FM-1 as finished – no log, no report |
| Way back from V16 (via V15) | simulation only; whether a real V16 accepts a step back to V15 is unconfirmed (the same history shows V15 installed four minutes after V16, which fits, but nobody recorded what the FM-1 did) |
| Resuming after a power cut / aborted installation, CHECK HEAD detection (FAILED −5) and the way out the app names | simulation only; after a dropout the app says where the FM-1 *probably* is – it does not look again by itself |
| Finishing an interrupted installation (update mode, rescue level 1) | simulation only |
| Rewriting font & factory sounds (Melodee's resource tool) | only against the demo and source code; with 1.6.0 the update loader reports the resources as complete by itself |
| Installing your own `.fwsc` file | simulation only |
| **Telling two FM-1 apart** (2.6.1, after pi finding 35: the earlier unit, restored to Skiff 2.7 with M-Flasher, was connected silently as the newer one at the newer one's USB port) | logic only (unit tests replaying the owner's two profiles; nothing of this ran on hardware). How it works: the *unit ID* – the original firmware's USB serial number, which equals M-Flasher's “unit id” and survives every flash (two units measured, 2026-11-18), and m-bootloader's unit ID – is learned into the profile whenever it is seen (also in the V15 step of a switch, where another unit's ID now stops the switch before the target is written). A profile with a unit ID matches only that ID. A unit without a unit ID (the Felucca family) is compared with every profile; only positive evidence rules one out: another unit ID, a flash chip that was *read* as the other one, or that profile's unit being connected at another port right now. Firmware, identity, USB port and an answer from an earlier plug-in are no evidence. Two or more candidates: the app asks at every plug-in – owners of two FM-1 on Felucca-family firmwares get the question every time. Unanswered: no profile changes, no write, the strictest rule of the candidates. Answered only: every write names the unit and its revision first. ROM boot mode reads only the flash chip, not the unit ID: not used for matching. The app's own installation at a USB port still names the unit at its first re-appearance there (then the write confirmation applies). Byte order of m-bootloader's unit ID against the serial number: both are accepted, not checked |
| Pinning the target device via the USB path after the restart, two FM-1 at the same time | pinning by USB path: held in the one switch on hardware (same path at every restart, one Pi 5, through two hubs). Two FM-1 at the same time: simulation only. Telling two FM-1 apart by the original firmware's serial number: two units showed two different numbers; the matching rules of the app (a profile learns the number, the app asks instead of guessing) are tested as logic only. Whether the update loaders report the unit's own number is not known |
| Downloads: https only, size limit, a download that breaks off | only against fake servers in the tests |
| **Rescue level 2** (ROM boot mode: write back V15, repair settings, full backup) | only with the Unbricker's mock; pkexec, kernel module `sg`, jl-uboot-tool and handing the files over to the user never on a device. The Unbricker itself has been used on real devices by its community, its embedding in the app has not |
| **"Is my FM-1 clean?"** | quick check (command 82) only against the demo and the Melodee source code; dump check only with model dumps built from real packages, never with a real 1 MiB dump; reading in ROM boot mode only with the mock |
| **Clean up leftovers** (erase V16 leftovers in 0x93000–0xA8FFF sector by sector, expert mode, `flash/restzone.py`) | only with the Unbricker's mock. That the WL82 loader really limits "erase sector" to 4 KiB is an assumption – otherwise the block check after each sector stops |
| **The newer FM-1** (logo on the front, flash UC25HQ80 0xB36014): the table of minimum versions (`data/hardware.json`), the question about the unit, the blocker / confirmation in the plan, the flash ID read in ROM boot mode or from a firmware's identity answer | **one installation ran on one newer unit** (Skiff 2.7, 2026-11-18, see "Used on real hardware") – the only measured row of the table; the unit as delivered identifies as `FM-1_015`, which says nothing about the public V15 package on such a unit. The minimum versions are read from the firmwares' sources and release notes (commits named in the table), not measured; the plan's rule is tested as pure logic and in the UI test; that the original V15 works on these units is what other projects write (Felucca, ChoralRoot, TransitFM1, the Unbricker), not something this app has seen; about V16 on these units nothing is known. Whether a logo on the front always means the newer chip is the community's rule of thumb, not a fact from M-VAVE |
| Rescue level 2 on the newer chip: the bundled Unbricker 1.2.1 (upstream a1eaf4d) accepts 0xB36014 | upstream's own tests with its mock only (they run in this suite); the app never wrote in ROM boot mode on either chip |
| An identity answer that carries the flash ID (fm1-base builds of 2026-11-05/06: AMB-1 1.3.1, LOOM-8 0.13 …) is recognised as an FM-1 again and the ID is kept | only with answers built in the tests after felucca-bsp's `ota.c` |
| Rescue level 3 (Transporter with XIAO RP2040) | instructions only; the Pico-instead-of-XIAO variant is unverified |
| V16 specifics (first-start formatting, boot head with the original loader) | only in the emulator or open; test plan: help page "V16 – note" and docs/flash-protocol.md §17 |

### SLOOP 3 and m-bootloader

**With the app on a real FM-1, once (2026-11-18):** the owner's earlier unit (flash 0x856014), SLOOP 3 3.4.0-beta and
m-bootloader 2.4 installed on it outside the app, at one Raspberry Pi 5 (Raspberry Pi OS Bookworm, Qt 6.4.2), **read
only**. This table exists a second time in the code (`SLOOP3_HARDWARE` in `fm1companion/flash/tested.py`);
`tests/test_flash_audit.py` fails when the two differ.

| What (`tested.py` key) | On a real FM-1 with this app |
|---|---|
| `sloop3_connect` – recognising SLOOP 3 3.4.0 and connecting its bundled editor (3.4.0) | seen once: recognised by its own INFO (USB `1209:0001` "FM-1 SLOOP 3"); the bundled 3.4.0 editor showed "Connected: SLOOP 3.4.0, protocol 2" with live values, on Qt 6.4.2 (Chromium 102). Its tabs and editing were not gone through |
| `sloop3_backup` – a SLOOP 3 backup (read only) | seen once: `sloop3-backup` with 6 parts (objects 0–5), every CRC-32 valid, the empty sample slots listed as not included; nothing on the FM-1 changed |
| `mboot_recognise` – recognising m-bootloader 2.4's MIDI MODE (not as a Felucca update loader) | seen once: USB `1209:0002` "FM-1 Update" (bcdDevice 2.00) – the same USB ID as a Felucca update loader – taken as MIDI MODE by its answer to 0x12, which also gave the flash ID 0x856014 (stored with the device) |
| `sloop3_refusal` – refusing an installation under a running SLOOP 3 | seen once: "Install on active FM-1" (Skiff 2.7 chosen) refused at once, nothing was sent. (The button is now off in that state, the refusal stays as a second check) |
| `sloop3_restore` – restoring a SLOOP 3 backup | not run (writes) – stand-in only, see below |
| `mboot_flash_copy` – the flash backup in MIDI MODE | not run – stand-in only, see below |
| `sloop3_writing` – anything that writes to a SLOOP 3 or m-bootloader unit | not run |
| `sloop3_new_chip` – SLOOP 3 or m-bootloader on a newer FM-1 (logo on the front) | not run |

The run also showed a profile mix-up: after the owner answered "Which FM-1 is this?" for the SLOOP 3 unit, the profile
of the other FM-1 (not plugged in) had lost its firmware. Fixed afterwards (2.6.1: a unit that answers nothing changes
no profile's firmware, and the USB name "FM-1 SLOOP 3" counts as SLOOP 3); the fix itself was not run on hardware.

Everything else below ran **only against stand-ins**: **the PC twin** = SLOOP 3's own `host/twin.c`
("serve": `fw/main.c` 3.3.7 on a simulated FM-1 with the published application area, built from the tag) and
**m-bootloader's code** = its `src/midi_update.c` and `src/core.c` of 2.4, compiled for the PC with a flash file behind
it. Neither is in the repository; the tests in the repository use `tests/fake_sloop3.py` (which replays the twin's
recorded answers) and `tests/fake_mboot.py` (written from m-bootloader's specification).

**Seen on the same FM-1, outside the app (2026-11-18):** m-bootloader 2.4 installed with M-Flasher 0.12.1, SLOOP 3
3.4.0-beta with the author's installer page in Chromium. MIDI MODE is USB `1209:0002`, product "FM-1 Update",
bcdDevice 2.00 – the same USB ID as the Felucca family's update loaders, only name and device version differ. SLOOP 3
is `1209:0001`, "FM-1 SLOOP 3", bcdDevice 3.10, MIDI port "FM-1 SLOOP 3 MIDI 1". So the names the app relies on are
confirmed.

| Function | Status |
|---|---|
| Telling MIDI MODE from a Felucca update loader (same USB ID): product name exactly "FM-1 Update", bcdDevice 2.00 where the system tells it, the handshake answer, and the answer to 0x12; a port that looks like MIDI MODE and does not answer 0x12 gets nothing sent either | the real unit in MIDI MODE was told apart once (`mboot_recognise` above); a port that does not answer 0x12, and other loaders: unit tests only |
| A SLOOP 3 unit beside another FM-1 (no USB serial number, no identity): the owner is asked which unit it is when the profile at that USB port last ran another firmware | the question came once on the real unit (2026-11-18) and named the right profile; the other profile lost its firmware then (see above, fixed afterwards: unit test and replay with the real device layer only) |
| SLOOP 3 3.4.0: its own editor (bundled beside the one of 3.3.x), backup round trip | PC twin of 3.4.0, in the app's window on Qt 6.11 |
| Recognising SLOOP 3 (its INFO; the Felucca probes stay unanswered and change nothing) | seen once on the real unit (`sloop3_connect` above); that the probes change nothing: PC twin only (all five probes unanswered, INFO before and after the same) |
| SLOOP 3's bundled web editor on the Editor page (connect, lists, LIVE state, all 13 tabs, no JavaScript error, only its own protocol goes out) | PC twin, in the app's window on Qt 6.11 (`tests/ui_sloop3_test.py` with `SLOOP3_DEVICE_CMD`); in the repository with the fake. On the real unit with Qt 6.4.2 / Chromium 102 once: connected, live values (`sloop3_connect` above) – the tabs one by one, and the CSS fallback, were not checked there |
| Editing in the editor beyond opening the tabs (drawing notes, writing samples, kits, the FM6 bank, projects) | not driven by the app's tests; the author's own `tests/editor_e2e.js` does that in Chromium against the same twin |
| Recording a sample with the microphone in the bundled editor | not available (the web view grants no microphone); a file can be dropped |
| `sloop3-backup`: read, restore of sounds, FM6 bank, SYN kits and project, read back and compared | reading: once on the real unit (`sloop3_backup` above). The restore: PC twin only (`tests/test_sloop3.py` with `SLOOP3_TWIN_CMD`: back up, change, restore, back up again); in the repository with the fake. Reading while SLOOP 3 plays on a real FM-1: unknown – stop playback |
| Samples of SLOOP 3 in a backup | not possible over its protocol (no read command); only in a flash backup |
| Recognising m-bootloader's MIDI MODE (0x12) and the flash chip (JEDEC ID → hardware revision) | the earlier chip (0x856014) once on the real unit (`mboot_recognise` above); the newer chip's ID: m-bootloader's code on the PC only |
| Flash backup in MIDI MODE (0x23 reads, compared with the chip's CRC-32 from 0x24) | not run on the real unit (`mboot_flash_copy`); m-bootloader's code on the PC: 1 MiB read, identical to the flash file. Speed and reliability over real USB: unknown. m-bootloader versions before the read commands (the installer page mentions some that do not answer reads): the app then reports “does not answer” and saves nothing |
| 0x12 sent to an update loader that is not m-bootloader | not sent by design: only a port named exactly "FM-1 Update" is asked (Felucca-family loaders are "<Name> Update"). What M-VAVE's or a Felucca loader would do with 0x12 was not tried |
| Installing SLOOP 3, installing m-bootloader | **not done by the app** (the author's installer page; M-Flasher / M-UPGRADE) |
| The blocker for the newer hardware revision (no installer page offered; explicit confirmation when unknown) | page logic only (`tests/ui_sloop3_test.py`) |
| Installing another firmware from m-bootloader's MIDI MODE with the app's installer | **refused by the app** (it tells MIDI MODE from an update loader by 0x12; “Install on active FM-1” is off there, and the installer and Recovery › “Finish installation” refuse it again); page logic, UI test and unit test only – not pressed on the real unit. m-bootloader's README lists the browser installers it was tested with |

### Studio

| Function | Status |
|---|---|
| **Writing back to the FM-1** (Studio › Write back to the FM-1) | simulation only (safety backup first, version check) |
| Sound fidelity of the twin compared with the device | firmware code identical, timing and overload behaviour not |
| **Skiff twin** (2.2, 2.1.1: fetch from a Skiff FM-1, open a Skiff backup, write back) | only against the twin itself and the suite's own code (simulation); never a write-back to a real Skiff FM-1. *Fetch* ran once against a real Skiff 2.7 FM-1 with the 2.2 twin (freshly installed firmware, empty projects) |
| Skiff 2.x backups in the backup layer (29 objects with the pattern bank) | backups of the Skiff twin, and two backups of a real Skiff 2.7 FM-1 (2026-11-18, nearly empty: fresh firmware). Restoring one to a device: never |
| **JIANT FM twin** (V10.6z2, V10.60, V10.61: fetch from a JIANT FM-1, open a JIANT backup, write back) | only against the twin itself and the suite's own code (simulation); never against a real JIANT FM-1 or a backup made by a real device or JIANT's web editor |
| JIANT backups in the backup layer (43 objects: 0–10, the songs' sections 40–71; 35 objects from V10.6p) | only with backups of the JIANT twin; the device detection by INFO "JIANT …" and identity FM-1_909 only in tests |
| **SLOOP twin** (2.8.5, 2.8.4: fetch from a SLOOP FM-1, open a SLOOP backup, write back) | only against the twin itself, the demo device and the suite's own code (simulation); never against a real SLOOP FM-1 or a backup made by SLOOP's web editor on a real device |
| **CoralFlower twin** (0.7.1, 0.7: fetch from a CoralFlower FM-1, open a CoralFlower backup, write back with RESTART) | only against the twin itself and the suite's own code (simulation); never against a real CoralFlower FM-1 or a backup made by a real device or CoralFlower's web installer |
| CoralFlower backups in the backup layer (26 objects: 6–11, 30, 31, 50–67; 8, 14, 17 in 0.1–0.4.2) | only with backups of the CoralFlower twin; the restart after a restore (RESTART 72) and the device detection by INFO "CoralFlower …" and identity FM-1_930 only in tests |
| **KARMA twin** (3.1, 3.0.5: fetch from a KARMA FM-1, open a KARMA backup, write back) | only against the twin itself and the suite's own code (simulation); never against a real KARMA FM-1 (the backup of a real KARMA 3.0.5 was made, not opened in the twin as a test) |
| KARMA backups in the backup layer (SLOOP's protocol and format, 14 objects; FUN7 projects; SLOOP backups onto KARMA) | backups of the KARMA twin, and two backups of a real KARMA 3.0.5 FM-1 (2026-11-18, 14 objects each, identical); the detection by INFO "FELUCCA KARMA 3.0.5" was seen on that device. Restoring, FUN7 projects, SLOOP backups onto KARMA: only in tests |
| **Felucca twin** (1.8.1, 1.8.0: fetch from a Felucca FM-1, open a Felucca backup, write back) | only against the twin itself and the suite's own code (simulation); never against a real Felucca FM-1 or a backup made by Felucca's own web editor on a device |
| Felucca recognition next to SLOOP (INFO "FELUCCA v…" vs "FELUCCA SLOOP …", FM-1_900 / FM-1_9XY) and the Felucca twin for Felucca forks (with a warning) | only in tests (fake INFO links, remembered identities); no fork device tried |
| **ChoralRoot FM-1 twin** (0.39, 0.38: fetch from a ChoralRoot FM-1, open a ChoralRoot backup, write back with RESTART) | only against the twin itself and the suite's own code (simulation); never against a real ChoralRoot FM-1 or a backup made by ChoralRoot's installer on a device |
| ChoralRoot backups in the backup layer (34 objects; 28 / 27 / 17 of 0.14–0.21: loop slots 40–49, no projects, no samples) and the detection by INFO "ChoralRoot …" / identity FM-1_920 | only with backups of the ChoralRoot twin and in tests |
| ORBIT backups (SLOOP's protocol and format, refused on SLOOP / KARMA and the other way round), Hortator's own INFO, GR!MM's identity FM-1_966 | only in tests (fake links and files); no ORBIT, Hortator or GR!MM device tried |
| **X0X twin** (1.2.1, 1.2.0: fetch from an X0X FM-1, open an X0X backup or `.x0b` file, write back whole with SEL on the device) | only against the twin itself and the suite's own code (simulation); never against a real X0X FM-1 |
| **LOOM-8 twin** (0.14, 0.13.1: fm1-base's image `.loom8`, write back whole with SEL) | only against the twin itself; never against a real LOOM-8 FM-1 (no INFO: the device is known by the profile's firmware only) |
| **FoMni and AMB-1 twins** (no backup over USB: play, export, studio states) | only the twins themselves; never compared with a real device |
| **FM1 Open twin** (V14, M-VAVE's simulator code: fetch from an FM1 Open FM-1, open an FM1 Open backup, write back) | only against the twin itself and the suite's own code (simulation); never against a real FM-1 with FM1 Open. Its open engine stands in for the device's binary engine (M-VAVE's simulator does the same): sound may differ from the device |
| FM1 Open backups (`fm1openbk.py`: its file protocol, the working project, 16 slots read by loading them, 4 DX7 library banks, the sample bank) | only with the twin; that a real FM1 Open answers the identity FM-1_8000014 to the update handshake in normal operation is taken from M-VAVE's workbench code (`sdk/WebUpdate.js`), not observed |
| **The FM1 Open twin as the stand-in for the original V15/V16** (note in the Studio, no fetch / write back) | only the page logic, with a simulated device |
| **Original V15: restore factory voices** (FM Voices › Original V15: restore factory voices …: all 128 voices in A–D overwritten, commands 33/34 on object 5 as M-VAVE's `sdk/WebUpdate.js` restoreFactory; the current voices cannot be saved first) | only against a simulated V15 built from that code; never on a real FM-1 |

### Backups, presets, projects, samples
- Restoring a backup to the device (full and partial).
- Real SLOOP: backup (commands 34–36). Real SLOOP and Skiff: sample upload, "stop song" behaviour. (The backup of a
  real Skiff 2.7 ran once, see above.)
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
