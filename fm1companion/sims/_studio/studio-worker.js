// SPDX-License-Identifier: GPL-3.0-only
// The studio twin's Web Worker (studio.html starts it). Live: the twin (TwinHost), its clock in real time while no
// audio pulls it, and audio for the AudioWorklet on a MessagePort. Render job: a second worker of this file renders
// offline from a snapshot (the live twin is untouched); its chunks wait for the page's acknowledgement.
importScripts("twin-core.js", "twin-host.js");
const host = new TwinHost({ emitMidi: (m) => postMessage({ ev: "midi", data: m }) });
let clockOn = true, audioPort = null, last = performance.now(), ack = null;

setInterval(() => {                                   // the device clock while no audio drives it
  const now = performance.now(), ms = Math.floor(now - last);
  if (ms <= 0) return;
  last += ms;
  if (clockOn && !audioPort && host.t) host.handle({ op: "run", ms: Math.min(ms, 100) });
}, 5);

function views(r) {                                   // typed arrays travel as transferables
  const t = [];
  for (const v of Object.values(r || {})) if (ArrayBuffer.isView(v)) t.push(v.buffer);
  return t;
}

onmessage = async (e) => {
  const c = e.data;
  if (c.op === "ack") { const a = ack; ack = null; if (a) a(); return; }
  try {
    let r;
    if (c.op === "boot") { r = await host.boot(c.wasm, c.flash, c.master ?? 1023); clockOn = c.clock !== false; last = performance.now(); }
    else if (c.op === "clock") { clockOn = !!c.run; last = performance.now(); r = {}; }
    else if (c.op === "audio_port") {
      audioPort = c.port;
      audioPort.onmessage = (m) => {
        const n = m.data.need | 0;
        if (!n || !host.t) return;
        const a = host.t.render(n);
        host.out();
        audioPort.postMessage({ l: a.l, r: a.r }, [a.l.buffer, a.r.buffer]);
      };
      r = {};
    } else if (c.op === "audio_off") { if (audioPort) audioPort.close(); audioPort = null; last = performance.now(); r = {}; }
    else if (c.op === "render_job") {
      const state = c.memory ? { memory: c.memory } : { flash: c.flash };
      r = await TwinHost.renderJob(await WebAssembly.compile(c.wasm), state, c.params, (b) => new Promise((res) => {
        ack = res;
        postMessage({ id: c.id, chunk: b }, [b.buffer]);
      }));
    } else r = await host.handle(c);
    postMessage({ id: c.id, ok: true, r }, views(r));
  } catch (err) {
    postMessage({ id: c.id, ok: false, error: String(err && err.stack || err) });
  }
};
