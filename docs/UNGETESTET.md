# Was getestet ist und was nicht – ehrliche Liste (Stand 2.3.1)

Die Suite wurde überwiegend in einer Entwicklungsumgebung **ohne FM-1** gebaut und getestet: gegen simulierte
FM-1 (Melodee, SLOOP, generisch), gegen die Testgeräte aus dem Melodee- und SLOOP-Editor, gegen selbst gebaute
Loader-Simulationen, gegen den Mock des Unbrickers und mit Firmware-Quellcode, der dafür kompiliert wurde. Diese
Tests zeigen, dass die App sich an die dokumentierten Protokolle hält – **nicht**, dass jedes Gerät sich genauso
verhält wie dokumentiert.

**Wenn du eine Funktion an echter Hardware ausprobierst, schreib bitte einen Testbericht** (Issue-Vorlage
„Hardware-Testbericht“). Nur mit Backup und auf eigenes Risiko.

## An echter Hardware benutzt (Stand 2.3.1)

Bisher an **einem** FM-1 mit Melodee, an einem Raspberry Pi 5:

| Funktion | Stand |
|---|---|
| Verbinden, Erkennen von Melodee, Editor-Protokoll | benutzt, funktioniert |
| **Firmware-Update Melodee 1.6.0-rc.8 → 1.6.0 über die Suite** (Firmware+ › Installieren) | einmal durchgeführt, hat funktioniert |
| Komplett-Backup (lesend) | in Gebrauch |
| MIDI (Monitor, Tastatur, Verbindungen) | Grundfunktionen in Gebrauch |
| Studio (Backup holen, am Bildschirm spielen) | Grundfunktionen in Gebrauch |
| Systemcheck | erkennt Muster, die aus echten Pi-5-Protokollen stammen (USB-Controller-Absturz beim Audio-Start, fluidsynth.service belegt die HDMI-Soundkarte) |

Das ist **ein** Gerät, **eine** Firmware-Familie und **ein** Update-Weg. Alles andere in dieser Liste gilt als
ungetestet, auch wenn es in der Simulation sauber läuft.

## Nicht an echter Hardware getestet

### Flashen und Rettung (höchstes Risiko)

| Funktion | Stand |
|---|---|
| Installieren einer anderen Firmware (Wechsel Melodee ↔ SLOOP ↔ Skiff …) | nur Simulation |
| V15-Zwischenschritt beim Firmware-Wechsel | nur Simulation |
| Zurück zum Original (V15, V16) | nur Simulation |
| Rückweg von V16 (über V15) | nur Simulation; ob echte V16 einen Rückschritt auf V15 annimmt, ist unbestätigt |
| Fortsetzen nach Stromausfall / abgebrochener Installation, CHECK-HEAD-Erkennung | nur Simulation |
| Unterbrochene Installation abschließen (Update-Modus, Rettung Stufe 1) | nur Simulation |
| Schrift & Werkssounds nachschreiben (Ressourcen-Werkzeug von Melodee) | nur gegen Demo und Quelltext; bei 1.6.0 meldet der Update-Loader die Ressourcen selbst als vollständig |
| Eigene `.fwsc`-Datei installieren | nur Simulation |
| Festnageln des Zielgeräts über den USB-Pfad nach dem Neustart, zwei FM-1 gleichzeitig | nur Simulation |
| **Rettung Stufe 2** (ROM-Bootmodus: V15 zurückschreiben, Einstellungen reparieren, Komplettsicherung) | nur mit dem Mock des Unbrickers; pkexec, Kernelmodul `sg`, jl-uboot-tool und die Übergabe der Dateien an den Benutzer nie am Gerät. Der Unbricker selbst wurde von seiner Community an echten Geräten benutzt, die Einbettung in die App nicht |
| **„Ist mein FM-1 sauber?“** | Schnellprüfung (Befehl 82) nur gegen Demo und Melodee-Quelltext; Dump-Prüfung nur mit aus echten Paketen gebauten Modell-Dumps, nie mit einem echten 1-MiB-Dump; Lesen im ROM-Bootmodus nur mit dem Mock |
| **Reste aufräumen** (V16-Reste in 0x93000–0xA8FFF sektorweise löschen, Expertenmodus, `flash/restzone.py`) | nur mit dem Mock des Unbrickers. Ob der WL82-Loader „Sektor löschen“ wirklich auf 4 KiB begrenzt, ist eine Annahme – die Blockprüfung nach jedem Sektor hält sonst an |
| Rettung Stufe 3 (Transporter mit XIAO RP2040) | nur Anleitung; die Pico-statt-XIAO-Variante ist ungeprüft |
| V16-Besonderheiten (Erststart-Formatierung, Bootkopf beim Original-Loader) | nur im Emulator bzw. offen; Testplan: Hilfe „V16 – Hinweis“ und docs/flash-protocol.md §17 |

