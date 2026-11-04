# SPDX-License-Identifier: GPL-3.0-only
"""Texts of the pinned rescue files in the UI language (display layer only).

flashmodel.py, restzone.py and rescuegate.py are pinned by checksum (uboot_manifest.OWN) and partly run with root
rights through a helper that copies only pinned files – they stay exactly as they are, with their German messages,
and import nothing from the rest of the app. This module translates what they produce, at the place where the UI
shows it: findings, notes and details of a report, refusal reasons, results and the lines of their log.

How: an ordered table of rules. Each rule is the German template of one message from those files (``{name}`` marks
a variable part) and its English source text (marked with ``N_``/``NP_``). The German template becomes a regular
expression; on a match the English text is translated with ``_()`` and filled with the variable parts (which are
translated themselves, recursively). With German as the UI language the catalog ``locale/de/rescue.po`` gives back
the original German template, so the shown text is byte for byte what the pinned file produced.

Never hide a safety message: text that no rule matches – or anything that goes wrong while translating – is shown
unchanged. tests/test_rescue_texts.py checks that every German text of the three files is covered and that the
German round trip is exact.

Nothing here runs as root and nothing here is pinned.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field

from ..i18n import DECIMAL_COMMA, N_, NP_, _, current_language, ngettext

HEX = r"0x[0-9A-F]+"
SHA = r"[0-9a-f]+"


@dataclass
class Rule:
    de: str                                   # German template from the pinned file ({name} = variable part)
    en: str | tuple                           # English source text, or (singular, plural) with n
    n: str = ""                               # plural: name of the count placeholder
    pat: dict = field(default_factory=dict)   # name -> regex of the variable part (default: any text)
    kind: dict = field(default_factory=dict)  # name -> how the value is translated (see _value)
    rx: re.Pattern | None = None
    prefix: str = ""                          # literal text before the first placeholder (quick pre-check)


def R(de: str, en, n: str = "", pat: dict | None = None, kind: dict | None = None) -> Rule:
    return Rule(de, en, n, dict(pat or {}), dict(kind or {}))


_KIND = {"voll": N_("full"), "teilweise": N_("partial"), "unterbrochen": N_("interrupted")}  # i18n: ignore
_YESNO = {"ja": N_("yes"), "nein": N_("no")}  # i18n: ignore
_HEAD_OK = {"Kopf ok": N_("header ok"), "Kopf fehlt/defekt": N_("header missing/defective")}  # i18n: ignore
_PAYLOAD_OK = {"ok": N_("ok"), "nicht ok": N_("not ok")}  # i18n: ignore
_LAYOUTS = r"V15-Aufbau \(Firmware bis 0x92FFF\)|V16-Aufbau \(Firmware über 0x93000 hinaus\)|Aufbau ohne Vorlage"  # i18n: ignore
_FILES = r"(?:, [^,]+? \d[\d.]* Bytes)*"
_FILE_ITEM = re.compile(r", (?P<name>[^,]+?) (?P<size>\d[\d.]*) Bytes")  # i18n: ignore
_ALT = r"Sicherer Weg: .+"  # i18n: ignore


def _num(v: str) -> str:
    """'1.048.576' (German thousands separator, as the pinned files write it) in the UI language"""
    return v if current_language() in DECIMAL_COMMA else v.replace(".", ",")


def _files(v: str) -> str:
    return "".join(", " + _("{name} {size} bytes").format(name=m["name"], size=_num(m["size"]))
                   for m in _FILE_ITEM.finditer(v))


# ====================================================================== the table (order matters: specific first)
RULES: list[Rule] = [
    # ---------------------------------------------------------------- names and short labels (exact)
    R("Bootkopf", N_("boot header")),  # i18n: ignore
    R("key_mac / reservierter Rest", N_("key_mac / reserved rest")),  # i18n: ignore
    R("Bluetooth-Daten (V15-Aufbau)", N_("Bluetooth data (V15 layout)")),  # i18n: ignore
    R("Bluetooth-Daten (V16-Aufbau)", N_("Bluetooth data (V16 layout)")),  # i18n: ignore
    R("Werk (V13–V15)", N_("factory (V13–V15)")),  # i18n: ignore
    R("unbekannt", N_("unknown")),  # i18n: ignore
    R("beschädigt", N_("damaged")),  # i18n: ignore
    R("am echten Gerät ungetestet", N_("untested on a real device")),  # i18n: ignore
    R("Sauber", N_("Clean")),  # i18n: ignore
    R("Mit Hinweisen", N_("With notes")),  # i18n: ignore
    R("Nicht in Ordnung", N_("Not OK")),  # i18n: ignore
    R("Schnellprüfung (nur lesen)", N_("Quick check (read only)")),  # i18n: ignore
    R("Flash-Dump-Prüfung", N_("Flash dump check")),  # i18n: ignore
    R("Reste aufräumen – Plan (Simulation)", N_("Clean up leftovers – plan (simulation)")),  # i18n: ignore
    R("Reste aufräumen – Plan", N_("Clean up leftovers – plan")),  # i18n: ignore
    R("Reste aufräumen (Simulation)", N_("Clean up leftovers (simulation)")),  # i18n: ignore
    R("Reste aufräumen", N_("Clean up leftovers")),  # i18n: ignore
    R("Werks-Abbild der Original-Firmware V15 (FM-1_015, an einem Gerät gemessen)",  # i18n: ignore
      N_("factory image of the original firmware V15 (FM-1_015, measured on one device)")),
    R("Demo-Melodee (Simulation)", N_("Demo Melodee (simulation)")),  # i18n: ignore
    R("Demo-V16 (Simulation)", N_("Demo V16 (simulation)")),  # i18n: ignore
    R("V15-Aufbau (Firmware bis 0x92FFF)", N_("V15 layout (firmware up to 0x92FFF)")),  # i18n: ignore
    R("V16-Aufbau (Firmware über 0x93000 hinaus)", N_("V16 layout (firmware beyond 0x93000)")),  # i18n: ignore
    R("Aufbau ohne Vorlage", N_("layout without a reference")),  # i18n: ignore
    R("Firmware ohne Vorlage (Dateitabelle geprüft)", N_("firmware without a reference (file table checked)")),  # i18n: ignore
    R("Firmware mit V16-Aufbau (laut ihrer Dateitabelle)",  # i18n: ignore
      N_("firmware with the V16 layout (according to its file table)")),
    R("bekanntes Paket mit V15-Aufbau", N_("known package with the V15 layout")),  # i18n: ignore
    R("bekanntes Werks-Abbild, Dateitabelle der Firmware: Ende 0x93000, Einstellungen ab 0x93000, alle Prüfsummen "  # i18n: ignore
      "stimmen",
      N_("known factory image, file table of the firmware: end 0x93000, settings from 0x93000, all checksums match")),
    R("Dateitabelle der Firmware: Ende 0x93000, Einstellungen ab 0x93000, alle Prüfsummen stimmen",  # i18n: ignore
      N_("file table of the firmware: end 0x93000, settings from 0x93000, all checksums match")),
    R("Bootkopf ab Werk (V13–V15)", N_("factory boot header (V13–V15)")),  # i18n: ignore
    R("Demo-Bootkopf (Simulation)", N_("demo boot header (simulation)")),  # i18n: ignore
    # names from data/known_packages.json (pinned as well)
    R("Original-Firmware V15", N_("Original firmware V15")),  # i18n: ignore
    R("Original-Firmware V16", N_("Original firmware V16")),  # i18n: ignore
    R("Bootkopf ab Werk (V13, V14, V15)", N_("factory boot header (V13, V14, V15)")),  # i18n: ignore
    R("Bootkopf der Community-Pakete (Melodee, SLOOP, Skiff, JIANT)",  # i18n: ignore
      N_("boot header of the community packages (Melodee, SLOOP, Skiff, JIANT)")),
    # Melodee memory map (flashmodel.MELODEE_MAP) and the loss descriptions
    R("Prophet-Bänke", N_("Prophet banks")),  # i18n: ignore
    R("gemeinsame Preset-Erweiterung", N_("shared preset extension")),  # i18n: ignore
    R("FM6-Bank (Kopie A)", N_("FM6 bank (copy A)")),  # i18n: ignore
    R("Projekte und Preset-Bänke (Pool)", N_("projects and preset banks (pool)")),  # i18n: ignore
    R("CZ-Bänke", N_("CZ banks")),  # i18n: ignore
    R("Ressourcen (Werkssounds, Schrift, Bluetooth-Programm)",  # i18n: ignore
      N_("resources (factory sounds, font, Bluetooth program)")),
    R("Update-Zwischenspeicher", N_("update buffer")),  # i18n: ignore
    R("Erweiterungen und Projekte (Pool)", N_("extensions and projects (pool)")),  # i18n: ignore
    R("Einstellungen", N_("settings")),  # i18n: ignore
    R("Songs, Presets und Einstellungen", N_("songs, presets and settings")),  # i18n: ignore
    R("weitere Song- und Preset-Bereiche", N_("further song and preset areas")),  # i18n: ignore

    # ---------------------------------------------------------------- flashmodel.py: package, file table
    R("{name} lässt sich nicht lesen ({error}).", N_("{name} cannot be read ({error})."), kind={"error": "raw"}),  # i18n: ignore
    R("zu kurz für ein FM-1-Paket", N_("too short for an FM-1 package")),  # i18n: ignore
    R("Paketkopf-Prüfsumme falsch", N_("package header checksum wrong")),  # i18n: ignore
    R("Dateiliste des Pakets beschädigt", N_("file list of the package damaged")),  # i18n: ignore
    R("kein flash.bin im Paket", N_("no flash.bin in the package")),  # i18n: ignore
    R("keine Ressourcen-Antwort", N_("no resource answer")),  # i18n: ignore
    R("zu kurz", N_("too short")),  # i18n: ignore
    R("kein gültiger Eintrag „app_area_head“ bei 0x04000", N_("no valid entry “app_area_head” at 0x04000")),  # i18n: ignore
    R("kein Eintrag „app_area_head“ bei 0x04000", N_("no entry “app_area_head” at 0x04000")),  # i18n: ignore
    R("app_area_head nennt eine unmögliche Länge 0x{size}", N_("app_area_head states an impossible length 0x{size}"),  # i18n: ignore
      kind={"size": "raw"}),
    R("{name} liegt außerhalb des App-Bereichs", N_("{name} lies outside the app area")),  # i18n: ignore
    R("Prüfsumme (CRC) von {name} stimmt nicht", N_("checksum (CRC) of {name} does not match")),  # i18n: ignore
    R("keine Datei app.bin in der Tabelle", N_("no file app.bin in the table")),  # i18n: ignore
    R("kein Eintrag PRCT (Ende der Firmware)", N_("no entry PRCT (end of the firmware)")),  # i18n: ignore
    R("Tabelle nicht lesbar ({error})", N_("table not readable ({error})"), kind={"error": "raw"}),  # i18n: ignore
    R("Aufräumen würde den Bereich „{why}“ berühren", N_("Cleaning up would touch the area “{why}”")),  # i18n: ignore

    # ---------------------------------------------------------------- flashmodel.py: preflight
    R("Der Dateiname sagt V{v}, das Paket selbst ist {identity}.",  # i18n: ignore
      N_("The file name says V{v}, the package itself is {identity}.")),
    R("Das Paket steht nicht in der Liste bekannter Pakete.", N_("The package is not in the list of known packages.")),  # i18n: ignore
    R("Kein Backup des jetzigen Stands.", N_("No backup of the current state.")),  # i18n: ignore
    R("Layoutwechsel {old} → {new}: schreibt bis {end}, die Datenbereiche liegen danach woanders.",  # i18n: ignore
      N_("Layout change {old} → {new}: writes up to {end}, the data areas are elsewhere afterwards."),
      kind={"old": "raw", "new": "raw"}),
    R("Das Ziel schreibt {start}–{end}, das die jetzige Firmware als Datenspeicher nutzt.",  # i18n: ignore
      N_("The target writes {start}–{end}, which the current firmware uses as data storage."),
      pat={"start": HEX, "end": HEX}),
    R("Der Bootkopf bleibt der alte (nur im ROM-Bootmodus relevant).",  # i18n: ignore
      N_("The boot header stays the old one (only relevant in ROM boot mode).")),
    R("Dieser Schreibweg schreibt nur 0x04000–0x92FFF; das Paket braucht bis {end}.",  # i18n: ignore
      N_("This write path only writes 0x04000–0x92FFF; the package needs up to {end}.")),

    # ---------------------------------------------------------------- flashmodel.py: quick check
    R("Das FM-1 hat seine Kennung nicht verraten – die App kann die Firmware nicht zuordnen.",  # i18n: ignore
      N_("The FM-1 did not reveal its identity – the app cannot assign the firmware.")),
    R("Das FM-1 meldet {identity} ({firmware}) – eine Firmware, die die App kennt.",  # i18n: ignore
      N_("The FM-1 reports {identity} ({firmware}) – a firmware the app knows.")),
    R("Das FM-1 meldet {identity} ({firmware}). Diese Kennung steht nicht in der Liste bekannter Pakete – das heißt "  # i18n: ignore
      "nicht, dass etwas kaputt ist, die App kann sie nur nicht gegenprüfen.",
      N_("The FM-1 reports {identity} ({firmware}). This identity is not in the list of known packages – that does not "
         "mean that something is broken, the app just cannot cross-check it.")),
    R("Die Ressourcen (Werkssounds, Schrift, Bluetooth-Programm) sind vollständig und passen zur laufenden Firmware. "  # i18n: ignore
      "Die Firmware selbst ist aber ein anderer Build als der, den die App unter dieser Version kennt – die App kann "
      "sie darum nicht gegenprüfen.",
      N_("The resources (factory sounds, font, Bluetooth program) are complete and match the running firmware. The "
         "firmware itself, however, is a different build from the one the app knows under this version – so the app "
         "cannot cross-check it.")),
    R("Ressourcen (Werkssounds, Schrift, Bluetooth-Programm) sind vollständig: Die Firmware hat Kopf und Prüfsumme "  # i18n: ignore
      "selbst nachgerechnet, sie passen zu genau diesem Build.",
      N_("Resources (factory sounds, font, Bluetooth program) are complete: the firmware recomputed header and "
         "checksum itself, they match exactly this build.")),
    R("Die Ressourcen fehlen oder sind beschädigt – dann fehlen Werkssounds oder die Schrift sieht anders aus. "  # i18n: ignore
      "Abhilfe: Seite „Firmware“ › „Schrift & Werkssounds nachschreiben“.",
      N_("The resources are missing or damaged – then factory sounds are missing or the font looks different. "
         "Remedy: page “Firmware” › “Rewrite font & factory sounds”.")),
    R("Befehl 82: installiert={installed}, Länge {length}, CRC-32 {crc} (bekannt: {known})",  # i18n: ignore
      N_("Command 82: installed={installed}, length {length}, CRC-32 {crc} (known: {known})"),
      pat={"installed": "ja|nein"}, kind={"installed": _YESNO, "length": "raw", "crc": "raw", "known": "raw"}),  # i18n: ignore
    R("Befehl 82: installiert={installed}, Länge {length}, CRC-32 {crc}",  # i18n: ignore
      N_("Command 82: installed={installed}, length {length}, CRC-32 {crc}"),
      pat={"installed": "ja|nein"}, kind={"installed": _YESNO, "length": "raw", "crc": "raw"}),  # i18n: ignore
    R("Ressourcen-Status nicht abfragbar: {error}", N_("Resource status cannot be queried: {error}")),  # i18n: ignore
    R("Ohne Dump sieht die App nicht in den Flash: Reste früherer Firmwares (z. B. von V16), der Bootkopf und die "  # i18n: ignore
      "Datenbereiche bleiben ungeprüft.",
      N_("Without a dump the app does not see into the flash: leftovers of earlier firmwares (e.g. of V16), the boot "
         "header and the data areas remain unchecked.")),
    R("Ob jedes Byte der Firmware stimmt, prüft das FM-1 beim Start nur teilweise selbst – byte-genau geht es nur mit "  # i18n: ignore
      "einem Flash-Dump (Teil 2 dieser Prüfung).",
      N_("Whether every byte of the firmware is correct, the FM-1 checks itself only partly at startup – byte for byte "
         "it is only possible with a flash dump (part 2 of this check).")),
    R("Die Ressourcen-Prüfung gibt es nur bei Melodee ab 1.6.", N_("The resource check only exists with Melodee 1.6 and later.")),  # i18n: ignore

    # ---------------------------------------------------------------- flashmodel.py: dump check
    R("Die Datei ist {size} Bytes groß – ein vollständiger FM-1-Dump hat genau 1 MiB ({full} Bytes).",  # i18n: ignore
      N_("The file is {size} bytes in size – a complete FM-1 dump has exactly 1 MiB ({full} bytes)."),
      kind={"size": "num", "full": "num"}),
    R("Der Dump ist komplett leer – so sieht kein laufendes FM-1 aus. Wahrscheinlich ist beim Lesen etwas "  # i18n: ignore
      "schiefgegangen.",
      N_("The dump is completely empty – no running FM-1 looks like this. Something probably went wrong while "
         "reading.")),
    R("Bootkopf aus {name}", N_("boot header from {name}")),  # i18n: ignore
    R("Bootkopf 0x00000–0x03FFF: SHA-256 {sha}… ({name})", N_("Boot header 0x00000–0x03FFF: SHA-256 {sha}… ({name})"),  # i18n: ignore
      pat={"sha": SHA}),
    R("Der Bootkopf ist der ab Werk (gleich bei V13, V14, V15) – so soll es sein.",  # i18n: ignore
      N_("The boot header is the factory one (the same for V13, V14, V15) – that is how it should be.")),
    R("Das entsteht im ROM-Bootmodus, wenn der ganze Flash beschrieben wurde – oder vielleicht durch ein normales "  # i18n: ignore
      "Update auf V16: Ob der Original-Loader dabei den Kopf mitschreibt, ist noch nicht geklärt.",
      N_("This happens in ROM boot mode when the whole flash was written – or perhaps through a normal update to V16: "
         "whether the original loader also writes the header is not yet clarified.")),
    R("Das entsteht nur, wenn im ROM-Bootmodus der ganze Flash beschrieben wurde.",  # i18n: ignore
      N_("This only happens when the whole flash was written in ROM boot mode.")),
    R("Der Bootkopf ist nicht der ab Werk, sondern: {name}. {how} Melodee lehnt dann die Rückkehr zur Original-V15 ab "  # i18n: ignore
      "(„FAILED -5“).",
      N_("The boot header is not the factory one, but: {name}. {how} Melodee then refuses the return to the original "
         "V15 (“FAILED -5”)."),
      pat={"how": r"Das entsteht .+?"}),  # i18n: ignore
    R("Den Bootkopf kennt die App nicht (z. B. nach m-bootloader). Die Firmware kann trotzdem laufen; die Rückkehr zur "  # i18n: ignore
      "Original-Firmware kann dann „FAILED -5“ melden.",
      N_("The app does not know the boot header (e.g. after m-bootloader). The firmware can run anyway; the return to "
         "the original firmware can then report “FAILED -5”.")),
    R("{name}, abgeschnitten", N_("{name}, truncated")),  # i18n: ignore
    R("Im App-Bereich steht V16, aber nur bis 0x92FFF – der Rest (Ende des Programms und die 128 Werks-Voices, DX7.bin "  # i18n: ignore
      "ab 0x96267) fehlt. So schreibt nur ein Werkzeug für V15-große Pakete (z. B. der Unbricker im ROM-Bootmodus). "
      "Abhilfe: Original-V15 und danach deine Firmware neu aufspielen.",
      N_("The app area holds V16, but only up to 0x92FFF – the rest (end of the program and the 128 factory voices, "
         "DX7.bin from 0x96267) is missing. Only a tool for V15-sized packages writes like this (e.g. the unbricker in "
         "ROM boot mode). Remedy: install the original V15 and then your firmware again.")),
    R("App-Bereich 0x04000–0x92FFF = V16, 0x93000–0xA8FFF ≠ V16", N_("App area 0x04000–0x92FFF = V16, 0x93000–0xA8FFF ≠ V16")),  # i18n: ignore
    R("App-Bereich 0x04000–{end}: byte-gleich mit {name} ({sha}…)",  # i18n: ignore
      N_("App area 0x04000–{end}: byte-identical with {name} ({sha}…)"), pat={"end": HEX, "sha": SHA}),
    R("Im Flash steht {name} ({identity}), erwartet war {expected}.",  # i18n: ignore
      N_("The flash holds {name} ({identity}), {expected} was expected."), kind={"identity": "raw"}),
    R("Die Firmware im Flash ist byte-genau {name}.", N_("The firmware in the flash is exactly {name}, byte for byte.")),  # i18n: ignore
    R("Dateitabelle 0x04000: Kopf- und Datei-Prüfsummen stimmen, {layout}{files}",  # i18n: ignore
      N_("File table 0x04000: header and file checksums match, {layout}{files}"),
      pat={"layout": _LAYOUTS, "files": _FILES}, kind={"files": _files}),
    R("Dateitabelle 0x04000: FEHLER: {errors}, {layout}{files}",  # i18n: ignore
      N_("File table 0x04000: ERROR: {errors}, {layout}{files}"),
      pat={"layout": _LAYOUTS, "files": _FILES}, kind={"errors": "list", "files": _files}),
    R("Im App-Bereich steht ein {factory}. Es weicht vom veröffentlichten V15-Paket ab – das ist kein Fehler: Geräte ab "  # i18n: ignore
      "Werk tragen ein anderes Abbild. Die Dateitabelle der Firmware und ihre Prüfsummen stimmen. Nichts neu aufspielen; "
      "diese Sicherung ist dein Rückweg.",
      N_("The app area holds a {factory}. It differs from the published V15 package – that is not an error: devices "
         "from the factory carry a different image. The file table of the firmware and its checksums match. Do not "
         "reinstall anything; this backup is your way back.")),
    R("Werks-Abbild oder andere Version von {name}", N_("factory image or other version of {name}")),  # i18n: ignore
    R("Werks-Abbild oder andere Version (weicht vom veröffentlichten Paket {name} in {n} von {total} Sektoren ab) – "  # i18n: ignore
      "kein Fehler: Die Dateitabelle der Firmware und die Prüfsummen (CRC) aller ihrer Dateien stimmen, sie ist in sich "
      "vollständig. Geräte ab Werk tragen oft ein anderes Abbild als das veröffentlichte Paket. Nichts neu aufspielen.",
      N_("Factory image or other version (differs from the published package {name} in {n} of {total} sectors) – not "
         "an error: the file table of the firmware and the checksums (CRC) of all its files match, it is complete in "
         "itself. Devices from the factory often carry a different image than the published package. Do not reinstall "
         "anything."),
      pat={"n": r"\d+", "total": r"\d+"}),
    R("Abweichende Sektoren gegenüber {name}: {ranges}", N_("Differing sectors compared with {name}: {ranges}"),  # i18n: ignore
      kind={"ranges": "raw"}),
    R("{name} (beschädigt)", N_("{name} (damaged)")),  # i18n: ignore
    R("Die Firmware im App-Bereich ist beschädigt oder unvollständig geschrieben: {errors}. Diese Prüfsummen rechnet "  # i18n: ignore
      "die Firmware-Tabelle selbst vor – das ist ein echter Strukturfehler, keine bloße Abweichung von einem Paket. "
      "Bitte neu aufspielen.",
      N_("The firmware in the app area is damaged or incompletely written: {errors}. The firmware table itself states "
         "these checksums – this is a real structural error, not a mere deviation from a package. Please reinstall."),
      kind={"errors": "list"}),
    R("Der App-Bereich gleicht {name}, aber {n} von {total} Sektoren weichen ab und die Dateitabelle der Firmware bei "  # i18n: ignore
      "0x04000 ist nicht lesbar. Die Firmware ist beschädigt oder unvollständig geschrieben – bitte neu aufspielen.",
      N_("The app area resembles {name}, but {n} of {total} sectors differ and the file table of the firmware at "
         "0x04000 is not readable. The firmware is damaged or incompletely written – please reinstall."),
      pat={"n": r"\d+", "total": r"\d+"}),
    R("Abweichende Sektoren: {ranges}", N_("Differing sectors: {ranges}"), kind={"ranges": "raw"}),  # i18n: ignore
    R("Die Firmware im App-Bereich passt zu keinem Paket, das die App kennt (z. B. eine neuere Version). Ihre "  # i18n: ignore
      "Dateitabelle und Prüfsummen stimmen ({layout}) – sie ist in sich vollständig.",
      N_("The firmware in the app area matches no package the app knows (e.g. a newer version). Its file table and "
         "checksums match ({layout}) – it is complete in itself.")),
    R("Die Firmware im App-Bereich passt zu keinem Paket, das die App kennt (z. B. eine neuere Version). Ob sie "  # i18n: ignore
      "vollständig ist, lässt sich so nicht sagen.",
      N_("The firmware in the app area matches no package the app knows (e.g. a newer version). Whether it is "
         "complete cannot be said this way.")),
    R("In {n} Sektoren ({ranges}) liegen noch Reste von V16. V15 und Community-Firmwares schreiben dort nie hin und "  # i18n: ignore
      "räumen sie nicht weg. Melodee 1.5.1 lief im Emulator damit genau wie auf sauberem Flash; für 1.6.0-rc.7 ist das "
      "nicht geprüft. Löschen kannst du sie im ROM-Bootmodus mit „V16-Reste aufräumen“ (Expertenmodus dieser Karte, am "
      "echten Gerät ungetestet) – oder liegen lassen.",
      NP_("Leftovers of V16 are still in {n} sector ({ranges}). V15 and community firmwares never write there and do "
          "not clean them up. Melodee 1.5.1 ran with them in the emulator exactly as on a clean flash; for 1.6.0-rc.7 "
          "this is not checked. You can erase them in ROM boot mode with “Clean up V16 leftovers” (expert mode of this "
          "card, untested on a real device) – or leave them.",
          "Leftovers of V16 are still in {n} sectors ({ranges}). V15 and community firmwares never write there and do "
          "not clean them up. Melodee 1.5.1 ran with them in the emulator exactly as on a clean flash; for 1.6.0-rc.7 "
          "this is not checked. You can erase them in ROM boot mode with “Clean up V16 leftovers” (expert mode of this "
          "card, untested on a real device) – or leave them."),
      n="n", kind={"ranges": "raw"}),
    R("Reste: {ranges} – Aufräum-Plan: {plan}", N_("Leftovers: {ranges} – clean-up plan: {plan}"),  # i18n: ignore
      kind={"ranges": "raw", "plan": "raw"}),
    R("Keine Reste von V16 oder anderen bekannten Paketen in den Datenbereichen.",  # i18n: ignore
      N_("No leftovers of V16 or other known packages in the data areas.")),
    R("Die Melodee-Ressourcen (Werkssounds, Schrift, Bluetooth-Programm) bei 0xCB000 sind vollständig und gehören zu "  # i18n: ignore
      "genau diesem Build.",
      N_("The Melodee resources (factory sounds, font, Bluetooth program) at 0xCB000 are complete and belong to "
         "exactly this build.")),
    R("Die Melodee-Ressourcen sind in sich vollständig, gehören aber zu einem anderen Build. Abhilfe: „Schrift & "  # i18n: ignore
      "Werkssounds nachschreiben“ (Seite „Firmware“).",
      N_("The Melodee resources are complete in themselves, but belong to a different build. Remedy: “Rewrite font & "
         "factory sounds” (page “Firmware”).")),
    R("Die Melodee-Ressourcen bei 0xCB000 fehlen oder sind beschädigt – dann fehlen Werkssounds. Abhilfe: „Schrift & "  # i18n: ignore
      "Werkssounds nachschreiben“ (Seite „Firmware“).",
      N_("The Melodee resources at 0xCB000 are missing or damaged – then factory sounds are missing. Remedy: “Rewrite "
         "font & factory sounds” (page “Firmware”).")),
    R("Ressourcen 0xCB000: {head}, Nutzdaten {payload}", N_("Resources 0xCB000: {head}, payload {payload}"),  # i18n: ignore
      pat={"head": "Kopf ok|Kopf fehlt/defekt", "payload": "ok|nicht ok"},  # i18n: ignore
      kind={"head": _HEAD_OK, "payload": _PAYLOAD_OK}),
    R("Ob deine Songs, Presets und Einstellungen in sich stimmen, prüft die Firmware selbst – die App vergleicht nur "  # i18n: ignore
      "mit bekannten Paketen.",
      N_("Whether your songs, presets and settings are consistent in themselves is checked by the firmware itself – "
         "the app only compares with known packages.")),
    R("Bereiche ohne Vorlage (Einstellungen, Bluetooth-Kopplungen, Seriennummer) kann sie nicht bewerten.",  # i18n: ignore
      N_("It cannot assess areas without a reference (settings, Bluetooth pairings, serial number).")),
    R("Der Dump zeigt den Stand zum Zeitpunkt des Lesens.", N_("The dump shows the state at the time of reading.")),  # i18n: ignore

    # ---------------------------------------------------------------- flashmodel.py: loss sentences (planner)
    R("Beim Schreiben von V16: der Bereich 0x93000–0xA8FFF (22 Sektoren) – bei dir: {what}. So gut wie sicher: Der "  # i18n: ignore
      "Update-Loader schreibt genau so weit, wie das Paket lang ist (bei V15 an echter Hardware aufgezeichnet).",
      N_("When writing V16: the area 0x93000–0xA8FFF (22 sectors) – on your device: {what}. As good as certain: the "
         "update loader writes exactly as far as the package is long (recorded with V15 on real hardware)."),
      kind={"what": "names"}),
    R("Beim ersten Start formatiert V16 zusätzlich {ranges} – bei dir: {what}. Beobachtet im Emulator, nicht an echter "  # i18n: ignore
      "Hardware. Die Ressourcen schreibt die App beim Rückweg auf Melodee neu.",
      N_("At the first start V16 additionally formats {ranges} – on your device: {what}. Observed in the emulator, not "
         "on real hardware. The app rewrites the resources on the way back to Melodee."),
      kind={"ranges": "raw", "what": "names"}),
    R("Beim ersten Start formatiert V16 zusätzlich {ranges} – bei dir: {what}. Beobachtet im Emulator, nicht an echter "  # i18n: ignore
      "Hardware.",
      N_("At the first start V16 additionally formats {ranges} – on your device: {what}. Observed in the emulator, not "
         "on real hardware."),
      kind={"ranges": "raw", "what": "names"}),
    R("Alles, was du unter V16 gespeichert hast (Patterns, Einstellungen), ist danach nicht mehr nutzbar – die App kann "  # i18n: ignore
      "von V16 kein Backup machen.",
      N_("Everything you saved under V16 (patterns, settings) can no longer be used afterwards – the app cannot make a "
         "backup of V16.")),
    R("V15 und Community-Firmwares schreiben nur bis 0x92FFF: Reste von V16 in 0x93000–0xA8FFF bleiben liegen. Melodee "  # i18n: ignore
      "1.5.1 lief damit im Emulator wie auf sauberem Flash; prüfen kannst du es mit „Ist mein FM-1 sauber?“ (Rettung).",
      N_("V15 and community firmwares only write up to 0x92FFF: leftovers of V16 in 0x93000–0xA8FFF remain. Melodee "
         "1.5.1 ran with them in the emulator as on a clean flash; you can check it with “Is my FM-1 clean?” "
         "(Recovery).")),

    # ---------------------------------------------------------------- restzone.py: gates, plan, run
    R("{addr} ist keine Sektorgrenze – abgelehnt.", N_("{addr} is not a sector boundary – refused."), kind={"addr": "raw"}),  # i18n: ignore
    R("{addr} liegt außerhalb der Restzone {lo}–{hi} – abgelehnt.",  # i18n: ignore
      N_("{addr} lies outside the leftover zone {lo}–{hi} – refused."), pat={"addr": HEX, "lo": HEX, "hi": HEX}),
    R("{addr} berührt „{why}“ – wird nie gelöscht.", N_("{addr} touches “{why}” – is never erased."), pat={"addr": HEX}),  # i18n: ignore
    R("Der Dump hat nicht genau 1 MiB – so wird nichts gelöscht.", N_("The dump is not exactly 1 MiB – so nothing is erased.")),  # i18n: ignore
    R("Die Firmware im Flash ist keine, die die App byte-genau kennt (oder sie ist beschädigt). Dann lässt sich nicht "  # i18n: ignore
      "sicher sagen, welche Bereiche ihr gehören – es wird nichts gelöscht. Spiel zuerst eine bekannte Firmware auf "
      "(z. B. Melodee 1.6.0-rc.7) und lies den Flash dann neu.",
      N_("The firmware in the flash is not one the app knows byte for byte (or it is damaged). Then it cannot be said "
         "for sure which areas belong to it – nothing is erased. First install a known firmware (e.g. Melodee "
         "1.6.0-rc.7) and then read the flash again.")),
    R("Auf dem FM-1 steht {name}. Bei V16 gehört 0x93000–0xA8FFF zur Firmware selbst – dort wird nie aufgeräumt. "  # i18n: ignore
      "Aufräumen geht erst, wenn wieder V15, Melodee oder eine andere Firmware mit V15-Aufbau läuft.",
      N_("The FM-1 holds {name}. With V16, 0x93000–0xA8FFF belongs to the firmware itself – nothing is ever cleaned up "
         "there. Cleaning up is only possible when V15, Melodee or another firmware with the V15 layout runs again.")),
    R("Der App fehlt der V16-Steckbrief – ohne ihn erkennt sie keine Reste. Es wird nichts gelöscht.",  # i18n: ignore
      N_("The app lacks the V16 profile – without it, it recognizes no leftovers. Nothing is erased.")),
    R("Löschen wurde unterbrochen, der Inhalt ist aber nicht mehr eindeutig ein halb gelöschter V16-Rest – bleibt, wie "  # i18n: ignore
      "er ist.",
      N_("Erasing was interrupted, but the content is no longer clearly a half-erased V16 leftover – stays as it is.")),
    R("trägt keine V16-Bytes (z. B. Daten deiner Firmware) – bleibt.",  # i18n: ignore
      N_("carries no V16 bytes (e.g. data of your firmware) – stays.")),
    R("war geplant, ist laut frischer Sicherung aber kein V16-Rest (mehr) – bleibt.",  # i18n: ignore
      N_("was planned, but according to the fresh backup it is not (or no longer) a V16 leftover – stays.")),
    R("V16-Rest, aber nicht bestätigt – bleibt.", N_("V16 leftover, but not confirmed – stays.")),  # i18n: ignore
    R("Zwei Lesungen von {addr} weichen voneinander ab – die USB-Verbindung ist unzuverlässig.",  # i18n: ignore
      N_("Two readings of {addr} differ from each other – the USB connection is unreliable."), pat={"addr": HEX}),
    R("Absturz-Simulation gibt es nur mit dem Mock-FM-1.", N_("Crash simulation is only available with the mock FM-1.")),  # i18n: ignore
    R("Schritt 1/5: Gerät prüfen (Chip-Key, Flash-ID)", N_("Step 1/5: check the device (chip key, flash ID)")),  # i18n: ignore
    R("Schritt 2/5: Pflicht-Sicherung – ganzer Flash, zweimal gelesen und verglichen",  # i18n: ignore
      N_("Step 2/5: mandatory backup – whole flash, read twice and compared")),
    R("Schritt 3/5: Plan aus dieser Sicherung", N_("Step 3/5: plan from this backup")),  # i18n: ignore
    R("Abgelehnt: {reason} Es wurde nichts gelöscht.", N_("Refused: {reason} Nothing was erased.")),  # i18n: ignore
    R("Firmware: {firmware} · V16-Reste: {n} Sektoren {ranges} · leer: {empty} · bleiben: {kept}",  # i18n: ignore
      NP_("Firmware: {firmware} · V16 leftovers: {n} sector {ranges} · empty: {empty} · staying: {kept}",
          "Firmware: {firmware} · V16 leftovers: {n} sectors {ranges} · empty: {empty} · staying: {kept}"),
      n="n", pat={"ranges": ".*?", "empty": r"\d+", "kept": r"\d+"}, kind={"ranges": "raw"}),
    R("Schritt 4/5: {n} Sektoren löschen, jeden einzeln prüfen ({untested}). Jetzt nichts abstecken.",  # i18n: ignore
      NP_("Step 4/5: erase {n} sector, check it on its own ({untested}). Do not unplug anything now.",
          "Step 4/5: erase {n} sectors, check each one on its own ({untested}). Do not unplug anything now."),
      n="n"),
    R("Sektor {key} hat sich seit der Sicherung verändert – angehalten.",  # i18n: ignore
      N_("Sector {key} has changed since the backup – stopped."), pat={"key": HEX}),
    R("Simulierter Absturz mitten im Löschen (nur in der Simulation).",  # i18n: ignore
      N_("Simulated crash in the middle of erasing (only in the simulation).")),
    R("Sektor {key} ist nach dem Löschen nicht leer – zweiter Versuch",  # i18n: ignore
      N_("Sector {key} is not empty after erasing – second attempt"), pat={"key": HEX}),
    R("Sektor {key} ist nach zwei Löschversuchen nicht leer.", N_("Sector {key} is not empty after two erase attempts."),  # i18n: ignore
      pat={"key": HEX}),
    R("Nach dem Löschen von {key} hat sich auch {addr} verändert – der Chip hat mehr gelöscht als den einen Sektor. "  # i18n: ignore
      "Sofort angehalten.",
      N_("After erasing {key}, {addr} has changed as well – the chip erased more than the one sector. Stopped "
         "immediately."), pat={"key": HEX, "addr": HEX}),
    R("{key} gelöscht und geprüft ({kind})", N_("{key} erased and checked ({kind})"),  # i18n: ignore
      pat={"key": HEX, "kind": "voll|teilweise|unterbrochen"}, kind={"kind": _KIND}),  # i18n: ignore
    R("Schritt 5/5: ganzen Flash zurücklesen und mit dem Erwarteten vergleichen",  # i18n: ignore
      N_("Step 5/5: read back the whole flash and compare it with what is expected")),
    R("Der Flash weicht bei {addr} vom Erwarteten ab.", N_("The flash differs from what is expected at {addr}."),  # i18n: ignore
      pat={"addr": HEX}),
    R("Fertig: {n} Sektoren gelöscht und geprüft, alles andere byte-gleich mit der Sicherung.",  # i18n: ignore
      NP_("Done: {n} sector erased and checked, everything else byte-identical with the backup.",
          "Done: {n} sectors erased and checked, everything else byte-identical with the backup."), n="n"),
    R("ANGEHALTEN: {error}", N_("STOPPED: {error}")),  # i18n: ignore

    # ---------------------------------------------------------------- restzone.py: report
    R("Angehalten: {error} Gelöscht und geprüft: {n} Sektoren ({erased}). Deine Sicherung liegt bereit; das Journal "  # i18n: ignore
      "merkt sich den Stand. Bitte das FM-1 erst wieder normal benutzen, wenn der Lauf fertig ist: im ROM-Bootmodus "
      "noch einmal „V16-Reste aufräumen“ starten – er setzt dort fort.",
      NP_("Stopped: {error} Erased and checked: {n} sector ({erased}). Your backup is ready; the journal remembers the "
          "state. Please only use the FM-1 normally again when the run is finished: start “Clean up V16 leftovers” "
          "once more in ROM boot mode – it continues there.",
          "Stopped: {error} Erased and checked: {n} sectors ({erased}). Your backup is ready; the journal remembers the "
          "state. Please only use the FM-1 normally again when the run is finished: start “Clean up V16 leftovers” "
          "once more in ROM boot mode – it continues there."),
      n="n", kind={"erased": "raw"}),
    R("Angehalten: {error} Gelöscht und geprüft: {n} Sektoren. Deine Sicherung liegt bereit; das Journal merkt sich "  # i18n: ignore
      "den Stand. Bitte das FM-1 erst wieder normal benutzen, wenn der Lauf fertig ist: im ROM-Bootmodus noch einmal "
      "„V16-Reste aufräumen“ starten – er setzt dort fort.",
      NP_("Stopped: {error} Erased and checked: {n} sector. Your backup is ready; the journal remembers the state. "
          "Please only use the FM-1 normally again when the run is finished: start “Clean up V16 leftovers” once more "
          "in ROM boot mode – it continues there.",
          "Stopped: {error} Erased and checked: {n} sectors. Your backup is ready; the journal remembers the state. "
          "Please only use the FM-1 normally again when the run is finished: start “Clean up V16 leftovers” once more "
          "in ROM boot mode – it continues there."),
      n="n"),
    R("{n} Sektoren tragen noch V16-Bytes ({ranges}). Diese – und nur diese – würde „Aufräumen“ löschen.",  # i18n: ignore
      NP_("{n} sector still carries V16 bytes ({ranges}). This one – and only this one – would be erased by “Clean up”.",
          "{n} sectors still carry V16 bytes ({ranges}). These – and only these – would be erased by “Clean up”."),
      n="n", kind={"ranges": "raw"}),
    R("In 0x93000–0xA8FFF liegen keine V16-Reste. Es gibt nichts aufzuräumen.",  # i18n: ignore
      N_("There are no V16 leftovers in 0x93000–0xA8FFF. There is nothing to clean up.")),
    R("{n} Sektoren mit V16-Resten gelöscht, jeder einzeln geprüft; danach den ganzen Flash zurückgelesen: Alles andere "  # i18n: ignore
      "ist byte-gleich mit der Sicherung.",
      NP_("{n} sector with V16 leftovers erased and checked on its own; then the whole flash read back: everything else "
          "is byte-identical with the backup.",
          "{n} sectors with V16 leftovers erased, each one checked on its own; then the whole flash read back: "
          "everything else is byte-identical with the backup."), n="n"),
    R("Es gab nichts zu löschen – in 0x93000–0xA8FFF liegen keine V16-Reste.",  # i18n: ignore
      N_("There was nothing to erase – there are no V16 leftovers in 0x93000–0xA8FFF.")),
    R("Firmware im Flash: {firmware}", N_("Firmware in the flash: {firmware}")),  # i18n: ignore
    R("{n} Sektoren in der Zone bleiben unangetastet (keine V16-Bytes, z. B. Daten deiner Firmware).",  # i18n: ignore
      NP_("{n} sector in the zone stays untouched (no V16 bytes, e.g. data of your firmware).",
          "{n} sectors in the zone stay untouched (no V16 bytes, e.g. data of your firmware)."), n="n"),
    R("bleibt {addr}: {why}", N_("stays {addr}: {why}"), pat={"addr": HEX}),  # i18n: ignore
    R("Sicherung (zweimal gelesen, identisch): {path}", N_("Backup (read twice, identical): {path}"), kind={"path": "raw"}),  # i18n: ignore
    R("Flash danach: {path}", N_("Flash afterwards: {path}"), kind={"path": "raw"}),  # i18n: ignore
    R("Journal: {path}", N_("Journal: {path}"), kind={"path": "raw"}),  # i18n: ignore
    R("Diese Funktion ist {untested} – bisher nur mit dem simulierten FM-1 des Unbrickers geprüft. Dass der Chip beim "  # i18n: ignore
      "Befehl „Sektor löschen“ genau 4 KiB löscht, ist angenommen; die App prüft danach jeden Block nach und hält "
      "sofort an, wenn es mehr war.",
      N_("This function is {untested} – so far only checked with the unbricker's simulated FM-1. That the chip erases "
         "exactly 4 KiB with the command “erase sector” is assumed; the app then checks every block and stops "
         "immediately if it was more.")),
    R("Ob deine Firmware mit oder ohne die Reste besser läuft, sagt dieser Lauf nicht. Melodee 1.5.1 lief im Emulator "  # i18n: ignore
      "mit Resten genauso wie ohne; für 1.6.0-rc.7 ist das nicht geprüft.",
      N_("Whether your firmware runs better with or without the leftovers, this run does not say. Melodee 1.5.1 ran in "
         "the emulator with leftovers just as without; for 1.6.0-rc.7 this is not checked.")),
    R("Bootkopf, Firmware-Bereich und alles außerhalb 0x93000–0xA8FFF werden nicht verändert.",  # i18n: ignore
      N_("Boot header, firmware area and everything outside 0x93000–0xA8FFF are not changed.")),

    # ---------------------------------------------------------------- restzone.py: command line run
    R("Demo-Optionen gibt es nur in der Simulation.", N_("Demo options are only available in the simulation.")),  # i18n: ignore
    R("V16-Paket {path} nicht lesbar ({error}) – ohne es weiter",  # i18n: ignore
      N_("V16 package {path} not readable ({error}) – continuing without it"), kind={"path": "raw"}),
    R("Ohne bestätigte Sektorliste wird nichts gelöscht.", N_("Without a confirmed sector list nothing is erased.")),  # i18n: ignore
    R("SIMULATION: Mock-FM-1 des Unbrickers – kein echtes Gerät, nichts wird an Hardware verändert.",  # i18n: ignore
      N_("SIMULATION: the unbricker's mock FM-1 – no real device, nothing is changed on hardware.")),
    R("ABGELEHNT: {reason} Es wurde nichts gelöscht.", N_("REFUSED: {reason} Nothing was erased.")),  # i18n: ignore
    R("FEHLER: {error}", N_("ERROR: {error}")),  # i18n: ignore
    R("Ergebnis-Datei nicht schreibbar: {error}", N_("Result file not writable: {error}"), kind={"error": "raw"}),  # i18n: ignore
    R("Protokoll: {path}", N_("Log: {path}"), kind={"path": "raw"}),  # i18n: ignore

    # ---------------------------------------------------------------- rescuegate.py
    R("Sicherer Weg: M-Flasher (github.com/jvitkauskas/m-flasher, liest die Flash-Karte aus der Firmware) oder "  # i18n: ignore
      "fm1_rescue.py aus felucca-bsp (wählt das Paket passend zum Bootkopf und schreibt sonst nichts).",
      N_("Safe way: M-Flasher (github.com/jvitkauskas/m-flasher, reads the flash map from the firmware) or "
         "fm1_rescue.py from felucca-bsp (chooses the package that matches the boot header and writes nothing else).")),
    R("Die Sicherung hat nicht genau 1 MiB – so wird nichts geschrieben.",  # i18n: ignore
      N_("The backup is not exactly 1 MiB – so nothing is written.")),
    R("Auf dem FM-1 steht {name} mit V16-Aufbau: Dort gehört 0x93000–0xA8FFF noch zur Firmware (Ende des Programms und "  # i18n: ignore
      "die Werks-Voices DX7.bin), die Einstellungen liegen woanders (0xBA000 ff.). „Einstellungen reparieren“ ist nur "
      "für den V15-Aufbau gebaut und würde die Firmware überschreiben. Es wurde nichts geschrieben.",
      N_("The FM-1 holds {name} with the V16 layout: there 0x93000–0xA8FFF still belongs to the firmware (end of the "
         "program and the factory voices DX7.bin), the settings are elsewhere (0xBA000 onwards). “Repair settings” is "
         "only built for the V15 layout and would overwrite the firmware. Nothing was written.")),
    R("Im App-Bereich steht der Anfang von V16 (abgeschnitten). Damit ist der Aufbau nicht der von V15 – „Einstellungen "  # i18n: ignore
      "reparieren“ hilft hier nicht und schreibt nichts. Erst die Original-V15 zurückschreiben, dann neu sichern.",
      N_("The app area holds the beginning of V16 (truncated). So the layout is not that of V15 – “Repair settings” "
         "does not help here and writes nothing. First write back the original V15, then back up again.")),
    R("Die Dateitabelle der Firmware im Flash sagt: Sie reicht bis {end} und hat einen SEQ64-Bereich – das ist der "  # i18n: ignore
      "V16-Aufbau. Dort gehört 0x93000–0xA8FFF zur Firmware. „Einstellungen reparieren“ würde sie überschreiben. Es "
      "wurde nichts geschrieben.",
      N_("The file table of the firmware in the flash says: it extends to {end} and has a SEQ64 area – that is the V16 "
         "layout. There 0x93000–0xA8FFF belongs to the firmware. “Repair settings” would overwrite it. Nothing was "
         "written."), pat={"end": HEX}),
    R("Die Dateitabelle der Firmware im Flash sagt: Sie reicht bis {end} – das ist der V16-Aufbau. Dort gehört "  # i18n: ignore
      "0x93000–0xA8FFF zur Firmware. „Einstellungen reparieren“ würde sie überschreiben. Es wurde nichts geschrieben.",
      N_("The file table of the firmware in the flash says: it extends to {end} – that is the V16 layout. There "
         "0x93000–0xA8FFF belongs to the firmware. “Repair settings” would overwrite it. Nothing was written."),
      pat={"end": HEX}),
    R("Die Firmware im Flash hat einen Aufbau, für den das Werkzeug nicht gebaut ist (Ende {end}, Einstellungen ab "  # i18n: ignore
      "{vm}; z. B. V13/V14). Im Zweifel schreibt die App nichts.",
      N_("The firmware in the flash has a layout the tool is not built for (end {end}, settings from {vm}; e.g. "
         "V13/V14). When in doubt, the app writes nothing."), pat={"end": HEX}),
    R("Die Firmware im Flash ist beschädigt ({errors}). Dann lässt sich nicht sicher sagen, wo sie endet – es wurde "  # i18n: ignore
      "nichts geschrieben. Zuerst die Original-V15 zurückschreiben.",
      N_("The firmware in the flash is damaged ({errors}). Then it cannot be said for sure where it ends – nothing was "
         "written. First write back the original V15."), kind={"errors": "list"}),
    R("Die Firmware im Flash ist keine, die die App kennt, und ihre Dateitabelle ist nicht lesbar. Dann lässt sich "  # i18n: ignore
      "nicht beweisen, dass 0x93000–0xE8FFF nur Einstellungen enthält – es wurde nichts geschrieben.",
      N_("The firmware in the flash is not one the app knows, and its file table is not readable. Then it cannot be "
         "proven that 0x93000–0xE8FFF contains only settings – nothing was written.")),
    R("Die Sicherung zeigt den Bootkopf von V16 ({sha}…). M-VAVEs Updater schreibt beim Installieren von V16 einen "  # i18n: ignore
      "eigenen, größeren Bootlader. Ob die Original-V15 darunter startet, ist nicht geklärt, und die V16-App "
      "(0x4000–0xA8FFF) kann dieses Werkzeug nicht schreiben – es schreibt nur bis 0x92FFF. Den Bootkopf schreibt die "
      "App nie. Darum wurde nichts geschrieben. {alternative}",
      N_("The backup shows the boot header of V16 ({sha}…). M-VAVE's updater writes its own, larger boot loader when "
         "installing V16. Whether the original V15 starts under it has not been established, and this tool cannot "
         "write the V16 app (0x4000–0xA8FFF) – it only writes up to 0x92FFF. The app never writes the boot header. "
         "That is why nothing was written. {alternative}"), pat={"sha": SHA, "alternative": _ALT}),
    R("Den Bootkopf in der Sicherung ({sha}…, {head}) kennt die App nicht als den ab Werk. Ob die Original-V15 darunter "  # i18n: ignore
      "startet, kann sie nicht sagen (z. B. nach m-bootloader oder einem Schreiben des ganzen Flashs). Im Zweifel wurde "
      "nichts geschrieben. {alternative}",
      N_("The app does not know the boot header in the backup ({sha}…, {head}) as the factory one. Whether the original "
         "V15 starts under it, it cannot say (e.g. after m-bootloader or a write of the whole flash). When in doubt, "
         "nothing was written. {alternative}"), pat={"sha": SHA, "alternative": _ALT}),
    R("Diese Option gibt es über die App nicht.", N_("This option is not available through the app.")),  # i18n: ignore
    R("Im ROM-Bootmodus schreibt die App nur die geprüfte Original-V15 (--verify-v15).",  # i18n: ignore
      N_("In ROM boot mode the app only writes the checked original V15 (--verify-v15).")),
    R("ABGELEHNT (Suite-Prüfung der Sicherung): {reason}", N_("REFUSED (suite check of the backup): {reason}")),  # i18n: ignore
    R("Suite-Prüfung der Sicherung: OK – {basis}", N_("Suite check of the backup: OK – {basis}")),  # i18n: ignore
    R("Ohne Sicherung beendet – es wurde nichts geschrieben.", N_("Ended without a backup – nothing was written.")),  # i18n: ignore

    # ---------------------------------------------------------------- generic wrappers (last: they match a lot)
    R("{reason} Es wurde nichts geschrieben.", N_("{reason} Nothing was written.")),  # i18n: ignore
    R("{reason} Es wurde nichts gelöscht.", N_("{reason} Nothing was erased.")),  # i18n: ignore
    R("ABGELEHNT: {reason}", N_("REFUSED: {reason}")),  # i18n: ignore
]

# prefixes the (English) unbricker puts in front of a message from these files
_FOREIGN_PREFIXES = ("ERROR: ", "WARNING: ")
_PH = re.compile(r"\{(\w+)\}")
_WS = re.compile(r"(\s*)(.*?)(\s*)", re.S)
_EXACT: dict[str, Rule] = {}
_PATTERNS: list[Rule] = []


def _compile() -> None:
    if _EXACT or _PATTERNS:
        return
    for r in RULES:
        if not _PH.search(r.de):
            _EXACT.setdefault(r.de, r)
            continue
        parts, pos, seen = [], 0, set()
        for m in _PH.finditer(r.de):
            parts.append(re.escape(r.de[pos:m.start()]))
            name = m.group(1)
            if name in seen:
                parts.append(f"(?P={name})")
            else:
                seen.add(name)
                parts.append(f"(?P<{name}>{r.pat.get(name) or (r'[0-9]+' if name == r.n else '.+?')})")
            pos = m.end()
        parts.append(re.escape(r.de[pos:]))
        r.rx = re.compile("".join(parts), re.S)
        r.prefix = r.de[:r.de.index("{")]
        _PATTERNS.append(r)


def _msg(r: Rule, values: dict) -> str:
    if isinstance(r.en, tuple):
        try:
            count = int(values.get(r.n, "0"))
        except ValueError:
            count = 2
        return ngettext(r.en[0], r.en[1], count).format(**values)
    return _(r.en).format(**values)


def _names(v: str, depth: int) -> str:
    """comma-separated German names ("Prophet-Bänke, CZ-Bänke") – names may contain ", " themselves"""
    items = sorted(_EXACT, key=len, reverse=True)
    out, rest = [], v
    while rest:
        for de in items:
            if rest == de or rest.startswith(de + ", "):
                out.append(_msg(_EXACT[de], {}))
                rest = rest[len(de) + 2:]
                break
        else:
            out.append(_tr(rest, depth + 1))
            break
    return ", ".join(out)


def _value(kind, v: str, depth: int) -> str:
    if kind == "raw":
        return v
    if kind == "num":
        return _num(v)
    if kind == "list":
        return "; ".join(_tr(x, depth + 1) for x in v.split("; "))
    if kind == "names":
        return _names(v, depth)
    if isinstance(kind, dict):
        en = kind.get(v)
        return _(en) if en else v
    if callable(kind):
        return kind(v)
    return _tr(v, depth + 1)


def _tr(text: str, depth: int) -> str:
    if depth > 8 or not text:
        return text
    lead, core, trail = _WS.fullmatch(text).groups()
    r = _EXACT.get(core)
    if r is not None:
        return lead + _msg(r, {}) + trail
    for r in _PATTERNS:
        if not core.startswith(r.prefix):
            continue
        m = r.rx.fullmatch(core)
        if m:
            vals = {k: _value(r.kind.get(k), v, depth) for k, v in m.groupdict().items()}
            return lead + _msg(r, vals) + trail
    for p in _FOREIGN_PREFIXES:
        if core.startswith(p):
            inner = core[len(p):]
            t = _tr(inner, depth + 1)
            if t != inner:
                return lead + p + t + trail
    return text


def tr(text):
    """a message of flashmodel.py / restzone.py / rescuegate.py in the UI language; anything else unchanged"""
    if not isinstance(text, str) or not text:
        return text
    _compile()
    try:
        return _tr(text, 0)
    except Exception:  # noqa: BLE001 – never hide a safety message because of a translation problem
        return text


def matches(text: str) -> bool:
    """True if a rule of the table covers this whole text (tests, coverage check)"""
    _compile()
    core = text.strip()
    return core in _EXACT or any(r.rx.fullmatch(core) for r in _PATTERNS)


# ====================================================================== whole results
def report(rep):
    """flashmodel.Report (also from restzone.report) -> copy with all texts in the UI language"""
    out = copy.copy(rep)
    out.title = tr(rep.title)
    out.firmware = tr(rep.firmware)
    out.findings = [(lv, tr(t)) for lv, t in rep.findings]
    out.details = [tr(t) for t in rep.details]
    out.not_covered = [tr(t) for t in rep.not_covered]
    out.compared = [tr(t) for t in rep.compared]
    out.residue = list(rep.residue)
    return out


def verdict(v: dict) -> dict:
    """rescuegate verdict ({"ok", "reason", "firmware", "basis", "head"}) in the UI language"""
    return {k: tr(x) if k in ("reason", "firmware", "basis", "head") else x for k, x in (v or {}).items()}


def result(res: dict) -> dict:
    """result file of restzone.py / rescuegate.py in the UI language (texts only; addresses, paths unchanged)"""
    out = dict(res or {})
    for k in ("refused", "error", "firmware", "note", "untested"):
        if isinstance(out.get(k), str):
            out[k] = tr(out[k])
    if isinstance(out.get("skipped"), list):
        out["skipped"] = [dict(s, why=tr(s.get("why", ""))) if isinstance(s, dict) else s for s in out["skipped"]]
    if isinstance(out.get("verdict"), dict):
        out["verdict"] = verdict(out["verdict"])
    return out


def level_text(level: str) -> str:
    """level of a report ("gruen", "gelb", "rot") as a word in the UI language"""
    return _({"gruen": N_("Clean"), "gelb": N_("With notes"), "rot": N_("Not OK")}.get(level, level))  # i18n: ignore
