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
from ..i18n import N_, _

# fixed sentences of flashmodel.describe_loss() (flashmodel.py is pinned and stays German) -> UI language
_LOSS_TEXT = {
    "Alles, was du unter V16 gespeichert hast (Patterns, Einstellungen), ist danach nicht mehr nutzbar – "  # i18n: ignore
    "die App kann von V16 kein Backup machen.":
        N_("Everything you saved under V16 (patterns, settings) can no longer be used afterwards – "
           "the app cannot make a backup of V16."),
    "V15 und Community-Firmwares schreiben nur bis 0x92FFF: Reste von V16 in 0x93000–0xA8FFF bleiben "  # i18n: ignore
    "liegen. Melodee 1.5.1 lief damit im Emulator wie auf sauberem Flash; prüfen kannst du es mit "
    "„Ist mein FM-1 sauber?“ (Rettung).":
        N_("V15 and community firmwares only write up to 0x92FFF: leftovers of V16 in 0x93000–0xA8FFF remain. "
           "Melodee 1.5.1 ran with them in the emulator as on a clean flash; you can check it with "
           "“Is my FM-1 clean?” (Recovery)."),
}


_LOSS_WHAT = {"Songs, Presets und Einstellungen": N_("songs, presets and settings"),  # i18n: ignore
              "weitere Song- und Preset-Bereiche": N_("further song and preset areas")}  # i18n: ignore
_LOSS_RE = [
    (re.compile(r"^Beim Schreiben von V16: der Bereich 0x93000–0xA8FFF \(22 Sektoren\) – bei dir: (?P<what>.+)\. "  # i18n: ignore
                r"So gut wie sicher: Der Update-Loader schreibt genau so weit, wie das Paket lang ist \(bei V15 an "  # i18n: ignore
                r"echter Hardware aufgezeichnet\)\.$"),
     N_("When writing V16: the area 0x93000–0xA8FFF (22 sectors) – on your device: {what}. As good as certain: the "
        "update loader writes exactly as far as the package is long (recorded with V15 on real hardware).")),
    (re.compile(r"^Beim ersten Start formatiert V16 zusätzlich (?P<ranges>.+?) – bei dir: (?P<what>.+)\. "  # i18n: ignore
                r"Beobachtet im Emulator, nicht an echter Hardware\. Die Ressourcen schreibt die App beim Rückweg auf "  # i18n: ignore
                r"Melodee neu\.$"),
     N_("At the first start V16 additionally formats {ranges} – on your device: {what}. Observed in the emulator, not "
        "on real hardware. The app rewrites the resources on the way back to Melodee.")),
    (re.compile(r"^Beim ersten Start formatiert V16 zusätzlich (?P<ranges>.+?) – bei dir: (?P<what>.+)\. "  # i18n: ignore
                r"Beobachtet im Emulator, nicht an echter Hardware\.$"),  # i18n: ignore
     N_("At the first start V16 additionally formats {ranges} – on your device: {what}. Observed in the emulator, not "
        "on real hardware.")),
]


def _loss_text(t: str) -> str:
    if t in _LOSS_TEXT:
        return _(_LOSS_TEXT[t])
    for rx, msg in _LOSS_RE:
        m = rx.match(t)
        if m:
            parts = m.groupdict()
            what = parts["what"]
            parts["what"] = _(_LOSS_WHAT[what]) if what in _LOSS_WHAT else what
            return _(msg).format(**parts)
    return t

TRAP_TEXT = _("When switching to the original firmware, the running Felucca firmware compares the boot area "
              "(CHECK HEAD). If it differs – e.g. after m-bootloader or M-VAVE Open V12/V14 –, the FM-1 shows "
              "“FAILED -5” (Felucca 1.6.1 copies: -15). Nothing is written, the current firmware keeps running. "
              "Ways out: a Felucca firmware with the new check (felucca-bsp afd83ede) or download mode "
              "(recovery stage 2, M-Flasher).")
BOOT_CHANGERS = ("m-bootloader", "mbootloader", "open", "fm1open")

