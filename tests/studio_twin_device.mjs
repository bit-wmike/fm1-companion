// SPDX-License-Identifier: GPL-3.0-only
// A studio twin as an FM-1 on a pipe (one MIDI message per line as hex on stdin / stdout, like tests/mock_fm1.mjs),
// for transport.SubprocessTransport: the Suite's own Python code (backup, presets, playback …) talks to the real
// firmware code. The device clock runs in real time (sound is computed and dropped).
//   node tests/studio_twin_device.mjs <twin.wasm> [--flash in.bin] [--save out.bin]
import { readFileSync, writeFileSync } from "node:fs";
import readline from "node:readline";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const TwinCore = require(new URL("../fm1companion/sims/_studio/twin-core.js", import.meta.url).pathname);
const args = process.argv.slice(2);
const opt = (k) => { const i = args.indexOf(k); return i >= 0 ? args[i + 1] : null; };
const t = await TwinCore.create(readFileSync(args[0]), opt("--flash") ? { flash: readFileSync(opt("--flash")) } : {});
const emit = (msgs) => { for (const m of msgs) process.stdout.write(Buffer.from(m).toString("hex") + "\n"); };
let last = performance.now();
const clock = setInterval(() => {
  const now = performance.now(), ms = Math.floor(now - last);
  if (ms > 0) { last += ms; t.runMs(Math.min(ms, 100)); emit(t.takeOut()); }
}, 5);
console.log(`# studio twin ${t.version()} ready`);
const rl = readline.createInterface({ input: process.stdin });
rl.on("line", (l) => {
  l = l.trim();
  if (!l || l.startsWith("#")) return;
  emit(t.midi(Uint8Array.from(Buffer.from(l, "hex"))));
});
rl.on("close", () => {
  clearInterval(clock);
  if (opt("--save")) writeFileSync(opt("--save"), t.getFlash());
  process.exit(0);
});
