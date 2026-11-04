# FM-1 firmware installation – protocol, loaders, safety (installer workstream)

Status: research notes for `fm1companion/flash/`. **Nothing here has touched a real FM-1.**
Every statement is tagged **[V]** = verified from source code we read (file named), or
**[A]** = assumed / taken from documentation or third-party text without code we could check.

## 1. Transport and framing

* [V] Everything runs over USB-MIDI SysEx (`melodee/tools/fm1_install.py`, `felucca-bsp/chip/ota.c`,
  `fm1_open/sdk/WebUpdate.js`). Message = `F0 pack7(00 59 cmd len:u24le 00? body checksum) F7`,
  8→7 LSB-first bit packing, checksum = `~sum(body)` (bytes from index 6).
* [V] Handshake `HS_QUERY = F0 00 32 45 00 00 00 40 7F F7` (= cmd 0x11, empty). Answer: cmd 0x11 with a
  27-byte zero-padded ASCII identity, e.g. `FM-1_015`, `FM-1_91060`, `ota-FM-1_900`.
  Running Felucca-family firmware answers it in its main loop (`ota_service`) – the handshake is
  harmless and does not disturb playback. Stock answers it too (it is how M-UPGRADE finds the device).
* [V] `UPGRADE = F0 22 24 35 7F F7` starts a session (same bytes for step 1 and step 2).
  (`F0 22 24 35 7D F7` = "soft key → ROM UBOOT" in Felucca – never sent by us.)
* [V] Device-pull: the device sends read requests (cmd 0x30: flags, addr u32, len u24, max 512) on the
  **logical image** (the .fwsc without the 20 marker bytes at `i*0x30+0x2F`, i<20). The host answers each
  with the bytes. Terminal requests: `0xE0000000` (end of step 1, "verification done") and `0xF0000000`
  (end of step 2, written); the host answers `success\0`.
* [V] Package identity ("product") = the 20 marker bytes: `chr(m - i - 1)`, `0x7D` = unused.

## 2. Two steps

1. **Step 1 (running firmware)** [V felucca-bsp ota.c, melodee ota.c]: reads UFW header + entry list,
   finds `ota.bin` (type 100), checks it, stages it at flash 0xE0000, asks `0xE0000000`, writes the
   `UPDATA_PARM` record and resets. Nothing of the application is written in step 1; if it fails the
   old firmware keeps running ("nothing was written").
