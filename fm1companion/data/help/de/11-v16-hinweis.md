# V16 – Hinweis

M-VAVE bietet die Werks-Firmware **V16** (Datei `FM-1.fwsc`, Kennung `FM-1_016`) zum Download an: acht Stimmen,
64-Step-Pattern, 128 Werks-Voices im DX7-Format. In der App ist V16 **experimentell – nur mit Backup**.

## Das Wichtigste in drei Sätzen

* V16 legt den Speicher des FM-1 **anders auf** als V15 und alle Community-Firmwares (Melodee, SLOOP, Skiff, JIANT).
  Beim Wechsel gehen Songs und Presets auf dem Gerät verloren – darum ist ein Backup Pflicht.
* Von Melodee aus geht V16 nur **über die Original-V15**: Melodee prüft vorher den Bootkopf und würde V16 sonst mit
  „FAILED -5“ ablehnen. Die App macht diesen Zwischenschritt automatisch.
* Der **Rückweg** (V16 → V15 → Melodee) ist mit echter V16 **noch nicht bestätigt**. Deshalb: nicht auf deinem
  Haupt-FM-1 ausprobieren, sondern – wenn du eins hast – auf einem zweiten, das auch mal ein paar Tage ausfallen darf.

## Was sicher ist und was nicht

Die App sagt bei jeder Aussage, woher sie kommt:

| Aussage | Woher |
|---|---|
| V16 schreibt beim Installieren 0x04000–0xA8FFF, V15 und Melodee nur bis 0x92FFF. | aus den Paketen; bei V15 an echter Hardware aufgezeichnet, bei V16 abgeleitet |
| Der Update-Loader ist in V13, V14, V15 und V16 byte-gleich. | aus den Paketen |
| Beim normalen Update auf V15 wurde der Bootkopf (0x0–0x3FFF) nicht angefragt. | Aufzeichnung an echter Hardware – Kopf im Gerät und im Paket waren dabei gleich |
| Ob der Update-Loader bei V16 (anderer Bootkopf) den Kopf mitschreibt. | **offen** – Melodees Quelltext geht davon aus, die Aufzeichnung kann es nicht ausschließen (Dump A ↔ B klärt es) |
| Ein FM-1, das sich als Version 16 meldete, ließ sich per normalem Update auf V15 zurücksetzen. | Aufzeichnung – aber es war ein V15-Abkömmling, keine echte V16 |
| Beim ersten Start formatiert V16 0xBA000–0xC9FFF, 0xDA000–0xE2FFF und 0xED000–0xEFFFF. | nur im Emulator beobachtet |
| Reste von V16 in 0x93000–0xA8FFF stören Melodee nicht. | im Emulator mit Melodee 1.5.1; für 1.6.0-rc.7 nur aus dem Quelltext (Sektoren ohne gültigen Melodee-Datensatz gelten als frei und werden vor dem Schreiben gelöscht), nicht am Gerät |
| Ob der Loader vor dem Schreiben alles löscht oder Sektor für Sektor. | offen (der Aufbau des Loaders spricht fürs Löschen vorab) |

## So läuft es in der App

