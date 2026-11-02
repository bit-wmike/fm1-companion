# SPDX-License-Identifier: GPL-3.0-only
"""„Link einfügen": aus einem beliebigen Link (oder Dateipfad, Namen, kopiertem Text) einen
vorausgefüllten Firmware-Eintrag machen, den der Nutzer prüfen und korrigieren kann.

    draft = resolve("https://github.com/dspaudio/orbit/releases/tag/v0.7.1")
    draft.name, draft.repo, draft.confidence["repo"]   # "ORBIT", "dspaudio/orbit", "sicher"

Ohne Netz wird aufgelöst, was der mitgelieferte Katalog hergibt; mit ``online=True``
(nur im Hintergrund-Thread!) zusätzlich GitHub-Releases und Hinweise im Repo.
Kein Qt hier – der Dialog liegt in ui/linkimport_dialog.py.
"""
from __future__ import annotations

import difflib
import re
import urllib.parse
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import catalog as catmod
from . import config, firmware

SURE, GUESS = "sicher", "geraten"
SPEC_FIELDS = [f for f in firmware.FirmwareSpec.__dataclass_fields__ if f != "builtin"]


@dataclass
class FirmwareDraft(firmware.FirmwareSpec):
    """FirmwareSpec-Felder + was davon sicher und was geraten ist."""
    confidence: dict = field(default_factory=dict)       # Feld -> "sicher" | "geraten"
    hints: list = field(default_factory=list)            # deutsche Sätze: was geraten / geprüft wurde
    source: str = ""                                     # Art der Eingabe
    input: str = ""
    local_file: str = ""
    tag: str = ""
    candidates: list = field(default_factory=list)       # bei Namen: mögliche Firmwares (Katalog + GitHub)
    exact: bool = False                                  # Name passt eindeutig zu genau einer Firmware

    def set(self, name: str, value, how: str = SURE, overwrite: bool = True):
        if value in (None, "", []) or (not overwrite and getattr(self, name)):
            return
        if getattr(self, name) and self.confidence.get(name) == SURE and how == GUESS:
            return
        setattr(self, name, value)
        self.confidence[name] = how

    def spec_dict(self) -> dict:
        d = asdict(self)
        return {k: d[k] for k in SPEC_FIELDS}

    def to_spec(self) -> firmware.FirmwareSpec:
        return firmware.FirmwareSpec(builtin=False, **self.spec_dict())


def empty_draft(text: str = "") -> FirmwareDraft:
    return FirmwareDraft(key="", name="", repo="", asset="", installer="", editor="", protocol="none",
                         input=text)


# ------------------------------------------------------------ Eingabe zerlegen
_URL = re.compile(r"(?:https?://|www\.|file://)[^\s<>\"'`]+|(?<![\w/])(?:github\.com|[\w-]+\.github\.io|"
                  r"raw\.githubusercontent\.com|thefirmwarehub\.com|fm1-editor\.com)/?[^\s<>\"'`]*", re.I)


def extract(text: str) -> tuple[str, str]:
    """-> (Art, Wert): ("url", …) | ("file", …) | ("name", …) | ("", "")"""
    t = (text or "").strip()
    if not t:
        return "", ""
    m = _URL.search(t)
    if m:
        u = m.group(0).rstrip(").,;:!?]>'\"")
        if u.lower().startswith("file://"):
            return "file", urllib.parse.unquote(u[7:])
        if not u.lower().startswith("http"):
            u = "https://" + u
        return "url", u
    for cand in [t.strip("'\""), *re.findall(r"(?:~|/)[^\s'\"]+\.fwsc", t)]:
        if len(cand) > 1024 or "\n" in cand:
            continue
        p = Path(cand).expanduser()
        try:
            is_file = p.is_file()
        except (OSError, ValueError):                   # Name zu lang, Nullbytes …
            is_file = False
        if cand.lower().endswith(".fwsc") or is_file:
            return "file", str(p)
    word = re.sub(r"\s+", " ", t)[:80]
    return ("name", word) if re.search(r"\w", word) else ("", "")


def asset_pattern(filename: str, tag: str) -> str:
    ver = tag.lstrip("vV")
    return filename.replace(ver, "{ver}") if ver and ver in filename else filename


