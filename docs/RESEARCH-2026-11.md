# Research November 2026: community sources for the Studio twin, backup and rescue

As of: 2 November 2026. I read local clones (all `git clone --depth 1`, date = last commit) and ran nothing
except a test build of fm1-emu. Nothing was tested on a device.
Evidence is given as `Repo:File:Line`.
**[V]** = read in the source code or document, **[A]** = statement of the project, not verified,
**[?]** = uncertain or contradictory.

Not reachable (the proxy blocks everything except github.com): *.github.io pages (fm1-emu report as PDF, jielie web
page, installers), thefirmwarehub.com (instead I read the `fm1_rescue.py` linked there in
`hugelton/felucca-bsp/tools/`), reddit, forum.m-vave.com, doc.zh-jieli.com, fm1-editor.com, KVR, Elektronauts,
Gearspace, the article and video pages. Cloning failed (403, probably deleted or private): `jvitkauskas/fm1-bootloader`,
`stanleygurvich/fm1-firmware-database`. I could not check release assets (the GitHub API was blocked in the research
environment).

---

## (a) Summary: the 10 best opportunities

The order follows the benefit for the Studio workflow (backup → twin → edit → render →
write back). Effort: S ≈ 1–2 days, M ≈ 1 week, L ≈ several weeks.

