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
from .common import Card, Disclosure, confirm, flow_row, icon, run_job, show_error

DEMO = os.environ.get("FM1C_DEMO") == "1"
MAX_PACKAGES = 40

INTRO = ("Prüft, ob nach vielen Firmware-Wechseln alles an seinem Platz ist. <b>Schnellprüfung:</b> nur lesen über USB, "
         "ohne Risiko. <b>Dump-Prüfung:</b> vergleicht eine Komplettsicherung des Flashs Sektor für Sektor mit allen "
         "Paketen, die die App kennt.")
DETAILS = ("<b>Schnellprüfung</b> fragt Kennung und Version ab, bei Melodee ab 1.6 zusätzlich den Ressourcen-Status "
           "(Werkssounds, Schrift, Bluetooth-Programm): Die Firmware rechnet dafür Kopf und CRC-32 selbst nach. Sie sieht "
           "nicht in den restlichen Flash.<br><br>"
           "<b>Dump-Prüfung</b> braucht eine 1-MiB-Komplettsicherung (z. B. „Notfall-Komplettsicherung“ in Stufe 2 "
           "oder „full-flash-before.bin“ aus dem Rettungsordner). Sie prüft Bootkopf, Firmware-Bereich, ein "
           "abgeschnittenes V16, Reste von V16 in den Datenbereichen und die Melodee-Ressourcen. Gelesen wird im "
           "ROM-Bootmodus nur – geschrieben wird nie.<br><br>"
           "<b>V16-Reste aufräumen</b> (Expertenmodus, ROM-Bootmodus, <b>am echten Gerät ungetestet</b>): löscht "
           "gezielt einzelne 4-KiB-Sektoren in 0x93000–0xA8FFF – aber nur solche, die laut einer frisch gelesenen "
           "Sicherung noch genau V16-Bytes tragen, und nur, wenn im Flash byte-genau eine bekannte Firmware mit "
           "V15-Aufbau steht (Melodee, V15, SLOOP …), nie bei V16. Geschrieben wird nichts. Vorher wird der ganze Flash "
           "zweimal gelesen und gesichert, nach jedem Sektor geprüft, am Ende der ganze Flash verglichen; ein Journal "
           "macht einen abgebrochenen Lauf fortsetzbar. Nach allem, was bisher bekannt ist (Emulator, Quelltext), sind "
           "die Reste harmlos – liegen lassen ist also auch in Ordnung.")

LEVEL_COLOR = {fm.GREEN: "success", fm.YELLOW: "warning", fm.RED: "danger"}


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
        raise RuntimeError(f"{path.name} lässt sich nicht lesen ({e.strerror}). Es wurde nichts verändert.")
    refs = fm.load_known()
    heads = {fm.FACTORY_HEAD_SHA256}
    if DEMO:
        refs += fm.demo_refs()
        heads |= fm.demo_heads()
    try:
        rep = fm.check_dump(data, refs, packages=local_packages(), factory_heads=heads)
    except fm.DumpError as e:
        raise RuntimeError(f"{e} Bitte eine Komplettsicherung wählen (z. B. full-flash-before.bin).")
    rep.title = f"Flash-Dump „{path.name}“"
    return rep


