# Melodee 1.6.0

Melodee 1.6.0 is the final release (2 November 2026) after eight pre-releases (1.6.0-rc.1 to rc.8). What changes
for you in the suite and what to watch out for when updating.

## Update in three steps

1. **Make a backup while the old firmware is still running.** The app makes one by itself before the update – if you come from
   Melodee 1.5.1 or older, it is mandatory. **This backup is your way back:** older firmware cannot read
   what 1.6 saves, and rejects backups from 1.6.
2. In [Firmware+ › Install & Versions](app://page/firmware) choose *Melodee* and *v1.6.0* and install.
   The plan shows the notes on 1.6 (backup, different volumes).
3. **Factory sounds:** the update loader of 1.6.0 writes the font, factory sounds and Bluetooth program along by itself. The
   app only checks afterwards. If they are already there, it writes nothing and reports: *“Resources already complete”*.
   From 1.5.1 or older this does not work the first time, because your preset banks are still stored there. The new
   firmware moves them on start, after that the app writes the factory sounds itself. Unlike with the Firmware Hub,
   you do **not have to install twice**. If the FM-1 still shows **“Setup unfinished”**: *Rewrite font & factory
   sounds* (same page) or install the same version once more.

## Projects may sound different

* **CZ-1 tracks are 6 dB quieter** than before.
* The **limiter** only kicks in at −2 dBFS instead of −5.2 dBFS. Loud mixes keep more peak level (loudest point
  about −3.5 instead of −5.9 dBFS).
* **TPDF dither** everywhere, and MASTER acts once at the end on the finished mix.

So check the balance of your projects after the update. This also applies if you come from 1.6.0-rc.8.

## Recording via USB

* The **stereo recording is now taken before MASTER**, like the four single tracks. The volume control on the FM-1 no longer changes
  the recording level; a low setting no longer means a quiet recording. You can see the levels on the
  [Recording](app://page/recorder) page.
* You set the sound **from the computer to the FM-1** with **USB VOLUME** in the HOME menu (default 50 % = the old level with
  MASTER in the middle). MASTER no longer controls it.

## New in 1.6 – and how the suite handles it

| New in Melodee 1.6 | In the suite |
|---|---|
| Song with 64 sections, rests per track, “Loop song” | [Project View](app://page/project_view) shows all 64; MIDI export follows the song |
| Tracks 1–4 with 16 patterns (5–8 keep 8), 32 project slots | Project View, Projects, Backups, Compose know all 16 and 32 |
| Timed punch effects (4 per section, 27 effects) | Shown in the song table (read only). Sound exact only in the [Studio](app://page/studio) and on the FM-1, not in the MIDI export |
| Presets per song block and per pattern | Shown in the song table and the pattern overview (read only). The block preset is played first, then the pattern preset, then the track's sound. Exact only in the Studio |
| KIT CR78, KIT FM, range editing, MIDI LEARN, Bluetooth MIDI | on the device. MIDI LEARN only applies until switching off |

You edit punch effects and song presets on the device. The web editor still shows only
8 patterns in its pattern view and only addresses slots 1–4 with its project command. If it replaces a whole arrangement, that
deletes punches and presets. Backups keep them.

## Studio

For 1.6.0 and 1.6.1 there is a separate [Studio twin](help:13-studio) each. For devices and backups with
1.6.0-rc.7 or rc.8, their twins stay available. The studio takes the version of your FM-1 or backup. If it differs, it tells you.
Between rc.x and 1.6.0 the difference is the volume (see above).

## Back to an older version

* The installation plan warns when you switch from Melodee 1.6 to an older Melodee or another firmware.
  You have to confirm explicitly.
* The app does **not restore a backup from 1.6 onto an older Melodee**. It tells you why beforehand instead of
  showing a device error. Use the backup from before the update to 1.6.
* Returning to the original firmware erases the resource area. On the way back to Melodee the app writes it again.

## FM-1 with UCUN flash

Some FM-1s have a flash chip from UCUN (UC25HQ80, ID 0xB36014). Only from 1.6.0 on does Melodee save
correctly on it. The **USB ROM recovery (Recovery Stage 2) does not know this flash yet** and stops without
writing. The app then tells you so. Normal installations via Firmware+ are not affected.

## Melodee 1.6.1: clock and sync

Melodee 1.6.1 (3 November 2026) is a normal update from 1.6.0: projects, presets, settings and backups
stay the same, 1.6.0 can still read them. The installation plan therefore shows no additional warnings. If you come
from a pre-release or from 1.5.1, everything above applies (backup, volumes). What changes for you:

* **The FM-1 can now set the tempo.** If GLO › GLOBAL › CLK is set to **INT**, it sends MIDI clock via USB,
  USB HOST and Bluetooth, also while stopped, plus Start on PLAY and Stop on STOP. A DAW or a drum machine can
  follow. On **USB, TRS or BLE** the FM-1 follows and sends no clock. No clock goes out via TRS.
* **Who leads?** If the suite is to set the tempo ([MIDI › Clock](app://page/midi)), set CLK on the FM-1 to **USB**. On
  INT the FM-1 does not listen to the app's clock and sends its own, then two clocks run. The page
  reminds you of this. If the FM-1 is to lead, set CLK to **INT** and leave the app's clock off. The *Clock* tab then shows
  the FM-1's tempo under “Incoming clock (follower)”.
* **Song from a section.** In the [Project View](app://page/project_view), “Start the song on the FM-1 itself”
  now also starts from a chosen section, like PLAY at the SONG cursor. With CLK INT, devices running along
  then get **Song Position and Continue** and join at the same place. Song Position has no pre-roll: devices
  that first have to seek may miss the first pulses.
* **Song Position from the app.** With CLK USB the FM-1 follows a Song Position. The suite's clock can
  therefore start “from bar” (Song Position + Continue). The FM-1 sets every pattern to where it would be when playing from
  the start. It does not play its own SONG sections in the process.
* **Accurate clock.** INT now runs on exact note values and no longer drifts. Synced LFOs lock onto
  the song.
* **Loading a project keeps CLK.** CLK is now a device setting: loading a project or a template (also
  from the suite, from Projects or the Studio) no longer changes it. TUNE, SYSTEM MIDI and ROUT still come from
  the project. If a setup used to rely on a project's CLK: set CLK once on the FM-1.
* **Restoring a backup restores CLK**, as it was at the time of the backup. Check CLK afterwards if you had
  changed it in the meantime.

The Studio twin 1.6.1 calculates with the same clock as the device, but does not send any clock out itself.
Melodee has so far only checked clock output and Song Position with automated tests, not yet with real
devices.
