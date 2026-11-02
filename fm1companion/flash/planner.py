# SPDX-License-Identifier: GPL-3.0-only
"""Installationsplan: welche Schritte in welcher Reihenfolge, mit Risiko, Warnungen und Sperren.

plan_install(device_state, target) ist rein (kein MIDI, kein Netz) und vollständig getestet.
Regeln und Begründungen: docs/flash-protocol.md.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import flashmodel as fm, package as pk
from .. import melodee16 as m16

TRAP_TEXT = ("Beim Wechsel auf die Original-Firmware vergleicht die laufende Felucca-Firmware den Bootbereich "
             "(CHECK HEAD). Weicht er ab – z. B. nach m-bootloader oder M-VAVE Open V12/V14 –, zeigt das FM-1 "
             "„FAILED -5“ (Felucca-1.6.1-Kopien: -15). Es wird nichts geschrieben, die jetzige Firmware läuft weiter. "
             "Auswege: eine Felucca-Firmware mit neuer Prüfung (felucca-bsp afd83ede) oder der Download-Modus "
             "(Rettung Stufe 2, M-Flasher).")
BOOT_CHANGERS = ("m-bootloader", "mbootloader", "open", "fm1open")

RISK_TEXT = {"keins": "kein Risiko", "niedrig": "geringes Risiko", "mittel": "mittleres Risiko", "hoch": "hohes Risiko"}

# Bestätigungen, die der Nutzer ausdrücklich geben muss
CONFIRM_TEXT = {
    "unknown_file": "Ich weiß, was ich tue: Diese Datei ist in keinem Katalog. Ich habe Kennung und Prüfsumme "
                    "selbst geprüft.",
    "power": "Das FM-1 ist geladen bzw. am Strom, das Kabel steckt direkt am Rechner, und der Rechner geht "
             "während der Installation nicht schlafen.",
    "no_backup": "Ich mache kein Backup und nehme in Kauf, dass Projekte und Sounds verloren gehen.",
    "beta": "Mir ist klar, dass dies eine Testversion (Beta) ist.",
    "felucca09": "Ich weiß, dass Felucca 0.9-Daten beim Wechsel auf 1.x den Start verhindern können "
                 "(Backup ist Pflicht; Abhilfe siehe Rettung).",
    "no_checksum": "Prüfsumme nicht verfügbar: Ich installiere dieses Paket, obwohl die App es gegen keine "
                   "Prüfsumme prüfen kann (das Release hat keine SHA256SUMS-Datei).",
    "melodee16_down": "Mir ist klar: Projekte, Presets und Backups von Melodee 1.6 kann die ältere Firmware nicht "
                      "lesen. Mein Rückweg ist ein Backup von vor dem Update auf 1.6.",
    "v16": "Mir ist klar: V16 ist experimentell und nur mit Backup gedacht. Der Rückweg von V16 auf V15 ist mit echter "
           "V16 noch nicht bestätigt – im schlimmsten Fall hilft nur der ROM-Bootmodus (Rettung Stufe 2).",
}

V16_WARNING = ("Experimentell – nur mit Backup. V16 nutzt einen anderen Flash-Aufbau als V15 und alle Community-Firmwares. "
               "Belegt ist: Der Update-Loader ist in V15 und V16 derselbe, und ein FM-1, das sich als Version 16 meldete "
               "(ein V15-Abkömmling), ließ sich per normalem Update auf V15 zurücksetzen. Mit echter V16 ist der Rückweg "
               "aber noch nicht bestätigt, und offen ist, ob der Loader dabei auch den Bootkopf neu schreibt. Klappt "
               "der Rückweg nicht, hilft der ROM-Bootmodus (Rettung Stufe 2). "
               "Empfehlung: nicht auf dem Haupt-FM-1.")
MULTI_DEVICE_TEXT = "Bitte nur das FM-1 angeschlossen lassen, das du aktualisieren willst."


@dataclass
class DeviceState:
    """was die App über das angeschlossene FM-1 weiß"""
    identity: str = ""            # Handshake-Kennung, z. B. "FM-1_91060", "ota-FM-1_900"
    felucca: bool = False         # Felucca-Protokoll nutzbar (Backup möglich)
    firmware_key: str = ""        # z. B. "melodee"
    version: str = ""             # z. B. "Melodee 1.6.0-rc.7"
    port: str = ""
    connected: bool = True
    ports: int = 1                # wie viele FM-1 (auch im Update-Modus) angeschlossen sind
    busy: bool = False            # eine andere Installation läuft
    pinned: bool = False          # Zielgerät eindeutig über USB-Anschluss/Seriennummer festgehalten

    @property
    def update_mode(self) -> bool:
        return self.identity.lower().startswith("ota-")

    @property
    def open_firmware(self) -> bool:
        return bool(pk.OPEN_ID_RE.match(self.identity))


@dataclass
class Target:
    """was installiert werden soll"""
    name: str
    product: str = ""             # erwartete Kennung (aus Paket oder Katalog)
    sha256: str = ""              # erwartete Prüfsumme ("" = unbekannt)
    family: str = ""              # Loader-Familie "stock"|"felucca"|"" (unbekannt)
    requires_stock_v15: bool = False
    notes: list[str] = field(default_factory=list)
    resources: bool = False       # Melodee ab 1.6: Ressourcen nach der Firmware schreiben
    key: str = ""                 # FirmwareSpec.key
    tag: str = ""
    file: Path | None = None
    package: pk.PackageInfo | None = None    # wenn die Datei schon vorliegt und untersucht ist
    known: bool = True            # aus Katalog/Builtin; False = eigene Datei
    prerelease: bool = False
    checksum: bool = True         # False: Paket lässt sich gegen keine Prüfsumme prüfen (Bestätigung nötig)

    @classmethod
    def from_spec(cls, spec, tag: str = "", file: Path | None = None, package=None, prerelease=False):
        return cls(name=spec.name, product=spec.product, sha256=(spec.sha256 or "").lower(),
                   family=_family(spec), requires_stock_v15=bool(spec.requires_stock_v15),
                   notes=list(spec.notes or []), resources=bool(spec.resources), key=spec.key, tag=tag,
                   file=file, package=package, known=True, prerelease=prerelease)

    @classmethod
    def from_file(cls, info: pk.PackageInfo, catalog_entry=None):
        if catalog_entry is not None:
            t = cls.from_spec(catalog_entry, file=info.path, package=info)
            return t
        return cls(name=info.path.name, product=info.product, sha256="", family=info.family,
                   file=info.path, package=info, known=bool(info.official), key="stock" if info.official else "",
                   requires_stock_v15=False)

    @property
    def is_stock(self) -> bool:
        return self.family == "stock" or self.key.startswith("stock")

    @property
    def is_v16(self) -> bool:
        return self.product == "FM-1_016" or self.key == "stock-v16" or bool(
            self.package is not None and self.package.official == "FM-1_016")


def _family(spec) -> str:
    if spec.family == "stock" or spec.key.startswith("stock"):
        return "stock"
    lf = getattr(spec, "loader_family", "")
    if lf:
        return pk.FAMILY_OF_CATALOG.get(lf, lf)
    if spec.protocol == "felucca" or spec.installer:
        return "felucca"
    return ""


@dataclass
class Step:
    kind: str                     # backup | fetch | stock | install | resume | resources | verify
    title: str
    risk: str                     # keins | niedrig | mittel | hoch
    text: str
    writes: bool = False


@dataclass
class InstallPlan:
    target: Target
    state: DeviceState
    steps: list[Step] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    confirmations: list[str] = field(default_factory=list)
    lost: list[str] = field(default_factory=list)    # was danach weg ist
    via_stock: bool = False
    resume: bool = False
    v15_option: str = "none"      # none | default_on | default_off | locked (Zwischenschritt V15 im Dialog)
    v15_reason: str = ""          # Begründung, wenn der Zwischenschritt nicht vom Katalog, sondern von V16 kommt

    @property
    def ok(self) -> bool:
        return not self.blockers

    def missing(self, confirmed) -> list[str]:
        return [c for c in self.confirmations if c not in set(confirmed or ())]

    def kinds(self) -> list[str]:
        return [s.kind for s in self.steps]

    def summary(self) -> str:
        return " → ".join(s.title for s in self.steps)


def check_package(t: Target, info: pk.PackageInfo) -> list[str]:
    """Sperren für ein vorliegendes Paket (Prüfsumme, Modell, Unversehrtheit, Loader)"""
    out = []
    if t.sha256 and info.sha256 != t.sha256:
        out.append(f"Die Prüfsumme von {info.path.name} stimmt nicht mit dem Katalog überein "
                   f"(erwartet {t.sha256[:12]}…, Datei {info.sha256[:12]}…). Die Datei ist beschädigt oder "
                   "wurde ausgetauscht – sie wird nicht installiert.")
    if info.model != "FM-1":
        out.append(f"Das Paket ist für „{info.model}“, nicht für das FM-1 ({info.product}).")
    if t.product and info.product != t.product and t.known:
        out.append(f"Das Paket meldet {info.product}, erwartet war {t.product}.")
    if info.damage:
        out.append(f"Das Paket ist beschädigt ({info.damage}). Bitte neu herunterladen.")
    if info.family not in ("stock", "felucca") and t.family not in ("stock", "felucca"):
        out.append("Die App kennt den Update-Loader dieses Pakets nicht und schickt es deshalb an keinen "
                   "Loader. Bitte den Installer des Autors benutzen.")
    if info.flash_bytes > 0xFF000:
        out.append(f"Das Paket würde bis {fm.hx(info.flash_bytes - 1)} schreiben – bis in den geschützten Bereich ab "
                   "0xFF000 (Gerätekennung, Bluetooth-Adresse). Kein bekanntes FM-1-Paket tut das; es wird nicht "
                   "installiert.")
    if info.family == "stock" and not info.official:
        out.append("Das Paket enthält den Original-Loader, ist aber keine unveränderte Original-Firmware "
                   "(V15/V16). Veränderte Original-Pakete werden nicht installiert.")
    return out


def v15_option(state: DeviceState, t: Target) -> str:
    """Zwischenschritt über Original-V15: none (Ziel ist Original / Gerät läuft schon V15 / nicht möglich),
    locked (Katalog verlangt V15), default_on (Firmware-Wechsel oder unbekannte Firmware), default_off (Update
    derselben Firmware)."""
    if state.identity == "FM-1_015" or state.update_mode:
        return "none"
    if v16_switch(state, t):
        return "locked"
    if t.is_stock:
        return "none"
    if t.requires_stock_v15:
        return "locked"
    cur = (state.firmware_key or "").lower()
    if cur and t.key and cur == t.key.lower():
        return "default_off"
    return "default_on"


def runs_v16(state: DeviceState) -> bool:
    return state.identity == "FM-1_016"


def v16_switch(state: DeviceState, t: Target) -> bool:
    """Wechsel zwischen V16 und V15-Aufbau, der nur über die Original-V15 geht:
    - Ziel V16 von einer Firmware, die nicht die Original-Firmware ist: Melodee & Co. vergleichen vor dem Original-Loader
      den Bootkopf des Pakets mit dem des Geräts (melodee firmware/src/ota.c, „CHECK HEAD“). V16 hat einen anderen
      Bootkopf als der Werks-Kopf (V13–V15) – das gäbe „FAILED -5“. [Paket]
    - von V16 auf etwas anderes als V15: Ob V16 den Loader einer Community-Firmware annimmt, ist unbekannt; der Weg
      über V15 nutzt in beiden Richtungen denselben Original-Loader. [Annahme, vorsichtig]"""
    if t.is_v16:
        return not runs_v16(state) and not pk.is_stock_identity(state.identity)
    return runs_v16(state) and t.product != "FM-1_015" and t.key != "stock-v15"


def v16_layout(t: Target) -> bool:
    """Ziel nutzt den V16-Aufbau: Kennung/Katalog sagen V16 – oder das vorliegende Paket schreibt über 0x92FFF hinaus
    (Inhalt zählt, nicht Name oder Kennung: so fällt auch ein umbenanntes oder umgekennzeichnetes Paket auf)"""
    return t.is_v16 or bool(t.package is not None and t.package.flash_bytes > fm.V15_END)


def layout_change(state: DeviceState, t: Target) -> bool:
    return v16_layout(t) != runs_v16(state)


def switch_key(state: DeviceState, t: Target) -> str:
    """Schlüssel, unter dem die Wahl für genau diesen Wechsel gemerkt wird"""
    return f"{(state.firmware_key or state.identity or '?').lower()}>{(t.key or t.product or t.name).lower()}"


def plan_install(state: DeviceState, target: Target, journal_active: dict | None = None,
                 stock_package: pk.PackageInfo | None = None, via_v15: bool | None = None,
                 backup: bool | None = None) -> InstallPlan:
    """backup: None/True = vorher sichern (wenn die Firmware das kann), False = ausdrücklich ohne Backup"""
    p = InstallPlan(target=target, state=state)
    t = target
    info = t.package
    if info is not None:
        if not t.family:
            t.family = info.family
        if not t.product:
            t.product = info.product
    fam = t.family if t.family in ("stock", "felucca") else (info.family if info else "")

    # ---- Sperren
    if state.busy or (journal_active and journal_active.get("phase") not in ("done", "failed", "safe")
                      and not state.update_mode and journal_active.get("pid_alive")):
        p.blockers.append("Es läuft bereits eine Installation. Bitte warten, bis sie fertig ist.")
    if not state.connected:
        p.blockers.append("Kein FM-1 verbunden. Bitte per USB-Datenkabel anschließen und einschalten.")
    if state.open_firmware:
        p.blockers.append("Auf diesem FM-1 läuft die M-VAVE-Open-Firmware. Erst mit M-VAVEs eigenem Werkzeug "
                          "(FM1 Workbench) zur Original-Firmware zurückkehren, dann hier installieren.")
    if state.identity and not state.update_mode and not state.open_firmware and \
            not state.identity.startswith("FM-1_"):
        p.blockers.append(f"Das angeschlossene Gerät meldet „{state.identity}“ – das ist kein FM-1.")
    if info is not None:
        p.blockers += check_package(t, info)
    if fam not in ("stock", "felucca") and info is None and not t.key:
        p.blockers.append("Unbekannte Loader-Familie.")
    if state.ports > 1 and not state.pinned:
        p.blockers.append(MULTI_DEVICE_TEXT + " Es sind mehrere FM-1 (oder Geräte im Update-Modus) angeschlossen, "
                          "und die App kann das Zielgerät nicht eindeutig an USB-Anschluss oder Seriennummer "
                          "erkennen. Nach dem Neustart in den Update-Modus könnte sie sonst das falsche Gerät "
                          "beschreiben.")
    elif state.ports > 1:
        p.warnings.append("Es sind mehrere FM-1 angeschlossen. Die App hält das Zielgerät über seinen "
                          "USB-Anschluss fest – bitte während der Installation kein Kabel umstecken.")

    # ---- Update-Modus: nur fortsetzen, wenn der wartende Loader zum Paket passt
    if state.update_mode:
        rule = pk.LoaderRule(fam, t.product or (info.product if info else "")) if fam else None
        if rule is None or not rule(state.identity):
            hint = {"ota-FM-1_900": "Er wartet auf ein Paket der Felucca-Familie (z. B. Melodee) – am besten "
                                    "genau das, dessen Installation unterbrochen wurde.",
                    "ota-FM-1_015": "Er wartet auf die unveränderte Original-Firmware V15."}
            h = hint.get(state.identity) or ("Er wartet auf das Paket, dessen Installation unterbrochen wurde."
                                            if pk.FELUCCA_LOADER_RE.match(state.identity) or
                                            pk.STOCK_LOADER_RE.match(state.identity) else
                                            "Diesen Loader kennt die App nicht.")
            p.blockers.append(f"Das FM-1 wartet im Update-Modus ({state.identity}). Dieses Paket gehört nicht zu "
                              f"diesem Loader – es wird nichts gesendet. {h}")
        p.resume = True
        p.steps.append(Step("fetch", "Paket laden & Prüfsumme prüfen", "keins",
                            "Paket holen und gegen die bekannte Prüfsumme prüfen."))
        if journal_active and journal_active.get("sha256") and info is not None \
                and journal_active["sha256"] != info.sha256:
            p.warnings.append(f"Unterbrochen wurde „{journal_active.get('target', '?')}“. Am sichersten ist es, "
                              "genau dieses Paket noch einmal zu schreiben.")
        p.steps.append(Step("resume", "Unterbrochene Installation abschließen", "mittel",
                            "Der wartende Update-Loader liest das ganze Paket noch einmal und schreibt es. "
                            "Bereits richtige Bereiche werden übersprungen.", writes=True))
        _tail(p, t)
        _confirm_power(p, t, fam)
        return p

    # ---- Vorprüfung (flashmodel.preflight): Kennung aus dem Paket, nie aus dem Dateinamen
    if info is not None and t.file is not None:
        v = fm.name_version(Path(t.file).name)
        if v is not None and pk.is_stock_identity(info.product) and v != int(info.product[-3:]):
            p.blockers.append(f"Der Dateiname „{Path(t.file).name}“ sagt V{v}, das Paket selbst ist aber "
                              f"{info.product} (V{int(info.product[-3:])}). Die App richtet sich nur nach dem Inhalt – "
                              "diese Datei wird nicht installiert. Bitte die Datei richtig benennen oder neu laden.")
    lchange = layout_change(state, t)
    if lchange and not state.identity and not state.felucca:
        # z. B. Melodee, deren Erkennung noch läuft: sonst sähe der Plan „kein Backup möglich“ statt der Backup-Pflicht
        p.blockers.append("Die laufende Firmware ist (noch) nicht erkannt. Für den Wechsel zwischen V16 und dem "
                          "V15-Aufbau muss die App wissen, was gerade läuft – bitte warten, bis das FM-1 oben erkannt "
                          "ist, und den Plan neu öffnen. Ohne Erkennung: zuerst die Original-V15 installieren.")
    if lchange and state.felucca and backup is False:
        p.blockers.append("Für den Wechsel zwischen V16 und dem V15-Aufbau ist ein Backup Pflicht: Dabei werden "
                          "Bereiche überschrieben, in denen deine Projekte und Sounds liegen. Bitte „Vorher ein Backup "
                          "machen“ anhaken.")

    mel = melodee_change(state, t)
    if mel == "up16" and state.felucca and backup is False:
        p.blockers.append(m16.BACKUP_REQUIRED_16)

    # ---- normaler Ablauf
    if state.felucca and backup is not False:
        p.steps.append(Step("backup", "Backup des Geräts", "keins",
                            "Alle Projekte, Sounds und Einstellungen werden vorher gesichert (nur lesen). "
                            "Klappt das Backup nicht, bricht die Installation ab."))
    elif state.felucca:
        p.warnings.append("Ohne Backup: Projekte, Sounds und Einstellungen auf dem Gerät werden vorher NICHT "
                          "gesichert.")
        p.confirmations.append("no_backup")
    else:
        p.warnings.append("Kein Backup möglich: Die laufende Firmware hat kein Editor-Protokoll, die App kann von "
                          "ihr nichts sichern. Wichtige Projekte vorher mit dem Werkzeug der Firmware sichern.")
        p.confirmations.append("no_backup")
    p.steps.append(Step("fetch", "Paket laden & Prüfsumme prüfen", "keins",
                        "Paket herunterladen (oder vorhandene Datei nehmen) und gegen die Prüfsumme prüfen."))
    p.v15_option = v15_option(state, t)
    if p.v15_option == "locked" and v16_switch(state, t):
        p.v15_reason = ("Pflicht: V16 geht nur von der Original-Firmware aus – deine jetzige Firmware würde V16 mit "
                        "„FAILED -5“ ablehnen." if t.is_v16 else
                        "Pflicht: Der Rückweg von V16 führt über die Original-V15 (derselbe Update-Loader wie V16).")
    default = p.v15_option in ("locked", "default_on")
    p.via_stock = p.v15_option == "locked" or (p.v15_option != "none" and (default if via_v15 is None else via_v15))
    if p.via_stock:
        why = (("V16 lässt sich nur von der Original-Firmware aus installieren: Deine jetzige Firmware vergleicht vor "
                "dem Original-Loader den Bootkopf und würde V16 mit „FAILED -5“ ablehnen." if t.is_v16 else
                "Der Rückweg von V16 geht über die Original-V15 – sie nutzt denselben Update-Loader wie V16.")
               if p.v15_option == "locked" and v16_switch(state, t) else
               "Diese Firmware wird laut Katalog von der Original-Firmware V15 aus installiert."
               if p.v15_option == "locked" else
               "Firmware-Wechsel: Über die Original-V15 startet die neue Firmware von einem sauberen, bewährten "
               "Stand – das vermeidet Flüchtigkeitsfehler beim schnellen Hin- und Herwechseln.")
        p.steps.append(Step("stock", "Zwischenschritt: Original-V15 aufspielen", "hoch",
                            why + " Die App spielt die unveränderte V15 (Prüfsumme db1642b2…) über M-VAVEs eigenen "
                            "Loader auf und prüft danach, ob das FM-1 „FM-1_015“ meldet.", writes=True))
        if stock_package is not None and not stock_package.official == "FM-1_015":
            p.blockers.append("Die bereitgelegte V15-Datei ist nicht die unveränderte Original-Firmware.")
        p.warnings.append("Deine Projekte auf dem Gerät können beim Zwischenschritt verloren gehen – die App sichert "
                          "sie vorher." if state.felucca else
                          "Deine Projekte auf dem Gerät können beim Zwischenschritt verloren gehen – von dieser "
                          "Firmware kann die App sie nicht sichern.")
        if state.felucca:
            p.warnings.append(TRAP_TEXT)
    if t.is_stock and state.felucca and not p.via_stock:
        p.warnings.append(TRAP_TEXT)
    if t.is_v16:
        p.warnings.insert(0, "<b>V16 (experimentell):</b> " + V16_WARNING)
        p.confirmations.append("v16")
    if lchange:
        p.lost += fm.describe_loss(state.identity, state.firmware_key, t.product or "")
        if not t.is_v16 and info is not None and info.flash_bytes > fm.V15_END:
            p.lost.append(f"Dieses Paket schreibt bis {fm.hx(info.flash_bytes - 1)} – über 0x92FFF hinaus, wo die "
                          "Firmwares mit V15-Aufbau (auch Melodee) Songs, Presets und Sounds speichern.")
    p.steps.append(Step("install", f"{t.name} aufspielen", "hoch" if _not_resumable(t, fam) else "mittel",
                        "Die laufende Firmware startet den Update-Loader aus dem Paket, der Loader schreibt die "
                        "Firmware. Nicht abziehen, nicht ausschalten.", writes=True))
    _tail(p, t)

    # ---- Warnungen und Bestätigungen
    cur = (state.firmware_key or "").lower()
    if cur and t.key and cur != t.key or (state.identity and t.product and state.identity != t.product
                                          and not t.key):
        p.lost.append("Projekte, Sounds und Einstellungen der jetzigen Firmware sind danach nicht mehr nutzbar "
                      "(das Backup bleibt auf dem Rechner)." if state.felucca else
                      "Projekte, Sounds und Einstellungen der jetzigen Firmware sind danach nicht mehr nutzbar.")
    elif not cur and state.identity and not state.update_mode and not lchange:
        p.lost.append("Welche Firmware gerade läuft, ist unbekannt – ist es eine andere als die neue, sind deren "
                      "Projekte, Sounds und Einstellungen danach nicht mehr nutzbar.")
    if t.is_stock:
        p.lost.append("Die Original-Firmware kann Daten der bisherigen Firmware überschreiben.")
        if state.felucca or (state.identity and not pk.is_stock_identity(state.identity)):
            p.warnings.append("Nach einer Felucca-Firmware liest die Original-Firmware deren Reste als eigene "
                              "Einstellungen: Presets können verstümmelt sein. Zuerst am FM-1 System > Reset Patches "
                              "probieren; hilft das nicht: Rettung > „Einstellungen nach Rückkehr reparieren“.")
    if "beta" in t.notes or t.prerelease:
        p.warnings.append("Testversion (Beta / Release Candidate): Projekte lassen sich evtl. nicht in ältere "
                          "Versionen laden.")
        p.confirmations.append("beta")
    if "projectsNotBackwardCompatible" in t.notes:
        p.warnings.append("Projekte dieser Version lassen sich nicht in ältere Versionen zurückladen.")
    _melodee16(p, state, t, mel)
    if any(b in (t.key or "").lower() or b in (t.family or "").lower() for b in BOOT_CHANGERS):
        p.warnings.append("Diese Installation ändert den Bootbereich des FM-1. Danach lehnen viele Felucca-Firmwares "
                          "(auch Melodee und SLOOP) die Rückkehr zur Original-V15 ab („FAILED -5“). Der Rückweg geht "
                          "dann nur über den Download-Modus (Rettung Stufe 2 / M-Flasher).")
    if "needsMiniLab3" in t.notes:
        p.warnings.append("Diese Firmware ist für die Bedienung mit einem Arturia MiniLab 3 gedacht.")
    if _felucca09_risk(state, t):
        p.warnings.append("Wechsel von Felucca 0.9 auf 1.x: Bekannt ist, dass alte 0.9-Daten den Start von 1.0.x "
                          "verhindern (schwarzer Bildschirm, ROM-UBOOT). Backup ist Pflicht. Abhilfe in der "
                          "Rettung (Stufe 2): Datenbereiche 0x97000 und 0xFC000 löschen – das löscht Songs und "
                          "Einstellungen.")
        p.confirmations.append("felucca09")
        if "no_backup" in p.confirmations:
            p.blockers.append("Für Felucca 0.9 → 1.x ist ein Backup Pflicht – ohne Backup geht es hier nicht.")
    if not t.checksum and t.known:
        p.warnings.append("Prüfsumme nicht verfügbar: Dieses Release hat keine SHA256SUMS-Datei, und der Katalog "
                          "kennt die Prüfsumme nicht. Die App kann nicht prüfen, ob die Datei unverändert ist.")
        p.confirmations.append("no_checksum")
    if not t.known:
        p.warnings.append(f"Eigene Datei: Kennung {info.product if info else '?'}, SHA-256 "
                          f"{info.sha256 if info else '?'} – in keinem Katalog.")
        p.confirmations.append("unknown_file")
    _confirm_power(p, t, fam)
    return p


def target_version(t: Target) -> str:
    """Version des Ziels: Release-Tag, sonst aus dem Namen (z. B. „melodee-1.6.0.fwsc“)"""
    return t.tag or (t.name if m16.version_key(t.name) else "")


def melodee_change(state: DeviceState, t: Target) -> str:
    """Wechsel über die Grenze von Melodee 1.6 (RELEASE_1.6.0.md „Upgrading“)?
    up16: Melodee < 1.6 -> Melodee ≥ 1.6 · down16: Melodee ≥ 1.6 -> ältere Melodee oder andere Firmware ·
    within: Melodee -> Melodee, ohne die Grenze zu kreuzen · '' sonst / unbekannt"""
    cur = (state.firmware_key or "").lower() == "melodee" or m16.is_melodee(state.version)
    if not cur or state.update_mode:
        return ""
    tv = target_version(t)
    to_mel = (t.key or "").lower() == "melodee"
    if m16.is_16(state.version):
        if not to_mel or (tv and m16.before_16(tv)):
            return "down16"
        return "within"
    if to_mel and m16.before_16(state.version) and m16.is_16(tv):
        return "up16"
    return "within" if to_mel else ""


def _melodee16(p: InstallPlan, state: DeviceState, t: Target, mel: str):
    tv = target_version(t)
    if mel == "up16":
        p.warnings.insert(0, m16.UPGRADE_16)
        if t.resources:
            p.warnings.append(m16.RESOURCES_FROM_15)
    if mel in ("up16", "within") and m16.new_levels(state.version, tv):
        p.warnings.append(m16.LEVELS_160)
    if mel == "down16":
        name = f"{t.name} {tv}".strip() if tv and tv.lower() not in t.name.lower() else t.name
        p.warnings.insert(0, m16.downgrade_text(name))
        if m16.DOWNGRADE_LOST not in p.lost:
            p.lost.append(m16.DOWNGRADE_LOST)
        p.confirmations.append("melodee16_down")


def _tail(p: InstallPlan, t: Target):
    if t.resources:
        p.steps.append(Step("resources", "Ressourcen schreiben", "niedrig",
                            "Schriften, Werkssounds und Bluetooth-Programm (Melodee ab 1.6). Scheitert das, läuft "
                            "die Firmware trotzdem – es lässt sich später nachholen.", writes=True))
    p.steps.append(Step("verify", "Neustart abwarten & Identität prüfen", "keins",
                        f"Das FM-1 startet neu und muss danach „{t.product}“ melden." if t.product else
                        "Das FM-1 startet neu und muss sich danach mit der neuen Firmware melden."))


def _not_resumable(t: Target, fam: str) -> bool:
    return "powerLossNotResumable" in t.notes or fam not in ("stock", "felucca")


def _confirm_power(p: InstallPlan, t: Target, fam: str):
    if _not_resumable(t, fam) or p.via_stock or t.is_stock:
        p.warnings.append("Ein Stromausfall während des Schreibens lässt sich hier NICHT fortsetzen – das FM-1 "
                          "bliebe dann schwarz (nur die Rettung Stufe 2 hilft)." if _not_resumable(t, fam) else
                          "Die Original-Firmware hat keinen Startschutz: Während des Schreibens darf der Strom "
                          "nicht ausfallen.")
        if "power" not in p.confirmations:
            p.confirmations.append("power")


def _felucca09_risk(state: DeviceState, t: Target) -> bool:
    v = (state.version or "").lower()
    if not ("felucca" in v and re.search(r"(?<![\d.])0\.9", v)):
        return False
    tv = (t.tag or t.name or "").lower()
    return (t.key == "felucca" or "felucca" in tv) and bool(re.search(r"(?<![\d.])1\.\d", tv))