RISK_TEXT = {"keins": _("no risk"), "niedrig": _("low risk"), "mittel": _("medium risk"), "hoch": _("high risk")}

# Bestätigungen, die der Nutzer ausdrücklich geben muss
CONFIRM_TEXT = {
    "unknown_file": _("I know what I am doing: this file is in no catalog. I have checked the identity and "
                      "checksum myself."),
    "power": _("The FM-1 is charged or on power, the cable is plugged directly into the computer, and the "
               "computer does not go to sleep during the installation."),
    "no_backup": _("I am not making a backup and accept that projects and sounds may be lost."),
    "beta": _("I understand that this is a test version (beta)."),
    "felucca09": _("I know that Felucca 0.9 data can prevent the start when switching to 1.x "
                   "(backup is mandatory; for the remedy see Recovery)."),
    "no_checksum": _("Checksum not available: I am installing this package although the app cannot check it "
                     "against any checksum (the release has no SHA256SUMS file)."),
    "melodee16_down": _("I understand: the older firmware cannot read projects, presets and backups from "
                        "Melodee 1.6. My way back is a backup from before the update to 1.6."),
    "v16": _("I understand: V16 is experimental and meant only with a backup. The way back from V16 to V15 has not "
             "yet been confirmed with real V16 – in the worst case only ROM boot mode helps (recovery stage 2)."),
}

V16_WARNING = _("Experimental – only with a backup. V16 uses a different flash layout than V15 and all community "
                "firmwares. Established: the update loader is the same in V15 and V16, and an FM-1 that reported as "
                "version 16 (a V15 derivative) could be reset to V15 with a normal update. With real V16, however, the "
                "way back has not yet been confirmed, and it is open whether the loader also rewrites the boot header. "
                "If the way back does not work, ROM boot mode helps (recovery stage 2). "
                "Recommendation: not on your main FM-1.")
