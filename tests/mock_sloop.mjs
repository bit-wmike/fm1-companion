// Das simulierte FM-1 des SLOOP-Web-Editors (makeMockDevice aus isod89/sloop-fm1 web/editor.html)
// als Prozess – und Werkzeuge, mit denen tests/test_sloop_backup.py die App gegen den Editor-Code prüft.
//   node tests/mock_sloop.mjs <editor.html>                  Mock-Gerät: eine MIDI-Nachricht pro Zeile als Hex
//   node tests/mock_sloop.mjs <editor.html> objects <datei>  backupObjects(): Datei prüfen -> {id: len}
//   node tests/mock_sloop.mjs <editor.html> stringify <datei> JSON.stringify(JSON.parse(datei), null, 1)
//   node tests/mock_sloop.mjs <editor.html> capture          backupCapture() gegen einen neuen Mock (mit Inhalt)
//   node tests/mock_sloop.mjs <editor.html> restore <datei>  backupRestore() in einen neuen Mock, dann capture
//   node tests/mock_sloop.mjs <editor.html> reqs             die Anfragen req.bk* für feste Argumente (Hex)
import { readFileSync } from "node:fs";
import vm from "node:vm";
import readline from "node:readline";
const html = readFileSync(process.argv[2], "utf8");
const proto = html.slice(html.indexOf("/*PROTO-BEGIN*/"), html.indexOf("/*PROTO-END*/"));
const E = vm.runInNewContext(proto + ";({ makeMockDevice, Link, req, parse, CMD, SMP, buildSlot, backupCapture, backupRestore, backupObjects, b64enc, b64dec, crc32 })",
  { setTimeout, clearTimeout, setInterval, clearInterval, console, TextEncoder, TextDecoder });
const mode = process.argv[3] || "";

function attach() {
  const m = E.makeMockDevice({ auto: false });
  const inp = [...m.access.inputs.values()][0], out = [...m.access.outputs.values()][0];
  const link = new E.Link((d) => out.send(d), { timeout: 2000 });
  inp.onmidimessage = (e) => link.receive(e.data);
  return { m, rq: (r, o) => link.request(r, o), done: () => { link.close(); m.stop(); } };
}
/* wie test_web.mjs editorBackup(): ein User-Preset, ein Projekt in Section C, ein Sample in USR2 */
async function fill(rq) {
  await rq(E.req.upStore(3, "BACKUP ME"));
  await rq(E.req.project(1, 2), { timeout: 4000, retries: 0 });
  const s = Int16Array.from({ length: 3000 }, (_, i) => Math.round(8000 * Math.sin(i / 7)));
  const { hdr, data } = E.buildSlot("test", [{ s, root: 60 }]);
  await rq(E.req.smpBegin(1), { timeout: 1000, retries: 0 });
  for (let off = 0; off < data.length; off += 256) await rq(E.req.smpWrite(1, E.SMP.DATA_OFF + off, data.subarray(off, off + 256)), { timeout: 1000 });
  await rq(E.req.smpEnd(1, hdr), { timeout: 2000, retries: 0 });
}
const hex = (r) => Buffer.from([r[0], ...r[1]]).toString("hex");

if (mode === "objects") {
  const m = E.backupObjects(JSON.parse(readFileSync(process.argv[4], "utf8")));
  console.log(JSON.stringify(Object.fromEntries([...m].map(([k, v]) => [k, v.length]))));
} else if (mode === "stringify") {
  process.stdout.write(JSON.stringify(JSON.parse(readFileSync(process.argv[4], "utf8")), null, 1));
} else if (mode === "capture" || mode === "restore") {
  const { rq, done } = attach();
  const info = E.parse[E.CMD.INFO](await rq(E.req.info()));
  if (mode === "capture") await fill(rq);
  else await E.backupRestore(rq, JSON.parse(readFileSync(process.argv[4], "utf8")));
  process.stdout.write(JSON.stringify(await E.backupCapture(rq, info), null, 1));
  done();
} else if (mode === "reqs") {
  const d = Uint8Array.from({ length: 20 }, (_, i) => (i * 29 + 200) & 255);
  console.log(JSON.stringify([E.req.bkList(), E.req.bkGet(33, 70000, 256), E.req.bkGet(2, 512, 77),
    E.req.bkBegin(6, 3080, 0xDEADBEEF), E.req.bkData(6, 2816, d), E.req.bkCommit(6), E.req.bkAbort(11)].map(hex)));
} else {
  const { m } = { m: E.makeMockDevice({ auto: false }) };
  const input = [...m.access.inputs.values()][0];
  const output = [...m.access.outputs.values()][0];
  input.onmidimessage = (e) => process.stdout.write(Buffer.from(e.data).toString("hex") + "\n");
  console.log("# SLOOP-Editor-Mock bereit");
  const rl = readline.createInterface({ input: process.stdin });
  const queue = [];
  let busy = false;
  const pump = () => {
    if (busy || !queue.length) return;
    busy = true;
    output.send(queue.shift());
    setTimeout(() => { busy = false; pump(); }, 2);
  };
  rl.on("line", (l) => { l = l.trim(); if (!l || l.startsWith("#")) return; queue.push(Uint8Array.from(Buffer.from(l, "hex"))); pump(); });
  rl.on("close", () => { m.stop(); process.exit(0); });
}
