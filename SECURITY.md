# Sicherheit

## Eine Lücke melden

Bitte melde Sicherheitslücken **nicht** als öffentliches Issue, sondern privat über GitHub:
Reiter **Security** › **Report a vulnerability** im Repository
(<https://github.com/bit-wmike/fm1-companion/security/advisories/new>).

Hilfreich sind: betroffene Version (`fm1-companion --version`), Schritte zum Nachstellen, was passiert
und was du erwartet hättest. Bitte keine Tokens, Passwörter oder persönlichen Daten mitschicken – auch
nicht in angehängten Fehlerberichten (die App entfernt Tokens, prüfe den Inhalt trotzdem vorher).

Das Projekt wird in der Freizeit gepflegt; wir melden uns so bald wie möglich und veröffentlichen eine
Korrektur zusammen mit einem Hinweis, sobald sie fertig ist.

## Was dazu gehört

* Code dieses Repositorys: die App, die Installer (`install.sh`, `.run`, `.deb`), die Werkzeuge in `tools/`.
* Alles, was ungewollt Daten preisgibt (Fehlerbericht, Diagnose, Protokolle), Code von außen ausführt
  oder das FM-1 ohne Zustimmung verändert.
* **Geräte-Sicherheit ist ausdrücklich willkommen:** Wege, auf denen eine Webseite, ein Simulator, ein
  Paket oder ein Bedienfehler ein FM-1 unbrauchbar machen könnte, auch wenn es „nur“ ein Bug ist.

Nicht dazu gehören Lücken in den Firmwares selbst (Melodee, Skiff, SLOOP, Original-Firmware) oder in
fremden Werkzeugen – bitte direkt an deren Projekte melden. Vendorte Werkzeuge (z. B.
`fm1companion/vendor/unbricker`) gerne hier melden, wir geben es weiter.

## Bekannte Grenzen

* **Root-Helfer der Rettung Stufe 2** (`fm1companion/flash/uboot.py`): Vor dem Start mit Root-Rechten
  werden die Werkzeuge in ein root-eigenes Temp-Verzeichnis kopiert und gegen eingebaute SHA-256-Prüfsummen
  geprüft. Diese Prüfsummen sichern die **Unversehrtheit** (keine beschädigten oder versehentlich
  geänderten Dateien), sie sind **keine Sicherheitsgrenze** gegen Schadcode, der bereits mit deinen
  Benutzerrechten läuft – wer das kann, kann auch die App selbst verändern. Das Werkzeug schreibt seine
  Sicherungen und Protokolle als root in den Rettungsordner unter `~/FM-1/Rettung`; danach übergibt der
  Helfer die Dateien an deinen Benutzer, ohne symbolischen Links zu folgen.
* Online-Editoren und -Simulatoren sind fremde Webseiten. Sie dürfen Editor-Befehle erst nach deiner
  Zustimmung je Adresse ans FM-1 senden; Firmware-Update und Bootmodus bleiben immer gesperrt.
* Das Ressourcen-Werkzeug einer Firmware ist Python-Code aus deren Repository. Bei eingebauten Firmwares
  ist der Commit je Version festgehalten; bei selbst hinzugefügten Firmwares und verschobenen Tags fragt
  die App vor dem Ausführen.