def unique_key(base: str, own_key: str = "") -> str:
    base = catmod.slug(base)[:40] or "firmware"
    taken = {s.key for s in firmware.catalog() if s.key != own_key}
    k, i = base, 2
    while k in taken:
        k = f"{base}-{i}"
        i += 1
    return k


# ------------------------------------------------------------ Auflösen
def resolve(text: str, cat: catmod.Catalog | None = None, online: bool = False) -> FirmwareDraft:
    cat = cat if cat is not None else catmod.load()
    kind, val = extract(text)
    d = empty_draft(text)
    d.source = kind
    entry = None
    if kind == "":
        d.hints.append("Nichts erkannt – bitte einen Link, eine .fwsc-Datei oder einen Namen einfügen.")
        return d
    if kind == "file":
        entry = _from_file(d, val, cat)
    elif kind == "name":
        return _resolve_name(text, val, cat, online)
    else:
        entry = _from_url(d, val, cat)
    if entry is not None:
        _from_entry(d, entry)
    _finish(d, online)
    return d


def _from_entry(d: FirmwareDraft, e: catmod.Entry):
    pkg = None
    if d.sha256:
        pkg = next((p for p in e.packages if p.sha256 == d.sha256), None)
    if d.tag and not pkg:
        pkg = next((p for p in e.packages if p.version == d.tag and p.downloadable), None)
    s = e.to_spec(pkg)
    for f in SPEC_FIELDS:
        v = getattr(s, f)
        if f in ("download_url", "sha256", "asset", "product") and d.confidence.get(f) == SURE:
            continue
        d.set(f, v, SURE)
    d.key = e.key
    d.confidence["key"] = SURE
    if e.builtin:
        d.hints.append(f"{e.name} ist schon fest in der App eingebaut.")


def _from_file(d: FirmwareDraft, path: str, cat) -> catmod.Entry | None:
    p = Path(path).expanduser()
    d.local_file = str(p)
    try:
        ok = p.is_file()
    except (OSError, ValueError):
        ok = False
    if not ok:
        d.hints.append(f"Datei {p} nicht gefunden.")
        d.set("name", p.stem, GUESS)
        return None
    if p.suffix.lower() not in (".fwsc", ".bin", ".fw"):
        d.hints.append(f"{p.name} ist keine Firmware-Datei (.fwsc) – Prüfsumme trotzdem berechnet, "
                       "Aufspielen wäre gefährlich.")
    try:
        info = catmod.identify_file(p, cat)
    except OSError as e:
        d.hints.append(f"Datei {p.name} ließ sich nicht lesen: {e.strerror or e}.")
        d.set("name", p.stem, GUESS)
        return None
    d.set("sha256", info["sha256"], SURE)
    d.set("product", info["product"], SURE if info["product"] else GUESS)
    if info["entry"]:
        pk = info["package"]
        d.tag = pk.version
        d.hints.append(f"Bekanntes Paket: {pk.label} (Prüfsumme stimmt mit dem Mooring-Katalog überein).")
        return info["entry"]
    d.hints.append("Paket ist nicht im Katalog – Prüfsumme wurde berechnet, Herkunft bitte selbst prüfen.")
    if info["product"]:
        d.hints.append(f"Paket-Kennung: {info['product']}.")
    name = re.sub(r"[-_ ]?v?\d[\w.+-]*$", "", p.stem) or p.stem
    d.set("name", name, GUESS)
    d.set("asset", p.name, GUESS)
    e = cat.by_name(name, 0.8)
    if e:
        d.hints.append(f"Der Dateiname passt zu {e.name} – Angaben von dort übernommen (geraten).")
        s = e.to_spec()
        for f in ("name", "repo", "author", "license", "family", "description", "category", "emulator_url",
                  "editor_url", "manual_url", "installer_url", "protocol", "installer"):
            d.set(f, getattr(s, f), GUESS)
    return None


