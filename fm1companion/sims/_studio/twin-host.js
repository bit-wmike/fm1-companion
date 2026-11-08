// SPDX-License-Identifier: GPL-3.0-only
// The studio twin's command set, shared by every host: the twin worker in QtWebEngine (studio-worker.js) and the
// node host of the tests (tests/studio_node_host.mjs). Commands are plain objects {op, ...}; results are plain
// objects (Uint8Array / Float32Array where bytes are involved). Firmware-agnostic: it only uses TwinCore (st_* ABI).
//   live instance:  boot, midi, keys, buttons, encoder, master, press, project_load, transport, info, frame,
//                   flash_get, flash_sum, flash_set (power cycle with that flash), reset, snapshot, pull (live audio), run
//   render job:     TwinHost.renderJob(module, {memory|flash}, params, sink)  (a separate instance: the live one
//                   is untouched)
(function (root) {
  "use strict";
  const TwinCore = root.TwinCore || (typeof require !== "undefined" ? require("./twin-core.js") : null);
  const ED = [0xF0, 0x7D, 0x46, 0x4C];
  const isEd = (m) => m.length > 5 && m[0] === 0xF0 && m[1] === 0x7D && m[2] === 0x46 && m[3] === 0x4C;

  // an editor request on a twin whose clock may stand still: answer, giving it up to `ms` of device time
  function edRequest(t, cmd, args = [], ms = 500) {
    const mine = (m) => isEd(m) && m[4] === cmd;
    let r = t.midi([...ED, cmd, ...args, 0xF7]).find(mine);
    for (let i = 0; !r && i < ms; i++) { t.runMs(1); r = t.takeOut().find(mine); }
    return r ? r.slice(5, -1) : null;
  }

  // mixer for a render: mute every listed part (editor TRACK_MIX keeps the level, sets MUTE), as a user would
  function applyMutes(t, mutes) {
    for (let k = 0; k < mutes.length; k++) {
      if (mutes[k] == null) continue;
      const cur = edRequest(t, 28, [k]);                     // TRACK_MIX query: track, level v14, mute
      if (!cur) continue;
      edRequest(t, 28, [k, cur[1], cur[2], mutes[k] ? 1 : 0]);
    }
  }

  // the transport: a twin with st_transport_set (Skiff) moves it itself; Melodee's twin gets its editor commands
  // (74 BANK_SONG op 3 = stop, 33 SONG op 2 = the chain from row 1)
  function stopTransport(t) {
    if (!t.transportSet(0)) edRequest(t, 74, [3]);
  }
  function startTransport(t, how) {
    if (how === "play") {                                   // PLAY on the panel, from the top
      if (t.transport().busy) { stopTransport(t); t.runMs(30); }
      t.press(TwinCore.B.PLAY);
    } else if (how === "song") {                            // the song from the top
      if (t.transport().busy) { stopTransport(t); t.runMs(30); }
      if (!t.transportSet(2)) edRequest(t, 33, [2]);
    } else if (how === "stop") {
      stopTransport(t);
    }
  }

  function encode(l, r, n, format) {
    if (format === "s24") {
      const b = new Uint8Array(n * 6);
      for (let i = 0, o = 0; i < n; i++)
        for (const v of [l[i], r[i]]) {
          let x = Math.round(Math.max(-1, Math.min(1, v)) * 8388607);
          b[o++] = x & 255; b[o++] = (x >> 8) & 255; b[o++] = (x >> 16) & 255;
        }
      return b;
    }
    const f = new Float32Array(n * 2);
    for (let i = 0; i < n; i++) { f[2 * i] = l[i]; f[2 * i + 1] = r[i]; }
    return new Uint8Array(f.buffer);
  }

  // an offline render on its own instance. params: seconds | until_end (+ max_seconds, tail_seconds), start
  // ("none" | "play" | "song"), mutes [bool x 4], format ("f32" | "s24"), chunk (frames). sink(bytes) may return
  // a promise (the next chunk waits). -> {frames, ended, seconds, peak, fs}
  async function renderJob(module, state, p, sink, progress) {
    const t = await TwinCore.create(module, state.memory ? { memory: state.memory } : { flash: state.flash });
    if (!state.memory) t.runMs(p.settle_ms ?? 300);
    if (p.project != null) t.projectLoad(p.project);
    if (p.mutes) applyMutes(t, p.mutes);
    startTransport(t, p.start || "none");
    const fs = t.fs, chunk = p.chunk || fs;
    const maxFrames = Math.round((p.until_end ? (p.max_seconds || 600) : (p.seconds || 10)) * fs);
    const tail = Math.round((p.tail_seconds ?? 2) * fs);
    let frames = 0, ended = false, endAt = -1, peak = 0, wasBusy = t.transport().busy;
    while (frames < maxFrames) {
      let n = Math.min(chunk, maxFrames - frames);
      if (endAt >= 0) n = Math.min(n, endAt - frames);
      if (n <= 0) break;
      const a = t.render(n);
      for (let i = 0; i < n; i++) { const x = Math.max(Math.abs(a.l[i]), Math.abs(a.r[i])); if (x > peak) peak = x; }
      frames += n;
      await sink(encode(a.l, a.r, n, p.format || "f32"));
      if (progress) progress(frames, maxFrames);
      if (p.until_end && endAt < 0) {
        const busy = t.transport().busy;
        if (wasBusy && !busy) { endAt = frames + tail; ended = true; }
        wasBusy = wasBusy || busy;
      }
    }
    return { frames, ended, seconds: frames / fs, peak, fs };
  }

  class TwinHost {
    constructor(env = {}) { this.env = env; this.t = null; this.module = null; }
    async boot(wasm, flash, master = 1023) {
      this.module = wasm instanceof WebAssembly.Module ? wasm : await WebAssembly.compile(wasm);
      this.master = master;
      this.t = await TwinCore.create(this.module, flash ? { flash, master } : { master });
      return this.info();
    }
    info() {
      const t = this.t;
      return { version: t.version(), fs: t.fs, flash_size: t.flashSize(), transport: t.transport(), now: t.ex.st_now(),
               project: t.projectCurrent() };
    }
    out() { const o = this.t.takeOut(); if (o.length && this.env.emitMidi) this.env.emitMidi(o); return o; }
    // complete MIDI messages in -> the messages out (given to env.emitMidi, the twin's MIDI port). wait_ms: an editor request
    // without an answer gets that much device time (the clock may stand still between pulls)
    midi(data, wait_ms = 0) {
      const t = this.t, msgs = TwinCore.splitMidi(Uint8Array.from(data));
      let out = t.midi(...msgs);
      const asked = msgs.filter(isEd).map((m) => m[4]);
      for (let i = 0; i < wait_ms && asked.some((c) => !out.some((m) => isEd(m) && m[4] === c)); i++) {
        t.runMs(1); out = out.concat(t.takeOut());
      }
      if (out.length && this.env.emitMidi) this.env.emitMidi(out);
      return out;
    }
    async handle(c) {
      const t = this.t;
      switch (c.op) {
        case "info": return this.info();
        case "midi": return { n: this.midi(c.data, c.wait_ms ?? 0).length };   // (the messages: env.emitMidi)
        case "keys": t.keys(c.mask); return {};
        case "buttons": t.buttons(c.mask); return {};
        case "encoder": t.encoder(c.id, c.n); return {};
        case "master": t.master(c.value); return {};
        case "press": t.press(c.label, c.ms ?? 40); this.out(); return { transport: t.transport() };
        case "transport_start": startTransport(t, c.how || "play"); this.out(); return { transport: t.transport() };
        case "project_load": { const rc = t.projectLoad(c.slot); this.out(); return { rc, project: t.projectCurrent() }; }
        case "project_used": return { used: [...Array(32).keys()].filter((k) => t.projectUsed(k)) };
        case "transport": return t.transport();
        case "frame": return { fb: t.frame(), gen: t.ex.st_fb_gen(), leds: t.leds() };
        case "flash_get": return { flash: t.getFlash() };
        case "flash_sum": {                               // FNV-1a over the NOR image: has anything been saved?
          const f = new Uint8Array(t.mem, t.ex.st_flash(), t.flashSize());
          let h = 0x811C9DC5;
          for (let i = 0; i < f.length; i++) h = Math.imul(h ^ f[i], 16777619);
          return { sum: h >>> 0 };
        }
        case "flash_set": this.t = await TwinCore.create(this.module, { flash: c.flash, master: this.master }); return this.info();
        case "reset": this.t = await TwinCore.create(this.module, { flash: t.getFlash(), master: this.master }); return this.info();
        case "snapshot": return c.kind === "flash" ? { flash: t.getFlash() } : { memory: t.snapshotMemory() };
        case "run": t.runMs(c.ms); this.out(); return {};
        case "pull": { const a = t.render(c.n); this.out(); return { l: a.l, r: a.r }; }
        default: throw new Error("unknown op " + c.op);
      }
    }
  }
  TwinHost.renderJob = renderJob;
  TwinHost.edRequest = edRequest;
  if (typeof module !== "undefined" && module.exports) module.exports = TwinHost;
  else root.TwinHost = TwinHost;
})(typeof globalThis !== "undefined" ? globalThis : this);
