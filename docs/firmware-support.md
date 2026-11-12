# Firmware support – the catalog audit (2026-11-11)

Every firmware of the catalog (`fm1companion/data/catalog_snapshot.json`, 252 entries, 245 GitHub repositories) was
checked against its sources for what the app relies on: the INFO string it answers, its package identity, its
editor and backup protocol, its backup inventory, and how it installs. The runtime side of the result is
`fm1companion/fwfamily.py` (a table of the firmwares that name themselves or differ from their parent); this page is
the human side.

## How it was done

* `git clone --depth 1 --filter=blob:limit=1m` of every repository (241 cloned; 4 are gone: Oktowave, Neverhood,
  B-Boy Edition, KETCH); stars from `gh api search/repositories` (7 repositories per query).
* `tools/firmware_audit.py` reads each clone as data: the editor header `7D 46 4C`, `case ED_INFO` and the string
  it sends (its `#define` resolved), the backup commands' enum values (Felucca `ED_BACKUP_LIST` = 65, SLOOP
  `ED_BK_LIST` = 34, anything else), `ED_BK_IDS`, X0X's image backup (`BU_MAGIC`, `RS_REQ 0x41`), `PRODUCT` and the
  `--release` formula of `tools/build.py`, the web editor's `BACKUP_IDS` / `BK.RESTORE`, the docs' words on V15.
  Forks were compared with their parents by these fields; where a field differed, the source was read by hand.
* Every firmware that answers the editor's INFO (77) was then run against the app twice – the state before this
  change and after – with a fake device that answers exactly what its source answers (INFO, the backup commands it
  has, its inventory), once with its source's default INFO and once with what its `--release` build answers
  (`FELUCCA vX.Y` for Felucca's forks, `FELUCCA X.Y BETA` for SLOOP's). Command 34 on every audited firmware of
  Felucca's family is `UI_STATE` (a query) or does nothing without arguments; 65 is unassigned on SLOOP and its
  forks – both are safe to ask.

## Summary

| | count |
|---|---|
| catalog entries audited | 252 (248 with a repository, 4 of them gone; 4 without one) |
| answer the editor's INFO (`7D 46 4C`) | 77 – Felucca protocol 48, SLOOP protocol 17, X0X image 5 (4 of them X0X forks without its backup), own 2, none 9 |
| no Felucca INFO (fm1-base / FoMni / M-VAVE SDK / own frameworks) | 162 – backups stay off (the app says why), the firmware is named by its package identity |
| handled correctly before (of the 77) | 53 (55 backed up correctly; Saadet's restore and TRIBE's name were wrong) |
| fixed | 22 backup failures (15 SLOOP-protocol forks timed out on Felucca's LIST, 6 firmwares without the app's protocols timed out instead of a message, 1 inventory rejected) + misplaced restores (Saadet's objects as sample slots, sloopDX ↔ SLOOP, Clipper/Saadet/DUELL/Tessera backups onto other firmwares) + 5 identity collisions (TRIBE as X0X, TONITOR / Fart Machine One / RAMBEAVER as Skiff, sloopDX 2.6's FM-1_926) |
| not supported (and why) | Optimist and Hortator (backup protocols of their own), CARNAC OS, DREADNOUGHT, modular-one, Felucca SALT, organvave-1 and X0X's pre-1.0.9 forks (no backup commands): a clear message now; the 162 without INFO (no editor protocol to back up through) |

### The bugs

1. **Wrong backup protocol → timeout.** The app picked SLOOP's protocol only when the INFO contained "sloop",
   "karma" or "orbit". SLOOP's `tools/build.py --release X.Y` writes `FELUCCA X.Y BETA` – no name – and most of
   SLOOP's forks name themselves without "sloop" (`FELUCCA CLIPPER 1.3.1`, `JANGADA 1.0.1`, `FELUCCA winampFM`,
   `SPIRALFM1`, …): Felucca's LIST went out, nothing answered. 15 firmwares (Clipper 83★, Jangada, fm1-quest,
   FM1 MOVE, sloopDX's releases, Floyd and SLOOPbanks' releases, …).
2. **No protocol at all → timeout instead of a message.** Optimist (its own commands 43–48), CARNAC OS,
   DREADNOUGHT, modular-one, Felucca SALT, organvave-1.
3. **Unknown inventory rejected.** An inventory as long as a known one but with other IDs was refused ("unexpected
   object order"): WaveLoop's 23 objects (Felucca's 13 + 10..19) against Melodee's 23. Objects of a fork's own
   inventory larger than Melodee's limits were refused too. SLOOP's file reader refused every object but SLOOP's
   (Clipper's 12..14, Synthesthetic's 12).
4. **Objects misclassified.** Every object 32..63 was restored as a sample slot: Saadet's 32..40 (its state,
   performances, HYDRA waves) went through SMP_BEGIN – an empty one erased a sample slot.
5. **Cross-firmware restores.** Nothing stopped a sloopDX backup (its own 10/11) from going onto SLOOP, a Clipper
   or Saadet backup onto SLOOP or Felucca; sloopDX was named "sloop" (its INFO contains it) and got SLOOP's twin.
6. **Identity collisions.** TRIBE answers X0X's INFO unchanged (`X0X 0.3.0 BETA`, FM-1_9000300) and was taken for
   X0X; the FM-1_7xx rule took TONITOR (FM-1_700), Fart Machine One (701, 703) and RAMBEAVER (745) for Skiff;
   sloopDX 2.6's release FM-1_926 with a plain INFO was taken for Felucca.

### What changed (generic, data-driven)