| # | Opportunity | What exactly | Effort |
|---|---|---|---|
| 1 | **One shared state format: the 1 MiB NOR image** | The suite's Melodee source twin already works with a 1 MiB flash (`sims/melodee-studio/1.6.0-rc.8/manifest.json` `"flash_size": 1048576`, `_studio/twin-core.js` `setFlash/getFlash`). fm1-emu takes exactly the same format: `fm1-live --flash FILE` (raw 1 MiB, `crates/fm1-live/src/main.rs:232-239`) and `fm1-emu --raw-flash CHIP.bin`. M-Flasher and the Unbricker write the same format as a full dump. So full dump, twin (both engines) and the way back to the device fit **one** format: dump the device → the twin starts with exactly the device state → change things in the twin → write the image back. Open [?]: whether a real dump (app area encrypted with the chip key) starts unmodified in fm1-emu has not been tested; the user data sectors are unencrypted. | M |
| 2 | **fm1-emu as the second engine, called as a subprocess (`fm1-live`, Linux ARM64)** | MIT licence, according to the README Linux ARM64 builds with an AArch64 JIT (`README.md:202-215`). With `--usb` a Felucca firmware appears as a **virtual ALSA MIDI device** "<product> (emu)", which `fm1_install.py` also works with (`README.md:224-229`). So the suite's editor, backup and installation protocols run **unchanged against the twin**. In addition `FM1_USB_WAV` (record USB audio), `FM1_BENCH_KEYS` (scripted input), `FM1_FLASH_CUT` (power cut at the n-th erase or write). Tempo on the Pi is open (see b1). | M–L |
| 3 | **Backup → twin via the suite's own protocol** | A Felucca backup (`backup.py`) can be played into the twin with PUT (commands 65–67): in fm1-emu via the virtual `--usb` port, in the source twin via the st_* interface (if the glue builds the editor protocol in; FM1VST, for example, removes it, `Quixotic7/FM1VST/README.md:65`). Afterwards the flash is read off: backup file → 1 MiB image, without rescue mode. | S–M |
| 4 | **Full dump as a second backup level via the M-Flasher command line** | `m-flasher info/backup/restore/firmware/download-mode` (`jvitkauskas/m-flasher/docs/usage.md:106-119`), MIT. Knows V16 (reads the areas from the firmware's file table, `src/sectormap.rs:43-104`), keeps factory data (calibration, BT address) even when restoring third-party dumps (`docs/safety.md`), checks via CRC32 on the chip and reads at about 860 KB/s (m-bootloader README). Today the suite only detects M-Flasher and starts it; it would make sense to offer it as a dump tool in the backup assistant. | S |
| 5 | **`fm1_rescue.py` (felucca-bsp, MPL-2.0) as a V16-capable ROM rescue** | Pure Python with pyusb (Debian: `python3-usb`), 444 lines, needs neither a jl-uboot-tool clone nor `sg`. Accepts only V15/V16 by hash and picks **the package whose boot head equals the device's** (`tools/fm1_rescue.py:18-31`). So for V16 it writes the area 0x4000–0xA8FFF, for V15 0x4000–0x92FFF; it never writes the head. The Unbricker can only do V15 (see safety finding S2). MPL-2.0 can be combined with GPL-3.0. | S–M |
| 6 | **Backup for the original firmware (V14/V15/V16) via MIDI, without rescue mode** | `czietz/fm1-read-voice` reads the current sound (VCED) with system command 0x23 (object 5, length 155), according to the README tested on V14/V15 (`fm1_read_voice.py` `CMD_READ = 0x23, SUBCMD_VOICE = 5`). `bratta/fm1-manager` steps through all 128 presets via Program Change and reads out each one (`README.md:33-36`, `src/backup.ts`). V16 additionally has "Bank Export" on the device (32 voices as a DX7 bulk dump, `benny-sparra/.../docs/fm1-research.md` section V16). A lead for later, **do not build in** without a hardware test: according to static analysis, 0x23 on object 5 from offset 0x10000000 on V16 reads the raw memory area with presets, FX library and globe block (`fm1-research.md:2648-2656`). | S–M |
| 7 | **Approximating the original sound: msfa engine of Virtual-FM-1 (GPL-3.0) instead of FM1 Open V14** | `jbschooley/Virtual-FM-1/engines/dx7` (msfa), matched against real hardware: velocity × 100/127, LFO table, detune, pitch (`docs/HARDWARE-NOTES.md`, measured on FM-1+VA 093); plus `engines/fm1_fx`. Important for V16: according to czietz, M-VAVE **corrected the detune calculation in V16** (`czietz/fm1-firmware-patcher/README.md`). So a twin based on FM1 Open V14 sounds different from V16 with detuned operators. Only fm1-emu with the V16 package would be exact. | M |
| 8 | **Safe intermediate step with TransitFM1 instead of V15 across the board** | TransitFM1 (GPL-3.0) identifies as `FM-1_015` and protects the write against interruption up to the last sector. A write V15 → third-party firmware, by contrast, is almost entirely unprotected (`Quixotic7/TransitFM1/README.md:6`, table `:43-50`). The suite sets V15 as the intermediate step by default (`docs/flash-protocol.md` §12) and so enlarges the risk window. In addition there is FM1 Open's loader identity `ota-FM1C_<build>` (`m-vave/fm1_open/sdk/WebUpdate.js:149`), which the suite does not know. | S |
| 9 | **Test environment without a device** | fm1-emu without a window plus `fm1-playwright`/`setup-fm1-emulator` (MIT) for regression tests of installer, backup and restore against real firmware packages, also with a simulated power cut (`FM1_FLASH_CUT`). Today the suite only tests against its own simulators and the Unbricker's mock. | M |
| 10 | **Flash map from the firmware's file table instead of hard-coded areas** | Like `m-flasher/src/sectormap.rs` (VM, BTIF, USRTRIM, USRFLASH, SEQ64, USR, PRCT from the `app_dir_head`) and `fm-static` "Layout Analysis". This makes the dump check robust for V16 and future versions and fixes the false red reports for factory devices (safety finding S3). | S–M |

### Urgent safety findings (short version, details in b5)

* **S1 – "Einstellungen reparieren" (repair settings) has no V16 lock.** **Status: fixed in 2.2.0** (lock for V16
  layout, `uboot.py`/`rescuegate.py`). `ui/pages/recovery.py:498-522` → `flash/uboot.py:294` →
  Unbricker `repair-settings`. Its windows are fixed to the V15 layout: 0x93000–0xE8FFF and 0xFC000–0xFEFFF
  (`vendor/unbricker/fm1_unbrick.py:77-78`). With V16, 0x93000–0xA8FFF still contains **program code** (end of app.bin
  and DX7.bin), 0xBA000–0xDA000 the VM area. A run on a V16 device therefore overwrites the firmware, and the app's
  sentence "Die Firmware selbst wurde nicht verändert" (the firmware itself was not changed, `recovery.py:520-521`) is
  then not true. The Unbricker only warns when Felucca markers are found (`fm1_unbrick.py:1533-1537`); it does not
  lock V16.
* **S2 – The ROM rescue always writes the V15 app, even under a V16 boot head.** **Status: fixed in 2.2.0** (no V15
  write-back with a V16 or unknown head). `fm1_rescue.py:18` says:
  "M-UPGRADE rewrites the flash head when it installs V16". The script therefore picks the package by the head. The
  Unbricker reports a different head only as a hint (`fm1_unbrick.py:1128-1135`) and writes V15 anyway.
  Whether a V15 app starts under the V16 head (larger bootloader, `isd_config` at 0x3938 instead of 0x38D0,
  `m-flasher/docs/development.md:74-76`) is **open [?]**. At the time of this research, the suite's rescue page offered, in this case,
  "Original-V15 zurückschreiben" (write back original V15) without a warning.
* **S3 – The dump check can wrongly call factory devices "beschädigt – bitte neu aufspielen" (damaged – please
  reflash).** **Status: fixed in 2.2.0** (factory/unknown original images are no longer called "damaged").
  `flash/flashmodel.py:727-740`. Factory devices carry a different image in the app area than the public V15 package
  (`fm1_unbrick.py:1530-1532`; measured by maxfridbe: SHA-256 `8c1d13a5…` instead of `6edf3c37…`,
  `maxfridbe/mvave-fm1/skills/mvave-fm1-firmware/SKILL.md:172`). Nothing gets deleted by this, but the advice to
  reflash is an unnecessary risk.

---

## (b) Findings

### b1. Twin: Melodee from source and fm1-emu

**fm1-emu (joakimeriksson, MIT, active: 1 commit, 2026-11-02)**
* According to the project, runs with 81 community firmwares and the original firmware **V13, V15 and V16**
  (`docs/stock-firmware.md:3`, with V16-specific corrections in table rows 48–51), both cores with the
  original firmware.
* **USB:** the controller does not raise interrupts yet. Firmwares that poll (Felucca family, i.e. Melodee,
  SLOOP, Skiff) enumerate; the original firmware works with interrupts and does not enumerate
  (`README.md:228-229`). So a backup of V16 via USB still does not work. This confirms the state of the ROADMAP.
* **State:** `.fm1save` is JSON (`format: "fm1-emulator-save"`, `version`, `hash`, `name`, package as base64,
  list of 4 KiB sectors with offset; `crates/fm1-plugin/src/savefile.rs:10-30`). `fm1-live --flash` stores a
  raw 1 MiB file (atomically via `.tmp` and rename, `main.rs:505-519`). `restore_flash` only lays the differing
  sectors over the installed package (`fm1-soc/src/lib.rs:471-481`).
* **Loading a Melodee backup into fm1-emu:** (1) start the Melodee package with `--usb`, play the suite backup in via
  PUT over the virtual port, save the flash with `--flash`. This is the recommended route, because the format belongs
  to the firmware. (2) Load a full dump with `--raw-flash` or `--flash`; whether that works with the encrypted app
  area is [?].
* **Tempo [A]:** native with AArch64 JIT at 240 MHz Melodee 5.9×, Felucca 4.4×, X0X 2.65× real time (on an
  Apple Mac, `PLAN.md:328-331`); in the browser (wasm) Melodee 2.47× (`PLAN.md:425-427`). There is no measurement for
  a Pi 5 (Cortex-A76). Expectation [?]: Melodee about 1.5–3×.
  **Own test:** `cargo build --release -p fm1-emu` built on x86_64 in 28 s. However, the batch runner did not manage
  10 s of emulated time (V15 and SLOOP 2.8.4 with `--keys` and `--wav`) within 300 s. This path probably does not use
  the JIT; it is not usable as a tempo measurement. **`fm1-live` has to be measured on the Pi.**
* **Sound fidelity [?]:** Melodee and X0X hand audio work to core 1. According to PLAN, core 1 does not start for
  Melodee in the 8 s bench, both then fall back to core 0 (`PLAN.md:320, 1071`). So the voice count under load may
  differ from the device. The project only proves "byte-identical" between JIT and interpreter, not against the
  hardware.
* **Integration:** a subprocess (`fm1-live`, ARM64 release or `cargo build` on the Pi) is the route with the lowest
  risk. Embedding the crates (`fm1-core`, `fm1-soc`) would require a Rust FFI layer. The Debian packages
  Rust/cargo exist on Bookworm/Trixie; `minifb`/`cpal`/`midir` need X11/ALSA development packages [A].
* **Result:** the plan from the ROADMAP holds. Melodee from source is the fast main engine; fm1-emu is the
  second engine for everything else, above all for V16 sound. New: the state of both engines should be the
  1 MiB image (opportunity 1).

**Other engines compared**
* simonjohansson/fm1-emulator (GPL-2.0-or-later, QEMU-based, Linux x86-64 only) and hdavid's fork (Rust,
  interpreter, original firmware idling at only 0.047–0.27× real time on an M4 Max, `hd-fm1-emulator/README.md:75`):
  too slow for the Pi.
