# SPDX-License-Identifier: GPL-3.0-only
"""„Ist mein FM-1 sauber?“ – Karte auf der Seite „Rettung“ und Ergebnisfenster mit Ampel.

Teil 1 nur lesend über USB-MIDI (Kennung, Version, bei Melodee ab 1.6 der Ressourcen-Status, Befehl 82).
Teil 2 mit Flash-Dump: Datei wählen oder im ROM-Bootmodus über den eingebauten Unbricker lesen (`backup`, liest zweimal).
Die Bewertung macht flash/flashmodel.py; hier nur Oberfläche, Hintergrund-Jobs und Gerätewechsel.
"""
from __future__ import annotations

import os
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QApplication, QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QInputDialog, QLabel,
                             QPushButton, QScrollArea, QVBoxLayout, QWidget)

from .. import config
from ..flash import flashmodel as fm
from ..i18n import _, ngettext, pgettext
from .common import Card, Disclosure, confirm, flow_row, icon, run_job, show_error

DEMO = os.environ.get("FM1C_DEMO") == "1"
MAX_PACKAGES = 40

INTRO = _("Checks whether everything is in its place after many firmware switches. <b>Quick check:</b> read only "
          "over USB, without risk. <b>Dump check:</b> compares a full backup of the flash sector by sector with all "
          "packages the app knows.")
DETAILS = _("<b>Quick check</b> asks for identity and version, with Melodee 1.6 and later also the resource status "
            "(factory sounds, font, Bluetooth program): the firmware recomputes header and CRC-32 itself for this. It "
            "does not look into the rest of the flash.<br><br>"
            "<b>Dump check</b> needs a 1 MiB full backup (e.g. “Emergency full backup” in stage 2 "
            "or “full-flash-before.bin” from the recovery folder). It checks the boot header, the firmware area, a "
            "truncated V16, leftovers of V16 in the data areas and the Melodee resources. In ROM boot mode it only "
            "reads – it never writes.<br><br>"
            "<b>Clean up V16 leftovers</b> (expert mode, ROM boot mode, <b>untested on a real device</b>): erases "
            "selected single 4 KiB sectors in 0x93000–0xA8FFF – but only those that, according to a freshly read "
            "backup, still carry exactly V16 bytes, and only if the flash holds a known firmware with the V15 layout "
            "byte for byte (Melodee, V15, SLOOP …), never with V16. Nothing is written. Beforehand the whole flash is "
            "read twice and backed up, checked after every sector, and the whole flash compared at the end; a journal "
            "makes an aborted run resumable. From everything known so far (emulator, source code), the leftovers are "
            "harmless – so leaving them is fine too.")

LEVEL_COLOR = {fm.GREEN: "success", fm.YELLOW: "warning", fm.RED: "danger"}
# fm.LEVEL_TEXT in the UI language (flashmodel.py is pinned and stays German)
LEVEL_LABEL = {fm.GREEN: _("Clean"), fm.YELLOW: _("With notes"), fm.RED: _("Not OK")}


def level_text(level: str) -> str:
    return LEVEL_LABEL.get(level) or fm.LEVEL_TEXT.get(level, level)


def _color(level: str) -> str:
    from . import theme
    return getattr(theme.current(), LEVEL_COLOR.get(level, "success"))


def local_packages() -> list:
    """FM-1-Pakete, die auf dem Rechner liegen (Firmware-Ordner, Journal-Kopien) – für Sektorvergleiche"""
    from .. import firmware
    files: list[Path] = []
    try:
        files += firmware.local_packages()
    except OSError:
        pass
    kept = config.DATA_DIR / "flash" / "packages"
    if kept.is_dir():
        files += sorted(kept.glob("*.fwsc"))
    out, seen = [], set()
    for f in files[:MAX_PACKAGES * 2]:
        try:
            p = fm.Package.load(f)
        except (fm.PackageError, OSError, ValueError):
            continue
        if p.file_sha256 in seen:
            continue
        seen.add(p.file_sha256)
        ref = fm.ref_for(file_sha256=p.file_sha256)
        p.label = ref.name if ref else f"{p.identity} ({Path(f).name})"
        out.append(p)
        if len(out) >= MAX_PACKAGES:
            break
    return out


