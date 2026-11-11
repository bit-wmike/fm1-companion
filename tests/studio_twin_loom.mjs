// SPDX-License-Identifier: GPL-3.0-only
// Headless check of the LOOM-8 studio twins (fm1companion/sims/loom-8-studio/<version>/twin.wasm, on fm1-base) through the
// shared host core (fm1companion/sims/_studio/twin-core.js): boot ("LOOM-8 0.14": fm1-base has no editor INFO), the
// identity handshake (felucca-bsp's ota.c), PLAY: its project plays, saved (SAVE) to the flash; fm1-base's backup
// protocol (backup.c: cmd 0x42 with the app's magic, the "LM8B" image with the user presets and the three projects'
// 21 objects) and its restore into a second twin (the backup put on the twin's side of the cable, cmd 0x41, the
// device's report read at RS_OK_ADDR), the same image back from it; a restore refused while playing (RS_FAIL_ADDR | 10)
// and a damaged one; audio bit-identical across instances, USB MIDI out, a RAM fork, the transport (st_transport_set)
// and the eight tracks held silent (st_track_mute_set).
//   node tests/studio_twin_loom.mjs fm1companion/sims/loom-8-studio   -> JSON on stdout
import { readFileSync, readdirSync, existsSync } from "node:fs";
import { createHash } from "node:crypto";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const root = new URL("..", import.meta.url).pathname;
const TwinCore = require(root + "fm1companion/sims/_studio/twin-core.js");
const dir = process.argv[2];

const CRC_T = Array.from({ length: 256 }, (_, n) => { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1; return c >>> 0; });
const crc32 = (b) => { let c = 0xFFFFFFFF; for (const x of b) c = CRC_T[(c ^ x) & 255] ^ (c >>> 8); return (c ^ 0xFFFFFFFF) >>> 0; };
// ota.c's framing: F0 pack7(00 59 cmd len24 body ~sum(body)) F7, the bits LSB first
function pack7(b) {
  const o = []; let acc = 0, nb = 0;
  for (const x of b) { acc |= x << nb; nb += 8; while (nb >= 7) { o.push(acc & 127); acc >>= 7; nb -= 7; } }
  if (nb) o.push(acc & 127);
  return o;
}
function unpack7(a) {
  const o = []; let acc = 0, nb = 0;
  for (const x of a) { acc |= (x & 127) << nb; nb += 7; if (nb >= 8) { o.push(acc & 255); acc >>= 8; nb -= 8; } }
  return o;
}
function frame(cmd, body) {
  const n = body.length, s = body.reduce((a, x) => a + x, 0);
  return [0xF0, ...pack7([0, 0x59, cmd, n & 255, (n >> 8) & 255, 0, ...body, ~s & 255]), 0xF7];
}
function parse(m) {
  if (m[0] !== 0xF0 || m[m.length - 1] !== 0xF7) return null;
  const u = unpack7(m.slice(1, -1));
  if (u.length < 7 || u[0] !== 0 || u[1] !== 0x59) return null;
  const n = u[3] | u[4] << 8 | u[5] << 16;
  if (n + 7 !== u.length) return null;
  return { cmd: u[2], body: u.slice(6, 6 + n) };
}
const le32 = (n) => [n & 255, (n >>> 8) & 255, (n >>> 16) & 255, (n >>> 24) & 255];
const rd32 = (b, o) => (b[o] | b[o + 1] << 8 | b[o + 2] << 16 | b[o + 3] << 24) >>> 0;
const rd16 = (b, o) => b[o] | b[o + 1] << 8;
const RS_OK = 0xC0000000, RS_FAIL = 0xCF000000;