* shcherbakov7 (GPL-3.0, Rust/wasm, Russian README): V13–V15 run, Melodee only checked with 0.13.1. No added value
  compared with fm1-emu.
* FM1VST (GPL-3.0): Melodee **1.4.5** from source as an `.fm1core` module with a C ABI, flash as a RAM image;
  editor protocol, OTA and USB audio are removed; modules only for macOS (Mach-O). Useful as a model for the
  source glue, not directly usable on the Pi.
* Virtual-FM-1 (GPL-3.0, Linux x86-64 VST3/LV2): Melodee 1.4.1 and Felucca from source, synchronisation via the
  editor protocol; for the original firmware an msfa engine without read-back. A good reference for the Felucca glue
  and for the original sound.
* felucca-vst (cdodd, GPL-3.0, LV2 Linux x86-64): only Felucca and derivatives with Felucca's browser emulator, no
  Melodee.
* weavejam/Felucca: renders Felucca projects offline as mix + 4 stems (tuttidaw). The idea "stems from the
  project" can be transferred to the suite; the code is Felucca-specific.

### b2. V16

* Memory layout [V, several sources agreeing]: image up to 0xA9000, VM 0xBA000–0xDA000, SEQ64 0xDA000–0xEC000,
  BTIF 0xEC000–0xED000, USER 0xED000 (+0x12000) (`m-flasher/docs/development.md:74-76`,
  `fm1-emu/docs/stock-firmware.md:48`). This matches the suite (`docs/flash-protocol.md` §17.1).
* **Larger bootloader:** `isd_config.ini` is at 0x3938 instead of 0x38D0 (M-Flasher). According to `fm1_rescue.py:18`,
  M-UPGRADE rewrites the boot head when installing V16. So the suite's open question §17.1 point 4 / §17.6(a) is
  **probably answered with "yes, the head is written"** [A, without a recording]. Consequence: see S2.
* Features according to benny-sparra (hardware tests 23–26 Oct): 8 voices with their own channel, 20-voice
  polyphony, an FX library with 128 groups stored separately from the presets, FX CCs 70–93 instead of 0–23, Bank
  Export as a DX7 bulk dump (`fm1-research.md:2195-2300`). For a V16 twin this means: a preset no longer carries the
  effects; state = presets + FX library + globe + voice records + patterns.
* Sound: detune is corrected in V16 (czietz). The FM1 Open V14 twin therefore differs; the note
  "V16 may differ" in ROADMAP step 4 should state this concretely.
* czietz has a patch for V16 (aftertouch off, result SHA `cc825caf…`) and for V15 (detune, aftertouch, colours,
  result `4638959b…`). The suite could add these fixed output hashes as "known variants" to
  `known_packages.json`, so that they do not count as an "unknown file".

