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
| wrong/corrupt package | sha256 vs spec/catalog; unknown files need explicit "Ich weiß, was ich tue"; UFW header/entry CRC check (from SLOOP `package_damage`) |
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

## 12. Zwischenschritt über Original-V15 (Projektvorgabe, umgesetzt in `flash/planner.py`)

| Fall | Option im Plan-Dialog |
|---|---|
| Firmware-Wechsel (anderer Schlüssel) oder aktuelle Firmware unbekannt | **an** (Häkchen gesetzt, abwählbar) – Schutz vor Flüchtigkeitsfehlern |
| Ziel hat `requires_stock_v15` | **an und gesperrt** (Katalog verlangt V15) |
| Update derselben Firmware (z. B. Melodee rc.6 → rc.7) | aus, als Option angeboten |
| Ziel ist Original V15/V16, Gerät läuft schon `FM-1_015`, Gerät im Update-Modus | keine Option |

Reihenfolge: **Backup (Felucca-Protokoll) → Paket laden → V15 → Ziel → Ressourcen → Identität prüfen.**
Grund [V Unbricker-Flash-Karte]: Felucca-Daten liegen bei 0x97000..0xDFFFF / 0xE0000 / 0xFC000 und überlappen
den Einstellungsspeicher der Original-Firmware 0x93000..0xE8FFF – V15 kann sie überschreiben. Der Dialog sagt:
„Deine Projekte auf dem Gerät können beim Zwischenschritt verloren gehen – die App sichert sie vorher.“
Die Wahl wird nur pro Wechsel gemerkt (`config.module("installer")["v15_choice"]["melodee>sloop"]`); es gibt
keine globale Abschaltung. Bleibt man auf V15 und die Presets sind verstümmelt: System > Reset Patches.

## 13. Rettung Stufe 2 – ROM-Bootmodus „WL82 UBOOT1.00“ (4c4a:8057)

* [V MvaveFM1Unbricker 1.2.0, MIT, Michael P Jones; Reddit-Anleitung u/acrawf1] Ein FM-1, dessen Firmware nicht
  startet, erscheint als USB-Massenspeicher „WL82 UBOOT1.00“. MIDI-Installer melden dann „FM-1 not found“.
  kagaimiqs jl-uboot-tool (MIT) lädt einen RAM-Loader (JieLi `wl82loader.bin`, sha256 d41da612…) und kann den
  1-MiB-Flash lesen/löschen/schreiben (SCSI über `/dev/sgN`, unter Linux root + Modul `sg`).
* Ablauf (alles im unveränderten `vendor/unbricker/fm1_unbrick.py`): genau ein WL82-Gerät; Chip-Key **0x980F**
  und Flash-ID **0x856014** (keine Übersteuerung); ganzer Flash zweimal lesen, vergleichen, speichern;
  Firmwarebereich aus der .fwsc extrahieren (`flash.bin[0x4000:0x93000]`; V15 → sha256 **6edf3c37…** [V eigener
  Test]); nur **0x4000..0x92FFF** schreiben; zurücklesen und vergleichen; aus- und einschalten.
  Nie unter 0x4000, nie 0xE9000..0xFBFFF (Bluetooth, Werkssounds) und 0xFF000..; kein Chip-/Block-Löschen.
* App-Zusätze (`flash/uboot.py`): Gerät nur, wenn sysfs-Vorfahr 4c4a:8057 **und** SCSI-Vendor/Model WL82/UBOOT1.00
  (keine freie Auswahl, Größe wird angezeigt); Start über `pkexec`; jl-uboot-tool wird erst nach Zustimmung über
  `fm1_unbrick.py setup --no-pip` auf Commit adb3f18 geholt; Abhängigkeiten per apt (python3-crcmod,
  python3-yaml, python3-pycryptodome [A: liefert das Modul `Cryptodome`], python3-tqdm).