def _match_link(cat, url: str) -> catmod.Entry | None:
    u = url.lower().split("#")[0].rstrip("/")
    best, blen = None, 0
    for e in cat.entries:
        for v in e.links.values():
            v2 = v.lower().split("#")[0].rstrip("/")
            if v2 and (u == v2 or u.startswith(v2 + "/")) and len(v2) > blen:
                best, blen = e, len(v2)
    return best


def _from_url(d: FirmwareDraft, url: str, cat) -> catmod.Entry | None:
    pu = urllib.parse.urlparse(url)
    host = pu.netloc.lower().removeprefix("www.")
    parts = [x for x in pu.path.split("/") if x]
    # ---- Firmware-Verzeichnisse mit Sprungmarke
    if host in ("thefirmwarehub.com", "fm1-editor.com"):
        anchor = urllib.parse.unquote(pu.fragment)
        if anchor:
            e = cat.by_name(anchor, 0.6)
            if e:
                d.hints.append(f"Sprungmarke „#{anchor}“ auf {host} gehört zu {e.name} (ähnlicher Name).")
                d.confidence["_match"] = GUESS
                return e
        d.hints.append(f"Link auf {host} ohne erkennbare Firmware – bitte den Link der Firmware selbst nehmen.")
        return None
    # ---- GitHub
    repo = catmod.repo_of(url)
    if host == "raw.githubusercontent.com" and len(parts) >= 4:
        if parts[-1].lower().endswith(".fwsc"):
            d.set("download_url", url, SURE)
            d.set("asset", parts[-1], GUESS)
    elif host == "github.com" and len(parts) >= 2:
        rest = parts[2:]
        if rest[:2] == ["releases", "download"] and len(rest) >= 4:
            d.tag = urllib.parse.unquote(rest[2])
            fn = urllib.parse.unquote(rest[-1])
            if fn.lower().endswith(".fwsc"):
                d.set("download_url", url, SURE)
                d.set("asset", asset_pattern(fn, d.tag), SURE)
        elif rest[:2] == ["releases", "tag"] and len(rest) >= 3:
            d.tag = urllib.parse.unquote("/".join(rest[2:]))
        elif rest[:1] in (["blob"], ["raw"]) and rest[-1].lower().endswith(".fwsc"):
            raw = f"https://raw.githubusercontent.com/{repo}/{'/'.join(rest[1:])}"
            d.set("download_url", raw, SURE)
            d.set("asset", rest[-1], GUESS)
    elif host.endswith(".github.io"):
        e = _match_link(cat, url)
        if e:
            d.hints.append(f"Seite gehört laut Katalog zu {e.name}.")
            d.set("homepage", url, SURE) if not e.repo else None
            return e
        repo = catmod.pages_repo(url)
        if repo:
            d.confidence["repo"] = GUESS
            d.hints.append(f"Repo aus der GitHub-Pages-Adresse geraten: {repo}.")
        if any(w in url.lower() for w in ("emu", "sim", "try", "demo", "play")):
            d.set("emulator_url", url, GUESS)
        elif "editor" in url.lower():
            d.set("editor_url", url, GUESS)
        else:
            d.set("installer_url", url, GUESS)
    elif url.lower().split("?")[0].endswith(".fwsc"):
        d.set("download_url", url, SURE)
        fn = urllib.parse.unquote(parts[-1]) if parts else "firmware.fwsc"
        d.set("asset", fn, GUESS)
        d.set("name", re.sub(r"[-_ ]?v?\d[\w.+-]*$", "", fn[:-5]) or fn[:-5], GUESS)
        d.hints.append("Direkte Paket-Adresse – Prüfsumme ist unbekannt, die App kann sie beim Prüfen berechnen.")
    else:
        e = _match_link(cat, url)
        if e:
            d.hints.append(f"Seite gehört laut Katalog zu {e.name}.")
            return e
        d.set("homepage", url, GUESS)
        d.set("name", host.split(".")[0].capitalize(), GUESS)
        d.hints.append("Unbekannte Seite – als Homepage übernommen.")
    if d.download_url:
        e, p = (None, None)
        for ent in cat.entries:
            for pk in ent.packages:
                if pk.url and pk.url == d.download_url:
                    e, p = ent, pk
        if e:
            d.set("sha256", p.sha256, SURE)
            d.tag = p.version
            d.hints.append(f"Paket {p.label} ist im Mooring-Katalog – Prüfsumme übernommen.")
            return e
    if repo:
        if not d.confidence.get("repo"):
            d.confidence["repo"] = SURE
        how = d.confidence["repo"]
        d.repo = repo
        e = cat.by_repo(repo)
        if e:
            if how == GUESS:
                d.hints.append(f"{repo} steht im Katalog: {e.name}.")
            return e
        d.set("name", repo.split("/")[1].replace("-", " ").replace("_", " ").strip(), GUESS)
        d.set("author", repo.split("/")[0], GUESS)
        d.hints.append(f"{repo} steht nicht im Katalog – Name und Autor aus dem Repo geraten.")
    return None


