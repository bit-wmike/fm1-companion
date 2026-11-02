# Mitmachen bei der FM-1 Companion Suite

Danke, dass du helfen willst! Die Suite ist ein inoffizielles Community-Projekt für den M-VAVE FM-1
(GPL-3.0-only). Beiträge sind willkommen: Fehlerberichte, **Testberichte von echter Hardware**,
Übersetzungshilfen, Code und Dokumentation.

Bitte lies vorher kurz [SECURITY.md](SECURITY.md) (Sicherheitslücken nicht öffentlich melden) und den
[Verhaltenskodex](CODE_OF_CONDUCT.md). Wie der Code aufgebaut ist, steht in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Entwicklungsumgebung

Die App läuft mit Debian-/Ubuntu-Paketen, ohne pip und ohne Compiler auf dem Zielrechner.

```bash
git clone https://github.com/bit-wmike/fm1-companion.git
cd fm1-companion
# Systempakete (die Liste steht in fm1companion/data/deps.json):
sudo apt install python3 python3-pyqt6 python3-pyqt6.qtsvg python3-pyqt6.qtwebengine \
  python3-pyqt6.qtwebchannel python3-mido python3-rtmidi python3-numpy git qt6-translations-l10n
# empfohlen: python3-pyqt6.qtmultimedia ffmpeg alsa-utils pipewire-bin fluidsynth fluid-soundfont-gm
python3 -m fm1companion --check        # fehlt etwas?
python3 -m fm1companion --demo         # ohne FM-1: zwei simulierte Geräte
```

Für Tests zusätzlich `python3-pytest`. Zielversionen: Python ≥ 3.11, PyQt6 ≥ 6.4, NumPy ≥ 1.24 (Debian
Bookworm). Bitte keine Sprachmittel oder APIs, die erst später kamen (z. B. PEP 695, NumPy-2-Funktionen).

## Tests

```bash
# Unit-Tests (ohne Bildschirm)
QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_*.py fm1companion/vendor/unbricker/tests -q
# Oberflächen-Tests: jedes Skript einzeln, ohne Bildschirm, im Demo-Modus mit eigenem Temp-HOME
QT_QPA_PLATFORM=offscreen python3 tests/ui_flow_test.py
QT_QPA_PLATFORM=offscreen python3 tests/ui_smoke.py /tmp/screenshots   # Screenshots aller Seiten
```

Als root (z. B. im Container) braucht Qt WebEngine `QTWEBENGINE_DISABLE_SANDBOX=1`. Die UI-Skripte geben am
Ende `N/M bestanden` aus und enden mit Code 0 nur, wenn alles bestanden ist. Neue Funktionen bekommen einen
Test; Gerätezugriffe testest du gegen die Simulation (`fm1companion/demo_device.py`, `FM1C_DEMO=1`).

## Code-Stil

* Oberfläche **deutsch**, Anrede „du“, kurz und klar; einheitliche Begriffe („FM-1“, „Firmware“, „Backup“,
  „Preset“, „Projekt“, „Sample“). Keine englischen UI-Texte, keine „TODO“s in der Oberfläche.
* Jede Python-Datei beginnt mit `# SPDX-License-Identifier: GPL-3.0-only`.
* Zeilen bis 120 Zeichen. `ruff check --select E9,F63,F7 .` muss sauber sein; bitte keine neuen
  ungenutzten Importe.
* Nie im GUI-Thread blockieren (kein Netz, kein `sleep`, keine Geräteanfragen) – dafür gibt es `run_job`
  und `ctx.job` (siehe docs/ARCHITECTURE.md).
* Optionale Abhängigkeiten nur verzögert importieren, mit deutschem Hinweis („sudo apt install …“);
  jede neue Paketabhängigkeit gehört in `fm1companion/data/deps.json`.

## Sicherheitsregeln (Gerät und Nutzerdaten)

Diese Regeln sind wichtiger als jede Funktion:

1. **Nie ungefragt aufs FM-1 schreiben.** Jede schreibende Aktion braucht eine ausdrückliche Handlung und
   eine Bestätigung, die genau sagt, was überschrieben wird. Vorher lesen, damit Rückgängig möglich ist;
   Backups vor Installationen anbieten.
2. **Firmware-Update- und Bootmodus-SysEx** (`F0 22 24 35 7F`, `F0 22 24 35 7D`, Kennungsabfrage, Update-
   Datenstrom) dürfen nie aus Webseiten, Simulatoren oder Online-Editoren ans Gerät gelangen
   (`sims.sim_out_allowed`, `sims.OutGate`).
3. Neue Flash-, Rettungs- oder Schreibwege bleiben als **ungetestet** markiert (in der App und in
   [docs/UNGETESTET.md](docs/UNGETESTET.md)), bis jemand sie auf echter Hardware geprüft hat.
4. **Keine persönlichen Daten** in Code, Tests, Fixtures oder Doku: keine echten Namen, Rechnernamen,
   Home-Pfade, Seriennummern, Tokens. Beispiele nutzen `/home/user`, `pi`, „Max Mustermann“.
5. Alles, was die App nach außen gibt (Fehlerbericht, Diagnose, kopierte Berichte), läuft durch
   `fm1companion/redact.py`. Netzzugriffe nur über https und nur auf Klick – die zwei abschaltbaren
   automatischen Abfragen stehen in der README unter „Datenschutz“.
6. Code aus fremden Repos wird nicht ungefragt ausgeführt (siehe `firmware.check_tool`).

## Simulatoren und Studio-Zwilling bauen

Die mitgelieferten WebAssembly-Dateien unter `fm1companion/sims/` werden in einer Entwicklungsumgebung
gebaut, nie auf dem Rechner der Nutzer:

```bash
python3 tools/build_sims.py --repo fm1-open=/pfad/zu/fm1_open --repo melodee=/pfad/zu/melodee
python3 tools/build_sims.py --target melodee-studio --ref v1.6.0 --repo melodee=/pfad/zu/melodee
```

Benötigt git, WASI SDK 27 (lädt das Skript bei Bedarf) und für Melodee Python mit Pillow und fontTools.
Jeder Build schreibt `manifest.json` (Prüfsummen, Commit) und eine `NOTICE` mit Quellenangabe und
GPL-Angebot. Für ein Release die Quell-Tarballs erzeugen und anhängen:

```bash
python3 tools/make_source_tarballs.py --repo melodee=/pfad/zu/melodee --repo fm1_open=/pfad/zu/fm1_open
```

## Pakete bauen

```bash
python3 tools/build_deb.py     # dist/fm1-companion_<version>_all.deb
python3 tools/build_run.py     # dist/FM-1-Companion-Suite-<version>.run
```

## Hardware-Tests

Am meisten hilft ein ehrlicher Testbericht von echter Hardware (Vorlage „Hardware-Testbericht“ bei den
Issues): Gerät, Firmware vorher/nachher, was genau gemacht wurde, Ergebnis. Bitte nur mit Backup und auf
eigenes Risiko – siehe die Warnung in der README.

## Pull Requests

* Ein Thema pro Pull Request, kurze Beschreibung, was und warum.
* Tests laufen lassen und das Ergebnis in die PR-Beschreibung schreiben.
* Mit einem Beitrag erklärst du dich einverstanden, dass er unter GPL-3.0-only veröffentlicht wird.
