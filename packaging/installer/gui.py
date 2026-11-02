#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Grafischer Installer der FM-1 Companion Suite (PyQt6-QWizard). Wird vom .run-Paket gestartet.

  gui.py --source DIR                 interaktiv
  gui.py --source DIR --unattended [--prefix P] [--system] [--desktop] [--demo-data] [--no-menu]
                                    [--no-fluidsynth-fix]
"""
from __future__ import annotations

import html
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import core  # noqa: E402


def unattended(args) -> int:
    prefix = Path(args.prefix).expanduser() if args.prefix else (core.SYSTEM_PREFIX if args.system else core.user_prefix())
    print(RISK_TITLE + "\n\n" + "\n\n".join(risk_lines(Path(args.source))) + "\n")
    core.install(Path(args.source), prefix, args.system, not args.no_menu, args.desktop, args.demo_data, args.python,
                 fluidsynth_fix=not args.no_fluidsynth_fix)
    return 0


RISK_TITLE = "Wichtiger Hinweis"
# Fallback, falls fm1companion/risk.py im Quellordner fehlt – derselbe Text wie in der App
RISK_FALLBACK = [
    "Die FM-1 Companion Suite ist ein inoffizielles Community-Projekt und nicht mit M-VAVE verbunden.",
    "Firmware installieren, Rettung (Update-Modus, ROM-Bootmodus), Reste aufräumen und das Zurückschreiben aus dem "
    "Studio verändern dein Gerät. Das geschieht auf eigenes Risiko.",
    "Mach vorher ein Backup und trenne das Gerät während eines Vorgangs nicht.",
    "Nicht jede Funktion ist auf echter Hardware getestet (siehe docs/UNGETESTET.md).",
    "Es gibt keine Gewährleistung und keine Haftung, soweit gesetzlich zulässig (GNU GPL 3.0, Abschnitte 15 und 16).",
]


def risk_lines(src: Path) -> list[str]:
    """Hinweistext aus fm1companion/risk.py des Quellordners (ohne die App zu importieren)."""
    import ast
    try:
        tree = ast.parse((Path(src) / "fm1companion" / "risk.py").read_text("utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "TEXT_LINES" for t in node.targets):
                lines = ast.literal_eval(node.value)
                if isinstance(lines, list) and all(isinstance(x, str) for x in lines):
                    return lines
    except (OSError, SyntaxError, ValueError):
        pass
    return list(RISK_FALLBACK)


def interactive(args) -> int:
    from PyQt6.QtCore import QProcess, Qt
    from PyQt6.QtGui import QFont, QIcon
    from PyQt6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QLabel, QPlainTextEdit, QProgressBar,
                                 QRadioButton, QTextBrowser, QVBoxLayout, QWizard, QWizardPage)
    app = QApplication(sys.argv)
    app.setApplicationName("FM-1 Companion Suite – Installation")
    src = Path(args.source).resolve()
    version = core.read_version(src)
    sys.path.insert(0, str(src))
    try:                                        # gleiches Aussehen wie die App
        from fm1companion.ui import theme
        theme.apply(app, "auto")
    except Exception:  # noqa: BLE001
        pass
    png = src / "data" / "icons" / "hicolor" / "128x128" / "apps" / "fm1-companion.png"
    app.setWindowIcon(QIcon(str(png)))
    wz = QWizard()
    wz.setWindowTitle(f"FM-1 Companion Suite {version} installieren")
    wz.setWizardStyle(QWizard.WizardStyle.ModernStyle)
    wz.setPixmap(QWizard.WizardPixmap.LogoPixmap, QIcon(str(png)).pixmap(56, 56))
    wz.resize(700, 520)
    for b, t in ((QWizard.WizardButton.NextButton, "Weiter >"), (QWizard.WizardButton.BackButton, "< Zurück"),
                 (QWizard.WizardButton.FinishButton, "Fertig"), (QWizard.WizardButton.CancelButton, "Abbrechen"),
                 (QWizard.WizardButton.CommitButton, "Installieren")):
        wz.setButtonText(b, t)

    def label(text):
        lab = QLabel(text)
        lab.setWordWrap(True)
        lab.setTextFormat(Qt.TextFormat.RichText)
        return lab

    # Willkommen
    p1 = QWizardPage()
    p1.setTitle(f"Willkommen zur FM-1 Companion Suite {version}")
    p1.setSubTitle("Alles fürs M-VAVE FM-1 in einer App.")
    v = QVBoxLayout(p1)
    old_user = core.installed_info(core.user_prefix(), False)
    old_sys = core.installed_info(core.SYSTEM_PREFIX, True)
    old = old_user or old_sys
    v.addWidget(label("Firmware wechseln, Backups, Klänge und Presets, Samples, MIDI, Simulator, Aufnahme und Hilfe – "
                      "dieser Assistent richtet alles ein.<br><br>" +
                      (f"<b>Gefunden: Version {old['version']} ({old['mode']}).</b> Sie wird aktualisiert; deine "
                       "Einstellungen und dein Ordner ~/FM-1 bleiben erhalten." if old else "")))
    v.addStretch(1)
    wz.addPage(p1)

    # Lizenz
    p2 = QWizardPage()
    p2.setTitle("Lizenz")
    p2.setSubTitle("Die Suite ist freie Software unter der GNU GPL 3.0.")
    v = QVBoxLayout(p2)
    lic = QTextBrowser()
    lic.setPlainText((src / "LICENSE").read_text("utf-8", errors="replace") if (src / "LICENSE").exists() else "GPL-3.0")
    v.addWidget(lic, 1)
    accept = QCheckBox("Ich akzeptiere die Lizenzbedingungen")
    v.addWidget(accept)
    p2.registerField("accept*", accept)
    wz.addPage(p2)

    # Wichtiger Hinweis (derselbe Text wie vor der ersten Installation/Rettung in der App)
    p2b = QWizardPage()
    p2b.setTitle(RISK_TITLE)
    p2b.setSubTitle("Inoffiziell, auf eigenes Risiko, ohne Gewährleistung.")
    v = QVBoxLayout(p2b)
    v.addWidget(label("".join(f"<p>{html.escape(t)}</p>" for t in risk_lines(src))))
    understood = QCheckBox("Ich habe verstanden")
    v.addWidget(understood)
    p2b.registerField("understood*", understood)
    v.addStretch(1)
    wz.addPage(p2b)

    # Installationsart
    p3 = QWizardPage()
    p3.setTitle("Installationsart")
    v = QVBoxLayout(p3)
    grp = QButtonGroup(p3)
    r_user = QRadioButton(f"Nur für mich  ({core.layout(core.user_prefix(), False)['app']}, kein Passwort nötig)")
    r_sys = QRadioButton("Für alle Benutzer  (/usr/local, fragt nach dem Administrator-Passwort)")
    for r in (r_user, r_sys):
        grp.addButton(r)
        v.addWidget(r)
    (r_sys if (old_sys and not old_user) else r_user).setChecked(True)
    if not shutil.which("pkexec") and os.geteuid() != 0:
        r_sys.setEnabled(False)
        r_sys.setText(r_sys.text() + " – pkexec fehlt")
    v.addStretch(1)
    wz.addPage(p3)

    # Komponenten
    p4 = QWizardPage()
    p4.setTitle("Komponenten")
    v = QVBoxLayout(p4)
    c_menu = QCheckBox("Eintrag im Startmenü (Multimedia) und „FM-1 Companion deinstallieren“")
    c_menu.setChecked(True)
    c_desk = QCheckBox("Verknüpfung auf dem Schreibtisch")
    c_demo = QCheckBox("Beispiel-Samples (in ~/FM-1/Samples/Beispiele (Demo))")
    c_fluid = QCheckBox("FluidSynth-Systemdienst abschalten (fluidsynth.service belegt bei jeder Anmeldung die "
                        "HDMI-Soundkarte; FluidSynth bleibt installiert, die App startet es selbst)")
    c_fluid.setChecked(True)
    c_fluid.setVisible(core.fluidsynth_service_enabled())   # nur fragen, wenn der Dienst wirklich aktiv ist
    for c in (c_menu, c_desk, c_demo, c_fluid):
        v.addWidget(c)
    v.addStretch(1)
    wz.addPage(p4)

    # Fortschritt
    p5 = QWizardPage()
    p5.setTitle("Installation")
    p5.setCommitPage(True)
    v = QVBoxLayout(p5)
    bar = QProgressBar()
    bar.setRange(0, 7)
    out = QPlainTextEdit()
    out.setReadOnly(True)
    out.setFont(QFont("monospace"))
    v.addWidget(bar)
    v.addWidget(out, 1)
    state = {"done": False, "ok": False}
    p5.isComplete = lambda: state["done"]
    wz.addPage(p5)

    # Fertig
    p6 = QWizardPage()
    p6.setTitle("Fertig")
    v = QVBoxLayout(p6)
    done_lab = label("")
    v.addWidget(done_lab)
    c_start = QCheckBox("FM-1 Companion jetzt starten")
    c_start.setChecked(True)
    v.addWidget(c_start)
    v.addStretch(1)
    wz.addPage(p6)

    proc = QProcess(wz)

    def run_install():
        out.clear()
        system = r_sys.isChecked()
        cmd = [args.python, str(HERE / "core.py"), "install", "--source", str(src)]
        if system:
            cmd += ["--system"]
        if not c_menu.isChecked():
            cmd += ["--no-menu"]
        if c_desk.isChecked():
            cmd += ["--desktop"]
        if c_demo.isChecked():
            cmd += ["--demo-data"]
        if c_fluid.isHidden() or not c_fluid.isChecked() or system:
            cmd += ["--no-fluidsynth-fix"]
        if system and os.geteuid() != 0:
            out.appendPlainText("Bitte das Administrator-Passwort eingeben …")
            cmd = ["pkexec"] + cmd
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.readyReadStandardOutput.connect(lambda: [out.appendPlainText(x) for x in
                                                      bytes(proc.readAllStandardOutput()).decode(errors="replace").splitlines()]
                                             or bar.setValue(min(7, bar.value() + 1)))

        def finished(code, _st):
            state["done"], state["ok"] = True, code == 0
            bar.setValue(7 if code == 0 else 0)
            if code != 0:
                out.appendPlainText(f"\nFEHLER: Installation abgebrochen (Code {code}).")
            p5.completeChanged.emit()
        proc.finished.connect(finished)
        proc.start(cmd[0], cmd[1:])

    def page_changed(i):
        if wz.page(i) is p5 and not state["done"]:
            run_install()
        if wz.page(i) is p6:
            done_lab.setText("<b>Die FM-1 Companion Suite ist installiert.</b><br>Du findest sie im Startmenü unter "
                             "Multimedia. Ohne Gerät ausprobieren: <code>fm1-companion --demo</code>"
                             if state["ok"] else "Die Installation hat nicht geklappt – Details auf der vorigen Seite.")
            c_start.setVisible(state["ok"])
    wz.currentIdChanged.connect(page_changed)
    if wz.exec() and state["ok"] and c_start.isChecked():
        info = core.installed_info(core.SYSTEM_PREFIX if r_sys.isChecked() else core.user_prefix(), r_sys.isChecked())
        exe = Path(info["files"][0]) if info and info.get("files") else None
        if exe and exe.exists():
            subprocess.Popen([str(exe)], start_new_session=True)
    return 0 if state["ok"] else 1


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--unattended", action="store_true")
    ap.add_argument("--prefix", default="")
    ap.add_argument("--system", action="store_true")
    ap.add_argument("--desktop", action="store_true")
    ap.add_argument("--demo-data", action="store_true")
    ap.add_argument("--no-menu", action="store_true")
    ap.add_argument("--no-fluidsynth-fix", action="store_true")
    ap.add_argument("--python", default="/usr/bin/python3" if Path("/usr/bin/python3").exists() else sys.executable)
    a, _ = ap.parse_known_args(argv)
    return unattended(a) if a.unattended else interactive(a)


if __name__ == "__main__":
    sys.exit(main())
