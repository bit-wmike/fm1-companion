# V16 – note

M-VAVE offers the factory firmware **V16** (file `FM-1.fwsc`, identity `FM-1_016`) for download: eight voices,
64-step patterns, 128 factory voices in DX7 format. In the app, V16 is **experimental – only with a backup**.

## The essentials in three sentences

* V16 lays out the FM-1's memory **differently** from V15 and all community firmwares (Melodee, SLOOP, Skiff, JIANT).
  When switching, songs and presets on the device are lost – that is why a backup is mandatory.
* From Melodee, V16 only goes **via the original V15**: Melodee checks the boot header first and would otherwise reject V16 with
  “FAILED -5”. The app does this intermediate step automatically.
* The **way back** (V16 → V15 → Melodee) has **not yet been confirmed** with a real V16. Therefore: do not try it on your
  main FM-1, but – if you have one – on a second one that can be out of action for a few days.

## What is certain and what is not

For every statement, the app says where it comes from:

| Statement | Source |
|---|---|
| When installing, V16 writes 0x04000–0xA8FFF, V15 and Melodee only up to 0x92FFF. | from the packages; for V15 recorded on real hardware, for V16 derived |
| The update loader is byte-identical in V13, V14, V15 and V16. | from the packages |
| During a normal update to V15, the boot header (0x0–0x3FFF) was not requested. | recording on real hardware – the header in the device and in the package were identical |
| Whether the update loader writes the header along with V16 (different boot header). | **open** – Melodee's source code assumes it does, the recording cannot rule it out (dump A ↔ B settles it) |
| An FM-1 that reported itself as version 16 could be reset to V15 with a normal update. | recording – but it was a V15 derivative, not a real V16 |
| On first start, V16 formats 0xBA000–0xC9FFF, 0xDA000–0xE2FFF and 0xED000–0xEFFFF. | only observed in the emulator |
| Leftovers of V16 in 0x93000–0xA8FFF do not disturb Melodee. | in the emulator with Melodee 1.5.1; for 1.6.0-rc.7 only from the source code (sectors without a valid Melodee record count as free and are erased before writing), not on the device |
| Whether the loader erases everything before writing or sector by sector. | open (the structure of the loader suggests erasing beforehand) |

## How it works in the app

* **There:** [Firmware+ › Install & Versions](app://page/firmware) › “Back to the original …” › V16. The plan shows: backup → original V15
  → V16 → check identity. While the FM-1 is writing, it shows in red **“Do not unplug and do not switch off now.”**, and cancelling is then
  locked. At the end the app says what it has checked (checksum, identity `FM-1_016`) and what it has not (every byte –
  that is only possible with a dump).
* **Back:** if V16 is running, Firmware+ › “Install & Versions” shows “Way back from V16 …”. The app first installs V15, then your
  target firmware and, for Melodee, afterwards font and factory sounds (resources). Then restore your backup.
* **Check:** [Recovery](app://page/recovery) › “Is my FM-1 clean?”.

## Is my FM-1 clean?

* **Quick check (read only, no risk):** identity, version and, for Melodee from 1.6 on, the state of the resources –
  the firmware recalculates header and checksum itself. It does **not** look into the rest of the memory.
* **Dump check:** a complete backup (1 MiB) is compared sector by sector with all known packages:
  boot header, firmware, a truncated V16, leftovers of V16, Melodee resources. Result as a traffic light.
  You get the dump in ROM boot mode (“Read & check in ROM boot mode …”, read only) or as a file.
* **Clean up V16 leftovers** (expert mode of the card, ROM boot mode, **untested on a real device**): only erases
  4 KiB sectors in 0x93000–0xA8FFF that, according to a freshly read double backup, still contain exactly V16 – and
  only if the flash holds, byte for byte, a known firmware with V15 layout (never with V16). Procedure: first read and
  plan (nothing is erased), then, after your confirmation (“CLEAN UP”), back up again, erase sector by sector
  and verify, and at the end compare the whole flash. If something is interrupted, a journal remembers the state; the same button
  resumes. It is assumed (not checked) that the chip erases exactly 4 KiB on the “erase sector” command – the
  app then checks every 64 KiB block and stops immediately if more is missing. According to everything known so far
  (emulator, source code), the leftovers are harmless: leaving them in place is just as fine.
  For practice: tick “Simulation” in Stage 2 – then everything runs against the unbricker's practice flash.

## Test plan for your first real V16 test (preferably with an expendable FM-1)

Four complete backups, each read in ROM boot mode and checked with “Is my FM-1 clean?”:

1. **Dump A** – before V16 (Melodee running, backup made).
2. **Dump B** – directly after the V16 update. Normally V16 starts immediately; then B also shows the
   first-start formatting. Compare A ↔ B: was it really written up to 0xA8FFF, did the boot header stay the same
   (or does it contain the header from V16 afterwards)?
   What changed at 0xE0000–0xE4FFF (loader buffer)?
3. **Dump C** – after the way back to V15. Are the V16 leftovers still there?
4. **Dump D** – after Melodee. Does Melodee 1.6.0-rc.7 run with the leftovers? Are all sounds there?
5. Optional **Dump E** – after “Clean up V16 leftovers”: are only the leftover sectors empty, everything else the same as D?

Please do **not** try deliberately interrupted updates – not even on an expendable FM-1.

## If the way back does not work

* First the normal **update mode** (hold OCT− while switching on, see “Update mode & recovery”).
* If the FM-1 no longer accepts anything there or stays dark: [Recovery › Stage 2](app://page/recovery)
  (ROM boot mode “WL82 UBOOT1.00” with the built-in unbricker). It writes the original V15 and refuses
  to write V16 (it only writes up to 0x92FFF – V16 would be truncated afterwards). We have not yet tried it with
  V16 on the device.
* If, after V16, the **boot header of V16** is in the device (the backup shows it), Stage 2 does **not** write V15:
  whether V15 starts under this header is open. In that case use [M-Flasher](https://github.com/jvitkauskas/m-flasher) or
  `fm1_rescue.py` from [felucca-bsp](https://github.com/hugelton/felucca-bsp) (chooses V15 or V16 to match
  the header). “Repair settings” is also locked with the V16 layout – there, 0x93000–0xA8FFF still holds
  firmware.
