# Katalog („Firmware+", früher „Entdecken") – Recherche-Notizen

Stand: 30.10.2026, Workstream *katalog*.

## Quellen

| Quelle | Erreichbar | Inhalt | Nutzung |
|---|---|---|---|
| Mooring `mooring/catalog.json` (awesome-fm-1, cicloid) | raw.githubusercontent.com ✓ | Schema 3, Revision 6: 145 Pakete (`allowlist.entries`), 121 mit Download (96 Familien), 34 Loader | Pakete, SHA-256, Produkt-Kennung, Hinweise, `requiresStockV15`, Loader → Abstammung |
| awesome-fm-1 `README.md` (CC0) | raw ✓ | 223 Open-Source-, 4 Closed-Source-, 20 „Not a synth“-Firmwares, 151 Werkzeuge/Links | Namen, Autoren, Beschreibungen, Links nach Rolle, Lizenz, Abstammung, Status |
| GitHub-REST-API | **im Container gesperrt** (Proxy: nur sitzungsgebundene Repos, 403); auf einem normalen Rechner verfügbar (60/h ohne Token) | Sterne, Releases, Topics | träge Anreicherung mit Cache + ETag, optionaler Token |
| raw.githubusercontent.com (README, Dateien) | ✓, kein API-Limit | README der Firmware, Datei-Existenz | Vorschaubild (erstes Nicht-Badge-Bild), README-Anzeige, Felucca-Werkzeuge prüfen (`web/fm1backup.js`, `tools/fm1_install.py`) |
| thefirmwarehub.com, fm1-editor.com/firmware | **nicht erreichbar** aus dem Container | Kategorien, Status | nur als Link-Ziele (`#anker`) und im Link-Import (Anker → unscharfer Namensvergleich). Kein Scraper. |
| FM-1 Firmware Atlas (wero1414, CC0) | ✓ | 67 Firmwares + echte Sterne vom 14.10.2026 | angesehen, (noch) nicht eingebunden |

## Signatur des Mooring-Katalogs

`catalog.signed.json` (Hülle `mooring-catalog-envelope` v1, Ed25519-Signatur, 64 Byte) und
`catalog.json.intoto.jsonl` (DSSE/in-toto, keyid `dbe8230a406629ab`). Der **öffentliche Schlüssel ist nicht
veröffentlicht**: alle 26 Commits des Repos (inkl. der 4 „Publish Mooring“-Commits) enthalten nur die drei
Katalog-Dateien; kein `cicloid/mooring`-Repo, nichts per Websuche. Ohne Schlüssel keine echte Prüfung –
deshalb zeigt die App **kein** „signiert ✓“. `catalog.mooring_signature_status()` prüft nur, dass die
SHA-256 in der in-toto-Aussage zur Katalog-Datei passt (Konsistenz, keine Echtheit). Technisch wäre die
Prüfung auf Bookworm machbar (python3-cryptography 38 kann Ed25519; im 3.11-Test-venv fehlt es, also
optionaler Import). Die eigentliche Absicherung bleibt die Paket-Prüfsumme beim Installieren (Installer).

## Heuristiken (README)

* Eintrag: `- [Name](url) - Beschreibung …`; Inline-Links werden nach Linktext einer Rolle zugeordnet
  (install/flasher → installer; emulator/simulator/demo/play/try/preview → emulator; editor/studio/workbench →
  editor; manual/guide/handbook → manual; source; sonst site). Kombi-Texte („Installer and emulator“) → beide.
* Autor: „<Wort> by X“ bis zum ersten Satzzeichen oder Bindewort, ohne „inspired/steered/… by“; „X's author“
  wird über den Eintrag X aufgelöst; Rückfall: Repo-Besitzer.
* Lizenz: SPDX-artige Kennung im Text, „No licence stated“, Closed source → proprietär.
* Status-Flags: beta, alpha, experimentell, wip (early, prototype, in progress …), kostenpflichtig, quellcode.
* Kategorie (wie die Hub-Verzeichnisse): Stichwort-Punkte, erster Satz zählt dreifach → Synth, Groovebox,
  Sampler, Akkorde, Generativ, Werkzeug, Spiele & Spaß, Original (Abschnitt Official).
* Abstammung: zuerst **Loader-SHA aus Mooring** (feluccaG1/G2 → Felucca, sloopG3 → SLOOP, stock → Original),
  dann Text („Built on/Based on/X fork/X-based …“) mit Auflösung über andere Einträge (z. B. GHOULBOX-Fork →
  Felucca), „FoMni“ als Unterzweig von Felucca. 212 von 252 Einträgen haben eine Abstammung.
* Zuordnung Mooring ↔ README: GitHub-Repo (ohne Unterordner-Links), dann normierter Familien-/Paketname.
  Familien ohne README-Eintrag werden eigene Einträge (3), README-Einträge ohne Paket bleiben Link-Einträge.
  Pakete ohne Download (nur Kennung) werden über den Namen zugeordnet; 2 (Vector37, SMK-37) bleiben übrig.
* Felucca-Protokoll („Backup-fähig“) nur mit Beleg: Familien melodee/skiff/sloop/felucca, oder (Link-Import
  online) `web/fm1backup.js` bzw. `tools/fm1_install.py` im Repo. Sonst „wird beim Verbinden geprüft“.

## Fixtures

`tests/fixtures/catalog/github_*.json` sind **schema-treu nachgebaut** (GitHub REST v3), weil die API im
Container gesperrt ist; echte Werte (Tag, Dateiname, Größe, URL) stammen aus dem Mooring-Katalog, Sterne
und Daten sind Beispiele und tragen ein `_provenance`-Feld. Auf echter Hardware/Netz ungetestet: Antworten
der echten API (Feldnamen entsprechen der öffentlichen Doku).

## Schnappschuss

`python3 tools/build_catalog_snapshot.py --mooring … --readme … --screenshots` → `fm1companion/data/catalog_snapshot.json`
(252 Firmwares, 96 direkt installierbar, 151 Werkzeuge/Links, 141 Vorschaubild-Adressen, ~300 KB).