2. **Step 2 (the package's own loader)**: re-enumerates as a different USB device, answers the
   handshake with an `ota-…` identity, reads the whole image and writes it, asks `0xF0000000`, reboots.
3. **Verify**: wait for a non-`ota-` identity and compare it with the package product.

The loader always comes **from the package** [V ota.c "stage the package's loader"; Firmware Hub KB].

## 3. Who accepts what in step 1 (the running firmware)

| running firmware | step-1 policy | source |
|---|---|---|
| Stock V13–V16 | any package its verifier accepts (proprietary checks, incl. "no-op" refusal of same version) | [A] FM-1-RE docs/io/11 |
| Melodee, SLOOP | packages whose loader carries `FELUCCA-LOADER-1` (all Felucca-family loaders) **or** the official stock loader (known CRCs) – the latter only if the package's boot area 0x0–0x3FFF equals the device's (**CHECK HEAD**, error −5 "FAILED -5"); m-bootloader writer (Melodee) | [V] melodee/firmware/src/ota.c, sloop-fm1/firmware/src/ota.c |
| Felucca (felucca-bsp, current) | any well-formed loader; for non-Felucca loaders the boot area must be a valid FM-1 boot image | [V] felucca-bsp/chip/ota.c |
| M-VAVE Open (`FM1_OFFICIAL_OPEN`, `FM1_COMMUNITY_n`) | own tagged protocol (cmd 49/50); Melodee refuses to install from it ("go back to stock first") | [V] fm1_open/sdk/WebUpdate.js, melodee/web/fm1ota.js |

## 4. Loader families and identities

| family (Mooring) | handshake identity in update mode | USB | writes | resumable after power loss |
|---|---|---|---|---|
| `stock` (usb_hid_ota.bin, sha256 ef1f2db8…) | `ota-FM-1_015` when started by Felucca-family step 1 [V: they write that name into UPDATA_PARM]; from stock itself probably `ota-FM-1_0NN` [A] | `4d4a:4155` "ota-FM-1" [A FM-1-RE capture] | whole package incl. boot area 0x0–0x3FFF, USR, isd_config [V melodee docs/RECOVERY.md] | stays in update mode until done [A] |
| `feluccaG1/G2`, `sloopG3` (Felucca-derived `ldr_core.c`) | `ota-FM-1_900` (FELUCCA_ID) – some forks use `ota-<product>` (Skiff) [V loader.c; Mooring notes] | `1209:0002` "<Name> Update" e.g. "Melodee FM-1 Update", "Felucca Update", "Jangada Update" [V usb.c] | app area only (0x4000–0x93000/0x97000) [V loader.c FL_RANGE_OK] | G2/G3 yes (restart the same package); G1 often **not** (`powerLossNotResumable`) [A Mooring notes] |

**Rule (Melodee, adopted):** a package goes only to its own loader family. Stock package → stock loader;
Felucca-family package → `ota-FM-1_9xx`-style loader. Sending a Felucca package to the stock loader
would write its head over the boot data; sending V15 to a Felucca loader writes only the app area
(this exact bug happened in Melodee's web installer 16 Oct 2026) [V melodee RECOVERY.md].

How the app decides a package's family [V]: official V15/V16 by sha256 (and the stock loader's
`ota.bin` CRCs); `FELUCCA-LOADER-1` marker in the package → Felucca family; otherwise unknown →
refused unless the catalog names the loader family (by `loaderSHA256`).

## 5. Running-mode identities (handshake)

* [V] Stock: `FM-1_0NN` (V15 = `FM-1_015`, V16 = `FM-1_016`); `FM-1_099` is not stock.
* [V] Melodee releases `FM-1_9XXYYZZ`-ish (`FM-1_91060` = 1.6.0), dev `FM-1_900`; many Felucca forks
  use `FM-1_9NN`; fm1-base forks `FM-1_<d>XXYYZZ` (AMB `FM-1_7010300`…). Identities are **not unique**
  (SLOOP, ORBIT, GHOULBOX … all `FM-1_900`) → the app shows catalog candidates and asks once.
* [V] TransitFM1 answers `FM-1_015` (like V15) but its loader is `ota-FM-1_900` [melodee RECOVERY.md].
* [V] M-VAVE Open: `FM1_OFFICIAL_OPEN`, `FM1_COMMUNITY_<n>`; WebUpdate.js also treats `FM-1_8NNNNNN`
  as Open-like but notes other firmwares share that prefix.

## 6. USB / MIDI port names (Linux ALSA)

| mode | VID:PID | product string → ALSA client/port |
|---|---|---|
| stock running | `4c4a:c755` [A capture] | "USB Composite Device" (MIDI iface "FM-1 Midi") → port "USB Composite Device MIDI 1" [A FM-1-RE] |
| stock update loader | `4d4a:4155` [A] | "ota-FM-1" composite [A] |
| Melodee running / loader | `1209:0001` / `1209:0002` [V] | "Melodee FM-1" / "Melodee FM-1 Update" (older: "Melodee", "Melodee Update") |
| Felucca & forks | `1209:0001` / `1209:0002` [V] | "Felucca"/"Felucca Update" or fork name ("CTL-1 FM-1", "Jangada Update", …) |
| ROM UBOOT (mask ROM) | `4c4a:8057` [V melodee docs] | USB mass storage "WL82 UBOOT1.00" – **no MIDI**; needs a USB recovery tool |
| Windows-style | – | "USB-Midi", "Sinco-Midi" (Melodee/WebUpdate port filters) |

App rule: a port is an FM-1 candidate if its USB VID:PID is one of the above, or its name matches the
FM-1 name list (incl. "composite", "usb-midi", "sinco", "ota", "update" with an FM-1 VID). Generic
names on a non-FM-1 VID are never probed.

## 7. Update mode / recovery gestures

* [V melodee BOOT-SAFETY.md] Melodee ≥ 1.3.5 (boot stub): after an interrupted write the FM-1 starts the
  MIDI update loader again; **hold OCT− alone while switching on** to request it; **three short
  key-light flashes = update mode**; one long flash = ROM UBOOT. Both octave keys = calibration.
* [A TransitFM1 README] same gesture; never power-cycle twice in a row in update mode (second arrival
  without a normal boot drops to ROM UBOOT).
* [A] Stock V15 and Melodee ≤ 1.3.4 have no rescue gesture.
* ROM UBOOT (`4c4a:8057`): outside MIDI; the app only detects it and points to the Melodee/Hub USB
  recovery tools (`fm1_recover.py`, MvaveFM1Unbricker). We do not implement ROM flashing (too risky blind).

## 8. Resume semantics

* [V fm1_install.py] If the device is already in update mode, the install skips step 1 and runs step 2 —
  **only** if the loader identity equals the package's own loader (`LOADER_HINT`: `ota-FM-1_900` →
  "finish with a Melodee package", `ota-FM-1_015` → "finish with the official V15"). `--force` never
  resumes.
