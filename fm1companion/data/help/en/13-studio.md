# Studio

The FM-1 is the instrument for sketching, the suite is the studio. On the [Studio](app://page/studio) page
a **digital twin** of your FM-1 runs: the same firmware as on the device (Melodee, Skiff, JIANT FM or SLOOP), with its
complete state (Melodee: all 32 projects; Skiff: projects A–D and the pattern bank; JIANT FM: every song's sections and
the song index; SLOOP: the sections A–D, the FM6 bank, the SYN drum kits and the sample slots; running music, user
presets, settings). It sounds identical – just through the computer's speakers.

## How it works: FM-1 → Studio → WAV or back

1. **Fetch from FM-1:** reads a complete backup from the active FM-1 (its name is on the button) and loads it into
   the twin. The backup is also stored in [Backups](app://page/backups) – nothing is lost. Nothing is changed
   on the FM-1 (playback stops briefly).
   Alternatively **Open from backup …** (any Melodee, Skiff, JIANT FM or SLOOP backup – the twin of the backup's
   firmware starts; SLOOP backups in the format of SLOOP's web editor too) or
   **Open last studio state**.
2. **Play and refine:** the FM-1 on the screen responds to the mouse, the computer keyboard (bottom row
   Z S X D C …, top row Q 2 W 3 E …), a MIDI keyboard (choice “MIDI input”) and, if you like, the keys of the
   real FM-1. Controls: drag, mouse wheel or arrow keys. **Space** starts/stops. You load projects (Melodee 1–32,
   Skiff A–D, JIANT FM: the current song's sections A–D, SLOOP: its sections A–D) from the list on the right.
3. **Export as WAV …:** the whole song to the end, a fixed number of seconds or passes of the pattern.
   The default is WAV 24 bit (44.1 kHz stereo), optionally 32 bit floating point, saved in the folder
   “FM-1 Studio” in your music folder, file name with project name and date. **Single tracks (stems):** one
   separate file per track, as long as the mix – for further work in Logic or Audacity. Each track is rendered on
   its own, with its share of the shared effects (reverb, delay, master); the sum of the stems is therefore
   not bit-identical to the mix.
4. **Write back to the FM-1 …:** asks first, names the device, automatically makes a
   **safety backup** beforehand and then writes projects, running music and presets. The device's settings
   stay as they are unless you tick the box. Only works with full access (Melodee, Skiff, JIANT FM or SLOOP connected –
   the same firmware as the twin's) and never on the twin itself. SLOOP only writes while its song is stopped (STOP).

## Studio state and undo

The twin remembers everything an FM-1 still has after switching off (the flash), and the app saves this
automatically – per device, as soon as something has changed and when you leave the page. **Remember intermediate state**
adds a point to the list, **Undo** (Ctrl+Z) and **Redo** (Ctrl+Shift+Z) jump
between the states (up to 30). As on the device: whatever has not yet been saved in the running project (SAVE)
is not part of the state.

## Versions

The twin exists in several versions (Melodee 1.6.0-rc.7 … 1.6.1, Skiff 2.1.1 and 2.2, JIANT FM V10.6z2 – the stable
version of JIANT's installer – plus V10.60 and V10.61, SLOOP 2.8.5 – the version of SLOOP's installer – and 2.8.4). The app chooses the firmware
and version of your FM-1 or of the backup. If the version does not match exactly, a note at the top says: “The studio
runs with Melodee …, your FM-1 has … – sound may differ.”
Writing back to an FM-1 with a different version is then only possible with the expert box ticked.

## The twin as a device

**Use as device** (or “Studio twin … virtual” in the device selection at the top) makes the twin the
active device: [Projects](app://page/projects), [Compose](app://page/compose), Presets, Backups and the Editor
then work with it. It is always marked as “virtual”; installing firmware, recovery, USB audio and
automatic backups are not available for it. To go back: choose your FM-1 at the top.

When playing in Projects and Compose, the target without an FM-1 is called **“in the studio (identical sound)”**. The
old approximation on the computer only remains when there is no twin for the firmware.

## Limits

* So far **Melodee**, **Skiff**, **JIANT FM** and **SLOOP**. The original firmware (via FM1 Open) will follow later.
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
* The twin has no CPU limit: the device drops voices when overloaded, the twin never does. Floating-point parts
  can differ in the last bits (inaudible). No Bluetooth, no USB host, no firmware update in the
  twin.
* The sound goes through the system's default output. If a specific output is chosen under “Output”,
  the app moves the studio sound there (needs `pactl`, package pulseaudio-utils).
* The twin's clock runs while the page is visible or something is playing; otherwise it rests (saves computing time).
