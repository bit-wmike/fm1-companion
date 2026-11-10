# Felucca licensing

Felucca is free software. Its **code** is licensed under the GNU General Public License,
version 3 only (`GPL-3.0-only`, full text in `LICENSE`). Its **assets** are not part of
that licence: the icon atlas `assets/icons.png`, the panel image `docs/panel.jpg` and the drum sounds made by
`tools/gen_waves.py` (the Hügelton Sample Pack) are Copyright (C) 2026 Hügelton Instruments,
all rights reserved. Their licence terms will be published later. SLOOP's firmware does not contain the
Hügelton Sample Pack: its sampled drum kit is made of CC0 recordings (`assets/samples-cc0/KIT`), and
`gen_waves.py` only feeds the SLICE engine's demo loop, which SLOOP does not build.

Copyright (C) 2026 Leo Kuroshita (@kurogedelic), Hügelton Instruments

## What is code (GPL-3.0-only)

Every file in this tree that carries an `SPDX-License-Identifier: GPL-3.0-only` header (one source file
carries another: `firmware/src/fm6_core.c`, msfa, Apache-2.0, below; the firmware built with it is
GPL-3.0-only as a whole):

- the firmware: `firmware/` (app, HAL, update loader)
- the build script and tools: `build.sh`, `tools/`
- the web pages (installer, editor) and their tests: `web/` (not the Fukiai font, below)
- the host tests: `tests/`

You may use, study, change and share it under the GPL. If you distribute Felucca, or
firmware derived from it, you must also give your recipients its complete corresponding
source under the same licence. That includes devices that ship with modified Felucca
inside.

## Additional permission (GPL-3.0 section 7)

As an additional permission under GPL-3.0 section 7, you may combine Felucca, or a work
based on it, with the Felucca Assets (above), and convey the combination.
This is allowed even though the Felucca Assets are not licensed under the GPL, provided
that:

- you follow the GPL for every part that is not a Felucca Asset; and
- you follow the terms published for the assets.

The Felucca Assets are data (wavetables, icons, sample data). They are not program
code. A firmware image built from the GPL sources with replacement assets, or with no
assets, is entirely governed by the GPL.

## Third-party material

