# FM1 Open V14 – Sample-Slots: Protokoll und Format

Quelle: `/tmp/fm1_open` (M-VAVE FM1 Open V14, Commit 4c7496d): `app/transfer.c`, `app/samples.c`,
`app/instrument.h`, `app/synth/sampledrum.h`, `sdk/WebUpdate.js` (Klasse `Library`), `sdk/Samples.js`,
`web/synth/sea_encode.h`, `sea_decode.c`, `sample_metadata.inc`. Umsetzung: `fm1companion/fm1open.py`,
Geräteseite zum Testen: `tests/fm1open_sim.py`.

## Rahmen (Datei-Protokoll, USB-MIDI)

```
F0 7D 46('F') 4D('M') 02 cmd tok_lo tok_hi <payload> sum F7
```
* `cmd` < 64. Token 14 Bit (7+7), der Host zählt pro Anfrage hoch (Start zufällig – das Gerät merkt sich
  pro Anschluss die letzte Anfrage und schickt bei **byte-gleicher** Wiederholung die gespeicherte Antwort).
* `payload`: bis 132 Byte (8 Bit), als **LSB-zuerst-Bitstrom in 7-Bit-Gruppen** (`value |= b << bits` …);
  Reste müssen 0 und < 7 Bit sein. Nachricht max. 192 Byte.
* `sum`: Summe aller Bytes von `7D` bis einschließlich `sum` ≡ 0 (mod 128).
* Antwort: gleicher Kopf, `cmd | 64`, gleiches Token; Nutzdaten beginnen mit **Status**:
  0 ok, 1 leer, `64 − r` für Fehler r < 0 (71 = ungültig −7, 72 = Speicher −8, 73 = Tuning-Platz −9,
  65–70 = Flash-Fehler −1…−6, 67 = beschäftigt). Antworten ≤ 96 Byte.
* Host (Workbench): 3 Versuche à 1 s mit demselben Paket.

## Befehle

| cmd | Daten | Antwort | Bedeutung |
|---|---|---|---|
| 0 | – | 14 Byte: Version (3/4), 16 Plätze, aktiver Platz, Projektgröße LE16 (4048/4064), Clock, 8 Quellen | Projekt-Info – dient zum **Erkennen** von FM1 Open |
| 21 | – | 7 Byte | Tuning-Fähigkeit, Größen (V13+) |
| 22 | – | 9 Byte: 1, 128, 4, Bank-Lib, **Sample-Kapazität LE32 (446464)**, Flags | V14-Fähigkeiten; fehlt → Kapazität 462848 |
| 11 | – | Status, Größe LE32, Region LE32 (=466944) | Sample-Status: 0 keine Bank, 1 prüft, 2 bereit, 3 empfängt, 4 Fehler |
| 12 | Größe LE32 | – | Beginn: **löscht den ersten Sektor sofort** (alte Bank ist weg), Zustand 3 |
| 13 | Offset LE32 + 1…128 Byte | – | Schreiben, Offsets lückenlos aufsteigend |
| 14 | – | – | Abschluss: prüft Kopf, CRC, SEA5-Blockköpfe, setzt Commit-Wort |
| 15 | Offset LE32 + n (≤ 64) | n Byte | Lesen (nur Zustand 2) |
| 16 | – | – | Abbruch (Zustand 3 → 4) |

Gerät bricht eine Sitzung nach 10 s ohne Daten ab (Zustand 4).

## Bank (eine für alle 8 Pads, bis 446 464 Byte ≈ 19,55 s)

Kopf 512 Byte, Little Endian: `magic "FM1S" (0x53314D46)`, `version 1`, `bytes` (Gesamtgröße), `crc`,
`commit` (0), `rate 32000`, 2× reserviert 0, dann 16 Einträge × 28 Byte `{offset, frames, bytes, name[16]}`,
32 Byte 0. Einträge lückenlos ab 512; `bytes = ceil(frames/320)·228`; Name druckbares ASCII, max. 15 Zeichen;
Einträge 9–16 leer (Offset = Ende). CRC = CRC-32 (zlib) über die ganze Bank mit Byte 12–19 als Null.

Pads → MIDI-Noten: 36, 38, 37, 41, 47, 48, 42, 46 (Kick, Snare, Rim, Toms, Hi-Hats nach GM).

## SEA5

Mono, 32 kHz, Block = 320 Frames = 228 Byte: `01 45 14 5A`, 8 × int16 Startzustand (4 Verlauf, 4 LMS-Gewichte),
16 Skalen-Nibbles (je 20 Frames), 200 Byte 5-Bit-Codes (MSB zuerst). Encoder: je Gruppe alle 16 Skalen
probieren (Start bei der vorigen), Fehler² + Gewichts-Strafe, beste gewinnt; Zustand läuft über Blockgrenzen.
Der Python-Port ist **byte-genau** zur C-Referenz (Test kompiliert die Originalquellen mit clang/gcc).
Die Workbench wandelt WAV mit dem Browser (`decodeAudioData` @ 32 kHz) – dieses Resampling ist nicht
nachgebildet; die App nutzt einen eigenen Tiefpass + Interpolation (numpy). Ab int16 sind die Bytes gleich.

## Sicherheit in der App

1. „Lesen & sichern“: ganze Bank lesen, als `.fm1s` (gleiches Format wie „备份网页采样库“ der Workbench)
   unter Backups/<Gerät> ablegen.
2. Schreiben nur nach Rückfrage, die nennt, welche Slots ersetzt werden; vorher wird die Bank erneut gelesen,
   mit dem Stand beim Lesen verglichen (sonst Abbruch) und nochmals gesichert.
3. Bank wird vor dem Löschen vollständig geprüft; nach dem Schreiben wird alles zurückgelesen und Byte für Byte
   verglichen. Bei Abbruch/Fehler: Backup zurückspielen („Backup zurückspielen …“).

Nicht am echten Gerät getestet – nur gegen `tests/fm1open_sim.py` (nach transfer.c/samples.c).
