# SPDX-License-Identifier: GPL-3.0-only
"""Flash-Modell des FM-1: Pakete zerlegen, Flash-Layouts, Installationsketten nachspielen, Dumps prüfen.

Herkunft: Übergabe „fm1_flash_safety“ (fm1_flashmodel.py, 31.10.2026, für diese App geschrieben) – hier
nachgeprüft, korrigiert und erweitert (Ressourcen, Bootkopf ab Werk, Dump-Prüfung in Alltagssprache).
Paketformat nach M-VAVE fm1_open sdk/Package.cs (Apache-2.0) und MvaveFM1Unbricker (MIT), nachimplementiert –
kein Code kopiert. Nur Standardbibliothek, liest nur Dateien und Bytes, spricht nie mit einem Gerät.

Vertrauensstufen (docs/flash-protocol.md, Abschnitt „V16 und Flash-Sicherheit“):
  [Paket]  aus Paketen/Quelltexten belegt      [Aufz.]  aus einer Aufzeichnung an echter Hardware
  [Emu]    im Emulator beobachtet              [Annahme] nicht geprüft
"""
from __future__ import annotations

import hashlib
import json
import re
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path

FLASH_SIZE = 0x100000
SECTOR = 0x1000
HEAD_END = 0x4000              # Bootkopf 0x0–0x3FFF (uboot, isd_config)
APP_START = 0x4000
V15_END = 0x93000              # Ende von flash.bin bei V15 und allen Community-Firmwares [Paket]
V16_END = 0xA9000              # Ende von flash.bin bei V16 (app.bin, cfg_tool.bin, DX7.bin) [Paket; m-flasher
                               # docs/development.md, felucca-bsp tools/fm1_rescue.py]
CHIP_KEY = 0x980F

# Nie löschen, auch nicht beim Aufräumen (Unbricker-Regeln + fm1_open Package.cs) [Paket]
NEVER_ERASE = [(0x00000, 0x04000, "Bootkopf"), (0xFF000, 0x100000, "key_mac / reservierter Rest")]

# Bootkopf ab Werk: V13 (FM-1_009), V14 und V15 haben byte-gleich denselben Kopf [Paket: FM-1-RE-Pakete V13/V14, V15].
# Melodees eigener Loader schreibt ihn nie [Paket: melodee loader/ldr_core.c]. Der Original-Loader hat ihn beim
# aufgezeichneten V15-Update nicht angefragt [Aufz.] – dort waren Kopf im Gerät und im Paket aber gleich. Ob er bei V16
# (anderer Kopf) den Kopf mitschreibt, ist OFFEN: Stufe 2 liest Kopf-Einträge (0x0, 0x4000) und plant danach
# („verify_and_plan_update“), und Melodees ota.c geht davon aus, dass der Original-Loader „the whole flash.bin including
# the head“ schreibt. Ein FM-1, das nie V16 hatte und nie im ROM-Bootmodus beschrieben wurde, trägt diesen Kopf.
FACTORY_HEAD_SHA256 = "d9f43191777656de01c7bed4f9e9ba2e34e94832c5d2ed02c3fc7ab7a6d9bd67"

# Loader-Staging der Felucca-Familie (Melodee rc.7 firmware/src/ota.c OTA_AREA, 5 × 4 KiB, Eintrag bei 0xE4F00) [Paket].
# Wo die Original-Firmware V15/V16 ihren Loader in Stufe 1 ablegt, ist offen (FM-1-RE: „VM/loader flash area“).
STAGING = (0xE0000, 0xE5000)

# Melodee ab 1.6: Ressourcenbereich (Schrift, Werkssounds, Funk-Programm) [Paket: melodee firmware/src/res.c]
RES_BASE, RES_END, RES_MAGIC = 0xCB000, 0xE0000, 0x3153524D      # „MRS1“


class PackageError(Exception):
    pass


class DumpError(Exception):
    pass


# ----------------------------------------------------------------------------- Bausteine
def crc16(data, c=0):
    for b in data:
        c ^= b << 8
        for _ in range(8):
            c = ((c << 1) ^ 0x1021) if c & 0x8000 else c << 1
        c &= 0xFFFF
    return c


def cipher(buf, state=0xFFFF):
    """JieLi-Stromchiffre, Polynom 0x1021, ihr eigenes Inverses"""
    out = bytearray(buf)
    for i in range(len(out)):
        out[i] ^= state & 0xFF
        state = ((state << 1) ^ (0x1021 if state & 0x8000 else 0)) & 0xFFFF
    return bytes(out)


def sha(d) -> str:
    return hashlib.sha256(d).hexdigest()


def _u16(b, o):
    return struct.unpack_from("<H", b, o)[0]


def _u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def hx(a: int) -> str:
    return f"0x{a:05X}"


def ranges_text(ranges) -> str:
    """[(start, länge)] -> „0x93000–0xA8FFF, …“"""
    return ", ".join(f"{hx(a)}–{hx(a + n - 1)}" for a, n in ranges) or "–"


def merge_sectors(addrs) -> list[tuple[int, int]]:
    """Sektoradressen -> [(start, länge)] zusammenhängender Bereiche"""
    out: list[list[int]] = []
    for a in sorted(addrs):
        if out and out[-1][0] + out[-1][1] == a:
            out[-1][1] += SECTOR
        else:
            out.append([a, SECTOR])
    return [(a, n) for a, n in out]


# ----------------------------------------------------------------------------- Paket
@dataclass
class Package:
    path: str
    file_sha256: str
    identity: str                 # „FM-1_015“, „FM-1_016“, „FM-1_91060“ … (aus den Marker-Bytes, nie aus dem Namen)
    entries: list
    flash: bytes                  # flash.bin: Flash-Abbild ab Adresse 0
    layout: dict = field(default_factory=dict)   # Bereich -> (start, ende) absolut, aus dem Verzeichnis des Pakets
    head_sha256: str = ""
    label: str = ""

    @property
    def write_end(self) -> int:
        return len(self.flash)

    @property
    def app_sha256(self) -> str:
        return sha(self.flash[APP_START:])

    @classmethod
    def load(cls, path):
        try:
            data = Path(path).read_bytes()
        except OSError as e:
            raise PackageError(f"{Path(path).name} lässt sich nicht lesen ({e.strerror}).")
        return cls.from_bytes(data, str(path))

    @classmethod
    def from_bytes(cls, data, path="<bytes>"):
        stride, marked, nochar = 0x30, 20, 0x7D
        if len(data) < stride * marked:
            raise PackageError("zu kurz für ein FM-1-Paket")
        img, chars = bytearray(), []
        for i in range(marked):
            blk = data[i * stride:(i + 1) * stride]
            img += blk[:0x2F]
            if blk[0x2F] != nochar:
                chars.append(chr((blk[0x2F] - i - 1) & 0xFF))
        img += data[stride * marked:]
        img = bytes(img)
        hdr = cipher(img[:0x40])
        if crc16(hdr[2:0x40]) != _u16(hdr, 0):
            raise PackageError("Paketkopf-Prüfsumme falsch")
        n = _u16(hdr, 8)
        if not 0 < n <= 11 or crc16(img[0x40:0x40 + n * 0x50]) != _u16(hdr, 2):
            raise PackageError("Dateiliste des Pakets beschädigt")
        entries, flash = [], None
        for i in range(n):
            e = cipher(img[0x40 + i * 0x50:0x40 + (i + 1) * 0x50])
            typ, off, ln = _u16(e, 0), _u32(e, 8), _u32(e, 12)
            name = e[64:80].split(b"\0")[0].decode("latin1")
            entries.append({"type": typ, "name": name, "offset": off, "length": ln})
            if typ == 0:
                flash = img[off:off + ln]
        if flash is None or len(flash) < APP_START + SECTOR:
            raise PackageError("kein flash.bin im Paket")
        pkg = cls(str(path), sha(data), "".join(chars), entries, bytes(flash))
        pkg.head_sha256 = sha(pkg.flash[:HEAD_END])
        pkg.layout = _layout(pkg.flash)
        return pkg