### b3. Backup completeness

**Melodee (rc.7 source read):** the backup knows 57 objects (`firmware/src/editor_backup.c:12-14`): the running
music (0), settings including the template (1), 32 project slots (2–5, 64–91) and all 22 preset banks, among them
FM6/Native-FM6, CZ and Prophet banks and the shared extension (`upreset.c:107-115`). The settings contain
the BLE appendix (`storage.c:20`, tag `BLE1`) and the learned key mapping (`panel.c:6-9`).
**Not in the backup:**
* the resource area 0xCB000–0xDFFFF (belongs to the firmware; command 82 restores it),
* unsaved RAM changes (only after SAVE > PROJECT),
* boot head, `key_mac` 0xFF000–0xFFFFF, staging 0xE0000–0xE4FFF,
* anything other firmwares have left in the flash.

So the backup is sufficient for the twin. A full dump is the only **byte-exact** way back.

Caution when copying from device to device [?]: object 1 brings along the source device's BLE bindings and key
mapping.

**Original firmware:** there is no backup protocol. The USR area 0xEA000–0xFC000 contains 128 DX7 VMEM voices from
+0x4000 and 128 slot settings of 59 bytes each from +0x10000; updates never write it
(`fm1-emu/docs/stock-firmware.md:22-26`). The VM contains settings (among others the BT MAC as ID 102 and the
wheel calibration, FM-1-RE `docs/architecture.md` §7). Via MIDI one can currently only get the voices (opportunity
6), not the slot settings and the VM. Only a full dump backs up everything.

**What a full dump adds:** boot head (shows whether V16 changed the head), device-specific data
(`key_mac`, BTIF, on the SMK-37 the calibration at 0xC1000), leftovers of other firmwares, the exact start state
for the twin.

When restoring, factory data must be preserved, as M-Flasher does. For comparison:
`workeffortwaste/fm1-backup-restore` also writes 0xFF000 back according to its tests
(`app/src/test/.../RestoreTest.kt:139,166-173`). Third-party "recovery snapshots" would thus overwrite device data.

### b4. Protocol and format knowledge that is missing from the suite or stated differently

* FM1 Open's loader identity in update mode: `ota-FM1C_<build_id[:16]>` (`fm1_open/sdk/WebUpdate.js:149`; FM1Kit
  README). It is missing from `docs/flash-protocol.md` §4/§5. The recovery would not recognise a waiting Open
  loader.
* System commands of the original firmware (frame `00 59 cmd len24 … ~sum`): FM-1-RE warns that 33–36 and 48 must not
  be tried out (`docs/io/11-ota-protocol.md`, section syscmd). czietz uses 35 = 0x23 as a read command on object 5
  in production. Reading on object 5 thus counts as proven (V14/V15); 34 (write) and 48 remain taboo.
* Partial-block framing: the length must be correct after packing (FM-1-RE `docs/io/12-v15-reflash-proof.md`). The
  vendored `fm1_install.py:104-110` does this correctly (`n + 8`). No bug in the suite.
* `aroum/fm1_sysex_scanner.py` sends `F0 00 32 45 00 00 00 <id> 7F F7` for id 0–127. Unpacked, this is **always
  command 0x11 (identity)** with nonsensical lengths; only id 64 is the valid handshake (own decoding with an
  unpublished helper script). So the script does not scan parameters and is probably harmless [?]. It brings
  nothing.
* `aroum/fm1_flasher.py` uses an invented host-push protocol (commands 0x01–0x04, mismatched checksum). According to
  its own warning it does not work (`REVERSE_ENGINEERING.md:366-369`). Do not use it; only the unpacking of the
  `.fwsc` from the macOS updater is of any use.
* More inaccuracies in aroum's `REVERSE_ENGINEERING.md`: §7.1 "4/8 MB flash" is general AC791N knowledge, the
  FM-1 has 1 MiB (P25Q80H, JEDEC 0x856014, §1.1); §5.2 names "SysEx commands 0x01/0x02" as a way back into the
  bootloader, which is wrong (correct: `F0 22 24 35 7F/7D F7`); §3.1 no. 17 calls the Pico an "SWD programmer", it
  is the USB_KEY route [?]. AC7911BA/BB differ only in SDRAM (2/8 MB), not in flash.
* Factory presets via the updater: M-UPGRADE writes 16 × 8 voices with manufacturer ID `00 32` and its own frame
  (KingParamount `docs/protocol-and-provenance.md`; benny `fm1-research.md:3706-3760`). The suite already includes
  the banks (`dx7_sources.py:42`).
* Velocity: the original firmware scales incoming velocity by 100/127 (measured on FM-1+VA). `fmrender.py` does not
  do this. For previewing this is irrelevant, for an original twin it is not.

### b5. Safety: comparison of the suite's flash and rescue paths with aroum §1.3

I checked `flash/{flashmodel,restzone,planner,uboot,uboot_manifest,engine}.py`, `vendor/unbricker/*`,
`ui/pages/recovery.py`, `firmware.py` and `vendor/fm1_install.py`.

