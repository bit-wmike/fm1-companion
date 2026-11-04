# Studio

Das FM-1 ist das Instrument zum Skizzieren, die Suite ist das Studio. Auf der Seite [Studio](app://page/studio)
läuft ein **digitaler Zwilling** deines FM-1: dieselbe Melodee-Firmware wie auf dem Gerät, mit seinem kompletten
Zustand (alle 32 Projekte, laufende Musik, User-Presets, Einstellungen). Er klingt identisch – nur eben über die
Lautsprecher am Rechner.

## So geht's: FM-1 → Studio → WAV oder zurück

1. **Vom FM-1 holen:** liest ein komplettes Backup vom aktiven FM-1 (Name steht auf dem Knopf) und lädt es in
   den Zwilling. Das Backup landet zusätzlich in [Backups](app://page/backups) – es geht nichts verloren. Am FM-1
   wird nichts verändert (die Wiedergabe stoppt kurz).
   Alternativ **Aus Backup öffnen …** (jedes Melodee-Backup) oder **Letzten Studio-Stand öffnen**.
2. **Spielen und verfeinern:** Das FM-1 auf dem Bildschirm reagiert auf Maus, Rechner-Tastatur (untere Reihe
   Z S X D C …, obere Q 2 W 3 E …), ein MIDI-Keyboard (Auswahl „MIDI-Eingang“) und auf Wunsch die Tasten des
   echten FM-1. Regler: ziehen, Mausrad oder Pfeiltasten. **Leertaste** startet/stoppt. Projekte 1–32 lädst du
   rechts in der Liste.
3. **Als WAV exportieren …:** ganzer Song bis zum Ende, eine feste Zahl Sekunden oder Durchläufe des Patterns.
   Standard ist WAV 24 bit (44,1 kHz Stereo), wahlweise 32 bit Fließkomma, gespeichert im Ordner
   „FM-1 Studio“ in deinem Musik-Ordner, Dateiname mit Projektname und Datum. **Einzelspuren (Stems):** je Spur
   eine eigene Datei, gleich lang wie der Mix – zum Weiterarbeiten in Logic oder Audacity. Jede Spur wird allein
   gerendert, mit ihrem Anteil an den gemeinsamen Effekten (Hall, Delay, Master); die Summe der Stems ist darum
   nicht bitgenau der Mix.
4. **Aufs FM-1 zurückschreiben …:** fragt nach, nennt das Gerät, macht vorher automatisch ein
   **Sicherheits-Backup** und schreibt dann Projekte, laufende Musik und Presets. Die Einstellungen des Geräts
   bleiben, außer du setzt den Haken. Geht nur mit vollem Zugriff (Melodee verbunden) und nie auf den Zwilling
   selbst.

## Studio-Stand und Rückgängig

Der Zwilling merkt sich alles, was ein FM-1 nach dem Ausschalten noch hat (den Flash), und die App speichert das
automatisch – je Gerät, sobald sich etwas geändert hat und beim Verlassen der Seite. **Zwischenstand merken**
legt einen Punkt in die Liste, **Rückgängig** (Strg+Z) und **Wiederherstellen** (Strg+Umschalt+Z) springen
zwischen den Ständen (bis zu 30). Wie am Gerät gilt: Was im laufenden Projekt noch nicht gespeichert ist (SAVE),
gehört nicht zum Stand.

## Versionen

Den Zwilling gibt es in mehreren Melodee-Versionen. Die App wählt die Version deines FM-1 bzw. des Backups. Passt
sie nicht genau, zeigt ein Hinweis oben: „Studio läuft mit Melodee …, dein FM-1 hat … – Klang kann abweichen.“
Zurückschreiben auf ein FM-1 mit anderer Version geht dann nur mit dem Experten-Haken.

## Der Zwilling als Gerät

**Als Gerät benutzen** (oder oben in der Geräteauswahl „Studio-Zwilling … virtuell“) macht den Zwilling zum
aktiven Gerät: [Projekte](app://page/projects), [Komponieren](app://page/compose), Presets, Backups und der Editor
arbeiten dann mit ihm. Er ist immer als „virtuell“ gekennzeichnet; Firmware installieren, Rettung, USB-Audio und
automatische Backups gibt es für ihn nicht. Zurück: oben dein FM-1 wählen.

Beim Abspielen in Projekte und Komponieren heißt das Ziel ohne FM-1 **„im Studio (identischer Klang)“**. Die
alte Annäherung im Rechner bleibt nur, wenn es für die Firmware keinen Zwilling gibt.

## Grenzen

* Bisher nur **Melodee**. SLOOP, Skiff und die Original-Firmware (über FM1 Open) folgen später.
* Der Zwilling hat keine CPU-Grenze: das Gerät lässt bei Überlast Stimmen weg, der Zwilling nie. Fließkomma-Teile
  können in den letzten Bits abweichen (unhörbar). Kein Bluetooth, kein USB-Host, kein Firmware-Update im
  Zwilling.
* Der Ton geht über den Standard-Ausgang des Systems. Ist unter „Ausgabe“ ein bestimmter Ausgang gewählt,
  verschiebt die App den Studio-Ton dorthin (braucht `pactl`, Paket pulseaudio-utils).
* Die Uhr des Zwillings läuft, solange die Seite sichtbar ist oder etwas spielt; sonst ruht er (spart Rechenzeit).
