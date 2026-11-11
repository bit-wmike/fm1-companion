// SPDX-License-Identifier: GPL-3.0-only
// Headless check of the FM1 Open studio twin (fm1companion/sims/fm1-open-studio/<version>/twin.wasm) through the shared
// host core (fm1companion/sims/_studio/twin-core.js): boot (a new device: the factory sample bank), st_version, the
// SDK's identity handshake (FM-1_8000014), FM1 Open's own file transfer protocol (app/transfer.c: F0 7D 'F' 'M' 02,
// token, LSB-first 7-bit stream, checksum): the project library (command 0), the capabilities (22), a factory project
// uploaded into the working area and saved into slot 1 (3/4/5, 7), its name (9), the working project downloaded
// (1/2) byte-identical, the DX7 library banks read and written back (kind 4), the sample bank's status (11), the flash
// persisting across a power cycle, PLAY non-silent and bit-identical across instances, a RAM fork, a MIDI note,
// the screen, the transport (st_transport_set) and the tracks muted (st_track_mute_set).
//   node tests/studio_twin_fm1open.mjs fm1companion/sims/fm1-open-studio   -> JSON on stdout
import { readFileSync, readdirSync, existsSync } from "node:fs";
import { createHash } from "node:crypto";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const root = new URL("..", import.meta.url).pathname;
const TwinCore = require(root + "fm1companion/sims/_studio/twin-core.js");
const dir = process.argv[2];