MULTI_DEVICE_TEXT = _("Please leave only the FM-1 connected that you want to update.")


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
        out.append(_("The checksum of {name} does not match the catalog (expected {expected}…, file {actual}…). "
                     "The file is damaged or was replaced – it is not installed.").format(
                         name=info.path.name, expected=t.sha256[:12], actual=info.sha256[:12]))
    if info.model != "FM-1":
        out.append(_("The package is for “{model}”, not for the FM-1 ({product}).").format(
            model=info.model, product=info.product))
    if t.product and info.product != t.product and t.known:
        out.append(_("The package reports {product}, {expected} was expected.").format(
            product=info.product, expected=t.product))
    if info.damage:
        out.append(_("The package is damaged ({damage}). Please download it again.").format(damage=info.damage))
    if info.family not in ("stock", "felucca") and t.family not in ("stock", "felucca"):
        out.append(_("The app does not know the update loader of this package and therefore sends it to no "
                     "loader. Please use the author's installer."))
    if info.flash_bytes > 0xFF000:
        out.append(_("The package would write up to {end} – into the protected area from 0xFF000 (device ID, "
                     "Bluetooth address). No known FM-1 package does that; it is not installed.").format(
                         end=fm.hx(info.flash_bytes - 1)))
    if info.family == "stock" and not info.official:
        out.append(_("The package contains the original loader, but is not an unmodified original firmware "
                     "(V15/V16). Modified original packages are not installed."))
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
        p.blockers.append(_("An installation is already running. Please wait until it is finished."))
    if not state.connected:
        p.blockers.append(_("No FM-1 connected. Please connect it with a USB data cable and switch it on."))
    if state.open_firmware:
        p.blockers.append(_("This FM-1 runs the M-VAVE Open firmware. First return to the original firmware with "
                            "M-VAVE's own tool (FM1 Workbench), then install here."))
    if state.identity and not state.update_mode and not state.open_firmware and \
            not state.identity.startswith("FM-1_"):
        p.blockers.append(_("The connected device reports “{identity}” – that is not an FM-1.").format(
            identity=state.identity))
    if info is not None:
        p.blockers += check_package(t, info)
    if fam not in ("stock", "felucca") and info is None and not t.key:
        p.blockers.append(_("Unknown loader family."))
    if state.ports > 1 and not state.pinned:
        p.blockers.append(_("{please} Several FM-1 (or devices in update mode) are connected, and the app cannot "
                            "identify the target device unambiguously by USB port or serial number. After the "
                            "restart into update mode it could otherwise write to the wrong device.").format(
                                please=MULTI_DEVICE_TEXT))
    elif state.ports > 1:
        p.warnings.append(_("Several FM-1 are connected. The app pins the target device by its USB port – "
                            "please do not move any cable during the installation."))

    # ---- Update-Modus: nur fortsetzen, wenn der wartende Loader zum Paket passt
    if state.update_mode:
        rule = pk.LoaderRule(fam, t.product or (info.product if info else "")) if fam else None
        if rule is None or not rule(state.identity):
            hint = {"ota-FM-1_900": _("It is waiting for a package of the Felucca family (e.g. Melodee) – ideally "
                                      "exactly the one whose installation was interrupted."),
                    "ota-FM-1_015": _("It is waiting for the unmodified original firmware V15.")}
            h = hint.get(state.identity) or (_("It is waiting for the package whose installation was interrupted.")
                                            if pk.FELUCCA_LOADER_RE.match(state.identity) or
                                            pk.STOCK_LOADER_RE.match(state.identity) else
                                            _("The app does not know this loader."))
            p.blockers.append(_("The FM-1 is waiting in update mode ({identity}). This package does not belong to "
                                "this loader – nothing is sent. {hint}").format(identity=state.identity, hint=h))
        p.resume = True
        p.steps.append(Step("fetch", _("Load package & check checksum"), "keins",
                            _("Fetch the package and check it against the known checksum.")))
        if journal_active and journal_active.get("sha256") and info is not None \
                and journal_active["sha256"] != info.sha256:
            p.warnings.append(_("“{target}” was interrupted. The safest thing is to write exactly this package "
                                "again.").format(target=journal_active.get('target', '?')))
        p.steps.append(Step("resume", _("Finish the interrupted installation"), "mittel",
                            _("The waiting update loader reads the whole package once more and writes it. "
                              "Areas that are already correct are skipped."), writes=True))
        _tail(p, t)
        _confirm_power(p, t, fam)
        return p

    # ---- Vorprüfung (flashmodel.preflight): Kennung aus dem Paket, nie aus dem Dateinamen
    if info is not None and t.file is not None:
        v = fm.name_version(Path(t.file).name)
        if v is not None and pk.is_stock_identity(info.product) and v != int(info.product[-3:]):
            p.blockers.append(_("The file name “{name}” says V{name_version}, but the package itself is {product} "
                                "(V{version}). The app goes only by the content – this file is not installed. Please "
                                "name the file correctly or download it again.").format(
                                    name=Path(t.file).name, name_version=v, product=info.product,
                                    version=int(info.product[-3:])))
    lchange = layout_change(state, t)
    if lchange and not state.identity and not state.felucca:
        # z. B. Melodee, deren Erkennung noch läuft: sonst sähe der Plan „kein Backup möglich“ statt der Backup-Pflicht
        p.blockers.append(_("The running firmware is not recognized (yet). For the switch between V16 and the V15 "
                            "layout the app must know what is running – please wait until the FM-1 is recognized at "
                            "the top, and open the plan again. Without recognition: install the original V15 first."))
    if lchange and state.felucca and backup is False:
        p.blockers.append(_("A backup is mandatory for the switch between V16 and the V15 layout: it overwrites "
                            "areas where your projects and sounds are stored. Please tick “Make a backup first”."))

    mel = melodee_change(state, t)
    if mel == "up16" and state.felucca and backup is False:
        p.blockers.append(m16.BACKUP_REQUIRED_16)

    # ---- normaler Ablauf
    if state.felucca and backup is not False:
        p.steps.append(Step("backup", _("Backup of the device"), "keins",
                            _("All projects, sounds and settings are backed up first (read only). "
                              "If the backup fails, the installation is aborted.")))
    elif state.felucca:
        p.warnings.append(_("Without backup: projects, sounds and settings on the device are NOT backed up "
                            "first."))
        p.confirmations.append("no_backup")
    else:
        p.warnings.append(_("No backup possible: the running firmware has no editor protocol, the app cannot back "
                            "up anything from it. Back up important projects first with the firmware's own tool."))
        p.confirmations.append("no_backup")
    p.steps.append(Step("fetch", _("Load package & check checksum"), "keins",
                        _("Download the package (or take the existing file) and check it against the checksum.")))
    p.v15_option = v15_option(state, t)
    if p.v15_option == "locked" and v16_switch(state, t):
        p.v15_reason = (_("Mandatory: V16 only works from the original firmware – your current firmware would refuse "
                          "V16 with “FAILED -5”.") if t.is_v16 else
                        _("Mandatory: the way back from V16 leads via the original V15 (the same update loader as "
                          "V16)."))
    default = p.v15_option in ("locked", "default_on")
    p.via_stock = p.v15_option == "locked" or (p.v15_option != "none" and (default if via_v15 is None else via_v15))
    if p.via_stock:
        why = ((_("V16 can only be installed from the original firmware: your current firmware compares the boot "
                  "header before the original loader and would refuse V16 with “FAILED -5”.") if t.is_v16 else
                _("The way back from V16 goes via the original V15 – it uses the same update loader as V16."))
               if p.v15_option == "locked" and v16_switch(state, t) else
               _("According to the catalog, this firmware is installed from the original firmware V15.")
               if p.v15_option == "locked" else
               _("Firmware switch: via the original V15 the new firmware starts from a clean, proven state – "
                 "this avoids careless mistakes when switching back and forth quickly."))
        p.steps.append(Step("stock", _("Intermediate step: install original V15"), "hoch",
                            _("{why} The app installs the unmodified V15 (checksum db1642b2…) via M-VAVE's own "
                              "loader and then checks whether the FM-1 reports “FM-1_015”.").format(why=why),
                            writes=True))
        if stock_package is not None and not stock_package.official == "FM-1_015":
            p.blockers.append(_("The prepared V15 file is not the unmodified original firmware."))
        p.warnings.append(_("Your projects on the device can be lost in the intermediate step – the app backs them "
                            "up first.") if state.felucca else
                          _("Your projects on the device can be lost in the intermediate step – the app cannot back "
                            "them up from this firmware."))
        if state.felucca:
            p.warnings.append(TRAP_TEXT)
    if t.is_stock and state.felucca and not p.via_stock:
        p.warnings.append(TRAP_TEXT)
    if t.is_v16:
        p.warnings.insert(0, _("<b>V16 (experimental):</b> {warning}").format(warning=V16_WARNING))
        p.confirmations.append("v16")
    if lchange:
        p.lost += [_loss_text(x) for x in fm.describe_loss(state.identity, state.firmware_key, t.product or "")]
        if not t.is_v16 and info is not None and info.flash_bytes > fm.V15_END:
            p.lost.append(_("This package writes up to {end} – beyond 0x92FFF, where the firmwares with the V15 "
                            "layout (Melodee too) store songs, presets and sounds.").format(end=fm.hx(info.flash_bytes - 1)))
    p.steps.append(Step("install", _("Install {name}").format(name=t.name), "hoch" if _not_resumable(t, fam) else "mittel",
                        _("The running firmware starts the update loader from the package, the loader writes the "
                          "firmware. Do not unplug, do not switch off."), writes=True))
    _tail(p, t)

    # ---- Warnungen und Bestätigungen
    cur = (state.firmware_key or "").lower()
    if cur and t.key and cur != t.key or (state.identity and t.product and state.identity != t.product
                                          and not t.key):
        p.lost.append(_("Projects, sounds and settings of the current firmware can no longer be used afterwards "
                        "(the backup stays on the computer).") if state.felucca else
                      _("Projects, sounds and settings of the current firmware can no longer be used afterwards."))
    elif not cur and state.identity and not state.update_mode and not lchange:
        p.lost.append(_("Which firmware is running right now is unknown – if it is a different one than the new one, "
                        "its projects, sounds and settings can no longer be used afterwards."))
    if t.is_stock:
        p.lost.append(_("The original firmware can overwrite data of the previous firmware."))
        if state.felucca or (state.identity and not pk.is_stock_identity(state.identity)):
            p.warnings.append(_("After a Felucca firmware, the original firmware reads its leftovers as its own "
                                "settings: presets can be garbled. First try System > Reset Patches on the FM-1; "
                                "if that does not help: Recovery > “Repair settings after return”."))
    if "beta" in t.notes or t.prerelease:
        p.warnings.append(_("Test version (beta / release candidate): projects may not load in older "
                            "versions."))
        p.confirmations.append("beta")
    if "projectsNotBackwardCompatible" in t.notes:
        p.warnings.append(_("Projects of this version cannot be loaded back into older versions."))
    _melodee16(p, state, t, mel)
    if any(b in (t.key or "").lower() or b in (t.family or "").lower() for b in BOOT_CHANGERS):
        p.warnings.append(_("This installation changes the boot area of the FM-1. Afterwards many Felucca firmwares "
                            "(Melodee and SLOOP too) refuse the return to the original V15 (“FAILED -5”). The way "
                            "back then only goes via download mode (recovery stage 2 / M-Flasher)."))
    if "needsMiniLab3" in t.notes:
        p.warnings.append(_("This firmware is meant to be operated with an Arturia MiniLab 3."))
    if _felucca09_risk(state, t):
        p.warnings.append(_("Switch from Felucca 0.9 to 1.x: old 0.9 data is known to prevent 1.0.x from starting "
                            "(black screen, ROM-UBOOT). Backup is mandatory. Remedy in recovery (stage 2): erase the "
                            "data areas 0x97000 and 0xFC000 – this erases songs and settings."))
        p.confirmations.append("felucca09")
        if "no_backup" in p.confirmations:
            p.blockers.append(_("For Felucca 0.9 → 1.x a backup is mandatory – without a backup it is not possible here."))
    if not t.checksum and t.known:
        p.warnings.append(_("Checksum not available: this release has no SHA256SUMS file, and the catalog does not "
                            "know the checksum. The app cannot check whether the file is unmodified."))
        p.confirmations.append("no_checksum")
    if not t.known:
        p.warnings.append(_("Own file: identity {identity}, SHA-256 {sha256} – in no catalog.").format(
            identity=info.product if info else '?', sha256=info.sha256 if info else '?'))
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
        p.steps.append(Step("resources", _("Write resources"), "niedrig",
                            _("Fonts, factory sounds and Bluetooth program (Melodee 1.6 and later). If this fails, "
                              "the firmware still runs – it can be done later."), writes=True))
    p.steps.append(Step("verify", _("Wait for restart & check identity"), "keins",
                        _("The FM-1 restarts and must then report “{product}”.").format(product=t.product) if t.product
                        else _("The FM-1 restarts and must then report with the new firmware.")))


def _not_resumable(t: Target, fam: str) -> bool:
    return "powerLossNotResumable" in t.notes or fam not in ("stock", "felucca")


def _confirm_power(p: InstallPlan, t: Target, fam: str):
    if _not_resumable(t, fam) or p.via_stock or t.is_stock:
        p.warnings.append(_("A power loss while writing can NOT be resumed here – the FM-1 would then stay black "
                            "(only recovery stage 2 helps).") if _not_resumable(t, fam) else
                          _("The original firmware has no start protection: the power must not fail while "
                            "writing."))
        if "power" not in p.confirmations:
            p.confirmations.append("power")


def _felucca09_risk(state: DeviceState, t: Target) -> bool:
    v = (state.version or "").lower()
    if not ("felucca" in v and re.search(r"(?<![\d.])0\.9", v)):
        return False
    tv = (t.tag or t.name or "").lower()
    return (t.key == "felucca" or "felucca" in tv) and bool(re.search(r"(?<![\d.])1\.\d", tv))
