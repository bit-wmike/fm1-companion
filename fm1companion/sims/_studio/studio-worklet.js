// SPDX-License-Identifier: GPL-3.0-only
// The studio twin's sound: a small FIFO that asks the twin worker for audio (MessagePort, 512 frames at a time) and
// plays it. The twin's clock is the audio clock while this runs (as on the device: the audio ISR drives time).
const TARGET = 2048, CHUNK = 512, SIZE = 8192;
class StudioOut extends AudioWorkletProcessor {
  constructor() {
    super();
    this.l = new Float32Array(SIZE); this.r = new Float32Array(SIZE);
    this.w = 0; this.rd = 0; this.asked = 0; this.port2 = null; this.underruns = 0;
    this.port.onmessage = (e) => {
      if (e.data.type === "port") {
        this.port2 = e.data.port;
        this.port2.onmessage = (m) => {
          const { l, r } = m.data;
          for (let i = 0; i < l.length; i++) { this.l[this.w % SIZE] = l[i]; this.r[this.w % SIZE] = r[i]; this.w++; }
          this.asked -= l.length;
        };
        this.ask();
      }
    };
  }
  ask() {
    while (this.port2 && this.w - this.rd + this.asked < TARGET) { this.port2.postMessage({ need: CHUNK }); this.asked += CHUNK; }
  }
  process(inputs, outputs) {
    const out = outputs[0], n = out[0].length;
    for (let i = 0; i < n; i++) {
      if (this.rd < this.w) { out[0][i] = this.l[this.rd % SIZE]; if (out[1]) out[1][i] = this.r[this.rd % SIZE]; this.rd++; }
      else { out[0][i] = 0; if (out[1]) out[1][i] = 0; if (i === 0) this.underruns++; }
    }
    this.ask();
    return true;
  }
}
registerProcessor("studio-out", StudioOut);
