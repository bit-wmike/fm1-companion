// SPDX-License-Identifier: GPL-3.0-only
// How tests/fixtures/musik/sloop285_project.bin was made: SLOOP 2.8.5's own firmware code, the Studio twin bundled with
// the app (fm1companion/sims/sloop-studio/2.8.5, isod89/sloop-fm1 v2.8.5 built unmodified with
// tools/studio/sloop_studio.c), is given steps, drum hits, a lock, a nudge, a fill condition and pattern settings over
// SLOOP's editor protocol; then the running project (backup object 0, BK_GET 35: what a sloop-backup holds) is read.
//   node tests/fixtures/musik/make_sloop2_fixture.mjs OUT.bin
import { readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const root = new URL("../../..", import.meta.url).pathname;
const TwinCore = require(root + "fm1companion/sims/_studio/twin-core.js");
const dir = root + "fm1companion/sims/sloop-studio/2.8.5";
const m = JSON.parse(readFileSync(dir + "/manifest.json", "utf8"));
const t = await TwinCore.create(new WebAssembly.Module(readFileSync(dir + "/" + m.wasm)));
function request(cmd, args = []) {
  const mine = (x) => x[0] === 0xF0 && x[1] === 0x7D && x[2] === 0x46 && x[3] === 0x4C && x[4] === cmd;
  let r = t.midi([0xF0, 0x7D, 0x46, 0x4C, cmd, ...args, 0xF7]).find(mine);
  for (let ms = 0; !r && ms < 3000; ms++) { t.runMs(1); r = t.takeOut().find(mine); }
  if (!r) throw new Error("no reply to " + cmd);
  return r.slice(5, -1);
}
const v14 = (v) => [(v + 8192) & 127, (v + 8192) >> 7];
const g7 = (v, n) => Array.from({ length: n }, (_, i) => (v >>> (7 * i)) & 127);
const r32 = (a, o) => (a[o] | a[o + 1] << 7 | a[o + 2] << 14 | a[o + 3] << 21 | a[o + 4] << 28) >>> 0;
const u32 = (n) => [0, 1, 2, 3, 4].map((i) => (n >>> (7 * i)) & (i === 4 ? 15 : 127));
function unpack7(a, n) { const o = []; let i = 0; while (o.length < n) { const k = Math.min(7, n - o.length), mm = a[i++]; for (let j = 0; j < k; j++) o.push(a[i++] | ((mm >> j) & 1) << 7); } return Uint8Array.from(o); }
t.runMs(1000);
request(3, [1, 0, ...v14(128)]);                        // GLO BPM 128
request(3, [1, 1, ...v14(10)]);                         // GLO SWING 10
// track 1: a line with a tie, an accent, a slide, a chord; LEN 32, SWG 20, GATE 96
const st = (n, notes, time, flags, vel) => [n, ...[...notes, 0, 0, 0, 0].slice(0, 4), time, flags, vel, 0, 0, 0];
request(30, [0, 0, ...st(1, [48], 0, 0, 100)]);
request(30, [0, 1, ...st(0, [], 1, 0, 0)]);             // TIE
request(30, [0, 4, ...st(1, [51], 0, 1, 110)]);          // ACCENT
request(30, [0, 8, ...st(1, [53], 0, 2, 90)]);           // SLIDE
request(30, [0, 9, ...st(1, [55], 0, 0, 90)]);
request(30, [0, 16, ...st(3, [60, 64, 67], 0, 0, 80)]);  // a chord
request(30, [0, 24, ...st(1, [43], 0, 0, 100)]);
for (const [id, v] of [[29, 32], [31, 20], [32, 96]]) request(31, [0, id, ...v14(v)]);
// track 2: DIV 1/8, 8 steps
for (let i = 0; i < 8; i += 2) request(30, [1, i, ...st(1, [72 + i], 0, 0, 100)]);
for (const [id, v] of [[29, 8], [30, 1]]) request(31, [1, id, ...v14(v)]);
// track 3: 8T (never swung)
request(30, [2, 0, ...st(1, [36], 0, 0, 100)]); request(30, [2, 3, ...st(1, [38], 0, 0, 100)]);
request(31, [2, 30, ...v14(4)]); request(31, [2, 29, ...v14(6)]);
// the drums: kick on the beats, snare (lane 2) on 2 and 4, a hat (lane 4) on the off-beats
for (let i = 0; i < 16; i++) {
  const on = (i % 4 === 0 ? 1 : 0) | (i % 8 === 4 ? 4 : 0) | (i % 4 === 2 ? 16 : 0);
  if (on) request(33, [i, ...g7(on, 3), ...g7(0, 5), ...g7(0, 5)]);
}
request(38, [0, 4, 0, ...v14(90)]);                      // LOCK_SET: track 1, step 5, P_LEVEL 90
request(40, [0, 9, 64 + 16]);                           // MICRO_SET: track 1, step 10, 16/64 late
request(42, [0, 24, 1]);                                // FILL_SET: track 1, step 25 only in a fill
t.runMs(200);
const a = request(34);
let o = null;
for (let i = 0, p = 2; i < a[1]; i++, p += 11) if (a[p] === 0) o = { size: r32(a, p + 1) };
const buf = new Uint8Array(o.size);
for (let off = 0; off < o.size; off += 256) {
  const n = Math.min(256, o.size - off), g = request(35, [0, ...u32(off), n & 127, n >> 7]);
  buf.set(unpack7(g.slice(9), n), off);
}
writeFileSync(process.argv[2], buf);
console.log(o.size, Buffer.from(buf.slice(0, 4)).toString());