* [V Melodee BOOT-SAFETY] resume restarts the write from the beginning (matching sectors skipped).
* [A] For `powerLossNotResumable` (G1) packages, a power cut during step 2 may leave the device
  unbootable (ROM UBOOT path only).
* The app keeps a **journal** (`DATA_DIR/flash/journal.json`): target package path + sha256, product,
  expected loader, phase. After a restart the recovery wizard offers exactly that package.

## 9. `requiresStockV15` (Mooring catalog)

* [V] In catalog revision 6, 119 of 121 downloadable packages carry `requiresStockV15: true`; only the
  stock V15/V16 entries are false. Community guidance [A awesome-fm-1 README "Custom firmwares expect
  you to be on V15 first"; Firmware Hub; TransitFM1]: start such an install from **official V15**
  because (a) installers/loaders assume V15's boot area and data layout, (b) another custom firmware's
  step 1 may refuse foreign loaders or its saved data may crash the next firmware, (c) V15 is the
  well-trodden path.
* **App semantics (decision):** `requires_stock_v15` → the device must report `FM-1_015` when the
  target install starts. Otherwise the plan first installs the hash-verified official V15
  (`db1642b2…`, own stock loader), verifies `FM-1_015`, then installs the target. TransitFM1 also
  reports `FM-1_015`; that is its documented purpose, so it is accepted.
* Builtin Melodee/Skiff/SLOOP specs keep `requires_stock_v15 = False` (their own installers handle
  Felucca-family → Felucca-family switches; Melodee's own step 1 accepts Felucca-family loaders).
* Direct switches to/from stock V16: V16 uses the same stock loader [V catalog loaderSHA256]. Returning
  to V16 from Melodee goes through Melodee's CHECK HEAD (V16's boot area may differ → FAILED −5,
  nothing written) [A]; the app therefore recommends V15 as the way back and treats V16 as optional.
  M-VAVE replaces the V16 file at its URL – the app always checks sha256 `d017fb65…` and refuses a
  mismatch.

## 10. Risks and mitigations in the app

| risk | mitigation |
|---|---|
| wrong/corrupt package | sha256 vs spec/catalog; unknown files need an explicit "Ich weiß, was ich tue" (I know what I am doing); UFW header/entry CRC check (from SLOOP `package_damage`) |
| package for another model | product must match `FM-1_…`; device model compared before step 1 |
| package sent to a foreign loader | expected loader family per package; refuse otherwise (nothing sent) |
| interrupted step 2 | journal + recovery wizard resumes with the same package in its own loader |
| non-resumable loader | explicit power/battery/cable confirmation |
| two installs at once | process lock file + in-process lock |
| several FM-1 attached | warning; the engine sticks to the selected port name, the loader is searched among FM-1 candidates only |
| data loss | auto-backup (Felucca protocol) before writes; plan lists what will be lost |
| V16 URL changes content | hash check, refuse on mismatch |
| CHECK HEAD refusal (stock return) | detected (step 1 stops in 0x…–0x4000 window) → explain, never retried another way |
| ROM UBOOT | detected via sysfs `4c4a:8057`, guidance only |

## 11. Engine choice

The app vendors Melodee's `tools/fm1_install.py` (GPL-3.0-only) as
`fm1companion/vendor/fm1_install.py` and drives `Updater` in-process with a pluggable backend
(`MidoBackend` for real ports, `SimBackend` for tests/demo). Reasons: the same code path is tested in
simulation as runs on hardware; structured errors instead of parsing stdout; progress callbacks; loader
policy can be extended per family without patching subprocess output. Melodee packages with a resource
region still get it written with Melodee's own repo tool (`firmware.write_resources`), because the
resource format belongs to that firmware version.

## 12. Intermediate step via original V15 (project requirement, implemented in `flash/planner.py`)

| Case | Option in the plan dialog |
|---|---|
| Firmware switch (different key) or current firmware unknown | **on** (checkbox ticked, can be unticked) – protection against careless mistakes |
| Target has `requires_stock_v15` | **on and locked** (the catalog requires V15) |
| Update of the same firmware (e.g. Melodee rc.6 → rc.7) | off, offered as an option |
| Target is original V15/V16, device already runs `FM-1_015`, device in update mode | no option |

Order: **Backup (Felucca protocol) → load package → V15 → target → resources → check identity.**
Reason [V Unbricker flash map]: Felucca data lives at 0x97000..0xDFFFF / 0xE0000 / 0xFC000 and overlaps
the original firmware's settings storage 0x93000..0xE8FFF – V15 can overwrite it. The dialog says (in German):
"Your projects on the device can be lost during the intermediate step – the app backs them up first."
The choice is only remembered per switch (`config.module("installer")["v15_choice"]["melodee>sloop"]`); there is
no global switch-off. If you stay on V15 and the presets are garbled: System > Reset Patches.

## 13. Rescue level 2 – ROM boot mode "WL82 UBOOT1.00" (4c4a:8057)

* [V MvaveFM1Unbricker 1.2.0, MIT, Michael P Jones; Reddit guide u/acrawf1] An FM-1 whose firmware does not
  start appears as the USB mass storage device "WL82 UBOOT1.00". MIDI installers then report "FM-1 not found".
  kagaimiq's jl-uboot-tool (MIT) loads a RAM loader (JieLi `wl82loader.bin`, sha256 d41da612…) and can
  read/erase/write the 1 MiB flash (SCSI via `/dev/sgN`, on Linux root + module `sg`).
* Procedure (all in the unmodified `vendor/unbricker/fm1_unbrick.py`): exactly one WL82 device; chip key **0x980F**
  and flash ID **0x856014** (no override); read the whole flash twice, compare, save;
  extract the firmware area from the .fwsc (`flash.bin[0x4000:0x93000]`; V15 → sha256 **6edf3c37…** [V own
  test]); write only **0x4000..0x92FFF**; read back and compare; switch off and on.
  Never below 0x4000, never 0xE9000..0xFBFFF (Bluetooth, factory sounds) and 0xFF000..; no chip/block erase.
* App additions (`flash/uboot.py`): a device only if its sysfs ancestor is 4c4a:8057 **and** the SCSI vendor/model
  is WL82/UBOOT1.00 (no free selection, the size is shown); start via `pkexec`; jl-uboot-tool is fetched only after
  consent via `fm1_unbrick.py setup --no-pip` at commit adb3f18; dependencies via apt (python3-crcmod,
  python3-yaml, python3-pycryptodome [A: provides the module `Cryptodome`], python3-tqdm).
* Felucca 0.9 → 1.0.x [A Reddit]: 1.0.x does not start because of old 0.9 data. The remedy there: erase
  0x97000+0x8000 and 0xFC000+0x2000 (erases songs/settings). The app does not erase raw; it warns in the plan and
  offers `repair-settings` (resets these windows with a factory dump; upstream only tested with the mock).
* `softkey` sends a running original FM-1 into ROM boot mode via MIDI – only for the voluntary
  "Notfall-Komplettsicherung" (emergency full backup), never automatically.

## 14. Rescue level 3 – FM-1 Transporter (hardware)

[V TRANSPORTER-GUIDE.md] If the FM-1 never appears as WL82: a Seeed XIAO RP2040 on the USB data lines;
it sends the USB_KEY at power-on and forces ROM boot mode. Wiring: **D6 (GP0) → D+ (green),
D7 (GP1) → D− (white), GND → GND (black), do not connect VBUS (red).** Flash the UF2 with the BOOT button held;
then `fm1_transporter_recover.py` (rekey → info: `key=980F id=856014` → dump → restore --write).
**RP2040 required, explicitly not RP2350** (GPIO erratum, D+ pull-up). [A, untested] Raspberry Pi Pico
(RP2040) instead of XIAO: pin 1 = GP0 → D+, pin 2 = GP1 → D−, any GND, VBUS unconnected; try the XIAO UF2 or
rebuild kurogedelic/FM-1-transporter with `-DPICO_BOARD=pico` and the same loader.

## 15. Prevention / basic checks [A community]

Charge the battery > 1 h (deep discharge prevents starting), try another data cable/port/computer, go to V15
before a third-party firmware, keep a backup after a successful flash. Update mode: documented is **OCT− alone**
at power-on (three short key-light flashes); "according to the community" also holding OCT− + OCT+ for 10 s (on
Melodee OCT−+OCT+ is the calibration!).

## 16. Bootloader trap (CHECK HEAD) and download-mode tools

* [V melodee/firmware/src/ota.c, sloop-fm1/firmware/src/ota.c] Melodee 1.6.0-rc.7 and SLOOP 2.8.3 still have the
  **old** check: packages with the M-VAVE loader only if their first 0x4000 bytes equal the device's boot area byte
  for byte (otherwise −5). Skiff not checked (no clone). [A fm-1.drewr.dev/bootloader-check] After m-bootloader or
  Open V12/V14 the boot area is different → these firmwares refuse V15 ("trapped"). Fix: felucca-bsp `afd83ede`
  (`ota_boot_valid`, present in this repo clone [V]). Otherwise the way out: download mode.
* Device codes (only on the FM-1 display; the computer only sees "stops reading"): −2 header CRC, −3 no
  loader/flash.bin (m-bootloader installer), −5 CHECK HEAD, −7 unknown loader, −11 loader read-back, −13 no
  "success", −15 Felucca 1.6.1 copies: flash.bin ≠ 0x93000 (Open V12 0x6F000 / V14 0x73000). The app detects −5
  by the read address (the package's boot area) and explains the others in the log.
* Planner: warning before installations that change the boot area (m-bootloader, Open), and at the V15
  intermediate step.
* **M-Flasher** (jvitkauskas, MIT, Rust; ARM64 .deb, glibc ≥ 2.35 → Bookworm ok): download-mode tool with a
  flash map; soft key `F0 22 24 35 7D F7` [V src/midi.rs] sends a running original/Felucca FM-1 into
  ROM download mode; Felucca/SLOOP: OCT−+OCT+ 5 s; m-bootloader: REC at power-on. Decision: do not
  embed it (Rust binary, its own UI) – the rescue page detects `m-flasher` in the PATH and starts it,
  otherwise a link + apt hint. The built-in route remains MvaveFM1Unbricker (Python, testable with a mock).
* Hub rescue `fm1_rescue.py` (GPL-3.0): not fetched/checked (budget) – open as a cross-check.

## 17. V16 and flash safety (analysis and integration, 31 Oct 2026)

Confidence levels: **[Paket]** (package) evidenced by packages/source code · **[Aufz.]** (recording) recorded on
real hardware · **[Emu]** observed in the emulator · **[Annahme]** (assumption) not checked. Module:
`fm1companion/flash/flashmodel.py`, profiles: `fm1companion/data/known_packages.json` (generated with
`tools/make_known_packages.py`), tests: `tests/test_flashmodel.py` (real packages via `FM1_TEST_PKGDIR`, otherwise
skipped), `tests/ui_v16_test.py`. Basis: the hand-over `fm1_flash_safety/INTEGRATION.md` (a separate analysis) –
recalculated here.

### 17.1 Recalculated with the real packages

| Package | Identity | SHA-256 | flash.bin | Layout | Boot head |
|---|---|---|---|---|---|
| V15 (`FM-1_firmware_15.zip` = `FM-1.fwsc`) | FM-1_015 | db1642b2… | 0x93000 | v15 | d9f43191… |
| V16 | FM-1_016 | d017fb65… | 0xA9000 | v16 (VM 0xBA000, SEQ64 0xDA000, BTIF 0xEC000, USER 0xED000) | 8f5228c2… |
| Melodee 1.5.1 | FM-1_91051 | 172afb5b… | 0x93000 | v15 | 53144a0a… |
| **Melodee 1.6.0-rc.7** (new, hash from the release's SHA256SUMS) | FM-1_91060 | 607f746a… | 0x93000 | v15 | 53144a0a… |
| SLOOP 2.8.3 / Skiff 2.0.3 / JIANT 0.9.9 | FM-1_900 / _720 / _909 | as in the hand-over | 0x93000 | v15 | 53144a0a… |

Confirmed [Paket]: all identities, hashes, lengths and layouts of the hand-over. V16 app directory: `app.bin` up to
0x960E8, `DX7.bin` 0x96267–0xA8071, PRCT 0x0–0xA9000. V15 additionally brings a USR entry (73 KB factory voices),
which the recorded update never requests; V16 has none (the voices are in `DX7.bin` in the app area).

**Corrections to the hand-over:**
1. *"The boot head remains Melodee's in all cases"* is only true in the model. Real devices carry the **factory
   boot head** (d9f43191…, byte-identical in V13 = FM-1_009, V14 and V15 [Paket: FM-1-RE packages]), because
   Melodee's own loader (`firmware/loader/ldr_core.c`: "the flash head is never written") [Paket] never writes it
   and the normal update does not request it when the head is the same [Aufz.] – after a V16 update this is open
   (point 4). `installed_state(…, head=…)` now models this. Recalculated (acceptance): V13/V14 have `flash.bin` =
   0x94000 (VM from 0x94000), V15 and all community packages 0x93000.
2. **Melodee → V16 directly does not work** [Paket: `melodee/firmware/src/ota.c`]: before the original loader,
   Melodee compares the first 0x4000 bytes of the package with the device's boot head; V16's head ≠ factory head →
   −5. The plan therefore enforces V15 as the intermediate step (fixed, `planner.v16_switch`), the simulation refuses
   Melodee → V16 with −5. For V16 → non-V15 also fixed via V15 [Annahme, cautious: whether V16 accepts a Felucca
   loader is unknown].
3. **Staging depends on the running firmware, not on the package**: Melodee (and, according to the Unbricker, the
   Felucca family) stores the package's loader at 0xE0000–0xE4FFF, the update record at 0xE4F00 [Paket]. Where
   original V15/V16 store it is open (FM-1-RE: "VM/loader flash area").
4. Conflict in the sources: Melodee's `ota.c` says the original loader "rewrites the whole flash.bin including the
   head"; the recording shows that the area 0x0–0x3FFF is not requested in step 2. **Re-check
   (acceptance):** the recording proves this only for the case "head in the device = head in the package" (V15 on
   a device with the factory head). Step 2 reads the head entries at 0x0 and 0x4000 and plans accordingly
   (`verify_and_plan_update`, SDK lineage: areas with the same CRC are skipped) – so with V16 and a **different**
   head the loader may well request and write the head. This is **open** [Annahme in both directions]; dump A ↔ B
   settles it. Consequences: the dump check no longer calls a V16 head "ROM boot mode only"; the way back via V15
   remains correct (the original loader would then also write the V15 head back). An abort during a head write
   would be worse than one in the app area – one more reason for "Jetzt nicht abstecken" (do not unplug now).

### 17.2 Loader `ota.bin`

* Byte-identical in V13, V14, V15 and V16 (SHA-256 ef1f2db8…, = FM-1-RE `ota_stock.bin`) [Paket].
* Start/end from the package: the check of the file headers looks for `flash.bin`, `app_dir_head`, `uboot.boot`;
  the recorded step 2 requests exactly `flash.bin[0x4000:end)` in descending order [Aufz.]. For V16 therefore
  0x4000–0xA8FFF [derived].
* Erasing: the listing calls `prepare_flash_regions` (+0x4536) **before** `write_update_regions` (+0x47C0) – this
  fits "erase the whole area first" (SDK names). FM-1-RE itself lists the erase effect as unconfirmed
  ("Confirm the erase, write, metadata … side effects") → remains **[Annahme]**; `install_ota` models erasing
  up front.

### 17.3 Hypothesis "truncated V16" (from a user report)

* Only writers in ROM boot mode truncate at 0x93000: the Unbricker (`FW_LEN = 0x8F000`) without `--verify-v15` also
  accepts FM-1_016 ("official-other", warning only) and writes 0x4000–0x92FFF [Paket]. The app always calls it with
  `--verify-v15` and only with the downloaded V15; in addition `uboot.Runner.restore` refuses packages beyond
  0x92FFF (`TOOL_TRUNCATES`).
* Web installers cannot truncate: the device requests, the computer delivers; which areas are written is decided
  by the loader in the device [Aufz.]. Melodee's web installer only accepts V15 as an original package
  (`web/fm1pkg.js validateStockPackage`) [Paket]; Melodee's own loader refuses `flash.bin` ≠ 0x93000 (−3)
  [Paket]; M-Flasher writes `flash.bin` completely [Paket]. FM-SWITCH and thefirmwarehub: source code not available.
* A truncated V16 would have no complete `app.bin` (end 0x960E8) and no factory voices. Since in the reported case
  V16 was used but ROM boot mode never was, the hypothesis is **unlikely** [derived]. The "gaps in the presets" fit
  better with V16's first-start formatting or with V15/Melodee data that V16 finds in USER 0xF0000–0xFEFFF
  [Annahme]. The model covers the case (`(v16, "from4000", 0x93000)`), the dump check detects it (app area up to
  0x92FFF = V16, the rest not) and reports red.

### 17.4 Melodee rc.7: resources 0xCB000–0xDFFFF

* Memory map rc.7 (`storage.c`): Prophet banks 0x93000–0x9CFFF, extension 0x9D000, FM6 bank 0x9F000, pool
  0xA0000–0xC7FFF and 0xEA000–0xF9FFF, CZ 0xC8000, resources 0xCB000–0xDFFFF, staging 0xE0000–0xE4FFF,
  settings 0xFC000 [Paket].
* A V16 install (up to 0xA8FFF) hits the Prophet banks, the FM6 bank and the start of the pool; V16's first start
  [Emu] hits the pool, the CZ banks, the **last six resource sectors 0xDA000–0xDFFFF** and the staging area.
* Command 82 (`fm1_resources.py`): writing erases all 21 sectors, writes the payload and the header last →
  fully heals the resource area. Status (argument 0, read only) returns `installed` (= header + CRC-32 of the
  payload match this build's `res_want`) as well as the build's length/CRC; rc.7: 53 844 bytes, CRC 2035d335
  [Paket]. Not healed: user data (restore a backup) and V16 leftovers in 0x93000–0xA8FFF.

### 17.5 What the app does now

* **Pre-check** (`planner.plan_install` with `flashmodel`): identity only from the package – a file name with a
  different version blocks (`NAME_IDENTITY_MISMATCH`); unknown hash → confirmation "unknown file" (`HASH_UNKNOWN`);
  switching V15 layout ↔ V16 without a backup although one would be possible → blocked (`NO_BACKUP`); for firmwares
  without an editor protocol (V16 itself, original V15) it stays at the confirmation "without backup", otherwise
  there would be no way back; `LAYOUT_CHANGE`/`OVERWRITES_DATA` → "Was danach nicht mehr auf dem Gerät ist" (what
  will no longer be on the device afterwards) with areas and Melodee names; `TOOL_TRUNCATES` → the Unbricker route
  never writes V16. `HEAD_NOT_WRITTEN` is irrelevant on the normal route.
* **Installing V16**: "experimental – only with a backup", fixed via V15, while writing a red notice "Jetzt
  nicht abstecken" (do not unplug now) and no cancelling; the completion sentence names what was checked
  (checksum, identity) and what was not.
* **Way back**: banner in Firmware+ › "Installieren & Versionen" (Install & Versions) when `FM-1_016` is running;
  dialog "Rückweg von V16" (way back from V16: target firmware, version, suggestion from the firmware history) →
  normal plan, fixed via V15, then resources.
* **"Ist mein FM-1 sauber?"** (Is my FM-1 clean?, Rescue): quick check (identity, version, command 82) and dump
  check (file or Unbricker `backup` in ROM boot mode) with a traffic light, sentences, "not covered" and details.
  Demo: three example dumps.
* **Clean up leftovers** (`flash/restzone.py`, card "Ist mein FM-1 sauber?" › expert mode) – **untested on a real
  device**, only checked with the Unbricker's mock FM-1 (`tests/test_restzone.py`, `tests/ui_v16_test.py`).
  * Primitive: jl-uboot-tool `JL_LoaderV2.flash_erase_sector` (command 0xFB01, 4 KiB) via the unmodified
    Unbricker function `erase_repair_sector` (the same as `repair-settings`, upstream only tested with the mock)
    including its gates `_check_mutable`/`_check_repairable`. Nothing is ever written. That the WL82 loader erases
    exactly 4 KiB is an **[Annahme]** (assumption); after each sector the whole 64 KiB block is compared and the
    process stops immediately.
  * Hard rules in the code: only whole sectors in 0x93000–0xA8FFF (`gate`), never NEVER_ERASE/Bluetooth; only with
    a byte-exactly known firmware with V15 layout in the app area (`_app_match`), never with V16/unknown; only
    sectors that, according to the dump of **the same session** (`take_backup`: read twice, identical, saved, read
    back), still carry V16 bytes – fully (profile hash or package) or partially (`_sector_blocks_match` or
    `_sector_matches`). Before erasing, the sector is read again and must equal the dump; afterwards it must read
    empty twice; at the end the whole flash is compared with what is expected (`full-flash-after.bin`).
  * Two runs, two backups: `plan` (erase nothing) → confirmation with the sector list and a typed word → `clean
    --only …` (only the intersection of the confirmation and a fresh plan is erased).
  * Journal `Rettung/Reste-aufräumen/reste-journal.json` (atomic, fsync) before and after each sector. Resuming: a
    sector in state "loeschen" (erasing) only counts as a leftover if the original was V16 according to the journal
    backup (checksum), the app area is the same and since then exclusively bits 0→1 have changed (≥ 16 bytes left).
  * Boot head: neither read nor evaluated (whether V16 writes it via the normal update is open) and stays
    NEVER_ERASE.
  * root: like the other commands via `ROOT_HELPER`; `restzone.py`, `flashmodel.py` and `known_packages.json` are
    in `uboot_manifest.OWN` with checksums (regenerate after every change, a test checks it).

### 17.6 Open – can be settled with dumps of an expendable FM-1 (help page "V16 – Hinweis", test plan A–D)

a) Does V16 really write 0x4000–0xA8FFF, does the boot head stay the same (A ↔ B)? b) Does the loader erase up front
(only visible with a deliberate abort – not recommended)? c) Staging of the original firmware (0xE0000–0xE4FFF
before/after)? d) Do the leftovers in C and D remain, does rc.7 run with them? e) Does V16 also format on the second
start? Afterwards correct `RUNTIME`, `STAGING` and `install_ota` in `flashmodel.py`.
