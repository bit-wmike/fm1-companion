// SPDX-License-Identifier: GPL-3.0-only
// Headless check of the ChoralRoot FM-1 studio twins (fm1companion/sims/choralroot-studio/<version>/twin.wasm) through
// the shared host core (fm1companion/sims/_studio/twin-core.js): boot, the backup protocol's INFO (no editor: 0 engines,
// the backup and RESTART tags) and the identity handshake, the 34-object inventory (cr_backup.c CRB_IDS), a loop taken
// on the panel (REC, four chords, REC: it plays), SAVE held (the settings and loop slot 1 written), the whole backup GET
// and PUT into a second twin as ChoralRoot's web installer restores it (every object it lists, then RESTART), the power
// cycle that reads it in (byte-identical back, the loop plays from slot 1 with PLAY), audio non-silent and bit-identical
// across instances, a RAM fork, the screen, LEDs, USB MIDI out, the transport (st_transport_set) and the parts held
// silent (st_track_mute_set).
//   node tests/studio_twin_choralroot.mjs fm1companion/sims/choralroot-studio   -> JSON on stdout
import { readFileSync, readdirSync, existsSync } from "node:fs";
import { createHash } from "node:crypto";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const root = new URL("..", import.meta.url).pathname;
const TwinCore = require(root + "fm1companion/sims/_studio/twin-core.js");
const dir = process.argv[2];

