// SPDX-License-Identifier: GPL-3.0-only
// Headless check of the FoMni studio twins (fm1companion/sims/fomni-studio/<version>/twin.wasm, on FoMni's own emulator)
// through the shared host core (fm1companion/sims/_studio/twin-core.js): boot ("FoMni-1 0.4.1 BETA": no editor INFO),
// the identity handshake (ota.c), a chord held and strummed on the panel, USB MIDI out (its notes), the rhythm
// (st_transport_set), SAVE: the project in the flash (storage.c) and back in a new instance, audio bit-identical across
// instances, a RAM fork and the four parts held silent one by one (st_track_mute_set: the stems).
//   node tests/studio_twin_fomni.mjs fm1companion/sims/fomni-studio   -> JSON on stdout
import { readFileSync, readdirSync, existsSync } from "node:fs";
import { createHash } from "node:crypto";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const root = new URL("..", import.meta.url).pathname;
const TwinCore = require(root + "fm1companion/sims/_studio/twin-core.js");
const dir = process.argv[2];

function unpack7(a) {
  const o = []; let acc = 0, nb = 0;
  for (const x of a) { acc |= (x & 127) << nb; nb += 7; if (nb >= 8) { o.push(acc & 255); acc >>= 8; nb -= 8; } }
  return o;
}
const sha = (x) => createHash("sha256").update(x).digest("hex");
function stats(l, r) {
  let peak = 0, bad = 0, e = 0;
  for (const b of [l, r]) for (const v of b) { if (!Number.isFinite(v)) bad++; else { peak = Math.max(peak, Math.abs(v)); e += v * v; } }
  return { peak, nonFinite: bad, rms: Math.sqrt(e / (l.length + r.length)) };
}
const used = (f) => f.reduce((n, x) => n + (x !== 0xFF), 0);
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
  check(res.version_export === `FoMni-1 ${m.firmware_version.toUpperCase()} BETA`, "st_version = FoMni-1 + the release");
  const id = t.midi([0xF0, 0x00, 0x32, 0x45, 0x00, 0x00, 0x00, 0x40, 0x7F, 0xF7]).find((x) => x[0] === 0xF0);
  const u = id && unpack7(id.slice(1, -1));
  res.identity = u ? String.fromCharCode(...u.slice(6, u.indexOf(0, 6))) : null;
  check(res.identity && m.identity.startsWith(res.identity) && res.identity.startsWith("FM-1_8"), "the identity handshake");
  res.screen_lit = Array.from(t.frame()).filter((x) => x).length;
  check(res.screen_lit > 1000 && !t.transport().playing, "the screen; quiet until played");
  const play = (d) => {                               // a chord (black key 1) held, five white keys strummed
    d.keys(1 << 1); d.runMs(50);
    for (const k of [0, 2, 4, 6, 7]) { d.keys((1 << 1) | (1 << k)); d.runMs(30); d.keys(1 << 1); d.runMs(30); }
  };
  play(t);
  const a = t.render(t.fs);
  t.keys(0);
  res.live = stats(a.l, a.r);
  const out = t.takeOut();
  res.notes = out.filter((x) => (x[0] & 0xF0) === 0x90 && x[2] > 0).length;
  check(res.live.peak > 0.05 && res.live.nonFinite === 0 && res.notes > 0, "a strum sounds and goes out over USB MIDI");
  res.transport_set = t.transportSet(1);
  res.rhythm = t.transport().playing;
  res.rhythm_peak = stats(...Object.values(t.render(t.fs))).peak;
  t.transportSet(0); t.runMs(50);
  check(res.transport_set && res.rhythm && res.rhythm_peak > 0.05 && !t.transport().busy, "st_transport_set: the rhythm");
  // KNOB 1 turned, SAVE: the project in the flash; a new instance from it
  t.encoder(2, 6); t.runMs(500);
  t.press(TwinCore.B.SAVE); t.runMs(4000);
  const flash = t.getFlash();
  res.flash_used = used(flash);
  check(res.flash_used > 0, "SAVE writes the project to the flash");
  const takes = [];
  for (let k = 0; k < 2; k++) {
    const d = await TwinCore.create(wasm, { flash });
    d.transportSet(1);
    play(d);
    const t0 = performance.now();
    const b = d.render(4 * d.fs);
    const ms = performance.now() - t0;
    takes.push({ ...stats(b.l, b.r), hash: sha(Buffer.from(b.l.buffer)) + sha(Buffer.from(b.r.buffer)), realtime: 4000 / ms,
                 flash_same: sha(d.getFlash()) === sha(flash) });
  }
  res.takes = takes;
  check(takes[0].peak > 0.05 && takes[0].hash === takes[1].hash && takes[0].flash_same, "deterministic from the flash");
  const f1 = await TwinCore.create(wasm, { flash }); f1.transportSet(1); f1.render(4410);
  const f2 = await TwinCore.create(wasm, { memory: f1.snapshotMemory() });
  res.fork_identical = sha(Buffer.from(f1.render(44100).l.buffer)) === sha(Buffer.from(f2.render(44100).l.buffer));
  check(res.fork_identical, "memory fork continues identically");
  // the four parts alone (harp voice 1, harp voice 2, chord and bass, rhythm), and all of them silent
  res.part_rms = [];
  for (const k of [0, 1, 2, 3, -1]) {
    const d = await TwinCore.create(wasm);
    for (let j = 0; j < 4; j++) d.muteSet(j, j !== k);
    d.transportSet(1);
    play(d);
    const b = d.render(2 * d.fs);
    res.part_rms.push(stats(b.l, b.r).rms);
  }
  check(res.part_rms.slice(0, 4).every((x) => x > 1e-3) && res.part_rms[4] < 1e-5, "st_track_mute_set: four parts");
  results.push(res);
}
console.log(JSON.stringify(results));
