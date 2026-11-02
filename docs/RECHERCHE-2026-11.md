# Recherche November 2026: Community-Quellen für Studio-Zwilling, Backup und Rettung

Stand: 2. November 2026. Gelesen habe ich lokale Klone (alle `git clone --depth 1`, Datum = letzter Commit),
nichts ausgeführt außer einem Testbau von fm1-emu. Am Gerät habe ich nichts getestet.
Belege sind als `Repo:Datei:Zeile` angegeben.
**[V]** = im Quelltext oder Dokument nachgelesen, **[A]** = Angabe des Projekts, nicht nachgeprüft,
**[?]** = unsicher oder widersprüchlich.

Nicht erreichbar (Proxy blockiert alles außer github.com): *.github.io-Seiten (fm1-emu-Bericht als PDF, jielie-Webseite,
Installer), thefirmwarehub.com (die dort verlinkte `fm1_rescue.py` habe ich stattdessen in `hugelton/felucca-bsp/tools/`
gelesen), reddit, forum.m-vave.com, doc.zh-jieli.com, fm1-editor.com, KVR, Elektronauts, Gearspace, die Artikel- und
Video-Seiten. Klonen ging nicht (403, vermutlich gelöscht oder privat): `jvitkauskas/fm1-bootloader`,
`stanleygurvich/fm1-firmware-database`. Release-Assets konnte ich nicht prüfen (GitHub-API ist für diese Sitzung gesperrt).

---

## (a) Kurzfazit: die 10 besten Gelegenheiten

Die Reihenfolge richtet sich nach dem Nutzen für den Studio-Ablauf (Backup → Zwilling → bearbeiten → rendern →
zurückschreiben). Aufwand: S ≈ 1–2 Tage, M ≈ 1 Woche, L ≈ mehrere Wochen.

| # | Gelegenheit | Was genau | Aufwand |
|---|---|---|---|
| 1 | **Ein gemeinsames Zustandsformat: das 1-MiB-NOR-Abbild** | Der Melodee-Quellcode-Zwilling der Suite arbeitet schon mit einem 1-MiB-Flash (`sims/melodee-studio/1.6.0-rc.8/manifest.json` `"flash_size": 1048576`, `_studio/twin-core.js` `setFlash/getFlash`). fm1-emu nimmt genau dasselbe Format: `fm1-live --flash FILE` (rohe 1 MiB, `crates/fm1-live/src/main.rs:232-239`) und `fm1-emu --raw-flash CHIP.bin`. M-Flasher und der Unbricker schreiben dasselbe Format als Volldump. Damit passen Volldump, Zwilling (beide Engines) und Geräte-Rückweg auf **ein** Format: Gerät dumpen → Zwilling startet mit exakt dem Gerätezustand → im Zwilling ändern → Abbild zurück. Offen [?]: Ob ein echter Dump (App-Bereich mit Chip-Key verschlüsselt) unverändert in fm1-emu startet, ist nicht getestet; die Nutzerdaten-Sektoren sind unverschlüsselt. | M |
| 2 | **fm1-emu als zweite Engine, aufgerufen als Unterprozess (`fm1-live`, Linux ARM64)** | MIT-Lizenz, laut README Linux-ARM64-Builds mit AArch64-JIT (`README.md:202-215`). Mit `--usb` erscheint eine Felucca-Firmware als **virtuelles ALSA-MIDI-Gerät** „<Produkt> (emu)“, mit dem auch `fm1_install.py` arbeitet (`README.md:224-229`). Damit laufen das Editor-, Backup- und Installationsprotokoll der Suite **unverändert gegen den Zwilling**. Dazu `FM1_USB_WAV` (USB-Audio aufnehmen), `FM1_BENCH_KEYS` (Eingaben als Skript), `FM1_FLASH_CUT` (Stromausfall beim n-ten Löschen oder Schreiben). Tempo am Pi ist offen (siehe b1). | M–L |
| 3 | **Backup → Zwilling über das eigene Protokoll** | Ein Felucca-Backup (`backup.py`) kann mit PUT (Befehle 65–67) in den Zwilling gespielt werden: in fm1-emu über den virtuellen `--usb`-Port, im Quellcode-Zwilling über die st_*-Schnittstelle (falls die Glue das Editorprotokoll mitbaut; FM1VST baut es zum Beispiel aus, `Quixotic7/FM1VST/README.md:65`). Danach liest man den Flash ab: Backup-Datei → 1-MiB-Abbild, ohne Rettungsmodus. | S–M |
| 4 | **Volldump als zweite Backup-Stufe über die M-Flasher-Befehlszeile** | `m-flasher info/backup/restore/firmware/download-mode` (`jvitkauskas/m-flasher/docs/usage.md:106-119`), MIT. Kennt V16 (liest die Bereiche aus der Dateitabelle der Firmware, `src/sectormap.rs:43-104`), behält Werksdaten (Kalibrierung, BT-Adresse) auch beim Zurückspielen fremder Dumps (`docs/safety.md`), prüft per CRC32 auf dem Chip und liest etwa 860 KB/s (m-bootloader README). Heute erkennt die Suite M-Flasher nur und startet ihn; sinnvoll wäre, ihn als Dump-Werkzeug im Backup-Assistenten anzubieten. | S |
| 5 | **`fm1_rescue.py` (felucca-bsp, MPL-2.0) als V16-taugliche ROM-Rettung** | Reines Python mit pyusb (Debian: `python3-usb`), 444 Zeilen, braucht weder jl-uboot-tool-Klon noch `sg`. Nimmt nur V15/V16 per Hash an und wählt **das Paket, dessen Bootkopf dem des Geräts gleicht** (`tools/fm1_rescue.py:18-31`). Damit schreibt es für V16 den Bereich 0x4000–0xA8FFF, für V15 0x4000–0x92FFF; den Kopf schreibt es nie. Der Unbricker kann nur V15 (siehe Sicherheitsbefund S2). MPL-2.0 ist mit GPL-3.0 kombinierbar. | S–M |
| 6 | **Backup für Original-Firmware (V14/V15/V16) über MIDI, ohne Rettungsmodus** | `czietz/fm1-read-voice` liest mit dem Systembefehl 0x23 (Objekt 5, Länge 155) den aktuellen Klang (VCED) aus, laut README getestet auf V14/V15 (`fm1_read_voice.py` `CMD_READ = 0x23, SUBCMD_VOICE = 5`). `bratta/fm1-manager` geht per Program Change durch alle 128 Presets und liest jedes aus (`README.md:33-36`, `src/backup.ts`). V16 hat zusätzlich „Bank Export“ am Gerät (32 Voices als DX7-Bulk, `benny-sparra/.../docs/fm1-research.md` Abschnitt V16). Spur für später, **nicht einbauen** ohne Hardwaretest: Laut statischer Analyse liest 0x23 auf Objekt 5 ab Offset 0x10000000 auf V16 den rohen Speicherbereich mit Presets, FX-Bibliothek und Globe-Block (`fm1-research.md:2648-2656`). | S–M |
| 7 | **Original-Klang annähern: msfa-Engine von Virtual-FM-1 (GPL-3.0) statt FM1 Open V14** | `jbschooley/Virtual-FM-1/engines/dx7` (msfa), an echter Hardware abgeglichen: Velocity × 100/127, LFO-Tabelle, Detune, Tonhöhe (`docs/HARDWARE-NOTES.md`, gemessen an FM-1+VA 093); dazu `engines/fm1_fx`. Wichtig für V16: Laut czietz hat M-VAVE in **V16 die Detune-Berechnung korrigiert** (`czietz/fm1-firmware-patcher/README.md`). Ein Zwilling über FM1 Open V14 klingt also bei verstimmten Operatoren anders als V16. Exakt wäre nur fm1-emu mit dem V16-Paket. | M |
| 8 | **Sicherer Zwischenschritt mit TransitFM1 statt pauschal V15** | TransitFM1 (GPL-3.0) meldet sich als `FM-1_015` und schützt das Schreiben bis auf den letzten Sektor gegen Abbruch. Ein Schreibvorgang V15 → Fremdfirmware ist dagegen fast ganz ungeschützt (`Quixotic7/TransitFM1/README.md:6`, Tabelle `:43-50`). Die Suite setzt V15 als Zwischenschritt standardmäßig an (`docs/flash-protocol.md` §12) und vergrößert damit das Risikofenster. Dazu kommt die Loader-Kennung `ota-FM1C_<build>` von FM1 Open (`m-vave/fm1_open/sdk/WebUpdate.js:149`), die die Suite nicht kennt. | S |
| 9 | **Testumgebung ohne Gerät** | fm1-emu ohne Fenster plus `fm1-playwright`/`setup-fm1-emulator` (MIT) für Regressionstests von Installer, Backup und Restore gegen echte Firmware-Pakete, auch mit simuliertem Stromausfall (`FM1_FLASH_CUT`). Die Suite testet heute nur gegen eigene Simulatoren und den Mock des Unbrickers. | M |
| 10 | **Flash-Karte aus der Dateitabelle der Firmware statt fest einprogrammierter Bereiche** | Wie `m-flasher/src/sectormap.rs` (VM, BTIF, USRTRIM, USRFLASH, SEQ64, USR, PRCT aus dem `app_dir_head`) und `fm-static` „Layout Analysis“. Das macht die Dump-Prüfung robust für V16 und künftige Versionen und behebt die falschen Rot-Meldungen bei Werksgeräten (Sicherheitsbefund S3). | S–M |