const HDR = [0x7D, 0x46, 0x4C];
function request(t, cmd, args = []) {                // one request -> the reply's payload (same cmd); exactly one reply
  const mine = (m) => m[0] === 0xF0 && m[1] === 0x7D && m[2] === 0x46 && m[3] === 0x4C && m[4] === cmd;
  let r = t.midi([0xF0, ...HDR, cmd, ...args, 0xF7]).filter(mine);
  for (let ms = 0; !r.length && ms < 3000; ms++) { t.runMs(1); r = t.takeOut().filter(mine); }
  if (!r.length) throw new Error(`no reply to command ${cmd} args ${args.slice(0, 8)} len ${args.length}`);
  t.runMs(20);
  if (r.length + t.takeOut().filter(mine).length !== 1) throw new Error(`command ${cmd}: more than one reply`);
  return r[0].slice(5, -1);
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
const str = (a) => { const e = a.indexOf(0); return String.fromCharCode(...a.slice(0, e < 0 ? a.length : e)); };
const sha = (x) => createHash("sha256").update(x).digest("hex");
function stats(l, r) {
  let peak = 0, bad = 0;
  for (const b of [l, r]) for (const v of b) { if (!Number.isFinite(v)) bad++; else peak = Math.max(peak, Math.abs(v)); }
  return { peak, nonFinite: bad };
}
const rms = (a) => { let e = 0; for (let i = 0; i < a.l.length; i++) e += a.l[i] * a.l[i] + a.r[i] * a.r[i]; return Math.sqrt(e / (2 * a.l.length)); };
let cur = null;
const check = (ok, what) => { if (!ok) { console.error(JSON.stringify(cur)); throw new Error("FAIL: " + what); } };

// the looper (docs/LOOPER.md, Free mode): REC arms, the first chord starts the take, REC again ends it and the loop
// plays; stopped, SAVE held (the global save) writes the settings and the loop into its slot (1)
function take(t) {
  t.press(TwinCore.B.REC);
  t.runMs(200);
  let rec = false;
  for (const k of [7, 12, 14, 20]) { t.keys(1 << k); t.runMs(450); rec = rec || t.transport().rec; t.keys(0); t.runMs(50); }
  t.press(TwinCore.B.REC);
  t.runMs(300);
  const playing = t.transport().playing;
  t.transportSet(0);
  t.runMs(100);
  t.press(TwinCore.B.SAVE, 1500);
  t.runMs(1500);
  return rec && playing;
}

const results = [];
for (const v of readdirSync(dir).sort()) {
  const mf = `${dir}/${v}/manifest.json`;
  if (!existsSync(mf)) continue;
  const m = JSON.parse(readFileSync(mf, "utf8"));
  const wasm = new WebAssembly.Module(readFileSync(`${dir}/${v}/${m.wasm}`));
  const res = { version: v, firmware_version: m.firmware_version }; cur = res;
  const t = await TwinCore.create(wasm);
  res.version_export = t.version();
  check(res.version_export === "ChoralRoot " + m.firmware_version, "st_version = ChoralRoot + manifest version");
  const info = request(t, 1);
  res.info = str(info);
  res.info_tail = info.slice(info.indexOf(0) + 1);           // 0 engines .., then the tags 42 01 03, 43 01 03
  check(res.info === res.version_export && res.info_tail[0] === 0, "INFO: the version, no editor");
  res.identity_reply = t.midi([0xF0, 0x00, 0x32, 0x45, 0x00, 0x00, 0x00, 0x40, 0x7F, 0xF7])[0];
  check(res.identity_reply && res.identity_reply[0] === 0xF0 && res.identity_reply[1] === 0x00, "identity handshake");
  const inv = list(t);
  res.ids = inv.map((o) => o.id);
  check(res.ids.length === 34 && res.ids[0] === 1 && res.ids[33] === 49, "34 objects (cr_backup.c CRB_IDS)");
  res.fresh_used = inv.filter((o) => o.size).length;
  t.runMs(1000);
  res.screen_lit = Array.from(t.frame()).filter((x) => x).length;
  check(res.screen_lit > 1000, "the screen draws");
  // a root key: its chord sounds, its notes go out over USB MIDI
  t.keys(1 << 7);
  const live = t.render(t.fs / 2);
  t.keys(0); t.render(t.fs / 4);
  res.live_peak = stats(live.l, live.r).peak;
  res.live_note_ons = t.takeOut().filter((x) => (x[0] & 0xF0) === 0x90 && x[2] > 0).length;
  check(res.live_peak > 0.01 && res.live_note_ons > 0, "a key sounds and goes out over USB MIDI");
  // a loop on the panel, saved into slot 1
  res.take = take(t);
  check(res.take && !t.transport().busy, "REC records a loop that plays; the transport stops");
  const inv2 = list(t);
  const bk = Object.fromEntries(inv2.map((o) => [o.id, o.size ? get(t, o) : new Uint8Array(0)]));
  res.sizes = Object.fromEntries(inv2.filter((o) => o.size).map((o) => [o.id, o.size]));
  check(res.sizes[1] > 0 && res.sizes[40] > 0, "SAVE held: the settings and loop slot 1");
  // into a second twin as fm1backup.js restoreBackup does: every object with data the device lists (ids > 1, then the
  // settings), then RESTART
  const u = await TwinCore.create(wasm);
  res.put_rc = {};
  const order = [...inv2.filter((o) => o.id > 1), ...inv2.filter((o) => o.id === 1)];
  for (const o of order) if (bk[o.id].length) res.put_rc[o.id] = put(u, o.id, bk[o.id]);
  check(Object.values(res.put_rc).every((rc) => rc === 0), "PUT rc 0");
  const got = Object.fromEntries(list(u).map((o) => [o.id, o.size ? get(u, o) : new Uint8Array(0)]));
  res.identical = Object.fromEntries(Object.keys(bk).map((id) => [id, sha(got[id]) === sha(bk[id])]));
  check(Object.values(res.identical).every((x) => x), "every backup object GETs back byte-identical");
  // a CRC error and an unknown id (Felucca's running music 0, a sample slot 32) are refused, nothing written
  request(u, 67, [0, 40, ...u32(bk[40].length), ...u32(crc32(bk[40]) ^ 1)]);
  request(u, 67, [1, 40, ...u32(0), ...pack7(Array.from(bk[40].subarray(0, 256)))]);
  res.bad_crc = bk[40].length <= 256 ? request(u, 67, [2, 40])[2] : 2;
  request(u, 67, [3, 40]);
  res.unknown_id = [0, 32].map((id) => request(u, 67, [0, id, ...u32(16), ...u32(0)])[2]);
  check(res.bad_crc === 2 && res.unknown_id.every((rc) => rc === 1), "a CRC error and unknown objects are refused");
  res.restart_rc = request(u, 72)[0];
  check(res.restart_rc === 0, "RESTART answers");
  // the restart (the host's power cycle): a new instance on that flash reads it in
  const t2 = await TwinCore.create(wasm, { flash: u.getFlash() });
  const back = Object.fromEntries(list(t2).map((o) => [o.id, o.size ? get(t2, o) : new Uint8Array(0)]));
  res.persisted = Object.keys(res.sizes).every((id) => sha(back[id]) === sha(bk[id]));
  check(res.persisted, "the settings and the loop survive the restart");
  // two instances from that flash play the loop with PLAY: the same samples
  const song = t2.getFlash();
  const takes = [];
  for (let k = 0; k < 2; k++) {
    const d = await TwinCore.create(wasm, { flash: song });
    d.runMs(300);
    d.takeOut();
    d.press(TwinCore.B.PLAY);
    const tr = d.transport(), leds = d.leds();
    const t0 = performance.now();
    const a = d.render(4 * d.fs);
    const ms = performance.now() - t0;
    const midi = d.takeOut();
    takes.push({ playing: tr.playing && tr.busy, play_lit: !!(leds.buttons & (1 << d.ex.st_panel_button(TwinCore.B.PLAY))),
                 ...stats(a.l, a.r), hash: sha(Buffer.from(a.l.buffer)) + sha(Buffer.from(a.r.buffer)),
                 realtime: 4000 / ms, project: d.projectCurrent(), projects: d.projectUsed(0),
                 note_ons: midi.filter((x) => (x[0] & 0xF0) === 0x90 && x[2] > 0).length });
  }
  res.takes = takes;
  check(takes[0].playing && takes[0].play_lit && takes[0].peak > 0.05 && takes[0].nonFinite === 0 &&
        takes[0].note_ons > 0, "the loop plays");
  check(takes[0].hash === takes[1].hash, "deterministic");
  check(takes[0].project === 255 && !takes[0].projects, "no project slots");
  // a fork: the RAM of a playing instance continues identically in another
  const f1 = await TwinCore.create(wasm, { flash: song }); f1.runMs(300); f1.press(TwinCore.B.PLAY); f1.render(4410);
  const f2 = await TwinCore.create(wasm, { memory: f1.snapshotMemory() });
  const a1 = f1.render(44100), a2 = f2.render(44100);
  res.fork_identical = sha(Buffer.from(a1.l.buffer)) === sha(Buffer.from(a2.l.buffer));
  check(res.fork_identical, "memory fork continues identically");
  // the transport through the optional export: stop, the song (none here: the loop), restart
  res.transport_set = f1.transportSet(0); f1.runMs(50);
  res.stopped = !f1.transport().busy;
  f1.transportSet(2); f1.runMs(50);
  res.song_play = f1.transport().playing;
  f1.transportSet(3); f1.runMs(50);
  res.restart_play = f1.transport().playing;
  f1.transportSet(0); f1.runMs(50);
  check(res.transport_set && res.stopped && res.song_play && res.restart_play && !f1.transport().busy, "st_transport_set");
  // the parts held silent (the stems): the chord alone, the chord muted (the loop is the chord part's: the bass is off
  // by default), everything muted
  res.part_rms = [];
  for (const mutes of [[0, 1], [1, 0], [1, 1]]) {
    const d = await TwinCore.create(wasm, { flash: song });
    for (let k = 0; k < 2; k++) d.muteSet(k, !!mutes[k]);
    d.press(TwinCore.B.PLAY);
    res.part_rms.push(rms(d.render(3 * d.fs)));
    res.mute_read = d.ex.st_track_mute(1);
  }
  check(res.part_rms[0] > 0.01 && res.part_rms[1] < 2e-3 && res.part_rms[2] < 2e-3 && res.mute_read === 1,
        "st_track_mute_set");
  results.push(res);
}
console.log(JSON.stringify(results));
