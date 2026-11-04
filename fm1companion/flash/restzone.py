# SPDX-License-Identifier: GPL-3.0-only
""""Clean up leftovers": erase V16 leftovers in 0x93000–0xA8FFF selectively – in ROM boot mode, sector by sector.

Why this is needed: V16 writes its app area up to 0xA8FFF. V15 and all community firmwares (Melodee, SLOOP,
Skiff, JIANT) only write up to 0x92FFF and never clean up the rest [Pkg + Rec, docs/flash-protocol.md §17].

Approach: the bundled, unmodified MvaveFM1Unbricker (vendor/unbricker/fm1_unbrick.py) as a library. Via
kagaimiq's jl-uboot-tool it provides the primitive "erase one 4 KiB sector" (JL_LoaderV2 ERASE_FLASH_SECTOR, the same
function `erase_repair_sector` its `repair-settings` uses – upstream only tested with a mock) and its
safety gates `_check_mutable`/`_check_repairable`. Nothing is ever written, only erased.

Hard rules (in the code, not just in the dialog):
  * only whole sectors in the leftover zone 0x93000–0xA8FFF (`gate`), never NEVER_ERASE (boot header, key_mac) or
    Bluetooth; in addition the Unbricker's gates;
  * only if a dump freshly read in THIS session (read twice, identical, saved to a file and
    read back – `fm1_unbrick.take_backup`) shows that the sector still carries exactly V16 bytes (fully or as a
    partial write); whatever a firmware has written there since stays;
  * only if the app area holds, byte for byte, a known firmware with V15 layout – never with V16 or unknown content;
  * immediately before erasing, the sector is read again (twice) and must equal the dump;
  * after each sector: read the sector twice (must be blank) and compare the whole 64 KiB block around it with what
    is expected (catches a chip that erases more than 4 KiB); at the end, the whole flash;
  * journal (atomic, fsync) before and after each sector; after a crash the next run continues. A half-erased
    sector only still counts as a V16 leftover if, according to the journal backup, its original was V16 and
    since then only bits have changed from 0 to 1 (which is what an erase does).

Independent of the boot header: whether the stock loader also writes the header when updating to V16 is open; the
header is neither read nor assessed here and stays NEVER_ERASE.

UNTESTED on a real device (only with the Unbricker's mock FM-1). Whether the WL82 loader really limits "erase sector"
to exactly 4 KiB is an [Assumption] – the block check after each sector then stops immediately.

Runs as a script (root via uboot.Runner + ROOT_HELPER, verified copy) and as a module (planning, tests, report).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
if __package__:
    from . import flashmodel as fm
else:                                   # as a script: flashmodel.py sits next to it (source tree or verified root copy)
    sys.path.insert(0, str(HERE))
    import flashmodel as fm             # noqa: E402

SECTOR = fm.SECTOR
BLOCK64 = 0x10000
ZONE = (fm.V15_END, 0xA9000)            # 0x93000–0xA8FFF: what V16 writes beyond the V15 size [Pkg]
BLANK = b"\xff" * SECTOR
# Bluetooth sector of both layouts [Pkg] – lies outside the zone, still checked strictly
BTIF = [(0xE9000, 0xEA000, "Bluetooth-Daten (V15-Aufbau)"), (0xEC000, 0xED000, "Bluetooth-Daten (V16-Aufbau)")]
FORBIDDEN = list(fm.NEVER_ERASE) + BTIF
RESUME_MIN_BYTES = 16                   # half-erased sector: this many non-0xFF bytes must still carry it
UNTESTED = "am echten Gerät ungetestet"

EXIT_OK, EXIT_REFUSED, EXIT_FAIL, EXIT_DANGER = 0, 10, 11, 12
JOURNAL_NAME = "reste-journal.json"


class Refused(Exception):
    """Rule violated – nothing was erased"""


class Abort(Exception):
    """Abort in the middle of erasing (error, crash simulation)"""


# ----------------------------------------------------------------------------- hard gates
def gate(addr: int) -> None:
    """single gate for every erase command in this file: whole sector in the leftover zone, nothing forbidden"""
    if not isinstance(addr, int) or addr % SECTOR:
        raise Refused(f"{addr!r} ist keine Sektorgrenze – abgelehnt.")
    lo, hi = addr, addr + SECTOR
    if lo < ZONE[0] or hi > ZONE[1]:
        raise Refused(f"{fm.hx(addr)} liegt außerhalb der Restzone {fm.hx(ZONE[0])}–{fm.hx(ZONE[1] - 1)} – abgelehnt.")
    for nlo, nhi, why in FORBIDDEN:
        if lo < nhi and hi > nlo:
            raise Refused(f"{fm.hx(addr)} berührt „{why}“ – wird nie gelöscht.")


# ----------------------------------------------------------------------------- detection
def v16_kind(blk: bytes, addr: int, v16_refs, v16_pkgs=()) -> str:
    """"voll" (exactly the V16 bytes), "teilweise" (partially written by an abort: every non-0xFF byte or every
    non-0xFF block equals V16) or "" (no V16 bytes)"""
    if blk == BLANK or len(blk) != SECTOR:
        return ""
    h = fm.sha(blk)
    for k in v16_refs:
        if k.sectors.get(addr) == h:
            return "voll"
    for p in v16_pkgs:
        kb = bytes(p.flash[addr:addr + SECTOR])
        if len(kb) == SECTOR and blk == kb:
            return "voll"
    for k in v16_refs:
        if k.blocks and fm._sector_blocks_match(blk, addr, k.blocks):
            return "teilweise"
    for p in v16_pkgs:
        kb = bytes(p.flash[addr:addr + SECTOR])
        if len(kb) == SECTOR and fm._sector_matches(blk, kb):
            return "teilweise"
    return ""


def half_erased(cur: bytes, orig: bytes) -> bool:
    """cur may have come from orig through an interrupted erase: only bits 0→1, and enough bytes still
    left that this is not coincidentally new data"""
    if len(cur) != len(orig) or cur == BLANK:
        return False
    nz = [i for i, b in enumerate(cur) if b != 0xFF]
    return len(nz) >= RESUME_MIN_BYTES and all(cur[i] & orig[i] == orig[i] for i in nz)


@dataclass
class Plan:
    ok: bool = False                                  # may it be erased at all?
    reason: str = ""                                  # why not (plain language)
    firmware: str = ""
    family: str = ""
    app_sha256: str = ""
    sectors: list = field(default_factory=list)       # [{"addr", "kind", "orig_file", "orig_sha256"}]
    skipped: list = field(default_factory=list)       # [{"addr", "why"}]
    already_clean: int = 0                            # blank sectors in the zone

    def addrs(self) -> list[int]:
        return [s["addr"] for s in self.sectors]


def make_plan(dump: bytes, refs, v16_pkgs=(), journal: dict | None = None, backup_path: str = "",
              jread=None) -> Plan:
    """Cleanup plan from a freshly read 1 MiB dump. journal: unfinished journal of an earlier run
    (for half-erased sectors); jread(path) -> bytes reads its backup. Never raises; Plan.ok says whether erasing
    is allowed."""
    p = Plan()
    if len(dump) != fm.FLASH_SIZE:
        p.reason = "Der Dump hat nicht genau 1 MiB – so wird nichts gelöscht."
        return p
    p.app_sha256 = fm.sha(dump[fm.APP_START:fm.V15_END])
    v16_refs = [k for k in refs if k.family == "v16-layout"]
    app = fm._app_match(dump, refs)
    if app is None:
        p.reason = ("Die Firmware im Flash ist keine, die die App byte-genau kennt (oder sie ist beschädigt). Dann "
                    "lässt sich nicht sicher sagen, welche Bereiche ihr gehören – es wird nichts gelöscht. Spiel "
                    "zuerst eine bekannte Firmware auf (z. B. Melodee 1.6.0-rc.7) und lies den Flash dann neu.")
        return p
    p.firmware, p.family = app.name, app.family
    if app.family != "v15-layout":
        p.reason = (f"Auf dem FM-1 steht {app.name}. Bei V16 gehört 0x93000–0xA8FFF zur Firmware selbst – dort wird "
                    "nie aufgeräumt. Aufräumen geht erst, wenn wieder V15, Melodee oder eine andere Firmware mit "
                    "V15-Aufbau läuft.")
        return p
    if not v16_refs and not v16_pkgs:
        p.reason = "Der App fehlt der V16-Steckbrief – ohne ihn erkennt sie keine Reste. Es wird nichts gelöscht."
        return p
    jsec = {}
    if journal and journal.get("state") == "laeuft" and journal.get("app_sha256") == p.app_sha256:
        jsec = {int(a, 16): e for a, e in journal.get("sectors", {}).items()}
    cache: dict[str, bytes | None] = {}
    for addr in range(ZONE[0], ZONE[1], SECTOR):
        gate(addr)
        blk = bytes(dump[addr:addr + SECTOR])
        if blk == BLANK:
            p.already_clean += 1
            continue
        kind = v16_kind(blk, addr, v16_refs, v16_pkgs)
        if kind:
            p.sectors.append({"addr": addr, "kind": kind, "orig_file": backup_path, "orig_sha256": fm.sha(blk)})
            continue
        j = jsec.get(addr)
        if j and j.get("state") == "loeschen":
            orig = _journal_orig(j, jread, cache)
            if orig is not None and v16_kind(orig, addr, v16_refs, v16_pkgs) and half_erased(blk, orig):
                p.sectors.append({"addr": addr, "kind": "unterbrochen", "orig_file": j["orig_file"],
                                  "orig_sha256": j["orig_sha256"]})
                continue
            p.skipped.append({"addr": addr, "why": "Löschen wurde unterbrochen, der Inhalt ist aber nicht mehr "
                                                   "eindeutig ein halb gelöschter V16-Rest – bleibt, wie er ist."})
            continue
        p.skipped.append({"addr": addr, "why": "trägt keine V16-Bytes (z. B. Daten deiner Firmware) – bleibt."})
    p.ok = True
    return p


def _journal_orig(j: dict, jread, cache) -> bytes | None:
    f = j.get("orig_file") or ""
    if not f or jread is None:
        return None
    if f not in cache:
        try:
            cache[f] = jread(f)
        except OSError:
            cache[f] = None
    data = cache[f]
    if not data or len(data) != fm.FLASH_SIZE:
        return None
    addr = int(j.get("addr", "0"), 16)
    orig = bytes(data[addr:addr + SECTOR])
    return orig if fm.sha(orig) == j.get("orig_sha256") else None


# ----------------------------------------------------------------------------- journal
def load_journal(path: Path) -> dict | None:
    try:
        d = json.loads(Path(path).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and d.get("schema") == 1 else None


def save_journal(path: Path, data: dict) -> None:
    """atomic: write new file, fsync, rename, fsync directory"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".neu")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, indent=1, ensure_ascii=False))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    try:
        fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