### Dringende Sicherheitsbefunde (Kurzfassung, Details in b5)

* **S1 – „Einstellungen reparieren“ hat keine V16-Sperre.** **Status: behoben in 2.2.0** (Sperre bei V16-Aufbau, `uboot.py`/`rescuegate.py`). `ui/pages/recovery.py:498-522` → `flash/uboot.py:294` →
  Unbricker `repair-settings`. Dessen Fenster sind fest im V15-Aufbau gewählt: 0x93000–0xE8FFF und 0xFC000–0xFEFFF
  (`vendor/unbricker/fm1_unbrick.py:77-78`). Bei V16 liegt in 0x93000–0xA8FFF noch **Programmcode** (Ende von app.bin und
  DX7.bin), in 0xBA000–0xDA000 der VM-Bereich. Ein Lauf auf einem V16-Gerät überschreibt also die Firmware, und der Satz
  in der App „Die Firmware selbst wurde nicht verändert“ (`recovery.py:520-521`) stimmt dann nicht. Der Unbricker warnt
  nur, wenn Felucca-Marker gefunden werden (`fm1_unbrick.py:1533-1537`); V16 sperrt er nicht.
* **S2 – Die ROM-Rettung schreibt immer die V15-App, auch unter einem V16-Bootkopf.** **Status: behoben in 2.2.0** (kein V15-Zurückschreiben bei V16- oder unbekanntem Kopf). `fm1_rescue.py:18` sagt:
  „M-UPGRADE rewrites the flash head when it installs V16“. Das Skript wählt deshalb das Paket nach dem Kopf. Der
  Unbricker meldet einen abweichenden Kopf nur als Hinweis (`fm1_unbrick.py:1128-1135`) und schreibt trotzdem V15.
  Ob eine V15-App unter dem V16-Kopf (größerer Bootloader, `isd_config` bei 0x3938 statt 0x38D0,
  `m-flasher/docs/development.md:74-76`) startet, ist **offen [?]**. Die Rettungsseite der Suite bietet in diesem Fall
  ohne Warnung „Original-V15 zurückschreiben“ an.
* **S3 – Die Dump-Prüfung kann Werksgeräte fälschlich „beschädigt – bitte neu aufspielen“ nennen.** **Status: behoben in 2.2.0** (Werks-/unbekannte Original-Abbilder werden nicht mehr „beschädigt“ genannt).
  `flash/flashmodel.py:727-740`. Werksgeräte tragen im App-Bereich ein anderes Abbild als das öffentliche V15-Paket
  (`fm1_unbrick.py:1530-1532`; bei maxfridbe gemessen: SHA-256 `8c1d13a5…` statt `6edf3c37…`,
  `maxfridbe/mvave-fm1/skills/mvave-fm1-firmware/SKILL.md:172`). Gelöscht wird dadurch nichts, aber der Rat zum
  Neuaufspielen ist ein unnötiges Risiko.

---

## (b) Befunde

### b1. Zwilling: Melodee aus dem Quellcode und fm1-emu

**fm1-emu (joakimeriksson, MIT, aktiv: 1 Commit, 2026-11-02)**
* Läuft laut Projekt mit 81 Community-Firmwares und der Original-Firmware **V13, V15 und V16**
  (`docs/stock-firmware.md:3`, mit V16-spezifischen Korrekturen in Tabellenzeilen 48–51), beide Kerne bei der
  Original-Firmware.
