# SPDX-License-Identifier: GPL-3.0-only
"""The firmwares of the Felucca family by what they answer: a table from the catalog audit (docs/firmware-support.md).

A firmware that answers the editor's INFO (7D 46 4C, command 1) names itself there – or not: Felucca's own releases
answer "FELUCCA v1.8.1", and so do most of its forks (their tools/build.py --release writes the same "v" + X.Y), SLOOP's
forks built with --release answer "FELUCCA 0.9 BETA". What is left once "FELUCCA", the version and words like BETA or
WEB are taken away (``info_name``) is the firmware's own name ("FELUCCA CLIPPER 1.3.1" -> "clipper", "SAADET v1.0" ->
"saadet"), or nothing: then the firmware is handled by its parent's rules, and the backup protocol is asked of the
device itself (backup.protocol: a read-only LIST, Felucca's 65 first, then SLOOP's 34).

Each entry says, for a firmware the audit found:
* ``backup``: the backup protocol – "felucca" (commands 65–67, "felucca-backup" files), "sloop" (34–36, "sloop-backup"),
  "x0x" (X0X's image, 0x41–0x43), "own" (a protocol of its own the app does not speak) or "none" (no backup over USB)
* ``layout``: where its backup objects live. "" = its parent's, by its inventory (Felucca's 0..9 and 32..34, SLOOP's
  0..11 and 32..35): such a fork's backups go onto its parent and back. A layout of its own: its objects mean
  something else at the same IDs, a backup of it only goes back onto it (and ``reads``: the layouts it takes as well)
* ``samples``: the backup objects that are user sample slots (restored through SMP_BEGIN/WRITE/END, an empty one
  erases the slot); None = the parent's (Felucca 32..63, SLOOP 32..35)
* ``restore``: SLOOP protocol: the order its own editor writes the objects back in (BK.RESTORE)
* ``twin``: the studio twin that opens it ("" = none; None = the usual rules)
Forks that keep their parent's protocol and inventory need no entry; an entry only names them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# words in an INFO string that are no name: build kinds and pre-release marks
VERSION_WORDS = {"beta", "alpha", "rc", "dev", "test", "web", "public", "release", "preview", "wip", "stable", "final",
                 "landing", "build", "fw", "firmware"}


def info_name(version: str) -> str:
    """the firmware's own name in an INFO string, lower case, without "FELUCCA", the version and build words
    ("FELUCCA v1.8.1" -> "", "FELUCCA SLOOP 2.8.5" -> "sloop", "FELUCCA WEB GB2.4" -> "gb", "X0X 1.2.1 BETA" -> "x0x")"""
    toks = [t for t in re.split(r"[\s_/]+", (version or "").strip().lower()) if t]
    if toks and toks[0] == "felucca":
        toks = toks[1:]
    out = []
    for t in toks:
        if re.match(r"v?\d", t):                 # a version ("v1.8.1", "0.9", "1bd443")
            continue
        t = re.sub(r"(?<=[a-z])v?\d+(?:\.\d+)+[0-9a-z.\-]*$", "", t)     # "gb2.4" -> "gb" (a version glued on)
        t = t.strip("-.:,()[]")
        if not t or re.match(r"v?\d", t) or t in VERSION_WORDS or t.startswith("{"):
            continue
        out.append(t)
    return " ".join(out)


@dataclass(frozen=True)
class Family:
    key: str                         # the catalog's key
    names: tuple = ()                # info_name() values it answers
    backup: str = "felucca"          # felucca | sloop | x0x | own | none
    layout: str = ""                 # "" = its parent's (by its inventory)
    reads: tuple = ()                # other layouts it takes backups of
    samples: tuple | None = None     # its sample slot objects; None = the parent's
    restore: tuple = ()              # SLOOP protocol: its editor's restore order
    twin: str | None = None          # the studio twin that opens it ("" none, None the usual rules)
    device: str = ""                 # the device layer's name for it (protocol.Info.firmware_name), "" = key
    evidence: str = ""               # where the audit found it

    @property
    def name(self) -> str:
        return self.device or self.key


SLOOP_ORDER = (6, 7, 8, 9, 10, 11, 2, 3, 4, 5, 0, 1)

FAMILIES: tuple[Family, ...] = (
    # ---- the parents and the firmwares the app knew before the audit (named here so the table is whole)
    Family("felucca", (), "felucca", evidence="firmware/src/editor_backup.c ED_BK_IDS"),
    Family("melodee", ("melodee",), "felucca", evidence="firmware/src/editor_backup.c"),
    Family("skiff", (), "felucca", evidence="INFO as Felucca's, engine DRUM-X; ED_BK_IDS 0..9, 16..34"),
    Family("jiant-fm", ("jiant",), "felucca", device="jiant", evidence="INFO \"JIANT V10.60\""),
    Family("coralflower", ("coralflower",), "felucca", evidence="cf_backup.c CRB_IDS"),
    Family("choralroot-fm-1", ("choralroot",), "felucca", device="choralroot", evidence="cr_backup.c CRB_IDS"),
    Family("sloop", ("sloop",), "sloop", "sloop", evidence="firmware/src/editor.c ED_BK_*"),
    Family("karma", ("karma",), "sloop", "karma", reads=("sloop",), evidence="SLOOP fork, project format 7"),
    Family("orbit", ("orbit",), "sloop", "orbit", evidence="ORP8 projects; its editor refuses foreign backups"),
    Family("x0x", ("x0x",), "x0x", evidence="firmware/src/backup.c (from 1.0.9)"),
    Family("hortator", ("hortator",), "own", evidence="its own protocol 7D 48 54"),
    # ---- SLOOP's forks (commands 34–36, "sloop-backup" files)
    Family("sloopdx", ("sloopdx",), "sloop", "sloopdx", samples=(), restore=(6, 7, 8, 9, 2, 3, 4, 5, 0, 1, 10, 11),
           twin="", evidence="INFO \"FELUCCA sloopDX 4.0.1\"; ED_BK_IDS 0..11, no samples; 10/11 its own objects"),
    Family("clipper", ("clipper",), "sloop", "clipper", reads=("sloop",),
           restore=(6, 7, 12, 13, 8, 9, 2, 3, 4, 5, 0, 14, 1), twin="",
           evidence="INFO \"FELUCCA CLIPPER 1.3.1\"; ED_BK_IDS + 12..14 (CLIPPER_SYNTH)"),
    Family("synthesthetic", ("synthesthetic",), "sloop", "synthesthetic", reads=("sloop",),
           restore=(6, 7, 8, 9, 10, 11, 12, 2, 3, 4, 5, 0, 1), twin="",
           evidence="INFO \"FELUCCA SYNTHESTHETIC 0.2\"; ED_BK_IDS + 12"),
    Family("jangada", ("jangada",), "sloop", evidence="INFO \"JANGADA 1.0.1\"; SLOOP's ED_BK_IDS without 35"),
    Family("winampfm", ("winampfm",), "sloop", evidence="INFO \"FELUCCA winampFM 0.9\"; SLOOP 2.8's inventory"),
    Family("no-man-sky-fm", ("nms fm",), "sloop", evidence="INFO \"FELUCCA NMS FM 0.9\"; SLOOP 2.8's inventory"),
    Family("fm1-move", ("fm1 move",), "sloop", evidence="INFO \"FELUCCA FM1 MOVE 0.3.1\"; SLOOP 2.6's inventory"),
    Family("selva1", ("selva1",), "sloop", evidence="INFO \"FELUCCA SELVA1 0.1\""),
    Family("paleotone", ("paleotone",), "sloop", evidence="INFO \"FELUCCA PALEOTONE 2.8.4\""),
    Family("notesorcery", ("notesorcery",), "sloop", evidence="INFO \"FELUCCA NOTESORCERY …\""),
    Family("launchparty", ("launchparty",), "sloop", evidence="INFO \"FELUCCA LAUNCHPARTY 0.3.2 PUBLIC\""),
    Family("spiralfm1", ("spiralfm1",), "sloop", evidence="INFO \"SPIRALFM1 1.0\""),
    Family("optimist", ("optimist",), "own",
           evidence="INFO \"FELUCCA OPTIMIST 0.3\"; its own backup commands 43–48 (io/editor/ed_backup.c)"),
    # ---- Felucca's forks (commands 65–67, "felucca-backup" files)
    Family("ghoulbox", ("gb",), "felucca", evidence="INFO \"FELUCCA WEB GB2.4\"; Felucca's 13 objects"),
    Family("caravel", ("caravel",), "felucca", evidence="INFO \"CARAVEL …\"; Felucca's 13 objects"),
    Family("araminta", ("araminta",), "felucca", evidence="INFO \"ARAMINTA …\"; Felucca's 13 objects"),
    Family("pigeon", ("pigeon",), "felucca", evidence="INFO \"PIGEON v0.5.0-beta.1\"; Felucca's 13 objects"),
    Family("dirge", ("dirge",), "felucca", evidence="INFO \"DIRGE …\"; Felucca's 13 objects"),
    Family("switch-blade", ("switch blade",), "felucca", evidence="INFO \"Switch Blade …\"; ED_BK_IDS 0..7"),
    Family("bongo-cat-s-tiny-studio", ("bongo cat",), "felucca", evidence="INFO \"BONGO CAT 0.4.0-rc1\""),
    Family("bunker-evolved", ("be",), "felucca", evidence="INFO \"FELUCCA BE 1.0-RC2\""),
    Family("felucca-8-patterns", ("argo",), "felucca", evidence="INFO \"ARGO …\"; Felucca's 13 objects"),
    Family("purplemonkey-fm-1", ("purplemonkey",), "felucca", evidence="INFO \"FELUCCA PurpleMonkey 0.1-dev\""),
    Family("tessera", ("tessera",), "felucca", "tessera", twin="",
           evidence="INFO \"TESSERA v2.0\"; ED_BK_IDS 0..29, TSP1 projects (Melodee's places, other contents)"),
    Family("saadet", ("saadet",), "felucca", "saadet", samples=(), twin="",
           evidence="INFO \"SAADET v…\"; ED_BK_IDS 1, 6, 7, 9..28, 32..40: 32 its state, 33..38 performances, "
                    "39 40 user waves – no sample slots"),
    Family("duell", ("duell",), "felucca", "duell", samples=(32, 33, 34), twin="",
           evidence="INFO \"DUELL 0.2.1 beta\"; ED_BK_IDS 0..5, 8, 13..26 (patterns, wavetables), 32..34"),
    Family("red-mass-vampire-os", ("red mass vampire os",), "felucca", "red-mass-vampire-os", twin="",
           evidence="INFO \"RED MASS VAMPIRE OS …\"; ED_BK_IDS 0..14, 32..34"),
    # ---- the editor's INFO, but no backup over USB
    Family("carnac-os", ("carnac os",), "none", evidence="INFO \"CARNAC OS …\", no backup commands"),
    Family("dreadnought", ("dreadnought",), "none", evidence="INFO \"DREADNOUGHT …\", no backup commands"),
    Family("modular-one", ("m1",), "none", evidence="INFO \"FELUCCA M1 0.1\", no backup commands"),
)

_BY_KEY = {f.key: f for f in FAMILIES}
_BY_NAME = {n: f for f in FAMILIES for n in f.names}


def by_key(key: str) -> Family | None:
    k = (key or "").lower()
    return _BY_KEY.get(k) or next((f for f in FAMILIES if f.device and f.device == k), None)


def by_info(version: str) -> Family | None:
    """the family an INFO string names (its own name, or one starting with a known name: "FELUCCA GNOMAD V1 1bd443"
    -> "gnomad …"), None for a plain "FELUCCA v…" or an unknown name"""
    n = info_name(version)
    if not n:
        return None
    if n in _BY_NAME:
        return _BY_NAME[n]
    words = n.split()
    for i in range(len(words) - 1, 0, -1):
        f = _BY_NAME.get(" ".join(words[:i]))
        if f:
            return f
    return None


def sloop_keys() -> tuple[str, ...]:
    """the keys (device names) of every firmware with SLOOP's backup protocol"""
    return tuple(f.name for f in FAMILIES if f.backup == "sloop")


def layout(key_or_family) -> str:
    f = key_or_family if isinstance(key_or_family, Family) else by_key(key_or_family)
    return f.layout if f else ""
