# FM-1 firmware installation – protocol, loaders, safety (installer workstream)

Status: research notes for `fm1companion/flash/`. **Almost nothing here has touched a real FM-1** – the one
exception is an update within Melodee (1.6.0-rc.8 → 1.6.0), see `docs/UNTESTED.md`; per kind of step the app keeps
this in `fm1companion/flash/tested.py` and shows it before the click.
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
* **App semantics (decision):** `requires_stock_v15` → on a switch, the device must report `FM-1_015` when the
  target install starts (an update within the same firmware goes directly, §12). Otherwise the plan first installs the hash-verified official V15
  (`db1642b2…`, own stock loader), verifies `FM-1_015`, then installs the target. TransitFM1 also
  reports `FM-1_015`; that is its documented purpose, so it is accepted.
* Builtin Melodee/Skiff/SLOOP/KARMA specs keep `requires_stock_v15 = False` (their own installers handle
  Felucca-family → Felucca-family switches; Melodee's own step 1 accepts Felucca-family loaders). A switch to them
  goes via V15 anyway by the policy of §12; an update within them does not.
* Direct switches to/from stock V16: V16 uses the same stock loader [V catalog loaderSHA256]. Returning
  to V16 from Melodee goes through Melodee's CHECK HEAD (V16's boot area may differ → FAILED −5,
  nothing written) [A]; the app therefore recommends V15 as the way back and treats V16 as optional.
  M-VAVE replaces the V16 file at its URL – the app always checks sha256 `d017fb65…` and refuses a
  mismatch.

## 10. Risks and mitigations in the app

| risk | mitigation |
|---|---|
| wrong/corrupt package | sha256 vs spec/catalog; unknown files need an explicit "I know what I am doing"; UFW header/entry CRC check (from SLOOP `package_damage`) |
| package for another model | product must match `FM-1_…`; device model compared before step 1 |
| package sent to a foreign loader | expected loader family per package; refuse otherwise (nothing sent) |
| interrupted step 2 | journal + recovery wizard resumes with the same package in its own loader |
| non-resumable loader | explicit power/battery/cable confirmation |
| two installs at once | process lock file + in-process lock |
| several FM-1 attached | the target is pinned by its USB path (sysfs; otherwise a unique serial number) for every step and across the re-enumerations; if it cannot be pinned and more than one candidate is connected, nothing is sent. Unpinned (no USB details at all): a second FM-1 that answers next to the freshly started loader stops the install before step 2 – a second device that *replaces* the target while it restarts cannot be told apart there |
| plan made for another device state | `Engine.write(expect_identity=…)`: the identity the plan was made for (after the V15 step: `FM-1_015`) is compared with the handshake when writing starts; a difference sends nothing |
| Cancel after the start | honoured until the first UPGRADE; with a running Felucca-family firmware also during step 1 (the host stops answering before it confirms `0xE0000000`; ota.c then erases the staging area and carries on – "Nothing is committed before the host's success", after up to 4 × 3 × 5 s). With the original firmware as the running firmware (its step 1 is unknown [A]) the button is switched off when UPGRADE is sent. The dialog never accepts a cancel the engine cannot honour |
| the host's last answer of step 1 is lost | the port stays open after `0xE0000000` was answered until the device leaves the bus (at most 6 s more per repeat) and a repeat is answered again; if no loader appears and the FM-1 still answers with its start identity, the result is "nothing was written" (`notstaged`, safe) instead of "paused" |
| a download breaks off | `firmware._fetch`: https only (redirects too), at most 16 MiB, kept only when `Content-Length` bytes arrived; a cached package no checksum vouches for is re-read (`ufw_check`) and fetched again when damaged |
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

## 12. Intermediate step via original V15 (owner's policy, implemented in `flash/planner.py`)

