# Backups

Ein Backup enthält alles, was die Firmware über ihre Schnittstelle herausgibt: Projekte, User-Presets,
Einstellungen. Es ist dasselbe Format wie beim Web-Editor der Firmware.

* **Anlegen:** Strg+B oder [Backups](app://page/backups) › *Backup jetzt*. Optional mit Notiz.
* **Zurückspielen:** Backup wählen › *Zurückspielen …*. Vorher legt die App automatisch ein
  Sicherheits-Backup des aktuellen Stands an – du kannst also immer zurück.
* **Einzelne Projekte** holst du auf der Seite [Projekte](app://page/projects) aus dem Gerät oder aus einem Backup.
* **Wo liegen sie?** Im Hauptordner (Standard `~/FM-1/Backups/<Gerätename>/`). Sichere diesen Ordner ab und zu
  zusätzlich auf einen USB-Stick oder ins Netz.
* Automatische Backups (vor Updates) werden nach der eingestellten Anzahl aufgeräumt; manuelle bleiben immer.

## Welche Firmwares lassen sich sichern?

Backups laufen über das Editor-Protokoll der Firmware. Die App spricht drei Backup-Protokolle: das von
**Felucca** (Felucca, Melodee, Skiff, JIANT FM, CoralFlower, ChoralRoot und die Felucca-Forks, die es behalten),
das von **SLOOP** (SLOOP, KARMA, ORBIT und SLOOPs Forks: sloopDX, Clipper, Jangada, winampFM, …) und das von
**X0X** (ab X0X 1.0.9); M-VAVEs FM1 Open hat ein eigenes.

* Eine Firmware, die sich auf Nachfrage mit Namen meldet („FELUCCA CLIPPER 1.3.1“), bekommt ihre eigenen Regeln.
  Eine, die wie Felucca antwortet („FELUCCA v1.0“), wird **gefragt**, welches Backup-Protokoll sie spricht – nur
  mit lesenden Anfragen; mit einem geratenen Protokoll schreibt die App nie.
* Eine Firmware mit eigenem Backup-Protokoll (Optimist, Hortator) oder ganz ohne bekommt statt einer
  Zeitüberschreitung eine klare Meldung: Sichere sie mit ihrem eigenen Editor.
* Ein Backup geht nur auf eine Firmware zurück, die unter denselben Objekten dasselbe ablegt. Ein sloopDX-Backup
  auf SLOOP, ein Saadet-Backup auf Felucca landeten an falschen Stellen – die App lehnt ab, bevor etwas
  geschrieben wird.
* Die vollständige Liste der Katalog-Firmwares und was die App mit jeder tut, steht in `docs/firmware-support.md`.
