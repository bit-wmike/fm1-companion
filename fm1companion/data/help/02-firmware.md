# Firmware wechseln & zurück zum Original

Das FM-1 kann verschiedene Firmwares tragen, z. B. **Melodee**, **Skiff**, **SLOOP** oder die offizielle
Firmware von M-VAVE. Alles dazu liegt auf **einer** Seite: [Firmware+](app://page/discover) (Seitenleiste ›
*Daten & Werkzeuge*).

* **Alle Firmwares** – der Katalog: suchen, ansehen, im Simulator ausprobieren, *Zu meinen Firmwares*.
* **Installieren & Versionen** – [hier](app://page/firmware) wird installiert, aktualisiert und gewechselt: Versionen
  einer Firmware wählen, mehrere Versionen *Nur herunterladen* (auch für eine Firmware, die noch nicht drauf ist) und
  dann zwischen ihnen springen, *Eigene .fwsc-Datei installieren …*, *Firmware hinzufügen …* (Link oder Repo),
  *Zurück zum Original …* und der Rückweg von V16 über V15.
* **Neuigkeiten** und **Werkzeuge & Wissen** – neue Versionen der Firmwares, denen du folgst, und nützliche Links.

Ob es für dein FM-1 ein Update gibt, steht auch auf der [Übersicht](app://page/overview) (*Firmware & Updates*).

## So wechselst du

1. **Backup anlegen** (Strg+B). Die App macht vor jedem Update zusätzlich selbst eines (abschaltbar in den
   Einstellungen) und merkt sich, zu welcher Firmware es gehört.
2. In [Firmware+ › Installieren & Versionen](app://page/firmware) Firmware und Version wählen, *Auf aktives FM-1
   installieren*.
3. **Strom und USB nicht trennen**, bis die App „fertig“ meldet. Andere Programme, die MIDI senden, schließen.
4. Kommst du später zu einer Firmware zurück, bietet dir die App das passende Backup an.

## Zurück zur Original-Firmware

* Melodee bringt einen Weg **zurück zur offiziellen Firmware** (V15) mit; vorher wird gesichert.
* Die offiziellen Dateien und Updater gibt es bei M-VAVE: <https://www.m-vave.com/download>.
* Den aktuellen Stand der offiziellen Firmware und alternative Updater (auch für Linux) sammelt
  [awesome-fm-1](https://github.com/cicloid/awesome-fm-1).

## Gut zu wissen

* Projekte und Presets einer Firmware passen nicht automatisch zu einer anderen. Deshalb: Backups pro Firmware.
* Zu Melodee 1.6 gehört eine Ressourcen-Datei (Schrift, Werkssounds). Ab 1.6.0 schreibt der Update-Loader sie
  selbst mit; fehlt sie trotzdem (z. B. beim ersten Update von 1.5.1), schreibt die App sie nach.
* Update auf **Melodee 1.6.0**: Backup ist dein Rückweg, Projekte können anders klingen – siehe
  [Melodee 1.6.0](help:14-melodee-1-6-0).
* Wenn etwas schiefgeht: [Update-Modus & Rettung](help:03-update-modus).
