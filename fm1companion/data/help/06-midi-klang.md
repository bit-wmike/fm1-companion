# MIDI & Klangerzeuger

Das FM-1 ist ein USB-MIDI-Gerät. Die Suite nutzt das an mehreren Stellen:

* **MIDI-Werkzeuge** (Seite *MIDI*): Ports verbinden, mitlesen, Clock senden.
* **Klangerzeuger:** Melodees MIDI-Spuren 5–8 erzeugen auf dem FM-1 selbst keinen Ton. Ein Klangerzeuger auf
  dem Rechner (z. B. `fluidsynth` mit dem General-MIDI-Soundfont) spielt sie hörbar ab.
  Pakete: `sudo apt install fluidsynth fluid-soundfont-gm`.
* **MIDI-Datei → Pattern:** [Komponieren › Aus MIDI-Datei](app://page/workshop) macht aus einer MIDI-Datei ein Pattern für eine Spur.

Findet die App kein MIDI, prüfe im [Systemcheck](app://action/syscheck) die Zeile „MIDI“ – meist fehlt
`python3-rtmidi`.

## Projekte und Patterns anhören

In **Projekte › Ansicht & Export** und in **Komponieren** gibt es eine Abspiel-Leiste (Leertaste startet und stoppt):

* **Auf dem FM-1:** Die Noten gehen per MIDI an das aktive FM-1, und es spielt sie mit **den Klängen, die gerade
  auf seinen Spuren eingestellt sind** – genau so, wie es klingt. Die App liest dazu die Einstellung
  *GLO › SYSTEM › ROUT* (welcher MIDI-Kanal welche Spur spielt). Gespeichert wird dabei nichts. Ist eine Spur
  zur Aufnahme scharf (REC), startet die Wiedergabe nicht, damit das FM-1 nichts mitschneidet.
  Weichen die Klänge im Projekt von denen am Gerät ab, sagt die App das.
* **Song am FM-1 selbst starten:** Für die gerade gelesene laufende Musik spielt das FM-1 seinen Song selbst.
* **Im Rechner (Annäherung):** Ohne FM-1 rechnet die App eine Vorschau. FM6-Spuren klingen mit dem echten
  Patch aus dem Projekt, andere Engines nur ähnlich.

**Kein Ton?** Mit dem Knopf **„Ausgabe: …“** wählst du, wohin der Ton im Rechner geht. Ist das FM-1 als
USB-Audiogerät angeschlossen, wird es oft zum Standard-Ausgang – dann landet jeder Ton am FM-1 statt an deinen
Lautsprechern. Der [Systemcheck](app://action/syscheck) zeigt in der Zeile „Vorhören“, wohin der Ton gerade geht.

**Rückgängig:** In Komponieren macht **Strg+Z** jede Änderung rückgängig, **Strg+Umschalt+Z** oder **Strg+Y**
stellt sie wieder her. „Rückgängig am FM-1“ ist etwas anderes: Es spielt den Stand vor dem letzten Schreiben
zurück aufs Gerät.