def _finish(d: FirmwareDraft, online: bool):
    if online and d.repo:
        _online(d)
    if d.download_url and not d.release_tag and d.tag:
        d.release_tag = d.tag                          # feste Paket-Adresse: Version für die Anzeige
    if not d.name:
        d.set("name", d.repo.split("/")[-1] if d.repo else "Neue Firmware", GUESS)
    if not d.key:
        d.key = unique_key(d.family or d.name)
        d.confidence["key"] = GUESS
    if not d.asset and d.download_url:
        d.set("asset", d.download_url.rsplit("/", 1)[-1], GUESS)
    if d.protocol != "felucca":
        fam = (d.family or d.key).lower()
        if fam in catmod.FELUCCA_PROTOCOL_FAMILIES:
            d.set("protocol", "felucca", SURE)
        else:
            d.protocol = "none"
            d.confidence.setdefault("protocol", GUESS)
            d.hints.append("Ob Backup und Projekte gehen (Felucca-Protokoll), wird beim Verbinden geprüft.")


def _names_of(e: catmod.Entry) -> list[str]:
    return [x for x in (e.name, e.key, e.family, e.repo.split("/")[-1] if e.repo else "", *e.aliases) if x]


def _catalog_candidates(val: str, cat) -> tuple[catmod.Entry | None, bool, list[dict]]:
    """-> (beste Katalog-Firmware, eindeutig?, Kandidaten)"""
    n = catmod.norm(val)
    bare = catmod.norm(catmod.FILLER_WORDS.sub(" ", val.lower()))
    entry = cat.by_name(val, 0.7) or next(iter(cat.search(val)), None)

    def same(e):
        return any(catmod.norm(x) in (n, bare) or catmod.norm(catmod.FILLER_WORDS.sub(" ", x.lower())) in (n, bare)
                   for x in _names_of(e))
    exact = bool(entry and same(entry))
    names = {}
    for e in cat.entries:
        for x in _names_of(e):
            names.setdefault(catmod.norm(x), e)
    near = [names[m] for w in dict.fromkeys(filter(None, (n, bare)))
            for m in difflib.get_close_matches(w, list(names), n=6, cutoff=0.6)]
    found = [e for e in ([entry] if entry else []) + near + cat.search(val)[:8] if e]
    uniq = list({e.key: e for e in found}.values())[:8]
    if exact and sum(1 for e in uniq if same(e)) > 1:
        exact = False                                   # gleich heißende Einträge: lieber fragen
    return entry, exact, [{"title": e.name, "repo": e.repo, "description": e.description, "source": "Katalog",
                           "stars": None, "fork": False, "exact": same(e),
                           "input": f"https://github.com/{e.repo}" if e.repo else e.name} for e in uniq]


def _github_candidates(val: str, skip: set[str]) -> tuple[list[dict], str]:
    try:
        hits = catmod.search_repos(val)
    except Exception as e:  # noqa: BLE001 – Suche ist nur ein Zusatz
        return [], f"GitHub-Suche nicht möglich: {e}"
    return [{"title": h["repo"].split("/")[1].replace("-", " ").replace("_", " ").strip(), "repo": h["repo"],
             "description": h["description"], "source": "GitHub", "stars": h["stars"], "fork": h["fork"],
             "exact": h["exact"], "input": f"https://github.com/{h['repo']}"}
            for h in hits if h["repo"].lower() not in skip], ""