function identity(t, full) {
  const r = t.midi(frame(0x11, full ? [1] : [])).map(parse).find((f) => f && f.cmd === 0x11);
  if (!r) return null;                               // (before 1.2.1: no answer to the query with the body 01)
  return String.fromCharCode(...r.body.slice(0, r.body.indexOf(0)));
}
function backup(t) {                                 // tools/x0x_backup.py receive: the frames until the end frame
  let got = [], end = null;
  for (const f of t.midi(frame(0x42, [...Buffer.from("LM8B")])).map(parse).filter(Boolean)) {
    if (f.cmd === 0x42) { if (rd32(f.body, 0) !== got.length) throw new Error("a gap in the backup"); got = got.concat(f.body.slice(4)); }
    if (f.cmd === 0x43) end = f.body;
  }
  if (!end || rd32(end, 0) !== got.length || rd32(end, 4) !== crc32(got) || end[8]) throw new Error("backup end");
  return { image: Uint8Array.from(got), flags: end[9] };
}
function records(img) {                              // backup.c's image: head, six records, the tail
  if (String.fromCharCode(...img.slice(0, 4)) !== "LM8B" || rd32(img, 12) !== crc32(img.slice(0, 12))) throw new Error("head");
  const out = []; let p = 16;
  for (let i = 0; i < rd16(img, 6); i++) {
    const id = rd16(img, p), n = rd16(img, p + 2), c = rd32(img, p + 4), d = img.slice(p + 8, p + 8 + n);
    if (crc32(d) !== c) throw new Error("record CRC");
    out.push({ id, n, sha: createHash("sha256").update(d).digest("hex") });
    p += 8 + n;
  }
  if (p + 4 !== img.length || rd32(img, p) !== crc32(img.slice(0, p))) throw new Error("tail");
  return out;
}
function restore(t, img) {                           // the suite's twin path: stage (0x7E), then 0x41; -> the report
  for (let off = 0; off < img.length; off += 512)
    t.midi(frame(0x7E, [...le32(off), ...img.slice(off, off + 512)]));
  const out = t.midi(frame(0x41, [...le32(img.length), ...le32(crc32(img))])).map(parse).filter(Boolean);
  const rep = out.filter((f) => f.cmd === 0x30).map((f) => rd32(f.body, 1)).filter((a) => a >= RS_OK);
  return rep.length ? (rep.includes(RS_OK) ? 0 : rep.find((a) => a >= RS_FAIL) - RS_FAIL) : -1;
}
const sha = (x) => createHash("sha256").update(x).digest("hex");
function stats(l, r) {
  let peak = 0, bad = 0;
  for (const b of [l, r]) for (const v of b) { if (!Number.isFinite(v)) bad++; else peak = Math.max(peak, Math.abs(v)); }
  return { peak, nonFinite: bad };
}
let cur = null;
const check = (ok, what) => { if (!ok) { console.error(JSON.stringify(cur)); throw new Error("FAIL: " + what); } };
const tap = (t, k) => { t.keys(1 << k); t.runMs(40); t.keys(0); t.runMs(40); };