| What | Licence | Where |
| --- | --- | --- |
| Instrument and drum samples (Versilian Studios VSCO-2 CE, VCSL; Sonic Pi: SCRCH) | CC0 1.0 | `assets/samples-cc0/`, provenance in `ATTRIBUTION.txt` there |
| Terminus font 8x16: the FM-1's screen (Terminus Bold, ter-u16b, SLOOP 2.6), and the web editor's font (the regular ter-u16n; `tools/gen_webfont.py` makes it a TrueType font inlined in `web/editor.html`) | SIL OFL 1.1 | `assets/fonts/ter-u16b.bdf`, `assets/fonts/ter-u16n.bdf`, `assets/fonts/Terminus-LICENSE.txt` |
| Fukiai icon font (Hügelton Instruments), web editor only | MIT | `web/fukiai.ttf`, `web/FUKIAI-LICENSE.txt` |
| CrispyZebra by Leo Kuroshita (<https://github.com/hugelton/CrispyZebra>): the PHASE engine's waveforms are a C port of its oscillator | GPL-3.0 | `firmware/src/eng_phase.c` |
| klattsch by Tony Gies (<https://github.com/tgies/klattsch>): design reference for the VOICE (formant) engine; no code copied. Formant data from Klatt (1980) / Hillenbrand et al. (1995) | MIT (klattsch) | credit only |
| msfa by Google Inc. and Pascal Gauthier, from Dexed (<https://github.com/asb2m10/dexed>): the FM6 engine's synthesis (SLOOP 2.4), ported to integer C for Felucca 1.0 by Leo Kuroshita (Dexed itself is GPL-3.0; only msfa is used; the FM6 factory patches are Felucca's own) | Apache-2.0 | `firmware/src/fm6_core.c`, `LICENSES/Apache-2.0-msfa.txt` |
| DaisySP (Electrosmith, Emilie Gillet) and Rings' sympathetic strings (Emilie Gillet): the PHYS engine's models (SLOOP 2.5, from Felucca 1.0, ported to fixed point by Leo Kuroshita) | MIT | `firmware/src/phys_dsp.c`, `firmware/src/phys_symp.c` (the notice is in each file) |
| Plaits by Emilie Gillet, with her stmlib (<https://github.com/pichenettes/eurorack>): 19 of its models in her own C++, unchanged (2026-10-20: VA, VCF, phase distortion, FM, six-op FM and its three patch banks, grain, wave terrain, string machine, chiptune, string, modal, noise, speech, particle, the three drums), and the tables they use | MIT | `firmware/mi/` (`plaits/`, `stmlib/`; `plaits/resources.cc` cut down by `tools/gen_plaits_resources.py`); `firmware/mi/plx.cc` is this firmware's way in (GPL-3.0); `firmware/cxx/` holds the few standard templates the sources use |
| Elements by Emilie Gillet (same repository): its Ominous Voice (the module's hidden mode) and the envelope and tables it uses, in her own C++, unchanged | MIT | `firmware/mi/elements/` (`resources.cc` cut down by `tools/gen_plaits_resources.py`); `firmware/mi/elx.cc` is this firmware's way in (GPL-3.0) |
| Plaits by Emilie Gillet (<https://github.com/pichenettes/eurorack>): the PRISM engine's models and their lookup tables, ported to fixed point. Not affiliated with or endorsed by Mutable Instruments (a registered trademark) | MIT | `firmware/src/prism_dsp.c`, `firmware/src/prism_tables.h`, `LICENSES/MIT-plaits.txt`; the float originals are built for comparison by `tests/prism_ref/` |
| Elements by Emilie Gillet (same repository): its exciters (the particles and plectrum strikes, the bow with its banded waveguides, the tube and diffuser of blow), ported to fixed point onto the PHYS engine's modal bank. Not affiliated with or endorsed by Mutable Instruments | MIT | `firmware/src/strike_dsp.c`, `firmware/src/bow_dsp.c` (the notice is in each file); the float originals are built for comparison by `tests/strike_ref/`, `tests/bow_ref/` |
| Grids by Emilie Gillet (same repository): its 25 drum maps and the way they are read (blending the four nodes around MAP X / Y, a step playing when its level beats the density, randomness, accents over 192), for SEQ > DRUM GEN. Not affiliated with or endorsed by Mutable Instruments | GPL-3.0-or-later | `firmware/src/grids_maps.h` (the maps, unchanged), `dg_level` / `dg_hit` in `firmware/src/gen_seq.c` |
| Clouds by Emilie Gillet (same repository): its granular player (the grains, their scheduling, windows and levelling) and mu-law coding, ported to fixed point for the CLOUD send effect. Not affiliated with or endorsed by Mutable Instruments | MIT | `firmware/src/cloud_dsp.c` (the notice is in the file), the mu-law routines in `firmware/src/cloud.c`; the float original is built for comparison by `tests/cloud_ref/` |
| X0X by Charles Vestal (<https://github.com/charlesvestal/fm1-x0x>, a Felucca fork): its TB-303 engine (a port of Open303 by Robin Schmidt, MIT, with schwung-303's Devilfish ranges and drive, GPL-3.0; the RAT model from davemollen/dm-Rat, GPL-3.0) and its TB-3PO line generator (from schwung-tb3po / the Phazerville Hemisphere Suite, GPL-3.0) | GPL-3.0 (Open303: MIT) | `firmware/src/acid303.c` (copied, prefixed), `firmware/src/acid_gen.c` (adapted to SLOOP's steps); `tests/acid_gen_test.c` compares with X0X's generator |
| FM-106 by PatttF (<https://github.com/PatttF/fm-106>, after ultramaster KR-106 by Kay Rock Screenprinting): its Juno-106 DCO (PolyBLEP saw, pulse, sub, noise), IR3109 / BA662 ladder filter and MN3009 BBD chorus (left channel), and their tables. Not affiliated with or endorsed by Roland | GPL-3.0 | `firmware/src/juno_dsp.c` (adapted: the filter coefficients once a block, the chorus mono), `firmware/src/juno_tables.h` (unchanged); `tests/juno_test.c` compares with FM-106 sample for sample |
| LXR-02 firmware by Erica Synths (its source, released under GPL-3.0-only with additional terms under its section 7: their notice is `LICENSES/LXR-NOTICE.md`, copied unchanged): the drum voices of its sound engine (the drum, snare, cymbal and hi-hat voices, their oscillators, transient generator, envelopes, resonant filter, distortion and decimator, and the parameters' curves), ported to KARMA's drum track; two of its band-limited wavetables (the saw and the square below 440 Hz). KARMA is a modified version, not Erica Synths' firmware, and is not affiliated with, approved or endorsed by Erica Synths; "Erica Synths", "LXR" and "LXR-02" are their names, used here only to say where the code comes from | GPL-3.0-only (+ their section 7 terms) | `firmware/src/lxr_dsp.c` (Portions Copyright (C) 2026 Ariel Muchnik: what was adapted is listed at its top), `firmware/src/lxr_waves.h` (made by `tools/gen_lxr_tables.py`); played by `firmware/src/hex_drums.c` (KARMA's own: the kits); the originals are built for comparison by `tests/lxr_ref/` (`tests/lxr_test.c`) |
| The LXR-02 firmware's transient samples (Erica Synths, from its `transientTables.c`, which credits them: signed 8 bit pcm, "4102__patchen__atik-2-10-stereoatik"; and per sample: js_expClick, js_click2, 82279__kevoy__acoustic-kick, altemark_rs03 rimshot, pj cohen pedal chic, Shiftr 808 clap, md 16 17, md 16 36): nine of its twelve, trimmed | GPL-3.0-only (+ their section 7 terms), as their firmware | `firmware/src/lxr_transients.h` (made by `tools/gen_lxr_tables.py`, the origins listed at its top) |
| Melodee by Kerem Kilic (<https://github.com/keremimo/melodee>, a Felucca fork; its dual-core code also used by FM-106): the second core's audio worker (one job at a time, a check word against misread requests, retirement to one core) and CPU1's start, stacks, hold and fault handling | GPL-3.0-only | `firmware/src/audio_worker.h` (unchanged), `firmware/hal/fm1_multicore.h` (adapted: no test hooks, KARMA's RAM layout), the CPU1 parts of `firmware/hal/fm1_irq.h`, `fm1_sys.h`, `fm1_vec.S`, `firmware/crt0.S` |
| JIANT FM by Juan Ignacio Antonucci (Juan Jiant) (<https://github.com/juanjiant-bit/JIANT>, a Felucca fork): the harmony of its song generator (one key and one plan of chords that every generated part reads, each part in a role) and its pitch helpers, after `firmware/src/gen.c`; the USB STEMS mode (each part's stem taken as it is mixed, the FX return, the stems ring and packets, the squeezed frame after an underrun, EP4 queued at every TIMER5 tick) and the audio half acknowledged before its render, after its `usb.c`, `fx.c`, `audio.c` | GPL-3.0-only | `firmware/src/gen_seq.c`, `firmware/src/usb.c`, `firmware/src/fx.c` (stem_take), `firmware/src/audio.c` (Portions Copyright (C) 2026 Juan Ignacio Antonucci) |
| FM1 Open by M-VAVE (<https://github.com/m-vave/fm1_open>, application code GPL-3.0 with an SDK linking exception; its binary SDK is not used here): the FM-1 display panel's settings (porch, gate, VCOM, power, gamma, brightness) and the RX-clocked audio interrupt option, taken from its board code to match the official hardware setup | GPL-3.0 | `firmware/src/lcd.c` LCD_SEQ, `firmware/hal/fm1_audio.h` FM1_AUDIO_RX_CLOCK |
| JieLi AC79 SDK: `uboot.boot`, `cfg_tool.bin`, `eq_cfg_hw.bin` are read from your SDK checkout at build time and placed in the package; no SDK files are in this tree | Apache-2.0 | <https://gitee.com/Jieli-Tech/fw-AC79_AIoT_SDK> |

## Contributions

Contributions are welcome under GPL-3.0-only. By submitting one, you agree that it may be
combined with the Felucca Assets under the section 7 permission above.

## Trademarks

"Felucca" and "Hügelton Instruments" are names of Hügelton Instruments.

"Erica Synths", "LXR" and "LXR-02" are names of Erica Synths. KARMA's drum voices are ported from their open-source
LXR firmware (above); KARMA is not affiliated with, approved or endorsed by Erica Synths.

"M-VAVE" and "FM-1" are trademarks of their respective owners. Felucca is independent
firmware that runs on FM-1 hardware. It is not affiliated with, endorsed by or supported
by those owners.

## Radio

Felucca never enables the Bluetooth / Wi-Fi radio of the hardware.
