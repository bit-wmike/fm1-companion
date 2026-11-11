# Backups

A backup contains everything the firmware hands out through its interface: projects, user presets,
settings. It is the same format as with the firmware's web editor.

* **Create:** Ctrl+B or [Backups](app://page/backups) › *Back up now*. Optionally with a note.
* **Restore:** choose a backup › *Restore everything …*. Before that, the app automatically creates a
  safety backup of the current state – so you can always go back.
* **Single projects** can be fetched from the device or from a backup on the [Projects](app://page/projects) page.
* **Where are they?** In the main folder (default `~/FM-1/Backups/<device name>/`). Now and then, also copy this
  folder to a USB stick or to the cloud.
* Automatic backups (before updates) are cleaned up after the configured number; manual ones are always kept.

## Which firmwares can be backed up?

Backups go through the firmware's editor protocol. The app speaks three backup protocols: **Felucca's**
(Felucca, Melodee, Skiff, JIANT FM, CoralFlower, ChoralRoot and the Felucca forks that keep it), **SLOOP's**
(SLOOP, KARMA, ORBIT and SLOOP's forks: sloopDX, Clipper, Jangada, winampFM, …) and **X0X's** (from X0X 1.0.9);
M-VAVE's FM1 Open has its own.

* A firmware that names itself when asked ("FELUCCA CLIPPER 1.3.1") gets its own rules. One that answers like
  Felucca ("FELUCCA v1.0") is **asked** which backup protocol it speaks – only with read-only requests; the app
  never writes with a protocol it guessed.
* A firmware with a backup protocol of its own (Optimist, Hortator) or without any gets a clear message instead
  of a timeout: back it up with its own editor.
* A backup only goes back onto a firmware that keeps the same things under the same objects. A sloopDX backup on
  SLOOP, a Saadet backup on Felucca would land in the wrong places – the app refuses before anything is written.
* The full list of the catalog's firmwares and what the app does with each is in `docs/firmware-support.md`.
