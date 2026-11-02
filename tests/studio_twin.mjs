// SPDX-License-Identifier: GPL-3.0-only
// Headless check of the studio twins (fm1companion/sims/<firmware>-studio/<version>/twin.wasm) through the shared
// host core (fm1companion/sims/_studio/twin-core.js): boot, editor identity, full backup GET / PUT with Melodee's
// own fixture objects (tests/fixtures/musik/obj_*.bin, written by the firmware), flash persistence across a
// "power cycle", project load + PLAY on the panel, audio non-silent and bit-identical across instances, speed.
//   node tests/studio_twin.mjs fm1companion/sims/melodee-studio tests/fixtures/musik   -> JSON on stdout
import { readFileSync, readdirSync, existsSync } from "node:fs";
import { createHash } from "node:crypto";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const root = new URL("..", import.meta.url).pathname;
const TwinCore = require(root + "fm1companion/sims/_studio/twin-core.js");
const dir = process.argv[2], fixtures = process.argv[3];

const HDR = [0x7D, 0x46, 0x4C];
function request(t, cmd, args = []) {                // one editor request -> the reply's payload (same cmd)
  const mine = (m) => m[0] === 0xF0 && m[1] === 0x7D && m[2] === 0x46 && m[3] === 0x4C && m[4] === cmd;
  let r = t.midi([0xF0, ...HDR, cmd, ...args, 0xF7]).find(mine);
  for (let ms = 0; !r && ms < 3000; ms++) { t.runMs(1); r = t.takeOut().find(mine); }   // a reply may need time
  if (!r) throw new Error(`no reply to command ${cmd} args ${args.slice(0, 8)} len ${args.length}`);
  return r.slice(5, -1);
}
const u32 = (n) => [0, 1, 2, 3, 4].map((i) => (n >>> (7 * i)) & (i === 4 ? 15 : 127));
const r32 = (a, o) => (a[o] | a[o + 1] << 7 | a[o + 2] << 14 | a[o + 3] << 21 | a[o + 4] << 28) >>> 0;
const CRC_T = Array.from({ length: 256 }, (_, n) => { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1; return c >>> 0; });
const crc32 = (b) => { let c = 0xFFFFFFFF; for (const x of b) c = CRC_T[(c ^ x) & 255] ^ (c >>> 8); return (c ^ 0xFFFFFFFF) >>> 0; };
function pack7(b) { const o = []; for (let i = 0; i < b.length; i += 7) { const g = b.slice(i, i + 7); o.push(g.reduce((m, x, j) => m | ((x >> 7) & 1) << j, 0)); for (const x of g) o.push(x & 127); } return o; }
function unpack7(a, n) { const o = []; let i = 0; while (o.length < n) { const k = Math.min(7, n - o.length), m = a[i++]; for (let j = 0; j < k; j++) o.push(a[i++] | ((m >> j) & 1) << 7); } return Uint8Array.from(o); }

function list(t) {
  const a = request(t, 65);
  if (a[0] !== 1 || a[1] !== 0) throw new Error("BACKUP_LIST refused");
  const out = [];
  for (let i = 0; i < a[2]; i++) { const p = 3 + i * 11; out.push({ id: a[p], size: r32(a, p + 1), crc: r32(a, p + 6) }); }
  return out;
}
function get(t, o) {
  const buf = new Uint8Array(o.size);
  for (let off = 0; off < o.size; off += 256) {
    const n = Math.min(256, o.size - off), a = request(t, 66, [o.id, ...u32(off), n & 127, n >> 7]);
    if (a[1] !== 0) throw new Error(`GET ${o.id} rc ${a[1]}`);
    buf.set(unpack7(a.slice(9), n), off);
  }
  if (crc32(buf) !== o.crc) throw new Error(`GET ${o.id}: CRC`);
  return buf;
}
function put(t, id, data) {
  let a = request(t, 67, [0, id, ...u32(data.length), ...u32(crc32(data))]);
  if (a[2]) return a[2];
  for (let off = 0; off < data.length; off += 256) {
    a = request(t, 67, [1, id, ...u32(off), ...pack7(Array.from(data.subarray(off, off + 256)))]);
    if (a[2]) { request(t, 67, [3, id]); return a[2]; }
  }
  return request(t, 67, [2, id])[2];
}
const str = (a) => { let e = a.indexOf(0); return String.fromCharCode(...a.slice(0, e < 0 ? a.length : e)); };
const sha = (x) => createHash("sha256").update(x).digest("hex");
function stats(l, r) {
  let peak = 0, sum = 0, bad = 0;
  for (const b of [l, r]) for (const v of b) { if (!Number.isFinite(v)) bad++; else { peak = Math.max(peak, Math.abs(v)); sum += v * v; } }
  return { peak, rms: Math.sqrt(sum / (2 * l.length)), nonFinite: bad };
}
let cur = null;
const check = (ok, what) => { if (!ok) { console.error(JSON.stringify(cur)); throw new Error("FAIL: " + what); } };

