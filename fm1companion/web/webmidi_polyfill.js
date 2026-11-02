// FM-1 Companion: Web-MIDI für den eingebetteten Editor.
// Qt WebEngine hat kein Web-MIDI. Dieses Skript stellt navigator.requestMIDIAccess
// bereit und leitet alle Nachrichten über QWebChannel an die App weiter, die den
// echten MIDI-Port besitzt (python-rtmidi). Die API folgt der Web-MIDI-Spezifikation
// so weit, wie die Editoren der Felucca-Familie sie benutzen.
(function () {
  "use strict";
  if (window.__fm1MidiBridge) return;
  window.__fm1MidiBridge = true;

  let bridgeReady = null;
  function bridge() {
    if (!bridgeReady) {
      bridgeReady = new Promise((resolve, reject) => {
        const fail = (msg) => { bridgeReady = null; reject(new DOMException(msg, "NotSupportedError")); };
        const timer = setTimeout(() => fail("FM-1 Companion: die App antwortet nicht (Kanal weg?) – Seite neu laden"), 5000);
        const start = () => {
          if (!(window.qt && window.qt.webChannelTransport)) { clearTimeout(timer); fail("FM-1 Companion: kein Kanal zur App"); return; }
          try {
            new QWebChannel(window.qt.webChannelTransport, (ch) => {
              clearTimeout(timer);
              if (ch && ch.objects && ch.objects.fm1midi) resolve(ch.objects.fm1midi);
              else fail("FM-1 Companion: MIDI-Brücke nicht angemeldet");
            });
          } catch (err) { clearTimeout(timer); fail("FM-1 Companion: " + err); }
        };
        if (window.qt && window.qt.webChannelTransport) start();
        else if (document.readyState !== "loading") start();
        else window.addEventListener("DOMContentLoaded", start, { once: true });
      });
    }
    return bridgeReady;
  }

  class Emitter {
    constructor() { this._l = {}; }
    addEventListener(t, f) { (this._l[t] = this._l[t] || []).push(f); }
    removeEventListener(t, f) { this._l[t] = (this._l[t] || []).filter((x) => x !== f); }
    _emit(t, e) {
      const h = this["on" + t];
      if (typeof h === "function") { try { h.call(this, e); } catch (err) { console.error(err); } }
      for (const f of this._l[t] || []) { try { f.call(this, e); } catch (err) { console.error(err); } }
    }
  }

  class Port extends Emitter {
    constructor(access, d, type) {
      super();
      this._access = access;
      this.id = d.id; this.name = d.name; this.manufacturer = d.manufacturer || "M-VAVE";
      this.version = ""; this.type = type; this.state = d.state || "connected"; this.connection = "closed";
      this.onstatechange = null;
    }
    open() { this.connection = "open"; return Promise.resolve(this); }
    close() { this.connection = "closed"; return Promise.resolve(this); }
  }
  class Input extends Port {
    constructor(a, d) { super(a, d, "input"); this._onmsg = null; }
    get onmidimessage() { return this._onmsg; }
    set onmidimessage(f) { this._onmsg = f; if (f) this.connection = "open"; }
  }
  class Output extends Port {
    constructor(a, d) { super(a, d, "output"); }
    send(data, _timestamp) {
      if (this.state !== "connected") throw new DOMException("Port getrennt", "InvalidStateError");
      const bytes = Array.from(data, (x) => x & 0xff);
      if (bytes.length && bytes[0] === 0xf0 && !this._access.sysexEnabled)
        throw new DOMException("SysEx ohne sysex:true angefordert", "InvalidAccessError");
      this.connection = "open";
      try { this._access._bridge.send(this.id, bytes); }
      catch (err) { throw new DOMException("FM-1 Companion: Senden fehlgeschlagen – " + err, "InvalidStateError"); }
    }
    clear() {}
  }

  class Access extends Emitter {
    constructor(b, sysex) {
      super();
      this._bridge = b; this.sysexEnabled = !!sysex;
      this.inputs = new Map(); this.outputs = new Map(); this.onstatechange = null;
    }
    _sync(list) {
      const seen = new Set();
      for (const d of list) {
        seen.add(d.type + d.id);
        const map = d.type === "input" ? this.inputs : this.outputs;
        let p = map.get(d.id);
        if (!p) {
          p = d.type === "input" ? new Input(this, d) : new Output(this, d);
          map.set(d.id, p);
          if (d.state === "connected") this._state(p);
        } else if (p.state !== d.state) {
          p.state = d.state;
          if (d.state === "disconnected") p.connection = "closed";
          this._state(p);
        }
      }
      for (const map of [this.inputs, this.outputs]) {
        for (const [id, p] of map) {
          if (!seen.has(p.type + id) && p.state !== "disconnected") { p.state = "disconnected"; p.connection = "closed"; this._state(p); }
        }
      }
    }
    _state(p) {
      const e = { port: p, type: "statechange" };
      p._emit("statechange", e);
      this._emit("statechange", e);
    }
  }

  navigator.requestMIDIAccess = async function (opts) {
    const b = await bridge();
    const access = new Access(b, opts && opts.sysex);
    const list = JSON.parse(await new Promise((r, rej) => {
      const t = setTimeout(() => rej(new DOMException("FM-1 Companion: keine Antwort von der App", "NotSupportedError")), 5000);
      b.ports((v) => { clearTimeout(t); r(v); });
    }));
    access._sync(list);
    b.portsChanged.connect((json) => access._sync(JSON.parse(json)));
    b.midiIn.connect((id, data) => {
      const p = access.inputs.get(id);
      if (!p || p.state !== "connected") return;
      p._emit("midimessage", { data: Uint8Array.from(data), timeStamp: performance.now(), target: p });
    });
    return access;
  };
  window.__fm1Companion = { version: 1 };
})();