### Studio

| Funktion | Stand |
|---|---|
| **Aufs FM-1 zurückschreiben** (Studio › Zurückschreiben) | nur Simulation (Sicherheits-Backup vorher, Versionsprüfung) |
| Klangtreue des Zwillings gegenüber dem Gerät | Firmware-Code identisch, Zeitverhalten und Überlast-Verhalten nicht |

### Backups, Presets, Projekte, Samples
- Zurückspielen eines Backups auf das Gerät (voll und teilweise).
- Echtes SLOOP und Skiff: Backup (Befehle 34–36), Sample-Upload, „Song stoppen“-Verhalten.
- Preset schreiben, umbenennen, löschen, auf Spur laden auf echtem Melodee; FM6_PUT und DX7-SysEx an die
  Original-Firmware.
- Pattern/Song schreiben (Komponieren, Aus MIDI-Datei) auf echtem Gerät.
- FM1-Open-Sample-Protokoll: aus dem Quellcode nachgebaut, nur gegen den eigenen Simulator getestet.

### Audio, Desktop, Installation
- Aufnahme über PipeWire/ALSA vom FM-1, taktgenaue Aufnahme mit echtem MIDI-Clock.
- FluidSynth-Klangerzeuger: Kommandozeile und Router-Regeln nur gegen eine Attrappe getestet.
- Plasma 5/6, Wayland, Breeze: überwiegend Offscreen-Rendering.
- Grafischer Installer (.run) nur im unbeaufsichtigten Modus; pkexec/kdialog/zenity-Pfade nicht.
- `.deb`: gebaut und mit `dpkg --root` in ein Testverzeichnis installiert.

### Netz
- Katalog-Anreicherung über die GitHub-REST-API und Release-Listen laufen in den Tests gegen aufgezeichnete
  Antworten; Online-Emulatoren und -Editoren nur gegen eine lokale Testseite.

## Empfohlene Reihenfolge für einen ersten eigenen Test

1. FM-1 anstecken → wird die Firmware richtig erkannt?
2. **Backup jetzt** (nur lesend) → im Backup-Browser öffnen: Presets und Projekte plausibel?
3. Editor öffnen, Presets einlesen (nur lesend).
4. Erst danach schreibende Aktionen, klein anfangen: ein Preset in einen leeren Platz (Rückgängig prüfen).
5. Firmware: zuerst ein Update **derselben** Firmware-Familie (z. B. eine neuere Melodee-Version).
6. Firmware-Wechsel, Zurück zum Original und alles im ROM-Bootmodus erst, wenn 1–5 sauber liefen – am besten
   mit einem Gerät, dessen Ausfall du verschmerzen kannst, und mit den Rettungswerkzeugen griffbereit.

## Bekannte Lücken (bewusst nicht gebaut)
- Projekt-Ansicht versteht nur Melodee-Projekte (SLOOP/Skiff nur erkannt).
- Auf Melodee gibt es keine Sample-Slots (vom Entwickler entfernt) – Kits nur bauen, nicht hochladen.
- MIDI-Export rechnet ARP, Strum, Chance und Motion nicht aus.
- Reste aufräumen löscht nur V16-Reste in 0x93000–0xA8FFF, wenn im App-Bereich eine bekannte Firmware mit
  V15-Aufbau steht; andere Fremdreste zeigt die Dump-Prüfung nur an.
