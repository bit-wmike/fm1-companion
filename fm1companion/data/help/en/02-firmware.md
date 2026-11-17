# Switching firmware & back to the original

The FM-1 can run different firmwares, e.g. **Melodee**, **Skiff**, **SLOOP**, **KARMA** or the official
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

## Switching always goes via the original V15

When you **switch** to another firmware, the app first installs M-VAVE's unmodified **original firmware V15**
(checksum-checked), then the new firmware – two installations in one plan. The reason: firmware authors expect a
start from the original V15, and a direct switch from another custom firmware has bricked a device at least once.
The installation plan says so: “Switching from X to Y: the app installs the original firmware V15 first, then Y”.

What you should know about the V15 step:

* The original firmware has **no start protection** – keep power and USB connected while it is written.
* Saved data of the old firmware **can remain** in the flash (the V15 step only writes the firmware area). The
  original firmware may read it as its own: presets can look garbled until the target firmware is installed.
* This two-step path **has not been run on a real FM-1 by this app yet** – only in the simulation. Run on a real
  FM-1 so far: an update within Melodee. The plan repeats this for every installation.
* The original V15 (≈ 0.9 MB) is downloaded when it is first needed; the plan says whether it is already on this
  computer. *Download the original V15 for offline use* in [Firmware+ › Install & Versions](app://page/firmware)
  fetches it beforehand.
* If the running firmware refuses the original V15 (the FM-1 shows “FAILED -5”), nothing is written. The app then
  says which way is left.

* **Update or downgrade within the same firmware** (e.g. SLOOP 2.8.5 → 2.8.6, KARMA 3.1 → 3.0.5): directly, without
  the V15 step – also when the catalog says the firmware is installed from V15 (the FM-1 already runs it and its
  own update loader). A fork is a firmware of its own – SLOOP → KARMA is a switch.
* **Firmware not recognised** (the app cannot tell what runs on the FM-1): counts as a switch, so via V15.
* **The FM-1 already runs the original V15**, or you go **back to the original V15**: directly.
* **V16**: as before – to V16 from a community firmware and the way back from V16 always go via V15.
* The **backup** is made before the first step, as with every installation.
* If the switch stops after the V15 step (cable, power, cancel), the FM-1 runs the original V15. The app remembers
  the target: [Firmware+ › Install & Versions](app://page/firmware) shows *Continue: install …* – the V15 step is not
  repeated. If it stopped *during* a step, finish it first on the [Recovery](app://page/recovery) page.
* Experts can tick *Expert: install directly without the V15 step* in the plan. It is off by default, is not
  remembered and the plan warns that this is not the start most firmware authors expect. There is no setting
  for it.
* *Cancel* works as long as nothing has been written; with a Felucca-family firmware also while the FM-1 checks
  the package. As soon as the installation can no longer be stopped, the button says so.

## Back to the original firmware

* Melodee comes with a way **back to the official firmware** (V15); a backup is made first.
* The official files and updaters are available from M-VAVE: <https://www.m-vave.com/download>.
* The current state of the official firmware and alternative updaters (also for Linux) are collected by
  [awesome-fm-1](https://github.com/cicloid/awesome-fm-1).

## Newer FM-1 (logo on the front)

FM-1s sold from late 2026 – you recognise them by the **M-VAVE logo printed on the front** – have a different memory
chip inside (UCUN UC25HQ80 instead of the Puya chip). Nothing is wrong with them, but firmware versions written before
that do not know the chip. On such a unit they **save nothing**, and most of them **can no longer be updated or
replaced**: the FM-1 is then stuck on that version until the original firmware is written back in ROM boot mode
([Update mode & recovery](help:03-update-modus), stage 2).

What the app does about it:

* It asks you **once per device**: “Does your FM-1 have the M-VAVE logo on the front?” (Yes / No / I don't know). The
  app cannot read the chip over MIDI. You can change the answer on the pages *Devices* (button *Unit …*) and
  *Overview*.
* On a **newer unit** it does not install a version that does not know the chip – the plan says so and names the
  first version that works. There is no expert switch for this.
* If the app **does not know** which unit you have, such a version is only installed after you confirm in the plan
  that your FM-1 has no logo on the front.
* On an **earlier unit** nothing changes.

First versions that know the newer chip (read in the firmwares' sources, as of 17 November 2026): Melodee 1.6.0,
Skiff 2.2, SLOOP 2.8.5, KARMA 3.0.4, JIANT FM V10.6z, Felucca 1.8.2, ChoralRoot 0.29.1, CoralFlower 0.7, X0X 1.2.1,
FoMni 0.4.1, AMB-1 1.3.1, LOOM-8 0.13, Hortator 1.0.1, TransitFM1 0.1.4. No released version known: ORBIT, gr!mm.
Not proven on this chip according to their authors: m-bootloader (one such unit did not start afterwards) and
SLOOP 3, which needs it. For all other firmwares of the catalog the app does not know – it then treats a newer unit
as “not supported”.

Other ways to find out which chip you have: **M-Flasher** shows it under *Device info* (FLASH CHIP: `0xB36014` is the
newer one, `0x856014` the earlier one), **m-bootloader** shows it in its menu, and the recovery (stage 2) reads it in
ROM boot mode – the app then keeps it for the device by itself.

Honestly: **nothing of this has been run on a newer FM-1 with this app.** The original V15 is the way back that the
authors of Felucca, ChoralRoot, TransitFM1 and the recovery tool name for these units; whether the V16 package works
on them, nobody has written down. The recovery tool bundled with the app (MvaveFM1Unbricker 1.2.1) knows both chips.

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
