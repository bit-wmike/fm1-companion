# SPDX-License-Identifier: GPL-3.0-only
"""Den Original-Web-Editor der installierten Firmware lokal bereitstellen.

Der Editor kommt aus dem Git-Repo der Firmware, genau in der Version, die auf
dem Gerät läuft (git archive <tag> web/). Er wird über einen kleinen Server
nur für diesen Rechner ausgeliefert (127.0.0.1, zufälliger Port) – nötig, weil
die Editoren JS-Module nachladen, was bei file:// blockiert ist. Internet
braucht er dabei nicht; nur das erste Bereitstellen einer neuen Version holt
die Dateien aus dem Repo.
"""
from __future__ import annotations

import functools
import io
import json
import posixpath
import queue
import re
import urllib.request
import shutil
import subprocess
import tarfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .config import DATA_DIR
from .firmware import REPOS_DIR, FirmwareSpec, ensure_tools

SITES = DATA_DIR / "editor"
SIMS = Path(__file__).resolve().parent / "sims"     # mitgelieferte Simulatoren (WASM), unter /sims/<name>/
SKIP = ("web/emu/", "web/learn/", "web/node_modules/", "web/test", "web/conv/")


def site_dir(key: str, tag: str) -> Path:
    return SITES / key / tag


def prepare(spec: FirmwareSpec, tag: str, log=print) -> Path:
    """-> Ordner mit web/editor.html dieser Version."""
    d = site_dir(spec.key, tag)
    marker = d / ".complete"
    if marker.exists() and (d / spec.editor).exists():
        return d
    repo = REPOS_DIR / spec.key
    if not (repo / ".git").exists():
        ensure_tools(spec, tag, log)
    log(f"stelle den Editor von {spec.name} {tag} bereit …")
    folder = str(Path(spec.editor).parent) or "web"
    r = subprocess.run(["git", "archive", "--format=tar", tag, folder], cwd=repo, capture_output=True, timeout=300)
    if r.returncode:
        # Tag lokal unbekannt: einmal nachholen
        subprocess.run(["git", "fetch", "--tags", "--quiet"], cwd=repo, timeout=180)
        r = subprocess.run(["git", "archive", "--format=tar", tag, folder], cwd=repo, capture_output=True, timeout=300)
        if r.returncode:
            raise RuntimeError(f"Editor für {tag} nicht verfügbar: {r.stderr.decode(errors='replace').strip()[:200]}")
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(r.stdout)) as tf:
        members = [m for m in tf.getmembers() if not any(m.name.startswith(s) for s in SKIP)
                   and not m.name.startswith("/") and ".." not in Path(m.name).parts]
        try:
            tf.extractall(d, members=members, filter="data")
        except TypeError:                      # Python < 3.11.4: ohne Filter keine Links/Gerätedateien
            tf.extractall(d, members=[m for m in members if m.isfile() or m.isdir()])
    if not (d / spec.editor).exists():
        raise RuntimeError(f"{spec.name} {tag} enthält keinen Editor unter {spec.editor}")
    marker.write_text(tag)
    return d


def manual_index(spec: FirmwareSpec, tag: str) -> Path | None:
    """Handbuch dieser Version (liegt neben dem Editor unter web/manual/), falls vorhanden."""
    p = site_dir(spec.key, tag) / Path(spec.editor).parent / "manual" / "index.html"
    return p if p.exists() else None


def missing_manual_images(index: Path) -> list[str]:
    names = re.findall(r'<img[^>]*\bsrc="([^"/:]+\.png)"', index.read_text("utf-8", errors="replace"))
    return [n for n in dict.fromkeys(names) if not (index.parent / n).exists()]


def fetch_manual_images(spec: FirmwareSpec, index: Path, log=print, base: str = "") -> int:
    """Die Bildschirmfotos des Handbuchs liegen nicht im Repo; einmal aus dem Online-Handbuch holen und
    neben die lokale Kopie legen. Danach geht das Handbuch offline. Fehler sind kein Abbruch."""
    base = base or spec.manual_online
    if not base:
        return 0
    got = 0
    for name in missing_manual_images(index):
        try:
            req = urllib.request.Request(base.rstrip("/") + "/" + name, headers={"User-Agent": "fm1-companion"})
            with urllib.request.urlopen(req, timeout=15) as r:
                data = r.read(8 * 1024 * 1024)
            if not data.startswith(b"\x89PNG"):
                continue
            tmp = index.parent / (name + ".part")
            tmp.write_bytes(data)
            tmp.replace(index.parent / name)
            got += 1
        except Exception:                       # offline oder Bild online nicht vorhanden
            if got == 0:
                break                           # gleich das erste scheitert: nicht 59-mal warten
    return got


def available(key: str) -> list[str]:
    root = SITES / key
    from .firmware import version_key
    return sorted((p.name for p in root.iterdir() if (p / ".complete").exists()), key=version_key) if root.exists() else []


class MidiRelay:
    """MIDI aus der App an Seiten außerhalb der App (Firefox): Server-Sent Events auf /_fm1/midi."""

    def __init__(self):
        self._clients: list[queue.Queue] = []
        self.closed = False

    def publish(self, data):
        msg = json.dumps([int(x) & 0xFF for x in data])
        for q in list(self._clients):
            if q.qsize() < 512:
                q.put(msg)

    @property
    def clients(self) -> int:
        return len(self._clients)

    def serve(self, handler):
        q: queue.Queue = queue.Queue()
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        self._clients.append(q)
        try:
            handler.wfile.write(b": fm1-companion\n\n")
            handler.wfile.flush()
            while not self.closed:
                try:
                    line = "data: " + q.get(timeout=10) + "\n\n"
                except queue.Empty:
                    line = ": ping\n\n"
                handler.wfile.write(line.encode())
                handler.wfile.flush()
        except OSError:                         # Browser hat die Seite geschlossen
            pass
        finally:
            self._clients.remove(q)


