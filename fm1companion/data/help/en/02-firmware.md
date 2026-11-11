# Switching firmware & back to the original

The FM-1 can run different firmwares, e.g. **Melodee**, **Skiff**, **SLOOP** or the official
firmware from M-VAVE. Everything for this is on **one** page: [Firmware+](app://page/discover) (sidebar ›
*Data & Tools*).

* **All firmwares** – the catalog: search, look at, try out in the simulator, *Add to my firmwares*.
* **Install & Versions** – [this is where](app://page/firmware) you install, update and switch: choose versions
  of a firmware, *Download only* several versions (also for a firmware that is not on the device yet) and
  then jump between them, *Install own .fwsc file …*, *Add firmware …* (link or repo),
  *Back to the original …* and the way back from V16 via V15.
* **News** and **Tools & knowledge** – new versions of the firmwares you follow, and useful links.

Whether there is an update for your FM-1 is also shown on the [Overview](app://page/overview) (*Firmware & Updates*).

## How to switch

1. **Make a backup** (Ctrl+B). Before every update the app also makes one by itself (can be switched off in the
   settings) and remembers which firmware it belongs to.
2. In [Firmware+ › Install & Versions](app://page/firmware) choose the firmware and version, then *Install on active
   FM-1*.
3. **Do not disconnect power or USB** until the app reports “done”. Close other programs that send MIDI.
4. If you later return to a firmware, the app offers you the matching backup.

## Back to the original firmware

* Melodee comes with a way **back to the official firmware** (V15); a backup is made first.
* The official files and updaters are available from M-VAVE: <https://www.m-vave.com/download>.
* The current state of the official firmware and alternative updaters (also for Linux) are collected by
  [awesome-fm-1](https://github.com/cicloid/awesome-fm-1).

## Good to know

* Projects and presets of one firmware do not automatically fit another. That is why: backups per firmware.
* Melodee 1.6 comes with a resource file (font, factory sounds). From 1.6.0 on, the update loader writes it
  along by itself; if it is still missing (e.g. on the first update from 1.5.1), the app writes it afterwards.
* Update to **Melodee 1.6.0**: your backup is your way back, projects may sound different – see
  [Melodee 1.6.0](help:14-melodee-1-6-0).
* If something goes wrong: [Update mode & recovery](help:03-update-modus).

## USB hubs

With a good cable directly at the computer, installing is easiest. Through a **USB hub** the connection more
often drops for a moment, or the FM-1 takes longer to come back after its restarts – then an installation starts
but does not finish. The app therefore detects a hub in front of the FM-1 and switches to **careful USB mode** by
itself (shown in the installation plan):

* it waits longer for the update loader and for the restart, answers the FM-1's requests more slowly and keeps
  only one answer on its way at a time;
* if the connection drops while writing, it waits for **the same** FM-1 in update mode and starts the write again
  (up to 3 times) – only with the loader that belongs to the package, exactly like *Finish installation* on the
  [Recovery](app://page/recovery) page. All safety checks stay the same.

Settings › *Careful USB mode for flashing*: automatic (default), always or never.

If it still does not work:

1. Connect the FM-1 **directly** to the computer with a short data cable – on a Raspberry Pi preferably to a
   **black USB 2 port**. If you need a hub: one with its **own power supply**, without other power-hungry devices.
2. The [system check](app://action/syscheck) shows how the FM-1 is connected (hubs, speed, USB 2/3) and whether **USB
   power saving** (autosuspend) is on for the FM-1 or its hub. The optional **udev rule** of the app switches it
   off: `./install.sh --usb-power-rule` (or the checkbox in the graphical installer), or by hand
   `sudo install -m 0644 packaging/udev/60-fm1-companion.rules /etc/udev/rules.d/` and
   `sudo udevadm control --reload-rules`. The Debian package ships it in `/usr/share/fm1-companion/udev/`.
3. Every installation writes a **flash log** (every request of the FM-1, repeated requests, dropouts, kernel
   messages of that time). It is part of the error report ([Help › Create bug report](app://action/report)) – please attach it when
   you report a problem.
