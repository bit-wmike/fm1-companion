# SPDX-License-Identifier: GPL-3.0-only
"""Sperren für die Rettung Stufe 2 (ROM-Bootmodus), geprüft an der Sicherung aus DEMSELBEN Werkzeuglauf.

Der mitgelieferte MvaveFM1Unbricker (vendor/unbricker/fm1_unbrick.py, unverändert) kennt nur den V15-Aufbau:
  * `repair-settings` setzt fest 0x93000–0xE8FFF und 0xFC000–0xFEFFF zurück. Bei V16 liegt in 0x93000–0xA8FFF noch
    Programmcode (Ende von app.bin, DX7.bin), dazu VM 0xBA000–0xD9FFF, SEQ64 und USER [Paket: V16 FM-1_016; m-flasher
    docs/development.md; felucca-bsp tools/fm1_rescue.py]. Auf einem V16-Gerät würde er die Firmware überschreiben;
    er warnt nur bei Felucca-Markern.
  * `restore` schreibt immer die Original-V15 nach 0x4000–0x92FFF, auch unter einem V16-Bootkopf. M-UPGRADE schreibt
    den Kopf beim Installieren von V16 neu (felucca-bsp tools/fm1_rescue.py: „each version brings its own
    bootloader“; der V16-Kopf legt isd_config bei 0x3938 statt 0x38D0 ab, m-flasher). Ob V15 darunter startet, ist
    offen – fm1_rescue.py wählt darum das Paket nach dem Kopf und schreibt sonst nichts.

Diese Datei läuft als root (uboot.Runner → ROOT_HELPER, geprüfte Kopie, Prüfsumme in uboot_manifest.OWN) und benutzt
den Unbricker als Bibliothek: Sein `take_backup` (ganzer Flash zweimal gelesen, verglichen, als Datei gesichert und
rückgelesen) bekommt eine Prüfung dahinter. Sie läuft, NACHDEM die Sicherung liegt und BEVOR der Unbricker irgendetwas
löscht oder schreibt (in beiden Befehlen ist die Sicherung Schritt 3, geschrieben wird erst ab Schritt 5/6). Passt etwas
nicht, bricht der Lauf mit SafetyError ab – es wurde nichts geschrieben. Alle Tore des Unbrickers gelten weiter.

Regeln (im Zweifel ablehnen):
  repair-settings – nur, wenn die frische Sicherung den V15-Aufbau BEWEIST:
    a) App-Bereich byte-gleich mit einem bekannten Paket mit V15-Aufbau (Original-V15, Melodee, SLOOP, Skiff, JIANT …)
       und genau 0x93000 lang, oder
    b) App-Bereich = ein bekanntes Werks-Abbild (flashmodel.FACTORY_APPS) und die Dateitabelle stimmt, oder
    c) die Dateitabelle der Firmware selbst (Kopf- und Datei-CRCs geprüft) sagt: Firmware endet bei 0x93000, VM beginnt
       dort, kein SEQ64 (flashmodel.directory_layout == „v15-layout“).
    Sonst (V16, abgeschnittenes V16, V13/V14 mit 0x94000, beschädigt, unbekannt): abgelehnt.
  restore – nur unter dem Bootkopf ab Werk (V13/V14/V15, d9f43191…). V16-Kopf: abgelehnt (die V16-App schreibt dieses
    Werkzeug nicht: der Unbricker schreibt nur 0x4000–0x92FFF). Unbekannter Kopf (m-bootloader, Community-Kopf …):
    abgelehnt. Den Kopf schreibt hier niemand.

Am echten Gerät UNGETESTET (nur mit dem Mock-FM-1 des Unbrickers); die Prüfung selbst liest nur die Sicherung.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if __package__:
    from . import flashmodel as fm
else:                                   # als Skript: flashmodel.py liegt daneben (Quellbaum oder geprüfte root-Kopie)
    sys.path.insert(0, str(HERE))
    import flashmodel as fm             # noqa: E402

EXIT_REFUSED = 10                       # wie restzone: Regel verletzt, nichts geschrieben
COMMANDS = ("restore", "repair-settings")
ALTERNATIVE = ("Sicherer Weg: M-Flasher (github.com/jvitkauskas/m-flasher, liest die Flash-Karte aus der Firmware) "
               "oder fm1_rescue.py aus felucca-bsp (wählt das Paket passend zum Bootkopf und schreibt sonst nichts).")


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


# ----------------------------------------------------------------------------- die Prüfungen (reine Funktionen)
def repair_verdict(dump: bytes, refs) -> dict:
    """darf repair-settings (0x93000–0xE8FFF, 0xFC000–0xFEFFF) auf diesem Flash schreiben? -> {"ok", "reason",
    "firmware", "basis"}. Wirft nie."""
    v = {"ok": False, "reason": "", "firmware": "", "basis": ""}
    if len(dump) != fm.FLASH_SIZE:
        v["reason"] = "Die Sicherung hat nicht genau 1 MiB – so wird nichts geschrieben."
        return v
    app = fm._app_match(dump, refs)
    pre = fm.sha(dump[fm.APP_START:fm.V15_END])
    v16_ref = next((k for k in refs if k.family == "v16-layout" and k.app_prefix.get(fm.V15_END) == pre), None)
    if app is not None:
        v["firmware"] = app.name
        if app.family == "v15-layout" and app.flash_bytes == fm.V15_END:
            v.update(ok=True, basis="bekanntes Paket mit V15-Aufbau")
            return v
        v["reason"] = (f"Auf dem FM-1 steht {app.name} mit V16-Aufbau: Dort gehört 0x93000–0xA8FFF noch zur Firmware "
                       "(Ende des Programms und die Werks-Voices DX7.bin), die Einstellungen liegen woanders "
                       "(0xBA000 ff.). „Einstellungen reparieren“ ist nur für den V15-Aufbau gebaut und würde die "
                       "Firmware überschreiben. Es wurde nichts geschrieben.")
        return v
    if v16_ref is not None:
        v["firmware"] = f"{v16_ref.name}, abgeschnitten"
        v["reason"] = ("Im App-Bereich steht der Anfang von V16 (abgeschnitten). Damit ist der Aufbau nicht der von V15 "
                       "– „Einstellungen reparieren“ hilft hier nicht und schreibt nichts. Erst die Original-V15 "
                       "zurückschreiben, dann neu sichern.")
        return v
    st = fm.app_directory(dump)
    lay = fm.directory_layout(st)
    factory = fm.FACTORY_APPS.get(pre)
    if lay == "v15-layout":
        v["firmware"] = factory or "Firmware ohne Vorlage (Dateitabelle geprüft)"
        v.update(ok=True, basis=("bekanntes Werks-Abbild" if factory else "") + (", " if factory else "")
                 + "Dateitabelle der Firmware: Ende 0x93000, Einstellungen ab 0x93000, alle Prüfsummen stimmen")
        return v
    if lay == "v16-layout":
        v["firmware"] = "Firmware mit V16-Aufbau (laut ihrer Dateitabelle)"
        v["reason"] = (f"Die Dateitabelle der Firmware im Flash sagt: Sie reicht bis {fm.hx((st['prct_end'] or 0) - 1)}"
                       + (" und hat einen SEQ64-Bereich" if st["seq64"] else "") + " – das ist der V16-Aufbau. Dort "
                       "gehört 0x93000–0xA8FFF zur Firmware. „Einstellungen reparieren“ würde sie überschreiben. Es "
                       "wurde nichts geschrieben.")
        return v
    if st["ok"]:
        v["reason"] = (f"Die Firmware im Flash hat einen Aufbau, für den das Werkzeug nicht gebaut ist (Ende "
                       f"{fm.hx((st['prct_end'] or 0) - 1)}, Einstellungen ab "
                       f"{fm.hx(st['vm'][0]) if st['vm'] else 'unbekannt'}; z. B. V13/V14). Im Zweifel schreibt die "
                       "App nichts.")
    elif st["found"]:
        v["reason"] = ("Die Firmware im Flash ist beschädigt (" + "; ".join(st["errors"]) + "). Dann lässt sich nicht "
                       "sicher sagen, wo sie endet – es wurde nichts geschrieben. Zuerst die Original-V15 "
                       "zurückschreiben.")
    else:
        v["reason"] = ("Die Firmware im Flash ist keine, die die App kennt, und ihre Dateitabelle ist nicht lesbar. Dann "
                       "lässt sich nicht beweisen, dass 0x93000–0xE8FFF nur Einstellungen enthält – es wurde nichts "
                       "geschrieben.")
    return v


def restore_verdict(dump: bytes, refs, factory_heads=None, heads: dict | None = None) -> dict:
    """darf die Original-V15 (0x4000–0x92FFF) unter dem Bootkopf dieses Flashs geschrieben werden? -> {"ok", "reason",
    "head"}. Wirft nie."""
    v = {"ok": False, "reason": "", "head": ""}
    if len(dump) != fm.FLASH_SIZE:
        v["reason"] = "Die Sicherung hat nicht genau 1 MiB – so wird nichts geschrieben."
        return v
    hs = fm.sha(dump[:fm.HEAD_END])
    heads = dict(heads or {})
    v16_heads = {k.head_sha256 for k in refs if k.family == "v16-layout"}
    if hs in (factory_heads or {fm.FACTORY_HEAD_SHA256}):
        v.update(ok=True, head="Bootkopf ab Werk (V13–V15)" if hs == fm.FACTORY_HEAD_SHA256 else
                 "Demo-Bootkopf (Simulation)")
        return v
    if hs in v16_heads or "V16" in heads.get(hs, ""):
        v["head"] = "Bootkopf aus V16"
        v["reason"] = ("Die Sicherung zeigt den Bootkopf von V16 (" + hs[:16] + "…). M-VAVEs Updater schreibt beim "
                       "Installieren von V16 einen eigenen, größeren Bootlader. Ob die Original-V15 darunter startet, "
                       "ist nicht geklärt, und die V16-App (0x4000–0xA8FFF) kann dieses Werkzeug nicht schreiben – es "
                       "schreibt nur bis 0x92FFF. Den Bootkopf schreibt die App nie. Darum wurde nichts geschrieben. "
                       + ALTERNATIVE)
        return v
    v["head"] = heads.get(hs, "unbekannt")
    v["reason"] = (f"Den Bootkopf in der Sicherung ({hs[:16]}…, {v['head']}) kennt die App nicht als den ab Werk. Ob "
                   "die Original-V15 darunter startet, kann sie nicht sagen (z. B. nach m-bootloader oder einem "
                   "Schreiben des ganzen Flashs). Im Zweifel wurde nichts geschrieben. " + ALTERNATIVE)
    return v


# ----------------------------------------------------------------------------- Lauf
def build_parser():
    ap = argparse.ArgumentParser(prog="rescuegate.py", add_help=True, allow_abbrev=False,
                                 description="Unbricker restore / repair-settings mit Prüfung der frischen Sicherung")
    ap.add_argument("command", choices=COMMANDS)
    ap.add_argument("--result", required=True, help="Ergebnis als JSON hierhin")
    ap.add_argument("--refs", default="", help="known_packages.json (Standard: neben dem Skript bzw. data/)")
    ap.add_argument("--demo-refs", action="store_true", help="Demo-Firmwares und Demo-Kopf dazunehmen (nur --dry-run)")
    return ap


FORBIDDEN_ARGS = ("--i-know", "--force-write", "--mock-chip-key")


def main(argv=None, device=None) -> int:
    """device: nur Tests – ein fertiges fm1_unbrick.Fm1Device (Mock), wie fm1_unbrick.main(device=…)"""
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = build_parser()
    args, rest = ap.parse_known_args(argv)
    res: dict = {"schema": 1, "command": args.command, "ok": False, "checked": False}
    rc = 1
    try:
        dry = "--dry-run" in rest
        if any(a.split("=")[0] in FORBIDDEN_ARGS for a in rest):
            raise ValueError("Diese Option gibt es über die App nicht.")
        if args.demo_refs and not dry:
            raise ValueError("Demo-Optionen gibt es nur in der Simulation.")
        side = HERE / "known_packages.json"            # root-Kopie: neben dem Skript; Quellbaum: data/
        refs_file = args.refs or (side if side.is_file() else None)
        refs = fm.load_known(refs_file)
        heads = fm.known_heads(refs_file)
        fheads = {fm.FACTORY_HEAD_SHA256}
        if args.command == "restore" and "--verify-v15" not in rest:
            raise ValueError("Im ROM-Bootmodus schreibt die App nur die geprüfte Original-V15 (--verify-v15).")
        if args.demo_refs:
            refs += fm.demo_refs()
            fheads |= fm.demo_heads()
    except (ValueError, OSError) as e:
        res["refused"] = f"{e} Es wurde nichts geschrieben."
        _write(args.result, res)
        print("ABGELEHNT: " + res["refused"], flush=True)
        return EXIT_REFUSED

    fu = _unbricker()
    orig = fu.take_backup

    def take_backup(dev, outdir, packages=()):
        path, data = orig(dev, outdir, packages)
        if args.command == "repair-settings":
            v = repair_verdict(data, refs)
        else:
            v = restore_verdict(data, refs, fheads, heads)
        res.update(checked=True, verdict=v, backup=str(path))
        if not v["ok"]:
            res["refused"] = v["reason"]
            raise fu.SafetyError("ABGELEHNT (Suite-Prüfung der Sicherung): " + v["reason"], EXIT_REFUSED)
        fu.log.info("Suite-Prüfung der Sicherung: OK – %s", v.get("basis") or v.get("head") or "")
        return path, data

    fu.take_backup = take_backup
    try:
        rc = fu.main([args.command] + rest, device=device)
    finally:
        fu.take_backup = orig
    res["rc"] = rc
    res["ok"] = rc == 0 and res["checked"] and "refused" not in res
    if rc == 0 and not res["checked"]:
        res["note"] = "Ohne Sicherung beendet – es wurde nichts geschrieben."
    _write(args.result, res)
    return EXIT_REFUSED if "refused" in res and rc != 0 else rc


def _write(path, res):
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(res, indent=1, ensure_ascii=False), "utf-8")
    except OSError as e:
        print(f"Ergebnis-Datei nicht schreibbar: {e}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
