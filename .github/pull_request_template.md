## Was und warum

<!-- Kurz: was ändert sich, welches Problem löst es? Verweis auf ein Issue, falls vorhanden. -->

## Tests

<!-- Welche Tests hast du laufen lassen (Befehle, Ergebnis)? Neue Tests für neue Funktionen. -->

- [ ] `QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_*.py -q`
- [ ] betroffene UI-Skripte (`QT_QPA_PLATFORM=offscreen python3 tests/ui_<bereich>_test.py`)

## Checkliste

- [ ] Schreibt nichts ungefragt aufs FM-1; neue Schreibwege haben eine Bestätigung und sind in docs/UNGETESTET.md
      vermerkt, solange sie nicht auf echter Hardware getestet sind
- [ ] Keine persönlichen Daten, Tokens, Home-Pfade oder Rechnernamen in Code, Tests oder Doku
- [ ] Oberflächentexte auf Deutsch, SPDX-Kopf in neuen Python-Dateien
- [ ] Ich bin einverstanden, dass mein Beitrag unter GPL-3.0-only veröffentlicht wird
