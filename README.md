# FM-1 Companion Suite 2.3.2

> [!WARNING]
> **Inoffizielles Community-Projekt – nicht mit M-VAVE verbunden.**
> Firmware installieren, Rettung (Update-Modus, ROM-Bootmodus), „Ist mein FM-1 sauber?“/Reste aufräumen und das
> Zurückschreiben aus dem Studio verändern dein Gerät und geschehen **auf eigenes Risiko**. Mach vorher ein Backup.
>
> **Diese Funktionen sind noch nicht auf echter Hardware getestet:** Firmware-Wechsel, Zurück zum Original,
> Rettung Stufe 1–3, Reste aufräumen, die ROM-Prüfung von „Ist mein FM-1 sauber?“ und das Zurückschreiben aus dem
> Studio. Sie laufen bisher nur gegen Simulationen.
> An echter Hardware benutzt wurde bisher: ein Update **Melodee 1.6.0-rc.8 → 1.6.0** über die Suite (hat
> funktioniert), Backups, MIDI und die Grundfunktionen des Studios – an einem einzigen Gerät.
> Die genaue Liste: [docs/UNGETESTET.md](docs/UNGETESTET.md). Keine Gewährleistung (GPL-3.0, Abschnitte 15 und 16).

Die Desktop-Suite für das **M-VAVE FM-1** mit Firmwares der Felucca-Familie (**Melodee**, **Skiff**, **SLOOP**)
und der offiziellen Firmware. Alles rund ums Gerät in einer App – gebaut für den Raspberry Pi 5
(Raspberry Pi OS Bookworm/Trixie, KDE Plasma), läuft auf jedem Debian/Ubuntu.

## Was drin ist

| Bereich | Was es kann |
|---|---|
| **Übersicht** | aktives Gerät, Sicherung, Firmware & Updates, Schnellzugriff – Reiter „Zustand“: Live-Last, Speicher-Pool, Projekte, alle 384 User-Presets |
| **Editor** | der Original-Web-Editor der installierten Firmware, eingebettet und offline |
| **Geräte** | mehrere FM-1 mit eigenen Profilen, Firmware-Verlauf, von Gerät zu Gerät kopieren |
| **Klang** | Presets (mit Reiter FM-Voices/DX7), Samples & Kits (mit Reiter „Auf dem FM-1“) |
| **Studio** | der digitale Zwilling deines FM-1: dieselbe Melodee-Firmware im Rechner (1.6.1, dazu 1.6.0 und 1.6.0-rc.7/rc.8), mit dem kompletten Gerätezustand aus dem Backup – am Bildschirm, mit Tastatur oder MIDI-Keyboard spielen, Projekte abspielen, als WAV (24 bit oder 32 bit float, optional Einzelspuren) für Logic/Audacity ausgeben, Zwischenstände mit Rückgängig, mit Sicherheits-Backup zurück aufs FM-1 schreiben; auch als virtuelles Gerät für Projekte, Komponieren, Presets und Backups |
| **Musik** | Projekte (Plätze & Bibliothek / Ansicht & Export), Komponieren (Erzeugen / Aus MIDI-Datei), Aufnahme mit Audacity |
| **Daten & Werkzeuge** | Backups (Liste / Durchsuchen & Vergleichen), Firmware+, Rettung, MIDI (mit Klangerzeuger) |
| **Firmware+** | alle Firmwares an einem Ort: Katalog (suchen, ansehen, im Simulator ausprobieren, Links importieren, Neuigkeiten) und Reiter „Installieren & Versionen“ (Versionen von GitHub, installieren, wechseln, mehrere Versionen herunterladen, eigene .fwsc, zurück zum Original, Rückweg von V16) |
| **Rettung** | Update-Modus, unterbrochene Installation abschließen, „Ist mein FM-1 sauber?“, Rettung Stufe 2 |
| **Hilfe** | Hilfe-Center (offline), FAQ, Diagnose, Systemcheck |

Dazu der Rahmen: helles/dunkles/automatisches Design mit Akzentfarbe, einheitliche Symbole, Befehlssuche
(**Strg+K**), Hinweise mit Verlauf, Einrichtungs-Assistent, „Was ist neu“, Absturz-Dialog mit Fehlerbericht und
eine schmale Rechner-Anzeige unten rechts (CPU, RAM, Temperatur, freier Platz). Ein Klick darauf öffnet den
**Systemcheck**: Er liest das Kernel-Protokoll und erklärt auf Deutsch, wenn das FM-1 sich immer wieder trennt,
das Netzteil schwächelt, USB-Audio Fehler meldet oder der Pi zu heiß wird.
Welche Bereiche enthalten sind, zeigt die App selbst (Seitenleiste, Hilfe › Was ist neu).