* **USB:** Der Controller löst noch keine Interrupts aus. Firmwares, die abfragen (Felucca-Familie, also Melodee,
  SLOOP, Skiff), melden sich an; die Original-Firmware arbeitet mit Interrupts und meldet sich nicht an
  (`README.md:228-229`). Ein Backup von V16 über USB geht also weiterhin nicht. Das bestätigt den Stand der ROADMAP.
* **Zustand:** `.fm1save` ist JSON (`format: "fm1-emulator-save"`, `version`, `hash`, `name`, Paket als base64,
  Liste von 4-KiB-Sektoren mit Offset; `crates/fm1-plugin/src/savefile.rs:10-30`). `fm1-live --flash` speichert eine
  rohe 1-MiB-Datei (atomar über `.tmp` und rename, `main.rs:505-519`). `restore_flash` legt nur die abweichenden
  Sektoren über das installierte Paket (`fm1-soc/src/lib.rs:471-481`).
* **Melodee-Backup in fm1-emu laden:** (1) Melodee-Paket mit `--usb` starten, Suite-Backup per PUT über den virtuellen
  Port einspielen, Flash per `--flash` sichern. Das ist der empfohlene Weg, weil das Format der Firmware gehört.
  (2) Einen Volldump mit `--raw-flash` oder `--flash` laden; ob das mit dem verschlüsselten App-Bereich klappt, ist [?].
* **Tempo [A]:** Native mit AArch64-JIT bei 240 MHz Melodee 5,9×, Felucca 4,4×, X0X 2,65× Echtzeit (auf einem
  Apple-Mac, `PLAN.md:328-331`); im Browser (wasm) Melodee 2,47× (`PLAN.md:425-427`). Für einen Pi 5 (Cortex-A76) gibt es
  keine Messung. Erwartung [?]: Melodee etwa 1,5–3×.
  **Eigener Test:** `cargo build --release -p fm1-emu` baute auf x86_64 in 28 s. Der Batch-Runner schaffte aber 10 s
  emulierte Zeit (V15 und SLOOP 2.8.4 mit `--keys` und `--wav`) nicht in 300 s. Wahrscheinlich nutzt dieser Pfad den
  JIT nicht; als Tempomessung taugt das nicht. **Messen muss man `fm1-live` auf dem Pi.**
* **Klangtreue [?]:** Melodee und X0X geben Audioarbeit an Kern 1 ab. Laut PLAN startet Kern 1 bei Melodee im
  8-s-Bench nicht, beide fallen dann auf Kern 0 zurück (`PLAN.md:320, 1071`). Die Stimmenzahl unter Last kann also vom
  Gerät abweichen. „Byte-identisch“ belegt das Projekt nur zwischen JIT und Interpreter, nicht gegenüber der Hardware.
* **Einbinden:** Unterprozess (`fm1-live`, ARM64-Release oder `cargo build` auf dem Pi) ist der Weg mit dem geringsten
  Risiko. Die Crates (`fm1-core`, `fm1-soc`) einzubinden würde eine Rust-FFI-Schicht verlangen. Die Debian-Pakete
  Rust/cargo sind auf Bookworm/Trixie vorhanden; `minifb`/`cpal`/`midir` brauchen X11/ALSA-Entwicklerpakete [A].
* **Ergebnis:** Der Plan aus der ROADMAP hält. Melodee aus dem Quellcode ist die schnelle Haupt-Engine; fm1-emu ist die
  zweite Engine für alles andere, vor allem für V16-Klang. Neu ist: Der Zustand beider Engines sollte das
  1-MiB-Abbild sein (Gelegenheit 1).

**Weitere Engines verglichen**
* simonjohansson/fm1-emulator (GPL-2.0-or-later, QEMU-basiert, Linux nur x86-64) und der Fork von hdavid (Rust,
  Interpreter, Original-Firmware im Leerlauf nur 0,047–0,27× Echtzeit auf einem M4 Max, `hd-fm1-emulator/README.md:75`):
  zu langsam für den Pi.
* shcherbakov7 (GPL-3.0, Rust/wasm, russisches README): V13–V15 laufen, Melodee nur 0.13.1 geprüft. Kein Mehrwert
  gegenüber fm1-emu.
* FM1VST (GPL-3.0): Melodee **1.4.5** aus dem Quellcode als `.fm1core`-Modul mit C-ABI, Flash als RAM-Abbild;
  Editorprotokoll, OTA und USB-Audio sind ausgebaut; Module nur für macOS (Mach-O). Als Vorbild für die
  Quellcode-Glue nützlich, für den Pi nicht direkt nutzbar.
* Virtual-FM-1 (GPL-3.0, Linux x86-64 VST3/LV2): Melodee 1.4.1 und Felucca aus dem Quellcode, Abgleich über das
  Editorprotokoll; für die Original-Firmware eine msfa-Engine ohne Rücklesen. Gute Referenz für die Felucca-Glue und für
  den Original-Klang.
* felucca-vst (cdodd, GPL-3.0, LV2 Linux x86-64): nur Felucca und Ableger mit dem Browser-Emulator von Felucca, kein
  Melodee.
* weavejam/Felucca: rendert Felucca-Projekte offline als Mix + 4 Stems (tuttidaw). Die Idee „Stems aus dem
  Projekt“ lässt sich auf die Suite übertragen; der Code ist Felucca-spezifisch.

### b2. V16

* Speicheraufbau [V, mehrere Quellen übereinstimmend]: Abbild bis 0xA9000, VM 0xBA000–0xDA000, SEQ64 0xDA000–0xEC000,
  BTIF 0xEC000–0xED000, USER 0xED000 (+0x12000) (`m-flasher/docs/development.md:74-76`, `fm1-emu/docs/stock-firmware.md:48`).
  Das stimmt mit der Suite überein (`docs/flash-protocol.md` §17.1).
* **Größerer Bootloader:** `isd_config.ini` liegt bei 0x3938 statt 0x38D0 (M-Flasher). Laut `fm1_rescue.py:18` schreibt
  M-UPGRADE beim Installieren von V16 den Bootkopf neu. Damit ist die offene Frage §17.1 Punkt 4 / §17.6(a) der Suite
  **wahrscheinlich mit „ja, der Kopf wird geschrieben“ beantwortet** [A, ohne Aufzeichnung]. Folge: siehe S2.
