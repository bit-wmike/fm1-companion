# Fahrplan: Studio-Zwilling

Leitbild: Das FM-1 ist das Instrument (skizzieren, ausprobieren), die Suite ist das Studio. Die Suite hat einen
digitalen Zwilling des FM-1: dieselbe Firmware, mit dem kompletten Gerätezustand aus dem Backup, identischer
Klang über die Lautsprecher am Rechner, in der Suite weiter bearbeitbar, als WAV ausgebbar und zurück aufs FM-1
schreibbar. Die Suite ersetzt keine DAW (Logic, Audacity), kann aber alles, was zum FM-1 gehört.

| Schritt | Firmware | Stand |
|---|---|---|
| 1 | **Melodee** (Priorität 1), passend zur Version auf dem Gerät (1.6.1, 1.6.0, 1.6.0-rc.7 und rc.8 mitgeliefert) | **fertig in 2.2.0**, 1.6.0 seit 2.3.0, 1.6.1 seit 2.3.2 (Seite „Studio“, virtuelles Gerät, Wiedergabe-Ziel „Im Studio“) |
| 2 | **SLOOP** (neueste stabile, zurzeit 2.8.4) | erst wenn Melodee gut läuft |
| 3 | **Skiff** (neueste stabile, zurzeit 2.1.1) | erst wenn Melodee gut läuft |
| 4 | **Original-Firmware:** vorerst über **FM1 Open** (M-VAVEs offener V14-Code), mit Hinweis, dass V16 abweichen kann | danach |
| – | **Original V16** | **pausiert, nicht gestrichen** |

## Stand 2.2.0 (Melodee)

Erledigt: Seite **Studio** (MUSIK, erster Eintrag) – vom aktiven FM-1 holen (Backup landet auch in der Liste), aus
Backup öffnen, letzten Studio-Stand je Geräteprofil automatisch laden; Versions-Banner (Zwilling vs. Gerät/Backup);
spielbares FM-1 (Bildschirm, LEDs, Tasten, Regler; Maus, Rechner-Tastatur, MIDI-Keyboard, Tasten des echten FM-1);
Projekte des Zwillings laden/abspielen, Song, Leertaste; WAV-Export (24 bit / 32 bit float, Song bis zum Ende,
Sekunden oder Durchläufe, Einzelspuren); Studio-Stand automatisch gesichert, Zwischenstände mit
Rückgängig/Wiederherstellen; Zurückschreiben mit Bestätigung, Sicherheits-Backup, Einstellungen nur auf Wunsch,
Versionssperre (Experten-Haken). Der Zwilling ist als **virtuelles Gerät** wählbar (Projekte, Komponieren, Presets,
Backups, Editor arbeiten über `StudioTransport`); er ist nie in `ctx.ports`, `ctx.is_virtual` kennzeichnet ihn,
automatische Backups und Firmware-Prüfung lassen ihn aus. Wiedergabe-Leiste: „Im Studio (identischer Klang)“
statt der Annäherung im Rechner (die bleibt nur, wenn es keinen Zwilling gibt). Neue Versionen bauen:
`docs/studio-build.md`.

Offen (Melodee): Ausgabe-Wahl für das Studio ist „bestmöglich“ (pactl move-sink-input des QtWebEngine-Tons);
Tempo/Last am Pi 5 messen; ein CPU-Lastmodell wie am Gerät (Overload-Shedding) gibt es im Zwilling nicht.

## Stand 2.3.0 (Melodee 1.6.0)

Erledigt: Zwilling für die fertige **Melodee 1.6.0** (Tag v1.6.0, Commit 8f0e251; Klebeschicht unverändert), rc.7
und rc.8 bleiben für Geräte auf diesen Versionen; `pick_build` nimmt für 1.6.0 genau 1.6.0, die Versionswarnung nennt
zwischen rc.x und 1.6.0 die neuen Pegel (CZ-1 −6 dB, Limiter −2 dBFS). Simulator auf 1.6.0. Projekt-Ansicht liest
Punch-Effekte, Song-Block- und Pattern-Presets (nur Anzeige; exakt klingen sie nur im Studio) – geprüft an einem
1.6.0-Projekt aus Melodees eigenem Code (`tools/melodee_fixtures/fixturegen16.c`, `tests/fixtures/musik16`).
Sicherheit beim Wechsel auf/ab 1.6: `fm1companion/melodee16.py` (Installationsplan, Backup-Sperre).

