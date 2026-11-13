// SPDX-License-Identifier: GPL-3.0-only
// The studio twin's sound: a small FIFO that asks the twin worker for audio (MessagePort, 512 frames at a time) and
// plays it. The twin's clock is the audio clock while this runs (as on the device: the audio ISR drives time).
// When the worker is late the FIFO runs dry (a dropout: silence is played). Dropouts are counted and reported to the
// page ({type: "stats", underruns, target}, at most once a second), and after three within ten seconds the FIFO's
// target doubles (2048 -> 4096 -> 8192 frames = 46 / 93 / 186 ms): more delay, fewer crackles on a busy computer.
const CHUNK = 512, SIZE = 32768, TARGET_MIN = 2048, TARGET_MAX = 8192;
class StudioOut extends AudioWorkletProcessor {
  constructor() {
    super();
    this.l = new Float32Array(SIZE); this.r = new Float32Array(SIZE);
    this.w = 0; this.rd = 0; this.asked = 0; this.port2 = null;
    this.target = TARGET_MIN; this.underruns = 0; this.started = false; this.dry = false;
    this.recent = []; this.frames = 0; this.sinceReport = 0; this.reported = -1;
    this.port.onmessage = (e) => {
      if (e.data.type === "port") {
        this.port2 = e.data.port;
        this.port2.onmessage = (m) => {
          const { l, r } = m.data;
          for (let i = 0; i < l.length; i++) { this.l[this.w % SIZE] = l[i]; this.r[this.w % SIZE] = r[i]; this.w++; }
          this.asked -= l.length;
          this.started = true;
        };
        this.ask();
      }
    };
  }
  ask() {
    while (this.port2 && this.w - this.rd + this.asked < this.target) { this.port2.postMessage({ need: CHUNK }); this.asked += CHUNK; }
  }
  dropout() {                                         // once per gap, not per block
    this.underruns++;
    this.recent = this.recent.filter((t) => this.frames - t < 10 * sampleRate);
    this.recent.push(this.frames);
    if (this.recent.length >= 3 && this.target < TARGET_MAX) { this.target *= 2; this.recent = []; }
  }
  process(inputs, outputs) {
    const out = outputs[0], n = out[0].length;
    for (let i = 0; i < n; i++) {
      if (this.rd < this.w) { out[0][i] = this.l[this.rd % SIZE]; if (out[1]) out[1][i] = this.r[this.rd % SIZE]; this.rd++; this.dry = false; }
      else {
        out[0][i] = 0; if (out[1]) out[1][i] = 0;
        if (this.started && !this.dry) { this.dry = true; this.dropout(); }
      }
    }
    this.frames += n; this.sinceReport += n;
    if (this.sinceReport >= sampleRate) {
      this.sinceReport = 0;
      const key = this.underruns * 16384 + this.target;
      if (this.underruns && key !== this.reported) {
        this.reported = key;
        this.port.postMessage({ type: "stats", underruns: this.underruns, target: this.target });
      }
    }
    this.ask();
    return true;
  }
}
registerProcessor("studio-out", StudioOut);