class _Quiet(SimpleHTTPRequestHandler):
    relay: MidiRelay | None = None
    exchange: "Exchange | None" = None

    def log_message(self, *a):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def translate_path(self, path):
        p = unquote(urlsplit(path).path)
        if p.startswith("/sims/"):
            rel = posixpath.normpath(p[len("/sims/"):]).lstrip("/")
            if rel.startswith("..") or rel == ".":
                return str(SIMS / "__missing__")
            return str(SIMS.joinpath(*rel.split("/")))
        return super().translate_path(path)

    def _host_ok(self) -> bool:
        """Schutz gegen DNS-Rebinding: nur Anfragen an 127.0.0.1:<port> oder localhost:<port>, sonst 421."""
        port = self.server.server_address[1]
        host = (self.headers.get("Host") or "").strip().lower()
        if host in (f"127.0.0.1:{port}", f"localhost:{port}"):
            return True
        self.send_error(421, "Misdirected Request")
        return False

    def do_HEAD(self):
        if self._host_ok():
            super().do_HEAD()

    def do_GET(self):
        if not self._host_ok():
            return
        path = urlsplit(self.path).path
        if path == "/_fm1/midi" and self.relay is not None:
            self.relay.serve(self)
            return
        if path.startswith("/_fm1/blob/") and self.exchange is not None:
            data = self.exchange.blob(path[len("/_fm1/blob/"):])
            if data is None:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        super().do_GET()

    def do_POST(self):
        """Daten einer eigenen Seite an die App (Studio: PCM beim Rendern, Flash-Abzug) – nur mit Einmal-Schlüssel."""
        if not self._host_ok():
            return
        u = urlsplit(self.path)
        sink = self.exchange.sink(u.path[len("/_fm1/sink/"):]) if self.exchange is not None and \
            u.path.startswith("/_fm1/sink/") else None
        if sink is None:
            self.send_error(404)
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        body = self.rfile.read(n) if 0 < n <= 64 * 1024 * 1024 else b""
        q = dict(p.split("=", 1) for p in u.query.split("&") if "=" in p)
        try:
            sink(unquote(q.get("k", "")), body)
        except Exception:                       # noqa: BLE001 – die Seite bekommt einen Fehler
            self.send_error(500)
            return
        self.send_response(204)
        self.end_headers()


class Exchange:
    """Einmal-Adressen für Binärdaten zwischen App und eigenen Seiten: blob (App -> Seite, GET) und sink
    (Seite -> App, POST; fn(key, bytes) im Server-Thread). Schlüssel sind zufällig (secrets.token_urlsafe)."""

    def __init__(self):
        self._blobs: dict[str, bytes] = {}
        self._sinks: dict[str, object] = {}
        self._lock = threading.Lock()

    def add_blob(self, data: bytes) -> str:
        import secrets
        k = secrets.token_urlsafe(16)
        with self._lock:
            self._blobs[k] = bytes(data)
        return k

    def blob(self, k: str):
        with self._lock:
            return self._blobs.pop(k, None)          # einmal abholbar

    def add_sink(self, fn) -> str:
        import secrets
        k = secrets.token_urlsafe(16)
        with self._lock:
            self._sinks[k] = fn
        return k

    def sink(self, k: str):
        with self._lock:
            return self._sinks.get(k)

    def remove(self, k: str):
        with self._lock:
            self._blobs.pop(k, None)
            self._sinks.pop(k, None)


class LocalServer:
    """liefert SITES (Editoren) und unter /sims/ die mitgelieferten Simulatoren aus – nur auf 127.0.0.1."""

    def __init__(self):
        SITES.mkdir(parents=True, exist_ok=True)
        self.relay = MidiRelay()
        self.exchange = Exchange()
        handler = functools.partial(type("_Handler", (_Quiet,), {"relay": self.relay, "exchange": self.exchange}),
                                    directory=str(SITES))
        _Quiet.extensions_map = {**SimpleHTTPRequestHandler.extensions_map, ".js": "text/javascript",
                                 ".mjs": "text/javascript", ".ttf": "font/ttf", ".json": "application/json",
                                 ".wasm": "application/wasm"}
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def url(self, spec: FirmwareSpec, tag: str) -> str:
        return f"http://127.0.0.1:{self.port}/{spec.key}/{tag}/{spec.editor}"

    def sim_url(self, name: str, entry: str = "index.html") -> str:
        return f"http://127.0.0.1:{self.port}/sims/{name}/{entry}"

    def blob_url(self, data: bytes) -> str:
        return f"http://127.0.0.1:{self.port}/_fm1/blob/{self.exchange.add_blob(data)}"

    def sink_url(self, fn) -> tuple[str, str]:
        """-> (URL, Schlüssel zum Entfernen)"""
        k = self.exchange.add_sink(fn)
        return f"http://127.0.0.1:{self.port}/_fm1/sink/{k}", k

    def stop(self):
        self.relay.closed = True
        self.httpd.shutdown()
        self.httpd.server_close()
