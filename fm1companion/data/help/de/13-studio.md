# Studio

Das FM-1 ist das Instrument zum Skizzieren, die Suite ist das Studio. Auf der Seite [Studio](app://page/studio)
läuft ein **digitaler Zwilling** deines FM-1: dieselbe Firmware wie auf dem Gerät (Melodee, Skiff, JIANT FM, SLOOP,
CoralFlower, KARMA, Felucca, ChoralRoot FM-1, X0X, FoMni, AMB-1, LOOM-8 oder M-VAVEs FM1 Open), mit seinem kompletten Zustand (Melodee: alle 32 Projekte; Skiff: Projekte A–D und die
Pattern-Bank; JIANT FM: die Sections aller Songs und der Song-Index; SLOOP: die Sections A–D, die FM6-Bank, die
SYN-Drumkits und die Sample-Slots; CoralFlower: die Patterns und der Song des Sequencers mit ihren Sounds; KARMA: die
Teile A–D des Songs, die Song-Reihenfolge, die HEX-Drumkits und die Sample-Slots; FM1 Open: seine 16 Projekte, die
DX7-Bibliothek und die Sample-Bank; laufende Musik, User-Presets, Einstellungen). Er klingt identisch – nur eben über
die Lautsprecher am Rechner. (Ausnahme FM1 Open: siehe unten „Die Original-Firmware und FM1 Open“.)
Auch **Felucca** selbst, die Grundlage der Familie, hat einen Zwilling (Projekte A–D, die User-Presets mit ihren
FM6-Patches und die Sample-Slots USR1–USR3); er öffnet Felucca-Backups (auch die von Feluccas eigenem Web-Editor) und
holt von einem Felucca-FM-1 und schreibt dorthin zurück wie die anderen. Auch ChoralRoot FM-1, X0X, FoMni, AMB-1 und
LOOM-8 haben Zwillinge; was bei ihnen anders ist (Loop-Plätze, ein Backup nur als Ganzes, kein Backup über USB), steht
unten unter „Grenzen“.

## So geht's: FM-1 → Studio → WAV oder zurück

1. **Vom FM-1 holen:** liest ein komplettes Backup vom aktiven FM-1 (Name steht auf dem Knopf) und lädt es in
   den Zwilling. Das Backup landet zusätzlich in [Backups](app://page/backups) – es geht nichts verloren. Am FM-1
   wird nichts verändert (die Wiedergabe stoppt kurz). Eine Ausnahme: FM1 Open kann ein gespeichertes Projekt nicht
   direkt herausgeben – das FM-1 lädt jedes gespeicherte Projekt der Reihe nach in seinen Arbeitsbereich, und am Ende
   legt die App dein Arbeitsprojekt zurück. Speichere ungespeicherte Änderungen vorher am FM-1 (die App fragt nach): Wird
   dazwischen das Kabel gezogen, kann das Arbeitsprojekt nicht zurückgelegt werden.
   Alternativ **Aus Backup öffnen …** (jedes Melodee-, Skiff-, JIANT-FM-, SLOOP-, CoralFlower-, KARMA-, Felucca-, ChoralRoot-, X0X-, LOOM-8- oder
   FM1-Open-Backup – es
   startet der Zwilling der Firmware des Backups; SLOOP- und KARMA-Backups auch im Format von SLOOPs Web-Editor) oder
   **Letzten Studio-Stand öffnen**.
2. **Spielen und verfeinern:** Das FM-1 auf dem Bildschirm reagiert auf Maus, Rechner-Tastatur (untere Reihe
   Z S X D C …, obere Q 2 W 3 E …), ein MIDI-Keyboard (Auswahl „MIDI-Eingang“) und auf Wunsch die Tasten des
   echten FM-1. Regler: ziehen, Mausrad oder Pfeiltasten. **Leertaste** startet/stoppt. Projekte (Melodee 1–32,
   Skiff A–D, JIANT FM: die Sections A–D des aktuellen Songs, SLOOP: seine Sections A–D, KARMA: die Teile A–D des
   Songs, FM1 Open 1–16) lädst du rechts in der
   Liste.
3. **Als WAV exportieren …** (in der oberen Knopfreihe): eine feste Zahl Sekunden, Durchläufe des Patterns oder der
   ganze Song bis zum Ende. „Ganzer Song“ ist nur bei Melodee voreingestellt, dessen Song-Kette am Ende stoppt. Skiff,
   JIANT FM, SLOOP, KARMA, Felucca, CoralFlower und X0X stoppen nur, wenn ihr Song auf einmaliges Abspielen gestellt
   ist – ein Song in Schleife läuft bis zur Grenze von 15 Minuten, und die App sagt das danach. Bei Zwillingen ohne
   Song (FM1 Open, ChoralRoot, LOOM-8, FoMni, AMB-1) ist die Option aus. **Abbrechen** stoppt einen laufenden Export
   und lässt keine Datei zurück.
   Standard ist WAV 24 bit (44,1 kHz Stereo), wahlweise 32 bit Fließkomma, gespeichert im Ordner
   „FM-1 Studio“ in deinem Musik-Ordner, Dateiname mit Projektname und Datum; eine vorhandene Datei wird nie
   überschrieben (die neue bekommt eine Nummer). **Einzelspuren (Stems):** je Spur eine eigene Datei
   (`…_track1.wav` …), gleich lang wie der Mix – zum Weiterarbeiten in Logic oder Audacity. Mix und alle Stems starten
   vom selben Schnappschuss des Zwillings, sie liegen also zeitlich genau übereinander. Jede Spur wird allein
   gerendert, mit ihrem Anteil an den gemeinsamen Effekten (Hall, Delay, Master); die Summe der Stems ist darum
   nicht der Mix – Pegel und Dynamik weichen ab.
4. **Aufs FM-1 zurückschreiben …:** fragt nach, nennt das Gerät, macht vorher automatisch ein
   **Sicherheits-Backup** und schreibt dann Projekte, laufende Musik und Presets. Der Dialog sagt, woher der Inhalt
   des Zwillings stammt. Wurde er von genau diesem FM-1 geholt, werden nur die Teile geschrieben, die du im Studio
   geändert hast; der Rest bleibt auf dem Gerät, wie er ist. Wurde der Zwilling leer gestartet oder stammt er aus
   einem Backup, von einem anderen Gerät oder aus einem älteren Stand, sagt das eine rote Warnung, und es braucht
   einen zweiten Haken – dann wird alles Aufgezählte auf dem FM-1 ersetzt. Die Einstellungen des Geräts
   bleiben, außer du setzt den Haken. Geht nur mit vollem Zugriff (Melodee, Skiff, JIANT FM, SLOOP, CoralFlower,
   KARMA oder FM1 Open verbunden – dieselbe Firmware wie die des Zwillings) und nie auf den Zwilling selbst. SLOOP und KARMA schreiben nur,
   während ihr Song gestoppt ist (STOP). JIANT FM: Zwilling und FM-1 auf verschiedenen Seiten von JIANTs Wechsel von
   8 auf 6 Songs (V10.6p) lassen sich nicht aufeinander schreiben.

## Studio-Stand und Rückgängig

Der Zwilling merkt sich alles, was ein FM-1 nach dem Ausschalten noch hat (den Flash), und die App speichert das
automatisch – je Gerät, sobald sich etwas geändert hat und beim Verlassen der Seite. **Zwischenstand merken**
legt einen Punkt in die Liste, **Rückgängig** (Strg+Z) und **Wiederherstellen** (Strg+Umschalt+Z) springen
zwischen den Ständen (bis zu 30). Wie am Gerät gilt: Was im laufenden Projekt noch nicht gespeichert ist (SAVE),
gehört nicht zum Stand. Rückgängig bringt auch zurück, woher der Stand stammt (die Zeile unter den Knöpfen).

Bleibt der Zwilling einmal von selbst stehen (sein Prozess wurde beendet, meist weil dem Rechner der Speicher ausging), sagt die App das und bietet an, den automatisch gespeicherten Stand wieder zu öffnen. Solange das Fenster minimiert ist oder eine andere Seite gezeigt wird, pausiert der Zwilling, um Rechenzeit zu sparen – außer er spielt oder exportiert gerade. Setzt der Ton auf einem ausgelasteten Rechner aus, zählt das Studio das unter „Ton und MIDI“ mit und vergrößert seinen Tonpuffer von selbst (etwas mehr Verzögerung, weniger Knackser).

## Versionen

Den Zwilling gibt es in mehreren Versionen (Melodee 1.6.0-rc.7 … 1.6.1, Skiff 2.1.1 und 2.2, JIANT FM V10.6z2 – die
stabile Version von JIANTs Installer – sowie V10.60 und V10.61, SLOOP 2.8.5 – die Version von SLOOPs Installer – und
2.8.4, CoralFlower 0.7.1 – die seines Installers – und 0.7, KARMA 3.1 – die Version, die sein Installer ausliefert –
sowie 3.0.5, FM1 Open V14). Die App wählt Firmware
und Version deines FM-1 bzw. des Backups. Passt die Version nicht genau, zeigt ein Hinweis oben: „Studio läuft mit
Melodee …, dein FM-1 hat … – Klang kann abweichen.“
Zurückschreiben auf ein FM-1 mit anderer Version geht dann nur mit dem Experten-Haken.
Felucca: 1.8.1 – die Version von Feluccas Installer – und 1.8.0 (eine 1.8.2-Beta läuft mit dem Hinweis auf dem
1.8.1-Zwilling).

## Die Original-Firmware und FM1 Open

Die meisten FM-1 laufen mit M-VAVEs **Original-Firmware V15** (oder der neueren V16). Ihr Code ist nicht offen, darum
gibt es keinen Zwilling von ihr. M-VAVE hat eine offene Firmware veröffentlicht, **FM1 Open** (V14,
github.com/m-vave/fm1_open): eine eigene, neue Firmware – 8 Spuren, 16 Projekte, 8 Sample-Pads, eigene Bedienung –, die
die **128 Werks-DX7-Stimmen der V15** in ihrer Reihenfolge (aus dem V15-Paket) und das Update-Protokoll übernimmt. Ihre
Klangerzeugung auf dem Gerät ist ein geschlossenes Binärteil; M-VAVEs Browser-Simulator rechnet stattdessen mit einer
offenen Engine, und der FM1-Open-Zwilling des Studios ist genau dieser Simulator-Code.

* **FM-1 mit FM1 Open:** Der Zwilling ist seine eigene Firmware (V14). Holen, Zurückschreiben und Backups (in eigenem
  Format: das Arbeitsprojekt, die 16 Projekte, die vier Bänke der DX7-Bibliothek, die Sample-Bank) laufen über FM1
  Opens eigenes Dateiprotokoll. Zum Lesen eines Projekts lädt das FM-1 es kurz; danach zeigt es wieder das Projekt von
  vorher, als geändert markiert. Projekte, die im Backup leer sind, bleiben auf dem FM-1, wie sie sind (FM1 Open kann
  über USB keinen Platz löschen). Der Klang folgt M-VAVEs Simulator und kann leicht vom Gerät abweichen.
* **FM-1 mit der Original-V15/V16:** Das Studio startet stattdessen den FM1-Open-Zwilling, mit einem Hinweis: dieselben
  Werksstimmen, aber eine andere Firmware – Bedienung, Projekte, Effekte und Klang können abweichen (V16 hat außerdem
  den Detune korrigiert). Die Original-Firmware hat **kein Backup-Protokoll**: Von ihr lässt sich nichts holen und
  nichts zurückschreiben (einzige Sicherung ist ein Komplett-Dump unter [Rettung](app://page/recovery)). Was die App
  für V15 kann, ist M-VAVEs eigene Stimmen-Wiederherstellung: Unter [FM-Voices](app://page/voices) überschreibt
  **Original-V15: Werks-Voices wiederherstellen …** alle 128 Stimmen in den Bänken A–D mit den Werksstimmen (aus
  M-VAVEs per Prüfsumme festgelegtem V15-Paket). Die Stimmen, die vorher auf dem FM-1 waren, **lassen sich vorher nicht
  sichern** – selbst gespeicherte Stimmen gehen verloren. Eigene Stimmen in eine V15-Bank zu schreiben, bietet die App
  nicht an (das bräuchte erst einen Test am Gerät).

## Der Zwilling als Gerät

**Als Gerät benutzen** (oder oben in der Geräteauswahl „Studio-Zwilling … virtuell“) macht den Zwilling zum
aktiven Gerät: [Projekte](app://page/projects), [Komponieren](app://page/compose), Presets, Backups und der Editor
arbeiten dann mit ihm. Er ist immer als „virtuell“ gekennzeichnet; Firmware installieren, Rettung, USB-Audio und
automatische Backups gibt es für ihn nicht. Zurück: oben dein FM-1 wählen.

Beim Abspielen in Projekte und Komponieren heißt das Ziel ohne FM-1 **„im Studio (identischer Klang)“**. Die
alte Annäherung im Rechner bleibt nur, wenn es für die Firmware keinen Zwilling gibt.

## Grenzen

* Bisher **Melodee**, **Skiff**, **JIANT FM**, **SLOOP**, **CoralFlower**, **KARMA** und **FM1 Open** – der auch für
  die Original-Firmware V15/V16 einspringt (siehe oben).
* FM1 Open: Der Zwilling hat kein Bluetooth, kein USB-Audio, kein TRS-MIDI und keinen Updater; seine offene Engine
  steht für die des Geräts. Die Projektansicht liest FM1 Opens Projekte nicht.
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
* Felucca: Der Zwilling hat einen Kern (MENU › SYSTEM › 2ND CORE wirkt nicht), keinen Tastendruck für AFTERTOUCH (die
  Tasten sind an oder aus), kein USB-Audio und kein TRS-MIDI. Ein **Felucca-Fork**, der sich nur als „FELUCCA v…“
  meldet (oder den du für das Gerät gewählt hast, z. B. Felucca MPE), bekommt den Felucca-Zwilling – mit einem Hinweis
  oben, dass er abweichen kann; ebenso ein Felucca-Build mit anderer Paket-Kennung (ein Entwicklungs-Build: FM-1_900).
  Schreib nur zurück, wenn du weißt, dass der Fork Feluccas Daten liest – das FM-1 prüft jeden Teil, und das
  Sicherheits-Backup kommt zuerst. Die Projektansicht liest Feluccas Projekte noch nicht.
* ChoralRoot FM-1 (Zwillinge von 0.39 – der Version, die sein Installer anbietet – und 0.38; ein FM-1, das
  „ChoralRoot …“ meldet, oder ein Backup von ChoralRoots Installer öffnet ihn): keine Projektplätze – seine zehn
  Loop-Plätze wählst du am Panel des Zwillings (PLAY gehalten, PRESETS), Abspielen spielt den Loop; Musik entsteht am
  Panel (ChoralRoot hat keinen Editor), und nur SAVE gehalten schreibt Loops und Einstellungen, wie am Gerät. Die zwei
  Stems sind seine Akkord- und Bass-Parts (ein Stem trägt keinen Metronom-Klick). Nach dem Laden eines Backups und nach
  dem Zurückschreiben startet ChoralRoot neu (der Zwilling wie das FM-1). Backups von 0.21 und älter halten die
  User-Sounds in Objekten, die die neuere Firmware nicht mehr kennt: das Studio lässt sie weg und nennt sie. Der
  Zwilling hat einen Kern, kein USB-Audio, kein TRS-MIDI und kein Bluetooth; die Update-Geste tut dort nichts.
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
