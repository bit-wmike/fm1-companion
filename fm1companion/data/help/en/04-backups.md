# Backups

A backup contains everything the firmware hands out through its interface: projects, user presets,
settings. It is the same format as with the firmware's web editor.

* **Create:** Ctrl+B or [Backups](app://page/backups) › *Back up now*. Optionally with a note.
* **Restore:** choose a backup › *Restore everything …*. Before that, the app automatically creates a
  safety backup of the current state – so you can always go back.
* **Single projects** can be fetched from the device or from a backup on the [Projects](app://page/projects) page.
* **Where are they?** In the main folder (default `~/FM-1/Backups/<device name>/`). Now and then, also copy this
  folder to a USB stick or to the cloud.
* Automatic backups (on connect, before updates) are cleaned up after the configured number; manual ones are
  always kept.
* Before a restore writes anything, the app asks the FM-1 whether it accepts each part. A problem the FM-1 only
  finds while writing, or a pulled cable, stops the restore in the middle – the message then says which parts
  were already written, and the safety backup still holds the state from before.

## Automatic backups and playback

Reading a backup **briefly stops playback on the FM-1** – the firmware does that itself. By default the app makes
an automatic backup once a day, about 20 seconds after you connect the FM-1. It announces it first:
*Automatic backup of … starts in N s – playback on the FM-1 stops briefly*, with two choices:

* **Skip today** – no automatic backup of this device until tomorrow. A manual backup (Ctrl+B) is always possible.
* **Back up now** – do not wait for the countdown.

The app cannot see whether the FM-1 is playing, so it cannot wait for a quiet moment by itself. If you never want
this, switch off *on connect* under [Backups](app://page/backups) › *Automatic backups*.

## Which firmwares can be backed up?

Backups go through the firmware's editor protocol. The app speaks three backup protocols: **Felucca's**
(Felucca, Melodee, Skiff, JIANT FM, CoralFlower, ChoralRoot and the Felucca forks that keep it), **SLOOP's**
(SLOOP, KARMA, ORBIT and SLOOP's forks: sloopDX, Clipper, Jangada, winampFM, …) and **X0X's** (from X0X 1.0.9);
M-VAVE's FM1 Open has its own.

* A firmware that names itself when asked ("FELUCCA CLIPPER 1.3.1") gets its own rules. One that answers like
  Felucca ("FELUCCA v1.0") is **asked** which backup protocol it speaks – only with requests that change nothing
  of yours (the FM-1 stops playback for them); the app never writes with a protocol it guessed.
* A firmware with a backup protocol of its own (Optimist, Hortator) or without any gets a clear message instead
  of a timeout: back it up with its own editor.
* A backup only goes back onto a firmware that keeps the same things under the same objects. A sloopDX backup on
  SLOOP, a Saadet backup on Felucca would land in the wrong places – the app refuses before anything is written.
* The full list of the catalog's firmwares and what the app does with each is in `docs/firmware-support.md`.