const results = [];
for (const v of readdirSync(dir).sort()) {
  const mf = `${dir}/${v}/manifest.json`;
  if (!existsSync(mf)) continue;
  const m = JSON.parse(readFileSync(mf, "utf8"));
  const wasm = new WebAssembly.Module(readFileSync(`${dir}/${v}/${m.wasm}`));
  const res = { version: v, firmware_version: m.firmware_version }; cur = res;
  const t = await TwinCore.create(wasm);
  res.version_export = t.version();
  check(res.version_export === "LOOM-8 " + m.firmware_version, "st_version = LOOM-8 + the release");
  res.identity_plain = identity(t, false);
  check(res.identity_plain && m.identity.startsWith(res.identity_plain) && res.identity_plain.startsWith("FM-1_10"),
        "the identity handshake");
  res.screen_lit = Array.from(t.frame()).filter((x) => x).length;
  check(res.screen_lit > 1000, "the screen");
  for (const k of [0, 7, 14, 21]) { t.keys(1 << k); t.runMs(60); t.keys(0); t.runMs(60); }   // steps on the track
  t.press(TwinCore.B.PLAY);
  res.play_peak = stats(...Object.values(t.render(2 * t.fs))).peak;
  res.notes_out = t.takeOut().filter((x) => (x[0] & 0xF0) === 0x90 && x[2] > 0).length;
  t.transportSet(0); t.runMs(100);
  check(res.play_peak > 0.05, "steps written on the panel, PLAY: they play");
  t.press(TwinCore.B.SAVE); t.runMs(3000);
  const fresh = backup(t);
  res.records = records(fresh.image).map((r) => [r.id, r.n]);
  check(res.records.map((r) => r[0]).join() === [1, ...Array.from({ length: 21 }, (_, i) => i + 2)].join(),
        "the presets and the three projects' 21 objects, in backup.c's order");
  res.saved = res.records.filter((r) => r[1]).length;
  const u = await TwinCore.create(wasm);
  res.restore_rc = restore(u, fresh.image);
  check(res.restore_rc === 0, "restored (RS_OK_ADDR)");
  const back = backup(u);
  res.identical = JSON.stringify(records(back.image)) === JSON.stringify(records(fresh.image));
  check(res.identical, "the restored twin backs up the same objects");
  const song = u.getFlash();
  const takes = [];
  for (let k = 0; k < 2; k++) {
    const d = await TwinCore.create(wasm, { flash: song });
    d.press(TwinCore.B.PLAY);
    const tr = d.transport();
    const t0 = performance.now();
    const a = d.render(4 * d.fs);
    const ms = performance.now() - t0;
    takes.push({ playing: tr.playing, ...stats(a.l, a.r), hash: sha(Buffer.from(a.l.buffer)) + sha(Buffer.from(a.r.buffer)),
                 realtime: 4000 / ms });
  }
  res.takes = takes;
  check(takes[0].playing && takes[0].peak > 0.05 && takes[0].nonFinite === 0, "it plays after the power cycle");
  check(takes[0].hash === takes[1].hash, "deterministic");
  const p = await TwinCore.create(wasm);
  p.press(TwinCore.B.PLAY);
  res.playing_rc = restore(p, fresh.image);
  p.transportSet(0); p.runMs(100);
  const bad = fresh.image.slice(); bad[20] ^= 4;
  res.damaged_rc = restore(p, bad);
  check(res.playing_rc === 10 && res.damaged_rc > 0 && res.damaged_rc !== 10, "refused restores");
  const f1 = await TwinCore.create(wasm, { flash: song }); f1.press(TwinCore.B.PLAY); f1.render(4410);
  const f2 = await TwinCore.create(wasm, { memory: f1.snapshotMemory() });
  res.fork_identical = sha(Buffer.from(f1.render(44100).l.buffer)) === sha(Buffer.from(f2.render(44100).l.buffer));
  check(res.fork_identical, "memory fork continues identically");
  res.transport_set = f1.transportSet(0); f1.runMs(50);
  res.stopped = !f1.transport().busy;
  f1.transportSet(1); f1.runMs(50);
  res.play_again = f1.transport().playing;
  check(res.transport_set && res.stopped && res.play_again, "st_transport_set");
  // every track silent, then the mix again: the eight tracks held silent (the stems)
  res.track_peak = [];
  for (const mute of [true, false]) {
    const d = await TwinCore.create(wasm, { flash: song });
    for (let k = 0; k < 8; k++) d.muteSet(k, mute);
    d.press(TwinCore.B.PLAY);
    d.render(d.fs / 2);
    res.track_peak.push(stats(...Object.values(d.render(2 * d.fs))).peak);
    res.mute_read = d.ex.st_track_mute(3);
  }
  check(res.track_peak[0] < 1e-3 && res.track_peak[1] > 0.05, "st_track_mute_set: eight tracks");
  results.push(res);
}
console.log(JSON.stringify(results));