**Policy: "always flash to V15 first – except updates within the same custom firmware".** It applies to every
install path (Firmware+ › Install & Versions, the catalog's *Install …*, link import, own .fwsc, *Way back from V16*);
Recovery's *Finish installation* is unchanged (it writes exactly the interrupted package).

| Case (`planner.v15_option`) | V15 step |
|---|---|
| Update / downgrade / reinstall of the **same firmware** (`planner.same_firmware`), also when the catalog sets `requires_stock_v15` (the device already runs that firmware and its own update loader) | `none` – directly |
| Switch custom A → custom B (a fork is another firmware: SLOOP → KARMA), stock V13/V14 → custom, own file (key `eigene`) | `default_on` – V15 first, expert opt-out |
| Running firmware **not recognised** (no key, "?", or the INFO answer names another firmware than the profile) | `default_on` – counts as a switch |
| Device already runs `FM-1_015`, target is original V15 itself, device in update mode | `none` |
| **Switch** to a target with `requires_stock_v15` (catalog) | `locked` |
| V16 switch (`planner.v16_switch`: to V16 from a non-stock firmware, from V16 to anything but V15) | `locked` (unchanged) |
| Target stock V16 from stock | `none` (unchanged) |

"Same firmware" uses the robust recognition: `planner.running_firmware` normalises the recorded key through
`fwfamily.by_key` (device names such as `jiant` = catalog key `jiant-fm`) and checks it against what the INFO answer
names (`fwfamily.by_info`); a conflict (stale profile) means "unknown" and so a switch.

The plan says what happens and why, and nothing more than is known (`planner.V15_WHY`, `V15_STEP_FACTS`):
"Switching from X to Y: the app installs the original firmware V15 first, then Y. Why: firmware authors expect a
start from the original V15, and a direct switch from another custom firmware has bricked a device at least once."
Sources: JIANT's README asks for V15 first when coming from another custom firmware ("a brick was reported going
straight from one") [A]; Melodee's README recommends a stop between firmwares too (TransitFM1, not offered by this
app) [A]. What the plan does **not** claim any more: that the V15 step removes leftovers. It does not – the stock
loader's recorded step 2 requests exactly `flash.bin[0x4000:end)`, i.e. 0x4000–0x92FFF for V15 (§17.2); the data
areas above stay as they are, and V15 may read them as its own settings (presets can look garbled until the target
is installed). The plan also says that the V15 step itself has no start protection (Melodee RECOVERY.md: "Restoring
stock also removes Melodee's application boot protection; keep power connected throughout the update") and that
this two-step path has not been run on a real FM-1 by this app. If the running firmware refuses V15 (CHECK HEAD,
FAILED −5, §16), nothing is written, the chain is cleared and the message names what is left: the expert option for
`default_on`, download mode with another tool (M-Flasher) for `locked`. The checkbox *Expert: install directly without the V15 step*
(`plan_install(via_v15=False)`, only for `default_on`) is off by default, is **not remembered** (no per-switch memory,
no setting) and adds a warning to the plan.

Order: **Backup (Felucca protocol) → load and check every package → V15 → target → resources → check identity.**
Reason [V Unbricker flash map]: Felucca data lives at 0x97000..0xDFFFF / 0xE0000 / 0xFC000 and overlaps
the original firmware's settings storage 0x93000..0xE8FFF – V15 can overwrite it. The dialog says:
"Your projects on the device can be lost during the intermediate step – the app backs them up first."

**Two-step chain in the journal.** Before the V15 step the journal records the target
(`journal.json` `"chain": {"key", "name", "tag", "file", "device_id", "usb_path", "started"}`); it is cleared once
the target is written – by the install flow or by Recovery's *Finish installation* –, when the V15 step failed
safely (nothing written), or when the user presses *Dismiss*; it is ignored after 14 days and matched to the
connected FM-1 by USB path or profile. Interrupted after V15 (cancel, failure of the target
step, or the V15 step finished on the Recovery page): the FM-1 runs `FM-1_015`, Install & Versions shows *Continue:
install Y* and the plan (`plan_install(chain=…)`, `InstallPlan.chain_resume`) installs Y directly – the V15 step is
not repeated. If you stay on V15 and the presets are garbled: System > Reset Patches.

## 13. Rescue level 2 – ROM boot mode "WL82 UBOOT1.00" (4c4a:8057)

* [V MvaveFM1Unbricker 1.2.0 (bundled since 2.6.1: 1.2.1, see §19), MIT, Michael P Jones; Reddit guide u/acrawf1] An FM-1 whose firmware does not
  start appears as the USB mass storage device "WL82 UBOOT1.00". MIDI installers then report "FM-1 not found".
  kagaimiq's jl-uboot-tool (MIT) loads a RAM loader (JieLi `wl82loader.bin`, sha256 d41da612…) and can
  read/erase/write the 1 MiB flash (SCSI via `/dev/sgN`, on Linux root + module `sg`).
* Procedure (all in the unmodified `vendor/unbricker/fm1_unbrick.py`): exactly one WL82 device; chip key **0x980F**
  and flash ID **0x856014** or, from the bundled 1.2.1, **0xB36014** (§19; no override); read the whole flash twice, compare, save;
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
  "Emergency full backup", never automatically.

## 14. Rescue level 3 – FM-1 Transporter (hardware)

[V TRANSPORTER-GUIDE.md] If the FM-1 never appears as WL82: a Seeed XIAO RP2040 on the USB data lines;
it sends the USB_KEY at power-on and forces ROM boot mode. Wiring: **D6 (GP0) → D+ (green),
D7 (GP1) → D− (white), GND → GND (black), do not connect VBUS (red).** Flash the UF2 with the BOOT button held;
then `fm1_transporter_recover.py` (rekey → info: `key=980F id=856014`, on a newer FM-1 `id=B36014` → dump → restore --write).
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
* **M-Flasher, m-bootloader and SLOOP 3 – bundled and driven** (decision 2026-11-19, replacing the earlier "do not
  embed M-Flasher"). The suite does not re-implement these tools; it ships pinned versions, keeps them up to date
  and runs them in the background, as it does with the Unbricker and the firmware editors.
  * **Pins** (`fm1companion/tools/manifest.json`; per file: version, release tag and commit, URL, size, SHA-256,
    licence, and how the hash was checked against upstream):
    M-Flasher 0.12.1 (jvitkauskas/m-flasher, MIT, tag `v0.12.1` = 8fbece7; release tarballs
    `m-flasher-0.12.1-linux-{aarch64,x86_64}.tar.gz`, binary `m-flasher/m-flasher`; upstream publishes no
    SHA256SUMS, the values were measured on the HTTPS download and `tools/update_tool_pins.py --check` compares
    them with GitHub's per-file digest in the release workflow), m-bootloader 2.4 (jvitkauskas/m-bootloader, MIT,
    `v2.4` = 395e6cc; `fm1-uboot.boot` 13 760 B, sha256 677ae52a…49e4, checked against the release's
    `SHA256SUMS` and byte-identical to M-Flasher's built-in `data/bootloader-fm1.boot`), SLOOP 3 3.4.0
    (isod89/sloop3-fm1, GPL-3.0, `v3.4.0-beta` = 2228313; `sloop3-3.4.0.area` 474 706 B, sha256 12312547…5ce1,
    byte-identical to the `AREA` inlined in `index.html` at that commit).
  * **How they reach the user:** m-bootloader's and SLOOP 3's files, all licence and notice files
    (`fm1companion/tools/bundled/`) are in git. M-Flasher's two binaries are **not** (≈ 7.5 + 8.6 MB per
    version): `tools/fetch_tools.py` downloads the pinned tarballs at build time, checks size and SHA-256 of the
    tarball and of the binary, and puts them into `tools-bin/`. The `.deb` stays `Architecture: all` and carries
    both builds under `/usr/lib/fm1-companion/tools-bin/` (postinst checks them again); the `.run` and
    `install.sh` copy only the one for `uname -m` after the same check. So the offline rescue works after a
    normal install. There is **no armhf build** (32-bit Raspberry Pi OS): the app says "not available" and the
    Unbricker route stays. A source checkout without `tools-bin/` says "not bundled – run tools/fetch_tools.py".
    The binaries need glibc ≥ 2.35 (both builds; Bookworm 2.36, Trixie 2.41).
  * **Permissions:** `packaging/udev/70-fm1-companion-download-mode.rules` (`TAG+="uaccess"` for USB `4c4a:8057`,
    the same lines as M-Flasher's own rule) and `fm1-companion-sg.conf` (load `sg` at boot) – installed by the
    `.deb` (active, `/usr/lib/udev/rules.d`, `/usr/lib/modules-load.d`), by `install.sh` (asks, default yes) and the
    graphical installer (checkbox, ticked), removed again by `apt remove` / the uninstaller. `uaccess` covers the
    user at the local, active seat only (not SSH).
  * **Running it** (`fm1companion/tools/runner.py`, `tools/proc.py`): the binary's SHA-256 is checked before
    every run – hashed in place only where nobody but root can change it (the .deb's `/usr/lib`), otherwise
    copied once into a private 0700 folder, and the copy is hashed and run; no shell, a fixed argument list, stdin
    closed, `LANG=C`, a time limit and a cancel button. M-Flasher never outlives the app: `PR_SET_PDEATHSIG`
    (SIGTERM), its own process group (stop = SIGTERM to the group, SIGKILL after 2 s, every wait bounded),
    stopped at app exit; its PID is recorded beside the lock (`tool.pid`), so a lock whose window died is not free
    while that M-Flasher still runs, and a process that does not stop even after SIGKILL keeps the lock until it
    ends ("unplug the FM-1"). Commands that talk to a unit take the one-installation-at-a-time lock
    (`flash/journal.py`); every line goes into a flight log `tool-<time>.txt` with unit ids masked (last four hex
    digits; also with 0x, as spaced or colon-separated bytes and inside file names). **In this version only
    `find`, `info --device /dev/sgN`, `--version` and `--help` can run**; everything that writes or switches
    modes (backup, restore, firmware, bootloader, download-mode, restart, run-ram, `--yes`) is refused before
    the process starts. The parser (`tools/mflasher_parse.py`) reads `find` and the `info` block (model,
    bootloader, firmware *guess*, app, chip key, flash ID, unit id, loader, evidence); M-Flasher has no
    machine-readable output and only exit codes 0/1, so the app judges by the text. Recovery › Tools shows the
    tools and runs `find` ("Which FM-1 does M-Flasher see?").
  * **Updates** (`tools/updates.py`): the same GitHub release query as for firmwares, by default once a day
    (Settings: daily / weekly / only at start / never), only while "look for firmware updates … (asks GitHub)" is
    on, never with `FM1C_NO_NET=1`, never while an installation or tool run holds the lock. A newer version is
    only announced (version + release text); **Update** downloads it into a staging folder, checks it against
    GitHub's per-file digest (m-bootloader: also its `SHA256SUMS`; no digest → refused), checks M-Flasher's
    tarball member, `--version` and the `--help` text (contract `mflasher-0.12`; these two run once, in the staging
    folder, in their own session, with no environment and no stdin), then stores it under
    `DATA_DIR/tools/<tool>/<version>/`. The previous version stays the fallback until the new one has once run
    successfully (a clean `info`); a new version that crashes or prints an unknown format is marked failed and
    the previous one is used again. **A new M-Flasher is used for read-only commands only (owner decision); one
    whose `--help` differs from the known contract is kept but not used at all – the bundled one runs and the Tools
    view says so. Writes only ever run a binary whose SHA-256 an app release lists in `write_verified`
    ({version: {arch: sha256}} – by bytes, not by version number).** An update is vouched for by HTTPS plus
    GitHub's digest only (trust on first use): it runs on this computer for reading; a bundled version is vouched
    for by the app's own release review.
  * **Licences:** `bundled/m-flasher/THIRD-PARTY-LICENSES.txt`, made by `tools/gen_mflasher_licences.py` from
    M-Flasher's Cargo.lock at the pinned commit (every crate from crates.io, checked against Cargo.lock's
    checksums: name, version, declared licence, every licence/copyright/notice text) – including the fonts egui
    compiles in (epaint_default_fonts: Ubuntu Light under the Ubuntu Font Licence 1.0, Hack under MIT + the
    Bitstream Vera licence, Noto Emoji under the OFL 1.1, the emoji icon font by John Slegers under MIT) and
    M-Flasher's own OFL fonts. Shipped next to the binary in the .deb/.run and shown by "Show licence"; the release
    workflow regenerates it and stops when it differs.
  * **Later steps** (not in this version): "Into download mode" (soft key `F0 22 24 35 7D F7`, answered by stock
    015/016, Felucca/Skiff 2.7, SLOOP 2.8.5 [hw], SLOOP 3 [src] and m-bootloader's MIDI MODE [SPEC]; otherwise a
    guided card: Felucca/SLOOP OCT−+OCT+ 5 s, m-bootloader REC at power-on or PLAY → DOWNLOAD MODE), full flash
    backup (two reads compared, stored per device with unit id, flash ID and SHA-256) and restore only after the
    unit id and flash ID match, install m-bootloader (`bootloader --file`, writes only 0x0000–0x3FFF) and remove it
    (owner decision: `bootloader --stock` with the public V15 package's boot area), firmware through download
    mode (`firmware`, from 0x4000 on, boot area untouched), SLOOP 3 over m-bootloader's MIDI MODE from the bundled
    area (writes 0x4000–0x78000 for 3.4.0) – each with a mandatory full backup first and M-Flasher's
    "Type "yes" to write" prompt as the last gate (the app never passes `--yes`; it reads unit id, flash ID and
    the planned ranges of that very session and answers only when they match).
* Hub rescue `fm1_rescue.py` (GPL-3.0): not fetched/checked (budget) – open as a cross-check.

## 17. V16 and flash safety (analysis and integration, 31 Oct 2026)

Confidence levels: **[Package]** evidenced by packages/source code · **[Rec.]** recorded on
real hardware · **[Emu]** observed in the emulator · **[Assumption]** not checked. Module:
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

Confirmed [Package]: all identities, hashes, lengths and layouts of the hand-over. V16 app directory: `app.bin` up to
0x960E8, `DX7.bin` 0x96267–0xA8071, PRCT 0x0–0xA9000. V15 additionally brings a USR entry (73 KB factory voices),
which the recorded update never requests; V16 has none (the voices are in `DX7.bin` in the app area).

**Corrections to the hand-over:**
1. *"The boot head remains Melodee's in all cases"* is only true in the model. Real devices carry the **factory
   boot head** (d9f43191…, byte-identical in V13 = FM-1_009, V14 and V15 [Package: FM-1-RE packages]), because
   Melodee's own loader (`firmware/loader/ldr_core.c`: "the flash head is never written") [Package] never writes it
   and the normal update does not request it when the head is the same [Rec.] – after a V16 update this is open
   (point 4). `installed_state(…, head=…)` now models this. Recalculated (acceptance): V13/V14 have `flash.bin` =
   0x94000 (VM from 0x94000), V15 and all community packages 0x93000.
2. **Melodee → V16 directly does not work** [Package: `melodee/firmware/src/ota.c`]: before the original loader,
   Melodee compares the first 0x4000 bytes of the package with the device's boot head; V16's head ≠ factory head →
   −5. The plan therefore enforces V15 as the intermediate step (fixed, `planner.v16_switch`), the simulation refuses
   Melodee → V16 with −5. For V16 → non-V15 also fixed via V15 [Assumption, cautious: whether V16 accepts a Felucca
   loader is unknown].
3. **Staging depends on the running firmware, not on the package**: Melodee (and, according to the Unbricker, the
   Felucca family) stores the package's loader at 0xE0000–0xE4FFF, the update record at 0xE4F00 [Package]. Where
   original V15/V16 store it is open (FM-1-RE: "VM/loader flash area").
4. Conflict in the sources: Melodee's `ota.c` says the original loader "rewrites the whole flash.bin including the
   head"; the recording shows that the area 0x0–0x3FFF is not requested in step 2. **Re-check
   (acceptance):** the recording proves this only for the case "head in the device = head in the package" (V15 on
   a device with the factory head). Step 2 reads the head entries at 0x0 and 0x4000 and plans accordingly
   (`verify_and_plan_update`, SDK lineage: areas with the same CRC are skipped) – so with V16 and a **different**
   head the loader may well request and write the head. This is **open** [Assumption in both directions]; dump A ↔ B
   settles it. Consequences: the dump check no longer calls a V16 head "ROM boot mode only"; the way back via V15
   remains correct (the original loader would then also write the V15 head back). An abort during a head write
   would be worse than one in the app area – one more reason for "Do not unplug now".

### 17.2 Loader `ota.bin`

* Byte-identical in V13, V14, V15 and V16 (SHA-256 ef1f2db8…, = FM-1-RE `ota_stock.bin`) [Package].
* Start/end from the package: the check of the file headers looks for `flash.bin`, `app_dir_head`, `uboot.boot`;
  the recorded step 2 requests exactly `flash.bin[0x4000:end)` in descending order [Rec.]. For V16 therefore
  0x4000–0xA8FFF [derived].
* Erasing: the listing calls `prepare_flash_regions` (+0x4536) **before** `write_update_regions` (+0x47C0) – this
  fits "erase the whole area first" (SDK names). FM-1-RE itself lists the erase effect as unconfirmed
  ("Confirm the erase, write, metadata … side effects") → remains **[Assumption]**; `install_ota` models erasing
  up front.

### 17.3 Hypothesis "truncated V16" (from a user report)

* Only writers in ROM boot mode truncate at 0x93000: the Unbricker (`FW_LEN = 0x8F000`) without `--verify-v15` also
  accepts FM-1_016 ("official-other", warning only) and writes 0x4000–0x92FFF [Package]. The app always calls it with
  `--verify-v15` and only with the downloaded V15; in addition `uboot.Runner.restore` refuses packages beyond
  0x92FFF (`TOOL_TRUNCATES`).
* Web installers cannot truncate: the device requests, the computer delivers; which areas are written is decided
  by the loader in the device [Rec.]. Melodee's web installer only accepts V15 as an original package
  (`web/fm1pkg.js validateStockPackage`) [Package]; Melodee's own loader refuses `flash.bin` ≠ 0x93000 (−3)
  [Package]; M-Flasher writes `flash.bin` completely [Package]. FM-SWITCH and thefirmwarehub: source code not available.
* A truncated V16 would have no complete `app.bin` (end 0x960E8) and no factory voices. Since in the reported case
  V16 was used but ROM boot mode never was, the hypothesis is **unlikely** [derived]. The "gaps in the presets" fit
  better with V16's first-start formatting or with V15/Melodee data that V16 finds in USER 0xF0000–0xFEFFF
  [Assumption]. The model covers the case (`(v16, "from4000", 0x93000)`), the dump check detects it (app area up to
  0x92FFF = V16, the rest not) and reports red.

### 17.4 Melodee rc.7: resources 0xCB000–0xDFFFF

* Memory map rc.7 (`storage.c`): Prophet banks 0x93000–0x9CFFF, extension 0x9D000, FM6 bank 0x9F000, pool
  0xA0000–0xC7FFF and 0xEA000–0xF9FFF, CZ 0xC8000, resources 0xCB000–0xDFFFF, staging 0xE0000–0xE4FFF,
  settings 0xFC000 [Package].
* A V16 install (up to 0xA8FFF) hits the Prophet banks, the FM6 bank and the start of the pool; V16's first start
  [Emu] hits the pool, the CZ banks, the **last six resource sectors 0xDA000–0xDFFFF** and the staging area.
* Command 82 (`fm1_resources.py`): writing erases all 21 sectors, writes the payload and the header last →
  fully heals the resource area. Status (argument 0, read only) returns `installed` (= header + CRC-32 of the
  payload match this build's `res_want`) as well as the build's length/CRC; rc.7: 53 844 bytes, CRC 2035d335
  [Package]. Not healed: user data (restore a backup) and V16 leftovers in 0x93000–0xA8FFF.

### 17.5 What the app does now

* **Pre-check** (`planner.plan_install` with `flashmodel`): identity only from the package – a file name with a
  different version blocks (`NAME_IDENTITY_MISMATCH`); unknown hash → confirmation "unknown file" (`HASH_UNKNOWN`);
  switching V15 layout ↔ V16 without a backup although one would be possible → blocked (`NO_BACKUP`); for firmwares
  without an editor protocol (V16 itself, original V15) it stays at the confirmation "without backup", otherwise
  there would be no way back; `LAYOUT_CHANGE`/`OVERWRITES_DATA` → "What is no longer on the device afterwards" with areas and Melodee names; `TOOL_TRUNCATES` → the Unbricker route
  never writes V16. `HEAD_NOT_WRITTEN` is irrelevant on the normal route.
* **Installing V16**: "experimental – only with a backup", fixed via V15, while writing a red notice "Do not
  unplug now" and no cancelling; the completion sentence names what was checked
  (checksum, identity) and what was not.
* **Way back**: banner in Firmware+ › "Install & Versions" when `FM-1_016` is running;
  dialog "Way back from V16" (target firmware, version, suggestion from the firmware history) →
  normal plan, fixed via V15, then resources.
* **"Is my FM-1 clean?"** (Rescue): quick check (identity, version, command 82) and dump
  check (file or Unbricker `backup` in ROM boot mode) with a traffic light, sentences, "not covered" and details.
  Demo: three example dumps.
* **Clean up leftovers** (`flash/restzone.py`, card "Is my FM-1 clean?" › expert mode) – **untested on a real
  device**, only checked with the Unbricker's mock FM-1 (`tests/test_restzone.py`, `tests/ui_v16_test.py`).
  * Primitive: jl-uboot-tool `JL_LoaderV2.flash_erase_sector` (command 0xFB01, 4 KiB) via the unmodified
    Unbricker function `erase_repair_sector` (the same as `repair-settings`, upstream only tested with the mock)
    including its gates `_check_mutable`/`_check_repairable`. Nothing is ever written. That the WL82 loader erases
    exactly 4 KiB is an **[Assumption]**; after each sector the whole 64 KiB block is compared and the
    process stops immediately.
  * Hard rules in the code: only whole sectors in 0x93000–0xA8FFF (`gate`), never NEVER_ERASE/Bluetooth; only with
    a byte-exactly known firmware with V15 layout in the app area (`_app_match`), never with V16/unknown; only
    sectors that, according to the dump of **the same session** (`take_backup`: read twice, identical, saved, read
    back), still carry V16 bytes – fully (profile hash or package) or partially (`_sector_blocks_match` or
    `_sector_matches`). Before erasing, the sector is read again and must equal the dump; afterwards it must read
    empty twice; at the end the whole flash is compared with what is expected (`full-flash-after.bin`).
  * Two runs, two backups: `plan` (erase nothing) → confirmation with the sector list and a typed word → `clean
    --only …` (only the intersection of the confirmation and a fresh plan is erased).
  * Journal `Rettung/Reste-aufräumen/reste-journal.json` (real folder names: recovery/clean-up-leftovers; atomic, fsync) before and after each sector. Resuming: a
    sector in state "loeschen" (erasing; internal value) only counts as a leftover if the original was V16 according to the journal
    backup (checksum), the app area is the same and since then exclusively bits 0→1 have changed (≥ 16 bytes left).
  * Boot head: neither read nor evaluated (whether V16 writes it via the normal update is open) and stays
    NEVER_ERASE.
  * root: like the other commands via `ROOT_HELPER`; `restzone.py`, `flashmodel.py` and `known_packages.json` are
    in `uboot_manifest.OWN` with checksums (regenerate after every change, a test checks it).

### 17.6 Open – can be settled with dumps of an expendable FM-1 (help page "V16 – note", test plan A–D)

a) Does V16 really write 0x4000–0xA8FFF, does the boot head stay the same (A ↔ B)? b) Does the loader erase up front
(only visible with a deliberate abort – not recommended)? c) Staging of the original firmware (0xE0000–0xE4FFF
before/after)? d) Do the leftovers in C and D remain, does rc.7 run with them? e) Does V16 also format on the second
start? Afterwards correct `RUNTIME`, `STAGING` and `install_ota` in `flashmodel.py`.

