# A tour of the FM-1 Companion Suite in pictures

Forty screenshots of version 2.7.0, in the order you would meet the app: [everyday use](#1-everyday-use) ·
[sounds and projects](#2-sounds-and-projects) · [backups](#3-backups) · [firmware](#4-firmware) ·
[the bundled tools](#5-the-bundled-tools-m-flasher-m-bootloader-sloop-3) · [Studio twins](#6-studio-twins) ·
[MIDI](#7-midi) · [recovery](#8-recovery) · [settings and help](#9-settings-and-help).

**How these pictures were made – please read this once.** All of them come from **demo mode**
(`fm1-companion --demo`), English interface, light theme. No FM-1 was connected. The devices "FM-1 A", "FM-1 B" and
"FM-1 C" are the three simulated FM-1 of demo mode; the sounds in the sample library are generated tones; the
"flash backups" are a test image of random bytes. The questions of the bundled tools (sections 3 to 5) show the
app's own wording, filled in for a made-up unit called "FM-1 Earlier" – they were opened for the picture, nothing
was installed or written. Where a question ends with "Tested on a real FM-1 …" or "Not tested …", that sentence is
the app's own and says what really ran on hardware; the full list is [UNTESTED.md](UNTESTED.md).
The pictures can be made again with `tools/make_screenshots.py`.

Back to the [README](../README.md).

## 1. Everyday use

### Overview

![Overview page: the connected FM-1 with name, firmware and version, a backup card, firmware updates and quick-access buttons](screenshots/overview.png)

The first page after the start. It shows which FM-1 is connected, which firmware runs on it, whether there is a
backup, and offers the things you need most often as buttons. The list on the left is the whole app: every section
is one click away.

### Devices

![Devices page with three device cards, FM-1 A with Melodee, FM-1 B with SLOOP and FM-1 C with an unknown firmware](screenshots/devices.png)

If you own more than one FM-1, each gets its own card with its own colour, notes, firmware history and backups.
Below the cards you can compare two devices and copy projects or presets from one to the other. (Here: the three
simulated FM-1 of demo mode.)

### The Device menu

![The opened Device menu: back up now, reconnect, rename, download mode, install or remove m-bootloader, install SLOOP 3, Back to](screenshots/device-menu.png)

Everything that concerns the connected FM-1 is also in the **Device** menu: a backup with Ctrl+B, reconnecting, and
– new in 2.7.0 – the actions of the bundled tools (download mode, m-bootloader, SLOOP 3, "Back to …").

### Command search (Ctrl+K)

![Command search with the word backup typed in and a list of matching pages, actions and help topics](screenshots/command-search.png)

Press **Ctrl+K** and type what you are looking for – a page, an action or a help topic. Enter takes you there.

### Editor

![Editor page with Melodee's web editor: sound engine, preset list and parameters](screenshots/editor.png)

The web editor of the installed firmware, built into the app and usable without internet. This is Melodee's editor;
in demo mode it talks to a simulated device (the picture says "Mock device (no hardware)").

## 2. Sounds and projects

### Presets

![Presets page: the user presets on the FM-1 on the left, the preset library on the computer on the right](screenshots/presets.png)

Left: the user presets stored on the FM-1 (up to 384). Right: your library on the computer. "Back up all" copies
every used slot into the library; from there you can write presets back, compare, label and export them.

### Samples & Kits

![Samples and Kits page: a list of drum and tone samples with a waveform, and the kit builder with drum sounds mapped to keys](screenshots/samples.png)

Your sample library with a waveform preview, and on the right the kit builder: pick sounds, put them on keys, and
upload the kit to an FM-1 whose firmware has sample slots (Skiff, SLOOP, FM1 Open). The sounds shown here were
generated for the picture.

### Slicing a loop

![Slice dialog: a drum loop cut into sixteen pieces at its hits](screenshots/sample-slicer.png)

"Slice …" cuts a loop into pieces – at the hits it detects, in equal parts or on a tempo grid – and turns the pieces
into WAV files or directly into a kit.

### Projects

![Projects page: the 32 project slots of the FM-1 on the left, the project library on the computer on the right](screenshots/projects.png)

Left: the project slots on the FM-1. Right: your project library on the computer. Fetch projects to keep them, give
them names and notes, and play them back into any slot – also onto another FM-1 with the same firmware.

### Projects › View & Export

![View and Export: a project opened from a backup with its tempo and tracks, a play button and a pattern overview](screenshots/project-view.png)

Open a project from a backup, a file or live from the FM-1, look at its patterns and song, listen to it on the FM-1
or on the computer, and export it as a MIDI file for other music programs.

### Compose

![Compose page: settings for key, scale and chord progression on the left, the generated chords in a piano roll on the right](screenshots/compose.png)

Ideas instead of typing steps: the app generates rhythms, chord progressions, bass lines and melodies. Listen with
the FM-1's own sound, adjust the notes in the piano roll, then write the result into a pattern – the pattern is
read first, so you can undo.

### Compose › From MIDI File

![From MIDI File: a small MIDI melody opened and shown as steps of a pattern, with grid and length settings](screenshots/compose-midi-file.png)

Write a melody or a beat in any music program, save it as a MIDI file and bring it into a pattern of an FM-1 track
here. Longer pieces are spread over several patterns.

### Recording

![Recording page while recording: source, mode and sample rate, a running timer and level meters for eight channels](screenshots/recording.png)

Records the FM-1's sound over USB, with level meters and – with Melodee – separate tracks. Recordings can be opened
in Audacity or taken into the sample library. The signal in the picture is demo mode's built-in test signal.

## 3. Backups

### Backups › List

![Backups page: a list of three backups with date, firmware and content, buttons for restore and export, and below it the list of whole-flash backups](screenshots/backups.png)

Every backup of the active FM-1 with date, firmware and content; "Back up now" makes a new one, and the app can make
one automatically when you plug the FM-1 in. Below: **flash backups**, copies of the FM-1's whole memory chip made
with M-Flasher, each checked against the chip's own checksum. (The three flash backups in the picture are a test
image, not from a real FM-1.)

### Backups › Browse & Compare

![Browse and Compare: the content of one backup as a tree of running music, projects, presets and settings with checkboxes](screenshots/backups-browse.png)

Look inside a backup without restoring it: which projects, presets and settings it holds. Tick only the parts you
want back, or compare two backups to see what changed.

### Writing a whole-flash backup back

![The question before a whole-flash backup is written back: which unit, which image, what changes, where the fresh backup is, and what was tested](screenshots/dlg-flash-write-back.png)

Before the app writes a flash backup back, it first makes a fresh backup of the current state and then asks this
question. It names the unit, the image and how much will change. A flash backup only ever goes back onto the FM-1 it
came from. (Opened for the picture with a made-up unit; nothing was written.)

## 4. Firmware

### Firmware+ – the catalog

![Firmware+ catalog: cards of community firmwares with name, author, short description and labels, with search and filters above](screenshots/firmware-catalog.png)

More than 250 community firmwares for the FM-1 in one place, with search and filters ("directly installable", "with
simulator", …). The catalog data is bundled with the app, so it also opens offline.

### A firmware's page

![Detail page of the Skiff firmware: description, a note about how it is installed, and what the app can do with it](screenshots/firmware-detail.png)

One click on a card shows what the firmware is, who wrote it, what to know before installing, and what the app can
do with it – install, back up, edit, simulate.

### Install & Versions

![Install and Versions: a list of built-in firmwares on the left, the versions of Melodee with the installed one marked, and install buttons](screenshots/firmware-install.png)

Choose a firmware and a version, then "Install on active FM-1". The installed version is marked. From here you
also go back to M-VAVE's original firmware. (The version list in the picture is demo mode's.)

### The installation plan

![Installation plan for Skiff on a unit with m-bootloader: three numbered steps with their risk, notes, a line about what was tested on a real FM-1, and a confirmation checkbox](screenshots/install-plan.png)

Nothing is installed before you have seen the plan: every step with its risk, what will be gone afterwards, and –
in bold – whether this kind of installation has ever run on a real FM-1. You have to tick the confirmation before
"Install" becomes active; "Dry run" checks everything and writes nothing. (A plan for a made-up unit with
m-bootloader and SLOOP 3, going to Skiff through download mode.)

### Simulator

![Simulator tab: the bundled Melodee simulator running, a drawn FM-1 with a lit display](screenshots/simulator.png)

Try a firmware before installing it: the bundled simulators run the firmware's real code on the computer. Play
with the mouse or the computer keyboard.

### "Does your FM-1 have the logo on the front?"

![The question whether the FM-1 has the M-VAVE logo on the front, with the answers newer unit, earlier unit and I don't know](screenshots/dlg-logo-question.png)

FM-1 sold from late 2026 have a different memory chip, and older firmware versions save nothing on it. The app asks
once per device and then never installs a version that does not know the chip of a newer unit.

### "Which FM-1 is this?"

![The question before a write: this is FM-1 Earlier, check the front of the FM-1 you have connected](screenshots/dlg-which-fm1.png)

Most firmwares do not tell the app which unit they run on. If you own several FM-1, the app therefore names the
unit before every write and lets you check – it never guesses by the USB port.

## 5. The bundled tools: M-Flasher, m-bootloader, SLOOP 3

The app brings three tools by other authors along and drives them with its own buttons: **M-Flasher** (reads and
writes the FM-1's memory chip in USB download mode), **m-bootloader** (a replacement start program with a menu) and
**SLOOP 3** (a firmware that installs only through m-bootloader). The questions below were opened for the pictures
with a made-up unit; the last paragraph of each is the app's own statement of what ran on a real FM-1.

### Recovery › Tools

![The Tools card on the Recovery page: a table with M-Flasher, m-bootloader and SLOOP 3, their versions and checksum state, and buttons for backup, download mode, m-bootloader, SLOOP 3 and Back to](screenshots/recovery-tools.png)

The home of the three tools: which version is bundled and in use, whether its checksum is correct, and every action
as a button – back up the whole flash, write it back, download mode, install or remove m-bootloader, install SLOOP 3,
"Back to …". (The card's introduction still says that firmware through download mode and SLOOP 3 "follow in later
versions" – in 2.7.0 both are there: the buttons below it.)

### Installing m-bootloader

![The question before m-bootloader is installed: only the boot area is written, the whole flash was backed up just now](screenshots/dlg-install-mbootloader.png)

The app backs up the whole flash first, then asks. Only the small boot area at the start of the memory is written;
firmware, projects and sounds stay.

### Installing SLOOP 3

![The question before SLOOP 3 is installed through m-bootloader's MIDI MODE, with a bold warning that the old firmware's presets and projects are gone afterwards](screenshots/dlg-install-sloop3.png)

SLOOP 3 (a public beta) goes on through m-bootloader's MIDI MODE. The question says in bold what you lose: SLOOP 3
sets up its own storage, so the old firmware's presets and projects on the FM-1 are gone afterwards – only the
whole-flash backup made just before keeps them.

### Firmware through download mode

![The question before Skiff is installed through download mode on a unit with m-bootloader: what is written, what stays, and what was tested](screenshots/dlg-firmware-download-mode.png)

On an FM-1 with m-bootloader – also one that runs SLOOP 3 – another firmware goes on through USB download mode.
m-bootloader stays in place, and there is no detour through the original firmware.

### "Back to …"

![The Back to dialog: back to a whole-flash backup with a list of three backups, the one from before m-bootloader marked as recommended, back to SLOOP 3, back to a Felucca-family firmware, back to the original V15](screenshots/dlg-back-to.png)

One button for the way back. The app lists this unit's whole-flash backups and marks which are **from before
m-bootloader** and which are **with m-bootloader**; the newest one from before is pre-selected. The other ways
(SLOOP 3, another firmware, the original V15) are offered with their steps – or with the reason why one is not
possible right now. Each way only starts the actions above, each with its own question.

### SLOOP 3's own editor

![Editor page with SLOOP 3's dark web editor: transport, scenes and master controls](screenshots/sloop3-editor.png)

When the app recognises SLOOP 3, the Editor page shows SLOOP 3's own editor, bundled unchanged so it works offline.
(Here with SLOOP 3 3.3.7 against the project's test stand-in, not a real FM-1.)

## 6. Studio twins

### Studio

![Studio page: a drawn FM-1 with a live display showing four tracks, playback buttons and a project list](screenshots/studio.png)

The Studio runs your firmware's own code on the computer – a twin of your FM-1, loaded with the device's state from
a backup. Play it on screen, with the computer keyboard or a MIDI keyboard, and hear what the FM-1 would play.

### Another firmware, another twin

![Studio page with the SLOOP twin: the same drawn FM-1 with SLOOP's display](screenshots/studio-sloop.png)

Plug in an FM-1 with another firmware and the Studio starts that firmware's twin – here SLOOP 2.8.5, fetched from
demo mode's second device.

### Thirteen firmwares

![Six Studio twins side by side with their different displays: Melodee, SLOOP, Skiff, KARMA, JIANT FM and X0X](screenshots/studio-twins.png)

Six of the thirteen twins side by side: each shows its own firmware's display. (Melodee and SLOOP were fetched from
demo devices, the other four started empty. Next to a real FM-1 only the Melodee twin has been used so far, and the
Skiff twin once.)

### Export as WAV

![Export as WAV dialog: length, format, single tracks, folder and file name](screenshots/studio-export.png)

Render a song from the twin to a WAV file – faster than real time, optionally every track as its own file.

## 7. MIDI

### Keyboard

![MIDI page, Keyboard tab: an on-screen piano with three keys pressed, channel and velocity settings](screenshots/midi-keyboard.png)

Play the FM-1 from the screen or the computer keyboard, with pitch bend, modulation and sustain. The MIDI page also
has a clock, the firmware's parameters as sliders, connections between MIDI devices and a sound module for Melodee's
MIDI tracks.

### Monitor

![MIDI page, Monitor tab: a list of note-on and note-off messages with time, direction, raw data and meaning](screenshots/midi-monitor.png)

Every MIDI message between the app and the FM-1, in plain words next to the raw numbers – useful when something
does not react as expected. The list can be filtered and exported.

## 8. Recovery

![Recovery page: stage 1 update mode and stage 2 ROM boot mode, each with symptoms, numbered steps and buttons](screenshots/recovery.png)

For the bad day: the FM-1 does not start, or an installation was interrupted. The page goes from the mildest case
(update mode) to the hardest, each stage with its symptoms and steps, and it shows whether it sees the FM-1 in that
state. In demo mode the practice run with a simulated FM-1 is pre-ticked.

## 9. Settings and help

### Settings

![Settings dialog, General tab: update channel, automatic backups, careful USB mode and language](screenshots/settings.png)

A handful of settings in six tabs: updates and automatic backups, appearance, folders, recording, devices, storage.

### Settings › Storage

![Settings, Storage tab: what the app keeps on the computer by category with sizes, and automatic cleanup options](screenshots/storage.png)

What the app itself keeps on your computer – logs, downloaded firmware, caches – with sizes, a delete button per
category and an automatic cleanup. Your own backups, projects, samples and recordings are never deleted from here.

### Help

![Help page: a list of guides on the left and the Getting started guide on the right](screenshots/help.png)

The built-in help works offline: getting started, switching firmware, recovery, backups, samples, MIDI, a glossary
and frequently asked questions.

### System check

![System check dialog: a list of findings with warnings, notes and OK entries, each with a suggested solution](screenshots/system-check.png)

Looks at the computer for things that can disturb the FM-1 – USB trouble, too little power, missing audio parts –
and says how to fix each. (Taken on a test machine without sound and MIDI, hence the two warnings.)