def _resolve_name(text: str, val: str, cat, online: bool) -> FirmwareDraft:
    """Name statt Link: Katalog, mit Netz zusätzlich die GitHub-Suche. Mehrere mögliche Firmwares landen in
    ``candidates`` (der Dialog lässt dann wählen); ``exact`` heißt: der Name passt eindeutig."""
    entry, exact, cands = _catalog_candidates(val, cat)
    hits, note = [], ""
    if online and not exact:
        hits, note = _github_candidates(val, {c["repo"].lower() for c in cands if c["repo"]})
    gh_best = hits[0] if hits and hits[0]["exact"] else None
    if exact or (entry and not gh_best):
        d = empty_draft(text)
        d.source = "name"
        d.hints.append(f"„{val}“ im Katalog gefunden: {entry.name}.")
        _from_entry(d, entry)
        _finish(d, online)
    elif hits:
        pick = gh_best or hits[0]
        d = resolve(pick["input"], cat, online)
        d.confidence["repo"] = GUESS
        d.hints = [h for h in d.hints if "steht nicht im Katalog" not in h]
        d.hints.insert(0, f"Auf GitHub gefunden: {pick['repo']}"
                       + (f" – {pick['description']}" if pick["description"] else "") + " (bitte prüfen).")
    else:
        d = empty_draft(text)
        d.source = "name"
        d.set("name", val.strip(), GUESS)
        d.hints.append(f"„{val}“ steht nicht im Katalog – bitte die Angaben selbst ergänzen.")
        if online:
            d.hints.append(note or "Auch auf GitHub keine passende FM-1-Firmware gefunden – am besten den "
                                   "GitHub-Link einfügen.")
        _finish(d, online)
    d.source, d.input = "name", text
    d.candidates = cands + hits
    d.exact = exact or gh_best is not None
    if len(d.candidates) > 1 and not d.exact:
        d.hints.insert(0, f"{len(d.candidates)} mögliche Firmwares gefunden – bitte die richtige auswählen.")
    return d


def _matches(pattern: str, name: str) -> bool:
    """Paketname passt zum Muster mit {ver}?"""
    if not pattern:
        return False
    rx = re.escape(pattern).replace(re.escape("{ver}"), r"[\w.+-]+")
    return re.fullmatch(rx, name, re.I) is not None


def _pick_release(d: FirmwareDraft, rels: list) -> tuple[dict | None, dict | None]:
    """Release + .fwsc-Asset: gewünschte Version, sonst das neueste stabile; bei mehreren Paketen
    (z. B. orbit.fwsc / orbit-ble.fwsc) das, was zum bekannten Paketnamen passt."""
    def fwsc(r):
        a = [x for x in r["assets"] if x["name"].lower().endswith(".fwsc")]
        known = [x for x in a if _matches(d.asset, x["name"])]
        return (known or ([] if d.asset and d.confidence.get("asset") == SURE else a) or [None])[0]
    if d.tag:
        r = next((r for r in rels if r["tag"] == d.tag), None)
        if r and fwsc(r):
            return r, fwsc(r)
    for stable in (True, False):
        for r in rels:
            if (not stable or not r.get("prerelease")) and fwsc(r):
                return r, fwsc(r)
    return None, None


def _release_sum(rel: dict, name: str) -> str:
    """Prüfsumme aus SHA256SUMS (o. ä.) im selben Release, sonst ''."""
    sums = next((x for x in rel["assets"] if re.fullmatch(r"(?i)sha256sums(\.txt)?|.*\.sha256", x["name"])), None)
    txt = catmod.fetch_text(sums["url"], 64 << 10) if sums and sums.get("url") else None
    for line in (txt or "").splitlines():
        parts = line.split()
        if parts and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]) and (len(parts) == 1 or parts[-1].lstrip("*") == name):
            return parts[0].lower()
    return ""


def _from_release_url(url: str, repo: str) -> str:
    """Tag aus einer Release-Download-Adresse dieses Repos, sonst ''."""
    m = re.match(rf"https://github\.com/{re.escape(repo)}/releases/download/([^/]+)/", url or "", re.I)
    return urllib.parse.unquote(m.group(1)) if m else ""


