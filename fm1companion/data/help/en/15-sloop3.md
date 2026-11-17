# SLOOP 3 (public beta)

**SLOOP 3** is a rewrite of SLOOP by isod89: five tracks (four synths and the drums), 13 engines, 8 patterns per track,
a song, samples and slices, and its own web editor. Its author calls it a **public beta**: expect bugs, keep backups,
do not rely on it on stage yet. It is not SLOOP 2.x with a new number – the app treats it as a firmware of its own.

## What the app does with SLOOP 3

* **It recognises it.** An FM-1 that runs SLOOP 3 shows up as “SLOOP 3 3.3.7 (public beta)”. An FM-1 that waits in
  m-bootloader's MIDI MODE shows up as “m-bootloader – MIDI MODE”, with the flash chip it found.
* **Its editor.** The page [Editor](app://page/editor) shows SLOOP 3's own web editor – the author's file, unchanged,
  bundled with the app, so it works without internet. From that page only SLOOP 3's own editor commands reach the FM-1.
* **Backups.** [Backups](app://page/backups) › “Back up now” saves the project (as saved and as it is at that moment),
  your sounds with favourites and theme, your FM6 bank and your SYN kits into a `sloop3-backup` file. “Restore” puts
  them back onto an FM-1 that runs SLOOP 3. Stop playback first.
* **A copy of the whole flash**, while the FM-1 is in m-bootloader's MIDI MODE:
  [Firmware+](app://page/firmware) › SLOOP 3 › “Back up the whole flash now”. The app only reads, and compares its copy
  with the checksum the flash chip itself computes.

## What it does not do

* **Your samples are not in a `sloop3-backup`.** SLOOP 3 has no command to hand them out. They are in a copy of the
  whole flash, or in the files you sent them from.
* **The app does not install SLOOP 3, and it does not install m-bootloader.** It explains the way and opens the
  author's installer page in your browser.
* **No automatic backup** for SLOOP 3: on a beta the app reads only when you ask.
* The Studio has no SLOOP 3 twin. The other device pages (projects, presets, samples) speak the Felucca editor
  protocol, which SLOOP 3 does not have – use its editor.
* Recording a sample from the microphone inside the bundled editor is not available; drop a file instead.

## Before you install it

1. **m-bootloader is required.** SLOOP 3 installs only through m-bootloader's MIDI MODE. m-bootloader is an open-source
   bootloader by Julius Vitkauskas; installing it replaces the FM-1's boot area. That is done once, outside this app:
   with M-Flasher in USB download mode, or – on M-VAVE's firmware – with M-VAVE's M-UPGRADE and `fm1-uboot.fwsc`.
2. **Back up the whole flash first.** It is the only way back: SLOOP 2.8 or M-VAVE's firmware return by writing that
   backup back with M-Flasher. SLOOP 3 reads SLOOP 2.8's projects, presets, samples and settings once, at its first
   start, and never writes them.
3. **Newer FM-1s (sold from late 2026, M-VAVE logo on the front, flash chip UCUN UC25HQ80): do not install it for
   now.** SLOOP 3 has not been tried on that revision, m-bootloader is little tested on it, and its author reports one
   such FM-1 that did not start any more after m-bootloader was installed. In MIDI MODE the app reads the chip and
   says which one it is; for the newer one it does not offer the installer page. M-Flasher shows the same under
   FLASH CHIP: `0x856014` is the older chip, `0xB36014` the newer.
4. An official M-VAVE update puts M-VAVE's bootloader back. m-bootloader then has to be installed again.

## Good to know

* Only one program can talk to the FM-1 in MIDI MODE at a time: quit the app (or unplug and replug) before you use the
  installer page in the browser.
* If SLOOP 3 does not start: hold OCT− at power-on for its RESCUE, or hold PLAY for m-bootloader's menu.
* All of this was checked on the author's PC twin of SLOOP 3 3.3.6 and 3.3.7 (the firmware's code on a simulated
  FM-1) and against m-bootloader 2.4's own MIDI MODE code on a PC – **never on a real FM-1 by this app**.
* More: [Switching firmware](help:02-firmware) · [Backups](help:04-backups) · [Links](help:10-links)