* Felucca 0.9 → 1.0.x [A Reddit]: 1.0.x startet wegen alter 0.9-Daten nicht. Abhilfe dort: 0x97000+0x8000 und
  0xFC000+0x2000 löschen (löscht Songs/Einstellungen). Die App löscht nicht roh; sie warnt im Plan und bietet
  `repair-settings` (setzt diese Fenster mit einem Werks-Dump zurück; upstream nur mit Mock getestet).
* `softkey` schickt ein laufendes Original-FM-1 per MIDI in den ROM-Bootmodus – nur für die freiwillige
  „Notfall-Komplettsicherung“, nie automatisch.

## 14. Rettung Stufe 3 – FM-1 Transporter (Hardware)

[V TRANSPORTER-GUIDE.md] Wenn das FM-1 nie als WL82 erscheint: Seeed XIAO RP2040 an die USB-Datenleitungen;
er sendet beim Einschalten den USB_KEY und erzwingt den ROM-Bootmodus. Verkabelung: **D6 (GP0) → D+ (grün),
D7 (GP1) → D− (weiß), GND → GND (schwarz), VBUS (rot) nicht anschließen.** UF2 mit gedrückter BOOT-Taste
aufspielen; dann `fm1_transporter_recover.py` (rekey → info: `key=980F id=856014` → dump → restore --write).
**RP2040 nötig, RP2350 ausdrücklich nicht** (GPIO-Erratum, D+-Pull-up). [A, ungetestet] Raspberry Pi Pico
(RP2040) statt XIAO: Pin 1 = GP0 → D+, Pin 2 = GP1 → D−, beliebiger GND, VBUS frei; XIAO-UF2 probieren oder
kurogedelic/FM-1-transporter mit `-DPICO_BOARD=pico` und demselben Loader neu bauen.

## 15. Vorbeugung / Grundchecks [A Community]

Akku > 1 h laden (Tiefentladung verhindert den Start), anderes Datenkabel/Port/Rechner probieren, vor Fremd-
Firmware auf V15, nach erfolgreichem Flashen ein Backup behalten. Update-Modus: dokumentiert ist **OCT− allein**
beim Einschalten (drei kurze Tastenlicht-Blitze); „laut Community“ auch OCT− + OCT+ 10 s halten (bei Melodee
ist OCT−+OCT+ die Kalibrierung!).

## 16. Bootloader-Falle (CHECK HEAD) und Download-Modus-Werkzeuge

* [V melodee/firmware/src/ota.c, sloop-fm1/firmware/src/ota.c] Melodee 1.6.0-rc.7 und SLOOP 2.8.3 haben noch die
  **alte** Prüfung: Pakete mit M-VAVE-Loader nur, wenn deren erste 0x4000 Bytes byteweise dem Bootbereich des Geräts
  gleichen (sonst −5). Skiff nicht geprüft (kein Klon). [A fm-1.drewr.dev/bootloader-check] Nach m-bootloader oder
  Open V12/V14 ist der Bootbereich anders → diese Firmwares lehnen V15 ab ("gefangen"). Fix: felucca-bsp `afd83ede`
  (`ota_boot_valid`, in diesem Repo-Klon vorhanden [V]). Ausweg sonst: Download-Modus.
* Gerätecodes (nur auf dem FM-1-Display; der Rechner sieht nur "hört auf zu lesen"): −2 Kopf-CRC, −3 kein
  Loader/flash.bin (m-bootloader-Installer), −5 CHECK HEAD, −7 unbekannter Loader, −11 Loader-Rücklesen, −13 kein
  "success", −15 Felucca-1.6.1-Kopien: flash.bin ≠ 0x93000 (Open V12 0x6F000 / V14 0x73000). Die App erkennt −5
  an der Leseadresse (Bootbereich des Pakets) und erklärt die anderen im Log.
* Planer: Warnung vor Installationen, die den Bootbereich ändern (m-bootloader, Open), und beim V15-Zwischenschritt.
* **M-Flasher** (jvitkauskas, MIT, Rust; ARM64-.deb, glibc ≥ 2.35 → Bookworm ok): Download-Modus-Werkzeug mit
  Flash-Karte; Softkey `F0 22 24 35 7D F7` [V src/midi.rs] schickt ein laufendes Original-/Felucca-FM-1 in den
  ROM-Download-Modus; Felucca/SLOOP: OCT−+OCT+ 5 s; m-bootloader: REC beim Einschalten. Entscheidung: nicht
  einbetten (Rust-Binary, eigene Oberfläche) – die Rettungsseite erkennt `m-flasher` im PATH und startet es,
  sonst Link + apt-Hinweis. Der eingebaute Weg bleibt MvaveFM1Unbricker (Python, testbar mit Mock).
