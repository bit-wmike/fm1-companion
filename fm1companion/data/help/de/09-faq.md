# Häufige Fragen

**Die App findet mein FM-1 nicht.**
USB-Datenkabel verwenden, FM-1 einschalten, F5 drücken. Hilft das nicht: [Systemcheck](app://action/syscheck) –
er zeigt, ob der Rechner das Gerät überhaupt sieht.

**Das FM-1 trennt sich immer wieder.**
Fast immer Kabel, Hub oder Netzteil. Der Systemcheck erkennt solche Schleifen im Kernel-Protokoll und sagt, was
zu tun ist.

**Darf ich während eines Updates etwas anderes machen?**
Ja, aber nichts, was mit dem FM-1 spricht (Browser-Editor, DAW), und USB nicht abziehen.

**Sammelt die App Daten?**
Nein. Ohne Telemetrie, ohne Konten. Automatisch fragt die App nur zwei Dinge ab (abschaltbar): beim Anstecken
eines FM-1 die Versionsliste seiner Firmware auf GitHub und beim Öffnen des Katalogs den Katalog von awesome-fm-1
samt Vorschaubildern. Alles andere nur auf deinen Klick. Fehlerberichte werden nur lokal gespeichert.
Abschalten: Einstellungen › „beim Anstecken eines FM-1 nach Firmware-Updates suchen“ und im Katalog unter „Mehr“
(automatisch aktualisieren, Vorschaubilder).

**Wo sind meine Daten?**
Alles, was dir gehört, liegt im Hauptordner (Standard `~/FM-1`). Der wird beim Deinstallieren **nie** gelöscht.

**Wie lösche ich alte Protokolle und heruntergeladene Firmware?**
Werkzeuge › *Speicher und Aufräumen …* (oder Einstellungen › Speicher) zeigt, was die App auf dem Computer
aufbewahrt – Protokolle, heruntergeladene Firmware-Pakete, Kopien installierter Pakete, Firmware-Werkzeuge,
Editor-Kopien, Katalogbilder, Sample-Pack-Downloads, Web-Cache, temporäre Dateien und Fehlerberichte – mit Größe,
*Ordner öffnen* und *Löschen …* (immer mit Rückfrage). Deine Backups, Projekte, Samples, Kits und Aufnahmen werden dort
nie gelöscht, ebenso wenig alles, was eine unterbrochene Installation noch braucht (das Paket aus dem Journal, die
Firmware-Werkzeuge, die neueste Original-Firmware V15 für eine Rettung ohne Internet). Während einer Installation wird
nichts gelöscht. Studio-Stände sind deine Arbeit: sie werden nur einzeln gelöscht (*Einen Stand löschen …*).

Das **automatische Aufräumen** (gleicher Tab, standardmäßig an, höchstens einmal am Tag) löscht ältere
Protokollteile nach 30 Tagen, behält die
3 neuesten Paketkopien und je Firmware die 2 neuesten Editor-Versionen und löscht Katalogbilder, Sample-Pack-Downloads
und temporäre Dateien nach 30 Tagen. Der [Systemcheck](app://action/syscheck) zeigt die Summe.

Firmware-Downloads in `~/FM-1/Firmware` werden nie automatisch gelöscht (der Ordner gehört dir); im selben Tab kannst
du „je Firmware die neuesten N Downloads behalten“ einschalten.

**Etwas ist abgestürzt.**
Die App zeigt dann einen Dialog mit *Fehlerbericht speichern*. Der Bericht enthält Protokolle, Versionen und
Einstellungen ohne persönliche Pfade – du entscheidest, ob du ihn weitergibst. Selbst erstellen: Hilfe ›
Fehlerbericht erstellen.

**Wie bekomme ich das alte Aussehen zurück?**
Ansicht › Design › *Systemstil (KDE/Breeze)*. Symbole: Einstellungen › Aussehen › *System-Thema*.
