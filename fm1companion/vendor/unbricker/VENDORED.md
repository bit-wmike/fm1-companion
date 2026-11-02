# Vendored: MvaveFM1Unbricker

* Source: https://github.com/Quixotic7/MvaveFM1Unbricker, commit 707470a (2026-10-17), tool version 1.2.0
* Licence: MIT, Copyright (c) 2026 Michael P Jones (see LICENSE) – compatible with FM-1 Companion's GPL-3.0-only.
* Files are copied **unchanged** (fm1_unbrick.py, fm1_transporter_recover.py, guides, tests, transporter/).
  FM-1 Companion drives them as a subprocess (`fm1companion/flash/uboot.py`) so their safety rules stay
  exactly as upstream wrote and tested them.
* jl-uboot-tool (kagaimiq, MIT) is NOT vendored: `fm1_unbrick.py setup` fetches the pinned commit adb3f18 and
  checks the WL82 loader blob's SHA-256 (d41da612…) – only after the user agrees in the app.
* Added by FM-1 Companion (not upstream): licence texts for the prebuilt Transporter firmware in
  `transporter/` (`THIRD_PARTY.md`, `LICENSE.FM-1-Transporter` MIT, `LICENSE.pico-sdk` BSD-3-Clause,
  `LICENSE.tinyusb` MIT, `LICENSE.Pico-PIO-USB` MIT).