* Hub-Rescue `fm1_rescue.py` (GPL-3.0): nicht abgerufen/geprüft (Budget) – als Gegenprobe offen.

## 17. V16 und Flash-Sicherheit (Analyse und Einbau, 31.10.2026)

Vertrauensstufen: **[Paket]** aus Paketen/Quelltexten belegt · **[Aufz.]** an echter Hardware aufgezeichnet ·
**[Emu]** im Emulator beobachtet · **[Annahme]** nicht geprüft. Modul: `fm1companion/flash/flashmodel.py`,
Steckbriefe: `fm1companion/data/known_packages.json` (erzeugt mit `tools/make_known_packages.py`), Tests:
`tests/test_flashmodel.py` (echte Pakete über `FM1_TEST_PKGDIR`, sonst übersprungen), `tests/ui_v16_test.py`.
Grundlage: Übergabe `fm1_flash_safety/INTEGRATION.md` (anderer Chat) – hier nachgerechnet.

### 17.1 Nachgerechnet mit den echten Paketen

| Paket | Kennung | SHA-256 | flash.bin | Layout | Bootkopf |
|---|---|---|---|---|---|
| V15 (`FM-1_firmware_15.zip` = `FM-1.fwsc`) | FM-1_015 | db1642b2… | 0x93000 | v15 | d9f43191… |
| V16 | FM-1_016 | d017fb65… | 0xA9000 | v16 (VM 0xBA000, SEQ64 0xDA000, BTIF 0xEC000, USER 0xED000) | 8f5228c2… |
| Melodee 1.5.1 | FM-1_91051 | 172afb5b… | 0x93000 | v15 | 53144a0a… |
| **Melodee 1.6.0-rc.7** (neu, Hash aus den SHA256SUMS des Releases) | FM-1_91060 | 607f746a… | 0x93000 | v15 | 53144a0a… |
| SLOOP 2.8.3 / Skiff 2.0.3 / JIANT 0.9.9 | FM-1_900 / _720 / _909 | wie Übergabe | 0x93000 | v15 | 53144a0a… |

Bestätigt [Paket]: alle Kennungen, Hashes, Längen und Layouts der Übergabe. V16-App-Verzeichnis: `app.bin` bis
0x960E8, `DX7.bin` 0x96267–0xA8071, PRCT 0x0–0xA9000. V15 bringt zusätzlich einen USR-Eintrag (73 KB Werks-Voices),
den der aufgezeichnete Update nie anfragt; V16 hat keinen (die Voices stecken in `DX7.bin` im App-Bereich).

**Korrekturen an der Übergabe:**
1. *„Der Boot-Kopf bleibt in allen Fällen der von Melodee“* stimmt nur im Modell. Echte Geräte tragen den **Bootkopf
   ab Werk** (d9f43191…, byte-gleich in V13 = FM-1_009, V14 und V15 [Paket: FM-1-RE-Pakete]), weil Melodees eigener
   Loader (`firmware/loader/ldr_core.c`: „the flash head is never written“) [Paket] ihn nie schreibt und der normale
   Update ihn bei gleichem Kopf nicht anfragt [Aufz.] – nach einem V16-Update ist das offen (Punkt 4).
   `installed_state(…, head=…)` modelliert das jetzt. Nachgerechnet (Abnahme): V13/V14 haben `flash.bin` = 0x94000
   (VM ab 0x94000), V15 und alle Community-Pakete 0x93000.