| Rule from aroum §1.3 / fm1-emu recon | Suite | Result |
|---|---|---|
| Never write or erase 0x0000–0x3FFF | `flashmodel.py:31` NEVER_ERASE (head + `key_mac`); Unbricker `PROTECTED_END = 0x4000` (`fm1_unbrick.py:59`); `restzone.py:58` | ✓ |
| App from 0x4000, area up to 0x92FFF | `flashmodel.py:25-27`; the Unbricker writes only 0x4000–0x92FFF; `uboot.py:276-291` blocks V16 (`TOOL_TRUNCATES`) | ✓, but see S2 (V16 head) |
| Felucca 0.9 → 1.0: erase 0x97000+0x8000 and 0xFC000+0x2000 | hint `planner.py:406-412`; executed via `repair-settings`, which resets **more** (0x93000–0xE8FFF and 0xFC000–0xFEFFF, `fm1_unbrick.py:77-86`) | ✓ for V15 layout; **S1** for V16 |
| ROM boot mode WL82 UBOOT1.00, USB 4C4A:8057 | `uboot.py:33`, selection via sysfs plus SCSI identity (`uboot.py:81-92`) | ✓ |
| Chip key 0x980F, flash ID 0x856014, 1 MiB | `flashmodel.py:23,28`, `uboot.py:34`, Unbricker without override | ✓ (variants AC7911BA/BB only concern the RAM) |
| USB_KEY 0x16EF via RP2040 (Transporter), wiring | `recovery.py:72-84`: D6/GP0 → D+ green, D7/GP1 → D− white, GND, VBUS unconnected | ✓ (the value 0x16EF itself is only in the Transporter firmware) |
| Dangerous SysEx `F0 22 24 35 7D F7` | Only `uboot.softkey()` after confirmation (`recovery.py:526-554`); simulators and online editors filter 7D/7F and packed `F0 00` frames (`sims/__init__.py:136-157`) | ✓ |
| ROM boot without dongle: leave on for 2 min, three quick restarts, OCT−+OCT+ 10 s | `recovery.py:36-47`: OCT− (evidenced), OCT−+OCT+ as unconfirmed, warning "do not switch off/on twice in update mode" | Gap: watchdog (2 min) and "three restarts" are missing as a hint. Contradiction in the sources: M-Flasher names OCT−+OCT+ 5 s = download mode on Felucca/SLOOP (`m-flasher/README.md`), aroum 10 s = update mode; on Melodee it is the calibration |

**S1 (urgent; status: fixed in 2.2.0):** Before `repair-settings`, check the backup from the same session. Only if it
shows the V15 layout (`flashmodel._app_match`, family ≠ `v16-layout`) may anything be written, otherwise abort. If
needed, in a separate script like `restzone.py` with the function `erase_repair_sector`. In addition, the final
sentence in `recovery.py:520-521` must be corrected.

**S2 (urgent; status: fixed in 2.2.0):** Before "Original-V15 zurückschreiben" (write back original V15), evaluate
the head from the mandatory backup. If it is the V16 head (`8f5228c2…`), do not write V15: either use the
`fm1_rescue.py` logic (V16 app 0x4000–0xA8FFF) or M-Flasher `firmware FM-1_016.fwsc`, or at least a clear warning.

**S3 (status: fixed in 2.2.0):** In `check_dump`, report an unknown app area that starts with `FM-1_0NN` and is
otherwise plausible as a "factory or unknown original version" (yellow), not as "damaged" (red). Show the differing
sectors as well.

**S4 (medium):** No longer set the V15 intermediate step by default when the loader route does not require it, or
offer TransitFM1 as an intermediate stop (TransitFM1 `README.md:43-50`; fm-static "no blanket V15 prerequisite",
`README.md:8`). It should stay fixed for V16 and for `requiresStockV15`.

**S5 (low):** In practice a ROM boot session lasts about one tool run; `info` followed by `backup` led to a
reset (`SKILL.md:170`). `restzone` plan → clean and the rescue procedures with several runs then fail safely, but
confusingly for the user. Best to do everything in one run or to warn the user beforehand.

Teardown (aroum `photos/`): board "DX7 MB V07 260620" (photo_02 looked at myself), AC7911B in a QFN-48 package, no
debug pads or jumpers. This confirms: without a working firmware the only way is USB (ROM mode or USB_KEY).

---

## (c) All entries checked

Relevance: – none, n low, m medium, **h high**. "maintained" = last commit in October or November 2026.

### Reverse engineering and firmware development (awesome-fm-1, all entries)

