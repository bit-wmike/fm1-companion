# MIDI & sound module

The FM-1 is a USB MIDI device. The suite uses this in several places:

* **MIDI tools** (page *MIDI*): connect ports, monitor, send clock.
* **Sound module:** Melodee's MIDI tracks 5–8 produce no sound on the FM-1 itself. A sound module on
  the computer (e.g. `fluidsynth` with the General MIDI soundfont) plays them audibly.
  Packages: `sudo apt install fluidsynth fluid-soundfont-gm`.
* **MIDI file → pattern:** [Compose › From MIDI File](app://page/workshop) turns a MIDI file into a pattern for a track.

If the app finds no MIDI, check the “MIDI” line in the [System check](app://action/syscheck) – usually
`python3-rtmidi` is missing.

## Listening to projects and patterns

In **Projects › View & Export** and in **Compose** there is a playback bar (Space starts and stops):

* **On the FM-1:** the notes go via MIDI to the active FM-1, and it plays them with **the sounds currently
  set on its tracks** – exactly as it sounds. For this the app reads the setting
  *GLO › SYSTEM › ROUT* (which MIDI channel plays which track). Nothing is saved in the process. If a track
  is armed for recording (REC), playback does not start, so that the FM-1 does not record anything.
  If the sounds in the project differ from those on the device, the app tells you.
* **Start the song on the FM-1 itself:** for the running music that was just read, the FM-1 plays its song itself.
* **On the computer (approximation):** without an FM-1 the app renders a preview. FM6 tracks sound with the real
  patch from the project, other engines only similar.

**No sound?** With the **“Output: …”** button you choose where the sound goes on the computer. If the FM-1 is
connected as a USB audio device, it often becomes the default output – then every sound ends up on the FM-1 instead
of your speakers. The [System check](app://action/syscheck) shows in the “Preview” line where the sound is currently going.

**Undo:** in Compose, **Ctrl+Z** undoes every change, **Ctrl+Shift+Z** or **Ctrl+Y**
redoes it. “Undo on the FM-1” is something else: it writes the state from before the last write
back to the device.