2. **Melodee → V16 direkt geht nicht** [Paket: `melodee/firmware/src/ota.c`]: Vor dem Original-Loader vergleicht
   Melodee die ersten 0x4000 Bytes des Pakets mit dem Bootkopf des Geräts; V16s Kopf ≠ Werks-Kopf → −5. Der Plan
   erzwingt deshalb V15 als Zwischenschritt (fest, `planner.v16_switch`), die Simulation lehnt Melodee → V16 mit −5 ab.
   Für V16 → Nicht-V15 ebenfalls fest über V15 [Annahme, vorsichtig: ob V16 einen Felucca-Loader annimmt, ist unbekannt].
3. **Staging hängt an der laufenden Firmware, nicht am Paket**: Melodee (und laut Unbricker die Felucca-Familie)
   legt den Loader des Pakets bei 0xE0000–0xE4FFF ab, Update-Datensatz bei 0xE4F00 [Paket]. Wo Original-V15/V16 ihn
   ablegen, ist offen (FM-1-RE: „VM/loader flash area“).
4. Konflikt in den Quellen: Melodees `ota.c` sagt, der Original-Loader „rewrites the whole flash.bin including the
   head“; die Aufzeichnung zeigt, dass der Bereich 0x0–0x3FFF in Stufe 2 nicht angefragt wird. **Nachprüfung
   (Abnahme):** Die Aufzeichnung beweist das nur für den Fall „Kopf im Gerät = Kopf im Paket“ (V15 auf einem Gerät
   mit Werks-Kopf). Stufe 2 liest die Kopf-Einträge bei 0x0 und 0x4000 und plant danach (`verify_and_plan_update`,
   SDK-Lineage: Bereiche mit gleicher CRC werden übersprungen) – bei V16 mit **anderem** Kopf kann der Loader den
   Kopf also durchaus anfragen und schreiben. Das ist **offen** [Annahme in beide Richtungen]; Dump A ↔ B klärt es.
   Folgen: Die Dump-Prüfung nennt einen V16-Kopf nicht mehr „nur ROM-Bootmodus“; der Rückweg über V15 bleibt
   richtig (der Original-Loader würde dann auch den V15-Kopf zurückschreiben). Ein Abbruch während eines
   Kopf-Schreibvorgangs wäre schlimmer als einer im App-Bereich – ein Grund mehr für „Jetzt nicht abstecken“.

### 17.2 Loader `ota.bin`

* In V13, V14, V15 und V16 byte-gleich (SHA-256 ef1f2db8…, = FM-1-RE `ota_stock.bin`) [Paket].
* Start/Ende aus dem Paket: Die Prüfung der Dateiköpfe sucht `flash.bin`, `app_dir_head`, `uboot.boot`; die
  aufgezeichnete Stufe 2 fragt genau `flash.bin[0x4000:Ende)` absteigend an [Aufz.]. Für V16 daher 0x4000–0xA8FFF
  [abgeleitet].
* Löschen: Das Listing ruft `prepare_flash_regions` (+0x4536) **vor** `write_update_regions` (+0x47C0) auf – passt zu
  „ganzen Bereich zuerst löschen“ (SDK-Namen). FM-1-RE führt die Löschwirkung selbst als unbestätigt
  („Confirm the erase, write, metadata … side effects“) → bleibt **[Annahme]**; `install_ota` modelliert Löschen vorab.

### 17.3 Hypothese „abgeschnittenes V16“ (aus einem Nutzerbericht)

* Nur Schreiber im ROM-Bootmodus schneiden bei 0x93000 ab: der Unbricker (`FW_LEN = 0x8F000`) nimmt ohne
  `--verify-v15` auch FM-1_016 („official-other“, nur Warnung) und schreibt 0x4000–0x92FFF [Paket]. Die App ruft ihn
  immer mit `--verify-v15` und nur mit der heruntergeladenen V15 auf; zusätzlich lehnt `uboot.Runner.restore` Pakete
  über 0x92FFF ab (`TOOL_TRUNCATES`).