def _online(d: FirmwareDraft):
    """GitHub: Releases (Paketname, Version), fmhub.json, Web-Installer-Seite, .fwsc im Repo und
    Hinweise im Repo (Felucca-Werkzeuge)."""
    try:
        info = catmod.enrich(d.repo)
    except catmod.RateLimited as e:
        d.hints.append(f"GitHub: {e}")
        info = {}
    except catmod.Offline as e:
        d.hints.append(f"GitHub: {e}")
        return
    if info.get("message"):
        d.hints.append(info["message"])
    d.set("description", info.get("description", ""), SURE, overwrite=False)
    if info.get("homepage") and not d.repo:
        d.set("homepage", info["homepage"], SURE, overwrite=False)
    hub = catmod.fmhub_info(d.repo)
    if hub:
        d.set("description", hub.get("what", ""), SURE, overwrite=False)
        d.set("author", hub.get("author", ""), SURE, overwrite=d.confidence.get("author") != SURE)
        d.set("emulator_url", hub.get("play", ""), SURE, overwrite=False)
        d.set("editor_url", hub.get("editor", ""), SURE, overwrite=False)
        d.set("manual_url", hub.get("manual", "") or hub.get("cheat", ""), SURE, overwrite=False)
        d.hints.append("Angaben aus fmhub.json im Repo übernommen.")
    rels = info.get("releases") or []
    rel, a = _pick_release(d, rels)
    pinned = _from_release_url(d.download_url, d.repo)
    if rel and a:
        if pinned and pinned != rel["tag"] and firmware.version_key(rel["tag"]) > firmware.version_key(pinned):
            # Katalog kennt nur eine ältere Version: den GitHub-Releases folgen statt auf die alte festzulegen
            d.hints.append(f"Auf GitHub gibt es eine neuere Version ({rel['tag']}) als im Katalog ({pinned}) – "
                           "die neue Version wird übernommen.")
            d.download_url = d.sha256 = d.release_tag = ""
            d.confidence.pop("download_url", None)
            d.confidence.pop("sha256", None)
            d.tag = rel["tag"]
        if not d.download_url:
            d.set("download_url", a["url"], SURE)
            if not d.sha256:
                d.set("sha256", _release_sum(rel, a["name"]), SURE)
        if d.confidence.get("asset") != SURE or not _matches(d.asset, a["name"]):
            d.set("asset", asset_pattern(a["name"], rel["tag"]), SURE)
        d.tag = d.tag or rel["tag"]
        d.hints.append(f"Neuestes Release mit Paket: {rel['tag']} ({a['name']}).")
    elif not d.download_url:
        if rels:
            d.hints.append("Die Releases auf GitHub enthalten kein .fwsc-Paket – suche auf der Installer-Seite "
                           "und im Repo.")
        _find_elsewhere(d)
    if d.protocol != "felucca":
        for path in ("web/fm1backup.js", "tools/fm1_install.py"):
            if catmod.raw_exists(d.repo, path):
                d.set("protocol", "felucca", SURE)
                d.set("installer", "tools/fm1_install.py", SURE) if path.startswith("tools") else None
                d.hints.append(f"Repo enthält {path} – Backup und Projekte über das Felucca-Protokoll erwartet.")
                break


