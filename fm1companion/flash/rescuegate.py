# SPDX-License-Identifier: GPL-3.0-only
"""Locks for rescue stage 2 (ROM boot mode), checked against the backup from the SAME tool run.

The bundled MvaveFM1Unbricker (vendor/unbricker/fm1_unbrick.py, unmodified) only knows the V15 layout:
  * `repair-settings` always resets 0x93000–0xE8FFF and 0xFC000–0xFEFFF. With V16, 0x93000–0xA8FFF still holds
    program code (end of app.bin, DX7.bin), plus VM 0xBA000–0xD9FFF, SEQ64 and USER [Pkg: V16 FM-1_016; m-flasher
    docs/development.md; felucca-bsp tools/fm1_rescue.py]. On a V16 device it would overwrite the firmware;
    it only warns on Felucca markers.
  * `restore` always writes stock V15 to 0x4000–0x92FFF, even under a V16 boot header. M-UPGRADE rewrites
    the header when installing V16 (felucca-bsp tools/fm1_rescue.py: "each version brings its own
    bootloader"; the V16 header places isd_config at 0x3938 instead of 0x38D0, m-flasher). Whether V15 boots under it is
    open – which is why fm1_rescue.py picks the package by the header and otherwise writes nothing.

This file runs as root (uboot.Runner → ROOT_HELPER, verified copy, checksum in uboot_manifest.OWN) and uses
the Unbricker as a library: its `take_backup` (whole flash read twice, compared, saved to a file and
read back) gets a check after it. The check runs AFTER the backup exists and BEFORE the Unbricker erases or writes
anything (in both commands the backup is step 3, writing only starts at step 5/6). If something does not
match, the run aborts with SafetyError – nothing has been written. All of the Unbricker's gates still apply.

Rules (refuse when in doubt):
  repair-settings – only if the fresh backup PROVES the V15 layout:
    a) app area byte-identical to a known package with V15 layout (stock V15, Melodee, SLOOP, Skiff, JIANT …)
       and exactly 0x93000 long, or
    b) app area = a known factory image (flashmodel.FACTORY_APPS) and the file table is valid, or
    c) the firmware's own file table (header and file CRCs verified) says: firmware ends at 0x93000, VM starts
       there, no SEQ64 (flashmodel.directory_layout == "v15-layout").
    Otherwise (V16, truncated V16, V13/V14 with 0x94000, damaged, unknown): refused.
  restore – only under the factory boot header (V13/V14/V15, d9f43191…). V16 header: refused (this tool does not
    write the V16 app: the Unbricker only writes 0x4000–0x92FFF). Unknown header (m-bootloader, community header …):
    refused. Nobody writes the header here.

UNTESTED on a real device (only with the Unbricker's mock FM-1); the check itself only reads the backup.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if __package__:
    from . import flashmodel as fm
else:                                   # as a script: flashmodel.py sits next to it (source tree or verified root copy)
    sys.path.insert(0, str(HERE))
    import flashmodel as fm             # noqa: E402

EXIT_REFUSED = 10                       # like restzone: rule violated, nothing written
COMMANDS = ("restore", "repair-settings")
ALTERNATIVE = ("Sicherer Weg: M-Flasher (github.com/jvitkauskas/m-flasher, liest die Flash-Karte aus der Firmware) "
               "oder fm1_rescue.py aus felucca-bsp (wählt das Paket passend zum Bootkopf und schreibt sonst nichts).")


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


# ----------------------------------------------------------------------------- the checks (pure functions)
def repair_verdict(dump: bytes, refs) -> dict:
    """may repair-settings (0x93000–0xE8FFF, 0xFC000–0xFEFFF) write to this flash? -> {"ok", "reason",
    "firmware", "basis"}. Never raises."""
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
    """may stock V15 (0x4000–0x92FFF) be written under this flash's boot header? -> {"ok", "reason",
    "head"}. Never raises."""
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


# ----------------------------------------------------------------------------- run
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
    """device: tests only – a ready fm1_unbrick.Fm1Device (mock), like fm1_unbrick.main(device=…)"""
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
        side = HERE / "known_packages.json"            # root copy: next to the script; source tree: data/
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