# ----------------------------------------------------------------------------- procedure on the device
def _unbricker():
    """vendor/unbricker/fm1_unbrick.py (unmodified) – next to this script (root copy) or in the source tree"""
    try:
        import fm1_unbrick
        return fm1_unbrick
    except ImportError:
        pass
    side = HERE / "fm1_unbrick.py"
    sys.path.insert(0, str(HERE if side.is_file() else HERE.parent / "vendor" / "unbricker"))
    import fm1_unbrick
    return fm1_unbrick


def _read2(fu, dev, addr: int, n: int) -> bytes:
    a = fu.read_flash(dev, addr, n, None)
    b = fu.read_flash(dev, addr, n, None)
    if a != b:
        raise Abort(f"Zwei Lesungen von {fm.hx(addr)} weichen voneinander ab – die USB-Verbindung ist unzuverlässig.")
    return a


def execute(dev, fu, outdir: Path, refs, v16_pkgs=(), only: set | None = None, plan_only: bool = False,
            journal_path: Path | None = None, abort_after: int | None = None) -> dict:
    """on the opened device (fm1_unbrick.Fm1Device, real or mock): back up, plan, erase, verify -> result.
    only: only these sectors (confirmed by the user) – intersected with the fresh plan.
    abort_after: mock only – after this many erase commands, simulate a half-erased sector and an abort."""
    log = fu.log
    if abort_after is not None and not dev.mock:
        raise Refused("Absturz-Simulation gibt es nur mit dem Mock-FM-1.")
    journal_path = Path(journal_path or outdir / JOURNAL_NAME)
    res = {"schema": 1, "tool": "restzone", "untested": UNTESTED, "mock": bool(dev.mock), "plan_only": plan_only,
           "zone": [fm.hx(ZONE[0]), fm.hx(ZONE[1] - 1)], "erased": [], "skipped": [], "ok": False, "level": "rot"}

    def finish(**kw):
        res.update(kw)
        return res

    dev.session = None                                # every run is its own session with its own backup
    log.info("Schritt 1/5: Gerät prüfen (Chip-Key, Flash-ID)")
    fu.check_identity(dev, False)
    log.info("Schritt 2/5: Pflicht-Sicherung – ganzer Flash, zweimal gelesen und verglichen")
    backup_path, dump = fu.take_backup(dev, outdir)
    res.update(backup=str(backup_path), backup_sha256=fm.sha(dump), session=str(backup_path.parent))
    log.info("Schritt 3/5: Plan aus dieser Sicherung")
    old = load_journal(journal_path)
    plan = make_plan(dump, refs, v16_pkgs, old, str(backup_path), jread=lambda f: Path(f).read_bytes())
    res.update(firmware=plan.firmware, family=plan.family, already_clean=plan.already_clean,
               planned=[{"addr": fm.hx(s["addr"]), "kind": s["kind"]} for s in plan.sectors],
               skipped=[{"addr": fm.hx(s["addr"]), "why": s["why"]} for s in plan.skipped])
    if not plan.ok:
        log.info("Abgelehnt: %s Es wurde nichts gelöscht.", plan.reason)
        return finish(refused=plan.reason, ok=False, level="gelb")
    # top to bottom: if the chip unexpectedly erases a whole 64 KiB block, the first (verified) mishap hits
    # block 0xA0000–0xAFFFF (leftovers + backed-up data) and never the firmware end 0x90000–0x92FFF; the block check then stops.
    todo = sorted((s for s in plan.sectors if only is None or s["addr"] in only), key=lambda s: s["addr"], reverse=True)
    if only is not None:
        for a in sorted(only - set(plan.addrs())):
            res["skipped"].append({"addr": fm.hx(a), "why": "war geplant, ist laut frischer Sicherung aber kein "
                                                         "V16-Rest (mehr) – bleibt."})
        for s in plan.sectors:
            if s["addr"] not in only:
                res["skipped"].append({"addr": fm.hx(s["addr"]), "why": "V16-Rest, aber nicht bestätigt – bleibt."})
    log.info("  Firmware: %s · V16-Reste: %d Sektoren %s · leer: %d · bleiben: %d", plan.firmware, len(plan.sectors),
             fm.ranges_text(fm.merge_sectors(plan.addrs())) if plan.sectors else "", plan.already_clean,
             len(plan.skipped))
    if plan_only or not todo:
        if not plan_only and old and old.get("state") == "laeuft":
            old["state"] = "abgeschlossen"
            save_journal(journal_path, old)
        return finish(ok=True, level="gruen" if not plan.sectors else "gelb")

    for s in todo:                                   # all gates up front, before anything is erased
        gate(s["addr"])
        fu._check_repairable(s["addr"], SECTOR)
    journal = {"schema": 1, "state": "laeuft", "app_sha256": plan.app_sha256, "started": time.strftime("%Y-%m-%d %H:%M:%S"),
               "backup": str(backup_path), "sectors": {}}
    for s in todo:
        journal["sectors"][fm.hx(s["addr"])] = {"addr": fm.hx(s["addr"]), "state": "geplant", "kind": s["kind"],
                                                "orig_file": s["orig_file"], "orig_sha256": s["orig_sha256"]}
    save_journal(journal_path, journal)
    expected = bytearray(dump)
    log.info("Schritt 4/5: %d Sektoren löschen, jeden einzeln prüfen (%s). Jetzt nichts abstecken.", len(todo), UNTESTED)
    erases = 0
    try:
        for s in todo:
            addr = s["addr"]
            key = fm.hx(addr)
            gate(addr)
            fu._check_repairable(addr, SECTOR)
            now = _read2(fu, dev, addr, SECTOR)
            if now != dump[addr:addr + SECTOR]:
                raise Abort(f"Sektor {key} hat sich seit der Sicherung verändert – angehalten.")
            journal["sectors"][key]["state"] = "loeschen"
            save_journal(journal_path, journal)
            for attempt in (1, 2):
                fu.erase_repair_sector(dev, addr)
                erases += 1
                if abort_after is not None and erases >= abort_after:
                    blk = dev.ldr.flash[addr:addr + SECTOR]          # mock: leave it half-erased
                    for i in range(0, SECTOR, 2):
                        blk[i] = dump[addr + i]
                    dev.ldr.flash[addr:addr + SECTOR] = blk
                    raise Abort("Simulierter Absturz mitten im Löschen (nur in der Simulation).")
                back = _read2(fu, dev, addr, SECTOR)
                if back == BLANK:
                    break
                log.info("  Sektor %s ist nach dem Löschen nicht leer – zweiter Versuch", key)
            else:
                raise Abort(f"Sektor {key} ist nach zwei Löschversuchen nicht leer.")
            expected[addr:addr + SECTOR] = BLANK
            blo = addr - addr % BLOCK64
            got = fu.read_flash(dev, blo, BLOCK64, None)
            if got != bytes(expected[blo:blo + BLOCK64]):
                bad = next(i for i in range(BLOCK64) if got[i] != expected[blo + i])
                raise Abort(f"Nach dem Löschen von {key} hat sich auch {fm.hx(blo + bad)} verändert – der Chip hat "
                            "mehr gelöscht als den einen Sektor. Sofort angehalten.")
            journal["sectors"][key]["state"] = "fertig"
            save_journal(journal_path, journal)
            res["erased"].append(key)
            log.info("  %s gelöscht und geprüft (%s)", key, s["kind"])
        log.info("Schritt 5/5: ganzen Flash zurücklesen und mit dem Erwarteten vergleichen")
        final = fu.read_flash(dev, 0, fm.FLASH_SIZE, "Kontrolle")
        if final != bytes(expected):
            bad = next(i for i in range(fm.FLASH_SIZE) if final[i] != expected[i])
            raise Abort(f"Der Flash weicht bei {fm.hx(bad)} vom Erwarteten ab.")
        after = Path(res["session"]) / "full-flash-after.bin"
        fu.durable_write(after, final)
        journal["state"] = "abgeschlossen"
        save_journal(journal_path, journal)
        log.info("Fertig: %d Sektoren gelöscht und geprüft, alles andere byte-gleich mit der Sicherung.", len(todo))
        return finish(ok=True, level="gruen", after=str(after))
    except (Abort, Refused, fu.UnbrickError, OSError) as e:
        log.error("ANGEHALTEN: %s", e)
        return finish(ok=False, level="rot", error=str(e), journal=str(journal_path))


