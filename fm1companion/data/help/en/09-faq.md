# Frequently asked questions

**The app does not find my FM-1.**
Use a USB data cable, switch the FM-1 on, press F5. If that does not help: [System check](app://action/syscheck) –
it shows whether the computer sees the device at all.

**The FM-1 keeps disconnecting.**
Almost always the cable, hub or power supply. The system check detects such loops in the kernel log and tells you
what to do.

**May I do something else during an update?**
Yes, but nothing that talks to the FM-1 (browser editor, DAW), and do not unplug USB.

**Does the app collect data?**
No. No telemetry, no accounts. The app only fetches two things automatically (can be switched off): when an
FM-1 is plugged in, the version list of its firmware on GitHub, and when the catalog is opened, the catalog of awesome-fm-1
including preview images. Everything else only when you click. Bug reports are only saved locally.
To switch off: Settings › “look for firmware updates when an FM-1 is plugged in (asks GitHub)” and in the catalog under “More”
(“Update the catalog automatically when opening”, “Load preview images from the internet”).

**Where is my data?**
Everything that belongs to you is in the main folder (default `~/FM-1`). It is **never** deleted on uninstall.

**How do I delete old logs and downloaded firmware?**
Tools › *Storage and cleanup …* (or Settings › Storage) lists what the app keeps on the computer – logs,
downloaded firmware packages, copies of installed packages, firmware tools, editor copies, catalog images, sample
pack downloads, web cache, temporary files and error reports – with their size, *Open folder* and *Delete …* (always
with a question first). Your backups, projects, samples, kits and recordings are never deleted there, and neither is
anything an interrupted installation still needs (the package of the journal, the firmware tools, the newest original
firmware V15 for a rescue without internet). While an installation runs, nothing is deleted. Studio states are your
work: they are only deleted one at a time (*Delete a state …*).

The **automatic cleanup** (same tab, on by default, at most once a day) deletes older log parts after 30 days, keeps
the 3 newest package copies and the 2 newest
editor versions per firmware, and deletes catalog images, sample pack downloads and temporary files after 30 days.
The [System check](app://action/syscheck) shows the total.

Firmware downloads in `~/FM-1/Firmware` are never deleted automatically (that folder is yours); you can switch on
“keep the newest N downloads per firmware” in the same tab.

**Something crashed.**
The app then shows a dialog with *Save bug report*. The report contains logs, versions and
settings without personal paths – you decide whether to pass it on. To create one yourself: Help ›
Create bug report.

**How do I get the old look back?**
View › Theme › *System style (KDE/Breeze)*. Icons: Settings › Appearance › *System theme (Breeze/Papirus)*.
