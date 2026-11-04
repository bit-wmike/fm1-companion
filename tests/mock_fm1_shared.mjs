// Sound: like mock_fm1.mjs, but with the shared library (command 80, opt.shared). The simulated FM-1 from the Melodee editor itself (makeMockDevice) as a process:
// one MIDI message per line as hex on stdin/stdout. With it the app tests its
// Web MIDI bridge against exactly the device the editor's developer tests against.
//   node tests/mock_fm1.mjs /path/to/editor.html
import { readFileSync } from "node:fs";
import vm from "node:vm";
import readline from "node:readline";
const html = readFileSync(process.argv[2], "utf8");
const proto = html.slice(html.indexOf("/*PROTO-BEGIN*/"), html.indexOf("/*PROTO-END*/"));
const E = vm.runInNewContext(proto + ";({ makeMockDevice })", { setTimeout, clearTimeout, setInterval, clearInterval, console });
const mock = E.makeMockDevice({ auto: false, shared: true });
const input = [...mock.access.inputs.values()][0];
const output = [...mock.access.outputs.values()][0];
input.onmidimessage = (e) => process.stdout.write(Buffer.from(e.data).toString("hex") + "\n");
console.log("# Melodee editor mock ready");
const rl = readline.createInterface({ input: process.stdin });
const queue = [];
let busy = false;
rl.on("line", (l) => { l = l.trim(); if (!l || l.startsWith("#")) return; queue.push(Uint8Array.from(Buffer.from(l, "hex"))); pump(); });
// the mock device holds only one frame at a time (like the real one): deliver one after the other
function pump() {
  if (busy || !queue.length) return;
  busy = true;
  output.send(queue.shift());
  setTimeout(() => { busy = false; pump(); }, 20);
}
