// SPDX-License-Identifier: GPL-3.0-only
// The studio twin page: QWebChannel object "studio" (fm1companion/studio_web.py) <-> the twin worker.
//   Python -> command(json {id, op, ...}); bytes arrive as {blob: url} (fetched here), bytes in results go to
//   c.sink (POST, ?k=<field>) and are replaced by {sink: field}. JS -> reply(json) with {id, ok, ...} or {ev, ...}.
"use strict";
let bridge = null, worker = null, wasmBytes = null, seq = 0, ctx = null, node = null;
const waits = new Map();

function startWorker() {
  worker = new Worker("studio-worker.js");
  worker.onmessage = (e) => {
    const m = e.data;
    if (m.ev === "midi") { send({ ev: "midi", data: m.data.map((x) => Array.from(x)) }); return; }
    const w = waits.get(m.id);
    if (w) { waits.delete(m.id); w(m); }
  };
}
function toWorker(c, transfer = []) {
  return new Promise((res) => { const id = "w" + (++seq); waits.set(id, res); worker.postMessage({ ...c, id }, transfer); });
}
function send(o) { if (bridge) bridge.reply(JSON.stringify(o)); }

async function fetchBytes(url) { return new Uint8Array(await (await fetch(url)).arrayBuffer()); }
async function inArgs(c) {
  for (const [k, v] of Object.entries(c)) if (v && typeof v === "object" && v.blob) c[k] = await fetchBytes(v.blob);
  return c;
}
async function outBytes(r, sink) {
  const o = {};
  for (const [k, v] of Object.entries(r || {})) {
    if (ArrayBuffer.isView(v)) {
      await fetch(sink + "?k=" + encodeURIComponent(k), { method: "POST", body: new Uint8Array(v.buffer, v.byteOffset, v.byteLength) });
      o[k] = { sink: k };
    } else o[k] = v;
  }
  return o;
}

async function audio(on) {
  if (!on) {
    if (ctx) { await ctx.close(); ctx = null; node = null; }
    await toWorker({ op: "audio_off" });
    return { on: false };
  }
  if (ctx) return { on: true, state: ctx.state, rate: ctx.sampleRate };
  ctx = new AudioContext({ sampleRate: 44100, latencyHint: "interactive" });
  await ctx.audioWorklet.addModule("studio-worklet.js");
  node = new AudioWorkletNode(ctx, "studio-out", { numberOfInputs: 0, outputChannelCount: [2] });
  node.connect(ctx.destination);
  const mc = new MessageChannel();
  await toWorker({ op: "audio_port", port: mc.port1 }, [mc.port1]);
  node.port.postMessage({ type: "port", port: mc.port2 }, [mc.port2]);
  if (ctx.state !== "running") await ctx.resume().catch(() => {});
  return { on: true, state: ctx.state, rate: ctx.sampleRate, latency: ctx.baseLatency };
}

async function render(c) {                         // offline: a render worker from a snapshot of the live twin
  const snap = await toWorker({ op: "snapshot", kind: c.source === "saved" ? "flash" : "memory" });
  if (!snap.ok) throw new Error(snap.error);
  const rw = new Worker("studio-worker.js");
  try {
    return await new Promise((resolve, reject) => {
      let posting = Promise.resolve();
      rw.onmessage = (e) => {
        const m = e.data;
        if (m.chunk) {
          posting = posting.then(() => fetch(c.sink, { method: "POST", body: m.chunk }))
            .then(() => rw.postMessage({ op: "ack" }), reject);
          return;
        }
        if (m.ev) return;
        posting.then(() => (m.ok ? resolve(m.r) : reject(new Error(m.error))));
      };
      const state = snap.r.memory ? { memory: snap.r.memory } : { flash: snap.r.flash };
      rw.postMessage({ op: "render_job", id: "render", wasm: wasmBytes, params: c, ...state },
                     [Object.values(state)[0].buffer]);
    });
  } finally { rw.terminate(); }
}

async function onCommand(json) {
  const c = await inArgs(JSON.parse(json));
  let r;
  try {
    if (c.op === "boot") {
      wasmBytes = await fetchBytes(c.wasm);
      if (worker) worker.terminate();
      startWorker();
      const m = await toWorker({ op: "boot", wasm: wasmBytes.slice(), flash: c.flash || null, clock: c.clock, master: c.master });
      if (!m.ok) throw new Error(m.error);
      r = m.r;
    } else if (c.op === "audio") r = await audio(!!c.on);
    else if (c.op === "panel") r = self.studioPanel ? self.studioPanel(c) : { on: false };
    else if (c.op === "render") r = await render(c);
    else {
      const transfer = c.flash ? [c.flash.buffer] : [];
      const m = await toWorker(c, transfer);
      if (!m.ok) throw new Error(m.error);
      r = m.r;
    }
    if (c.nowait) return;
    send({ id: c.id, ok: true, ...(await outBytes(r, c.sink)) });
  } catch (err) {
    if (c.nowait) return;
    send({ id: c.id, ok: false, error: String(err && err.stack || err) });
  }
}

new QWebChannel(qt.webChannelTransport, (ch) => {
  bridge = ch.objects.studio;
  bridge.command.connect((s) => { onCommand(s); });
  bridge.ready();
});