const CRC_T = Array.from({ length: 256 }, (_, n) => { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1; return c >>> 0; });
const crc32 = (b) => { let c = 0xFFFFFFFF; for (const x of b) c = CRC_T[(c ^ x) & 255] ^ (c >>> 8); return (c ^ 0xFFFFFFFF) >>> 0; };
function pack7(b) { const o = []; let bits = 0, v = 0; for (const x of b) { v |= x << bits; bits += 8; while (bits >= 7) { o.push(v & 127); v >>>= 7; bits -= 7; } } if (bits) o.push(v & 127); return o; }
function unpack7(a) { const o = []; let bits = 0, v = 0; for (const x of a) { v |= x << bits; bits += 7; if (bits >= 8) { o.push(v & 255); v >>>= 8; bits -= 8; } } return Uint8Array.from(o); }
let token = 1;
function frame(cmd, payload = []) {
  const tk = token++ & 16383, d = [0xF0, 0x7D, 0x46, 0x4D, 0x02, cmd, tk & 127, tk >> 7, ...pack7(payload)];
  d.push((-d.slice(1).reduce((a, b) => a + b, 0)) & 127, 0xF7);
  return d;
}
// one request -> {status, data} of the reply (the same command | 64)
function request(t, cmd, payload = []) {
  const mine = (m) => m[0] === 0xF0 && m[1] === 0x7D && m[2] === 0x46 && m[3] === 0x4D && m[5] === (cmd | 64);
  let r = t.midi(frame(cmd, payload)).find(mine);
  for (let ms = 0; !r && ms < 3000; ms++) { t.runMs(1); r = t.takeOut().find(mine); }
  if (!r) throw new Error(`no reply to command ${cmd}`);
  const d = unpack7(r.slice(8, -2));
  return { status: d[0], data: d.slice(1) };
}
const le = (v, n) => Array.from({ length: n }, (_, i) => (v >>> (8 * i)) & 255);
function download(t, kind, target = 0) {
  const r = request(t, 1, [kind, target]);
  if (r.status) throw new Error(`download ${kind}: status ${r.status}`);
  const size = r.data[0] | r.data[1] << 8, out = new Uint8Array(size);
  for (let at = 0; at < size; at += 64) {
    const n = Math.min(64, size - at), c = request(t, 2, [at & 255, at >> 8, n]);
    if (c.status) throw new Error(`read ${at}: ${c.status}`);
    out.set(c.data.slice(0, n), at);
  }
  return out;
}
function upload(t, kind, target, bytes) {
  let r = request(t, 3, [kind, target, bytes.length & 255, bytes.length >> 8]);
  if (r.status) return r.status;
  for (let at = 0; at < bytes.length; at += 64) {
    r = request(t, 4, [at & 255, at >> 8, ...bytes.slice(at, at + 64)]);
    if (r.status) return r.status;
  }
  return request(t, 5, le(crc32(bytes), 4)).status;
}
const sha = (x) => createHash("sha256").update(x).digest("hex");
const ascii = (b) => String.fromCharCode(...b).replace(/\0.*$/s, "");
function stats(l, r) {
  let peak = 0, bad = 0;
  for (const b of [l, r]) for (const v of b) { if (!Number.isFinite(v)) bad++; else peak = Math.max(peak, Math.abs(v)); }
  return { peak, nonFinite: bad };
}
function identity(t) {               // the update handshake F0 00 32 45 00 00 00 40 7F F7 (00 59 11 ...)
  const m = t.midi([0xF0, 0x00, 0x32, 0x45, 0x00, 0x00, 0x00, 0x40, 0x7F, 0xF7]).find((x) => x[0] === 0xF0);
  if (!m) return "";
  const raw = unpack7(m.slice(1, -1));
  return raw[0] === 0 && raw[1] === 89 && raw[2] === 17 ? ascii(raw.slice(6, 33)) : "?";
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
  check(res.version_export === "FM1 OPEN " + m.firmware_version, "st_version = FM1 OPEN + manifest version");
  res.identity = identity(t);
  check(res.identity === m.identity, "the handshake answers the package identity");
  res.update_key = t.midi([0xF0, 0x22, 0x24, 0x35, 0x7F, 0xF7]).length;      // dropped: no updater in the twin
  const lib = request(t, 0);
  res.library = { status: lib.status, format: lib.data[0], slots: lib.data[1], slot: lib.data[2],
                  bytes: lib.data[3] | lib.data[4] << 8, sources: Array.from(lib.data.slice(6, 14)) };
  check(lib.status === 0 && lib.data[1] === 16 && res.library.bytes === 4064, "command 0: 16 slots of 4064 bytes");
  const cap = request(t, 22);
  res.capabilities = Array.from(cap.data.slice(0, 4));
  res.library_available = cap.data[3];
  res.names_fresh = [0, 1, 15].map((s) => ascii(request(t, 9, [s]).data).trim());
  res.slot_status_fresh = request(t, 9, [0]).status;
  check(res.slot_status_fresh === 1, "a new device: slot 1 empty");
  const smp = request(t, 11);
  res.samples = { status: smp.data[0], bytes: smp.data[1] | smp.data[2] << 8 | smp.data[3] << 16 };
  check(smp.data[0] === 2, "the factory sample bank is ready");
  t.runMs(500);
  res.screen_lit = Array.from(t.frame()).filter((x) => x).length;
  check(res.screen_lit > 1000, "the screen shows something");
  // a factory project (Simulator.c's sim_factory_project: the project with its tuning block) into the working area,
  // then SAVE into slot 1 (command 7), as the workbench's "save to the device" does
  check(t.ex.sim_factory_project(0) === 0, "factory project 0");
  const proj = new Uint8Array(t.mem, t.ex.sim_file(), 4264).slice();
  res.factory_name = ascii(proj.slice(3836, 3848)).trim();
  res.upload_rc = upload(t, 0, 0, Array.from(proj));
  res.save_rc = request(t, 7, [0]).status;
  check(res.upload_rc === 0 && res.save_rc === 0, "upload into the working area and save into slot 1");
  res.slot_name = ascii(request(t, 9, [0]).data).trim();
  check(res.slot_name === res.factory_name && t.projectUsed(0) && !t.projectUsed(1), "slot 1 holds it");
  const work = download(t, 3);
  res.working_bytes = work.length;
  res.working_same = sha(work.slice(0, 4064)) === sha(proj.slice(0, 4064));
  // the DX7 library: bank A read (the 128 V15 factory voices, four banks of 32), written into bank D, read back
  const bankA = download(t, 4, 0);
  res.bank_a_first = ascii(bankA.slice(118, 128));
  res.bank_d_rc = upload(t, 4, 3, Array.from(bankA));
  res.bank_d_same = sha(download(t, 4, 3)) === sha(bankA);
  check(bankA.length === 4096 && res.bank_d_rc === 0 && res.bank_d_same, "a DX7 library bank written and read back");
  // a power cycle: the flash keeps slot 1 and bank D
  t.runMs(3000);                                                    // (the settings are written a little later)
  const flash = t.getFlash();
  const t2 = await TwinCore.create(wasm, { flash });
  res.persisted = ascii(request(t2, 9, [0]).data).trim() === res.factory_name &&
    sha(download(t2, 4, 3)) === sha(bankA);
  check(res.persisted, "slot 1 and the library survive a power cycle");
  // PLAY on slot 1 in two instances: the same samples
  const takes = [];
  for (let k = 0; k < 2; k++) {
    const d = await TwinCore.create(wasm, { flash });
    d.runMs(300);
    res.load_rc = d.projectLoad(0);
    d.runMs(100); d.takeOut();
    d.press(TwinCore.B.PLAY);
    const tr = d.transport();
    const t0 = performance.now();
    const a = d.render(4 * d.fs);
    const ms = performance.now() - t0;
    takes.push({ playing: tr.playing && tr.busy, ...stats(a.l, a.r),
                 hash: sha(Buffer.from(a.l.buffer)) + sha(Buffer.from(a.r.buffer)), realtime: 4000 / ms,
                 project: d.projectCurrent(), play_lit: !!(d.leds().buttons & (1 << d.ex.st_panel_button(TwinCore.B.PLAY))) });
  }
  res.takes = takes;
  check(res.load_rc === 0 && takes[0].playing && takes[0].peak > 0.02 && takes[0].nonFinite === 0, "slot 1 plays");
  check(takes[0].hash === takes[1].hash, "deterministic");
  // a fork: the RAM of a playing instance continues identically in another
  const f1 = await TwinCore.create(wasm, { flash }); f1.runMs(300); f1.projectLoad(0); f1.press(TwinCore.B.PLAY); f1.render(4410);
  const f2 = await TwinCore.create(wasm, { memory: f1.snapshotMemory() });
  const a1 = f1.render(44100), a2 = f2.render(44100);
  res.fork_identical = sha(Buffer.from(a1.l.buffer)) === sha(Buffer.from(a2.l.buffer));
  check(res.fork_identical, "memory fork continues identically");
  // the transport through the optional export
  res.transport_set = f1.transportSet(0); f1.runMs(50);
  res.stopped = !f1.transport().busy;
  f1.transportSet(2); f1.runMs(50);
  res.song_play = f1.transport().playing;
  f1.transportSet(3); f1.runMs(50);
  res.restart_play = f1.transport().playing;
  f1.transportSet(0); f1.runMs(50);
  check(res.transport_set && res.stopped && res.song_play && res.restart_play && !f1.transport().busy, "st_transport_set");
  // every track muted: silence; none muted: the song
  res.mute_rms = [];
  for (const all of [false, true]) {
    const d = await TwinCore.create(wasm, { flash });
    d.runMs(300); d.projectLoad(0);
    for (let k = 0; k < 8; k++) d.muteSet(k, all);
    d.press(TwinCore.B.PLAY);
    const a = d.render(3 * d.fs);
    let e = 0;
    for (let i = 0; i < a.l.length; i++) e += a.l[i] * a.l[i] + a.r[i] * a.r[i];
    res.mute_rms.push(Math.sqrt(e / (2 * a.l.length)));
  }
  check(res.mute_rms[0] > 0.005 && res.mute_rms[1] < res.mute_rms[0] / 20, "st_track_mute_set");
  // a note over USB MIDI (channel 1 = track 1) sounds
  const n = await TwinCore.create(wasm, { flash });
  n.runMs(300); n.projectLoad(0); n.runMs(50);
  n.midi([0x90, 60, 100]);
  const na = n.render(n.fs / 2);
  n.midi([0x80, 60, 0]);
  res.note_peak = stats(na.l, na.r).peak;
  check(res.note_peak > 0.01, "a USB MIDI note sounds");
  results.push(res);
}
console.log(JSON.stringify(results));