| Name | Link | Licence | Rel. | Note |
|---|---|---|---|---|
| FM-1-RE | github.com/AL-255/FM-1-RE | WTFPL | **h** (knowledge) | As of 2026-09-08, 142 MB. Memory map V13 (VM 0x94000, BTIF 0xE9000, USR 0xEA000, `key_mac` 0xFF000), engine = msfa (byte-exact tables), 44 118 Hz, 12 voices; OTA captures; system command table. The safety verdict "NO-GO" and "no rescue in ROM" are superseded by USB_KEY and WL82. Only V13/V14 images, **no V15/V16 function maps**. Branch `with-custom-firmware`: Dexed port with ALSA/JACK/LV2 host (`firmware/host/`). Do not take over code; use as a reference. |
| fm1-custom-fw (aroum) | github.com/aroum/fm1-custom-fw | **no licence file** | m | Teardown photos, pinout, link list (sections 2–7, all read). The scanner is ineffective, the flasher does not work (b4); several inaccuracies. Code cannot be taken over because of the missing licence. |
| fm1-nes GETTING_STARTED / APP_UPDATES | github.com/Keitark/fm1-nes | Apache-2.0 + GPL/MIT | n | Updates of the app only while keeping the bootloader; experimental, own writer. |
| fm1-emulator (simonjohansson) | github.com/simonjohansson/fm1-emulator | GPL-2.0-or-later | n | QEMU, Linux x86-64 only, slow. |
| FM-1 browser emulator (shcherbakov7) | github.com/shcherbakov7/m-wave-fm1-browser-emulator | GPL-3.0 | n | Rust/wasm, V13–V15; superseded by fm1-emu. |
| Dinghy | github.com/zvenson/dinghy | GPL-3.0 | n | Minimal firmware, reference for rescue and USB. |
| fm1-bootloader | github.com/jvitkauskas/fm1-bootloader | Apache-2.0 (according to the list) | n | **not reachable (403)**. |
| MvaveFM1-Designer | github.com/Quixotic7/MvaveFM1-Designer | MIT | n | Control panel mock-ups and JSON; possibly for the twin's graphics. |
| fm1-firmware-patcher | github.com/czietz/fm1-firmware-patcher | Unlicense | m | V15 and V16 patches; **V16 has corrected detune**; fixed output hashes (b2). |
| MVAVE-M-UPGRADE-decompiled | github.com/entitymar/MVAVE-M-UPGRADE-decompiled | none | n | Decompilation; licence unclear, read only. |
| jielie | github.com/kagaimiq/jielie | none | n | Chip reference, USB_KEY docs (`isp/usb/`). As of 2024. |
| JieLi AC79 SDK docs | doc.zh-jieli.com | – | n | not reachable. |
| jl-uboot-tool | github.com/kagaimiq/jl-uboot-tool | MIT | m | Already used (via the Unbricker). As of 2025, "trashed" according to the author; WL82 officially "unknown". |
| ghidra-jieli | github.com/kagaimiq/ghidra-jieli | Apache-2.0 | – | pi32v2 "very early". |
| jl-misctools | github.com/kagaimiq/jl-misctools | MIT | n | Container and key tools; the suite has its own parsers. |
| Schwung | github.com/charlesvestal/schwung | MIT | – | Ableton Move framework. |
| fm1-emu | github.com/joakimeriksson/fm1-emu | MIT | **h** | Second engine, test environment (b1). |
| felucca-try | github.com/spinkham/felucca-try | GPL-3.0 | n | MIDI CC 20–27 → control panel buttons; an idea for the twin. |
| foma | github.com/konsumer/foma | GPL-3.0 | – | Firmware development with Docker. |
| felucca-bsp | github.com/hugelton/felucca-bsp | MPL-2.0 | **h** | `tools/fm1_rescue.py` (opportunity 5, S2); the `ota_boot_valid` fix is known to the suite. |
| Felucca-Engines | github.com/hugelton/Felucca-Engines | MIT | n | Felucca DSP as C99. |
| FM-1 Macropad Patch | github.com/smememe/fm1-macropad-patch | MIT | – | V15 patch; its output hash could be added. |
| Fluyt | github.com/rgrvmusic/fluyt-logue | GPL-3.0 | – | KORG ports. |
| sloopy-sloop-32 | github.com/smhgzll/sloopy-sloop-32 | GPL-3.0 | n | SLOOP on ESP32, shows the portability of the SLOOP code. |
| fm1-playwright | github.com/DimmKirr/fm1-playwright | MIT | m | Test environment via fm1-emu. |
| setup-fm1-emulator | github.com/DimmKirr/setup-fm1-emulator | MIT | m | CI action. |
| fm1-base | github.com/charlesvestal/fm1-base | MPL-2.0 | n | Host simulator concept. |
| setup-fm1-toolchain | github.com/DimmKirr/setup-fm1-toolchain | MIT | n | Toolchain mirror (build of Melodee from source for the device). |
| mvave-fm1 (maxfridbe) | github.com/maxfridbe/mvave-fm1 | MIT | m | Field reports: a ROM session lasts about one run; factory V15 has a different hash (S3, S5). |

### Firmware update and rescue