* `fwfamily.py`: `info_name()` takes "FELUCCA", the version and build words off an INFO string; a table maps the
  names the audit found to the catalog's keys, their backup protocol (`felucca`, `sloop`, `x0x`, `own`, `none`),
  their layout (where their objects live, `""` = the parent's), their sample slots, their restore order and twin.
  Forks that keep their parent's protocol and inventory need no entry.
* `backup.protocol()`: a named firmware gets its protocol from the table; a plain `FELUCCA v…` or an unknown name is
  **asked** – Felucca's LIST (65), then SLOOP's (34), both read-only, the answer kept with the link; `own`/`none` and
  "answers neither" are clear messages before any backup command. Nothing is ever written with a guessed protocol.
* Inventories: unknown ones are backed up as they are (no "unexpected object order"; a generic size limit); the
  check for the running music and settings looks at objects 0 and 1 where the inventory has them.
* Layouts: `backup.layout_of()` / `layouts_fit()` / `check_layout()` – a backup only goes onto a firmware whose
  objects mean the same (Felucca ↔ Melodee ↔ unnamed forks that grew Felucca's inventory; SLOOP → KARMA, Clipper,
  Synthesthetic), refused before anything is written otherwise, for full and partial restores, both protocols.
* Sample slots: the family's (Saadet none); for an unknown inventory the device is asked with a PUT begin + abort
  (Felucca refuses PUT for its sample slots) – nothing written.
* SLOOP files: any object 0..63 is kept; `sloopbk.order()` restores in the fork's editor order, unknown objects after
  the banks; an object the device does not know is skipped like SLOOP's optional ones.
* Names: `protocol.Info.firmware_name` and `firmware.guess_firmware` trust the INFO name over remembered
  identities; X0X's INFO with an identity the catalog lists for one fork alone names the fork; the FM-1_7xx Skiff
  rule and SLOOP-style `FELUCCA X.Y BETA` releases defer to the catalog's identity. The studio has no twin for forks
  with layouts of their own (`StudioService.firmware_for`, `studio.backup_firmware`).
* The Discover page shows the backup protocol of every firmware in the table.
* Tests: `tests/test_firmware_support.py` (recorded INFO strings, inventories and fake devices).

### Install

All 96 catalog packages with a loader come from Mooring with `requiresStockV15`; the app then always takes the
detour over stock V15 (`flash/planner.py v15_option` "locked"). Identities and loader families are consistent with
the packages' own `tools/build.py`. Against the firmwares' docs the flag is **right** for TRIRÊME, ZEN-1, FuMi-1,
Cut-a-dor, GammaWave (from other custom firmwares), Prysm (ETCH refuses it) and stricter than needed for OPLbox and
Arctic Scream (they install over other firmwares too); most docs only describe the way back to V15. The detour is
the safe side, so the flag was left as the catalog has it. The 22 older packages without a download have no loader
family; the planner falls back to the package's own loader marker.

## The table

Sorted by stars. *INFO* is the string in the source (`…` = a build word such as WEB/TEST/DEV the release replaces);
*Backup via app* is the result of the simulated run before and after this change (`ok`, `timeout`, `rejected`,
`message` = a clear refusal; "twin-tested" = covered by the studio twins' tests). *Install*: package identities /
loader families / `requiresStockV15`.

| Firmware | ★ | Lineage | INFO (source default) | Protocol | Backup inventory | Backup via app (before → now) | Install (identity / loader / V15 flag) | Twin | Notes |
|---|---|---|---|---|---|---|---|---|---|
| SLOOP (`sloop`) | 781 | SLOOP | `FELUCCA SLOOP 2.8.5` | SLOOP 34–36 | SLOOP 2.7+ 16 | ok | FM-1_900 / sloopG3 / V15 | ✓ | named in INFO; its forks' `--release` builds answer `FELUCCA X.Y BETA` (no name) |
| Felucca (`felucca`) | 707 | Felucca | `FELUCCA …` | Felucca 65–67 | Felucca 13 | ok | FM-1_915, FM-1_916 / feluccaG2 / V15 | ✓ | plain INFO, shared with every fork built with `--release`: protocol probed (65 answers at once) |
| FM1 Open (`fm1-open`) | 346 | Original M-VAVE | – | own (FM1 Open file protocol) | – | – | no package | ✓ | M-VAVE SDK: own file protocol (fm1openbk) |
| JIANT FM (`jiant-fm`) | 234 | Felucca | `JIANT …` | Felucca 65–67 | JIANT 43 / 35 (songs 40..) | ok (twin-tested) | no package | ✓ | own song inventory (twin); the evaluator found no ED_BK_IDS literal |
| X0X (`x0x`) | 230 | Felucca | `X0X …` | X0X image 0x41–43 | image (6 objects) | ok from 1.0.9 (twin-tested) | FM-1_9010100 / feluccaG1 / V15 | ✓ | image backup from 1.0.9; older builds: clear message |
| Melodee (`melodee`) | 201 | Felucca | `MELODEE …` | Felucca 65–67 | Melodee 57 | ok | no package | ✓ | firmware/src/editor_backup.c |
| Skiff (`skiff`) | 154 | Felucca | – | Felucca 65–67 | Skiff 29 (0..9, 16..34) | ok (twin-tested) | FM-1_712 / feluccaG2 / V15 | ✓ | INFO as Felucca's, DRUM-X engine names it |
| AMB-1 (`amb-1`) | 126 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | FM-1_7010200, FM-1_7010300 / feluccaG2 / V15 | ✓ | no INFO (twin: no backup) |
| ChoralRoot FM-1 (`choralroot-fm-1`) | 106 | Felucca | – | Felucca 65–67 (subset, RESTART 72) | ChoralRoot 34 / 28 | ok (twin-tested) | no package | ✓ | own inventory (twin) |
| GHOULBOX (`ghoulbox`) | 86 | Felucca | `FELUCCA … GB2.4` | Felucca 65–67 | Felucca 13 | ok | FM-1_900 / feluccaG2 / V15 |  | INFO `FELUCCA WEB GB2.4` (web build); Felucca's 13 objects |
| Clipper (`clipper`) | 83 | SLOOP | `FELUCCA CLIPPER 1.3.1` | SLOOP 34–36 | 0,1,2,3,4,5,6,7,8,9,12,13,14,32,33,34,35 | timeout → **ok** | no package |  | **fixed**: INFO named Felucca → timeout; its objects 12..14 rejected as damaged by the SLOOP file reader; restored in its editor's order now |
| FoMni (`fomni`) | 81 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | FM-1_8000400 / feluccaG1 / V15 | ✓ | no INFO (twin: no backup) |
| LOOM-8 (`loom-8`) | 71 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package | ✓ | fm1-base image backup (LM8B), no INFO |
| Lunar Modulator (`lunar-modulator`) | 63 | – | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| Hortator (`hortator`) | 57 | Felucca | – | own protocol | – | no INFO: backup off, named by identity | FM-1_900 / feluccaG1 / V15 |  | own protocol 7D 48 54 (Hortator INFO asked by the device layer) |
| OberVave (`obervave`) | 57 | – | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| Dinghy (`dinghy`) | 53 | SLOOP | – | no Felucca INFO | – | no INFO: backup off, named by identity | FM-1_993 / sloopG3 / V15 |  |  |
| GR!MM (`gr-mm`) | 42 | SLOOP | – | no Felucca INFO | – | no INFO: backup off, named by identity | FM-1_966 / sloopG3 / V15 |  | own protocol 7D 47 52; FM-1_966 names it |
| ORBIT (`orbit`) | 40 | SLOOP | `FELUCCA ORBIT …` | SLOOP 34–36 | 0,1,2,3,4,5,6,7,8,9,10,32,33,34,35 | ok | FM-1_900 / sloopG3 / V15 |  | SLOOP protocol, own layout (ORP8): cross-restores refused |
| DUB-1 (`dub-1`) | 39 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | FM-1_8010200, FM-1_8010300 / feluccaG1 / V15 |  |  |
| Jangada (`jangada`) | 37 | SLOOP | `JANGADA 1.0.1` | SLOOP 34–36 | 0,1,2,3,4,5,6,7,8,9,10,11,32,33,34 | timeout → **ok** | FM-1_909, FM-1_910 / sloopG3 / V15 |  | **fixed**: INFO `JANGADA …` (no FELUCCA) → Felucca protocol → timeout |
| RemoteFM1 (`remotefm1`) | 37 | Original M-VAVE | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| FMini-1 (`fmini-1`) | 36 | Felucca | `FELUCCA …` | Felucca 65–67 | 0,1,2,3,4,5,6,7,8,9 | ok | FM-1_900 / feluccaG2 / V15 |  |  |
| Optimist (`optimist`) | 35 | SLOOP | `FELUCCA OPTIMIST 0.3` | own (BK_LIST=43) | – | timeout → **message** | no package |  | **fixed**: own backup commands 43–48 → was a timeout, now a clear message |
| zp12 (`zp12`) | 35 | SLOOP | – | no Felucca INFO | – | no INFO: backup off, named by identity | FM-1_974 / sloopG3 / V15 |  |  |
| fm1-dock (`fm1-dock`) | 33 | SLOOP | – | no Felucca INFO | – | no INFO: backup off, named by identity | FM-1_956 / sloopG3 / V15 |  |  |
| Tonitor (`tonitor`) | 31 | SLOOP | – | no Felucca INFO | – | no INFO: backup off, named by identity | FM-1_700 / sloopG3 / V15 |  | **fixed** (identity): FM-1_700 was guessed Skiff (FM-1_7xx rule) |
| ECO-1 (`eco-1`) | 30 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| GRAV-1 (`grav-1`) | 30 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| HYP-1 (`hyp-1`) | 30 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | FM-1_7010505, FM-1_7010544 / feluccaG1 / V15 |  |  |
| LOF-1 (`lof-1`) | 30 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| Ravel (`ravel`) | 29 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| Tessera (`tessera`) | 29 | Felucca | `TESSERA v2.0` | Felucca 65–67 | 30 objects (0..29) | ok | no package |  | TSP1 projects in Melodee's places: own layout |
| Polly (`polly`) | 27 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | FM-1_3000403, FM-1_3000500 / feluccaG2 / V15 |  |  |
| sloopDX (`sloopdx`) | 27 | SLOOP | `FELUCCA sloopDX 4.0.1` | SLOOP 34–36 | 0,1,2,3,4,5,6,7,8,9,10,11 | ok/timeout → **ok** | FM-1_926, FM-1_935, FM-1_940 / sloopG3 / V15 |  | **fixed**: releases FM-1_926/935/940 answer `FELUCCA X.Y BETA` → Felucca LIST timed out; INFO `sloopDX` was taken for SLOOP, its own 10/11 restored as SLOOP's MIDI LEARN/theme, SLOOP twin offered |
| Caravel (`caravel`) | 25 | Felucca | `CARAVEL …` | Felucca 65–67 | Felucca 13 | ok | no package |  | INFO "CARAVEL …"; Felucca's 13 objects |
| CoralFlower (`coralflower`) | 25 | Felucca | `FELUCCA v1.0` | Felucca 65–67 | CoralFlower 26 | ok (twin-tested) | no package | ✓ | own inventory (twin); INFO names it |
| MAKU (`maku`) | 25 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | FM-1_910 / feluccaG2 / V15 |  |  |
| Mutable Dreams (`mutable-dreams`) | 25 | – | – | X0X image 0x41–43 (no INFO) | – | no INFO: backup off, named by identity | no package |  |  |
| CTL-1 (`ctl-1`) | 24 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | FM-1_6000902, FM-1_6010000 / feluccaG2 / V15 |  |  |
| Fart Machine One (`fart-machine-one`) | 24 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | FM-1_701, FM-1_703 / feluccaG1 / V15 |  | **fixed** (identity): FM-1_701/703 were guessed Skiff |
| FM Lab (`fm-lab`) | 24 | SLOOP | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| Erik37 (`erik37`) | 22 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| FiMba-1 (`fimba-1`) | 22 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | FM-1_8000601 / feluccaG1 / V15 |  |  |
| MONO2K (`mono2k`) | 22 | SLOOP | – | no Felucca INFO | – | no INFO: backup off, named by identity | FM-1_980 / sloopG3 / V15 |  |  |
| DUELL (`duell`) | 21 | Felucca | `{DUELL_VERSION_STR}` | Felucca 65–67 | 24 objects (0..34) | ok | FM-1_901 / feluccaG2 / V15 |  | own layout (13..26 patterns, wavetables): cross-restores refused |
| FuMi-1 (`fumi-1`) | 21 | Felucca, FoMni | – | no Felucca INFO | – | no INFO: backup off, named by identity | FM-1_7010007, FM-1_7010100 / feluccaG1 / V15 |  | docs: expects stock V15 – flag right |
| Rainbow mode (`rainbow-mode`) | 21 | Felucca | `FELUCCA …` | Felucca 65–67 | Felucca 13 | ok | FM-1_911 / feluccaG2 / V15 |  |  |
| fm1-nes (`fm1-nes`) | 19 | Original M-VAVE | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| Niagara Drift (`niagara-drift`) | 17 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | FM-1_8000100 / feluccaG1 / V15 |  |  |
| Saadet (`saadet`) | 17 | Felucca | `SAADET v{APP_VERSION}` | Felucca 65–67 | 32 objects (1..40) | ok | no package |  | **fixed**: 32..40 are its state/performances/waves, were restored as sample slots (an empty one erased a slot); a Saadet backup could go onto Melodee/Felucca |
| FM-106 (`fm-106`) | 16 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | FM-1_907 / feluccaG2 / V15 |  |  |
| FRASIL (`frasil`) | 16 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| Araminta (`araminta`) | 15 | Felucca | `ARAMINTA …` | Felucca 65–67 | Felucca 13 | ok | no package |  | INFO "ARAMINTA …"; Felucca's 13 objects |
| fm1-chord (`fm1-chord`) | 15 | Felucca | `FELUCCA …` | Felucca 65–67 | Melodee/Felucca 12 | ok | FM-1_903 / feluccaG1 / V15 |  |  |
| FM1 Move (`fm1-move`) | 15 | – | `FELUCCA FM1 MOVE 0.3.1` | SLOOP 34–36 | 0,1,2,3,4,5,6,7,8,9,10,32,33,34,35 | timeout → **ok** | no package |  | **fixed**: timeout (SLOOP protocol, named `FM1 MOVE`) |
| FM1 Quest (`fm1-quest`) | 15 | SLOOP | `FELUCCA 0.9` | SLOOP 34–36 | SLOOP 2.7+ 16 | timeout → **ok** | FM-1_900 / sloopG3 / V15 |  | **fixed**: INFO `FELUCCA 0.9` (no name) → timeout; now probed → SLOOP |
| PERF-1 (`perf-1`) | 15 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| Carnac OS (`carnac-os`) | 14 | Felucca | `CARNAC OS …` | editor INFO, no backup | – | timeout → **message** | FM-1_900 / feluccaG1 / V15 |  | **fixed**: editor INFO, no backup → clear message instead of timeout |
| ZEN-1 (`zen-1`) | 14 | Felucca, FoMni | – | no Felucca INFO | – | no INFO: backup off, named by identity | FM-1_6010002, FM-1_6010100, FM-1_6010200 / feluccaG1 / V15 |  | docs: expects stock V15 – flag right |
| FM1-GM (`fm1-gm`) | 13 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | FM-1_600 / feluccaG1 / V15 |  |  |
| fm1-radio (`fm1-radio`) | 13 | – | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| Frodhes (`frodhes`) | 12 | SLOOP | – | no Felucca INFO | – | no INFO: backup off, named by identity | FM-1_958 / sloopG3 / V15 |  |  |
| Golana (`golana`) | 12 | Felucca | `FELUCCA …` | Felucca 65–67 | Felucca 13 | ok | FM-1_911, FM-1_912, FM-1_913 / feluccaG2 / V15 |  |  |
| KAWA (`kawa`) | 12 | SLOOP | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| MONK-1 (`monk-1`) | 12 | – | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| TRIBE (`tribe`) | 12 | Felucca | `X0X …` | editor INFO, no backup | – | message | FM-1_9000100, FM-1_9000200, FM-1_9000300 / feluccaG1 / V15 |  | **fixed** (identity): answers X0X's INFO unchanged; FM-1_9000x00 named TRIBE from the catalog |
| winampFM (`winampfm`) | 12 | SLOOP | `FELUCCA winampFM 0.9` | SLOOP 34–36 | SLOOP 2.7+ 16 | timeout → **ok** | FM-1_900 / sloopG3 / V15 |  | **fixed**: timeout |
| April OS (`april-os`) | 11 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | paid/closed |  |  |
| BBL JAM BOX (`bbl-jam-box`) | 10 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| Bigorna (`bigorna`) | 10 | – | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| Bubba Machine (`bubba-machine`) | 10 | eigenständig | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| ETCH (`etch`) | 10 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | FM-1_1038, FM-1_1500 / feluccaG2 / V15 |  |  |
| GammaWave (GW-1) (`gammawave-gw-1`) | 10 | Felucca | `FELUCCA …` | Felucca 65–67 | as JIANT's | as JIANT (not simulated) | FM-1_981 / feluccaG2 / V15 |  | JIANT-based (`JIANT` landing build); inventory as JIANT's |
| PIGEON (`pigeon`) | 10 | Felucca | `PIGEON v0.5.0-beta.1` | Felucca 65–67 | Felucca 13 | ok | no package |  | INFO "PIGEON v0.5.0-beta.1"; Felucca's 13 objects |
| S1Vave (`s1vave`) | 10 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| TransitFM1 (`transitfm1`) | 10 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| CS-1 (`cs-1`) | 9 | SLOOP | – | no Felucca INFO | – | no INFO: backup off, named by identity | FM-1_950 / sloopG3 / V15 |  |  |
| FMEL-1 (`fmel-1`) | 9 | SLOOP | – | no Felucca INFO | – | no INFO: backup off, named by identity | paid/closed |  |  |
| Matryoshka (`matryoshka`) | 9 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| MNM-1 (galfaroth) (`mnm-1-galfaroth`) | 9 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| NOIZ DEMONS (`noiz-demons`) | 9 | Felucca | `FELUCCA …` | Felucca 65–67 | Felucca 13 | ok | FM-1_900 / feluccaG2 / V15 |  |  |
| PurpleMonkey FM-1 (`purplemonkey-fm-1`) | 9 | Felucca | `FELUCCA PurpleMonkey 0.1-dev` | Felucca 65–67 | Melodee/Felucca 12 | ok | FM-1_927 / feluccaG1 / V15 |  | INFO "FELUCCA PurpleMonkey 0.1-dev" |
| WaveLoop FM-1 (`waveloop-fm-1`) | 9 | Felucca | `FELUCCA …` | Felucca 65–67 | 23 objects (0..34) | rejected → **ok** | FM-1_910 / feluccaG1 / V15 |  | **fixed**: 23 objects (Felucca's 13 + 10..19) = length of Melodee's 23 → rejected as "unexpected object order" |
| Felucca Salt (`felucca-salt`) | 8 | Felucca | `FELUCCA FONT …` | editor INFO, no backup | – | timeout → **message** | FM-1_909 / feluccaG1 / V15 |  | **fixed**: no backup commands → probe answers neither: clear message |
| FMER (`fmer`) | 8 | – | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| Shine On (`shine-on`) | 8 | – | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| 386DX (`386dx`) | 7 | – | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| Chipbox (`chipbox`) | 7 | SLOOP | – | no Felucca INFO | – | no INFO: backup off, named by identity | FM-1_337 / sloopG3 / V15 |  |  |
| MC-1 (`mc-1`) | 7 | eigenständig | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| MIN-1 (`min-1`) | 7 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | FM-1_5000201 / feluccaG1 / V15 |  |  |
| No Man Sky FM (`no-man-sky-fm`) | 7 | SLOOP | `FELUCCA NMS FM 0.9` | SLOOP 34–36 | SLOOP 2.7+ 16 | timeout → **ok** | FM-1_900 / sloopG3 / V15 |  | **fixed**: timeout |
| TRIO (`trio`) | 7 | Original M-VAVE | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| Bytemania (`bytemania`) | 6 | SLOOP | – | no Felucca INFO | – | no INFO: backup off, named by identity | FM-1_942 / sloopG3 / V15 |  |  |
| Fantasma (`fantasma`) | 6 | Felucca | `FELUCCA …` | Felucca 65–67 | Felucca 13 | ok | FM-1_911 / feluccaG2 / V15 |  |  |
| GATOR (`gator`) | 6 | SLOOP | – | no Felucca INFO | – | no INFO: backup off, named by identity | FM-1_906 / sloopG3 / V15 |  |  |
| Gnomad (`gnomad`) | 6 | Felucca | `FELUCCA …` | Felucca 65–67 | Melodee/Felucca 12 | ok | FM-1_900 / feluccaG1 / V15 |  |  |
| Mini-Tone (`mini-tone`) | 6 | Felucca | `FELUCCA …` | Felucca 65–67 | Felucca 13 | ok | FM-1_900 / feluccaG2 / V15 |  |  |
| Modular One (`modular-one`) | 6 | Felucca | `FELUCCA M1 0.1` | editor INFO, no backup | – | timeout → **message** | FM-1_900 / feluccaG1 / V15 |  | **fixed**: as CARNAC OS |
| SIDOU (`sidou`) | 6 | – | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| UnholyUnion (`unholyunion`) | 6 | Felucca | `FELUCCA …` | Felucca 65–67 | Felucca 13 | ok | no package |  |  |
| Velo (`velo`) | 6 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| Arctic Scream (`arctic-scream`) | 5 | SLOOP | – | no Felucca INFO | – | no INFO: backup off, named by identity | FM-1_977 / sloopG3 / V15 |  | docs: installs from V15, GR!MM or any other firmware – flag stricter than needed |
| BEETLE (`beetle`) | 5 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | FM-1_2123 / feluccaG2 / V15 |  |  |
| Berlinan (`berlinan`) | 5 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| DRIFT (Fabian Freitag) (`drift-fabian-freitag`) | 5 | Felucca | `FELUCCA …` | Felucca 65–67 | Felucca 13 | ok | no package |  |  |
| ESTAMPIE (`estampie`) | 5 | Felucca | `FELUCCA … GB2.1` | Felucca 65–67 | Felucca 13 | ok | no package |  | Ghoulbox fork (`GB2.1`): named as Ghoulbox, same layout |
| fm1-doom (`fm1-doom`) | 5 | – | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| K-WASUB (`k-wasub`) | 5 | – | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| MIDIMON (`midimon`) | 5 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| MYCELIUM (`mycelium`) | 5 | SLOOP | – | no Felucca INFO | – | no INFO: backup off, named by identity | FM-1_900 / sloopG3 / V15 |  |  |
| SLOOP ALG (`sloop-alg`) | 5 | SLOOP | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| SLOOP DX7 Banks (`sloopbanks`) | 5 | SLOOP | `FELUCCA SLOOP 2.3` | SLOOP 34–36 | 0,1,2,3,4,5,6,7,32,33,34 | ok/timeout → **ok** | FM-1_925 / sloopG3 / V15 |  | **fixed**: release FM-1_925 answers `FELUCCA 2.5 BETA` → timeout |
| Speak37 (`speak37`) | 5 | Felucca | `FELUCCA …` | Felucca 65–67 | Felucca 13 | ok | no package |  |  |
| TastyWaves (`tastywaves`) | 5 | SLOOP | – | no Felucca INFO | – | no INFO: backup off, named by identity | FM-1_937 / sloopG3 / V15 |  |  |
| Trireme FM-1 (`trireme-fm-1`) | 5 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | FM-1_961 / feluccaG2 / V15 |  | docs: "install the stock V15 firmware first" – flag right |
| B1 Drawbar Organ (`b1-drawbar-organ`) | 4 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | FM-1_481026 / feluccaG2 / V15 |  |  |
| Cut-A-Dor (`cut-a-dor`) | 4 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | FM-1_7010201 / feluccaG1 / V15 |  | docs: start from V15 – flag right |
| FOMAJAM (`fomajam`) | 4 | SLOOP | – | no Felucca INFO | – | no INFO: backup off, named by identity | FM-1_961 / sloopG3 / V15 |  |  |
| LCD-4 (`lcd-4`) | 4 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | FM-1_4000102 / feluccaG1 / V15 |  |  |
| Loom FM-1 (`loom-fm-1`) | 4 | Felucca | `FELUCCA …` | Felucca 65–67 | Felucca 13 | ok | FM-1_985 / feluccaG2 / V15 |  |  |
| Modular-1 (`modular-1`) | 4 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| OrganVave-1 (`organvave-1`) | 4 | – | `FELUCCA …` | editor INFO, no backup | – | timeout → **message** | no package |  | **fixed**: as Felucca SALT |
| TheReFmB1rth (`therefmb1rth`) | 4 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| Tuna (`tuna`) | 4 | Felucca | `FELUCCA …` | Felucca 65–67 | Felucca 13 | ok | FM-1_900 / feluccaG2 / V15 |  |  |
| BOP-1 (`bop-1`) | 3 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| Crepuscular Homunculus (`crepuscular-homunculus`) | 3 | SLOOP | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| Doom FM-1 / SMK (`doom-fm-1-smk`) | 3 | – | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| Evander's Music Box (`evander-s-music-box`) | 3 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| SLOOP Floyd FM (`floyd`) | 3 | SLOOP | `FELUCCA SLOOP 2.4.1` | SLOOP 34–36 | 0,1,2,3,4,5,6,7,8,32,33,34,35 | ok/timeout → **ok** | FM-1_924 / sloopG3 / V15 |  | **fixed**: release FM-1_924 answers `FELUCCA 2.4 BETA` → timeout |
| FM-1 Doom (`fm-1-doom`) | 3 | – | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| FM1 Keyboard Academy (`fm1-keyboard-academy`) | 3 | Original M-VAVE | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| fm1-polyseq (`fm1-polyseq`) | 3 | eigenständig | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| GameDude (`gamedude`) | 3 | – | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| Glitchbox (`glitchbox`) | 3 | Felucca | `FELUCCA …` | Felucca 65–67 | Felucca 13 | ok | no package |  |  |
| J6 (`j6`) | 3 | Felucca | – | X0X image 0x41–43 (no INFO) | – | no INFO: backup off, named by identity | no package |  |  |
| Mocky (`mocky`) | 3 | – | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| MOPE-1 (`mope-1`) | 3 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| NU//METAL SKRATCHMASTER PRO (`nu-metal-skratchmaster-pro`) | 3 | SLOOP | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| Switch Blade (`switch-blade`) | 3 | Felucca | `Switch Blade …` | Felucca 65–67 | 0,1,2,3,4,5,6,7 | ok | no package |  | INFO "Switch Blade …"; ED_BK_IDS 0..7 |
| Synthesthetic (`synthesthetic`) | 3 | SLOOP | `FELUCCA SYNTHESTHETIC 0.2` | SLOOP 34–36 | 0,1,2,3,4,5,6,7,8,9,10,11,12,32,33,34,35 | timeout → **ok** | no package |  | **fixed**: timeout; object 12 rejected by the SLOOP file reader |
| X0X-RMX (`x0x-rmx`) | 3 | Felucca | `X0X …` | editor INFO, no backup | – | message | no package |  | X0X fork from before X0X's backup: clear message |
| AMB-1 Evolved (`amb-1-evolved`) | 2 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | FM-1_7080001 / feluccaG1 / V15 |  |  |
| BRUTAL FM (`brutal-fm`) | 2 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| Bunker Evolved (`bunker-evolved`) | 2 | Felucca | `FELUCCA BE 1.0-RC2` | Felucca 65–67 | Felucca 13 | ok | FM-1_7100002 / feluccaG2 / V15 |  | INFO "FELUCCA BE 1.0-RC2" |
| Demake synth (`demake-synth`) | 2 | SLOOP | – | no Felucca INFO | – | no INFO: backup off, named by identity | FM-1_969 / sloopG3 / V15 |  |  |
| DIRGE (`dirge`) | 2 | Felucca | `DIRGE …` | Felucca 65–67 | Felucca 13 | ok | no package |  | INFO "DIRGE …"; Felucca's 13 objects |
| Flyfish (`flyfish`) | 2 | Felucca | `FELUCCA …` | Felucca 65–67 | Felucca 13 | ok | FM-1_900 / feluccaG2 / V15 |  |  |
| fm1-amy (`fm1-amy`) | 2 | Felucca | `X0X …` | X0X image 0x41–43 | – | message | no package |  | X0X's backup kept |
| fm1-doom (Deadlock) (`fm1-doom-deadlock`) | 2 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| fm1-rocket-os (`fm1-rocket-os`) | 2 | Felucca | `X0X …` | editor INFO, no backup | – | message | no package |  | as X0X-RMX |
| fm1-t1 (`fm1-t1`) | 2 | – | `FELUCCA …` | Felucca 65–67 | Felucca 13 | ok | no package |  |  |
| FMDj (`fmdj`) | 2 | Original M-VAVE | `FELUCCA …` | Felucca 65–67 | Felucca 13 | ok | no package |  |  |
| LUOGU (`luogu`) | 2 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| NOCT FM (`noct-fm`) | 2 | – | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| Norilsk (`norilsk`) | 2 | – | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| NoteSorcery (`notesorcery`) | 2 | SLOOP | `FELUCCA NOTESORCERY …` | SLOOP 34–36 | 0,1,2,3,4,5,6,7,8,9,32,33,34,35 | timeout → **ok** | no package |  | **fixed**: timeout |
| SPELLBOX (`spellbox`) | 2 | – | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| Tonic (`tonic`) | 2 | – | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| TripDeck (`tripdeck`) | 2 | – | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| Acultur8 X0X (`acultur8-x0x`) | 1 | Felucca | `X0X …` | editor INFO, no backup | – | message | no package |  | as X0X-RMX |
| AEther (`aether`) | 1 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| Album (`album`) | 1 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| AmbWave (`ambwave`) | 1 | Felucca | `FELUCCA …` | Felucca 65–67 | Felucca 13 | ok | FM-1_900 / feluccaG1 / V15 |  |  |
| BEETLE BLUE (`beetle-blue`) | 1 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | FM-1_2123 / feluccaG2 / V15 |  |  |
| Bongo Cat's Tiny Studio (`bongo-cat-s-tiny-studio`) | 1 | Felucca | `BONGO CAT 0.4.0-rc1` | Felucca 65–67 | Felucca 13 | ok | FM-1_980 / feluccaG2 / V15 |  | INFO "BONGO CAT 0.4.0-rc1" |
| BREAK//174 (`break-174`) | 1 | – | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| CHORDS (`chords`) | 1 | Felucca, FoMni | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| CRATE (`crate`) | 1 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| DelRio (`delrio`) | 1 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| DRIFT (MaxPower) (`drift-maxpower`) | 1 | Felucca, FoMni | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| DriftSeq (`driftseq`) | 1 | – | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| FIELD (`field`) | 1 | Felucca | – | X0X image 0x41–43 (no INFO) | – | no INFO: backup off, named by identity | no package |  |  |
| FIZZ (`fizz`) | 1 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| FM-1 SID (`fm-1-sid`) | 1 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| FM-PLAITS (`fm-plaits`) | 1 | Original M-VAVE | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| fm1-mdx (`fm1-mdx`) | 1 | – | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| fm1-wheel (`fm1-wheel`) | 1 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| FN ACID (`fn-acid`) | 1 | SLOOP | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| HERA (`hera`) | 1 | SLOOP | – | no Felucca INFO | – | no INFO: backup off, named by identity | FM-1_4310100 / sloopG3 / V15 |  |  |
| IND-1 (`ind-1`) | 1 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | FM-1_5000201 / feluccaG1 / V15 |  |  |
| KIDBOY (`kidboy`) | 1 | Felucca | `FELUCCA …` | Felucca 65–67 | Felucca 13 | ok | FM-1_938 / feluccaG2 / V15 |  |  |
| Makohai (`makohai`) | 1 | SLOOP | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| Mini-Vision (`mini-vision`) | 1 | Felucca | `FELUCCA …` | Felucca 65–67 | Felucca 13 | ok | FM-1_900 / feluccaG2 / V15 |  |  |
| MNM-1 (`mnm-1`) | 1 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| MODELAR (`modelar`) | 1 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| Monohull (`monohull`) | 1 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | FM-1_928 / feluccaG2 / V15 |  |  |
| MORPHEUS (`morpheus`) | 1 | SLOOP | – | no Felucca INFO | – | no INFO: backup off, named by identity | FM-1_8000013 / sloopG3 / V15 |  |  |
| NESynth (`nesynth`) | 1 | – | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| NIGHTFALL (`nightfall`) | 1 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | FM-1_999 / feluccaG2 / V15 |  |  |
| OPLBOX (`oplbox`) | 1 | SLOOP | – | no Felucca INFO | – | no INFO: backup off, named by identity | FM-1_964 / sloopG3 / V15 |  | docs: installs over stock V14/V15, Felucca, SLOOP, Dinghy – flag stricter than needed |
| OPN-1 (`opn-1`) | 1 | SLOOP | – | no Felucca INFO | – | no INFO: backup off, named by identity | FM-1_950 / sloopG3 / V15 |  |  |
| Patricktronics (`patricktronics`) | 1 | Felucca | `FELUCCA …` | Felucca 65–67 | Felucca 13 | ok | no package |  |  |
| RAMBEAVER (`rambeaver`) | 1 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | FM-1_745 / feluccaG1 / V15 |  | **fixed** (identity): FM-1_745 was guessed Skiff |
| RED MASS VAMPIRE OS (`red-mass-vampire-os`) | 1 | – | `RED MASS VAMPIRE OS …` | Felucca 65–67 | 18 objects (0..34) | ok | no package |  | own objects 10..14: own layout |
| REGGAE-OS (`reggae-os`) | 1 | Felucca | `FELUCCA …` | Felucca 65–67 | Felucca 13 | ok | no package |  |  |
| Scintilla (`scintilla`) | 1 | Original M-VAVE | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| SEQ-4 (`seq-4`) | 1 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| SPIRALFM1 (`spiralfm1`) | 1 | SLOOP | `SPIRALFM1 1.0` | SLOOP 34–36 | 0,1,2,3,4,5,6,7,32,33,34 | timeout → **ok** | no package |  | **fixed**: timeout (INFO without FELUCCA) |
| Threadboard (`threadboard`) | 1 | – | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| ACID FM (`acid-fm`) | 0 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| ACID FM1 (`acid-fm1`) | 0 | – | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| AMARU OS (`amaru-os`) | 0 | Felucca | `FELUCCA BE 1.0-RC2` | Felucca 65–67 | Felucca 13 | ok | no package |  | Bunker Evolved fork, same INFO (`FELUCCA BE`): named as Bunker Evolved |
| Barge (`barge`) | 0 | SLOOP | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| BB1 (Big Beat) (`bb1-big-beat`) | 0 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| Cat Piano (`cat-piano`) | 0 | – | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| core-fm1 (`core-fm1`) | – | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| CRT-4 (`crt-4`) | 0 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | FM-1_2000102 / feluccaG1 / V15 |  |  |
| Deal or No Deal (`deal-or-no-deal`) | 0 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| DREADNOUGHT (`dreadnought`) | 0 | Felucca | `DREADNOUGHT …` | editor INFO, no backup | – | timeout → **message** | no package |  | **fixed**: as CARNAC OS |
| DUODRONE (`duodrone`) | 0 | – | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| Felucca 8 Patterns (`felucca-8-patterns`) | – | Felucca | `ARGO …` | Felucca 65–67 | Felucca 13 | ok | FM-1_900 / feluccaG2 / V15 |  | INFO "ARGO …"; Felucca's 13 objects |
| Felucca MPE (`felucca-mpe`) | 0 | Felucca | `FELUCCA …` | Felucca 65–67 | Felucca 13 | ok | no package |  |  |
| Finton (`finton`) | 0 | – | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| FM-1 B-Boy Edition (`fm-1-b-boy-edition`) | – | – | – | repo gone | – | – | no package |  |  |
| FM-1 Neverhood (`fm-1-neverhood`) | – | Felucca | – | repo gone | – | – | FM-1_731800 / feluccaG2 / V15 |  |  |
| FM-1 Studio Tess (`fm-1-studio-tess`) | 0 | Felucca | `MELODEE …` | Felucca 65–67 | 31 objects (0..31) | ok | no package |  |  |
| FM-1+VA (`fm-1-va`) | – | – | – | – | – | – | paid/closed |  |  |
| FM1 Betis Mode (`fm1-betis-mode`) | 0 | Original M-VAVE | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| FM1 Cofrade (`fm1-cofrade`) | 0 | Original M-VAVE | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| fm1-ctrl (`fm1-ctrl`) | 0 | – | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| FM1 time-signal source (`fm1-time-signal-source`) | 0 | Original M-VAVE | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| genAMB (`genamb`) | – | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| Golana DZ Edition (`golana-dz-edition`) | 0 | Felucca | `FELUCCA …` | Felucca 65–67 | Felucca 13 | ok | FM-1_902 / feluccaG2 / V15 |  |  |
| Groove OS (`groove-os`) | – | – | – | – | – | – | paid/closed |  |  |
| Haave (`haave`) | 0 | Felucca | `FELUCCA …` | Felucca 65–67 | Felucca 13 | ok | no package |  |  |
| Haunted FM-1 (`haunted-fm-1`) | 0 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| Hungry Travis (`hungry-travis`) | 0 | – | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| Ketch (`ketch`) | – | Felucca | – | repo gone | – | – | no package |  |  |
| LAUNCHPARTY (`launchparty`) | 0 | SLOOP | `FELUCCA LAUNCHPARTY 0.3.2 PUBLIC` | SLOOP 34–36 | 0,1,2,3,4,5,6,7,32,33,34 | timeout → **ok** | FM-1_900 / sloopG3 / V15 |  | **fixed**: timeout |
| MASIF (`masif`) | 0 | SLOOP | – | no Felucca INFO | – | no INFO: backup off, named by identity | FM-1_961 / sloopG3 / V15 |  |  |
| MD-One (`md-one`) | 0 | SLOOP | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| More Chords (`more-chords`) | 0 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| Oktowave (`oktowave`) | – | Felucca | – | repo gone | – | – | no package |  |  |
| ONE CLICK FM-1 (`one-click-fm-1`) | 0 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| OpenIFW-fm1 (`openifw-fm1`) | 0 | SLOOP | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| PALEOTONE (`paleotone`) | – | Felucca | `FELUCCA PALEOTONE 2.8.4` | SLOOP 34–36 | SLOOP 2.7+ 16 | timeout → **ok** | no package |  | **fixed**: timeout |
| Prysm (`prysm`) | 0 | SLOOP | – | no Felucca INFO | – | no INFO: backup off, named by identity | FM-1_987 / sloopG3 / V15 |  | docs: ETCH refuses it, restore V15 first – flag right |
| Quod (`quod`) | 0 | SLOOP | – | no Felucca INFO | – | no INFO: backup off, named by identity | FM-1_901 / sloopG3 / V15 |  |  |
| REDACTED (`redacted`) | – | Felucca | – | – | – | – | no package |  |  |
| RIFT (`rift`) | 0 | SLOOP | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| ROM-1 (`rom-1`) | 0 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| Selva1 (`selva1`) | 0 | SLOOP | `FELUCCA SELVA1 0.1` | SLOOP 34–36 | SLOOP 2.7+ 16 | timeout → **ok** | FM-1_901 / sloopG3 / V15 |  | **fixed**: timeout |
| Shed (`shed`) | 0 | Original M-VAVE | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| SID-1 (`sid-1`) | 0 | SLOOP | – | no Felucca INFO | – | no INFO: backup off, named by identity | FM-1_960 / sloopG3 / V15 |  |  |
| SkurkyMello (`skurkymello`) | 0 | SLOOP | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| M-VAVE Original-Firmware (`stock`) | – | Original M-VAVE | – | – | – | – | FM-1_015, FM-1_016 / stock |  |  |
| UNIBOX (`unibox`) | 0 | SLOOP | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| UTA BOX (`uta-box`) | 0 | – | – | no Felucca INFO | – | no INFO: backup off, named by identity | paid/closed |  |  |
| VNL-1 (`vnl-1`) | 0 | Felucca | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| WitchBox (`witchbox`) | 0 | SLOOP | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
| Zen-Lite (`zen-lite`) | 0 | SLOOP | – | no Felucca INFO | – | no INFO: backup off, named by identity | no package |  |  |
