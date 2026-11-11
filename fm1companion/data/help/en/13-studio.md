# Studio

The FM-1 is the instrument for sketching, the suite is the studio. On the [Studio](app://page/studio) page
a **digital twin** of your FM-1 runs: the same firmware as on the device (Melodee, Skiff, JIANT FM, SLOOP,
CoralFlower, KARMA or M-VAVE's FM1 Open), with its complete state (Melodee: all 32 projects; Skiff: projects A–D and the pattern bank;
JIANT FM: every song's sections and the song index; SLOOP: the sections A–D, the FM6 bank, the SYN drum kits and the
sample slots; CoralFlower: the sequencer's patterns and song with their sounds; KARMA: the song's parts A–D, the song
order, the HEX drum kits and the sample slots; FM1 Open: its 16 projects, the DX7 library and the sample bank; running
music, user presets, settings). It sounds identical – just through the computer's speakers. (FM1 Open is the exception:
see “The original firmware and FM1 Open” below.)

## How it works: FM-1 → Studio → WAV or back

1. **Fetch from FM-1:** reads a complete backup from the active FM-1 (its name is on the button) and loads it into
   the twin. The backup is also stored in [Backups](app://page/backups) – nothing is lost. Nothing is changed
   on the FM-1 (playback stops briefly).
   Alternatively **Open from backup …** (any Melodee, Skiff, JIANT FM, SLOOP, CoralFlower, KARMA or FM1 Open backup –
   the twin of the backup's firmware starts; SLOOP and KARMA backups in the format of SLOOP's web editor too) or
   **Open last studio state**.
2. **Play and refine:** the FM-1 on the screen responds to the mouse, the computer keyboard (bottom row
   Z S X D C …, top row Q 2 W 3 E …), a MIDI keyboard (choice “MIDI input”) and, if you like, the keys of the
   real FM-1. Controls: drag, mouse wheel or arrow keys. **Space** starts/stops. You load projects (Melodee 1–32,
   Skiff A–D, JIANT FM: the current song's sections A–D, SLOOP: its sections A–D, KARMA: the song's parts A–D, FM1 Open
   1–16) from the list on the right.
3. **Export as WAV …:** the whole song to the end, a fixed number of seconds or passes of the pattern.
   The default is WAV 24 bit (44.1 kHz stereo), optionally 32 bit floating point, saved in the folder
   “FM-1 Studio” in your music folder, file name with project name and date. **Single tracks (stems):** one
   separate file per track, as long as the mix – for further work in Logic or Audacity. Each track is rendered on
   its own, with its share of the shared effects (reverb, delay, master); the sum of the stems is therefore
   not bit-identical to the mix.
4. **Write back to the FM-1 …:** asks first, names the device, automatically makes a
   **safety backup** beforehand and then writes projects, running music and presets. The device's settings
   stay as they are unless you tick the box. Only works with full access (Melodee, Skiff, JIANT FM, SLOOP, CoralFlower,
   KARMA or FM1 Open connected – the same firmware as the twin's) and never on the twin itself. SLOOP only writes while its song
   is stopped (STOP).

## Studio state and undo

The twin remembers everything an FM-1 still has after switching off (the flash), and the app saves this
automatically – per device, as soon as something has changed and when you leave the page. **Remember intermediate state**
adds a point to the list, **Undo** (Ctrl+Z) and **Redo** (Ctrl+Shift+Z) jump
between the states (up to 30). As on the device: whatever has not yet been saved in the running project (SAVE)
is not part of the state.

## Versions

The twin exists in several versions (Melodee 1.6.0-rc.7 … 1.6.1, Skiff 2.1.1 and 2.2, JIANT FM V10.6z2 – the stable
version of JIANT's installer – plus V10.60 and V10.61, SLOOP 2.8.5 – the version of SLOOP's installer – and 2.8.4,
CoralFlower 0.7.1 – its installer's – and 0.7, KARMA 3.1 – the version its installer serves – plus 3.0.5, FM1 Open
V14). The app
chooses the firmware
and version of your FM-1 or of the backup. If the version does not match exactly, a note at the top says: “The studio
runs with Melodee …, your FM-1 has … – sound may differ.”
Writing back to an FM-1 with a different version is then only possible with the expert box ticked.

## The original firmware and FM1 Open

Most FM-1 run M-VAVE's **original firmware V15** (or the newer V16). Its code is not open, so there is no twin of
it. M-VAVE has published an open firmware, **FM1 Open** (V14, github.com/m-vave/fm1_open): a new firmware of its own –
8 tracks, 16 projects, 8 sample pads, its own operation – that keeps the **128 factory DX7 voices of V15** in their
order (taken from the V15 package) and the update protocol. Its sound engine on the device is a closed binary; M-VAVE's
browser simulator runs an open engine instead, and the studio's FM1 Open twin is exactly that simulator code.

* **FM-1 with FM1 Open:** the twin is its own firmware (V14). Fetch, write back, backups (in their own format: the
  working project, the 16 projects, the four DX7 library banks, the sample bank) work over FM1 Open's own file
  protocol. To read a project, the FM-1 loads it briefly; afterwards it shows the project of before again, marked as
  changed. Projects that are empty in the backup stay as they are on the FM-1 (FM1 Open cannot delete a slot over
  USB). The sound follows M-VAVE's simulator and can differ slightly from the device.
* **FM-1 with the original V15/V16:** the studio starts the FM1 Open twin instead, with a note: the same factory
  voices, but a different firmware – operation, projects, effects and sound may differ (V16 also corrected the
  detune). The original firmware has **no backup protocol**: nothing can be fetched from it or written back (a full
  dump in [Recovery](app://page/recovery) is the only backup). What the app can do for V15 is M-VAVE's own voice
  restore: on [FM Voices](app://page/voices), **Original V15: restore factory voices …** overwrites all 128
  voices in the banks A–D with the factory voices (from M-VAVE's checksum-pinned V15 package). The voices on the FM-1
  before **cannot be saved first** – voices you stored there yourself are lost. Writing your own voices into a V15 bank
  is not offered (it would need a hardware test first).

## The twin as a device

**Use as device** (or “Studio twin … virtual” in the device selection at the top) makes the twin the
active device: [Projects](app://page/projects), [Compose](app://page/compose), Presets, Backups and the Editor
then work with it. It is always marked as “virtual”; installing firmware, recovery, USB audio and
automatic backups are not available for it. To go back: choose your FM-1 at the top.

When playing in Projects and Compose, the target without an FM-1 is called **“in the studio (identical sound)”**. The
old approximation on the computer only remains when there is no twin for the firmware.

## Limits

* So far **Melodee**, **Skiff**, **JIANT FM**, **SLOOP**, **CoralFlower**, **KARMA** and **FM1 Open** – which also
  stands in for the original firmware V15/V16 (see above).
* FM1 Open: the twin has no Bluetooth, no USB audio, no TRS MIDI and no updater; its open engine stands in for the
  device's. The project view does not read FM1 Open's projects.
* Skiff: the whole song to the end ends only with SONG ONCE (otherwise Skiff loops); the Skiff twin has one core
  (MENU › CPU 2 CORES has no effect), no USB audio and no TRS MIDI. The project view does not read Skiff's songs yet.
* JIANT FM: the stems of tracks 5 and 6 stay silent while MENU › TRACKS is 4 (JIANT's default: T5 and T6 asleep). The
  twin has one core, no Bluetooth (PROX has no signal), no USB host and no USB audio (the INPUT engine hears silence).
  A JIANT version without a twin of its own (e.g. V10.6c … V10.6z10) runs on the next older twin, with a warning. From
  V10.6p JIANT has 6 songs: songs 7 and 8 of an older backup are left out (the studio names them).
  The project view does not read JIANT's songs yet.
* SLOOP: “Whole song to the end” plays SLOOP's song order (SAVE layer › SONG MODE; the order is part of the settings)
  – if it names an empty section, the loop plays instead (until the export's limit, as with a song set to LOOP). The
  twin has one core (MENU › 2ND CORE has no effect), no USB audio, no TRS MIDI and no USB rescue. An older SLOOP (e.g.
  2.8.3) runs on the 2.8.4 twin, with a warning. The project view does not read SLOOP's sections yet (SLOOP's web
  editor shows them).
* CoralFlower: no project slots – its eight patterns and the song play from the twin's panel (SEQ, PLAY) or with Play;
  music is made on the panel (CoralFlower has no editor). The four stems are its parts: chords, bass, layer, drums (a
  stem carries neither the metronome's click nor the power-on sound; the mix has both, as the device plays them).
  After loading a backup and after writing back, CoralFlower restarts (the twin as well as the FM-1) – that is how it
  reads what was restored. The twin has one core, no USB audio and no TRS MIDI; the update gesture (OCT− + OCT+ + HOME
  held) does nothing there. The project view does not read CoralFlower's patterns.
* KARMA: the twin has one core (as KARMA's own single-core build), no USB audio (MENU › USB AUDIO =
  STEMS has no effect), no TRS MIDI and no USB rescue. KARMA keeps its backups in SLOOP's format; a SLOOP backup (up to
  2.6) can be written to a KARMA FM-1 (SLOOP 2.5's SYN kits are left out, as KARMA's editor does), a KARMA backup
  never to SLOOP. KARMA's three saved songs (SAVE on the SONG screen) are not part of a backup – KARMA's own backup
  leaves them out too. The project view does not read KARMA's parts yet.
* The twin has no CPU limit: the device drops voices when overloaded, the twin never does. Floating-point parts
  can differ in the last bits (inaudible). No Bluetooth, no USB host, no firmware update in the
  twin.
* The sound goes through the system's default output. If a specific output is chosen under “Output”,
  the app moves the studio sound there (needs `pactl`, package pulseaudio-utils).
* The twin's clock runs while the page is visible or something is playing; otherwise it rests (saves computing time).
