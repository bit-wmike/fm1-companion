# SPDX-License-Identifier: GPL-3.0-only
"""Kostenlose Sample-Pakete von GitHub: durchsehen, ganz oder ordnerweise laden, in die Bibliothek.

Ohne GitHub-API (deren Limit ist klein): der Inhalt kommt per „partial clone“ – git lädt nur
die Ordnerstruktur (ein paar hundert KiB), Audiodateien erst beim Herunterladen und nur für
die gewählten Ordner (sparse checkout). Braucht nur `git`.

Lizenzen: Jedes Paket trägt die Lizenz, die im Repository wirklich steht. Wo keine klare
Lizenz zu finden ist, steht „Lizenz unklar“ – solche Pakete sind nur zum Ausprobieren gedacht,
nicht zum Veröffentlichen.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .config import CACHE_DIR

AUDIO = {".wav", ".wave", ".aif", ".aiff", ".flac", ".mp3", ".ogg"}


@dataclass
class Pack:
    id: str
    name: str
    repo: str                       # owner/name auf GitHub
    licence: str                    # SPDX oder "unklar"
    description: str
    licence_note: str = ""          # wo die Lizenz steht / warum unklar
    tags: tuple[str, ...] = ()
    size_hint: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def url(self) -> str:
        return f"https://github.com/{self.repo}"

    @property
    def free(self) -> bool:
        return self.licence in FREE

    @property
    def tag(self) -> str:
        return "quelle:" + self.id


FREE = {"CC0-1.0": "CC0 – gemeinfrei, frei nutzbar (auch kommerziell, ohne Namensnennung)",
        "CC-BY-4.0": "CC BY 4.0 – frei nutzbar mit Namensnennung",
        "CC-BY-3.0": "CC BY 3.0 – frei nutzbar mit Namensnennung"}

# Geprüft am 30.10.2026 (LICENSE-Datei bzw. README im Repository).
PACKS = [
    Pack("vcsl", "VCSL – Versilian Community Sample Library", "sgossner/VCSL", "CC0-1.0",
         "Rund 4 000 Samples echter Instrumente: Trommeln (Membranophones), Becken, Glocken und Percussion "
         "(Idiophones), Bläser, Saiten, Elektronik. Ideal für eigene Drum-Kits.",
         "LICENSE im Repository: CC0 1.0 Universal.", ("akustisch", "percussion", "instrumente"), "≈ 1,3 GB"),
    Pack("vsco2ce", "VSCO 2 Community Edition (Orchester)", "sgossner/VSCO-2-CE", "CC0-1.0",
         "Orchester-Samples: Streicher, Holz, Blech, Pauken und Orchester-Percussion.",
         "LICENSE im Repository: CC0 1.0 Universal.", ("orchester", "instrumente"), "≈ 1 GB"),
    Pack("dirt", "Dirt-Samples (TidalCycles)", "tidalcycles/Dirt-Samples", "unklar",
         "Die Klangsammlung von TidalCycles/SuperDirt: Bassdrums, Breaks, Drum-Machine-Hits, Stimmen, FX "
         "(rund 2 000 kurze Sounds).",
         "Das Repository hat keine LICENSE-Datei und die README nennt keine Lizenz; die Sounds stammen aus "
         "vielen Quellen. Nur zum Ausprobieren – nicht für Veröffentlichungen.", ("drums", "breaks", "fx"), "≈ 230 MB"),
    Pack("drummachines", "Tidal Drum Machines", "ritchse/tidal-drum-machines", "unklar",
         "Samples klassischer Drum-Machines (TR-808, TR-909, LinnDrum …), sortiert nach Gerät.",
         "Keine Lizenzangabe im Repository; Aufnahmen kommerzieller Geräte unbekannter Herkunft. "
         "Nur zum Ausprobieren.", ("drums", "808", "909"), "≈ 300 MB"),
]


def licence_text(p: Pack) -> str:
    return FREE.get(p.licence, "Lizenz unklar – nur zum Ausprobieren, nicht veröffentlichen")


def pack(pid: str) -> Pack:
    return next(p for p in PACKS if p.id == pid)


# ------------------------------------------------------------ Inhalt ---

@dataclass
class Entry:
    path: str
    size: int

    @property
    def folder(self) -> str:
        return self.path.rsplit("/", 1)[0] if "/" in self.path else ""


def parse_tree(text: str) -> list[Entry]:
    """Ausgabe von `git ls-tree -r HEAD` (mit oder ohne -l) -> Audiodateien.
    (Ohne -l: die Größen fehlen, dafür muss git keine einzige Audiodatei laden.)"""
    out = []
    for line in text.splitlines():
        meta, _, path = line.partition("\t")
        parts = meta.split()
        if len(parts) < 3 or parts[1] != "blob" or not path:
            continue
        if path.startswith('"'):                      # git quotet Sonderzeichen
            try:
                path = path[1:-1].encode("latin-1").decode("unicode_escape").encode("latin-1").decode("utf-8")
            except (UnicodeError, ValueError):
                continue
        if Path(path).suffix.lower() in AUDIO:
            out.append(Entry(path, int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0))
    return out


def folders(entries: list[Entry]) -> dict[str, tuple[int, int]]:
    """Ordner -> (Anzahl Dateien, Bytes), nur Ordner, die direkt Audio enthalten."""
    d: dict[str, list[int]] = {}
    for e in entries:
        c = d.setdefault(e.folder, [0, 0])
        c[0] += 1
        c[1] += e.size
    return {k: (v[0], v[1]) for k, v in sorted(d.items())}


def _cache(p: Pack) -> Path:
    return CACHE_DIR / "samplepacks" / p.id


def git_url(p: Pack) -> str:
    base = os.environ.get("FM1C_PACKS_GITBASE")          # Tests: lokale Repositories
    return f"{base.rstrip('/')}/{p.id}" if base else f"{p.url}.git"


class Cancelled(RuntimeError):
    pass


def git_error_text(line: str) -> str:
    """letzte git-Zeile -> deutscher Satz."""
    low = line.lower()
    if "could not resolve host" in low or "name or service not known" in low or "temporary failure" in low:
        return "Keine Internetverbindung (GitHub nicht erreichbar)."
    if "timed out" in low or "operation too slow" in low:
        return "Zeitüberschreitung – die Verbindung zu GitHub ist zu langsam oder abgebrochen."
    if "repository not found" in low or "404" in low:
        return "Das Paket gibt es auf GitHub nicht mehr – bitte die App aktualisieren."
    if "rate limit" in low or "403" in low or "429" in low:
        return "GitHub lehnt gerade ab (zu viele Anfragen) – in ein paar Minuten noch einmal."
    if "no space left" in low:
        return "Kein freier Speicherplatz mehr."
    if "ssl" in low or "certificate" in low:
        return "Sichere Verbindung nicht möglich (Zertifikat) – stimmen Datum und Uhrzeit des Rechners?"
    return "git: " + (line or "unbekannter Fehler")


def _git(*args, cwd=None, timeout=600, cancel=None) -> str:
    """git ausführen; cancel() wird alle 0,2 s geprüft und beendet den Vorgang (Cancelled)."""
    exe = shutil.which("git")
    if not exe:
        raise RuntimeError("Für Sample-Pakete wird git gebraucht (sudo apt install git)")
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
    if cancel is None:
        r = subprocess.run([exe, *args], cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
        out, err, rc = r.stdout, r.stderr, r.returncode
    else:
        proc = subprocess.Popen([exe, *args], cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        t0 = time.time()
        while True:
            try:
                out, err = proc.communicate(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                if cancel():
                    proc.kill()
                    proc.communicate()
                    raise Cancelled("abgebrochen")
                if time.time() - t0 > timeout:
                    proc.kill()
                    proc.communicate()
                    raise RuntimeError("Zeitüberschreitung – die Verbindung zu GitHub ist zu langsam oder abgebrochen.")
        rc = proc.returncode
    if rc:
        msg = (err or out).strip().splitlines()
        raise RuntimeError(git_error_text(msg[-1] if msg else ""))
    return out


def _repo(p: Pack) -> Path:
    """partieller Klon (nur Struktur) im Cache, beim ersten Mal angelegt."""
    d = _cache(p) / "repo"
    if not (d / ".git").exists():
        shutil.rmtree(d, ignore_errors=True)
        d.parent.mkdir(parents=True, exist_ok=True)
        _git("clone", "--quiet", "--filter=blob:none", "--no-checkout", "--depth", "1", git_url(p), str(d))
    return d


def index(p: Pack, refresh: bool = False, max_age: float = 7 * 86400) -> list[Entry]:
    """Inhalt eines Pakets (aus dem Cache, höchstens eine Woche alt)."""
    fx = os.environ.get("FM1C_PACKS_FIXTURES")
    if fx and (Path(fx) / f"{p.id}.tree").exists():
        return parse_tree((Path(fx) / f"{p.id}.tree").read_text("utf-8"))
    cf = _cache(p) / "index.json"
    if not refresh and cf.exists() and time.time() - cf.stat().st_mtime < max_age:
        try:
            return [Entry(**e) for e in json.loads(cf.read_text("utf-8"))["files"]]
        except (ValueError, KeyError, TypeError):
            pass
    repo = _repo(p)
    if refresh:
        _git("fetch", "--quiet", "--filter=blob:none", "--depth", "1", "origin", cwd=repo)
        _git("reset", "--quiet", "--soft", "FETCH_HEAD", cwd=repo)
    entries = parse_tree(_git("ls-tree", "-r", "HEAD", cwd=repo))
    rev = _git("rev-parse", "HEAD", cwd=repo).strip()
    cf.write_text(json.dumps({"rev": rev, "files": [e.__dict__ for e in entries]}), "utf-8")
    return entries


def download(p: Pack, dest_root: Path, folder_list: list[str] | None = None,
             log: Callable[[str], None] = lambda s: None, cancel: Callable[[], bool] = lambda: False) -> list[Path]:
    """Ordner (oder alles) laden und nach dest_root/<Paketname>/… kopieren. -> neue Audiodateien."""
    repo = _repo(p)
    entries = index(p)
    want = [f for f in (folder_list or [])]
    pats = [f"/{f}/*" if f else "/*" for f in want] if want else ["/*"]
    _git("sparse-checkout", "set", "--no-cone", *pats, cwd=repo)
    sel = [e for e in entries if not want or e.folder in want]
    size = sum(e.size for e in sel)
    log(f"lade {len(sel)} Audiodateien" + (f" ({size / 1e6:.0f} MB)" if size else "") + " … (je nach Größe einige Minuten)")
    _git("read-tree", "-mu", "HEAD", cwd=repo, timeout=3600, cancel=cancel)   # holt nur die gewählten Dateien
    target = Path(dest_root) / folder_name(p)
    out = []
    for i, e in enumerate(sel):
        if cancel():
            raise Cancelled("abgebrochen")
        src = repo / e.path
        if not src.exists():
            continue
        dst = target / e.path
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists() or dst.stat().st_size != src.stat().st_size:
            shutil.copyfile(src, dst)
        out.append(dst)
        if i % 50 == 0:
            log(f"{i + 1}/{len(sel)} {Path(e.path).name}")
    write_source_note(p, target)
    return out


def folder_name(p: Pack) -> str:
    return _safe_dir(p.name.split(" – ")[0].split(" (")[0])


def write_source_note(p: Pack, target: Path):
    target.mkdir(parents=True, exist_ok=True)
    (target / "QUELLE.txt").write_text(
        f"{p.name}\nQuelle: {p.url}\nLizenz: {p.licence} – {licence_text(p)}\n{p.licence_note}\n"
        f"Geladen am {time.strftime('%d.%m.%Y')} mit FM-1 Companion.\n", "utf-8")


def note_for(p: Pack) -> str:
    return f"Quelle: {p.name} – {p.url} · Lizenz: {p.licence if p.free else 'unklar'}"


def _safe_dir(name: str) -> str:
    import re
    return re.sub(r"[^\w\-. ]+", "_", name).strip() or "Paket"