## Installieren

**Aus dem Quellcode** (empfohlen, solange es noch keine Release-Pakete gibt):

```bash
git clone https://github.com/bit-wmike/fm1-companion.git
cd fm1-companion
./install.sh
```

`install.sh` prüft die Systempakete und installiert fehlende (fragt nach sudo; `--no-apt` überspringt das),
installiert die App für deinen Benutzer und legt einen Startmenü-Eintrag an. Weitere Schalter: `--desktop`
(Verknüpfung auf dem Schreibtisch), `--no-fluidsynth-fix` (siehe unten). Entfernen: `./uninstall.sh`
(`--all` zusätzlich Einstellungen und interne Daten) oder Startmenü › „FM-1 Companion deinstallieren“.
Updates: `git pull && ./install.sh` – deine Einstellungen und `~/FM-1` bleiben erhalten.

Ist Debians FluidSynth-Benutzerdienst (`fluidsynth.service`) aktiv, belegt er bei jeder Anmeldung die erste
Soundkarte (am Pi: HDMI). Der Installer schaltet ihn dann ab und sagt das deutlich (FluidSynth bleibt
installiert, die App startet es selbst); mit `--no-fluidsynth-fix` bleibt er unverändert. Der grafische
Installer fragt per Häkchen.

**Pakete (.deb/.run):** unter [Releases](https://github.com/bit-wmike/fm1-companion/releases), sobald
veröffentlicht. Bis dahin selbst bauen (Ergebnis in `dist/`):

```bash
python3 tools/build_deb.py        # dist/fm1-companion_2.3.2_all.deb  → sudo apt install ./dist/fm1-companion_2.3.2_all.deb
python3 tools/build_run.py        # dist/FM-1-Companion-Suite-2.3.2.run → sh dist/FM-1-Companion-Suite-2.3.2.run
```

Der `.run`-Installer ist ein grafischer Assistent (Lizenz, Hinweis, nur für mich oder für alle, Startmenü,
Desktop-Verknüpfung, Beispiel-Samples); ohne Fenster:
`sh FM-1-Companion-Suite-2.3.2.run --unattended [--prefix DIR] [--no-deps]`.

## Voraussetzungen

Pflicht: `python3` (≥ 3.11), `python3-pyqt6`, `python3-pyqt6.qtsvg`, `python3-pyqt6.qtwebengine`,
`python3-pyqt6.qtwebchannel`, `python3-mido`, `python3-rtmidi`, `python3-numpy`, `git`, `qt6-translations-l10n`.
Empfohlen: `python3-pyqt6.qtmultimedia`, `ffmpeg`, `alsa-utils`, `pipewire-bin`, `audacity`, `fluidsynth`,
`fluid-soundfont-gm`. Die Liste steht an einer Stelle: `fm1companion/data/deps.json`.
Prüfen: `fm1-companion --check`. Der Starter nutzt immer `/usr/bin/python3` – auch wenn ein venv aktiv ist.

## Ausprobieren ohne Gerät

`fm1-companion --demo` startet mit zwei simulierten FM-1 (Melodee 1.6 und SLOOP 2.8).
Weitere Schalter: `--syscheck` (Systemcheck im Terminal), `--no-splash`, `--debug`, `--version`.

## Datenschutz

Ohne Telemetrie, ohne Konten. Automatisch fragt die App nur zwei Dinge ab (abschaltbar): beim Anstecken eines
FM-1 die Versionsliste seiner Firmware auf GitHub und beim Öffnen des Katalogs den Katalog von awesome-fm-1
samt Vorschaubildern. Alles andere nur auf deinen Klick. Fehlerberichte werden nur lokal gespeichert.

* **Abschalten:** Einstellungen › „beim Anstecken eines FM-1 nach Firmware-Updates suchen“; im Katalog
  (Firmware+ › „Mehr“) „Katalog beim Öffnen automatisch aktualisieren“ und „Vorschaubilder aus dem Internet
  laden“.
* **Ohne automatische Netzzugriffe:** `FM1C_NO_NET=1 fm1-companion` schaltet beide automatischen Abfragen ab;
  Katalog und Link-Import arbeiten dann nur mit dem mitgelieferten Stand. Was du selbst startest (Firmware- oder
  Editor-Download, „Neu laden“ der Versionsliste), geht weiterhin ins Netz.
* **Fehlerberichte** (ZIP, Hilfe › Diagnose) enthalten Protokolle, Versionen und Einstellungen – ohne
  persönliche Pfade, Notizen, Rechnernamen, Zugangsdaten (z. B. ein GitHub-Token) und volle Seriennummern.
  Ob du ihn weitergibst, entscheidest du; schau ihn vorher an.
* Ein optionales **GitHub-Token** (Katalog › „Mehr“ › „GitHub-Token …“) steht in `config.json`, die nur für
  deinen Benutzer lesbar gespeichert wird (0600).

## Sicherheit

* **Risiko-Hinweis:** Vor der ersten Firmware-Installation, Rettungs- oder ROM-Aktion zeigt die App einmal einen
  Hinweis („Ich habe verstanden“). Die Bestätigungen jeder einzelnen Aktion bleiben.
* **Online-Editoren** aus dem Katalog sind fremde Webseiten: Editor-Befehle (SysEx) ans FM-1 erst nach deiner
  Zustimmung je Adresse; wechselt die Seite die Adresse, gilt wieder der sichere Filter (nur Noten, Controller,
  Takt). Firmware-Update und Bootmodus sind aus Webseiten immer gesperrt.
* **Werkzeuge aus Firmware-Repos:** Für „Schrift & Werkssounds“ führt die App das Python-Werkzeug der Firmware
  aus deren Repository aus. Bei eingebauten Firmwares ist der Commit je Version festgehalten – zeigt ein Tag
  später auf einen anderen Commit, fragt die App. Bei selbst hinzugefügten Firmwares fragt sie vor dem ersten
  Ausführen jedes Commits (Repo und Commit werden angezeigt).
* Sicherheitslücken bitte privat melden: [SECURITY.md](SECURITY.md).

## Wo liegt was

```
~/FM-1/                                   deine Daten (wird NIE automatisch gelöscht)
  Backups/<Gerät>/  Projekte/  Samples/  Kits/  Aufnahmen/  Firmware/
~/.config/fm1-companion/config.json       Einstellungen
~/.local/share/fm1-companion/             Geräteprofile, Werkzeug-Repos, Editor-Kopien (+ app/ bei Benutzer-Installation)
~/.local/state/fm1-companion/logs/        Protokolle
```

## Tests

```bash
QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_*.py fm1companion/vendor/unbricker/tests -q
QT_QPA_PLATFORM=offscreen python3 tests/ui_flow_test.py        # jedes tests/ui_*_test.py einzeln
QT_QPA_PLATFORM=offscreen python3 tests/ui_smoke.py /tmp/screenshots
```

Mitmachen: [CONTRIBUTING.md](CONTRIBUTING.md) · Aufbau des Codes: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Rechtliches

* Die FM-1 Companion Suite ist ein **inoffizielles** Projekt der Community. Sie ist nicht mit M-VAVE oder den
  Entwicklern der unterstützten Firmwares verbunden und wird von ihnen weder unterstützt noch geprüft.
* „M-VAVE“, „FM-1“ und andere Produkt- und Firmennamen sind Marken ihrer jeweiligen Inhaber. Sie werden hier nur
  beschreibend verwendet, um zu sagen, mit welchem Gerät die Software zusammenarbeitet.
* Die Software wird ohne jede Gewährleistung bereitgestellt; die Haftung ist ausgeschlossen, soweit gesetzlich
  zulässig (GNU GPL 3.0, Abschnitte 15 und 16). Die Benutzung – insbesondere das Flashen – geschieht auf eigenes
  Risiko.
* Mitgelieferte Simulatoren sind **aus dem offiziellen Quellcode gebaut** (M-VAVE FM1 Open) bzw. aus dem
  Quellcode der Firmware (Melodee); Quellenangaben und das GPL-Angebot stehen in
  `fm1companion/sims/*/NOTICE`.

## Mitwirkende und Lizenz

GPL-3.0-only. Danke an Melodee (Kerem Kiliç), Felucca (Leo Kuroshita / Hügelton
Instruments), Skiff (illuminus5), SLOOP (isod89), M-VAVE FM1 Open, awesome-fm-1 (cicloid, CC0-1.0) und Lucide (ISC).
Vollständige Liste: Hilfe › Über › Mitwirkende & Lizenzen (`fm1companion/data/credits.json`).