## 18. USB hubs: careful mode, automatic resume, flash log (`flash/robust.py`)

User report (Pi 5): directly at the Pi the installation works; through an active USB hub the FM-1 is detected and
step 1 starts, but the installation does not finish. Failure modes and what the app does:

* [V melodee ota.c `ota_read`] the device repeats a read request after 5 s without a matching answer, 3 tries,
  then the session ends (step 1: old firmware keeps running, nothing written; step 2: loader back in its main loop,
  update mode stays [V loader.c `ldr_main`]). Step-1 session deadline 120 s. **App:** answers every repetition;
  careful idle limits 16 s (step 1) and 20 s (Felucca loader in step 2: its session has certainly ended) instead of
  8 s / 180 s.
* [V usb.c] the device holds **one** received SysEx frame; frames arriving while one is pending are dropped. A
  late, stale answer can therefore push out the next real one. **App:** copies of a request that piled up are
  answered once; an already answered request followed by a newer one is skipped (the device has moved on);
  careful mode keeps ≥ 30 ms between two responses (one in flight at a time).
* [V usb.c `ota_wire_send`] the device aborts if its TX queue stays full > 200 ms. On Linux snd-usb-audio keeps
  IN URBs queued while the ALSA input is open; the rtmidi callback (`MidoLink._rx`) only puts into a queue and
  never blocks; nothing sleeps with a closed input while the device is sending (the input is opened before
  UPGRADE/handshake). Not changed.