def _decrypt_area(flash, start, length):
    out = bytearray(flash[start:start + length])
    for p in range(0, len(out), 32):
        out[p:p + 32] = cipher(out[p:p + 32], CHIP_KEY ^ (p >> 2))
    return bytes(out)


def _records(buf, base, count):
    res = []
    for i in range(count):
        r = buf[base + 32 * i:base + 32 * (i + 1)]
        if len(r) < 32:
            break
        name = r[16:28].split(b"\0")[0]
        if not name or not all(32 <= c < 127 for c in name):
            continue
        res.append({"name": name.decode(), "addr": _u32(r, 4), "size": _u32(r, 8)})
    return res


def _layout(flash):
    """Bereiche aus dem (entschlüsselten) Verzeichnis des App-Bereichs [Paket]"""
    lay = {"app": (APP_START, len(flash))}
    try:
        area = _decrypt_area(flash, APP_START, min(0x400, len(flash) - APP_START))
        recs = _records(area, 0, 12)
        for r in recs:
            if r["name"] in ("VM", "BTIF", "USR", "SEQ64") and r["addr"] != 0xFFFFFFFF:
                lay[r["name"]] = (r["addr"], r["addr"] + r["size"])
        head = next((r for r in recs if r["name"] == "app_area_hea"), None)
        if head and head["size"] < len(flash):   # USER-Verzeichnis direkt hinter den Nutzdaten (V16: 0xED000)
            whole = _decrypt_area(flash, APP_START, min(head["size"] + 0x80, len(flash) - APP_START))
            for r in _records(whole, head["size"], 2):
                if r["name"] == "USER":
                    lay["USER"] = (r["addr"], r["addr"] + r["size"])
    except (struct.error, IndexError):
        pass
    return lay


def classify_family(pkg) -> str:
    """„v15-layout“ (Original-V15 und alle Felucca-Community-Firmwares) oder „v16-layout“ [Paket]"""
    lay = getattr(pkg, "layout", {}) or {}
    if "SEQ64" in lay or pkg.write_end > V15_END:
        return "v16-layout"
    return "v15-layout"


# ----------------------------------------------------------------------------- bekannte Pakete (ohne Paketdateien)
DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "known_packages.json"


@dataclass
class KnownRef:
    """ein bekanntes Paket als Steckbrief: Prüfsummen und Layout, ohne die Paketdatei selbst"""
    label: str
    name: str
    identity: str
    file_sha256: str
    flash_bytes: int
    head_sha256: str
    app_sha256: str
    family: str
    layout: dict = field(default_factory=dict)
    app_prefix: dict = field(default_factory=dict)     # {ende: sha256(flash[0x4000:ende])} – für „abgeschnitten“
    sectors: dict = field(default_factory=dict)        # {adresse: sha256 des Sektors} – für Reste-Erkennung
    blocks: dict = field(default_factory=dict)         # {adresse: sha256[:16] je 512-Byte-Block} – halbe Sektoren
    resources: dict = field(default_factory=dict)      # Melodee: {"length": n, "crc": n, "sha256": …}
    version: str = ""
    note: str = ""

    @property
    def write_end(self) -> int:
        return self.flash_bytes


def load_known(path: Path | str | None = None) -> list[KnownRef]:
    p = Path(path) if path else DATA_FILE
    raw = json.loads(p.read_text("utf-8"))
    out = []
    for e in raw.get("packages", []):
        out.append(KnownRef(
            label=e["label"], name=e["name"], identity=e["identity"], file_sha256=e["file_sha256"],
            flash_bytes=int(e["flash_bytes"]), head_sha256=e["head_sha256"], app_sha256=e["app_sha256"],
            family=e["family"], layout={k: (int(a, 16), int(b, 16)) for k, (a, b) in e.get("layout", {}).items()},
            app_prefix={int(k, 16): v for k, v in e.get("app_prefix", {}).items()},
            sectors={int(k, 16): v for k, v in e.get("sectors", {}).items()},
            blocks={int(k, 16): v for k, v in e.get("blocks", {}).items()},
            resources=dict(e.get("resources", {})), version=e.get("version", ""), note=e.get("note", "")))
    return out


def known_heads(path=None) -> dict[str, str]:
    p = Path(path) if path else DATA_FILE
    return dict(json.loads(p.read_text("utf-8")).get("heads", {}))


def known_hashes(path=None) -> set[str]:
    return {k.file_sha256 for k in load_known(path)}


def ref_for(identity: str = "", file_sha256: str = "", refs=None) -> KnownRef | None:
    refs = load_known() if refs is None else refs
    if file_sha256:
        r = next((k for k in refs if k.file_sha256 == file_sha256), None)
        if r:
            return r
    cands = [k for k in refs if identity and k.identity == identity]
    return cands[-1] if cands else None


def ref_from_package(pkg: Package, label: str = "", name: str = "") -> KnownRef:
    return KnownRef(label=label or pkg.identity, name=name or pkg.identity, identity=pkg.identity,
                    file_sha256=pkg.file_sha256, flash_bytes=pkg.write_end, head_sha256=pkg.head_sha256,
                    app_sha256=pkg.app_sha256, family=classify_family(pkg), layout=dict(pkg.layout))


