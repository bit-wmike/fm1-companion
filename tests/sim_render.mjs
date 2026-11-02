// SPDX-License-Identifier: GPL-3.0-only
// Headless check of the bundled simulators: instantiate each wasm exactly as its AudioWorklet does, play a
// note through the simulator's own MIDI entry, render audio and report peak, RMS, finite-ness and speed.
//   node tests/sim_render.mjs fm1companion/sims [seconds]   -> JSON on stdout
import { readFileSync } from "node:fs";
const dir = process.argv[2], seconds = Number(process.argv[3] || 2), RATE = 44100;
const stub = new Proxy({}, { get: () => () => 0 });
function stats(name, render, noteOn, noteOff) {
  let peak = 0, sum = 0, n = 0, bad = 0, silentBefore = 0;
  const pre = render(4096); for (const v of pre) silentBefore = Math.max(silentBefore, Math.abs(v));
  noteOn();
  const t0 = performance.now();
  let frames = 0;
  while (frames < seconds * RATE) {
    const buf = render(128);
    if (frames > seconds * RATE / 2 && noteOff) { noteOff(); noteOff = null; }
    for (const v of buf) { if (!Number.isFinite(v)) bad++; else { peak = Math.max(peak, Math.abs(v)); sum += v * v; n++; } }
    frames += 128;
  }
  const ms = performance.now() - t0;
  return { name, seconds, peak, rms: Math.sqrt(sum / Math.max(1, n)), nonFinite: bad, peakBeforeNote: silentBefore,
           renderMs: ms, realtimeFactor: (seconds * 1000) / ms };
}
const out = [];
{ // FM1 Open: worklet = new WebAssembly.Instance(new WebAssembly.Module(wasm), {}); _initialize; sim_boot; sim_render
  const c = new WebAssembly.Instance(new WebAssembly.Module(readFileSync(`${dir}/fm1-open/simulator.wasm`)), {}).exports;
  c._initialize(); c.sim_boot(1);
  const bf = c.sim_block_frames();
  let buf = null, pos = bf;
  const render = (n) => { const o = new Float32Array(n * 2); for (let i = 0; i < n; i++) { if (pos === bf) { buf = new Float32Array(c.memory.buffer, c.sim_render(), bf * 2); pos = 0; } o[2 * i] = buf[2 * pos]; o[2 * i + 1] = buf[2 * pos + 1]; pos++; } return o; };
  const midi = (d) => { new Uint8Array(c.memory.buffer, c.sim_file(), d.length).set(d); return c.sim_midi(d.length); };
  out.push(stats("fm1-open", render, () => midi([0x90, 60, 110]), () => midi([0x80, 60, 0])));
}
{ // Melodee: WebAssembly.instantiate(wasm, {env: stub, wasi_snapshot_preview1: stub}); _initialize; web_boot; web_render
  const ex = new WebAssembly.Instance(new WebAssembly.Module(readFileSync(`${dir}/melodee/melodee.wasm`)), { env: stub, wasi_snapshot_preview1: stub }).exports;
  ex._initialize(); ex.web_boot(700);
  const render = (n) => { const o = new Float32Array(n * 2); for (let k = 0; k < n; k += 128) { const m = Math.min(128, n - k); ex.web_render(m); const l = new Float32Array(ex.memory.buffer, ex.web_out_l(), m), r = new Float32Array(ex.memory.buffer, ex.web_out_r(), m); for (let i = 0; i < m; i++) { o[2 * (k + i)] = l[i]; o[2 * (k + i) + 1] = r[i]; } } return o; };
  out.push(stats("melodee", render, () => ex.web_midi_in(0x90, 60, 110), () => ex.web_midi_in(0x80, 60, 0)));
}
console.log(JSON.stringify(out));
