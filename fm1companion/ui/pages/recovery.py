# SPDX-License-Identifier: GPL-3.0-only
"""Rescue: FM-1 doesn't start, hangs in update mode or an installation was interrupted.

Level 1 – update mode via USB MIDI (journal, correct package, own loader).
Level 2 – ROM boot mode “WL82 UBOOT1.00” (MvaveFM1Unbricker + jl-uboot-tool, root via pkexec).
Level 3 – FM-1 Transporter (hardware: RP2040 board on the USB data lines).
Background and sources: docs/flash-protocol.md.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (QCheckBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QPushButton,
                             QScrollArea, QVBoxLayout, QWidget)

from . import PageSpec
from ... import firmware
from ...flash import engine as fe, journal as fj, package as fpk, tested, texts as rescue_texts, uboot
from ..common import Card, Disclosure, confirm, heading, icon, show_error
from ...i18n import _, pgettext

DEMO = os.environ.get("FM1C_DEMO") == "1"
ONE_UBOOT = _("Exactly one device “WL82 UBOOT1.00” (USB 4c4a:8057) must be connected. The app never chooses a drive "
              "by itself.")

BASICS_SHORT = _("<b>First:</b> charge the battery for at least one hour, use a USB <b>data</b> cable directly on the "
                 "computer (no hub), close other MIDI programs and browser tabs.")
BASICS = _("<b>The basics first:</b><ul>"
           "<li>Charge the battery for at least one hour – a deeply discharged battery can prevent the start.</li>"
           "<li>Try another USB <b>data</b> cable (not a charge-only cable), another USB port directly on the "
           "computer (no hub) or another computer.</li>"
           "<li>Close other music programs and browser tabs with MIDI.</li></ul>")

STEPS_UPDATE = _("<ol><li>Switch the FM-1 off.</li>"
                 "<li>Hold <b>OCT−</b> pressed while switching it on.</li>"
                 "<li><b>Three short flashes</b> of the key lighting = update mode. One long flash = ROM boot mode "
                 "→ stage 2.</li>"
                 "<li>Plug in the USB cable – the app detects the device here immediately.</li></ol>")
DETAILS_UPDATE = _("The OCT− key combination is documented for Melodee 1.3.5 and later, TransitFM1 and Felucca "
                   "firmwares with start protection.<br>According to the community (not confirmed): hold OCT− and OCT+ "
                   "together for 10 seconds. Caution: with Melodee, OCT−+OCT+ is the calibration. Do not switch off and "
                   "on twice in a row while the FM-1 is in update mode (TransitFM1: the second time ends up in "
                   "ROM boot mode).<br>The update loader of the M-VAVE FM1 Open reports as “ota-FM1C_…” – the app "
                   "sends it nothing (use the FM1 Open updater from M-VAVE for that).<br>"
                   "“Finish installation” knows the interrupted package from the app's journal and checks beforehand "
                   "whether the waiting update loader belongs to this package – otherwise nothing is sent.")

STEPS_UBOOT = _("<ol><li>Once: “Fetch tool” (jl-uboot-tool, is checked).</li>"
                "<li>Connect the fully charged FM-1 directly to the computer – below it must say “Found”.</li>"
                "<li>“Write back original V15” – the app first backs up the whole flash twice and checks the boot "
                "header on this backup: it only writes under the factory header.</li>"
                "<li>Switch the FM-1 off and on again.</li></ol>"
                "<small>A session in ROM boot mode usually lasts only <b>one</b> tool run. After that (also after "
                "a refusal) switch the FM-1 off and on and bring it into ROM boot mode again.</small>")
DETAILS_UBOOT = _("The app uses the bundled <a href='https://github.com/Quixotic7/MvaveFM1Unbricker'>"
                  "MvaveFM1Unbricker</a> (MIT) with kagaimiq's jl-uboot-tool. It backs up the whole flash twice and "
                  "compares, checks chip key 0x980F and flash ID 0x856014, writes <b>only 0x4000–0x92FFF</b> from the "
                  "original V15 and reads everything back. Below 0x4000 it never writes, nothing is erased completely. "
                  "<br><b>Boot header:</b> before writing, the app checks the boot header (0x0000–0x3FFF) on the backup "
                  "from the same run. Only under the factory header (V13–V15) is V15 written. If the device has the "
                  "header of V16 (M-VAVE's updater rewrites it for V16) or an unknown one (e.g. m-bootloader), "
                  "nothing is written: whether V15 starts under it has not been established, and this tool cannot "
                  "write the V16 app (0x4000–0xA8FFF). Then use M-Flasher (below) or "
                  "<a href='https://github.com/hugelton/felucca-bsp'>fm1_rescue.py</a> from felucca-bsp – it "
                  "chooses the package to match the header. The app never writes the header.<br>"
                  "Needs administrator rights (pkexec) and the kernel module <tt>sg</tt> (USB 4c4a:8057).<br>"
                  "<b>Honestly:</b> in this app only tested in the simulation; the tool itself has been used by the "
                  "community on real devices.<br><br>"
                  "<b>Alternative: <a href='https://github.com/jvitkauskas/m-flasher'>M-Flasher</a></b> (MIT, separate "
                  "program with a flash map; writes only what changes, and checks everything). Download mode: running "
                  "original or Felucca FM-1 via the “DOWNLOAD MODE” button; Felucca/SLOOP: hold OCT− and OCT+ for 5 s; "
                  "with m-bootloader: REC while switching on. For the Pi there is an ARM64 package "
                  "(<tt>sudo apt install ./m-flasher_…_arm64.deb</tt>).<br><br>"
                  "<small>Felucca 0.9 → 1.0.x (known): 1.0.x does not start because of old 0.9 data. Remedy according "
                  "to the instructions: erase the data areas 0x97000 (32 KiB) and 0xFC000 (8 KiB) – this erases songs "
                  "and settings. For this the app does “Repair settings” with a factory dump (a backup is mandatory "
                  "here). This only works if the backup from the same run shows the V15 layout (firmware ends at "
                  "0x93000) – with V16 there is still firmware in 0x93000–0xA8FFF, then nothing is written."
                  "</small><br><br>"
                  "<b>If the FM-1 does not appear as “WL82 UBOOT1.00” at all</b> (after MvaveFM1Unbricker, "
                  "Troubleshooting): 1. <b>Leave it switched on for two minutes</b> – Felucca firmwares have a "
                  "watchdog and start protection that switches to ROM boot mode by itself after two failed starts. "
                  "2. Hold OCT− and OCT+ for 10 s while switching on (update mode). 3. <b>Three quick restarts:</b> "
                  "on, wait 10 s, off; on, 10 s, off; on – if every start crashes, the third ends up in "
                  "ROM boot mode. Only if none of this helps: stage 3 (hardware).")

TIER3_SHORT = _("<b>Symptom:</b> the FM-1 appears neither in update mode nor as “WL82 UBOOT1.00” – not even "
                "after <b>two minutes switched on</b> (watchdog) and <b>three quick restarts</b> (on, 10 s, "
                "off; on, 10 s, off; on). Then only hardware helps: a <b>Seeed XIAO RP2040</b> on the "
                "USB data lines forces ROM boot mode "
                "(<a href='https://github.com/kurogedelic/FM-1-transporter'>FM-1 Transporter</a>).")
STEPS_TIER3 = _("<ol><li>Connect the XIAO RP2040 to D+, D− and GND of the FM-1 as instructed (VBUS free).</li>"
                "<li>Flash the Transporter firmware with the unbricker tool in the terminal.</li>"
                "<li>In the terminal: rekey → info → dump → restore (the app does not run stage 3 itself).</li></ol>")
TIER3 = _("<b>Wiring (tested, TRANSPORTER-GUIDE):</b> XIAO <b>D6 (GP0) → D+ (green)</b>, "
          "<b>D7 (GP1) → D− (white)</b>, <b>GND → GND (black)</b>, <b>do not connect VBUS (red)</b> (tape it off). "
          "Flash the UF2: hold the BOOT button and plug in. <b>Honestly:</b> the bundled UF2 is only a "
          "placeholder – the real Transporter firmware is only created with <tt>python3 fm1_transporter_recover.py "
          "setup</tt> from the unbricker repo (MvaveFM1Unbricker). After that, also with this tool in the terminal: "
          "rekey → info (must show <tt>key=980F id=856014</tt>) → dump (read twice, compare, keep) → "
          "restore --write. The app itself does not run stage 3.<br><br>"
          "<b>RP2040 only! No RP2350 boards</b> (GPIO erratum and D+ pull-up make the Transporter unusable).<br><br>"
          "<b>Untested, not confirmed by the community:</b> with a <b>Raspberry Pi Pico (RP2040)</b> instead of the "
          "XIAO – pin 1 = GP0 → D+, pin 2 = GP1 → D−, any GND, VBUS free. Either try the XIAO UF2 or rebuild "
          "kurogedelic/FM-1-transporter with <tt>-DPICO_BOARD=pico</tt> and the same loader (pico-sdk). "
          "The XIAO remains the documented way.")

FAQ = _("<b>… an installation is aborted?</b> Do not unplug anything, check the cable, then “Finish installation” "
        "here – the app knows the interrupted package from its journal.<br>"
        "<b>… the FM-1 shows “FAILED -5”?</b> The running firmware refused the original firmware (CHECK HEAD). "
        "Nothing was written; the FM-1 keeps running. Report the problem to the firmware author.<br>"
        "<b>… presets are garbled after returning to the original firmware?</b> System &gt; Reset Patches.<br>"
        "<b>Prevention:</b> battery full, keep a backup after every successful flash. The detour via the "
        "original V15 before third-party firmware is preset, but is itself a write operation: two installations "
        "instead of one, so twice a period in which battery and cable must hold. It is mandatory only for V16 and "
        "for firmwares that only work from V15.")


# where an installation stopped (journal "stopped_in"), in words
PHASE_TEXT = {"prepared": _("before anything was sent"), "step1": _("while the FM-1 checked the package (step 1)"),
              "loader": _("while the FM-1 switched to update mode"), "writing": _("while writing"),
              "reboot": _("at the restart after writing"), "resources": _("while writing the resources")}


class RecoveryPage(QWidget):
    def __init__(self, ctx, window=None):
        super().__init__(window)
        self.ctx, self.window = ctx, window
        self.journal = fj.Journal()
        outer = QVBoxLayout(self)
        outer.addWidget(heading(_("Recovery"), _("FM-1 does not start, is stuck in update mode, or an installation was "
                                                 "interrupted? Step by step back to a working device.")))
        area = QScrollArea()
        area.setWidgetResizable(True)
        body = QWidget()
        lay = QVBoxLayout(body)
        area.setWidget(body)
        outer.addWidget(area, 1)

        basics = self._label(BASICS_SHORT)
        lay.addWidget(basics)
        self.details: dict[str, Disclosure] = {}
        self.details["basics"] = Disclosure(_("Details: basics"), BASICS)
        lay.addWidget(self.details["basics"])

        # ---- Level 1
        c1 = Card(_("Stage 1 – update mode: finish installation"))
        self.status = self._label("")
        self.status.setObjectName("rettungStatus")
        c1.layout().addWidget(self._label(_("<b>Symptom:</b> black screen or “UPDATING”, installers report "
                                            "“update mode”, an installation was aborted.") + STEPS_UPDATE))
        c1.layout().addWidget(self.status)
        self.b_finish = QPushButton(icon("system-software-update"), _("Finish installation"))
        self.b_finish.setProperty("variant", "primary")
        self.b_finish.clicked.connect(self.finish_install)
        self.b_v15 = QPushButton(icon("edit-undo"), _("Write original V15 instead"))
        self.b_v15.setToolTip(_("Only possible if the FM-1 is waiting in the original update mode (ota-FM-1_015)."))
        self.b_v15.clicked.connect(lambda: self.finish_install(stock=True))
        self.b_file = QPushButton(icon("document-open"), _("Choose another package …"))
        self.b_file.clicked.connect(lambda: self.finish_install(choose=True))
        c1.layout().addLayout(_row(self.b_finish, self.b_v15, self.b_file))
        self.b_forget = QPushButton(icon("edit-clear", "dialog-close"), _("Forget the interrupted installation"))
        self.b_forget.setToolTip(_("Removes the app's note about the interrupted installation (and its copy of the "
                                   "package). Nothing is sent to the FM-1. Use it when the FM-1 runs normally again."))
        self.b_forget.clicked.connect(self.forget_active)
        self.b_forget_chain = QPushButton(icon("edit-clear", "dialog-close"), _("Forget the two-step switch"))
        self.b_forget_chain.setToolTip(_("Removes the app's note that a second step is still to come. Nothing is "
                                         "sent to the FM-1."))
        self.b_forget_chain.clicked.connect(self.forget_chain)
        c1.layout().addLayout(_row(self.b_forget, self.b_forget_chain))
        self.details["update"] = Disclosure(_("Details: key combinations, community notes, journal"), DETAILS_UPDATE)
        c1.layout().addWidget(self.details["update"])
        lay.addWidget(c1)

        # ---- Level 2
        c2 = Card(_("Stage 2 – ROM boot mode “WL82 UBOOT1.00”"))
        c2.layout().addWidget(self._label(
            _("<b>Symptom:</b> black screen, no MIDI device, the computer shows a USB drive "
              "“WL82 UBOOT1.00”. <b>Do not format it!</b>") + STEPS_UBOOT))
        self.uboot_status = self._label("")
        c2.layout().addWidget(self.uboot_status)
        self.chk_sim = QCheckBox(_("Simulation – practice run with the tool's mock FM-1, nothing is written"))
        self.chk_sim.setChecked(DEMO)
        self.chk_sim.toggled.connect(lambda _on: self.refresh())
        self.chk_sim.setToolTip(_("Pre-ticked in demo mode. For a real recovery the tick must be removed – then the "
                                  "tool really writes to the flash of the connected FM-1."))
        c2.layout().addWidget(self.chk_sim)
        self.sim_note = self._label(_("<small>In demo mode the simulation is pre-ticked. For a real FM-1 remove the "
                                      "tick – only then is anything written.</small>") if DEMO else
                                    _("<small>Tick it to practise without a device: then everything runs against a "
                                      "simulated flash, nothing is changed on the FM-1.</small>"))
        c2.layout().addWidget(self.sim_note)
        self.b_setup = QPushButton(icon("download"), _("Fetch tool (jl-uboot-tool) …"))
        self.b_setup.clicked.connect(self.uboot_setup)
        self.b_restore = QPushButton(icon("tools-wizard", "system-run"), _("Write back original V15 …"))
        self.b_restore.setProperty("variant", "primary")
        self.b_restore.clicked.connect(self.uboot_restore)
        self.b_repair = QPushButton(icon("configure"), _("Repair settings after return …"))
        self.b_repair.setToolTip(_("repair-settings: resets the settings areas with a factory dump – only with the "
                                   "V15 layout (the app checks this on the fresh backup). Upstream only tested with a "
                                   "mock – try System > Reset Patches first."))
        self.b_repair.clicked.connect(self.uboot_repair)
        self.b_dump = QPushButton(icon("document-save"), _("Emergency full backup …"))
        self._dump_tip = _("Optional, advanced: send an original FM-1 into ROM boot mode via softkey and only read the "
                           "whole flash. Needs administrator rights. Never runs automatically.")
        self._dump_needs = _("For this, jl-uboot-tool must be fetched and an FM-1 connected (running with the "
                             "original firmware or already in ROM boot mode).")
        self.b_dump.setToolTip(self._dump_tip)
        self.b_dump.clicked.connect(self.uboot_full_backup)
        c2.layout().addLayout(_row(self.b_setup, self.b_restore))
        c2.layout().addLayout(_row(self.b_repair, self.b_dump))
        mf = shutil.which("m-flasher")
        if mf:
            b_mf = QPushButton(icon("system-run"), _("Open M-Flasher"))
            b_mf.setToolTip(_("M-Flasher is installed (alternative tool, see details)"))
            b_mf.clicked.connect(lambda: subprocess.Popen([mf], start_new_session=True))
            c2.layout().addLayout(_row(b_mf))
        self.details["uboot"] = Disclosure(_("Details: tool, memory areas, M-Flasher, Felucca 0.9"),
                                           DETAILS_UBOOT + "<br><br>" + (_("M-Flasher: found – button above.") if mf
                                                                         else _("M-Flasher: not installed.")))
        c2.layout().addWidget(self.details["uboot"])
        lay.addWidget(c2)

        # ---- Level 3
        c3 = Card(_("Stage 3 – FM-1 Transporter (hardware)"))
        c3.layout().addWidget(self._label(TIER3_SHORT + STEPS_TIER3))
        b_guide = QPushButton(icon("help-contents"), _("Open Transporter guide"))
        b_guide.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(
            str(uboot.VENDOR / "TRANSPORTER-GUIDE.md"))))
        c3.layout().addLayout(_row(b_guide))
        self.details["tier3"] = Disclosure(_("Details: wiring, boards, terminal commands"), TIER3)
        c3.layout().addWidget(self.details["tier3"])
        lay.addWidget(c3)

        # ---- Is my FM-1 clean?
        from ..clean_check import CleanCheckCard
        self.clean = CleanCheckCard(ctx, self, window, sim=lambda: self.chk_sim.isChecked(), runner=self._runner)
        lay.addWidget(self.clean)

        self.details["faq"] = Disclosure(_("What to do if … (abort, FAILED -5, garbled presets, prevention)"), FAQ)
        lay.addWidget(self.details["faq"])
        lay.addStretch(1)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        ctx.changed.connect(self.refresh)
        ctx.ports_changed.connect(self.refresh)
        self.refresh()

    def _label(self, html):
        lab = QLabel(html)
        lab.setWordWrap(True)
        lab.setTextFormat(Qt.TextFormat.RichText)
        lab.setOpenExternalLinks(True)
        return lab

    # ------------------------------------------------------------ State
    def activate(self):
        self.timer.start(1500)
        self.refresh()

    def deactivate(self):
        self.timer.stop()

    def shutdown(self):
        self.timer.stop()

    def update_port(self):
        """a connected FM-1 in update mode (active or in the list)"""
        c = self.ctx
        if c.port and (c.port.update_mode or c.protocol == "update"):
            return c.port
        return next((p for p in c.ports if p.update_mode), None)

    def refresh(self):
        act = self.journal.active
        port = self.update_port()
        ident = self.ctx.identity if self.ctx.port is port else ""
        lines = []
        if port:
            lines.append((_("<span style='color:#f67400'>● Detected live: an FM-1 is waiting in update mode</span> at "
                            "“{port}” (loader {loader}).") if ident else
                          _("<span style='color:#f67400'>● Detected live: an FM-1 is waiting in update mode</span> at "
                            "“{port}”.")).format(port=port.label, loader=ident))
        else:
            lines.append(_("<span style='color:#888'>○ Not detected: no FM-1 in update mode.</span> The display "
                           "updates by itself."))
        if act:
            where = act.get("stopped_in") or act.get("phase", "")
            lines.append(_("Interrupted installation according to the app's journal: <b>{target}</b> "
                           "({product}; it stopped {where}). The app has not looked at the FM-1 since – what is "
                           "detected live is in the line above.").format(
                               target=act.get('target', '?'), product=act.get('product', ''),
                               where=PHASE_TEXT.get(where, "“" + where + "”")))
        ch = self.journal.chain
        if ch:
            lines.append(_("Switch in two steps: after the original V15 the app still installs <b>{name}</b> – once "
                           "the FM-1 runs V15: Firmware+ › Install & Versions › “Continue”.").format(
                               name=ch.get("name") or ch.get("key", "?")))
        self.status.setText("<br>".join(lines))
        self.b_forget.setVisible(bool(act))
        self.b_forget_chain.setVisible(bool(ch))
        # level 1 buttons: only with a waiting device – and “Original V15” only if the loader accepts it
        wait_hint = _("Only possible once an FM-1 in update mode is detected (see status above).")
        self.b_finish.setEnabled(bool(port))
        self.b_finish.setToolTip((_("Writes the package of the interrupted installation (according to the journal) – "
                                    "after checking checksum and loader.") if act else
                                  _("No journal entry: you will be asked for the package whose installation was "
                                    "interrupted.")) if port else wait_hint)
        v15_ok = bool(port) and (not ident or bool(fpk.STOCK_LOADER_RE.match(ident)))
        self.b_v15.setEnabled(v15_ok)
        self.b_v15.setToolTip(_("Only possible if the FM-1 is waiting in the original update mode (ota-FM-1_015).")
                              if v15_ok else
                              (_("The FM-1 is waiting as “{identity}” – {loader}. It does not accept the original "
                                 "V15; please finish the interrupted package.").format(identity=ident,
                                                                                      loader=_loader_kind(ident))
                               if port else wait_hint))
        self.b_file.setEnabled(bool(port))
        self.b_file.setToolTip(_("Choose another .fwsc package – the app checks whether it matches the waiting loader.")
                               if port else wait_hint)
        devs = uboot.scan() if not DEMO else []
        usb = self.ctx.uboot if hasattr(self.ctx, "uboot") else []
        if devs:
            txt = _("<span style='color:#f67400'>● Detected live:</span> {devices}").format(
                devices="; ".join(d.describe() for d in devs))
        elif usb:
            txt = _("<span style='color:#f67400'>● An FM-1 in ROM boot mode is on USB</span>, but there is no SCSI "
                    "device for it: run <tt>sudo modprobe sg</tt>.")
        else:
            txt = _("<span style='color:#888'>○ Not detected: no device “WL82 UBOOT1.00” connected.</span>")
        miss = uboot.missing_deps()
        if miss:
            txt += "<br>" + _("Packages are missing for the tool: <tt>sudo apt install {packages}</tt>").format(
                packages=" ".join(miss))
        ready = uboot.tool_ready()
        txt += "<br>" + (_("Tool jl-uboot-tool: present (checked before every start).") if ready else
                         _("Tool jl-uboot-tool: not fetched yet – first “Fetch tool”."))
        self.uboot_status.setText(txt)
        # level 2 buttons: simulation always works (practice); real only with the tool and exactly one device
        sim = self.chk_sim.isChecked()
        self.b_setup.setToolTip(_("Fetches kagaimiq's jl-uboot-tool once at the checked state – nothing is changed on "
                                  "the FM-1.") + ("" if not ready else " " + _("Already present; fetching again only "
                                                                               "checks it.")))
        for b, what in ((self.b_restore, _("Writes the original V15 (0x4000–0x92FFF) to the flash of the FM-1 in "
                                           "ROM boot mode – after the mandatory double backup, only under the factory "
                                           "boot header.")),
                        (self.b_repair, _("Only resets the settings areas to a factory dump – only if the fresh "
                                          "backup shows the V15 layout."))):
            if sim:
                b.setEnabled(True)
                b.setToolTip(_("{action} Simulation: runs against a mock, nothing is written.").format(action=what))
            elif not ready:
                b.setEnabled(False)
                b.setToolTip(_("First “Fetch tool” – without jl-uboot-tool the app cannot talk to the chip."))
            elif len(devs) != 1:
                b.setEnabled(False)
                b.setToolTip(_("Only possible once exactly one device “WL82 UBOOT1.00” is detected (see status above).")
                             if not devs else _("Several devices in ROM boot mode – please connect only one."))
            else:
                b.setEnabled(True)
                b.setToolTip(what)
        dump_ok = sim or (ready and (bool(devs) or self.ctx.port is not None))
        self.b_dump.setEnabled(dump_ok)
        if not dump_ok:
            self.b_dump.setToolTip(self._dump_needs)
        elif self.b_dump.toolTip() == self._dump_needs:
            self.b_dump.setToolTip(self._dump_tip)

    # ------------------------------------------------------------ Level 1
    def forget_active(self):
        """the user says the interrupted installation is no longer pending: only the journal note goes"""
        self.journal.clear_active()
        self.refresh()

    def forget_chain(self):
        self.journal.clear_chain()
        self.refresh()

    def _package_for(self, ident: str, stock: bool, choose: bool) -> Path | None:
        if stock:
            sp = firmware.spec("stock-v15")
            return None if sp is None else sp
        act = self.journal.active
        if act and not choose and act.get("package") and Path(act["package"]).exists():
            return Path(act["package"])
        path, _filter = QFileDialog.getOpenFileName(self, _("Package of the interrupted installation"),
                                                    str(self.ctx.settings.firmware_dir), _("FM-1 firmware (*.fwsc)"))
        return Path(path) if path else None

    def finish_install(self, stock: bool = False, choose: bool = False) -> bool:
        port = self.update_port()
        if port is None:
            show_error(self, _("No device in update mode"), _("Please bring the FM-1 into update mode as described "
                                                              "and connect it."))
            return False
        src = self._package_for("", stock, choose)
        if src is None:
            return False
        act = self.journal.active or {}
        ident_now = self.ctx.identity if self.ctx.port is port else ""
        expect_sha = ""
        if stock:
            what = _("the unmodified <b>original firmware V15</b> (checksum db1642b2…)")
            if ident_now and not fpk.STOCK_LOADER_RE.match(ident_now):
                show_error(self, _("Does not match the waiting loader"),
                           _("The FM-1 is waiting as “{identity}” – that is {loader}. It does not accept the original "
                             "V15. Nothing was sent. Please use “Finish installation” with the package of the "
                             "interrupted installation.").format(identity=ident_now, loader=_loader_kind(ident_now)))
                return False
        else:
            # check the package right here – the user sees before clicking what would be sent
            try:
                pre = fpk.analyze(Path(src))
            except fpk.PackageError as e:
                show_error(self, _("No FM-1 package"), _("{error} Nothing was sent.").format(error=e))
                return False
            fam = {"stock": _("Original (M-VAVE)"), "felucca": _("Felucca family")}.get(pre.family, _("unknown"))
            facts = _("Identity {product} · loader family {family} · SHA-256 {sha256}…").format(
                product=pre.product, family=fam, sha256=pre.sha256[:16])
            if pre.damage:
                facts += " · <b>" + _("damaged: {damage}").format(damage=pre.damage) + "</b>"
            facts = "<br><small>" + facts + "</small>"
            if pre.damage:
                show_error(self, _("Package damaged"), _("{name} is damaged ({damage}) and is not sent. Please download "
                                                         "the package again (Firmware+ › “Install & Versions” › "
                                                         "“Download only”).").format(name=Path(src).name,
                                                                                      damage=pre.damage))
                return False
            if pre.loader_rule is None:
                show_error(self, _("Unknown update loader"), _("The app does not know the update loader of {name} and "
                                                               "therefore sends the package to no loader. Nothing was "
                                                               "sent.").format(name=Path(src).name))
                return False
            if ident_now and not pre.loader_rule(ident_now):
                show_error(self, _("Does not match the waiting loader"),
                           _("The FM-1 is waiting as “{identity}”, but {name} belongs to {loader}. Nothing was sent. "
                             "Please choose the package whose installation was interrupted.").format(
                               identity=ident_now, name=Path(src).name, loader=pre.loader_rule.label))
                return False
            if act.get("package") and Path(act["package"]) == Path(src):
                expect_sha = act.get("sha256") or ""
                what = _("<b>{target}</b> (the package of the interrupted installation){facts}").format(
                    target=act.get('target') or act.get('product') or _("the interrupted firmware"), facts=facts)
            else:
                what = _("the package <b>{name}</b>{facts}").format(name=Path(src).name, facts=facts)
                if act.get("sha256") and act["sha256"] != pre.sha256:
                    what += "<br>" + _("<b>Caution:</b> “{target}” was interrupted – that is a different package. The "
                                       "safest thing is to write exactly the interrupted package again.").format(
                                           target=act.get('target', '?'))
        from ..risk_dialog import ensure_risk_ack
        if not ensure_risk_ack(self):
            return False
        if not confirm(self, _("Finish installation"),
                       _("The waiting FM-1 receives {what}.<br><br>"
                         "The app checks checksum and loader once more beforehand – if anything does not match, nothing "
                         "is sent.<br>While writing, do not unplug the cable and do not switch the FM-1 off.").format(
                           what=what) + "<br><br><b>" + tested.note(["resume"]) + "</b>",
                       pgettext("button", "Finish")):
            return False
        cancel = threading.Event()
        device_id = self.ctx.profile.id if self.ctx.profile else ""
        self.ctx.hold = True
        if self.ctx.port is port:
            self.ctx.disconnect(None)

        def work(w):
            path = src
            if stock:
                path = firmware.download(src, firmware.fetch_releases(src)[0], log=w.log.emit).fwsc
            info = fpk.analyze(path)
            if stock and info.official != "FM-1_015":
                raise RuntimeError(_("The V15 file is not the unmodified original firmware – nothing sent."))
            w.log.emit(_("Package: {product} ({family}), SHA-256 {sha256}…").format(
                product=info.product, family=info.family, sha256=info.sha256[:16]))
            if DEMO:
                from ...flash import demo
                demo.register(Path(path).read_bytes())
            eng = fe.Engine(log=w.log.emit, progress=lambda a, b: w.progress.emit(a, b or 100), cancel=cancel)
            ident = eng.probe(port.in_name)
            w.log.emit(_("Device reports: {identity}").format(identity=ident.text if ident else _("nothing")))
            pin = fe.Pin(usb_path=port.usb_path) if port.usb_path else None
            r = eng.write(info, port=port.in_name, label=_("Recovery: {target}").format(target=act.get('target') or info.product),
                          device_id=device_id, pin=pin, expect_sha=expect_sha)
            w.log.emit(_("Done – the FM-1 reports {identity}.").format(identity=r.identity))
            if r.identity != "FM-1_015":
                # a two-step switch whose target is now written here is finished: no “Continue” any more
                # (after the original V15 the second step is still to come – the chain stays)
                self.journal.clear_chain()
            return r.identity

        def run(w):
            try:
                return work(w)
            except (RuntimeError, fe.FlashError):
                raise                                   # already readable sentences
            except Exception as e:  # noqa: BLE001
                w.log.emit(_("Technical details: {details}").format(details=firmware.explain(e)))
                raise RuntimeError(firmware.friendly_error(e)) from e
        from ..feedback import run as fb_run
        try:
            d = fb_run(self, _("Finish installation"), run,
                       done=lambda ident: _("Installation finished – the FM-1 now reports {identity} and starts "
                                            "normally.").format(identity=ident))
        finally:
            self.ctx.reconnect_soon(1500)
        self.refresh()
        return d.ok

    # ------------------------------------------------------------ Level 2
    def _runner(self, log):
        return uboot.Runner(dry_run=self.chk_sim.isChecked(), log=log)

    def uboot_setup(self):
        if not confirm(self, _("Fetch tool"),
                       _("The app downloads kagaimiq's <b>jl-uboot-tool</b> (MIT) from GitHub at the checked state "
                         "adb3f18 and checks the checksum of the WL82 loader (d41da612…). Nothing is installed, it is "
                         "only copied into the app data folder. Continue?"), _("Download")):
            return False
        from ..feedback import run as fb_run
        d = fb_run(self, _("Fetch jl-uboot-tool"), lambda w: _check(self._runner(w.log.emit).setup()),
                   done=_("jl-uboot-tool fetched and checked (state adb3f18). Nothing was changed on the FM-1."))
        return d.ok

    def uboot_restore(self) -> bool:
        sim = self.chk_sim.isChecked()
        devs = [] if sim else uboot.scan()
        if not sim:
            if len(devs) != 1:
                msg = ONE_UBOOT
                if devs:
                    msg = _("{message} Found: {devices}").format(message=msg, devices=", ".join(x.sg for x in devs))
                show_error(self, _("No unambiguous device"), msg)
                return False
            if not devs[0].size_ok:
                show_error(self, _("Size does not match"), _("{device} – that is not an FM-1.").format(device=devs[0].describe()))
                return False
        dev = devs[0] if devs else None
        from ..risk_dialog import ensure_risk_ack
        if not ensure_risk_ack(self):
            return False
        if not confirm(self, _("Recovery stage 2 – step 1 of 3"),
                       BASICS + "<br>" + _("The FM-1 must be <b>fully charged</b> and connected directly to the "
                                           "computer.") + "<br>" +
                       _("Device: <b>{device}</b>").format(device=dev.describe() if dev else _("Simulation"))
                       + "<br><br>" +
                       _("Procedure: read the whole flash twice and compare (mandatory, also contains your projects), "
                         "check the boot header on this backup (it only continues under the factory header – with a "
                         "V16 or unknown header nothing is written), write the firmware area 0x4000–0x92FFF from the "
                         "original V15, read back.") + "<br><br><b>" + tested.note(["rom_restore"]) + "</b>",
                       _("Continue"),
                       danger=True):
            return False
        word = pgettext("typed confirmation", "WRITE")
        text, ok = QInputDialog.getText(self, _("Recovery stage 2 – step 2 of 3"),
                                        _("To confirm, type {word} (writes to the flash of the FM-1):").format(word=word))
        if not ok or text.strip().upper() not in {word.upper(), "SCHREIBEN", "WRITE"}:  # i18n: ignore
            return False
        if not sim and not confirm(self, _("Recovery stage 2 – step 3 of 3"),
                                   _("Now the system asks for your password (pkexec): the tool needs administrator "
                                     "rights for direct USB access. After that do not unplug anything until “Done” "
                                     "appears."), pgettext("button", "Start"), danger=True):
            return False

        def work(w):
            st = firmware.spec("stock-v15")
            v15 = firmware.download(st, firmware.fetch_releases(st)[0], log=w.log.emit).fwsc
            r = self._runner(w.log.emit)
            return _gate_check(r, r.restore(str(v15), device=dev.sg if dev else ""))
        from ..feedback import run as fb_run
        d = fb_run(self, _("Write original V15 in ROM boot mode"), work,
                   done=(_("Simulation finished – nothing was written.") if sim else
                         _("Original V15 written (0x4000–0x92FFF) and read back. The double backup of the "
                           "whole flash is in the recovery folder. Now switch the FM-1 off and on again.")),
                   actions=[(_("Open recovery folder"), lambda _r: QDesktopServices.openUrl(
                       QUrl.fromLocalFile(str(uboot.work_dir()))))])
        return d.ok

    def _one_uboot(self, title: str):
        """exactly ONE device in ROM boot mode (USB 4c4a:8057, WL82 UBOOT1.00) – otherwise None (with message).
        In simulation: (None, True)."""
        if self.chk_sim.isChecked():
            return None, True
        devs = uboot.scan()
        if len(devs) != 1:
            show_error(self, title, _("{message} Found: {devices}").format(message=ONE_UBOOT, devices=", ".join(
                x.sg for x in devs)) if devs else _("{message} Found: none.").format(message=ONE_UBOOT))
            return None, False
        if not devs[0].size_ok:
            show_error(self, title, _("{device} – that is not an FM-1.").format(device=devs[0].describe()))
            return None, False
        return devs[0], True

    def uboot_repair(self) -> bool:
        dev, ok = self._one_uboot(_("Repair settings"))
        if not ok:
            return False
        path, _filter = QFileDialog.getOpenFileName(self, _("Factory dump (1 MiB) of a fresh original FM-1"),
                                                    str(uboot.work_dir()), _("Flash dump (*.bin)"))
        if not path:
            return False
        from ..risk_dialog import ensure_risk_ack
        if not ensure_risk_ack(self):
            return False
        if not confirm(self, _("Repair settings"),
                       _("<b>Upstream only tested with a mock.</b> First try <i>System &gt; Reset Patches</i> on the "
                         "FM-1.<br>The tool backs up the flash twice and then resets only the settings areas "
                         "(0x93000–0xE8FFF, 0xFC000–0xFEFFF) to the factory dump. Songs and settings of the previous "
                         "firmware are lost in the process (they stay in the backup), with Melodee also factory sounds "
                         "and font (0xCB000).<br>These areas are pure data only with the <b>V15 layout</b>. That is why "
                         "the app checks on the backup from the same run where the firmware ends – with V16 (there is "
                         "still firmware in 0x93000–0xA8FFF), an unknown or a damaged layout nothing is written."),
                       _("Repair"), danger=True):
            return False
        word = pgettext("typed confirmation", "WRITE")
        text, ok = QInputDialog.getText(self, _("Confirm"), _("To confirm, type {word}:").format(word=word))
        if not ok or text.strip().upper() not in {word.upper(), "SCHREIBEN", "WRITE"}:  # i18n: ignore
            return False
        def work(w):
            r = self._runner(w.log.emit)
            _gate_check(r, r.repair_settings(path, device=dev.sg if dev else ""))
            return r.last_verdict().get("verdict") or {}
        from ..feedback import run as fb_run
        d = fb_run(self, _("Repair settings"), work,
                   done=lambda v: repair_done_text(v, sim=dev is None))
        return d.ok

    def uboot_full_backup(self) -> bool:
        from ..risk_dialog import ensure_risk_ack
        if not ensure_risk_ack(self):
            return False
        if not confirm(self, _("Emergency full backup (optional)"),
                       _("For advanced users: an FM-1 with the <b>original firmware</b> is sent into ROM boot mode via "
                         "softkey, then the whole flash is only read twice and compared. Needs administrator rights. "
                         "Afterwards switch the FM-1 off and on."), _("Back up")):
            return False
        folder = str(uboot.work_dir() / "Komplettsicherungen")  # i18n: ignore (folder name on disk)
        sim = self.chk_sim.isChecked()
        if not sim and getattr(self.ctx, "is_virtual", False):
            show_error(self, _("Not a real FM-1"), _("The Studio twin is not a real FM-1 – a full backup only works "
                                                     "from a connected device. Please choose a real FM-1 at the top."))
            return False
        port = self.ctx.port
        softkey = not sim and self.ctx.protocol != "update"
        if not sim:
            before = uboot.scan()
            if softkey and before:
                show_error(self, _("Already a device in ROM boot mode"),
                           _("A device “WL82 UBOOT1.00” is already connected. So that the backup surely comes from the "
                             "right FM-1: unplug this device first, or – if it is the one you want – back it up without "
                             "softkey (disconnect the FM-1 first, then try again)."))
                return False
            if not softkey and len(before) != 1:
                dev, ok = self._one_uboot(_("Emergency full backup"))
                if not ok:
                    return False
        if softkey and port is None:
            show_error(self, _("No device"), _("Please connect the FM-1 with the original firmware."))
            return False

        def work(w):
            r = self._runner(w.log.emit)
            dev = ""
            if softkey:
                _check(r.softkey(port.in_name), r)
                w.log.emit(_("Waiting for the FM-1 in ROM boot mode (USB 4c4a:8057) …"))
                for _try in range(40):
                    found = [d for d in uboot.scan() if not port.usb_path or d.usb_path == port.usb_path]
                    if len(found) == 1:
                        dev = found[0].sg
                        break
                    if len(found) > 1:
                        raise RuntimeError(_("Several devices in ROM boot mode – please leave only the FM-1 connected "
                                             "that you want to back up."))
                    threading.Event().wait(0.5)
                if not dev:
                    raise RuntimeError(_("The FM-1 did not appear in ROM boot mode (USB 4c4a:8057). Switch it off and "
                                         "on again and try again."))
            elif not sim:
                devs = uboot.scan()
                if len(devs) != 1:
                    raise RuntimeError(_("Exactly one device “WL82 UBOOT1.00” must be connected."))
                dev = devs[0].sg
            return _check(r.backup(folder, device=dev), r)
        from ..feedback import run as fb_run
        d = fb_run(self, _("Emergency full backup"), work,
                   done=_("Full backup read (twice, compared) and saved in {folder}. Nothing was written on the "
                          "FM-1 – now switch it off and on again.").format(folder=folder),
                   actions=[(_("Open folder"), lambda _r: QDesktopServices.openUrl(QUrl.fromLocalFile(folder)))])
        return d.ok


def _loader_kind(ident: str) -> str:
    """which update loader is waiting – in words (display only)"""
    if fpk.OPEN_LOADER_RE.match(ident or ""):
        return _("the update loader of the M-VAVE FM1 Open (only accepts the package of exactly its build)")
    if (ident or "").startswith("ota-FM-1_9"):
        return _("a Felucca loader (Melodee, SLOOP, Skiff …)")
    return _("an update loader the app does not know")


def repair_done_text(verdict: dict, sim: bool = False) -> str:
    """Completion of “Repair settings”. Only after rc 0 – then rescuegate has detected the V15 layout on the backup of
    the same run, and the unbricker's gates only allow 0x93000–0xE8FFF and 0xFC000–0xFEFFF."""
    if sim:
        return _("Simulation finished – nothing was written on the FM-1.")
    verdict = rescue_texts.verdict(verdict)          # rescuegate.py (pinned) writes German -> UI language
    fw = verdict.get("firmware") or _("the firmware in the flash")
    basis = verdict.get("basis") or _("V15 layout")
    return _("The settings areas 0x93000–0xE8FFF and 0xFC000–0xFEFFF are now in factory state (rewritten and read back "
             "where necessary). Beforehand the app checked on the backup from this run that {firmware} has the V15 "
             "layout and ends before 0x93000 ({basis}); that is why the tool did not write the boot header and the "
             "firmware area 0x0000–0x92FFF. Songs, sounds and settings from these areas – with Melodee also factory "
             "sounds and font – are now only in the backup in the recovery folder. Now switch the FM-1 off and on "
             "again.").format(firmware=fw, basis=basis)