* **Hin:** [Firmware+ › Installieren & Versionen](app://page/firmware) › „Zurück zum Original …“ › V16. Der Plan zeigt: Backup → Original-V15
  → V16 → Kennung prüfen. Während das FM-1 schreibt, steht dort rot **„Jetzt nicht abstecken“**, Abbrechen ist dann
  gesperrt. Am Ende sagt die App, was sie geprüft hat (Prüfsumme, Kennung `FM-1_016`) und was nicht (jedes Byte –
  das geht nur mit einem Dump).
* **Zurück:** Läuft V16, zeigt Firmware+ › „Installieren & Versionen“ „Rückweg von V16 …“. Die App spielt erst V15 auf, dann deine
  Zielfirmware und bei Melodee danach Schrift und Werkssounds (Ressourcen). Danach dein Backup zurückspielen.
* **Prüfen:** [Rettung](app://page/recovery) › „Ist mein FM-1 sauber?“.

## Ist mein FM-1 sauber?

* **Schnellprüfung (nur lesen, ohne Risiko):** Kennung, Version und bei Melodee ab 1.6 der Zustand der Ressourcen –
  die Firmware rechnet Kopf und Prüfsumme selbst nach. Sie sieht **nicht** in den restlichen Speicher.
* **Dump-Prüfung:** Eine Komplettsicherung (1 MiB) wird Sektor für Sektor mit allen bekannten Paketen verglichen:
  Bootkopf, Firmware, ein abgeschnittenes V16, Reste von V16, Melodee-Ressourcen. Ergebnis als Ampel.
  Den Dump bekommst du im ROM-Bootmodus („Im ROM-Bootmodus lesen & prüfen …“, nur lesen) oder als Datei.
* **V16-Reste aufräumen** (Expertenmodus der Karte, ROM-Bootmodus, **am echten Gerät ungetestet**): löscht nur
  4-KiB-Sektoren in 0x93000–0xA8FFF, die laut einer frisch gelesenen Doppel-Sicherung noch genau V16 enthalten – und
  nur, wenn im Flash byte-genau eine bekannte Firmware mit V15-Aufbau steht (nie bei V16). Ablauf: erst lesen und
  planen (nichts wird gelöscht), dann nach deiner Bestätigung („AUFRÄUMEN“) neu sichern, Sektor für Sektor löschen
  und prüfen, am Ende den ganzen Flash vergleichen. Bricht etwas ab, merkt sich ein Journal den Stand; derselbe Knopf
  setzt fort. Angenommen (nicht geprüft) ist, dass der Chip beim Befehl „Sektor löschen“ genau 4 KiB löscht – die
  App prüft danach jeden 64-KiB-Block und hält sofort an, wenn mehr fehlt. Nach allem, was bisher bekannt ist
  (Emulator, Quelltext), sind die Reste harmlos: Liegenlassen ist genauso in Ordnung.
  Zum Üben: Häkchen „Simulation“ in Stufe 2 – dann läuft alles gegen den Übungs-Flash des Unbrickers.

## Testplan für deinen ersten echten V16-Test (am besten mit einem entbehrlichen FM-1)

Vier Komplettsicherungen, jeweils im ROM-Bootmodus gelesen und mit „Ist mein FM-1 sauber?“ geprüft:

1. **Dump A** – vor V16 (Melodee läuft, Backup gemacht).
2. **Dump B** – direkt nach dem V16-Update. Normalerweise startet V16 sofort; dann zeigt B auch die
   Erststart-Formatierung. Vergleich A ↔ B: Wurde wirklich bis 0xA8FFF geschrieben, blieb der Bootkopf gleich
   (oder steht danach der Kopf aus V16 drin)?
   Was hat sich bei 0xE0000–0xE4FFF (Loader-Zwischenspeicher) geändert?
3. **Dump C** – nach dem Rückweg auf V15. Liegen die V16-Reste noch da?
4. **Dump D** – nach Melodee. Läuft Melodee 1.6.0-rc.7 mit den Resten? Sind alle Sounds da?
5. Optional **Dump E** – nach „V16-Reste aufräumen“: nur die Rest-Sektoren sind leer, alles andere gleich D?

Absichtlich abgebrochene Updates bitte **nicht** ausprobieren – auch nicht auf einem entbehrlichen FM-1.

## Wenn der Rückweg nicht klappt

* Erst der normale **Update-Modus** (OCT− beim Einschalten halten, siehe „Update-Modus & Rettung“).
* Nimmt das FM-1 dort nichts mehr an oder bleibt dunkel: [Rettung › Stufe 2](app://page/recovery)
  (ROM-Bootmodus „WL82 UBOOT1.00“ mit dem eingebauten Unbricker). Er schreibt die Original-V15 und weigert sich,
  V16 zu schreiben (er schreibt nur bis 0x92FFF – V16 wäre danach abgeschnitten). Ausprobiert haben wir ihn mit
  V16 auf dem Gerät noch nicht.
* Steht nach V16 der **Bootkopf von V16** im Gerät (die Sicherung zeigt es), schreibt Stufe 2 die V15 **nicht**:
  Ob V15 unter diesem Kopf startet, ist offen. Dann [M-Flasher](https://github.com/jvitkauskas/m-flasher) oder
  `fm1_rescue.py` aus [felucca-bsp](https://github.com/hugelton/felucca-bsp) benutzen (wählt V15 oder V16 passend
  zum Kopf). „Einstellungen reparieren“ ist bei V16-Aufbau ebenfalls gesperrt – dort liegt in 0x93000–0xA8FFF
  noch Firmware.