* Web-Installer können nicht abschneiden: Das Gerät fragt an, der Rechner liefert; welche Bereiche geschrieben werden,
  entscheidet der Loader im Gerät [Aufz.]. Melodees Web-Installer nimmt als Original-Paket nur V15 an
  (`web/fm1pkg.js validateStockPackage`) [Paket]; Melodees eigener Loader lehnt `flash.bin` ≠ 0x93000 ab (−3)
  [Paket]; M-Flasher schreibt `flash.bin` ganz [Paket]. FM-SWITCH und thefirmwarehub: Quelltext nicht verfügbar.
* Ein abgeschnittenes V16 hätte kein vollständiges `app.bin` (Ende 0x960E8) und keine Werks-Voices. Da im
  berichteten Fall V16 benutzt, der ROM-Bootmodus aber nie benutzt wurde, ist die Hypothese **unwahrscheinlich** [abgeleitet]. Die
  „Lücken bei den Presets“ passen eher zur V16-Erststart-Formatierung bzw. zu V15-/Melodee-Daten, die V16 in USER
  0xF0000–0xFEFFF vorfindet [Annahme]. Das Modell bildet den Fall ab (`(v16, "from4000", 0x93000)`), die
  Dump-Prüfung erkennt ihn (App-Bereich bis 0x92FFF = V16, Rest nicht) und meldet Rot.

### 17.4 Melodee rc.7: Ressourcen 0xCB000–0xDFFFF

* Speicherkarte rc.7 (`storage.c`): Prophet-Bänke 0x93000–0x9CFFF, Erweiterung 0x9D000, FM6-Bank 0x9F000, Pool
  0xA0000–0xC7FFF und 0xEA000–0xF9FFF, CZ 0xC8000, Ressourcen 0xCB000–0xDFFFF, Staging 0xE0000–0xE4FFF,
  Einstellungen 0xFC000 [Paket].
* V16-Install (bis 0xA8FFF) trifft Prophet-Bänke, FM6-Bank und den Anfang des Pools; der V16-Erststart [Emu] trifft
  Pool, CZ-Bänke, die **letzten sechs Ressourcen-Sektoren 0xDA000–0xDFFFF** und das Staging.
* Befehl 82 (`fm1_resources.py`): Schreiben löscht alle 21 Sektoren, schreibt die Nutzdaten und den Kopf zuletzt →
  heilt den Ressourcenbereich vollständig. Status (Argument 0, nur lesen) liefert `installed` (= Kopf + CRC-32 der
  Nutzdaten passen zum `res_want` dieses Builds) sowie Länge/CRC des Builds; rc.7: 53 844 Bytes, CRC 2035d335 [Paket].
  Nicht geheilt werden Nutzerdaten (Backup zurückspielen) und V16-Reste in 0x93000–0xA8FFF.

### 17.5 Was die App jetzt tut

* **Vorprüfung** (`planner.plan_install` mit `flashmodel`): Kennung nur aus dem Paket – Dateiname mit anderer Version
  sperrt (`NAME_IDENTITY_MISMATCH`); unbekannter Hash → Bestätigung „unbekannte Datei“ (`HASH_UNKNOWN`); Wechsel
  V15-Aufbau ↔ V16 ohne Backup, obwohl eins möglich wäre → Sperre (`NO_BACKUP`); bei Firmwares ohne Editor-Protokoll
  (V16 selbst, Original-V15) bleibt es bei der Bestätigung „ohne Backup“, sonst gäbe es keinen Rückweg;
  `LAYOUT_CHANGE`/`OVERWRITES_DATA` → „Was danach nicht mehr auf dem Gerät ist“ mit Bereichen und Melodee-Namen;
  `TOOL_TRUNCATES` → Unbricker-Weg schreibt nie V16. `HEAD_NOT_WRITTEN` ist auf dem normalen Weg gegenstandslos.
* **V16 installieren**: „experimentell – nur mit Backup“, fest über V15, während des Schreibens roter Hinweis „Jetzt
  nicht abstecken“ und kein Abbrechen; Fertig-Satz nennt, was geprüft wurde (Prüfsumme, Kennung) und was nicht.
* **Rückweg**: Banner in Firmware+ › „Installieren & Versionen“, wenn `FM-1_016` läuft; Dialog „Rückweg von V16“ (Zielfirmware,
  Version, Vorschlag aus dem Firmware-Verlauf) → normaler Plan, fest über V15, danach Ressourcen.
