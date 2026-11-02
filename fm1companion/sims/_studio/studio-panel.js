// SPDX-License-Identifier: GPL-3.0-only
// The visible FM-1 of the Studio page: screen, LEDs, keys, buttons and encoders of the twin, drawn like the
// Melodee simulator (sims/melodee/index.html) and played with mouse, touch and the computer keyboard. It talks to
// the twin worker directly (studio.js: toWorker) – no round trip through Python for keys or the screen.
// Python switches it on with the command {op: "panel", on, bg, fg, dim}; Space / Ctrl+Z / Ctrl+Shift+Z go to
// Python as {ev: "ui", action}.
"use strict";
(function () {
  const BTN = { "OCT-": 0, "OCT+": 1, FX: 2, SCL: 3, ENV: 4, LFO: 5, EDIT: 6, GLO: 7, HOME: 8, SAVE: 9, ARP: 10, SEQ: 11, PLAY: 12, REC: 13 };
  const ENC = { SELECT: 0, ALGORITHM: 1, KNOB1: 2, KNOB2: 3, KNOB3: 4, KNOB4: 5, PRESETS: 6 };
  const WHITE_K = [0, 2, 4, 6, 7, 9, 11, 12, 14, 16, 18, 19, 21, 23, 24, 26];
  const BLACK_K = [1, 3, 5, 8, 10, 13, 15, 17, 20, 22, 25];
  const BLACK_AFTER = [0, 1, 2, 4, 5, 7, 8, 9, 11, 12, 14];
  const TRACKER = { KeyZ: 0, KeyS: 1, KeyX: 2, KeyD: 3, KeyC: 4, KeyF: 5, KeyV: 6, KeyB: 7, KeyH: 8, KeyN: 9, KeyJ: 10, KeyM: 11,
                    Comma: 12, KeyL: 13, Period: 14, Semicolon: 15, Slash: 16, KeyQ: 12, Digit2: 13, KeyW: 14, Digit3: 15,
                    KeyE: 16, Digit4: 17, KeyR: 18, KeyT: 19, Digit6: 20, KeyY: 21, Digit7: 22, KeyU: 23, KeyI: 24, Digit9: 25, KeyO: 26 };
  const CAP = { Comma: ",", Period: ".", Semicolon: ";", Slash: "/" };

  let device = null, screen = null, g2d = null, img = null, on = false, lastGen = -1, polling = false, master = 1023;
  const keys = [], buttons = [], knobs = [];
  const heldKeys = new Map(), heldBtns = new Map();
  let keyMask = 0, btnMask = 0;

  const css = `
  html, body { margin: 0; height: 100%; overflow: hidden; background: var(--bg); color: var(--fg); font: 13px sans-serif; }
  :root { --bg: #202124; --fg: #ddd; --body: #3a3d42; --tray: #26282c; --knob: #1f2124; --btn: #2e3135; --label: #e8e8ea;
          --key-w: #d9dadc; --key-b: #c9cacd; --lit: #ffb347; }
  #wrap { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; }
  .device { position: relative; aspect-ratio: 1000 / 640; background: var(--body); border-radius: 4.4% / 6.9%;
            user-select: none; -webkit-user-select: none; touch-action: manipulation; container-type: inline-size; }
  .device > * { position: absolute; }
  .tray { background: var(--tray); border-radius: 14px; }
  .bezel { background: #111214; border-radius: 11%; }
  canvas { image-rendering: pixelated; background: #000; }
  .label { color: var(--label); font-size: 1.7cqw; font-weight: 600; letter-spacing: .05em; text-align: center; pointer-events: none; white-space: nowrap; }
  .knob { border-radius: 50%; background: var(--knob); box-shadow: 0 0 0 .5cqw var(--tray), inset 0 0 0 .2cqw #44474c;
          cursor: ns-resize; touch-action: none; transform: rotate(var(--angle, 0deg)); outline: none; }
  .knob:focus-visible { box-shadow: 0 0 0 .5cqw var(--lit); }
  .knob::after { content: ""; position: absolute; left: 50%; top: 8%; width: .3cqw; height: 22%; margin-left: -.15cqw; background: var(--label); border-radius: 2px; }
  .btn { background: var(--btn); border-radius: 9px; box-shadow: inset 0 0 0 1.5px #4a4d52; color: var(--label); font-size: 1.4cqw;
         font-weight: 600; display: flex; align-items: center; justify-content: center; cursor: pointer; touch-action: none; }
  .btn.held { filter: brightness(1.5); }
  .btn.lit { color: #1a1206; background: var(--lit); box-shadow: 0 0 1.4cqw var(--lit); }
  .key { border-radius: 999px; background: var(--key-w); cursor: pointer; touch-action: none; display: flex; align-items: flex-end;
         justify-content: center; padding-bottom: .8cqw; color: #55575c; font-size: 1.3cqw; font-weight: 600; box-sizing: border-box; }
  .key.black { background: var(--key-b); }
  .key.held { filter: brightness(.8); }
  .key.lit { background: var(--lit); box-shadow: 0 0 1.4cqw var(--lit); }
  #state { display: none; }`;

  function add(cls, x, y, w, h, text = "", tag = "div") {
    const el = document.createElement(tag);
    el.className = cls;
    el.textContent = text;
    Object.assign(el.style, { left: `${x / 10}%`, top: `${y / 6.4}%`, width: `${w / 10}%`, height: `${h / 6.4}%` });
    device.appendChild(el);
    return el;
  }

  function fit() {                                   // the whole device always visible, centred
    if (!device) return;
    const W = innerWidth - 8, H = innerHeight - 8;
    const w = Math.max(200, Math.min(W, H * 1000 / 640));
    device.style.width = w + "px";
  }

  function build() {
    const st = document.createElement("style");
    st.textContent = css;
    document.head.appendChild(st);
    const wrap = document.createElement("div");
    wrap.id = "wrap";
    device = document.createElement("div");
    device.className = "device";
    device.id = "device";
    wrap.appendChild(device);
    document.body.appendChild(wrap);
    add("tray", 58, 258, 210, 58);
    add("tray", 568, 160, 400, 140);
    add("bezel", 300, 44, 236, 236);
    add("tray", 36, 340, 928, 270);
    screen = add("", 306, 50, 224, 224, "", "canvas");
    screen.width = screen.height = 240;
    g2d = screen.getContext("2d");
    img = g2d.createImageData(240, 240);
    const knob = (x, y, r, label, id) => {
      add("label", x - 60, y - r - 34, 120, 22, label);
      const el = add("knob", x - r, y - r, 2 * r, 2 * r);
      el.tabIndex = 0;
      el.setAttribute("role", "slider");
      el.setAttribute("aria-label", label);
      el.title = label + " – ziehen, Mausrad oder Pfeiltasten";
      const k = { el, id, angle: id === "master" ? -135 + 270 * master / 1023 : 0 };
      el.style.setProperty("--angle", `${k.angle}deg`);
      knobs.push(k);
    };
    knob(105, 100, 24, "MASTER", "master");
    knob(215, 100, 24, "SELECT", ENC.SELECT);
    knob(105, 205, 24, "PRESETS", ENC.PRESETS);
    knob(215, 205, 24, "ALGORITHM", ENC.ALGORITHM);
    for (let i = 0; i < 4; i++) knob(610 + i * 104, 100, 22, `KNOB ${i + 1}`, ENC[`KNOB${i + 1}`]);
    const button = (x, y, w, h, label) => {
      const el = add("btn", x, y, w, h, label);
      el.setAttribute("role", "button");
      buttons[BTN[label]] = el;
    };
    button(72, 268, 82, 38, "OCT-");
    button(170, 268, 82, 38, "OCT+");
    ["FX", "SCL", "ENV", "LFO", "EDIT", "GLO"].forEach((b, i) => button(582 + i * 63, 174, 54, 48, b));
    ["HOME", "SAVE", "ARP", "SEQ", "PLAY", "REC"].forEach((b, i) => button(582 + i * 63, 236, 54, 48, b));
    const pitch = (928 - 24) / 16, kw = pitch * 0.78;
    for (let i = 0; i < 16; i++) keys[WHITE_K[i]] = add("key white", 48 + i * pitch + (pitch - kw) / 2, 340 + 270 * 0.43, kw, 270 * 0.52);
    BLACK_AFTER.forEach((a, k) => { keys[BLACK_K[k]] = add("key black", 48 + (a + 1) * pitch - kw / 2, 340 + 270 * 0.06, kw, 270 * 0.33); });
    for (const [code, k] of Object.entries(TRACKER)) keys[k].textContent += (keys[k].textContent ? " " : "") + (CAP[code] || code.slice(-1));
    keys.forEach((el, i) => hold(el, heldKeys, i));
    buttons.forEach((el, i) => hold(el, heldBtns, i));
    for (const ev of ["pointerup", "pointercancel", "lostpointercapture"])
      device.addEventListener(ev, (e) => { if (heldKeys.delete("p" + e.pointerId) | heldBtns.delete("p" + e.pointerId)) sync(); }, true);
    for (const k of knobs) knobInput(k);
    addEventListener("resize", fit);
    addEventListener("keydown", (e) => onKey(e, true));
    addEventListener("keyup", (e) => onKey(e, false));
    addEventListener("blur", () => { heldKeys.clear(); heldBtns.clear(); sync(); });
    fit();
  }

  function sync() {
    let k = 0, b = 0;
    for (const v of heldKeys.values()) k |= 1 << v;
    for (const v of heldBtns.values()) b |= 1 << v;
    keys.forEach((el, i) => el.classList.toggle("held", !!(k & (1 << i))));
    buttons.forEach((el, i) => el.classList.toggle("held", !!(b & (1 << i))));
    if (!worker) return;
    if (k !== keyMask) toWorker({ op: "keys", mask: (keyMask = k) });
    if (b !== btnMask) toWorker({ op: "buttons", mask: (btnMask = b) });
  }
  function hold(el, map, v) {
    el.addEventListener("contextmenu", (e) => e.preventDefault());
    el.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      el.setPointerCapture(e.pointerId);
      map.set("p" + e.pointerId, v);
      sync();
    });
  }
  function turn(k, n) {
    if (!n || !worker) return;
    if (k.id === "master") {
      master = Math.max(0, Math.min(1023, master + n * 16));
      k.angle = -135 + 270 * master / 1023;
      toWorker({ op: "master", value: master });
    } else {
      k.angle = (k.angle + n * 18) % 360;
      toWorker({ op: "encoder", id: k.id, n });
    }
    k.el.style.setProperty("--angle", `${k.angle}deg`);
  }
  function knobInput(k) {
    let drag = null, wheel = 0;
    k.el.addEventListener("pointerdown", (e) => { e.preventDefault(); k.el.setPointerCapture(e.pointerId); drag = { y: e.clientY, acc: 0 }; });
    k.el.addEventListener("pointermove", (e) => {
      if (!drag) return;
      drag.acc += drag.y - e.clientY;
      drag.y = e.clientY;
      const n = Math.trunc(drag.acc / 6);
      if (n) { drag.acc -= n * 6; turn(k, n); }
    });
    for (const ev of ["pointerup", "pointercancel"]) k.el.addEventListener(ev, () => { drag = null; });
    k.el.addEventListener("wheel", (e) => {
      e.preventDefault();
      const dy = e.deltaMode === 1 ? e.deltaY * 33 : e.deltaY;
      if (Math.abs(dy) >= 50) return turn(k, dy < 0 ? 1 : -1);
      wheel -= dy;
      const n = Math.trunc(wheel / 25);
      if (n) { wheel -= n * 25; turn(k, n); }
    }, { passive: false });
    k.el.addEventListener("keydown", (e) => {
      const n = { ArrowUp: 1, ArrowRight: 1, ArrowDown: -1, ArrowLeft: -1 }[e.key];
      if (n) { e.preventDefault(); e.stopPropagation(); turn(k, n); }
    });
  }
  function onKey(e, down) {
    if (!on) return;
    if (down && e.ctrlKey && !e.altKey && e.code === "KeyZ") {          // the Studio page's undo / redo
      e.preventDefault();
      send({ ev: "ui", action: e.shiftKey ? "redo" : "undo" });
      return;
    }
    if (down && e.ctrlKey && !e.altKey && e.code === "KeyY") { e.preventDefault(); send({ ev: "ui", action: "redo" }); return; }
    if (e.code === "Space" && !e.ctrlKey && !e.altKey && !e.metaKey) {  // play / stop
      e.preventDefault();
      if (down && !e.repeat) send({ ev: "ui", action: "toggle_play" });
      return;
    }
    const k = TRACKER[e.code];
    if (k === undefined || e.metaKey || e.ctrlKey || e.altKey || (e.target.closest && e.target.closest("[role=slider]"))) return;
    e.preventDefault();
    if (e.repeat) return;
    if (down) heldKeys.set(e.code, k); else heldKeys.delete(e.code);
    sync();
  }

  function draw(fb) {
    const d = img.data;
    for (let i = 0; i < 240 * 240; i++) {
      const c = ((fb[i] & 0xff) << 8) | (fb[i] >> 8);
      d[4 * i] = (c >> 11) * 255 / 31;
      d[4 * i + 1] = ((c >> 5) & 63) * 255 / 63;
      d[4 * i + 2] = (c & 31) * 255 / 31;
      d[4 * i + 3] = 255;
    }
    g2d.putImageData(img, 0, 0);
  }
  async function poll() {                            // ~30 frames/s while visible
    if (polling) return;
    polling = true;
    try {
      while (on) {
        const t0 = performance.now();
        if (worker && document.visibilityState !== "hidden") {
          const m = await toWorker({ op: "frame" });
          if (m.ok && m.r) {
            if (m.r.gen !== lastGen) { lastGen = m.r.gen; draw(m.r.fb); }
            const L = m.r.leds || {};
            keys.forEach((el, i) => el.classList.toggle("lit", !!(L.keys & (1 << i))));
            buttons.forEach((el, i) => el.classList.toggle("lit", !!(L.buttons & (1 << i))));
          }
        }
        await new Promise((r) => setTimeout(r, Math.max(5, 33 - (performance.now() - t0))));
      }
    } finally { polling = false; }
  }

  // {op: "panel", on, bg, fg} from studio.js
  self.studioPanel = function (c) {
    if (!device) build();
    const root = document.documentElement.style;
    if (c.bg) { root.setProperty("--bg", c.bg); document.body.style.background = c.bg; }
    if (c.fg) root.setProperty("--fg", c.fg);
    on = !!c.on;
    lastGen = -1;
    if (on) poll(); else { heldKeys.clear(); heldBtns.clear(); sync(); }
    return { on };
  };
})();