* [V mido rtmidi backend] `get_input_names()` creates a fresh rtmidi client on every call – the port list is
  current after re-enumeration; no extra client needed.
* Re-enumeration (hub glitch, power dip): the port disappears – or reappears under the same ALSA name (card
  number reused). **App:** `WatchedLink` also compares the USB device number (sysfs `devnum`); careful mode waits
  up to 90 s for **the pinned device** in update mode with **its own loader** (package rule, same model) and sends
  UPGRADE again – the same resume as Recovery › Finish installation, up to 3 times. Step 1 is repeated (max. 2)
  only after a dropout or repeated requests, never after a plain stop (that is the device refusing the package).
  Without a pin nothing is repeated.
* Slower re-enumeration: waits for loader/restart 120 s in careful mode (30/40 s otherwise).
* Autosuspend: hubs default to `power/control=auto` (kernel hub driver). Reported in plan and system check;
  optional udev rule `packaging/udev/60-fm1-companion.rules` (FM-1 IDs + every hub on its path → `on`), installed
  only on request.

Careful mode: Settings › "Careful USB mode for flashing" = auto (FM-1 behind a hub, `devices.usb_link`) / always /
never. Flash log: `DATA_DIR/flash/logs/flash-*.txt` (every request with gap, repetitions, duplicates, port
events, resumes, kernel lines of the time window), last 3 in the bug report ZIP (`flash/`), redacted.
Tests: `tests/test_usb_hub.py` (`sim.SimFaults`).