def _gate_check(runner, rc: int):
    """like _check, but with the reason for the block (rescuegate.py) if it refused"""
    from ...flash import rescuegate
    res = runner.last_verdict() if hasattr(runner, "last_verdict") else {}
    if rc == rescuegate.EXIT_REFUSED or (rc != 0 and res.get("refused")):
        raise RuntimeError(_("Refused – nothing was written. {reason} The backup from this run is in the recovery "
                             "folder. The FM-1 usually leaves ROM boot mode after one tool run – for another attempt "
                             "switch it off and on and bring it into ROM boot mode again.").format(
            reason=rescue_texts.tr(res.get("refused")) or _("The check of the backup did not pass (details in the log).")))
    if rc != 0 and getattr(runner, "hint", ""):
        raise RuntimeError(runner.hint)
    if rc == 0 and not res.get("checked"):
        raise RuntimeError(_("The tool ended without checking the backup – the app cannot say what happened. "
                             "Please look at the log."))
    return _check(rc)


def _check(rc: int, runner=None):
    if rc != 0 and getattr(runner, "hint", ""):      # e.g. UCUN flash (0xB36014): rescue doesn't know it yet
        raise RuntimeError(runner.hint)
    if rc != 0:
        raise RuntimeError(_("The tool reports an error (code {code}) – details in the log. {hint}").format(
            code=rc, hint=_("Code 3: firmware area possibly incomplete – do NOT switch off, try again.") if rc == 3
            else _("Nothing was written, unless the log says otherwise.")))
    return rc


def _row(*ws):
    h = QHBoxLayout()
    for w in ws:
        h.addWidget(w)
    h.addStretch(1)
    return h


def _make(ctx, window):
    return RecoveryPage(ctx, window)


PAGE = PageSpec(key="recovery", title=_("Recovery"), group="daten", factory=_make,
                icons=("tools-wizard", "help-hint", "dialog-warning"), order=14,
                description=_("FM-1 does not start or is stuck in update mode – rescue it step by step"),
                keywords=("recovery", "brick", "update-modus", "uboot", "wl82", "transporter", "oct", "sauber", "dump",
                          "flash prüfen", "v16", "rescue", "update mode", "unbrick", "check flash", "clean"))
