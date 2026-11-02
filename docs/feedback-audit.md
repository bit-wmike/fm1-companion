# Rückmeldung je Aktion – Prüfliste (Bereich Seiten/Klang/Musik/Samples/Daten-Seiten)

Regel: Jede Aktion sagt auf Deutsch, **was passiert ist**, auch bei Teilergebnissen, und wenn es zählt, **was nicht
passiert ist**. Kein stilles Gelingen, kein nacktes „OK“.

Mittel: `JobDialog(..., done_text=, fail_text=, actions=, cancellable=)` zeigt „<Titel> – läuft …“ → grünes
„Fertig“ + Ergebniszeile bzw. rotes „Fehlgeschlagen“ + Erklärung, Protokoll unter „Details“, Knopf
„Schließen“. `run_job(..., done_text=, fail_text=, notify_fail=)` zeigt Toasts für Hintergrund-Jobs.
Ohne `done_text` nimmt der Dialog die letzte Protokollzeile, nie ein leeres „OK“.

| Seite / Aktion | vorher | nachher |
|---|---|---|
| **Alle JobDialoge** | Überschrift „… – fertig“, Knopf „Close“/„Schließen“, sonst nichts | „läuft …“ / ✓ „Fertig“ + Ergebniszeile / ✗ „Fehlgeschlagen“ + Erklärung, Details aufklappbar, „Abbrechen“ (oder grau mit Grund) |
| Dateifehler in Jobs | „[Errno 13] Permission denied: …“ | „Keine Schreibrechte (…) – bitte Rechte prüfen …“ |
| Komponieren · In Pattern schreiben | Statuszeile | Dialog: „Spur k, Pattern b: n Steps geschrieben und nachgelesen. Alter Inhalt gesichert. Dauerhaft erst mit SAVE.“ |
| Komponieren · Rückgängig | Statuszeile | Dialog: „… hat wieder den alten Inhalt.“; auf fremdem Gerät: Ablehnung mit Gerätenamen |
| Komponieren · Als MIDI speichern | Statuszeile, Absturz bei Dateifehler | Statuszeile „nur als Datei, am FM-1 nichts geändert“ + Toast; Dateifehler deutsch |
| Werkstatt · Auf das FM-1 spielen | Log „Fertig“; ohne Datei: nichts | „n Patterns geschrieben (…); Song-Kette ersetzt/unverändert. Alter Stand gesichert.“; Abbruch: „x Patterns vorher gelesen – Rückgängig holt sie zurück“ bzw. „nichts geändert“; ohne Datei: Erklärung |
| Werkstatt · Rückgängig | nur Log | „n Patterns (und Song-Kette) haben wieder den alten Stand.“ |
| Presets · Lesen/Sichern/Schreiben/Löschen/Umbenennen/Verschieben/Spur speichern | Statuszeile | Statuszeile mit Zahlen; bei Teilfehler: „Bereits geänderte Plätze lassen sich mit Rückgängig zurücknehmen.“ |
| Presets · Tags / Entfernen | still | Statuszeile „… (nur Bibliothek, am FM-1 nichts geändert)“ |
| Presets · Exportieren | Statuszeile | Statuszeile + Toast, „am FM-1 nichts geändert“ |
| FM-Voices · Sammlung laden | „fertig (…)“ | „heruntergeladen … – ans FM-1 wurde nichts gesendet“ + Toast |
| FM-Voices · Zur eigenen Bank | still | Statuszeile mit Anzahl |
| FM-Voices · FM6-Preset / Rückgängig / SysEx | Statuszeile | unverändert (schon klar), Rückgängig je Gerät |
| Samples · Kit in Slot laden | Dialog „fertig“ | „Kit „X“ (n Sounds) liegt jetzt in USRk. Alter Inhalt gesichert – Rückgängig“; Fehler: ob gesichert oder „nichts geändert“ |
| Samples · Slot leeren / Rückgängig | Dialog „fertig“ / – | „USRk ist leer. Alter Inhalt gesichert“ / „USRk hat wieder den alten Inhalt“ |
| Samples · Als WAVs exportieren | Erfolg als **Warn**-Dialog (show_error) | Toast „n WAV-Dateien exportiert nach …“ |
| Samples · Kit speichern | still | Toast „Kit „X“ gespeichert (n Sounds) – nur auf dem Rechner“ |
| Samples · Passend kürzen | still | Toast „nur im Kit-Baukasten, Dateien unverändert“ |
| Samples · Dateien hierher ziehen | still (nur Neu-Einlesen) | Toast mit Anzahl |
| Samples · FM1 Open lesen / schreiben | Dialog „fertig“ | „gelesen und gesichert: Datei – am FM-1 nichts geändert“ / „geschrieben und Byte für Byte geprüft; vorheriger Stand als .fm1s“ |
| Samples · MP3 ohne ffmpeg | Länge „?“ | Hinweis „für MP3/FLAC: sudo apt install ffmpeg“ (Liste und Tooltip) |
| Sample-Pakete · Laden | Statuszeile | Statuszeile + Toast (Erfolg/Fehler) |
| Aufnahme · Exportieren | Geräte-Zeile | + Toast „n von m exportiert (neben dem Original), k übersprungen“ |
| Aufnahme · Als Sample / Löschen | Zeile / still | + Toast, Dateifehler deutsch |
| Projekte · Holen | Dialog „fertig“ | „n von m Projekt(en) in die Bibliothek geholt – am FM-1 nichts geändert“ |
| Projekte · Platz leeren / Einspielen | Dialog „fertig“ | „Platz k ist jetzt leer“ / „„X“ liegt jetzt in Platz k – unter SAVE > PROJECT laden“; Fehler: „das Gerät hat nichts geschrieben“ |
| Projekt-Ansicht · Vom FM-1 lesen | Dialog „fertig“ | „… gelesen und geöffnet – nur angesehen, am FM-1 nichts geändert“ |
| Projekt-Ansicht · Exporte | Toast | Toast; Dateifehler deutsch statt Absturz |
| Backup-Browser · Sicherheits-Backup / Zurückspielen | Dialog „fertig“ | „Jetziger Stand gesichert: Datei. Noch nichts zurückgespielt“ / „n Teile zurückgespielt; vorheriger Stand im Sicherheits-Backup …“ |
| Backup-Browser · Live-Stand lesen | Dialog „fertig“ | schließt selbst; „nur im Speicher, am FM-1 nichts geändert“ |
| Firmware+ · Katalog aktualisieren | still | Toast mit Anzahl Einträge; Fehler: Hinweis „gespeicherter Stand“ |
| Firmware+ · Link speichern | still | Toast „gespeichert – Installiert wurde nichts“; Dateifehler deutsch |
| Klangerzeuger · Start / Stopp | Toast / still | Toast / Toast |
| MIDI · Protokoll exportieren | Toast; Absturz bei Dateifehler | Toast; Dateifehler deutsch |
| Geräte · Vergleichen | A = B möglich | Knopf grau mit Grund; A ≠ B voreingestellt |

