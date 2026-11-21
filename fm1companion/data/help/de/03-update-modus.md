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

## Nach einer unterbrochenen Installation

Nach einer Unterbrechung sagt die Meldung der App, was *wahrscheinlich* ist – sie hat das FM-1 nicht noch einmal
angesehen. Die Seite [Rettung](app://page/recovery) zeigt, was gerade wirklich erkannt wird:

* **Ein FM-1 im Update-Modus wird erkannt:** *Installation abschließen* schreibt das unterbrochene Paket noch
  einmal. Die App kennt es aus ihrem Journal und prüft, ob es zum wartenden Update-Loader gehört – sonst wird nichts
  gesendet.
* **Nichts wird erkannt und das FM-1 läuft normal:** Es wurde nichts geschrieben. *Unterbrochene Installation
  vergessen* entfernt die Notiz der App.
* **Nichts wird erkannt und das Display bleibt schwarz:** das FM-1 einmal aus- und einschalten und den Update-Modus
  versuchen (oben). Klappt das nicht, hilft der nächste Abschnitt.

Ehrlich: Das Abschließen einer unterbrochenen Installation ist bisher nur in der Simulation gelaufen, nicht auf
einem echten FM-1.

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

## Die mitgelieferten Werkzeuge: M-Flasher, m-bootloader, SLOOP 3

Die App bringt **M-Flasher** 0.12.1 (für das 64-Bit-Raspberry-Pi-OS und x86-64-PCs; für das 32-Bit-System gibt es
ihn nicht), **m-bootloader** 2.4 und **SLOOP 3** 3.4.0 mit – sie sind also auch ohne Internet da. Rettung ›
**Werkzeuge** zeigt jedes davon: die mitgelieferte Version, die benutzte Version, ob die Prüfsumme stimmt (die App
prüft sie vor jeder Verwendung und startet nie eine veränderte Datei) und wann sie zuletzt nach einer neuen Version
gesucht hat.

* **Neue Versionen:** Standardmäßig fragt die App einmal am Tag bei GitHub nach (Einstellungen › Allgemein: täglich,
  wöchentlich, nur beim Start oder nie; nur solange die Suche nach Firmware-Updates eingeschaltet ist). Eine neue
  Version wird nur angezeigt. **Aktualisieren** lädt sie herunter, prüft sie gegen die Prüfsumme von GitHub und
  behält deine jetzige Version als Rückfall, bis die neue einmal funktioniert hat. Ein neuer M-Flasher wird zuerst
  nur zum Lesen benutzt; einer mit geänderten Befehlen wird behalten, aber nicht benutzt.
* **Download-Modus ohne Passwort:** Der Installer richtet eine Regel ein, mit der du das FM-1 im ROM-Download-Modus
  (USB 4c4a:8057) erreichst, und lädt beim Start den Linux-Treiber `sg` – `./install.sh` tut das standardmäßig
  (`--no-download-mode-rule` lässt es weg; bekommt es keine Administratorrechte, gibt es die genauen Befehle aus).
* **Flash komplett sichern** (Rettung › Werkzeuge oder Backups › Flash-Backups): Die App bringt das FM-1 in den
  USB-Download-Modus – selbst, solange es über USB-MIDI läuft (Original-Firmware, Felucca-Familie, SLOOP 3, MIDI MODE
  von m-bootloader), sonst sagt sie dir, wie: mit m-bootloader beim Einschalten **PLAY** halten und **DOWNLOAD MODE**
  wählen, oder beim Einschalten **REC** halten (geht direkt dorthin). Dann liest M-Flasher den ganzen Flash (1 MiB)
  **zweimal**; jeder Lesevorgang wird mit der CRC-32 verglichen, die der Flash-Chip selbst berechnet, und nur wenn
  beide gleich sind, wird eine Kopie in `Backups/<Gerät>/Flash/` abgelegt, mit Beschreibung: Unit-ID, Flash-Chip,
  SHA-256, M-Flasher-Version, die Firmware, die die App kannte, und das Datum. Auf das FM-1 wird nichts geschrieben.
  Hat die App das FM-1 in den Download-Modus geschickt, startet sie es danach wieder. Unit ID und Flash-Chip, die
  M-Flasher liest, merkt sich das Geräteprofil (nur für das Gerät, das die App selbst in den Download-Modus geschickt
  hat, und nur, wenn kein anderes Profil diese ID hat).
* **Flash zurückschreiben** („Flash zurückschreiben …“ unter Rettung › Werkzeuge, „Zurückschreiben …“ unter Backups ›
  Flash-Backups): nur auf das FM-1, von dem die Kopie stammt. Die App liest zuerst das angeschlossene Gerät:
  **Unit-ID und Flash-Chip müssen die der Kopie sein** – eine Kopie eines anderen Geräts oder Chips wird abgelehnt,
  ohne Ausweg. Dann sichert sie den jetzigen Stand (zweimal gelesen, wie oben; ohne diese Sicherung wird nichts
  geschrieben) und fragt dich noch einmal, mit Gerät, Revision, Datum und Firmware der Kopie: **Der ganze Flash geht
  zurück, der Boot-Bereich (Bootloader) eingeschlossen**, und damit Firmware, Projekte, Sounds und Einstellungen von
  damals. Direkt vor dem Schreiben zeigt M-Flasher Unit-ID, Flash-Chip und die Sektoren, die er schreiben wird, und
  wartet auf „yes“; die App antwortet nur, wenn alles zu ihrer eigenen Prüfung passt, sonst schreibt M-Flasher
  nichts. Danach müssen M-Flashers eigene Prüfung (die CRC-32 des Chips) und ein frisches Lesen der Kopie gleichen;
  dann wird das FM-1 neu gestartet. Hat das Schreiben begonnen, lässt es sich nicht mehr abbrechen – dann das FM-1
  nicht abstecken; auch wenn die App geschlossen wird oder abstürzt, schreibt M-Flasher zu Ende, und beim nächsten Start sagt die App, was passiert ist, und fragt, was geschehen soll. Eine Kopie mit anderen Werksdaten (Kalibrierung, Bluetooth-Adresse) als das Gerät wird abgelehnt. Geht etwas schief, bleibt das FM-1 im Download-Modus, und die eben gemachte Sicherung geht auf
  demselben Weg zurück.
* **In den Download-Modus / FM-1 neu starten** (Rettung › Werkzeuge und das Menü Gerät): dasselbe Umschalten für
  sich allein, ohne etwas zu lesen – von der App nur an ein FM-1, das sie selbst an seinem USB-Anschluss erkannt hat,
  sonst sagt sie dir, wie. „FM-1 neu starten“ startet die Firmware aus dem Download-Modus wieder. Beides schreibt
  nichts.
* **m-bootloader installieren** (Rettung › Werkzeuge): schreibt das mitgebrachte m-bootloader 2.4 (Prüfsumme
  geprüft) **nur in den Boot-Bereich 0x00000–0x03FFF**. Vorher wird der ganze Flash gesichert (zweimal gelesen, nicht
  abwählbar), dann fragt die App dich noch einmal und nennt das Gerät und seine Revision. Auf einem neueren Gerät
  (Logo vorne) gibt es nur eine m-bootloader-Version, die dort bekanntermaßen läuft (2.4-395e6cc, nach dem Bericht
  eines Besitzers). Direkt vor dem Schreiben beantwortet die App die Frage von M-Flasher nur, wenn jeder geplante
  Sektor im Boot-Bereich liegt; danach muss ein frisches Lesen genau diesen Bootloader enthalten, und außerhalb des
  Boot-Bereichs darf sich nichts geändert haben. Die App merkt sich, dass dieses FM-1 m-bootloader hat. Dann: beim
  Einschalten **PLAY** halten für sein Menü.
* **m-bootloader entfernen (Original-Boot-Bereich)**: stellt den Boot-Bereich der Original-Firmware V15 von M-VAVE
  wieder her – die App lädt das V15-Paket einmal herunter, wenn es nicht auf diesem Computer ist, und prüft es. Vorher
  dieselbe Sicherung, dieselben Prüfungen; geschrieben werden nur die Sektoren, in denen dein Boot-Bereich von dem
  der V15 abweicht. Das macht auch rückgängig, was FM1 Open im Boot-Bereich geändert hat. Auf einem FM-1, das mit
  V16 kam, ist es der Boot-Bereich der V15, nicht unbedingt der ursprüngliche. SLOOP 3 wird nur über m-bootloader
  installiert: Ob es danach startet, ist nicht bekannt – die eben gemachte Sicherung ist der Rückweg. **Nur auf einem
  früheren Gerät** (ohne Logo vorne): Der Boot-Bereich der V15 wurde noch nie auf ein neueres geschrieben, deshalb
  lehnt die App dort ab und nennt stattdessen den Rückweg – das Zurückschreiben einer Sicherung des ganzen Flashs
  dieses Geräts von vor m-bootloader (sie bietet sie an, wenn es eine gibt). „FM-1 neu starten“ wird abgelehnt,
  solange ein Schreiben nicht geprüft endete (das FM-1 bleibt dann im Download-Modus, wo sich seine Sicherung
  zurückschreiben lässt).
* **Flash-Sicherung importieren …** (Backups › Flash-Backups, Rettung › Werkzeuge): übernimmt eine Kopie des ganzen
  Flashs, die M-Flasher selbst gemacht hat (seine `.bin` mit ihrer `.json`, anderswo abgelegt), in die Sicherungen
  der App – gegen ihre `.json` geprüft, abgelegt beim Gerät, dessen Unit-ID die `.json` nennt (das Abbild selbst
  enthält sie nicht; ist kein Profil als dieses Gerät bekannt, wählst du eines, geraten wird nichts). Eine
  importierte Kopie mit intaktem Boot-Bereich von vor m-bootloader ist der Rückweg, den die App auf einem neueren Gerät
  anbietet. m-bootloader installieren und entfernen stehen auch im Menü **Gerät**.
* **Warum der Original-Boot-Bereich wichtig ist:** Viele Firmwares der Felucca-Familie lehnen die Original-V15 ab,
  wenn der Boot-Bereich nicht der der V15 ist („FAILED -5“ auf dem FM-1, CHECK HEAD). Weiß die App, dass dein FM-1
  m-bootloader oder den Boot-Bereich von FM1 Open hat (aus einer Flash-Sicherung, von M-Flasher, von m-bootloader
  selbst oder aus dem Firmware-Verlauf), hält ein Plan, der in diese Falle liefe (erst V15, oder V15 selbst aus einer
  Firmware der Felucca-Familie – und nach FM1 Open jede Felucca-Firmware), an und bittet dich, zuerst den
  Original-Boot-Bereich wiederherzustellen. Ein Update derselben Firmware auf einem FM-1 mit m-bootloader geht
  weiterhin; der Plan warnt, dass der spätere Rückweg den Original-Boot-Bereich braucht.
* **Ehrlich gesagt – was an echter Hardware lief (20.11.2026, ein Raspberry Pi 5, zwei Geräte):** das Sichern an
  beiden Geräten (per Umschalten durch die App aus Melodee und aus SLOOP 3, mit REC beim Einschalten, über das Menü
  von m-bootloader), jede Kopie gleich einer von Hand mit M-Flasher gemachten; das Zurückschreiben nur an einem
  früheren Gerät und nur kleine Änderungen (ein Sektor, geprüft), die Ablehnung der Kopie eines anderen Geräts und
  das gesperrte Schließen der App während des Schreibens. Noch nicht gelaufen: Zurückschreiben an einem neueren
  Gerät (Logo vorne), eine große Änderung oder der Boot-Bereich, ein echtes Abmelden während des Schreibens.
  m-bootloader installieren und entfernen und „In den Download-Modus“ liefen über die App noch nicht an einem echten
  FM-1 (der Besitzer hat dasselbe von Hand mit M-Flasher gemacht). Firmware über den Download-Modus und SLOOP 3 über
  die App sind unten beschrieben; sie liefen noch nicht an einem echten FM-1.
* **Firmware über den Download-Modus** (ein FM-1 mit m-bootloader, auch eines mit laufendem SLOOP 3): Weiß die App,
  dass das Gerät m-bootloader hat, und wählst du unter Firmware+ eine Firmware der Felucca-Familie (Melodee, SLOOP 2,
  Skiff …) – ein Wechsel oder alles aus einem laufenden SLOOP 3 –, sagt der Plan **„über den Download-Modus
  (M-Flasher)“**, statt anzuhalten. M-Flasher schreibt dann nur von 0x04000 bis zum Ende des Pakets: Der Boot-Bereich –
  m-bootloader – bleibt, CHECK HEAD spielt also keine Rolle, und es gibt keinen V15-Zwischenschritt. Zuerst das
  Geräte-Backup (wo die Firmware eines hat) und der ganze Flash gesichert (zweimal gelesen); dann die Gefahrenfrage;
  die App antwortet M-Flasher nur, wenn die geplanten Sektoren genau die berechneten sind und alle ab 0x04000 liegen;
  danach muss ein frisches Lesen genau das Paket enthalten. Warnt M-Flasher, dass die installierte Firmware „ihre
  Daten an anderen Stellen ablegt“, fragt dich die App noch einmal und nennt, was verloren gehen kann (Projekte,
  Klänge und Einstellungen dieser Firmware; der Rückweg ist die eben gemachte Sicherung). Für die Original-V15, den
  Boot-Bereich von FM1 Open und ein Update derselben Firmware gelten die üblichen Regeln; auf einem neueren Gerät muss
  das Ziel eines sein, das die Hardware-Regel durchlässt.
* **SLOOP 3 installieren (MIDI MODE)** (Rettung › Werkzeuge, Menü Gerät, Firmware+ › SLOOP 3): Die App schreibt das
  mitgebrachte SLOOP 3 3.4.0 über den MIDI MODE von m-bootloader – offline, das Paket genau so gebaut, wie es die
  Installer-Seite des Autors baut. Nur wenn m-bootloader im MIDI MODE mit der Unit-ID dieses Geräts antwortet; zuerst
  wird dort der ganze Flash zweimal gelesen (jedes Lesen mit der eigenen Prüfsumme des Chips geprüft), dann die
  Gefahrenfrage. m-bootloader prüft das ganze Paket, bevor es etwas löscht, und schreibt nur 0x04000 bis zum Ende des
  Pakets. Danach kann die App es mit einem frischen Lesen im Download-Modus noch einmal prüfen. Ohne m-bootloader ist
  der erste Schritt „m-bootloader installieren“, jeder Schritt mit seiner eigenen Frage.
* **Zurück zu …** (Rettung › Werkzeuge, Menü Gerät): ein Knopf für den Rückweg – zur letzten Komplettsicherung des
  Flash dieses Geräts, zu SLOOP 3, zu einer Firmware der Felucca-Familie oder zur Original-V15 mit dem
  Original-Boot-Bereich (nicht auf einem neueren Gerät). Er zeigt den Weg jedes Ziels und startet dann nur die eigenen
  Schritte der App, jeden mit seiner eigenen Frage.

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
