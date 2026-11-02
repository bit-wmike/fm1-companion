# Melodee 1.6.0

Melodee 1.6.0 ist die fertige Version (2. November 2026) nach acht Vorabversionen (1.6.0-rc.1 bis rc.8). Was sich
für dich in der Suite ändert und worauf du beim Update achten solltest.

## Update in drei Schritten

1. **Backup machen, solange die alte Firmware läuft.** Die App macht vor dem Update selbst eines – kommst du von
   Melodee 1.5.1 oder älter, ist es Pflicht. **Dieses Backup ist dein Rückweg:** Ältere Firmware kann nicht lesen,
   was 1.6 speichert, und lehnt Backups von 1.6 ab.
2. In [Firmware+ › Installieren & Versionen](app://page/firmware) *Melodee* und *v1.6.0* wählen und installieren.
   Der Plan zeigt die Hinweise zu 1.6 (Backup, andere Lautstärken).
3. **Werkssounds:** Der Update-Loader von 1.6.0 schreibt Schrift, Werkssounds und Bluetooth-Programm selbst mit. Die
   App fragt danach nur nach. Sind sie schon da, schreibt sie nichts und meldet: *„Ressourcen schon vollständig“*.
   Von 1.5.1 oder älter klappt das beim ersten Mal nicht, weil dort noch deine Preset-Banken liegen. Die neue
   Firmware räumt sie beim Start um, danach schreibt die App die Werkssounds selbst. Anders als beim Firmware Hub
   musst du **nicht zweimal installieren**. Steht am FM-1 trotzdem **„Setup unfinished“**: *Schrift & Werkssounds
   nachschreiben* (gleiche Seite) oder dieselbe Version noch einmal installieren.

## Projekte können anders klingen

* **CZ-1-Spuren sind 6 dB leiser** als vorher.
* Der **Limiter** greift erst bei −2 dBFS statt −5,2 dBFS. Laute Mixe behalten mehr Spitzenpegel (lauteste Stelle
  etwa −3,5 statt −5,9 dBFS).
* Überall **TPDF-Dither**, und MASTER wirkt einmal am Ende auf den fertigen Mix.

Also nach dem Update die Balance deiner Projekte prüfen. Das gilt auch, wenn du von 1.6.0-rc.8 kommst.

## Aufnahme über USB

* Die **Stereo-Aufnahme liegt jetzt vor MASTER**, wie die vier Einzelspuren. Der Lautstärkeregler am FM-1 ändert den
  Aufnahmepegel nicht mehr, eine leise Einstellung heißt keine leise Aufnahme mehr. Pegel siehst du auf der Seite
  [Aufnahme](app://page/recorder).
* Den Ton **vom Rechner zum FM-1** stellst du mit **USB VOLUME** im HOME-Menü ein (Standard 50 % = der alte Pegel bei
  MASTER in der Mitte). MASTER regelt ihn nicht mehr.

## Neu in 1.6 – und wie die Suite damit umgeht

| Neu in Melodee 1.6 | In der Suite |
|---|---|
| Song mit 64 Abschnitten, Pausen je Spur, „Loop song“ | [Projekt-Ansicht](app://page/project_view) zeigt alle 64; MIDI-Export folgt dem Song |
| Spur 1–4 mit 16 Patterns (5–8 behalten 8), 32 Projektplätze | Projekt-Ansicht, Projekte, Backups, Komponieren kennen alle 16 bzw. 32 |
| Zeitgesteuerte Punch-Effekte (4 je Abschnitt, 27 Effekte) | In der Song-Tabelle angezeigt (nur lesen). Klingen exakt nur im [Studio](app://page/studio) und am FM-1, nicht im MIDI-Export |
| Presets je Song-Block und je Pattern | In der Song-Tabelle und der Pattern-Übersicht angezeigt (nur lesen). Gespielt wird zuerst das Block-Preset, dann das Pattern-Preset, dann der Klang der Spur. Exakt nur im Studio |
| KIT CR78, KIT FM, Bereichs-Bearbeitung, MIDI LEARN, Bluetooth-MIDI | am Gerät. MIDI LEARN gilt nur bis zum Ausschalten |

Punch-Effekte und Song-Presets bearbeitest du am Gerät. Der Web-Editor zeigt in seiner Pattern-Ansicht weiter nur
8 Patterns und spricht mit seinem Projekt-Befehl nur die Plätze 1–4 an. Ersetzt er ein ganzes Arrangement, löscht
das Punches und Presets. Backups behalten sie.

## Studio

Für 1.6.0 gibt es einen eigenen [Studio-Zwilling](help:13-studio). Für Geräte und Backups mit 1.6.0-rc.7 oder rc.8
bleiben deren Zwillinge dabei. Das Studio nimmt die Version deines FM-1 bzw. Backups. Weicht sie ab, sagt es dir das.
Zwischen rc.x und 1.6.0 ist das die Lautstärke (siehe oben).

## Zurück zu einer älteren Version

* Der Installationsplan warnt, wenn du von Melodee 1.6 auf eine ältere Melodee oder eine andere Firmware wechselst.
  Du musst ausdrücklich bestätigen.
* Ein **Backup von 1.6 spielt die App nicht auf eine ältere Melodee** zurück. Sie sagt dir vorher, warum, statt einen
  Gerätefehler zu zeigen. Nimm das Backup von vor dem Update auf 1.6.
* Rückkehr zur Original-Firmware löscht den Ressourcen-Bereich. Beim Weg zurück auf Melodee schreibt die App ihn neu.

## FM-1 mit UCUN-Flash

Manche FM-1 haben einen Flash-Baustein von UCUN (UC25HQ80, Kennung 0xB36014). Erst ab 1.6.0 speichert Melodee
darauf richtig. Die **USB-ROM-Rettung (Rettung Stufe 2) kennt diesen Flash noch nicht** und hält an, ohne zu
schreiben. Die App sagt dir das dann. Normale Installationen über Firmware+ sind davon nicht betroffen.