const results = [];
for (const v of readdirSync(dir).sort()) {
  const mf = `${dir}/${v}/manifest.json`;
  if (!existsSync(mf)) continue;
  const m = JSON.parse(readFileSync(mf, "utf8"));
  const wasm = new WebAssembly.Module(readFileSync(`${dir}/${v}/${m.wasm}`));
  const res = { version: v, firmware_version: m.firmware_version }; cur = res;
  const t = await TwinCore.create(wasm);
  res.version_export = t.version();
  check(res.version_export === m.firmware_version, "st_version = manifest");
  res.info = str(request(t, 1));
  res.ping = request(t, 25);
  res.identity_reply = t.midi([0xF0, 0x00, 0x32, 0x45, 0x00, 0x00, 0x00, 0x40, 0x7F, 0xF7])[0];
  // a fresh device: the whole backup as the editor reads it
  const inv = list(t);
  res.objects = inv.length;
  const empty = Object.fromEntries(inv.filter((o) => o.size).map((o) => [o.id, get(t, o)]));
  res.empty_sizes = Object.fromEntries(Object.entries(empty).map(([k, b]) => [k, b.length]));
  // Melodee's own objects (written by the firmware): projects 1 and 5, then the running music (last, as the editor)
  const fx = {};
  for (const id of [2, 64, 0]) fx[id] = new Uint8Array(readFileSync(`${fixtures}/obj_${id}.bin`));
  res.put_rc = {};
  for (const id of [2, 64, 0]) res.put_rc[id] = put(t, id, fx[id]);
  check(Object.values(res.put_rc).every((rc) => rc === 0), "PUT rc 0");
  const back = Object.fromEntries(list(t).filter((o) => [0, 2, 64].includes(o.id)).map((o) => [o.id, get(t, o)]));
  if (process.env.DUMP0) { (await import("node:fs")).writeFileSync(process.env.DUMP0, back[0]); }
  res.identical = Object.fromEntries([2, 64, 0].map((id) => [id, sha(back[id]) === sha(fx[id])]));
  check(res.identical[2] && res.identical[64], "saved projects GET back byte-identical");
  // the running music is rebuilt from RAM on every GET (the firmware normalises a loaded project): what a twin
  // gives back, another twin takes and gives back byte for byte (a backup of the twin restores identically)
  {
    const t3 = await TwinCore.create(wasm);
    check(put(t3, 0, back[0]) === 0, "PUT running music into a second twin");
    const again = get(t3, list(t3).find((o) => o.id === 0));
    res.running_music_roundtrip = sha(again) === sha(back[0]);
    check(res.running_music_roundtrip, "running music round trip twin -> twin byte-identical");
  }
  // what the running music holds (expected.json "live"): track 1 step 1
  const step = request(t, 30, [0, 0]);
  res.live_step0 = { note: step[3], vel: step[9] };
  res.bpm = request(t, 2, [1, 0]);
  // power cycle: the flash image into a new instance
  const flash = t.getFlash();
  const t2 = await TwinCore.create(wasm, { flash });
  const back2 = Object.fromEntries(list(t2).filter((o) => [2, 64].includes(o.id)).map((o) => [o.id, get(t2, o)]));
  res.persisted = sha(back2[2]) === sha(fx[2]) && sha(back2[64]) === sha(fx[64]);
  check(res.persisted, "projects survive the power cycle");
  res.projects_used = [...Array(32).keys()].filter((k) => t2.projectUsed(k));
  // a song with sound: the power-on sounds of tracks 1..4, steps written through TRACK_STEP, saved as project 1
  // with the editor's PROJECT save; then two instances from that flash play it: the same samples
  const w = await TwinCore.create(wasm);
  for (let k = 0; k < 4; k++)
    for (let i = 0; i < 16; i += 2 + k) {
      const a = request(w, 30, [k, i, 1, 36 + 12 * (k % 3) + (i % 5), 0, 0, 0, 0, 0, 100]);
      check(a[0] === k && a[1] === i && a[3] === 36 + 12 * (k % 3) + (i % 5), "TRACK_STEP written");
    }
  res.project_save = request(w, 9, [1, 0]);
  check(res.project_save[2] === 1, "PROJECT save reports slot 1 used");
  const song = w.getFlash();
  const takes = [];
  for (let k = 0; k < 2; k++) {
    const d = await TwinCore.create(wasm, { flash: song });
    d.runMs(300);
    const rc = d.projectLoad(0);
    d.runMs(100);
    d.press(TwinCore.B.PLAY);
    const tr = d.transport();
    const t0 = performance.now();
    const a = d.render(4 * d.fs);
    const ms = performance.now() - t0;
    takes.push({ rc, playing: tr.playing || tr.busy, ...stats(a.l, a.r), hash: sha(Buffer.from(a.l.buffer)) + sha(Buffer.from(a.r.buffer)), realtime: 4000 / ms, framesPerSec: 4 * d.fs / (ms / 1000), current: d.projectCurrent() });
  }
  res.takes = takes;
  check(takes[0].rc === 0 && takes[0].playing, "project 1 loads and plays");
  check(takes[0].peak > 0.01 && takes[0].nonFinite === 0, "audio non-silent");
  check(takes[0].hash === takes[1].hash, "deterministic");

  // a fork: the RAM of a playing instance continues identically in another
  const f1 = await TwinCore.create(wasm, { flash: song }); f1.projectLoad(0); f1.press(TwinCore.B.PLAY); f1.render(4410);
  const f2 = await TwinCore.create(wasm, { memory: f1.snapshotMemory() });
  const a1 = f1.render(44100), a2 = f2.render(44100);
  res.fork_identical = sha(Buffer.from(a1.l.buffer)) === sha(Buffer.from(a2.l.buffer));
  check(res.fork_identical, "memory fork continues identically");
  // stop: the editor's song stop (BANK_SONG 3 = transport_req 2), then the transport is idle
  request(f1, 74, [3]); f1.runMs(50);
  res.stopped = !f1.transport().busy;
  results.push(res);
}
console.log(JSON.stringify(results));