Nicht in diesem Bereich (separat geprüft): Firmware-Seite (Download/Installation), Backups-Seite, Übersicht,
Rettung, Hauptfenster – sie nutzen dieselbe JobDialog-API.

## Rückmeldungen – Installer, Firmware, Backups, Rettung, Gerät (ws/fixa)

Jede Aktion meldet am Ende, was passiert ist und was nicht (Fertig/Fehlgeschlagen über `JobDialog(done_text=…)`,
Wrapper `ui/feedback.run`, kleine Aktionen über die Statuszeile `ctx.message`).

| Aktion | Wo | Rückmeldung bei Erfolg | bei Fehler / Abbruch |
|---|---|---|---|
| Firmware installieren | Firmware, Katalog, eigene Datei | „Installation abgeschlossen – das FM-1 meldet jetzt <Kennung> (<Firmware Version>), über die Original-V15, Ressourcen geschrieben / NICHT geschrieben, Backup vorher gespeichert / ohne Backup.“ | deutscher Grund; vor V15: „am FM-1 wurde nichts geändert“; nach V15: „Auf das FM-1 wurde die Original-Firmware V15 geschrieben … <Ziel> wurde NICHT geschrieben“ |
| Probelauf | Plan „Nur Probelauf“ | „Probelauf erfolgreich – alle Pakete geprüft, das FM-1 hat geantwortet. Am FM-1 wurde nichts geändert.“ | Grund (z. B. Paket beschädigt), nichts gesendet |
| Nur herunterladen | Firmware | „<Firmware> <Version> heruntergeladen (<Datei>, Prüfsumme geprüft / nicht verfügbar) – noch NICHT installiert. Gespeichert in …“ + [Jetzt installieren …] [Ordner öffnen] | HTTP 403/404/offline auf Deutsch, Details unter „Details anzeigen“ |
| Schrift & Werkssounds nachschreiben | Firmware | „Ressourcen von Melodee <tag> geschrieben … Die Firmware selbst wurde nicht verändert.“ | deutscher Grund |
| Firmware hinzufügen / entfernen | Firmware | Statuszeile: „… hinzugefügt – noch nichts installiert“ / „… entfernt. Pakete bleiben, am FM-1 ändert sich nichts.“ | – |
| Release-Liste | Firmware | „lade …“, dann Liste | „Keine Versionen gefunden. <Grund: offline / Abruf-Limit / keine Releases>“ |
| Backup jetzt | Backups, Übersicht, Strg+B | „Backup gespeichert: <Datei> (57 Teile, 12 Projekte, 412 KB). Am FM-1 wurde nichts verändert.“ + [Ordner öffnen] | Firmware ohne Backup: „<Firmware> hat kein Editor-Protokoll – davon kann die App kein Backup machen …“; schreibgeschützt: „Keine Schreibrechte …“ |
| Alles zurückspielen | Backups | „Rücksicherung abgeschlossen: N Teile vom Stand <Datum> geschrieben … Der vorherige Stand liegt im Sicherheits-Backup <Datei>.“ (oder: „Es gibt KEIN Sicherheits-Backup“) | andere Firmware-Familie: „Zurückspielen nicht möglich“ + was geht |
| Spiegeln | Backups | „Spiegel aktualisiert: N Backups kopiert und geprüft, keine Fehler. Ziel: …“ | Anzahl Fehler |
| Notiz / Etiketten / Anheften / Hinzufügen / Löschen | Backups | Statuszeile: „<Datei>: Notiz gespeichert“, „angeheftet – wird nie automatisch gelöscht“, „Backup hinzugefügt (Kopie)“, „Gelöscht: …“ | „Nicht gespeichert … (schreibgeschützter Ordner?)“ |
| Projekte entnehmen | Backups | Info: „N Projekte liegen jetzt in der Projekt-Bibliothek … Am FM-1 wurde nichts verändert.“ | – |
| Beschädigte Backups | Backups | in der Liste rot „beschädigt“, Zählung im Kopf | Öffnen/Zurückspielen: „lässt sich nicht lesen … Löschen geht“ |
| Umbenennen | Übersicht | „Umbenannt in „X“ – Backups liegen jetzt in „X“.“ | „Umbenennen nicht möglich“ + Grund |
| Firmware wählen (mehrdeutige Kennung) | Gerät verbinden | „Gemerkt: Auf <Gerät> läuft <Firmware> (Kennung …)“ / „Firmware unbekannt – Installationen gelten als Wechsel“ | – |
| Neu verbinden | Hauptfenster | „Verbinde neu mit …“ / „N FM-1 gefunden“ / „kein FM-1 gefunden – USB-Datenkabel prüfen“ | – |
| FM-1-Seite „Neu einlesen“ | FM-1 | „eingelesen um HH:MM:SS (nur gelesen, nichts verändert)“; generisch: was bekannt ist + warum keine Live-Werte | „Fehler: …“ |
| Rettung: Installation abschließen | Rettung | „Installation abgeschlossen – das FM-1 meldet jetzt <Kennung> und startet normal.“ | Engine-Meldung (deutsch) |
| Rettung: Werkzeug holen / V15 im ROM-Modus / Einstellungen reparieren / Komplettsicherung | Rettung | jeweils, was geschrieben bzw. NICHT geschrieben wurde (Simulation: „nichts wurde geschrieben“) + [Ordner öffnen] | Code-Erklärung |
| Einstellungen kaputt | Start | einmaliger Hinweis: Datei beiseitegelegt / ungültige Felder ersetzt | – |