# ----------------------------------------------------------------------------- Flash-Modell
class Flash:
    """1 MiB NOR-Flash mit Herkunft je 4-KiB-Sektor. Schreiben löscht vorher die berührten Sektoren."""

    def __init__(self, data=None):
        self.data = bytearray(b"\xff" * FLASH_SIZE)
        self.prov = ["blank"] * (FLASH_SIZE // SECTOR)
        if data is not None:
            self.data[:len(data)] = data

    def copy(self):
        f = Flash()
        f.data[:] = self.data
        f.prov = list(self.prov)
        return f

    def write(self, addr, payload, tag):
        end = addr + len(payload)
        assert end <= FLASH_SIZE
        for s in range(addr // SECTOR, (end - 1) // SECTOR + 1):
            self.data[s * SECTOR:(s + 1) * SECTOR] = b"\xff" * SECTOR
            self.prov[s] = tag
        self.data[addr:end] = payload

    def erase(self, addr, length):
        assert addr % SECTOR == 0 and length % SECTOR == 0
        for s in range(addr // SECTOR, (addr + length) // SECTOR):
            self.data[s * SECTOR:(s + 1) * SECTOR] = b"\xff" * SECTOR
            self.prov[s] = "erased"

    def install(self, pkg, mode="full", stop_at=None, tag=None):
        """Schreiber im ROM-Bootmodus: mode „full“ = flash.bin ab 0, „from4000“ = ab 0x4000 (Unbricker).
        stop_at: Adresse, an der das Schreiben endet (Abbruch oder ein Werkzeug, das nur bis 0x93000 schreibt)."""
        lo = 0 if mode == "full" else APP_START
        hi = len(pkg.flash) if stop_at is None else min(stop_at, len(pkg.flash))
        if hi > lo:
            self.write(lo, pkg.flash[lo:hi], tag or pkg.identity)


# Was eine Firmware beim ersten Start selbst löscht/schreibt – im Emulator beobachtet (fm1-emu, 2,5 s) [Emu].
# Gleiches Verhalten auf leerem und auf fremd beschriebenem Flash; Flash mit gültigen Daten derselben Firmware wurde
# nicht getestet. Melodee 1.6.0-rc.7 lag dem Emulator nicht vor (nur 1.5.1).
RUNTIME = {
    "FM-1_016": {"erase": [(0xBA000, 0xCA000), (0xDA000, 0xE3000), (0xED000, 0xF0000)],
                 "program": [(0xBA000, 0xBB000), (0xDA000, 0xE3000), (0xEC000, 0xF0000)]},
    "FM-1_015": {"erase": [(0x93000, 0x97000), (0xF2000, 0xF4000), (0xFB000, 0xFC000)],
                 "program": [(0x93000, 0x94000), (0xE9000, 0xEA000), (0xF2000, 0xF4000), (0xFB000, 0xFC000)]},
    "FM-1_91051": {"erase": [(0xFC000, 0xFD000)], "program": [(0xFC000, 0xFD000)]},
}


def boot(flash, pkg):
    """beobachtete Erststart-Formatierung von `pkg` anwenden (ohne Eintrag: nichts)"""
    rt = RUNTIME.get(pkg.identity)
    if not rt:
        return
    for lo, hi in rt["erase"]:
        flash.erase(lo, hi - lo)
    for lo, hi in rt["program"]:
        fill = hashlib.sha256(b"boot-" + pkg.identity.encode()).digest()
        flash.write(lo, (fill * ((hi - lo) // 32 + 1))[:hi - lo], "boot:" + pkg.identity)


def install_ota(flash, pkg, progress=1.0, staging=False):
    """normaler Update-Weg (Gerät fragt, Rechner liefert) wie an echter Hardware aufgezeichnet [Aufz.: AL-255/FM-1-RE,
    04.09.2026, V15-Paket]: Stufe 2 fragt genau flash.bin[0x4000:Ende) in 512-Byte-Blöcken absteigend an; der Bootkopf
    wird nur gelesen (er war im Gerät und im Paket gleich – ob ein anderer Kopf wie bei V16 geschrieben würde, ist
    offen; das Modell schreibt ihn nicht), alles hinter flash.bin nie angefragt. Der Loader ota.bin ist in V13–V16 byte-gleich [Paket], er
    nimmt Anfang/Ende aus dem Paket – für V16 also 0x4000–0xA8FFF [abgeleitet].
    [Annahme] Der Loader löscht den ganzen Zielbereich zuerst (Listing: prepare_flash_regions vor
    write_update_regions; FM-1-RE führt die Löschwirkung als unbestätigt) und schreibt dann von oben nach unten.
    progress: Anteil der geschriebenen Blöcke (1.0 = komplett). staging: True = Loader-Staging der laufenden
    Felucca-Firmware bei 0xE0000–0xE4FFF mitmodellieren (Melodee-Quelle [Paket]; bei Original-Firmware offen)."""
    if staging:
        lo, hi = staging if isinstance(staging, tuple) else STAGING
        flash.write(lo, hashlib.sha256(b"staging").digest() * ((hi - lo) // 32), "staging")
    end = len(pkg.flash)
    flash.erase(APP_START, end - APP_START)
    blocks = (end - APP_START) // 512
    start = end - int(blocks * progress) * 512
    if start < end:
        flash.data[start:end] = pkg.flash[start:end]
        for s in range(start // SECTOR, (end - 1) // SECTOR + 1):
            flash.prov[s] = pkg.identity if s * SECTOR >= start else "partial:" + pkg.identity


def installed_state(pkg, user_data=True, head: bytes | None = None):
    """ein benutztes FM-1, auf dem `pkg` läuft: Pseudo-Nutzerdaten in den Datenbereichen.
    head: Bootkopf des Geräts (Standard: der des Pakets). Echte Geräte ohne V16-Vergangenheit tragen den Werks-Kopf
    (FACTORY_HEAD_SHA256), weil Melodees Loader ihn nie schreibt und der Original-Loader ihn bei gleichem Kopf nicht
    anfragt – für Community-Firmware also V15s Kopf."""
    f = Flash()
    f.write(0, pkg.flash, pkg.identity)
    if head is not None:
        f.data[:HEAD_END] = head[:HEAD_END]
        for s in range(HEAD_END // SECTOR):
            f.prov[s] = "head"
    if user_data:
        lay = pkg.layout
        regions = []
        if "VM" in lay:
            regions.append(lay["VM"])
        if "USR" in lay:
            regions.append(lay["USR"])
        regions.append((0xFC000, 0xFF000))       # Globals der Felucca-Familie
        for lo, hi in regions:
            for s in range(lo // SECTOR, hi // SECTOR):
                blk = b"".join(hashlib.sha256(b"user%d-%d" % (s, i)).digest() for i in range(SECTOR // 32))
                f.data[s * SECTOR:(s + 1) * SECTOR] = blk
                f.prov[s] = "user"
    return f


# ----------------------------------------------------------------------------- Auswertung
def data_regions(pkg):
    """Bereiche, die die Firmware als eigene Daten (nicht Code) benutzt"""
    lay, out = pkg.layout, []
    for k in ("VM", "SEQ64", "BTIF", "USR", "USER"):
        if k in lay:
            out.append((k, *lay[k]))
    if "USER" not in lay:
        out.append(("globals", 0xFC000, 0xFF000))
    return out


BLOCK = 512                    # Anfragegröße des Update-Loaders: ein abgebrochener Update endet an einer Blockgrenze


def block_hash(b) -> str:
    return sha(b)[:16]


def _sector_blocks_match(blk, addr, blocks) -> bool:
    """halb geschriebener Sektor ohne Paketdatei: jeder 512-Byte-Block ist leer (0xFF) oder gleicht dem Block des
    Pakets (Steckbrief „blocks“), mindestens einer gleicht"""
    hit = False
    for o in range(0, SECTOR, BLOCK):
        b = blk[o:o + BLOCK]
        if b == b"\xff" * BLOCK:
            continue
        if blocks.get(addr + o) != block_hash(b):
            return False
        hit = True
    return hit


def _sector_matches(blk, kb):
    """True, wenn jedes Nicht-0xFF-Byte von blk dem Byte in kb gleicht (und blk nicht fast leer ist): genaue Kopien
    und halb geschriebene Sektoren eines abgebrochenen Updates"""
    nz = [i for i, b in enumerate(blk) if b != 0xFF]
    return len(nz) >= 64 and all(blk[i] == kb[i] for i in nz)


def foreign_residue(flash, final_pkg, known):
    """Sektoren in den Datenbereichen der Firmware, die Bytes eines anderen Pakets tragen (ganz oder teilweise)"""
    res = []
    data = flash.data if isinstance(flash, Flash) else flash
    for name, lo, hi in data_regions(final_pkg):
        for s in range(lo // SECTOR, hi // SECTOR):
            blk = bytes(data[s * SECTOR:(s + 1) * SECTOR])
            if blk == b"\xff" * SECTOR:
                continue
            for k in known:
                if k is final_pkg or getattr(k, "flash", None) is None:
                    continue
                kb = k.flash[s * SECTOR:(s + 1) * SECTOR]
                if len(kb) == SECTOR and _sector_matches(blk, kb):
                    res.append({"sector": s * SECTOR, "region": name, "foreign": k.identity, "partial": blk != kb})
                    break
    return res


def head_owner(flash, known):
    h = sha(bytes(flash.data[:HEAD_END]))
    if h == FACTORY_HEAD_SHA256:
        return "Werk (V13–V15)"
    for k in known:
        if k.head_sha256 == h:
            return k.identity
    return "unbekannt"


def replay(start, steps, final_pkg, known):
    """Schritte: („ota“, pkg, progress[, staging]) | („boot“, pkg) | (pkg, „full“|„from4000“, stop_at)"""
    f = start.copy()
    log = []
    for step in steps:
        if step[0] == "ota":
            install_ota(f, step[1], step[2] if len(step) > 2 else 1.0, step[3] if len(step) > 3 else False)
            log.append({"ota": step[1].identity, "progress": step[2] if len(step) > 2 else 1.0})
            continue
        if step[0] == "boot":
            boot(f, step[1])
            log.append({"boot": step[1].identity})
            continue
        pkg, mode, stop = step
        f.install(pkg, mode, stop)
        log.append({"install": pkg.identity, "mode": mode,
                    "wrote": [hex(0 if mode == "full" else APP_START),
                              hex(len(pkg.flash) if stop is None else min(stop, len(pkg.flash)))]})
    n = min(len(final_pkg.flash), FLASH_SIZE)
    app_ok = f.data[APP_START:n] == final_pkg.flash[APP_START:n]
    lost = [s * SECTOR for s, p in enumerate(start.prov) if p == "user" and f.prov[s] != "user"]
    return {"steps": log, "head_owner": head_owner(f, known), "final_app_intact": app_ok,
            "user_sectors_destroyed": [hex(a) for a in lost],
            "foreign_residue": foreign_residue(f, final_pkg, known), "flash": f}


def cleanup_plan(residue):
    """Reste-Sektoren zu Löschbereichen zusammenfassen; wirft, wenn ein NEVER_ERASE-Bereich berührt würde"""
    safe = []
    for lo, n in merge_sectors(r["sector"] for r in residue):
        hi = lo + n
        for nlo, nhi, why in NEVER_ERASE:
            if lo < nhi and hi > nlo:
                raise AssertionError(f"Aufräumen würde den Bereich „{why}“ berühren")
        safe.append((lo, n))
    return safe


# ----------------------------------------------------------------------------- Vorprüfung
NAME_VERSION_RE = re.compile(r"(?:^|[^a-z0-9])v(?:ersion)?[_ -]?(\d{1,3})(?![0-9])", re.I)


def name_version(filename: str) -> int | None:
    """welche Original-Version der Dateiname behauptet („FM-1_v15.fwsc“ -> 15), sonst None"""
    m = NAME_VERSION_RE.search(Path(filename or "").name)
    return int(m.group(1)) if m else None


def preflight(target, current=None, filename=None, known_hashes=None, have_backup=False, mode="stock-ota"):
    """-> [(stufe, code, text)], stufe „block“ | „warn“ | „info“. Codes wie INTEGRATION.md §5.
    mode: „stock-ota“ (normaler Update-Weg), „from4000“ (Unbricker: schreibt nur 0x4000–0x92FFF), „full“."""
    out = []
    if filename:
        v = name_version(filename)
        if v is not None and target.identity.startswith("FM-1_0") and v != int(target.identity[-3:]):
            out.append(("block", "NAME_IDENTITY_MISMATCH",
                        f"Der Dateiname sagt V{v}, das Paket selbst ist {target.identity}."))
    if known_hashes is not None and target.file_sha256 not in known_hashes:
        out.append(("warn", "HASH_UNKNOWN", "Das Paket steht nicht in der Liste bekannter Pakete."))
    if not have_backup:
        out.append(("block", "NO_BACKUP", "Kein Backup des jetzigen Stands."))
    if current is not None:
        fa, fb = classify_family(current), classify_family(target)
        if fa != fb:
            out.append(("warn", "LAYOUT_CHANGE", f"Layoutwechsel {fa} → {fb}: schreibt bis {hx(target.write_end - 1)}, "
                                                 "die Datenbereiche liegen danach woanders."))
        if target.write_end > current.write_end:
            out.append(("warn", "OVERWRITES_DATA", f"Das Ziel schreibt {hx(current.write_end)}–"
                                                   f"{hx(target.write_end - 1)}, das die jetzige Firmware als "
                                                   "Datenspeicher nutzt."))
        if mode == "from4000" and current.head_sha256 != target.head_sha256:
            out.append(("info", "HEAD_NOT_WRITTEN", "Der Bootkopf bleibt der alte (nur im ROM-Bootmodus relevant)."))
    if mode == "from4000" and target.write_end > V15_END:
        out.append(("block", "TOOL_TRUNCATES", f"Dieser Schreibweg schreibt nur 0x04000–0x92FFF; das Paket braucht "
                                               f"bis {hx(target.write_end - 1)}."))
    return out


def loss_ranges(current, target) -> list[tuple[str, int, int]]:
    """was ein Wechsel current -> target überschreibt bzw. was die neue Firmware beim ersten Start formatiert
    -> [(art, start, ende)]  art „install“ [Paket + Aufz.] | „erststart“ [Emu]"""
    out = []
    if target.write_end > current.write_end:
        out.append(("install", current.write_end, target.write_end))
    rt = RUNTIME.get(target.identity)
    if rt and classify_family(current) != classify_family(target):
        for lo, hi in rt["erase"]:
            out.append(("erststart", lo, hi))
    return out


def verify_dump(dump, pkg, mode="stock-ota"):
    """Dump mit dem vergleichen, was geschrieben sein sollte. Der normale Update-Weg schreibt den Bootkopf
    (jedenfalls bei gleichem Kopf) nicht, darum zählt bei „stock-ota“ und „from4000“ erst ab 0x4000. -> (ok, erste abweichende Sektoradresse, Anzahl)"""
    lo = 0 if mode == "full" else APP_START
    exp = pkg.flash[lo:]
    got = dump[lo:lo + len(exp)]
    if got == exp:
        return True, None, 0
    bad = [a for a in range(0, len(exp), SECTOR) if got[a:a + SECTOR] != exp[a:a + SECTOR]]
    return False, lo + bad[0], len(bad)


# ----------------------------------------------------------------------------- Melodee-Ressourcen
def parse_resource_status(b) -> dict:
    """Antwort auf Editor-Befehl 82 mit Argument 0 (nur lesen; melodee tools/fm1_resources.py status)"""
    b = list(b or [])
    if len(b) != 12:
        raise ValueError("keine Ressourcen-Antwort")
    r35 = lambda at: sum(b[at + i] << 7 * i for i in range(5)) & 0xFFFFFFFF   # noqa: E731
    return {"rc": b[0], "installed": bool(b[1]), "length": r35(2), "crc": r35(7)}


def resource_header(dump: bytes) -> dict | None:
    """Ressourcen-Kopf im Dump (0xCB000) prüfen: Magic, Kopf-CRC, Nutzdaten-CRC [Paket: res.c]"""
    h = dump[RES_BASE:RES_BASE + 32]
    if len(h) < 32:
        return None
    magic, length, crc = struct.unpack_from("<3I", h)
    hcrc = _u32(h, 28)
    if magic != RES_MAGIC:
        return {"present": False}
    ok_head = zlib.crc32(h[:28]) & 0xFFFFFFFF == hcrc
    payload_ok = ok_head and 0 < length <= RES_END - RES_BASE - 32 and \
        zlib.crc32(dump[RES_BASE + 32:RES_BASE + 32 + length]) & 0xFFFFFFFF == crc
    return {"present": True, "length": length, "crc": crc, "head_ok": ok_head, "payload_ok": payload_ok}


# ----------------------------------------------------------------------------- Prüfberichte in Alltagssprache
GREEN, YELLOW, RED = "gruen", "gelb", "rot"
LEVEL_TEXT = {GREEN: "Sauber", YELLOW: "Mit Hinweisen", RED: "Nicht in Ordnung"}


@dataclass
class Report:
    level: str = GREEN
    title: str = ""
    findings: list = field(default_factory=list)      # [(stufe, satz)]
    details: list = field(default_factory=list)       # technische Zeilen (Adressen, Prüfsummen)
    not_covered: list = field(default_factory=list)   # was diese Prüfung NICHT abdeckt
    firmware: str = ""                                # erkannte Firmware (Klartext)
    residue: list = field(default_factory=list)       # [(start, länge)]
    compared: list = field(default_factory=list)      # womit verglichen wurde

    def add(self, level: str, text: str):
        self.findings.append((level, text))
        order = {GREEN: 0, YELLOW: 1, RED: 2}
        if order[level] > order[self.level]:
            self.level = level


def quick_check(identity: str, version: str, res_status: dict | None, refs=None, res_error: str = "") -> Report:
    """„Ist mein FM-1 sauber?“ ohne Dump: nur Kennung, Version und (bei Melodee) Ressourcen-Status (Befehl 82)."""
    refs = load_known() if refs is None else refs
    r = Report(title="Schnellprüfung (nur lesen)")
    ref = next((k for k in refs if k.identity == identity and k.version and k.version in (version or "")), None) \
        or ref_for(identity, refs=refs)
    fw = version or identity or "unbekannt"
    r.firmware = fw
    if not identity:
        r.add(YELLOW, "Das FM-1 hat seine Kennung nicht verraten – die App kann die Firmware nicht zuordnen.")
    elif ref is not None:
        r.add(GREEN, f"Das FM-1 meldet {identity} ({fw}) – eine Firmware, die die App kennt.")
    else:
        r.add(YELLOW, f"Das FM-1 meldet {identity} ({fw}). Diese Kennung steht nicht in der Liste bekannter Pakete – "
                      "das heißt nicht, dass etwas kaputt ist, die App kann sie nur nicht gegenprüfen.")
    if res_status is not None:
        want = ref.resources if ref is not None else {}
        if res_status.get("installed"):
            if want and (res_status.get("length"), res_status.get("crc")) != (want.get("length"), want.get("crc")):
                # Befehl 82 meldet Länge/CRC, die DIESER Build erwartet (res_want), nicht die gespeicherten Werte:
                # „installiert“ heißt also immer „passt zur laufenden Firmware“ – abweichend ist die Firmware selbst
                r.add(YELLOW, "Die Ressourcen (Werkssounds, Schrift, Bluetooth-Programm) sind vollständig und passen "
                              "zur laufenden Firmware. Die Firmware selbst ist aber ein anderer Build als der, den die "
                              "App unter dieser Version kennt – die App kann sie darum nicht gegenprüfen.")
            else:
                r.add(GREEN, "Ressourcen (Werkssounds, Schrift, Bluetooth-Programm) sind vollständig: Die Firmware hat "
                             "Kopf und Prüfsumme selbst nachgerechnet, sie passen zu genau diesem Build.")
        else:
            r.add(YELLOW, "Die Ressourcen fehlen oder sind beschädigt – dann fehlen Werkssounds oder die Schrift sieht "
                          "anders aus. Abhilfe: Seite „Firmware“ › „Schrift & Werkssounds nachschreiben“.")
        r.details.append(f"Befehl 82: installiert={'ja' if res_status.get('installed') else 'nein'}, Länge "
                         f"{res_status.get('length')}, CRC-32 {res_status.get('crc', 0):08x}"
                         + (f" (bekannt: {want.get('crc', 0):08x})" if want else ""))
    elif res_error:
        r.details.append(f"Ressourcen-Status nicht abfragbar: {res_error}")
    r.not_covered = [
        "Ohne Dump sieht die App nicht in den Flash: Reste früherer Firmwares (z. B. von V16), der Bootkopf und die "
        "Datenbereiche bleiben ungeprüft.",
        "Ob jedes Byte der Firmware stimmt, prüft das FM-1 beim Start nur teilweise selbst – byte-genau geht es nur "
        "mit einem Flash-Dump (Teil 2 dieser Prüfung).",
    ]
    if res_status is None and not res_error:
        r.not_covered.append("Die Ressourcen-Prüfung gibt es nur bei Melodee ab 1.6.")
    return r


def _app_match(dump: bytes, refs) -> KnownRef | None:
    for k in sorted(refs, key=lambda k: -k.flash_bytes):
        if k.flash_bytes <= FLASH_SIZE and sha(dump[APP_START:k.flash_bytes]) == k.app_sha256:
            return k
    return None


# Werks-Abbilder: App-Bereich 0x4000–0x92FFF von Geräten ab Werk, die FM-1_015 melden, aber NICHT dem veröffentlichten
# V15-Paket (6edf3c37…) gleichen. Quelle: maxfridbe/mvave-fm1 skills/mvave-fm1-firmware/SKILL.md §10 (ein Gerät,
# gemessen 22.10.2026); MvaveFM1Unbricker repair-settings: „factory units carry a firmware image that differs from the
# public package“. Kein Paket dazu bekannt – darum nur als Prüfsumme des Bereichs.
FACTORY_APPS = {
    "8c1d13a5934eb3c70a5fec644577618a31b2a96c8748a7b692c506a86505dbb1":
        "Werks-Abbild der Original-Firmware V15 (FM-1_015, an einem Gerät gemessen)",
}

_CRC_TAB = []
for _i in range(256):
    _c = _i << 8
    for _ in range(8):
        _c = ((_c << 1) ^ 0x1021) if _c & 0x8000 else _c << 1
    _CRC_TAB.append(_c & 0xFFFF)


def _crc16_fast(data, c=0):
    """wie crc16 (CCITT, MSB zuerst), mit Tabelle – für ganze Firmware-Dateien"""
    tab = _CRC_TAB
    for b in data:
        c = ((c << 8) & 0xFFFF) ^ tab[(c >> 8) ^ b]
    return c


def app_directory(flash: bytes) -> dict:
    """Dateitabelle des App-Bereichs ab 0x4000 prüfen – die Struktur, die der Bootlader selbst liest [Paket].
    Jeder 32-Byte-Eintrag: CRC-16 des Eintrags (Bytes 2–31), CRC-16 der Datei, Adresse, Länge, Attribut, Name.
    Erster Eintrag „app_area_head“ (Länge = Ende der Dateien relativ zu 0x4000), danach die Dateien (Attribut 0x82:
    app.bin, cfg_tool.bin, bei V16 auch DX7.bin – ihre Daten-CRC gilt für den entschlüsselten Bereich) und die
    Flash-Bereiche VM, PRCT (geschützter Bereich ab 0 = Ende der Firmware), BTIF, USR, SEQ64.
    Geprüft an V13, V14, V15, SLOOP, Karma, BBL und den hd-fm1-Testpaketen: alle Kopf- und Daten-CRCs stimmen.
    -> {"found", "ok", "errors", "files", "prct_end", "vm", "seq64", "files_end"}. Wirft nie."""
    out = {"found": False, "ok": False, "errors": [], "files": [], "prct_end": None, "vm": None, "seq64": False,
           "files_end": None}
    try:
        if len(flash) < APP_START + 0x200:
            out["errors"].append("zu kurz")
            return out
        top = _decrypt_area(flash, APP_START, 0x200)
        ents = []
        for i in range(16):
            r = top[32 * i:32 * (i + 1)]
            if r == b"\xff" * 32:
                break
            if crc16(r[2:32]) != _u16(r, 0):
                if i == 0:
                    out["errors"].append("kein gültiger Eintrag „app_area_head“ bei 0x04000")
                    return out
                break                                   # Ende der Tabelle (danach steht Code)
            ents.append({"name": r[16:32].split(b"\0")[0].decode("latin1"), "dcrc": _u16(r, 2),
                         "addr": _u32(r, 4), "size": _u32(r, 8), "attr": r[12]})
        if not ents or not ents[0]["name"].startswith("app_area_hea"):
            out["errors"].append("kein Eintrag „app_area_head“ bei 0x04000")
            return out
        out["found"] = True
        area = ents[0]["size"]
        if not 0x200 < area <= len(flash) - APP_START:
            out["errors"].append(f"app_area_head nennt eine unmögliche Länge 0x{area:X}")
            return out
        out["files_end"] = APP_START + area
        whole = None
        for e in ents[1:]:
            n = e["name"]
            if e["attr"] == 0x82:
                if e["addr"] + e["size"] > area:
                    out["errors"].append(f"{n} liegt außerhalb des App-Bereichs")
                    continue
                if whole is None:
                    whole = _decrypt_area(flash, APP_START, area)
                ok = _crc16_fast(whole[e["addr"]:e["addr"] + e["size"]]) == e["dcrc"]
                out["files"].append({"name": n, "start": APP_START + e["addr"], "size": e["size"], "crc_ok": ok})
                if not ok:
                    out["errors"].append(f"Prüfsumme (CRC) von {n} stimmt nicht")
            elif n == "PRCT" and e["addr"] == 0:
                out["prct_end"] = e["size"]
            elif n == "VM":
                out["vm"] = (e["addr"], e["addr"] + e["size"])
            elif n == "SEQ64":
                out["seq64"] = True
        if not any(f["name"] == "app.bin" for f in out["files"]):
            out["errors"].append("keine Datei app.bin in der Tabelle")
        if out["prct_end"] is None:
            out["errors"].append("kein Eintrag PRCT (Ende der Firmware)")
        out["ok"] = not out["errors"]
    except (struct.error, IndexError, UnicodeDecodeError) as e:
        out["errors"].append(f"Tabelle nicht lesbar ({e})")
        out["ok"] = False
    return out


def directory_layout(d: dict) -> str:
    """„v15-layout“ nur, wenn die geprüfte Tabelle es selbst sagt: Firmware (PRCT) endet genau bei 0x93000, die
    Einstellungen (VM) beginnen dort, kein SEQ64 und alle Dateien davor. „v16-layout“, wenn die Firmware über 0x93000
    hinausreicht (bis 0xA9000 oder weiter) oder SEQ64 hat. Sonst (Tabelle fehlt/beschädigt, V13/V14 mit 0x94000 …) „“ = unbekannt."""
    if not d.get("ok"):
        return ""
    if d["seq64"] or (d["prct_end"] or 0) >= V16_END:
        return "v16-layout"
    if d["prct_end"] == V15_END and d["vm"] and d["vm"][0] == V15_END and (d["files_end"] or 0) <= V15_END:
        return "v15-layout"
    return ""


def check_dump(dump: bytes, refs=None, packages=(), expected: str = "", factory_heads=None) -> Report:
    """1-MiB-Dump gegen bekannte Pakete prüfen: Bootkopf, App-Bereich, abgeschnittenes V16, Reste fremder Firmware,
    Melodee-Ressourcen. refs: Steckbriefe (known_packages.json), packages: vorliegende Pakete (für halbe Sektoren),
    expected: Kennung, die laufen sollte (z. B. „FM-1_91060“). factory_heads: Bootköpfe ab Werk (Demo: eigener)."""
    if len(dump) != FLASH_SIZE:
        raise DumpError(f"Die Datei ist {len(dump):,} Bytes groß – ein vollständiger FM-1-Dump hat genau 1 MiB "
                        f"({FLASH_SIZE:,} Bytes).".replace(",", "."))
    refs = list(load_known() if refs is None else refs)
    for p in packages:
        if not any(k.file_sha256 == p.file_sha256 for k in refs):
            refs.append(ref_from_package(p, label=p.label or p.identity))
    r = Report(title="Flash-Dump-Prüfung")
    r.compared = sorted({k.name for k in refs})
    if dump.count(0xFF) == FLASH_SIZE or not any(dump):
        r.add(RED, "Der Dump ist komplett leer – so sieht kein laufendes FM-1 aus. Wahrscheinlich ist beim Lesen etwas "
                   "schiefgegangen.")
        return r
    # Bootkopf
    heads = known_heads()
    hs = sha(dump[:HEAD_END])
    head_name = heads.get(hs) or next((f"Bootkopf aus {k.name}" for k in refs if k.head_sha256 == hs), "")
    r.details.append(f"Bootkopf 0x00000–0x03FFF: SHA-256 {hs[:16]}… ({head_name or 'unbekannt'})")
    if hs in (factory_heads or {FACTORY_HEAD_SHA256}):
        r.add(GREEN, "Der Bootkopf ist der ab Werk (gleich bei V13, V14, V15) – so soll es sein.")
    elif head_name:
        how = ("Das entsteht im ROM-Bootmodus, wenn der ganze Flash beschrieben wurde – oder vielleicht durch ein "
               "normales Update auf V16: Ob der Original-Loader dabei den Kopf mitschreibt, ist noch nicht geklärt."
               if hs == next((k.head_sha256 for k in refs if k.family == "v16-layout"), None) else
               "Das entsteht nur, wenn im ROM-Bootmodus der ganze Flash beschrieben wurde.")
        r.add(YELLOW, f"Der Bootkopf ist nicht der ab Werk, sondern: {head_name}. {how} Melodee lehnt dann die "
                      "Rückkehr zur Original-V15 ab („FAILED -5“).")
    else:
        r.add(YELLOW, "Den Bootkopf kennt die App nicht (z. B. nach m-bootloader). Die Firmware kann trotzdem laufen; "
                      "die Rückkehr zur Original-Firmware kann dann „FAILED -5“ melden.")
    # App-Bereich
    app = _app_match(dump, refs)
    dlay = ""
    pre = sha(dump[APP_START:V15_END])
    v16 = next((k for k in refs if k.family == "v16-layout" and k.app_prefix.get(V15_END) == pre), None)
    truncated = False
    if app is None and v16 is not None:
        truncated = True
        r.firmware = f"{v16.name}, abgeschnitten"
        r.add(RED, "Im App-Bereich steht V16, aber nur bis 0x92FFF – der Rest (Ende des Programms und die "
                   "128 Werks-Voices, DX7.bin ab 0x96267) fehlt. So schreibt nur ein Werkzeug für V15-große Pakete "
                   "(z. B. der Unbricker im ROM-Bootmodus). Abhilfe: Original-V15 und danach deine Firmware neu "
                   "aufspielen.")
        r.details.append("App-Bereich 0x04000–0x92FFF = V16, 0x93000–0xA8FFF ≠ V16")
    elif app is not None:
        r.firmware = app.name
        r.details.append(f"App-Bereich 0x04000–{hx(app.flash_bytes - 1)}: byte-gleich mit {app.name} "
                         f"({app.file_sha256[:12]}…)")
        if expected and app.identity != expected:
            r.add(RED, f"Im Flash steht {app.name} ({app.identity}), erwartet war {expected}.")
        else:
            r.add(GREEN, f"Die Firmware im Flash ist byte-genau {app.name}.")
    else:
        # Unbekannter Inhalt: erst die Dateitabelle der Firmware selbst prüfen (Kopf- und Daten-CRCs). Werksgeräte
        # tragen ein anderes Abbild als das veröffentlichte V15-Paket – „beschädigt“ heißt es nur bei echten
        # Strukturfehlern, nie nur, weil Sektoren von einem Paket abweichen.
        st = app_directory(dump)
        dlay = directory_layout(st)
        factory = FACTORY_APPS.get(sha(dump[APP_START:V15_END]))
        best = None
        for p in packages:
            n = p.write_end
            diff = [a for a in range(APP_START, n, SECTOR) if dump[a:a + SECTOR] != p.flash[a:a + SECTOR]]
            if best is None or len(diff) < len(best[1]):
                best = (p, diff, (n - APP_START) // SECTOR)
        close = bool(best and len(best[1]) < best[2] // 4)
        lay_txt = {"v15-layout": "V15-Aufbau (Firmware bis 0x92FFF)",
                   "v16-layout": "V16-Aufbau (Firmware über 0x93000 hinaus)"}.get(dlay, "Aufbau ohne Vorlage")
        if st["found"]:
            r.details.append("Dateitabelle 0x04000: " + ("Kopf- und Datei-Prüfsummen stimmen" if st["ok"] else
                             "FEHLER: " + "; ".join(st["errors"])) + f", {lay_txt}"
                             + "".join(f", {f['name']} {f['size']:,} Bytes".replace(",", ".") for f in st["files"]))
        if factory and st["ok"] and dlay == "v15-layout":
            r.firmware = factory
            r.add(YELLOW, f"Im App-Bereich steht ein {factory}. Es weicht vom veröffentlichten V15-Paket ab – das ist "
                          "kein Fehler: Geräte ab Werk tragen ein anderes Abbild. Die Dateitabelle der Firmware und "
                          "ihre Prüfsummen stimmen. Nichts neu aufspielen; diese Sicherung ist dein Rückweg.")
        elif st["ok"] and close:
            name = best[0].label or best[0].identity
            r.firmware = f"Werks-Abbild oder andere Version von {name}"
            r.add(YELLOW, f"Werks-Abbild oder andere Version (weicht vom veröffentlichten Paket {name} in "
                          f"{len(best[1])} von {best[2]} Sektoren ab) – kein Fehler: Die Dateitabelle der Firmware "
                          "und die Prüfsummen (CRC) aller ihrer Dateien stimmen, sie ist in sich vollständig. Geräte "
                          "ab Werk tragen oft ein anderes Abbild als das veröffentlichte Paket. Nichts neu aufspielen.")
            r.details.append(f"Abweichende Sektoren gegenüber {name}: {ranges_text(merge_sectors(best[1]))}")
        elif st["found"] and not st["ok"]:
            r.firmware = (f"{best[0].label or best[0].identity} (beschädigt)" if close else "beschädigt")
            r.add(RED, "Die Firmware im App-Bereich ist beschädigt oder unvollständig geschrieben: "
                       + "; ".join(st["errors"]) + ". Diese Prüfsummen rechnet die Firmware-Tabelle selbst vor – "
                       "das ist ein echter Strukturfehler, keine bloße Abweichung von einem Paket. Bitte neu aufspielen.")
            if close:
                r.details.append(f"Abweichende Sektoren gegenüber {best[0].label or best[0].identity}: "
                                 f"{ranges_text(merge_sectors(best[1]))}")
        elif close:
            # keine lesbare Dateitabelle, aber fast gleich einem Paket: typischerweise ein abgebrochenes Schreiben
            r.firmware = f"{best[0].label or best[0].identity} (beschädigt)"
            r.add(RED, f"Der App-Bereich gleicht {best[0].label or best[0].identity}, aber {len(best[1])} von {best[2]} "
                       "Sektoren weichen ab und die Dateitabelle der Firmware bei 0x04000 ist nicht lesbar. Die "
                       "Firmware ist beschädigt oder unvollständig geschrieben – bitte neu aufspielen.")
            r.details.append(f"Abweichende Sektoren: {ranges_text(merge_sectors(best[1]))}")
        else:
            r.firmware = "unbekannt"
            r.add(YELLOW, "Die Firmware im App-Bereich passt zu keinem Paket, das die App kennt (z. B. eine neuere "
                          "Version). " + (f"Ihre Dateitabelle und Prüfsummen stimmen ({lay_txt}) – sie ist in sich "
                                          "vollständig." if st["ok"] else
                                          "Ob sie vollständig ist, lässt sich so nicht sagen."))
    # Reste fremder Firmware (nur bei V15-Layout-Firmware möglich: V16 schreibt bis 0xA8FFF; eine unbekannte Firmware,
    # deren eigene Tabelle den V16-Aufbau zeigt, trägt dort ihren Code – das sind keine Reste)
    if app is not None:
        family = app.family
    else:
        family = "v16-layout" if truncated or dlay == "v16-layout" else "v15-layout"
    if family == "v15-layout" and not truncated:
        hits = set()
        for k in refs:
            for addr, h in k.sectors.items():
                blk = dump[addr:addr + SECTOR]
                if addr >= V15_END and (sha(blk) == h or (k.blocks and _sector_blocks_match(blk, addr, k.blocks))):
                    hits.add(addr)
        for p in packages:
            if classify_family(p) != "v16-layout":
                continue
            for addr in range(V15_END, p.write_end, SECTOR):
                blk = dump[addr:addr + SECTOR]
                if blk != b"\xff" * SECTOR and _sector_matches(blk, p.flash[addr:addr + SECTOR]):
                    hits.add(addr)
        if hits:
            r.residue = merge_sectors(hits)
            r.add(YELLOW, f"In {len(hits)} Sektoren ({ranges_text(r.residue)}) liegen noch Reste von V16. V15 und "
                          "Community-Firmwares schreiben dort nie hin und räumen sie nicht weg. Melodee 1.5.1 lief im "
                          "Emulator damit genau wie auf sauberem Flash; für 1.6.0-rc.7 ist das nicht geprüft. "
                          "Löschen kannst du sie im ROM-Bootmodus mit „V16-Reste aufräumen“ (Expertenmodus dieser "
                          "Karte, am echten Gerät ungetestet) – oder liegen lassen.")
            r.details.append("Reste: " + ranges_text(r.residue) + " – Aufräum-Plan: "
                             + ranges_text(cleanup_plan([{"sector": a} for a in hits])))
        else:
            r.add(GREEN, "Keine Reste von V16 oder anderen bekannten Paketen in den Datenbereichen.")
    # Melodee-Ressourcen
    if app is not None and app.resources:
        rh = resource_header(dump)
        want = app.resources
        if rh and rh.get("payload_ok") and (rh["length"], rh["crc"]) == (want.get("length"), want.get("crc")):
            r.add(GREEN, "Die Melodee-Ressourcen (Werkssounds, Schrift, Bluetooth-Programm) bei 0xCB000 sind "
                         "vollständig und gehören zu genau diesem Build.")
        elif rh and rh.get("payload_ok"):
            r.add(YELLOW, "Die Melodee-Ressourcen sind in sich vollständig, gehören aber zu einem anderen Build. "
                          "Abhilfe: „Schrift & Werkssounds nachschreiben“ (Seite „Firmware“).")
        else:
            r.add(YELLOW, "Die Melodee-Ressourcen bei 0xCB000 fehlen oder sind beschädigt – dann fehlen Werkssounds. "
                          "Abhilfe: „Schrift & Werkssounds nachschreiben“ (Seite „Firmware“).")
        if rh:
            r.details.append(f"Ressourcen 0xCB000: {'Kopf ok' if rh.get('head_ok') else 'Kopf fehlt/defekt'}, "
                             f"Nutzdaten {'ok' if rh.get('payload_ok') else 'nicht ok'}")
    r.not_covered = [
        "Ob deine Songs, Presets und Einstellungen in sich stimmen, prüft die Firmware selbst – die App vergleicht nur "
        "mit bekannten Paketen.",
        "Bereiche ohne Vorlage (Einstellungen, Bluetooth-Kopplungen, Seriennummer) kann sie nicht bewerten.",
        "Der Dump zeigt den Stand zum Zeitpunkt des Lesens.",
    ]
    return r


# ----------------------------------------------------------------------------- Demo (synthetische Dumps)
def _demo_bytes(seed: str, n: int) -> bytes:
    out, i = bytearray(), 0
    while len(out) < n:
        out += hashlib.sha256(f"{seed}-{i}".encode()).digest()
        i += 1
    return bytes(out[:n])


def demo_refs() -> list[KnownRef]:
    """zwei erfundene Firmwares für den Demo-Modus (klar als Demo benannt) – mit echten Layoutgrenzen"""
    out = []
    for label, name, ident, end, fam in (("demo-melodee", "Demo-Melodee (Simulation)", "FM-1_91060", V15_END,
                                          "v15-layout"),
                                         ("demo-v16", "Demo-V16 (Simulation)", "FM-1_016", 0xA9000, "v16-layout")):
        fl = _demo_bytes(label, end)
        k = KnownRef(label=label, name=name, identity=ident, file_sha256=sha(fl), flash_bytes=end,
                     head_sha256=sha(fl[:HEAD_END]), app_sha256=sha(fl[APP_START:]), family=fam,
                     layout={"app": (APP_START, end)}, note="Demo")
        if fam == "v16-layout":
            k.app_prefix = {V15_END: sha(fl[APP_START:V15_END])}
            k.sectors = {a: sha(fl[a:a + SECTOR]) for a in range(V15_END, end, SECTOR)}
            k.blocks = {a: block_hash(fl[a:a + BLOCK]) for a in range(V15_END, end, BLOCK)}
        out.append(k)
    return out


def demo_heads() -> set[str]:
    return {sha(_demo_bytes("werk", HEAD_END))}


def demo_dumps() -> dict[str, bytes]:
    """synthetische 1-MiB-Dumps für den Demo-Modus: sauber, mit V16-Resten, V16 abgeschnitten"""
    head = _demo_bytes("werk", HEAD_END)
    mel, v16 = _demo_bytes("demo-melodee", V15_END), _demo_bytes("demo-v16", 0xA9000)
    user = _demo_bytes("nutzerdaten", FLASH_SIZE)

    def base():
        d = bytearray(user)
        d[:HEAD_END] = head
        return d
    clean = base()
    clean[APP_START:V15_END] = mel[APP_START:]
    resid = bytearray(clean)
    resid[0x97000:0xA9000] = v16[0x97000:0xA9000]
    trunc = base()
    trunc[APP_START:V15_END] = v16[APP_START:V15_END]
    return {"demo-sauber.bin": bytes(clean), "demo-v16-reste.bin": bytes(resid),
            "demo-v16-abgeschnitten.bin": bytes(trunc)}


# ----------------------------------------------------------------------------- Datenverlust in Worten
# Melodee 1.6 (rc.7): Speicherkarte laut firmware/src/storage.c und res.c [Paket]
MELODEE_MAP = [
    (0x93000, 0x9D000, "Prophet-Bänke"), (0x9D000, 0x9F000, "gemeinsame Preset-Erweiterung"),
    (0x9F000, 0xA0000, "FM6-Bank (Kopie A)"), (0xA0000, 0xC8000, "Projekte und Preset-Bänke (Pool)"),
    (0xC8000, 0xCB000, "CZ-Bänke"), (0xCB000, 0xE0000, "Ressourcen (Werkssounds, Schrift, Bluetooth-Programm)"),
    (0xE0000, 0xE5000, "Update-Zwischenspeicher"), (0xE5000, 0xFB000, "Erweiterungen und Projekte (Pool)"),
    (0xFC000, 0xFF000, "Einstellungen"),
]


def _names(lo: int, hi: int, table) -> str:
    hit = [n for a, b, n in table if a < hi and b > lo]
    return ", ".join(dict.fromkeys(hit))


def describe_loss(current_identity: str, current_key: str, target_identity: str) -> list[str]:
    """Sätze für „Was danach nicht mehr auf dem Gerät ist“ bei einem Wechsel zwischen V15-Aufbau und V16"""
    out = []
    melodee = (current_key or "").startswith("melodee") or current_identity.startswith("FM-1_910")
    if target_identity == "FM-1_016" and current_identity != "FM-1_016":
        what = (_names(V15_END, 0xA9000, MELODEE_MAP) if melodee else "Songs, Presets und Einstellungen")
        out.append(f"Beim Schreiben von V16: der Bereich 0x93000–0xA8FFF (22 Sektoren) – bei dir: {what}. "
                   "So gut wie sicher: Der Update-Loader schreibt genau so weit, wie das Paket lang ist (bei V15 an echter Hardware aufgezeichnet).")
        rt = RUNTIME["FM-1_016"]["erase"]
        what2 = ", ".join(dict.fromkeys(n for lo, hi in rt for n in [_names(lo, hi, MELODEE_MAP)] if n)) \
            if melodee else "weitere Song- und Preset-Bereiche"
        out.append("Beim ersten Start formatiert V16 zusätzlich " + ranges_text([(a, b - a) for a, b in rt])
                   + f" – bei dir: {what2}. Beobachtet im Emulator, nicht an echter Hardware."
                   + (" Die Ressourcen schreibt die App beim Rückweg auf Melodee neu." if melodee else ""))
    elif current_identity == "FM-1_016" and target_identity != "FM-1_016":
        out.append("Alles, was du unter V16 gespeichert hast (Patterns, Einstellungen), ist danach nicht mehr nutzbar – "
                   "die App kann von V16 kein Backup machen.")
        out.append("V15 und Community-Firmwares schreiben nur bis 0x92FFF: Reste von V16 in 0x93000–0xA8FFF bleiben "
                   "liegen. Melodee 1.5.1 lief damit im Emulator wie auf sauberem Flash; prüfen kannst du es mit "
                   "„Ist mein FM-1 sauber?“ (Rettung).")
    return out
