# SPDX-License-Identifier: GPL-3.0-only
"""Serve the original web editor of the installed firmware locally.

The editor comes from the firmware's git repo, in exactly the version running on
the device (git archive <tag> web/). It is served by a small server
only for this computer (127.0.0.1, random port) – needed because
the editors load JS modules, which is blocked under file://. It does not need
internet for that; only the first provisioning of a new version fetches
the files from the repo.
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
from .i18n import _
from .firmware import REPOS_DIR, FirmwareSpec, ensure_tools

SITES = DATA_DIR / "editor"
SIMS = Path(__file__).resolve().parent / "sims"     # bundled simulators (WASM), under /sims/<name>/
SKIP = ("web/emu/", "web/learn/", "web/node_modules/", "web/test", "web/conv/")


def site_dir(key: str, tag: str) -> Path:
    return SITES / key / tag


def prepare(spec: FirmwareSpec, tag: str, log=print) -> Path:
    """-> folder with web/editor.html of this version."""
    d = site_dir(spec.key, tag)
    marker = d / ".complete"
    if marker.exists() and (d / spec.editor).exists():
        return d
    repo = REPOS_DIR / spec.key
    if not (repo / ".git").exists():
        ensure_tools(spec, tag, log)
    log(_("providing the editor of {name} {version} …").format(name=spec.name, version=tag))
    folder = str(Path(spec.editor).parent) or "web"
    r = subprocess.run(["git", "archive", "--format=tar", tag, folder], cwd=repo, capture_output=True, timeout=300)
    if r.returncode:
        # tag unknown locally: fetch once
        subprocess.run(["git", "fetch", "--tags", "--quiet"], cwd=repo, timeout=180)
        r = subprocess.run(["git", "archive", "--format=tar", tag, folder], cwd=repo, capture_output=True, timeout=300)
        if r.returncode:
            raise RuntimeError(_("Editor for {version} not available: {error}").format(
                                   version=tag, error=r.stderr.decode(errors='replace').strip()[:200]))
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(r.stdout)) as tf:
        members = [m for m in tf.getmembers() if not any(m.name.startswith(s) for s in SKIP)
                   and not m.name.startswith("/") and ".." not in Path(m.name).parts]
        try:
            tf.extractall(d, members=members, filter="data")
        except TypeError:                      # Python < 3.11.4: without a filter, no links/device files
            tf.extractall(d, members=[m for m in members if m.isfile() or m.isdir()])
    if not (d / spec.editor).exists():
        raise RuntimeError(_("{name} {version} contains no editor under {path}").format(
                               name=spec.name, version=tag, path=spec.editor))
    marker.write_text(tag)
    return d


def manual_index(spec: FirmwareSpec, tag: str) -> Path | None:
    """Manual of this version (lives next to the editor under web/manual/), if present."""
    p = site_dir(spec.key, tag) / Path(spec.editor).parent / "manual" / "index.html"
    return p if p.exists() else None


def missing_manual_images(index: Path) -> list[str]:
    names = re.findall(r'<img[^>]*\bsrc="([^"/:]+\.png)"', index.read_text("utf-8", errors="replace"))
    return [n for n in dict.fromkeys(names) if not (index.parent / n).exists()]


def fetch_manual_images(spec: FirmwareSpec, index: Path, log=print, base: str = "") -> int:
    """The manual's screenshots are not in the repo; fetch them once from the online manual and
    put them next to the local copy. After that the manual works offline. Errors do not abort."""
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
        except Exception:                       # offline, or image not available online
            if got == 0:
                break                           # the very first one fails: do not wait 59 times
    return got


def available(key: str) -> list[str]:
    root = SITES / key
    from .firmware import version_key
    return sorted((p.name for p in root.iterdir() if (p / ".complete").exists()), key=version_key) if root.exists() else []


class MidiRelay:
    """MIDI from the app to pages outside the app (Firefox): server-sent events on /_fm1/midi/<key>."""

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
        except OSError:                         # browser has closed the page
            pass
        finally:
            self._clients.remove(q)


class _Quiet(SimpleHTTPRequestHandler):
    relay: MidiRelay | None = None
    exchange: "Exchange | None" = None

    relay_token: str = ""

    def log_message(self, *a):
        pass

    def list_directory(self, path):
        """no folder listings (a folder without index.html is simply “not found”)."""
        self.send_error(404)
        return None

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
        """Protection against DNS rebinding: only requests to 127.0.0.1:<port> or localhost:<port>, otherwise 421."""
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
        if path.startswith("/_fm1/midi"):
            # what is played on the FM-1: only for the page the app itself opened (random key in the address)
            import hmac
            key = path[len("/_fm1/midi/"):]
            if self.relay is None or not self.relay_token or not hmac.compare_digest(key.encode(), self.relay_token.encode()):
                self.send_error(404)
                return
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
        """Data from one of our own pages to the app (studio: PCM while rendering, flash dump) – only with a one-time key."""
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
        except Exception:                       # noqa: BLE001 – the page gets an error
            self.send_error(500)
            return
        self.send_response(204)
        self.end_headers()


class Exchange:
    """One-time addresses for binary data between the app and its own pages: blob (app -> page, GET) and sink
    (page -> app, POST; fn(key, bytes) in the server thread). Keys are random (secrets.token_urlsafe)."""

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
            return self._blobs.pop(k, None)          # can be fetched once

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
    """serves SITES (editors) and, under /sims/, the bundled simulators – only on 127.0.0.1."""

    def __init__(self):
        SITES.mkdir(parents=True, exist_ok=True)
        self.relay = MidiRelay()
        self.exchange = Exchange()
        import secrets
        self.relay_token = secrets.token_urlsafe(16)
        handler = functools.partial(type("_Handler", (_Quiet,), {"relay": self.relay, "exchange": self.exchange,
                                                                 "relay_token": self.relay_token}),
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

    def relay_url(self) -> str:
        """server-sent events with the MIDI of the FM-1 for a simulator opened in Firefox (random key per app start)."""
        return f"http://127.0.0.1:{self.port}/_fm1/midi/{self.relay_token}"

    def blob_url(self, data: bytes) -> str:
        return f"http://127.0.0.1:{self.port}/_fm1/blob/{self.exchange.add_blob(data)}"

    def sink_url(self, fn) -> tuple[str, str]:
        """-> (URL, key for removal)"""
        k = self.exchange.add_sink(fn)
        return f"http://127.0.0.1:{self.port}/_fm1/sink/{k}", k

    def stop(self):
        self.relay.closed = True
        self.httpd.shutdown()
        self.httpd.server_close()
