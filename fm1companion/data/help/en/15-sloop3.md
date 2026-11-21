# SLOOP 3 (public beta)

**SLOOP 3** is a rewrite of SLOOP by isod89: five tracks (four synths and the drums), 13 engines, 8 patterns per track,
a song, samples and slices, and its own web editor. Its author calls it a **public beta**: expect bugs, keep backups,
do not rely on it on stage yet. It is not SLOOP 2.x with a new number – the app treats it as a firmware of its own.

## What the app does with SLOOP 3

* **It recognises it.** An FM-1 that runs SLOOP 3 shows up as “SLOOP 3 3.3.7 (public beta)”. An FM-1 that waits in
  m-bootloader's MIDI MODE shows up as “m-bootloader – MIDI MODE”, with the flash chip it found.
* **Its editor.** The page [Editor](app://page/editor) shows SLOOP 3's own web editor – the author's file, unchanged,
  bundled with the app, so it works without internet. From that page only SLOOP 3's own editor commands, notes and controllers reach the FM-1.
* **Backups.** [Backups](app://page/backups) › “Back up now” saves the project (as saved and as it is at that moment),
  your sounds with favourites and theme, your FM6 bank and your SYN kits into a `sloop3-backup` file. “Restore” puts
  them back onto an FM-1 that runs SLOOP 3. Stop playback first.
* **A copy of the whole flash**, while the FM-1 is in m-bootloader's MIDI MODE:
  [Firmware+](app://page/firmware) › SLOOP 3 › “Back up the whole flash now”. The app only reads, and compares its copy
  with the checksum the flash chip itself computes.

## Its project in the app

SLOOP 3 keeps **one project** on the FM-1. The app reads it the way the backup does – read only:

* [Project View](app://page/project_view) › “Read from FM-1” shows it with “running music” (as it plays now, unsaved
  changes too) or project 1 (the one saved on the FM-1): tracks, the 8 patterns per track with their notes, the song,
  tempo and swing. Listen to it there and export it as a MIDI file (the drums on channel 10), as CSV or as JSON.
  “Open project file …” also opens a `.s3p` from SLOOP 3's editor, an `.s3b` editor backup or a `sloop3-backup`.
* [Projects](app://page/projects) fetches it into the library as a `.s3p` file – the same file the editor's
  Projects tab saves and opens. The library lists it with its tempo and length; “Add file …” takes `.s3p` and `.s3b`
  files, “Save as file …” puts a copy wherever you like.
* **Putting a project onto the FM-1 is not in the app yet.** Use the editor's Projects tab for that.
* Not played in the preview and the MIDI export: fills, the arpeggiator, harmony and strum, the parameter locks
  (they are listed in the JSON) and the patterns' own sounds. A project from a newer SLOOP 3 than 3.4.0 is not opened
  – the app says so instead of guessing.

## What it does not do

* **Your samples are not in a `sloop3-backup`.** SLOOP 3 has no command to hand them out. They are in a copy of the
  whole flash, or in the files you sent them from.
* **The app installs SLOOP 3 only through m-bootloader's MIDI MODE** – “Install SLOOP 3 with this app (MIDI MODE)”,
  offline from the SLOOP 3 3.4.0 it brings along, after a whole-flash backup read twice in MIDI MODE (not yet run on a
  real FM-1; see the help page “Update mode & recovery”). m-bootloader, which SLOOP 3 needs, the app installs too:
  that is the first step of the same button, or Recovery › Tools › “Install m-bootloader”. The author's installer page
  stays the other way.
* **No automatic backup** for SLOOP 3 unless you switch it on (see below): on a beta the app reads only when you ask.
* The Studio has no SLOOP 3 twin. The other device pages (presets, samples) speak the Felucca editor
  protocol, which SLOOP 3 does not have – use its editor.
* Recording a sample from the microphone inside the bundled editor is not available; drop a file instead.

## Status, samples from a flash copy, more for backups

* **Live values.** [FM-1 Status](app://page/fm1) shows SLOOP 3's CPU load (average and peak), the voices playing,
  tempo and bar, and a level meter per track, every 2 s, read with the same read-only request its editor uses. SLOOP
  2.8 shows CPU load, tempo and its four meters the same way. Nothing is asked while a backup, a restore or an
  installation has the FM-1.
* **Samples from a flash copy.** [Backups](app://page/backups) › “Flash backups” lists the copies of the whole flash
  (date, size, chip, and whether the file still matches the checksum taken when it was copied). “Samples to the
  library” takes USR1–USR4 out of one as WAV files (“SLOOP 3 USR1 …”) into Samples › “SLOOP 3 (flash)”; “Samples from
  a file …” does the same with a copy M-Flasher saved. Offline – nothing is sent. SLOOP 2.8 keeps its samples in the
  same four places and format, so a copy made under SLOOP 2.8 gives SLOOP 2.8's samples. “Write back …” writes a copy
  back through M-Flasher, only onto the FM-1 it came from (see Update mode & recovery).
* **The editor's own backup file (.s3b).** “Add file …” takes an `.s3b` over as a SLOOP 3 backup (the project and
  your sounds – all an `.s3b` holds); “As .s3b …” saves a SLOOP 3 backup for the editor's “Restore”.
* **Look inside and compare.** [Browse & Compare](app://page/backup_browser) shows a SLOOP 3 backup's project
  (format, tempo), your 32 sounds (S01–S32) with favourites, the FM6 bank's voices (B01–B27), the SYN kits and the
  settings, and compares two backups part by part and sound by sound. It goes back only whole (“Restore”).
* **Automatic backup** for SLOOP 3 is there but **off**: Backups › “also for SLOOP 3”. Reading while SLOOP 3 plays has
  not been tried on a real FM-1 yet.

## Before you install it

1. **m-bootloader is required.** SLOOP 3 installs only through m-bootloader's MIDI MODE. m-bootloader is an open-source
   bootloader by Julius Vitkauskas; installing it replaces the FM-1's boot area. That is done once: by this app (Recovery
   › Tools, or the first step of “Install SLOOP 3”, with M-Flasher in USB download mode), or – on M-VAVE's firmware –
   with M-VAVE's M-UPGRADE and `fm1-uboot.fwsc`.
2. **Back up the whole flash first.** It is the only way back: SLOOP 2.8 or M-VAVE's firmware return by writing that
   backup back with M-Flasher. SLOOP 3 reads SLOOP 2.8's projects, presets, samples and settings once, at its first
   start, and never writes them.
3. **Newer FM-1s (sold from late 2026, M-VAVE logo on the front, flash chip UCUN UC25HQ80): only m-bootloader 2.4
   and SLOOP 3 3.4.0 or later, at your own risk.** Their owner reports one such FM-1 on which m-bootloader 2.4 and SLOOP
   3 3.4.0 run and saving works (2026-11-19) – done outside this app, one unit. Older m-bootloader versions are risky
   there: its author reports one such FM-1 that did not start any more after 1.1 or 1.2. In MIDI MODE the app reads
   the chip and m-bootloader's version; for a newer unit it offers the installer page only with m-bootloader 2.4 as the
   build that was reported (it shows "2.4-395e6cc" in MIDI MODE) or 2.5 and later (the read-only flash copy always, in MIDI MODE). M-Flasher shows the chip under FLASH CHIP: `0x856014` is the older chip, `0xB36014` the
   newer.
4. An official M-VAVE update puts M-VAVE's bootloader back. m-bootloader then has to be installed again.

## Good to know

* Only one program can talk to the FM-1 in MIDI MODE at a time: quit the app (or unplug and replug) before you use the
  installer page in the browser.
* If SLOOP 3 does not start: hold OCT− at power-on for its RESCUE, or hold PLAY for m-bootloader's menu.
* All of this was checked on the author's PC twin of SLOOP 3 3.3.6, 3.3.7 and 3.4.0 (the firmware's code on a simulated
  FM-1) and against m-bootloader 2.4's own MIDI MODE code on a PC.
* **On a real FM-1, once, read only** (2026-11-18: one earlier unit, one Raspberry Pi 5): the app recognised SLOOP 3
  3.4.0, its bundled editor connected, a SLOOP 3 backup was read (6 parts, all checksums valid), m-bootloader 2.4's
  MIDI MODE was recognised (not mistaken for an update mode), and an installation under the running SLOOP 3 was
  refused with nothing sent. **Not tried on a real FM-1:** restoring a SLOOP 3 backup, the flash backup in MIDI MODE,
  anything that writes, and anything on a newer FM-1 (logo on the front) – see 3. above for what its owner
  reports from outside the app.
* More: [Switching firmware](help:02-firmware) · [Backups](help:04-backups) · [Links](help:10-links)