* **„Ist mein FM-1 sauber?“** (Rettung): Schnellprüfung (Kennung, Version, Befehl 82) und Dump-Prüfung (Datei oder
  Unbricker `backup` im ROM-Bootmodus) mit Ampel, Sätzen, „nicht abgedeckt“ und Details. Demo: drei Beispiel-Dumps.
* **Reste aufräumen** (`flash/restzone.py`, Karte „Ist mein FM-1 sauber?“ › Expertenmodus) – **am echten Gerät
  ungetestet**, nur mit dem Mock-FM-1 des Unbrickers geprüft (`tests/test_restzone.py`, `tests/ui_v16_test.py`).
  * Primitiv: jl-uboot-tool `JL_LoaderV2.flash_erase_sector` (Befehl 0xFB01, 4 KiB) über die unveränderte
    Unbricker-Funktion `erase_repair_sector` (dieselbe wie `repair-settings`, upstream nur mit Mock getestet) samt
    deren Toren `_check_mutable`/`_check_repairable`. Geschrieben wird nie. Dass der WL82-Loader dabei genau 4 KiB
    löscht, ist **[Annahme]**; nach jedem Sektor wird der ganze 64-KiB-Block verglichen und sofort angehalten.
  * Harte Regeln im Code: nur ganze Sektoren in 0x93000–0xA8FFF (`gate`), nie NEVER_ERASE/Bluetooth; nur bei
    byte-genau bekannter Firmware mit V15-Aufbau im App-Bereich (`_app_match`), nie bei V16/unbekannt; nur Sektoren,
    die laut Dump **derselben Sitzung** (`take_backup`: zweimal gelesen, identisch, gesichert, rückgelesen) noch
    V16-Bytes tragen – voll (Steckbrief-Hash oder Paket), teilweise (`_sector_blocks_match` bzw. `_sector_matches`).
    Vor dem Löschen wird der Sektor erneut gelesen und muss dem Dump gleichen; danach muss er zweimal leer lesen;
    am Ende wird der ganze Flash mit dem Erwarteten verglichen (`full-flash-after.bin`).
  * Zwei Läufe, zwei Sicherungen: `plan` (nichts löschen) → Bestätigung mit Sektorliste und Tippwort → `clean
    --only …` (gelöscht wird nur die Schnittmenge aus Bestätigung und frischem Plan).
  * Journal `Rettung/Reste-aufräumen/reste-journal.json` (atomar, fsync) vor und nach jedem Sektor. Fortsetzen: ein
    Sektor im Zustand „loeschen“ zählt nur als Rest, wenn das Original laut Journal-Sicherung (Prüfsumme) V16 war, der
    App-Bereich gleich ist und sich seither ausschließlich Bits 0→1 geändert haben (≥ 16 Bytes übrig).
  * Bootkopf: wird weder gelesen noch bewertet (ob V16 ihn über den normalen Update schreibt, ist offen) und bleibt
    NEVER_ERASE.
  * root: wie die übrigen Befehle über `ROOT_HELPER`; `restzone.py`, `flashmodel.py` und `known_packages.json` sind
    mit Prüfsummen in `uboot_manifest.OWN` (nach jeder Änderung neu erzeugen, Test prüft es).

### 17.6 Offen – mit Dumps eines entbehrlichen FM-1 klärbar (Hilfe „V16 – Hinweis“, Testplan A–D)

a) Schreibt V16 wirklich 0x4000–0xA8FFF, bleibt der Bootkopf gleich (A ↔ B)? b) Löscht der Loader vorab (nur mit
absichtlichem Abbruch sichtbar – nicht empfohlen)? c) Staging der Original-Firmware (0xE0000–0xE4FFF vor/nach)?
d) Bleiben die Reste in C und D, läuft rc.7 damit? e) Formatiert V16 auch beim zweiten Start? Danach `RUNTIME`,
`STAGING` und `install_ota` in `flashmodel.py` korrigieren.