def analyse_dump(path: Path | str):
    """Dump-Datei lesen und prüfen (läuft im Hintergrund) -> Report"""
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as e:
        raise RuntimeError(_("{name} cannot be read ({error}). Nothing was changed.").format(name=path.name, error=e.strerror))
    refs = fm.load_known()
    heads = {fm.FACTORY_HEAD_SHA256}
    if DEMO:
        refs += fm.demo_refs()
        heads |= fm.demo_heads()
    try:
        rep = fm.check_dump(data, refs, packages=local_packages(), factory_heads=heads)
    except fm.DumpError as e:
        raise RuntimeError(_("{error} Please choose a full backup (e.g. full-flash-before.bin).").format(error=e))
    rep.title = _("Flash dump “{name}”").format(name=path.name)
    return rep


class ReportDialog(QDialog):
    """Ergebnis mit Ampel, Sätzen in Alltagssprache, „Was das nicht abdeckt“ und technischen Details"""

    def __init__(self, parent, rep: fm.Report, source: str = "", window=None):
        super().__init__(parent)
        self.rep, self.window = rep, window
        self.setWindowTitle(_("Is my FM-1 clean?"))
        self.setMinimumWidth(520)
        self.resize(720, 480)
        outer = QVBoxLayout(self)
        head = QLabel(f"<span style='color:{_color(rep.level)}; font-size:20px'>●</span> "
                      f"<b style='font-size:15px'>{level_text(rep.level)}</b>"
                      + (f" – {rep.firmware}" if rep.firmware else ""))
        head.setObjectName("CleanVerdict")
        head.setTextFormat(Qt.TextFormat.RichText)
        head.setWordWrap(True)
        outer.addWidget(head)
        sub = QLabel(rep.title + (f" · {source}" if source else ""))
        sub.setProperty("role", "muted")
        sub.setWordWrap(True)
        outer.addWidget(sub)
        area = QScrollArea()
        area.setWidgetResizable(True)
        body = QWidget()
        lay = QVBoxLayout(body)
        area.setWidget(body)
        outer.addWidget(area, 1)
        items = "".join(f"<tr><td style='color:{_color(lv)}; padding-right:6px' valign='top'>●</td>"
                        f"<td style='padding-bottom:6px'>{t}</td></tr>" for lv, t in rep.findings)
        found = QLabel(f"<table cellspacing='0'>{items}</table>")
        found.setObjectName("CleanFindings")
        found.setWordWrap(True)
        found.setTextFormat(Qt.TextFormat.RichText)
        found.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(found)
        if rep.not_covered:
            nc = QLabel("<b>" + _("What this check does not cover:") + "</b><ul style='margin-left:-20px'>"
                        + "".join(f"<li>{t}</li>" for t in rep.not_covered) + "</ul>")
            nc.setObjectName("CleanNotCovered")
            nc.setWordWrap(True)
            nc.setTextFormat(Qt.TextFormat.RichText)
            lay.addWidget(nc)
        det = list(rep.details)
        if rep.compared:
            det.append(_("Compared with: {packages}").format(packages=", ".join(rep.compared)))
        if det:
            lay.addWidget(Disclosure(_("Technical details"), "<br>".join(det)))
        lay.addStretch(1)
        bb = QDialogButtonBox()
        b_copy = bb.addButton(_("Copy report"), QDialogButtonBox.ButtonRole.ActionRole)
        b_copy.setToolTip(_("Put the report as text on the clipboard – e.g. for a question in the forum"))
        b_copy.clicked.connect(self.copy)
        self.b_res = None
        # the findings come from flashmodel.py (German); "nachschreiben" = the rewrite-resources hint
        if any("nachschreiben" in t for _lv, t in rep.findings) and window is not None and window.page("firmware"):  # i18n: ignore
            self.b_res = bb.addButton(_("To Firmware+ › Install"), QDialogButtonBox.ButtonRole.ActionRole)
            self.b_res.setToolTip(_("There: “Rewrite font & factory sounds”"))
            self.b_res.clicked.connect(lambda: (self.accept(), window.go("firmware")))
        close = bb.addButton(_("Close"), QDialogButtonBox.ButtonRole.RejectRole)
        close.setDefault(True)
        bb.rejected.connect(self.reject)
        outer.addWidget(bb)

    def text(self) -> str:
        r = self.rep
        lines = [_("Is my FM-1 clean? – {level}").format(level=level_text(r.level)) + (f" ({r.firmware})" if r.firmware else ""),
                 r.title, ""]
        lines += [f"[{lv}] {t}" for lv, t in r.findings]
        if r.not_covered:
            lines += ["", _("Not covered:")] + [f"- {t}" for t in r.not_covered]
        if r.details:
            lines += ["", _("Details:")] + r.details
        from ..redact import redact_text
        return redact_text("\n".join(lines))

    def copy(self):
        QApplication.clipboard().setText(self.text())


