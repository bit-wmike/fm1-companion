// SPDX-License-Identifier: GPL-3.0-only
// Headless check of the Felucca studio twins (fm1companion/sims/felucca-studio/<version>/twin.wasm) through the shared
// host core (fm1companion/sims/_studio/twin-core.js): boot, editor INFO ("FELUCCA v1.8.1") and identity (FM-1_918), the
// 13-object backup inventory (editor_backup.c ED_BK_IDS: 0..9, the sample slots 32..34), a song written through the
// editor (TRACK_STEP on all four tracks, PROJECT save into slot A), a user sample uploaded into USR1 (SMP_BEGIN / WRITE /
// END: the flash hooks), the whole backup GET and PUT into a second twin as Felucca's web/fm1backup.js restores it (the
// samples through SMP_*, byte-identical back), flash persistence across a "power cycle", project load + PLAY on the
// panel, audio non-silent and bit-identical across instances, a RAM fork, the screen, LEDs, USB MIDI out, stop
// (st_transport_set and the editor's SONG command).
//   node tests/studio_twin_felucca.mjs fm1companion/sims/felucca-studio   -> JSON on stdout
import { readFileSync, readdirSync, existsSync } from "node:fs";
import { createHash } from "node:crypto";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const root = new URL("..", import.meta.url).pathname;
const TwinCore = require(root + "fm1companion/sims/_studio/twin-core.js");
const dir = process.argv[2];

