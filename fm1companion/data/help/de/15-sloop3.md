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

## Was sie nicht macht

* **Deine Samples sind nicht in einem `sloop3-backup`.** SLOOP 3 hat keinen Befehl, der sie herausgibt. Sie stecken
  in einer Kopie des ganzen Flash-Speichers oder in den Dateien, aus denen du sie gesendet hast.
* **Die App installiert SLOOP 3 nicht, und sie installiert m-bootloader nicht.** Sie erklärt den Weg und öffnet die
  Installer-Seite des Autors in deinem Browser.
* **Kein automatisches Backup** für SLOOP 3: Bei einer Beta liest die App nur, wenn du es willst.
* Das Studio hat keinen SLOOP-3-Zwilling. Die anderen Geräteseiten (Projekte, Presets, Samples) sprechen das
  Felucca-Editor-Protokoll, das SLOOP 3 nicht hat – nimm seinen Editor.
* Ein Sample mit dem Mikrofon aufnehmen geht im enthaltenen Editor nicht; zieh stattdessen eine Datei hinein.

## Bevor du es installierst

1. **m-bootloader ist Pflicht.** SLOOP 3 lässt sich nur über den MIDI MODE von m-bootloader installieren.
   m-bootloader ist ein quelloffener Bootloader von Julius Vitkauskas; seine Installation ersetzt den Boot-Bereich
   des FM-1. Das geschieht einmal, außerhalb dieser App: mit M-Flasher im USB-Download-Modus oder – auf der Firmware
   von M-VAVE – mit M-UPGRADE von M-VAVE und `fm1-uboot.fwsc`.
2. **Sichere zuerst den ganzen Flash-Speicher.** Das ist der einzige Weg zurück: SLOOP 2.8 oder die Firmware von
   M-VAVE kommen zurück, indem du dieses Backup mit M-Flasher zurückschreibst. SLOOP 3 liest die Projekte, Presets,
   Samples und Einstellungen von SLOOP 2.8 einmal, beim ersten Start, und schreibt sie nie.
3. **Neuere FM-1 (verkauft ab Ende 2026, M-VAVE-Logo auf der Vorderseite, Flash-Chip UCUN UC25HQ80): vorerst nicht
   installieren.** SLOOP 3 wurde auf dieser Revision nicht ausprobiert, m-bootloader ist dort wenig getestet, und sein
   Autor berichtet von einem solchen FM-1, der nach der Installation von m-bootloader nicht mehr startete. Im MIDI
   MODE liest die App den Chip aus und sagt, welcher es ist; für den neueren bietet sie die Installer-Seite nicht an.
   M-Flasher zeigt dasselbe unter FLASH CHIP: `0x856014` ist der ältere Chip, `0xB36014` der neuere.
4. Ein offizielles Update von M-VAVE spielt den Bootloader von M-VAVE zurück. m-bootloader muss danach neu
   installiert werden.

## Gut zu wissen

* Im MIDI MODE kann nur ein Programm mit dem FM-1 sprechen: Beende die App (oder steck ab und wieder an), bevor du
  die Installer-Seite im Browser benutzt.
* Startet SLOOP 3 nicht: Halte beim Einschalten OCT− für sein RESCUE oder PLAY für das Menü von m-bootloader.
* Das alles wurde am PC-Zwilling des Autors für SLOOP 3 3.3.6 und 3.3.7 geprüft (der Code der Firmware auf einem
  simulierten FM-1) und gegen den MIDI-MODE-Code von m-bootloader 2.4 auf einem PC – **von dieser App nie auf einem
  echten FM-1**.
* Mehr: [Firmware wechseln](help:02-firmware) · [Backups](help:04-backups) · [Links](help:10-links)