## 19. The newer FM-1: flash chip UC25HQ80, JEDEC 0xB36014 (2026-11-17)

**Facts, with their sources** (repositories as cloned on 2026-11-17; nothing here was run on such a unit by this app):

* FM-1s sold from late 2026 – "the ones with the logo on the front" – carry another maker's 1 MiB SPI NOR: the JEDEC
  ID reads `B3 60 14` (UCUN UC25HQ80) instead of Puya's `85 60 14`; the type and capacity bytes are the same
  [V ChoralRoot `docs/INSTALL-COMPAT.md` "A second flash chip", HISTORY 3y222; MvaveFM1Unbricker a1eaf4d;
  fm1-base 6d6ae6d; Melodee PR #111].
* The Felucca family reads the ID once at power-on and, on an ID it does not know, runs **RAM-only**: nothing is read
  from or written to the flash, and most of them skip the update session too – an installer sends UPGRADE and the unit
  never asks for the first block ("stopped answering after 0 requests") [V ChoralRoot as above; Felucca README 1.8.2;
  SLOOP 2.8.5 notes; TransitFM1 `docs/SAFETY.md` "The flash chip": 0.1.3 shows UNKNOWN FLASH CHIP and its loader
  refuses too]. So a too-old version on a newer unit **strands** it: the only way back is the chip's ROM boot
  (§13) and writing the original V15 from there.
* What the firmwares do on the chip once they know it: only generic SPI NOR commands (9F, 05/35, 06, 20, 02, 0B, 4B)
  [V ChoralRoot, Hortator ac55f47]. The part can be slower to erase or program than the Puya part [V ChoralRoot
  HISTORY 3y264, felucca-bsp #12] – "not tried on hardware" there.
* **The original firmware.** No source read says which original version the newer units are delivered with. The
  original V15 is named as the way back on exactly these units by Felucca (README), ChoralRoot (releases/0.29.1.md),
  TransitFM1 (SAFETY.md) and the Unbricker (README, site); one such unit took a community firmware through M-VAVE's own
  updater from the original firmware it came with (ChoralRoot HISTORY 3y222). About the V16 package on these units
  nothing was found. m-vave/fm1_open says nothing on the chip.
* **m-bootloader** [V jvitkauskas/m-bootloader README "Little tested on the FM-1's newer flash chip"]: one such unit
  did not start after 1.1 or 1.2 was installed, cause unknown; another runs 1.3 and 2.0 on one owner's report.
  **SLOOP 3** [V its installer page]: "accepts this chip, but it has not been tried on one yet".
* **How to find out which chip a unit has.** Over MIDI the original firmware and most firmwares do not say. Ways that
  exist: (a) ROM boot mode – the Unbricker's `info`/`backup`/`restore` print `Flash ID 0x…`, the Transporter
  `id=…`; (b) the update identity answer (`00 59 11`, 27 text bytes) of fm1-base firmwares built between fm1-base
  19bf320 (2026-11-05) and 3ae2a65 (2026-11-06) carries the ID in text bytes 24..26 (felucca-bsp#29: AMB-1 1.3.1,
  LOOM-8 0.13 …; taken out again because installers that strip only trailing zeros took the answer for no FM-1 – this
  app did so too until 2.6.1); (c) M-Flasher shows it under Device info (FLASH CHIP), m-bootloader in its menu;
  (d) ChoralRoot before 0.29.1 shows `no unit ID` on such a unit, TransitFM1 before 0.1.4 `UNKNOWN FLASH CHIP`.
  No source documents a difference in the USB descriptors or the serial number.

**What the app does** (`fm1companion/hardware.py`, `flash/planner.py` `_new_chip`, `ui/hw_revision.py`):

* The table `fm1companion/data/hardware.json` holds, per firmware, the first version that accepts 0xB36014, with the
  commit it was read in (`docs/firmware-support.md` shows it). `hardware.support(key, version)` answers yes / no /
  risky / unknown; versions are compared in each firmware's own order (JIANT: V10.6z before V10.7.N; a bare "V10.7"
  is two different builds and counts as unknown).
* The unit's revision lives in the device profile (`hw_revision`, `hw_revision_source`, `flash_id`). It is set by
  (a) and (b) above when the app sees the ID, otherwise by the owner's answer to one question – "Does your FM-1 have
  the M-VAVE logo on the front?" – asked once per device when an installation depends on it, changeable on the pages
  Devices and Overview.
* **"Newer unit" is sticky, the strictest reading wins** (`hardware.set_revision`). Profiles are matched to devices
  loosely (FM-1s have no reliable serial number), so a second FM-1 can appear under a profile: a read or answered
  "earlier" never replaces a recorded "newer" – it is kept as `hw_revision_conflict` and shown ("two devices under one
  profile?"). "Newer" always counts, whoever says it. Only the owner takes "newer" or a read ID away: *Unit …* with
  an explicit confirmation (logged). Merging two profiles keeps the strictest statement.
* A flash ID read in ROM boot mode is stored on a profile only when that profile is unambiguously the unit in ROM
  mode – the device the Recovery page itself sent there (softkey, same USB port), or the only profile seen at that
  USB port in this session. Otherwise it counts for this session's checks at that USB port (`hardware.SESSION_READS`)
  and nothing is stored. Recovery › finish installation judges the FM-1 that waits in update mode, not the active
  device: certain only by a serial number; otherwise unknown at best, and newer if any candidate profile is.
* What feeds the rule: the version is the release tag, the catalog's version or the version a checksum vouches for –
  never a file name or display name; a pre-release or variant of a minimum version ("1.6.0-rc", "-beta", "_rc1") is
  not that version unless the row lists it (`also`: Felucca 1.8.2-beta); a table row applies only to its own
  repository (a fork added under the same key is unknown); only M-VAVE's two original packages count as "the
  original" – another target of the stock loader family is unknown. An unreadable table blocks every plan for a unit
  that is not known to be an earlier one.
* The plan (every way to install goes through it: releases, catalog, link import, own files, the V15 step, "back to
  the original", continuing a switch, finishing an interrupted installation):

  | unit | target accepts the chip | target does not / risky / not known (catalog firmware) | own file, original V16 |
  |---|---|---|---|
  | earlier (no logo) | – | – | – |
  | newer (logo) | – (original V15: a note that this app has not run it there) | **blocked**, no override; names the first version that works | warning + confirmation |
  | not known | – | warning + the confirmation "My FM-1 has NO logo on the front" | warning + confirmation |

  On a newer unit that runs a version without support the plan and the Overview say why saving and updating fail and
  name the way out (ROM boot, original V15, then a version that knows the chip).
* **Recovery.** The bundled MvaveFM1Unbricker is upstream's a1eaf4d (tool version 1.2.1), whose only change against
  the 1.2.0 bundled before is that 0xB36014 is accepted beside 0x856014 (`FLASH_IDS_OK`; the Transporter script the
  same). The files are copied unchanged; the checksums in `flash/uboot_manifest.py` were replaced with theirs. The
  app's own gates (`rescuegate.py`, `restzone.py`, `flashmodel.py`) do not look at the flash ID themselves – they call
  the Unbricker's `check_identity` – and are unchanged. Upstream's later 1.3.0 (full-restore) and 1.4.0 (macOS) are
  not taken over. Writing in ROM boot mode was never run by this app on either chip (`docs/UNTESTED.md`).