| Name | Link | Licence | Rel. | Note |
|---|---|---|---|---|
| fm1-linux-update | github.com/fuleo/fm1-linux-update | MIT | n | V14 → V15; covered by the vendored installer. |
| fm1_flasher.py | aroum | – | – | does not work (b4). |
| fm1_ota.py (FM-1-RE) | AL-255/FM-1-RE/tools | WTFPL | n | Byte-exact client plus capture checker; test case for the suite (`tests/fixtures/fm1-v15-reflash-2026-09-04.json`). |
| FM-1 Transporter | github.com/kurogedelic/FM-1-transporter | MIT | m | Already in the suite's instructions. |
| USB_KEY dongle notes | ip2k/lunar-modulator `docs/10-usb-key-dongle.md` | MIT | n | Background. |
| MvaveFM1Unbricker | github.com/Quixotic7/MvaveFM1Unbricker | MIT | **h** | Vendored copy = upstream HEAD 517b938 (only the README has one more line). Limits see S1/S2. |
| flipper-fm1-recovery | github.com/merthsoft/flipper-fm1-recovery | Unlicense | n | Flipper as USB_KEY, PC part for Windows. |
| fm-static | github.com/seajaysec/fm-static | MIT | m | Layout analysis from package and dump tables; no blanket V15; its "cleanup" packages are risky. |
| How to update (Medium) | medium.com | – | – | not fetched. |
| fwradar changelog | fwradar.com | – | – | not fetched. |
| M-Flasher | github.com/jvitkauskas/m-flasher | **MIT** (aroum wrongly says GPL-3.0) | **h** | Opportunity 4; V16 layout; keeps factory data. |
| fm1_rescue.py (Hub) | thefirmwarehub.com (copy in felucca-bsp) | MPL-2.0 | **h** | Opportunity 5. |
| Slipway | github.com/Discodave-Glitch/slipway | MIT | n | Windows. |
| FM1Kit | github.com/ocean/FM1Kit | MIT | m | Loader identity `ota-FM1C_…`; resuming from the loader. |
| FM-1 Transporter multiboard | github.com/Nicodesy06/FM1Trasmitter-for-pico | MIT | n | Profiles for other RP2040 boards (interesting for Pico users). |
| m-bootloader | github.com/jvitkauskas/m-bootloader | MIT | m | Download mode via REC, rescue menu; the suite already warns (the boot area changes). Official updates reset the original bootloader. |
| FM-1 Swap | github.com/HyruleanHero1988/FM-1-Swap | MIT | n | Android. |
| FM-SWITCH | github.com/ocean/FM-SWITCH | none | n | iOS. |

### Editors, librarians, companion software

| Name | Link | Licence | Rel. | Note |
|---|---|---|---|---|
| FM1 Editor & Librarian | github.com/benny-sparra/fm1-dx7-patch-importer | MIT | **h** (knowledge) | `docs/fm1-research.md` (4300 lines): V16 hardware tests, object 4/5, Bank Export, list of dangerous commands. |
| DXcompanion | dxcompanion.uk | – | – | not reachable. |
| Dexed | asb2m10 | GPL-3 | n | Parameter SysEx. |
| FM-1 Workbench | github.com/thegiantsnail/fm1-workbench-public | GPL-3.0 | n | JS DX7 engine "calibrated on the device". |
| Virtual FM-1 | github.com/jbschooley/Virtual-FM-1 | GPL-3.0 | **h** | Opportunity 7; Melodee/Felucca/SLOOP from source, synchronisation; Linux x86-64. |
| FM1VST | github.com/Quixotic7/FM1VST | GPL-3.0 | m | Model for the core ABI; Melodee 1.4.5; macOS only. |
| FM-1 Mobile | github.com/hb3p8/fm1-mobile-emu | GPL-3.0 | n | wasm builds, Melodee included (294 MB). |
| sloop-fm1-go | jahlib | GPL-3.0 | n | not cloned; Android. |
| SLOOP 8-Track Web | smhulme | GPL-3.0 | – | not cloned. |
| FM-1 Pulses | mene311 | ? | – | not cloned; FM-1+VA. |
| FM-1 Utility | fm1-utility.pages.dev | – | – | not reachable. |
| fm1-read-voice | github.com/czietz/fm1-read-voice | MIT (in the README) | **h** | Command 0x23 / object 5 (opportunity 6). |
| fm1-bank-sender | leomaimoni | – | – | Android. |
| fm1_soundbank_app | github.com/pfkellogg/fm1-bonus-box | none | n | Sending banks; provenance from KingParamount. |
| mac-usb-midi-bridge | pfkellogg | MIT | – | macOS. |
| Felucca-WebApp | github.com/hugelton/Felucca-WebApp | GPL-3.0 | m | Editor with `?mock=1`; protocol of the Felucca family. |
| FM-1 Screen Reader | charlesvestal/fm1-reader | MIT | n | not cloned; accessibility. |
| FM1 Studio | thingsapart/fm1-studio | GPL-3.0 | n | not cloned; Android. |
| FM1 DECK | TonalPAK/FM1-DECK | GPL-3.0 | n | not cloned. |
| felucca-vst | github.com/cdodd/felucca-vst | GPL-3.0 | m | LV2 for Linux, Felucca family only with the browser emulator. |
| Falua | ehn96/Falua | GPL-3.0 | – | JIANT engine. |
| SloopStudio | smhulme | GPL-3.0 | – | iOS. |
| FM-1 Pulses Felucca | mene311 | ? | n | Project writing via the editor protocol. |
| SLICE64-FM Editor | jeymadcat-ops | none | – | |
| Felucca Editor (hunterxiong) | hunterxiong2026 | MIT | n | Felucca 1.5 via SysEx. |
| SLOOP Editor Skill | hunterxiong2026 | MIT | n | SLOOP 2.4.1. |
| Octo-Vave | github.com/amacias/octo-vave | MIT | n | Catalog with hardware validation; possible as a source for the catalog. |
| FM1 MIDI Converter | github.com/balancechiang/fm1-midi-converter | GPL-3.0 | n | Format "FM1 V4 project" (unclear which firmware) [?]. |
| Ghoulbox VST3 | LexLongcoding | GPL-3.0 | – | Windows. |
| FM-1 for Android | github.com/HolidayMan/fm1-android | GPL-3.0 | n | Melodee 1.4.5, FM1 Open V14 from source. |
| PocketFM | HolidayMan/fm1-ios | GPL-3.0 | – | |
| Harbor | github.com/markterm/harbor | none (aroum says GPL-3.0) [?] | n | fm1-emu on Android; core 1 optimisations have gone upstream. |
| FM-1 Pocket | github.com/Prado-Design/fm1-pocket | GPL-3.0-only + SDK exception | n | FM1 Open V14 as wasm (the suite already has that). |

