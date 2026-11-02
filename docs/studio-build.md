# Studio-Zwilling: eine neue Melodee-Version hinzufügen

Der Studio-Zwilling ist Melodees eigener Firmware-Code, für den Rechner als WebAssembly übersetzt – zusammen mit
einer kleinen Klebeschicht (`tools/studio/melodee_studio.c`), die den NOR-Flash, den USB-SysEx-Pfad (Editor-
Protokoll), Tasten/Regler/LEDs, den Bildschirm und das Rendern nach außen gibt (ABI `st_*`, siehe
`fm1companion/sims/_studio/twin-core.js`). Mehrere Versionen liegen nebeneinander:

```
fm1companion/sims/melodee-studio/<version>/   manifest.json, twin.wasm, LICENSE, NOTICE
fm1companion/sims/_studio/                    gemeinsamer Host (Seite, Worker, AudioWorklet, Bedienfläche)
```

`studio.pick_build()` wählt beim Öffnen die Version des Geräts bzw. Backups, sonst die nächstältere (mit Warnung).

## Voraussetzungen (nur auf dem Bau-Rechner, nicht auf dem Pi)

* `git`, Python 3
* **Pillow** und **fontTools** – Melodees Kopfdatei-Generatoren (Schriften, Bilder) brauchen sie:
  `sudo apt install python3-pil python3-fonttools`
* **WASI SDK 27** (clang für wasm32-wasi). Ohne `--wasi-sdk DIR` lädt das Skript es nach `--work` (~125 MB).
* zum Prüfen: `node` (≥ 18) für `tests/test_studio.py`, QtWebEngine für `tests/ui_studio_test.py`

## Bauen

```sh
git clone https://github.com/keremimo/melodee /tmp/melodee        # oder vorhandenen Klon nehmen (nur gelesen)
python3 tools/build_sims.py --target melodee-studio --ref v1.6.0-rc.9 --repo melodee=/tmp/melodee
```

Das legt `fm1companion/sims/melodee-studio/1.6.0-rc.9/` an (Manifest mit Commit, Werkzeugkette, Prüfsummen,
`firmware_version`, `identity`, `abi`). Soll die Version dauerhaft zur Liste gehören, in `tools/build_sims.py`
unter `STUDIOS["melodee-studio"]["refs"]` eintragen (dann baut `--target melodee-studio` ohne `--ref` alle).

Ändert sich zwischen Versionen etwas an Melodees Schnittstellen (Namen in `hostsim.c`, Flash-Aufteilung,
Projekt-Speicher), muss `tools/studio/melodee_studio.c` angepasst werden; das Manifest hält die Prüfsumme der
Klebeschicht fest (`glue_sha256`), `tests/test_studio.py` prüft sie.

## Prüfen

```sh
python3 -m pytest tests/test_studio.py -q                      # Builds, Firmware-Kern in node, Backup-Rundreise
QT_QPA_PLATFORM=offscreen QTWEBENGINE_DISABLE_SANDBOX=1 python3 tests/ui_studio_test.py
QT_QPA_PLATFORM=offscreen QTWEBENGINE_DISABLE_SANDBOX=1 python3 tests/ui_studio_page_test.py
```

`tests/test_studio.py::test_builds_side_by_side` erwartet die mitgelieferten Versionen – neue Version dort
ergänzen. Die Pakete (.deb, .run) nehmen alles unter `fm1companion/` mit (`tools/build_common.app_files`);
`tests/test_packaging.py` prüft, dass Host-Dateien und jedes `twin.wasm` im Paket landen.

## Andere Firmwares

SLOOP, Skiff und FM1 Open folgen demselben Muster: eigener Eintrag in `STUDIOS`, eigene Klebeschicht
`tools/studio/<firmware>_studio.c` mit derselben `st_*`-ABI. Host (JS) und Python (`fm1companion/studio.py`,
`ui/studio_service.py`, Seite `ui/pages/studio.py`) bleiben gleich; `StudioService.firmware_for()` muss die neue
Firmware kennen. Siehe `docs/ROADMAP.md`.