class ReportDialog(QDialog):
    """Ergebnis mit Ampel, Sätzen in Alltagssprache, „Was das nicht abdeckt“ und technischen Details"""

    def __init__(self, parent, rep: fm.Report, source: str = "", window=None):
        super().__init__(parent)
        self.rep, self.window = rep, window
        self.setWindowTitle("Ist mein FM-1 sauber?")
        self.setMinimumWidth(520)
        self.resize(720, 480)
        outer = QVBoxLayout(self)
        head = QLabel(f"<span style='color:{_color(rep.level)}; font-size:20px'>●</span> "
                      f"<b style='font-size:15px'>{fm.LEVEL_TEXT[rep.level]}</b>"
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
            nc = QLabel("<b>Was diese Prüfung nicht abdeckt:</b><ul style='margin-left:-20px'>"
                        + "".join(f"<li>{t}</li>" for t in rep.not_covered) + "</ul>")
            nc.setObjectName("CleanNotCovered")
            nc.setWordWrap(True)
            nc.setTextFormat(Qt.TextFormat.RichText)
            lay.addWidget(nc)
        det = list(rep.details)
        if rep.compared:
            det.append("Verglichen mit: " + ", ".join(rep.compared))
        if det:
            lay.addWidget(Disclosure("Technische Details", "<br>".join(det)))
        lay.addStretch(1)
        bb = QDialogButtonBox()
        b_copy = bb.addButton("Bericht kopieren", QDialogButtonBox.ButtonRole.ActionRole)
        b_copy.setToolTip("Den Bericht als Text in die Zwischenablage legen – z. B. für eine Frage im Forum")
        b_copy.clicked.connect(self.copy)
        self.b_res = None
        if any("nachschreiben" in t for _, t in rep.findings) and window is not None and window.page("firmware"):
            self.b_res = bb.addButton("Zu Firmware+ › Installieren", QDialogButtonBox.ButtonRole.ActionRole)
            self.b_res.setToolTip("Dort: „Schrift & Werkssounds nachschreiben“")
            self.b_res.clicked.connect(lambda: (self.accept(), window.go("firmware")))
        close = bb.addButton("Schließen", QDialogButtonBox.ButtonRole.RejectRole)
        close.setDefault(True)
        bb.rejected.connect(self.reject)
        outer.addWidget(bb)

    def text(self) -> str:
        r = self.rep
        lines = [f"Ist mein FM-1 sauber? – {fm.LEVEL_TEXT[r.level]}" + (f" ({r.firmware})" if r.firmware else ""),
                 r.title, ""]
        lines += [f"[{lv}] {t}" for lv, t in r.findings]
        if r.not_covered:
            lines += ["", "Nicht abgedeckt:"] + [f"- {t}" for t in r.not_covered]
        if r.details:
            lines += ["", "Details:"] + r.details
        from ..redact import redact_text
        return redact_text("\n".join(lines))

    def copy(self):
        QApplication.clipboard().setText(self.text())


class CleanCheckCard(Card):
    """die Karte auf der Rettungsseite. sim(): Simulationshäkchen der Stufe 2; runner(log): uboot.Runner"""

    def __init__(self, ctx, page, window=None, sim=lambda: DEMO, runner=None):
        super().__init__("Ist mein FM-1 sauber?")
        self.ctx, self.page, self.window, self.sim, self.runner = ctx, page, window, sim, runner
        self.last_report: fm.Report | None = None
        self.last_dialog: ReportDialog | None = None
        self._busy = False
        intro = QLabel(INTRO)
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.RichText)
        self.layout().addWidget(intro)
        self.b_quick = QPushButton(icon("dialog-information", "help-about"), "Schnellprüfung (nur lesen)")
        self.b_quick.setProperty("variant", "primary")
        self.b_quick.clicked.connect(self.quick)
        self.b_file = QPushButton(icon("document-open"), "Dump-Datei prüfen …")
        self.b_file.setToolTip("Eine 1-MiB-Komplettsicherung des Flashs wählen und mit allen bekannten Paketen "
                               "vergleichen – am FM-1 passiert dabei nichts.")
        self.b_file.clicked.connect(lambda: self.dump_file())
        self.b_rom = QPushButton(icon("drive-removable-media", "document-save"), "Im ROM-Bootmodus lesen && prüfen …")
        self.b_rom.setToolTip("Für Fortgeschrittene: das FM-1 im ROM-Bootmodus („WL82 UBOOT1.00“) zweimal komplett "
                              "auslesen (nur lesen, braucht Administratorrechte) und gleich prüfen.")
        self.b_rom.clicked.connect(self.rom_dump)
        row = [self.b_quick, self.b_file, self.b_rom]
        if DEMO:
            self.b_demo = QPushButton(icon("document-new"), "Beispiel-Dumps anlegen (Demo)")
            self.b_demo.setToolTip("Legt drei erfundene Dumps an (sauber, mit V16-Resten, V16 abgeschnitten), "
                                   "damit du die Dump-Prüfung ohne Gerät ausprobieren kannst.")
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
        self.chk_expert = QCheckBox("Expertenmodus: V16-Reste im ROM-Bootmodus aufräumen")
        self.chk_expert.setToolTip("Zeigt „V16-Reste aufräumen …“. Löscht nur Sektoren, die nachweislich noch V16 "
                                   "enthalten – am echten Gerät ungetestet.")
        self.chk_expert.setChecked(bool(self.opts.get("experte")))
        self.chk_expert.toggled.connect(self._expert_toggled)
        self.b_clean = QPushButton(icon("edit-clear", "edit-delete"), "V16-Reste aufräumen …")
        self.b_clean.setProperty("variant", "danger")
        self.b_clean.setToolTip("Im ROM-Bootmodus: sichern, Reste von V16 in 0x93000–0xA8FFF suchen, nach deiner "
                                "Bestätigung genau diese Sektoren löschen und prüfen. Am echten Gerät ungetestet.")
        self.b_clean.clicked.connect(lambda: self.clean_residue())
        self.layout().addWidget(flow_row(self.chk_expert, self.b_clean))
        self.b_clean.setVisible(self.chk_expert.isChecked())
        self.layout().addWidget(Disclosure("Details: was geprüft wird, was nicht, Reste aufräumen", DETAILS))
        ctx.changed.connect(self.refresh)
        self.refresh()

    # ------------------------------------------------------------ Zustand
    def refresh(self):
        c = self.ctx
        if self._busy:
            return
        if c.port is None:
            tip = "Erst ein FM-1 per USB anschließen (normal eingeschaltet, nicht im Update-Modus)."
            ok = False
        elif c.protocol == "update":
            tip = "Das FM-1 wartet im Update-Modus – erst die Installation abschließen (Stufe 1)."
            ok = False
        else:
            ok = True
            tip = ("Liest Kennung, Version und – bei Melodee ab 1.6 – den Ressourcen-Status. Ändert nichts am FM-1."
                   if c.connected else "Liest die Kennung des FM-1. Ändert nichts am FM-1.")
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
        self.status.setText(f"Letztes Ergebnis: {fm.LEVEL_TEXT[rep.level]}" + (f" – {rep.firmware}" if rep.firmware
                                                                              else "") + (f" ({source})" if source else ""))
        self.last_dialog = ReportDialog(self, rep, source, self.window)
        self.last_dialog.exec()

    # ------------------------------------------------------------ Teil 1: nur lesen
    def quick(self):
        c = self.ctx
        if c.port is None or c.protocol == "update":
            show_error(self, "Schnellprüfung nicht möglich", self.b_quick.toolTip() + " Es wurde nichts verändert.")
            return False
        port_key, name = c.port.key, (c.profile.name if c.profile else c.port.label)
        identity = c.identity or (c.profile.identity if c.profile else "")
        version = c.info.version if c.info else (c.profile.version_string if c.profile else "")

        def finish(res):
            status, err = res
            self._set_busy(False)
            rep = fm.quick_check(identity, version, status, res_error=err)
            if self.ctx.port is None or self.ctx.port.key != port_key:
                rep.add(fm.YELLOW, f"Während der Prüfung wurde das Gerät gewechselt oder getrennt – dieser Bericht gilt "
                                   f"für „{name}“.")
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
                return None, ("keine Antwort auf Befehl 82 – diese Firmware kennt die Ressourcen-Abfrage nicht"
                              if not (version or "").lower().startswith("melodee 1.6") else
                              "keine Antwort auf Befehl 82 (Wiedergabe stoppen, Bluetooth aus und erneut versuchen)")
        self._set_busy(True, f"Schnellprüfung von „{name}“ läuft …")
        c.job(self, "Schnellprüfung", work, dialog=False, on_done=finish,
              on_fail=lambda m, tb: (self._set_busy(False), show_error(
                  self, "Schnellprüfung fehlgeschlagen", f"{m} Am FM-1 wurde nichts verändert.")))
        return True

    # ------------------------------------------------------------ Teil 2: Dump
    def dump_file(self, path: str = ""):
        if not path:
            start = config.folder("Rettung")
            ex = start / "Beispiel-Dumps"
            path, _ = QFileDialog.getOpenFileName(self, "Flash-Dump (1 MiB) wählen",
                                                  str(ex if DEMO and ex.is_dir() else start),
                                                  "Flash-Dump (*.bin);;Alle Dateien (*)")
        if not path:
            return False
        self._set_busy(True, f"Prüfe „{Path(path).name}“ …")
        run_job(self, lambda w: analyse_dump(path),
                on_done=lambda rep: (self._set_busy(False), self._show(rep)),
                on_fail=lambda m, tb: (self._set_busy(False), show_error(self, "Dump-Prüfung nicht möglich", m)))
        return True

    def demo_examples(self):
        folder = config.folder("Rettung") / "Beispiel-Dumps"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            for name, data in fm.demo_dumps().items():
                (folder / name).write_bytes(data)
        except OSError as e:
            show_error(self, "Beispiel-Dumps", f"Der Ordner {folder} ist nicht beschreibbar ({e.strerror}).")
            return False
        self.status.setText(f"Drei Beispiel-Dumps liegen in {folder}: demo-sauber.bin, demo-v16-reste.bin, "
                            "demo-v16-abgeschnitten.bin. Jetzt „Dump-Datei prüfen …“.")
        return True

    def rom_dump(self):
        from ..flash import uboot
        sim = self.sim()
        if not sim:
            if not uboot.tool_ready():
                show_error(self, "Werkzeug fehlt", "Zuerst in Stufe 2 „Werkzeug holen“ – ohne jl-uboot-tool kann die "
                                                   "App den Flash nicht lesen. Es wurde nichts verändert.")
                return False
            devs = uboot.scan()
            if len(devs) != 1:
                show_error(self, "Kein Gerät im ROM-Bootmodus",
                           "Es muss genau ein Gerät „WL82 UBOOT1.00“ (USB 4c4a:8057) angeschlossen sein. So kommt das "
                           "FM-1 dorthin: siehe Stufe 2. Die App wählt nie selbst einen Datenträger aus."
                           + (" Gefunden: " + ", ".join(d.sg for d in devs) if devs else ""))
                return False
            dev = devs[0].sg
        else:
            dev = ""
        from .risk_dialog import ensure_risk_ack
        if not ensure_risk_ack(self):
            return False
        if not confirm(self, "Im ROM-Bootmodus lesen && prüfen",
                       ("<b>Simulation:</b> Das Werkzeug liest seinen Übungs-Flash (Zufallsdaten) – am FM-1 passiert "
                        "nichts.<br><br>" if sim else "") +
                       "Der eingebaute Unbricker liest den ganzen Flash <b>zweimal</b> und vergleicht beide Lesungen. "
                       "Es wird <b>nichts geschrieben</b>. Braucht Administratorrechte (Passwortabfrage). Danach das FM-1 "
                       "aus- und wieder einschalten.", "Lesen"):
            return False
        folder = uboot.work_dir() / "Komplettsicherungen"
        before = {p: p.stat().st_mtime for p in folder.rglob("full-flash-before.bin")} if folder.is_dir() else {}

        def work(w):
            r = (self.runner or (lambda log: uboot.Runner(dry_run=sim, log=log)))(w.log.emit)
            rc = r.backup(str(folder), device=dev)
            if rc != 0:
                raise RuntimeError(f"Das Werkzeug meldet einen Fehler (Code {rc}) – Details im Protokoll. Es wurde nichts "
                                   "geschrieben.")
            new = [p for p in folder.rglob("full-flash-before.bin") if before.get(p) != p.stat().st_mtime]
            if not new:
                raise RuntimeError("Das Werkzeug hat keine Komplettsicherung abgelegt – Details im Protokoll.")
            path = max(new, key=lambda p: p.stat().st_mtime)
            w.log.emit(f"Komplettsicherung: {path}")
            rep = analyse_dump(path)
            rep.title = f"Im ROM-Bootmodus gelesen ({path.parent.name})"
            if sim:
                rep.add(fm.YELLOW, "Simulation: Das war der Übungs-Flash des Werkzeugs, nicht dein FM-1.")
            return rep, path
        from . import feedback
        d = feedback.run(self, "Im ROM-Bootmodus lesen", work,
                         done=lambda res: f"Gelesen (zweimal, verglichen) und gespeichert: {res[1]}. Am FM-1 wurde "
                                          "nichts geschrieben – jetzt aus- und wieder einschalten.")
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
            show_error(self, title, "Zuerst in Stufe 2 „Werkzeug holen“ – ohne jl-uboot-tool kann die App nicht mit "
                                    "dem Chip sprechen. Es wurde nichts verändert.")
            return "", False
        devs = uboot.scan()
        if len(devs) != 1 or not devs[0].size_ok:
            show_error(self, title, "Es muss genau ein FM-1 im ROM-Bootmodus („WL82 UBOOT1.00“, USB 4c4a:8057, 1 MiB) "
                                    "angeschlossen sein. Die App wählt nie selbst einen Datenträger aus. Gefunden: "
                       + (", ".join(d.describe() for d in devs) if devs else "keins") + ". Es wurde nichts verändert.")
            return "", False
        return devs[0].sg, True

    def _residue_job(self, title: str, command: str, dev: str, only=None, done: str = ""):
        """restzone über den Unbricker-Weg (root/pkexec bzw. Simulation) -> (Ergebnis-dict, Report) oder None"""
        import json
        import time
        from ..flash import restzone, uboot
        folder = uboot.work_dir() / "Reste-aufräumen"
        result = folder / f"ergebnis-{command}-{time.strftime('%Y%m%d-%H%M%S')}.json"

        def work(w):
            # vorliegende V16-Pakete helfen bei halb geschriebenen Sektoren (sonst genügen die Steckbrief-Prüfsummen)
            v16 = [str(p.path) for p in local_packages() if fm.classify_family(p) == "v16-layout"]
            r = (self.runner or (lambda log: uboot.Runner(dry_run=self.sim(), log=log)))(w.log.emit)
            rc = r.residue(command, str(folder), str(result), only=only, device=dev, v16_packages=v16)
            try:
                res = json.loads(result.read_text("utf-8"))
            except (OSError, ValueError):
                raise RuntimeError(f"Das Werkzeug hat kein Ergebnis abgelegt (Code {rc}) – Details im Protokoll. "
                                   + ("Es wurde nichts gelöscht." if command == "plan" else
                                      "Ob gelöscht wurde, steht im Protokoll und im Journal im Rettungsordner."))
            rep = restzone.report(res)
            if res.get("mock"):
                rep.add(fm.YELLOW, "Simulation: Das war der Übungs-Flash des Werkzeugs, nicht dein FM-1.")
            return res, rep
        from . import feedback
        d = feedback.run(self, title, work, done=done or (lambda v: fm.LEVEL_TEXT[v[1].level] + " – Ergebnis folgt."))
        return d.result_value if d.ok and d.result_value else None

    def clean_residue(self) -> bool:
        """Expertenmodus: Schritt 1 sichern + planen (nichts löschen), Schritt 2 nach Tipp-Bestätigung genau die
        geplanten Sektoren löschen – mit frischer Sicherung, nur was diese erneut als V16-Rest bestätigt."""
        title = "V16-Reste aufräumen"
        dev, ok = self._rom_device(title)
        if not ok:
            return False
        sim = self.sim()
        from .risk_dialog import ensure_risk_ack
        if not ensure_risk_ack(self):
            return False
        if not confirm(self, f"{title} – Schritt 1 von 2",
                       ("<b>Simulation:</b> Alles läuft gegen den Übungs-Flash des Werkzeugs – am FM-1 passiert nichts."
                        "<br><br>" if sim else "<b>Experimentell, am echten Gerät ungetestet.</b><br><br>") +
                       "Das FM-1 muss im ROM-Bootmodus sein. Zuerst liest der Unbricker den ganzen Flash <b>zweimal</b>, "
                       "vergleicht und sichert ihn als Datei. Dann sucht die App in 0x93000–0xA8FFF nach Sektoren, die "
                       "noch genau V16-Bytes tragen. <b>In diesem Schritt wird nichts gelöscht.</b>"
                       + ("" if sim else " Braucht Administratorrechte (Passwortabfrage)."), "Lesen && planen"):
            return False
        got = self._residue_job(f"{title}: lesen und planen", "plan", dev)
        if not got:
            return False
        res, rep = got
        planned = [int(p["addr"], 16) for p in res.get("planned", [])]
        if res.get("refused") or not planned:
            self._show(rep, "Plan")
            return True
        rng = fm.ranges_text(fm.merge_sectors(planned))
        self._show(rep, "Plan")
        if not confirm(self, f"{title} – Schritt 2 von 2",
                       f"<b>Gelöscht werden {len(planned)} Sektoren à 4 KiB: {rng}</b> – nur V16-Reste, nichts von "
                       f"deiner Firmware ({res.get('firmware', '?')}).<br><br>"
                       "Vorher liest das Werkzeug den Flash noch einmal zweimal und sichert ihn; gelöscht wird nur, was "
                       "diese frische Sicherung erneut als V16-Rest bestätigt. Nach jedem Sektor prüft es nach, am Ende "
                       "den ganzen Flash. Bootkopf, Firmware und alles außerhalb 0x93000–0xA8FFF bleiben, wie sie sind."
                       "<br><br>Sicherung von Schritt 1: " + str(res.get("backup", "")) +
                       ("" if sim else "<br><br><b>Am echten Gerät ungetestet.</b> Während des Löschens nichts "
                                       "abstecken. Ein abgebrochener Lauf lässt sich mit diesem Knopf fortsetzen."),
                       "Weiter", danger=True):
            return False
        text, ok = QInputDialog.getText(self, f"{title} – Bestätigen",
                                        f"Zum Löschen von {len(planned)} Sektoren AUFRÄUMEN eintippen:")
        if not ok or text.strip().upper() not in ("AUFRÄUMEN", "AUFRAEUMEN"):
            self.status.setText("Aufräumen abgebrochen – es wurde nichts gelöscht.")
            return False
        got = self._residue_job(f"{title}: löschen", "clean", dev, only=planned,
                                done=lambda v: ("Simulation beendet – am FM-1 wurde nichts verändert. "
                                                if v[0].get("mock") else "") + fm.LEVEL_TEXT[v[1].level]
                                + f": {len(v[0].get('erased', []))} Sektoren gelöscht und geprüft."
                                + ("" if v[0].get("mock") else " Jetzt das FM-1 aus- und wieder einschalten."))
        if not got:
            return False
        self._show(got[1], "Aufräumen")
        return got[0].get("ok", False)