* Funktionen laut benny-sparra (Hardwaretests 23.–26.10.): 8 Voices mit eigenem Kanal, 20 Stimmen Polyphonie, eine
  FX-Bibliothek mit 128 Gruppen, die getrennt von den Presets gespeichert wird, FX-CCs 70–93 statt 0–23, Bank Export als
  DX7-Bulk (`fm1-research.md:2195-2300`). Für einen V16-Zwilling heißt das: Ein Preset trägt die Effekte nicht mehr
  mit; Zustand = Presets + FX-Bibliothek + Globe + Voice-Records + Patterns.
* Klang: Detune ist in V16 korrigiert (czietz). Der FM1-Open-V14-Zwilling weicht deshalb ab; das sollte der Hinweis
  „V16 kann abweichen“ in ROADMAP Schritt 4 konkret nennen.
* czietz hat einen Patch für V16 (Aftertouch aus, Ergebnis-SHA `cc825caf…`) und für V15 (Detune, Aftertouch, Farben,
  Ergebnis `4638959b…`). Diese festen Ausgabe-Hashes könnte die Suite als „bekannte Varianten“ in
  `known_packages.json` aufnehmen, damit sie nicht als „unbekannte Datei“ gelten.

### b3. Backup-Vollständigkeit

**Melodee (rc.7-Quelltext gelesen):** Das Backup kennt 57 Objekte (`firmware/src/editor_backup.c:12-14`): die laufende
Musik (0), Einstellungen samt Vorlage (1), 32 Projektplätze (2–5, 64–91) und alle 22 Preset-Bänke, darunter
FM6/Native-FM6, CZ- und Prophet-Bänke und die gemeinsame Erweiterung (`upreset.c:107-115`). Die Einstellungen enthalten
den BLE-Anhang (`storage.c:20`, Tag `BLE1`) und die gelernte Tastenbelegung (`panel.c:6-9`).
**Nicht im Backup:**
* der Ressourcenbereich 0xCB000–0xDFFFF (gehört zur Firmware; Befehl 82 stellt ihn wieder her),
* ungespeicherte RAM-Änderungen (erst SAVE > PROJECT),
* Bootkopf, `key_mac` 0xFF000–0xFFFFF, Staging 0xE0000–0xE4FFF,
* alles, was andere Firmwares im Flash hinterlassen haben.

Für den Zwilling reicht das Backup also aus. Ein Volldump ist der einzige **byte-genaue** Rückweg.

Achtung beim Kopieren von Gerät zu Gerät [?]: Objekt 1 bringt BLE-Bindungen und Tastenbelegung des Quellgeräts mit.

**Original-Firmware:** Es gibt kein Backup-Protokoll. Der USR-Bereich 0xEA000–0xFC000 enthält 128 DX7-VMEM-Voices ab
+0x4000 und 128 Platz-Einstellungen zu je 59 Bytes ab +0x10000; Updates schreiben ihn nie
(`fm1-emu/docs/stock-firmware.md:22-26`). Die VM enthält Einstellungen (unter anderem BT-MAC als ID 102 und die
Rad-Kalibrierung, FM-1-RE `docs/architecture.md` §7). Über MIDI kommt man heute nur an die Voices (Gelegenheit 6), nicht
an die Platz-Einstellungen und die VM. Nur ein Volldump sichert alles.

**Was ein Volldump zusätzlich bringt:** Bootkopf (zeigt, ob V16 den Kopf geändert hat), gerätespezifische Daten
(`key_mac`, BTIF, auf dem SMK-37 die Kalibrierung bei 0xC1000), die Reste anderer Firmwares, den exakten Startzustand
für den Zwilling.

Beim Zurückspielen muss man Werksdaten bewahren, wie M-Flasher es tut. Zum Vergleich:
`workeffortwaste/fm1-backup-restore` schreibt laut seinen Tests auch 0xFF000 zurück
(`app/src/test/.../RestoreTest.kt:139,166-173`). Fremde „Recovery-Snapshots“ würden damit Gerätedaten überschreiben.

### b4. Protokoll- und Formatwissen, das in der Suite fehlt oder anders steht

* Loader-Kennung von FM1 Open im Update-Modus: `ota-FM1C_<build_id[:16]>` (`fm1_open/sdk/WebUpdate.js:149`; FM1Kit
  README). In `docs/flash-protocol.md` §4/§5 fehlt sie. Die Wiederherstellung würde einen wartenden Open-Loader nicht
  zuordnen.
* Systembefehle der Original-Firmware (Rahmen `00 59 cmd len24 … ~sum`): FM-1-RE warnt, 33–36 und 48 dürfe man nicht
  ausprobieren (`docs/io/11-ota-protocol.md`, Abschnitt syscmd). czietz nutzt 35 = 0x23 als Lesebefehl auf Objekt 5
  produktiv. Lesen auf Objekt 5 gilt damit als erprobt (V14/V15); 34 (Schreiben) und 48 bleiben tabu.
* Partial-Block-Framing: Die Länge muss nach dem Packen stimmen (FM-1-RE `docs/io/12-v15-reflash-proof.md`). Der
  vendorte `fm1_install.py:104-110` macht das richtig (`n + 8`). Kein Fehler in der Suite.
* `aroum/fm1_sysex_scanner.py` sendet `F0 00 32 45 00 00 00 <id> 7F F7` für id 0–127. Entpackt ist das **immer Befehl
  0x11 (Kennung)** mit unsinnigen Längen; nur id 64 ist der gültige Handshake (eigene Dekodierung mit
  einem nicht veröffentlichten Hilfsskript). Das Skript scannt also keine Parameter und ist wahrscheinlich harmlos [?]. Es bringt
  nichts.
* `aroum/fm1_flasher.py` nutzt ein ausgedachtes Host-Push-Protokoll (Befehle 0x01–0x04, unpassende Prüfsumme). Laut
  eigener Warnung funktioniert es nicht (`REVERSE_ENGINEERING.md:366-369`). Nicht verwenden; nur das Auspacken der
  `.fwsc` aus dem macOS-Updater taugt etwas.
* Weitere Ungenauigkeiten in aroum `REVERSE_ENGINEERING.md`: §7.1 „4/8 MB Flash“ ist allgemeines AC791N-Wissen, das
  FM-1 hat 1 MiB (P25Q80H, JEDEC 0x856014, §1.1); §5.2 nennt „SysEx-Befehle 0x01/0x02“ als Weg zurück in den
  Bootloader, das ist falsch (richtig: `F0 22 24 35 7F/7D F7`); §3.1 Nr. 17 nennt den Pico als „SWD-Programmer“, es
  ist der USB_KEY-Weg [?]. AC7911BA/BB unterscheiden sich nur im SDRAM (2/8 MB), nicht im Flash.
