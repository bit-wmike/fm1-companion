// SPDX-License-Identifier: GPL-3.0-only
// The studio twin host (fm1companion/sims/_studio/twin-host.js) on a pipe, for tests of fm1companion/studio.py
// without QtWebEngine (studio.NodeBackend). One JSON object per line:
//   in   {"id": n, "op": "...", ...}          bytes as base64 in "flash" / "memory" / "wasm_path" for the module
//   out  {"id": n, "ok": true, ...} | {"id": n, "ok": false, "error": "..."} | {"ev": "midi", "data": [[...], ...]}
// The device clock runs in real time unless {"op": "clock", "run": false}. render writes to "path" (append).
import { readFileSync, appendFileSync, writeFileSync } from "node:fs";
import readline from "node:readline";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const base = new URL("../fm1companion/sims/_studio/", import.meta.url).pathname;
globalThis.TwinCore = require(base + "twin-core.js");
const TwinHost = require(base + "twin-host.js");
const send = (o) => process.stdout.write(JSON.stringify(o, (k, v) => v instanceof Uint8Array ? { b64: Buffer.from(v).toString("base64") } : v instanceof Float32Array ? Array.from(v) : v) + "\n");
const host = new TwinHost({ emitMidi: (m) => send({ ev: "midi", data: m }) });
const bytes = (v) => v && v.b64 !== undefined ? new Uint8Array(Buffer.from(v.b64, "base64")) : v;
let clockOn = true, last = performance.now();
setInterval(() => {
  const now = performance.now(), ms = Math.floor(now - last);
  if (ms <= 0) return;
  last += ms;
  if (clockOn && host.t) host.handle({ op: "run", ms: Math.min(ms, 100) });
}, 5);
const rl = readline.createInterface({ input: process.stdin });
let chain = Promise.resolve();
rl.on("line", (line) => {
  if (!line.trim()) return;
  const c = JSON.parse(line);
  chain = chain.then(async () => {
    try {
      let r;
      if (c.op === "boot") r = await host.boot(readFileSync(c.wasm_path), bytes(c.flash), c.master ?? 1023);
      else if (c.op === "clock") { clockOn = !!c.run; last = performance.now(); r = {}; }
      else if (c.op === "render") {
        const state = c.source === "saved" ? { flash: host.t.getFlash() } : { memory: host.t.snapshotMemory() };
        writeFileSync(c.path, new Uint8Array(0));
        r = await TwinHost.renderJob(host.module, state, c, (b) => appendFileSync(c.path, b));
      } else {
        for (const k of ["flash", "memory"]) if (c[k]) c[k] = bytes(c[k]);
        r = await host.handle(c);
      }
      send({ id: c.id, ok: true, ...r });
    } catch (e) {
      send({ id: c.id, ok: false, error: String(e && e.stack || e) });
    }
  });
});
rl.on("close", () => process.exit(0));
send({ ev: "ready" });