# ----------------------------------------------------------------------------- report (plain language)
def report(res: dict):
    """Result file of the run -> flashmodel.Report with traffic light"""
    r = fm.Report(title=("Reste aufräumen – Plan" if res.get("plan_only") else "Reste aufräumen")
                  + (" (Simulation)" if res.get("mock") else ""))
    r.firmware = res.get("firmware", "")
    planned, erased, skipped = res.get("planned", []), res.get("erased", []), res.get("skipped", [])
    if res.get("refused"):
        r.add(fm.YELLOW, res["refused"] + " Es wurde nichts gelöscht.")
    elif res.get("error"):
        r.add(fm.RED, f"Angehalten: {res['error']} Gelöscht und geprüft: {len(erased)} Sektoren"
                      + (f" ({', '.join(erased)})" if erased else "") + ". Deine Sicherung liegt bereit; das Journal "
                      "merkt sich den Stand. Bitte das FM-1 erst wieder normal benutzen, wenn der Lauf fertig ist: im "
                      "ROM-Bootmodus noch einmal „V16-Reste aufräumen“ starten – er setzt dort fort.")
    elif res.get("plan_only"):
        if planned:
            r.residue = fm.merge_sectors(int(p["addr"], 16) for p in planned)
            r.add(fm.YELLOW, f"{len(planned)} Sektoren tragen noch V16-Bytes ({fm.ranges_text(r.residue)}). Diese – "
                             "und nur diese – würde „Aufräumen“ löschen.")
        else:
            r.add(fm.GREEN, "In 0x93000–0xA8FFF liegen keine V16-Reste. Es gibt nichts aufzuräumen.")
    elif erased:
        r.add(fm.GREEN, f"{len(erased)} Sektoren mit V16-Resten gelöscht, jeder einzeln geprüft; danach den ganzen "
                        "Flash zurückgelesen: Alles andere ist byte-gleich mit der Sicherung.")
    else:
        r.add(fm.GREEN, "Es gab nichts zu löschen – in 0x93000–0xA8FFF liegen keine V16-Reste.")
    if res.get("firmware"):
        r.details.append(f"Firmware im Flash: {res['firmware']}")
    if skipped:
        r.add(fm.GREEN, f"{len(skipped)} Sektoren in der Zone bleiben unangetastet (keine V16-Bytes, z. B. Daten "
                        "deiner Firmware).")
        r.details += [f"bleibt {s['addr']}: {s['why']}" for s in skipped[:40]]
    if res.get("backup"):
        r.details.append(f"Sicherung (zweimal gelesen, identisch): {res['backup']}")
    if res.get("after"):
        r.details.append(f"Flash danach: {res['after']}")
    if res.get("journal"):
        r.details.append(f"Journal: {res['journal']}")
    r.not_covered = [
        f"Diese Funktion ist {UNTESTED} – bisher nur mit dem simulierten FM-1 des Unbrickers geprüft. Dass der Chip "
        "beim Befehl „Sektor löschen“ genau 4 KiB löscht, ist angenommen; die App prüft danach jeden Block nach und "
        "hält sofort an, wenn es mehr war.",
        "Ob deine Firmware mit oder ohne die Reste besser läuft, sagt dieser Lauf nicht. Melodee 1.5.1 lief im "
        "Emulator mit Resten genauso wie ohne; für 1.6.0-rc.7 ist das nicht geprüft.",
        "Bootkopf, Firmware-Bereich und alles außerhalb 0x93000–0xA8FFF werden nicht verändert.",
    ]
    return r


