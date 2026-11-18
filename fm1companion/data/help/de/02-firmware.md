# Firmware wechseln & zurück zum Original

Das FM-1 kann verschiedene Firmwares tragen, z. B. **Melodee**, **Skiff**, **SLOOP**, **KARMA** oder die offizielle
Firmware von M-VAVE. Alles dazu liegt auf **einer** Seite: [Firmware+](app://page/discover) (Seitenleiste ›
*Daten & Werkzeuge*).

* **Alle Firmwares** – der Katalog: suchen, ansehen, im Simulator ausprobieren, *Zu meinen Firmwares*.
* **Installieren & Versionen** – [hier](app://page/firmware) wird installiert, aktualisiert und gewechselt: Versionen
  einer Firmware wählen, mehrere Versionen *Nur herunterladen* (auch für eine Firmware, die noch nicht drauf ist) und
  dann zwischen ihnen springen, *Eigene .fwsc-Datei installieren …*, *Firmware hinzufügen …* (Link oder Repo),
  *Zurück zum Original …* und der Rückweg von V16 über V15.
* **Neuigkeiten** und **Werkzeuge & Wissen** – neue Versionen der Firmwares, denen du folgst, und nützliche Links.

Ob es für dein FM-1 ein Update gibt, steht auch auf der [Übersicht](app://page/overview) (*Firmware & Updates*).

## So wechselst du

1. **Backup anlegen** (Strg+B). Die App macht vor jedem Update zusätzlich selbst eines (abschaltbar in den
   Einstellungen) und merkt sich, zu welcher Firmware es gehört.
2. In [Firmware+ › Installieren & Versionen](app://page/firmware) Firmware und Version wählen, *Auf aktives FM-1
   installieren*.
3. **Strom und USB nicht trennen**, bis die App „fertig“ meldet. Andere Programme, die MIDI senden, schließen.
4. Kommst du später zu einer Firmware zurück, bietet dir die App das passende Backup an.

## Ein Wechsel geht immer über die Original-V15

Wenn du zu einer anderen Firmware **wechselst**, spielt die App zuerst M-VAVEs unveränderte **Original-Firmware V15**
auf (mit Prüfsumme), dann die neue Firmware – zwei Installationen in einem Plan. Der Grund: Die Autoren der
Firmwares erwarten einen Start von der Original-V15, und ein direkter Wechsel von einer anderen Community-Firmware
hat schon mindestens ein Gerät unbrauchbar gemacht. Der Installationsplan sagt es: „Wechsel von X zu Y: Die App
spielt zuerst die Original-Firmware V15 auf, dann Y“.

Was du über den V15-Schritt wissen solltest:

* Die Original-Firmware hat **keinen Startschutz** – lass Strom und USB verbunden, während sie geschrieben wird.
* Gespeicherte Daten der alten Firmware **können im Flash bleiben** (der V15-Schritt schreibt nur den
  Firmware-Bereich). Die Original-Firmware liest sie womöglich als ihre eigenen: Presets können verstümmelt
  aussehen, bis die Ziel-Firmware installiert ist.
* Dieser Weg in zwei Schritten ist **von dieser App noch nie auf einem echten FM-1 gelaufen** – nur in der
  Simulation. Auf einem echten FM-1 gelaufen ist bisher: ein Update innerhalb von Melodee. Der Plan wiederholt das
  bei jeder Installation.
* Die Original-V15 (≈ 0,9 MB) wird geladen, wenn sie zum ersten Mal gebraucht wird; der Plan sagt, ob sie schon auf
  diesem Computer liegt. *Original-V15 für die Offline-Nutzung herunterladen* in
  [Firmware+ › Installieren & Versionen](app://page/firmware) holt sie vorab.
* Lehnt die laufende Firmware die Original-V15 ab (das FM-1 zeigt „FAILED -5“), wird nichts geschrieben. Die App
  sagt dann, welcher Weg bleibt.

* **Update oder Downgrade innerhalb derselben Firmware** (z. B. SLOOP 2.8.5 → 2.8.6, KARMA 3.1 → 3.0.5): direkt, ohne
  den V15-Schritt – auch wenn der Katalog sagt, dass die Firmware von V15 aus installiert wird (das FM-1 läuft schon
  mit ihr und ihrem Update-Loader). Ein Fork ist eine eigene Firmware – SLOOP → KARMA ist ein Wechsel.
* **Firmware nicht erkannt** (die App weiß nicht, was auf dem FM-1 läuft): gilt als Wechsel, also über V15.
* **Das FM-1 läuft schon mit der Original-V15**, oder du gehst **zurück zur Original-V15**: direkt.
* **V16**: wie bisher – zu V16 von einer Community-Firmware und der Rückweg von V16 gehen immer über V15.
* Das **Backup** entsteht vor dem ersten Schritt, wie bei jeder Installation.
* Bleibt der Wechsel nach dem V15-Schritt stehen (Kabel, Strom, Abbruch), läuft das FM-1 mit der Original-V15. Die
  App merkt sich das Ziel: [Firmware+ › Installieren & Versionen](app://page/firmware) zeigt *Weiter: … installieren*
  – der V15-Schritt wird nicht wiederholt. Ist er *während* eines Schritts stehen geblieben, schließe diesen zuerst
  auf der Seite [Rettung](app://page/recovery) ab.
* Experten können im Plan *Experte: direkt installieren, ohne den V15-Schritt* ankreuzen. Das ist standardmäßig aus,
  wird nicht gespeichert, und der Plan warnt, dass das nicht der Start ist, den die meisten Firmware-Autoren
  erwarten. Eine Einstellung dafür gibt es nicht.
* *Abbrechen* wirkt, solange nichts geschrieben wurde; bei einer Firmware der Felucca-Familie auch noch, während
  das FM-1 das Paket prüft. Sobald sich die Installation nicht mehr anhalten lässt, sagt der Knopf das.

## Zurück zur Original-Firmware

* Melodee bringt einen Weg **zurück zur offiziellen Firmware** (V15) mit; vorher wird gesichert.
* Die offiziellen Dateien und Updater gibt es bei M-VAVE: <https://www.m-vave.com/download>.
* Den aktuellen Stand der offiziellen Firmware und alternative Updater (auch für Linux) sammelt
  [awesome-fm-1](https://github.com/cicloid/awesome-fm-1).

## Neueres FM-1 (Logo auf der Vorderseite)

FM-1, die seit Ende 2026 verkauft werden – du erkennst sie am **aufgedruckten M-VAVE-Logo auf der Vorderseite** –,
haben einen anderen Speicherbaustein (UCUN UC25HQ80 statt des Puya-Bausteins). An ihnen ist nichts kaputt, aber
Firmware-Versionen von davor kennen den Baustein nicht. Auf so einem Gerät **speichern sie nichts**, und die meisten
lassen sich **nicht mehr aktualisieren oder ersetzen**: Das FM-1 hängt dann auf dieser Version fest, bis im
ROM-Bootmodus die Original-Firmware zurückgeschrieben wird
([Update-Modus & Rettung](help:03-update-modus), Stufe 2).

Was die App dagegen tut:

* Sie fragt dich **einmal pro Gerät**: „Hat dein FM-1 das M-VAVE-Logo auf der Vorderseite?“ (Ja / Nein / Weiß ich
  nicht). Über MIDI kann die App den Baustein nicht auslesen. Ändern kannst du die Antwort auf den Seiten *Geräte*
  (Knopf *Gerät …*) und *Übersicht*.
* Auf einem **neueren Gerät** installiert sie keine Version, die den Baustein nicht kennt – der Plan sagt das und
  nennt die erste Version, die den Baustein kennt (von dieser App nicht getestet). Einen Experten-Schalter gibt es dafür nicht.
* **Weiß die App nicht**, welches Gerät du hast, wird so eine Version nur installiert, wenn du im Plan bestätigst,
  dass dein FM-1 kein Logo auf der Vorderseite hat.
* Auf einem **früheren Gerät** ändert sich nichts.
* „Neueres Gerät“ bleibt haften: Ist es für ein Gerät einmal gemerkt – durch deine Antwort oder weil der Baustein
  ausgelesen wurde –, bleibt es so, bis du es selbst änderst (*Gerät …*, mit Bestätigung). Wird später unter demselben
  Gerät ein früherer Baustein gelesen, behandelt die App es weiter als neueres Gerät und sagt dir das; vielleicht
  sind es zwei FM-1 unter einem Profil.

Erste Versionen, die den neueren Baustein kennen (in den Quellen der Firmwares gelesen, Stand 17. November 2026):
Melodee 1.6.0, Skiff 2.2, SLOOP 2.8.5, KARMA 3.0.4, JIANT FM V10.6z, Felucca 1.8.2, ChoralRoot 0.29.1,
CoralFlower 0.7, X0X 1.2.1, FoMni 0.4.1, AMB-1 1.3.1, LOOM-8 0.13, Hortator 1.0.1, TransitFM1 0.1.4. Keine
veröffentlichte Version bekannt: ORBIT, gr!mm. Laut ihren Autoren auf diesem Baustein nicht erprobt: m-bootloader
(ein solches Gerät startete danach nicht mehr) und SLOOP 3, das ihn braucht. Bei allen anderen Firmwares des Katalogs
weiß die App es nicht – ein neueres Gerät behandelt sie dann als „nicht unterstützt“.

Andere Wege herauszufinden, welchen Baustein du hast: **M-Flasher** zeigt ihn unter *Device info* (FLASH CHIP:
`0xB36014` ist der neuere, `0x856014` der frühere), **m-bootloader** zeigt ihn in seinem Menü, und die Rettung
(Stufe 2) liest ihn im ROM-Bootmodus – die App merkt ihn sich dann von selbst für das Gerät.

Ehrlich gesagt: **Nichts davon ist mit dieser App auf einem neueren FM-1 gelaufen.** Die Original-V15 ist der Weg
zurück, den die Autoren von Felucca, ChoralRoot, TransitFM1 und des Rettungswerkzeugs für diese Geräte nennen; ob das
V16-Paket auf ihnen funktioniert, hat niemand aufgeschrieben. Das mitgelieferte Rettungswerkzeug
(MvaveFM1Unbricker 1.2.1) kennt beide Bausteine.

## Gut zu wissen

* Projekte und Presets einer Firmware passen nicht automatisch zu einer anderen. Deshalb: Backups pro Firmware.
* Zu Melodee 1.6 gehört eine Ressourcen-Datei (Schrift, Werkssounds). Ab 1.6.0 schreibt der Update-Loader sie
  selbst mit; fehlt sie trotzdem (z. B. beim ersten Update von 1.5.1), schreibt die App sie nach.
* Update auf **Melodee 1.6.0**: Backup ist dein Rückweg, Projekte können anders klingen – siehe
  [Melodee 1.6.0](help:14-melodee-1-6-0).
* Wenn etwas schiefgeht: [Update-Modus & Rettung](help:03-update-modus).

## USB-Hubs

Mit einem guten Kabel direkt am Computer geht das Installieren am einfachsten. Über einen **USB-Hub** reißt die
Verbindung öfter kurz ab, oder das FM-1 braucht nach seinen Neustarts länger, bis es wieder da ist – dann beginnt
eine Installation, wird aber nicht fertig. Die App erkennt deshalb einen Hub vor dem FM-1 und schaltet von selbst
in den **vorsichtigen USB-Modus** (steht im Installationsplan):

* sie wartet länger auf den Update-Loader und auf den Neustart, beantwortet die Anfragen des FM-1 langsamer und
  hat immer nur eine Antwort gleichzeitig unterwegs;
* reißt die Verbindung beim Schreiben ab, wartet sie auf **dasselbe** FM-1 im Update-Modus und startet das
  Schreiben neu (bis zu 3-mal) – nur mit dem Loader, der zum Paket gehört, genau wie *Installation abschließen* auf
  der Seite [Rettung](app://page/recovery). Alle Sicherheitsprüfungen bleiben gleich.

Einstellungen › *Vorsichtiger USB-Modus beim Flashen*: automatisch (Standard), immer oder nie.

Wenn es trotzdem nicht klappt:

1. Das FM-1 mit einem kurzen Datenkabel **direkt** an den Computer anschließen – am Raspberry Pi am besten an eine
   **schwarze USB-2-Buchse**. Wenn ein Hub nötig ist: einer mit **eigenem Netzteil**, ohne andere stromhungrige Geräte.
2. Der [Systemcheck](app://action/syscheck) zeigt, wie das FM-1 angeschlossen ist (Hubs, Geschwindigkeit, USB 2/3) und ob
   **USB-Stromsparen** (Autosuspend) für das FM-1 oder seinen Hub an ist. Die optionale **udev-Regel** der App
   schaltet es ab: `./install.sh --usb-power-rule` (oder das Häkchen im grafischen Installer), oder von Hand
   `sudo install -m 0644 packaging/udev/60-fm1-companion.rules /etc/udev/rules.d/` und
   `sudo udevadm control --reload-rules`. Das Debian-Paket liefert sie in `/usr/share/fm1-companion/udev/` mit.
3. Jede Installation schreibt ein **Flash-Protokoll** (jede Anfrage des FM-1, wiederholte Anfragen, Abbrüche,
   Kernel-Meldungen aus dieser Zeit). Es ist Teil des Fehlerberichts ([Hilfe › Fehlerbericht erstellen](app://action/report)) – bitte
   anhängen, wenn du ein Problem meldest.
