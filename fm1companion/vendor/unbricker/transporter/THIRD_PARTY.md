# Licences of the prebuilt Transporter firmware

`fm1_transporter-a632d92-placeholder.uf2` is object code. It contains:

| component | licence | text |
| --- | --- | --- |
| [FM-1 Transporter](https://github.com/kurogedelic/FM-1-transporter) @ `a632d923203170e3a08565f4056dbb3fd3d65ca5` | MIT, Copyright (c) 2026 kurogedelic (Leo Kuroshita / Hügelton Instruments) | `LICENSE.FM-1-Transporter` |
| [Raspberry Pi Pico SDK](https://github.com/raspberrypi/pico-sdk) 2.2.0 | BSD-3-Clause, Copyright (c) 2020 Raspberry Pi (Trading) Ltd. | `LICENSE.pico-sdk` |
| [TinyUSB](https://github.com/hathach/tinyusb) (the version pinned by pico-sdk 2.2.0) | MIT, Copyright (c) Ha Thach (tinyusb.org) | `LICENSE.tinyusb` |
| [Pico-PIO-USB](https://github.com/sekigon-gonnoc/Pico-PIO-USB) (submodule of the Transporter, patched as in its CMakeLists.txt) | MIT, Copyright (c) 2021 sekigon-gonnoc | `LICENSE.Pico-PIO-USB` |

The C runtime comes from the Arm GNU Toolchain 14.2.Rel1 (newlib/libgcc, whose licences permit
redistribution of linked binaries without further conditions).

JieLi's `wl82loader.bin` is **not** contained: the file holds a placeholder pattern instead, which
`fm1_transporter_recover.py setup` replaces on the user's machine (see README.md).

These licence texts were added by FM-1 Companion (the upstream unbricker repository ships the `.uf2`
without them); the texts were taken from the projects' repositories at the versions named above
(TinyUSB and Pico-PIO-USB: current default branch, same licence and copyright holder).
