// SPDX-License-Identifier: GPL-3.0-only
// The studio twin's ABI (tools/studio/*_studio.c, exports st_*), wrapped for any JS host: node (tests), the
// twin worker (studio-worker.js) and offline renders. Generic: every firmware glue that exports the st_* set works.
//   const t = await TwinCore.create(wasmBytes, {flash, master});   t.midi([...]) -> [[msg], ...]
(function (root) {
  "use strict";
  const stub = new Proxy({}, { get: () => () => 0 });   // WASI: nothing is printed or opened

  function splitMidi(bytes) {                        // a raw MIDI byte stream -> complete messages
    const out = []; let i = 0;
    while (i < bytes.length) {
      const b = bytes[i];
      if (b === 0xF0) {
        let e = i + 1; while (e < bytes.length && bytes[e] !== 0xF7) e++;
        out.push(Array.from(bytes.subarray(i, Math.min(e + 1, bytes.length)))); i = e + 1; continue;
      }
      if (b < 0x80) { i++; continue; }
      const hi = b & 0xF0;
      const len = b >= 0xF8 || b === 0xF6 ? 1 : (hi === 0xC0 || hi === 0xD0 || b === 0xF1 || b === 0xF3) ? 2 : 3;
      out.push(Array.from(bytes.subarray(i, i + len))); i += len;
    }
    return out;
  }

  class TwinCore {
    static async create(wasm, opts = {}) {
      const module = wasm instanceof WebAssembly.Module ? wasm : await WebAssembly.compile(wasm);
      const t = new TwinCore(module);
      if (opts.memory) t.restoreMemory(opts.memory);   // a fork: the whole RAM of another instance (already booted)
      else {
        if (opts.flash) t.setFlash(opts.flash); else t.ex.st_flash_erase();
        if (opts.boot !== false) t.boot(opts.master ?? 1023);
      }
      return t;
    }
    constructor(module) {
      this.module = module;
      this.instance = new WebAssembly.Instance(module, { env: stub, wasi_snapshot_preview1: stub });
      this.ex = this.instance.exports;
      this.ex._initialize();
      this.fs = this.ex.st_fs();
    }
    get mem() { return this.ex.memory.buffer; }
    boot(master = 1023) { return this.ex.st_boot(master) === 1; }
    version() {
      const p = this.ex.st_version(), m = new Uint8Array(this.mem); let e = p; while (m[e]) e++;
      return String.fromCharCode(...m.subarray(p, e));
    }
    flashSize() { return this.ex.st_flash_size(); }
    getFlash() { return new Uint8Array(this.mem, this.ex.st_flash(), this.flashSize()).slice(); }
    setFlash(bytes) {
      const n = this.flashSize(), b = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
      if (b.length !== n) throw new Error(`flash image must be ${n} bytes, not ${b.length}`);
      new Uint8Array(this.mem, this.ex.st_flash(), n).set(b);
    }
    snapshotMemory() { return new Uint8Array(this.mem).slice(); }
    restoreMemory(bytes) {
      const need = bytes.byteLength, have = this.mem.byteLength;
      if (need > have) this.ex.memory.grow(Math.ceil((need - have) / 65536));
      new Uint8Array(this.mem).set(bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes));
    }
    // complete MIDI messages in (SysEx, notes, clock …) -> the messages the device sent meanwhile
    midi(...msgs) {
      const flat = [].concat(...msgs.map((m) => Array.from(m)));
      const inp = new Uint8Array(this.mem, this.ex.st_in(), flat.length); inp.set(flat);
      this.ex.st_midi_in(flat.length);
      return this.takeOut();
    }
    takeOut() {
      const n = this.ex.st_out_len();
      if (!n) return [];
      const bytes = new Uint8Array(this.mem, this.ex.st_out(), n).slice();
      this.ex.st_out_clear();
      return splitMidi(bytes);
    }
    keys(m) { this.ex.st_keys(m >>> 0); }
    buttons(m) { this.ex.st_buttons(m >>> 0); }
    encoder(id, n) { this.ex.st_encoder(id, n | 0); }
    master(v) { this.ex.st_master(v | 0); }
    // a panel button by label (panel.c: B_PLAY 10, B_REC 11, B_SAVE 7 …): down for ms, then up (as a finger)
    press(label, ms = 40) {
      const bit = 1 << this.ex.st_panel_button(label);
      this.buttons(bit); this.runMs(ms); this.buttons(0); this.runMs(20);
    }
    projectLoad(slot) { return this.ex.st_project_load(slot); }       // 0 ok, 1 empty, 2 stop first
    projectUsed(slot) { return !!this.ex.st_project_used(slot); }
    projectCurrent() { return this.ex.st_project_current(); }
    transport() { const t = this.ex.st_transport(); return { playing: !!(t & 1), chain: !!(t & 2), busy: !!(t & 4), rec: !!(t & 8) }; }
    // (optional export, Skiff's and JIANT FM's twins and later) the transport without the firmware's own editor command:
    // how 0 stop, 1 play as the panel's PLAY, 2 the song from the top, 3 restart. -> false: not exported (Melodee)
    transportSet(how) { if (!this.ex.st_transport_set) return false; this.ex.st_transport_set(how >>> 0); return true; }
    // (optional export, CoralFlower's twin: no editor TRACK_MIX) part k held silent (on) or not, for the stems.
    // -> false: not exported (the host uses the editor's TRACK_MIX)
    muteSet(k, on) { if (!this.ex.st_track_mute_set) return false; this.ex.st_track_mute_set(k >>> 0, on ? 1 : 0); return true; }
    runMs(ms) { this.ex.st_run_ms(ms >>> 0); }
    // n frames -> {l, r} (copies); chunks of up to 4096 frames
    render(n) {
      const l = new Float32Array(n), r = new Float32Array(n);
      for (let k = 0; k < n; k += 4096) {
        const m = Math.min(4096, n - k);
        this.ex.st_render(m);
        l.set(new Float32Array(this.mem, this.ex.st_out_l(), m), k);
        r.set(new Float32Array(this.mem, this.ex.st_out_r(), m), k);
      }
      return { l, r };
    }
    frame() { return new Uint16Array(this.mem, this.ex.st_fb(), 240 * 240).slice(); }
    leds() { return { keys: this.ex.st_led_keys() >>> 0, buttons: this.ex.st_led_buttons() >>> 0 }; }
  }
  TwinCore.splitMidi = splitMidi;
  TwinCore.B = { FX: 0, SCL: 1, ENV: 2, LFO: 3, EDIT: 4, GLO: 5, HOME: 6, SAVE: 7, ARP: 8, SEQ: 9, PLAY: 10, REC: 11 };
  if (typeof module !== "undefined" && module.exports) module.exports = TwinCore;
  else root.TwinCore = TwinCore;
})(typeof globalThis !== "undefined" ? globalThis : this);
