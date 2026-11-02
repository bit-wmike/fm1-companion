// klang: wie mock_fm1.mjs, aber mit der gemeinsamen Bibliothek (Befehl 80, opt.shared). Das simulierte FM-1 aus dem Melodee-Editor selbst (makeMockDevice) als Prozess:
// eine MIDI-Nachricht pro Zeile als Hex auf stdin/stdout. Damit testet die App ihre
// Web-MIDI-Brücke gegen genau das Gerät, gegen das der Editor-Entwickler testet.
//   node tests/mock_fm1.mjs /pfad/zu/editor.html
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
console.log("# Melodee-Editor-Mock bereit");
const rl = readline.createInterface({ input: process.stdin });
const queue = [];
let busy = false;
rl.on("line", (l) => { l = l.trim(); if (!l || l.startsWith("#")) return; queue.push(Uint8Array.from(Buffer.from(l, "hex"))); pump(); });
// das Mock-Gerät hält nur einen Frame gleichzeitig (wie das echte): nacheinander zustellen
function pump() {
  if (busy || !queue.length) return;
  busy = true;
  output.send(queue.shift());
  setTimeout(() => { busy = false; pump(); }, 20);
}