class CleanCheckCard(Card):
    """die Karte auf der Rettungsseite. sim(): Simulationshäkchen der Stufe 2; runner(log): uboot.Runner"""

    def __init__(self, ctx, page, window=None, sim=lambda: DEMO, runner=None):
        super().__init__(_("Is my FM-1 clean?"))
        self.ctx, self.page, self.window, self.sim, self.runner = ctx, page, window, sim, runner
        self.last_report: fm.Report | None = None
        self.last_dialog: ReportDialog | None = None
        self._busy = False
        intro = QLabel(INTRO)
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.RichText)
        self.layout().addWidget(intro)
        self.b_quick = QPushButton(icon("dialog-information", "help-about"), _("Quick check (read only)"))
        self.b_quick.setProperty("variant", "primary")
        self.b_quick.clicked.connect(self.quick)
        self.b_file = QPushButton(icon("document-open"), _("Check dump file …"))
        self.b_file.setToolTip(_("Choose a 1 MiB full backup of the flash and compare it with all known packages – "
                                 "nothing happens on the FM-1."))
        self.b_file.clicked.connect(lambda: self.dump_file())
        self.b_rom = QPushButton(icon("drive-removable-media", "document-save"), _("Read && check in ROM boot mode …"))
        self.b_rom.setToolTip(_("For advanced users: read the FM-1 completely twice in ROM boot mode (“WL82 UBOOT1.00”) "
                                "(read only, needs administrator rights) and check it right away."))
        self.b_rom.clicked.connect(self.rom_dump)
        row = [self.b_quick, self.b_file, self.b_rom]
        if DEMO:
            self.b_demo = QPushButton(icon("document-new"), _("Create example dumps (demo)"))
            self.b_demo.setToolTip(_("Creates three made-up dumps (clean, with V16 leftovers, V16 truncated), "
                                     "so you can try the dump check without a device."))
            self.b_demo.clicked.connect(self.demo_examples)
            row.append(self.b_demo)
        self.layout().addWidget(flow_row(*row))
        self.status = QLabel("")
        self.status.setObjectName("CleanStatus")
        self.status.setWordWrap(True)
        self.status.setProperty("role", "muted")
        self.layout().addWidget(self.status)
        # ---- Expertenmodus: V16-Reste aufräumen (ROM-Bootmodus)
        self.opts = config.module("rettung", {"experte": False})
        self.chk_expert = QCheckBox(_("Expert mode: clean up V16 leftovers in ROM boot mode"))
        self.chk_expert.setToolTip(_("Shows “Clean up V16 leftovers …”. Only erases sectors that provably still "
                                     "contain V16 – untested on a real device."))
        self.chk_expert.setChecked(bool(self.opts.get("experte")))
        self.chk_expert.toggled.connect(self._expert_toggled)
        self.b_clean = QPushButton(icon("edit-clear", "edit-delete"), _("Clean up V16 leftovers …"))
        self.b_clean.setProperty("variant", "danger")
        self.b_clean.setToolTip(_("In ROM boot mode: back up, look for leftovers of V16 in 0x93000–0xA8FFF, after your "
                                  "confirmation erase exactly these sectors and check them. Untested on a real device."))
        self.b_clean.clicked.connect(lambda: self.clean_residue())
        self.layout().addWidget(flow_row(self.chk_expert, self.b_clean))
        self.b_clean.setVisible(self.chk_expert.isChecked())
        self.layout().addWidget(Disclosure(_("Details: what is checked, what is not, cleaning up leftovers"), DETAILS))
        ctx.changed.connect(self.refresh)
        self.refresh()

    # ------------------------------------------------------------ Zustand
    def refresh(self):
        c = self.ctx
        if self._busy:
            return
        if c.port is None:
            tip = _("First connect an FM-1 via USB (switched on normally, not in update mode).")
            ok = False
        elif c.protocol == "update":
            tip = _("The FM-1 is waiting in update mode – finish the installation first (stage 1).")
            ok = False
        else:
            ok = True
            tip = (_("Reads identity, version and – with Melodee 1.6 and later – the resource status. Changes nothing "
                     "on the FM-1.") if c.connected else _("Reads the identity of the FM-1. Changes nothing on the FM-1."))
        self.b_quick.setEnabled(ok)
        self.b_quick.setToolTip(tip)

    def _set_busy(self, on: bool, text: str = ""):
        self._busy = on
        for b in (self.b_quick, self.b_file, self.b_rom, self.b_clean, self.chk_expert):
            b.setEnabled(not on)
        self.status.setText(text)
        if not on:
            self.refresh()

    def _show(self, rep: fm.Report, source: str = ""):
        self.last_report = rep
        self.status.setText(_("Last result: {level}").format(level=level_text(rep.level))
                            + (f" – {rep.firmware}" if rep.firmware else "") + (f" ({source})" if source else ""))
        self.last_dialog = ReportDialog(self, rep, source, self.window)
        self.last_dialog.exec()

    # ------------------------------------------------------------ Teil 1: nur lesen
    def quick(self):
        c = self.ctx
        if c.port is None or c.protocol == "update":
            show_error(self, _("Quick check not possible"),
                       _("{reason} Nothing was changed.").format(reason=self.b_quick.toolTip()))
            return False
        port_key, name = c.port.key, (c.profile.name if c.profile else c.port.label)
        identity = c.identity or (c.profile.identity if c.profile else "")
        version = c.info.version if c.info else (c.profile.version_string if c.profile else "")

        def finish(res):
            status, err = res
            self._set_busy(False)
            rep = fm.quick_check(identity, version, status, res_error=err)
            if self.ctx.port is None or self.ctx.port.key != port_key:
                rep.add(fm.YELLOW, _("The device was switched or disconnected during the check – this report applies "
                                     "to “{name}”.").format(name=name))
            self._show(rep, name)

        if not c.connected:          # Firmware ohne Editor-Protokoll: nur Kennung
            finish((None, ""))
            return True

        def work(w, link):
            from ..protocol import Cmd
            from ..transport import LinkError
            try:
                return fm.parse_resource_status(link.request(Cmd.RESOURCES, [0], timeout=3.0, retries=0)), ""
            except (LinkError, ValueError):
                return None, (_("no answer to command 82 – this firmware does not know the resource query")
                              if not (version or "").lower().startswith("melodee 1.6") else
                              _("no answer to command 82 (stop playback, switch Bluetooth off and try again)"))
        self._set_busy(True, _("Quick check of “{name}” running …").format(name=name))
        c.job(self, _("Quick check"), work, dialog=False, on_done=finish,
              on_fail=lambda m, tb: (self._set_busy(False), show_error(
                  self, _("Quick check failed"), _("{error} Nothing was changed on the FM-1.").format(error=m))))
        return True

    # ------------------------------------------------------------ Teil 2: Dump
    def dump_file(self, path: str = ""):
        if not path:
            start = config.folder("Rettung")
            ex = start / "Beispiel-Dumps"  # i18n: ignore (folder name on disk)
            path, _filter = QFileDialog.getOpenFileName(self, _("Choose flash dump (1 MiB)"),
                                                        str(ex if DEMO and ex.is_dir() else start),
                                                        _("Flash dump (*.bin);;All files (*)"))
        if not path:
            return False
        self._set_busy(True, _("Checking “{name}” …").format(name=Path(path).name))
        run_job(self, lambda w: analyse_dump(path),
                on_done=lambda rep: (self._set_busy(False), self._show(rep)),
                on_fail=lambda m, tb: (self._set_busy(False), show_error(self, _("Dump check not possible"), m)))
        return True

    def demo_examples(self):
        folder = config.folder("Rettung") / "Beispiel-Dumps"  # i18n: ignore (folder name on disk)
        try:
            folder.mkdir(parents=True, exist_ok=True)
            for name, data in fm.demo_dumps().items():
                (folder / name).write_bytes(data)
        except OSError as e:
            show_error(self, _("Example dumps"), _("The folder {folder} is not writable ({error}).").format(
                folder=folder, error=e.strerror))
            return False
        self.status.setText(_("Three example dumps are in {folder}: demo-sauber.bin, demo-v16-reste.bin, "
                              "demo-v16-abgeschnitten.bin. Now “Check dump file …”.").format(folder=folder))
        return True

    def rom_dump(self):
        from ..flash import uboot
        sim = self.sim()
        if not sim:
            if not uboot.tool_ready():
                show_error(self, _("Tool missing"), _("First “Fetch tool” in stage 2 – without jl-uboot-tool the app "
                                                      "cannot read the flash. Nothing was changed."))
                return False
            devs = uboot.scan()
            if len(devs) != 1:
                msg = _("Exactly one device “WL82 UBOOT1.00” (USB 4c4a:8057) must be connected. How the FM-1 gets "
                        "there: see stage 2. The app never chooses a drive by itself.")
                if devs:
                    msg = _("{message} Found: {devices}").format(message=msg, devices=", ".join(d.sg for d in devs))
                show_error(self, _("No device in ROM boot mode"), msg)
                return False
            dev = devs[0].sg
        else:
            dev = ""
        from .risk_dialog import ensure_risk_ack
        if not ensure_risk_ack(self):
            return False
        if not confirm(self, _("Read && check in ROM boot mode"),
                       (_("<b>Simulation:</b> the tool reads its practice flash (random data) – nothing happens on the "
                          "FM-1.") + "<br><br>" if sim else "") +
                       _("The built-in unbricker reads the whole flash <b>twice</b> and compares both readings. "
                         "<b>Nothing is written</b>. Needs administrator rights (password prompt). Afterwards switch the "
                         "FM-1 off and on again."), _("Read")):
            return False
        folder = uboot.work_dir() / "Komplettsicherungen"
        before = {p: p.stat().st_mtime for p in folder.rglob("full-flash-before.bin")} if folder.is_dir() else {}

        def work(w):
            r = (self.runner or (lambda log: uboot.Runner(dry_run=sim, log=log)))(w.log.emit)
            rc = r.backup(str(folder), device=dev)
            if rc != 0:
                raise RuntimeError(_("The tool reports an error (code {code}) – details in the log. Nothing was "
                                     "written.").format(code=rc))
            new = [p for p in folder.rglob("full-flash-before.bin") if before.get(p) != p.stat().st_mtime]
            if not new:
                raise RuntimeError(_("The tool did not store a full backup – details in the log."))
            path = max(new, key=lambda p: p.stat().st_mtime)
            w.log.emit(_("Full backup: {path}").format(path=path))
            rep = analyse_dump(path)
            rep.title = _("Read in ROM boot mode ({folder})").format(folder=path.parent.name)
            if sim:
                rep.add(fm.YELLOW, _("Simulation: that was the practice flash of the tool, not your FM-1."))
            return rep, path
        from . import feedback
        d = feedback.run(self, _("Read in ROM boot mode"), work,
                         done=lambda res: _("Read (twice, compared) and saved: {path}. Nothing was written on the "
                                            "FM-1 – now switch it off and on again.").format(path=res[1]))
        if d.ok and d.result_value:
            rep, path = d.result_value
            self._show(rep)
        return d.ok

    # ------------------------------------------------------------ Expertenmodus: V16-Reste aufräumen
    def _expert_toggled(self, on: bool):
        self.opts["experte"] = bool(on)
        config.save()
        self.b_clean.setVisible(on)

    def _rom_device(self, title: str):
        """(Gerätepfad, ok) – Simulation: ("", True); echt: genau ein WL82 UBOOT1.00 mit Werkzeug"""
        from ..flash import uboot
        if self.sim():
            return "", True
        if not uboot.tool_ready():
            show_error(self, title, _("First “Fetch tool” in stage 2 – without jl-uboot-tool the app cannot talk to "
                                      "the chip. Nothing was changed."))
            return "", False
        devs = uboot.scan()
        if len(devs) != 1 or not devs[0].size_ok:
            show_error(self, title, _("Exactly one FM-1 in ROM boot mode (“WL82 UBOOT1.00”, USB 4c4a:8057, 1 MiB) "
                                      "must be connected. The app never chooses a drive by itself. Found: {devices}. "
                                      "Nothing was changed.").format(
                devices=", ".join(d.describe() for d in devs) if devs else pgettext("device", "none")))
            return "", False
        return devs[0].sg, True

    def _residue_job(self, title: str, command: str, dev: str, only=None, done: str = ""):
        """restzone über den Unbricker-Weg (root/pkexec bzw. Simulation) -> (Ergebnis-dict, Report) oder None"""
        import json
        import time
        from ..flash import restzone, uboot
        folder = uboot.work_dir() / "Reste-aufräumen"  # i18n: ignore (folder name on disk)
        result = folder / f"ergebnis-{command}-{time.strftime('%Y%m%d-%H%M%S')}.json"

        def work(w):
            # vorliegende V16-Pakete helfen bei halb geschriebenen Sektoren (sonst genügen die Steckbrief-Prüfsummen)
            v16 = [str(p.path) for p in local_packages() if fm.classify_family(p) == "v16-layout"]
            r = (self.runner or (lambda log: uboot.Runner(dry_run=self.sim(), log=log)))(w.log.emit)
            rc = r.residue(command, str(folder), str(result), only=only, device=dev, v16_packages=v16)
            try:
                res = json.loads(result.read_text("utf-8"))
            except (OSError, ValueError):
                raise RuntimeError(_("The tool did not store a result (code {code}) – details in the log. {outcome}").format(
                    code=rc, outcome=_("Nothing was erased.") if command == "plan" else
                    _("Whether anything was erased is in the log and in the journal in the recovery folder.")))
            rep = restzone.report(res)
            if res.get("mock"):
                rep.add(fm.YELLOW, _("Simulation: that was the practice flash of the tool, not your FM-1."))
            return res, rep
        from . import feedback
        d = feedback.run(self, title, work, done=done or (lambda v: _("{level} – result follows.").format(
            level=level_text(v[1].level))))
        return d.result_value if d.ok and d.result_value else None

    def clean_residue(self) -> bool:
        """Expertenmodus: Schritt 1 sichern + planen (nichts löschen), Schritt 2 nach Tipp-Bestätigung genau die
        geplanten Sektoren löschen – mit frischer Sicherung, nur was diese erneut als V16-Rest bestätigt."""
        title = _("Clean up V16 leftovers")
        dev, ok = self._rom_device(title)
        if not ok:
            return False
        sim = self.sim()
        from .risk_dialog import ensure_risk_ack
        if not ensure_risk_ack(self):
            return False
        if not confirm(self, _("{title} – step 1 of 2").format(title=title),
                       (_("<b>Simulation:</b> everything runs against the practice flash of the tool – nothing happens "
                          "on the FM-1.") if sim else _("<b>Experimental, untested on a real device.</b>")) + "<br><br>" +
                       _("The FM-1 must be in ROM boot mode. First the unbricker reads the whole flash <b>twice</b>, "
                         "compares it and saves it as a file. Then the app looks in 0x93000–0xA8FFF for sectors that "
                         "still carry exactly V16 bytes. <b>Nothing is erased in this step.</b>")
                       + ("" if sim else " " + _("Needs administrator rights (password prompt).")), _("Read && plan")):
            return False
        got = self._residue_job(_("{title}: read and plan").format(title=title), "plan", dev)
        if not got:
            return False
        res, rep = got
        planned = [int(p["addr"], 16) for p in res.get("planned", [])]
        if res.get("refused") or not planned:
            self._show(rep, "Plan")
            return True
        rng = fm.ranges_text(fm.merge_sectors(planned))
        self._show(rep, "Plan")
        n = len(planned)
        if not confirm(self, _("{title} – step 2 of 2").format(title=title),
                       ngettext("<b>{n} sector of 4 KiB will be erased: {ranges}</b> – only V16 leftovers, nothing of "
                                "your firmware ({firmware}).",
                                "<b>{n} sectors of 4 KiB each will be erased: {ranges}</b> – only V16 leftovers, nothing "
                                "of your firmware ({firmware}).", n).format(n=n, ranges=rng,
                                                                            firmware=res.get('firmware', '?'))
                       + "<br><br>" +
                       _("Beforehand the tool reads the flash twice once more and backs it up; only what this fresh "
                         "backup confirms again as a V16 leftover is erased. It checks after every sector, and the "
                         "whole flash at the end. Boot header, firmware and everything outside 0x93000–0xA8FFF stay as "
                         "they are.")
                       + "<br><br>" + _("Backup from step 1: {path}").format(path=res.get("backup", "")) +
                       ("" if sim else "<br><br>" + _("<b>Untested on a real device.</b> Do not unplug anything while "
                                                      "erasing. An aborted run can be resumed with this button.")),
                       _("Continue"), danger=True):
            return False
        # Translators: the word the user must type to confirm the erase – capital letters
        word = pgettext("typed confirmation", "CLEAN UP")
        text, ok = QInputDialog.getText(self, _("{title} – confirm").format(title=title),
                                        ngettext("To erase {n} sector, type {word}:",
                                                 "To erase {n} sectors, type {word}:", n).format(n=n, word=word))
        accepted = {word.upper(), word.upper().replace("Ä", "AE").replace("Ö", "OE").replace("Ü", "UE")}
        if not ok or text.strip().upper() not in accepted:
            self.status.setText(_("Clean-up cancelled – nothing was erased."))
            return False

        def done(v):
            erased = len(v[0].get("erased", []))
            msg = ngettext("{level}: {n} sector erased and checked.", "{level}: {n} sectors erased and checked.",
                           erased).format(level=level_text(v[1].level), n=erased)
            if v[0].get("mock"):
                return _("Simulation finished – nothing was changed on the FM-1. {result}").format(result=msg)
            return _("{result} Now switch the FM-1 off and on again.").format(result=msg)
        got = self._residue_job(_("{title}: erase").format(title=title), "clean", dev, only=planned, done=done)
        if not got:
            return False
        self._show(got[1], _("Clean-up"))
        return got[0].get("ok", False)
