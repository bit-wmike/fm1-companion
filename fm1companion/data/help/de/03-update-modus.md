# Update-Modus & Rettung

Bleibt das FM-1 nach einem abgebrochenen Update dunkel oder startet nicht richtig, hilft fast immer der
**Update-Modus**.

## Update-Modus starten

1. FM-1 ausschalten.
2. **OCT− gedrückt halten** (nur diese Taste) und dabei einschalten.
3. **Drei kurze Lichtblitze** der Tastenbeleuchtung bedeuten: Das FM-1 ist im Update-Modus.
4. USB anschließen. In der App erscheint das Gerät mit dem Zusatz *(Update-Modus)*.
5. In [Firmware+ › Installieren & Versionen](app://page/firmware) die Firmware erneut installieren.

Melodee ab 1.3.5 mit Boot-Schutz geht nach einem unterbrochenen Schreibvorgang (auch Stromausfall) von selbst
in den Update-Modus zurück – einfach wieder einschalten und neu installieren.

## Wenn gar nichts mehr geht

Zeigt der Rechner ein Laufwerk **„WL82 UBOOT1.00“** und das Display bleibt schwarz, steckt der Chip in seinem
eingebauten Rettungsmodus. Dann hilft die Seite [Rettung](app://page/recovery), **Stufe 2**: Sie führt
dich Schritt für Schritt durch das mitgelieferte MvaveFM1Unbricker (mit Pflicht-Doppelsicherung des Flash).
Hintergrund steht im Abschnitt „Firmware update and recovery“ in
[awesome-fm-1](https://github.com/cicloid/awesome-fm-1).

Was Stufe 2 vor dem Schreiben prüft – an der Sicherung, die im **selben** Lauf gelesen wurde:

* **„Original-V15 zurückschreiben“** schreibt nur unter dem Bootkopf ab Werk (V13–V15). Zeigt die Sicherung den
  Bootkopf von V16 (M-VAVEs Updater schreibt ihn beim Installieren von V16 neu) oder einen unbekannten Kopf
  (z. B. m-bootloader), wird **nichts** geschrieben: Ob V15 darunter startet, ist nicht geklärt, und die V16-App
  (0x04000–0xA8FFF) kann der eingebaute Unbricker nicht schreiben. Dann hilft
  [M-Flasher](https://github.com/jvitkauskas/m-flasher) oder `fm1_rescue.py` aus
  [felucca-bsp](https://github.com/hugelton/felucca-bsp), das das Paket passend zum Bootkopf wählt.
* **„Einstellungen reparieren“** setzt 0x93000–0xE8FFF und 0xFC000–0xFEFFF zurück. Das sind nur beim
  **V15-Aufbau** reine Daten. Bei V16 liegt in 0x93000–0xA8FFF noch Firmware (Programmende, Werks-Voices) – dann,
  und bei unbekanntem oder beschädigtem Aufbau, wird nichts geschrieben.
* Eine Sitzung im ROM-Bootmodus hält meist nur **einen Werkzeuglauf**. Danach – auch nach einer Ablehnung – das
  FM-1 aus- und einschalten und neu in den ROM-Bootmodus bringen.

## Wenn das FM-1 gar nicht als „WL82 UBOOT1.00“ erscheint

Der Chip zeigt das Laufwerk erst, wenn er in den ROM-Bootmodus gefallen ist. Der Reihe nach (nach dem
Troubleshooting von MvaveFM1Unbricker), FM-1 direkt am Rechner:

1. **Zwei Minuten eingeschaltet lassen.** Felucca-Firmwares haben einen Watchdog und einen Startschutz: Nach zwei
   Fehlstarts hintereinander wechselt das FM-1 von selbst in den ROM-Bootmodus. Dann erneut nachsehen.
2. **OCT− und OCT+** zusammen halten, einschalten und 10 Sekunden weiter halten (Update-Modus, falls die Firmware so
   weit kommt; bei Melodee ist das die Kalibrierung).
3. **Drei schnelle Neustarts:** ein, 10 s warten, aus; ein, 10 s, aus; ein. Stürzt jeder Start ab, landet der
   dritte im ROM-Bootmodus. (So kommt ein FM-1 auch aus Versehen dorthin.)
4. Hilft nichts davon, bleibt nur Hardware: [Rettung](app://page/recovery), **Stufe 3** (FM-1 Transporter).

## Typische Ursachen für Abbrüche

* Wackeliges oder zu langes USB-Kabel, USB-Hub ohne eigenes Netzteil
* Unterspannung am Raspberry Pi – prüfe das mit dem [Systemcheck](app://action/syscheck)
* Ein anderes Programm (Browser-Editor, DAW) spricht gleichzeitig mit dem FM-1