### Patches, hardware, documentation, related

| Name | Link | Licence | Rel. | Note |
|---|---|---|---|---|
| fm1-factory-presets | github.com/KingParamount/fm1-factory-presets | banks CC0, docs CC BY-SA 4.0, tools MIT | m | already included (`dx7_sources.py:42`); protocol of the updater restore. |
| fm1-banks | github.com/mene311/fm1-banks | MIT | n | already included. |
| This DX7 Cart… / NeuralDX7 | thisdx7cartdoesnotexist.com | – | n | Generator idea. |
| Ambient & Dreams | natlifesounds.com | commercial | – | |
| fm1-sustain-footswitch, Bonus Box, voice-tuner | pfkellogg | none/MIT | – | Hardware. |
| fm1buddy | github.com/konsumer/fm1buddy | GPL-3.0 | n | Firmware switching from SD; USB MIDI host. |
| fm1-cartridge | github.com/andrerezemini/fm1-cartridge | GPL-3.0 | n | Melodee on ESP32; contains `sloop-2.8.4.fwsc` (useful as a test package). |
| FM-1 MIDI Guide, fm1-guide, Easy Guide, Tao of Mac | various | – | n | not reachable (github.io/web). |
| FM-1 SysEx protocol | KingParamount | CC BY-SA | m | see above. |
| OTA protocol docs | FM-1-RE | WTFPL | m | see above. |
| Felucca BUILDING.md | hugelton/Felucca | GPL-3.0 | n | |
| FM-1 Firmware Database | stanleygurvich/fm1-firmware-database | GPL-3.0 | – | **403**. |
| FM-1 Firmware Atlas | github.com/wero1414/atlas-fm-1 | CC0 | n | `data/firmwares.json` possible as a second catalog source. |
| FM-1 learning handbook, FM-1 Lab | itoyohane, web255078-lab | ?/none | – | |
| fm1-dev-wiki (aroum 3.6) | github.com/sbegueria/fm1-dev-wiki | CC0 | m | Evidenced facts on rescue (safe mode after two failed starts), flash map and NOINIT. |
| fm1-firmware-collection (aroum) | github.com/2bbb/fm1-firmware-collection | MIT | n | All firmwares as submodules with CI builds; catalog source. |
| ip2k/mvave-fm1-open-firmware (aroum) | = Lunar Modulator | MIT | n | Dongle specification. |
| fm1-manager (aroum) | github.com/bratta/fm1-manager | no licence file (aroum: MIT) [?] | m | Original backup by stepping through and reading out (opportunity 6). |
| fm1-backup-restore (aroum) | github.com/workeffortwaste/fm1-backup-restore | MIT | m | Full dump via Android; writes `key_mac` back (b3). |
| fm1-grid (aroum) | github.com/jasonbot/fm1-grid | GPL-3.0 | n | Four fm1-emu instances; model for several twins. |
| hdavid/fm1-emulator, weavejam/Felucca (aroum 3.4) | see b1 | GPL | n/m | |
| TransitFM1 | github.com/Quixotic7/TransitFM1 | GPL-3.0 | **h** | Opportunity 8 / S4. |
| Related M-VAVE (SMK-37, Chocolate, BlackBox, IR Box, MK300, SMC …) | various | various | –/n | Only the SMK-37 is related (same chip, USR at 0xF4000, **calibration 0xC1000 device-specific**). Relevant for the suite only if it is to support the SMK-37. |
| Official (product page, downloads, V16 URL, V15, MIDI docs, release notes, manual, FM1 Open) | m-vave.com, fm1_open | – | m | V16 hash `d017fb65…` confirmed several times (M-Flasher, czietz, fm1_rescue). |

### Articles, videos, communities (titles only, not reachable)

Articles: Synth Anatomy (review, V15, Librarian, Baud Girl, Felucca, SLOOP, X0X, Groove OS, Xfer "Shrub"), Piano &
Synth, Sonicstate, MatrixSynth (3), Time To House, Noizefield, Ars Technica. Videos: Synth Anatomy Review, Maks Makes
CC, Sound Import, V09 Upgrade, BLE-MIDI, Full song, FM-1+VA Tutorial, Felucca 0.9 Guide, Felucca First Look, Felucca
Jam. Communities: KVR, r/MVaveFM1, Hacker News, Elektronauts, Gearspace, Plugg Supply, GitHub topic m-vave.
Relevance for the suite: – or n in each case.

### Firmwares that matter for the twin (from the firmware list)

With their own host or browser build: Felucca (browser emulator, `tests/hostsim.c`), ChoralRoot (own emulator
`tools/emu`, core in FM1VST), FM1 Open (simulator.wasm, already in the suite), Skiff/Mini-Tone/PIGEON (felucca_web glue
in FM1VST), Groove OS (browser emulator, closed source, commercial), Dinghy/foma/fm1-base (host simulator). Melodee and
SLOOP have **no** emulator of their own. Others build it themselves (FM1VST, Virtual-FM-1, FM-1 for Android: Melodee
1.4.x) or use fm1-emu. None builds 1.6.0-rc.8; the suite is the only one that does.
