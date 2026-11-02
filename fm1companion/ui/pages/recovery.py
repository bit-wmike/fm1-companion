# SPDX-License-Identifier: GPL-3.0-only
"""Rettung: FM-1 startet nicht, hängt im Update-Modus oder eine Installation wurde unterbrochen.

Stufe 1 – Update-Modus über USB-MIDI (Journal, richtiges Paket, eigener Loader).
Stufe 2 – ROM-Bootmodus „WL82 UBOOT1.00“ (MvaveFM1Unbricker + jl-uboot-tool, root über pkexec).
Stufe 3 – FM-1 Transporter (Hardware: RP2040-Board an den USB-Datenleitungen).
Grundlagen und Quellen: docs/flash-protocol.md.
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
from ...flash import engine as fe, journal as fj, package as fpk, uboot
from ..common import Card, Disclosure, confirm, heading, icon, show_error

DEMO = os.environ.get("FM1C_DEMO") == "1"

BASICS_SHORT = ("<b>Zuerst:</b> Akku mindestens eine Stunde laden, ein USB-<b>Daten</b>kabel direkt am Rechner "
                "(kein Hub), andere MIDI-Programme und Browser-Tabs schließen.")
BASICS = ("<b>Erst die Grundlagen:</b><ul>"
          "<li>Akku mindestens eine Stunde laden – ein tiefentladener Akku kann den Start verhindern.</li>"
          "<li>Ein anderes USB-<b>Daten</b>kabel (kein reines Ladekabel), einen anderen USB-Anschluss direkt am "
          "Rechner (kein Hub) oder einen anderen Rechner probieren.</li>"
          "<li>Andere Musik-Programme und Browser-Tabs mit MIDI schließen.</li></ul>")

STEPS_UPDATE = ("<ol><li>FM-1 ausschalten.</li>"
                "<li><b>OCT−</b> gedrückt halten und dabei einschalten.</li>"
                "<li><b>Drei kurze Blitze</b> der Tastenbeleuchtung = Update-Modus. Ein langer Blitz = ROM-Bootmodus "
                "→ Stufe 2.</li>"
                "<li>USB-Kabel einstecken – die App erkennt das Gerät hier sofort.</li></ol>")
DETAILS_UPDATE = ("Die OCT−-Tastenkombination ist dokumentiert für Melodee ab 1.3.5, TransitFM1 und Felucca-Firmwares "
                  "mit Startschutz.<br>Laut Community (nicht bestätigt): OCT− und OCT+ zusammen 10 Sekunden halten. "
                  "Achtung: bei Melodee ist OCT−+OCT+ die Kalibrierung. Nicht zweimal hintereinander aus- und "
                  "einschalten, während das FM-1 im Update-Modus ist (TransitFM1: das zweite Mal landet im "
                  "ROM-Bootmodus).<br>Der Update-Loader der M-VAVE FM1 Open meldet sich als „ota-FM1C_…“ – ihm "
                  "schickt die App nichts (dafür den FM1-Open-Updater von M-VAVE benutzen).<br>"
                  "„Installation abschließen“ kennt das unterbrochene Paket aus dem Journal der "
                  "App und prüft vorher, ob der wartende Update-Loader zu diesem Paket gehört – sonst wird nichts "
                  "gesendet.")

STEPS_UBOOT = ("<ol><li>Einmalig „Werkzeug holen“ (jl-uboot-tool, wird geprüft).</li>"
               "<li>FM-1 voll geladen und direkt am Rechner anschließen – unten muss „Gefunden“ stehen.</li>"
               "<li>„Original-V15 zurückschreiben“ – die App sichert vorher den ganzen Flash zweimal und prüft an "
               "dieser Sicherung den Bootkopf: Nur unter dem Kopf ab Werk wird geschrieben.</li>"
               "<li>FM-1 aus- und wieder einschalten.</li></ol>"
               "<small>Eine Sitzung im ROM-Bootmodus hält meist nur <b>einen</b> Werkzeuglauf. Danach (auch nach "
               "einer Ablehnung) das FM-1 aus- und einschalten und neu in den ROM-Bootmodus bringen.</small>")
DETAILS_UBOOT = ("Die App benutzt das mitgelieferte <a href='https://github.com/Quixotic7/MvaveFM1Unbricker'>"
                 "MvaveFM1Unbricker</a> (MIT) mit kagaimiqs jl-uboot-tool. Es sichert den ganzen Flash zweimal und "
                 "vergleicht, prüft Chip-Key 0x980F und Flash-ID 0x856014, schreibt <b>nur 0x4000–0x92FFF</b> aus der "
                 "Original-V15 und liest alles zurück. Unter 0x4000 wird nie geschrieben, nichts wird komplett gelöscht. "
                 "<br><b>Bootkopf:</b> Vor dem Schreiben prüft die App an der Sicherung aus demselben Lauf den Bootkopf "
                 "(0x0000–0x3FFF). Nur unter dem Kopf ab Werk (V13–V15) wird die V15 geschrieben. Hat das Gerät den "
                 "Kopf von V16 (M-VAVEs Updater schreibt ihn bei V16 neu) oder einen unbekannten (z. B. m-bootloader), "
                 "wird nichts geschrieben: Ob V15 darunter startet, ist nicht geklärt, und die V16-App "
                 "(0x4000–0xA8FFF) kann dieses Werkzeug nicht schreiben. Dann M-Flasher (unten) oder "
                 "<a href='https://github.com/hugelton/felucca-bsp'>fm1_rescue.py</a> aus felucca-bsp benutzen – es "
                 "wählt das Paket passend zum Kopf. Den Kopf schreibt die App nie.<br>"
                 "Braucht Administratorrechte (pkexec) und das Kernelmodul <tt>sg</tt> (USB 4c4a:8057).<br>"
                 "<b>Ehrlich:</b> In dieser App nur in der Simulation getestet; das Werkzeug selbst wurde von der "
                 "Community an echten Geräten benutzt.<br><br>"
                 "<b>Alternative: <a href='https://github.com/jvitkauskas/m-flasher'>M-Flasher</a></b> (MIT, eigenes "
                 "Programm mit Flash-Karte; schreibt nur, was sich ändert, und prüft alles). Download-Modus: laufendes "
                 "Original- oder Felucca-FM-1 per Knopf „DOWNLOAD MODE“; Felucca/SLOOP: OCT− und OCT+ 5 s halten; mit "
                 "m-bootloader: REC beim Einschalten. Für den Pi gibt es ein ARM64-Paket "
                 "(<tt>sudo apt install ./m-flasher_…_arm64.deb</tt>).<br><br>"
                 "<small>Felucca 0.9 → 1.0.x (bekannt): 1.0.x startet wegen alter 0.9-Daten nicht. Abhilfe laut Anleitung: "
                 "Datenbereiche 0x97000 (32 KiB) und 0xFC000 (8 KiB) löschen – das löscht Songs und Einstellungen. Die App "
                 "macht dafür „Einstellungen reparieren“ mit Werks-Dump (Backup ist dabei Pflicht). Das geht nur, wenn "
                 "die Sicherung aus demselben Lauf den V15-Aufbau zeigt (Firmware endet bei 0x93000) – bei V16 liegt "
                 "in 0x93000–0xA8FFF noch Firmware, dann wird nichts geschrieben.</small><br><br>"
                 "<b>Wenn das FM-1 gar nicht als „WL82 UBOOT1.00“ erscheint</b> (nach MvaveFM1Unbricker, "
                 "Troubleshooting): 1. <b>Zwei Minuten eingeschaltet lassen</b> – Felucca-Firmwares haben einen "
                 "Watchdog und Startschutz, der nach zwei Fehlstarts von selbst in den ROM-Bootmodus wechselt. "
                 "2. OCT− und OCT+ beim Einschalten 10 s halten (Update-Modus). 3. <b>Drei schnelle Neustarts:</b> "
                 "ein, 10 s warten, aus; ein, 10 s, aus; ein – stürzt jeder Start ab, landet der dritte im "
                 "ROM-Bootmodus. Erst wenn nichts davon hilft: Stufe 3 (Hardware).")

TIER3_SHORT = ("<b>Symptom:</b> Das FM-1 erscheint weder im Update-Modus noch als „WL82 UBOOT1.00“ – auch nicht "
               "nach <b>zwei Minuten eingeschaltet</b> (Watchdog) und <b>drei schnellen Neustarts</b> (ein, 10 s, "
               "aus; ein, 10 s, aus; ein). Dann hilft nur Hardware: ein <b>Seeed XIAO RP2040</b> an den "
               "USB-Datenleitungen erzwingt den ROM-Bootmodus "
               "(<a href='https://github.com/kurogedelic/FM-1-transporter'>FM-1 Transporter</a>).")
STEPS_TIER3 = ("<ol><li>XIAO RP2040 nach Anleitung an D+, D− und GND des FM-1 anschließen (VBUS frei).</li>"
               "<li>Transporter-Firmware mit dem Unbricker-Werkzeug im Terminal aufspielen.</li>"
               "<li>Im Terminal: rekey → info → dump → restore (die App führt Stufe 3 nicht selbst aus).</li></ol>")
TIER3 = ("<b>Verkabelung (getestet, TRANSPORTER-GUIDE):</b> XIAO <b>D6 (GP0) → D+ (grün)</b>, "
         "<b>D7 (GP1) → D− (weiß)</b>, <b>GND → GND (schwarz)</b>, <b>VBUS (rot) nicht anschließen</b> (abkleben). "
         "UF2 aufspielen: BOOT-Taste halten und einstecken. <b>Ehrlich:</b> Die mitgelieferte UF2 ist nur ein "
         "Platzhalter – die echte Transporter-Firmware entsteht erst mit <tt>python3 fm1_transporter_recover.py "
         "setup</tt> aus dem Unbricker-Repo (MvaveFM1Unbricker). Danach, ebenfalls mit diesem Werkzeug im Terminal: "
         "rekey → info (muss <tt>key=980F id=856014</tt> zeigen) → dump (zweimal lesen, vergleichen, behalten) → "
         "restore --write. Die App selbst führt Stufe 3 nicht aus.<br><br>"
         "<b>Nur RP2040! Keine RP2350-Boards</b> (GPIO-Erratum und D+-Pull-up machen den Transporter unbrauchbar).<br><br>"
         "<b>Ungetestet, nicht von der Community bestätigt:</b> mit einem <b>Raspberry Pi Pico (RP2040)</b> statt XIAO – "
         "Pin 1 = GP0 → D+, Pin 2 = GP1 → D−, ein beliebiger GND, VBUS frei. Entweder die XIAO-UF2 probieren oder "
         "kurogedelic/FM-1-transporter mit <tt>-DPICO_BOARD=pico</tt> und demselben Loader neu bauen (pico-sdk). "
         "Der XIAO bleibt der dokumentierte Weg.")

FAQ = ("<b>… eine Installation abbricht?</b> Nichts abziehen, Kabel prüfen, dann hier „Installation abschließen“ – "
       "die App kennt das unterbrochene Paket aus ihrem Journal.<br>"
       "<b>… das FM-1 „FAILED -5“ zeigt?</b> Die laufende Firmware hat die Original-Firmware abgelehnt (CHECK HEAD). "
       "Es wurde nichts geschrieben; das FM-1 läuft weiter. Problem beim Firmware-Autor melden.<br>"
       "<b>… Presets nach der Rückkehr zur Original-Firmware verstümmelt sind?</b> System &gt; Reset Patches.<br>"
       "<b>Vorbeugen:</b> Akku voll, nach jedem erfolgreichen Flash ein Backup behalten. Der Umweg über die "
       "Original-V15 vor Fremd-Firmware ist voreingestellt, aber selbst ein Schreibvorgang: zwei Installationen "
       "statt einer, also zweimal ein Zeitraum, in dem Akku und Kabel halten müssen. Pflicht ist er nur bei V16 und "
       "bei Firmwares, die nur von V15 aus gehen.")


class RecoveryPage(QWidget):
    def __init__(self, ctx, window=None):
        super().__init__(window)
        self.ctx, self.window = ctx, window
        self.journal = fj.Journal()
        outer = QVBoxLayout(self)
        outer.addWidget(heading("Rettung", "FM-1 startet nicht, hängt im Update-Modus oder eine Installation wurde "
                                           "unterbrochen? Schritt für Schritt zurück zu einem laufenden Gerät."))
        area = QScrollArea()
        area.setWidgetResizable(True)
        body = QWidget()
        lay = QVBoxLayout(body)
        area.setWidget(body)
        outer.addWidget(area, 1)

        basics = self._label(BASICS_SHORT)
        lay.addWidget(basics)
        self.details: dict[str, Disclosure] = {}
        self.details["basics"] = Disclosure("Details: Grundlagen", BASICS)
        lay.addWidget(self.details["basics"])

        # ---- Stufe 1
        c1 = Card("Stufe 1 – Update-Modus: Installation abschließen")
        self.status = self._label("")
        self.status.setObjectName("rettungStatus")
        c1.layout().addWidget(self._label("<b>Symptom:</b> schwarzer Bildschirm oder „UPDATING“, Installer melden "
                                          "„Update-Modus“, eine Installation wurde abgebrochen." + STEPS_UPDATE))
        c1.layout().addWidget(self.status)
        self.b_finish = QPushButton(icon("system-software-update"), "Installation abschließen")
        self.b_finish.setProperty("variant", "primary")
        self.b_finish.clicked.connect(self.finish_install)
        self.b_v15 = QPushButton(icon("edit-undo"), "Stattdessen Original-V15 schreiben")
        self.b_v15.setToolTip("Nur möglich, wenn das FM-1 im Original-Update-Modus (ota-FM-1_015) wartet.")
        self.b_v15.clicked.connect(lambda: self.finish_install(stock=True))
        self.b_file = QPushButton(icon("document-open"), "Anderes Paket wählen …")
        self.b_file.clicked.connect(lambda: self.finish_install(choose=True))
        c1.layout().addLayout(_row(self.b_finish, self.b_v15, self.b_file))
        self.details["update"] = Disclosure("Details: Tastenkombinationen, Community-Hinweise, Journal", DETAILS_UPDATE)
        c1.layout().addWidget(self.details["update"])
        lay.addWidget(c1)

        # ---- Stufe 2
        c2 = Card("Stufe 2 – ROM-Bootmodus „WL82 UBOOT1.00“")
        c2.layout().addWidget(self._label(
            "<b>Symptom:</b> schwarzer Bildschirm, kein MIDI-Gerät, der Rechner zeigt einen USB-Datenträger "
            "„WL82 UBOOT1.00“. <b>Nicht formatieren!</b>" + STEPS_UBOOT))
        self.uboot_status = self._label("")
        c2.layout().addWidget(self.uboot_status)
        self.chk_sim = QCheckBox("Simulation – Übungslauf mit dem Mock-FM-1 des Werkzeugs, nichts wird geschrieben")
        self.chk_sim.setChecked(DEMO)
        self.chk_sim.toggled.connect(lambda _on: self.refresh())
        self.chk_sim.setToolTip("Im Demo-Modus vorangehakt. Zum echten Retten muss das Häkchen weg – dann schreibt "
                                "das Werkzeug wirklich in den Flash des angeschlossenen FM-1.")
        c2.layout().addWidget(self.chk_sim)
        self.sim_note = self._label("<small>Im Demo-Modus ist die Simulation vorangehakt. Für ein echtes FM-1 das "
                                    "Häkchen entfernen – erst dann wird geschrieben.</small>" if DEMO else
                                    "<small>Zum Üben ohne Gerät anhaken: dann läuft alles gegen einen simulierten "
                                    "Flash, am FM-1 wird nichts verändert.</small>")
        c2.layout().addWidget(self.sim_note)
        self.b_setup = QPushButton(icon("download"), "Werkzeug holen (jl-uboot-tool) …")
        self.b_setup.clicked.connect(self.uboot_setup)
        self.b_restore = QPushButton(icon("tools-wizard", "system-run"), "Original-V15 zurückschreiben …")
        self.b_restore.setProperty("variant", "primary")
        self.b_restore.clicked.connect(self.uboot_restore)
        self.b_repair = QPushButton(icon("configure"), "Einstellungen nach Rückkehr reparieren …")
        self.b_repair.setToolTip("repair-settings: setzt die Einstellungsbereiche mit einem Werks-Dump zurück – nur "
                                 "bei V15-Aufbau (prüft die App an der frischen Sicherung). Upstream nur mit Mock "
                                 "getestet – erst System > Reset Patches probieren.")
        self.b_repair.clicked.connect(self.uboot_repair)
        self.b_dump = QPushButton(icon("document-save"), "Notfall-Komplettsicherung …")
        self._dump_tip = ("Optional, fortgeschritten: Original-FM-1 per Softkey in den ROM-Bootmodus schicken und den "
                          "ganzen Flash nur lesen. Braucht Administratorrechte. Läuft nie automatisch.")
        self.b_dump.setToolTip(self._dump_tip)
        self.b_dump.clicked.connect(self.uboot_full_backup)
        c2.layout().addLayout(_row(self.b_setup, self.b_restore))
        c2.layout().addLayout(_row(self.b_repair, self.b_dump))
        mf = shutil.which("m-flasher")
        if mf:
            b_mf = QPushButton(icon("system-run"), "M-Flasher öffnen")
            b_mf.setToolTip("M-Flasher ist installiert (alternatives Werkzeug, siehe Details)")
            b_mf.clicked.connect(lambda: subprocess.Popen([mf], start_new_session=True))
            c2.layout().addLayout(_row(b_mf))
        self.details["uboot"] = Disclosure("Details: Werkzeug, Speicherbereiche, M-Flasher, Felucca 0.9",
                                           DETAILS_UBOOT + ("<br><br>M-Flasher: " + ("gefunden – Knopf oben." if mf
                                                                                      else "nicht installiert.")))
        c2.layout().addWidget(self.details["uboot"])
        lay.addWidget(c2)

        # ---- Stufe 3
        c3 = Card("Stufe 3 – FM-1 Transporter (Hardware)")
        c3.layout().addWidget(self._label(TIER3_SHORT + STEPS_TIER3))
        b_guide = QPushButton(icon("help-contents"), "Transporter-Anleitung öffnen")
        b_guide.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(
            str(uboot.VENDOR / "TRANSPORTER-GUIDE.md"))))
        c3.layout().addLayout(_row(b_guide))
        self.details["tier3"] = Disclosure("Details: Verkabelung, Boards, Terminal-Befehle", TIER3)
        c3.layout().addWidget(self.details["tier3"])
        lay.addWidget(c3)

        # ---- Ist mein FM-1 sauber?
        from ..clean_check import CleanCheckCard
        self.clean = CleanCheckCard(ctx, self, window, sim=lambda: self.chk_sim.isChecked(), runner=self._runner)
        lay.addWidget(self.clean)

        self.details["faq"] = Disclosure("Was tun, wenn … (Abbruch, FAILED -5, verstümmelte Presets, Vorbeugen)", FAQ)
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

    # ------------------------------------------------------------ Zustand
    def activate(self):
        self.timer.start(1500)
        self.refresh()

    def deactivate(self):
        self.timer.stop()

    def shutdown(self):
        self.timer.stop()

    def update_port(self):
        """ein angeschlossenes FM-1 im Update-Modus (aktiv oder in der Liste)"""
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
            lines.append(f"<span style='color:#f67400'>● Live erkannt: ein FM-1 wartet im Update-Modus</span> an "
                         f"„{port.label}“" + (f" (Loader {ident})" if ident else "") + ".")
        else:
            lines.append("<span style='color:#888'>○ Nicht erkannt: kein FM-1 im Update-Modus.</span> Die Anzeige "
                         "aktualisiert sich von selbst.")
        if act:
            lines.append(f"Unterbrochene Installation laut Journal: <b>{act.get('target', '?')}</b> "
                         f"({act.get('product', '')}, Phase „{act.get('phase', '')}“).")
        self.status.setText("<br>".join(lines))
        # Knöpfe der Stufe 1: nur mit einem wartenden Gerät – und „Original-V15“ nur, wenn der Loader sie annimmt
        wait_hint = "Erst möglich, wenn ein FM-1 im Update-Modus erkannt ist (siehe Zustand oben)."
        self.b_finish.setEnabled(bool(port))
        self.b_finish.setToolTip(("Schreibt das Paket der unterbrochenen Installation (laut Journal) – nach Prüfung von "
                                  "Prüfsumme und Loader." if act else
                                  "Kein Journal-Eintrag: Du wirst nach dem Paket gefragt, dessen Installation "
                                  "unterbrochen wurde.") if port else wait_hint)
        v15_ok = bool(port) and (not ident or bool(fpk.STOCK_LOADER_RE.match(ident)))
        self.b_v15.setEnabled(v15_ok)
        self.b_v15.setToolTip("Nur möglich, wenn das FM-1 im Original-Update-Modus (ota-FM-1_015) wartet." if v15_ok
                              else (f"Das FM-1 wartet als „{ident}“ – {_loader_kind(ident)}. Er nimmt die "
                                    "Original-V15 nicht an; bitte das unterbrochene Paket abschließen." if port
                                    else wait_hint))
        self.b_file.setEnabled(bool(port))
        self.b_file.setToolTip("Ein anderes .fwsc-Paket wählen – die App prüft, ob es zum wartenden Loader passt."
                               if port else wait_hint)
        devs = uboot.scan() if not DEMO else []
        usb = self.ctx.uboot if hasattr(self.ctx, "uboot") else []
        if devs:
            txt = "<span style='color:#f67400'>● Live erkannt:</span> " + "; ".join(d.describe() for d in devs)
        elif usb:
            txt = ("<span style='color:#f67400'>● Ein FM-1 im ROM-Bootmodus ist am USB</span>, aber kein SCSI-Gerät "
                   "dazu: <tt>sudo modprobe sg</tt> ausführen.")
        else:
            txt = "<span style='color:#888'>○ Nicht erkannt: kein Gerät „WL82 UBOOT1.00“ angeschlossen.</span>"
        miss = uboot.missing_deps()
        if miss:
            txt += f"<br>Für das Werkzeug fehlen Pakete: <tt>sudo apt install {' '.join(miss)}</tt>"
        ready = uboot.tool_ready()
        txt += "<br>Werkzeug jl-uboot-tool: " + ("vorhanden (wird vor jedem Start geprüft)." if ready else
                                                 "noch nicht geholt – zuerst „Werkzeug holen“.")
        self.uboot_status.setText(txt)
        # Knöpfe der Stufe 2: Simulation geht immer (Übung); echt nur mit Werkzeug und genau einem Gerät
        sim = self.chk_sim.isChecked()
        self.b_setup.setToolTip("Holt kagaimiqs jl-uboot-tool einmalig auf den geprüften Stand – nichts wird am FM-1 "
                                "verändert." + ("" if not ready else " Schon vorhanden; erneut holen prüft es nur."))
        for b, what in ((self.b_restore, "Schreibt die Original-V15 (0x4000–0x92FFF) in den Flash des FM-1 im "
                                         "ROM-Bootmodus – nach Pflicht-Doppelsicherung, nur unter dem Bootkopf ab Werk."),
                        (self.b_repair, "Setzt nur die Einstellungsbereiche auf einen Werks-Dump zurück – nur, wenn "
                                        "die frische Sicherung den V15-Aufbau zeigt.")):
            if sim:
                b.setEnabled(True)
                b.setToolTip(what + " Simulation: läuft gegen einen Mock, nichts wird geschrieben.")
            elif not ready:
                b.setEnabled(False)
                b.setToolTip("Zuerst „Werkzeug holen“ – ohne jl-uboot-tool kann die App nicht mit dem Chip sprechen.")
            elif len(devs) != 1:
                b.setEnabled(False)
                b.setToolTip("Erst möglich, wenn genau ein Gerät „WL82 UBOOT1.00“ erkannt ist (siehe Zustand oben)."
                             if not devs else "Mehrere Geräte im ROM-Bootmodus – bitte nur eins anschließen.")
            else:
                b.setEnabled(True)
                b.setToolTip(what)
        dump_ok = sim or (ready and (bool(devs) or self.ctx.port is not None))
        self.b_dump.setEnabled(dump_ok)
        if not dump_ok:
            self.b_dump.setToolTip("Dafür muss jl-uboot-tool geholt sein und ein FM-1 angeschlossen (laufend mit "
                                   "Original-Firmware oder schon im ROM-Bootmodus).")
        elif self.b_dump.toolTip().startswith("Dafür"):
            self.b_dump.setToolTip(self._dump_tip)

    # ------------------------------------------------------------ Stufe 1
    def _package_for(self, ident: str, stock: bool, choose: bool) -> Path | None:
        if stock:
            sp = firmware.spec("stock-v15")
            return None if sp is None else sp
        act = self.journal.active
        if act and not choose and act.get("package") and Path(act["package"]).exists():
            return Path(act["package"])
        path, _ = QFileDialog.getOpenFileName(self, "Paket der unterbrochenen Installation",
                                              str(self.ctx.settings.firmware_dir), "FM-1-Firmware (*.fwsc)")
        return Path(path) if path else None

    def finish_install(self, stock: bool = False, choose: bool = False) -> bool:
        port = self.update_port()
        if port is None:
            show_error(self, "Kein Gerät im Update-Modus", "Bitte das FM-1 wie beschrieben in den Update-Modus bringen "
                                                          "und anschließen.")
            return False
        src = self._package_for("", stock, choose)
        if src is None:
            return False
        act = self.journal.active or {}
        ident_now = self.ctx.identity if self.ctx.port is port else ""
        expect_sha = ""
        if stock:
            what = "die unveränderte <b>Original-Firmware V15</b> (Prüfsumme db1642b2…)"
            if ident_now and not fpk.STOCK_LOADER_RE.match(ident_now):
                show_error(self, "Passt nicht zum wartenden Loader",
                           f"Das FM-1 wartet als „{ident_now}“ – das ist {_loader_kind(ident_now)}. Er nimmt die "
                           "Original-V15 nicht an. Es wurde nichts gesendet. Bitte „Installation abschließen“ mit dem "
                           "Paket der unterbrochenen Installation benutzen.")
                return False
        else:
            # das Paket schon hier prüfen – der Nutzer sieht vor dem Klick, was gesendet würde
            try:
                pre = fpk.analyze(Path(src))
            except fpk.PackageError as e:
                show_error(self, "Kein FM-1-Paket", f"{e} Es wurde nichts gesendet.")
                return False
            fam = {"stock": "Original (M-VAVE)", "felucca": "Felucca-Familie"}.get(pre.family, "unbekannt")
            facts = (f"<br><small>Kennung {pre.product} · Loader-Familie {fam} · SHA-256 {pre.sha256[:16]}…"
                     + (f" · <b>beschädigt: {pre.damage}</b>" if pre.damage else "") + "</small>")
            if pre.damage:
                show_error(self, "Paket beschädigt", f"{Path(src).name} ist beschädigt ({pre.damage}) und wird nicht "
                           "gesendet. Bitte das Paket neu herunterladen (Firmware+ › „Installieren & Versionen“ › „Nur herunterladen“).")
                return False
            if pre.loader_rule is None:
                show_error(self, "Unbekannter Update-Loader", f"Die App kennt den Update-Loader von {Path(src).name} "
                           "nicht und schickt das Paket deshalb an keinen Loader. Es wurde nichts gesendet.")
                return False
            if ident_now and not pre.loader_rule(ident_now):
                show_error(self, "Passt nicht zum wartenden Loader",
                           f"Das FM-1 wartet als „{ident_now}“, {Path(src).name} gehört aber zu {pre.loader_rule.label}. "
                           "Es wurde nichts gesendet. Bitte das Paket wählen, dessen Installation unterbrochen wurde.")
                return False
            if act.get("package") and Path(act["package"]) == Path(src):
                expect_sha = act.get("sha256") or ""
                what = (f"<b>{act.get('target') or act.get('product') or 'die unterbrochene Firmware'}</b> "
                        f"(das Paket der unterbrochenen Installation){facts}")
            else:
                what = f"das Paket <b>{Path(src).name}</b>{facts}"
                if act.get("sha256") and act["sha256"] != pre.sha256:
                    what += (f"<br><b>Achtung:</b> Unterbrochen wurde „{act.get('target', '?')}“ – das ist ein anderes "
                             "Paket. Am sichersten ist es, genau das unterbrochene Paket noch einmal zu schreiben.")
        from ..risk_dialog import ensure_risk_ack
        if not ensure_risk_ack(self):
            return False
        if not confirm(self, "Installation abschließen",
                       f"Das wartende FM-1 bekommt {what}.<br><br>"
                       "Die App prüft vorher noch einmal Prüfsumme und Loader – passt etwas nicht, wird nichts gesendet."
                       "<br>Während des Schreibens das Kabel nicht abziehen und das FM-1 nicht ausschalten.", "Abschließen"):
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
                raise RuntimeError("Die V15-Datei ist nicht die unveränderte Original-Firmware – nichts gesendet.")
            w.log.emit(f"Paket: {info.product} ({info.family}), SHA-256 {info.sha256[:16]}…")
            if DEMO:
                from ...flash import demo
                demo.register(Path(path).read_bytes())
            eng = fe.Engine(log=w.log.emit, progress=lambda a, b: w.progress.emit(a, b or 100), cancel=cancel)
            ident = eng.probe(port.in_name)
            w.log.emit(f"Gerät meldet: {ident.text if ident else 'nichts'}")
            pin = fe.Pin(usb_path=port.usb_path) if port.usb_path else None
            r = eng.write(info, port=port.in_name, label=f"Rettung: {act.get('target') or info.product}",
                          device_id=device_id, pin=pin, expect_sha=expect_sha)
            w.log.emit(f"Fertig – das FM-1 meldet {r.identity}.")
            return r.identity

        def run(w):
            try:
                return work(w)
            except (RuntimeError, fe.FlashError):
                raise                                   # schon deutsche Sätze
            except Exception as e:  # noqa: BLE001
                w.log.emit("Technische Details: " + firmware.explain(e))
                raise RuntimeError(firmware.friendly_error(e)) from e
        from ..feedback import run as fb_run
        try:
            d = fb_run(self, "Installation abschließen", run,
                       done=lambda ident: f"Installation abgeschlossen – das FM-1 meldet jetzt {ident} und startet "
                                          "normal.")
        finally:
            self.ctx.reconnect_soon(1500)
        self.refresh()
        return d.ok

    # ------------------------------------------------------------ Stufe 2
    def _runner(self, log):
        return uboot.Runner(dry_run=self.chk_sim.isChecked(), log=log)

    def uboot_setup(self):
        if not confirm(self, "Werkzeug holen",
                       "Die App lädt kagaimiqs <b>jl-uboot-tool</b> (MIT) von GitHub auf den geprüften Stand adb3f18 und "
                       "prüft die Prüfsumme des WL82-Loaders (d41da612…). Es wird nichts installiert, nur in den "
                       "App-Datenordner kopiert. Fortfahren?", "Herunterladen"):
            return False
        from ..feedback import run as fb_run
        d = fb_run(self, "jl-uboot-tool holen", lambda w: _check(self._runner(w.log.emit).setup()),
                   done="jl-uboot-tool geholt und geprüft (Stand adb3f18). Am FM-1 wurde nichts verändert.")
        return d.ok

    def uboot_restore(self) -> bool:
        sim = self.chk_sim.isChecked()
        devs = [] if sim else uboot.scan()
        if not sim:
            if len(devs) != 1:
                show_error(self, "Kein eindeutiges Gerät",
                           "Es muss genau ein Gerät „WL82 UBOOT1.00“ (USB 4c4a:8057) angeschlossen sein. Die App wählt "
                           "nie selbst einen Datenträger aus." + (" Gefunden: " + ", ".join(x.sg for x in devs)
                                                                    if devs else ""))
                return False
            if not devs[0].size_ok:
                show_error(self, "Größe passt nicht", f"{devs[0].describe()} – das ist kein FM-1.")
                return False
        dev = devs[0] if devs else None
        from ..risk_dialog import ensure_risk_ack
        if not ensure_risk_ack(self):
            return False
        if not confirm(self, "Rettung Stufe 2 – Schritt 1 von 3",
                       BASICS + "<br>Das FM-1 muss <b>voll geladen</b> sein und direkt am Rechner hängen.<br>"
                       f"Gerät: <b>{dev.describe() if dev else 'Simulation'}</b><br><br>"
                       "Ablauf: ganzen Flash zweimal lesen und vergleichen (Pflicht, enthält auch deine Projekte), "
                       "an dieser Sicherung den Bootkopf prüfen (nur unter dem Kopf ab Werk geht es weiter – bei "
                       "V16- oder unbekanntem Kopf wird nichts geschrieben), Firmwarebereich 0x4000–0x92FFF aus der "
                       "Original-V15 schreiben, zurücklesen.", "Weiter",
                       danger=True):
            return False
        text, ok = QInputDialog.getText(self, "Rettung Stufe 2 – Schritt 2 von 3",
                                        "Zum Bestätigen SCHREIBEN eintippen (schreibt in den Flash des FM-1):")
        if not ok or text.strip().upper() not in ("SCHREIBEN", "WRITE"):
            return False
        if not sim and not confirm(self, "Rettung Stufe 2 – Schritt 3 von 3",
                                   "Jetzt fragt das System nach deinem Passwort (pkexec): Das Werkzeug braucht "
                                   "Administratorrechte für den direkten USB-Zugriff. Danach nichts abziehen, bis "
                                   "„Done“ erscheint.", "Starten", danger=True):
            return False

        def work(w):
            st = firmware.spec("stock-v15")
            v15 = firmware.download(st, firmware.fetch_releases(st)[0], log=w.log.emit).fwsc
            r = self._runner(w.log.emit)
            return _gate_check(r, r.restore(str(v15), device=dev.sg if dev else ""))
        from ..feedback import run as fb_run
        d = fb_run(self, "Original-V15 im ROM-Bootmodus schreiben", work,
                   done=("Simulation beendet – nichts wurde geschrieben." if sim else
                         "Original-V15 geschrieben (0x4000–0x92FFF) und zurückgelesen. Die Doppelsicherung des "
                         "ganzen Flashs liegt im Rettungsordner. Jetzt das FM-1 aus- und wieder einschalten."),
                   actions=[("Rettungsordner öffnen", lambda _r: QDesktopServices.openUrl(
                       QUrl.fromLocalFile(str(uboot.work_dir()))))])
        return d.ok

    def _one_uboot(self, title: str):
        """genau EIN Gerät im ROM-Bootmodus (USB 4c4a:8057, WL82 UBOOT1.00) – sonst None (mit Meldung).
        In der Simulation: (None, True)."""
        if self.chk_sim.isChecked():
            return None, True
        devs = uboot.scan()
        if len(devs) != 1:
            show_error(self, title, "Es muss genau ein Gerät „WL82 UBOOT1.00“ (USB 4c4a:8057) angeschlossen sein. Die "
                       "App wählt nie selbst einen Datenträger aus." + (" Gefunden: " + ", ".join(x.sg for x in devs)
                                                                        if devs else " Gefunden: keins."))
            return None, False
        if not devs[0].size_ok:
            show_error(self, title, f"{devs[0].describe()} – das ist kein FM-1.")
            return None, False
        return devs[0], True

    def uboot_repair(self) -> bool:
        dev, ok = self._one_uboot("Einstellungen reparieren")
        if not ok:
            return False
        path, _ = QFileDialog.getOpenFileName(self, "Werks-Dump (1 MiB) eines frischen Original-FM-1",
                                              str(uboot.work_dir()), "Flash-Dump (*.bin)")
        if not path:
            return False
        from ..risk_dialog import ensure_risk_ack
        if not ensure_risk_ack(self):
            return False
        if not confirm(self, "Einstellungen reparieren",
                       "<b>Upstream nur mit Mock getestet.</b> Zuerst am FM-1 <i>System &gt; Reset Patches</i> "
                       "probieren.<br>Das Werkzeug sichert den Flash zweimal und setzt dann nur die "
                       "Einstellungsbereiche (0x93000–0xE8FFF, 0xFC000–0xFEFFF) auf den Werks-Dump zurück. Songs und "
                       "Einstellungen der vorigen Firmware gehen dabei verloren (bleiben im Backup), bei Melodee auch "
                       "Werkssounds und Schrift (0xCB000).<br>Diese Bereiche sind nur beim <b>V15-Aufbau</b> reine "
                       "Daten. Darum prüft die App an der Sicherung aus demselben Lauf, wo die Firmware endet – bei "
                       "V16 (dort liegt in 0x93000–0xA8FFF noch Firmware), einem unbekannten oder beschädigten Aufbau "
                       "wird nichts geschrieben.", "Reparieren",
                       danger=True):
            return False
        text, ok = QInputDialog.getText(self, "Bestätigen", "Zum Bestätigen SCHREIBEN eintippen:")
        if not ok or text.strip().upper() not in ("SCHREIBEN", "WRITE"):
            return False
        def work(w):
            r = self._runner(w.log.emit)
            _gate_check(r, r.repair_settings(path, device=dev.sg if dev else ""))
            return r.last_verdict().get("verdict") or {}
        from ..feedback import run as fb_run
        d = fb_run(self, "Einstellungen reparieren", work,
                   done=lambda v: repair_done_text(v, sim=dev is None))
        return d.ok

    def uboot_full_backup(self) -> bool:
        from ..risk_dialog import ensure_risk_ack
        if not ensure_risk_ack(self):
            return False
        if not confirm(self, "Notfall-Komplettsicherung (optional)",
                       "Für Fortgeschrittene: Ein FM-1 mit <b>Original-Firmware</b> wird per Softkey in den "
                       "ROM-Bootmodus geschickt, dann wird der ganze Flash zweimal nur gelesen und verglichen. Braucht "
                       "Administratorrechte. Danach das FM-1 aus- und einschalten.", "Sichern"):
            return False
        folder = str(uboot.work_dir() / "Komplettsicherungen")
        sim = self.chk_sim.isChecked()
        if not sim and getattr(self.ctx, "is_virtual", False):
            show_error(self, "Kein echtes FM-1", "Der Studio-Zwilling ist kein echtes FM-1 – eine Komplettsicherung "
                       "geht nur von einem angeschlossenen Gerät. Bitte oben ein echtes FM-1 wählen.")
            return False
        port = self.ctx.port
        softkey = not sim and self.ctx.protocol != "update"
        if not sim:
            before = uboot.scan()
            if softkey and before:
                show_error(self, "Schon ein Gerät im ROM-Bootmodus",
                           "Es ist bereits ein Gerät „WL82 UBOOT1.00“ angeschlossen. Damit die Sicherung sicher vom "
                           "richtigen FM-1 kommt: erst dieses Gerät abziehen, oder – wenn es das gewünschte ist – "
                           "es ohne Softkey sichern (FM-1 vorher trennen, dann erneut versuchen).")
                return False
            if not softkey and len(before) != 1:
                dev, ok = self._one_uboot("Notfall-Komplettsicherung")
                if not ok:
                    return False
        if softkey and port is None:
            show_error(self, "Kein Gerät", "Bitte das FM-1 mit Original-Firmware anschließen.")
            return False

        def work(w):
            r = self._runner(w.log.emit)
            dev = ""
            if softkey:
                _check(r.softkey(port.in_name), r)
                w.log.emit("Warte auf das FM-1 im ROM-Bootmodus (USB 4c4a:8057) …")
                for _ in range(40):
                    found = [d for d in uboot.scan() if not port.usb_path or d.usb_path == port.usb_path]
                    if len(found) == 1:
                        dev = found[0].sg
                        break
                    if len(found) > 1:
                        raise RuntimeError("Mehrere Geräte im ROM-Bootmodus – bitte nur das FM-1 angeschlossen lassen, "
                                           "das du sichern willst.")
                    threading.Event().wait(0.5)
                if not dev:
                    raise RuntimeError("Das FM-1 ist nicht im ROM-Bootmodus erschienen (USB 4c4a:8057). Aus- und wieder "
                                       "einschalten und erneut versuchen.")
            elif not sim:
                devs = uboot.scan()
                if len(devs) != 1:
                    raise RuntimeError("Es muss genau ein Gerät „WL82 UBOOT1.00“ angeschlossen sein.")
                dev = devs[0].sg
            return _check(r.backup(folder, device=dev), r)
        from ..feedback import run as fb_run
        d = fb_run(self, "Notfall-Komplettsicherung", work,
                   done=f"Komplettsicherung gelesen (zweimal, verglichen) und gespeichert in {folder}. Am FM-1 wurde "
                        "nichts geschrieben – jetzt aus- und wieder einschalten.",
                   actions=[("Ordner öffnen", lambda _r: QDesktopServices.openUrl(QUrl.fromLocalFile(folder)))])
        return d.ok


def _loader_kind(ident: str) -> str:
    """welcher Update-Loader wartet – in Worten (nur Anzeige)"""
    if fpk.OPEN_LOADER_RE.match(ident or ""):
        return "der Update-Loader der M-VAVE FM1 Open (nimmt nur das Paket genau seines Builds an)"
    if (ident or "").startswith("ota-FM-1_9"):
        return "ein Felucca-Loader (Melodee, SLOOP, Skiff …)"
    return "ein Update-Loader, den die App nicht kennt"


def repair_done_text(verdict: dict, sim: bool = False) -> str:
    """Abschluss von „Einstellungen reparieren“. Nur nach rc 0 – dann hat rescuegate an der Sicherung desselben Laufs
    den V15-Aufbau festgestellt, und die Tore des Unbrickers lassen nur 0x93000–0xE8FFF und 0xFC000–0xFEFFF zu."""
    if sim:
        return "Simulation beendet – am FM-1 wurde nichts geschrieben."
    fw = verdict.get("firmware") or "die Firmware im Flash"
    basis = verdict.get("basis") or "V15-Aufbau"
    return ("Die Einstellungsbereiche 0x93000–0xE8FFF und 0xFC000–0xFEFFF sind jetzt im Werkszustand (wo nötig neu "
            "geschrieben und zurückgelesen). Vorher hat die App an der Sicherung aus diesem Lauf geprüft, dass "
            f"{fw} den V15-Aufbau hat und vor 0x93000 endet ({basis}); den Bootkopf und den Firmware-Bereich "
            "0x0000–0x92FFF hat das Werkzeug darum nicht beschrieben. Songs, Sounds und Einstellungen aus diesen "
            "Bereichen – bei Melodee auch Werkssounds und Schrift – liegen nur noch in der Sicherung im "
            "Rettungsordner. Jetzt das FM-1 aus- und wieder einschalten.")


def _gate_check(runner, rc: int):
    """wie _check, aber mit dem Grund der Sperre (rescuegate.py), wenn sie abgelehnt hat"""
    from ...flash import rescuegate
    res = runner.last_verdict() if hasattr(runner, "last_verdict") else {}
    if rc == rescuegate.EXIT_REFUSED or (rc != 0 and res.get("refused")):
        raise RuntimeError("Abgelehnt – es wurde nichts geschrieben. " + (res.get("refused") or
                           "Die Prüfung der Sicherung hat nicht bestanden (Details im Protokoll).")
                           + " Die Sicherung aus diesem Lauf liegt im Rettungsordner. Das FM-1 verlässt den "
                           "ROM-Bootmodus meist nach einem Werkzeuglauf – für einen weiteren Versuch aus- und "
                           "einschalten und neu in den ROM-Bootmodus bringen.")
    if rc != 0 and getattr(runner, "hint", ""):
        raise RuntimeError(runner.hint)
    if rc == 0 and not res.get("checked"):
        raise RuntimeError("Das Werkzeug hat ohne die Prüfung der Sicherung geendet – die App kann nicht sagen, was "
                           "geschehen ist. Bitte das Protokoll ansehen.")
    return _check(rc)


def _check(rc: int, runner=None):
    if rc != 0 and getattr(runner, "hint", ""):      # z. B. UCUN-Flash (0xB36014): Rettung kennt ihn noch nicht
        raise RuntimeError(runner.hint)
    if rc != 0:
        raise RuntimeError(f"Das Werkzeug meldet einen Fehler (Code {rc}) – Details im Protokoll. "
                           + ("Code 3: Firmwarebereich evtl. unvollständig – NICHT ausschalten, erneut versuchen."
                              if rc == 3 else "Es wurde nichts geschrieben, falls das Protokoll nichts anderes sagt."))
    return rc


def _row(*ws):
    h = QHBoxLayout()
    for w in ws:
        h.addWidget(w)
    h.addStretch(1)
    return h


def _make(ctx, window):
    return RecoveryPage(ctx, window)


PAGE = PageSpec(key="recovery", title="Rettung", group="daten", factory=_make,
                icons=("tools-wizard", "help-hint", "dialog-warning"), order=14,
                description="FM-1 startet nicht oder hängt im Update-Modus – Schritt für Schritt retten",
                keywords=("recovery", "brick", "update-modus", "uboot", "wl82", "transporter", "oct", "sauber", "dump",
                          "flash prüfen", "v16"))