const HDR = [0x7D, 0x46, 0x4C];
function request(t, cmd, args = []) {                // one editor request -> the reply's payload (same cmd)
  const mine = (m) => m[0] === 0xF0 && m[1] === 0x7D && m[2] === 0x46 && m[3] === 0x4C && m[4] === cmd;
  let r = t.midi([0xF0, ...HDR, cmd, ...args, 0xF7]).find(mine);
  for (let ms = 0; !r && ms < 3000; ms++) { t.runMs(1); r = t.takeOut().find(mine); }
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
// a valid user sample slot (eng_sample.c smp_user_hdr_t: "FSMP", version 1, one zone over 1000 bytes of IMA ADPCM)
function sample() {
  const data = Uint8Array.from({ length: 1000 }, (_, i) => (i * 37 + 11) & 255);
  const b = new Uint8Array(512 + data.length), v = new DataView(b.buffer);
  v.setUint32(0, 0x504D5346, true); v.setUint16(4, 1, true); b[6] = 1;
  b.set([...("TWIN")].map((c) => c.charCodeAt(0)), 8);
  v.setUint32(16, data.length, true); v.setUint32(20, crc32(data), true);
  const z = 32;                                       // smp_zone_t: off n ls le rate root16 pred idx lo hi looped
  v.setUint32(z, 0, true); v.setUint32(z + 4, 2000, true); v.setUint32(z + 8, 0, true); v.setUint32(z + 12, 1999, true);
  v.setUint32(z + 16, 65536, true); v.setInt16(z + 20, 60 * 16, true); b[z + 25] = 0; b[z + 26] = 127;
  b.set(data, 512);
  return b;
}
function uploadSample(t, slot, b) {                   // web/fm1backup.js restoreBackup's sample path (11, 12, 13)
  const rc = [request(t, 11, [slot]).at(-1)];
  for (let off = 512; off < b.length; off += 256)
    rc.push(request(t, 12, [slot, off & 127, off >> 7 & 127, off >> 14 & 127, ...pack7(Array.from(b.subarray(off, off + 256)))]).at(-1));
  rc.push(request(t, 13, [slot, ...pack7(Array.from(b.subarray(0, 480)))]).at(-1));
  return rc;
}
const str = (a) => { const e = a.indexOf(0); return String.fromCharCode(...a.slice(0, e < 0 ? a.length : e)); };
const sha = (x) => createHash("sha256").update(x).digest("hex");
function stats(l, r) {
  let peak = 0, bad = 0;
  for (const b of [l, r]) for (const v of b) { if (!Number.isFinite(v)) bad++; else peak = Math.max(peak, Math.abs(v)); }
  return { peak, nonFinite: bad };
}
let cur = null;
const check = (ok, what) => { if (!ok) { console.error(JSON.stringify(cur)); throw new Error("FAIL: " + what); } };
const NOTE = (k, i) => 40 + 3 * k + (i % 5);

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
  const inv = list(t);
  res.ids = inv.map((o) => o.id);
  res.empty_sizes = Object.fromEntries(inv.filter((o) => o.size).map((o) => [o.id, o.size]));
  // the screen after the splash: drawn, not black; the HOME page
  t.runMs(1000);
  res.screen_lit = Array.from(t.frame()).filter((x) => x).length;
  // a song through the editor: steps on all four tracks, saved as project A
  for (let k = 0; k < 4; k++)
    for (let i = 0; i < 16; i += 2 + (k % 3)) {
      const a = request(t, 30, [k, i, 1, NOTE(k, i), 0, 0, 0, 0, 0, 100]);
      check(a[0] === k && a[1] === i && a[3] === NOTE(k, i), "TRACK_STEP written");
    }
  res.project_save = request(t, 9, [1, 0]);
  check(res.project_save[2] === 1, "PROJECT save reports project A used");
  // a user sample into USR1 as Felucca's editor uploads it (SMP_BEGIN, the data from +512, SMP_END with the header)
  const SMP = sample();
  res.smp_rc = uploadSample(t, 0, SMP);
  check(res.smp_rc.every((rc) => rc === 0), "sample upload rc 0");
  // the whole backup as the editor reads it, then into a second twin as the editor restores it (the running music last)
  const inv2 = list(t);
  check(inv2.find((o) => o.id === 32).size === SMP.length, "USR1 listed with the sample's size");
  const bk = Object.fromEntries(inv2.map((o) => [o.id, o.size ? get(t, o) : new Uint8Array(0)]));
  res.sizes = Object.fromEntries(inv2.filter((o) => o.size).map((o) => [o.id, o.size]));
  const u = await TwinCore.create(wasm);
  const order = inv2.map((o) => o.id).filter((id) => id > 1).concat([1, 0]);   // (web/fm1backup.js restoreBackup)
  res.put_rc = {};
  for (const id of order)
    if (id >= 32) { if (bk[id].length) res.put_rc[id] = uploadSample(u, id - 32, bk[id]).find((rc) => rc) || 0; }
    else if (bk[id].length) res.put_rc[id] = put(u, id, bk[id]);
  check(Object.values(res.put_rc).every((rc) => rc === 0), "PUT rc 0");
  const back = Object.fromEntries(list(u).map((o) => [o.id, o.size ? get(u, o) : new Uint8Array(0)]));
  res.identical = Object.fromEntries(Object.keys(bk).map((id) => [id, sha(back[id]) === sha(bk[id])]));
  check(Object.values(res.identical).every((x) => x), "every backup object GETs back byte-identical");
  // the running music: track 1 step 1 as written
  const step = request(u, 30, [0, 0]);
  res.live_step0 = { note: step[3], vel: step[9] };
  // power cycle: the flash image into a new instance
  const t2 = await TwinCore.create(wasm, { flash: u.getFlash() });
  const inv3 = list(t2);
  res.persisted = sha(get(t2, inv3.find((o) => o.id === 2))) === sha(bk[2]) && sha(get(t2, inv3.find((o) => o.id === 32))) === sha(bk[32]);
  check(res.persisted, "project A and USR1 survive the power cycle");
  res.projects_used = [...Array(32).keys()].filter((k) => t2.projectUsed(k));
  // two instances from that flash play project A: the same samples
  const song = t.getFlash();
  const takes = [];
  for (let k = 0; k < 2; k++) {
    const d = await TwinCore.create(wasm, { flash: song });
    d.runMs(600);
    const rc = d.projectLoad(0);
    d.runMs(100);
    d.takeOut();
    d.press(TwinCore.B.PLAY);
    const tr = d.transport(), leds = d.leds();
    const t0 = performance.now();
    const a = d.render(4 * d.fs);
    const ms = performance.now() - t0;
    const midi = d.takeOut();
    takes.push({ rc, playing: tr.playing || tr.busy, play_lit: !!(leds.buttons & (1 << d.ex.st_panel_button(TwinCore.B.PLAY))),
                 ...stats(a.l, a.r), hash: sha(Buffer.from(a.l.buffer)) + sha(Buffer.from(a.r.buffer)),
                 realtime: 4000 / ms, current: d.projectCurrent(),
                 note_ons: midi.filter((x) => (x[0] & 0xF0) === 0x90 && x[2] > 0).length });
  }
  res.takes = takes;
  check(takes[0].rc === 0 && takes[0].playing, "project A loads and plays");
  check(takes[0].peak > 0.01 && takes[0].nonFinite === 0, "audio non-silent");
  check(takes[0].hash === takes[1].hash, "deterministic");
  // a fork: the RAM of a playing instance continues identically in another
  const f1 = await TwinCore.create(wasm, { flash: song }); f1.runMs(600); f1.projectLoad(0); f1.press(TwinCore.B.PLAY); f1.render(4410);
  const f2 = await TwinCore.create(wasm, { memory: f1.snapshotMemory() });
  const a1 = f1.render(44100), a2 = f2.render(44100);
  res.fork_identical = sha(Buffer.from(a1.l.buffer)) === sha(Buffer.from(a2.l.buffer));
  check(res.fork_identical, "memory fork continues identically");
  // stop through the optional export
  res.transport_set = f1.transportSet(0); f1.runMs(50);
  res.stopped = !f1.transport().busy;
  check(res.transport_set && res.stopped, "st_transport_set stops");
  // the song from the top (st_transport_set 2: the SONG chain's rows, as the editor's SONG op 2), then the editor's SONG
  // op 3 (33: 3 stop) stops it
  f1.transportSet(2); f1.runMs(50);
  res.song_play = f1.transport().playing;
  request(f1, 33, [3]); f1.runMs(50);
  res.editor_stop = !f1.transport().busy;
  check(res.song_play && res.editor_stop, "the song plays, the editor's SONG op 3 stops it");
  results.push(res);
}
console.log(JSON.stringify(results));
