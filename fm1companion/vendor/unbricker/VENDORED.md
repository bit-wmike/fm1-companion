# Vendored: MvaveFM1Unbricker

* Source: https://github.com/Quixotic7/MvaveFM1Unbricker, commit a1eaf4d (2026-11-02), tool version 1.2.1
  (before FM-1 Companion 2.6.1: 707470a, 1.2.0. a1eaf4d is the next upstream commit that touches the tools; its only
  change: the flash ID 0xB36014 – the UC25HQ80 of the FM-1s with the logo on the front – is accepted beside 0x856014.
  Upstream's later 1.3.0/1.4.0 – full-restore, macOS – are not taken over: the app's own gates were written and tested
  against the 1.2.x commands.)
* Licence: MIT, Copyright (c) 2026 Michael P Jones (see LICENSE) – compatible with FM-1 Companion's GPL-3.0-only.
* Files are copied **unchanged** (fm1_unbrick.py, fm1_transporter_recover.py, guides, tests, transporter/).
  FM-1 Companion drives them as a subprocess (`fm1companion/flash/uboot.py`) so their safety rules stay
  exactly as upstream wrote and tested them.
* jl-uboot-tool (kagaimiq, MIT) is NOT vendored: `fm1_unbrick.py setup` fetches the pinned commit adb3f18 and
  checks the WL82 loader blob's SHA-256 (d41da612…) – only after the user agrees in the app.
* Added by FM-1 Companion (not upstream): licence texts for the prebuilt Transporter firmware in
  `transporter/` (`THIRD_PARTY.md`, `LICENSE.FM-1-Transporter` MIT, `LICENSE.pico-sdk` BSD-3-Clause,
  `LICENSE.tinyusb` MIT, `LICENSE.Pico-PIO-USB` MIT).
