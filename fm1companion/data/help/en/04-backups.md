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