Offen: `fixturegen16.c` speichert den Song nur als laufende Musik (Objekt 0) – `project_save_as` mit Song-Presets
bricht im Host-Test-Aufbau die Ressourcen-Tabelle (nicht untersucht, nur Host).

## Stand 2.3.2 (Melodee 1.6.1)

Erledigt: Zwilling **Melodee 1.6.1** (Tag v1.6.1, Commit 165d177; Klebeschicht unverändert, Kennung FM-1_91061) neben
1.6.0, rc.7 und rc.8; `pick_build` nimmt für 1.6.1 genau 1.6.1, für 1.6.0 weiter 1.6.0. Simulator auf 1.6.1.
Werkzeug-Pin v1.6.1 (`KNOWN_TOOL_COMMITS`). 1.6.0 → 1.6.1 ist im Installationsplan ein normales Update.
Clock/Sync von 1.6.1 (gegen `midi_clock.c`, `editor.c`, `project.c` geprüft): „Song am FM-1 selbst starten“ ab einem
Abschnitt (BANK_SONG op 2 mit Zeile → `chain_prepare_from`; mit CLK INT sendet das FM-1 dann Song Position +
Continue), der Taktgeber der MIDI-Seite kann ab einem Takt starten (Song Position + Continue, `clock.song_position`)
und warnt, wenn das FM-1 mit CLK INT selbst Clock sendet. Hilfe „Melodee 1.6.0“ um 1.6.1 ergänzt (CLK beim
Projekt-Laden behalten, Backup stellt CLK wieder her).

Offen: Der Zwilling gibt die Clock des FM-1 (eigener Echtzeit-Ring in `usb.c`) nicht nach außen – das bräuchte eine
neue Klebeschicht und damit neue Builds aller Versionen. Clock-Ausgabe an echter Hardware ungetestet (auch bei
Melodee selbst nur automatisch geprüft).

## Zweiter Weg: fm1-emu (Prozessor-Emulation)

[joakimeriksson/fm1-emu](https://github.com/joakimeriksson/fm1-emu) (MIT) emuliert das FM-1 auf Prozessor-Ebene
und führt **unveränderte .fwsc-Pakete** aus – jede Firmware, jede Version, laut Projekt auch **Original V13/V15/V16**.
Es speichert den Flash als Sektor-Dateien (`.fm1save`), emuliert USB-MIDI für Firmwares, die USB abfragen
(Felucca-Familie: Melodee, SLOOP …, also auch das Editor-Protokoll der Suite) und kann USB-Audio aufnehmen.
Nachteil: langsamer (Melodee im Browser ~2,5-fach Echtzeit auf einem Laptop; auf dem Pi 5 vermutlich um Echtzeit
oder darunter – messen). WAV-Ausgabe geht unabhängig vom Tempo.

Plan: Der Studio-Zwilling bekommt eine austauschbare Engine. Erste Engine: Melodee aus dem Quellcode übersetzt
(schnell, Echtzeit am Pi). Zweite Engine: fm1-emu für alle anderen Firmwares/Versionen und als Ersatz, wenn es
keinen passenden Quellcode-Build gibt. Weitere Funde im Verzeichnis
[aroum/fm1-custom-fw](https://github.com/aroum/fm1-custom-fw) (Abschnitt „Emulators“): Quixotic7/FM1VST
(Felucca-Familie als DAW-Plugin aus dem Quellcode), weavejam/Felucca (rendert Felucca-Projekte offline als Mix +
4 Stems), hdavid/fm1-emulator (Desktop-Fork mit USB-MIDI).

## V16 (pausiert)

Der Nutzer will V16 ausdrücklich. Blocker: Der V16-Quellcode ist nicht offen; M-VAVE hat nur V14 als
`m-vave/fm1_open` veröffentlicht. Wege, sobald wieder Zeit ist:

- Bei jedem neuen Stand von `m-vave/fm1_open` prüfen, ob V15/V16 dazugekommen sind (dann wie Melodee bauen).
- M-VAVE nach dem V16-Code fragen (Forum: forum.m-vave.com).
- **Neu und am aussichtsreichsten:** fm1-emu (siehe oben) führt V16 schon aus. Offen: M-VAVEs Firmware steuert
  USB über Interrupts, die fm1-emu noch nicht nachbildet – ein Backup per USB geht damit noch nicht; Klang,
  Bedienung und Flash schon. Tempo auf dem Pi messen.
