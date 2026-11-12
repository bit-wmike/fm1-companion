# SPDX-License-Identifier: GPL-3.0-only
"""Firmware catalog "Firmware+": all known FM-1 firmwares in one place.

Sources (see docs/catalog.md):
  * Mooring catalog (awesome-fm-1/mooring/catalog.json, schema 3) – machine-readable:
    packages with SHA-256, download address, product identity, notes (beta, …).
  * awesome-fm-1 README – names, authors, descriptions, links (installer, emulator,
    editor, manual), licence, lineage (Felucca, SLOOP …).
  * GitHub (optional, later, cached) – stars, releases, screenshot.

The app ships a ready-made snapshot (data/catalog_snapshot.json, built with
tools/build_catalog_snapshot.py) so that "Firmware+" also works without internet.
At runtime: snapshot → cached live state (refreshed every 24 h in the background)
→ enrichment from the GitHub cache.

This module has no Qt dependency.
"""
from __future__ import annotations

import base64
import difflib
import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import config
from .i18n import N_, _

MOORING_URL = "https://raw.githubusercontent.com/cicloid/awesome-fm-1/main/mooring/catalog.json"
README_URL = "https://raw.githubusercontent.com/cicloid/awesome-fm-1/main/README.md"
AWESOME_URL = "https://github.com/cicloid/awesome-fm-1"
HUB_URL = "https://thefirmwarehub.com/"
DIRECTORY_URL = "https://fm1-editor.com/firmware/"
SNAPSHOT_FILE = Path(__file__).resolve().parent / "data" / "catalog_snapshot.json"
UA = "fm1-companion"
MAX_AGE = 24 * 3600
class _HttpsOnlyRedirect(urllib.request.HTTPRedirectHandler):
    """Redirects only to https – never silently down to http (or file:, ftp: …)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme.lower() != "https":
            raise urllib.error.URLError(_("Redirect to an insecure address refused: {url}").format(url=newurl))
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_HttpsOnlyRedirect())
_raw_urlopen = _opener.open


def _https_urlopen(req, timeout: float = 20, **kw):
    """Catalog and preview images come from addresses in a third-party catalog: https only."""
    url = req.full_url if isinstance(req, urllib.request.Request) else str(req)
    if urllib.parse.urlsplit(url).scheme.lower() != "https":
        raise urllib.error.URLError(_("only https addresses allowed: {url}").format(url=url[:120]))
    return _raw_urlopen(req, timeout=timeout, **kw)


urlopen = _https_urlopen                  # replaceable in tests
FELUCCA_PROTOCOL_FAMILIES = {"melodee", "skiff", "sloop", "felucca"}

# categories as in the firmware directories (thefirmwarehub.com / fm1-editor.com)
# the German names are internal keys (stored in the catalog data) – shown via category_name() / lineage_name()
CATEGORIES = ["Original", "Synth", "Groovebox", "Sampler", "Akkorde", "Generativ", "Werkzeug", "Spiele & Spaß"]  # i18n: ignore
LINEAGES = ["Felucca", "SLOOP", "FoMni", "Original M-VAVE", "eigenständig"]  # i18n: ignore
CATEGORY_NAMES = {"Original": N_("Original"), "Synth": N_("Synth"), "Groovebox": N_("Groovebox"),
                  "Sampler": N_("Sampler"), "Akkorde": N_("Chords"), "Generativ": N_("Generative"),
                  "Werkzeug": N_("Tool"), "Spiele & Spaß": N_("Games & fun")}  # i18n: ignore
LINEAGE_NAMES = {"eigenständig": N_("standalone")}  # i18n: ignore
# license values written by the catalog parser (internal keys) -> display text
LICENSE_NAMES = {"proprietär": N_("proprietary"), "keine Lizenz angegeben": N_("no license stated")}  # i18n: ignore
# descriptions the app writes itself (not from awesome-fm-1) – stored in English, shown translated
STOCK_DESCRIPTION = N_("The FM-1's factory firmware: 6-operator FM like the DX7, 128 presets, arpeggiator and "
                       "step sequencer. With it you can get back from any custom firmware.")
MOORING_ONLY_DESCRIPTION = N_("Listed in the Mooring catalog (installable), but not (yet) in the awesome-fm-1 list.")
_APP_DESCRIPTIONS = {STOCK_DESCRIPTION, MOORING_ONLY_DESCRIPTION}
# older snapshots / caches stored these texts in German
_LEGACY_DESCRIPTIONS = {
    "Die Werks-Firmware des FM-1: 6-Operator-FM wie der DX7, 128 Presets, Arpeggiator und "  # i18n: ignore
    "Step-Sequencer. Mit ihr kommst du von jeder Custom-Firmware zurück.": STOCK_DESCRIPTION,
    "Steht im Mooring-Katalog (installierbar), aber (noch) nicht in der awesome-fm-1-Liste.":  # i18n: ignore
        MOORING_ONLY_DESCRIPTION,
}


def category_name(key: str) -> str:
    """display name of a category key ("Akkorde" -> "Chords")"""
    return _(CATEGORY_NAMES[key]) if key in CATEGORY_NAMES else key


def lineage_name(key: str) -> str:
    return _(LINEAGE_NAMES[key]) if key in LINEAGE_NAMES else key


def license_name(lic: str) -> str:
    return _(LICENSE_NAMES[lic]) if lic in LICENSE_NAMES else lic


def description_text(e) -> str:
    """description of an entry for display (app-written descriptions translated, upstream ones as they are)"""
    d = _LEGACY_DESCRIPTIONS.get(e.description, e.description)
    return _(d) if d in _APP_DESCRIPTIONS else d


def is_app_description(e) -> bool:
    """True when the description was written by the app (not the author's text from awesome-fm-1)"""
    return _LEGACY_DESCRIPTIONS.get(e.description, e.description) in _APP_DESCRIPTIONS


def protocol_evidence_text(e) -> str:
    m = re.fullmatch(r"(?:known Felucca protocol family|bekannte Felucca-Protokoll-Familie) \((.+)\)",  # i18n: ignore
                     e.protocol_evidence or "")
    return _("known Felucca protocol family ({family})").format(family=m.group(1)) if m else e.protocol_evidence
FIRMWARE_SECTIONS = {"Official": "official", "Open source": "open", "Closed source": "closed",
                     "Not a synth": "notsynth"}

NOTE_TEXT = {                      # translated where used: _(NOTE_TEXT[key])
    "beta": N_("Beta – may still have bugs."),
    "powerLossNotResumable": N_("An interrupted installation cannot be resumed – charge the battery fully "
                                "first or leave it on the power supply and don't move the cable!"),
    "projectsNotBackwardCompatible": N_("Projects from this version cannot be loaded into older versions."),
    "needsMiniLab3": N_("Needs an Arturia MiniLab 3 to play."),
    "requiresStockV15": N_("Can only be installed from the original firmware V15 – the app takes the "
                           "detour automatically."),
}


def cache_dir() -> Path:
    d = config.CACHE_DIR / "catalog"
    d.mkdir(parents=True, exist_ok=True)
    return d


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "firmware"


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def repo_of(url: str) -> str:
    """'owner/name' from a GitHub address (also github.io and raw), otherwise ''."""
    m = re.match(r"https?://(?:www\.)?github\.com/([\w.-]+)/([\w.-]+)", url or "")
    if m:
        return f"{m.group(1)}/{m.group(2).removesuffix('.git')}"
    m = re.match(r"https?://raw\.githubusercontent\.com/([\w.-]+)/([\w.-]+)/", url or "")
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    return ""


def pages_repo(url: str) -> str:
    """owner.github.io/repo/... -> 'owner/repo' (guessed)."""
    m = re.match(r"https?://([\w-]+)\.github\.io/([\w.-]+)", url or "", re.I)
    return f"{m.group(1)}/{m.group(2)}" if m else ""


# ================================================================== data ===

@dataclass
class Package:
    label: str
    version: str = ""
    product: str = ""
    sha256: str = ""
    raw_size: int = 0
    url: str = ""
    family: str = ""
    license: str = ""
    notes: list[str] = field(default_factory=list)
    requires_stock_v15: bool = False
    stock: bool = False
    source_repo: str = ""
    loader_family: str = ""

    @property
    def downloadable(self) -> bool:
        return bool(self.url and self.sha256)

    @property
    def filename(self) -> str:
        return self.url.rsplit("/", 1)[-1] if self.url else ""


@dataclass
class Entry:
    key: str
    name: str
    author: str = ""
    description: str = ""
    section: str = "open"               # official / open / closed / notsynth / mooring / own
    categories: list[str] = field(default_factory=list)
    lineage: list[str] = field(default_factory=list)     # e.g. ["Felucca", "FoMni"]
    lineage_text: str = ""
    license: str = ""
    repo: str = ""                      # owner/name
    links: dict[str, str] = field(default_factory=dict)  # role -> URL
    flags: list[str] = field(default_factory=list)       # beta, experimentell, wip, kostenpflichtig, quellcode (note keys)
    packages: list[Package] = field(default_factory=list)  # newest first
    family: str = ""
    protocol: str = "none"
    protocol_evidence: str = ""
    aliases: list[str] = field(default_factory=list)
    screenshot: str = ""
    stars: int | None = None
    pushed_at: str = ""
    builtin: bool = False

    # ---- derived
    @property
    def latest(self) -> Package | None:
        return next((p for p in self.packages if p.downloadable), None)

    @property
    def installable(self) -> bool:
        return self.latest is not None

    @property
    def open_source(self) -> bool:
        if self.section == "closed":
            return False
        lic = self.license.lower()
        return bool(lic) and not any(x in lic for x in ("proprietär", "kommerziell", "keine"))  # i18n: ignore

    @property
    def beta(self) -> bool:
        if any(f in self.flags for f in ("beta", "experimentell", "wip", "alpha")):
            return True
        lp = self.latest
        return bool(lp and "beta" in lp.notes)

    @property
    def emulator_url(self) -> str:
        return self.links.get("emulator", "")

    @property
    def editor_url(self) -> str:
        return self.links.get("editor", "")

    @property
    def repo_url(self) -> str:
        return f"https://github.com/{self.repo}" if self.repo else ""

    @property
    def homepage(self) -> str:
        return self.links.get("site") or self.repo_url or self.links.get("installer", "")

    @property
    def hub_anchor(self) -> str:
        return norm(self.family or self.name)

    def warnings(self) -> list[str]:
        out = []
        lp = self.latest
        notes = list(lp.notes) if lp else []
        if self.beta and "beta" not in notes:
            notes.insert(0, "beta")
        for n in notes:
            out.append(_(NOTE_TEXT[n]) if n in NOTE_TEXT else n)
        if lp and lp.requires_stock_v15:
            out.append(_(NOTE_TEXT["requiresStockV15"]))
        if "kostenpflichtig" in self.flags:
            out.append(_("Paid – purchase and installation go through the manufacturer's site."))
        if self.section == "closed":
            out.append(_("Closed source."))
        if not lp and "quellcode" in self.flags:
            out.append(_("No ready-made package – only for building yourself from the source code."))
        return out

    def search_text(self) -> str:
        return " ".join([self.name, self.author, self.description, " ".join(self.categories),
                         " ".join(self.lineage), self.family, self.repo, " ".join(self.aliases),
                         self.license]).lower()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Entry":
        d = dict(d)
        pk = [Package(**{k: v for k, v in p.items() if k in Package.__dataclass_fields__})
              for p in d.pop("packages", [])]
        e = cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
        e.packages = pk
        return e

    def to_spec(self, package: Package | None = None):
        """FirmwareSpec for the installer / "My firmwares"."""
        from . import firmware
        p = package or self.latest
        asset = ""
        if p and p.filename:
            ver = p.version.lstrip("vV")
            asset = p.filename.replace(ver, "{ver}") if ver and ver in p.filename else p.filename
        b = firmware.spec(self.key) if self.builtin else None
        if b:
            return b
        return firmware.FirmwareSpec(
            key=self.key, name=self.name, repo=self.repo, asset=asset, builtin=False,
            installer="tools/fm1_install.py" if self.protocol == "felucca" else "",
            editor="", description=self.description, homepage=self.homepage if not self.repo else "",
            protocol=self.protocol, family=self.family, product=p.product if p else "",
            author=self.author, license=self.license, editor_url=self.editor_url,
            emulator_url=self.emulator_url, manual_url=self.links.get("manual", ""),
            installer_url=self.links.get("installer", ""), download_url=p.url if p else "",
            sha256=p.sha256 if p else "", requires_stock_v15=bool(p and p.requires_stock_v15),
            notes=list(p.notes) if p else [], category=(self.categories or [""])[0], catalog_id=self.key,
            loader_family=p.loader_family if p else "")


@dataclass
class Resource:
    """Tool, editor, guide, video … from the remaining README sections."""
    section: str
    name: str
    url: str
    description: str = ""
    links: dict[str, str] = field(default_factory=dict)


@dataclass
class Catalog:
    entries: list[Entry] = field(default_factory=list)
    resources: list[Resource] = field(default_factory=list)
    revision: int = 0
    issued: str = ""
    built: str = ""
    source: str = "snapshot"            # snapshot / live / cache
    unknown_packages: list[Package] = field(default_factory=list)

    def by_key(self, key: str) -> Entry | None:
        return next((e for e in self.entries if e.key == key), None)

    def by_repo(self, repo: str) -> Entry | None:
        r = repo.lower()
        return next((e for e in self.entries if e.repo and e.repo.lower() == r), None)

    def by_sha256(self, sha: str) -> tuple[Entry | None, Package | None]:
        sha = sha.lower()
        for e in self.entries:
            for p in e.packages:
                if p.sha256 == sha:
                    return e, p
        return None, None

    def by_name(self, text: str, cutoff: float = 0.75) -> Entry | None:
        n = norm(text)
        if not n:
            return None
        names: dict[str, Entry] = {}
        for e in self.entries:
            for cand in [e.name, e.key, e.family, e.repo.split("/")[-1] if e.repo else "", *e.aliases]:
                if cand:
                    names.setdefault(norm(cand), e)
        if n in names:
            return names[n]
        # "karma fm1 firmware" → "karma": drop filler words and also compare the catalog names without them
        bare = norm(FILLER_WORDS.sub(" ", text.lower()))
        if bare and bare != n:
            stripped = {norm(FILLER_WORDS.sub(" ", k)): e for k, e in names.items()}
            if bare in names or bare in stripped:
                return names.get(bare) or stripped[bare]
        m = difflib.get_close_matches(n, list(names), n=1, cutoff=cutoff)
        if not m and bare and bare != n:
            m = difflib.get_close_matches(bare, list(names), n=1, cutoff=cutoff)
        return names[m[0]] if m else None

    def search(self, text: str) -> list[Entry]:
        words = text.lower().split()
        return [e for e in self.entries if all(w in e.search_text() for w in words)]

    def to_dict(self) -> dict:
        return {"format": "fm1c-catalog", "version": 1, "revision": self.revision, "issued": self.issued,
                "built": self.built, "entries": [e.to_dict() for e in self.entries],
                "resources": [asdict(r) for r in self.resources],
                "unknown_packages": [asdict(p) for p in self.unknown_packages]}

    @classmethod
    def from_dict(cls, d: dict, source: str = "snapshot") -> "Catalog":
        return cls(entries=[Entry.from_dict(x) for x in d.get("entries", [])],
                   resources=[Resource(**r) for r in d.get("resources", [])],
                   revision=d.get("revision", 0), issued=d.get("issued", ""), built=d.get("built", ""),
                   source=source,
                   unknown_packages=[Package(**p) for p in d.get("unknown_packages", [])])


# ================================================================ Mooring ===

def _split_label(label: str) -> tuple[str, str]:
    """'Felucca 8 Patterns 1.5.1 (felucca-1.51.fwsc)' -> ('Felucca 8 Patterns', '1.5.1')"""
    head = label.split("(")[0].strip()
    toks = head.split()
    for i in range(len(toks) - 1, 0, -1):
        if re.match(r"^[vV]?\d[\w.+-]*,?$", toks[i]):
            return " ".join(toks[:i]).rstrip(","), " ".join(toks[i:]).rstrip(",").split(",")[0]
    return head, ""


def parse_mooring(data: dict) -> dict:
    """-> {revision, issued, packages: [Package], loaders: {sha: family}}"""
    if data.get("format") != "mooring-catalog":
        raise ValueError(_("not a Mooring catalog"))
    loaders = {l["sha256"]: l.get("family", "") for l in data.get("loaders", {}).get("loaders", [])}
    pkgs = []
    for e in data.get("allowlist", {}).get("entries", []):
        dl = e.get("download") or {}
        name, ver = _split_label(e.get("label", ""))
        p = Package(label=e.get("label", ""), version=dl.get("releaseTag") or ver, product=e.get("product", ""),
                    sha256=(e.get("sha256") or "").lower(), raw_size=int(e.get("rawSize") or 0),
                    url=dl.get("url", ""), family=dl.get("family", ""), license=dl.get("license", ""),
                    notes=list(dl.get("notes") or []), requires_stock_v15=bool(dl.get("requiresStockV15")),
                    stock=bool(e.get("stock")), source_repo=dl.get("sourceRepo", ""),
                    loader_family=loaders.get(dl.get("loaderSHA256", ""), ""))
        p._name = name   # type: ignore[attr-defined]  (only needed when merging)
        pkgs.append(p)
    return {"revision": data.get("revision", 0), "issued": data.get("issued", ""), "packages": pkgs,
            "loaders": loaders}


def mooring_signature_status(catalog_bytes: bytes, intoto_line: bytes | None) -> str:
    """Only checks consistency (checksum in the in-toto statement == catalog file).
    A real signature check needs the public key (keyid dbe8230a406629ab);
    it is not published – which is why the app shows no "signed ✓"."""
    if not intoto_line:
        return _("no signature")
    try:
        env = json.loads(intoto_line)
        stmt = json.loads(base64.b64decode(env["payload"]))
        want = stmt["subject"][0]["digest"]["sha256"]
    except (ValueError, KeyError, IndexError, TypeError):
        return _("signature unreadable")
    if hashlib.sha256(catalog_bytes).hexdigest() != want:
        return _("checksum does not match")
    return _("checksum matches, key unknown")


# ================================================================= README ===

_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_LICENSE = re.compile(
    r"\b((?:GPL|LGPL|AGPL)-\d\.\d(?:-only|-or-later)?(?: with (?:the )?(?:FM1 SDK )?linking exception)?|MIT|"
    r"Apache-2\.0|MPL-2\.0|BSD-\d-Clause|Unlicense|WTFPL|CC0(?:-1\.0)?|CC BY(?:-SA)? \d\.\d|ISC|Zlib)\b")
_NO_LICENSE = re.compile(r"No licen[cs]e (?:stated|declared)", re.I)


def link_roles(text: str) -> list[str]:
    t = text.lower()
    roles = []
    if any(w in t for w in ("install", "flasher")):
        roles.append("installer")
    if any(w in t for w in ("emulator", "simulator", "demo", "play it", "try it", "browser version", "virtual fm-1",
                            "play online", "web player", "browser instrument", "play and install", "preview")):
        roles.append("emulator")
    if any(w in t for w in ("editor", "studio", "workbench")):
        roles.append("editor")
    if any(w in t for w in ("manual", "guide", "handbook")):
        roles.append("manual")
    if "source" in t:
        roles.append("source")
    if not roles:
        roles.append("site")
    return roles


_NOT_AUTHOR = {"inspired", "steered", "blended", "browse", "shaped", "played", "used", "shared", "step", "set",
               "driven", "followed", "replaced", "triggered", "generated", "controlled", "voiced", "read",
               "powered", "sorted", "grouped"}


def find_author(text: str) -> str:
    for m in re.finditer(r"(\w+)\s+[Bb]y\s+([^:,;\[\]]+)", text):
        if m.group(1).lower() in _NOT_AUTHOR:
            continue
        cand = re.split(r"\s+(?:with|where|that|for|and|in|on|made|inspired|combining|who|after|from|to)\s|\.\s|\.$",
                        m.group(2) + " ")[0].strip().rstrip(".")
        cand = re.sub(r"\s*\(\$[\d.]+\)", "", cand).strip()
        if not cand or len(cand) > 40 or len(cand.split()) > 5 or cand[0] in "'(":
            continue
        if cand.split()[0].lower() in ("a", "an", "the", "its", "device", "step") and not cand.startswith("The "):
            continue
        return cand
    m = re.search(r"\bBy ([A-Z][^.:,;]+)\.", text)
    return m.group(1).strip() if m else ""


_CAT_WORDS = [
    ("Groovebox", r"groovebox|drum machine|\d+-track|\w+-track|tracker|drum|beat|arranger|rhythm|looper|sequencer"),
    ("Sampler", r"sampl|chop|slic|sp-12|breakbeat|tape loop|field recording"),
    ("Akkorde", r"chord|omnichord|orchid|strum|harp|accompaniment"),
    ("Generativ", r"generative|self-playing|plays itself|plays from power-on|plays on switch-on|ambient|drone|"
                  r"composes|random"),
    ("Synth", r"synth|fm\b|polysynth|virtual analog|wavetable|organ|juno|dx7|cs-80|sid|mellotron|vocal|singer|"
              r"physical model|engine"),
    ("Spiele & Spaß", r"\bgames?\b|doom|\bnes\b|game boy|novelty|parody|\btoy\b|children|toddler|larp|fart|kids"),  # i18n: ignore
    ("Werkzeug", r"midi controller|controller firmware|macro pad|usb keyboard|platform|framework|skeleton|pictures|"
                 r"stopover|time-signal|console|learning|course|beginner"),
]


def categorize(name: str, text: str, section: str) -> list[str]:
    if section == "official":
        return ["Original"]
    first = text.split(". ")[0].lower()
    full = (name + " " + text).lower()
    score: dict[str, float] = {}
    for cat, rx in _CAT_WORDS:
        n_first = len(re.findall(rx, first))
        n_all = len(re.findall(rx, full))
        if n_first or n_all:
            score[cat] = n_first * 3 + n_all
    if section == "notsynth":
        game = score.get("Spiele & Spaß", 0)  # i18n: ignore
        return ["Spiele & Spaß"] if game >= score.get("Werkzeug", 0) and game else ["Werkzeug"]  # i18n: ignore
    ranked = sorted(score.items(), key=lambda x: -x[1])
    top = ranked[0][1] if ranked else 0
    cats = [c for c, v in ranked if v >= max(2, top / 2) or v == top]
    return cats[:3] or ["Synth"]


_LINEAGE_PATTERNS = [
    (r"\b(?i:built on|based on|fork of|forked from|derived from|derives from|starting from|on the platform of|"
     r"descend\w* from|continuing|remix of|grew out of|reuses|on)\s+(?:the\s+)?([A-Z][\w!-]*(?:\s+[A-Z][\w!-]*)?)", 1),
    (r"\b([A-Z][\w!-]*(?:\s+[A-Z][\w!-]*)?)(?:\s+[\d.]+)?[ -](?:fork|derivative|branch|remix|platform)\b", 1),
    (r"\bon (fm1-base)\b", 1),
    (r"\b([A-Z][\w!-]*)-based\b", 1),
    (r"\bbuilt on top of the (V15) firmware", 1),
]


def lineage_mentions(text: str) -> list[str]:
    out = []
    for rx, g in _LINEAGE_PATTERNS:
        for m in re.finditer(rx, text):
            out.append(m.group(g).strip().rstrip("."))
    if re.search(r"from scratch|original (?:four-part )?groovebox|written from scratch", text, re.I):
        out.append("__own__")
    if re.search(r"FM1 Open", text):
        out.append("FM1 Open")
    return out


def parse_readme(text: str) -> tuple[list[dict], list[Resource]]:
    """-> (firmware entries as raw data, resources)"""
    items, res = [], []
    sec = sub = ""
    for line in text.splitlines():
        if line.startswith("## "):
            sec, sub = line[3:].strip(), ""
            continue
        if line.startswith("### "):
            sub = line[4:].strip()
            continue
        m = re.match(r"- \[([^\]]+)\]\(([^)\s]+)\)\s*(?:and \[[^\]]+\]\([^)]+\)\s*)?-\s*(.*)$", line)
        if not m or sec in ("Contents", "Contributing"):
            continue
        name, url, rest = m.group(1).strip(), m.group(2), m.group(3).strip()
        links = _LINK.findall(rest)
        desc = _LINK.sub(lambda x: x.group(1), rest)
        kind = FIRMWARE_SECTIONS.get(sub if sec == "Custom firmware" else sec)
        if kind:
            items.append({"section": kind, "name": name, "url": url, "text": desc, "links": links})
        else:
            lk = {}
            for t, u in links:
                for r in link_roles(t):
                    lk.setdefault(r, u)
            res.append(Resource(section=sec, name=name, url=url, description=desc, links=lk))
    return items, res


def _readme_entry(it: dict) -> Entry:
    name, url, desc = it["name"], it["url"], it["text"]
    repo = repo_of(url)
    links: dict[str, str] = {}
    sub = re.match(r"https?://github\.com/[\w.-]+/[\w.-]+/(?:tree|blob)/", url)
    if not repo:
        links["site"] = url
        repo = ""
    elif sub:
        links["source"] = url
    for t, u in it["links"]:
        for r in link_roles(t):
            if r == "source" and not repo and repo_of(u):
                repo = repo_of(u)
            links.setdefault(r, u)
    lic = ""
    m = _LICENSE.search(desc)
    if m:
        lic = m.group(1)
    elif _NO_LICENSE.search(desc):
        lic = "keine Lizenz angegeben"  # i18n: ignore
    elif it["section"] == "closed":
        lic = "proprietär"  # i18n: ignore
    flags = []
    low = desc.lower()
    if re.search(r"\bbeta\b", low):
        flags.append("beta")
    if re.search(r"\balpha\b", low):
        flags.append("alpha")
    if re.search(r"experiment", low):
        flags.append("experimentell")
    if re.search(r"work in progress|in progress|\bearly\b|prototype|proof of concept|development snapshot|"
                 r"\bwip\b|drafts?\b|teased", low):
        flags.append("wip")
    if re.search(r"commercial|\$\d", low):
        flags.append("kostenpflichtig")
    if re.search(r"no release|build from source|source only|no binaries", low):
        flags.append("quellcode")
    author = find_author(desc)
    aliases = re.findall(r"\(([^)]+)\)", name)
    clean_desc = re.sub(r"\s*\b(?:GPL|LGPL|AGPL)-\d\.\d[^.]*\.\s*$|\s*(?:MIT|MPL-2\.0|Apache-2\.0|CC0|Unlicense)\.\s*$",
                        "", desc).strip()
    e = Entry(key=slug(name), name=name, author=author, description=clean_desc, section=it["section"],
              license=lic, repo=repo, links=links, flags=flags, aliases=aliases)
    e.lineage_text = "; ".join(x for x in lineage_mentions(desc) if x != "__own__")
    e._mentions = lineage_mentions(desc)   # type: ignore[attr-defined]
    e.categories = categorize(name, desc, it["section"])
    return e


# ================================================================ Merge ===

_LOADER_LINEAGE = {"stock": "Original M-VAVE", "feluccaG1": "Felucca", "feluccaG2": "Felucca", "sloopG3": "SLOOP"}


def merge(readme_text: str | None, mooring: dict | None, builtin_keys: dict | None = None) -> Catalog:
    """Merge README entries and Mooring packages into one catalog (both directions)."""
    items, res = parse_readme(readme_text) if readme_text else ([], [])
    cat = Catalog(resources=res, built=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    entries: list[Entry] = []
    stock = Entry(key="stock", name="M-VAVE Original-Firmware", author="M-VAVE", section="official",
                  categories=["Original"], lineage=["Original M-VAVE"], license="proprietär",  # i18n: ignore
                  description=STOCK_DESCRIPTION)
    for it in items:
        if it["section"] == "official":
            n = it["name"].lower()
            if it["url"].endswith(".fwsc"):
                stock.links.setdefault("firmware_" + n.split()[-1].lower(), it["url"])
            elif repo_of(it["url"]) == "m-vave/fm1_open":
                e = _readme_entry(it)
                e.key, e.lineage = "fm1-open", ["Original M-VAVE"]
                e.categories = ["Original", "Groovebox"]
                entries.append(e)
            elif "product page" in n:
                stock.links["site"] = it["url"]
            elif "downloads" in n:
                stock.links["downloads"] = it["url"]
            elif "manual" in n:
                stock.links["manual"] = it["url"]
            elif "release notes" in n:
                stock.links["release_notes"] = it["url"]
            elif "midi" in n:
                stock.links["midi"] = it["url"]
            continue
        entries.append(_readme_entry(it))
    entries.insert(0, stock)

    if mooring:
        cat.revision, cat.issued = mooring["revision"], mooring["issued"]
        pkgs: list[Package] = mooring["packages"]
        by_repo = {e.repo.lower(): e for e in reversed(entries) if e.repo and "source" not in e.links}
        by_name = {}
        for e in entries:
            for c in [e.name, e.key, *e.aliases, e.name.split("(")[0]]:
                by_name.setdefault(norm(c), e)
        fam_entry: dict[str, Entry] = {}
        orphans: list[Package] = []
        for p in pkgs:
            e = None
            if p.stock:
                e = stock
            elif p.family:
                e = fam_entry.get(p.family)
                if e is None:
                    gh = repo_of(p.source_repo).lower()
                    e = by_repo.get(gh) or by_name.get(norm(p.family)) or by_name.get(norm(p._name))
                    if e is None:
                        e = Entry(key=p.family, name=p._name or p.family, section="mooring",
                                  repo=repo_of(p.source_repo), license=p.license,
                                  description=MOORING_ONLY_DESCRIPTION)
                        e.categories = ["Synth"]
                        entries.append(e)
                        by_name[norm(e.name)] = e
                    fam_entry[p.family] = e
                    if not e.family:
                        e.family = p.family
            if e is None:                         # package without download: match by name
                e = by_name.get(norm(p._name))
            if e is None:
                orphans.append(p)
                continue
            e.packages.append(p)
            if p.loader_family and not e.lineage:
                lin = _LOADER_LINEAGE.get(p.loader_family)
                if lin:
                    e.lineage = [lin]
            if not e.license and p.license:
                e.license = p.license
            if "beta" in p.notes and p is e.latest and "beta" not in e.flags:
                pass
        cat.unknown_packages = orphans
        for e in entries:
            # newest first: packages with a download before bare identities, then catalog order (new = at the end)
            e.packages = list(reversed([p for p in e.packages if p.downloadable])) + \
                list(reversed([p for p in e.packages if not p.downloadable]))
    _resolve_lineage(entries)
    # make keys unique; built-in firmwares keep their key
    builtin_keys = builtin_keys or {"melodee": "keremimo/melodee", "skiff": "illuminus5/skiff",
                                    "sloop": "isod89/sloop-fm1", "karma": "eatyourgreensstudio-dotcom/karma-fm1"}
    seen: set[str] = set()
    for e in entries:
        for k, r in builtin_keys.items():
            if e.repo.lower() == r.lower() and "source" not in e.links:
                e.key, e.builtin = k, True
        if not e.family and e.key in FELUCCA_PROTOCOL_FAMILIES:
            e.family = e.key
        base, i = e.key, 2
        while e.key in seen:
            e.key = f"{base}-{i}"
            i += 1
        seen.add(e.key)
        fam = e.family or e.key
        if fam in FELUCCA_PROTOCOL_FAMILIES:
            e.protocol, e.protocol_evidence = "felucca", f"known Felucca protocol family ({fam})"
        if not e.author and e.repo:
            e.author = e.repo.split("/")[0]
        if e.author.endswith("'s author"):
            ref = next((x for x in entries if norm(x.name) == norm(e.author[:-9])), None)
            e.author = (ref.author if ref and ref.author else "") or (e.repo.split("/")[0] if e.repo else "")
        for x in list(e.__dict__):
            if x.startswith("_"):
                delattr(e, x)
    for p in cat.unknown_packages:
        p.__dict__.pop("_name", None)
    cat.entries = entries
    return with_builtins(cat)


# the line each built-in firmware descends from, for an entry the catalog does not have (yet)
BUILTIN_LINEAGE = {"karma": (["SLOOP"], "SLOOP; Felucca")}


def with_builtins(cat: Catalog) -> Catalog:
    """the app's built-in firmwares the catalog does not list (KARMA: on no awesome-fm-1 list yet) as entries of their
    own, so the catalog view shows them and “Install …” works – an entry the catalog does have stays as it is (it is
    marked built-in by its repository in merge())"""
    from . import firmware
    have = {e.key for e in cat.entries} | {e.repo.lower() for e in cat.entries if e.repo}
    for sp in firmware.BUILTIN:
        if sp.key in have or sp.repo.lower() in have or not sp.versions:
            continue
        lineage, text = BUILTIN_LINEAGE.get(sp.key, ([], ""))
        links = {k: v for k, v in (("site", sp.homepage), ("installer", sp.installer_url), ("editor", sp.editor_url),
                                   ("emulator", sp.emulator_url), ("manual", sp.manual_url)) if v}
        pkgs = [Package(label=f"{sp.name} {r.tag.lstrip('v')} ({next(iter(r.assets), '')})",
                        version=r.tag, product=sp.product, sha256=r.sha256, url=next(iter(r.assets.values()), ""),
                        family=sp.family, license=sp.license, source_repo=f"https://github.com/{sp.repo}",
                        loader_family=sp.loader_family)
                for r in firmware.pinned_releases(sp, live=False) if r.sha256]
        cat.entries.append(Entry(key=sp.key, name=sp.name, author=sp.author, description=sp.description,
                                 section="open", categories=[sp.category] if sp.category else [], lineage=list(lineage),
                                 lineage_text=text, license=sp.license, repo=sp.repo, links=links, packages=pkgs,
                                 family=sp.family, protocol="felucca",
                                 protocol_evidence=f"built-in firmware of the app ({sp.key})", builtin=True))
    return cat


def _resolve_lineage(entries: list[Entry]):
    names = {}
    for e in entries:
        for c in [e.name, e.key, *e.aliases, e.name.split("(")[0], e.name.split()[0] if e.name else ""]:
            names.setdefault(norm(c), e)
    roots = {"felucca": "Felucca", "sloop": "SLOOP", "fomni": "FoMni", "fm1open": "Original M-VAVE",
             "v15": "Original M-VAVE", "fm1base": "Felucca", "feluccas": "Felucca"}

    def chain(e: Entry, depth=0) -> list[str]:
        if e.lineage or depth > 6:
            return e.lineage
        res: list[str] = []
        for men in getattr(e, "_mentions", []):
            if men == "__own__":
                continue
            n = norm(men)
            n1 = norm(men.split()[0])
            if n in roots or n1 in roots:
                base = roots.get(n) or roots[n1]
                res = (["Felucca", "FoMni"] if base == "FoMni" else [base])
                break
            ref = names.get(n) or names.get(n1)
            if ref is not None and ref is not e:
                sub = chain(ref, depth + 1)
                if sub:
                    res = sub
                    break
        if not res:                       # weaker: first known lineage name in the text
            m = re.search(r"\b(Felucca|SLOOP|FoMni|fm1-base|FM1 Open|X0X|ChoralRoot|GHOULBOX|Golana|Melodee)\b",
                          e.description)
            if m:
                ref = names.get(norm(m.group(1)))
                base = roots.get(norm(m.group(1)))
                res = ([base] if base else (chain(ref, depth + 1) if ref is not None and ref is not e else []))
        if not res and "__own__" in getattr(e, "_mentions", []):
            res = ["eigenständig"]  # i18n: ignore
        e.lineage = res
        return res

    for e in entries:
        chain(e)
        if "FoMni" in e.lineage_text and "FoMni" not in e.lineage:
            e.lineage = ["Felucca", "FoMni"]
        e.lineage_text = re.sub(r"^(?:A|An|The)\s+", "", e.lineage_text)
        if e.key in ("x0x", "fomni") and "Felucca" not in e.lineage:
            e.lineage = ["Felucca"] + (["FoMni"] if e.key == "fomni" else [])
    for e in entries:
        if not e.lineage and e.section in ("open", "closed", "notsynth", "mooring"):
            e.lineage = ["eigenständig"] if re.search(r"original|from scratch|own engine", e.description, re.I) else []  # i18n: ignore


# ============================================================== loading ===

# ======================================================== Screenshots ===

_BADGE = re.compile(r"shields\.io|badge|/actions/|travis|codecov|awesome\.re|img\.youtube|\.svg(?:\?|$)", re.I)


def first_image(markdown: str, repo: str, branch: str = "HEAD") -> str:
    """first real image (not a badge) from a README – as an absolute address."""
    for m in re.finditer(r"!\[[^\]]*\]\(\s*<?([^)\s>]+)>?[^)]*\)|<img[^>]+src=[\"']([^\"']+)[\"']", markdown):
        url = (m.group(1) or m.group(2) or "").strip()
        if not url or _BADGE.search(url):
            continue
        if url.startswith("//"):
            url = "https:" + url
        if not re.match(r"https?://", url):
            url = f"https://raw.githubusercontent.com/{repo}/{branch}/{url.lstrip('./')}"
        url = re.sub(r"^https://github\.com/([\w.-]+/[\w.-]+)/(?:blob|raw)/([^/]+)/",
                     r"https://raw.githubusercontent.com/\1/\2/", url)
        return url
    return ""


def load_snapshot(path: Path = SNAPSHOT_FILE) -> Catalog:
    try:
        return Catalog.from_dict(json.loads(path.read_text("utf-8")), "snapshot")
    except (OSError, ValueError, TypeError):
        return Catalog(source="leer")


def cached_file() -> Path:
    return cache_dir() / "catalog.json"


def load(prefer_cache: bool = True) -> Catalog:
    """Snapshot or a newer live state from the cache (without network)."""
    snap = load_snapshot()
    if prefer_cache and cached_file().exists():
        try:
            live = Catalog.from_dict(json.loads(cached_file().read_text("utf-8")), "cache")
            if live.entries and (live.revision, live.built) >= (snap.revision, snap.built):
                _carry_over(snap, live)
                return with_builtins(live)
        except (OSError, ValueError, TypeError):
            pass
    return with_builtins(snap)


def _carry_over(old: Catalog, new: Catalog):
    """Take over details only the snapshot has (stars, screenshot …)."""
    for e in new.entries:
        o = old.by_key(e.key)
        if o:
            e.stars = e.stars if e.stars is not None else o.stars
            e.screenshot = e.screenshot or o.screenshot
            e.pushed_at = e.pushed_at or o.pushed_at


def cache_age() -> float | None:
    f = cached_file()
    return time.time() - f.stat().st_mtime if f.exists() else None


def needs_refresh() -> bool:
    a = cache_age()
    return a is None or a > MAX_AGE


class Offline(Exception):
    pass


def _get(url: str, etag_file: Path | None = None, timeout=20) -> tuple[bytes | None, str]:
    """-> (data or None on 304, ETag)"""
    hdr = {"User-Agent": UA}
    if etag_file and etag_file.exists():
        hdr["If-None-Match"] = etag_file.read_text().strip()
    req = urllib.request.Request(url, headers=hdr)
    try:
        with urlopen(req, timeout=timeout) as r:
            return r.read(), r.headers.get("ETag", "")
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return None, ""
        raise Offline(_("HTTP {code} at {url}").format(code=e.code, url=url)) from e
    except (urllib.error.URLError, OSError) as e:
        raise Offline(str(getattr(e, "reason", e))) from e


def refresh(log=lambda *_a: None) -> Catalog:
    """Fetch the live state (Mooring + README), merge it, store it in the cache. Runs in the background."""
    d = cache_dir()
    out = {}
    for name, url in (("mooring.json", MOORING_URL), ("README.md", README_URL)):
        f, ef = d / name, d / (name + ".etag")
        log(_("loading {name} …").format(name=url.rsplit('/', 1)[-1]))
        data, etag = _get(url, ef if f.exists() else None)
        if data is not None:
            f.write_bytes(data)
            if etag:
                ef.write_text(etag)
        out[name] = f.read_bytes()
    moor = parse_mooring(json.loads(out["mooring.json"]))
    cat = merge(out["README.md"].decode("utf-8"), moor)
    snap = load_snapshot()
    _carry_over(snap, cat)
    cat.source = "live"
    old = load()
    tmp = cached_file().with_suffix(".tmp")
    tmp.write_text(json.dumps(cat.to_dict(), ensure_ascii=False), "utf-8")
    tmp.replace(cached_file())
    record_changes(old, cat)
    return cat


# ======================================================= news ===

def changes_file() -> Path:
    return cache_dir() / "changes.json"


def record_changes(old: Catalog, new: Catalog):
    """remember new packages (new version in the catalog) and new firmwares – for the News tab."""
    if not old.entries:
        return
    try:
        log = json.loads(changes_file().read_text("utf-8"))
    except (OSError, ValueError):
        log = []
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    known = {p.sha256 for e in old.entries for p in e.packages}
    old_keys = {e.key for e in old.entries}
    for e in new.entries:
        if e.key not in old_keys:
            log.append({"key": e.key, "kind": "neu",  # i18n: ignore
                        "text": _("{name} is new in the catalog").format(name=e.name), "date": now})
        for p in e.packages:
            if p.sha256 and p.sha256 not in known and p.downloadable:
                log.append({"key": e.key, "kind": "version", "tag": p.version,
                            "text": _("{name} {version} is available").format(name=e.name, version=p.version),
                            "date": now})
    changes_file().write_text(json.dumps(log[-300:], ensure_ascii=False, indent=1), "utf-8")


def recorded_changes() -> list[dict]:
    try:
        return json.loads(changes_file().read_text("utf-8"))
    except (OSError, ValueError):
        return []


# ===================================================== package identity ===

def product_of(raw: bytes) -> str:
    """Identity of a .fwsc package (e.g. 'FM-1_900'): one marker byte after each of the first
    20 blocks of 0x30 bytes (format as in Melodee's tools/fm1_install.py, GPL-3.0)."""
    blocks, blk, keep = 20, 0x30, 0x2F
    if len(raw) < blocks * blk:
        return ""
    return "".join(chr((raw[i * blk + keep] - i - 1) & 0xFF) for i in range(blocks)
                   if raw[i * blk + keep] != 0x7D)


def by_product(identity: str, cat: Catalog | None = None) -> list[Entry]:
    """all catalog entries whose packages report this identity (e.g. many firmwares share "FM-1_900").
    Built-in firmwares first, then alphabetical."""
    if not identity:
        return []
    try:
        cat = cat or load()
    except Exception:  # noqa: BLE001 – no catalog, no candidates
        return []
    out = [e for e in cat.entries if any(p.product == identity for p in e.packages)]
    out.sort(key=lambda e: (not e.builtin, e.name.lower()))
    return out


def spec_by_sha256(sha: str, cat: Catalog | None = None):
    """FirmwareSpec for a package with exactly this checksum (or None)"""
    try:
        cat = cat or load()
    except Exception:  # noqa: BLE001
        return None
    e, p = cat.by_sha256(sha)
    if e is None:
        return None
    sp = e.to_spec(p)
    if p is not None and not sp.sha256:          # built-in firmware: pin exactly this package
        import dataclasses
        sp = dataclasses.replace(sp, sha256=p.sha256, product=p.product or sp.product,
                                 loader_family=p.loader_family or sp.loader_family)
    return sp


def identify_file(path: Path, cat: Catalog | None = None) -> dict:
    raw = Path(path).read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    ident = product_of(raw)
    if not re.fullmatch(r"[\w.+-]{2,40}", ident or ""):
        ident = ""
    e, p = (cat.by_sha256(sha) if cat else (None, None))
    return {"sha256": sha, "size": len(raw), "product": ident, "entry": e, "package": p}


# ============================================================ GitHub ===
# Enrichment, only on request/lazily: detail view + a small background budget.
# Without a token GitHub allows 60 API requests per hour; conditional requests (ETag, 304) do not count.

class RateLimited(Exception):
    def __init__(self, msg: str, reset: int = 0):
        super().__init__(msg)
        self.reset = reset




def github_token() -> str:
    return (config.module("catalog", {}).get("github_token") or "").strip()


def _gh_cache(path: str) -> Path:
    d = cache_dir() / "github"
    d.mkdir(parents=True, exist_ok=True)
    return d / (re.sub(r"[^\w.-]+", "_", path.strip("/")) + ".json")


def _read_cache(f: Path) -> dict | None:
    try:
        return json.loads(f.read_text("utf-8"))
    except (OSError, ValueError):
        return None


def limit_message(reset: int) -> str:
    when = time.strftime("%H:%M", time.localtime(reset)) if reset else ""
    if when:
        return _("GitHub limit reached – try again later (from {time}). With your own GitHub token (settings of "
                 "“Firmware+”) you get more requests.").format(time=when)
    return _("GitHub limit reached – try again later. With your own GitHub token (settings of “Firmware+”) you get "
             "more requests.")


def gh_api(path: str, ttl: float = MAX_AGE) -> tuple[object, str]:
    """GET api.github.com/<path> with cache + ETag. -> (data, hint). The hint is empty or a
    German message when old data comes from the cache. Without cached data: RateLimited/Offline."""
    f = _gh_cache(path)
    c = _read_cache(f)
    if c and time.time() - c.get("fetched", 0) < ttl:
        return c["data"], ""
    hdr = {"User-Agent": UA, "Accept": "application/vnd.github+json"}
    if c and c.get("etag"):
        hdr["If-None-Match"] = c["etag"]
    tok = github_token()
    if tok:
        hdr["Authorization"] = f"Bearer {tok}"
    req = urllib.request.Request("https://api.github.com/" + path.lstrip("/"), headers=hdr)
    try:
        with urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
            f.write_text(json.dumps({"etag": r.headers.get("ETag", ""), "fetched": time.time(), "data": data},
                                    ensure_ascii=False), "utf-8")
            return data, ""
    except urllib.error.HTTPError as e:
        if e.code == 304 and c:
            c["fetched"] = time.time()
            f.write_text(json.dumps(c, ensure_ascii=False), "utf-8")
            return c["data"], ""
        if e.code in (403, 429):
            rem = e.headers.get("X-RateLimit-Remaining") if e.headers else None
            reset = int((e.headers.get("X-RateLimit-Reset") if e.headers else 0) or 0)
            msg = limit_message(reset) if rem == "0" or e.code == 429 else \
                _("GitHub is refusing access right now (403) – try again later.")
            if c:
                return c["data"], msg
            raise RateLimited(msg, reset) from e
        if e.code == 404:
            raise Offline(_("Repository not found on GitHub (deleted, renamed or private)")) from e
        if c:
            return c["data"], _("GitHub answers with error {code} – showing the saved state").format(code=e.code)
        raise Offline(_("GitHub error {code}").format(code=e.code)) from e
    except (urllib.error.URLError, OSError, ValueError) as e:
        if c:
            return c["data"], _("offline – showing the saved state")
        raise Offline(_("no connection to GitHub")) from e


def gh_cached(path: str) -> object | None:
    c = _read_cache(_gh_cache(path))
    return c["data"] if c else None


def norm_releases(data) -> list[dict]:
    out = []
    for r in data if isinstance(data, list) else []:
        if r.get("draft"):
            continue
        out.append({"tag": r.get("tag_name", ""), "name": r.get("name") or r.get("tag_name", ""),
                    "date": (r.get("published_at") or r.get("created_at") or "")[:10],
                    "prerelease": bool(r.get("prerelease")), "url": r.get("html_url", ""),
                    "notes": r.get("body") or "",
                    "assets": [{"name": a.get("name", ""), "size": a.get("size", 0),
                                "url": a.get("browser_download_url", "")} for a in r.get("assets", [])]})
    return out


def enrich(repo: str, ttl: float = MAX_AGE) -> dict:
    """Repo info + releases. -> {stars, description, topics, pushed_at, releases, message}"""
    info, msg1 = gh_api(f"repos/{repo}", ttl)
    rels, msg2 = gh_api(f"repos/{repo}/releases?per_page=15", ttl)
    info = info if isinstance(info, dict) else {}
    return {"stars": info.get("stargazers_count"), "description": info.get("description") or "",
            "topics": info.get("topics") or [], "pushed_at": (info.get("pushed_at") or "")[:10],
            "homepage": info.get("homepage") or "", "releases": norm_releases(rels), "message": msg1 or msg2}


def cached_enrichment(repo: str) -> dict | None:
    info, rels = gh_cached(f"repos/{repo}"), gh_cached(f"repos/{repo}/releases?per_page=15")
    if info is None and rels is None:
        return None
    info = info if isinstance(info, dict) else {}
    return {"stars": info.get("stargazers_count"), "description": info.get("description") or "",
            "topics": info.get("topics") or [], "pushed_at": (info.get("pushed_at") or "")[:10],
            "homepage": info.get("homepage") or "", "releases": norm_releases(rels or []), "message": ""}


def _stars_file() -> Path:
    d = cache_dir() / "github"
    d.mkdir(parents=True, exist_ok=True)
    return d / "stars.json"


def cached_stars() -> dict[str, dict]:
    """repo (lower case) -> {"stars": int | None, "pushed_at": "YYYY-MM-DD", "t": fetched}"""
    c = _read_cache(_stars_file())
    return c if isinstance(c, dict) else {}


def _star_batches(repos: list[str], max_len: int = 230) -> list[list[str]]:
    """GitHub search allows at most 256 characters per query – several ``repo:`` qualifiers per query"""
    out, cur, n = [], [], len(" fork:true")
    for r in repos:
        add = len(r) + 6
        if cur and n + add > max_len:
            out.append(cur)
            cur, n = [], len(" fork:true")
        cur.append(r)
        n += add
    if cur:
        out.append(cur)
    return out


def fetch_stars(repos, ttl: float = MAX_AGE, progress=None, sleep=time.sleep, max_wait: float = 75) -> dict[str, dict]:
    """Stars and last push of many repos at once, via the GitHub search (one request per ~10 repos instead of
    one per repo). Results are cached for ``ttl``; repos GitHub does not know are remembered as unknown.
    If the search limit is reached (10 per minute without a token) it waits up to ``max_wait`` seconds,
    otherwise it stops and keeps what it has. -> the whole cache"""
    have = cached_stars()
    now = time.time()
    todo = sorted({r.strip("/") for r in repos if r and "/" in r
                   and now - have.get(r.strip("/").lower(), {}).get("t", 0) >= ttl})
    done = 0

    def save():
        try:
            _stars_file().write_text(json.dumps(have, ensure_ascii=False), "utf-8")
        except OSError:
            pass

    def query(batch: list[str]) -> bool:
        nonlocal done
        q = " ".join(f"repo:{r}" for r in batch) + " fork:true"
        for attempt in range(2):
            try:
                data, _hint = gh_api("search/repositories?per_page=100&q=" + urllib.parse.quote(q), ttl)
                break
            except RateLimited as e:
                wait = (e.reset - time.time() + 2) if e.reset else max_wait
                if attempt or wait > max_wait:
                    return False
                sleep(max(1.0, wait))
            except Offline:
                # GitHub answers 422 if one repo of the query does not exist (renamed, deleted) – split it up
                if len(batch) == 1:
                    have[batch[0].lower()] = {"stars": None, "pushed_at": "", "t": time.time()}
                    done += 1
                    return True
                mid = len(batch) // 2
                return query(batch[:mid]) and query(batch[mid:])
        else:
            return False
        found = {}
        for it in (data.get("items") or []) if isinstance(data, dict) else []:
            found[(it.get("full_name") or "").lower()] = it
        for r in batch:
            it = found.get(r.lower())
            have[r.lower()] = {"stars": it.get("stargazers_count") if it else None,
                               "pushed_at": ((it or {}).get("pushed_at") or "")[:10], "t": time.time()}
        done += len(batch)
        if progress:
            progress(done, len(todo))
        return True

    for batch in _star_batches(todo):
        ok = query(batch)
        save()
        if not ok:
            break
    return have


def raw_exists(repo: str, path: str) -> bool:
    """File present in the repo? (raw.githubusercontent.com – no API, no rate limit)"""
    req = urllib.request.Request(f"https://raw.githubusercontent.com/{repo}/HEAD/{path}", method="HEAD",
                                 headers={"User-Agent": UA})
    try:
        with urlopen(req, timeout=15) as r:
            return r.status < 400
    except (urllib.error.URLError, OSError):
        return False


# ------------------------------------------- further sources (Pages, fmhub.json, repo files, search)
FM1_WORDS = re.compile(r"fm[-_ ]?1|felucca|m[-_ ]?vave|sloop|fwsc", re.I)
FILLER_WORDS = re.compile(r"fm[-_ ]?1|m[-_ ]?vave|\bfirmware\b|\bfw\b|\bfor\b|\bfür\b|\bcustom\b", re.I)  # i18n: ignore


def fetch_text(url: str, max_bytes: int = 3 << 20, timeout: float = 15) -> str | None:
    """fetch a small text file/page; None on error (no network, 404 …)."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urlopen(req, timeout=timeout) as r:
            return r.read(max_bytes).decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, ValueError):
        return None


def pages_url(repo: str) -> str:
    """'owner/name' -> https://owner.github.io/name/ (default GitHub Pages address)"""
    owner, _sep, name = repo.partition("/")
    return f"https://{owner.lower()}.github.io/{name}/" if owner and name else ""


def fmhub_info(repo: str) -> dict:
    """fmhub.json in the repo (short description for the firmware directory): what, author, play, editor, cheat …"""
    txt = fetch_text(f"https://raw.githubusercontent.com/{repo}/HEAD/fmhub.json", 256 << 10)
    try:
        d = json.loads(txt) if txt else {}
    except ValueError:
        return {}
    return {k: v for k, v in d.items() if isinstance(v, str) and v.strip()} if isinstance(d, dict) else {}


_META = re.compile(r"\{[^{}]*\"pkg\"\s*:\s*\"[^\"]+\.fwsc\"[^{}]*\}")
_FWSC_REF = re.compile(r"[\"'(=]\s*((?:https?://|\.{0,2}/)?[\w%./-]*?[\w%.-]+\.fwsc)(?=[\"')\s?#])", re.I)


def installer_meta(html: str, base_url: str) -> dict:
    """Web installer page of the Felucca family (make_site.py): embedded
    {"version", "product", "pkg", "sha256"} – otherwise the first link to a .fwsc file.
    -> {url, version, product, sha256} (missing fields empty) or {}."""
    if not html:
        return {}
    m = _META.search(html)
    if m:
        try:
            meta = json.loads(m.group(0))
        except ValueError:
            meta = None
        if isinstance(meta, dict) and isinstance(meta.get("pkg"), str):
            sha = str(meta.get("sha256") or "").lower()
            return {"url": urllib.parse.urljoin(base_url, meta["pkg"]), "version": str(meta.get("version") or ""),
                    "product": str(meta.get("product") or ""),
                    "sha256": sha if re.fullmatch(r"[0-9a-f]{64}", sha) else ""}
    refs = [r for r in _FWSC_REF.findall(html) if not re.search(r"(^|/)fm-?1\.fwsc$", r, re.I)]  # not the original
    if refs:
        return {"url": urllib.parse.urljoin(base_url, refs[0]), "version": "", "product": "", "sha256": ""}
    return {}


def find_installer_package(urls: list[str]) -> dict:
    """the first installer page that names a package. -> installer_meta(...) + {"page": url} or {}."""
    seen = set()
    for u in urls:
        if not u or u in seen:
            continue
        seen.add(u)
        meta = installer_meta(fetch_text(u) or "", u)
        if meta:
            return {**meta, "page": u}
    return {}


def repo_fwsc_files(repo: str, refs: tuple[str, ...] = ("HEAD", "gh-pages")) -> list[dict]:
    """.fwsc files that sit directly in the repo (some publish this way instead of via releases).
    -> [{path, ref, url}] – those with the highest version in the name first. One API request per branch."""
    out = []
    for ref in refs:
        try:
            data, _hint = gh_api(f"repos/{repo}/git/trees/{ref}?recursive=1")
        except (RateLimited, Offline):
            continue
        for t in (data.get("tree") or []) if isinstance(data, dict) else []:
            p = t.get("path", "")
            if t.get("type") == "blob" and p.lower().endswith(".fwsc") and not re.search(r"(^|/)fm-?1\.fwsc$", p, re.I):
                out.append({"path": p, "ref": ref,
                            "url": f"https://raw.githubusercontent.com/{repo}/{ref}/{urllib.parse.quote(p)}"})
        if out:
            break

    def ver(p):
        m = re.search(r"(\d+(?:\.\d+)+)", p["path"].rsplit("/", 1)[-1])
        return tuple(int(x) for x in m.group(1).split(".")) if m else ()
    out.sort(key=ver, reverse=True)
    return out


def search_repos(text: str, limit: int = 8) -> list[dict]:
    """GitHub search for FM-1 firmwares. -> [{repo, description, stars, fork, exact}] – only hits that look like
    FM-1 (name, description or topics), the best-matching name first.

    Many firmwares are created on GitHub as a fork (e.g. of Felucca); the search otherwise hides forks,
    hence ``fork:true``. Plain copies (a fork with the same name as a found original) are dropped."""
    words = [w for w in re.split(r"[\s_]+", FILLER_WORDS.sub(" ", text).strip())
             if w and not FM1_WORDS.fullmatch(w)]
    if not words:
        return []
    q = " ".join(words).lower()                    # (same search = same cache entry)
    want = norm(q)

    def bare_name(repo):
        return norm(FM1_WORDS.sub("", repo.split("/")[1]))
    found: dict[str, dict] = {}
    for query in (f"{q} fm-1", f"{q} fm1", f"{q} m-vave", f"{q} in:name"):
        try:
            data, _hint = gh_api("search/repositories?per_page=30&q=" + urllib.parse.quote(query + " fork:true"))
        except (RateLimited, Offline):
            break
        for it in (data.get("items") or []) if isinstance(data, dict) else []:
            name = it.get("full_name", "")
            text_all = " ".join([name, it.get("description") or "", " ".join(it.get("topics") or [])])
            if name and name not in found and FM1_WORDS.search(text_all):
                found[name] = {"repo": name, "description": it.get("description") or "",
                               "stars": it.get("stargazers_count") or 0, "fork": bool(it.get("fork")),
                               "exact": bare_name(name) == want or norm(name.split("/")[1]) == want}
        if any(r["exact"] for r in found.values()) or len(found) >= 3 * limit:
            break
    originals = {r["repo"].split("/")[1].lower() for r in found.values() if not r["fork"]}
    hits = [r for r in found.values() if not (r["fork"] and r["repo"].split("/")[1].lower() in originals)]

    def score(r):
        n = bare_name(r["repo"])
        return (r["exact"], bool(n) and (want in n or n in want), difflib.SequenceMatcher(None, want, n).ratio(),
                not r["fork"], r["stars"])
    return sorted(hits, key=score, reverse=True)[:limit]


def repo_readme(repo: str, ttl: float = 7 * MAX_AGE) -> str:
    f = _gh_cache(f"readme/{repo}")
    c = _read_cache(f)
    if c and time.time() - c.get("fetched", 0) < ttl:
        return c["data"]
    for name in ("README.md", "readme.md", "Readme.md"):
        req = urllib.request.Request(f"https://raw.githubusercontent.com/{repo}/HEAD/{name}",
                                     headers={"User-Agent": UA})
        try:
            with urlopen(req, timeout=20) as r:
                txt = r.read().decode("utf-8", "replace")
            f.write_text(json.dumps({"fetched": time.time(), "data": txt}, ensure_ascii=False), "utf-8")
            return txt
        except (urllib.error.URLError, OSError):
            continue
    if c:
        return c["data"]
    raise Offline(_("README not reachable"))


def thumb_file(url: str) -> Path:
    d = cache_dir() / "thumbs"
    d.mkdir(parents=True, exist_ok=True)
    return d / (hashlib.sha1(url.encode()).hexdigest() + ".img")


def fetch_thumb(url: str, max_bytes: int = 6 << 20) -> Path:
    f = thumb_file(url)
    if f.exists() and f.stat().st_size:
        return f
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urlopen(req, timeout=20) as r:
        data = r.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise Offline(_("Image too large"))
    f.write_bytes(data)
    return f
