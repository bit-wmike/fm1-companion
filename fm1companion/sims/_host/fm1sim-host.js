// SPDX-License-Identifier: GPL-3.0-only
// FM-1 Companion: helper loaded into every simulator page (bundled builds include it as the first script;
// online emulators get it injected by the app at document creation). It changes nothing the page does
// on its own; it only adds:
//   * window.__fm1sim.midi(bytes)   play MIDI into the simulator (worklet message, else computer keys)
//   * window.__fm1sim.record        tap of everything the page sends to the speakers -> WAV download
//   * ?fm1latency=interactive|balanced|playback   replaces the page's AudioContext latencyHint
//   * outside the app (Firefox): MIDI from the app via server-sent events on /_fm1/midi/<key> (?fm1relay=<key>)
(function () {
  "use strict";
  if (window.__fm1sim) return;
  const q = new URLSearchParams(location.search);
  const latency = q.get("fm1latency");
  const contexts = [], nodes = [], queue = [];
  const api = { version: 1, contexts, nodes, notesIn: 0, errors: [] };
  window.__fm1sim = api;
  addEventListener("error", (e) => api.errors.push(String(e.message || e)));

  // ---------------------------------------------------------------- WebAssembly on older Chromium
  // Chromium < 104 (Qt WebEngine 6.4 on Bookworm) refuses `new WebAssembly.Module(bytes)` above 4 KB on the
  // main thread. Pages that compile synchronously (FM1 Open's panel) get a module compiled asynchronously
  // while their .wasm download completes; the worklet (not the main thread) is unaffected.
  const compiled = [];
  const NativeModule = WebAssembly.Module;
  const view = (b) => (b instanceof ArrayBuffer ? new Uint8Array(b) : new Uint8Array(b.buffer, b.byteOffset, b.byteLength));
  const same = (a, b) => {
    if (a.length !== b.length) return false;
    for (let i = 0; i < a.length; i += 1) if (a[i] !== b[i]) return false;
    return true;
  };
  const Module = function (bytes, ...rest) {
    try {
      const u8 = view(bytes);
      for (const c of compiled) if (same(c.bytes, u8)) return c.module;
    } catch (_) { /* not a buffer: let the engine report it */ }
    return new NativeModule(bytes, ...rest);
  };
  Module.prototype = NativeModule.prototype;
  for (const k of ["imports", "exports", "customSections"]) Module[k] = NativeModule[k].bind(NativeModule);
  WebAssembly.Module = Module;
  // the same limit applies to `new WebAssembly.Instance`: keep one spare instance (no imports) per module
  const NativeInstance = WebAssembly.Instance;
  const spare = new Map();
  const prepare = (m) => { WebAssembly.instantiate(m, {}).then((i) => spare.set(m, i), () => {}); };
  const Instance = function (module, imports) {
    const i = spare.get(module);
    if (i && (!imports || !Object.keys(imports).length)) {
      spare.delete(module);
      prepare(module);
      return i;
    }
    return new NativeInstance(module, imports);
  };
  Instance.prototype = NativeInstance.prototype;
  WebAssembly.Instance = Instance;
  const nativeFetch = window.fetch && window.fetch.bind(window);
  if (nativeFetch) {
    window.fetch = function (input, init) {
      const url = String(input && input.url ? input.url : input);
      const p = nativeFetch(input, init);
      if (!/\.wasm(\?|#|$)/.test(url)) return p;
      return p.then(async (r) => {
        if (!r.ok) return r;
        const bytes = new Uint8Array(await r.arrayBuffer());
        try {
          const module = await WebAssembly.compile(bytes);
          compiled.push({ bytes, module });
          try { spare.set(module, await WebAssembly.instantiate(module, {})); } catch (_) { /* needs imports */ }
        } catch (_) { /* the page reports it */ }
        return new Response(bytes, { status: r.status, statusText: r.statusText, headers: r.headers });
      });
    };
  }

  // ---------------------------------------------------------------- audio graph hooks
  const NativeAC = window.AudioContext || window.webkitAudioContext;
  if (NativeAC) {
    const AC = class extends NativeAC {
      constructor(opts) {
        const o = Object.assign({}, opts || {});
        if (latency) o.latencyHint = latency;
        super(o);
        contexts.push(this);
      }
    };
    window.AudioContext = AC;
    if (window.webkitAudioContext) window.webkitAudioContext = AC;
  }
  const NativeNode = window.AudioWorkletNode;
  if (NativeNode) {
    window.AudioWorkletNode = class extends NativeNode {
      constructor(c, name, opts) {
        super(c, name, opts);
        nodes.push(this);
        setTimeout(flush, 50);
      }
    };
  }
  const taps = new WeakMap();
  let rec = null;
  function tap(ctx) {
    let t = taps.get(ctx);
    if (t) return t;
    const input = ctx.createGain();
    const sp = ctx.createScriptProcessor(4096, 2, 2);
    sp.onaudioprocess = (e) => {
      const b = e.inputBuffer;
      for (let ch = 0; ch < e.outputBuffer.numberOfChannels; ch++) e.outputBuffer.getChannelData(ch).fill(0);
      if (!rec || rec.ctx !== ctx) return;
      const l = b.getChannelData(0), r = b.numberOfChannels > 1 ? b.getChannelData(1) : l;
      rec.l.push(l.slice()); rec.r.push(r.slice()); rec.frames += l.length;
      for (let i = 0; i < l.length; i++) { const a = Math.abs(l[i]), c = Math.abs(r[i]); if (a > rec.peak) rec.peak = a; if (c > rec.peak) rec.peak = c; }
    };
    nativeConnect.call(input, sp);
    nativeConnect.call(sp, ctx.destination);
    t = { input, sp };
    taps.set(ctx, t);
    return t;
  }
  const nativeConnect = AudioNode.prototype.connect;
  AudioNode.prototype.connect = function (dest, ...rest) {
    const r = nativeConnect.call(this, dest, ...rest);
    try {
      if (dest instanceof AudioDestinationNode) nativeConnect.call(this, tap(dest.context).input);
    } catch (err) { api.errors.push("tap: " + err); }
    return r;
  };

  function wav(l, r, frames, rate) {
    const buf = new ArrayBuffer(44 + frames * 4), v = new DataView(buf);
    const s = (o, t) => { for (let i = 0; i < t.length; i++) v.setUint8(o + i, t.charCodeAt(i)); };
    s(0, "RIFF"); v.setUint32(4, 36 + frames * 4, true); s(8, "WAVE"); s(12, "fmt ");
    v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 2, true); v.setUint32(24, rate, true);
    v.setUint32(28, rate * 4, true); v.setUint16(32, 4, true); v.setUint16(34, 16, true); s(36, "data"); v.setUint32(40, frames * 4, true);
    let o = 44;
    for (let k = 0; k < l.length; k++) {
      const a = l[k], b = r[k];
      for (let i = 0; i < a.length; i++) {
        v.setInt16(o, Math.max(-1, Math.min(1, a[i])) * 32767, true);
        v.setInt16(o + 2, Math.max(-1, Math.min(1, b[i])) * 32767, true);
        o += 4;
      }
    }
    return new Blob([buf], { type: "audio/wav" });
  }
  api.record = {
    start() {
      const ctx = contexts.find((c) => c.state === "running") || contexts[contexts.length - 1];
      if (!ctx) return { ok: false, error: "noaudio" };
      tap(ctx);
      rec = { ctx, l: [], r: [], frames: 0, peak: 0, t0: performance.now() };
      return { ok: true, rate: ctx.sampleRate };
    },
    state() {
      return rec ? { recording: true, seconds: rec.frames / rec.ctx.sampleRate, peak: rec.peak } : { recording: false };
    },
    stop(name) {
      if (!rec) return { ok: false, error: "notrecording" };
      const r = rec; rec = null;
      const blob = wav(r.l, r.r, r.frames, r.ctx.sampleRate);
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = name || "Simulator.wav";
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 60000);
      return { ok: true, seconds: r.frames / r.ctx.sampleRate, peak: r.peak, bytes: blob.size };
    },
  };

  // ---------------------------------------------------------------- MIDI into the simulator
  const id = () => window.__FM1SIM_ID || "";
  const TRACKER = ["KeyZ", "KeyS", "KeyX", "KeyD", "KeyC", "KeyV", "KeyG", "KeyB", "KeyH", "KeyN", "KeyJ", "KeyM",
                   "KeyQ", "Digit2", "KeyW", "Digit3", "KeyE", "KeyR", "Digit5", "KeyT", "Digit6", "KeyY", "Digit7", "KeyU"];
  let started = false;
  function start() {
    if (started) return;
    started = true;
    const sel = { "melodee": "#power", "fm1-open": "#sim-midi" }[id()];
    const b = sel && document.querySelector(sel);
    if (b && !b.disabled) b.click();
    else started = false;
  }
  api.start = start;

  // ---------------------------------------------------------------- real-time meter
  // The audio clock (currentTime) only moves as fast as the simulator renders. Compared with the wall clock
  // over a few seconds this says honestly whether it keeps up (1.0) or plays slower and stutters (< 1.0).
  const meter = { hist: [], ctx: null };
  function sample() {
    const ctx = contexts.find((c) => c.state === "running");
    const now = performance.now();
    if (!ctx) { meter.hist = []; meter.ctx = null; return; }
    if (ctx !== meter.ctx) { meter.hist = []; meter.ctx = ctx; }
    meter.hist.push([now, ctx.currentTime]);
    while (meter.hist.length > 2 && now - meter.hist[0][0] > 4000) meter.hist.shift();
  }
  setInterval(sample, 500);
  api.speed = () => {
    sample();
    const h = meter.hist;
    if (!meter.ctx) return { running: false };
    if (h.length < 2 || h[h.length - 1][0] - h[0][0] < 1500) return { running: true, ratio: null };
    const a = h[0], b = h[h.length - 1];
    return { running: true, ratio: (b[1] - a[1]) / ((b[0] - a[0]) / 1000), seconds: (b[0] - a[0]) / 1000 };
  };
  api.status = () => ({ id: id(), nodes: nodes.length, contexts: contexts.map((c) => c.state),
                        sampleRate: contexts[0] ? contexts[0].sampleRate : 0, notesIn: api.notesIn });
  function deliver(d) {
    const kind = id(), n = nodes[nodes.length - 1];
    if (kind === "melodee") {
      if (d[0] >= 0xf0) return true;
      if (!n) return false;
      n.port.postMessage({ type: "midi", data: d });
      return true;
    }
    if (kind === "fm1-open") {
      if (!n) return false;
      n.port.postMessage({ type: "midi", data: Uint8Array.from(d) });
      return true;
    }
    // unknown (online) simulator without our adapter: computer keyboard as most emulators map it
    const st = d[0] & 0xf0;
    if (st !== 0x90 && st !== 0x80) return true;
    const code = TRACKER[(((d[1] - 48) % 24) + 24) % 24];
    const down = st === 0x90 && d[2] > 0;
    const ev = new KeyboardEvent(down ? "keydown" : "keyup", { code, key: code.slice(-1).toLowerCase(), bubbles: true });
    (document.activeElement && document.activeElement !== document.body ? document.activeElement : window).dispatchEvent(ev);
    return true;
  }
  function flush() {
    while (queue.length && deliver(queue[0])) queue.shift();
  }
  api.midi = (bytes) => {
    const d = Array.from(bytes, (x) => x & 255);
    if (!d.length) return;
    if (((d[0] & 0xf0) === 0x90) && d[2]) api.notesIn++;
    if (deliver(d)) return;
    if (queue.length < 256) queue.push(d);
    start();
    setTimeout(flush, 200);
  };

  // outside the app: MIDI from FM-1 Companion's local server (Firefox)
  if (!(window.qt && window.qt.webChannelTransport) && /^(127\.0\.0\.1|localhost)$/.test(location.hostname) && window.EventSource) {
    addEventListener("load", () => {
      const key = q.get("fm1relay");                 // random key the app puts into the address it opens
      if (!key) return;
      const es = new EventSource("/_fm1/midi/" + encodeURIComponent(key));
      es.onmessage = (e) => { try { api.midi(JSON.parse(e.data)); } catch (_) { /* ignore */ } };
      api.relay = es;
    });
  }
})();
