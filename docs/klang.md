# klang – Presets und FM-Voices (Notizen)

## Melodee-Preset-Bibliothek (Befehl 80)
* Quelle: `web/EDITOR_PROTOCOL.md` "Shared user library", Editor `sharedBank` (web/editor.html), Firmware
  `firmware/src/editor_library.c`. `presetlib.SharedBank` baut die Anfragen byte-genau wie der Editor
  (`tests/test_presets.py::test_put_args_byte_exact`) und läuft gegen den Editor-Mock (`tests/mock_fm1_shared.mjs`,
  `makeMockDevice({shared:true})`).
* **v14 ist Offset-8192** (Firmware `ed_rv`, Editor `v14enc`). `protocol.v14/r14` nutzen Zweierkomplement –
  für echte Geräte falsch (betrifft DESC/GET/SET); presetlib hat eigene Kodierung.
* PUT-Regeln der Firmware: Name 1–12 (nativ 1–20) druckbar, Werte −64…127, FM6/PROPHET nur mit nativen
  Daten (kind 1/3), Bit 6 = Felucca-Standardregler (für DX7-Importe), Bit 5 = nativen Namen behalten.
* Schreiben/Umbenennen/Löschen/Speichern rufen `ed_flash_stop()` → **stoppen die Wiedergabe**; Favoriten
  (FAV_SET 38, Kategorie 16 bzw. NENGINES) antworten rc 4 = „nach STOP gespeichert“. Beides zeigt die Seite an.
* Ältere Melodee (UP_* 16–21, 64 Plätze) wird erkannt (UP_LIST antwortet) und klar abgelehnt (Hinweis auf 1.5+).

## DX7 senden (ohne Felucca)
Nach „FM1 Editor & Librarian“ (benny-sparra/fm1-dx7-patch-importer, MIT, docs/fm1-research.md):
M-VAVE-Firmware (Kennung FM-1_0xx ≤ 019) nimmt den Einzel-Voice-Dump (Edit-Puffer, SAVE am Gerät);
FM-1+VA und Unbekanntes bekommen 155 Parameteränderungen `F0 43 1n gg pp dd F7` (überschreiben nichts).
Melodee und FM1 Open nehmen den Einzel-Dump (Melodee: FM6-Spur; FM1 Open: Spuren mit passendem Eingangskanal).

## Renderer
`fmrender.py` portiert msfa/Dexed-Mathematik (Apache-2.0) aus FM1 Open `web/synth/dx7*.c`. Gemessen im Container:
≈0,03–0,06 s für 2 s Ton (Feedback-Kette in reinem Python). LFO ist vereinfacht.

## Sammlungen
Auf Commits festgelegt, Download über raw.githubusercontent.com (keine API): KingParamount/fm1-factory-presets (CC0),
mene311/fm1-banks (MIT), Yamaha-ROMs und Grey Matter E! aus benny-sparra/fm1-dx7-patch-importer (Lizenz unklar).