* Werks-Presets über den Updater: M-UPGRADE schreibt 16 × 8 Voices mit Herstellerkennung `00 32` und eigenem Rahmen
  (KingParamount `docs/protocol-and-provenance.md`; benny `fm1-research.md:3706-3760`). Die Suite bindet die Bänke schon
  ein (`dx7_sources.py:42`).
* Velocity: Die Original-Firmware skaliert eingehende Velocity mit 100/127 (gemessen an FM-1+VA). `fmrender.py` tut das
  nicht. Für das Vorhören ist das unerheblich, für einen Original-Zwilling nicht.

### b5. Sicherheit: Abgleich der Flash- und Rettungs-Pfade der Suite mit aroum §1.3

Geprüft habe ich `flash/{flashmodel,restzone,planner,uboot,uboot_manifest,engine}.py`, `vendor/unbricker/*`,
`ui/pages/recovery.py`, `firmware.py` und `vendor/fm1_install.py`.

| Regel aus aroum §1.3 / fm1-emu recon | Suite | Ergebnis |
|---|---|---|
| 0x0000–0x3FFF nie schreiben oder löschen | `flashmodel.py:31` NEVER_ERASE (Kopf + `key_mac`); Unbricker `PROTECTED_END = 0x4000` (`fm1_unbrick.py:59`); `restzone.py:58` | ✓ |
| App ab 0x4000, Bereich bis 0x92FFF | `flashmodel.py:25-27`; Unbricker schreibt nur 0x4000–0x92FFF; `uboot.py:276-291` sperrt V16 (`TOOL_TRUNCATES`) | ✓, aber siehe S2 (V16-Kopf) |
| Felucca 0.9 → 1.0: 0x97000+0x8000 und 0xFC000+0x2000 löschen | Hinweis `planner.py:406-412`; ausgeführt über `repair-settings`, das **mehr** zurücksetzt (0x93000–0xE8FFF und 0xFC000–0xFEFFF, `fm1_unbrick.py:77-86`) | ✓ für V15-Aufbau; **S1** bei V16 |
| ROM-Bootmodus WL82 UBOOT1.00, USB 4C4A:8057 | `uboot.py:33`, Auswahl über sysfs plus SCSI-Kennung (`uboot.py:81-92`) | ✓ |
| Chip-Key 0x980F, Flash-ID 0x856014, 1 MiB | `flashmodel.py:23,28`, `uboot.py:34`, Unbricker ohne Übersteuerung | ✓ (Varianten AC7911BA/BB betreffen nur den RAM) |
| USB_KEY 0x16EF über RP2040 (Transporter), Verkabelung | `recovery.py:72-84`: D6/GP0 → D+ grün, D7/GP1 → D− weiß, GND, VBUS frei | ✓ (der Wert 0x16EF selbst steht nur in der Transporter-Firmware) |
| Gefährliche SysEx `F0 22 24 35 7D F7` | Nur `uboot.softkey()` nach Bestätigung (`recovery.py:526-554`); Simulatoren und Online-Editoren filtern 7D/7F und gepackte `F0 00`-Rahmen (`sims/__init__.py:136-157`) | ✓ |
| ROM-Boot ohne Dongle: 2 min an lassen, drei schnelle Neustarts, OCT−+OCT+ 10 s | `recovery.py:36-47`: OCT− (belegt), OCT−+OCT+ als unbestätigt, Warnung „nicht zweimal aus/an im Update-Modus“ | Lücke: Watchdog (2 min) und „drei Neustarts“ fehlen als Hinweis. Widerspruch in den Quellen: M-Flasher nennt OCT−+OCT+ 5 s = Download-Modus bei Felucca/SLOOP (`m-flasher/README.md`), aroum 10 s = Update-Modus; bei Melodee ist es die Kalibrierung |

**S1 (dringend; Status: behoben in 2.2.0):** Vor `repair-settings` die Sicherung aus derselben Sitzung prüfen. Nur wenn sie den V15-Aufbau zeigt
(`flashmodel._app_match`, Familie ≠ `v16-layout`), darf geschrieben werden, sonst wird abgebrochen. Bei Bedarf in einem
eigenen Skript wie `restzone.py` mit der Funktion `erase_repair_sector`. Außerdem muss der Abschlusssatz in
`recovery.py:520-521` korrigiert werden.

**S2 (dringend; Status: behoben in 2.2.0):** Vor „Original-V15 zurückschreiben“ den Kopf aus der Pflicht-Sicherung bewerten. Ist es der V16-Kopf
(`8f5228c2…`), nicht V15 schreiben: entweder `fm1_rescue.py`-Logik (V16-App 0x4000–0xA8FFF) oder M-Flasher
`firmware FM-1_016.fwsc`, oder zumindest eine deutliche Warnung.

**S3 (Status: behoben in 2.2.0):** In `check_dump` einen unbekannten App-Bereich, der mit `FM-1_0NN` beginnt und sonst plausibel ist, als „Werks-
oder unbekannte Original-Version“ melden (gelb), nicht als „beschädigt“ (rot). Dazu die abweichenden Sektoren anzeigen.

**S4 (mittel):** Den V15-Zwischenschritt nicht mehr standardmäßig setzen, wenn der Loader-Weg es nicht verlangt, oder
TransitFM1 als Zwischenhalt anbieten (TransitFM1 `README.md:43-50`; fm-static „no blanket V15 prerequisite“,
`README.md:8`). Fest bleiben sollte er für V16 und für `requiresStockV15`.

**S5 (gering):** Eine ROM-Bootsitzung hält in der Praxis etwa einen Werkzeuglauf; `info` und danach `backup` führte zum
Reset (`SKILL.md:170`). `restzone` plan → clean und die Rettungsabläufe mit mehreren Läufen scheitern dann sicher, aber
für den Nutzer verwirrend. Am besten alles in einem Lauf erledigen oder den Nutzer vorher darauf hinweisen.

Teardown (aroum `photos/`): Platine „DX7 MB V07 260620“ (photo_02 selbst angesehen), AC7911B im QFN-48-Gehäuse, keine
Debug-Pads oder Jumper. Das bestätigt: Ohne funktionierende Firmware geht es nur über USB (ROM-Modus oder USB_KEY).

---

## (c) Alle geprüften Einträge

