# Catalog ("Firmware+", formerly "Discover") – research notes

As of: 30 Oct 2026, workstream *catalog*.

## Sources

| Source | Reachable | Content | Use |
|---|---|---|---|
| Mooring `mooring/catalog.json` (awesome-fm-1, cicloid) | raw.githubusercontent.com ✓ | schema 3, revision 6: 145 packages (`allowlist.entries`), 121 with download (96 families), 34 loaders | packages, SHA-256, product identity, notes, `requiresStockV15`, loader → lineage |
| awesome-fm-1 `README.md` (CC0) | raw ✓ | 223 open-source, 4 closed-source, 20 "Not a synth" firmwares, 151 tools/links | names, authors, descriptions, links by role, licence, lineage, status |
| GitHub REST API | **blocked in the container** (proxy: only session-bound repos, 403); available on a normal computer (60/h without token) | stars, releases, topics | lazy enrichment with cache + ETag, optional token |
| raw.githubusercontent.com (README, files) | ✓, no API limit | firmware README, file existence | thumbnail (first non-badge image), README display, checking Felucca tools (`web/fm1backup.js`, `tools/fm1_install.py`) |
| thefirmwarehub.com, fm1-editor.com/firmware | **not reachable** from the container | categories, status | only as link targets (`#anchor`) and in the link import (anchor → fuzzy name match). No scraper. |
| FM-1 Firmware Atlas (wero1414, CC0) | ✓ | 67 firmwares + real stars from 14 Oct 2026 | looked at, not integrated (yet) |

## Signature of the Mooring catalog

`catalog.signed.json` (envelope `mooring-catalog-envelope` v1, Ed25519 signature, 64 bytes) and
`catalog.json.intoto.jsonl` (DSSE/in-toto, keyid `dbe8230a406629ab`). The **public key is not
published**: all 26 commits of the repo (incl. the 4 "Publish Mooring" commits) contain only the three
catalog files; no `cicloid/mooring` repo, nothing via web search. Without the key there is no real verification –
which is why the app shows **no** "signed ✓". `catalog.mooring_signature_status()` only checks that the
SHA-256 in the in-toto statement matches the catalog file (consistency, not authenticity). Technically the
check would be feasible on Bookworm (python3-cryptography 38 supports Ed25519; it is missing in the 3.11 test venv,
hence an optional import). The actual protection remains the package checksum at install time (installer).

## Heuristics (README)

* Entry: `- [Name](url) - description …`; inline links are assigned to a role by their link text
  (install/flasher → installer; emulator/simulator/demo/play/try/preview → emulator; editor/studio/workbench →
  editor; manual/guide/handbook → manual; source; otherwise site). Combined texts ("Installer and emulator") → both.
* Author: "<word> by X" up to the first punctuation mark or conjunction, excluding "inspired/steered/… by"; "X's
  author" is resolved via entry X; fallback: repo owner.
* Licence: SPDX-like identifier in the text, "No licence stated", closed source → proprietary.
* Status flags: beta, alpha, experimental, wip (early, prototype, in progress …), paid, source code.
* Category (like the hub directories): keyword points, the first sentence counts triple → Synth, Groovebox,
  Sampler, Chords, Generative, Tool, Games & Fun, Original (section Official).
* Lineage: first the **loader SHA from Mooring** (feluccaG1/G2 → Felucca, sloopG3 → SLOOP, stock → Original),
  then text ("Built on/Based on/X fork/X-based …") resolved via other entries (e.g. GHOULBOX fork →
  Felucca), "FoMni" as a sub-branch of Felucca. 212 of 252 entries have a lineage.
* Matching Mooring ↔ README: GitHub repo (without links to subfolders), then normalised family/package name.
  Families without a README entry become their own entries (3), README entries without a package stay link entries.
  Packages without download (identity only) are matched by name; 2 (Vector37, SMK-37) remain unmatched.
* Felucca protocol ("backup-capable") only with evidence: families melodee/skiff/sloop/felucca, or (link import
  online) `web/fm1backup.js` or `tools/fm1_install.py` in the repo. Otherwise "checked when connecting".

## Fixtures

`tests/fixtures/catalog/github_*.json` are **reconstructed true to the schema** (GitHub REST v3), because the API is
blocked in the container; real values (tag, file name, size, URL) come from the Mooring catalog, stars
and dates are examples and carry a `_provenance` field. Untested on real hardware/network: responses
of the real API (field names follow the public documentation).

## Snapshot

`python3 tools/build_catalog_snapshot.py --mooring … --readme … --screenshots` → `fm1companion/data/catalog_snapshot.json`
(252 firmwares, 96 directly installable, 151 tools/links, 141 thumbnail addresses, ~300 KB).
