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

**Something crashed.**
The app then shows a dialog with *Save bug report*. The report contains logs, versions and
settings without personal paths – you decide whether to pass it on. To create one yourself: Help ›
Create bug report.

**How do I get the old look back?**
View › Theme › *System style (KDE/Breeze)*. Icons: Settings › Appearance › *System theme (Breeze/Papirus)*.
