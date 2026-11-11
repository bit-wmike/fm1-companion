# Studio

Das FM-1 ist das Instrument zum Skizzieren, die Suite ist das Studio. Auf der Seite [Studio](app://page/studio)
läuft ein **digitaler Zwilling** deines FM-1: dieselbe Firmware wie auf dem Gerät (Melodee, Skiff, JIANT FM, SLOOP,
CoralFlower oder KARMA), mit seinem kompletten Zustand (Melodee: alle 32 Projekte; Skiff: Projekte A–D und die
Pattern-Bank; JIANT FM: die Sections aller Songs und der Song-Index; SLOOP: die Sections A–D, die FM6-Bank, die
SYN-Drumkits und die Sample-Slots; CoralFlower: die Patterns und der Song des Sequencers mit ihren Sounds; KARMA: die
Teile A–D des Songs, die Song-Reihenfolge, die HEX-Drumkits und die Sample-Slots;
laufende Musik, User-Presets, Einstellungen). Er klingt identisch – nur eben über die
Lautsprecher am Rechner.

## So geht's: FM-1 → Studio → WAV oder zurück

1. **Vom FM-1 holen:** liest ein komplettes Backup vom aktiven FM-1 (Name steht auf dem Knopf) und lädt es in
   den Zwilling. Das Backup landet zusätzlich in [Backups](app://page/backups) – es geht nichts verloren. Am FM-1
   wird nichts verändert (die Wiedergabe stoppt kurz).
   Alternativ **Aus Backup öffnen …** (jedes Melodee-, Skiff-, JIANT-FM-, SLOOP-, CoralFlower- oder KARMA-Backup – es
   startet der Zwilling der Firmware des Backups; SLOOP- und KARMA-Backups auch im Format von SLOOPs Web-Editor) oder
   **Letzten Studio-Stand öffnen**.
2. **Spielen und verfeinern:** Das FM-1 auf dem Bildschirm reagiert auf Maus, Rechner-Tastatur (untere Reihe
   Z S X D C …, obere Q 2 W 3 E …), ein MIDI-Keyboard (Auswahl „MIDI-Eingang“) und auf Wunsch die Tasten des
   echten FM-1. Regler: ziehen, Mausrad oder Pfeiltasten. **Leertaste** startet/stoppt. Projekte (Melodee 1–32,
   Skiff A–D, JIANT FM: die Sections A–D des aktuellen Songs, SLOOP: seine Sections A–D, KARMA: die Teile A–D des
   Songs) lädst du rechts in der
   Liste.
3. **Als WAV exportieren …:** ganzer Song bis zum Ende, eine feste Zahl Sekunden oder Durchläufe des Patterns.
   Standard ist WAV 24 bit (44,1 kHz Stereo), wahlweise 32 bit Fließkomma, gespeichert im Ordner
   „FM-1 Studio“ in deinem Musik-Ordner, Dateiname mit Projektname und Datum. **Einzelspuren (Stems):** je Spur
   eine eigene Datei, gleich lang wie der Mix – zum Weiterarbeiten in Logic oder Audacity. Jede Spur wird allein
   gerendert, mit ihrem Anteil an den gemeinsamen Effekten (Hall, Delay, Master); die Summe der Stems ist darum
   nicht bitgenau der Mix.
4. **Aufs FM-1 zurückschreiben …:** fragt nach, nennt das Gerät, macht vorher automatisch ein
   **Sicherheits-Backup** und schreibt dann Projekte, laufende Musik und Presets. Die Einstellungen des Geräts
   bleiben, außer du setzt den Haken. Geht nur mit vollem Zugriff (Melodee, Skiff, JIANT FM, SLOOP, CoralFlower oder
   KARMA verbunden – dieselbe Firmware wie die des Zwillings) und nie auf den Zwilling selbst. SLOOP schreibt nur,
   während sein Song gestoppt ist (STOP).

## Studio-Stand und Rückgängig

Der Zwilling merkt sich alles, was ein FM-1 nach dem Ausschalten noch hat (den Flash), und die App speichert das
automatisch – je Gerät, sobald sich etwas geändert hat und beim Verlassen der Seite. **Zwischenstand merken**
legt einen Punkt in die Liste, **Rückgängig** (Strg+Z) und **Wiederherstellen** (Strg+Umschalt+Z) springen
zwischen den Ständen (bis zu 30). Wie am Gerät gilt: Was im laufenden Projekt noch nicht gespeichert ist (SAVE),
gehört nicht zum Stand.

## Versionen

Den Zwilling gibt es in mehreren Versionen (Melodee 1.6.0-rc.7 … 1.6.1, Skiff 2.1.1 und 2.2, JIANT FM V10.6z2 – die
stabile Version von JIANTs Installer – sowie V10.60 und V10.61, SLOOP 2.8.5 – die Version von SLOOPs Installer – und
2.8.4, CoralFlower 0.7.1 – die seines Installers – und 0.7, KARMA 3.1 – die Version, die sein Installer ausliefert –
sowie 3.0.5). Die App wählt Firmware
und Version deines FM-1 bzw. des Backups. Passt die Version nicht genau, zeigt ein Hinweis oben: „Studio läuft mit
Melodee …, dein FM-1 hat … – Klang kann abweichen.“
Zurückschreiben auf ein FM-1 mit anderer Version geht dann nur mit dem Experten-Haken.

## Der Zwilling als Gerät

**Als Gerät benutzen** (oder oben in der Geräteauswahl „Studio-Zwilling … virtuell“) macht den Zwilling zum
aktiven Gerät: [Projekte](app://page/projects), [Komponieren](app://page/compose), Presets, Backups und der Editor
arbeiten dann mit ihm. Er ist immer als „virtuell“ gekennzeichnet; Firmware installieren, Rettung, USB-Audio und
automatische Backups gibt es für ihn nicht. Zurück: oben dein FM-1 wählen.

Beim Abspielen in Projekte und Komponieren heißt das Ziel ohne FM-1 **„im Studio (identischer Klang)“**. Die
alte Annäherung im Rechner bleibt nur, wenn es für die Firmware keinen Zwilling gibt.

## Grenzen

* Bisher **Melodee**, **Skiff**, **JIANT FM**, **SLOOP**, **CoralFlower** und **KARMA**. Die Original-Firmware (über
  FM1 Open) folgt später.
* Skiff: „Ganzer Song bis zum Ende“ endet nur mit SONG ONCE (sonst läuft Skiff in Schleife); der Skiff-Zwilling hat
  einen Kern (MENU › CPU 2 CORES wirkt nicht), kein USB-Audio und kein TRS-MIDI. Die Projektansicht liest Skiffs
  Songs noch nicht.
* JIANT FM: Die Stems der Spuren 5 und 6 bleiben still, solange MENU › TRACKS auf 4 steht (JIANTs Voreinstellung: T5
  und T6 schlafen). Der Zwilling hat einen Kern, kein Bluetooth (PROX bekommt kein Signal), keinen USB-Host und kein
  USB-Audio (die INPUT-Engine hört Stille). Eine JIANT-Version ohne eigenen Zwilling (z. B. V10.6c … V10.6z10) läuft
  mit dem nächstälteren Zwilling, mit Hinweis. Ab V10.6p hat JIANT 6 Songs: Songs 7 und 8 eines älteren Backups
  bleiben weg (das Studio nennt sie). Die Projektansicht liest JIANTs Songs noch nicht.
* SLOOP: „Ganzer Song bis zum Ende“ spielt SLOOPs Song-Reihenfolge (SAVE-Ebene › SONG MODE; die Reihenfolge gehört zu
  den Einstellungen) – nennt sie eine leere Section, läuft stattdessen die Schleife (bis zur Grenze des Exports, wie
  bei einem Song auf LOOP). Der Zwilling hat einen Kern (MENU › 2ND CORE wirkt nicht), kein USB-Audio, kein TRS-MIDI
  und keine USB-Rettung. Ein älteres SLOOP (z. B. 2.8.3) läuft mit dem 2.8.4-Zwilling, mit Hinweis. Die Projektansicht
  liest SLOOPs Sections noch nicht (SLOOPs Web-Editor zeigt sie).
* CoralFlower: keine Projektplätze – seine acht Patterns und der Song spielen über das Panel des Zwillings (SEQ, PLAY)
  oder mit Abspielen; Musik entsteht am Panel (CoralFlower hat keinen Editor). Die vier Stems sind seine Parts: Akkorde,
  Bass, Layer, Drums (ein Stem enthält weder den Klick des Metronoms noch den Einschaltklang; der Mix hat beides, wie das
  Gerät es spielt). Nach dem Laden eines Backups und nach dem Zurückschreiben startet CoralFlower neu (der Zwilling wie
  das FM-1) – so liest es das Zurückgespielte ein. Der Zwilling hat einen Kern, kein USB-Audio und kein TRS-MIDI; die
  Update-Geste (OCT− + OCT+ + HOME halten) tut dort nichts. Die Projektansicht liest CoralFlowers Patterns nicht.
* KARMA: Der Zwilling hat einen Kern (wie KARMAs eigener Ein-Kern-Build), kein USB-Audio (MENU › USB AUDIO
  = STEMS wirkt nicht), kein TRS-MIDI und keine USB-Rettung. KARMA speichert seine Backups im Format von SLOOP; ein
  SLOOP-Backup (bis 2.6) lässt sich auf ein KARMA-FM-1 schreiben (die SYN-Kits von SLOOP 2.5 bleiben weg, wie in KARMAs
  Editor), ein KARMA-Backup nie auf SLOOP. KARMAs drei gespeicherte Songs (SAVE auf der SONG-Seite) gehören nicht zum
  Backup – auch KARMAs eigenes Backup lässt sie weg. Die Projektansicht liest KARMAs Teile noch nicht.
* X0X (1.2.1, seine neueste Version, und 1.2.0): ein Projekt – sein Klang, die 32 Patterns, der Song und die
  Knopfbewegungen –, keine Projektplätze: Patterns und Song spielen vom Bedienfeld des Zwillings oder mit Abspielen. Die
  fünf Einzelspuren sind seine Teile: die 909, die 808, 303 A, 303 B und BREAK. X0X hat ein eigenes Backup-Format:
  **Aus Backup öffnen …** öffnet auch X0Xs eigene `.x0b`-Dateien, und X0X nimmt ein Backup nur ganz. Beim
  Zurückschreiben muss X0X gestoppt sein und fragt auf seinem Bildschirm (RESTORE FROM USB?): **SEL** am FM-1 drücken.
  Die Break-Loops in den Sample-Plätzen gehören nicht zu einem X0X-Backup. Der Zwilling hat einen Kern, kein USB-Audio,
  kein TRS-MIDI und keine Hardware-Kalibrierung; die Update-Geste bewirkt dort nichts. Die Projektansicht liest X0Xs
  Patterns nicht.
* FoMni (0.4.1 und 0.4) und AMB-1 (1.3.2 und 1.3.1) haben kein Backup über USB: ihre Zwillinge starten leer (oder aus
  einem Studio-Stand) und werden nie von einem FM-1 geladen oder auf eines geschrieben – spielen, als WAV exportieren
  und die Stände im Studio behalten. FoMnis vier Einzelspuren sind seine Harfenstimmen 1 und 2, der Akkord mit dem Bass
  und der Rhythmus; AMB-1 spielt von selbst, seine vier Einzelspuren sind seine Schichten: Pad, Glocken, Bass und Puls.
* LOOM-8 (0.14 und 0.13.1): sein Backup ist das von fm1-base (die Benutzer-Presets und die drei Projekte, nur als
  Ganzes; **Aus Backup öffnen …** öffnet auch LOOM-8s eigene `.loom8`-Dateien). Beim Zurückschreiben muss LOOM-8
  gestoppt sein und fragt auf seinem Bildschirm: **SEL** am FM-1 drücken. Seine Sample-Bank gehört nicht zum Backup. Die
  acht Einzelspuren sind seine Spuren. Der Zwilling hat einen Kern, kein Bluetooth, keinen USB-Host und kein USB-Audio;
  die Projektansicht liest LOOM-8s Projekte nicht.
* Der Zwilling hat keine CPU-Grenze: das Gerät lässt bei Überlast Stimmen weg, der Zwilling nie. Fließkomma-Teile
  können in den letzten Bits abweichen (unhörbar). Kein Bluetooth, kein USB-Host, kein Firmware-Update im
  Zwilling.
* Der Ton geht über den Standard-Ausgang des Systems. Ist unter „Ausgabe“ ein bestimmter Ausgang gewählt,
  verschiebt die App den Studio-Ton dorthin (braucht `pactl`, Paket pulseaudio-utils).
* Die Uhr des Zwillings läuft, solange die Seite sichtbar ist oder etwas spielt; sonst ruht er (spart Rechenzeit).