# ----------------------------------------------------------------------------- demo
def demo_image() -> bytes:
    """Mock flash for the simulation: demo Melodee with V16 leftovers 0x97000–0xA8FFF, one sector half-written,
    user data in 0x93000–0x96FFF before it"""
    d = bytearray(fm.demo_dumps()["demo-v16-reste.bin"])
    d[0x97000:0x97800] = b"\xff" * 0x800            # aborted update: the lower blocks are missing
    return bytes(d)


# ----------------------------------------------------------------------------- command line
def build_parser():
    ap = argparse.ArgumentParser(prog="restzone.py", description="V16-Reste 0x93000–0xA8FFF löschen (ROM-Bootmodus)")
    ap.add_argument("command", choices=["plan", "clean"])
    ap.add_argument("dir", help="Ordner für Sicherungen und Journal")
    ap.add_argument("--result", required=True, help="Ergebnis als JSON hierhin")
    ap.add_argument("--only", default="", help="clean: nur diese Sektoren (0x…,0x…)")
    ap.add_argument("--v16-package", action="append", default=[], help="V16-Paket für Teilsektoren (optional)")
    ap.add_argument("--refs", default="", help="known_packages.json (Standard: neben dem Skript bzw. data/)")
    ap.add_argument("--demo-refs", action="store_true", help="Demo-Firmwares dazunehmen (nur mit --dry-run)")
    ap.add_argument("--dry-run", action="store_true", help="Mock-FM-1 des Unbrickers statt echtem Gerät")
    ap.add_argument("--mock-image", help="Mock: Flash-Inhalt (1 MiB)")
    ap.add_argument("--mock-demo", action="store_true", help="Mock: eingebautes Demo-Abbild")
    ap.add_argument("--mock-abort-after", type=int, help="Mock: Absturz nach N Löschbefehlen simulieren")
    ap.add_argument("--device", help="/dev/sgN")
    ap.add_argument("--jl-dir", default="")
    ap.add_argument("--log-dir", default="logs")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    fu = _unbricker()
    log_path = fu.setup_logging("reste-" + args.command, Path(args.log_dir))
    out = Path(args.dir)
    res: dict = {"schema": 1, "ok": False, "level": "rot", "mock": args.dry_run, "plan_only": args.command == "plan"}
    rc = EXIT_FAIL
    try:
        if (args.demo_refs or args.mock_demo or args.mock_abort_after is not None) and not args.dry_run:
            raise Refused("Demo-Optionen gibt es nur in der Simulation.")
        side = HERE / "known_packages.json"
        refs = fm.load_known(args.refs or (side if side.is_file() else None))
        if args.demo_refs:
            refs += fm.demo_refs()
        pkgs = []
        for f in args.v16_package:
            try:
                p = fm.Package.load(f)
            except (fm.PackageError, OSError) as e:
                fu.log.info("V16-Paket %s nicht lesbar (%s) – ohne es weiter", f, e)
                continue
            if fm.classify_family(p) == "v16-layout":
                pkgs.append(p)
        only = None
        if args.command == "clean":
            if not args.only:
                raise Refused("Ohne bestätigte Sektorliste wird nichts gelöscht.")
            only = {int(a, 16) for a in args.only.split(",") if a.strip()}
            for a in only:
                gate(a)
        ns = argparse.Namespace(dry_run=args.dry_run, device=args.device, i_know=False, jl_dir=args.jl_dir,
                                mock_image=None, mock_flaky=None, mock_chip_key=None, dir=str(out))
        if args.dry_run:
            img = Path(args.mock_image).read_bytes() if args.mock_image else (demo_image() if args.mock_demo else None)
            mock = fu.MockUboot(image=img)
            dev = fu.Fm1Device("mock://fm1-uboot", mock, io_max=mock.usb_buffer_size(), mock=True)
            fu.log.info("SIMULATION: Mock-FM-1 des Unbrickers – kein echtes Gerät, nichts wird an Hardware verändert.")
        else:
            dev = fu.connect(ns)
        try:
            fu.save_retained_ram(dev, out)
            res = execute(dev, fu, out, refs, pkgs, only, plan_only=args.command == "plan",
                          abort_after=args.mock_abort_after)
            if dev.mock and args.mock_image:          # simulation: the mock flash survives as a file until the next run
                Path(args.mock_image).write_bytes(bytes(dev.ldr.flash))
        finally:
            dev.close()
        rc = EXIT_OK if res.get("ok") else (EXIT_REFUSED if res.get("refused") else EXIT_DANGER)
    except Refused as e:
        fu.log.error("ABGELEHNT: %s Es wurde nichts gelöscht.", e)
        res.update(refused=str(e), level="gelb")
        rc = EXIT_REFUSED
    except fu.UnbrickError as e:
        fu.log.error("FEHLER: %s", e)
        res.update(error=str(e))
        rc = EXIT_FAIL
    finally:
        try:
            Path(args.result).parent.mkdir(parents=True, exist_ok=True)
            Path(args.result).write_text(json.dumps(res, indent=1, ensure_ascii=False), "utf-8")
        except OSError as e:
            fu.log.error("Ergebnis-Datei nicht schreibbar: %s", e)
        fu.log.info("Protokoll: %s", log_path)
        fu.close_logging()
    return rc


if __name__ == "__main__":
    sys.exit(main())
