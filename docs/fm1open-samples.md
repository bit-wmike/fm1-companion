# FM1 Open V14 – sample slots: protocol and format

Source: a clone of M-VAVE FM1 Open V14 (`m-vave/fm1_open`, commit 4c7496d): `app/transfer.c`, `app/samples.c`,
`app/instrument.h`, `app/synth/sampledrum.h`, `sdk/WebUpdate.js` (class `Library`), `sdk/Samples.js`,
`web/synth/sea_encode.h`, `sea_decode.c`, `sample_metadata.inc`. Implementation: `fm1companion/fm1open.py`,
device side for testing: `tests/fm1open_sim.py`.

## Frame (file protocol, USB-MIDI)

```
F0 7D 46('F') 4D('M') 02 cmd tok_lo tok_hi <payload> sum F7
```
* `cmd` < 64. Token 14 bits (7+7), the host counts up per request (random start – the device remembers
  the last request per port and, for a **byte-identical** repeat, sends the stored answer).
* `payload`: up to 132 bytes (8 bit), as an **LSB-first bit stream in 7-bit groups** (`value |= b << bits` …);
  remainders must be 0 and < 7 bits. Message max. 192 bytes.
* `sum`: sum of all bytes from `7D` up to and including `sum` ≡ 0 (mod 128).
* Answer: same header, `cmd | 64`, same token; the payload starts with a **status**:
  0 ok, 1 empty, `64 − r` for error r < 0 (71 = invalid −7, 72 = memory −8, 73 = tuning slot −9,
  65–70 = flash errors −1…−6, 67 = busy). Answers ≤ 96 bytes.
* Host (Workbench): 3 attempts of 1 s each with the same packet.

## Commands

| cmd | data | answer | meaning |
|---|---|---|---|
| 0 | – | 14 bytes: version (3/4), 16 slots, active slot, project size LE16 (4048/4064), clock, 8 sources | project info – used to **detect** FM1 Open |
| 21 | – | 7 bytes | tuning capability, sizes (V13+) |
| 22 | – | 9 bytes: 1, 128, 4, bank lib, **sample capacity LE32 (446464)**, flags | V14 capabilities; missing → capacity 462848 |
| 11 | – | status, size LE32, region LE32 (=466944) | sample status: 0 no bank, 1 checking, 2 ready, 3 receiving, 4 error |
| 12 | size LE32 | – | begin: **erases the first sector immediately** (the old bank is gone), state 3 |
| 13 | offset LE32 + 1…128 bytes | – | write, offsets contiguous and ascending |
| 14 | – | – | finish: checks header, CRC, SEA5 block headers, sets the commit word |
| 15 | offset LE32 + n (≤ 64) | n bytes | read (state 2 only) |
| 16 | – | – | abort (state 3 → 4) |

The device aborts a session after 10 s without data (state 4).

## Bank (one for all 8 pads, up to 446 464 bytes ≈ 19.55 s)

Header 512 bytes, little endian: `magic "FM1S" (0x53314D46)`, `version 1`, `bytes` (total size), `crc`,
`commit` (0), `rate 32000`, 2× reserved 0, then 16 entries × 28 bytes `{offset, frames, bytes, name[16]}`,
32 bytes 0. Entries contiguous from 512; `bytes = ceil(frames/320)·228`; name printable ASCII, max. 15 characters;
entries 9–16 empty (offset = end). CRC = CRC-32 (zlib) over the whole bank with bytes 12–19 as zero.

Pads → MIDI notes: 36, 38, 37, 41, 47, 48, 42, 46 (kick, snare, rim, toms, hi-hats following GM).

## SEA5

Mono, 32 kHz, block = 320 frames = 228 bytes: `01 45 14 5A`, 8 × int16 start state (4 history, 4 LMS weights),
16 scale nibbles (20 frames each), 200 bytes of 5-bit codes (MSB first). Encoder: for each group try all 16 scales
(starting at the previous one), error² + weight penalty, the best one wins; the state carries across block boundaries.
The Python port is **byte-exact** to the C reference (the test compiles the original sources with clang/gcc).
The Workbench converts WAV with the browser (`decodeAudioData` @ 32 kHz) – this resampling is not
reproduced; the app uses its own low-pass + interpolation (numpy). From int16 on, the bytes are identical.

## Safety in the app

1. "Read & back up": read the whole bank, store it as `.fm1s` (same format as the Workbench's
   "备份网页采样库" – back up web sample library) under Backups/<device>.
2. Write only after a confirmation that names which slots will be replaced; before that the bank is read again,
   compared with the state at the time of reading (otherwise abort) and backed up once more.
3. The bank is fully checked before erasing; after writing, everything is read back and compared byte for
   byte. On abort/error: restore the backup ("Restore backup …").

Not tested on a real device – only against `tests/fm1open_sim.py` (modelled on transfer.c/samples.c).
