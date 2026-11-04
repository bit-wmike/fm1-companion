# Melodee 1.6 project format (backup objects 0, 2–5, 64–91)

Derived from Melodee **1.6.0-rc.7** sources (`firmware/src/project.c`, `pattern_store.c`, `project_stream.c`,
`step_snapshot.h`, `recorded_note.h`, `song_chain.c`, `mix.c`) and **verified against bytes written by that
firmware's own code**: `tools/melodee_fixtures/fixturegen.c` compiles the real firmware sources on the host
(via Melodee's `tests/backup_test.c` harness), builds music in RAM, saves it with `project_save_as` and reads the
objects back through the real `BACKUP_LIST`/`BACKUP_GET` handler. Output: `tests/fixtures/musik/obj_*.bin`,
`expected.json` (RAM state), `layout.json` (every constant below, printed by the C compiler) and `tables.json`
(parameter/engine/preset tables, shipped as `fm1companion/data/melodee_tables.json`). Melodee's own legacy
fixtures (`fx/fbkk.bin` = FBKK/FUN16, `fx/fun18.bin`) decode too. Decoder: `fm1companion/projectfmt.py`.

All integers little endian. Sizes for rc.7: NTRK 8 (4 parts + 4 MIDI-only tracks), NPAT 16 per part,
8 per MIDI-only track (older containers: 8 per track), NSTEP 64, P_COUNT 124, P_E0 116, G_COUNT 45.

**Important:** rc.7 has **16 patterns per part** (INFO tag `50 04 10 40`, PATTERN reply count 16), not 8.

## 1. Object = HPJ1 compact image (what a backup holds)

| off | size | field |
|---|---|---|
| 0 | 4 | magic `HPJ1` (0x314A5048) |
| 4 | 4 | logical length = 27904 (BANK_STORE_SIZE) |
| 8 | 16 | 128-bit page map: bits 0..57 = tail pages, bits 64..115 = head pages |
| 24 | 8 | reserved |
| 32 | … | present tail pages (region T = record[13220:27904], 256 B pages, last page 92 B), then present head pages (region S = record[0:13220], last page 164 B) |

Missing pages are zero. The last tail page is always stored (it holds the hash). A raw record (FBK magic first,
27904 bytes) is also accepted. Older firmware wrote other sizes (see §6).

## 2. Bank container (27904 bytes)

| off | size | field |
|---|---|---|
| 0 | 4 | magic: FBKK `4B4B4246`, FBKL `4C…`, FBKM `4D…`, FBKN `4E…`, FBKO `4F…`, **FBKP** `504B4246` (rc.7 default), FBKQ `514B4246` (+ per-pattern sounds) |
| 4 | 4 | 27904 |
| 8 | 13212 | FUN record (§3): sounds, globals and each part's **active** pattern |
| 13220 | 14680 | tail (§4): other patterns, timing, song, MIDI-only tracks |
| 27900 | 4 | FNV-1a 32 (offset 2166136261, prime 16777619) over bytes 0..27899 |

## 3. FUN record (FUN22 `INUF` = 0x46554E49; FUN16..21 = 0x43..0x48 read the same way)

| off | size | field |
|---|---|---|
| 0 | 4 | magic; 4: size 13212 |
| 8 | 54 | globals 0..26 as int16 (0 BPM, 1 SWING, 2 CLK, 3 TUNE …) |
| 16 | 4 | `M F X` + FX return (overlays retired globals 4–5) |
| 20 | 12 | shared FX slots 1–3: TYPE, MIX, RATE, CHAR |
| 56 | 1 | FX mode |
| 62 | 1 | selected track; 63: tracks in use (1..8); 64: phys flag |
| 65, 67 | 2 | active bank per part: bits 3k..3k+2 (low 3 bits), bit 12+k (bit 3) |
| 66 | 1 | np = P_COUNT stored (122/124 FUN22; 104 FUN16, 108 FUN17, 116 FUN18, 117 FUN19, 119 FUN20, 125 FUN21) |
| 68 | 4 × (np + 2 + 64·8) | per part: np params as value+64 (first np−8 = ids 0.., last 8 = P_E0..P_E7), engine, preset, 64 steps (8 B, §5; FUN16/17: 9 B) |
| 2620 | 36 | legacy song: count, 3 rsv, 16 × (slot, repeat) |
| 2656 | 260 | motion: count, on mask, 2 rsv, 64 × (place = track<<6 \| step, param, int16 value) |
| 2916 | 10 | `U` 3, 4 × uint16 shared-library origin (slot + 1, 0 = none) |
| 3096 | 2 | MPC octave word (0xA000 \| (oct+3) << 3k) |
| 3098 | 3 | shared FX TONE + 64 |
| 3104 | 512 | 4 × 128-byte packed FM6 voice |
| 3616 | 576 | 4 × 144-byte CZ-1 tone |
| 4192 | 8192 | 1024 timed notes, 8 B each, grouped owner by owner (§5) |
| 12384 | 552 | 4 × 138-byte Prophet record |
| 12936 | 216 | 4 × 54-byte drum patch |
| 13152 | 44 | timed-note counts, 32 × 11 bit (FBKK/FBKL only; FBKP keeps all counts in the tail) |
| 13196 | 12 | project name, ASCII 32..126, 0-padded |
| 13208 | 4 | FNV-1a over 0..13207 |

Track parameter ids used by the app: 0 LEVEL, 17 ARP MODE, 25 ROOT, 26 SCL, 29 LEN, 30 DIV (1/4 1/8 1/16 1/32 8T
16T 1/2 1/1 2BAR 4BAR), 31 SWG, 32 GATE (1..127, gate = step × GATE/128), 37 VOICE, 39 PAN (−64..63), 40 MUTE,
81 CHRD, 116 (MIDI track) CH.

## 4. Tail (offsets relative to record byte 13220)

FBKP/FBKQ (`L` = 16 banks, 2 mask bytes) — FBKL..FBKO in brackets (8 banks, 1 mask byte):

| tail off | field |
|---|---|
| 0 | active bank per track (8 bytes) |
| 8 | timing: (track × L + bank) × 4: LEN, DIV, SWING (int8), GATE |
| 520 [264] | legacy song columns 16 × 8 |
| 648 [392] | motion bank tags 64 |
| 712 [456] | `FN61` + 4 × 16 FM6 function settings |
| 780 [524] | timed-note counts, 11 bit each: FBKP all 128 owners (track × 16 + bank); FBKL owners past the first 32 |
| 956 [568] | MIDI-only tracks 4 × 125: params + 64, MPC octave + 3 |
| 1456 [1068] | song (FBKM+): count, loop, sounds-follow, 0, 64 × (repeat, 8 banks). FBKP: bank 16 = REST, 17 = HOLD (older: 8/9) |
| +580 | punch FX (FBKN+): 64 × 4 × (fx, start, length) = 768 |
| +768 | if sounds: count, P_COUNT, uint16 size (302), 64 × 8 sound ids, count × 302-byte snapshot |
| … | FBKQ: 8 × 16 pattern sound ids |
| … | per track: bank mask (2 B); per present bank: 64-bit step mask, then the set steps (8 B each). A part's active bank is never here (it is in the FUN record) |

FBKK (dense, parts only): tail 4: 4 parts × 7 non-active banks × 64 × 8 B; timing at 14340, song columns at 14468
(16 × 4), motion tags 14532, FN61 14596.

## 5. Step and timed note bit layouts (LSB first)

8-byte step (`step_packed_put` / `proj_step_pack`): note0..3 7 bits each; meta 4 bits (0..4 = NOTE with n notes,
5 TIE, 6 REST, bit 3 = recorded overview); flags 2 (1 accent, 2 slide); vel 7; hit 8 (lane bits KICK SNARE CLAP
HAT-CL HAT-OP TOM RIM BELL); acc 8 (lane accents, or recorded gate 1..255 without hits); probability 7
(0 = 100 %, 1..100, 101 = never). 9-byte step (FUN7/8/16/17): 4 notes, n|time<<3|flags<<5|rec<<7, vel, hit, acc,
probability.

Timed note (8 B): onset 16 (1/65536 of the swung step), duration 16, note 7, vel 7, step 8 (low 6 bits = step),
exponent 3 (duration × 2^exp / 65536 steps), pitch 6 (+24), gate 1.

## 6. Other formats

FUN7 (3388) / FUN8 (3584) / FBK9 (20224) are read after `web/EDITOR_PROTOCOL.md` (not verified against code).
Templates (TPL*), SLOOP/Skiff objects and anything else decode as "unknown format" / "other firmware"
without raising.

## 7. Time model used by the MIDI export

Step = DIV in quarters; swing s = clamp(pattern SWG + global SWG, 0, 100): even steps base + s·base/250, odd
steps base − s·base/250. Song section = repeat × duration of track 1's pattern (REST/HOLD on track 1: one 4/4 bar
per repeat); other tracks loop their pattern from the section start; HOLD continues the phase. Not computed: ARP,
chord keys, strum, chance (all steps exported), motion.