Relevanz: – keine, n niedrig, m mittel, **h hoch**. „gepflegt“ = letzter Commit im Oktober oder November 2026.

### Reverse Engineering und Firmware-Entwicklung (awesome-fm-1, alle Einträge)

| Name | Link | Lizenz | Rel. | Notiz |
|---|---|---|---|---|
| FM-1-RE | github.com/AL-255/FM-1-RE | WTFPL | **h** (Wissen) | Stand 2026-09-08, 142 MB. Speicherkarte V13 (VM 0x94000, BTIF 0xE9000, USR 0xEA000, `key_mac` 0xFF000), Engine = msfa (byte-genaue Tabellen), 44 118 Hz, 12 Stimmen; OTA-Mitschnitte; Systembefehlstabelle. Sicherheitsurteil „NO-GO“ und „keine Rettung im ROM“ sind durch USB_KEY und WL82 überholt. Nur V13/V14-Abbilder, **keine V15/V16-Funktionskarten**. Zweig `with-custom-firmware`: Dexed-Port mit ALSA/JACK/LV2-Host (`firmware/host/`). Code nicht übernehmen; als Referenz nutzen. |
| fm1-custom-fw (aroum) | github.com/aroum/fm1-custom-fw | **keine Lizenzdatei** | m | Teardown-Fotos, Pinbelegung, Linkliste (Abschnitte 2–7, alle gelesen). Scanner ist wirkungslos, Flasher funktioniert nicht (b4); mehrere Ungenauigkeiten. Code wegen fehlender Lizenz nicht übernehmbar. |
| fm1-nes GETTING_STARTED / APP_UPDATES | github.com/Keitark/fm1-nes | Apache-2.0 + GPL/MIT | n | Updates nur der App unter Erhalt des Bootloaders; experimentell, eigener Schreiber. |
| fm1-emulator (simonjohansson) | github.com/simonjohansson/fm1-emulator | GPL-2.0-or-later | n | QEMU, Linux nur x86-64, langsam. |
| FM-1 browser emulator (shcherbakov7) | github.com/shcherbakov7/m-wave-fm1-browser-emulator | GPL-3.0 | n | Rust/wasm, V13–V15; durch fm1-emu überholt. |
| Dinghy | github.com/zvenson/dinghy | GPL-3.0 | n | Minimalfirmware, Referenz für Rettung und USB. |
| fm1-bootloader | github.com/jvitkauskas/fm1-bootloader | Apache-2.0 (laut Liste) | n | **nicht erreichbar (403)**. |
| MvaveFM1-Designer | github.com/Quixotic7/MvaveFM1-Designer | MIT | n | Bedienfeld-Mockups und JSON; eventuell für Zwilling-Grafik. |
| fm1-firmware-patcher | github.com/czietz/fm1-firmware-patcher | Unlicense | m | V15- und V16-Patches; **V16 hat Detune korrigiert**; feste Ausgabe-Hashes (b2). |
| MVAVE-M-UPGRADE-decompiled | github.com/entitymar/MVAVE-M-UPGRADE-decompiled | keine | n | Dekompilat; Lizenz ungeklärt, nur lesen. |
| jielie | github.com/kagaimiq/jielie | keine | n | Chip-Referenz, USB_KEY-Doku (`isp/usb/`). Stand 2024. |
| JieLi AC79 SDK Doku | doc.zh-jieli.com | – | n | nicht erreichbar. |
| jl-uboot-tool | github.com/kagaimiq/jl-uboot-tool | MIT | m | Wird schon genutzt (über den Unbricker). Stand 2025, laut Autor „trashed“; WL82 offiziell „unknown“. |
| ghidra-jieli | github.com/kagaimiq/ghidra-jieli | Apache-2.0 | – | pi32v2 „very early“. |
| jl-misctools | github.com/kagaimiq/jl-misctools | MIT | n | Container- und Key-Werkzeuge; die Suite hat eigene Parser. |
| Schwung | github.com/charlesvestal/schwung | MIT | – | Ableton-Move-Framework. |
| fm1-emu | github.com/joakimeriksson/fm1-emu | MIT | **h** | Zweite Engine, Testumgebung (b1). |
| felucca-try | github.com/spinkham/felucca-try | GPL-3.0 | n | MIDI-CC 20–27 → Bedienfeldknöpfe; Idee für den Zwilling. |
| foma | github.com/konsumer/foma | GPL-3.0 | – | Firmware-Entwicklung mit Docker. |
| felucca-bsp | github.com/hugelton/felucca-bsp | MPL-2.0 | **h** | `tools/fm1_rescue.py` (Gelegenheit 5, S2); `ota_boot_valid`-Fix ist der Suite bekannt. |
| Felucca-Engines | github.com/hugelton/Felucca-Engines | MIT | n | Felucca-DSP als C99. |
| FM-1 Macropad Patch | github.com/smememe/fm1-macropad-patch | MIT | – | V15-Patch; Ausgabehash könnte man aufnehmen. |
| Fluyt | github.com/rgrvmusic/fluyt-logue | GPL-3.0 | – | KORG-Ports. |
| sloopy-sloop-32 | github.com/smhgzll/sloopy-sloop-32 | GPL-3.0 | n | SLOOP auf ESP32, zeigt die Portierbarkeit des SLOOP-Codes. |
| fm1-playwright | github.com/DimmKirr/fm1-playwright | MIT | m | Testumgebung über fm1-emu. |
| setup-fm1-emulator | github.com/DimmKirr/setup-fm1-emulator | MIT | m | CI-Action. |
| fm1-base | github.com/charlesvestal/fm1-base | MPL-2.0 | n | Host-Simulator-Konzept. |
| setup-fm1-toolchain | github.com/DimmKirr/setup-fm1-toolchain | MIT | n | Toolchain-Spiegel (Build von Melodee aus dem Quellcode für das Gerät). |
| mvave-fm1 (maxfridbe) | github.com/maxfridbe/mvave-fm1 | MIT | m | Praxisberichte: ROM-Sitzung hält etwa einen Lauf; Werks-V15 hat einen anderen Hash (S3, S5). |

### Firmware-Update und Rettung

