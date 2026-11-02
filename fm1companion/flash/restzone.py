# SPDX-License-Identifier: GPL-3.0-only
"""„Reste aufräumen“: V16-Reste in 0x93000–0xA8FFF gezielt löschen – im ROM-Bootmodus, Sektor für Sektor.

Warum es das braucht: V16 schreibt seinen App-Bereich bis 0xA8FFF. V15 und alle Community-Firmwares (Melodee, SLOOP,
Skiff, JIANT) schreiben nur bis 0x92FFF und räumen den Rest nie weg [Paket + Aufz., docs/flash-protocol.md §17].

Weg: der mitgelieferte, unveränderte MvaveFM1Unbricker (vendor/unbricker/fm1_unbrick.py) als Bibliothek. Er bringt
über kagaimiqs jl-uboot-tool das Primitiv „einen 4-KiB-Sektor löschen“ (JL_LoaderV2 ERASE_FLASH_SECTOR, dieselbe
Funktion `erase_repair_sector`, mit der sein `repair-settings` arbeitet – upstream nur mit Mock getestet) und seine
Sicherheitstore `_check_mutable`/`_check_repairable`. Geschrieben wird nie, nur gelöscht.

Harte Regeln (im Code, nicht nur im Dialog):
  * nur ganze Sektoren in der Restzone 0x93000–0xA8FFF (`gate`), nie NEVER_ERASE (Bootkopf, key_mac) oder Bluetooth;
    zusätzlich die Tore des Unbrickers;
  * nur, wenn ein in DIESER Sitzung frisch gelesener Dump (zweimal gelesen, identisch, als Datei gesichert und
    rückgelesen – `fm1_unbrick.take_backup`) zeigt, dass der Sektor noch genau V16-Bytes trägt (voll oder als
    Teilschreibung); was eine Firmware inzwischen neu beschrieben hat, bleibt;
  * nur, wenn im App-Bereich byte-genau eine bekannte Firmware mit V15-Aufbau steht – nie bei V16 oder Unbekanntem;
  * unmittelbar vor dem Löschen wird der Sektor erneut (zweimal) gelesen und muss dem Dump gleichen;
  * nach jedem Sektor: Sektor zweimal lesen (muss leer sein) und den ganzen 64-KiB-Block drumherum mit dem Erwarteten
    vergleichen (fängt einen Chip ab, der mehr als 4 KiB löscht); am Ende den ganzen Flash;
  * Journal (atomar, fsync) vor und nach jedem Sektor; nach einem Absturz setzt der nächste Lauf fort. Ein halb
    gelöschter Sektor zählt nur dann noch als V16-Rest, wenn sein Original laut Journal-Sicherung V16 war und sich
    seitdem ausschließlich Bits von 0 auf 1 geändert haben (das, was ein Löschvorgang tut).

Nicht abhängig vom Bootkopf: Ob der Original-Loader beim Update auf V16 den Kopf mitschreibt, ist offen; der Kopf
wird hier weder gelesen noch bewertet und bleibt NEVER_ERASE.

Am echten Gerät UNGETESTET (nur mit dem Mock-FM-1 des Unbrickers). Ob der WL82-Loader „Sektor löschen“ wirklich auf
genau 4 KiB begrenzt, ist [Annahme] – die Blockprüfung nach jedem Sektor hält dann sofort an.

Läuft als Skript (root über uboot.Runner + ROOT_HELPER, geprüfte Kopie) und als Modul (Planung, Tests, Bericht).
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
else:                                   # als Skript: flashmodel.py liegt daneben (Quellbaum oder geprüfte root-Kopie)
    sys.path.insert(0, str(HERE))
    import flashmodel as fm             # noqa: E402

SECTOR = fm.SECTOR
BLOCK64 = 0x10000
ZONE = (fm.V15_END, 0xA9000)            # 0x93000–0xA8FFF: was V16 über V15-Größe hinaus schreibt [Paket]
BLANK = b"\xff" * SECTOR
# Bluetooth-Sektor beider Layouts [Paket] – liegt außerhalb der Zone, wird trotzdem hart geprüft
BTIF = [(0xE9000, 0xEA000, "Bluetooth-Daten (V15-Aufbau)"), (0xEC000, 0xED000, "Bluetooth-Daten (V16-Aufbau)")]
FORBIDDEN = list(fm.NEVER_ERASE) + BTIF
RESUME_MIN_BYTES = 16                   # halb gelöschter Sektor: so viele Nicht-0xFF-Bytes müssen ihn tragen
UNTESTED = "am echten Gerät ungetestet"

EXIT_OK, EXIT_REFUSED, EXIT_FAIL, EXIT_DANGER = 0, 10, 11, 12
JOURNAL_NAME = "reste-journal.json"


class Refused(Exception):
    """Regel verletzt – es wurde nichts gelöscht"""


class Abort(Exception):
    """Abbruch mitten im Löschen (Fehler, Absturz-Simulation)"""


# ----------------------------------------------------------------------------- harte Tore
def gate(addr: int) -> None:
    """einziges Tor für jeden Löschbefehl dieser Datei: ganzer Sektor in der Restzone, nichts Verbotenes"""
    if not isinstance(addr, int) or addr % SECTOR:
        raise Refused(f"{addr!r} ist keine Sektorgrenze – abgelehnt.")
    lo, hi = addr, addr + SECTOR
    if lo < ZONE[0] or hi > ZONE[1]:
        raise Refused(f"{fm.hx(addr)} liegt außerhalb der Restzone {fm.hx(ZONE[0])}–{fm.hx(ZONE[1] - 1)} – abgelehnt.")
    for nlo, nhi, why in FORBIDDEN:
        if lo < nhi and hi > nlo:
            raise Refused(f"{fm.hx(addr)} berührt „{why}“ – wird nie gelöscht.")


# ----------------------------------------------------------------------------- Erkennung
def v16_kind(blk: bytes, addr: int, v16_refs, v16_pkgs=()) -> str:
    """„voll“ (genau die V16-Bytes), „teilweise“ (abgebrochen geschrieben: jedes Nicht-0xFF-Byte bzw. jeder
    Nicht-0xFF-Block gleicht V16) oder „“ (keine V16-Bytes)"""
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
    """cur kann aus orig durch einen unterbrochenen Löschvorgang entstanden sein: nur Bits 0→1, und noch genug Bytes
    übrig, dass das kein Zufall neuer Daten ist"""
    if len(cur) != len(orig) or cur == BLANK:
        return False
    nz = [i for i, b in enumerate(cur) if b != 0xFF]
    return len(nz) >= RESUME_MIN_BYTES and all(cur[i] & orig[i] == orig[i] for i in nz)


@dataclass
class Plan:
    ok: bool = False                                  # darf überhaupt gelöscht werden?
    reason: str = ""                                  # warum nicht (Alltagssprache)
    firmware: str = ""
    family: str = ""
    app_sha256: str = ""
    sectors: list = field(default_factory=list)       # [{"addr", "kind", "orig_file", "orig_sha256"}]
    skipped: list = field(default_factory=list)       # [{"addr", "why"}]
    already_clean: int = 0                            # leere Sektoren in der Zone

    def addrs(self) -> list[int]:
        return [s["addr"] for s in self.sectors]


def make_plan(dump: bytes, refs, v16_pkgs=(), journal: dict | None = None, backup_path: str = "",
              jread=None) -> Plan:
    """Aufräum-Plan aus einem frisch gelesenen 1-MiB-Dump. journal: unfertiges Journal eines früheren Laufs
    (für halb gelöschte Sektoren); jread(pfad) -> bytes liest dessen Sicherung. Wirft nie; Plan.ok sagt, ob gelöscht
    werden darf."""
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


# ----------------------------------------------------------------------------- Journal
def load_journal(path: Path) -> dict | None:
    try:
        d = json.loads(Path(path).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and d.get("schema") == 1 else None


def save_journal(path: Path, data: dict) -> None:
    """atomar: neue Datei schreiben, fsync, umbenennen, Ordner fsync"""
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


# ----------------------------------------------------------------------------- Ablauf am Gerät
def _unbricker():
    """vendor/unbricker/fm1_unbrick.py (unverändert) – neben diesem Skript (root-Kopie) oder im Quellbaum"""
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
    """am geöffneten Gerät (fm1_unbrick.Fm1Device, echt oder Mock): sichern, planen, löschen, prüfen -> Ergebnis.
    only: nur diese (vom Nutzer bestätigten) Sektoren – geschnitten mit dem frischen Plan.
    abort_after: nur Mock – nach so vielen Löschbefehlen einen halb gelöschten Sektor und Abbruch simulieren."""
    log = fu.log
    if abort_after is not None and not dev.mock:
        raise Refused("Absturz-Simulation gibt es nur mit dem Mock-FM-1.")
    journal_path = Path(journal_path or outdir / JOURNAL_NAME)
    res = {"schema": 1, "tool": "restzone", "untested": UNTESTED, "mock": bool(dev.mock), "plan_only": plan_only,
           "zone": [fm.hx(ZONE[0]), fm.hx(ZONE[1] - 1)], "erased": [], "skipped": [], "ok": False, "level": "rot"}

    def finish(**kw):
        res.update(kw)
        return res

    dev.session = None                                # jede Ausführung ist eine eigene Sitzung mit eigener Sicherung
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
    # von oben nach unten: Löscht der Chip wider Erwarten einen ganzen 64-KiB-Block, trifft der erste (geprüfte) Fehlgriff
    # den Block 0xA0000–0xAFFFF (Reste + gesicherte Daten) und nie das Firmware-Ende 0x90000–0x92FFF; danach hält die Blockprüfung an.
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

    for s in todo:                                   # alle Tore vorab, bevor irgendetwas gelöscht wird
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
                    blk = dev.ldr.flash[addr:addr + SECTOR]          # Mock: halb gelöscht stehen lassen
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


# ----------------------------------------------------------------------------- Bericht (Alltagssprache)
def report(res: dict):
    """Ergebnis-Datei des Laufs -> flashmodel.Report mit Ampel"""
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


# ----------------------------------------------------------------------------- Demo
def demo_image() -> bytes:
    """Mock-Flash für die Simulation: Demo-Melodee mit V16-Resten 0x97000–0xA8FFF, ein Sektor halb geschrieben,
    davor Nutzerdaten in 0x93000–0x96FFF"""
    d = bytearray(fm.demo_dumps()["demo-v16-reste.bin"])
    d[0x97000:0x97800] = b"\xff" * 0x800            # abgebrochener Update: die unteren Blöcke fehlen
    return bytes(d)


# ----------------------------------------------------------------------------- Kommandozeile
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
            if dev.mock and args.mock_image:          # Simulation: der Mock-Flash überlebt als Datei bis zum nächsten Lauf
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
