# SLOOP 3 (öffentliche Beta)

**SLOOP 3** ist eine Neufassung von SLOOP von isod89: fünf Spuren (vier Synths und das Schlagzeug), 13 Engines,
8 Patterns je Spur, ein Song, Samples und Slices und ein eigener Web-Editor. Der Autor nennt es eine **öffentliche
Beta**: rechne mit Fehlern, mach Backups, verlass dich auf der Bühne noch nicht darauf. Es ist kein SLOOP 2.x mit neuer
Nummer – die App behandelt es als eigene Firmware.

## Was die App mit SLOOP 3 macht

* **Sie erkennt es.** Ein FM-1 mit SLOOP 3 erscheint als „SLOOP 3 3.3.7 (öffentliche Beta)“. Ein FM-1, der im
  MIDI MODE von m-bootloader wartet, erscheint als „m-bootloader – MIDI MODE“, mit dem gefundenen Flash-Chip.
* **Sein Editor.** Die Seite [Editor](app://page/editor) zeigt den eigenen Web-Editor von SLOOP 3 – die Datei des
  Autors, unverändert, in der App enthalten; er läuft also ohne Internet. Von dieser Seite erreichen nur die
  Editor-Befehle von SLOOP 3, Noten und Controller den FM-1.
* **Backups.** [Backups](app://page/backups) › „Backup jetzt“ speichert das Projekt (wie gespeichert und wie es in
  dem Moment ist), deine Sounds mit Favoriten und Theme, deine FM6-Bank und deine SYN-Kits in eine
  `sloop3-backup`-Datei. „Zurückspielen“ spielt sie auf einen FM-1 mit SLOOP 3 zurück. Halte vorher die
  Wiedergabe an.
* **Eine Kopie des ganzen Flash-Speichers**, solange der FM-1 im MIDI MODE von m-bootloader ist:
  [Firmware+](app://page/firmware) › SLOOP 3 › „Ganzen Flash jetzt sichern“. Die App liest nur und vergleicht ihre
  Kopie mit der Prüfsumme, die der Flash-Chip selbst berechnet.

## Sein Projekt in der App

SLOOP 3 hat **ein Projekt** auf dem FM-1. Die App liest es so wie das Backup – nur lesend:

* [Projekt-Ansicht](app://page/project_view) › „Vom FM-1 lesen“ zeigt es mit „laufende Musik“ (wie es gerade spielt,
  auch ungespeicherte Änderungen) oder Projekt 1 (das auf dem FM-1 gespeicherte): Spuren, die 8 Patterns je Spur mit
  ihren Noten, den Song, Tempo und Swing. Dort kannst du es anhören und als MIDI-Datei (Schlagzeug auf Kanal 10), als
  CSV oder als JSON exportieren. „Projektdatei öffnen …“ öffnet auch eine `.s3p` aus dem SLOOP-3-Editor, ein
  `.s3b`-Editor-Backup oder ein `sloop3-backup`.
* [Projekte](app://page/projects) holt es als `.s3p`-Datei in die Bibliothek – dieselbe Datei, die der Projects-Tab
  des Editors speichert und öffnet. Die Bibliothek zeigt Tempo und Länge; „Datei hinzufügen …“ nimmt `.s3p`- und
  `.s3b`-Dateien, „Als Datei speichern …“ legt eine Kopie ab, wo du willst.
* **Ein Projekt auf den FM-1 bringen kann die App noch nicht.** Nimm dafür den Projects-Tab des Editors.
* Beim Vorhören und im MIDI-Export nicht dabei: Fills, Arpeggiator, Harmonie und Strum, die Parameter-Locks (im JSON
  aufgeführt) und die eigenen Sounds der Patterns. Ein Projekt von einem neueren SLOOP 3 als 3.4.0 öffnet die App
  nicht – sie sagt es, statt zu raten.

## Was sie nicht macht

* **Deine Samples sind nicht in einem `sloop3-backup`.** SLOOP 3 hat keinen Befehl, der sie herausgibt. Sie stecken
  in einer Kopie des ganzen Flash-Speichers oder in den Dateien, aus denen du sie gesendet hast.
* **Die App installiert SLOOP 3 nur über den MIDI MODE von m-bootloader** – „SLOOP 3 mit dieser App installieren
  (MIDI MODE)“, offline aus dem mitgebrachten SLOOP 3 3.4.0, nach einer im MIDI MODE zweimal gelesenen Sicherung des
  ganzen Flashs (noch nicht an einem echten FM-1 gelaufen; siehe die Hilfeseite „Update-Modus & Rettung“). m-bootloader,
  das SLOOP 3 braucht, installiert die App auch: als ersten Schritt desselben Knopfs oder unter Rettung › Werkzeuge ›
  „m-bootloader installieren“. Die Installer-Seite des Autors bleibt der andere Weg.
* **Kein automatisches Backup** für SLOOP 3, außer du schaltest es ein (siehe unten): Bei einer Beta liest die App nur,
  wenn du es willst.
* Das Studio hat keinen SLOOP-3-Zwilling. Die anderen Geräteseiten (Presets, Samples) sprechen das
  Felucca-Editor-Protokoll, das SLOOP 3 nicht hat – nimm seinen Editor.
* Ein Sample mit dem Mikrofon aufnehmen geht im enthaltenen Editor nicht; zieh stattdessen eine Datei hinein.

## Zustand, Samples aus einer Flash-Kopie, mehr für Backups

* **Live-Werte.** [FM-1-Zustand](app://page/fm1) zeigt die CPU-Last von SLOOP 3 (Mittel und Spitze), die klingenden
  Stimmen, Tempo und Takt und einen Pegel pro Spur, alle 2 s, gelesen mit derselben reinen Leseanfrage, die sein
  Editor benutzt. SLOOP 2.8 zeigt CPU-Last, Tempo und seine vier Pegel genauso. Solange ein Backup, ein Zurückspielen
  oder eine Installation das FM-1 belegt, wird nichts gefragt.
* **Samples aus einer Flash-Kopie.** [Backups](app://page/backups) › „Flash-Backups“ listet die Kopien des ganzen
  Flash-Speichers (Datum, Größe, Chip und ob die Datei noch zur Prüfsumme vom Kopieren passt). „Samples in die
  Bibliothek“ holt USR1–USR4 als WAV-Dateien („SLOOP 3 USR1 …“) nach Samples › „SLOOP 3 (flash)“; „Samples aus einer
  Datei …“ macht dasselbe mit einer Kopie, die M-Flasher gespeichert hat. Offline – es wird nichts gesendet. SLOOP 2.8
  legt seine Samples an denselben vier Plätzen im selben Format ab; eine unter SLOOP 2.8 gemachte Kopie liefert also
  dessen Samples. „Zurückschreiben …“ schreibt eine Kopie über M-Flasher zurück, nur auf das FM-1, von dem sie stammt (siehe Update-Modus & Rettung).
* **Die eigene Backup-Datei des Editors (.s3b).** „Datei hinzufügen …“ übernimmt eine `.s3b` als SLOOP-3-Backup (das
  Projekt und deine Sounds – mehr enthält eine `.s3b` nicht); „Als .s3b …“ speichert ein SLOOP-3-Backup für das
  „Restore“ des Editors.
* **Hineinschauen und vergleichen.** [Durchsuchen & Vergleichen](app://page/backup_browser) zeigt bei einem
  SLOOP-3-Backup das Projekt (Format, Tempo), deine 32 Sounds (S01–S32) mit Favoriten, die Stimmen der FM6-Bank
  (B01–B27), die SYN-Kits und die Einstellungen, und vergleicht zwei Backups Teil für Teil und Sound für Sound.
  Zurück geht es nur als Ganzes („Zurückspielen“).
* **Automatisches Backup** für SLOOP 3 gibt es, aber **aus**: Backups › „auch für SLOOP 3“. Lesen, während SLOOP 3
  spielt, wurde an einem echten FM-1 noch nicht ausprobiert.

## Bevor du es installierst

1. **m-bootloader ist Pflicht.** SLOOP 3 lässt sich nur über den MIDI MODE von m-bootloader installieren.
   m-bootloader ist ein quelloffener Bootloader von Julius Vitkauskas; seine Installation ersetzt den Boot-Bereich
   des FM-1. Das geschieht einmal: mit dieser App (Rettung › Werkzeuge oder der erste Schritt von „SLOOP 3
   installieren“, mit M-Flasher im USB-Download-Modus) oder – auf der Firmware von M-VAVE – mit M-UPGRADE von M-VAVE
   und `fm1-uboot.fwsc`.
2. **Sichere zuerst den ganzen Flash-Speicher.** Das ist der einzige Weg zurück: SLOOP 2.8 oder die Firmware von
   M-VAVE kommen zurück, indem du dieses Backup mit M-Flasher zurückschreibst. SLOOP 3 liest die Projekte, Presets,
   Samples und Einstellungen von SLOOP 2.8 einmal, beim ersten Start, und schreibt sie nie.
3. **Neuere FM-1 (verkauft ab Ende 2026, M-VAVE-Logo auf der Vorderseite, Flash-Chip UCUN UC25HQ80): nur m-bootloader
   2.4 und SLOOP 3 3.4.0 oder neuer, auf eigenes Risiko.** Ihr Besitzer berichtet von einem solchen FM-1, auf dem
   m-bootloader 2.4 und SLOOP 3 3.4.0 laufen und Speichern klappt (2026-11-19) – außerhalb dieser App, ein Gerät.
   Ältere m-bootloader-Versionen sind dort riskant: Sein Autor berichtet von einem solchen FM-1, der nach 1.1 oder 1.2
   nicht mehr startete. Im MIDI MODE liest die App den Chip und die Version von m-bootloader aus; für ein neueres
   Gerät bietet sie die Installer-Seite nur mit m-bootloader 2.4 in dem berichteten Build (im MIDI MODE
   „2.4-395e6cc“) oder 2.5 und neuer an (die
   Flash-Kopie, die nur liest, im MIDI MODE immer). M-Flasher zeigt
   den Chip unter FLASH CHIP: `0x856014` ist der ältere Chip, `0xB36014` der neuere.
4. Ein offizielles Update von M-VAVE spielt den Bootloader von M-VAVE zurück. m-bootloader muss danach neu
   installiert werden.

## Gut zu wissen

* Im MIDI MODE kann nur ein Programm mit dem FM-1 sprechen: Beende die App (oder steck ab und wieder an), bevor du
  die Installer-Seite im Browser benutzt.
* Startet SLOOP 3 nicht: Halte beim Einschalten OCT− für sein RESCUE oder PLAY für das Menü von m-bootloader.
* Das alles wurde am PC-Zwilling des Autors für SLOOP 3 3.3.6, 3.3.7 und 3.4.0 geprüft (der Code der Firmware auf einem
  simulierten FM-1) und gegen den MIDI-MODE-Code von m-bootloader 2.4 auf einem PC.
* **An einem echten FM-1, einmal, nur lesend** (18.11.2026: ein früheres Gerät, ein Raspberry Pi 5): Die App hat SLOOP 3
  3.4.0 erkannt, der mitgelieferte Editor hat sich verbunden, ein SLOOP-3-Backup wurde gelesen (6 Teile, alle
  Prüfsummen gültig), der MIDI MODE von m-bootloader 2.4 wurde erkannt (nicht mit einem Update-Modus verwechselt), und
  eine Installation unter dem laufenden SLOOP 3 wurde abgelehnt, ohne dass etwas gesendet wurde. **Nicht an einem
  echten FM-1 ausprobiert:** das Zurückspielen eines SLOOP-3-Backups, das Flash-Backup im MIDI MODE, alles andere, was
  schreibt, und alles an einem neueren FM-1 (Logo vorn) – was dessen Besitzer von außerhalb der App berichtet,
  steht oben unter 3. **SLOOP 3 über die App installieren** (MIDI MODE, von Melodee 1.6.1 aus) lief am 21.11.2026
  einmal an diesem früheren Gerät – siehe [Update-Modus & Rettung](help:03-update-modus).
* Mehr: [Firmware wechseln](help:02-firmware) · [Backups](help:04-backups) · [Links](help:10-links)