| Name | Link | Lizenz | Rel. | Notiz |
|---|---|---|---|---|
| fm1-linux-update | github.com/fuleo/fm1-linux-update | MIT | n | V14 → V15; durch den vendorten Installer abgedeckt. |
| fm1_flasher.py | aroum | – | – | funktioniert nicht (b4). |
| fm1_ota.py (FM-1-RE) | AL-255/FM-1-RE/tools | WTFPL | n | Byte-genauer Client plus Mitschnitt-Prüfer; Testfall für die Suite (`tests/fixtures/fm1-v15-reflash-2026-09-04.json`). |
| FM-1 Transporter | github.com/kurogedelic/FM-1-transporter | MIT | m | Schon in den Anleitungen der Suite. |
| USB_KEY dongle notes | ip2k/lunar-modulator `docs/10-usb-key-dongle.md` | MIT | n | Hintergrund. |
| MvaveFM1Unbricker | github.com/Quixotic7/MvaveFM1Unbricker | MIT | **h** | Vendorte Kopie = upstream HEAD 517b938 (nur die README hat eine Zeile mehr). Grenzen siehe S1/S2. |
| flipper-fm1-recovery | github.com/merthsoft/flipper-fm1-recovery | Unlicense | n | Flipper als USB_KEY, PC-Teil für Windows. |
| fm-static | github.com/seajaysec/fm-static | MIT | m | Layout-Analyse aus Paket- und Dumptabellen; kein pauschales V15; seine „Cleanup“-Pakete sind riskant. |
| How to update (Medium) | medium.com | – | – | nicht abgerufen. |
| fwradar changelog | fwradar.com | – | – | nicht abgerufen. |
| M-Flasher | github.com/jvitkauskas/m-flasher | **MIT** (aroum nennt fälschlich GPL-3.0) | **h** | Gelegenheit 4; V16-Aufbau; behält Werksdaten. |
| fm1_rescue.py (Hub) | thefirmwarehub.com (Kopie in felucca-bsp) | MPL-2.0 | **h** | Gelegenheit 5. |
| Slipway | github.com/Discodave-Glitch/slipway | MIT | n | Windows. |
| FM1Kit | github.com/ocean/FM1Kit | MIT | m | Loader-Kennung `ota-FM1C_…`; Fortsetzen aus dem Loader. |
| FM-1 Transporter multiboard | github.com/Nicodesy06/FM1Trasmitter-for-pico | MIT | n | Profile für weitere RP2040-Boards (für Pico-Nutzer interessant). |
| m-bootloader | github.com/jvitkauskas/m-bootloader | MIT | m | Download-Modus über REC, Rettungsmenü; die Suite warnt schon (Bootbereich ändert sich). Offizielle Updates setzen den Original-Bootloader zurück. |
| FM-1 Swap | github.com/HyruleanHero1988/FM-1-Swap | MIT | n | Android. |
| FM-SWITCH | github.com/ocean/FM-SWITCH | keine | n | iOS. |

### Editoren, Librarians, Begleitsoftware

| Name | Link | Lizenz | Rel. | Notiz |
|---|---|---|---|---|
| FM1 Editor & Librarian | github.com/benny-sparra/fm1-dx7-patch-importer | MIT | **h** (Wissen) | `docs/fm1-research.md` (4300 Zeilen): V16-Hardwaretests, Objekt 4/5, Bank Export, Liste gefährlicher Befehle. |
| DXcompanion | dxcompanion.uk | – | – | nicht erreichbar. |
| Dexed | asb2m10 | GPL-3 | n | Parameter-SysEx. |
| FM-1 Workbench | github.com/thegiantsnail/fm1-workbench-public | GPL-3.0 | n | JS-DX7-Engine „am Gerät kalibriert“. |
| Virtual FM-1 | github.com/jbschooley/Virtual-FM-1 | GPL-3.0 | **h** | Gelegenheit 7; Melodee/Felucca/SLOOP aus dem Quellcode, Abgleich; Linux x86-64. |
| FM1VST | github.com/Quixotic7/FM1VST | GPL-3.0 | m | Vorbild für die Core-ABI; Melodee 1.4.5; nur macOS. |
| FM-1 Mobile | github.com/hb3p8/fm1-mobile-emu | GPL-3.0 | n | wasm-Builds, Melodee enthalten (294 MB). |
| sloop-fm1-go | jahlib | GPL-3.0 | n | nicht geklont; Android. |
| SLOOP 8-Track Web | smhulme | GPL-3.0 | – | nicht geklont. |
| FM-1 Pulses | mene311 | ? | – | nicht geklont; FM-1+VA. |
| FM-1 Utility | fm1-utility.pages.dev | – | – | nicht erreichbar. |
| fm1-read-voice | github.com/czietz/fm1-read-voice | MIT (im README) | **h** | Befehl 0x23 / Objekt 5 (Gelegenheit 6). |
| fm1-bank-sender | leomaimoni | – | – | Android. |
| fm1_soundbank_app | github.com/pfkellogg/fm1-bonus-box | keine | n | Banken senden; Provenienz von KingParamount. |
| mac-usb-midi-bridge | pfkellogg | MIT | – | macOS. |
| Felucca-WebApp | github.com/hugelton/Felucca-WebApp | GPL-3.0 | m | Editor mit `?mock=1`; Protokoll der Felucca-Familie. |
| FM-1 Screen Reader | charlesvestal/fm1-reader | MIT | n | nicht geklont; Barrierefreiheit. |
| FM1 Studio | thingsapart/fm1-studio | GPL-3.0 | n | nicht geklont; Android. |
| FM1 DECK | TonalPAK/FM1-DECK | GPL-3.0 | n | nicht geklont. |
| felucca-vst | github.com/cdodd/felucca-vst | GPL-3.0 | m | LV2 für Linux, nur Felucca-Familie mit Browser-Emulator. |
| Falua | ehn96/Falua | GPL-3.0 | – | JIANT-Engine. |
| SloopStudio | smhulme | GPL-3.0 | – | iOS. |
| FM-1 Pulses Felucca | mene311 | ? | n | Projektschreiben über das Editorprotokoll. |
| SLICE64-FM Editor | jeymadcat-ops | keine | – | |
| Felucca Editor (hunterxiong) | hunterxiong2026 | MIT | n | Felucca 1.5 per SysEx. |
| SLOOP Editor Skill | hunterxiong2026 | MIT | n | SLOOP 2.4.1. |
| Octo-Vave | github.com/amacias/octo-vave | MIT | n | Katalog mit Hardware-Validierung; als Quelle für den Katalog möglich. |
| FM1 MIDI Converter | github.com/balancechiang/fm1-midi-converter | GPL-3.0 | n | Format „FM1 V4 project“ (unklar, welche Firmware) [?]. |
| Ghoulbox VST3 | LexLongcoding | GPL-3.0 | – | Windows. |
| FM-1 for Android | github.com/HolidayMan/fm1-android | GPL-3.0 | n | Melodee 1.4.5, FM1 Open V14 aus dem Quellcode. |
| PocketFM | HolidayMan/fm1-ios | GPL-3.0 | – | |
| Harbor | github.com/markterm/harbor | keine (aroum nennt GPL-3.0) [?] | n | fm1-emu auf Android; Kern-1-Optimierungen sind upstream eingeflossen. |
| FM-1 Pocket | github.com/Prado-Design/fm1-pocket | GPL-3.0-only + SDK-Ausnahme | n | FM1 Open V14 als wasm (die Suite hat das schon). |

