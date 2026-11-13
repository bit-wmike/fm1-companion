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
