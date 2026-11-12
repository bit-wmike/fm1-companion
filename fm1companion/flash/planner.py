# SPDX-License-Identifier: GPL-3.0-only
"""Installation plan: which steps in which order, with risk, warnings and locks.

plan_install(device_state, target) is pure (no MIDI, no network) and fully tested.
Rules and rationale: docs/flash-protocol.md.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import flashmodel as fm, package as pk, texts as rescue_texts
from .. import fwfamily, melodee16 as m16
from ..i18n import _

TRAP_TEXT = _("When switching to the original firmware, the running Felucca firmware compares the boot area "
              "(CHECK HEAD). If it differs – e.g. after m-bootloader or M-VAVE Open V12/V14 –, the FM-1 shows "
              "“FAILED -5” (Felucca 1.6.1 copies: -15). Nothing is written, the current firmware keeps running. "
              "Ways out: a Felucca firmware with the new check (felucca-bsp afd83ede) or download mode "
              "(recovery stage 2, M-Flasher).")
BOOT_CHANGERS = ("m-bootloader", "mbootloader", "open", "fm1open")

RISK_TEXT = {"keins": _("no risk"), "niedrig": _("low risk"), "mittel": _("medium risk"), "hoch": _("high risk")}

# confirmations the user must give explicitly
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
    """what the app knows about the connected FM-1"""
    identity: str = ""            # handshake identity, e.g. "FM-1_91060", "ota-FM-1_900"
    felucca: bool = False         # Felucca protocol usable (backup possible)
    firmware_key: str = ""        # e.g. "melodee"
    firmware_name: str = ""       # display name of the running firmware, e.g. "Melodee" ("" = unknown)
    version: str = ""             # e.g. "Melodee 1.6.0-rc.7"
    port: str = ""
    connected: bool = True
    ports: int = 1                # how many FM-1 (including in update mode) are connected
    busy: bool = False            # another installation is running
    pinned: bool = False          # target device unambiguously pinned by USB connection/serial number
    usb_notes: list = field(default_factory=list)   # USB hub / careful mode / autosuspend (flash/robust.plan_notes)

    @property
    def update_mode(self) -> bool:
        return self.identity.lower().startswith("ota-")

    @property
    def open_firmware(self) -> bool:
        return bool(pk.OPEN_ID_RE.match(self.identity))


@dataclass
class Target:
    """what is to be installed"""
    name: str
    product: str = ""             # expected identity (from package or catalog)
    sha256: str = ""              # expected checksum ("" = unknown)
    family: str = ""              # loader family "stock"|"felucca"|"" (unknown)
    requires_stock_v15: bool = False
    notes: list[str] = field(default_factory=list)
    resources: bool = False       # Melodee 1.6+: write resources after the firmware
    key: str = ""                 # FirmwareSpec.key
    tag: str = ""
    file: Path | None = None
    package: pk.PackageInfo | None = None    # if the file is already present and inspected
    known: bool = True            # from catalog/builtin; False = own file
    prerelease: bool = False
    checksum: bool = True         # False: package cannot be checked against any checksum (confirmation needed)

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
    risk: str                     # keins | niedrig | mittel | hoch  (none | low | medium | high)
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
    lost: list[str] = field(default_factory=list)    # what is gone afterwards
    via_stock: bool = False
    resume: bool = False
    v15_option: str = "none"      # none | default_on | locked (intermediate step V15; see v15_option())
    v15_reason: str = ""          # why the V15 step is in the plan (shown next to the expert option)
    direct: bool = False          # expert opt-out: a firmware switch without the V15 step
    chain_resume: bool = False    # continues a switch whose V15 step is already done (journal "chain")

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
    """Locks for an available package (checksum, model, integrity, loader)"""
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


NO_FIRMWARE_KEYS = ("", "eigene", "unknown", "?")   # own files and "don't know" name no firmware


def fw_identity(key: str) -> str:
    """the catalog key a firmware key stands for ("jiant" -> "jiant-fm"; own files and "unknown" -> "")"""
    k = (key or "").strip().lower()
    if k in NO_FIRMWARE_KEYS or k.startswith("__"):
        return ""
    f = fwfamily.by_key(k)
    return f.key if f is not None else k


def running_firmware(state: DeviceState) -> str:
    """the firmware that runs on the device as a catalog key – "" when it is not known for sure. The recorded key
    (profile, INFO, the user's choice) counts only if the INFO answer does not name another firmware: a fork that
    names itself ("FELUCCA KARMA 3.1") is never its parent, and a stale profile is no proof."""
    if pk.is_stock_identity(state.identity):
        return "stock-v" + state.identity[-2:] if state.identity[-3:].isdigit() else ""
    cur = fw_identity(state.firmware_key)
    named = fwfamily.by_info(state.version) if state.version else None
    if named is not None:
        if cur and cur != named.key:
            return ""
        return named.key
    return cur


def same_firmware(state: DeviceState, t: Target) -> bool:
    """an update (or downgrade) within the firmware that runs now – the only case without the V15 step"""
    if t.is_stock:
        return False
    cur, tk = running_firmware(state), fw_identity(t.key)
    return bool(cur and tk and cur == tk)


def v15_option(state: DeviceState, t: Target) -> str:
    """Intermediate step via stock V15 (policy: "always via V15 first – except updates within the same firmware"):
    none (update/downgrade of the same firmware – even when the catalog requires V15 –, the device already runs V15,
    the target is stock itself, update mode), locked (V16 switch, or a switch to a firmware the catalog requires V15
    for) or default_on (every other switch: from another custom
    firmware, from stock V13/V14/V16 or from an unknown firmware – the expert can opt out)."""
    if state.identity == "FM-1_015" or state.update_mode:
        return "none"
    if v16_switch(state, t):
        return "locked"
    if t.is_stock:
        return "none"
    if same_firmware(state, t):              # (also with the catalog's requiresStockV15: the device already runs this
        return "none"                        # firmware and its own update loader)
    if t.requires_stock_v15:
        return "locked"
    return "default_on"


def current_name(state: DeviceState) -> str:
    """what the plan calls the running firmware ("" = not recognised)"""
    if not running_firmware(state):
        return ""
    stock = f"V{int(state.identity[-3:])}" if pk.is_stock_identity(state.identity) else ""
    return stock or state.firmware_name or state.firmware_key or state.version


def runs_v16(state: DeviceState) -> bool:
    return state.identity == "FM-1_016"


def v16_switch(state: DeviceState, t: Target) -> bool:
    """Switch between V16 and V15 layout that only works via stock V15:
    - Target V16 from a firmware that is not the stock firmware: Melodee & co. compare the package's boot header
      with the device's before handing over to the stock loader (melodee firmware/src/ota.c, "CHECK HEAD"). V16 has a
      different boot header than the factory header (V13–V15) – that would give "FAILED -5". [Pkg]
    - From V16 to anything other than V15: whether V16 accepts the loader of a community firmware is unknown; the path
      via V15 uses the same stock loader in both directions. [Assumption, cautious]"""
    if t.is_v16:
        return not runs_v16(state) and not pk.is_stock_identity(state.identity)
    return runs_v16(state) and t.product != "FM-1_015" and t.key != "stock-v15"


def v16_layout(t: Target) -> bool:
    """Target uses the V16 layout: identity/catalog say V16 – or the available package writes beyond 0x92FFF
    (content counts, not name or identity: so a renamed or relabelled package is caught too)"""
    return t.is_v16 or bool(t.package is not None and t.package.flash_bytes > fm.V15_END)


def layout_change(state: DeviceState, t: Target) -> bool:
    return v16_layout(t) != runs_v16(state)


def plan_install(state: DeviceState, target: Target, journal_active: dict | None = None,
                 stock_package: pk.PackageInfo | None = None, via_v15: bool | None = None,
                 backup: bool | None = None, chain: dict | None = None) -> InstallPlan:
    """backup: None/True = back up first (if the firmware supports it), False = explicitly without backup.
    via_v15: None = the policy; False = the expert opt-out (only for a switch with v15_option "default_on").
    chain: the journal's interrupted two-step switch ({"key": …}), if any."""
    p = InstallPlan(target=target, state=state)
    t = target
    info = t.package
    if info is not None:
        if not t.family:
            t.family = info.family
        if not t.product:
            t.product = info.product
    fam = t.family if t.family in ("stock", "felucca") else (info.family if info else "")

    # ---- locks
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
    p.warnings += [n for n in state.usb_notes or [] if n]     # USB hub, careful mode, autosuspend

    # ---- update mode: only continue if the waiting loader matches the package
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

    # ---- pre-check (flashmodel.preflight): identity from the package, never from the file name
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
        # e.g. Melodee whose detection is still running: otherwise the plan would show "no backup possible" instead of the mandatory backup
        p.blockers.append(_("The running firmware is not recognized (yet). For the switch between V16 and the V15 "
                            "layout the app must know what is running – please wait until the FM-1 is recognized at "
                            "the top, and open the plan again. Without recognition: install the original V15 first."))
    if lchange and state.felucca and backup is False:
        p.blockers.append(_("A backup is mandatory for the switch between V16 and the V15 layout: it overwrites "
                            "areas where your projects and sounds are stored. Please tick “Make a backup first”."))

    mel = melodee_change(state, t)
    if mel == "up16" and state.felucca and backup is False:
        p.blockers.append(m16.BACKUP_REQUIRED_16)

    # ---- normal flow
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
    if p.v15_option == "default_on":
        was = current_name(state)
        p.v15_reason = (_("Switching from {current} to {target}: the app installs the original firmware V15 first, "
                          "then {target} – recommended, so no leftovers of {current} stay on the FM-1.").format(
                              current=was, target=t.name) if was else
                        _("The running firmware is not recognised, so this counts as a switch: the app installs the "
                          "original firmware V15 first, then {target} – recommended, so no leftovers of the previous "
                          "firmware stay on the FM-1.").format(target=t.name))
    p.via_stock = p.v15_option == "locked" or (p.v15_option == "default_on" and via_v15 is not False)
    p.direct = p.v15_option == "default_on" and not p.via_stock
    if p.direct:
        was = current_name(state) or _("the previous firmware")
        p.warnings.insert(0, _("<b>Expert option: installed directly, without the V15 step.</b> Leftovers of {current} "
                               "(settings, projects, sample areas) can stay in the flash and confuse {target}. If "
                               "{target} behaves strangely afterwards, install it again with the V15 step.").format(
                                   current=was, target=t.name))
    if chain and state.identity == "FM-1_015" and fw_identity(chain.get("key", "")) == fw_identity(t.key) and t.key:
        p.chain_resume = True
        p.warnings.insert(0, _("Continues the interrupted switch to {target}: the original firmware V15 is already "
                               "installed (step 1 of 2 is done) – now {target} is installed directly.").format(
                                   target=t.name))
    if p.via_stock:
        why = ((_("V16 can only be installed from the original firmware: your current firmware compares the boot "
                  "header before the original loader and would refuse V16 with “FAILED -5”.") if t.is_v16 else
                _("The way back from V16 goes via the original V15 – it uses the same update loader as V16."))
               if p.v15_option == "locked" and v16_switch(state, t) else
               _("According to the catalog, this firmware is installed from the original firmware V15.")
               if p.v15_option == "locked" else p.v15_reason)
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
        p.lost += [rescue_texts.tr(x) for x in fm.describe_loss(state.identity, state.firmware_key, t.product or "")]
        if not t.is_v16 and info is not None and info.flash_bytes > fm.V15_END:
            p.lost.append(_("This package writes up to {end} – beyond 0x92FFF, where the firmwares with the V15 "
                            "layout (Melodee too) store songs, presets and sounds.").format(end=fm.hx(info.flash_bytes - 1)))
    p.steps.append(Step("install", _("Install {name}").format(name=t.name), "hoch" if _not_resumable(t, fam) else "mittel",
                        _("The running firmware starts the update loader from the package, the loader writes the "
                          "firmware. Do not unplug, do not switch off."), writes=True))
    _tail(p, t)

    # ---- warnings and confirmations
    cur = fw_identity(state.firmware_key)
    if cur and t.key and cur != fw_identity(t.key) or (state.identity and t.product and state.identity != t.product
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
    """Version of the target: release tag, otherwise from the name (e.g. "melodee-1.6.0.fwsc")"""
    return t.tag or (t.name if m16.version_key(t.name) else "")


def melodee_change(state: DeviceState, t: Target) -> str:
    """Switch across the Melodee 1.6 boundary (RELEASE_1.6.0.md "Upgrading")?
    up16: Melodee < 1.6 -> Melodee ≥ 1.6 · down16: Melodee ≥ 1.6 -> older Melodee or other firmware ·
    within: Melodee -> Melodee without crossing the boundary · '' otherwise / unknown"""
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
