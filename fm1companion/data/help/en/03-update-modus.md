# Update mode & recovery

If the FM-1 stays dark or does not start properly after an interrupted update, **update mode** almost always
helps.

## Starting update mode

1. Switch the FM-1 off.
2. **Hold down OCT−** (only this key) while switching it on.
3. **Three short flashes** of the key lighting mean: the FM-1 is in update mode.
4. Connect USB. In the app the device appears with the addition *(update mode)*.
5. In [Firmware+ › Install & Versions](app://page/firmware) install the firmware again.

Melodee from 1.3.5 on with boot protection returns to update mode by itself after an interrupted write (also
a power cut) – just switch it on again and reinstall.

## After an interrupted installation

After an interruption the app's message says what is *likely* – it has not looked at the FM-1 again. The
[Recovery](app://page/recovery) page shows what is detected live:

* **An FM-1 in update mode is detected:** *Finish installation* writes the interrupted package again. The app knows
  it from its journal and checks that it belongs to the waiting update loader – otherwise nothing is sent.
* **Nothing is detected and the FM-1 runs normally:** nothing was written. *Forget the interrupted installation*
  removes the app's note.
* **Nothing is detected and the screen stays black:** switch the FM-1 off and on once and try update mode (above).
  If that does not work, see the next section.

Honestly: finishing an interrupted installation has only been run in the simulation so far, not on a real FM-1.

## When nothing works at all

If the computer shows a drive **“WL82 UBOOT1.00”** and the display stays black, the chip is in its
built-in recovery mode. Then the [Recovery](app://page/recovery) page, **Stage 2**, helps: it guides
you step by step through the bundled MvaveFM1Unbricker (with a mandatory double backup of the flash).
Background is in the section “Firmware update and recovery” in
[awesome-fm-1](https://github.com/cicloid/awesome-fm-1).

What Stage 2 checks before writing – on the backup that was read in the **same** run:

* **“Write back original V15”** only writes under the factory boot header (V13–V15). If the backup shows the
  boot header of V16 (M-VAVE's updater rewrites it when installing V16) or an unknown header
  (e.g. m-bootloader), **nothing** is written: whether V15 starts under it has not been established, and the V16 app
  (0x04000–0xA8FFF) cannot be written by the built-in unbricker. In that case use
  [M-Flasher](https://github.com/jvitkauskas/m-flasher) or `fm1_rescue.py` from
  [felucca-bsp](https://github.com/hugelton/felucca-bsp), which chooses the package to match the boot header.
* **“Repair settings”** resets 0x93000–0xE8FFF and 0xFC000–0xFEFFF. These are pure data only with the
  **V15 layout**. With V16, 0x93000–0xA8FFF still holds firmware (end of the program, factory voices) – in that case,
  and with an unknown or damaged layout, nothing is written.
* A session in ROM boot mode usually lasts for only **one tool run**. After that – also after a refusal – switch the
  FM-1 off and on and bring it into ROM boot mode again.

## The bundled tools: M-Flasher, m-bootloader, SLOOP 3

The app brings **M-Flasher** 0.12.1 (for 64-bit Raspberry Pi OS and x86-64 PCs; there is no build for the 32-bit
system), **m-bootloader** 2.4 and **SLOOP 3** 3.4.0 with it, so they are there without internet. Recovery ›
**Tools** shows each one: the bundled version, the version in use, whether its checksum is right (the app checks it
before every use and never runs a changed file), and when it last looked for a new version.

* **New versions:** by default the app asks GitHub once a day (Settings › General: daily, weekly, only at the start
  or never; only while the firmware update check is on). A new version is only shown. **Update** downloads it,
  checks it against GitHub's checksum and keeps your current version as the fallback until the new one has worked
  once. A new M-Flasher runs on this computer only for reading; one whose commands have changed is kept but not
  used.
* **Download mode without a password:** the installer sets up a rule that lets you reach the FM-1 in ROM download
  mode (USB 4c4a:8057) and loads the Linux driver `sg` at start – `./install.sh` does it by default
  (`--no-download-mode-rule` leaves it out; if it cannot get administrator rights it prints the exact commands).
* **Back up the whole flash** (Recovery › Tools, or Backups › Flash backups): the app puts the FM-1 into USB download
  mode – by itself while it runs on USB MIDI (original firmware, Felucca family, SLOOP 3, m-bootloader's MIDI MODE),
  otherwise it tells you how: with m-bootloader hold **PLAY** while switching on and choose **DOWNLOAD MODE**, or
  hold **REC** while switching on (goes there directly). Then M-Flasher reads the whole flash (1 MiB) **twice**;
  each read is checked against the CRC-32 the flash chip computes, and only when both reads are identical one copy
  is kept in `Backups/<device>/Flash/`, with a description: the unit ID, the flash chip, the SHA-256, the M-Flasher
  version, the firmware the app knew and the date. Nothing is written to the FM-1. When the app sent the FM-1 into
  download mode, it restarts it afterwards. The unit ID and flash chip M-Flasher reads are kept with the device
  profile (only for the unit the app itself sent into download mode, and only if no other profile has that ID).
* **Write flash back** (“Write flash back …” on Recovery › Tools, “Write back …” on Backups › Flash backups): only
  onto the FM-1 the copy came from. The app reads the connected unit first: its **unit ID and flash chip must equal
  the copy's** – a copy of another unit or chip is refused, there is no way around it. Then it backs up the current
  state (read twice, as above; without that backup nothing is written) and asks you once more, naming the unit, its
  revision, the copy's date and firmware: **the whole flash goes back, the boot area (bootloader) included**, and
  with it the firmware, projects, sounds and settings of that day. Right before writing, M-Flasher shows the unit
  ID, the flash chip and the sectors it will write and waits for “yes”; the app answers only when all of it matches
  its own check, otherwise M-Flasher writes nothing. Afterwards M-Flasher's own check (the chip's CRC-32) and a fresh
  read must equal the copy; then the FM-1 is restarted. Once writing has started it cannot be cancelled – do not
  unplug the FM-1 then; even if the app is closed or crashes, M-Flasher finishes the write, and at the next start the app says what happened and asks what to do. A copy whose factory data (calibration, Bluetooth address) differs from the unit's is refused. If something goes wrong, the FM-1 stays in download mode and the backup made just before
  goes back the same way.
* **Into download mode / Restart the FM-1** (Recovery › Tools, and the Device menu): the same switch on its own,
  without reading anything – by the app only to an FM-1 it recognised itself at its USB port, otherwise it tells you
  how. “Restart the FM-1” starts the firmware again from download mode. Neither writes anything.
* **Install m-bootloader** (Recovery › Tools): writes the m-bootloader 2.4 the app brings (its checksum checked) into
  the **boot area 0x00000–0x03FFF only**. First the whole flash is backed up (read twice, no way to skip it), then the
  app asks you once more, naming the unit and its revision. On a newer unit (logo on the front) only an m-bootloader
  version that is known to run there is offered (2.4-395e6cc, from one owner's report). Right before writing, the app
  answers M-Flasher's question only when every sector it plans lies inside the boot area; afterwards a fresh read must
  hold exactly this bootloader and nothing outside the boot area may have changed. The app remembers that this FM-1
  has m-bootloader. Then: hold **PLAY** while switching on for its menu.
* **Remove m-bootloader (original boot area)**: puts back the boot area of M-VAVE's original firmware V15 – the app
  downloads the V15 package once if it is not on this computer and checks it. The same backup first, the same checks;
  only the sectors where your boot area differs from V15's are written. This also undoes what FM1 Open changed in the
  boot area. On an FM-1 that came with V16 it is V15's boot area, not necessarily the one it came with. SLOOP 3 is
  installed only through m-bootloader: whether it starts afterwards is not known – the backup made just before is the
  way back. **Only on an earlier unit** (no logo on the front): V15's boot area has never been written onto a newer
  one, so there the app refuses and names the way back instead – writing back a whole-flash backup of this unit made
  before m-bootloader (it offers it when there is one). “Restart the FM-1” is refused while a write did not end
  verified (the FM-1 then stays in download mode, where its backup can be written back).
* **Import a flash backup …** (Backups › Flash backups, Recovery › Tools): takes a whole-flash copy that M-Flasher
  made itself (its `.bin` with its `.json`, kept elsewhere) into the app's backups – checked against its `.json`,
  filed under the device whose unit ID the `.json` names (the image itself does not carry it; if no profile is known
  as that unit you choose one, nothing is guessed). An imported copy with an intact boot area from before
  m-bootloader is the way back the app offers on a newer unit. Install and remove m-bootloader are also in the
  **Device** menu.
* **Why the original boot area matters:** many Felucca-family firmwares refuse the original V15 when the boot area is
  not V15's (“FAILED -5” on the FM-1, CHECK HEAD). When the app knows that your FM-1 has m-bootloader or FM1 Open's
  boot area (from a flash backup, from M-Flasher, from m-bootloader itself or from the firmware history), a plan that
  would hit this (V15 first, or V15 itself from a Felucca-family firmware – and after FM1 Open any Felucca-family
  firmware) stops and asks you to put back the original boot area first. An update of the same firmware on an FM-1
  with m-bootloader still works; the plan warns that the later way back needs the original boot area.
* **Honestly – what ran on real hardware (2026-11-20, one Raspberry Pi 5, two units):** the backup on both units (by
  the app's own switch from Melodee and from SLOOP 3, with REC at power-on, through m-bootloader's menu), each copy
  identical with one M-Flasher made by hand; writing back only on an earlier unit and only small changes (one
  sector, verified), the refusal of another unit's copy, and closing the app being blocked while it writes. Not yet
  run: writing back on a newer unit (logo on the front), a large change or the boot area, a real logout during the
  write. Installing and removing m-bootloader and “Into download mode” have not been run through the app on a real
  FM-1 yet (the owner did the same with M-Flasher by hand). Firmware through download mode and SLOOP 3 through the
  app come in later versions.

## If the FM-1 does not appear as “WL82 UBOOT1.00” at all

The chip only shows the drive once it has dropped into ROM boot mode. In this order (following the
troubleshooting of MvaveFM1Unbricker), with the FM-1 connected directly to the computer:

1. **Leave it switched on for two minutes.** Felucca firmwares have a watchdog and a start guard: after two
   failed starts in a row the FM-1 switches into ROM boot mode by itself. Then check again.
2. Hold **OCT− and OCT+** together, switch on and keep holding for 10 seconds (update mode, if the firmware gets
   that far; on Melodee this is the calibration).
3. **Three quick restarts:** on, wait 10 s, off; on, 10 s, off; on. If every start crashes, the
   third one ends up in ROM boot mode. (This is also how an FM-1 can get there by accident.)
4. If none of this helps, only hardware remains: [Recovery](app://page/recovery), **Stage 3** (FM-1 Transporter).

## Typical causes of interruptions

* Loose or too long USB cable, USB hub without its own power supply
* Undervoltage on the Raspberry Pi – check this with the [System check](app://action/syscheck)
* Another program (browser editor, DAW) talks to the FM-1 at the same time