def _find_elsewhere(d: FirmwareDraft):
    """kein Release-Paket: Web-Installer-Seite (GitHub Pages), dann .fwsc-Dateien im Repo."""
    base = catmod.pages_url(d.repo)
    pages = [d.installer_url, base + "webapp/installer/", base + "installer/", base, d.homepage]
    meta = catmod.find_installer_package([u for u in pages if u and u.startswith("https://")])
    if meta:
        d.set("download_url", meta["url"], SURE)
        d.set("asset", meta["url"].rsplit("/", 1)[-1], SURE)
        d.set("installer_url", meta["page"], SURE, overwrite=False)
        d.set("sha256", meta["sha256"], SURE)
        d.set("product", meta["product"], SURE)
        d.tag = meta["version"] or d.tag
        d.hints.append(f"Paket auf der Installer-Seite gefunden: {d.asset}"
                       + (f" (Version {meta['version']})" if meta["version"] else "")
                       + ("" if meta["sha256"] else " – ohne Prüfsumme") + ".")
        return
    files = catmod.repo_fwsc_files(d.repo)
    if files:
        f = files[0]
        fn = f["path"].rsplit("/", 1)[-1]
        d.set("download_url", f["url"], SURE)
        d.set("asset", fn, SURE)
        m = re.search(r"(\d+(?:\.\d+)+[\w.-]*?)\.fwsc$", fn, re.I)
        d.tag = d.tag or (m.group(1) if m else "")
        d.hints.append(f"Paket liegt direkt im Repo: {f['path']} (Zweig {f['ref']}) – Prüfsumme unbekannt.")
        if len(files) > 1:
            d.hints.append("Weitere Pakete im Repo: " + ", ".join(x["path"] for x in files[1:4]) + ".")
        return
    d.hints.append("Kein Paket gefunden (weder in den Releases noch auf der Installer-Seite oder im Repo) – "
                   "vermutlich nur über den Web-Installer im Browser installierbar.")


# ------------------------------------------------------------ Prüfen & Speichern
def validate(d: FirmwareDraft, editing_key: str = "") -> list[str]:
    errs = []
    if not d.name.strip():
        errs.append("Bitte einen Namen angeben.")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,47}", d.key or ""):
        errs.append("Der Schlüssel darf nur Kleinbuchstaben, Ziffern, Punkt, - und _ enthalten.")
    taken = {s.key: s for s in firmware.catalog()}
    if d.key in taken and d.key != editing_key:
        errs.append(f"Den Schlüssel „{d.key}“ gibt es schon ({taken[d.key].name}).")
    if d.repo and not re.fullmatch(r"[\w.-]+/[\w.-]+", d.repo):
        errs.append("GitHub-Repo bitte als besitzer/name angeben.")
    for f in ("homepage", "editor_url", "emulator_url", "manual_url", "installer_url", "download_url"):
        v = getattr(d, f)
        if v and not re.match(r"https://[^\s/]+\.[^\s]+$", v):
            errs.append(f"{LABELS.get(f, f)}: bitte eine vollständige https-Adresse angeben.")
    if d.sha256 and not re.fullmatch(r"[0-9a-f]{64}", d.sha256.lower()):
        errs.append("Die Prüfsumme muss 64 Hex-Zeichen (SHA-256) lang sein.")
    if not (d.repo or d.download_url or d.homepage or d.installer_url):
        errs.append("Bitte mindestens ein GitHub-Repo, eine Paket-Adresse oder eine Webseite angeben.")
    return errs


def save(d: FirmwareDraft, editing_key: str = "") -> firmware.FirmwareSpec:
    s = config.load()
    entry = d.spec_dict()
    entry["sha256"] = entry["sha256"].lower()
    s.extra_firmwares = [x for x in s.extra_firmwares if x.get("key") not in (d.key, editing_key)] + [entry]
    config.save(s)
    return d.to_spec()


def remove(key: str) -> bool:
    s = config.load()
    n = len(s.extra_firmwares)
    s.extra_firmwares = [x for x in s.extra_firmwares if x.get("key") != key]
    config.save(s)
    return len(s.extra_firmwares) < n


def own_entries() -> list[dict]:
    return list(config.load().extra_firmwares)


def draft_from_saved(e: dict) -> FirmwareDraft:
    d = empty_draft()
    for k, v in e.items():
        if k in SPEC_FIELDS:
            setattr(d, k, v)
            d.confidence[k] = SURE
    d.source = "gespeichert"
    return d


LABELS = {"name": "Name", "key": "Schlüssel", "repo": "GitHub-Repo", "asset": "Paketname", "author": "Autor",
          "license": "Lizenz", "family": "Familie", "product": "Paket-Kennung", "category": "Kategorie",
          "description": "Beschreibung", "homepage": "Webseite", "editor_url": "Online-Editor",
          "emulator_url": "Online-Simulator", "manual_url": "Handbuch", "installer_url": "Web-Installer",
          "download_url": "Paket-Adresse", "sha256": "Prüfsumme (SHA-256)", "protocol": "Protokoll",
          "requires_stock_v15": "Nur von V15 aus"}