### Patches, Hardware, Dokumentation, Verwandtes

| Name | Link | Lizenz | Rel. | Notiz |
|---|---|---|---|---|
| fm1-factory-presets | github.com/KingParamount/fm1-factory-presets | Bänke CC0, Doku CC BY-SA 4.0, Tools MIT | m | schon eingebunden (`dx7_sources.py:42`); Protokoll der Updater-Wiederherstellung. |
| fm1-banks | github.com/mene311/fm1-banks | MIT | n | schon eingebunden. |
| This DX7 Cart… / NeuralDX7 | thisdx7cartdoesnotexist.com | – | n | Generator-Idee. |
| Ambient & Dreams | natlifesounds.com | kommerziell | – | |
| fm1-sustain-footswitch, Bonus Box, voice-tuner | pfkellogg | keine/MIT | – | Hardware. |
| fm1buddy | github.com/konsumer/fm1buddy | GPL-3.0 | n | Firmwarewechsel von SD; USB-MIDI-Host. |
| fm1-cartridge | github.com/andrerezemini/fm1-cartridge | GPL-3.0 | n | Melodee auf ESP32; enthält `sloop-2.8.4.fwsc` (nützlich als Testpaket). |
| FM-1 MIDI Guide, fm1-guide, Easy Guide, Tao of Mac | diverse | – | n | nicht erreichbar (github.io/Web). |
| FM-1 SysEx protocol | KingParamount | CC BY-SA | m | siehe oben. |
| OTA protocol docs | FM-1-RE | WTFPL | m | siehe oben. |
| Felucca BUILDING.md | hugelton/Felucca | GPL-3.0 | n | |
| FM-1 Firmware Database | stanleygurvich/fm1-firmware-database | GPL-3.0 | – | **403**. |
| FM-1 Firmware Atlas | github.com/wero1414/atlas-fm-1 | CC0 | n | `data/firmwares.json` als zweite Katalogquelle möglich. |
| FM-1 learning handbook, FM-1 Lab | itoyohane, web255078-lab | ?/keine | – | |
| fm1-dev-wiki (aroum 3.6) | github.com/sbegueria/fm1-dev-wiki | CC0 | m | Belegte Fakten zu Rettung (sicherer Modus nach zwei Fehlstarts), Flash-Karte und NOINIT. |
| fm1-firmware-collection (aroum) | github.com/2bbb/fm1-firmware-collection | MIT | n | Alle Firmwares als Submodule mit CI-Builds; Katalogquelle. |
| ip2k/mvave-fm1-open-firmware (aroum) | = Lunar Modulator | MIT | n | Dongle-Spezifikation. |
| fm1-manager (aroum) | github.com/bratta/fm1-manager | keine Lizenzdatei (aroum: MIT) [?] | m | Original-Backup durch Durchschalten und Auslesen (Gelegenheit 6). |
| fm1-backup-restore (aroum) | github.com/workeffortwaste/fm1-backup-restore | MIT | m | Volldump über Android; schreibt `key_mac` zurück (b3). |
| fm1-grid (aroum) | github.com/jasonbot/fm1-grid | GPL-3.0 | n | Vier fm1-emu-Instanzen; Vorbild für mehrere Zwillinge. |
| hdavid/fm1-emulator, weavejam/Felucca (aroum 3.4) | siehe b1 | GPL | n/m | |
| TransitFM1 | github.com/Quixotic7/TransitFM1 | GPL-3.0 | **h** | Gelegenheit 8 / S4. |
| Related M-VAVE (SMK-37, Chocolate, BlackBox, IR Box, MK300, SMC …) | diverse | diverse | –/n | Nur SMK-37 ist verwandt (gleicher Chip, USR bei 0xF4000, **Kalibrierung 0xC1000 gerätespezifisch**). Für die Suite nur relevant, wenn sie SMK-37 unterstützen soll. |
| Official (Produktseite, Downloads, V16-URL, V15, MIDI-Doku, Release Notes, Handbuch, FM1 Open) | m-vave.com, fm1_open | – | m | V16-Hash `d017fb65…` bestätigt mehrfach (M-Flasher, czietz, fm1_rescue). |

### Artikel, Videos, Communities (nur Titel, nicht erreichbar)

Artikel: Synth Anatomy (Review, V15, Librarian, Baud Girl, Felucca, SLOOP, X0X, Groove OS, Xfer „Shrub“), Piano & Synth,
Sonicstate, MatrixSynth (3), Time To House, Noizefield, Ars Technica. Videos: Synth Anatomy Review, Maks Makes CC,
Sound Import, V09 Upgrade, BLE-MIDI, Full song, FM-1+VA Tutorial, Felucca 0.9 Guide, Felucca First Look, Felucca Jam.
Communities: KVR, r/MVaveFM1, Hacker News, Elektronauts, Gearspace, Plugg Supply, GitHub-Topic m-vave.
Relevanz für die Suite jeweils – oder n.

### Firmwares, die für den Zwilling zählen (aus der Firmwareliste)

Mit eigenem Host- oder Browser-Build: Felucca (Browser-Emulator, `tests/hostsim.c`), ChoralRoot (eigener Emulator
`tools/emu`, Kern in FM1VST), FM1 Open (simulator.wasm, schon in der Suite), Skiff/Mini-Tone/PIGEON (felucca_web-Glue in
FM1VST), Groove OS (Browser-Emulator, Closed Source, kommerziell), Dinghy/foma/fm1-base (Host-Simulator). Melodee und
SLOOP haben **keinen** eigenen Emulator. Andere bauen ihn selbst (FM1VST, Virtual-FM-1, FM-1 for Android: Melodee 1.4.x)
oder nehmen fm1-emu. Keiner baut 1.6.0-rc.8; das macht die Suite als einzige.
